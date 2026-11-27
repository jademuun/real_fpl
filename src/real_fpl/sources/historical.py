"""Backfill closed seasons from the historical mirror.

A one-off per season: a closed season's results no longer change, so this does
not need a schedule. The current season always comes from the live FPL API
(sources/fpl.py), which is why config lists only closed seasons here.

Run it on the ingestion server, like every other fetch -- the laptop's database
is a copy of the server's, and anything written only to the laptop disappears
on the next pull.
"""

from __future__ import annotations

import logging
import sqlite3

from ..config import load_config
from ..db import insert_missing, upsert
from ..http import historical_fetcher
from ..transform import historical as t

log = logging.getLogger(__name__)


def ingest_season(conn: sqlite3.Connection, season: str) -> dict:
    cfg = load_config()["historical"]
    fetcher = historical_fetcher(conn)

    def fetch(name: str) -> list[dict[str, str]]:
        url = cfg["base_url"] + cfg["files"][name].format(season=season)
        # get_text archives the CSV under data/raw/historical/<season>/<name>/
        # before it is parsed (invariant 2).
        return t.read_csv(fetcher.get_text(url, label=f"{season}/{name}"))

    players_raw = fetch("players_raw")
    teams = fetch("teams")
    fixtures = fetch("fixtures")
    merged_gw = fetch("merged_gw")

    gw_rows, unmapped = t.player_gw_rows(merged_gw, t.element_to_opta(players_raw), season)
    if unmapped:
        log.warning("%s: %d merged_gw rows had no opta_code and were skipped", season, unmapped)

    return {
        "season": season,
        "teams": upsert(conn, "teams", t.teams_rows(teams, season),
                        key=("season", "fpl_team_id")),
        "fixtures": upsert(conn, "fixtures", t.fixtures_rows(fixtures, season),
                           key=("season", "fpl_fixture_id")),
        "season_players": upsert(conn, "season_players",
                                 t.season_players_rows(players_raw, season),
                                 key=("season", "opta_code")),
        "new_players": insert_missing(conn, "players", t.past_players_rows(players_raw)),
        "player_gw": upsert(conn, "player_gw", gw_rows,
                            key=("season", "gameweek", "opta_code", "fpl_fixture_id")),
        "unmapped_rows": unmapped,
    }
