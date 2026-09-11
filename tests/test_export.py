"""Export tests.

The enriched view is three LEFT JOINs deep. A wrong join condition there does not
raise -- it silently yields NULL team names, or duplicate rows if a join key is
not unique. Both are checked here.
"""

from __future__ import annotations

import pandas as pd
import pytest

from real_fpl import db
from real_fpl.export import export


@pytest.fixture
def conn(tmp_path):
    c = db.connect(tmp_path / "test.db")
    db.init_db(c)

    db.upsert(c, "teams", [
        {"season": "2026-27", "fpl_team_id": 11, "name": "Man City", "short_name": "MCI"},
        {"season": "2026-27", "fpl_team_id": 7, "name": "Brighton", "short_name": "BHA"},
    ], key=("season", "fpl_team_id"))

    db.upsert(c, "players", [{
        "opta_code": "p223094", "fpl_element_id": 1, "web_name": "Haaland",
        "position": "FWD", "birth_date": "2000-07-21", "fpl_team_id": 11,
    }], key=("opta_code",))

    db.upsert(c, "player_gw", [{
        "season": "2026-27", "gameweek": 1, "opta_code": "p223094",
        "fpl_fixture_id": 1, "opponent_team": 7, "was_home": 1,
        "minutes": 90, "goals_scored": 2, "expected_goals": 1.35,
        "total_points": 13, "value": 150,
    }], key=("season", "gameweek", "opta_code", "fpl_fixture_id"))
    return c


def test_enriched_export_resolves_team_names(conn, tmp_path):
    path = export(conn, "player_gw_enriched", "csv", out_dir=tmp_path)
    df = pd.read_csv(path)

    assert len(df) == 1, "joins must not multiply rows"
    row = df.iloc[0]
    assert row["web_name"] == "Haaland"
    assert row["team"] == "Man City"
    assert row["opponent"] == "Brighton"


def test_enriched_export_derives_price_columns(conn, tmp_path):
    """FPL stores price in tenths of a million; the export converts it."""
    df = pd.read_csv(export(conn, "player_gw_enriched", "csv", out_dir=tmp_path))
    row = df.iloc[0]
    assert row["price_m"] == 15.0
    assert row["points_per_million"] == pytest.approx(13 / 15.0, abs=1e-3)


def test_export_parquet_roundtrip(conn, tmp_path):
    path = export(conn, "player_gw", "parquet", out_dir=tmp_path)
    df = pd.read_parquet(path)
    assert df.iloc[0]["opta_code"] == "p223094"
    # Unlike CSV, parquet keeps the numeric type rather than returning a string.
    assert df["expected_goals"].dtype.kind == "f"


def test_export_rejects_unknown_table(conn, tmp_path):
    with pytest.raises(ValueError, match="unknown table"):
        export(conn, "nope", "csv", out_dir=tmp_path)


def test_export_rejects_unknown_format(conn, tmp_path):
    with pytest.raises(ValueError, match="unknown format"):
        export(conn, "player_gw", "xlsx", out_dir=tmp_path)
