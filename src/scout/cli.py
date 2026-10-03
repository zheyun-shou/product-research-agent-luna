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
