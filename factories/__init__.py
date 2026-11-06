"""Factories: criação dos objetos "complexos" usados pelo sistema RAG web —
o client/collection do Chroma, o client de chat do Ollama e o pool de
conexões do PostgreSQL.
"""
from .chroma_factory import ChromaClientFactory, ChromaCollectionFactory
from .ollama_factory import OllamaClientFactory
from .postgres_factory import PostgresPoolFactory

__all__ = [
    "ChromaClientFactory",
    "ChromaCollectionFactory",
    "OllamaClientFactory",
    "PostgresPoolFactory",
]
