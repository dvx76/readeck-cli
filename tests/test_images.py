"""The stdlib image decoders behind inline images, checked against ImageMagick's decoding."""

from __future__ import annotations

from pathlib import Path

import pytest

from readeck_cli import images

DATA = Path(__file__).parent / "data"


@pytest.mark.parametrize("name", ["baseline.jpg", "progressive.jpg"])
def test_decode_jpeg_matches_reference(name):
    data = (DATA / name).read_bytes()
    width, height, pixels = images.decode_jpeg(data)
    reference = (DATA / f"{name}.rgb").read_bytes()
    assert (width, height) == (40, 23) == images.size(data)  # odd height: partial MCU row
    error = sum(abs(a - b) for a, b in zip(pixels, reference, strict=True)) / len(reference)
    assert error < 2, f"mean error {error:.2f}"


def test_decode_jpeg_downscales_for_display():
    width, height, pixels = images.decode_jpeg((DATA / "baseline.jpg").read_bytes(), max_width=10)
    assert (width, height, len(pixels)) == (10, 6, 10 * 6 * 3)  # 1/4 scale, rounded up


def test_decode_interlaced_gif_matches_reference():
    data = (DATA / "interlaced.gif").read_bytes()
    assert images.decode_gif(data) == (40, 23, (DATA / "interlaced.gif.rgba").read_bytes())


@pytest.mark.parametrize(
    ("reply", "expected"),
    [
        (b"\x1b_Gi=31;OK\x1b\\\x1b[6;20;9t\x1b[?62;4c", (True, False, (9, 20))),  # kitty graphics only
        (  # kitty 0.50+ also confirms Unicode placeholders
            b"\x1b_Gi=31;OK\x1b\\\x1b_Gi=32,e=T,U=1;OK\x1b\\\x1b[6;20;9t\x1b[?62;4c",
            (True, True, (9, 20)),
        ),
        (b"\x1b_Gi=31;OK\x1b\\\x1b_Gi=32;EINVAL:bad\x1b\\\x1b[?62;4c", (True, False, None)),  # no feature query
        (b"\x1b[?62;22c", (False, False, None)),  # answers device attributes only: no graphics
    ],
)
def test_query_terminal_reads_the_terminal_reply(reply, expected):
    import os
    import pty
    import subprocess
    import sys

    primary, replica = pty.openpty()
    child = (  # make the pty its controlling terminal (/dev/tty), then ask it
        "import fcntl, os, termios; os.setsid(); fcntl.ioctl(0, termios.TIOCSCTTY, 0)\n"
        "from readeck_cli import images; print(repr(images.query_terminal()))"
    )
    proc = subprocess.Popen([sys.executable, "-c", child], stdin=replica, stdout=subprocess.PIPE, env=os.environ)
    os.close(replica)
    sent = b""
    while b"\x1b[c" not in sent:
        sent += os.read(primary, 1024)
    assert b"a=q" in sent  # asked about kitty graphics
    os.write(primary, reply)
    out, _ = proc.communicate(timeout=10)
    os.close(primary)
    assert out.decode().strip() == repr(expected)


def test_placeholder_cells_encode_row_column_and_image_id():
    # kitty's spec: "\e[38;5;42m\U10EEEE\U0305\U0305\U030E" is row 0, column 0 of image 42 + (2 << 24)
    row = images.placeholder_row(42 + (2 << 24), 1, 2)
    assert row == "\x1b[38;5;42m\U0010eeee\u030d\u0305\u030e\U0010eeee\u030d\u030d\u030e\x1b[0m"
    assert len(images.DIACRITICS) == 297 and images.DIACRITICS[-1] == "\U0001d244"  # the full table


def test_graphics_go_through_tmux_passthrough(monkeypatch):
    monkeypatch.delenv("TMUX", raising=False)
    plain = images.upload("f=100", b"png", 7, cols=3, rows=2)
    assert plain == "\x1b_Ga=T,U=1,c=3,r=2,i=7,f=100,q=2,m=0;cG5n\x1b\\"
    monkeypatch.setenv("TMUX", "/tmp/tmux-1000/default,1,0")
    wrapped = images.upload("f=100", b"png", 7, cols=3, rows=2)
    assert wrapped == "\x1bPtmux;\x1b" + plain.replace("\x1b\\", "\x1b\x1b\\") + "\x1b\\"
