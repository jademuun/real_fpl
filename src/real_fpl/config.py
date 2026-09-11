"""Project paths and config loading."""

from __future__ import annotations

import functools
from pathlib import Path
from typing import Any

import yaml

PROJECT_ROOT = Path(__file__).resolve().parents[2]
CONFIG_PATH = PROJECT_ROOT / "config" / "sources.yaml"
DATA_DIR = PROJECT_ROOT / "data"
RAW_DIR = DATA_DIR / "raw"
EXPORT_DIR = DATA_DIR / "exports"
DB_PATH = DATA_DIR / "fpl.db"


@functools.lru_cache(maxsize=1)
def load_config(path: Path | None = None) -> dict[str, Any]:
    with open(path or CONFIG_PATH) as fh:
        return yaml.safe_load(fh)
