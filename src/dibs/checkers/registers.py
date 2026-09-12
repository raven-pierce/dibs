"""Corporations Canada (federal) and Québec REQ name checkers, over a local SQLite
index (see dibs.data). Live enterprise + exact name => hard; contains => medium.
Dissolved/struck enterprises and withdrawn former names are demoted to INFO
(context, unscored), like a dead trademark. Missing index => UNKNOWN, never
AVAILABLE."""

from __future__ import annotations

from urllib.parse import quote

from .. import data
from ..models import CheckResult, Hit, Status, Tier, strip_accents
from .base import BaseChecker

CORPCAN_SEARCH = "https://ised-isde.canada.ca/cc/lgcy/fdrlCrpSrch.html?p=0&crpNm={q}"
REQ_SEARCH = (
    "https://www.registreentreprises.gouv.qc.ca/REQNA/GR/GR03/GR03A71."
    "RechercheRegistre.MVC/GR03A71"
)


def corpcan_live(status: str, active: list[str]) -> bool:
    """Live if status is blank/unknown or matches an active value; else dead."""
    st = (status or "").strip()
    if not st:
        return True
    return st.casefold() in {a.casefold() for a in active}


def req_enterprise_live(label: str, dead_subs: list[str], live_subs: list[str]) -> bool:
    """Classify a REQ STAT_IMMAT French label. Dead substring wins; otherwise live
    (including an explicit live substring, or an unknown/blank label)."""
    norm = strip_accents(label or "").lower()
    if any(s in norm for s in dead_subs):
        return False
    return True  # immatriculée, blank, or unrecognized => treated live


def _hit_for(name, status_text, live, exact, verify, extra_detail, extra):
    tier = (Tier.HARD if exact else Tier.MEDIUM) if live else Tier.INFO
    status = Status.TAKEN if live else Status.AVAILABLE
    kind = "EXACT" if exact else "CONTAINS"
    tag = "LIVE" if live else "DEAD"
    detail = f"{tag} [{status_text}]"
    if extra_detail:
        detail += f" | {extra_detail}"
    return Hit(f"{name} {kind}", status, tier, verify, detail, extra=extra)


class CorpCanChecker(BaseChecker):
    name = "corpcan"
    columns = ["CorpCan"]

    async def check(self, candidate, http, config):
        r = CheckResult(self.name, "CorpCan", candidate)
        if not data.corpcan_ready():
            r.errors.append("Corporations Canada index missing; run `dibs data refresh`")
            return [r]
        active = config.data["status"]["corpcan_active"]
        for term in candidate.legal_terms():
            verify = CORPCAN_SEARCH.format(q=quote(term))
            rows = data.corpcan_search(term)
            if not rows:
                r.hits.append(Hit(f"{term} (Corporations Canada)", Status.AVAILABLE,
                                  Tier.HARD, verify, "no federal corporation with this name"))
                continue
            for row in rows[:25]:
                live = corpcan_live(row["status"], active)
                r.hits.append(_hit_for(
                    row["name"], row["status"], live, row["exact"], verify,
                    f"corp #{row['number']}",
                    {"exact": row["exact"], "alive": live, "status": row["status"]},
                ))
        return [r]


class ReqChecker(BaseChecker):
    name = "req"
    columns = ["REQ"]

    async def check(self, candidate, http, config):
        r = CheckResult(self.name, "REQ", candidate)
        if not data.req_ready():
            r.errors.append(
                "REQ index missing. Download the open-data ZIP from "
                "https://www.donneesquebec.ca/recherche/dataset/registre-des-entreprises "
                "then run `dibs req import <zip>`"
            )
            return [r]
        st = config.data["status"]
        for term in candidate.legal_terms():
            rows = data.req_search(term)
            if not rows:
                r.hits.append(Hit(f"{term} (REQ)", Status.AVAILABLE, Tier.HARD,
                                  REQ_SEARCH, "no Québec enterprise with this name"))
                continue
            for row in rows[:25]:
                ent_live = req_enterprise_live(
                    row["status"], st["req_dead_substrings"], st["req_live_substrings"])
                # A live blocker needs an active enterprise AND a current name.
                live = ent_live and row["name_current"]
                status_text = row["status"] or "unknown"
                if not row["name_current"]:
                    status_text += ", former name"
                r.hits.append(_hit_for(
                    row["name"], status_text, live, row["exact"], REQ_SEARCH,
                    f"NEQ {row['neq']}",
                    {"exact": row["exact"], "alive": live,
                     "status": row["status"], "name_current": row["name_current"]},
                ))
        return [r]
