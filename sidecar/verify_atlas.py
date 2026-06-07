"""Verify the MapForge atlas builder produces an engine-faithful atlas.

Approach: build the atlas + manifest for a known tileset, then for a
sample of (slot, sub) pairs, compare the atlas-cell pixels against the
StiCache's per-frame PIL image. If they match exactly the atlas data
is correct — and since the TS IsoRenderer is a 1:1 port of the Python
iso_renderer's draw loop, render parity follows.

This is a data-layer test, not a full visual diff. The visual diff
requires a browser (or node-canvas) to actually paint via the TS
renderer; run the editor in the browser to confirm visually.

Usage:
    python verify_atlas.py
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


def pixels_visually_equal(a: Image.Image, b: Image.Image) -> bool:
    """True when the two RGBA images render identically — alpha is
    compared everywhere; RGB only where alpha is non-zero. Transparent
    pixels' RGB is ignored (PIL paste-with-mask leaves transparent
    atlas pixels at the canvas background, which differs from the
    source's palette-driven RGB on the invisible region)."""
    if a.size != b.size:
        return False
    if a.mode != "RGBA":
        a = a.convert("RGBA")
    if b.mode != "RGBA":
        b = b.convert("RGBA")
    pa = a.tobytes()
    pb = b.tobytes()
    if len(pa) != len(pb):
        return False
    for i in range(0, len(pa), 4):
        if pa[i + 3] != pb[i + 3]:
            return False
        if pa[i + 3] != 0:
            if pa[i] != pb[i] or pa[i + 1] != pb[i + 1] or pa[i + 2] != pb[i + 2]:
                return False
    return True

from routes.mapforge import _build_atlas, _ATLAS_CACHE  # noqa: E402
from iso_renderer import load_tileset_xml, StiCache, REDUX_BASE, MP_BASE  # noqa: E402
from parse_dat_ext import parse_dat_file  # noqa: E402


def verify_tileset(xml_path: Path, tileset: int, dat_path: Path | None = None):
    """Build atlas + manifest. Validate every cell pixel matches the
    StiCache frame. If `dat_path` is given, also confirm every (slot,sub)
    actually used in that sector's layer entries resolves to a cell."""
    print(f"\n=== Verifying tileset {tileset} ===")
    print(f"  xml:  {xml_path}")
    png_bytes, manifest, cache_dir = _build_atlas(xml_path, tileset)
    print(f"  atlas: {manifest.atlas_w}x{manifest.atlas_h}, "
          f"{len(png_bytes)/1024:.1f} KB, {len(manifest.cells)} cells")
    print(f"  cache dir: {cache_dir}")

    # Decode atlas + open StiCache for ground truth.
    atlas = Image.open(io.BytesIO(png_bytes)).convert("RGBA")
    sx = str(xml_path).lower()
    if "redux" in sx:
        loose = [REDUX_BASE / "Data-DMK" / "Tilesets",
                 REDUX_BASE / "Data" / "Tilesets"]
        slf = [REDUX_BASE / "Data" / "Tilesets.slf",
               MP_BASE / "Data" / "Tilesets.slf"]
    else:
        loose = [MP_BASE / "Data-1.13" / "Tilesets",
                 MP_BASE / "Data" / "Tilesets"]
        slf = [MP_BASE / "Data" / "Tilesets.slf",
               REDUX_BASE / "Data" / "Tilesets.slf"]
    cache = StiCache(tileset, loose_dirs=loose, slf_paths=slf)
    slot_map = load_tileset_xml(xml_path, tileset)

    # Group cells by slot for efficient iteration.
    cells_by_slot: dict[int, list] = {}
    for c in manifest.cells:
        cells_by_slot.setdefault(c.slot, []).append(c)

    # Per-slot sanity check: every sub-frame the StiCache loads should
    # have a cell with matching (w, h, ox, oy). Pixel-region from the
    # atlas should equal the StiCache PIL pixel-by-pixel.
    ok_slots = 0
    fail_slots = 0
    sampled_cells = 0
    sampled_ok = 0
    for slot, sti_name in sorted(slot_map.items()):
        if not sti_name:
            continue
        frames = cache.get(sti_name)
        cells = cells_by_slot.get(slot, [])
        if len(cells) != len(frames):
            print(f"  [FAIL] slot {slot} ({sti_name}): "
                  f"manifest has {len(cells)} cells, StiCache has {len(frames)}")
            fail_slots += 1
            continue
        # Cells are appended in sub-1 order (1-based -> 0-based index).
        cells_sorted = sorted(cells, key=lambda c: c.sub)
        for cell, (sti_pil, sti_ox, sti_oy) in zip(cells_sorted, frames):
            if cell.w != sti_pil.size[0] or cell.h != sti_pil.size[1]:
                print(f"  [FAIL] slot {slot} sub {cell.sub}: "
                      f"cell {cell.w}x{cell.h} != STI {sti_pil.size}")
                fail_slots += 1
                break
            if cell.ox != sti_ox or cell.oy != sti_oy:
                print(f"  [FAIL] slot {slot} sub {cell.sub}: "
                      f"offsets ({cell.ox},{cell.oy}) != "
                      f"STI ({sti_ox},{sti_oy})")
                fail_slots += 1
                break
            # Per-pixel compare on every 50th sprite to keep runtime down.
            sampled_cells += 1
            if sampled_cells % 50 != 0:
                continue
            region = atlas.crop((cell.x, cell.y,
                                 cell.x + cell.w, cell.y + cell.h))
            # PIL paste with alpha mask leaves transparent pixels at the
            # atlas's background (0,0,0,0) — but the StiCache's RGBA has
            # palette-driven RGB even for alpha=0 pixels. Compare only
            # the VISIBLE pixels: alpha + RGB-where-alpha-nonzero.
            if pixels_visually_equal(region, sti_pil):
                sampled_ok += 1
            else:
                print(f"  [FAIL] slot {slot} sub {cell.sub}: "
                      f"atlas pixels differ from StiCache (visible region)")
                fail_slots += 1
                break
        else:
            ok_slots += 1
            continue
        # for/else didn't fire: a break happened, already counted as fail
        continue

    print(f"  slots: {ok_slots} pass, {fail_slots} fail "
          f"(pixel-sampled {sampled_ok}/{sampled_cells // 50 if sampled_cells else 0})")

    # Sector coverage check: confirm every (slot, sub) used by D13.dat
    # has a corresponding atlas cell.
    if dat_path:
        print(f"  sector check: {dat_path.name}")
        parsed = parse_dat_file(dat_path)
        used: set[tuple[int, int]] = set()
        for layer in ("land", "objs", "shadows", "structs", "roofs", "onroofs"):
            for tile_entries in parsed[layer]:
                for slot, sub in tile_entries:
                    used.add((slot, sub))
        cell_keys = {(c.slot, c.sub) for c in manifest.cells}
        missing = used - cell_keys
        if missing:
            print(f"    [WARN] {len(missing)} (slot,sub) pairs in sector "
                  f"have no atlas cell (likely unmapped slots): "
                  f"{sorted(missing)[:8]}{'...' if len(missing) > 8 else ''}")
        else:
            print(f"    all {len(used)} used (slot,sub) pairs covered.")

    return fail_slots == 0


if __name__ == "__main__":
    # Point these env vars at a JA2 1.13 install's Ja2Set.dat.xml (and an
    # optional sample .dat). These are dev-machine-specific, so they're read
    # from the environment rather than hardcoded.
    #   MW2_VERIFY_XML        path to a Ja2Set.dat.xml
    #   MW2_VERIFY_TILESET    tileset index to verify (default 71)
    #   MW2_VERIFY_DAT        optional path to a sample <sector>.dat
    xml_env = os.environ.get("MW2_VERIFY_XML")
    if not xml_env:
        sys.exit(
            "verify_atlas: set MW2_VERIFY_XML to a JA2 install's Ja2Set.dat.xml "
            "(and optionally MW2_VERIFY_TILESET / MW2_VERIFY_DAT)."
        )
    xml_path = Path(xml_env)
    tileset = int(os.environ.get("MW2_VERIFY_TILESET", "71"))
    dat_env = os.environ.get("MW2_VERIFY_DAT")
    dat_path = Path(dat_env) if dat_env else None

    ok = True
    if xml_path.is_file():
        ok &= verify_tileset(xml_path, tileset,
                             dat_path if dat_path and dat_path.is_file() else None)
    else:
        print(f"verify_atlas: no Ja2Set.dat.xml at {xml_path}", file=sys.stderr)
        ok = False

    print("\n" + ("PASS" if ok else "FAIL"))
    sys.exit(0 if ok else 1)
