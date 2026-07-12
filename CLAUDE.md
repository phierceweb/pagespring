# pagespring

Orientation for AI assistants. Keep short.

## Status

v0.1 — a standalone pf-core consumer: `src/` layout, console-script CLI, and
ruff + mypy-strict + pytest gates.

## Docs

| Doc | What |
|---|---|
| [README.md](README.md) | What it is + quick start |
| [docs/usage.md](docs/usage.md) | Full command set |
| [docs/architecture.md](docs/architecture.md) | acquire → normalize flow + adding a pattern |

## What this is

**pagespring acquires + normalizes online software manuals** — the acquisition
front-end to **pagespeak**. Point it at a manual's URL; a *pattern* recognizes
the source, *acquires* the raw pages (stdlib `urllib` via `pagespring.http`),
and *normalizes* them into ONE clean HTML/markdown file with absolute asset URLs
under `incoming/<slug>/`. That clean file is the deliverable — pagespring stops
there.

- `src/pagespring/` — the package: a lean **pf-core[cli] consumer** +
  `beautifulsoup4` + `pyyaml`, stdlib fetch, no ML stack.
- `incoming/` — the deliverable: one `incoming/<slug>/` per manual, each holding
  the clean file plus a `manifest.json` (source URL, pattern, `convert_recipe`,
  `sha256`, …) that makes the hand-off self-describing. Gitignored (large,
  regenerable). **You move these to pagespeak by hand** — there is no automation
  between pagespring and pagespeak.
- `tests/` — mirrors `src/pagespring/` 1:1; mock `pagespring.http`, never hit
  the network.

Conversion into the RAG corpus is **pagespeak's** job (a separate project,
downstream of `incoming/`); pagespring neither runs nor imports it.

## Using it

```
bin/run ingest <url>   # acquire + normalize → incoming/<slug>/ (the deliverable)
bin/run --help         # all commands (patterns, classify, status) + flags — live, don't mirror here
```

The registered patterns are **live** via `bin/run patterns`; check a URL's
routing with `bin/run classify <url>`. Don't enumerate the patterns here — a
list in this file drifts.

## Adding a new source shape

Author `src/pagespring/patterns/<name>.py` (Pattern protocol: `name`,
`convert_recipe`, `match`, `acquire`, `normalize`), register it in `registry.py`
(host-specific patterns first, extension patterns next, `docs_probe` last —
first match wins), and add `tests/test_<name>.py` mocking `pagespring.http`. Fetching
is stdlib `urllib` via `pagespring.http` — never add `httpx`/`requests`. Verify
by reading the clean file in `incoming/`, not by running pagespeak. Each pattern
*declares* its intended pagespeak `convert_recipe` flags as a hint for that
downstream step; pagespring never runs them.

## Dev

```
bin/setup   # venv + editable install ([dev]: pytest, ruff, mypy, pre-commit)
bin/test    # pytest
bin/lint    # ruff check + ruff format --check + mypy (strict) — same gates as CI
```

pf-core installs from PyPI (`pf-core[cli]~=0.4.1`). For active pf-core co-dev
with a local checkout, install it editable over the pin:
`.venv/bin/pip install -e ../pf-core[cli]`.

## Rules

Project rules live in `.ai/rules/` (pf-core's canonical set — code-style,
layering, error-handling, config-driven, logging, scope, git-safety, docs-sync,
…). `bin/setup` symlinks `.claude/rules` and `.cursor/rules` to them. Read them.

## Related projects

- [pagespeak](https://github.com/phierceweb/pagespeak) — the converter
  pagespring feeds (consumes `incoming/`).
- [pf-core](https://github.com/phierceweb/pf-core) — the framework.
