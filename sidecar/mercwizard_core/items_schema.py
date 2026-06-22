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


# Form sections (the frontend renders each as a collapsible; "Advanced" is
# collapsed by default).
G_IDENTITY = "Identity"
G_ECON = "Economy"
G_GRAPHIC = "Graphic"
G_ADV = "Advanced"

COMMON_FIELDS: tuple[FieldSpec, ...] = (
    FieldSpec("szItemName", "Name", G_IDENTITY, "str", cap=NAME_MAX),
    FieldSpec("szLongItemName", "Long name", G_IDENTITY, "str", cap=LONG_NAME_MAX),
    FieldSpec("szItemDesc", "Description", G_IDENTITY, "str", cap=DESC_MAX),
    FieldSpec("szBRName", "BR name", G_IDENTITY, "str", cap=BR_NAME_MAX),
    FieldSpec("szBRDesc", "BR description", G_IDENTITY, "str", cap=BR_DESC_MAX),
    # usItemClass is rendered as a read-only ClassBadge, never an input — its
    # group is irrelevant (the form excludes it from the field loop).
    FieldSpec("usItemClass", "Item class (bitfield)", G_ADV, "int", US_MIN, US_MAX,
              advanced=True, note="Bitfield (IC_*). Read-only here; changing it "
              "would re-point sister-file stats."),
    FieldSpec("usPrice", "Price", G_ECON, "int", US_MIN, US_MAX),
    FieldSpec("ubCoolness", "Coolness", G_ECON, "int", 0, 10),
    FieldSpec("ubWeight", "Weight", G_ECON, "int", UB_MIN, UB_MAX),
    FieldSpec("ItemSize", "Item size", G_ECON, "int", UB_MIN, UB_MAX),
    FieldSpec("ubPerPocket", "Per pocket", G_ECON, "int", UB_MIN, UB_MAX),
    FieldSpec("bReliability", "Reliability", G_ADV, "int", B_MIN, B_MAX),
    FieldSpec("bRepairEase", "Repair ease", G_ADV, "int", B_MIN, B_MAX),
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
_IC_WEAPON = 0x2 | 0x4 | 0x8 | 0x10 | 0x20 | 0x40 | 0x80  # GUN|BLADE|THROWKNIFE|LAUNCHER|TENTACLES|THROWN|PUNCH
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


@dataclass(frozen=True)
class Category:
    key: str
    label: str
    mask: int


# IC_MAPFILTER_* (Item Types.h:692-700), priority order. Misc is the catch-all.
CATEGORIES: tuple[Category, ...] = (
    Category("guns", "Guns", 0x2 | 0x10),                              # GUN|LAUNCHER
    Category("ammo", "Ammo", 0x400),                                   # AMMO
    Category("explosives", "Explosives", 0x100 | 0x200),               # GRENADE|BOMB
    Category("melee", "Melee", 0x4 | 0x80 | 0x40 | 0x8),              # BLADE|PUNCH|THROWN|THROWING_KNIFE
    Category("kits", "Kits", 0x2000 | 0x1000 | 0x4000),               # KIT|MEDKIT|APPLIABLE
    Category("lbe", "LBE", 0x20000 | 0x40000),                        # LBEGEAR|BELTCLIP
    Category("armor", "Armor", 0x800 | 0x8000),                       # ARMOUR|FACE
    Category("misc", "Misc", 0x20 | 0x10000 | 0x10000000 | 0x20000000 | 0x1),  # TENTACLES|KEY|MISC|MONEY|NONE
)


def resolve_category(us_item_class: int) -> str:
    for cat in CATEGORIES:
        if us_item_class & cat.mask:
            return cat.key
    return "misc"


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


# Verified field definitions (help) + units. Each entry is sourced; entries with
# a "# src:" note cite the engine/XML evidence. Risky units we could NOT verify
# (e.g. usRange in tiles, ubWeight unit) are intentionally LEFT OUT rather than
# guessed — wrong definitions are worse than none. Weapon-stat citations come
# from the inline comments in Data-1.13/TableData/Items/Weapons.xml.
_FIELD_DOCS: dict[str, dict[str, str]] = {
    # ── Common (Items.xml) ──
    "szItemName": {"help": "Short item name shown in inventory."},
    "szLongItemName": {"help": "Full item name shown in tooltips and shops."},
    "szItemDesc": {"help": "Description shown in the item examine panel."},
    "szBRName": {"help": "Item name as it appears in Bobby Ray's online store."},
    "szBRDesc": {"help": "Item description in Bobby Ray's online store."},
    "usPrice": {"help": "Base item value used for buy/sell/repair pricing."},
    "ubCoolness": {"help": "Rarity/'coolness' tier; higher items appear later and "
                           "are harder to find. Editor range 0–10."},
    "ubWeight": {"help": "Item weight; adds to a merc's carried burden."},
    "ItemSize": {"help": "Inventory size class of the item."},
    "ubPerPocket": {"help": "How many of this item stack in a single inventory slot."},
    "bReliability": {"help": "Reliability modifier; higher resists jamming and "
                             "slows condition loss."},
    "bRepairEase": {"help": "Repair-ease modifier; higher is faster/cheaper to repair."},
    # ── Weapon (Weapons.xml) ──
    "ubImpact": {"help": "Base damage the weapon deals per hit."},
    "ubDeadliness": {"help": "Affects a merc's affinity toward the weapon (not raw "
                             "damage)."},  # src: Weapons.xml ubDeadliness comment
    "bAccuracy": {"help": "Accuracy bonus used by the Old chance-to-hit system "
                          "(OCTH)."},      # src: Weapons.xml bAccuracy comment
    "nAccuracy": {"help": "Accuracy value used by the New chance-to-hit system "
                          "(NCTH)."},      # src: Weapons.xml nAccuracy comment
    "usRange": {"help": "Effective range of the weapon (internal range units)."},
    "ubMagSize": {"help": "Default magazine capacity.", "unit": "rounds"},
    "ubReadyTime": {"help": "Action-point cost to ready the weapon.", "unit": "AP"},
    "APsToReload": {"help": "Action-point cost to reload.", "unit": "AP"},
    "ubShotsPer4Turns": {"help": "Rate of fire, expressed as shots per 4 turns."},
    "ubShotsPerBurst": {"help": "Rounds fired in a single burst."},
    "ubBulletSpeed": {"help": "Projectile travel speed."},
    # ── Armour (Armours.xml) ──
    "ubProtection": {"help": "Amount of incoming damage this armour absorbs."},
    "ubCoverage": {"help": "Portion of the body this armour protects.", "unit": "%"},
    "ubDegradePercent": {"help": "How quickly protection degrades as the armour "
                                 "takes damage.", "unit": "%"},
    # ── Magazine (Magazines.xml) ──
    # ubMagSize handled above (shared key).
    # ── Explosive (Explosives.xml) ──
    "ubDamage": {"help": "Direct explosive damage."},
    "ubStunDamage": {"help": "Stun/breath damage dealt by the blast."},
    "ubRadius": {"help": "Blast radius of the explosion."},
    "ubVolume": {"help": "Noise volume produced by the explosion."},
    "ubVolatility": {"help": "Chance the item detonates when damaged or dropped."},
}


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
        doc = _FIELD_DOCS.get(s.key)
        if doc:
            e["help"] = doc["help"]
            if doc.get("unit"):
                e["unit"] = doc["unit"]
        out.append(e)
    return out


def common_schema_payload() -> list[dict]:
    return _payload(COMMON_FIELDS)


def class_schema_payload(family: ClassFamily) -> list[dict]:
    return _payload(family.fields)


# IC_* bit → human name, sourced from Tactical/Item Types.h:655-682
_IC_BIT_NAMES: tuple[tuple[int, str], ...] = (
    (0x1,        "NONE"),
    (0x2,        "GUN"),
    (0x4,        "BLADE"),
    (0x8,        "THROWKNIFE"),
    (0x10,       "LAUNCHER"),
    (0x20,       "TENTACLES"),
    (0x40,       "THROWN"),
    (0x80,       "PUNCH"),
    (0x100,      "GRENADE"),
    (0x200,      "BOMB"),
    (0x400,      "AMMO"),
    (0x800,      "ARMOUR"),
    (0x1000,     "MEDKIT"),
    (0x2000,     "KIT"),
    (0x4000,     "APPLIABLE"),
    (0x8000,     "FACE"),
    (0x10000,    "KEY"),
    (0x20000,    "LBEGEAR"),
    (0x40000,    "BELTCLIP"),
    (0x10000000, "MONEY"),
    (0x20000000, "MISC"),
)


def decode_class(us_item_class: int) -> str:
    """Return a human-readable bit-name string for a usItemClass value.

    E.g. decode_class(2) → "GUN", decode_class(0x800|0x8000) → "ARMOUR | FACE".
    Returns "NONE" when the value is 0 or no known bits are set.
    """
    names = [name for bit, name in _IC_BIT_NAMES if us_item_class & bit]
    return " | ".join(names) if names else "NONE"
