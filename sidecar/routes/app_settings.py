"""App-level settings — persisted in state.json's `settings` block.

Known keys (unknown keys round-trip untouched):
  baseline_install_path  — reference install for the INI editor's
                           Author-mode "vs reference" diff. NOTE: this
                           is deliberately NOT called "stock": the
                           project's frozen base install is itself
                           modded; only the engine-mined schema defaults
                           are true stock values.
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from mercwizard_core.voice_lab.recipes import ensure_workspace_is_external

from .state import get_state

router = APIRouter()


class SettingsPatch(BaseModel):
    baseline_install_path: Optional[str] = None
    # Voice Lab keeps authoring-only imports and immutable recipe history
    # outside the game install.  Tool paths are explicit so ambient PATH is
    # never trusted for audio/transcription work.
    voice_authoring_workspace: Optional[str] = None
    voice_ffmpeg_path: Optional[str] = None
    voice_transcriber_python: Optional[str] = None
    # Install ids whose first-run setup offer has been shown/dismissed.
    # NOTE: this model is CLOSED — unknown keys sent by clients are
    # silently dropped (adversarial-review finding); every persisted
    # setting needs an explicit field here.
    setup_offered_installs: Optional[list[str]] = None
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
    if "setup_offered_installs" in fields:
        update["setup_offered_installs"] = fields["setup_offered_installs"] or None
    for key in ("voice_authoring_workspace", "voice_ffmpeg_path", "voice_transcriber_python"):
        if key not in fields:
            continue
        value = fields[key]
        if not value:
            update[key] = None
            continue
        path = Path(value)
        if key == "voice_authoring_workspace":
            if not path.is_dir():
                raise HTTPException(status_code=400, detail={
                    "error": "VOICE_WORKSPACE_NOT_FOUND",
                    "message": "Voice authoring workspace must be an existing directory",
                })
            try:
                path = ensure_workspace_is_external(
                    path, (install.path for install in get_state().list_installs()),
                )
            except ValueError as exc:
                if str(exc).startswith("VOICE_WORKSPACE_IN_INSTALL:"):
                    raise HTTPException(status_code=400, detail={
                        "error": "VOICE_WORKSPACE_IN_INSTALL",
                        "message": "Voice authoring workspace must be outside every registered game install",
                    }) from exc
                raise HTTPException(status_code=400, detail={
                    "error": "VOICE_WORKSPACE_INVALID",
                    "message": "Voice authoring workspace could not be resolved safely",
                }) from exc
        elif not path.is_file():
            raise HTTPException(status_code=400, detail={
                "error": "VOICE_TOOL_NOT_FOUND",
                "message": "Configured Voice Lab tool is not an existing file",
            })
        update[key] = str(path)
    return get_state().update_settings(update)
