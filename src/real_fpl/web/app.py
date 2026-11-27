"""The web UI: a small FastAPI app that renders HTML pages from the database.

How one page view works, end to end:

  1. The browser asks for a URL, e.g.  GET /?pos=MID&sort=price
  2. FastAPI finds the *route* (a function below decorated with @app.get) whose
     path matches, and turns the query string into typed function arguments.
  3. The route calls a function in queries.py, which runs SQL and returns dicts.
  4. The route hands those dicts to a Jinja *template* (templates/*.html), which
     fills them into HTML.
  5. That HTML goes back to the browser as the response. The browser then fetches
     the CSS/JS the page links to from /static.

No JavaScript app, no JSON API: the server does all the work and sends finished
HTML. htmx (templates/base.html) adds the one bit of interactivity, re-fetching
only the table when a filter changes.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Iterator
from urllib.parse import urlencode

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from ..config import DB_PATH, load_config
from . import queries

HERE = Path(__file__).parent
templates = Jinja2Templates(directory=HERE / "templates")

# FPL's status codes, as served in the bootstrap snapshot.
STATUS = {
    "a": ("Available", "ok"),
    "d": ("Doubtful", "warn"),
    "i": ("Injured", "bad"),
    "s": ("Suspended", "bad"),
    "u": ("Unavailable", "bad"),
    "n": ("Not in squad", "bad"),
}


def _status(code: str | None) -> tuple[str, str]:
    return STATUS.get(code or "", ("", ""))


templates.env.globals["status"] = _status


def _optional_int(value: str | None) -> int | None:
    # HTML forms send an empty string for "no choice", which FastAPI would
    # reject as an int, so optional numeric filters arrive as text and are
    # converted here.
    try:
        return int(value) if value else None
    except ValueError:
        return None


def _optional_float(value: str | None) -> float | None:
    try:
        return float(value) if value else None
    except ValueError:
        return None


def create_app(db_path: Path | None = None, season: str | None = None) -> FastAPI:
    """Build the app. Tests pass their own small database and season."""
    db_path = Path(db_path or DB_PATH)
    season = season or load_config()["current_season"]

    app = FastAPI(title="real_fpl")
    app.mount("/static", StaticFiles(directory=HERE / "static"), name="static")

    @app.exception_handler(sqlite3.OperationalError)
    def data_refreshing(request: Request, exc: sqlite3.OperationalError):
        """Show "data refreshing" instead of a crash while the DB is being rebuilt.

        The model worktree drops and rebuilds derived tables (target_rows,
        predictions) for a few seconds at a time, and pull-db.sh replaces the
        whole file. A page that lands in that window sees "no such table" or
        "database is locked". That is a normal, temporary state, so it gets a
        503 ("try again shortly") page. Any other database error is a real bug
        and is re-raised.
        """
        msg = str(exc)
        if "no such table" in msg or "locked" in msg or "unable to open" in msg:
            return templates.TemplateResponse(
                request, "refreshing.html", {"season": season}, status_code=503,
                headers={"Retry-After": "5"},
            )
        raise exc

    def get_conn() -> Iterator[sqlite3.Connection]:
        """One read-only connection per request, closed when the request ends.

        mode=ro is the safety catch: the UI only reads, so it should be
        *unable* to write. It can't then trip the player_snapshots triggers or
        change the laptop's copy of the database by accident. It also refuses
        to open a missing file instead of quietly creating an empty database.

        check_same_thread=False because FastAPI may run this dependency and the
        route on different worker threads; each connection is still used by
        only one request at a time.
        """
        uri = db_path.resolve().as_uri() + "?mode=ro"
        # timeout: wait up to 5s for a writer to finish before giving up with
        # "database is locked", which is usually long enough to ride it out.
        conn = sqlite3.connect(uri, uri=True, check_same_thread=False, timeout=5)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
        finally:
            conn.close()

    @app.get("/", response_class=HTMLResponse)
    def players_page(
        request: Request,
        pos: str | None = None,
        team: str | None = None,
        q: str | None = None,
        max_price: str | None = None,
        sort: str = queries.DEFAULT_SORT,
        dir: str = "desc",
        conn: sqlite3.Connection = Depends(get_conn),
    ):
        filters = {
            "pos": pos if pos in queries.POSITIONS else "",
            "team": _optional_int(team),
            "q": (q or "").strip(),
            "max_price": _optional_float(max_price),
        }
        pred = queries.live_predictions(conn, season)
        if sort not in queries.SORTS or (sort == "pred" and not pred):
            sort = queries.DEFAULT_SORT
        desc = dir != "asc"

        players = queries.latest_players(
            conn, season,
            pos=filters["pos"] or None, team=filters["team"], q=filters["q"] or None,
            max_price=filters["max_price"], sort=sort, desc=desc, pred=pred,
        )

        def sort_url(col: str) -> str:
            """Link for a column header: same filters, sorted by `col`.

            Clicking the column already sorted on flips the direction. Names
            start A-Z; numbers start high-to-low, which is what you want first.
            """
            if col == sort:
                new_dir = "asc" if desc else "desc"
            else:
                new_dir = "asc" if col in ("name", "team", "pos") else "desc"
            params = {k: v for k, v in filters.items() if v not in (None, "")}
            params.update(sort=col, dir=new_dir)
            return "/?" + urlencode(params)

        context = {
            "players": players, "filters": filters, "sort": sort, "desc": desc,
            "sort_url": sort_url, "positions": queries.POSITIONS,
            "teams": queries.teams(conn, season), "season": season,
            "snapshot_ts": queries.latest_snapshot_ts(conn, season),
            "pred": pred,
        }
        # Same URL, two answers. A normal visit gets the whole page. When htmx
        # makes the request (a filter changed), it sends the HX-Request header
        # and only wants the table, which it swaps into the page in place.
        if request.headers.get("HX-Request"):
            return templates.TemplateResponse(request, "_players_table.html", context)
        return templates.TemplateResponse(request, "players.html", context)

    @app.get("/player/{opta_code}", response_class=HTMLResponse)
    def player_page(
        request: Request,
        opta_code: str,
        # `season` is already taken by the current season above, so the URL's
        # ?season= is read into a differently named argument.
        season_param: str | None = Query(None, alias="season"),
        conn: sqlite3.Connection = Depends(get_conn),
    ):
        header = queries.player_header(conn, opta_code, season)
        if header is None:
            raise HTTPException(status_code=404, detail="No such player")

        seasons = queries.player_seasons(conn, opta_code)
        shown = season_param if season_param in seasons else (seasons[0] if seasons else season)
        matches = queries.player_gameweeks(conn, opta_code, shown)
        pred = queries.live_predictions(conn, season)
        return templates.TemplateResponse(
            request, "player.html",
            {
                "p": header, "seasons": seasons, "shown": shown, "season": season,
                "context": queries.player_season_context(conn, opta_code, shown),
                "matches": matches,
                "chart": queries.per_gameweek_series(matches),
                "pred": pred,
                "predictions": (
                    queries.player_predictions(conn, opta_code, season, pred) if pred else []
                ),
            },
        )

    @app.get("/fixtures", response_class=HTMLResponse)
    def fixtures_page(
        request: Request,
        start: str | None = None,
        conn: sqlite3.Connection = Depends(get_conn),
    ):
        n_gw = 5
        next_gw = queries.next_gameweek(conn, season) or 1
        from_gw = _optional_int(start) or next_gw
        from_gw = max(1, min(from_gw, 38 - n_gw + 1))
        grid = queries.fixture_grid(conn, season, from_gw, n_gw)
        return templates.TemplateResponse(
            request, "fixtures.html",
            {"grid": grid, "season": season, "from_gw": from_gw, "n_gw": n_gw,
             "next_gw": next_gw},
        )

    return app


# What `uvicorn real_fpl.web.app:app` imports. Building it opens no database:
# connections are made per request, so importing this module is cheap and safe.
app = create_app()
