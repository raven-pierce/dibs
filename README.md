# dibs

A command-line **knockout filter** for brand names. Give it candidates and it
reports where each is already taken: domains, US and Canadian trademarks, the
Canadian corporate registers, developer namespaces, app stores, social handles,
and a rough web footprint. Outputs a coloured table, per-name Markdown, a CSV,
and a self-contained HTML report.

> **dibs is a screening tool, not legal advice.** It drops hopeless names; it does
> not clear a name. Not a NUANS report, not a trademark clearance opinion. It
> reports what literally exists, with class and status, and leaves every judgment
> of confusion and risk to a qualified human. "Clear" means *these sources showed
> nothing*, never *safe to use*.

Every check is **TAKEN**, **AVAILABLE**, or **UNKNOWN**. A source that fails or
can't be checked reliably is UNKNOWN, never counted as available — a false "clear"
is the worst possible output.

## Install

Python 3.11+, using [uv](https://docs.astral.sh/uv/).

```bash
uv sync
uv run dibs --help
```

## Quick start

```bash
uv run dibs data refresh          # one-time: build the Corporations Canada index
uv run dibs check zephyr nimbus   # screen names
uv run dibs check -f names.txt    # or a .txt (one per line) / .csv (see below)
```

Outputs land in `./reports/`: `index.html`, `summary.csv`, one `<name>.md` each.
Every run is also appended to a local history; browse it with `dibs serve`.

## Dashboard

```bash
dibs serve            # http://127.0.0.1:8787
```

Each `dibs check` appends its results to `.cache/history.sqlite`. `dibs serve`
opens a localhost-only, read-only dashboard over that history: every screened
name ranked fewest-blockers-first, each name drilling into its run timeline and
every hit with a verify link. It renders live, so new runs appear without a
restart. It binds `127.0.0.1` only and never initiates searches itself.

## Flexible input (CSV)

A `.csv` gives each name different values per dimension. Columns, all optional
except `name`; blanks derive from `name`; unknown columns ignored; list cells are
pipe-separated:

Any column may hold several pipe-separated values (`legal` and `tm` included);
each is searched and the results merged, deduped.

| Column | Feeds | Example |
|---|---|---|
| `name` | label + default for the rest | `Kestrel` |
| `legal` | Corporations Canada, REQ | `Kestrel Labs\|Kestrel Technologies Inc.` |
| `tm` | USPTO, CIPO | `Kestrel\|Kestrel Cloud` |
| `domains` | domain bases (first is primary; each expanded across the TLD list, no prefix variants added) | `kestrel\|getkestrel` |
| `handles` | GitHub, npm, PyPI, crates, Docker, Packagist, social | `kestrellabs\|bykestrel` |

```csv
name,legal,tm,domains,handles
Kestrel,Kestrel Technologies Inc.,Kestrel,kestrel|getkestrel,kestrellabs|bykestrel
```

With no `domains`, the name derives its slug plus the configured prefix/suffix
variants (`bykestrel`, `getkestrel`, `kestrelhq`, …). With no `handles`, the
slug is used.

## What it checks

| Source | Column | Notes |
|---|---|---|
| Domains (RDAP) | `.com` `.ca` `domains` | IANA bootstrap per TLD; `.io` via override, `.co` via WHOIS. A 404 with a "blocked/reserved" notice is TAKEN. |
| USPTO | `USPTO` | **Undocumented** tmsearch JSON backend. Live/dead, Nice classes, goods, owner. Class 9/42 scored separately. |
| CIPO | `CIPO` | The JSON endpoint the public search form posts to; live 9/42 hits get a capped detail fetch for owner and goods. |
| Corporations Canada | `CorpCan` | Local index from federal open data. Live exact name = hard; dissolved/inactive shown as context, unscored. |
| Québec REQ | `REQ` | Local index from the imported open-data ZIP (names + enterprise status). Struck enterprises and former names shown as context, unscored. List the French legal name in `legal` (Quebec requires a French name). |
| GitHub / npm / PyPI / crates / Docker / Packagist | resp. | Namespace existence per handle. |
| Apple / Google Play | `Apple` `Play` | Title match only, never a non-empty result set. |
| Social | `Social` | X, YouTube, Mastodon.social, Bluesky (honest UA). Others are manual links. |
| Web footprint | `Web` | Result counts (Wikipedia, Hacker News, GitHub repos). Never a blocker. |

Colours: green = clear, yellow = partial/contains/adjacent, red = hard blocker,
dim = UNKNOWN. The `Web` column shows counts.

## Scoring

Taken hits add weight; names rank ascending (fewest blockers first), tie-broken by
hard count then unknown count. All weights and class lists are configurable.

| Tier | Weight | Examples |
|---|---|---|
| Hard | 10 | live exact mark in class 9/42; primary `.com`/`.ca`; exact corporate name |
| Medium | 3 | contains-match mark in 9/42; exact mark in an adjacent class; GitHub/npm; app-store exact title |
| Soft | 1 | other TLDs and variants; PyPI/crates/Docker/Packagist; social |
| Info | 0 | footprint counts, dead/inactive hits, manual notes |

**Status gating.** A hit only scores if it's live. Dead or expunged trademarks
(USPTO, CIPO), dissolved or inactive federal corporations, and struck Québec
enterprises or withdrawn former names are demoted to `Info`: shown in the reports
as context, never counted as blockers. Unknown status is treated as live
(conservative). The status vocabularies live in `[status]` in `dibs.toml`.

## Configuration

Copy `dibs.toml`, edit, pass `--config`, or keep it in the working directory.
Configurable: TLD list, domain variants, RDAP overrides, WHOIS fallbacks,
trademark class lists, weights, per-host rate limits, daily budgets, cache TTLs,
enabled checkers. Unset keys fall back to defaults in `src/dibs/config.py`.

## Environment variables

| Variable | Purpose |
|---|---|
| `DIBS_CONTACT` | Contact in the User-Agent (URL or mailbox you control). **Required** for crates.io, CIPO, USPTO, Packagist — they report UNKNOWN without it. Lets operators reach you instead of blocking you; don't fake it. |
| `GITHUB_TOKEN` | Optional. Lifts GitHub's rate limit (60/hr → 5,000/hr). |
| `BRAVE_API_KEY` | Optional. Adds the Brave web-footprint provider. |

Copy `.env.example` to `.env` and fill it in; `.env` is gitignored and loaded
automatically. Prefer it over putting `contact` in `dibs.toml`.

## Bulk-data indexes

Two Canadian registers have no usable search API, so dibs queries a local SQLite
index built from open data, under `.cache/data/` (gitignored).

- **Corporations Canada**: `dibs data refresh` downloads and indexes the federal
  active-corporation CSVs (Open Government Licence). `--include-dissolved` adds the
  dissolved set.
- **Québec REQ**: the open-data ZIP sits behind a browser challenge and the search
  form disallows automation, so download it manually from
  <https://www.donneesquebec.ca/recherche/dataset/registre-des-entreprises>, then
  `dibs req import <zip>`. The data is **CC BY-NC-SA 4.0 (non-commercial)**;
  screening your own names is personal use, the licence call is yours.

## Caching and politeness

Responses are cached in `.cache/dibs.sqlite` (method + URL + body, default 7-day
TTL; `--no-cache` bypasses). A global concurrency cap plus per-host rate limits
apply; government hosts get one request per two seconds and a daily budget
(default 400), after which remaining names go UNKNOWN. Retries use exponential
backoff on 429/5xx honouring `Retry-After`.

## USPTO and CIPO endpoints

The USPTO Developer Hub was decommissioned in 2026; no documented API searches
marks by text. dibs uses the JSON backend the public tmsearch UI itself calls, and
CIPO the endpoint its public form posts to. Both are treated like a scrape:
identifying User-Agent, rate limits, daily budget, and **loud failure** — a changed
response shape is saved to `.cache/raw/` and reported UNKNOWN, never a silent zero.

## Development

```bash
uv run pytest          # offline, against recorded fixtures
uv run pytest --live   # also hit real endpoints
uv run ruff check
```

## Out of scope

NUANS (paid, no API); judging trademark confusion or similarity; social platforms
that need a logged-in browser (listed as manual links).

## License

GNU General Public License v3.0 or later (GPL-3.0-or-later). See [LICENSE](LICENSE).
