"""Local SQLite name indexes for the two Canadian registers with no search API:
Corporations Canada (federal open data) and Québec REQ (imported ZIP). Built under
.cache/data so checkers query locally with no load on the source sites."""

from __future__ import annotations

import csv
import io
import sqlite3
import zipfile
from pathlib import Path

import httpx

from .models import normalize_phrase

DATA_DIR = Path(".cache/data")

CORPCAN_ACTIVE = [
    "https://d4bf66bykfyaf.cloudfront.net/corporations-active-cbca-en.csv",
    "https://d4bf66bykfyaf.cloudfront.net/corporations-active-non-cbca-en.csv",
]
CORPCAN_DISSOLVED = [
    "https://d4bf66bykfyaf.cloudfront.net/corporations-inactive-or-dissolved-cbca-en.csv",
    "https://d4bf66bykfyaf.cloudfront.net/corporations-inactive-or-dissolved-non-cbca-en.csv",
]
CORPCAN_DB = DATA_DIR / "corpcan.sqlite"
REQ_DB = DATA_DIR / "req.sqlite"


# --- Corporations Canada -------------------------------------------------
def corpcan_ready() -> bool:
    return CORPCAN_DB.exists()


def corpcan_refresh(include_dissolved: bool = False, ua: str = "dibs") -> int:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    urls = list(CORPCAN_ACTIVE) + (CORPCAN_DISSOLVED if include_dissolved else [])
    if CORPCAN_DB.exists():
        CORPCAN_DB.unlink()
    conn = sqlite3.connect(str(CORPCAN_DB))
    conn.execute(
        "CREATE TABLE corp (name_norm TEXT, name TEXT, number TEXT, status TEXT)"
    )
    total = 0
    with httpx.Client(timeout=120.0, headers={"User-Agent": ua}, follow_redirects=True) as client:
        for url in urls:
            with client.stream("GET", url) as resp:
                resp.raise_for_status()
                buf = io.StringIO()
                for chunk in resp.iter_text():
                    buf.write(chunk)
                buf.seek(0)
                reader = csv.DictReader(buf)
                rows = []
                for row in reader:
                    number = row.get("Corporation number") or row.get("Corporation Number") or ""
                    status = row.get("Status", "")
                    for col in ("Corporate name - form 1", "Corporate name - form 2"):
                        name = (row.get(col) or "").strip()
                        if name:
                            rows.append((normalize_phrase(name), name, number, status))
                    if len(rows) >= 5000:
                        conn.executemany("INSERT INTO corp VALUES (?,?,?,?)", rows)
                        total += len(rows)
                        rows = []
                if rows:
                    conn.executemany("INSERT INTO corp VALUES (?,?,?,?)", rows)
                    total += len(rows)
    conn.execute("CREATE INDEX idx_corp_norm ON corp(name_norm)")
    conn.commit()
    conn.close()
    return total


def corpcan_search(phrase: str, limit: int = 25) -> list[dict]:
    if not CORPCAN_DB.exists():
        return []
    target = normalize_phrase(phrase)
    conn = sqlite3.connect(str(CORPCAN_DB))
    try:
        rows = conn.execute(
            "SELECT name, number, status, name_norm FROM corp "
            "WHERE name_norm = ? OR name_norm LIKE ? LIMIT ?",
            (target, f"%{target}%", limit),
        ).fetchall()
    finally:
        conn.close()
    return [
        {"name": n, "number": num, "status": st, "exact": nn == target}
        for n, num, st, nn in rows
    ]


# --- REQ (Québec) --------------------------------------------------------
def req_ready() -> bool:
    return REQ_DB.exists()


def req_import(zip_path: str | Path) -> int:
    """Index the Nom file (NEQ, NOM_ASSUJ, STAT_NOM) from the REQ ZIP. Columns
    matched case-insensitively so a header tweak doesn't silently drop rows."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    zip_path = Path(zip_path)
    if REQ_DB.exists():
        REQ_DB.unlink()
    conn = sqlite3.connect(str(REQ_DB))
    conn.execute("CREATE TABLE ent (name_norm TEXT, name TEXT, neq TEXT, status TEXT)")
    total = 0
    with zipfile.ZipFile(zip_path) as zf:
        target_name = None
        for info in zf.namelist():
            base = info.rsplit("/", 1)[-1].lower()
            if base.startswith("nom") and base.endswith((".csv", ".txt")):
                target_name = info
                break
        if target_name is None:
            conn.close()
            raise ValueError("no Nom.csv-like file found in the ZIP")
        with zf.open(target_name) as fh:
            text = io.TextIOWrapper(fh, encoding="utf-8", errors="replace")
            reader = csv.DictReader(text)
            cols = {c.lower(): c for c in (reader.fieldnames or [])}
            name_col = cols.get("nom_assuj")
            neq_col = cols.get("neq")
            stat_col = cols.get("stat_nom")
            if name_col is None:
                conn.close()
                raise ValueError(f"NOM_ASSUJ column not found; headers were {reader.fieldnames}")
            rows = []
            for row in reader:
                name = (row.get(name_col) or "").strip()
                if not name:
                    continue
                rows.append((
                    normalize_phrase(name), name,
                    row.get(neq_col, "") if neq_col else "",
                    row.get(stat_col, "") if stat_col else "",
                ))
                if len(rows) >= 5000:
                    conn.executemany("INSERT INTO ent VALUES (?,?,?,?)", rows)
                    total += len(rows)
                    rows = []
            if rows:
                conn.executemany("INSERT INTO ent VALUES (?,?,?,?)", rows)
                total += len(rows)
    conn.execute("CREATE INDEX idx_ent_norm ON ent(name_norm)")
    conn.commit()
    conn.close()
    return total


def req_search(phrase: str, limit: int = 25) -> list[dict]:
    if not REQ_DB.exists():
        return []
    target = normalize_phrase(phrase)
    conn = sqlite3.connect(str(REQ_DB))
    try:
        rows = conn.execute(
            "SELECT name, neq, status, name_norm FROM ent "
            "WHERE name_norm = ? OR name_norm LIKE ? LIMIT ?",
            (target, f"%{target}%", limit),
        ).fetchall()
    finally:
        conn.close()
    return [
        {"name": n, "neq": neq, "status": st, "exact": nn == target}
        for n, neq, st, nn in rows
    ]
