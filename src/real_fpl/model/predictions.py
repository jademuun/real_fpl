"""The `predictions` table and `predictions_gw` view: the model -> UI hand-off.

The shape is a contract with the UI (CLAUDE.md, "Contract: predictions"). The UI
reads column names, `n_fixtures`, and picks the current model as the newest
created_at. Changing any of those means telling the UI agent first.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from typing import Iterable

SCHEMA = """
CREATE TABLE IF NOT EXISTS predictions (
    season          TEXT    NOT NULL,
    opta_code       TEXT    NOT NULL,
    decision_gw     INTEGER NOT NULL,
    fpl_fixture_id  INTEGER NOT NULL,
    gameweek        INTEGER NOT NULL,
    horizon         INTEGER NOT NULL,
    pred_points     REAL    NOT NULL,   -- expected points, full precision
    model_version   TEXT    NOT NULL,
    created_at      TEXT    NOT NULL,
    PRIMARY KEY (season, opta_code, decision_gw, fpl_fixture_id, model_version)
);

-- One row per player per gameweek in his 3-gameweek window. A blank gameweek
-- appears explicitly with n_fixtures = 0 and pred_points = 0.0; a double
-- gameweek has n_fixtures = 2 and the sum of both matches. Summing per-match
-- predictions is only valid because each is an expectation (LEARNING §22).
CREATE VIEW IF NOT EXISTS predictions_gw AS
WITH heads AS (
    SELECT DISTINCT season, opta_code, decision_gw, model_version FROM predictions
),
gws AS (
    SELECT DISTINCT season, gameweek FROM fixtures WHERE gameweek IS NOT NULL
)
SELECT h.season, h.opta_code, h.decision_gw, g.gameweek, h.model_version,
       COALESCE(SUM(p.pred_points), 0.0) AS pred_points,
       COUNT(p.fpl_fixture_id)           AS n_fixtures
FROM heads h
JOIN gws g
  ON g.season = h.season AND g.gameweek BETWEEN h.decision_gw AND h.decision_gw + 2
LEFT JOIN predictions p
  ON p.season = h.season AND p.opta_code = h.opta_code
 AND p.decision_gw = h.decision_gw AND p.model_version = h.model_version
 AND p.gameweek = g.gameweek
GROUP BY h.season, h.opta_code, h.decision_gw, g.gameweek, h.model_version;
"""


def ensure_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)


def write_predictions(
    conn: sqlite3.Connection,
    model_version: str,
    rows: Iterable[tuple[str, str, int, int, int, int, float]],
    created_at: str | None = None,
) -> int:
    """Replace this model_version's predictions for the (season, decision_gw)
    pairs being written, in one transaction.

    rows: (season, opta_code, decision_gw, fpl_fixture_id, gameweek, horizon,
    pred_points). Predictions for other deadlines are kept, so last week's live
    predictions survive this week's run -- that is the honest record of how the
    model did when it couldn't know the answer.
    """
    ensure_schema(conn)
    rows = list(rows)
    created_at = created_at or datetime.now(timezone.utc).isoformat(timespec="seconds")
    deadlines = {(r[0], r[2]) for r in rows}
    with conn:  # one transaction: the UI never sees a half-written version
        for season, t in deadlines:
            conn.execute("DELETE FROM predictions WHERE model_version=? AND season=? "
                         "AND decision_gw=?", (model_version, season, t))
        conn.executemany(
            "INSERT INTO predictions (season, opta_code, decision_gw, fpl_fixture_id, "
            "gameweek, horizon, pred_points, model_version, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [(*r, model_version, created_at) for r in rows],
        )
    return len(rows)
