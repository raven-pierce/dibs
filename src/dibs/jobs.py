"""Background check runner for the dashboard. A single worker thread drains a
queue, so browser-triggered checks run one at a time (no double-hitting rate-
limited sources). Each job tracks live progress; results land in the history."""

from __future__ import annotations

import asyncio
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


class JobManager:
    def __init__(self, config: Config):
        self.config = config
        self._q: queue.Queue = queue.Queue()
        self._jobs: dict[str, dict] = {}
        self._lock = threading.Lock()
        threading.Thread(target=self._loop, daemon=True).start()

    def submit(self, candidate: Candidate) -> str:
        jid = uuid.uuid4().hex[:12]
        with self._lock:
            self._jobs[jid] = {
                "id": jid, "name": candidate.display, "slug": candidate.slug or "unnamed",
                "state": "queued", "done": 0, "total": 0, "checker": None,
                "error": None, "score": None, "created": time.time(), "finished": None,
            }
        self._q.put((jid, candidate))
        return jid

    def snapshot(self) -> list[dict]:
        with self._lock:
            jobs = sorted(self._jobs.values(), key=lambda j: j["created"], reverse=True)
        return jobs[:20]

    def _set(self, jid: str, **kw) -> None:
        with self._lock:
            if jid in self._jobs:
                self._jobs[jid].update(kw)

    def _loop(self) -> None:
        while True:
            jid, candidate = self._q.get()
            self._set(jid, state="running", started=time.time())

            def cb(_name, checker, done, total, _jid=jid):
                self._set(_jid, done=done, total=total, checker=checker)

            try:
                outcome = asyncio.run(
                    run_screening([candidate], self.config, progress=cb))
                results = outcome.results.get(candidate.display, [])
                row = score_candidate(candidate, results, self.config)
                history.append(history_row(row), ts=time.time())
                write_markdown(row)             # per-name report
                write_static_from_history()      # full CSV + HTML snapshot
                self._set(jid, state="done", finished=time.time(),
                          score=row.score, slug=candidate.slug or "unnamed")
            except Exception as exc:  # noqa: BLE001 - surface, don't crash the worker
                self._set(jid, state="error", finished=time.time(),
                          error=f"{type(exc).__name__}: {exc}")
            finally:
                self._q.task_done()
