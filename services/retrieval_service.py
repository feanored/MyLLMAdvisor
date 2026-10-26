"""Service: busca vetorial no Chroma e montagem dos trechos (rows) que viram
CONTEXTO — por frase inteira, por palavras-chave, ou pelas duas juntas (ver
KeywordService e KEYWORD_MIN_PER_TERM em models/config.py) — mais os
utilitários de metadados/figuras usados pelo chat e pelo `/rag-view`.
"""
import re
from pathlib import Path

from factories import ChromaClientFactory, ChromaCollectionFactory
from models import (
    FILTER_FIELDS_METADATA_KEY,
    KEYWORD_MAX_DISTANCE,
    KEYWORD_MIN_PER_TERM,
    KEYWORD_PHRASE_LABEL,
    MAX_DISTANCE,
    METADATA_FIELDS_EXCLUDED,
    NRAG_RESULTS,
    compute_filter_fields,
    decode_filter_fields,
    iter_collection_metadatas,
)

from .keyword_service import KeywordService


def _resolve_page_value(metadata, document):
    page = metadata.get("page")
    if page is not None:
        return page

    if "start_line" in metadata and "end_line" in metadata:
        start_line = metadata.get("start_line")
        end_line = metadata.get("end_line")
        if start_line is not None and end_line is not None:
            return f"{start_line}-{end_line}"

    if "line_start" in metadata and "line_end" in metadata:
        line_start = metadata.get("line_start")
        line_end = metadata.get("line_end")
        if line_start is not None and line_end is not None:
            return f"{line_start}-{line_end}"

    if "chunk_index" in metadata:
        return metadata.get("chunk_index")

    return ""


def _search_collection(collection_name, question, n_results=NRAG_RESULTS, where=None):
    if not question:
        return None, "Informe uma pergunta."

    try:
        col = ChromaCollectionFactory.open(collection_name)
    except Exception as exc:
        return None, f"Erro ao abrir a collection: {exc}"

    if col.count() < 1:
        return None, "Base de dados vazia. Envie PDFs em /upload (ou indexe com rag_papers.py index)."

    try:
        results = col.query(
            query_texts=[question],
            n_results=max(1, min(50, int(n_results))),
            where=where,
            include=["documents", "metadatas", "distances"],
        )
    except Exception as exc:
        return None, f"Erro ao filtrar por metadado: {exc}"

    return results, None


def _search_by_keywords(collection_name, keywords, n_results=NRAG_RESULTS, where=None, limit=None, phrase=None):
    """Consulta o RAG uma vez por palavra-chave e, se `phrase` vier (a
    pergunta inteira, sob KEYWORD_PHRASE_LABEL), mais uma vez por ela — numa
    única chamada ao Chroma (várias query_texts de uma vez). Funde os
    resultados: cada chunk entra uma vez, com a MENOR distância obtida; cada
    termo tem KEYWORD_MIN_PER_TERM vaga(s) garantida(s) (ver models/config.py)
    antes do ranking global preencher o resto. Devolve (results,
    keywords_by_id, erro), `results` no mesmo formato de col.query()."""
    if not keywords and not phrase:
        return None, {}, "Nenhuma palavra-chave encontrada na pergunta."

    try:
        col = ChromaCollectionFactory.open(collection_name)
    except Exception as exc:
        return None, {}, f"Erro ao abrir a collection: {exc}"

    if col.count() < 1:
        return None, {}, "Base de dados vazia. Envie PDFs em /upload (ou indexe com rag_papers.py index)."

    terms = list(keywords)
    labels = list(keywords)  # rótulo de cada termo em `terms`, na mesma ordem
    if phrase:
        terms.append(phrase)
        labels.append(KEYWORD_PHRASE_LABEL)

    try:
        results = col.query(
            query_texts=terms,
            n_results=max(1, min(50, int(n_results))),
            where=where,
            include=["documents", "metadatas", "distances"],
        )
    except Exception as exc:
        return None, {}, f"Erro ao consultar por palavras-chave: {exc}"

    ids_all = results.get("ids") or []
    documents_all = results.get("documents") or []
    metadatas_all = results.get("metadatas") or []
    distances_all = results.get("distances") or []

    best = {}        # chave do chunk -> melhor ocorrência dele entre os termos
    per_term = {}    # rótulo do termo -> chaves dos chunks, na ordem em que vieram
    for term_idx, label in enumerate(labels):
        ids = ids_all[term_idx] if term_idx < len(ids_all) else []
        documents = documents_all[term_idx] if term_idx < len(documents_all) else []
        metadatas = metadatas_all[term_idx] if term_idx < len(metadatas_all) else []
        distances = distances_all[term_idx] if term_idx < len(distances_all) else []

        for idx, document in enumerate(documents):
            key = ids[idx] if idx < len(ids) else f"doc:{hash(document)}"
            distance = distances[idx] if idx < len(distances) else None
            per_term.setdefault(label, []).append(key)
            entry = best.get(key)
            if entry is None:
                best[key] = {
                    "key": key,
                    "document": document,
                    "metadata": metadatas[idx] if idx < len(metadatas) else {},
                    "distance": distance,
                    "keywords": [label],
                }
                continue
            if label not in entry["keywords"]:
                entry["keywords"].append(label)
            if distance is not None and (entry["distance"] is None or distance < entry["distance"]):
                entry["distance"] = distance

    by_distance = lambda entry: (entry["distance"] is None, entry["distance"] or 0.0)
    ordered = sorted(best.values(), key=by_distance)

    # Vaga garantida: os KEYWORD_MIN_PER_TERM melhores trechos de cada termo —
    # cada palavra-chave e a frase inteira, no mesmo pé de igualdade —, rodada
    # a rodada (o 1º de cada termo, depois o 2º de cada, ...), pra que nenhum
    # termo da pergunta fique de fora do contexto. O resto das vagas vai para
    # os menores do ranking global.
    selected, taken = [], set()
    for rank in range(max(0, KEYWORD_MIN_PER_TERM)):
        reserved = []
        for label in labels:
            keys = per_term.get(label) or []
            if rank < len(keys) and keys[rank] not in taken:
                taken.add(keys[rank])
                reserved.append(best[keys[rank]])
        selected.extend(sorted(reserved, key=by_distance))

    for entry in ordered:
        if entry["key"] not in taken:
            taken.add(entry["key"])
            selected.append(entry)

    if limit:
        selected = selected[: max(1, int(limit))]
    ordered = sorted(selected, key=by_distance)

    merged = {
        "ids": [[entry["key"] for entry in ordered]],
        "documents": [[entry["document"] for entry in ordered]],
        "metadatas": [[entry["metadata"] for entry in ordered]],
        "distances": [[entry["distance"] for entry in ordered]],
    }
    keywords_by_id = {entry["key"]: entry["keywords"] for entry in ordered}
    return merged, keywords_by_id, None


def _coerce_metadata_value(raw_value):
    """Tenta recuperar o tipo original do valor de metadata (int/float/bool)
    a partir do texto vindo do combo de filtro (HTML <select> só manda
    string), já que o `where` do Chroma exige igualdade exata de tipo."""
    if isinstance(raw_value, (int, float, bool)):
        return raw_value
    text = str(raw_value)
    if text.lower() in ("true", "false"):
        return text.lower() == "true"
    try:
        return int(text)
    except ValueError:
        pass
    try:
        return float(text)
    except ValueError:
        pass
    return text


def _build_result_rows(results, max_distance=None, keywords_by_id=None):
    documents = results.get("documents", [[]])[0]
    metadatas = results.get("metadatas", [[]])[0]
    distances = results.get("distances", [[]])[0]
    ids = (results.get("ids") or [[]])[0]

    if max_distance is None:
        max_distance = MAX_DISTANCE
    else:
        try:
            max_distance = float(max_distance)
        except (TypeError, ValueError):
            max_distance = MAX_DISTANCE

    rows = []
    for idx, document in enumerate(documents):
        metadata = metadatas[idx] if idx < len(metadatas) else {}
        distance = distances[idx] if idx < len(distances) else None
        if distance is not None and distance > max_distance:
            continue

        source = metadata.get("source") or metadata.get("path") or metadata.get("file") or ""
        page = _resolve_page_value(metadata, document)

        if source and isinstance(source, str):
            source = Path(source).name if Path(source).name else source

        header_parts = []
        if source:
            header_parts.append(source)
        if page not in (None, ""):
            header_parts.append(f"pág. {page}")

        chunk_id = ids[idx] if idx < len(ids) else None
        rows.append({
            "id": len(rows) + 1,
            "header": " • ".join(header_parts) if header_parts else f"Trecho {len(rows) + 1}",
            "source": source,
            "page": page,
            "distance": round(distance, 6) if distance is not None else None,
            "text": document,
            "kind": metadata.get("kind"),
            "figure_path": metadata.get("figure_path"),
            "keywords": (keywords_by_id or {}).get(chunk_id) or [],
        })

    return rows


def _figure_caption(text):
    """Extrai a legenda de um chunk de figura indexado pelo rag_papers.py
    (formato "[FIGURA] legenda" ou "[FIGURA sem legenda]", ver _fig_text)."""
    first_line = (text or "").split("\n", 1)[0].strip()
    if first_line.startswith("[FIGURA] "):
        return first_line[len("[FIGURA] "):].strip()
    return ""


# Mesmo padrão que o front-end usa pra embutir a imagem na resposta (ver
# FIGURE_MENTION_RE em static/js/components/answer.js) — usado aqui só pra
# identificar quais números de figura o texto cita (ver
# ChatService.stream_answer / backfill_mentioned_figures).
_FIGURE_MENTION_RE = re.compile(r"\bFig(?:ura)?\.?\s*(\d+(?:\.\d+)?)", re.IGNORECASE)


def _mentioned_figure_numbers(text):
    """Números de figura citados no texto ("Figura 2.53", "Fig. 5", ...),
    na ordem em que aparecem, sem repetição."""
    seen = []
    for m in _FIGURE_MENTION_RE.finditer(text or ""):
        num = m.group(1)
        if num not in seen:
            seen.append(num)
    return seen


# Equações: o rag_papers.py grava cada fórmula como LaTeX entre $$...$$, com
# \tag{N} quando numerada (ver clean_latex lá). Menção a uma equação
# numerada na resposta ("Equação (3.2)", "Eq. 5", "equations 2") — mesmo
# padrão de EQUATION_MENTION_RE em static/js/components/answer.js.
_DISPLAY_MATH_RE = re.compile(r"\$\$([\s\S]+?)\$\$")
_TAG_RE = re.compile(r"\\tag\*?\s*\{([^{}]+)\}")
_EQUATION_MENTION_RE = re.compile(
    r"\b(?:equa[çc][ãa]o|equa[çc][õo]es|equations?|eqs?)\.?\s*\(?\s*(\d+(?:\.\d+)*[a-z]?)\b\)?",
    re.IGNORECASE,
)


def _tagged_equations(text):
    """(número, latex) de cada $$...$$ numerado (\\tag{N}) do texto."""
    found = []
    for m in _DISPLAY_MATH_RE.finditer(text or ""):
        latex = m.group(1).strip()
        tag = _TAG_RE.search(latex)
        if tag:
            found.append((tag.group(1).strip().strip("()"), latex))
    return found


def _mentioned_equation_numbers(text):
    """Números de equação citados no texto, na ordem, sem repetição."""
    seen = []
    for m in _EQUATION_MENTION_RE.finditer(text or ""):
        if m.group(1) not in seen:
            seen.append(m.group(1))
    return seen


class RetrievalService:
    """API pública de recuperação usada pelos controllers e pelo ChatService."""

    @staticmethod
    def build_where(filter_key, filter_value):
        filter_key = (filter_key or "").strip()
        if not filter_key:
            return None
        if isinstance(filter_value, (list, tuple)):
            values = [_coerce_metadata_value(v) for v in filter_value if v not in (None, "")]
            if not values:
                return None
            if len(values) == 1:
                return {filter_key: values[0]}
            return {filter_key: {"$in": values}}
        if filter_value in (None, ""):
            return None
        return {filter_key: _coerce_metadata_value(filter_value)}

    @staticmethod
    def retrieve_rows(collection_name, question, n_results=NRAG_RESULTS, where=None,
                       max_distance=None, use_keywords=False):
        """Recupera os trechos que virarão CONTEXTO. Com use_keywords=True,
        busca por palavras-chave E pela frase inteira ao mesmo tempo (ver
        _search_by_keywords). Devolve (rows, keywords, erro); `keywords` pode
        vir vazio mesmo com use_keywords=True (pergunta só com palavras
        vazias) — a frase inteira ainda participa da busca nesse caso."""
        if not question:
            return [], [], "Informe uma pergunta."

        if use_keywords:
            keywords = KeywordService.extract(question)
            if max_distance in (None, ""):
                max_distance = KEYWORD_MAX_DISTANCE
            results, keywords_by_id, error = _search_by_keywords(
                collection_name, keywords, NRAG_RESULTS, where, limit=n_results, phrase=question
            )
            if error:
                return [], keywords, error
            rows = _build_result_rows(results, max_distance=max_distance, keywords_by_id=keywords_by_id)
            return rows, keywords, None

        if max_distance in (None, ""):
            max_distance = MAX_DISTANCE
        results, error = _search_collection(collection_name, question, n_results, where)
        if error:
            return [], [], error
        return _build_result_rows(results, max_distance=max_distance), [], None

    @staticmethod
    def metadata_fields(collection_name):
        """Chaves de metadata presentes nos chunks de uma collection e os
        valores distintos de cada uma (só tipos escalares), pra popular os
        combos de filtro em /rag-view.

        Lê o cache gravado pelos indexadores no metadata da própria
        collection (ver metadata_fields.refresh_filter_fields_cache) — só cai
        na varredura de todos os chunks se a collection ainda não tiver esse
        cache (ex.: indexada antes dessa mudança)."""
        cached = decode_filter_fields(ChromaCollectionFactory.metadata(collection_name).get(FILTER_FIELDS_METADATA_KEY))
        if cached is not None:
            return cached, None

        try:
            col = ChromaCollectionFactory.open(collection_name)
        except Exception as exc:
            return {}, f"Erro ao abrir a collection: {exc}"

        try:
            metadatas = iter_collection_metadatas(col)
        except Exception as exc:
            return {}, f"Erro ao ler metadados: {exc}"

        return compute_filter_fields(metadatas, METADATA_FIELDS_EXCLUDED), None

    @staticmethod
    def extract_figures(rows):
        """Figuras presentes nos trechos recuperados (kind=="figure"), pra
        que o front-end substitua o marcador [FIGURA] na resposta do LLM
        pela imagem real do disco, sem depender do LLM citar o caminho
        corretamente."""
        figures = []
        for row in rows:
            if row.get("kind") == "figure" and row.get("figure_path"):
                figures.append({"path": row["figure_path"], "caption": _figure_caption(row["text"])})
        return figures

    @staticmethod
    def find_figure_by_number(collection_name, number, where=None):
        """Busca direta (por conteúdo literal, sem embedding) pelo chunk de
        figura cuja legenda cite esse número — usado como "resgate" (ver
        backfill_mentioned_figures). `number` sozinho ("2.5") bate por
        substring em vários números ("2.50", "2.53", ...), então filtra pelo
        número exato extraído da legenda. Devolve {"path", "caption"} ou
        None se não achar."""
        try:
            col = ChromaCollectionFactory.open(collection_name)
        except Exception:
            return None

        filters = [{"kind": "figure"}]
        if where:
            filters.append(where)
        where_clause = {"$and": filters} if len(filters) > 1 else filters[0]

        try:
            got = col.get(
                where=where_clause,
                where_document={"$contains": f"Figura {number}"},
                include=["documents", "metadatas"],
                limit=50,
            )
        except Exception:
            return None

        for doc, meta in zip(got.get("documents") or [], got.get("metadatas") or []):
            if _mentioned_figure_numbers(doc)[:1] == [number]:
                return {"path": meta.get("figure_path"), "caption": _figure_caption(doc)}
        return None

    @staticmethod
    def backfill_mentioned_figures(collection_name, answer, existing_figures, where=None):
        """Figuras que a resposta do modelo citou ("Figura 2.53") mas cujo
        chunk de imagem não veio na busca por similaridade (ver
        find_figure_by_number) — chamado depois da resposta pronta, só pros
        números que ainda faltam. Devolve a lista das novas encontradas
        (pode vir vazia); quem chama decide se funde com `existing_figures`."""
        have_numbers = set()
        have_paths = set()
        for fig in existing_figures:
            have_paths.add(fig.get("path"))
            nums = _mentioned_figure_numbers(fig.get("caption") or "")
            if nums:
                have_numbers.add(nums[0])

        found = []
        for number in _mentioned_figure_numbers(answer):
            if number in have_numbers:
                continue
            fig = RetrievalService.find_figure_by_number(collection_name, number, where)
            if fig and fig.get("path") and fig["path"] not in have_paths:
                found.append(fig)
                have_paths.add(fig["path"])
                have_numbers.add(number)
        return found

    @staticmethod
    def extract_equations(rows):
        """Equações numeradas presentes nos trechos recuperados (tanto nos
        chunks de texto quanto nos kind=="equation"), pra que o front-end
        exiba a equação, renderizada a partir do LaTeX indexado, quando a
        resposta citar "Equação (N)" — sem depender de o LLM reescrever o
        LaTeX corretamente. Um mesmo número em dois documentos: vale o do
        trecho mais próximo da pergunta (o primeiro)."""
        equations, seen = [], set()
        for row in rows:
            for number, latex in _tagged_equations(row.get("text")):
                if number not in seen:
                    seen.add(number)
                    equations.append({"number": number, "latex": latex,
                                      "source": row.get("source"), "page": row.get("page")})
        return equations

    @staticmethod
    def find_equation_by_number(collection_name, number, where=None):
        """Busca direta (por metadado, sem embedding) pelo chunk da equação
        com esse número — usado como "resgate" (ver
        backfill_mentioned_equations). Devolve {"number", "latex", "source",
        "page"} ou None se não achar."""
        try:
            col = ChromaCollectionFactory.open(collection_name)
        except Exception:
            return None

        filters = [{"kind": "equation"}, {"equation_number": number}]
        if where:
            filters.append(where)

        try:
            got = col.get(where={"$and": filters}, include=["documents", "metadatas"], limit=1)
        except Exception:
            return None

        for doc, meta in zip(got.get("documents") or [], got.get("metadatas") or []):
            for found_number, latex in _tagged_equations(doc):
                if found_number == number:
                    return {"number": number, "latex": latex,
                            "source": meta.get("source"), "page": meta.get("page")}
        return None

    @staticmethod
    def backfill_mentioned_equations(collection_name, answer, existing_equations, where=None):
        """Equações que a resposta citou ("Equação (3.2)") mas que não vieram
        nos trechos recuperados (ver find_equation_by_number) — chamado depois
        da resposta pronta, só pros números que ainda faltam. Devolve só as
        novas; quem chama decide se funde com `existing_equations`."""
        have = {eq["number"] for eq in existing_equations}
        found = []
        for number in _mentioned_equation_numbers(answer):
            if number in have:
                continue
            eq = RetrievalService.find_equation_by_number(collection_name, number, where)
            if eq:
                found.append(eq)
                have.add(number)
        return found

    @staticmethod
    def context_blocks(rows):
        blocks = []
        for row in rows:
            identifier = f"{row['id']} | {row['header']}"
            if row["text"]:
                blocks.append(f"### {identifier}\n{row['text']}")
        return "\n\n".join(blocks)

    @staticmethod
    def list_collections():
        try:
            cols = ChromaClientFactory.create().list_collections()
            return sorted(c.name for c in cols)
        except Exception:
            return ["No collection found!"]
