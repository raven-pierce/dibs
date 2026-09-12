"""Background check runner for the dashboard and API. A single worker thread
drains a queue, so checks run one at a time (no double-hitting rate-limited
sources). Identical in-flight requests are deduped. Each job tracks live progress;
results land in history and the static reports."""

from __future__ import annotations

import asyncio
import json
import queue
import threading
import time
import uuid

from . import history
from .config import Config
from .models import Candidate
from .report import history_row, write_markdown, write_static_from_history
from .run import run as run_screening
from .scoring import score_candidate


def _fingerprint(candidate: Candidate, only, skip, quick) -> str:
    return json.dumps(
        {"d": candidate.display, "l": candidate.legal, "t": candidate.tm,
         "dm": candidate.domains, "h": candidate.handles,
         "only": sorted(only) if only else None,
         "skip": sorted(skip) if skip else None, "q": bool(quick)},
        default=list, sort_keys=True,
    )


class JobManager:
    def __init__(self, config: Config):
        self.config = config
        self._q: queue.Queue = queue.Queue()
        self._jobs: dict[str, dict] = {}
        self._lock = threading.Lock()
        threading.Thread(target=self._loop, daemon=True).start()

    def submit(self, candidate: Candidate, only=None, skip=None, quick=False) -> str:
        fp = _fingerprint(candidate, only, skip, quick)
        with self._lock:
            for j in self._jobs.values():
                if j["state"] in ("queued", "running") and j["fp"] == fp:
                    return j["id"]  # dedupe an identical in-flight request
            jid = uuid.uuid4().hex[:12]
            self._jobs[jid] = {
                "id": jid, "name": candidate.display, "slug": candidate.slug or "unnamed",
                "state": "queued", "done": 0, "total": 0, "checker": None,
                "error": None, "score": None, "result": None,
                "created": time.time(), "finished": None, "fp": fp,
            }
        self._q.put((jid, candidate, only, skip, quick))
        return jid

    def job(self, jid: str) -> dict | None:
        with self._lock:
            j = self._jobs.get(jid)
            return dict(j) if j else None

    def snapshot(self) -> list[dict]:
        with self._lock:
            jobs = sorted(self._jobs.values(), key=lambda j: j["created"], reverse=True)
            return [dict(j) for j in jobs[:20]]

    def running_count(self) -> int:
        with self._lock:
            return sum(1 for j in self._jobs.values() if j["state"] in ("queued", "running"))

    def wait(self, jid: str, timeout: float) -> dict | None:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            j = self.job(jid)
            if j is None or j["state"] in ("done", "error"):
                return j
            time.sleep(0.1)
        return self.job(jid)

    def _set(self, jid: str, **kw) -> None:
        with self._lock:
            if jid in self._jobs:
                self._jobs[jid].update(kw)

    def _loop(self) -> None:
        while True:
            jid, candidate, only, skip, quick = self._q.get()
            self._set(jid, state="running", started=time.time())

            def cb(_name, checker, done, total, _jid=jid):
                self._set(_jid, done=done, total=total, checker=checker)

            try:
                outcome = asyncio.run(run_screening(
                    [candidate], self.config,
                    only=set(only) if only else None,
                    skip=set(skip) if skip else None,
                    quick=bool(quick), progress=cb))
                results = outcome.results.get(candidate.display, [])
                row = score_candidate(candidate, results, self.config)
                payload = history_row(row)
                history.append(payload, ts=time.time())
                write_markdown(row)
                write_static_from_history()
                self._set(jid, state="done", finished=time.time(),
                          score=row.score, slug=payload["slug"], result=payload)
            except Exception as exc:  # noqa: BLE001 - surface, don't crash the worker
                self._set(jid, state="error", finished=time.time(),
                          error=f"{type(exc).__name__}: {exc}")
            finally:
                self._q.task_done()
