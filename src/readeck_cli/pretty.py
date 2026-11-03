"""Human-friendly terminal rendering for ``-o pretty`` (the default on a terminal)."""

from __future__ import annotations

import datetime
import os
import re
import shutil
import sys
import textwrap
import unicodedata
import zlib
from typing import Any

# Catppuccin Mocha: readable on dark terminals and still legible on light ones.
TEXT = "#cdd6f4"
SUB = "#a6adc8"
MUTED = "#7f849c"
FAINT = "#585b70"
BLUE = "#89b4fa"
MAUVE = "#cba6f7"
GREEN = "#a6e3a1"
YELLOW = "#f9e2af"
PEACH = "#fab387"
RED = "#f38ba8"
TEAL = "#94e2d5"
PINK = "#f5c2e7"
SURFACE = "#313244"
BASE = "#1e1e2e"
LABEL_COLORS = (BLUE, MAUVE, GREEN, PEACH, TEAL, PINK, YELLOW, RED)

# SGR, OSC (hyperlinks, text sizing) and APC (kitty graphics) sequences: zero width on screen.
ANSI_RX = re.compile(r"\x1b\[[0-9;:]*m|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)|\x1b_[^\x1b]*\x1b\\")


def kitty_like() -> bool:
    """kitty, Ghostty or WezTerm outside a multiplexer: they draw powerline caps natively."""
    if os.environ.get("TMUX") or os.environ.get("TERM", "").startswith(("screen", "tmux")):
        return False
    return (
        os.environ.get("TERM") in ("xterm-kitty", "xterm-ghostty")
        or "KITTY_WINDOW_ID" in os.environ
        or "GHOSTTY_RESOURCES_DIR" in os.environ
        or os.environ.get("TERM_PROGRAM", "").lower() in ("ghostty", "wezterm")
    )


class Term:
    """Styling for stdout: colors only on a terminal, honoring NO_COLOR and FORCE_COLOR.

    ``width`` and ``color`` override detection (used by the ``browse`` panes). ``rounded`` pills
    need a terminal that draws powerline glyphs itself; ``sizing`` (kitty's text-sizing protocol,
    for large titles) is only used when printing straight to a kitty terminal.
    """

    def __init__(self, width: int | None = None, color: bool | None = None) -> None:
        if color is not None:
            self.color = color and not os.environ.get("NO_COLOR")
        elif os.environ.get("NO_COLOR"):
            self.color = False
        elif os.environ.get("FORCE_COLOR"):
            self.color = True
        else:
            self.color = sys.stdout.isatty() and os.environ.get("TERM") != "dumb"
        self.truecolor = os.environ.get("COLORTERM", "").lower() in ("truecolor", "24bit")
        self.width = max(60, min(shutil.get_terminal_size((100, 24)).columns, 120)) if width is None else max(40, width)
        self.rounded = self.color and kitty_like()
        self.sizing = (
            self.color
            and width is None
            and sys.stdout.isatty()
            and not os.environ.get("TMUX")
            and (os.environ.get("TERM") == "xterm-kitty" or "KITTY_WINDOW_ID" in os.environ)
        )

    def rgb(self, hexcolor: str, layer: int) -> str:
        r, g, b = (int(hexcolor[i : i + 2], 16) for i in (1, 3, 5))
        if self.truecolor:
            return f"{layer};2;{r};{g};{b}"
        cube = 16 + 36 * round(r / 255 * 5) + 6 * round(g / 255 * 5) + round(b / 255 * 5)
        return f"{layer};5;{cube}"

    def s(
        self,
        text: Any,
        fg: str | None = None,
        bg: str | None = None,
        bold: bool = False,
        italic: bool = False,
        underline: bool = False,
    ) -> str:
        text = str(text)
        if not self.color or not text:
            return text
        codes = [self.rgb(fg, 38)] if fg else []
        if bg:
            codes.append(self.rgb(bg, 48))
        codes += [c for c, on in (("1", bold), ("3", italic), ("4", underline)) if on]
        return f"\x1b[{';'.join(codes)}m{text}\x1b[0m" if codes else text

    def big(self, text: str, scale: int = 2) -> str:
        """Text drawn ``scale`` times larger (kitty OSC 66); it covers ``scale`` rows, so the caller
        leaves ``scale - 1`` empty lines below it."""
        return f"\x1b]66;s={scale};{text}\x07" if self.sizing else text

    def pill(self, text: str, fg: str, bg: str) -> str:
        """A rounded chip on terminals that draw powerline caps, a padded block elsewhere."""
        if self.rounded:
            return self.s("\ue0b6", bg) + self.s(text, fg, bg, bold=True) + self.s("\ue0b4", bg)
        return self.s(f" {text} ", fg, bg, bold=True)

    def link(self, text: str, url: str) -> str:
        """OSC 8 hyperlink (clickable in most modern terminals)."""
        return f"\x1b]8;;{url}\x1b\\{text}\x1b]8;;\x1b\\" if self.color else text


# ---------------------------------------------------------------- text helpers
def vlen(text: str) -> int:
    """Visible width in terminal cells (ignores escapes, counts wide characters twice)."""
    return sum(2 if unicodedata.east_asian_width(c) in "WF" else 1 for c in ANSI_RX.sub("", text))


def pad(text: str, width: int) -> str:
    return text + " " * max(0, width - vlen(text))


def trunc(text: str, width: int) -> str:
    """Shorten plain text to ``width`` cells, ending with an ellipsis when cut."""
    if vlen(text) <= width:
        return text
    out, used = "", 0
    for char in text:
        cell = vlen(char)
        if used + cell > width - 1:
            break
        out, used = out + char, used + cell
    return out.rstrip() + "…"


def clip(text: str, width: int) -> str:
    """Cut styled text to ``width`` cells, keeping escape sequences intact."""
    out, used, pos = [], 0, 0
    for match in [*ANSI_RX.finditer(text), None]:
        end = match.start() if match else len(text)
        for char in text[pos:end]:
            cell = vlen(char)
            if used + cell > width:
                return "".join(out) + "\x1b[0m"
            out.append(char)
            used += cell
        if match:
            out.append(match.group())
            pos = match.end()
    return "".join(out)


def wrap(text: str, width: int) -> list[str]:
    return textwrap.wrap(" ".join(text.split()), width) if text else []


def parse_time(value: str | None) -> datetime.datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.datetime.fromisoformat(value)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=datetime.UTC)


def age_days(value: str | None) -> int | None:
    parsed = parse_time(value)
    return None if parsed is None else max(0, (datetime.datetime.now(datetime.UTC) - parsed).days)


def ago(value: str | None, short: bool = False) -> str:
    days = age_days(value)
    if days is None:
        return ""
    if days < 1:
        return "today"
    if short:
        for limit, size, unit in ((14, 1, "d"), (60, 7, "w"), (365, 30, "mo")):
            if days < limit:
                return f"{days // size}{unit}"
        return f"{days // 365}y"
    if days < 2:
        return "yesterday"
    for limit, size, unit in ((14, 1, "day"), (60, 7, "week"), (365, 30, "month")):
        if days < limit:
            n = days // size
            return f"{n} {unit}{'s' if n > 1 else ''} ago"
    n = days // 365
    return f"{n} year{'s' if n > 1 else ''} ago"


def date(value: str | None) -> str:
    parsed = parse_time(value)
    return f"{parsed.day} {parsed:%b %Y}" if parsed else ""


def label_color(name: str) -> str:
    return LABEL_COLORS[zlib.crc32(name.encode()) % len(LABEL_COLORS)]


def progress_bar(t: Term, percent: int, width: int) -> str:
    filled = round(width * max(0, min(percent, 100)) / 100)
    color = GREEN if percent >= 100 else BLUE
    return t.s("▰" * filled, color) + t.s("▱" * (width - filled), FAINT)


def status_icon(t: Term, bookmark: dict[str, Any]) -> str:
    progress = bookmark.get("read_progress") or 0
    if progress >= 100:
        return t.s("✓", GREEN)
    return t.s("◐", BLUE) if progress else t.s("•", PEACH)


def type_icon(t: Term, bookmark: dict[str, Any]) -> str:
    return {"video": t.s("▶", RED), "photo": t.s("◩", TEAL)}.get(bookmark.get("type") or "", " ")


def tags(t: Term, labels: list[str] | None) -> str:
    return " ".join(t.s(f"#{name}", label_color(name)) for name in labels or [])


def key(t: Term, name: str) -> str:
    return "  " + t.s(f"{name:<11}", MUTED)


def rule(t: Term) -> str:
    return "  " + t.s("─" * (t.width - 4), FAINT)


# ------------------------------------------------------------------- bookmarks
def bookmark(data: dict[str, Any]) -> None:
    """Detail view for ``show``: a header bar, then aligned metadata rows."""
    print("\n".join(bookmark_lines(data, Term())))


def bookmark_head(data: dict[str, Any], t: Term) -> list[str]:
    """Title and metadata rows shared by ``show``, the ``browse`` preview and ``read``."""
    w = t.width
    title = data.get("title") or "(untitled)"
    badge = t.pill((data.get("type") or "article").upper(), BASE, MAUVE)
    indent = "  " + " " * vlen(badge) + "  "
    star = "  " + t.s("★", YELLOW) if data.get("is_marked") else ""
    scale = 2 if t.sizing else 1
    parts = wrap(title, max(10, (w - len(indent) - 3) // scale))
    lines = []
    for i, part in enumerate(parts):
        lead = "  " + badge + "  " if i == 0 else indent
        lines.append(lead + t.s(t.big(part, scale), BLUE, bold=True) + (star if i == len(parts) - 1 else ""))
        lines += [""] * (scale - 1)
    lines.append("")
    site = data.get("site_name") or data.get("site")
    if site:
        lines.append(key(t, "Site") + t.s(site, MAUVE, bold=True))
    if data.get("authors"):
        lines.append(key(t, "Authors") + t.s(", ".join(data["authors"]), TEXT))
    if date(data.get("created")):
        lines.append(key(t, "Saved") + t.s(date(data["created"]), TEXT) + "  " + t.s(ago(data["created"]), MUTED))
    if date(data.get("published")):
        lines.append(key(t, "Published") + t.s(date(data["published"]), TEXT))
    progress = data.get("read_progress") or 0
    extent = []
    if data.get("reading_time"):
        extent.append(f"{data['reading_time']} min")
    if data.get("word_count"):
        extent.append(f"{data['word_count']:,} words")
    lines.append(
        key(t, "Progress")
        + progress_bar(t, progress, 20)
        + "  "
        + t.s(f"{progress}%", TEXT, bold=True)
        + (t.s(f"  of {' · '.join(extent)}", MUTED) if extent else "")
    )
    if data.get("labels"):
        lines.append(key(t, "Labels") + " ".join(t.pill(x, label_color(x), SURFACE) for x in data["labels"]))
    state = [
        t.s("★ favorite", YELLOW) if data.get("is_marked") else t.s("☆ not favorite", MUTED),
        t.s("▣ archived", MUTED) if data.get("is_archived") else t.s("▢ in inbox", GREEN),
    ]
    if data.get("lang"):
        state.append(t.s(data["lang"], SUB))
    lines.append(key(t, "State") + t.s("  ·  ", MUTED).join(state))
    return lines


def bookmark_lines(data: dict[str, Any], t: Term) -> list[str]:
    """Detail view: the shared head, then note, description and links."""
    w = t.width
    lines = bookmark_head(data, t)
    for i, part in enumerate(wrap(data.get("note") or "", w - 15)):
        lines.append(key(t, "Note" if i == 0 else "") + t.s(part, YELLOW))
    description = wrap(data.get("description") or "", w - 6)
    if description:
        lines += ["", rule(t), *("  " + t.s(part, TEXT) for part in description), rule(t)]
    lines.append("")
    if data.get("url"):
        lines.append(key(t, "Source") + t.link(t.s(data["url"], BLUE, underline=True), data["url"]))
    if data.get("readeck_url"):
        lines.append(key(t, "Readeck") + t.link(t.s(data["readeck_url"], MAUVE, underline=True), data["readeck_url"]))
    lines.append(key(t, "ID") + t.s(data.get("id", ""), SUB))
    return lines


def date_group(created: str | None) -> str:
    days = age_days(created)
    if days is None:
        return "Undated"
    for limit, name in ((7, "This week"), (31, "This month"), (92, "Last 3 months"), (365, "This year")):
        if days < limit:
            return name
    return "Older"


def bookmark_row(t: Term, item: dict[str, Any], title_w: int) -> str:
    """One result row: status, favorite, title, type, site, reading time, age (title_w + 33 cells)."""
    archived = bool(item.get("is_archived"))
    title = trunc(item.get("title") or "(untitled)", title_w)
    site = trunc(item.get("site_name") or item.get("site") or "", 13)
    minutes = f"{item['reading_time']}m" if item.get("reading_time") else ""
    return (
        status_icon(t, item)
        + " "
        + (t.s("★", YELLOW) if item.get("is_marked") else " ")
        + " "
        + pad(t.s(title, MUTED if archived else TEXT, bold=not archived), title_w)
        + " "
        + type_icon(t, item)
        + " "
        + pad(t.s(site, MAUVE), 14)
        + t.s(f"{minutes:>5}", SUB)
        + "  "
        + t.s(f"{ago(item.get('created'), short=True):>5}", MUTED)
    )


def bookmark_list(bookmarks: list[dict[str, Any]], total: int, offset: int, filters: str, grouped: bool) -> None:
    """Result table for ``search``: one row per bookmark, grouped by save date when sorted by it."""
    t = Term()
    w = t.width
    title_w = w - 37
    shown = f"{offset + 1}–{offset + len(bookmarks)}" if bookmarks else "0"
    summary = t.s(shown, TEXT, bold=True) + t.s(f" of {total} bookmarks", MUTED)
    lines = ["  " + summary + (t.s("  ·  ", MUTED) + t.s(filters, BLUE) if filters else ""), ""]
    if not bookmarks:
        print("\n".join([*lines, "  " + t.s("No bookmarks match.", SUB)]))
        return
    lines.append(t.s(f"      {'TITLE':<{title_w + 2}} {'SITE':<14}{'TIME':>5}  {'SAVED':>5}", MUTED))
    group = None
    for item in bookmarks:
        if grouped and date_group(item.get("created")) != group:
            group = date_group(item.get("created"))
            lines.append("  " + t.s(group, MAUVE, bold=True) + " " + t.s("─" * (w - 5 - len(group)), FAINT))
        lines.append("  " + bookmark_row(t, item, title_w))
        ident = t.s(item.get("id", ""), FAINT)
        if item.get("readeck_url"):
            ident = t.link(ident, item["readeck_url"])
        progress = progress_bar(t, item.get("read_progress") or 0, 6)
        lines.append("      " + "  ".join(x for x in (ident, tags(t, item.get("labels")), progress) if x))
    lines.append("")
    legend = (
        f"{t.s('✓', GREEN)} read  {t.s('◐', BLUE)} reading  {t.s('•', PEACH)} unread  "
        f"{t.s('★', YELLOW)} favorite  {t.s('dimmed = archived', MUTED)}"
    )
    lines.append("  " + legend)
    hint = "readeck show <id>"
    if offset + len(bookmarks) < total:
        hint = f"more: --offset {offset + len(bookmarks)} or --all  ·  {hint}"
    lines.append("  " + t.s(hint, SUB))
    print("\n".join(lines))


# --------------------------------------------------------------- other outputs
def highlights(items: list[dict[str, Any]]) -> None:
    t = Term()
    if not items:
        print("  " + t.s("No highlights.", SUB))
        return
    lines: list[str] = []
    current = None
    for item in items:
        source = item.get("bookmark_id")
        if source and source != current:
            current = source
            if lines:
                lines.append("")
            title = item.get("bookmark_title") or source
            lines.append("  " + t.s(trunc(title, t.width - 20), TEXT, bold=True) + "  " + t.s(source, FAINT))
        for part in wrap(item.get("text") or "", t.width - 8):
            lines.append("  " + t.s("▎", YELLOW) + "  " + t.s(part, TEXT, italic=True))
        meta = " · ".join(x for x in (date(item.get("created")), item.get("id", "")) if x)
        lines.append("     " + t.s(meta, MUTED))
    print("\n".join(lines))


def labels(items: list[dict[str, Any]]) -> None:
    t = Term()
    if not items:
        print("  " + t.s("No labels.", SUB))
        return
    name_w = min(max(vlen(x["name"]) for x in items), 30)
    top = max(x["count"] for x in items) or 1
    for item in items:
        bar = "▇" * max(1, round(20 * item["count"] / top))
        name = pad(t.s(trunc(item["name"], name_w), label_color(item["name"]), bold=True), name_w)
        print(f"  {name}  {t.s(f'{item["count"]:>5}', TEXT)}  {t.s(bar, label_color(item['name']))}")


def collections(items: list[dict[str, Any]]) -> None:
    t = Term()
    if not items:
        print("  " + t.s("No collections.", SUB))
        return
    for item in items:
        pin = t.s("★", YELLOW) if item.get("is_pinned") else " "
        filters = "  ".join(
            t.s(f"{k}=", MUTED) + t.s(",".join(map(str, v)) if isinstance(v, list) else v, SUB)
            for k, v in item.items()
            if k not in ("id", "name", "is_pinned")
        )
        print(f"  {pin} {t.s(item.get('name', ''), TEXT, bold=True)}  {t.s(item.get('id', ''), FAINT)}")
        if filters:
            print(f"    {filters}")


def record(data: Any, indent: int = 2) -> None:
    """Generic aligned key/value view (info, auth, mirror and other receipts)."""
    t = Term()
    if not isinstance(data, dict):
        print(" " * indent + t.s(data, TEXT))
        return
    width = min(max((len(str(k)) for k in data), default=0) + 2, 24)
    for name, value in data.items():
        label = " " * indent + t.s(f"{str(name).replace('_', ' '):<{width}}", MUTED)
        if isinstance(value, dict) and value:
            print(label)
            record(value, indent + 2)
        elif isinstance(value, list) and value and isinstance(value[0], dict):
            print(label)
            for entry in value:
                print(" " * (indent + 2) + t.s("• ", FAINT) + t.s("  ".join(str(v) for v in entry.values()), SUB))
        elif value is True:
            print(label + t.s("✓ yes", GREEN))
        elif value is False:
            print(label + t.s("✗ no", RED))
        elif value in (None, "", [], {}):
            print(label + t.s("—", FAINT))
        else:
            text = ", ".join(map(str, value)) if isinstance(value, list) else str(value)
            print(label + t.s(text, TEXT))
