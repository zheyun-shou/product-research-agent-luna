"""Command line for product-scout."""
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import List

import typer

from scout.config import load_settings
from scout.db.conn import connect, init_schema
from scout.sources.amazon_se.check import check_page
from scout.sources.amazon_se.product import parse_product_page
from scout.sources.amazon_se.search import has_next_page, parse_search_page
from scout.sources.amazon_se.urls import BASE_URL, SOURCE, page_kind, product_url, snapshot_key
from scout.sources.browser import BrowserSession
from scout.sources.snapshots import save_html

app = typer.Typer(no_args_is_help=True)


@app.callback()
def main() -> None:
    """product-scout command line."""
    # The Windows console defaults to GBK here and cannot print Swedish letters.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")


@app.command()
def version() -> None:
    """Print the version."""
    typer.echo("product-scout 0.1.0")


@app.command("db-init")
def db_init() -> None:
    """Create the tables in the main database."""
    with connect(load_settings().database_url) as conn:
        init_schema(conn)
    typer.echo("Tables are ready.")


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


@app.command("parse-search")
def parse_search_cmd(path: Path) -> None:
    """Print what the parser reads from a saved search page."""
    html = path.read_text(encoding="utf-8")
    for c in parse_search_page(html):
        typer.echo(json.dumps(c.model_dump(mode="json"), ensure_ascii=False))
    typer.echo(f"next page: {has_next_page(html)}")


@app.command("parse-product")
def parse_product_cmd(path: Path) -> None:
    """Print what the parser reads from a saved product page. The folder name is the ASIN."""
    fetched_at = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
    rec = parse_product_page(path.read_text(encoding="utf-8"), product_url(path.parent.name), fetched_at)
    shown = rec.model_dump(mode="json", exclude={"text"})
    typer.echo(json.dumps(shown, ensure_ascii=False, indent=2))
    typer.echo(f"missing: {rec.missing_core()}")
