"""Utilitário compartilhado: cache dos valores distintos de metadata de uma
collection do Chroma (o Chroma não expõe `DISTINCT`/agregação no servidor).
Os indexadores calculam isso uma vez ao final da indexação e gravam no
próprio metadata da collection (`FILTER_FIELDS_METADATA_KEY`);
`RetrievalService.metadata_fields` lê esse cache, caindo na varredura
completa só se a collection não tiver sido indexada com ele.

Zero dependências pesadas (só `json`) de propósito: importado tanto pelo
servidor web quanto pelos scripts de indexação standalone, que assim evitam
puxar Flask/Docling/torch etc.
"""
import json

# Chaves de metadata que nunca viram opção de filtro em /rag-view (são
# identificadores internos do chunk, não atributos que façam sentido escolher
# num combo).
METADATA_FIELDS_EXCLUDED = {
    "file_hash", "chunk_hash", "chunk_index", "start_line", "end_line",
    "line_start", "line_end", "figure_path", "source_path", "page", "equation_number",
}

# Chave de metadata da própria collection onde o cache fica gravado.
FILTER_FIELDS_METADATA_KEY = "filter_fields_json"

_METADATA_BATCH_SIZE = 300


def iter_collection_metadatas(col, batch_size=_METADATA_BATCH_SIZE):
    """Metadados de todos os chunks de uma collection, paginado em lotes
    (Chroma implementa .get(include=["metadatas"], limit=..., offset=...)).
    Buscar tudo de uma vez estoura o limite de variáveis do SQLite por trás
    do Chroma em collections grandes ("too many SQL variables")."""
    metadatas = []
    offset = 0
    while True:
        got = col.get(include=["metadatas"], limit=batch_size, offset=offset)
        batch = got.get("metadatas") or []
        metadatas.extend(batch)
        if len(batch) < batch_size:
            break
        offset += batch_size
    return metadatas


def compute_filter_fields(metadatas, excluded_keys=METADATA_FIELDS_EXCLUDED):
    """Chaves de metadata presentes nos chunks e os valores distintos de cada
    uma (só tipos escalares) — formato gravado por `refresh_filter_fields_cache`
    e lido por `RetrievalService.metadata_fields`."""
    fields = {}
    for metadata in metadatas:
        if not metadata:
            continue
        for key, value in metadata.items():
            if key in excluded_keys or not isinstance(value, (str, int, float, bool)):
                continue
            values = fields.setdefault(key, set())
            values.add(value)

    return {
        key: sorted(values, key=lambda v: (type(v).__name__, v))
        for key, values in fields.items()
    }


def encode_filter_fields(fields):
    return json.dumps(fields, ensure_ascii=False)


def decode_filter_fields(raw):
    """None se `raw` estiver ausente ou não for um cache válido — sinaliza ao
    chamador que precisa cair na varredura completa (ver `metadata_fields`
    em `RetrievalService`)."""
    if not raw:
        return None
    try:
        return json.loads(raw)
    except (TypeError, ValueError):
        return None


def refresh_filter_fields_cache(col, excluded_keys=METADATA_FIELDS_EXCLUDED):
    """Recalcula os valores distintos de metadata da collection e grava o
    resultado como cache no metadata dela — chamado pelos indexadores ao
    final de `cmd_index`, pra que o servidor web nunca precise escanear todos
    os chunks pra responder `/collection-metadata`."""
    metadatas = iter_collection_metadatas(col)
    fields = compute_filter_fields(metadatas, excluded_keys)
    merged_metadata = {**(col.metadata or {}), FILTER_FIELDS_METADATA_KEY: encode_filter_fields(fields)}
    col.modify(metadata=merged_metadata)
