# Anti-Patterns

Concrete examples of what NOT to do, drawn from real violations.

---

## No `transaction()` in orchestrators or entry points

Only services and repos own DB access. If an orchestrator needs data, it calls a service, which calls a repo.

```python
# WRONG — orchestrator touching the DB directly
from pf_core.db import transaction

def run_export():
    with transaction() as conn:
        entries = conn.execute(text("SELECT ..."))

# RIGHT — orchestrator delegates to a service
def run_export():
    entries = entry_service.get_entries_for_export()
```

---

## No `os.environ` in services

Services receive configuration — they don't reach for globals. Use the project's config object.

```python
# WRONG
def resolve_model():
    return os.environ.get("OPENROUTER_MODEL", "gpt-4o-mini")

# RIGHT
from app.config import cfg

def resolve_model():
    return cfg.OPENROUTER_MODEL
```

---

## No `print()` in services

Use structured logging. `print()` is only acceptable in CLI entry points for user-facing output.

```python
# WRONG
def grade_one(answer):
    print(f"Grading submission...", file=sys.stderr)

# RIGHT
from pf_core.log import get_logger
logger = get_logger(__name__)

def grade_one(answer):
    logger.info("grading_submission")
```

---

## No god-module `_util.py` files

`_util.py` is for thin shared helpers (under 150 lines). If it grows past that, business logic has leaked in. Split by subdomain.

```
# WRONG — 490-line _util.py with catalog scanning, status queries, resolvers
app/api/_util.py  (490 lines)

# RIGHT — split by concern
app/api/_util.py       (resolvers, request helpers — 80 lines)
app/api/_catalog.py    (catalog scanning — 120 lines)
app/api/_status.py     (section status queries — 90 lines)
```

---

## No business logic in utility files

If a function makes decisions, transforms domain data, or coordinates multiple operations, it belongs in a service — not in `_util.py`, `_helpers.py`, or `_common.py`.

---

## No duplicate exception hierarchies

One `errors.py` per project, in `app/errors.py`. Don't create a second one in `services/errors.py`.

---

## No monster files

Service files over 300 lines, orchestrator files over 400 lines — split them. See `project-structure.md` for naming conventions.

---

## No copy-pasting between files

If two services need the same logic, extract it to a shared module in the appropriate layer. Don't duplicate.

---

## No raw HTTP requests for LLM calls

Use `pf_core.clients.openrouter` — it handles timeouts, retries, provider routing, and usage tracking.

---

## No raw SQL for dialect-specific behavior

Use SQLAlchemy expression constructs for database independence. Never write dialect-detection code or raw SQL that only works on one database.

```python
# WRONG — dialect detection, raw SQL timestamp
def _now_expr(conn):
    if conn.dialect.name == "mysql":
        return text("NOW(6)")
    return text("strftime('%Y-%m-%dT%H:%M:%SZ','now')")

# WRONG — Python-side timestamp in SQL context
from pf_core.db.helpers import now_iso
stmt = t.update().values(deleted_at=now_iso())

# RIGHT — SQLAlchemy handles dialect translation
from sqlalchemy import func, table, column
t = table("entries", column("id"), column("deleted_at"))
stmt = t.update().where(t.c.id == id_value).values(deleted_at=func.now())
```

---

## No hardcoded config in framework APIs

Framework functions read tunable values from environment variables — callers should not pass config strings. See `config-driven.md` for the full pattern.

```python
# WRONG — caller passes config value
setup_rate_limit(app, default_limit="60/minute")

# RIGHT — framework reads API_RATE_LIMIT_PER_MINUTE internally
setup_rate_limit(app)
```
