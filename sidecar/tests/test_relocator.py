"""Tests for the Move flow (MercRelocator)."""
from __future__ import annotations

from pathlib import Path

import pytest

from mercwizard_core import relocator
from mercwizard_core.inject import (
    aim_availability,
    edt as edt_mod,
    profiles_xml,
    starting_gear,
)
from mercwizard_core.models import AimBinding, Gear, GearKit, Merc


def _set_up_filled_slot(install_root: Path, slot: int, aim_bio_id: int = 5) -> Merc:
    """Place a complete merc into `slot`: profile + AIM binding + gear + bio.

    Returns the Merc object that was written.
    """
    table = install_root / "Data-1.13" / "TableData"
    table.mkdir(parents=True, exist_ok=True)
    (install_root / "Data-1.13" / "BinaryData").mkdir(parents=True, exist_ok=True)

    merc = Merc(
        uiIndex=slot,
        ubFaceIndex=160 + slot,
        Type=1,
        zName="Source",
        zNickname="Src",
        biographyText="The source merc's bio.",
        additionalInfoText="Source addl.",
    )
    profiles_xml.upsert(table / "MercProfiles.xml", merc)
    aim_availability.upsert(
        table / "AIMAvailability.xml",
        AimBinding(uiIndex=slot, description="Source", ProfilId=slot, AimBioID=aim_bio_id),
    )
    starting_gear.upsert(
        table / "MercStartingGear.xml",
        Gear(mIndex=slot, mName="Source", kits=[GearKit(mWeapon=2, mBig0=71, mBig0Quantity=3)]),
    )
    edt_mod.write_bio(
        install_root, slot,
        biography=merc.biographyText,
        additional=merc.additionalInfoText,
        aim_bio_id=aim_bio_id,
    )
    return merc


def test_move_vanilla_aim_to_vanilla_aim(tmp_path: Path) -> None:
    """Move slot 5 to slot 10. Both are vanilla AIM (AimBioID = uiIndex)."""
    _set_up_filled_slot(tmp_path, slot=5, aim_bio_id=5)
    report = relocator.move(tmp_path, source_slot=5, dest_slot=10)
    assert report.success, f"Move failed at step {report.error_step}: {report.error}"

    # Source slot 5 should be empty in MercProfiles
    profiles_path = tmp_path / "Data-1.13" / "TableData" / "MercProfiles.xml"
    assert profiles_xml.read_slot(profiles_path, 5) is None
    # Destination slot 10 should have the merc
    dest = profiles_xml.read_slot(profiles_path, 10)
    assert dest is not None
    assert dest["zName"] == "Source"
    assert dest["uiIndex"] == "10"

    # AIM binding: source removed, dest added
    aim_map = aim_availability.read_all(tmp_path / "Data-1.13" / "TableData" / "AIMAvailability.xml")
    assert 5 not in aim_map
    assert 10 in aim_map
    assert aim_map[10].AimBioID == 10  # vanilla AIM: AimBioID = uiIndex

    # EDT bio relocated
    bio_at_dest, _ = edt_mod.read_bio(tmp_path, ui_index=10, aim_bio_id=10)
    assert bio_at_dest == "The source merc's bio."
    bio_at_source, _ = edt_mod.read_bio(tmp_path, ui_index=5, aim_bio_id=5)
    assert bio_at_source == ""  # cleared


def test_move_to_occupied_slot_raises(tmp_path: Path) -> None:
    _set_up_filled_slot(tmp_path, slot=5, aim_bio_id=5)
    _set_up_filled_slot(tmp_path, slot=10, aim_bio_id=10)
    with pytest.raises(relocator.MoveError, match="occupied"):
        relocator.move(tmp_path, source_slot=5, dest_slot=10)


def test_move_from_empty_slot_raises(tmp_path: Path) -> None:
    (tmp_path / "Data-1.13" / "TableData").mkdir(parents=True)
    with pytest.raises(relocator.MoveError, match="Could not read source slot"):
        relocator.move(tmp_path, source_slot=99, dest_slot=10)


def test_move_to_same_slot_raises(tmp_path: Path) -> None:
    _set_up_filled_slot(tmp_path, slot=5, aim_bio_id=5)
    with pytest.raises(relocator.MoveError, match="same"):
        relocator.move(tmp_path, source_slot=5, dest_slot=5)


def test_move_gear_block_relocates(tmp_path: Path) -> None:
    _set_up_filled_slot(tmp_path, slot=5, aim_bio_id=5)
    relocator.move(tmp_path, source_slot=5, dest_slot=12)
    gear_path = tmp_path / "Data-1.13" / "TableData" / "MercStartingGear.xml"
    assert starting_gear.read_slot(gear_path, 5) is None
    g = starting_gear.read_slot(gear_path, 12)
    assert g is not None
    assert g.kits[0].mWeapon == 2
    assert g.kits[0].mAbsolutePrice == -1
