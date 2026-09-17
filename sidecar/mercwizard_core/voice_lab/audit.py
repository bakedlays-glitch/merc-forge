"""Deterministic and transcript-assisted Voice Lab audit findings."""
from __future__ import annotations

from dataclasses import dataclass, field
from difflib import SequenceMatcher
from hashlib import sha256
import json
from typing import Iterable, Literal

from .models import Finding, Transcription, VoiceAsset, VoiceProfile


_SILENCE_LIMIT_MS = 500
_NEAR_SILENCE_DBFS = -80.0
_EXTREME_DURATION_MS = 120_000
_EXPECTED_SAMPLE_RATE = 22_050
_EXPECTED_CHANNELS = 1
_SUBTITLE_RATIO = 0.65
_ADJACENT_RATIO = 0.72
_WORDS_PER_SECOND = 2.8
_DURATION_FACTOR = 2.2


@dataclass(frozen=True)
class AudioMetrics:
    duration_ms: int
    pcm_sha256: str
    sample_rate: int | None = _EXPECTED_SAMPLE_RATE
    channels: int | None = _EXPECTED_CHANNELS
    leading_silence_ms: int = 0
    trailing_silence_ms: int = 0
    peak_dbfs: float | None = None
    rms_dbfs: float | None = None
    lufs: float | None = None


@dataclass(frozen=True)
class AuditLine:
    """Audit-ready view of one logical line, independent of UI or storage transport."""

    voice_index: int
    family: Literal["speech", "battle"]
    line_id: str
    audio: VoiceAsset | None
    subtitle: str | None = None
    transcription: Transcription | None = None
    metrics: AudioMetrics | None = None
    dialogue: VoiceAsset | None = None
    gap: VoiceAsset | None = None
    audio_variants: list[VoiceAsset] = field(default_factory=list)
    expected_components: tuple[str, ...] = ("audio",)
    expected_gap_duration_ms: int | None = None
    gap_valid: bool = True
    reviewed_component_hashes: dict[str, str] = field(default_factory=dict)
    reviewed_candidate_asset_id: str | None = None
    logical_line_evidence_id: str | None = None

    @property
    def evidence_id(self) -> str:
        return self.logical_line_evidence_id or f"voice-line:{self.voice_index}:{self.family}:{self.line_id}"


@dataclass(frozen=True)
class AuditBank:
    voice_index: int
    profiles: list[VoiceProfile] = field(default_factory=list)
    lines: list[AuditLine] = field(default_factory=list)
    edt_valid: bool = True
    edt_asset: VoiceAsset | None = None
    bank_evidence_id: str | None = None

    @property
    def evidence_id(self) -> str:
        return self.bank_evidence_id or f"voice-bank:{self.voice_index}"


def run_deterministic_audit(bank: AuditBank | Iterable[AuditLine]) -> list[Finding]:
    """Find reproducible structural/audio defects without requiring transcription."""
    audit_bank = _as_bank(bank)
    findings: list[Finding] = []
    if not audit_bank.edt_valid:
        findings.append(_finding("MALFORMED_EDT", audit_bank.voice_index, _bank_ids(audit_bank), "high", 1.0, "The dialogue EDT does not have the required fixed-record structure.", bank=audit_bank))
    if len(audit_bank.profiles) > 1:
        findings.append(_finding("SHARED_BANK_IMPACT", audit_bank.voice_index, _bank_ids(audit_bank), "warning", 1.0, "This voice bank is shared by more than one merc profile.", bank=audit_bank, profiles=[profile.profile_id for profile in audit_bank.profiles]))
    pcm_groups: dict[str, list[AuditLine]] = {}
    subtitle_groups: dict[str, list[AuditLine]] = {}
    for line in audit_bank.lines:
        ids = _line_ids(line)
        missing = _missing_components(line)
        if missing:
            findings.append(_finding("MISSING_COMPONENT", line.voice_index, ids, "high", 1.0, f"Required components are missing: {', '.join(missing)}.", line=line, missing=missing))
        reviewed_candidate = _reviewed_shadowed_candidate(line)
        if reviewed_candidate is not None:
            findings.append(_finding("SHADOWED_AUDIO", line.voice_index, ids + [reviewed_candidate.asset_id], "warning", 1.0, "The explicitly reviewed audio candidate is shadowed and is not the active in-game file.", line=line, reviewed_candidate=reviewed_candidate))
        if line.metrics is not None:
            _audit_metrics(findings, line, ids)
            if line.metrics.pcm_sha256:
                pcm_groups.setdefault(line.metrics.pcm_sha256, []).append(line)
        if line.subtitle and _normalized(line.subtitle):
            subtitle_groups.setdefault(_normalized(line.subtitle), []).append(line)
        _audit_gap(findings, line, ids)
        mismatches = _component_hash_mismatches(line)
        if mismatches:
            findings.append(_finding("COMPONENT_HASH_DRIFT", line.voice_index, ids, "warning", 1.0, "A component changed after the recorded synchronized review set.", line=line, mismatches=mismatches))
    for grouped in pcm_groups.values():
        if len(grouped) > 1:
            first = grouped[0]
            findings.append(_finding("EXACT_AUDIO_DUPLICATE", first.voice_index, _many_ids(grouped), "warning", 1.0, "Different triggers decode to exactly the same PCM audio.", audit_lines=grouped, lines=[line.line_id for line in grouped]))
    for subtitle, grouped in subtitle_groups.items():
        if len(grouped) > 1:
            first = grouped[0]
            findings.append(_finding("DUPLICATE_SUBTITLE", first.voice_index, _many_ids(grouped), "low", 1.0, "Different triggers display identical subtitle text.", audit_lines=grouped, lines=[line.line_id for line in grouped], subtitle=subtitle))
    return _deduplicate(findings)


def run_content_audit(
    lines: Iterable[AuditLine], *, preserve_context_order: bool = False,
) -> list[Finding]:
    """Use cached optional transcripts for advisory, within-bank content findings.

    Full-library callers keep deterministic numeric ordering.  A caller that
    explicitly supplies a contextual branch sequence can opt in to preserving
    that order for adjacent-line comparisons.
    """
    materialized = list(lines)
    if not preserve_context_order:
        materialized.sort(key=lambda line: (line.voice_index, line.family, _line_sort_key(line.line_id)))
    findings: list[Finding] = []
    for line in materialized:
        transcript = line.transcription
        if transcript is None or line.audio is None or transcript.source_sha256 != line.audio.sha256:
            continue
        transcript_normal = _normalized(transcript.text)
        subtitle_normal = _normalized(line.subtitle or "")
        if subtitle_normal and transcript_normal:
            ratio = SequenceMatcher(None, subtitle_normal, transcript_normal).ratio()
            if ratio < _SUBTITLE_RATIO:
                findings.append(_finding("SUBTITLE_TRANSCRIPT_MISMATCH", line.voice_index, _line_ids(line), "high", transcript.confidence, "The optional transcript materially disagrees with the displayed subtitle.", line=line, subtitle=line.subtitle, transcript=transcript.text, similarity=ratio))
        if line.metrics is not None and transcript_normal:
            estimated_seconds = len(transcript_normal.split()) / _WORDS_PER_SECOND
            decoded_seconds = line.metrics.duration_ms / 1000.0
            if decoded_seconds > 0 and estimated_seconds / decoded_seconds > _DURATION_FACTOR:
                findings.append(_finding("DURATION_TEXT_MISMATCH", line.voice_index, _line_ids(line), "warning", transcript.confidence, "The transcript's estimated speech time is much longer than the decoded audio.", line=line, estimated_s=estimated_seconds, decoded_s=decoded_seconds))
    for current, following in zip(materialized, materialized[1:]):
        if current.voice_index != following.voice_index or current.family != following.family:
            continue
        if current.transcription is None or following.transcription is None:
            continue
        if current.audio is None or following.audio is None:
            continue
        if current.transcription.source_sha256 != current.audio.sha256 or following.transcription.source_sha256 != following.audio.sha256:
            continue
        left, right = _normalized(current.transcription.text), _normalized(following.transcription.text)
        if left and right:
            ratio = SequenceMatcher(None, left, right).ratio()
            if ratio >= _ADJACENT_RATIO:
                findings.append(_finding("SEQUENCE_NEAR_DUPLICATE", current.voice_index, _line_ids(current) + _line_ids(following), "warning", min(current.transcription.confidence, following.transcription.confidence), "Adjacent lines have near-equivalent optional transcripts and may repeat in sequence.", audit_lines=[current, following], lines=[current.line_id, following.line_id], similarity=ratio))
    return _deduplicate(findings)


def _as_bank(bank: AuditBank | Iterable[AuditLine]) -> AuditBank:
    if isinstance(bank, AuditBank):
        return bank
    lines = list(bank)
    voice_index = lines[0].voice_index if lines else 0
    return AuditBank(voice_index=voice_index, lines=lines)


def _audit_metrics(findings: list[Finding], line: AuditLine, ids: list[str]) -> None:
    metrics = line.metrics
    assert metrics is not None
    if metrics.sample_rate != _EXPECTED_SAMPLE_RATE or metrics.channels != _EXPECTED_CHANNELS:
        findings.append(_finding("INVALID_AUDIO_FORMAT", line.voice_index, ids, "high", 1.0, "Active audio is not canonical 22,050 Hz mono.", line=line, sample_rate=metrics.sample_rate, channels=metrics.channels))
    if metrics.leading_silence_ms > _SILENCE_LIMIT_MS or metrics.trailing_silence_ms > _SILENCE_LIMIT_MS:
        findings.append(_finding("EXCESS_SILENCE", line.voice_index, ids, "warning", 1.0, "The line has more than 500 ms of leading or trailing silence.", line=line, leading_ms=metrics.leading_silence_ms, trailing_ms=metrics.trailing_silence_ms))
    if metrics.peak_dbfs is not None and metrics.peak_dbfs >= -0.1:
        findings.append(_finding("CLIPPING", line.voice_index, ids, "high", 1.0, "The decoded audio peak is at or above -0.1 dBFS and may clip.", line=line, peak_dbfs=metrics.peak_dbfs))
    if metrics.rms_dbfs is not None and metrics.rms_dbfs <= _NEAR_SILENCE_DBFS:
        findings.append(_finding("NEAR_SILENCE", line.voice_index, ids, "warning", 1.0, "The decoded audio is near silent.", line=line, rms_dbfs=metrics.rms_dbfs))
    if metrics.duration_ms > _EXTREME_DURATION_MS:
        findings.append(_finding("EXTREME_DURATION", line.voice_index, ids, "warning", 1.0, "The decoded audio duration is unusually long for one voice trigger.", line=line, duration_ms=metrics.duration_ms))
    if metrics.lufs is not None and (metrics.lufs < -35.0 or metrics.lufs > -8.0):
        findings.append(_finding("LEVEL_OUTLIER", line.voice_index, ids, "warning", 1.0, "The line loudness is outside the Voice Lab review range.", line=line, lufs=metrics.lufs))


def _audit_gap(findings: list[Finding], line: AuditLine, ids: list[str]) -> None:
    if line.gap is not None and not line.gap_valid:
        findings.append(_finding("INVALID_GAP", line.voice_index, ids, "high", 1.0, "The lip-sync gap file has invalid or overlapping timing pairs.", line=line))
    if line.gap is not None and line.metrics is not None and line.expected_gap_duration_ms is not None and abs(line.metrics.duration_ms - line.expected_gap_duration_ms) > 100:
        findings.append(_finding("STALE_GAP", line.voice_index, ids, "warning", 1.0, "The lip-sync gap data was generated for a materially different audio duration.", line=line, audio_duration_ms=line.metrics.duration_ms, gap_duration_ms=line.expected_gap_duration_ms))


def _missing_components(line: AuditLine) -> list[str]:
    present = {"audio": line.audio is not None, "subtitle": line.subtitle is not None, "gap": line.gap is not None}
    return [component for component in line.expected_components if not present.get(component, False)]


def _line_ids(line: AuditLine) -> list[str]:
    ids = [asset.asset_id for asset in (line.audio, line.dialogue, line.gap) if asset is not None]
    return ids or [line.evidence_id]


def _bank_ids(bank: AuditBank) -> list[str]:
    return [bank.edt_asset.asset_id] if bank.edt_asset is not None else [bank.evidence_id]


def _reviewed_shadowed_candidate(line: AuditLine) -> VoiceAsset | None:
    if line.reviewed_candidate_asset_id is None:
        return None
    candidates = [*line.audio_variants]
    if line.audio is not None:
        candidates.append(line.audio)
    for candidate in candidates:
        if candidate.asset_id == line.reviewed_candidate_asset_id:
            return None if candidate.winner else candidate
    return None


def _component_hash_mismatches(line: AuditLine) -> dict[str, dict[str, str | None]]:
    current = {
        name: asset.sha256
        for name, asset in (("audio", line.audio), ("edt", line.dialogue), ("gap", line.gap))
        if asset is not None
    }
    return {
        name: {"expected": expected, "current": current.get(name)}
        for name, expected in line.reviewed_component_hashes.items()
        if current.get(name) != expected
    }


def _many_ids(lines: Iterable[AuditLine]) -> list[str]:
    return [asset_id for line in lines for asset_id in _line_ids(line)]


def _line_sort_key(line_id: str) -> tuple[int, str]:
    return (int(line_id), line_id) if line_id.isdigit() else (10**9, line_id)


def _normalized(text: str) -> str:
    return " ".join("".join(char.lower() if char.isalnum() else " " for char in text).split())


def _finding(code: str, voice_index: int, asset_ids: list[str], severity: str, confidence: float, plain_language: str, **evidence: object) -> Finding:
    canonical_evidence = json.loads(
        json.dumps(evidence, default=_json_default, sort_keys=True, separators=(",", ":"))
    )
    material = {"code": code, "voice_index": voice_index, "asset_ids": sorted(asset_ids), "evidence": canonical_evidence}
    encoded = json.dumps(material, sort_keys=True, separators=(",", ":")).encode("utf-8")
    stable_suffix = ":".join(sorted(asset_ids)) or str(voice_index)
    return Finding(
        stable_key=f"{code}:{voice_index}:{stable_suffix}", evidence_hash=sha256(encoded).hexdigest(),
        code=code, severity=severity, confidence=max(0.0, min(1.0, confidence)), asset_ids=sorted(asset_ids),
        evidence={"plain_language": plain_language, **canonical_evidence},
    )


def _json_default(value: object) -> object:
    if isinstance(value, AuditLine):
        return {
            "voice_index": value.voice_index, "family": value.family, "line_id": value.line_id,
            "logical_line_evidence_id": value.evidence_id, "audio": value.audio,
            "dialogue": value.dialogue, "gap": value.gap, "audio_variants": value.audio_variants,
            "metrics": value.metrics, "subtitle": value.subtitle,
            "expected_components": value.expected_components,
            "expected_gap_duration_ms": value.expected_gap_duration_ms, "gap_valid": value.gap_valid,
            "reviewed_component_hashes": value.reviewed_component_hashes,
            "reviewed_candidate_asset_id": value.reviewed_candidate_asset_id,
        }
    if isinstance(value, AuditBank):
        return {
            "voice_index": value.voice_index, "bank_evidence_id": value.evidence_id,
            "edt_asset": value.edt_asset,
            "profiles": [profile.profile_id for profile in value.profiles],
        }
    if isinstance(value, VoiceAsset):
        return {
            "asset_id": value.asset_id, "family": value.family, "voice_index": value.voice_index,
            "line_id": value.line_id, "extension": value.extension, "source_kind": value.source_kind,
            "source_locator": value.source_locator, "layer_rank": value.layer_rank,
            "size_bytes": value.size_bytes, "mtime_ns": value.mtime_ns, "sha256": value.sha256,
            "writable": value.writable, "winner": value.winner,
        }
    if isinstance(value, AudioMetrics):
        return {
            "duration_ms": value.duration_ms, "pcm_sha256": value.pcm_sha256,
            "sample_rate": value.sample_rate, "channels": value.channels,
            "leading_silence_ms": value.leading_silence_ms,
            "trailing_silence_ms": value.trailing_silence_ms, "peak_dbfs": value.peak_dbfs,
            "rms_dbfs": value.rms_dbfs, "lufs": value.lufs,
        }
    raise TypeError(type(value).__name__)


def _deduplicate(findings: list[Finding]) -> list[Finding]:
    return list({finding.stable_key: finding for finding in findings}.values())
