"""Forward-carry gate for the MapForge prop-label catalog.

Scans a live install's tileset registrations for The Wasteland's custom prop
sheets and reports any that have no friendly name in prop_labels.json — plus
per-sub object sheets missing sub names, and custom art sitting on disk that is
neither registered nor aliased (a candidate to surface or fold in). This is how
naming "carries forward": register a new custom sheet and this fails until it is
named.

    python tools/prop_labels_lint.py [--install <dir>] [--xml <file>]
                                      [--tileset-root <dir>] [--tileset N ...] [--strict]

It also refuses an ENGINE-INVALID registration of a custom sheet: a sheet with
more subs than the engine reservation of its slot's type (gNumTilesPerType,
TileDat.cpp — CreateTileDatabase cuts the sheet there and the extra subs resolve
into the next type in-game), a slot whose type is an engine-addressed marker
(MOCKFLOOR 72 = exit-grid / AP-cursor tile), a slot with no qualified capacity,
or a registered sheet that is not readable on disk (LoadTileSurfaces fails).

Exit 1 when a registered custom sheet is unnamed or engine-invalid (or, with
--strict, when a per-sub sheet is missing any sub name). Read-only.
"""
from __future__ import annotations

import os
import argparse
import struct
import sys
from pathlib import Path
import xml.etree.ElementTree as ET

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))  # sidecar/
from mercwizard_core.mapforge import prop_labels as PL  # noqa: E402

# Engine reservations per tile type, verified against the LIVE engine tree
# (Visual Studio Root/TileEngine/TileDat.cpp gNumTilesPerType). Single owner:
# The Wasteland repo, Headless_Compiler/sector_gen/wilderness_fixed_assets.py.
sys.path.insert(0, str(HERE.parents[2] / "Headless_Compiler"))  # <repo>/MercWizard2/sidecar/tools -> <repo>
from sector_gen.wilderness_fixed_assets import TILE_CAPACITY  # noqa: E402

# The conservative fixed-world table intentionally omits some otherwise valid
# shadow types. These three reservations are read directly from the live engine
# TileDat.cpp gNumTilesPerType and TileDat.h enum: FIRSTSHADOW (24) 12,
# FENCESHADOW (87) 23, FIRSTVEHICLESHADOW (90) 12.
SOURCE_CAPACITY_SUPPLEMENT = {24: 12, 87: 23, 90: 12}

# Types the engine addresses by tile INDEX for its own UI — never a home for art.
RESERVED_TYPES = {
    72: "MOCKFLOOR: exit-grid marker (Exit Grids.cpp:102) + AP-cost cursor (Interface Cursors.cpp:15)",
}

DEFAULT_INSTALL = Path(os.environ.get("JA2_INSTALL", "")) / "Data-1.13"
CUSTOM_TILESETS = (70, 71, 72, 73)
FRAME_NAMED_CATEGORIES = {"furniture", "container", "scatter", "vegetation", "signage", "prop"}


class MissingTilesetError(ValueError):
    """The selected XML has no block for a requested tileset."""


def own_block(xml_path: Path, ts: int) -> dict[int, str]:
    tree = ET.parse(xml_path)
    for t in tree.getroot().iter("Tileset"):
        if int(t.get("index", -1)) == ts:
            f = t.find("Files")
            return {int(x.get("index")): (x.text or "").strip()
                    for x in (f.findall("file") if f is not None else [])}
    raise MissingTilesetError(f"Tileset {ts} is absent from {xml_path}")


def frame_count(path: Path) -> int:
    """Sub-image count straight from the STCI header (offset 28, <H); -1 if unreadable.

    The earlier ja2-open-toolset import resolved a path that does not exist next to
    MercWizard2, so it always returned -1 and the per-sub coverage check never ran.
    """
    try:
        with open(path, "rb") as fh:
            head = fh.read(30)
    except OSError:
        return -1
    if head[:4] != b"STCI" or len(head) < 30:
        return -1
    count = struct.unpack_from("<H", head, 28)[0]
    return count if count > 0 else -1


def missing_frame_names(rec: dict, count: int, strict: bool) -> list[int]:
    """Check actual 1-based indices; a same-length sparse dict is not coverage."""
    if count < 1:
        return []
    names = rec.get("per_sub") or {}
    if not names and not (strict and rec.get("category") in FRAME_NAMED_CATEGORIES):
        return []
    return [i for i in range(1, count + 1) if not isinstance(names.get(str(i)), str)
            or not names[str(i)].strip()]


def invalid_frame_keys(rec: dict, count: int) -> list[str]:
    """Catalog keys outside the actual STI frame range are stale or malformed."""
    names = rec.get("per_sub") or {}
    return [key for key in names if not str(key).isdecimal() or not 1 <= int(key) <= count]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--install", default=str(DEFAULT_INSTALL))
    ap.add_argument("--xml", type=Path, help="selected build's Ja2Set.dat.xml (defaults to --install/Ja2Set.dat.xml)")
    ap.add_argument("--tileset-root", type=Path, help="selected build's tilesets directory (defaults to --install/tilesets)")
    ap.add_argument("--tileset", type=int, action="append")
    ap.add_argument("--strict", action="store_true",
                    help="also fail when a per-sub sheet is missing any sub name")
    args = ap.parse_args()

    PL.reload()
    data_root = Path(args.install)
    xml_path = args.xml or data_root / "Ja2Set.dat.xml"
    tsroot = args.tileset_root or data_root / "tilesets"
    tilesets = args.tileset or list(CUSTOM_TILESETS)

    unnamed: list[str] = []
    missing_subs: list[str] = []
    invalid_subs: list[str] = []
    unregistered: list[str] = []
    engine_invalid: list[str] = []
    named_ok = 0
    skipped_tilesets: list[int] = []

    for ts in tilesets:
        try:
            reg = own_block(xml_path, ts)
        except MissingTilesetError:
            if args.tileset is not None:
                raise
            skipped_tilesets.append(ts)
            continue
        reg_names = {v.lower() for v in reg.values()}
        for slot, fname in sorted(reg.items()):
            if not PL.is_custom(fname):
                continue
            fc = frame_count(tsroot / str(ts) / fname)
            cap = TILE_CAPACITY.get(slot, SOURCE_CAPACITY_SUPPLEMENT.get(slot))
            if slot in RESERVED_TYPES:
                engine_invalid.append(f"T{ts} slot {slot}: {fname} — {RESERVED_TYPES[slot]}")
            elif cap is None:
                engine_invalid.append(f"T{ts} slot {slot}: {fname} — no qualified engine capacity for this slot "
                                      "(add it to sector_gen/wilderness_fixed_assets.TILE_CAPACITY from the live TileDat.cpp)")
            elif fc < 0:
                engine_invalid.append(f"T{ts} slot {slot}: {fname} — sheet not readable at tilesets/{ts}/ "
                                      "(a present registration with a missing file fails LoadTileSurfaces)")
            elif fc > cap:
                engine_invalid.append(f"T{ts} slot {slot}: {fname} — {fc} subs > engine reservation {cap} "
                                      f"(gNumTilesPerType; subs {cap + 1}-{fc} never load in-game)")
            rec = PL.label_for(fname)
            if not rec:
                unnamed.append(f"T{ts} slot {slot}: {fname}")
                continue
            named_ok += 1
            absent = missing_frame_names(rec, fc, args.strict)
            if absent:
                missing_subs.append(f"T{ts} slot {slot}: {fname} — missing subs {absent}")
            invalid = invalid_frame_keys(rec, fc)
            if invalid:
                invalid_subs.append(f"T{ts} slot {slot}: {fname} — invalid sub keys {invalid}")
        # on-disk custom art neither registered nor aliased/named
        for p in sorted((tsroot / str(ts)).glob("*.sti")):
            n = p.name.lower()
            if not PL.is_custom(n) or n in reg_names:
                continue
            if n in PL.alias_keys() or PL.label_for(n):
                continue
            unregistered.append(f"T{ts}: {p.name} (on disk, unregistered, unnamed)")

    print(f"prop-label lint: {named_ok} registered custom sheets named OK across "
          f"{[ts for ts in tilesets if ts not in skipped_tilesets]}")
    if skipped_tilesets:
        print(f"Tilesets absent from default XML, skipped: {skipped_tilesets}")
    for title, rows in (("ENGINE-INVALID registrations (refused)", engine_invalid),
                        ("UNNAMED registered custom sheets", unnamed),
                        ("per-sub sheets MISSING sub names", missing_subs),
                        ("invalid per-sub keys", invalid_subs),
                        ("unregistered/unnamed custom art on disk", unregistered)):
        if rows:
            print(f"\n{title} ({len(rows)}):")
            for r in rows:
                print(f"  - {r}")

    fail = bool(unnamed) or bool(engine_invalid) or bool(invalid_subs) or (args.strict and bool(missing_subs))
    if not fail and not unregistered and not missing_subs:
        print("\nAll registered custom props are named and engine-valid.")
    return 1 if fail else 0


if __name__ == "__main__":
    raise SystemExit(main())
