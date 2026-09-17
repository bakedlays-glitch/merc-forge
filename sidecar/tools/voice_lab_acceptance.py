"""Read-only Voice Lab acceptance evidence for a chosen game install.

The tool deliberately has no deploy or Undo mode.  Its state database,
temporary transcriber inputs, and JSON report are all kept outside the target
install so it can serve as a release gate against the canonical Copy.
"""
from __future__ import annotations

import os
import argparse
from hashlib import sha256
import json
from pathlib import Path
import tempfile
from typing import Any, Iterable

from mercwizard_core.voice_lab.audio import AudioToolchain
from mercwizard_core.voice_lab.audit import AudioMetrics, AuditLine, run_content_audit
from mercwizard_core.voice_lab.models import Finding, VoiceAsset, VoiceBank, VoiceProfile
from mercwizard_core.voice_lab.service import VoiceLabService
from mercwizard_core.voice_lab.transcriber import Transcriber


def _winget_ffmpeg() -> Path | None:
    """Newest winget-installed ffmpeg.exe (survives version bumps — winget keeps
    only the current build, so the pinned 8.1 path rotted on the 9.0 upgrade)."""
    base = (
        Path.home() / "AppData/Local/Microsoft/WinGet/Packages"
        / "Gyan.FFmpeg_Microsoft.Winget.Source_8wekyb3d8bbwe"
    )
    builds = sorted(base.glob("ffmpeg-*-full_build/bin/ffmpeg.exe"))
    return builds[-1] if builds else None


DEFAULT_FFMPEG = _winget_ffmpeg()
DEFAULT_TRANSCRIBER = Path(os.environ.get("MW2_TRANSCRIBER_PYTHON", ""))
DEFAULT_RELEVANT_LINE_IDS = ("111", "112")


def collect_read_only_report(
    install_root: Path | str,
    *,
    king_profile: int,
    tycho_name: str,
    transcriber: Any,
    audio_toolchain: Any,
    state_root: Path | str,
    relevant_line_ids: Iterable[str] = DEFAULT_RELEVANT_LINE_IDS,
    require_transcriber: bool = True,
) -> dict[str, Any]:
    """Collect audit evidence without calling a writer or touching the install.

    The caller supplies the exact local audio and transcription capabilities;
    there is intentionally no ambient PATH or model-download fallback.
    """
    root = Path(install_root).resolve()
    state = Path(state_root).resolve()
    _require_external_state_root(root, state)
    state.mkdir(parents=True, exist_ok=True)
    requested_lines = tuple(dict.fromkeys(str(item) for item in relevant_line_ids))
    service = VoiceLabService.for_install(
        "voice-lab-read-only-acceptance", root, state / "voice_lab.sqlite3",
        workspace=state / "authoring", audio_toolchain=audio_toolchain,
    )
    try:
        snapshot = service.scan()
        king_bank, king = _profile_bank(snapshot.banks, king_profile)
        tycho_bank, tycho = _named_profile_bank(snapshot.banks, tycho_name)
        before = _install_evidence(root, service, (king_bank, tycho_bank))
        capability = transcriber.capability()
        capability_json = _model_json(capability)
        if require_transcriber and capability_json.get("available") is not True:
            reason = capability_json.get("reason") or "unknown reason"
            raise RuntimeError(f"required Voice Lab transcription is unavailable: {reason}")
        transcripts = _transcribe_requested_lines(
            service, king_bank, requested_lines, transcriber, capability_json,
            state / "transcriber-inputs",
        )
        findings = _audit_requested_lines(
            service, snapshot, king_bank, requested_lines, audio_toolchain,
            state / "audit-inputs",
        )
        after = _install_evidence(root, service, (king_bank, tycho_bank))
        return {
            "mode": "read_only",
            "writes_performed": 0,
            "transcription": capability_json,
            "audio_toolchain": _audio_capability(audio_toolchain),
            "king": {
                "profile": _profile_json(king),
                "bank": _bank_json(king_bank),
                "relevant_lines": _relevant_line_evidence(service, snapshot, king_bank, requested_lines),
                "transcripts": transcripts,
                "findings": [_finding_json(item) for item in findings],
            },
            "tycho": {
                "profile": _profile_json(tycho),
                "bank": _bank_json(tycho_bank),
            },
            "install_evidence": {"before": before, "after": after, "unchanged": before == after},
        }
    finally:
        service.close()


def _transcribe_requested_lines(
    service: VoiceLabService,
    bank: VoiceBank,
    line_ids: tuple[str, ...],
    transcriber: Any,
    capability: dict[str, Any],
    stage_root: Path,
) -> list[dict[str, Any]]:
    if capability.get("available") is not True:
        return []
    stage_root.mkdir(parents=True, exist_ok=True)
    staged: list[dict[str, Any]] = []
    for line in bank.lines:
        if line.family != "speech" or line.line_id not in line_ids or line.audio_winner is None:
            continue
        asset = line.audio_winner
        # The transcriber receives an external temporary copy so even an SLF
        # source never causes a sidecar staging write below the game root.
        with tempfile.NamedTemporaryFile(dir=stage_root, prefix="line-", suffix=asset.extension, delete=False) as handle:
            staged_path = Path(handle.name)
            handle.write(service.read_asset_bytes(asset))
        try:
            transcription = transcriber.transcribe(asset, staged_path)
            service.store.save_transcription(transcription)
        finally:
            try:
                staged_path.unlink()
            except FileNotFoundError:
                pass
        staged.append({
            "line_id": line.line_id,
            "asset_id": asset.asset_id,
            "source_sha256": asset.sha256,
            "text": transcription.text,
            "confidence": transcription.confidence,
            "model_id": transcription.model_id,
            "model_version": transcription.model_version,
        })
    return staged


def _relevant_line_evidence(
    service: VoiceLabService,
    snapshot: Any,
    bank: VoiceBank,
    line_ids: tuple[str, ...],
) -> list[dict[str, Any]]:
    evidence: list[dict[str, Any]] = []
    for line_id in line_ids:
        try:
            detail = service.line_detail(snapshot, bank.voice_index, "speech", line_id)
        except KeyError:
            continue
        line = detail["line"]
        audio = line.audio_winner
        evidence.append({
            "line_id": line_id,
            "audio_asset_id": None if audio is None else audio.asset_id,
            "audio_sha256": None if audio is None else audio.sha256,
            "subtitle": detail["subtitle"],
            "transcription": None if detail["transcription"] is None else _model_json(detail["transcription"]),
        })
    return evidence


def _audit_requested_lines(
    service: VoiceLabService,
    snapshot: Any,
    bank: VoiceBank,
    line_ids: tuple[str, ...],
    audio_toolchain: Any,
    stage_root: Path,
) -> list[Finding]:
    """Run the ordinary content audit on requested evidence lines only.

    A release acceptance needs evidence for the King telephone sequence, not
    an hours-long full-roster decode.  Selection happens outside the audit;
    the generic audit receives the same `AuditLine` structure used by a full
    service scan and contains no profile/name special case.
    """
    stage_root.mkdir(parents=True, exist_ok=True)
    audit_lines: list[AuditLine] = []
    edt = next((line.dialogue_edt for line in bank.lines if line.family == "dialogue_edt"), None)
    speech_lines = {
        line.line_id: line
        for line in bank.lines
        if line.family == "speech" and line.audio_winner is not None
    }
    for line_id in line_ids:
        line = speech_lines.get(line_id)
        if line is None:
            continue
        asset = line.audio_winner
        with tempfile.NamedTemporaryFile(dir=stage_root, prefix="audit-", suffix=asset.extension, delete=False) as handle:
            staged_path = Path(handle.name)
            handle.write(service.read_asset_bytes(asset))
        try:
            decoded = audio_toolchain.decoded_metrics(staged_path)
        finally:
            try:
                staged_path.unlink()
            except FileNotFoundError:
                pass
        detail = service.line_detail(snapshot, bank.voice_index, "speech", line.line_id)
        transcript = service.store.transcription(asset.asset_id, asset.sha256)
        audit_lines.append(AuditLine(
            voice_index=bank.voice_index, family="speech", line_id=line.line_id, audio=asset,
            subtitle=detail["subtitle"], transcription=transcript,
            metrics=AudioMetrics(
                duration_ms=decoded.duration_ms, pcm_sha256=decoded.pcm_sha256,
                sample_rate=decoded.sample_rate, channels=decoded.channels,
                leading_silence_ms=decoded.leading_silence_ms, trailing_silence_ms=decoded.trailing_silence_ms,
                peak_dbfs=decoded.peak_dbfs, rms_dbfs=decoded.rms_dbfs,
            ), dialogue=edt, gap=line.gap_winner,
        ))
    return run_content_audit(audit_lines, preserve_context_order=True)


def _profile_bank(banks: Iterable[VoiceBank], profile_id: int) -> tuple[VoiceBank, VoiceProfile]:
    for bank in banks:
        for profile in bank.profiles:
            if profile.profile_id == profile_id:
                return bank, profile
    raise LookupError(f"profile {profile_id} has no browsable Voice Lab bank")


def _named_profile_bank(banks: Iterable[VoiceBank], name: str) -> tuple[VoiceBank, VoiceProfile]:
    target = name.casefold().strip()
    for bank in banks:
        for profile in bank.profiles:
            if target in {profile.name.casefold(), profile.nickname.casefold()}:
                return bank, profile
    raise LookupError(f"profile named {name!r} has no browsable Voice Lab bank")


def _bank_json(bank: VoiceBank) -> dict[str, Any]:
    return {
        "voice_index": bank.voice_index,
        "profiles": [_profile_json(profile) for profile in bank.profiles],
        "line_count": len(bank.lines),
    }


def _profile_json(profile: VoiceProfile) -> dict[str, Any]:
    return {
        "profile_id": profile.profile_id,
        "profile_type": profile.profile_type,
        "name": profile.name,
        "nickname": profile.nickname,
        "voice_index": profile.voice_index,
    }


def _finding_json(finding: Finding) -> dict[str, Any]:
    value = finding.model_dump(mode="json")
    return _strip_locators(value)


def _model_json(value: Any) -> dict[str, Any]:
    if hasattr(value, "model_dump"):
        return dict(value.model_dump(mode="json"))
    return {
        key: getattr(value, key)
        for key in ("available", "reason", "model_id", "model_version")
        if hasattr(value, key)
    }


def _audio_capability(audio_toolchain: Any) -> dict[str, Any]:
    version = audio_toolchain.version() if hasattr(audio_toolchain, "version") else None
    return {"available": True, "ffmpeg_version": version}


def _install_evidence(root: Path, service: VoiceLabService, banks: tuple[Any, ...]) -> dict[str, Any]:
    """Hash the actual Release 1 read surface without hashing unrelated art.

    The physical asset bytes are reread for both the before and after evidence.
    This verifies that a change made while the acceptance process is running is
    visible even though the inventory metadata came from its initial scan.
    """
    assets: dict[str, dict[str, Any]] = {}
    slf_archives: dict[str, dict[str, Any]] = {}
    for bank in banks:
        for line in bank.lines:
            candidates = [*line.audio_variants, *line.gap_variants, *line.dialogue_variants]
            for asset in candidates:
                if asset.asset_id in assets:
                    continue
                archive: Path | None = None
                archive_identity: str | None = None
                if asset.source_kind == "slf":
                    # Validate the physical container before reading its member.
                    # Read-only acceptance must never follow an inventory locator
                    # outside the selected install.
                    archive, archive_identity = _slf_archive_for_evidence(root, asset)
                current_bytes = service.read_asset_bytes(asset)
                assets[asset.asset_id] = {
                    "family": asset.family,
                    "voice_index": asset.voice_index,
                    "line_id": asset.line_id,
                    "source_kind": asset.source_kind,
                    "sha256": sha256(current_bytes).hexdigest(),
                    "size_bytes": len(current_bytes),
                }
                if archive is not None and archive_identity is not None:
                    if archive_identity not in slf_archives:
                        slf_archives[archive_identity] = {
                            "sha256": _hash_file(archive),
                            "size_bytes": archive.stat().st_size,
                        }
    profile_files: dict[str, dict[str, Any]] = {}
    for path in sorted(root.glob("Data*/TableData/MercProfiles.xml"), key=lambda item: str(item).lower()):
        relative = path.relative_to(root).as_posix()
        profile_files[relative] = {"sha256": _hash_file(path), "size_bytes": path.stat().st_size}
    return {
        "profiles": profile_files,
        "voice_assets": assets,
        "slf_archives": slf_archives,
    }


def _slf_archive_for_evidence(root: Path, asset: VoiceAsset) -> tuple[Path, str]:
    """Resolve an in-install archive and return its stable report key."""
    try:
        locator = json.loads(asset.source_locator)
        archive_value = locator["archive"]
    except (TypeError, ValueError, KeyError) as exc:
        raise ValueError(f"invalid SLF source locator for {asset.asset_id}") from exc
    if not isinstance(archive_value, str):
        raise ValueError(f"invalid SLF archive locator for {asset.asset_id}")
    try:
        archive = Path(archive_value).resolve(strict=True)
    except OSError as exc:
        raise ValueError(f"unreadable SLF archive for {asset.asset_id}") from exc
    if not archive.is_file():
        raise ValueError(f"SLF archive is not a file for {asset.asset_id}")
    try:
        identity = archive.relative_to(root.resolve(strict=True)).as_posix()
    except ValueError:
        raise ValueError(f"SLF archive resolves outside the supplied install for {asset.asset_id}")
    return archive, identity


def _hash_file(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as source:
        while block := source.read(1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


def _strip_locators(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _strip_locators(item) for key, item in value.items() if key != "source_locator"}
    if isinstance(value, list):
        return [_strip_locators(item) for item in value]
    return value


def _require_external_state_root(install_root: Path, state_root: Path) -> None:
    try:
        state_root.relative_to(install_root)
    except ValueError:
        return
    raise ValueError("read-only acceptance state must be outside the supplied install")


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Read-only Merc Forge Voice Lab acceptance evidence")
    parser.add_argument("--install", required=True, type=Path)
    parser.add_argument("--king-profile", required=True, type=int)
    parser.add_argument("--tycho-name", required=True)
    parser.add_argument("--read-only", action="store_true", help="Required: never deploy or Undo the target install")
    parser.add_argument("--require-transcriber", action="store_true")
    parser.add_argument("--transcriber-python", type=Path, default=DEFAULT_TRANSCRIBER)
    parser.add_argument("--ffmpeg", type=Path, default=DEFAULT_FFMPEG)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--state-root", type=Path)
    parser.add_argument("--line-id", action="append", dest="line_ids")
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    if not args.read_only:
        raise SystemExit("--read-only is required; this acceptance tool has no writer mode")
    install = args.install.resolve()
    output = args.output.resolve()
    _require_external_state_root(install, output.parent)
    state_root = (args.state_root or output.parent / "voice_lab_acceptance_state").resolve()
    _require_external_state_root(install, state_root)
    toolchain = AudioToolchain.resolve({"voice_ffmpeg_path": str(args.ffmpeg)})
    report = collect_read_only_report(
        install, king_profile=args.king_profile, tycho_name=args.tycho_name,
        transcriber=Transcriber(args.transcriber_python), audio_toolchain=toolchain,
        state_root=state_root, relevant_line_ids=args.line_ids or DEFAULT_RELEVANT_LINE_IDS,
        require_transcriber=args.require_transcriber,
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
