"""Command-line interface definition for ``readeck``."""

from __future__ import annotations

import argparse
import sys

from readeck_cli import __version__
from readeck_cli.api import Api, CliError
from readeck_cli.commands import COMMANDS, cmd_auth
from readeck_cli.config import load_settings


def add_common_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--url", help="Readeck base URL (env: READECK_URL)")
    parser.add_argument("--token", help="Readeck API token (env: READECK_TOKEN)")
    parser.add_argument("--insecure", action="store_true", help="skip TLS certificate verification")
    parser.add_argument("--timeout", type=float, default=30.0, help="HTTP timeout in seconds (default 30)")


def add_filter_args(parser: argparse.ArgumentParser) -> None:
    group = parser.add_argument_group("filters")
    group.add_argument("-q", "--query", help="full-text search string (see README for syntax)")
    group.add_argument("--since", metavar="WHEN", help="saved at or after WHEN (6m, 30d, 2w, 1y, 2026-01-01)")
    group.add_argument("--until", metavar="WHEN", help="saved at or before WHEN")
    group.add_argument("--title", help="filter by title")
    group.add_argument("--author", help="filter by author")
    group.add_argument("--site", help="filter by site name or domain")
    group.add_argument("--lang", help="filter by language code")
    group.add_argument("--labels", help="filter by labels (comma separated)")
    group.add_argument("--type", action="append", choices=["article", "photo", "video"])
    group.add_argument("--read-status", action="append", choices=["unread", "reading", "read"], dest="read_status")
    group.add_argument(
        "--marked",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="only favorites (--no-marked: only non-favorites)",
    )
    group.add_argument(
        "--archived",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="--archived: archived only; --no-archived: active only",
    )
    group.add_argument(
        "--has-notes",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="only bookmarks with a note and/or highlights",
    )
    group.add_argument("--collection", help="collection ID")
    group.add_argument("--sort", help="comma separated sort keys, e.g. -created,title")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="readeck",
        description="Read-only CLI for a self-hosted Readeck instance.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    search = sub.add_parser("search", help="search bookmarks", description="Search bookmarks.")
    add_common_args(search)
    add_filter_args(search)
    search.add_argument("--limit", type=int, default=20, help="page size, 1-100 (default 20)")
    search.add_argument("--offset", type=int, default=0)
    search.add_argument("--all", action="store_true", help="fetch all matching pages (up to --max)")
    search.add_argument("--max", type=int, default=500, help="cap for --all (default 500)")
    search.add_argument("--format", choices=["json", "table"], default="json")

    show = sub.add_parser("show", help="show one bookmark's metadata")
    add_common_args(show)
    show.add_argument("id")

    read = sub.add_parser("read", help="print a bookmark's article as Markdown")
    add_common_args(read)
    read.add_argument("id")
    read.add_argument("--out", help="write Markdown to this file instead of stdout")

    highlights = sub.add_parser("highlights", help="list highlights, for the whole library or one bookmark")
    add_common_args(highlights)
    highlights.add_argument("id", nargs="?", help="bookmark ID (omit for the whole library)")
    highlights.add_argument("--limit", type=int, default=50)
    highlights.add_argument("--offset", type=int, default=0)
    highlights.add_argument("--format", choices=["json", "table"], default="json")

    labels = sub.add_parser("labels", help="list labels with counts")
    add_common_args(labels)
    labels.add_argument("--format", choices=["json", "table"], default="json")

    collections = sub.add_parser("collections", help="list saved collections (saved searches)")
    add_common_args(collections)
    collections.add_argument("--format", choices=["json", "table"], default="json")

    mirror = sub.add_parser("mirror", help="export matching articles as Markdown files (incremental)")
    add_common_args(mirror)
    add_filter_args(mirror)
    mirror.add_argument("--dir", required=True, help="destination directory")
    mirror.add_argument("--max", type=int, default=500, help="maximum number of bookmarks (default 500)")
    mirror.add_argument("--refresh", action="store_true", help="re-download already mirrored bookmarks")

    info = sub.add_parser("info", help="show instance information")
    add_common_args(info)

    auth = sub.add_parser("auth", help="save or inspect Readeck credentials")
    add_common_args(auth)
    auth.add_argument(
        "action",
        nargs="?",
        choices=["login", "status", "logout"],
        default="login",
        help="login (default): prompt and save; status: show/verify config; logout: delete config file",
    )
    auth.add_argument("--no-verify", action="store_true", help="skip the connection check after login")

    return parser


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "auth":
            cmd_auth(args)
        else:
            settings = load_settings(args)
            api = Api(settings.url, settings.token, insecure=settings.insecure, timeout=settings.timeout)
            COMMANDS[args.command](api, settings.url, args)
    except CliError as exc:
        print(f"readeck: {exc}", file=sys.stderr)
        raise SystemExit(exc.code) from exc
