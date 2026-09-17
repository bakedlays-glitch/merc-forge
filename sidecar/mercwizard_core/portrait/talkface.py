"""Build the distinct 90x100 animated face used by on-map RPC dialogue."""
from __future__ import annotations

import io
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageChops

from .animate_skip import BoundingBox
from .sizes import fit_to_size
from .sti import write_animated_face_sti

TALKFACE_SIZE = (90, 100)


@dataclass(frozen=True)
class TalkFaceBuild:
    base: Image.Image
    frames: list[Image.Image]
    eyes_xy: tuple[int, int]
    mouth_xy: tuple[int, int]
    animated_eyes: bool
    animated_mouth: bool


def _load(data: bytes) -> Image.Image:
    return Image.open(io.BytesIO(data)).convert("RGBA")


def _fit(img: Image.Image) -> Image.Image:
    return fit_to_size(img, TALKFACE_SIZE)


def _map_small_box(box: BoundingBox) -> tuple[int, int, int, int]:
    """Map a 48x43 crop coordinate through the common square source crop."""
    x, y, w, h = box.x, box.y, max(1, box.w), max(1, box.h)
    crop48_h = 43.0 / 48.0
    top48 = (1.0 - crop48_h) / 2.0
    crop_t_w = TALKFACE_SIZE[0] / TALKFACE_SIZE[1]
    left_t = (1.0 - crop_t_w) / 2.0

    def to_master(px: float, py: float) -> tuple[float, float]:
        return px / 48.0, top48 + (py / 43.0) * crop48_h

    def to_target(mx: float, my: float) -> tuple[float, float]:
        return (
            (mx - left_t) / crop_t_w * TALKFACE_SIZE[0],
            my * TALKFACE_SIZE[1],
        )

    mx0, my0 = to_master(x, y)
    mx1, my1 = to_master(x + w, y + h)
    tx0, ty0 = to_target(mx0, my0)
    tx1, ty1 = to_target(mx1, my1)
    bx = max(0, min(89, round(tx0)))
    by = max(0, min(99, round(ty0)))
    bw = max(1, min(90 - bx, round(tx1 - tx0)))
    bh = max(1, min(100 - by, round(ty1 - ty0)))
    return bx, by, bw, bh


def _changed_box(base: Image.Image, variants: list[Image.Image], fallback: tuple[int, int, int, int]) -> tuple[int, int, int, int]:
    boxes: list[tuple[int, int, int, int]] = []
    base_rgb = base.convert("RGB")
    for variant in variants:
        diff = ImageChops.difference(base_rgb, variant.convert("RGB"))
        # Max-channel difference avoids a luminance conversion hiding a strong
        # change in only one channel.
        mask = diff.point(lambda p: 255 if p > 20 else 0).convert("L")
        bbox = mask.getbbox()
        if bbox:
            boxes.append(bbox)
    if not boxes:
        return fallback
    x0 = max(0, min(b[0] for b in boxes) - 2)
    y0 = max(0, min(b[1] for b in boxes) - 2)
    x1 = min(90, max(b[2] for b in boxes) + 2)
    y1 = min(100, max(b[3] for b in boxes) + 2)
    return x0, y0, max(1, x1 - x0), max(1, y1 - y0)


def _pad(images: list[Image.Image], count: int, *, eyes: bool) -> list[Image.Image]:
    if not images:
        return []
    if eyes:
        if len(images) == 1:
            return [images[0]] * 4
        if len(images) == 2:
            return [images[0], images[1], images[0], images[1]]
        if len(images) == 3:
            return [images[0], images[1], images[0], images[2]]
        return images[:4]
    if len(images) == 1:
        return [images[0]] * 3
    if len(images) == 2:
        return [images[0], images[1], images[0]]
    return images[:count]


def _frames_for_region(
    base: Image.Image,
    source_bytes: list[bytes] | None,
    fallback_box: tuple[int, int, int, int],
    *,
    count: int,
    eyes: bool,
) -> tuple[list[Image.Image], tuple[int, int], bool]:
    if not source_bytes:
        x, y, w, h = fallback_box
        return [Image.new("RGBA", (w, h), (0, 0, 0, 0)) for _ in range(count)], (x, y), False

    raw = [_load(data) for data in source_bytes]
    full_face = raw[0].width >= 48 and raw[0].height >= 43
    if full_face:
        variants = [_fit(img) for img in raw]
        box = _changed_box(base, variants, fallback_box)
    else:
        variants = raw
        box = fallback_box
    x, y, w, h = box

    region_frames: list[Image.Image] = []
    base_crop = base.crop((x, y, x + w, y + h)).convert("RGB")
    for variant in variants:
        if full_face:
            crop = variant.crop((x, y, x + w, y + h)).convert("RGB")
            diff = ImageChops.difference(base_crop, crop)
            mask = diff.point(lambda p: 255 if p > 6 else 0).convert("L")
            rgba = crop.convert("RGBA")
            rgba.putalpha(mask)
            region_frames.append(rgba)
        else:
            region_frames.append(variant.resize((w, h), Image.Resampling.LANCZOS).convert("RGBA"))
    return [f.copy() for f in _pad(region_frames, count, eyes=eyes)], (x, y), True


def build_talkface(
    source_png_bytes: bytes,
    *,
    explicit_eye_pngs: list[bytes] | None,
    explicit_mouth_pngs: list[bytes] | None,
    small_eye_box: BoundingBox,
    small_mouth_box: BoundingBox,
) -> TalkFaceBuild:
    base = _fit(_load(source_png_bytes))
    eye_frames, eyes_xy, animated_eyes = _frames_for_region(
        base, explicit_eye_pngs, _map_small_box(small_eye_box), count=4, eyes=True,
    )
    mouth_frames, mouth_xy, animated_mouth = _frames_for_region(
        base, explicit_mouth_pngs, _map_small_box(small_mouth_box), count=3, eyes=False,
    )
    frames = eye_frames + mouth_frames
    if len(frames) != 7:
        raise ValueError(f"Talk face needs 7 animation frames; got {len(frames)}")
    return TalkFaceBuild(base, frames, eyes_xy, mouth_xy, animated_eyes, animated_mouth)


def write_talkface(path: Path, built: TalkFaceBuild) -> None:
    if built.base.size != TALKFACE_SIZE:
        raise ValueError(f"Talk face base must be {TALKFACE_SIZE}; got {built.base.size}")
    write_animated_face_sti(path, built.base, built.frames)
