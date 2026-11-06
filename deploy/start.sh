#!/usr/bin/env bash
# Sobe os processos do My LLM Advisor numa máquina só (fly.io): PostgreSQL,
# Ollama, servidor do Chroma e o site. Se qualquer um cair, o script sai e o
# Fly reinicia a máquina inteira.
#
# Com OLLAMA_HOST definido (fly.gpu.toml), o Ollama é externo, numa GPU fora
# do Fly: não sobe o local nem baixa modelos, e o site fala direto com ele
# (a biblioteca `ollama` lê OLLAMA_HOST e OLLAMA_API_KEY do ambiente).
# Com DATABASE_URL definido, o PostgreSQL também é externo e não sobe aqui.
set -euo pipefail

mkdir -p /data/chromadb /data/documentos "$HF_HOME"
pids=()

# Parada da máquina (deploy, desligamento sem tráfego): repassa o sinal aos
# processos, para o PostgreSQL fechar o banco direito antes de sair. SIGTERM
# para todos (o SIGINT chega ignorado nos processos lançados com "&" de um
# script) e SIGINT para o PostgreSQL, o desligamento rápido dele.
pg_pid=""
stop_all() {
  kill -TERM "${pids[@]}" 2>/dev/null || true
  if [[ -n "$pg_pid" ]]; then kill -INT "$pg_pid" 2>/dev/null || true; fi
  wait
  exit 0
}
trap stop_all INT TERM

if [[ -z "${DATABASE_URL:-}" ]]; then
  PGBIN=/usr/lib/postgresql/17/bin
  PGDATA=/data/postgres
  # Array, não função: uma função rodada com "&" vira um subshell, e o $! (para
  # onde o trap manda o sinal de parada) seria o dele, não o do PostgreSQL.
  AS_POSTGRES=(setpriv --reuid=postgres --regid=postgres --init-groups --)

  mkdir -p "$PGDATA" /run/postgresql
  chown -R postgres:postgres "$PGDATA" /run/postgresql
  chmod 700 "$PGDATA"
  if [[ ! -s "$PGDATA/PG_VERSION" ]]; then
    # Só conexões pelo socket Unix, de dentro da própria máquina, sem senha
    # ("trust"); nenhuma porta TCP fica aberta, então ninguém de fora alcança o banco.
    "${AS_POSTGRES[@]}" "$PGBIN/initdb" -D "$PGDATA" -E UTF8 --locale=C.UTF-8 --auth-local=trust --auth-host=reject
  fi
  "${AS_POSTGRES[@]}" "$PGBIN/postgres" -D "$PGDATA" -c listen_addresses='' -c unix_socket_directories=/run/postgresql &
  pg_pid=$!
  pids+=($!)
  for _ in $(seq 120); do
    "$PGBIN/pg_isready" -q -h /run/postgresql -U postgres && break
    sleep 0.5
  done

  # Usuário, banco e tabelas: os mesmos SQLs de uma instalação local (db/),
  # que só criam o que ainda falta. Pelo socket, a senha não é usada.
  "$PGBIN/psql" -h /run/postgresql -U postgres -d postgres -q \
      -v db_user="${DB_USER:-advisor}" -v db_name="${DB_NAME:-myllmadvisor}" -v db_password= \
      -f db/01_create_database.sql
  "$PGBIN/psql" -h /run/postgresql -U "${DB_USER:-advisor}" -d "${DB_NAME:-myllmadvisor}" -q -f db/02_schema.sql
fi

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

# Um processo só (a fila de indexação, os cancelamentos e o limite de
# tentativas de login vivem na memória dele), com threads para os streams longos.
gunicorn --bind 0.0.0.0:5001 --workers 1 --threads 16 --timeout 120 rag_web:app &
pids+=($!)

wait -n "${pids[@]}"
exit 1
