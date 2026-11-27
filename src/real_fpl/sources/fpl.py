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
from ..transform import entry, matches, players

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
    upsert(conn, "season_players", players.season_players_rows(bootstrap, season),
           key=("season", "opta_code"))
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


def ingest_entry(conn: sqlite3.Connection, entry_id: int) -> dict:
    """One manager's team: gameweek history, plus picks for each gameweek not
    yet stored. 1 request + 1 per new gameweek (about 2 a day once caught up).

    Picks are only public once a gameweek's deadline has passed, and don't
    change after it, so a stored gameweek isn't fetched again -- except the
    latest one, re-fetched each run as a cheap guard against a partial store.
    Runs after ingest_snapshot, so season_players already maps this season's
    element ids to opta_code.
    """
    season = load_config()["current_season"]
    fetcher = fpl_fetcher(conn)
    history = fetcher.get_json(_url("entry_history", entry_id=entry_id), label="entry-history")
    gw_rows = entry.entry_gw_rows(history, season, entry_id)
    upsert(conn, "entry_gw", gw_rows, key=("season", "entry_id", "gameweek"))

    have = {r[0] for r in conn.execute(
        "SELECT DISTINCT gameweek FROM entry_picks WHERE season=? AND entry_id=?",
        (season, entry_id))}
    gws = sorted(r["gameweek"] for r in gw_rows)
    todo = [gw for gw in gws if gw not in have] + ([gws[-1]] if gws and gws[-1] in have else [])

    mapping = {r[0]: r[1] for r in conn.execute(
        "SELECT fpl_element_id, opta_code FROM season_players WHERE season=?", (season,))}
    n_picks = unmapped = 0
    for gw in todo:
        picks = fetcher.get_json(_url("entry_picks", entry_id=entry_id, gw=gw), label="entry-picks")
        rows = entry.entry_picks_rows(picks, season, entry_id, gw, mapping)
        unmapped += sum(r["opta_code"] is None for r in rows)
        n_picks += upsert(conn, "entry_picks", rows, key=("season", "entry_id", "gameweek", "slot"))
    if unmapped:
        log.warning("entry %s: %d picks had no opta_code mapping (stored with NULL)", entry_id, unmapped)
    return {"gameweeks": len(gw_rows), "picks_fetched_for": todo, "picks": n_picks,
            "unmapped": unmapped}
