"""Phase 3: baselines and models that predict FPL points per match.

The ladder (each must beat the one below on the validation season):
    baseline_season  his season-to-date points per match
    baseline_last4   his points per match over his last 4 matches
    trees_v1         gradient-boosted trees on model_rows FEATURES
FPL's own xP is scored alongside for comparison (next match only), but never
written as a prediction: it doesn't exist for the current season.
"""
