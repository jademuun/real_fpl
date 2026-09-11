"""Parser tests.

These run against a committed sample of a real API response, never the network.
A test that depends on a live API fails when someone gets injured, which teaches
you nothing and trains you to ignore red builds.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from real_fpl.transform import matches, players
from real_fpl.transform.coerce import to_float, to_int
from real_fpl.transform.seasons import normalise

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def bootstrap():
    return json.loads((FIXTURES / "bootstrap_sample.json").read_text())


def test_position_map(bootstrap):
    assert players.position_map(bootstrap) == {1: "GKP", 2: "DEF", 3: "MID", 4: "FWD"}


def test_players_rows_use_opta_code_as_key(bootstrap):
    rows = players.players_rows(bootstrap)
    assert len(rows) == len(bootstrap["elements"])
    assert all(r["opta_code"].startswith("p") for r in rows)
    # opta_code is the 'p'-prefixed form of FPL's integer `code`; the two must
    # agree, since the whole cross-source join rests on it.
    for r in rows:
        assert r["opta_code"] == f"p{r['fpl_code']}"


def test_teams_use_pulse_id_not_opta(bootstrap):
    """Teams carry pulse_id, not opta_code -- the opposite of players."""
    rows = players.teams_rows(bootstrap, "2026-27")
    assert all(r["pl_team_id"] is not None for r in rows)
    assert all(r["opta_id"] is None for r in rows)


def test_snapshot_rows_carry_point_in_time_fields(bootstrap):
    rows = players.snapshot_rows(bootstrap, "2026-27", "2026-09-11T19:00:00+00:00")
    assert len(rows) == len(bootstrap["elements"])
    r = rows[0]
    assert r["snapshot_ts"] == "2026-09-11T19:00:00+00:00"
    for field in ("now_cost", "selected_by_percent", "status", "form"):
        assert field in r
    assert isinstance(r["now_cost"], int)


def test_current_gameweek(bootstrap):
    gw = players.current_gameweek(bootstrap)
    assert gw is None or isinstance(gw, int)


@pytest.mark.parametrize(
    "raw,expected",
    [("2024/25", "2024-25"), ("2024-25", "2024-25"), (" 1999/00 ", "1999-00")],
)
def test_season_normalise(raw, expected):
    assert normalise(raw) == expected


def test_season_normalise_rejects_garbage():
    with pytest.raises(ValueError):
        normalise("last year")


@pytest.mark.parametrize(
    "raw,expected", [("12.4", 12), (None, None), ("", None), ("abc", None), (7, 7)]
)
def test_to_int(raw, expected):
    assert to_int(raw) == expected


@pytest.mark.parametrize("raw,expected", [("0.35", 0.35), (None, None), ("", None)])
def test_to_float(raw, expected):
    assert to_float(raw) == expected


def test_player_gw_rows_skip_rows_without_a_key():
    """A row with no round/fixture cannot be addressed by the primary key."""
    summary = {
        "history": [
            {"round": 1, "fixture": 10, "minutes": 90, "expected_goals": "0.42"},
            {"round": None, "fixture": 11, "minutes": 45},
            {"round": 2, "fixture": None, "minutes": 20},
        ]
    }
    rows = matches.player_gw_rows(summary, "p223094", "2026-27")
    assert len(rows) == 1
    assert rows[0]["gameweek"] == 1
    assert rows[0]["expected_goals"] == 0.42
    assert rows[0]["opta_code"] == "p223094"


def test_player_season_rows_normalise_the_season_label():
    summary = {
        "history_past": [
            {"season_name": "2024/25", "total_points": 142, "start_cost": 55, "minutes": 3000}
        ]
    }
    rows = matches.player_season_rows(summary, "p154561")
    assert rows[0]["season"] == "2024-25"
    assert rows[0]["total_points"] == 142
