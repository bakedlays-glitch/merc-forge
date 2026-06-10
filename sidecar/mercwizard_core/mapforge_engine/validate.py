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

# parsed-dict plural layer name -> n_per_tile count key
_LAYER_TO_COUNT_KEY = {
    "land": "land",
    "objs": "obj",
    "structs": "struct",
    "shadows": "shadow",
    "roofs": "roof",
    "onroofs": "onroof",
}


def validate_parsed(parsed: Dict[str, Any]) -> List[Finding]:
    """Validate a parsed sector dict. Returns findings ordered
    error -> warn -> info (within each, in check order)."""
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
        warns.append(Finding(
            SEVERITY_WARN, "NO_EDGEPOINTS",
            "No edge points — arriving mercs have no deployment tiles. The "
            "engine needs N/S/E/W edge entry data to place squads when the "
            "player walks into the sector.",
        ))

    # ── 5. High object-count tiles (WARN) — the "Object Count" save bug ─
    obj_counts = n_per_tile.get("obj") or []
    hot = [i for i, c in enumerate(obj_counts) if c >= 4]
    if hot:
        warns.append(Finding(
            SEVERITY_WARN, "HIGH_OBJECT_COUNT",
            f"{len(hot)} tile(s) carry >=4 object-layer entries. Dense roads "
            f"and cliffs are the classic trigger for the editor's 'Object "
            f"Count' save corruption — verify these tiles save cleanly.",
            tiles=hot[:_TILE_SAMPLE_CAP], count=len(hot),
        ))

    # ── 5b. Terrain heights (WARN + INFO) ─────────────────────────────
    # Engine facts (1.13 source, verified 2026-06-10): engine-authored
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

    return errors + warns + infos
