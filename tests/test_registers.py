"""REQ import (status + name currency) and register status classification."""

from __future__ import annotations

import io
import zipfile

import pytest

from dibs import data
from dibs.checkers.registers import corpcan_live, req_enterprise_live


def test_corpcan_live():
    active = ["Active"]
    assert corpcan_live("Active", active) is True
    assert corpcan_live("active", active) is True
    assert corpcan_live("", active) is True          # blank/unknown => live
    assert corpcan_live("Dissolved", active) is False
    assert corpcan_live("Inactive", active) is False


def test_req_enterprise_live():
    dead, live = ["radi"], ["immatricul"]
    assert req_enterprise_live("Immatriculée", dead, live) is True
    assert req_enterprise_live("Radiée d'office", dead, live) is False
    assert req_enterprise_live("Radiée sur demande", dead, live) is False
    assert req_enterprise_live("", dead, live) is True          # unknown => live
    assert req_enterprise_live("État inconnu", dead, live) is True


def _make_zip() -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr(
            "DomaineValeur.csv",
            "TYP_DOM_VAL,COD_DOM_VAL,VAL_DOM_FRAN\n"
            "STAT_IMMAT,I,Immatriculée\n"
            "STAT_IMMAT,R,Radiée d'office\n",
        )
        zf.writestr(
            "Entreprise.csv",
            "NEQ,COD_STAT_IMMAT\n"
            "1111111111,I\n"   # active
            "2222222222,R\n",  # struck
        )
        zf.writestr(
            "Nom.csv",
            "NEQ,NOM_ASSUJ,STAT_NOM,DAT_FIN_NOM_ASSUJ\n"
            "1111111111,Kestrel Technologies inc.,1,\n"       # active ent, current name
            "2222222222,Laboratoires Kestrel inc.,1,\n"        # struck ent, current name
            "1111111111,Ancien Kestrel inc.,3,2019-01-01\n",   # active ent, former name
        )
    return buf.getvalue()


@pytest.fixture
def req_zip(tmp_path, monkeypatch):
    monkeypatch.setattr(data, "DATA_DIR", tmp_path)
    monkeypatch.setattr(data, "REQ_DB", tmp_path / "req.sqlite")
    p = tmp_path / "req.zip"
    p.write_bytes(_make_zip())
    return p


def test_req_import_and_status(req_zip):
    n = data.req_import(req_zip)
    assert n == 3
    rows = {r["name"]: r for r in data.req_search("Kestrel")}
    assert rows["Kestrel Technologies inc."]["status"] == "Immatriculée"
    assert rows["Kestrel Technologies inc."]["name_current"] is True
    assert rows["Laboratoires Kestrel inc."]["status"] == "Radiée d'office"
    assert rows["Ancien Kestrel inc."]["name_current"] is False  # has an end date


def test_req_import_without_status_files(tmp_path, monkeypatch):
    # Nom-only ZIP still imports; status blank (treated live downstream).
    monkeypatch.setattr(data, "DATA_DIR", tmp_path)
    monkeypatch.setattr(data, "REQ_DB", tmp_path / "req.sqlite")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("Nom.csv", "NEQ,NOM_ASSUJ,DAT_FIN_NOM_ASSUJ\n9,Kestrel inc.,\n")
    p = tmp_path / "req.zip"
    p.write_bytes(buf.getvalue())
    assert data.req_import(p) == 1
    rows = data.req_search("Kestrel")
    assert rows[0]["status"] == ""
    assert rows[0]["name_current"] is True
