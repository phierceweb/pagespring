# pagespring

[![PyPI](https://img.shields.io/pypi/v/pagespring)](https://pypi.org/project/pagespring/)

Acquire and normalize online documentation into clean, convertible source
files — the **acquisition front-end to
[pagespeak](https://github.com/phierceweb/pagespeak)**.

Point it at a manual's URL. A *pattern* recognizes the source type, *acquires*
the raw pages (stdlib `urllib`), and *normalizes* them into ONE clean
HTML/markdown file with absolute asset URLs under `incoming/<slug>/`. That clean
file is the deliverable; converting it into the finished RAG corpus is a separate
step (pagespeak) that consumes `incoming/` on its own — pagespring never runs it.

Lean by design: [`pf-core[cli]`](https://github.com/phierceweb/pf-core) ([PyPI](https://pypi.org/project/pf-core/)) + `beautifulsoup4`, stdlib fetch, no ML stack.

## Intended use

pagespring is for **publicly available documentation** — vendor manuals, help
centers, open textbooks, API specs. It fetches only what the source serves to
any reader: there is no login/session handling, no paywall traversal, and no
bot-detection evasion. It is a **polite client**: it identifies itself with a
`pagespring/<version>` User-Agent (`PAGESPRING_UA` overrides it), honors
`429 Retry-After`, backs off on server errors, paces crawl requests, and caps
crawl sizes.

It is a *user-invoked, one-manual-at-a-time* archiver — closer to "Save Page
As" than to an autonomous crawler — so it does not consult `robots.txt`
(which governs bots that discover URLs on their own; you supply the URL).
Before mirroring a site, check its terms of use. What you may do with the
acquired copy (personal RAG corpus, internal search, redistribution) is
governed by the source's license — the deliverable under `incoming/` stays on
your machine, and nothing is re-published by this tool.

## Install

```bash
pip install pagespring
```

## Quick start

```bash
pagespring ingest https://docs.tableplus.com   # acquire + normalize → incoming/tableplus/
pagespring renormalize <slug>                   # replay normalize from kept raw/ — no re-crawl
pagespring refresh --all                        # re-check every manual against its source
pagespring audit --all                          # $0 sanity checks on everything staged
pagespring localize <slug>                      # pull a deliverable's images later (resumable; --all)
pagespring patterns                             # list the source patterns
pagespring classify <url>                       # which pattern handles a URL (no fetch)
pagespring status                               # what's been acquired
```

Deliverables land in `./incoming/<slug>/` under the directory you run from.

## Dev

```bash
bin/setup   # clone → venv + editable install with dev extras
bin/test    # pytest
bin/lint    # ruff check + ruff format --check + mypy (strict)
```

See [docs/usage.md](https://github.com/phierceweb/pagespring/blob/main/docs/usage.md) for the full command set and
[docs/architecture.md](https://github.com/phierceweb/pagespring/blob/main/docs/architecture.md) for the acquire → normalize flow
and how to add a new source pattern.
