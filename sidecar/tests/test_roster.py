"""Tests for the roster module — parsing MercProfiles + AIMAvailability."""
from __future__ import annotations

from pathlib import Path

from mercwizard_core.inject import aim_availability, profiles_xml
from mercwizard_core.models import Merc, AimBinding
from mercwizard_core.roster import find_empty_slots, find_unused_face_index, load_roster


def _setup_install(tmp_path: Path) -> Path:
    """Create a minimal Data-1.13 tree at tmp_path."""
    table_data = tmp_path / "Data-1.13" / "TableData"
    table_data.mkdir(parents=True)
    (tmp_path / "Data-1.13" / "faces").mkdir(parents=True)
    return tmp_path


def test_empty_install_has_all_empty_slots(tmp_path: Path) -> None:
    install = _setup_install(tmp_path)
    roster = load_roster(install)
    assert len(roster) == 256
    assert all(e.is_empty for e in roster)


def test_filled_slot_appears_in_roster(tmp_path: Path) -> None:
    install = _setup_install(tmp_path)
    merc = Merc(uiIndex=10, ubFaceIndex=160, zName="Carter", zNickname="Carter", Type=1)
    profiles_xml.upsert(install / "Data-1.13" / "TableData" / "MercProfiles.xml", merc)

    roster = load_roster(install)
    entry_10 = next(e for e in roster if e.slot == 10)
    assert not entry_10.is_empty
    assert entry_10.name == "Carter"
    assert entry_10.nickname == "Carter"
    assert entry_10.profile_type == 1
    assert entry_10.face_index == 160


def test_aim_binding_attached_to_roster(tmp_path: Path) -> None:
    install = _setup_install(tmp_path)
    merc = Merc(uiIndex=10, ubFaceIndex=160, zName="Carter", zNickname="Carter", Type=1)
    profiles_xml.upsert(install / "Data-1.13" / "TableData" / "MercProfiles.xml", merc)
    aim_availability.upsert(
        install / "Data-1.13" / "TableData" / "AIMAvailability.xml",
        AimBinding(uiIndex=10, description="Carter", ProfilId=10, AimBioID=10),
    )
    roster = load_roster(install)
    entry_10 = next(e for e in roster if e.slot == 10)
    assert entry_10.aim_binding is not None
    assert entry_10.aim_binding.AimBioID == 10


def test_find_empty_slots(tmp_path: Path) -> None:
    install = _setup_install(tmp_path)
    merc = Merc(uiIndex=10, ubFaceIndex=160, zName="X", zNickname="X")
    profiles_xml.upsert(install / "Data-1.13" / "TableData" / "MercProfiles.xml", merc)
    roster = load_roster(install)
    empties = find_empty_slots(roster, in_range=range(200, 220))
    assert 200 in empties
    assert 219 in empties
    assert 10 not in empties  # slot 10 is filled


def test_find_unused_face_index_starts_at_160(tmp_path: Path) -> None:
    install = _setup_install(tmp_path)
    merc = Merc(uiIndex=10, ubFaceIndex=160, zName="X", zNickname="X")
    profiles_xml.upsert(install / "Data-1.13" / "TableData" / "MercProfiles.xml", merc)
    roster = load_roster(install)
    next_unused = find_unused_face_index(roster)
    assert next_unused == 161  # 160 is taken
