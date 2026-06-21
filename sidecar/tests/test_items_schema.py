from __future__ import annotations
from mercwizard_core import items_schema as s

def test_common_fields_present_and_typed() -> None:
    keys = {f.key for f in s.COMMON_FIELDS}
    assert {"szItemName", "szItemDesc", "usPrice", "ubCoolness",
            "ubGraphicType", "ubGraphicNum"} <= keys
    assert s.get_common_spec("szItemName").kind == "str"
    assert s.get_common_spec("usPrice").kind == "int"

def test_string_caps() -> None:
    assert s.get_common_spec("szItemName").cap == 79
    assert s.get_common_spec("szItemDesc").cap == 399

def test_clamp_int() -> None:
    spec = s.get_common_spec("ubCoolness")  # 0..10
    assert s.clamp_int(spec, 99) == (10, True)
    assert s.clamp_int(spec, 5) == (5, False)

def test_resolve_family_by_class_bit() -> None:
    assert s.resolve_family(0x2).record_tag == "WEAPON"     # IC_GUN
    assert s.resolve_family(0x800).record_tag == "ARMOUR"   # IC_ARMOUR
    assert s.resolve_family(0x400).record_tag == "MAGAZINE" # IC_AMMO
    assert s.resolve_family(0x100).record_tag == "EXPLOSIVE"# IC_GRENADE
    assert s.resolve_family(0x10000) is None                # IC_KEY → no sister stats

def test_weapon_family_has_key_stats() -> None:
    fam = s.resolve_family(0x2)
    wkeys = {f.key for f in fam.fields}
    assert {"ubImpact", "usRange", "ubMagSize"} <= wkeys

def test_utf16_len_counts_code_units() -> None:
    assert s.utf16_len("abc") == 3
    assert s.utf16_len("\U0001F600") == 2  # emoji = 2 UTF-16 units
