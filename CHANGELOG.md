# Changelog

All notable changes to **pagespring** are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/); the project aims to follow
semantic versioning.

## [0.14.0] — 2026-10-04

### Added

- **`docs_probe` recognizes MadCap Flare HTML5 help** by the runtime attributes on
  every page, from its entry shell or any topic URL, and acquires the topics its
  TOC data lists in tree order, nested at their depth, with no crawl.
- **`docs_probe` recognizes Fluid Topics portals** by their app shell and reads
  the publication a `/r/` or `/reader/` URL names through the portal's API, in
  tree order. Inline images are bundled as files beside the deliverable.

### Changed

- **pf-core pin raised to `~=0.25.0`.**

### Fixed

- **`--if-changed` and `refresh` re-stage a bundled image whose bytes changed**
  under the same name; an unchanged deliverable no longer keeps the old image.
- **A Writerside topic skipped as a repeat, a duplicate or a failed fetch still
  heads the topics under it**, so they keep their nesting.

## [0.13.0] — 2026-09-30

### Added

- **`ingest --batch <file>`** ingests each URL or path in a file, one per line;
  blank lines and `#` comments are skipped. The ingest flags apply to every line,
  lines are paced, a failed line is reported and the batch carries on, and a
  repeated URL is skipped. No line takes over a slug an earlier line staged, even
  with `--replace`. One status line per URL, then a summary; exit 1 if any
  line failed, 2 if the file can't be read or holds no URLs, or if a URL or
  `--slug` is given beside `--batch`.
- **`docs_probe` recognizes more documentation platforms**, each acquired from
  the most complete ordered source it publishes:
  - **mdBook**: the whole book from `print.html`, else a crawl in TOC order.
  - **Writerside**: topics from `HelpTOC.json`, in TOC order and nesting; a
    starting page keeps its cards' one-line descriptions.
  - **Antora**: the one component version the URL names, in nav order, plus
    pages only the component's sitemap lists.
  - **Starlight**: the sitemap in sidebar order, scoped to the seed's locale.
  - **VitePress**: the server-rendered sidebars, the entry's section first.
  - **Docsify**: the raw markdown the sidebar files list (the one in each page's
    directory and above it), routed as the runtime routes it, `ext` included,
    with `:include` embeds inlined and `:omitFragmentLine` honoured. A page only
    another page's text links to is staged after it when it sits in a directory
    the sidebar covers; a dead link there is not a lost page.
  - **MediaWiki**: the seed page plus the pages it links to, through the action
    API.
  - **Docsy** without a Hugo generator tag goes to the Hugo crawl.
- **`sitemap_crawl`**: a URL naming a sitemap file (`sitemap.xml`,
  `sitemap-index.xml`, …) crawls the pages it lists under its directory and
  keeps each page's main content.
- **MathJax formulas keep their text**: a formula drawn as SVG glyphs is rebuilt
  as MathML (Writerside, and pages extracted as readable content).
- **VitePress and readable extraction flatten shiki code blocks** to plain text,
  keeping the language and line breaks.
- **`github_markdown` reads MDX**: `.mdx` files are listed beside `.md` (and a
  `/blob/` URL naming one is claimed), reduced to markdown with imports,
  exports and component tags dropped and their text kept. A directory's
  `index.md`/`index.mdx` leads it, and a page's front matter gives way to its
  `title` as the page heading (`.md` pages too).
- **`PAGESPRING_MAX_EXTRACT_BYTES`** sets a doc archive's extraction budget.
- **`refresh` takes several slugs, or `--pattern <name>` (repeatable)** to sweep
  only the slugs a pattern acquired, so the one-request PDF sources can be
  re-checked apart from the crawls. An unknown pattern name, slugs mixed with
  `--all`/`--pattern`, or a named slug with no readable manifest exits 2 before
  anything is fetched.
- **`classify --probe <url>`** names the route `docs_probe` would take — the
  evidence it matched and every generator tag — from the entry page and the
  probes the ladder needs, without crawling. Exit 2 when nothing recognizes
  the site, 4 when the fetch fails. An unrecognized site's refusal, from
  `ingest` too, names the generator tags the page carries.

### Changed

- **`docs_probe` reads every `<meta name="generator">` tag**, not just the
  first, so a platform that names itself after its framework (Astro, then
  Starlight) is visible to detection.
- **`docs_probe` follows a same-site `<meta http-equiv="refresh">` entry page**
  before detecting; a refresh inside `<noscript>`, or a delayed one (a session
  timeout), is not followed. A PDF reached through one, even one too large to
  read as a page, keeps no validators, so `refresh` re-fetches it.
- **The Hugo crawl** stages pages in the theme sidebar's order and caps after
  ordering, so a capped crawl keeps the first pages in reading order. It reads
  Docsy content from `div.td-content`, strips Docsy, Hugo Book and Geekdoc
  chrome, stages each URL spelling of a page once, skips a page with nothing to
  read, and drops a list page once every page it lists is staged.
- **`slug_from_host` drops a leading `wiki` label**, as it drops `docs` and `help`.
- **pf-core pin raised to `~=0.24.0`.** A validator probe (`refresh`, and the
  image re-check on a re-localize) also counts a 200 that carries the strong
  `ETag` it sent as unchanged, so a server that ignores conditional requests no
  longer forces a re-download.

### Fixed

- **A section URL on a site whose `llms.txt` sits above it stages that section**,
  not the whole index; the host match ignores `www.` and the scheme.
- **Link rewriting leaves indented code blocks as written**, and resolves a link
  whose label is a code span.
- **A leading `---` block is front matter only when it reads as YAML keys**, so a
  callout framed by rules (`Note: …`) keeps its text. Docsy's per-page
  `llms.txt` pointer is dropped.
- **An EPUB or doc-archive deliverable keeps its figures**: image refs naming
  archive members are copied into `incoming/<slug>/images/` and re-pointed (on
  `ingest` and a changed `renormalize`), a missing member audits as
  `broken_image_ref`, and the manifest records the images and their hash. In a
  markdown member every ref outside code is re-pointed, and an rst member's
  `image` and `figure` directives are bundled. An EPUB is titled from its OPF
  and leaves out its navigation document and Project Gutenberg's license
  boilerplate.
- **Links between EPUB or HTML-archive members point inside the deliverable**
  (`chapter.xhtml#x` becomes `#x`). An id an earlier member already holds is
  renamed, with the links to it, so each link lands in the member it names.
- **SIGTERM and SIGHUP stop a run cleanly** (exit 128+signal); an image pass cut
  short keeps its integrity record.
- **A killed image pass is told apart from damage**: `localize` records the pass
  as open in the manifest (an optional `image_pass_open` field; manifest schema
  v7) instead of clearing the hash, so a SIGKILL mid-pass resumes on the next run
  while a file changed anywhere else still refuses. `audit` reports an open pass
  as `localize_interrupted`.
- **Apple `lost` counts the union of fetch failures, TOC topics never saved and
  merge drops**, recorded in `raw/`, so `renormalize` can lower it. An Apple
  ingest with `--slug` checks the guide's own TOC.
- **Paligo topics drop their breadcrumb.**
- **OpenAPI parameter tables escape `|` and fold line breaks in every cell**, so
  a 3.1 type list (`integer | null`), a description containing a bar, or one
  with `\r\n` line endings no longer shifts or splits the row.
- **An unchanged re-fetch records the `ETag`/`Last-Modified` it was served**
  (`ingest --if-changed`, `refresh`), so a source that re-stamps an identical
  file answers the next refresh probe with a 304 instead of a full download.
- **`refresh` probes the stored validators of any PDF deliverable**, including
  one `docs_probe` found at an extensionless URL, instead of re-downloading it
  on every sweep.
- **`docs_probe` waits the polite delay between its last probe and the first
  request of the strategy it hands off to.**
- **`audit`'s `duplicate_source_url` matches every spelling of one source** the
  ingest guard treats as the same (`http://www.` and `https://` of one URL, a
  trailing slash, a fragment; a local file by resolved path).
- **A Hugo child sitemap over the size cap or with a damaged gzip body marks the
  crawl truncated** instead of failing the ingest, as an unreachable one does.
- **A Hugo or `github_markdown` page at a very long path stages** instead of
  failing the ingest on the file-name limit.

## [0.12.0] — 2026-09-14

### Changed

- **Relicensed to Apache-2.0** (was MIT). Releases through 0.11.0 stay under MIT;
  the change applies from this release forward.
- **A same-source re-ingest that finds far fewer pages than the staged manual is
  refused** (exit 2; `refresh` reports `failed`), keeping the manual. `--replace`
  accepts it; `COLLAPSE_KEEP_PCT` and `COLLAPSE_MIN_PAGES` tune the check. For a
  single-fetch source (PDF, doc archive, API spec) the message describes the
  re-fetched document, and the hint adds `--keep-raw` when the slug holds `raw/`.
- **Staging is atomic.** Re-ingest and `renormalize` write the new deliverable
  before clearing stale files, so a full disk or a kill keeps the previous one; an
  interrupted `--replace` takeover leaves the displaced manual under its manifest.
- **`localize <slug>` exits 2 when it refuses the slug.** `audit`'s `sha_unverified`
  warning advises re-ingesting with `--download-images`.
- **`ingest --download-images` prints this run's downloads apart from the total**
  in `images/`.
- **OpenAPI deliverables open with a `## Tags` section** for described tags, show
  request-body descriptions and 3.1 type lists (`string | null`). Postman
  deliverables fence raw bodies in their declared language and mark disabled
  parameters.

### Added

- **Swagger UI, Redoc and Scalar pages ingest the spec they name**, whether
  `docs_probe` or `api_spec` (a `/swagger-ui.html` or `/swagger/` path) claims the
  page. The page's `url` query picks the spec; otherwise Redoc/Scalar attributes,
  Swagger UI config documents (`configUrl`) and quoted spec URLs in the page or its
  initializer are candidates, never Swagger's Petstore demo. Each is proven to parse
  as OpenAPI; a page naming several parse-proven specs, or none, exits 2 listing
  what it found.
- **`docs_probe` ingests an OpenAPI document served from an extensionless path**
  (springdoc's `/v3/api-docs/<group>`) — one with a version and paths, not an API
  root that only links to its spec.

### Fixed

- **`apple_help` crawls the version-less topic links Apple's guides publish**, for
  the platform the URL names (`mac`, `ios`, `ipados`, `watchos`, …) or, for a URL
  naming none (`/guide/iphone/`), the platform and locale Apple redirects it to —
  including topic tokens with an underscore. A topic-page seed also fetches its
  release's welcome page for the outline and title, a locale seed stays in its
  locale, a crawl that captures no topic page exits 2 without staging, and TOC
  topics the crawl never saved count as `lost`.
- **`api_spec` refuses a body that is neither JSON nor YAML** (an HTML error page
  served in place of the spec) with exit 2 instead of a traceback.
- **`refresh --all` reports a slug whose refresh raises as `failed`** and sweeps on.
- **Image reuse keeps a cached image until its replacement is in hand**
  (`--download-images`, `localize`). An image the server doesn't confirm unchanged
  is fetched: identical bytes reuse the file, changed bytes replace it under the same
  name, and a failed fetch or a non-image body keeps the file and its record. Each
  probe and fetch is paced with the crawl delay. An interrupted image pass records
  every image that landed in `images.json`.
- **`docs_probe` reads the `llms.txt` nearest the URL's path**, so a docs subpath's
  own index wins over the site root's. It skips rendered-page fetches when a
  generator tag names a platform other than GitBook. GitBook acquisition — hosted
  `*.gitbook.io` sites and the `llms.txt` route alike — caps at 1000 pages
  (`truncated`). Every request on the spec and llms.txt routes is paced.
- **Markdown from llms.txt platforms is cleaned** (`gitbook`, `llms_txt`, the
  `docs_probe` llms.txt route): notes sending AI clients to an llms.txt index, YAML
  front matter (a leading `---` block that parses as a mapping), and top-level MDX
  `import`/`export` definitions outside code fences are dropped. Page-relative links
  resolve against the page URL — never inside code, and not in `llms-full.txt`.
- **`refresh`, `ingest --if-changed` and `renormalize` compare the file on disk**
  with its recorded hash: a missing or altered deliverable is re-staged rather than
  reported `unchanged`, and a 304 validator probe is trusted only for an intact one.
  A deliverable localized without a recorded hash is left in place.
- **`localize` refuses a deliverable that no longer matches its recorded hash**
  instead of recording the damage as verified; `--all` reports a slug with an
  unusable manifest and carries on. `renormalize` records the `lost` count
  normalize derives.
- **`archive_download` refuses unsafe or invalid archives with exit 2**: a
  declared size, member count or compression ratio over the extraction budget, a
  non-archive body, a corrupt archive, or a member escaping the extraction root.
  EPUB spine hrefs are percent-decoded, keeping chapter order.
- **Routing:** `github_markdown` claims only a repo root, `/tree/` and `.md`
  `/blob/` URLs, leaving release assets, `/raw/` files and source archives to their
  extension patterns; `readthedocs` leaves spec, archive and PDF file URLs to theirs
  and fetches a subproject URL's own build at its version; `pdf_url` claims only
  `.pdf` paths and Read the Docs PDF builds (`docs_probe` hands any other PDF to it,
  including one too large for the text budget); `zendesk_help` claims only paths
  starting `/hc/` on other hosts and exits 2 on a non-JSON API answer.
- **Crawl order and coverage:** Sphinx crawls stage pages in toctree order;
  `github_markdown` keeps `.MD` files and orders numbered files numerically; Hugo
  keeps `tags`/`categories`/`print` pages below the site root; Docusaurus keeps doc
  slugs that start like a version.
- **`microsoft_support` paces every sitemap page and article request**; a sitemap
  page that is not a `<urlset>` marks the crawl truncated, and a sustained 403
  block — confirmed by re-checking an article that loaded earlier (or the next one),
  since a restricted article also answers 403 — stops it with the unfetched articles
  counted as `lost`.
- **API spec rendering:** OpenAPI resolves `$ref` request bodies and path items and
  ignores wrong-typed fields instead of crashing; Postman renders folder and
  request descriptions, query parameters, path variables, and every body mode.

## [0.11.0] — 2026-08-30

### Changed

- **Slugs fold to at most 100 characters**, trailing dash trimmed — derived
  slugs and `--slug` alike. A source whose title exceeds the cap ingests instead
  of dying on the filesystem's name limit; it stages under the truncated name.
- **`--if-changed` answers `unchanged` only while the deliverable it describes is
  on disk.** A slug that lost its file re-stages.
- **A local-path ingest records the resolved absolute path as `source_url`.**
  `refresh` replays that value and the slug-takeover guard compares it, neither
  of which a bare relative path survives. Manifests from earlier versions keep
  their literal argument; the next refresh rewrites it.
- **`archive_download` picks the deliverable's kind by which member family
  carries the archive.** A markdown collection shipping one `search.html` stays
  markdown, and packaging files (`README`, `LICENSE`, …) don't count toward the
  text family. `pages` counts the family that won.
- **`microsoft_support` refuses a crawl that captured no article** (exit `2`).
  Nothing is staged, so the previous deliverable survives.
- **`github_markdown` accepts `/blob/` URLs**: a blob naming a `.md` file scopes
  the crawl to that file's directory on that branch. A blob naming anything else
  is declined, so a spec, PDF or archive committed to a repo routes to its own
  pattern.
- **A `github_markdown` ingest scoped to a subdirectory slugs as
  `<owner>-<repo>-<subdir>`**, path separators folded to `-`. A last segment alone
  collides: `docs` for every repo that keeps its manual there, `guide` for both
  locales of `docs/<lang>/guide`. An unscoped repo ingest still slugs as
  `<owner>-<repo>`.
- **`audit` reads a deliverable as localized only when it carries a local image
  ref**, not from `images/` existing — a re-ingest keeps that directory while
  staging a fresh un-localized file, whose `sha256` still describes what is on
  disk.
- **pf-core pin raised to `~=0.22.0`.**

### Fixed

- **A local archive ingests from disk.** `archive_download` claims any path
  carrying an archive suffix, a bare path or `file://` URL included, but read it
  through the fetcher — which refused on scheme. A path naming no file now says
  which file it could not find.
- **A directory entry URL no longer scopes one level too high** in ClickHelp,
  Paligo and Hugo: the last path segment is dropped only when it names a file, so
  a seed like `…/docs/manual` keeps its scope instead of 404ing every fetch.
- **A GitBook site reached through `llms.txt` keeps the slug and title
  `docs_probe` derived**, so a custom domain no longer folds to its generic host
  label and collides with every other `help.`/`docs.` site.
- **`github_markdown` percent-encodes the raw fetch URL**, so a file whose name
  carries a space or a non-ASCII character is fetched rather than dropped.
- **A README below the repo root is staged as content** — it is that directory's
  index page. The other meta names (`LICENSE`/`CONTRIBUTING`/`CHANGELOG`/
  `documentation.md`) are excluded at **any** depth, so a vendored license, a
  per-package changelog or a nested nav file stays out of the deliverable.
- **A Sphinx crawl stages one copy of a page reachable under two URLs** — a
  directory URL and its `index.html`, or a redirect alias.
- **`apple_help` counts a topic dropped during the merge as `lost`**, so `pages`
  no longer describes topics the deliverable does not carry.
- **The Microsoft Support sitemap walk ends on a `<loc>`-less 200** — a soft-404,
  CDN error page or WAF interstitial — and stops at a page cap, reporting
  `truncated` when it hits one.
- **Zendesk bare (`/hc/de`) and regional (`/hc/es-419`) locales are recognized**
  instead of silently serving `en-us`.
- **EPUB spine hrefs resolve against the OPF's own directory**, and two chapters
  sharing a basename in different folders both reach the deliverable.
- **An OpenAPI spec whose `paths:` is empty or a list renders** instead of
  raising: an explicit `paths:` with nothing under it parses as `None`, not as a
  missing key.
- **MkDocs lead-in dedup uses the first section that carries text**, so a heading
  followed straight by a sub-heading no longer leaves the intro duplicated.
- **`read_manifest` returns `None` for a parseable non-object and for undecodable
  bytes**, so a corrupt manifest meets the same guards as a missing one instead
  of raising past them.
- **Image case-normalization and unchanged-image reuse write the deliverable
  atomically**, so a kill mid-write cannot truncate it.

## [0.10.0] — 2026-08-26

### Added

- **`ingest --replace`** takes over a slug already holding a different source,
  deleting that manual along with its image cache and manifest.
  `run_ingest(replace=...)` is the API equivalent.
- **`PAGESPRING_ENV_FILE`** names the `.env` settings resolve from. Without it
  an installed, non-editable CLI resolves to the directory containing
  site-packages, where no `.env` exists, so `.env`-sourced settings silently
  fell back to their defaults (exported environment variables always applied).
  A missing override file logs a warning instead of silently no-opping. The
  working directory is never searched: every key in the resolved file enters the
  process environment, so an adjacent project's `.env` would be adopted whole.

### Changed

- **Python 3.12 is the floor** — `requires-python >=3.12`, following pf-core.
  A 3.11 install of 0.10.0 is unresolvable; stay on 0.9.x for 3.11.
- **A re-ingest whose slug collides with a different source is refused** instead
  of replacing it, exiting `2`. Remote URLs are compared canonically, so a
  respelled re-ingest of the same source still proceeds; local paths and
  `file://` URLs are compared as resolved filesystem paths. A slug dir holding
  content with no readable manifest is refused (an empty one is staged into as
  usual). `--if-changed` reports `unchanged` only for the source the slug
  already holds. Callers relying on the old clear-and-restage must pass `--slug`
  or `--replace`.
- **`audit` reports `sha_unverified` (warning) where it used to report
  `sha_mismatch` (error)** for a deliverable whose image pass recorded no
  `localized_sha256` — an ingest killed mid-pass. `audit --strict` therefore
  exits `0` on such a slug where it previously exited `1`; the finding is
  reported either way. Page loss renders as `<1%` and `>99%` at the ends rather
  than rounding to `0%` or `100%`.
- **A corpus sweep survives one unreadable slug dir.** `audit --all`, `refresh
  --all` and `status` skip a manifest missing required fields and report the
  rest, instead of aborting the whole sweep on it.
- **pf-core pin raised to `~=0.20.0`** — for `canonical_url`, which the
  slug-collision check compares source URLs with. pf-core 0.20.0 requires
  Python 3.12, which is what moves this package's floor.

### Fixed

- **EPUB 3 content documents are recognized.** `.xhtml` counts as an HTML member
  in an EPUB container (an OPF package document or an `application/epub+zip`
  mimetype member), so spec-named chapters are staged in spine order. Other
  archives are unaffected: a `.md` collection carrying a stray `.xhtml` keeps its
  markdown. The archive kind-sniff is also case-insensitive.
- **A one-operation API spec no longer audits as a collapsed crawl.** `api_spec`
  declares `single_fetch` — the marker `audit` and `refresh` read for a
  deliverable derived from exactly one URL — so `single_page_crawl` no longer
  applies to it.
- **Image provenance no longer claims an earlier run's file.** The hash join
  skips files already on disk before this run's downloads — whatever state the
  sidecar is in — and leaves an ambiguous multi-candidate match unrecorded.
- **A localized deliverable with no recorded hash no longer audits as `ok`.** It
  reports `sha_unverified` when its bytes have diverged from the staged
  `sha256`; whether an image pass ran is read from `images/` existing rather
  than the image count.
- **`llms-full.txt` is read as content, not as an index.** Its body is the
  deliverable (one page, `single_document`). Routing keys on the URL's path
  basename, so a query string, fragment, or case change no longer sends it to
  `docs_probe` for a site crawl. Its slug now folds in the host, so two vendors
  publishing at the same path no longer collide — **an `llms.txt`/`llms-full.txt`
  slug ingested under 0.9.x will re-ingest to a new directory**; delete the old
  one, or `audit --all` reports `duplicate_source_url` on both.
- **A section base URL matches on path boundaries, not string prefixes.** An
  `llms_txt` section seed no longer absorbs a sibling section sharing its prefix
  (`/guide` pulling `/guide-advanced/*`), and an uppercase host matches its
  lowercase links instead of staging nothing.
- **An interrupted ingest no longer locks its slug.** The manifest is written
  before the deliverable is copied, so a run killed mid-copy leaves provenance
  and the same URL can simply be re-ingested.

## [0.9.0] — 2026-08-07

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
