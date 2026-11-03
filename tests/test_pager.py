"""The built-in pager behind ``readeck read`` and ``browse``'s ^R, and same-page link resolution."""

from __future__ import annotations

import fcntl
import os
import pty
import select
import struct
import subprocess
import sys
import termios
import time

from conftest import base_env

from readeck_cli import pretty, reader
from readeck_cli.pager import Pager

ARTICLE = """---
title: Scheduling
source: https://lwn.net/Articles/945422/
---

# Scheduling

See [how it works](#Qx.bdef.how-it-works), the [results](https://lwn.net/Articles/945422/#results),
a site footnote[1](#Qx.bdef.fn1) and a note.[^1]

## Background

Some background text.

## How it works

The mechanism. Back to [Background](#Qx.bdef.background).

### Lag

Lag text.

## Results

Numbers.

1. A footnote body. [↩](#Qx.bdef.fnref1)

[^1]: An annotation note.
"""


def render(cols: int = 100, sizing: bool = False):
    t = pretty.Term(width=cols, color=True)
    t.sizing = sizing
    return reader.render(ARTICLE, {}, t)


def row_of(doc, text: str) -> int:
    return next(n for n, line in enumerate(doc.lines) if text in pretty.ANSI_RX.sub("", line))


def target(doc, link_text: str) -> int | None:
    row, _, _, url, _ = next(link for link in doc.links if link[4] == link_text)
    return reader.resolve(doc, reader.fragment(url, doc.page_url), row)


def test_same_page_links_resolve_to_headings_and_footnotes():
    doc = render()
    assert target(doc, "how it works") == row_of(doc, "How it works ─")  # Readeck's Xx.Xxxx. prefix
    assert target(doc, "results") == row_of(doc, "Results ─")  # absolute link to the article's own page
    assert target(doc, "1") == row_of(doc, "A footnote body")  # #fn1 lands on the ↩ back to it
    assert target(doc, "↩") == row_of(doc, "a site footnote")  # and #fnref1 on the reference
    assert target(doc, "¹") == row_of(doc, "An annotation note")  # Readeck annotation [^1]
    assert reader.fragment("https://lwn.net/Articles/1/#x", doc.page_url) is None  # another page


def test_pager_keys_navigate_sections_links_and_search():
    doc = render()
    app = Pager(doc, 100, 12)
    app.handle("text", "]")
    assert app.top == row_of(doc, "Background ─")
    assert "§1 · " in pretty.ANSI_RX.sub("", app.status(pretty.Term(color=False)))
    app.handle("text", "]]")
    assert app.top == row_of(doc, "2.1  Lag")
    app.handle("text", "[")
    assert app.top == row_of(doc, "How it works ─")
    app.handle("text", "c")
    app.handle("down", "")
    app.handle("enter", "")
    assert (app.contents, app.top) == (None, row_of(doc, "2.1  Lag"))
    app.handle("text", "g/numbers")  # typed in one run: the search takes the rest
    app.handle("enter", "")
    assert app.matches[app.match][0] == row_of(doc, "Numbers.")
    app.handle("text", "g")
    app.handle("tab", "")
    assert app.message.endswith("↪ §2 How it works")
    app.handle("enter", "")
    assert app.top == row_of(doc, "How it works ─")
    app.handle("backspace", "")
    assert app.top == 0


def test_large_title_scrolls_as_one_unit():
    doc = render(sizing=True)
    assert doc.tall == {0}  # the 2× title also covers row 1
    app = Pager(doc, 100, 10)
    app.handle("text", "j")
    assert app.top == 2
    app.handle("text", "k")
    assert app.top == 0
    assert "\x1b[2;1H" in app.render().split("\x1b[1;1H")[0]  # its lower row is cleared first


class Terminal:
    """A command on a pseudo-terminal (stdin and stdout), with its screen output collected."""

    def __init__(self, stub, *args, rows=12, cols=100):
        self.primary, replica = pty.openpty()
        fcntl.ioctl(replica, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))
        env = base_env(stub, {"READECK_IMAGES": "0"})  # never query the developer's terminal
        cmd = [sys.executable, "-m", "readeck_cli", *args]
        self.proc = subprocess.Popen(cmd, stdin=replica, stdout=replica, stderr=subprocess.PIPE, env=env)
        os.close(replica)
        self.screen = b""

    def expect(self, marker: bytes, timeout: float = 10) -> None:
        deadline = time.monotonic() + timeout
        while marker not in self.screen:
            assert time.monotonic() < deadline, f"no {marker!r} in {self.screen[-400:]!r}"
            if select.select([self.primary], [], [], 0.1)[0]:
                try:
                    self.screen += os.read(self.primary, 65536)
                except OSError:  # EIO: the command exited
                    break

    def send(self, keys: bytes) -> None:
        self.screen = b""
        os.write(self.primary, keys)

    def close(self) -> int:
        try:
            while select.select([self.primary], [], [], 0.2)[0] and os.read(self.primary, 65536):
                pass
        except OSError:
            pass
        code = self.proc.wait(timeout=10)
        os.close(self.primary)
        return code


def test_read_pages_on_a_terminal_and_q_quits(stub):
    term = Terminal(stub, "read", "bm1")
    term.expect(b"j/k scroll")  # the status line
    assert b"\x1b[?1049h" in term.screen  # alternate screen
    term.send(b"G")
    term.expect(b"100%")
    term.send(b"q")
    term.expect(b"\x1b[?1049l")
    assert term.close() == 0


def test_read_no_pager_prints_the_article(stub):
    term = Terminal(stub, "read", "bm1", "--no-pager")
    term.expect(b"Building")
    code = term.close()
    assert code == 0 and b"\x1b[?1049h" not in term.screen and b"j/k scroll" not in term.screen


def test_browse_ctrl_r_opens_the_pager_and_q_returns(stub):
    term = Terminal(stub, "browse", rows=30, cols=140)
    term.expect(b"esc quit")
    term.send(b"\x12")
    term.expect(b"j/k scroll")
    term.send(b"q")
    term.expect(b"esc quit")  # back in the picker
    term.send(b"\x1b")
    assert term.close() == 130
