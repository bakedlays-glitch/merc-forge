"""Door-table appendix record tests for Lane A11 (review findings F13/F14).

appendix_extract.py's DOOR TABLE section must branch on the map's major
version exactly like the engine's DOOR::Load (Keys.cpp:841-848) does --
14-byte _OLD_DOOR records for major<7.0, 12-byte DOOR records for
major>=7.0 -- instead of assuming 14 bytes unconditionally, which misread
every door on a major>=7.0 map (a9.dat: 34 doors, 68-byte drift).

Fixtures are real appendix tails sliced from the live Copy install; see
fixtures/appendix/README.md for provenance + sha256. Field-layout note: the
new (12-byte) DOOR record widens sGridNo from INT16 to INT32, which also
moves fLocked from +2 to +4 -- not just a stride change.
"""
import json
import struct
from pathlib import Path

from mercwizard_core.mapforge_engine.appendix_extract import extract_appendix_entities

FIXTURES = Path(__file__).parent / "fixtures" / "appendix"


def _load(stem):
    tail = (FIXTURES / f"{stem}_tail.bin").read_bytes()
    meta = json.loads((FIXTURES / f"{stem}_meta.json").read_text(encoding="utf-8"))
    parsed = {
        "flags": meta["flags"], "major": meta["major"], "minor": meta["minor"],
        "cols": meta["cols"], "rows": meta["rows"], "appendix_offset": 0,
    }
    return tail, parsed


def test_a9_v7_doors_use_12_byte_records_and_land_exactly_on_eof():
    """a9.dat (major 7.0): DOOR class = 12-byte records. The old
    hardcoded-14 code drifted the cursor on every door after the first;
    the fix must consume the door table exactly to the fixture's EOF (the
    door table is a9's last appendix section, so its 1-byte count + 34*12
    must end precisely at len(data), with nothing left over)."""
    data, parsed = _load("a9_v7_34doors")
    out = extract_appendix_entities(data, parsed)
    assert out["blocked_at"] is None
    assert out["door_record_size"] == 12
    assert len(out["doors"]) == 34
    # Door table is a9's last section: walking backward by (1 count byte +
    # 34*12 record bytes) from EOF must land exactly on a byte equal to 34
    # -- confirms the reader didn't just get the COUNT right by luck while
    # drifting the byte offset (a wrong record size would make this land on
    # an arbitrary byte, not coincidentally re-read 34).
    doors_count_pos = len(data) - (1 + 34 * 12)
    assert data[doors_count_pos] == 34
    # Witness-vote-verified gridnos (ascending on this sector) -- regression pin.
    assert [d["gridno"] for d in out["doors"][:5]] == [8238, 8260, 8262, 8742, 9014]
    assert out["doors"][1]["locked"] is True


def test_a2_v5_doors_use_14_byte_old_records():
    """A2.DAT (major 5.0): _OLD_DOOR = 14-byte records (INT16 gridno @+0,
    fLocked @+2) -- this path was already correct before the fix; pinned
    here so a future change can't quietly widen it to match the new layout."""
    data, parsed = _load("A2_v5_3doors")
    out = extract_appendix_entities(data, parsed)
    assert out["blocked_at"] is None
    assert out["door_record_size"] == 14
    assert len(out["doors"]) == 3
    assert [d["gridno"] for d in out["doors"]] == [18953, 15621, 18475]
    assert all(d["locked"] for d in out["doors"])


def test_hardcoded_14_byte_stride_would_have_misread_a9_v7_doors():
    """Guard rail: prove the F13 defect was real. Re-run a9's door table
    with the OLD hardcoded-14-byte math (what appendix_extract.py did
    before this fix) and show it disagrees with the fixed 12-byte reader
    and never lands on the fixture's true EOF. The door table's byte
    position isn't 0 -- a9's tail also carries lights/mapinfo/exitgrids
    ahead of it -- so it's derived from the (independently verified) fixed
    parse: doortable is the last section, so
    len(data) - (1 + n_doors*record_size) is its count byte."""
    data, parsed = _load("a9_v7_34doors")
    out = extract_appendix_entities(data, parsed)
    n_doors = len(out["doors"])
    doors_count_pos = len(data) - (1 + n_doors * out["door_record_size"])
    dt_count = data[doors_count_pos]
    assert dt_count == n_doors == 34
    pos = doors_count_pos + 1
    bad_gridnos = []
    for _ in range(dt_count):
        if pos + 14 > len(data):
            break  # the old code would bail with doortable_records_overrun here
        bad_gridnos.append(struct.unpack_from("<h", data, pos)[0])
        pos += 14
    good_gridnos = [d["gridno"] for d in out["doors"]]
    assert bad_gridnos != good_gridnos[: len(bad_gridnos)]
    assert pos != len(data)  # the old 14-byte stride never lands on the true EOF


def test_doortable_overrun_bails_hard_not_silent():
    """Truncating a real door table mid-record must set blocked_at and
    keep only the whole records already parsed -- never a partial record
    or a silently wrong count."""
    data, parsed = _load("a9_v7_34doors")
    full = extract_appendix_entities(data, parsed)
    doors_count_pos = len(data) - (1 + len(full["doors"]) * full["door_record_size"])
    truncated = data[: doors_count_pos + 1 + 12 * 5]  # count byte (says 34) + 5 whole 12B records
    out = extract_appendix_entities(truncated, parsed)
    assert out["blocked_at"] == "doortable_records_overrun"
    assert len(out["doors"]) == 5
