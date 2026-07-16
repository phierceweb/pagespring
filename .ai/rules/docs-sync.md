# Docs Sync

**Every substantive change to pagespring must include documentation updates.** A PR or changeset that adds, removes, or changes user-visible behavior is incomplete without the matching doc update. This is not optional.

## What counts as substantive

- New source pattern added to `src/pagespring/patterns/`, or a change to an existing pattern's URL matching or output
- CLI command or flag added, removed, renamed, or changed (`src/pagespring/cli.py`)
- Manifest schema changed (`src/pagespring/manifest.py`)
- Fetch/politeness behavior changed (`src/pagespring/http.py` — User-Agent, retries, pacing, crawl caps)
- Pattern protocol or registry/classification order changed (`base.py`, `registry.py`)
- New or changed env var or config default (`config.py`, `.env.example`)

## What does NOT require doc updates

- Internal refactors that don't change behavior
- Test-only changes
- Comment or docstring improvements
- Dependency version bumps (unless they change behavior)

## Checklist

### New or changed source pattern?
- [ ] Update docs/architecture.md (pattern list + classification-order notes)
- [ ] Update docs/usage.md if the user-visible flow changes (new URL shapes, new flags)
- [ ] Run the live-ingest verification in `.ai/rules/pattern-verification.md` before calling it done

### CLI surface changed?
- [ ] Update the docs/usage.md command list and the affected section
- [ ] Update the README Quick start if a headline command changed

### Manifest or deliverable layout changed?
- [ ] Update docs/usage.md "Reading the result" and docs/architecture.md's manifest section
- [ ] Add a CHANGELOG entry — downstream consumers parse the manifest

### Fetch/politeness behavior changed?
- [ ] Update the README "Intended use" section and docs/architecture.md's http notes
- [ ] Update `.env.example` if an env var was added or renamed

## Principle

Code and docs travel together. A change that updates a module but not its docs is incomplete. When in doubt, update the doc.
