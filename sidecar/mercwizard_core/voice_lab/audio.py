"""Pinned FFmpeg rendering, waveform inspection, and JA2 OGG gap generation."""
from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import math
from pathlib import Path
import re
import struct
import subprocess
from typing import Any, Mapping

from .models import CutOperation, EditRecipe, NormalizeOperation, TrimOperation, revalidate_edit_recipe


_SILENCE_RE = re.compile(r"silence_(start|end):\s*([0-9.]+)")


def _setting(settings: Any, key: str) -> Any:
    if isinstance(settings, Mapping):
        return settings.get(key)
    return getattr(settings, key, None)


@dataclass(frozen=True)
class AudioProbe:
    duration_ms: int
    sample_rate: int | None = None
    channels: int | None = None


@dataclass(frozen=True)
class Waveform:
    duration_ms: int
    peaks: tuple[float, ...]


@dataclass(frozen=True)
class DecodedMetrics:
    """Decoded signal facts consumed by deterministic Voice Lab audits."""

    duration_ms: int
    pcm_sha256: str
    sample_rate: int | None
    channels: int | None
    leading_silence_ms: int
    trailing_silence_ms: int
    peak_dbfs: float | None
    rms_dbfs: float | None


@dataclass(frozen=True)
class RenderResult:
    output_path: Path
    duration_ms: int
    encoded_sha256: str
    decoded_pcm_sha256: str
    source_sha256: str
    recipe_sha256: str
    ffmpeg_version: str


@dataclass(frozen=True)
class GapResult:
    gap_bytes: bytes
    duration_ms: int
    pairs: tuple[tuple[int, int], ...]


class AudioToolchain:
    """A configured FFmpeg/ffprobe pair; ambient PATH is never used."""

    def __init__(self, ffmpeg: Path, ffprobe: Path) -> None:
        self.ffmpeg = ffmpeg
        self.ffprobe = ffprobe

    @classmethod
    def resolve(cls, settings: Any) -> "AudioToolchain":
        configured = _setting(settings, "voice_ffmpeg_path")
        if not configured:
            raise RuntimeError("Voice Lab ffmpeg path is not configured")
        ffmpeg = Path(str(configured)).expanduser()
        if not ffmpeg.is_file():
            raise RuntimeError(f"Voice Lab ffmpeg executable is unavailable: {ffmpeg}")
        ffprobe = ffmpeg.with_name("ffprobe.exe" if ffmpeg.suffix.lower() == ".exe" else "ffprobe")
        if not ffprobe.is_file():
            raise RuntimeError(f"Voice Lab ffprobe executable is unavailable: {ffprobe}")
        return cls(ffmpeg, ffprobe)

    def version(self) -> str:
        completed = self._run([str(self.ffmpeg), "-version"])
        first = completed.stdout.splitlines()[0] if completed.stdout else ""
        if not first.startswith("ffmpeg version "):
            raise RuntimeError("ffmpeg did not report a version")
        return first

    def probe(self, audio_path: Path | str) -> AudioProbe:
        path = Path(audio_path)
        completed = self._run([
            str(self.ffprobe), "-v", "error", "-show_entries",
            "format=duration:stream=sample_rate,channels", "-of", "json", str(path),
        ])
        try:
            import json
            payload = json.loads(completed.stdout)
            duration_seconds = float(payload["format"]["duration"])
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise RuntimeError(f"ffprobe did not report duration for {path}") from exc
        streams = payload.get("streams", [])
        stream = streams[0] if streams else {}
        return AudioProbe(
            duration_ms=max(0, int(round(duration_seconds * 1000))),
            sample_rate=_optional_int(stream.get("sample_rate")),
            channels=_optional_int(stream.get("channels")),
        )

    def waveform(self, audio_path: Path | str, *, buckets: int = 128) -> Waveform:
        if buckets <= 0:
            raise ValueError("waveform buckets must be positive")
        path = Path(audio_path)
        probe = self.probe(path)
        completed = self._run([
            str(self.ffmpeg), "-hide_banner", "-nostdin", "-i", str(path),
            "-ac", "1", "-ar", "22050", "-f", "f32le", "-",
        ], text=False)
        samples = memoryview(completed.stdout).cast("f") if completed.stdout else memoryview(b"").cast("B")
        if not samples:
            return Waveform(duration_ms=probe.duration_ms, peaks=tuple(0.0 for _ in range(buckets)))
        peaks: list[float] = []
        for bucket in range(buckets):
            start = len(samples) * bucket // buckets
            end = len(samples) * (bucket + 1) // buckets
            peaks.append(max((abs(float(value)) for value in samples[start:end]), default=0.0))
        return Waveform(duration_ms=probe.duration_ms, peaks=tuple(peaks))

    def decoded_metrics(self, audio_path: Path | str) -> DecodedMetrics:
        """Decode once into canonical PCM and derive audit-safe signal facts."""
        path = Path(audio_path)
        probe = self.probe(path)
        completed = self._run([
            str(self.ffmpeg), "-hide_banner", "-nostdin", "-i", str(path),
            "-ac", "1", "-ar", "22050", "-f", "f32le", "-",
        ], text=False)
        raw = bytes(completed.stdout)
        samples = memoryview(raw).cast("f") if raw else ()
        values = [float(value) for value in samples]
        if not values:
            return DecodedMetrics(probe.duration_ms, self._decoded_pcm_sha256(path), probe.sample_rate, probe.channels, 0, 0, None, None)
        threshold = 10 ** (-60 / 20)
        first = next((index for index, value in enumerate(values) if abs(value) > threshold), len(values))
        last = next((index for index, value in enumerate(reversed(values)) if abs(value) > threshold), len(values))
        peak = max(abs(value) for value in values)
        rms = math.sqrt(sum(value * value for value in values) / len(values))
        def db(value: float) -> float:
            return 20 * math.log10(max(value, 1e-12))
        return DecodedMetrics(
            duration_ms=probe.duration_ms, pcm_sha256=self._decoded_pcm_sha256(path), sample_rate=probe.sample_rate,
            channels=probe.channels, leading_silence_ms=int(round(first * 1000 / 22050)),
            trailing_silence_ms=int(round(last * 1000 / 22050)), peak_dbfs=db(peak), rms_dbfs=db(rms),
        )

    def render_recipe(self, source_path: Path | str, recipe: EditRecipe, output_path: Path | str) -> RenderResult:
        recipe = revalidate_edit_recipe(recipe)
        source = Path(source_path)
        destination = Path(output_path)
        if recipe.output_extension != ".ogg" or destination.suffix.lower() != ".ogg":
            raise ValueError("Voice Lab previews must render canonical .ogg output")
        source_bytes = source.read_bytes()
        source_sha256 = sha256(source_bytes).hexdigest()
        if source_sha256 != recipe.input_sha256:
            raise ValueError("recipe input_sha256 does not match immutable source bytes")
        duration_ms = self.probe(source).duration_ms
        self._validate_render_ranges(recipe, duration_ms)
        destination.parent.mkdir(parents=True, exist_ok=True)
        filters, output_label = self._filters(recipe, duration_ms)
        command = [
            str(self.ffmpeg), "-hide_banner", "-nostdin", "-y", "-i", str(source),
            "-filter_complex", ";".join(filters), "-map", output_label,
            "-ar", "22050", "-ac", "1", "-c:a", "libvorbis", "-q:a", "4", str(destination),
        ]
        self._run(command)
        if not destination.is_file() or destination.stat().st_size == 0:
            raise RuntimeError("ffmpeg did not create Voice Lab preview output")
        return RenderResult(
            output_path=destination,
            duration_ms=self.probe(destination).duration_ms,
            encoded_sha256=sha256(destination.read_bytes()).hexdigest(),
            decoded_pcm_sha256=self._decoded_pcm_sha256(destination),
            source_sha256=source_sha256,
            recipe_sha256=recipe.serialized_recipe_hash(),
            ffmpeg_version=self.version(),
        )

    def generate_gap(self, audio_path: Path | str) -> GapResult:
        path = Path(audio_path)
        duration_ms = self.probe(path).duration_ms
        command = [
            str(self.ffmpeg), "-hide_banner", "-nostdin", "-i", str(path),
            "-af", "silencedetect=noise=-33dB:d=0.05", "-f", "null", "-",
        ]
        completed = self._run(command)
        starts: list[float] = []
        pairs: list[tuple[int, int]] = []
        for match in _SILENCE_RE.finditer(completed.stderr):
            value = float(match.group(2))
            if match.group(1) == "start":
                starts.append(value)
            elif starts:
                start = starts.pop(0)
                pair = _clamp_gap_pair(start, value, duration_ms)
                if pair is not None:
                    pairs.append(pair)
        for start in starts:
            pair = _clamp_gap_pair(start, duration_ms / 1000.0, duration_ms)
            if pair is not None:
                pairs.append(pair)
        ordered = _merge_gap_pairs(pairs)
        gap_bytes = b"".join(struct.pack("<II", start, end) for start, end in ordered)
        validate_gap(gap_bytes, duration_ms)
        return GapResult(gap_bytes=gap_bytes, duration_ms=duration_ms, pairs=tuple(ordered))

    def _filters(self, recipe: EditRecipe, duration_ms: int) -> tuple[list[str], str]:
        filters: list[str] = []
        current = "a0"
        filters.append("[0:a]asetpts=PTS-STARTPTS[a0]")
        current_duration = duration_ms
        for index, operation in enumerate(recipe.operations, start=1):
            next_label = f"a{index}"
            if isinstance(operation, TrimOperation):
                filters.append(
                    f"[{current}]atrim=start={operation.start_ms / 1000:.3f}:end={operation.end_ms / 1000:.3f},asetpts=PTS-STARTPTS[{next_label}]"
                )
                current_duration = operation.end_ms - operation.start_ms
            elif isinstance(operation, CutOperation):
                before, after = f"pre{index}", f"post{index}"
                filters.extend([
                    f"[{current}]atrim=start=0:end={operation.start_ms / 1000:.3f},asetpts=PTS-STARTPTS[{before}]",
                    f"[{current}]atrim=start={operation.end_ms / 1000:.3f}:end={current_duration / 1000:.3f},asetpts=PTS-STARTPTS[{after}]",
                    f"[{before}][{after}]concat=n=2:v=0:a=1[{next_label}]",
                ])
                current_duration -= operation.end_ms - operation.start_ms
            elif isinstance(operation, NormalizeOperation):
                filters.append(
                    f"[{current}]loudnorm=I={operation.target_lufs}:TP={operation.true_peak_db}:LRA=11[{next_label}]"
                )
            else:  # pragma: no cover - discriminated union keeps this unreachable.
                raise ValueError(f"unsupported recipe operation: {operation}")
            current = next_label
        final_label = "out"
        filters.append(f"[{current}]aresample=22050,aformat=channel_layouts=mono[{final_label}]")
        return filters, f"[{final_label}]"

    @staticmethod
    def _validate_render_ranges(recipe: EditRecipe, duration_ms: int) -> None:
        current_duration = duration_ms
        for operation in recipe.operations:
            if isinstance(operation, (CutOperation, TrimOperation)):
                if operation.end_ms > current_duration:
                    raise ValueError("operation range exceeds source duration at this recipe step")
                if isinstance(operation, CutOperation) and operation.end_ms - operation.start_ms >= current_duration:
                    raise ValueError("cut operation would produce zero-length output")
                if isinstance(operation, TrimOperation):
                    current_duration = operation.end_ms - operation.start_ms
                else:
                    current_duration -= operation.end_ms - operation.start_ms

    def _decoded_pcm_sha256(self, audio_path: Path) -> str:
        completed = self._run([
            str(self.ffmpeg), "-hide_banner", "-nostdin", "-i", str(audio_path),
            "-ac", "1", "-ar", "22050", "-f", "s16le", "-",
        ], text=False)
        return sha256(completed.stdout).hexdigest()

    @staticmethod
    def _run(command: list[str], *, text: bool = True) -> subprocess.CompletedProcess[Any]:
        try:
            completed = subprocess.run(command, capture_output=True, text=text, check=False)
        except OSError as exc:
            raise RuntimeError(f"unable to execute audio tool: {command[0]}") from exc
        if completed.returncode != 0:
            stderr = completed.stderr.decode("utf-8", "replace") if isinstance(completed.stderr, bytes) else completed.stderr
            raise RuntimeError(f"audio tool failed ({completed.returncode}): {stderr[-1000:]}")
        return completed


def validate_gap(gap_bytes: bytes, duration_ms: int) -> tuple[tuple[int, int], ...]:
    """Validate JA2's flat little-endian silence-pair binary format."""
    if duration_ms < 0:
        raise ValueError("gap duration must not be negative")
    if len(gap_bytes) % 8 != 0:
        raise ValueError("gap bytes must contain complete little-endian <II> pairs")
    pairs: list[tuple[int, int]] = []
    prior_end = 0
    for offset in range(0, len(gap_bytes), 8):
        start, end = struct.unpack_from("<II", gap_bytes, offset)
        if not 0 <= start < end <= duration_ms:
            raise ValueError("gap pair is outside duration or zero-length")
        if start < prior_end:
            raise ValueError("gap pairs overlap or are out of order")
        prior_end = end
        pairs.append((start, end))
    return tuple(pairs)


def _clamp_gap_pair(start_seconds: float, end_seconds: float, duration_ms: int) -> tuple[int, int] | None:
    start = max(0, min(duration_ms, int(math.floor(start_seconds * 1000))))
    end = max(0, min(duration_ms, int(math.ceil(end_seconds * 1000))))
    return (start, end) if start < end else None


def _merge_gap_pairs(pairs: list[tuple[int, int]]) -> list[tuple[int, int]]:
    merged: list[tuple[int, int]] = []
    for start, end in sorted(pairs):
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    return merged


def _optional_int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
