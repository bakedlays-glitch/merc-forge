# sidecar/tests/test_item_class_xml.py
from __future__ import annotations
from pathlib import Path
import pytest
from mercwizard_core.inject import item_class_xml as cx

WEAPONS = (
    "﻿<WEAPONLIST>\r\n"
    "\t<WEAPON>\r\n\t\t<uiIndex>0</uiIndex>\r\n\t\t<szWeaponName>Nothing</szWeaponName>\r\n"
    "\t\t<ubImpact>0</ubImpact>\r\n\t\t<usRange>0</usRange>\r\n\t</WEAPON>\r\n"
    "\t<WEAPON>\r\n\t\t<uiIndex>1</uiIndex>\r\n\t\t<szWeaponName>Glock 17</szWeaponName>\r\n"
    "\t\t<ubImpact>25</ubImpact>\r\n\t\t<usRange>115</usRange>\r\n\t</WEAPON>\r\n"
    "</WEAPONLIST>"
)

@pytest.fixture
def weapons(tmp_path: Path) -> Path:
    p = tmp_path / "Weapons.xml"
    p.write_bytes(WEAPONS.encode("utf-8"))
    return p

def test_read_row(weapons: Path) -> None:
    row = cx.read_row(weapons, "WEAPON", 1)
    assert row["ubImpact"] == 25 and row["usRange"] == 115
    assert cx.read_row(weapons, "WEAPON", 99) is None

def test_edit_row_in_place(weapons: Path) -> None:
    cx.edit_row(weapons, record_tag="WEAPON", class_index=1,
                fields={"ubImpact": 30, "usRange": 120})
    out = weapons.read_bytes()
    assert out.startswith(b"\xef\xbb\xbf")  # BOM preserved
    body = out[3:].decode("utf-8")
    assert "<ubImpact>30</ubImpact>" in body and "<usRange>120</usRange>" in body
    # Row 0 untouched.
    assert "<ubImpact>0</ubImpact>" in body
    assert "<usRange>0</usRange>" in body
    assert body.count("<szWeaponName>Glock 17</szWeaponName>") == 1

def test_edit_row_missing_raises(weapons: Path) -> None:
    with pytest.raises(cx.ClassRowError) as exc_info:
        cx.edit_row(weapons, record_tag="WEAPON", class_index=99, fields={"ubImpact": 1})
    assert exc_info.value.code == "ROW_NOT_FOUND"
