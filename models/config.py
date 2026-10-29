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
    "LOCAL_IMAGE_ROOTS", "LOCAL_IMAGE_EXTENSIONS",
    "HELP_DOC_GROUPS", "HELP_MEMORY_MAX_TURNS",
    "UPLOAD_DIR", "UPLOAD_MAX_MB", "NAV_ITEMS",
    "CHROMA_HOST", "CHROMA_PORT", "AUTH_USER", "AUTH_PASSWORD",
]

# --- Servidor do ChromaDB ------------------------------------------------------
# Localmente, o `chroma run` da documentação; no deploy (fly.io), o que as
# variáveis de ambiente apontarem.
CHROMA_HOST = os.environ.get("CHROMA_HOST", "localhost")
CHROMA_PORT = int(os.environ.get("CHROMA_PORT", "8000"))

# --- Acesso -------------------------------------------------------------------
# Com AUTH_PASSWORD definido (deploy online, via `fly secrets set`), todas as
# rotas exigem autenticação HTTP básica com AUTH_USER/AUTH_PASSWORD; sem ele
# (uso local), o acesso é livre. O endereço do Ollama vem de OLLAMA_HOST, lido
# direto pela biblioteca `ollama`.
AUTH_USER = os.environ.get("AUTH_USER", "advisor")
AUTH_PASSWORD = os.environ.get("AUTH_PASSWORD", "")

# --- Modelo / base padrão ---------------------------------------------------
# qwen3:14b em vez de qwen3:8b: qualidade de resposta melhor observada no uso
# real. A variável de ambiente DEFAULT_MODEL troca o padrão (o deploy em CPU,
# fly.toml, usa o qwen3:8b, mais rápido sem GPU).
DEFAULT_MODEL = os.environ.get("DEFAULT_MODEL", "qwen3:8b")
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
# Diretórios onde imagens locais (figuras extraídas dos PDFs) podem residir.
LOCAL_IMAGE_ROOTS = [
    Path("./.chromadb/figures"),
]
LOCAL_IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"}

# --- Orientador (root, página inicial) --------------------------------------
# Documentos selecionáveis no combo do Orientador. Cada chave vira um filtro
# de metadado "source" ($in); `default_checked` decide a seleção inicial.
# Vazio (padrão) = o combo lista sozinho todos os documentos (valores de
# "source") da collection DEFAULT_BASE, um por item, sem precisar editar nada
# a cada PDF novo indexado. Preencha só para agrupar/renomear documentos, ex.:
#   HELP_DOC_GROUPS = {
#       "livro_x": {"label": "Livro X", "sources": ["Livro_X.pdf"], "default_checked": True},
#       "artigos": {"label": "Artigos", "sources": ["artigo1.pdf", "artigo2.pdf"]},
#   }
HELP_DOC_GROUPS = {}

# Teto de turnos guardados por ConversationService, pra não estourar o
# num_ctx do modelo (16k tokens) somado ao CONTEXTO do turno atual.
HELP_MEMORY_MAX_TURNS = 6

# --- Envio de PDFs (/upload) -------------------------------------------------
# Pasta onde os PDFs enviados pela tela /upload são guardados antes de
# indexados (na collection do rag_papers.py, ver IndexingService). Um PDF
# reenviado com o mesmo nome substitui o anterior, na pasta e na base.
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
