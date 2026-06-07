"""Shared portrait compilation pipeline.

Used by both the `/portrait/compile` upload route and the `.wmerc` import
flow. Takes a single source image plus a face_index and writes all four
canonical STI files (SmallFace + 65FACE + 33FACE + BigFace), zero-padded
duplicates included.

Caller is responsible for holding `state.write_lock` — this module does
not acquire it, so the same write lock can wrap a larger transaction
(e.g. profile + EDT + portrait + voice in one atomic deploy_import).
"""
from __future__ import annotations

import io
from pathlib import Path

from PIL import Image

from .animate_explicit import make_explicit_frames
from .animate_skip import (
    DEFAULT_EYE_BOX,
    DEFAULT_MOUTH_BOX,
    BoundingBox,
    make_skip_frames,
)
from .sizes import make_33face, make_65face, make_bigface, make_smallface
from .sti import write_smallface_sti, write_static_sti


def compile_and_write_all(
    install_root: Path,
    face_index: int,
    source_png_bytes: bytes,
    skip_animation: bool = True,
    eye_box: BoundingBox = DEFAULT_EYE_BOX,
    mouth_box: BoundingBox = DEFAULT_MOUTH_BOX,
    *,
    bigface_source_png: bytes | None = None,
    explicit_eye_pngs: list[bytes] | None = None,
    explicit_mouth_pngs: list[bytes] | None = None,
) -> list[str]:
    """Compile a portrait PNG into the four canonical STIs and write them.

    Three optional kwargs let the caller supply richer authoring inputs:

      - `bigface_source_png` — alternate source for the 106x122 BigFace.
        Lets an artist author the AIM/M.E.R.C. hero portrait with different
        framing/composition than the tight 48x43 face. If absent, BigFace
        is center-cropped from `source_png_bytes` like the other sizes.

      - `explicit_eye_pngs` — 1..4 PNG byte-strings of eye-region variants.
        Each may be a pre-cropped 17x6 sub-frame OR a larger full-face
        variant (the wizard auto-crops at `eye_box`). Auto-pads if fewer
        than 4 supplied. When provided, overrides `skip_animation`.

      - `explicit_mouth_pngs` — 1..3 PNG byte-strings of mouth-region
        variants. Same auto-detect + auto-pad as eyes. When provided,
        overrides `skip_animation` for mouth slots.

    The flag matrix:
        explicit_eye+mouth absent              -> static skip frames (7 dummy crops)
        either explicit_*_pngs provided        -> explicit overrides whichever
                                                  region(s) have inputs; the other
                                                  region falls back to skip

    `skip_animation` is accepted for API compatibility but is currently always
    treated as True when no explicit frames are supplied — the legacy
    `animate_procedural` placeholder was dropped (vertical-squash + skin-tone
    fill didn't produce real-looking animation; users wanting blinks/talking
    supply explicit frames instead).

    Returns the list of file paths written. Idempotent — overwrites existing
    STIs at the same face_index. VFS-aware: writes to the mod content
    layer for the active install (Data-Vengeance for Vengeance, Data-1.13
    for vanilla, etc.).
    """
    src = Image.open(io.BytesIO(source_png_bytes)).convert("RGBA")
    # BigFace source: separate authoring overrides the cropped-from-main default.
    if bigface_source_png is not None:
        bigface_src = Image.open(io.BytesIO(bigface_source_png)).convert("RGBA")
        bigface = make_bigface(bigface_src)
    else:
        bigface = make_bigface(src)
    smallface = make_smallface(src)
    face_65 = make_65face(src)
    face_33 = make_33face(src)

    # Animation frames: explicit overrides everything when provided per-region.
    # Mixing modes is allowed — explicit eyes + skip mouths is a valid combo
    # if the artist only authored eye variants.
    using_explicit = explicit_eye_pngs is not None or explicit_mouth_pngs is not None
    if using_explicit:
        if explicit_eye_pngs:
            eye_sources = [Image.open(io.BytesIO(b)).convert("RGBA") for b in explicit_eye_pngs]
        else:
            # Default to one frame = the base SmallFace, so make_explicit_frames
            # auto-pads to a visually-static eye region (same shape as skip mode).
            eye_sources = [smallface]
        if explicit_mouth_pngs:
            mouth_sources = [Image.open(io.BytesIO(b)).convert("RGBA") for b in explicit_mouth_pngs]
        else:
            mouth_sources = [smallface]
        anim_frames = make_explicit_frames(
            smallface,
            eye_sources=eye_sources,
            mouth_sources=mouth_sources,
            eye_box=eye_box,
            mouth_box=mouth_box,
        )
    else:
        # The `skip_animation` flag is decorative now — no procedural path
        # exists. Always emit 7 dummy crops; the merc renders static unless
        # the caller supplies explicit_eye_pngs / explicit_mouth_pngs.
        _ = skip_animation
        anim_frames = make_skip_frames(smallface, eye_box=eye_box, mouth_box=mouth_box)

    # Resolve `faces/` via the install's VFS — picks the mod content layer
    # for modded installs, falls back to Data-1.13 for vanilla.
    from ..install_context import make_install_context
    ctx = make_install_context(Path(install_root))
    faces_dir = ctx.faces_dir(for_write=True)
    faces_dir.mkdir(parents=True, exist_ok=True)
    written: list[str] = []

    # SmallFace (48×43) — the 8-frame animation STI
    sf_path = faces_dir / f"{face_index}.sti"
    write_smallface_sti(sf_path, smallface, anim_frames)
    written.append(str(sf_path))
    # Zero-padded variant — only meaningful for face_index < 10 (vanilla
    # convention preserves `02.sti` distinct from `2.sti`). For >= 10 the
    # padded format equals the unpadded one — skip the duplicate write
    # (bug-sweep #82).
    if face_index < 10:
        sf_padded = faces_dir / f"{face_index:02}.sti"
        write_smallface_sti(sf_padded, smallface, anim_frames)
        written.append(str(sf_padded))

    # For subdir variants, probe whether the install uses lowercase or
    # mixed-case names (mods are inconsistent: `65FACE/`, `65Face/`,
    # `65face/`). Reuse the existing dir if one is present — don't
    # silently fork a second case-variant dir (bug-sweep #84).
    SUBDIR_VARIANTS = {
        "65FACE": ("65FACE", "65Face", "65face"),
        "33FACE": ("33FACE", "33Face", "33face"),
        "BigFaces": ("BigFaces", "bigfaces", "BIGFACES"),
    }
    for canonical, sized in (("65FACE", face_65), ("33FACE", face_33), ("BigFaces", bigface)):
        chosen = canonical
        for variant in SUBDIR_VARIANTS[canonical]:
            if (faces_dir / variant).is_dir():
                chosen = variant
                break
        target_subdir = faces_dir / chosen
        target_subdir.mkdir(parents=True, exist_ok=True)
        path = target_subdir / f"{face_index}.sti"
        write_static_sti(path, sized)
        written.append(str(path))
        if face_index < 10:
            padded_path = target_subdir / f"{face_index:02}.sti"
            write_static_sti(padded_path, sized)
            written.append(str(padded_path))

    return written
