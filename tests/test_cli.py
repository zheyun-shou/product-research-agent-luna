from typer.testing import CliRunner

from scout.cli import app


def test_version_command():
    result = CliRunner().invoke(app, ["version"])
    assert result.exit_code == 0
    assert "product-scout 0.1.0" in result.output
