"""`.wmerc` bundle routes — export and import."""
from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from mercwizard_core import bundle as bundle_mod
from mercwizard_core.cross_lock import cross_process_install_lock

from .roster import _resolve_install
from .state import get_state

router = APIRouter()


class ExportPayload(BaseModel):
    slot: int
    out_path: str
    author_name: str | None = None
    license: str = "unspecified"
    intended_mod: str = "any"
    notes: str | None = None
    include_voice: bool = True
    portrait_source_png: str | None = None
    extreme_master_png: str | None = None
    bigface_source_png: str | None = None
    anim_eye_1: str | None = None
    anim_eye_2: str | None = None
    anim_mouth_1: str | None = None
    anim_mouth_2: str | None = None
    anim_mouth_3: str | None = None
    preview_png: str | None = None


class ImportPayload(BaseModel):
    bundle_path: str
    target_slot: int | None = None


class ImportWritePayload(BaseModel):
    bundle_path: str
    target_slot: int | None = None
    force: bool = False


@router.post("/bundle/export")
def export_bundle(payload: ExportPayload, install_id: str | None = Query(default=None)) -> dict:
    info = _resolve_install(install_id)
    try:
        out = bundle_mod.export_merc(
            install_root=info.path,
            ui_index=payload.slot,
            out_path=Path(payload.out_path),
            portrait_source_png=Path(payload.portrait_source_png) if payload.portrait_source_png else None,
            extreme_master_png=Path(payload.extreme_master_png) if payload.extreme_master_png else None,
            bigface_source_png=Path(payload.bigface_source_png) if payload.bigface_source_png else None,
            anim_eye_1=Path(payload.anim_eye_1) if payload.anim_eye_1 else None,
            anim_eye_2=Path(payload.anim_eye_2) if payload.anim_eye_2 else None,
            anim_mouth_1=Path(payload.anim_mouth_1) if payload.anim_mouth_1 else None,
            anim_mouth_2=Path(payload.anim_mouth_2) if payload.anim_mouth_2 else None,
            anim_mouth_3=Path(payload.anim_mouth_3) if payload.anim_mouth_3 else None,
            preview_png=Path(payload.preview_png) if payload.preview_png else None,
            author_name=payload.author_name,
            license=payload.license,
            intended_mod=payload.intended_mod,
            notes=payload.notes,
            include_voice=payload.include_voice,
        )
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail={"error": "EXPORT_FAILED", "message": str(e)})
    return {"ok": True, "out_path": str(out)}


@router.post("/bundle/import-preview")
def import_preview(payload: ImportPayload) -> dict:
    """Read the bundle and return its manifest + file list without writing."""
    try:
        contents = bundle_mod.read_wmerc(Path(payload.bundle_path))
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail={"error": "BUNDLE_NOT_FOUND"})
    except Exception as e:
        raise HTTPException(status_code=400, detail={"error": "BUNDLE_INVALID", "message": str(e)})
    return {
        "manifest": contents.manifest.model_dump(mode="json"),
        "files": list(contents.files.keys()),
        "has_portrait": contents.has_portrait_source,
        "has_animation_frames": contents.has_animation_frames,
        "has_voice": contents.has_voice,
    }


@router.post("/bundle/import")
def import_bundle(payload: ImportWritePayload, install_id: str | None = Query(default=None)) -> dict:
    """Deploy a .wmerc bundle into the active install at `target_slot`.

    The write transaction (audit → backup → profile/AIM/gear/EDT → STIs →
    voice) is wrapped in the global write lock so it doesn't race with other
    CRUD operations.
    """
    info = _resolve_install(install_id)
    state = get_state()
    try:
        with cross_process_install_lock(info.id), state.write_lock:
            report = bundle_mod.deploy_import(
                install_root=info.path,
                bundle_path=Path(payload.bundle_path),
                install_id=info.id,
                target_slot=payload.target_slot,
                force=payload.force,
            )
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail={"error": "BUNDLE_NOT_FOUND"})
    except bundle_mod.ImportAuditError as e:
        raise HTTPException(status_code=400, detail={"error": "AUDIT_FAILED", "issues": e.issues})
    except bundle_mod.SlotOccupiedError as e:
        raise HTTPException(
            status_code=409,
            detail={"error": "SLOT_OCCUPIED", "slot": e.slot,
                    "message": "Pass force=true to overwrite"},
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail={"error": "BUNDLE_INVALID", "message": str(e)})
    return {
        "ok": True,
        "report": {
            "target_slot": report.target_slot,
            "files_written": report.files_written,
            "bio_route": report.bio_route,
            "portrait_compiled": report.portrait_compiled,
            "voice_clips_copied": report.voice_clips_copied,
            "aim_bio_id_used": report.aim_bio_id_used,
            # Type=2 expansion-MERC bios route to MERCBIOS.EDT at
            # MercBioID × 1120; the importer rederives the BioID against
            # the target install (never trusts the bundled value — see
            # CLAUDE.md bug-fix #2). Surface it in the response so the
            # frontend can show which record was written.
            "merc_bio_id_used": report.merc_bio_id_used,
            "issues": report.issues,
            "partial_failures": report.partial_failures,
        },
    }
