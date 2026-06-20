"""Merc CRUD: create, update, delete, move."""
from __future__ import annotations

import asyncio
import io
import json
from pathlib import Path
from typing import Callable, Optional

from fastapi import APIRouter, HTTPException, Query, Response
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from mercwizard_core import audit, backup, bundle as bundle_mod, relocator
from mercwizard_core.cross_lock import cross_process_install_lock
from mercwizard_core.install_context import make_install_context


def _install_context(info):
    """Build an InstallContext from an InstallInfo (convenience for route handlers)."""
    return make_install_context(info.path)
from mercwizard_core.inject import (
    aim_availability,
    edt as edt_mod,
    merc_availability,
    profiles_xml,
    starting_gear,
)
from mercwizard_core.models import AimBinding, Gear, Merc, MercBinding
from mercwizard_core.slot_picker import build_slot_picker

from .roster import _resolve_install
from .state import get_state

router = APIRouter()


class MercCreatePayload(BaseModel):
    merc: Merc
    gear: Gear | None = None
    aim_binding: AimBinding | None = None
    merc_binding: MercBinding | None = None
    force: bool = False  # allow overwriting an occupied slot


class MercUpdatePayload(BaseModel):
    merc: Merc | None = None
    gear: Gear | None = None
    aim_binding: AimBinding | None = None
    merc_binding: MercBinding | None = None


class MoveBody(BaseModel):
    to_slot: int
    to_install_id: str | None = None
    force: bool = False


def _backup_before_write(
    info,
    merc: Merc,
    reason: str,
    progress_cb: Optional[Callable[[int, int, str], None]] = None,
) -> backup.BackupEntry:
    """Snapshot the files that would be affected by a write on this merc.

    Returns the BackupEntry so callers can pass `backup_entry.id` to
    `backup.record_files_created` / `backup.restore` for rollback.
    `progress_cb` is forwarded to `backup.snapshot` for per-file progress
    streaming from Phase 1.
    """
    files = backup.files_for_merc(info.path, merc.uiIndex, merc.ubFaceIndex)
    return backup.snapshot(
        install_root=info.path,
        install_id=info.id,
        files_to_back_up=files,
        reason=reason,
        progress_cb=progress_cb,
    )


@router.post("/merc")
def create_merc(
    payload: MercCreatePayload,
    install_id: str | None = Query(default=None),
) -> dict:
    info = _resolve_install(install_id)
    state = get_state()

    from mercwizard_core.install_context import make_install_context
    ctx = make_install_context(info.path)
    profiles_path = ctx.profiles_xml_path(for_write=True)
    aim_path = ctx.aim_xml_path(for_write=True)
    merc_xml_path = ctx.merc_xml_path(for_write=True)
    gear_path = ctx.gear_xml_path(for_write=True)

    # Audit on payload data + live install state. Run outside the write
    # lock — it's pure validation. AIM-binding audit will fire later inside
    # the lock when we auto-derive the binding.
    picker = build_slot_picker(info.path, vfs_config_path=info.vfs_config_path, ctx=ctx)
    slot_info = picker.slots[payload.merc.uiIndex] if 0 <= payload.merc.uiIndex < len(picker.slots) else None
    issues = audit.audit_full(
        payload.merc,
        gear=payload.gear,
        aim_binding=payload.aim_binding,
        slot_info=slot_info,
    )
    if audit.has_errors(issues):
        raise HTTPException(status_code=400, detail={
            "error": "AUDIT_FAILED",
            "issues": [i.model_dump() for i in issues],
        })

    with cross_process_install_lock(info.id), state.write_lock:
        # Inside the lock: slot-occupancy check + bio-id allocation + writes
        # are all atomic together. Earlier code did SLOT_OCCUPIED + compute_*
        # outside the lock — two concurrent requests could pass the same
        # checks (TOCTOU) and write to the same AimBioID/MercBioID offset,
        # silently overwriting each other's bios.
        if not payload.force:
            if profiles_xml.is_slot_occupied(profiles_path, payload.merc.uiIndex):
                raise HTTPException(status_code=409, detail={
                    "error": "SLOT_OCCUPIED",
                    "slot": payload.merc.uiIndex,
                    "message": "Pass force=true to overwrite",
                })

        # Auto-fill the AIM binding when the frontend didn't supply one and
        # the merc is Type=AIM. AIM membership is purely XML-driven
        # (AIMAvailability.xml's <ProfilId>), not engine-hardcoded ranges,
        # so we do NOT gate on `is_aim_bound_slot`.
        aim_binding = payload.aim_binding
        if aim_binding is None and payload.merc.Type == 1:
            try:
                new_bio_id = aim_availability.compute_aim_bio_id(aim_path, payload.merc.uiIndex)
                aim_binding = AimBinding(
                    uiIndex=payload.merc.uiIndex,
                    description=payload.merc.zName or payload.merc.zNickname,
                    ProfilId=payload.merc.uiIndex,
                    AimBioID=new_bio_id,
                )
            except ValueError:
                pass

        # Auto-fill the M.E.R.C. binding for Type=2. Same XML-driven rationale.
        merc_binding = payload.merc_binding
        if merc_binding is None and payload.merc.Type == 2:
            try:
                new_merc_bio_id = merc_availability.compute_merc_bio_id(
                    merc_xml_path, payload.merc.uiIndex
                )
                ui_idx = merc_availability.compute_ui_index(merc_xml_path)
                merc_binding = MercBinding(
                    uiIndex=ui_idx,
                    Name=payload.merc.zName or payload.merc.zNickname,
                    ProfilId=payload.merc.uiIndex,
                    MercBioID=new_merc_bio_id,
                )
            except ValueError:
                pass

        # Phase 2.8: mirror deploy_import's `_rollback_and_raise` pattern.
        # If any write between backup and the final EDT raises (lxml
        # serialization error, disk full, etc.), revert the install to its
        # pre-create state so the user doesn't end up with a half-created
        # merc that has a profile row but no bio (or vice versa).
        backup_entry = _backup_before_write(
            info, payload.merc, reason=f"create_slot_{payload.merc.uiIndex}"
        )
        files_written: list[Path] = []
        error_step: Optional[str] = None
        steps_completed: list[str] = []
        try:
            error_step = "profiles"
            profiles_xml.upsert(profiles_path, payload.merc)
            files_written.append(profiles_path)
            steps_completed.append("profiles")

            if aim_binding is not None:
                error_step = "aim_avail"
                aim_availability.upsert(aim_path, aim_binding)
                files_written.append(aim_path)
                steps_completed.append("aim_avail")
            if merc_binding is not None and merc_xml_path is not None:
                error_step = "merc_avail"
                merc_availability.upsert(merc_xml_path, merc_binding)
                files_written.append(merc_xml_path)
                steps_completed.append("merc_avail")
            if payload.gear is not None:
                error_step = "gear"
                starting_gear.upsert(gear_path, payload.gear)
                files_written.append(gear_path)
                steps_completed.append("gear")

            error_step = "edt"
            aim_bio_id = aim_binding.AimBioID if aim_binding else None
            merc_bio_id = merc_binding.MercBioID if merc_binding else None
            route = edt_mod.write_bio(
                info.path,
                ui_index=payload.merc.uiIndex,
                biography=payload.merc.biographyText,
                additional=payload.merc.additionalInfoText,
                aim_bio_id=aim_bio_id,
                merc_bio_id=merc_bio_id,
                ctx=ctx,
            )
            files_written.append(route.path)
            steps_completed.append("edt")
        except HTTPException:
            # Bubble up framework errors (audit, slot-occupied, etc.) — they
            # haven't done any destructive I/O at this point.
            raise
        except Exception as e:
            rollback_ok = False
            rollback_msg: Optional[str] = None
            try:
                backup.record_files_created(
                    backup_id=backup_entry.id,
                    install_id=info.id,
                    files=[Path(p) for p in files_written],
                )
                backup.restore(
                    backup_id=backup_entry.id,
                    install_id=info.id,
                    install_root=info.path,
                )
                rollback_ok = True
            except Exception as rb_e:
                rollback_msg = f"{type(rb_e).__name__}: {rb_e}"
            detail = {
                "error": "CREATE_FAILED" if rollback_ok else "CREATE_FAILED_ROLLBACK_FAILED",
                "error_step": error_step,
                "steps_completed": steps_completed,
                "backup_id": backup_entry.id,
                "rollback_ok": rollback_ok,
                "rollback_error": rollback_msg,
                "message": f"{type(e).__name__}: {e}",
            }
            raise HTTPException(status_code=500, detail=detail)

    return {
        "ok": True,
        "slot": payload.merc.uiIndex,
        "issues": [i.model_dump() for i in issues],
    }


def _run_update(
    *,
    info,
    state,
    slot: int,
    payload: "MercUpdatePayload",
    profiles_path: Path,
    aim_path: Path,
    merc_xml_path: Optional[Path],
    gear_path: Path,
    emit: Callable[[dict], None],
    ctx=None,
) -> None:
    """Synchronous worker for `update_merc`.

    Runs inside `asyncio.to_thread` (so the route's event loop stays free)
    and inside `state.write_lock` (so all mutations remain serialized).
    Emits NDJSON events via `emit(...)`. Final event is always
    `{done: True, ok: bool, ...}` — even on rollback or unexpected failure.

    On any exception mid-writes, calls `backup.record_files_created` +
    `backup.restore` to revert the install to its pre-edit state, then
    emits the failure event.
    """
    files_written: list[Path] = []
    backup_entry: Optional[backup.BackupEntry] = None
    error_step: Optional[str] = None
    steps_completed: list[str] = []

    with cross_process_install_lock(info.id), state.write_lock:
        # ── bio-id derivation (no I/O; same logic as the pre-streaming code) ──
        aim_binding_to_use = payload.aim_binding
        if (
            aim_binding_to_use is None
            and payload.merc is not None
            and payload.merc.Type == 1
        ):
            existing = aim_availability.lookup_aim_bio_id(aim_path, slot)
            try:
                bio_id = existing if existing is not None else aim_availability.compute_aim_bio_id(aim_path, slot)
                aim_binding_to_use = AimBinding(
                    uiIndex=slot,
                    description=payload.merc.zName or payload.merc.zNickname,
                    ProfilId=slot,
                    AimBioID=bio_id,
                )
            except ValueError:
                pass

        merc_binding_to_use = payload.merc_binding
        if (
            merc_binding_to_use is None
            and payload.merc is not None
            and payload.merc.Type == 2
        ):
            existing_merc_bio = merc_availability.lookup_merc_bio_id(merc_xml_path, slot)
            try:
                bio_id = existing_merc_bio if existing_merc_bio is not None else \
                    merc_availability.compute_merc_bio_id(merc_xml_path, slot)
                existing_rows = merc_availability.read_all(merc_xml_path)
                ui_idx = existing_rows[slot].uiIndex if slot in existing_rows else \
                    merc_availability.compute_ui_index(merc_xml_path)
                merc_binding_to_use = MercBinding(
                    uiIndex=ui_idx,
                    Name=payload.merc.zName or payload.merc.zNickname,
                    ProfilId=slot,
                    MercBioID=bio_id,
                )
            except ValueError:
                pass

        try:
            if payload.merc is not None:
                # Step 1: backup snapshot. _backup_before_write returns the
                # BackupEntry so the rollback path can target this specific
                # snapshot.
                error_step = "backup"
                emit({"step": "backup", "status": "start", "label": "Backing up files..."})

                def _backup_progress(idx: int, total: int, rel: str) -> None:
                    emit({
                        "step": "backup",
                        "status": "progress",
                        "label": f"Backing up: {rel}",
                        "index": idx,
                        "total": total,
                    })

                backup_entry = _backup_before_write(
                    info, payload.merc, reason=f"edit_slot_{slot}",
                    progress_cb=_backup_progress,
                )
                emit({"step": "backup", "status": "done"})
                steps_completed.append("backup")

                # Step 2: MercProfiles.xml
                error_step = "profiles"
                emit({"step": "profiles", "status": "start", "label": "Writing merc profile..."})
                profiles_xml.upsert(profiles_path, payload.merc)
                files_written.append(profiles_path)
                emit({"step": "profiles", "status": "done"})
                steps_completed.append("profiles")

                # Step 3: EDT biography
                error_step = "edt"
                emit({"step": "edt", "status": "start", "label": "Writing biography..."})
                aim_bio_id = aim_binding_to_use.AimBioID if aim_binding_to_use else None
                merc_bio_id = merc_binding_to_use.MercBioID if merc_binding_to_use else None
                route = edt_mod.write_bio(
                    info.path,
                    ui_index=slot,
                    biography=payload.merc.biographyText,
                    additional=payload.merc.additionalInfoText,
                    aim_bio_id=aim_bio_id,
                    merc_bio_id=merc_bio_id,
                    ctx=ctx,
                )
                files_written.append(route.path)
                emit({"step": "edt", "status": "done"})
                steps_completed.append("edt")

            # Step 4: AIM availability (Type=1 path)
            if aim_binding_to_use is not None:
                error_step = "aim_avail"
                emit({"step": "aim_avail", "status": "start", "label": "Writing AIM availability..."})
                aim_availability.upsert(aim_path, aim_binding_to_use)
                files_written.append(aim_path)
                emit({"step": "aim_avail", "status": "done"})
                steps_completed.append("aim_avail")

            # Step 5: MERC availability (Type=2 path)
            if merc_binding_to_use is not None and merc_xml_path is not None:
                error_step = "merc_avail"
                emit({"step": "merc_avail", "status": "start", "label": "Writing MERC availability..."})
                merc_availability.upsert(merc_xml_path, merc_binding_to_use)
                files_written.append(merc_xml_path)
                emit({"step": "merc_avail", "status": "done"})
                steps_completed.append("merc_avail")

            # Step 6: starting gear
            if payload.gear is not None:
                error_step = "gear"
                emit({"step": "gear", "status": "start", "label": "Writing starting gear..."})
                starting_gear.upsert(gear_path, payload.gear)
                files_written.append(gear_path)
                emit({"step": "gear", "status": "done"})
                steps_completed.append("gear")

            emit({"done": True, "ok": True, "slot": slot})

        except Exception as e:
            # Phase 2.8: integrated partial-write rollback.
            #
            # Update semantics differ from deploy_import: we want
            # "revert the slot to its pre-edit contents" (the user was
            # editing an existing merc, edit failed, the original merc
            # should still be there). deploy_import's pattern is "delete
            # everything written" which is right for new-merc creates but
            # wrong for updates — it would erase Carter entirely.
            #
            # So we ONLY call restore (phase 1 = copy snapshot back over
            # the install). Phase 2 (files_created deletion) is skipped —
            # we don't want to delete files like MercProfiles.xml that
            # existed pre-update.
            error_type = type(e).__name__
            error_message = str(e)
            rollback_ok = False
            rollback_msg: Optional[str] = None
            if backup_entry is not None:
                try:
                    backup.restore(
                        backup_id=backup_entry.id,
                        install_id=info.id,
                        install_root=info.path,
                    )
                    rollback_ok = True
                except Exception as rb_e:
                    rollback_msg = f"{type(rb_e).__name__}: {rb_e}"
            emit({
                "done": True,
                "ok": False,
                "error": "SAVE_FAILED" if rollback_ok else "SAVE_FAILED_ROLLBACK_FAILED",
                "error_step": error_step,
                "steps_completed": steps_completed,
                "backup_id": backup_entry.id if backup_entry is not None else None,
                "rollback_ok": rollback_ok,
                "rollback_error": rollback_msg,
                "message": f"{error_type}: {error_message}",
            })


@router.put("/merc/{slot}")
async def update_merc(
    slot: int,
    payload: MercUpdatePayload,
    install_id: str | None = Query(default=None),
) -> StreamingResponse:
    """Update a merc and stream per-step progress as NDJSON.

    Audit + slot-mismatch checks run synchronously BEFORE the stream is
    opened, so input errors return as normal 400 responses (no body
    streaming).

    Once the stream opens, the response is always 200; success vs failure
    is in-band on the final `{done: true, ok: bool, ...}` event. This is
    a StreamingResponse limitation (HTTP status can't change mid-body).
    """
    info = _resolve_install(install_id)
    state = get_state()

    if payload.merc is not None and payload.merc.uiIndex != slot:
        raise HTTPException(status_code=400, detail={
            "error": "SLOT_MISMATCH",
            "message": f"Path slot={slot} but payload.merc.uiIndex={payload.merc.uiIndex}",
        })

    from mercwizard_core.install_context import make_install_context
    ctx = make_install_context(info.path)
    profiles_path = ctx.profiles_xml_path(for_write=True)
    aim_path = ctx.aim_xml_path(for_write=True)
    merc_xml_path = ctx.merc_xml_path(for_write=True)
    gear_path = ctx.gear_xml_path(for_write=True)

    # Audit OUTSIDE the stream so AUDIT_FAILED returns as a normal 400.
    if payload.merc is not None:
        picker = build_slot_picker(info.path, vfs_config_path=info.vfs_config_path, ctx=ctx)
        slot_info = picker.slots[payload.merc.uiIndex] if 0 <= payload.merc.uiIndex < len(picker.slots) else None
        issues = audit.audit_full(
            payload.merc,
            gear=payload.gear,
            aim_binding=payload.aim_binding,
            slot_info=slot_info,
        )
        if audit.has_errors(issues):
            raise HTTPException(status_code=400, detail={
                "error": "AUDIT_FAILED",
                "issues": [i.model_dump() for i in issues],
            })

    async def event_stream():
        queue: asyncio.Queue[dict] = asyncio.Queue()
        loop = asyncio.get_running_loop()

        def emit(ev: dict) -> None:
            # Threadsafe — _run_update runs in a worker thread via asyncio.to_thread.
            loop.call_soon_threadsafe(queue.put_nowait, ev)

        task = asyncio.create_task(asyncio.to_thread(
            _run_update,
            info=info,
            state=state,
            slot=slot,
            payload=payload,
            profiles_path=profiles_path,
            aim_path=aim_path,
            merc_xml_path=merc_xml_path,
            gear_path=gear_path,
            emit=emit,
            ctx=ctx,
        ))

        try:
            while True:
                try:
                    ev = await asyncio.wait_for(queue.get(), timeout=0.5)
                except asyncio.TimeoutError:
                    # Worker may have finished or be still processing a slow op.
                    # If task is done AND queue is empty, emit a synthetic done
                    # event (defensive — _run_update should always emit one).
                    if task.done() and queue.empty():
                        exc = task.exception()
                        if exc is not None:
                            yield json.dumps({
                                "done": True, "ok": False,
                                "error": "INTERNAL_ERROR",
                                "message": f"{type(exc).__name__}: {exc}",
                            }) + "\n"
                        else:
                            yield json.dumps({
                                "done": True, "ok": False,
                                "error": "INTERNAL_ERROR",
                                "message": "Worker thread finished without emitting done event.",
                            }) + "\n"
                        return
                    continue
                yield json.dumps(ev) + "\n"
                if ev.get("done"):
                    break
        finally:
            # Always wait on the task so unhandled exceptions don't leak.
            try:
                await task
            except Exception:
                pass  # Already surfaced via the stream (or in INTERNAL_ERROR above).

    return StreamingResponse(event_stream(), media_type="application/x-ndjson")


@router.delete("/merc/{slot}")
def delete_merc(slot: int, install_id: str | None = Query(default=None)) -> dict:
    info = _resolve_install(install_id)
    state = get_state()

    from mercwizard_core.install_context import make_install_context
    ctx = make_install_context(info.path)
    profiles_path = ctx.profiles_xml_path(for_write=True)
    aim_path = ctx.aim_xml_path(for_write=True)
    merc_xml_path = ctx.merc_xml_path(for_write=True)
    gear_path = ctx.gear_xml_path(for_write=True)

    if profiles_xml.read_slot(profiles_path, slot) is None:
        raise HTTPException(status_code=404, detail={"error": "SLOT_EMPTY"})

    # Use the placeholder-aware lookup, not raw read_all — modded
    # AIMAvailability.xml files ship `<AimBioID>-1</AimBioID>` /
    # `<ProfilId>-1</ProfilId>` placeholder rows for every slot 0-254.
    # Reading raw and using `.AimBioID` here returns -1 for those slots,
    # which then trips edt.py:315's `0 <= aim_bio_id <= 199` guard with
    # a ValueError → the catch-all rollback at line 596 returns
    # DELETE_FAILED 500. Bug-review finding C1 — broke delete on any
    # empty slot of any modded install with placeholder rows.
    aim_bio_id = aim_availability.lookup_aim_bio_id(aim_path, slot)
    merc_bio_id = merc_availability.lookup_merc_bio_id(merc_xml_path, slot)

    with cross_process_install_lock(info.id), state.write_lock:
        face_index = None
        existing = profiles_xml.read_slot(profiles_path, slot)
        if existing is not None:
            try:
                face_index = int(existing.get("ubFaceIndex", "0").strip())
            except (ValueError, AttributeError):
                pass
        files = backup.files_for_merc(info.path, slot, face_index)
        backup_entry = backup.snapshot(
            info.path, info.id, files, reason=f"delete_slot_{slot}",
        )

        # Wrap the multi-file delete in rollback semantics so a mid-way
        # failure (lxml serialization on AIMAvailability.xml, EDT seek
        # past end on a corrupt MERCBIOS, etc.) restores the install
        # instead of leaving the user with a half-deleted merc (profile
        # cleared but bio still present, or vice versa). Mirrors the
        # rollback pattern in create_merc + _run_update.
        error_step: Optional[str] = None
        try:
            error_step = "profiles"
            profiles_xml.clear_slot(profiles_path, slot)
            error_step = "aim_avail"
            aim_availability.remove(aim_path, slot)
            if merc_xml_path is not None:
                error_step = "merc_avail"
                merc_availability.remove(merc_xml_path, slot)
            error_step = "gear"
            starting_gear.clear_slot(gear_path, slot)
            error_step = "edt"
            edt_mod.clear_bio(
                info.path, slot,
                aim_bio_id=aim_bio_id, merc_bio_id=merc_bio_id,
                ctx=ctx,
            )
        except Exception as e:
            # Attempt rollback. If restore also fails, surface both
            # errors so the user knows the install needs manual repair.
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
                "error": "DELETE_FAILED" if rollback_ok else "DELETE_FAILED_ROLLBACK_FAILED",
                "message": f"{type(e).__name__}: {e}",
                "error_step": error_step,
                "backup_id": backup_entry.id,
                "rollback_ok": rollback_ok,
            }
            if rollback_error:
                detail["rollback_error"] = rollback_error
            raise HTTPException(status_code=500, detail=detail) from e

    return {"ok": True, "slot": slot}


# ─── Decoded-portrait LRU cache ─────────────────────────────────────
# Keys: (install_id, face_index, size, source_id) where source_id is
# either the file's mtime_ns+size (loose case) or a sha-ish identifier
# for SLF entries (mtime_ns of the SLF + entry name). Values: PNG bytes.
# Hits skip the STI decode entirely — typical roster repaint goes from
# ~3 s to ~10 ms. User feedback: "i want it to be fast".
#
# Lock added 2026-05-25: FastAPI runs each handler in a threadpool
# worker. A 16-cell roster fetches 16 portraits in parallel, all
# hitting this cache. Without the lock, concurrent inserts during
# eviction can KeyError on `del first_key` (one thread evicted the
# entry the other thread expected to delete), surfacing as 500
# INTERNAL_ERROR and a blank cell.
import threading
_PNG_CACHE: dict[tuple[str, int, str, str], bytes] = {}
_PNG_CACHE_MAX = 512  # ~512 portraits @ ~5 KB each = 2.5 MB; fine.
_PNG_CACHE_LOCK = threading.Lock()


def _png_cache_get(key: tuple[str, int, str, str]) -> Optional[bytes]:
    with _PNG_CACHE_LOCK:
        return _PNG_CACHE.get(key)


def _etag_for_png(png_bytes: bytes) -> str:
    """Stable ETag derived from the PNG body. Cheap (md5 of first 4 KB
    is enough — portrait STIs decode to a stable PNG byte stream when
    the source hasn't changed). Quoted to match the HTTP ETag grammar."""
    import hashlib
    h = hashlib.md5(png_bytes[:4096]).hexdigest()[:16]
    return f'"{h}-{len(png_bytes)}"'


def _png_cache_put(key: tuple[str, int, str, str], value: bytes) -> None:
    with _PNG_CACHE_LOCK:
        if len(_PNG_CACHE) >= _PNG_CACHE_MAX:
            try:
                first_key = next(iter(_PNG_CACHE))
                del _PNG_CACHE[first_key]
            except StopIteration:
                pass
        _PNG_CACHE[key] = value


@router.get("/merc/{slot}/portrait")
def get_merc_portrait(
    slot: int,
    install_id: str | None = Query(default=None),
    size: str = Query(default="smallface", description="smallface | face_65 | face_33 | bigface"),
) -> Response:
    """Stream the slot's portrait STI's first frame as PNG.

    Used by the roster grid for thumbnails. Reads `ubFaceIndex` from
    MercProfiles.xml — that's the engine-correct face routing (a merc at
    slot 200 may legitimately point at face 26 to reuse the vanilla
    artwork, see audit FACE_INDEX_SHADOWS_VANILLA).

    Lookup order for the STI bytes (matches the engine's VFS precedence):
      1. Loose files under mod content (Data-1.13/Faces/, etc.)
      2. Loose files under any data layer (read_resolve fallback)
      3. SLF archives in every data layer (Data/Faces.slf for vanilla
         face_index 0-159, mod-shipped Faces.slf if present)

    Decoded PNGs are cached in a module-level LRU so the second roster
    view is instant. The cache key includes face_index + size, so a
    portrait recompile (which bumps the underlying STI mtime) doesn't
    self-evict cleanly across the whole cache; the roster query
    invalidates the URL cache-buster which forces a refetch of the
    *PNG*, and the cached entry is overwritten with the fresh decode.

    Returns 404 for empty slots, 404 when neither loose nor SLF has the
    STI, and 204 when ubFaceIndex is 0 (vanilla "no portrait" convention).
    """
    info = _resolve_install(install_id)
    ctx = make_install_context(info.path)
    profiles_path = ctx.profiles_xml_path()
    raw = profiles_xml.read_slot(profiles_path, slot)
    if raw is None:
        raise HTTPException(status_code=404, detail={
            "error": "SLOT_EMPTY",
            "slot": slot,
        })
    try:
        face_index = int(raw.get("ubFaceIndex", "0").strip())
    except (ValueError, AttributeError):
        face_index = 0
    if face_index == 0 and slot != 0:
        # Face index 0 is "no portrait" (vanilla convention) for every slot
        # EXCEPT slot 0 (Barry/the Chosen one), whose real face IS face 0 —
        # matches the roster bake's `face_index == 0 and slot != 0` skip.
        # Return 204 so the client can render the slot number alone.
        return Response(status_code=204)

    # Resolve source bytes + a version-id derived from the on-disk
    # mtime (loose) or SLF mtime + entry path (SLF). The version-id
    # goes into the cache key so a portrait recompile or SLF replace
    # naturally invalidates without manual eviction. The stat to
    # compute source_id is ~0.1ms; the decode it short-circuits is
    # ~10-50ms — net win even when the cache misses.
    # Resolve the requested size first, then the fallback chain — and accept
    # the FIRST candidate that BOTH resolves AND decodes. This mirrors the
    # roster grid bake (routes/roster.py _bake_portrait_sheet) so the sidebar
    # BigFace never goes blank for a merc the grid renders: a BigFace-less
    # NPC (most NPCs) downscales from its SmallFace instead of 404-ing, and a
    # present-but-undecodable size (16-bit / malformed palette) is skipped in
    # favour of a sibling size that decodes. Lazy import — ja2py is vendored.
    from .roster import _FALLBACK_ORDER  # the grid bake's fallback policy
    from mercwizard_core.sti_decode import decode_sti_frame_to_png

    last_reason = (
        f"face {face_index} ({size}) not found loose or in any SLF "
        f"archive under {info.path}"
    )
    for cand in (size, *_FALLBACK_ORDER.get(size, ())):
        result = ctx.face_sti_bytes(face_index, size=cand)
        if result is None:
            continue
        sti_bytes, source_id = result
        # Cache key keeps the REQUESTED size + the chosen candidate's
        # source_id (on-disk/SLF mtime), so a recompile or SLF replace
        # invalidates naturally and repeat requests hit the cache.
        cache_key = (info.id, face_index, size, source_id)
        cached = _png_cache_get(cache_key)
        if cached is not None:
            return Response(
                content=cached,
                media_type="image/png",
                headers={
                    "Cache-Control": "private, max-age=60",
                    "ETag": _etag_for_png(cached),
                },
            )
        png_bytes = decode_sti_frame_to_png(sti_bytes, frame_index=0)
        if png_bytes is None:
            last_reason = (
                f"face {face_index} ({cand}) decode returned None "
                f"(likely 16-bit STI or malformed palette)"
            )
            continue
        _png_cache_put(cache_key, png_bytes)
        return Response(
            content=png_bytes,
            media_type="image/png",
            headers={
                "Cache-Control": "private, max-age=60",
                "ETag": _etag_for_png(png_bytes),
            },
        )

    # No candidate size resolved + decoded — the client renders a placeholder.
    raise HTTPException(status_code=404, detail={
        "error": "PORTRAIT_NOT_FOUND",
        "slot": slot,
        "face_index": face_index,
        "size": size,
        "message": last_reason,
    })


@router.get("/merc/{slot}/animation-frames.png")
def get_merc_animation_frames(
    slot: int,
    install_id: str | None = Query(default=None),
) -> Response:
    """One horizontal strip of every animation frame in the merc's SmallFace
    STI, each composited onto the base face the way the engine renders it.

    A JA2 face STI is: frame 0 = the full face, then the eye/blink sub-frames
    (1-4), then the mouth/talk sub-frames (5-7) — small region images the
    engine pastes onto the base at the merc's usEyesX/Y / usMouthX/Y at
    runtime. We reconstruct each composited face into a 48x43 cell so the
    Edit tab can show the merc's whole expression set (base · blink · talk),
    not just the neutral portrait. 204 when the slot has no portrait (face
    index 0); 404 when the SmallFace STI is missing.
    """
    import io as _io
    from PIL import Image as _Image

    info = _resolve_install(install_id)
    ctx = make_install_context(info.path)
    raw = profiles_xml.read_slot(ctx.profiles_xml_path(), slot)
    if raw is None:
        raise HTTPException(status_code=404, detail={"error": "SLOT_EMPTY", "slot": slot})
    try:
        face_index = int(raw.get("ubFaceIndex", "0").strip())
    except (ValueError, AttributeError):
        face_index = 0
    if face_index == 0 and slot != 0:
        return Response(status_code=204)

    res = ctx.face_sti_bytes(face_index, size="smallface")
    if res is None:
        raise HTTPException(status_code=404, detail={
            "error": "PORTRAIT_NOT_FOUND", "slot": slot, "face_index": face_index,
            "message": f"SmallFace STI for face {face_index} not found",
        })
    sti_bytes, _source_id = res

    from mercwizard_core.sti_decode import decode_sti_frame_to_png
    frames: list = []
    for i in range(64):  # generous cap; real STIs hold ~8
        png = decode_sti_frame_to_png(sti_bytes, frame_index=i)
        if png is None:
            break
        try:
            frames.append(_Image.open(_io.BytesIO(png)).convert("RGBA"))
        except Exception:  # noqa: BLE001
            break
    if not frames:
        raise HTTPException(status_code=404, detail={
            "error": "PORTRAIT_DECODE_FAILED", "slot": slot, "face_index": face_index,
        })

    def _coord(key: str) -> int:
        try:
            return int((raw.get(key) or "0").strip())
        except (ValueError, AttributeError):
            return 0
    eye_xy = (_coord("usEyesX"), _coord("usEyesY"))
    mouth_xy = (_coord("usMouthX"), _coord("usMouthY"))

    base = frames[0]
    n = len(frames)
    # Canonical layout authored by the merc pipeline + shipped by vanilla:
    # after the base, the next (up to) 4 frames are eye/blink, the rest are
    # mouth/talk. min() guards a short STI.
    eye_n = min(4, n - 1)
    cw, ch = _CELL_SIZES_SMALLFACE
    cells: list = []
    for i, fr in enumerate(frames):
        if i == 0:
            cell = base.copy()
        else:
            cell = base.copy()
            pos = eye_xy if i <= eye_n else mouth_xy
            try:
                cell.paste(fr, pos, fr)  # alpha-composite the sub-frame at its coord
            except (ValueError, SystemError):
                pass  # bad coord / size mismatch — leave the base for this cell
        if cell.size != (cw, ch):
            cell = cell.resize((cw, ch), _Image.NEAREST)
        cells.append(cell)

    strip = _Image.new("RGBA", (cw * len(cells), ch), (0, 0, 0, 0))
    for idx, im in enumerate(cells):
        strip.paste(im, (idx * cw, 0))
    buf = _io.BytesIO()
    strip.save(buf, format="PNG")
    data = buf.getvalue()
    return Response(
        content=data,
        media_type="image/png",
        headers={"Cache-Control": "private, max-age=60", "ETag": _etag_for_png(data)},
    )


# Canonical SmallFace cell size — every composited animation frame is
# normalized to this so the frontend can slice the strip on a fixed stride.
_CELL_SIZES_SMALLFACE = (48, 43)


def _decode_face_sti_frame_zero(sti_path: Path) -> Optional[bytes]:
    """Decode frame[0] of a portrait STI to PNG bytes.

    Thin wrapper around `sti_decode.decode_sti_frame_to_png` for the
    roster portrait endpoint. Kept as a route-local function so any
    future portrait-specific logic (default fallback art, cache-key
    derivation) has a natural home without polluting the shared decoder.
    """
    from mercwizard_core.sti_decode import decode_sti_frame_to_png
    return decode_sti_frame_to_png(sti_path, frame_index=0)


def _run_move_same_install(
    *,
    info,
    state,
    source_slot: int,
    dest_slot: int,
    emit: Callable[[dict], None],
) -> None:
    """Worker for the same-install branch of /move. Streams events the way
    `_run_duplicate` does."""
    steps_completed: list[str] = []
    backup_entry: Optional[backup.BackupEntry] = None
    error_step: Optional[str] = None

    with cross_process_install_lock(info.id), state.write_lock:
        try:
            error_step = "backup"
            emit({"step": "backup", "status": "start", "label": "Backing up files…"})
            backup_files: list[Path] = []
            profiles_path = _install_context(info).profiles_xml_path()
            for s in (source_slot, dest_slot):
                existing = profiles_xml.read_slot(profiles_path, s)
                face_index = None
                if existing:
                    try:
                        face_index = int(existing.get("ubFaceIndex", "0").strip())
                    except (ValueError, AttributeError):
                        pass
                backup_files.extend(backup.files_for_merc(info.path, s, face_index))

            def _backup_progress(idx: int, total: int, rel: str) -> None:
                emit({
                    "step": "backup",
                    "status": "progress",
                    "label": f"Backing up: {rel}",
                    "index": idx,
                    "total": total,
                })

            backup_entry = backup.snapshot(
                install_root=info.path,
                install_id=info.id,
                files_to_back_up=backup_files,
                reason=f"move_{source_slot}_to_{dest_slot}",
                progress_cb=_backup_progress,
            )
            emit({"step": "backup", "status": "done"})
            steps_completed.append("backup")

            error_step = "move"
            emit({"step": "move", "status": "start", "label": "Relocating merc data…"})
            try:
                report = relocator.move(info.path, source_slot=source_slot, dest_slot=dest_slot)
            except relocator.MoveError as e:
                emit({
                    "done": True, "ok": False,
                    "error": "MOVE_INVALID",
                    "message": str(e),
                    "error_step": "move",
                    "steps_completed": steps_completed,
                    "backup_id": backup_entry.id if backup_entry else None,
                })
                return

            if not report.success:
                emit({
                    "done": True, "ok": False,
                    "error": "MOVE_FAILED",
                    "message": report.error or "Unknown move failure",
                    "error_step": report.error_step or "move",
                    "steps_completed": steps_completed + (report.steps_completed or []),
                    "backup_id": backup_entry.id if backup_entry else None,
                })
                return

            steps_completed.extend(report.steps_completed)
            emit({"step": "move", "status": "done"})
            emit({
                "done": True, "ok": True,
                "from": source_slot,
                "to": dest_slot,
                "cross_install": False,
                "steps_completed": steps_completed,
            })
        except Exception as e:
            emit({
                "done": True, "ok": False,
                "error": "INTERNAL_ERROR",
                "message": f"{type(e).__name__}: {e}",
                "error_step": error_step,
                "steps_completed": steps_completed,
                "backup_id": backup_entry.id if backup_entry else None,
            })


def _run_move_cross_install(
    *,
    info,
    target_info,
    state,
    source_slot: int,
    dest_slot: int,
    force: bool,
    emit: Callable[[dict], None],
) -> None:
    """Worker for the cross-install branch. Coarse-grained progress since
    `move_between_installs` is a self-contained export+import pipeline."""
    error_step: Optional[str] = None
    _ids_sorted = sorted([info.id, target_info.id])
    with cross_process_install_lock(_ids_sorted[0]), \
         cross_process_install_lock(_ids_sorted[1]), \
         state.write_lock:
        try:
            error_step = "move"
            emit({
                "step": "move",
                "status": "start",
                "label": f"Moving slot {source_slot} → {target_info.id} slot {dest_slot}…",
            })
            cross_report = bundle_mod.move_between_installs(
                source_install=info.path,
                source_install_id=info.id,
                target_install=target_info.path,
                target_install_id=target_info.id,
                source_slot=source_slot,
                target_slot=dest_slot,
                force=force,
            )
            emit({"step": "move", "status": "done"})
            emit({
                "done": True, "ok": True,
                "from": source_slot,
                "to": dest_slot,
                "to_install_id": target_info.id,
                "cross_install": True,
                "steps_completed": ["move"],
                "report": {
                    "source_install_root": cross_report.source_install_root,
                    "target_install_root": cross_report.target_install_root,
                    "files_written": cross_report.files_written,
                    "portrait_compiled": cross_report.portrait_compiled,
                    "voice_clips_copied": cross_report.voice_clips_copied,
                    "aim_bio_id_used": cross_report.aim_bio_id_used,
                    "source_backup_id": cross_report.source_backup_id,
                    "issues": cross_report.issues,
                    "partial_failures": cross_report.partial_failures,
                },
            })
        except bundle_mod.ImportAuditError as e:
            emit({
                "done": True, "ok": False,
                "error": "AUDIT_FAILED",
                "issues": e.issues,
                "error_step": "move",
            })
        except bundle_mod.SlotOccupiedError as e:
            emit({
                "done": True, "ok": False,
                "error": "SLOT_OCCUPIED",
                "slot": e.slot,
                "message": "Pass force=true to overwrite the target slot",
                "error_step": "move",
            })
        except ValueError as e:
            emit({
                "done": True, "ok": False,
                "error": "MOVE_INVALID",
                "message": str(e),
                "error_step": "move",
            })
        except Exception as e:
            emit({
                "done": True, "ok": False,
                "error": "INTERNAL_ERROR",
                "message": f"{type(e).__name__}: {e}",
                "error_step": error_step,
            })


@router.post("/merc/{slot}/move")
async def move_merc(
    slot: int,
    body: MoveBody,
    install_id: str | None = Query(default=None),
) -> StreamingResponse:
    """Streaming move. Same NDJSON envelope as /duplicate and PUT /merc/{slot}.

    Branches on `body.to_install_id`: same-install (relocator) emits per-step
    backup + move events; cross-install (bundle move_between_installs) emits
    a single coarse "move" step because the pipeline owns its own progress
    surface. Both end with `{done: True, ok: bool, ...}`.
    """
    info = _resolve_install(install_id)
    state = get_state()

    cross_install = body.to_install_id is not None and body.to_install_id != info.id
    target_info = None
    if cross_install:
        target_info = state.get_install(body.to_install_id)  # type: ignore[arg-type]
        # Split the two failure modes so the frontend can show the specific
        # reason rather than the old "not registered or invalid" hedge.
        if target_info is None:
            raise HTTPException(
                status_code=400,
                detail={
                    "error": "INVALID_TARGET_INSTALL",
                    "message": (
                        f"Target install '{body.to_install_id}' is not registered. "
                        "Refresh installs in Settings or re-add the install path."
                    ),
                },
            )
        if not target_info.valid:
            errs = "; ".join(target_info.errors) if target_info.errors else "validation failed"
            raise HTTPException(
                status_code=400,
                detail={
                    "error": "INVALID_TARGET_INSTALL",
                    "message": (
                        f"Target install '{body.to_install_id}' is registered but currently invalid: "
                        f"{errs}"
                    ),
                },
            )

    async def event_stream():
        queue: asyncio.Queue[dict] = asyncio.Queue()
        loop = asyncio.get_running_loop()

        def emit(ev: dict) -> None:
            loop.call_soon_threadsafe(queue.put_nowait, ev)

        if cross_install:
            task = asyncio.create_task(asyncio.to_thread(
                _run_move_cross_install,
                info=info, target_info=target_info, state=state,
                source_slot=slot, dest_slot=body.to_slot, force=body.force,
                emit=emit,
            ))
        else:
            task = asyncio.create_task(asyncio.to_thread(
                _run_move_same_install,
                info=info, state=state,
                source_slot=slot, dest_slot=body.to_slot,
                emit=emit,
            ))

        try:
            while True:
                try:
                    ev = await asyncio.wait_for(queue.get(), timeout=0.5)
                except asyncio.TimeoutError:
                    if task.done() and queue.empty():
                        exc = task.exception()
                        if exc is not None:
                            yield json.dumps({
                                "done": True, "ok": False,
                                "error": "INTERNAL_ERROR",
                                "message": f"{type(exc).__name__}: {exc}",
                            }) + "\n"
                        else:
                            yield json.dumps({
                                "done": True, "ok": False,
                                "error": "INTERNAL_ERROR",
                                "message": "Worker thread finished without emitting done event.",
                            }) + "\n"
                        return
                    continue
                yield json.dumps(ev) + "\n"
                if ev.get("done"):
                    break
        finally:
            try:
                await task
            except Exception:
                pass

    return StreamingResponse(event_stream(), media_type="application/x-ndjson")


def _run_duplicate(
    *,
    info,
    state,
    source_slot: int,
    dest_slot: int,
    emit: Callable[[dict], None],
) -> None:
    """Worker for the streaming /duplicate endpoint.

    Mirrors `_run_update`'s shape: takes a per-event emit callback, runs
    inside the cross-process install lock + state.write_lock, ends with a
    `{done: True, ok: bool, ...}` event regardless of outcome.

    Backup snapshot streams per-file progress via `_backup_progress`; the
    copy phase emits a single `copy/start` then a `copy/done` with the list
    of completed sub-steps (Profile / AIM row / MERC row / Gear / EDT) in
    the same `steps_completed` payload field `SaveProgressBar` consumes.
    """
    steps_completed: list[str] = []
    backup_entry: Optional[backup.BackupEntry] = None
    error_step: Optional[str] = None

    with cross_process_install_lock(info.id), state.write_lock:
        try:
            # Step 1: back up source AND dest (dest is mutated; source is
            # snapshotted as a safety net so the rollback can recover
            # accidental cross-talk on shared face/gear indexes).
            error_step = "backup"
            emit({"step": "backup", "status": "start", "label": "Backing up files…"})
            backup_files: list[Path] = []
            profiles_path = _install_context(info).profiles_xml_path()
            for s in (source_slot, dest_slot):
                existing = profiles_xml.read_slot(profiles_path, s)
                face_index = None
                if existing:
                    try:
                        face_index = int(existing.get("ubFaceIndex", "0").strip())
                    except (ValueError, AttributeError):
                        pass
                backup_files.extend(backup.files_for_merc(info.path, s, face_index))

            def _backup_progress(idx: int, total: int, rel: str) -> None:
                emit({
                    "step": "backup",
                    "status": "progress",
                    "label": f"Backing up: {rel}",
                    "index": idx,
                    "total": total,
                })

            backup_entry = backup.snapshot(
                install_root=info.path,
                install_id=info.id,
                files_to_back_up=backup_files,
                reason=f"duplicate_{source_slot}_to_{dest_slot}",
                progress_cb=_backup_progress,
            )
            emit({"step": "backup", "status": "done"})
            steps_completed.append("backup")

            # Step 2: copy. relocator.duplicate handles XML+EDT writes and
            # returns a MoveReport with .steps_completed entries that describe
            # each finished sub-step. We surface them as one combined event.
            error_step = "copy"
            emit({"step": "copy", "status": "start", "label": "Copying merc data…"})
            try:
                report = relocator.duplicate(info.path, source_slot=source_slot, dest_slot=dest_slot)
            except relocator.MoveError as e:
                emit({
                    "done": True, "ok": False,
                    "error": "DUPLICATE_INVALID",
                    "message": str(e),
                    "error_step": "copy",
                    "steps_completed": steps_completed,
                    "backup_id": backup_entry.id if backup_entry else None,
                })
                return

            if not report.success:
                emit({
                    "done": True, "ok": False,
                    "error": "DUPLICATE_FAILED",
                    "message": report.error or "Unknown duplicate failure",
                    "error_step": report.error_step or "copy",
                    "steps_completed": steps_completed + (report.steps_completed or []),
                    "backup_id": backup_entry.id if backup_entry else None,
                })
                return

            steps_completed.extend(report.steps_completed)
            emit({"step": "copy", "status": "done"})

            emit({
                "done": True, "ok": True,
                "from": source_slot,
                "to": dest_slot,
                "steps_completed": steps_completed,
            })

        except Exception as e:
            emit({
                "done": True, "ok": False,
                "error": "INTERNAL_ERROR",
                "message": f"{type(e).__name__}: {e}",
                "error_step": error_step,
                "steps_completed": steps_completed,
                "backup_id": backup_entry.id if backup_entry else None,
            })


@router.post("/merc/{slot}/duplicate")
async def duplicate_merc(
    slot: int,
    body: MoveBody,
    install_id: str | None = Query(default=None),
) -> StreamingResponse:
    """Non-destructive variant of move: source stays filled, dest receives a copy.

    Streams per-step progress as NDJSON so the Duplicate UI can render a
    live progress bar (same format `update_merc` emits). The final event is
    always `{done: True, ok: bool, ...}` — failure context lives in-band so
    the HTTP status stays 200 once the stream has opened.
    """
    info = _resolve_install(install_id)
    state = get_state()

    async def event_stream():
        queue: asyncio.Queue[dict] = asyncio.Queue()
        loop = asyncio.get_running_loop()

        def emit(ev: dict) -> None:
            loop.call_soon_threadsafe(queue.put_nowait, ev)

        task = asyncio.create_task(asyncio.to_thread(
            _run_duplicate,
            info=info,
            state=state,
            source_slot=slot,
            dest_slot=body.to_slot,
            emit=emit,
        ))

        try:
            while True:
                try:
                    ev = await asyncio.wait_for(queue.get(), timeout=0.5)
                except asyncio.TimeoutError:
                    if task.done() and queue.empty():
                        exc = task.exception()
                        if exc is not None:
                            yield json.dumps({
                                "done": True, "ok": False,
                                "error": "INTERNAL_ERROR",
                                "message": f"{type(exc).__name__}: {exc}",
                            }) + "\n"
                        else:
                            yield json.dumps({
                                "done": True, "ok": False,
                                "error": "INTERNAL_ERROR",
                                "message": "Worker thread finished without emitting done event.",
                            }) + "\n"
                        return
                    continue
                yield json.dumps(ev) + "\n"
                if ev.get("done"):
                    break
        finally:
            try:
                await task
            except Exception:
                pass

    return StreamingResponse(event_stream(), media_type="application/x-ndjson")
