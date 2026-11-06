"""Service: onde ficam os documentos de cada usuário — a base no Chroma, os
PDFs enviados e as imagens das figuras.

Tudo continua na mesma máquina, mas separado por conta: cada usuário tem a
sua collection e as suas subpastas, e o servidor sempre as escolhe pela conta
logada, nunca por um nome vindo do navegador. Assim ninguém consulta, lista,
exclui nem vê as figuras dos PDFs de outra conta.
"""
from models import DEFAULT_BASE, FIGURES_DIR, UPLOAD_DIR


class UserStorage:

    @staticmethod
    def collection(user_id):
        """Nome da collection do usuário (ex.: rag_documentos_u3)."""
        return f"{DEFAULT_BASE}_u{int(user_id)}"

    @staticmethod
    def upload_dir(user_id):
        """Pasta dos PDFs enviados pelo usuário (ex.: documentos/u3)."""
        return UPLOAD_DIR / f"u{int(user_id)}"

    @staticmethod
    def figures_dir(user_id):
        """Pasta das imagens das figuras dos PDFs do usuário (ex.: .chromadb/figures/u3)."""
        return FIGURES_DIR / f"u{int(user_id)}"
