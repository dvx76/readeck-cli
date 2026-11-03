"""Terminal rendering of an article's Markdown for ``readeck read`` (the default on a terminal).

The layout follows the ``show`` view: the same title and metadata header, then the article in a
reading column. On wide terminals a margin column on the right takes the note, the table of
contents, figure captions, quote attributions and link references. Images are drawn with kitty's
graphics protocol where the terminal supports it (see ``images``).
"""

from __future__ import annotations

import os
import re
import shutil
import sys
from typing import Any

from readeck_cli import images
from readeck_cli.api import Api
from readeck_cli.pretty import (
    BASE,
    BLUE,
    FAINT,
    GREEN,
    MAUVE,
    MUTED,
    PEACH,
    PINK,
    SUB,
    SURFACE,
    TEAL,
    TEXT,
    YELLOW,
    Term,
    bookmark_head,
    key,
    pad,
    text_sizing,
    trunc,
    vlen,
)

LAVENDER = "#b4befe"
SKY = "#89dceb"
OVERLAY = "#6c7086"
SURFACE2 = "#45475a"
MANTLE = "#181825"

MAIN_WIDE = 84  # reading column when the margin is shown
MARGIN_MIN = 28
MARGIN_MAX = 36
GAP = 4
SUPERSCRIPT = str.maketrans("0123456789", "⁰¹²³⁴⁵⁶⁷⁸⁹")


# ------------------------------------------------------------------- parsing
def front_matter(text: str) -> tuple[dict[str, Any], str]:
    """Split Readeck's YAML front matter (flat keys and ``- item`` lists) from the body."""
    meta: dict[str, Any] = {}
    if not text.startswith("---\n"):
        return meta, text
    end = text.find("\n---\n", 4)
    if end == -1:
        return meta, text
    name = None
    for line in text[4:end].splitlines():
        if line.startswith("- ") and name:
            meta.setdefault(name, []).append(line[2:].strip().strip('"'))
        elif ":" in line and not line.startswith(" "):
            name, _, value = line.partition(":")
            name, value = name.strip(), value.strip().strip('"')
            if value:
                meta[name] = value
    return meta, text[end + 5 :]


LIST_RX = re.compile(r"(\s*)([-*+]|\d+[.)])\s+(.*)")
FOOTNOTE_RX = re.compile(r"\[\^([^\]\s]+)\]:\s*(.*)")
IMAGE_RX = re.compile(r"\s*\[?!\[([^\]]*)\]\(([^)\s]+)[^)]*\)(?:\]\([^)]*\))?\s*")
TABLE_RULE_RX = re.compile(r"\s*\|?\s*:?-+:?\s*(\|\s*:?-+:?\s*)*\|?\s*")
BLOCK_START_RX = re.compile(r"(#{1,6}\s|```|~~~|>|\[\^[^\]\s]+\]:|\s*([-*+]|\d+[.)])\s)")


def blocks(text: str) -> list[tuple]:
    """Block structure: ("h", level, text), ("p", text), ("img", alt, url), ("code", lang, lines),
    ("quote", blocks), ("list", [(depth, marker, text)]), ("table", aligns, head, rows), ("hr",),
    ("fn", name, text) for a footnote definition (``[^name]: text``)."""
    lines, i, out = text.split("\n"), 0, []
    while i < len(lines):
        line = lines[i]
        if not line.strip():
            i += 1
        elif m := re.match(r"(#{1,6})\s+(.*?)\s*#*\s*$", line):
            out.append(("h", len(m[1]), m[2]))
            i += 1
        elif line.lstrip().startswith(("```", "~~~")):
            fence = line.lstrip()[:3]
            lang, body = line.lstrip()[3:].strip().split(" ")[0], []
            i += 1
            while i < len(lines) and not lines[i].lstrip().startswith(fence):
                body.append(lines[i].expandtabs(4))
                i += 1
            out.append(("code", lang, body))
            i += 1
        elif re.fullmatch(r"\s*([-*_])(\s*\1){2,}\s*", line):
            out.append(("hr",))
            i += 1
        elif m := FOOTNOTE_RX.fullmatch(line):
            text = m[2]
            i += 1
            while i < len(lines) and lines[i].startswith(("    ", "\t")) and lines[i].strip():
                text += " " + lines[i].strip()
                i += 1
            out.append(("fn", m[1], text))
        elif m := IMAGE_RX.fullmatch(line):
            out.append(("img", m[1], m[2]))
            i += 1
        elif line.startswith(">"):
            body = []
            while i < len(lines) and lines[i].startswith(">"):
                body.append(re.sub(r"^> ?", "", lines[i]))
                i += 1
            out.append(("quote", blocks("\n".join(body))))
        elif "|" in line and i + 1 < len(lines) and TABLE_RULE_RX.fullmatch(lines[i + 1]):
            aligns = [
                "c" if c.startswith(":") and c.endswith(":") else "r" if c.endswith(":") else "l"
                for c in table_cells(lines[i + 1])
            ]
            head, rows = table_cells(line), []
            i += 2
            while i < len(lines) and "|" in lines[i]:
                rows.append(table_cells(lines[i]))
                i += 1
            out.append(("table", aligns, head, rows))
        elif LIST_RX.match(line):
            items: list[list] = []
            while i < len(lines):
                if m := LIST_RX.match(lines[i]):
                    items.append([len(m[1].expandtabs(4)), m[2], m[3]])
                elif lines[i].strip() and items:
                    items[-1][2] += " " + lines[i].strip()
                elif not (i + 1 < len(lines) and LIST_RX.match(lines[i + 1])):
                    break
                i += 1
            depths = sorted({x[0] for x in items})
            out.append(("list", [(depths.index(indent), mark, text) for indent, mark, text in items]))
        else:
            para = [line.strip()]
            i += 1
            while i < len(lines) and lines[i].strip() and not BLOCK_START_RX.match(lines[i]):
                para.append(lines[i].strip())
                i += 1
            out.append(("p", " ".join(para)))
    return out


def table_cells(line: str) -> list[str]:
    return [c.strip() for c in re.split(r"(?<!\\)\|", line.strip().strip("|"))]


INLINE_RX = re.compile(
    r"`(?P<code>[^`]+)`|\[\^(?P<fn>[^\]\s]+)\]|!\[(?P<alt>[^\]]*)\]\((?P<src>[^)\s]+)[^)]*\)|\[(?P<text>[^\]]+)\]\((?P<href>[^)\s]+)[^)]*\)"
    r"|==(?P<mark>[^=]+?)==|\*\*(?P<b>.+?)\*\*|(?<!\w)__(?P<b2>.+?)__(?!\w)"
    r"|\*(?P<i>[^*\s][^*]*?)\*|(?<!\w)_(?P<i2>[^_]+?)_(?!\w)|<(?P<auto>https?://[^>\s]+)>"
)
ESCAPED_RX = re.compile(r"\\([\\`*_{}\[\]()#+\-.!|=<>~])")


def spans(text: str, flags: frozenset = frozenset(), url: str | None = None) -> list[tuple]:
    """Inline Markdown as (text, flags, link) runs; flags: b, i, code, mark (highlight), link."""
    out, pos = [], 0
    for m in INLINE_RX.finditer(text):
        if m.start() > pos:
            out.append((ESCAPED_RX.sub(r"\1", text[pos : m.start()]), flags, url))
        g = m.groupdict()
        if g["code"] is not None:
            out.append((g["code"], flags | {"code"}, url))
        elif g["fn"] is not None:  # footnote reference: a link to its definition
            label = g["fn"].translate(SUPERSCRIPT) if g["fn"].isdigit() else f"[{g['fn']}]"
            out.append((label, flags | {"link"}, f"#fn:{g['fn']}"))
        elif g["src"] is not None:
            out.append(("◩ " + (g["alt"] or "image"), flags | {"link"}, g["src"]))
        elif g["href"] is not None:
            out += spans(g["text"], flags | {"link"}, g["href"])
        elif g["auto"] is not None:
            out.append((g["auto"], flags | {"link"}, g["auto"]))
        elif g["mark"] is not None:
            out += spans(g["mark"], flags | {"mark"}, url)
        elif (g["b"] or g["b2"]) is not None:
            out += spans(g["b"] or g["b2"], flags | {"b"}, url)
        else:
            out += spans(g["i"] or g["i2"], flags | {"i"}, url)
        pos = m.end()
    if pos < len(text):
        out.append((ESCAPED_RX.sub(r"\1", text[pos:]), flags, url))
    return out


def plain(text: str) -> str:
    return "".join(t for t, _, _ in spans(text))


def wrap(runs: list[tuple], width: int, style) -> list[tuple[str, set, list[tuple[int, int, str, str]]]]:
    """Word-wrap styled runs; returns (rendered line, flags used on it, links on it as (first column,
    end column, URL, text)). Spaces inside a highlight, code span or link keep its styling so the
    background or underline is continuous."""
    tokens, word = [], []
    for text, flags, url in runs:
        for part in re.split(r"( )", re.sub(r"\s+", " ", text)):
            if part == " ":
                if word:
                    tokens.append(("w", word))
                    word = []
                tokens.append(("s", flags, url))
            elif part:
                word.append((part, flags, url))
    if word:
        tokens.append(("w", word))
    lines, line, used, space = [], [], 0, None
    for token in tokens:
        if token[0] == "s":
            space = token if line else None
            continue
        size = sum(vlen(c) for c, _, _ in token[1])
        if line and used + size + (1 if space else 0) > width:
            lines.append(line)
            line, used, space = [], 0, None
        if space:
            line.append(space)
            used += 1
            space = None
        line.append(token)
        used += size
    if line:
        lines.append(line)
    out = []
    for line in lines:
        text, used_flags, links, col = "", set(), [], 0
        for token in line:
            pieces = [(" ", token[1], token[2])] if token[0] == "s" else token[1]
            for chunk, flags, url in pieces:
                inside = token[0] == "w" or flags & {"mark", "code", "link"}
                text += style(chunk, flags, url) if inside else chunk
                size = vlen(chunk)
                if url and "link" in flags:
                    if links and links[-1][1] == col and links[-1][2] == url:
                        links[-1] = (links[-1][0], col + size, url, links[-1][3] + chunk)
                    else:
                        links.append((col, col + size, url, chunk))
                col += size
                if token[0] == "w":
                    used_flags |= flags
        out.append((text, used_flags, links))
    return out


KEYWORDS = frozenset(
    "auto break case char const continue default do double else enum extern float for goto if inline int long "  # noqa: SIM905
    "return short signed sizeof static struct switch typedef union unsigned void volatile while def class import "
    "from as with try except finally raise yield lambda pass None True False self async await fn let mut pub impl "
    "use mod match func package var type interface go defer range nil function new this null export bool true "
    "false not and or in is elif local then end".split()
)
CODE_RX = re.compile(
    r"(?P<com>//.*|/\*.*?\*/|--\s.*)|(?P<str>\"(?:\\.|[^\"\\])*\"|'(?:\\.|[^'\\])*'|`[^`]*`)|(?P<num>\b\d[\w.]*)"
    r"|(?P<word>[A-Za-z_]\w*)(?P<call>(?=\s*\())?|(?P<op>[-+*/=<>!&|%^~?:]+)"
)
HASH_COMMENT_LANGS = frozenset(
    "python py sh bash shell zsh console yaml yml toml ruby rb perl r make dockerfile".split()  # noqa: SIM905
)


def highlight(t: Term, line: str, lang: str) -> str:
    """Lightweight syntax coloring: comments, strings, numbers, keywords, calls."""
    if lang.lower() in HASH_COMMENT_LANGS and "#" in line:
        code, _, comment = line.partition("#")
        return highlight(t, code, "") + t.s("#" + comment, OVERLAY, italic=True)
    out, pos = "", 0
    for m in CODE_RX.finditer(line):
        out += t.s(line[pos : m.start()], TEXT)
        token = m.group()
        if m["com"]:
            out += t.s(token, OVERLAY, italic=True)
        elif m["str"]:
            out += t.s(token, GREEN)
        elif m["num"]:
            out += t.s(token, PEACH)
        elif m["word"] in KEYWORDS:
            out += t.s(token, MAUVE)
        elif m["call"] is not None:
            out += t.s(token, BLUE)
        elif m["word"]:
            out += t.s(token, TEXT)
        else:
            out += t.s(token, SKY)
        pos = m.end()
    return out + t.s(line[pos:], TEXT)


# ----------------------------------------------------------------- rendering
class Row:
    """One output line: a main-column string, an optional margin string, a prefix (an image
    drawn without moving the cursor) and the links on it as (start, end, URL, text) columns of
    ``main``. ``full`` rows span the margin too, so notes skip them."""

    __slots__ = ("full", "links", "main", "margin", "prefix")

    def __init__(self, main: str = "", full: bool = False, prefix: str = "", links: list | None = None):
        self.main, self.full, self.prefix, self.margin, self.links = main, full, prefix, "", links or []


class Document:
    """A rendered article: its lines, and what the pager needs to navigate them.

    ``links`` are (row, start column, end column, URL, text); ``headings`` are (row, level, number,
    title) and ``anchors`` map heading slugs and footnote names to rows. ``tall`` rows hold a 2×
    title that also covers the row below. Images are either Unicode placeholder text in the lines,
    shown once ``uploads`` are written, or drawn at the cursor: ``prefixes`` per row when printing,
    ``pictures`` (row, column, cols, rows, image ID, kitty keys, payload) in the pager.
    """

    def __init__(self, renderer: Renderer, lines: list[str]):
        self.lines = lines
        self.mode = renderer.mode
        self.page_url = renderer.page_url
        self.title = renderer.title
        self.headings = renderer.headings
        self.anchors = renderer.anchors
        self.uploads = renderer.uploads
        self.pictures = renderer.pictures
        self.prefixes = {n: row.prefix for n, row in enumerate(renderer.rows) if row.prefix}
        self.links = [
            (n, renderer.left + start, renderer.left + end, url, text)
            for n, row in enumerate(renderer.rows)
            for start, end, url, text in row.links
        ]
        self.tall = {n for n, line in enumerate(lines) if "\x1b]66;" in line}

    def printed(self) -> str:
        """The article as printed straight to the terminal."""
        body = "\n".join(self.prefixes.get(n, "") + line for n, line in enumerate(self.lines))
        return "".join(self.uploads) + body + "\n"


class Renderer:
    def __init__(
        self,
        t: Term,
        loader: images.Loader | None,
        cell: tuple[float, float],
        rows: int,
        mode: str | None = None,
        page_url: str | None = None,
    ):
        self.t = t
        self.loader = loader
        self.cell = cell
        self.screen_rows = rows
        self.mode = mode if loader else None
        self.page_url = page_url
        self.title = ""
        cols = t.width
        self.wide = cols >= 2 + MAIN_WIDE + GAP + MARGIN_MIN + 2
        if self.wide:
            self.width = MAIN_WIDE
            self.margin_width = min(MARGIN_MAX, cols - 2 - MAIN_WIDE - GAP - 2)
            total = MAIN_WIDE + GAP + self.margin_width
        else:
            self.width = min(cols - 4, 100)
            self.margin_width = 0
            total = self.width
        self.left = max(2, (cols - total) // 2)
        self.total = total
        self.rows: list[Row] = []
        self.notes: list[tuple[int, list[str]]] = []
        self.links: list[str] = []
        self.sections = self.subsections = self.figures = 0
        self.headings: list[tuple[int, int, str, str]] = []
        self.anchors: dict[str, int] = {}
        self.uploads: list[str] = []
        self.pictures: list[tuple] = []
        self.footnotes = 0

    # --------------------------------------------------------------- helpers
    def add(self, main: str = "", **kw) -> int:
        self.rows.append(Row(main, **kw))
        return len(self.rows) - 1

    def note(self, at: int, lines: list[str]) -> None:
        """Queue margin lines next to row ``at`` (they flow down past earlier notes)."""
        if self.wide and lines:
            self.notes.append((at, lines))

    def margin_text(self, text: str, color: str, italic: bool = False) -> list[str]:
        return [
            s
            for s, *_ in wrap(
                [(text, frozenset(), None)], self.margin_width, lambda c, f, u: self.t.s(c, color, italic=italic)
            )
        ]

    def style(self, text: str, flags: frozenset, url: str | None) -> str:
        t = self.t
        if "sup" in flags:
            return t.s(text, MAUVE, bold=True)
        fg, bg, bold, italic, underline = TEXT, None, False, False, False
        if "b" in flags:
            fg, bold = PEACH, True
        if "i" in flags:
            fg, italic = PINK, True
        if "code" in flags:
            fg, bg = GREEN, SURFACE
        if "mark" in flags:
            fg, bg = BASE, YELLOW
        if "link" in flags:
            fg, underline = SKY, True
        styled = t.s(text, fg, bg, bold, italic, underline)
        return t.link(styled, url) if url else styled

    def with_references(self, runs: list[tuple]) -> list[tuple]:
        """In the wide layout, number each link with a superscript that points to the margin."""
        if not self.wide:
            return runs
        out, previous = [], None
        for text, flags, url in [*runs, ("", frozenset(), None)]:
            if previous and url != previous and not previous.startswith("#fn:"):  # footnotes are numbered already
                self.links.append(previous)
                number = len(self.links)
                out.append((str(number).translate(SUPERSCRIPT), frozenset({"sup", f"ref:{number}"}), None))
            if text:
                out.append((text, flags, url))
            previous = url if "link" in flags else None
        return out

    def text(self, markdown: str, indent: int = 2, style=None) -> None:
        """A wrapped paragraph in the main column; link references go to the margin."""
        runs = self.with_references(spans(markdown))
        for line, flags, links in wrap(runs, self.width - indent, style or self.style):
            row = self.add(" " * indent + line, links=shifted(links, indent))
            for flag in sorted(f for f in flags if f.startswith("ref:")):
                number = int(flag[4:])
                self.note(row, self.reference(number))

    def reference(self, number: int) -> list[str]:
        t, url = self.t, self.links[number - 1]
        anchor = fragment(url, self.page_url)
        if anchor is not None:  # a jump within the article
            shown = "↪ #" + READECK_ID_PREFIX_RX.sub("", anchor)
            return [t.s(str(number).translate(SUPERSCRIPT), MAUVE, bold=True) + " " + t.s(shown, LAVENDER)]
        bare = re.sub(r"^[a-z]+://(www\.)?", "", url)
        host, _, path = bare.partition("/")
        lines = [t.s(str(number).translate(SUPERSCRIPT), MAUVE, bold=True) + " " + t.link(t.s(host, LAVENDER), url)]
        if path:
            lines.append("  " + t.s(trunc("/" + path, self.margin_width - 2), MUTED))
        return lines

    def picture(self, url: str, max_cols: int, max_rows: int) -> tuple[int, int, str, bytes] | None:
        """(cols, rows, kitty keys, payload) for an image that fits ``max_cols``×``max_rows`` cells."""
        loaded = self.loader.get(url) if self.loader and len(self.pictures) < images.MAX_IMAGES else None
        if not loaded:
            return None
        width, height, keys, payload = loaded
        cw, ch = self.cell
        cols = max(1, min(max_cols, round(width / cw)))
        rows = max(1, round(height * cols * cw / width / ch))
        if rows > max_rows:
            rows = max_rows
            cols = max(1, min(max_cols, round(width * rows * ch / height / cw)))
        return cols, rows, keys, payload

    def frame(self, url: str, inner: int, max_rows: int, full: bool) -> int | None:
        """A rounded frame with the image centered inside; returns the row of its top border."""
        picture = self.picture(url, inner - 2, max_rows)
        if not picture:
            return None
        cols, rows, keys, payload = picture
        t = self.t
        image = images.image_id(len(self.pictures) + 1)
        top = self.add(t.s("╭" + "─" * inner + "╮", SURFACE2), full=full)
        side = t.s("│", SURFACE2)
        before = (inner - cols) // 2
        if self.mode == images.PLACEHOLDERS:
            self.uploads.append(images.upload(keys, payload, image, cols, rows))
            for k in range(rows):
                cells = images.placeholder_row(image, k, cols)
                self.add(side + " " * before + cells + " " * (inner - cols - before) + side, full=full)
        else:
            column = self.left + 1 + before
            escape = images.escape(keys, payload, cols, rows)
            self.add(side + " " * inner + side, full=full, prefix=place(escape, rows, column))
            for _ in range(rows - 1):
                self.add(side + " " * inner + side, full=full)
        self.pictures.append((top + 1, self.left + 1 + before, cols, rows, image, keys, payload))
        return top

    def heading(self, row: int, level: int, number: str, title: str) -> None:
        self.headings.append((row, level, number, title))
        for name in (slug(title), loose(title)):
            self.anchors.setdefault(name, row)

    # ---------------------------------------------------------------- blocks
    def header(self, bookmark: dict[str, Any], cover: str | None, contents: list[str], note: str | None) -> None:
        t = self.t
        if cover:
            inner = self.total - 2
            if self.frame(cover, inner, min(18, max(6, self.screen_rows // 2)), full=True) is not None:
                self.add(t.s("╰" + "─" * inner + "╯", SURFACE2), full=True)
                self.add(full=True)
        head = bookmark_head(bookmark, _resized(t, self.width + 2))
        for line in head:
            self.add(line[2:] if line.startswith("  ") else line, full=t.sizing and not line)
        if contents and not self.wide:
            room, line, label = self.width - 11, "", "Contents"
            for n, title in enumerate(contents, 1):
                entry = t.s(f"{n} ", MAUVE, bold=True) + t.s(trunc(title, room - 3), SUB)
                if line and vlen(line) + 5 + vlen(entry) > room:
                    self.add(key(t, label)[2:] + line)
                    line, label = "", ""
                line += (t.s("  ›  ", FAINT) if line else "") + entry
            self.add(key(t, label)[2:] + line)
        self.add()
        start = len(self.rows)
        if note and self.wide:
            self.note(
                start,
                [
                    t.s("✎ ", YELLOW) + t.s("Your note", YELLOW, bold=True),
                    *self.margin_text(note, YELLOW, italic=True),
                    "",
                ],
            )
        elif note:
            inner = self.width - 2
            self.add(t.s("╭─", YELLOW) + t.s(" ✎ Note ", YELLOW, bold=True) + t.s("─" * (inner - 9) + "╮", YELLOW))
            for line, *_ in wrap(spans(note), inner - 4, lambda c, f, u: t.s(c, YELLOW, italic=True)):
                self.add(t.s("│", YELLOW) + "  " + pad(line, inner - 3) + t.s(" │", YELLOW))
            self.add(t.s("╰" + "─" * inner + "╯", YELLOW))
            self.add()
        if contents and self.wide:
            lines = [
                t.s(f"§{n} ", MAUVE) + t.s(trunc(title, self.margin_width - 4), SUB)
                for n, title in enumerate(contents, 1)
            ]
            self.note(start, [t.s("Contents", MUTED, bold=True), *lines])

    def block(self, block: tuple) -> None:
        kind, t = block[0], self.t
        if kind == "p":
            self.text(block[1])
            self.add()
        elif kind == "h" and block[1] <= 2:
            self.sections += 1
            self.subsections = 0
            label = t.pill(str(self.sections), BASE, MAUVE) + " " + t.s(plain(block[2]), MAUVE, bold=True) + " "
            self.add()
            row = self.add(label + t.s("─" * max(0, self.width - vlen(label)), SURFACE2))
            self.heading(row, block[1], str(self.sections), plain(block[2]))
            self.add()
        elif kind == "h" and block[1] == 3:
            self.subsections += 1
            number = f"{self.sections}.{self.subsections}" if self.sections else str(self.subsections)
            row = self.add(
                "  " + t.s(number, PEACH, bold=True) + "  " + t.s(plain(block[2]), PEACH, bold=True, underline=True)
            )
            self.heading(row, 3, number, plain(block[2]))
            self.add()
        elif kind == "h":
            row = self.add("  " + t.s(plain(block[2]), SUB, bold=True))
            self.heading(row, block[1], "", plain(block[2]))
            self.add()
        elif kind == "fn":
            self.footnote(block[1], block[2])
        elif kind == "img":
            self.figure(block[1], block[2])
        elif kind == "quote":
            self.quote(block[1])
        elif kind == "code":
            self.code(block[1], block[2])
        elif kind == "list":
            self.listing(block[1])
        elif kind == "table":
            self.table(*block[1:])
        elif kind == "hr":
            self.add("  " + t.s("· " * ((self.width - 2) // 2), SURFACE2))
            self.add()

    def figure(self, alt: str, url: str) -> None:
        t = self.t
        self.figures += 1
        caption = plain(alt)
        label = f" ◩ Figure {self.figures} "
        inner = self.width - 2
        top = self.frame(url, inner, min(22, max(6, self.screen_rows * 2 // 3)), full=False)
        if top is None:  # no inline images here: a link to the picture instead
            chip = t.link(t.s(label + "· open ↗ ", BASE, TEAL, bold=True), url)
            row = self.add("  " + chip, links=[(2, 2 + vlen(chip), url, label + "· open ↗ ")])
            if caption and self.wide:
                self.note(row, self.margin_text(caption, SUB, italic=True))
            elif caption:
                for line, *_ in wrap(spans(caption), self.width - 4, lambda c, f, u: t.s(c, SUB, italic=True)):
                    self.add("  " + line)
            self.add()
            return
        bottom = t.s("╰─", SURFACE2) + t.s(label, BASE, TEAL, bold=True)
        inline = not self.wide and caption and vlen(caption) <= inner - len(label) - 4
        if inline:
            bottom += t.s(" " + caption + " ", SUB, italic=True)
        self.add(bottom + t.s("─" * max(0, inner + 1 - vlen(bottom)) + "╯", SURFACE2))
        if caption and self.wide:
            self.note(
                top + 1, [t.s(f"Figure {self.figures}", TEAL, bold=True), *self.margin_text(caption, SUB, italic=True)]
            )
        elif caption and not inline:
            for line, *_ in wrap(spans(caption), self.width - 4, lambda c, f, u: t.s(c, SUB, italic=True)):
                self.add("  " + line)
        self.add()

    def quote(self, inner_blocks: list[tuple]) -> None:
        t = self.t
        paragraphs = [b[1] for b in inner_blocks if b[0] == "p"]
        attribution = (
            paragraphs.pop() if len(paragraphs) > 1 and paragraphs[-1].startswith(("—", "-- ", "– ")) else None
        )
        bar = t.s("┃", MAUVE, MANTLE) + t.s("  ", None, MANTLE)
        start = len(self.rows)

        def style(c, f, u):
            styled = t.s(c, LAVENDER, MANTLE, "b" in f, True, "link" in f)
            return t.link(styled, u) if u else styled

        for n, paragraph in enumerate(paragraphs):
            if n:
                self.add("  " + bar + t.s(" " * (self.width - 5), None, MANTLE))
            for line, _, links in wrap(spans(paragraph), self.width - 6, style):
                self.add("  " + bar + _fill(t, line, self.width - 5, MANTLE), links=shifted(links, 5))
        if attribution and self.wide:
            self.note(start, [t.s(attribution, SUB, bold=True)])
        elif attribution:
            self.add("  " + bar + _fill(t, t.s(attribution, SUB, MANTLE, bold=True), self.width - 5, MANTLE))
        self.add()

    def code(self, lang: str, lines: list[str]) -> None:
        t = self.t
        inner = self.width - 4
        digits = len(str(len(lines)))
        text_w = inner - digits - 4
        label = lang or "text"
        self.add(
            "  "
            + t.s("╭─", SURFACE2)
            + t.pill(label, BASE, GREEN)
            + t.s("─" * max(0, inner - vlen(t.pill(label, BASE, GREEN)) - 1) + "╮", SURFACE2)
        )
        side = t.s("│", SURFACE2)
        for number, line in enumerate(lines, 1):
            parts = [line[i : i + text_w] for i in range(0, len(line), text_w)] or [""]
            for k, part in enumerate(parts):
                gutter = f"{number:>{digits}}" if k == 0 else " " * (digits - 1) + "↪"
                body = (
                    t.s(f" {gutter} ", FAINT, MANTLE)
                    + t.s("│ ", SURFACE, MANTLE)
                    + _fill(t, highlight(t, part, lang), text_w, MANTLE)
                )
                self.add("  " + side + body + side)
        self.add("  " + t.s("╰" + "─" * inner + "╯", SURFACE2))
        self.add()

    def listing(self, items: list[tuple]) -> None:
        t = self.t
        counters: dict[int, int] = {}
        for depth, mark, text in items:
            for deeper in [d for d in counters if d > depth]:
                del counters[deeper]
            indent = "  " + "   " * depth
            if mark[0].isdigit():
                counters[depth] = counters.get(depth, int(mark[:-1]) - 1) + 1
                bullet = (
                    t.pill(str(counters[depth]), BASE, PEACH)
                    if t.rounded
                    else t.s(f"{counters[depth]}.", PEACH, bold=True)
                )
            else:
                bullet = t.s(("●", "○", "▪")[depth % 3], (BLUE, MAUVE, TEAL)[depth % 3])
            width = vlen(bullet) + 1
            runs = self.with_references(spans(text))
            for n, (line, flags, links) in enumerate(wrap(runs, self.width - len(indent) - width, self.style)):
                row = self.add(
                    indent + (bullet + " " if n == 0 else " " * width) + line,
                    links=shifted(links, len(indent) + width),
                )
                for flag in sorted(f for f in flags if f.startswith("ref:")):
                    self.note(row, self.reference(int(flag[4:])))
        self.add()

    def table(self, aligns: list[str], head: list[str], rows: list[list[str]]) -> None:
        t = self.t
        columns = len(head)
        data = [[plain(c) for c in head]] + [[plain(c) for c in r[:columns]] + [""] * (columns - len(r)) for r in rows]
        widths = [max(vlen(r[k]) for r in data) for k in range(columns)]
        room = self.width - 2 - (3 * columns + 1)
        while sum(widths) > room and max(widths) > 3:
            widths[widths.index(max(widths))] -= 1

        def cell(text: str, k: int) -> str:
            text, align = trunc(text, widths[k]), aligns[k] if k < len(aligns) else "l"
            gap = widths[k] - vlen(text)
            if align == "r":
                return " " * gap + text
            if align == "c":
                return " " * (gap // 2) + text + " " * (gap - gap // 2)
            return text + " " * gap

        line = lambda left, mid, right: "  " + t.s(left + mid.join("─" * (w + 2) for w in widths) + right, SURFACE2)  # noqa: E731
        bar = t.s("│", SURFACE2)
        self.add(line("╭", "┬", "╮"))
        self.add(
            "  "
            + bar
            + bar.join(t.s(f" {cell(c, k)} ", LAVENDER, SURFACE, bold=True) for k, c in enumerate(data[0]))
            + bar
        )
        self.add(line("├", "┼", "┤"))
        for n, r in enumerate(data[1:]):
            bg = MANTLE if n % 2 else None
            self.add(
                "  "
                + bar
                + bar.join(t.s(f" {cell(c, k)} ", PEACH if k == 0 else TEXT, bg) for k, c in enumerate(r))
                + bar
            )
        self.add(line("╰", "┴", "╯"))
        self.add()

    def footnote(self, name: str, text: str) -> None:
        """A footnote definition (Readeck writes annotation notes as ``[^n]: note``)."""
        t = self.t
        if not self.footnotes:
            self.add("  " + t.s("─" * 12, SURFACE2))
        self.footnotes += 1
        marker = name.translate(SUPERSCRIPT) if name.isdigit() else f"[{name}]"
        self.anchors[f"fn:{name}"] = len(self.rows)
        style = lambda c, f, u: self.style(c, f, u) if u else t.s(c, SUB, italic=True)  # noqa: E731
        width = vlen(marker) + 1
        for n, (line, _, links) in enumerate(wrap(spans(text), self.width - 2 - width, style)):
            lead = t.s(marker, MAUVE, bold=True) + " " if n == 0 else " " * width
            self.add("  " + lead + line, links=shifted(links, 2 + width))
        self.add()

    def footer(self, bookmark: dict[str, Any], minutes: int | None) -> None:
        t = self.t
        site = bookmark.get("site_name") or bookmark.get("site") or ""
        left = t.pill("READECK", BASE, MAUVE) + (" " + t.pill(site, TEXT, SURFACE) if site else "")
        right = t.pill(f"◷ {minutes} min", TEXT, SURFACE) if minutes else ""
        url = bookmark.get("url")
        if url:
            shown = trunc(re.sub(r"^[a-z]+://(www\.)?", "", url), max(10, self.total - vlen(left) - vlen(right) - 8))
            right += " " + t.link(t.pill("↗ " + shown, BASE, BLUE), url)
        gap = max(1, self.total - vlen(left) - vlen(right))
        end = vlen(left) + gap + vlen(right) - 1
        links = [(end - vlen(shown) - 2, end, url, "↗ " + shown)]
        self.add(left + " " * gap + right, full=True, links=links if url else [])

    # ---------------------------------------------------------------- output
    def lines(self) -> list[str]:
        """Compose rows (without their prefixes): main column, then the margin notes flowed down
        without overlapping."""
        margin: dict[int, str] = {}
        free = 0
        for at, lines in self.notes:
            at = max(at, free)
            for line in lines:
                while at < len(self.rows) and self.rows[at].full:
                    at += 1
                while at >= len(self.rows):
                    self.add()
                margin[at] = line
                at += 1
            free = at + 1
        out = []
        offset = " " * self.left
        for n, row in enumerate(self.rows):
            if row.full or n not in margin:
                out.append((offset + row.main).rstrip(" ") if row.main else "")
            else:
                out.append(offset + pad(row.main, self.width) + " " * GAP + margin[n])
        return out


def shifted(links: list[tuple], by: int) -> list[tuple]:
    return [(start + by, end + by, url, text) for start, end, url, text in links]


READECK_ID_PREFIX_RX = re.compile(r"^[A-Za-z]{2}\.[A-Za-z]{4}\.")


def slug(title: str) -> str:
    """GitHub-style heading anchor: lower case, punctuation dropped, spaces as dashes."""
    return re.sub(r"[^\w\- ]", "", title.lower()).strip().replace(" ", "-")


def loose(text: str) -> str:
    return re.sub(r"[\W_]", "", text.lower())


def fragment(url: str, page_url: str | None) -> str | None:
    """The anchor a link jumps to inside the article: ``#name`` links, and links to the article's
    own page with a fragment (Readeck keeps the former, but some sites write the latter)."""
    if url.startswith("#"):
        return url[1:]
    base, _, anchor = url.partition("#")
    same = lambda u: re.sub(r"^[a-z]+://(www\.)?", "", u.lower()).rstrip("/")  # noqa: E731
    return anchor if anchor and page_url and same(base) == same(page_url.partition("#")[0]) else None


def resolve(doc: Document, anchor: str, from_row: int = -1) -> int | None:
    """The row a same-page link lands on. Readeck drops element IDs from article.md and prefixes
    same-page links with a random ``Xx.Xxxx.``, so this matches the rest against heading slugs and
    footnote names; a site footnote (``#fn1``) lands on the link back to it (``#fnref1``) and back."""
    name = READECK_ID_PREFIX_RX.sub("", anchor)
    for candidate in (name, name.lower(), slug(name), loose(name)):
        if candidate in doc.anchors:
            return doc.anchors[candidate]
    pair = lambda f: re.sub(r"ref|note|back", "", loose(f))  # noqa: E731
    for row, _, _, url, _ in doc.links:
        other = fragment(url, doc.page_url)
        if other is not None and row != from_row:
            other = READECK_ID_PREFIX_RX.sub("", other)
            if loose(other) != loose(name) and pair(other) == pair(name) and pair(name):
                return row
    return None


def _fill(t: Term, styled: str, width: int, bg: str) -> str:
    """Pad a styled string to ``width`` keeping ``bg`` behind the padding and between its runs."""
    if not t.color:
        return pad(styled, width)
    layer = f"\x1b[{t.rgb(bg, 48)}m"
    return layer + pad(styled.replace("\x1b[0m", "\x1b[0m" + layer), width) + "\x1b[0m"


def _resized(t: Term, width: int) -> Term:
    """A copy of ``t`` laid out ``width`` cells wide."""
    copy = Term(width=width, color=t.color)
    copy.truecolor, copy.rounded, copy.sizing = t.truecolor, t.rounded, t.sizing
    return copy


def place(escape: str, rows: int, column: int) -> str:
    """Prefix for the first of ``rows`` lines: scroll room for them, then draw the image at ``column``
    of that line without moving the cursor (the rows' own text is printed afterwards)."""
    down = rows - 1
    reserve = "\n" * down + f"\x1b[{down}A" if down > 0 else ""
    return reserve + "\r" + (f"\x1b[{column}C" if column else "") + escape + "\r"


# ---------------------------------------------------------------------- entry
def parse(markdown: str) -> tuple[dict[str, Any], str | None, str | None, list[tuple]]:
    """Front matter, cover image URL, note, and the body blocks of a Readeck ``article.md``."""
    meta, body = front_matter(markdown)
    parsed = blocks(body)
    cover = note = None
    if parsed and parsed[0][0] == "h" and parsed[0][1] == 1:
        meta.setdefault("title", plain(parsed.pop(0)[2]))
    if parsed and parsed[0][0] == "img":
        cover = parsed.pop(0)[2]
    if len(parsed) >= 2 and parsed[0][:2] == ("h", 4) and parsed[0][2] == "Note" and parsed[1][0] == "p":
        note = parsed[1][1]
        del parsed[:2]
    return meta, cover, note, parsed


def render(
    markdown: str,
    bookmark: dict[str, Any],
    t: Term,
    loader: images.Loader | None = None,
    cell: tuple[float, float] = (10.0, 20.0),
    screen_rows: int = 40,
    mode: str | None = images.DIRECT,
) -> Document:
    meta, cover, note, body = parse(markdown)
    bookmark = {**{"title": meta.get("title"), "site_name": meta.get("website"), "url": meta.get("source")}, **bookmark}
    r = Renderer(t, loader, cell, screen_rows, mode, bookmark.get("url"))
    r.title = bookmark.get("title") or ""
    contents = [plain(b[2]) for b in body if b[0] == "h" and b[1] <= 2]
    r.header(bookmark, cover, contents, note)
    for block in body:
        r.block(block)
    r.footer(bookmark, bookmark.get("reading_time"))
    return Document(r, r.lines())


def image_urls(markdown: str) -> list[str]:
    _, cover, _, body = parse(markdown)

    def walk(items):
        for b in items:
            if b[0] == "img":
                yield b[2]
            elif b[0] == "quote":
                yield from walk(b[1])

    return [u for u in [cover, *walk(body)] if u]


def show(
    markdown: str,
    bookmark: dict[str, Any],
    api: Api,
    out: int | None = None,
    keys: int | None = None,
    paging: str = "auto",
) -> None:
    """Show the rendered article on ``out`` (stdout by default), with inline images when the
    terminal can draw them. On a terminal, an article taller than the screen opens in the pager
    (``paging="always"`` even when it fits, ``"never"`` prints it); ``keys`` is the terminal to read
    keys from (stdin when it is a terminal, else ``/dev/tty``)."""
    fd = sys.stdout.fileno() if out is None else out
    tty = os.isatty(fd)
    size = os.get_terminal_size(fd) if tty else shutil.get_terminal_size((100, 24))
    color = None if out is None else tty
    probe = Term(width=size.columns, color=color)
    sizing = probe.color and tty and text_sizing()
    mode = images.mode(fd) if probe.color else None
    loader = None
    cell = (10.0, 20.0)
    if mode:
        cell = images.cell_size(fd)
        loader = images.Loader(api.media, image_urls(markdown), round((size.columns - 4) * cell[0]))

    def layout(cols: int, rows: int) -> Document:
        t = Term(width=cols, color=color)
        t.sizing = sizing
        return render(markdown, bookmark, t, loader, cell, rows, mode)

    from readeck_cli import pager

    try:
        doc = layout(size.columns, size.lines)
        if tty and paging != "never" and (paging == "always" or len(doc.lines) >= size.lines) and pager.usable():
            if keys is None and os.isatty(0):
                keys = 0
            terminal = keys if keys is not None else os.open("/dev/tty", os.O_RDWR | os.O_NOCTTY)
            try:
                pager.page(layout, doc, terminal, fd)
            finally:
                if terminal != keys:
                    os.close(terminal)
        else:
            sys.stdout.flush()
            pager.write_all(fd, doc.printed())
    finally:
        if loader:
            loader.close()
