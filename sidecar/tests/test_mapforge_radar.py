"""Unit tests for the MapForge radar STI encoder (A3).

These exercise the pure encode path (no install/renderer): a synthetic
88x44 image -> write_radar_sti -> decode it back and assert it's a valid
88x44 single-frame STI. The render half (IsoRenderer) is verified by a
real-install smoke, not here.
"""
import io

from PIL import Image

from mercwizard_core.mapforge_engine.radar import (
    write_radar_sti, _dither_to_enough_colors, RADAR_W, RADAR_H,
)
from mercwizard_core.sti_decode import decode_sti_frame_to_png


def _multicolor(w=RADAR_W, h=RADAR_H):
    im = Image.new("RGBA", (w, h))
    px = im.load()
    for y in range(h):
        for x in range(w):
            px[x, y] = ((x * 3) % 256, (y * 5) % 256, (x * y) % 256, 255)
    return im


def test_write_radar_sti_roundtrips_88x44(tmp_path):
    out = tmp_path / "A9.STI"
    write_radar_sti(_multicolor(), out)
    assert out.exists()
    png = decode_sti_frame_to_png(out.read_bytes(), 0)
    assert png is not None
    assert Image.open(io.BytesIO(png)).size == (RADAR_W, RADAR_H)


def test_write_radar_sti_handles_near_uniform(tmp_path):
    # A flat sector would trip quantize_with_anchor's 255-color floor; the
    # dither fallback must keep it from raising.
    im = Image.new("RGBA", (RADAR_W, RADAR_H), (40, 60, 30, 255))
    out = tmp_path / "FLAT.STI"
    write_radar_sti(im, out)  # must not raise
    png = decode_sti_frame_to_png(out.read_bytes(), 0)
    assert png is not None
    assert Image.open(io.BytesIO(png)).size == (RADAR_W, RADAR_H)


def test_dither_reaches_enough_colors():
    im = Image.new("RGBA", (RADAR_W, RADAR_H), (40, 60, 30, 255))
    out = _dither_to_enough_colors(im)
    colors = out.convert("RGB").getcolors(maxcolors=100000)
    assert colors is not None and len(colors) >= 255


def test_write_radar_sti_leaves_no_tmp(tmp_path):
    out = tmp_path / "A1.STI"
    write_radar_sti(_multicolor(), out)
    assert not (tmp_path / "A1.STI.tmp").exists()
