# Imagem do My LLM Advisor para o fly.io: o site (Flask via gunicorn), o
# servidor do Chroma e o Ollama numa máquina só, rodando em CPU. Banco, PDFs
# e modelos ficam no volume montado em /data (ver fly.toml e deploy/start.sh).

# Só o binário e as bibliotecas de CPU do Ollama; as de GPU somam vários GB.
FROM ollama/ollama:0.40.11 AS ollama
RUN rm -rf /usr/lib/ollama/cuda_* /usr/lib/ollama/rocm* /usr/lib/ollama/vulkan*

FROM python:3.14-slim-trixie

RUN apt-get update \
 && apt-get install -y --no-install-recommends ca-certificates libgomp1 libstdc++6 libglib2.0-0 libgl1 \
 && rm -rf /var/lib/apt/lists/*

COPY --from=ollama /usr/bin/ollama /usr/bin/ollama
COPY --from=ollama /usr/lib/ollama /usr/lib/ollama

WORKDIR /app

# torch/torchvision da build de CPU antes do resto: a do PyPI traz junto as
# bibliotecas CUDA (vários GB), inúteis numa máquina sem GPU. O "2.14.1+cpu"
# já satisfaz o "torch==2.14.1" do requirements.txt.
COPY requirements.txt .
RUN pip install --no-cache-dir --index-url https://download.pytorch.org/whl/cpu \
        torch==2.14.1 torchvision==0.29.1 \
 && pip install --no-cache-dir -r requirements.txt gunicorn==23.0.0

COPY . .
# O código usa ./.chromadb e ./documentos por caminho relativo: apontam pro volume.
RUN ln -s /data/chromadb .chromadb && ln -s /data/documentos documentos

ENV PYTHONUNBUFFERED=1
EXPOSE 5001
CMD ["bash", "deploy/start.sh"]
