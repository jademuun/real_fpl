"""Phase 2: derived tables built from the facts, for the model to learn from.

Everything here is rebuilt from scratch on each run. Unlike player_snapshots,
none of it is history that could be lost: it is all recomputable from
player_gw and fixtures, so a rebuild is always safe.
"""
