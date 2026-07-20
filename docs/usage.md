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
- [Reading the result](#reading-the-result)
- [When no pattern matches](#when-no-pattern-matches)
- [Exit codes](#exit-codes)

## Commands

The installed command is `pagespring` (in a repo checkout, `bin/run <cmd>` runs the same CLI from the project venv):

```
pagespring ingest <url>      # acquire + normalize a manual → incoming/<slug>/
pagespring renormalize <slug># re-run normalize against kept raw/ — no re-crawl (needs --keep-raw at ingest)
pagespring localize <slug>   # grab an already-ingested deliverable's images → images/ (resumable; --all)
pagespring patterns          # list the registered source patterns + convert recipes
pagespring classify <url>    # show which pattern handles a URL — no fetch
pagespring status            # list incoming/ deliverables (pattern, pages, size, date, source)
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

- `--keep-raw` keeps the raw crawl alongside the clean file in `incoming/<slug>/raw/`.
- `--download-images` pulls an html/markdown source's remote images into `incoming/<slug>/images/` and re-points the refs (no-op for PDFs). Use it for sources whose images sit behind expiring or tokened URLs.
- `--if-changed` re-crawls but **skips re-staging** when the result is byte-identical to the existing deliverable (compared via the manifest's `sha256`): it prints `unchanged` and leaves the file, its images, and its mtime alone. The crawl still runs — the slug isn't known until after acquire — so this saves the re-write and churn, not the download.

**Re-ingesting replaces.** A second `ingest` of the same slug clears the slug dir first — no stale `raw/`, no orphaned files. The replace happens only once the new normalize succeeds, so a failed re-crawl never destroys a previous good deliverable.

## Ingesting API specs

`ingest` also accepts an **API specification** — an OpenAPI/Swagger spec or a Postman collection — and renders its structure (endpoints, parameters, request bodies, responses, or Postman requests) into one clean markdown file. The `api_spec` pattern recognises these by content, so point it at the raw spec — a URL **or a local file**:

```
pagespring ingest https://api.vendor.com/openapi.json     # OpenAPI 3.x / Swagger 2.0 → markdown
pagespring ingest ./vendor-openapi.yaml                   # local spec file (e.g. a ReDoc "Download")
pagespring ingest ./vendor-postman_collection.json        # Postman collection → markdown
```

Do hand it the **spec file itself**, not the rendered docs page. Most modern API portals (Swagger UI, ReDoc, ReadMe) render client-side from a spec `ingest` can fetch directly even when the page is an empty JS shell; when the spec sits behind a "Download" button, save it and ingest the local file. `ingest` reads the spec only — it never calls the API.

The deliverable is markdown carrying the `--split-sections` recipe, so pagespeak chunks it per endpoint. `pages` reports the operation/request count — a spec that yields 0 is logged as a warning, the same coverage signal as a truncated crawl.

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

## Reading the result

Each `incoming/<slug>/` holds the deliverable — one file per manual, `incoming/<slug>/<slug>.{html,md,pdf}` — plus a `manifest.json` recording its provenance (source URL, pattern, title, `convert_recipe`, page count, `sha256`, ingest time). The manifest makes the hand-off to pagespeak self-describing: the `convert_recipe` travels *with* the file instead of living only in `pagespring patterns`. **Verify a pattern by reading the deliverable file** — not by running pagespeak. `ingest` prints the page count and size so a half-lost crawl is obvious at a glance (a 187-page guide that returns 3 pages is a problem, not a result).

`pagespring status` lists every `incoming/<slug>/` from its manifest — pattern, pages, size, ingest date, and source host. (Legacy dirs from before the manifest fall back to the file's own name/size/date.) Whether a slug has been converted into the manuals corpus is pagespeak's concern, downstream and out of pagespring's view.

## When no pattern matches

Any http(s) URL that no specific pattern claims classifies to `docs_probe` rather than going unmatched — `classify` prints `docs_probe`, meaning "will content-probe the site at acquire," not a confirmed source type. The actual routing happens during `ingest`: `docs_probe` fetches the base page and tries, in order, its `<meta name="generator">` tag, `_static/` assets, a `search/search_index.json`, and an `llms.txt`. A site none of these recognise exits `2`, printing exactly what was probed — that message is the guidance for authoring the source a new pattern (see [architecture.md](architecture.md#adding-a-new-pattern)).

`classify` returns no pattern only for a non-web argument nothing claims — every http(s) URL is routed, since any URL the specific patterns decline falls through to `docs_probe`.

## Exit codes

`ingest` and `renormalize` distinguish failure modes so scripts (and you) can tell them apart:

- `2` — no pattern matched a local file/`file://` argument, `docs_probe` couldn't recognise the site's generator at acquire time, or a URL/file routed to `api_spec` that isn't a recognizable OpenAPI/Swagger/Postman document. For `renormalize`: the slug was never ingested, has no kept `raw/`, or its recorded pattern is no longer registered.
- `3` — normalize produced an empty file (the source likely changed shape; nothing staged, a prior deliverable survives).
- `4` — a network fetch died during acquire (nothing staged; `ingest` only — `renormalize` never touches the network).

These rely on pf-core's `run_cli` propagating `typer.Exit` codes; without it a failed `ingest` would exit `0`.
