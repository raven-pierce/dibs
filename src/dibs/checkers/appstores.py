"""App stores. Apple via iTunes Search (ca + us); Google Play via the search page's
AF_initDataCallback blobs (regex fallback). Both fuzzy, so a hit requires a title
match: exact = TAKEN(medium), whole-word = TAKEN(soft)."""

from __future__ import annotations

import json
import re

from ..models import CheckResult, Hit, Status, Tier, normalize_phrase
from .base import BaseChecker

ITUNES = "https://itunes.apple.com/search?term={term}&entity=software&country={cc}&limit=25"
PLAY = "https://play.google.com/store/search?q={q}&c=apps&hl=en&gl=CA"


def _match_tier(target: str, title: str) -> Tier | None:
    t_norm = normalize_phrase(title)
    if t_norm == target:
        return Tier.MEDIUM
    if re.search(rf"\b{re.escape(target)}\b", t_norm):
        return Tier.SOFT
    return None


class AppStoreChecker(BaseChecker):
    name = "appstore"
    columns = ["Apple"]

    async def check(self, candidate, http, config):
        r = CheckResult(self.name, "Apple", candidate)
        target = normalize_phrase(candidate.phrase)
        seen = False
        for cc in ("ca", "us"):
            url = ITUNES.format(term=candidate.phrase.replace(" ", "+"), cc=cc)
            try:
                resp = await http.get(url, group="appstore", accept_statuses=(200,))
            except Exception as exc:  # noqa: BLE001
                r.errors.append(f"{cc}: {type(exc).__name__}: {exc}")
                continue
            try:
                results = json.loads(resp.text).get("results", [])
            except ValueError:
                r.errors.append(f"{cc}: unparseable iTunes response")
                continue
            for app in results:
                tier = _match_tier(target, app.get("trackName", ""))
                if tier is None:
                    continue
                seen = True
                r.hits.append(Hit(
                    f"{app.get('trackName')} ({cc.upper()})",
                    Status.TAKEN, tier, app.get("trackViewUrl"),
                    f"by {app.get('artistName', '?')} | {app.get('bundleId', '')}",
                ))
        if not seen and not r.errors:
            r.hits.append(Hit(f"{candidate.phrase} (App Store)", Status.AVAILABLE, Tier.SOFT))
        return [r]


class GooglePlayChecker(BaseChecker):
    name = "googleplay"
    columns = ["Play"]

    async def check(self, candidate, http, config):
        r = CheckResult(self.name, "Play", candidate)
        target = normalize_phrase(candidate.phrase)
        url = PLAY.format(q=candidate.phrase.replace(" ", "+"))
        try:
            resp = await http.get(url, group="googleplay", accept_statuses=(200,))
        except Exception as exc:  # noqa: BLE001
            r.errors.append(f"{type(exc).__name__}: {exc}")
            return [r]
        if resp.status != 200:
            r.errors.append(f"Play HTTP {resp.status}")
            return [r]
        apps = _parse_play(resp.text)
        if apps is None:
            path = http.dump_raw("googleplay", candidate.slug, resp.text)
            r.errors.append(f"could not parse Play search HTML, saved to {path}")
            return [r]
        seen = False
        for pkg, title in apps:
            tier = _match_tier(target, title)
            if tier is None:
                continue
            seen = True
            r.hits.append(Hit(
                f"{title}", Status.TAKEN, tier,
                f"https://play.google.com/store/apps/details?id={pkg}", pkg,
            ))
        if not seen:
            r.hits.append(Hit(f"{candidate.phrase} (Play)", Status.AVAILABLE, Tier.SOFT))
        return [r]


def _parse_play(html: str) -> list[tuple[str, str]] | None:
    """Extract (package_id, title) pairs from AF_initDataCallback blobs.

    Defensive: scans every blob for lists shaped [<package-id>, 7] and reads the
    sibling title, then falls back to a details?id= regex. Returns None only when
    even the fallback finds nothing (treated as a parse failure worth inspecting).
    """
    pkg_re = re.compile(r"^[a-zA-Z][\w.]+\.[\w.]+$")
    found: dict[str, str] = {}

    for blob in re.findall(r"AF_initDataCallback\((\{.*?\})\);", html, re.DOTALL):
        m = re.search(r"data:\s*(\[.*?\])\s*,\s*sideChannel", blob, re.DOTALL)
        if not m:
            continue
        try:
            data = json.loads(m.group(1))
        except ValueError:
            continue
        _walk(data, pkg_re, found)

    if not found:
        # Fallback: pair each details?id= with the nearest following title-ish text.
        ids = re.findall(r"/store/apps/details\?id=([\w.]+)", html)
        for pkg in ids:
            found.setdefault(pkg, "")
        if not ids:
            return None
    return list(found.items())


def _walk(node, pkg_re, found: dict) -> None:
    if isinstance(node, list):
        # App node: node[0] == [<package-id>, 7]; the title is a sibling string.
        # Play's exact index shifts between layouts, so scan for the first plausible
        # title string rather than hardcode node[3].
        if (
            len(node) >= 2
            and isinstance(node[0], list)
            and len(node[0]) >= 2
            and isinstance(node[0][0], str)
            and node[0][1] == 7
            and pkg_re.match(node[0][0])
        ):
            title = ""
            for sib in node[1:]:
                if (
                    isinstance(sib, str)
                    and sib
                    and "/" not in sib
                    and any(c.isalpha() for c in sib)
                ):
                    title = sib
                    break
            found[node[0][0]] = title
        # Bare [<package-id>, 7] anywhere: register the id even without a title.
        if len(node) >= 2 and isinstance(node[0], str) and node[1] == 7 and pkg_re.match(node[0]):
            found.setdefault(node[0], "")
        for item in node:
            _walk(item, pkg_re, found)
    elif isinstance(node, dict):
        for value in node.values():
            _walk(value, pkg_re, found)
