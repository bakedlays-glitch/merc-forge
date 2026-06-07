"""App-level settings — persisted in state.json's `settings` block.

Known keys (unknown keys round-trip untouched):
  baseline_install_path  — reference install for the INI editor's
                           Author-mode "vs reference" diff. NOTE: this
                           is deliberately NOT called "stock": the
                           project's frozen base install is itself
                           modded; only the engine-mined schema defaults
                           are true stock values.
  backup_mode            — forward-compat home for backup.py's
                           documented-but-unenforced mode setting.
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from .state import get_state

router = APIRouter()


class SettingsPatch(BaseModel):
    baseline_install_path: Optional[str] = None
    backup_mode: Optional[str] = None
    # Explicit clears (PATCH semantics: omitted = leave alone,
    # empty string = delete).


@router.get("/settings")
def get_settings() -> dict:
    return get_state().get_settings()


@router.put("/settings")
def put_settings(patch: SettingsPatch) -> dict:
    update: dict = {}
    fields = patch.model_dump(exclude_unset=True)
    if "baseline_install_path" in fields:
        v = fields["baseline_install_path"]
        if v:
            if not Path(v).is_dir():
                raise HTTPException(status_code=400, detail={
                    "error": "BASELINE_NOT_FOUND",
                    "message": f"Not a directory: {v}"})
            update["baseline_install_path"] = v
        else:
            update["baseline_install_path"] = None  # delete
    if "backup_mode" in fields:
        update["backup_mode"] = fields["backup_mode"] or None
    return get_state().update_settings(update)
