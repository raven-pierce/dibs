"""End-to-end checker behaviour with mocked HTTP (respx), proving the three states
propagate and that a failing source becomes UNKNOWN rather than AVAILABLE.
"""

from __future__ import annotations

from copy import deepcopy

import httpx
import pytest
import respx
from conftest import load

from dibs.checkers.devns import GithubChecker, PypiChecker
from dibs.checkers.rdap import RdapChecker
from dibs.config import DEFAULTS, Config
from dibs.http import Http
from dibs.models import Candidate, Status


def cfg(tmp_path) -> Config:
    c = Config(deepcopy(DEFAULTS))
    c.data["contact"] = "https://example.invalid/dibs"
    # Point the cache at a temp dir so tests never touch a real .cache.
    return c


@pytest.fixture
def http(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    h = Http(cfg(tmp_path), use_cache=False)
    yield h


@respx.mock
async def test_github_taken_available_unknown(http):
    c = Candidate("Zephyr")
    route = respx.get("https://api.github.com/users/zephyr")

    route.mock(return_value=httpx.Response(200, json={"login": "zephyr"}))
    res = (await GithubChecker().check(c, http, http.config))[0]
    assert res.worst == Status.TAKEN

    route.mock(return_value=httpx.Response(404, json={"message": "Not Found"}))
    res = (await GithubChecker().check(Candidate("Zephyr2"), http, http.config))[0]
    # different slug -> new route needed; instead re-mock same slug via cache-off
    respx.get("https://api.github.com/users/zephyr2").mock(
        return_value=httpx.Response(404))
    res = (await GithubChecker().check(Candidate("zephyr2"), http, http.config))[0]
    assert res.worst == Status.AVAILABLE

    respx.get("https://api.github.com/users/rate").mock(
        return_value=httpx.Response(403, json={"message": "rate limited"}))
    res = (await GithubChecker().check(Candidate("rate"), http, http.config))[0]
    assert res.worst == Status.UNKNOWN
    assert res.errors


@respx.mock
async def test_pypi_normalizes_name(http):
    # An explicit handle keeps its separators; PyPI applies PEP 503 collapse.
    respx.get("https://pypi.org/pypi/typing-extensions/json").mock(
        return_value=httpx.Response(200, json={"info": {}}))
    cand = Candidate("Typing Extensions", handles=("Typing_Extensions",))
    res = (await PypiChecker().check(cand, http, http.config))[0]
    assert res.worst == Status.TAKEN


@respx.mock
async def test_network_error_is_unknown_not_available(http):
    respx.get("https://pypi.org/pypi/zephyr/json").mock(
        side_effect=httpx.ConnectError("down"))
    res = (await PypiChecker().check(Candidate("Zephyr"), http, http.config))[0]
    assert res.worst == Status.UNKNOWN


@respx.mock
async def test_rdap_com_taken_ca_available(http):
    # Minimal bootstrap mapping com/ca to test hosts.
    boot = {
        "services": [
            [["com"], ["https://rdap.example-com/"]],
            [["ca"], ["https://rdap.example-ca/"]],
        ]
    }
    respx.get("https://data.iana.org/rdap/dns.json").mock(
        return_value=httpx.Response(200, json=boot))
    respx.get("https://rdap.example-com/domain/zephyr.com").mock(
        return_value=httpx.Response(200, text=load("rdap_com_taken.json")))
    respx.get("https://rdap.example-ca/domain/zephyr.ca").mock(
        return_value=httpx.Response(404, text=""))

    cfg_small = http.config
    cfg_small.data["domains"]["tlds"] = ["com", "ca"]
    cfg_small.data["domains"]["variants"] = []
    results = await RdapChecker().check(Candidate("Zephyr"), http, cfg_small)
    by_col = {r.column: r for r in results}
    assert by_col[".com"].worst == Status.TAKEN
    assert by_col[".com"].hits[0].extra["registrar"] == "MarkMonitor Inc."
    assert by_col[".ca"].worst == Status.AVAILABLE
