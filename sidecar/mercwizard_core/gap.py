"""JA2 ``.gap`` lip-sync sidecar generation for voice clips.

JA2 plays a merc's speech from ``Speech/<usVoiceIndex:03d>_<quote:03d>.wav``
and pairs each clip with a same-named ``.gap`` file describing the *silent*
intervals in the audio. The engine uses it to drive the talking-face
animation: the mouth animates while the merc is speaking and freezes shut
during a gap.

**Binary format** (reverse-engineered from ``Tactical/GAP.cpp`` +
``faces.h`` and verified against 5,169 stock ``.gap`` files):

    A flat array of ``(start, end)`` pairs, each two little-endian uint32s
    (8 bytes per pair). No header, no count field — the engine
    (``AudioGapListInit``) reads ``Start, End, Start, End, ...`` until EOF.
    ``start``/``end`` are **milliseconds** from the start of the clip
    (``PollAudioGap`` compares them against ``SoundGetPosition``, which
    returns ms). Pairs must be ascending and non-overlapping — the engine
    only ever walks the list forward.

This module is **stdlib-only** on purpose: ``numpy`` is not a declared
dependency and ``audioop`` was removed in Python 3.13 (the project targets
``>=3.10``). We decode PCM WAV with ``wave`` + ``array`` and emit the gap
bytes with ``struct``. Anything we can't decode (ogg/mp3/ADPCM/corrupt)
yields no gap — the clip still plays, just without lip-sync.
"""
from __future__ import annotations

import io
import logging
import struct
import sys
import wave
from array import array
from pathlib import Path

logger = logging.getLogger(__name__)


# --- silence-detection tuning ------------------------------------------------
# Validated against the stock JA2 speech corpus: these recover the real
# internal pauses without over-segmenting. They live as named constants so
# they're easy to tweak without touching the detection logic.
WINDOW_MS = 10                 # analysis window for the peak envelope
THRESH_FRAC_OF_PEAK = 0.07     # silence threshold, relative to the clip's own peak
FLOOR_FRAC_OF_FULLSCALE = 0.015  # absolute floor, so a near-silent clip isn't all-gap
MIN_GAP_MS = 50                # ignore silences shorter than this (consonant stops, etc.)
MERGE_MS = 30                  # fuse two gaps separated by less than this


def gaps_to_bytes(gaps: list[tuple[int, int]]) -> bytes:
    """Serialize ``(start_ms, end_ms)`` pairs to the engine's ``.gap`` format."""
    out = bytearray()
    for start, end in gaps:
        out += struct.pack("<II", int(start), int(end))
    return bytes(out)


def parse_gap_bytes(data: bytes) -> list[tuple[int, int]]:
    """Parse ``.gap`` bytes back into ``(start_ms, end_ms)`` pairs.

    Mirrors the engine's EOF-terminated read: any trailing partial pair is
    ignored. Provided for tests/round-trip checks and future gap reading.
    """
    n = (len(data) // 8) * 8
    if n == 0:
        return []
    vals = struct.unpack("<%dI" % (n // 4), data[:n])
    return [(vals[i], vals[i + 1]) for i in range(0, len(vals), 2)]


def _window_peaks(data: bytes, win_ms: int):
    """Decode PCM WAV bytes into a per-window peak envelope.

    Returns ``(rate, win_frames, nframes, fullscale, peaks)`` or ``None`` if
    the bytes aren't decodable integer PCM (ogg/mp3/ADPCM/float/24-bit/corrupt).
    ``peaks[k]`` is the max sample magnitude (native units) over window ``k``.
    """
    try:
        w = wave.open(io.BytesIO(data), "rb")
    except Exception:  # noqa: BLE001 — non-PCM (ADPCM/float) raises here, treat as undecodable
        return None
    try:
        ch = w.getnchannels()
        width = w.getsampwidth()
        rate = w.getframerate()
        nframes = w.getnframes()
        if ch <= 0 or rate <= 0 or nframes <= 0:
            return None
        raw = w.readframes(nframes)
    except Exception:  # noqa: BLE001
        return None
    finally:
        try:
            w.close()
        except Exception:  # noqa: BLE001
            pass

    if width == 1:        # 8-bit PCM is UNSIGNED (0..255), centered at 128
        arr = array("B")
        arr.frombytes(raw)
        fullscale, signed, center = 128, False, 128
    elif width == 2:      # 16-bit signed little-endian
        arr = array("h")
        if arr.itemsize != 2:
            return None
        arr.frombytes(raw)
        fullscale, signed, center = 32768, True, 0
    elif width == 4:      # 32-bit signed little-endian
        arr = array("i")
        if arr.itemsize != 4:
            return None
        arr.frombytes(raw)
        fullscale, signed, center = 2 ** 31, True, 0
    else:
        return None       # 24-bit or exotic — skip permissively
    if signed and sys.byteorder == "big":
        arr.byteswap()

    win_frames = max(1, round(rate * win_ms / 1000.0))
    span = win_frames * ch
    peaks: list[int] = []
    n = len(arr)
    i = 0
    while i < n:
        sl = arr[i:i + span]
        i += span
        if not sl:
            break
        mx = max(sl)
        mn = min(sl)
        if signed:
            pk = mx if mx >= -mn else -mn
        else:
            hi = mx - center
            lo = center - mn
            pk = hi if hi >= lo else lo
        peaks.append(pk)
    return rate, win_frames, nframes, fullscale, peaks


def detect_silence_gaps(
    data: bytes,
    *,
    win_ms: int = WINDOW_MS,
    thresh_frac: float = THRESH_FRAC_OF_PEAK,
    floor_frac: float = FLOOR_FRAC_OF_FULLSCALE,
    min_gap_ms: int = MIN_GAP_MS,
    merge_ms: int = MERGE_MS,
) -> list[tuple[int, int]] | None:
    """Find silent intervals in PCM WAV bytes.

    Returns a list of ascending, non-overlapping ``(start_ms, end_ms)`` silence
    intervals, or ``None`` if the audio can't be decoded (ogg/mp3/ADPCM/corrupt).
    An empty list means the clip decoded fine but contains no qualifying silence.
    """
    decoded = _window_peaks(data, win_ms)
    if decoded is None:
        return None
    rate, win_frames, nframes, fullscale, peaks = decoded
    if not peaks:
        return []

    dur_ms = int(round(nframes * 1000.0 / rate))
    clip_peak = max(peaks)
    thresh = max(thresh_frac * clip_peak, floor_frac * fullscale)

    def win_ms_at(k: int) -> int:
        return int(round(k * win_frames * 1000.0 / rate))

    # Collect raw silent runs as [start_window, end_window) index spans.
    runs: list[tuple[int, int]] = []
    k = 0
    m = len(peaks)
    while k < m:
        if peaks[k] < thresh:
            j = k
            while j < m and peaks[j] < thresh:
                j += 1
            runs.append((k, j))
            k = j
        else:
            k += 1

    # Convert to ms, merge near-adjacent runs, drop sub-threshold-duration ones.
    gaps_ms = [[win_ms_at(a), min(win_ms_at(b), dur_ms)] for a, b in runs]
    merged: list[list[int]] = []
    for g in gaps_ms:
        if merged and g[0] - merged[-1][1] < merge_ms:
            merged[-1][1] = g[1]
        else:
            merged.append(g)

    out: list[tuple[int, int]] = []
    for start, end in merged:
        start = max(0, start)
        end = min(end, dur_ms)
        if end - start >= min_gap_ms:
            out.append((start, end))
    return out


def _gap_path_for(clip_path: Path) -> Path:
    """The ``.gap`` path beside a clip.

    The engine (``GAP.cpp``) swaps the extension at the *first* ``.`` — e.g.
    ``250_027.wav`` -> ``250_027.gap``. ``with_suffix`` swaps the *last*
    extension, which is identical for the single-dot ``<idx>_<bark>.wav`` names
    JA2 speech actually uses. For a multi-dot name (``a.b.wav``) the two diverge
    (``a.b.gap`` here vs ``a.gap`` in-engine), but the engine only ever plays
    barks named ``<idx>_<quote>.wav`` — it never loads a multi-dot file as
    speech, so such a gap would be unused regardless. The wizard derives the gap
    name the same way (last-dot swap) on write and delete, so it stays
    self-consistent.
    """
    return clip_path.with_suffix(".gap")


def write_gap_beside(clip_path: Path, data: bytes) -> Path | None:
    """Best-effort: write a ``.gap`` lip-sync sidecar next to ``clip_path``.

    - Decodable PCM ``.wav`` with >=1 silence interval -> writes ``<stem>.gap``
      and returns its path.
    - Decodable but silent-free, or undecodable (ogg/mp3/ADPCM/corrupt), or a
      non-``.wav`` clip -> writes nothing and removes any *stale* same-named
      ``.gap`` (so a previous clip's silence map can't mis-sync this one);
      returns ``None``.

    Never raises: a clip without a ``.gap`` still plays — it just won't lip-sync.
    """
    # Never operate on a .gap path itself: `_gap_path_for` would resolve to the
    # same file and the stale-removal branch below would unlink our own input.
    if clip_path.suffix.lower() == ".gap":
        return None
    try:
        gap_path = _gap_path_for(clip_path)
        gaps = detect_silence_gaps(data) if clip_path.suffix.lower() == ".wav" else None
        if gaps:
            gap_path.write_bytes(gaps_to_bytes(gaps))
            logger.debug("voice gap: wrote %s (%d gaps)", gap_path.name, len(gaps))
            return gap_path
        # Nothing to record — drop any stale sidecar so it can't mis-sync.
        if gap_path.exists():
            gap_path.unlink()
            logger.debug("voice gap: removed stale %s (no gaps for new clip)", gap_path.name)
        else:
            logger.debug("voice gap: no gap for %s (%s)", clip_path.name,
                         "no silence" if gaps == [] else "undecodable/non-wav")
        return None
    except Exception:  # noqa: BLE001 — lip-sync generation must never break a clip write
        logger.debug("voice gap: generation failed for %s", clip_path.name, exc_info=True)
        return None
