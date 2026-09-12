"""Outputs: coloured rich terminal table, per-name Markdown reports, a combined
CSV, and a self-contained HTML report. Colour legend: green = clear, yellow =
partial/contains/adjacent, red = hard blocker, dim grey = UNKNOWN.
"""

from __future__ import annotations

import csv
from pathlib import Path

from jinja2 import Environment, PackageLoader, select_autoescape
from rich.console import Console
from rich.table import Table

from .models import CheckResult, Status, Tier
from .scoring import ScoreRow

REPORTS = Path("reports")


def _cell(result: CheckResult | None) -> tuple[str, str]:
    """Return (text, rich-style) for one table cell."""
    if result is None:
        return "-", "dim"
    if result.column == "Web":  # footprint: show counts, never a verdict colour
        counts = [str(h.extra.get("count")) for h in result.hits if "count" in h.extra]
        return ("/".join(counts) if counts else "?", "cyan")
    status = result.worst
    if status == Status.UNKNOWN:
        return "UNK", "dim"
    if status == Status.AVAILABLE:
        return "clear", "green"
    # TAKEN: red if any hard-tier taken hit, else yellow.
    hard = any(h.status == Status.TAKEN and h.tier == Tier.HARD for h in result.hits)
    taken = sum(1 for h in result.hits if h.status == Status.TAKEN)
    return (f"TAKEN ({taken})" if taken > 1 else "TAKEN", "bold red" if hard else "yellow")


def _by_column(results: list[CheckResult]) -> dict[str, CheckResult]:
    return {r.column: r for r in results}


def render_table(rows: list[ScoreRow], columns: list[str], console: Console | None = None) -> None:
    console = console or Console()
    table = Table(title="dibs — name screening (knockout filter, not legal advice)",
                  header_style="bold")
    table.add_column("Name", style="bold")
    for col in columns:
        table.add_column(col, justify="center")
    table.add_column("Score", justify="right")
    table.add_column("Unk", justify="right", style="dim")

    for row in rows:
        by_col = _by_column(row.results)
        cells = [row.candidate.display]
        for col in columns:
            text, style = _cell(by_col.get(col))
            cells.append(f"[{style}]{text}[/{style}]")
        cells.append(str(row.score))
        cells.append(str(row.unknown))
        table.add_row(*cells)
    console.print(table)


def _fmt_hit(h) -> str:
    parts = [h.status.value]
    if h.tier != Tier.INFO:
        parts.append(h.tier.value)
    line = f"{h.label} — {' / '.join(parts)}"
    if h.detail:
        line += f" — {h.detail}"
    if h.url:
        line += f"  <{h.url}>"
    return line


def write_markdown(row: ScoreRow) -> Path:
    REPORTS.mkdir(parents=True, exist_ok=True)
    path = REPORTS / f"{row.candidate.slug or 'unnamed'}.md"
    lines = [
        f"# {row.candidate.display}",
        "",
        f"**Score:** {row.score}  |  hard {row.hard}, medium {row.medium}, "
        f"soft {row.soft}, unknown {row.unknown}",
        "",
        "> Screening tool, not legal advice. Not a NUANS or trademark clearance search.",
        "",
    ]
    live_hits = []
    dead_hits = []
    unknown = []
    manual = []
    for result in row.results:
        for h in result.hits:
            src = f"[{result.source}] "
            if h.status == Status.UNKNOWN and "manual" in h.label.lower():
                manual.append(src + _fmt_hit(h))
            elif h.status == Status.UNKNOWN:
                unknown.append(src + _fmt_hit(h))
            elif h.extra.get("alive") is False:
                dead_hits.append(src + _fmt_hit(h))
            elif h.status == Status.TAKEN:
                live_hits.append(src + _fmt_hit(h))
        for err in result.errors:
            unknown.append(f"[{result.source}] ERROR — {err}")

    def section(title, items):
        lines.append(f"## {title}")
        lines.append("")
        if items:
            lines.extend(f"- {i}" for i in items)
        else:
            lines.append("_none_")
        lines.append("")

    section("Live blockers (taken)", live_hits)
    section("Dead / historical marks (context only)", dead_hits)
    section("Unknown (source failed or not checkable — verify by hand)", unknown)
    section("Manual checks", manual)
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def write_csv(rows: list[ScoreRow], columns: list[str]) -> Path:
    REPORTS.mkdir(parents=True, exist_ok=True)
    path = REPORTS / "summary.csv"
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(["name", "score", "hard", "medium", "soft", "unknown", *columns])
        for row in rows:
            by_col = _by_column(row.results)
            cells = []
            for col in columns:
                result = by_col.get(col)
                cells.append(result.worst.value if result else "")
            writer.writerow([
                row.candidate.display, row.score, row.hard, row.medium,
                row.soft, row.unknown, *cells,
            ])
    return path


def write_html(rows: list[ScoreRow], columns: list[str]) -> Path:
    REPORTS.mkdir(parents=True, exist_ok=True)
    env = Environment(
        loader=PackageLoader("dibs", "templates"),
        autoescape=select_autoescape(["html"]),
    )
    env.filters["cell"] = _cell
    table_rows = []
    for row in rows:
        by_col = _by_column(row.results)
        cells = []
        for col in columns:
            text, style = _cell(by_col.get(col))
            cls = {
                "green": "clear", "bold red": "hard", "yellow": "partial",
                "dim": "unknown", "cyan": "info",
            }.get(style, "")
            cells.append({"text": text, "cls": cls})
        details = []
        for result in row.results:
            for h in result.hits:
                if h.status == Status.AVAILABLE and h.tier == Tier.INFO and not h.detail:
                    continue
                details.append({
                    "source": result.source,
                    "label": h.label,
                    "status": h.status.value,
                    "tier": h.tier.value,
                    "detail": h.detail,
                    "url": h.url,
                })
            for err in result.errors:
                details.append({"source": result.source, "label": "ERROR",
                                "status": "UNKNOWN", "tier": "", "detail": err, "url": None})
        table_rows.append({
            "name": row.candidate.display, "score": row.score, "hard": row.hard,
            "medium": row.medium, "soft": row.soft, "unknown": row.unknown,
            "cells": cells, "details": details, "slug": row.candidate.slug,
        })
    template = env.get_template("report.html.j2")
    path = REPORTS / "index.html"
    path.write_text(template.render(columns=columns, rows=table_rows), encoding="utf-8")
    return path


def history_row(row: ScoreRow) -> dict:
    """Flatten a scored candidate into the structure the history store persists."""
    by_col = _by_column(row.results)
    columns = {}
    for result in row.results:
        hard = any(h.status == Status.TAKEN and h.tier == Tier.HARD for h in result.hits)
        taken = sum(1 for h in result.hits if h.status == Status.TAKEN)
        columns[result.column] = {
            "status": result.worst.value, "hard": hard, "count": taken,
        }
    for col in by_col:  # stable presence even if empty
        columns.setdefault(col, {"status": "AVAILABLE", "hard": False, "count": 0})
    detail = []
    for result in row.results:
        for h in result.hits:
            if h.status == Status.AVAILABLE and h.tier == Tier.INFO and not h.detail:
                continue
            detail.append({
                "source": result.source, "column": result.column, "label": h.label,
                "status": h.status.value, "tier": h.tier.value,
                "detail": h.detail, "url": h.url,
            })
        for err in result.errors:
            detail.append({"source": result.source, "column": result.column,
                           "label": "ERROR", "status": "UNKNOWN", "tier": "",
                           "detail": err, "url": None})
    return {
        "name": row.candidate.display, "slug": row.candidate.slug or "unnamed",
        "score": row.score, "hard": row.hard, "medium": row.medium,
        "soft": row.soft, "unknown": row.unknown,
        "columns": columns, "detail": detail,
    }


def summary_line(rows: list[ScoreRow]) -> str:
    if not rows:
        return "No candidates."
    parts = [f"{r.candidate.display}({r.score}" + (f",{r.unknown}?" if r.unknown else "") + ")"
             for r in rows]
    return "Ranked, fewest blockers first: " + "  ".join(parts)
