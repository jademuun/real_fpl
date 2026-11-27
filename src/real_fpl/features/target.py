"""The answer key: one row per player per upcoming match, as seen from a deadline.

Grain, in one sentence:

    one row per (season, player, decision gameweek t, fixture), for every
    fixture of the player's club in gameweeks t, t+1 and t+2.

"Decision gameweek t" means standing just before GW t's deadline. Everything
the model may use is fixed as of that moment, even for a match two weeks out,
because that is all anyone deciding before GW t could know. `horizon` (0/1/2)
says how far ahead the match is, so one model can learn that week-3 guesses are
less certain than this week's.

The rules, each pinned by a test in tests/test_features.py:

  * A double gameweek is two rows sharing one `gameweek`: two opponents, two
    venues, two predictions. The gameweek total is a sum over those rows.
  * A blank gameweek has no row. With no opponent there is nothing to predict;
    its 0 belongs in the per-gameweek summary, not in training data.
  * The window is gameweek *numbers* t..t+2, never "the next 3 matches": with a
    blank gameweek in the way, "next 3 matches" would reach into GW t+3.
  * Who gets rows is decided from the past only: the player must have been
    listed for his club's most recent match before t (every squad player is
    listed, minutes or not). Requiring him to still be there at t+2 would
    choose rows by looking at the future.
  * His club is the club of that most recent match, so a January transfer is
    followed from the first deadline after it.
  * `points` is the target: whole FPL points actually scored, unclipped (it can
    be negative). If the match is finished and he has no row for it (he left
    the club mid-window), points = 0: what owning him paid for that match. If
    the match isn't played yet, points is NULL: a row to predict.
  * No decisions before GW2: with nothing played yet there is no "as of".
    Look-backs don't cross the summer in v1.

The table is derived, so it is rebuilt from scratch every run (nothing in it
can't be recomputed from player_gw and fixtures).
"""

from __future__ import annotations

import sqlite3

TABLE = "target_rows"

SCHEMA = f"""
DROP TABLE IF EXISTS {TABLE};
CREATE TABLE {TABLE} (
    season          TEXT    NOT NULL,
    opta_code       TEXT    NOT NULL,
    decision_gw     INTEGER NOT NULL,  -- standing before this gameweek's deadline
    gameweek        INTEGER NOT NULL,  -- gameweek of the match being predicted
    horizon         INTEGER NOT NULL,  -- gameweek - decision_gw: 0, 1 or 2
    fpl_fixture_id  INTEGER NOT NULL,
    team            INTEGER NOT NULL,  -- player's club as of the deadline
    opponent        INTEGER NOT NULL,
    was_home        INTEGER NOT NULL,
    kickoff_utc     TEXT,
    -- The club's most recent finished match before the deadline. Every
    -- feature is measured "up to and including this match" -- the precise
    -- meaning of "as of the deadline".
    asof_fixture_id INTEGER NOT NULL,
    -- Outcomes. Never features: they are what happened in the match itself.
    points          INTEGER,           -- the target; NULL = not played yet
    actual_minutes  INTEGER,           -- for a later "will he play?" model
    -- The primary key turns a logic bug that double-counts a match into a
    -- loud error instead of silently inflated targets.
    PRIMARY KEY (season, opta_code, decision_gw, fpl_fixture_id)
);
"""

# Built as a sequence of temporary tables rather than one big WITH query.
# A first single-query version took minutes: SQLite re-derived the stacked
# fixture list per row and, with no index on "players listed for fixture X",
# scanned the whole season of player_gw for every (deadline, team) pair --
# about 400 million row checks. Each step below is computed once and indexed
# on exactly the columns the next step looks up by. EXPLAIN QUERY PLAN is how
# to see this: "SEARCH ... USING INDEX (season=?)" on a big table inside a loop
# means a near-full scan per row.
STEPS: tuple[str, ...] = (
    # Each fixture once from each side, so "a team's matches" is one filter.
    """
    CREATE TEMP TABLE team_fixtures AS
    SELECT season, fpl_fixture_id, gameweek, kickoff_utc, finished,
           team_h AS team, team_a AS opponent, 1 AS was_home
    FROM fixtures WHERE gameweek IS NOT NULL
    UNION ALL
    SELECT season, fpl_fixture_id, gameweek, kickoff_utc, finished,
           team_a, team_h, 0
    FROM fixtures WHERE gameweek IS NOT NULL
    """,
    "CREATE INDEX temp.ix_tf ON team_fixtures (season, team, gameweek)",

    # Deadlines: GW2 up to the one after the last fully finished gameweek. For
    # the current season that last deadline is the live one.
    """
    CREATE TEMP TABLE decisions AS
    WITH progress AS (
        SELECT season,
               COALESCE(MIN(CASE WHEN COALESCE(finished, 0) = 0 THEN gameweek END) - 1,
                        MAX(gameweek)) AS last_done
        FROM fixtures WHERE gameweek IS NOT NULL
        GROUP BY season
    )
    SELECT DISTINCT f.season, f.gameweek AS t
    FROM fixtures f JOIN progress p USING (season)
    WHERE f.gameweek >= 2 AND f.gameweek <= p.last_done + 1
    """,

    # A team's finished match m is "its most recent match" for every deadline t
    # with  m.gameweek < t <= next match's gameweek. In a double gameweek the
    # first match's range is empty (the next match is in the same gameweek), so
    # only the later one counts -- hence ordering by kickoff, not gameweek.
    """
    CREATE TEMP TABLE team_latest AS
    SELECT d.season, d.t, m.team, m.fpl_fixture_id
    FROM (
        SELECT season, team, fpl_fixture_id, gameweek AS from_gw,
               LEAD(gameweek) OVER (PARTITION BY season, team
                                    ORDER BY kickoff_utc, fpl_fixture_id) AS until_gw
        FROM team_fixtures WHERE finished = 1
    ) m
    JOIN decisions d
      ON d.season = m.season
     AND d.t > m.from_gw
     AND (m.until_gw IS NULL OR d.t <= m.until_gw)
    """,

    # Which club each player was listed for in each match (every squad player
    # is listed, with or without minutes).
    """
    CREATE TEMP TABLE player_matches AS
    SELECT g.season, g.fpl_fixture_id, g.opta_code,
           CASE WHEN g.was_home = 1 THEN f.team_h ELSE f.team_a END AS team
    FROM player_gw g JOIN fixtures f USING (season, fpl_fixture_id)
    """,
    "CREATE INDEX temp.ix_pm ON player_matches (season, fpl_fixture_id, team)",

    # Eligible: listed for his club's most recent match before t. That club is
    # his club as of the deadline.
    """
    CREATE TEMP TABLE eligible AS
    SELECT tl.season, tl.t, pm.opta_code, pm.team, tl.fpl_fixture_id AS asof_fixture_id
    FROM team_latest tl
    JOIN player_matches pm
      ON pm.season = tl.season
     AND pm.fpl_fixture_id = tl.fpl_fixture_id
     AND pm.team = tl.team
    """,

    # One row per upcoming match of his club in gameweeks t..t+2.
    f"""
    INSERT INTO {TABLE}
    SELECT e.season, e.opta_code, e.t, tf.gameweek, tf.gameweek - e.t,
           tf.fpl_fixture_id, e.team, tf.opponent, tf.was_home, tf.kickoff_utc,
           e.asof_fixture_id,
           CASE WHEN tf.finished = 1 THEN COALESCE(g.total_points, 0) END,
           CASE WHEN tf.finished = 1 THEN COALESCE(g.minutes, 0) END
    FROM eligible e
    JOIN team_fixtures tf
      ON tf.season = e.season
     AND tf.team = e.team
     AND tf.gameweek BETWEEN e.t AND e.t + 2   -- gameweek numbers, not "next 3 rows"
    LEFT JOIN player_gw g
      ON g.season = e.season
     AND g.opta_code = e.opta_code
     AND g.fpl_fixture_id = tf.fpl_fixture_id
    """,
)

TEMP_TABLES = ("team_fixtures", "decisions", "team_latest", "player_matches", "eligible")


def build_target(conn: sqlite3.Connection, keep_temp: bool = False) -> int:
    """Rebuild target_rows. keep_temp leaves the helper tables (team_latest...)
    in place for the feature build that runs straight after."""
    conn.executescript(SCHEMA)
    for t in TEMP_TABLES:
        conn.execute(f"DROP TABLE IF EXISTS temp.{t}")
    for step in STEPS:
        conn.execute(step)
    conn.commit()
    if not keep_temp:
        drop_temp(conn)
    return conn.execute(f"SELECT COUNT(*) FROM {TABLE}").fetchone()[0]


def drop_temp(conn: sqlite3.Connection) -> None:
    for t in TEMP_TABLES:
        conn.execute(f"DROP TABLE IF EXISTS temp.{t}")
