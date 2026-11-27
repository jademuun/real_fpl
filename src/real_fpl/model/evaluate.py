"""Scoring predictions against what happened.

Primary measure: RMSE (square root of the mean squared error). Squared error is
the one that rewards predicting the *mean*, which is what per-match expected
points must be so they can be summed (LEARNING §22). MAE is shown because it is
easy to read ("off by about 1 point"), but never used to pick a winner.

Also reported:
  poisson_dev  Poisson deviance: the natural error measure for non-negative
               counts with many zeros. Lower is better.
  corr         does the prediction order players right? (ignores level)
  bias         mean prediction minus mean actual: is the level right?

Every score is given overall and per group, because 36% of rows are players
who haven't played this season: predicting ~0 for them is trivially right and
makes all models look alike. The groups are where models actually differ.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

# Season-based split, never random: neighbouring rows share target matches
# (LEARNING §18), so a random split would leak answers into training.
SPLIT = {"train": ("2024-25",), "validate": ("2025-26",), "test": ("2026-27",)}


def poisson_deviance(y: np.ndarray, p: np.ndarray) -> float:
    """Mean Poisson deviance. Targets are clipped at 0 (the rare negative match
    score) and predictions floored just above 0, where the formula is defined."""
    y = np.clip(y, 0, None)
    p = np.clip(p, 1e-6, None)
    with np.errstate(divide="ignore", invalid="ignore"):
        ylogy = np.where(y > 0, y * np.log(y / p), 0.0)
    return float(np.mean(2 * (ylogy - (y - p))))


def metrics(y: np.ndarray, p: np.ndarray) -> dict[str, float]:
    y = np.asarray(y, dtype=float)
    p = np.asarray(p, dtype=float)
    return {
        "n": len(y),
        "rmse": float(np.sqrt(np.mean((p - y) ** 2))),
        "poisson_dev": poisson_deviance(y, p),
        "mae": float(np.mean(np.abs(p - y))),
        "corr": float(np.corrcoef(p, y)[0, 1]) if len(y) > 1 and np.std(p) > 0 else float("nan"),
        "bias": float(np.mean(p) - np.mean(y)),
    }


def groups(df: pd.DataFrame) -> dict[str, pd.Series]:
    """Row masks for the report. All defined from features (the past), never
    from the outcome -- grouping by "did he play?" would itself be a leak."""
    return {
        "all": pd.Series(True, index=df.index),
        "regulars (60+ min, last 4)": df["minutes_l4"] >= 60,
        "next match only (h=0)": df["horizon"] == 0,
        "GKP": df["position"] == "GKP",
        "DEF": df["position"] == "DEF",
        "MID": df["position"] == "MID",
        "FWD": df["position"] == "FWD",
    }


def report(df: pd.DataFrame, preds: dict[str, pd.Series]) -> pd.DataFrame:
    """One row per (group, model): the scores of each prediction column."""
    out = []
    for gname, mask in groups(df).items():
        for model, p in preds.items():
            m = mask & p.notna() & df["points"].notna()
            if m.sum() == 0:
                continue
            out.append({"group": gname, "model": model,
                        **metrics(df.loc[m, "points"], p[m])})
    return pd.DataFrame(out)
