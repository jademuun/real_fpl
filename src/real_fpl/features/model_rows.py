"""Features: target_rows plus everything known at the deadline, one row per match.

Same grain as target_rows (season, player, deadline t, fixture). Every feature
is measured "as of" `asof_fixture_id` -- the club's most recent finished match
before the deadline -- using running totals ordered by kickoff and cut off at
that match. Nothing from gameweek t onwards can reach a feature; the leakage
test in tests/test_features.py changes all of that data and checks that no
feature moves.

What is deliberately NOT here, and why:
  * FPL's fixture difficulty and team strength: FPL revises them during the
    season (196 of 380 fixtures changed between September and November), and
    the stored values are the latest ones -- the future leaking in.
  * Set-piece duties: only current values exist for past seasons. Todo.
  * Schedule-adjusted ratings: Step 3b, compared against these plain averages.

Feature groups:
  player   season-to-date rates (per match listed, per 90 played) and the last
           4 matches; price and position; position-aware defensive contribution
  team     the player's club: xG and goals, for and against, per match, season
           to date and last 6 matches
  opp      the same for this row's opponent, as of the same deadline
  match    horizon (how far ahead), home or away

Team xG is the sum of its players' xG in the match. Goals come from the score,
not from summing player goals, because own goals are credited to the other side.

Look-backs stay within a season in v1 (no carrying form over the summer), so
early-season rows rest on few matches; `n_listed` / `team_n` say how few.
"""

from __future__ import annotations

import sqlite3

from . import target

TABLE = "model_rows"

# The model's inputs. Listed explicitly so Phase 3 can never pick up a target
# column (points, actual_minutes) or an id by accident.
FEATURES: tuple[str, ...] = (
    # match
    "horizon", "was_home",
    # player
    "position", "price", "n_listed", "n_apps",
    "minutes_pm", "start_rate", "points_pm", "bonus_pm", "bps_pm",
    "xg90", "xa90", "xgi90", "dc_hit_rate",
    "minutes_l4", "points_l4", "xgi90_l4",
    # team
    "team_n", "team_xg_for_pm", "team_xg_against_pm", "team_gf_pm", "team_ga_pm",
    "team_xg_for_l6", "team_xg_against_l6",
    # opponent
    "opp_n", "opp_xg_for_pm", "opp_xg_against_pm", "opp_gf_pm", "opp_ga_pm",
    "opp_xg_for_l6", "opp_xg_against_l6",
)

# Defensive-contribution thresholds for the 2-point bonus (per Jade, 26 Nov):
# DEF need 10 (clearances, blocks, interceptions, tackles), MID need 12 (the same
# plus recoveries). FWD and GKP can't score it, so their rate is NULL, not 0.
DC_THRESHOLD = {"DEF": 10, "MID": 12}

STEPS: tuple[str, ...] = (
    # Each player's listed matches in kickoff order, with running totals. The
    # row for match m holds his totals over every match up to and including m,
    # so joining on the as-of match gives "season so far, as of the deadline".
    f"""
    CREATE TEMP TABLE player_running AS
    WITH m AS (
        SELECT g.season, g.opta_code, g.fpl_fixture_id, f.kickoff_utc,
               g.minutes, g.starts, g.total_points, g.bonus, g.bps,
               g.expected_goals, g.expected_assists, g.expected_goal_involvements,
               g.value,
               -- 1/0 when he played and his position can earn DC points and the
               -- stat was measured; NULL otherwise (FWD, GKP, 2024-25, no minutes)
               CASE WHEN g.minutes > 0 AND g.defensive_contribution IS NOT NULL
                         AND sp.position = 'DEF'
                    THEN g.defensive_contribution >= {DC_THRESHOLD['DEF']}
                    WHEN g.minutes > 0 AND g.defensive_contribution IS NOT NULL
                         AND sp.position = 'MID'
                    THEN g.defensive_contribution >= {DC_THRESHOLD['MID']}
               END AS dc_hit
        FROM player_gw g
        JOIN fixtures f USING (season, fpl_fixture_id)
        LEFT JOIN season_players sp ON sp.season = g.season AND sp.opta_code = g.opta_code
    )
    SELECT season, opta_code, fpl_fixture_id, value,
           COUNT(*)                         OVER w  AS n_listed,
           SUM(minutes > 0)                 OVER w  AS n_apps,
           SUM(minutes)                     OVER w  AS min_sum,
           SUM(starts)                      OVER w  AS starts_sum,
           SUM(total_points)                OVER w  AS pts_sum,
           SUM(bonus)                       OVER w  AS bonus_sum,
           SUM(bps)                         OVER w  AS bps_sum,
           SUM(expected_goals)              OVER w  AS xg_sum,
           SUM(expected_assists)            OVER w  AS xa_sum,
           SUM(expected_goal_involvements)  OVER w  AS xgi_sum,
           SUM(dc_hit)                      OVER w  AS dc_hits,
           COUNT(dc_hit)                    OVER w  AS dc_chances,
           AVG(minutes)                     OVER w4 AS minutes_l4,
           AVG(total_points)                OVER w4 AS points_l4,
           SUM(expected_goal_involvements)  OVER w4 AS xgi_l4_sum,
           SUM(minutes)                     OVER w4 AS min_l4_sum
    FROM m
    -- "Last 4" means his last 4 listed matches (ROWS), so a double gameweek
    -- counts as two. Every window ends at CURRENT ROW: the as-of match, never
    -- later. Widening it is exactly the leak the leakage test exists to catch.
    WINDOW w  AS (PARTITION BY season, opta_code ORDER BY kickoff_utc, fpl_fixture_id
                  ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW),
           w4 AS (PARTITION BY season, opta_code ORDER BY kickoff_utc, fpl_fixture_id
                  ROWS BETWEEN 3 PRECEDING AND CURRENT ROW)
    """,
    "CREATE INDEX temp.ix_pr ON player_running (season, opta_code, fpl_fixture_id)",

    # One row per team per finished match: xG (summed from its players) and
    # goals (from the score), for and against.
    """
    CREATE TEMP TABLE team_match AS
    WITH txg AS (
        SELECT g.season, g.fpl_fixture_id,
               CASE WHEN g.was_home = 1 THEN f.team_h ELSE f.team_a END AS team,
               SUM(g.expected_goals) AS xg
        FROM player_gw g JOIN fixtures f USING (season, fpl_fixture_id)
        GROUP BY 1, 2, 3
    ),
    sides AS (
        SELECT season, fpl_fixture_id, kickoff_utc, team_h AS team, team_a AS opp,
               team_h_score AS gf, team_a_score AS ga
        FROM fixtures WHERE finished = 1
        UNION ALL
        SELECT season, fpl_fixture_id, kickoff_utc, team_a, team_h,
               team_a_score, team_h_score
        FROM fixtures WHERE finished = 1
    )
    SELECT s.season, s.fpl_fixture_id, s.kickoff_utc, s.team, s.gf, s.ga,
           f.xg AS xg_for, a.xg AS xg_against
    FROM sides s
    LEFT JOIN txg f ON f.season = s.season AND f.fpl_fixture_id = s.fpl_fixture_id AND f.team = s.team
    LEFT JOIN txg a ON a.season = s.season AND a.fpl_fixture_id = s.fpl_fixture_id AND a.team = s.opp
    """,
    """
    CREATE TEMP TABLE team_running AS
    SELECT season, team, fpl_fixture_id,
           COUNT(*)         OVER w  AS n,
           AVG(xg_for)      OVER w  AS xg_for_pm,
           AVG(xg_against)  OVER w  AS xg_against_pm,
           AVG(gf)          OVER w  AS gf_pm,
           AVG(ga)          OVER w  AS ga_pm,
           AVG(xg_for)      OVER w6 AS xg_for_l6,
           AVG(xg_against)  OVER w6 AS xg_against_l6
    FROM team_match
    WINDOW w  AS (PARTITION BY season, team ORDER BY kickoff_utc, fpl_fixture_id
                  ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW),
           w6 AS (PARTITION BY season, team ORDER BY kickoff_utc, fpl_fixture_id
                  ROWS BETWEEN 5 PRECEDING AND CURRENT ROW)
    """,
    "CREATE INDEX temp.ix_tr ON team_running (season, team, fpl_fixture_id)",
    "CREATE INDEX temp.ix_tl ON team_latest (season, t, team)",

    f"""
    DROP TABLE IF EXISTS {TABLE}
    """,
    f"""
    CREATE TABLE {TABLE} AS
    SELECT
        r.season, r.opta_code, r.decision_gw, r.gameweek, r.horizon, r.fpl_fixture_id,
        r.team, r.opponent, r.was_home, r.kickoff_utc, r.asof_fixture_id,
        -- targets (never features)
        r.points, r.actual_minutes,
        -- FPL's own expected points for this match: the baseline to beat, not a
        -- feature. FPL publishes it before the gameweek, so it is only fair for
        -- the coming gameweek (horizon 0); for later ones it wasn't known yet.
        CASE WHEN r.horizon = 0 THEN g.xp END AS baseline_fpl_xp,

        sp.position,
        p.value / 10.0                                         AS price,
        p.n_listed, p.n_apps,
        1.0 * p.min_sum / p.n_listed                           AS minutes_pm,
        1.0 * p.starts_sum / p.n_listed                        AS start_rate,
        1.0 * p.pts_sum / p.n_listed                           AS points_pm,
        1.0 * p.bonus_sum / p.n_listed                         AS bonus_pm,
        1.0 * p.bps_sum / p.n_listed                           AS bps_pm,
        90.0 * p.xg_sum  / NULLIF(p.min_sum, 0)                AS xg90,
        90.0 * p.xa_sum  / NULLIF(p.min_sum, 0)                AS xa90,
        90.0 * p.xgi_sum / NULLIF(p.min_sum, 0)                AS xgi90,
        1.0 * p.dc_hits / NULLIF(p.dc_chances, 0)              AS dc_hit_rate,
        p.minutes_l4, p.points_l4,
        90.0 * p.xgi_l4_sum / NULLIF(p.min_l4_sum, 0)          AS xgi90_l4,

        tr.n AS team_n, tr.xg_for_pm AS team_xg_for_pm, tr.xg_against_pm AS team_xg_against_pm,
        tr.gf_pm AS team_gf_pm, tr.ga_pm AS team_ga_pm,
        tr.xg_for_l6 AS team_xg_for_l6, tr.xg_against_l6 AS team_xg_against_l6,

        orr.n AS opp_n, orr.xg_for_pm AS opp_xg_for_pm, orr.xg_against_pm AS opp_xg_against_pm,
        orr.gf_pm AS opp_gf_pm, orr.ga_pm AS opp_ga_pm,
        orr.xg_for_l6 AS opp_xg_for_l6, orr.xg_against_l6 AS opp_xg_against_l6
    FROM target_rows r
    -- his totals up to and including the as-of match (eligibility guarantees a row)
    JOIN player_running p
      ON p.season = r.season AND p.opta_code = r.opta_code AND p.fpl_fixture_id = r.asof_fixture_id
    LEFT JOIN season_players sp
      ON sp.season = r.season AND sp.opta_code = r.opta_code
    LEFT JOIN team_running tr
      ON tr.season = r.season AND tr.team = r.team AND tr.fpl_fixture_id = r.asof_fixture_id
    -- the opponent as of the same deadline: its own most recent match before t
    LEFT JOIN team_latest otl
      ON otl.season = r.season AND otl.t = r.decision_gw AND otl.team = r.opponent
    LEFT JOIN team_running orr
      ON orr.season = r.season AND orr.team = r.opponent AND orr.fpl_fixture_id = otl.fpl_fixture_id
    LEFT JOIN player_gw g
      ON g.season = r.season AND g.gameweek = r.gameweek
     AND g.opta_code = r.opta_code AND g.fpl_fixture_id = r.fpl_fixture_id
    """,
    # Same key as target_rows: a join that fans out would fail loudly here.
    f"CREATE UNIQUE INDEX ix_{TABLE}_key ON {TABLE} (season, opta_code, decision_gw, fpl_fixture_id)",
)

TEMP_TABLES = ("player_running", "team_match", "team_running")


def build_all(conn: sqlite3.Connection) -> dict[str, int]:
    """Rebuild target_rows, then model_rows on top of it."""
    n_target = target.build_target(conn, keep_temp=True)
    try:
        for t in TEMP_TABLES:
            conn.execute(f"DROP TABLE IF EXISTS temp.{t}")
        for step in STEPS:
            conn.execute(step)
        conn.commit()
    finally:
        target.drop_temp(conn)
        for t in TEMP_TABLES:
            conn.execute(f"DROP TABLE IF EXISTS temp.{t}")
    n_model = conn.execute(f"SELECT COUNT(*) FROM {TABLE}").fetchone()[0]
    return {target.TABLE: n_target, TABLE: n_model}
