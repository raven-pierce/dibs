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


def backup() -> Path | None:
    """Copy the history DB and reports into .cache/backups/<timestamp>/. Returns
    the backup directory, or None if there was nothing to back up."""
    has_history = history.HISTORY_DB.exists()
    has_reports = REPORTS.exists()
    if not (has_history or has_reports):
        return None
    base = BACKUP_DIR / time.strftime("%Y%m%d-%H%M%S")
    made, n = base, 1
    while made.exists():  # avoid collision on two backups in the same second
        made = base.with_name(f"{base.name}-{n}")
        n += 1
    made.mkdir(parents=True)
    if has_history:
        shutil.copy2(history.HISTORY_DB, made / "history.sqlite")
    if has_reports:
        shutil.copytree(REPORTS, made / "reports")
    return made


def reset(with_backup: bool = True) -> Path | None:
    """Optionally back up, then delete the history DB and reports directory.
    Returns the backup directory (or None if nothing backed up / backup disabled)."""
    made = backup() if with_backup else None
    if history.HISTORY_DB.exists():
        history.HISTORY_DB.unlink()
    if REPORTS.exists():
        shutil.rmtree(REPORTS)
    return made
