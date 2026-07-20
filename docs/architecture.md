# Architecture

The acquire → normalize flow, the Pattern contract that makes it extensible, and how to add a new source.

pagespring *acquires*; pagespeak *converts*. This doc covers the acquisition half only — everything upstream of the clean file in `incoming/<slug>/`.

---

## Table of Contents

- [The pipeline](#the-pipeline)
- [The manifest](#the-manifest)
- [The Pattern contract](#the-pattern-contract)
- [Classification order](#classification-order)
- [Fetching](#fetching)
- [Adding a new pattern](#adding-a-new-pattern)

## The pipeline

One command, `ingest`, drives `run_ingest` in `orchestrate.py`:

1. **classify** — `registry.classify(url)` walks the registered patterns and returns the first whose `match()` accepts the URL (or raises `NoPatternError`).
2. **acquire** — the pattern downloads the raw pages into a temp workdir and returns an `AcquireResult` (raw dir, source kind, slug, page count, and an optional human `title` for the deliverable heading — falls back to the slug).
3. **normalize** — the pattern turns the raw pages into ONE clean file (`.html` / `.md` / `.pdf`) with **absolute** asset URLs.
4. **stage** — `run_ingest` clears `incoming/<slug>/`, copies the clean file in, and writes a `manifest.json` beside it (see [The manifest](#the-manifest)).

All work happens in a temp dir; only the final clean file (plus its `manifest.json`, and optionally `raw/`, `images/`) lands in `incoming/`. Empty normalize output raises `EmptyOutputError` *before* the staging clear, so a bad re-crawl never destroys a prior good deliverable.

**Image localization is a decoupled step.** Because normalize leaves asset URLs **absolute**, a no-image deliverable is already complete (pagespeak can fetch those URLs at convert time). Images can be pulled either inline (`ingest --download-images`) or after the fact (`pagespring localize <slug>` → `orchestrate.localize_images`), both downloading into `incoming/<slug>/images/` and re-pointing refs. `localize` is **resumable** — it re-points each image as it lands and checkpoints the file — so a book whose image set exceeds one run's budget is finished by re-running until none remain.

## The manifest

Every staged deliverable gets a sibling `incoming/<slug>/manifest.json` (`manifest.py`) — the provenance record that makes the pagespeak hand-off self-describing:

```json
{
  "schema_version": 1,
  "pagespring_version": "0.1.0",
  "source_url": "https://docs.tableplus.com/",
  "pattern": "docs_probe",
  "slug": "tableplus",
  "kind": "markdown",
  "deliverable": "tableplus.md",
  "convert_recipe": ["--split-sections"],
  "pages": 62,
  "bytes": 123456,
  "sha256": "…",
  "images": 0,
  "ingested_at": "2026-06-14T17:23:01Z"
}
```

`sha256` is the hash of the deliverable **as `normalize()` produced it** (before `--download-images` re-points any refs) — so on the default path it matches the on-disk file, and it stays stable as the content's identity regardless of image-localization.

That hash is what `ingest --if-changed` compares against: a re-crawl that normalizes to the same bytes leaves the existing `incoming/<slug>/` untouched (file, images, mtime) and reports `unchanged`. The crawl itself still runs — the slug is only known after `acquire`, so `--if-changed` saves the re-stage and churn, not the network round-trip. `status` reads these manifests; legacy dirs without one fall back to the deliverable file's own facts.

## The Pattern contract

A pattern is one source type's knowledge, as a class implementing the `Pattern` protocol (`base.py`). Five members:

- `name` — the registry id (`apple_help`, `gitbook`, …).
- `convert_recipe` — the extra `pagespeak convert` flags this source's output wants. **A hint for the downstream step; pagespring never runs it.**
- `match(url)` — cheap host/path check, returns bool.
- `acquire(url, workdir)` — download raw pages, return an `AcquireResult`.
- `normalize(acq, workdir)` — merge/clean into one file, return its path.

The `acquire`/`normalize` split is the key design rule: `acquire` holds all network and source-shape knowledge; `normalize` is pure transformation over local files. That split is what lets tests mock `pagespring.http` and exercise both halves with no network.

## Classification order

First match wins, so registration order in `registry.py` is load-bearing. Four tiers, cheapest/most-specific first:

1. **Host-specific** patterns (e.g. `apple_help`, `readthedocs`, `github_markdown`) — they recognise a known host. RTD projects without PDF builds fall back to a Sphinx crawl instead of failing.
2. **Extension / content** patterns — `api_spec` (a `.json`/`.yaml`/`.yml` extension, or an `openapi`/`swagger`/`postman` token in the last path segment), then `pdf_url`, `archive_download` — so a spec or a `.pdf` routes here rather than falling through to a broader pattern below.
3. **`gitbook`**, narrowed to its own hosting (`*.gitbook.io`) — custom-domain GitBook sites carry no URL tell, so they fall through to `docs_probe` instead.
4. **`docs_probe` last** — a content-probing catch-all that claims any http(s) URL nothing above it matched. Its `match` is nearly free (scheme check only); all the real classification work happens in `acquire`, which probes the base page in order — `<meta name="generator">` first, then fallback tells: `_static/` assets (Sphinx), a `search/search_index.json` (MkDocs), an `llms.txt` with per-page `.md` links (GitBook-style sites on custom domains, delegated back to the gitbook machinery so its image-proxy resolution still applies). A site none of these recognise raises `InvalidInputError` naming what was probed (CLI exit 2).

So `classify` reporting `docs_probe` means "will content-probe at acquire" — not a confirmed source type. `classify` alone therefore cannot prove a URL is unroutable; only `ingest` (or a direct read of `docs_probe.acquire`) can, since it's the tier that actually fetches and sniffs. `classify` returns `None` only for a non-web argument (a local file path or `file://` URL) — every http(s) URL has a match by the time `docs_probe` is reached.

`api_spec` and `docs_probe` are the two patterns that match on **content shape** rather than host, following the same precedent: a cheap `match`, then a content-sniffing `acquire`. `api_spec`'s `match` is the usual cheap path check, but OpenAPI-vs-Postman can't be told from a URL — so `acquire` fetches the file and *content-sniffs* it: an `openapi`/`swagger` key ⇒ OpenAPI, `info` + `item` ⇒ Postman, neither ⇒ a clean `InvalidInputError` (CLI exit 2). It emits `kind="markdown"`, rendering the spec's endpoints, params, and responses (or the collection's requests) into one file instead of crawling pages. It also accepts a **local file path**, since a spec is often behind a ReDoc/Swagger-UI "Download" button rather than at a stable URL.

Check a URL's routing with `pagespring classify <url>`; list what's registered with `pagespring patterns`.

## Fetching

All network I/O goes through `pagespring.http` (stdlib `urllib`). **Never add `httpx` or `requests`.** It provides an identifying `pagespring/<version>` User-Agent (`PAGESPRING_UA` overrides it for a source that mishandles tool UAs), status-aware retries (permanent 4xx fail fast; 429 honours `Retry-After`; 5xx and network errors back off), charset resolved from the response, and `polite_sleep()` between crawl requests. Patterns log-and-skip individual page failures rather than aborting a whole crawl, so one dead topic page doesn't lose the manual. See the README's *Intended use* section for the client-behavior stance (public docs only, no auth/evasion, robots.txt position).

## Adding a new pattern

1. Write `src/pagespring/patterns/<name>.py` implementing the five-member `Pattern` protocol.
2. Register the instance in `registry.py`, respecting the [classification order](#classification-order): host-specific first, extension/content next, then `gitbook` — and **before** `docs_probe`, which must stay last or its catch-all shadows every pattern registered after it.
3. Keep all fetching in `pagespring.http`, and leave asset URLs **absolute** so pagespeak can pull them during convert.
4. Set `AcquireResult.pages` to the count of source units fetched (pages / articles / files) — `ingest` surfaces it, which is how coverage gaps get caught.
5. If the source caps or truncates a crawl, `log.warning` it — a silently truncated crawl reads as a complete one.
6. Add `tests/test_<name>.py` mocking `pagespring.http`: assert both the `match()` routing and that `normalize()` produces the clean shape. Then verify against the real source by reading the `incoming/` file.
7. Declare the pattern's intended pagespeak flags in `convert_recipe` (a hint; pagespring never runs them).
