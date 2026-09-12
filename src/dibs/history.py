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
    conn.row_factory = sqlite3.Row  # access by column name, order-independent
    conn.execute(
        """CREATE TABLE IF NOT EXISTS history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts REAL, name TEXT, slug TEXT,
            score INTEGER, hard INTEGER, medium INTEGER, soft INTEGER, unknown INTEGER,
            columns_json TEXT, detail_json TEXT, input_json TEXT)"""
    )
    # Migration: add input_json to a pre-existing table that lacks it.
    try:
        conn.execute("ALTER TABLE history ADD COLUMN input_json TEXT")
    except sqlite3.OperationalError:
        pass
    conn.execute("CREATE INDEX IF NOT EXISTS idx_hist_slug ON history(slug)")
    return conn


def append(row: dict, ts: float | None = None) -> None:
    """Persist one candidate's result. `row` carries name, slug, the score fields,
    columns, detail, and input (the dimensions used, for rerun)."""
    conn = _conn()
    try:
        conn.execute(
            "INSERT INTO history (ts,name,slug,score,hard,medium,soft,unknown,"
            "columns_json,detail_json,input_json) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (ts or time.time(), row["name"], row["slug"], row["score"], row["hard"],
             row["medium"], row["soft"], row["unknown"],
             json.dumps(row["columns"]), json.dumps(row["detail"]),
             json.dumps(row.get("input"))),
        )
        conn.commit()
    finally:
        conn.close()


def _rowdict(r) -> dict:
    return {
        "id": r["id"], "ts": r["ts"], "name": r["name"], "slug": r["slug"],
        "score": r["score"], "hard": r["hard"], "medium": r["medium"],
        "soft": r["soft"], "unknown": r["unknown"],
        "columns": json.loads(r["columns_json"]),
        "detail": json.loads(r["detail_json"]),
        "input": json.loads(r["input_json"]) if r["input_json"] else None,
    }


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


def run(run_id: int) -> dict | None:
    """One run by its unique id."""
    if not HISTORY_DB.exists():
        return None
    conn = _conn()
    try:
        r = conn.execute("SELECT * FROM history WHERE id = ?", (run_id,)).fetchone()
    finally:
        conn.close()
    return _rowdict(r) if r else None


def latest_overall() -> dict | None:
    """The single most recent run of any name."""
    if not HISTORY_DB.exists():
        return None
    conn = _conn()
    try:
        r = conn.execute("SELECT * FROM history ORDER BY id DESC LIMIT 1").fetchone()
    finally:
        conn.close()
    return _rowdict(r) if r else None


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
