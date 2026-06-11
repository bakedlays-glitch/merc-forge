"""Tests for the canon building library — verbatim building grafts
extracted from real maps (mercwizard_core/mapforge_engine/
building_library.py + the GET /mapforge/building-library endpoint).

Covers: extraction with overhang bbox expansion, the structure/contents
split (walls/doors/roofs/land vs furniture/objs + the shadow family
split), room-id normalization, cross-map dedupe, label heuristics +
fallback, town context from SectorNames.xml, and endpoint caching.
"""
from __future__ import annotations

import json
import struct
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from mercwizard_core.mapforge_engine import building_library as bl
from mercwizard_core.mapforge_engine.parse_dat_ext import parse_dat_full


# ─── Synthetic .dat builder (all 6 layers + rooms) ─────────────────────
# Extends test_mapforge_library.py's _build_minimal_dat with objs (3-byte
# entries!), shadows, roofs, onroofs and per-tile room ids.

def build_full_dat(
    rows: int = 16,
    cols: int = 16,
    tileset: int = 7,
    land: dict[int, list[tuple[int, int]]] | None = None,
    objs: dict[int, list[tuple[int, int]]] | None = None,
    structs: dict[int, list[tuple[int, int]]] | None = None,
    shadows: dict[int, list[tuple[int, int]]] | None = None,
    roofs: dict[int, list[tuple[int, int]]] | None = None,
    onroofs: dict[int, list[tuple[int, int]]] | None = None,
    rooms: dict[int, int] | None = None,
) -> bytes:
    """A minimal but VALID JA2 1.13 sector .dat (major 8.0, minor 29)
    carrying arbitrary per-tile entries on every layer + room ids."""
    world_max = rows * cols
    land = land or {}
    objs = objs or {}
    structs = structs or {}
    shadows = shadows or {}
    roofs = roofs or {}
    onroofs = onroofs or {}
    rooms = rooms or {}
    out = bytearray()
    out += struct.pack("<f", 8.0)        # major
    out += bytes([29])                   # minor (>=29 → 2-byte room info)
    out += struct.pack("<i", rows)
    out += struct.pack("<i", cols)
    out += struct.pack("<I", 0)          # flags == 0
    out += struct.pack("<I", tileset)
    out += bytes(4)                      # pad → header_len == 25
    out += bytes(2 * world_max)          # heights (all 0)
    for i in range(world_max):
        b0 = len(land.get(i, [])) & 0xF
        b1 = (len(objs.get(i, [])) & 0xF) | ((len(structs.get(i, [])) & 0xF) << 4)
        b2 = (len(shadows.get(i, [])) & 0xF) | ((len(roofs.get(i, [])) & 0xF) << 4)
        b3 = len(onroofs.get(i, [])) & 0xF
        out += bytes([b0, b1, b2, b3])
    for i in range(world_max):           # land pass (2-byte)
        for (t, s) in land.get(i, []):
            out += bytes([t & 0xFF, s & 0xFF])
    for i in range(world_max):           # obj pass (3-byte: B + uint16)
        for (t, s) in objs.get(i, []):
            out += bytes([t & 0xFF]) + struct.pack("<H", s)
    for i in range(world_max):           # struct pass
        for (t, s) in structs.get(i, []):
            out += bytes([t & 0xFF, s & 0xFF])
    for i in range(world_max):           # shadow pass
        for (t, s) in shadows.get(i, []):
            out += bytes([t & 0xFF, s & 0xFF])
    for i in range(world_max):           # roof pass
        for (t, s) in roofs.get(i, []):
            out += bytes([t & 0xFF, s & 0xFF])
    for i in range(world_max):           # onroof pass
        for (t, s) in onroofs.get(i, []):
            out += bytes([t & 0xFF, s & 0xFF])
    for i in range(world_max):           # room info, 2 bytes/tile
        out += struct.pack("<H", rooms.get(i, 0))
    out += bytes(32)                     # MAPCREATE_STRUCT tail
    return bytes(out)


def _g(x: int, y: int, cols: int = 16) -> int:
    return y * cols + x


def make_house(
    rooms_id: int = 9,
    furniture_slot: int = 73,
    cols: int = 16,
) -> dict:
    """A vanilla-pattern house: roomed interior 3×2 at (5,5)-(7,6),
    walls on the rect perimeter (4,4)-(8,7) with a door at (6,7), roofs
    over (5,5)-(8,7), floors on the interior, furniture + objs debris +
    furniture shadow inside, a wall drop shadow on the S row.

    Room tiles deliberately EXCLUDE the wall ring, so extraction must
    expand the bbox to capture the walls + roof overhang.
    """
    land: dict[int, list[tuple[int, int]]] = {}
    for y in range(16):
        for x in range(16):
            land[_g(x, y, cols)] = [(0, 1)]          # base earth
    structs: dict[int, list[tuple[int, int]]] = {}
    shadows: dict[int, list[tuple[int, int]]] = {}
    roofs: dict[int, list[tuple[int, int]]] = {}
    onroofs: dict[int, list[tuple[int, int]]] = {}
    objs: dict[int, list[tuple[int, int]]] = {}
    rooms: dict[int, int] = {}
    # Walls (slot 36) around rect (4,4)-(8,7); door (slot 40) at (6,7).
    for x in range(4, 9):
        structs.setdefault(_g(x, 4, cols), []).append((36, 2))   # N row
        if x != 6:
            structs.setdefault(_g(x, 7, cols), []).append((36, 5))  # S row
    structs.setdefault(_g(6, 7, cols), []).append((40, 1))       # door
    for y in range(5, 8):
        structs.setdefault(_g(4, y, cols), []).append((36, 1))   # W col
        structs.setdefault(_g(8, y, cols), []).append((36, 4))   # E col
    # (The SE corner (8,7) ends up dual-struct naturally: the S-row wall
    # + the E-col wall land on the same tile — the canonical emergent
    # corner pattern.)
    # Roofs over (5,5)-(8,7) — N row + W col rely on overhang sprites.
    for y in range(5, 8):
        for x in range(5, 9):
            roofs[_g(x, y, cols)] = [(64, 9)]
    onroofs[_g(6, 5, cols)] = [(70, 1)]                          # roof vent
    # Interior floors (FIRSTFLOOR=60 lives on the LAND layer).
    for y in range(5, 7):
        for x in range(5, 8):
            land[_g(x, y, cols)].append((60, 1))
            rooms[_g(x, y, cols)] = rooms_id
    # Contents: furniture struct + objs debris + furniture shadow.
    structs.setdefault(_g(6, 5, cols), []).append((furniture_slot, 3))
    objs[_g(5, 5, cols)] = [(79, 2)]
    shadows.setdefault(_g(6, 5, cols), []).append((24, 3))
    # Structure shadow: wall drop shadow on the S row (wall slot, sub 35).
    shadows.setdefault(_g(6, 7, cols), []).append((36, 35))
    return dict(land=land, objs=objs, structs=structs, shadows=shadows,
                roofs=roofs, onroofs=onroofs, rooms=rooms)


def _write_tileset_xml(path: Path, tileset: int,
                       slot_files: dict[int, str]) -> None:
    root = ET.Element("Ja2Set")
    ts = ET.SubElement(root, "Tileset")
    ts.set("index", str(tileset))
    files = ET.SubElement(ts, "Files")
    for slot, name in sorted(slot_files.items()):
        f = ET.SubElement(files, "file")
        f.set("index", str(slot))
        f.text = name
    path.parent.mkdir(parents=True, exist_ok=True)
    ET.ElementTree(root).write(path, encoding="utf-8", xml_declaration=True)


def _make_install(
    tmp_path: Path,
    maps: dict[str, bytes],
    tileset: int = 7,
    slot_files: dict[int, str] | None = None,
    sector_names: dict[str, str] | None = None,
) -> tuple[Path, Path]:
    """Build a fake install: loose maps + Ja2Set.dat.xml (+ optional
    SectorNames.xml). Returns (install_root, xml_path)."""
    install = tmp_path / "install"
    layer = install / "Data-1.13"
    maps_dir = layer / "Maps"
    maps_dir.mkdir(parents=True)
    for name, data in maps.items():
        (maps_dir / name).write_bytes(data)
    xml_path = layer / "Ja2Set.dat.xml"
    _write_tileset_xml(xml_path, tileset, slot_files or {
        0: "earth.sti", 36: "build_01.sti", 40: "door1.sti",
        60: "floor_1.sti", 64: "flat_r1.sti", 70: "roofvent.sti",
        73: "furn_6.sti", 79: "debrocks.sti", 24: "drum1shd.sti",
    })
    if sector_names:
        sn_root = ET.Element("SECTOR_NAMES")
        for grid, name in sector_names.items():
            sec = ET.SubElement(sn_root, "SECTOR")
            ET.SubElement(sec, "SectorGrid").text = grid
            ET.SubElement(sec, "szExploredName").text = name
        sn_path = layer / "TableData" / "Map" / "SectorNames.xml"
        sn_path.parent.mkdir(parents=True)
        ET.ElementTree(sn_root).write(sn_path, encoding="utf-8",
                                      xml_declaration=True)
    return install, xml_path


def _build(install: Path, xml_path: Path, tileset: int = 7,
           thumbs: bool = False) -> dict:
    return bl.build_library(
        xml_path, tileset, install, loose_dirs=[], slf_paths=[],
        thumbs=thumbs,
    )


# ─── Extraction ─────────────────────────────────────────────────────────

def test_extracts_building_with_overhang_expansion(tmp_path: Path) -> None:
    """The room cluster is 3×2 at (5,5)-(7,6); walls + roofs extend to
    (4,4)-(8,7). The expanded bbox must capture the full 5×4 shell."""
    dat = build_full_dat(**make_house())
    install, xml = _make_install(tmp_path, {"C5.dat": dat})
    lib = _build(install, xml)
    assert lib["matching_maps"] == 1
    assert len(lib["entries"]) == 1
    e = lib["entries"][0]
    assert (e["w"], e["h"]) == (5, 4)
    assert e["room_count"] == 1
    assert e["source_map"] == "C5.dat"
    assert e["sector"] == "C5"
    # Full-rect capture: every tile of the expanded bbox is present.
    assert len(e["tiles"]) == 5 * 4


def test_structure_contents_split(tmp_path: Path) -> None:
    """Walls/door/roofs/onroofs/land/floors + wall shadows → structure;
    furniture structs + objs + furniture shadows → contents."""
    dat = build_full_dat(**make_house())
    install, xml = _make_install(tmp_path, {"C5.dat": dat})
    e = _build(install, xml)["entries"][0]

    def slots(tiles: list, layer: str) -> set[int]:
        return {s for t in tiles for s, _ in t["layers"][layer]}

    s_struct = slots(e["tiles"], "structs")
    assert 36 in s_struct and 40 in s_struct       # walls + door
    assert 73 not in s_struct                      # furniture NOT structure
    assert slots(e["tiles"], "roofs") == {64}
    assert slots(e["tiles"], "onroofs") == {70}
    assert {0, 60} <= slots(e["tiles"], "land")    # earth + floors
    assert slots(e["tiles"], "shadows") == {36}    # wall drop shadow
    assert slots(e["tiles"], "objs") == set()      # objs never structure

    c_struct = slots(e["contents_tiles"], "structs")
    assert c_struct == {73}                        # furniture only
    assert slots(e["contents_tiles"], "objs") == {79}
    assert slots(e["contents_tiles"], "shadows") == {24}
    assert slots(e["contents_tiles"], "roofs") == set()
    assert slots(e["contents_tiles"], "land") == set()

    # SE corner dual-struct survives verbatim: tile (8,7) of the source
    # = (dx 4, dy 3) carries TWO wall entries.
    corner = next(t for t in e["tiles"] if (t["dx"], t["dy"]) == (4, 3))
    assert len(corner["layers"]["structs"]) == 2


def test_room_ids_normalized(tmp_path: Path) -> None:
    """Source room id 9 → normalized 1 in the entry (so identical
    buildings from maps with different id allocations dedupe, and the
    frontend's remapRoomIds renumbers from a clean 1..N base)."""
    dat = build_full_dat(**make_house(rooms_id=9))
    install, xml = _make_install(tmp_path, {"C5.dat": dat})
    e = _build(install, xml)["entries"][0]
    room_vals = {t["room"] for t in e["tiles"]}
    assert room_vals == {0, 1}
    interior = [t for t in e["tiles"] if t["room"] == 1]
    assert len(interior) == 6  # the 3×2 roomed interior


def test_dedupe_across_maps(tmp_path: Path) -> None:
    """The same building in two maps → ONE entry with seen_in == 2,
    even when the source room ids differ (normalization)."""
    dat_a = build_full_dat(**make_house(rooms_id=9))
    dat_b = build_full_dat(**make_house(rooms_id=4))
    install, xml = _make_install(
        tmp_path, {"C5.dat": dat_a, "D6.dat": dat_b})
    lib = _build(install, xml)
    assert lib["matching_maps"] == 2
    assert len(lib["entries"]) == 1
    assert lib["entries"][0]["seen_in"] == 2


def test_other_tileset_maps_are_skipped(tmp_path: Path) -> None:
    dat_match = build_full_dat(**make_house())
    dat_other = build_full_dat(tileset=3, **make_house())
    install, xml = _make_install(
        tmp_path, {"C5.dat": dat_match, "E9.dat": dat_other})
    lib = _build(install, xml)
    assert lib["matching_maps"] == 1
    assert len(lib["entries"]) == 1


def test_roomed_patch_without_fabric_is_skipped(tmp_path: Path) -> None:
    """A roomed region with no walls and no roof (exit-grid marker
    style) must not become a library entry."""
    rooms = {_g(x, y): 3 for x in range(2, 5) for y in range(2, 4)}
    dat = build_full_dat(rooms=rooms)
    install, xml = _make_install(tmp_path, {"C5.dat": dat})
    lib = _build(install, xml)
    assert lib["entries"] == []
    assert lib["skipped_clusters"] == 1


# ─── Labels ─────────────────────────────────────────────────────────────

def test_label_fallback_and_shape(tmp_path: Path) -> None:
    """Generic STI names → function falls back to 'Building'; the label
    carries sector, size and room count."""
    dat = build_full_dat(**make_house())
    install, xml = _make_install(tmp_path, {"C5.dat": dat})
    e = _build(install, xml)["entries"][0]
    assert e["function"] == "Building"
    assert e["label"] == "Building — C5 · 5×4 · 1 room"


def test_function_label_house_from_bed_filename(tmp_path: Path) -> None:
    """A contents slot whose STI filename contains 'bed' → 'House'."""
    dat = build_full_dat(**make_house())
    install, xml = _make_install(
        tmp_path, {"C5.dat": dat},
        slot_files={0: "earth.sti", 36: "build_01.sti", 40: "door1.sti",
                    60: "floor_1.sti", 64: "flat_r1.sti",
                    70: "roofvent.sti", 73: "double_bed.sti",
                    79: "debrocks.sti", 24: "drum1shd.sti"},
    )
    e = _build(install, xml)["entries"][0]
    assert e["function"] == "House"
    assert e["label"].startswith("House — C5")


def test_town_label_from_sector_names(tmp_path: Path) -> None:
    dat = build_full_dat(**make_house())
    install, xml = _make_install(
        tmp_path, {"C5.dat": dat},
        sector_names={"C5": "The Den", "A2": "Arroyo"},
    )
    e = _build(install, xml)["entries"][0]
    assert e["town"] == "The Den"
    assert "C5 (The Den)" in e["label"]


# ─── Endpoint + cache ───────────────────────────────────────────────────

def test_endpoint_builds_then_serves_cache(
    tmp_path: Path, monkeypatch,
) -> None:
    import routes.mapforge as mf

    dat = build_full_dat(**make_house())
    install, xml = _make_install(tmp_path, {"C5.dat": dat})
    cache_dir = tmp_path / "libcache"
    monkeypatch.setattr(mf, "_BUILDING_LIB_CACHE_DIR", cache_dir)
    monkeypatch.setattr(mf, "_active_install_root", lambda: install)

    first = mf.building_library(xml=str(xml), tileset=7)
    assert first["from_cache"] is False
    assert len(first["entries"]) == 1
    # Thumbnails are generated through the endpoint (no art in the fake
    # install → background-only canvas, but valid b64 PNG bytes).
    assert first["entries"][0]["thumb_png_b64"]
    assert list(cache_dir.glob("*.json"))

    second = mf.building_library(xml=str(xml), tileset=7)
    assert second["from_cache"] is True
    assert second["entries"] == first["entries"]


def test_endpoint_cache_invalidates_on_map_change(
    tmp_path: Path, monkeypatch,
) -> None:
    import routes.mapforge as mf

    dat = build_full_dat(**make_house())
    install, xml = _make_install(tmp_path, {"C5.dat": dat})
    cache_dir = tmp_path / "libcache"
    monkeypatch.setattr(mf, "_BUILDING_LIB_CACHE_DIR", cache_dir)
    monkeypatch.setattr(mf, "_active_install_root", lambda: install)

    first = mf.building_library(xml=str(xml), tileset=7)
    assert first["from_cache"] is False
    # Edit a map (mtime + size change) → fingerprint changes → rebuild.
    map_path = install / "Data-1.13" / "Maps" / "C5.dat"
    map_path.write_bytes(build_full_dat(**make_house(rooms_id=5)) + b"")
    import os as _os
    _os.utime(map_path, (map_path.stat().st_atime,
                         map_path.stat().st_mtime + 10))
    third = mf.building_library(xml=str(xml), tileset=7)
    assert third["from_cache"] is False


def test_entry_shape_matches_frontend_cliptile(tmp_path: Path) -> None:
    """The contract the frontend's mapClipboard machinery relies on:
    every tile has dx/dy/room/height + all 6 layer keys with [slot, sub]
    pairs, and the payload round-trips through JSON."""
    dat = build_full_dat(**make_house())
    install, xml = _make_install(tmp_path, {"C5.dat": dat})
    lib = json.loads(json.dumps(_build(install, xml)))
    e = lib["entries"][0]
    for field in ("id", "label", "town", "source_map", "tileset", "w", "h",
                  "room_count", "seen_in", "thumb_png_b64", "tiles",
                  "contents_tiles"):
        assert field in e
    for t in e["tiles"] + e["contents_tiles"]:
        assert set(t) == {"dx", "dy", "layers", "room", "height"}
        assert set(t["layers"]) == set(
            ("land", "objs", "shadows", "structs", "roofs", "onroofs"))
        for entries in t["layers"].values():
            for pair in entries:
                assert len(pair) == 2
                assert all(isinstance(v, int) for v in pair)
