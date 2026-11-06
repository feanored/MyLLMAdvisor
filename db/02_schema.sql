-- Tabelas do My LLM Advisor: contas de usuário, sessões de login e as
-- conversas do Orientador com suas perguntas e respostas. Roda conectado ao
-- banco do app, como o dono dele (ver db/01_create_database.sql), e pode
-- rodar de novo: só cria o que ainda não existe.
--
--   psql -U advisor -d myllmadvisor -f db/02_schema.sql
--
-- Normalização: cada tabela tem chave simples e todo atributo depende só
-- dela (2ª forma normal; também 3ª). O título é da conversa, não do turno; o
-- e-mail é do usuário, não da conversa. As listas de um turno (palavras-chave,
-- figuras, equações) só existem e só são lidas junto com ele, então ficam
-- nele mesmo, como array/JSONB, em vez de tabelas próprias.

\set ON_ERROR_STOP on

-- Conta de usuário. O e-mail é o login, gravado já normalizado (minúsculas,
-- sem espaços nas pontas) pelo AuthService.
CREATE TABLE IF NOT EXISTS users (
    id                   bigint      GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    email                text        NOT NULL UNIQUE
                                     CHECK (email = lower(btrim(email)) AND email LIKE '_%@_%'),
    -- Hash argon2id no formato PHC ($argon2id$v=19$m=...,t=...,p=...$sal$hash):
    -- o sal e os parâmetros vão junto, a senha em si nunca é gravada.
    password_hash        text        NOT NULL,
    created_at           timestamptz NOT NULL DEFAULT now(),
    password_changed_at  timestamptz NOT NULL DEFAULT now()
);

-- Sessão de login, uma por navegador. O cookie leva um token aleatório; aqui
-- fica só o SHA-256 dele, então nem quem lê o banco consegue se passar pelo
-- usuário. Sair, trocar a senha ou expirar apaga a linha.
CREATE TABLE IF NOT EXISTS sessions (
    token_hash  bytea       PRIMARY KEY,
    user_id     bigint      NOT NULL REFERENCES users (id) ON DELETE CASCADE,
    created_at  timestamptz NOT NULL DEFAULT now(),
    expires_at  timestamptz NOT NULL
);
CREATE INDEX IF NOT EXISTS sessions_user_id_idx ON sessions (user_id);
CREATE INDEX IF NOT EXISTS sessions_expires_at_idx ON sessions (expires_at);

-- Conversa do Orientador. O id (UUID) é gerado pelo navegador ao começar uma
-- conversa nova; o título é a primeira pergunta, truncada.
CREATE TABLE IF NOT EXISTS conversations (
    id          uuid        PRIMARY KEY,
    user_id     bigint      NOT NULL REFERENCES users (id) ON DELETE CASCADE,
    title       text        NOT NULL,
    created_at  timestamptz NOT NULL DEFAULT now(),
    updated_at  timestamptz NOT NULL DEFAULT now()
);
-- Barra lateral: as conversas do usuário, da mais recente para a mais antiga.
CREATE INDEX IF NOT EXISTS conversations_user_recent_idx ON conversations (user_id, updated_at DESC);

-- Turno: uma pergunta e a resposta dela. A resposta é o texto cru do modelo
-- (markdown + LaTeX), o mesmo que volta como memória da conversa nas próximas
-- perguntas; a tela o renderiza de novo ao reabrir, com as figuras e equações
-- recuperadas do RAG naquele turno.
CREATE TABLE IF NOT EXISTS turns (
    id               uuid        PRIMARY KEY,
    conversation_id  uuid        NOT NULL REFERENCES conversations (id) ON DELETE CASCADE,
    question         text        NOT NULL,
    answer           text        NOT NULL DEFAULT '',
    -- pending: a resposta ainda está chegando (ou o servidor caiu no meio).
    status           text        NOT NULL DEFAULT 'pending'
                                 CHECK (status IN ('pending', 'done', 'cancelled', 'error')),
    keywords         text[]      NOT NULL DEFAULT '{}',
    figures          jsonb       NOT NULL DEFAULT '[]',   -- [{"path": ..., "caption": ...}]
    equations        jsonb       NOT NULL DEFAULT '[]',   -- [{"number": ..., "latex": ..., ...}]
    created_at       timestamptz NOT NULL DEFAULT clock_timestamp()
);
CREATE INDEX IF NOT EXISTS turns_conversation_idx ON turns (conversation_id, created_at);
