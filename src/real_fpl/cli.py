"""Command line interface."""

from __future__ import annotations

import logging

import typer
from rich.console import Console
from rich.table import Table

from . import db
from .config import DB_PATH
from .sources import fpl as fpl_source

app = typer.Typer(add_completion=False, help="Premier League / FPL data ingestion.")
console = Console()

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")


@app.command("init-db")
def init_db() -> None:
    """Create the SQLite schema."""
    conn = db.connect()
    db.init_db(conn)
    console.print(f"[green]schema ready[/green] at {DB_PATH}")


@app.command("fetch-fpl")
def fetch_fpl(
    snapshot: bool = typer.Option(False, "--snapshot", help="Bootstrap only (one request)."),
    all_: bool = typer.Option(False, "--all", help="Bootstrap + fixtures + every player."),
) -> None:
    """Fetch from the FPL API."""
    if not (snapshot or all_):
        raise typer.BadParameter("pass --snapshot or --all")

    conn = db.connect()
    db.init_db(conn)

    bootstrap = fpl_source.fetch_bootstrap(conn)
    result = fpl_source.ingest_snapshot(conn, bootstrap)
    console.print(
        f"[green]snapshot[/green] gw={result['gameweek']} at {result['snapshot_ts']}: "
        f"{result['players']} players, {result['snapshots']} snapshot rows"
    )

    # The tracked team, when this machine has REAL_FPL_ENTRY_ID set (the server
    # does; laptops don't). Its own failure must not cost the snapshot, which is
    # already stored and is the part that can never be re-fetched.
    from .config import entry_id

    eid = entry_id()
    if eid is not None:
        try:
            r = fpl_source.ingest_entry(conn, eid)
            console.print(f"[green]entry[/green] {r['gameweeks']} gameweeks, picks for "
                          f"{r['picks_fetched_for'] or 'nothing new'}")
        except Exception as exc:  # noqa: BLE001
            console.print(f"[yellow]entry fetch failed:[/yellow] {exc}")

    if all_:
        n = fpl_source.ingest_fixtures(conn)
        console.print(f"[green]fixtures[/green] {n} rows")

        with console.status("fetching player histories...") as status:
            def progress(i: int, total: int, name: str) -> None:
                status.update(f"player histories {i}/{total} — {name}")

            hist = fpl_source.ingest_player_histories(conn, bootstrap, progress)

        console.print(
            f"[green]histories[/green] {hist['players_fetched']} players, "
            f"{hist['player_gw_rows']} match rows, "
            f"{hist['player_season_rows']} past-season rows"
        )
        if hist["failures"]:
            console.print(f"[yellow]{len(hist['failures'])} players failed[/yellow]")


@app.command("fetch-historical")
def fetch_historical(
    season: list[str] = typer.Option(
        None, "--season", help="Season like 2024-25; repeatable. Default: all in config."
    ),
) -> None:
    """Backfill closed seasons from the historical mirror (one-off per season)."""
    from .config import load_config
    from .sources import historical

    conn = db.connect()
    db.init_db(conn)
    seasons = season or load_config()["historical"]["seasons"]
    for s in seasons:
        with console.status(f"backfilling {s}..."):
            r = historical.ingest_season(conn, s)
        console.print(
            f"[green]{s}[/green] player_gw {r['player_gw']:,}, fixtures {r['fixtures']}, "
            f"teams {r['teams']}, season_players {r['season_players']}, "
            f"new players {r['new_players']}"
            + (f", [yellow]{r['unmapped_rows']} unmapped rows[/yellow]" if r["unmapped_rows"] else "")
        )


@app.command("export")
def export_cmd(
    table: str = typer.Option("player_gw_enriched", "--table"),
    fmt: str = typer.Option("csv", "--format", help="csv or parquet"),
) -> None:
    """Write a table out as CSV or Parquet."""
    from .export import export

    conn = db.connect()
    path = export(conn, table, fmt)
    console.print(f"[green]wrote[/green] {path}")


@app.command("serve")
def serve(
    port: int = typer.Option(8000, "--port"),
    reload: bool = typer.Option(False, "--reload", help="Restart on code changes (development)."),
) -> None:
    """Run the web UI at http://localhost:PORT (read-only view of the database)."""
    import uvicorn

    if not DB_PATH.exists():
        # Fail loudly: in a second checkout (the `ui` worktree) data/ doesn't
        # exist, and pointing at nothing would just show empty pages.
        console.print(
            f"[red]no database at {DB_PATH}[/red] -- set REAL_FPL_DB to the real one"
        )
        raise typer.Exit(1)
    console.print(f"serving {DB_PATH} at [bold]http://localhost:{port}[/bold]")
    # 127.0.0.1, not 0.0.0.0: reachable from this machine only. The site has no
    # login, so it shouldn't be on the network until that's a deliberate choice.
    uvicorn.run("real_fpl.web.app:app", host="127.0.0.1", port=port, reload=reload)


@app.command("status")
def status() -> None:
    """Row counts and last fetch per source."""
    conn = db.connect()
    db.init_db(conn)

    t = Table("table", "rows")
    for name, count in db.table_counts(conn).items():
        t.add_row(name, f"{count:,}")
    console.print(t)

    rows = conn.execute(
        "SELECT source, COUNT(*) n, MAX(fetched_at) last FROM raw_fetches GROUP BY source"
    ).fetchall()
    if rows:
        t2 = Table("source", "fetches", "last fetch")
        for r in rows:
            t2.add_row(r["source"], f"{r['n']:,}", r["last"])
        console.print(t2)

    snaps = conn.execute(
        "SELECT COUNT(DISTINCT snapshot_ts) n, MIN(snapshot_ts) a, MAX(snapshot_ts) b "
        "FROM player_snapshots"
    ).fetchone()
    if snaps and snaps["n"]:
        console.print(f"snapshots: {snaps['n']} distinct timestamps, {snaps['a']} → {snaps['b']}")


if __name__ == "__main__":
    app()
