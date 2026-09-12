"""Turn a candidate's CheckResults into a score and ranking. Only TAKEN hits
score; UNKNOWN never scores and is tracked separately so a false "clear" cannot
be produced by a source that failed.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .config import Config
from .models import Candidate, CheckResult, Status, Tier


@dataclass
class ScoreRow:
    candidate: Candidate
    score: int = 0
    hard: int = 0
    medium: int = 0
    soft: int = 0
    unknown: int = 0
    results: list[CheckResult] = field(default_factory=list)


def score_candidate(candidate: Candidate, results: list[CheckResult], config: Config) -> ScoreRow:
    weights = config.data["weights"]
    row = ScoreRow(candidate=candidate, results=results)
    for result in results:
        if result.errors:
            row.unknown += 1
        for hit in result.hits:
            if hit.status == Status.UNKNOWN:
                row.unknown += 1
                continue
            if hit.status != Status.TAKEN or hit.tier == Tier.INFO:
                continue
            if hit.tier == Tier.HARD:
                row.hard += 1
                row.score += weights["hard"]
            elif hit.tier == Tier.MEDIUM:
                row.medium += 1
                row.score += weights["medium"]
            elif hit.tier == Tier.SOFT:
                row.soft += 1
                row.score += weights["soft"]
    return row


def rank(rows: list[ScoreRow]) -> list[ScoreRow]:
    """Fewest blockers first: score, then hard count, then unknown count."""
    return sorted(rows, key=lambda r: (r.score, r.hard, r.unknown))
