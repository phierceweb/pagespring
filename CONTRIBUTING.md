# Contributing to pagespring

Thanks for your interest. pagespring is a lean acquisition tool — stdlib fetch,
`beautifulsoup4` parsing, no ML stack; contributions that keep it small,
well-tested, and documented are welcome.

## Scope — read this first

pagespring **acquires and normalizes** publicly available documentation into
one clean file per manual under `incoming/<slug>/` — and stops there.
Conversion into a RAG corpus is [pagespeak](https://github.com/phierceweb/pagespeak)'s
job; pagespring never runs or imports it. Fetching stays in `pagespring.http`
(stdlib `urllib`) — no `httpx`/`requests`, and nothing that handles logins,
paywalls, or bot-detection evasion (see the README's *Intended use*).

The most useful contribution is a new **source pattern** — see
[docs/architecture.md](docs/architecture.md#adding-a-new-pattern) for the
four-member protocol and the registration order rules.

## Development setup

Python 3.12+ is required.

```bash
git clone https://github.com/phierceweb/pagespring
cd pagespring
bin/setup        # venv + editable install with [dev] + pre-commit hooks
```

## Before you open a pull request

These checks run in CI and as pre-commit hooks — run them locally first:

```bash
bin/test    # pytest — full suite, must be green
bin/lint    # ruff check + ruff format --check + mypy (strict) + file-size gate
```

And hold the change to these standards:

- **Tests travel with code.** New behavior needs tests; a bug fix needs a
  regression test that fails before your change and passes after. Tests mock
  `pagespring.http` — they never hit the network.
- **Patterns get a live verification.** A green mocked suite proves the
  transform logic, not the source's current shape — before calling a pattern
  done, run a real `ingest` and read the deliverable in `incoming/`. Check the
  printed `pages:` count for a silently truncated crawl, and look for page
  chrome leaking in, a hub that lists only part of its catalog, and a host
  rate-limiting pages away.
- **Docs travel with code.** A behavior change is incomplete without the
  matching `docs/*.md` update.
- **File-size gate.** Python files over the hard limit fail the build; split by
  concern instead of growing a monolith.

## Coding conventions

`bin/setup` installs the framework's full rule set into `.ai/rules/`
(gitignored, so it exists only in a set-up checkout). The essentials:

- Modern Python 3.12+ syntax — `X | None`, lowercase `dict`/`list`/`tuple`.
- Type hints on every public signature; Google-style docstrings on public APIs.
- Structured logging via `pw_foundation.log.get_logger(__name__)` — never bare
  `print` outside CLI entry points.
- Polite fetching: identifying UA, `polite_sleep()` between crawl requests,
  crawl caps with a `log.warning` when they truncate.
- Parse HTML with `pagespring.patterns._soup.parse_html`, never a bare
  `BeautifulSoup(...)`, so attribute values read the same on every Python.

## Versioning

Pre-1.0: a minor bump (`0.X.0`) may include breaking changes, always called out
in `CHANGELOG.md`; a patch bump is fixes only.

## License

Contributions are accepted under the Apache License 2.0. Opening a pull request
licenses your work to the project under those terms.

## Questions

Open an issue for bugs and feature requests. For anything security-sensitive,
follow [SECURITY.md](SECURITY.md) instead of filing a public issue.
