"""FPL entry (one manager's team) -> entry_gw, entry_picks.

history/  -> current[]: one row per gameweek whose deadline has passed
             chips[]:   which chip was played in which gameweek
picks/    -> picks[]:   the 15 players, slot 1-15, captaincy, multiplier

Players come as that season's FPL `element` id, which FPL reassigns every
summer. It is mapped to opta_code (the join key, invariant 1) through
season_players for the same season, and the raw element id is kept too, so a
pick whose player isn't mapped is stored with opta_code NULL rather than lost.
"""

from __future__ import annotations

from typing import Any

from .coerce import to_bool_int, to_int, to_str


def entry_gw_rows(history: dict[str, Any], season: str, entry_id: int) -> list[dict[str, Any]]:
    chips = {to_int(c.get("event")): to_str(c.get("name")) for c in history.get("chips", [])}
    rows = []
    for h in history.get("current", []):
        gw = to_int(h.get("event"))
        if gw is None:
            continue
        rows.append({
            "season": season,
            "entry_id": entry_id,
            "gameweek": gw,
            "points": to_int(h.get("points")),
            "total_points": to_int(h.get("total_points")),
            "overall_rank": to_int(h.get("overall_rank")),
            "bank": to_int(h.get("bank")),
            "value": to_int(h.get("value")),
            "event_transfers": to_int(h.get("event_transfers")),
            "event_transfers_cost": to_int(h.get("event_transfers_cost")),
            "points_on_bench": to_int(h.get("points_on_bench")),
            "active_chip": chips.get(gw),
        })
    return rows


def entry_picks_rows(
    picks: dict[str, Any],
    season: str,
    entry_id: int,
    gameweek: int,
    element_to_opta: dict[int, str],
) -> list[dict[str, Any]]:
    rows = []
    for p in picks.get("picks", []):
        element, slot = to_int(p.get("element")), to_int(p.get("position"))
        if element is None or slot is None:
            continue
        rows.append({
            "season": season,
            "entry_id": entry_id,
            "gameweek": gameweek,
            "slot": slot,
            "fpl_element_id": element,
            "opta_code": element_to_opta.get(element),
            "multiplier": to_int(p.get("multiplier")) or 0,
            "is_captain": to_bool_int(p.get("is_captain")) or 0,
            "is_vice_captain": to_bool_int(p.get("is_vice_captain")) or 0,
        })
    return rows
