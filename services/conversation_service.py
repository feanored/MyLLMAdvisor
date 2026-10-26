"""Service: memória de conversa do Orientador.

O Ollama não tem memória entre chamadas: `ChatService.stream_answer` busca o
histórico aqui e reenvia os turnos anteriores (só pergunta/resposta, sem o
CONTEXTO do RAG de cada um) antes da pergunta atual.
"""
import threading

from models import HELP_MEMORY_MAX_TURNS


class ConversationService:
    _lock = threading.Lock()
    # conversation_id -> [{"role": "user"|"assistant", "content": str}, ...]
    # Em memória de processo só — some num restart do servidor.
    _conversations = {}

    @classmethod
    def history(cls, conversation_id):
        """Mensagens anteriores da conversa, prontas pra entrar em `messages`."""
        if not conversation_id:
            return []
        with cls._lock:
            return list(cls._conversations.get(conversation_id, []))

    @classmethod
    def append_turn(cls, conversation_id, question, answer):
        """Registra o par pergunta/resposta, descartando os turnos mais
        antigos acima de HELP_MEMORY_MAX_TURNS."""
        if not conversation_id or not question or not answer:
            return
        with cls._lock:
            turns = cls._conversations.setdefault(conversation_id, [])
            turns.append({"role": "user", "content": question})
            turns.append({"role": "assistant", "content": answer})
            overflow = len(turns) - HELP_MEMORY_MAX_TURNS * 2
            if overflow > 0:
                del turns[:overflow]

    @classmethod
    def forget_turn(cls, conversation_id, question):
        """Remove o par pergunta/resposta cuja pergunta bate com `question`
        (ver wireDeleteButton em rag-help.js). Localiza pelo texto porque o
        histórico não guarda o turnId do front-end; remove só a 1ª ocorrência."""
        if not conversation_id or not question:
            return
        with cls._lock:
            turns = cls._conversations.get(conversation_id)
            if not turns:
                return
            for i, msg in enumerate(turns):
                if msg["role"] == "user" and msg["content"] == question:
                    del turns[i:i + 2]
                    return
