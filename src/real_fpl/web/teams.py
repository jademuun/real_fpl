"""Club identity for the UI: a tiny kit (shirt) icon per club.

Why shirts and not one colour each: about a third of the league plays in red,
so a single colour can't tell Arsenal, Liverpool, Man Utd, Forest, Brentford and
Sunderland apart. Shirts carry far more identity in a 20-pixel icon: Arsenal's
white sleeves, Newcastle's black-and-white stripes, Villa's claret and blue.
The three-letter code always sits next to the icon, so even two near-identical
kits (Brentford and Sunderland, both red-and-white stripes) stay unambiguous.

Why not club crests: crests are the clubs' registered trademarks and artwork,
not ours to copy. Kit colours and stripe patterns are generic.

Keyed by FPL's short_name (ARS, MCI...), which every page already has. A club
missing here (promoted next season) gets a plain grey shirt rather than an
error; add a line when that happens.
"""

from __future__ import annotations

from dataclasses import dataclass

from markupsafe import Markup


@dataclass(frozen=True)
class Kit:
    body: str
    sleeves: str | None = None   # None = same as body
    stripes: str | None = None   # vertical stripes in this colour over the body
    trim: str | None = None      # collar and cuffs
    primary: str | None = None   # banner colour on the player page; default body
    secondary: str | None = None  # banner accent stripe; default stripes/sleeves/trim


KITS: dict[str, Kit] = {
    "ARS": Kit("#EF0107", sleeves="#FFFFFF", trim="#FFFFFF"),
    "AVL": Kit("#670E36", sleeves="#95BFE5", trim="#95BFE5"),
    "BOU": Kit("#DA291C", stripes="#111111", trim="#111111"),
    "BRE": Kit("#E30613", stripes="#FFFFFF", trim="#111111"),
    "BHA": Kit("#0057B8", stripes="#FFFFFF", trim="#0057B8"),
    "BUR": Kit("#6C1D45", sleeves="#99D6EA", trim="#99D6EA"),
    "CHE": Kit("#034694", trim="#FFFFFF"),
    "COV": Kit("#59CBE8", trim="#0B2341", secondary="#0B2341"),
    "CRY": Kit("#1B458F", stripes="#C4122E", trim="#1B458F"),
    "EVE": Kit("#003399", trim="#FFFFFF"),
    "FUL": Kit("#FFFFFF", trim="#111111", secondary="#111111"),
    "HUL": Kit("#F5A12D", stripes="#111111", trim="#111111"),
    "IPS": Kit("#0044A9", sleeves="#FFFFFF", trim="#FFFFFF"),
    "LEE": Kit("#FFFFFF", trim="#FFCD00", secondary="#1D428A"),
    "LEI": Kit("#003090", trim="#FDBE11"),
    "LIV": Kit("#C8102E", trim="#C8102E", secondary="#00B2A9"),
    "MCI": Kit("#6CABDD", trim="#1C2C5B"),
    "MUN": Kit("#DA291C", trim="#111111", secondary="#FBE122"),
    "NEW": Kit("#111111", stripes="#FFFFFF", trim="#111111"),
    "NFO": Kit("#DD0000", trim="#FFFFFF"),
    "SOU": Kit("#D71920", stripes="#FFFFFF", trim="#111111"),
    "SUN": Kit("#EB172B", stripes="#FFFFFF", sleeves="#EB172B", trim="#FFFFFF"),
    "TOT": Kit("#FFFFFF", trim="#132257", secondary="#132257"),
    "WHU": Kit("#7A263A", sleeves="#1BB1E7", trim="#1BB1E7"),
    "WOL": Kit("#FDB913", trim="#231F20"),
}

FALLBACK = Kit("#A8A29E", trim="#78716C")


def _shirt_svg(code: str, kit: Kit) -> str:
    """One <symbol> drawing a shirt on a 24x24 grid.

    The body is a plain rectangle (x 6-18), so stripes are just rectangles
    inside it and no clipping is needed. Sleeves are two quadrilaterals. A
    thin dark outline keeps white shirts (Fulham, Spurs, Leeds) visible on a
    white page.
    """
    sleeves = kit.sleeves or kit.body
    trim = kit.trim or kit.body
    stripes = ""
    if kit.stripes:
        # Three stripes on a 12-wide body: x = 7.5, 11, 14.5 (2 wide each).
        stripes = "".join(
            f'<rect x="{x}" y="4" width="2" height="18" fill="{kit.stripes}"/>'
            for x in (7.5, 11, 14.5)
        )
    outline = 'stroke="rgb(0 0 0 / .35)" stroke-width=".6" stroke-linejoin="round"'
    return (
        f'<symbol id="kit-{code}" viewBox="0 0 24 24">'
        f'<path d="M6 4 L1.5 7.5 L3.5 12 L6 10.5 Z" fill="{sleeves}" {outline}/>'
        f'<path d="M18 4 L22.5 7.5 L20.5 12 L18 10.5 Z" fill="{sleeves}" {outline}/>'
        f'<rect x="6" y="4" width="12" height="18" rx=".8" fill="{kit.body}" {outline}/>'
        f"{stripes}"
        # Collar: a V at the neck in the trim colour.
        f'<path d="M9.5 4 L12 7 L14.5 4" fill="none" stroke="{trim}" stroke-width="1.4"/>'
        "</symbol>"
    )


def kit_sprite() -> Markup:
    """All shirts as one hidden SVG, included once per page (base.html).

    Each chip then just says <use href="#kit-ARS">: the drawing is sent once
    instead of once per table row, which matters on a 674-row player table.
    Markup() tells Jinja this is trusted HTML (we built it from the dict above),
    so it isn't escaped into visible text.
    """
    symbols = "".join(_shirt_svg(code, kit) for code, kit in KITS.items())
    symbols += _shirt_svg("none", FALLBACK)
    return Markup(
        '<svg xmlns="http://www.w3.org/2000/svg" style="display:none" aria-hidden="true">'
        f"{symbols}</svg>"
    )


def kit_id(short_name: str | None) -> str:
    return f"kit-{short_name}" if short_name in KITS else "kit-none"


def team_style(short_name: str | None) -> str:
    """CSS variables for the big team-coloured banner on the player page."""
    kit = KITS.get(short_name or "", FALLBACK)
    primary = kit.primary or kit.body
    secondary = kit.secondary or kit.stripes or kit.sleeves or kit.trim or primary
    # Dark text on light shirts, white on everything else (simple luminance check).
    r, g, b = (int(primary[i : i + 2], 16) for i in (1, 3, 5))
    text = "#111111" if (0.299 * r + 0.587 * g + 0.114 * b) > 160 else "#FFFFFF"
    return f"--team:{primary};--team-2:{secondary};--team-text:{text}"
