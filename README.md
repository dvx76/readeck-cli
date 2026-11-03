# readeck-cli

Read-only CLI for a self-hosted [Readeck](https://readeck.org) instance, designed for agents
(and useful for humans). Full-text search with date-range filters, articles as Markdown,
highlights, and an incremental local mirror for `rg`-style research. It never modifies your
library.

## Install

```sh
uv tool install git+https://github.com/dvx76/readeck-cli   # installs the `readeck` command
# or run without installing:
uvx --from git+https://github.com/dvx76/readeck-cli readeck search -q "linux kernel" --since 6m
```

Not on PyPI yet. From a checkout, `uv tool install .` also works.

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
readeck search -q "linux kernel" --since 6m       # colored view on a terminal, JSON when piped
readeck browse --since 6m                         # interactive picker: type to filter, live preview
readeck read <ID>                                 # rendered article in a pager on a terminal, Markdown when piped
readeck read <ID> --no-pager                      # print the rendered article instead of paging it
readeck read <ID> --raw                           # Markdown source even on a terminal (--out FILE to save)
readeck highlights [<ID>]                         # highlighted passages
readeck labels                                    # labels with counts
readeck mirror --since 6m --dir DIR               # incremental Markdown export
```

Search filters: `--since`/`--until` (`6m`, `30d`, `2w`, `1y`, or dates), `--title`, `--author`,
`--site`, `--lang`, `--labels`, `--type`, `--read-status`, `--marked`, `--archived`,
`--has-notes`, `--collection`, `--sort`, `--limit`, `--offset`, `--all`. Full syntax:
`readeck search --help`.

Output: on a terminal every command prints a colored, human-friendly view; when stdout is piped
or redirected it prints JSON (for agents and `jq`). Force either with `-o pretty` or `-o json`.
Colors honor `NO_COLOR` and `FORCE_COLOR`.

On a terminal, `read` opens articles taller than the screen in a built-in pager (`browse`'s ^R
always does, and `q` returns to the picker):

| Keys | |
|---|---|
| `j`/`k`, ↑/↓, space/`b`, `d`/`u`, `g`/`G` | scroll by line, page, half page; top, bottom |
| `/` then `n`/`N` | search, next and previous match |
| `]`/`[` | next and previous section |
| `c` | contents panel: pick a section with ↑/↓ and Enter |
| Tab/Shift-Tab, Enter | select a link and follow it; Backspace jumps back |
| `q` | quit |

Links within the article (`#section` links, links to the article's own page, footnotes) jump
inside the pager; other links open in your browser. The status line shows the current section and
position, e.g. `§2 · 43%`. Nothing is written back to Readeck: reading progress stays untouched.

Article images are drawn inline in kitty, Ghostty, WezTerm, Rex, and any other terminal that
answers the kitty graphics protocol's support query. PNG, JPEG and GIF are decoded by the CLI
itself, no extra tools needed. kitty (0.28+), Ghostty and Rex get Unicode placeholders: the image
is uploaded once and drawn as text cells, so it scrolls and clips with the text. That also works
inside tmux 3.3+ with `set -g allow-passthrough on` when tmux runs in kitty, Ghostty or Rex; other
terminals inside tmux get links to the images instead. WezTerm and Konsole, which lack placeholders,
get images drawn at the cursor (redrawn as you scroll in the pager). On wide terminals a margin
column holds your note, the table of contents, captions and link references. Set
`READECK_IMAGES=0` to turn images off, or `READECK_IMAGES=1` to force them on.

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
