"""Model: configuração e constantes do sistema RAG web — ver `config.py` — e
utilitários de metadata de collection compartilhados com os indexadores
standalone — ver `metadata_fields.py`.
"""
from .config import *  # noqa: F401,F403
from .metadata_fields import (
    FILTER_FIELDS_METADATA_KEY,
    METADATA_FIELDS_EXCLUDED,
    compute_filter_fields,
    decode_filter_fields,
    encode_filter_fields,
    iter_collection_metadatas,
    refresh_filter_fields_cache,
)
