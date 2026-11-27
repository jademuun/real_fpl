#!/usr/bin/env bash
# Make this machine's data/fpl.db a copy of the database on the ingestion server.
#
# Why pull instead of fetching locally: only one machine may run the fetchers.
# player_snapshots is append-only and its values can't be re-fetched later, so two
# machines fetching on their own would each hold snapshots the other lacks -- two
# diverging histories that can't be cleanly merged. Every other machine is a
# read-only copy of the one that fetches.
#
# Why sqlite3_rsync instead of scp/rsync: the database runs in WAL mode, so recent
# writes can still sit in fpl.db-wal, and copying fpl.db alone can give a stale or
# torn file. sqlite3_rsync goes through SQLite itself, is safe while the server is
# writing, and only transfers pages that differ. It needs SQLite >= 3.47 and must be
# installed on BOTH machines (it runs a copy of itself on the far end over ssh).
#
# Configuration is per machine, so it lives in the environment, not in the repo:
#   REAL_FPL_REMOTE_DB   user@host:path/to/fpl.db   (required; path relative to the
#                        remote home directory, or absolute)
#   REAL_FPL_REMOTE_EXE  sqlite3_rsync on the remote (default .local/bin/sqlite3_rsync;
#                        a command run over ssh doesn't get the login PATH, so a bare
#                        name usually isn't found)
set -euo pipefail

: "${REAL_FPL_REMOTE_DB:?set it to user@host:path/to/fpl.db, e.g. in ~/.bashrc}"
remote_exe="${REAL_FPL_REMOTE_EXE:-.local/bin/sqlite3_rsync}"

# Resolve the repo from the script's own location, so it works from any directory.
repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$repo_root"

# ORIGIN first, REPLICA second. Swapping them would overwrite the server's
# database -- the only complete copy -- with this one.
sqlite3_rsync "$REAL_FPL_REMOTE_DB" data/fpl.db --exe "$remote_exe" -v

uv run real-fpl status
