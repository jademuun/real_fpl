"""Read-only queries behind the web pages.

Same rule as `transform/`: plain functions, a connection in and plain dicts out,
with no web framework in sight. That keeps every query testable against a tiny
database without starting a server, and keeps the routes in app.py thin: they
only decide *which* query to run and *which* template renders the answer.

Every function takes `season` explicitly instead of guessing it from the data,
so a test can build a one-season database and the app decides what "current"
means in one place (config/sources.yaml).
"""

from __future__ import annotations

import sqlite3
from typing import Any

POSITIONS = ("GKP", "DEF", "MID", "FWD")

# Whitelist of sortable columns: the key comes from the URL (?sort=price), the
# value is the SQL that sorts by it. User input only ever *selects* a key from
# this dict and is never pasted into the SQL text, which is what makes
# `ORDER BY {expr}` safe. Placeholders (?) can't be used for ORDER BY columns,
# so a whitelist is the standard answer.
SORTS: dict[str, str] = {
    "name": "p.web_name COLLATE NOCASE",
    "team": "t.short_name",
    "pos": "position",
    "price": "s.now_cost",
    "form": "s.form",
    "selected": "s.selected_by_percent",
    "points": "s.total_points",
    # Only meaningful when predictions exist; otherwise every row is NULL and
    # the sort is a no-op rather than an error.
    "pred": "pred_points",
}
DEFAULT_SORT = "points"


def _rows(cur: sqlite3.Cursor) -> list[dict[str, Any]]:
    return [dict(r) for r in cur.fetchall()]


def teams(conn: sqlite3.Connection, season: str) -> list[dict[str, Any]]:
    return _rows(
        conn.execute(
            "SELECT fpl_team_id, name, short_name FROM teams WHERE season = ? ORDER BY name",
            (season,),
        )
    )


def latest_snapshot_ts(conn: sqlite3.Connection, season: str) -> str | None:
    row = conn.execute(
        "SELECT MAX(snapshot_ts) FROM player_snapshots WHERE season = ?", (season,)
    ).fetchone()
    return row[0] if row else None


def latest_players(
    conn: sqlite3.Connection,
    season: str,
    *,
    pos: str | None = None,
    team: int | None = None,
    q: str | None = None,
    max_price: float | None = None,
    sort: str = DEFAULT_SORT,
    desc: bool = True,
    pred: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Every player in the season's most recent snapshot, filtered and sorted.

    One snapshot run writes one row per player under the same snapshot_ts, so
    "latest state" is simply the rows carrying the largest timestamp.

    Position and club come from season_players (who he is *this season*), with
    players as a fallback: `players` is current-state only and has no position
    at all for players backfilled from older seasons.
    """
    if sort not in SORTS:
        sort = DEFAULT_SORT

    where = ["s.snapshot_ts = (SELECT MAX(snapshot_ts) FROM player_snapshots WHERE season = :season)"]
    params: dict[str, Any] = {"season": season}
    if pos in POSITIONS:
        where.append("COALESCE(sp.position, p.position) = :pos")
        params["pos"] = pos
    if team is not None:
        where.append("t.fpl_team_id = :team")
        params["team"] = team
    if q:
        # Match any of the name fields, so "Bruno" and "Fernandes" both work.
        where.append(
            "(p.web_name LIKE :q OR p.first_name LIKE :q OR p.second_name LIKE :q)"
        )
        params["q"] = f"%{q}%"
    if max_price is not None:
        where.append("s.now_cost <= :max_cost")
        # Prices are stored in tenths of a million (FPL's unit); the page talks in £m.
        params["max_cost"] = round(max_price * 10)

    if pred:
        # The live prediction for the very next gameweek (horizon 0), from one
        # model. A LEFT JOIN so players the model skipped still appear.
        pred_select = "pg.pred_points, pg.n_fixtures"
        pred_join = """
        LEFT JOIN predictions_gw pg
               ON pg.season = :season AND pg.opta_code = s.opta_code
              AND pg.decision_gw = :decision_gw AND pg.gameweek = :decision_gw
              AND pg.model_version = :model_version"""
        params.update(decision_gw=pred["decision_gw"], model_version=pred["model_version"])
    else:
        pred_select = "NULL AS pred_points, NULL AS n_fixtures"
        pred_join = ""

    # SQLite sorts NULLs first in ascending order; push them last either way
    # so "no value" never tops the table.
    direction = "DESC" if desc else "ASC"
    sql = f"""
        SELECT s.opta_code, p.web_name, p.first_name, p.second_name,
               COALESCE(sp.position, p.position) AS position,
               t.short_name AS team, t.fpl_team_id,
               s.now_cost / 10.0 AS price,
               s.form, s.selected_by_percent, s.total_points,
               s.status, s.news, s.chance_of_playing_next_round,
               {pred_select}
        FROM player_snapshots s
        JOIN players p ON p.opta_code = s.opta_code
        LEFT JOIN season_players sp ON sp.opta_code = s.opta_code AND sp.season = :season
        LEFT JOIN teams t ON t.season = :season
                         AND t.fpl_team_id = COALESCE(sp.fpl_team_id, p.fpl_team_id)
        {pred_join}
        WHERE {' AND '.join(where)}
        ORDER BY {SORTS[sort]} IS NULL, {SORTS[sort]} {direction}, p.web_name COLLATE NOCASE
    """
    return _rows(conn.execute(sql, params))


def player_header(
    conn: sqlite3.Connection, opta_code: str, season: str
) -> dict[str, Any] | None:
    """Who the player is, plus his latest snapshot if he has one this season.

    Returns None for an unknown opta_code so the route can answer 404.
    """
    row = conn.execute(
        """
        SELECT p.opta_code, p.web_name, p.first_name, p.second_name, p.birth_date,
               COALESCE(sp.position, p.position) AS position,
               t.name AS team, t.short_name AS team_short,
               s.now_cost / 10.0 AS price, s.status, s.news,
               s.chance_of_playing_next_round, s.selected_by_percent, s.form,
               s.total_points, s.snapshot_ts
        FROM players p
        LEFT JOIN season_players sp ON sp.opta_code = p.opta_code AND sp.season = :season
        LEFT JOIN teams t ON t.season = :season
                         AND t.fpl_team_id = COALESCE(sp.fpl_team_id, p.fpl_team_id)
        LEFT JOIN player_snapshots s
               ON s.opta_code = p.opta_code
              AND s.snapshot_ts = (SELECT MAX(snapshot_ts) FROM player_snapshots
                                   WHERE season = :season AND opta_code = p.opta_code)
        WHERE p.opta_code = :code
        """,
        {"code": opta_code, "season": season},
    ).fetchone()
    return dict(row) if row else None


def player_seasons(conn: sqlite3.Connection, opta_code: str) -> list[str]:
    """Seasons this player has match rows for, newest first."""
    cur = conn.execute(
        "SELECT DISTINCT season FROM player_gw WHERE opta_code = ? ORDER BY season DESC",
        (opta_code,),
    )
    return [r[0] for r in cur.fetchall()]


def player_season_context(
    conn: sqlite3.Connection, opta_code: str, season: str
) -> dict[str, Any] | None:
    """Position and club *in that season*, which may differ from today's.

    This is why season_players exists: showing a 2024-25 season under the club
    the player is at now would leak the present into history.
    """
    row = conn.execute(
        """
        SELECT sp.position, t.name AS team, t.short_name AS team_short
        FROM season_players sp
        LEFT JOIN teams t ON t.season = sp.season AND t.fpl_team_id = sp.fpl_team_id
        WHERE sp.opta_code = ? AND sp.season = ?
        """,
        (opta_code, season),
    ).fetchone()
    return dict(row) if row else None


def player_gameweeks(
    conn: sqlite3.Connection, opta_code: str, season: str
) -> list[dict[str, Any]]:
    """One row per match (a double gameweek gives two rows for the same GW)."""
    return _rows(
        conn.execute(
            """
            SELECT g.gameweek, g.kickoff_utc, g.was_home,
                   o.short_name AS opponent,
                   g.minutes, g.goals_scored, g.assists,
                   g.expected_goals, g.expected_assists,
                   g.bonus, g.total_points, g.xp,
                   g.value / 10.0 AS price
            FROM player_gw g
            LEFT JOIN teams o ON o.season = g.season AND o.fpl_team_id = g.opponent_team
            WHERE g.opta_code = ? AND g.season = ?
            ORDER BY g.gameweek, g.kickoff_utc
            """,
            (opta_code, season),
        )
    )


def per_gameweek_series(matches: list[dict[str, Any]]) -> dict[str, list]:
    """Collapse matches to one point per gameweek, for the charts.

    Points add up across a double gameweek (that's how FPL scores it); price is
    the same for both matches, so the last one is kept.
    """
    by_gw: dict[int, dict[str, Any]] = {}
    for m in matches:
        gw = by_gw.setdefault(m["gameweek"], {"points": 0, "price": None})
        gw["points"] += m["total_points"] or 0
        gw["price"] = m["price"]
    gws = sorted(by_gw)
    return {
        "gameweeks": gws,
        "points": [by_gw[g]["points"] for g in gws],
        "price": [by_gw[g]["price"] for g in gws],
    }


def next_gameweek(conn: sqlite3.Connection, season: str) -> int | None:
    """The earliest gameweek that still has an unplayed fixture."""
    row = conn.execute(
        "SELECT MIN(gameweek) FROM fixtures WHERE season = ? AND finished = 0 "
        "AND gameweek IS NOT NULL",
        (season,),
    ).fetchone()
    return row[0] if row else None


def fixture_grid(
    conn: sqlite3.Connection, season: str, from_gw: int, n_gw: int = 5
) -> dict[str, Any]:
    """Teams x gameweeks, each cell a list of that team's fixtures in that GW.

    A cell is a *list* because a gameweek can hold two matches for a team
    (double gameweek) or none (blank gameweek). Both matter a lot in FPL, so the
    grid must not assume exactly one.

    Every fixture is stored once, home-team-first; here it is emitted twice,
    once from each side, each with that side's own difficulty rating.
    """
    gws = list(range(from_gw, from_gw + n_gw))
    team_rows = teams(conn, season)
    short = {t["fpl_team_id"]: t["short_name"] for t in team_rows}
    grid: dict[int, dict[int, list[dict[str, Any]]]] = {
        t["fpl_team_id"]: {gw: [] for gw in gws} for t in team_rows
    }

    fixtures = conn.execute(
        """
        SELECT gameweek, kickoff_utc, team_h, team_a,
               team_h_difficulty, team_a_difficulty
        FROM fixtures
        WHERE season = ? AND gameweek BETWEEN ? AND ?
        ORDER BY kickoff_utc
        """,
        (season, gws[0], gws[-1]),
    ).fetchall()
    for f in fixtures:
        sides = (
            (f["team_h"], f["team_a"], "H", f["team_h_difficulty"]),
            (f["team_a"], f["team_h"], "A", f["team_a_difficulty"]),
        )
        for team_id, opp_id, venue, difficulty in sides:
            if team_id in grid:
                grid[team_id][f["gameweek"]].append(
                    {"opponent": short.get(opp_id, "?"), "venue": venue,
                     "difficulty": difficulty}
                )

    rows = [
        {"team": t["name"], "short_name": t["short_name"],
         "cells": [grid[t["fpl_team_id"]][gw] for gw in gws]}
        for t in team_rows
    ]
    return {"gameweeks": gws, "rows": rows}


# --- predictions (contract: CLAUDE.md, "Contract: predictions") -------------

def _has_table(conn: sqlite3.Connection, name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE name = ? AND type IN ('table', 'view')", (name,)
    ).fetchone()
    return row is not None


def model_versions(conn: sqlite3.Connection, season: str) -> list[dict[str, Any]]:
    """Every model that has predictions this season, newest first."""
    if not _has_table(conn, "predictions"):
        return []
    return _rows(
        conn.execute(
            """
            SELECT model_version, MAX(created_at) AS created_at, MAX(decision_gw) AS decision_gw
            FROM predictions WHERE season = ?
            GROUP BY model_version ORDER BY created_at DESC
            """,
            (season,),
        )
    )


def live_predictions(
    conn: sqlite3.Connection, season: str, model_version: str | None = None
) -> dict[str, Any] | None:
    """Which predictions to show: the newest model, at its latest decision_gw.

    Several model_versions coexist on purpose (the baseline stays around to
    compare against), so "current" means the one created most recently, and
    "live" means its highest decision_gw. Returns None until the model has
    written anything -- the page then says "no predictions yet".
    """
    if not _has_table(conn, "predictions_gw") or not _has_table(conn, "predictions"):
        return None
    if model_version:
        # A specific model was asked for (the dropdown on /predictions).
        chosen = [m for m in model_versions(conn, season) if m["model_version"] == model_version]
        if chosen:
            return dict(chosen[0])
    row = conn.execute(
        """
        SELECT model_version, MAX(created_at) AS created_at
        FROM predictions WHERE season = ?
        GROUP BY model_version ORDER BY created_at DESC LIMIT 1
        """,
        (season,),
    ).fetchone()
    if row is None:
        return None
    decision_gw = conn.execute(
        "SELECT MAX(decision_gw) FROM predictions WHERE season = ? AND model_version = ?",
        (season, row["model_version"]),
    ).fetchone()[0]
    return {"model_version": row["model_version"], "created_at": row["created_at"],
            "decision_gw": decision_gw}


def player_predictions(
    conn: sqlite3.Connection, opta_code: str, season: str, pred: dict[str, Any]
) -> list[dict[str, Any]]:
    """The player's predicted points for each of the next gameweeks (horizon 0-2).

    n_fixtures says why a number is high or zero: 2 is a double gameweek,
    0 a blank one (pred_points 0.0, no match to score in).
    """
    return _rows(
        conn.execute(
            """
            SELECT gameweek, pred_points, n_fixtures
            FROM predictions_gw
            WHERE season = ? AND opta_code = ? AND decision_gw = ? AND model_version = ?
            ORDER BY gameweek
            """,
            (season, opta_code, pred["decision_gw"], pred["model_version"]),
        )
    )


# --- home page ----------------------------------------------------------------

def site_stats(conn: sqlite3.Connection, season: str) -> dict[str, Any]:
    """The headline numbers on the home page: how much data there is, how fresh."""
    row = conn.execute(
        """
        SELECT
          (SELECT COUNT(*) FROM player_snapshots
            WHERE snapshot_ts = (SELECT MAX(snapshot_ts) FROM player_snapshots
                                 WHERE season = :season))             AS players,
          (SELECT COUNT(*) FROM player_gw)                           AS matches,
          (SELECT COUNT(DISTINCT season) FROM player_gw)             AS seasons,
          (SELECT COUNT(DISTINCT snapshot_ts) FROM player_snapshots) AS snapshots,
          (SELECT MAX(snapshot_ts) FROM player_snapshots)            AS last_snapshot
        """,
        {"season": season},
    ).fetchone()
    return dict(row)


def in_form(conn: sqlite3.Connection, season: str, limit: int = 5) -> list[dict[str, Any]]:
    """Top players by FPL's `form` (average points over the last 30 days)."""
    return latest_players(conn, season, sort="form")[:limit]


def latest_news(conn: sqlite3.Connection, season: str, limit: int = 6) -> list[dict[str, Any]]:
    """The most recent injury/suspension news from the latest snapshot.

    news_added is when FPL posted the note, so this is "what changed lately",
    not just "who is injured" (that list is long and mostly stale).
    """
    return _rows(
        conn.execute(
            """
            SELECT s.opta_code, p.web_name, t.short_name AS team, s.status, s.news,
                   s.news_added, s.chance_of_playing_next_round
            FROM player_snapshots s
            JOIN players p ON p.opta_code = s.opta_code
            LEFT JOIN season_players sp ON sp.opta_code = s.opta_code AND sp.season = :season
            LEFT JOIN teams t ON t.season = :season
                             AND t.fpl_team_id = COALESCE(sp.fpl_team_id, p.fpl_team_id)
            WHERE s.snapshot_ts = (SELECT MAX(snapshot_ts) FROM player_snapshots
                                   WHERE season = :season)
              AND s.news IS NOT NULL AND s.news != ''
            ORDER BY s.news_added DESC
            LIMIT :limit
            """,
            {"season": season, "limit": limit},
        )
    )


def predictions_table(
    conn: sqlite3.Connection, season: str, pred: dict[str, Any], pos: str | None = None
) -> dict[str, Any]:
    """Players x the next three gameweeks, for one model at its live decision_gw.

    The database holds one row per player per gameweek (predictions_gw); the
    page wants one row per player with a column per gameweek. That reshaping
    ("pivot") is done here in Python: simpler to read than the SQL version,
    and ~700 players is nothing.

    Totals are summed at full precision; only the template rounds, per the
    contract.
    """
    dgw = pred["decision_gw"]
    gws = [dgw, dgw + 1, dgw + 2]
    params: dict[str, Any] = {"season": season, "dgw": dgw, "mv": pred["model_version"]}
    pos_filter = ""
    if pos in POSITIONS:
        pos_filter = "AND COALESCE(sp.position, p.position) = :pos"
        params["pos"] = pos
    cur = conn.execute(
        f"""
        SELECT pg.opta_code, pg.gameweek, pg.pred_points, pg.n_fixtures,
               p.web_name, COALESCE(sp.position, p.position) AS position,
               t.short_name AS team, s.now_cost / 10.0 AS price
        FROM predictions_gw pg
        JOIN players p ON p.opta_code = pg.opta_code
        LEFT JOIN season_players sp ON sp.opta_code = pg.opta_code AND sp.season = :season
        LEFT JOIN teams t ON t.season = :season
                         AND t.fpl_team_id = COALESCE(sp.fpl_team_id, p.fpl_team_id)
        LEFT JOIN player_snapshots s
               ON s.opta_code = pg.opta_code
              AND s.snapshot_ts = (SELECT MAX(snapshot_ts) FROM player_snapshots
                                   WHERE season = :season)
        WHERE pg.season = :season AND pg.decision_gw = :dgw AND pg.model_version = :mv
          {pos_filter}
        """,
        params,
    )
    players: dict[str, dict[str, Any]] = {}
    for r in cur.fetchall():
        row = players.setdefault(r["opta_code"], {
            "opta_code": r["opta_code"], "web_name": r["web_name"], "position": r["position"],
            "team": r["team"], "price": r["price"], "cells": {gw: None for gw in gws},
        })
        if r["gameweek"] in row["cells"]:
            row["cells"][r["gameweek"]] = {"pred": r["pred_points"], "n": r["n_fixtures"]}

    out = []
    for row in players.values():
        cells = [row["cells"][gw] for gw in gws]
        row["cells"] = cells
        row["total"] = sum(c["pred"] for c in cells if c)
        # Points per £m over the three gameweeks: the number a transfer turns on.
        row["per_m"] = row["total"] / row["price"] if row["price"] else None
        out.append(row)
    return {"gameweeks": gws, "rows": out}


# --- my team (CLAUDE.md, "Contract: entry") -----------------------------------

def my_team(
    conn: sqlite3.Connection,
    season: str,
    pred: dict[str, Any] | None,
    entry_id: int | None = None,
) -> dict[str, Any] | None:
    """A manager's latest squad: the 15 picks, each with price, status, points
    and predictions, plus the gameweek summary (bank, value, rank...).

    "Latest" is the newest gameweek with picks. FPL only reveals a team's picks
    after that gameweek's deadline, so transfers made since then are invisible
    here until the next deadline passes. The page says so.

    Returns None until the server has fetched a team (tables absent or empty).
    """
    if not (_has_table(conn, "entry_picks") and _has_table(conn, "entry_gw")):
        return None
    where, params = "season = :season", {"season": season}
    if entry_id is not None:
        where += " AND entry_id = :entry"
        params["entry"] = entry_id
    latest = conn.execute(
        f"SELECT entry_id, MAX(gameweek) AS gw FROM entry_picks WHERE {where} "
        "GROUP BY entry_id ORDER BY gw DESC, entry_id LIMIT 1",
        params,
    ).fetchone()
    if latest is None:
        return None
    entry_id, gw = latest["entry_id"], latest["gw"]

    q = {"season": season, "entry": entry_id, "gw": gw,
         "dgw": pred["decision_gw"] if pred else None,
         "mv": pred["model_version"] if pred else None}
    if pred:
        pred_cols = """
                   (SELECT pg.pred_points FROM predictions_gw pg
                     WHERE pg.season = :season AND pg.opta_code = ep.opta_code
                       AND pg.decision_gw = :dgw AND pg.model_version = :mv
                       AND pg.gameweek = :dgw)                         AS pred_next,
                   (SELECT SUM(pg.pred_points) FROM predictions_gw pg
                     WHERE pg.season = :season AND pg.opta_code = ep.opta_code
                       AND pg.decision_gw = :dgw AND pg.model_version = :mv) AS pred_3gw"""
    else:
        # predictions_gw may not exist (yet, or mid-rebuild); naming it in the
        # SQL would fail the whole query, so leave it out entirely.
        pred_cols = "NULL AS pred_next, NULL AS pred_3gw"
    picks = _rows(
        conn.execute(
            f"""
            SELECT ep.slot, ep.multiplier, ep.is_captain, ep.is_vice_captain,
                   ep.opta_code, ep.fpl_element_id,
                   -- LEFT JOIN + fallback: if the server couldn't map FPL's
                   -- element id to an opta_code (a brand-new signing), the
                   -- pick still shows instead of silently leaving a hole.
                   COALESCE(p.web_name, '#' || ep.fpl_element_id) AS web_name,
                   COALESCE(sp.position, p.position) AS position,
                   t.short_name AS team,
                   s.now_cost / 10.0 AS price, s.status, s.news,
                   s.chance_of_playing_next_round,
                   -- Points in that gameweek, summed over a double gameweek.
                   (SELECT SUM(g.total_points) FROM player_gw g
                     WHERE g.season = :season AND g.gameweek = :gw
                       AND g.opta_code = ep.opta_code)                 AS gw_points,
                   {pred_cols}
            FROM entry_picks ep
            LEFT JOIN players p ON p.opta_code = ep.opta_code
            LEFT JOIN season_players sp ON sp.opta_code = ep.opta_code AND sp.season = :season
            LEFT JOIN teams t ON t.season = :season
                             AND t.fpl_team_id = COALESCE(sp.fpl_team_id, p.fpl_team_id)
            LEFT JOIN player_snapshots s
                   ON s.opta_code = ep.opta_code
                  AND s.snapshot_ts = (SELECT MAX(snapshot_ts) FROM player_snapshots
                                       WHERE season = :season)
            WHERE ep.season = :season AND ep.entry_id = :entry AND ep.gameweek = :gw
            ORDER BY ep.slot
            """,
            q,
        )
    )
    summary = conn.execute(
        "SELECT * FROM entry_gw WHERE season = ? AND entry_id = ? AND gameweek = ?",
        (season, entry_id, gw),
    ).fetchone()

    # Is that gameweek still being played? Picks appear right after the
    # deadline, long before the matches finish, so the newest gameweek is often
    # "live": its points are partial (or 0) and must not read as final.
    live = conn.execute(
        "SELECT EXISTS (SELECT 1 FROM fixtures WHERE season = ? AND gameweek = ? AND finished = 0)",
        (season, gw),
    ).fetchone()[0] == 1
    previous = conn.execute(
        "SELECT gameweek, points FROM entry_gw WHERE season = ? AND entry_id = ? AND gameweek < ? "
        "ORDER BY gameweek DESC LIMIT 1",
        (season, entry_id, gw),
    ).fetchone()

    xi = [p for p in picks if p["slot"] <= 11]
    bench = [p for p in picks if p["slot"] > 11]
    # The pitch draws the XI in rows by position, goalkeeper at the back.
    lines = [[p for p in xi if p["position"] == pos] for pos in POSITIONS]
    unplaced = [p for p in xi if p["position"] not in POSITIONS]
    if unplaced:
        lines.append(unplaced)

    # Expected points next GW if this XI and captain stay as they are: the
    # captain's prediction counts double (or triple), exactly as FPL scores it.
    expected_next = None
    if pred and any(p["pred_next"] is not None for p in xi):
        expected_next = sum((p["pred_next"] or 0) * max(p["multiplier"], 1) for p in xi)

    return {
        "entry_id": entry_id, "gameweek": gw, "live": live,
        "previous": dict(previous) if previous else None,
        "summary": dict(summary) if summary else {},
        "lines": lines, "bench": bench, "picks": picks,
        "expected_next": expected_next,
    }
