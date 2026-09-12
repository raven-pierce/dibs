"""Checker registry. Order here is the table column order."""

from __future__ import annotations

from .appstores import AppStoreChecker, GooglePlayChecker
from .base import BaseChecker, Checker
from .cipo import CipoChecker
from .devns import (
    CratesChecker,
    DockerhubChecker,
    GithubChecker,
    NpmChecker,
    PackagistChecker,
    PypiChecker,
)
from .footprint import FootprintChecker
from .rdap import RdapChecker
from .registers import CorpCanChecker, ReqChecker
from .social import SocialChecker
from .uspto import UsptoChecker

ALL_CHECKERS: list[type[BaseChecker]] = [
    RdapChecker,
    UsptoChecker,
    CipoChecker,
    CorpCanChecker,
    ReqChecker,
    GithubChecker,
    NpmChecker,
    PypiChecker,
    CratesChecker,
    DockerhubChecker,
    PackagistChecker,
    AppStoreChecker,
    GooglePlayChecker,
    SocialChecker,
    FootprintChecker,
]

__all__ = ["ALL_CHECKERS", "BaseChecker", "Checker"]
