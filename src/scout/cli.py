"""Command line for product-scout."""
import typer

from scout.config import load_settings
from scout.db.conn import connect, init_schema

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
