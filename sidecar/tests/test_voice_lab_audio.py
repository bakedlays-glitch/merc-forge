"""FFmpeg-backed Voice Lab render, waveform, and lip-sync contracts."""
from __future__ import annotations

import math
from pathlib import Path
import wave
from hashlib import sha256

import pytest

from mercwizard_core.voice_lab.audio import AudioToolchain, validate_gap
from mercwizard_core.voice_lab.models import EditRecipe


def _winget_ffmpeg() -> Path | None:
    """Newest winget-installed ffmpeg.exe (survives version bumps — winget keeps
    only the current build, so a pinned version path rots on every upgrade)."""
    base = (
        Path.home() / "AppData/Local/Microsoft/WinGet/Packages"
        / "Gyan.FFmpeg_Microsoft.Winget.Source_8wekyb3d8bbwe"
    )
    builds = sorted(base.glob("ffmpeg-*-full_build/bin/ffmpeg.exe"))
    return builds[-1] if builds else None


FFMPEG = _winget_ffmpeg()


class _Settings:
    voice_ffmpeg_path = str(FFMPEG) if FFMPEG else None
    voice_authoring_workspace = None


@pytest.fixture
def ffmpeg_toolchain() -> AudioToolchain:
    """Use the explicit approved ffmpeg capability, not the ambient PATH."""
    try:
        return AudioToolchain.resolve(_Settings())
    except RuntimeError as exc:
        pytest.skip(str(exc))


@pytest.fixture
def spoken_wav(tmp_path: Path) -> Path:
    """Create 100ms silence, 200ms tone, then 100ms silence at 22.05 kHz."""
    sample_rate = 22_050
    frames: list[int] = []
    for index in range(sample_rate * 4 // 10):
        seconds = index / sample_rate
        amplitude = 0 if seconds < 0.1 or seconds >= 0.3 else int(10_000 * math.sin(2 * math.pi * 440 * seconds))
        frames.append(amplitude)
    path = tmp_path / "spoken.wav"
    with wave.open(str(path), "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(sample_rate)
        output.writeframes(b"".join(frame.to_bytes(2, "little", signed=True) for frame in frames))
    return path


@pytest.fixture
def spoken_ogg(ffmpeg_toolchain: AudioToolchain, spoken_wav: Path, tmp_path: Path) -> Path:
    """Exercise gap detection against the same OGG format the game receives."""
    recipe = EditRecipe.model_validate({
        "recipe_id": "fixture-ogg",
        "install_id": "fixture-install",
        "voice_index": 108,
        "family": "speech",
        "line_id": "111",
        "input_asset_id": "fixture-wav",
        "input_sha256": sha256(spoken_wav.read_bytes()).hexdigest(),
        "output_extension": ".ogg",
        "operations": [],
    })
    return ffmpeg_toolchain.render_recipe(spoken_wav, recipe, tmp_path / "spoken.ogg").output_path


def test_ogg_gap_generation_is_valid(ffmpeg_toolchain: AudioToolchain, spoken_ogg: Path) -> None:
    """Using wave on an OGG would silently omit a required lip-sync file."""
    result = ffmpeg_toolchain.generate_gap(spoken_ogg)
    pairs = validate_gap(result.gap_bytes, result.duration_ms)
    assert all(start < end <= result.duration_ms for start, end in pairs)


def test_zero_byte_gap_is_valid_for_audio_without_detected_silence() -> None:
    """Treating a valid empty gap as an error would block continuous speech clips."""
    assert validate_gap(b"", 400) == ()


def test_same_recipe_same_toolchain_has_same_decoded_pcm_hash(
    ffmpeg_toolchain: AudioToolchain,
    spoken_wav: Path,
    tmp_path: Path,
) -> None:
    """Changing the rendered sound under the same recipe would invalidate review."""
    recipe = EditRecipe.model_validate({
        "recipe_id": "deterministic",
        "install_id": "fixture-install",
        "voice_index": 108,
        "family": "speech",
        "line_id": "111",
        "input_asset_id": "fixture-wav",
        "input_sha256": sha256(spoken_wav.read_bytes()).hexdigest(),
        "output_extension": ".ogg",
        "operations": [{"kind": "cut", "start_ms": 100, "end_ms": 200}],
    })

    first = ffmpeg_toolchain.render_recipe(spoken_wav, recipe, tmp_path / "first.ogg")
    second = ffmpeg_toolchain.render_recipe(spoken_wav, recipe, tmp_path / "second.ogg")
    assert first.decoded_pcm_sha256 == second.decoded_pcm_sha256
    assert len(first.encoded_sha256) == len(second.encoded_sha256) == 64
    assert first.ffmpeg_version.startswith("ffmpeg version ")


def test_render_revalidates_forged_model_copy_before_running_ffmpeg(
    ffmpeg_toolchain: AudioToolchain,
    spoken_wav: Path,
    tmp_path: Path,
) -> None:
    """A copied recipe must not bypass the renderer's pinned output contract."""
    recipe = EditRecipe.model_validate({
        "recipe_id": "forged-copy",
        "install_id": "fixture-install",
        "voice_index": 108,
        "family": "speech",
        "line_id": "111",
        "input_asset_id": "fixture-wav",
        "input_sha256": sha256(spoken_wav.read_bytes()).hexdigest(),
        "output_extension": ".ogg",
        "operations": [],
    }).model_copy(update={
        "output_format_contract": {
            "container": "ogg", "codec": "libvorbis", "quality": 3,
            "sample_rate_hz": 22050, "channels": 1,
        }
    })

    with pytest.raises(ValueError, match="output format contract"):
        ffmpeg_toolchain.render_recipe(spoken_wav, recipe, tmp_path / "forged.ogg")


def test_waveform_has_a_duration_and_non_silent_peak(
    ffmpeg_toolchain: AudioToolchain,
    spoken_wav: Path,
) -> None:
    """A waveform that omits source duration cannot support millisecond edits."""
    waveform = ffmpeg_toolchain.waveform(spoken_wav, buckets=16)
    assert 390 <= waveform.duration_ms <= 410
    assert max(waveform.peaks) > 0.1
