"""Items.xml + sister-file field schema — engine-derived single source of truth.

Identity = Items.xml `uiIndex` (0..1853). Per-class stats live in sister files
keyed by the item's `ubClassIndex` (the sister file's own `<uiIndex>` element IS
the class index). Class bits from `Tactical/Item Types.h:655-682`; string caps
from `Item Types.h:1096-1100` (CHAR16[N] → cap N-1).

S1 = COMMON_FIELDS (Items.xml). S2 = CLASS_FAMILIES (sister-file stats). The
per-class numeric ranges below are TYPE-DERIVED (ub*=0..255, b*=-128..127,
us*=0..65535, s*=-32768..32767); exact engine clamps are refined in the
pre-S2 research note but the validation path is identical regardless.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

TEMPLATE_INDEX = 0
NAME_MAX = 79
LONG_NAME_MAX = 79
BR_NAME_MAX = 79
DESC_MAX = 399
BR_DESC_MAX = 399

UB_MIN, UB_MAX = 0, 255
B_MIN, B_MAX = -128, 127
US_MIN, US_MAX = 0, 65535
S_MIN, S_MAX = -32768, 32767


@dataclass(frozen=True)
class FieldSpec:
    key: str
    label: str
    group: str
    kind: str            # "str" | "int"
    min: int = 0
    max: int = 0
    cap: int = 0         # str cap (UTF-16 code units)
    advanced: bool = False
    note: Optional[str] = None


G_NAMES = "Names & description"
G_CORE = "Core"
G_GRAPHIC = "Graphic"

COMMON_FIELDS: tuple[FieldSpec, ...] = (
    FieldSpec("szItemName", "Name", G_NAMES, "str", cap=NAME_MAX),
    FieldSpec("szLongItemName", "Long name", G_NAMES, "str", cap=LONG_NAME_MAX),
    FieldSpec("szItemDesc", "Description", G_NAMES, "str", cap=DESC_MAX),
    FieldSpec("szBRName", "BR name", G_NAMES, "str", cap=BR_NAME_MAX),
    FieldSpec("szBRDesc", "BR description", G_NAMES, "str", cap=BR_DESC_MAX),
    FieldSpec("usItemClass", "Item class (bitfield)", G_CORE, "int", US_MIN, US_MAX,
              advanced=True, note="Bitfield (IC_*). Changing this changes which "
              "sister-file stats apply; edit with care."),
    FieldSpec("usPrice", "Price", G_CORE, "int", US_MIN, US_MAX),
    FieldSpec("ubCoolness", "Coolness", G_CORE, "int", 0, 10),
    FieldSpec("ubWeight", "Weight", G_CORE, "int", UB_MIN, UB_MAX),
    FieldSpec("ItemSize", "Item size", G_CORE, "int", UB_MIN, UB_MAX),
    FieldSpec("ubPerPocket", "Per pocket", G_CORE, "int", UB_MIN, UB_MAX),
    FieldSpec("bReliability", "Reliability", G_CORE, "int", B_MIN, B_MAX),
    FieldSpec("bRepairEase", "Repair ease", G_CORE, "int", B_MIN, B_MAX),
    FieldSpec("ubGraphicType", "Graphic type", G_GRAPHIC, "int", UB_MIN, UB_MAX,
              advanced=True),
    FieldSpec("ubGraphicNum", "Graphic number", G_GRAPHIC, "int", UB_MIN, UB_MAX,
              advanced=True),
)

_COMMON_BY_KEY = {f.key: f for f in COMMON_FIELDS}
COMMON_STR_KEYS = frozenset(f.key for f in COMMON_FIELDS if f.kind == "str")
COMMON_INT_KEYS = frozenset(f.key for f in COMMON_FIELDS if f.kind == "int")


@dataclass(frozen=True)
class ClassFamily:
    name: str
    mask: int
    filename: str
    record_tag: str
    fields: tuple[FieldSpec, ...]


_WEAPON_FIELDS = (
    FieldSpec("ubWeaponType", "Weapon type", "Weapon", "int", UB_MIN, UB_MAX),
    FieldSpec("ubCalibre", "Calibre", "Weapon", "int", UB_MIN, UB_MAX),
    FieldSpec("ubReadyTime", "Ready time (AP)", "Weapon", "int", UB_MIN, UB_MAX),
    FieldSpec("ubShotsPer4Turns", "Shots / 4 turns", "Weapon", "int", UB_MIN, UB_MAX),
    FieldSpec("ubShotsPerBurst", "Shots per burst", "Weapon", "int", UB_MIN, UB_MAX),
    FieldSpec("ubBulletSpeed", "Bullet speed", "Weapon", "int", UB_MIN, UB_MAX),
    FieldSpec("ubImpact", "Impact (damage)", "Weapon", "int", UB_MIN, UB_MAX),
    FieldSpec("ubDeadliness", "Deadliness", "Weapon", "int", UB_MIN, UB_MAX),
    FieldSpec("bAccuracy", "Accuracy (OCTH)", "Weapon", "int", B_MIN, B_MAX),
    FieldSpec("ubMagSize", "Magazine size", "Weapon", "int", UB_MIN, UB_MAX),
    FieldSpec("usRange", "Range", "Weapon", "int", US_MIN, US_MAX),
    FieldSpec("APsToReload", "APs to reload", "Weapon", "int", UB_MIN, UB_MAX),
    FieldSpec("nAccuracy", "Accuracy (NCTH)", "Weapon", "int", S_MIN, S_MAX),
)
_ARMOUR_FIELDS = (
    FieldSpec("ubArmourClass", "Armour class", "Armour", "int", UB_MIN, UB_MAX),
    FieldSpec("ubProtection", "Protection", "Armour", "int", UB_MIN, UB_MAX),
    FieldSpec("ubCoverage", "Coverage", "Armour", "int", UB_MIN, UB_MAX),
    FieldSpec("ubDegradePercent", "Degrade %", "Armour", "int", UB_MIN, UB_MAX),
)
_MAGAZINE_FIELDS = (
    FieldSpec("ubCalibre", "Calibre", "Magazine", "int", UB_MIN, UB_MAX),
    FieldSpec("ubMagSize", "Magazine size", "Magazine", "int", UB_MIN, UB_MAX),
    FieldSpec("ubAmmoType", "Ammo type", "Magazine", "int", UB_MIN, UB_MAX),
    FieldSpec("ubMagType", "Mag type", "Magazine", "int", UB_MIN, UB_MAX),
)
_EXPLOSIVE_FIELDS = (
    FieldSpec("ubType", "Type", "Explosive", "int", UB_MIN, UB_MAX),
    FieldSpec("ubDamage", "Damage", "Explosive", "int", UB_MIN, UB_MAX),
    FieldSpec("ubStunDamage", "Stun damage", "Explosive", "int", UB_MIN, UB_MAX),
    FieldSpec("ubRadius", "Radius", "Explosive", "int", UB_MIN, UB_MAX),
    FieldSpec("ubVolume", "Volume", "Explosive", "int", UB_MIN, UB_MAX),
    FieldSpec("ubVolatility", "Volatility", "Explosive", "int", UB_MIN, UB_MAX),
)

# IC_* masks. Order matters: first match wins (weapon families share no bits
# with the others, so simple priority is safe).
_IC_WEAPON = 0x2 | 0x4 | 0x8 | 0x10 | 0x20 | 0x80  # GUN|BLADE|THROWKNIFE|LAUNCHER|TENTACLES|PUNCH
_IC_AMMO = 0x400
_IC_ARMOUR = 0x800 | 0x8000  # ARMOUR|FACE
_IC_EXPLOSV = 0x100 | 0x200  # GRENADE|BOMB

CLASS_FAMILIES: tuple[ClassFamily, ...] = (
    ClassFamily("Weapon", _IC_WEAPON, "Weapons.xml", "WEAPON", _WEAPON_FIELDS),
    ClassFamily("Ammo", _IC_AMMO, "Magazines.xml", "MAGAZINE", _MAGAZINE_FIELDS),
    ClassFamily("Armour", _IC_ARMOUR, "Armours.xml", "ARMOUR", _ARMOUR_FIELDS),
    ClassFamily("Explosive", _IC_EXPLOSV, "Explosives.xml", "EXPLOSIVE", _EXPLOSIVE_FIELDS),
)


def resolve_family(us_item_class: int) -> Optional[ClassFamily]:
    for fam in CLASS_FAMILIES:
        if us_item_class & fam.mask:
            return fam
    return None


def get_common_spec(key: str) -> Optional[FieldSpec]:
    return _COMMON_BY_KEY.get(key)


def clamp_int(spec: FieldSpec, value: int) -> tuple[int, bool]:
    if value < spec.min:
        return spec.min, True
    if value > spec.max:
        return spec.max, True
    return value, False


def utf16_len(text: str) -> int:
    return len(text.encode("utf-16-le")) // 2


def _payload(specs) -> list[dict]:
    out = []
    for s in specs:
        e: dict = {"key": s.key, "label": s.label, "group": s.group, "kind": s.kind}
        if s.kind == "str":
            e["cap"] = s.cap
        else:
            e["min"], e["max"] = s.min, s.max
        if s.advanced:
            e["advanced"] = True
        if s.note:
            e["note"] = s.note
        out.append(e)
    return out


def common_schema_payload() -> list[dict]:
    return _payload(COMMON_FIELDS)


def class_schema_payload(family: ClassFamily) -> list[dict]:
    return _payload(family.fields)
