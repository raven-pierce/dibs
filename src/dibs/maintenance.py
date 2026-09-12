"""Reset: back up then clear the run history and reports for a fresh start. The
response cache (.cache/dibs.sqlite) and bulk-data indexes (.cache/data) are left
alone — a reset clears accumulated screening results, not the expensive caches."""

from __future__ import annotations

import shutil
import time
from pathlib import Path

from . import history
from .report import REPORTS

BACKUP_DIR = Path(".cache/backups")


def reset(backup: bool = True) -> Path | None:
    """Optionally back up, then delete the history DB and the reports directory.
    Returns the backup directory (or None if nothing to back up / backup disabled)."""
    made: Path | None = None
    has_history = history.HISTORY_DB.exists()
    has_reports = REPORTS.exists()

    if backup and (has_history or has_reports):
        stamp = time.strftime("%Y%m%d-%H%M%S")
        made = BACKUP_DIR / stamp
        made.mkdir(parents=True, exist_ok=True)
        if has_history:
            shutil.copy2(history.HISTORY_DB, made / "history.sqlite")
        if has_reports:
            shutil.copytree(REPORTS, made / "reports")

    if has_history:
        history.HISTORY_DB.unlink()
    if has_reports:
        shutil.rmtree(REPORTS)
    return made
