"""*** RETRACTED — DO NOT RUN (hard-stops at import). ***

Original (WRONG) intent: register gecko_plant_floors.sti at T72 MOCKFLOOR slot 72
as a supposedly-free additive slot. It is not free.

Slot 72 was never free: MOCKFLOOR is
an engine-addressed marker type (reservation 1 in gNumTilesPerType, TileDat.cpp;
Exit Grids.cpp:102 and Interface Cursors.cpp:15 use tile MOCKFLOOR1 as the
exit-grid / AP-cursor marker, art inherited from T0 highl3.sti). This registration
displaced that tile and only sub 1 of the 3-sub sheet could load. prop_labels_lint
now refuses it; the re-home is a separate scoped T72 edit on Will's word. Kept only
as the record of the T72-block-scoped edit pattern (backup outside Data-1.13).

Backup goes OUTSIDE Data-1.13 (VFS-scope lesson). Scoped edit — only the
<Tileset index="72"> block is touched; the rest of the file stays byte-exact.
"""
raise SystemExit("RETRACTED 2026-09-07: slot 72 is MOCKFLOOR (engine exit-grid/AP-cursor marker); "
                 "gecko_plant_floors lives at T72 slot 63 now -- see the docstring, do not re-run.")

import os
import re, shutil, sys, hashlib  # noqa: E402  (unreachable; kept as the record of the scoped-edit pattern)
from datetime import datetime
from pathlib import Path

CP = Path(os.environ.get("JA2_INSTALL", ""))
XML = CP / "Data-1.13" / "Ja2Set.dat.xml"
STI = CP / "Data-1.13" / "tilesets" / "72" / "gecko_plant_floors.sti"
BKROOT = CP / "_wasteland_xml_backups"   # OUTSIDE Data-1.13
NEW_LINE = '\t\t\t\t<file index="72">gecko_plant_floors.sti</file>'
DO = "--write" in sys.argv

assert STI.exists(), f"missing art: {STI}"
orig = XML.read_text(encoding="utf-8")
# 1) isolate the T72 block (non-greedy)
bm = re.search(r'<Tileset index="72".*?</Tileset>', orig, re.S)
assert bm, "T72 block not found"
blk = bm.group(0)
assert '<file index="72">' not in blk, "slot 72 file already registered in T72"
# 2) within the block, insert the new line before the block's </Files>
im = re.search(r'(\s*)</Files>', blk)
assert im, "T72 </Files> not found"
new_blk = blk[:im.start()] + "\n" + NEW_LINE + im.group(0) + blk[im.end():]
new_xml = orig[:bm.start()] + new_blk + orig[bm.end():]

# safety: exactly one line added, nothing else changed
added = len(new_xml) - len(orig)
assert new_xml.replace(NEW_LINE + "\n\t\t\t", "").replace("\t\t\t", "", 0) or True
diff_lines = [l for l in new_xml.splitlines() if l not in orig.splitlines()]
print(f"lines added: {[l.strip() for l in diff_lines]}")
assert diff_lines == [NEW_LINE], f"unexpected extra changes: {diff_lines}"

# verify the parse resolves slot 72
sys.path.insert(0, str(CP.parent.parent))  # not needed; parse inline
import xml.etree.ElementTree as ET
root = ET.fromstring(new_xml)
got = None
for t in root.iter("Tileset"):
    if int(t.get("index", -1)) == 72:
        for f in t.find("Files").findall("file"):
            if int(f.get("index")) == 72:
                got = (f.text or "").strip()
assert got == "gecko_plant_floors.sti", f"slot 72 resolves to {got!r}"
print(f"verify: T72 slot 72 -> {got}")

if DO:
    BKROOT.mkdir(exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    bk = BKROOT / f"Ja2Set.dat.xml.bak_{ts}_gecko_plant_floors"
    shutil.copy2(XML, bk)
    XML.write_text(new_xml, encoding="utf-8", newline="")
    print(f"WROTE {XML}")
    print(f"backup (outside Data-1.13): {bk}")
    print(f"new sha256: {hashlib.sha256(XML.read_bytes()).hexdigest()[:16]}")
else:
    print("dry-run OK (pass --write to apply)")
