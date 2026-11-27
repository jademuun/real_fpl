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
    r = client.get("/players")
    assert r.status_code == 200
    assert "<html" in r.text and "Salah" in r.text and "Doubtful 75%" in r.text


def test_htmx_request_gets_only_the_table(client):
    r = client.get("/players?pos=GKP", headers={"HX-Request": "true"})
    assert r.status_code == 200
    assert "<html" not in r.text
    assert "Raya" in r.text and "Salah" not in r.text


def test_empty_form_fields_are_ignored(client):
    # A submitted form sends team= and max_price= as empty strings.
    r = client.get("/players?q=&pos=&team=&max_price=&sort=bogus")
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


# --- predictions (contract in CLAUDE.md) ------------------------------------
# The model hasn't written any yet, so these build fake rows matching the
# contract. predictions_gw is a view on the model side; here it is a plain
# table with the same columns, which is all a reader can see anyway.

@pytest.fixture
def pred_db(db_path):
    c = sqlite3.connect(db_path)
    c.executescript("""
        CREATE TABLE predictions (
            season TEXT NOT NULL, opta_code TEXT NOT NULL, decision_gw INTEGER NOT NULL,
            fpl_fixture_id INTEGER NOT NULL, gameweek INTEGER NOT NULL, horizon INTEGER NOT NULL,
            pred_points REAL NOT NULL, model_version TEXT NOT NULL, created_at TEXT NOT NULL,
            PRIMARY KEY (season, opta_code, decision_gw, fpl_fixture_id, model_version));
        CREATE TABLE predictions_gw (
            season TEXT, opta_code TEXT, decision_gw INTEGER, gameweek INTEGER,
            model_version TEXT, pred_points REAL, n_fixtures INTEGER);
    """)
    old, new = "2026-09-01T00:00:00Z", "2026-09-02T00:00:00Z"
    c.executemany(
        "INSERT INTO predictions VALUES (?,?,?,?,?,?,?,?,?)",
        [
            # The baseline is older: the newer trees_v1 must win.
            (SEASON, "p1", 4, 10, 4, 0, 9.9, "baseline_last4", old),
            (SEASON, "p1", 3, 99, 3, 0, 1.0, "trees_v1", new),   # superseded decision_gw
            (SEASON, "p1", 4, 10, 4, 0, 4.26, "trees_v1", new),
        ],
    )
    c.executemany(
        "INSERT INTO predictions_gw VALUES (?,?,?,?,?,?,?)",
        [
            (SEASON, "p1", 4, 4, "baseline_last4", 9.9, 1),
            (SEASON, "p1", 3, 3, "trees_v1", 1.0, 1),
            (SEASON, "p1", 4, 4, "trees_v1", 4.26, 1),
            (SEASON, "p1", 4, 5, "trees_v1", 10.04, 2),
            (SEASON, "p1", 4, 6, "trees_v1", 0.0, 0),
            (SEASON, "p2", 4, 4, "trees_v1", 7.0, 2),
        ],
    )
    c.commit()
    c.close()
    return db_path


def test_no_predictions_yet(conn, client):
    assert queries.live_predictions(conn, SEASON) is None
    assert "No predictions yet" in client.get("/players").text
    assert "No predictions yet" in client.get("/player/p1").text


def test_live_predictions_pick_newest_model_and_latest_gw(pred_db):
    c = sqlite3.connect(pred_db)
    c.row_factory = sqlite3.Row
    live = queries.live_predictions(c, SEASON)
    assert (live["model_version"], live["decision_gw"]) == ("trees_v1", 4)
    rows = queries.latest_players(c, SEASON, pred=live, sort="pred")
    assert [(r["web_name"], r["pred_points"]) for r in rows] == [
        ("Salah", 7.0), ("Saka", 4.26), ("Raya", None)]  # unpredicted player last
    assert [r["n_fixtures"] for r in queries.player_predictions(c, "p1", SEASON, live)] == [1, 2, 0]
    c.close()


def test_predictions_shown_to_one_decimal(pred_db):
    client = TestClient(create_app(pred_db, season=SEASON))
    page = client.get("/players").text
    assert "Pred GW4" in page and "4.3" in page and "DGW" in page
    player = client.get("/player/p1").text
    assert "10.0" in player and "Double GW" in player and "Blank GW" in player


def test_missing_table_means_refreshing_not_crash(db_path):
    # Simulates the model worktree mid-rebuild, or pull-db.sh swapping the file.
    c = sqlite3.connect(db_path)
    c.execute("DROP TABLE fixtures")
    c.commit()
    c.close()
    r = TestClient(create_app(db_path, season=SEASON)).get("/fixtures")
    assert r.status_code == 503 and "refreshing" in r.text



# --- home, about, team colours ------------------------------------------------

def test_home_page(client):
    r = client.get("/")
    assert r.status_code == 200
    assert "Real FPL" in r.text and "In form" in r.text
    assert "Knock" in r.text  # Salah's news
    assert 'href="#kit-LIV"' in r.text  # Liverpool's shirt icon
    assert 'id="kit-LIV"' in r.text    # ...and the sprite it points at


def test_about_renders_readme(client):
    r = client.get("/about")
    assert r.status_code == 200
    assert "<h1>real_fpl</h1>" in r.text
    # The diagram is handed to mermaid.js, not shown as a code block.
    assert '<pre class="mermaid">' in r.text


def test_unknown_team_gets_fallback_kit():
    from real_fpl.web.teams import FALLBACK, kit_id, team_style
    assert kit_id("XYZ") == kit_id(None) == "kit-none"
    assert FALLBACK.body in team_style("XYZ")


def test_every_kit_is_distinct():
    # The whole point of kits over colours: no two clubs may look identical.
    from real_fpl.web.teams import KITS
    looks = [(k.body, k.sleeves, k.stripes, k.trim) for k in KITS.values()]
    assert len(set(looks)) == len(looks)


def test_players_fragment_carries_chart_data(client):
    r = client.get("/players?pos=GKP", headers={"HX-Request": "true"})
    assert 'id="value-data"' in r.text and '"name": "Raya"' in r.text
    assert '"name": "Salah"' not in r.text  # filtered out of the chart too


def test_predictions_page_empty_state(client):
    r = client.get("/predictions")
    assert r.status_code == 200 and "No predictions yet" in r.text


def test_predictions_page_table(pred_db):
    client = TestClient(create_app(pred_db, season=SEASON))
    r = client.get("/predictions")
    assert r.status_code == 200 and "trees_v1 (newest)" in r.text
    # Saka: 4.26 + 10.04 + 0.0 = 14.3 over GW4-6, at £10.0m.
    assert "14.3" in r.text and "1.43" in r.text and "blank" in r.text
    # The older model is still selectable, and shows its own numbers.
    old = client.get("/predictions?model=baseline_last4")
    assert "9.9" in old.text


def test_predictions_table_pivot(pred_db):
    c = sqlite3.connect(pred_db)
    c.row_factory = sqlite3.Row
    live = queries.live_predictions(c, SEASON)
    t = queries.predictions_table(c, SEASON, live)
    saka = next(r for r in t["rows"] if r["web_name"] == "Saka")
    assert t["gameweeks"] == [4, 5, 6]
    assert [cell["n"] for cell in saka["cells"]] == [1, 2, 0]
    assert saka["total"] == pytest.approx(14.30)  # full precision, not rounded
    salah = next(r for r in t["rows"] if r["web_name"] == "Salah")
    assert salah["cells"][1] is None  # no row for GW5: shown as a dash
    c.close()


# --- my team (proposed contract: entry_picks + entry_gw) ----------------------
# Fake entry id 1: a real manager's id never goes into committed files.

ENTRY_DDL = """
    CREATE TABLE entry_picks (
        season TEXT NOT NULL, entry_id INTEGER NOT NULL, gameweek INTEGER NOT NULL,
        opta_code TEXT NOT NULL, fpl_element_id INTEGER NOT NULL, slot INTEGER NOT NULL,
        multiplier INTEGER NOT NULL, is_captain INTEGER NOT NULL, is_vice_captain INTEGER NOT NULL,
        PRIMARY KEY (season, entry_id, gameweek, slot));
    CREATE TABLE entry_gw (
        season TEXT NOT NULL, entry_id INTEGER NOT NULL, gameweek INTEGER NOT NULL,
        points INTEGER, total_points INTEGER, overall_rank INTEGER, bank INTEGER, value INTEGER,
        event_transfers INTEGER, event_transfers_cost INTEGER, points_on_bench INTEGER,
        active_chip TEXT, PRIMARY KEY (season, entry_id, gameweek));
"""


def _add_team(path):
    c = sqlite3.connect(path)
    c.executescript(ENTRY_DDL)
    c.executemany(
        "INSERT INTO entry_picks VALUES (?,?,?,?,?,?,?,?,?)",
        [
            # An older gameweek's picks: must be ignored in favour of GW2.
            (SEASON, 1, 1, "p2", 22, 1, 2, 1, 0),
            (SEASON, 1, 2, "p3", 33, 1, 1, 0, 0),    # GKP
            (SEASON, 1, 2, "p1", 11, 2, 2, 1, 0),    # MID, captain
            (SEASON, 1, 2, "p999", 999, 3, 1, 0, 0),  # element the server couldn't map
            (SEASON, 1, 2, "p2", 22, 12, 0, 0, 1),   # bench, vice-captain
        ],
    )
    c.execute(
        "INSERT INTO entry_gw VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        (SEASON, 1, 2, 30, 95, 123456, 15, 1002, 1, 4, 6, None),
    )
    c.commit()
    c.close()


def test_team_page_empty_state(client):
    r = client.get("/team")
    assert r.status_code == 200 and "No team yet" in r.text


def test_my_team_latest_gameweek_and_captaincy(db_path):
    _add_team(db_path)
    c = sqlite3.connect(db_path)
    c.row_factory = sqlite3.Row
    squad = queries.my_team(c, SEASON, pred=None)
    assert squad["gameweek"] == 2 and squad["entry_id"] == 1
    assert [p["web_name"] for p in squad["picks"]] == ["Raya", "Saka", "#999", "Salah"]
    assert [p["web_name"] for p in squad["bench"]] == ["Salah"]
    # GKP line first, then MIDs; the unmapped pick still gets a place.
    assert [[p["web_name"] for p in line] for line in squad["lines"] if line] == [
        ["Raya"], ["Saka"], ["#999"]]
    saka = squad["picks"][1]
    assert saka["gw_points"] == 8  # double gameweek: 2 + 6
    assert squad["expected_next"] is None  # no predictions table
    c.close()


def test_team_page_with_predictions(pred_db):
    _add_team(pred_db)
    client = TestClient(create_app(pred_db, season=SEASON))
    r = client.get("/team")
    assert r.status_code == 200
    assert "£100.2m" in r.text and "£1.5m" in r.text and "123,456" in r.text
    # Saka is captain: his 8 GW2 points show as 16 on the pitch.
    assert "16 pts" in r.text
    # Expected next GW = Saka 4.26 x 2 (captain); Raya has no prediction.
    assert "8.5" in r.text
