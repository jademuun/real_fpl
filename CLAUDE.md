# real_fpl — project instructions

Claude Code reads this file automatically at the start of every session in this repo. Its job is
to carry forward decisions that are expensive to rediscover, so a future session doesn't
helpfully undo them.

## What this project is

Ingest Premier League player data, then model which FPL players are worth buying and selling.
Phase 1 (ingestion) is built, plus a backfill of 2024-25 and 2025-26. Phase 2 (features) is in
progress; Phase 3 (model) is not started.

Decided 2026-11-27: the target is **FPL points per match, for each of the player's fixtures in the
next 3 gameweeks, predicted separately** (not one summed number). Features are as of before GW t
for all three; a `horizon` column (0/1/2) says how far ahead the match is. A 3-GW total, if
needed, is the sum of the per-match predictions. A double gameweek = two rows sharing one
`gameweek`; a blank gameweek = no training row (it shows as 0 only in per-gameweek summaries).
`points` (the target) is whole actual points; `pred_points` is stored at full precision and only
*displayed* to one decimal. Built by `real-fpl build-features` into `target_rows`
(`src/real_fpl/features/target.py`); derived tables are rebuilt from scratch, never upserted. The model ladder is
baselines (last-4 average, FPL's `xP`), then a Tweedie/Poisson GLM, then gradient-boosted trees,
each evaluated on a **season-based split, never random**. Ranking and transfer decisions are a
later Part 2.

## Commands

```bash
uv run real-fpl init-db
uv run real-fpl fetch-fpl --snapshot     # 1 request; price + injury state now
uv run real-fpl fetch-fpl --all          # + fixtures + 656 player histories (~6 min)
uv run real-fpl fetch-historical         # one-off: closed seasons from the mirror
uv run real-fpl build-features           # rebuild derived Phase 2 tables (~3 s)
uv run real-fpl status
uv run pytest
```

Always `uv run`. There is no activated virtualenv and the system Python is PEP 668
externally-managed with no pip, so `python` and `pip` directly will not work.

## Data sources — decided, do not re-litigate

**Use:** the FPL API (`fantasy.premierleague.com/api`) and the Premier League's own API
(`footballapi.pulselive.com`). Both are public JSON. Neither needs HTML parsing or a browser.

**Also use, for closed seasons only:** the vaastav/Fantasy-Premier-League GitHub mirror
(`historical:` in `config/sources.yaml`). It is FPL's own data scraped weekly into CSV, so it is
not a new source of truth, just the only place FPL's per-match history survives once a season
ends. Approved by Jade 2026-11-27 for the 2024-25 and 2025-26 backfill. Never use it for the
current season; the live API is authoritative there. Its `teams.csv` strength and fixture
difficulty are end-of-season values: never features (leakage).

**Deliberately excluded**, each checked live on 2026-09-11:

| Source | Why not |
|---|---|
| FBref | HTTP 403 behind a Cloudflare interstitial. Would need a headless browser. |
| Understat | Page is now client-rendered — the old embedded `JSON.parse(...)` payload is gone. `robots.txt` is a blanket `Disallow: /`. FPL already provides xG/xA. |
| FotMob | `robots.txt` explicitly disallows `/api/*`. |
| WhoScored | Incapsula bot protection over licensed Opta data. |

If richer stats are ever needed (progressive passes, touches in box), that is an FBref problem
and needs a real browser — do not reach for Understat, it no longer works the way tutorials say.

Opta itself is not directly accessible; it's Stats Perform's commercial licensed feed. Both
sources above *are* Opta data via licensed distribution.

## The three invariants

Breaking any of these is a correctness bug, not a style preference.

### 1. `opta_code` is the join key

FPL exposes `opta_code` (`"p223094"`) and the PL API exposes `altIds.opta` — identical values,
verified on 656/656 players. FPL's integer `code` is the same number without the `p`.

Join on this. Never join on player names. Never join on `fpl_element_id`, which is reassigned
between seasons.

Teams are the exception: they have **no** Opta code. They carry `pulse_id`, which is the PL
site's team id. That's the team-side join key.

### 2. Raw responses are archived before parsing

`Fetcher.get_json` gzips every response into `data/raw/<source>/<endpoint>/` and logs it in
`raw_fetches` before returning it. Parsers then work from disk.

This means a parser bug costs a re-parse, not a re-fetch of data that has since changed upstream.
Keep it that way — do not add a code path that parses a response without archiving it.

### 3. `player_snapshots` is append-only

Price, ownership, form, `ep_next` and injury news are only ever served *as of now*. Once
superseded upstream, the previous value is unrecoverable from any source.

So that table is insert-only, enforced by SQLite triggers that abort on UPDATE and DELETE. Use
`db.append()` for it and `db.upsert()` for everything else. If a trigger ever fires, the fix is
the calling code, not the trigger.

The reason is modelling, not tidiness: training on end-of-season prices to predict that season's
points leaks the future into the features, and the model scores brilliantly offline and loses
money in practice.

## Conventions

- Settled facts (`player_gw`, `fixtures`, `player_season`) are upserted on natural keys, so every
  command is safe to re-run and an interrupted run is fixed by running it again.
- Endpoints, season ids and rate limits live in `config/sources.yaml`, not as constants in code.
- Transforms in `src/real_fpl/transform/` are pure functions from parsed JSON to row dicts. Keep
  them free of network and database access so they stay testable.
- Tests run against committed samples in `tests/fixtures/`, never the live API.
- Coerce types at the boundary — FPL returns numerics as strings (`"12.4"`).

## Working with several agents

Several Claude sessions work on this repo at once, each in its **own git worktree and branch**:

| Worktree | Branch | Owns |
|---|---|---|
| `~/repo/real_fpl` | `model` | features and model (`src/real_fpl/features/`, later `model/`); `data/`, `docs/` |
| `~/repo/real_fpl-ui` | `ui` | the web UI (`src/real_fpl/web/`) |

- **Never commit to `master`.** It only receives merges via pull request, and the home server
  runs `master`, so nothing half-built reaches it. Pick up other agents' merged work with
  `git rebase master` on your own branch.
- **Stay in your own worktree and directories.** Shared files (`cli.py`, `pyproject.toml`,
  `README.md`) will conflict at merge time, which is fine. Never resolve a `uv.lock` conflict by
  hand: take either side, then run `uv lock`.
- **There is one database**, `~/repo/real_fpl/data/fpl.db`, written only by the model worktree
  (and replaced wholesale by `scripts/pull-db.sh`). Other worktrees point at it with
  `REAL_FPL_DB` and **open it read-only** (`sqlite3.connect("file:...?mode=ro", uri=True)`), never
  through `db.connect()` + `init_db()`, which create tables and run migrations. Derived tables
  (`target_rows`, `predictions`) are dropped and rebuilt for a few seconds at a time, so a reader
  must treat "no such table" or a locked database as "data refreshing", not crash.
- `docs/LEARNING.md` and `docs/NEXT-SESSION.md` exist only in `~/repo/real_fpl/docs/`
  (gitignored). Append there by full path; don't start copies elsewhere.

### Contract: `predictions` (model writes, UI reads)

Agreed 2026-11-27 so the UI can be built against fake rows before the model exists. Changing a
column is a change to both sides: update this section first.

```sql
CREATE TABLE predictions (
    season          TEXT    NOT NULL,
    opta_code       TEXT    NOT NULL,   -- player; join to players / season_players
    decision_gw     INTEGER NOT NULL,   -- predicted as of just before this gameweek's deadline
    fpl_fixture_id  INTEGER NOT NULL,   -- the match; join to fixtures
    gameweek        INTEGER NOT NULL,   -- gameweek of that match
    horizon         INTEGER NOT NULL,   -- gameweek - decision_gw: 0, 1 or 2
    pred_points     REAL    NOT NULL,   -- expected FPL points, full precision
    model_version   TEXT    NOT NULL,   -- e.g. 'baseline_last4', 'trees_v1'
    created_at      TEXT    NOT NULL,   -- ISO-8601 UTC
    PRIMARY KEY (season, opta_code, decision_gw, fpl_fixture_id, model_version)
);
```

Plus a view `predictions_gw` with one row per (season, opta_code, decision_gw, gameweek,
model_version): `pred_points` summed over that gameweek's matches and `n_fixtures`
(2 = double gameweek, **0 = blank gameweek with `pred_points` 0.0**).

Rules for readers:
- **Display** `pred_points` rounded to one decimal; never round stored values or sums.
- Several `model_version`s coexist (the baseline and each model, for comparison). Show one at a
  time; the "current" one is the latest `created_at`.
- The live predictions are the rows with the season's highest `decision_gw`.
- Until the model exists the table is absent: show "no predictions yet".

## Never commit

`data/` — the database, the raw archive and exports are all reproducible. `.venv/` likewise.

## Style

Explain *why* in comments, not *what*. Jade is using this project to learn how such systems are
built, so a load-bearing decision that isn't obvious from the code should say why it's there.
