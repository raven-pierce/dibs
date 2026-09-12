"""Social handles. Checks only platforms distinguishable with an honest bot
User-Agent and no login/JS: X, YouTube, Mastodon.social, Bluesky. Platforms that
need a browser UA or return identical shells become manual-link notes (INFO, never
scored). Runs over every handle in the candidate."""

from __future__ import annotations

from ..models import CheckResult, Hit, Status, Tier
from .base import BaseChecker

MANUAL = {
    "Reddit": "https://www.reddit.com/user/{s}",
    "LinkedIn": "https://www.linkedin.com/company/{s}",
    "Instagram": "https://www.instagram.com/{s}/",
    "TikTok": "https://www.tiktok.com/@{s}",
    "Threads": "https://www.threads.net/@{s}",
    "Twitch": "https://www.twitch.tv/{s}",
    "Facebook": "https://www.facebook.com/{s}",
}


class SocialChecker(BaseChecker):
    name = "social"
    columns = ["Social"]

    async def check(self, candidate, http, config):
        r = CheckResult(self.name, "Social", candidate)
        for slug in candidate.handle_list():
            await self._x(slug, http, r)
            await self._youtube(slug, http, r)
            await self._mastodon(slug, http, r)
            await self._bluesky(slug, http, r)
            for platform, tmpl in MANUAL.items():
                r.hits.append(Hit(
                    f"{platform}/{slug} (manual check)", Status.UNKNOWN, Tier.INFO,
                    tmpl.format(s=slug), "not reliably checkable without login/JS",
                ))
        return [r]

    async def _x(self, slug, http, r):
        url = f"https://x.com/{slug}"
        try:
            resp = await http.get(url, group="default", accept_statuses=(200, 404))
        except Exception as exc:  # noqa: BLE001
            r.hits.append(Hit(f"x.com/{slug}", Status.UNKNOWN, Tier.SOFT, url,
                              f"{type(exc).__name__}"))
            return
        if resp.status == 200:
            r.hits.append(Hit(f"x.com/{slug}", Status.TAKEN, Tier.SOFT, url, "best-effort"))
        elif resp.status == 404:
            r.hits.append(Hit(f"x.com/{slug}", Status.AVAILABLE, Tier.SOFT, detail="best-effort"))
        else:
            r.hits.append(Hit(f"x.com/{slug}", Status.UNKNOWN, Tier.SOFT, url,
                              f"HTTP {resp.status}"))

    async def _youtube(self, slug, http, r):
        url = f"https://www.youtube.com/@{slug}"
        try:
            resp = await http.get(url, group="default", accept_statuses=(200, 404))
        except Exception as exc:  # noqa: BLE001
            r.hits.append(Hit(f"youtube.com/@{slug}", Status.UNKNOWN, Tier.SOFT, url,
                              f"{type(exc).__name__}"))
            return
        status = {200: Status.TAKEN, 404: Status.AVAILABLE}.get(resp.status, Status.UNKNOWN)
        r.hits.append(Hit(f"youtube.com/@{slug}", status, Tier.SOFT,
                          url if status != Status.AVAILABLE else None))

    async def _mastodon(self, slug, http, r):
        url = f"https://mastodon.social/api/v1/accounts/lookup?acct={slug}"
        page = f"https://mastodon.social/@{slug}"
        try:
            resp = await http.get(url, group="default", accept_statuses=(200, 404))
        except Exception as exc:  # noqa: BLE001
            r.hits.append(Hit(f"mastodon.social/@{slug}", Status.UNKNOWN, Tier.SOFT, page,
                              f"{type(exc).__name__}"))
            return
        status = {200: Status.TAKEN, 404: Status.AVAILABLE}.get(resp.status, Status.UNKNOWN)
        r.hits.append(Hit(f"mastodon.social/@{slug}", status, Tier.SOFT,
                          page if status == Status.TAKEN else None))

    async def _bluesky(self, slug, http, r):
        url = (
            "https://public.api.bsky.app/xrpc/com.atproto.identity.resolveHandle"
            f"?handle={slug}.bsky.social"
        )
        try:
            resp = await http.get(url, group="default", accept_statuses=(200, 400))
        except Exception as exc:  # noqa: BLE001
            r.hits.append(Hit(f"{slug}.bsky.social", Status.UNKNOWN, Tier.SOFT, None,
                              f"{type(exc).__name__}"))
            return
        if resp.status == 200:
            r.hits.append(Hit(f"{slug}.bsky.social", Status.TAKEN, Tier.SOFT,
                              f"https://bsky.app/profile/{slug}.bsky.social"))
        elif resp.status == 400:
            r.hits.append(Hit(f"{slug}.bsky.social", Status.AVAILABLE, Tier.SOFT))
        else:
            r.hits.append(Hit(f"{slug}.bsky.social", Status.UNKNOWN, Tier.SOFT, None,
                              f"HTTP {resp.status}"))
