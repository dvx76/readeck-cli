"""Command implementations for the Readeck CLI (all read-only)."""

from __future__ import annotations

import argparse
import datetime
import json
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

from readeck_cli.api import Api, CliError, fail
from readeck_cli.utils import (
    bool_param,
    dump_json,
    print_bookmark_table,
    slugify,
    summarize,
    time_token,
)


def build_filter_params(args: argparse.Namespace) -> dict[str, Any]:
    """Map parsed filter arguments to Readeck query parameters."""
    params: dict[str, Any] = {
        "search": args.query,
        "title": args.title,
        "author": args.author,
        "site": args.site,
        "lang": args.lang,
        "labels": args.labels,
        "type": args.type or None,
        "read_status": args.read_status or None,
        "is_marked": bool_param(args.marked),
        "is_archived": bool_param(args.archived),
        "has_notes": bool_param(args.has_notes),
        "collection": args.collection,
        "range_start": time_token(args.since),
        "range_end": time_token(args.until),
        "sort": args.sort,
    }
    return {k: v for k, v in params.items() if v is not None}


def fetch_page(api: Api, args: argparse.Namespace, limit: int, offset: int) -> tuple[list[dict], int]:
    params = build_filter_params(args)
    params["limit"] = limit
    params["offset"] = offset
    headers, data = api.json("GET", "/bookmarks", params)
    total = int(headers.get("Total-Count", len(data)))
    return data, total


def fetch_all(api: Api, args: argparse.Namespace, max_items: int) -> tuple[list[dict], int]:
    collected: list[dict] = []
    total = 0
    offset = 0
    while len(collected) < max_items:
        page, total = fetch_page(api, args, min(100, max_items - len(collected)), offset)
        collected.extend(page)
        offset += len(page)
        if not page or offset >= total:
            break
    return collected[:max_items], total


def cmd_search(api: Api, base_url: str, args: argparse.Namespace) -> None:
    if not 1 <= args.limit <= 100:
        fail("--limit must be between 1 and 100", 2)
    if args.offset < 0:
        fail("--offset must be >= 0", 2)
    if args.all:
        bookmarks, total = fetch_all(api, args, args.max)
    else:
        bookmarks, total = fetch_page(api, args, args.limit, args.offset)
    if args.format == "table":
        print_bookmark_table(bookmarks, base_url)
        print(f"({len(bookmarks)} of {total})")
    else:
        dump_json(
            {
                "total": total,
                "count": len(bookmarks),
                "offset": args.offset,
                "bookmarks": [summarize(b, base_url) for b in bookmarks],
            }
        )


def cmd_show(api: Api, base_url: str, args: argparse.Namespace) -> None:
    _, data = api.json("GET", f"/bookmarks/{args.id}")
    dump_json(summarize(data, base_url))


def cmd_read(api: Api, base_url: str, args: argparse.Namespace) -> None:
    _, body = api.request("GET", f"/bookmarks/{args.id}/article.md", accept="text/markdown")
    text = body.decode("utf-8", "replace")
    if args.out:
        path = Path(args.out).expanduser()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        dump_json(
            {
                "id": args.id,
                "path": str(path),
                "bytes": len(body),
                "readeck_url": f"{base_url}/bookmarks/{args.id}",
            }
        )
    else:
        sys.stdout.write(text)
        if text and not text.endswith("\n"):
            sys.stdout.write("\n")


def cmd_highlights(api: Api, base_url: str, args: argparse.Namespace) -> None:
    if args.id:
        _, data = api.json("GET", f"/bookmarks/{args.id}/annotations")
    else:
        _, data = api.json("GET", "/bookmarks/annotations", {"limit": args.limit, "offset": args.offset})
    keys = ("id", "text", "created", "bookmark_id", "bookmark_title", "bookmark_url", "bookmark_site_name")
    highlights = [{k: h[k] for k in keys if h.get(k) not in (None, "")} for h in data]
    if args.format == "table":
        if not highlights:
            print("no highlights")
        for highlight in highlights:
            where = highlight.get("bookmark_title") or highlight.get("bookmark_id", "")
            print(f"- {highlight.get('text', '')[:100]}")
            print(f"  {where}  {highlight.get('created', '')[:10]}  id={highlight.get('id', '')}")
    else:
        dump_json(highlights)


def cmd_labels(api: Api, base_url: str, args: argparse.Namespace) -> None:
    _, data = api.json("GET", "/bookmarks/labels")
    labels = [{"name": x.get("name", ""), "count": x.get("count", 0)} for x in data]
    labels.sort(key=lambda x: (-x["count"], x["name"]))
    if args.format == "table":
        for label in labels:
            print(f"{label['count']:5}  {label['name']}")
    else:
        dump_json(labels)


def cmd_collections(api: Api, base_url: str, args: argparse.Namespace) -> None:
    _, data = api.json("GET", "/bookmarks/collections")
    keys = (
        "id",
        "name",
        "search",
        "title",
        "author",
        "site",
        "labels",
        "type",
        "read_status",
        "is_marked",
        "is_archived",
        "range_start",
        "range_end",
        "is_pinned",
    )
    collections = [{k: c[k] for k in keys if c.get(k) not in (None, "", [])} for c in data]
    if args.format == "table":
        for collection in collections:
            desc = " ".join(f"{k}={v}" for k, v in collection.items() if k not in ("id", "name", "is_pinned"))
            print(f"{collection.get('id', ''):10} {collection.get('name', '')}  {desc}")
    else:
        dump_json(collections)


def cmd_info(api: Api, base_url: str, args: argparse.Namespace) -> None:
    _, data = api.json("GET", "/info")
    dump_json(data)


def cmd_mirror(api: Api, base_url: str, args: argparse.Namespace) -> None:
    root = Path(args.dir).expanduser()
    root.mkdir(parents=True, exist_ok=True)
    index_path = root / "_index.json"
    index: dict[str, Any] = {"version": 1, "bookmarks": {}}
    if index_path.exists() and not args.refresh:
        try:
            index = json.loads(index_path.read_text(encoding="utf-8"))
            index.setdefault("bookmarks", {})
        except (OSError, json.JSONDecodeError) as exc:
            raise CliError(f"cannot read mirror index {index_path}: {exc}") from exc

    if args.sort is None:
        args.sort = "-created"
    bookmarks, total = fetch_all(api, args, args.max)
    added: list[str] = []
    skipped = 0
    failed: list[dict[str, str]] = []
    for bookmark in bookmarks:
        bid = bookmark["id"]
        recorded = index["bookmarks"].get(bid)
        if recorded and not args.refresh and (root / recorded["file"]).exists():
            skipped += 1
            continue
        try:
            _, body = api.request("GET", f"/bookmarks/{bid}/article.md", accept="text/markdown")
        except CliError as exc:
            failed.append({"id": bid, "title": bookmark.get("title", ""), "error": str(exc)})
            continue
        created = (bookmark.get("created") or "")[:10]
        name = f"{created}-{slugify(bookmark.get('title') or 'untitled')}-{bid}.md"
        (root / name).write_text(body.decode("utf-8", "replace"), encoding="utf-8")
        index["bookmarks"][bid] = {
            "file": name,
            "title": bookmark.get("title", ""),
            "created": bookmark.get("created", ""),
            "url": bookmark.get("url", ""),
        }
        added.append(name)

    index["updated"] = datetime.datetime.now().astimezone().isoformat(timespec="seconds")
    index_path.write_text(json.dumps(index, ensure_ascii=False, indent=1), encoding="utf-8")
    dump_json(
        {
            "dir": str(root),
            "matched": total,
            "added": len(added),
            "skipped": skipped,
            "failed": failed,
        }
    )


COMMANDS: dict[str, Callable[[Api, str, argparse.Namespace], None]] = {
    "search": cmd_search,
    "show": cmd_show,
    "read": cmd_read,
    "highlights": cmd_highlights,
    "labels": cmd_labels,
    "collections": cmd_collections,
    "mirror": cmd_mirror,
    "info": cmd_info,
}
