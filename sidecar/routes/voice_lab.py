"""Typed, path-safe HTTP façade for the Voice Lab domain services."""
from __future__ import annotations

from hashlib import sha256
import os
from pathlib import Path
import re
import tempfile
from threading import RLock
from typing import Any, Literal
from uuid import uuid4

from fastapi import APIRouter, File, HTTPException, Query, Request, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel, Field

from mercwizard_core.inject._atomic_xml import write_bytes_atomic
from mercwizard_core.voice_lab.audio import AudioToolchain
from mercwizard_core.voice_lab.jobs import VoiceLabJobs
from mercwizard_core.voice_lab.models import DeployPlan, EditRecipe, FindingState, InventorySnapshot
from mercwizard_core.voice_lab.recipes import ensure_workspace_is_external
from mercwizard_core.voice_lab.service import VoiceLabOperationError, VoiceLabService
from mercwizard_core.voice_lab.transcriber import Transcriber

from .roster import _resolve_install
from .state import get_state


router = APIRouter()
_jobs = VoiceLabJobs()
_snapshots: dict[str, InventorySnapshot] = {}
_snapshots_lock = RLock()
_plans: dict[str, tuple[str, DeployPlan]] = {}
_plans_lock = RLock()
_preview_generations: dict[tuple[str, str], str] = {}
_preview_generations_lock = RLock()
_SAFE_UPLOAD_NAME = re.compile(r"[^A-Za-z0-9_.-]+")
_AUDIO_EXTENSIONS = {".ogg": "audio/ogg", ".wav": "audio/wav", ".mp3": "audio/mpeg"}


def _audio_response(data: bytes, media_type: str, range_header: str | None) -> Response:
    """Return audio bytes with the single-range support browser media controls need."""
    total = len(data)
    base_headers = {"Accept-Ranges": "bytes"}
    if not range_header:
        return Response(
            data,
            media_type=media_type,
            headers={**base_headers, "Content-Length": str(total)},
        )

    match = re.fullmatch(r"bytes=(\d*)-(\d*)", range_header.strip())
    if match is None or total == 0:
        return Response(status_code=416, headers={**base_headers, "Content-Range": f"bytes */{total}"})

    start_text, end_text = match.groups()
    if not start_text and not end_text:
        return Response(status_code=416, headers={**base_headers, "Content-Range": f"bytes */{total}"})

    if start_text:
        start = int(start_text)
        end = int(end_text) if end_text else total - 1
    else:
        suffix_length = int(end_text)
        if suffix_length <= 0:
            return Response(status_code=416, headers={**base_headers, "Content-Range": f"bytes */{total}"})
        start = max(total - suffix_length, 0)
        end = total - 1

    if start >= total or end < start:
        return Response(status_code=416, headers={**base_headers, "Content-Range": f"bytes */{total}"})
    end = min(end, total - 1)
    body = data[start : end + 1]
    return Response(
        body,
        status_code=206,
        media_type=media_type,
        headers={
            **base_headers,
            "Content-Range": f"bytes {start}-{end}/{total}",
            "Content-Length": str(len(body)),
        },
    )


class FindingPatch(BaseModel):
    state: FindingState


class IdentifierRequest(BaseModel):
    recipe_id: str = Field(min_length=1, max_length=128)


class AuditRequest(BaseModel):
    voice_index: int = Field(ge=0, le=65535)
    line_ids: list[str] | None = Field(default=None, max_length=512)


class DeployRequest(BaseModel):
    plan_id: str = Field(min_length=1, max_length=128)


class TranscriptionRequest(BaseModel):
    asset_ids: list[str] = Field(default_factory=list, max_length=512)

class RecipeDraftRequest(BaseModel):
    input_asset_id: str; input_sha256: str; voice_index: int; family: Literal["speech", "battle"]; line_id: str; output_extension: Literal[".ogg"]
    operations: list[dict[str, Any]] = Field(default_factory=list); subtitle: str | None = None; replacement_source: dict[str, str] | None = None


def _workspace() -> Path | None:
    configured = get_state().get_settings().get("voice_authoring_workspace")
    if not configured:
        return None
    return ensure_workspace_is_external(
        Path(configured), (install.path for install in get_state().list_installs()),
    )


def _service(install_id: str | None = None) -> VoiceLabService:
    info = _resolve_install(install_id)
    try:
        toolchain: Any = _audio_toolchain()
    except RuntimeError:
        toolchain = _UnavailableAudioToolchain()
    return VoiceLabService.for_install(
        info.id, info.path, state=get_state(), workspace=_workspace(), audio_toolchain=toolchain,
    )


def _audio_toolchain() -> AudioToolchain:
    return AudioToolchain.resolve(get_state().get_settings())


class _UnavailableAudioToolchain:
    """Allows read-only store access before optional FFmpeg is configured."""

    def __getattr__(self, name: str) -> Any:
        raise RuntimeError("Voice Lab audio tools are not configured")


def _public(value: Any) -> Any:
    """Drop local locators from every public DTO, including nested evidence."""
    if hasattr(value, "model_dump"):
        value = value.model_dump(mode="json")
    if isinstance(value, dict):
        return {
            key: _public(item)
            for key, item in value.items()
            if key not in {"source_locator", "preview_asset_path", "staged_path", "manifest_path"}
            and not key.endswith("_absolute_path")
        }
    if isinstance(value, (list, tuple)):
        return [_public(item) for item in value]
    if isinstance(value, Path):
        return None
    if isinstance(value, str) and (Path(value).is_absolute() or re.match(r"^[A-Za-z]:[\\/]", value)):
        return "[redacted]"
    return value


def _not_found(kind: str) -> HTTPException:
    return HTTPException(status_code=404, detail={"error": f"{kind}_NOT_FOUND", "message": "Requested Voice Lab resource was not found"})


def _snapshot(install_id: str) -> InventorySnapshot | None:
    with _snapshots_lock:
        return _snapshots.get(install_id)


def _remember_snapshot(snapshot: InventorySnapshot, *, replace: bool = True) -> None:
    with _snapshots_lock:
        if replace or snapshot.install_id not in _snapshots:
            _snapshots[snapshot.install_id] = snapshot
            return
        current = _snapshots[snapshot.install_id]
        banks = {bank.voice_index: bank for bank in current.banks}
        banks.update({bank.voice_index: bank for bank in snapshot.banks})
        _snapshots[snapshot.install_id] = InventorySnapshot(
            install_id=snapshot.install_id,
            banks=[banks[index] for index in sorted(banks)],
        )


def _scan_work(install_id: str, voice_index: int | None = None):
    def work(progress, cancelled):
        if cancelled():
            return None
        service = _service(install_id)
        try:
            progress(0, 0, "scanning Voice Lab inventory")
            snapshot = service.scan(
                progress=progress,
                cancelled=cancelled,
                voice_indexes=None if voice_index is None else {voice_index},
            )
            if snapshot is None:
                return None
            if cancelled():
                return None
            _remember_snapshot(snapshot, replace=voice_index is None)
            progress(1, 1, "inventory ready")
            return snapshot
        finally:
            service.close()
    return work


def _audit_work(install_id: str, snapshot: InventorySnapshot, request: AuditRequest):
    def work(progress, cancelled):
        if cancelled():
            return None
        service = _service(install_id)
        try:
            return service.audit_snapshot(
                snapshot, request.voice_index, line_ids=request.line_ids,
                progress=progress, cancelled=cancelled,
            )
        finally:
            service.close()
    return work


def _analysis_lines(snapshot: InventorySnapshot, request: AuditRequest):
    """Return auditable lines in the caller's requested review order."""
    bank = snapshot.bank(request.voice_index)
    if request.line_ids is None:
        return [
            line for line in bank.lines
            if line.family in {"speech", "battle"}
        ]
    by_id = {
        line.line_id: line for line in bank.lines
        if line.family in {"speech", "battle"}
    }
    return [by_id[line_id] for line_id in request.line_ids if line_id in by_id]


def _analysis_work(install_id: str, snapshot: InventorySnapshot, request: AuditRequest):
    """Fill missing transcript cache entries before publishing one complete audit."""
    def work(progress, cancelled):
        if cancelled():
            return None
        service = _service(install_id)
        try:
            lines = _analysis_lines(snapshot, request)
            missing = [
                line.audio_winner for line in lines
                if line.audio_winner is not None
                and service.store.transcription(
                    line.audio_winner.asset_id, line.audio_winner.sha256,
                ) is None
            ]
            total = len(missing) + len(lines)
            if missing:
                transcriber = Transcriber.from_settings(get_state().get_settings())
                for completed, asset in enumerate(missing, start=1):
                    if cancelled():
                        return None
                    transcript = transcriber.transcribe(
                        asset, service._stage_readable_asset(asset),
                    )
                    service.store.save_transcription(transcript)
                    progress(completed, total, "Transcribing missing lines")
                    if cancelled():
                        return None
            if cancelled():
                return None

            def audit_progress(completed: int, _audit_total: int, _message: str) -> None:
                progress(
                    len(missing) + completed,
                    total,
                    "Checking audio and subtitles",
                )

            return service.audit_snapshot(
                snapshot,
                request.voice_index,
                line_ids=request.line_ids,
                progress=audit_progress,
                cancelled=cancelled,
            )
        finally:
            service.close()
    return work


@router.get("/voice-lab/status")
def voice_lab_status(install_id: str | None = Query(default=None)) -> dict[str, Any]:
    info = _resolve_install(install_id)
    settings = get_state().get_settings()
    service = _service(install_id)
    try:
        deployment = service.deployment_status().model_dump(mode="json")
    finally:
        service.close()
    return {
        "install_id": info.id,
        "scan_workers": _jobs.scan_worker_count,
        "transcription_workers": _jobs.transcription_worker_count,
        "authoring_workspace_configured": bool(settings.get("voice_authoring_workspace")),
        "ffmpeg_configured": bool(settings.get("voice_ffmpeg_path")),
        "transcriber_configured": bool(settings.get("voice_transcriber_python")),
        "has_snapshot": _snapshot(info.id) is not None,
        "deployment": deployment,
    }


def _deployment_error(error: Exception, fallback_code: str, fallback_message: str) -> HTTPException:
    """Keep stable writer failures at the HTTP boundary without leaking paths."""
    if isinstance(error, VoiceLabOperationError):
        return HTTPException(status_code=409, detail={"error": error.code, "message": error.message})
    if str(error).startswith("STALE_PLAN:"):
        return HTTPException(
            status_code=409,
            detail={"error": "VOICE_SOURCE_CHANGED", "message": str(error).split(":", 1)[1].strip()},
        )
    code, separator, message = str(error).partition(":")
    if separator and code in {"VOICE_SOURCE_CHANGED", "VOICE_TOOLCHAIN_UNAVAILABLE", "VOICE_RECOVERY_REQUIRED", "VOICE_GAME_RUNNING"}:
        return HTTPException(status_code=409, detail={"error": code, "message": message.strip()})
    return HTTPException(status_code=422, detail={"error": fallback_code, "message": fallback_message})


@router.post("/voice-lab/scans", status_code=201)
def start_scan(
    install_id: str | None = Query(default=None),
    voice_index: int | None = Query(default=None, ge=0, le=65535),
) -> dict[str, Any]:
    info = _resolve_install(install_id)
    return _jobs.submit_scan(
        _scan_work(info.id, voice_index), kind="scan",
        key=("scan", os.path.normcase(str(info.path.resolve())), voice_index),
    ).public()


@router.post("/voice-lab/audits", status_code=201)
def start_audit(request: AuditRequest, install_id: str | None = Query(default=None)) -> dict[str, Any]:
    info = _resolve_install(install_id)
    snapshot = _snapshot(info.id)
    if snapshot is None:
        raise HTTPException(status_code=409, detail={
            "error": "VOICE_INVENTORY_REQUIRED",
            "message": "Run the Voice Lab inventory scan before auditing a bank.",
        })
    try:
        bank = snapshot.bank(request.voice_index)
    except KeyError:
        raise _not_found("BANK")
    selected_ids = set(request.line_ids) if request.line_ids is not None else None
    total = sum(
        1 for line in bank.lines
        if line.family in {"speech", "battle"}
        and (selected_ids is None or line.line_id in selected_ids)
    )
    return _jobs.submit_scan(
        _audit_work(info.id, snapshot, request), total=total, kind="audit",
    ).public()


@router.post("/voice-lab/analyses", status_code=201)
def start_analysis(request: AuditRequest, install_id: str | None = Query(default=None)) -> dict[str, Any]:
    """Transcribe uncached clips, then audit one already-indexed voice set."""
    info = _resolve_install(install_id)
    snapshot = _snapshot(info.id)
    if snapshot is None:
        raise HTTPException(status_code=409, detail={
            "error": "VOICE_INVENTORY_REQUIRED",
            "message": "Index the game voice files before analyzing a merc.",
        })
    try:
        lines = _analysis_lines(snapshot, request)
    except KeyError:
        raise _not_found("BANK")
    service = _service(info.id)
    try:
        missing_count = sum(
            service.store.transcription(line.audio_winner.asset_id, line.audio_winner.sha256) is None
            for line in lines if line.audio_winner is not None
        )
    finally:
        service.close()
    return _jobs.submit_analysis(
        _analysis_work(info.id, snapshot, request),
        total=missing_count + len(lines),
    ).public()


@router.get("/voice-lab/jobs/{job_id}")
def get_job(job_id: str) -> dict[str, Any]:
    try:
        return _jobs.get(job_id).public()
    except KeyError:
        raise _not_found("JOB")


@router.post("/voice-lab/jobs/{job_id}/cancel")
def cancel_job(job_id: str) -> dict[str, Any]:
    try:
        return _jobs.cancel(job_id).public()
    except KeyError:
        raise _not_found("JOB")


@router.get("/voice-lab/banks")
def list_banks(install_id: str | None = Query(default=None)) -> list[dict[str, Any]]:
    info = _resolve_install(install_id)
    snapshot = _snapshot(info.id)
    return [] if snapshot is None else _public(snapshot.banks)


@router.get("/voice-lab/catalog")
def list_bank_catalog(install_id: str | None = Query(default=None)) -> list[dict[str, Any]]:
    """Publish merc names immediately without indexing voice files."""
    service = _service(install_id)
    try:
        return _public(service.profile_catalog().banks)
    finally:
        service.close()


@router.get("/voice-lab/lines")
def list_lines(
    voice_index: int, install_id: str | None = Query(default=None),
) -> list[dict[str, Any]]:
    info = _resolve_install(install_id)
    snapshot = _snapshot(info.id)
    if snapshot is None:
        return []
    try:
        return _public(snapshot.bank(voice_index).lines)
    except KeyError:
        raise _not_found("BANK")

@router.get("/voice-lab/lines/{voice_index}/{family}/{line_id}")
def line_detail(voice_index: int, family: Literal["speech", "battle", "dialogue_edt"], line_id: str, install_id: str | None = Query(default=None)) -> dict[str, Any]:
    service = _service(install_id)
    try:
        snapshot = _snapshot(_resolve_install(install_id).id)
        if snapshot is None: raise _not_found("LINE")
        return _public(service.line_detail(snapshot, voice_index, family, line_id))
    except KeyError: raise _not_found("LINE")
    finally: service.close()


@router.get("/voice-lab/findings")
def list_findings(install_id: str | None = Query(default=None)) -> list[dict[str, Any]]:
    service = _service(install_id)
    try:
        return _public(service.store.findings())
    finally:
        service.close()


@router.patch("/voice-lab/findings/{stable_key}")
def patch_finding(stable_key: str, patch: FindingPatch, install_id: str | None = Query(default=None)) -> dict[str, Any]:
    service = _service(install_id)
    try:
        return _public(service.store.set_finding_state(stable_key, patch.state))
    except KeyError:
        raise _not_found("FINDING")
    finally:
        service.close()


@router.get("/voice-lab/assets/{asset_id}/audio")
def asset_audio(asset_id: str, request: Request, install_id: str | None = Query(default=None)) -> Response:
    service = _service(install_id)
    try:
        asset = service.store.asset(asset_id)
        return _audio_response(
            service.read_asset_bytes(asset),
            _AUDIO_EXTENSIONS.get(asset.extension.lower(), "application/octet-stream"),
            request.headers.get("range"),
        )
    except (KeyError, OSError, ValueError):
        raise _not_found("ASSET")
    finally:
        service.close()


@router.get("/voice-lab/assets/{asset_id}/waveform")
def asset_waveform(asset_id: str, install_id: str | None = Query(default=None), buckets: int = Query(default=128, ge=1, le=2048)) -> dict[str, Any]:
    service = _service(install_id)
    try:
        asset = service.store.asset(asset_id)
        waveform = _audio_toolchain().waveform(service._stage_readable_asset(asset), buckets=buckets)
        return {"asset_id": asset.asset_id, "duration_ms": waveform.duration_ms, "peaks": list(waveform.peaks)}
    except KeyError:
        raise _not_found("ASSET")
    except (OSError, RuntimeError, ValueError):
        raise HTTPException(status_code=422, detail={"error": "WAVEFORM_UNAVAILABLE", "message": "Waveform analysis was unavailable"})
    finally:
        service.close()


def _import_root() -> Path:
    workspace = _workspace()
    if workspace is not None:
        return workspace / "voice_lab" / "imports"
    appdata = os.environ.get("APPDATA")
    base = Path(appdata) / "MercWizard" if appdata else Path.home() / ".config" / "MercWizard"
    return base / "voice_lab" / "imports"


@router.post("/voice-lab/import-source", status_code=201)
async def import_source(file: UploadFile = File(...), install_id: str | None = Query(default=None)) -> dict[str, Any]:
    original = file.filename or "source.ogg"
    suffix = Path(original).suffix.lower()
    if suffix not in _AUDIO_EXTENSIONS:
        raise HTTPException(status_code=422, detail={"error": "UNSUPPORTED_AUDIO", "message": "Voice Lab imports require OGG, WAV, or MP3 audio"})
    data = await file.read()
    if not data:
        raise HTTPException(status_code=422, detail={"error": "EMPTY_AUDIO", "message": "Voice Lab import is empty"})
    root = _import_root()
    root.mkdir(parents=True, exist_ok=True)
    safe_stem = _SAFE_UPLOAD_NAME.sub("-", Path(original).stem).strip(".-") or "source"
    digest = sha256(data).hexdigest()
    target = root / f"{digest}-{safe_stem[:64]}{suffix}"
    staged_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(dir=root, prefix=".validate-", suffix=suffix, delete=False) as staged:
            staged.write(data)
            staged.flush()
            os.fsync(staged.fileno())
            staged_path = Path(staged.name)
        probe = _audio_toolchain().probe(staged_path)
        if probe.duration_ms <= 0:
            raise ValueError("no duration")
        # Final authoring artifact is atomically replaced only after ffprobe
        # accepts the exact hashed bytes.
        write_bytes_atomic(target, data)
    except (OSError, RuntimeError, ValueError):
        raise HTTPException(status_code=422, detail={"error": "INVALID_AUDIO", "message": "Voice Lab could not validate the uploaded audio"})
    finally:
        if staged_path is not None:
            try:
                staged_path.unlink()
            except OSError:
                pass
    service = _service(install_id)
    try:
        asset = service.register_imported_asset(
            asset_id=f"import-{digest}", extension=suffix, source_locator=target,
            size_bytes=len(data), sha256=digest, duration_ms=probe.duration_ms,
        )
        return _public(asset)
    finally:
        service.close()


@router.post("/voice-lab/transcriptions", status_code=201)
def start_transcriptions(request: TranscriptionRequest, install_id: str | None = Query(default=None)) -> dict[str, Any]:
    info = _resolve_install(install_id)
    asset_ids = list(dict.fromkeys(request.asset_ids))
    validation_service = _service(info.id)
    try:
        for asset_id in asset_ids:
            validation_service.store.asset(asset_id)
    except KeyError:
        raise _not_found("ASSET")
    finally:
        validation_service.close()

    def work(progress, cancelled):
        service = _service(info.id)
        try:
            transcriber = Transcriber.from_settings(get_state().get_settings())
            for index, asset_id in enumerate(asset_ids, start=1):
                if cancelled():
                    return None
                asset = service.store.asset(asset_id)
                transcript = transcriber.transcribe(asset, service._stage_readable_asset(asset))
                service.store.save_transcription(transcript)
                progress(index, len(asset_ids), "transcribing audio")
            return None
        finally:
            service.close()
    return _jobs.submit_transcription(work, total=len(asset_ids)).public()

@router.get("/voice-lab/transcriptions")
def get_transcription(asset_id: str, install_id: str | None = Query(default=None)) -> dict[str, Any] | None:
    service = _service(install_id)
    try:
        asset = service.store.asset(asset_id); transcript = service.store.transcription(asset.asset_id, asset.sha256)
        return None if transcript is None else _public(transcript)
    except KeyError: raise _not_found("ASSET")
    finally: service.close()


@router.get("/voice-lab/recipes")
def list_recipes(install_id: str | None = Query(default=None)) -> list[dict[str, Any]]:
    service = _service(install_id)
    try:
        return [_public(recipe) for recipe in service.list_recipes()]
    finally:
        service.close()


@router.post("/voice-lab/recipes", status_code=201)
def save_recipe(recipe: RecipeDraftRequest, install_id: str | None = Query(default=None)) -> dict[str, Any]:
    service = _service(install_id)
    try:
        return _public(service.create_recipe_from_draft(recipe.model_dump()))
    except HTTPException:
        raise
    except (OSError, ValueError):
        raise HTTPException(status_code=422, detail={"error": "INVALID_RECIPE", "message": "Voice Lab recipe is invalid"})
    finally:
        service.close()


@router.post("/voice-lab/recipes/{recipe_id}/preview", status_code=201)
def render_preview(recipe_id: str, install_id: str | None = Query(default=None)) -> dict[str, Any]:
    info = _resolve_install(install_id)
    generation_id = uuid4().hex
    preview_asset_id = f"preview-{recipe_id}-{generation_id}"
    generation_key = (info.id, recipe_id)
    with _preview_generations_lock:
        _preview_generations[generation_key] = generation_id
    def work(progress, cancelled):
        service = _service(info.id)
        try:
            recipe = service.store.recipe(recipe_id)
            asset = service.store.asset(recipe.input_asset_id)
            if cancelled():
                return None
            root = _import_root().parent / "previews"
            target = root / f"{recipe.recipe_id}-{generation_id}.ogg"
            result = _audio_toolchain().render_recipe(service._stage_readable_asset(asset), recipe, target)
            if cancelled():
                return None
            updated = recipe.model_copy(update={
                "preview_asset_path": str(result.output_path), "preview_sha256": result.encoded_sha256,
                "preview_pcm_sha256": result.decoded_pcm_sha256, "preview_ffmpeg_version": result.ffmpeg_version,
                "preview_recipe_sha256": result.recipe_sha256, "preview_source_sha256": result.source_sha256,
                "preview_gap_present": False, "updated_utc": recipe.updated_utc,
            })
            # Each request publishes its own opaque asset, while only the
            # latest request may advance the recipe's deployable preview.
            # A slow older render therefore cannot overwrite a newer review.
            with _preview_generations_lock:
                is_latest = _preview_generations.get(generation_key) == generation_id
            if is_latest:
                service.save_recipe(updated)
            service.register_preview_asset(
                asset_id=preview_asset_id, recipe_id=recipe.recipe_id,
                source_locator=result.output_path, size_bytes=result.output_path.stat().st_size,
                sha256=result.encoded_sha256, duration_ms=result.duration_ms,
            )
            progress(1, 1, "preview rendered")
            return updated
        finally:
            service.close()
            with _preview_generations_lock:
                if _preview_generations.get(generation_key) == generation_id:
                    _preview_generations.pop(generation_key, None)
    return _jobs.submit_scan(work, total=1, kind="preview", preview_asset_id=preview_asset_id).public()


@router.post("/voice-lab/deploy/preflight")
def deploy_preflight(request: IdentifierRequest, install_id: str | None = Query(default=None)) -> dict[str, Any]:
    service = _service(install_id)
    try:
        plan = service.preflight(request.recipe_id)
        with _plans_lock:
            _plans[plan.plan_id] = (_resolve_install(install_id).id, plan)
        return _public(plan)
    except KeyError:
        raise _not_found("RECIPE")
    except (OSError, RuntimeError, ValueError) as error:
        raise _deployment_error(error, "PREFLIGHT_FAILED", "Voice Lab deployment preflight failed")
    finally:
        service.close()


@router.post("/voice-lab/deploy")
def deploy(request: DeployRequest, install_id: str | None = Query(default=None)) -> dict[str, Any]:
    service = _service(install_id)
    try:
        with _plans_lock:
            saved = _plans.get(request.plan_id)
        if saved is None or saved[0] != _resolve_install(install_id).id:
            raise KeyError(request.plan_id)
        # Plans are intentionally revalidated by VoiceLabService.deploy under
        # its writer locks.  The route only carries its opaque ID across two
        # otherwise stateless HTTP requests.
        service.handoff_preflight_plan(saved[1])
        return _public(service.deploy(request.plan_id))
    except KeyError:
        raise _not_found("DEPLOY_PLAN")
    except (OSError, RuntimeError, ValueError) as error:
        raise _deployment_error(error, "DEPLOY_FAILED", "Voice Lab deployment failed")
    finally:
        service.close()
        with _plans_lock:
            _plans.pop(request.plan_id, None)


@router.get("/voice-lab/deployments")
def deployment_history(install_id: str | None = Query(default=None)) -> list[dict[str, Any]]:
    service = _service(install_id)
    try:
        return _public(service.deployment_history())
    finally:
        service.close()


@router.post("/voice-lab/deployments/{deployment_id}/undo")
def undo_deployment(deployment_id: str, install_id: str | None = Query(default=None)) -> dict[str, Any]:
    service = _service(install_id)
    try:
        return _public(service.undo(deployment_id))
    except KeyError:
        raise _not_found("DEPLOYMENT")
    except (OSError, RuntimeError, ValueError) as error:
        raise _deployment_error(error, "UNDO_FAILED", "Voice Lab deployment undo failed")
    finally:
        service.close()
