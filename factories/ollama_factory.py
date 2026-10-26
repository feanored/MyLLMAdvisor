"""Factory: client de chat do Ollama."""
import ollama


class OllamaClientFactory:
    """Cria o client de chat do Ollama (host padrão do pacote `ollama`)."""

    @staticmethod
    def create():
        return ollama.Client()
