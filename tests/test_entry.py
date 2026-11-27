"""Entry (one manager's team): transforms and the incremental fetch.

Samples follow the real response shapes (checked 27 Nov 2026) with made-up
values and entry id 1. The fetch is tested with a stand-in fetcher, never the
network.
"""

from __future__ import annotations

import pytest

from real_fpl import db
from real_fpl.sources import fpl as fpl_source
from real_fpl.transform import entry

S = "2026-27"

HISTORY = {
    "current": [
        {"event": 1, "points": 65, "total_points": 65, "overall_rank": 1000, "bank": 0,
         "value": 1000, "event_transfers": 0, "event_transfers_cost": 0, "points_on_bench": 0},
        {"event": 2, "points": 40, "total_points": 105, "overall_rank": 900, "bank": 7,
         "value": 1003, "event_transfers": 2, "event_transfers_cost": 4, "points_on_bench": 9},
    ],
    "past": [],
    "chips": [{"name": "bboost", "time": "2026-08-17T22:27:19Z", "event": 1}],
}


def picks(gw):
    return {
        "active_chip": None, "automatic_subs": [], "entry_history": {"event": gw},
        "picks": [
            {"element": 10, "position": 1, "multiplier": 1, "is_captain": False,
             "is_vice_captain": False, "element_type": 1},
            {"element": 20, "position": 2, "multiplier": 2, "is_captain": True,
             "is_vice_captain": False, "element_type": 3},
            {"element": 99, "position": 12, "multiplier": 0, "is_captain": False,
             "is_vice_captain": True, "element_type": 1},
        ],
    }


def test_entry_gw_rows_carry_the_chip_of_each_gameweek():
    rows = entry.entry_gw_rows(HISTORY, S, 1)
    assert [(r["gameweek"], r["active_chip"]) for r in rows] == [(1, "bboost"), (2, None)]
    assert rows[1]["event_transfers_cost"] == 4 and rows[1]["bank"] == 7


def test_unmapped_pick_is_kept_with_its_element_id():
    rows = entry.entry_picks_rows(picks(1), S, 1, 1, {10: "p10", 20: "p20"})
    by_slot = {r["slot"]: r for r in rows}
    assert by_slot[2]["opta_code"] == "p20" and by_slot[2]["is_captain"] == 1
    assert by_slot[2]["multiplier"] == 2
    assert by_slot[12]["opta_code"] is None and by_slot[12]["fpl_element_id"] == 99
    assert by_slot[12]["multiplier"] == 0 and by_slot[12]["is_vice_captain"] == 1


class StubFetcher:
    """Answers by URL and records what was asked for."""

    def __init__(self, history):
        self.history, self.urls = history, []

    def get_json(self, url, params=None, *, label, archive=True):
        self.urls.append(url)
        if url.endswith("/history/"):
            return self.history
        return picks(int(url.rstrip("/").split("/")[-2]))


@pytest.fixture
def conn(tmp_path, monkeypatch):
    c = db.connect(tmp_path / "t.db")
    db.init_db(c)
    db.upsert(c, "season_players",
              [{"season": S, "opta_code": "p10", "fpl_element_id": 10},
               {"season": S, "opta_code": "p20", "fpl_element_id": 20}],
              key=("season", "opta_code"))
    return c


def test_fetch_only_new_gameweeks_plus_the_latest(conn, monkeypatch):
    stub = StubFetcher(HISTORY)
    monkeypatch.setattr(fpl_source, "fpl_fetcher", lambda conn: stub)

    first = fpl_source.ingest_entry(conn, 1)
    assert first["picks_fetched_for"] == [1, 2]

    history = {**HISTORY, "current": HISTORY["current"] + [{**HISTORY["current"][1], "event": 3}]}
    stub.history = history
    second = fpl_source.ingest_entry(conn, 1)
    # GW1 is settled and stored: not fetched again. GW3 is new.
    assert second["picks_fetched_for"] == [3]

    third = fpl_source.ingest_entry(conn, 1)
    assert third["picks_fetched_for"] == [3]  # only the latest, as a guard
    assert conn.execute("SELECT COUNT(*) FROM entry_picks").fetchone()[0] == 9
    assert conn.execute("SELECT COUNT(*) FROM entry_gw").fetchone()[0] == 3


def test_entry_id_comes_only_from_the_environment(monkeypatch):
    from real_fpl.config import entry_id

    monkeypatch.delenv("REAL_FPL_ENTRY_ID", raising=False)
    assert entry_id() is None
    monkeypatch.setenv("REAL_FPL_ENTRY_ID", " 42 ")
    assert entry_id() == 42
    monkeypatch.setenv("REAL_FPL_ENTRY_ID", "abc")
    with pytest.raises(ValueError):
        entry_id()
