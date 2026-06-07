"""Engine-faithful iso renderer for JA2 1.13 .dat sector files.

Reproduces the in-game static-world draw loop. Output is intended to match
what the JA2 map editor renders for the same tile data.

Engine source references (all paths relative to source-master/):
  TileEngine/renderworld.cpp:3440  RenderStaticWorld - layer-group dispatch
  TileEngine/renderworld.cpp:3477-3485  STRUCT+ROOF+ONROOF+TOPMOST grouped in one RenderTiles call
  TileEngine/renderworld.cpp:1187  RenderTiles - per-row, per-layer, per-tile, per-LEVELNODE walk
  TileEngine/renderworld.cpp:1340  Per-row level loop (level-major WITHIN a single iso row)
  TileEngine/worlddef.h:229-237   pLandHead..pTopmostHead aliases on pLevelNodes[0..8]
  TileEngine/worldman.cpp:1066    AddStructToTail (and AddObjectToTail/AddShadowToTail/
                                  AddRoofToTail/AddOnRoofToTail) - LoadWorld appends in
                                  stored order so pNext walk == .dat array order
  TileEngine/worldman.cpp:500     AddLandToHead - land is prepended at load, so the
                                  in-memory linked list is reverse-of-stored order;
                                  static-land render starts from pLandStart and walks
                                  pPrevNode. For a preview, drawing all land in stored
                                  order is visually equivalent for buildings (the
                                  pLandStart optimization is a perf shortcut, not a
                                  correctness requirement).
  Editor/newsmooth.cpp:281        gbWallTileLUT - EDITOR-time smoothing only; the
                                  engine renders stored sub-indices verbatim, so this
                                  renderer must NOT apply the LUT.

Iso projection (matches engine WORLD_TILE_X=40 / WORLD_TILE_Y=20):
  screen_x = (tile_x - tile_y) * 20
  screen_y = (tile_x + tile_y) * 10
The (screen_x, screen_y) point is the SOUTH apex of the diamond (bottom corner).
Each STI sub-frame stores (offset_x, offset_y) as UINT16 but the engine reads
them as INT16. Sprite top-left on the canvas:
  paste_x = screen_x + sti_offset_x
  paste_y = screen_y + sti_offset_y

STI quirks:
  - Palette index 0 == transparent (BUILD an alpha mask, do NOT trust PIL's
    auto-handling of P-mode transparency).
  - Shadow sprites are drawn as darken-blend: the alpha mask of the sprite
    is used to darken underlying canvas pixels by 50%, NOT pasted opaquely.

Draw order (matches RenderStaticWorld):
  Pass 1: LAND     (single-layer; iso-row order over the whole region)
  Pass 2: OBJECTS  (single-layer)
  Pass 3: SHADOWS  (single-layer; darken-blend)
  Pass 4: STRUCT + ROOF + ONROOF grouped:
            for each iso row (x+y ascending, back-to-front):
              for each layer in [structs, roofs, onroofs]:
                for each tile in row (x-y ascending, left-to-right):
                  for each stored entry: composite
          The level-major-WITHIN-a-row ordering is the critical fidelity fix:
          if structs and roofs are iterated layer-major across the whole map,
          a BACK roof can draw OVER a FRONT wall (the "stepped roof" bug in
          the previous scratch renderer).
"""
from __future__ import annotations

import argparse
import io
import os
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from .parse_dat_ext import parse_dat_file
# ja2py is vendored at the sidecar root and is top-level importable.
from ja2py.fileformats.Sti import load_8bit_sti, is_8bit_sti  # noqa: E402
from ja2py.fileformats.SlfFS import SlfFS  # noqa: E402


# Iso constants (engine WORLD_TILE_X / WORLD_TILE_Y)
TILE_W = 40
TILE_H = 20
TILE_HW = TILE_W // 2
TILE_HH = TILE_H // 2

# WALL_HEIGHT (tiledef.h:34) is the per-floor height the engine lifts roofs
# and onroofs by, so they sit on top of walls instead of at ground level.
# Applied at renderworld.cpp:1830 (STATIC_ROOF) and :1842 (STATIC_ONROOF).
# Without this, roof STIs (which are designed with offsets that put them at
# tile-floor level) render INSIDE the building footprint and get covered by
# walls — what looks like "missing roof pieces" and "roof not seated on the
# house" is actually the roof sinking into the walls.
WALL_HEIGHT = 50

# Per-layer Y-lift in pixels (matches the engine's per-case adjustments).
# Multi-floor buildings would stack: roofs of 2nd floor = WALL_HEIGHT * 2,
# but JA2 maps are 1-floor in the static-render world; the basement is a
# separate map. So one WALL_HEIGHT is sufficient.
LAYER_Y_LIFT = {
    "land": 0,
    "objs": 0,
    "shadows": 0,
    "structs": 0,
    "roofs": WALL_HEIGHT,
    "onroofs": WALL_HEIGHT,
}


def load_tileset_xml(xml_path: Path, tileset_index: int) -> dict[int, str]:
    """Slot index -> STI filename for one tileset, with inheritance from
    tileset 0 (the 'GENERIC 1' base) overlaid by the requested tileset.
    Matches the engine's LoadMapTileset inheritance behavior."""
    def _one(idx: int) -> dict[int, str]:
        tree = ET.parse(xml_path)
        for ts in tree.getroot().iter("Tileset"):
            if int(ts.get("index", -1)) == idx:
                fnode = ts.find("Files")
                if fnode is None:
                    return {}
                return {
                    int(f.get("index")): (f.text or "").strip()
                    for f in fnode.findall("file")
                }
        return {}
    base = _one(0) if tileset_index != 0 else {}
    override = _one(tileset_index)
    out = dict(base)
    out.update(override)
    return out


# Palette-index-0 → transparent alpha map, applied via bytes.translate (a
# C-level table) instead of a per-pixel Python loop. Index 0 → 0 alpha
# (transparent); every other index → 255 (opaque).
_STI_ALPHA_TABLE = bytes([0] + [255] * 255)


class StiCache:
    """Loads STIs from loose tileset dirs and Tilesets.slf, caches sub-frames
    as RGBA PIL images with palette-index-0 transparency applied and offsets
    converted from stored UINT16 to engine-semantic INT16."""

    def __init__(self, tileset_index: int,
                 loose_dirs: list[Path],
                 slf_paths: list[Path]):
        self.tileset_index = tileset_index
        self.loose_dirs = loose_dirs
        self.cache: dict[str, list] = {}
        self.slfs = []
        # filename (lowercased basename) -> (SlfFS, member path), built ONCE
        # so _extract_from_slf is an O(1) lookup instead of re-walking the
        # whole archive (thousands of members) per tile — the old behavior
        # made a cold bake O(tiles × archive-entries). First-opened SLF wins
        # a basename collision (matches the old first-in-walk-order match).
        self._slf_index: dict[str, tuple] = {}
        for slf in slf_paths:
            if slf.exists():
                try:
                    fs = SlfFS(str(slf))
                except Exception as e:
                    print(f"  [warn] could not open SLF {slf}: {e}",
                          file=sys.stderr)
                    continue
                self.slfs.append(fs)
                for path in fs.walk.files():
                    self._slf_index.setdefault(
                        os.path.basename(path).lower(), (fs, path))
        # SlfFS holds one file handle per archive; concurrent readbytes()
        # races on the file's seek position so multi-threaded callers
        # (MercWizard2's _build_atlas + _build_palette_sheet) need to
        # serialize the read step. The walk is read-only over an in-memory
        # index so it's safe unlocked; only readbytes needs the lock.
        # Lazy-import keeps the top-level imports of
        # this file unchanged for the existing read-only-elsewhere callers.
        import threading as _threading
        self._io_lock = _threading.Lock()
        self.misses: list[str] = []

    def _find_loose(self, name: str) -> Path | None:
        for base in self.loose_dirs:
            if not base.exists():
                continue
            for sub in (str(self.tileset_index), "0"):
                for variant in (name, name.upper(), name.lower()):
                    p = base / sub / variant
                    if p.exists():
                        return p
        return None

    def _extract_from_slf(self, name: str) -> bytes | None:
        # O(1) via the prebuilt basename index (see __init__). Lock only the
        # seek+read — SlfFS shares one file handle, so concurrent reads race.
        hit = self._slf_index.get(name.lower())
        if hit is None:
            return None
        fs, path = hit
        with self._io_lock:
            return fs.readbytes(path)

    def get(self, name: str) -> list[tuple[Image.Image, int, int]]:
        key = name.lower()
        if key in self.cache:
            return self.cache[key]
        data: bytes | None = None
        loose = self._find_loose(name)
        if loose:
            data = loose.read_bytes()
        else:
            data = self._extract_from_slf(name)
        if data is None:
            self.cache[key] = []
            self.misses.append(name)
            return []
        try:
            if not is_8bit_sti(io.BytesIO(data)):
                self.cache[key] = []
                return []
            imgs = load_8bit_sti(io.BytesIO(data))
            frames = []
            for sub in imgs.images:
                p = sub.image
                if p.mode != "P":
                    p = p.convert("P")
                indices = p.tobytes()
                # Fast alpha: C-level byte-table map (index 0 → transparent),
                # not a per-pixel Python loop. Identical result.
                alpha = indices.translate(_STI_ALPHA_TABLE)
                rgba = p.convert("RGBA")
                alpha_img = Image.frombytes("L", p.size, alpha)
                rgba.putalpha(alpha_img)
                ox, oy = sub.offsets[0], sub.offsets[1]
                if ox > 32767:
                    ox -= 65536
                if oy > 32767:
                    oy -= 65536
                frames.append((rgba, ox, oy))
            self.cache[key] = frames
            return frames
        except Exception as e:
            print(f"  [warn] failed to parse {name}: {e}", file=sys.stderr)
            self.cache[key] = []
            return []


class IsoRenderer:
    def __init__(self, dat_path: Path, xml_path: Path, tileset_index: int,
                 ring: int = 5,
                 bg_color: tuple[int, int, int, int] = (60, 50, 40, 255),
                 parsed: dict | None = None,
                 loose_dirs: list[Path] | None = None,
                 slf_paths: list[Path] | None = None):
        """Iso renderer for a single sector.

        `parsed` (optional): a pre-parsed dict (from parse_dat_file).
        Pass this to AVOID re-parsing the .dat on every render — the
        MapForge session model uses this so edits don't pay the parse
        cost. When None, the renderer parses dat_path itself.

        `loose_dirs` / `slf_paths` (optional): explicit tileset asset
        search roots — loose `<tileset>/<file>` directories and
        `Tilesets.slf` archives. The CALLER is responsible for deriving
        these from the active install (this module ships no hardcoded
        install paths). When omitted, `_auto_asset_paths()` returns
        empty lists, so the renderer will run but find no tile graphics
        — callers that want a populated render MUST pass these.
        """
        self.dat_path = dat_path
        self.xml_path = xml_path
        self.tileset_index = tileset_index
        self.ring = ring
        self.bg_color = bg_color

        self.parsed = parsed if parsed is not None else parse_dat_file(dat_path)
        self.cols = self.parsed["cols"]
        self.rows = self.parsed["rows"]
        self.slot_map = load_tileset_xml(xml_path, tileset_index)

        if loose_dirs is None and slf_paths is None:
            loose_dirs, slf_paths = self._auto_asset_paths()
        else:
            loose_dirs = loose_dirs or []
            slf_paths = slf_paths or []
        self.sti = StiCache(tileset_index, loose_dirs=loose_dirs,
                            slf_paths=slf_paths)

        # Canvas state (set per render call so this object is reusable).
        self._cv: Image.Image | None = None
        self._ix_min = 0
        self._iy_min = 0

    def _auto_asset_paths(self) -> tuple[list[Path], list[Path]]:
        """Last-resort asset-root resolution when the caller passed no
        explicit `loose_dirs`/`slf_paths`. This module is install-path
        agnostic by design — there are no hardcoded game installs here —
        so there is nothing to auto-discover. Returns empty lists; the
        caller (e.g. routes/mapforge.py) derives tileset roots from the
        active install and passes them in explicitly."""
        return [], []

    # --- iso math ---------------------------------------------------------
    def _tile_to_pix_raw(self, x: int, y: int) -> tuple[int, int]:
        return ((x - y) * TILE_HW, (x + y) * TILE_HH)

    def _tile_to_pix(self, x: int, y: int) -> tuple[int, int]:
        return ((x - y) * TILE_HW - self._ix_min,
                (x + y) * TILE_HH - self._iy_min)

    # --- public API -------------------------------------------------------
    def render(self,
               room_id: int | None = None,
               bbox: tuple[int, int, int, int] | None = None,
               highlight_room: bool = True,
               skip_layers: set[str] | None = None) -> Image.Image:
        """Render `room_id` (with surrounding `ring`-tile border) or a
        custom `bbox` (x0,y0,x1,y1 in tile coords). If both are None,
        renders the whole sector.

        `skip_layers` is a set of layer names to omit from the render
        (e.g. {"roofs", "onroofs"} for a wall-only inspection view).
        Valid names: "land", "objs", "shadows", "structs", "roofs",
        "onroofs". Skipping is applied AFTER the iso-row pass for that
        layer, so the engine's other ordering rules still hold for the
        layers that ARE drawn."""
        skip = skip_layers or set()
        rx0, ry0, rx1, ry1, highlight = self._resolve_region(room_id, bbox)
        if not highlight_room:
            highlight = set()

        # Canvas size: iso bbox of the tile rect + sprite-overhang margins.
        # Tall walls/roofs extend up by ~80px; some trees more. Land tiles
        # below the south edge are clipped if iy_max margin is too small.
        corners = [self._tile_to_pix_raw(x, y)
                   for x in (rx0, rx1) for y in (ry0, ry1)]
        ix_min = min(p[0] for p in corners) - 80
        ix_max = max(p[0] for p in corners) + 80
        iy_min = min(p[1] for p in corners) - 200
        iy_max = max(p[1] for p in corners) + 60
        cw, ch = ix_max - ix_min, iy_max - iy_min

        self._cv = Image.new("RGBA", (cw, ch), self.bg_color)
        self._ix_min = ix_min
        self._iy_min = iy_min

        if highlight:
            self._draw_room_highlight(highlight, rx0, ry0, rx1, ry1)

        # Iso row groups: tiles with same x+y share one screen-Y row.
        tiles_in_region = [(x, y) for y in range(ry0, ry1 + 1)
                           for x in range(rx0, rx1 + 1)]
        rows_by_xy: dict[int, list[tuple[int, int]]] = {}
        for tx, ty in tiles_in_region:
            rows_by_xy.setdefault(tx + ty, []).append((tx, ty))
        for k in rows_by_xy:
            rows_by_xy[k].sort(key=lambda c: c[0] - c[1])
        ordered_xy = sorted(rows_by_xy)

        # PASS 1 / 2 / 3: LAND, OBJECTS, SHADOWS - each is its own
        # whole-region pass in the engine. Iso-row order over the region.
        if "land" not in skip:
            for xy in ordered_xy:
                for tx, ty in rows_by_xy[xy]:
                    self._draw_tile_layer(tx, ty, "land", shadow=False)
        if "objs" not in skip:
            for xy in ordered_xy:
                for tx, ty in rows_by_xy[xy]:
                    self._draw_tile_layer(tx, ty, "objs", shadow=False)
        if "shadows" not in skip:
            for xy in ordered_xy:
                for tx, ty in rows_by_xy[xy]:
                    self._draw_tile_layer(tx, ty, "shadows", shadow=True)

        # PASS 4: STRUCT + ROOF + ONROOF grouped (engine passes 4 levels
        # to one RenderTiles call). For each iso row, level-major within
        # the row: all structs, then all roofs, then all onroofs across
        # the row's tiles. Next row.
        layers_4 = tuple(l for l in ("structs", "roofs", "onroofs")
                         if l not in skip)
        if layers_4:
            for xy in ordered_xy:
                row = rows_by_xy[xy]
                for layer in layers_4:
                    for tx, ty in row:
                        self._draw_tile_layer(tx, ty, layer, shadow=False)

        return self._cv

    # --- internals --------------------------------------------------------
    def _resolve_region(self, room_id, bbox):
        if room_id is not None:
            tiles = [(g % self.cols, g // self.cols)
                     for g, r in enumerate(self.parsed["rooms"])
                     if r == room_id]
            if not tiles:
                raise ValueError(
                    f"Room {room_id} not found in {self.dat_path.name}")
            xs = [t[0] for t in tiles]
            ys = [t[1] for t in tiles]
            rx0 = max(0, min(xs) - self.ring)
            ry0 = max(0, min(ys) - self.ring)
            rx1 = min(self.cols - 1, max(xs) + self.ring)
            ry1 = min(self.rows - 1, max(ys) + self.ring)
            return rx0, ry0, rx1, ry1, set(tiles)
        if bbox is not None:
            return bbox[0], bbox[1], bbox[2], bbox[3], set()
        return 0, 0, self.cols - 1, self.rows - 1, set()

    def _draw_room_highlight(self, room_tiles, rx0, ry0, rx1, ry1):
        d = ImageDraw.Draw(self._cv)
        for tx, ty in room_tiles:
            if not (rx0 <= tx <= rx1 and ry0 <= ty <= ry1):
                continue
            px, py = self._tile_to_pix(tx, ty)
            diamond = [(px, py - TILE_H),
                       (px + TILE_HW, py - TILE_HH),
                       (px, py),
                       (px - TILE_HW, py - TILE_HH)]
            d.polygon(diamond, fill=(60, 120, 60, 70),
                      outline=(100, 200, 100, 150))

    def _draw_tile_layer(self, tx: int, ty: int, layer: str, shadow: bool):
        gn = ty * self.cols + tx
        y_lift = LAYER_Y_LIFT.get(layer, 0)
        for slot, sub in self.parsed[layer][gn]:
            self._composite(slot, sub, tx, ty, shadow, y_lift=y_lift)

    def _composite(self, slot: int, sub: int, tx: int, ty: int,
                   shadow: bool, y_lift: int = 0):
        name = self.slot_map.get(slot)
        if not name:
            return
        frames = self.sti.get(name)
        if not frames:
            return
        # Stored sub-index is 1-BASED (engine tiledef.cpp:1018-1024:
        # "Tile database is zero-based, Type indecies are 1-based!"
        # *pusTileIndex = usSubIndex + gTileTypeStartIndex[type] - 1;
        # So .dat sub=14 means STI frame 13.
        frame_idx = sub - 1
        if frame_idx < 0 or frame_idx >= len(frames):
            return
        pil, ox, oy = frames[frame_idx]
        px, py = self._tile_to_pix(tx, ty)
        paste_x = px + ox
        # y_lift is the engine's per-layer Y adjustment (e.g., roofs get
        # -= WALL_HEIGHT to sit on top of walls).
        paste_y = py + oy - y_lift
        if shadow:
            # Darken-blend: build a 50%-alpha black mask shaped like the
            # shadow sprite and alpha-composite it onto the canvas.
            # NOT an opaque paste of a black sprite (which would obliterate
            # ground textures).
            alpha = pil.split()[-1]
            half = alpha.point(lambda v: v // 2)
            dark = Image.new("RGBA", pil.size, (0, 0, 0, 0))
            dark.putalpha(half)
            self._cv.alpha_composite(dark, (paste_x, paste_y))
        else:
            # STI alpha is binary (0 or 255); paste(mask=image) is correct.
            self._cv.paste(pil, (paste_x, paste_y), pil)


def add_title(canvas: Image.Image, text: str) -> None:
    try:
        font = ImageFont.truetype(r"C:\Windows\Fonts\consola.ttf", 14)
    except Exception:
        font = ImageFont.load_default()
    d = ImageDraw.Draw(canvas)
    # Drop-shadow halo for readability over any background.
    for dx in (-1, 0, 1):
        for dy in (-1, 0, 1):
            if dx == 0 and dy == 0:
                continue
            d.text((10 + dx, 10 + dy), text,
                   fill=(0, 0, 0, 255), font=font)
    d.text((10, 10), text, fill=(240, 240, 240, 255), font=font)


def main():
    ap = argparse.ArgumentParser(
        description="Engine-faithful iso renderer for JA2 .dat sectors."
    )
    ap.add_argument("--dat", required=True, help="Path to .dat sector file")
    ap.add_argument("--xml", required=True, help="Path to Ja2Set.dat.xml")
    ap.add_argument("--tileset", type=int, required=True,
                    help="Tileset index (e.g. 23 for vanilla Drassen, "
                         "71 for a modded Akatzena)")
    ap.add_argument("--room", type=int, default=None,
                    help="Frame around this room ID (with --ring border)")
    ap.add_argument("--bbox", default=None,
                    help="Tile bbox 'x0,y0,x1,y1' (overrides --room)")
    ap.add_argument("--ring", type=int, default=5,
                    help="Tile border around the room (default 5)")
    ap.add_argument("--full", action="store_true",
                    help="Render the full sector (overrides --room/--bbox)")
    ap.add_argument("--no-highlight", action="store_true",
                    help="Don't tint the targeted room tiles green")
    ap.add_argument("--no-open", action="store_true",
                    help="Don't auto-open the PNG after writing")
    ap.add_argument("--skip-layers", default="",
                    help="Comma-separated layer names to skip "
                         "(e.g. 'roofs,onroofs' for a wall-only view)")
    ap.add_argument("--scale", type=int, default=1,
                    help="Integer upscale factor with NEAREST (default 1)")
    ap.add_argument("--title", default=None,
                    help="Override the title overlay text")
    ap.add_argument("--out", required=True, help="Output PNG path")
    args = ap.parse_args()

    bbox = None
    room = args.room
    if args.full:
        room = None
        bbox = None
    elif args.bbox:
        bbox = tuple(int(v) for v in args.bbox.split(","))
        room = None

    renderer = IsoRenderer(Path(args.dat), Path(args.xml),
                           args.tileset, ring=args.ring)
    print(f"Tileset {args.tileset}: {len(renderer.slot_map)} slots defined "
          f"(with tile-0 inheritance)")

    skip_layers = set(s.strip() for s in args.skip_layers.split(",") if s.strip())
    canvas = renderer.render(room_id=room, bbox=bbox,
                             highlight_room=not args.no_highlight,
                             skip_layers=skip_layers)

    title = args.title or (
        f"{Path(args.dat).name} ts={args.tileset}"
        + (f" room={room}" if room is not None else "")
        + (f" bbox={args.bbox}" if bbox else "")
        + (f" skip={','.join(sorted(skip_layers))}" if skip_layers else "")
    )
    add_title(canvas, title)

    if args.scale > 1:
        canvas = canvas.resize(
            (canvas.size[0] * args.scale, canvas.size[1] * args.scale),
            Image.NEAREST,
        )

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(out)
    print(f"Wrote: {out}  ({canvas.size[0]}x{canvas.size[1]})")

    if renderer.sti.misses:
        miss_set = sorted(set(renderer.sti.misses))
        print(f"  [warn] {len(miss_set)} STI(s) not found: "
              f"{', '.join(miss_set[:8])}"
              + ("..." if len(miss_set) > 8 else ""))

    if not args.no_open:
        subprocess.Popen(["cmd", "/c", "start", "", str(out)], shell=False)


if __name__ == "__main__":
    main()
