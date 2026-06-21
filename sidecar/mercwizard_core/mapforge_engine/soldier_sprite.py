"""Decode a body type's STANDING sprite (one direction) to PNG, for the
MapForge NPC overlay. Reuses the existing SLF + STI helpers; never writes.
Recipe + bodytype->STI table validated in
docs/superpowers/specs/2026-06-21-soldier-sprite-research.md.
"""
from __future__ import annotations

import io
from pathlib import Path
from typing import Optional

# bodytype int -> standing STI internal path under Anims/ (forward slashes).
BODYTYPE_STANDING_STI: dict[int, str] = {
    0:  "S_MERC/S_R_STD.STI",      # REGMALE
    1:  "M_MERC/M_R_STD.STI",      # BIGMALE
    2:  "S_MERC/S_R_STD.STI",      # STOCKYMALE (shares regular male)
    3:  "F_MERC/F_BRETH2.STI",     # REGFEMALE
    4:  "MONSTERS/MN_BREAT.STI",   # ADULTFEMALEMONSTER
    11: "CIVS/FT_BRTH.STI",        # FATCIV
    12: "CIVS/M_BREATH.STI",       # MANCIV
    20: "ANIMALS/CT_BREATH.STI",   # BLOODCAT
    29: "ANIMALS/DOG_BREATH.STI",  # DOG (Wasteland)
    30: "ANIMALS/GORIS_BREATH.STI",
    31: "ANIMALS/GRUTHAR_BREATH.STI",
    32: "ANIMALS/MOM_BREATH.STI",
}
_FALLBACK = "S_MERC/S_R_STD.STI"


def _resolve_anim_sti_bytes(install_root: str, internal_path: str) -> Optional[bytes]:
    """Loose-first (<root>/Data/Anims/<path>, case-insensitive), then
    <root>/Data/Anims.slf at /<path>. Returns raw STI bytes or None.
    Also probes <root>/Data-1.13/Anims/<path> as a higher-priority VFS
    override layer before falling back to the SLF."""
    root = Path(install_root)
    rel = internal_path.replace("\\", "/").strip("/")
    # 1. loose — case-insensitive walk of each path segment.
    for base in (root / "Data" / "Anims", root / "Data-1.13" / "Anims"):
        cur = base
        ok = base.is_dir()
        for seg in rel.split("/"):
            if not ok:
                break
            match = None
            try:
                for child in cur.iterdir():
                    if child.name.lower() == seg.lower():
                        match = child
                        break
            except OSError:
                ok = False
                break
            if match is None:
                ok = False
                break
            cur = match
        if ok and cur.is_file():
            try:
                return cur.read_bytes()
            except OSError:
                pass
    # 2. SLF.
    slf_path = root / "Data" / "Anims.slf"
    if slf_path.is_file():
        try:
            from mercwizard_core.install_context import _open_slf_cached
            slf = _open_slf_cached(slf_path)
            internal = "/" + rel
            if slf is not None and slf.isfile(internal):
                return slf.openbin(internal, "r").read()
        except Exception:
            pass
    return None



def _pick_subimage(total_subimages: int, direction: int) -> int:
    """Sub-image index for a standing STI's first frame in `direction`.
    Standing STIs are 8 dirs x M frames/dir contiguous; direction is remapped
    one step clockwise (gOneCDirection). Caller guarantees total>=8."""
    frames_per_dir = total_subimages // 8
    sub = frames_per_dir * ((direction + 1) % 8)
    return min(sub, total_subimages - 1)


def render_standing_sprite(install_root: str, bodytype: int,
                           direction: int) -> Optional[bytes]:
    """PNG of `bodytype`'s standing sprite facing `direction` (0-7), or None."""
    path = BODYTYPE_STANDING_STI.get(bodytype, _FALLBACK)
    data = _resolve_anim_sti_bytes(install_root, path)
    if data is None and path != _FALLBACK:
        data = _resolve_anim_sti_bytes(install_root, _FALLBACK)
    if data is None:
        return None
    try:
        from ja2py.fileformats.Sti import is_8bit_sti, load_8bit_sti
        from mercwizard_core.sti_decode import decode_subimage_to_rgba
        buf = io.BytesIO(data)
        if not is_8bit_sti(buf):
            return None
        buf.seek(0)
        images = load_8bit_sti(buf)
        total = len(images.images)
        if total < 8:
            return None
        sub = _pick_subimage(total, direction)
        rgba = decode_subimage_to_rgba(images, sub)
        out = io.BytesIO()
        rgba.save(out, format="PNG")
        return out.getvalue()
    except Exception:
        return None
