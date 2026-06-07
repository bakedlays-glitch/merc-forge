"""Static slot-lock map — back-compat surface for ``GET /slots/locks``.

This module predates :mod:`slot_picker` and used to be the only slot-tier
source of truth. As of the engine-faithful rewrite, named-slot truth lives
in :func:`slot_picker.engine_named_slots`; this module re-derives the same
tier classifications from there for the legacy endpoint, then layers on the
vanilla 1.13 data conventions that the legacy endpoint also carries (the
named 1.13-expansion AIM/MERC slots — Gary/Doc/Boss/etc. — that aren't named
in ``soldier profile type.h`` but are named in the vanilla AIMAvailability.xml).

New code should use :func:`slot_picker.build_slot_picker` instead — it joins
the engine-named table with live XML data and is install-aware. The
``/slots/locks`` endpoint stays alive for callers that haven't migrated.

Differences vs. the pre-rewrite version:
  * Slots 51-56 are now SAFE (was LOCKED). Modern 1.13 reads <Type> from
    MercProfiles.xml per ``fReadProfileDataFromXML=TRUE``, so the legacy
    IMP-fallback that mis-tagged 51-56 is dead. (Per-install gating only
    matters in :func:`slot_picker.build_slot_picker`, which has access to
    the install's Ja2_Options.ini.)
  * Named-slot data is sourced from :func:`slot_picker.engine_named_slots`
    so there's a single place to add new named slots.
"""
from __future__ import annotations

from enum import Enum
from typing import Optional

from pydantic import BaseModel

from .slot_picker import engine_named_slots


class SlotLockTier(str, Enum):
    SAFE = "safe"
    VANILLA_OVERWRITE = "vanilla_overwrite"
    QUEST_BOUND = "quest_bound"
    LOCKED = "locked"


class SlotLockInfo(BaseModel):
    slot: int
    tier: SlotLockTier
    name: Optional[str] = None
    role: Optional[str] = None


# ──────────────────────────────────────────────────────────────────────────
#  1.13 expansion-named slots — data convention, NOT in engine source enum.
# ──────────────────────────────────────────────────────────────────────────
# Vanilla AIMAvailability.xml ships these characters but they aren't named
# constants in soldier profile type.h. The legacy /slots/locks endpoint
# surfaces them as VANILLA_OVERWRITE so the warning modal can say "you're
# about to delete Gary" instead of "you're about to delete slot 223".
_VANILLA_DATA_NAMED: dict[int, tuple[str, str]] = {
    223: ("GARY",   "1.13 expansion AIM (AimBioID=62)"),
    228: ("DOC",    "1.13 expansion AIM (AimBioID=68)"),
    230: ("BOSS",   "1.13 expansion AIM (AimBioID=48)"),
    231: ("SNAKE",  "1.13 expansion AIM (AimBioID=49)"),
    232: ("SPAM",   "1.13 expansion AIM (AimBioID=50)"),
    233: ("SPIKE",  "1.13 expansion AIM (AimBioID=51)"),
    234: ("JIMMY",  "1.13 expansion AIM (AimBioID=52)"),
    235: ("LEECH",  "1.13 expansion AIM (AimBioID=56)"),
    236: ("BOB",    "1.13 expansion AIM (AimBioID=53)"),
    237: ("KELLY",  "1.13 expansion AIM (AimBioID=54)"),
    238: ("VINNY",  "1.13 expansion AIM (AimBioID=55)"),
    239: ("KABOOM", "1.13 expansion AIM (AimBioID=57)"),
    240: ("BUD",    "1.13 expansion AIM (AimBioID=58)"),
    241: ("RUSTY",  "1.13 expansion AIM (AimBioID=59)"),
    242: ("NEEDLE", "1.13 expansion AIM (AimBioID=60)"),
    243: ("SCREW",  "1.13 expansion AIM (AimBioID=61)"),
    245: ("MOUSE",  "1.13 expansion AIM (AimBioID=64)"),
    246: ("HECTOR", "1.13 expansion AIM (AimBioID=65)"),
    248: ("STELLA", "1.13 expansion AIM (AimBioID=66)"),
    250: ("MOSES",  "1.13 expansion AIM (AimBioID=67)"),
    251: ("SMOKE",  "1.13 expansion AIM (AimBioID=63)"),
    253: ("NPC170", "1.13 expansion M.E.R.C. tail slot — mods deploy real characters here"),
}

_VANILLA_AIM_RANGE = range(0, 40)


def slot_lock_info(slot: int) -> SlotLockInfo:
    """Return the lock tier + named-constant + role for ``slot`` (0-254).

    Out-of-range slots return SAFE.
    """
    if not 0 <= slot <= 254:
        return SlotLockInfo(slot=slot, tier=SlotLockTier.SAFE, name=None, role=None)

    engine = engine_named_slots(is_ub=False).get(slot)
    if engine is not None:
        if engine.is_vehicle_slot or engine.is_main_story_locked:
            tier = SlotLockTier.LOCKED
        elif engine.is_quest_bound:
            tier = SlotLockTier.QUEST_BOUND
        else:
            tier = SlotLockTier.VANILLA_OVERWRITE
        return SlotLockInfo(slot=slot, tier=tier, name=engine.name, role=engine.role)

    if slot in _VANILLA_DATA_NAMED:
        name, role = _VANILLA_DATA_NAMED[slot]
        return SlotLockInfo(
            slot=slot,
            tier=SlotLockTier.VANILLA_OVERWRITE,
            name=name,
            role=role,
        )

    if slot in _VANILLA_AIM_RANGE:
        return SlotLockInfo(
            slot=slot,
            tier=SlotLockTier.VANILLA_OVERWRITE,
            name=None,
            role=f"Vanilla AIM slot (would overwrite the original AIM merc at slot {slot})",
        )

    # 51-56: gap. Modern 1.13 (fReadProfileDataFromXML=TRUE) reads <Type> from
    # MercProfiles.xml, so these are SAFE. Per-install gating only happens in
    # slot_picker.build_slot_picker; the static endpoint defaults to "modern".
    return SlotLockInfo(slot=slot, tier=SlotLockTier.SAFE, name=None, role=None)


def all_slot_locks() -> list[SlotLockInfo]:
    """Return the full 0-254 map. Used by the GET /slots/locks endpoint."""
    return [slot_lock_info(slot) for slot in range(255)]
