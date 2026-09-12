"""Core types. Every check resolves to TAKEN / AVAILABLE / UNKNOWN; a failed
source is UNKNOWN, never AVAILABLE."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from enum import StrEnum


class Status(StrEnum):
    TAKEN = "TAKEN"
    AVAILABLE = "AVAILABLE"
    UNKNOWN = "UNKNOWN"


class Tier(StrEnum):
    HARD = "hard"
    MEDIUM = "medium"
    SOFT = "soft"
    INFO = "info"  # never scored


def strip_accents(text: str) -> str:
    return unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")


def normalize_slug(text: str) -> str:
    """Lowercase ASCII, no separators: 'Zéphyr Labs' -> 'zephyrlabs'."""
    return re.sub(r"[^a-z0-9]", "", strip_accents(text).lower())


def normalize_hyphen(text: str) -> str:
    """Lowercase ASCII, separators to '-': 'Zéphyr Labs' -> 'zephyr-labs'."""
    return re.sub(r"[^a-z0-9]+", "-", strip_accents(text).lower()).strip("-")


def normalize_handle(text: str) -> str:
    """Handle form: lowercase ASCII, internal [._-] kept, spaces dropped."""
    ascii_low = strip_accents(text).lower().strip()
    return re.sub(r"[^a-z0-9._-]", "", ascii_low)


def normalize_phrase(text: str) -> str:
    """Comparison form for marks/entities: uppercase, accents stripped, spaces collapsed."""
    return re.sub(r"\s+", " ", strip_accents(text).upper()).strip()


@dataclass(frozen=True)
class Candidate:
    """One name to screen. Each dimension can be given explicitly; blank ones
    derive from `display`.

    - legal   -> corporate registers (Corporations Canada, REQ); may be several
    - tm      -> trademark checkers (USPTO, CIPO); may be several
    - domains -> explicit domain bases; first is the primary (hard .com/.ca)
    - handles -> explicit usernames for dev namespaces and social
    """

    display: str
    legal: tuple[str, ...] | None = None
    tm: tuple[str, ...] | None = None
    domains: tuple[str, ...] | None = None
    handles: tuple[str, ...] | None = None

    @property
    def phrase(self) -> str:
        """Display name, whitespace-collapsed (app-store / footprint search term)."""
        return re.sub(r"\s+", " ", self.display).strip()

    def legal_terms(self) -> list[str]:
        """Corporate search terms, or the display name when none given."""
        src = self.legal or (self.display,)
        return [re.sub(r"\s+", " ", t).strip() for t in src if t.strip()]

    def tm_terms(self) -> list[str]:
        """Trademark search terms, or the display name when none given."""
        src = self.tm or (self.display,)
        return [re.sub(r"\s+", " ", t).strip() for t in src if t.strip()]

    @property
    def slug(self) -> str:
        return normalize_slug(self.display)

    @property
    def hyphen(self) -> str:
        return normalize_hyphen(self.display)

    @property
    def is_multiword(self) -> bool:
        return self.hyphen != self.slug

    @property
    def primary_base(self) -> str:
        """Domain base whose .com/.ca count as hard blockers."""
        if self.domains:
            return normalize_slug(self.domains[0])
        return self.slug

    def domain_bases(self) -> list[str]:
        """Explicit bases (normalized) or the derived slug."""
        if self.domains:
            return [normalize_slug(d) for d in self.domains if normalize_slug(d)]
        return [self.slug] if self.slug else []

    def handle_list(self) -> list[str]:
        """Explicit handles (normalized) or the derived slug."""
        if self.handles:
            return [normalize_handle(h) for h in self.handles if normalize_handle(h)]
        return [self.slug] if self.slug else []


def split_multi(value) -> tuple[str, ...] | None:
    """Normalize a multi-value field to a tuple, or None if empty. Accepts a
    pipe-separated string (CSV) or a list/tuple of strings (dashboard form)."""
    if not value:
        return None
    if isinstance(value, str):
        items = value.split("|")
    else:
        items = list(value)
    parts = tuple(p.strip() for p in items if p and p.strip())
    return parts or None


def candidate_from_fields(
    name: str | None, legal=None, tm=None, domains=None, handles=None
) -> Candidate | None:
    """Build a Candidate from field values (each a pipe-string or a list). Blank
    name returns None. Shared by the CSV loader and the dashboard form."""
    name = (name or "").strip()
    if not name:
        return None
    return Candidate(
        display=name,
        legal=split_multi(legal),
        tm=split_multi(tm),
        domains=split_multi(domains),
        handles=split_multi(handles),
    )


@dataclass
class Hit:
    label: str
    status: Status
    tier: Tier = Tier.SOFT
    url: str | None = None
    detail: str = ""
    extra: dict = field(default_factory=dict)


@dataclass
class CheckResult:
    source: str
    column: str
    candidate: Candidate
    hits: list[Hit] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    @property
    def worst(self) -> Status:
        """Column-level status for the table cell."""
        statuses = {h.status for h in self.hits}
        if Status.TAKEN in statuses:
            return Status.TAKEN
        if self.errors or Status.UNKNOWN in statuses:
            return Status.UNKNOWN
        return Status.AVAILABLE
