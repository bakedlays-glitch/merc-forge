"""Re-home gecko_plant_floors.sti from T72 slot 72 (MOCKFLOOR) to slot 63 (FOURTHFLOOR).

Why: MOCKFLOOR's engine reservation is 1 and the engine addresses that single
tile by index as the exit-grid / AP-cursor marker (Exit Grids.cpp:102,
Interface Cursors.cpp:15), so the registration at slot 72 displaced
the marker and only sub 1 of the 3-sub sheet could load. FOURTHFLOOR (63) is a
FLOOR type (reservation 8, FLAT_FLOOR terrain, GridNoIndoors) — the semantics
the sheet's interior-floor labels describe. It displaces gecko_cvflr4.sti (cave
floor, 35 subs over an 8 reservation), which no installed or drafted surface map
places and which the cave design moves to its own underground tileset.

Edit is confined to the <Tileset index="72"> block: exactly one line removed
(slot 72) and one line changed (slot 63). Every other byte of the file is
asserted identical. Backup goes OUTSIDE Data-1.13 (VFS directory-scope hash).

    python tools/rehome_gecko_plant_floors_slot63.py           # dry-run, prints the diff
    python tools/rehome_gecko_plant_floors_slot63.py --write   # backup + write
"""
from __future__ import annotations

import os
import hashlib
import re
import shutil
import sys
from datetime import datetime
from pathlib import Path

CP = Path(os.environ.get("JA2_INSTALL", ""))
XML = CP / "Data-1.13" / "Ja2Set.dat.xml"
STI = CP / "Data-1.13" / "tilesets" / "72" / "gecko_plant_floors.sti"
BKROOT = CP / "_wasteland_xml_backups"  # OUTSIDE Data-1.13

OLD_72 = '<file index="72">gecko_plant_floors.sti</file>'
OLD_63 = '<file index="63">gecko_cvflr4.sti</file>'
NEW_63 = '<file index="63">gecko_plant_floors.sti</file>'


def rehome(orig: str) -> str:
    bm = re.search(r'<Tileset index="72">.*?</Tileset>', orig, re.S)
    assert bm, "T72 block not found"
    blk = bm.group(0)
    assert blk.count(OLD_72) == 1, "slot 72 registration not found exactly once in T72"
    assert blk.count(OLD_63) == 1, "slot 63 gecko_cvflr4 not found exactly once in T72"
    # drop the whole slot-72 line (its leading newline + indent go with it)
    new_blk, n = re.subn(r'\r?\n[ \t]*' + re.escape(OLD_72), "", blk)
    assert n == 1, n
    new_blk = new_blk.replace(OLD_63, NEW_63)
    return orig[: bm.start()] + new_blk + orig[bm.end():]


def main() -> int:
    do = "--write" in sys.argv
    assert STI.exists(), f"missing art: {STI}"
    with open(XML, "r", encoding="utf-8", newline="") as fh:
        orig = fh.read()
    new = rehome(orig)

    o_lines, n_lines = orig.splitlines(), new.splitlines()
    removed = [l.strip() for l in o_lines if l not in n_lines]
    added = [l.strip() for l in n_lines if l not in o_lines]
    print("removed:", removed)
    print("added:  ", added)
    assert sorted(removed) == sorted([OLD_72, OLD_63]), removed
    assert added == [NEW_63], added
    assert len(o_lines) - len(n_lines) == 1, "exactly one line must disappear"
    # other tileset blocks byte-identical
    for m in re.finditer(r'<Tileset index="(\d+)">.*?</Tileset>', orig, re.S):
        if m.group(1) != "72":
            assert m.group(0) in new, f"tileset {m.group(1)} block changed"
    assert new.count(OLD_72) == 0 and orig.count(NEW_63) == 0 and new.count(NEW_63) == 1

    if not do:
        print("dry-run OK (pass --write to apply)")
        return 0
    BKROOT.mkdir(exist_ok=True)
    bk = BKROOT / f"Ja2Set.dat.xml.bak_{datetime.now():%Y%m%d_%H%M%S}_pre_plant_floors_slot63"
    shutil.copy2(XML, bk)
    with open(XML, "w", encoding="utf-8", newline="") as fh:
        fh.write(new)
    print(f"WROTE {XML}")
    print(f"backup (outside Data-1.13): {bk}")
    print(f"new sha256: {hashlib.sha256(XML.read_bytes()).hexdigest()[:16]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
