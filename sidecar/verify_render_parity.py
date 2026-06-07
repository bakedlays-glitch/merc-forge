"""Compare iso_renderer.py output against a Python re-render that uses
the SAME data the TS IsoRenderer uses (atlas + manifest + parsed dict).

If these match pixel-for-pixel, the data layer + render algorithm
match. The TS IsoRenderer's render() is a 1:1 transliteration of the
Python loop in iso_renderer.py, so a match here implies the TS port
will also match — without needing a browser to run the TS.

Usage:
    python verify_render_parity.py
"""
from __future__ import annotations

import io
import os
import sys
from pathlib import Path

# NOTE: dev-only verification tool. Depends on the separate Headless_Compiler
# project (and vendored ja2-open-toolset), which is NOT part of this repo. Set
# WASTELAND_ROOT to the dir containing your Headless_Compiler / ja2-open-toolset
# checkouts.
sys.path.insert(0, str(Path(__file__).resolve().parent))
_WASTELAND_ROOT = os.environ.get("WASTELAND_ROOT")
if _WASTELAND_ROOT:
    _wr = Path(_WASTELAND_ROOT)
    sys.path.insert(0, str(_wr / "Headless_Compiler"))
    sys.path.insert(0, str(_wr / "Headless_Compiler" / "map_corpus"))
    sys.path.insert(0, str(_wr / "ja2-open-toolset"))

from PIL import Image  # noqa: E402

from routes.mapforge import _build_atlas  # noqa: E402
from iso_renderer import IsoRenderer, TILE_W, TILE_H, WALL_HEIGHT  # noqa: E402
from parse_dat_ext import parse_dat_file  # noqa: E402


# Per-layer Y-lift table — must match LAYER_Y_LIFT in iso_renderer.py + TS.
LAYER_Y_LIFT = {
    "land": 0, "objs": 0, "shadows": 0, "structs": 0,
    "roofs": WALL_HEIGHT, "onroofs": WALL_HEIGHT,
}


def atlas_render(
    parsed: dict, atlas: Image.Image, manifest, room_id: int, ring: int = 5,
) -> Image.Image:
    """Reproduce iso_renderer.py's render loop using atlas cells instead
    of StiCache. Returns the composited PIL Image. This is what the TS
    code does, just in Python so we can pixel-diff vs the canonical
    renderer."""
    cols = parsed["cols"]
    rows = parsed["rows"]

    # Build (slot, sub) -> AtlasCell lookup, same as the TS code.
    cell_by_key: dict[tuple[int, int], object] = {}
    for c in manifest.cells:
        cell_by_key[(c.slot, c.sub)] = c

    # Resolve room region (same math as iso_renderer.py + TS).
    tiles = [(g % cols, g // cols)
             for g, r in enumerate(parsed["rooms"]) if r == room_id]
    if not tiles:
        raise ValueError(f"Room {room_id} not found")
    xs = [t[0] for t in tiles]
    ys = [t[1] for t in tiles]
    rx0 = max(0, min(xs) - ring)
    ry0 = max(0, min(ys) - ring)
    rx1 = min(cols - 1, max(xs) + ring)
    ry1 = min(rows - 1, max(ys) + ring)
    highlight = set(tiles)

    # Canvas size — same overhang margins.
    def t2p_raw(x, y): return ((x - y) * 20, (x + y) * 10)
    corners = [t2p_raw(x, y) for x in (rx0, rx1) for y in (ry0, ry1)]
    ix_min = min(p[0] for p in corners) - 80
    ix_max = max(p[0] for p in corners) + 80
    iy_min = min(p[1] for p in corners) - 200
    iy_max = max(p[1] for p in corners) + 60
    cw, ch = ix_max - ix_min, iy_max - iy_min
    cv = Image.new("RGBA", (cw, ch), (60, 50, 40, 255))

    # Room highlight (same diamond, same RGBA).
    from PIL import ImageDraw
    d = ImageDraw.Draw(cv)
    for tx, ty in highlight:
        px = (tx - ty) * 20 - ix_min
        py = (tx + ty) * 10 - iy_min
        diamond = [(px, py - TILE_H),
                   (px + TILE_W // 2, py - TILE_H // 2),
                   (px, py),
                   (px - TILE_W // 2, py - TILE_H // 2)]
        d.polygon(diamond, fill=(60, 120, 60, 70),
                  outline=(100, 200, 100, 150))

    # Build a darken-atlas equivalent of the StiCache shadow blending.
    # We bake it once instead of per-shadow-sprite for parity with TS.
    darken_atlas = atlas.copy()
    px = darken_atlas.load()
    for yy in range(darken_atlas.size[1]):
        for xx in range(darken_atlas.size[0]):
            r, g, b, a = px[xx, yy]
            px[xx, yy] = (0, 0, 0, a // 2)

    # Group tiles by iso-row.
    rows_by_xy: dict[int, list[tuple[int, int]]] = {}
    for ty in range(ry0, ry1 + 1):
        for tx in range(rx0, rx1 + 1):
            rows_by_xy.setdefault(tx + ty, []).append((tx, ty))
    for k in rows_by_xy:
        rows_by_xy[k].sort(key=lambda c: c[0] - c[1])
    ordered_xy = sorted(rows_by_xy)

    def composite_layer(layer: str, shadow: bool, y_lift: int):
        for xy in ordered_xy:
            for tx, ty in rows_by_xy[xy]:
                gn = ty * cols + tx
                for slot, sub in parsed[layer][gn]:
                    cell = cell_by_key.get((slot, sub))
                    if cell is None:
                        continue
                    paste_x = (tx - ty) * 20 - ix_min + cell.ox
                    paste_y = (tx + ty) * 10 - iy_min + cell.oy - y_lift
                    sprite = (darken_atlas if shadow else atlas).crop(
                        (cell.x, cell.y, cell.x + cell.w, cell.y + cell.h)
                    )
                    cv.alpha_composite(sprite, (paste_x, paste_y))

    # PASS 1, 2, 3: single-layer passes
    composite_layer("land", False, 0)
    composite_layer("objs", False, 0)
    composite_layer("shadows", True, 0)
    # PASS 4: STRUCT + ROOF + ONROOF grouped, level-major within iso row
    for xy in ordered_xy:
        for layer in ("structs", "roofs", "onroofs"):
            y_lift = LAYER_Y_LIFT[layer]
            for tx, ty in rows_by_xy[xy]:
                gn = ty * cols + tx
                src = atlas
                for slot, sub in parsed[layer][gn]:
                    cell = cell_by_key.get((slot, sub))
                    if cell is None:
                        continue
                    paste_x = (tx - ty) * 20 - ix_min + cell.ox
                    paste_y = (tx + ty) * 10 - iy_min + cell.oy - y_lift
                    sprite = src.crop((cell.x, cell.y,
                                       cell.x + cell.w, cell.y + cell.h))
                    cv.alpha_composite(sprite, (paste_x, paste_y))

    return cv


def diff_images(a: Image.Image, b: Image.Image) -> tuple[int, int, int]:
    """Return (different_pixels, total_pixels, max_channel_diff)."""
    if a.size != b.size:
        return (a.size[0] * a.size[1], a.size[0] * a.size[1], 255)
    pa = a.tobytes()
    pb = b.tobytes()
    diff = 0
    total = a.size[0] * a.size[1]
    max_d = 0
    for i in range(0, len(pa), 4):
        same = True
        for j in range(4):
            d = abs(pa[i + j] - pb[i + j])
            if d != 0:
                same = False
                if d > max_d:
                    max_d = d
        if not same:
            diff += 1
    return (diff, total, max_d)


def main():
    # Dev-machine-specific, so read from the environment rather than hardcode:
    #   MW2_VERIFY_DAT       path to a sample <sector>.dat
    #   MW2_VERIFY_XML       path to the matching Ja2Set.dat.xml
    #   MW2_VERIFY_TILESET   tileset index for that map (default 23)
    dat_env = os.environ.get("MW2_VERIFY_DAT")
    xml_env = os.environ.get("MW2_VERIFY_XML")
    if not (dat_env and xml_env):
        print(
            "verify_render_parity: set MW2_VERIFY_DAT + MW2_VERIFY_XML "
            "(and optionally MW2_VERIFY_TILESET) to a JA2 install's files."
        )
        return 1
    dat = Path(dat_env)
    xml = Path(xml_env)
    tileset = int(os.environ.get("MW2_VERIFY_TILESET", "23"))

    if not (dat.is_file() and xml.is_file()):
        print(f"Source files not found: {dat}, {xml}")
        return 1

    for room_id in (89, 102):
        print(f"\n=== D13 room {room_id} ===")
        # 1. Canonical render via iso_renderer.py
        renderer = IsoRenderer(dat, xml, tileset, ring=5)
        canonical = renderer.render(room_id=room_id, highlight_room=True)
        print(f"  canonical: {canonical.size}")

        # 2. Atlas-based re-render (what the TS does, in Python)
        png_bytes, manifest, _ = _build_atlas(xml, tileset)
        atlas = Image.open(io.BytesIO(png_bytes)).convert("RGBA")
        parsed = parse_dat_file(dat)
        ts_repro = atlas_render(parsed, atlas, manifest, room_id, ring=5)
        print(f"  atlas-repro: {ts_repro.size}")

        # 3. Pixel diff
        diff, total, max_d = diff_images(canonical, ts_repro)
        pct = (diff / total) * 100 if total else 0
        print(f"  diff: {diff}/{total} pixels ({pct:.3f}%), "
              f"max channel diff = {max_d}")

        # Save side-by-side for visual inspection if a non-trivial diff
        out_dir = Path(__file__).resolve().parent / "scratch"
        out_dir.mkdir(exist_ok=True)
        canonical.save(out_dir / f"d13_r{room_id}_canonical.png")
        ts_repro.save(out_dir / f"d13_r{room_id}_atlas_repro.png")
        if diff > 0:
            # Diff image: red where canonical and repro differ
            diff_img = Image.new("RGBA", canonical.size, (0, 0, 0, 255))
            pa = canonical.tobytes()
            pb = ts_repro.tobytes()
            px = bytearray(canonical.size[0] * canonical.size[1] * 4)
            for i in range(0, len(pa), 4):
                if pa[i:i+4] != pb[i:i+4]:
                    px[i:i+4] = b"\xff\x00\x00\xff"
                else:
                    # Dim the matching part so the red diff stands out
                    px[i:i+4] = bytes([pa[i] // 4, pa[i+1] // 4,
                                       pa[i+2] // 4, 255])
            diff_img = Image.frombytes("RGBA", canonical.size, bytes(px))
            diff_img.save(out_dir / f"d13_r{room_id}_diff.png")
            print(f"  wrote diff image to {out_dir}/d13_r{room_id}_diff.png")

    return 0


if __name__ == "__main__":
    sys.exit(main())
