"""Train, compare on the validation season, test once, then predict live.

The sequence, and why each step uses the seasons it does:
  1. Tune on train (2024-25) -> score on validate (2025-26). Every choice
     (loss, size) is made here.
  2. Refit the chosen setup on train + validate -> score on test (2026-27,
     finished matches). Looked at once: it's the honest estimate of how the
     model does on a season it has never seen.
  3. Refit on everything with a known answer -> predict the live deadline and
     write to `predictions`, baselines first and trees last, so the trees are
     the newest model_version (the UI shows the newest).
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone

import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingRegressor

from ..config import load_config
from ..features.model_rows import FEATURES
from .evaluate import SPLIT, report
from .predictions import write_predictions

TREES_VERSION = "trees_v1"

# A deliberately small grid: two losses (the decision from LEARNING §23) and
# two sizes. Bigger searches come once the first version is understood.
GRID = [
    {"loss": loss, "max_iter": n, "learning_rate": 0.05}
    for loss in ("squared_error", "poisson")
    for n in (200, 500)
]


@dataclass
class Result:
    validation: pd.DataFrame
    test: pd.DataFrame
    chosen: dict
    written: dict[str, int]


def load(conn: sqlite3.Connection) -> pd.DataFrame:
    df = pd.read_sql("SELECT * FROM model_rows", conn)
    # Trees treat a category as a set of labels, not a number (DEF isn't "2").
    df["position"] = df["position"].astype("category")
    return df


def baselines(df: pd.DataFrame) -> dict[str, pd.Series]:
    return {"baseline_season": df["points_pm"], "baseline_last4": df["points_l4"]}


def fit(df: pd.DataFrame, params: dict) -> HistGradientBoostingRegressor:
    y = df["points"].astype(float)
    if params["loss"] == "poisson":
        # Poisson can't take negative targets; the rare negative match (red
        # card, own goals; min -5) counts as 0 for this loss only.
        y = y.clip(lower=0)
    # A column with no values in the training seasons can teach nothing, and
    # scikit-learn crashes on it rather than skipping it. dc_hit_rate is the
    # real case: the defensive-contribution rule began in 2025-26, so a model
    # trained on 2024-25 alone can't use it; one trained on later seasons can.
    cols = [c for c in FEATURES if df[c].notna().any()]
    model = HistGradientBoostingRegressor(
        **params,
        categorical_features="from_dtype",
        # early_stopping would hold out a *random* slice of rows, the leak the
        # season split exists to avoid; the size is chosen on validation instead.
        early_stopping=False,
        random_state=0,
    )
    return model.fit(df[cols], y)


def predict(model: HistGradientBoostingRegressor, df: pd.DataFrame) -> pd.Series:
    # Expected points below 0 aren't meaningful; squared-error trees can dip
    # slightly under for no-hopers.
    cols = list(model.feature_names_in_)  # exactly the columns it was trained on
    return pd.Series(np.clip(model.predict(df[cols]), 0, None), index=df.index)


def name(params: dict) -> str:
    return f"trees[{params['loss']}, {params['max_iter']}]"


def run(conn: sqlite3.Connection, write: bool = True) -> Result:
    df = load(conn)
    known = df[df["points"].notna()]
    train = known[known.season.isin(SPLIT["train"])]
    val = known[known.season.isin(SPLIT["validate"])]
    test = known[known.season.isin(SPLIT["test"])]

    # 1. tune on validation
    val_preds = {**baselines(val), "fpl_xp (h=0 only)": val["baseline_fpl_xp"]}
    for params in GRID:
        val_preds[name(params)] = predict(fit(train, params), val)
    val_report = report(val, val_preds)
    all_rows = val_report[val_report.group == "all"].set_index("model")
    tree_names = [name(p) for p in GRID]
    chosen = GRID[tree_names.index(all_rows.loc[tree_names, "rmse"].idxmin())]

    # 2. test once
    model = fit(pd.concat([train, val]), chosen)
    test_preds = {**baselines(test), TREES_VERSION: predict(model, test)}
    test_report = report(test, test_preds)

    # 3. refit on every known answer, predict the live deadline
    written: dict[str, int] = {}
    if write:
        season = load_config()["current_season"]
        live = df[(df.season == season) & (df.decision_gw == df[df.season == season].decision_gw.max())]
        final = fit(known, chosen)
        outputs = {**baselines(live), TREES_VERSION: predict(final, live)}
        for version, p in outputs.items():  # trees last -> newest created_at
            created = datetime.now(timezone.utc).isoformat(timespec="microseconds")
            rows = zip(live.season, live.opta_code, live.decision_gw, live.fpl_fixture_id,
                       live.gameweek, live.horizon, p.astype(float))
            written[version] = write_predictions(conn, version, rows, created)

    return Result(val_report, test_report, chosen, written)
