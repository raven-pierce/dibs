"""Corporations Canada (federal) and Québec REQ name checkers. Both query a local
SQLite index built from open data (see dibs.data). Exact name match => hard
blocker; substring match => medium. Until the index exists the checker reports
UNKNOWN with the step needed to build it, never AVAILABLE.
"""

from __future__ import annotations

from urllib.parse import quote

from .. import data
from ..models import CheckResult, Hit, Status, Tier
from .base import BaseChecker

CORPCAN_SEARCH = "https://ised-isde.canada.ca/cc/lgcy/fdrlCrpSrch.html?p=0&crpNm={q}"
REQ_SEARCH = (
    "https://www.registreentreprises.gouv.qc.ca/REQNA/GR/GR03/GR03A71."
    "RechercheRegistre.MVC/GR03A71"
)


class CorpCanChecker(BaseChecker):
    name = "corpcan"
    columns = ["CorpCan"]

    async def check(self, candidate, http, config):
        r = CheckResult(self.name, "CorpCan", candidate)
        if not data.corpcan_ready():
            r.errors.append("Corporations Canada index missing; run `dibs data refresh`")
            return [r]
        for term in candidate.legal_terms():
            verify = CORPCAN_SEARCH.format(q=quote(term))
            rows = data.corpcan_search(term)
            if not rows:
                r.hits.append(Hit(f"{term} (Corporations Canada)", Status.AVAILABLE,
                                  Tier.HARD, verify, "no federal corporation with this name"))
                continue
            for row in rows[:25]:
                tier = Tier.HARD if row["exact"] else Tier.MEDIUM
                kind = "EXACT" if row["exact"] else "CONTAINS"
                r.hits.append(Hit(
                    f"{row['name']} [{row['status']}] {kind}",
                    Status.TAKEN, tier, verify,
                    f"corp #{row['number']}",
                    extra={"exact": row["exact"]},
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
        for term in candidate.legal_terms():
            rows = data.req_search(term)
            if not rows:
                r.hits.append(Hit(f"{term} (REQ)", Status.AVAILABLE, Tier.HARD,
                                  REQ_SEARCH, "no Québec enterprise with this name"))
                continue
            for row in rows[:25]:
                tier = Tier.HARD if row["exact"] else Tier.MEDIUM
                kind = "EXACT" if row["exact"] else "CONTAINS"
                r.hits.append(Hit(
                    f"{row['name']} [{row['status']}] {kind}",
                    Status.TAKEN, tier, REQ_SEARCH,
                    f"NEQ {row['neq']}",
                    extra={"exact": row["exact"]},
                ))
        return [r]
