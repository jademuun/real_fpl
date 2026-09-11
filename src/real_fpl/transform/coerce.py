"""Type coercion for FPL's JSON.

FPL returns most numerics as strings ("12.4", "0.0") and uses null liberally for
fields like chance_of_playing. Coerce once, at the boundary, so nothing
downstream has to think about it.
"""

from __future__ import annotations

from typing import Any


def to_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def to_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def to_bool_int(value: Any) -> int | None:
    """SQLite has no bool; store 0/1."""
    if value is None:
        return None
    return 1 if value else 0


def to_str(value: Any) -> str | None:
    if value is None:
        return None
    s = str(value).strip()
    return s or None
