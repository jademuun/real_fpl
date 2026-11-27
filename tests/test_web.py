"""Web UI tests: queries directly, and each page through FastAPI's TestClient.

TestClient calls the app in-process -- no server, no port, no browser -- and
returns the HTML the browser would have got. The database is a tiny one built
here, never the real one.
"""

from __future__ import annotations

import sqlite3

import pytest
from fastapi.testclient import TestClient

from real_fpl import db
from real_fpl.web import queries
from real_fpl.web.app import create_app

SEASON = "2026-27"


@pytest.fixture
def db_path(tmp_path):
    path = tmp_path / "web.db"
    c = db.connect(path)
    db.init_db(c)
    db.upsert(c, "teams", [
        {"season": SEASON, "fpl_team_id": 1, "name": "Arsenal", "short_name": "ARS"},
        {"season": SEASON, "fpl_team_id": 2, "name": "Liverpool", "short_name": "LIV"},
        {"season": "2025-26", "fpl_team_id": 7, "name": "Chelsea", "short_name": "CHE"},
    ], key=["season", "fpl_team_id"])
    db.upsert(c, "players", [
        {"opta_code": "p1", "web_name": "Saka", "first_name": "Bukayo", "second_name": "Saka",
         "position": "MID", "fpl_team_id": 1},
        {"opta_code": "p2", "web_name": "Salah", "first_name": "Mohamed", "second_name": "Salah",
         "position": "MID", "fpl_team_id": 2},
        {"opta_code": "p3", "web_name": "Raya", "first_name": "David", "second_name": "Raya",
         "position": "GKP", "fpl_team_id": 1},
    ], key=["opta_code"])
    db.upsert(c, "season_players", [
        {"season": SEASON, "opta_code": "p1", "position": "MID", "fpl_team_id": 1},
        {"season": SEASON, "opta_code": "p2", "position": "MID", "fpl_team_id": 2},
        {"season": SEASON, "opta_code": "p3", "position": "GKP", "fpl_team_id": 1},
        # Last season p1 was at a different club: the player page must say so.
        {"season": "2025-26", "opta_code": "p1", "position": "MID", "fpl_team_id": 7},
    ], key=["season", "opta_code"])
    # db.append takes its column list from the first row, so every row carries
    # the same keys.
    snap = {"season": SEASON, "gameweek": 3, "form": 5.0, "selected_by_percent": 10.0,
            "chance_of_playing_next_round": None, "news": None}
    old, new = "2026-09-01T08:00:00+00:00", "2026-09-02T08:00:00+00:00"
    db.append(c, "player_snapshots", [
        # An older snapshot with a different price: only the latest may show.
        {**snap, "snapshot_ts": old, "opta_code": "p1", "now_cost": 95, "total_points": 10,
         "status": "a"},
        {**snap, "snapshot_ts": new, "opta_code": "p1", "now_cost": 100, "total_points": 20,
         "status": "a"},
        {**snap, "snapshot_ts": new, "opta_code": "p2", "now_cost": 130, "total_points": 30,
         "status": "d", "chance_of_playing_next_round": 75, "news": "Knock"},
        {**snap, "snapshot_ts": new, "opta_code": "p3", "now_cost": 55, "total_points": 15,
         "status": "a"},
    ])
    gw = {"minutes": 90, "goals_scored": 0, "assists": 0, "bonus": 0}
    db.upsert(c, "player_gw", [
        {**gw, "season": SEASON, "gameweek": 1, "opta_code": "p1", "fpl_fixture_id": 1,
         "opponent_team": 2, "was_home": 1, "total_points": 8, "value": 100},
        # A double gameweek: two matches in GW2.
        {**gw, "season": SEASON, "gameweek": 2, "opta_code": "p1", "fpl_fixture_id": 3,
         "opponent_team": 2, "was_home": 0, "total_points": 2, "value": 100},
        {**gw, "season": SEASON, "gameweek": 2, "opta_code": "p1", "fpl_fixture_id": 4,
         "opponent_team": 2, "was_home": 1, "total_points": 6, "value": 100},
        {**gw, "season": "2025-26", "gameweek": 1, "opta_code": "p1", "fpl_fixture_id": 9,
         "opponent_team": None, "was_home": 1, "total_points": 3, "value": 90},
    ], key=["season", "gameweek", "opta_code", "fpl_fixture_id"])
    db.upsert(c, "fixtures", [
        {"season": SEASON, "fpl_fixture_id": 10, "gameweek": 4, "team_h": 1, "team_a": 2,
         "team_h_difficulty": 4, "team_a_difficulty": 3, "finished": 0,
         "kickoff_utc": "2026-09-10T15:00:00Z"},
        {"season": SEASON, "fpl_fixture_id": 11, "gameweek": 5, "team_h": 2, "team_a": 1,
         "team_h_difficulty": 2, "team_a_difficulty": 5, "finished": 0,
         "kickoff_utc": "2026-09-17T15:00:00Z"},
        {"season": SEASON, "fpl_fixture_id": 12, "gameweek": 5, "team_h": 1, "team_a": 2,
         "team_h_difficulty": 4, "team_a_difficulty": 3, "finished": 0,
         "kickoff_utc": "2026-09-20T15:00:00Z"},
    ], key=["season", "fpl_fixture_id"])
    c.close()
    return path


@pytest.fixture
def conn(db_path):
    c = sqlite3.connect(db_path)
    c.row_factory = sqlite3.Row
    yield c
    c.close()


@pytest.fixture
def client(db_path):
    return TestClient(create_app(db_path, season=SEASON))


# --- queries ---------------------------------------------------------------

def test_latest_players_uses_only_newest_snapshot(conn):
    rows = queries.latest_players(conn, SEASON)
    assert [r["web_name"] for r in rows] == ["Salah", "Saka", "Raya"]  # points desc
    saka = next(r for r in rows if r["opta_code"] == "p1")
    assert saka["price"] == 10.0 and saka["total_points"] == 20


def test_latest_players_filters(conn):
    assert [r["web_name"] for r in queries.latest_players(conn, SEASON, pos="GKP")] == ["Raya"]
    assert [r["web_name"] for r in queries.latest_players(conn, SEASON, team=2)] == ["Salah"]
    assert [r["web_name"] for r in queries.latest_players(conn, SEASON, q="bukayo")] == ["Saka"]
    assert {r["web_name"] for r in queries.latest_players(conn, SEASON, max_price=10.0)} == {
        "Saka", "Raya"}


def test_unknown_sort_falls_back_to_default(conn):
    # A sort key that isn't whitelisted must never reach the SQL.
    rows = queries.latest_players(conn, SEASON, sort="1; DROP TABLE players")
    assert [r["web_name"] for r in rows] == ["Salah", "Saka", "Raya"]


def test_double_gameweek_sums_points_in_chart_series(conn):
    series = queries.per_gameweek_series(queries.player_gameweeks(conn, "p1", SEASON))
    assert series == {"gameweeks": [1, 2], "points": [8, 8], "price": [10.0, 10.0]}


def test_season_context_is_the_club_at_the_time(conn):
    assert queries.player_season_context(conn, "p1", "2025-26")["team"] == "Chelsea"


def test_fixture_grid_shows_both_sides_and_doubles(conn):
    grid = queries.fixture_grid(conn, SEASON, from_gw=4, n_gw=2)
    ars = next(r for r in grid["rows"] if r["short_name"] == "ARS")
    gw4, gw5 = ars["cells"]
    assert gw4 == [{"opponent": "LIV", "venue": "H", "difficulty": 4}]
    assert [(f["venue"], f["difficulty"]) for f in gw5] == [("A", 5), ("H", 4)]
    assert queries.next_gameweek(conn, SEASON) == 4


# --- pages -----------------------------------------------------------------

def test_players_page(client):
    r = client.get("/")
    assert r.status_code == 200
    assert "<html" in r.text and "Salah" in r.text and "Doubtful 75%" in r.text


def test_htmx_request_gets_only_the_table(client):
    r = client.get("/?pos=GKP", headers={"HX-Request": "true"})
    assert r.status_code == 200
    assert "<html" not in r.text
    assert "Raya" in r.text and "Salah" not in r.text


def test_empty_form_fields_are_ignored(client):
    # A submitted form sends team= and max_price= as empty strings.
    r = client.get("/?q=&pos=&team=&max_price=&sort=bogus")
    assert r.status_code == 200 and "Saka" in r.text


def test_player_page(client):
    r = client.get("/player/p1")
    assert r.status_code == 200 and "Bukayo Saka" in r.text
    r = client.get("/player/p1?season=2025-26")
    assert "Chelsea" in r.text


def test_unknown_player_is_404(client):
    assert client.get("/player/nope").status_code == 404


def test_fixtures_page(client):
    r = client.get("/fixtures")
    assert r.status_code == 200 and "LIV (H)" in r.text and "fdr-5" in r.text


def test_connection_is_read_only(db_path):
    uri = db_path.resolve().as_uri() + "?mode=ro"
    c = sqlite3.connect(uri, uri=True)
    with pytest.raises(sqlite3.OperationalError, match="readonly"):
        c.execute("DELETE FROM teams")
