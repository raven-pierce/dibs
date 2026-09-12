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


def _find_member(zf: zipfile.ZipFile, prefix: str) -> str | None:
    for info in zf.namelist():
        base = info.rsplit("/", 1)[-1].lower()
        if base.startswith(prefix) and base.endswith((".csv", ".txt")):
            return info
    return None


def _open_reader(zf: zipfile.ZipFile, member: str):
    fh = zf.open(member)
    text = io.TextIOWrapper(fh, encoding="utf-8", errors="replace")
    reader = csv.DictReader(text)
    cols = {c.lower(): c for c in (reader.fieldnames or [])}
    return reader, cols


def _domain_labels(zf: zipfile.ZipFile, domain: str) -> dict[str, str]:
    """Map COD_DOM_VAL -> VAL_DOM_FRAN for one TYP_DOM_VAL from DomaineValeur."""
    member = _find_member(zf, "domainevaleur") or _find_member(zf, "domaine")
    if member is None:
        return {}
    reader, cols = _open_reader(zf, member)
    typ, cod, val = cols.get("typ_dom_val"), cols.get("cod_dom_val"), cols.get("val_dom_fran")
    if not (typ and cod and val):
        return {}
    out: dict[str, str] = {}
    for row in reader:
        if (row.get(typ) or "").strip().upper() == domain.upper():
            out[(row.get(cod) or "").strip()] = (row.get(val) or "").strip()
    return out


def req_import(zip_path: str | Path) -> int:
    """Index REQ names with enterprise status and name currency.

    Reads Nom (NOM_ASSUJ, NEQ, DAT_FIN_NOM_ASSUJ), Entreprise (COD_STAT_IMMAT) and
    DomaineValeur (to resolve the STAT_IMMAT code to its French label). Enterprise
    status and name-end-date let the checker gate struck enterprises and withdrawn
    names as context, not blockers. Entreprise/DomaineValeur are optional; without
    them status is blank (treated live). Columns matched case-insensitively."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    zip_path = Path(zip_path)
    if REQ_DB.exists():
        REQ_DB.unlink()
    conn = sqlite3.connect(str(REQ_DB))
    conn.execute("CREATE TABLE ent (name_norm TEXT, name TEXT, neq TEXT, name_current INTEGER)")
    conn.execute("CREATE TABLE ent_stat (neq TEXT PRIMARY KEY, status TEXT)")
    total = 0
    with zipfile.ZipFile(zip_path) as zf:
        nom = _find_member(zf, "nom")
        if nom is None:
            conn.close()
            raise ValueError("no Nom.csv-like file found in the ZIP")

        # Enterprise status: COD_STAT_IMMAT resolved to its French label.
        ent_member = _find_member(zf, "entreprise")
        if ent_member is not None:
            labels = _domain_labels(zf, "STAT_IMMAT")
            reader, cols = _open_reader(zf, ent_member)
            neq_c, stat_c = cols.get("neq"), cols.get("cod_stat_immat")
            if neq_c and stat_c:
                batch = []
                for row in reader:
                    neq = (row.get(neq_c) or "").strip()
                    code = (row.get(stat_c) or "").strip()
                    if not neq:
                        continue
                    batch.append((neq, labels.get(code, code)))
                    if len(batch) >= 5000:
                        conn.executemany("INSERT OR REPLACE INTO ent_stat VALUES (?,?)", batch)
                        batch = []
                if batch:
                    conn.executemany("INSERT OR REPLACE INTO ent_stat VALUES (?,?)", batch)

        # Names.
        reader, cols = _open_reader(zf, nom)
        name_col = cols.get("nom_assuj")
        neq_col = cols.get("neq")
        enddate_col = cols.get("dat_fin_nom_assuj")
        if name_col is None:
            conn.close()
            raise ValueError(f"NOM_ASSUJ column not found; headers were {list(cols)}")
        rows = []
        for row in reader:
            name = (row.get(name_col) or "").strip()
            if not name:
                continue
            current = 1 if (not enddate_col or not (row.get(enddate_col) or "").strip()) else 0
            rows.append((normalize_phrase(name), name,
                         row.get(neq_col, "") if neq_col else "", current))
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
            "SELECT e.name, e.neq, COALESCE(s.status,''), e.name_current, e.name_norm "
            "FROM ent e LEFT JOIN ent_stat s ON e.neq = s.neq "
            "WHERE e.name_norm = ? OR e.name_norm LIKE ? LIMIT ?",
            (target, f"%{target}%", limit),
        ).fetchall()
    finally:
        conn.close()
    return [
        {"name": n, "neq": neq, "status": st, "name_current": bool(cur), "exact": nn == target}
        for n, neq, st, cur, nn in rows
    ]
