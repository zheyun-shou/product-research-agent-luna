import pytest

from scout.db.conn import init_schema

pytestmark = pytest.mark.db


def test_schema_creates_the_phase_1_tables(db):
    rows = db.execute(
        "SELECT table_name FROM information_schema.tables WHERE table_schema = 'public'"
    ).fetchall()
    names = {row["table_name"] for row in rows}
    assert {"products", "listings", "variants", "price_history", "fetch_log"} <= names


def test_pgvector_is_at_least_0_8(db):
    row = db.execute("SELECT extversion FROM pg_extension WHERE extname = 'vector'").fetchone()
    assert row is not None
    major, minor = (int(part) for part in row["extversion"].split(".")[:2])
    assert (major, minor) >= (0, 8)


def test_init_schema_can_run_twice(db):
    init_schema(db)
    init_schema(db)
