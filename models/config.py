"""Model: configuração e constantes do sistema RAG web (`rag_web.py`).

Nenhuma lógica de negócio mora aqui — só os valores que as factories e os
services (ver `factories/`, `services/`) usam para decidir como se comportar.
Centralizar tudo num único módulo facilita achar e ajustar qualquer
parâmetro sem precisar navegar pelas camadas de negócio.
"""
import os
from pathlib import Path

# METADATA_FIELDS_EXCLUDED e as demais utilidades de metadata_fields.py são
# reexportadas por `models/__init__.py` direto do módulo irmão, não daqui.

__all__ = [
    "DEFAULT_MODEL", "DEFAULT_BASE", "ALLOWED_CHAT_MODELS",
    "NRAG_RESULTS", "MAX_DISTANCE", "DEBUG", "STOPWORDS",
    "KEYWORD_SEARCH", "KEYWORD_MAX_TERMS", "KEYWORD_MIN_LEN",
    "KEYWORD_MAX_DISTANCE", "KEYWORD_MIN_PER_TERM", "KEYWORD_PHRASE_LABEL",
    "FIGURES_DIR", "LOCAL_IMAGE_EXTENSIONS",
    "HELP_DOC_GROUPS", "HELP_MEMORY_MAX_TURNS",
    "UPLOAD_DIR", "UPLOAD_MAX_MB", "NAV_ITEMS",
    "CHROMA_HOST", "CHROMA_PORT",
    "DATABASE_URL", "DB_HOST", "DB_PORT", "DB_NAME", "DB_USER", "DB_PASSWORD", "DB_POOL_MAX_SIZE",
    "SESSION_COOKIE", "SESSION_DAYS", "PASSWORD_MIN_LEN", "PASSWORD_MAX_LEN", "INVITE_CODE",
    "LOGIN_MAX_FAILURES", "LOGIN_LOCKOUT_MINUTES", "CONVERSATION_TITLE_MAX_LEN",
]

# --- Servidor do ChromaDB ------------------------------------------------------
# Localmente, o `chroma run` da documentação; no deploy (fly.io), o que as
# variáveis de ambiente apontarem.
CHROMA_HOST = os.environ.get("CHROMA_HOST", "localhost")
CHROMA_PORT = int(os.environ.get("CHROMA_PORT", "8000"))

# O endereço do Ollama vem de OLLAMA_HOST, lido direto pela biblioteca `ollama`.

# --- Banco de dados (PostgreSQL) -----------------------------------------------
# Contas, sessões de login e conversas (ver db/ e PostgresPoolFactory). O
# padrão é um servidor local criado com db/01_create_database.sql; cada valor
# pode vir de uma variável de ambiente de mesmo nome. DATABASE_URL, se
# definida (ex.: postgresql://usuario:senha@host:5432/banco), vale no lugar de
# todos os DB_*. DB_HOST também aceita a pasta do socket Unix (ex.:
# /run/postgresql, como no fly.io, onde o Postgres roda na mesma máquina).
DATABASE_URL = os.environ.get("DATABASE_URL", "")
DB_HOST = os.environ.get("DB_HOST", "localhost")
DB_PORT = int(os.environ.get("DB_PORT", "5432"))
DB_NAME = os.environ.get("DB_NAME", "myllmadvisor")
DB_USER = os.environ.get("DB_USER", "advisor")
DB_PASSWORD = os.environ.get("DB_PASSWORD", "advisor")
DB_POOL_MAX_SIZE = 10  # conexões abertas ao mesmo tempo (o gunicorn tem 16 threads)

# --- Contas e login --------------------------------------------------------------
# Toda rota, menos o login, exige uma conta (ver AuthService e _load_user em
# rag_web.py). A sessão é um cookie HttpOnly com um token aleatório; o banco
# guarda só o hash dele (tabela sessions).
SESSION_COOKIE = "advisor_session"
SESSION_DAYS = 30            # validade de um login; depois, pede a senha de novo
PASSWORD_MIN_LEN = 10
PASSWORD_MAX_LEN = 256       # teto: o hash de uma "senha" de megabytes travaria o servidor
# Código de convite para criar conta pela tela de login. Vazio (padrão) =
# ninguém cria conta por lá, só pelo manage_users.py. Online, defina com
# `fly secrets set INVITE_CODE=...`: quem tem conta usa a GPU do Ollama.
INVITE_CODE = os.environ.get("INVITE_CODE", "")
# Contra adivinhação de senha (e de código de convite): depois de
# LOGIN_MAX_FAILURES erros em LOGIN_LOCKOUT_MINUTES, o mesmo e-mail (ou o mesmo
# IP, com o triplo de tentativas) fica bloqueado até a janela passar.
LOGIN_MAX_FAILURES = 5
LOGIN_LOCKOUT_MINUTES = 15

# --- Modelo / base padrão ---------------------------------------------------
# qwen3:8b, local e online (mais rápido, e o único viável em CPU no fly.io).
# A variável de ambiente DEFAULT_MODEL troca o padrão (ex.: qwen3:14b, com
# qualidade de resposta melhor numa GPU local).
DEFAULT_MODEL = os.environ.get("DEFAULT_MODEL", "qwen3:8b")
# Cada usuário tem a sua base: a collection DEFAULT_BASE + "_u" + id da conta
# (ex.: rag_documentos_u3, ver UserStorage). A DEFAULT_BASE em si é a do
# `rag_papers.py index` de linha de comando, que nenhuma conta enxerga até
# ser passada a uma (manage_users.py adopt-documents).
DEFAULT_BASE = "rag_documentos"
# Modelos de chat que o popup "Trocar modelo" (Ctrl+clique em "Perguntar")
# pode oferecer, filtrados a partir de `ollama list` — ver ChatService.list_models.
ALLOWED_CHAT_MODELS = ["qwen3:8b", "qwen3:14b", "qwen3.5:latest"]

# --- Recuperação (RAG) -------------------------------------------------------
NRAG_RESULTS = 10    # número de trechos recuperados do RAG
MAX_DISTANCE = 0.75  # distância máxima para filtrar RAGs (angle~75deg)
DEBUG = True         # True: trechos recuperados no console do navegador

# METADATA_FIELDS_EXCLUDED: ver metadata_fields.py (compartilhado com os
# indexadores, que gravam o cache de valores distintos de metadata usado por
# RetrievalService.metadata_fields).

# --- Busca por palavras-chave ------------------------------------------------
# Em vez da frase inteira como um único vetor (o "ruído" da pergunta dilui o
# assunto), cada palavra vira uma consulta independente ao RAG, e a frase
# ORIGINAL entra como mais uma, lado a lado (ver _search_by_keywords em
# retrieval_service.py).
KEYWORD_SEARCH = True   # padrão do /ask (o Orientador não manda o parâmetro)
KEYWORD_MAX_TERMS = 12  # teto de palavras-chave consultadas por pergunta
KEYWORD_MIN_LEN = 3     # tamanho mínimo de uma palavra-chave (em letras)
# Uma palavra isolada nunca fica tão próxima de um parágrafo quanto uma frase
# inteira fica: o corte de MAX_DISTANCE, calibrado para a pergunta completa,
# descartaria quase tudo no modo palavra-chave. Daí um corte próprio, mais
# frouxo (a seleção fina já é feita pelo ranking por menor distância).
KEYWORD_MAX_DISTANCE = 0.95
# Distâncias de termos diferentes não estão na mesma escala: sem vaga
# garantida, uma palavra genérica ocuparia quase todo o contexto, expulsando
# os termos mais específicos (ver retrieval_service.py).
KEYWORD_MIN_PER_TERM = 1  # 0 = ranking global puro, sem vaga reservada
KEYWORD_PHRASE_LABEL = "(frase completa)"

# Palavras vazias: artigos, preposições, pronomes, conectivos, interrogativos
# ("qual", "como", "onde"...) e verbos de comando ("explique", "descreva"...).
# São justamente as que aparecem em qualquer pergunta e não dizem nada sobre o
# assunto — consultar o RAG com elas só traria chunks aleatórios. A comparação
# é feita sem acentos, então basta listar uma das grafias (ver KeywordService).
STOPWORDS = set("""
a à às ao aos alguma algumas algum alguns ambos ante antes apenas apos após
aquela aquelas aquele aqueles aquilo as até bem cada com como contra da das de
dela delas dele deles demais depois desde dessa dessas desse desses desta destas
deste destes do dos e é ela elas ele eles em entre era eram essa essas esse esses
esta está estas este estes eu foi fomos for foram fosse há isso isto já lhe lhes
mais mas me mesma mesmo meu meus minha minhas muita muitas muito muitos na
nas nem no nos nós nossa nossas nosso nossos num numa nunca o os ou outra outras
outro outros para pela pelas pelo pelos por porque porquê pois pra qual quais
qualquer quando quanta quantas quanto quantos que quem se sem sendo ser será seu
seus só sob sobre sua suas tal também te tem têm tenha ter teu teus toda todas
todo todos tua tuas um uma umas uns vez você vocês onde aqui está seja ser
são sou somos estou estamos estão estava estavam seria seriam havia tinha tinham
fazer faz feito usar usado usada usados usadas utilizar utilizado utilizada
diga dizer explique explicar explica descreva descrever descreve fale falar
mostre mostrar mostra liste listar lista resuma resumir resume informe informar
cite citar comente comentar apresente apresentar detalhe detalhar defina definir
compare comparar quero gostaria favor preciso pode poderia existe existem sim nao
and are be by can could do does for from how in into is it its of on or that the no
their there these this to was were what when where which who why with would you yes
""".split())

# --- Imagens locais -----------------------------------------------------------
# Figuras extraídas dos PDFs: uma subpasta por usuário (u<id>, ver
# UserStorage), e o /local-image só serve as da conta logada.
FIGURES_DIR = Path("./.chromadb/figures")
LOCAL_IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"}

# --- Orientador (root, página inicial) --------------------------------------
# Documentos selecionáveis no combo do Orientador. Cada chave vira um filtro
# de metadado "source" ($in); `default_checked` decide a seleção inicial.
# Vazio (padrão) = o combo lista sozinho todos os documentos (valores de
# "source") da base do usuário, um por item, sem precisar editar nada a cada
# PDF novo indexado. Preencha só para agrupar/renomear documentos (vale para
# todas as contas; cada uma só vê os grupos com PDFs que ela tem), ex.:
#   HELP_DOC_GROUPS = {
#       "livro_x": {"label": "Livro X", "sources": ["Livro_X.pdf"], "default_checked": True},
#       "artigos": {"label": "Artigos", "sources": ["artigo1.pdf", "artigo2.pdf"]},
#   }
HELP_DOC_GROUPS = {}

# Teto de turnos que ConversationService devolve como memória da conversa,
# pra não estourar o num_ctx do modelo (16k tokens) somado ao CONTEXTO do
# turno atual. No banco, a conversa guarda todos.
HELP_MEMORY_MAX_TURNS = 6
# Título de uma conversa: a primeira pergunta, truncada neste tamanho.
CONVERSATION_TITLE_MAX_LEN = 60

# --- Envio de PDFs (/upload) -------------------------------------------------
# Pasta onde os PDFs enviados pela tela /upload são guardados antes de
# indexados (ver IndexingService), numa subpasta por usuário (u<id>). Um PDF
# reenviado com o mesmo nome substitui o anterior, na pasta e na base dele.
UPLOAD_DIR = Path("./documentos")
UPLOAD_MAX_MB = 300  # teto por requisição (soma dos arquivos enviados de uma vez)

# --- Menu lateral --------------------------------------------------------------
# Links do rodapé do menu lateral, comum a todas as telas (templates/base.html).
# `endpoint` = nome da função da rota em rag_web.py; `icon` = um dos ícones
# desenhados em templates/base.html (macro nav_icon). Tela nova = uma entrada aqui.
NAV_ITEMS = [
    {"endpoint": "index", "label": "Orientador", "icon": "chat"},
    {"endpoint": "upload_page", "label": "Enviar PDFs", "icon": "upload"},
]
