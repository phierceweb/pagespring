# Changelog

All notable changes to **pagespring** are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/); the project aims to follow
semantic versioning.

## [0.9.0] — 2026-08-05

### Added

- **Three new source shapes** — `docs_probe` recognizes **SCHEMA ST4** (Quanos)
  HTML manuals by content tells, walking `js/treedata.json` and fetching only
  the tree's leaves; **WordPress** by generator meta or the `wp-json` link its
  head declares, acquiring one post through that endpoint; and **Asciidoctor**
  by generator meta, taking either a single self-contained file or a crawl of
  the sibling `.html` pages sharing the entry page's directory.
- **Every fetch carries a size cap**, with separate budgets for text fetches
  (HTML, sitemaps) and binary downloads (PDFs, archives, images). The cap bounds
  the decoded body as well as the wire read, so an oversized or compressed
  response fails the acquire rather than exhausting memory as it inflates.
  `PAGESPRING_MAX_TEXT_BYTES` and `PAGESPRING_MAX_DOWNLOAD_BYTES` override the
  defaults — see `.env.example`.
- **`bin/check-framework`** refuses code that hand-rolls what pf-core provides —
  banned imports, builtin raises in library code, `os.environ` reads, non-atomic
  JSON writes. Runs in `bin/lint`, pre-commit and CI; each failure names its
  replacement, and an exemption is per file with a stated reason.

### Changed

- **Manifest schema v6** — adds `single_document` (the source is one article
  rather than a crawled index, so `audit`'s `single_page_crawl` check treats a
  `pages: 1` deliverable as correct), `kept_raw` (`raw/` was staged, so
  `renormalize` can replay offline), `lost` (pages discovered but never staged),
  and `localized_sha256` (the deliverable's hash after an image pass re-pointed
  its refs). `kept_raw` is read from the staged directory, not the flag.
  `status` marks those slugs `raw`. All additive; read them with `.get`.
- **`--keep-raw` is ignored for PDF deliverables.** `pdf_url.normalize` returns
  the downloaded file unchanged, so a kept `raw/` would duplicate the staged
  PDF without enabling anything.
- **pf-core pin raised to `~=0.18.1`.** TLS verification is now pinned on per
  `Fetcher`, so pf-core's process-wide `PF_VERIFY_TLS` (legacy
  `URL_CHECK_VERIFY_TLS`) can no longer disable certificate checks for an
  ingest. `ClientError` — raised for a malformed, truncated, or over-cap body —
  is reported as an acquire failure rather than a traceback. 0.18 also stops the
  localizer skipping an extensionless CDN ref whose basename carries a dot.

### Fixed

- **Responsive images reach the localizer.** Normalize reduces every
  `<picture>`, `srcset`/`data-srcset`, and `data-src` to a plain `<img src>`
  before absolutizing. A base64 spacer in `src` yields to the real image in
  `data-src`; a `<source media="(not all)">` variant yields to the rendered
  `<img>`, dropping the `originalimagename` build attribute with it. An `<img>`
  with no usable reference is dropped rather than staged empty.
- **The widest available rendition is downloaded.** Candidates are ranked by
  declared width — a `srcset` `w`/`x` descriptor or a CDN sizing parameter
  (`wid=`, `width=`). An already-localized ref is never swapped back to remote.
- **Image URLs are unescaped before fetching**, so a ref carrying `&amp;`
  resolves with its CDN sizing parameter intact.
- **`localize` is an explicit no-op for PDF deliverables** — a PDF carries its
  images inline. `localize --all` no longer aborts on the first PDF slug.
- **`audit --all` and `refresh --all` over an empty or missing `incoming/` exit
  `2`**, with a message naming the path they looked in.
- **The Asciidoctor crawl honours `CRAWL_STALL_AFTER_S`** like the other
  queue-driven crawls; a stalled crawl is marked `truncated`.
- **`manifest.json` and the image sidecar are written atomically.** The manifest
  is written before the image pass and kept through a re-ingest's clear, so an
  interrupted run still leaves provenance.
- **Every slug-taking command folds its argument** through one resolver
  (`pagespring.paths.slug_dir`) — `renormalize`, `localize`, `refresh` and
  `audit` included. A slug that folds to nothing exits `2`.
- **`ProgressWatchdog` raises `InvalidInputError`** on a negative window, not
  `ValueError`.
- **Every derived slug is folded, not just a `--slug` override.** A
  pattern-derived slug is passed through `slugify`; one that folds to nothing
  exits `2`. The slug names the directory an ingest clears, so a
  remote-controlled `..` is refused. A slug staged unfolded renames its
  directory on re-ingest.
- **Pages lost mid-crawl are recorded and audited.** Crawl patterns count pages
  discovered but never staged into `lost`, the manifest carries it, and `audit`
  reports `pages_lost` as an error. A page that fetches but yields no content
  container counts too. Losses nothing can enumerate set `truncated` instead: a
  Microsoft Support sitemap that 403s mid-pagination, an unreadable Hugo child
  sitemap, and OpenStax's next-link chain, where a dead link strands every page
  after it.
- **`ingest --download-images` is idempotent** — it shares one image pass with
  `localize`, including the sidecar-reuse probe and the orphan sweep.
- **A localized deliverable is integrity-checked.** `audit` checks
  `localized_sha256` once `images > 0`. `localize_incomplete` is gated on
  `images/` existing rather than on a non-zero count, and counts remote refs
  itself rather than through the localizer's matcher, so a ref the localizer
  declines to download is still reported.
- **Reusing a cached image rewrites only whole refs**, so a URL containing
  another image's URL as a prefix is left alone. The conditional-GET probe sends
  the decoded URL the stored validators describe, so a ref carrying `&amp;` can
  304.
- **`docs_probe` requires a real Sphinx tell** — a `_static/` asset path
  anywhere in the document no longer routes a site to the Sphinx crawler.
- **Raw filenames are flattened in `_st4` and `_clickhelp`** — path separators
  are dropped from the remote-controlled id the filename is built from.
- **GitBook logs a failed rendered-page fetch** — the page's text still
  converts, but its image refs stay unresolved.
- **Local image names are case-stable** — two URLs differing only in case
  resolve to one file, as they do on a case-insensitive filesystem.
- **New audit check `broken_image_ref`** — an `images/<name>` ref whose file is
  missing, which the remote-ref count cannot see.
- **`zendesk_help` declines article attachments.** `/hc/…/article_attachments/`
  URLs are binary files; they fall through to `docs_probe`, which routes them by
  content.
- **A Zendesk article URL scopes to that article** instead of paging the whole
  help center. Single-article ingests slug as `<host>-<article>` and set
  `single_document`.

## [0.8.0] — 2026-08-01

### Added

- **Four new source shapes** — `docs_probe` recognizes **Hugo**, **ClickHelp**, and
  **Paligo** by content (none emit a generator tag), and a top-level
  **`adobe_helpx`** pattern acquires helpx.adobe.com product guides from their
  TOC index.
- **Image sidecar** — `incoming/<slug>/images.json` records each localized
  image's source URL, validators, and sha256. `localize` reuses unchanged images
  via conditional GET, replaces changed ones, and prunes orphans once a document
  is fully localized; the CLI reports reused/pruned counts.
- **Re-ingest keeps the image cache** — `images/` and the sidecar survive
  re-ingest and `refresh`, so unchanged images are not re-downloaded. A re-ingest
  still resets `images` to 0 and restores absolute refs; re-run `localize`.
- **Crawl liveness** — queue-driven crawls bail when no page lands for
  `CRAWL_STALL_AFTER_S` (`0` disables) instead of hanging on a trickling server.
  A stalled crawl is marked `truncated`.
- **Real PDF page counts** — `pages` on a PDF deliverable is the document's page
  count, not the file count. `None` when a PDF is unreadable.
- **New audit checks** — `crawl_truncated`, `single_page_crawl`, and the
  corpus-level `duplicate_content` / `duplicate_source_url`, which compare slugs
  against each other and so require `--all`.
- **`pdf_url` rejects non-PDF payloads** (magic bytes) — a "PDF" URL that
  redirects to an HTML landing page exits 2 instead of staging a fake `.pdf`.
- **`zendesk_help`** can scope a crawl to a section or category URL;
  **`github_markdown`** detects truncated listings; **`apple_help`** dedups the
  two URL forms of one topic.

### Changed

- **Manifest schema v5** — `truncated` added (v4); `convert_recipe` removed (v5,
  the first non-additive change). pagespring records what a source is; pagespeak
  decides how to convert it. `pagespring patterns` lists names only.
- **Deliverables carry no scripts, styles, or site chrome** — normalizers strip
  `<script>`/`<style>`/`<noscript>`, and the per-page furniture each source
  appends: Adobe's feedback widget, pagination, social share, promo cards and CTA
  footer; Apple's PDF download block; Hugo's sidebar nav; Zendesk's
  author-supplied embeds.
- **`docs_probe` routing** — PDF payloads hand off to `pdf_url`, and the
  ClickHelp and Paligo content tells are checked before the generator sniff.
- **`adobe_helpx` declines `.pdf` URLs** so helpx PDFs keep routing to `pdf_url`.
- **pf-core pin raised to `~=0.15.1`**; new runtime dependency `pypdfium2`.

### Fixed

- **`_hugo` crawled `/categories/` and `/tags/`** — Hugo's generated taxonomy
  list pages are indexes of a manual, not part of it, and each duplicated the
  home page.
- **`_mkdocs` emitted every page twice** — the search index's page-level record
  repeats the text of each of its sections; only the lead prose is kept.
- **`archive_download` produced invalid, mis-ordered output** — members were
  concatenated as whole standalone documents in lexical filename order. Now one
  document, in EPUB spine order where an OPF is present.
- **`llms_txt` / `gitbook` silently dropped pages** — a URL whose `.md` sits in
  the fragment is an in-page anchor, not a page; it was fetched and counted, then
  skipped by normalize's `*.md` glob.

## [0.7.0] — 2026-07-24

### Changed

- **`pagespring.http` is a shim over `pf_core.fetch`** — unchanged public names,
  signatures, and defaults (`fetch_text`, `fetch_bytes`, `fetch_bytes_meta`,
  `not_modified`, `Validators`, `polite_sleep`), with the `PAGESPRING_UA` identity
  and the polite crawl delay still owned here. Raw `urllib` exceptions keep
  propagating, so patterns still branch on `HTTPError.code`.
- **Fetched URLs are SSRF-guarded** — private, loopback, link-local, and
  unresolvable hosts are refused before any request goes out, on the initial URL
  and on every redirect hop (redirects are now walked explicitly). A refused URL
  raises `InvalidInputError` → CLI exit 2; `URL_FETCH_ALLOW_PRIVATE=1` opts out
  for a deliberately internal source.
- **`pagespring.images` is a shim over `pf_core.fetch.images`** —
  `download_images` and `count_remote_images` keep their signatures, naming
  scheme, and file-as-ledger resume; deliverable and image writes are now atomic.
  Localizer log events are `doc_images_localized` / `image_localize_failed`.
  Refs with a non-image extension (`.bmp`, `.tif`, `.tiff`) are no longer
  localized, and no longer counted as remaining.
- **pf-core pin raised to `~=0.13.0`** — for the fetch core and image localizer.

### Fixed

- **Image localization no longer rewrites bare image URLs in prose** — only
  markdown `](…)` and `<img src=…>` refs are retargeted, so a doc that quotes or
  links an image URL in its text keeps it intact.

## [0.6.0] — 2026-07-20

### Added

- **`ingest --slug <name>`** — override the derived slug (folded to
  kebab-case); names the `incoming/` dir and the deliverable file.
- **Stage-time duplicate detection** — an ingest whose content is
  byte-identical to another slug's manifest warns
  `content identical to incoming/<other>/` (still staged; the warning is the
  signal). Result field `duplicate_of` carries it for library callers.

### Changed

- **`refresh` pins the recorded slug**: the re-ingest is forced into the
  existing slug, so a retitled source or a `--slug` override refreshes in
  place instead of staging a duplicate dir. The `moved` outcome is retired.
- **The deliverable always stages as `<slug>.<ext>`** regardless of what the
  pattern's normalize named its output (patterns that name files at acquire
  time can't see a `--slug` override).
- **pf-core floor raised to `~=0.11.0`** — tracks the current minor line; no
  new APIs consumed (the suite already runs against 0.11.0).

## [0.5.0] — 2026-07-19

### Added

- **`audit [<slug>|--all] [--strict]`** — deterministic $0 checks over staged
  deliverables (read-only; no network, no LLM). Errors: missing manifest,
  missing/empty deliverable, sha mismatch on an un-localized file. Warnings:
  unfinished localize (remote refs remain), multi-page deliverable with zero
  headings. Report-only by default; `--strict` exits 1 on any error-level
  finding to gate the pagespeak hand-off.
- **`llms.txt` at the repo root** — AI-discovery index of every shipped doc
  (completeness enforced by `tests/test_llms_txt_index.py`).

## [0.4.0] — 2026-07-19

### Added

- **`refresh [<slug>|--all]`** — re-check ingested manuals against their
  recorded sources and re-stage what changed. One outcome line per slug
  (`changed` / `unchanged` / `moved` / `failed` / `skipped`) plus a summary;
  per-slug failures don't stop the sweep; the kept-raw property survives a
  refresh. Exit `1` when any slug failed, `2` when a named slug can't be
  refreshed.
- **Conditional-GET fast path.** `pdf_url` and `archive_download` acquires now
  record the response's `ETag`/`Last-Modified` (manifest schema v3, additive);
  `refresh` probes those sources with one conditional GET and a definitive 304
  skips the re-download entirely. Crawl sources always re-crawl — an entry
  page's validators prove nothing about the rest of a site.

## [0.3.0] — 2026-07-19

### Added

- **`renormalize <slug>`** — re-run the pattern's current normalize against the
  kept `incoming/<slug>/raw/` and re-stage the deliverable, with no re-crawl
  (requires an ingest made with `--keep-raw`). Byte-identical output re-stages
  nothing and reports `unchanged`; changed output replaces the deliverable and
  refreshes the manifest's content facts (localized-image count resets — re-run
  `localize`). Exit `2` when the slug/raw/pattern precondition fails, `3` on
  empty output (prior deliverable survives).
- **Manifest schema v2: `title`.** The manifest now records acquire's source
  title, so a `renormalize` replay reproduces the deliverable's heading instead
  of degrading it to the slug. v1 manifests (no `title`) replay with the
  slug-fallback heading.

## [0.2.0] — 2026-07-19

### Changed

- **Slug folds unified on pf-core's `slugify`** (pf-core floor raised to
  `~=0.9.0`): `pdf_url`, `archive_download`, `github_markdown`, `api_spec`,
  and `zendesk_help` share one fold. ASCII inputs slug identically;
  accented input folds to ASCII (`Café` → `cafe`).

## [0.1.2] — 2026-07-15

### Changed

- **README** — add a PyPI version badge and switch the docs and pf-core links to
  absolute URLs so they resolve on the PyPI project page.

## [0.1.1] — 2026-07-12

### Fixed

- **Microsoft 365 pattern** — article images served as relative `media/…`
  paths (e.g. Sway, Publisher) are now made absolute against the article URL,
  so the deliverable's asset refs resolve and `--download-images` can fetch them.
- **Microsoft 365 pattern** — a throttle (403) or network error while
  paginating a product sitemap now logs `microsoft_support.sitemap_error`
  rather than silently truncating the article catalog; the expected
  end-of-pagination 404 stays quiet.

## [0.1.0] — 2026-07-12

Initial public release.

- **Pipeline** — `ingest <url>`: classify → acquire → normalize → stage ONE
  clean HTML/markdown/PDF deliverable with absolute asset URLs under
  `incoming/<slug>/`, plus a `manifest.json` provenance record (source URL,
  pattern, `convert_recipe`, page count, `sha256`, ingest time).
  `--keep-raw`, `--download-images`, and `--if-changed` (skip re-staging when
  the re-fetch normalizes byte-identical) flags.
- **Source patterns** — Apple support User Guides, GitBook (hosted +
  custom-domain via llms.txt), `llms.txt` docs sites, Read the Docs (PDF build
  with Sphinx-crawl fallback), GitHub markdown repos, Zendesk Help Centers,
  Microsoft 365 support, OpenStax textbooks, OpenAPI/Swagger specs + Postman
  collections (URL or local file), direct PDF links, doc archives
  (zip/tar/epub), and a content-probing `docs_probe` catch-all
  (MkDocs/Docusaurus/Sphinx generator sniffing). List them live with
  `pagespring patterns`.
- **CLI** — `ingest`, `localize` (resumable post-hoc image download,
  `--all`), `patterns`, `classify`, `status`.
- **Polite fetching** — stdlib `urllib` only; identifying
  `pagespring/<version>` User-Agent (`PAGESPRING_UA` override), 429
  `Retry-After` honored, backoff on 5xx, paced crawls, size caps that warn
  when they truncate.
- **Exit codes** — `2` unrecognized source, `3` empty normalize (nothing
  staged; a prior deliverable survives), `4` fetch failure during acquire.
