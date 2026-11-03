"""Interactive bookmark browser for ``readeck browse`` (stdlib only, POSIX terminals).

The list and preview are drawn on the terminal behind stdin, so stdout stays free for the
selected bookmark (``readeck browse -o json | jq .url`` works).
"""

from __future__ import annotations

import os
import select
import shutil
import subprocess
import sys
import webbrowser
from typing import Any

from readeck_cli import pretty
from readeck_cli.api import Api, CliError

try:
    import termios
    import tty
except ImportError:  # Windows
    termios = tty = None  # type: ignore[assignment]

KEYS = {
    b"\x1b[A": "up",
    b"\x1bOA": "up",
    b"\x10": "up",  # ctrl-p
    b"\x1b[B": "down",
    b"\x1bOB": "down",
    b"\x0e": "down",  # ctrl-n
    b"\x1b[5~": "pgup",
    b"\x1b[6~": "pgdn",
    b"\x1b[H": "home",
    b"\x1bOH": "home",
    b"\x1b[1~": "home",
    b"\x1b[F": "end",
    b"\x1bOF": "end",
    b"\x1b[4~": "end",
    b"\r": "enter",
    b"\n": "enter",
    b"\x7f": "backspace",
    b"\x08": "backspace",
    b"\x15": "clear",  # ctrl-u
    b"\x12": "read",  # ctrl-r
    b"\x0f": "open",  # ctrl-o
    b"\x1b": "quit",
    b"\x03": "quit",  # ctrl-c
    b"\x04": "quit",  # ctrl-d
}
SEQUENCES = sorted(KEYS, key=len, reverse=True)
HINTS = "↑↓ move · type to filter · enter show · ^R read · ^O open · esc quit"


def parse_keys(data: bytes) -> list[tuple[str, str]]:
    """Split raw terminal input into (key, text) events; printable runs become ("text", ...)."""
    events: list[tuple[str, str]] = []
    i = 0
    while i < len(data):
        seq = next((s for s in SEQUENCES if data.startswith(s, i) and (s != b"\x1b" or i + 1 == len(data))), None)
        if seq:
            events.append((KEYS[seq], ""))
            i += len(seq)
        elif data[i] == 0x1B:  # unknown escape sequence (F-keys, mouse...): skip it
            j = i + 2
            while j < len(data) and not 0x40 <= data[j] <= 0x7E:
                j += 1
            i = j + 1
        elif data[i] < 0x20:
            i += 1
        else:
            j = i
            while j < len(data) and data[j] >= 0x20 and data[j] != 0x7F:
                j += 1
            events.append(("text", data[i:j].decode("utf-8", "ignore")))
            i = j
    return events


class Browser:
    def __init__(self, bookmarks: list[dict[str, Any]], total: int, filters: str, api: Api):
        self.items = bookmarks
        self.total = total
        self.filters = filters
        self.api = api
        self.haystacks = [
            " ".join(
                str(x)
                for x in (
                    b.get("title"),
                    b.get("site_name"),
                    b.get("site"),
                    *(b.get("labels") or []),
                    *(b.get("authors") or []),
                )
                if x
            ).lower()
            for b in bookmarks
        ]
        self.query = ""
        self.matches = list(range(len(bookmarks)))
        self.cursor = 0
        self.top = 0
        self.message = ""

    # ------------------------------------------------------------------ state
    def refilter(self) -> None:
        terms = self.query.lower().split()
        self.matches = [i for i, hay in enumerate(self.haystacks) if all(t in hay for t in terms)]
        self.cursor = self.top = 0

    def current(self) -> dict[str, Any] | None:
        return self.items[self.matches[self.cursor]] if self.matches else None

    def move(self, delta: int) -> None:
        if self.matches:
            self.cursor = max(0, min(len(self.matches) - 1, self.cursor + delta))

    # ----------------------------------------------------------------- render
    def render(self, cols: int, rows: int) -> str:
        t = pretty.Term(width=cols, color=True)
        body = max(1, rows - 3)
        side = cols >= 120
        list_w = cols * 11 // 20 if side else cols
        list_h = body if side else max(1, min(len(self.matches) or 1, body // 2))
        preview_w = cols - list_w - 1 if side else cols
        preview_h = body if side else body - list_h - 1

        if self.cursor < self.top:
            self.top = self.cursor
        elif self.cursor >= self.top + list_h:
            self.top = self.cursor - list_h + 1

        count = f"{len(self.matches)}/{len(self.items)}"
        if len(self.items) < self.total:
            count += f" of {self.total}"
        header = " " + t.s("readeck", pretty.MAUVE, bold=True) + "  " + t.s(count, pretty.SUB)
        if self.filters:
            header += t.s("  ·  ", pretty.MUTED) + t.s(self.filters, pretty.BLUE)
        prompt = (
            " " + t.s("❯ ", pretty.GREEN, bold=True) + t.s(self.query, pretty.TEXT, bold=True) + t.s("▏", pretty.BLUE)
        )

        title_w = max(10, list_w - 36)
        list_lines = []
        for pos in range(self.top, self.top + list_h):
            if pos >= len(self.matches):
                list_lines.append("" if self.matches or pos else "  " + t.s("No matches.", pretty.SUB))
                continue
            row = pretty.bookmark_row(t, self.items[self.matches[pos]], title_w)
            if pos == self.cursor:
                bg = f"\x1b[{t.rgb(pretty.SURFACE, 48)}m"
                row = bg + t.s("▌", pretty.BLUE) + bg + " " + row.replace("\x1b[0m", "\x1b[0m" + bg)
                row = pretty.pad(pretty.clip(row, list_w), list_w) + "\x1b[0m"
            else:
                row = "  " + row
            list_lines.append(row)

        item = self.current()
        preview = pretty.bookmark_lines(item, pretty.Term(width=preview_w - 1, color=True)) if item else []
        preview = preview[:preview_h]

        lines = [header, prompt]
        if side:
            for i in range(body):
                left = pretty.pad(pretty.clip(list_lines[i] if i < len(list_lines) else "", list_w), list_w)
                right = preview[i] if i < len(preview) else ""
                lines.append(left + t.s("│", pretty.FAINT) + " " + right)
        else:
            lines += list_lines
            lines.append(t.s("─" * cols, pretty.FAINT))
            lines += preview + [""] * (preview_h - len(preview))
        footer = self.message or HINTS
        lines.append(" " + t.s(footer, pretty.YELLOW if self.message else pretty.MUTED))
        return "\x1b[H" + "\r\n".join(pretty.clip(line, cols) + "\x1b[0m\x1b[K" for line in lines[:rows])

    # ------------------------------------------------------------------- keys
    def handle(self, key: str, text: str, page: int) -> str | None:
        """Apply one key; returns "select" or "quit" when browsing ends."""
        self.message = ""
        if key == "text":
            self.query += text
            self.refilter()
        elif key == "backspace":
            self.query = self.query[:-1]
            self.refilter()
        elif key == "clear":
            self.query = ""
            self.refilter()
        elif key in ("up", "down", "pgup", "pgdn", "home", "end"):
            delta = {"up": -1, "down": 1, "pgup": -page, "pgdn": page, "home": -(10**9), "end": 10**9}[key]
            self.move(delta)
        elif key == "enter" and self.current():
            return "select"
        elif key == "quit":
            return "quit"
        return key if key in ("read", "open") and self.current() else None


def ensure_terminal() -> None:
    if termios is None:
        raise CliError("browse needs a POSIX terminal (not supported on Windows)", 2)
    if not sys.stdin.isatty():
        raise CliError("browse needs an interactive terminal; use `readeck search` instead", 2)


def browse(bookmarks: list[dict[str, Any]], total: int, filters: str, api: Api) -> dict[str, Any] | None:
    """Run the browser on the terminal behind stdin; returns the selected bookmark or None."""
    ensure_terminal()
    fd = sys.stdin.fileno()
    out = os.open(os.ttyname(fd), os.O_WRONLY | os.O_NOCTTY)
    saved = termios.tcgetattr(fd)
    app = Browser(bookmarks, total, filters, api)

    def write(data: str) -> None:
        os.write(out, data.encode())

    def enter() -> None:
        tty.setraw(fd, termios.TCSANOW)  # TCSANOW keeps keys typed before the UI appeared
        write("\x1b[?1049h\x1b[?25l")

    def leave() -> None:
        write("\x1b[?25h\x1b[?1049l")
        termios.tcsetattr(fd, termios.TCSADRAIN, saved)

    try:
        enter()
        size = None
        while True:
            current = os.get_terminal_size(out)
            if current != size:
                size = current
                write("\x1b[2J" + app.render(size.columns, size.lines))
            if not select.select([fd], [], [], 0.25)[0]:
                continue
            for key, text in parse_keys(os.read(fd, 4096)):
                action = app.handle(key, text, page=max(1, size.lines - 4))
                if action == "select":
                    return app.current()
                if action == "quit":
                    return None
                if action in ("read", "open"):
                    leave()  # the pager, or a console browser, takes over the terminal meanwhile
                    try:
                        if action == "read":
                            path = f"/bookmarks/{app.current()['id']}/article.md"
                            _, body = api.request("GET", path, accept="text/markdown")
                            pager = os.environ.get("PAGER") or ("less -R" if shutil.which("less") else "more")
                            subprocess.run(pager, shell=True, input=body, stdout=out, check=False)
                        else:
                            url = app.current().get("url", "")
                            app.message = f"opened {url}" if url and webbrowser.open(url) else "no browser available"
                    except CliError as exc:
                        app.message = str(exc)
                    enter()
                    write("\x1b[2J")
            write(app.render(size.columns, size.lines))
    finally:
        leave()
        os.close(out)
