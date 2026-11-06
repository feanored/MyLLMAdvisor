"""Service: contas de usuário, senhas e sessões de login (tabelas users e
sessions, ver db/02_schema.sql).

Senhas: hash argon2id (argon2-cffi, parâmetros recomendados do RFC 9106), com
sal aleatório por senha, gravado junto no próprio hash. Se os parâmetros
padrão da biblioteca subirem numa versão nova, o hash é refeito no próximo
login. Sessões: o cookie leva um token aleatório de 256 bits; o banco guarda só
o SHA-256 dele.

As tentativas erradas de login (e de código de convite) são contadas na
memória do processo, como a fila de indexação: o gunicorn roda um processo só.
"""
import hashlib
import hmac
import re
import secrets
import threading
import time

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError
from psycopg.errors import UniqueViolation

from factories import PostgresPoolFactory
from models import (
    INVITE_CODE,
    LOGIN_LOCKOUT_MINUTES,
    LOGIN_MAX_FAILURES,
    PASSWORD_MAX_LEN,
    PASSWORD_MIN_LEN,
    SESSION_DAYS,
)

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_EMAIL_MAX_LEN = 254
_hasher = PasswordHasher()


class AuthService:
    _dummy_hash = None  # ver _verify
    _failures = {}      # ("email" | "ip" | "user", valor) -> [instantes dos erros]
    _failures_lock = threading.Lock()

    # --- validação ---------------------------------------------------------

    @staticmethod
    def normalize_email(email):
        return str(email or "").strip().lower()

    @staticmethod
    def _email_error(email):
        if len(email) > _EMAIL_MAX_LEN or not _EMAIL_RE.match(email):
            return "Informe um e-mail válido."
        return None

    @staticmethod
    def _password_error(password, email=""):
        if len(password) < PASSWORD_MIN_LEN:
            return f"A senha precisa ter pelo menos {PASSWORD_MIN_LEN} caracteres."
        if len(password) > PASSWORD_MAX_LEN:
            return f"A senha pode ter no máximo {PASSWORD_MAX_LEN} caracteres."
        if email and password.strip().lower() == email:
            return "A senha não pode ser o próprio e-mail."
        return None

    # --- limite de tentativas ------------------------------------------------

    @classmethod
    def _limits(cls, email=None, ip=None, user_id=None):
        """Chaves contadas numa tentativa, cada uma com o seu teto de erros."""
        keys = []
        if email:
            keys.append((("email", email), LOGIN_MAX_FAILURES))
        if ip:
            keys.append((("ip", ip), LOGIN_MAX_FAILURES * 3))
        if user_id:
            keys.append((("user", user_id), LOGIN_MAX_FAILURES))
        return keys

    @classmethod
    def _blocked(cls, keys):
        window = LOGIN_LOCKOUT_MINUTES * 60
        now = time.monotonic()
        with cls._failures_lock:
            for key, limit in keys:
                recent = [t for t in cls._failures.get(key, []) if now - t < window]
                if recent:
                    cls._failures[key] = recent
                else:
                    cls._failures.pop(key, None)
                if len(recent) >= limit:
                    return True
        return False

    @classmethod
    def _record_failure(cls, keys):
        now = time.monotonic()
        with cls._failures_lock:
            for key, _limit in keys:
                cls._failures.setdefault(key, []).append(now)
            # Sem isso, tentativas com e-mails sempre novos fariam o dicionário crescer à toa.
            if len(cls._failures) > 10_000:
                window = LOGIN_LOCKOUT_MINUTES * 60
                for key in [k for k, ts in cls._failures.items() if now - ts[-1] >= window]:
                    del cls._failures[key]

    @classmethod
    def _clear_failures(cls, keys):
        with cls._failures_lock:
            for key, _limit in keys:
                cls._failures.pop(key, None)

    @staticmethod
    def _blocked_message():
        return f"Muitas tentativas erradas. Espere {LOGIN_LOCKOUT_MINUTES} minutos e tente de novo."

    # --- senhas --------------------------------------------------------------

    @classmethod
    def _verify(cls, password_hash, password):
        """True se `password` bate com `password_hash`. Sem hash (e-mail sem
        conta), verifica contra um hash qualquer e devolve False: a resposta
        leva o mesmo tempo, e o tempo não denuncia quais e-mails têm conta."""
        if len(password) > PASSWORD_MAX_LEN:
            return False
        if password_hash is None:
            if cls._dummy_hash is None:
                cls._dummy_hash = _hasher.hash(secrets.token_hex(16))
            password_hash, real = cls._dummy_hash, False
        else:
            real = True
        try:
            _hasher.verify(password_hash, password)
            return real
        except (VerificationError, InvalidHashError):
            return False

    # --- contas --------------------------------------------------------------

    @classmethod
    def create_user(cls, email, password):
        """Cria a conta e devolve (usuário, erro)."""
        email = cls.normalize_email(email)
        password = str(password or "")
        error = cls._email_error(email) or cls._password_error(password, email)
        if error:
            return None, error
        try:
            with PostgresPoolFactory.connection() as conn:
                user = conn.execute(
                    "INSERT INTO users (email, password_hash) VALUES (%s, %s) RETURNING id, email",
                    (email, _hasher.hash(password)),
                ).fetchone()
        except UniqueViolation:
            return None, "Já existe uma conta com este e-mail."
        return user, None

    @classmethod
    def register(cls, email, password, invite_code, ip):
        """Cadastro pela tela de login, só com o código de convite
        (INVITE_CODE). Devolve (usuário, erro)."""
        if not INVITE_CODE:
            return None, "A criação de conta está desativada."
        keys = cls._limits(ip=ip)
        if cls._blocked(keys):
            return None, cls._blocked_message()
        if not hmac.compare_digest(str(invite_code or "").strip().encode(), INVITE_CODE.encode()):
            cls._record_failure(keys)
            return None, "Código de convite inválido."
        return cls.create_user(email, password)

    @classmethod
    def authenticate(cls, email, password, ip):
        """Confere e-mail e senha; devolve (usuário, erro). A mensagem de erro
        é a mesma para e-mail sem conta e senha errada."""
        email = cls.normalize_email(email)
        password = str(password or "")
        keys = cls._limits(email=email, ip=ip)
        if cls._blocked(keys):
            return None, cls._blocked_message()

        with PostgresPoolFactory.connection() as conn:
            row = conn.execute(
                "SELECT id, email, password_hash FROM users WHERE email = %s", (email,)
            ).fetchone()
        if not cls._verify(row["password_hash"] if row else None, password):
            cls._record_failure(keys)
            return None, "E-mail ou senha incorretos."

        cls._clear_failures(keys[:1])
        if _hasher.check_needs_rehash(row["password_hash"]):
            with PostgresPoolFactory.connection() as conn:
                conn.execute("UPDATE users SET password_hash = %s WHERE id = %s",
                             (_hasher.hash(password), row["id"]))
        return {"id": row["id"], "email": row["email"]}, None

    @classmethod
    def change_password(cls, user_id, current_password, new_password, keep_token):
        """Troca a senha de quem está logado (confirmando a atual) e encerra
        as outras sessões da conta, mantendo a deste navegador (`keep_token`).
        Devolve o erro, ou None."""
        current_password = str(current_password or "")
        new_password = str(new_password or "")
        keys = cls._limits(user_id=user_id)
        if cls._blocked(keys):
            return cls._blocked_message()

        with PostgresPoolFactory.connection() as conn:
            row = conn.execute("SELECT email, password_hash FROM users WHERE id = %s", (user_id,)).fetchone()
        if not row:
            return "Conta não encontrada."
        if not cls._verify(row["password_hash"], current_password):
            cls._record_failure(keys)
            return "A senha atual está incorreta."
        error = cls._password_error(new_password, row["email"])
        if error:
            return error
        if new_password == current_password:
            return "A nova senha precisa ser diferente da atual."

        cls._clear_failures(keys)
        with PostgresPoolFactory.connection() as conn:
            conn.execute(
                "UPDATE users SET password_hash = %s, password_changed_at = now() WHERE id = %s",
                (_hasher.hash(new_password), user_id),
            )
            conn.execute("DELETE FROM sessions WHERE user_id = %s AND token_hash <> %s",
                         (user_id, cls._token_hash(keep_token or "")))
        return None

    @classmethod
    def set_password(cls, email, new_password):
        """Define a senha sem pedir a atual (administração, manage_users.py)
        e encerra todas as sessões da conta. Devolve o erro, ou None."""
        email = cls.normalize_email(email)
        new_password = str(new_password or "")
        error = cls._password_error(new_password, email)
        if error:
            return error
        with PostgresPoolFactory.connection() as conn:
            row = conn.execute(
                "UPDATE users SET password_hash = %s, password_changed_at = now() WHERE email = %s RETURNING id",
                (_hasher.hash(new_password), email),
            ).fetchone()
            if not row:
                return "Não existe conta com este e-mail."
            conn.execute("DELETE FROM sessions WHERE user_id = %s", (row["id"],))
        return None

    @classmethod
    def find_user(cls, email):
        """A conta (id, email) com este e-mail, ou None."""
        with PostgresPoolFactory.connection() as conn:
            return conn.execute("SELECT id, email FROM users WHERE email = %s",
                                (cls.normalize_email(email),)).fetchone()

    @staticmethod
    def list_users():
        with PostgresPoolFactory.connection() as conn:
            return conn.execute(
                "SELECT u.email, u.created_at, count(c.id) AS conversations"
                " FROM users u LEFT JOIN conversations c ON c.user_id = u.id"
                " GROUP BY u.id ORDER BY u.created_at"
            ).fetchall()

    @classmethod
    def delete_user(cls, email):
        """Apaga a conta, as sessões e as conversas dela. True se existia."""
        with PostgresPoolFactory.connection() as conn:
            cur = conn.execute("DELETE FROM users WHERE email = %s", (cls.normalize_email(email),))
            return cur.rowcount > 0

    # --- sessões --------------------------------------------------------------

    @staticmethod
    def _token_hash(token):
        return hashlib.sha256(token.encode()).digest()

    @classmethod
    def create_session(cls, user_id):
        """Abre uma sessão e devolve o token que vai no cookie. Aproveita
        para apagar as sessões vencidas de todo mundo."""
        token = secrets.token_urlsafe(32)
        with PostgresPoolFactory.connection() as conn:
            conn.execute("DELETE FROM sessions WHERE expires_at < now()")
            conn.execute(
                "INSERT INTO sessions (token_hash, user_id, expires_at)"
                " VALUES (%s, %s, now() + make_interval(days => %s))",
                (cls._token_hash(token), user_id, SESSION_DAYS),
            )
        return token

    @classmethod
    def user_for_session(cls, token):
        """Usuário dono da sessão (id, email, created_at), ou None se o token
        não existir ou estiver vencido."""
        if not token or len(token) > 128:
            return None
        with PostgresPoolFactory.connection() as conn:
            return conn.execute(
                "SELECT u.id, u.email, u.created_at FROM sessions s JOIN users u ON u.id = s.user_id"
                " WHERE s.token_hash = %s AND s.expires_at > now()",
                (cls._token_hash(token),),
            ).fetchone()

    @classmethod
    def delete_session(cls, token):
        if not token:
            return
        with PostgresPoolFactory.connection() as conn:
            conn.execute("DELETE FROM sessions WHERE token_hash = %s", (cls._token_hash(token),))
