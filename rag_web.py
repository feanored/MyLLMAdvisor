"""Controller: rotas Flask do sistema RAG web.

Lógica de negócio mora em `factories/` e `services/`; configuração em
`models/`. Aqui só request/response HTTP, formatação SSE e qual template
renderizar (`templates/`, todos herdando o layout de `templates/base.html`).
"""
from flask import (Flask, Response, g, jsonify, redirect, render_template, request, send_file,
                   send_from_directory, stream_with_context, url_for)
import json
import mimetypes
import queue
import threading
from pathlib import Path
from urllib.parse import urlsplit

import psycopg
from werkzeug.middleware.proxy_fix import ProxyFix

from models import (
    DEFAULT_MODEL,
    HELP_DOC_GROUPS,
    INVITE_CODE,
    KEYWORD_MAX_DISTANCE,
    KEYWORD_SEARCH,
    MAX_DISTANCE,
    NAV_ITEMS,
    NRAG_RESULTS,
    SESSION_COOKIE,
    SESSION_DAYS,
    UPLOAD_MAX_MB,
)
from services import (AuthService, ChatService, ConversationService, IndexingService, LocalImageService,
                      RetrievalService, UserStorage)

# O front-end usa módulos ES (static/js/): o navegador recusa módulo servido
# com MIME errado, e no Windows o mimetypes lê o tipo do .js do registro
# (às vezes "text/plain").
mimetypes.add_type("text/javascript", ".js")

app = Flask(__name__)
# jsonify ordena as chaves alfabeticamente por padrão — quebraria a ordem de
# exibição dos documentos em /help-groups (ver models/config.py).
app.json.sort_keys = False
app.config["MAX_CONTENT_LENGTH"] = UPLOAD_MAX_MB * 1024 * 1024
# No fly.io o HTTPS termina no proxy, que avisa pelo X-Forwarded-Proto: sem
# isso, request.is_secure seria sempre falso e o cookie de sessão iria sem Secure.
app.wsgi_app = ProxyFix(app.wsgi_app, x_proto=1)

# Abertas sem login: o próprio login, o cadastro e os arquivos estáticos.
_PUBLIC_ENDPOINTS = {"static", "favicon", "auth_login", "auth_register"}
# Telas: sem login, abrem só com o popup de login por cima (ver base.html).
_PAGE_ENDPOINTS = {"index", "upload_page", "account_page"}


@app.context_processor
def _layout_context():
    """Disponível em todo template: os links do menu lateral (NAV_ITEMS), o
    usuário logado (ou None) e se o cadastro com convite está ligado."""
    return {"nav_items": NAV_ITEMS, "current_user": g.get("user"), "invite_enabled": bool(INVITE_CODE)}


def _same_origin():
    """Requisição que muda algo (POST) feita por uma página deste mesmo site?
    O navegador sempre manda Origin num POST vindo de outro site; sem ele, é
    da mesma origem (ou não veio de um navegador). Junto com o cookie
    SameSite=Lax, impede que outro site faça ações em nome de quem está logado."""
    origin = request.headers.get("Origin")
    return not origin or urlsplit(origin).netloc == request.host


@app.before_request
def _load_user():
    """Toda rota, menos as de _PUBLIC_ENDPOINTS, exige login (ver
    AuthService). Sem login, as telas abrem com o popup de login e o resto
    responde 401, que o front-end trata recarregando a página (api.js)."""
    g.user = None
    if request.endpoint == "static":
        return None
    if request.method not in ("GET", "HEAD", "OPTIONS") and not _same_origin():
        return jsonify({"ok": False, "error": "Origem da requisição não permitida."}), 403
    g.user = AuthService.user_for_session(request.cookies.get(SESSION_COOKIE))
    if g.user or request.endpoint in _PUBLIC_ENDPOINTS | _PAGE_ENDPOINTS:
        return None
    if request.endpoint == "rag_view":
        return redirect(url_for("index"))
    return jsonify({"ok": False, "error": "Faça login para continuar."}), 401


@app.errorhandler(psycopg.Error)
def database_unavailable(error):
    """Banco fora do ar (ou sem as tabelas): avisa em vez de um erro 500 cru."""
    app.logger.error("Erro no PostgreSQL: %s", error)
    message = "Banco de dados indisponível. Tente de novo em instantes."
    if request.endpoint in _PAGE_ENDPOINTS:
        return Response(message, 503, mimetype="text/plain")
    return jsonify({"ok": False, "error": message}), 503


def _client_ip():
    """IP de quem fez a requisição (no fly.io, o proxy o informa em Fly-Client-IP)."""
    return request.headers.get("Fly-Client-IP") or request.remote_addr or ""


def _logged_in(user):
    """Abre a sessão de `user` e devolve a resposta com o cookie dela:
    HttpOnly (o JavaScript não lê), SameSite=Lax (outro site não o usa) e,
    com HTTPS, Secure."""
    token = AuthService.create_session(user["id"])
    response = jsonify({"ok": True, "email": user["email"]})
    response.set_cookie(SESSION_COOKIE, token, max_age=SESSION_DAYS * 24 * 3600, path="/",
                        httponly=True, secure=request.is_secure, samesite="Lax")
    return response


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


# Em CPU (deploy no fly.io), o modelo pode passar minutos lendo o prompt antes
# do primeiro pedaço da resposta; um stream calado esse tempo todo pode ser
# derrubado por um proxy no caminho. Um comentário SSE de tempos em tempos
# mantém a conexão viva — o front-end ignora linhas sem "data:" (sse.js).
SSE_KEEPALIVE_SECONDS = 15
_SSE_KEEPALIVE = ": keep-alive\n\n"


def _with_keepalive(events, interval=SSE_KEEPALIVE_SECONDS):
    """Repassa os eventos de `events` (consumido numa thread própria) e, a
    cada `interval` segundos sem nenhum, produz None no lugar."""
    pending = queue.Queue()
    end = object()

    def pump():
        try:
            for item in events:
                pending.put(item)
        finally:
            pending.put(end)

    threading.Thread(target=pump, name="sse-pump", daemon=True).start()
    while True:
        try:
            item = pending.get(timeout=interval)
        except queue.Empty:
            yield None
            continue
        if item is end:
            return
        yield item


def _parse_ask_request():
    """Lê os parâmetros de /ask tanto de POST (JSON) quanto de GET
    (querystring), com a mesma interface, prontos para ChatService.stream_answer.
    A collection não vem do pedido: é sempre a base do usuário logado."""
    if request.method == "POST":
        get = (request.get_json(silent=True) or {}).get
    else:
        get = request.args.get

    return {
        "question": str(get("question", "")).strip(),
        "model": get("model", DEFAULT_MODEL),
        "collection": _user_collection(),
        "think": _bool_param(get, "think", "false"),
        "contexto": _bool_param(get, "context", "false"),
        "palavras_chave": _bool_param(get, "keywords", KEYWORD_SEARCH),
        "max_distance": get("max_distance"),
        "request_id": get("request_id"),
        "where": RetrievalService.build_where(get("filter_key"), get("filter_value")),
        "help_mode": _bool_param(get, "help_mode", "false"),
        "conversation_id": get("conversation_id"),
        "turn_id": get("turn_id"),
        # Decidido pelo front-end (ver is_first_turn em ChatService.stream_answer).
        "is_first_turn": _bool_param(get, "is_first_turn", "true"),
    }


def _user_collection():
    """A base (collection do Chroma) do usuário logado: toda consulta, lista
    e exclusão de documentos usa só ela, nunca um nome vindo do navegador
    (ver UserStorage)."""
    return UserStorage.collection(g.user["id"])


def _indexed_documents():
    """Nomes dos PDFs ("source") indexados na base do usuário, em ordem
    alfabética — lidos do cache de metadados da collection, sem varrer os chunks."""
    fields, _error = RetrievalService.metadata_fields(_user_collection())
    return [src for src in (fields or {}).get("source", []) if isinstance(src, str)]


@app.route('/favicon.ico')
def favicon():
    return send_from_directory('static', 'favicon.ico',
        mimetype='image/vnd.microsoft.icon'
    )


# --- Telas ----------------------------------------------------------------------

@app.route("/")
def index():
    """Orientador: chat pré-configurado, focado na base de documentos do usuário."""
    return render_template("orientador.html", active="index")


@app.route("/upload")
def upload_page():
    """Envio de PDFs: sobe os arquivos e acompanha a indexação deles."""
    return render_template("upload.html", active="upload_page", max_mb=UPLOAD_MAX_MB)


@app.route("/account")
def account_page():
    """Minha conta: e-mail, data de criação e troca de senha."""
    return render_template("account.html", active="account_page")


@app.route("/rag-view")
def rag_view():
    """Consulta bruta às bases, sem LLM — ferramenta de administração, sem
    link no menu lateral."""
    with open("static/rag_view.html", "r", encoding="utf-8") as f:
        return f.read()


# --- Contas e login -------------------------------------------------------------------

@app.route("/auth/login", methods=["POST"])
def auth_login():
    payload = request.get_json(silent=True) or {}
    user, error = AuthService.authenticate(payload.get("email"), payload.get("password"), _client_ip())
    if error:
        return jsonify({"ok": False, "error": error}), 401
    return _logged_in(user)


@app.route("/auth/register", methods=["POST"])
def auth_register():
    """Cria a conta (só com o código de convite, INVITE_CODE) e já entra nela."""
    payload = request.get_json(silent=True) or {}
    user, error = AuthService.register(payload.get("email"), payload.get("password"),
                                       payload.get("invite_code"), _client_ip())
    if error:
        return jsonify({"ok": False, "error": error}), 400
    return _logged_in(user)


@app.route("/auth/logout", methods=["POST"])
def auth_logout():
    AuthService.delete_session(request.cookies.get(SESSION_COOKIE))
    response = jsonify({"ok": True})
    response.delete_cookie(SESSION_COOKIE, path="/")
    return response


@app.route("/auth/password", methods=["POST"])
def auth_password():
    """Troca a senha; os outros navegadores logados nesta conta saem."""
    payload = request.get_json(silent=True) or {}
    error = AuthService.change_password(g.user["id"], payload.get("current_password"),
                                        payload.get("new_password"), request.cookies.get(SESSION_COOKIE))
    if error:
        return jsonify({"ok": False, "error": error}), 400
    return jsonify({"ok": True})


# --- Conversas do Orientador ------------------------------------------------------------

@app.route("/conversations")
def conversations_list():
    """Conversas do usuário logado (id, título), da mais recente para a mais antiga."""
    return jsonify(ConversationService.list_conversations(g.user["id"]))


@app.route("/conversations/export")
def conversations_export():
    """Todas as conversas do usuário com os turnos, para o CSV do "Exportar"."""
    return jsonify(ConversationService.export(g.user["id"]))


@app.route("/conversations/import", methods=["POST"])
def conversations_import():
    payload = request.get_json(silent=True) or {}
    imported = ConversationService.import_conversations(g.user["id"], payload.get("conversations"))
    return jsonify({"ok": True, "imported": imported})


@app.route("/conversations/delete", methods=["POST"])
def conversations_delete():
    payload = request.get_json(silent=True) or {}
    if not ConversationService.delete(g.user["id"], payload.get("id")):
        return jsonify({"ok": False, "error": "Conversa não encontrada."}), 404
    return jsonify({"ok": True})


@app.route("/conversations/<conversation_id>")
def conversation_detail(conversation_id):
    """Uma conversa com todas as perguntas e respostas, para reabri-la."""
    conversation = ConversationService.get(g.user["id"], conversation_id)
    if not conversation:
        return jsonify({"ok": False, "error": "Conversa não encontrada."}), 404
    return jsonify(conversation)


# --- Envio e indexação de PDFs ------------------------------------------------------

@app.route("/upload", methods=["POST"])
def upload_files():
    """Recebe os PDFs (campo "files", vários por requisição) e os põe na fila
    de indexação da base do usuário (ver IndexingService)."""
    files = [f for f in request.files.getlist("files") if f and f.filename]
    if not files:
        return jsonify({"ok": False, "error": "Escolha pelo menos um PDF."}), 400
    jobs, rejected = IndexingService.submit(g.user["id"], files)
    return jsonify({"ok": True, "jobs": jobs, "rejected": rejected})


@app.route("/upload/jobs")
def upload_jobs():
    """Envios do usuário desde que o servidor subiu, do mais recente pro mais antigo."""
    return jsonify(IndexingService.list_jobs(g.user["id"]))


@app.route("/documents")
def documents():
    return jsonify(_indexed_documents())


@app.route("/documents/delete", methods=["POST"])
def documents_delete():
    """Exclui um PDF da base do usuário (ver IndexingService.remove_document)."""
    payload = request.get_json(silent=True) or {}
    removed, error = IndexingService.remove_document(g.user["id"], payload.get("source"))
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
    cobre): HELP_DOC_GROUPS de models/config.py, se preenchido (só os grupos
    com PDFs que o usuário tem); senão, um item por documento ("source") da
    base do usuário."""
    documents = _indexed_documents()
    if HELP_DOC_GROUPS:
        groups = {}
        for key, group in HELP_DOC_GROUPS.items():
            sources = [src for src in group["sources"] if src in documents]
            if sources:
                groups[key] = {**group, "sources": sources}
        return jsonify(groups)
    return jsonify({
        src: {"label": Path(src).stem.replace("_", " "), "sources": [src], "default_checked": False}
        for src in documents
    })


@app.route("/local-image")
def local_image():
    """Serve as figuras extraídas dos PDFs do usuário (ver LocalImageService)."""
    target = LocalImageService.resolve(request.args.get("path", ""), g.user["id"])
    if not target:
        return "", 404
    return send_file(target)


@app.route("/rag-query")
def rag_query():
    question = request.args.get("question", "").strip()
    collection = _user_collection()
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
    """Metadados (chaves + valores distintos) presentes nos chunks da base do
    usuário, usados para popular os combos de filtro em /rag-view."""
    collection = _user_collection()
    fields, error = RetrievalService.metadata_fields(collection)
    return jsonify({"collection": collection, "fields": fields, "error": error})


@app.route("/models")
def models():
    return json.dumps(ChatService.list_models())


@app.route("/collections")
def collections():
    """As bases que o usuário pode consultar no /rag-view: só a dele."""
    return json.dumps([_user_collection()])


@app.route("/ask", methods=["GET", "POST"])
def ask():
    params = _parse_ask_request()
    cancel_event = ChatService.register(params["request_id"])
    # Lido aqui, fora do generator: a resposta é transmitida depois que o
    # contexto da requisição (onde g.user mora) já foi desfeito.
    user_id = g.user["id"]

    def generate():
        try:
            events = ChatService.stream_answer(cancel_event=cancel_event, user_id=user_id, **params)
            for event in _with_keepalive(events):
                yield _SSE_KEEPALIVE if event is None else _format_sse_event(*event)
        finally:
            # Navegador fechou a conexão: a thread do stream_answer não
            # percebe sozinha, então para o modelo como num /ask/cancel.
            cancel_event.set()

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
    """Apaga um par pergunta/resposta da conversa (ver ConversationService.delete_turn)."""
    payload = request.get_json(silent=True) or {}
    ConversationService.delete_turn(g.user["id"], payload.get("turn_id"))
    return jsonify({"ok": True})


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5001, debug=True, threaded=True)
