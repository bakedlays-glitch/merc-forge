"""Unit tests for parse_appendix_minimal — the three latent size bugs.

Each test calls parse_appendix_minimal DIRECTLY with crafted bytes.
Tests are written FIRST (TDD RED) and should FAIL against the current
wrong-size code, then pass after the three fixes are applied.

Bugs being fixed:
  1. MapInfo tail (major<7.0): 100 bytes, NOT 99.
  2. Door table: uint8 count + 14-byte records, NOT uint16 count + 10-byte.
  3. Edgepoint element width: INT16 (2 bytes) for major<7.0, NOT hardcoded 4.
"""
import struct

import pytest

from mercwizard_core.mapforge_engine.parse_dat_ext import (
    MAP_AMBIENTLIGHTLEVEL_SAVED,
    MAP_DOORTABLE_SAVED,
    MAP_EDGEPOINTS_SAVED,
    MAP_EXITGRIDS_SAVED,
    MAP_FULLSOLDIER_SAVED,
    MAP_WORLDITEMS_SAVED,
    MAP_WORLDLIGHTS_SAVED,
    parse_appendix_minimal,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _tail_100() -> bytes:
    """100-byte _OLD_MAPCREATE_STRUCT (legacy v<7). All zeroes is fine for
    these tests — we just need to advance the cursor by exactly 100 bytes."""
    return b"\x00" * 100


def _tail_32() -> bytes:
    """32-byte MAPCREATE_STRUCT (modern v>=7). All zeroes."""
    return b"\x00" * 32


def _exitgrid_section(count: int) -> bytes:
    """uint16 count + count*12-byte exit grid records (all-zero records)."""
    return struct.pack("<H", count) + b"\x00" * (12 * count)


# ---------------------------------------------------------------------------
# Bug 1 — MapInfo tail must be 100 bytes for major<7.0, not 99
# ---------------------------------------------------------------------------

class TestTailSize:
    """Place the exitgrid section EXACTLY 100 bytes after appendix_offset.
    With tail_size=99 (the bug) the cursor is 1 byte short: it reads
    a byte from inside the tail as the exitgrid count byte[1], so eg_count
    is wrong or the subsequent records overrun. With tail_size=100 (the fix)
    the cursor lands exactly on the uint16 exitgrid count and reads correctly.
    """

    def _build(self, eg_count: int) -> bytes:
        """100-byte tail + uint16 exitgrid count + eg_count*12 records."""
        return _tail_100() + _exitgrid_section(eg_count)

    def test_v5_exitgrid_count_reads_correctly_after_100byte_tail(self):
        """With exactly 100-byte tail and eg_count=3, must read 3 exitgrids and
        not stop at 'exitgrid_records_overrun'. With tail=99, the count reads
        from offset 99 (a zero byte of the tail), but the 12*count advance
        overruns the 37 bytes that remain — so stopped_at would be non-None."""
        eg_count = 3
        data = self._build(eg_count)
        out = parse_appendix_minimal(
            data=data,
            appendix_offset=0,
            flags=MAP_EXITGRIDS_SAVED,
            major=5.0,
        )
        assert out["appendix_exitgrid_count"] == eg_count, (
            f"Expected eg_count={eg_count}, got {out['appendix_exitgrid_count']} "
            f"(stopped_at={out['appendix_parse_stopped_at']!r}) — "
            "likely tail is being skipped as 99 bytes instead of 100"
        )
        assert out["appendix_parse_stopped_at"] is None, (
            f"Parse stopped at {out['appendix_parse_stopped_at']!r}; "
            "exitgrid section should be clean after correct 100-byte tail"
        )

    def test_v5_zero_exitgrids_reads_zero_after_100byte_tail(self):
        """eg_count=0: just the tail + the uint16(0) count. Must read 0."""
        data = self._build(0)
        out = parse_appendix_minimal(
            data=data,
            appendix_offset=0,
            flags=MAP_EXITGRIDS_SAVED,
            major=5.0,
        )
        assert out["appendix_exitgrid_count"] == 0
        assert out["appendix_parse_stopped_at"] is None

    def test_v5_tail_size_mismatch_is_detectable(self):
        """Structural proof: the data is sized exactly for tail=100.
        If the impl uses tail=99, it reads 1 extra byte from the tail into
        the exitgrid section, and the cursor will land WRONG (either
        misreading the count, or overrunning). We verify the happy path
        passes cleanly so that any failure isolates to the tail size."""
        # Build 100B tail + count=1 + 12B record — total 114 bytes
        data = _tail_100() + _exitgrid_section(1)
        assert len(data) == 114
        out = parse_appendix_minimal(
            data=data,
            appendix_offset=0,
            flags=MAP_EXITGRIDS_SAVED,
            major=5.0,
        )
        assert out["appendix_exitgrid_count"] == 1
        assert out["appendix_parse_stopped_at"] is None

    def test_v7_tail_size_32_unchanged(self):
        """v7+ tail = 32 bytes. This is already correct in the code; must
        remain correct after the fix (don't accidentally break it)."""
        # 32B tail + count=2 + 24B records
        data = _tail_32() + _exitgrid_section(2)
        out = parse_appendix_minimal(
            data=data,
            appendix_offset=0,
            flags=MAP_EXITGRIDS_SAVED,
            major=7.0,
        )
        assert out["appendix_exitgrid_count"] == 2
        assert out["appendix_parse_stopped_at"] is None


# ---------------------------------------------------------------------------
# Bug 2 — Door table: uint8 count + 14-byte records, not uint16 + 10-byte
# ---------------------------------------------------------------------------

class TestDoorTable:
    """Place the door table after the 100-byte tail (no soldiers, no exitgrids).
    The count byte is a uint8 (1 byte), and each record is 14 bytes.
    With the bug (uint16 + 10B), the count reads 2 bytes and records are 10B,
    so the cursor overshoots and the result is wrong or the parse overruns.
    """

    def _build(self, door_count: int) -> bytes:
        """100-byte tail + uint8 door count + door_count*14-byte records."""
        data = _tail_100()
        data += bytes([door_count])           # uint8 count (1 byte)
        data += b"\x00" * (14 * door_count)  # 14-byte records
        return data

    def test_v5_door_count_uint8_read_correctly(self):
        """door_count=2 must be read as 2, and parse must succeed cleanly.
        With bug (uint16 read): the count byte + the first byte of the first
        record form uint16=0x0002 (still 2 on LE), BUT the stride is 10
        instead of 14, so the cursor lands 2*4=8 bytes short, and reads past
        end. Actually: uint16 consumes 2 bytes where only 1 is the count, then
        10*2=20 bytes for records (total 22) vs correct 1+14*2=29 — different
        offsets. The check is: stopped_at is None AND count == 2."""
        door_count = 2
        data = self._build(door_count)
        out = parse_appendix_minimal(
            data=data,
            appendix_offset=0,
            flags=MAP_DOORTABLE_SAVED,
            major=5.0,
        )
        assert out["appendix_doortable_count"] == door_count, (
            f"Expected door count={door_count}, got {out['appendix_doortable_count']} "
            f"(stopped_at={out['appendix_parse_stopped_at']!r})"
        )
        assert out["appendix_parse_stopped_at"] is None, (
            f"Parse stopped at {out['appendix_parse_stopped_at']!r}; "
            "door table should be clean with uint8 count + 14B records"
        )

    def test_v5_door_zero_count_succeeds(self):
        """door_count=0: 100B tail + 1B uint8(0). No records. Should pass."""
        data = _tail_100() + bytes([0])
        out = parse_appendix_minimal(
            data=data,
            appendix_offset=0,
            flags=MAP_DOORTABLE_SAVED,
            major=5.0,
        )
        assert out["appendix_doortable_count"] == 0
        assert out["appendix_parse_stopped_at"] is None

    def test_v5_door_5_records_14bytes_each_no_overrun(self):
        """5 doors * 14B = 70 bytes + 1B count + 100B tail = 171 bytes total.
        Must read count=5 and not overrun."""
        door_count = 5
        data = self._build(door_count)
        assert len(data) == 100 + 1 + 14 * 5  # 171
        out = parse_appendix_minimal(
            data=data,
            appendix_offset=0,
            flags=MAP_DOORTABLE_SAVED,
            major=5.0,
        )
        assert out["appendix_doortable_count"] == 5
        assert out["appendix_parse_stopped_at"] is None

    def test_v5_door_truncation_guard_uses_1byte_count(self):
        """If data is cut off BEFORE the uint8 count byte, must bail with
        'doortable_count_truncated' (not an index error). This also proves
        the guard checks pos+1 not pos+2."""
        data = _tail_100()  # no count byte at all
        out = parse_appendix_minimal(
            data=data,
            appendix_offset=0,
            flags=MAP_DOORTABLE_SAVED,
            major=5.0,
        )
        assert out["appendix_parse_stopped_at"] == "doortable_count_truncated"

    def test_v5_door_count_distinguishable_from_uint16(self):
        """Key structural proof: place count=1 (byte value 0x01) followed
        immediately by 14 zero bytes. If the impl reads uint16, it consumes
        [0x01, 0x00] = 1, then advances 10 bytes for the record — leaving 4
        bytes in the buffer unconsumed (cursor lands at 100+2+10=112, but we
        only gave 100+1+14=115 bytes, so it actually doesn't overrun there).
        But the SECOND test: count=3, 3*14=42 bytes. With uint16 read,
        count byte [0x03] followed by first record byte [0x00] = uint16=0x0003,
        then 10*3=30 bytes consumed, but we gave 1+42=43 bytes for that section
        (total 143). 2+30=32 vs 1+42=43 — cursor at 132 vs 143, so no overrun,
        but the KEY difference is the count field: uint16 reads count as 3 (same
        as uint8 in this case because of LE and the next byte is 0). The REAL
        distinguishing test is the truncation guard: with uint16 the guard is
        pos+2>end; with uint8 the guard is pos+1>end. So build data of exactly
        tail+1byte and check it doesn't bail with truncated (since 1 byte IS
        enough for a uint8 count of 0)."""
        # Exactly tail(100) + 1 byte (uint8=0). This is enough for uint8 read
        # (count=0 doors) but NOT for uint16 read (which needs 2 bytes for count).
        data = _tail_100() + bytes([0])
        out = parse_appendix_minimal(
            data=data,
            appendix_offset=0,
            flags=MAP_DOORTABLE_SAVED,
            major=5.0,
        )
        # With old code (uint16): pos+2>end at pos=100, end=101 → 102>101 → BAIL
        # With new code (uint8): pos+1>end at pos=100, end=101 → 101>101 is False → count=0 OK
        assert out["appendix_doortable_count"] == 0
        assert out["appendix_parse_stopped_at"] is None, (
            "With a 1-byte uint8 count of 0 the parse should succeed; "
            "failure means the impl is still reading 2 bytes for the count (uint16 bug)"
        )


# ---------------------------------------------------------------------------
# Bug 3 — Edgepoint element: INT16 (2 bytes) for major<7.0, not INT32 (4)
# ---------------------------------------------------------------------------

class TestEdgepoints:
    """8 edgepoint sections, each: uint16 size + uint16 middle + size*element.
    For major<7.0 the element is int16 (2 bytes); for major>=7.0 it is int32 (4).
    Current code hardcodes record_size=4 (wrong for v5).
    """

    def _edge_section(self, gridnos: list, middle: int = 0) -> bytes:
        """uint16 size + uint16 middle + size*int16 (legacy v<7 format)."""
        hdr = struct.pack("<HH", len(gridnos), middle)
        body = b"".join(struct.pack("<h", g) for g in gridnos)
        return hdr + body

    def _build_v5_edgepoints(self, sections: list) -> bytes:
        """100-byte tail + 8 edge sections (list of 8 lists of gridnos)."""
        data = _tail_100()
        for gn_list in sections:
            data += self._edge_section(gn_list)
        return data

    def test_v5_edgepoint_count_int16_elements(self):
        """Single populated section (north primary) with 2 gridnos in int16.
        With bug (record_size=4): 2*4=8 bytes consumed, but we only gave 2*2=4
        → overrun → bail. With fix (record_size=2): 2*2=4 bytes, no overrun."""
        # North has 2 gridnos; remaining 7 sections empty
        sections = [[100, 200]] + [[] for _ in range(7)]
        data = self._build_v5_edgepoints(sections)
        out = parse_appendix_minimal(
            data=data,
            appendix_offset=0,
            flags=MAP_EDGEPOINTS_SAVED,
            major=5.0,
        )
        assert out["appendix_edgepoint_count"] == 2, (
            f"Expected 2 edgepoints, got {out['appendix_edgepoint_count']} "
            f"(stopped_at={out['appendix_parse_stopped_at']!r}) — "
            "likely record_size is still 4 instead of 2 for major<7.0"
        )
        assert out["appendix_parse_stopped_at"] is None, (
            f"Parse stopped at {out['appendix_parse_stopped_at']!r}; "
            "v5 edgepoints with int16 elements should not overrun"
        )

    def test_v5_edgepoint_all_8_sections(self):
        """All 8 sections populated. Total count = sum of all gridnos."""
        # 8 sections with 1, 2, 3, 0, 1, 0, 2, 1 = 10 total
        sections = [[10], [20, 30], [40, 50, 60], [], [70], [], [80, 90], [100]]
        data = self._build_v5_edgepoints(sections)
        out = parse_appendix_minimal(
            data=data,
            appendix_offset=0,
            flags=MAP_EDGEPOINTS_SAVED,
            major=5.0,
        )
        assert out["appendix_edgepoint_count"] == 10
        assert out["appendix_parse_stopped_at"] is None

    def test_v5_edgepoint_empty_sections_succeed(self):
        """All 8 sections empty (size=0). Must read 0 edges cleanly."""
        sections = [[] for _ in range(8)]
        data = self._build_v5_edgepoints(sections)
        out = parse_appendix_minimal(
            data=data,
            appendix_offset=0,
            flags=MAP_EDGEPOINTS_SAVED,
            major=5.0,
        )
        assert out["appendix_edgepoint_count"] == 0
        assert out["appendix_parse_stopped_at"] is None

    def test_v7_edgepoint_still_uses_int32(self):
        """v7+ edges use int32 (4 bytes). Build using 4-byte elements, confirm
        the fix doesn't break the v7 path."""
        def edge32(gridnos, middle=0):
            hdr = struct.pack("<HH", len(gridnos), middle)
            body = b"".join(struct.pack("<i", g) for g in gridnos)
            return hdr + body

        data = _tail_32()
        data += edge32([1000, 2000])  # north: 2 int32 gridnos
        for _ in range(7):
            data += edge32([])
        out = parse_appendix_minimal(
            data=data,
            appendix_offset=0,
            flags=MAP_EDGEPOINTS_SAVED,
            major=7.0,
        )
        assert out["appendix_edgepoint_count"] == 2
        assert out["appendix_parse_stopped_at"] is None


# ---------------------------------------------------------------------------
# Combined: all three bugs in one v5 map with no blockers
# ---------------------------------------------------------------------------

class TestCombinedV5:
    """A v5 (major=5.0) synthetic map with DOORTABLE | EXITGRIDS | EDGEPOINTS.
    No items, no soldiers, no schedules. The parse should walk all three sections
    cleanly once all three bugs are fixed."""

    def test_v5_door_exit_edge_all_pass(self):
        # 100-byte tail
        data = _tail_100()

        # exitgrids: uint16 count=1 + 12-byte record
        data += _exitgrid_section(1)

        # doortable: uint8 count=1 + 14-byte record
        data += bytes([1]) + b"\x00" * 14

        # edgepoints: 8 sections, north has 1 int16 gridno, rest empty
        data += struct.pack("<HH", 1, 0) + struct.pack("<h", 100)
        for _ in range(7):
            data += struct.pack("<HH", 0, 0)

        flags = MAP_EXITGRIDS_SAVED | MAP_DOORTABLE_SAVED | MAP_EDGEPOINTS_SAVED
        out = parse_appendix_minimal(
            data=data,
            appendix_offset=0,
            flags=flags,
            major=5.0,
        )
        assert out["appendix_exitgrid_count"] == 1
        assert out["appendix_doortable_count"] == 1
        assert out["appendix_edgepoint_count"] == 1
        assert out["appendix_parse_stopped_at"] is None
