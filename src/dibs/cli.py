"""dibs command-line interface."""

from __future__ import annotations

import asyncio
import csv
import time
from pathlib import Path

import typer
from dotenv import load_dotenv
from rich.console import Console

from . import __version__, data, history
from .checkers import ALL_CHECKERS
from .config import Config
from .models import Candidate, candidate_from_fields, split_multi
from .report import (
    history_row,
    render_table,
    summary_line,
    write_markdown,
    write_static_from_history,
)
from .run import run as run_screening
from .scoring import rank, score_candidate

app = typer.Typer(add_completion=False, help="Brand-name knockout screener. Not legal advice.")
console = Console()


def _split(cell: str | None) -> tuple[str, ...] | None:
    return split_multi(cell)


def _candidate_from_row(row: dict) -> Candidate | None:
    """Build a Candidate from a CSV row. Columns: name (required), legal, tm,
    domains, handles. Blank columns derive from name. Unknown columns ignored."""
    low = {(k or "").strip().lower(): (v or "").strip() for k, v in row.items()}
    return candidate_from_fields(
        low.get("name"), low.get("legal"), low.get("tm"),
        low.get("domains"), low.get("handles"),
    )


def _load_candidates(names: list[str], file: Path | None) -> list[Candidate]:
    out: list[Candidate] = []
    for name in names:
        out.append(Candidate(display=name))
    if file:
        if file.suffix.lower() == ".csv":
            with file.open(encoding="utf-8", newline="") as fh:
                for row in csv.DictReader(fh):
                    cand = _candidate_from_row(row)
                    if cand:
                        out.append(cand)
        else:
            for line in file.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if line and not line.startswith("#"):
                    out.append(Candidate(display=line))
    seen: set[str] = set()
    deduped: list[Candidate] = []
    for cand in out:
        key = cand.display.lower()
        if key not in seen:
            seen.add(key)
            deduped.append(cand)
    return deduped


@app.command()
def check(
    names: list[str] = typer.Argument(None, help="Candidate names."),
    file: Path | None = typer.Option(
        None, "--file", "-f",
        help="Input file: .txt (one name per line) or .csv (name,legal,tm,domains,handles).",
    ),
    config_path: Path | None = typer.Option(None, "--config", help="Path to dibs.toml."),
    quick: bool = typer.Option(False, "--quick",
                               help="Skip slow sources once a hard blocker is found."),
    no_cache: bool = typer.Option(False, "--no-cache", help="Bypass the on-disk cache."),
    only: str | None = typer.Option(None, "--only", help="Comma-separated checker names to run."),
    skip: str | None = typer.Option(None, "--skip", help="Comma-separated checker names to skip."),
    strict: bool = typer.Option(False, "--strict", help="Exit 2 if any result is UNKNOWN."),
) -> None:
    """Screen NAMES (and/or a --file) across every enabled source."""
    load_dotenv()
    candidates = _load_candidates(names or [], file)
    if not candidates:
        console.print("[red]No names given.[/red] Pass names or --file.")
        raise typer.Exit(1)
    config = Config.load(config_path)
    only_set = {s.strip() for s in only.split(",")} if only else None
    skip_set = {s.strip() for s in skip.split(",")} if skip else None

    if config.checker_enabled("corpcan") and not data.corpcan_ready() and (
        not skip_set or "corpcan" not in skip_set
    ):
        console.print("[dim]Corporations Canada index missing; run `dibs data refresh`.[/dim]")

    outcome = asyncio.run(run_screening(
        candidates, config, use_cache=not no_cache,
        only=only_set, skip=skip_set, quick=quick,
    ))

    rows = [score_candidate(c, outcome.results[c.display], config) for c in candidates]
    ranked = rank(rows)

    if outcome.disabled:
        console.print("[yellow]Disabled (missing prerequisite):[/yellow]")
        for name, reason in outcome.disabled.items():
            console.print(f"  [dim]{name}[/dim]: {reason}")

    render_table(ranked, outcome.active_columns, console)

    run_ts = time.time()
    controls = {"only": sorted(only_set) if only_set else None,
                "skip": sorted(skip_set) if skip_set else None, "quick": quick}
    for row in ranked:
        write_markdown(row, ts=run_ts)
        history.append(history_row(row, controls=controls), ts=run_ts)
    write_static_from_history()  # full latest-per-name CSV + HTML snapshot

    console.print()
    console.print(summary_line(ranked))
    console.print("[dim]Reports: reports/<name>/<timestamp>.md · reports/summary.csv · "
                  "reports/index.html[/dim]")
    console.print("[dim]History saved. Browse with `dibs serve`.[/dim]")

    if strict and any(r.unknown for r in rows):
        raise typer.Exit(2)


data_app = typer.Typer(help="Manage local bulk-data indexes.")
app.add_typer(data_app, name="data")


@data_app.command("refresh")
def data_refresh(
    include_dissolved: bool = typer.Option(False, "--include-dissolved"),
    config_path: Path | None = typer.Option(None, "--config"),
) -> None:
    """Download and index the Corporations Canada open-data CSVs."""
    load_dotenv()
    config = Config.load(config_path)
    ua = config.user_agent(__version__)
    console.print("[dim]Downloading Corporations Canada open data (~110 MB+)...[/dim]")
    count = data.corpcan_refresh(include_dissolved=include_dissolved, ua=ua)
    console.print(f"[green]Indexed {count:,} corporation name rows.[/green]")


req_app = typer.Typer(help="Manage the Québec REQ index.")
app.add_typer(req_app, name="req")


@req_app.command("import")
def req_import(zip_path: Path = typer.Argument(..., help="REQ open-data ZIP.")) -> None:
    """Index enterprise names from the REQ open-data ZIP (download it manually first)."""
    if not zip_path.exists():
        console.print(f"[red]File not found:[/red] {zip_path}")
        raise typer.Exit(1)
    console.print(
        "[yellow]REQ open data is licensed CC BY-NC-SA 4.0 (non-commercial).[/yellow] "
        "Screening your own candidate names is personal use; the licence call is yours."
    )
    count = data.req_import(zip_path)
    console.print(f"[green]Indexed {count:,} enterprise name rows.[/green]")


@app.command()
def sources(config_path: Path | None = typer.Option(None, "--config")) -> None:
    """List checkers, their columns, and any missing prerequisites."""
    load_dotenv()
    config = Config.load(config_path)
    for cls in ALL_CHECKERS:
        inst = cls()
        enabled = config.checker_enabled(inst.name)
        missing = inst.requires(config) if enabled else None
        state = "[green]on[/green]" if enabled and not missing else (
            "[yellow]blocked[/yellow]" if enabled else "[dim]off[/dim]")
        cols = ", ".join(inst.columns)
        line = f"{inst.name:12} {state:20} cols: {cols}"
        if missing:
            line += f"  [dim]— {missing}[/dim]"
        console.print(line)


@app.command()
def serve(
    port: int = typer.Option(8787, "--port", "-p", help="Port to bind (localhost only)."),
    host: str = typer.Option("127.0.0.1", "--host", help="Bind address; keep localhost."),
    config_path: Path | None = typer.Option(None, "--config"),
) -> None:
    """Serve the run-history dashboard on localhost. Browse history and launch checks."""
    load_dotenv()
    from .server import serve as run_server

    config = Config.load(config_path)
    run_server(config, host=host, port=port)


@app.command()
def backup() -> None:
    """Back up the run history and reports to .cache/backups/<timestamp>/."""
    from . import maintenance

    made = maintenance.backup()
    if made:
        console.print(f"[green]Backed up to[/green] {made}")
    else:
        console.print("[dim]Nothing to back up.[/dim]")


@app.command()
def reset(
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip the confirmation prompt."),
    no_backup: bool = typer.Option(False, "--no-backup", help="Do not back up before clearing."),
) -> None:
    """Clear run history and reports for a fresh start (a backup is saved first)."""
    from . import maintenance
    from .maintenance import BACKUP_DIR

    if not yes:
        where = "no backup" if no_backup else f"a backup is saved to {BACKUP_DIR}/ first"
        typer.confirm(
            f"This clears all run history and reports ({where}). Continue?", abort=True)
    made = maintenance.reset(with_backup=not no_backup)
    console.print("[green]Reset done.[/green]")
    if made:
        console.print(f"[dim]Backup: {made}[/dim]")


@app.command()
def version() -> None:
    """Print the dibs version."""
    console.print(__version__)


if __name__ == "__main__":
    app()
