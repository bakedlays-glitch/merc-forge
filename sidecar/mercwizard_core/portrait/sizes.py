"""Aspect-ratio-aware crop and scaling for the 4 canonical portrait sizes.

The JA2 engine does NOT dynamically scale STIs — the four sizes must be
provided exactly:

| Name      | Size    | Used in                       |
|-----------|---------|-------------------------------|
| BigFace   | 106×122 | AIM website / bio screen      |
| SmallFace |  48×43  | Tactical HUD squad portrait   |
| 65Face    |  31×27  | Strategic map / roster        |
| 33Face    |  15×14  | Overhead map nodes            |

Aspect ratios:
- BigFace: 106÷122 ≈ 0.868 (taller than wide)
- SmallFace: 48÷43 ≈ 1.116 (wider than tall)
- 65Face: 31÷27 ≈ 1.148 (wider than tall)
- 33Face: 15÷14 ≈ 1.071 (roughly square)

Never resize one to another directly — that horizontally squashes the face
and misaligns the eye/mouth sub-frames. Always center-crop to target aspect
ratio first, then resize.
"""
from __future__ import annotations

from PIL import Image


BIGFACE = (106, 122)
SMALLFACE = (48, 43)
FACE_65 = (31, 27)
FACE_33 = (15, 14)


def center_crop(img: Image.Image, target_w: int, target_h: int) -> Image.Image:
    """Crop `img` to the target aspect ratio (target_w × target_h), centered.

    Doesn't resize — only crops. Caller should follow with `.resize()` if
    actually scaling to the target dimensions.

    Ported from MercWizard/server.py's center_crop() and
    Headless_Compiler/compile_merc.py's equivalent.
    """
    sw, sh = img.size
    if sw * target_h > sh * target_w:
        # Source is wider than target aspect — crop horizontally
        new_w = sh * target_w // target_h
        left = (sw - new_w) // 2
        return img.crop((left, 0, left + new_w, sh))
    if sw * target_h < sh * target_w:
        # Source is taller than target aspect — crop vertically
        new_h = sw * target_h // target_w
        top = (sh - new_h) // 2
        return img.crop((0, top, sw, top + new_h))
    return img  # already at target aspect


def fit_to_size(img: Image.Image, target_size: tuple[int, int]) -> Image.Image:
    """Center-crop then resize to exact target size. Returns a new image."""
    cropped = center_crop(img, *target_size)
    return cropped.resize(target_size, Image.Resampling.LANCZOS)


def make_smallface(img: Image.Image) -> Image.Image:
    """Source → 48×43 (the tactical HUD size, with animation frames)."""
    return fit_to_size(img, SMALLFACE)


def make_bigface(img: Image.Image) -> Image.Image:
    """Source → 106×122 (the AIM website hero portrait)."""
    return fit_to_size(img, BIGFACE)


def make_65face(img: Image.Image) -> Image.Image:
    """Source → 31×27 (strategic map portrait)."""
    return fit_to_size(img, FACE_65)


def make_33face(img: Image.Image) -> Image.Image:
    """Source → 15×14 (overhead map node portrait)."""
    return fit_to_size(img, FACE_33)
