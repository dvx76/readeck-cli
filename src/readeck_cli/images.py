"""Inline images for the ``read`` view (stdlib only).

Kitty's graphics protocol (kitty, Ghostty, WezTerm, Rex) takes PNG files or raw pixels. Readeck stores
article images as PNG, JPEG or (animated) GIF, so PNG goes to the terminal unchanged and JPEG/GIF
are decoded here, downscaled to the size they are shown at, and sent as raw RGB(A).

Where the terminal supports them (kitty, Ghostty, Rex), images are drawn with Unicode placeholders:
the image is uploaded once and its cells are ordinary text, so it scrolls and clips with the text
and also works inside tmux. Other kitty-graphics terminals (WezTerm, Konsole) get images drawn at
the cursor.
"""

from __future__ import annotations

import base64
import functools
import math
import os
import random
import re
import struct
import subprocess
import time
import zlib
from collections.abc import Callable
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor
from concurrent.futures.process import BrokenProcessPool
from itertools import compress
from operator import add

from readeck_cli.pretty import in_multiplexer, kitty_like

PNG_MAGIC = b"\x89PNG\r\n\x1a\n"


class ImageError(ValueError):
    """The image is in a format or variant this module cannot decode."""


# ------------------------------------------------------------------ detection
PLACEHOLDERS = "placeholders"  # images are text cells (kitty Unicode placeholders)
DIRECT = "direct"  # images are drawn at the cursor


def mode(fd: int) -> str | None:
    """How the terminal on ``fd`` shows images: ``PLACEHOLDERS``, ``DIRECT`` or None (no images).
    ``READECK_IMAGES=0/1`` turns them off or on; known terminals are recognized from their
    environment and any other terminal is asked directly."""
    setting = os.environ.get("READECK_IMAGES", "").lower()
    if setting in ("0", "no", "off", "false"):
        return None
    forced = setting in ("1", "yes", "on", "true")
    if not forced and not os.isatty(fd):
        return None
    if os.environ.get("TMUX"):  # only placeholders survive tmux, uploaded through its passthrough
        return PLACEHOLDERS if forced or tmux_placeholders() else None
    if not forced and in_multiplexer():
        return None
    if placeholders_known():
        return PLACEHOLDERS
    if forced or kitty_like():
        return DIRECT
    supported, placeholders, _ = query_terminal()
    return (PLACEHOLDERS if placeholders else DIRECT) if supported else None


def placeholders_known() -> bool:
    """kitty (0.28+), Ghostty and Rex (built on libghostty) draw Unicode placeholders; WezTerm does not."""
    return (
        os.environ.get("TERM") in ("xterm-kitty", "xterm-ghostty", "xterm-rex")
        or "KITTY_WINDOW_ID" in os.environ
        or "GHOSTTY_RESOURCES_DIR" in os.environ
        or os.environ.get("TERM_PROGRAM", "").lower() in ("ghostty", "rex")
    )


def tmux_placeholders() -> bool:
    """Inside tmux: the outer terminal draws placeholders and tmux passes graphics through to it
    (``set -g allow-passthrough on``)."""
    try:
        passthrough = subprocess.run(
            ["tmux", "show", "-Apv", "allow-passthrough"], capture_output=True, text=True, timeout=2
        ).stdout.split()
        client = subprocess.run(
            ["tmux", "display", "-p", "#{client_termname} #{client_termtype}"],
            capture_output=True,
            text=True,
            timeout=2,
        ).stdout.lower()
    except OSError, subprocess.SubprocessError:
        return False
    outer = client.startswith(("xterm-kitty", "xterm-ghostty", "xterm-rex")) or any(
        name in client for name in (" kitty", " ghostty", " rex")
    )
    return outer and bool(passthrough) and passthrough[-1] in ("on", "all")


def cell_size(fd: int) -> tuple[float, float]:
    """Pixel size of one terminal cell: from the window size ioctl, else asked of the terminal."""
    try:
        import fcntl
        import termios

        rows, cols, xpx, ypx = struct.unpack("HHHH", fcntl.ioctl(fd, termios.TIOCGWINSZ, b"\0" * 8))
        if rows and cols and xpx and ypx:
            return xpx / cols, ypx / rows
    except ImportError, OSError, ValueError:
        pass
    return query_terminal()[2] or (10.0, 20.0)


@functools.cache
def query_terminal(timeout: float = 0.5) -> tuple[bool, bool, tuple[float, float] | None]:
    """Ask the controlling terminal whether it supports kitty graphics and Unicode placeholders,
    and how big its cells are.

    Sends a kitty graphics query (a 1x1 image that is checked, not stored), a placeholder feature
    query (kitty 0.50+), a cell size request (CSI 16 t) and a primary device attributes request
    (CSI c). Every terminal answers the last one, and in order, so its reply ends the wait without
    a timeout on terminals that ignore the others.
    """
    try:
        import select
        import termios
        import tty

        fd = os.open("/dev/tty", os.O_RDWR | os.O_NOCTTY)
    except ImportError, OSError:
        return False, False, None
    reply = b""
    try:
        saved = termios.tcgetattr(fd)
        try:
            tty.setcbreak(fd, termios.TCSANOW)  # no echo of the replies
            os.write(fd, b"\x1b_Gi=31,s=1,v=1,a=q,t=d,f=24;AAAA\x1b\\\x1b_Ga=q,i=32,e=T,U=1\x1b\\\x1b[16t\x1b[c")
            deadline = time.monotonic() + timeout
            while not _DA1_RX.search(reply):
                left = deadline - time.monotonic()
                if left <= 0 or not select.select([fd], [], [], left)[0]:
                    break
                reply += os.read(fd, 1024)
        finally:
            termios.tcsetattr(fd, termios.TCSANOW, saved)
    except OSError, termios.error:
        pass
    finally:
        os.close(fd)
    cell = _CELL_RX.search(reply)
    return (
        b"\x1b_Gi=31;OK" in reply,
        bool(_PLACEHOLDERS_RX.search(reply)),
        (int(cell[2]), int(cell[1])) if cell and int(cell[1]) and int(cell[2]) else None,
    )


_PLACEHOLDERS_RX = re.compile(rb"\x1b_Gi=32,[^;\x1b]*U=1[^;\x1b]*;OK")
_DA1_RX = re.compile(rb"\x1b\[\?[\d;]*c")
_CELL_RX = re.compile(rb"\x1b\[6;(\d+);(\d+)t")


def size(data: bytes) -> tuple[int, int] | None:
    """Pixel size of a PNG, GIF or JPEG without decoding it."""
    try:
        if data.startswith(PNG_MAGIC):
            return struct.unpack(">II", data[16:24])
        if data[:6] in (b"GIF87a", b"GIF89a"):
            return struct.unpack("<HH", data[6:10])
        if data.startswith(b"\xff\xd8"):
            pos = 2
            while pos + 9 < len(data):
                while data[pos] == 0xFF and data[pos + 1] == 0xFF:
                    pos += 1
                marker = data[pos + 1]
                if marker in (0xC0, 0xC1, 0xC2):
                    h, w = struct.unpack(">HH", data[pos + 5 : pos + 9])
                    return w, h
                pos += 2 + struct.unpack(">H", data[pos + 2 : pos + 4])[0]
    except struct.error:
        pass
    return None


# ----------------------------------------------------------------------- kitty
def encode(data: bytes, max_width: int | None = None) -> tuple[str, bytes]:
    """Kitty graphics format keys and payload for an image: PNG as is, JPEG/GIF decoded to raw pixels."""
    if data.startswith(PNG_MAGIC):
        return "f=100", data
    if data.startswith(b"\xff\xd8"):
        (w, h, pixels), fmt = decode_jpeg(data, max_width), 24
    elif data[:6] in (b"GIF87a", b"GIF89a"):
        (w, h, pixels), fmt = decode_gif(data), 32
    else:
        raise ImageError("unsupported image format")
    return f"f={fmt},s={w},v={h},o=z", zlib.compress(pixels, 1)


def escape(keys: str, payload: bytes, cols: int, rows: int) -> str:
    """Draw an encoded image at the cursor over ``cols``×``rows`` cells, leaving the cursor in place."""
    return _command(f"a=T,{keys},c={cols},r={rows},C=1,q=2", payload)


def upload(keys: str, payload: bytes, image_id: int, cols: int = 0, rows: int = 0) -> str:
    """Transmit an image under ``image_id`` without showing it. With ``cols``×``rows`` it also gets
    the virtual placement that Unicode placeholder cells (``placeholder_row``) draw."""
    action = f"a=T,U=1,c={cols},r={rows}" if cols else "a=t"
    return _command(f"{action},i={image_id},{keys},q=2", payload)


def placement(image_id: int, cols: int, rows: int, y: int, height: int) -> str:
    """Show pixel rows ``y`` to ``y + height`` of an uploaded image at the cursor over ``cols``×``rows``
    cells (the pager's fallback where placeholders are not supported)."""
    return _command(f"a=p,i={image_id},p=1,y={y},h={height},c={cols},r={rows},C=1,q=2")


def delete(image_id: int, free: bool = False) -> str:
    """Remove an image's placements from the screen, and with ``free`` its data too."""
    return _command(f"a=d,d={'I' if free else 'i'},i={image_id},q=2")


def _command(keys: str, payload: bytes = b"") -> str:
    """A kitty graphics command, the payload split in chunks; inside tmux each chunk goes through
    its passthrough (``allow-passthrough on``) to the outer terminal."""
    encoded = base64.standard_b64encode(payload).decode()
    chunks = [encoded[i : i + 4096] for i in range(0, len(encoded), 4096)] or [""]
    out = []
    for n, chunk in enumerate(chunks):
        head = f"{keys},m={int(n < len(chunks) - 1)}" if n == 0 else f"m={int(n < len(chunks) - 1)}"
        sequence = f"\x1b_G{head};{chunk}\x1b\\" if chunk else f"\x1b_G{head}\x1b\\"
        if os.environ.get("TMUX"):
            sequence = "\x1bPtmux;" + sequence.replace("\x1b", "\x1b\x1b") + "\x1b\\"
        out.append(sequence)
    return "".join(out)


# Unicode placeholders: each cell is U+10EEEE with diacritics for its row, its column and the image
# ID's high byte; the low byte is the 256-color foreground (which tmux passes through unchanged).
PLACEHOLDER = "\U0010eeee"
_DIACRITIC_RANGES = (
    (0x305, 0x305), (0x30D, 0x30E), (0x310, 0x310), (0x312, 0x312), (0x33D, 0x33F), (0x346, 0x346),
    (0x34A, 0x34C), (0x350, 0x352), (0x357, 0x357), (0x35B, 0x35B), (0x363, 0x36F), (0x483, 0x487),
    (0x592, 0x595), (0x597, 0x599), (0x59C, 0x5A1), (0x5A8, 0x5A9), (0x5AB, 0x5AC), (0x5AF, 0x5AF),
    (0x5C4, 0x5C4), (0x610, 0x617), (0x657, 0x65B), (0x65D, 0x65E), (0x6D6, 0x6DC), (0x6DF, 0x6E2),
    (0x6E4, 0x6E4), (0x6E7, 0x6E8), (0x6EB, 0x6EC), (0x730, 0x730), (0x732, 0x733), (0x735, 0x736),
    (0x73A, 0x73A), (0x73D, 0x73D), (0x73F, 0x741), (0x743, 0x743), (0x745, 0x745), (0x747, 0x747),
    (0x749, 0x74A), (0x7EB, 0x7F1), (0x7F3, 0x7F3), (0x816, 0x819), (0x81B, 0x823), (0x825, 0x827),
    (0x829, 0x82D), (0x951, 0x951), (0x953, 0x954), (0xF82, 0xF83), (0xF86, 0xF87), (0x135D, 0x135F),
    (0x17DD, 0x17DD), (0x193A, 0x193A), (0x1A17, 0x1A17), (0x1A75, 0x1A7C), (0x1B6B, 0x1B6B),
    (0x1B6D, 0x1B73), (0x1CD0, 0x1CD2), (0x1CDA, 0x1CDB), (0x1CE0, 0x1CE0), (0x1DC0, 0x1DC1),
    (0x1DC3, 0x1DC9), (0x1DCB, 0x1DCC), (0x1DD1, 0x1DE6), (0x1DFE, 0x1DFE), (0x20D0, 0x20D1),
    (0x20D4, 0x20D7), (0x20DB, 0x20DC), (0x20E1, 0x20E1), (0x20E7, 0x20E7), (0x20E9, 0x20E9),
    (0x20F0, 0x20F0), (0x2CEF, 0x2CF1), (0x2DE0, 0x2DFF), (0xA66F, 0xA66F), (0xA67C, 0xA67D),
    (0xA6F0, 0xA6F1), (0xA8E0, 0xA8F1), (0xAAB0, 0xAAB0), (0xAAB2, 0xAAB3), (0xAAB7, 0xAAB8),
    (0xAABE, 0xAABF), (0xAAC1, 0xAAC1), (0xFE20, 0xFE26), (0x10A0F, 0x10A0F), (0x10A38, 0x10A38),
    (0x1D185, 0x1D189), (0x1D1AA, 0x1D1AD), (0x1D242, 0x1D244),
)  # fmt: skip
DIACRITICS = "".join(chr(c) for low, high in _DIACRITIC_RANGES for c in range(low, high + 1))  # kitty's table
_SESSION = random.randint(1, 255)  # high byte of this run's image IDs, so they don't clash with earlier runs


MAX_IMAGES = 240


def image_id(n: int) -> int:
    """The ID of an article's ``n``-th image (1 to ``MAX_IMAGES``). Its low byte is a 256-color
    index above the 16 basic colors, which tmux could rewrite to other escapes."""
    return _SESSION << 24 | (n + 15)


def placeholder_row(image_id: int, row: int, cols: int) -> str:
    """Text drawing row ``row`` of an image's virtual placement, ``cols`` cells wide."""
    cells = "".join(PLACEHOLDER + DIACRITICS[row] + DIACRITICS[col] + DIACRITICS[image_id >> 24] for col in range(cols))
    return f"\x1b[38;5;{image_id & 255}m{cells}\x1b[0m"


class Loader:
    """Fetches images concurrently and decodes JPEG/GIF in worker processes (pure-Python decoding
    is CPU-bound), so an article's images are ready about as fast as its slowest one."""

    def __init__(self, fetch: Callable[[str], bytes | None], urls: list[str], max_width: int):
        self.fetch, self.max_width = fetch, max_width
        try:
            self.decoders: ProcessPoolExecutor | None = ProcessPoolExecutor(max_workers=min(4, os.cpu_count() or 1))
        except OSError, NotImplementedError:  # no multiprocessing on this platform
            self.decoders = None
        self.threads = ThreadPoolExecutor(max_workers=8)
        self.jobs = {url: self.threads.submit(self._load, url) for url in dict.fromkeys(urls)}

    def _load(self, url: str) -> tuple[int, int, str, bytes] | None:
        data = self.fetch(url)
        dims = size(data) if data else None
        if not dims or not all(dims):
            return None
        try:
            if not data.startswith(PNG_MAGIC):
                try:
                    if self.decoders is None:
                        raise RuntimeError("no worker processes")
                    keys, payload = self.decoders.submit(encode, data, self.max_width).result()
                except OSError, RuntimeError, BrokenProcessPool:  # decode in this process instead
                    keys, payload = encode(data, self.max_width)
            else:
                keys, payload = encode(data)
        except ImageError, IndexError, KeyError, ValueError, struct.error, zlib.error:
            return None
        return dims[0], dims[1], keys, payload

    def get(self, url: str) -> tuple[int, int, str, bytes] | None:
        """(width, height, kitty keys, payload) once loaded, or None if it cannot be shown."""
        job = self.jobs.get(url)
        return job.result() if job else None

    def close(self) -> None:
        self.threads.shutdown(wait=False, cancel_futures=True)
        if self.decoders:
            self.decoders.shutdown(wait=False, cancel_futures=True)


# ------------------------------------------------------------------------ GIF
def decode_gif(data: bytes) -> tuple[int, int, bytes]:
    """First frame of a GIF as RGBA (transparent pixels stay transparent)."""
    width, height, flags = struct.unpack("<HHB", data[6:11])
    pos = 13
    palette = b""
    if flags & 0x80:
        n = 3 << ((flags & 7) + 1)
        palette, pos = data[pos : pos + n], pos + n
    transparent = None
    while pos < len(data):
        block = data[pos]
        if block == 0x21:  # extension: remember the transparent index, skip the rest
            label = data[pos + 1]
            pos += 2
            if label == 0xF9 and data[pos + 1] & 1:
                transparent = data[pos + 4]
            while data[pos]:
                pos += data[pos] + 1
            pos += 1
        elif block == 0x2C:
            fx, fy, fw, fh, fflags = struct.unpack("<HHHHB", data[pos + 1 : pos + 10])
            pos += 10
            if fflags & 0x80:
                n = 3 << ((fflags & 7) + 1)
                palette, pos = data[pos : pos + n], pos + n
            min_code = data[pos]
            pos += 1
            chunks = []
            while data[pos]:
                chunks.append(data[pos + 1 : pos + 1 + data[pos]])
                pos += data[pos] + 1
            indexes = _lzw(b"".join(chunks), min_code, fw * fh)
            if fflags & 0x40:  # interlaced rows: 0,8,.. 4,12,.. 2,6,.. 1,3,..
                order = [*range(0, fh, 8), *range(4, fh, 8), *range(2, fh, 4), *range(1, fh, 2)]
                rows = [b""] * fh
                for i, y in enumerate(order):
                    rows[y] = indexes[i * fw : (i + 1) * fw]
                indexes = b"".join(rows)
            colors = [palette[i * 3 : i * 3 + 3] + b"\xff" for i in range(256)]
            colors = [c if len(c) == 4 else b"\0\0\0\xff" for c in colors]
            if transparent is not None:
                colors[transparent] = b"\0\0\0\0"
            canvas = bytearray(width * height * 4)
            for y in range(min(fh, height - fy)):
                row = b"".join(colors[i] for i in indexes[y * fw : y * fw + min(fw, width - fx)])
                start = ((fy + y) * width + fx) * 4
                canvas[start : start + len(row)] = row
            return width, height, bytes(canvas)
        else:
            break
    raise ImageError("GIF has no image")


def _lzw(data: bytes, min_code: int, count: int) -> bytes:
    clear, end = 1 << min_code, (1 << min_code) + 1
    out = bytearray()
    table: list[bytes] = []
    size, acc, nbits, prev = min_code + 1, 0, 0, None
    for byte in data:
        acc |= byte << nbits
        nbits += 8
        while nbits >= size:
            code = acc & ((1 << size) - 1)
            acc >>= size
            nbits -= size
            if code == clear:
                table = [bytes([i]) for i in range(clear)] + [b"", b""]
                size, prev = min_code + 1, None
                continue
            if code == end or not table:
                return bytes(out[:count])
            if code < len(table):
                entry = table[code]
                if prev is not None:
                    table.append(prev + entry[:1])
            elif prev is not None:
                entry = prev + prev[:1]
                table.append(entry)
            else:
                raise ImageError("corrupt GIF data")
            out += entry
            prev = entry
            if len(table) == 1 << size and size < 12:
                size += 1
    return bytes(out[:count])


# ----------------------------------------------------------------------- JPEG
ZIGZAG = (
    0, 1, 8, 16, 9, 2, 3, 10, 17, 24, 32, 25, 18, 11, 4, 5, 12, 19, 26, 33, 40, 48, 41, 34, 27, 20, 13, 6, 7, 14,
    21, 28, 35, 42, 49, 56, 57, 50, 43, 36, 29, 22, 15, 23, 30, 37, 44, 51, 58, 59, 52, 45, 38, 31, 39, 46, 53,
    60, 61, 54, 47, 55, 62, 63,
)  # fmt: skip


class _Huffman:
    __slots__ = ("fast", "maxcode", "offset", "values")

    def __init__(self, counts: bytes, values: bytes):
        self.values = values
        self.fast = [-1] * 512  # 9-bit lookahead: (length << 8) | value
        self.maxcode = [-1] * 18
        self.offset = [0] * 18
        code = k = 0
        for length in range(1, 17):
            n = counts[length - 1]
            self.offset[length] = k - code
            for _ in range(n):
                if length <= 9:
                    shift = 9 - length
                    for low in range(1 << shift):
                        self.fast[(code << shift) | low] = (length << 8) | values[k]
                code += 1
                k += 1
            self.maxcode[length] = code - 1 if n else -1
            code <<= 1
        self.maxcode[17] = 1 << 30


class _Bits:
    """MSB-first bit reader over one restart interval of unstuffed entropy-coded data."""

    __slots__ = ("acc", "data", "nbits", "pos")

    def __init__(self, data: bytes):
        self.data, self.pos, self.acc, self.nbits = data, 0, 0, 0

    def fill(self) -> None:
        data, pos, acc, nbits = self.data, self.pos, self.acc, self.nbits
        end = len(data)
        while nbits <= 24:
            acc = ((acc << 8) | (data[pos] if pos < end else 0)) & 0xFFFFFFFFFF
            pos += 1
            nbits += 8
        self.pos, self.acc, self.nbits = pos, acc, nbits

    def bits(self, n: int) -> int:
        if self.nbits < n:
            self.fill()
        self.nbits -= n
        return (self.acc >> self.nbits) & ((1 << n) - 1)

    def receive(self, s: int) -> int:
        """Read an ``s``-bit magnitude and sign-extend it (JPEG F.2.2.1)."""
        if self.nbits < s:
            self.fill()
        self.nbits -= s
        v = (self.acc >> self.nbits) & ((1 << s) - 1)
        return v if v >> (s - 1) else v - (1 << s) + 1

    def decode(self, h: _Huffman) -> int:
        if self.nbits < 16:
            self.fill()
        entry = h.fast[(self.acc >> (self.nbits - 9)) & 511]
        if entry >= 0:
            self.nbits -= entry >> 8
            return entry & 255
        code = (self.acc >> (self.nbits - 16)) & 0xFFFF
        for length in range(10, 17):
            c = code >> (16 - length)
            if c <= h.maxcode[length]:
                self.nbits -= length
                return h.values[c + h.offset[length]]
        raise ImageError("corrupt JPEG data")


class _Component:
    __slots__ = ("blocks", "bpl", "cid", "dc_pred", "h", "lines", "q", "tq", "v")

    def __init__(self, cid: int, h: int, v: int, tq: int):
        self.cid, self.h, self.v, self.tq = cid, h, v, tq
        self.dc_pred = 0


def decode_jpeg(data: bytes, max_width: int | None = None) -> tuple[int, int, bytes]:
    """Decode a baseline or progressive Huffman JPEG to RGB.

    With ``max_width`` the image is decoded at 1/2, 1/4 or 1/8 scale when that still leaves at
    least ``max_width`` pixels, which is much faster (the reduced inverse DCT only uses the
    low-frequency coefficients).
    """
    if not data.startswith(b"\xff\xd8"):
        raise ImageError("not a JPEG")
    qts: dict[int, list[int]] = {}
    dc_tables: dict[int, _Huffman] = {}
    ac_tables: dict[int, _Huffman] = {}
    comps: list[_Component] = []
    width = height = restart = 0
    progressive = False
    adobe_transform = None
    pos = 2
    while pos < len(data):
        if data[pos] != 0xFF:
            raise ImageError("corrupt JPEG marker")
        marker = data[pos + 1]
        pos += 2
        if marker == 0xFF:  # fill byte
            pos -= 1
            continue
        if marker == 0xD9:
            break
        length = struct.unpack(">H", data[pos : pos + 2])[0]
        seg = data[pos + 2 : pos + length]
        pos += length
        if marker == 0xDB:
            i = 0
            while i < len(seg):
                precision, tq = seg[i] >> 4, seg[i] & 15
                if precision:
                    zz = struct.unpack(">64H", seg[i + 1 : i + 129])
                    i += 129
                else:
                    zz = seg[i + 1 : i + 65]
                    i += 65
                table = [0] * 64
                for k in range(64):
                    table[ZIGZAG[k]] = zz[k]
                qts[tq] = table
        elif marker == 0xC4:
            i = 0
            while i < len(seg):
                tc, th = seg[i] >> 4, seg[i] & 15
                counts = seg[i + 1 : i + 17]
                n = sum(counts)
                (ac_tables if tc else dc_tables)[th] = _Huffman(counts, seg[i + 17 : i + 17 + n])
                i += 17 + n
        elif marker in (0xC0, 0xC1, 0xC2):
            progressive = marker == 0xC2
            if seg[0] != 8:
                raise ImageError("only 8-bit JPEGs are supported")
            height, width, n = struct.unpack(">HHB", seg[1:6])
            comps = [
                _Component(seg[6 + 3 * k], seg[7 + 3 * k] >> 4, seg[7 + 3 * k] & 15, seg[8 + 3 * k]) for k in range(n)
            ]
            if not width or not height or n not in (1, 3):
                raise ImageError("unsupported JPEG layout")
            hmax, vmax = max(c.h for c in comps), max(c.v for c in comps)
            mcux, mcuy = math.ceil(width / (8 * hmax)), math.ceil(height / (8 * vmax))
            for c in comps:
                c.bpl, c.lines = mcux * c.h, mcuy * c.v
                c.blocks = [[0] * 64 for _ in range(c.bpl * c.lines)]
        elif marker in (0xC3, *range(0xC5, 0xC8), *range(0xC9, 0xD0)):
            raise ImageError("lossless, hierarchical and arithmetic-coded JPEGs are not supported")
        elif marker == 0xDD:
            restart = struct.unpack(">H", seg[:2])[0]
        elif marker == 0xEE and seg.startswith(b"Adobe") and len(seg) >= 12:
            adobe_transform = seg[11]
        elif marker == 0xDA:
            if not comps:
                raise ImageError("scan before frame header")
            ns = seg[0]
            by_id = {c.cid: c for c in comps}
            scan = []
            for k in range(ns):
                c = by_id[seg[1 + 2 * k]]
                td, ta = seg[2 + 2 * k] >> 4, seg[2 + 2 * k] & 15
                scan.append((c, dc_tables.get(td), ac_tables.get(ta)))
            ss, se, ah_al = seg[1 + 2 * ns], seg[2 + 2 * ns], seg[3 + 2 * ns]
            match = _SCAN_END.search(data, pos)
            end = match.start() if match else len(data)
            intervals = [part.replace(b"\xff\x00", b"\xff") for part in _RST.split(data[pos:end])]
            pos = end
            _decode_scan(scan, comps, width, height, intervals, restart, progressive, ss, se, ah_al >> 4, ah_al & 15)
    if not comps:
        raise ImageError("JPEG has no frame")
    for c in comps:
        if c.tq not in qts:
            raise ImageError("JPEG references a missing quantization table")
        c.q = qts[c.tq]

    scale = 1
    while max_width and scale < 8 and width / (scale * 2) >= max_width:
        scale *= 2
    return _to_rgb(comps, width, height, 8 // scale, adobe_transform)


_SCAN_END = re.compile(rb"\xff[^\x00\xd0-\xd7]")
_RST = re.compile(rb"\xff[\xd0-\xd7]")


def _decode_scan(scan, comps, width, height, intervals, restart, progressive, ss, se, ah, al):
    hmax, vmax = max(c.h for c in comps), max(c.v for c in comps)
    if len(scan) == 1:  # non-interleaved: one block per unit, over the component's own extent
        c = scan[0][0]
        cols = math.ceil(math.ceil(width * c.h / hmax) / 8)
        rows = math.ceil(math.ceil(height * c.v / vmax) / 8)
        units = [[(c, scan[0][1], scan[0][2], r * c.bpl + q)] for r in range(rows) for q in range(cols)]
    else:
        mcux, mcuy = math.ceil(width / (8 * hmax)), math.ceil(height / (8 * vmax))
        units = [
            [
                (c, dc, ac, (my * c.v + v) * c.bpl + mx * c.h + h)
                for c, dc, ac in scan
                for v in range(c.v)
                for h in range(c.h)
            ]
            for my in range(mcuy)
            for mx in range(mcux)
        ]
    if not progressive:
        decode = _block_baseline
    elif ss == 0:
        decode = _block_dc_first if ah == 0 else _block_dc_refine
    else:
        decode = _block_ac_first if ah == 0 else _block_ac_refine
    state = {"eobrun": 0}
    per_interval = restart or len(units)
    for n, start in enumerate(range(0, len(units), per_interval)):
        reader = _Bits(intervals[n] if n < len(intervals) else b"")
        for c in comps:
            c.dc_pred = 0
        state["eobrun"] = 0
        for unit in units[start : start + per_interval]:
            for c, dc, ac, index in unit:
                decode(reader, c, dc, ac, c.blocks[index], ss, se, al, state)


def _block_baseline(r: _Bits, c, dc, ac, blk, ss, se, al, state):
    t = r.decode(dc)
    c.dc_pred += r.receive(t) if t else 0
    blk[0] = c.dc_pred
    k = 1
    decode, receive = r.decode, r.receive
    while k < 64:
        rs = decode(ac)
        s = rs & 15
        if not s:
            if rs != 0xF0:
                break
            k += 16
            continue
        k += rs >> 4
        if k > 63:
            break
        blk[ZIGZAG[k]] = receive(s)
        k += 1


def _block_dc_first(r, c, dc, ac, blk, ss, se, al, state):
    t = r.decode(dc)
    c.dc_pred += r.receive(t) if t else 0
    blk[0] = c.dc_pred << al


def _block_dc_refine(r, c, dc, ac, blk, ss, se, al, state):
    if r.bits(1):
        blk[0] |= 1 << al


def _block_ac_first(r, c, dc, ac, blk, ss, se, al, state):
    if state["eobrun"]:
        state["eobrun"] -= 1
        return
    k = ss
    while k <= se:
        rs = r.decode(ac)
        run, s = rs >> 4, rs & 15
        if not s:
            if run < 15:
                state["eobrun"] = (1 << run) - 1 + (r.bits(run) if run else 0)
                return
            k += 16
            continue
        k += run
        if k > 63:
            return
        blk[ZIGZAG[k]] = r.receive(s) * (1 << al)
        k += 1


def _block_ac_refine(r, c, dc, ac, blk, ss, se, al, state):
    p1, m1 = 1 << al, -1 << al
    k = ss
    if not state["eobrun"]:
        while k <= se:
            rs = r.decode(ac)
            run, s = rs >> 4, rs & 15
            if s:
                s = p1 if r.bits(1) else m1
            elif run != 15:
                state["eobrun"] = (1 << run) + (r.bits(run) if run else 0)
                break
            while k <= se:
                z = ZIGZAG[k]
                if blk[z]:
                    if r.bits(1) and not blk[z] & p1:
                        blk[z] += p1 if blk[z] >= 0 else m1
                else:
                    run -= 1
                    if run < 0:
                        break
                k += 1
            if s and k <= 63:
                blk[ZIGZAG[k]] = s
            k += 1
    if state["eobrun"]:
        while k <= se:
            z = ZIGZAG[k]
            if blk[z] and r.bits(1) and not blk[z] & p1:
                blk[z] += p1 if blk[z] >= 0 else m1
            k += 1
        state["eobrun"] -= 1


# The inverse DCT runs on packed integers: an n×n output block is one big int holding n·n 64-bit
# lanes (16.16 fixed point), so each coefficient adds a single precomputed multiple of its basis
# image to the block (exact integer SIMD in pure Python). Most blocks have only a few coefficients.
_LANE = 64
_FRAC = 16
_OFFSET = 1024  # lane value = (pixel + 128 + _OFFSET) << _FRAC, so bits 16-31 index _CLAMP
_INDEXES = range(64)
_CLAMP = bytes(min(255, max(0, i - _OFFSET)) for i in range(65536))


def _basis(n: int) -> dict[int, int]:
    """Packed n×n images of each 8×8 DCT basis function (low frequencies only), keyed by natural index."""
    weights = []
    for x in range(n):
        xs = (x + 0.5) * 8 / n - 0.5  # sample the 8-pixel basis at the centers of n output pixels
        weights.append([(0.125**0.5 if u == 0 else 0.5) * math.cos((2 * xs + 1) * u * math.pi / 16) for u in range(n)])
    basis = {}
    for v in range(n):
        for u in range(n):
            packed = 0
            for y in range(n):
                for x in range(n):
                    packed += round(weights[y][v] * weights[x][u] * (1 << _FRAC)) << (_LANE * (y * n + x))
            basis[v * 8 + u] = packed
    return basis


def _to_rgb(comps, width, height, n, adobe_transform) -> tuple[int, int, bytes]:
    """Inverse DCT each block at ``n``×``n`` pixels, then upsample chroma and convert to RGB."""
    basis = _basis(n)
    bias = sum(((128 + _OFFSET) << _FRAC) + (1 << (_FRAC - 1)) << (_LANE * i) for i in range(n * n))
    nbytes = n * n * _LANE // 8
    clamp = _CLAMP.__getitem__
    planes = []
    for c in comps:
        stride = c.bpl * n
        plane = bytearray(stride * c.lines * n)
        terms = [c.q[k] * basis[k] if k in basis else 0 for k in range(64)]  # dequantization folded in
        for index, blk in enumerate(c.blocks):
            acc = bias
            for k in compress(_INDEXES, blk):
                acc += blk[k] * terms[k]
            pixels = bytes(map(clamp, memoryview(acc.to_bytes(nbytes, "little")).cast("H")[1::4]))
            origin = (index // c.bpl) * n * stride + (index % c.bpl) * n
            for y in range(n):
                start = origin + y * stride
                plane[start : start + n] = pixels[y * n : y * n + n]
        planes.append((plane, stride, c))

    out_w = math.ceil(width * n / 8)
    out_h = math.ceil(height * n / 8)
    hmax, vmax = max(c.h for c in comps), max(c.v for c in comps)

    def row_of(plane, stride, c, y):
        """Row ``y`` of a component plane, stretched to full resolution."""
        sy = y * c.v // vmax
        row = plane[sy * stride : sy * stride + stride]
        factor = hmax // c.h
        if factor > 1:
            wide = bytearray(len(row) * factor)
            for k in range(factor):
                wide[k::factor] = row
            row = wide
        return row[:out_w]

    rgb = bytearray(out_w * out_h * 3)
    span = out_w * 3
    if len(planes) == 1:
        for y in range(out_h):
            row = row_of(*planes[0], y)
            for k in range(3):
                rgb[y * span + k : (y + 1) * span : 3] = row
        return out_w, out_h, bytes(rgb)
    if adobe_transform == 0:  # Adobe APP14 transform=0: components are already RGB
        for y in range(out_h):
            for k in range(3):
                rgb[y * span + k : (y + 1) * span : 3] = row_of(*planes[k], y)
        return out_w, out_h, bytes(rgb)
    lut = _OFFSET  # the clamp table's zero point, so values stay non-negative indexes
    cr_r = [round(1.402 * (i - 128)) + lut for i in range(256)].__getitem__
    cb_g = [round(-0.344136 * (i - 128)) for i in range(256)].__getitem__
    cr_g = [round(-0.714136 * (i - 128)) + lut for i in range(256)].__getitem__
    cb_b = [round(1.772 * (i - 128)) + lut for i in range(256)].__getitem__
    for y in range(out_h):
        ys, cbs, crs = (row_of(*planes[k], y) for k in range(3))
        base = y * span
        green = map(add, map(add, ys, map(cb_g, cbs), strict=True), map(cr_g, crs), strict=True)
        rgb[base : base + span : 3] = bytes(map(clamp, map(add, ys, map(cr_r, crs), strict=True)))
        rgb[base + 1 : base + span : 3] = bytes(map(clamp, green))
        rgb[base + 2 : base + span : 3] = bytes(map(clamp, map(add, ys, map(cb_b, cbs), strict=True)))
    return out_w, out_h, bytes(rgb)
