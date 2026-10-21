"""Open connections and create the tables."""
import threading
from contextlib import contextmanager
from importlib.resources import files

import psycopg
from psycopg.conninfo import make_conninfo
from psycopg import sql
from psycopg.rows import dict_row


def connect(url: str) -> psycopg.Connection:
    return psycopg.connect(url, row_factory=dict_row)


def init_schema(conn: psycopg.Connection) -> None:
    sql = files("scout.db").joinpath("schema.sql").read_text(encoding="utf-8")
    conn.execute(sql)
    conn.commit()


def empty_tables(conn: psycopg.Connection) -> None:
    """Delete all rows of all tables. Only in a database whose name ends with _eval or _test, never in the main one."""
    name = conn.info.dbname
    if not name.endswith(("_eval", "_test")):
        raise ValueError(f"will not empty the database {name}: only a database whose name ends with _eval or _test")
    tables = [r["tablename"] for r in conn.execute("SELECT tablename FROM pg_tables WHERE schemaname = 'public'")]
    if tables:
        conn.execute(sql.SQL("TRUNCATE {} RESTART IDENTITY CASCADE").format(
            sql.SQL(", ").join(sql.Identifier(t) for t in tables)))
    conn.commit()


def end_transaction(conn: psycopg.Connection) -> None:
    """Close the open transaction after a chat turn, so that a failed statement does not block the next turn."""
    if conn.info.transaction_status == psycopg.pq.TransactionStatus.INERROR:
        conn.rollback()
    else:
        conn.commit()


class ThreadConnections:
    """The connection of the main thread, and a connection of a small pool for each candidate researcher, so that
    the researchers do not wait for one another (agent/researcher.py). All code uses this object as the connection:
    it gives the connection of the current thread. A researcher thread takes its own one with own()."""

    def __init__(self, main: psycopg.Connection, size: int = 5):
        self.main, self.size = main, size
        self._local, self._guard, self._pool = threading.local(), threading.Lock(), None

    def current(self) -> psycopg.Connection:
        return getattr(self._local, "conn", None) or self.main

    def __getattr__(self, name):
        return getattr(self.current(), name)

    def share(self) -> None:
        """Before the threads start: an open transaction of the main connection can lock the rows that they write,
        and the main thread waits for them."""
        end_transaction(self.main)

    @contextmanager
    def own(self):
        """A connection of the pool for the current thread, until the end of the block. Its transaction closes at
        the end, so the main thread sees what it stored."""
        with self._guard:
            if self._pool is None:
                from psycopg_pool import ConnectionPool
                info = self.main.info
                self._pool = ConnectionPool(make_conninfo(**info.get_parameters(), password=info.password or ""),
                                            min_size=0, max_size=self.size, kwargs={"row_factory": dict_row},
                                            open=True)
        with self._pool.connection() as conn:
            self._local.conn = conn
            try:
                yield conn
                end_transaction(conn)
            finally:
                self._local.conn = None

    def close(self) -> None:
        if self._pool is not None:
            self._pool.close()


def connect_checkpoints(url: str) -> psycopg.Connection:
    """A connection for the LangGraph checkpointer. It needs autocommit and dict rows."""
    return psycopg.connect(url, autocommit=True, prepare_threshold=0, row_factory=dict_row)
