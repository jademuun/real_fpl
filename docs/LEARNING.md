# How this project was built, and why

Notes on the reasoning behind `real_fpl` — written to be transferable to your next project, not
just to explain this one.

---

## 1. The first step is not code. It's checking your assumptions.

You asked which of FotMob, Understat, the PL official site and WhoScored to scrape. The instinct
is to pick one and start writing a parser. That's the wrong first move, and here's the concrete
proof.

Before writing a line, I sent one HTTP request to each candidate. Results:

- **FBref** → `HTTP 403`, Cloudflare interstitial.
- **Understat** → `HTTP 200`, but only 18,699 bytes, *and the same 18,699 bytes for every season
  URL*. The page no longer embeds its data. Every tutorial online still tells you to regex
  `JSON.parse(...)` out of the HTML; that stopped working.
- **FotMob** → its `robots.txt` explicitly says `Disallow: /api/*`.
- **FPL's own API** → `HTTP 200`, 656 players, 109 fields each — including xG, xA, tackles,
  recoveries, cards, minutes and price-at-each-gameweek.

That last one reframed the whole project. **The data you wanted mostly didn't need scraping at
all.** A day of work building an Understat parser would have been a day spent building something
that (a) no longer works and (b) was redundant.

> **Transferable rule:** spend the first hour probing sources with `curl`, not designing. The
> goal of that hour is to *kill options*. Cheap questions first: does it return 200? Is it JSON
> or HTML? Does `robots.txt` allow it? Is the data in the HTML or loaded later by JavaScript?

### The hierarchy of "scraping"

Not all data extraction is equal. Always take the highest tier available:

| Tier | What it is | Fragility |
|---|---|---|
| 1. Documented API | Official, versioned, with terms | Very low |
| 2. **Undocumented JSON API** | The site's own frontend calls it | Low — **both our sources are here** |
| 3. Data embedded in HTML | JSON in a `<script>` tag | Medium — Understat *was* here |
| 4. HTML parsing | BeautifulSoup over the rendered page | High — breaks on any redesign |
| 5. Browser automation | Selenium/Playwright driving a real browser | Highest — slow, heavy, brittle |

Tier 4 is what most people mean by "scraping", and it's the tier you should work hardest to
avoid. **How to find tier 2:** open the site, hit F12 → Network → Fetch/XHR, reload, and watch
what JSON the page requests. That's how `footballapi.pulselive.com` was found. The site's own
frontend is telling you where its data lives.

### On being a good citizen

Check `robots.txt`, send a real User-Agent identifying your project, and rate-limit yourself.
This isn't only ethics — it's self-interest. The fastest route to a permanent IP ban is
hammering an endpoint with 656 unthrottled requests.

---

## 2. Why `uv`, specifically

Python's problem: if you `pip install pandas` globally, every project shares one set of versions.
Project A needs pandas 1.x, project B needs 3.x, and you're stuck. The fix is a **virtual
environment** — a per-project package directory.

The historical tools: `venv` + `pip` (built in, slow, no lockfile), `poetry`/`pipenv` (added
lockfiles, still slow), and now `uv` (written in Rust, 10–100× faster, one tool for everything).

But here the choice wasn't about speed. I ran this diagnostic:

```
$ python3 -m pip --version        → No module named pip
$ python3 -c "import ensurepip"   → No module named ensurepip
$ ls /usr/lib/python3.14/EXTERNALLY-MANAGED  → exists
```

Three separate blockers. No pip. No `ensurepip`, so `python -m venv` couldn't bootstrap a pip
either. And that `EXTERNALLY-MANAGED` marker is **PEP 668** — Debian/Ubuntu deliberately locking
the system Python so `pip install` can't break OS tools that depend on it.

The usual fix is `sudo apt install python3-pip python3-venv`. `uv` avoids the problem entirely:
it's a single static binary that **downloads and manages its own Python**, so it never touches
the system one. That's why `uv run` works here on Python 3.13 even though the system Python is
3.14 with no packaging tools at all.

### What the files mean

- **`pyproject.toml`** — what you *declare*. `pandas>=2.2` means "any version at least 2.2".
- **`uv.lock`** — what you actually *got*. `pandas==3.0.5`, plus every transitive dependency,
  pinned exactly.

Commit both. The lockfile is what makes the project rebuild identically in six months.

One detail worth noticing: I set `requires-python = ">=3.12,<3.14"`. Your system Python is 3.14,
which is new enough that compiled packages like `pyarrow` may not have prebuilt wheels yet —
without a wheel, pip tries to compile from C source and fails. Capping below 3.14 made `uv` fetch
3.13 automatically. **Newest is not always best** for anything with compiled dependencies.

`uv run <cmd>` = "make sure the environment matches the lockfile, then run this in it." No
activating, no forgetting to activate.

---

## 3. The three design decisions that actually matter

Most of the code here is plumbing. Three decisions carry real weight.

### (a) Archive the raw response before parsing it

[`http.py`](../src/real_fpl/http.py) gzips every response to `data/raw/` and logs it in a
`raw_fetches` table *before* returning it. Parsers read from disk.

Why: **your parser will be wrong, and the data will have moved on.** When you find the bug next
month, you want to re-parse what you already have — not re-fetch an API that has since updated
prices, corrected xG and changed injury news.

This paid off within the same session: I built the test fixtures in `tests/fixtures/` by reading
the archive, with zero extra network calls.

> **Transferable rule:** separate *acquisition* from *interpretation*. Acquisition is
> irreversible and rate-limited; interpretation is free and repeatable. Never couple them.

### (b) Separate settled facts from point-in-time state

This is the one I'd most want you to take away, because it's invisible until it ruins a model.

Two kinds of data are hiding in the same API response:

**Settled facts** — Haaland played 90 minutes in GW1 and scored 0.42 xG. True forever. Stored in
`player_gw`, upserted (upstream revises bonus points and xG for a day or two after a match, so
re-fetching must *correct* rows, not duplicate or fail on them).

**Point-in-time state** — Haaland costs £15.0m, is 46% owned, and has no injury news *right now*.
The API only ever shows you today's value. Tomorrow's fetch overwrites it and yesterday is
**unrecoverable from any source on earth.**

So `player_snapshots` is append-only, enforced by SQLite triggers that abort on UPDATE and
DELETE. Try it:

```
sqlite> UPDATE player_snapshots SET now_cost = 1;
Error: player_snapshots is append-only: updating a snapshot destroys the
point-in-time record the models depend on
```

**Why this matters for the ML phase — this is the real reason.** Say you train a model to predict
a player's season points, and you include his price as a feature. If that price is the
*end-of-season* price, you've leaked the future: prices rise *because* players score. Your model
learns "expensive ⇒ high scoring", reports 95% accuracy, and is worthless — at transfer time you
only know today's price, not December's.

This is called **target leakage**, and it is the single most common reason a model looks great
offline and fails in production. The snapshot table is what lets you later reconstruct *exactly
what was knowable before gameweek N* and train honestly.

You cannot retrofit this. If you start with CSVs that overwrite, the history simply doesn't
exist. **That's why the storage design came before the model, even though the model is the point
of the project.**

### (c) Find a real join key before you need it

Combining two sources means matching players across them. The naive approach is names — and
names are a swamp: "Son Heung-min" vs "Heung-Min Son", accents, initials, "J.Timber".

Before building anything, I checked whether a shared ID existed. FPL exposes `opta_code`, the PL
API exposes `altIds.opta`, and they're identical — Haaland is `p223094` in both, across all 656
players. Exact integer join, zero fuzzy matching.

> **Transferable rule:** find your join key during exploration, not during integration. If there
> isn't one, that's a major cost you need to know about on day one.

Also note the trap I walked into: I *assumed* teams had an `opta_code` too. They don't — they
have `pulse_id`. My first version would have silently written `NULL` into every row, with no
error, producing an unjoinable table. Same with season labels: FPL says `"2024/25"`, my config
said `"2024-25"`, and joining those gives **zero rows and no error message.**

> Both bugs share a shape: **failures that produce empty results rather than exceptions.** These
> are the expensive ones. Verify with real data early and assert on counts, not just on "it ran".

---

## 4. What to focus on, in order

For any data project like this:

1. **Probe the sources first.** One hour with `curl`. Kill options. Find the join key. Decide
   nothing else until this is done.
2. **Set up the environment properly.** Lockfile committed, one documented command to reproduce.
3. **Design storage around what's irreversible.** Ask "which of these fields can I never recover
   if I miss it today?" Those need append-only history. Everything else can be re-fetched.
4. **Build the smallest end-to-end slice.** Here: `init-db` → `fetch-fpl --snapshot` → `status`.
   One request, real rows in a real table. Get *something* working before building breadth.
5. **Prove your invariants with tests.** Run the ingest twice and assert facts stayed at 656
   while snapshots doubled to 1312. That single test proves both idempotency and append-only.
6. **Only then scale up.** The 656-request full fetch came after the 1-request version worked.
7. **Features and modelling last.** They're the fun part and the part everyone starts with. They
   are also worthless on a foundation that leaked.

The general shape: **irreversible decisions first, reversible decisions later.** Source choice
and storage design are expensive to change once you have data. Model choice is a weekend.

---

## 5. What's deliberately not built yet

- The PL official source module — FPL alone covers every attribute you listed.
- Historical backfill of past seasons.
- Feature engineering and the model.

One flag for when you get there: you described the modelling step as **classification**. For
buy/sell decisions, **regression on points over the next N gameweeks is usually more useful**.
Classifying into fixed tiers up front throws away the margin — and the margin is exactly what a
transfer decision turns on. "Predicted 6.1 vs 5.9 points" tells you they're interchangeable;
"tier A vs tier B" hides that. You can always bucket the predictions afterwards for readability.

---

# Part II — Environments, reproducibility, and what a database actually is

Notes from a debrief session. Everything below was verified on this machine.

---

## 6. Having `pip` is not the same as being able to install things

You installed pip. Then this happened:

```
$ pip install requests
error: externally-managed-environment
× This environment is externally managed

$ python3 -m venv testvenv
The virtual environment was not created successfully because
ensurepip is not available.
```

Two *independent* blockers. Worth separating them, because they teach different things.

### Blocker 1: PEP 668, and why it exists

Your OS uses Python for its own tooling — `apt` itself, networking utilities, and more. Those
tools depend on *specific versions* of Python libraries.

Now imagine `pip install --upgrade requests` at the system level, and the new version drops a
function `apt` relied on. You've just broken your package manager — with the package manager.
This was common enough that Debian/Ubuntu adopted **PEP 668**: a marker file
(`/usr/lib/python3.14/EXTERNALLY-MANAGED`) telling pip "this Python belongs to the OS, refuse to
write here."

So pip isn't broken. It's **protecting you**, and it's right to.

`--break-system-packages` overrides it. The flag is named as a warning, not a suggestion. Don't.

> **The rule:** never install packages into the system Python. Not with `--user`, not with
> `--break-system-packages`. The system Python exists to run the operating system.

### Blocker 2: no `ensurepip`

Debian splits Python across several apt packages. You have `python3-pip` but not
`python3.14-venv`, which is what ships `ensurepip` — the module `venv` uses to bootstrap a pip
*inside* each new environment. Without it, `venv` builds the directory and then can't finish.

Fix if you want the standard toolchain: `sudo apt install python3.14-venv`

### What a virtualenv actually is

It's demystifying to look. A venv is **a directory, not a mode the shell is in**:

```
.venv/
├── pyvenv.cfg      ← text file pointing at a base interpreter
├── bin/            ← python, pip, and your project's entry points
└── lib/python3.13/site-packages/   ← where packages land
```

Our `pyvenv.cfg`:

```
home = /home/arthas/.local/share/uv/python/cpython-3.13-linux-x86_64-gnu/bin
version_info = 3.13
include-system-site-packages = false
```

That last line is the whole trick: packages installed here are invisible to the system Python,
and vice versa. **Isolation by directory layout, nothing more.**

And `source .venv/bin/activate` is not magic either — it prepends `.venv/bin` to your `$PATH`, so
typing `python` finds the venv's copy first. That's essentially all it does. This is why
`uv run <cmd>` works without activating anything: it just invokes the right binary directly,
which is one fewer piece of state to forget about.

Note what `readlink` showed: the venv's Python is **3.13.15**, from uv's own store, while your
system Python is **3.14.4**. uv downloaded an entirely separate interpreter. That's how it
sidesteps both blockers at once — it never touches the OS Python, so PEP 668 is irrelevant and
`ensurepip` is never needed.

### The tool landscape, honestly

| Tool | What it does | Use it when |
|---|---|---|
| `venv` + `pip` | Stdlib. Environment + installs. No lockfile. | Always available; fine for simple work |
| `pip-tools` | Adds real lockfiles to pip | You want lockfiles but must stay on pip |
| `poetry` / `pdm` | Environment + lockfile + packaging | Established projects already using them |
| **`uv`** | All of the above, plus manages Python itself. Rust, very fast. | **Default choice for new projects** |
| `conda`/`mamba` | Manages non-Python deps too (CUDA, MKL, compilers) | Heavy scientific stacks where pip wheels fail |
| `pipx` | Installs *applications*, each in a private venv | CLI tools (`ruff`, `httpie`) — not libraries |

An honest note on the earlier `uv` recommendation: at the time, uv was the only thing that
*worked at all* here. Now that you have pip, `apt install python3.14-venv` + `venv`/`pip` is a
perfectly legitimate alternative. uv remains the better default — speed, a real lockfile, and
Python-version management — but it's now a preference rather than a necessity. **Don't
generalise a workaround into a law.**

### `requirements.txt` vs `pyproject.toml` + lockfile

The distinction that matters is **declaring** versus **recording**:

- **`pyproject.toml`** — what you *want*: `pandas>=2.2`, meaning "anything 2.2 or newer."
- **`uv.lock`** — what you *got*: `pandas==3.0.5`, every transitive dependency, plus 172 file
  hashes. Exact and tamper-evident.

`pip freeze > requirements.txt` looks like a lockfile but isn't a good one. It flattens direct
and transitive dependencies into one undifferentiated list, so a year later you can't tell what
you actually asked for from what got dragged in — and it records no hashes.

**Commit both files.** The lockfile is what makes the project rebuild identically later. (This
is true for *applications*. Libraries don't commit lockfiles, because they must stay flexible for
whoever depends on them.)

---

## 7. Reproducibility on a server

"Reproducible" isn't binary — it's a ladder. Each rung costs more and buys more:

| Level | What's pinned | Gets you |
|---|---|---|
| 0 | Nothing | "works on my machine" |
| 1 | `requirements.txt` with `>=` | roughly similar |
| 2 | Lockfile, exact versions + hashes | identical Python packages |
| 3 | + the Python version itself | identical interpreter |
| 4 | + OS and system libraries (container) | identical environment |
| 5 | + base image digest, not a tag | bit-for-bit identical |

**This project is at level 3 already**, via `uv.lock` plus `requires-python`. For a data pipeline
you run yourself, level 3 is genuinely enough. Reach for Docker (level 4+) when you need to
guarantee system libraries too, or when the server's OS differs meaningfully from yours.

### The dependency people forget

Python packages are not your only dependencies. `pandas` and `pyarrow` ship **compiled C
extensions** linked against system libraries like `glibc`. That's why wheels have names like
`manylinux_2_17_x86_64` — they encode a minimum glibc and a CPU architecture.

Practical consequences:

- A wheel built for x86_64 **will not run on ARM** (Graviton, Apple Silicon). Different wheel.
- A very old server may have too old a glibc, so pip falls back to compiling from source — which
  needs a C toolchain and often fails.
- This is exactly why `requires-python` was capped below 3.14: on a brand-new Python, wheels may
  not exist yet, and "no wheel" means "compile from C and probably fail."

> **Transferable rule:** when you pin versions, you're pinning the Python layer. The layer below
> — interpreter, libc, architecture — is pinned by the lockfile only for the interpreter, and by
> a container for the rest.

### Deploying this project

Nothing here is Docker-dependent. Minimum viable server setup:

```bash
git clone <repo> && cd real_fpl
curl -LsSf https://astral.sh/uv/install.sh | sh
uv sync --frozen          # --frozen = obey the lockfile exactly; fail if stale
uv run real-fpl init-db
uv run real-fpl fetch-fpl --all
```

`--frozen` is the important flag: **use it in every automated context.** Without it, uv may
quietly re-resolve and update the lockfile, which is precisely the non-reproducibility you were
trying to avoid. The rule of thumb: `uv sync` while developing, `uv sync --frozen` in CI and on
servers.

For the daily snapshot, prefer a **systemd timer** over cron: it logs to journald, survives
reboots cleanly, and won't silently overlap runs.

```ini
# /etc/systemd/system/fpl-snapshot.service
[Service]
Type=oneshot
WorkingDirectory=/opt/real_fpl
ExecStart=/home/arthas/.local/bin/uv run --frozen real-fpl fetch-fpl --snapshot
```

Use absolute paths — systemd units get a minimal `$PATH` and will not find `uv` otherwise. This
is the single most common reason a job that works in your shell fails as a timer.

---

## 8. Databases: the distinction nobody explains

You asked "do I install it, or how does it work?" That question has two completely different
answers depending on the database, and the difference is the thing worth learning.

### SQLite is a library. PostgreSQL is a server.

**SQLite** is not a program that runs. It's a C library that gets **compiled into your
application**. Your Python process opens a file and reads and writes it directly. There is no
database process, no port, no username, no password, no daemon to start.

Proof, on your machine, with nothing installed:

```
$ python3 -c "import sqlite3; print(sqlite3.sqlite_version)"
3.46.1
```

It was already there. It ships inside Python's standard library. **The "database" is the file
`data/fpl.db`** — 1.4 MB. You can copy it, email it, or drop it on a USB stick, and it opens
anywhere.

**PostgreSQL** is the opposite model: a long-running server process. You install it, start it,
it listens on port 5432, you create users and grant permissions, and your program connects over a
network socket. The data lives in a directory the server owns and that you must never touch
directly.

```
SQLite                              PostgreSQL
┌─────────────────────┐             ┌──────────────┐      ┌────────────────┐
│ your python process │             │ your process │─TCP─▶│ postgres server│
│  ┌───────────────┐  │             └──────────────┘ 5432 │  owns its files│
│  │ sqlite libr.  │──┼──▶ fpl.db                         └────────────────┘
│  └───────────────┘  │   (a file)
└─────────────────────┘
```

So: **you do not install SQLite. You already have it.** That's not a simplification — there is
genuinely nothing to install, configure, start, secure, or back up as a service.

### When SQLite stops being the right answer

SQLite's real limit is **concurrent writers**. In WAL mode (which `db.py` enables) you get many
simultaneous readers plus *one* writer. That's why the ingestion could run while exports were
read from the same file.

Switch to PostgreSQL when you need:

- **multiple processes writing at once** — several ingesters, or a web app with concurrent users;
- **network access** — the database on a different machine from the code;
- **many concurrent connections**, e.g. a public API;
- genuinely large data — SQLite handles hundreds of GB, but Postgres has better tooling there.

For this project — one person, one machine, one writer, a database that will reach maybe a few
hundred MB after years — **SQLite is the correct answer, probably permanently.** Reaching for
Postgres here would add a service to run, secure, and back up, in exchange for nothing.

That is also the strongest argument for SQLite on a server: there is no service to operate.

### Backups, and a trap

Because the database is a file, backup *is* copying the file. But **`cp` is not safe while
something is writing**: WAL mode keeps recent transactions in a separate `-wal` file, so a naive
copy can catch a torn state.

Use SQLite's own backup command, which takes a consistent snapshot even during writes:

```bash
sqlite3 data/fpl.db ".backup /backups/fpl-$(date +%F).db"
```

Verified while the database was live: the backup came back with all 656 players intact.

### If you ever do migrate

Keep the SQL portable and the switch is small. Things that would tie you to SQLite:

- `INSERT ... ON CONFLICT DO UPDATE` — works in Postgres 9.5+, fine;
- our append-only `TRIGGER`s — same idea in Postgres, different syntax;
- SQLite's loose typing — Postgres is strict and will reject things SQLite tolerates.

Using SQLAlchemy Core would abstract most of this. It wasn't used here because it's a real
abstraction to learn on top of SQL you're trying to understand — and plain `sqlite3` keeps the
actual SQL visible, which matters more while learning. That's a deliberate trade, and worth
revisiting only if a migration becomes real.
