import pytest

from scout.config import load_settings
from scout.db.conn import connect, init_schema

TABLES = "chunks, page_texts, eliminations, price_history, variants, listings, products, fetch_log, web_searches"


@pytest.fixture
def db():
    conn = connect(load_settings().test_database_url)
    init_schema(conn)
    conn.execute(f"TRUNCATE {TABLES} RESTART IDENTITY CASCADE")
    conn.commit()
    yield conn
    conn.close()
