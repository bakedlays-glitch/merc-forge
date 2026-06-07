"""INI editor routes — schema-driven engine-config editing.

Write strategy is gated by docs/INI_EDITOR_ENGINE_FACTS.md (author mode
edits the mod canon in place; play mode writes per-campaign `.Override`
files into the engine write profile). All mutation goes through
mercwizard_core.ini_editor's self-verifying surgical writer, under the
standard cross-process install lock + state write lock, with a
backup.py snapshot first (restorable via POST /backup/restore).
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from mercwizard_core import backup as backup_mod
from mercwizard_core.cross_lock import cross_process_install_lock
from mercwizard_core.ini_editor import (
    EDITABLE_INIS,
    IniChange,
    IniEditor,
    IniEditorError,
    canonical_ini_name,
    list_schemas,
    load_schema,
    validate_against_schema,
)
from mercwizard_core.vfs import (
    VfsConfigError,
    compute_vfs_mismatch,
    parse_vfs_config,
)

from .roster import _resolve_install
from .state import get_state

router = APIRouter()


# ───────────────────────────── models ───────────────────────────────────────

class ChangeItem(BaseModel):
    ini_file: str
    section: str
    key: str
    # None/omitted = delete the key (the override is removed; the base
    # layer's value shows through again).
    value: Optional[str] = None
    delete: bool = False


class ChangesPayload(BaseModel):
    target: str = Field(pattern="^(canon|override)$")
    changes: list[ChangeItem]
    dry_run: bool = False


class ChangeResult(BaseModel):
    ini_file: str
    section: str
    key: str
    status: str                      # "applied" | "planned"
    warning: Optional[str] = None


class ApplyResult(BaseModel):
    ok: bool
    dry_run: bool
    target: str
    applied: int
    backup_id: Optional[str]
    files: list[dict]
    results: list[ChangeResult]


# ───────────────────────────── helpers ──────────────────────────────────────

def _editor_for(info) -> IniEditor:
    try:
        layout = parse_vfs_config(info.path)
    except VfsConfigError as e:
        raise HTTPException(status_code=400, detail={
            "error": "VFS_CONFIG_BROKEN", "message": str(e),
        })
    return IniEditor(layout)


def _http_from_ini_error(e: IniEditorError) -> HTTPException:
    status = {
        "GAME_RUNNING": 409,
        "INI_FILE_UNKNOWN": 404,
        "SCHEMA_NOT_FOUND": 404,
        "PLAY_MODE_UNSUPPORTED": 422,
        "JA2_INI_NOT_FOUND": 404,
    }.get(e.code, 400)
    return HTTPException(status_code=status, detail={
        "error": e.code, "message": str(e),
    })


# ───────────────────────────── reads ────────────────────────────────────────

@router.get("/ini/schemas")
def get_schemas(install_id: str | None = Query(default=None)) -> dict:
    info = _resolve_install(install_id)
    editor = _editor_for(info)
    ewp = editor.layout.engine_write_profile()
    return {
        "schemas": list_schemas(),
        "editable": sorted(EDITABLE_INIS.values()),
        "writable_profile": ewp.name if ewp else None,
        "profile_root": str(ewp.profile_root) if ewp and ewp.profile_root else None,
        "vfs_mismatch": compute_vfs_mismatch(info.path, info.vfs_config_path),
    }


@router.get("/ini/schema/{ini_file}")
def get_schema(ini_file: str) -> dict:
    try:
        return load_schema(ini_file)
    except IniEditorError as e:
        raise _http_from_ini_error(e)


@router.get("/ini/effective/{ini_file}")
def get_effective(
    ini_file: str,
    install_id: str | None = Query(default=None),
    baseline: str | None = Query(
        default=None,
        description="Optional path to a stock-1.13 install used for "
                    "per-key stock_value (the author-hat 'vs stock' diff)."),
) -> dict:
    info = _resolve_install(install_id)
    editor = _editor_for(info)
    if baseline:
        bp = Path(baseline)
        if not bp.is_dir():
            raise HTTPException(status_code=400, detail={
                "error": "BASELINE_NOT_FOUND", "message": f"Not a directory: {baseline}"})
        editor.baseline_root = bp
    try:
        result = editor.effective(ini_file)
    except IniEditorError as e:
        raise _http_from_ini_error(e)
    result["vfs_mismatch"] = compute_vfs_mismatch(info.path, info.vfs_config_path)
    return result


@router.get("/ini/overrides")
def get_overrides(install_id: str | None = Query(default=None)) -> dict:
    """Every Play-mode override in the active campaign's profile — the
    'My changes' view."""
    info = _resolve_install(install_id)
    editor = _editor_for(info)
    ewp = editor.layout.engine_write_profile()
    return {
        "writable_profile": ewp.name if ewp else None,
        "profile_root": str(ewp.profile_root) if ewp and ewp.profile_root else None,
        "overrides": editor.overrides(),
    }


# ───────────────────────────── writes ───────────────────────────────────────

@router.post("/ini/changes", response_model=ApplyResult)
def apply_changes(
    payload: ChangesPayload,
    install_id: str | None = Query(default=None),
) -> ApplyResult:
    """Atomic batch apply. One backup snapshot covers every file about to
    be mutated; each file is rewritten once via the self-verifying
    surgical writer. `dry_run=true` returns the plan (targets + advisory
    schema warnings) without touching disk."""
    info = _resolve_install(install_id)
    state = get_state()
    editor = _editor_for(info)

    changes: list[IniChange] = []
    results: list[ChangeResult] = []
    schema_cache: dict[str, dict] = {}
    try:
        for item in payload.changes:
            canon = canonical_ini_name(item.ini_file)
            value = None if item.delete else item.value
            if value is None and not item.delete:
                raise IniEditorError(
                    "BAD_VALUE",
                    f"{canon} {item.section}/{item.key}: provide a value "
                    "or set delete=true")
            ch = IniChange(section=item.section, key=item.key,
                           value=value, ini_file=canon)
            ch.validate()
            changes.append(ch)
            if canon not in schema_cache:
                schema_cache[canon] = load_schema(canon)
            warning = (validate_against_schema(schema_cache[canon], ch)
                       if value is not None else None)
            results.append(ChangeResult(
                ini_file=canon, section=item.section, key=item.key,
                status="planned" if payload.dry_run else "applied",
                warning=warning,
            ))
    except IniEditorError as e:
        raise _http_from_ini_error(e)

    if payload.dry_run:
        try:
            plan = editor.apply_changes(changes, payload.target,
                                        exe_name=info.exe_path.name,
                                        dry_run=True)
        except (IniEditorError, VfsConfigError) as e:
            if isinstance(e, IniEditorError):
                raise _http_from_ini_error(e)
            raise HTTPException(status_code=400, detail={
                "error": "VFS_CONFIG_BROKEN", "message": str(e)})
        return ApplyResult(ok=True, dry_run=True, target=payload.target,
                           applied=0, backup_id=None,
                           files=plan["files"], results=results)

    with cross_process_install_lock(info.id), state.write_lock:
        # Resolve every write target first (raises before any disk touch),
        # snapshot them all in one backup entry, then apply.
        try:
            targets: dict[str, Path] = {}
            for ch in changes:
                if ch.ini_file not in targets:
                    targets[ch.ini_file] = editor.write_target(
                        ch.ini_file, payload.target)
        except (IniEditorError, VfsConfigError) as e:
            if isinstance(e, IniEditorError):
                raise _http_from_ini_error(e)
            raise HTTPException(status_code=400, detail={
                "error": "VFS_CONFIG_BROKEN", "message": str(e)})

        backup_entry = backup_mod.snapshot(
            install_root=info.path,
            install_id=info.id,
            files_to_back_up=[p for p in targets.values() if p.is_file()],
            reason=f"ini_{payload.target}_edit",
        )
        created_candidates = [p for p in targets.values() if not p.is_file()]

        try:
            outcome = editor.apply_changes(changes, payload.target,
                                           exe_name=info.exe_path.name)
        except (IniEditorError, VfsConfigError) as e:
            if isinstance(e, IniEditorError):
                raise _http_from_ini_error(e)
            raise HTTPException(status_code=400, detail={
                "error": "VFS_CONFIG_BROKEN", "message": str(e)})

        if created_candidates:
            backup_mod.record_files_created(
                backup_entry.id, info.id,
                [p for p in created_candidates if p.is_file()],
            )

    return ApplyResult(
        ok=True, dry_run=False, target=payload.target,
        applied=outcome["applied"], backup_id=backup_entry.id,
        files=outcome["files"], results=results,
    )
