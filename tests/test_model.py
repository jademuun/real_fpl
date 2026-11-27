"""Model plumbing: the predictions hand-off and the scoring functions.

Training itself is not unit-tested on real data (that's what the validation
report is for); these pin the parts whose mistakes would be silent.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from real_fpl import db
from real_fpl.model.evaluate import groups, metrics, poisson_deviance
from real_fpl.model.predictions import write_predictions

S = "2099-00"


@pytest.fixture
def conn(tmp_path):
    c = db.connect(tmp_path / "t.db")
    db.init_db(c)
    # Team 1: GW5 once, GW6 blank, GW7 twice.
    db.upsert(c, "fixtures", [
        {"season": S, "fpl_fixture_id": i, "gameweek": gw, "team_h": 1, "team_a": a}
        for i, gw, a in [(51, 5, 2), (61, 6, 3), (71, 7, 4), (72, 7, 2)]
    ], key=("season", "fpl_fixture_id"))
    return c


ROWS = [(S, "p1", 5, 51, 5, 0, 2.25), (S, "p1", 5, 71, 7, 2, 1.5), (S, "p1", 5, 72, 7, 2, 3.0)]


def gw_rows(conn, version="m"):
    return [tuple(r) for r in conn.execute(
        "SELECT gameweek, pred_points, n_fixtures FROM predictions_gw "
        "WHERE opta_code='p1' AND model_version=? ORDER BY gameweek", (version,))]


def test_gameweek_view_shows_blank_as_zero_and_sums_doubles(conn):
    write_predictions(conn, "m", ROWS)
    assert gw_rows(conn) == [(5, 2.25, 1), (6, 0.0, 0), (7, 4.5, 2)]


def test_rewrite_replaces_that_deadline_only(conn):
    write_predictions(conn, "m", ROWS)
    write_predictions(conn, "m", [(S, "p1", 4, 51, 5, 1, 9.0)])            # older deadline kept
    write_predictions(conn, "m", [(S, "p1", 5, 51, 5, 0, 1.0)])            # GW5 deadline replaced
    n = conn.execute("SELECT decision_gw, COUNT(*) FROM predictions GROUP BY 1").fetchall()
    assert [tuple(r) for r in n] == [(4, 1), (5, 1)]


def test_versions_coexist_and_full_precision_is_kept(conn):
    write_predictions(conn, "a", ROWS)
    write_predictions(conn, "b", [(S, "p1", 5, 51, 5, 0, 1.23456789)])
    assert conn.execute("SELECT COUNT(DISTINCT model_version) FROM predictions").fetchone()[0] == 2
    assert conn.execute("SELECT pred_points FROM predictions WHERE model_version='b'"
                        ).fetchone()[0] == pytest.approx(1.23456789)


def test_squared_error_prefers_the_mean_and_mae_the_median():
    """LEARNING §22 as a test: 0 x6, 2 x2, 10 x2 -> mean 2.4, median 0."""
    y = np.array([0] * 6 + [2] * 2 + [10] * 2, dtype=float)
    at = lambda c: metrics(y, np.full_like(y, c))  # noqa: E731
    assert at(2.4)["rmse"] < at(0)["rmse"] and at(2.4)["rmse"] < at(3)["rmse"]
    assert at(0)["mae"] < at(2.4)["mae"]


def test_poisson_deviance_is_zero_for_a_perfect_prediction():
    y = np.array([0.0, 1.0, 4.0])
    assert poisson_deviance(y, y) == pytest.approx(0.0, abs=1e-5)
    assert poisson_deviance(y, y + 1) > 0


def test_report_groups_use_only_features():
    """Grouping by the outcome ("did he play?") would leak the answer into the report."""
    df = pd.DataFrame({"minutes_l4": [90, 0], "horizon": [0, 1], "position": ["DEF", "FWD"],
                       "points": [6, 0], "actual_minutes": [90, 0]})
    for mask in groups(df.drop(columns=["points", "actual_minutes"])).values():
        assert len(mask) == 2
