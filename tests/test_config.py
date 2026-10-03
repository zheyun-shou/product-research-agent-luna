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
