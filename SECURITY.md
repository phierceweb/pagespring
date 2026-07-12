# Security Policy

## Reporting a vulnerability

**Please do not open a public issue for security vulnerabilities.**

Report privately through GitHub's [private vulnerability reporting][gh-pvr]:
the repository's **Security** tab → **Report a vulnerability**.

Include the affected version, a description of the issue, and steps to
reproduce. You can expect an initial acknowledgement within a few days.

[gh-pvr]: https://docs.github.com/en/code-security/security-advisories/guidance-on-reporting-and-writing-information-about-vulnerabilities/privately-reporting-a-security-vulnerability

## Supported versions

pagespring is pre-1.0 and under active development. Security fixes land in
the latest tagged release; pin to a tagged release and upgrade promptly when
a fix ships.

## Scope

pagespring is a local CLI that fetches and parses remote documentation. It
needs no credentials — there are no API keys or secrets to handle. The most
security-relevant surfaces are:

- **`pagespring.http`** — the fetch layer (stdlib `urllib`, plain GETs).
- **Archive extraction** (`archive_download`) — zips extract via `zipfile`'s
  sanitized `extractall`; tars use the `data` extraction filter.
- **Content parsing** — BeautifulSoup for HTML, `json`/`yaml.safe_load` for
  specs; deliverables are written as inert files, never executed.
