# real_fpl — project instructions

Claude Code reads this file automatically at the start of every session in this repo. Its job is
to carry forward decisions that are expensive to rediscover, so a future session doesn't
helpfully undo them.

## What this project is

Ingest Premier League player data, then model which FPL players are worth buying and selling.
Phase 1 (ingestion) is built. Phases 2 (features) and 3 (model) are not.

## Commands

```bash
uv run real-fpl init-db
uv run real-fpl fetch-fpl --snapshot     # 1 request; price + injury state now
uv run real-fpl fetch-fpl --all          # + fixtures + 656 player histories (~6 min)
uv run real-fpl status
uv run pytest
```

Always `uv run`. There is no activated virtualenv and the system Python is PEP 668
externally-managed with no pip, so `python` and `pip` directly will not work.

## Data sources — decided, do not re-litigate

**Use:** the FPL API (`fantasy.premierleague.com/api`) and the Premier League's own API
(`footballapi.pulselive.com`). Both are public JSON. Neither needs HTML parsing or a browser.

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

## Never commit

`data/` — the database, the raw archive and exports are all reproducible. `.venv/` likewise.

## Style

Explain *why* in comments, not *what*. Erik is using this project to learn how such systems are
built, so a load-bearing decision that isn't obvious from the code should say why it's there.
