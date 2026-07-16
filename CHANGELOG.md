# Changelog

All notable changes to **pagespring** are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/); the project aims to follow
semantic versioning.

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
