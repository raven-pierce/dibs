"""Rough common-law footprint: result counts, never a blocker (INFO only). Keyless
providers (Wikipedia, Hacker News, GitHub repos) plus Brave when BRAVE_API_KEY is
set. Pluggable via config [footprint].providers."""

from __future__ import annotations

import json
import os
from urllib.parse import quote

from ..models import CheckResult, Hit, Status, Tier
from .base import BaseChecker


class FootprintChecker(BaseChecker):
    name = "footprint"
    columns = ["Web"]
    slow = True

    async def check(self, candidate, http, config):
        r = CheckResult(self.name, "Web", candidate)
        providers = config.data["footprint"]["providers"]
        phrase = candidate.phrase
        if "wikipedia" in providers:
            await self._count(
                r, http, "Wikipedia",
                "https://en.wikipedia.org/w/api.php?action=query&list=search"
                f"&srsearch={quote(phrase)}&format=json&srlimit=1",
                "wikipedia", lambda d: d["query"]["searchinfo"]["totalhits"],
                f"https://en.wikipedia.org/w/index.php?search={quote(phrase)}",
            )
        if "hn" in providers:
            await self._count(
                r, http, "Hacker News",
                f"https://hn.algolia.com/api/v1/search?query={quote(phrase)}&tags=story",
                "hn", lambda d: d["nbHits"],
                f"https://hn.algolia.com/?query={quote(phrase)}",
            )
        if "github" in providers:
            headers = {"Accept": "application/vnd.github+json"}
            token = os.environ.get("GITHUB_TOKEN")
            if token:
                headers["Authorization"] = f"Bearer {token}"
            await self._count(
                r, http, "GitHub repos",
                f"https://api.github.com/search/repositories?q={quote(phrase)}",
                "ghsearch", lambda d: d["total_count"],
                f"https://github.com/search?q={quote(phrase)}&type=repositories",
                headers=headers,
            )
        if "brave" in providers and os.environ.get("BRAVE_API_KEY"):
            await self._brave(r, http, f'"{phrase}" software')
        return [r]

    async def _count(self, r, http, label, url, group, extract, page, headers=None):
        try:
            resp = await http.get(url, group=group, headers=headers, accept_statuses=(200,))
            if resp.status != 200:
                r.hits.append(Hit(label, Status.UNKNOWN, Tier.INFO, page, f"HTTP {resp.status}"))
                return
            count = extract(json.loads(resp.text))
            r.hits.append(Hit(f"{label}: {count}", Status.AVAILABLE, Tier.INFO, page,
                              f"{count} result(s)", extra={"count": count}))
        except Exception as exc:  # noqa: BLE001
            r.hits.append(Hit(label, Status.UNKNOWN, Tier.INFO, page, f"{type(exc).__name__}"))

    async def _brave(self, r, http, query):
        url = f"https://api.search.brave.com/res/v1/web/search?q={quote(query)}"
        headers = {
            "Accept": "application/json",
            "X-Subscription-Token": os.environ["BRAVE_API_KEY"],
        }
        try:
            resp = await http.get(url, group="default", headers=headers, accept_statuses=(200,))
            if resp.status != 200:
                r.hits.append(Hit("Brave", Status.UNKNOWN, Tier.INFO, None, f"HTTP {resp.status}"))
                return
            data = json.loads(resp.text)
            results = data.get("web", {}).get("results", [])
            top = "; ".join(x.get("title", "") for x in results[:5])
            r.hits.append(Hit(f"Brave: {len(results)} page-1", Status.AVAILABLE, Tier.INFO,
                              None, top, extra={"count": len(results)}))
        except Exception as exc:  # noqa: BLE001
            r.hits.append(Hit("Brave", Status.UNKNOWN, Tier.INFO, None, f"{type(exc).__name__}"))
