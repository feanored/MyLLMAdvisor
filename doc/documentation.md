# My LLM Advisor — documentação completa

Referência detalhada do projeto: arquitetura, instalação, cada tela e cada
decisão de implementação. Para a visão geral e o início rápido, veja o
[README](../README.md); para experimentar as telas sem instalar nada, a
[demonstração no GitHub Pages](https://feanored.github.io/MyLLMAdvisor/).

## Índice

- [Estrutura principal](#estrutura-principal)
  - [Organização interna de rag_web.py (MVC + factory + service)](#organização-interna-de-rag_webpy-mvc--factory--service)
  - [Layout comum das telas](#layout-comum-das-telas)
- [Requisitos](#requisitos)
  - [Criar ambiente Python 3.14](#criar-ambiente-python-314)
  - [Modelos do Ollama](#modelos-do-ollama)
- [Subindo o ambiente](#subindo-o-ambiente)
- [Usando rag_papers.py (artigos e livros em PDF)](#usando-rag_paperspy-artigos-e-livros-em-pdf)
  - [Equações em LaTeX](#equações-em-latex)
- [Usando rag_web.py (interface web)](#usando-rag_webpy-interface-web)
  - [/ — Orientador](#--orientador)
  - [/upload — Enviar PDFs](#upload--enviar-pdfs)
  - [Busca por palavras-chave](#busca-por-palavras-chave)
  - [/rag-view — Consulta bruta às bases](#rag-view--consulta-bruta-às-bases)
  - [Modo debug (DEBUG)](#modo-debug-debug)
  - [Endpoints](#endpoints)
- [Demonstração no GitHub Pages](#demonstração-no-github-pages)
- [Convenção de nomes de collections](#convenção-de-nomes-de-collections)
- [Administrar bases](#administrar-bases)

## Estrutura principal

- `rag_papers.py` — indexa **PDFs de artigos científicos e livros** como texto estruturado (Docling), com extração e descrição de figuras e **equações convertidas para LaTeX** (ver "Equações em LaTeX"). Usado pela linha de comando e, por baixo, pela tela de envio de PDFs.
- `rag_web.py` — aplicação Flask (**controller**) com a interface web. Só rotas Flask, formatação de request/response e qual template renderizar — nenhuma regra de negócio.
- `templates/` — as telas (Jinja), todas herdando o mesmo layout (`base.html`: barra superior + menu lateral):
  - `orientador.html` — **Orientador** (raiz `/`): o chat sobre os artigos e livros indexados na collection `rag_documentos`;
  - `upload.html` — **Enviar PDFs** (`/upload`): envio de PDFs pelo navegador e acompanhamento da indexação.
- `static/rag_view.html` — consulta bruta às bases (`/rag-view`, sem LLM): ferramenta de administração, sem link no menu.
- `documentos/` — PDFs enviados pela tela `/upload` (`UPLOAD_DIR`), criada no primeiro envio.
- `.chromadb/` — pasta local do ChromaDB, criada pelos indexadores (collections e figuras extraídas dos PDFs).

### Organização interna de `rag_web.py` (MVC + factory + service)

O servidor web é dividido em camadas, cada uma num pacote/módulo próprio:

```
.
├── rag_web.py            # Controller: só rotas Flask
├── templates/            # View: telas Jinja (base.html = layout comum)
├── models/
│   ├── config.py          # Model: constantes e configuração
│   └── metadata_fields.py # cache de metadata p/ filtros — compartilhado com rag_papers.py
├── factories/
│   ├── chroma_factory.py # ChromaClientFactory, ChromaCollectionFactory
│   └── ollama_factory.py # OllamaClientFactory
└── services/
    ├── keyword_service.py      # KeywordService
    ├── retrieval_service.py    # RetrievalService
    ├── conversation_service.py # ConversationService
    ├── chat_service.py         # ChatService
    ├── image_service.py        # LocalImageService
    └── indexing_service.py     # IndexingService
```

| Camada | Pacote | Responsabilidade |
|---|---|---|
| **Controller** | `rag_web.py` | Rotas Flask: lê request, chama o service certo, formata a resposta (JSON, SSE ou template). Nenhuma regra de negócio. |
| **View** | `templates/` | Marcação das telas; `base.html` tem a barra superior e o menu lateral comuns (ver "Layout comum das telas"). |
| **Model** | `models/` | Constantes e configuração (modelos padrão, limiares de distância, parâmetros da busca por palavras-chave, stopwords, diretórios de imagem e de upload, documentos do Orientador, links do menu). |
| **Factory** | `factories/` | Criação dos objetos complexos: `ChromaClientFactory`/`ChromaCollectionFactory` (client e collection do Chroma, escolhendo a embedding function certa por "dono") e `OllamaClientFactory` (client de chat). |
| **Service** | `services/keyword_service.py` | `KeywordService` — extração de palavras-chave da pergunta (stopwords, fusão de números com a palavra à esquerda). |
| **Service** | `services/retrieval_service.py` | `RetrievalService` — busca vetorial (frase inteira e/ou palavras-chave), montagem dos trechos (rows), metadados de filtro, figuras, equações, listagem de collections. |
| **Service** | `services/conversation_service.py` | `ConversationService` — memória de conversa do Orientador (histórico de pergunta/resposta por `conversation_id`, até `HELP_MEMORY_MAX_TURNS` turnos). |
| **Service** | `services/chat_service.py` | `ChatService` — orquestra uma pergunta: monta o prompt certo por "dono" da collection, conversa com o modelo em streaming (incluindo o histórico da conversa, se houver) e mantém o registro de cancelamento das perguntas em andamento. |
| **Service** | `services/image_service.py` | `LocalImageService` — valida um caminho de imagem local contra os diretórios permitidos antes de servi-lo. |
| **Service** | `services/indexing_service.py` | `IndexingService` — recebe os PDFs da tela `/upload`, grava em `UPLOAD_DIR` e indexa um por vez em segundo plano (ver "`/upload` — Enviar PDFs"). |

Cada pacote reexporta sua API pública no `__init__.py` (ex.: `from services
import ChatService`), então o import não depende de saber em qual arquivo
cada classe mora. O `ChatService.stream_answer` produz eventos de negócio
`(nome, payload)` agnósticos de protocolo; só o controller
(`_format_sse_event` em `rag_web.py`) sabe que a resposta é um stream SSE.

O front-end (`static/`) segue a mesma separação, em módulos ES carregados
direto pelo navegador (sem etapa de build) — os templates só têm marcação e
um `<script type="module">` apontando para o controlador da página:

```
static/
├── layout.css   # barra superior, menu lateral, paleta (variáveis CSS) e botões — comum a todas as telas
├── rag_help.css # Orientador (chat)
├── upload.css   # Enviar PDFs
└── js/
    ├── services/    # sem DOM de página: api.js (todas as chamadas HTTP), sse.js (leitura do stream de /ask),
    │                # timer.js (cronômetros), text.js (escape/imagens locais), download.js (arquivo para baixar)
    ├── components/  # widgets reutilizáveis: select.js, keywords.js, multiselect.js (combo de
    │                # documentos do Orientador), answer.js (markdown/fórmulas/figuras/equações
    │                # da resposta), answer-export.js (HTML da resposta), sidebar.js (menu em tela estreita)
    └── pages/       # controladores: rag-help.js (Orientador), upload.js (Enviar PDFs),
                     # rag-view.js (consulta bruta)
```

### Layout comum das telas

Toda tela estende `templates/base.html`, que desenha a barra superior ("My
LLM Advisor", link para o Orientador) e o menu lateral. O menu tem duas
partes: em cima, o conteúdo da própria tela (bloco `sidebar`: a lista de
conversas no Orientador, os documentos da base no Enviar PDFs); embaixo, fixa,
a navegação entre telas, montada a partir de `NAV_ITEMS` em
`models/config.py`, com o link da tela atual destacado. Para criar uma tela
nova:

1. uma rota em `rag_web.py` que chame `render_template("minha_tela.html", active="nome_da_funcao")`;
2. `templates/minha_tela.html` com `{% extends "base.html" %}` e os blocos
   `title`, `head` (CSS/JS próprios), `sidebar`, `content` e `body_end`;
3. uma entrada `{"endpoint": "nome_da_funcao", "label": "...", "icon": "..."}`
   em `NAV_ITEMS`, se ela deve aparecer no menu (telas de administração,
   como o `/rag-view`, ficam de fora).

**Telas menores.** Até 1100 px de largura o menu lateral só fica mais
estreito (300 px). Até 760 px (celular em retrato, ou paisagem estreita) ele
vira um trilho fino com o botão ☰ em cima e os ícones das telas embaixo; o ☰
abre o menu inteiro como gaveta por cima do conteúdo, que fecha tocando fora,
com Esc, ou ao escolher uma tela, uma conversa ou "Nova conversa"
(`static/js/components/sidebar.js`; numa tela nova, marque com
`data-close-sidebar` o que deve fechar a gaveta ao ser clicado).

**Conteúdo largo na resposta** nunca alarga a página: equação destacada,
figura e tabela mais largas que a resposta ganham cada uma a sua barra de
rolagem horizontal, e fórmula no meio do texto quebra a linha. No celular, a
figura mantém pelo menos 480 px (em vez de encolher até virar miniatura) e as
colunas das tabelas, pelo menos 7rem (`.answer-figure` e `.answer-box table`
em `static/rag_help.css`). O HTML baixado pelo ⬇️ segue as mesmas regras.

Cores, botões e o foco de teclado vêm de `static/layout.css` (variáveis
`--accent`, `--header-bg`, `--sidebar-bg` etc.), então uma tela nova já nasce
com a mesma cara. A paleta é "tinta e papel": barra superior em azul-tinta
(`--header-bg`) com um padrão em traço fino de livros e lápis
(`static/header_pattern.svg`, embutido no CSS), destaques em azul de
caneta-tinteiro (`--accent`) e fundo branco-papel. O favicon ("MLA") está em
`static/favicon.svg`, com `static/favicon.ico` de reserva.

## Requisitos

- Python 3.14 (recomendado) + ambiente virtual.
- [Ollama](https://ollama.com/) rodando localmente (ou acessível na rede), com os modelos necessários já baixados (`ollama pull <modelo>`).
- Nenhuma API paga: indexação, embeddings, OCR de fórmulas, descrição de figuras e chat rodam todos localmente via Ollama.

### Criar ambiente Python 3.14

Windows PowerShell:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install flask chromadb ollama numpy docling
```

Linux/macOS:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
pip install flask chromadb ollama numpy docling
```

O que cada pacote faz no projeto (só os importados diretamente pelo código —
o resto vem como dependência deles):

| Pacote | Usado por | Para quê |
|---|---|---|
| `flask` | `rag_web.py` | servidor web (rotas, SSE, templates Jinja, upload) |
| `chromadb` | todos | banco vetorial (`PersistentClient` no `rag_papers.py` de linha de comando, `HttpClient` no web) |
| `ollama` | todos | embeddings, chat, descrição de figuras e OCR de fórmulas |
| `numpy` | `rag_papers.py` | normalização dos embeddings (distância cosseno) |
| `docling` | `rag_papers.py` (e a tela `/upload`) | conversão dos PDFs, chunking (`HybridChunker`) e detecção de fórmulas; traz junto o `docling-core` e o `pypdfium2` (recorte das fórmulas) |

### Modelos do Ollama

Baixe os modelos antes de indexar ou subir o servidor (o Ollama precisa estar
rodando: `ollama serve`, ou o app em background).

**Obrigatórios:**

```bash
ollama pull bge-m3        # embedding (~1,2 GB)
ollama pull qwen3:14b     # chat do Orientador (~9,3 GB)
ollama pull qwen3.5       # descrição das figuras dos PDFs (~6,6 GB, multimodal)
ollama pull glm-ocr       # equações -> LaTeX (~2,7 GB)
```

**Opcional:**

```bash
ollama pull qwen3:8b      # rag_papers.py --answer e opção mais leve no "Trocar modelo" (~5,2 GB)
```

| Papel | Modelo | Onde se configura | Usado por |
|---|---|---|---|
| Embedding | `bge-m3` | `EMBED_MODEL` (`rag_papers.py`) | indexação e busca do chat |
| Chat (web) | `qwen3:14b` | `DEFAULT_MODEL`; opções do "Trocar modelo" em `ALLOWED_CHAT_MODELS` (`models/config.py`) | Orientador (`/`) |
| Descrição de figuras | `qwen3.5` | `DESCRIBE_MODEL` (`rag_papers.py`) | `rag_papers.py index` e `/upload` |
| Equações → LaTeX | `glm-ocr` | `FORMULA_MODEL` (`rag_papers.py`) | `rag_papers.py index` (`--formulas ollama`, padrão) e `/upload` |
| Resposta no terminal | `qwen3:8b` | `ASK_MODEL` (`rag_papers.py`) | `rag_papers.py query --answer` |

O embedding é sempre o `bge-m3`: ele fica gravado nos metadados de cada
collection na indexação, e a busca precisa usar o mesmo modelo. O popup
"Trocar modelo" do Orientador (Ctrl+clique em "Perguntar") só mostra os
modelos de `ALLOWED_CHAT_MODELS` (`qwen3:8b`, `qwen3:14b`, `qwen3.5`) que
estiverem instalados.

## Subindo o ambiente

Passo a passo juntando as peças das seções acima, na ordem certa:

1. **Ambiente Python** — crie/ative o venv e instale as dependências (ver "Criar ambiente Python 3.14" acima).
2. **Ollama** — deixe o Ollama rodando (`ollama serve`, ou o app já roda em background) e baixe os modelos usados (ver "Modelos do Ollama" acima).
3. **Servidor do ChromaDB** — `rag_web.py` fala com o Chroma por **HTTP** (`ChromaClientFactory` → `chromadb.HttpClient(host="localhost", port=8000)`), não abre `.chromadb` direto. Suba o servidor numa aba/terminal separado (deixe rodando):
   ```bash
   chroma run --path ./.chromadb --port 8000
   ```
   (o executável `chroma` já vem instalado junto com o pacote `chromadb` do venv, em `.venv/Scripts/chroma.exe` no Windows ou `.venv/bin/chroma` no Linux/macOS; a pasta `.chromadb` é criada se não existir.)
4. **`rag_web.py`** — com o servidor do passo 3 já no ar, em outro terminal:
   ```bash
   python rag_web.py
   ```
   Abre em `http://0.0.0.0:5001`.
5. **Indexar os documentos** — pela tela **Enviar PDFs** (`/upload`, link no rodapé do menu lateral), ou pela linha de comando (`python rag_papers.py index <pasta_com_pdfs>`, ver abaixo).

> Ordem importa: `rag_web.py` só enxerga as collections se o `chroma run` do passo 3 estiver de pé **e** apontando pra pasta `.chromadb` certa — servidor apontando pra pasta errada (ou não rodando) faz o Orientador ficar sem documentos e a indexação pelo `/upload` falhar.

> **Não rode `rag_papers.py index` com o servidor do Chroma no ar.** O indexador de linha de comando abre a pasta `.chromadb` direto em disco (`PersistentClient`), e dois processos escrevendo na mesma pasta ao mesmo tempo podem corromper o banco. Com o servidor rodando, use a tela `/upload` (que grava pelo servidor); a linha de comando é para indexar em lote com o servidor parado.

## Usando `rag_papers.py` (artigos e livros em PDF)

```bash
python rag_papers.py index <pasta_com_pdfs>
python rag_papers.py index <pasta_com_pdfs> --incremental
python rag_papers.py query "qual o tema central do artigo?"
python rag_papers.py query "resuma a metodologia" --answer
python rag_papers.py stats
```

Para indexar em lote com o servidor do Chroma **parado** (ver o aviso em
"Subindo o ambiente"); com o servidor no ar, use a tela `/upload`, que roda
exatamente o mesmo código (`build_pipeline` + `index_pdf`).

- Extrai texto com ordem de leitura preservada (títulos, tabelas, equações, legendas) via Docling, chunking alinhado ao tokenizer do BGE-M3.
- A pasta é varrida recursivamente (`*.pdf`); cada arquivo vira um valor do metadado `source`, que é o que aparece nos combos de documentos das telas web.
- Reindexar um PDF (mesmo nome) substitui todos os trechos da versão anterior dele na base.
- Figuras são salvas em `.chromadb/figures/` com página + legenda, e cada uma é sempre descrita por um modelo multimodal (`qwen3.5`) — a descrição vira um chunk pesquisável junto com a imagem.
- Tudo vai para a collection `rag_documentos`, com embedding `bge-m3`.
- `--incremental` pula PDFs já indexados (por hash de conteúdo).
- `--formulas {ollama,docling,none}` escolhe como as equações viram LaTeX (ver abaixo).
- `--kind {text,figure,equation}` filtra a busca em `query`; `--answer` chama `qwen3:8b` local.

### Equações em LaTeX

Em artigos científicos, as equações renderizadas no PDF (quase sempre geradas
a partir de LaTeX) são parte essencial do conteúdo — mas o texto que o PDF
expõe para elas costuma ser ilegível (glifos soltos, índices fora de lugar).
Por isso o `rag_papers.py index` reconstrói o LaTeX de cada fórmula:

1. o Docling detecta as fórmulas da página (itens com label `FORMULA`, tanto
   as destacadas quanto as do meio do texto);
2. cada uma é **recortada direto do PDF** pela caixa detectada (`pypdfium2`,
   que já vem com o Docling; `FORMULA_RENDER_SCALE` = 3 → ~216 dpi, com
   margem `FORMULA_PADDING`) — sem guardar a imagem de todas as páginas em
   memória, o que pesaria em livros grandes;
3. o recorte é transcrito para LaTeX pelo **GLM-OCR** (`FORMULA_MODEL =
   "glm-ocr"`, prompt `Formula Recognition:`) via Ollama local;
4. `clean_latex` normaliza a saída para o MathJax: tira cercas de código e
   delimitadores, troca `equation`/`align`/`gather` pelos equivalentes que
   funcionam dentro de `$$...$$` (`aligned`/`gathered`), remove `\label`/
   `\nonumber` e transforma o número à direita (`\qquad (3.2)`) em
   `\tag{3.2}`; saídas vazias ou degeneradas (> `FORMULA_MAX_CHARS`) são
   descartadas e a fórmula mantém o texto original;
5. o LaTeX substitui o texto da fórmula **antes** do chunking, então os
   chunks de texto já saem com a equação no lugar certo, como `$$...$$`
   (destacada) ou `$...$` (inline) — é o próprio serializer do Docling que
   põe os delimitadores;
6. cada equação **numerada** ganha também um chunk próprio (`kind="equation"`,
   metadado `equation_number`), com o LaTeX e o parágrafo anterior como
   contexto: `[EQUAÇÃO (3.2)] $$... \tag{3.2}$$`.

Por que GLM-OCR: é um modelo de OCR de documentos (zai-org, **licença MIT**,
0,9B de parâmetros, ~1,8–2,7 GB) publicado na biblioteca oficial do Ollama,
com um modo dedicado de reconhecimento de fórmulas que devolve LaTeX e
resultados de ponta em benchmarks de parsing de documentos (OmniDocBench),
pequeno o bastante para rodar em CPU. Alternativas, via `--formulas`:

- `docling` — o enriquecimento de fórmulas do próprio Docling
  (`do_formula_enrichment`, modelo CodeFormula baixado do HuggingFace na 1ª
  execução, fora do Ollama; bem mais lento em CPU). O LaTeX dele passa pelo
  mesmo `clean_latex`/chunk de equação;
- `none` — mantém o texto que o PDF traz.

Trocar `FORMULA_MODEL` por outro modelo de visão do Ollama (ex.: `qwen3.5`,
já usado nas figuras) também funciona, ajustando `FORMULA_PROMPT` para algo
como "Transcreva esta fórmula em LaTeX, sem explicações".

> PDFs indexados antes disso continuam com o texto antigo das fórmulas:
> reindexe-os (sem `--incremental`, que os pularia pelo hash) para ganhar o LaTeX.

Na resposta, as equações são renderizadas pelo **MathJax 4** (saída CHTML,
com MathML para acessibilidade), carregado no Orientador, no `/rag-view` e no
HTML baixado, com os delimitadores `$...$`, `\(...\)` (inline) e `$$...$$`,
`\[...\]` (destacadas). Equação destacada mais larga que a resposta ganha
barra de rolagem própria (`displayOverflow: 'scroll'`) e fórmula no meio do
texto quebra a linha (`linebreaks.inline`), recursos que só existem a partir
do MathJax 4. Ver "Equações citadas na resposta" abaixo.

## Usando `rag_web.py` (interface web)

### `/` — Orientador

O chat, focado na collection `rag_documentos` (`DEFAULT_BASE`), para
conversar sobre os artigos e livros indexados. Sem combo de modelo/base: a
busca na base e a busca por palavras-chave já vêm ligadas — os valores são
os defaults do próprio controller (`_parse_ask_request` em `rag_web.py`; o
front-end só manda `context: true`, o resto fica por conta do default).
Ctrl+clique em "Perguntar" abre um popup pra trocar o modelo só naquela
página.

**Sidebar de conversas salvas.** Botão "Nova conversa" no topo da barra
lateral, com dois ícones discretos ao lado — "⬇" Exportar e "⬆" Importar
(ver abaixo); abaixo deles, a lista de conversas já salvas (título =
primeira pergunta, truncada). Cada conversa é salva no `localStorage` do
navegador como o próprio HTML já renderizado da thread — reabrir uma
conversa salva é só reinjetar esse HTML de volta, mantendo a mesma
`conversation_id` (dá memória ao chat, ver "Memória da conversa" abaixo;
limitação: essa memória real do backend some num restart do servidor, mesmo
com os turnos antigos ainda visíveis na tela). Cada bloco de resposta, menos
o primeiro (que define a conversa), tem um "✕" para excluir só aquele
turno, sem apagar a conversa inteira; a conversa inteira também pode ser
excluída pelo "×" ao lado do título dela na barra lateral. As duas
exclusões pedem confirmação num modal (Cancelar/Excluir, foco em Cancelar;
Esc ou clique fora cancelam — `static/js/components/confirm-dialog.js`,
o mesmo da exclusão de documentos em `/upload`). Enquanto uma
resposta está sendo renderizada, a barra lateral trava: não dá pra
selecionar outra conversa, excluir a conversa ativa, nem clicar em "Nova
conversa"/Exportar/Importar — evita perder ou corromper o turno em
andamento.

**Importar/exportar conversas (CSV).** Como as conversas só vivem no
`localStorage` do navegador (não há conta de usuário nem banco de dados),
"⬇ Exportar" baixa todas as conversas salvas num único arquivo
`myllmadvisor_conversas_AAAA-MM-DD.csv` — uma linha por turno (colunas
`conversation_id,title,turn_index,turn_id,html`), com o HTML do turno
devidamente escapado (aspas duplicadas, campo entre aspas quando tem vírgula
ou quebra de linha, como qualquer CSV bem formado). "⬆ Importar" lê um CSV
nesse mesmo formato (ex.: o de um backup anterior, ou de outro navegador) e
faz *upsert* por `conversation_id`: conversas com o mesmo id são
sobrescritas, as demais são só adicionadas à lista, sem apagar nada que já
existia. Útil pra levar o histórico entre máquinas/navegadores ou pra ter um
backup fora do `localStorage` (que se perde ao limpar os dados do site).

**Combo de documentos.** Ao lado do botão Perguntar, um combo de múltipla
escolha com os documentos da base, servido ao front-end por `/help-groups`:

- por padrão (`HELP_DOC_GROUPS = {}` em `models/config.py`), lista
  automaticamente **um item por PDF indexado** na collection `rag_documentos`
  (valores distintos do metadado `source`, com o nome do arquivo sem extensão
  como rótulo) — indexou um PDF novo, ele já aparece, sem editar nada;
- opcionalmente, `HELP_DOC_GROUPS` pode ser preenchido para agrupar vários
  PDFs sob um rótulo amigável e/ou definir quais vêm marcados de início
  (`default_checked`) — ver o exemplo comentado em `models/config.py`.

Sem nada marcado (e sem nenhum `default_checked`), a busca vale para a
collection inteira; com algo marcado, só o(s) documento(s) marcado(s) (ver
`selectedSources` em `rag-help.js`). Marcar/desmarcar vira um filtro de
metadado `source` ($in) na busca.

**Prompt do Orientador.** O Orientador manda `help_mode: true` em todo
`/ask` — troca o prompt usado pra responder pelo `HELP_PROMPT` (ver
`ChatService._build_prompt` em `services/chat_service.py`): uma explicação
didática, como numa conversa de orientação, do conceito/método/resultado
perguntado, a partir **apenas** dos trechos recuperados, indicando de qual
documento e página vem cada ponto importante e mencionando limitações ou
divergências que os próprios textos trouxerem.

Os PDFs indexados têm figuras indexadas como chunks `[FIGURA]` com legenda
("Figura N.M: ..."); o prompt pede pra citar esse número ao abordar o
conteúdo correspondente, o que aciona a substituição automática por imagem
(`FIGURE_MENTION_RE` em `static/js/components/answer.js`) — assim a resposta mostra a figura certa no
meio do texto, sem depender do modelo citar o caminho do arquivo
corretamente.

**Equações citadas na resposta.** O prompt explica ao modelo que as
equações do CONTEXTO estão em LaTeX (com `\tag{N}` = número no documento) e
pede que ele cite as numeradas como "Equação (N)" e escreva qualquer outra
expressão em LaTeX, copiando do CONTEXTO. O `ChatService` manda ao
front-end o evento `equations` — as equações numeradas presentes nos trechos
recuperados (`RetrievalService.extract_equations`) — e, ao renderizar a
resposta, `answer.js` acrescenta logo depois da 1ª menção a cada uma
("Equação (3.2)", "Eq. 5", `EQUATION_MENTION_RE`) a própria equação,
renderizada a partir do **LaTeX indexado**, não de uma reescrita do modelo
(que pode errar um índice ou um sinal). Se o modelo já escreveu a equação
com o mesmo `\tag{N}`, ela não é repetida. Como nas figuras, uma equação
citada que não veio na busca é resgatada depois da resposta pronta, pelo
metadado `equation_number` (`RetrievalService.backfill_mentioned_equations`).
Um mesmo número em dois documentos: vale o do trecho mais próximo da
pergunta.

**Fórmulas renderizadas uma vez só.** A resposta chega em pedaços, e o
texto é refeito a cada um. Se o MathJax renderizasse tudo de novo a cada
pedaço, cada fórmula voltaria a ser LaTeX cru por um instante: a tela pisca
e o layout pula. Por isso, como as figuras (que reaproveitam o mesmo `<img>`),
`answer.js` renderiza cada fórmula distinta **uma única vez**
(`MathJax.tex2chtmlPromise`) e guarda o resultado; no texto, cada fórmula é
um encaixe (`.math-slot`) que recebe na hora uma cópia do nó já pronto, sem
passar pelo LaTeX cru. Como o HTML do texto não é fatiado, funciona também
com fórmula dentro de negrito, título ou tabela. O encaixe guarda o LaTeX de
origem (`data-source`): ao reabrir uma conversa salva, `restoreMath` as
renderiza de novo (o CSS dos símbolos que o MathJax gera não vai junto no HTML
salvo), e no HTML baixado elas voltam a ser LaTeX, que o MathJax do próprio
arquivo renderiza.

**Resgate de figura citada só em texto.** A busca por similaridade pode
recuperar um trecho de TEXTO que só *menciona* uma figura ("veja a Figura
2.53 para mais detalhes") sem que o chunk da PRÓPRIA figura entre no top N
resultados — o modelo cita o número (seguindo a menção que leu), mas nenhuma
imagem chega ao front-end, porque `extract_figures` só olha os chunks
`kind="figure"` que de fato vieram na busca. Depois da resposta pronta,
`RetrievalService.backfill_mentioned_figures` procura, por conteúdo literal
(sem embedding, `where_document: {"$contains": ...}`), o chunk de figura de
cada número citado que ainda falta, e `ChatService` reenvia o evento
`figures` com a lista completa — o front-end só precisa sobrescrever
`figures` de novo. Uma ressalva honesta: nem toda figura citada existe de
verdade — um modelo pode "continuar a sequência" e inventar um número
plausível; o prompt proíbe isso explicitamente ("NUNCA invente um número de
figura"), mas não há garantia total contra alucinação de um LLM.

**Layout de chat com múltiplos turnos.** O Orientador empilha cada pergunta junto da sua resposta como um "turno" na tela — a mais recente
embaixo —, com o campo de pergunta fixo no rodapé (Enter envia, Shift+Enter
quebra linha) e a conversa rolando por cima, igual a um chat comum. O combo
de documentos fica ao lado do botão Perguntar. Um indicador de carregando
(3 pontinhos) aparece no lugar da resposta assim que a pergunta é enviada e
some sozinho quando o primeiro pedaço da resposta chega.

**Memória da conversa.** O Ollama não guarda histórico entre chamadas — cada
`client.chat()` é stateless, só "lembra" o que vier na lista `messages`
daquela chamada. Por isso o front-end gera um `conversationId` e manda em
todo `/ask`; o backend (`ConversationService`, em
`services/conversation_service.py`) guarda o histórico (só o texto de
pergunta/resposta de cada turno, até `HELP_MEMORY_MAX_TURNS` turnos) e
`ChatService.stream_answer` o inclui como mensagens antes do prompt do turno
atual. Isso é diferente do **CONTEXTO** (os trechos do RAG — ver
`RetrievalService`): o contexto é refeito do zero a cada pergunta nova
(nova busca, novos trechos); só a conversa em si (o texto puro de
pergunta/resposta) é que se acumula — os trechos de um turno antigo não
voltam a entrar no prompt de um turno novo, pra não inflar o histórico com
texto que uma busca nova já recupera de novo se continuar relevante.

Só isso não basta, na prática: uma busca nova a partir de "detalhe o item 2"
pode recuperar trechos de outra seção dos documentos que têm seu próprio
"item 2" (sem relação nenhuma com a numeração da resposta anterior), e só
ter a resposta anterior "em algum lugar lá atrás" na lista de mensagens não
é suficiente para modelos menores: eles tendem a regenerar a resposta
inteira em vez de aprofundar só o ponto pedido. Por isso o texto da última
resposta é colado de novo, explicitamente, bem ao lado da pergunta atual
(ver `HELP_PREVIOUS_ANSWER_BLOCK`/`_build_prompt`). Seguimentos sem nenhum
trecho recuperado ("obrigado", "sim") são enviados ao modelo sem o
`HELP_PROMPT`, como papo comum.

### `/upload` — Enviar PDFs

Tela para subir PDFs pelo navegador e indexá-los na collection
`rag_documentos`, sem linha de comando (link "Enviar PDFs" no rodapé do menu
lateral).

- **Envio**: arraste os PDFs para a área central ou clique nela para
  escolher (vários de uma vez, até `UPLOAD_MAX_MB` = 300 MB por envio). A
  lista dos escolhidos aparece antes do envio, com o tamanho e um "×" para
  tirar algum; "Enviar e indexar" mostra o progresso do upload.
- **Validação** (`IndexingService.submit`): só aceita arquivo terminado em
  `.pdf` **e** cujo conteúdo começa com `%PDF-`; o nome é sanitizado (sem
  caminho, só letras/dígitos/espaço/`-_.()`, acentos preservados) e vira o
  `source` do documento — o nome que aparece no Orientador. Os arquivos ficam
  em `UPLOAD_DIR` (`./documentos`). Um PDF que já está na fila não pode ser
  reenviado até terminar.
- **Indexação em segundo plano**: uma única thread indexa um PDF por vez
  (Docling e os modelos do Ollama são pesados; dois ao mesmo tempo só
  disputariam CPU/GPU), com o mesmo código da linha de comando
  (`rag_papers.build_pipeline` + `rag_papers.index_pdf`), mas gravando pelo
  `HttpClient` do servidor do Chroma — nunca abrindo a pasta `.chromadb`
  direto, que o servidor já tem aberta. O Docling e os modelos são carregados
  no primeiro envio e reaproveitados nos seguintes.
- **Andamento**: a lista "Indexação" mostra cada envio com o estado (na fila,
  indexando, indexado, já estava na base, erro), a etapa atual ("Convertendo
  fórmulas para LaTeX (10/42)", "Descrevendo figuras (3/7)"...) e o tempo
  decorrido, atualizada por polling em `/upload/jobs` enquanto houver algo
  pendente. Esse histórico vive na memória do servidor: some num restart.
- **Reenvio**: o mesmo PDF sem alterações (mesmo nome e mesmo hash) é
  detectado e não é reindexado ("Já estava na base"); com o mesmo nome e
  conteúdo diferente, a versão nova substitui a antiga, na pasta e na base.
  Para reindexar um PDF que não mudou, exclua-o da base (abaixo) e envie de novo.
- **Excluir da base**: cada PDF da lista "Na base" (menu lateral) tem uma
  lixeira (visível ao passar o mouse; sempre visível em tela de toque) que,
  depois de confirmada num modal, tira da base todos os trechos dele (texto,
  figuras e equações) e apaga as imagens das figuras
  (`IndexingService.remove_document`). O arquivo em `UPLOAD_DIR` não é
  apagado. Enquanto houver algum PDF na fila ou indexando (ou um envio
  subindo), todas as lixeiras ficam desativadas e o servidor recusa a
  exclusão: indexação e exclusão nunca gravam na collection ao mesmo tempo
  (`_write_lock`), porque a exclusão no meio de uma indexação falhava.
- **Criação da base**: a collection `rag_documentos` só é criada pelo
  indexador (no primeiro envio), sempre com a embedding `bge-m3`; as telas só
  a abrem (`ChromaCollectionFactory.open` nunca cria). Se ela já existir com
  outra embedding function (ex.: criada vazia, com a padrão do Chroma, por uma
  versão anterior do servidor — o erro "Embedding function conflict"), ela é
  recriada automaticamente se estiver vazia; com dados, a indexação para e
  avisa para apagá-la e reindexar.
- **Menu lateral**: em cima, a lista dos documentos já na base (`/documents`),
  com os recém-indexados destacados; o Orientador passa a oferecê-los no
  combo de documentos ao recarregar a página.

### Busca por palavras-chave

Buscar pela frase inteira mistura, num único vetor de consulta, o assunto da
pergunta e o "ruído" dela (interrogativos, verbos de comando, conectivos) — o
que afasta os trechos realmente relevantes. Com a busca por palavras-chave
ligada (sempre, no Orientador; opcional no `/rag-view`), `rag_web.py`:

1. quebra a pergunta em palavras; um número sozinho ("5", "3.2", "61850") não
   diz nada como consulta ao RAG, então é fundido com a palavra que o precede
   na frase — "Tabela 5", "equação 3.2", "IEC 61850" — formando uma única
   palavra-chave composta (a fusão não ocorre se a palavra à esquerda for ela
   mesma uma stopword, ex. "para 3 motivos" não vira "para 3"); números que
   sobram soltos são descartados, mas siglas com dígito ("Fig.2", "IEC61850")
   continuam valendo;
2. descarta as palavras vazias (`STOPWORDS`) e as com menos de `KEYWORD_MIN_LEN`
   letras, até `KEYWORD_MAX_TERMS` termos;
3. consulta a base **uma vez por palavra (ou composto) E uma vez pela frase
   original, inteira** — tudo numa única chamada ao Chroma —, recuperando
   `NRAG_RESULTS` trechos de cada consulta; a frase inteira entra porque ela
   enxerga relações entre as palavras que a busca por termos soltos não capta
   (e vice-versa), então as duas abordagens rodam sempre juntas, nunca uma
   no lugar da outra;
4. funde os resultados: um trecho que apareceu em várias consultas entra uma
   vez só, com a **menor** distância que obteve;
5. monta o CONTEXTO com os de menor distância, garantindo antes
   `KEYWORD_MIN_PER_TERM` vaga(s) para o melhor trecho de cada termo — cada
   palavra-chave **e também a frase inteira**, no mesmo pé de igualdade — sem
   isso um termo genérico ocupa quase todo o contexto, já que distâncias de
   consultas diferentes não estão na mesma escala;
6. aplica o corte `KEYWORD_MAX_DISTANCE` (mais frouxo que `MAX_DISTANCE`, porque
   uma palavra isolada nunca fica tão próxima de um parágrafo quanto uma frase).

As palavras efetivamente consultadas aparecem abaixo do campo Pergunta e no HTML
baixado; um trecho recuperado pela frase inteira (e não por nenhuma palavra-chave
específica) aparece marcado como "(frase completa)" na coluna/lista de origem. Se
a pergunta não tiver nenhuma palavra-chave aproveitável (ex.: "o que é isso?"), a
frase inteira ainda é consultada normalmente — só não há palavras-chave adicionais
para mostrar.

### `/rag-view` — Consulta bruta às bases

Ferramenta de administração: não tem link no menu lateral (vai para uma
futura tela de administração, quando houver perfis de usuário); acesse
digitando `/rag-view`. Mostra os trechos recuperados (fonte, página/linhas, distância, palavra-chave, texto) sem passar por nenhum LLM — útil para depurar a qualidade da busca vetorial isoladamente. Também renderiza imagens inline quando o texto recuperado contém caminhos locais, e as equações em LaTeX (MathJax) — útil pra conferir a qualidade do OCR das fórmulas. Tem a opção **Busca por palavras-chave** (ver acima); com ela marcada, a coluna **Palavra-chave** mostra a(s) palavra(s) da pergunta cujo `col.query()` trouxe aquele trecho — útil para ver de onde veio (ou por que não veio) cada resultado.

### Modo debug (`DEBUG`)

Setando `DEBUG = True` em `models/config.py` (padrão), cada pergunta feita imprime no **console do navegador** (F12) uma tabela (`console.table`) com os trechos recuperados — uma versão mínima do `/rag-view`, sem precisar abrir outra aba. Com `DEBUG` e `think: true` no `/ask`, o prompt completo enviado ao modelo também volta como evento `thinking`.

### Endpoints

- `/` — Orientador (chat pré-configurado, focado em `rag_documentos`).
- `/upload` (GET) — tela Enviar PDFs.
- `/upload` (POST) — recebe os PDFs (campo `files`, vários) e os põe na fila de indexação; devolve os jobs criados e os rejeitados, com o motivo.
- `/upload/jobs` — estado de cada envio desde que o servidor subiu (mais recente primeiro).
- `/documents` — nomes dos PDFs indexados em `rag_documentos`.
- `/documents/delete` (POST) — exclui um PDF da base (`source`): trechos e imagens das figuras; recusado (409) com indexação em andamento.
- `/help-groups` — os documentos selecionáveis no combo do Orientador (rótulo + arquivo(s) de cada um): `HELP_DOC_GROUPS`, se preenchido, ou um item por PDF indexado em `rag_documentos`.
- `/rag-view` — consulta bruta.
- `/collections` — lista as collections do ChromaDB.
- `/collection-metadata` — chaves de metadata e valores distintos de uma collection, para popular os combos de filtro.
- `/models` — lista os modelos instalados no Ollama.
- `/rag-query` — busca (sem LLM) usada pelo `/rag-view`.
- `/ask` — pergunta + resposta em streaming (SSE) usada pelo Orientador; eventos: `keywords`, `figures`, `equations`, `debug_rows`, `thinking`, `content`, `cancelled`, `done`, `error_msg`. Parâmetro opcional `help_mode`: troca o prompt pelo `HELP_PROMPT` do Orientador (só `/` manda isso). Parâmetro opcional `conversation_id`: dá memória ao chat entre perguntas (ver `ConversationService`; só `/` manda isso). Parâmetro opcional `is_first_turn` (default `true`): decidido pelo front-end, usado pra mostrar as palavras-chave só no 1º turno e detectar seguimento puramente conversacional (só `/` manda `false`).
- `/ask/cancel` — cancela uma pergunta em andamento (por `request_id`).
- `/ask/forget-turn` — apaga um par pergunta/resposta da memória da conversa (`conversation_id`, `question`), chamado ao excluir um bloco de resposta no Orientador.
- `/local-image` — serve imagens locais citadas nas respostas (restrito aos diretórios de figuras conhecidos).

## Demonstração no GitHub Pages

O GitHub Pages só serve arquivos estáticos (não roda Flask, Ollama nem
Chroma), então o demo é uma cópia das telas reais com o backend simulado no
próprio navegador:

- `demo/build_demo.py` renderiza os templates de verdade (`templates/`) em
  HTML, copia o `static/` real e injeta na cópia a API simulada e um aviso de
  "Demonstração" na barra superior. Os caminhos absolutos do app (`/static/`,
  `/upload`) viram relativos, porque o Pages serve o site em `/MyLLMAdvisor/`.
  Precisa só do `flask`. Se um template mudar a ponto de o build não achar
  onde injetar algo, ele para com uma mensagem dizendo o quê.
- `demo/mock-api.js` intercepta `fetch` e `XMLHttpRequest` só no demo (o app
  nunca carrega esse arquivo): o Orientador responde em streaming, no mesmo
  formato SSE do `/ask`, uma pergunta de exemplo sobre transformada de Fourier
  (texto escrito para o demo, com equações citadas e uma figura); o "Enviar
  PDFs" simula o progresso do envio e a fila de indexação etapa por etapa, e
  oferece um PDF de exemplo. Nenhum arquivo sai do navegador.
- `demo/assets/` tem a figura de exemplo; `doc/demo-orientador.png` é o
  screenshot do topo do README.
- `.github/workflows/pages.yml` gera e publica o demo a cada push no `master`.
  Pré-requisito, uma vez: em Settings > Pages > Build and deployment, escolher
  **Source: GitHub Actions**.

Para ver localmente (o servidor precisa servir `.js` como JavaScript, senão o
navegador recusa os módulos):

```bash
python demo/build_demo.py
python -m http.server -d demo/dist 8080   # abre em http://localhost:8080
```

`?auto=1` no endereço envia a pergunta de exemplo (Orientador) ou o PDF de
exemplo (Enviar PDFs) sozinho — usado para gerar os screenshots.

## Convenção de nomes de collections

| Collection | Origem | Embedding |
|---|---|---|
| `rag_documentos` | `rag_papers.py index` (artigos e livros) | bge-m3 |

O `rag_web.py` identifica o "dono" de cada collection pelos metadados gravados nela, não pelo nome (`ChromaCollectionFactory.kind()`): com `embedding_model` no metadata → veio do `rag_papers.py` ("docling", aberta com a embedding `bge-m3`); sem → "generic" (collection avulsa). O Orientador sempre usa o `HELP_PROMPT`; sem `help_mode`, uma collection do `rag_papers.py` usa o `DOC_PROMPT` dele, e uma avulsa, um prompt genérico.

## Administrar bases

- Não apague manualmente arquivos dentro de `.chromadb` sem saber o que está fazendo.
- Para recriar uma base do zero: apague a collection específica (via `chromadb`/`list_collections()`/`delete_collection()`, não a pasta inteira) e reenvie os PDFs pelo `/upload` (ou rode `rag_papers.py index` com o servidor do Chroma parado).
