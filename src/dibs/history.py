"""Append-only run history in .cache/history.sqlite. Each `check` writes one row
per candidate; the dashboard reads latest-per-name and per-name timelines. Never
overwrites — every run is kept."""

from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path

HISTORY_DB = Path(".cache/history.sqlite")


def _conn() -> sqlite3.Connection:
    HISTORY_DB.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(HISTORY_DB))
    conn.execute(
        """CREATE TABLE IF NOT EXISTS history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts REAL, name TEXT, slug TEXT,
            score INTEGER, hard INTEGER, medium INTEGER, soft INTEGER, unknown INTEGER,
            columns_json TEXT, detail_json TEXT)"""
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_hist_slug ON history(slug)")
    return conn


def append(row: dict, ts: float | None = None) -> None:
    """Persist one candidate's result. `row` carries name, slug, the score fields,
    columns (dict col->status) and detail (list of hit dicts)."""
    conn = _conn()
    try:
        conn.execute(
            "INSERT INTO history (ts,name,slug,score,hard,medium,soft,unknown,"
            "columns_json,detail_json) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (ts or time.time(), row["name"], row["slug"], row["score"], row["hard"],
             row["medium"], row["soft"], row["unknown"],
             json.dumps(row["columns"]), json.dumps(row["detail"])),
        )
        conn.commit()
    finally:
        conn.close()


def _rowdict(r) -> dict:
    keys = ("id", "ts", "name", "slug", "score", "hard", "medium", "soft", "unknown",
            "columns", "detail")
    d = dict(zip(keys[:-2], r[:-2], strict=False))
    d["columns"] = json.loads(r[-2])
    d["detail"] = json.loads(r[-1])
    return d


def latest() -> list[dict]:
    """Newest run of each name, ranked fewest-blockers-first."""
    if not HISTORY_DB.exists():
        return []
    conn = _conn()
    try:
        rows = conn.execute(
            "SELECT * FROM history WHERE id IN "
            "(SELECT MAX(id) FROM history GROUP BY slug) "
            "ORDER BY score ASC, hard ASC, unknown ASC"
        ).fetchall()
    finally:
        conn.close()
    return [_rowdict(r) for r in rows]


def timeline(slug: str) -> list[dict]:
    """All runs for one name, newest first."""
    if not HISTORY_DB.exists():
        return []
    conn = _conn()
    try:
        rows = conn.execute(
            "SELECT * FROM history WHERE slug = ? ORDER BY ts DESC", (slug,)
        ).fetchall()
    finally:
        conn.close()
    return [_rowdict(r) for r in rows]


def all_columns(rows: list[dict]) -> list[str]:
    """Union of column names across rows, preserving first-seen order."""
    seen: list[str] = []
    for row in rows:
        for col in row["columns"]:
            if col not in seen:
                seen.append(col)
    return seen
