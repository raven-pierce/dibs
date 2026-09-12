"""Offline parser tests against recorded fixtures. Each asserts the pure parsing
logic so a change in a source's response shape is caught here, not in production.
"""

from __future__ import annotations

import json

from bs4 import BeautifulSoup
from conftest import load

from dibs.checkers.appstores import _match_tier, _parse_play
from dibs.checkers.cipo import _goods, _text_after, is_dead
from dibs.checkers.rdap import _extract_registration, _grep, _looks_blocked
from dibs.checkers.uspto import _int_classes, classify
from dibs.models import Tier, normalize_phrase, normalize_slug


# --- naming --------------------------------------------------------------
def test_normalize_slug_strips_accents_and_punct():
    assert normalize_slug("Zéphyr Labs!") == "zephyrlabs"


def test_normalize_phrase_uppercases_ascii():
    assert normalize_phrase("Zéphyr  Labs") == "ZEPHYR LABS"


# --- rdap ----------------------------------------------------------------
def test_rdap_extract_registration():
    created, registrar = _extract_registration(load("rdap_com_taken.json"))
    assert created.startswith("1997-09-15")
    assert registrar == "MarkMonitor Inc."


def test_rdap_blocked_notice_detected():
    assert _looks_blocked(load("rdap_works_blocked.json")) is True


def test_whois_grep_and_free():
    taken = load("whois_co_taken.txt")
    assert _grep(taken, "Creation Date:").startswith("2010-02-25")
    assert _grep(taken, "Registrar:") == "MarkMonitor, Inc."
    assert "NOT FOUND" in load("whois_co_free.txt").upper()


# --- uspto ---------------------------------------------------------------
def test_uspto_int_classes():
    assert _int_classes(["IC 009", "IC 042"]) == [9, 42]


def test_uspto_classify_tiers():
    core, adj = [9, 42], [35, 38, 41, 36]
    assert classify([42], True, core, adj) == Tier.HARD
    assert classify([42], False, core, adj) == Tier.MEDIUM
    assert classify([35], True, core, adj) == Tier.MEDIUM
    assert classify([12], True, core, adj) == Tier.INFO


def test_uspto_fixture_shape():
    docs = json.loads(load("uspto_zephyr.json"))["hits"]["hits"]
    alive = [d for d in docs if d["source"]["alive"]]
    dead = [d for d in docs if not d["source"]["alive"]]
    assert len(alive) == 2 and len(dead) == 1
    saas = next(d for d in docs if d["id"] == "87678206")["source"]
    assert _int_classes(saas["internationalClass"]) == [42]


# --- cipo ----------------------------------------------------------------
def test_cipo_is_dead():
    assert is_dead("EXPUNGED") is True
    assert is_dead("ABANDONED SECTION 36") is True
    assert is_dead("REGISTERED") is False
    assert is_dead("SOME NEW STATUS") is False  # unknown status treated as live


def test_cipo_list_fixture():
    docs = json.loads(load("cipo_zephyr_list.json"))["docs"]
    live = [d for d in docs if not is_dead(d["statusDesc"])]
    assert {d["appNo"] for d in live} == {"2143703"}


def test_cipo_detail_owner_and_goods():
    soup = BeautifulSoup(load("cipo_detail_2143703.html"), "html.parser")
    assert _text_after(soup, "Registered Owner") == "Nurse Call Systems Ltd."
    goods = _goods(soup)
    assert goods.startswith("9:")
    assert "nurse call" in goods.lower()


# --- app stores ----------------------------------------------------------
def test_itunes_match_tier():
    results = json.loads(load("itunes_zephyr_ca.json"))["results"]
    target = normalize_phrase("Zephyr")
    tiers = [_match_tier(target, r["trackName"]) for r in results]
    assert tiers[0] == Tier.MEDIUM  # exact "Zephyr"
    assert tiers[1] == Tier.SOFT    # "Weather Zephyr Pro" contains the word


def test_itunes_no_false_positive_on_unrelated():
    assert _match_tier(normalize_phrase("zephyr"), "Completely Different") is None


def test_play_parse_extracts_package_and_title():
    apps = _parse_play(load("play_zephyr.html"))
    assert apps is not None
    pkgs = dict(apps)
    assert "com.zephyrlabs.zephyr" in pkgs
    assert pkgs["com.zephyrlabs.zephyr"] == "Zephyr"


def test_play_parse_returns_none_on_garbage():
    assert _parse_play("<html>no apps here</html>") is None
