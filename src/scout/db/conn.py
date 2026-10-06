"""Open connections and create the tables."""
from importlib.resources import files

import psycopg
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


def connect_checkpoints(url: str) -> psycopg.Connection:
    """A connection for the LangGraph checkpointer. It needs autocommit and dict rows."""
    return psycopg.connect(url, autocommit=True, prepare_threshold=0, row_factory=dict_row)
