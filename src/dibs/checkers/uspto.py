"""USPTO trademarks via the undocumented tmsearch.uspto.gov JSON backend the public
UI calls (no keyed text-search API exists since the 2026 Developer Hub shutdown).
Loud failure: unparseable response saved to raw and reported UNKNOWN."""

from __future__ import annotations

import json

from ..config import Config
from ..http import BudgetExceeded
from ..models import CheckResult, Hit, Status, Tier, normalize_phrase
from .base import BaseChecker

SEARCH_URL = "https://tmsearch.uspto.gov/prod-stage-v1-0-0/tmsearch"


def classify(classes: list[int], exact: bool, core: list[int], adjacent: list[int]) -> Tier:
    if any(c in core for c in classes):
        return Tier.HARD if exact else Tier.MEDIUM
    if any(c in adjacent for c in classes):
        return Tier.MEDIUM if exact else Tier.SOFT
    return Tier.INFO


def _int_classes(raw: list[str]) -> list[int]:
    out = []
    for item in raw or []:
        digits = "".join(ch for ch in str(item) if ch.isdigit())
        if digits:
            out.append(int(digits))
    return out


class UsptoChecker(BaseChecker):
    name = "uspto"
    columns = ["USPTO"]
    slow = True

    def requires(self, config: Config) -> str | None:
        if not config.contact:
            return "USPTO needs a contact string (DIBS_CONTACT or config 'contact')"
        return None

    async def check(self, candidate, http, config):
        result = CheckResult(self.name, "USPTO", candidate)
        tm = config.data["trademarks"]
        seen: set[str] = set()  # serials already listed, across terms
        for term in candidate.tm_terms():
            query = {"query": {"bool": {"must": [{"match": {"WM": term}}]}},
                     "size": 50, "from": 0}
            try:
                resp = await http.post(SEARCH_URL, group="uspto", json_body=query,
                                       headers={"Accept": "application/json"})
            except BudgetExceeded as exc:
                result.errors.append(str(exc))
                continue
            except Exception as exc:  # noqa: BLE001
                result.errors.append(f"{term}: {type(exc).__name__}: {exc}")
                continue
            if resp.status != 200:
                result.errors.append(f"{term}: tmsearch HTTP {resp.status}")
                continue
            try:
                docs = json.loads(resp.text)["hits"]["hits"]
            except (ValueError, KeyError, TypeError):
                path = http.dump_raw("uspto", candidate.slug, resp.text)
                result.errors.append(f"{term}: unparseable tmsearch response, saved to {path}")
                continue
            self._collect(term, docs, tm, seen, result)
        return [result]

    def _collect(self, term, docs, tm, seen, result):
        target = normalize_phrase(term)
        for doc in docs:
            src = doc.get("source", {})
            mark = src.get("wordmark") or ""
            names = {normalize_phrase(mark)} | {
                normalize_phrase(p) for p in (src.get("wordmarkPseudoText") or [])
            }
            exact = target in names
            if not exact and not any(target in n for n in names):
                continue  # require literal containment, not loose token match
            serial = src.get("id") or src.get("registrationId") or ""
            if serial and serial in seen:
                continue
            seen.add(serial)
            classes = _int_classes(src.get("internationalClass", []))
            alive = bool(src.get("alive"))
            tier = classify(classes, exact, tm["core"], tm["adjacent"])
            goods = "; ".join(src.get("goodsAndServices", []) or [])[:400]
            owner = "; ".join(src.get("ownerName", []) or [])
            kind = "EXACT" if exact else "CONTAINS"
            label = f"{mark} (IC {','.join(str(c) for c in classes) or '?'}) {kind}"
            url = (f"https://tsdr.uspto.gov/#caseNumber={serial}"
                   "&caseType=SERIAL_NO&searchType=statusSearch")
            result.hits.append(Hit(
                label, Status.TAKEN if alive else Status.AVAILABLE,
                tier if alive else Tier.INFO, url=url,
                detail=f"{'LIVE' if alive else 'DEAD'} | owner: {owner} | {goods}",
                extra={"alive": alive, "classes": classes, "exact": exact,
                       "serial": serial, "owner": owner, "term": term},
            ))
