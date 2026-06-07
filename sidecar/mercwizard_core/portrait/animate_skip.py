"""The 'skip animation' default — produces 7 dummy animation sub-frames.

CRITICAL CONTRACT: 'skip' is a UX label, NEVER a binary-format choice. The
SmallFace STI MUST always have exactly 8 sub-frames: one 48×43 base + four
eye sub-frames + three mouth sub-frames. The eye sub-frames must share a
size; the mouth sub-frames must share a size. Default vanilla sizes are
17×6 / 14×6, but the engine reads each STI's sub-frame size from the
ETRLE per-frame header (Faces.cpp:480-481) — so non-vanilla sizes (e.g.
Vengeance's 31×13 / 32×21, or a Create-flow user's drag-rect selection)
work fine as long as they're internally consistent and at least 1×1.

The JA2 engine has NO fallback path for fewer frames. Writing < 8 frames
causes garbled rendering or an out-of-bounds access violation crash.

For 'skip' mode, we generate 7 dummy animation sub-frames that are just
crops of the base portrait at the eye/mouth regions. The engine's animation
cycle plays, but visually nothing changes — the merc is static in-game but
the STI is valid and won't crash.

This module's `make_skip_frames()` always returns exactly 7 PIL Images.
Sub-frame sizes default to vanilla 17×6 / 14×6 but the caller can override
via the bounding-box `w` / `h` fields (Slice B drag-rect UX).
"""
from __future__ import annotations

from dataclasses import dataclass

from PIL import Image


# Vanilla 1.13 conventions. The engine reads sizes from the STI per-frame
# header, so non-vanilla mods (Vengeance: 31×13 / 32×21) work too.
EYE_SUBFRAME_SIZE = (17, 6)
MOUTH_SUBFRAME_SIZE = (14, 6)


@dataclass(frozen=True)
class BoundingBox:
    """Region of interest in 48×43 SmallFace space.

    `x` / `y` are the top-left of the region, written to MercProfiles.xml's
    `usEyesX` / `usEyesY` (or mouth equivalents). The engine renders the
    animation strip there at runtime.

    `w` / `h` are the STI sub-frame size for this region. If > 0, the wizard
    crops a (w, h) window at (x, y) and writes that as the sub-frame size in
    the STI header — letting the user pick a sub-frame shape via the
    drag-rect UI. If both are 0 (the default), the canonical vanilla size
    for the region is used (17×6 for eyes, 14×6 for mouths).
    """
    x: int
    y: int
    w: int = 0
    h: int = 0


# Vanilla defaults if eye/mouth detection fails (from compile_merc.py ~line 65).
# w/h here would only be honored by callers that pass them through to
# make_skip_frames; the public callers default to canonical sizes.
DEFAULT_EYE_BOX = BoundingBox(x=10, y=8)
DEFAULT_MOUTH_BOX = BoundingBox(x=7, y=28)


def _clamp_crop(x: int, y: int, sub_w: int, sub_h: int, canvas_w: int, canvas_h: int) -> tuple[int, int]:
    """Clamp the (x, y) crop origin so the sub-region stays within the canvas."""
    max_x = max(0, canvas_w - sub_w)
    max_y = max(0, canvas_h - sub_h)
    return max(0, min(x, max_x)), max(0, min(y, max_y))


def _resolve_subframe_size(
    box: BoundingBox, canonical: tuple[int, int], canvas_size: tuple[int, int]
) -> tuple[int, int]:
    """Pick the sub-frame size for this region.

    Returns the box's (w, h) if both are positive — clamped to fit inside the
    48×43 canvas. Otherwise returns the canonical (17×6 or 14×6).
    """
    if box.w > 0 and box.h > 0:
        cw, ch = canvas_size
        return (min(box.w, cw), min(box.h, ch))
    return canonical


def make_skip_frames(
    base_48x43: Image.Image,
    eye_box: BoundingBox = DEFAULT_EYE_BOX,
    mouth_box: BoundingBox = DEFAULT_MOUTH_BOX,
) -> list[Image.Image]:
    """Return exactly 7 dummy animation sub-frames (eye×4 + mouth×3).

    The caller combines these with the base 48×43 portrait to form the
    canonical 8-frame STI. The engine sees a valid animation strip but each
    frame is identical to the corresponding region of the base — so the
    merc plays the cycle but visually doesn't move.

    Sub-frame sizes default to vanilla 17×6 / 14×6 but can be overridden via
    the bounding box's `w` / `h` fields (Slice B drag-rect UX).
    """
    assert base_48x43.size == (48, 43), (
        f"Base must be 48×43; got {base_48x43.size}. The 8-frame STI requires "
        "an exact 48×43 base or the engine crashes."
    )

    canvas_w, canvas_h = base_48x43.size
    eye_size = _resolve_subframe_size(eye_box, EYE_SUBFRAME_SIZE, base_48x43.size)
    mouth_size = _resolve_subframe_size(mouth_box, MOUTH_SUBFRAME_SIZE, base_48x43.size)

    ex, ey = _clamp_crop(eye_box.x, eye_box.y, eye_size[0], eye_size[1], canvas_w, canvas_h)
    eye_sub = base_48x43.crop((ex, ey, ex + eye_size[0], ey + eye_size[1]))

    mx, my = _clamp_crop(mouth_box.x, mouth_box.y, mouth_size[0], mouth_size[1], canvas_w, canvas_h)
    mouth_sub = base_48x43.crop((mx, my, mx + mouth_size[0], my + mouth_size[1]))

    assert eye_sub.size == eye_size, f"Eye sub-frame {eye_sub.size} != {eye_size}"
    assert mouth_sub.size == mouth_size, f"Mouth sub-frame {mouth_sub.size} != {mouth_size}"

    frames = [
        eye_sub.copy(),
        eye_sub.copy(),
        eye_sub.copy(),
        eye_sub.copy(),
        mouth_sub.copy(),
        mouth_sub.copy(),
        mouth_sub.copy(),
    ]
    assert len(frames) == 7, f"Must return exactly 7 frames; got {len(frames)}"
    return frames
