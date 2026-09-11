"""Export tables to CSV or Parquet.

SQLite is the store; these are the hand-off format for pandas, Excel or
whatever the analysis stage uses. Exports are disposable -- always regenerate
rather than edit one and treat it as the source of truth.

CSV is readable and universal but forgets types (every column comes back a
string). Parquet keeps types and compresses well, and is the better choice once
the files get big enough to notice.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from .config import EXPORT_DIR
from .db import ALL_TABLES

# A player-gameweek row joined to who the player actually is -- the shape the
# feature-engineering stage will want, rather than a bare fact table.
PLAYER_GW_ENRICHED = """
SELECT
    g.season, g.gameweek, g.opta_code,
    p.web_name, p.position, p.birth_date,
    t.name AS team, o.name AS opponent,
    g.was_home, g.kickoff_utc,
    g.minutes, g.starts, g.goals_scored, g.assists,
    g.expected_goals, g.expected_assists, g.expected_goal_involvements,
    g.expected_goals_conceded, g.clean_sheets, g.goals_conceded, g.saves,
    g.yellow_cards, g.red_cards,
    g.tackles, g.recoveries, g.clearances_blocks_interceptions,
    g.defensive_contribution,
    g.influence, g.creativity, g.threat, g.ict_index,
    g.bps, g.bonus, g.total_points,
    g.value, g.selected, g.transfers_balance,
    -- price is stored in tenths of a million, as FPL serves it
    ROUND(g.value / 10.0, 1) AS price_m,
    CASE WHEN g.value > 0
         THEN ROUND(g.total_points / (g.value / 10.0), 3) END AS points_per_million
FROM player_gw g
LEFT JOIN players p ON p.opta_code = g.opta_code
LEFT JOIN teams   t ON t.fpl_team_id = p.fpl_team_id  AND t.season = g.season
LEFT JOIN teams   o ON o.fpl_team_id = g.opponent_team AND o.season = g.season
ORDER BY g.season, g.gameweek, p.web_name
"""

VIEWS = {"player_gw_enriched": PLAYER_GW_ENRICHED}


def export(conn, table: str, fmt: str = "csv", out_dir: Path | None = None) -> Path:
    if table in VIEWS:
        df = pd.read_sql_query(VIEWS[table], conn)
    elif table in ALL_TABLES:
        df = pd.read_sql_query(f"SELECT * FROM {table}", conn)
    else:
        raise ValueError(
            f"unknown table {table!r}; choose from "
            f"{', '.join(sorted(set(ALL_TABLES) | set(VIEWS)))}"
        )

    out_dir = out_dir or EXPORT_DIR
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d")
    path = out_dir / f"{table}_{stamp}.{fmt}"

    if fmt == "csv":
        df.to_csv(path, index=False)
    elif fmt == "parquet":
        df.to_parquet(path, index=False)
    else:
        raise ValueError(f"unknown format {fmt!r}; use csv or parquet")

    return path
