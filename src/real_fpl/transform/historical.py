"""Historical mirror CSVs -> table rows.

The mirror (vaastav/Fantasy-Premier-League) is FPL's own data, scraped week by
week and published as CSV. It exists here for one reason: FPL's API serves
per-match detail only for the current season, and a 3-gameweek target needs
more than one part-season of history to train and test on.

Its column names are FPL's own, so the live-API transforms are reused rather
than duplicated -- a merged_gw row has the same keys as an element-summary
`history` entry. What differs is handled here:

  * every value is a string, booleans included ("True"/"False"), which is why
    coerce.to_bool_int parses strings instead of truth-testing them;
  * 2024-25 has assistant-manager rows (position "AM") -- not players;
  * merged_gw's `position` text varies by season ("GK" in 2024-25, "GKP"
    later), so position is taken from players_raw's numeric element_type
    instead, mapped the same way the API maps it;
  * players_raw.csv carries `opta_code`, so the mirror's per-season `element`
    id maps straight to the join key (invariant 1: never join on names).

Pure functions: text in, rows out. Fetching lives in sources/historical.py.
"""

from __future__ import annotations

import csv
import io
from collections import defaultdict
from typing import Any

from . import matches, players
from .coerce import to_float, to_int, to_str

# Not players: the 2024-25 assistant-manager chip put managers in the data.
SKIP_POSITIONS = {"AM"}
# players_raw.csv `element_type` -> position. 5 is the 2024-25 assistant manager.
ELEMENT_TYPES = {1: "GKP", 2: "DEF", 3: "MID", 4: "FWD"}


def read_csv(text: str) -> list[dict[str, str]]:
    return list(csv.DictReader(io.StringIO(text)))


def element_to_opta(players_raw: list[dict[str, str]]) -> dict[int, str]:
    """That season's FPL element id -> opta_code. Valid for that season only."""
    out: dict[int, str] = {}
    for p in players_raw:
        element, opta = to_int(p.get("id")), to_str(p.get("opta_code"))
        if element is not None and opta:
            out[element] = opta
    return out


def season_players_rows(players_raw: list[dict[str, str]], season: str) -> list[dict[str, Any]]:
    rows = []
    for p in players_raw:
        position = ELEMENT_TYPES.get(to_int(p.get("element_type")))
        opta = to_str(p.get("opta_code"))
        if position is None or not opta:
            continue
        rows.append(
            {
                "season": season,
                "opta_code": opta,
                "fpl_element_id": to_int(p.get("id")),
                "position": position,
                "fpl_team_id": to_int(p.get("team")),
            }
        )
    return rows


def past_players_rows(players_raw: list[dict[str, str]]) -> list[dict[str, Any]]:
    """Identity-only `players` rows, for players who may have left the league.

    Only fields that don't change over time. The current-state fields
    (fpl_element_id, position, club) are left NULL: for someone no longer in
    FPL they have no current value, and for someone still there they must not
    be overwritten -- which is why these rows are written with insert_missing.
    """
    rows = []
    for p in players_raw:
        opta = to_str(p.get("opta_code"))
        if not opta or ELEMENT_TYPES.get(to_int(p.get("element_type"))) is None:
            continue
        rows.append(
            {
                "opta_code": opta,
                "fpl_code": to_int(p.get("code")),
                "first_name": to_str(p.get("first_name")),
                "second_name": to_str(p.get("second_name")),
                "web_name": to_str(p.get("web_name")),
            }
        )
    return rows


def player_gw_rows(
    merged_gw: list[dict[str, str]],
    element_opta: dict[int, str],
    season: str,
) -> tuple[list[dict[str, Any]], int]:
    """merged_gw rows -> player_gw rows, plus a count of rows with no opta_code.

    The unmapped count is returned rather than silently dropped, so the caller
    can report it: a large number would mean the id mapping is wrong.
    """
    by_element: dict[int, list[dict[str, str]]] = defaultdict(list)
    for r in merged_gw:
        if r.get("position") in SKIP_POSITIONS:
            continue
        element = to_int(r.get("element"))
        if element is not None:
            by_element[element].append(r)

    rows: list[dict[str, Any]] = []
    unmapped = 0
    for element, history in by_element.items():
        opta = element_opta.get(element)
        if opta is None:
            unmapped += len(history)
            continue
        for h in history:
            # Reuse the live-API transform one row at a time, so `xP` can be
            # attached to exactly the row it came from.
            converted = matches.player_gw_rows({"history": [h]}, opta, season)
            if converted:
                converted[0]["xp"] = to_float(h.get("xP"))
                rows.append(converted[0])
    return rows, unmapped


def fixtures_rows(fixtures: list[dict[str, str]], season: str) -> list[dict[str, Any]]:
    # The live transform passes `id` through unconverted (it's an int in JSON);
    # coerce it here so the CSV's "17" doesn't arrive as text.
    typed = [{**f, "id": to_int(f.get("id"))} for f in fixtures]
    rows = players.fixtures_rows(typed, season)
    for row, f in zip(rows, fixtures):
        row["pl_fixture_id"] = to_int(f.get("pulse_id"))
    return rows


def teams_rows(teams: list[dict[str, str]], season: str) -> list[dict[str, Any]]:
    """Note: the mirror's strength columns are end-of-season values, not what was
    known at each gameweek -- store them, but never use them as features."""
    typed = [{**t, "id": to_int(t.get("id"))} for t in teams]
    return players.teams_rows({"teams": typed}, season)
