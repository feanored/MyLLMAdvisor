"""Controller: rotas Flask do sistema RAG web.

Lógica de negócio mora em `factories/` e `services/`; configuração em
`models/`. Aqui só request/response HTTP, formatação SSE e qual template
renderizar (`templates/`, todos herdando o layout de `templates/base.html`).
"""
from flask import (Flask, Response, jsonify, render_template, request, send_file,
                   send_from_directory, stream_with_context)
import json
import mimetypes
from pathlib import Path

from models import (
    DEFAULT_BASE,
    DEFAULT_MODEL,
    HELP_DOC_GROUPS,
    KEYWORD_MAX_DISTANCE,
    KEYWORD_SEARCH,
    MAX_DISTANCE,
    NAV_ITEMS,
    NRAG_RESULTS,
    UPLOAD_MAX_MB,
)
from services import ChatService, ConversationService, IndexingService, LocalImageService, RetrievalService

# O front-end usa módulos ES (static/js/): o navegador recusa módulo servido
# com MIME errado, e no Windows o mimetypes lê o tipo do .js do registro
# (às vezes "text/plain").
mimetypes.add_type("text/javascript", ".js")

app = Flask(__name__)
# jsonify ordena as chaves alfabeticamente por padrão — quebraria a ordem de
# exibição dos documentos em /help-groups (ver models/config.py).
app.json.sort_keys = False
app.config["MAX_CONTENT_LENGTH"] = UPLOAD_MAX_MB * 1024 * 1024


@app.context_processor
def _layout_context():
    """Disponível em todo template: os links do menu lateral (NAV_ITEMS)."""
    return {"nav_items": NAV_ITEMS}


def _bool_param(get, key, default):
    """Interpreta um parâmetro booleano vindo de querystring (sempre string)
    ou de JSON (pode já vir bool) da mesma forma: "true" (sem diferenciar
    caixa) é verdadeiro, qualquer outra coisa é falso."""
    return str(get(key, default)).lower() == "true"


def _format_sse_event(name, payload):
    """Serializa um evento de negócio (ver ChatService.stream_answer) no
    formato de fio Server-Sent Events. Listas/dicts viram JSON; "thinking" e
    "content" têm as quebras de linha expandidas em múltiplas linhas "data:"
    (assim o navegador remonta o texto original, ver static/js/services/sse.js);
    os demais payloads (strings de status/erro) vão direto."""
    if isinstance(payload, (list, dict)):
        data = json.dumps(payload)
    elif name in ("thinking", "content"):
        data = payload.replace("\n", "\ndata: ")
    else:
        data = payload
    return f"event: {name}\ndata: {data}\n\n"


def _parse_ask_request():
    """Lê os parâmetros de /ask tanto de POST (JSON) quanto de GET
    (querystring), com a mesma interface, prontos para ChatService.stream_answer."""
    if request.method == "POST":
        get = (request.get_json(silent=True) or {}).get
    else:
        get = request.args.get

    return {
        "question": str(get("question", "")).strip(),
        "model": get("model", DEFAULT_MODEL),
        "collection": get("collection", DEFAULT_BASE),
        "think": _bool_param(get, "think", "false"),
        "contexto": _bool_param(get, "context", "false"),
        "palavras_chave": _bool_param(get, "keywords", KEYWORD_SEARCH),
        "max_distance": get("max_distance"),
        "request_id": get("request_id"),
        "where": RetrievalService.build_where(get("filter_key"), get("filter_value")),
        "help_mode": _bool_param(get, "help_mode", "false"),
        "conversation_id": get("conversation_id"),
        # Decidido pelo front-end (ver is_first_turn em ChatService.stream_answer).
        "is_first_turn": _bool_param(get, "is_first_turn", "true"),
    }


def _indexed_documents():
    """Nomes dos PDFs ("source") indexados na collection padrão, em ordem
    alfabética — lidos do cache de metadados da collection, sem varrer os chunks."""
    fields, _error = RetrievalService.metadata_fields(DEFAULT_BASE)
    return [src for src in (fields or {}).get("source", []) if isinstance(src, str)]


@app.route('/favicon.ico')
def favicon():
    return send_from_directory('static', 'favicon.ico',
        mimetype='image/vnd.microsoft.icon'
    )


# --- Telas ----------------------------------------------------------------------

@app.route("/")
def index():
    """Orientador: chat pré-configurado, focado na collection padrão
    (DEFAULT_BASE em models/config.py)."""
    return render_template("orientador.html", active="index")


@app.route("/upload")
def upload_page():
    """Envio de PDFs: sobe os arquivos e acompanha a indexação deles."""
    return render_template("upload.html", active="upload_page", max_mb=UPLOAD_MAX_MB)


@app.route("/rag-view")
def rag_view():
    """Consulta bruta às bases, sem LLM — ferramenta de administração, sem
    link no menu lateral."""
    with open("static/rag_view.html", "r", encoding="utf-8") as f:
        return f.read()


# --- Envio e indexação de PDFs ------------------------------------------------------

@app.route("/upload", methods=["POST"])
def upload_files():
    """Recebe os PDFs (campo "files", vários por requisição) e os põe na fila
    de indexação (ver IndexingService)."""
    files = [f for f in request.files.getlist("files") if f and f.filename]
    if not files:
        return jsonify({"ok": False, "error": "Escolha pelo menos um PDF."}), 400
    jobs, rejected = IndexingService.submit(files)
    return jsonify({"ok": True, "jobs": jobs, "rejected": rejected})


@app.route("/upload/jobs")
def upload_jobs():
    """Estado de cada envio desde que o servidor subiu, do mais recente pro mais antigo."""
    return jsonify(IndexingService.list_jobs())


@app.route("/documents")
def documents():
    return jsonify(_indexed_documents())


@app.route("/documents/delete", methods=["POST"])
def documents_delete():
    """Exclui um PDF da base (ver IndexingService.remove_document)."""
    payload = request.get_json(silent=True) or {}
    removed, error = IndexingService.remove_document(payload.get("source"))
    if error:
        return jsonify({"ok": False, "error": error}), 409
    return jsonify({"ok": True, "removed": removed})


@app.errorhandler(413)
def too_large(_error):
    return jsonify({"ok": False, "error": f"Envio maior que o limite de {UPLOAD_MAX_MB} MB. "
                                          "Envie menos arquivos de cada vez."}), 413


# --- Chat e consulta ------------------------------------------------------------------

@app.route("/help-groups")
def help_groups():
    """Documentos selecionáveis no Orientador (rótulo + arquivos que cada um
    cobre): HELP_DOC_GROUPS de models/config.py, se preenchido; senão, um
    item por documento ("source") indexado na collection padrão."""
    if HELP_DOC_GROUPS:
        return jsonify(HELP_DOC_GROUPS)
    return jsonify({
        src: {"label": Path(src).stem.replace("_", " "), "sources": [src], "default_checked": False}
        for src in _indexed_documents()
    })


@app.route("/local-image")
def local_image():
    """Serve imagens locais (páginas/figuras extraídas dos PDFs)"""
    target, forbidden = LocalImageService.resolve(request.args.get("path", ""))
    if forbidden:
        return "", 403
    if not target:
        return "", 404
    return send_file(target)


@app.route("/rag-query")
def rag_query():
    question = request.args.get("question", "").strip()
    collection = request.args.get("collection", DEFAULT_BASE)
    n_results = max(1, min(50, int(request.args.get("n_results") or NRAG_RESULTS)))
    use_keywords = request.args.get("keywords", "false").lower() == "true"
    max_distance = request.args.get("max_distance") or (KEYWORD_MAX_DISTANCE if use_keywords else MAX_DISTANCE)
    where = RetrievalService.build_where(request.args.get("filter_key"), request.args.get("filter_value"))

    if not question:
        return jsonify({"rows": [], "error": "Informe uma pergunta."})

    rows, keywords, error = RetrievalService.retrieve_rows(
        collection, question, n_results, where, max_distance=max_distance, use_keywords=use_keywords
    )
    if error:
        return jsonify({"rows": [], "error": error})

    return jsonify({"rows": rows, "collection": collection, "question": question,
                    "max_distance": max_distance, "keywords": keywords})


@app.route("/collection-metadata")
def collection_metadata():
    """Metadados (chaves + valores distintos) presentes nos chunks de uma
    collection, usados para popular os combos de filtro em /rag-view."""
    collection = request.args.get("collection", "").strip()
    if not collection:
        return jsonify({"fields": {}, "error": "Informe uma collection."})

    fields, error = RetrievalService.metadata_fields(collection)
    return jsonify({"collection": collection, "fields": fields, "error": error})


@app.route("/models")
def models():
    return json.dumps(ChatService.list_models())


@app.route("/collections")
def collections():
    return json.dumps(RetrievalService.list_collections())


@app.route("/ask", methods=["GET", "POST"])
def ask():
    params = _parse_ask_request()
    cancel_event = ChatService.register(params["request_id"])

    def generate():
        for name, payload in ChatService.stream_answer(cancel_event=cancel_event, **params):
            yield _format_sse_event(name, payload)

    return Response(
        stream_with_context(generate()),
        mimetype="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.route("/ask/cancel", methods=["POST"])
def ask_cancel():
    payload = request.get_json(silent=True) or {}
    if not ChatService.cancel(payload.get("request_id")):
        return jsonify({"ok": False, "error": "Requisição não encontrada ou já concluída."})
    return jsonify({"ok": True})


@app.route("/ask/forget-turn", methods=["POST"])
def ask_forget_turn():
    """Apaga um turno da memória da conversa (ver ConversationService.forget_turn)."""
    payload = request.get_json(silent=True) or {}
    ConversationService.forget_turn(payload.get("conversation_id"), payload.get("question"))
    return jsonify({"ok": True})


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5001, debug=True, threaded=True)
