"""Domain availability via RDAP (IANA bootstrap), WHOIS fallback for TLDs with no
RDAP (.co). 200 => TAKEN, 404 => AVAILABLE, else UNKNOWN; a 404 with a
"blocked"/"reserved" notice is TAKEN."""

from __future__ import annotations

import json

from ..config import Config
from ..http import BudgetExceeded, Http
from ..models import Candidate, CheckResult, Hit, Status, Tier
from .base import BaseChecker

BOOTSTRAP_URL = "https://data.iana.org/rdap/dns.json"


def build_domains(candidate: Candidate, config: Config) -> list[tuple[str, str, bool]]:
    """(fqdn, tld, is_primary) triples. Primary base's .com/.ca are the hard blockers.

    Explicit `domains`: each base across the TLD list, no prefix variants (the user
    already chose the prefixes). Derived: slug across TLDs, hyphen form for
    multi-word names, plus the configured prefix/suffix variants.
    """
    dom = config.data["domains"]
    primary = candidate.primary_base
    out: list[tuple[str, str, bool]] = []
    seen: set[str] = set()

    def add(fqdn: str, tld: str, is_primary: bool) -> None:
        if fqdn not in seen:
            seen.add(fqdn)
            out.append((fqdn, tld, is_primary))

    if candidate.domains:
        for base in candidate.domain_bases():
            for tld in dom["tlds"]:
                add(f"{base}.{tld}", tld, base == primary)
        return out

    slug = candidate.slug
    for tld in dom["tlds"]:
        add(f"{slug}.{tld}", tld, True)
    if candidate.is_multiword:
        for tld in dom.get("hyphen_tlds", []):
            add(f"{candidate.hyphen}.{tld}", tld, False)
    for text, position, tld in dom.get("variants", []):
        label = f"{text}{slug}" if position == "prefix" else f"{slug}{text}"
        add(f"{label}.{tld}", tld, False)
    return out


class RdapChecker(BaseChecker):
    name = "rdap"
    columns = [".com", ".ca", "domains"]
    slow = False

    def __init__(self) -> None:
        self._tld_map: dict[str, str] | None = None

    async def _bootstrap(self, http: Http, config: Config) -> dict[str, str]:
        if self._tld_map is not None:
            return self._tld_map
        ttl = config.data["cache"]["bootstrap_ttl_days"] * 86400
        resp = await http.get(BOOTSTRAP_URL, group="rdap", ttl_seconds=ttl)
        mapping: dict[str, str] = {}
        try:
            data = json.loads(resp.text)
            for entry in data.get("services", []):
                tlds, urls = entry[0], entry[1]
                base = urls[0].rstrip("/") + "/"
                for tld in tlds:
                    mapping[tld.lower()] = base
        except (ValueError, IndexError, KeyError):
            pass
        for tld, base in config.data["domains"].get("rdap_overrides", {}).items():
            mapping[tld.lower()] = base.rstrip("/") + "/"
        self._tld_map = mapping
        return mapping

    async def check(self, candidate, http, config):
        mapping = await self._bootstrap(http, config)
        whois_fallback = config.data["domains"].get("whois_fallback", {})
        com = CheckResult(self.name, ".com", candidate)
        ca = CheckResult(self.name, ".ca", candidate)
        other = CheckResult(self.name, "domains", candidate)

        for fqdn, tld, is_primary in build_domains(candidate, config):
            # Only the primary base's .com/.ca get their own columns and hard tier.
            if is_primary and tld == "com":
                target = com
            elif is_primary and tld == "ca":
                target = ca
            else:
                target = other
            hard = is_primary and tld in ("com", "ca")
            tier = Tier.HARD if hard else Tier.SOFT
            try:
                if tld in mapping:
                    hit = await self._rdap_lookup(fqdn, mapping[tld], tier, http)
                elif tld in whois_fallback:
                    hit = await self._whois_lookup(fqdn, whois_fallback[tld], tier, http)
                else:
                    hit = Hit(
                        fqdn,
                        Status.UNKNOWN,
                        tier,
                        detail="no RDAP endpoint or WHOIS fallback for this TLD",
                    )
            except BudgetExceeded as exc:
                hit = Hit(fqdn, Status.UNKNOWN, tier, detail=str(exc))
            except Exception as exc:  # noqa: BLE001 - any failure => UNKNOWN
                hit = Hit(fqdn, Status.UNKNOWN, tier, detail=f"{type(exc).__name__}: {exc}")
            target.hits.append(hit)
        return [com, ca, other]

    async def _rdap_lookup(self, fqdn: str, base: str, tier: Tier, http: Http) -> Hit:
        url = f"{base}domain/{fqdn}"
        resp = await http.get(
            url,
            group="rdap",
            headers={"Accept": "application/rdap+json"},
            accept_statuses=(200, 404),
        )
        verify = f"https://rdap.org/domain/{fqdn}"
        if resp.status == 200:
            created, registrar = _extract_registration(resp.text)
            detail = " ".join(
                p for p in [f"created {created}" if created else "", registrar] if p
            )
            return Hit(fqdn, Status.TAKEN, tier, url=verify, detail=detail.strip(),
                       extra={"created": created, "registrar": registrar})
        if resp.status == 404:
            if _looks_blocked(resp.text):
                return Hit(fqdn, Status.TAKEN, tier, url=verify,
                           detail="registry-blocked/reserved name")
            return Hit(fqdn, Status.AVAILABLE, tier)
        return Hit(fqdn, Status.UNKNOWN, tier, detail=f"RDAP HTTP {resp.status}")

    async def _whois_lookup(self, fqdn: str, host: str, tier: Tier, http: Http) -> Hit:
        text = await http.whois(host, fqdn)
        verify = f"https://who.is/whois/{fqdn}"
        upper = text.upper()
        if "NOT FOUND" in upper or "NO MATCH" in upper or "NO DATA FOUND" in upper:
            return Hit(fqdn, Status.AVAILABLE, tier, detail="whois: not found")
        created = _grep(text, "Creation Date:")
        registrar = _grep(text, "Registrar:")
        if created or registrar or "DOMAIN NAME" in upper:
            detail = " ".join(p for p in [
                f"created {created}" if created else "",
                f"registrar {registrar}" if registrar else "",
            ] if p)
            return Hit(fqdn, Status.TAKEN, tier, url=verify, detail=("whois: " + detail).strip())
        return Hit(fqdn, Status.UNKNOWN, tier, url=verify, detail="whois: unparseable response")


def _grep(text: str, prefix: str) -> str | None:
    for line in text.splitlines():
        line = line.strip()
        if line.lower().startswith(prefix.lower()):
            return line[len(prefix):].strip()
    return None


def _looks_blocked(body: str) -> bool:
    low = body.lower()
    return any(w in low for w in ("blocked", "reserved", "dpml", "premium"))


def _extract_registration(body: str) -> tuple[str | None, str | None]:
    try:
        data = json.loads(body)
    except ValueError:
        return None, None
    created = None
    for event in data.get("events", []):
        if event.get("eventAction") == "registration":
            created = event.get("eventDate")
    registrar = None
    for entity in data.get("entities", []):
        roles = entity.get("roles", [])
        if "registrar" in roles:
            for item in entity.get("vcardArray", [[], []])[1]:
                if item and item[0] == "fn":
                    registrar = item[3]
            registrar = registrar or entity.get("handle")
    return created, registrar
