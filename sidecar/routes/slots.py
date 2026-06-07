"""Slot-lock map endpoint.

Returns the 0-254 slot map with each slot's lock tier + named-constant name
+ role description. Frontend fetches this once on app load and uses it for:
  - Color-coding Roster + SlotPicker
  - Pre-write warning modal in Create/Edit/Move/Duplicate/Import

The map is static (compiled from `Tactical/Soldier Profile.h` source), so the
endpoint just serializes `slot_locks.all_slot_locks()`.
"""
from __future__ import annotations

from fastapi import APIRouter

from mercwizard_core.slot_locks import SlotLockInfo, all_slot_locks

router = APIRouter()


@router.get("/slots/locks")
def get_slot_locks() -> list[SlotLockInfo]:
    return all_slot_locks()
