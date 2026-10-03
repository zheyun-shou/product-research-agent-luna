import csv
import shutil
from decimal import Decimal
from pathlib import Path

import pytest

from scout.eval.prices import (
    PriceLabel, evaluate_prices, extract_from_snapshot, read_labels, write_template,
)

FIXTURES = Path(__file__).parent / "fixtures" / "amazon_se"


def snapshot_tree(tmp_path):
    for asin, name in [("B0AAAAAAA1", "product_full.html"), ("B0AAAAAAA2", "product_unavailable.html")]:
        folder = tmp_path / "snapshots" / "amazon_se" / "product" / asin
        folder.mkdir(parents=True)
        shutil.copy(FIXTURES / name, folder / "20261003T101500.html")
        (folder / "20261003T101500.png").write_bytes(b"png")
    product = tmp_path / "snapshots" / "amazon_se" / "product"
    # a later recheck saved HTML without a screenshot
    (product / "B0AAAAAAA1" / "20261004T090000.html").write_text("later", encoding="utf-8")
    # a product that was never screenshotted
    (product / "B0AAAAAAA5").mkdir()
    (product / "B0AAAAAAA5" / "20261003T101500.html").write_text("x", encoding="utf-8")
    return tmp_path / "snapshots"


def test_template_lists_the_latest_screenshotted_snapshot_per_product(tmp_path):
    out = tmp_path / "labels.csv"
    assert write_template(snapshot_tree(tmp_path), out) == 2
    rows = list(csv.DictReader(out.open(encoding="utf-8")))
    assert [r["asin"] for r in rows] == ["B0AAAAAAA1", "B0AAAAAAA2"]
    assert rows[0]["snapshot"].endswith("20261003T101500.html")
    assert rows[0]["screenshot"].endswith(".png")
    assert rows[0]["true_price"] == ""


def test_template_never_overwrites_labels(tmp_path):
    out = tmp_path / "labels.csv"
    out.write_text("already labelled", encoding="utf-8")
    with pytest.raises(FileExistsError):
        write_template(snapshot_tree(tmp_path), out)


def write_labels(path, rows):
    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=["asin", "snapshot", "screenshot", "true_price",
                                                "true_currency", "true_in_stock", "note"])
        writer.writeheader()
        writer.writerows(rows)


def test_read_labels_skips_unlabelled_rows_and_reads_none(tmp_path):
    path = tmp_path / "labels.csv"
    write_labels(path, [
        {"asin": "A1", "snapshot": "a.html", "true_price": "8990.00", "true_currency": "SEK", "true_in_stock": "yes"},
        {"asin": "A2", "snapshot": "b.html", "true_price": "none", "true_currency": "", "true_in_stock": "no"},
        {"asin": "A3", "snapshot": "c.html", "true_price": "", "true_currency": "", "true_in_stock": ""},
    ])
    labels = read_labels(path)
    assert [(l.asin, l.true_price, l.true_in_stock) for l in labels] == [
        ("A1", Decimal("8990.00"), True), ("A2", None, False),
    ]


def test_evaluate_prices_counts_matches_and_lists_failures(tmp_path):
    snaps = snapshot_tree(tmp_path) / "amazon_se" / "product"
    labels = [
        PriceLabel("B0AAAAAAA1", snaps / "B0AAAAAAA1" / "20261003T101500.html", Decimal("8990"), "SEK", True),
        PriceLabel("B0AAAAAAA2", snaps / "B0AAAAAAA2" / "20261003T101500.html", None, None, False),
        PriceLabel("B0AAAAAAA1", snaps / "B0AAAAAAA1" / "20261003T101500.html", Decimal("10990"), "SEK", True),
    ]
    result = evaluate_prices(labels, extract_from_snapshot)
    summary = result.summary()
    assert (summary["pages"], summary["price_correct"], summary["stock_correct"]) == (3, 2, 3)
    assert summary["price_accuracy"] == pytest.approx(0.667, abs=0.001)
    assert len(result.failures) == 1 and "10990" in result.failures[0]


def test_template_rows_follow_the_explorer_name_order(tmp_path):
    product = tmp_path / "snapshots" / "amazon_se" / "product"
    for asin in ["B01N3LBO4J", "B0BR1TGTYP", "B083V5R42B"]:
        (product / asin).mkdir(parents=True)
        (product / asin / "20261003T101500.html").write_text("x", encoding="utf-8")
        (product / asin / "20261003T101500.png").write_bytes(b"png")
    out = tmp_path / "labels.csv"
    write_template(tmp_path / "snapshots", out)
    rows = list(csv.DictReader(out.open(encoding="utf-8")))
    # Windows Explorer compares digit runs as numbers: 0 < 1 < 83.
    assert [r["asin"] for r in rows] == ["B0BR1TGTYP", "B01N3LBO4J", "B083V5R42B"]
