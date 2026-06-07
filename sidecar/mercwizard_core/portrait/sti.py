"""ETRLE-encoded STI writers for the 4 canonical portrait sizes.

Uses vendored ja2py for the binary format. We enforce JA2-specific rules
the ja2py library doesn't know about:
  - The 8-frame layout for SmallFace (assertion-checked)
  - Canonical sub-frame sizes (17×6 × 4 eye frames + 14×6 × 3 mouth frames)
  - Shared 255-color palette across all sub-frames (single palette object)
  - rawmode='RGB' on palette objects (the ja2py interleaving fix)
  - (0,0,0) anchor pixel at (0, h-1) for palette index 0 (transparent)
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Optional

from PIL import Image, ImagePalette

# Add the vendored ja2py to the import path
_THIS_DIR = Path(__file__).parent
_SIDECAR_ROOT = _THIS_DIR.parent.parent  # mercwizard_core/portrait → sidecar/
if str(_SIDECAR_ROOT) not in sys.path:
    sys.path.insert(0, str(_SIDECAR_ROOT))

from ja2py.content import Images8Bit, SubImage8Bit
from ja2py.fileformats.Sti import save_8bit_sti, load_8bit_sti

from .quantize import quantize_with_anchor, quantize_against_palette


# Canonical sub-frame sizes (immutable engine contract)
SMALLFACE_BASE_SIZE = (48, 43)
EYE_SUBFRAME_SIZE = (17, 6)
MOUTH_SUBFRAME_SIZE = (14, 6)
CANONICAL_ANIM_SIZES = [
    EYE_SUBFRAME_SIZE,    # frame 1
    EYE_SUBFRAME_SIZE,    # frame 2
    EYE_SUBFRAME_SIZE,    # frame 3
    EYE_SUBFRAME_SIZE,    # frame 4
    MOUTH_SUBFRAME_SIZE,  # frame 5
    MOUTH_SUBFRAME_SIZE,  # frame 6
    MOUTH_SUBFRAME_SIZE,  # frame 7
]


def _build_image_palette(p_img: Image.Image) -> ImagePalette.ImagePalette:
    """Build a ja2py-compatible ImagePalette from a P-mode image's palette.

    Critical: the FIRST argument to `ImagePalette.ImagePalette()` is `mode`,
    not `rawmode`. PIL leaves `rawmode=None` by default. ja2py's
    `_palette_to_bytes()` (Sti.py:298-309) checks `palette.rawmode` — if
    falsy, it treats the bytes as planar (RRR…GGG…BBB) and reorders into
    rainbow garbage. Set `rawmode="RGB"` explicitly so ja2py takes the
    no-reorder branch and the bytes pass through correctly.

    Per-frame palette `rawmode="RGB"` fixes elsewhere in this module are
    incidental — `save_8bit_sti` reads `ja2_images.palette`, the shared
    palette built here, not the sub-image palettes.
    """
    pal_bytes = bytes(p_img.getpalette())[: 256 * 3]
    # Pad to 768 bytes if shorter
    if len(pal_bytes) < 768:
        pal_bytes = pal_bytes + b"\x00" * (768 - len(pal_bytes))
    palette = ImagePalette.ImagePalette("RGB", pal_bytes)
    palette.rawmode = "RGB"  # defeats ja2py's planar-bytes reorder
    return palette


def _build_palette_source(
    base: Image.Image, anim_frames: list[Image.Image]
) -> Image.Image:
    """Tile `base` + `anim_frames` onto a single RGBA canvas so a single
    quantize pass produces a 255-color palette that covers every pixel.

    Skip-mode and procedural-mode anim frames are derived from the base's
    color universe and don't strictly need this — but explicit-mode
    (hand-painted blink frames, bundle-supplied animation, AI-generated
    variants) routinely has colors the base doesn't, and quantize-against-
    base-palette would map those to the nearest neighbor and lose detail.

    Composite layout: base on the left (48x43), anim frames stacked
    vertically to its right. Transparent gutters where the anim frames
    don't fill — those force palette index 0 = (0,0,0), unused by anim.
    """
    bw, bh = base.size
    max_anim_w = max((f.size[0] for f in anim_frames), default=0)
    total_anim_h = sum(f.size[1] for f in anim_frames)
    canvas_w = bw + max_anim_w
    canvas_h = max(bh, total_anim_h)
    canvas = Image.new("RGBA", (canvas_w, canvas_h), (0, 0, 0, 0))
    canvas.paste(base, (0, 0))
    y = 0
    for f in anim_frames:
        canvas.paste(f, (bw, y))
        y += f.size[1]
    return canvas


def write_smallface_sti(
    out_path: Path,
    base_48x43: Image.Image,
    anim_frames_7: list[Image.Image],
) -> None:
    """Write the 8-frame 48x43 SmallFace STI.

    The 7 animation frames must match the canonical sizes exactly. Caller is
    expected to use animate_skip.make_skip_frames() or
    animate_explicit.make_explicit_frames() to produce them.

    Palette is computed across the **union** of base + all 7 anim frames
    (via `_build_palette_source`) so colors unique to the anim frames
    survive. This matters for explicit-mode authoring where the artist
    paints blink variations whose colors don't appear elsewhere in the
    base portrait.

    Raises:
        AssertionError: If sizes or frame count are wrong (intentionally
            loud; an invalid STI written to disk would crash the game).
    """
    assert base_48x43.size == SMALLFACE_BASE_SIZE, (
        f"Base frame must be {SMALLFACE_BASE_SIZE}; got {base_48x43.size}. "
        "The 8-frame STI requires an exact 48x43 base or the engine crashes."
    )
    assert len(anim_frames_7) == 7, (
        f"Need exactly 7 animation frames; got {len(anim_frames_7)}. "
        "The engine has no path for fewer frames - it'll garble or crash."
    )
    # Sub-frame size is mod-defined (Faces.cpp:480-481 reads
    # usEyesWidth/Height from frame 1's ETRLE header). All 4 eye frames
    # must share a size and all 3 mouth frames must share a size — the
    # engine reads the size once per region and reuses it across the
    # animation cycle.
    eye_size = anim_frames_7[0].size
    mouth_size = anim_frames_7[4].size
    for i in range(4):
        assert anim_frames_7[i].size == eye_size, (
            f"Eye frame {i + 1}: expected {eye_size} (matching frame 1), "
            f"got {anim_frames_7[i].size}. All 4 eye frames must share a size."
        )
    for i in range(3):
        assert anim_frames_7[4 + i].size == mouth_size, (
            f"Mouth frame {i + 1}: expected {mouth_size} (matching frame 5), "
            f"got {anim_frames_7[4 + i].size}. All 3 mouth frames must share a size."
        )

    # Build the shared palette over the union of base + all 7 anim frames.
    palette_source = _build_palette_source(base_48x43, anim_frames_7)
    palette_p = quantize_with_anchor(palette_source)
    base_p = quantize_against_palette(base_48x43, palette_p)
    anim_p = [quantize_against_palette(f, palette_p) for f in anim_frames_7]

    # Build the ja2py Images8Bit container. Use the union-quantize palette,
    # not base_p's — base_p is `quantize_against_palette(base, palette_p)` so
    # its palette object IS palette_p's, but use palette_p explicitly for
    # readability and so future code changes don't accidentally drift.
    palette = _build_image_palette(palette_p)

    # Each SubImage8Bit needs a P-mode image whose palette getdata() returns
    # the same bytes as the shared palette. We rebuild each sub-image's
    # internal palette to match.
    def _normalize_palette(p_img: Image.Image) -> Image.Image:
        out = p_img.copy()
        out.putpalette(palette.palette, rawmode="RGB")
        if out.palette is not None:
            out.palette.rawmode = "RGB"
        return out

    base_norm = _normalize_palette(base_p)
    anim_norm = [_normalize_palette(f) for f in anim_p]

    sub_images = [SubImage8Bit(base_norm, offsets=(0, 0))]
    for f in anim_norm:
        sub_images.append(SubImage8Bit(f, offsets=(0, 0)))

    images = Images8Bit(
        sub_images,
        palette,
        width=SMALLFACE_BASE_SIZE[0],
        height=SMALLFACE_BASE_SIZE[1],
    )

    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "wb") as f:
        save_8bit_sti(images, f)


def write_static_sti(out_path: Path, img: Image.Image) -> None:
    """Write a single-frame STI (BigFace 106×122, 65Face 31×27, 33Face 15×14).

    No animation strip; just one quantized sub-image.
    """
    if img.size[0] <= 0 or img.size[1] <= 0:
        raise ValueError(f"Image must be non-empty; got {img.size}")

    p_img = quantize_with_anchor(img)
    palette = _build_image_palette(p_img)

    normalized = p_img.copy()
    normalized.putpalette(palette.palette, rawmode="RGB")
    if normalized.palette is not None:
        normalized.palette.rawmode = "RGB"

    images = Images8Bit(
        [SubImage8Bit(normalized, offsets=(0, 0))],
        palette,
        width=img.size[0],
        height=img.size[1],
    )

    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "wb") as f:
        save_8bit_sti(images, f)


def verify_smallface_sti(sti_path: Path) -> dict:
    """Read back a written STI and confirm it's a valid 8-frame layout.

    Used by tests and post-write verification. Returns structure info dict.
    Does NOT raise on layout violations — surface info, let the caller decide.

    "Valid SmallFace" is now mod-defined: 8 frames where the base is 48x43,
    all 4 eye frames share a size, and all 3 mouth frames share a size
    (Faces.cpp:480-481 reads per-region sizes from the STI per-frame
    header). The vanilla 17x6 / 14x6 is the most common case but not a
    correctness requirement — Vengeance ships 31x13 / 32x21.
    """
    with open(sti_path, "rb") as f:
        images = load_8bit_sti(f)

    frame_sizes = [sub.image.size for sub in images.images]
    eye_sizes_consistent = (
        len(frame_sizes) == 8
        and len({frame_sizes[1], frame_sizes[2], frame_sizes[3], frame_sizes[4]}) == 1
    )
    mouth_sizes_consistent = (
        len(frame_sizes) == 8
        and len({frame_sizes[5], frame_sizes[6], frame_sizes[7]}) == 1
    )
    is_smallface = (
        len(frame_sizes) == 8
        and frame_sizes[0] == SMALLFACE_BASE_SIZE
        and eye_sizes_consistent
        and mouth_sizes_consistent
    )
    return {
        "frame_count": len(frame_sizes),
        "frame_sizes": frame_sizes,
        "canvas_size": (images.width, images.height),
        "is_smallface": is_smallface,
        "is_static_single_frame": len(frame_sizes) == 1,
        # Surface the actual per-region sizes used so test/audit code can
        # report them instead of asserting the vanilla canonicals.
        "eye_subframe_size": frame_sizes[1] if eye_sizes_consistent else None,
        "mouth_subframe_size": frame_sizes[5] if mouth_sizes_consistent else None,
    }
