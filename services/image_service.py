"""Service: validação de caminhos de imagens locais (figuras extraídas dos
PDFs) citadas nas respostas do RAG, antes de servi-las pelo endpoint
`/local-image` — nunca serve um arquivo fora de LOCAL_IMAGE_ROOTS.
"""
from pathlib import Path

from models import LOCAL_IMAGE_EXTENSIONS, LOCAL_IMAGE_ROOTS


class LocalImageService:
    @staticmethod
    def resolve(raw_path):
        """Resolve `raw_path` para um Path seguro dentro de um dos
        LOCAL_IMAGE_ROOTS, ou devolve None se o caminho for inválido, tiver
        extensão não permitida, não existir, ou cair fora dos diretórios
        conhecidos (nesse último caso, `forbidden` some True para o
        controller responder 403 em vez de 404)."""
        if not raw_path:
            return None, False

        try:
            target = Path(raw_path).resolve()
        except (OSError, ValueError):
            return None, False

        if target.suffix.lower() not in LOCAL_IMAGE_EXTENSIONS or not target.is_file():
            return None, False

        roots = []
        for root in LOCAL_IMAGE_ROOTS:
            try:
                roots.append(root.resolve())
            except OSError:
                continue

        if not any(target == root or root in target.parents for root in roots):
            return None, True  # forbidden: existe, mas fora dos diretórios conhecidos

        return target, False
