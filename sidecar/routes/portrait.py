"""Portrait pipeline routes: detect, preview, animate, compile."""
from __future__ import annotations

import base64
import io
import json
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, File, Form, HTTPException, Query, UploadFile
from PIL import Image

from mercwizard_core import audit, backup, rpc_facessmall as SF
from mercwizard_core.body_types import (
    BodyTypeWriteError,
    body_types_for_install,
    validate_body_type_write,
)
from mercwizard_core.cross_lock import cross_process_install_root_lock
from mercwizard_core.install_context import make_install_context
from mercwizard_core.portrait.animate_skip import (
    DEFAULT_EYE_BOX,
    DEFAULT_MOUTH_BOX,
    BoundingBox,
)
from mercwizard_core.portrait.compile import compile_and_write_all
from mercwizard_core.portrait.sizes import make_33face, make_65face, make_bigface, make_smallface
from mercwizard_core.portrait.talkface import build_talkface, write_talkface
from mercwizard_core.inject import edt as edt_mod, profiles_xml, starting_gear
from mercwizard_core.inject._atomic_xml import write_bytes_atomic
from mercwizard_core.models import Gear, Merc
from mercwizard_core.slot_picker import build_slot_picker

from .roster import _resolve_install
from .state import get_state

router = APIRouter()


def _load_upload(file: UploadFile) -> Image.Image:
    data = file.file.read()
    return Image.open(io.BytesIO(data)).convert("RGBA")


def _png_b64(img: Image.Image) -> str:
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode("ascii")


@router.post("/portrait/preview")
def preview(
    image: UploadFile = File(...),
    eye_x: int = Form(DEFAULT_EYE_BOX.x),
    eye_y: int = Form(DEFAULT_EYE_BOX.y),
    mouth_x: int = Form(DEFAULT_MOUTH_BOX.x),
    mouth_y: int = Form(DEFAULT_MOUTH_BOX.y),
) -> dict:
    src = _load_upload(image)
    bigface = make_bigface(src)
    smallface = make_smallface(src)
    face_65 = make_65face(src)
    face_33 = make_33face(src)
    return {
        "bigface": _png_b64(bigface),
        "smallface": _png_b64(smallface),
        "face_65": _png_b64(face_65),
        "face_33": _png_b64(face_33),
    }


@router.post("/portrait/compile")
def compile_portrait(
    image: UploadFile = File(...),
    face_index: int = Form(...),
    eye_x: int = Form(DEFAULT_EYE_BOX.x),
    eye_y: int = Form(DEFAULT_EYE_BOX.y),
    eye_w: int = Form(0),
    eye_h: int = Form(0),
    mouth_x: int = Form(DEFAULT_MOUTH_BOX.x),
    mouth_y: int = Form(DEFAULT_MOUTH_BOX.y),
    mouth_w: int = Form(0),
    mouth_h: int = Form(0),
    skip_animation: bool = Form(True),
    rpc_talkface: bool = Form(False),
    install_id: str | None = Query(default=None),
    # Optional alternate-authoring inputs. When omitted, the legacy single-PNG
    # flow runs unchanged. When supplied, they override the corresponding
    # part of the compile (see compile_and_write_all docstring).
    bigface_image: UploadFile | None = File(default=None),
    anim_eye_1: UploadFile | None = File(default=None),
    anim_eye_2: UploadFile | None = File(default=None),
    anim_eye_3: UploadFile | None = File(default=None),
    anim_eye_4: UploadFile | None = File(default=None),
    anim_mouth_1: UploadFile | None = File(default=None),
    anim_mouth_2: UploadFile | None = File(default=None),
    anim_mouth_3: UploadFile | None = File(default=None),
) -> dict:
    """Compile a single portrait image into the 4 STI files.

    The required `image` upload is the main face source — used for SmallFace,
    65Face, 33Face, and (unless overridden) BigFace.

    Optional uploads let an artist supply richer authoring:

      - `bigface_image`: alternate source for the 106x122 BigFace, when the
        AIM/M.E.R.C. hero portrait wants different framing than the tight
        48x43 face. Omit to crop BigFace from the main image like the rest.

      - `anim_eye_1..4`, `anim_mouth_1..3`: per-slot animation sub-frame
        sources. Each may be exactly 17x6 (eye) / 14x6 (mouth) for verbatim
        use, OR larger (auto-cropped at the merc's eye_x/y or mouth_x/y).
        Auto-pad if fewer are supplied — e.g. one anim_eye_1 puts the same
        crop in all 4 eye slots, matching skip-mode behavior; three anim_eye
        uploads fill slots 1/2/4 with slot 3 duplicating slot 1 per the
        engine's hardware convention.

    When any anim_eye_* OR anim_mouth_* upload is present, the explicit
    path runs; `skip_animation` controls only the unauthored region.
    """
    info = _resolve_install(install_id)
    state = get_state()

    png_bytes = image.file.read()
    eye_box = BoundingBox(x=eye_x, y=eye_y, w=eye_w, h=eye_h)
    mouth_box = BoundingBox(x=mouth_x, y=mouth_y, w=mouth_w, h=mouth_h)

    bigface_bytes = bigface_image.file.read() if bigface_image is not None else None

    def _collect(uploads: list[UploadFile | None]) -> list[bytes] | None:
        # Slot-positional: gaps in the middle (anim_eye_2 supplied but not _1)
        # collapse to a contiguous list. Callers usually start from slot 1.
        raw = [u.file.read() for u in uploads if u is not None]
        return raw if raw else None

    eye_uploads = _collect([anim_eye_1, anim_eye_2, anim_eye_3, anim_eye_4])
    mouth_uploads = _collect([anim_mouth_1, anim_mouth_2, anim_mouth_3])

    # Snapshot every face STI that compile_and_write_all might touch
    # BEFORE we enter the write phase. compile writes 4 files (SmallFace,
    # 65Face, 33Face, BigFace) under faces/ subdirs; if any one fails
    # partway, the user is left with an inconsistent set (e.g. a new
    # BigFace + stale SmallFace). The snapshot covers all four so a
    # rollback restores the in-game-consistent state.
    ctx = make_install_context(info.path)
    files_to_snapshot = [
        ctx.face_sti_path(face_index, size=s)
        for s in ("smallface", "face_65", "face_33", "bigface")
    ]
    if rpc_talkface:
        files_to_snapshot.append(ctx.rpc_talkface_path(face_index, for_write=True))
    # Snapshot INSIDE the cross-process install lock (a review fix).
    # Previously snapshot ran before the lock acquisition,
    # opening a window where another MercWizard process could write the
    # same face STI between our snapshot and our own write — rollback
    # would then restore THEIR pre-mutation bytes and silently lose
    # their edit. Mirrors the facegear.py pattern where snapshot lives
    # under the same lock as the mutation it protects.
    with cross_process_install_root_lock(info.path), state.write_lock:
        # Existence belongs to the same critical section as snapshot + write.
        # Otherwise a second process can create a face after this request's
        # check but before lock acquisition, and rollback will misclassify the
        # other process's face as ours and schedule it for deletion.
        existed_before = {p for p in files_to_snapshot if p.exists()}
        backup_entry = backup.snapshot(
            install_root=info.path,
            install_id=info.id,
            files_to_back_up=files_to_snapshot,
            reason=f"portrait_compile_face_{face_index}",
        )
        try:
            written = compile_and_write_all(
                install_root=info.path,
                face_index=face_index,
                source_png_bytes=png_bytes,
                skip_animation=skip_animation,
                eye_box=eye_box,
                mouth_box=mouth_box,
                bigface_source_png=bigface_bytes,
                explicit_eye_pngs=eye_uploads,
                explicit_mouth_pngs=mouth_uploads,
            )
            talk_build = None
            if rpc_talkface:
                talk_build = build_talkface(
                    png_bytes,
                    explicit_eye_pngs=eye_uploads,
                    explicit_mouth_pngs=mouth_uploads,
                    small_eye_box=eye_box,
                    small_mouth_box=mouth_box,
                )
                talk_path = ctx.rpc_talkface_path(face_index, for_write=True)
                write_talkface(talk_path, talk_build)
                written.append(str(talk_path))
        except Exception as e:
            # Attempt rollback. compile_and_write_all writes 4 files
            # sequentially; if STI 3 of 4 fails, files 1 and 2 are
            # already on disk in the new (possibly-mismatched) state.
            # restore() puts them back to pre-compile state.
            rollback_ok = True
            rollback_error: Optional[str] = None
            try:
                created = [p for p in files_to_snapshot if p.exists() and p not in existed_before]
                if created:
                    backup.record_files_created(backup_entry.id, info.id, created)
                backup.restore(
                    backup_id=backup_entry.id,
                    install_id=info.id,
                    install_root=info.path,
                )
            except Exception as restore_err:
                rollback_ok = False
                rollback_error = f"{type(restore_err).__name__}: {restore_err}"
            detail = {
                "error": "PORTRAIT_COMPILE_FAILED" if rollback_ok
                         else "PORTRAIT_COMPILE_FAILED_ROLLBACK_FAILED",
                "message": f"{type(e).__name__}: {e}",
                "face_index": face_index,
                "backup_id": backup_entry.id,
                "rollback_ok": rollback_ok,
            }
            if rollback_error:
                detail["rollback_error"] = rollback_error
            raise HTTPException(status_code=500, detail=detail) from e

    # The roster portrait sheet caches faces keyed on MercProfiles.xml's
    # mtime, which a portrait-only recompile does NOT bump. Drop the
    # cached sheet (memory + disk) for this install so the roster shows
    # the new face immediately instead of after the next profile write.
    from .roster import invalidate_portrait_sheet_cache
    invalidate_portrait_sheet_cache(info.id)

    return {
        "ok": True,
        "face_index": face_index,
        "files_written": written,
        "frame_count": 8,  # SmallFace always carries 8 frames (1 base + 7 anim)
        "explicit_animation": eye_uploads is not None or mouth_uploads is not None,
        "bigface_override": bigface_bytes is not None,
        "talkface": ({
            "written": True,
            "eyes_x": talk_build.eyes_xy[0],
            "eyes_y": talk_build.eyes_xy[1],
            "mouth_x": talk_build.mouth_xy[0],
            "mouth_y": talk_build.mouth_xy[1],
            "animated_eyes": talk_build.animated_eyes,
            "animated_mouth": talk_build.animated_mouth,
        } if talk_build is not None else None),
        "backup_id": backup_entry.id,
    }


@router.post("/portrait/rpc-save")
def save_rpc_portrait(
    image: UploadFile = File(...),
    operation: str = Form(...),
    merc_json: str = Form(...),
    gear_json: str | None = Form(default=None),
    eye_x: int = Form(DEFAULT_EYE_BOX.x),
    eye_y: int = Form(DEFAULT_EYE_BOX.y),
    eye_w: int = Form(0),
    eye_h: int = Form(0),
    mouth_x: int = Form(DEFAULT_MOUTH_BOX.x),
    mouth_y: int = Form(DEFAULT_MOUTH_BOX.y),
    mouth_w: int = Form(0),
    mouth_h: int = Form(0),
    install_id: str | None = Query(default=None),
    bigface_image: UploadFile | None = File(default=None),
    anim_eye_1: UploadFile | None = File(default=None),
    anim_eye_2: UploadFile | None = File(default=None),
    anim_eye_3: UploadFile | None = File(default=None),
    anim_eye_4: UploadFile | None = File(default=None),
    anim_mouth_1: UploadFile | None = File(default=None),
    anim_mouth_2: UploadFile | None = File(default=None),
    anim_mouth_3: UploadFile | None = File(default=None),
) -> dict:
    """Atomically compile both RPC face systems and persist their coordinates.

    Create also writes the Type-3 profile, starting gear, and biography. Edit
    updates the profile and biography. One snapshot and one install lock cover
    the entire operation, so a late failure cannot strand new faces beside an
    old profile (or vice versa).
    """
    if operation not in {"create", "edit"}:
        raise HTTPException(status_code=422, detail="operation must be 'create' or 'edit'")
    try:
        merc = Merc.model_validate(json.loads(merc_json))
        gear = Gear.model_validate(json.loads(gear_json)) if gear_json else None
    except Exception as exc:
        raise HTTPException(status_code=422, detail=f"Invalid RPC save payload: {exc}") from exc
    if merc.Type != 3:
        raise HTTPException(status_code=422, detail="Atomic RPC portrait save requires profile Type 3.")
    if gear is not None and gear.mIndex != merc.uiIndex:
        raise HTTPException(status_code=422, detail="Gear mIndex must match the RPC profile slot.")

    info = _resolve_install(install_id)
    state = get_state()
    ctx = make_install_context(info.path)
    picker = build_slot_picker(info.path, vfs_config_path=info.vfs_config_path, ctx=ctx)
    slot_info = picker.slots[merc.uiIndex] if 0 <= merc.uiIndex < len(picker.slots) else None
    issues = audit.audit_full(
        merc,
        gear=gear,
        slot_info=slot_info,
        body_types=body_types_for_install(info.path).options,
    )
    if audit.has_errors(issues):
        raise HTTPException(status_code=400, detail={
            "error": "AUDIT_FAILED",
            "issues": [issue.model_dump() for issue in issues],
        })

    png_bytes = image.file.read()
    bigface_bytes = bigface_image.file.read() if bigface_image is not None else None
    eye_box = BoundingBox(eye_x, eye_y, eye_w, eye_h)
    mouth_box = BoundingBox(mouth_x, mouth_y, mouth_w, mouth_h)

    def _collect(uploads: list[UploadFile | None]) -> list[bytes] | None:
        raw = [upload.file.read() for upload in uploads if upload is not None]
        return raw if raw else None

    eye_uploads = _collect([anim_eye_1, anim_eye_2, anim_eye_3, anim_eye_4])
    mouth_uploads = _collect([anim_mouth_1, anim_mouth_2, anim_mouth_3])
    face_index = merc.ubFaceIndex
    talk_path = ctx.rpc_talkface_path(face_index, for_write=True)
    small_coords_path = ctx.rpc_faces_small_path(for_write=True)
    profiles_path = ctx.profiles_xml_path(for_write=True)
    gear_path = ctx.gear_xml_path(for_write=True)

    targets = backup.files_for_merc(info.path, merc.uiIndex, face_index)
    targets.extend([
        ctx.face_sti_path(face_index, size=size, for_write=True)
        for size in ("smallface", "face_65", "face_33", "bigface")
    ])
    targets.extend([talk_path, small_coords_path, profiles_path])
    if gear is not None:
        targets.append(gear_path)
    # Stable de-duplication keeps the backup manifest compact while retaining
    # the first VFS-resolved path for each target.
    targets = list(dict.fromkeys(Path(path) for path in targets))

    written: list[str] = []
    backup_entry = None
    talk_build = None
    with cross_process_install_root_lock(info.path), state.write_lock:
        occupied = profiles_xml.is_slot_occupied(profiles_path, merc.uiIndex)
        if operation == "create" and occupied:
            raise HTTPException(status_code=409, detail={
                "error": "SLOT_OCCUPIED", "slot": merc.uiIndex,
                "message": "Choose an empty slot before creating the RPC.",
            })
        if operation == "edit" and not occupied:
            raise HTTPException(status_code=404, detail=f"RPC profile {merc.uiIndex} does not exist.")
        existing_fields = profiles_xml.read_slot(profiles_path, merc.uiIndex)
        try:
            existing_body_type = int((existing_fields or {}).get("ubBodyType", "").strip())
        except ValueError:
            existing_body_type = None
        try:
            validate_body_type_write(
                info.path,
                merc.ubBodyType,
                existing_body_type=existing_body_type,
            )
        except BodyTypeWriteError as error:
            raise HTTPException(status_code=400, detail={
                "error": error.code,
                "body_type": error.body_type,
                "message": str(error),
            }) from error

        existed_before = {path for path in targets if path.exists()}
        backup_entry = backup.snapshot(
            install_root=info.path,
            install_id=info.id,
            files_to_back_up=targets,
            reason=f"rpc_{operation}_portrait_{merc.uiIndex}",
        )
        try:
            written.extend(compile_and_write_all(
                install_root=info.path,
                face_index=face_index,
                source_png_bytes=png_bytes,
                skip_animation=True,
                eye_box=eye_box,
                mouth_box=mouth_box,
                bigface_source_png=bigface_bytes,
                explicit_eye_pngs=eye_uploads,
                explicit_mouth_pngs=mouth_uploads,
            ))
            talk_build = build_talkface(
                png_bytes,
                explicit_eye_pngs=eye_uploads,
                explicit_mouth_pngs=mouth_uploads,
                small_eye_box=eye_box,
                small_mouth_box=mouth_box,
            )
            write_talkface(talk_path, talk_build)
            written.append(str(talk_path))

            updated_merc = merc.model_copy(update={
                "usEyesX": talk_build.eyes_xy[0],
                "usEyesY": talk_build.eyes_xy[1],
                "usMouthX": talk_build.mouth_xy[0],
                "usMouthY": talk_build.mouth_xy[1],
            })
            profiles_xml.upsert(profiles_path, updated_merc)
            written.append(str(profiles_path))
            if gear is not None:
                starting_gear.upsert(gear_path, gear)
                written.append(str(gear_path))
            bio_route = edt_mod.write_bio(
                info.path,
                ui_index=updated_merc.uiIndex,
                biography=updated_merc.biographyText,
                additional=updated_merc.additionalInfoText,
                ctx=ctx,
            )
            written.append(str(bio_route.path))

            existing_small = ""
            read_small = ctx.rpc_faces_small_path()
            source_small = small_coords_path if small_coords_path.exists() else read_small
            if source_small.exists():
                existing_small = source_small.read_text(encoding="utf-8-sig")
            small_text = SF.upsert(
                existing_small,
                updated_merc.uiIndex,
                updated_merc.zNickname or updated_merc.zName or f"RPC{updated_merc.uiIndex}",
                eye_box.x,
                eye_box.y,
                mouth_box.x,
                mouth_box.y,
            )
            write_bytes_atomic(small_coords_path, small_text.encode("utf-8"))
            written.append(str(small_coords_path))
            merc = updated_merc
        except Exception as exc:
            rollback_ok = False
            rollback_error: str | None = None
            try:
                created = [path for path in targets if path.exists() and path not in existed_before]
                if created:
                    backup.record_files_created(backup_entry.id, info.id, created)
                backup.restore(backup_entry.id, info.id, info.path)
                rollback_ok = True
            except Exception as restore_exc:
                rollback_error = f"{type(restore_exc).__name__}: {restore_exc}"
            detail = {
                "error": "RPC_SAVE_FAILED" if rollback_ok else "RPC_SAVE_FAILED_ROLLBACK_FAILED",
                "message": f"{type(exc).__name__}: {exc}",
                "backup_id": backup_entry.id,
                "rollback_ok": rollback_ok,
            }
            if rollback_error:
                detail["rollback_error"] = rollback_error
            raise HTTPException(status_code=500, detail=detail) from exc

    from .roster import invalidate_portrait_sheet_cache
    invalidate_portrait_sheet_cache(info.id)
    return {
        "ok": True,
        "operation": operation,
        "slot": merc.uiIndex,
        "face_index": face_index,
        "files_written": written,
        "backup_id": backup_entry.id,
        "issues": [issue.model_dump() for issue in issues],
        "talkface": {
            "written": True,
            "eyes_x": talk_build.eyes_xy[0],
            "eyes_y": talk_build.eyes_xy[1],
            "mouth_x": talk_build.mouth_xy[0],
            "mouth_y": talk_build.mouth_xy[1],
            "animated_eyes": talk_build.animated_eyes,
            "animated_mouth": talk_build.animated_mouth,
        },
    }
