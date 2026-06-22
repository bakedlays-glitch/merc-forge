from __future__ import annotations
from pathlib import Path
import pytest
from mercwizard_core import item_enums as en
from mercwizard_core.install_context import make_install_context


def _ctx(tmp_path: Path):
    items = tmp_path / "Data-1.13" / "TableData" / "Items"
    items.mkdir(parents=True)
    (tmp_path / "JA2.exe").touch()
    (items.parent / "MercProfiles.xml").write_text("<MERCPROFILES />")
    (items / "AmmoStrings.xml").write_bytes((
        "<AMMOLIST>\r\n"
        "\t<AMMO>\r\n\t\t<uiIndex>0</uiIndex>\r\n\t\t<AmmoCaliber>0</AmmoCaliber>\r\n\t</AMMO>\r\n"
        "\t<AMMO>\r\n\t\t<uiIndex>2</uiIndex>\r\n\t\t<AmmoCaliber>9x19mm</AmmoCaliber>\r\n\t</AMMO>\r\n"
        "</AMMOLIST>").encode("utf-8"))
    (items / "AmmoTypes.xml").write_bytes((
        "<AMMOTYPELIST>\r\n"
        "\t<AMMOTYPE>\r\n\t\t<uiIndex>0</uiIndex>\r\n\t\t<name>Ball</name>\r\n\t</AMMOTYPE>\r\n"
        "</AMMOTYPELIST>").encode("utf-8"))
    return make_install_context(tmp_path)


def test_calibre_options_from_ammostrings(tmp_path: Path):
    opts = en.calibre_options(_ctx(tmp_path))
    assert {"value": 2, "label": "9x19mm"} in opts


def test_ammo_type_options_from_ammotypes(tmp_path: Path):
    opts = en.ammo_type_options(_ctx(tmp_path))
    assert {"value": 0, "label": "Ball"} in opts


def test_static_enum_tables_nonempty_and_shaped():
    for table in (en.WEAPON_TYPE_OPTIONS, en.ARMOUR_CLASS_OPTIONS,
                  en.EXPLOSIVE_TYPE_OPTIONS, en.MAG_TYPE_OPTIONS):
        assert table and all({"value", "label"} <= set(o) for o in table)


def test_enum_options_for_dispatch(tmp_path: Path):
    ctx = _ctx(tmp_path)
    assert en.enum_options_for("ubCalibre", ctx)
    assert en.enum_options_for("ubArmourClass", ctx) is en.ARMOUR_CLASS_OPTIONS
    assert en.enum_options_for("usPrice", ctx) is None
