# Pattern verification

Source sites drift; synthetic fixtures don't. **Before trusting a pattern — new,
changed, or relied on — run a real ingest and read the `incoming/` file.** The
unit suite mocks `pagespring.http` and proves the transform logic; it cannot see
that a site restructured, added rate-limiting, or changed its chrome.

Do: after editing or first-running a pattern, `bin/run ingest <real-url>`, read
the artifact, and check the printed `pages:` count for a silently truncated crawl.

Failure modes a green mocked suite cannot see — each has bitten a real pattern:

- A site changes the page chrome or footer a pattern strips, so boilerplate
  leaks into every page until a live ingest reveals it.
- A hub page server-renders only a fraction of its entries and client-renders
  the rest, so the true catalog is a sitemap or index the pattern must follow —
  not the links visible in the fetched HTML.
- A host rate-limits with a status code the generic retry doesn't treat as
  recoverable, silently dropping fetchable pages.
- A URL matches a broad pattern that can't actually handle it, so it fails at
  fetch time instead of routing elsewhere or exiting cleanly.

The lesson, not the instances: a green `bin/test` is necessary, never sufficient.
A pattern is only proven once a real artifact has been read.
