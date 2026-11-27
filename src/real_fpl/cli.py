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


@app.command("build-features")
def build_features() -> None:
    """Rebuild the derived Phase 2 tables from the facts."""
    from .features.model_rows import build_all

    conn = db.connect()
    db.init_db(conn)
    for table, n in build_all(conn).items():
        console.print(f"[green]{table}[/green] {n:,} rows")


@app.command("train")
def train_cmd(
    no_write: bool = typer.Option(False, "--no-write", help="Evaluate only; don't write predictions."),
) -> None:
    """Compare baselines and trees, then write live predictions."""
    import pandas as pd

    from .model.train import run

    conn = db.connect()
    db.init_db(conn)
    r = run(conn, write=not no_write)
    pd.set_option("display.width", 160)
    cols = ["group", "model", "n", "rmse", "poisson_dev", "corr", "bias", "mae"]
    console.print("[bold]Validation (2025-26), models trained on 2024-25[/bold]")
    # Plain print: rich would read "trees[poisson, 200]" as markup and drop it.
    print(r.validation[cols].round(3).to_string(index=False))
    print(f"\nchosen: {r.chosen}")
    console.print("\n[bold]Test (2026-27 so far), trained on 2024-25 + 2025-26[/bold]")
    print(r.test[cols].round(3).to_string(index=False))
    for version, n in r.written.items():
        console.print(f"[green]predictions[/green] {version}: {n:,} rows")


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
