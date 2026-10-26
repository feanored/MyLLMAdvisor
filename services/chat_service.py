"""Service: orquestra uma pergunta ao chat — recupera contexto, monta o
prompt (por "dono" da collection) e conversa com o modelo em streaming.
Mantém também o registro de cancelamento das perguntas em andamento.

O generator `stream_answer` produz eventos de negócio `(nome, payload)`,
agnósticos do formato de fio SSE — quem serializa pro protocolo é o
controller (`rag_web.py`), que é a camada que sabe o que é HTTP/SSE.
"""
import threading

import ollama

from factories import ChromaCollectionFactory, OllamaClientFactory
from models import ALLOWED_CHAT_MODELS, DEBUG, DEFAULT_MODEL, NRAG_RESULTS

from .conversation_service import ConversationService
from .retrieval_service import RetrievalService

# Prompt do Orientador (help_mode=True): explicação didática, conversacional,
# a partir dos artigos/livros recuperados, no lugar do DOC_PROMPT mais formal
# do modo avançado. Citar "Figura N.M" aciona a substituição automática por
# imagem, e "Equação (N)" a exibição da equação indexada em LaTeX (ver
# FIGURE_MENTION_RE/EQUATION_MENTION_RE em static/js/components/answer.js).
HELP_PROMPT = """\
Você é um orientador científico que ajuda a pessoa a entender o conteúdo de
artigos científicos e livros, a partir dos trechos recuperados abaixo. O
objetivo é que ela COMPREENDA o assunto perguntado — conceitos, métodos,
resultados, argumentos — explicado de forma clara, como numa conversa de
orientação, não uma citação acadêmica formal.

Use APENAS os trechos recuperados como fonte de verdade; não invente dados,
números, fórmulas, autores ou conclusões que não apareçam no CONTEXTO.
{previous_answer_block}
Se a pergunta de agora NÃO for um seguimento da resposta anterior (ou não
houver resposta anterior), estruture a resposta assim:
1. Uma resposta direta à pergunta, em poucas frases.
2. O desenvolvimento: explique o conceito/método/resultado com suas próprias
   palavras a partir do CONTEXTO, mantendo a notação e os termos técnicos do
   texto original. Use listas ou passos numerados só quando o próprio
   assunto for sequencial (ex.: um algoritmo, uma derivação, um protocolo).
3. Indique de qual documento (nome do arquivo e página, vindos do cabeçalho
   de cada trecho do CONTEXTO) vem cada ponto importante.
4. Sempre que um trecho do CONTEXTO tiver uma figura correspondente (marcado
   "[FIGURA] Figura N.M: ..."), cite exatamente "Figura N.M" ao abordar
   aquele conteúdo — a imagem é inserida automaticamente na resposta.
   Prefira sempre as figuras com esse número de legenda; figuras "sem
   legenda" podem ser mencionadas, mas nem sempre a imagem certa é exibida.
   NUNCA invente um número de figura — cite só os que aparecerem
   literalmente no CONTEXTO (seja no próprio marcador "[FIGURA]", seja
   mencionados dentro de um trecho de texto), mesmo que um número
   "seguinte" pareça lógico de citar.
5. As equações aparecem no CONTEXTO em LaTeX, entre $$ ... $$ (destacadas)
   ou $ ... $ (no meio do texto); \\tag{{N}} é o número da equação no
   documento. Ao usar uma equação numerada, cite-a como "Equação (N)" — ela
   é exibida automaticamente, renderizada a partir do LaTeX indexado, então
   não precisa reescrevê-la. Qualquer outra expressão matemática (uma
   equação sem número, um símbolo, uma variável) escreva em LaTeX entre
   $ ... $ ou $$ ... $$, copiando o LaTeX do CONTEXTO sem alterá-lo. NUNCA
   invente um número de equação.
6. Se os próprios textos trouxerem limitações, ressalvas ou divergências
   entre autores sobre o assunto, mencione-as ao final.

Caso o CONTEXTO não cubra o que foi perguntado (e a pergunta não seja um
seguimento de algo que você já respondeu), responda apenas:
Não consegui encontrar a resposta com base no contexto fornecido.

### PERGUNTA
{question}

### CONTEXTO
{context}
"""

# Entra no lugar de {previous_answer_block} só a partir do 2º turno da conversa.
HELP_PREVIOUS_ANSWER_BLOCK = """
### SUA RESPOSTA ANTERIOR NESTA CONVERSA
{previous_answer}

Se a pergunta de agora pedir pra detalhar, explicar melhor, ou continuar
algo da resposta ACIMA (ex.: "detalhe o item 2", "e depois?", "explique
melhor isso"), use-a pra identificar exatamente o que foi pedido e
aprofunde SÓ aquele ponto — NÃO repita a resposta acima inteira de novo. O
CONTEXTO mais abaixo pode trazer trechos de OUTRAS partes dos documentos
que só coincidem no texto (ex.: outra seção que também tem um "item 2");
use-o só se for realmente sobre o mesmo assunto da resposta acima, e
ignore-o se não for.
"""


class ChatService:
    _active_requests = {}  # request_id -> threading.Event, ver register/cancel

    # --- cancelamento ---------------------------------------------------

    @classmethod
    def register(cls, request_id):
        """Registra uma nova pergunta em andamento ANTES de começar a
        transmiti-la (chamado pelo controller, fora do generator de
        streaming), pra que um /ask/cancel disparado logo em seguida sempre
        encontre o request_id já presente — sem depender de o generator já
        ter começado a rodar."""
        event = threading.Event()
        if request_id:
            cls._active_requests[request_id] = event
        return event

    @classmethod
    def cancel(cls, request_id):
        event = cls._active_requests.get(request_id) if request_id else None
        if not event:
            return False
        event.set()
        return True

    @classmethod
    def _unregister(cls, request_id):
        if request_id:
            cls._active_requests.pop(request_id, None)

    # --- listagem de modelos ---------------------------------------------

    @staticmethod
    def list_models():
        try:
            result = ollama.list()
            names = [m.model for m in result.models if m.model in ALLOWED_CHAT_MODELS]
            return sorted(names)
        except Exception:
            return [DEFAULT_MODEL]

    # --- construção do prompt ---------------------------------------------

    @staticmethod
    def _build_prompt(collection, question, rows, help_mode=False, previous_answer=None):
        """Prompt específico do "dono" da collection, ou um genérico de
        fallback. `help_mode=True` usa HELP_PROMPT; `previous_answer` entra
        via HELP_PREVIOUS_ANSWER_BLOCK."""
        if help_mode:
            context = RetrievalService.context_blocks(rows)
            previous_block = (
                HELP_PREVIOUS_ANSWER_BLOCK.format(previous_answer=previous_answer)
                if previous_answer else ""
            )
            return HELP_PROMPT.format(question=question, context=context, previous_answer_block=previous_block)

        kind = ChromaCollectionFactory.kind(collection)

        if kind == "docling":
            from rag_papers import DOC_PROMPT
            context = RetrievalService.context_blocks(rows)
            return DOC_PROMPT.format(question=question, context=context)

        context = "\n".join(f"{row['id']}. [{row['header']}] {row['text']}" for row in rows)
        return (
            "Responda à QUESTÃO DO USUÁRIO a seguir. Utilize o CONTEXTO fornecido abaixo.\n"
            "Caso não encontre a resposta no contexto, responda apenas: "
            'Não consegui encontrar a resposta com base no contexto fornecido.\n\n'
            f"CONTEXTO:\n{context}\n\nQUESTÃO DO USUÁRIO:\n{question}\n\n"
        )

    # --- streaming da resposta ---------------------------------------------

    @classmethod
    def stream_answer(cls, *, question, model, collection, think, contexto,
                       palavras_chave, max_distance, where, request_id, cancel_event,
                       help_mode=False, conversation_id=None, is_first_turn=True):
        """Gera a resposta como uma sequência de eventos de negócio
        `(nome, payload)`, formatados como SSE pelo controller
        (`_format_sse_event` em rag_web.py).

        `conversation_id`: dá memória ao chat (ver ConversationService).
        `is_first_turn`: decidido pelo front-end (se já existe turno na
        tela), não pela memória do backend, que some num restart do
        servidor."""
        try:
            if not collection:
                yield ("error_msg", "Nenhuma collection selecionada.")
                return

            history = ConversationService.history(conversation_id)

            rows, keywords, error = RetrievalService.retrieve_rows(
                collection, question, NRAG_RESULTS, where,
                max_distance=max_distance, use_keywords=palavras_chave,
            )
            if error:
                yield ("error_msg", error)
                return

            # Só anuncia as palavras-chave no primeiro turno da conversa.
            if contexto and keywords and is_first_turn:
                yield ("keywords", keywords)

            figures = RetrievalService.extract_figures(rows)
            if figures:
                yield ("figures", figures)

            equations = RetrievalService.extract_equations(rows)
            if equations:
                yield ("equations", equations)

            if DEBUG:
                debug_rows = [
                    {"id": r["id"], "source": r["source"], "page": r["page"],
                     "distance": r["distance"], "keywords": ", ".join(r["keywords"]),
                     "text": r["text"]}
                    for r in rows
                ]
                yield ("debug_rows", debug_rows)

            previous_answer = next(
                (m["content"] for m in reversed(history) if m["role"] == "assistant"), None
            )

            # Seguimento sem nenhum trecho recuperado: provável só papo
            # ("sim", "obrigado"), não pergunta nova — pula o HELP_PROMPT.
            conversational_follow_up = help_mode and not is_first_turn and not rows

            prompt = question
            if contexto and not conversational_follow_up:
                prompt = cls._build_prompt(collection, question, rows, help_mode, previous_answer)

            # Visualizar o prompt
            if DEBUG and think:
                yield ("thinking", prompt)

            messages = history + [{"role": "user", "content": prompt}]

            client = OllamaClientFactory.create()
            answer_parts = []
            cancelled = False
            for chunk in client.chat(
                model=model,
                messages=messages,
                stream=True,
                think=think,
                options={"temperature": 0.1, "num_ctx": 1024 * 16},
            ):
                if cancel_event.is_set():
                    cancelled = True
                    break
                msg = chunk.message
                if think and getattr(msg, "thinking", None):
                    yield ("thinking", msg.thinking)
                if msg.content:
                    answer_parts.append(msg.content)
                    yield ("content", msg.content)

            if cancelled:
                yield ("cancelled", "ok")
                return

            answer = "".join(answer_parts).strip()
            if conversation_id:
                ConversationService.append_turn(conversation_id, question, answer)

            # Resgata figuras citadas na resposta que não vieram no top N.
            if contexto and answer:
                extra_figures = RetrievalService.backfill_mentioned_figures(collection, answer, figures, where)
                if extra_figures:
                    figures = figures + extra_figures
                    yield ("figures", figures)

                # Idem para equações citadas pelo número.
                extra_equations = RetrievalService.backfill_mentioned_equations(collection, answer, equations, where)
                if extra_equations:
                    equations = equations + extra_equations
                    yield ("equations", equations)

            yield ("done", "ok")

        except Exception as e:
            yield ("error_msg", str(e))
        finally:
            cls._unregister(request_id)
