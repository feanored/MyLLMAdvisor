"""Service: conversas do Orientador, guardadas por usuário no PostgreSQL
(tabelas conversations e turns, ver db/02_schema.sql).

O Ollama não tem memória entre chamadas: `ChatService.stream_answer` busca o
histórico aqui e reenvia os turnos anteriores (só pergunta/resposta, sem o
CONTEXTO do RAG de cada um) antes da pergunta atual. É ele também quem grava
cada turno: abre ao chegar a pergunta (`start_turn`) e fecha com a resposta
(`finish_turn`), mesmo que o navegador seja fechado no meio.

Toda consulta filtra pelo dono (`user_id`): a conversa de outro usuário é
tratada como inexistente.
"""
import uuid

from psycopg.types.json import Jsonb

from factories import PostgresPoolFactory
from models import CONVERSATION_TITLE_MAX_LEN, HELP_MEMORY_MAX_TURNS

TURN_STATUSES = ("pending", "done", "cancelled", "error")


class ConversationService:

    @staticmethod
    def parse_id(value):
        """O id (UUID) normalizado, ou None se `value` não for um UUID."""
        try:
            return str(uuid.UUID(str(value)))
        except (TypeError, ValueError):
            return None

    @staticmethod
    def title_of(question):
        """Título = a primeira pergunta da conversa, truncada (igual todo chat faz)."""
        clean = " ".join(str(question or "").split())
        if len(clean) <= CONVERSATION_TITLE_MAX_LEN:
            return clean
        return clean[:CONVERSATION_TITLE_MAX_LEN].rstrip() + "…"

    # --- leitura -----------------------------------------------------------

    @staticmethod
    def list_conversations(user_id):
        """As conversas do usuário (id, título), da mais recente para a mais antiga."""
        with PostgresPoolFactory.connection() as conn:
            return conn.execute(
                "SELECT id::text, title FROM conversations WHERE user_id = %s ORDER BY updated_at DESC",
                (user_id,),
            ).fetchall()

    @classmethod
    def get(cls, user_id, conversation_id):
        """A conversa com todos os turnos, em ordem, ou None."""
        conversation_id = cls.parse_id(conversation_id)
        if not conversation_id:
            return None
        with PostgresPoolFactory.connection() as conn:
            conversation = conn.execute(
                "SELECT id::text, title FROM conversations WHERE id = %s AND user_id = %s",
                (conversation_id, user_id),
            ).fetchone()
            if not conversation:
                return None
            conversation["turns"] = conn.execute(
                "SELECT id::text, question, answer, status, keywords, figures, equations"
                " FROM turns WHERE conversation_id = %s ORDER BY created_at, id",
                (conversation_id,),
            ).fetchall()
        return conversation

    @classmethod
    def history(cls, user_id, conversation_id):
        """Os últimos HELP_MEMORY_MAX_TURNS turnos respondidos da conversa,
        prontos pra entrar em `messages` do Ollama."""
        conversation_id = cls.parse_id(conversation_id)
        if not user_id or not conversation_id:
            return []
        with PostgresPoolFactory.connection() as conn:
            rows = conn.execute(
                "SELECT question, answer FROM ("
                "  SELECT t.question, t.answer, t.created_at, t.id FROM turns t"
                "  JOIN conversations c ON c.id = t.conversation_id"
                "  WHERE c.id = %s AND c.user_id = %s AND t.status = 'done' AND t.answer <> ''"
                "  ORDER BY t.created_at DESC, t.id DESC LIMIT %s"
                ") recent ORDER BY created_at, id",
                (conversation_id, user_id, HELP_MEMORY_MAX_TURNS),
            ).fetchall()
        messages = []
        for row in rows:
            messages.append({"role": "user", "content": row["question"]})
            messages.append({"role": "assistant", "content": row["answer"]})
        return messages

    @classmethod
    def export(cls, user_id):
        """Todas as conversas do usuário, cada uma com os seus turnos."""
        with PostgresPoolFactory.connection() as conn:
            rows = conn.execute(
                "SELECT c.id::text AS conversation_id, c.title, t.id::text AS turn_id, t.question,"
                " t.answer, t.status, t.keywords, t.figures, t.equations"
                " FROM conversations c LEFT JOIN turns t ON t.conversation_id = c.id"
                " WHERE c.user_id = %s ORDER BY c.updated_at DESC, c.id, t.created_at, t.id",
                (user_id,),
            ).fetchall()
        conversations = {}
        for row in rows:
            conversation = conversations.setdefault(
                row["conversation_id"], {"id": row["conversation_id"], "title": row["title"], "turns": []})
            if row["turn_id"]:
                conversation["turns"].append({
                    "id": row["turn_id"], "question": row["question"], "answer": row["answer"],
                    "status": row["status"], "keywords": row["keywords"],
                    "figures": row["figures"], "equations": row["equations"],
                })
        return list(conversations.values())

    # --- gravação de um turno (ChatService.stream_answer) ---------------------

    @classmethod
    def start_turn(cls, user_id, conversation_id, turn_id, question):
        """Grava a pergunta como um turno ainda sem resposta, criando a
        conversa se for a primeira pergunta dela. Devolve o erro, ou None."""
        conversation_id = cls.parse_id(conversation_id)
        turn_id = cls.parse_id(turn_id)
        if not conversation_id or not turn_id:
            return "Identificador de conversa inválido."
        with PostgresPoolFactory.connection() as conn:
            # Conversa nova: cria. Já existente: só sobe para o topo da lista,
            # e só se for do mesmo usuário (senão, nenhuma linha volta).
            owned = conn.execute(
                "INSERT INTO conversations (id, user_id, title) VALUES (%s, %s, %s)"
                " ON CONFLICT (id) DO UPDATE SET updated_at = now()"
                " WHERE conversations.user_id = EXCLUDED.user_id RETURNING id",
                (conversation_id, user_id, cls.title_of(question)),
            ).fetchone()
            if not owned:
                return "Conversa não encontrada."
            inserted = conn.execute(
                "INSERT INTO turns (id, conversation_id, question) VALUES (%s, %s, %s)"
                " ON CONFLICT (id) DO NOTHING RETURNING id",
                (turn_id, conversation_id, question),
            ).fetchone()
            if not inserted:
                conn.rollback()
                return "Este turno já foi gravado."
        return None

    @staticmethod
    def finish_turn(turn_id, status, answer, keywords=(), figures=(), equations=()):
        """Grava o desfecho de um turno aberto por start_turn. Se o turno foi
        excluído enquanto a resposta chegava, não faz nada."""
        with PostgresPoolFactory.connection() as conn:
            conn.execute(
                "UPDATE turns SET status = %s, answer = %s, keywords = %s, figures = %s, equations = %s"
                " WHERE id = %s",
                (status, answer, list(keywords), Jsonb(list(figures)), Jsonb(list(equations)), turn_id),
            )

    # --- exclusão --------------------------------------------------------------

    @classmethod
    def delete(cls, user_id, conversation_id):
        """Apaga a conversa e os turnos dela. True se existia."""
        conversation_id = cls.parse_id(conversation_id)
        if not conversation_id:
            return False
        with PostgresPoolFactory.connection() as conn:
            cur = conn.execute("DELETE FROM conversations WHERE id = %s AND user_id = %s",
                               (conversation_id, user_id))
            return cur.rowcount > 0

    @classmethod
    def delete_turn(cls, user_id, turn_id):
        """Apaga um par pergunta/resposta (ver wireDeleteButton em
        rag-help.js): sai da conversa e da memória das próximas perguntas."""
        turn_id = cls.parse_id(turn_id)
        if not turn_id:
            return False
        with PostgresPoolFactory.connection() as conn:
            cur = conn.execute(
                "DELETE FROM turns t USING conversations c"
                " WHERE t.id = %s AND c.id = t.conversation_id AND c.user_id = %s",
                (turn_id, user_id),
            )
            return cur.rowcount > 0

    # --- importação -------------------------------------------------------------

    @staticmethod
    def _clean_turn(turn):
        """Um turno importado só com os campos e tipos esperados, ou None se
        não tiver pergunta."""
        if not isinstance(turn, dict) or not str(turn.get("question") or "").strip():
            return None
        status = turn.get("status") if turn.get("status") in TURN_STATUSES else "done"
        as_list = lambda value: value if isinstance(value, list) else []
        return {
            "question": str(turn["question"]),
            "answer": str(turn.get("answer") or ""),
            "status": status,
            "keywords": [str(k) for k in as_list(turn.get("keywords"))],
            "figures": [f for f in as_list(turn.get("figures")) if isinstance(f, dict) and f.get("path")],
            "equations": [e for e in as_list(turn.get("equations")) if isinstance(e, dict) and e.get("number")],
        }

    @classmethod
    def import_conversations(cls, user_id, conversations):
        """Grava conversas vindas de um CSV exportado (ou do localStorage de
        antes das contas). Conversa com o mesmo id do usuário é substituída;
        id de outro usuário (ou inválido) vira uma conversa nova. Devolve
        quantas conversas foram gravadas."""
        imported = 0
        with PostgresPoolFactory.connection() as conn:
            for conversation in conversations if isinstance(conversations, list) else []:
                if not isinstance(conversation, dict):
                    continue
                turns = [t for t in map(cls._clean_turn, conversation.get("turns") or []) if t]
                if not turns:
                    continue
                title = cls.title_of(conversation.get("title") or turns[0]["question"])
                conversation_id = cls.parse_id(conversation.get("id")) or str(uuid.uuid4())
                owned = conn.execute(
                    "INSERT INTO conversations (id, user_id, title) VALUES (%s, %s, %s)"
                    " ON CONFLICT (id) DO UPDATE SET title = EXCLUDED.title, updated_at = now()"
                    " WHERE conversations.user_id = EXCLUDED.user_id RETURNING id",
                    (conversation_id, user_id, title),
                ).fetchone()
                if not owned:
                    conversation_id = str(uuid.uuid4())
                    conn.execute("INSERT INTO conversations (id, user_id, title) VALUES (%s, %s, %s)",
                                 (conversation_id, user_id, title))
                conn.execute("DELETE FROM turns WHERE conversation_id = %s", (conversation_id,))
                # Ids de turno sempre novos (os do arquivo podem colidir com
                # os de outra conta); a ordem vem do created_at, 1 ms por turno.
                for position, turn in enumerate(turns):
                    conn.execute(
                        "INSERT INTO turns (id, conversation_id, question, answer, status, keywords,"
                        " figures, equations, created_at)"
                        " VALUES (%s, %s, %s, %s, %s, %s, %s, %s, now() + %s * interval '1 millisecond')",
                        (str(uuid.uuid4()), conversation_id, turn["question"], turn["answer"], turn["status"],
                         turn["keywords"], Jsonb(turn["figures"]), Jsonb(turn["equations"]), position),
                    )
                imported += 1
        return imported
