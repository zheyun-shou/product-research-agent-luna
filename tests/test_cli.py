from typer.testing import CliRunner

from scout.cli import app


def test_eval_chat_stops_before_the_run_when_the_provider_is_wrong():
    result = CliRunner().invoke(app, ["eval-chat", "--chat", "nvdia:some-model"])
    assert result.exit_code == 1 and "Cannot run" in result.output and "unknown provider" in result.output


def test_version_command():
    result = CliRunner().invoke(app, ["version"])
    assert result.exit_code == 0
    assert "product-scout 0.1.0" in result.output
