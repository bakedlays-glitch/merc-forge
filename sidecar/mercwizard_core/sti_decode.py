"""Palette-aware STI → RGBA PNG decoder, shared by the FaceGear overlay
extractor and the roster portrait endpoint.

Lives outside `facegear.py` and `portrait/` so neither set of consumers
has to drag the other's dependency graph in.

Why a custom palette walk instead of `Image.convert("RGBA")`?
ja2py's `Images8Bit.images[*].image` carries a per-sub-image palette
that sometimes has a stale or garbled `rawmode` attribute. PIL's
`convert()` honors that mode when expanding palette indexes to RGB,
which silently swaps R and B for any STI whose rawmode landed as 'BGR'
instead of 'RGB'. The container-level `images.palette.palette` carries
the authoritative bytes; we resolve through that buffer directly so the
output matches what the engine renders in-game.

Frame 0 / index 0 is the engine's transparent slot — emit a fully-
transparent pixel instead of resolving against the palette. Vanilla
1.13 ships palette[0] = (0,0,0) for portraits anyway, so the difference
only matters for mods that paint over index 0 on accident.
"""
from __future__ import annotations

import io
from pathlib import Path
from typing import Optional

from PIL import Image


def decode_sti_frame_to_png(
    source: "Path | bytes",
    frame_index: int = 0,
) -> Optional[bytes]:
    """Decode one frame of an 8-bit STI to PNG bytes.

    `source` may be either a filesystem path OR raw STI bytes (e.g.
    extracted from an SLF archive). Both paths converge on the same
    ja2py decoder via `_get_filelike`, which accepts file-like objects.
    The bytes path lets us serve portraits that live inside
    `Data/Faces.slf` without round-tripping through a temp file — a user
    reported roster portraits stayed blank for vanilla mercs whose
    art lives in the SLF.

    Returns None when:
      - The data isn't a readable 8-bit or 16-bit STI.
      - The requested frame index is out of range.
      - Any I/O or decode error occurs.

    8-bit STIs are palette-resolved frame-by-frame (the vanilla face/FaceGear
    path). 16-bit STIs — used by many 1.13 / mod face packs, and previously
    unsupported here (which left those mercs blank in the roster) — are single
    RGB images in ja2py; only frame 0 is meaningful, converted straight to RGBA.
    """
    # Lazy imports: ja2py is vendored and pays a non-trivial startup cost.
    # Loading at call time keeps the rest of the sidecar lean for callers
    # that never decode an STI (e.g., MapForge atlas baker has its own
    # SLF path that doesn't use this module).
    try:
        from ja2py.fileformats.Sti import (
            is_8bit_sti, load_8bit_sti, is_16bit_sti, load_16bit_sti,
        )
    except Exception:
        return None
    try:
        if isinstance(source, (bytes, bytearray, memoryview)):
            data = bytes(source)
        else:
            with open(source, "rb") as f:
                data = f.read()

        buf = io.BytesIO(data)
        if is_8bit_sti(buf):
            buf.seek(0)
            images = load_8bit_sti(buf)
            if not images.images or frame_index >= len(images.images):
                return None
            rgba = decode_subimage_to_rgba(images, frame_index)
        else:
            buf.seek(0)
            if not is_16bit_sti(buf):
                return None
            if frame_index != 0:
                return None  # 16-bit STIs carry a single RGB image, no frames
            buf.seek(0)
            rgba = load_16bit_sti(buf).image.convert("RGBA")
        out = io.BytesIO()
        rgba.save(out, format="PNG")
        return out.getvalue()
    except Exception:
        return None


def decode_subimage_to_rgba(images, frame_index: int) -> Image.Image:
    """Lower-level helper: resolve one already-loaded Images8Bit subframe
    against the container palette and return an RGBA PIL image.

    Exposed so the FaceGear overlay extractor can attach PNG `tEXt`
    metadata (sOffsetX/Y) before encoding to bytes — that path needs the
    intermediate PIL image rather than ready-to-ship PNG bytes.

    Raises IndexError if `frame_index` is out of range; caller's
    responsibility to bounds-check first (the higher-level
    `decode_sti_frame_to_png` does this and returns None instead).
    """
    sub = images.images[frame_index]
    p_img = sub.image
    pal_bytes = bytes(images.palette.palette)
    rgba = Image.new("RGBA", p_img.size, (0, 0, 0, 0))
    pixels = []
    for idx in p_img.getdata():
        if idx == 0:
            # Engine treats palette index 0 as fully transparent.
            pixels.append((0, 0, 0, 0))
        else:
            r = pal_bytes[idx * 3]
            g = pal_bytes[idx * 3 + 1]
            b = pal_bytes[idx * 3 + 2]
            pixels.append((r, g, b, 255))
    rgba.putdata(pixels)
    return rgba
