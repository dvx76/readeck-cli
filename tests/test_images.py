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
