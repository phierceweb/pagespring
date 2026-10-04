# Usage

How to drive the pagespring CLI — acquiring a manual, inspecting routing, and reading the result.

pagespring is the **acquisition** front-end. It is not pagespeak: pagespeak *converts* an acquired file into the RAG corpus, while pagespring stops at `incoming/<slug>/`. The hand-off between them is manual.

---

## Table of Contents

- [Commands](#commands)
- [Ingesting a manual](#ingesting-a-manual)
- [Ingesting a list of manuals](#ingesting-a-list-of-manuals)
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
pagespring ingest --batch <file>  # one ingest per URL line; per-line status + summary
pagespring renormalize <slug># re-run normalize against kept raw/ — no re-crawl (needs --keep-raw at ingest)
pagespring refresh <slug>... # re-check manuals against their live sources; --all or --pattern sweeps
pagespring audit <slug>      # $0 deterministic checks on staged deliverables; --all; --strict gates
pagespring localize <slug>   # grab an already-ingested deliverable's images → images/ (resumable; --all)
pagespring patterns          # list the registered source patterns, in match order
pagespring classify <url>    # show which pattern handles a URL — no fetch; --probe names docs_probe's route
pagespring status            # list incoming/ deliverables (pattern, pages, size, raw?, date, source)
pagespring --help            # the live, authoritative command + flag reference
```

Treat `pagespring --help` as the source of truth for flags — do not rely on a copy here.

## Ingesting a manual

`pagespring ingest <url>` runs the full flow: classify the URL, acquire the raw pages, then normalize them into ONE clean file with absolute asset URLs under `incoming/<slug>/`.

```
pagespring ingest https://support.apple.com/guide/keynote/welcome/mac
pagespring ingest https://support.apple.com/guide/logicpro-ipad/welcome/ipados   # an Apple guide for any platform
pagespring ingest https://docs.tableplus.com
pagespring ingest https://example.com/manual.pdf
pagespring ingest https://requests.readthedocs.io/en/latest/   # Read the Docs → PDF build
pagespring ingest https://docs.vendor.com/llms-full.txt   # inlined full-docs file → one markdown deliverable
pagespring ingest https://vendor.com/manual.epub           # doc archives (zip/tar/epub) → merged clean file
pagespring ingest https://docs.vendor.com/sitemap.xml      # opt-in: crawl the pages a sitemap lists
pagespring ingest https://github.com/owner/repo/tree/main/docs   # markdown/MDX docs kept in a repo
pagespring ingest https://help.vendor.com/r/product/2.0/en/  # one publication of a Fluid Topics portal
pagespring ingest ./openapi.json                # a local file or file:// path, not just a URL
```

The argument can be a **local file path or `file://` URL** for an **API spec** — handy for one you've saved from a viewer's "Download" button (`api_spec` recognizes it by content shape rather than host) — or a **doc archive** (zip, tar, epub). A local `.pdf` routes to `pdf_url` and fails the fetch layer's scheme guard, so ingest a PDF by its URL.

**A doc archive ships its own figures.** Each image ref in an EPUB or zip that names an archive member is copied into `incoming/<slug>/images/` and re-pointed, so the deliverable needs no network for them; a ref to a member the archive lacks surfaces as `broken_image_ref` in `audit`. A link from one member to another becomes an in-document link; an id an earlier member already holds is renamed, with the links to it, so each link lands in the member it names. An EPUB is titled from its OPF metadata and leaves out its navigation document.

**A sitemap URL is an explicit request to crawl it.** Point `ingest` at a `sitemap.xml` (or a sitemap index) for a site nothing else recognizes: every page it lists under its directory is fetched and reduced to its main content. The sitemap is taken as given, so choose one scoped to the manual — a multi-locale sitemap stages every language.

**A wiki manual is its landing page plus the pages it links to.** For a MediaWiki site, seed at the manual's own landing page, not the wiki's main page, which links to everything.

**A Fluid Topics portal holds many publications.** Seed at one publication's reader URL (`/r/<publication>/`, or any topic under it), not the portal's home page, which names none. Its inline images land in `incoming/<slug>/images/`, as an archive's figures do.

A few flags worth knowing (run `--help` for the rest):

- `--keep-raw` keeps the raw crawl alongside the clean file in `incoming/<slug>/raw/`, so a later normalize change replays offline via `renormalize`. Ignored for PDF deliverables — their normalize is a passthrough, so raw would just duplicate the staged file.
- `--download-images` pulls an html/markdown source's remote images into `incoming/<slug>/images/` and re-points the refs (no-op for PDFs). Use it for sources whose images sit behind expiring or tokened URLs.
- `--if-changed` re-crawls but **skips re-staging** when the result is byte-identical to the existing deliverable (compared via the manifest's `sha256`): it prints `unchanged` and leaves the file, its images, and its mtime alone. That answer needs the recorded deliverable to still be **on disk and matching its recorded hash** (`localized_sha256` once an image pass recorded one) — a slug whose file went missing or was altered re-stages rather than reporting `unchanged` forever. A file localized without a recorded hash can't be compared and is left in place. The crawl still runs — the slug isn't known until after acquire — so this saves the re-write and churn, not the download.
- `--slug <name>` overrides the derived slug (folded to kebab-case, then capped at 100 characters) — it names the `incoming/` dir **and** the deliverable file, and `refresh` keeps it pinned thereafter. Use it when the URL-derived slug is noise (`auto-align-2-2-2-user-manual` → `auto-align-2`).
- `--replace` lets an ingest take over a slug that already holds a **different** source, deleting that manual and its image cache, and accepts a same-source re-crawl the collapse guard refused. Without it both are refused — see below.

**Duplicate detection.** Every ingest compares the new deliverable's `sha256` against every other slug's manifest; byte-identical content under a second name prints `warning : content identical to incoming/<other>/`. Still staged — a deliberate duplicate is allowed; the warning is the product (the same manual fetched from two vendor URLs is how duplicate chunks reach retrieval).

**Re-ingesting the same source replaces, except the image cache.** A second `ingest` of the same slug writes the new deliverable atomically, then clears what it replaced — no stale `raw/`, no orphaned files — but keeps `images/` and `images.json`, so a re-ingest does not re-download images the source has not changed. Nothing is cleared until the new deliverable is in place, so a failed re-crawl, a full disk or a kill never destroys a previous good deliverable. A re-ingest without `--download-images` resets the manifest's `images` to 0 and restores absolute refs, so re-run `localize` afterwards; the sidecar makes that near-free.

**A slug collision with a different source is refused.** Where the slug dir already holds a manual from another URL, `ingest` raises instead of clearing it: `incoming/` is gitignored, so the displaced manual has no other copy, and host- or filename-derived slugs collide readily across one vendor's manuals. Two remote URLs are compared canonically, so an `http`→`https`, `www.`, trailing-slash, fragment, or tracking-param respelling still counts as the same source and replaces as usual; a local path or `file://` URL has no canonical form and is compared as a resolved filesystem path, so the same file typed `./spec.json`, `spec.json`, or `file://` is one source. A slug dir carrying no readable manifest — a legacy pre-manifest slug, or a corrupted one — is refused whenever it still holds anything, since nothing there can say what that is; an empty leftover dir holds no manual and is staged into as usual. `--if-changed` reports `unchanged` only for the source the slug already holds; a different URL serving byte-identical content is refused like any other collision rather than quietly re-using the slug. Give the new source its own directory with `--slug`, or take the slug over with `--replace` — which deletes the displaced manual **and** its image cache, that cache belonging to the manual being displaced rather than the new one. The check runs after acquire, since the slug isn't known until then, so a refused ingest has already paid for its crawl.

**A collapsed re-crawl is refused.** When a same-source re-ingest finds far fewer pages than the manual staged in its slug (`COLLAPSE_KEEP_PCT` in `.env.example`), `ingest` exits `2` and the staged manual stays. A source that changed its link shape or throttled the crawl still normalizes to a non-empty shell, and staging that would replace the whole manual. A re-crawl cut short by its page cap (`truncated`) is refused whenever the staged manual is larger and was complete, however many pages it kept — a cap proves nothing about the source. The guard covers single-fetch sources too — a stub PDF, a shrunken archive, an API spec with far fewer operations — and says so in its message. `refresh` reports the slug `failed`. When the source really did shrink, re-ingest with `--slug <slug> --replace` (the `--slug` keeps a refreshed slug's directory), adding `--keep-raw` when the slug holds `raw/` — `--replace` without it deletes the kept raw.

## Ingesting a list of manuals

`pagespring ingest --batch <file>` runs one ingest per line of a text file — a URL or local path per line; blank lines and lines starting with `#` are skipped. `--keep-raw`, `--download-images`, `--if-changed` and `--replace` apply to every line; a URL argument or `--slug` is refused beside `--batch`, since each line takes its own derived slug. Lines run in order, paced like crawl requests. A failed line is reported and the batch moves on; a line naming the same source as an earlier line is skipped rather than crawled twice, however it is spelled (`www.`, a trailing slash, a fragment; a local file by its resolved path). No line takes over a slug an earlier line staged, even with `--replace`: two sources can derive one slug, and the batch cannot give either a `--slug`, so the later line fails — ingest it on its own with `--slug`. Each line prints as it finishes (`staged`, `unchanged`, `failed — <reason>`, `skipped — repeats line N`), then a summary names any failed lines.

## Ingesting API specs

`ingest` also accepts an **API specification** — an OpenAPI/Swagger spec or a Postman collection — and renders its structure (endpoints, parameters, request bodies, responses, or Postman requests) into one clean markdown file. The `api_spec` pattern recognises these by content, so point it at the raw spec — a URL **or a local file**:

```
pagespring ingest https://api.vendor.com/openapi.json     # OpenAPI 3.x / Swagger 2.0 → markdown
pagespring ingest ./vendor-openapi.yaml                   # local spec file (e.g. a ReDoc "Download")
pagespring ingest ./vendor-postman_collection.json        # Postman collection → markdown
```

A rendered **Swagger UI, Redoc or Scalar page** works when it names its spec URL outright: `ingest` finds the URL — in the page's `url` query, a Redoc/Scalar attribute, a Swagger UI config document (`configUrl`), or a quoted spec URL in the page or its initializer — proves it parses as OpenAPI, and ingests that spec. Swagger's Petstore demo is never taken. A page naming several specs (API versions, stable and unstable) exits `2` listing them — ingest the one you want, either by its own URL (an extensionless one like springdoc's `/v3/api-docs/users` works) or by the page with the spec in its query: `pagespring ingest 'https://host/swagger-ui/index.html?url=/v3/api-docs/users'`. A page that computes its spec URL in script exits `2` naming the UI; ingest the spec's URL, or a copy saved from its "Download" button. `ingest` reads the spec only — it never calls the API.

The deliverable is markdown, one section per endpoint. `pages` reports the operation/request count — a spec that yields 0 is logged as a warning, the same coverage signal as a truncated crawl.

## Localizing images separately

`--download-images` runs *inline* during `ingest`, coupling the crawl and the (often far larger) image download into one run. For a big book — or when you just want the text now and the images later — ingest **without** `--download-images` (the deliverable is already complete, with **absolute** image URLs), then grab the images as a separate step:

```
pagespring localize anatomy-and-physiology-2e   # one book
pagespring localize --all                        # every incoming/<slug>/
```

`localize` downloads the deliverable's remote images into `incoming/<slug>/images/` and re-points the refs — **no re-crawl** — then updates the manifest's image count. It is **resumable**: each image is re-pointed the moment it lands and the file is checkpointed, so a run cut short keeps its progress and a re-run skips what's done. Re-run until it prints `done` (none remaining) — this is how a book whose image set is too large for one run gets fully localized. A cached image is never deleted before its replacement is in hand: when the image host is down, the file and its record stay and the ref waits for the next run. `localize` refuses a deliverable that no longer matches its recorded hash — re-ingest it instead, since a pass would record the damage as verified; `localize <slug>` exits `2`, and `--all` prints `skip <slug>: …` and carries on. A pass killed outright (SIGKILL, power loss) leaves its manifest marking the pass open: `audit` reports `localize_interrupted`, and the next `localize` resumes it while a file changed anywhere else is still refused.

## Renormalizing without a re-crawl

`pagespring renormalize <slug>` re-runs the pattern's **current** `normalize` against the kept `incoming/<slug>/raw/` and re-stages the deliverable — no acquire, no network. Use it to iterate on a pattern's normalize logic against a real crawl without re-fetching the site on every attempt (the polite way to field-test), or to re-stage a deliverable after upgrading pagespring.

```
pagespring ingest https://help.vendor.com --keep-raw   # crawl once, keep the raw pages
pagespring renormalize <slug>                           # replay normalize as often as needed
```

- Requires the slug to have been ingested with `--keep-raw` — without a kept `raw/` there is nothing to replay (the error says so; re-ingest with the flag).
- **Byte-identical output re-stages nothing** and prints `unchanged` — the signal that a normalize change was behavior-preserving. Changed output replaces the deliverable and updates the manifest.
- A changed replay leaves the new deliverable's asset URLs **absolute** again (that is what normalize produces) and clears `images/` — the old files were named for the old deliverable's refs, and stale ones would push a re-localize onto suffixed names. A doc archive's own figures are copied back from the kept `raw/`. If you had localized images, re-run `pagespring localize <slug>` afterwards.
- Do not point it at a slug whose pattern has been renamed/removed since the ingest — the manifest records the pattern by name and the replay refuses rather than guessing.

## Refreshing the corpus

Manuals rev — plugin updates, firmware manuals, edition bumps. `pagespring refresh` re-checks ingested slugs against their recorded `source_url` and re-stages only what changed:

```
pagespring refresh <slug>...          # the named manuals, each once
pagespring refresh --pattern pdf_url  # every slug a pattern acquired (repeatable)
pagespring refresh --all              # sweep every incoming/<slug>/
```

Sweep by pattern to split cheap sources from expensive ones. A PDF, archive or spec costs one conditional request when its validators still hold, while a crawl re-fetches every page at the polite delay. `--pattern` selects by the pattern the manifest records, so a PDF that `docs_probe` found stays under `docs_probe`. Name slugs, or sweep with `--all`/`--pattern` — not both.

One line per slug, then a summary count:

- **`changed`** — the source produced different content; the deliverable was replaced (hand it back to pagespeak).
- **`unchanged`** — byte-identical re-crawl (nothing touched), or, for a single-fetch source whose manifest carries validators (a PDF, including one served from an extensionless docs URL, or a doc archive), a conditional-GET probe answered 304, or 200 with the same strong `ETag` — `unchanged — not modified (validator probe)` — and nothing was re-downloaded at all. The probe is trusted only while the staged deliverable is on disk and matches its recorded hash; otherwise the slug is fully re-ingested. Crawl sources always re-crawl: an entry page's validators prove nothing about the rest of a site.
- **`failed`** — the source didn't answer, normalized to nothing, or re-crawled to far fewer pages than are staged; the existing deliverable is kept.
- **`skipped`** — no manifest (never ingested by a manifest-writing version).

`--all` over an **empty or missing** `incoming/`, or a `--pattern` no staged slug records, sweeps nothing and exits `2` — never read that as a clean sweep.

A slug ingested with `--keep-raw` keeps that property across a refresh (the new crawl's raw is kept, so `renormalize` stays possible), and the **recorded slug is pinned** — a retitled source or a `--slug` override refreshes in place instead of minting a duplicate dir. A refresh never auto-downloads images — re-run `localize` after a `changed` slug that needs them.

The summary is the wrapper hook: grep the report for `: changed` to know which slugs to re-convert (pagespeak) and re-index.

## Auditing deliverables

`pagespring audit [<slug>|--all]` runs deterministic, $0 checks over staged deliverables — no network, no LLM, read-only. It catches what a glance at `status` can't:

- **errors** (the deliverable can't be trusted): `manifest_missing`, `deliverable_missing`, `deliverable_empty`, `sha_mismatch` — the on-disk file no longer hashes to the recorded hash: `localized_sha256` once an image pass recorded one, the staged `sha256` while un-localized (hand-edited or corrupted) — `crawl_truncated`, a crawl that hit its page cap or stalled — `pages_lost`, pages discovered but never staged because the source errored mid-crawl, which no content check can see — `single_page_crawl`, a crawl pattern that returned exactly one page (the too-specific-seed signature: point `llms_txt` at one doc page instead of the index and it fetches that page's `.md` twin, staging 1 page where the site has 170; PDF deliverables, `single_fetch` patterns, and sources the acquire marked `single_document` — a blog post, an article, or an `llms-full.txt` whose body IS the documentation — are one file by design and never fire it) — `broken_image_ref`, a local `images/<name>` ref (`<img src>`, SVG `<image href>` or markdown) whose file is missing — a figure a doc archive names but doesn't ship included (invisible to the remote-ref count, so a fully localized deliverable could still ship dead images) — and `duplicate_source_url`, two slugs staged from the same source, however its URL is spelled (scheme, `www.`, host case, trailing slash and fragment fold away, and a local file compares by resolved path, as the ingest guard compares them), which blocks the hand-off for both.
- **warnings** (real but survivable): `localize_incomplete` (localized images recorded but remote refs remain — re-run `localize`), `localize_interrupted` (an image pass was cut short before recording its outcome — re-run `localize` to resume it), `sha_unverified` (an image pass ran but left neither a `localized_sha256` nor an open-pass mark — a manifest older than schema v7 — and the file no longer matches the staged `sha256`; the divergence may be the pass's own re-pointing, but nothing on disk can tell, and a plain `ok` would read as verified — re-ingest with `--download-images` to record one; `localize` over it would record whatever is on disk), `no_headings` (a multi-page crawl normalized to heading-less soup — the half-lost-crawl signature; it will split into nothing downstream), `duplicate_content` (two slugs whose manifests record the same `sha256` — content identity as normalize produced it, before any image pass re-pointed refs, so it is not a comparison of the bytes now on disk; legitimate when a vendor mirrors one manual at two URLs).

`duplicate_content` and `duplicate_source_url` are **corpus-level**: they compare slugs against each other, so they only appear under `--all`.

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

Any http(s) URL that no specific pattern claims classifies to `docs_probe` rather than going unmatched — `classify` prints `docs_probe`, meaning "will content-probe the site at acquire," not a confirmed source type. The actual routing happens during `ingest`: `docs_probe` fetches the base page and works from the most specific evidence to the least — content type first (PDF magic bytes, for a "docs" URL that serves a PDF), then an API reference UI (a Swagger UI, Redoc or Scalar page, whose named spec is ingested instead), then the asset tells of vendor tools that emit no generator tag, then `<meta name="generator">`, then the weaker fallback tells (a `search/search_index.json`, an `llms.txt`). Don't mirror the ladder here; a site none of it recognises exits `2` printing exactly what was probed, and that message is the live list (and the guidance for authoring a new pattern — see [architecture.md](architecture.md#adding-a-new-pattern)).

`classify` returns no pattern only for a non-web argument nothing claims — every http(s) URL is routed, since any URL the specific patterns decline falls through to `docs_probe`.

To see where `docs_probe` would send a URL without crawling it, add `--probe`: it fetches the entry page (plus the search-index and `llms.txt` probes the ladder needs) and prints the route, the evidence it matched on, and every `<meta name="generator">` tag:

```
$ pagespring classify --probe https://www.mkdocs.org
docs_probe
route    : mkdocs (via search_index)
```

A URL a specific pattern claims prints only that pattern, since its URL already decides the route. `--probe` exits `2` for a site no rung recognises (printing what was probed, and any generator tags the page carries) and `4` when the fetch fails.

## Exit codes

`refresh` reports per-slug outcomes instead of failing fast: exit `0` for a clean sweep, `1` when any slug failed (the report names them; a failure outranks every `2` below that arises during the sweep), `2` when a slug you named can't be refreshed (no readable manifest, or a name that folds to nothing — refused before any fetch), when `--all` or `--pattern` found no slugs to sweep, when `--pattern` names no registered pattern, when slugs are mixed with `--all`/`--pattern`, or when none of them was given. As with `audit`, an empty corpus is `2` and never a clean sweep.

`ingest --batch` reports per line, as `refresh` does: exit `0` when every line staged, was unchanged or was skipped as a repeat; `1` when any line failed; `2` when the file can't be read or holds no URLs, or when a URL argument or `--slug` is given beside `--batch`.

`audit` exits `0` when it ran, `1` under `--strict` when any error-level finding exists, and `2` when `--all` found no slugs to audit — `2` means "could not do what you asked", never "clean".

`ingest` and `renormalize` distinguish failure modes so scripts (and you) can tell them apart:

- `2` — no pattern matched a local file/`file://` argument, `docs_probe` couldn't recognise the site's generator at acquire time, a crawl captured none of the source's articles (a hub whose shape changed, or a site that quota-blocked every fetch — nothing is staged, so the prior deliverable survives), a URL/file routed to `api_spec` that isn't a recognizable OpenAPI/Swagger/Postman document, an API reference page naming no parseable spec or several (the message lists them), a refused slug takeover (the slug holds a different source and `--replace` wasn't given; the message names what it protected), a same-source re-crawl that found far fewer pages than the staged manual (nothing staged; `--replace` accepts it), or a doc archive that is not a valid zip/tar/epub, has a member escaping the extraction root, or exceeds the extraction budget (`PAGESPRING_MAX_EXTRACT_BYTES`). For `renormalize`: the slug was never ingested, has no kept `raw/`, its manifest is missing required fields, or its recorded pattern is no longer registered. For `localize <slug>`: the slug has no usable manifest, or its deliverable is missing or no longer matches its recorded hash.
- `3` — normalize produced an empty file (the source likely changed shape; nothing staged, a prior deliverable survives).
- `4` — a network fetch died during acquire (nothing staged; `ingest` only — `renormalize` never touches the network).

These rely on pf-core's `run_cli` propagating `typer.Exit` codes; without it a failed `ingest` would exit `0`.

A run stopped by SIGTERM or SIGHUP exits `128` plus the signal number (`143`, `129`) after its cleanup runs: an image pass records its outcome, and staging never leaves a half-written deliverable. A signal the caller ignores (`nohup`) stays ignored.

`localize` is the exception to the empty-corpus rule above: `localize --all` over an empty or missing `incoming/` exits `0` having done nothing, so a wrapper cannot read its `0` as "every image is local" the way it can for `audit` and `refresh`.

A malformed invocation — unknown option, missing argument — also exits `2`, with a usage message rather than a traceback. So `2` means "the command couldn't proceed with what it was given", whether that's the argv or the source.

Every command that takes a `<slug>` folds it before it names a directory, so a slug argument that folds to nothing (`..`, `.`, `///`) exits `2` on all of them rather than resolving to the corpus root. Folding also **caps the slug at 100 characters** (trailing dash trimmed), which is what keeps a source titled in a full paragraph from naming a file the filesystem refuses.
