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
    eval_database_url: str  # eval-hidden empties it before each target
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
        eval_database_url=os.environ.get(
            "EVAL_DATABASE_URL", "postgresql://scout:scout@localhost:5433/product_scout_eval"
        ),
        browser_profile_dir=ROOT / "browser-profile",
        snapshot_dir=ROOT / "snapshots",
        data_dir=ROOT / "data",
    )
