# Git Safety

## Commit rules

- Never force-push to main/master.
- Never use `--no-verify` to skip pre-commit hooks.
- Never amend published commits.
- Create new commits rather than amending existing ones.

## Branch hygiene

- Feature branches off main.
- Keep branches focused — one feature or fix per branch.
- Delete branches after merge.

## What not to commit

- `.env` files (secrets).
- Database files (`*.db`, `*.sqlite`).
- `__pycache__/`, `.pyc` files.
- IDE directories (`.idea/`, `.vscode/` user settings).
- Log files.
- Virtual environments (`.venv/`, `venv/`).
