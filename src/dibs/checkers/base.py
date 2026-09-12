"""Checker interface. Every source implements this; the run loop treats them
uniformly. A checker that raises is caught by run() and turned into UNKNOWN, so
a source failure never crashes the run and is never counted as AVAILABLE.
"""

from __future__ import annotations

from typing import Protocol

from ..config import Config
from ..http import Http
from ..models import Candidate, CheckResult


class Checker(Protocol):
    name: str  # stable id, matches [checkers] config keys
    columns: list[str]  # table columns this source fills
    slow: bool  # True = deprioritized under --quick

    def requires(self, config: Config) -> str | None:
        """Return a human message if a prerequisite is missing, else None."""
        ...

    async def check(self, candidate: Candidate, http: Http, config: Config) -> list[CheckResult]:
        """Return one CheckResult per column. Must not raise for expected failures."""
        ...


class BaseChecker:
    name: str = ""
    columns: list[str] = []
    slow: bool = False

    def requires(self, config: Config) -> str | None:
        return None
