"""SQLite response cache (keyed by method + URL + body) and per-group daily
counters. 404s are cached (meaningful); 429/5xx are not."""

from __future__ import annotations

import asyncio
import hashlib
import json
import sqlite3
import time
from dataclasses import dataclass
from datetime import date
from pathlib import Path


@dataclass
class CachedResponse:
    status: int
    headers: dict
    text: str
    from_cache: bool = True


def _key(method: str, url: str, body: str | None) -> str:
    raw = f"{method.upper()} {url} {body or ''}".encode()
    return hashlib.sha256(raw).hexdigest()


class Cache:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self._lock = asyncio.Lock()
        self._conn = sqlite3.connect(str(path), check_same_thread=False)
        self._conn.execute(
            """CREATE TABLE IF NOT EXISTS responses (
                key TEXT PRIMARY KEY, status INTEGER, headers TEXT,
                body TEXT, fetched_at REAL)"""
        )
        self._conn.execute(
            """CREATE TABLE IF NOT EXISTS budget (
                grp TEXT, day TEXT, count INTEGER, PRIMARY KEY (grp, day))"""
        )
        self._conn.commit()

    async def get(
        self, method: str, url: str, body: str | None, ttl_seconds: float
    ) -> CachedResponse | None:
        async with self._lock:
            row = self._conn.execute(
                "SELECT status, headers, body, fetched_at FROM responses WHERE key=?",
                (_key(method, url, body),),
            ).fetchone()
        if row is None:
            return None
        status, headers, text, fetched_at = row
        if time.time() - fetched_at > ttl_seconds:
            return None
        return CachedResponse(status, json.loads(headers), text, from_cache=True)

    async def put(
        self, method: str, url: str, body: str | None, status: int, headers: dict, text: str
    ) -> None:
        async with self._lock:
            self._conn.execute(
                "INSERT OR REPLACE INTO responses VALUES (?,?,?,?,?)",
                (_key(method, url, body), status, json.dumps(headers), text, time.time()),
            )
            self._conn.commit()

    async def budget_check_and_increment(self, group: str, limit: int | None) -> bool:
        """Return True if a request is allowed today; increment the counter when so."""
        if limit is None:
            return True
        today = date.today().isoformat()
        async with self._lock:
            row = self._conn.execute(
                "SELECT count FROM budget WHERE grp=? AND day=?", (group, today)
            ).fetchone()
            count = row[0] if row else 0
            if count >= limit:
                return False
            self._conn.execute(
                "INSERT OR REPLACE INTO budget VALUES (?,?,?)", (group, today, count + 1)
            )
            self._conn.commit()
            return True

    def close(self) -> None:
        self._conn.close()
