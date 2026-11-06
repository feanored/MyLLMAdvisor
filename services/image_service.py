"""Service: validação de caminhos de imagens locais (figuras extraídas dos
PDFs) citadas nas respostas do RAG, antes de servi-las pelo endpoint
`/local-image` — só serve imagens da pasta de figuras da conta logada.
"""
from pathlib import Path

from models import LOCAL_IMAGE_EXTENSIONS

from .user_storage import UserStorage


class LocalImageService:
    @staticmethod
    def resolve(raw_path, user_id):
        """A imagem de nome igual ao de `raw_path` na pasta de figuras do
        usuário (ver UserStorage.figures_dir), ou None se não houver.

        Só o nome do arquivo conta, nunca as pastas do caminho: não dá pra
        sair da pasta da conta ("../"), e caminhos gravados antes de as
        figuras irem para uma pasta por conta (respostas antigas, conversas
        importadas) continuam achando a imagem depois de os PDFs serem
        passados à conta (manage_users.py adopt-documents)."""
        name = Path(str(raw_path or "").replace("\\", "/")).name
        if not name or Path(name).suffix.lower() not in LOCAL_IMAGE_EXTENSIONS:
            return None
        # Absoluto: o send_file do Flask leria um caminho relativo a partir
        # da pasta do app, não da pasta onde o servidor roda.
        target = (UserStorage.figures_dir(user_id) / name).resolve()
        return target if target.is_file() else None
