"""Verified enum option tables for integer-coded item fields.

Each static table is cited to the exact engine source file + line where the
enum was defined.  XML-backed tables (calibre, ammo type) are loaded at
runtime from the live install so they stay current as mods expand them.

Usage::

    from mercwizard_core.item_enums import enum_options_for
    opts = enum_options_for("ubCalibre", ctx)   # list[{"value": int, "label": str}]
    opts = enum_options_for("usPrice", ctx)     # None  → keep number input
"""
from __future__ import annotations

import threading as _threading
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from mercwizard_core.install_context import InstallContext

# ---------------------------------------------------------------------------
# Type alias
# ---------------------------------------------------------------------------
EnumOption = dict  # {"value": int, "label": str}


# ---------------------------------------------------------------------------
# Static tables — engine-#define / enum, each with a source: file:line citation
# ---------------------------------------------------------------------------

# source: Source Files/1.13 Source/source-master/i18n/_EnglishText.cpp:2207-2218
# WeaponType[ubWeaponType] — the game indexes this array directly by ubWeaponType
# value; the ordering is authoritative from _EnglishText.cpp, NOT from the
# Weapons.h enum (which was NOGUNCLASS/HANDGUNCLASS/... in a different order).
WEAPON_TYPE_OPTIONS: list[EnumOption] = [
    {"value": 0, "label": "Other"},
    {"value": 1, "label": "Pistol"},
    {"value": 2, "label": "MP"},
    {"value": 3, "label": "SMG"},
    {"value": 4, "label": "Rifle"},
    {"value": 5, "label": "Sniper rifle"},
    {"value": 6, "label": "Assault rifle"},
    {"value": 7, "label": "LMG"},
    {"value": 8, "label": "Shotgun"},
]

# source: Source Files/1.13 Source/source-master/Tactical/Weapons.h:101-110
# enum { ARMOURCLASS_HELMET=0, ARMOURCLASS_VEST, ARMOURCLASS_LEGGINGS,
#        ARMOURCLASS_PLATE, ARMOURCLASS_MONST, ARMOURCLASS_VEHICLE }
# Cross-checked: Armours.xml contains values 0-4 (VEHICLE=5 unused in data).
ARMOUR_CLASS_OPTIONS: list[EnumOption] = [
    {"value": 0, "label": "Helmet"},
    {"value": 1, "label": "Vest"},
    {"value": 2, "label": "Leggings"},
    {"value": 3, "label": "Plate / Ceramic insert"},
    {"value": 4, "label": "Monster armour"},
    {"value": 5, "label": "Vehicle armour"},
]

# source: Source Files/1.13 Source/source-master/Tactical/Items.h:19-35
# enum { EXPLOSV_NORMAL=0, EXPLOSV_STUN, EXPLOSV_TEARGAS, EXPLOSV_MUSTGAS,
#        EXPLOSV_FLARE, EXPLOSV_NOISE, EXPLOSV_SMOKE, EXPLOSV_CREATUREGAS,
#        EXPLOSV_BURNABLEGAS, EXPLOSV_FLASHBANG, EXPLOSV_SIGNAL_SMOKE,
#        EXPLOSV_SMOKE_DEBRIS, EXPLOSV_SMOKE_FIRERETARDANT, EXPLOSV_ANY_TYPE }
# Cross-checked: Explosives.xml contains values 0-12 (EXPLOSV_ANY_TYPE=13
# is a sentinel, not a real type).
EXPLOSIVE_TYPE_OPTIONS: list[EnumOption] = [
    {"value": 0,  "label": "Normal (HE)"},
    {"value": 1,  "label": "Stun"},
    {"value": 2,  "label": "Tear gas"},
    {"value": 3,  "label": "Mustard gas"},
    {"value": 4,  "label": "Flare"},
    {"value": 5,  "label": "Noise"},
    {"value": 6,  "label": "Smoke"},
    {"value": 7,  "label": "Creature gas"},
    {"value": 8,  "label": "Burnable gas"},
    {"value": 9,  "label": "Flashbang"},
    {"value": 10, "label": "Signal smoke"},
    {"value": 11, "label": "Smoke debris"},
    {"value": 12, "label": "Fire-retardant smoke"},
]

# source: Source Files/1.13 Source/source-master/Tactical/Weapons.h:138-144
# enum { AMMO_MAGAZINE=0, AMMO_BULLET, AMMO_BOX, AMMO_CRATE }
# Cross-checked: Magazines.xml uses values 0-3 (all four present).
MAG_TYPE_OPTIONS: list[EnumOption] = [
    {"value": 0, "label": "Magazine"},
    {"value": 1, "label": "Loose bullet"},
    {"value": 2, "label": "Ammo box"},
    {"value": 3, "label": "Ammo crate"},
]


# ---------------------------------------------------------------------------
# XML-backed loaders — tolerant of missing / malformed file
# ---------------------------------------------------------------------------

# Parsed XML enum options cached by (path -> (mtime_ns, options)). These loaders
# run on every GET /items/{id}; without this they'd re-parse AmmoStrings/AmmoTypes
# on every detail-panel open. Fingerprinted by mtime so an edit is picked up.
_XML_CACHE: dict[str, tuple[int, list[EnumOption]]] = {}
_XML_LOCK = _threading.Lock()


def _cached_xml(path: "Optional[Path]", parser) -> list[EnumOption]:
    if path is None or not path.exists():
        return []
    try:
        key = str(path)
        mtime = path.stat().st_mtime_ns
    except OSError:
        return []
    with _XML_LOCK:
        hit = _XML_CACHE.get(key)
        if hit and hit[0] == mtime:
            return hit[1]
    try:
        opts = parser(str(path))
    except ET.ParseError:
        return []
    with _XML_LOCK:
        _XML_CACHE[key] = (mtime, opts)
    return opts


def _parse_calibre(path: str) -> list[EnumOption]:
    root = ET.parse(path).getroot()
    opts: list[EnumOption] = []
    for ammo in root.findall(".//AMMO"):
        idx_el = ammo.find("uiIndex")
        label_el = ammo.find("AmmoCaliber")
        if idx_el is None or label_el is None:
            continue
        try:
            value = int(idx_el.text or "")
        except (ValueError, TypeError):
            continue
        label = (label_el.text or "").strip()
        if label and label != "0":
            opts.append({"value": value, "label": label})
    return sorted(opts, key=lambda o: o["value"])


def _parse_ammo_type(path: str) -> list[EnumOption]:
    root = ET.parse(path).getroot()
    opts: list[EnumOption] = []
    for ammo in root.findall(".//AMMOTYPE"):
        idx_el = ammo.find("uiIndex")
        name_el = ammo.find("name")
        if idx_el is None or name_el is None:
            continue
        try:
            value = int(idx_el.text or "")
        except (ValueError, TypeError):
            continue
        label = (name_el.text or "").strip()
        if label:
            opts.append({"value": value, "label": label})
    return sorted(opts, key=lambda o: o["value"])


def calibre_options(ctx: "InstallContext") -> list[EnumOption]:
    """Calibre options from AmmoStrings.xml (<uiIndex> → <AmmoCaliber>), mtime-cached.

    source: TableData/Items/AmmoStrings.xml — <AMMOLIST><AMMO><uiIndex>/<AmmoCaliber>
    Returns [] if the file is absent or unreadable.
    """
    return _cached_xml(ctx.items_table_path("AmmoStrings.xml"), _parse_calibre)


def ammo_type_options(ctx: "InstallContext") -> list[EnumOption]:
    """Ammo type options from AmmoTypes.xml (<uiIndex> → <name>), mtime-cached.

    source: TableData/Items/AmmoTypes.xml — <AMMOTYPELIST><AMMOTYPE><uiIndex>/<name>
    Returns [] if the file is absent or unreadable.
    """
    return _cached_xml(ctx.items_table_path("AmmoTypes.xml"), _parse_ammo_type)


# ---------------------------------------------------------------------------
# Dispatch
# ---------------------------------------------------------------------------

# Fields that map to static tables
_STATIC_MAP: dict[str, list[EnumOption]] = {
    "ubWeaponType":  WEAPON_TYPE_OPTIONS,
    "ubArmourClass": ARMOUR_CLASS_OPTIONS,
    "ubType":        EXPLOSIVE_TYPE_OPTIONS,   # on EXPLOSIVETYPE struct
    "ubMagType":     MAG_TYPE_OPTIONS,
}


def enum_options_for(
    field_key: str,
    ctx: "InstallContext",
) -> Optional[list[EnumOption]]:
    """Return the option list for *field_key*, or None if no dropdown applies.

    Args:
        field_key: The XML/struct field name, e.g. ``"ubCalibre"``.
        ctx: Live install context for XML lookups.

    Returns:
        A list of ``{"value": int, "label": str}`` dicts, or ``None`` when the
        field has no verified enum (caller should keep a plain number input).
    """
    if field_key in _STATIC_MAP:
        return _STATIC_MAP[field_key]
    if field_key == "ubCalibre":
        return calibre_options(ctx) or None
    if field_key == "ubAmmoType":
        return ammo_type_options(ctx) or None
    return None
