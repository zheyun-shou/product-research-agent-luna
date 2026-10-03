from datetime import datetime, timezone
from decimal import Decimal

from scout.sources.base import ProductRecord

NOW = datetime(2026, 10, 3, 10, 15, tzinfo=timezone.utc)


def test_missing_core_lists_empty_core_fields():
    rec = ProductRecord(
        source="amazon_se", source_id="B0AAAAAAA1", url="https://www.amazon.se/dp/B0AAAAAAA1",
        fetched_at=NOW, title="Kylskåp", price=Decimal("4999"), in_stock=True,
    )
    assert rec.missing_core() == ["brand", "model"]
