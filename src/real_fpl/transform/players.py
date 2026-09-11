"""bootstrap-static -> teams, players, player_snapshots."""

from __future__ import annotations

from typing import Any

from .coerce import to_bool_int, to_float, to_int, to_str


def position_map(bootstrap: dict[str, Any]) -> dict[int, str]:
    """element_type id -> GKP / DEF / MID / FWD."""
    return {t["id"]: t["singular_name_short"] for t in bootstrap["element_types"]}


def current_gameweek(bootstrap: dict[str, Any]) -> int | None:
    """The gameweek in progress, else the next one, else None (season over)."""
    for event in bootstrap["events"]:
        if event.get("is_current"):
            return event["id"]
    for event in bootstrap["events"]:
        if event.get("is_next"):
            return event["id"]
    return None


def teams_rows(bootstrap: dict[str, Any], season: str) -> list[dict[str, Any]]:
    # Teams, unlike players, carry no opta_code. They carry `pulse_id`, which is
    # the Premier League site's own team id -- so that, not an Opta code, is the
    # join key on the team side.
    return [
        {
            "season": season,
            "fpl_team_id": t["id"],
            "pl_team_id": to_int(t.get("pulse_id")),
            "opta_id": None,
            "name": t["name"],
            "short_name": t.get("short_name"),
            "strength": to_int(t.get("strength")),
            "strength_attack_home": to_int(t.get("strength_attack_home")),
            "strength_attack_away": to_int(t.get("strength_attack_away")),
            "strength_defence_home": to_int(t.get("strength_defence_home")),
            "strength_defence_away": to_int(t.get("strength_defence_away")),
        }
        for t in bootstrap["teams"]
    ]


def players_rows(bootstrap: dict[str, Any]) -> list[dict[str, Any]]:
    positions = position_map(bootstrap)
    rows = []
    for e in bootstrap["elements"]:
        opta = to_str(e.get("opta_code"))
        if not opta:
            # Every player carried one in every response observed; skip rather
            # than invent a key, since opta_code is the cross-source join.
            continue
        rows.append(
            {
                "opta_code": opta,
                "fpl_element_id": e["id"],
                "pl_player_id": None,
                "fpl_code": to_int(e.get("code")),
                "first_name": to_str(e.get("first_name")),
                "second_name": to_str(e.get("second_name")),
                "web_name": to_str(e.get("web_name")),
                "birth_date": to_str(e.get("birth_date")),
                "position": positions.get(e.get("element_type")),
                "team_code": to_int(e.get("team_code")),
                "fpl_team_id": to_int(e.get("team")),
            }
        )
    return rows


def snapshot_rows(
    bootstrap: dict[str, Any], season: str, snapshot_ts: str
) -> list[dict[str, Any]]:
    """Price, ownership, form and injury state as of `snapshot_ts`.

    These are the fields the API only ever shows for *now*. Once a price change
    or an injury note is superseded upstream, the previous value is unrecoverable
    unless it was captured here.
    """
    gw = current_gameweek(bootstrap)
    rows = []
    for e in bootstrap["elements"]:
        opta = to_str(e.get("opta_code"))
        if not opta:
            continue
        rows.append(
            {
                "snapshot_ts": snapshot_ts,
                "season": season,
                "gameweek": gw,
                "opta_code": opta,
                "now_cost": to_int(e.get("now_cost")),
                "cost_change_event": to_int(e.get("cost_change_event")),
                "cost_change_start": to_int(e.get("cost_change_start")),
                "selected_by_percent": to_float(e.get("selected_by_percent")),
                "status": to_str(e.get("status")),
                "news": to_str(e.get("news")),
                "news_added": to_str(e.get("news_added")),
                "chance_of_playing_this_round": to_int(e.get("chance_of_playing_this_round")),
                "chance_of_playing_next_round": to_int(e.get("chance_of_playing_next_round")),
                "form": to_float(e.get("form")),
                "points_per_game": to_float(e.get("points_per_game")),
                "total_points": to_int(e.get("total_points")),
                "minutes": to_int(e.get("minutes")),
                "ep_this": to_float(e.get("ep_this")),
                "ep_next": to_float(e.get("ep_next")),
                "transfers_in_event": to_int(e.get("transfers_in_event")),
                "transfers_out_event": to_int(e.get("transfers_out_event")),
                "value_season": to_float(e.get("value_season")),
            }
        )
    return rows


def fixtures_rows(fixtures: list[dict[str, Any]], season: str) -> list[dict[str, Any]]:
    return [
        {
            "season": season,
            "fpl_fixture_id": f["id"],
            "pl_fixture_id": None,
            "fpl_code": to_int(f.get("code")),
            "gameweek": to_int(f.get("event")),
            "kickoff_utc": to_str(f.get("kickoff_time")),
            "team_h": to_int(f.get("team_h")),
            "team_a": to_int(f.get("team_a")),
            "team_h_score": to_int(f.get("team_h_score")),
            "team_a_score": to_int(f.get("team_a_score")),
            "team_h_difficulty": to_int(f.get("team_h_difficulty")),
            "team_a_difficulty": to_int(f.get("team_a_difficulty")),
            "finished": to_bool_int(f.get("finished")),
        }
        for f in fixtures
    ]
