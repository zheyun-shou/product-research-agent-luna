"""Command line for product-scout."""
from typing import List

import typer

from scout.config import load_settings
from scout.db.conn import connect, init_schema
from scout.sources.amazon_se.check import check_page
from scout.sources.amazon_se.urls import BASE_URL, SOURCE, page_kind, snapshot_key
from scout.sources.browser import BrowserSession
from scout.sources.snapshots import save_html

app = typer.Typer(no_args_is_help=True)


@app.callback()
def main() -> None:
    """product-scout command line."""


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
