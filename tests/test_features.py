"""Target table rules, on a tiny hand-built season where every awkward case occurs.

Teams 1-4. Gameweeks:
    GW1  1-2, 3-4                    finished
    GW2  1-3, 2-4                    finished
    GW3  2-3                         finished   (team 1 has a BLANK)
    GW4  1-4, then 2-1               finished   (team 1 has a DOUBLE)
    GW5  1-3, 2-4                    not played
    GW6  1-2                         not played
So the last fully finished gameweek is 4 and deadlines run GW2..GW5.

Players:
    p_reg   team 1 throughout (GW1, GW2, both GW4 matches)
    p_new   joins team 1 in GW4
    p_move  team 2 in GW1-2, then team 3 from GW3
    p_left  team 1 in GW1 only, then gone
"""

from __future__ import annotations

import pytest

from real_fpl import db
from real_fpl.features.target import TABLE, build_target

S = "2099-00"

FIXTURES = [
    # id, gw, kickoff, home, away, finished
    (11, 1, "2099-08-01T15:00Z", 1, 2, 1),
    (12, 1, "2099-08-01T15:00Z", 3, 4, 1),
    (21, 2, "2099-08-08T15:00Z", 1, 3, 1),
    (22, 2, "2099-08-08T15:00Z", 2, 4, 1),
    (31, 3, "2099-08-15T15:00Z", 2, 3, 1),
    (41, 4, "2099-08-22T15:00Z", 1, 4, 1),
    (42, 4, "2099-08-25T19:00Z", 2, 1, 1),
    (51, 5, "2099-08-29T15:00Z", 1, 3, 0),
    (52, 5, "2099-08-29T15:00Z", 2, 4, 0),
    (61, 6, "2099-09-05T15:00Z", 1, 2, 0),
]

PLAYER_GW = [
    # opta, fixture, gw, was_home, points, minutes
    ("p_reg", 11, 1, 1, 2, 90),
    ("p_reg", 21, 2, 1, 6, 90),
    ("p_reg", 41, 4, 1, 9, 90),
    ("p_reg", 42, 4, 0, 1, 30),
    ("p_new", 41, 4, 1, 0, 0),
    ("p_new", 42, 4, 0, 2, 60),
    ("p_move", 11, 1, 0, 1, 90),
    ("p_move", 22, 2, 1, 3, 90),
    ("p_move", 31, 3, 0, 5, 90),   # now away side = team 3
    ("p_left", 11, 1, 1, 0, 0),
]


@pytest.fixture
def conn(tmp_path):
    c = db.connect(tmp_path / "t.db")
    db.init_db(c)
    db.upsert(c, "fixtures", [
        {"season": S, "fpl_fixture_id": i, "gameweek": gw, "kickoff_utc": ko,
         "team_h": h, "team_a": a, "finished": fin}
        for i, gw, ko, h, a, fin in FIXTURES
    ], key=("season", "fpl_fixture_id"))
    db.upsert(c, "player_gw", [
        {"season": S, "gameweek": gw, "opta_code": o, "fpl_fixture_id": fx,
         "was_home": home, "total_points": pts, "minutes": mins}
        for o, fx, gw, home, pts, mins in PLAYER_GW
    ], key=("season", "gameweek", "opta_code", "fpl_fixture_id"))
    build_target(c)
    return c


def rows(conn, opta, t):
    return [tuple(r) for r in conn.execute(
        f"SELECT gameweek, horizon, fpl_fixture_id, team, points FROM {TABLE} "
        "WHERE opta_code=? AND decision_gw=? ORDER BY kickoff_utc", (opta, t))]


def test_blank_gameweek_does_not_stretch_the_window(conn):
    """Before GW2: team 1 plays GW2, nothing in GW3, twice in GW4 -- and NOT GW5,
    which "the next 3 matches" would have reached."""
    assert rows(conn, "p_reg", 2) == [
        (2, 0, 21, 1, 6),
        (4, 2, 41, 1, 9),
        (4, 2, 42, 1, 1),
    ]


def test_double_gameweek_is_two_rows_in_one_gameweek(conn):
    r = rows(conn, "p_reg", 3)
    gw4 = [x for x in r if x[0] == 4]
    assert len(gw4) == 2 and {x[1] for x in gw4} == {1}
    assert sum(x[4] for x in gw4) == 10  # the gameweek total is a sum of its rows


def test_unplayed_matches_have_null_points(conn):
    assert rows(conn, "p_reg", 5) == [(5, 0, 51, 1, None), (6, 1, 61, 1, None)]


def test_deadlines_stop_after_the_live_one(conn):
    assert {r[0] for r in conn.execute(f"SELECT DISTINCT decision_gw FROM {TABLE}")} == {2, 3, 4, 5}


def test_eligibility_uses_only_the_past(conn):
    """p_new is listed from GW4. At deadlines 2-4 nobody had seen him at the club,
    so he gets no rows -- even though, with hindsight, he played in GW4."""
    for t in (2, 3, 4):
        assert rows(conn, "p_new", t) == []
    assert [r[2] for r in rows(conn, "p_new", 5)] == [51, 61]


def test_transfer_is_followed_from_the_next_deadline(conn):
    assert {r[3] for r in rows(conn, "p_move", 3)} == {2}  # last seen at team 2 (GW2)
    assert {r[3] for r in rows(conn, "p_move", 4)} == {3}  # last seen at team 3 (GW3)


def test_player_who_left_scores_zero_then_drops_out(conn):
    """Eligible before GW2 (he was at the club); he never played again, so the
    finished GW2 match is 0 -- what owning him paid. From GW3 he is gone."""
    assert rows(conn, "p_left", 2)[0] == (2, 0, 21, 1, 0)
    assert rows(conn, "p_left", 3) == []


def test_rebuild_is_idempotent(conn):
    n = conn.execute(f"SELECT COUNT(*) FROM {TABLE}").fetchone()[0]
    assert build_target(conn) == n


# ---------------------------------------------------------------------------
# model_rows: features as of the deadline
# ---------------------------------------------------------------------------

from real_fpl.features.model_rows import FEATURES, build_all  # noqa: E402

SCORES = {11: (1, 0), 12: (2, 2), 21: (0, 1), 22: (3, 0), 31: (1, 1), 41: (2, 0), 42: (1, 1)}
POSITIONS = {"p_reg": "DEF", "p_new": "MID", "p_move": "FWD", "p_left": "GKP"}


@pytest.fixture
def built(conn):
    for fx, (h, a) in SCORES.items():
        conn.execute("UPDATE fixtures SET team_h_score=?, team_a_score=? "
                     "WHERE season=? AND fpl_fixture_id=?", (h, a, S, fx))
    # Simple, hand-checkable stats derived from each row's points and minutes.
    conn.execute("""UPDATE player_gw SET
        expected_goals = total_points / 10.0, expected_assists = 0.0,
        expected_goal_involvements = total_points / 10.0,
        starts = minutes >= 60, bonus = 0, bps = 10, value = 50,
        defensive_contribution = 11""")
    db.upsert(conn, "season_players",
              [{"season": S, "opta_code": o, "position": p} for o, p in POSITIONS.items()],
              key=("season", "opta_code"))
    conn.commit()
    build_all(conn)
    return conn


def feature(conn, opta, t, fixture, col):
    return conn.execute(f"SELECT {col} FROM model_rows WHERE opta_code=? AND decision_gw=? "
                        "AND fpl_fixture_id=?", (opta, t, fixture)).fetchone()[0]


def test_player_features_use_matches_before_the_deadline(built):
    # Before GW3: GW1 (2 pts) and GW2 (6 pts) only.
    assert feature(built, "p_reg", 3, 41, "points_pm") == pytest.approx(4.0)
    assert feature(built, "p_reg", 3, 41, "n_listed") == 2
    # Before GW5: both GW4 matches too (2+6+9+1)/4.
    assert feature(built, "p_reg", 5, 51, "points_pm") == pytest.approx(4.5)


def test_features_are_frozen_across_horizons(built):
    """The GW4 rows decided before GW2 use the same player numbers as the GW2 row."""
    for col in ("points_pm", "minutes_pm", "xgi90", "price"):
        vals = {feature(built, "p_reg", 2, fx, col) for fx in (21, 41, 42)}
        assert len(vals) == 1, col


def test_opponent_is_measured_as_of_the_same_deadline(built):
    """Before GW4, team 1's two GW4 opponents: team 4 has played 2 matches (it
    blanked GW3), team 2 has played 3. Each opponent is measured up to its own
    most recent match. (Before GW3 both had played 2: a GW3 match is not before
    the GW3 deadline -- the first draft of this test got that wrong.)"""
    assert feature(built, "p_reg", 4, 41, "opp_n") == 2   # vs team 4
    assert feature(built, "p_reg", 4, 42, "opp_n") == 3   # vs team 2


def test_dc_rate_is_null_where_it_cannot_score(built):
    assert feature(built, "p_reg", 3, 41, "dc_hit_rate") == pytest.approx(1.0)  # DEF, 11 >= 10
    assert feature(built, "p_move", 3, 31, "dc_hit_rate") is None              # FWD
    assert feature(built, "p_left", 2, 21, "dc_hit_rate") is None              # GKP


def _features_before(conn, last_t):
    cols = ", ".join(FEATURES)
    return conn.execute(
        f"SELECT season, opta_code, decision_gw, fpl_fixture_id, {cols} FROM model_rows "
        "WHERE decision_gw <= ? ORDER BY 1, 2, 3, 4", (last_t,)).fetchall()


@pytest.mark.parametrize("t", [2, 3, 4, 5])
def test_no_feature_sees_the_future(built, t):
    """THE leakage test. Rewrite everything that happened from gameweek t on --
    points, minutes, xG, price, scores -- and rebuild. Every feature on a row
    decided at or before t must be unchanged; only targets may move."""
    before = [tuple(r) for r in _features_before(built, t)]
    targets_before = built.execute("SELECT SUM(points) FROM model_rows").fetchone()[0]

    built.execute("""UPDATE player_gw SET total_points = total_points + 7, minutes = 90,
                     starts = 1, expected_goals = expected_goals + 0.9,
                     expected_goal_involvements = expected_goal_involvements + 0.9,
                     value = value + 9, bonus = 3, bps = 40, defensive_contribution = 0
                     WHERE gameweek >= ?""", (t,))
    built.execute("UPDATE fixtures SET team_h_score = team_h_score + 3 WHERE gameweek >= ?", (t,))
    built.commit()
    build_all(built)

    after = [tuple(r) for r in _features_before(built, t)]
    assert after == before
    if t <= 4:  # there were finished matches from t on, so the change must reach the targets
        assert built.execute("SELECT SUM(points) FROM model_rows").fetchone()[0] != targets_before
