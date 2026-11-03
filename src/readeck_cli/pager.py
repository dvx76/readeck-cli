"""Built-in pager for the rendered ``read`` view (stdlib only, POSIX terminals).

``less`` drops kitty graphics escapes, so the article is paged here instead, in the alternate
screen: scrolling, search, section jumps, a contents panel and link following. Images drawn with
Unicode placeholders are text, so they scroll and clip with it; elsewhere each visible part of an
image is placed again on every redraw. Nothing is written back to Readeck (the CLI is read-only).
"""

from __future__ import annotations

import os
import re
import select
import webbrowser
from collections.abc import Callable
from typing import Any

from readeck_cli import images, pretty
from readeck_cli.pretty import ANSI_RX, vlen

try:
    import termios
    import tty
except ImportError:  # Windows
    termios = tty = None  # type: ignore[assignment]

HINTS = "j/k scroll · / search · ]/[ section · c contents · tab links · q quit"


def usable() -> bool:
    return termios is not None


def write_all(fd: int, data: str) -> None:
    view = memoryview(data.encode())
    while view:
        view = view[os.write(fd, view) :]


class Pager:
    """Pager state: the document, the scroll position, and the search, link and panel modes."""

    def __init__(self, doc: Any, cols: int, rows: int):
        self.message = ""
        self.query = ""
        self.typing: str | None = None  # the search being typed
        self.matches: list[tuple[int, int, int]] = []  # (row, start column, end column)
        self.match = -1
        self.link = -1  # selected link (index into self.links)
        self.contents: int | None = None  # cursor in the open contents panel
        self.history: list[int] = []
        self.opening: str | None = None  # external link to open
        self.top = 0
        self.load(doc, cols, rows)

    def load(self, doc: Any, cols: int, rows: int) -> None:
        self.doc, self.cols, self.rows = doc, cols, rows
        self.plain = [ANSI_RX.sub("", line) for line in doc.lines]
        self.sections = [h for h in doc.headings if h[1] <= 3]
        self.links: list[list[tuple]] = []  # a link wrapped over several rows is one link
        for n, link in enumerate(doc.links):
            previous = doc.links[n - 1] if n else None
            if previous and previous[3] == link[3] and link[0] == previous[0] + 1 and self.links[-1][-1] is previous:
                self.links[-1].append(link)
            else:
                self.links.append([link])
        self.link = -1
        if self.query:
            self.find(self.query)
        self.scroll_to(self.top)

    @property
    def body(self) -> int:
        return max(1, self.rows - 1)

    @property
    def last(self) -> int:
        return max(0, len(self.doc.lines) - self.body)

    # ---------------------------------------------------------------- moving
    def scroll_to(self, top: int, direction: int = 1) -> None:
        """Move the first shown row, never to the lower half of a 2× title (it scrolls as one)."""
        top = max(0, min(self.last, top))
        if top - 1 in self.doc.tall:
            top = top + 1 if direction > 0 and top < self.last else top - 1
        self.top = top

    def scroll(self, delta: int) -> None:
        self.scroll_to(self.top + delta, delta)

    def show_row(self, row: int) -> None:
        """Scroll so ``row`` is in view, a third of the way down when it is not already."""
        if not self.top <= row < self.top + self.body:
            self.scroll_to(row - self.body // 3)

    def jump(self, row: int) -> None:
        """Put ``row`` (a heading, a footnote) at the top, remembering where we were."""
        self.history.append(self.top)
        self.scroll_to(row)

    def section(self) -> tuple[int, int, str, str] | None:
        """The section the top of the screen is in."""
        current = None
        for heading in self.sections:
            if heading[0] > self.top:
                break
            current = heading
        return current

    # ---------------------------------------------------------------- search
    def find(self, query: str) -> None:
        self.query, needle = query, query.lower()
        self.matches = []
        for row, line in enumerate(self.plain):
            low = line.lower()
            start = low.find(needle) if needle else -1
            while start >= 0:
                col = vlen(line[:start])
                self.matches.append((row, col, col + vlen(line[start : start + len(needle)])))
                start = low.find(needle, start + len(needle))
        self.match = -1

    def next_match(self, step: int) -> None:
        if not self.matches:
            self.message = f"not found: {self.query}" if self.query else "no search yet: press /"
            return
        if self.match < 0:
            after = [n for n, m in enumerate(self.matches) if m[0] >= self.top]
            self.match = after[0] if after and step > 0 else (after[0] - 1 if after else len(self.matches) - 1)
        else:
            self.match = (self.match + step) % len(self.matches)
        self.match %= len(self.matches)
        self.show_row(self.matches[self.match][0])
        self.message = f"/{self.query}  {self.match + 1}/{len(self.matches)}"

    # ----------------------------------------------------------------- links
    def cycle(self, step: int) -> None:
        links = self.links
        if not links:
            self.message = "no links in this article"
            return
        in_view = lambda n: self.top <= links[n][0][0] < self.top + self.body  # noqa: E731
        if self.link >= 0 and in_view(self.link):
            self.link = (self.link + step) % len(links)
        else:
            shown = [n for n in range(len(links)) if in_view(n)]
            after = [n for n in range(len(links)) if links[n][0][0] >= self.top]
            self.link = (shown[0] if step > 0 else shown[-1]) if shown else (after[0] if after else 0)
        self.show_row(links[self.link][0][0])
        self.message = " ".join(part[4].strip() for part in links[self.link]) + "  →  " + self.target_name()

    def target_name(self) -> str:
        """Where the selected link goes: a section or footnote of the article, or its URL."""
        from readeck_cli import reader

        row, _, _, url, _ = self.links[self.link][0]
        anchor = reader.fragment(url, self.doc.page_url)
        if anchor is None:
            return url
        target = reader.resolve(self.doc, anchor, row)
        heading = next((h for h in self.doc.headings if h[0] == target), None)
        if heading:
            return f"↪ §{heading[2]} {heading[3]}" if heading[2] else f"↪ {heading[3]}"
        return "↪ " + ("this article, line " + str(target + 1) if target is not None else "#" + anchor + " (not found)")

    def follow(self) -> str | None:
        """Jump to the selected same-page link; returns an external URL to open."""
        from readeck_cli import reader

        row, _, _, url, _ = self.links[self.link][0]
        anchor = reader.fragment(url, self.doc.page_url)
        if anchor is None:
            return url
        target = reader.resolve(self.doc, anchor, row)
        if target is None:
            self.message = f"#{anchor}: not in this article"
            return None
        self.history.append(self.top)
        heading = any(h[0] == target for h in self.doc.headings)
        self.scroll_to(target if heading else target - self.body // 3)
        self.link = next((n for n, link in enumerate(self.links) if link[0][0] == target), -1)
        self.message = "jumped to the link's target · backspace: back"
        return None

    # ------------------------------------------------------------------ keys
    def handle(self, key: str, text: str) -> str | None:
        """Apply one key event (a typed run is one character at a time, as it may switch modes);
        returns "quit", or "open" with ``self.opening`` set to a URL."""
        for char in text if key == "text" else [""]:
            if self.typing is not None:
                self.type_search(key, char)
            elif self.contents is not None:
                self.pick(key, char)
            else:
                self.message = ""
                if self.key(char or key) == "quit":
                    return "quit"
                if self.opening:
                    return "open"
        return None

    def key(self, key: str) -> str | None:
        page = self.body - 1
        moves = {
            "j": 1, "down": 1, "k": -1, "up": -1, " ": page, "f": page, "pgdn": page, "b": -page, "pgup": -page,
            "d": page // 2, "u": -page // 2, "g": -(10**9), "home": -(10**9), "G": 10**9, "end": 10**9,
        }  # fmt: skip
        if key in moves:
            self.scroll(moves[key])
        elif key in ("q", "Q", "quit"):
            return "quit"
        elif key == "escape":
            if self.link < 0 and self.match < 0:
                return "quit"
            self.link = self.match = -1
        elif key == "/":
            self.typing = ""
        elif key in ("n", "N"):
            self.next_match(1 if key == "n" else -1)
        elif key in ("]", "["):
            rows = [h[0] for h in self.sections]
            if key == "]":
                target = next((r for r in rows if r > self.top), None)
            else:
                target = next((r for r in reversed(rows) if r < self.top), None)
            if target is None:
                self.scroll(10**9 if key == "]" else -(10**9))
            else:
                self.scroll_to(target, 1 if key == "]" else -1)
        elif key in ("c", "t"):
            if not self.sections:
                self.message = "this article has no sections"
            else:
                current = self.section()
                self.contents = self.sections.index(current) if current else 0
        elif key in ("tab", "backtab"):
            self.cycle(1 if key == "tab" else -1)
        elif key == "enter":
            if self.link >= 0:
                self.opening = self.follow()
            else:
                self.scroll(1)
        elif key == "backspace" and self.history:
            self.scroll_to(self.history.pop())
        return None

    def type_search(self, key: str, text: str) -> None:
        if key == "text":
            self.typing += text
        elif key == "backspace":
            self.typing = self.typing[:-1]
        elif key == "clear":
            self.typing = ""
        elif key == "enter":
            query, self.typing = self.typing, None
            if query:
                self.find(query)
            self.next_match(1)
        elif key in ("escape", "quit"):
            self.typing = None
        return None

    def pick(self, key: str, text: str) -> None:
        if key in ("down", "tab") or text in ("j",):
            self.contents = min(len(self.sections) - 1, self.contents + 1)
        elif key in ("up", "backtab") or text in ("k",):
            self.contents = max(0, self.contents - 1)
        elif key in ("home",) or text == "g":
            self.contents = 0
        elif key in ("end",) or text == "G":
            self.contents = len(self.sections) - 1
        elif key == "enter":
            self.jump(self.sections[self.contents][0])
            self.contents = None
        elif key in ("escape", "quit") or text in ("q", "c", "t"):
            self.contents = None
        return None

    # ---------------------------------------------------------------- render
    def render(self) -> str:
        doc, t = self.doc, pretty.Term(width=self.cols, color=True)
        out = []
        if doc.mode == images.DIRECT:
            out += [images.delete(p[4]) for p in doc.pictures]
        for i in range(self.body):
            row = self.top + i
            if row - 1 in doc.tall and i > 0:
                continue  # the lower half of the 2× title drawn on the row above
            line = doc.lines[row] if row < len(doc.lines) else ""
            if row in doc.tall:
                if i == self.body - 1:
                    line = ""  # no room for both rows
                else:
                    out.append(f"\x1b[{i + 2};1H\x1b[2K")
            out.append(f"\x1b[{i + 1};1H\x1b[2K{line}\x1b[0m")
            for n, (r, start, end) in enumerate(self.matches):
                if r == row:
                    color = pretty.PEACH if n == self.match else pretty.YELLOW
                    out.append(self.overlay(i, self.plain[row], start, end, t, color))
            for r, start, _, url, label in self.links[self.link] if self.link >= 0 else []:
                if r == row:
                    cell = f"\x1b[{i + 1};{start + 1}H"
                    out.append(cell + t.link(t.s(label, pretty.BASE, "#89dceb", bold=True), url))
        if doc.mode == images.DIRECT and self.contents is None:
            out += self.place_pictures()
        if self.contents is not None:
            out.append(self.panel(t))
        out.append(f"\x1b[{self.rows};1H\x1b[2K" + self.status(t) + "\x1b[0m")
        return "".join(out)

    def overlay(self, i: int, line: str, start: int, end: int, t, bg: str) -> str:
        """Redraw columns ``start``..``end`` of screen row ``i`` (plain ``line``) highlighted."""
        text, used = "", 0
        for char in line:
            width = vlen(char)
            if start <= used < end:
                text += char
            elif used >= end:
                break
            used += width
        return f"\x1b[{i + 1};{start + 1}H" + t.s(text, pretty.BASE, bg, bold=True)

    def place_pictures(self) -> list[str]:
        """Direct-mode images: place the visible rows of each one, cropped to the screen."""
        out = []
        for row, column, cols, rows, image, keys, payload in self.doc.pictures:
            first, last = max(row, self.top), min(row + rows, self.top + self.body)
            if first >= last:
                continue
            height = _pixel_height(keys, payload)
            y, h = (first - row) * height // rows, (last - first) * height // rows
            out.append(f"\x1b[{first - self.top + 1};{column + 1}H" + images.placement(image, cols, last - first, y, h))
        return out

    def panel(self, t) -> str:
        """The contents panel: a box over the article listing its sections."""
        width = min(64, self.cols - 4)
        height = min(len(self.sections), max(3, self.body - 4))
        left = (self.cols - width) // 2
        first = max(0, min(self.contents - height // 2, len(self.sections) - height))
        bg = pretty.BASE
        border = lambda s: t.s(s, pretty.MAUVE, bg)  # noqa: E731
        title = " Contents "
        out = [border("╭─") + t.s(title, pretty.MAUVE, bg, bold=True) + border("─" * (width - 3 - len(title)) + "╮")]
        for n in range(first, first + height):
            _, level, number, name = self.sections[n]
            label = f"§{number}" if level <= 2 and number else number
            indent = "" if level <= 2 else "   "
            room = width - 6 - len(indent) - len(label)
            entry = indent + label + " " + pretty.trunc(name, room)
            selected = n == self.contents
            fill = pretty.SURFACE if selected else bg
            marker = t.s("▌", pretty.BLUE, fill) if selected else t.s(" ", None, fill)
            colour = pretty.MAUVE if level <= 2 else pretty.PEACH
            name = entry[len(indent + label) + 1 :]
            body = t.s(indent + label + " ", colour, fill, bold=True) + t.s(name, pretty.TEXT, fill)
            out.append(border("│") + marker + body + t.s(" " * (width - 3 - vlen(entry)), None, fill) + border("│"))
        hint = " ↑↓ move · enter jump · esc close "
        out.append(border("╰" + "─" * (width - 3 - len(hint))) + t.s(hint, pretty.MUTED, bg) + border("─╯"))
        return "".join(f"\x1b[{2 + k};{left + 1}H{line}" for k, line in enumerate(out))

    def status(self, t) -> str:
        if self.typing is not None:
            typed = t.s(self.typing, pretty.TEXT, bold=True)
            return " " + t.s("/", pretty.GREEN, bold=True) + typed + t.s("▏", pretty.BLUE)
        current = self.section()
        where = f"§{current[2]}" if current and current[2] else "§–"
        percent = 100 if self.last == 0 else round(self.top * 100 / self.last)
        left = t.pill(f"{where} · {percent}%", pretty.BASE, pretty.MAUVE)
        name = current[3] if current else (self.doc.title or "")
        left += " " + t.s(pretty.trunc(name, max(10, self.cols // 3)), pretty.SUB, bold=True)
        right = self.message or HINTS
        room = self.cols - vlen(left) - 3
        return left + "  " + t.s(pretty.trunc(right, max(1, room)), pretty.YELLOW if self.message else pretty.MUTED)


def _pixel_height(keys: str, payload: bytes) -> int:
    """Height in pixels of an encoded image: ``v=`` for raw pixels, else the PNG header."""
    found = re.search(r"\bv=(\d+)", keys)
    return int(found[1]) if found else images.size(payload)[1]


def page(layout: Callable[[int, int], Any], doc: Any, keys: int, out: int) -> None:
    """Page a rendered document in the alternate screen of the terminal ``out``, reading keys from
    ``keys``; ``layout(cols, rows)`` renders it again when the terminal is resized."""
    from readeck_cli.tui import parse_keys

    saved = termios.tcgetattr(keys)
    size = os.get_terminal_size(out)
    app = Pager(doc, size.columns, size.lines)

    def show_images() -> None:
        if app.doc.mode == images.DIRECT:
            write_all(out, "".join(images.upload(k, p, image) for _, _, _, _, image, k, p in app.doc.pictures))
        else:
            write_all(out, "".join(app.doc.uploads))

    def start() -> None:
        tty.setraw(keys, termios.TCSANOW)
        write_all(out, "\x1b[?1049h\x1b[?25l\x1b[2J")

    try:
        start()
        show_images()
        while True:
            current = os.get_terminal_size(out)
            if (current.columns, current.lines) != (app.cols, app.rows):
                section = app.section()  # keep the same heading (and lines below it) at the top
                index, offset = (app.sections.index(section), app.top - section[0]) if section else (None, 0)
                share = app.top / max(1, len(app.doc.lines))
                app.load(layout(current.columns, current.lines), current.columns, current.lines)
                if index is not None and index < len(app.sections):
                    app.scroll_to(app.sections[index][0] + offset)
                else:
                    app.scroll_to(round(share * len(app.doc.lines)))
                write_all(out, "\x1b[2J")
                show_images()
            write_all(out, app.render())
            if not select.select([keys], [], [], 0.25)[0]:
                continue
            for key, text in parse_keys(os.read(keys, 4096)):
                action = app.handle(key, text)
                if action == "quit":
                    return
                if action == "open":
                    url, app.opening = app.opening, None
                    termios.tcsetattr(keys, termios.TCSANOW, saved)
                    opened = webbrowser.open(url)
                    tty.setraw(keys, termios.TCSANOW)
                    app.message = f"opened {url}" if opened else f"no browser available for {url}"
                    write_all(out, "\x1b[2J")
    finally:
        write_all(out, "".join(images.delete(p[4], free=True) for p in app.doc.pictures))
        write_all(out, "\x1b[2J\x1b[?25h\x1b[?1049l")
        termios.tcsetattr(keys, termios.TCSADRAIN, saved)
