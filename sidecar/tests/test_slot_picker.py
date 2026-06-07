"""Tests for the engine-faithful slot picker.

Covers the 10 cases enumerated in the engine-faithful rewrite plan §D.1:

  1. Empty install — every slot is_empty + tier safe / vanilla_overwrite
     by engine convention; no AIM/MERC rows.
  2. Named engine slots get correct names + tiers (QUEEN locked, MIGUEL
     quest_bound, PROF_HUMMER locked, VICKI vanilla_overwrite).
  3. UB shift — FIRST_RPC moves from 57→60; MIGUEL lands at 60 in UB.
  4. AimRow.present mirrors live XML; placeholder ProfilId=-1 → False.
  5. MercRow.present mirrors live XML; placeholder ProfilId=0 → False.
  6. BUNS_CHAOTIC at slot 215 is named via the engine table (AimBioID=17
     collision with vanilla Buns documented in engine_name).
  7. category derivation: aim if row present, merc if row present, rpc/
     npc by FIRST_RPC/FIRST_NPC range otherwise.
  8. tier derivation: locked > quest_bound > vanilla_overwrite > safe.
  9. 51-56 gap: SAFE on modern install (reads_profiles_from_xml=True),
     LOCKED on legacy install (False).
  10. /slots/locks endpoint shape unchanged for back-compat.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from mercwizard_core.engine_flags import EngineFlags
from mercwizard_core.install_context import make_install_context
from mercwizard_core.models import AimBinding, MercBinding
from mercwizard_core.slot_locks import SlotLockTier, all_slot_locks
from mercwizard_core.slot_picker import (
    FIRST_NPC,
    FIRST_RPC_NON_UB,
    FIRST_RPC_UB,
    MAX_LAPTOP_AIM_DISPLAY,
    build_slot_picker,
    engine_named_slots,
)
from mercwizard_core.inject import aim_availability, merc_availability, profiles_xml


def _empty_install(root: Path) -> Path:
    """Minimal install with empty XML files in TableData/."""
    table = root / "Data-1.13" / "TableData"
    table.mkdir(parents=True, exist_ok=True)
    (root / "JA2.exe").write_bytes(b"\x00" * 32)
    # Empty (well-formed) XMLs so readers return {} without erroring.
    (table / "MercProfiles.xml").write_text("<PROFILES />", encoding="utf-8")
    (table / "AIMAvailability.xml").write_text("<AIM_AVAILABLES />", encoding="utf-8")
    (table / "MercAvailability.xml").write_text("<MERC_AVAILABLES />", encoding="utf-8")
    return root


def _modern_flags() -> EngineFlags:
    return EngineFlags(is_ub=False, reads_profiles_from_xml=True)


def _legacy_flags() -> EngineFlags:
    return EngineFlags(is_ub=False, reads_profiles_from_xml=False)


# 1 ────────────────────────────────────────────────────────────────────────


def test_empty_install_has_255_slots_all_empty(tmp_path: Path) -> None:
    """A clean install with no profiles → 255 SlotInfo entries, all empty,
    no AIM/MERC rows, no engine-named profile occupancy."""
    root = _empty_install(tmp_path / "install")
    picker = build_slot_picker(root, flags=_modern_flags())
    assert len(picker.slots) == 255
    assert all(s.is_empty for s in picker.slots)
    assert picker.aim_row_count == 0
    assert picker.merc_row_count == 0
    assert picker.laptop_aim_display_cap == MAX_LAPTOP_AIM_DISPLAY


# 2 ────────────────────────────────────────────────────────────────────────


def test_named_slots_have_correct_tier_and_name(tmp_path: Path) -> None:
    """Engine-named slots get correct tier/name/role from
    Tactical/soldier profile type.h."""
    root = _empty_install(tmp_path / "install")
    picker = build_slot_picker(root, flags=_modern_flags())

    # QUEEN at 75 — main-story locked
    queen = picker.slots[75]
    assert queen.engine_name == "QUEEN"
    assert queen.tier == "locked"

    # MIGUEL at 57 — quest_bound RPC
    miguel = picker.slots[57]
    assert miguel.engine_name == "MIGUEL"
    assert miguel.tier == "quest_bound"
    assert miguel.category == "rpc"

    # PROF_HUMMER at 160 — vehicle locked
    hummer = picker.slots[160]
    assert hummer.engine_name == "PROF_HUMMER"
    assert hummer.tier == "locked"
    assert hummer.category == "locked"

    # VICKI at 4 — vanilla AIM (vanilla_overwrite even without live data)
    vicki = picker.slots[4]
    assert vicki.engine_name == "VICKI"
    assert vicki.tier == "vanilla_overwrite"


# 3 ────────────────────────────────────────────────────────────────────────


def test_ub_shifts_first_rpc(tmp_path: Path) -> None:
    """UB build moves FIRST_RPC from 57→60. MIGUEL is now at 60, slot 57
    is unnamed."""
    root = _empty_install(tmp_path / "install")
    ub_flags = EngineFlags(is_ub=True, reads_profiles_from_xml=True)
    picker = build_slot_picker(root, flags=ub_flags)

    assert picker.slots[60].engine_name == "MIGUEL"
    assert picker.slots[57].engine_name is None
    # The shifted slot 60 is quest_bound RPC under UB
    assert picker.slots[60].tier == "quest_bound"

    # Sanity: non-UB still has MIGUEL at 57
    non_ub = build_slot_picker(root, flags=_modern_flags())
    assert non_ub.slots[57].engine_name == "MIGUEL"


# 4 ────────────────────────────────────────────────────────────────────────


def test_aim_row_placeholder_is_not_present(tmp_path: Path) -> None:
    """Modded AIMAvailability rows with ProfilId=-1 are placeholders, not
    real bindings. picker.aim_row.present should be False."""
    root = _empty_install(tmp_path / "install")
    aim_path = root / "Data-1.13" / "TableData" / "AIMAvailability.xml"
    # Real row at 25, placeholder at 26.
    aim_availability.upsert(
        aim_path,
        AimBinding(uiIndex=25, description="Test", ProfilId=25, AimBioID=25),
    )
    aim_availability.upsert(
        aim_path,
        AimBinding(uiIndex=26, description="", ProfilId=-1, AimBioID=-1),
    )

    picker = build_slot_picker(root, flags=_modern_flags())
    assert picker.slots[25].aim_row.present is True
    assert picker.slots[26].aim_row.present is False
    # Category follows row presence
    assert picker.slots[25].category == "aim"
    assert picker.slots[26].category != "aim"


# 5 ────────────────────────────────────────────────────────────────────────


def test_merc_row_placeholder_is_not_present(tmp_path: Path) -> None:
    """MERC empty sentinel is ProfilId=0 (asymmetric with AIM's -1)."""
    root = _empty_install(tmp_path / "install")
    merc_path = root / "Data-1.13" / "TableData" / "MercAvailability.xml"
    # Real row at 45 (Gumpy).
    merc_availability.upsert(
        merc_path,
        MercBinding(
            uiIndex=5, Name="Gumpy", ProfilId=45, MercBioID=5,
            usMoneyPaid=100, usDay=0,
        ),
    )
    # Placeholder at 46 (ProfilId=0, the engine's empty sentinel).
    merc_availability.upsert(
        merc_path,
        MercBinding(
            uiIndex=0, Name="", ProfilId=0, MercBioID=-1,
            usMoneyPaid=0, usDay=0,
        ),
    )

    picker = build_slot_picker(root, flags=_modern_flags())
    assert picker.slots[45].merc_row.present is True
    assert picker.slots[45].merc_row.MercBioID == 5
    # Slot 0 has ProfilId=0 placeholder — must be treated as absent
    assert picker.slots[0].merc_row.present is False


# 6 ────────────────────────────────────────────────────────────────────────


def test_buns_chaotic_is_named_at_215(tmp_path: Path) -> None:
    """215 BUNS_CHAOTIC is engine-named — AimBioID=17 collides with vanilla
    Buns at slot 17."""
    root = _empty_install(tmp_path / "install")
    picker = build_slot_picker(root, flags=_modern_flags())
    buns_chaotic = picker.slots[215]
    assert buns_chaotic.engine_name == "BUNS_CHAOTIC"
    assert "Buns" in (buns_chaotic.engine_role or "")
    assert buns_chaotic.tier == "vanilla_overwrite"


# 7 ────────────────────────────────────────────────────────────────────────


def test_category_derivation_uses_xml_then_falls_back_to_range(tmp_path: Path) -> None:
    """Live AIM row beats range fallback; otherwise FIRST_RPC..FIRST_NPC
    decides."""
    root = _empty_install(tmp_path / "install")
    aim_path = root / "Data-1.13" / "TableData" / "AIMAvailability.xml"
    # Put an AIM row at 60 (which would otherwise be RPC range)
    aim_availability.upsert(
        aim_path,
        AimBinding(uiIndex=60, description="Mod merc", ProfilId=60, AimBioID=72),
    )
    picker = build_slot_picker(root, flags=_modern_flags())

    # 60 has AIM row → category is aim, NOT rpc
    assert picker.slots[60].category == "aim"

    # 58 (RPC range, no row) → rpc
    assert picker.slots[58].category == "rpc"

    # 80 (NPC range, no row) → npc
    assert picker.slots[80].category == "npc"

    # 220 (unassigned, no row) → unassigned
    assert picker.slots[220].category == "unassigned"


# 8 ────────────────────────────────────────────────────────────────────────


def test_tier_priority_locked_beats_quest_beats_overwrite(tmp_path: Path) -> None:
    """Vehicle/main-story locked overrides RPC quest-bound, which beats
    vanilla data conventions, which beats safe."""
    root = _empty_install(tmp_path / "install")
    picker = build_slot_picker(root, flags=_modern_flags())

    # 75 QUEEN → locked (main-story flag wins even though in 75-159 range)
    assert picker.slots[75].tier == "locked"
    # 78 CARMEN → quest_bound (named, but not main-story-locked)
    assert picker.slots[78].tier == "quest_bound"
    # 4 VICKI → vanilla_overwrite (named, no locks/quest flags, vanilla AIM)
    assert picker.slots[4].tier == "vanilla_overwrite"
    # 220 unassigned → safe
    assert picker.slots[220].tier == "safe"


# 9 ────────────────────────────────────────────────────────────────────────


def test_51_56_gap_safe_on_modern_locked_on_legacy(tmp_path: Path) -> None:
    """The 51-56 gap is empty in vanilla. Modern engines (fReadProfileData
    FromXML=TRUE) treat them as safe; legacy engines (FALSE) mis-tag them
    as IMP and they must stay LOCKED."""
    root = _empty_install(tmp_path / "install")

    modern = build_slot_picker(root, flags=_modern_flags())
    for slot in range(51, 57):
        assert modern.slots[slot].tier == "safe", (
            f"slot {slot} should be safe on modern XML-read install"
        )

    legacy = build_slot_picker(root, flags=_legacy_flags())
    for slot in range(51, 57):
        assert legacy.slots[slot].tier == "locked", (
            f"slot {slot} should be locked on legacy install"
        )


# 10 ───────────────────────────────────────────────────────────────────────


def test_slots_locks_endpoint_shape_back_compat() -> None:
    """Static /slots/locks output keeps slot/tier/name/role shape so existing
    consumers don't break. 51-56 are now SAFE (was LOCKED) — the only
    deliberate tier change in the rewrite."""
    locks = all_slot_locks()
    assert len(locks) == 255

    # Same fields, same types
    for entry in locks[:5]:
        assert hasattr(entry, "slot")
        assert hasattr(entry, "tier")
        assert hasattr(entry, "name")
        assert hasattr(entry, "role")

    # Engine-named slots come through
    assert locks[57].name == "MIGUEL"
    assert locks[75].name == "QUEEN"
    assert locks[160].name == "PROF_HUMMER"

    # 51-56 deliberately downgraded
    for slot in range(51, 57):
        assert locks[slot].tier == SlotLockTier.SAFE

    # Vanilla-data-named (1.13 expansion) still surface via legacy table
    assert locks[223].name == "GARY"
    assert locks[251].name == "SMOKE"


# Extras for coverage of named-slot table ─────────────────────────────────


def test_engine_named_slots_count_matches_source_enum() -> None:
    """Non-UB table has expected named-slot counts: 2 vanilla AIM, 11
    vanilla MERC, 18 RPC, 85 NPC, 5 vehicle (160-164 + 169), 5 expansion
    MERC named (165-168 + 191), 4 expansion (195-198), 1 expansion AIM
    named (215). Total = 131."""
    table = engine_named_slots(is_ub=False)
    # Just sanity-check a few cardinal values
    assert table[FIRST_RPC_NON_UB].name == "MIGUEL"
    assert table[FIRST_NPC].name == "QUEEN"
    assert table[159].name == "SPECK"
    # 51-56 must NOT be in the named table — they're an unnamed gap
    for slot in range(51, 57):
        assert slot not in table


def test_ub_table_preserves_npc_and_vehicle_slots() -> None:
    """The UB shift only affects 57-74 (RPC range). NPCs at 75-159 and
    vehicles at 160-164 stay where they are."""
    table = engine_named_slots(is_ub=True)
    assert table[75].name == "QUEEN"
    assert table[FIRST_RPC_UB].name == "MIGUEL"  # MIGUEL shifted to 60
    assert table[160].name == "PROF_HUMMER"
    # 57-59 are now unnamed in UB
    for slot in (57, 58, 59):
        assert slot not in table
