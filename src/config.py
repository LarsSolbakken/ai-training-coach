"""Felles stier og innstillinger fra config.toml."""

import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RAW_DIR = ROOT / "data" / "raw"
POLAR_DIR = RAW_DIR / "polar"
STRENGTH_CSV = RAW_DIR / "styrke" / "gym-day.csv"
GARMIN_DAYS_DIR = RAW_DIR / "garmin" / "days"
DB_PATH = ROOT / "data" / "training.db"


def load_config() -> dict:
    with (ROOT / "config.toml").open("rb") as f:
        return tomllib.load(f)
