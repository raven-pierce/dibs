"""Reset: backup then clear history + reports; caches untouched."""

from __future__ import annotations

from dibs import history, maintenance


def _seed_row(name):
    return {"name": name, "slug": name.lower(), "score": 0, "hard": 0, "medium": 0,
            "soft": 0, "unknown": 0, "columns": {}, "detail": [], "input": None}


def _wire(tmp_path, monkeypatch):
    monkeypatch.setattr(history, "HISTORY_DB", tmp_path / ".cache" / "history.sqlite")
    reports = tmp_path / "reports"
    reports.mkdir()
    (reports / "index.html").write_text("snapshot", encoding="utf-8")
    monkeypatch.setattr(maintenance, "REPORTS", reports)
    monkeypatch.setattr(maintenance, "BACKUP_DIR", tmp_path / ".cache" / "backups")
    return reports


def test_reset_backs_up_and_clears(tmp_path, monkeypatch):
    reports = _wire(tmp_path, monkeypatch)
    history.append(_seed_row("Zephyr"), ts=1.0)
    assert history.HISTORY_DB.exists()

    backup = maintenance.reset()
    assert not history.HISTORY_DB.exists()
    assert not reports.exists()
    assert (backup / "history.sqlite").exists()
    assert (backup / "reports" / "index.html").read_text() == "snapshot"


def test_reset_no_backup(tmp_path, monkeypatch):
    _wire(tmp_path, monkeypatch)
    history.append(_seed_row("Zephyr"), ts=1.0)
    assert maintenance.reset(with_backup=False) is None
    assert not history.HISTORY_DB.exists()
    assert not (tmp_path / ".cache" / "backups").exists()


def test_backup_keeps_originals(tmp_path, monkeypatch):
    reports = _wire(tmp_path, monkeypatch)
    history.append(_seed_row("Zephyr"), ts=1.0)
    made = maintenance.backup()
    assert (made / "history.sqlite").exists()
    assert (made / "reports" / "index.html").read_text() == "snapshot"
    # Non-destructive: originals still there.
    assert history.HISTORY_DB.exists() and reports.exists()


def test_reset_empty_is_safe(tmp_path, monkeypatch):
    monkeypatch.setattr(history, "HISTORY_DB", tmp_path / ".cache" / "history.sqlite")
    monkeypatch.setattr(maintenance, "REPORTS", tmp_path / "reports")
    monkeypatch.setattr(maintenance, "BACKUP_DIR", tmp_path / ".cache" / "backups")
    assert maintenance.reset() is None  # nothing to do, no crash
