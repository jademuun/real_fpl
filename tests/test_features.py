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
