"""Factory: client e collections do ChromaDB.

Abrir uma collection exige escolher a embedding function certa por trás —
essa decisão mora aqui, num único lugar, para que os services só peçam a
collection já pronta sem precisar saber os detalhes de construção.
"""
import chromadb

from models import CHROMA_HOST, CHROMA_PORT


class ChromaClientFactory:
    """Cria o client HTTP do ChromaDB (CHROMA_HOST/CHROMA_PORT, por padrão o
    servidor local na porta 8000)."""

    @staticmethod
    def create():
        return chromadb.HttpClient(host=CHROMA_HOST, port=CHROMA_PORT)


class ChromaCollectionFactory:
    """Abre collections do Chroma escolhendo a embedding function certa.

    A escolha é pelos metadados gravados na collection, nunca pelo nome:
    rag_papers.py grava embedding_model/dimension ("docling"); sem eles, é
    uma collection avulsa ("generic"). Reabrir com a EF errada é rejeitado
    pelo Chroma.
    """

    @staticmethod
    def metadata(collection_name):
        """Metadados gravados na collection (embedding_model, dimension,
        ...), sem precisar instanciar nenhuma embedding function pra lê-los."""
        try:
            cols = ChromaClientFactory.create().list_collections()
        except Exception:
            return {}
        for c in cols:
            if c.name == collection_name:
                return c.metadata or {}
        return {}

    @staticmethod
    def kind(collection_name):
        """Identifica o "dono" da collection: "docling" (rag_papers.py) ou
        "generic" (collection avulsa, sem metadados do indexador)."""
        meta = ChromaCollectionFactory.metadata(collection_name)
        if "embedding_model" in meta:
            return "docling"
        return "generic"

    @staticmethod
    def open(collection_name):
        """Abre a collection já com a embedding function certa pro seu
        "dono", sempre pelo servidor HTTP (import tardio do rag_papers.py:
        evita puxar Docling/torch etc. na inicialização do servidor web).

        Só ABRE, nunca cria: criar aqui uma collection que ainda não existe a
        gravaria com a embedding function padrão do Chroma, e o primeiro
        upload (que a abre com a do bge-m3) seria recusado por conflito.
        Quem cria é o indexador (rag_papers.get_collection)."""
        meta = ChromaCollectionFactory.metadata(collection_name)
        if not meta and collection_name not in ChromaCollectionFactory._names():
            raise LookupError(f'A base "{collection_name}" ainda não existe: envie um PDF em /upload para criá-la.')
        kind = ChromaCollectionFactory.kind(collection_name)
        embedding_model = meta.get("embedding_model")
        client = ChromaClientFactory.create()

        if kind == "docling":
            import rag_papers as docling
            ef = docling.BGEEmbeddingFunction(model_name=embedding_model)
            return docling.get_collection(ef, name=collection_name, create=False, client=client)

        return client.get_collection(name=collection_name)

    @staticmethod
    def _names():
        try:
            return {c.name for c in ChromaClientFactory.create().list_collections()}
        except Exception:
            return set()
