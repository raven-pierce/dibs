"""Static report snapshot regenerated from history (latest-per-name)."""

from __future__ import annotations

from dibs import history
from dibs.models import Candidate
from dibs.report import write_markdown, write_static_from_history
from dibs.scoring import ScoreRow


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


def test_markdown_per_run_timestamped(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    row = ScoreRow(candidate=Candidate("Kestrel"))
    write_markdown(row, ts=1_000_000_000)   # fixed run 1
    write_markdown(row, ts=1_000_000_060)   # run 2, 60s later
    folder = tmp_path / "reports" / "kestrel"
    md = sorted(p.name for p in folder.glob("*.md"))
    # Two timestamped runs kept, plus the stable latest.md pointer.
    assert "latest.md" in md
    assert len([m for m in md if m != "latest.md"]) == 2
