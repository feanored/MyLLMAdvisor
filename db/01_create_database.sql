-- Cria o usuário (role) e o banco do My LLM Advisor num servidor PostgreSQL.
-- Roda uma vez, como superusuário (postgres), e pode rodar de novo: o que já
-- existe é mantido. Os nomes e a senha batem com os DB_* de models/config.py;
-- para trocar, passe outros valores (e ajuste as variáveis de ambiente):
--
--   psql -U postgres -f db/01_create_database.sql
--   psql -U postgres -v db_user=outro -v db_password=segredo -v db_name=outro_banco -f db/01_create_database.sql
--
-- Depois, crie as tabelas com db/02_schema.sql. No fly.io, o deploy/start.sh
-- roda os dois sozinho.

\set ON_ERROR_STOP on

\if :{?db_user}
\else
  \set db_user advisor
\endif
\if :{?db_password}
\else
  \set db_password advisor
\endif
\if :{?db_name}
\else
  \set db_name myllmadvisor
\endif

SELECT format('CREATE ROLE %I LOGIN PASSWORD %L', :'db_user', :'db_password')
WHERE NOT EXISTS (SELECT FROM pg_roles WHERE rolname = :'db_user')
\gexec

-- template0 + UTF8: o banco aceita qualquer texto (acentos, LaTeX, emojis),
-- independente da codificação padrão do servidor.
SELECT format('CREATE DATABASE %I OWNER %I ENCODING ''UTF8'' TEMPLATE template0', :'db_name', :'db_user')
WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = :'db_name')
\gexec
