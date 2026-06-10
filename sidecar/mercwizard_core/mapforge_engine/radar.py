r"""
radar.py — MapForge radar/minimap STI generator (A3).

JA2 1.13 loads a pre-baked 88x44, 8-bit, ETRLE, single-frame STI as the
tactical minimap background (`RADARMAPS\<name>.STI`); soldier dots + the
viewport box are composited live on top. If the STI is missing the engine
silently shows a blank minimap. The engine's own editor "Radar Map" button
/ `-DOMAPS` writes this file into the writable VFS profile.

We mirror that: render the sector, scale to fill 88x44, encode an 8-bit
ETRLE STI. The WRITE LOCATION (the writable profile, above Radarmaps.slf)
is handled by the route via `vfs.resolve_override_write` — NOT here.

Honesty note: we downscale the tactical iso render rather than the engine's
dedicated overhead small-tile pass, so the result is functional (correct
format, terrain-shaped, fills the rect like vanilla — verified by decoding
a real A9.STI) but NOT pixel-faithful; live soldier-pin alignment is
approximate.
"""
from __future__ import annotations

import os
from pathlib import Path

from PIL import Image

from .iso_renderer import IsoRenderer
from ..portrait.sti import write_static_sti
from ..portrait.quantize import PortraitPaletteTooFewColors

# Engine constants (Radar Screen.cpp:98-99 RADAR_WINDOW_WIDTH/HEIGHT).
RADAR_W = 88
RADAR_H = 44


def render_radar_image(
    dat_path: Path,
    xml_path: Path,
    tileset: int,
    loose_dirs: list[Path],
    slf_paths: list[Path],
) -> Image.Image:
    """Render the full sector and scale it to fill an 88x44 RGBA image.

    Crops the renderer's content bbox first (the iso canvas carries
    asymmetric sprite-overhang margins) so the terrain fills the radar
    rectangle the way a real radar STI does, then stretches to 88x44.
    """
    renderer = IsoRenderer(
        dat_path, xml_path, tileset, ring=0,
        loose_dirs=loose_dirs, slf_paths=slf_paths,
    )
    canvas = renderer.render(
        room_id=None, bbox=None, highlight_room=False, skip_layers=set(),
    )
    if canvas.mode != "RGBA":
        canvas = canvas.convert("RGBA")
    # Drop the transparent overhang margins so we fill the rect with terrain.
    box = canvas.getbbox()
    if box is not None:
        canvas = canvas.crop(box)
    return canvas.resize((RADAR_W, RADAR_H), Image.Resampling.BOX)


def _dither_to_enough_colors(img: Image.Image) -> Image.Image:
    """Inject an imperceptible, deterministic +/-3 dither so a near-uniform
    render reaches >=255 distinct colors — `write_static_sti`'s quantizer
    reserves index 0 and demands 255 colors, which a flat/blank sector
    wouldn't otherwise have. 7^3 = 343 offset combinations guarantees the
    255 needed even from a single base color; +/-3 is invisible at 88x44.
    """
    img = img.convert("RGBA")
    px = img.load()
    w, h = img.size
    for y in range(h):
        for x in range(w):
            r, g, b, a = px[x, y]
            k = x * 13 + y * 7
            dr = (k % 7) - 3
            dg = ((k // 7) % 7) - 3
            db = ((k // 49) % 7) - 3
            px[x, y] = (
                min(255, max(0, r + dr)),
                min(255, max(0, g + dg)),
                min(255, max(0, b + db)),
                a,
            )
    return img


def write_radar_sti(img: Image.Image, out_path: Path) -> Path:
    """Encode `img` (any size; callers pass 88x44) as an 8-bit ETRLE STI at
    `out_path`, atomically. Tolerant of near-uniform renders.

    Reuses `write_static_sti` (composite-over-black + index-0 reservation +
    single-frame 8-bit ETRLE — the same encoder the merc pipeline ships).
    On a flat sector that trips the 255-color floor, retries once with an
    imperceptible dither rather than failing.
    """
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = out_path.with_name(out_path.name + ".tmp")
    try:
        try:
            write_static_sti(tmp, img)
        except PortraitPaletteTooFewColors:
            write_static_sti(tmp, _dither_to_enough_colors(img))
        os.replace(tmp, out_path)
    finally:
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass
    return out_path
