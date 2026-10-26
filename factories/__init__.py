"""Factories: criação dos objetos "complexos" usados pelo sistema RAG web —
o client/collection do Chroma e o client de chat do Ollama.
"""
from .chroma_factory import ChromaClientFactory, ChromaCollectionFactory
from .ollama_factory import OllamaClientFactory

__all__ = [
    "ChromaClientFactory",
    "ChromaCollectionFactory",
    "OllamaClientFactory",
]
