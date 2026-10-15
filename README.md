# readeck-cli

A **read-only** command-line client for a self-hosted [Readeck](https://readeck.org) instance,
designed for agents (and useful for humans in a terminal).

It speaks the [Readeck REST API](https://readeck.org/docs/api) directly and adds nothing to your
instance: search your library with full-text and time-range filters, read articles as Markdown,
list highlights/labels/collections, or mirror a filtered slice of the library to local Markdown
files for `rg`-style research.

## Install

```sh
# install the `readeck` command
uv tool install readeck-cli

# or run it without installing
uvx --from readeck-cli readeck search -q "linux kernel" --since 6m
```

> Not on PyPI yet: install from the repository for now with
> `uv tool install git+https://github.com/<you>/readeck-cli` (or `uv tool install .` in a checkout).

## Setup

1. In Readeck, open **Settings → API Tokens** and create a token with the **`bookmarks:read`**
   scope (read-only; sufficient for every command).
2. Provide it to the CLI in one of these ways:

```sh
# config file (recommended)
mkdir -p ~/.config/readeck
cat > ~/.config/readeck/cli.json <<'EOF'
{"url": "https://readeck.example.com", "token": "YOUR_TOKEN"}
EOF
chmod 600 ~/.config/readeck/cli.json

# or environment variables
export READECK_URL="https://readeck.example.com"
export READECK_TOKEN="YOUR_TOKEN"
```

Precedence: `--url`/`--token` flags → `READECK_URL`/`READECK_TOKEN` env vars →
`~/.config/readeck/cli.json` (override path with `READECK_CLI_CONFIG`).

## Commands

| Command | Description |
| --- | --- |
| `readeck search` | search bookmarks; JSON output by default, `--format table` for humans |
| `readeck show <id>` | bookmark metadata |
| `readeck read <id>` | article as Markdown (frontmatter with source URL; highlights inline as `==…==`); `--out FILE` |
| `readeck highlights [<id>]` | the user's highlights, library-wide or per bookmark |
| `readeck labels` | labels with bookmark counts |
| `readeck collections` | saved searches (usable via `search --collection <id>`) |
| `readeck mirror --dir DIR` | export matching articles to Markdown files (incremental, with an `_index.json`) |
| `readeck info` | instance version/features |

### Search filters

`-q/--query` (full text), `--since`/`--until` (relative tokens `6m`, `30d`, `2w`, `1y` or dates),
`--title`, `--author`, `--site`, `--lang`, `--labels`, `--type`, `--read-status`,
`--marked`/`--no-marked`, `--archived`/`--no-archived`, `--has-notes`/`--no-has-notes`,
`--collection`, `--sort`, `--limit` (max 100), `--offset`, `--all`.

### Full-text search syntax (`-q`)

- plain words search title, description, article body, site, labels, and notes
- field prefixes: `title:kernel`, `author:lwn`, `site:kernel.org`, `label:linux`, `note:todo`
- `"exact phrase"`, `-excluded-word`, `wildcard*`

## For agents

This repository ships a skill ([`skills/readeck/SKILL.md`](skills/readeck/SKILL.md)) that teaches
coding agents when and how to use the CLI — search first, read selectively, cite Readeck URLs.

```sh
# install to the current project
npx skills add <owner>/readeck-cli

# or globally, for a specific agent
npx skills add <owner>/readeck-cli -g -a opencode -y
```

The CLI exits with clear codes your agent can act on: `1` API error, `2` usage error,
`3` not configured yet.

## Development

```sh
uv sync            # create .venv and install dev dependencies
uv run pytest      # end-to-end tests against a stub Readeck API
uv run ruff check  # lint
```

Layout: `src/readeck_cli/` is the package (`cli.py` argument parsing, `commands.py` behavior,
`api.py` HTTP client, `config.py` settings, `utils.py` formatting), `tests/` contains a stub
Readeck server used for end-to-end tests.

## License

TBD — pick a license before publishing to PyPI.
