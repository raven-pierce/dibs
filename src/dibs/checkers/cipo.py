"""CIPO trademarks via the JSON endpoint the public search form posts to (no free
official search API exists). Live hits in core/adjacent classes get a capped
detail fetch for owner and goods. Parse failure => raw saved, UNKNOWN, never a
silent zero-hit "clear"."""

from __future__ import annotations

import json

from bs4 import BeautifulSoup

from ..config import Config
from ..http import BudgetExceeded
from ..models import CheckResult, Hit, Status, Tier, normalize_phrase
from .base import BaseChecker
from .uspto import classify

SEARCH_URL = "https://ised-isde.canada.ca/cipo/trademark-search/srch"
DETAIL_URL = "https://ised-isde.canada.ca/cipo/trademark-search/{appno}?lang=eng"
DEAD_MARKERS = ("EXPUNGED", "ABANDON", "CANCEL", "REFUS", "WITHDRAW", "DEAD")


def is_dead(status_desc: str, markers=DEAD_MARKERS) -> bool:
    up = (status_desc or "").upper()
    return any(m in up for m in markers)


class CipoChecker(BaseChecker):
    name = "cipo"
    columns = ["CIPO"]
    slow = True

    def requires(self, config: Config) -> str | None:
        if not config.contact:
            return "CIPO needs a contact string (DIBS_CONTACT or config 'contact')"
        return None

    async def check(self, candidate, http, config):
        result = CheckResult(self.name, "CIPO", candidate)
        tm = config.data["trademarks"]
        markers = config.data["status"]["trademark_dead_markers"]
        state = {"detail_used": 0, "seen": set(), "markers": markers}  # shared across terms
        for term in candidate.tm_terms():
            docs = await self._search(term, http, candidate.slug, result)
            if docs is not None:
                await self._collect(term, docs, tm, http, candidate.slug, state, result)
        return [result]

    async def _search(self, term, http, slug, result):
        body = {"domIntlFilter": "1", "searchfield1": "tm", "textfield1": term,
                "display": "list", "maxReturn": "1000",
                "nicetextfield1": None, "cipotextfield1": None}
        try:
            resp = await http.post(SEARCH_URL, group="cipo", json_body=body,
                                   headers={"Accept": "application/json"})
        except BudgetExceeded as exc:
            result.errors.append(str(exc))
            return None
        except Exception as exc:  # noqa: BLE001
            result.errors.append(f"{term}: {type(exc).__name__}: {exc}")
            return None
        if resp.status != 200:
            result.errors.append(f"{term}: CIPO search HTTP {resp.status}")
            return None
        try:
            data = json.loads(resp.text)
            _ = data["numFound"]
            return data["docs"]
        except (ValueError, KeyError, TypeError):
            path = http.dump_raw("cipo", slug, resp.text)
            result.errors.append(f"{term}: unparseable CIPO response, saved to {path}")
            return None

    async def _collect(self, term, docs, tm, http, slug, state, result):
        target = normalize_phrase(term)
        core_adj = set(tm["core"]) | set(tm["adjacent"])
        for doc in docs:
            mark = doc.get("markName") or ""
            if target not in normalize_phrase(mark):
                continue
            appno = str(doc.get("appNo") or doc.get("id") or "")
            if appno and appno in state["seen"]:
                continue
            state["seen"].add(appno)
            classes = [int(c) for c in doc.get("niceCodes", []) if str(c).isdigit()]
            live = not is_dead(doc.get("statusDesc", ""), state["markers"])
            exact = normalize_phrase(mark) == target
            tier = classify(classes, exact, tm["core"], tm["adjacent"])
            owner, goods = "", ""
            if live and any(c in core_adj for c in classes) \
                    and state["detail_used"] < tm["cipo_detail_cap"]:
                state["detail_used"] += 1
                owner, goods = await self._fetch_detail(appno, http, slug, result)
            kind = "EXACT" if exact else "CONTAINS"
            label = (f"{mark} (Nice {','.join(str(c) for c in classes) or '?'}) "
                     f"{kind} [{doc.get('statusDesc', '?')}]")
            detail = "LIVE" if live else "DEAD"
            if owner:
                detail += f" | owner: {owner}"
            if goods:
                detail += f" | {goods[:300]}"
            result.hits.append(Hit(
                label, Status.TAKEN if live else Status.AVAILABLE,
                tier if live else Tier.INFO, url=DETAIL_URL.format(appno=appno),
                detail=detail,
                extra={"alive": live, "classes": classes, "exact": exact,
                       "appno": appno, "owner": owner, "term": term},
            ))

    async def _fetch_detail(self, appno, http, slug, result) -> tuple[str, str]:
        try:
            resp = await http.get(DETAIL_URL.format(appno=appno), group="cipo",
                                  accept_statuses=(200, 404))
            if resp.status != 200:
                return "", ""
            soup = BeautifulSoup(resp.text, "html.parser")
            owner = _text_after(soup, "Registered Owner") or _text_after(soup, "Owner")
            goods = _goods(soup)
            return owner.strip(), goods.strip()
        except Exception as exc:  # noqa: BLE001 - detail is best-effort, never fatal
            result.errors.append(f"CIPO detail {appno} failed: {type(exc).__name__}")
            return "", ""


def _text_after(soup: BeautifulSoup, heading: str) -> str:
    """Return text following the first heading/term whose text starts with `heading`."""
    for tag in soup.find_all(["h1", "h2", "h3", "h4", "dt", "th", "strong"]):
        if tag.get_text(strip=True).lower().startswith(heading.lower()):
            sib = tag.find_next(["dd", "td", "p", "div", "span"])
            if sib:
                return sib.get_text(" ", strip=True)
    return ""


def _goods(soup: BeautifulSoup) -> str:
    """Extract the goods/services statements as 'class: statement' pairs.

    The detail page lists them as dt (Nice class number) / dd (statement) under a
    'Goods (Nice class & Statement)' heading.
    """
    for tag in soup.find_all(["h1", "h2", "h3", "h4"]):
        if tag.get_text(strip=True).lower().startswith("goods"):
            dl = tag.find_next("dl")
            if not dl:
                break
            pairs = []
            for dt in dl.find_all("dt"):
                dd = dt.find_next_sibling("dd")
                cls = dt.get_text(" ", strip=True)
                stmt = dd.get_text(" ", strip=True) if dd else ""
                pairs.append(f"{cls}: {stmt}" if stmt else cls)
            if pairs:
                return " | ".join(pairs)
            return dl.get_text(" ", strip=True)
    return ""
