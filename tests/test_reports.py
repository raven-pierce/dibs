"""Static report snapshot regenerated from history (latest-per-name)."""

from __future__ import annotations

from dibs import history
from dibs.report import write_static_from_history


def _row(name, score, hard, col_status):
    return {
        "name": name, "slug": name.lower(), "score": score,
        "hard": hard, "medium": 0, "soft": 0, "unknown": 0,
        "columns": {".com": {"status": col_status, "hard": hard > 0, "count": 1}},
        "detail": [{"source": "rdap", "column": ".com", "label": f"{name.lower()}.com",
                    "status": col_status, "tier": "hard", "detail": "", "url": None}],
    }


def test_snapshot_from_history(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    history.append(_row("Zephyr", 10, 1, "TAKEN"), ts=1.0)
    history.append(_row("Nimbus", 0, 0, "AVAILABLE"), ts=2.0)
    write_static_from_history()

    csv_text = (tmp_path / "reports" / "summary.csv").read_text()
    html_text = (tmp_path / "reports" / "index.html").read_text()
    # Both names present (full latest-per-name snapshot), ranked fewest first.
    assert "Zephyr" in csv_text and "Nimbus" in csv_text
    assert csv_text.index("Nimbus") < csv_text.index("Zephyr")  # score 0 before 10
    assert "Zephyr" in html_text and "Nimbus" in html_text
    assert "{{" not in html_text  # template fully rendered


def test_snapshot_latest_per_name(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    history.append(_row("Zephyr", 10, 1, "TAKEN"), ts=1.0)
    history.append(_row("Zephyr", 0, 0, "AVAILABLE"), ts=2.0)  # newer run of same name
    write_static_from_history()
    csv_text = (tmp_path / "reports" / "summary.csv").read_text()
    lines = [ln for ln in csv_text.splitlines() if ln.startswith("Zephyr")]
    assert len(lines) == 1  # only the latest run
    assert lines[0].split(",")[1] == "0"  # newest score
