"""Setup-flow routes (docs/SETUP_FLOW_SPEC.md).

The display step is renderer-aware: when cnc-ddraw owns the window
(ddraw.dll + ddraw.ini present at the install root) the flow edits
`ddraw.ini [ddraw]` keys — the file that actually controls the window
under the golden config; otherwise it falls back to Ja2.ini's
SCREEN_RESOLUTION codes. ddraw.ini is outside the INI editor's whitelist
on purpose; this module writes it via the same self-verifying
surgical_upsert (documented exemption).
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from mercwizard_core import backup as backup_mod
from mercwizard_core.cross_lock import cross_process_install_lock
from mercwizard_core.graphics import graphics_status
from mercwizard_core.ini_editor import (
    IniChange,
    IniEditorError,
    _decode,
    game_running,
    parse_ini_map,
    surgical_upsert,
)
from mercwizard_core.ini_presets import find_preset, preset_to_ini_changes

from .ini_editor import _editor_for, _http_from_ini_error
from .roster import _resolve_install
from .state import get_state

router = APIRouter()

# Engine-verified SCREEN_RESOLUTION codes (the schema's list_values is
# EMPTY — these are hardcoded by design; source: JA2.ini's own code
# table, verified against the engine in the 2026-06-07 review).
RESOLUTION_CODES = [
    {"code": 4, "label": "1280 x 720"},
    {"code": 5, "label": "1024 x 768"},
    {"code": 11, "label": "1600 x 900"},
    {"code": 19, "label": "1680 x 1050"},
    {"code": 20, "label": "1920 x 1080"},
    {"code": 22, "label": "1920 x 1200"},
    {"code": 23, "label": "2560 x 1440"},
    {"code": 24, "label": "2560 x 1600"},
]


def _ddraw_path(install_root) -> Optional[object]:
    p = install_root / "ddraw.ini"
    if p.is_file() and (install_root / "ddraw.dll").is_file():
        return p
    return None


@router.get("/setup/state")
def setup_state(install_id: str | None = Query(default=None)) -> dict:
    info = _resolve_install(install_id)
    editor = _editor_for(info)
    settings = get_state().get_settings()

    ddraw = _ddraw_path(info.path)
    if ddraw is not None:
        m = parse_ini_map(_decode(ddraw.read_bytes())[0])
        sect = next((v for k, v in m.items() if k.lower() == "ddraw"), {})
        display = {
            "renderer": "cnc-ddraw",
            "windowed": (sect.get("windowed", "false").lower() == "true"
                         and sect.get("fullscreen", "false").lower() != "true"),
            "resolution": sect.get("inject_resolution", ""),
            "available_resolutions": [r["label"].replace(" x ", "x")
                                      for r in RESOLUTION_CODES],
        }
    else:
        eff = editor.effective("Ja2.ini")["sections"]
        ja2 = next((v for k, v in eff.items() if k.lower() == "ja2 settings"), {})

        def _val(key: str) -> Optional[str]:
            for k, v in ja2.items():
                if k.lower() == key.lower():
                    return v.get("value")
            return None

        display = {
            "renderer": "engine",
            "windowed": _val("SCREEN_MODE_WINDOWED") == "1",
            "resolution": _val("SCREEN_RESOLUTION"),
            "available_resolutions": RESOLUTION_CODES,
        }

    intro = {}
    eff = editor.effective("Ja2.ini")["sections"]
    ja2 = next((v for k, v in eff.items() if k.lower() == "ja2 settings"), {})
    for key in ("PLAY_INTRO", "TOOLTIP_SCALE_FACTOR"):
        for k, v in ja2.items():
            if k.lower() == key.lower():
                intro[key] = v.get("value")

    return {
        "display": display,
        "intro": intro,
        "graphics": {"components": graphics_status(info.path)},
        "offered": info.id in (settings.get("setup_offered_installs") or []),
    }


class DisplayChoice(BaseModel):
    windowed: Optional[bool] = None
    # cnc-ddraw: "1280x720" free text; engine: a SCREEN_RESOLUTION code int
    resolution: Optional[str] = None


class IntroChoice(BaseModel):
    play_intro: Optional[bool] = None
    tooltip_scale: Optional[int] = None


class SetupApplyPayload(BaseModel):
    display: Optional[DisplayChoice] = None
    intro: Optional[IntroChoice] = None
    preset_ids: list[str] = []
    dry_run: bool = False


@router.post("/setup/apply")
def setup_apply(
    payload: SetupApplyPayload,
    install_id: str | None = Query(default=None),
) -> dict:
    """One staged batch: ddraw/Ja2.ini display keys + intro keys +
    chosen presets. One lock, one backup over every touched file.
    Graphics deploy is intentionally NOT here (separate route, own
    backup — the flow's completion text names both)."""
    info = _resolve_install(install_id)
    editor = _editor_for(info)
    state = get_state()

    if not payload.dry_run and game_running(info.exe_path.name):
        raise HTTPException(status_code=409, detail={
            "error": "GAME_RUNNING",
            "message": "Close JA2 before applying setup."})

    # Stage 1: collect everything.
    ja2_changes: list[IniChange] = []
    ddraw_changes: list[IniChange] = []
    ddraw = _ddraw_path(info.path)

    if payload.display is not None:
        d = payload.display
        if ddraw is not None:
            if d.windowed is not None:
                ddraw_changes.append(IniChange("ddraw", "windowed",
                                               "true" if d.windowed else "false"))
                ddraw_changes.append(IniChange("ddraw", "fullscreen",
                                               "false" if d.windowed else "true"))
            if d.resolution:
                ddraw_changes.append(IniChange("ddraw", "inject_resolution", d.resolution))
        else:
            if d.windowed is not None:
                ja2_changes.append(IniChange("Ja2 Settings", "SCREEN_MODE_WINDOWED",
                                             "1" if d.windowed else "0",
                                             ini_file="Ja2.ini"))
            if d.resolution:
                ja2_changes.append(IniChange("Ja2 Settings", "SCREEN_RESOLUTION",
                                             str(d.resolution), ini_file="Ja2.ini"))
    if payload.intro is not None:
        if payload.intro.play_intro is not None:
            ja2_changes.append(IniChange("Ja2 Settings", "PLAY_INTRO",
                                         "1" if payload.intro.play_intro else "0",
                                         ini_file="Ja2.ini"))
        if payload.intro.tooltip_scale is not None:
            ja2_changes.append(IniChange("Ja2 Settings", "TOOLTIP_SCALE_FACTOR",
                                         str(payload.intro.tooltip_scale),
                                         ini_file="Ja2.ini"))

    preset_batches: list[tuple[str, dict[str, list[IniChange]]]] = []
    for wire_id in payload.preset_ids:
        preset = find_preset(info.path, wire_id)
        if preset is None:
            raise HTTPException(status_code=404, detail={
                "error": "PRESET_NOT_FOUND", "message": f"No preset {wire_id}"})
        try:
            preset_batches.append((wire_id, preset_to_ini_changes(preset)))
        except IniEditorError as e:
            raise _http_from_ini_error(e)

    if payload.dry_run:
        plan: list[dict] = []
        if ddraw_changes:
            current = parse_ini_map(_decode(ddraw.read_bytes())[0]) if ddraw else {}
            sect = next((v for k, v in current.items() if k.lower() == "ddraw"), {})
            plan.append({"file": "ddraw.ini", "changes": [
                {"section": c.section, "key": c.key, "value": c.value,
                 "current": next((v for k, v in sect.items()
                                  if k.lower() == c.key.lower()), None)}
                for c in ddraw_changes]})
        try:
            if ja2_changes:
                p = editor.apply_changes(ja2_changes, "canon",
                                         exe_name=info.exe_path.name, dry_run=True)
                plan.extend(p["files"])
            for wire_id, by_target in preset_batches:
                for target, changes in by_target.items():
                    p = editor.apply_changes(changes, target,
                                             exe_name=info.exe_path.name,
                                             dry_run=True)
                    for f in p["files"]:
                        f["preset"] = wire_id
                    plan.extend(p["files"])
        except IniEditorError as e:
            raise _http_from_ini_error(e)
        return {"ok": True, "dry_run": True, "plan": plan}

    # Stage 2: apply under one lock + one snapshot.
    with cross_process_install_lock(info.id), state.write_lock:
        touched = []
        if ddraw is not None and ddraw_changes:
            touched.append(ddraw)
        try:
            if ja2_changes:
                seen = set()
                for c in ja2_changes:
                    if c.ini_file not in seen:
                        seen.add(c.ini_file)
                        touched.append(editor.write_target(c.ini_file, "canon"))
            for _, by_target in preset_batches:
                for target, changes in by_target.items():
                    seen = set()
                    for c in changes:
                        if c.ini_file not in seen:
                            seen.add(c.ini_file)
                            touched.append(editor.write_target(c.ini_file, target))
        except IniEditorError as e:
            raise _http_from_ini_error(e)

        entry = backup_mod.snapshot(
            install_root=info.path, install_id=info.id,
            files_to_back_up=[p for p in touched if p.is_file()],
            reason="setup_apply")
        created = [p for p in touched if not p.is_file()]

        applied = 0
        try:
            if ddraw is not None and ddraw_changes:
                surgical_upsert(ddraw, ddraw_changes)
                applied += len(ddraw_changes)
            if ja2_changes:
                out = editor.apply_changes(ja2_changes, "canon",
                                           exe_name=info.exe_path.name)
                applied += out["applied"]
            for _, by_target in preset_batches:
                for target, changes in by_target.items():
                    out = editor.apply_changes(changes, target,
                                               exe_name=info.exe_path.name)
                    applied += out["applied"]
        except IniEditorError as e:
            raise _http_from_ini_error(e)
        if created:
            backup_mod.record_files_created(
                entry.id, info.id, [p for p in created if p.is_file()])

    return {"ok": True, "dry_run": False, "applied": applied,
            "backup_id": entry.id}


@router.post("/setup/offered")
def mark_offered(install_id: str | None = Query(default=None)) -> dict:
    info = _resolve_install(install_id)
    state = get_state()
    current = list(state.get_settings().get("setup_offered_installs") or [])
    if info.id not in current:
        current.append(info.id)
        state.update_settings({"setup_offered_installs": current})
    return {"ok": True, "offered": current}
