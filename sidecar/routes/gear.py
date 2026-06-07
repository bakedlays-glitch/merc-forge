"""Gear / loadout routes."""
from __future__ import annotations

import json
from pathlib import Path

from fastapi import APIRouter, HTTPException, Query

from mercwizard_core.cross_lock import cross_process_install_lock
from mercwizard_core.inject import starting_gear
from mercwizard_core.models import Gear

from .roster import _resolve_install
from .state import get_state

router = APIRouter()


@router.get("/gear/presets")
def list_presets() -> list[dict]:
    """List the preset loadouts shipped with the wizard."""
    presets_dir = Path(__file__).parent.parent / "mercwizard_core" / "presets"
    if not presets_dir.is_dir():
        return []
    out: list[dict] = []
    for path in sorted(presets_dir.glob("*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            out.append({
                "id": path.stem,
                "name": data.get("name", path.stem),
                "description": data.get("description", ""),
                "gear": data.get("gear", {}),
            })
        except (OSError, json.JSONDecodeError):
            continue
    return out


@router.get("/gear/{slot}")
def get_gear(slot: int, install_id: str | None = Query(default=None)) -> dict:
    info = _resolve_install(install_id)
    from mercwizard_core.install_context import make_install_context
    gear_path = make_install_context(info.path).gear_xml_path(for_write=True)
    g = starting_gear.read_slot(gear_path, slot)
    if g is None:
        raise HTTPException(status_code=404, detail={"error": "NO_GEAR_FOR_SLOT"})
    return g.model_dump()


@router.put("/gear/{slot}")
def put_gear(slot: int, gear: Gear, install_id: str | None = Query(default=None)) -> dict:
    info = _resolve_install(install_id)
    state = get_state()
    if gear.mIndex != slot:
        raise HTTPException(status_code=400, detail={
            "error": "SLOT_MISMATCH",
            "message": f"Path slot={slot} but gear.mIndex={gear.mIndex}",
        })
    from mercwizard_core.install_context import make_install_context
    gear_path = make_install_context(info.path).gear_xml_path(for_write=True)
    with cross_process_install_lock(info.id), state.write_lock:
        starting_gear.upsert(gear_path, gear)
    return {"ok": True, "slot": slot}


@router.delete("/gear/{slot}")
def delete_gear(slot: int, install_id: str | None = Query(default=None)) -> dict:
    info = _resolve_install(install_id)
    state = get_state()
    from mercwizard_core.install_context import make_install_context
    gear_path = make_install_context(info.path).gear_xml_path(for_write=True)
    with cross_process_install_lock(info.id), state.write_lock:
        removed = starting_gear.clear_slot(gear_path, slot)
    return {"ok": True, "removed": removed}
