"""Season label normalisation.

The same season is spelled three different ways across the sources:

    FPL history_past.season_name   "2024/25"
    this project / vaastav repo    "2024-25"
    PL official compseason label   "2024/25"  (plus a numeric id, 719)

Everything is normalised to the hyphenated form on the way in, so joins work.
Left unnormalised, this is exactly the kind of thing that silently produces zero
matching rows and no error.
"""

from __future__ import annotations

import re

_SLASH = re.compile(r"^(\d{4})/(\d{2})$")
_HYPHEN = re.compile(r"^(\d{4})-(\d{2})$")


def normalise(label: str) -> str:
    """'2024/25' or '2024-25' -> '2024-25'."""
    label = label.strip()
    for pattern in (_SLASH, _HYPHEN):
        m = pattern.match(label)
        if m:
            return f"{m.group(1)}-{m.group(2)}"
    raise ValueError(f"unrecognised season label: {label!r}")
