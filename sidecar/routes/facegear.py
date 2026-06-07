"""FaceGear STI capacity routes.

Read: detect Face_*.sti frame counts in the active install.
Write: extend Face_*.sti with transparent frames so a high-face-index merc
       can equip the gear without crashing the engine at vobject.cpp:958.
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from mercwizard_core.backup import snapshot
from mercwizard_core.cross_lock import cross_process_install_lock
from mercwizard_core.facegear import (
    auto_position_overlay,
    crash_risk,
    detect_facegear_capacities,
    extend_facegear_sti,
    extract_overlay,
    find_orphan_variants,
    inject_overlay,
    nudge_overlay_offset,
    set_overlay_offset,
    read_frame_offset,
    read_registered_facegear_stis,
    repair_orphan_pair,
    resolve_orphan_repair_paths,
    _find_source_frame,
)
from mercwizard_core.install_context import make_install_context

from .roster import _resolve_install
from .state import get_state

router = APIRouter()


class FaceGearItem(BaseModel):
    name: str
    relative_path: str
    frame_count: int
    canvas_width: int
    canvas_height: int
    is_imp_variant: bool


class FaceGearOrphan(BaseModel):
    stem: str
    missing: str           # "base" or "imp"
    present_path: str


class FaceGearLoadError(BaseModel):
    """An STI that failed to load during capacity detection.

    Phase 2.4 surface: pre-fix these were silently dropped by a bare
    `except Exception: continue` in `detect_facegear_capacities`. Now the
    UI can show "1 STI failed to load: Face_X.sti — corrupt ETRLE strip"
    instead of an empty list.
    """
    name: str
    relative_path: str
    error: str
    message: str


class FaceGearCapacityResponse(BaseModel):
    items: list[FaceGearItem]
    lowest_frame_count: Optional[int] = None
    orphans: list[FaceGearOrphan] = []
    load_errors: list[FaceGearLoadError] = []
    install_id: str


class FaceGearExtendBody(BaseModel):
    face_index: int = Field(..., ge=0, le=255)
    only_crash_risk: bool = True


class FaceGearExtendResult(BaseModel):
    name: str
    relative_path: str
    previous_frame_count: int
    new_frame_count: int
    frames_appended: int
    noop: bool


class FaceGearExtendResponse(BaseModel):
    extended: list[FaceGearExtendResult]
    backup_id: Optional[str] = None
    install_id: str


@router.get("/facegear/capacity")
def facegear_capacity(install_id: Optional[str] = Query(default=None)) -> FaceGearCapacityResponse:
    """List every Face_*.sti in the install along with its frame count.

    The lowest frame count across all FaceGear STIs is the install's
    effective ubFaceIndex ceiling — any merc above that risks crashing
    on at least one piece of gear.
    """
    info = _resolve_install(install_id)
    ctx = make_install_context(info.path)
    load_errors: list[dict] = []
    infos = detect_facegear_capacities(ctx, load_errors=load_errors)
    items = [
        FaceGearItem(
            name=i.name,
            relative_path=i.relative_path,
            frame_count=i.frame_count,
            canvas_width=i.canvas_size[0],
            canvas_height=i.canvas_size[1],
            is_imp_variant=i.is_imp_variant,
        )
        for i in infos
    ]
    # Cross-reference filesystem orphans against FaceGear.xml so we
    # only flag items the engine actually loads. Bug #79: without
    # this filter, modder leftovers like Face_KGoggles got reported
    # as boot-CTD risks even though no FaceGear.xml row referenced
    # them — false alarm. When FaceGear.xml isn't readable
    # (read_registered_facegear_stis returns an empty set), fall back
    # to the old filesystem-only behavior by passing None.
    registered = read_registered_facegear_stis(ctx)
    orphans = [
        FaceGearOrphan(**o)
        for o in find_orphan_variants(
            infos,
            registered_stems=registered if registered else None,
        )
    ]
    return FaceGearCapacityResponse(
        items=items,
        lowest_frame_count=(min(i.frame_count for i in infos) if infos else None),
        orphans=orphans,
        load_errors=[FaceGearLoadError(**e) for e in load_errors],
        install_id=info.id,
    )


@router.post("/facegear/extend")
def facegear_extend(
    body: FaceGearExtendBody,
    install_id: Optional[str] = Query(default=None),
) -> FaceGearExtendResponse:
    """Extend every crash-risk FaceGear STI to cover `face_index`.

    Default `only_crash_risk=True` skips STIs that already have enough
    frames. The backup snapshot covers every file that will be modified.
    """
    info = _resolve_install(install_id)
    state = get_state()
    ctx = make_install_context(info.path)
    target_count = body.face_index + 1
    infos = detect_facegear_capacities(ctx)
    candidates = crash_risk(infos, body.face_index) if body.only_crash_risk else infos
    if not candidates:
        return FaceGearExtendResponse(extended=[], backup_id=None, install_id=info.id)

    results: list[FaceGearExtendResult] = []
    with cross_process_install_lock(info.id), state.write_lock:
        # Snapshot INSIDE the lock so a concurrent route can't mutate
        # the file between snapshot and our write — without the lock,
        # backup captures stale bytes and any rollback would restore
        # those instead of the actual pre-extend state. The lock is
        # already held for the whole extend loop so the snapshot adds
        # no contention.
        backup_entry = snapshot(
            install_root=info.path,
            install_id=info.id,
            files_to_back_up=[c.path for c in candidates],
            reason=f"facegear_extend_to_{target_count}",
        )
        for c in candidates:
            r = extend_facegear_sti(c.path, target_count)
            results.append(
                FaceGearExtendResult(
                    name=c.name,
                    relative_path=c.relative_path,
                    previous_frame_count=r["previous_frame_count"],
                    new_frame_count=r["new_frame_count"],
                    frames_appended=r["frames_appended"],
                    noop=r["noop"],
                )
            )

    return FaceGearExtendResponse(
        extended=results,
        backup_id=backup_entry.id,
        install_id=info.id,
    )


class FaceGearOverlayBody(BaseModel):
    """Upload a custom overlay for one merc into one FaceGear STI.

    `sti_name` is the filename (e.g. `Face_SunGoggles.sti`) — the route resolves
    the actual path by matching against the install's detected FaceGear inventory.
    `png_b64` is base64-encoded PNG bytes (any size >= 48×43; auto-cropped).
    `apply_to_imp` mirrors the overlay into the `_IMP.sti` partner so IMP-Type
    mercs render the same hat.
    """
    sti_name: str
    face_index: int = Field(..., ge=0, le=255)
    png_b64: str
    apply_to_imp: bool = True


class FaceGearOverlayResult(BaseModel):
    name: str
    relative_path: str
    previous_frame_count: int
    new_frame_count: int
    extended: bool


class FaceGearOverlayResponse(BaseModel):
    written: list[FaceGearOverlayResult]
    backup_id: Optional[str] = None
    install_id: str


@router.post("/facegear/overlay")
def facegear_overlay(
    body: FaceGearOverlayBody,
    install_id: Optional[str] = Query(default=None),
) -> FaceGearOverlayResponse:
    """Inject a custom overlay into one FaceGear STI at the merc's face index.

    Writes to `<install>/Data*/faces/FACESGEAR/<sti_name>` and, if
    `apply_to_imp` is set, also to the matching `_IMP.sti` partner.

    Both files get backed up before write; restore is one click in the
    Backups page if the overlay looks wrong in-game.
    """
    import base64

    info = _resolve_install(install_id)
    state = get_state()
    ctx = make_install_context(info.path)
    infos = detect_facegear_capacities(ctx)
    target = next((i for i in infos if i.name.lower() == body.sti_name.lower()), None)
    if target is None:
        raise HTTPException(
            status_code=404,
            detail={
                "error": "FACEGEAR_STI_NOT_FOUND",
                "message": f"No '{body.sti_name}' under faces/FACESGEAR/ in this install",
            },
        )

    paths_to_write = [target.path]
    if body.apply_to_imp and not target.is_imp_variant:
        # Look for the matching _IMP.sti partner in the same dir
        stem_no_ext = target.path.stem
        imp_candidate = target.path.with_name(f"{stem_no_ext}_IMP{target.path.suffix}")
        if imp_candidate.exists():
            paths_to_write.append(imp_candidate)

    try:
        png_bytes = base64.b64decode(body.png_b64)
    except Exception as e:
        raise HTTPException(
            status_code=400,
            detail={"error": "OVERLAY_DECODE_FAILED", "message": str(e)},
        )

    results: list[FaceGearOverlayResult] = []
    install_root_resolved = info.path.resolve()
    with cross_process_install_lock(info.id), state.write_lock:
        # Snapshot INSIDE the lock to mirror /facegear/extend — a
        # parallel /facegear/{nudge,set-offset,auto-position} hitting
        # the same STI could otherwise land its write between our
        # snapshot and our lock acquisition. Backup would then capture
        # the concurrent writer's bytes, and a rollback would restore
        # the wrong "pre-write" state. Bug-review finding A8/E2.
        backup_entry = snapshot(
            install_root=info.path,
            install_id=info.id,
            files_to_back_up=paths_to_write,
            reason=f"facegear_overlay_{target.path.stem}_face{body.face_index}",
        )
        for p in paths_to_write:
            try:
                r = inject_overlay(p, body.face_index, png_bytes)
            except ValueError as e:
                raise HTTPException(
                    status_code=400,
                    detail={"error": "OVERLAY_INVALID", "message": str(e)},
                )
            try:
                rel = p.resolve().relative_to(install_root_resolved)
                rel_str = str(rel).replace("\\", "/")
            except ValueError:
                rel_str = str(p)
            results.append(
                FaceGearOverlayResult(
                    name=p.name,
                    relative_path=rel_str,
                    previous_frame_count=r["previous_frame_count"],
                    new_frame_count=r["new_frame_count"],
                    extended=r["extended"],
                )
            )

    return FaceGearOverlayResponse(
        written=results,
        backup_id=backup_entry.id,
        install_id=info.id,
    )


class FaceGearAutoPositionBody(BaseModel):
    """Apply a stock FaceGear graphic to one merc with the offset computed
    from the merc's eye coordinates.

    The wizard auto-picks the first non-empty frame from the STI as the
    source pixels, looks up that source merc's eye coords from
    MercProfiles.xml, then writes the source frame's pixels at
    `target_face_index` with `sOffsetX/sOffsetY` shifted by the eye-coord
    delta. The engine renders the same gear graphic positioned per the
    target merc's eye row.

    Previously this body also accepted `source_face_index`, `source_eye_x`,
    `source_eye_y` to override the source. Those were removed in favor of
    direct sOffsetX/sOffsetY editing via `POST /facegear/set-offset` —
    users now fine-tune position with absolute coords rather than picking
    a different source merc to inherit a different painted baseline.
    `target_eye_x/y` continue to come from the wizard's merc state.
    """
    sti_name: str
    target_face_index: int = Field(..., ge=0, le=255)
    target_eye_x: int
    target_eye_y: int
    apply_to_imp: bool = True


class FaceGearAutoPositionResult(BaseModel):
    name: str
    relative_path: str
    source_face_index: int
    source_offset_xy: tuple[int, int]
    applied_offset_xy: tuple[int, int]
    delta_xy: tuple[int, int]
    source_eye_xy: tuple[int, int]
    target_eye_xy: tuple[int, int]
    extended: bool


class FaceGearAutoPositionResponse(BaseModel):
    written: list[FaceGearAutoPositionResult]
    backup_id: Optional[str] = None
    install_id: str


def _lookup_eye_coords_for_face_index(ctx, face_index: int) -> Optional[tuple[int, int]]:
    """Find the merc whose ubFaceIndex == face_index and return their usEyesX/Y."""
    from mercwizard_core.inject import profiles_xml as _px

    profiles_path = ctx.profiles_xml_path()
    all_profiles = _px.read_all_slots(profiles_path)
    for _slot, fields in all_profiles.items():
        try:
            face_idx_str = fields.get("ubFaceIndex", "").strip()
            if not face_idx_str or int(face_idx_str) != face_index:
                continue
            eye_x = int(fields.get("usEyesX", "10").strip())
            eye_y = int(fields.get("usEyesY", "10").strip())
            return (eye_x, eye_y)
        except (ValueError, AttributeError):
            continue
    return None


@router.post("/facegear/auto-position")
def facegear_auto_position(
    body: FaceGearAutoPositionBody,
    install_id: Optional[str] = Query(default=None),
) -> FaceGearAutoPositionResponse:
    """Apply a stock FaceGear graphic to the merc with engine-computed offset.

    Auto-picks the first non-empty frame as the source pixels, looks up
    that source merc's eye coords from MercProfiles.xml, then writes the
    source frame's pixels to `target_face_index` with sOffsetX/sOffsetY
    shifted by the eye-coord delta. Mirrors to the `_IMP.sti` partner when
    `apply_to_imp=True`. Both files are backed up first.

    Fine-tuning the position after auto-position is done via
    `POST /facegear/nudge` (±delta) or `POST /facegear/set-offset`
    (absolute), not by re-running this route with different source params.
    """
    info = _resolve_install(install_id)
    state = get_state()
    ctx = make_install_context(info.path)
    infos = detect_facegear_capacities(ctx)
    target = next((i for i in infos if i.name.lower() == body.sti_name.lower()), None)
    if target is None:
        raise HTTPException(
            status_code=404,
            detail={
                "error": "FACEGEAR_STI_NOT_FOUND",
                "message": f"No '{body.sti_name}' under faces/FACESGEAR/ in this install",
            },
        )

    # Auto-detect source: first non-empty frame in the STI.
    from ja2py.fileformats.Sti import load_8bit_sti
    with open(target.path, "rb") as f:
        _images = load_8bit_sti(f)
    source_idx = _find_source_frame(_images)
    if source_idx is None:
        raise HTTPException(
            status_code=400,
            detail={
                "error": "NO_STOCK_FRAME",
                "message": f"{body.sti_name} has no non-empty frames to source from",
            },
        )

    # Look up the source merc's eye coords from MercProfiles.xml; default
    # to vanilla (10, 10) when no merc owns that face index in this install.
    looked_up = _lookup_eye_coords_for_face_index(ctx, source_idx)
    source_eye = looked_up if looked_up is not None else (10, 10)

    paths_to_write = [target.path]
    if body.apply_to_imp and not target.is_imp_variant:
        stem_no_ext = target.path.stem
        imp_candidate = target.path.with_name(f"{stem_no_ext}_IMP{target.path.suffix}")
        if imp_candidate.exists():
            paths_to_write.append(imp_candidate)

    results: list[FaceGearAutoPositionResult] = []
    install_root_resolved = info.path.resolve()
    with cross_process_install_lock(info.id), state.write_lock:
        # Snapshot inside the lock — see /facegear/overlay for the
        # rationale. Bug-review finding A8/E2.
        backup_entry = snapshot(
            install_root=info.path,
            install_id=info.id,
            files_to_back_up=paths_to_write,
            reason=f"facegear_autopos_{target.path.stem}_face{body.target_face_index}",
        )
        for p in paths_to_write:
            try:
                r = auto_position_overlay(
                    p,
                    target_face_index=body.target_face_index,
                    target_eye_xy=(body.target_eye_x, body.target_eye_y),
                    source_eye_xy=source_eye,
                    source_face_index=source_idx,
                )
            except ValueError as e:
                raise HTTPException(
                    status_code=400,
                    detail={"error": "AUTOPOS_INVALID", "message": str(e)},
                )
            try:
                rel = p.resolve().relative_to(install_root_resolved)
                rel_str = str(rel).replace("\\", "/")
            except ValueError:
                rel_str = str(p)
            results.append(
                FaceGearAutoPositionResult(
                    name=p.name,
                    relative_path=rel_str,
                    source_face_index=r["source_face_index"],
                    source_offset_xy=r["source_offset_xy"],
                    applied_offset_xy=r["applied_offset_xy"],
                    delta_xy=r["delta_xy"],
                    source_eye_xy=r["source_eye_xy"],
                    target_eye_xy=r["target_eye_xy"],
                    extended=r["extended"],
                )
            )

    return FaceGearAutoPositionResponse(
        written=results,
        backup_id=backup_entry.id,
        install_id=info.id,
    )


class FaceGearOrphanRepairBody(BaseModel):
    """Optional list of orphan stems to repair. None / empty = repair all."""
    stems: Optional[list[str]] = None


class FaceGearOrphanRepairResult(BaseModel):
    stem: str
    source: str  # relative path
    target: str  # relative path
    bytes_written: int


class FaceGearOrphanRepairResponse(BaseModel):
    repaired: list[FaceGearOrphanRepairResult]
    skipped: list[dict]  # {stem, reason} for orphans we couldn't repair
    backup_id: Optional[str] = None
    install_id: str


@router.post("/facegear/orphans/repair")
def facegear_orphans_repair(
    body: FaceGearOrphanRepairBody = FaceGearOrphanRepairBody(),
    install_id: Optional[str] = Query(default=None),
) -> FaceGearOrphanRepairResponse:
    """Mirror each requested orphan's present STI to its missing partner.

    The fix the banner's own copy recommends: copy `Face_X.sti` to
    `Face_X_IMP.sti` (or vice-versa) so `InitializeFaceGearGraphics()`
    finds both at boot. STIs are universal 256-frame containers — copying
    is enough; the engine doesn't care that the IMP partner is byte-
    identical (several vanilla pairs ship that way).

    Re-scans the install before acting so the orphan list reflects
    ground truth. Targets that appeared between scan and write are
    surfaced as `skipped` with reason `target_exists` rather than
    silently overwritten.
    """
    info = _resolve_install(install_id)
    state = get_state()
    ctx = make_install_context(info.path)

    # Fresh scan — never trust client-supplied data for what's actually
    # on disk. Filter to registered orphans only (matches what the
    # banner shows). Repairing unregistered orphans wouldn't hurt but
    # they're not the engine-CTD risk; leave them alone.
    infos = detect_facegear_capacities(ctx)
    registered = read_registered_facegear_stis(ctx)
    current_orphans = find_orphan_variants(
        infos,
        registered_stems=registered if registered else None,
    )

    requested = set(s.strip() for s in (body.stems or []) if s.strip())
    targets = current_orphans if not requested else [
        o for o in current_orphans if o["stem"] in requested
    ]

    if not targets:
        return FaceGearOrphanRepairResponse(
            repaired=[], skipped=[], backup_id=None, install_id=info.id,
        )

    # Resolve each target's (source, target) pair up-front so we can
    # snapshot a single backup before doing any writes.
    plan: list[tuple[dict, "Path", "Path"]] = []
    skipped: list[dict] = []
    from pathlib import Path
    for orphan in targets:
        resolved = resolve_orphan_repair_paths(infos, orphan)
        if resolved is None:
            skipped.append({"stem": orphan["stem"], "reason": "source_missing_after_scan"})
            continue
        src, dst = resolved
        if dst.exists():
            skipped.append({"stem": orphan["stem"], "reason": "target_exists"})
            continue
        plan.append((orphan, src, dst))

    if not plan:
        return FaceGearOrphanRepairResponse(
            repaired=[], skipped=skipped, backup_id=None, install_id=info.id,
        )

    install_root_resolved = info.path.resolve()
    repaired: list[FaceGearOrphanRepairResult] = []
    with cross_process_install_lock(info.id), state.write_lock:
        # Snapshot the targets INSIDE the lock. Source paths must not
        # change between snapshot and copy — a concurrent
        # /facegear/overlay touching `Face_X.sti` mid-repair would let
        # repair_orphan_pair copy partially-written bytes while the
        # backup recorded "Face_X_IMP doesn't exist." A rollback then
        # deletes the half-corrupt IMP, leaving the user at a one-sided
        # orphan with the overlay applied to the base only — the exact
        # state they were trying to repair. Targets don't exist yet
        # (orphan = missing partner), so snapshot captures "doesn't
        # exist" → restore deletes the copies we're about to make.
        # Bug-review finding A8/E2.
        backup_entry = snapshot(
            install_root=info.path,
            install_id=info.id,
            files_to_back_up=[dst for (_, _, dst) in plan],
            reason=f"facegear_orphan_repair_{len(plan)}_pairs",
        )
        for (orphan, src, dst) in plan:
            try:
                n_bytes = repair_orphan_pair(src, dst)
            except FileExistsError:
                skipped.append({"stem": orphan["stem"], "reason": "target_exists"})
                continue
            except OSError as e:
                skipped.append({
                    "stem": orphan["stem"],
                    "reason": f"{type(e).__name__}: {e}",
                })
                continue

            def _rel(p: Path) -> str:
                try:
                    return str(p.resolve().relative_to(install_root_resolved)).replace("\\", "/")
                except ValueError:
                    return str(p)

            repaired.append(FaceGearOrphanRepairResult(
                stem=orphan["stem"],
                source=_rel(src),
                target=_rel(dst),
                bytes_written=n_bytes,
            ))

    return FaceGearOrphanRepairResponse(
        repaired=repaired,
        skipped=skipped,
        backup_id=backup_entry.id if repaired else None,
        install_id=info.id,
    )


class FaceGearNudgeBody(BaseModel):
    """Shift the existing offset on one FaceGear frame by (dx, dy) pixels.

    Used by the post-auto-position UI to fine-tune positioning without
    re-quantizing the image — pure header edit. Both `dx` and `dy` are
    signed; positive = right/down. Typical range ±1..±5; the engine
    reads INT16 so larger nudges are technically valid but anything past
    ±10 usually means the auto-position math went wrong, not that the
    user wants a real shift.
    """
    sti_name: str
    face_index: int = Field(..., ge=0, le=255)
    dx: int
    dy: int
    apply_to_imp: bool = True


class FaceGearNudgeResult(BaseModel):
    name: str
    relative_path: str
    previous_offset_xy: tuple[int, int]
    new_offset_xy: tuple[int, int]


class FaceGearNudgeResponse(BaseModel):
    nudged: list[FaceGearNudgeResult]
    backup_id: Optional[str] = None
    install_id: str


@router.post("/facegear/nudge")
def facegear_nudge(
    body: FaceGearNudgeBody,
    install_id: Optional[str] = Query(default=None),
) -> FaceGearNudgeResponse:
    """Shift frame[face_index]'s sOffsetX/sOffsetY by (dx, dy) on the
    named FaceGear STI (and its _IMP partner if `apply_to_imp`).

    Pure header edit — pixels untouched. Lets the user fine-tune
    auto-positioning when the eye-coord-delta math lands close but not
    perfect. A backup snapshot is taken first; restore via Backups page.
    """
    info = _resolve_install(install_id)
    state = get_state()
    ctx = make_install_context(info.path)
    infos = detect_facegear_capacities(ctx)
    target = next((i for i in infos if i.name.lower() == body.sti_name.lower()), None)
    if target is None:
        raise HTTPException(
            status_code=404,
            detail={
                "error": "FACEGEAR_STI_NOT_FOUND",
                "message": f"No '{body.sti_name}' under faces/FACESGEAR/ in this install",
            },
        )

    paths_to_write = [target.path]
    if body.apply_to_imp and not target.is_imp_variant:
        stem_no_ext = target.path.stem
        imp_candidate = target.path.with_name(f"{stem_no_ext}_IMP{target.path.suffix}")
        if imp_candidate.exists():
            paths_to_write.append(imp_candidate)

    install_root_resolved = info.path.resolve()
    nudged: list[FaceGearNudgeResult] = []
    with cross_process_install_lock(info.id), state.write_lock:
        # Snapshot inside the lock — see /facegear/overlay for the
        # rationale. Bug-review finding A8/E2.
        backup_entry = snapshot(
            install_root=info.path,
            install_id=info.id,
            files_to_back_up=paths_to_write,
            reason=f"facegear_nudge_{target.path.stem}_face{body.face_index}_{body.dx}_{body.dy}",
        )
        for p in paths_to_write:
            try:
                r = nudge_overlay_offset(p, body.face_index, body.dx, body.dy)
            except ValueError as e:
                raise HTTPException(
                    status_code=400,
                    detail={"error": "NUDGE_INVALID", "message": str(e)},
                )
            try:
                rel = p.resolve().relative_to(install_root_resolved)
                rel_str = str(rel).replace("\\", "/")
            except ValueError:
                rel_str = str(p)
            nudged.append(FaceGearNudgeResult(
                name=p.name,
                relative_path=rel_str,
                previous_offset_xy=r["previous_offset_xy"],
                new_offset_xy=r["new_offset_xy"],
            ))

    return FaceGearNudgeResponse(
        nudged=nudged,
        backup_id=backup_entry.id,
        install_id=info.id,
    )


class FaceGearSetOffsetBody(BaseModel):
    """Set absolute sOffsetX/sOffsetY on one FaceGear frame.

    Companion to `/facegear/nudge` (which shifts by a delta). Used by the
    direct-coord-editing X/Y inputs in FaceGearOverlayAuthor — the user
    types a target value and the wizard sets the offset to exactly that.
    `offset_x`/`offset_y` are signed INT16; values outside ±32768 return
    400 OFFSET_INVALID.
    """
    sti_name: str
    face_index: int = Field(..., ge=0, le=255)
    offset_x: int
    offset_y: int
    apply_to_imp: bool = True


class FaceGearSetOffsetResult(BaseModel):
    name: str
    relative_path: str
    previous_offset_xy: tuple[int, int]
    new_offset_xy: tuple[int, int]


class FaceGearSetOffsetResponse(BaseModel):
    written: list[FaceGearSetOffsetResult]
    backup_id: Optional[str] = None
    install_id: str


@router.post("/facegear/set-offset")
def facegear_set_offset(
    body: FaceGearSetOffsetBody,
    install_id: Optional[str] = Query(default=None),
) -> FaceGearSetOffsetResponse:
    """Set frame[face_index]'s sOffsetX/sOffsetY to absolute (offset_x,
    offset_y) on the named FaceGear STI (and its _IMP partner if
    `apply_to_imp`). Pure header edit; pixels untouched. Backup taken first.
    """
    info = _resolve_install(install_id)
    state = get_state()
    ctx = make_install_context(info.path)
    infos = detect_facegear_capacities(ctx)
    target = next((i for i in infos if i.name.lower() == body.sti_name.lower()), None)
    if target is None:
        raise HTTPException(
            status_code=404,
            detail={
                "error": "FACEGEAR_STI_NOT_FOUND",
                "message": f"No '{body.sti_name}' under faces/FACESGEAR/ in this install",
            },
        )

    paths_to_write = [target.path]
    if body.apply_to_imp and not target.is_imp_variant:
        stem_no_ext = target.path.stem
        imp_candidate = target.path.with_name(f"{stem_no_ext}_IMP{target.path.suffix}")
        if imp_candidate.exists():
            paths_to_write.append(imp_candidate)

    install_root_resolved = info.path.resolve()
    written: list[FaceGearSetOffsetResult] = []
    with cross_process_install_lock(info.id), state.write_lock:
        # Snapshot inside the lock — see /facegear/overlay for the
        # rationale. Bug-review finding A8/E2.
        backup_entry = snapshot(
            install_root=info.path,
            install_id=info.id,
            files_to_back_up=paths_to_write,
            reason=f"facegear_set_offset_{target.path.stem}_face{body.face_index}_{body.offset_x}_{body.offset_y}",
        )
        for p in paths_to_write:
            try:
                r = set_overlay_offset(p, body.face_index, body.offset_x, body.offset_y)
            except ValueError as e:
                raise HTTPException(
                    status_code=400,
                    detail={"error": "OFFSET_INVALID", "message": str(e)},
                )
            try:
                rel = p.resolve().relative_to(install_root_resolved)
                rel_str = str(rel).replace("\\", "/")
            except ValueError:
                rel_str = str(p)
            written.append(FaceGearSetOffsetResult(
                name=p.name,
                relative_path=rel_str,
                previous_offset_xy=r["previous_offset_xy"],
                new_offset_xy=r["new_offset_xy"],
            ))

    return FaceGearSetOffsetResponse(
        written=written,
        backup_id=backup_entry.id,
        install_id=info.id,
    )


@router.get("/facegear/overlay")
def facegear_overlay_preview(
    sti_name: str = Query(...),
    face_index: int = Query(..., ge=0, le=255),
    install_id: Optional[str] = Query(default=None),
) -> dict:
    """Read frame[face_index] from one FaceGear STI as a base64 PNG.

    Lets the UI show the merc's current overlay so users can see what's
    already in place before authoring a new one.
    """
    import base64

    info = _resolve_install(install_id)
    ctx = make_install_context(info.path)
    infos = detect_facegear_capacities(ctx)
    target = next((i for i in infos if i.name.lower() == sti_name.lower()), None)
    if target is None:
        raise HTTPException(
            status_code=404,
            detail={
                "error": "FACEGEAR_STI_NOT_FOUND",
                "message": f"No '{sti_name}' under faces/FACESGEAR/ in this install",
            },
        )
    png_bytes = extract_overlay(target.path, face_index)
    # Also surface the frame's signed sOffsetX/sOffsetY so the UI can show
    # the nudge widget for frames authored in a prior session — without
    # this, the widget was gated on session-local autoPos/nudge mutations
    # and stayed hidden on re-open. Returns null when the frame doesn't
    # exist (mirrors png_b64=None semantics).
    offset_xy = read_frame_offset(target.path, face_index)
    offset_payload = list(offset_xy) if offset_xy is not None else None
    if png_bytes is None:
        return {
            "sti_name": target.name,
            "face_index": face_index,
            "png_b64": None,
            "offset_xy": offset_payload,
        }
    return {
        "sti_name": target.name,
        "face_index": face_index,
        "png_b64": base64.b64encode(png_bytes).decode("ascii"),
        "offset_xy": offset_payload,
    }
