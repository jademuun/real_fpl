"""Historical mirror transforms, against small hand-written CSV samples.

Each test pins one trap found while checking the real files on 27 Nov 2026.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from real_fpl import db
from real_fpl.transform import historical as t

SAMPLES = Path(__file__).parent / "fixtures" / "historical"


@pytest.fixture
def players_raw():
    return t.read_csv((SAMPLES / "players_raw.csv").read_text())


@pytest.fixture
def merged_gw():
    return t.read_csv((SAMPLES / "merged_gw.csv").read_text())


def test_player_gw_rows_map_element_to_opta(players_raw, merged_gw):
    rows, unmapped = t.player_gw_rows(merged_gw, t.element_to_opta(players_raw), "2024-25")
    assert {r["opta_code"] for r in rows} == {"p223094", "p100001"}
    # The one element absent from players_raw is counted, not silently lost.
    assert unmapped == 1


def test_assistant_managers_are_dropped(players_raw, merged_gw):
    rows, _ = t.player_gw_rows(merged_gw, t.element_to_opta(players_raw), "2024-25")
    assert "p999999" not in {r["opta_code"] for r in rows}
    assert "p999999" not in {r["opta_code"] for r in t.season_players_rows(players_raw, "2024-25")}


def test_was_home_and_xp_survive(players_raw, merged_gw):
    rows, _ = t.player_gw_rows(merged_gw, t.element_to_opta(players_raw), "2024-25")
    scott = sorted((r for r in rows if r["opta_code"] == "p223094"), key=lambda r: r["gameweek"])
    assert [r["was_home"] for r in scott] == [0, 1]
    assert [r["xp"] for r in scott] == [1.6, 3.1]
    assert scott[1]["total_points"] == 9


def test_unmeasured_stats_stay_null(players_raw, merged_gw):
    """2024-25 predates defensive-contribution scoring: missing, not zero."""
    rows, _ = t.player_gw_rows(merged_gw, t.element_to_opta(players_raw), "2024-25")
    assert all(r["defensive_contribution"] is None for r in rows)
    assert all(r["tackles"] is None for r in rows)


def test_position_comes_from_element_type(players_raw):
    """merged_gw says "GK" in 2024-25; element_type gives the API's "GKP"."""
    rows = {r["opta_code"]: r["position"] for r in t.season_players_rows(players_raw, "2024-25")}
    assert rows == {"p223094": "MID", "p100001": "GKP"}


def test_backfill_never_overwrites_current_players(tmp_path, players_raw):
    conn = db.connect(tmp_path / "t.db")
    db.init_db(conn)
    db.upsert(conn, "players",
              [{"opta_code": "p223094", "web_name": "Scott", "fpl_element_id": 401,
                "position": "MID", "fpl_team_id": 9}], key=("opta_code",))

    inserted = db.insert_missing(conn, "players", t.past_players_rows(players_raw))

    assert inserted == 1  # only the keeper was new
    current = conn.execute(
        "SELECT fpl_element_id, fpl_team_id FROM players WHERE opta_code='p223094'").fetchone()
    assert tuple(current) == (401, 9)  # this season's id and club, not 2024-25's
