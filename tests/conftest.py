from __future__ import annotations

from pathlib import Path

import pytest

from dibs.config import Config

FIXTURES = Path(__file__).parent / "fixtures"


def pytest_addoption(parser):
    parser.addoption("--live", action="store_true", default=False,
                     help="run tests marked 'live' (hits real endpoints)")


def pytest_collection_modifyitems(config, items):
    if config.getoption("--live"):
        return
    skip = pytest.mark.skip(reason="needs --live")
    for item in items:
        if "live" in item.keywords:
            item.add_marker(skip)


def load(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


@pytest.fixture
def config() -> Config:
    from copy import deepcopy

    from dibs.config import DEFAULTS

    cfg = Config(deepcopy(DEFAULTS))
    cfg.data["contact"] = "https://example.invalid/dibs"
    return cfg
