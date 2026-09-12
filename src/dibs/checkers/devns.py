"""Developer namespaces: GitHub, npm (package + scope), PyPI, crates.io, Docker
Hub, Packagist. Existence check per handle. 200/exists => TAKEN, 404 => AVAILABLE,
else UNKNOWN. Each checker runs over every handle in the candidate."""

from __future__ import annotations

import json
import os
import re

from ..config import Config
from ..http import Http
from ..models import CheckResult, Hit, Status, Tier
from .base import BaseChecker


async def _simple(http: Http, url: str, group: str, headers=None) -> tuple[int, str]:
    resp = await http.get(url, group=group, headers=headers, accept_statuses=(200, 404))
    return resp.status, resp.text


def _pkg_hit(label: str, status: int, url: str, tier: Tier = Tier.MEDIUM) -> Hit:
    if status == 200:
        return Hit(label, Status.TAKEN, tier, url, "exists")
    if status == 404:
        return Hit(label, Status.AVAILABLE, tier)
    return Hit(label, Status.UNKNOWN, tier, detail=f"HTTP {status}")


class GithubChecker(BaseChecker):
    name = "github"
    columns = ["GitHub"]

    async def check(self, candidate, http, config):
        r = CheckResult(self.name, "GitHub", candidate)
        headers = {"Accept": "application/vnd.github+json"}
        token = os.environ.get("GITHUB_TOKEN")
        if token:
            headers["Authorization"] = f"Bearer {token}"
        for h in candidate.handle_list():
            page = f"https://github.com/{h}"
            try:
                status, _ = await _simple(http, f"https://api.github.com/users/{h}",
                                          "github", headers)
            except Exception as exc:  # noqa: BLE001
                r.errors.append(f"{h}: {type(exc).__name__}: {exc}")
                continue
            if status == 200:
                r.hits.append(Hit(f"github.com/{h}", Status.TAKEN, Tier.MEDIUM, page,
                                  "user or org exists"))
            elif status == 404:
                r.hits.append(Hit(f"github.com/{h}", Status.AVAILABLE, Tier.MEDIUM,
                                  detail="reserved/renamed names not detectable"))
            else:
                r.errors.append(f"{h}: GitHub HTTP {status} (rate limit; set GITHUB_TOKEN)")
        return [r]


class NpmChecker(BaseChecker):
    name = "npm"
    columns = ["npm"]

    async def check(self, candidate, http, config):
        r = CheckResult(self.name, "npm", candidate)
        for h in candidate.handle_list():
            try:
                pkg, _ = await _simple(http, f"https://registry.npmjs.org/{h}", "default")
                scope, _ = await _simple(http, f"https://registry.npmjs.org/-/org/{h}/user",
                                         "default")
            except Exception as exc:  # noqa: BLE001
                r.errors.append(f"{h}: {type(exc).__name__}: {exc}")
                continue
            r.hits.append(_pkg_hit(f"npm package {h}", pkg,
                                   f"https://www.npmjs.com/package/{h}"))
            r.hits.append(_pkg_hit(f"npm scope @{h}", scope, f"https://www.npmjs.com/org/{h}"))
        return [r]


class PypiChecker(BaseChecker):
    name = "pypi"
    columns = ["PyPI"]

    async def check(self, candidate, http, config):
        r = CheckResult(self.name, "PyPI", candidate)
        for h in candidate.handle_list():
            norm = re.sub(r"[-_.]+", "-", h).strip("-")  # PEP 503
            try:
                status, _ = await _simple(http, f"https://pypi.org/pypi/{norm}/json", "default")
            except Exception as exc:  # noqa: BLE001
                r.errors.append(f"{h}: {type(exc).__name__}: {exc}")
                continue
            r.hits.append(_pkg_hit(f"PyPI {norm}", status, f"https://pypi.org/project/{norm}/",
                                   tier=Tier.SOFT))
        return [r]


class CratesChecker(BaseChecker):
    name = "crates"
    columns = ["crates"]

    def requires(self, config: Config) -> str | None:
        if not config.contact:
            return "crates.io requires an identifying User-Agent (set DIBS_CONTACT)"
        return None

    async def check(self, candidate, http, config):
        r = CheckResult(self.name, "crates", candidate)
        for h in candidate.handle_list():
            try:
                status, _ = await _simple(http, f"https://crates.io/api/v1/crates/{h}", "crates")
            except Exception as exc:  # noqa: BLE001
                r.errors.append(f"{h}: {type(exc).__name__}: {exc}")
                continue
            if status not in (200, 404):
                r.errors.append(f"{h}: crates.io HTTP {status}")
                continue
            r.hits.append(_pkg_hit(f"crates.io {h}", status,
                                   f"https://crates.io/crates/{h}", tier=Tier.SOFT))
        return [r]


class DockerhubChecker(BaseChecker):
    name = "dockerhub"
    columns = ["Docker"]

    async def check(self, candidate, http, config):
        r = CheckResult(self.name, "Docker", candidate)
        for h in candidate.handle_list():
            page = f"https://hub.docker.com/u/{h}"
            try:
                # 200 = user, 308 -> orgs (also exists), 404 = free.
                resp = await http.get(f"https://hub.docker.com/v2/users/{h}", group="default",
                                      accept_statuses=(200, 301, 302, 308, 404))
            except Exception as exc:  # noqa: BLE001
                r.errors.append(f"{h}: {type(exc).__name__}: {exc}")
                continue
            if resp.status in (200, 301, 302, 308):
                r.hits.append(Hit(f"Docker Hub {h}", Status.TAKEN, Tier.SOFT, page,
                                  "namespace exists"))
            elif resp.status == 404:
                r.hits.append(Hit(f"Docker Hub {h}", Status.AVAILABLE, Tier.SOFT))
            else:
                r.errors.append(f"{h}: Docker Hub HTTP {resp.status}")
        return [r]


class PackagistChecker(BaseChecker):
    name = "packagist"
    columns = ["Packagist"]

    def requires(self, config: Config) -> str | None:
        if not config.contact:
            return "Packagist asks for a contact in the User-Agent (set DIBS_CONTACT)"
        return None

    async def check(self, candidate, http, config):
        r = CheckResult(self.name, "Packagist", candidate)
        for h in candidate.handle_list():
            url = f"https://packagist.org/packages/list.json?vendor={h}"
            try:
                resp = await http.get(url, group="packagist", accept_statuses=(200,))
            except Exception as exc:  # noqa: BLE001
                r.errors.append(f"{h}: {type(exc).__name__}: {exc}")
                continue
            if resp.status != 200:
                r.errors.append(f"{h}: Packagist HTTP {resp.status}")
                continue
            try:
                names = json.loads(resp.text).get("packageNames", [])
            except ValueError:
                r.errors.append(f"{h}: unparseable Packagist response")
                continue
            page = f"https://packagist.org/packages/{h}/"
            if names:
                r.hits.append(Hit(f"Packagist vendor {h}", Status.TAKEN, Tier.SOFT, page,
                                  f"{len(names)} package(s)"))
            else:
                r.hits.append(Hit(f"Packagist vendor {h}", Status.AVAILABLE, Tier.SOFT))
        return [r]
