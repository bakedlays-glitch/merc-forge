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

def test_resolve_category_partition() -> None:
    assert s.resolve_category(0x2) == "guns"        # GUN
    assert s.resolve_category(0x10) == "guns"        # LAUNCHER groups with guns
    assert s.resolve_category(0x400) == "ammo"
    assert s.resolve_category(0x100) == "explosives" # GRENADE
    assert s.resolve_category(0x4) == "melee"        # BLADE
    assert s.resolve_category(0x80) == "melee"       # PUNCH
    assert s.resolve_category(0x40) == "melee"       # THROWN
    assert s.resolve_category(0x2000) == "kits"      # KIT
    assert s.resolve_category(0x1000) == "kits"      # MEDKIT
    assert s.resolve_category(0x20000) == "lbe"      # LBEGEAR
    assert s.resolve_category(0x800) == "armor"      # ARMOUR
    assert s.resolve_category(0x8000) == "armor"     # FACE
    assert s.resolve_category(0x10000) == "misc"     # KEY
    assert s.resolve_category(0x1) == "misc"         # NONE
    assert {c.key for c in s.CATEGORIES} == {
        "guns","ammo","explosives","melee","kits","lbe","armor","misc"}

def test_weapon_family_includes_thrown_punch() -> None:
    for cls in (0x40, 0x80, 0x4, 0x8, 0x10, 0x2):  # THROWN/PUNCH/BLADE/THROWKNIFE/LAUNCHER/GUN
        assert s.resolve_family(cls).record_tag == "WEAPON"


def test_schema_payload_includes_help_for_verified_fields() -> None:
    payload = {e["key"]: e for e in s.common_schema_payload()}
    assert payload["ubCoolness"].get("help")
    # every field that declares a unit also declares help (no orphan units)
    for e in s.common_schema_payload():
        if e.get("unit"):
            assert e.get("help")


def test_class_schema_help_and_units() -> None:
    fam = s.resolve_family(0x2)  # Weapon family
    payload = {e["key"]: e for e in s.class_schema_payload(fam)}
    assert payload["ubMagSize"].get("unit") == "rounds" and payload["ubMagSize"].get("help")
    assert payload["ubReadyTime"].get("unit") == "AP"
    assert "NCTH" in payload["nAccuracy"]["help"]  # verified-cited definition
