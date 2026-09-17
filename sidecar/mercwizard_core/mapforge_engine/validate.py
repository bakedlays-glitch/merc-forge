r"""
validate.py
===========

Pre-flight validator for parsed JA2 1.13 `.dat` sectors (MapForge A4).

`validate_parsed(parsed)` is a PURE function over the dict returned by
`parse_dat_ext.parse_dat_full` — no I/O, no renderer deps — so it is
cheap to call on every edit and trivial to unit-test with synthetic
dicts. It answers two questions the engine can't tell you until you're
already in-game (the most expensive feedback loop in the project):

  1. "Will this map crash / fail to load?"   -> severity "error"
  2. "Will this map be playable?"             -> severity "warn"
  3. advisory / FYI                            -> severity "info"

The crash checks are grounded in the documented JA2 crash traps
(wasteland-map-authoring SKILL § Crash diagnostics):

  * Non-contiguous room IDs  -> Access Violation in InitMap.
  * Layer entry/count desync -> the engine mis-aligns its file reader,
    reads MAPINFO from the wrong offset, ends up with ubMapVersion < 15
    and asserts "Map is less than minimum supported version".
  * ubMapVersion < 15        -> the 99-byte-tail assertion.

The JSD frame-count crash trap (a JSD's usNumberOfStructures not matching
its STI's sub-frame count -> LoadMapTileset assertion) needs renderer /
tileset machinery and so lives in the route layer
(`routes/mapforge.py::_validate_tileset_jsds`), producing the same
`Finding` shape and merged into the same report.

NOTE ON COVERAGE: the parser steps over the variable-size appendix
sections (soldiers / items / schedules) on stock maps, so on a normal
surface map (flags 0x17D) the exit-grid / edge-point / light *counts*
read as None (unreached) and the playability checks below are skipped
with a PARSE_INCOMPLETE note. The crash checks (room IDs, layer desync,
over-cap, high-object-count) operate on the tile region which IS fully
parsed, so they work on every map. The playability checks are most
useful on the flags=0 maps MapForge generates from scratch.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

SEVERITY_ERROR = "error"   # won't load / will crash in-engine
SEVERITY_WARN = "warn"     # loads but likely not playable as-is
SEVERITY_INFO = "info"     # advisory / FYI


@dataclass
class Finding:
    """One validator result. `tiles` holds affected gridnos (capped),
    `count` the total affected (when > the cap), `slot` a tileset slot
    index for JSD findings. All optional."""
    severity: str
    code: str
    message: str
    tiles: List[int] = field(default_factory=list)
    count: Optional[int] = None
    slot: Optional[int] = None


_TILE_SAMPLE_CAP = 50  # don't ship 25k gridnos in a finding

_UNSET = object()  # "caller passed nothing" — distinct from an explicit None

# parsed-dict plural layer name -> n_per_tile count key. Single owner:
# dat_edit_ops (drift between copies would produce exactly the
# count-desync corruption the save guard exists to catch).
from .dat_edit_ops import _LAYER_TO_COUNT_KEY


def validate_parsed(parsed: Dict[str, Any], slot_art: Any = _UNSET) -> List[Finding]:
    """Validate a parsed sector dict. Returns findings ordered
    error -> warn -> info (within each, in check order).

    `slot_art` ({tile-type slot: art filename} for this map's own tileset)
    enables the corner/junction-art check. OMIT it and that check does not run
    at all — every pre-existing caller keeps its exact findings. Pass an
    explicit `None` to run it and get the honest "art identity unavailable"
    advisory instead of silence. Nothing here restricts saving: `save_session`
    gates on internal count consistency, not on findings, and this change does
    not touch that."""
    errors: List[Finding] = []
    warns: List[Finding] = []
    infos: List[Finding] = []

    flags = parsed.get("flags", 0)
    present = parsed.get("appendix_sections_present") or {}
    n_per_tile = parsed.get("n_per_tile") or {}

    # ── 1. Layer entry/count consistency + 4-bit cap (ERROR) ──────────
    desync_tiles: List[int] = []
    overcap_tiles: List[int] = []
    for plural, ck in _LAYER_TO_COUNT_KEY.items():
        entries = parsed.get(plural) or []
        counts = n_per_tile.get(ck) or []
        if len(entries) != len(counts):
            errors.append(Finding(
                SEVERITY_ERROR, "LAYER_ARRAY_LEN_MISMATCH",
                f"Layer '{plural}': entry array ({len(entries)}) != count "
                f"array ({len(counts)}). World-size corruption — the map "
                f"would not parse cleanly in-engine.",
            ))
            continue
        for i, (tile_entries, declared) in enumerate(zip(entries, counts)):
            actual = len(tile_entries)
            if actual != declared:
                desync_tiles.append(i)
            if actual > 15:
                overcap_tiles.append(i)
    if desync_tiles:
        errors.append(Finding(
            SEVERITY_ERROR, "LAYER_COUNT_DESYNC",
            f"{len(desync_tiles)} tile(s) have a layer entry count that "
            f"disagrees with the stored count nibble. The engine would "
            f"mis-align its file reader during LoadWorld, read MAPINFO from "
            f"the wrong offset, and assert 'Map is less than minimum "
            f"supported version' at load.",
            tiles=desync_tiles[:_TILE_SAMPLE_CAP], count=len(desync_tiles),
        ))
    if overcap_tiles:
        errors.append(Finding(
            SEVERITY_ERROR, "LAYER_OVER_CAP",
            f"{len(overcap_tiles)} tile(s) exceed the 4-bit per-layer cap "
            f"(15 entries). The writer truncates the count nibble and the "
            f"engine reads fewer entries than were written.",
            tiles=overcap_tiles[:_TILE_SAMPLE_CAP], count=len(overcap_tiles),
        ))

    # ── 2. Room-ID continuity (ERROR) ─────────────────────────────────
    rooms = parsed.get("rooms") or []
    distinct = sorted({r for r in rooms if r})
    if distinct:
        present_ids = set(distinct)
        gaps = [r for r in range(1, distinct[-1] + 1) if r not in present_ids]
        if gaps:
            shown = ", ".join(str(g) for g in gaps[:20])
            more = "..." if len(gaps) > 20 else ""
            # WARN, not ERROR: many hand-authored / vanilla maps ship with
            # non-contiguous room IDs and load fine. The documented Access
            # Violation in InitMap is specific to bulldozing rooms in the
            # editor and leaving gaps — so flag it, but don't claim a
            # certain crash on every gap (verified against real install
            # maps, e.g. A10.DAT, which carry gaps safely).
            warns.append(Finding(
                SEVERITY_WARN, "ROOM_ID_GAP",
                f"Room IDs are non-contiguous (missing {shown}{more} within "
                f"1..{distinct[-1]}). Safe in many hand-authored maps, but "
                f"after deleting rooms in the editor a gap can trigger an "
                f"Access Violation in InitMap — remap to be continuous "
                f"(1, 2, 3, ...) if you've been bulldozing.",
                count=len(gaps),
            ))

    # ── 2b. Room id shared across detached buildings (WARN) ───────────
    # The engine reveals a roof by ID EQUALITY: RemoveRoomRoof (TileEngine/
    # Render Fun.cpp:263) strips every tile whose gusWorldRoomInfo equals the
    # entered room's id, and gubWorldRoomHidden is a per-id array. One id on
    # two detached buildings pops both roofs when a merc enters either, and
    # the revealed flags persist into saves (The Wasteland C15: 82 buildings
    # on id 1). Component = 4-connected flood over tiles that are
    # roofed OR room-marked -- roofed-only would split a building around an
    # unroofed interior hole (a9 id 21) and false-flag a correct map. WARN,
    # not ERROR: the map loads, it just plays wrong.
    shared = _shared_room_ids(rooms, parsed.get("roofs") or [],
                              parsed.get("rows") or 0, parsed.get("cols") or 0)
    if shared:
        ids_shown = ", ".join(f"{rid} x{n}" for rid, n, _ in shared[:10])
        sample = [g for _, _, gs in shared for g in gs][:_TILE_SAMPLE_CAP]
        warns.append(Finding(
            SEVERITY_WARN, "ROOM_ID_SHARED",
            f"{len(shared)} room id(s) span more than one detached building "
            f"({ids_shown}) -- entering any one of them reveals every roof "
            f"on that id (RemoveRoomRoof matches by id, not adjacency). "
            f"Give each building its own id.",
            tiles=sample, count=len(shared),
        ))

    # ── 3. Map version + edge entry points (when the tail is readable) ─
    # The parser only extracts the tail when flags == 0 (otherwise it sits
    # mid-appendix and can't be located without parsing soldiers/items).
    tail = parsed.get("tail")
    if tail:
        mv = tail.get("ubMapVersion")
        if mv is not None and mv < 15:
            errors.append(Finding(
                SEVERITY_ERROR, "MAPVERSION_TOO_LOW",
                f"MapInfo ubMapVersion = {mv} (< 15). The engine asserts "
                f"'Map is less than minimum supported version' on load. "
                f"Re-save the map through the editor to bump the version.",
            ))
        edges = {
            "North": tail.get("sNorthGridNo"),
            "East": tail.get("sEastGridNo"),
            "South": tail.get("sSouthGridNo"),
            "West": tail.get("sWestGridNo"),
        }
        missing = [name for name, g in edges.items() if not g or g <= 0]
        if missing:
            side = "that edge" if len(missing) == 1 else "those edges"
            warns.append(Finding(
                SEVERITY_WARN, "MISSING_EDGE_ENTRY",
                f"Edge entry point(s) unset: {', '.join(missing)}. Mercs "
                f"arriving from {side} have no landing tile.",
                count=len(missing),
            ))

    # ── 4. Playability: exit grids / edge points (WARN) ───────────────
    # Only conclude "missing" when the flag is absent OR the count is a
    # definite 0. A None count means the parser stopped before reaching
    # the section (stock maps) — surfaced via PARSE_INCOMPLETE instead.
    eg = parsed.get("appendix_exitgrid_count")
    if not present.get("exitgrids") or eg == 0:
        warns.append(Finding(
            SEVERITY_WARN, "NO_EXIT_GRIDS",
            "No exit grids — the player can't leave (or strategically "
            "enter) this sector. A playable sector needs exit grids on its "
            "open edges, each pointing at a destination sector.",
        ))
    ep = parsed.get("appendix_edgepoint_count")
    if not present.get("edgepoints") or ep == 0:
        # INFO, not WARN: when MAP_EDGEPOINTS_SAVED is absent the engine
        # sets fGenerateEdgePoints and AUTO-REGENERATES edgepoints at
        # load (worlddef.cpp:3256-3276 → GenerateMapEdgepoints). The
        # real deployment dependency is the ENTRY POINTS (previous
        # check) + reachability — not stored edgepoints.
        infos.append(Finding(
            SEVERITY_INFO, "NO_EDGEPOINTS",
            "No stored edge points — the engine will auto-generate them "
            "at load from the entry points. Fine as long as the entry "
            "points are set and reachable.",
        ))

    # ── 5. High object-count tiles (WARN) — the "Object Count" save bug ─
    # Threshold matches the ENGINE's own editor: it warns at >10 and
    # REFUSES the save at >15 entries on one tile (worlddef.cpp:1932-47).
    # The old >=4 threshold false-positived on perfectly legal dense
    # vanilla road/cliff tiles.
    obj_counts = n_per_tile.get("obj") or []
    hot = [i for i, c in enumerate(obj_counts) if c > 10]
    if hot:
        warns.append(Finding(
            SEVERITY_WARN, "HIGH_OBJECT_COUNT",
            f"{len(hot)} tile(s) carry more than 10 object-layer entries — "
            f"the in-game editor warns at this density and refuses to save "
            f"at all above 15 entries on one tile.",
            tiles=hot[:_TILE_SAMPLE_CAP], count=len(hot),
        ))

    # ── 5c. Room-ID upper bound (ERROR) ───────────────────────────────
    # Room IDs index gubWorldRoomHidden[MAX_ROOMS] with MAX_ROOMS=65530
    # (Render Fun.h:9, indexed unchecked at Render Fun.cpp:72) — an ID
    # above 65529 is an out-of-bounds global array access in-game.
    over = [i for i, r in enumerate(rooms) if r > 65529]
    if over:
        errors.append(Finding(
            SEVERITY_ERROR, "ROOM_ID_OVER_CAP",
            f"{len(over)} tile(s) have a room ID above 65529 — the engine "
            f"indexes a fixed 65530-entry room array without bounds checks, "
            f"so these read/write out of bounds in-game.",
            tiles=over[:_TILE_SAMPLE_CAP], count=len(over),
        ))

    # ── 5b. Terrain heights (WARN + INFO) ─────────────────────────────
    # Engine facts (1.13 source): engine-authored
    # heights are exclusively multiples of WORLD_CLIFF_HEIGHT=80
    # (worlddef.h:53; edit_sys.cpp raises) — other values load but
    # mis-stack render layers (IGNORE_WORLD_HEIGHT quantizes to 80s,
    # renderworld.cpp:1454). And NO height delta is crossable: pathing
    # hard-blocks any adjacent-tile difference (worlddef.cpp:880,
    # PATHAI.cpp:2011/2815) and 1.13 compiles raised tiles as off-map
    # (GridNoOnWalkableWorldTile). Raised terrain = blocking scenery.
    heights = parsed.get("heights") or []
    nonstd = [i for i, h in enumerate(heights) if h % 80 != 0]
    if nonstd:
        warns.append(Finding(
            SEVERITY_WARN, "NONSTANDARD_HEIGHT",
            f"{len(nonstd)} tile(s) have a terrain height that isn't a "
            f"multiple of 80 (the engine's one cliff-raise unit). They "
            f"load, but render layers quantize to 80s and can visibly "
            f"mis-stack. Use 0/80/160/240.",
            tiles=nonstd[:_TILE_SAMPLE_CAP], count=len(nonstd),
        ))
    raised = [i for i, h in enumerate(heights) if h]
    if raised:
        infos.append(Finding(
            SEVERITY_INFO, "RAISED_TERRAIN",
            f"{len(raised)} tile(s) have raised terrain. Mercs cannot cross "
            f"ANY height difference (no climb mechanism exists for terrain "
            f"— see STATUS.md Phase 3e), so raised areas are route-blocking "
            f"scenery. Also: resaving this map in the in-game Map Editor "
            f"recomputes all heights from cliff-face sprites and will WIPE "
            f"these values unless cliff art backs them.",
            tiles=raised[:_TILE_SAMPLE_CAP], count=len(raised),
        ))

    # ── 6. Advisory presence checks (INFO) ────────────────────────────
    if not present.get("soldiers"):
        infos.append(Finding(
            SEVERITY_INFO, "NO_ENEMIES",
            "No enemy/soldier placements. Combat sectors typically place "
            "~32 enemies; a peaceful or interior sector may legitimately "
            "have none.",
        ))
    lc = parsed.get("appendix_light_count")
    if not present.get("lights") or lc == 0:
        infos.append(Finding(
            SEVERITY_INFO, "NO_LIGHTS",
            "No light sources. Fine for daylight-lit surface sectors; "
            "underground / interior sectors render fully dark without them.",
        ))

    # ── 7. Parse completeness (INFO) ──────────────────────────────────
    stopped = parsed.get("appendix_parse_stopped_at")
    if stopped:
        infos.append(Finding(
            SEVERITY_INFO, "PARSE_INCOMPLETE",
            f"Appendix parse stopped at '{stopped}', so sections after it "
            f"weren't validated (exit grids / edge points / lights on a "
            f"stock map). This is expected for maps that carry soldiers, "
            f"items, or schedules — those are preserved verbatim on save.",
        ))
    elif tail is None and flags != 0:
        infos.append(Finding(
            SEVERITY_INFO, "TAIL_UNREADABLE",
            "The map-info tail (entry points, map version) couldn't be "
            "located because this map has appendix sections. Entry-point "
            "and version checks were skipped.",
        ))

    # ── 9. Engine-LUT SOCKET checks (walls/terrain hard, water advisory) ──
    warns.extend(_socket_wall_findings(parsed))
    warns.extend(_socket_terrain_findings(parsed))
    infos.extend(_socket_water_findings(parsed))
    infos.extend(_socket_cave_findings(parsed))

    # ── 10. Wall/fence CORNER + junction art (never `error` here) ─────
    # `corner_findings` marks the verified art `error` because that is what the
    # generation-commit gate keys on. On an EXISTING map that word would lie:
    # `error` in this module means "won't load / will crash", and a fence that
    # simply stops does neither — hand-authored maps stop fences deliberately
    # (41 of 400 authored install maps carry at least one; spot-checked as
    # decorative stubs and ruined sections, not defects). So they land as
    # `warn` for reporting, and nothing here restricts saving.
    if slot_art is not _UNSET:
        for f in corner_findings(parsed, slot_art):
            if f.severity == SEVERITY_ERROR:
                f.severity = SEVERITY_WARN
            (warns if f.severity == SEVERITY_WARN else infos).append(f)

    return errors + warns + infos



def _shared_room_ids(rooms, roofs, rows: int, cols: int):
    """[(room_id, n_components, sample_gridnos)] for every room id that appears
    in >= 2 components, where a component is a 4-connected flood over tiles
    that are roofed OR room-marked."""
    total = rows * cols
    if total <= 0 or len(rooms) < total:
        return []
    has_roof = [bool(roofs[i]) if i < len(roofs) else False for i in range(total)]
    seen = [False] * total
    owners: Dict[int, List[List[int]]] = {}
    for seed in range(total):
        if seen[seed] or not (rooms[seed] > 0 or has_roof[seed]):
            continue
        stack, comp = [seed], []
        while stack:
            i = stack.pop()
            if seen[i] or not (rooms[i] > 0 or has_roof[i]):
                continue
            seen[i] = True
            comp.append(i)
            y, x = divmod(i, cols)
            if x + 1 < cols: stack.append(i + 1)
            if x > 0:        stack.append(i - 1)
            if y + 1 < rows: stack.append(i + cols)
            if y > 0:        stack.append(i - cols)
        for rid in {rooms[i] for i in comp if rooms[i]}:
            owners.setdefault(rid, []).append(comp)
    out = []
    for rid, comps in sorted(owners.items()):
        if len(comps) > 1:
            out.append((rid, len(comps), [c[0] for c in comps]))
    return out


def _socket_wall_findings(parsed: Dict[str, Any]) -> List[Finding]:
    """Wrong-axis straight walls (newsmooth.cpp BuildWallPiece). Hard rule —
    validated 0.38% FP on authored maps. Skips cave tilesets + corner cells."""
    from mercwizard_core.mapforge import socket_lut as S
    structs = parsed.get("structs")
    if not isinstance(structs, list) or parsed.get("tileset") in S.CAVE_TILESETS:
        return []
    cols, rows = parsed.get("cols", 160), parsed.get("rows", 160)
    bad: List[int] = []
    for gn, entries in enumerate(structs):
        if len(entries) != 1:
            continue
        t, s = int(entries[0][0]), int(entries[0][1])
        if t not in S.WALL_TYPES:
            continue
        cls = S.wall_class(s)
        if cls not in S.STRAIGHT_WALL_CLASSES:
            continue
        ew, ns = S.wall_family_continuation(structs, gn, cols, rows, t, S.CLASS_FAMILY[cls])
        if S.wall_run_axis_mismatch(cls, ew, ns) is not None:
            bad.append(gn)
    if not bad:
        return []
    return [Finding(SEVERITY_WARN, "SOCKET_WALL_AXIS",
                    f"{len(bad)} wall(s) face the wrong way (run-axis contradicts "
                    f"the connector class). Run the 'smooth_walls' generator.",
                    tiles=bad[:_TILE_SAMPLE_CAP], count=len(bad))]


def _socket_terrain_findings(parsed: Dict[str, Any]) -> List[Finding]:
    """Land fringe tiles whose boundary contradicts neighbours (gbSmoothStruct).
    Hard rule — validated 0.23% FP on authored maps."""
    from mercwizard_core.mapforge import socket_lut as S
    land = parsed.get("land")
    if not isinstance(land, list):
        return []
    cols, rows = parsed.get("cols", 160), parsed.get("rows", 160)
    bad: List[int] = []
    for gn, entries in enumerate(land):
        for e in entries:
            t, s = int(e[0]), int(e[1])
            if t not in S.TEXTURE_TYPES or s not in S.TERR_FRINGE_SUBS:
                continue
            code = S.boundary_code(land, gn, cols, rows, t)
            if not S.terrain_sub_is_legal(s, code):
                bad.append(gn)
    if not bad:
        return []
    return [Finding(SEVERITY_WARN, "SOCKET_TERRAIN_FRINGE",
                    f"{len(bad)} terrain fringe tile(s) don't match their texture "
                    f"boundary. Run the 'smooth_terrain' generator.",
                    tiles=bad[:_TILE_SAMPLE_CAP], count=len(bad))]


def _socket_water_findings(parsed: Dict[str, Any]) -> List[Finding]:
    """Water shoreline tiles vs SmoothWaterTerrain. ADVISORY only — shorelines are
    hand-authored (~4.9% deviate), so these are info, not a real defect."""
    from mercwizard_core.mapforge import socket_lut as S
    land = parsed.get("land")
    if not isinstance(land, list):
        return []
    cols, rows = parsed.get("cols", 160), parsed.get("rows", 160)
    bad: List[int] = []
    for gn, entries in enumerate(land):
        for e in entries:
            if int(e[0]) != S.WATER_TYPE or int(e[1]) not in S.WATER_FRINGE_SUBS:
                continue
            if not S.water_sub_is_legal(int(e[1]), S.water_bitvalue(land, gn, cols, rows)):
                bad.append(gn)
    if not bad:
        return []
    return [Finding(SEVERITY_INFO, "SOCKET_WATER_SHORE",
                    f"{len(bad)} water shore tile(s) differ from the auto-shore LUT "
                    f"(often fine — shorelines are hand-authored).",
                    tiles=bad[:_TILE_SAMPLE_CAP], count=len(bad))]


def _socket_cave_findings(parsed: Dict[str, Any]) -> List[Finding]:
    """Cave-wall tiles vs the cave autotiler LUT (newsmooth.cpp CalcNewCavePerimeter
    -> GetCaveTileIndexFromPerimeterValue). ADVISORY only — authored caverns fill
    interiors with full-floor variants (~2.8% deviate), so these are info. ts1 only."""
    from mercwizard_core.mapforge import socket_lut as S
    structs = parsed.get("structs")
    if not isinstance(structs, list) or parsed.get("tileset") != 1:
        return []
    cols, rows = parsed.get("cols", 160), parsed.get("rows", 160)
    bad: List[int] = []
    for gn, entries in enumerate(structs):
        for e in entries:
            if int(e[0]) not in S.CAVE_WALL_TYPES:
                continue
            if not S.cave_sub_is_legal(int(e[1]), S.cave_perimeter(structs, gn, cols, rows)):
                bad.append(gn)
                break
    if not bad:
        return []
    return [Finding(SEVERITY_INFO, "SOCKET_CAVE_WALL",
                    f"{len(bad)} cave-wall tile(s) differ from the auto-perimeter LUT "
                    f"(often fine — cave interiors are hand-filled).",
                    tiles=bad[:_TILE_SAMPLE_CAP], count=len(bad))]


# =====================================================================
# Wall/fence CORNER + junction art
# =====================================================================
# `_socket_wall_findings` above answers "does this straight wall run the way
# its connector class says". It cannot answer "does this rectangle actually
# have corners": a perimeter whose four turns still carry straight-run pieces
# satisfies every socket rule and renders with four open, projecting stubs.
#
# CHANGE-BOTH NOTICE. The geometry below is duplicated from
# `Headless_Compiler/sector_gen/validators.py` (`check_corner_art` and
# friends), for the same reason `socket_lut.py` duplicates
# `engine_socket_rules.py`: the sidecar must not reach into Headless_Compiler's
# package tree, and vice versa. If you change one, change both. The wall LUT
# itself is NOT re-copied here — it is imported from
# `mercwizard_core.mapforge.socket_lut`, which already carries it verbatim.
#
# The rules, the evidence behind them, and the reasons a family is resolved
# from EFFECTIVE ART rather than the numeric tile-type slot are documented once,
# in that Headless_Compiler module's own section header. Summary: fences
# (wirefenc/oldfence) are centreline pieces whose ARMS must be reciprocated by
# the tile they reach; 65-frame wall sheets (build_24a_rust) are lattice-edge
# pieces and no vertex may have degree 1; a run that stops is legal only as a
# facing pair across a gap (the native gate state) or under an explicit
# `allow_openings` allowance. Both rules were measured on the repaired Junktown
# v112 pair: 42 -> 0 fence defects, 10 -> 0 wall defects, gates untouched.

CORNER_ART_FAMILY = {
    "wirefenc": "fence",
    "oldfence": "fence",
    "build_24a_rust": "wall_sheet",
}
CORNER_HARD_ART = frozenset(CORNER_ART_FAMILY)   # only these BLOCK a commit
CORNER_STRUCTURAL_SLOTS = frozenset({36, 37, 38, 39, 86})
CORNER_MAX_GAP = 16

_FENCE_X = ((-1, 0), (1, 0))
_FENCE_Y = ((0, -1), (0, 1))
FENCE_ARMS = {
    1: ((1, 0), (0, -1)), 2: ((1, 0), (0, 1)),
    3: ((-1, 0), (0, 1)), 4: ((-1, 0), (0, -1)),
    10: _FENCE_X, 11: _FENCE_X, 12: _FENCE_X,
    13: _FENCE_Y, 14: _FENCE_Y, 15: _FENCE_Y,
}
_WALL_EW = ((0, 1), (1, 1))
_WALL_NS = ((1, 0), (1, 1))


def _wall_edge_table():
    """sub -> lattice edge, built from the shared socket LUT. Only the straight
    L/R classes and sub 13 (EXTERIOR_BOTTOMEND) have verified geometry; every
    other sub reports as unsupported rather than being guessed onto an axis."""
    from mercwizard_core.mapforge import socket_lut as S
    table = {sub: (_WALL_EW if S.CLASS_FAMILY[cls] == "L" else _WALL_NS)
             for sub, cls in S.WALL_SUB2CLASS.items() if cls in S.CLASS_FAMILY}
    table[13] = _WALL_NS
    return table


def _corner_art_stem(name):
    if not name:
        return None
    return str(name).strip().lower().rsplit(".", 1)[0] or None


def _fence_defects(structs, cols, rows, slot, allow, max_gap):
    tiles = {}
    unknown = set()
    for gn, cell in enumerate(structs):
        for e in cell:
            if int(e[0]) != slot:
                continue
            y, x = divmod(gn, cols)
            rec = tiles.setdefault((x, y), {"arms": set(), "unknown": False, "n": 0})
            rec["n"] += 1
            arms = FENCE_ARMS.get(int(e[1]))
            if arms is None:
                rec["unknown"] = True
                unknown.add(int(e[1]))
            else:
                rec["arms"].update(arms)

    dangling, orientation = set(), set()
    duplicate = {xy for xy, rec in tiles.items() if rec["n"] > 1}
    for (x, y), rec in tiles.items():
        if rec["unknown"] or (x, y) in allow:
            continue
        for (dx, dy) in rec["arms"]:
            nxt = (x + dx, y + dy)
            if nxt in allow:
                continue
            nb = tiles.get(nxt)
            if nb is not None:
                if not nb["unknown"] and (-dx, -dy) not in nb["arms"]:
                    orientation.add((x, y))
                continue
            cx, cy, hit = x + 2 * dx, y + 2 * dy, None
            for _ in range(max_gap):
                if not (0 <= cx < cols and 0 <= cy < rows):
                    break
                if (cx, cy) in tiles:
                    hit = (cx, cy)
                    break
                cx += dx
                cy += dy
            if hit is None or not (tiles[hit]["unknown"]
                                   or (-dx, -dy) in tiles[hit]["arms"]):
                dangling.add((x, y))
    return sorted(dangling), sorted(orientation), sorted(duplicate), sorted(unknown)


def _wall_defects(structs, cols, rows, slot, allow, max_gap):
    edge_table = _wall_edge_table()
    verts, per_tile, unknown = {}, {}, set()
    for gn, cell in enumerate(structs):
        for e in cell:
            if int(e[0]) != slot:
                continue
            y, x = divmod(gn, cols)
            edge = edge_table.get(int(e[1]))
            if edge is None:
                unknown.add(int(e[1]))
                continue
            per_tile.setdefault((x, y), []).append("EW" if edge is _WALL_EW else "NS")
            (ax, ay), (bx, by) = edge
            va, vb = (x + ax, y + ay), (x + bx, y + by)
            verts.setdefault(va, []).append(((x, y), vb))
            verts.setdefault(vb, []).append(((x, y), va))

    # Two pieces share a tile only as the engine's own bottom-corner pair —
    # one EW + one NS (tiledef.cpp CalculateWallOrientationsAtGridNo).
    duplicate = sorted(xy for xy, ax in per_tile.items()
                       if len(ax) > 1 and sorted(ax) != ["EW", "NS"])
    dangling = set()
    for v, owners in verts.items():
        if len(owners) != 1:
            continue
        tile, other = owners[0]
        if any(t in allow for t in ((v[0] - 1, v[1] - 1), (v[0], v[1] - 1),
                                    (v[0] - 1, v[1]), v)):
            continue
        dx, dy = v[0] - other[0], v[1] - other[1]
        cx, cy, hit = v[0] + dx, v[1] + dy, None
        for _ in range(max_gap):
            if not (0 <= cx <= cols and 0 <= cy <= rows):
                break
            if (cx, cy) in verts:
                hit = (cx, cy)
                break
            cx += dx
            cy += dy
        ok = False
        if hit is not None and len(verts[hit]) == 1:
            _ht, ho = verts[hit][0]
            ok = (hit[0] - ho[0], hit[1] - ho[1]) == (-dx, -dy)
        if not ok:
            dangling.add(tile)
    return sorted(dangling), duplicate, sorted(unknown)


_CORNER_REASON = {
    "CORNER_DANGLING": "a run that stops with nothing facing back at it "
                       "(missing corner or cap, or a projecting end)",
    "CORNER_ORIENTATION": "the adjoining tile carries this family but faces "
                          "the wrong way (incompatible corner/run orientation)",
    "CORNER_DUPLICATE": "two pieces of this family stacked on one tile in a "
                        "combination the engine never authors",
}


def corner_findings(parsed: Dict[str, Any], slot_art: Any,
                    scope: Optional[Any] = None,
                    allow_openings: Any = (),
                    hard_art: Any = CORNER_HARD_ART,
                    max_gap: int = CORNER_MAX_GAP,
                    cap: Optional[int] = _TILE_SAMPLE_CAP) -> List[Finding]:
    """Wall/fence junction-art findings, family-resolved from `slot_art`
    ({tile-type slot: art filename} for this map's own tileset).

    `slot_art=None` — art identity could not be resolved: one CORNER_UNSUPPORTED
    info finding, never a silent pass and never a guess from the numeric slot.
    `scope` (iterable of gridnos) limits findings to the tiles an operation
    touched. `allow_openings` (iterable of gridnos) is the explicit semantic
    allowance for deliberate gates/endpoints/damage that are not facing pairs.
    Severity is `error` for art in `hard_art`, `warn` for every other art."""
    structs = parsed.get("structs")
    if not isinstance(structs, list):
        return []
    if slot_art is None:
        return [Finding(SEVERITY_INFO, "CORNER_UNSUPPORTED",
                        "Corner/junction art was not checked: no slot→art map "
                        "was supplied. A numeric tile-type slot is not an art "
                        "identity (tileset 71 puts a wall sheet on slot 88 = "
                        "FIRSTVEHICLE), so no family was inferred.")]

    cols, rows = parsed.get("cols", 160), parsed.get("rows", 160)
    art_by_slot = {int(k): _corner_art_stem(v) for k, v in slot_art.items()}
    allow = {(g % cols, g // cols) for g in allow_openings}
    scope_set = None if scope is None else {int(g) for g in scope}
    out: List[Finding] = []

    def emit(code, severity, family, slot, art, xys):
        gns = [y * cols + x for (x, y) in xys]
        if scope_set is not None:
            gns = [g for g in gns if g in scope_set]
        if not gns:
            return
        shown = ", ".join(f"({g % cols},{g // cols})" for g in gns[:8])
        out.append(Finding(
            severity, code,
            f"{len(gns)} {family} tile(s) on slot {slot} ({art}): "
            f"{_CORNER_REASON[code]}. At {shown}"
            f"{', …' if len(gns) > 8 else ''}.",
            tiles=gns if cap is None else gns[:cap], count=len(gns), slot=slot))

    used = {int(e[0]) for cell in structs for e in cell}
    for slot in sorted(used):
        art = art_by_slot.get(slot)
        family = CORNER_ART_FAMILY.get(art) if art else None
        if family is None:
            if slot in CORNER_STRUCTURAL_SLOTS:
                out.append(Finding(
                    SEVERITY_INFO, "CORNER_UNSUPPORTED",
                    f"Slot {slot} carries {art!r}, which is not a junction "
                    f"family this checker covers — the slot is one the engine "
                    f"natively fills with perimeter art, so its corners were "
                    f"NOT validated.", slot=slot))
            continue
        severity = SEVERITY_ERROR if art in hard_art else SEVERITY_WARN
        if family == "fence":
            dang, orient, dup, unk = _fence_defects(structs, cols, rows, slot,
                                                    allow, max_gap)
        else:
            dang, dup, unk = _wall_defects(structs, cols, rows, slot, allow, max_gap)
            orient = []
        emit("CORNER_DANGLING", severity, family, slot, art, dang)
        emit("CORNER_ORIENTATION", severity, family, slot, art, orient)
        emit("CORNER_DUPLICATE", severity, family, slot, art, dup)
        if unk:
            out.append(Finding(
                SEVERITY_INFO, "CORNER_UNSUPPORTED",
                f"Slot {slot} ({art}, family {family}) uses sub-index(es) "
                f"{unk} with no verified junction geometry — those pieces were "
                f"treated as present-but-unclassified, not validated.",
                slot=slot))
    return out


def validate_generated_edits_before_commit(before_parsed: Dict[str, Any],
                                           after_parsed: Dict[str, Any],
                                           slot_art: Any = None,
                                           scope: Optional[Any] = None,
                                           allow_openings: Any = ()) -> List[Finding]:
    """The generation boundary's gate: blocking corner/junction findings the
    buffered result INTRODUCED, with the ones the user's map already carried
    subtracted out.

    Call it from `routes/mapforge.py::run_generator` with the pre-generation
    parsed dict and the buffered post-generation one, before the transactional
    apply. A non-empty return means the generated edits broke a junction the
    checker can prove: roll the buffer back and report these findings, which
    name the exact gridnos. An empty return says nothing NEW is broken — it is
    NOT a claim that the map is clean, and it deliberately never blocks a plain
    save of a map whose defects were already there.

    Identity is (code, gridno): a defect at the same tile with the same code on
    both sides is pre-existing. Only `error`-severity findings gate — advisory
    findings on unverified art and coverage gaps are reported by
    `validate_parsed`, never used to block."""
    def index(parsed):
        seen = set()
        for f in corner_findings(parsed, slot_art, scope=scope,
                                 allow_openings=allow_openings, cap=None):
            if f.severity == SEVERITY_ERROR:
                seen.update((f.code, g) for g in f.tiles)
        return seen

    pre = index(before_parsed)
    out: List[Finding] = []
    for f in corner_findings(after_parsed, slot_art, scope=scope,
                             allow_openings=allow_openings, cap=None):
        if f.severity != SEVERITY_ERROR:
            continue
        new = [g for g in f.tiles if (f.code, g) not in pre]
        if new:
            out.append(Finding(f.severity, f.code, f.message, tiles=new,
                               count=len(new), slot=f.slot))
    return out
