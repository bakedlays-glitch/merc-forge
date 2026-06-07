"""FaceGear STI capacity detection + extension + per-merc overlay authoring.

FaceGear STIs (`Face_*.sti` under `<install>/Data*/faces/FACESGEAR/`) are
'universal' frame-per-merc files: the frame at index N is the overlay for
the merc with `ubFaceIndex == N`. Vanilla 1.13 typically ships ~100 frames.
Modded installs may carry more or fewer.

When a merc with `ubFaceIndex >= frame_count` tries to equip the
corresponding gear in-game, the engine's bounds check fails:

    sgp/vobject.cpp:958
    SGP_THROW_IFFALSE(hSrcVObject->usNumberOfObjects > usIndex, ...);

The exception propagates to `_FailMessage` → ERROR_SCREEN → `exit(0)`.
Verified 2026-05-16 in source.

This module:
  - detect_facegear_capacities(ctx) — enumerate Face_*.sti + frame count
  - crash_risk(infos, face_index) — STIs that would crash for this index
  - find_orphan_variants(infos) — Face_X.sti without Face_X_IMP.sti partner
  - extend_facegear_sti(path, target_count) — append transparent frames
  - inject_overlay(path, face_index, png_bytes) — replace frame[N] with a
    custom overlay (per-merc hat / goggles / gas-mask authoring)
  - extract_overlay(path, face_index) — read frame[N] back as PNG bytes
    (used by the .wmerc bundle exporter to preserve per-merc overlays)
"""
from __future__ import annotations

import logging
import struct
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Optional

import io

from PIL import Image


logger = logging.getLogger(__name__)

# Vendored ja2py for STI I/O (same import-path bootstrap as portrait/sti.py).
_THIS_DIR = Path(__file__).parent
_SIDECAR_ROOT = _THIS_DIR.parent
if str(_SIDECAR_ROOT) not in sys.path:
    sys.path.insert(0, str(_SIDECAR_ROOT))

from ja2py.content import Images8Bit, SubImage8Bit
from ja2py.fileformats.Sti import load_8bit_sti, save_8bit_sti

from .install_context import InstallContext
from .portrait.quantize import quantize_against_palette
from .portrait.sizes import center_crop
from .portrait.sti import _build_image_palette


# FaceGear overlay canvas — must match the SmallFace dimensions (48×43).
# Engine composites bottom-left over the merc's tactical portrait.
FACEGEAR_OVERLAY_SIZE = (48, 43)


def _signed_to_unsigned_u16(v: int) -> int:
    """Encode a signed INT16 into the UINT16 bytes ja2py's StiSubImageHeader
    writes. The engine reads sOffsetX/sOffsetY as INT16 — `−2` on disk is
    bytes `0xFE 0xFF` (two's complement) which ja2py expresses as 65534.
    """
    return int(v) % 65536


def _unsigned_to_signed_i16(v: int) -> int:
    """Reverse of `_signed_to_unsigned_u16` — convert ja2py's UINT16-typed
    offset back to its true INT16 value (the engine's interpretation)."""
    v = int(v)
    return v - 65536 if v >= 32768 else v


# Per MercWizard2/CLAUDE.md: stock FaceGear sub-directory variants seen
# across mods. Both .STI and _IMP.STI variants live alongside each other.
FACEGEAR_SUBDIR_VARIANTS = ("FACESGEAR", "FacesGear", "facegear")


@dataclass
class FaceGearInfo:
    """One discovered Face_*.sti file and its frame count."""
    path: Path                 # absolute path on disk
    name: str                  # filename — e.g. "Face_SunGoggles.sti"
    relative_path: str         # path relative to install root, forward slashes
    frame_count: int
    canvas_size: tuple[int, int]  # width × height of each frame
    is_imp_variant: bool       # ends with "_IMP.sti" (must mirror non-IMP)


def _iter_facegear_dirs(ctx: InstallContext) -> Iterable[Path]:
    """Yield every existing FACESGEAR directory under the install root.

    Walks all `Data*` siblings of the install root and probes the standard
    `faces/FACESGEAR/` (and case-variant) paths. Modded installs commonly
    have multiple `Data*` layers; FaceGear STIs may live in any of them.
    """
    install_root = ctx.install_root
    if not install_root.is_dir():
        return
    seen: set[Path] = set()
    for data_dir in sorted(install_root.iterdir()):
        if not data_dir.is_dir():
            continue
        name_lower = data_dir.name.lower()
        if not (name_lower == "data" or name_lower.startswith("data-") or name_lower.startswith("data_")):
            continue
        for face_base in ("faces", "Faces"):
            base = data_dir / face_base
            if not base.is_dir():
                continue
            for variant in FACEGEAR_SUBDIR_VARIANTS:
                p = base / variant
                if p.is_dir():
                    rp = p.resolve()
                    if rp not in seen:
                        seen.add(rp)
                        yield p


def detect_facegear_capacities(
    ctx: InstallContext,
    load_errors: Optional[list[dict]] = None,
) -> list[FaceGearInfo]:
    """Enumerate every `Face_*.sti` in the install and read its frame count.

    Returns one entry per file (including the `_IMP` variants — each pair
    must independently cover the merc's face index or the engine crashes).

    Phase 2.4: STI load failures used to be silently swallowed via
    `except Exception: continue` — that hid corrupt files from the UI so
    the user thought the slot didn't exist. Now we narrow the except to
    the documented raise types for `load_8bit_sti` and optionally
    accumulate the failures into `load_errors` so the route handler can
    surface them in the API response.
    """
    results: list[FaceGearInfo] = []
    install_root = ctx.install_root.resolve()
    for d in _iter_facegear_dirs(ctx):
        candidates = list(d.glob("Face_*.sti")) + list(d.glob("Face_*.STI"))
        # De-dupe by resolved path (case-insensitive collisions on Windows)
        seen_paths: set[Path] = set()
        for sti_path in sorted(candidates, key=lambda p: p.name.lower()):
            rp = sti_path.resolve()
            if rp in seen_paths:
                continue
            seen_paths.add(rp)
            try:
                with open(sti_path, "rb") as f:
                    images = load_8bit_sti(f)
                count = len(images.images)
                canvas = (images.width, images.height)
            except (OSError, struct.error, ValueError, RuntimeError) as e:
                # Narrowed from a bare `except Exception` so genuinely
                # unexpected errors (bugs in ja2py, etc.) still surface
                # rather than getting silently dropped.
                logger.warning(
                    "Failed to load FaceGear STI %s: %s: %s",
                    sti_path, type(e).__name__, e,
                )
                if load_errors is not None:
                    try:
                        rel = rp.relative_to(install_root)
                        rel_str = str(rel).replace("\\", "/")
                    except ValueError:
                        rel_str = sti_path.name
                    load_errors.append({
                        "name": sti_path.name,
                        "relative_path": rel_str,
                        "error": type(e).__name__,
                        "message": str(e),
                    })
                continue
            try:
                rel = rp.relative_to(install_root)
            except ValueError:
                rel = sti_path
            results.append(
                FaceGearInfo(
                    path=rp,
                    name=sti_path.name,
                    relative_path=str(rel).replace("\\", "/"),
                    frame_count=count,
                    canvas_size=canvas,
                    is_imp_variant=sti_path.stem.lower().endswith("_imp"),
                )
            )
    return results


def crash_risk(infos: list[FaceGearInfo], face_index: int) -> list[FaceGearInfo]:
    """Return the FaceGear STIs whose frame count <= face_index.

    For face index N, the engine reads frame[N]. The STI must have at
    least N+1 frames. Anything shorter will hit the SGP_THROW_IFFALSE at
    vobject.cpp:958 the moment the merc equips the corresponding item.
    """
    return [info for info in infos if info.frame_count <= face_index]


def _atomic_save_sti(images: "Images8Bit", sti_path: Path) -> None:
    """Atomic STI write: serialize to a tempfile in the same directory,
    then os.replace() onto the target path.

    Without this, a crash or process kill mid-save leaves the STI half-
    written; the next load_8bit_sti() raises a struct error and the
    user loses the gear/face. Mirrors the pattern in
    `mercwizard_core/inject/_atomic_xml.py::save_atomic` so both code
    paths have the same crash-safety guarantee.

    The tempfile lives in the same directory as the final path so the
    os.replace() is a same-volume rename (atomic on Windows AND POSIX).
    A cross-volume replace would degrade to copy+unlink and lose the
    atomicity property.
    """
    import os
    import tempfile

    sti_path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_path_str = tempfile.mkstemp(
        prefix=f".{sti_path.stem}.",
        suffix=".sti.tmp",
        dir=str(sti_path.parent),
    )
    tmp_path = Path(tmp_path_str)
    try:
        # Close fd; ja2py's save_8bit_sti opens its own file handle.
        os.close(fd)
        with open(tmp_path, "wb") as f:
            save_8bit_sti(images, f)
        os.replace(tmp_path_str, str(sti_path))
    except Exception:
        # Clean up tempfile if anything went wrong before the replace.
        try:
            tmp_path.unlink()
        except FileNotFoundError:
            pass
        raise


def _stem_without_imp(name: str) -> str:
    """Map 'Face_SunGoggles_IMP.sti' → 'Face_SunGoggles', 'Face_Hat.sti' → 'Face_Hat'."""
    stem = name
    if "." in stem:
        stem = stem.rsplit(".", 1)[0]
    if stem.lower().endswith("_imp"):
        stem = stem[: -len("_imp")]
    return stem


def read_registered_facegear_stis(ctx) -> set[str]:
    """Parse the install's `TableData/FaceGear.xml` and return the set of
    Face_X stems (lowercased, no .sti, no _IMP suffix) that the engine
    will actually try to load at boot.

    An entry is "registered" when its `<szFile>` field is non-empty.
    Empty `<szFile />` placeholders are slot stubs the engine ignores.

    Why we need this: `find_orphan_variants` previously flagged ANY
    Face_X.sti without an _IMP partner as a boot-CTD risk. That
    produced false positives for modder leftovers (e.g. Face_KGoggles
    in a reference install was an unused orphan STI — flagging it scared
    users about a "boot crash" that would never actually happen
    because no FaceGear.xml row referenced the file). Filtering
    orphans through the registered set turns the banner into a real,
    actionable warning.

    Returns an empty set when FaceGear.xml is missing or unreadable;
    callers should treat that as "can't verify registration, fall back
    to the filesystem-level scan and tell the user the list may
    include false positives."
    """
    from xml.etree import ElementTree as ET
    xml_path = ctx.layout.resolve_read("TableData/FaceGear.xml")
    if xml_path is None or not xml_path.is_file():
        return set()
    try:
        tree = ET.parse(str(xml_path))
    except (OSError, ET.ParseError):
        return set()
    root = tree.getroot()
    stems: set[str] = set()
    # FaceGear.xml entries look like:
    #   <ITEM>
    #     <uiIndex>250</uiIndex>
    #     <Type>0</Type>
    #     <szFile>FACES\FACESGEAR\Face_KGoggles</szFile>
    #   </ITEM>
    # szFile is backslash-separated Windows-style, may include .sti.
    for item in root.iter("ITEM"):
        szfile = item.findtext("szFile")
        if not szfile or not szfile.strip():
            continue
        # Take the basename. Strip extension if present. Strip _IMP
        # suffix so this matches the same stem convention
        # `_stem_without_imp` uses for filesystem entries.
        path_norm = szfile.strip().replace("\\", "/")
        basename = path_norm.rsplit("/", 1)[-1]
        stem = _stem_without_imp(basename)
        if stem:
            stems.add(stem.lower())
    return stems


def find_orphan_variants(
    infos: list[FaceGearInfo],
    registered_stems: Optional[set[str]] = None,
) -> list[dict]:
    """Detect FaceGear STIs missing their _IMP pair (or vice versa).

    Per MercWizard2/CLAUDE.md: every Type>0 FaceGear item needs both
    `Face_X.STI` and `Face_X_IMP.STI` — `InitializeFaceGearGraphics()` calls
    `AddVideoObject()` on both at boot and a missing one returns NULL,
    which `vobject.cpp:1092` dereferences for a hard crash.

    When `registered_stems` is supplied (a set of lowercased Face_X
    stems pulled from the install's `FaceGear.xml`), only orphans whose
    stem is in the registered set are returned. Filesystem-only
    orphans — STIs sitting in `faces/FACESGEAR/` but never referenced
    by FaceGear.xml — are silently ignored because the engine never
    tries to load them; they can't cause a boot CTD. When
    `registered_stems` is None (the original behavior), every
    filesystem orphan is returned. Tests + back-compat callers keep
    the old signature.

    Returns a list of `{"stem": "Face_SunGoggles", "missing": "imp" | "base"}`
    entries describing which side of the pair is missing.
    """
    # Key the bucket dict by LOWERCASE stem so case-different `_IMP`
    # partners group together. `_stem_without_imp` preserves source
    # case, so `Face_NVGoggles.sti` + `Face_NVGOGGLES_IMP.sti` (real
    # pattern on case-sensitive FS — Linux/macOS, WSL, dev VMs) would
    # otherwise split into two buckets → both falsely reported as
    # orphans. The display_stem field preserves the as-on-disk
    # capitalization so the UI shows the user's actual filename.
    # Bug-review finding A6.
    by_stem: dict[str, dict[str, FaceGearInfo]] = {}
    display_stems: dict[str, str] = {}
    for info in infos:
        stem = _stem_without_imp(info.name)
        key = stem.lower()
        bucket = by_stem.setdefault(key, {})
        bucket["imp" if info.is_imp_variant else "base"] = info
        # Prefer the BASE entry's casing for display when present; falls
        # back to whatever we saw first.
        if "base" in bucket and not info.is_imp_variant:
            display_stems[key] = stem
        else:
            display_stems.setdefault(key, stem)

    orphans: list[dict] = []
    for key, bucket in by_stem.items():
        # Filter out unregistered stems when the caller supplied the
        # set. Keeps the orphan list focused on items the engine will
        # actually try to load — no more KGoggles-style false alarms.
        if registered_stems is not None and key not in registered_stems:
            continue
        display_stem = display_stems[key]
        if "base" not in bucket:
            orphans.append({
                "stem": display_stem,
                "missing": "base",
                "present_path": bucket["imp"].relative_path,
            })
        elif "imp" not in bucket:
            orphans.append({
                "stem": display_stem,
                "missing": "imp",
                "present_path": bucket["base"].relative_path,
            })
    orphans.sort(key=lambda o: o["stem"])
    return orphans


def resolve_orphan_repair_paths(
    infos: list[FaceGearInfo],
    orphan: dict,
) -> Optional[tuple[Path, Path]]:
    """Resolve (source, target) absolute paths for one orphan dict.

    `orphan` is one entry from `find_orphan_variants()`. Returns the
    source path (file present on disk, copied FROM) and target path
    (the missing partner, copied TO).

    Returns None when the source can't be located in `infos` — usually
    because the disk state shifted between the orphan scan and now
    (concurrent edit, file deletion). Caller should re-scan.
    """
    stem = orphan["stem"]
    missing_side = orphan["missing"]
    target_stem = f"{stem}_IMP" if missing_side == "imp" else stem
    source_stem = stem if missing_side == "imp" else f"{stem}_IMP"

    source_info = next(
        (i for i in infos if Path(i.name).stem.lower() == source_stem.lower()),
        None,
    )
    if source_info is None:
        return None

    target_name = f"{target_stem}{source_info.path.suffix}"
    target_path = source_info.path.with_name(target_name)
    return source_info.path, target_path


def repair_orphan_pair(source_path: Path, target_path: Path) -> int:
    """Copy `source_path` bytes verbatim to `target_path`.

    FaceGear STIs are universal 256-frame containers — engine boot only
    checks that the file exists and loads; identical base/IMP partners
    are how vanilla 1.13 ships several of them anyway (e.g. `Face_3.sti`
    and `Face_3_IMP.sti` are byte-identical). Returns the number of bytes
    written.

    Does NOT overwrite an existing target — caller pre-checks this so
    the orphan list reflects ground truth at the moment the user clicks
    Repair. Re-raises `FileExistsError` if the target appeared between
    scan and write (concurrent edit).
    """
    if target_path.exists():
        raise FileExistsError(
            f"Repair target {target_path} already exists — re-scan orphans"
        )
    data = source_path.read_bytes()
    target_path.parent.mkdir(parents=True, exist_ok=True)
    target_path.write_bytes(data)
    return len(data)


def extend_facegear_sti(sti_path: Path, target_count: int) -> dict:
    """Append transparent frames to a FaceGear STI until it has `target_count`.

    The appended frames are 1×1 fully-transparent placeholders — the engine
    accepts the index (no crash) and renders nothing for that face. Each
    frame still costs ~10 bytes on disk so the file growth is minimal.

    Returns a dict with the operation summary. Idempotent: if the file
    already has >= target_count frames, returns unchanged.

    Caller is responsible for backing the file up first via
    `mercwizard_core.backup.snapshot`. This function does NOT take a
    backup — wrapping it in a backup transaction is the route's job.
    """
    with open(sti_path, "rb") as f:
        images = load_8bit_sti(f)
    current_count = len(images.images)
    canvas_w, canvas_h = images.width, images.height

    if current_count >= target_count:
        return {
            "path": str(sti_path),
            "previous_frame_count": current_count,
            "new_frame_count": current_count,
            "frames_appended": 0,
            "noop": True,
        }

    # Build a 1×1 transparent sub-image that reuses the existing palette.
    # ja2py wants P-mode with the palette object intact. We borrow frame 0's
    # palette so the appended frames don't introduce a new color universe.
    first_image = images.images[0].image
    placeholder = Image.new("P", (1, 1), 0)
    if first_image.palette is not None:
        pal_bytes = bytes(first_image.getpalette())[: 256 * 3]
        if len(pal_bytes) < 768:
            pal_bytes = pal_bytes + b"\x00" * (768 - len(pal_bytes))
        placeholder.putpalette(pal_bytes, rawmode="RGB")
        if placeholder.palette is not None:
            placeholder.palette.rawmode = "RGB"

    extra_subs = [
        SubImage8Bit(placeholder.copy(), offsets=(0, 0))
        for _ in range(target_count - current_count)
    ]
    new_subs = list(images.images) + extra_subs

    # Rebuild the container palette via _build_image_palette so it ALWAYS
    # carries rawmode="RGB" — ja2py's save path mis-interleaves as planar
    # BGR otherwise (the same bug the portrait pipeline carefully avoids).
    # The loaded palette object's rawmode is unreliable.
    fresh_palette = _build_image_palette(first_image)
    new_images = Images8Bit(
        new_subs,
        fresh_palette,
        width=canvas_w,
        height=canvas_h,
    )
    _atomic_save_sti(new_images, sti_path)

    return {
        "path": str(sti_path),
        "previous_frame_count": current_count,
        "new_frame_count": target_count,
        "frames_appended": target_count - current_count,
        "noop": False,
    }


def _coerce_overlay_image(png_bytes: bytes) -> Image.Image:
    """Decode a user-supplied PNG (or other PIL-readable format) and coerce to
    the canonical 48×43 RGBA overlay shape.

    Larger sources are center-cropped to the 48×43 aspect ratio, then resized.
    Smaller sources are rejected (no upscale — the resulting STI sub-frame
    would be obviously blurry against the high-res original art).
    """
    src = Image.open(io.BytesIO(png_bytes)).convert("RGBA")
    sw, sh = src.size
    tw, th = FACEGEAR_OVERLAY_SIZE
    if sw < tw or sh < th:
        raise ValueError(
            f"FaceGear overlay must be at least {tw}×{th}; got {src.size}. "
            "Author at 48×43 or larger."
        )
    if src.size == FACEGEAR_OVERLAY_SIZE:
        return src
    cropped = center_crop(src, tw, th)
    return cropped.resize(FACEGEAR_OVERLAY_SIZE, Image.Resampling.LANCZOS)


def inject_overlay(
    sti_path: Path,
    face_index: int,
    overlay_png_bytes: bytes,
    *,
    extend_if_needed: bool = True,
    offset_xy: Optional[tuple[int, int]] = None,
) -> dict:
    """Replace frame[face_index] in a FaceGear STI with a custom overlay.

    The overlay is decoded to a 48×43 RGBA image (cropped + resized from
    larger inputs) then quantized against the STI's existing palette. This
    preserves every OTHER frame's rendering — only frame[face_index] changes
    pixels. New colors not in the palette are mapped to nearest neighbors
    (FaceGear art is typically simple enough that this looks fine).

    `offset_xy` controls the STI sub-frame header's `sOffsetX/sOffsetY` —
    the engine adds these to the bottom-anchored render position
    (vobject_blitters.cpp:319-320), letting the wizard auto-position a
    stock graphic per merc by setting an offset computed from the merc's
    `usEyesX/usEyesY`. If None, preserves whatever offset the target
    frame had (current behavior — vanilla-style 48×43 canvas with offset 0).

    If `extend_if_needed` and the STI has fewer than `face_index + 1` frames,
    the STI is extended with transparent placeholders up to `face_index`
    first, then the overlay overwrites the placeholder at the target index.

    Caller is responsible for taking a backup beforehand. The function does
    NOT write to the _IMP variant — pair it up at the route layer if both
    sides should change.
    """
    overlay = _coerce_overlay_image(overlay_png_bytes)

    with open(sti_path, "rb") as f:
        images = load_8bit_sti(f)
    original_count = len(images.images)
    extended = False

    if face_index >= original_count:
        if not extend_if_needed:
            raise ValueError(
                f"face_index={face_index} >= frame count {original_count}; "
                "pass extend_if_needed=True or call extend_facegear_sti first."
            )
        extend_facegear_sti(sti_path, face_index + 1)
        extended = True
        with open(sti_path, "rb") as f:
            images = load_8bit_sti(f)

    # Quantize against the container's authoritative palette. The loaded
    # palette object's rawmode is unreliable, so we rebuild it freshly via
    # _build_image_palette and use that for both the quantize reference and
    # the new sub-image's normalized palette.
    fresh_palette = _build_image_palette(images.images[0].image)
    pal_bytes = fresh_palette.palette
    if len(pal_bytes) < 768:
        pal_bytes = pal_bytes + b"\x00" * (768 - len(pal_bytes))
    ref = Image.new("P", (1, 1))
    ref.putpalette(pal_bytes, rawmode="RGB")
    if ref.palette is not None:
        ref.palette.rawmode = "RGB"
    quantized = quantize_against_palette(overlay, ref)

    # Normalize the quantized sub-image's palette to match the container's
    quantized.putpalette(pal_bytes, rawmode="RGB")
    if quantized.palette is not None:
        quantized.palette.rawmode = "RGB"

    # Replace frame[face_index] in the sub-image list.
    new_subs = list(images.images)
    # Decide the final sub-frame offsets:
    #   - explicit offset_xy from caller (auto-position path) takes priority
    #   - else PNG `mw2_offset_x/y` metadata (set by extract_overlay) — this
    #     is how bundle import preserves the source merc's offsets across
    #     a .wmerc round trip without an extra sidecar file
    #   - else preserve whatever offset the existing target frame had
    #     (vanilla-style replace; default (0,0) for newly-extended frames)
    if offset_xy is not None:
        final_offsets = (int(offset_xy[0]), int(offset_xy[1]))
    else:
        meta_offset = _read_offset_from_png_metadata(overlay_png_bytes)
        if meta_offset is not None:
            final_offsets = meta_offset
        else:
            existing = new_subs[face_index].offsets if face_index < len(new_subs) else (0, 0)
            final_offsets = (_unsigned_to_signed_i16(existing[0]), _unsigned_to_signed_i16(existing[1]))

    # ja2py's StiSubImageHeader declares offset_x/y as UINT16 ('H') but the
    # engine reads them as INT16. Negative values must be two's-complement
    # encoded as UINT16 before ja2py packs them (e.g. -2 → 65534) or save
    # explodes with `'H' format requires 0 <= number <= 65535`.
    encoded_offsets = (
        _signed_to_unsigned_u16(final_offsets[0]),
        _signed_to_unsigned_u16(final_offsets[1]),
    )
    new_subs[face_index] = SubImage8Bit(quantized, offsets=encoded_offsets)

    new_images = Images8Bit(
        new_subs,
        fresh_palette,
        width=images.width,
        height=images.height,
    )
    _atomic_save_sti(new_images, sti_path)

    return {
        "path": str(sti_path),
        "face_index": face_index,
        "previous_frame_count": original_count,
        "new_frame_count": len(new_subs),
        "extended": extended,
        # Report the signed INT16 (engine-interpretation) offset, not the
        # UINT16 wire encoding we just packed.
        "offset_xy": final_offsets,
    }


def extract_overlay(sti_path: Path, face_index: int) -> Optional[bytes]:
    """Read frame[face_index] from a FaceGear STI as PNG bytes.

    The returned PNG embeds the source frame's `sOffsetX/sOffsetY` as
    `mw2_offset_x` / `mw2_offset_y` tEXt metadata so a downstream
    `inject_overlay` (e.g. bundle import) can preserve the per-merc
    positioning without an out-of-band sidecar.

    Returns None when the index is out of range (the merc has no custom
    overlay in this gear's STI — the engine would have crashed on equip).
    Used by the `.wmerc` bundle exporter to preserve per-merc overlays.

    Palette-resolution lives in `sti_decode.decode_subimage_to_rgba`;
    this function only adds the FaceGear-specific PNG metadata tags.
    """
    from PIL.PngImagePlugin import PngInfo
    from .sti_decode import decode_subimage_to_rgba

    with open(sti_path, "rb") as f:
        images = load_8bit_sti(f)
    if face_index >= len(images.images):
        return None

    rgba = decode_subimage_to_rgba(images, face_index)

    meta = PngInfo()
    # Decode the UINT16-stored offsets back to their signed INT16 truth
    # (matches what the engine sees) before embedding in PNG metadata.
    sub = images.images[face_index]
    signed_off_x = _unsigned_to_signed_i16(sub.offsets[0])
    signed_off_y = _unsigned_to_signed_i16(sub.offsets[1])
    meta.add_text("mw2_offset_x", str(signed_off_x))
    meta.add_text("mw2_offset_y", str(signed_off_y))

    buf = io.BytesIO()
    rgba.save(buf, format="PNG", pnginfo=meta)
    return buf.getvalue()


def read_frame_offset(sti_path: Path, face_index: int) -> Optional[tuple[int, int]]:
    """Return signed `(sOffsetX, sOffsetY)` for frame[face_index], or None
    if out of range. Converts ja2py's UINT16 storage back to engine-correct INT16."""
    with open(sti_path, "rb") as f:
        images = load_8bit_sti(f)
    if face_index >= len(images.images):
        return None
    raw = images.images[face_index].offsets
    return (_unsigned_to_signed_i16(raw[0]), _unsigned_to_signed_i16(raw[1]))


def set_overlay_offset(
    sti_path: Path,
    face_index: int,
    offset_x: int,
    offset_y: int,
) -> dict:
    """Set frame[face_index]'s `sOffsetX/sOffsetY` to absolute
    (offset_x, offset_y) without touching the pixel content. Pure
    header-edit; no quantize, no repacking — preserves the existing
    palette and image bytes.

    Companion to `nudge_overlay_offset` (which shifts by a delta). The
    direct-coord-editing UI in FaceGearOverlayAuthor uses this when the
    user types absolute values in the X/Y inputs; the ←↑↓→ arrows still
    use the nudge primitive for ±1 px deltas.

    `offset_x`/`offset_y` are interpreted as signed INT16 by the engine.
    Values outside `-32768..32767` raise ValueError. Both nudge and
    set-offset share the same on-disk encoding (UINT16 two's complement
    via `_signed_to_unsigned_u16`).

    Returns `{"face_index": N, "previous_offset_xy": (x, y),
    "new_offset_xy": (offset_x, offset_y)}`.
    """
    if not (-32768 <= offset_x <= 32767) or not (-32768 <= offset_y <= 32767):
        raise ValueError(
            f"Offset ({offset_x}, {offset_y}) overflows INT16. "
            "Engine reads sOffsetX/sOffsetY as signed 16-bit."
        )

    with open(sti_path, "rb") as f:
        images = load_8bit_sti(f)
    if face_index >= len(images.images):
        raise ValueError(
            f"face_index={face_index} >= frame count {len(images.images)}; "
            "set_overlay_offset requires an existing frame (run inject_overlay "
            "or auto_position_overlay first)."
        )

    sub = images.images[face_index]
    prev_signed_x = _unsigned_to_signed_i16(sub.offsets[0])
    prev_signed_y = _unsigned_to_signed_i16(sub.offsets[1])

    new_offsets = (
        _signed_to_unsigned_u16(offset_x),
        _signed_to_unsigned_u16(offset_y),
    )
    new_subs = list(images.images)
    new_subs[face_index] = SubImage8Bit(sub.image, offsets=new_offsets)

    # Rebuild the container with a fresh-rawmode palette to dodge ja2py's
    # planar-interleave bug (same pattern as inject_overlay / nudge).
    fresh_palette = _build_image_palette(images.images[0].image)
    new_images = Images8Bit(
        new_subs,
        fresh_palette,
        width=images.width,
        height=images.height,
    )
    _atomic_save_sti(new_images, sti_path)

    return {
        "face_index": face_index,
        "previous_offset_xy": (prev_signed_x, prev_signed_y),
        "new_offset_xy": (int(offset_x), int(offset_y)),
    }


def nudge_overlay_offset(
    sti_path: Path,
    face_index: int,
    dx: int,
    dy: int,
) -> dict:
    """Shift frame[face_index]'s `sOffsetX/sOffsetY` by (dx, dy) without
    touching the pixel content. Pure header-edit; no quantize, no
    repacking — preserves the existing palette and image bytes.

    Used by the FaceGear "nudge" UI to fine-tune auto-positioning when
    the algorithm lands close but not perfect (Christine-style outliers
    where eye coord doesn't perfectly track goggle bbox).

    Returns the new signed offset as `{"face_index": N, "previous_offset_xy": (x, y),
    "new_offset_xy": (x+dx, y+dy)}`. Engine reads these as INT16 so callers
    can pass small ±1..±5 shifts safely; large values that would overflow
    INT16 (-32768..32767) raise ValueError.
    """
    with open(sti_path, "rb") as f:
        images = load_8bit_sti(f)
    if face_index >= len(images.images):
        raise ValueError(
            f"face_index={face_index} >= frame count {len(images.images)}; "
            "nudge requires an existing frame (run inject_overlay or "
            "auto_position_overlay first)."
        )

    sub = images.images[face_index]
    prev_signed_x = _unsigned_to_signed_i16(sub.offsets[0])
    prev_signed_y = _unsigned_to_signed_i16(sub.offsets[1])
    new_signed_x = prev_signed_x + dx
    new_signed_y = prev_signed_y + dy
    if not (-32768 <= new_signed_x <= 32767) or not (-32768 <= new_signed_y <= 32767):
        raise ValueError(
            f"Nudged offset ({new_signed_x}, {new_signed_y}) overflows INT16. "
            "Use a smaller delta or reset via auto-position."
        )

    # Repack the subimage with the same pixels + palette, but new offsets.
    # ja2py's StiSubImageHeader stores UINT16 ('H') so we encode signed → unsigned.
    new_offsets = (
        _signed_to_unsigned_u16(new_signed_x),
        _signed_to_unsigned_u16(new_signed_y),
    )
    new_subs = list(images.images)
    new_subs[face_index] = SubImage8Bit(sub.image, offsets=new_offsets)

    # Rebuild the container with a fresh-rawmode palette to dodge ja2py's
    # planar-interleave bug (same pattern as inject_overlay).
    fresh_palette = _build_image_palette(images.images[0].image)
    new_images = Images8Bit(
        new_subs,
        fresh_palette,
        width=images.width,
        height=images.height,
    )
    _atomic_save_sti(new_images, sti_path)

    return {
        "face_index": face_index,
        "previous_offset_xy": (prev_signed_x, prev_signed_y),
        "new_offset_xy": (new_signed_x, new_signed_y),
        "delta_xy": (dx, dy),
    }


def _find_source_frame(images: "Images8Bit") -> Optional[int]:
    """Pick the first frame index with non-zero pixel content.

    Skips empty/transparent frames (placeholder slots in the FaceGear STI
    that don't correspond to any deployed merc). Used as the default source
    when auto-positioning: we want a frame that actually has gear pixels
    in it, not a transparent placeholder.
    """
    for i, sub in enumerate(images.images):
        try:
            for px in sub.image.getdata():
                if px != 0:
                    return i
        except OSError:
            continue
    return None


def auto_position_overlay(
    sti_path: Path,
    target_face_index: int,
    target_eye_xy: tuple[int, int],
    source_eye_xy: tuple[int, int],
    *,
    source_face_index: Optional[int] = None,
    extend_if_needed: bool = True,
) -> dict:
    """Copy a stock FaceGear frame to `target_face_index` with offset computed
    from the merc's eye coordinates — the engine-supported shortcut for
    per-merc positioning without authoring per-merc art.

    Algorithm:
      1. Pick `source_face_index` (auto-detects first non-empty frame if None)
      2. Compute delta = target_eye - source_eye
      3. Compute new sub-frame offset = source_offset + delta
      4. Inject source frame's pixels at target_face_index with the new offset

    The engine renders `frame[target_face_index]` bottom-anchored to the
    portrait, then adds the sub-frame `sOffsetX/sOffsetY` — so the same gear
    graphic ends up positioned per merc's eye row without re-authoring pixels.

    Works because every JA2 face renders bottom-anchored to a fixed-size
    portrait panel; mercs whose eyes sit lower in their portrait need the
    overlay shifted down by the same delta. Imperfect when face proportions
    differ wildly from the source (the goggle row doesn't perfectly track
    the eye XML coord for outliers like Christine — see CLAUDE.md), but
    a sensible default for most mercs.

    Returns the inject result + the source/target/computed offsets so the
    UI can show what happened.
    """
    with open(sti_path, "rb") as f:
        images = load_8bit_sti(f)

    if source_face_index is None:
        source_face_index = _find_source_frame(images)
        if source_face_index is None:
            raise ValueError(
                f"{sti_path.name} has no non-empty frames — can't auto-position "
                "from stock. Upload a custom overlay instead."
            )
    if source_face_index >= len(images.images):
        raise ValueError(
            f"source_face_index={source_face_index} >= frame count "
            f"{len(images.images)}"
        )

    raw_off = images.images[source_face_index].offsets
    source_offset = (_unsigned_to_signed_i16(raw_off[0]), _unsigned_to_signed_i16(raw_off[1]))
    delta_x = int(target_eye_xy[0]) - int(source_eye_xy[0])
    delta_y = int(target_eye_xy[1]) - int(source_eye_xy[1])
    new_offset = (source_offset[0] + delta_x, source_offset[1] + delta_y)

    source_png = extract_overlay(sti_path, source_face_index)
    if source_png is None:
        # Shouldn't happen — source_face_index was bounded above
        raise RuntimeError(f"extract_overlay returned None for index {source_face_index}")

    inject_result = inject_overlay(
        sti_path,
        target_face_index,
        source_png,
        extend_if_needed=extend_if_needed,
        offset_xy=new_offset,
    )
    return {
        **inject_result,
        "source_face_index": source_face_index,
        "source_offset_xy": source_offset,
        "target_eye_xy": (int(target_eye_xy[0]), int(target_eye_xy[1])),
        "source_eye_xy": (int(source_eye_xy[0]), int(source_eye_xy[1])),
        "delta_xy": (delta_x, delta_y),
        "applied_offset_xy": new_offset,
    }


def _read_offset_from_png_metadata(png_bytes: bytes) -> Optional[tuple[int, int]]:
    """If the PNG carries `mw2_offset_x/y` tEXt metadata (from extract_overlay),
    return the decoded (x, y). Else None.

    Phase 2.5: the previous bare `except Exception: pass` collapsed three
    distinct cases — (a) no chunk present (legitimate None), (b) chunk
    present but malformed (data lost), (c) Image.open failure on a
    corrupt PNG — into the same None return. The fix narrows the except
    to the actual raise sites and logs a warning when the chunk parses
    fail, so bundle round-trips don't silently lose per-merc FaceGear
    offsets on subtle PNG corruption.
    """
    try:
        img = Image.open(io.BytesIO(png_bytes))
        info = img.info
    except (OSError, ValueError) as e:
        # PNG itself is unreadable. Caller treats this as "no offset
        # metadata available" — but it's worth logging since the upstream
        # PNG is probably corrupt and other code paths will hit the same
        # error.
        logger.warning("Failed to open PNG for offset metadata: %s: %s", type(e).__name__, e)
        return None
    if "mw2_offset_x" not in info or "mw2_offset_y" not in info:
        # Legitimate None — PNG simply has no offset chunk. Don't log.
        return None
    try:
        return (int(info["mw2_offset_x"]), int(info["mw2_offset_y"]))
    except (ValueError, TypeError) as e:
        # Chunk present but unparseable — data WAS there, we're losing it.
        # Warn loudly so a bundle round-trip's offset corruption surfaces.
        logger.warning(
            "mw2_offset PNG metadata present but malformed (%s=%r, %s=%r): %s: %s",
            "mw2_offset_x", info.get("mw2_offset_x"),
            "mw2_offset_y", info.get("mw2_offset_y"),
            type(e).__name__, e,
        )
        return None
