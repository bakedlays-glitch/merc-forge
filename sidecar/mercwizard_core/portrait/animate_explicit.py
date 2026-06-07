"""Explicit animation frames — user/bundle-provided sub-frame PNGs.

The third mode alongside `animate_skip` (static dummy frames) and
`animate_procedural` (programmatic blink/talk):

  - The Create flow may take optional eye/mouth variant uploads from the
    artist, so a fresh merc can blink in-game.
  - The Import flow may receive `anim_eye_*.png` / `anim_mouth_*.png` in
    the .wmerc bundle, preserving the source install's hand-authored
    animation pixels across a round-trip.

Input contract:

- `eye_sources`: 1..4 PIL Images. Each may be exactly 17x6 (canonical eye
  sub-frame size, used verbatim) OR larger (treated as a full-face variant
  of the same composition as the base portrait — the wizard crops a 17x6
  window at `eye_box.x/y`, matching where the engine renders the strip).
- `mouth_sources`: 1..3 PIL Images, canonical 14x6 or larger.

Auto-pad rules when fewer than max frames are supplied:

  Eyes (4 engine slots; slot 3 is the "hardware duplicate" of slot 1):
    1 source : all 4 slots get the same frame  (visually static eye)
    2 sources: slots 1,3 = source[0]; slots 2,4 = source[1]
    3 sources: slot 1=s[0], slot 2=s[1], slot 3=s[0] (engine dup), slot 4=s[2]
    4 sources: 1=s[0], 2=s[1], 3=s[2], 4=s[3]  (rare — overrides the
              "slot 3 is dup of 1" engine convention; engine still renders
              whatever bytes are in slot 3)

  Mouths (3 engine slots, all visually distinct):
    1 source : all 3 slots get the same frame  (visually static mouth)
    2 sources: slot 1=s[0], slot 2=s[1], slot 3=s[0]
    3 sources: each slot gets its own

Returns 7 PIL Images in the canonical order [eye*4, mouth*3] at the
exact canonical sizes. Assertion-checked.
"""
from __future__ import annotations

from PIL import Image

from .animate_skip import (
    EYE_SUBFRAME_SIZE,
    MOUTH_SUBFRAME_SIZE,
    BoundingBox,
    _clamp_crop,
)


def _coerce_to_subframe(
    src: Image.Image,
    target_size: tuple[int, int],
    crop_box: BoundingBox,
) -> Image.Image:
    """Coerce `src` to `target_size`.

    Three cases:
      - src exactly matches target → return verbatim
      - src is larger than target → crop a target-sized window at crop_box
      - src is smaller than target → ValueError (no upscale; STI sub-frames
        encode at native size and any upscale produces visible blurring)

    Note: `target_size` is the SUB-FRAME size for this region (e.g. 17x6
    for vanilla eyes, 31x13 for Vengeance Eskimo eyes). It's derived from
    the first source frame supplied, not from a hardcoded canonical value.
    The JA2 engine reads `usEyesWidth/Height` from the STI's per-frame
    header (Faces.cpp:480) so any consistent size works in-game.
    """
    sw, sh = src.size
    tw, th = target_size

    if (sw, sh) == (tw, th):
        return src

    if sw < tw or sh < th:
        raise ValueError(
            f"Animation source {src.size} is smaller than target "
            f"{target_size}. All eye/mouth frames in one STI must share a "
            "size (engine reads it from frame 1's header). Either supply "
            "all sources at the same size, or supply a full-face variant "
            "(>= 48x43) so the wizard can crop a target-sized window."
        )

    x, y = _clamp_crop(crop_box.x, crop_box.y, tw, th, sw, sh)
    return src.crop((x, y, x + tw, y + th))


def _pad_to_count(coerced: list[Image.Image], canonical_count: int, kind: str) -> list[Image.Image]:
    """Auto-pad `coerced` (1..canonical_count entries) up to `canonical_count`.

    Engine-aware: for eyes (canonical_count=4) we respect the "slot 3 is
    the hardware duplicate of slot 1" convention when the user gave fewer
    than 4 sources.
    """
    n = len(coerced)
    if n == canonical_count:
        return list(coerced)
    if n == 0:
        raise ValueError(f"Need at least one {kind} source frame, got zero")

    if kind == "eye":
        # 4 engine slots: [slot1, slot2, slot3=dup-of-slot1, slot4]
        if n == 1:
            return [coerced[0].copy() for _ in range(4)]
        if n == 2:
            return [coerced[0].copy(), coerced[1].copy(), coerced[0].copy(), coerced[1].copy()]
        if n == 3:
            return [coerced[0].copy(), coerced[1].copy(), coerced[0].copy(), coerced[2].copy()]
        # n >= 4: use first four (extras ignored)
        return [c.copy() for c in coerced[:4]]

    # mouth: 3 slots, all distinct
    if n == 1:
        return [coerced[0].copy() for _ in range(3)]
    if n == 2:
        return [coerced[0].copy(), coerced[1].copy(), coerced[0].copy()]
    # n >= 3: use first three
    return [c.copy() for c in coerced[:3]]


def make_explicit_frames(
    base_48x43: Image.Image,
    eye_sources: list[Image.Image],
    mouth_sources: list[Image.Image],
    eye_box: BoundingBox,
    mouth_box: BoundingBox,
) -> list[Image.Image]:
    """Produce 7 sub-frames from user-supplied images.

    Sub-frame size is derived from the FIRST source per region (not
    hardcoded). All eye sources must agree on a size after coercion;
    same for mouths. This lets Vengeance-style 31x13 eyes round-trip
    losslessly while vanilla-style 17x6 sources still work.

    See module docstring for the input contract and auto-pad rules.
    """
    assert base_48x43.size == (48, 43), (
        f"Base must be 48x43; got {base_48x43.size}"
    )
    if not eye_sources:
        raise ValueError("Need at least one eye source frame")
    if not mouth_sources:
        raise ValueError("Need at least one mouth source frame")

    # Target sub-frame size:
    #   - First source is FULL-FACE-OR-LARGER (≥ 48 in width AND ≥ 43 in
    #     height) → user is supplying a face variant; crop a vanilla-
    #     canonical 17x6 / 14x6 window at the eye/mouth box coords.
    #   - First source is SUB-FRAME-SIZED (both dimensions strictly less
    #     than the 48x43 face) → use its size verbatim. Vanilla 17x6 /
    #     14x6 hits this branch, as does Vengeance-style 31x13 / 32x21.
    #   - First source is AMBIGUOUS (50x40, 60x10, …) → not a sub-frame
    #     and not full-face. We treat this as full-face for safety: any
    #     source wider OR taller than the canonical sub-frame likely
    #     contains a face the user wants cropped, not a malformed
    #     sub-frame the user wants verbatim. Sub-canonical-but-not-
    #     sub-frame would have been caught earlier by
    #     `_coerce_to_subframe`'s "smaller than target" guard once the
    #     target is set; this picks the more recoverable target.
    def _pick_target(first: Image.Image, vanilla: tuple[int, int]) -> tuple[int, int]:
        w, h = first.size
        vw, vh = vanilla
        # Sub-frame-sized: both dimensions strictly less than the face.
        if w < 48 and h < 43:
            return (w, h)
        # Anything else: assume it's a face variant — crop at vanilla
        # canonical regardless of exact dimensions.
        return vanilla

    eye_target = _pick_target(eye_sources[0], EYE_SUBFRAME_SIZE)
    mouth_target = _pick_target(mouth_sources[0], MOUTH_SUBFRAME_SIZE)

    eye_coerced = [_coerce_to_subframe(s, eye_target, eye_box) for s in eye_sources]
    mouth_coerced = [_coerce_to_subframe(s, mouth_target, mouth_box) for s in mouth_sources]

    eye_frames = _pad_to_count(eye_coerced, 4, "eye")
    mouth_frames = _pad_to_count(mouth_coerced, 3, "mouth")

    frames = eye_frames + mouth_frames
    assert len(frames) == 7, f"Must return exactly 7 frames; got {len(frames)}"
    for i, f in enumerate(eye_frames):
        assert f.size == eye_target, (
            f"Eye frame {i + 1}: expected {eye_target}, got {f.size}"
        )
    for i, f in enumerate(mouth_frames):
        assert f.size == mouth_target, (
            f"Mouth frame {i + 1}: expected {mouth_target}, got {f.size}"
        )
    return frames
