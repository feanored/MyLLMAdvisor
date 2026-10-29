#!/usr/bin/env bash
# Sobe os processos do My LLM Advisor numa máquina só (fly.io): Ollama,
# servidor do Chroma e o site. Se qualquer um cair, o script sai e o Fly
# reinicia a máquina inteira.
#
# Com OLLAMA_HOST definido (fly.gpu.toml), o Ollama é externo, numa GPU fora
# do Fly: não sobe o local nem baixa modelos, e o site fala direto com ele
# (a biblioteca `ollama` lê OLLAMA_HOST e OLLAMA_API_KEY do ambiente).
set -euo pipefail

mkdir -p /data/chromadb /data/documentos "$HF_HOME"
pids=()

if [[ -z "${OLLAMA_HOST:-}" ]]; then
  mkdir -p "$OLLAMA_MODELS"
  ollama serve &
  pids+=($!)
fi

chroma run --path /data/chromadb --port 8000 &
pids+=($!)

if [[ -z "${OLLAMA_HOST:-}" ]]; then
  # Baixa para o volume os modelos que ainda faltam (só demora na 1ª vez; nas
  # seguintes, o pull só confere que já estão lá). Roda em segundo plano para
  # o site subir logo; até terminar, perguntas e envios falham avisando do modelo.
  until ollama list >/dev/null 2>&1; do sleep 1; done
  (
    for model in $OLLAMA_PULL_MODELS; do
      ollama pull "$model" || echo "[start] falha ao baixar $model"
    done
  ) &
else
  echo "[start] Ollama externo em $OLLAMA_HOST"
fi

# Um processo só (a fila de indexação, a memória das conversas e os
# cancelamentos vivem na memória dele), com threads para os streams longos.
gunicorn --bind 0.0.0.0:5001 --workers 1 --threads 16 --timeout 120 rag_web:app &
pids+=($!)

wait -n "${pids[@]}"
exit 1
