"""Orchestration: build enabled checkers, run every candidate x checker under the
shared HTTP client, and collect results. --quick runs fast checkers first and
skips slow ones for any candidate that already has a hard blocker.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field

from .checkers import ALL_CHECKERS, BaseChecker
from .config import Config
from .http import Http
from .models import Candidate, CheckResult, Status, Tier


@dataclass
class RunOutcome:
    results: dict[str, list[CheckResult]] = field(default_factory=dict)  # keyed by display
    disabled: dict[str, str] = field(default_factory=dict)  # checker -> reason
    active_columns: list[str] = field(default_factory=list)


def _has_hard(results: list[CheckResult]) -> bool:
    return any(
        h.status == Status.TAKEN and h.tier == Tier.HARD
        for r in results
        for h in r.hits
    )


async def _run_checker(checker, candidate, http, config, bucket) -> None:
    try:
        results = await checker.check(candidate, http, config)
    except Exception as exc:  # noqa: BLE001 - a checker must never crash the run
        results = [
            CheckResult(checker.name, col, candidate,
                        errors=[f"checker crashed: {type(exc).__name__}: {exc}"])
            for col in checker.columns
        ]
    bucket.extend(results)


async def run(candidates: list[Candidate], config: Config, *, use_cache: bool = True,
              only: set[str] | None = None, skip: set[str] | None = None,
              quick: bool = False) -> RunOutcome:
    outcome = RunOutcome()
    http = Http(config, use_cache=use_cache)

    checkers: list[BaseChecker] = []
    for cls in ALL_CHECKERS:
        inst = cls()
        if not config.checker_enabled(inst.name):
            continue
        if only and inst.name not in only:
            continue
        if skip and inst.name in skip:
            continue
        missing = inst.requires(config)
        if missing:
            outcome.disabled[inst.name] = missing
            continue
        checkers.append(inst)

    for c in checkers:
        for col in c.columns:
            if col not in outcome.active_columns:
                outcome.active_columns.append(col)

    fast = [c for c in checkers if not c.slow]
    slow = [c for c in checkers if c.slow]

    try:
        for candidate in candidates:
            bucket: list[CheckResult] = []
            await asyncio.gather(
                *(_run_checker(c, candidate, http, config, bucket) for c in fast)
            )
            run_slow = slow
            if quick and _has_hard(bucket):
                for c in slow:
                    for col in c.columns:
                        bucket.append(CheckResult(c.name, col, candidate,
                                      errors=["skipped (--quick: hard blocker already found)"]))
                run_slow = []
            if run_slow:
                await asyncio.gather(
                    *(_run_checker(c, candidate, http, config, bucket) for c in run_slow)
                )
            outcome.results[candidate.display] = bucket
    finally:
        await http.aclose()
    return outcome
