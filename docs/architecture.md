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

**Normalize flattens responsive images first.** The localizer follows `<img src>` and markdown `](…)` only, so any reference parked in `<picture>`/`<source srcset>`, `srcset`/`data-srcset`, or a `data-src` lazy-load attribute is invisible to it and ships remote no matter how often `localize` runs. `_site.flatten_responsive_images` reduces each image to one plain `<img src>`, resolving a winner **before** deleting any carrier. The winner is the **widest declared rendition** — a `srcset` `w`/`x` descriptor, or a CDN sizing parameter (`wid=`, `width=`) — because the deliverable feeds a vision pass, so resolution is the point. With no widths declared anywhere it falls back to `src`, then `data-src`, then the first `srcset` candidate.

Three traps drove that design. Publishers park a transparent `data:` spacer in `src` with the real URL in `data-src`, so preferring `src` blanks the figure. A `data:` URI contains its own comma, so splitting a srcset on commas yields base64 fragments that look like relative URLs. And a `<source>` behind `media="(not all)"` — a query that can never match — holds a variant the page never renders (Apple's dark-mode asset), so it must be excluded from ranking rather than treated as a better candidate.

Call it before `absolutize_refs`: a promoted `data-src` is usually page-relative. An `<img>` with no usable reference is dropped rather than staged empty, and an already-localized `images/…` ref is never swapped back to a remote rendition.

**Image refs are decoded before fetching.** The localizer matches refs by regex over the raw markup, so `&` arrives as `&amp;`. Fetched escaped, a CDN reads `amp;wid=1199` as an unknown parameter and serves its small default — the Adobe manuals localized at 400px for exactly this reason. `images.py` unescapes at the fetch boundary and keys the sidecar on the decoded URL.

**Image localization is a decoupled step.** Because normalize leaves asset URLs **absolute**, a no-image deliverable is already complete (pagespeak can fetch those URLs at convert time). Images can be pulled either inline (`ingest --download-images`) or after the fact (`pagespring localize <slug>` → `orchestrate.localize_images`), both downloading into `incoming/<slug>/images/` and re-pointing refs. `localize` is **resumable** — it re-points each image as it lands and checkpoints the file — so a book whose image set exceeds one run's budget is finished by re-running until none remain. `images.py` is a shim over `pf_core.fetch.images`, keeping pagespring's on-disk naming (sanitized basename, `-2`/`-3` on collision) and routing downloads through `pagespring.http` so they carry the crawl UA and delay.

**Refresh is a sweep of step-1-to-4 re-runs, driven by the manifests.** `pagespring refresh [<slug>|--all]` (→ `refresh.py`) re-ingests each slug from its manifest's `source_url` with `--if-changed` semantics, isolating per-slug failures so one dead source can't stop the sweep, preserving the kept-raw property, pinning the recorded slug (a retitled source or `--slug` override never mints a duplicate dir), and reporting each outcome (`changed`/`unchanged`/`failed`/`skipped`). Patterns that declare `single_fetch = True` (the deliverable derives from exactly the one recorded URL — `pdf_url`, `archive_download`) get a fast path first: their acquire captures the response's `ETag`/`Last-Modified` into the manifest, and refresh probes with one conditional GET (`http.not_modified`) — a definitive 304 reports `unchanged` with no re-download; anything else falls through to the full path. Crawl patterns never probe: an entry page's validators prove nothing about the rest of a site.

**Renormalize is an offline replay of steps 3–4.** `pagespring renormalize <slug>` (→ `orchestrate.run_renormalize`) re-runs the pattern's **current** `normalize()` against the kept `incoming/<slug>/raw/` — no classify, no acquire, no network. The `AcquireResult` is reconstructed from the manifest (pattern by recorded name, kind/slug/pages/title), and raw is copied into a fresh workdir so a normalize that mutates its input can't corrupt the kept copy. Byte-identical output re-stages nothing and reports `unchanged` — the signal that a normalize refactor was behavior-preserving. Changed output replaces the deliverable, clears any stale `images/` (a re-localize would otherwise collide into suffixed names), and refreshes the manifest's content facts (`sha256`, `bytes`, `deliverable`, `images` reset to 0 — refs are absolute again) while crawl provenance (`source_url`, `ingested_at`, `pages`) is untouched. Requires an ingest made with `--keep-raw`; the manifest's `kept_raw` records whether that happened, read from the staged directory so it cannot promise a replay that isn't there. `--keep-raw` is ignored for PDF deliverables — `pdf_url.normalize` returns the downloaded file unchanged, so a replay can only reproduce the staged bytes.

## The manifest

Every staged deliverable gets a sibling `incoming/<slug>/manifest.json` (`manifest.py`) — the provenance record that makes the pagespeak hand-off self-describing:

```json
{
  "schema_version": 6,
  "pagespring_version": "0.9.0",
  "source_url": "https://docs.tableplus.com/",
  "pattern": "docs_probe",
  "slug": "tableplus",
  "kind": "markdown",
  "title": "TablePlus Documentation",
  "etag": null,
  "last_modified": null,
  "truncated": false,
  "single_document": false,
  "kept_raw": false,
  "lost": 0,
  "localized_sha256": null,
  "deliverable": "tableplus.md",
  "pages": 62,
  "bytes": 123456,
  "sha256": "…",
  "images": 0,
  "ingested_at": "2026-06-14T17:23:01Z"
}
```

Schema v2 added `title` (acquire's source title, feeding `renormalize` replays); v3 added `etag`/`last_modified` (response validators from single-fetch acquires, feeding `refresh`'s conditional-GET probe); v4 added `truncated`; v6 added `lost` (pages discovered but never staged — `truncated` only ever meant the page *cap*, so a crawl losing pages to throttling looked complete), `single_document` — the source **is** one article, so a `pages: 1` deliverable is correct rather than a crawl that collapsed onto its seed (`audit`'s `single_page_crawl` check reads it) — `kept_raw`, recorded from the staged directory rather than the flag, so a manifest can never promise a `renormalize` replay that isn't on disk, and `localized_sha256`. `status` marks kept-raw slugs `raw`. Read the post-v1 keys with `.get` — older files lack them.

**v5 dropped `convert_recipe`** — the sole non-additive change. Every remaining field states what the source *is*; none instructs the converter. Do not add a field that does: pagespring cannot see pagespeak's flags, so a hint staged here goes stale silently the moment pagespeak's evidence or CLI moves, and nothing fails loudly when it does. pagespeak derives conversion settings from `kind`, `pattern`, and the deliverable itself.

`sha256` is the hash of the deliverable **as `normalize()` produced it** (before `--download-images` re-points any refs) — so on the default path it matches the on-disk file, and it stays stable as the content's identity regardless of image-localization. `localized_sha256` is the companion: the hash of the file **as it stands after** an image pass re-pointed its refs, refreshed by every pass and reset to `null` by `renormalize`. `audit` checks whichever of the two describes the bytes on disk, so a localized deliverable — the most-processed kind — still has an integrity record rather than none.

That hash is what `ingest --if-changed` compares against: a re-crawl that normalizes to the same bytes leaves the existing `incoming/<slug>/` untouched (file, images, mtime) and reports `unchanged`. The crawl itself still runs — the slug is only known after `acquire`, so `--if-changed` saves the re-stage and churn, not the network round-trip. `status` reads these manifests; legacy dirs without one fall back to the deliverable file's own facts.

## The Pattern contract

A pattern is one source type's knowledge, as a class implementing the `Pattern` protocol (`base.py`). Four members:

- `name` — the registry id (`apple_help`, `gitbook`, …).
- `match(url)` — cheap host/path check, returns bool.
- `acquire(url, workdir)` — download raw pages, return an `AcquireResult`.
- `normalize(acq, workdir)` — merge/clean into one file, return its path.

The `acquire`/`normalize` split is the key design rule: `acquire` holds all network and source-shape knowledge; `normalize` is pure transformation over local files. That split is what lets tests mock `pagespring.http` and exercise both halves with no network.

## Classification order

First match wins, so registration order in `registry.py` is load-bearing. Four tiers, cheapest/most-specific first:

1. **Host-specific** patterns (e.g. `apple_help`, `readthedocs`, `github_markdown`) — they recognise a known host. RTD projects without PDF builds fall back to a Sphinx crawl instead of failing.
2. **Extension / content** patterns — `api_spec` (a `.json`/`.yaml`/`.yml` extension, or an `openapi`/`swagger`/`postman` token in the last path segment), then `pdf_url`, `archive_download` — so a spec or a `.pdf` routes here rather than falling through to a broader pattern below.
3. **`gitbook`**, narrowed to its own hosting (`*.gitbook.io`) — custom-domain GitBook sites carry no URL tell, so they fall through to `docs_probe` instead.
4. **`docs_probe` last** — a content-probing catch-all that claims any http(s) URL nothing above it matched. Its `match` is nearly free (scheme check only); all the real classification work happens in `acquire`, which probes the base page in order — `%PDF-` magic bytes first (a vendor may serve the manual itself as a PDF from an extensionless path, which `pdf_url.match` cannot see), then the **tell-based** detectors, then `<meta name="generator">`, then fallback tells: `_static/` assets (Sphinx), a `search/search_index.json` (MkDocs), an `llms.txt` with per-page `.md` links (GitBook-style sites on custom domains, delegated back to the gitbook machinery so its image-proxy resolution still applies). A site none of these recognise raises `InvalidInputError` naming what was probed (CLI exit 2).

   **Tell-based detectors run before the generator sniff** because those systems have two faces: the entry page a reader lands on advertises differently from the topic pages that carry the content. ClickHelp publishes no generator meta at all; a Paligo *portal* shell carries none (only its topics do); a SCHEMA ST4 entry page advertises the publisher's own stylesheet rather than `ST4`. Probing the landing URL for a generator therefore identifies nothing, and the manual reads as an unrecognized site.

   **Content-container selectors are ordered, most-specific first — never grouped.** A publisher who renames the real container keeps the generator's stock id on an *outer* wrapper that also holds the search widget and nav. A grouped selector (`#content, #contentOrg`) returns whichever comes first in document order, which is the wrapper, so the chrome ships inside every page. This passes any fixture that treats the two ids as alternatives and only fails against the real source — check it with a live ingest, not a unit test.

So `classify` reporting `docs_probe` means "will content-probe at acquire" — not a confirmed source type. `classify` alone therefore cannot prove a URL is unroutable; only `ingest` (or a direct read of `docs_probe.acquire`) can, since it's the tier that actually fetches and sniffs. `classify` returns `None` only for a non-web argument (a local file path or `file://` URL) — every http(s) URL has a match by the time `docs_probe` is reached.

`api_spec` and `docs_probe` are the two patterns that match on **content shape** rather than host, following the same precedent: a cheap `match`, then a content-sniffing `acquire`. `api_spec`'s `match` is the usual cheap path check, but OpenAPI-vs-Postman can't be told from a URL — so `acquire` fetches the file and *content-sniffs* it: an `openapi`/`swagger` key ⇒ OpenAPI, `info` + `item` ⇒ Postman, neither ⇒ a clean `InvalidInputError` (CLI exit 2). It emits `kind="markdown"`, rendering the spec's endpoints, params, and responses (or the collection's requests) into one file instead of crawling pages. It also accepts a **local file path**, since a spec is often behind a ReDoc/Swagger-UI "Download" button rather than at a stable URL.

Check a URL's routing with `pagespring classify <url>`; list what's registered with `pagespring patterns`.

## Fetching

All network I/O goes through `pagespring.http`, a thin shim over `pf_core.fetch` (stdlib `urllib`). **Never add `httpx` or `requests`.** The shim owns what is pagespring's: the identifying `pagespring/<version>` User-Agent (`PAGESPRING_UA` overrides it for a source that mishandles tool UAs), the `timeout=` keyword the patterns pass, `polite_sleep()` between crawl requests, and a per-fetch size cap with separate budgets for text and binary downloads (`PAGESPRING_MAX_TEXT_BYTES` / `PAGESPRING_MAX_DOWNLOAD_BYTES` override the defaults in `http.py`). The cap bounds the decoded body as well as the wire read, so an overrun surfaces as `ClientError` → `AcquireError` → CLI exit 4 instead of exhausting memory as it inflates. The fetch core owns the transport: status-aware retries (permanent 4xx fail fast; 429 honours `Retry-After`, capped; 5xx and network errors back off), charset resolved from the response, cache validators, and manual redirect walking.

Two properties patterns are written against. **Raw `urllib` exceptions propagate unwrapped**, which is what lets a pattern branch on `HTTPError.code` (readthedocs' 404 PDF fallback, the microsoft_support 403 cooldown). And **URLs are SSRF-guarded** — on the initial request and every redirect hop, a URL that resolves to a private, loopback, link-local, or unresolvable address raises `InvalidInputError` before any request goes out (CLI exit 2); `URL_FETCH_ALLOW_PRIVATE=1` opts out for a deliberately internal source.

Patterns log-and-skip individual page failures rather than aborting a whole crawl, so one dead topic page doesn't lose the manual. See the README's *Intended use* section for the client-behavior stance (public docs only, no auth/evasion, robots.txt position).

## Adding a new pattern

1. Write `src/pagespring/patterns/<name>.py` implementing the four-member `Pattern` protocol.
2. Register the instance in `registry.py`, respecting the [classification order](#classification-order): host-specific first, extension/content next, then `gitbook` — and **before** `docs_probe`, which must stay last or its catch-all shadows every pattern registered after it.
3. Keep all fetching in `pagespring.http`, and leave asset URLs **absolute** so pagespeak can pull them during convert.
4. Call `_site.flatten_responsive_images(node)` immediately before `absolutize_refs` — without it, every `<picture>`/`srcset`/`data-src` reference the source uses ships remote and no `localize` run can reach it.
5. If the pattern derives a base directory from the entry URL, use `_site.names_a_file` — `docs_probe` hands over `url.rstrip("/")`, so a directory seed arrives suffix-less and an unconditional strip scopes the crawl to the *parent*, where every sibling 404s. Re-add the trailing slash before crawling too: the entry URL is also the `urljoin` base for `absolutize_refs`, so without it every relative asset resolves one level too high. Anchor on the **post-redirect** URL, and re-check scope after each fetch — an in-scope href can still redirect out.
6. Set `AcquireResult.pages` to the count of source units the deliverable covers (crawl pages / articles / PDF pages) — `ingest` surfaces it, which is how coverage gaps get caught. Use `None` when it genuinely can't be determined; never substitute a placeholder like `1`, which reads as real and hides the gap. PDF patterns get theirs from `patterns/_pdf.page_count`.
7. If the source caps or truncates a crawl, `log.warning` it — a silently truncated crawl reads as a complete one.
8. Add `tests/test_<name>.py` mocking `pagespring.http`: assert both the `match()` routing and that `normalize()` produces the clean shape. Then verify against the real source by reading the `incoming/` file.

Do **not** give the pattern an opinion about conversion. If the source needs unusual downstream handling, say so in pagespeak — pagespring's job ends at the clean file.
