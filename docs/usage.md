# Usage

How to drive the pagespring CLI — acquiring a manual, inspecting routing, and reading the result.

pagespring is the **acquisition** front-end. It is not pagespeak: pagespeak *converts* an acquired file into the RAG corpus, while pagespring stops at `incoming/<slug>/`. The hand-off between them is manual.

---

## Table of Contents

- [Commands](#commands)
- [Ingesting a manual](#ingesting-a-manual)
- [Ingesting API specs](#ingesting-api-specs)
- [Localizing images separately](#localizing-images-separately)
- [Renormalizing without a re-crawl](#renormalizing-without-a-re-crawl)
- [Refreshing the corpus](#refreshing-the-corpus)
- [Auditing deliverables](#auditing-deliverables)
- [Reading the result](#reading-the-result)
- [When no pattern matches](#when-no-pattern-matches)
- [Exit codes](#exit-codes)

## Commands

The installed command is `pagespring` (in a repo checkout, `bin/run <cmd>` runs the same CLI from the project venv):

```
pagespring ingest <url>      # acquire + normalize a manual → incoming/<slug>/
pagespring renormalize <slug># re-run normalize against kept raw/ — no re-crawl (needs --keep-raw at ingest)
pagespring refresh <slug>    # re-check a manual against its live source; --all sweeps the corpus
pagespring audit <slug>      # $0 deterministic checks on staged deliverables; --all; --strict gates
pagespring localize <slug>   # grab an already-ingested deliverable's images → images/ (resumable; --all)
pagespring patterns          # list the registered source patterns, in match order
pagespring classify <url>    # show which pattern handles a URL — no fetch
pagespring status            # list incoming/ deliverables (pattern, pages, size, raw?, date, source)
pagespring --help            # the live, authoritative command + flag reference
```

Treat `pagespring --help` as the source of truth for flags — do not rely on a copy here.

## Ingesting a manual

`pagespring ingest <url>` runs the full flow: classify the URL, acquire the raw pages, then normalize them into ONE clean file with absolute asset URLs under `incoming/<slug>/`.

```
pagespring ingest https://support.apple.com/guide/keynote/welcome/mac
pagespring ingest https://docs.tableplus.com
pagespring ingest https://example.com/manual.pdf
pagespring ingest https://requests.readthedocs.io/en/latest/   # Read the Docs → PDF build
pagespring ingest ./openapi.json                # a local file or file:// path, not just a URL
```

The argument can be a **local file path or `file://` URL**, not only a remote URL — handy for a spec or doc you've saved from a viewer's "Download" button (the source is then recognized by its content shape rather than its host).

A few flags worth knowing (run `--help` for the rest):

- `--keep-raw` keeps the raw crawl alongside the clean file in `incoming/<slug>/raw/`, so a later normalize change replays offline via `renormalize`. Ignored for PDF deliverables — their normalize is a passthrough, so raw would just duplicate the staged file.
- `--download-images` pulls an html/markdown source's remote images into `incoming/<slug>/images/` and re-points the refs (no-op for PDFs). Use it for sources whose images sit behind expiring or tokened URLs.
- `--if-changed` re-crawls but **skips re-staging** when the result is byte-identical to the existing deliverable (compared via the manifest's `sha256`): it prints `unchanged` and leaves the file, its images, and its mtime alone. The crawl still runs — the slug isn't known until after acquire — so this saves the re-write and churn, not the download.
- `--slug <name>` overrides the derived slug (folded to kebab-case) — it names the `incoming/` dir **and** the deliverable file, and `refresh` keeps it pinned thereafter. Use it when the URL-derived slug is noise (`auto-align-2-2-2-user-manual` → `auto-align-2`).

**Duplicate detection.** Every ingest compares the new deliverable's `sha256` against every other slug's manifest; byte-identical content under a second name prints `warning : content identical to incoming/<other>/`. Still staged — a deliberate duplicate is allowed; the warning is the product (the same manual fetched from two vendor URLs is how duplicate chunks reach retrieval).

**Re-ingesting replaces, except the image cache.** A second `ingest` of the same slug clears the slug dir first — no stale `raw/`, no orphaned files — but keeps `images/` and `images.json`, so a re-ingest does not re-download images the source has not changed. The replace happens only once the new normalize succeeds, so a failed re-crawl never destroys a previous good deliverable. A re-ingest without `--download-images` resets the manifest's `images` to 0 and restores absolute refs, so re-run `localize` afterwards; the sidecar makes that near-free.

## Ingesting API specs

`ingest` also accepts an **API specification** — an OpenAPI/Swagger spec or a Postman collection — and renders its structure (endpoints, parameters, request bodies, responses, or Postman requests) into one clean markdown file. The `api_spec` pattern recognises these by content, so point it at the raw spec — a URL **or a local file**:

```
pagespring ingest https://api.vendor.com/openapi.json     # OpenAPI 3.x / Swagger 2.0 → markdown
pagespring ingest ./vendor-openapi.yaml                   # local spec file (e.g. a ReDoc "Download")
pagespring ingest ./vendor-postman_collection.json        # Postman collection → markdown
```

Do hand it the **spec file itself**, not the rendered docs page. Most modern API portals (Swagger UI, ReDoc, ReadMe) render client-side from a spec `ingest` can fetch directly even when the page is an empty JS shell; when the spec sits behind a "Download" button, save it and ingest the local file. `ingest` reads the spec only — it never calls the API.

The deliverable is markdown, one section per endpoint. `pages` reports the operation/request count — a spec that yields 0 is logged as a warning, the same coverage signal as a truncated crawl.

## Localizing images separately

`--download-images` runs *inline* during `ingest`, coupling the crawl and the (often far larger) image download into one run. For a big book — or when you just want the text now and the images later — ingest **without** `--download-images` (the deliverable is already complete, with **absolute** image URLs that pagespeak can fetch), then grab the images as a separate step:

```
pagespring localize anatomy-and-physiology-2e   # one book
pagespring localize --all                        # every incoming/<slug>/
```

`localize` downloads the deliverable's remote images into `incoming/<slug>/images/` and re-points the refs — **no re-crawl** — then updates the manifest's image count. It is **resumable**: each image is re-pointed the moment it lands and the file is checkpointed, so a run cut short keeps its progress and a re-run skips what's done. Re-run until it prints `done` (none remaining) — this is how a book whose image set is too large for one run gets fully localized.

## Renormalizing without a re-crawl

`pagespring renormalize <slug>` re-runs the pattern's **current** `normalize` against the kept `incoming/<slug>/raw/` and re-stages the deliverable — no acquire, no network. Use it to iterate on a pattern's normalize logic against a real crawl without re-fetching the site on every attempt (the polite way to field-test), or to re-stage a deliverable after upgrading pagespring.

```
pagespring ingest https://help.vendor.com --keep-raw   # crawl once, keep the raw pages
pagespring renormalize <slug>                           # replay normalize as often as needed
```

- Requires the slug to have been ingested with `--keep-raw` — without a kept `raw/` there is nothing to replay (the error says so; re-ingest with the flag).
- **Byte-identical output re-stages nothing** and prints `unchanged` — the signal that a normalize change was behavior-preserving. Changed output replaces the deliverable and updates the manifest.
- A changed replay leaves the new deliverable's asset URLs **absolute** again (that is what normalize produces) and clears `images/` — the old files were named for the old deliverable's refs, and stale ones would push a re-localize onto suffixed names. If you had localized images, re-run `pagespring localize <slug>` afterwards.
- Do not point it at a slug whose pattern has been renamed/removed since the ingest — the manifest records the pattern by name and the replay refuses rather than guessing.

## Refreshing the corpus

Manuals rev — plugin updates, firmware manuals, edition bumps. `pagespring refresh` re-checks ingested slugs against their recorded `source_url` and re-stages only what changed:

```
pagespring refresh <slug>    # one manual
pagespring refresh --all     # sweep every incoming/<slug>/
```

One line per slug, then a summary count:

- **`changed`** — the source produced different content; the deliverable was replaced (hand it back to pagespeak).
- **`unchanged`** — byte-identical re-crawl (nothing touched), or, for single-fetch sources (direct PDFs, doc archives), a conditional-GET probe answered 304 — `unchanged — not modified (validator probe)` — and nothing was re-downloaded at all. Crawl sources always re-crawl: an entry page's validators prove nothing about the rest of a site.
- **`failed`** — the source didn't answer or normalized to nothing; the existing deliverable is kept.
- **`skipped`** — no manifest (never ingested by a manifest-writing version).

`--all` over an **empty or missing** `incoming/` sweeps nothing and exits `2` — never read that as a clean sweep.

A slug ingested with `--keep-raw` keeps that property across a refresh (the new crawl's raw is kept, so `renormalize` stays possible), and the **recorded slug is pinned** — a retitled source or a `--slug` override refreshes in place instead of minting a duplicate dir. A refresh never auto-downloads images — re-run `localize` after a `changed` slug that needs them.

The summary is the wrapper hook: grep the report for `: changed` to know which slugs to re-convert (pagespeak) and re-index.

## Auditing deliverables

`pagespring audit [<slug>|--all]` runs deterministic, $0 checks over staged deliverables — no network, no LLM, read-only. It catches what a glance at `status` can't:

- **errors** (the deliverable can't be trusted): `manifest_missing`, `deliverable_missing`, `deliverable_empty`, `sha_mismatch` — the on-disk file no longer hashes to the staged `sha256` (hand-edited or corrupted; only checked while un-localized, since `localize` legitimately rewrites refs) — `crawl_truncated`, a crawl that hit its page cap or stalled — `pages_lost`, pages discovered but never staged because the source errored mid-crawl, which no content check can see — `single_page_crawl`, a crawl pattern that returned exactly one page (the too-specific-seed signature: point `llms_txt` at one doc page instead of the index and it fetches that page's `.md` twin, staging 1 page where the site has 170; PDF deliverables, `single_fetch` patterns, and sources the acquire marked `single_document` — a blog post or article that IS one page — are one file by design and never fire it) — `broken_image_ref`, a local `images/<name>` ref whose file is missing (invisible to the remote-ref count, so a fully localized deliverable could still ship dead images) — and `duplicate_source_url`, two slugs staged from the same URL, which blocks the hand-off for both.
- **warnings** (real but survivable): `localize_incomplete` (localized images recorded but remote refs remain — re-run `localize`), `no_headings` (a multi-page crawl normalized to heading-less soup — the half-lost-crawl signature; it will split into nothing downstream), `duplicate_content` (two slugs holding byte-identical deliverables — legitimate when a vendor mirrors one manual at two URLs).

The last two are **corpus-level**: they compare slugs against each other, so they only appear under `--all`.

Report-only by default (exit `0`). `--strict` exits `1` when any **error**-level finding exists, so a script can gate the pagespeak hand-off:

```
pagespring audit --all --strict && <hand off to pagespeak>
```

`--all` over an **empty or missing** `incoming/` audits nothing and exits `2`. Never read that as a pass — a wiped or mis-pathed corpus is exactly what the gate exists to catch.

`audit` complements — it does not replace — reading the deliverable. It catches structural defects; only a human read catches wrong content.

## Reading the result

Each `incoming/<slug>/` holds the deliverable — one file per manual, `incoming/<slug>/<slug>.{html,md,pdf}` — plus a `manifest.json` recording its provenance (source URL, pattern, title, page count, `sha256`, ingest time). The manifest makes the hand-off to pagespeak self-describing — it says what the source *is*, and pagespeak decides how to convert it. **Verify a pattern by reading the deliverable file** — not by running pagespeak. `ingest` prints the page count and size so a half-lost crawl is obvious at a glance (a 187-page guide that returns 3 pages is a problem, not a result).

`pagespring status` lists every `incoming/<slug>/` from its manifest — pattern, pages, size, ingest date, and source host. A `raw` marker means the slug kept its crawl, so a normalize change replays offline with `renormalize` instead of needing a re-crawl. (Legacy dirs from before the manifest fall back to the file's own name/size/date.) Whether a slug has been converted into the manuals corpus is pagespeak's concern, downstream and out of pagespring's view.

## When no pattern matches

Any http(s) URL that no specific pattern claims classifies to `docs_probe` rather than going unmatched — `classify` prints `docs_probe`, meaning "will content-probe the site at acquire," not a confirmed source type. The actual routing happens during `ingest`: `docs_probe` fetches the base page and works from the most specific evidence to the least — content type first (PDF magic bytes, for a "docs" URL that serves a PDF), then the asset tells of vendor tools that emit no generator tag, then `<meta name="generator">`, then the weaker fallback tells (a `search/search_index.json`, an `llms.txt`). Don't mirror the ladder here; a site none of it recognises exits `2` printing exactly what was probed, and that message is the live list (and the guidance for authoring a new pattern — see [architecture.md](architecture.md#adding-a-new-pattern)).

`classify` returns no pattern only for a non-web argument nothing claims — every http(s) URL is routed, since any URL the specific patterns decline falls through to `docs_probe`.

## Exit codes

`refresh` reports per-slug outcomes instead of failing fast: exit `0` for a clean sweep, `1` when any slug failed (the report names them), `2` when the single slug you named can't be refreshed, when `--all` found no slugs to sweep, or when neither slug nor `--all` was given. As with `audit`, an empty corpus is `2` and never a clean sweep.

`audit` exits `0` when it ran, `1` under `--strict` when any error-level finding exists, and `2` when `--all` found no slugs to audit — `2` means "could not do what you asked", never "clean".

`ingest` and `renormalize` distinguish failure modes so scripts (and you) can tell them apart:

- `2` — no pattern matched a local file/`file://` argument, `docs_probe` couldn't recognise the site's generator at acquire time, or a URL/file routed to `api_spec` that isn't a recognizable OpenAPI/Swagger/Postman document. For `renormalize`: the slug was never ingested, has no kept `raw/`, or its recorded pattern is no longer registered.
- `3` — normalize produced an empty file (the source likely changed shape; nothing staged, a prior deliverable survives).
- `4` — a network fetch died during acquire (nothing staged; `ingest` only — `renormalize` never touches the network).

These rely on pf-core's `run_cli` propagating `typer.Exit` codes; without it a failed `ingest` would exit `0`.

A malformed invocation — unknown option, missing argument — also exits `2`, with a usage message rather than a traceback. So `2` means "the command couldn't proceed with what it was given", whether that's the argv or the source.

Every command that takes a `<slug>` folds it before it names a directory, so a slug argument that folds to nothing (`..`, `.`, `///`) exits `2` on all of them rather than resolving to the corpus root.
