"""Flexible input: Candidate dimensions, CSV parsing, per-dimension domain build."""

from __future__ import annotations

from copy import deepcopy

from dibs.checkers.rdap import build_domains
from dibs.cli import _candidate_from_row, _load_candidates, _split
from dibs.config import DEFAULTS, Config
from dibs.models import Candidate, candidate_from_fields


def cfg() -> Config:
    return Config(deepcopy(DEFAULTS))


def test_split_pipe():
    assert _split("kestrel|bykestrel| getkestrel ") == ("kestrel", "bykestrel", "getkestrel")
    assert _split("") is None
    assert _split(None) is None


def test_candidate_defaults_from_name():
    c = Candidate("Kestrel")
    assert c.legal_terms() == ["Kestrel"]
    assert c.tm_terms() == ["Kestrel"]
    assert c.primary_base == "kestrel"
    assert c.domain_bases() == ["kestrel"]
    assert c.handle_list() == ["kestrel"]


def test_candidate_explicit_dimensions():
    c = Candidate(
        display="Kestrel",
        legal=("Kestrel Technologies Inc.",),
        tm=("Kestrel",),
        domains=("kestrel", "bykestrel", "getkestrel"),
        handles=("kestrellabs", "bykestrel"),
    )
    assert c.legal_terms() == ["Kestrel Technologies Inc."]
    assert c.tm_terms() == ["Kestrel"]
    assert c.primary_base == "kestrel"
    assert c.domain_bases() == ["kestrel", "bykestrel", "getkestrel"]
    assert c.handle_list() == ["kestrellabs", "bykestrel"]


def test_candidate_multi_legal_and_tm():
    c = Candidate(
        display="Kestrel",
        legal=("Kestrel Labs", "Kestrel Technologies Inc."),
        tm=("Kestrel", "Kestrel Cloud"),
    )
    assert c.legal_terms() == ["Kestrel Labs", "Kestrel Technologies Inc."]
    assert c.tm_terms() == ["Kestrel", "Kestrel Cloud"]


def test_candidate_from_row():
    row = {
        "name": "Kestrel",
        "legal": "Kestrel Labs|Kestrel Technologies Inc.", "tm": "",
        "domains": "kestrel|bykestrel", "handles": "kestrellabs|bykestrel",
        "note": "ignored column",
    }
    c = _candidate_from_row(row)
    assert c.display == "Kestrel"
    assert c.legal == ("Kestrel Labs", "Kestrel Technologies Inc.")
    assert c.tm is None  # blank falls back to name
    assert c.tm_terms() == ["Kestrel"]
    assert c.domains == ("kestrel", "bykestrel")
    assert c.handles == ("kestrellabs", "bykestrel")


def test_candidate_from_row_requires_name():
    assert _candidate_from_row({"legal": "x", "domains": "y"}) is None


def test_candidate_from_fields():
    # Shared by the CSV loader and the dashboard form.
    c = candidate_from_fields("Kestrel", legal="Kestrel Inc.|Les Kestrel inc.",
                              tm="Kestrel", domains="kestrel|getkestrel", handles="kestrellabs")
    assert c.legal == ("Kestrel Inc.", "Les Kestrel inc.")
    assert c.domains == ("kestrel", "getkestrel")
    assert candidate_from_fields("  ") is None
    assert candidate_from_fields(None) is None
    # Dashboard chip inputs arrive as lists, not pipe-strings.
    c2 = candidate_from_fields("Kestrel", legal=["Kestrel Inc.", " Les Kestrel inc. ", ""])
    assert c2.legal == ("Kestrel Inc.", "Les Kestrel inc.")


def test_load_candidates_csv(tmp_path):
    p = tmp_path / "names.csv"
    p.write_text(
        "name,legal,domains,handles\n"
        "Kestrel,Kestrel Technologies Inc.,kestrel|bykestrel,kestrellabs\n"
        "Nimbus,,,\n",
        encoding="utf-8",
    )
    cands = _load_candidates([], p)
    assert [c.display for c in cands] == ["Kestrel", "Nimbus"]
    assert cands[0].domains == ("kestrel", "bykestrel")
    assert cands[1].domains is None  # derives from name
    assert cands[1].domain_bases() == ["nimbus"]


def test_load_candidates_txt(tmp_path):
    p = tmp_path / "names.txt"
    p.write_text("Kestrel\n# comment\n\nNimbus\n", encoding="utf-8")
    cands = _load_candidates([], p)
    assert [c.display for c in cands] == ["Kestrel", "Nimbus"]


def test_load_candidates_dedup():
    cands = _load_candidates(["Kestrel", "kestrel"], None)
    assert len(cands) == 1


def test_build_domains_explicit_no_prefix_variants():
    c = Candidate(display="Kestrel", domains=("kestrel", "bykestrel"))
    triples = build_domains(c, cfg())
    fqdns = {t[0] for t in triples}
    # Both bases across TLDs; no by/get/hq prefixes bolted onto the explicit list.
    assert "kestrel.com" in fqdns
    assert "bykestrel.com" in fqdns
    assert "getkestrel.com" not in fqdns
    assert "bybykestrel.com" not in fqdns
    # Only the first base's .com/.ca are primary (hard).
    primary_com = [t for t in triples if t[0] == "kestrel.com"][0]
    other_com = [t for t in triples if t[0] == "bykestrel.com"][0]
    assert primary_com[2] is True
    assert other_com[2] is False


def test_build_domains_derived_has_variants():
    c = Candidate(display="Kestrel")
    fqdns = {t[0] for t in build_domains(c, cfg())}
    assert "kestrel.com" in fqdns
    assert "getkestrel.com" in fqdns  # config prefix variant
    assert "kestrelhq.com" in fqdns   # config suffix variant
