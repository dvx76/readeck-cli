# AGENTS.md

Notes for agents working on this repository.

## Project

`readeck-cli` is a read-only CLI client for a self-hosted Readeck instance. It talks to the
Readeck REST API and must never add write operations (no saving, archiving, editing, or deleting
in the user's library). Agent-facing output is JSON by default; humans use `--format table`.

## Development

```sh
uv sync               # install dev dependencies
uv run pytest         # end-to-end tests against a stub Readeck API (tests/conftest.py)
uv run ruff check     # lint (fix with --fix)
uv run ruff format    # format
```

## Conventions

- Python 3.14+, **stdlib only at runtime** (no dependencies in `[project].dependencies`).
- Layout: `cli.py` (argument parsing/dispatch), `commands.py` (behavior), `api.py` (HTTP),
  `config.py` (credential resolution), `utils.py` (formatting). Add new commands in
  `commands.py`, register them in `COMMANDS`, define their arguments in `cli.py`.
- Exit codes: `1` API error, `2` usage error, `3` configuration error. Keep this contract
  stable; the skill and tests rely on it.
- Never print or log tokens unmasked (use `utils.mask_token`); the config file is written with
  mode `0600`.
- Tests run the CLI as a subprocess against the stub server with a non-TTY stdin; anything that
  prompts must fail cleanly without a terminal.
- When CLI behavior changes (flags, config, outputs, exit codes), update
  `skills/readeck/SKILL.md` in the same change so the distributed skill stays accurate.
