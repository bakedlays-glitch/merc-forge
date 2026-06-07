"""Trait catalogs for the wizard's UI.

Two coexisting systems:
- OT (Old Traits): 2 slots, IDs 0-15
- NT (New Traits / STOMP): up to 30 slots, IDs 0-23 (Major + Minor)

Ja2_Options.ini's ENABLE_NEW_TRAIT_SYSTEM flag determines which is active.
The wizard reads the flag at install-set time and shows the appropriate UI.

NT Major traits can be written twice to grant "Expert" tier (engine reads
via NUM_SKILL_TRAITS slot-count, not a separate ID).
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Literal


class TraitTier(str, Enum):
    MAJOR = "Major"
    MINOR = "Minor"


@dataclass(frozen=True)
class Trait:
    id: int
    name: str
    tier: TraitTier   # NT only; OT all use Major


OLD_TRAITS: dict[int, Trait] = {
    0: Trait(0, "None", TraitTier.MAJOR),
    1: Trait(1, "Lock Picking", TraitTier.MAJOR),
    2: Trait(2, "Hand-to-Hand", TraitTier.MAJOR),
    3: Trait(3, "Electronics", TraitTier.MAJOR),
    4: Trait(4, "Night Ops", TraitTier.MAJOR),
    5: Trait(5, "Throwing", TraitTier.MAJOR),
    6: Trait(6, "Teaching", TraitTier.MAJOR),
    7: Trait(7, "Heavy Weapons", TraitTier.MAJOR),
    8: Trait(8, "Auto Weapons", TraitTier.MAJOR),
    9: Trait(9, "Stealthy", TraitTier.MAJOR),
    10: Trait(10, "Ambidextrous", TraitTier.MAJOR),
    11: Trait(11, "Thiefing", TraitTier.MAJOR),
    12: Trait(12, "Martial Arts", TraitTier.MAJOR),
    13: Trait(13, "Knifing", TraitTier.MAJOR),
    14: Trait(14, "Rooftop Sniping", TraitTier.MAJOR),
    15: Trait(15, "Camouflaged", TraitTier.MAJOR),
}


NEW_TRAITS: dict[int, Trait] = {
    0: Trait(0, "None", TraitTier.MINOR),
    1: Trait(1, "Auto Weapons", TraitTier.MAJOR),
    2: Trait(2, "Heavy Weapons", TraitTier.MAJOR),
    3: Trait(3, "Marksman", TraitTier.MAJOR),
    4: Trait(4, "Hunter/Ranger", TraitTier.MAJOR),
    5: Trait(5, "Gunslinger", TraitTier.MAJOR),
    6: Trait(6, "Hand-to-Hand", TraitTier.MAJOR),
    7: Trait(7, "Squadleader", TraitTier.MAJOR),
    8: Trait(8, "Engineer", TraitTier.MAJOR),
    9: Trait(9, "Paramedic", TraitTier.MAJOR),
    10: Trait(10, "Ambidextrous", TraitTier.MINOR),
    11: Trait(11, "Melee", TraitTier.MINOR),
    12: Trait(12, "Throwing", TraitTier.MINOR),
    13: Trait(13, "Night Ops", TraitTier.MINOR),
    14: Trait(14, "Stealthy", TraitTier.MINOR),
    15: Trait(15, "Athletics", TraitTier.MINOR),
    16: Trait(16, "Bodybuilding", TraitTier.MINOR),
    17: Trait(17, "Demolitions", TraitTier.MINOR),
    18: Trait(18, "Teaching", TraitTier.MINOR),
    19: Trait(19, "Scouting", TraitTier.MINOR),
    20: Trait(20, "Covert Ops", TraitTier.MAJOR),
    21: Trait(21, "Radio Operator", TraitTier.MINOR),
    22: Trait(22, "Snitch", TraitTier.MINOR),
    23: Trait(23, "Survival", TraitTier.MINOR),
}


def get_active_traits(use_new_traits: bool) -> dict[int, Trait]:
    """Return the catalog the UI should expose, given the install's flag."""
    return NEW_TRAITS if use_new_traits else OLD_TRAITS


def trait_name(trait_id: int, use_new_traits: bool = True) -> str:
    """Look up a trait's display name; 'Unknown' if ID out of range."""
    catalog = get_active_traits(use_new_traits)
    trait = catalog.get(trait_id)
    return trait.name if trait else f"Unknown ({trait_id})"


def is_expert(slots: list[int], target_trait_id: int) -> bool:
    """True if `slots` contains `target_trait_id` twice (the Expert tier rule).

    Only applies to NT Major traits.
    """
    if target_trait_id == 0:
        return False
    return slots.count(target_trait_id) >= 2
