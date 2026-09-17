"""Engine-faithful slot picker endpoint.

Returns the full 0-254 slot map with live AIM/MERC row data joined from
AIMAvailability.xml + MercAvailability.xml + MercProfiles.xml. Frontend
fetches this through ``useSlotPicker()`` (30s staleTime) and renders the
picker grid + filter pills from it.

The legacy ``GET /slots/locks`` endpoint (in ``routes/slots.py``) still
returns its static engine-only tier map for backwards compatibility with
older clients that haven't migrated yet.
"""
from __future__ import annotations

from fastapi import APIRouter, Query

from mercwizard_core.slot_picker import SlotPickerResponse, build_slot_picker

# Single owner of install resolution — this module used to carry a
# character-for-character re-implementation.
from .roster import _resolve_install

router = APIRouter()


@router.get("/slots/picker")
def get_slot_picker(
    install_id: str | None = Query(default=None),
) -> SlotPickerResponse:
    info = _resolve_install(install_id)
    return build_slot_picker(
        info.path,
        vfs_config_path=info.vfs_config_path,
    )
