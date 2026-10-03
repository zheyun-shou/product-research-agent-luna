"""Price and stock extraction accuracy on hand-labelled amazon.se snapshots."""
import csv
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Callable

from scout.sources.amazon_se.product import parse_product_page
from scout.sources.amazon_se.urls import product_url
from scout.sources.base import ProductRecord

COLUMNS = ["asin", "snapshot", "screenshot", "true_price", "true_currency", "true_in_stock", "note"]


@dataclass(frozen=True)
class PriceLabel:
    asin: str
    snapshot: Path
    true_price: Decimal | None
    true_currency: str | None
    true_in_stock: bool | None


@dataclass
class PriceEvalResult:
    pages: int = 0
    price_correct: int = 0
    stock_pages: int = 0
    stock_correct: int = 0
    llm_pages: int = 0
    failures: list[str] = field(default_factory=list)

    def summary(self) -> dict:
        return {
            "pages": self.pages,
            "price_correct": self.price_correct,
            "price_accuracy": round(self.price_correct / self.pages, 3) if self.pages else None,
            "stock_pages": self.stock_pages,
            "stock_correct": self.stock_correct,
            "stock_accuracy": round(self.stock_correct / self.stock_pages, 3) if self.stock_pages else None,
            "llm_pages": self.llm_pages,
        }


def write_template(snapshot_dir: Path, out: Path) -> int:
    if out.exists():
        raise FileExistsError(f"{out} exists. Labels are never overwritten; pick another file name.")
    rows = []
    for folder in sorted((snapshot_dir / "amazon_se" / "product").iterdir()):
        # Only snapshots with a screenshot can be labelled. Later rechecks save HTML without one.
        pages = sorted(p for p in folder.glob("*.html") if p.with_suffix(".png").exists())
        if not pages:
            continue
        latest = pages[-1]
        rows.append({"asin": folder.name, "snapshot": str(latest.resolve()),
                     "screenshot": str(latest.with_suffix(".png").resolve())})
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=COLUMNS)
        writer.writeheader()
        writer.writerows(rows)
    return len(rows)


def read_labels(path: Path) -> list[PriceLabel]:
    labels = []
    with path.open(encoding="utf-8", newline="") as fh:
        for row in csv.DictReader(fh):
            price = (row.get("true_price") or "").strip().lower()
            stock = (row.get("true_in_stock") or "").strip().lower()
            if not price and not stock:
                continue
            labels.append(PriceLabel(
                asin=row["asin"],
                snapshot=Path(row["snapshot"]),
                true_price=None if price in ("", "none") else Decimal(price),
                true_currency=(row.get("true_currency") or "").strip() or None,
                true_in_stock={"yes": True, "no": False}.get(stock),
            ))
    return labels


def extract_from_snapshot(label: PriceLabel) -> ProductRecord:
    fetched_at = datetime.fromtimestamp(label.snapshot.stat().st_mtime, tz=timezone.utc)
    return parse_product_page(label.snapshot.read_text(encoding="utf-8"), product_url(label.asin), fetched_at)


def evaluate_prices(labels: list[PriceLabel], extract: Callable[[PriceLabel], ProductRecord]) -> PriceEvalResult:
    result = PriceEvalResult()
    for label in labels:
        rec = extract(label)
        result.pages += 1
        result.llm_pages += int(bool(rec.llm_fields))
        price_ok = rec.price == label.true_price and (
            label.true_price is None or rec.currency == label.true_currency
        )
        if price_ok:
            result.price_correct += 1
        else:
            result.failures.append(
                f"{label.asin}: got {rec.price} {rec.currency}, label {label.true_price} {label.true_currency}"
            )
        if label.true_in_stock is not None:
            result.stock_pages += 1
            result.stock_correct += int(rec.in_stock == label.true_in_stock)
    return result
