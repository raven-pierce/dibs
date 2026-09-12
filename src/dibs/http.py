"""HTTP core: one async client, global concurrency cap, per-group rate limiting,
retries with backoff honouring Retry-After, on-disk cache, daily budgets, and a
port-43 WHOIS helper. Every request carries the dibs User-Agent.
"""

from __future__ import annotations

import asyncio
import email.utils
import time
from pathlib import Path

import httpx

from . import __version__
from .cache import Cache, CachedResponse
from .config import Config


class BudgetExceeded(Exception):
    """Raised when a group's daily request budget is spent."""


class RateLimiter:
    """Minimum interval between request starts, per group."""

    def __init__(self, interval: float):
        self.interval = interval
        self._lock = asyncio.Lock()
        self._last = 0.0

    async def wait(self) -> None:
        async with self._lock:
            now = time.monotonic()
            gap = self.interval - (now - self._last)
            if gap > 0:
                await asyncio.sleep(gap)
            self._last = time.monotonic()


def _retry_after_seconds(value: str) -> float | None:
    value = value.strip()
    if value.isdigit():
        return float(value)
    parsed = email.utils.parsedate_to_datetime(value)
    if parsed is None:
        return None
    delta = parsed.timestamp() - time.time()
    return max(delta, 0.0)


class Http:
    def __init__(self, config: Config, use_cache: bool = True):
        self.config = config
        self.use_cache = use_cache
        self.raw_dir = Path(".cache/raw")
        self.cache = Cache(Path(".cache/dibs.sqlite"))
        self._client = httpx.AsyncClient(
            follow_redirects=True,
            timeout=config.data["timeouts"]["seconds"],
            headers={"User-Agent": config.user_agent(__version__)},
        )
        self._sem = asyncio.Semaphore(config.data["concurrency"]["global"])
        self._limiters: dict[str, RateLimiter] = {}

    def _limiter(self, group: str) -> RateLimiter:
        if group not in self._limiters:
            self._limiters[group] = RateLimiter(self.config.rate_interval(group))
        return self._limiters[group]

    async def request(
        self,
        method: str,
        url: str,
        *,
        group: str,
        headers: dict | None = None,
        content: str | None = None,
        json_body: dict | None = None,
        ttl_seconds: float | None = None,
        accept_statuses: tuple[int, ...] = (200, 404),
    ) -> CachedResponse:
        """Perform a request through cache/rate-limit/retry/budget.

        `accept_statuses` are treated as final (cached, returned). Any other status
        is retried; a status still outside the set after retries is returned as-is
        so the caller can decide (usually UNKNOWN).
        """
        if json_body is not None:
            import json as _json

            content = _json.dumps(json_body, separators=(",", ":"))
            headers = {**(headers or {}), "Content-Type": "application/json"}

        if ttl_seconds is None:
            ttl_seconds = self.config.data["cache"]["ttl_days"] * 86400

        if self.use_cache and ttl_seconds > 0:
            cached = await self.cache.get(method, url, content, ttl_seconds)
            if cached is not None:
                return cached

        allowed = await self.cache.budget_check_and_increment(
            group, self.config.daily_budget(group)
        )
        if not allowed:
            raise BudgetExceeded(f"daily budget spent for group '{group}'")

        retries = self.config.data["retries"]
        base, cap, max_tries = (
            retries["base_seconds"],
            retries["cap_seconds"],
            retries["max"],
        )
        last: httpx.Response | None = None
        for attempt in range(max_tries + 1):
            await self._limiter(group).wait()
            async with self._sem:
                resp = await self._client.request(
                    method, url, headers=headers, content=content
                )
            last = resp
            if resp.status_code in accept_statuses or attempt == max_tries:
                break
            if resp.status_code == 429 or resp.status_code >= 500:
                delay = min(base * (2**attempt), cap)
                ra = resp.headers.get("Retry-After")
                if ra:
                    parsed = _retry_after_seconds(ra)
                    if parsed is not None:
                        delay = min(max(parsed, delay), cap)
                await asyncio.sleep(delay)
                continue
            break  # non-retryable status outside accept set

        assert last is not None
        result = CachedResponse(
            last.status_code, dict(last.headers), last.text, from_cache=False
        )
        # Cache only stable outcomes, never a transient failure we gave up on.
        if self.use_cache and (
            last.status_code in accept_statuses
            or (last.status_code < 500 and last.status_code != 429)
        ):
            await self.cache.put(
                method, url, content, last.status_code, dict(last.headers), last.text
            )
        return result

    async def get(self, url: str, **kw) -> CachedResponse:
        return await self.request("GET", url, **kw)

    async def post(self, url: str, **kw) -> CachedResponse:
        return await self.request("POST", url, **kw)

    async def whois(self, host: str, query: str, *, group: str = "whois") -> str:
        """Port-43 WHOIS lookup, cached and rate-limited like HTTP."""
        cache_url = f"whois://{host}/{query}"
        ttl = self.config.data["cache"]["ttl_days"] * 86400
        if self.use_cache:
            cached = await self.cache.get("WHOIS", cache_url, None, ttl)
            if cached is not None:
                return cached.text

        allowed = await self.cache.budget_check_and_increment(
            group, self.config.daily_budget(group)
        )
        if not allowed:
            raise BudgetExceeded(f"daily budget spent for group '{group}'")

        await self._limiter(group).wait()
        async with self._sem:
            reader, writer = await asyncio.open_connection(host, 43)
            writer.write((query + "\r\n").encode())
            await writer.drain()
            chunks = []
            while True:
                data = await reader.read(4096)
                if not data:
                    break
                chunks.append(data)
            writer.close()
            await writer.wait_closed()
        text = b"".join(chunks).decode("utf-8", "replace")
        if self.use_cache:
            await self.cache.put("WHOIS", cache_url, None, 200, {}, text)
        return text

    def dump_raw(self, source: str, slug: str, body: str) -> Path:
        """Persist an unparseable response for inspection. Used on parse failure."""
        self.raw_dir.mkdir(parents=True, exist_ok=True)
        ts = time.strftime("%Y%m%d-%H%M%S")
        path = self.raw_dir / f"{source}-{slug}-{ts}.txt"
        path.write_text(body, encoding="utf-8")
        return path

    async def aclose(self) -> None:
        await self._client.aclose()
        self.cache.close()
