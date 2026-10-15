"""Formatting and value-normalization helpers."""

from __future__ import annotations

import json
import re
import sys
from typing import Any

RELATIVE_TIME_RX = re.compile(r"^\+?(\d+)([dwmy])$")
NEGATIVE_TIME_RX = re.compile(r"^-\d+[dwmy]$")

#: Bookmark fields kept in agent-facing summaries (API extras such as
#: ``resources``/``href`` are dropped on purpose).
SUMMARY_KEYS = (
    "id",
    "title",
    "url",
    "site_name",
    "site",
    "authors",
    "description",
    "lang",
    "type",
    "created",
    "published",
    "labels",
    "note",
    "is_marked",
    "is_archived",
    "read_progress",
    "reading_time",
    "word_count",
    "has_article",
    "loaded",
)


def time_token(value: str | None) -> str | None:
    """Normalize --since/--until values. Accepts 6m, -6m, 30d, 2w, 1y or dates."""
    if value is None:
        return None
    value = value.strip()
    if not value:
        return None
    match = RELATIVE_TIME_RX.match(value)
    if match:
        return f"-{match.group(1)}{match.group(2)}"
    if NEGATIVE_TIME_RX.match(value):
        return value
    return value  # absolute date, validated by the server


def bool_param(value: bool | None) -> str | None:
    """Serialize an optional boolean for the Readeck API query string."""
    if value is None:
        return None
    return "true" if value else "false"


def summarize(bookmark: dict[str, Any], base_url: str) -> dict[str, Any]:
    """Reduce a bookmark object to the fields agents need, plus a Readeck URL."""
    out: dict[str, Any] = {}
    for key in SUMMARY_KEYS:
        if key not in bookmark:
            continue
        value = bookmark[key]
        if value is None or value == "" or value == []:
            continue
        out[key] = value
    out["readeck_url"] = f"{base_url}/bookmarks/{bookmark['id']}"
    return out


def slugify(value: str, maxlen: int = 60) -> str:
    """Turn a title into a filesystem-safe slug."""
    value = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return value[:maxlen].rstrip("-") or "untitled"


def mask_token(token: str) -> str:
    """Mask a token for display: first and last four characters only."""
    if not token:
        return ""
    if len(token) <= 8:
        return "*" * len(token)
    return f"{token[:4]}…{token[-4:]}"


def dump_json(payload: Any) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2))


def print_bookmark_table(bookmarks: list[dict[str, Any]], base_url: str) -> None:
    if not bookmarks:
        print("no results")
        return
    for bookmark in bookmarks:
        flags = []
        if bookmark.get("is_marked"):
            flags.append("*")
        if bookmark.get("is_archived"):
            flags.append("archived")
        if bookmark.get("read_progress") == 100:
            flags.append("read")
        date = (bookmark.get("created") or "")[:10]
        title = (bookmark.get("title") or "(untitled)")[:88]
        print(f"{bookmark.get('id', ''):10} {date}  {title}")
        extra = " ".join(
            str(x)
            for x in (
                " ".join(flags),
                ",".join(bookmark.get("labels") or []),
                bookmark.get("site_name") or bookmark.get("site") or "",
            )
            if x
        )
        if extra:
            print(f"{'':10}           {extra}")
        sys.stdout.flush()
