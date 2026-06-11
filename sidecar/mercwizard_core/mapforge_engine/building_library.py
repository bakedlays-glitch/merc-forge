"""Canon building library — verbatim building grafts from real maps.

Replaces the procedural building stamp as MapForge's primary building
placement path. For a given (install, tileset) this module scans every
map of that tileset in the install (loose ``<layer>/Maps/*.dat`` plus
``Maps.slf`` archives, VFS priority order with basename shadowing),
detects buildings as connected room-id clusters, expands each cluster's
bbox to capture the roof/wall overhang ring, and captures EVERY layer
entry in the expanded bbox exactly as authored — land floors, objects,
shadows, walls/doors, roofs, onroofs, including canonical battle damage
in ruined tilesets.

Each captured building is split into:

  * ``tiles``          — the STRUCTURE: land (floors are land-layer
                         entries), wall/door/window/wall-decal structs,
                         roofs, onroofs, and the wall/door drop shadows.
  * ``contents_tiles`` — the CONTENTS: the whole objs layer, furniture
                         structs (OSTRUCT / FULLSTRUCT / ISTRUCT /
                         debris / vehicle families), and the shadows
                         paired with those furniture families.

The struct/shadow split keys on the TileTypeDefines slot families from
``TileEngine/TileDat.h:3230`` (the slot number in a .dat IS the enum
position):

  36-39 FIRSTWALL..FOURTHWALL        → structure
  40-43 FIRSTDOOR..FOURTHDOOR        → structure
  44-47 FIRSTDOORSHADOW..FOURTH…     → structure (shadow layer)
  48    SLANTROOFCEILING             → structure
  51    FOURTHWINDOW                 → structure
  56-59, 113-116 wall decals         → structure (they live on walls)
  everything else on structs         → contents (furniture/debris/…)

Wall drop shadows are stored on the SHADOW layer under the WALL slot
(sub >= 30), so shadow entries whose slot is a wall/door family count
as structure; shadow entries in the furniture-shadow families (24-35,
87, 90-91, 95-96, 99-100, 105-106, 11) ride with the contents so an
"include contents = off" stamp doesn't strand furniture shadows.

The tile lists are shaped to drop straight into the frontend's
``mapClipboard.ClipboardRegion`` (ClipTile = {dx, dy, layers, room,
height}) so ``pasteEdits`` + ``remapRoomIds`` work unmodified.
"""
from __future__ import annotations

import base64
import hashlib
import io
import json
import os
import re
import struct
import time
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

LAYER_NAMES = ("land", "objs", "shadows", "structs", "roofs", "onroofs")

# ─── TileTypeDefines slot families (TileDat.h:3230) ────────────────────
WALL_SLOTS = frozenset(range(36, 40))           # FIRSTWALL..FOURTHWALL
DOOR_SLOTS = frozenset(range(40, 44))           # FIRSTDOOR..FOURTHDOOR
DOOR_SHADOW_SLOTS = frozenset(range(44, 48))    # FIRSTDOORSHADOW..
WINDOW_SLOTS = frozenset({51})                  # FOURTHWINDOW
WALL_DECAL_SLOTS = frozenset({56, 57, 58, 59, 113, 114, 115, 116})
SLANT_ROOF_CEILING_SLOTS = frozenset({48})      # SLANTROOFCEILING

#: structs-layer slots that are part of the building STRUCTURE.
STRUCTURE_STRUCT_SLOTS = frozenset(
    WALL_SLOTS | DOOR_SLOTS | WINDOW_SLOTS
    | WALL_DECAL_SLOTS | SLANT_ROOF_CEILING_SLOTS
)

#: shadows-layer slots that belong to the STRUCTURE (wall drop shadows
#: are saved under the WALL slot at sub>=30; door shadows are 44-47).
STRUCTURE_SHADOW_SLOTS = frozenset(WALL_SLOTS | DOOR_SLOTS | DOOR_SHADOW_SLOTS)

# Detection guards.
MIN_COMPONENT_TILES = 2     # ignore stray 1-tile room markers
MAX_BBOX_SIDE = 45          # skip mega-clusters (cave systems etc.)
MAX_OVERHANG_EXPAND = 4     # ring-expansion cap per side

# Content-layer subdirs, highest VFS priority first (matches
# routes/mapforge.py's _TILESET_LAYERS).
_DATA_LAYERS = ("Data-1.13", "Data-DMK", "Data")


# ─── Tileset families ────────────────────────────────────────────────────
# JA2 town tilesets are per-sector VARIANTS of one art family — e.g.
# #16 "LAWLESS 1 (San Mona-d5)" / #18 "LAWLESS 2 (burnt-c5)" /
# #21 "LAWLESS 3 (burnt-c6)" — so a single-tileset scan often yields only
# the open map's own buildings. The library widens its scan to the whole
# family, grouped by normalized Ja2Set.dat.xml tileset name.
#
# NOTE: no authoritative tileset-family table exists in the corpus work
# (Headless_Compiler/map_corpus has no tileset_family field; its "family"
# mentions are wall-orientation families), so the grouping is this name
# heuristic, verified against the real install's tileset names.

#: Families the suffix-stripper can't derive (different WORD suffixes,
#: not numbers). Keyed on the heuristic-normalized name so the overlay is
#: name-based (portable across installs), not tileset-id-based.
_FAMILY_ALIASES = {
    # #31 CAMBRIA STRIP + #32 CAMBRIA HOMES are per-sector variants of
    # the Cambria town family.
    "CAMBRIA STRIP": "CAMBRIA",
    "CAMBRIA HOMES": "CAMBRIA",
}

#: Trailing sector-list token, e.g. "g2,h2" in "GRUMM g2,h2" or "d13".
_SECTOR_LIST_RE = re.compile(r"[A-P]\d{1,2}(?:[,/\-][A-P]?\d{1,2})*")


def normalize_family_name(name: str) -> str:
    """Normalize a Ja2Set.dat.xml tileset name to its family key.

    "LAWLESS 1 (San Mona-d5)" → "LAWLESS"; "GRUMM g2,h2" → "GRUMM";
    "FARM 2 (ruined walls)" → "FARM"; "LUSH2 (different trees)" → "LUSH"
    (the missing-space case); "DESERT SAM" stays "DESERT SAM" (word
    suffixes are NOT stripped — only trailing numbers / sector lists /
    parenthesized qualifiers). Returns "" for empty/missing names (the
    caller must treat that as "no family")."""
    s = (name or "").strip().strip("\"'")
    # Parenthesized qualifier — even unspaced ("DEAD AIRSTRIP(Drassen-c13)").
    s = re.sub(r"\(.*$", "", s)
    s = s.strip().upper()
    tokens = s.split()
    while len(tokens) > 1:
        t = tokens[-1]
        if t.isdigit() or _SECTOR_LIST_RE.fullmatch(t):
            tokens.pop()
        else:
            break
    s = " ".join(tokens)
    # Missing-space variant number glued onto the word: "LUSH2" → "LUSH".
    # Single digit preceded by a letter only, so "JA25" (the JA2.5
    # tilesets, letter+digit+digit) stays intact. Runs AFTER the token
    # strip so sector lists like "G2,H2" aren't mangled first.
    s = re.sub(r"(?<=[A-Z])\d$", "", s)
    return _FAMILY_ALIASES.get(s, s)


def load_tileset_names(xml_path: Path) -> Dict[int, str]:
    """Tileset index → <Name> text from Ja2Set.dat.xml ("" when the
    block has no Name node). Empty dict on parse failure."""
    try:
        tree = ET.parse(xml_path)
    except (ET.ParseError, OSError):
        return {}
    out: Dict[int, str] = {}
    for ts in tree.getroot().iter("Tileset"):
        try:
            idx = int(ts.get("index", "-1"))
        except ValueError:
            continue
        if idx < 0:
            continue
        out[idx] = (ts.findtext("Name") or "").strip()
    return out


def tileset_family(xml_path: Path, tileset: int) -> List[int]:
    """Sorted tileset ids sharing `tileset`'s name family (incl. itself).
    A tileset with no/empty name is its own family of one."""
    names = load_tileset_names(xml_path)
    fam = normalize_family_name(names.get(tileset, ""))
    if not fam:
        return [tileset]
    members = {i for i, n in names.items() if normalize_family_name(n) == fam}
    members.add(tileset)
    return sorted(members)


# ─── Map-source enumeration ─────────────────────────────────────────────

def list_map_sources(install_root: Path) -> List[Dict[str, Any]]:
    """Enumerate every map the install exposes, VFS priority order, with
    basename shadowing (a loose Data-1.13/Maps/C5.dat hides the C5.DAT
    inside Data/Maps.slf — same rule the engine's VFS applies).

    Returns dicts: {kind: "loose"|"slf", path: str, internal: str|None,
    name: str, mtime_ns: int, size: int}. The (path, mtime, size) triple
    feeds the cache fingerprint, so this is the single source of truth
    for both the scan and its invalidation.
    """
    out: List[Dict[str, Any]] = []
    seen: set[str] = set()
    # Loose maps, layer priority order.
    for layer in _DATA_LAYERS:
        maps_dir = install_root / layer / "Maps"
        if not maps_dir.is_dir():
            continue
        for p in sorted(maps_dir.iterdir()):
            if not p.is_file() or p.suffix.lower() != ".dat":
                continue
            key = p.name.lower()
            if key in seen:
                continue
            seen.add(key)
            try:
                st = p.stat()
            except OSError:
                continue
            out.append({
                "kind": "loose", "path": str(p), "internal": None,
                "name": p.name, "mtime_ns": st.st_mtime_ns, "size": st.st_size,
            })
    # SLF-bundled maps (vanilla packaging: Data/Maps.slf, sectors at root).
    for layer in _DATA_LAYERS:
        layer_root = install_root / layer
        if not layer_root.is_dir():
            continue
        for entry in layer_root.iterdir():
            if not (entry.is_file() and entry.name.lower() == "maps.slf"):
                continue
            try:
                st = entry.stat()
            except OSError:
                continue
            try:
                from ja2py.fileformats.SlfFS import SlfFS
                fs = SlfFS(str(entry))
                members = list(fs.walk.files())
            except Exception:  # noqa: BLE001 — unreadable SLF → skip
                continue
            for internal in members:
                base = os.path.basename(internal)
                if not base.lower().endswith(".dat"):
                    continue
                key = base.lower()
                if key in seen:
                    continue
                seen.add(key)
                out.append({
                    "kind": "slf", "path": str(entry), "internal": internal,
                    "name": base, "mtime_ns": st.st_mtime_ns, "size": 0,
                })
    return out


def fingerprint(install_root: Path, tileset: int, xml_path: Path,
                sources: List[Dict[str, Any]]) -> str:
    """Cache key: build-format version + install + tileset + tileset-XML
    mtime + every map source's (path, internal, mtime, size). `sources`
    spans EVERY map in the install (not just this tileset's), so edits to
    any family-sibling map invalidate the cache too; the XML mtime covers
    tileset renames that change the family grouping."""
    h = hashlib.sha1()
    # Payload-format version — bump when the build output changes shape
    # (famv1 = family-widened scan + source_tileset provenance fields).
    h.update(b"famv1|")
    h.update(str(install_root.resolve()).lower().encode("utf-8", "replace"))
    h.update(f"|ts={tileset}|".encode())
    try:
        h.update(str(xml_path.stat().st_mtime_ns).encode())
    except OSError:
        pass
    for s in sources:
        h.update(f"{s['path']}|{s['internal']}|{s['mtime_ns']}|{s['size']};"
                 .encode("utf-8", "replace"))
    return h.hexdigest()


def _read_source_bytes(src: Dict[str, Any]) -> Optional[bytes]:
    try:
        if src["kind"] == "loose":
            return Path(src["path"]).read_bytes()
        from ja2py.fileformats.SlfFS import SlfFS
        fs = SlfFS(src["path"])
        return fs.readbytes(src["internal"])
    except Exception:  # noqa: BLE001
        return None


def _header_tileset(data: bytes) -> Optional[int]:
    """Cheap header probe (mirrors parse_dat_ext's header read) so we
    only full-parse maps that match the requested tileset."""
    if len(data) < 25:
        return None
    try:
        major = struct.unpack_from("<f", data, 0)[0]
    except struct.error:
        return None
    if not (4.0 <= major <= 9.0):
        return None
    off = 17 if major >= 7.0 else 9
    if len(data) < off + 4:
        return None
    return struct.unpack_from("<I", data, off)[0]


# ─── Town names (SectorNames.xml) ───────────────────────────────────────

def load_sector_names(install_root: Path) -> Dict[str, str]:
    """SectorGrid (e.g. "C5") → explored display name (e.g. "The Den").

    Reads the install's TableData/Map/SectorNames.xml from the highest-
    priority layer that has it. The Wasteland renames towns here, so
    this is the canonical context label source. Returns {} when absent
    (labels fall back to the bare grid)."""
    for layer in _DATA_LAYERS:
        p = install_root / layer / "TableData" / "Map" / "SectorNames.xml"
        if not p.is_file():
            continue
        try:
            tree = ET.parse(p)
        except (ET.ParseError, OSError):
            continue
        out: Dict[str, str] = {}
        for sector in tree.getroot().iter("SECTOR"):
            grid = (sector.findtext("SectorGrid") or "").strip().upper()
            name = (sector.findtext("szExploredName") or "").strip()
            if grid and name:
                out[grid] = name
        if out:
            return out
    return {}


def sector_grid_from_name(map_name: str) -> str:
    """`C5.DAT` → "C5"; `a9_b1.dat` → "A9" (basement maps share the
    surface sector's town context)."""
    stem = Path(map_name).stem.upper()
    return stem.split("_", 1)[0]


# ─── Building detection ─────────────────────────────────────────────────

def _connected_room_components(
    rooms: List[int], rows: int, cols: int,
) -> List[Tuple[List[int], List[int]]]:
    """Connected components (4-neighbor) over tiles with room id > 0,
    REGARDLESS of id — a multi-room building (ids 3+4 touching through
    an interior wall) is one component. Returns (gridnos, room_ids)."""
    seen = bytearray(rows * cols)
    comps: List[Tuple[List[int], List[int]]] = []
    for start in range(rows * cols):
        if seen[start] or rooms[start] <= 0:
            continue
        stack = [start]
        seen[start] = 1
        tiles: List[int] = []
        ids: set[int] = set()
        while stack:
            g = stack.pop()
            tiles.append(g)
            ids.add(rooms[g])
            x, y = g % cols, g // cols
            for nx, ny in ((x - 1, y), (x + 1, y), (x, y - 1), (x, y + 1)):
                if 0 <= nx < cols and 0 <= ny < rows:
                    ng = ny * cols + nx
                    if not seen[ng] and rooms[ng] > 0:
                        seen[ng] = 1
                        stack.append(ng)
        comps.append((tiles, sorted(ids)))
    return comps


def _tile_is_building_fabric(parsed: Dict[str, Any], g: int) -> bool:
    """True when the tile carries entries that belong to a building's
    shell — used by the bbox overhang expansion. Roof entries extend
    past the room tiles (the W col + N row hold walls but no room id;
    roofs/onroofs can overhang further), and wall drop shadows live on
    the shadows layer under wall/door slots."""
    if parsed["roofs"][g] or parsed["onroofs"][g]:
        return True
    for slot, _sub in parsed["structs"][g]:
        if slot in WALL_SLOTS or slot in DOOR_SLOTS:
            return True
    for slot, _sub in parsed["shadows"][g]:
        if slot in STRUCTURE_SHADOW_SLOTS:
            return True
    return False


def _expand_bbox(parsed: Dict[str, Any], x0: int, y0: int,
                 x1: int, y1: int) -> Tuple[int, int, int, int]:
    """Grow the room-cluster bbox outward while the next ring still
    carries building fabric (walls / roofs / wall shadows). Captures the
    vanilla pattern where the N wall row + W wall col sit OUTSIDE the
    roomed tiles and the roof overhang extends past them. Capped at
    MAX_OVERHANG_EXPAND rings per side so two adjacent buildings can't
    chain-merge through shared fabric."""
    rows, cols = parsed["rows"], parsed["cols"]

    def row_has_fabric(y: int, xa: int, xb: int) -> bool:
        if not (0 <= y < rows):
            return False
        return any(_tile_is_building_fabric(parsed, y * cols + x)
                   for x in range(max(0, xa), min(cols - 1, xb) + 1))

    def col_has_fabric(x: int, ya: int, yb: int) -> bool:
        if not (0 <= x < cols):
            return False
        return any(_tile_is_building_fabric(parsed, y * cols + x)
                   for y in range(max(0, ya), min(rows - 1, yb) + 1))

    for side in range(4):  # N, W, S, E — each gets its own budget
        for _ in range(MAX_OVERHANG_EXPAND):
            if side == 0 and row_has_fabric(y0 - 1, x0, x1):
                y0 -= 1
            elif side == 1 and col_has_fabric(x0 - 1, y0, y1):
                x0 -= 1
            elif side == 2 and row_has_fabric(y1 + 1, x0, x1):
                y1 += 1
            elif side == 3 and col_has_fabric(x1 + 1, y0, y1):
                x1 += 1
            else:
                break
    return x0, y0, x1, y1


def _capture_building(
    parsed: Dict[str, Any],
    bbox: Tuple[int, int, int, int],
    room_ids: List[int],
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Capture (structure_tiles, contents_tiles) for the expanded bbox.

    Verbatim per-tile entries split by slot family. Room ids are
    NORMALIZED to 1..N (component order) so identical buildings from
    different maps dedupe; ids from a NEIGHBORING building that leak
    into the expanded ring are dropped to 0 (stamping must never adopt
    someone else's room)."""
    x0, y0, x1, y1 = bbox
    cols = parsed["cols"]
    room_remap = {rid: i + 1 for i, rid in enumerate(room_ids)}
    structure: List[Dict[str, Any]] = []
    contents: List[Dict[str, Any]] = []
    for y in range(y0, y1 + 1):
        for x in range(x0, x1 + 1):
            g = y * cols + x
            s_layers = {ln: [] for ln in LAYER_NAMES}
            c_layers = {ln: [] for ln in LAYER_NAMES}
            # LAND (incl. floors) → structure, verbatim.
            s_layers["land"] = [[s, u] for s, u in parsed["land"][g]]
            # OBJS → contents, verbatim.
            c_layers["objs"] = [[s, u] for s, u in parsed["objs"][g]]
            # STRUCTS → split by slot family.
            for slot, sub in parsed["structs"][g]:
                (s_layers if slot in STRUCTURE_STRUCT_SLOTS
                 else c_layers)["structs"].append([slot, sub])
            # SHADOWS → wall/door drop shadows are structure; furniture
            # shadows ride with the contents.
            for slot, sub in parsed["shadows"][g]:
                (s_layers if slot in STRUCTURE_SHADOW_SLOTS
                 else c_layers)["shadows"].append([slot, sub])
            # ROOFS + ONROOFS → structure, verbatim.
            s_layers["roofs"] = [[s, u] for s, u in parsed["roofs"][g]]
            s_layers["onroofs"] = [[s, u] for s, u in parsed["onroofs"][g]]
            room = room_remap.get(parsed["rooms"][g], 0)
            structure.append({
                "dx": x - x0, "dy": y - y0,
                "layers": s_layers, "room": room, "height": 0,
            })
            if any(c_layers[ln] for ln in LAYER_NAMES):
                contents.append({
                    "dx": x - x0, "dy": y - y0,
                    "layers": c_layers, "room": room, "height": 0,
                })
    return structure, contents


# ─── Function-label heuristics ──────────────────────────────────────────
# Best-effort: match keywords against the tileset XML's STI filenames
# for the building's CONTENTS slots. Vanilla filenames are mostly
# generic (furn_6.sti, lawless.sti) so most buildings fall back to
# "Building"; the keywords fire on tilesets whose authors named their
# art (bed/bar/shelf/crate/…).

_FUNCTION_RULES: List[Tuple[str, Tuple[str, ...]]] = [
    # (label, keywords). First rule with a hit (priority order) wins.
    ("Hospital", ("hosp", "medic", "surg")),
    ("Bar", ("barcounter", "bar_", "_bar", "saloon", "tavern", "pub")),
    ("Store/Warehouse", ("shelf", "shelv", "crate", "rack", "stock",
                         "wareh", "store")),
    ("Office", ("desk", "office", "file")),
    ("Kitchen", ("stove", "oven", "fridge", "kitchen")),
    ("House", ("bed", "bunk", "cot_", "couch", "sofa")),
]


def _function_label(
    contents_tiles: List[Dict[str, Any]],
    slot_filenames: Dict[int, str],
) -> str:
    names: set[str] = set()
    for t in contents_tiles:
        for ln in ("structs", "objs"):
            for slot, _sub in t["layers"][ln]:
                fn = slot_filenames.get(slot)
                if fn:
                    names.add(fn.lower())
    blob = " ".join(sorted(names))
    for label, keywords in _FUNCTION_RULES:
        for kw in keywords:
            if kw in blob:
                return label
    return "Building"


def compose_label(function: str, sector: str, town: str,
                  w: int, h: int, room_count: int) -> str:
    where = f"{sector} ({town})" if town and town.upper() != sector else sector
    rooms = f"{room_count} room{'s' if room_count != 1 else ''}"
    return f"{function} — {where} · {w}×{h} · {rooms}"


# ─── Thumbnails ─────────────────────────────────────────────────────────

THUMB_MAX_W = 168
THUMB_MAX_H = 126


def _thumb_b64(canvas) -> str:
    """Downscale a rendered building canvas to thumbnail size → PNG b64."""
    w, h = canvas.size
    scale = min(THUMB_MAX_W / max(1, w), THUMB_MAX_H / max(1, h), 1.0)
    if scale < 1.0:
        canvas = canvas.resize(
            (max(1, int(w * scale)), max(1, int(h * scale))))
    buf = io.BytesIO()
    canvas.convert("RGB").save(buf, format="PNG", optimize=True)
    return base64.b64encode(buf.getvalue()).decode("ascii")


# ─── Library build ──────────────────────────────────────────────────────

def _dedupe_key(structure: List[Dict[str, Any]],
                contents: List[Dict[str, Any]],
                w: int, h: int) -> str:
    """Identity of a building = its normalized tile data (rooms already
    renumbered 1..N), independent of where on which map it sits."""
    payload = json.dumps({"w": w, "h": h, "s": structure, "c": contents},
                         separators=(",", ":"), sort_keys=True)
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()


def _slot_requirements(structure: List[Dict[str, Any]],
                       contents: List[Dict[str, Any]]) -> Dict[int, int]:
    """Every slot a captured building references → the highest sub it
    uses on any layer. Drives the family slot-compatibility guard."""
    req: Dict[int, int] = {}
    for tiles in (structure, contents):
        for t in tiles:
            for ln in LAYER_NAMES:
                for slot, sub in t["layers"][ln]:
                    if sub > req.get(slot, 0):
                        req[slot] = sub
    return req


#: Cap on the per-build excluded-building note list (diagnostics only).
_MAX_EXCLUDED_NOTES = 20


def build_library(
    xml_path: Path,
    tileset: int,
    install_root: Path,
    loose_dirs: List[Path],
    slf_paths: List[Path],
    sources: Optional[List[Dict[str, Any]]] = None,
    thumbs: bool = True,
    progress: Optional[Callable[[str], None]] = None,
    frame_count_fn: Optional[Callable[[int], int]] = None,
) -> Dict[str, Any]:
    """Scan every map of `tileset`'s TILESET FAMILY in the install and
    extract its buildings. Returns the library payload (entries + scan
    stats + family info).

    Family widening: town tilesets are per-sector variants (LAWLESS 1/2/3,
    GRUMM g1/g2, …) sharing one slot layout, so buildings from sibling
    tilesets graft cleanly — they render/stamp with the TARGET tileset's
    art for the same slots (the intended reskin). Each sibling building
    passes a SLOT-COMPATIBILITY GUARD first: every (slot, sub) it uses
    must resolve to a registered STI in the target tileset's slot map
    (tile-0 inheritance included) with at least that many sub-frames.
    Incompatible siblings are excluded and counted instead of shipping
    invisible/garbled grafts.

    `loose_dirs` / `slf_paths` are the TILESET asset roots (for thumbnail
    rendering + the guard's frame counts) — derived by the caller from
    the active install, same contract as IsoRenderer. `frame_count_fn`
    overrides the StiCache-backed per-slot frame counter (tests)."""
    from .iso_renderer import IsoRenderer, StiCache, load_tileset_xml
    from .parse_dat_ext import parse_dat_full, DatParseError

    t0 = time.time()
    if sources is None:
        sources = list_map_sources(install_root)
    sector_names = load_sector_names(install_root)
    slot_filenames = load_tileset_xml(xml_path, tileset)
    tileset_names = load_tileset_names(xml_path)
    family_ids = tileset_family(xml_path, tileset)
    family_set = set(family_ids)

    entries: List[Dict[str, Any]] = []
    by_key: Dict[str, Dict[str, Any]] = {}
    scanned = 0
    matched_by_tileset: Dict[int, int] = {}
    skipped_clusters = 0
    excluded_incompatible = 0
    excluded_notes: List[Dict[str, Any]] = []
    shared_sti: Optional[StiCache] = None
    renderer: Optional[IsoRenderer] = None

    def _ensure_sti() -> StiCache:
        nonlocal shared_sti
        if shared_sti is None:
            shared_sti = StiCache(tileset, loose_dirs=loose_dirs,
                                  slf_paths=slf_paths)
        return shared_sti

    # Per-slot frame counts of the TARGET tileset (lazy — only decodes an
    # STI when a sibling building actually references the slot). Same
    # machinery as run_generator's frame_count wiring.
    _fc_cache: Dict[int, int] = {}

    def _frame_count(slot: int) -> int:
        if frame_count_fn is not None:
            return frame_count_fn(slot)
        if slot not in _fc_cache:
            nm = slot_filenames.get(slot)
            try:
                _fc_cache[slot] = len(_ensure_sti().get(nm)) if nm else 0
            except Exception:  # noqa: BLE001
                _fc_cache[slot] = 0
        return _fc_cache[slot]

    def _compat_missing(req: Dict[int, int]) -> List[str]:
        """Slots/subs the TARGET tileset can't render. Empty = graftable."""
        missing: List[str] = []
        for slot, max_sub in sorted(req.items()):
            nm = slot_filenames.get(slot)
            if not nm:
                missing.append(f"slot {slot}: not registered in target")
                continue
            fc = _frame_count(slot)
            if fc < max_sub:
                missing.append(
                    f"slot {slot} ({nm}): needs sub {max_sub}, "
                    f"target has {fc} frame(s)")
        return missing

    # Collect every family map ONE read/parse each, then process the
    # TARGET tileset's maps first so cross-tileset dedupe keeps target
    # provenance (a building present both at home and in a sibling map
    # is canon for the target).
    candidates: List[Tuple[Dict[str, Any], Dict[str, Any]]] = []
    for src in sources:
        data = _read_source_bytes(src)
        if data is None:
            continue
        scanned += 1
        if _header_tileset(data) not in family_set:
            continue
        try:
            parsed = parse_dat_full(data, src["name"])
        except DatParseError:
            continue
        if parsed["tileset"] not in family_set:
            continue
        candidates.append((src, parsed))
    candidates.sort(key=lambda c: c[1]["tileset"] != tileset)

    for src, parsed in candidates:
        map_ts = parsed["tileset"]
        matched_by_tileset[map_ts] = matched_by_tileset.get(map_ts, 0) + 1
        if progress:
            progress(f"scanning {src['name']}")
        rows, cols = parsed["rows"], parsed["cols"]
        comps = _connected_room_components(parsed["rooms"], rows, cols)
        if not comps:
            continue
        # One reusable renderer per build; swap the parsed dict per map.
        # Created with the TARGET tileset's slot map + StiCache, so
        # sibling-map thumbnails render in the target's art — exactly
        # what stamping will produce.
        if thumbs and renderer is None:
            renderer = IsoRenderer(
                Path(src["name"]), xml_path, tileset,
                parsed=parsed, loose_dirs=[], slf_paths=[],
            )
            renderer.sti = _ensure_sti()
        if renderer is not None:
            renderer.parsed = parsed
            renderer.cols = cols
            renderer.rows = rows

        sector = sector_grid_from_name(src["name"])
        town = sector_names.get(sector, "")
        for tiles, room_ids in comps:
            if len(tiles) < MIN_COMPONENT_TILES:
                skipped_clusters += 1
                continue
            xs = [g % cols for g in tiles]
            ys = [g // cols for g in tiles]
            bbox = _expand_bbox(parsed, min(xs), min(ys), max(xs), max(ys))
            x0, y0, x1, y1 = bbox
            w, h = x1 - x0 + 1, y1 - y0 + 1
            if w > MAX_BBOX_SIDE or h > MAX_BBOX_SIDE:
                skipped_clusters += 1
                continue
            structure, contents = _capture_building(parsed, bbox, room_ids)
            # Require some actual building fabric — a roomed patch with
            # no walls and no roof is an exit-grid / marker region.
            has_fabric = any(
                t["layers"]["roofs"] or t["layers"]["structs"]
                for t in structure
            )
            if not has_fabric:
                skipped_clusters += 1
                continue
            key = _dedupe_key(structure, contents, w, h)
            if key in by_key:
                by_key[key]["seen_in"] += 1
                continue
            # SLOT-COMPATIBILITY GUARD for family siblings: a graft
            # renders with the TARGET's art for the same slots, so every
            # (slot, sub) must exist there. The target's own buildings
            # are verbatim canon — no guard needed.
            if map_ts != tileset:
                missing = _compat_missing(
                    _slot_requirements(structure, contents))
                if missing:
                    excluded_incompatible += 1
                    if len(excluded_notes) < _MAX_EXCLUDED_NOTES:
                        excluded_notes.append({
                            "source_map": src["name"],
                            "source_tileset": map_ts,
                            "w": w, "h": h,
                            "missing": missing[:5],
                        })
                    continue
            thumb = ""
            if renderer is not None:
                try:
                    canvas = renderer.render(bbox=bbox, highlight_room=False)
                    thumb = _thumb_b64(canvas)
                except Exception:  # noqa: BLE001 — thumb is best-effort
                    thumb = ""
            function = _function_label(contents, slot_filenames)
            room_count = len(room_ids)
            entry = {
                "id": key[:16],
                "label": compose_label(function, sector, town, w, h,
                                       room_count),
                "function": function,
                "town": town,
                "sector": sector,
                "source_map": src["name"],
                # The stamp/render tileset is the TARGET (grafts adopt
                # its art); provenance lives in source_tileset.
                "tileset": tileset,
                "source_tileset": map_ts,
                "source_tileset_name": tileset_names.get(map_ts, ""),
                "w": w,
                "h": h,
                "room_count": room_count,
                "seen_in": 1,
                "thumb_png_b64": thumb,
                "tiles": structure,
                "contents_tiles": contents,
            }
            by_key[key] = entry
            entries.append(entry)

    # Stable, human-friendly order: by sector then size (big first).
    entries.sort(key=lambda e: (e["sector"], -(e["w"] * e["h"])))
    return {
        "tileset": tileset,
        "install_root": str(install_root),
        "entries": entries,
        "scanned_maps": scanned,
        "matching_maps": sum(matched_by_tileset.values()),
        "matched_by_tileset": matched_by_tileset,
        "family_tilesets": family_ids,
        "family_name": normalize_family_name(
            tileset_names.get(tileset, "")),
        "excluded_incompatible": excluded_incompatible,
        "excluded_notes": excluded_notes,
        "skipped_clusters": skipped_clusters,
        "build_ms": int((time.time() - t0) * 1000),
    }
