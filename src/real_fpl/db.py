"""SQLite schema and write helpers.

Two kinds of table live here, and the difference matters for the modelling stage:

  Settled facts (player_gw, fixtures, player_season)
      What happened in a match. Upserted, because upstream corrects scores,
      bonus points and xG for a day or two after kickoff.

  Point-in-time state (player_snapshots)
      Price, ownership, form and injury news *as they were known at a moment*.
      Insert-only. The API shows only today's value, so an overwritten snapshot
      is gone for good -- and a model trained on end-of-season prices to predict
      that season's points has quietly learned the future.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any, Iterable, Sequence

from .config import DB_PATH

SCHEMA = """
CREATE TABLE IF NOT EXISTS teams (
    season          TEXT    NOT NULL,
    fpl_team_id     INTEGER NOT NULL,
    pl_team_id      INTEGER,
    opta_id         TEXT,
    name            TEXT    NOT NULL,
    short_name      TEXT,
    strength        INTEGER,
    strength_attack_home  INTEGER,
    strength_attack_away  INTEGER,
    strength_defence_home INTEGER,
    strength_defence_away INTEGER,
    PRIMARY KEY (season, fpl_team_id)
);

-- opta_code is the cross-source join key: FPL exposes it as `opta_code`
-- ("p223094") and the Premier League API as altIds.opta, identically.
CREATE TABLE IF NOT EXISTS players (
    opta_code       TEXT    PRIMARY KEY,
    fpl_element_id  INTEGER,
    pl_player_id    INTEGER,
    fpl_code        INTEGER,
    first_name      TEXT,
    second_name     TEXT,
    web_name        TEXT,
    birth_date      TEXT,
    position        TEXT,
    team_code       INTEGER,
    fpl_team_id     INTEGER
);
CREATE INDEX IF NOT EXISTS idx_players_element ON players (fpl_element_id);

CREATE TABLE IF NOT EXISTS fixtures (
    season             TEXT    NOT NULL,
    fpl_fixture_id     INTEGER NOT NULL,
    pl_fixture_id      INTEGER,
    fpl_code           INTEGER,
    gameweek           INTEGER,
    kickoff_utc        TEXT,
    team_h             INTEGER,
    team_a             INTEGER,
    team_h_score       INTEGER,
    team_a_score       INTEGER,
    team_h_difficulty  INTEGER,
    team_a_difficulty  INTEGER,
    finished           INTEGER,
    PRIMARY KEY (season, fpl_fixture_id)
);

CREATE TABLE IF NOT EXISTS player_gw (
    season          TEXT    NOT NULL,
    gameweek        INTEGER NOT NULL,
    opta_code       TEXT    NOT NULL,
    fpl_fixture_id  INTEGER NOT NULL,
    was_home        INTEGER,
    opponent_team   INTEGER,
    kickoff_utc     TEXT,
    minutes         INTEGER,
    starts          INTEGER,
    goals_scored    INTEGER,
    assists         INTEGER,
    expected_goals             REAL,
    expected_assists           REAL,
    expected_goal_involvements REAL,
    expected_goals_conceded    REAL,
    clean_sheets    INTEGER,
    goals_conceded  INTEGER,
    saves           INTEGER,
    penalties_saved   INTEGER,
    penalties_missed  INTEGER,
    own_goals       INTEGER,
    yellow_cards    INTEGER,
    red_cards       INTEGER,
    tackles         INTEGER,
    recoveries      INTEGER,
    clearances_blocks_interceptions INTEGER,
    defensive_contribution INTEGER,
    influence       REAL,
    creativity      REAL,
    threat          REAL,
    ict_index       REAL,
    bps             INTEGER,
    bonus           INTEGER,
    total_points    INTEGER,
    value           INTEGER,   -- price in tenths of a million AT that gameweek
    selected        INTEGER,
    transfers_balance INTEGER,
    PRIMARY KEY (season, gameweek, opta_code, fpl_fixture_id)
);
CREATE INDEX IF NOT EXISTS idx_player_gw_player ON player_gw (opta_code, season, gameweek);

CREATE TABLE IF NOT EXISTS player_season (
    season          TEXT NOT NULL,
    opta_code       TEXT NOT NULL,
    minutes         INTEGER,
    starts          INTEGER,
    total_points    INTEGER,
    goals_scored    INTEGER,
    assists         INTEGER,
    expected_goals             REAL,
    expected_assists           REAL,
    expected_goal_involvements REAL,
    expected_goals_conceded    REAL,
    clean_sheets    INTEGER,
    goals_conceded  INTEGER,
    saves           INTEGER,
    yellow_cards    INTEGER,
    red_cards       INTEGER,
    own_goals       INTEGER,
    penalties_saved  INTEGER,
    penalties_missed INTEGER,
    tackles         INTEGER,
    recoveries      INTEGER,
    clearances_blocks_interceptions INTEGER,
    defensive_contribution INTEGER,
    influence       REAL,
    creativity      REAL,
    threat          REAL,
    ict_index       REAL,
    bps             INTEGER,
    bonus           INTEGER,
    start_cost      INTEGER,
    end_cost        INTEGER,
    PRIMARY KEY (season, opta_code)
);

-- APPEND ONLY. Never UPDATE a row in this table; see module docstring.
CREATE TABLE IF NOT EXISTS player_snapshots (
    snapshot_ts     TEXT    NOT NULL,
    season          TEXT,
    gameweek        INTEGER,
    opta_code       TEXT    NOT NULL,
    now_cost        INTEGER,
    cost_change_event  INTEGER,
    cost_change_start  INTEGER,
    selected_by_percent REAL,
    status          TEXT,
    news            TEXT,
    news_added      TEXT,
    chance_of_playing_this_round INTEGER,
    chance_of_playing_next_round INTEGER,
    form            REAL,
    points_per_game REAL,
    total_points    INTEGER,
    minutes         INTEGER,
    ep_this         REAL,
    ep_next         REAL,
    transfers_in_event  INTEGER,
    transfers_out_event INTEGER,
    value_season    REAL,
    PRIMARY KEY (snapshot_ts, opta_code)
);
CREATE INDEX IF NOT EXISTS idx_snapshots_player ON player_snapshots (opta_code, snapshot_ts);

CREATE TABLE IF NOT EXISTS raw_fetches (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    url          TEXT NOT NULL,
    source       TEXT NOT NULL,
    fetched_at   TEXT NOT NULL,
    http_status  INTEGER,
    sha256       TEXT,
    path         TEXT
);
CREATE INDEX IF NOT EXISTS idx_raw_source ON raw_fetches (source, fetched_at);

-- Guard the append-only rule at the database level rather than trusting every
-- future caller to remember it.
CREATE TRIGGER IF NOT EXISTS player_snapshots_no_update
BEFORE UPDATE ON player_snapshots
BEGIN
    SELECT RAISE(ABORT, 'player_snapshots is append-only: updating a snapshot destroys the point-in-time record the models depend on');
END;

CREATE TRIGGER IF NOT EXISTS player_snapshots_no_delete
BEFORE DELETE ON player_snapshots
BEGIN
    SELECT RAISE(ABORT, 'player_snapshots is append-only: rows must not be deleted');
END;
"""

# Tables whose contents are derived and can be rebuilt from data/raw/.
FACT_TABLES = ("teams", "players", "fixtures", "player_gw", "player_season")
ALL_TABLES = FACT_TABLES + ("player_snapshots", "raw_fetches")


def connect(path: Path | None = None) -> sqlite3.Connection:
    db_path = Path(path) if path is not None else DB_PATH
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    return conn


def init_db(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)
    conn.commit()


def upsert(
    conn: sqlite3.Connection,
    table: str,
    rows: Sequence[dict[str, Any]],
    key: Sequence[str],
) -> int:
    """INSERT ... ON CONFLICT DO UPDATE over `key`, so re-runs are idempotent.

    Upstream revises finished matches for a day or two (bonus points, xG), so
    re-fetching must correct existing rows rather than fail or duplicate.
    """
    if not rows:
        return 0
    cols = list(rows[0].keys())
    placeholders = ", ".join("?" for _ in cols)
    updates = [c for c in cols if c not in key]
    set_clause = ", ".join(f"{c}=excluded.{c}" for c in updates)
    sql = (
        f"INSERT INTO {table} ({', '.join(cols)}) VALUES ({placeholders}) "
        f"ON CONFLICT ({', '.join(key)}) DO "
        + (f"UPDATE SET {set_clause}" if updates else "NOTHING")
    )
    conn.executemany(sql, [tuple(r[c] for c in cols) for r in rows])
    conn.commit()
    return len(rows)


def append(conn: sqlite3.Connection, table: str, rows: Sequence[dict[str, Any]]) -> int:
    """Insert-only write for point-in-time tables.

    Uses DO NOTHING rather than DO UPDATE: re-running a snapshot within the same
    timestamp is a harmless no-op, but it must never overwrite what was recorded.
    """
    if not rows:
        return 0
    cols = list(rows[0].keys())
    placeholders = ", ".join("?" for _ in cols)
    sql = (
        f"INSERT INTO {table} ({', '.join(cols)}) VALUES ({placeholders}) "
        f"ON CONFLICT DO NOTHING"
    )
    conn.executemany(sql, [tuple(r[c] for c in cols) for r in rows])
    conn.commit()
    return len(rows)


def table_counts(conn: sqlite3.Connection, tables: Iterable[str] = ALL_TABLES) -> dict[str, int]:
    out: dict[str, int] = {}
    for t in tables:
        try:
            out[t] = conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
        except sqlite3.OperationalError:
            out[t] = -1
    return out
