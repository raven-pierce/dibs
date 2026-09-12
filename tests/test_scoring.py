"""Scoring, ranking, and the three-state cell logic."""

from __future__ import annotations

from copy import deepcopy

from dibs.config import DEFAULTS, Config
from dibs.models import Candidate, CheckResult, Hit, Status, Tier
from dibs.scoring import rank, score_candidate


def cfg() -> Config:
    return Config(deepcopy(DEFAULTS))


def test_hard_medium_soft_weights():
    c = Candidate("Zephyr")
    results = [
        CheckResult("rdap", ".com", c, hits=[Hit("zephyr.com", Status.TAKEN, Tier.HARD)]),
        CheckResult("github", "GitHub", c, hits=[Hit("gh", Status.TAKEN, Tier.MEDIUM)]),
        CheckResult("pypi", "PyPI", c, hits=[Hit("pypi", Status.TAKEN, Tier.SOFT)]),
    ]
    row = score_candidate(c, results, cfg())
    assert (row.hard, row.medium, row.soft) == (1, 1, 1)
    assert row.score == 10 + 3 + 1


def test_unknown_never_scores_and_is_counted():
    c = Candidate("Zephyr")
    results = [
        CheckResult("cipo", "CIPO", c, errors=["boom"]),
        CheckResult("rdap", ".ca", c, hits=[Hit("zephyr.ca", Status.UNKNOWN, Tier.HARD)]),
    ]
    row = score_candidate(c, results, cfg())
    assert row.score == 0
    assert row.unknown == 2


def test_info_tier_never_scores():
    c = Candidate("Zephyr")
    results = [CheckResult("footprint", "Web", c,
                           hits=[Hit("Web: 1200", Status.AVAILABLE, Tier.INFO)])]
    row = score_candidate(c, results, cfg())
    assert row.score == 0


def test_rank_fewest_blockers_first():
    a, b = Candidate("Aaa"), Candidate("Bbb")
    ra = score_candidate(a, [CheckResult("rdap", ".com", a,
                              hits=[Hit("a", Status.TAKEN, Tier.HARD)])], cfg())
    rb = score_candidate(b, [CheckResult("pypi", "PyPI", b,
                              hits=[Hit("b", Status.TAKEN, Tier.SOFT)])], cfg())
    assert [r.candidate.display for r in rank([ra, rb])] == ["Bbb", "Aaa"]


def test_checkresult_worst():
    c = Candidate("X")
    taken = CheckResult("rdap", ".com", c, hits=[
        Hit("a", Status.AVAILABLE), Hit("b", Status.TAKEN)])
    assert taken.worst == Status.TAKEN
    errored = CheckResult("cipo", "CIPO", c, errors=["x"])
    assert errored.worst == Status.UNKNOWN
    clear = CheckResult("rdap", ".ca", c, hits=[Hit("a", Status.AVAILABLE)])
    assert clear.worst == Status.AVAILABLE
