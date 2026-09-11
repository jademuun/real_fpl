"""The FPL API.

Not a scraper. fantasy.premierleague.com serves the site's own JSON from
/api/ with no authentication and no HTML parsing required -- it is the same data
the website itself renders from. That makes it stable in a way an HTML scraper
never is: a CSS class change breaks a scraper, but not this.

Four endpoints matter:
    bootstrap-static/         everything about every player, right now
    fixtures/                 all 380 fixtures, with results and difficulty
    element-summary/{id}/     one player's per-match history + past seasons
    event/{gw}/live/          every player's stats for one gameweek
"""

from __future__ import annotations

import logging
import sqlite3
from datetime import datetime, timezone
from typing import Any, Callable

from ..config import load_config
from ..db import append, upsert
from ..http import fpl_fetcher
from ..transform import matches, players

log = logging.getLogger(__name__)


def _url(endpoint: str, **kwargs: Any) -> str:
    cfg = load_config()["fpl"]
    return cfg["base_url"] + cfg["endpoints"][endpoint].format(**kwargs)


def fetch_bootstrap(conn: sqlite3.Connection) -> dict[str, Any]:
    fetcher = fpl_fetcher(conn)
    return fetcher.get_json(_url("bootstrap"), label="bootstrap-static")


def ingest_snapshot(conn: sqlite3.Connection, bootstrap: dict[str, Any] | None = None) -> dict:
    """One request. Records the player list and the current point-in-time state.

    Cheap enough to run on a schedule, and the only way to build a price and
    injury history -- the API will not tell you tomorrow what it said today.
    """
    season = load_config()["current_season"]
    if bootstrap is None:
        bootstrap = fetch_bootstrap(conn)

    snapshot_ts = datetime.now(timezone.utc).isoformat(timespec="seconds")

    n_teams = upsert(conn, "teams", players.teams_rows(bootstrap, season),
                     key=("season", "fpl_team_id"))
    n_players = upsert(conn, "players", players.players_rows(bootstrap),
                       key=("opta_code",))
    n_snaps = append(conn, "player_snapshots",
                     players.snapshot_rows(bootstrap, season, snapshot_ts))

    return {
        "teams": n_teams,
        "players": n_players,
        "snapshots": n_snaps,
        "snapshot_ts": snapshot_ts,
        "gameweek": players.current_gameweek(bootstrap),
    }


def ingest_fixtures(conn: sqlite3.Connection) -> int:
    season = load_config()["current_season"]
    fetcher = fpl_fetcher(conn)
    data = fetcher.get_json(_url("fixtures"), label="fixtures")
    return upsert(conn, "fixtures", players.fixtures_rows(data, season),
                  key=("season", "fpl_fixture_id"))


def ingest_player_histories(
    conn: sqlite3.Connection,
    bootstrap: dict[str, Any] | None = None,
    progress: Callable[[int, int, str], None] | None = None,
) -> dict:
    """One request per player -- ~656 of them, rate limited.

    This is the expensive command. It is resumable by construction: every write
    is an upsert on a natural key, so an interrupted run is fixed by running it
    again, not by cleaning up first.
    """
    season = load_config()["current_season"]
    if bootstrap is None:
        bootstrap = fetch_bootstrap(conn)

    fetcher = fpl_fetcher(conn)
    roster = [
        (e["id"], e["opta_code"], e.get("web_name", "?"))
        for e in bootstrap["elements"]
        if e.get("opta_code")
    ]

    total_gw = total_season = 0
    failures: list[tuple[int, str]] = []

    for i, (element_id, opta_code, name) in enumerate(roster, start=1):
        if progress:
            progress(i, len(roster), name)
        try:
            summary = fetcher.get_json(
                _url("element_summary", element_id=element_id),
                label="element-summary",
            )
        except Exception as exc:  # noqa: BLE001 - one bad player must not end the run
            log.warning("element-summary failed for %s (%s): %s", name, element_id, exc)
            failures.append((element_id, str(exc)))
            continue

        total_gw += upsert(
            conn, "player_gw",
            matches.player_gw_rows(summary, opta_code, season),
            key=("season", "gameweek", "opta_code", "fpl_fixture_id"),
        )
        total_season += upsert(
            conn, "player_season",
            matches.player_season_rows(summary, opta_code),
            key=("season", "opta_code"),
        )

    return {
        "players_fetched": len(roster) - len(failures),
        "player_gw_rows": total_gw,
        "player_season_rows": total_season,
        "failures": failures,
    }
