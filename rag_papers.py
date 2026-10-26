"""
rag_papers.py
=============

RAG TEXTUAL para artigos científicos e livros (PDF): Docling (extração estruturada,
preservando ordem de leitura/títulos/tabelas/equações/legendas) + BGE-M3
(embedding) + ChromaDB (distância cosseno).

Figuras são extraídas e salvas em disco com página + legenda, e cada uma é
descrita por um LLM de visão local (DESCRIBE_MODEL); a descrição entra no
índice como chunk vinculado à imagem.

Equações: cada fórmula detectada pelo Docling é recortada do PDF e
transcrita para LaTeX por um modelo de OCR local (FORMULA_MODEL, ver
FORMULA_BACKEND); o LaTeX substitui o texto extraído do PDF (quase sempre
ilegível em fórmulas) e entra nos chunks como $$...$$ (ou $...$, se inline).
Equações numeradas (\\tag{N}) ganham também um chunk próprio
(kind="equation", metadado equation_number), usado pelo chat pra exibir a
equação certa quando a resposta cita "Equação (N)".

Fluxo:
  index -> converte PDFs, chunka texto, embedda no ChromaDB, extrai e
           descreve as figuras.
  query -> recupera os chunks mais relevantes e monta o BLOCO de contexto
           (arquivo, página, seção). Com --answer, gera a resposta com o
           ASK_MODEL (Ollama local).

Instalação:
    pip install docling "docling-core[chunking]" chromadb ollama
    ollama pull bge-m3           # modelo de embedding
    ollama pull qwen3:8b         # modelo p/ --answer (QA sobre o contexto recuperado)
    ollama pull qwen3.5          # modelo multimodal p/ descrever imagens
    ollama pull glm-ocr          # OCR de fórmulas -> LaTeX (--formulas ollama)

Uso:
    python rag_papers.py index <pasta_com_pdfs>
    python rag_papers.py index <pasta_com_pdfs> --incremental
    python rag_papers.py query "qual o tema central do artigo?"
    python rag_papers.py query "resuma a metodologia" --answer
    python rag_papers.py query "equação de Navier-Stokes" --kind equation
    python rag_papers.py stats
"""

from __future__ import annotations
import argparse
import hashlib
import io
import re
import sys
import chromadb
from pathlib import Path
from chromadb import Documents, EmbeddingFunction, Embeddings
from chromadb.utils.embedding_functions import register_embedding_function
from models.metadata_fields import iter_collection_metadatas, refresh_filter_fields_cache

# ---------------------------------------------------------------------------
# CONFIGURAÇÃO
# ---------------------------------------------------------------------------

DB_PATH = "./.chromadb"
COLLECTION_NAME = "rag_documentos"
FIGURES_DIR = Path("./.chromadb/figures")

# Modelo de embedding servido pelo Ollama local (multilíngue, 8k tokens de
# contexto); o mesmo do CHUNK_TOKENIZER abaixo, pra o tamanho dos chunks bater.
EMBED_MODEL = "bge-m3"
OLLAMA_HOST = None

# Tokenizer HuggingFace usado só para o HybridChunker contar tokens por chunk
# (docling exige um id de tokenizer HF; não faz inferência, só tokeniza).
CHUNK_TOKENIZER = "BAAI/bge-m3"

MAX_TOKENS = 1024  # tamanho do chunk
TOP_K = 8

# Modelos de chat via Ollama local. DESCRIBE_MODEL precisa ser multimodal
# (suportar imagens) — qwen3 "puro" é texto-only, por isso o padrão é o
# qwen3.5, que é multimodal. ASK_MODEL (para --answer) é só texto, roda com o qwen3:8b normal.
DESCRIBE_MODEL = "qwen3.5"
ASK_MODEL = "qwen3:8b"

# Equações -> LaTeX (escolhido por --formulas):
#   "ollama"  -> recorta cada fórmula do PDF e transcreve com FORMULA_MODEL
#                via Ollama local (padrão);
#   "docling" -> enriquecimento de fórmulas do próprio Docling (modelo
#                CodeFormula, baixado do HuggingFace na 1ª execução; lento em CPU);
#   "none"    -> mantém o texto que o PDF traz (em fórmulas, quase sempre ilegível).
FORMULA_BACKEND = "ollama"
# GLM-OCR (zai-org, licença MIT, 0.9B): OCR de documentos com modo próprio de
# reconhecimento de fórmula, que devolve LaTeX. Leve o bastante pra rodar em
# CPU; `ollama pull glm-ocr`.
FORMULA_MODEL = "glm-ocr"
FORMULA_PROMPT = "Formula Recognition:"
FORMULA_RENDER_SCALE = 3.0  # resolução do recorte da fórmula (1.0 = 72 dpi)
FORMULA_PADDING = 4.0       # margem (pt) em volta da caixa detectada pelo Docling
FORMULA_MAX_CHARS = 2000    # saída maior que isso = OCR degenerado (repetição), descartada


# ---------------------------------------------------------------------------
# COMANDO: query
# ---------------------------------------------------------------------------
DOC_PROMPT = """\
Você é um(a) pesquisador(a) sênior escrevendo uma síntese técnica rigorosa a
partir de artigos científicos e livros. Use APENAS os trechos recuperados como fonte de
verdade; não invente resultados nem números. Cite sempre a fonte REAL do
trecho usado, no formato (NOME_DO_ARQUIVO, p. N, seção) — o nome do arquivo e
a página vêm do cabeçalho de cada trecho do CONTEXTO ("### id | arquivo.pdf •
pág. N"); troque NOME_DO_ARQUIVO pelo nome exibido ali e nunca escreva
"arquivo.pdf" literalmente — isso não identifica nenhum documento real.

Responda de forma APROFUNDADA:
1. Resposta direta à pergunta.
2. Evidência nos trechos (dados, métricas, definições com a notação do texto).
3. Método/contexto relevante.
4. Limitações ou ressalvas mencionadas pelo próprio texto.
Quando um trecho for uma [FIGURA], mencione o arquivo de imagem indicado.
As equações aparecem no CONTEXTO em LaTeX, entre $$ ... $$ ou $ ... $
(\\tag{{N}} é o número da equação no documento): cite as numeradas como
"Equação (N)" — são exibidas automaticamente a partir do LaTeX indexado — e
escreva qualquer outra expressão matemática em LaTeX entre $ ... $ ou
$$ ... $$, copiando do CONTEXTO sem alterar. Nunca invente um número de equação.

Caso não encontre a resposta à PERGUNTA no CONTEXTO, responda apenas: 
Não consegui encontrar a resposta com base no contexto fornecido.

### PERGUNTA
{question}

### CONTEXTO
{context}
"""


# ---------------------------------------------------------------------------
# EMBEDDING FUNCTION (BGE-M3 via Ollama local), normalizada p/ cosine
# ---------------------------------------------------------------------------

@register_embedding_function
class BGEEmbeddingFunction(EmbeddingFunction):
    def __init__(self, model_name: str = EMBED_MODEL, host: str | None = OLLAMA_HOST):
        import ollama
        self.model_name = model_name
        self.host = host
        self._client = ollama.Client(host=host) if host else ollama.Client()
        self._dim: int | None = None

    def __call__(self, input: Documents) -> Embeddings:
        import numpy as np

        texts = list(input)
        resp = self._client.embed(model=self.model_name, input=texts)
        vecs = np.asarray(resp.embeddings, dtype=np.float32)
        norms = np.linalg.norm(vecs, axis=1, keepdims=True)
        vecs = vecs / np.clip(norms, 1e-12, None)  # essencial p/ cosine bem-comportado
        if len(vecs):
            self._dim = vecs.shape[1]
        return vecs.tolist()

    def dim(self) -> int:
        if self._dim is None:
            resp = self._client.embed(model=self.model_name, input=" ")
            self._dim = len(resp.embeddings[0])
        return self._dim

    @staticmethod
    def name() -> str:
        return "bge-m3-ollama-ef"

    def get_config(self) -> dict:
        return {"model_name": self.model_name, "host": self.host}

    @staticmethod
    def build_from_config(config: dict) -> "BGEEmbeddingFunction":
        return BGEEmbeddingFunction(**config)


def get_collection(ef: BGEEmbeddingFunction, name: str | None = None, create: bool = True,
                   client=None):
    """`client`: por padrão abre a pasta DB_PATH direto em disco (CLI); o
    servidor web passa o seu HttpClient, porque dois processos escrevendo na
    mesma pasta do Chroma ao mesmo tempo corrompem o banco."""
    client = client or chromadb.PersistentClient(path=DB_PATH)
    collection_name = name or COLLECTION_NAME
    if not create:
        return client.get_collection(name=collection_name, embedding_function=ef)

    def get_or_create():
        return client.get_or_create_collection(
            name=collection_name,
            embedding_function=ef,
            configuration={"hnsw": {"space": "cosine", "ef_construction": 200,
                                    "ef_search": 100, "max_neighbors": 32}},
            metadata={"embedding_model": ef.model_name, "dimension": ef.dim()},
        )

    try:
        return get_or_create()
    except Exception as exc:
        if "Embedding function conflict" not in str(exc):
            raise
        # A collection já existe gravada com OUTRA embedding function (ex.: a
        # padrão do Chroma, se alguém a criou sem informar nenhuma). Vazia,
        # não há o que perder: recria com a certa. Com dados, misturar
        # embeddings de modelos diferentes estragaria a busca — melhor parar.
        existing = client.get_collection(name=collection_name)
        count = existing.count()
        if count:
            raise RuntimeError(
                f'A collection "{collection_name}" já existe com outra embedding function e tem '
                f"{count} trechos. Apague-a (client.delete_collection) e indexe os PDFs de novo."
            ) from exc
        client.delete_collection(name=collection_name)
        return get_or_create()


# ---------------------------------------------------------------------------
# CONVERSÃO + CHUNKING (Docling)
# ---------------------------------------------------------------------------

def build_converter(formula_backend: str = FORMULA_BACKEND):
    """Converter com geração de imagens das figuras habilitada (e, com
    formula_backend="docling", o enriquecimento de fórmulas do Docling)."""
    from docling.datamodel.base_models import InputFormat
    from docling.datamodel.pipeline_options import PdfPipelineOptions
    from docling.document_converter import DocumentConverter, PdfFormatOption

    opts = PdfPipelineOptions()
    opts.generate_picture_images = True   # necessário p/ extrair as figuras
    opts.images_scale = 2.0
    opts.do_formula_enrichment = formula_backend == "docling"
    return DocumentConverter(
        format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=opts)}
    )


def make_chunker():
    from docling.chunking import HybridChunker
    return HybridChunker(tokenizer=CHUNK_TOKENIZER, max_tokens=MAX_TOKENS)


def _page_from_chunk(chunk) -> int | None:
    """Extrai o menor nº de página da provenance do chunk (defensivo)."""
    try:
        pages = []
        for it in chunk.meta.doc_items:
            for prov in getattr(it, "prov", []) or []:
                if getattr(prov, "page_no", None) is not None:
                    pages.append(prov.page_no)
        return min(pages) if pages else None
    except Exception:
        return None


def _headings_from_chunk(chunk) -> str:
    try:
        return " > ".join(chunk.meta.headings or [])
    except Exception:
        return ""


def file_hash(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(65536), b""):
            h.update(block)
    return h.hexdigest()[:16]


# ---------------------------------------------------------------------------
# EQUAÇÕES -> LaTeX
# ---------------------------------------------------------------------------

_FENCE_RE = re.compile(r"^```(?:latex|tex|math)?\s*|\s*```$", re.IGNORECASE)
# Delimitadores que o modelo às vezes põe em volta da fórmula; o chunker do
# Docling já envolve o texto da fórmula em $$...$$ (ou $...$), então saem.
_MATH_DELIMITERS = (("$$", "$$"), ("\\[", "\\]"), ("\\(", "\\)"), ("$", "$"))
# Ambientes que o MathJax recusa dentro de $$...$$ ("Erroneous nesting") ->
# equivalente interno (None = só tira o ambiente).
_ENV_REPLACEMENTS = {
    "equation": None, "equation*": None, "displaymath": None,
    "align": "aligned", "align*": "aligned", "eqnarray": "aligned", "eqnarray*": "aligned",
    "gather": "gathered", "gather*": "gathered", "multline": "gathered", "multline*": "gathered",
}
_ENV_RE = re.compile(r"\\(begin|end)\{([A-Za-z]+\*?)\}")
# O GLM-OCR escreve "\tag {3.2}" (espaço antes da chave): normalizado por
# clean_latex, mas o padrão aceita os dois.
_TAG_RE = re.compile(r"\\tag\*?\s*\{([^{}]+)\}")
# Número escrito à direita em vez de \tag ("... \qquad (3.2)"); só com um
# espaçamento explícito antes, pra não confundir com f(2) no fim da fórmula.
_TRAILING_NUMBER_RE = re.compile(
    r"(?:\\q?quad|\\hfill|\\[,;: ]|~)+\s*\(\s*(\d+(?:\.\d+)*[a-z]?)\s*\)\s*$"
)


def clean_latex(raw: str) -> str:
    """Normaliza o LaTeX devolvido pelo OCR para entrar entre $$...$$ e ser
    renderizado pelo MathJax: tira cercas de código e delimitadores, troca
    ambientes de equação pelos equivalentes internos, remove \\label e
    \\nonumber, e converte o número à direita em \\tag{N}. Devolve "" se a
    saída não parecer uma fórmula válida."""
    text = _FENCE_RE.sub("", (raw or "").strip()).strip()
    for left, right in _MATH_DELIMITERS:
        if text.startswith(left) and text.endswith(right) and len(text) > len(left) + len(right):
            text = text[len(left):-len(right)].strip()
            break

    def replace_env(m):
        target = _ENV_REPLACEMENTS.get(m.group(2), m.group(2))
        if m.group(2) in _ENV_REPLACEMENTS and target is None:
            return ""
        return f"\\{m.group(1)}{{{target}}}"

    text = _ENV_RE.sub(replace_env, text)
    text = re.sub(r"\\label\{[^{}]*\}|\\nonumber|\\notag", "", text).strip()
    text = re.sub(r"\\tag(\*?)\s*\{", r"\\tag\1{", text)
    if not _TAG_RE.search(text):
        text = _TRAILING_NUMBER_RE.sub(lambda m: f" \\tag{{{m.group(1)}}}", text).strip()
    if not text or len(text) > FORMULA_MAX_CHARS:
        return ""
    return text


def equation_number(latex: str) -> str | None:
    """Número da equação (o conteúdo do \\tag{...}), sem parênteses."""
    m = _TAG_RE.search(latex or "")
    return m.group(1).strip().strip("()") if m else None


def _formula_png(pdf_doc, doc, item) -> bytes | None:
    """Recorta a fórmula direto do PDF (pypdfium2, já instalado junto com o
    Docling) pela caixa detectada pelo Docling — sem precisar guardar a
    imagem de todas as páginas em memória, o que pesaria em livros grandes."""
    prov = (getattr(item, "prov", None) or [None])[0]
    page = doc.pages.get(prov.page_no) if prov else None
    if page is None:
        return None
    width, height = page.size.width, page.size.height
    box = prov.bbox.to_bottom_left_origin(page_height=height)
    pad = FORMULA_PADDING
    # crop = quanto cortar de cada borda da página: (esq., baixo, dir., topo), em pt
    crop = (max(0.0, box.l - pad), max(0.0, box.b - pad),
            max(0.0, width - box.r - pad), max(0.0, height - box.t - pad))
    pdf_page = pdf_doc[prov.page_no - 1]
    try:
        image = pdf_page.render(scale=FORMULA_RENDER_SCALE, crop=crop).to_pil()
    finally:
        pdf_page.close()
    buf = io.BytesIO()
    image.save(buf, format="PNG")
    return buf.getvalue()


def _make_formula_ocr(model: str = FORMULA_MODEL, host: str | None = OLLAMA_HOST):
    """Retorna uma função PNG -> LaTeX (já normalizado por clean_latex) via
    Ollama; "" se o modelo falhar ou devolver algo que não pareça fórmula."""
    import ollama
    client = ollama.Client(host=host) if host else ollama.Client()

    def transcribe(png: bytes) -> str:
        try:
            response = client.chat(
                model=model,
                messages=[{"role": "user", "content": FORMULA_PROMPT, "images": [png]}],
                options={"temperature": 0.0},
            )
        except Exception as exc:
            print(f"  aviso: falha ao transcrever fórmula com '{model}': {exc}")
            return ""
        return clean_latex(response.message.content or "")

    return transcribe


def process_formulas(doc, pdf_path: Path, backend: str, ocr=None, log=print) -> list[dict]:
    """Troca o texto de cada fórmula do documento (label FORMULA) pelo LaTeX
    — transcrito pelo `ocr` (backend "ollama") ou já preenchido pelo Docling
    (backend "docling", só normalizado aqui) — ANTES do chunking, pra que os
    chunks de texto já saiam com a fórmula em LaTeX. Devolve as equações
    numeradas ({latex, number, page, context}), que viram chunks próprios."""
    if backend == "none":
        return []
    from docling_core.types.doc import DocItemLabel

    pdf_doc = None
    if backend == "ollama" and ocr is not None:
        try:
            import pypdfium2 as pdfium
            pdf_doc = pdfium.PdfDocument(str(pdf_path))
        except Exception as exc:
            log(f"aviso: não foi possível abrir {pdf_path.name} p/ recortar as fórmulas ({exc})")

    text_labels = {DocItemLabel.TEXT, DocItemLabel.PARAGRAPH, DocItemLabel.LIST_ITEM}
    # Parágrafos e fórmulas na ordem de leitura (o parágrafo vira o contexto
    # da fórmula seguinte); a lista é montada antes pra saber o total.
    items = [item for item, _level in doc.iterate_items()
             if getattr(item, "label", None) in text_labels | {DocItemLabel.FORMULA}]
    total = sum(1 for item in items if item.label == DocItemLabel.FORMULA)
    equations, done, converted = [], 0, 0
    last_text = ""  # parágrafo anterior à fórmula: contexto do chunk da equação
    try:
        for item in items:
            if item.label in text_labels:
                last_text = item.text or last_text
                continue
            done += 1
            if done == 1 or done % 10 == 0:
                log(f"Convertendo fórmulas para LaTeX ({done}/{total})")
            if pdf_doc is not None:
                try:
                    png = _formula_png(pdf_doc, doc, item)
                except Exception as exc:
                    log(f"aviso: falha ao recortar fórmula ({exc})")
                    png = None
                latex = ocr(png) if png else ""
            else:
                latex = clean_latex(item.text)
            if not latex:
                continue  # mantém o texto que já estava
            item.text = latex
            converted += 1
            number = equation_number(latex)
            if number:
                prov = (getattr(item, "prov", None) or [None])[0]
                equations.append({
                    "latex": latex,
                    "number": number,
                    "page": prov.page_no if prov else -1,
                    "context": last_text[-400:],
                })
    finally:
        if pdf_doc is not None:
            pdf_doc.close()

    if total:
        log(f"{converted}/{total} fórmulas convertidas para LaTeX ({len(equations)} numeradas)")
    return equations


# ---------------------------------------------------------------------------
# COMANDO: index
# ---------------------------------------------------------------------------

def build_pipeline(formulas: str = FORMULA_BACKEND) -> dict:
    """Objetos caros de criar (converter do Docling, chunker, clients do
    Ollama), montados uma vez e reaproveitados por todos os PDFs — pela CLI
    (cmd_index) e pela tela de upload (IndexingService)."""
    FIGURES_DIR.mkdir(parents=True, exist_ok=True)
    return {
        "formulas": formulas,
        "converter": build_converter(formulas),
        "chunker": make_chunker(),
        "describer": _make_image_describer(),
        "formula_ocr": _make_formula_ocr() if formulas == "ollama" else None,
    }


def is_indexed(col, pdf: Path, h: str | None = None) -> bool:
    """Se essa versão exata do PDF (mesmo nome e mesmo hash) já está na collection."""
    got = col.get(where={"$and": [{"source": pdf.name}, {"file_hash": h or file_hash(pdf)}]}, limit=1)
    return bool(got.get("ids"))


def index_pdf(col, pdf: Path, pipeline: dict, log=print) -> int:
    """Converte, chunka e indexa UM PDF (texto, figuras e equações),
    substituindo qualquer versão anterior do mesmo arquivo (mesmo `source`)
    na collection. Devolve o número de chunks gravados. `log` recebe as
    mensagens de progresso (console na CLI, tela no upload)."""
    h = file_hash(pdf)
    converter, chunker = pipeline["converter"], pipeline["chunker"]
    describer = pipeline["describer"]

    log(f"Convertendo {pdf.name}")
    doc = converter.convert(source=str(pdf)).document
    equations = process_formulas(doc, pdf, pipeline["formulas"], pipeline["formula_ocr"], log=log)

    docs, ids, metas = [], [], []

    # 1) Chunks de texto (contextualizados com títulos de seção).
    log("Dividindo o texto em trechos")
    for i, chunk in enumerate(chunker.chunk(dl_doc=doc)):
        text = chunker.contextualize(chunk=chunk)
        if not text.strip():
            continue
        docs.append(text)
        ids.append(f"{pdf.name}::{h}::txt::{i}")
        metas.append({
            "source": pdf.name,
            "source_path": str(pdf),
            "page": _page_from_chunk(chunk) or -1,
            "headings": _headings_from_chunk(chunk),
            "kind": "text",
            "file_hash": h,
        })

    # 2) Figuras: salva a imagem e (opcional) indexa uma descrição.
    pictures = getattr(doc, "pictures", []) or []
    for pic_i, pic in enumerate(pictures):
        try:
            img = pic.get_image(doc)
            caption = (pic.caption_text(doc) or "").strip()
            page = pic.prov[0].page_no if getattr(pic, "prov", None) else -1
        except Exception:
            img, caption, page = None, "", -1
        if img is None:
            continue
        log(f"Descrevendo figuras ({pic_i + 1}/{len(pictures)})")
        fig_path = FIGURES_DIR / f"{pdf.stem}__fig{pic_i:03d}.png"
        img.save(fig_path)

        fig_text = f"[FIGURA] {caption}" if caption else "[FIGURA sem legenda]"
        if describer is not None:
            desc = describer(fig_path, caption)
            if desc:
                fig_text += f"\nDescrição visual: {desc}"

        docs.append(fig_text)
        ids.append(f"{pdf.name}::{h}::fig::{pic_i}")
        metas.append({
            "source": pdf.name,
            "source_path": str(pdf),
            "page": page,
            "headings": "",
            "kind": "figure",
            "figure_path": str(fig_path),
            "file_hash": h,
        })

    # 3) Equações numeradas: um chunk por equação (LaTeX + parágrafo
    #    anterior como contexto), achável pelo número (equation_number).
    for eq_i, eq in enumerate(equations):
        eq_text = f"[EQUAÇÃO ({eq['number']})] $${eq['latex']}$$"
        if eq["context"]:
            eq_text += f"\nContexto: {eq['context']}"
        docs.append(eq_text)
        ids.append(f"{pdf.name}::{h}::eq::{eq_i}")
        metas.append({
            "source": pdf.name,
            "source_path": str(pdf),
            "page": eq["page"],
            "headings": "",
            "kind": "equation",
            "equation_number": eq["number"],
            "file_hash": h,
        })

    # Tira a versão anterior do mesmo arquivo (outro hash, ou outra contagem
    # de chunks) só agora, com a nova já pronta pra entrar no lugar.
    log("Gravando na base")
    col.delete(where={"source": pdf.name})
    for s in range(0, len(docs), 100):
        col.upsert(documents=docs[s:s + 100], ids=ids[s:s + 100],
                   metadatas=metas[s:s + 100])
    log(f"{len(docs)} trechos indexados (texto, figuras e equações)")
    return len(docs)


def cmd_index(args):
    root = Path(args.path).resolve()
    if not root.is_dir():
        sys.exit(f"Pasta não encontrada: {root}")
    pdfs = sorted(root.rglob("*.pdf"))
    if not pdfs:
        sys.exit("Nenhum PDF encontrado.")

    print(f"Carregando modelo de embedding: {EMBED_MODEL} (collection: {COLLECTION_NAME}) ...")
    ef = BGEEmbeddingFunction(model_name=EMBED_MODEL)
    col = get_collection(ef, create=True)

    indexed_hashes = set()
    if args.incremental:
        indexed_hashes = {m.get("file_hash") for m in iter_collection_metadatas(col) if m}

    pipeline = build_pipeline(args.formulas)
    total_chunks = 0
    for pdf in pdfs:
        if args.incremental and file_hash(pdf) in indexed_hashes:
            print(f"Pulando (já indexado): {pdf.name}")
            continue
        total_chunks += index_pdf(col, pdf, pipeline, log=lambda msg: print(f"  {msg}"))

    print(f"Concluído. Total na coleção: {col.count()} (novos: {total_chunks}).")

    refresh_filter_fields_cache(col)


def _make_image_describer(model: str = DESCRIBE_MODEL, host: str | None = OLLAMA_HOST):
    """Retorna uma função que descreve uma figura via Ollama (modelo multimodal local)."""
    import ollama
    client = ollama.Client(host=host) if host else ollama.Client()

    def describe(fig_path: Path, caption: str) -> str:
        try:
            response = client.chat(
                model=model,
                messages=[{
                    "role": "user",
                    "content": (
                        "Descreva objetivamente esta figura de artigo científico ou livro "
                        "(tipo de gráfico, eixos, variáveis, tendência principal). "
                        f"Legenda original: {caption!r}"
                    ),
                    "images": [fig_path.read_bytes()],
                }],
                # O qwen3.5 raciocina (thinking) por padrão: sem isso, gasta
                # minutos "pensando" antes de descrever cada figura.
                think=False,
                options={"temperature": 0.1},
            )
        except Exception as exc:
            print(f"aviso: falha ao descrever {fig_path.name} com '{model}': {exc}")
            return ""
        return (response.message.content or "").strip()

    return describe


def _format_context(res) -> str:
    blocks = []
    for doc, m, dist in zip(res["documents"][0], res["metadatas"][0],
                            res["distances"][0]):
        loc = f"{m['source']}, p.{m['page']}"
        if m.get("headings"):
            loc += f", {m['headings']}"
        if m.get("kind") == "figure" and m.get("figure_path"):
            loc += f", imagem: {m['figure_path']}"
        blocks.append(f"# {loc}  (similaridade≈{1 - dist:.3f})\n{doc}")
    return "\n\n".join(blocks)


def cmd_query(args):
    model_name, collection_name = EMBED_MODEL, COLLECTION_NAME
    ef = BGEEmbeddingFunction(model_name=model_name)
    col = get_collection(ef, name=collection_name, create=False)

    where = {"kind": args.kind} if args.kind else None
    res = col.query(query_texts=[args.question], n_results=args.top_k,
                    where=where, include=["documents", "metadatas", "distances"])
    if not res["documents"][0]:
        print("Nenhum trecho relevante. Você já rodou `index`?")
        return

    context = _format_context(res)
    prompt = DOC_PROMPT.format(question=args.question, context=context)

    if args.answer:
        _answer_with_ollama(prompt)
    else:
        print(prompt)
        print("\n" + "=" * 70)
        print("Bloco acima pronto para colar no LLM. Ou rode com --answer.")


def _answer_with_ollama(prompt: str, model: str = ASK_MODEL, host: str | None = OLLAMA_HOST):
    import ollama
    client = ollama.Client(host=host) if host else ollama.Client()
    print("\n--- Resposta do LLM ---\n")
    for chunk in client.chat(
        model=model,
        messages=[{"role": "user", "content": prompt}],
        stream=True,
        options={"temperature": 0.1},
    ):
        if chunk.message.content:
            print(chunk.message.content, end="", flush=True)
    print()


# ---------------------------------------------------------------------------
# COMANDO: stats
# ---------------------------------------------------------------------------

def cmd_stats(args):
    model_name, collection_name = EMBED_MODEL, COLLECTION_NAME
    ef = BGEEmbeddingFunction(model_name=model_name)
    col = get_collection(ef, name=collection_name, create=False)
    sources, kinds = set(), {}
    for m in iter_collection_metadatas(col):
        if not m:
            continue
        sources.add(m["source"])
        kinds[m["kind"]] = kinds.get(m["kind"], 0) + 1
    print(f"Coleção: {collection_name} | modelo: {col.metadata.get('embedding_model')}")
    print(f"Chunks: {col.count()} | Documentos: {len(sources)}")
    for k, n in kinds.items():
        print(f"  {k}: {n}")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    p = argparse.ArgumentParser(description="RAG textual de artigos e livros (Docling+BGE-M3).")
    sub = p.add_subparsers(dest="cmd", required=True)

    pi = sub.add_parser("index", help="Indexar PDFs de uma pasta.")
    pi.add_argument("path")
    pi.add_argument("--incremental", action="store_true")
    pi.add_argument("--formulas", choices=["ollama", "docling", "none"], default=FORMULA_BACKEND,
                    help="Como converter as equações para LaTeX (ver FORMULA_BACKEND).")
    pi.set_defaults(func=cmd_index)

    pq = sub.add_parser("query", help="Consultar e montar bloco de contexto.")
    pq.add_argument("question")
    pq.add_argument("--top-k", type=int, default=TOP_K)
    pq.add_argument("--kind", choices=["text", "figure", "equation"],
                    help="Filtrar só texto, só figuras ou só equações numeradas.")
    pq.add_argument("--answer", action="store_true")
    pq.set_defaults(func=cmd_query)

    ps = sub.add_parser("stats", help="Estatísticas da coleção.")
    ps.set_defaults(func=cmd_stats)

    args = p.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
