# Changelog

All notable changes to **pagespring** are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/); the project aims to follow
semantic versioning.

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
