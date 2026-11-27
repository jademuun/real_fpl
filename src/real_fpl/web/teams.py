"""Club colours for the UI.

Keyed by FPL's three-letter short_name (ARS, MCI...), which is what every
page already has to hand. Kept as a plain dict in code rather than a DB table:
it is presentation, not data, and nothing in the pipeline should depend on it.

Only colours, never crests: badges are the clubs' trademarks, colours are just
colours. A club missing here (newly promoted next season) gets the neutral
fallback rather than an error, so the UI never breaks on a new team; add a line
when that happens.

Each entry is (primary, secondary, text): `primary` fills the chip, `text` is
the colour readable on it, and `secondary` is the thin stripe that tells the
many red clubs apart.
"""

from __future__ import annotations

COLOURS: dict[str, tuple[str, str, str]] = {
    "ARS": ("#EF0107", "#FFFFFF", "#FFFFFF"),
    "AVL": ("#670E36", "#95BFE5", "#FFFFFF"),
    "BOU": ("#DA291C", "#000000", "#FFFFFF"),
    "BRE": ("#E30613", "#FFFFFF", "#FFFFFF"),
    "BHA": ("#0057B8", "#FFFFFF", "#FFFFFF"),
    "BUR": ("#6C1D45", "#99D6EA", "#FFFFFF"),
    "CHE": ("#034694", "#FFFFFF", "#FFFFFF"),
    "COV": ("#59CBE8", "#0B2341", "#0B2341"),
    "CRY": ("#1B458F", "#C4122E", "#FFFFFF"),
    "EVE": ("#003399", "#FFFFFF", "#FFFFFF"),
    "FUL": ("#FFFFFF", "#000000", "#000000"),
    "HUL": ("#F5A12D", "#000000", "#000000"),
    "IPS": ("#0044A9", "#FFFFFF", "#FFFFFF"),
    "LEE": ("#FFFFFF", "#FFCD00", "#1D428A"),
    "LEI": ("#003090", "#FDBE11", "#FFFFFF"),
    "LIV": ("#C8102E", "#00B2A9", "#FFFFFF"),
    "MCI": ("#6CABDD", "#1C2C5B", "#1C2C5B"),
    "MUN": ("#DA291C", "#FBE122", "#FFFFFF"),
    "NEW": ("#241F20", "#FFFFFF", "#FFFFFF"),
    "NFO": ("#DD0000", "#FFFFFF", "#FFFFFF"),
    "SOU": ("#D71920", "#130C0E", "#FFFFFF"),
    "SUN": ("#EB172B", "#FFFFFF", "#FFFFFF"),
    "TOT": ("#FFFFFF", "#132257", "#132257"),
    "WHU": ("#7A263A", "#1BB1E7", "#FFFFFF"),
    "WOL": ("#FDB913", "#231F20", "#231F20"),
}

FALLBACK = ("#78716C", "#D6D3D1", "#FFFFFF")


def team_style(short_name: str | None) -> str:
    """Inline CSS custom properties for one club, consumed by .team-chip in style.css.

    Passing colours as CSS variables keeps the look (padding, radius, stripe)
    in the stylesheet, and only the per-team values in the HTML.
    """
    primary, secondary, text = COLOURS.get(short_name or "", FALLBACK)
    return f"--team:{primary};--team-2:{secondary};--team-text:{text}"
