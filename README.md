# My LLM Advisor — seu orientador científico pessoal

Converse com os seus artigos científicos e livros. Envie os PDFs e o My LLM
Advisor os indexa — texto, figuras e equações, convertidas para LaTeX — e
responde às perguntas citando o documento e a página, com as equações
renderizadas e as figuras no meio da resposta. Tudo roda localmente, com
modelos do [Ollama](https://ollama.com/): nenhuma API paga, e nenhum
documento sai da sua máquina.

**[Ver a demonstração](https://feanored.github.io/MyLLMAdvisor/)** — as telas
reais, com respostas e indexação simuladas no navegador.

[![Orientador respondendo, com equações em LaTeX e figura](doc/demo-orientador.png)](https://feanored.github.io/MyLLMAdvisor/)

## Recursos

- **Orientador** — chat sobre os documentos da base, com memória da conversa,
  filtro por documento e citação de arquivo e página. Conversas guardadas na
  conta de cada usuário, com exportação e importação em CSV.
- **Contas** — login por e-mail e senha (hash argon2id), cadastro só com
  código de convite e tela Minha conta com troca de senha; contas e conversas
  num PostgreSQL. Cada conta tem a sua base de PDFs: ninguém vê, consulta ou
  exclui os documentos de outra.
- **Equações em LaTeX** — cada fórmula do PDF é transcrita para LaTeX pelo
  GLM-OCR e renderizada pelo MathJax; quando a resposta cita "Equação (3.2)",
  a equação indexada aparece logo em seguida.
- **Figuras** — extraídas dos PDFs e descritas por um modelo de visão, entram
  na busca e aparecem na resposta quando citadas.
- **Enviar PDFs** — upload pelo navegador, com a indexação de cada arquivo
  acompanhada etapa por etapa, e exclusão de um PDF da base (para reindexá-lo)
  com confirmação.
- **Busca por palavras-chave e frase inteira** juntas, para o contexto não ser
  dominado por um termo genérico da pergunta.
- **Responsivo** — no celular, o menu lateral vira um trilho de ícones.

## Início rápido

Requisitos: Python 3.14, o [Ollama](https://ollama.com/) rodando e um
PostgreSQL (o jeito mais rápido é o Docker, abaixo).

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1          # Linux/macOS: source .venv/bin/activate
pip install flask chromadb ollama numpy docling "psycopg[binary]" psycopg-pool argon2-cffi

ollama pull bge-m3       # embedding
ollama pull qwen3:8b     # chat
ollama pull qwen3.5      # descrição das figuras
ollama pull glm-ocr      # equações -> LaTeX
```

Banco das contas e conversas, uma vez só (ou com um PostgreSQL já
instalado, ver [Banco local](doc/documentation.md#banco-local)):

```powershell
docker run -d --name advisor-pg -e POSTGRES_PASSWORD=postgres -p 5432:5432 -v advisor-pg:/var/lib/postgresql/data postgres:17
docker cp db advisor-pg:/db
docker exec advisor-pg psql -U postgres -f /db/01_create_database.sql
docker exec advisor-pg psql -U advisor -d myllmadvisor -f /db/02_schema.sql
```

Em dois terminais, com o ambiente ativado:

```bash
chroma run --path ./.chromadb --port 8000   # banco vetorial (deixe rodando)
python rag_web.py                            # abre em http://localhost:5001
```

Crie a sua conta com `python manage_users.py add seu@email.com` (ou defina
`INVITE_CODE` antes de subir o `rag_web.py` e crie pela tela de login).
Abra **Enviar PDFs** no menu lateral, envie seus PDFs e, quando a indexação
terminar, pergunte no **Orientador**. Para indexar uma pasta inteira pela
linha de comando, com o servidor do Chroma parado:
`python rag_papers.py index <pasta_com_pdfs>`; depois, com o Chroma no ar,
`python manage_users.py adopt-documents seu@email.com` passa esses PDFs à
sua conta.

A versão online, com login, fica no [fly.io](https://fly.io/): o site, o
Chroma e o PostgreSQL no Fly (`fly.gpu.toml`), e o Ollama num PC com GPU, publicado pelo
Tailscale Funnel (`deploy/pc-gpu/`). Há também a alternativa de tudo em CPU
numa máquina só do Fly (`fly.toml`). Veja
[Deploy no fly.io](doc/documentation.md#deploy-no-flyio).

## Estrutura

| Caminho | O que é |
|---|---|
| `rag_web.py` | servidor web Flask (rotas) |
| `rag_papers.py` | indexação dos PDFs: Docling, OCR das fórmulas, descrição das figuras |
| `services/`, `factories/`, `models/` | regras de negócio, criação dos clients e configuração (`models/config.py`) |
| `db/`, `manage_users.py` | SQLs do PostgreSQL (contas, sessões e conversas) e administração das contas |
| `templates/`, `static/` | telas (layout comum em `templates/base.html`) e front-end em módulos ES |
| `demo/` | demonstração estática para o GitHub Pages |
| `Dockerfile`, `fly.toml`, `fly.gpu.toml`, `deploy/` | deploy no fly.io (com o Ollama num PC com GPU, ou tudo em CPU) |

## Documentação

A documentação completa está em [doc/documentation.md](doc/documentation.md). Atalhos:

- [Arquitetura](doc/documentation.md#organização-interna-de-rag_webpy-mvc--factory--service) e [layout comum das telas](doc/documentation.md#layout-comum-das-telas) (como criar uma tela nova)
- [Requisitos e modelos do Ollama](doc/documentation.md#requisitos)
- [Subindo o ambiente](doc/documentation.md#subindo-o-ambiente) (e por que não indexar pela linha de comando com o Chroma no ar)
- [Contas de usuário e banco de dados](doc/documentation.md#contas-de-usuário-e-banco-de-dados): login, segurança, tabelas, banco local e `manage_users.py`
- [Equações em LaTeX](doc/documentation.md#equações-em-latex)
- [Orientador](doc/documentation.md#--orientador): prompt, memória da conversa, figuras e equações na resposta
- [Enviar PDFs](doc/documentation.md#upload--enviar-pdfs)
- [Busca por palavras-chave](doc/documentation.md#busca-por-palavras-chave)
- [Endpoints](doc/documentation.md#endpoints)
- [Demonstração no GitHub Pages](doc/documentation.md#demonstração-no-github-pages)

## Licença

[AGPL-3.0](LICENSE).
