---
name: Readeck
description: Search and read articles saved in the user's self-hosted Readeck read-it-later library (read-only) with the readeck CLI. Use this skill whenever the user asks about articles, links, or reading material they saved — "find an article I saved about X", "what did I save last month", "summarize what I've read about Y", "did I save anything about Z" — or asks questions that their saved content could answer.
---

# Readeck library (read-only)

The user keeps articles in a self-hosted [Readeck](https://readeck.org) instance. Use the
`readeck` command to search, read, and quote from it.

If `readeck` is not on PATH, either install it (`uv tool install git+https://github.com/dvx76/readeck-cli`)
— ask the user first — or run it through uv without installing (it is not on PyPI):

```sh
uvx --from git+https://github.com/dvx76/readeck-cli readeck <command> [options]
```

Output is JSON when stdout is not a terminal and a colored, human-oriented view on a terminal.
Always pass `-o json` (`--output json`) so you get JSON even if your shell runs commands in a
pseudo-terminal; the examples below omit it for brevity. Exit code 3 means the CLI is not configured — see
Setup below and stop to tell the user. Exit code 1 is an API or network error with a
`readeck: ...` message on stderr; on `timed out after Ns`, retry once with a larger `--timeout`.
`mirror` records per-bookmark failures (including timeouts) under `failed` and keeps going.

## Setup

Credentials come from `--url`/`--token` flags, the `READECK_URL`/`READECK_TOKEN` environment
variables, or `~/.config/readeck/cli.json` (`{"url": "...", "token": "..."}`).

If the CLI exits with code 3, the user has not configured it yet. Ask them to run `readeck auth`
in their terminal: it prompts for the instance URL and an API token created in Readeck under
**Settings → API Tokens** (read-only scope, e.g. `bookmarks:read`). `readeck auth status` shows
the resolved configuration and verifies the connection. Do not ask for the token in chat if they
can run `readeck auth` themselves; if they paste one, write `~/.config/readeck/cli.json` and
`chmod 600` it.

## Commands

```sh
readeck search -q "linux kernel" --since 6m -o json  # full-text search
readeck search --labels security --read-status unread --sort -created
readeck search -q "rust" --all                    # fetch every match (up to --max, default 500)
readeck show <ID>                                 # one bookmark's metadata
readeck read <ID>                                 # article as Markdown on stdout
readeck read <ID> --out /path/file.md             # save instead of printing
readeck highlights [<ID>]                         # the user's highlighted passages
readeck labels                                    # labels with counts
readeck collections                               # saved searches (pass one via --collection)
readeck mirror --since 6m --dir <DIR>             # export matching articles to Markdown files
readeck info                                      # instance version/features
```

Search filters (all combinable, AND semantics): `-q`, `--since`/`--until` (accept `6m`, `30d`,
`2w`, `1y`, or dates like `2026-01-01`; filter on **created**, i.e. when the user saved it),
`--title`, `--author`, `--site`, `--lang`, `--labels`, `--type`, `--read-status`,
`--marked`/`--no-marked`, `--archived`/`--no-archived`, `--has-notes`/`--no-has-notes`,
`--collection`, `--sort`, `--limit` (max 100), `--offset`.

## Full-text search syntax (`-q`)

- plain words search title, description, **article body**, site, labels, and notes
- field prefixes: `title:kernel`, `author:lwn`, `site:kernel.org`, `label:linux`, `note:todo`
- `"exact phrase"`, `-excluded-word`, `wildcard*`

## Workflow

1. **Find**: start with `search` and a small `--limit` (10–25). Add filters when the question
   implies them (time: `--since`; topic: labels/queries). A result's `title` and `description`
   are enough to shortlist — don't fetch article bodies yet.
2. **Read selectively**: `read <ID>` for one or two candidates. If you need many or expect long
   articles, use `mirror` into a scratch directory (e.g. `~/.opencode/scratch/readeck/`) and then
   `rg`/read files locally — `mirror` is incremental, so re-running it is cheap and it supports
   the same filters.
3. **Answer with citations**: cite the saved article's `title` and its original `url`;
   `readeck_url` (in search/show output) is where the user can open it in Readeck.
4. **Weight highlights**: text wrapped in `==...==` inside `read` output marks passages the user
   highlighted — treat those as the parts that mattered to them.

## Notes

- `readeck browse` is an interactive full-screen picker for humans; never run it (it needs a
  terminal and exits 2 without one). Use `search` instead.
- Read-only: the CLI cannot save, edit, archive, or delete bookmarks. If the user asks for that,
  say so and suggest the Readeck web UI (or a write-scoped tool).
- `created` is when the user saved the article; `published` is the original publication date and
  may be null.
- Some bookmarks (videos, failed fetches, PDFs with errors) have no article; `read` returns a
  404-flavored error for them. Prefer results with `has_article: true` and `loaded: true`.
- If the user's instance has a self-signed certificate, add `--insecure` to commands.
- The CLI refuses redirects to another origin (scheme, host, or port) so the token is never sent
  elsewhere; it exits 1 with "redirected ... to a different origin: <target>". Do not retry or
  point the CLI at the target; tell the user to fix `READECK_URL` (e.g. `http` vs `https`) or
  their reverse proxy.
