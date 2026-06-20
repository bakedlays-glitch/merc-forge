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


def test_dither_survives_gamut_edges_and_transparency(tmp_path):
    """The review reproduced three bases where the old clamp-style dither
    collapsed under the 255-color floor: flat black (64 colors), flat
    white (64), and fully transparent (1 — the composite-over-black
    discarded the RGB dither). All three must now encode cleanly."""
    cases = {
        "BLACK.STI": (0, 0, 0, 255),
        "WHITE.STI": (255, 255, 255, 255),
        "CLEAR.STI": (0, 0, 0, 0),       # blank/new sector render
    }
    for name, rgba in cases.items():
        im = Image.new("RGBA", (RADAR_W, RADAR_H), rgba)
        dithered = _dither_to_enough_colors(im)
        colors = dithered.convert("RGB").getcolors(maxcolors=100000)
        assert colors is not None and len(colors) >= 255, name
        out = tmp_path / name
        write_radar_sti(im, out)          # must not raise
        png = decode_sti_frame_to_png(out.read_bytes(), 0)
        assert png is not None, name


# ─── Hub radar-thumbnail sprite sheet (read path) ───────────────────────

def _pack_radarmaps_slf(path, codes):
    """Pack valid 88x44 radar STIs into a Radarmaps.slf at `path`, one per
    sector `code` (root-stored as `<CODE>.STI`, like vanilla)."""
    from ja2py.fileformats.SlfFS import BufferedSlfFS
    slf = BufferedSlfFS()
    slf.library_name = "TEST"
    slf.library_path = "Radarmaps.slf"
    for code in codes:
        tmp = path.parent / f"_{code}.sti"
        write_radar_sti(_multicolor(), tmp)
        with slf.open(f"/{code}.STI", "wb") as f:
            f.write(tmp.read_bytes())
    with open(path, "wb") as f:
        slf.save(f)


def test_radar_thumb_sheet_bakes_from_radarmaps_slf(tmp_path):
    """The hub radar-thumbnail bake packs each sector's radar STI from
    Radarmaps.slf into one sprite sheet + a code→cell manifest. Regression
    guard: this read path drives the strategic-grid map previews and a
    refactor of _bake_radar_thumb_sheet could silently blank every cell."""
    from routes.mapforge import (
        _bake_radar_thumb_sheet, _RADAR_CELL_W, _RADAR_CELL_H,
        _RADAR_THUMB_COLS,
    )

    install = tmp_path / "inst"
    (install / "Data").mkdir(parents=True)
    _pack_radarmaps_slf(install / "Data" / "Radarmaps.slf", ("C5", "A9"))

    png, man = _bake_radar_thumb_sheet(install)
    assert png[:8] == b"\x89PNG\r\n\x1a\n"
    assert man["count"] == 2
    assert man["errors"] == []
    assert man["cell_w"] == _RADAR_CELL_W and man["cell_h"] == _RADAR_CELL_H
    assert man["cols"] == _RADAR_THUMB_COLS
    cells = {c["code"]: (c["x"], c["y"]) for c in man["cells"]}
    assert set(cells) == {"A9", "C5"}
    # Codes are laid out sorted, left→right at cell width (A9 before C5).
    assert cells["A9"] == (0, 0)
    assert cells["C5"] == (_RADAR_CELL_W, 0)


def _slf_with_image(path, code, img):
    """Pack a single <code>.STI radar (built from `img`) into a Radarmaps.slf."""
    from ja2py.fileformats.SlfFS import BufferedSlfFS
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.parent / f"_slf_{code}.sti"
    write_radar_sti(img, tmp)
    slf = BufferedSlfFS()
    slf.library_name = "TEST"
    slf.library_path = "Radarmaps.slf"
    with slf.open(f"/{code}.STI", "wb") as f:
        f.write(tmp.read_bytes())
    with open(path, "wb") as f:
        slf.save(f)


def test_radar_thumb_writable_profile_override_wins(tmp_path):
    """A radar the user regenerates lands in the WRITABLE VFS profile
    (Profiles/UserProfile_*/RADARMAPS — where sector_radar's
    resolve_override_write writes), NOT a content-layer dir. The thumb bake
    must read it FIRST so the mosaic shows the fresh art and a re-regenerate
    busts the cache.

    Proven with DISTINGUISHABLE art (the prior test used identical images +
    the wrong dir, so it passed without proving anything): the bundled SLF
    holds a BLUE A9, the writable-profile override a RED A9 — the baked cell
    must come out red, and re-writing the override must change the
    fingerprint even though it overwrites a file in place."""
    import io as _io
    import os as _os
    import sys as _sys
    from PIL import Image, ImageStat
    _sys.path.insert(0, _os.path.dirname(__file__))  # make sibling test modules importable
    from test_ini_editor import make_vfs_install
    from routes.mapforge import (
        _bake_radar_thumb_sheet, _radar_override_dir, _radar_thumb_fingerprint,
    )

    install = make_vfs_install(tmp_path / "inst")
    blue = Image.new("RGBA", (88, 44), (30, 30, 210, 255))
    red = Image.new("RGBA", (88, 44), (210, 30, 30, 255))
    _slf_with_image(install / "Data" / "Radarmaps.slf", "A9", blue)

    # The override goes where the engine + sector_radar actually write it.
    odir = _radar_override_dir(install)
    assert odir is not None and odir.name == "RADARMAPS", "write profile not resolved"
    odir.mkdir(parents=True, exist_ok=True)
    write_radar_sti(red, odir / "A9.STI")

    fp_before = _radar_thumb_fingerprint(install)
    png, man = _bake_radar_thumb_sheet(install)
    assert man["count"] == 1 and [c["code"] for c in man["cells"]] == ["A9"]
    cell = Image.open(_io.BytesIO(png)).convert("RGB").crop((0, 0, 88, 44))
    r, _g, b = ImageStat.Stat(cell).mean
    assert r > b + 40, f"override (red) must win over SLF (blue); got rgb≈({r:.0f},{_g:.0f},{b:.0f})"

    # Re-regenerate (overwrite in place) → fingerprint must change so the
    # cached sheet is rebuilt (dir mtime alone wouldn't catch this).
    write_radar_sti(Image.new("RGBA", (88, 44), (30, 210, 30, 255)), odir / "A9.STI")
    assert _radar_thumb_fingerprint(install) != fp_before
