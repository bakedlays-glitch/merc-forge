"""Tests for the MapForge library import path — focuses on the slot
allocation policy added 2026-05-24 (user report: auto-pick was picking
slots above the user's engine cap and silently shipping a CTD-bound
sector) plus the single-sub extraction added the same day for the
Phase 3 sub-import flow.
"""
from __future__ import annotations

import io
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest
from fastapi import HTTPException
from PIL import Image, ImagePalette

from routes.mapforge_library import _extract_single_sub_bytes, _next_free_slot


def _write_xml(path: Path, tileset_index: int, used_slots: list[int]) -> None:
    """Write a minimal Ja2Set.dat.xml with one Tileset block containing
    the specified used slot indices. Used by every test below."""
    root = ET.Element("Ja2Set")
    ts = ET.SubElement(root, "Tileset")
    ts.set("index", str(tileset_index))
    files = ET.SubElement(ts, "Files")
    for slot in used_slots:
        f = ET.SubElement(files, "file")
        f.set("index", str(slot))
        f.text = f"placeholder_{slot}.sti"
    ET.ElementTree(root).write(path, encoding="utf-8", xml_declaration=True)


def test_next_free_slot_picks_lowest_free(tmp_path: Path) -> None:
    """Baseline: returns the lowest slot index not in the used set."""
    xml = tmp_path / "Ja2Set.dat.xml"
    _write_xml(xml, tileset_index=1, used_slots=[0, 1, 2, 5, 6])
    assert _next_free_slot(xml, tileset=1) == 3


def test_next_free_slot_respects_cap(tmp_path: Path) -> None:
    """When every slot 0..cap is taken, raises NO_FREE_SLOT_UNDER_CAP
    (not the old TILESET_FULL that walked to 255)."""
    xml = tmp_path / "Ja2Set.dat.xml"
    # Fill 0..10; cap at 10.
    _write_xml(xml, tileset_index=1, used_slots=list(range(0, 11)))
    with pytest.raises(HTTPException) as ei:
        _next_free_slot(xml, tileset=1, engine_max_tile_slot=10)
    assert ei.value.status_code == 409
    assert ei.value.detail["error"] == "NO_FREE_SLOT_UNDER_CAP"
    assert ei.value.detail["engine_max_tile_slot"] == 10


def test_next_free_slot_default_cap_is_stock_ja2(tmp_path: Path) -> None:
    """Default cap = 150 (stock JA2 1.13 NUMBEROFTILETYPES - 1). With
    only slot 0 used the next free is 1, well under the cap."""
    xml = tmp_path / "Ja2Set.dat.xml"
    _write_xml(xml, tileset_index=1, used_slots=[0])
    assert _next_free_slot(xml, tileset=1) == 1


def test_next_free_slot_does_not_pick_above_cap(tmp_path: Path) -> None:
    """Regression for a 2026-05-24 user report. Slots 0..5 used + cap
    set at 5 → no candidate exists ≤ cap, must raise. Previously this
    would happily return 6, which the engine can't address."""
    xml = tmp_path / "Ja2Set.dat.xml"
    _write_xml(xml, tileset_index=1, used_slots=[0, 1, 2, 3, 4, 5])
    with pytest.raises(HTTPException) as ei:
        _next_free_slot(xml, tileset=1, engine_max_tile_slot=5)
    assert ei.value.detail["error"] == "NO_FREE_SLOT_UNDER_CAP"


def test_next_free_slot_skips_to_inheritance_from_tileset_zero(
    tmp_path: Path,
) -> None:
    """Tileset 0 is the base — its slots count as taken for any
    non-zero tileset (engine VFS chains tile-0 entries into every other
    tileset's lookup)."""
    xml = tmp_path / "Ja2Set.dat.xml"
    root = ET.Element("Ja2Set")
    # Base tileset 0 uses slots 0, 1.
    ts0 = ET.SubElement(root, "Tileset")
    ts0.set("index", "0")
    files0 = ET.SubElement(ts0, "Files")
    for s in (0, 1):
        f = ET.SubElement(files0, "file")
        f.set("index", str(s))
        f.text = f"base_{s}.sti"
    # Tileset 7 uses slot 2.
    ts7 = ET.SubElement(root, "Tileset")
    ts7.set("index", "7")
    files7 = ET.SubElement(ts7, "Files")
    f = ET.SubElement(files7, "file")
    f.set("index", "2")
    f.text = "specific.sti"
    ET.ElementTree(root).write(xml, encoding="utf-8", xml_declaration=True)
    # 0 + 1 (inherited) + 2 (own) all taken → 3 is next.
    assert _next_free_slot(xml, tileset=7) == 3


def test_next_free_slot_empty_xml_returns_one(tmp_path: Path) -> None:
    """Defensive: an unparseable XML returns 1 (current behavior).
    Wraps the OSError/ParseError path so the caller doesn't have to
    distinguish between 'no slots used' and 'no file'."""
    bogus = tmp_path / "does-not-exist.xml"
    assert _next_free_slot(bogus, tileset=1) == 1


# ─── Single-sub extraction (Phase 3 — sub-import flow) ────────────────

def _build_multiframe_8bit_sti(frame_count: int) -> bytes:
    """Build an in-memory N-frame indexed-mode STI for testing the
    single-sub extractor. Each frame is a solid-color block — frame i
    fills with palette index (i + 1) so the extractor can be checked
    by reading the chosen frame back and comparing its pixel values.
    """
    from ja2py.content.Image import Images8Bit, SubImage8Bit
    from ja2py.fileformats.Sti import save_8bit_sti

    palette = ImagePalette.raw("RGB", bytes(range(256)) * 3)
    images = []
    for i in range(frame_count):
        # Each frame is 4x4 filled with palette index (i+1).
        img = Image.new("P", (4, 4), color=i + 1)
        img.putpalette(palette.palette)
        images.append(SubImage8Bit(img, offsets=(0, 0), aux_data=None))
    container = Images8Bit(images=images, palette=palette, width=4, height=4)
    out = io.BytesIO()
    save_8bit_sti(container, out)
    return out.getvalue()


def test_extract_single_sub_picks_correct_frame() -> None:
    """Round-trip: build a 5-frame STI, extract frame 3, reload the
    extracted STI, verify it has exactly one frame whose pixel value
    matches what we put in (frame 3 → palette index 4)."""
    from ja2py.fileformats.Sti import load_8bit_sti

    source = _build_multiframe_8bit_sti(frame_count=5)
    extracted = _extract_single_sub_bytes(source, sub_idx=3)
    reloaded = load_8bit_sti(io.BytesIO(extracted))
    assert len(reloaded.images) == 1
    # Frame 3 was filled with palette index 4 (i + 1 from the builder).
    pixel = reloaded.images[0].image.getpixel((0, 0))
    assert pixel == 4


def test_extract_single_sub_out_of_range_raises() -> None:
    """Asking for a sub past the source's frame count is a 400 with
    SUB_OUT_OF_RANGE — the frontend uses this to surface a "pick a
    valid sub" error instead of producing a corrupt STI."""
    source = _build_multiframe_8bit_sti(frame_count=3)
    with pytest.raises(HTTPException) as ei:
        _extract_single_sub_bytes(source, sub_idx=99)
    assert ei.value.status_code == 400
    assert ei.value.detail["error"] == "SUB_OUT_OF_RANGE"
    assert ei.value.detail["frame_count"] == 3


def test_extract_single_sub_rejects_garbage_bytes() -> None:
    """Non-STI bytes produce STI_REENCODE_FAILED rather than an
    uncaught exception bubbling out to the client."""
    with pytest.raises(HTTPException) as ei:
        _extract_single_sub_bytes(b"this is not an STI", sub_idx=0)
    assert ei.value.status_code == 500
    assert ei.value.detail["error"] == "STI_REENCODE_FAILED"


# ─── Phase 4: inject-sub helpers ──────────────────────────────────────

from routes.mapforge_library import _find_loose_tileset_stis, _palettes_match


def test_find_loose_tileset_stis_only_lists_present_files(tmp_path: Path) -> None:
    """The loose-slots helper must walk the XML AND verify each
    registered file is on disk. SLF-only slots (no on-disk file)
    drop out so they don't show up in the inject destination
    dropdown."""
    install = tmp_path / "install"
    layer = install / "Data-1.13"
    tilesets_dir = layer / "Tilesets" / "1"
    tilesets_dir.mkdir(parents=True)
    # Loose file present on disk.
    on_disk = _build_multiframe_8bit_sti(frame_count=3)
    (tilesets_dir / "present.sti").write_bytes(on_disk)
    # XML registers TWO slots — only one exists as a loose file.
    xml_path = layer / "Ja2Set.dat.xml"
    root = ET.Element("Ja2Set")
    ts = ET.SubElement(root, "Tileset")
    ts.set("index", "1")
    files = ET.SubElement(ts, "Files")
    f1 = ET.SubElement(files, "file"); f1.set("index", "0"); f1.text = "present.sti"
    f2 = ET.SubElement(files, "file"); f2.set("index", "1"); f2.text = "slf_only.sti"
    ET.ElementTree(root).write(xml_path, encoding="utf-8", xml_declaration=True)
    slots = _find_loose_tileset_stis(install, xml_path, tileset=1)
    names = [s.filename for s in slots]
    assert "present.sti" in names
    assert "slf_only.sti" not in names


def test_find_loose_tileset_stis_reads_frame_count(tmp_path: Path) -> None:
    """The helper surfaces frame_count for each loose slot so the
    inject UI can show "destination has N frames; new will be N+1"."""
    install = tmp_path / "install"
    layer = install / "Data-1.13"
    tilesets_dir = layer / "Tilesets" / "2"
    tilesets_dir.mkdir(parents=True)
    sti_bytes = _build_multiframe_8bit_sti(frame_count=5)
    (tilesets_dir / "foo.sti").write_bytes(sti_bytes)
    xml_path = layer / "Ja2Set.dat.xml"
    root = ET.Element("Ja2Set")
    ts = ET.SubElement(root, "Tileset")
    ts.set("index", "2")
    files = ET.SubElement(ts, "Files")
    f = ET.SubElement(files, "file"); f.set("index", "0"); f.text = "foo.sti"
    ET.ElementTree(root).write(xml_path, encoding="utf-8", xml_declaration=True)
    slots = _find_loose_tileset_stis(install, xml_path, tileset=2)
    assert len(slots) == 1
    assert slots[0].frame_count == 5


def test_palettes_match_identical_bytes() -> None:
    """Two palettes with identical byte content compare equal — the
    inject path uses this to allow merging frames cross-STI when the
    colors line up."""
    p1 = ImagePalette.raw("RGB", bytes(range(256)) * 3)
    p2 = ImagePalette.raw("RGB", bytes(range(256)) * 3)
    assert _palettes_match(p1, p2) is True


def test_palettes_match_different_bytes() -> None:
    """Different palettes → not match. Inject path refuses these
    with PALETTE_MISMATCH unless force=true."""
    p1 = ImagePalette.raw("RGB", bytes(range(256)) * 3)
    p2 = ImagePalette.raw("RGB", bytes((i + 1) & 0xFF for i in range(256)) * 3)
    assert _palettes_match(p1, p2) is False


# ─── Cross-tileset copy (copy-to-tileset) ─────────────────────────────
# These are the no-crash proof for "add a tile from another tileset into
# the current tileset": they assert the append-only / index-keyed
# invariant on Ja2Set.dat.xml, that a fixture sector's per-tile (type,
# subindex) data is unchanged by the copy, the engine-cap enforcement,
# and the on-disk side effects (STI written, .bak made, no overwrite).

import struct

from fastapi import Response

from routes.mapforge_library import (
    copy_tile_to_tileset, CopyTileToTilesetBody, _commit_sti_to_tileset,
)


def _build_minimal_dat(
    rows: int = 8,
    cols: int = 8,
    tileset: int = 7,
    land: dict[int, list[tuple[int, int]]] | None = None,
    structs: dict[int, list[tuple[int, int]]] | None = None,
) -> bytes:
    """Build a minimal but VALID JA2 1.13 sector .dat (major 8.0, minor
    29) carrying the given per-tile (type, subindex) entries in the land
    + struct layers. 8×8 is the smallest map that clears the parser's
    blanket 116-byte floor.

    Header layout matches parse_dat_ext (major>=7): the parser reads
    `tileset` at offset 17 with header_len=25, so 4 pad bytes follow the
    tileset field. Land/struct passes are 2-byte (type, sub) per entry.
    """
    world_max = rows * cols
    land = land or {}
    structs = structs or {}
    out = bytearray()
    out += struct.pack("<f", 8.0)        # major
    out += bytes([29])                   # minor (>=29 → 2-byte room info)
    out += struct.pack("<i", rows)
    out += struct.pack("<i", cols)
    out += struct.pack("<I", 0)          # flags == 0 → tail is parseable
    out += struct.pack("<I", tileset)
    out += bytes(4)                      # pad so header_len == 25
    out += bytes(2 * world_max)          # per-tile heights (all 0)
    # Layer-count nibbles: b0 = land|world_flags, b1 = obj|struct(hi),
    # b2 = shadow|roof, b3 = onroof|unused. Only land + struct used.
    for i in range(world_max):
        nl = len(land.get(i, []))
        ns = len(structs.get(i, []))
        out += bytes([nl & 0xF, (ns & 0xF) << 4, 0, 0])
    for i in range(world_max):           # land pass
        for (t, s) in land.get(i, []):
            out += bytes([t & 0xFF, s & 0xFF])
    # obj pass: none
    for i in range(world_max):           # struct pass
        for (t, s) in structs.get(i, []):
            out += bytes([t & 0xFF, s & 0xFF])
    # shadow / roof / onroof: none
    out += bytes(2 * world_max)          # room info (2 bytes/tile, minor>=29)
    out += bytes(32)                     # MAPCREATE_STRUCT tail (major>=7)
    return bytes(out)


def _make_copy_install(
    tmp_path: Path,
    *,
    src_tileset: int,
    src_slot: int,
    src_filename: str,
    dest_tileset: int,
    dest_used: list[int],
    src_frames: int = 3,
    with_jsd: bool = False,
) -> tuple[Path, Path]:
    """Build a fake install whose Ja2Set.dat.xml registers a source slot
    (with a real loose STI on disk) and a destination tileset with the
    given used slots. Returns (install_root, xml_path)."""
    install = tmp_path / "install"
    layer = install / "Data-1.13"
    # Source slot's loose STI.
    src_dir = layer / "Tilesets" / str(src_tileset)
    src_dir.mkdir(parents=True)
    (src_dir / src_filename).write_bytes(
        _build_multiframe_8bit_sti(frame_count=src_frames)
    )
    if with_jsd:
        # A sibling .jsd marks the source as a multi-tile struct. Content
        # is opaque to the copy path — it copies the bytes verbatim.
        (src_dir / (src_filename[:-4] + ".jsd")).write_bytes(b"JSD\x00fake")
    # XML: source tileset block + destination tileset block.
    root = ET.Element("Ja2Set")
    ts_src = ET.SubElement(root, "Tileset")
    ts_src.set("index", str(src_tileset))
    files_src = ET.SubElement(ts_src, "Files")
    f = ET.SubElement(files_src, "file")
    f.set("index", str(src_slot))
    f.text = src_filename
    ts_dest = ET.SubElement(root, "Tileset")
    ts_dest.set("index", str(dest_tileset))
    files_dest = ET.SubElement(ts_dest, "Files")
    for slot in dest_used:
        fd = ET.SubElement(files_dest, "file")
        fd.set("index", str(slot))
        fd.text = f"dest_{slot}.sti"
    xml_path = layer / "Ja2Set.dat.xml"
    ET.ElementTree(root).write(xml_path, encoding="utf-8", xml_declaration=True)
    return install, xml_path


def _activate_install(monkeypatch, install_root: Path) -> None:
    """Point both the library route's get_state().active() AND
    mapforge's _active_install_root at `install_root`, so the copy path
    resolves the active install + its tileset asset roots to our fake."""
    import routes.mapforge as mf
    import routes.mapforge_library as mfl

    class _FakeInfo:
        path = str(install_root)

    class _FakeState:
        def active(self):
            return _FakeInfo()

    monkeypatch.setattr(mfl, "get_state", lambda: _FakeState())
    monkeypatch.setattr(mf, "_active_install_root", lambda: install_root)


def _files_by_index(xml_path: Path, tileset: int) -> dict[int, str]:
    """{slot: filename} for one tileset block (no inheritance) — used to
    assert the append-only invariant on the destination block."""
    tree = ET.parse(xml_path)
    for ts in tree.getroot().iter("Tileset"):
        if int(ts.get("index", -1)) == tileset:
            fnode = ts.find("Files")
            if fnode is None:
                return {}
            return {
                int(f.get("index")): (f.text or "").strip()
                for f in fnode.findall("file")
            }
    return {}


def test_copy_to_tileset_is_append_only_and_index_keyed(
    tmp_path: Path, monkeypatch,
) -> None:
    """Invariant #1: copying into a free slot APPENDS exactly one new
    `<file index>` and leaves every pre-existing entry byte-identical.
    This is what keeps already-saved sectors valid — the engine rebuilds
    the global tile index from a compile-time table keyed by slot index,
    so an append never shifts existing indices."""
    install, xml_path = _make_copy_install(
        tmp_path,
        src_tileset=7, src_slot=12, src_filename="grass.sti",
        dest_tileset=1, dest_used=[0, 1, 2, 5],
    )
    _activate_install(monkeypatch, install)
    before = _files_by_index(xml_path, 1)

    res = copy_tile_to_tileset(
        src_tileset=7, src_slot=12,
        body=CopyTileToTilesetBody(dest_tileset=1),  # target_slot defaults to 12
        response=Response(),
    )
    assert res.dest_tileset == 1
    assert res.slot == 12  # defaulted to the source slot index
    after = _files_by_index(xml_path, 1)

    # Every pre-existing entry is byte-identical (same slot → same file).
    for slot, fname in before.items():
        assert after[slot] == fname, f"slot {slot} mutated"
    # Exactly one new entry, at the resolved slot, naming the copied STI.
    new_slots = set(after) - set(before)
    assert new_slots == {12}
    assert after[12] == "grass.sti"
    # No existing slot was dropped or reindexed.
    assert set(before).issubset(set(after))


def test_copy_to_tileset_leaves_sector_tile_data_unchanged(
    tmp_path: Path, monkeypatch,
) -> None:
    """Invariant #2: a sector .dat that references the destination
    tileset parses to the SAME per-tile (type, subindex) data before and
    after the copy. The copy only touches Ja2Set.dat.xml + the Tilesets
    dir — it never rewrites sector .dats — so stored tile references are
    untouched."""
    from mercwizard_core.mapforge_engine.parse_dat_ext import parse_dat_full

    install, xml_path = _make_copy_install(
        tmp_path,
        src_tileset=7, src_slot=12, src_filename="grass.sti",
        dest_tileset=1, dest_used=[0, 1, 2],
    )
    _activate_install(monkeypatch, install)
    # A sector on tileset 1 with known land + struct references.
    dat_bytes = _build_minimal_dat(
        tileset=1,
        land={0: [(1, 5), (1, 6)], 3: [(2, 9)]},
        structs={1: [(40, 3)]},
    )
    sector_path = install / "Data-1.13" / "Maps" / "A9.dat"
    sector_path.parent.mkdir(parents=True)
    sector_path.write_bytes(dat_bytes)

    def _tile_data(p: Path) -> dict:
        parsed = parse_dat_full(p.read_bytes(), str(p))
        return {k: parsed[k] for k in ("land", "objs", "structs",
                                       "shadows", "roofs", "onroofs")}

    before = _tile_data(sector_path)
    copy_tile_to_tileset(
        src_tileset=7, src_slot=12,
        body=CopyTileToTilesetBody(dest_tileset=1),
        response=Response(),
    )
    after = _tile_data(sector_path)
    assert before == after
    # Spot-check the actual stored references survived intact.
    assert after["land"][0] == [(1, 5), (1, 6)]
    assert after["structs"][1] == [(40, 3)]


def test_copy_to_tileset_rejects_slot_above_engine_cap(
    tmp_path: Path, monkeypatch,
) -> None:
    """Invariant #3a: a manual target_slot above engine_max_tile_slot is
    a 400 SLOT_ABOVE_ENGINE_CAP — the engine HARD-crashes on sector load
    if it dereferences a slot >= NUMBEROFTILETYPES."""
    install, xml_path = _make_copy_install(
        tmp_path,
        src_tileset=7, src_slot=12, src_filename="grass.sti",
        dest_tileset=1, dest_used=[0],
    )
    _activate_install(monkeypatch, install)
    with pytest.raises(HTTPException) as ei:
        copy_tile_to_tileset(
            src_tileset=7, src_slot=12,
            body=CopyTileToTilesetBody(
                dest_tileset=1, target_slot=151, engine_max_tile_slot=150,
            ),
            response=Response(),
        )
    assert ei.value.status_code == 400
    assert ei.value.detail["error"] == "SLOT_ABOVE_ENGINE_CAP"
    # No write happened — the dest STI must not exist.
    assert not (install / "Data-1.13" / "Tilesets" / "1" / "grass.sti").exists()


def test_copy_to_tileset_auto_pick_raises_when_full_under_cap(
    tmp_path: Path, monkeypatch,
) -> None:
    """Invariant #3b: auto_pick with every slot 0..cap taken → 409
    NO_FREE_SLOT_UNDER_CAP, never a slot above cap."""
    install, xml_path = _make_copy_install(
        tmp_path,
        src_tileset=7, src_slot=2, src_filename="grass.sti",
        dest_tileset=1, dest_used=list(range(0, 11)),  # 0..10 all taken
    )
    _activate_install(monkeypatch, install)
    with pytest.raises(HTTPException) as ei:
        copy_tile_to_tileset(
            src_tileset=7, src_slot=2,
            # auto_pick → free-slot scan; cap at 10 so 0..10 are full.
            body=CopyTileToTilesetBody(
                dest_tileset=1, auto_pick=True, engine_max_tile_slot=10,
            ),
            response=Response(),
        )
    assert ei.value.status_code == 409
    assert ei.value.detail["error"] == "NO_FREE_SLOT_UNDER_CAP"


def test_copy_to_tileset_auto_pick_picks_lowest_free_slot(
    tmp_path: Path, monkeypatch,
) -> None:
    """The SLOT_TAKEN recovery path: auto_pick lands the tile in the
    lowest free slot (incl. tile-0 inheritance) and still appends
    append-only."""
    install, xml_path = _make_copy_install(
        tmp_path,
        src_tileset=7, src_slot=2, src_filename="grass.sti",
        dest_tileset=1, dest_used=[0, 1, 2, 3],  # lowest free is 4
    )
    _activate_install(monkeypatch, install)
    before = _files_by_index(xml_path, 1)
    res = copy_tile_to_tileset(
        src_tileset=7, src_slot=2,
        body=CopyTileToTilesetBody(dest_tileset=1, auto_pick=True),
        response=Response(),
    )
    assert res.slot == 4
    after = _files_by_index(xml_path, 1)
    assert set(after) - set(before) == {4}
    assert after[4] == "grass.sti"


def test_copy_to_tileset_side_effects_and_no_overwrite(
    tmp_path: Path, monkeypatch,
) -> None:
    """Invariant #4: a successful copy writes the STI into the dest
    Tilesets dir and makes a Ja2Set.dat.xml .bak; an OCCUPIED target slot
    is refused with 409 SLOT_TAKEN and nothing is overwritten."""
    install, xml_path = _make_copy_install(
        tmp_path,
        src_tileset=7, src_slot=12, src_filename="grass.sti",
        dest_tileset=1, dest_used=[0, 1, 2],
        with_jsd=True,
    )
    _activate_install(monkeypatch, install)

    # Happy path → STI written + .bak created + .jsd carried over.
    res = copy_tile_to_tileset(
        src_tileset=7, src_slot=12,
        body=CopyTileToTilesetBody(dest_tileset=1),  # slot 12 (free)
        response=Response(),
    )
    dest_sti = install / "Data-1.13" / "Tilesets" / "1" / "grass.sti"
    assert dest_sti.is_file()
    assert res.written_to == str(dest_sti)
    assert xml_path.with_suffix(xml_path.suffix + ".bak").is_file()
    # Source had a sibling .jsd → it rode along on the whole-STI copy.
    assert res.jsd_copied is True
    assert dest_sti.with_suffix(".jsd").is_file()

    # Now target an OCCUPIED slot (2) → 409 SLOT_TAKEN, no overwrite.
    occupant_before = (
        install / "Data-1.13" / "Tilesets" / "1" / "dest_2.sti"
    )
    with pytest.raises(HTTPException) as ei:
        copy_tile_to_tileset(
            src_tileset=7, src_slot=12,
            body=CopyTileToTilesetBody(dest_tileset=1, target_slot=2),
            response=Response(),
        )
    assert ei.value.status_code == 409
    assert ei.value.detail["error"] == "SLOT_TAKEN"
    assert ei.value.detail["occupant_filename"] == "dest_2.sti"
    # The XML still maps slot 2 to the original occupant (unchanged).
    assert _files_by_index(xml_path, 1)[2] == "dest_2.sti"
    # We never created a real file for the occupant, and the refusal
    # didn't fabricate one either.
    assert not occupant_before.exists()


def test_copy_to_tileset_rejects_overlong_filename(
    tmp_path: Path, monkeypatch,
) -> None:
    """The engine's per-slot filename buffer is CHAR8[32] and the tileset
    loader strncpy()s 32 bytes (XML_TileSet.hpp), so a name >31 chars is
    stored unterminated → the STI silently fails to load in-game. The copy
    path refuses it with 400 FILENAME_TOO_LONG before writing anything,
    rather than producing a tileset entry the engine can't resolve."""
    long_name = "this_is_a_really_long_tileset_filename.sti"  # 42 chars
    assert len(long_name) > 31
    install, xml_path = _make_copy_install(
        tmp_path,
        src_tileset=7, src_slot=12, src_filename=long_name,
        dest_tileset=1, dest_used=[0, 1, 2],
    )
    _activate_install(monkeypatch, install)
    with pytest.raises(HTTPException) as ei:
        copy_tile_to_tileset(
            src_tileset=7, src_slot=12,
            body=CopyTileToTilesetBody(dest_tileset=1),
            response=Response(),
        )
    assert ei.value.status_code == 400
    assert ei.value.detail["error"] == "FILENAME_TOO_LONG"
    assert ei.value.detail["max_len"] == 31
    # Fail-fast before any write — nothing landed in the dest Tilesets dir.
    assert not (install / "Data-1.13" / "Tilesets" / "1" / long_name).exists()


def test_commit_sti_to_tileset_rejects_reserved_device_name(tmp_path: Path) -> None:
    """The Windows reserved-device-name guard lives in the shared
    _commit_sti_to_tileset choke point, so BOTH import surfaces (catalog-add and
    cross-tileset copy) reject e.g. con.sti — copy_tile_to_tileset previously
    lacked it. The guard fires before any slot/XML/file work, so the bogus
    xml_path here is never read (and a con.sti SOURCE file can't even be created
    on Windows, which is why this drives the shared helper directly)."""
    for name in ("con.sti", "COM1.sti", "lpt9.sti", "aux.foo.sti"):
        with pytest.raises(HTTPException) as ei:
            _commit_sti_to_tileset(
                xml_path=tmp_path / "Ja2Set.dat.xml",
                tileset=1, sti_bytes=b"\x00", jsd_bytes=None,
                target_filename=name, target_slot=5,
                engine_max_tile_slot=150, allow_above_cap=False,
                response=Response(),
            )
        assert ei.value.status_code == 400, name
        assert ei.value.detail["error"] == "BAD_FILENAME", name


def test_copy_to_tileset_manual_override_of_inherited_slot_succeeds(
    tmp_path: Path, monkeypatch,
) -> None:
    """A manual target_slot that's only INHERITED from tile-0 (not in the dest
    tileset's own <file> entries) is overridable — the engine honors a
    per-tileset entry over the inherited one. Counting inheritance as 'taken'
    409'd nearly every manual import into a non-base tileset (refactor
    regression); a slot the dest tileset DEFINES itself is still refused."""
    install, xml_path = _make_copy_install(
        tmp_path,
        src_tileset=7, src_slot=12, src_filename="grass.sti",
        dest_tileset=1, dest_used=[0, 1],
    )
    # Add a base tileset 0 defining slot 5 → dest 1 INHERITS slot 5 but doesn't
    # define it in its own <file> entries.
    tree = ET.parse(xml_path)
    root = tree.getroot()
    ts0 = ET.SubElement(root, "Tileset")
    ts0.set("index", "0")
    files0 = ET.SubElement(ts0, "Files")
    f0 = ET.SubElement(files0, "file")
    f0.set("index", "5")
    f0.text = "base5.sti"
    tree.write(xml_path, encoding="utf-8", xml_declaration=True)
    _activate_install(monkeypatch, install)

    # Manual pick of the inherited-only slot 5 SUCCEEDS (override).
    res = copy_tile_to_tileset(
        src_tileset=7, src_slot=12,
        body=CopyTileToTilesetBody(dest_tileset=1, target_slot=5),
        response=Response(),
    )
    assert res.slot == 5
    assert _files_by_index(xml_path, 1)[5] == "grass.sti"

    # A slot the dest tileset DEFINES itself is still refused.
    with pytest.raises(HTTPException) as ei:
        copy_tile_to_tileset(
            src_tileset=7, src_slot=12,
            body=CopyTileToTilesetBody(dest_tileset=1, target_slot=1),
            response=Response(),
        )
    assert ei.value.status_code == 409
    assert ei.value.detail["error"] == "SLOT_TAKEN"
