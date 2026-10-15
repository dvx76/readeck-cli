# readeck-cli

Read-only CLI for a self-hosted [Readeck](https://readeck.org) instance, designed for agents
(and useful for humans). Full-text search with date-range filters, articles as Markdown,
highlights, and an incremental local mirror for `rg`-style research. It never modifies your
library.

## Install

```sh
uv tool install readeck-cli   # installs the `readeck` command
# or run without installing:
uvx --from readeck-cli readeck search -q "linux kernel" --since 6m
```

Not on PyPI yet: from a checkout, use `uv tool install .`.

## Setup

```sh
readeck auth          # prompts for the instance URL and API token, saves ~/.config/readeck/cli.json
readeck auth status   # show the resolved config and verify the connection
```

Create the token in Readeck under **Settings → API Tokens** with the read-only scope
(`bookmarks:read` is enough for every command). `READECK_URL`/`READECK_TOKEN` environment
variables or `--url`/`--token` flags override the config file.

## Usage

```sh
readeck search -q "linux kernel" --since 6m       # JSON by default, --format table for humans
readeck read <ID>                                 # article as Markdown (--out FILE to save)
readeck highlights [<ID>]                         # highlighted passages
readeck labels                                    # labels with counts
readeck mirror --since 6m --dir DIR               # incremental Markdown export
```

Search filters: `--since`/`--until` (`6m`, `30d`, `2w`, `1y`, or dates), `--title`, `--author`,
`--site`, `--lang`, `--labels`, `--type`, `--read-status`, `--marked`, `--archived`,
`--has-notes`, `--collection`, `--sort`, `--limit`, `--offset`, `--all`. Full syntax:
`readeck search --help`.

Exit codes: `1` API error, `2` usage error, `3` not configured.

## Agent skill

The `skills/readeck/` directory contains an agent skill teaching when and how to use the CLI:

```sh
npx skills add dvx76/readeck-cli -g -a opencode -y
```

## Development

```sh
uv sync            # create .venv and install dev dependencies
uv run pytest      # end-to-end tests against a stub Readeck API
uv run ruff check  # lint
```

## License

[MIT](LICENSE)
