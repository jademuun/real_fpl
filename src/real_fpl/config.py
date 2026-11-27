"""Project paths and config loading."""

from __future__ import annotations

import functools
import os
from pathlib import Path
from typing import Any

import yaml

PROJECT_ROOT = Path(__file__).resolve().parents[2]
CONFIG_PATH = PROJECT_ROOT / "config" / "sources.yaml"
DATA_DIR = PROJECT_ROOT / "data"
RAW_DIR = DATA_DIR / "raw"
EXPORT_DIR = DATA_DIR / "exports"
DB_PATH = DATA_DIR / "fpl.db"


def entry_id() -> int | None:
    """The FPL team to track, from REAL_FPL_ENTRY_ID; None when unset.

    Read from the environment, never from committed config: it identifies a
    real person's team, and only the server that fetches needs it.
    """
    raw = os.environ.get("REAL_FPL_ENTRY_ID", "").strip()
    if not raw:
        return None
    if not raw.isdigit():
        raise ValueError(f"REAL_FPL_ENTRY_ID must be a number, got {raw!r}")
    return int(raw)


@functools.lru_cache(maxsize=1)
def load_config(path: Path | None = None) -> dict[str, Any]:
    with open(path or CONFIG_PATH) as fh:
        return yaml.safe_load(fh)
