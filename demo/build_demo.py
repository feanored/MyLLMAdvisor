"""
build_demo.py
=============

Gera a versão estática de demonstração das telas, para o GitHub Pages: os
templates reais (`templates/`) renderizados em HTML, o `static/` real e uma
API simulada no navegador (`demo/mock-api.js`) no lugar do Flask/Ollama/Chroma.
O app não muda nada: tudo o que é específico do demo é injetado aqui, na
cópia gerada.

Precisa só do Flask (pra renderizar os templates Jinja), não do resto do
projeto — é o que o GitHub Action instala (ver .github/workflows/pages.yml).

Uso:
    python demo/build_demo.py                      # gera demo/dist/
    python demo/build_demo.py --out _site --repo-url https://github.com/usuario/MyLLMAdvisor
    python -m http.server -d demo/dist 8080        # pra ver localmente
"""
from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

from flask import Flask, render_template

ROOT = Path(__file__).resolve().parent.parent
DEMO = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from models import NAV_ITEMS, UPLOAD_MAX_MB  # noqa: E402  (só config, sem dependências pesadas)

# Telas do demo: endpoint -> (template, arquivo gerado, contexto extra).
# As URLs são as mesmas de rag_web.py, pra url_for gerar os mesmos links.
PAGES = {
    "index": ("orientador.html", "index.html", {}),
    "upload_page": ("upload.html", "upload.html", {"max_mb": UPLOAD_MAX_MB}),
}
URLS = {"index": "/", "upload_page": "/upload"}

# Ferramentas de administração ficam de fora do demo.
STATIC_EXCLUDE = {"rag_view.html", "rag_view.css", "rag-view.js"}

# As figuras do app vêm do endpoint /local-image; no demo, de arquivos estáticos.
IMAGE_SRC_APP = '`<img src="/local-image?path=${encodeURIComponent(path)}"'
IMAGE_SRC_DEMO = '`<img src="${path}"'


def _replace(html, old, new, page):
    if old not in html:
        sys.exit(f"build_demo: '{old}' não encontrado em {page} — o template mudou? Ajuste build_demo.py.")
    return html.replace(old, new)


def render_pages(repo_url):
    app = Flask(__name__, template_folder=str(ROOT / "templates"))
    for endpoint, url in URLS.items():
        app.add_url_rule(url, endpoint, lambda: "")

    @app.context_processor
    def _layout_context():
        return {"nav_items": NAV_ITEMS}

    badge = ('<p class="demo-badge">Demonstração: respostas e indexação simuladas no navegador. '
             f'<a href="{repo_url}">Ver o código</a></p>') if repo_url else \
            '<p class="demo-badge">Demonstração: respostas e indexação simuladas no navegador.</p>'

    pages = {}
    with app.test_request_context():
        for endpoint, (template, out_name, context) in PAGES.items():
            html = render_template(template, active=endpoint, **context)
            # Caminhos absolutos do app -> relativos (o Pages serve em /<repositório>/).
            html = _replace(html, 'href="/static/', 'href="static/', out_name)
            html = _replace(html, 'src="/static/', 'src="static/', out_name)
            html = _replace(html, 'href="/favicon.ico"', 'href="static/favicon.ico"', out_name)
            for nav_endpoint, url in URLS.items():
                html = html.replace(f'href="{url}"', f'href="{PAGES[nav_endpoint][1]}"')
            # API simulada antes dos módulos da página (script clássico no <head>).
            html = _replace(html, "</head>",
                            '<link rel="stylesheet" href="demo/demo.css">\n'
                            '<script src="demo/mock-api.js"></script>\n</head>', out_name)
            html = _replace(html, 'class="app-title">My LLM Advisor</a>',
                            f'class="app-title">My LLM Advisor</a>\n    {badge}', out_name)
            pages[out_name] = html
    return pages


def build(out, repo_url):
    if out.exists():
        shutil.rmtree(out)
    shutil.copytree(ROOT / "static", out / "static", ignore=lambda _dir, names: [n for n in names if n in STATIC_EXCLUDE])

    text_js = out / "static" / "js" / "services" / "text.js"
    source = text_js.read_text(encoding="utf-8")
    text_js.write_text(_replace(source, IMAGE_SRC_APP, IMAGE_SRC_DEMO, "static/js/services/text.js"), encoding="utf-8")

    (out / "demo").mkdir()
    for name in ("mock-api.js", "demo.css"):
        shutil.copy2(DEMO / name, out / "demo" / name)
    shutil.copytree(DEMO / "assets", out / "demo" / "assets")

    for name, html in render_pages(repo_url).items():
        (out / name).write_text(html, encoding="utf-8")
    (out / ".nojekyll").write_text("", encoding="utf-8")  # o Pages não deve processar com Jekyll
    print(f"Demo gerado em {out}")


def main():
    parser = argparse.ArgumentParser(description="Gera o demo estático das telas (GitHub Pages).")
    parser.add_argument("--out", default=str(DEMO / "dist"), help="Pasta de saída (apagada e recriada).")
    parser.add_argument("--repo-url", default="", help="Link do repositório, mostrado no aviso de demonstração.")
    args = parser.parse_args()
    build(Path(args.out).resolve(), args.repo_url)


if __name__ == "__main__":
    main()
