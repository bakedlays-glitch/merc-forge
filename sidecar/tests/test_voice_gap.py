"""Tests for JA2 ``.gap`` lip-sync generation (mercwizard_core.gap) and its
wiring into the voice write/delete paths.

All write tests use the hermetic ``fake_install`` tmp tree — they never touch
any real JA2 install. One opt-in test cross-checks the stock ``250_027.gap``
when that install happens to be present (skipped otherwise).
"""
from __future__ import annotations

import io
import math
import os
import wave
from array import array
from pathlib import Path

import pytest

from mercwizard_core import gap, voice


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #

def _make_wav(segments, *, rate=22050, ch=1, width=2) -> bytes:
    """Build PCM WAV bytes from ``[(amp_frac, dur_ms), ...]``.

    ``amp_frac`` 0.0 = silence, 1.0 = near-full-scale 220 Hz tone.
    """
    buf = io.BytesIO()
    w = wave.open(buf, "wb")
    w.setnchannels(ch)
    w.setsampwidth(width)
    w.setframerate(rate)
    if width == 2:
        frames = array("h")
        for amp, dur_ms in segments:
            n = int(rate * dur_ms / 1000)
            for s in range(n):
                v = int(amp * 30000 * math.sin(2 * math.pi * 220 * s / rate))
                for _c in range(ch):
                    frames.append(v)
        w.writeframes(frames.tobytes())
    elif width == 1:
        frames = bytearray()
        for amp, dur_ms in segments:
            n = int(rate * dur_ms / 1000)
            for s in range(n):
                v = 128 + int(amp * 120 * math.sin(2 * math.pi * 220 * s / rate))
                v = max(0, min(255, v))
                for _c in range(ch):
                    frames.append(v)
        w.writeframes(bytes(frames))
    else:  # pragma: no cover - tests only build 8/16-bit
        raise ValueError(width)
    w.close()
    return buf.getvalue()


def _assert_well_formed(pairs):
    """Engine requires ascending, non-overlapping, positive-width pairs."""
    prev_end = -1
    for s, e in pairs:
        assert e > s, f"non-positive gap {(s, e)}"
        assert s >= prev_end, f"out-of-order/overlapping gap {(s, e)} after {prev_end}"
        prev_end = e


def _has_gap_near(pairs, start, end, tol=25):
    return any(abs(s - start) <= tol and abs(e - end) <= tol for s, e in pairs)


# --------------------------------------------------------------------------- #
# format (de)serialization
# --------------------------------------------------------------------------- #

def test_gap_format_exact_bytes():
    # Matches the stock 250_027.gap layout: little-endian uint32 (start,end) pairs.
    pairs = [(2, 73), (978, 1206)]
    assert gap.gaps_to_bytes(pairs) == bytes.fromhex("0200000049000000d2030000b6040000")


def test_gap_roundtrip():
    pairs = [(0, 70), (998, 1197), (1566, 1837)]
    assert gap.parse_gap_bytes(gap.gaps_to_bytes(pairs)) == pairs


def test_parse_ignores_trailing_partial_pair():
    # Engine reads pairs until EOF; a lone trailing uint32 is not a pair.
    body = gap.gaps_to_bytes([(10, 60)])
    assert gap.parse_gap_bytes(body + b"\x05\x00\x00\x00") == [(10, 60)]


def test_parse_empty():
    assert gap.parse_gap_bytes(b"") == []


# --------------------------------------------------------------------------- #
# silence detection
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("ch", [1, 2])
@pytest.mark.parametrize("width", [1, 2])
def test_detect_internal_silence(ch, width):
    wav = _make_wav([(1.0, 300), (0.0, 300), (1.0, 300)], ch=ch, width=width)
    gaps = gap.detect_silence_gaps(wav)
    assert gaps is not None
    _assert_well_formed(gaps)
    assert _has_gap_near(gaps, 300, 600), gaps


def test_detect_no_silence_returns_empty():
    # Continuous tone: decodes fine but has no qualifying silence.
    gaps = gap.detect_silence_gaps(_make_wav([(1.0, 500)]))
    assert gaps == []


def test_detect_all_silence():
    gaps = gap.detect_silence_gaps(_make_wav([(0.0, 500)]))
    assert gaps is not None and len(gaps) == 1
    s, e = gaps[0]
    assert s <= 5 and e >= 480


def test_detect_short_blip_below_min_gap_ignored():
    # A 20 ms silence is below MIN_GAP_MS (50) and should be dropped.
    gaps = gap.detect_silence_gaps(_make_wav([(1.0, 300), (0.0, 20), (1.0, 300)]))
    assert gaps == []


@pytest.mark.parametrize("blob", [b"", b"not a wav at all", b"OggS\x00\x02nonsense"])
def test_undecodable_returns_none(blob):
    assert gap.detect_silence_gaps(blob) is None


# --------------------------------------------------------------------------- #
# write_gap_beside
# --------------------------------------------------------------------------- #

def test_write_gap_beside_wav(tmp_path):
    clip = tmp_path / "250_027.wav"
    data = _make_wav([(1.0, 300), (0.0, 300), (1.0, 300)])
    clip.write_bytes(data)
    out = gap.write_gap_beside(clip, data)
    assert out == clip.with_suffix(".gap")
    assert out.is_file()
    _assert_well_formed(gap.parse_gap_bytes(out.read_bytes()))


def test_write_gap_beside_non_wav_skips(tmp_path):
    clip = tmp_path / "250_027.ogg"
    clip.write_bytes(b"OggS\x00\x02whatever")
    assert gap.write_gap_beside(clip, b"OggS\x00\x02whatever") is None
    assert not clip.with_suffix(".gap").exists()


def test_write_gap_beside_removes_stale(tmp_path):
    clip = tmp_path / "250_027.wav"
    gap_path = clip.with_suffix(".gap")
    gap_path.write_bytes(gap.gaps_to_bytes([(100, 200)]))  # pretend a previous clip's gap
    # New clip is gap-free -> the stale sidecar must be removed.
    silent_free = _make_wav([(1.0, 400)])
    clip.write_bytes(silent_free)
    assert gap.write_gap_beside(clip, silent_free) is None
    assert not gap_path.exists()


# --------------------------------------------------------------------------- #
# integration through the voice write/delete paths
# --------------------------------------------------------------------------- #

def test_add_clip_bytes_emits_gap(fake_install):
    data = _make_wav([(1.0, 300), (0.0, 300), (1.0, 300)])
    clip = voice.add_clip_bytes(fake_install, 250, "250_027.wav", data)
    gap_path = Path(clip.path).with_suffix(".gap")
    assert gap_path.is_file()
    pairs = gap.parse_gap_bytes(gap_path.read_bytes())
    _assert_well_formed(pairs)
    assert _has_gap_near(pairs, 300, 600), pairs


def test_add_clip_bytes_ogg_writes_clip_without_gap(fake_install):
    clip = voice.add_clip_bytes(fake_install, 250, "250_027.ogg", b"OggS\x00\x02nope")
    assert Path(clip.path).is_file()
    assert not Path(clip.path).with_suffix(".gap").exists()


def test_overwrite_with_gap_free_clip_removes_gap(fake_install):
    voiced = _make_wav([(1.0, 300), (0.0, 300), (1.0, 300)])
    clip = voice.add_clip_bytes(fake_install, 250, "250_027.wav", voiced)
    gap_path = Path(clip.path).with_suffix(".gap")
    assert gap_path.is_file()
    # Re-upload the same name with a clip that has no silence.
    voice.add_clip_bytes(fake_install, 250, "250_027.wav", _make_wav([(1.0, 500)]))
    assert not gap_path.exists()


def test_delete_clip_removes_gap(fake_install):
    data = _make_wav([(1.0, 300), (0.0, 300), (1.0, 300)])
    clip = voice.add_clip_bytes(fake_install, 250, "250_027.wav", data)
    gap_path = Path(clip.path).with_suffix(".gap")
    assert gap_path.is_file()
    assert voice.delete_clip(fake_install, 250, "250_027.wav") is True
    assert not Path(clip.path).exists()
    assert not gap_path.exists()


def test_delete_all_clips_removes_gaps(fake_install):
    clips = []
    for bark in (27, 28):
        c = voice.add_clip_bytes(
            fake_install, 250, f"250_{bark:03d}.wav",
            _make_wav([(1.0, 300), (0.0, 300), (1.0, 300)]),
        )
        clips.append(Path(c.path))
    for c in clips:
        assert c.with_suffix(".gap").is_file()
    removed = voice.delete_all_clips(fake_install, 250)
    assert removed == 2  # counts clips, not the .gap sidecars
    for c in clips:
        assert not c.exists()
        assert not c.with_suffix(".gap").exists()


# --------------------------------------------------------------------------- #
# Vengeance / slot_prefix layout (canonical) — the path the wiring also covers
# --------------------------------------------------------------------------- #

@pytest.fixture
def slot_prefix_install(fake_install: Path) -> Path:
    """``fake_install`` tweaked so ``detect_flavor`` reports the slot_prefix layout.

    A single digit-prefixed flat clip under ``Speech/`` flips detection (see
    ``install_context.detect_flavor``).
    """
    speech = fake_install / "Data-1.13" / "Speech"
    speech.mkdir(parents=True, exist_ok=True)
    (speech / "250_000.ogg").write_bytes(b"seed clip")
    return fake_install


def test_slot_prefix_layout_is_detected(slot_prefix_install):
    from mercwizard_core.install_context import make_install_context
    assert make_install_context(slot_prefix_install).flavor.voice_layout == "slot_prefix"


def test_add_clip_bytes_emits_gap_slot_prefix(slot_prefix_install):
    data = _make_wav([(1.0, 300), (0.0, 300), (1.0, 300)])
    clip = voice.add_clip_bytes(slot_prefix_install, 250, "250_027.wav", data)
    clip_path = Path(clip.path)
    assert clip_path.suffix.lower() == ".wav"
    gap_path = clip_path.with_suffix(".gap")
    assert gap_path.is_file()
    pairs = gap.parse_gap_bytes(gap_path.read_bytes())
    _assert_well_formed(pairs)
    assert _has_gap_near(pairs, 300, 600), pairs


def test_delete_all_clips_removes_gaps_slot_prefix(slot_prefix_install):
    clips = []
    for bark in (27, 28):
        c = voice.add_clip_bytes(
            slot_prefix_install, 250, f"250_{bark:03d}.wav",
            _make_wav([(1.0, 300), (0.0, 300), (1.0, 300)]),
        )
        clips.append(Path(c.path))
    for c in clips:
        assert c.with_suffix(".gap").is_file()
    removed = voice.delete_all_clips(slot_prefix_install, 250)
    # Audio clips only: the two .wav we added + the 250_000.ogg seed (= 3);
    # the .gap sidecars are removed alongside but not counted.
    assert removed == 3
    for c in clips:
        assert not c.exists()
        assert not c.with_suffix(".gap").exists()


# --------------------------------------------------------------------------- #
# opt-in cross-check against the stock install
# --------------------------------------------------------------------------- #

# Opt-in: set JA2_STOCK_GAP to a stock ``250_027.gap`` to run this cross-check.
_STOCK_GAP_ENV = os.environ.get("JA2_STOCK_GAP")
_STOCK_GAP = Path(_STOCK_GAP_ENV) if _STOCK_GAP_ENV else None


@pytest.mark.skipif(
    _STOCK_GAP is None or not _STOCK_GAP.is_file(),
    reason="set JA2_STOCK_GAP to a stock 250_027.gap to run",
)
def test_stock_gap_roundtrips_byte_exact():
    raw = _STOCK_GAP.read_bytes()
    pairs = gap.parse_gap_bytes(raw)
    assert pairs == [(2, 73), (978, 1206)]
    assert gap.gaps_to_bytes(pairs) == raw
