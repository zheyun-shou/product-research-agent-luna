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


def test_the_filters_of_all_search_pages_are_merged(db):
    from scout.db.store import load_filters, save_filters
    from scout.sources.base import FilterGroup, FilterOption
    low = FilterOption(name="Up to 60.9 cm", value="h:1", high=60.9, unit="cm")
    mid = FilterOption(name="61 to 85.9 cm", value="h:2", low=61, high=85.9, unit="cm")
    save_filters(db, "amazon_se", "fridge", [FilterGroup(key="h", name="Height", options=[low])])
    save_filters(db, "amazon_se", "fridge", [FilterGroup(key="h", name="Height", options=[mid])])
    assert load_filters(db, "amazon_se", "fridge") == [FilterGroup(key="h", name="Height", options=[low, mid])]
    assert load_filters(db, "amazon_se", "wine") == []
