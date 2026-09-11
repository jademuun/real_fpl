# real_fpl

Premier League player data ingestion, and (later) a model that suggests which FPL players to buy
and sell.

**New here?** [`docs/LEARNING.md`](docs/LEARNING.md) explains how and why this project is built
the way it is — how the data sources were chosen, why `uv`, and the three design decisions that
actually matter.

## Setup

Requires [uv](https://docs.astral.sh/uv/). This machine's system Python is PEP 668
externally-managed with no pip, so `uv` is what makes the project installable:

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
uv sync
```

## Usage

```bash
uv run real-fpl init-db                    # create the SQLite schema
uv run real-fpl fetch-fpl --snapshot       # one request; price + injury state right now
uv run real-fpl fetch-fpl --all            # + fixtures + every player's match history
uv run real-fpl export                     # CSV / Parquet out
uv run real-fpl status                     # row counts and last-fetch times
```

`fetch-fpl --snapshot` is the one worth scheduling. Price, ownership and injury news are the
fields the API only ever shows you *as of now* — miss a day and that day is gone for good.

## Data sources

Two, both public JSON, neither requiring a browser or HTML scraping:

- **FPL API** (`fantasy.premierleague.com/api`) — the backbone. Supplies per-player per-match xG,
  xA, tackles, recoveries, minutes, cards, bonus, and the player's price at each gameweek.
- **Premier League official** (`footballapi.pulselive.com`) — the official Opta feed, for match
  detail, lineups and referees.

They join exactly on Opta ID (`opta_code` in FPL, `altIds.opta` on the PL side).

See [CLAUDE.md](CLAUDE.md) for why FBref, Understat, FotMob and WhoScored are deliberately not
used.

## Status

Built: the FPL ingestion path — schema, snapshots, fixtures, per-player match history, export.

Not built yet: the Premier League official source module (`fetch-pl`), historical backfill of
past seasons, feature engineering, and the model. The FPL API alone already covers price, xG, xA,
minutes, cards, prior-season points and injury state, so those are additive rather than blocking.

## Layout

```
config/sources.yaml     endpoints, season ids, rate limits
data/raw/               immutable gzipped API responses
data/fpl.db             SQLite store
data/exports/           generated CSV / Parquet
src/real_fpl/
  http.py               rate-limited session, retries, raw-response archiving
  db.py                 schema and upsert helpers
  sources/              one module per upstream source
  transform/            raw JSON -> table rows
```
