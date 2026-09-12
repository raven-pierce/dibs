"""Configuration loading: built-in defaults, TOML overlay, environment overrides."""

from __future__ import annotations

import os
import tomllib
from copy import deepcopy
from pathlib import Path
from typing import Any

DEFAULTS: dict[str, Any] = {
    "contact": "",
    "domains": {
        "tlds": ["com", "ca", "io", "co", "dev", "app", "works", "ai", "net", "org"],
        "variants": [
            ["by", "prefix", "com"],
            ["get", "prefix", "com"],
            ["works", "suffix", "com"],
            ["works", "suffix", "ca"],
            ["hq", "suffix", "com"],
        ],
        "hyphen_tlds": ["com", "ca"],
        "rdap_overrides": {"io": "https://rdap.identitydigital.services/rdap/"},
        "whois_fallback": {"co": "whois.registry.co"},
    },
    "trademarks": {
        "core": [9, 42],
        "adjacent": [35, 38, 41, 36],
        "cipo_detail_cap": 10,
    },
    "status": {
        # Trademark statuses (CIPO statusDesc) whose presence means dead.
        "trademark_dead_markers": ["EXPUNGED", "ABANDON", "CANCEL", "REFUS",
                                   "WITHDRAW", "DEAD"],
        # Corporations Canada Status values that count as live. Anything else
        # present (Dissolved, Inactive) is dead; blank/unknown is treated live.
        "corpcan_active": ["Active"],
        # REQ enterprise STAT_IMMAT French-label substrings (accent/case-insensitive).
        "req_dead_substrings": ["radi"],   # radiée d'office / radiée sur demande
        "req_live_substrings": ["immatricul"],  # immatriculée
    },
    "weights": {"hard": 10, "medium": 3, "soft": 1},
    "cache": {"ttl_days": 7, "bootstrap_ttl_days": 30, "bulk_ttl_days": 7},
    "rate_limits": {
        "default": 0.2,
        "rdap": 0.5,
        "whois": 2.0,
        "cipo": 2.0,
        "uspto": 2.0,
        "github": 0.2,
        "ghsearch": 6.0,
        "crates": 1.0,
        "packagist": 0.3,
        "appstore": 3.0,
        "googleplay": 2.0,
        "wikipedia": 0.3,
        "hn": 0.4,
    },
    "daily_budgets": {"cipo": 400, "uspto": 400},
    "concurrency": {"global": 8},
    "retries": {"max": 3, "base_seconds": 1.0, "cap_seconds": 30.0},
    "timeouts": {"seconds": 20.0},
    "checkers": {
        "rdap": True,
        "uspto": True,
        "cipo": True,
        "github": True,
        "npm": True,
        "pypi": True,
        "crates": True,
        "dockerhub": True,
        "packagist": True,
        "appstore": True,
        "googleplay": True,
        "corpcan": True,
        "req": True,
        "social": True,
        "footprint": True,
    },
    "footprint": {"providers": ["wikipedia", "hn", "github", "brave"]},
}


def _deep_merge(base: dict, overlay: dict) -> dict:
    out = deepcopy(base)
    for key, value in overlay.items():
        if key in out and isinstance(out[key], dict) and isinstance(value, dict):
            out[key] = _deep_merge(out[key], value)
        else:
            out[key] = deepcopy(value)
    return out


class Config:
    def __init__(self, data: dict[str, Any], path: Path | None = None):
        self.data = data
        self.path = path

    @classmethod
    def load(cls, path: str | Path | None = None) -> Config:
        chosen: Path | None = None
        if path is not None:
            chosen = Path(path)
            if not chosen.exists():
                raise FileNotFoundError(f"config not found: {chosen}")
        else:
            default = Path.cwd() / "dibs.toml"
            if default.exists():
                chosen = default

        data = deepcopy(DEFAULTS)
        if chosen is not None:
            with chosen.open("rb") as fh:
                data = _deep_merge(data, tomllib.load(fh))

        env_contact = os.environ.get("DIBS_CONTACT")
        if env_contact:
            data["contact"] = env_contact
        return cls(data, chosen)

    # Convenience accessors -------------------------------------------------
    @property
    def contact(self) -> str:
        return (self.data.get("contact") or "").strip()

    def rate_interval(self, group: str) -> float:
        limits = self.data["rate_limits"]
        return float(limits.get(group, limits["default"]))

    def daily_budget(self, group: str) -> int | None:
        return self.data["daily_budgets"].get(group)

    def checker_enabled(self, name: str) -> bool:
        return bool(self.data["checkers"].get(name, True))

    def user_agent(self, version: str) -> str:
        contact = self.contact
        suffix = f" (+{contact})" if contact else ""
        return f"dibs/{version}{suffix}"
