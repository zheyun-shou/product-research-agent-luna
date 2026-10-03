# Phase 1 Skeleton Implementation Plan

> **How to use this plan.** Do the tasks in order. Each step has a checkbox (`- [ ]`). Mark the step when it is done.

**Goal:** Fetch fridge listings from amazon.se in a dedicated Chrome profile and store them in Postgres. Measure the price extraction on a set of pages that are labelled by hand. Then add the LLM layer with two roles, and show the top picks in the command line.

**Architecture:** Tasks 1 to 10 fetch and store data, with no LLM. We test them on real pages. They end with a checkpoint and the price evaluation. A Playwright session controls a dedicated Chrome profile and opens one page at a time. Hand-written selectors read the search cards and the product pages into `ProductRecord`s. A small repository layer writes these records to `products`, `listings`, `variants` and `price_history`. After the checkpoint, an LLM layer that is not tied to one provider fills the fields that the selectors do not find. The layer has the roles `chat` and `worker` and starts on a free Gemini key. Python checks the raw text of each value. Then a `picks` command ranks the products, checks them again and writes the top 5.

**Tech Stack:** Python 3.10 (conda env `product-scout`), uv, Playwright with the installed Chrome, BeautifulSoup + lxml, psycopg 3, Postgres 17 with pgvector in Docker, pydantic 2, the `openai` package with the OpenAI-compatible endpoint of Gemini, Typer, pytest.

**Spec:** `docs/superpowers/specs/2026-10-02-product-scout-design.md`

## Global Constraints

- Python 3.10. Do not use 3.11+ features (`tomllib`, `StrEnum`, `typing.Self`, `ExceptionGroup`).
- pgvector 0.8 or later.
- v1 uses only amazon.se. Search terms are in Swedish and prices are in SEK. Do not convert currencies.
- Price is a budget filter. It is never a ranking factor.
- Prices come only from structured data or HTML, never from images.
- Each value that the LLM extracts includes `raw` text. Python checks that this text is in the source. If the check fails, remove the value. The field stays missing.
- The LLM never writes SQL.
- The user logs in and solves captchas. The program never types passwords and never bypasses captchas. It uses no stealth or anti-detection plugins.
- Fetch one page at a time. Wait 4 to 9 seconds between pages.
- Fetched pages, snapshots, labels, `.env` and `browser-profile/` stay out of Git. (`.gitignore` already lists `.env`, `data/`, `snapshots/`, `browser-profile/`.) Test fixtures are small HTML files that we write by hand with invented text. They are not copies of Amazon pages.
- Use only the free Gemini tier. Never enable billing.
- Write all project documents in English, at about 80% ASD-STE100 (short sentences, active voice, simple words).
- Do not copy or fork code from other repositories.
- Activate the conda env before each command (`conda activate product-scout`). Run the tests with `python -m pytest`.

## Review Focus

1. Swedish numbers use no-break spaces and narrow no-break spaces (`8\u00a0990,00\u00a0kr`, `1\u202f234 betyg`). The parser must read them as 8990.00 SEK and 1234. Test in Task 3.
2. A discounted item shows the old list price with a line through it, before the real price. The stored price must be the real price. Tests in Task 6 (search cards) and Task 7 (product page).
3. An unavailable product has no price and shows "Inte tillgänglig för närvarande". Later, the same text block contains "i lager". The program must store the product as out of stock, with a price history row. It must never show the product as a pick. Tests in Task 7, Task 8 and Task 14.
4. A captcha appears during a crawl. The crawl must pause and let the user solve it. Then it must try the same URL again. "Skip" must go to the next item and keep the other results. Test in Task 9.
5. The worker model gives a value, but its raw text is not on the page. The program must remove the value, and the field must stay missing. Test in Task 13.

---

## File Structure

```
pyproject.toml                       package, dependencies, `scout` command
docker-compose.yml                   Postgres 17 + pgvector on port 5433
db/init/01-test-db.sql               creates the test database on first start
.env.example                         variables to copy into .env
config/models.yaml                   which model each LLM role uses
profiles/fridge.yaml                 fridge category profile
eval/results/                        evaluation numbers (committed)
src/scout/
  __init__.py
  config.py                          settings and project paths
  cli.py                             Typer commands
  profiles.py                        category profile model and loader
  crawl.py                           search, fetch, parse, store loop
  db/
    __init__.py
    schema.sql                       tables for phase 1
    conn.py                          connect, init_schema
    store.py                         save_record, log_fetch
  sources/
    __init__.py
    base.py                          RawPage, SessionStatus, Candidate, ProductRecord
    browser.py                       PoliteTimer, BrowserSession (Playwright)
    snapshots.py                     snapshot paths and saving
    amazon_se/
      __init__.py
      urls.py                        URLs, ASINs, snapshot keys
      numbers.py                     Swedish number parsing
      check.py                       captcha, sign-in and error detection
      search.py                      search result parser
      product.py                     product page parser
      adapter.py                     AmazonSeAdapter
  llm/
    __init__.py
    config.py                        models.yaml loader
    ratelimit.py                     RateLimiter
    client.py                        LLM with structured output and retries
  extract/
    __init__.py
    verify.py                        raw-text check
    page.py                          worker extraction for one page
    screen.py                        worker title screening of search results
  picks/
    __init__.py
    rank.py                          cards, conditions, Bayesian score
    recheck.py                       recheck picks before showing
    write.py                         chat-written picks and citation check
  eval/
    __init__.py
    prices.py                        label template, labels, price evaluation
tests/
  conftest.py                        db fixture
  fakes.py                           FakeBrowser, FakeLLM
  fixtures/amazon_se/*.html          hand-written HTML
  test_*.py
```

---

### Task 1: Project skeleton and CLAUDE.md

**Files:**
- Create: `pyproject.toml`, `.env.example`, `src/scout/__init__.py`, `src/scout/config.py`, `src/scout/cli.py`, `tests/test_config.py`, `tests/test_cli.py`
- Modify: `CLAUDE.md` (full rewrite)

**Interfaces:**
- Produces: `scout.config.ROOT: Path`, `scout.config.Settings` (fields `database_url: str`, `test_database_url: str`, `browser_profile_dir: Path`, `snapshot_dir: Path`, `data_dir: Path`), `scout.config.load_settings() -> Settings`, `scout.cli.app: typer.Typer`.

- [ ] **Step 1: Write `pyproject.toml`**

```toml
[project]
name = "product-scout"
version = "0.1.0"
description = "A conversational agent that helps pick products in any category"
requires-python = ">=3.10"
dependencies = [
  "beautifulsoup4>=4.12",
  "lxml>=5.0",
  "openai>=1.40",
  "playwright>=1.45",
  "psycopg[binary]>=3.2",
  "pydantic>=2.7",
  "python-dotenv>=1.0",
  "pyyaml>=6.0",
  "typer>=0.12",
]

[project.optional-dependencies]
dev = ["pytest>=8.0"]

[project.scripts]
scout = "scout.cli:app"

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["src/scout"]

[tool.pytest.ini_options]
testpaths = ["tests"]
markers = ["db: needs the Postgres container (docker compose up -d)"]
```

- [ ] **Step 2: Install**

Run: `uv pip install -e ".[dev]"`
Expected: uv installs the packages into the active conda env (uv shows the python path of the env). You do not need a browser download, because Playwright uses the installed Chrome.

- [ ] **Step 3: Write the failing tests**

`tests/test_config.py`:

```python
from scout.config import ROOT, load_settings


def test_database_urls_come_from_the_environment(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://a:b@localhost:1/x")
    monkeypatch.setenv("TEST_DATABASE_URL", "postgresql://a:b@localhost:1/y")
    settings = load_settings()
    assert settings.database_url == "postgresql://a:b@localhost:1/x"
    assert settings.test_database_url == "postgresql://a:b@localhost:1/y"


def test_local_folders_sit_in_the_project_root():
    settings = load_settings()
    assert settings.browser_profile_dir == ROOT / "browser-profile"
    assert settings.snapshot_dir == ROOT / "snapshots"
    assert settings.data_dir == ROOT / "data"
    assert (ROOT / "pyproject.toml").exists()
```

`tests/test_cli.py`:

```python
from typer.testing import CliRunner

from scout.cli import app


def test_version_command():
    result = CliRunner().invoke(app, ["version"])
    assert result.exit_code == 0
    assert "product-scout 0.1.0" in result.output
```

- [ ] **Step 4: Run the tests to see them fail**

Run: `python -m pytest tests/test_config.py tests/test_cli.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'scout'` or missing names.

- [ ] **Step 5: Write the code**

`src/scout/__init__.py`:

```python
"""product-scout: pick products in any category."""
```

`src/scout/config.py`:

```python
"""Settings read from the environment and .env, and fixed project paths."""
import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[2]


@dataclass(frozen=True)
class Settings:
    database_url: str
    test_database_url: str
    browser_profile_dir: Path
    snapshot_dir: Path
    data_dir: Path


def load_settings() -> Settings:
    load_dotenv(ROOT / ".env")
    return Settings(
        database_url=os.environ.get(
            "DATABASE_URL", "postgresql://scout:scout@localhost:5433/product_scout"
        ),
        test_database_url=os.environ.get(
            "TEST_DATABASE_URL", "postgresql://scout:scout@localhost:5433/product_scout_test"
        ),
        browser_profile_dir=ROOT / "browser-profile",
        snapshot_dir=ROOT / "snapshots",
        data_dir=ROOT / "data",
    )
```

`src/scout/cli.py`:

```python
"""Command line for product-scout."""
import typer

app = typer.Typer(no_args_is_help=True)


@app.callback()
def main() -> None:
    """product-scout command line."""


@app.command()
def version() -> None:
    """Print the version."""
    typer.echo("product-scout 0.1.0")
```

`.env.example`:

```
# Copy this file to .env and fill in the values. .env is never committed.
GEMINI_API_KEY=
DATABASE_URL=postgresql://scout:scout@localhost:5433/product_scout
TEST_DATABASE_URL=postgresql://scout:scout@localhost:5433/product_scout_test
```

- [ ] **Step 6: Run the tests to see them pass**

Run: `python -m pytest tests/test_config.py tests/test_cli.py -v`
Expected: 3 passed.

- [ ] **Step 7: Rewrite `CLAUDE.md` for the new scope**

```markdown
# product-scout

product-scout is a conversational agent. It helps to select products in any category. Two uses are the purchases of Zheyun and the selection of incense for a possible shop in Sweden. It is also a CV project for AI Engineer roles. Thus it must be real, measured and deployable.

- Design: `docs/superpowers/specs/2026-10-02-product-scout-design.md`
- Plans: `docs/superpowers/plans/`
- Flowcharts: https://claude.ai/artifact/4jJqCkVudUFU24kTyXsiqZ

## v1 in short

- The only source is amazon.se (Swedish, SEK). A dedicated Chrome profile fetches it at low volume. Taobao is the first source after v1.
- The agent clarifies the needs, searches and stores, shows the top picks (default 5) and answers follow-up questions.
- Price is a budget filter. It is never a ranking factor. There is no default conversion, shipping or duty. Python calculates only what the user specifies.
- The LLM layer is not tied to one provider. It has two roles, `chat` (strong) and `worker` (light, reads images). The first setup uses a free Gemini key. Never enable billing.
- Each extracted value includes raw text, and Python finds this text in the source. Python checks each claim in the picks against the records.

## Environment

- conda env `product-scout` (Python 3.10). Install with `uv pip install -e ".[dev]"`.
- Postgres + pgvector in Docker. Start it with `docker compose up -d`.
- Run the tests with `python -m pytest`. Tests with the mark `db` need the container.

## Rules

- Write plainly. No em dashes or colons in anything Zheyun will send or sign.
- Write all project documents in English, at about 80% ASD-STE100 (short sentences, active voice, simple words). Chat with Zheyun in Chinese.
- Report only real numbers. Nothing goes into her CV until she confirms it.
- The user logs in and solves captchas herself. The program never types passwords and never bypasses captchas.
- Fetched pages, snapshots, labels and the browser profile stay out of Git.
- Do not fork or copy code from other people.
- The repository stays private until she says otherwise.
```

- [ ] **Step 8: Commit**

```bash
git add pyproject.toml .env.example src/scout tests CLAUDE.md
git commit -m "Project skeleton, settings, CLI entry point; CLAUDE.md for the product-scout scope"
```

---

### Task 2: Postgres with pgvector, schema and connection

**Files:**
- Create: `docker-compose.yml`, `db/init/01-test-db.sql`, `src/scout/db/__init__.py`, `src/scout/db/schema.sql`, `src/scout/db/conn.py`, `tests/conftest.py`, `tests/test_db_schema.py`
- Modify: `src/scout/cli.py` (add `db-init`)

**Interfaces:**
- Consumes: `load_settings()` from Task 1.
- Produces: `scout.db.conn.connect(url: str) -> psycopg.Connection` (rows as dicts), `scout.db.conn.init_schema(conn) -> None`, pytest fixture `db` (a connection to the test database with empty tables).

- [ ] **Step 1: Write the Docker files**

`docker-compose.yml`:

```yaml
services:
  db:
    image: pgvector/pgvector:pg17
    environment:
      POSTGRES_USER: scout
      POSTGRES_PASSWORD: scout
      POSTGRES_DB: product_scout
    ports:
      - "5433:5432"
    volumes:
      - pgdata:/var/lib/postgresql/data
      - ./db/init:/docker-entrypoint-initdb.d
volumes:
  pgdata: {}
```

`db/init/01-test-db.sql`:

```sql
CREATE DATABASE product_scout_test;
```

- [ ] **Step 2: Start the container**

Run: `docker compose up -d` then `docker compose ps`
Expected: the service `db` runs and port 5433 is mapped. (Docker Desktop must run.)

- [ ] **Step 3: Write the failing tests**

`tests/conftest.py`:

```python
import pytest

from scout.config import load_settings
from scout.db.conn import connect, init_schema

TABLES = "price_history, variants, listings, products, fetch_log"


@pytest.fixture
def db():
    conn = connect(load_settings().test_database_url)
    init_schema(conn)
    conn.execute(f"TRUNCATE {TABLES} RESTART IDENTITY CASCADE")
    conn.commit()
    yield conn
    conn.close()
```

`tests/test_db_schema.py`:

```python
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
```

- [ ] **Step 4: Run the tests to see them fail**

Run: `python -m pytest tests/test_db_schema.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'scout.db'`.

- [ ] **Step 5: Write the schema and connection code**

`src/scout/db/__init__.py`:

```python
"""Postgres access."""
```

`src/scout/db/schema.sql`:

```sql
CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS products (
    id          bigserial PRIMARY KEY,
    category    text NOT NULL,
    brand       text,
    model       text,
    name        text NOT NULL,
    attributes  jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at  timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS products_brand_model
    ON products (category, lower(brand), lower(model));

CREATE TABLE IF NOT EXISTS listings (
    id            bigserial PRIMARY KEY,
    product_id    bigint NOT NULL REFERENCES products(id) ON DELETE CASCADE,
    source        text NOT NULL,
    source_id     text NOT NULL,
    url           text NOT NULL,
    title         text,
    rating        numeric(3,2),
    review_count  integer,
    fetched_at    timestamptz NOT NULL,
    UNIQUE (source, source_id)
);

CREATE TABLE IF NOT EXISTS variants (
    id           bigserial PRIMARY KEY,
    listing_id   bigint NOT NULL REFERENCES listings(id) ON DELETE CASCADE,
    variant_key  text NOT NULL,
    label        text NOT NULL DEFAULT '',
    price        numeric(12,2),
    currency     text,
    in_stock     boolean,
    updated_at   timestamptz NOT NULL,
    UNIQUE (listing_id, variant_key)
);

CREATE TABLE IF NOT EXISTS price_history (
    id          bigserial PRIMARY KEY,
    variant_id  bigint NOT NULL REFERENCES variants(id) ON DELETE CASCADE,
    price       numeric(12,2),
    currency    text,
    in_stock    boolean,
    fetched_at  timestamptz NOT NULL
);

CREATE TABLE IF NOT EXISTS fetch_log (
    id           bigserial PRIMARY KEY,
    source       text NOT NULL,
    url          text NOT NULL,
    fetched_at   timestamptz NOT NULL DEFAULT now(),
    status       text NOT NULL,
    http_status  integer,
    duration_ms  integer,
    note         text NOT NULL DEFAULT ''
);
```

`src/scout/db/conn.py`:

```python
"""Open connections and create the tables."""
from importlib.resources import files

import psycopg
from psycopg.rows import dict_row


def connect(url: str) -> psycopg.Connection:
    return psycopg.connect(url, row_factory=dict_row)


def init_schema(conn: psycopg.Connection) -> None:
    sql = files("scout.db").joinpath("schema.sql").read_text(encoding="utf-8")
    conn.execute(sql)
    conn.commit()
```

Add to `src/scout/cli.py`:

```python
from scout.config import load_settings
from scout.db.conn import connect, init_schema


@app.command("db-init")
def db_init() -> None:
    """Create the tables in the main database."""
    with connect(load_settings().database_url) as conn:
        init_schema(conn)
    typer.echo("Tables are ready.")
```

- [ ] **Step 6: Run the tests to see them pass**

Run: `python -m pytest tests/test_db_schema.py -v`
Expected: 3 passed.

Run: `scout db-init`
Expected: `Tables are ready.`

- [ ] **Step 7: Commit**

```bash
git add docker-compose.yml db src/scout/db src/scout/cli.py tests/conftest.py tests/test_db_schema.py
git commit -m "Postgres with pgvector in Docker, phase 1 tables, db-init command"
```

---

### Task 3: Records and Swedish number parsing

**Files:**
- Create: `src/scout/sources/__init__.py`, `src/scout/sources/base.py`, `src/scout/sources/amazon_se/__init__.py`, `src/scout/sources/amazon_se/numbers.py`, `tests/test_numbers.py`, `tests/test_base.py`

**Interfaces:**
- Produces:
  - `RawPage(url, final_url, status: int, html: str, fetched_at: datetime, duration_ms: int = 0, snapshot_path: str | None = None)`
  - `SessionStatus(ok: bool, problem: Literal["captcha", "signin", "blocked", "gone"] | None = None, detail: str = "")`
  - `Candidate(source, source_id, url, title, price: Decimal | None, currency: str | None, rating: Decimal | None, review_count: int | None, sponsored: bool)`
  - `ProductRecord(...)` with `missing_core() -> list[str]` over `("title", "brand", "model", "price", "in_stock")`
  - `numbers.NUMBER: re.Pattern`, `to_decimal(text) -> Decimal | None`, `parse_price(text) -> tuple[Decimal | None, str | None]`, `parse_rating(text) -> Decimal | None`, `parse_count(text) -> int | None`

- [ ] **Step 1: Write the failing tests**

`tests/test_numbers.py`:

```python
from decimal import Decimal

import pytest

from scout.sources.amazon_se.numbers import parse_count, parse_price, parse_rating, to_decimal


@pytest.mark.parametrize(
    "text, expected",
    [
        ("2 499,00 kr", Decimal("2499.00")),
        ("8\u00a0990,00\u00a0kr", Decimal("8990.00")),
        ("1\u202f299,50 kr", Decimal("1299.50")),
        ("2.499,00 kr", Decimal("2499.00")),
        ("SEK 1,299.00", Decimal("1299.00")),
        ("499 kr", Decimal("499")),
    ],
)
def test_parse_price_reads_swedish_and_english_formats(text, expected):
    assert parse_price(text) == (expected, "SEK")


def test_parse_price_refuses_a_range():
    assert parse_price("2 499,00 kr - 2 999,00 kr") == (None, "SEK")


def test_parse_price_without_digits():
    assert parse_price("Se pris i kassan") == (None, None)


@pytest.mark.parametrize(
    "text, expected",
    [
        ("4,5 av 5 stjärnor", Decimal("4.5")),
        ("4.5 out of 5 stars", Decimal("4.5")),
        ("5,0 av 5 stjärnor", Decimal("5.0")),
        ("Inga betyg ännu", None),
    ],
)
def test_parse_rating(text, expected):
    assert parse_rating(text) == expected


@pytest.mark.parametrize(
    "text, expected",
    [
        ("1 234 betyg", 1234),
        ("1\u202f234 globala betyg", 1234),
        ("(1\u00a0234)", 1234),
        ("(1.2K)", 1200),
        ("1,2 tn", 1200),
        ("87", 87),
        ("inga", None),
    ],
)
def test_parse_count(text, expected):
    assert parse_count(text) == expected


@pytest.mark.parametrize(
    "text, expected",
    [("4,5", Decimal("4.5")), ("1.234", Decimal("1234")), ("59.5", Decimal("59.5")), ("", None), ("abc", None)],
)
def test_to_decimal(text, expected):
    assert to_decimal(text) == expected
```

`tests/test_base.py`:

```python
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
```

- [ ] **Step 2: Run the tests to see them fail**

Run: `python -m pytest tests/test_numbers.py tests/test_base.py -v`
Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Write the code**

`src/scout/sources/__init__.py`:

```python
"""Source adapters and the records they produce."""
```

`src/scout/sources/amazon_se/__init__.py`:

```python
"""amazon.se adapter."""
```

`src/scout/sources/base.py`:

```python
"""Records passed between adapters, extraction and storage."""
from datetime import datetime
from decimal import Decimal
from typing import ClassVar, Literal

from pydantic import BaseModel, Field


class RawPage(BaseModel):
    url: str
    final_url: str
    status: int
    html: str
    fetched_at: datetime
    duration_ms: int = 0
    snapshot_path: str | None = None


class SessionStatus(BaseModel):
    ok: bool
    problem: Literal["captcha", "signin", "blocked", "gone"] | None = None
    detail: str = ""


class Candidate(BaseModel):
    source: str
    source_id: str
    url: str
    title: str
    price: Decimal | None = None
    currency: str | None = None
    rating: Decimal | None = None
    review_count: int | None = None
    sponsored: bool = False


class ProductRecord(BaseModel):
    CORE_FIELDS: ClassVar[tuple[str, ...]] = ("title", "brand", "model", "price", "in_stock")

    source: str
    source_id: str
    url: str
    fetched_at: datetime
    title: str | None = None
    brand: str | None = None
    model: str | None = None
    price: Decimal | None = None
    currency: str | None = None
    rating: Decimal | None = None
    review_count: int | None = None
    in_stock: bool | None = None
    variant_label: str = ""
    specs: dict[str, str] = Field(default_factory=dict)
    bullets: list[str] = Field(default_factory=list)
    text: str = ""
    attributes: dict[str, dict] = Field(default_factory=dict)
    llm_fields: list[str] = Field(default_factory=list)

    def missing_core(self) -> list[str]:
        return [name for name in self.CORE_FIELDS if getattr(self, name) is None]
```

`src/scout/sources/amazon_se/numbers.py`:

```python
"""Numbers as amazon.se prints them: '2 499,00 kr', '4,5 av 5 stjärnor', '1 234 betyg'."""
import re
from decimal import Decimal, InvalidOperation

NUMBER = re.compile(r"\d[\d.,\u00a0\u202f ]*\d|\d")
_SPACES = re.compile(r"[\s\u00a0\u202f]+")
_RATING = re.compile(r"(\d(?:[.,]\d)?)\s*(?:av|out of)\s*5")
_COUNT = re.compile(r"(\d[\d.,\u00a0\u202f ]*\d|\d)\s*(tn|[kK])?(?!\w)")


def to_decimal(text: str) -> Decimal | None:
    """Read one number written with Swedish or English separators.

    Exactly three digits after the only separator means a thousands separator.
    """
    s = re.sub(r"[^\d,.]", "", _SPACES.sub("", text))
    if not re.search(r"\d", s):
        return None
    if "," in s and "." in s:
        if s.rfind(",") > s.rfind("."):
            s = s.replace(".", "").replace(",", ".")
        else:
            s = s.replace(",", "")
    else:
        sep = "," if "," in s else "." if "." in s else ""
        if sep:
            head, _, tail = s.rpartition(sep)
            head = head.replace(sep, "")
            s = head + tail if len(tail) == 3 else f"{head}.{tail}"
    try:
        return Decimal(s)
    except InvalidOperation:
        return None


def _currency(text: str) -> str | None:
    low = text.lower()
    if "kr" in low or "sek" in low:
        return "SEK"
    if "€" in low or "eur" in low:
        return "EUR"
    return None


def parse_price(text: str) -> tuple[Decimal | None, str | None]:
    """'2 499,00 kr' gives (Decimal('2499.00'), 'SEK'). A range gives no price."""
    groups = NUMBER.findall(text)
    if len(groups) != 1:
        return None, _currency(text)
    return to_decimal(groups[0]), _currency(text)


def parse_rating(text: str) -> Decimal | None:
    match = _RATING.search(text)
    return to_decimal(match.group(1)) if match else None


def parse_count(text: str) -> int | None:
    match = _COUNT.search(text)
    if not match:
        return None
    number, multiplier = match.group(1), match.group(2)
    if multiplier:
        value = to_decimal(number)
        return int(value * 1000) if value is not None else None
    return int(re.sub(r"\D", "", number))
```

- [ ] **Step 4: Run the tests to see them pass**

Run: `python -m pytest tests/test_numbers.py tests/test_base.py -v`
Expected: all passed.

- [ ] **Step 5: Commit**

```bash
git add src/scout/sources tests/test_numbers.py tests/test_base.py
git commit -m "Records for adapters and storage; Swedish price, rating and count parsing"
```

---

### Task 4: amazon.se URLs, page check and snapshots

**Files:**
- Create: `src/scout/sources/amazon_se/urls.py`, `src/scout/sources/amazon_se/check.py`, `src/scout/sources/snapshots.py`, `tests/test_amazon_urls.py`, `tests/test_amazon_check.py`, `tests/test_snapshots.py`

**Interfaces:**
- Consumes: `RawPage`, `SessionStatus` from Task 3.
- Produces:
  - `urls.BASE_URL = "https://www.amazon.se"`, `urls.SOURCE = "amazon_se"`, `search_url(query: str, page: int = 1) -> str`, `product_url(asin: str) -> str`, `asin_from_url(url: str) -> str | None`, `snapshot_key(url: str) -> str`, `page_kind(url: str) -> str` (`"search"`, `"product"` or `"other"`)
  - `check.check_page(page: RawPage) -> SessionStatus`
  - `snapshots.save_html(page: RawPage, base: Path, source: str, kind: str, key: str) -> Path`

- [ ] **Step 1: Write the failing tests**

`tests/test_amazon_urls.py`:

```python
from scout.sources.amazon_se.urls import (
    asin_from_url, page_kind, product_url, search_url, snapshot_key,
)


def test_search_url():
    assert search_url("kylskåp") == "https://www.amazon.se/s?k=kylsk%C3%A5p"
    assert search_url("kylskåp", 2) == "https://www.amazon.se/s?k=kylsk%C3%A5p&page=2"


def test_product_url_and_asin():
    assert product_url("B0AAAAAAA1") == "https://www.amazon.se/dp/B0AAAAAAA1"
    assert asin_from_url("https://www.amazon.se/Samsung-RB38/dp/B0AAAAAAA1/ref=sr_1_1?k=x") == "B0AAAAAAA1"
    assert asin_from_url("https://www.amazon.se/s?k=x") is None


def test_snapshot_key():
    assert snapshot_key("https://www.amazon.se/x/dp/B0AAAAAAA1/ref=y") == "B0AAAAAAA1"
    assert snapshot_key(search_url("kylskåp", 2)) == "kylskåp-p2"
    assert snapshot_key("https://www.amazon.se/") == "home-p1"


def test_page_kind():
    assert page_kind(search_url("kylskåp")) == "search"
    assert page_kind(product_url("B0AAAAAAA1")) == "product"
    assert page_kind("https://www.amazon.se/") == "other"
```

`tests/test_amazon_check.py`:

```python
from datetime import datetime, timezone

from scout.sources.amazon_se.check import check_page
from scout.sources.base import RawPage

NOW = datetime(2026, 10, 3, 10, 15, tzinfo=timezone.utc)
URL = "https://www.amazon.se/dp/B0AAAAAAA1"


def page(html="<html><body>ok</body></html>", status=200, final_url=URL):
    return RawPage(url=URL, final_url=final_url, status=status, html=html, fetched_at=NOW)


def test_normal_page_is_ok():
    assert check_page(page()).ok


def test_captcha_page():
    status = check_page(page('<form action="/errors/validateCaptcha"></form>', status=503))
    assert (status.ok, status.problem) == (False, "captcha")


def test_sign_in_redirect():
    status = check_page(page(final_url="https://www.amazon.se/ap/signin?openid=x"))
    assert status.problem == "signin"


def test_server_error_and_no_response_are_blocked():
    assert check_page(page(status=503)).problem == "blocked"
    assert check_page(page(status=0, html="")).problem == "blocked"


def test_missing_product_is_gone():
    assert check_page(page(status=404)).problem == "gone"
```

`tests/test_snapshots.py`:

```python
from datetime import datetime, timezone

from scout.sources.base import RawPage
from scout.sources.snapshots import save_html

NOW = datetime(2026, 10, 3, 10, 15, 0, tzinfo=timezone.utc)


def test_save_html_writes_under_source_kind_and_key(tmp_path):
    page = RawPage(url="u", final_url="u", status=200, html="<p>kylskåp</p>", fetched_at=NOW)
    path = save_html(page, tmp_path, "amazon_se", "search", "kylskåp-p1")
    assert path == tmp_path / "amazon_se" / "search" / "kylskåp-p1" / "20261003T101500.html"
    assert path.read_text(encoding="utf-8") == "<p>kylskåp</p>"


def test_save_html_makes_unsafe_keys_safe(tmp_path):
    page = RawPage(url="u", final_url="u", status=200, html="x", fetched_at=NOW)
    path = save_html(page, tmp_path, "amazon_se", "search", 'a/b:c?"d')
    assert path.parent.name == "a_b_c_d"
```

- [ ] **Step 2: Run the tests to see them fail**

Run: `python -m pytest tests/test_amazon_urls.py tests/test_amazon_check.py tests/test_snapshots.py -v`
Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Write the code**

`src/scout/sources/amazon_se/urls.py`:

```python
"""amazon.se URLs and the keys used to file snapshots."""
import re
from urllib.parse import parse_qs, urlencode, urlsplit

BASE_URL = "https://www.amazon.se"
SOURCE = "amazon_se"
_ASIN = re.compile(r"/dp/([A-Z0-9]{10})")


def search_url(query: str, page: int = 1) -> str:
    params = {"k": query}
    if page > 1:
        params["page"] = str(page)
    return f"{BASE_URL}/s?{urlencode(params)}"


def product_url(asin: str) -> str:
    return f"{BASE_URL}/dp/{asin}"


def asin_from_url(url: str) -> str | None:
    match = _ASIN.search(url)
    return match.group(1) if match else None


def page_kind(url: str) -> str:
    if asin_from_url(url):
        return "product"
    if urlsplit(url).path == "/s":
        return "search"
    return "other"


def snapshot_key(url: str) -> str:
    asin = asin_from_url(url)
    if asin:
        return asin
    query = parse_qs(urlsplit(url).query)
    return f"{query.get('k', ['home'])[0]}-p{query.get('page', ['1'])[0]}"
```

`src/scout/sources/amazon_se/check.py`:

```python
"""Tell a normal amazon.se page from a captcha, a sign-in redirect or an error."""
from scout.sources.base import RawPage, SessionStatus


def check_page(page: RawPage) -> SessionStatus:
    if "/ap/signin" in page.final_url:
        return SessionStatus(ok=False, problem="signin", detail="amazon.se asks you to sign in")
    if "/errors/validateCaptcha" in page.html:
        return SessionStatus(ok=False, problem="captcha", detail="amazon.se shows a captcha")
    if page.status in (404, 410):
        return SessionStatus(ok=False, problem="gone", detail=f"page not found ({page.status})")
    if page.status == 0 or page.status == 429 or page.status >= 500:
        return SessionStatus(ok=False, problem="blocked", detail=f"page did not load ({page.status})")
    return SessionStatus(ok=True)
```

`src/scout/sources/snapshots.py`:

```python
"""Save fetched pages locally so parsing and evaluation can be rerun. Snapshots stay out of Git."""
import re
from pathlib import Path

from scout.sources.base import RawPage


def save_html(page: RawPage, base: Path, source: str, kind: str, key: str) -> Path:
    safe_key = re.sub(r"[^\w.-]+", "_", key).strip("_")[:80] or "page"
    path = base / source / kind / safe_key / f"{page.fetched_at.strftime('%Y%m%dT%H%M%S')}.html"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(page.html, encoding="utf-8")
    return path
```

- [ ] **Step 4: Run the tests to see them pass**

Run: `python -m pytest tests/test_amazon_urls.py tests/test_amazon_check.py tests/test_snapshots.py -v`
Expected: all passed.

- [ ] **Step 5: Commit**

```bash
git add src/scout/sources tests/test_amazon_urls.py tests/test_amazon_check.py tests/test_snapshots.py
git commit -m "amazon.se URLs, captcha and sign-in detection, local snapshots"
```

---

### Task 5: Browser session and first real snapshots

**Files:**
- Create: `src/scout/sources/browser.py`, `tests/test_browser_timer.py`
- Modify: `src/scout/cli.py` (add `browser` and `snap`)

**Interfaces:**
- Consumes: `RawPage` (Task 3), `check_page`, `snapshot_key`, `page_kind`, `save_html` (Task 4), `load_settings` (Task 1).
- Produces: `PoliteTimer(min_delay=4.0, max_delay=9.0, rng=random.random, sleep=time.sleep, clock=time.monotonic)` with `wait() -> None`; `BrowserSession(profile_dir: Path, timer: PoliteTimer | None = None)` used as a context manager, with `fetch(url) -> RawPage`, `screenshot(path: Path) -> Path`, `show(url) -> None`.

- [ ] **Step 1: Write the failing test for the timer**

`tests/test_browser_timer.py`:

```python
import pytest

from scout.sources.browser import PoliteTimer


class FakeClock:
    def __init__(self):
        self.now = 100.0
        self.slept = []

    def clock(self):
        return self.now

    def sleep(self, seconds):
        self.slept.append(seconds)
        self.now += seconds


def timer(clock):
    return PoliteTimer(4.0, 9.0, rng=lambda: 0.5, sleep=clock.sleep, clock=clock.clock)


def test_first_wait_does_not_sleep():
    clock = FakeClock()
    timer(clock).wait()
    assert clock.slept == []


def test_second_wait_sleeps_the_rest_of_the_delay():
    clock = FakeClock()
    t = timer(clock)
    t.wait()
    clock.now += 2.0
    t.wait()
    assert clock.slept == [pytest.approx(4.5)]  # delay 6.5 minus 2 already passed


def test_no_sleep_after_a_long_gap():
    clock = FakeClock()
    t = timer(clock)
    t.wait()
    clock.now += 30.0
    t.wait()
    assert clock.slept == []
```

- [ ] **Step 2: Run the test to see it fail**

Run: `python -m pytest tests/test_browser_timer.py -v`
Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Write the browser module**

`src/scout/sources/browser.py`:

```python
"""A dedicated Chrome profile driven by Playwright, one page at a time."""
import random
import time
from datetime import datetime, timezone
from pathlib import Path

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import sync_playwright

from scout.sources.base import RawPage


class PoliteTimer:
    """Keeps a random gap of min_delay to max_delay seconds between page loads."""

    def __init__(self, min_delay=4.0, max_delay=9.0, rng=random.random, sleep=time.sleep, clock=time.monotonic):
        self.min_delay = min_delay
        self.max_delay = max_delay
        self._rng = rng
        self._sleep = sleep
        self._clock = clock
        self._last: float | None = None

    def wait(self) -> None:
        if self._last is not None:
            delay = self.min_delay + (self.max_delay - self.min_delay) * self._rng()
            remaining = delay - (self._clock() - self._last)
            if remaining > 0:
                self._sleep(remaining)
        self._last = self._clock()


class BrowserSession:
    """Opens the product-scout Chrome profile in a visible window.

    The profile lives in browser-profile/, separate from the everyday Chrome profile,
    because Chrome locks a profile that is in use and refuses automation on the default one.
    """

    def __init__(self, profile_dir: Path, timer: PoliteTimer | None = None):
        self.profile_dir = profile_dir
        self.timer = timer or PoliteTimer()

    def __enter__(self) -> "BrowserSession":
        self.profile_dir.mkdir(parents=True, exist_ok=True)
        self._pw = sync_playwright().start()
        self._ctx = self._pw.chromium.launch_persistent_context(
            str(self.profile_dir),
            channel="chrome",
            headless=False,
            locale="sv-SE",
            viewport={"width": 1366, "height": 900},
        )
        self._page = self._ctx.pages[0] if self._ctx.pages else self._ctx.new_page()
        return self

    def __exit__(self, *exc) -> None:
        self._ctx.close()
        self._pw.stop()

    def fetch(self, url: str) -> RawPage:
        self.timer.wait()
        started = time.monotonic()
        try:
            response = self._page.goto(url, wait_until="domcontentloaded", timeout=45_000)
            self._page.wait_for_timeout(1_500)
            status, html = (response.status if response else 0), self._page.content()
        except PlaywrightError:
            status, html = 0, ""
        return RawPage(
            url=url,
            final_url=self._page.url,
            status=status,
            html=html,
            fetched_at=datetime.now(timezone.utc),
            duration_ms=int((time.monotonic() - started) * 1000),
        )

    def screenshot(self, path: Path) -> Path:
        self._page.screenshot(path=str(path), full_page=False)
        return path

    def show(self, url: str) -> None:
        self._page.goto(url, wait_until="domcontentloaded")
        self._page.bring_to_front()
```

- [ ] **Step 4: Run the timer test to see it pass**

Run: `python -m pytest tests/test_browser_timer.py -v`
Expected: 3 passed.

- [ ] **Step 5: Add the `browser` and `snap` commands**

Add to `src/scout/cli.py`:

```python
from typing import List

from scout.sources.amazon_se.check import check_page
from scout.sources.amazon_se.urls import BASE_URL, SOURCE, page_kind, snapshot_key
from scout.sources.browser import BrowserSession
from scout.sources.snapshots import save_html


@app.command()
def browser(url: str = typer.Argument(BASE_URL + "/")) -> None:
    """Open the product-scout Chrome profile to set cookies, the delivery address or log in."""
    settings = load_settings()
    with BrowserSession(settings.browser_profile_dir) as session:
        session.show(url)
        typer.prompt("Press Enter here when you are done in the browser", default="", show_default=False)


@app.command()
def snap(urls: List[str], screenshot: bool = True) -> None:
    """Save snapshots of amazon.se pages, to check the selectors against real pages."""
    settings = load_settings()
    with BrowserSession(settings.browser_profile_dir) as session:
        for url in urls:
            page = session.fetch(url)
            status = check_page(page)
            path = save_html(page, settings.snapshot_dir, SOURCE, page_kind(url), snapshot_key(url))
            if screenshot:
                session.screenshot(path.with_suffix(".png"))
            typer.echo(f"{status.problem or 'ok':8} {path}")
```

- [ ] **Step 6: Manual check, set up the profile (Zheyun)**

Run: `scout browser`
Expected: a new Chrome window opens on amazon.se. It has no bookmarks or logins from your usual profile.
In that window, decline the non-essential cookies on the cookie banner ("Avvisa"). Set the delivery address to your Swedish postcode, so that prices and stock are correct for Sweden. Then push Enter in the terminal. The window closes.

- [ ] **Step 7: Manual check, capture samples (Zheyun)**

Run `scout browser "https://www.amazon.se/s?k=kylsk%C3%A5p"`. From the results, copy the links of three product pages. Select one discounted product with an old price that has a line through it. If you see a product that is out of stock, select it too. Push Enter. Then run:

`scout snap "https://www.amazon.se/s?k=kylsk%C3%A5p" <product link 1> <product link 2> <product link 3>`

Expected: four lines that start with `ok`, each with a path under `snapshots/amazon_se/`. Open the `.png` files. Make sure that they show the real pages and not a captcha. If a line shows `captcha`, solve the captcha with `scout browser` and run `snap` again.

- [ ] **Step 8: Commit**

```bash
git add src/scout/sources/browser.py src/scout/cli.py tests/test_browser_timer.py
git commit -m "Dedicated Chrome profile session with polite timing; browser and snap commands"
```

---

### Task 6: Search result parser

**Files:**
- Create: `src/scout/sources/amazon_se/search.py`, `tests/fixtures/amazon_se/search_basic.html`, `tests/test_amazon_search.py`
- Modify: `src/scout/cli.py` (add `parse-search`)

**Interfaces:**
- Consumes: `Candidate` (Task 3), `parse_price`, `parse_rating`, `parse_count` (Task 3), `SOURCE`, `product_url` (Task 4).
- Produces: `parse_search_page(html: str) -> list[Candidate]` (page order, no duplicates, sponsored cards flagged), `has_next_page(html: str) -> bool`.

- [ ] **Step 1: Write the fixture**

`tests/fixtures/amazon_se/search_basic.html` (structure written by hand, invented products):

```html
<html><body>
<div class="s-main-slot">
  <div data-component-type="s-search-result" data-asin="B0AAAAAAA1">
    <h2><a href="/Samsung-RB38/dp/B0AAAAAAA1/ref=sr_1_1"><span>Samsung RB38C7B5D22 kyl/frys 203 cm</span></a></h2>
    <span class="a-icon-alt">4,5 av 5 stjärnor</span>
    <a href="/dp/B0AAAAAAA1#customerReviews"><span>1&nbsp;234</span></a>
    <span class="a-price"><span class="a-offscreen">8&nbsp;990,00&nbsp;kr</span><span aria-hidden="true">8 990 kr</span></span>
  </div>
  <div data-component-type="s-search-result" data-asin="B0AAAAAAA2">
    <span class="puis-sponsored-label-text">Sponsrad</span>
    <h2><span>Bosch KGN39 kylskåp</span></h2>
    <span class="a-price a-text-price" data-a-strike="true"><span class="a-offscreen">7 999,00 kr</span></span>
    <span class="a-price"><span class="a-offscreen">6 499,00 kr</span></span>
  </div>
  <div data-component-type="s-search-result" data-asin="B0AAAAAAA3">
    <h2><span>Liten kylbox utan recensioner</span></h2>
  </div>
  <div data-component-type="s-search-result" data-asin="">
    <h2><span>Banner without an ASIN</span></h2>
  </div>
  <div data-component-type="s-search-result" data-asin="B0AAAAAAA1">
    <h2><span>Duplicate of the first card</span></h2>
  </div>
</div>
<a class="s-pagination-item s-pagination-next" href="/s?k=kylsk%C3%A5p&amp;page=2">Nästa</a>
</body></html>
```

- [ ] **Step 2: Write the failing tests**

`tests/test_amazon_search.py`:

```python
from decimal import Decimal
from pathlib import Path

from scout.sources.amazon_se.search import has_next_page, parse_search_page

FIXTURES = Path(__file__).parent / "fixtures" / "amazon_se"


def cards():
    return parse_search_page((FIXTURES / "search_basic.html").read_text(encoding="utf-8"))


def test_reads_cards_in_page_order_and_skips_bad_ones():
    assert [c.source_id for c in cards()] == ["B0AAAAAAA1", "B0AAAAAAA2", "B0AAAAAAA3"]


def test_first_card_fields():
    c = cards()[0]
    assert c.title == "Samsung RB38C7B5D22 kyl/frys 203 cm"
    assert c.url == "https://www.amazon.se/dp/B0AAAAAAA1"
    assert (c.price, c.currency) == (Decimal("8990.00"), "SEK")
    assert (c.rating, c.review_count) == (Decimal("4.5"), 1234)
    assert c.sponsored is False


def test_struck_through_price_is_not_the_price():
    c = cards()[1]
    assert c.price == Decimal("6499.00")
    assert c.sponsored is True


def test_card_without_price_or_reviews():
    c = cards()[2]
    assert (c.price, c.rating, c.review_count) == (None, None, None)


def test_has_next_page():
    assert has_next_page((FIXTURES / "search_basic.html").read_text(encoding="utf-8"))
    assert not has_next_page(
        '<span class="s-pagination-item s-pagination-next s-pagination-disabled">Nästa</span>'
    )
    assert not has_next_page("<html></html>")
```

- [ ] **Step 3: Run the tests to see them fail**

Run: `python -m pytest tests/test_amazon_search.py -v`
Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 4: Write the parser**

`src/scout/sources/amazon_se/search.py`:

```python
"""Read amazon.se search result pages with hand-written selectors."""
from bs4 import BeautifulSoup, Tag

from scout.sources.amazon_se.numbers import parse_count, parse_price, parse_rating
from scout.sources.amazon_se.urls import SOURCE, product_url
from scout.sources.base import Candidate

CARD = 'div[data-component-type="s-search-result"]'


def parse_search_page(html: str) -> list[Candidate]:
    soup = BeautifulSoup(html, "lxml")
    found: list[Candidate] = []
    seen: set[str] = set()
    for card in soup.select(CARD):
        asin = (card.get("data-asin") or "").strip()
        title_el = card.select_one("h2 span") or card.select_one("h2")
        if not asin or asin in seen or title_el is None:
            continue
        seen.add(asin)
        price, currency = _price(card)
        rating_el = card.select_one(".a-icon-alt")
        count_el = card.select_one('a[href*="customerReviews"]')
        found.append(
            Candidate(
                source=SOURCE,
                source_id=asin,
                url=product_url(asin),
                title=title_el.get_text(" ", strip=True),
                price=price,
                currency=currency,
                rating=parse_rating(rating_el.get_text()) if rating_el else None,
                review_count=parse_count(count_el.get_text(" ", strip=True)) if count_el else None,
                sponsored=_is_sponsored(card),
            )
        )
    return found


def has_next_page(html: str) -> bool:
    nxt = BeautifulSoup(html, "lxml").select_one(".s-pagination-next")
    return nxt is not None and "s-pagination-disabled" not in (nxt.get("class") or [])


def _price(card: Tag):
    for el in card.select(".a-price"):
        if el.get("data-a-strike") == "true" or "a-text-price" in (el.get("class") or []):
            continue
        off = el.select_one(".a-offscreen")
        if off and off.get_text(strip=True):
            return parse_price(off.get_text())
    return None, None


def _is_sponsored(card: Tag) -> bool:
    if card.select_one(".puis-sponsored-label-text"):
        return True
    return any(span.get_text(strip=True) in ("Sponsrad", "Sponsored") for span in card.select("span"))
```

- [ ] **Step 5: Run the tests to see them pass**

Run: `python -m pytest tests/test_amazon_search.py -v`
Expected: 5 passed.

- [ ] **Step 6: Add `parse-search` and check against the real snapshot**

Add to `src/scout/cli.py`:

```python
import json
from pathlib import Path

from scout.sources.amazon_se.search import has_next_page, parse_search_page


@app.command("parse-search")
def parse_search_cmd(path: Path) -> None:
    """Print what the parser reads from a saved search page."""
    html = path.read_text(encoding="utf-8")
    for c in parse_search_page(html):
        typer.echo(json.dumps(c.model_dump(mode="json"), ensure_ascii=False))
    typer.echo(f"next page: {has_next_page(html)}")
```

Run: `scout parse-search <the search snapshot .html from Task 5>`
Expected: one JSON line for each result card on the screenshot. The titles, prices, ratings and review counts are the same as on the screenshot. The last line is `next page: True`.

If a field is wrong or empty, open the snapshot in a text editor and find the element of that card. Change the selector in `search.py`. Add the same structure to `search_basic.html`, written by hand with invented text. Add a test for it. Run again until the tests and the real snapshot agree. In the commit message, write which selectors changed.

- [ ] **Step 7: Commit**

```bash
git add src/scout/sources/amazon_se/search.py src/scout/cli.py tests/fixtures tests/test_amazon_search.py
git commit -m "amazon.se search result parser, checked against a real snapshot"
```

---

### Task 7: Product page parser

**Files:**
- Create: `src/scout/sources/amazon_se/product.py`, `tests/fixtures/amazon_se/product_full.html`, `tests/fixtures/amazon_se/product_unavailable.html`, `tests/fixtures/amazon_se/product_parts_price.html`, `tests/test_amazon_product.py`
- Modify: `src/scout/cli.py` (add `parse-product`)

**Interfaces:**
- Consumes: `ProductRecord` (Task 3), number parsers (Task 3), `SOURCE`, `product_url`, `asin_from_url` (Task 4).
- Produces: `parse_product_page(html: str, url: str, fetched_at: datetime) -> ProductRecord`. Raises `ValueError` if the URL and the page have no ASIN. Fills `text` with the cleaned page text (title, byline, price block, availability, `key: value` spec lines, bullets, description; 12,000 characters maximum).

- [ ] **Step 1: Write the fixtures**

`tests/fixtures/amazon_se/product_full.html`:

```html
<html><body>
<input type="hidden" id="ASIN" value="B0AAAAAAA1">
<span id="productTitle">
   Samsung RB38C7B5D22/EF kyl/frys, 203 cm, 390 liter
</span>
<a id="bylineInfo">Besök Samsung-butiken</a>
<span id="acrPopover" title="4,5 av 5 stjärnor"><span class="a-icon-alt">4,5 av 5 stjärnor</span></span>
<span id="acrCustomerReviewText">1&nbsp;234 betyg</span>
<div id="corePrice_feature_div">
  <span class="a-price a-text-price" data-a-strike="true"><span class="a-offscreen">10&nbsp;990,00&nbsp;kr</span></span>
  <span class="a-price"><span class="a-offscreen">8&nbsp;990,00&nbsp;kr</span></span>
</div>
<div id="availability"><span>I lager</span></div>
<input id="add-to-cart-button" type="submit">
<div id="variation_color_name"><span class="selection">Svart</span></div>
<div id="feature-bullets"><ul>
  <li><span class="a-list-item">Höjd 203 cm, bredd 59,5 cm</span></li>
  <li><span class="a-list-item">Ljudnivå 35 dB</span></li>
</ul></div>
<table id="productDetails_techSpec_section_1">
  <tr><th>Märke</th><td>&lrm;Samsung</td></tr>
  <tr><th>Modellnummer</th><td>&lrm;RB38C7B5D22/EF</td></tr>
  <tr><th>Produktmått</th><td>&lrm;65,8 x 59,5 x 203 cm</td></tr>
</table>
<div id="productDescription"><p>Rymligt kylskåp med frysdel.</p></div>
</body></html>
```

`tests/fixtures/amazon_se/product_unavailable.html`:

```html
<html><body>
<span id="productTitle">Bosch KGN39VLEB kylskåp</span>
<div id="availability"><span>Inte tillgänglig för närvarande.<br>Vi vet inte när eller om den här varan kommer tillbaka i lager.</span></div>
<div id="detailBullets_feature_div"><ul>
  <li><span class="a-list-item"><span class="a-text-bold">Varumärke &rlm; : &lrm;</span><span>Bosch</span></span></li>
  <li><span class="a-list-item"><span class="a-text-bold">Artikelns modellnummer &rlm; : &lrm;</span><span>KGN39VLEB</span></span></li>
</ul></div>
</body></html>
```

`tests/fixtures/amazon_se/product_parts_price.html`:

```html
<html><body>
<span id="productTitle">Electrolux LRB3DE18S kylskåp</span>
<a id="bylineInfo">Varumärke: Electrolux</a>
<div id="corePriceDisplay_desktop_feature_div">
  <span class="a-price priceToPay"><span class="a-offscreen"></span><span aria-hidden="true"><span class="a-price-whole">5&nbsp;499<span class="a-price-decimal">,</span></span><span class="a-price-fraction">00</span><span class="a-price-symbol">kr</span></span></span>
</div>
<div id="availability"><span>Endast 2 kvar i lager.</span></div>
</body></html>
```

- [ ] **Step 2: Write the failing tests**

`tests/test_amazon_product.py`:

```python
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

import pytest

from scout.sources.amazon_se.product import parse_product_page

FIXTURES = Path(__file__).parent / "fixtures" / "amazon_se"
NOW = datetime(2026, 10, 3, 10, 15, tzinfo=timezone.utc)


def parse(name, url):
    return parse_product_page((FIXTURES / name).read_text(encoding="utf-8"), url, NOW)


def test_full_page():
    rec = parse("product_full.html", "https://www.amazon.se/Samsung/dp/B0AAAAAAA1/ref=sr_1_1?k=x")
    assert rec.source_id == "B0AAAAAAA1"
    assert rec.url == "https://www.amazon.se/dp/B0AAAAAAA1"
    assert rec.title == "Samsung RB38C7B5D22/EF kyl/frys, 203 cm, 390 liter"
    assert (rec.brand, rec.model) == ("Samsung", "RB38C7B5D22/EF")
    assert (rec.price, rec.currency) == (Decimal("8990.00"), "SEK")
    assert (rec.rating, rec.review_count) == (Decimal("4.5"), 1234)
    assert rec.in_stock is True
    assert rec.variant_label == "Svart"
    assert rec.specs["Produktmått"] == "65,8 x 59,5 x 203 cm"
    assert rec.bullets == ["Höjd 203 cm, bredd 59,5 cm", "Ljudnivå 35 dB"]
    assert "Produktmått: 65,8 x 59,5 x 203 cm" in rec.text
    assert "Ljudnivå 35 dB" in rec.text
    assert rec.missing_core() == []


def test_unavailable_page_has_no_price_and_is_out_of_stock():
    rec = parse("product_unavailable.html", "https://www.amazon.se/dp/B0AAAAAAA2")
    assert rec.in_stock is False
    assert rec.price is None
    assert (rec.brand, rec.model) == ("Bosch", "KGN39VLEB")
    assert rec.missing_core() == ["price"]


def test_price_split_into_parts_and_brand_from_byline():
    rec = parse("product_parts_price.html", "https://www.amazon.se/dp/B0AAAAAAA3")
    assert (rec.price, rec.currency) == (Decimal("5499.00"), "SEK")
    assert rec.brand == "Electrolux"
    assert rec.in_stock is True
    assert (rec.rating, rec.review_count, rec.model) == (None, None, None)


def test_brand_from_store_link():
    html = '<span id="productTitle">X</span><a id="bylineInfo">Besök Samsung-butiken</a>'
    assert parse_product_page(html, "https://www.amazon.se/dp/B0AAAAAAA4", NOW).brand == "Samsung"


def test_page_without_asin_is_refused():
    with pytest.raises(ValueError):
        parse_product_page("<html></html>", "https://www.amazon.se/gp/help", NOW)
```

- [ ] **Step 3: Run the tests to see them fail**

Run: `python -m pytest tests/test_amazon_product.py -v`
Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 4: Write the parser**

`src/scout/sources/amazon_se/product.py`:

```python
"""Read amazon.se product pages with hand-written selectors."""
import re
from datetime import datetime

from bs4 import BeautifulSoup, Tag

from scout.sources.amazon_se.numbers import parse_count, parse_price, parse_rating
from scout.sources.amazon_se.urls import SOURCE, asin_from_url, product_url
from scout.sources.base import ProductRecord

BRAND_KEYS = ("Märke", "Varumärke", "Brand")
MODEL_KEYS = ("Modellnummer", "Artikelns modellnummer", "Modellnamn", "Item model number", "Model number")
PRICE_AREAS = (
    "#corePrice_feature_div .a-price",
    "#corePriceDisplay_desktop_feature_div .priceToPay",
    "#corePriceDisplay_desktop_feature_div .a-price",
    "#apex_desktop .a-price",
)
SPEC_ROWS = (
    "#productDetails_techSpec_section_1 tr",
    "#productDetails_techSpec_section_2 tr",
    "#productDetails_detailBullets_sections1 tr",
    "#productOverview_feature_div tr",
)
UNAVAILABLE = ("inte tillgänglig", "tillfälligt slut", "slut i lager", "currently unavailable")
BYLINE = (r"Varumärke:\s*(.+)", r"Besök (.+?)-butiken", r"Visit the (.+?) Store", r"Brand:\s*(.+)")
MAX_TEXT = 12_000
_MARKS = dict.fromkeys(map(ord, "\u200e\u200f"), None)


def parse_product_page(html: str, url: str, fetched_at: datetime) -> ProductRecord:
    soup = BeautifulSoup(html, "lxml")
    asin = asin_from_url(url) or _hidden_asin(soup)
    if not asin:
        raise ValueError(f"No ASIN in the URL or page: {url}")
    specs = _specs(soup)
    bullets = [t for t in (_text(li) for li in soup.select("#feature-bullets li")) if t]
    price, currency = _price(soup)
    rating_el = soup.select_one("#acrPopover")
    rating_text = (rating_el.get("title") or _text(rating_el)) if rating_el else ""
    count_el = soup.select_one("#acrCustomerReviewText")
    return ProductRecord(
        source=SOURCE,
        source_id=asin,
        url=product_url(asin),
        fetched_at=fetched_at,
        title=_text(soup.select_one("#productTitle")) or None,
        brand=_first(specs, BRAND_KEYS) or _brand_from_byline(soup),
        model=_first(specs, MODEL_KEYS),
        price=price,
        currency=currency,
        rating=parse_rating(rating_text) if rating_text else None,
        review_count=parse_count(_text(count_el)) if count_el else None,
        in_stock=_in_stock(soup),
        variant_label=" / ".join(_text(el) for el in soup.select('[id^="variation_"] .selection')),
        specs=specs,
        bullets=bullets,
        text=_page_text(soup, specs, bullets),
    )


def _text(el: Tag | None) -> str:
    if el is None:
        return ""
    return " ".join(el.get_text(" ", strip=True).translate(_MARKS).split())


def _hidden_asin(soup: BeautifulSoup) -> str | None:
    el = soup.select_one("input#ASIN")
    return (el.get("value") or "").strip() or None if el else None


def _clean_key(key: str) -> str:
    return re.sub(r"\s*:\s*$", "", key).strip()


def _specs(soup: BeautifulSoup) -> dict[str, str]:
    specs: dict[str, str] = {}
    for selector in SPEC_ROWS:
        for row in soup.select(selector):
            cells = row.find_all(["th", "td"])
            if len(cells) >= 2:
                key, value = _clean_key(_text(cells[0])), _text(cells[1])
                if key and value:
                    specs.setdefault(key, value)
    for bold in soup.select("#detailBullets_feature_div li .a-text-bold"):
        value_el = bold.find_next_sibling("span")
        key, value = _clean_key(_text(bold)), _text(value_el)
        if key and value:
            specs.setdefault(key, value)
    return specs


def _first(specs: dict[str, str], keys: tuple[str, ...]) -> str | None:
    for key in keys:
        if specs.get(key):
            return specs[key]
    return None


def _brand_from_byline(soup: BeautifulSoup) -> str | None:
    byline = _text(soup.select_one("#bylineInfo"))
    for pattern in BYLINE:
        match = re.search(pattern, byline)
        if match:
            return match.group(1).strip()
    return None


def _price(soup: BeautifulSoup):
    for selector in PRICE_AREAS:
        for el in soup.select(selector):
            if el.get("data-a-strike") == "true" or "a-text-price" in (el.get("class") or []):
                continue
            off = el.select_one(".a-offscreen")
            text = _text(off) or _price_from_parts(el)
            if text:
                price, currency = parse_price(text)
                if price is not None:
                    return price, currency
    return None, None


def _price_from_parts(el: Tag) -> str:
    whole = el.select_one(".a-price-whole")
    if whole is None:
        return ""
    fraction = el.select_one(".a-price-fraction")
    symbol = el.select_one(".a-price-symbol")
    parts = [whole.get_text("", strip=True), fraction.get_text("", strip=True) if fraction else ""]
    return "".join(parts) + " " + (symbol.get_text(strip=True) if symbol else "")


def _in_stock(soup: BeautifulSoup) -> bool | None:
    availability = _text(soup.select_one("#availability")).lower()
    if any(marker in availability for marker in UNAVAILABLE):
        return False
    if soup.select_one("#add-to-cart-button") is not None:
        return True
    if "i lager" in availability or "in stock" in availability:
        return True
    return None


def _page_text(soup: BeautifulSoup, specs: dict[str, str], bullets: list[str]) -> str:
    lines = [
        _text(soup.select_one(selector))
        for selector in (
            "#productTitle", "#bylineInfo", "#corePrice_feature_div",
            "#corePriceDisplay_desktop_feature_div", "#availability",
        )
    ]
    lines += [f"{key}: {value}" for key, value in specs.items()]
    lines += bullets
    lines.append(_text(soup.select_one("#productDescription")))
    return "\n".join(line for line in lines if line)[:MAX_TEXT]
```

- [ ] **Step 5: Run the tests to see them pass**

Run: `python -m pytest tests/test_amazon_product.py -v`
Expected: 5 passed.

- [ ] **Step 6: Add `parse-product` and check against the real snapshots**

Add to `src/scout/cli.py`:

```python
from datetime import datetime, timezone

from scout.sources.amazon_se.product import parse_product_page
from scout.sources.amazon_se.urls import product_url


@app.command("parse-product")
def parse_product_cmd(path: Path) -> None:
    """Print what the parser reads from a saved product page. The folder name is the ASIN."""
    fetched_at = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
    rec = parse_product_page(path.read_text(encoding="utf-8"), product_url(path.parent.name), fetched_at)
    shown = rec.model_dump(mode="json", exclude={"text"})
    typer.echo(json.dumps(shown, ensure_ascii=False, indent=2))
    typer.echo(f"missing: {rec.missing_core()}")
```

Run: `scout parse-product <each product snapshot .html from Task 5>`
Expected: the title, price, rating, review count and stock are the same as on the screenshot next to the file. For the discounted product, the price is the real price, not the old price with the line through it.

If a field is wrong, find the element in the snapshot and repair the selector. Add the structure to a fixture, written by hand with invented text. Add a test and run again. In the commit message, write the selectors that changed.

- [ ] **Step 7: Commit**

```bash
git add src/scout/sources/amazon_se/product.py src/scout/cli.py tests/fixtures tests/test_amazon_product.py
git commit -m "amazon.se product page parser with specs, stock and page text, checked against real snapshots"
```

---

### Task 8: Storage

**Files:**
- Create: `src/scout/db/store.py`, `tests/test_store.py`

**Interfaces:**
- Consumes: `ProductRecord` (Task 3), `db` fixture (Task 2), `parse_product_page` (Task 7) for test data.
- Produces: `SavedIds(product_id: int, listing_id: int, variant_id: int)`, `save_record(conn, rec: ProductRecord, category: str) -> SavedIds`, `log_fetch(conn, source: str, url: str, status: str, http_status: int | None, duration_ms: int | None, note: str = "") -> None`.

Matching rule. If `(source, source_id)` is the same, update the existing listing. If not, use a product with the same category, brand and model (case-insensitive, both known). If there is no such product, make a new product. Phase 1 stores one variant for each listing, with the ASIN as its key.

- [ ] **Step 1: Write the failing tests**

`tests/test_store.py`:

```python
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import pytest

from scout.db.store import log_fetch, save_record
from scout.sources.amazon_se.product import parse_product_page

pytestmark = pytest.mark.db
FIXTURES = Path(__file__).parent / "fixtures" / "amazon_se"
NOW = datetime(2026, 10, 3, 10, 15, tzinfo=timezone.utc)


def record(name="product_full.html", asin="B0AAAAAAA1", when=NOW):
    html = (FIXTURES / name).read_text(encoding="utf-8")
    return parse_product_page(html, f"https://www.amazon.se/dp/{asin}", when)


def count(db, table):
    return db.execute(f"SELECT count(*) AS n FROM {table}").fetchone()["n"]


def test_new_record_creates_every_row(db):
    ids = save_record(db, record(), "fridge")
    product = db.execute("SELECT * FROM products WHERE id = %s", (ids.product_id,)).fetchone()
    variant = db.execute("SELECT * FROM variants WHERE id = %s", (ids.variant_id,)).fetchone()
    assert (product["brand"], product["model"], product["category"]) == ("Samsung", "RB38C7B5D22/EF", "fridge")
    assert (variant["price"], variant["currency"], variant["in_stock"]) == (Decimal("8990.00"), "SEK", True)
    assert count(db, "price_history") == 1


def test_saving_the_same_asin_again_updates_and_adds_history(db):
    first = save_record(db, record(), "fridge")
    later = record(when=NOW + timedelta(days=1)).model_copy(update={"price": Decimal("7990.00")})
    second = save_record(db, later, "fridge")
    assert first == second
    assert count(db, "listings") == 1
    assert count(db, "price_history") == 2
    price = db.execute("SELECT price FROM variants WHERE id = %s", (second.variant_id,)).fetchone()["price"]
    assert price == Decimal("7990.00")


def test_same_brand_and_model_on_another_asin_shares_the_product(db):
    a = save_record(db, record(asin="B0AAAAAAA1"), "fridge")
    b = save_record(db, record(asin="B0AAAAAAA9"), "fridge")
    assert a.product_id == b.product_id
    assert a.listing_id != b.listing_id


def test_records_without_a_model_get_their_own_products(db):
    a = save_record(db, record("product_parts_price.html", "B0AAAAAAA3"), "fridge")
    b = save_record(db, record("product_parts_price.html", "B0AAAAAAA4"), "fridge")
    assert a.product_id != b.product_id


def test_unavailable_product_is_stored_out_of_stock_with_history(db):
    ids = save_record(db, record("product_unavailable.html", "B0AAAAAAA2"), "fridge")
    row = db.execute("SELECT price, in_stock FROM price_history WHERE variant_id = %s", (ids.variant_id,)).fetchone()
    assert (row["price"], row["in_stock"]) == (None, False)


def test_attributes_are_merged(db):
    rec = record().model_copy(update={"attributes": {"height": {"value": 203.0, "unit": "cm", "raw": "203 cm"}}})
    ids = save_record(db, rec, "fridge")
    more = record().model_copy(update={"attributes": {"noise": {"value": 35.0, "unit": "dB", "raw": "35 dB"}}})
    save_record(db, more, "fridge")
    attrs = db.execute("SELECT attributes FROM products WHERE id = %s", (ids.product_id,)).fetchone()["attributes"]
    assert set(attrs) == {"height", "noise"}


def test_log_fetch(db):
    log_fetch(db, "amazon_se", "https://www.amazon.se/dp/B0AAAAAAA1", "captcha", 503, 1200, "amazon.se shows a captcha")
    row = db.execute("SELECT * FROM fetch_log").fetchone()
    assert (row["status"], row["http_status"], row["duration_ms"]) == ("captcha", 503, 1200)
```

- [ ] **Step 2: Run the tests to see them fail**

Run: `python -m pytest tests/test_store.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'scout.db.store'`.

- [ ] **Step 3: Write the store**

`src/scout/db/store.py`:

```python
"""Write product records and fetch logs to Postgres."""
from dataclasses import dataclass

import psycopg
from psycopg.types.json import Jsonb

from scout.sources.base import ProductRecord


@dataclass(frozen=True)
class SavedIds:
    product_id: int
    listing_id: int
    variant_id: int


def save_record(conn: psycopg.Connection, rec: ProductRecord, category: str) -> SavedIds:
    try:
        listing = conn.execute(
            "SELECT id, product_id FROM listings WHERE source = %s AND source_id = %s",
            (rec.source, rec.source_id),
        ).fetchone()
        if listing:
            listing_id, product_id = listing["id"], listing["product_id"]
            conn.execute(
                """UPDATE products SET brand = COALESCE(%s, brand), model = COALESCE(%s, model),
                       attributes = attributes || %s::jsonb
                   WHERE id = %s""",
                (rec.brand, rec.model, Jsonb(rec.attributes), product_id),
            )
            conn.execute(
                """UPDATE listings SET url = %s, title = COALESCE(%s, title), rating = COALESCE(%s, rating),
                       review_count = COALESCE(%s, review_count), fetched_at = %s
                   WHERE id = %s""",
                (rec.url, rec.title, rec.rating, rec.review_count, rec.fetched_at, listing_id),
            )
        else:
            product_id = _find_product(conn, rec, category) or _insert_product(conn, rec, category)
            listing_id = conn.execute(
                """INSERT INTO listings (product_id, source, source_id, url, title, rating, review_count, fetched_at)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s) RETURNING id""",
                (product_id, rec.source, rec.source_id, rec.url, rec.title, rec.rating, rec.review_count, rec.fetched_at),
            ).fetchone()["id"]
        variant_id = conn.execute(
            """INSERT INTO variants (listing_id, variant_key, label, price, currency, in_stock, updated_at)
               VALUES (%s, %s, %s, %s, %s, %s, %s)
               ON CONFLICT (listing_id, variant_key) DO UPDATE SET
                   label = EXCLUDED.label, price = EXCLUDED.price, currency = EXCLUDED.currency,
                   in_stock = EXCLUDED.in_stock, updated_at = EXCLUDED.updated_at
               RETURNING id""",
            (listing_id, rec.source_id, rec.variant_label, rec.price, rec.currency, rec.in_stock, rec.fetched_at),
        ).fetchone()["id"]
        conn.execute(
            "INSERT INTO price_history (variant_id, price, currency, in_stock, fetched_at) VALUES (%s, %s, %s, %s, %s)",
            (variant_id, rec.price, rec.currency, rec.in_stock, rec.fetched_at),
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    return SavedIds(product_id, listing_id, variant_id)


def _find_product(conn: psycopg.Connection, rec: ProductRecord, category: str) -> int | None:
    if not (rec.brand and rec.model):
        return None
    row = conn.execute(
        """SELECT id FROM products
           WHERE category = %s AND lower(brand) = lower(%s) AND lower(model) = lower(%s)
           ORDER BY id LIMIT 1""",
        (category, rec.brand, rec.model),
    ).fetchone()
    return row["id"] if row else None


def _insert_product(conn: psycopg.Connection, rec: ProductRecord, category: str) -> int:
    return conn.execute(
        """INSERT INTO products (category, brand, model, name, attributes)
           VALUES (%s, %s, %s, %s, %s::jsonb) RETURNING id""",
        (category, rec.brand, rec.model, rec.title or rec.source_id, Jsonb(rec.attributes)),
    ).fetchone()["id"]


def log_fetch(
    conn: psycopg.Connection, source: str, url: str, status: str,
    http_status: int | None, duration_ms: int | None, note: str = "",
) -> None:
    conn.execute(
        """INSERT INTO fetch_log (source, url, status, http_status, duration_ms, note)
           VALUES (%s, %s, %s, %s, %s, %s)""",
        (source, url, status, http_status, duration_ms, note),
    )
    conn.commit()
```

- [ ] **Step 4: Run the tests to see them pass**

Run: `python -m pytest tests/test_store.py -v`
Expected: 7 passed.

- [ ] **Step 5: Commit**

```bash
git add src/scout/db/store.py tests/test_store.py
git commit -m "Store product records as products, listings, variants and price history; fetch log"
```

---

### Task 9: Adapter, crawl and the fetch-and-store checkpoint

**Files:**
- Create: `src/scout/sources/amazon_se/adapter.py`, `src/scout/crawl.py`, `tests/fakes.py`, `tests/test_crawl.py`
- Modify: `src/scout/cli.py` (add `crawl` and `ask_user`)

**Interfaces:**
- Consumes: everything from Tasks 3 to 8.
- Produces:
  - `AmazonSeAdapter(browser, snapshot_dir: Path, screenshots: bool = False)` with `name`, `kind`, `access`, `terms_check: str`, `search_url(query, page) -> str`, `fetch(url) -> RawPage` (also saves the HTML snapshot, plus a screenshot for product pages when `screenshots`), `check_page(page) -> SessionStatus`, `check_session() -> SessionStatus`, `parse_search(page) -> tuple[list[Candidate], bool]`, `parse(page) -> ProductRecord`
  - `crawl.OnProblem = Callable[[SessionStatus, str], bool]` (True means try again, False means skip)
  - `crawl.Enricher = Callable[[ProductRecord], tuple[ProductRecord, bool]]` (the bool says whether an LLM call was made)
  - `fetch_checked(adapter, conn, url, on_problem) -> RawPage | None`
  - `crawl(conn, adapter, category, query, *, on_problem, max_pages=1, max_details=20, budget: Decimal | None = None, enrich: Enricher | None = None) -> CrawlReport`
  - `CrawlReport` fields `search_pages, candidates, over_budget, detail_pages, saved, llm_pages: int`, `skipped: list[str]`, `missing: Counter`, `notes: list[str]`
  - `tests/fakes.py: FakeBrowser(pages: dict[str, list[str]])`

- [ ] **Step 1: Write the test fake**

`tests/fakes.py`:

```python
"""Test doubles shared by several test files."""
from datetime import datetime, timezone
from pathlib import Path

from scout.sources.base import RawPage

NOW = datetime(2026, 10, 3, 10, 15, tzinfo=timezone.utc)


class FakeBrowser:
    """Serves HTML by URL. A list gives one page per fetch; the last one repeats."""

    def __init__(self, pages: dict[str, list[str]]):
        self.pages = {url: list(htmls) for url, htmls in pages.items()}
        self.fetched: list[str] = []

    def fetch(self, url: str) -> RawPage:
        self.fetched.append(url)
        queue = self.pages[url]
        html = queue.pop(0) if len(queue) > 1 else queue[0]
        return RawPage(url=url, final_url=url, status=200, html=html, fetched_at=NOW, duration_ms=5)

    def screenshot(self, path: Path) -> Path:
        path.write_bytes(b"png")
        return path
```

- [ ] **Step 2: Write the failing tests**

`tests/test_crawl.py`:

```python
from decimal import Decimal
from pathlib import Path

import pytest

from scout.crawl import crawl
from scout.sources.amazon_se.adapter import AmazonSeAdapter
from scout.sources.amazon_se.urls import product_url, search_url
from tests.fakes import FakeBrowser

pytestmark = pytest.mark.db
FIXTURES = Path(__file__).parent / "fixtures" / "amazon_se"
CAPTCHA = '<form action="/errors/validateCaptcha"></form>'


def html(name):
    return (FIXTURES / name).read_text(encoding="utf-8")


def site(captcha_first=False):
    first = [CAPTCHA, html("product_full.html")] if captcha_first else [html("product_full.html")]
    return FakeBrowser({
        search_url("kylskåp"): [html("search_basic.html")],
        product_url("B0AAAAAAA1"): first,
        product_url("B0AAAAAAA2"): [html("product_unavailable.html")],
        product_url("B0AAAAAAA3"): [html("product_parts_price.html")],
    })


def never_called(status, url):
    raise AssertionError(f"unexpected problem {status.problem} at {url}")


def count(db, table):
    return db.execute(f"SELECT count(*) AS n FROM {table}").fetchone()["n"]


def test_crawl_saves_every_candidate(db, tmp_path):
    adapter = AmazonSeAdapter(site(), tmp_path)
    report = crawl(db, adapter, "fridge", "kylskåp", on_problem=never_called)
    assert (report.search_pages, report.candidates, report.saved) == (1, 3, 3)
    assert count(db, "listings") == 3
    assert count(db, "fetch_log") == 4
    assert report.missing["price"] == 1
    assert any(tmp_path.glob("amazon_se/product/B0AAAAAAA1/*.html"))


def test_budget_drops_known_prices_above_it(db, tmp_path):
    report = crawl(db, AmazonSeAdapter(site(), tmp_path), "fridge", "kylskåp",
                   on_problem=never_called, budget=Decimal("7000"))
    assert (report.over_budget, report.saved) == (1, 2)


def test_captcha_pauses_then_retries_the_same_url(db, tmp_path):
    calls = []

    def solved(status, url):
        calls.append((status.problem, url))
        return True

    browser = site(captcha_first=True)
    report = crawl(db, AmazonSeAdapter(browser, tmp_path), "fridge", "kylskåp", on_problem=solved)
    assert calls == [("captcha", product_url("B0AAAAAAA1"))]
    assert browser.fetched.count(product_url("B0AAAAAAA1")) == 2
    assert report.saved == 3


def test_skipping_a_captcha_moves_on(db, tmp_path):
    report = crawl(db, AmazonSeAdapter(site(captcha_first=True), tmp_path), "fridge", "kylskåp",
                   on_problem=lambda status, url: False)
    assert report.saved == 2
    assert report.skipped == [product_url("B0AAAAAAA1")]


def test_max_details_caps_detail_pages(db, tmp_path):
    report = crawl(db, AmazonSeAdapter(site(), tmp_path), "fridge", "kylskåp",
                   on_problem=never_called, max_details=1)
    assert (report.detail_pages, report.saved) == (1, 1)


def test_enricher_is_called_and_counted(db, tmp_path):
    def enrich(rec):
        return rec.model_copy(update={"llm_fields": ["model"]}), True

    report = crawl(db, AmazonSeAdapter(site(), tmp_path), "fridge", "kylskåp",
                   on_problem=never_called, enrich=enrich)
    assert report.llm_pages == 3


def test_adapter_without_terms_check_is_refused(db, tmp_path):
    adapter = AmazonSeAdapter(site(), tmp_path)
    adapter.terms_check = ""
    with pytest.raises(RuntimeError):
        crawl(db, adapter, "fridge", "kylskåp", on_problem=never_called)
```

Also make an empty `tests/__init__.py`, so that `from tests.fakes import FakeBrowser` works.

- [ ] **Step 3: Run the tests to see them fail**

Run: `python -m pytest tests/test_crawl.py -v`
Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 4: Write the adapter and the crawl**

`src/scout/sources/amazon_se/adapter.py`:

```python
"""The amazon.se adapter: fetch through the browser, snapshot, check and parse."""
from pathlib import Path
from typing import Protocol

from scout.sources.amazon_se.check import check_page
from scout.sources.amazon_se.product import parse_product_page
from scout.sources.amazon_se.search import has_next_page, parse_search_page
from scout.sources.amazon_se.urls import BASE_URL, SOURCE, page_kind, search_url, snapshot_key
from scout.sources.base import Candidate, ProductRecord, RawPage, SessionStatus
from scout.sources.snapshots import save_html


class Browser(Protocol):
    def fetch(self, url: str) -> RawPage: ...
    def screenshot(self, path: Path) -> Path: ...


class AmazonSeAdapter:
    name = SOURCE
    kind = "product"
    access = "user_browser"
    terms_check = (
        "2026-10-03: Zheyun accepts personal, low-volume fetching of amazon.se "
        "in her own browser profile (spec section 8)."
    )

    def __init__(self, browser: Browser, snapshot_dir: Path, screenshots: bool = False):
        self.browser = browser
        self.snapshot_dir = snapshot_dir
        self.screenshots = screenshots

    def search_url(self, query: str, page: int = 1) -> str:
        return search_url(query, page)

    def fetch(self, url: str) -> RawPage:
        page = self.browser.fetch(url)
        kind = page_kind(url)
        path = save_html(page, self.snapshot_dir, SOURCE, kind, snapshot_key(url))
        if self.screenshots and kind == "product":
            self.browser.screenshot(path.with_suffix(".png"))
        return page.model_copy(update={"snapshot_path": str(path)})

    def check_page(self, page: RawPage) -> SessionStatus:
        return check_page(page)

    def check_session(self) -> SessionStatus:
        return check_page(self.browser.fetch(BASE_URL + "/"))

    def parse_search(self, page: RawPage) -> tuple[list[Candidate], bool]:
        return parse_search_page(page.html), has_next_page(page.html)

    def parse(self, page: RawPage) -> ProductRecord:
        return parse_product_page(page.html, page.url, page.fetched_at)
```

`src/scout/crawl.py`:

```python
"""One crawl: search pages, then detail pages, each checked, parsed and stored."""
from collections import Counter
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Callable

import psycopg

from scout.db.store import log_fetch, save_record
from scout.sources.base import ProductRecord, RawPage, SessionStatus

OnProblem = Callable[[SessionStatus, str], bool]
Enricher = Callable[[ProductRecord], "tuple[ProductRecord, bool]"]
MAX_ATTEMPTS = 3


@dataclass
class CrawlReport:
    search_pages: int = 0
    candidates: int = 0
    over_budget: int = 0
    detail_pages: int = 0
    saved: int = 0
    llm_pages: int = 0
    skipped: list[str] = field(default_factory=list)
    missing: Counter = field(default_factory=Counter)
    notes: list[str] = field(default_factory=list)


def fetch_checked(adapter, conn: psycopg.Connection, url: str, on_problem: OnProblem) -> RawPage | None:
    for _ in range(MAX_ATTEMPTS):
        page = adapter.fetch(url)
        status = adapter.check_page(page)
        log_fetch(conn, adapter.name, url, "ok" if status.ok else status.problem,
                  page.status, page.duration_ms, status.detail)
        if status.ok:
            return page
        if status.problem == "gone" or not on_problem(status, url):
            return None
    return None


def crawl(
    conn: psycopg.Connection,
    adapter,
    category: str,
    query: str,
    *,
    on_problem: OnProblem,
    max_pages: int = 1,
    max_details: int = 20,
    budget: Decimal | None = None,
    enrich: Enricher | None = None,
) -> CrawlReport:
    if not getattr(adapter, "terms_check", ""):
        raise RuntimeError(f"Adapter {adapter.name} has no terms check recorded, so it stays disabled.")
    report = CrawlReport()
    candidates, seen = [], set()
    for number in range(1, max_pages + 1):
        page = fetch_checked(adapter, conn, adapter.search_url(query, number), on_problem)
        if page is None:
            break
        report.search_pages += 1
        found, has_next = adapter.parse_search(page)
        for cand in found:
            if cand.source_id not in seen:
                seen.add(cand.source_id)
                candidates.append(cand)
        if not has_next:
            break
    report.candidates = len(candidates)
    if budget is not None:
        within = [c for c in candidates if c.price is None or c.price <= budget]
        report.over_budget = len(candidates) - len(within)
        candidates = within
    for cand in candidates[:max_details]:
        page = fetch_checked(adapter, conn, cand.url, on_problem)
        if page is None:
            report.skipped.append(cand.url)
            continue
        report.detail_pages += 1
        try:
            rec = adapter.parse(page)
        except ValueError as exc:
            report.skipped.append(cand.url)
            report.notes.append(str(exc))
            continue
        if enrich is not None:
            rec, called = enrich(rec)
            report.llm_pages += int(called)
        save_record(conn, rec, category)
        report.saved += 1
        report.missing.update(rec.missing_core())
    return report
```

- [ ] **Step 5: Run the tests to see them pass**

Run: `python -m pytest tests/test_crawl.py -v`
Expected: 7 passed.

- [ ] **Step 6: Add the `crawl` command**

Add to `src/scout/cli.py`:

```python
from decimal import Decimal
from typing import Optional

from scout.crawl import crawl as run_crawl
from scout.sources.amazon_se.adapter import AmazonSeAdapter
from scout.sources.base import SessionStatus


def ask_user(status: SessionStatus, url: str) -> bool:
    """Pause until the user has dealt with a captcha or sign-in in the browser window."""
    typer.echo(f"\n{status.detail} at {url}")
    typer.echo("Solve it or log in in the product-scout browser window.")
    answer = typer.prompt("Press Enter when done, or type s and Enter to skip", default="", show_default=False)
    return answer.strip().lower() != "s"


@app.command()
def crawl(
    category: str,
    query: str,
    pages: int = 1,
    details: int = 20,
    budget: Optional[float] = None,
    screenshots: bool = False,
) -> None:
    """Search amazon.se, fetch detail pages and store them. QUERY is in Swedish, e.g. kylskåp."""
    settings = load_settings()
    budget_sek = Decimal(str(budget)) if budget is not None else None
    with connect(settings.database_url) as conn, BrowserSession(settings.browser_profile_dir) as session:
        adapter = AmazonSeAdapter(session, settings.snapshot_dir, screenshots=screenshots)
        report = run_crawl(conn, adapter, category, query, on_problem=ask_user,
                           max_pages=pages, max_details=details, budget=budget_sek)
    typer.echo(f"search pages {report.search_pages}, candidates {report.candidates}, "
               f"over budget {report.over_budget}, detail pages {report.detail_pages}, saved {report.saved}")
    typer.echo(f"pages that needed an LLM call: {report.llm_pages} of {report.detail_pages}")
    typer.echo(f"fields the selectors missed: {dict(report.missing) or 'none'}")
    for url in report.skipped:
        typer.echo(f"skipped {url}")
    for note in report.notes:
        typer.echo(note)
```

- [ ] **Step 7: Run the whole test suite**

Run: `python -m pytest -v`
Expected: all passed.

- [ ] **Step 8: Commit**

```bash
git add src/scout/sources/amazon_se/adapter.py src/scout/crawl.py src/scout/cli.py tests/__init__.py tests/fakes.py tests/test_crawl.py
git commit -m "amazon.se adapter and crawl with captcha pause, budget filter and fetch log"
```

- [ ] **Step 9: CHECKPOINT, real crawl (Zheyun and Claude together)**

Run: `scout db-init` then `scout crawl fridge "kylskåp" --pages 1 --details 10`
Expected: the browser window opens one search page and a maximum of 10 product pages, 4 to 9 seconds apart. The report shows `saved` near 10 and lists the fields that the selectors did not find.

Look at the stored data:

```bash
docker compose exec db psql -U scout product_scout -c "SELECT l.source_id, left(l.title, 50) AS title, v.price, v.in_stock, l.rating, l.review_count, p.brand, p.model FROM listings l JOIN variants v ON v.listing_id = l.id JOIN products p ON p.id = l.product_id ORDER BY l.id;"
docker compose exec db psql -U scout product_scout -c "SELECT status, count(*) FROM fetch_log GROUP BY status;"
```

Open three of the products in a normal browser. Compare the price, the stock and the rating. If a value is different, go back to Task 6 or 7. Repair the selector, add a fixture case and run again.

**Stop here and do a review with Zheyun.** Go to Task 10 only when she agrees that the fetch and the storage work on real pages.

---

### Task 10: Price extraction evaluation

**Files:**
- Create: `src/scout/eval/__init__.py`, `src/scout/eval/prices.py`, `tests/test_eval_prices.py`, `eval/results/.gitkeep`
- Modify: `src/scout/cli.py` (add `eval-template` and `eval-prices`)

**Interfaces:**
- Consumes: `parse_product_page` (Task 7), `product_url` (Task 4), `ProductRecord` (Task 3).
- Produces:
  - `PriceLabel(asin: str, snapshot: Path, true_price: Decimal | None, true_currency: str | None, true_in_stock: bool | None)`
  - `write_template(snapshot_dir: Path, out: Path) -> int` (one row for each product, from its latest snapshot that has a screenshot; does not overwrite an existing file)
  - `read_labels(path: Path) -> list[PriceLabel]` (skips rows without a label; `none` means that the page shows no price)
  - `extract_from_snapshot(label: PriceLabel) -> ProductRecord`
  - `evaluate_prices(labels, extract: Callable[[PriceLabel], ProductRecord]) -> PriceEvalResult` with `summary() -> dict` and `failures: list[str]`

- [ ] **Step 1: Write the failing tests**

`tests/test_eval_prices.py`:

```python
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
```

- [ ] **Step 2: Run the tests to see them fail**

Run: `python -m pytest tests/test_eval_prices.py -v`
Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Write the evaluation module**

`src/scout/eval/__init__.py`:

```python
"""Evaluations with hand-labelled data."""
```

`src/scout/eval/prices.py`:

```python
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
```

- [ ] **Step 4: Run the tests to see them pass**

Run: `python -m pytest tests/test_eval_prices.py -v`
Expected: 4 passed.

- [ ] **Step 5: Add the commands**

Add to `src/scout/cli.py`:

```python
from scout.config import ROOT
from scout.eval.prices import evaluate_prices, extract_from_snapshot, read_labels, write_template


@app.command("eval-template")
def eval_template(out: Path = Path("data/eval/price_labels.csv")) -> None:
    """Write a label sheet with one row per saved product snapshot."""
    n = write_template(load_settings().snapshot_dir, out)
    typer.echo(f"{n} rows written to {out}. Fill true_price (or none), true_currency and true_in_stock (yes or no).")


@app.command("eval-prices")
def eval_prices(labels: Path = Path("data/eval/price_labels.csv")) -> None:
    """Measure price and stock extraction against your labels and save the numbers."""
    rows = read_labels(labels)
    result = evaluate_prices(rows, extract_from_snapshot)
    summary = {"extractor": "selectors", "run_at": datetime.now(timezone.utc).isoformat(), **result.summary()}
    out = ROOT / "eval" / "results" / f"prices-{datetime.now():%Y%m%d-%H%M}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    typer.echo(json.dumps(summary, indent=2))
    for failure in result.failures:
        typer.echo(f"wrong: {failure}")
    typer.echo(f"saved {out}")
```

Create `eval/results/.gitkeep` (empty).

- [ ] **Step 6: Build the labelled set (Zheyun)**

Crawl about 25 fridge pages with screenshots. Use a few different searches, so that the set has variety (discounted, out of stock, several variants):

```bash
scout crawl fridge "kylskåp" --details 10 --screenshots
scout crawl fridge "kyl och frys" --details 10 --screenshots
scout crawl fridge "minikylskåp" --details 5 --screenshots
scout eval-template
```

Open `data/eval/price_labels.csv` in Excel or a text editor. For each row, open the `.png` screenshot and fill in these columns:
- `true_price`: the price that you pay, as `8990.00`. Write `none` if the page shows no price
- `true_currency`: `SEK`
- `true_in_stock`: `yes` or `no`
- `note`: anything unusual, for example "price range" or "only used offers"

Use only the screenshot for the labels. Do not look at the output of the parser.

- [ ] **Step 7: Run the evaluation**

Run: `scout eval-prices`
Expected: a JSON summary with real counts and accuracies, a list of wrong pages, and a results file under `eval/results/`. If a failure occurs on many pages, repair the selectors (with a new fixture case and test) and run again. Keep the two runs in `eval/results/`.

- [ ] **Step 8: Commit**

```bash
git add src/scout/eval src/scout/cli.py tests/test_eval_prices.py eval/results
git commit -m "Price and stock extraction evaluation on hand-labelled amazon.se snapshots"
```

---

### Task 11: LLM layer with chat and worker roles

**Files:**
- Create: `config/models.yaml`, `src/scout/llm/__init__.py`, `src/scout/llm/config.py`, `src/scout/llm/ratelimit.py`, `src/scout/llm/client.py`, `tests/test_llm.py`
- Modify: `src/scout/cli.py` (add `llm-ping`)

**Interfaces:**
- Consumes: `ROOT` (Task 1).
- Produces:
  - `LLMConfig(providers: dict[str, ProviderConfig], roles: dict[str, RoleConfig])`, `load_llm_config(path: Path = ROOT / "config" / "models.yaml") -> LLMConfig`
  - `RateLimiter(rpm: int, sleep=time.sleep, clock=time.monotonic).wait()`
  - `LLM(config, clients: dict[str, Any] | None = None, sleep=time.sleep, clock=time.monotonic)` with `structured(role: Literal["chat", "worker"], system: str, user: str, schema: type[T]) -> T`, `model_for(role) -> str`, `client(provider) -> OpenAI`, `calls: list[CallRecord]`
  - `QuotaExhausted` (the daily quota is finished, or the rate limit continues after all retries), `LLMOutputError`, `inline_refs(schema: dict) -> dict`, `strip_fences(text: str) -> str`
  - If the endpoint refuses the JSON schema (HTTP 400), the call tries again one time in JSON mode, with the schema in the system prompt.

Prerequisite: Zheyun puts `GEMINI_API_KEY=...` in `.env` (a free key from https://aistudio.google.com/apikey, no billing).

- [ ] **Step 1: Write the model config**

`config/models.yaml`:

```yaml
# Which model each role uses. Check names and free-tier limits when the key is created:
# https://ai.google.dev/gemini-api/docs/models
# https://ai.google.dev/gemini-api/docs/rate-limits
providers:
  gemini:
    base_url: https://generativelanguage.googleapis.com/v1beta/openai/
    api_key_env: GEMINI_API_KEY
roles:
  chat:
    provider: gemini
    model: gemini-2.5-flash
    rpm: 10
  worker:
    provider: gemini
    model: gemini-2.5-flash-lite
    rpm: 15
```

- [ ] **Step 2: Write the failing tests**

`tests/test_llm.py`:

```python
from types import SimpleNamespace

import httpx
import pytest
from openai import BadRequestError, RateLimitError
from pydantic import BaseModel, ValidationError

from scout.llm.client import LLM, LLMOutputError, QuotaExhausted, inline_refs, strip_fences
from scout.llm.config import LLMConfig, load_llm_config
from scout.llm.ratelimit import RateLimiter


class Inner(BaseModel):
    name: str


class Outer(BaseModel):
    items: list[Inner]


class Answer(BaseModel):
    answer: str


class FakeCompletions:
    def __init__(self, replies):
        self.replies = list(replies)
        self.requests = []

    def create(self, **kwargs):
        self.requests.append(kwargs)
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=reply))],
            usage=SimpleNamespace(prompt_tokens=10, completion_tokens=5),
        )


class FakeOpenAI:
    def __init__(self, replies):
        self.chat = SimpleNamespace(completions=FakeCompletions(replies))


class FakeClock:
    def __init__(self):
        self.now = 0.0
        self.slept = []

    def clock(self):
        return self.now

    def sleep(self, seconds):
        self.slept.append(seconds)
        self.now += seconds


CONFIG = LLMConfig.model_validate({
    "providers": {"gemini": {"base_url": "https://example.test/", "api_key_env": "GEMINI_API_KEY"}},
    "roles": {
        "chat": {"provider": "gemini", "model": "strong-model", "rpm": 60},
        "worker": {"provider": "gemini", "model": "light-model", "rpm": 60},
    },
})


def rate_limited(message):
    response = httpx.Response(429, request=httpx.Request("POST", "https://example.test/"))
    return RateLimitError(message, response=response, body=None)


def make(replies):
    fake, clock = FakeOpenAI(replies), FakeClock()
    return LLM(CONFIG, clients={"gemini": fake}, sleep=clock.sleep, clock=clock.clock), fake, clock


def test_structured_reply_uses_the_role_model():
    llm, fake, _ = make(['{"answer": "pong"}'])
    assert llm.structured("worker", "sys", "user", Answer) == Answer(answer="pong")
    assert fake.chat.completions.requests[0]["model"] == "light-model"
    assert llm.calls[0].input_tokens == 10


def test_fenced_json_is_accepted():
    llm, _, _ = make(['```json\n{"answer": "pong"}\n```'])
    assert llm.structured("chat", "sys", "user", Answer).answer == "pong"


def test_invalid_reply_is_retried_with_feedback():
    llm, fake, _ = make(['{"wrong": 1}', '{"answer": "pong"}'])
    assert llm.structured("chat", "sys", "user", Answer).answer == "pong"
    second = fake.chat.completions.requests[1]["messages"]
    assert second[-1]["role"] == "user" and "did not match" in second[-1]["content"]


def test_three_invalid_replies_raise():
    llm, _, _ = make(["nope", "nope", "nope"])
    with pytest.raises(LLMOutputError):
        llm.structured("chat", "sys", "user", Answer)


def test_per_minute_limit_waits_and_retries():
    llm, _, clock = make([rate_limited("GenerateRequestsPerMinutePerProjectPerModel-FreeTier"), '{"answer": "ok"}'])
    assert llm.structured("worker", "sys", "user", Answer).answer == "ok"
    assert 15 in clock.slept


def test_daily_limit_raises_quota_exhausted():
    llm, _, _ = make([rate_limited("GenerateRequestsPerDayPerProjectPerModel-FreeTier")])
    with pytest.raises(QuotaExhausted):
        llm.structured("worker", "sys", "user", Answer)


def test_rate_limited_on_every_try_also_raises_quota_exhausted():
    minute = "GenerateRequestsPerMinutePerProjectPerModel-FreeTier"
    llm, _, _ = make([rate_limited(minute), rate_limited(minute), rate_limited(minute)])
    with pytest.raises(QuotaExhausted, match="still rate limited"):
        llm.structured("worker", "sys", "user", Answer)


def test_rejected_schema_falls_back_to_json_mode():
    bad = BadRequestError("Invalid JSON schema", response=httpx.Response(
        400, request=httpx.Request("POST", "https://example.test/")), body=None)
    llm, fake, _ = make([bad, '{"answer": "pong"}'])
    assert llm.structured("worker", "sys", "user", Answer).answer == "pong"
    retry = fake.chat.completions.requests[1]
    assert retry["response_format"] == {"type": "json_object"}
    assert "schema" in retry["messages"][0]["content"]


def test_missing_key_names_the_variable(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    llm = LLM(CONFIG)
    with pytest.raises(RuntimeError, match="GEMINI_API_KEY"):
        llm.structured("chat", "sys", "user", Answer)


def test_inline_refs_removes_refs():
    schema = inline_refs(Outer.model_json_schema())
    assert "$defs" not in schema
    assert schema["properties"]["items"]["items"]["properties"]["name"]["type"] == "string"


def test_strip_fences():
    assert strip_fences('```json\n{"a": 1}\n```') == '{"a": 1}'
    assert strip_fences('{"a": 1}') == '{"a": 1}'


def test_rate_limiter_spaces_calls():
    clock = FakeClock()
    limiter = RateLimiter(30, sleep=clock.sleep, clock=clock.clock)
    limiter.wait()
    limiter.wait()
    assert clock.slept == [pytest.approx(2.0)]


def test_config_needs_both_roles_and_known_providers():
    with pytest.raises(ValidationError):
        LLMConfig.model_validate({"providers": {}, "roles": {"chat": {"provider": "x", "model": "m", "rpm": 1}}})


def test_shipped_config_loads():
    config = load_llm_config()
    assert {"chat", "worker"} <= set(config.roles)
```

- [ ] **Step 3: Run the tests to see them fail**

Run: `python -m pytest tests/test_llm.py -v`
Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 4: Write the LLM layer**

`src/scout/llm/__init__.py`:

```python
"""Provider-neutral LLM calls with two roles, chat and worker."""
```

`src/scout/llm/config.py`:

```python
"""config/models.yaml: providers and the model each role uses."""
from pathlib import Path

import yaml
from pydantic import BaseModel, Field, model_validator

from scout.config import ROOT

DEFAULT_PATH = ROOT / "config" / "models.yaml"


class ProviderConfig(BaseModel):
    base_url: str
    api_key_env: str


class RoleConfig(BaseModel):
    provider: str
    model: str
    rpm: int = Field(gt=0)


class LLMConfig(BaseModel):
    providers: dict[str, ProviderConfig]
    roles: dict[str, RoleConfig]

    @model_validator(mode="after")
    def _check(self) -> "LLMConfig":
        for needed in ("chat", "worker"):
            if needed not in self.roles:
                raise ValueError(f"models.yaml needs a '{needed}' role")
        for name, role in self.roles.items():
            if role.provider not in self.providers:
                raise ValueError(f"role '{name}' uses unknown provider '{role.provider}'")
        return self


def load_llm_config(path: Path = DEFAULT_PATH) -> LLMConfig:
    return LLMConfig.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))
```

`src/scout/llm/ratelimit.py`:

```python
"""Keep requests under a requests-per-minute limit."""
import time


class RateLimiter:
    def __init__(self, rpm: int, sleep=time.sleep, clock=time.monotonic):
        self.interval = 60.0 / rpm
        self._sleep = sleep
        self._clock = clock
        self._last: float | None = None

    def wait(self) -> None:
        if self._last is not None:
            remaining = self._last + self.interval - self._clock()
            if remaining > 0:
                self._sleep(remaining)
        self._last = self._clock()
```

`src/scout/llm/client.py`:

```python
"""One calling interface for OpenAI-compatible endpoints (Gemini first) with structured output."""
import json
import os
import time
from dataclasses import dataclass
from typing import Any, Literal, TypeVar

from openai import BadRequestError, OpenAI, RateLimitError
from pydantic import BaseModel, ValidationError

from scout.llm.config import LLMConfig
from scout.llm.ratelimit import RateLimiter

Role = Literal["chat", "worker"]
T = TypeVar("T", bound=BaseModel)


class QuotaExhausted(RuntimeError):
    """The provider's daily free quota is used up."""


class LLMOutputError(RuntimeError):
    """The model did not return output that matches the schema."""


@dataclass
class CallRecord:
    role: str
    model: str
    input_tokens: int
    output_tokens: int
    seconds: float


def inline_refs(schema: dict) -> dict:
    """Replace $ref with the referenced definition, since some endpoints reject $ref."""
    defs = schema.get("$defs", {})

    def resolve(node):
        if isinstance(node, dict):
            if "$ref" in node:
                return resolve(defs[node["$ref"].split("/")[-1]])
            return {key: resolve(value) for key, value in node.items() if key != "$defs"}
        if isinstance(node, list):
            return [resolve(item) for item in node]
        return node

    return resolve(schema)


def strip_fences(text: str) -> str:
    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = stripped.split("\n", 1)[1] if "\n" in stripped else ""
        stripped = stripped.rstrip()
        if stripped.endswith("```"):
            stripped = stripped[:-3]
    return stripped.strip()


class LLM:
    MAX_ATTEMPTS = 3

    def __init__(self, config: LLMConfig, clients: dict[str, Any] | None = None, sleep=time.sleep, clock=time.monotonic):
        self.config = config
        self._clients = dict(clients or {})
        self._sleep = sleep
        self._clock = clock
        self._limiters = {name: RateLimiter(role.rpm, sleep, clock) for name, role in config.roles.items()}
        self.calls: list[CallRecord] = []

    def model_for(self, role: Role) -> str:
        return self.config.roles[role].model

    def client(self, provider: str):
        if provider not in self._clients:
            settings = self.config.providers[provider]
            key = os.environ.get(settings.api_key_env)
            if not key:
                raise RuntimeError(f"{settings.api_key_env} is not set. Put it in .env in the project root.")
            self._clients[provider] = OpenAI(base_url=settings.base_url, api_key=key)
        return self._clients[provider]

    def structured(self, role: Role, system: str, user: str, schema: type[T]) -> T:
        role_cfg = self.config.roles[role]
        client = self.client(role_cfg.provider)
        schema_dict = inline_refs(schema.model_json_schema())
        messages = [{"role": "system", "content": system}, {"role": "user", "content": user}]
        response_format = {"type": "json_schema", "json_schema": {"name": schema.__name__, "schema": schema_dict}}
        last_error = ""
        for attempt in range(1, self.MAX_ATTEMPTS + 1):
            self._limiters[role].wait()
            started = self._clock()
            try:
                response = client.chat.completions.create(
                    model=role_cfg.model, messages=messages, response_format=response_format
                )
            except RateLimitError as exc:
                if "PerDay" in str(exc):
                    raise QuotaExhausted(
                        f"The free daily quota for {role_cfg.model} is used up. It resets tomorrow."
                    ) from exc
                last_error = "rate limited"
                self._sleep(15 * attempt)
                continue
            except BadRequestError as exc:
                if response_format["type"] == "json_object":
                    raise
                # Some endpoints reject parts of a JSON schema. Fall back to JSON mode with the schema in the prompt.
                response_format = {"type": "json_object"}
                messages = [{"role": "system", "content": f"{system}\n\nReply with JSON that matches this "
                                                          f"schema:\n{json.dumps(schema_dict)}"}] + messages[1:]
                last_error = str(exc)
                continue
            text = response.choices[0].message.content or ""
            usage = getattr(response, "usage", None)
            self.calls.append(CallRecord(
                role, role_cfg.model,
                getattr(usage, "prompt_tokens", 0) or 0,
                getattr(usage, "completion_tokens", 0) or 0,
                self._clock() - started,
            ))
            try:
                return schema.model_validate_json(strip_fences(text))
            except ValidationError as exc:
                last_error = str(exc)
                messages = messages + [
                    {"role": "assistant", "content": text},
                    {"role": "user", "content": f"That reply did not match the JSON schema: {exc.errors()[:3]}. "
                                                "Reply again with JSON only."},
                ]
        if last_error == "rate limited":
            raise QuotaExhausted(f"{role_cfg.model} is still rate limited after {self.MAX_ATTEMPTS} tries.")
        raise LLMOutputError(
            f"{role_cfg.model} gave no valid {schema.__name__} after {self.MAX_ATTEMPTS} tries: {last_error}"
        )
```

- [ ] **Step 5: Run the tests to see them pass**

Run: `python -m pytest tests/test_llm.py -v`
Expected: all passed.

- [ ] **Step 6: Add `llm-ping` and check the real key**

Add to `src/scout/cli.py`:

```python
from pydantic import BaseModel

from scout.llm.client import LLM
from scout.llm.config import load_llm_config


class _PingItem(BaseModel):
    name: str
    value: str | None = None


class _Ping(BaseModel):
    items: list[_PingItem]


@app.command("llm-ping")
def llm_ping() -> None:
    """Check the API key, list the flash models it can use, and make one small structured call per role."""
    load_settings()  # loads .env
    config = load_llm_config()
    llm = LLM(config)
    for name in config.providers:
        ids = sorted(model.id for model in llm.client(name).models.list())
        typer.echo(f"{name}: " + ", ".join(i for i in ids if "flash" in i))
    for role in ("chat", "worker"):
        # Nested models and an optional field, the same shapes the extraction and picks schemas use.
        reply = llm.structured(role, "Reply in JSON.",
                               "Return two items: name ping with value pong, and name empty with value null.", _Ping)
        typer.echo(f"{role} ({llm.model_for(role)}): {[(i.name, i.value) for i in reply.items]}")
```

Run: `scout llm-ping`
Expected: a list of the available Flash models. Then, for each role, `[('ping', 'pong'), ('empty', None)]`. If Gemini refuses the JSON schema, the client changes to JSON mode automatically, so this check still passes.
If the model names in `config/models.yaml` are not in the list, replace them with the current Flash and Flash-Lite names (without the `models/` prefix). Set the `rpm` of each role to the free-tier limit at https://ai.google.dev/gemini-api/docs/rate-limits or in AI Studio. Run `scout llm-ping` again.

- [ ] **Step 7: Commit**

```bash
git add config/models.yaml src/scout/llm src/scout/cli.py tests/test_llm.py
git commit -m "Provider-neutral LLM layer with chat and worker roles, rate limits and quota handling"
```

---

### Task 12: Fridge category profile

**Files:**
- Create: `profiles/fridge.yaml`, `src/scout/profiles.py`, `tests/test_profiles.py`

**Interfaces:**
- Consumes: `ROOT` (Task 1).
- Produces: `ProfileAttribute(name, label, type: Literal["number", "enum", "boolean", "text"], kind: Literal["hard", "soft"], unit: str | None, values: list[str] | None)`, `CategoryProfile` with `attribute(name) -> ProfileAttribute | None`, `load_profile(category: str, profiles_dir: Path = PROFILES_DIR) -> CategoryProfile`.

- [ ] **Step 1: Write the profile**

`profiles/fridge.yaml`:

```yaml
category: fridge
display_name: Fridge
attributes:
  - name: height
    label: Height
    type: number
    unit: cm
    kind: hard
  - name: width
    label: Width
    type: number
    unit: cm
    kind: hard
  - name: depth
    label: Depth
    type: number
    unit: cm
    kind: hard
  - name: total_volume
    label: Total volume
    type: number
    unit: l
    kind: soft
  - name: freezer_volume
    label: Freezer volume
    type: number
    unit: l
    kind: soft
  - name: noise
    label: Noise
    type: number
    unit: dB
    kind: soft
  - name: energy_class
    label: Energy class
    type: enum
    values: [A, B, C, D, E, F, G]
    kind: soft
  - name: no_frost
    label: No Frost
    type: boolean
    kind: soft
questions_for_user:
  - How wide, tall and deep is the space for the fridge?
  - What is your budget?
  - Do you need a freezer part, and how big?
  - Do you care about noise?
complaint_types: [noise, frost, door seal, compressor life, after-sales service]
sources:
  products: [amazon_se]
  reviews: [amazon_se_reviews]
```

- [ ] **Step 2: Write the failing tests**

`tests/test_profiles.py`:

```python
import pytest
from pydantic import ValidationError

from scout.profiles import CategoryProfile, load_profile


def test_fridge_profile_loads():
    profile = load_profile("fridge")
    assert profile.attribute("height").unit == "cm"
    assert profile.attribute("height").kind == "hard"
    assert profile.attribute("energy_class").values[0] == "A"
    assert profile.attribute("colour") is None


def test_unknown_category_lists_what_exists():
    with pytest.raises(FileNotFoundError, match="fridge"):
        load_profile("spaceship")


def base(attributes):
    return {"category": "x", "display_name": "X", "attributes": attributes,
            "questions_for_user": [], "sources": {"products": ["amazon_se"]}}


def test_enum_needs_values():
    with pytest.raises(ValidationError):
        CategoryProfile.model_validate(base([{"name": "c", "label": "C", "type": "enum", "kind": "soft"}]))


def test_attribute_names_must_be_unique():
    attr = {"name": "h", "label": "H", "type": "number", "unit": "cm", "kind": "hard"}
    with pytest.raises(ValidationError):
        CategoryProfile.model_validate(base([attr, attr]))
```

- [ ] **Step 3: Run the tests to see them fail**

Run: `python -m pytest tests/test_profiles.py -v`
Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 4: Write the loader**

`src/scout/profiles.py`:

```python
"""Category profiles: common conditions, questions to ask, where to look."""
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field, model_validator

from scout.config import ROOT

PROFILES_DIR = ROOT / "profiles"


class ProfileAttribute(BaseModel):
    name: str
    label: str
    type: Literal["number", "enum", "boolean", "text"]
    kind: Literal["hard", "soft"]
    unit: str | None = None
    values: list[str] | None = None

    @model_validator(mode="after")
    def _enum_values(self) -> "ProfileAttribute":
        if self.type == "enum" and not self.values:
            raise ValueError(f"enum attribute '{self.name}' needs values")
        return self


class CategoryProfile(BaseModel):
    category: str
    display_name: str
    attributes: list[ProfileAttribute]
    questions_for_user: list[str]
    complaint_types: list[str] = Field(default_factory=list)
    sources: dict[str, list[str]]

    @model_validator(mode="after")
    def _unique_names(self) -> "CategoryProfile":
        names = [a.name for a in self.attributes]
        if len(names) != len(set(names)):
            raise ValueError("attribute names must be unique")
        return self

    def attribute(self, name: str) -> ProfileAttribute | None:
        return next((a for a in self.attributes if a.name == name), None)


def load_profile(category: str, profiles_dir: Path = PROFILES_DIR) -> CategoryProfile:
    path = profiles_dir / f"{category}.yaml"
    if not path.exists():
        available = ", ".join(sorted(p.stem for p in profiles_dir.glob("*.yaml"))) or "none"
        raise FileNotFoundError(f"No profile for '{category}'. Available: {available}")
    return CategoryProfile.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))
```

- [ ] **Step 5: Run the tests to see them pass**

Run: `python -m pytest tests/test_profiles.py -v`
Expected: 4 passed.

- [ ] **Step 6: Commit**

```bash
git add profiles src/scout/profiles.py tests/test_profiles.py
git commit -m "Fridge category profile and profile loader"
```

---

### Task 13: Worker title screening and extraction with raw-text checks

**Files:**
- Create: `src/scout/extract/__init__.py`, `src/scout/extract/verify.py`, `src/scout/extract/page.py`, `src/scout/extract/screen.py`, `tests/test_extract.py`, `tests/test_screen.py`
- Modify: `tests/fakes.py` (add `FakeLLM`), `src/scout/db/store.py` (add `stored_attributes`), `tests/test_store.py`, `src/scout/crawl.py` (title screening, reuse stored attributes, LLM errors), `tests/test_crawl.py`, `src/scout/cli.py` (`crawl --no-llm`, `eval-prices --llm`)

**Interfaces:**
- Consumes: `ProductRecord`, `Candidate` (Task 3), `to_decimal`, `parse_price` (Task 3), `LLM.structured`, `QuotaExhausted`, `LLMOutputError` (Task 11), `CategoryProfile`, `ProfileAttribute` (Task 12), `crawl` (Task 9).
- Produces: `verify_raw(raw: str, source: str) -> bool`; `NamedValue(name, value: str | None, raw: str | None)`; `PageExtraction(values: list[NamedValue])`; `enrich_record(rec, profile, llm) -> tuple[ProductRecord, bool]`; `screen_titles(cands: list[Candidate], profile, llm) -> list[Candidate]`; `store.stored_attributes(conn, source, source_id) -> dict`; `crawl(..., screen: Screener | None = None)` with `Screener = Callable[[list[Candidate]], list[Candidate]]` and a new `CrawlReport.off_topic: int`. Stored attribute format: `{"value": ..., "unit": ..., "raw": ..., "source_url": ...}` (the `source_chunk_id` of the spec comes with chunks in phase 3).

Why title screening is in this task and not in phase 5. A search for "kylskåp" also shows thermometers, shelves and door seals. Without a relevance filter, `picks` with no conditions can put a well-reviewed accessory first. One worker call for each crawl reads all candidate titles and keeps only the real products. This also prevents unnecessary fetches of detail pages. This is the first pass of the multi-stage ranking in the spec (4.3), moved forward.

- [ ] **Step 1: Add the fake LLM**

Append to `tests/fakes.py`:

```python
class FakeLLM:
    """Returns queued replies (or raises queued exceptions) and records the prompts."""

    def __init__(self, replies):
        self.replies = list(replies)
        self.requests = []

    def structured(self, role, system, user, schema):
        self.requests.append((role, user))
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply

    def model_for(self, role):
        return f"fake-{role}"
```

- [ ] **Step 2: Write the failing tests**

`tests/test_extract.py`:

```python
from datetime import datetime, timezone
from decimal import Decimal

from scout.extract.page import NamedValue, PageExtraction, enrich_record
from scout.extract.verify import verify_raw
from scout.profiles import load_profile
from scout.sources.base import ProductRecord
from tests.fakes import FakeLLM

NOW = datetime(2026, 10, 3, 10, 15, tzinfo=timezone.utc)
TEXT = "Bosch KGN39 kylskåp\nPris: 4\u00a0999,00 kr\nHöjd: 1780 mm\nEnergieffektivitetsklass: E"
PROFILE = load_profile("fridge")


def record(**changes):
    base = ProductRecord(source="amazon_se", source_id="B0AAAAAAA2", url="https://www.amazon.se/dp/B0AAAAAAA2",
                         fetched_at=NOW, title="Bosch KGN39 kylskåp", brand="Bosch", model="KGN39",
                         in_stock=True, text=TEXT)
    return base.model_copy(update=changes)


def reply(*values):
    return PageExtraction(values=[NamedValue(name=n, value=v, raw=r) for n, v, r in values])


def test_verify_raw_ignores_spacing_and_case():
    assert verify_raw("4 999,00 KR", "Pris: 4\u00a0999,00 kr")
    assert not verify_raw("35 dB", TEXT)
    assert not verify_raw("", TEXT)


def test_fills_missing_price_and_attributes_with_checked_raw_text():
    llm = FakeLLM([reply(("price", "4999.00", "4 999,00 kr"),
                         ("height", "178", "1780 mm"),
                         ("energy_class", "E", "Energieffektivitetsklass: E"))])
    rec, called = enrich_record(record(), PROFILE, llm)
    assert called
    assert (rec.price, rec.currency, rec.llm_fields) == (Decimal("4999.00"), "SEK", ["price"])
    assert rec.attributes["height"] == {"value": 178.0, "unit": "cm", "raw": "1780 mm",
                                        "source_url": "https://www.amazon.se/dp/B0AAAAAAA2"}
    assert rec.attributes["energy_class"]["value"] == "E"


def test_value_whose_raw_text_is_not_on_the_page_is_dropped():
    llm = FakeLLM([reply(("noise", "35", "35 dB"))])
    rec, _ = enrich_record(record(), PROFILE, llm)
    assert "noise" not in rec.attributes


def test_price_that_differs_from_its_raw_text_is_dropped():
    llm = FakeLLM([reply(("price", "3999", "4 999,00 kr"))])
    rec, _ = enrich_record(record(), PROFILE, llm)
    assert rec.price is None and rec.llm_fields == []


def test_enum_value_outside_the_profile_is_dropped():
    llm = FakeLLM([reply(("energy_class", "A+++", "Energieffektivitetsklass: E"))])
    rec, _ = enrich_record(record(), PROFILE, llm)
    assert "energy_class" not in rec.attributes


def test_prompt_asks_only_for_what_is_missing_and_uses_the_worker():
    llm = FakeLLM([reply()])
    enrich_record(record(), PROFILE, llm)
    role, prompt = llm.requests[0]
    assert role == "worker"
    assert "- price:" in prompt and "- brand:" not in prompt
    assert "- height: Height, number in cm" in prompt


def test_no_call_when_nothing_is_missing():
    full = {a.name: {"value": 1} for a in PROFILE.attributes}
    llm = FakeLLM([])
    rec, called = enrich_record(record(price=Decimal("4999"), attributes=full), PROFILE, llm)
    assert not called and llm.requests == []
```

Append to `tests/test_crawl.py`:

```python
from scout.llm.client import QuotaExhausted


def test_quota_running_out_keeps_storing_without_the_llm(db, tmp_path):
    calls = []

    def enrich(rec):
        calls.append(rec.source_id)
        raise QuotaExhausted("The free daily quota for light-model is used up. It resets tomorrow.")

    report = crawl(db, AmazonSeAdapter(site(), tmp_path), "fridge", "kylskåp",
                   on_problem=never_called, enrich=enrich)
    assert report.saved == 3
    assert len(calls) == 1
    assert any("quota" in note for note in report.notes)


def test_bad_llm_output_on_one_page_does_not_stop_the_crawl(db, tmp_path):
    from scout.llm.client import LLMOutputError

    def enrich(rec):
        if rec.source_id == "B0AAAAAAA1":
            raise LLMOutputError("no valid PageExtraction after 3 tries")
        return rec, True

    report = crawl(db, AmazonSeAdapter(site(), tmp_path), "fridge", "kylskåp",
                   on_problem=never_called, enrich=enrich)
    assert (report.saved, report.llm_pages) == (3, 2)
    assert any("B0AAAAAAA1" in note for note in report.notes)


def test_stored_attributes_are_not_extracted_again(db, tmp_path):
    seen = []

    def enrich(rec):
        seen.append(dict(rec.attributes))
        return rec.model_copy(update={"attributes": {"height": {"value": 203.0}}}), True

    crawl(db, AmazonSeAdapter(site(), tmp_path), "fridge", "kylskåp", on_problem=never_called, enrich=enrich)
    seen.clear()
    crawl(db, AmazonSeAdapter(site(), tmp_path), "fridge", "kylskåp", on_problem=never_called, enrich=enrich)
    assert all("height" in attrs for attrs in seen)


def test_screening_drops_off_topic_candidates_before_fetching(db, tmp_path):
    browser = site()
    report = crawl(db, AmazonSeAdapter(browser, tmp_path), "fridge", "kylskåp", on_problem=never_called,
                   screen=lambda cands: [c for c in cands if c.source_id != "B0AAAAAAA3"])
    assert (report.off_topic, report.saved) == (1, 2)
    assert product_url("B0AAAAAAA3") not in browser.fetched
```

Append to `tests/test_store.py`:

```python
from scout.db.store import stored_attributes


def test_stored_attributes(db):
    rec = record().model_copy(update={"attributes": {"height": {"value": 203.0, "unit": "cm", "raw": "203 cm"}}})
    save_record(db, rec, "fridge")
    assert stored_attributes(db, "amazon_se", "B0AAAAAAA1")["height"]["value"] == 203.0
    assert stored_attributes(db, "amazon_se", "B0NOTSTORED") == {}
```

`tests/test_screen.py`:

```python
from scout.extract.screen import Keep, screen_titles
from scout.profiles import load_profile
from scout.sources.base import Candidate
from tests.fakes import FakeLLM


def cand(n, title):
    return Candidate(source="amazon_se", source_id=f"B0AAAAAAA{n}", url=f"https://www.amazon.se/dp/B0AAAAAAA{n}",
                     title=title)


def test_keeps_only_the_numbers_the_worker_returns():
    cands = [cand(1, "Samsung kyl/frys"), cand(2, "Kylskåpstermometer"), cand(3, "Bosch kylskåp")]
    llm = FakeLLM([Keep(keep=[0, 2, 9])])
    kept = screen_titles(cands, load_profile("fridge"), llm)
    assert [c.source_id for c in kept] == ["B0AAAAAAA1", "B0AAAAAAA3"]
    role, prompt = llm.requests[0]
    assert role == "worker"
    assert "1: Kylskåpstermometer" in prompt and "Fridge" in prompt
```

- [ ] **Step 3: Run the tests to see them fail**

Run: `python -m pytest tests/test_extract.py tests/test_screen.py tests/test_crawl.py tests/test_store.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'scout.extract'`. The new crawl and store tests fail, because `screen`, `stored_attributes` and the error handling do not exist yet.

- [ ] **Step 4: Write the extraction code**

`src/scout/extract/__init__.py`:

```python
"""LLM extraction with raw-text checks."""
```

`src/scout/extract/verify.py`:

```python
"""Check that a value's raw text really appears in the source."""
_INVISIBLE = dict.fromkeys(map(ord, "\u200e\u200f\u200b"), None)


def normalise(text: str) -> str:
    return " ".join(text.translate(_INVISIBLE).split()).casefold()


def verify_raw(raw: str, source: str) -> bool:
    needle = normalise(raw)
    return bool(needle) and needle in normalise(source)
```

`src/scout/extract/page.py`:

```python
"""Ask the worker model for fields the selectors missed, keep only values whose raw text checks out."""
from pydantic import BaseModel

from scout.extract.verify import verify_raw
from scout.profiles import CategoryProfile, ProfileAttribute
from scout.sources.amazon_se.numbers import parse_price, to_decimal
from scout.sources.base import ProductRecord

SYSTEM = """You read one product page from amazon.se and pull out the requested fields.
Rules:
- Use only the page text you are given. Never guess.
- For each field, copy into "raw" the exact short piece of page text the value comes from, character for character.
- If the page does not state a field, set value and raw to null.
- Give numbers with a dot as decimal separator, converted to the unit asked for. 1780 mm is 178 when the unit is cm.
- For yes or no fields answer "true" or "false"."""

CORE = {
    "title": "the product title",
    "brand": "the brand name",
    "model": "the manufacturer's model number",
    "price": "the price you pay now for this item, in SEK, as a number",
}


class NamedValue(BaseModel):
    name: str
    value: str | None = None
    raw: str | None = None


class PageExtraction(BaseModel):
    values: list[NamedValue]


def _describe(attr: ProfileAttribute) -> str:
    if attr.type == "number":
        return f"{attr.label}, number in {attr.unit}" if attr.unit else f"{attr.label}, number"
    if attr.type == "enum":
        return f"{attr.label}, one of {', '.join(attr.values or [])}"
    if attr.type == "boolean":
        return f"{attr.label}, true or false"
    return f"{attr.label}, text"


def build_prompt(rec: ProductRecord, core: list[str], attrs: list[ProfileAttribute]) -> str:
    lines = ["Fields:"]
    lines += [f"- {name}: {CORE[name]}" for name in core]
    lines += [f"- {attr.name}: {_describe(attr)}" for attr in attrs]
    lines += ["", "Page text:", rec.text]
    return "\n".join(lines)


def convert(attr: ProfileAttribute, value: str):
    text = value.strip()
    if attr.type == "number":
        number = to_decimal(text)
        return float(number) if number is not None else None
    if attr.type == "enum":
        return next((option for option in attr.values or [] if option.casefold() == text.casefold()), None)
    if attr.type == "boolean":
        return {"true": True, "yes": True, "ja": True, "false": False, "no": False, "nej": False}.get(text.casefold())
    return text or None


def enrich_record(rec: ProductRecord, profile: CategoryProfile, llm) -> tuple[ProductRecord, bool]:
    core = [name for name in CORE if getattr(rec, name) is None]
    attrs = [attr for attr in profile.attributes if attr.name not in rec.attributes]
    if not rec.text or (not core and not attrs):
        return rec, False
    result = llm.structured("worker", SYSTEM, build_prompt(rec, core, attrs), PageExtraction)
    updates: dict = {}
    attributes = dict(rec.attributes)
    llm_fields = list(rec.llm_fields)
    wanted = {attr.name: attr for attr in attrs}
    for item in result.values:
        if item.value is None or not item.raw or not verify_raw(item.raw, rec.text):
            continue
        if item.name in core:
            if item.name == "price":
                price = to_decimal(item.value)
                if price is None or parse_price(item.raw)[0] != price:
                    continue
                updates.update(price=price, currency="SEK")
            else:
                updates[item.name] = item.value.strip()
            llm_fields.append(item.name)
        elif item.name in wanted:
            attr = wanted[item.name]
            value = convert(attr, item.value)
            if value is None:
                continue
            attributes[attr.name] = {"value": value, "unit": attr.unit, "raw": item.raw, "source_url": rec.url}
    return rec.model_copy(update={**updates, "attributes": attributes, "llm_fields": llm_fields}), True
```

`src/scout/extract/screen.py`:

```python
"""Keep only search results that are the product itself, judged from the titles in one worker call."""
from pydantic import BaseModel

from scout.profiles import CategoryProfile
from scout.sources.base import Candidate

SYSTEM = """You sort search results from amazon.se for a shopper.
Keep only items that are the product the shopper is looking for itself.
Drop accessories, spare parts, covers, cleaning products and unrelated items.
When unsure, keep the item."""


class Keep(BaseModel):
    keep: list[int]


def screen_titles(cands: list[Candidate], profile: CategoryProfile, llm) -> list[Candidate]:
    lines = "\n".join(f"{n}: {c.title}" for n, c in enumerate(cands))
    user = f"The shopper wants: {profile.display_name}\n\nResults:\n{lines}\n\nReturn the numbers to keep."
    wanted = set(llm.structured("worker", SYSTEM, user, Keep).keep)
    return [c for n, c in enumerate(cands) if n in wanted]
```

Add to `src/scout/db/store.py`:

```python
def stored_attributes(conn: psycopg.Connection, source: str, source_id: str) -> dict:
    """Attributes already stored for this listing's product, so they are not extracted again."""
    row = conn.execute(
        """SELECT p.attributes FROM listings l JOIN products p ON p.id = l.product_id
           WHERE l.source = %s AND l.source_id = %s""",
        (source, source_id),
    ).fetchone()
    return dict(row["attributes"]) if row else {}
```

In `src/scout/crawl.py`:

1. Change the imports and add the screener type:

```python
from scout.db.store import log_fetch, save_record, stored_attributes
from scout.llm.client import LLMOutputError, QuotaExhausted
from scout.sources.base import Candidate, ProductRecord, RawPage, SessionStatus

Screener = Callable[[list[Candidate]], list[Candidate]]
```

2. Add `off_topic: int = 0` to `CrawlReport`, and `screen: Screener | None = None` as the last keyword argument of `crawl`.

3. Immediately after the budget filter, before the detail loop:

```python
    if screen is not None and candidates:
        try:
            kept = screen(candidates)
            report.off_topic = len(candidates) - len(kept)
            candidates = kept
        except (QuotaExhausted, LLMOutputError) as exc:
            report.notes.append(f"Title screening skipped: {exc}")
```

4. Replace the enrich block inside the detail loop:

```python
        if enrich is not None:
            stored = stored_attributes(conn, rec.source, rec.source_id)
            rec = rec.model_copy(update={"attributes": {**stored, **rec.attributes}})
            try:
                rec, called = enrich(rec)
                report.llm_pages += int(called)
            except QuotaExhausted as exc:
                report.notes.append(f"{exc} The remaining pages are stored without LLM extraction.")
                enrich = None
            except LLMOutputError as exc:
                report.notes.append(f"{cand.url}: {exc}. Stored without LLM extraction.")
```

- [ ] **Step 5: Run the tests to see them pass**

Run: `python -m pytest tests/test_extract.py tests/test_screen.py tests/test_crawl.py tests/test_store.py -v`
Expected: all passed.

- [ ] **Step 6: Use the worker in `crawl` and `eval-prices`**

In `src/scout/cli.py`, add imports:

```python
from scout.extract.page import enrich_record
from scout.extract.screen import screen_titles
from scout.profiles import load_profile
```

Change the `crawl` command. Add the option `no_llm: bool = typer.Option(False, "--no-llm")`. Make the screener and the enricher before the `with` block and give them to `run_crawl`. At the end, show the screening result and the LLM usage:

```python
    enrich, screen, llm = None, None, None
    if not no_llm:
        profile = load_profile(category)
        llm = LLM(load_llm_config())
        enrich = lambda rec: enrich_record(rec, profile, llm)  # noqa: E731
        screen = lambda cands: screen_titles(cands, profile, llm)  # noqa: E731
```

```python
        report = run_crawl(conn, adapter, category, query, on_problem=ask_user, max_pages=pages,
                           max_details=details, budget=budget_sek, enrich=enrich, screen=screen)
```

Replace the line that prints `pages that needed an LLM call` with:

```python
    typer.echo(f"dropped as off topic by title: {report.off_topic}")
    typer.echo(f"pages sent to the worker model: {report.llm_pages} of {report.detail_pages}")
```

```python
    if llm is not None:
        tokens_in = sum(c.input_tokens for c in llm.calls)
        tokens_out = sum(c.output_tokens for c in llm.calls)
        typer.echo(f"LLM calls {len(llm.calls)}, tokens in {tokens_in}, out {tokens_out}")
```

Change the `eval-prices` command. Add `llm: bool = typer.Option(False, "--llm")` and `category: str = "fridge"`, and select the extractor:

```python
    extract, extractor = extract_from_snapshot, "selectors"
    if llm:
        load_settings()
        worker = LLM(load_llm_config())
        profile = load_profile(category)
        extract = lambda label: enrich_record(extract_from_snapshot(label), profile, worker)[0]  # noqa: E731
        extractor = f"selectors+worker:{worker.model_for('worker')}"
    result = evaluate_prices(rows, extract)
    summary = {"extractor": extractor, "run_at": datetime.now(timezone.utc).isoformat(), **result.summary()}
```

- [ ] **Step 7: Real run**

Run: `scout crawl fridge "kylskåp" --details 5`
Expected: the report shows how many results the screening removed as off topic, how many pages went to the worker, and the counts of LLM calls and tokens. Look at the search screenshot, or at the search page in a normal browser. Make sure that the removed results are really accessories. Check the stored attributes:

```bash
docker compose exec db psql -U scout product_scout -c "SELECT left(name, 40) AS name, attributes FROM products ORDER BY id DESC LIMIT 5;"
```

Each attribute has a `raw` value that you can find on the product page.

Run: `scout eval-prices --llm`
Expected: a second results file with the extractor `selectors+worker:...`. You can then compare the two runs.

- [ ] **Step 8: Commit**

```bash
git add src/scout/extract src/scout/db/store.py src/scout/crawl.py src/scout/cli.py tests/fakes.py tests/test_extract.py tests/test_screen.py tests/test_crawl.py tests/test_store.py eval/results
git commit -m "Worker title screening and extraction with raw-text checks; reuse stored attributes; quota and output-error fallbacks"
```

---

### Task 14: Command-line picks

**Files:**
- Create: `src/scout/picks/__init__.py`, `src/scout/picks/rank.py`, `src/scout/picks/recheck.py`, `src/scout/picks/write.py`, `tests/test_picks_rank.py`, `tests/test_picks_db.py`, `tests/test_picks_recheck.py`, `tests/test_picks_write.py`
- Modify: `src/scout/cli.py` (add `picks`)

**Interfaces:**
- Consumes: `connect`, `save_record` (Tasks 2, 8), `AmazonSeAdapter`, `fetch_checked` (Task 9), `LLM` (Task 11), `load_profile` (Task 12), `to_decimal` (Task 3), `FakeLLM` (Task 13).
- Produces:
  - `rank.Card` dataclass (`listing_id, product_id, title, brand, model, url, price, currency, in_stock, rating, review_count, attributes, fetched_at, score=0.0, missing=[]`)
  - `rank.Condition(attribute: str, op: Literal["<=", ">="], value: float)`, `parse_condition(text) -> Condition`
  - `load_cards(conn, category, budget: Decimal | None) -> list[Card]` (SQL: category, not out of stock, price known and within budget when a budget is given)
  - `apply_conditions(cards, conditions) -> tuple[list[Card], int]`, `bayesian(rating, count, prior_mean, weight=PRIOR_WEIGHT) -> float`, `rank(cards) -> list[Card]`
  - `recheck.recheck_picks(ranked, k, refetch: Callable[[Card], ProductRecord | None], budget) -> tuple[list[Card], list[str]]`
  - `write.PicksText`, `check_picks(text, cards_by_id) -> list[str]`, `write_picks(llm, cards, conditions_text, language="English", max_rounds=3) -> tuple[PicksText | None, list[str]]`, `format_picks(picks, texts, notes, conditions_text, dropped) -> str`

- [ ] **Step 1: Write the failing ranking tests**

`tests/test_picks_rank.py`:

```python
from datetime import datetime, timezone
from decimal import Decimal

import pytest

from scout.picks.rank import Card, Condition, apply_conditions, bayesian, parse_condition, rank

NOW = datetime(2026, 10, 3, 10, 15, tzinfo=timezone.utc)


def card(listing_id, rating=None, count=None, **attrs):
    return Card(listing_id=listing_id, product_id=listing_id, title=f"Fridge {listing_id}", brand=None, model=None,
                url=f"https://www.amazon.se/dp/B0AAAAAAA{listing_id}", price=Decimal("5000"), currency="SEK",
                in_stock=True, rating=None if rating is None else Decimal(str(rating)), review_count=count,
                attributes={k: {"value": v} for k, v in attrs.items()}, fetched_at=NOW)


def test_parse_condition():
    assert parse_condition("height<=180") == Condition("height", "<=", 180.0)
    assert parse_condition(" total_volume >= 300,5 ") == Condition("total_volume", ">=", 300.5)
    with pytest.raises(ValueError):
        parse_condition("height < 180")


def test_few_perfect_reviews_do_not_beat_many_good_ones():
    assert bayesian(Decimal("4.6"), 3000, 4.2) > bayesian(Decimal("5.0"), 2, 4.2)
    assert bayesian(None, None, 4.2) == 4.2


def test_conditions_drop_failures_and_mark_missing():
    cards = [card(1, height=175), card(2, height=190), card(3)]
    kept, dropped = apply_conditions(cards, [Condition("height", "<=", 180)])
    assert [c.listing_id for c in kept] == [1, 3]
    assert dropped == 1
    assert kept[1].missing == ["height"]


def test_rank_puts_missing_data_last_then_by_score():
    a = card(1, 4.0, 500)
    b = card(2, 4.8, 800)
    c = card(3, 5.0, 900)
    c.missing = ["height"]
    assert [x.listing_id for x in rank([a, b, c])] == [2, 1, 3]
```

`tests/test_picks_db.py`:

```python
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

import pytest

from scout.db.store import save_record
from scout.picks.rank import load_cards
from scout.sources.amazon_se.product import parse_product_page

pytestmark = pytest.mark.db
FIXTURES = Path(__file__).parent / "fixtures" / "amazon_se"
NOW = datetime(2026, 10, 3, 10, 15, tzinfo=timezone.utc)


def save(db, name, asin):
    html = (FIXTURES / name).read_text(encoding="utf-8")
    save_record(db, parse_product_page(html, f"https://www.amazon.se/dp/{asin}", NOW), "fridge")


def test_load_cards_skips_out_of_stock_and_applies_the_budget(db):
    save(db, "product_full.html", "B0AAAAAAA1")         # 8990 SEK, in stock
    save(db, "product_unavailable.html", "B0AAAAAAA2")  # out of stock
    save(db, "product_parts_price.html", "B0AAAAAAA3")  # 5499 SEK, in stock
    assert {c.url[-10:] for c in load_cards(db, "fridge", None)} == {"B0AAAAAAA1", "B0AAAAAAA3"}
    assert [c.url[-10:] for c in load_cards(db, "fridge", Decimal("6000"))] == ["B0AAAAAAA3"]
```

- [ ] **Step 2: Run them to see them fail**

Run: `python -m pytest tests/test_picks_rank.py tests/test_picks_db.py -v`
Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Write `rank.py`**

`src/scout/picks/__init__.py`:

```python
"""Filter, rank, recheck and write the top picks."""
```

`src/scout/picks/rank.py`:

```python
"""SQL filter for budget and stock, Python conditions and a Bayesian rating score."""
import re
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Literal

import psycopg

from scout.sources.amazon_se.numbers import to_decimal

PRIOR_WEIGHT = 20  # reviews needed before a product's own rating counts as much as the average

CARDS_SQL = """
SELECT l.id AS listing_id, p.id AS product_id, COALESCE(l.title, p.name) AS title, p.brand, p.model, l.url,
       v.price, v.currency, v.in_stock, l.rating, l.review_count, p.attributes, l.fetched_at
FROM listings l
JOIN products p ON p.id = l.product_id
JOIN variants v ON v.listing_id = l.id
WHERE p.category = %(category)s
  AND v.in_stock IS DISTINCT FROM false
  AND (%(budget)s::numeric IS NULL OR (v.price IS NOT NULL AND v.price <= %(budget)s::numeric))
ORDER BY l.id
"""
_CONDITION = re.compile(r"^\s*(\w+)\s*(<=|>=)\s*([\d.,]+)\s*$")


@dataclass
class Card:
    listing_id: int
    product_id: int
    title: str
    brand: str | None
    model: str | None
    url: str
    price: Decimal | None
    currency: str | None
    in_stock: bool | None
    rating: Decimal | None
    review_count: int | None
    attributes: dict
    fetched_at: datetime
    score: float = 0.0
    missing: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class Condition:
    attribute: str
    op: Literal["<=", ">="]
    value: float


def parse_condition(text: str) -> Condition:
    match = _CONDITION.match(text)
    number = to_decimal(match.group(3)) if match else None
    if not match or number is None:
        raise ValueError(f"Cannot read '{text}'. Write it like height<=180 or total_volume>=300.")
    return Condition(match.group(1), match.group(2), float(number))


def load_cards(conn: psycopg.Connection, category: str, budget: Decimal | None) -> list[Card]:
    rows = conn.execute(CARDS_SQL, {"category": category, "budget": budget}).fetchall()
    return [Card(**row) for row in rows]


def apply_conditions(cards: list[Card], conditions: list[Condition]) -> tuple[list[Card], int]:
    kept, dropped = [], 0
    for card in cards:
        missing, passes = [], True
        for cond in conditions:
            entry = card.attributes.get(cond.attribute) or {}
            if entry.get("value") is None:
                missing.append(cond.attribute)
                continue
            value = float(entry["value"])
            if (cond.op == "<=" and value > cond.value) or (cond.op == ">=" and value < cond.value):
                passes = False
                break
        if passes:
            card.missing = missing
            kept.append(card)
        else:
            dropped += 1
    return kept, dropped


def bayesian(rating: Decimal | None, count: int | None, prior_mean: float, weight: int = PRIOR_WEIGHT) -> float:
    if rating is None or not count:
        return prior_mean
    return (count * float(rating) + weight * prior_mean) / (count + weight)


def rank(cards: list[Card]) -> list[Card]:
    rated = [float(c.rating) for c in cards if c.rating is not None and c.review_count]
    prior = sum(rated) / len(rated) if rated else 0.0
    for card in cards:
        card.score = bayesian(card.rating, card.review_count, prior)
    return sorted(cards, key=lambda c: (bool(c.missing), -c.score))
```

- [ ] **Step 4: Run them to see them pass**

Run: `python -m pytest tests/test_picks_rank.py tests/test_picks_db.py -v`
Expected: all passed.

- [ ] **Step 5: Write the failing recheck and writing tests**

`tests/test_picks_recheck.py`:

```python
from decimal import Decimal

from scout.picks.recheck import recheck_picks
from tests.test_picks_rank import NOW, card
from scout.sources.base import ProductRecord


def fresh(c, price="5000", in_stock=True):
    return ProductRecord(source="amazon_se", source_id=c.url[-10:], url=c.url, fetched_at=NOW,
                         price=Decimal(price), currency="SEK", in_stock=in_stock)


def test_sold_out_and_over_budget_picks_are_replaced():
    ranked = [card(1), card(2), card(3), card(4)]
    states = {1: dict(in_stock=False), 2: dict(price="9000"), 3: {}, 4: {}}
    picks, notes = recheck_picks(ranked, 2, lambda c: fresh(c, **states[c.listing_id]), Decimal("8000"))
    assert [p.listing_id for p in picks] == [3, 4]
    assert len(notes) == 2


def test_recheck_updates_the_price_shown():
    picks, _ = recheck_picks([card(1)], 1, lambda c: fresh(c, price="4500"), None)
    assert picks[0].price == Decimal("4500")


def test_pages_that_cannot_be_rechecked_are_skipped():
    picks, notes = recheck_picks([card(1), card(2)], 1, lambda c: None if c.listing_id == 1 else fresh(c), None)
    assert [p.listing_id for p in picks] == [2]
    assert "could not be rechecked" in notes[0]
```

`tests/test_picks_write.py`:

```python
from scout.picks.write import Claim, PickText, PicksText, check_picks, format_picks, write_picks
from tests.fakes import FakeLLM
from tests.test_picks_rank import card


def cards():
    a = card(1, 4.5, 1234, height=178.0)
    a.model = "RB38C7B5D22"
    return {1: a}


def picks(summary, claims):
    return PicksText(picks=[PickText(listing_id=1, summary=summary,
                                     claims=[Claim(field=f, value=v) for f, v in claims])])


def test_correct_claims_pass():
    text = picks("Fits at 178 cm, rated 4.5 by 1 234 buyers, 5000 kr.",
                 [("height", "178"), ("rating", "4.5"), ("review_count", "1234"), ("price", "5000")])
    assert check_picks(text, cards()) == []


def test_wrong_claim_and_uncited_number_are_caught():
    text = picks("Rated 4.7, only 30 dB.", [("rating", "4.7")])
    errors = check_picks(text, cards())
    assert any("rating is 4.5" in e for e in errors)
    assert any("30" in e for e in errors)


def test_unknown_id_and_unknown_field_are_caught():
    text = PicksText(picks=[PickText(listing_id=9, summary="Good.", claims=[]),
                            PickText(listing_id=1, summary="Quiet.", claims=[Claim(field="noise", value="35")])])
    errors = check_picks(text, cards())
    assert any("unknown listing_id 9" in e for e in errors)
    assert any("no field 'noise'" in e for e in errors)


def test_model_number_in_the_summary_is_not_treated_as_numbers():
    assert check_picks(picks("The RB38C7B5D22 is a solid choice.", []), cards()) == []


def test_the_rating_scale_is_not_a_claim():
    text = picks("Rated 4.5/5, that is 4.5 out of 5.", [("rating", "4.5")])
    assert check_picks(text, cards()) == []


def test_write_picks_retries_with_the_errors_then_succeeds():
    bad = picks("Rated 4.9.", [("rating", "4.9")])
    good = picks("Rated 4.5.", [("rating", "4.5")])
    llm = FakeLLM([bad, good])
    text, errors = write_picks(llm, list(cards().values()), "height<=180")
    assert text == good and errors == []
    assert llm.requests[0][0] == "chat"
    assert "rating is 4.5" in llm.requests[1][1]


def test_write_picks_gives_up_after_three_rounds():
    bad = picks("Rated 4.9.", [("rating", "4.9")])
    text, errors = write_picks(FakeLLM([bad, bad, bad]), list(cards().values()), "")
    assert text is None and errors


def test_format_shows_missing_data_and_how_ranking_works():
    c = cards()[1]
    c.missing = ["width"]
    out = format_picks([c], None, ["note one"], "width<=60", 2)
    assert "Fridge 1" in out and "width: missing" in out
    assert "Bayesian" in out and "note one" in out and "2 products failed" in out
```

- [ ] **Step 6: Run them to see them fail**

Run: `python -m pytest tests/test_picks_recheck.py tests/test_picks_write.py -v`
Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 7: Write `recheck.py` and `write.py`**

`src/scout/picks/recheck.py`:

```python
"""Recheck every pick's price and stock right before showing it."""
from dataclasses import replace
from decimal import Decimal
from typing import Callable

from scout.picks.rank import Card
from scout.sources.base import ProductRecord


def recheck_picks(
    ranked: list[Card],
    k: int,
    refetch: Callable[[Card], ProductRecord | None],
    budget: Decimal | None,
) -> tuple[list[Card], list[str]]:
    picks, notes = [], []
    for card in ranked:
        if len(picks) == k:
            break
        rec = refetch(card)
        if rec is None:
            notes.append(f"{card.title} could not be rechecked, so it was left out.")
            continue
        if rec.in_stock is False:
            notes.append(f"{card.title} is sold out now and was replaced.")
            continue
        if budget is not None and rec.price is not None and rec.price > budget:
            notes.append(f"{card.title} now costs {rec.price} {rec.currency}, over budget, and was replaced.")
            continue
        picks.append(replace(card, price=rec.price, currency=rec.currency,
                             in_stock=rec.in_stock, fetched_at=rec.fetched_at))
    return picks, notes
```

`src/scout/picks/write.py`:

```python
"""The chat model writes the picks; Python checks every id and number against the cards."""
import json
import re
from decimal import Decimal

from pydantic import BaseModel

from scout.picks.rank import PRIOR_WEIGHT, Card
from scout.sources.amazon_se.numbers import to_decimal

SYSTEM = """You write short product recommendations for a shopper.
You get the shopper's conditions and product cards. Each card has an id and facts.
Rules:
- Write one pick per card, in the order given, with the card's id as listing_id.
- summary: one or two sentences on why the product fits, and what to watch out for, such as missing data.
- Use only facts on the cards.
- Every number you write in a summary must also be in that pick's claims, with the card field it comes from.
  Claim fields are price, rating, review_count, or an attribute name on the card. Do not write any other numbers.
- Write in {language}."""

SUMMARY_NUMBER = re.compile(r"\d{1,3}(?:[\u00a0\u202f ]\d{3})+(?:[.,]\d+)?|\d+(?:[.,]\d+)?")
RATING_SCALE = re.compile(r"(?:/|\bout of|\bav|\bof)\s*5\b", re.IGNORECASE)  # the "5" in "4.5/5" is not a claim


class Claim(BaseModel):
    field: str
    value: str


class PickText(BaseModel):
    listing_id: int
    summary: str
    claims: list[Claim]


class PicksText(BaseModel):
    picks: list[PickText]


def card_facts(card: Card) -> dict[str, Decimal]:
    facts: dict[str, Decimal] = {}
    if card.price is not None:
        facts["price"] = Decimal(card.price)
    if card.rating is not None:
        facts["rating"] = Decimal(card.rating)
    if card.review_count is not None:
        facts["review_count"] = Decimal(card.review_count)
    for name, entry in card.attributes.items():
        value = entry.get("value")
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            facts[name] = Decimal(str(value))
    return facts


def _card_json(card: Card) -> str:
    return json.dumps({
        "id": card.listing_id,
        "title": card.title,
        "brand": card.brand,
        "price": f"{card.price} {card.currency}" if card.price is not None else None,
        "in_stock": card.in_stock,
        "rating": str(card.rating) if card.rating is not None else None,
        "review_count": card.review_count,
        "attributes": {k: f"{v.get('value')} {v.get('unit') or ''}".strip() for k, v in card.attributes.items()},
        "missing": card.missing,
    }, ensure_ascii=False)


def check_picks(text: PicksText, cards: dict[int, Card]) -> list[str]:
    errors = []
    for pick in text.picks:
        card = cards.get(pick.listing_id)
        if card is None:
            errors.append(f"unknown listing_id {pick.listing_id}")
            continue
        facts = card_facts(card)
        claimed: set[Decimal] = set()
        for claim in pick.claims:
            if claim.field not in facts:
                errors.append(f"{pick.listing_id}: no field '{claim.field}' on the card")
                continue
            value = to_decimal(claim.value)
            if value is None or value != facts[claim.field]:
                errors.append(f"{pick.listing_id}: {claim.field} is {facts[claim.field]}, not {claim.value}")
                continue
            claimed.add(value)
        summary = RATING_SCALE.sub("", pick.summary)
        if card.model:
            summary = re.sub(re.escape(card.model), "", summary, flags=re.IGNORECASE)
        for raw in SUMMARY_NUMBER.findall(summary):
            number = to_decimal(raw)
            if number is not None and number not in claimed:
                errors.append(f"{pick.listing_id}: the number {raw} in the summary is not in the claims")
    missing_ids = sorted(set(cards) - {p.listing_id for p in text.picks})
    if missing_ids:
        errors.append(f"no pick written for ids {missing_ids}")
    return errors


def write_picks(llm, cards: list[Card], conditions_text: str, language: str = "English",
                max_rounds: int = 3) -> tuple[PicksText | None, list[str]]:
    by_id = {card.listing_id: card for card in cards}
    base = f"Conditions: {conditions_text or 'none'}\n\nCards:\n" + "\n".join(_card_json(c) for c in cards)
    errors: list[str] = []
    for _ in range(max_rounds):
        prompt = base if not errors else base + "\n\nYour last answer had these problems. Fix them:\n" + \
            "\n".join(f"- {e}" for e in errors)
        text = llm.structured("chat", SYSTEM.format(language=language), prompt, PicksText)
        errors = check_picks(text, by_id)
        if not errors:
            return text, []
    return None, errors


def format_picks(picks: list[Card], texts: PicksText | None, notes: list[str],
                 conditions_text: str, dropped: int) -> str:
    lines = [f"Conditions: {conditions_text or 'none'}", ""]
    summaries = {p.listing_id: p.summary for p in texts.picks} if texts else {}
    for n, card in enumerate(picks, 1):
        price = f"{card.price} {card.currency}" if card.price is not None else "price unknown"
        rating = f"{card.rating} ({card.review_count} ratings)" if card.rating is not None else "no ratings"
        lines.append(f"{n}. {card.title}")
        lines.append(f"   {price} | {rating} | rechecked {card.fetched_at:%Y-%m-%d %H:%M} UTC | id {card.listing_id}")
        attrs = ", ".join(f"{k}: {v.get('value')} {v.get('unit') or ''}".strip() for k, v in card.attributes.items())
        if attrs:
            lines.append(f"   {attrs}")
        if card.missing:
            lines.append("   " + ", ".join(f"{name}: missing" for name in card.missing))
        if card.listing_id in summaries:
            lines.append(f"   {summaries[card.listing_id]}")
        lines.append(f"   {card.url}")
    lines.append("")
    lines.append(f"How these were ranked: rating adjusted for review count (Bayesian average, prior weight "
                 f"{PRIOR_WEIGHT}). Products with missing data on your conditions come last. Price only "
                 "checks the budget.")
    if dropped:
        lines.append(f"{dropped} products failed your conditions and are not shown.")
    if texts is None:
        lines.append("The written summaries did not pass the checks, so only the facts are shown.")
    lines += notes
    return "\n".join(lines)
```

- [ ] **Step 8: Run them to see them pass**

Run: `python -m pytest tests/test_picks_recheck.py tests/test_picks_write.py -v`
Expected: all passed.

- [ ] **Step 9: Add the `picks` command**

Add to `src/scout/cli.py`:

```python
from scout.crawl import fetch_checked
from scout.db.store import save_record
from scout.picks.rank import apply_conditions, load_cards, parse_condition, rank
from scout.picks.recheck import recheck_picks
from scout.picks.write import format_picks, write_picks


@app.command()
def picks(
    category: str,
    budget: Optional[float] = None,
    where: Optional[List[str]] = typer.Option(None, "--where", help="e.g. height<=180, repeatable"),
    k: int = 5,
    no_llm: bool = typer.Option(False, "--no-llm"),
    lang: str = "English",
) -> None:
    """Rank stored products, recheck the top ones on amazon.se, and print the picks."""
    settings = load_settings()
    profile = load_profile(category)
    conditions = [parse_condition(w) for w in where or []]
    for cond in conditions:
        if profile.attribute(cond.attribute) is None:
            raise typer.BadParameter(f"'{cond.attribute}' is not in the {category} profile")
    budget_sek = Decimal(str(budget)) if budget is not None else None
    conditions_text = ", ".join(where or []) + (f", budget {budget_sek} SEK" if budget_sek else "")
    with connect(settings.database_url) as conn:
        cards, dropped = apply_conditions(load_cards(conn, category, budget_sek), conditions)
        ranked = rank(cards)
        if not ranked:
            typer.echo("No stored product meets these conditions. Run scout crawl first, or loosen them.")
            raise typer.Exit(1)
        with BrowserSession(settings.browser_profile_dir) as session:
            adapter = AmazonSeAdapter(session, settings.snapshot_dir)

            def refetch(card):
                page = fetch_checked(adapter, conn, card.url, ask_user)
                if page is None:
                    return None
                rec = adapter.parse(page)
                save_record(conn, rec, category)
                return rec

            chosen, notes = recheck_picks(ranked, k, refetch, budget_sek)
    texts, errors = None, []
    if chosen and not no_llm:
        texts, errors = write_picks(LLM(load_llm_config()), chosen, conditions_text, lang)
    typer.echo(format_picks(chosen, texts, notes + [f"check failed: {e}" for e in errors], conditions_text, dropped))
```

- [ ] **Step 10: Run the whole suite**

Run: `python -m pytest -v`
Expected: all passed.

- [ ] **Step 11: Real run (Zheyun)**

Run: `scout picks fridge --budget 9000 --where "height<=186" --where "width<=60"`
Expected: a maximum of 5 picks. The program checks each pick again a few seconds before it shows the pick. Each pick shows the price, rating, attributes, missing data, a short summary and a link. Open two links and make sure that the price and the stock are correct. Then run the same command with `--no-llm` and make sure that the facts are the same.

- [ ] **Step 12: Commit**

```bash
git add src/scout/picks src/scout/cli.py tests/test_picks_*.py
git commit -m "Command-line picks: SQL and Python filters, Bayesian ranking, recheck, checked summaries"
```

---

## After phase 1

- Write the real numbers from `eval/results/` in the progress notes. Nothing goes into the CV until Zheyun confirms it.
- Phase 2 (conversation, LangGraph, Langfuse) has its own plan.
