"""Portrait pipeline routes: detect, preview, animate, compile."""
from __future__ import annotations

import base64
import io
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, File, Form, HTTPException, Query, UploadFile
from PIL import Image

from mercwizard_core import backup
from mercwizard_core.cross_lock import cross_process_install_lock
from mercwizard_core.install_context import make_install_context
from mercwizard_core.portrait.animate_skip import (
    DEFAULT_EYE_BOX,
    DEFAULT_MOUTH_BOX,
    BoundingBox,
)
from mercwizard_core.portrait.compile import compile_and_write_all
from mercwizard_core.portrait.sizes import make_33face, make_65face, make_bigface, make_smallface

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

    # Snapshot INSIDE the cross-process install lock (review fix
    # 2026-05-25). Previously snapshot ran before the lock acquisition,
    # opening a window where another MercWizard process could write the
    # same face STI between our snapshot and our own write — rollback
    # would then restore THEIR pre-mutation bytes and silently lose
    # their edit. Mirrors the facegear.py pattern where snapshot lives
    # under the same lock as the mutation it protects.
    with cross_process_install_lock(info.id), state.write_lock:
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
        except Exception as e:
            # Attempt rollback. compile_and_write_all writes 4 files
            # sequentially; if STI 3 of 4 fails, files 1 and 2 are
            # already on disk in the new (possibly-mismatched) state.
            # restore() puts them back to pre-compile state.
            rollback_ok = True
            rollback_error: Optional[str] = None
            try:
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
        "backup_id": backup_entry.id,
    }
