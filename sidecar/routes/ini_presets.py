"""INI preset routes (docs/INI_PRESETS_SPEC.md)."""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from mercwizard_core import backup as backup_mod
from mercwizard_core.cross_lock import cross_process_install_lock
from mercwizard_core.ini_editor import IniEditorError
from mercwizard_core.ini_presets import (
    Preset,
    delete_install_preset,
    find_preset,
    install_preset_path,
    load_presets,
    preset_to_ini_changes,
    save_install_preset,
)

from .ini_editor import _editor_for, _http_from_ini_error
from .roster import _resolve_install
from .state import get_state

router = APIRouter()


def _preset_payload(p: Preset) -> dict:
    return {
        "id": p.wire_id,
        "name": p.name,
        "description": p.description,
        "default_target": p.default_target,
        "source": p.source,
        "effect_timing": p.effect_timing,
        "savegame_risk": p.savegame_risk,
        "apply_disabled": p.apply_disabled,
        "warnings": p.warnings,
        "changes": [
            {"ini_file": c.ini_file, "section": c.section, "key": c.key,
             "value": c.value, "delete": c.delete, "target": c.target}
            for c in p.changes
        ],
    }


@router.get("/ini/presets")
def list_presets(install_id: str | None = Query(default=None)) -> dict:
    info = _resolve_install(install_id)
    presets, file_warnings = load_presets(info.path)
    return {
        "presets": [_preset_payload(p) for p in presets],
        "file_warnings": file_warnings,
    }


class ApplyPresetPayload(BaseModel):
    id: str                       # wire id ("builtin:x" / "install:y")
    dry_run: bool = False


@router.post("/ini/presets/apply")
def apply_preset(
    payload: ApplyPresetPayload,
    install_id: str | None = Query(default=None),
) -> dict:
    info = _resolve_install(install_id)
    preset = find_preset(info.path, payload.id)
    if preset is None:
        raise HTTPException(status_code=404, detail={
            "error": "PRESET_NOT_FOUND", "message": f"No preset {payload.id}"})
    editor = _editor_for(info)
    try:
        by_target = preset_to_ini_changes(preset)
    except IniEditorError as e:
        raise _http_from_ini_error(e)

    state = get_state()
    results = []
    if payload.dry_run:
        for target, changes in by_target.items():
            try:
                plan = editor.apply_changes(changes, target,
                                            exe_name=info.exe_path.name,
                                            dry_run=True)
            except IniEditorError as e:
                raise _http_from_ini_error(e)
            results.append({"target": target, **plan})
        return {"ok": True, "dry_run": True, "preset": payload.id,
                "batches": results, "effect_timing": preset.effect_timing,
                "savegame_risk": preset.savegame_risk}

    with cross_process_install_lock(info.id), state.write_lock:
        # One snapshot covering every file any target-batch will touch.
        targets = []
        try:
            for target, changes in by_target.items():
                seen = set()
                for c in changes:
                    if c.ini_file not in seen:
                        seen.add(c.ini_file)
                        targets.append(editor.write_target(c.ini_file, target))
        except IniEditorError as e:
            raise _http_from_ini_error(e)
        entry = backup_mod.snapshot(
            install_root=info.path, install_id=info.id,
            files_to_back_up=[p for p in targets if p.is_file()],
            reason=f"ini_preset_{preset.id}")
        created = [p for p in targets if not p.is_file()]
        applied = 0
        for target, changes in by_target.items():
            try:
                out = editor.apply_changes(changes, target,
                                           exe_name=info.exe_path.name)
            except IniEditorError as e:
                raise _http_from_ini_error(e)
            applied += out["applied"]
        if created:
            backup_mod.record_files_created(
                entry.id, info.id, [p for p in created if p.is_file()])
    return {"ok": True, "dry_run": False, "preset": payload.id,
            "applied": applied, "backup_id": entry.id,
            "effect_timing": preset.effect_timing}


class CreatePresetPayload(BaseModel):
    id: Optional[str] = None      # derived from name when omitted
    name: str
    description: str = ""
    default_target: str = "override"
    changes: list[dict]


@router.post("/ini/presets")
def create_preset(
    payload: CreatePresetPayload,
    install_id: str | None = Query(default=None),
) -> dict:
    info = _resolve_install(install_id)
    slug = payload.id or "".join(
        ch if ch.isalnum() else "_" for ch in payload.name.lower()).strip("_")
    if not slug:
        raise HTTPException(status_code=400, detail={
            "error": "BAD_PRESET", "message": "Preset needs a name"})
    state = get_state()
    with cross_process_install_lock(info.id), state.write_lock:
        path = install_preset_path(info.path)
        if path.is_file():
            backup_mod.snapshot(
                install_root=info.path, install_id=info.id,
                files_to_back_up=[path], reason="ini_preset_author")
        try:
            preset = save_install_preset(info.path, {
                "id": slug, "name": payload.name,
                "description": payload.description,
                "default_target": payload.default_target,
                "changes": payload.changes,
            })
        except ValueError as e:
            raise HTTPException(status_code=400, detail={
                "error": "BAD_PRESET", "message": str(e)})
    return _preset_payload(preset)


@router.delete("/ini/presets/{wire_id}")
def delete_preset(
    wire_id: str,
    install_id: str | None = Query(default=None),
) -> dict:
    info = _resolve_install(install_id)
    if not wire_id.startswith("install:"):
        raise HTTPException(status_code=403, detail={
            "error": "BUILTIN_PRESET",
            "message": "Built-in presets can't be deleted"})
    state = get_state()
    with cross_process_install_lock(info.id), state.write_lock:
        path = install_preset_path(info.path)
        if path.is_file():
            backup_mod.snapshot(
                install_root=info.path, install_id=info.id,
                files_to_back_up=[path], reason="ini_preset_delete")
        ok = delete_install_preset(info.path, wire_id.split(":", 1)[1])
    if not ok:
        raise HTTPException(status_code=404, detail={
            "error": "PRESET_NOT_FOUND", "message": f"No preset {wire_id}"})
    return {"ok": True, "deleted": wire_id}
