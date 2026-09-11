"""element-summary -> player_gw, player_season.

element-summary/{id}/ returns three lists:
    fixtures      upcoming, not yet played  (not stored here)
    history       one row per match played this season  -> player_gw
    history_past  one row per previous season, aggregated -> player_season
"""

from __future__ import annotations

from typing import Any

from .coerce import to_bool_int, to_float, to_int, to_str
from .seasons import normalise

# history rows key the player by FPL element id, which is reassigned every
# season; opta_code is stable, so it is resolved in and stored instead.
_GW_INT = (
    "minutes",
    "starts",
    "goals_scored",
    "assists",
    "clean_sheets",
    "goals_conceded",
    "saves",
    "penalties_saved",
    "penalties_missed",
    "own_goals",
    "yellow_cards",
    "red_cards",
    "tackles",
    "recoveries",
    "clearances_blocks_interceptions",
    "defensive_contribution",
    "bps",
    "bonus",
    "total_points",
    "value",
    "selected",
    "transfers_balance",
)
_GW_FLOAT = (
    "expected_goals",
    "expected_assists",
    "expected_goal_involvements",
    "expected_goals_conceded",
    "influence",
    "creativity",
    "threat",
    "ict_index",
)


def player_gw_rows(
    summary: dict[str, Any], opta_code: str, season: str
) -> list[dict[str, Any]]:
    rows = []
    for h in summary.get("history", []):
        row: dict[str, Any] = {
            "season": season,
            "gameweek": to_int(h.get("round")),
            "opta_code": opta_code,
            "fpl_fixture_id": to_int(h.get("fixture")),
            "was_home": to_bool_int(h.get("was_home")),
            "opponent_team": to_int(h.get("opponent_team")),
            "kickoff_utc": to_str(h.get("kickoff_time")),
        }
        # A blank gameweek or fixture would break the primary key; skip rather
        # than write a row that cannot be addressed again.
        if row["gameweek"] is None or row["fpl_fixture_id"] is None:
            continue
        for f in _GW_INT:
            row[f] = to_int(h.get(f))
        for f in _GW_FLOAT:
            row[f] = to_float(h.get(f))
        rows.append(row)
    return rows


_SEASON_INT = (
    "minutes",
    "starts",
    "total_points",
    "goals_scored",
    "assists",
    "clean_sheets",
    "goals_conceded",
    "saves",
    "yellow_cards",
    "red_cards",
    "own_goals",
    "penalties_saved",
    "penalties_missed",
    "tackles",
    "recoveries",
    "clearances_blocks_interceptions",
    "defensive_contribution",
    "bps",
    "bonus",
    "start_cost",
    "end_cost",
)
_SEASON_FLOAT = (
    "expected_goals",
    "expected_assists",
    "expected_goal_involvements",
    "expected_goals_conceded",
    "influence",
    "creativity",
    "threat",
    "ict_index",
)


def player_season_rows(summary: dict[str, Any], opta_code: str) -> list[dict[str, Any]]:
    """Previous-season totals. This is where 'points from previous year' comes from."""
    rows = []
    for p in summary.get("history_past", []):
        label = p.get("season_name")
        if not label:
            continue
        row: dict[str, Any] = {
            "season": normalise(label),
            "opta_code": opta_code,
        }
        for f in _SEASON_INT:
            row[f] = to_int(p.get(f))
        for f in _SEASON_FLOAT:
            row[f] = to_float(p.get(f))
        rows.append(row)
    return rows
