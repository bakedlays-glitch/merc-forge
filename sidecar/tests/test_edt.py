"""Tests for EDT encoding + the AIMBIOS bug fix routing."""
from __future__ import annotations

import struct
from pathlib import Path

import pytest

from mercwizard_core.inject import edt


# ──────────────────────────────────────────────────────────────────────────
#  Encoding tests
# ──────────────────────────────────────────────────────────────────────────

def test_round_trip_ascii() -> None:
    text = "Hello world. This is a test biography."
    encoded = edt.encode_field(text, edt.BIO_FIELD_SIZE, edt.BIO_CHAR_MAX)
    decoded = edt.decode_field(encoded)
    assert decoded == text


def test_space_is_not_shifted() -> None:
    """Critical: space (ord 32) must NOT be shifted by ROT+1.
    Naïve ROT+1 makes spaces into '!' and breaks the bio renderer.
    """
    encoded = edt.encode_field(" ", edt.BIO_FIELD_SIZE, edt.BIO_CHAR_MAX)
    # Space stays at 0x20
    assert encoded[0] == 0x20
    assert encoded[1] == 0x00


def test_printable_chars_are_shifted_by_one() -> None:
    """ord('H') == 72, ROT+1 → 73 (0x49)"""
    encoded = edt.encode_field("H", edt.BIO_FIELD_SIZE, edt.BIO_CHAR_MAX)
    assert encoded[0] == 0x49


def test_bio_truncates_at_400() -> None:
    long_text = "x" * 500
    encoded = edt.encode_field(long_text, edt.BIO_FIELD_SIZE, edt.BIO_CHAR_MAX)
    # All 400 chars encoded (each char is 'x'=120; ROT+1 → 121 = 0x79)
    decoded = edt.decode_field(encoded)
    assert len(decoded) == 400


def test_record_size_exactly_1120() -> None:
    record = edt.encode_record("bio", "additional")
    assert len(record) == edt.RECORD_SIZE
    assert edt.RECORD_SIZE == 1120


def test_record_round_trip() -> None:
    bio = "Test biography with various chars: ABC 123 !@#$%^&*()"
    addl = "Additional info goes here."
    record = edt.encode_record(bio, addl)
    decoded_bio, decoded_addl = edt.decode_record(record)
    assert decoded_bio == bio
    assert decoded_addl == addl


# ──────────────────────────────────────────────────────────────────────────
#  Routing tests — the bug fix at the core of this rewrite
# ──────────────────────────────────────────────────────────────────────────

def test_vanilla_aim_slot_routes_to_aimbios_with_uiindex(fake_install: Path) -> None:
    """Slot 5 (vanilla AIM) → AIMBIOS.EDT at offset 5×1120."""
    route = edt.route_bio(fake_install, ui_index=5)
    assert route.kind == "aimbios"
    assert route.path.name == "AIMBIOS.EDT"
    assert route.record_index == 5
    assert route.offset == 5 * 1120


def test_vanilla_merc_slot_routes_to_mercbios(fake_install: Path) -> None:
    """Slot 45 (vanilla MERC) → MERCBIOS.EDT at offset (45-40)×1120 = 5600."""
    route = edt.route_bio(fake_install, ui_index=45)
    assert route.kind == "mercbios"
    assert route.path.name == "MERCBIOS.EDT"
    assert route.record_index == 5
    assert route.offset == 5 * 1120


def test_type2_at_vanilla_aim_slot_routes_to_mercbios_not_aimbios(fake_install: Path) -> None:
    """THE VANILLA TYPE-MISMATCH BUG FIX.

    A Type=2 (M.E.R.C.) merc placed at vanilla AIM slot 5 (Reaper's slot
    in stock JA2 1.13) must route to MERCBIOS at the supplied
    merc_bio_id × 1120 — NOT to AIMBIOS at slot 5 × 1120. The earlier
    behavior wrote the M.E.R.C. merc's bio over Reaper's vanilla AIM
    bio while the engine's M.E.R.C. laptop read MERCBIOS at the new
    merc_bio_id and found empty bytes. Silent vanilla-data corruption
    on every save of a Type=2 merc at any slot 0-39.

    routes/merc.py signals "Type=2 at vanilla AIM slot" by passing
    aim_bio_id=None + merc_bio_id=<new>; the route_bio convention is
    to read the bio_id shape as a Type proxy.
    """
    route = edt.route_bio(
        fake_install, ui_index=5, aim_bio_id=None, merc_bio_id=42
    )
    assert route.kind == "mercbios"
    assert route.path.name == "MERCBIOS.EDT"
    assert route.record_index == 42
    assert route.offset == 42 * 1120
    # And critically, NOT this — would clobber Reaper.
    assert route.offset != 5 * 1120


def test_type1_at_vanilla_merc_slot_routes_to_aimbios_not_mercbios(fake_install: Path) -> None:
    """Mirror of the Type=2-at-vanilla-AIM fix.

    A Type=1 (AIM) merc placed at vanilla MERC slot 45 (Tony's slot in
    stock JA2 1.13) must route to AIMBIOS at the supplied aim_bio_id ×
    1120 — NOT to MERCBIOS at (45 − 40) × 1120 = offset 5600. The earlier
    behavior clobbered Tony's vanilla M.E.R.C. bio.
    """
    route = edt.route_bio(
        fake_install, ui_index=45, aim_bio_id=71, merc_bio_id=None
    )
    assert route.kind == "aimbios"
    assert route.path.name == "AIMBIOS.EDT"
    assert route.record_index == 71
    assert route.offset == 71 * 1120
    # And critically, NOT this — would clobber Tony.
    assert route.offset != 5 * 1120


def test_expanded_aim_slot_175_uses_aim_bio_id_not_ui_index(fake_install: Path) -> None:
    """THE BUG FIX TEST.

    Slot 175 with AimBioID 45 must route to AIMBIOS.EDT at offset 45×1120
    (NOT 175×1120). compile_merc.py uses 175×1120 — that's the bug we're
    fixing. AimBioID must drive the offset.
    """
    route = edt.route_bio(fake_install, ui_index=175, aim_bio_id=45)
    assert route.kind == "aimbios"
    assert route.path.name == "AIMBIOS.EDT"
    assert route.record_index == 45  # AimBioID, NOT uiIndex
    assert route.offset == 45 * 1120
    # And critically, NOT this:
    assert route.offset != 175 * 1120


def test_expanded_aim_without_aim_bio_id_raises_on_write(fake_install: Path) -> None:
    """We refuse to silently route a WRITE to the wrong offset.

    The for_write=True path raises ValueError when bio_ids are missing
    for an expansion-AIM slot — silent mis-routing on writes was the
    original compile_merc.py bug being fixed.
    """
    with pytest.raises(ValueError, match="aim_bio_id"):
        edt.route_bio(fake_install, ui_index=175, for_write=True)


def test_expanded_aim_without_aim_bio_id_degrades_for_read(fake_install: Path) -> None:
    """Reads fall back to per-file NPC EDT when both bio_ids are None.

    Bug-review finding C3: relocator.move_within_install / duplicate
    call read_bio (for_write=False default) on expansion-AIM slots
    without try/except. Pre-fix this raised hard and broke Move /
    Duplicate on minimal installs without the AIM binding wired.
    Reads now degrade gracefully to the per-file NPC EDT path so
    callers can still surface whatever bio bytes are on disk.
    """
    route = edt.route_bio(fake_install, ui_index=175, for_write=False)
    assert route.kind == "per_file_npc"
    assert route.path.name == "175.EDT"


def test_expanded_merc_slot_with_merc_bio_id_routes_to_mercbios(fake_install: Path) -> None:
    """THE MERC ROUTING BUG FIX.

    Slot 178 with MercBioID 42 must route to MERCBIOS.EDT at offset 42×1120,
    NOT to MercEdt/178.EDT. The engine reads MERCBIOS.EDT for every Type=2
    merc — vanilla AND expansion. MercWizard 1.x wrote to MercEdt/ and the
    engine silently ignored those bytes, leaving expansion mercs showing
    whichever bio happened to live at offset (MercBioID × 1120).
    """
    route = edt.route_bio(fake_install, ui_index=178, merc_bio_id=42)
    assert route.kind == "mercbios"
    assert route.path.name == "MERCBIOS.EDT"
    assert route.record_index == 42
    assert route.offset == 42 * 1120


def test_unassigned_slot_with_aim_bio_id_routes_to_aimbios(fake_install: Path) -> None:
    """Regression: slot 203 (unassigned in vanilla, AIM-bound in Vengeance)
    with aim_bio_id=71 must route to AIMBIOS.EDT, NOT NPCDATA. The pre-fix
    code fell through `is_expanded_aim` (false for 203, not in the canonical
    AIM groups) and silently wrote to NPCDATA/203.EDT — the engine never
    reads that file for the AIM website display.
    """
    route = edt.route_bio(fake_install, ui_index=203, aim_bio_id=71)
    assert route.kind == "aimbios"
    assert route.path.name == "AIMBIOS.EDT"
    assert route.record_index == 71
    assert route.offset == 71 * 1120


def test_unassigned_slot_aim_routing_rejects_out_of_range_aim_bio_id(fake_install: Path) -> None:
    """Defense in depth: aim_bio_id > 199 raises rather than corrupting bytes
    past the engine's 200-record cap.
    """
    with pytest.raises(ValueError, match="aim_bio_id"):
        edt.route_bio(fake_install, ui_index=203, aim_bio_id=250)


def test_expanded_merc_slot_without_merc_bio_id_falls_back_for_read(fake_install: Path) -> None:
    """Reads degrade gracefully to per-file EDT when merc_bio_id is missing.

    Existing installs may carry per-file EDTs from the pre-fix wizard era;
    we need to read those for export/migration without forcing the caller
    to know the MercBioID upfront.
    """
    route = edt.route_bio(fake_install, ui_index=178, for_write=False)
    assert route.kind == "per_file_merc"
    assert route.path.name == "178.EDT"
    assert route.path.parent.name == "MercEdt"
    assert route.record_index == 0


def test_expanded_merc_slot_write_without_merc_bio_id_raises(fake_install: Path) -> None:
    """Writes without merc_bio_id refuse — same contract as the AIM fix."""
    with pytest.raises(ValueError, match="merc_bio_id"):
        edt.route_bio(fake_install, ui_index=178, for_write=True)


def test_scattered_aim_with_aim_bio_id(fake_install: Path) -> None:
    """Slot 235 (Leech in vanilla, AimBioID 56)."""
    route = edt.route_bio(fake_install, ui_index=235, aim_bio_id=56)
    assert route.kind == "aimbios"
    assert route.offset == 56 * 1120


def test_npc_range_routes_to_per_file_npc(fake_install: Path) -> None:
    """Slot 100 (NPC) → NPCDATA/100.EDT."""
    route = edt.route_bio(fake_install, ui_index=100)
    assert route.kind == "per_file_npc"
    assert route.path.name == "100.EDT"


def test_out_of_range_ui_index_raises(fake_install: Path) -> None:
    with pytest.raises(ValueError, match="uiIndex"):
        edt.route_bio(fake_install, ui_index=256)
    with pytest.raises(ValueError, match="uiIndex"):
        edt.route_bio(fake_install, ui_index=-1)


# ──────────────────────────────────────────────────────────────────────────
#  Write / read tests
# ──────────────────────────────────────────────────────────────────────────

def test_write_then_read_aimbios_record(fake_install: Path) -> None:
    bio = "A grizzled desert ranger."
    addl = "Marksman tier 5."
    edt.write_bio(fake_install, ui_index=5, biography=bio, additional=addl)
    got_bio, got_addl = edt.read_bio(fake_install, ui_index=5)
    assert got_bio == bio
    assert got_addl == addl


def test_write_expanded_aim_with_aim_bio_id(fake_install: Path) -> None:
    """Round-trip a bio at slot 234 (Jimmy, AimBioID 52) — uses scattered AIM routing.

    Slot 234 is in the canonical SCATTERED_AIM_SLOTS set, so it routes to
    AIMBIOS.EDT at offset AimBioID×1120 = 52*1120 = 58240. This is the bug
    fix in action — vanilla compile_merc.py would write at 234*1120 = 262080.
    """
    bio = "Jimmy's bio"
    edt.write_bio(fake_install, ui_index=234, biography=bio, additional="", aim_bio_id=52)
    got_bio, _ = edt.read_bio(fake_install, ui_index=234, aim_bio_id=52)
    assert got_bio == bio
    # The file's AIMBIOS.EDT should be at least 53 records long (record 52 + 1)
    aimbios = fake_install / "Data-1.13" / "BinaryData" / "AIMBIOS.EDT"
    assert aimbios.is_file()
    assert aimbios.stat().st_size >= 53 * 1120
    # And critically, NOT 235 records (the would-be wrong-offset size)
    assert aimbios.stat().st_size < 235 * 1120


def test_clear_record_zeros_bytes(fake_install: Path) -> None:
    edt.write_bio(fake_install, ui_index=10, biography="will be cleared", additional="info")
    edt.clear_bio(fake_install, ui_index=10)
    got_bio, got_addl = edt.read_bio(fake_install, ui_index=10)
    assert got_bio == ""
    assert got_addl == ""


def test_write_expanded_merc_with_merc_bio_id(fake_install: Path) -> None:
    """Round-trip a bio at slot 178 (expansion MERC, MercBioID 42).

    Slot 178 with MercBioID=42 should write to MERCBIOS.EDT at offset
    42 × 1120 = 47,040. MercWizard 1.x would have written to MercEdt/178.EDT
    instead — the engine reads MERCBIOS at 42 × 1120 regardless, which is
    why those expansion bios never showed up correctly in-game.
    """
    bio = "Eskimo from Anaktuvuk Pass."
    edt.write_bio(fake_install, ui_index=178, biography=bio, additional="", merc_bio_id=42)
    got_bio, _ = edt.read_bio(fake_install, ui_index=178, merc_bio_id=42)
    assert got_bio == bio
    mercbios = fake_install / "Data-1.13" / "BinaryData" / "MERCBIOS.EDT"
    assert mercbios.is_file()
    assert mercbios.stat().st_size >= 43 * 1120  # padded out to record 42 inclusive


def test_writing_to_far_offset_pads_file_with_zeros(fake_install: Path) -> None:
    """Writing slot 38 (offset 38*1120=42560) to a fresh file pads with zeros."""
    edt.write_bio(fake_install, ui_index=38, biography="slot 38", additional="")
    aimbios = fake_install / "Data-1.13" / "BinaryData" / "AIMBIOS.EDT"
    assert aimbios.stat().st_size == 39 * 1120
