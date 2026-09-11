"""Storage invariant tests.

The point-in-time guarantee is the one thing in this project that cannot be
repaired after the fact: if a snapshot is overwritten, the old value is gone from
every source, forever. So it is enforced by database trigger and tested here.
"""

from __future__ import annotations

import sqlite3

import pytest

from real_fpl import db


@pytest.fixture
def conn(tmp_path):
    c = db.connect(tmp_path / "test.db")
    db.init_db(c)
    return c


def _snapshot(ts: str, opta: str = "p223094", cost: int = 150) -> dict:
    return {"snapshot_ts": ts, "season": "2026-27", "gameweek": 3,
            "opta_code": opta, "now_cost": cost}


def test_upsert_is_idempotent(conn):
    row = {"opta_code": "p1", "web_name": "Test", "fpl_element_id": 1}
    for _ in range(3):
        db.upsert(conn, "players", [row], key=("opta_code",))
    assert conn.execute("SELECT COUNT(*) FROM players").fetchone()[0] == 1


def test_upsert_corrects_existing_rows(conn):
    """Upstream revises finished matches; a re-fetch must overwrite, not fail."""
    db.upsert(conn, "players", [{"opta_code": "p1", "web_name": "Old"}], key=("opta_code",))
    db.upsert(conn, "players", [{"opta_code": "p1", "web_name": "New"}], key=("opta_code",))
    assert conn.execute("SELECT web_name FROM players").fetchone()[0] == "New"


def test_snapshots_accumulate_across_timestamps(conn):
    db.append(conn, "player_snapshots", [_snapshot("2026-09-11T10:00:00+00:00", cost=150)])
    db.append(conn, "player_snapshots", [_snapshot("2026-09-12T10:00:00+00:00", cost=151)])
    costs = [r[0] for r in conn.execute(
        "SELECT now_cost FROM player_snapshots ORDER BY snapshot_ts")]
    assert costs == [150, 151], "price history must be preserved, not overwritten"


def test_snapshot_reinsert_at_same_ts_is_a_noop(conn):
    db.append(conn, "player_snapshots", [_snapshot("2026-09-11T10:00:00+00:00", cost=150)])
    db.append(conn, "player_snapshots", [_snapshot("2026-09-11T10:00:00+00:00", cost=999)])
    assert conn.execute("SELECT COUNT(*) FROM player_snapshots").fetchone()[0] == 1
    assert conn.execute("SELECT now_cost FROM player_snapshots").fetchone()[0] == 150


def test_snapshots_reject_update(conn):
    db.append(conn, "player_snapshots", [_snapshot("2026-09-11T10:00:00+00:00")])
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        conn.execute("UPDATE player_snapshots SET now_cost = 1")


def test_snapshots_reject_delete(conn):
    db.append(conn, "player_snapshots", [_snapshot("2026-09-11T10:00:00+00:00")])
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        conn.execute("DELETE FROM player_snapshots")


def test_empty_writes_are_safe(conn):
    assert db.upsert(conn, "players", [], key=("opta_code",)) == 0
    assert db.append(conn, "player_snapshots", []) == 0
