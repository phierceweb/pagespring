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
five-member protocol and the registration order rules.

## Development setup

Python 3.11+ is required.

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
  done, run a real `ingest` and read the deliverable in `incoming/`
  (see [`.ai/rules/pattern-verification.md`](.ai/rules/pattern-verification.md)).
- **Docs travel with code.** A behavior change is incomplete without the
  matching `docs/*.md` update — see [`.ai/rules/docs-sync.md`](.ai/rules/docs-sync.md).
- **File-size gate.** Python files over the hard limit fail the build; split by
  concern instead of growing a monolith.

## Coding conventions

The full set lives in [`.ai/rules/`](.ai/rules/). The essentials:

- Modern Python 3.11+ syntax — `X | None`, lowercase `dict`/`list`/`tuple`.
- Type hints on every public signature; Google-style docstrings on public APIs.
- Structured logging via `pf_core.log.get_logger(__name__)` — never bare
  `print` outside CLI entry points.
- Polite fetching: identifying UA, `polite_sleep()` between crawl requests,
  crawl caps with a `log.warning` when they truncate.

## Versioning

Pre-1.0: a minor bump (`0.X.0`) may include breaking changes, always called out
in `CHANGELOG.md`; a patch bump is fixes only.

## Questions

Open an issue for bugs and feature requests. For anything security-sensitive,
follow [SECURITY.md](SECURITY.md) instead of filing a public issue.
