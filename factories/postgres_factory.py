"""Factory: pool de conexões com o PostgreSQL (contas, sessões e conversas).

Um pool só por processo, aberto na primeira consulta: o servidor sobe mesmo
com o banco fora do ar (as telas mostram o erro), e cada requisição pega uma
conexão já aberta em vez de abrir uma nova a cada consulta.
"""
import atexit
import threading

from psycopg.conninfo import make_conninfo
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from models import DATABASE_URL, DB_HOST, DB_NAME, DB_PASSWORD, DB_POOL_MAX_SIZE, DB_PORT, DB_USER


class PostgresPoolFactory:
    """Entrega o pool de conexões (DATABASE_URL, ou os DB_* de
    models/config.py). Uso: `with PostgresPoolFactory.connection() as conn:`
    — ao sair do bloco sem erro, a transação é confirmada; com erro, desfeita."""

    _pool = None
    _lock = threading.Lock()

    @staticmethod
    def conninfo():
        if DATABASE_URL:
            return DATABASE_URL
        # Senha vazia = nenhuma (ex.: socket Unix com autenticação "trust").
        return make_conninfo(host=DB_HOST, port=DB_PORT, dbname=DB_NAME, user=DB_USER,
                             password=DB_PASSWORD or None)

    @classmethod
    def pool(cls):
        if cls._pool is None:
            with cls._lock:
                if cls._pool is None:
                    pool = ConnectionPool(cls.conninfo(), min_size=1, max_size=DB_POOL_MAX_SIZE,
                                          kwargs={"row_factory": dict_row}, open=False)
                    pool.open(wait=False)
                    # Fecha as conexões ao sair (ex.: fim do manage_users.py),
                    # antes de o Python desmontar as threads do pool.
                    atexit.register(pool.close)
                    cls._pool = pool
        return cls._pool

    @classmethod
    def connection(cls):
        return cls.pool().connection(timeout=10)
