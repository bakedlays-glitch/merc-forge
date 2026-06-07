"""Export a merc to a .wmerc bundle.

The exporter reads the merc's data from the live install (MercProfiles +
AIMAvailability + MercStartingGear + STI files + optional source portraits
from a side-channel directory) and packs it into a single zip.

For v1 we don't include the 5 animation PNGs (skip-mode default) unless the
caller supplies them — the importer regenerates dummy frames from the base
portrait if absent.
"""
from __future__ import annotations

import json
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from PIL import Image

from xml.etree import ElementTree as ET

from .. import voice as voice_mod
from ..inject import aim_availability, merc_availability, profiles_xml, starting_gear
from ..inject import edt as edt_mod
from ..install_context import EXTRA_TABLES, make_install_context
from ..models import AimBinding, GearKit, Merc, MercBinding
from .manifest import (
    WmercAuthor,
    WmercCompat,
    WmercManifest,
    WmercPortraitMeta,
    WmercSchemaFingerprint,
    WmercVoiceMeta,
)


_AUDIO_SUFFIXES = (".ogg", ".wav", ".mp3")
_AUDIO_PLUS_GAP = _AUDIO_SUFFIXES + (".gap",)


def _gather_battlesnds(install_root: Path, slot: int) -> dict[str, bytes]:
    """`Battlesnds/<slot>_*.ogg` — combat shouts."""
    ctx = make_install_context(install_root)
    out: dict[str, bytes] = {}
    root = ctx.battlesnds_root()
    if not root.is_dir():
        return out
    prefix = f"{slot}_"
    try:
        for f in root.iterdir():
            if f.is_file() and f.name.startswith(prefix) and f.suffix.lower() in _AUDIO_SUFFIXES:
                out[f"audio/battlesnds/{f.name}"] = f.read_bytes()
    except OSError:
        pass
    return out


def _gather_npc_speech(install_root: Path, slot: int) -> dict[str, bytes]:
    """`NPC_Speech/<slot>_*.ogg` — NPC-style dialogue."""
    ctx = make_install_context(install_root)
    out: dict[str, bytes] = {}
    root = ctx.npc_speech_root()
    if not root.is_dir():
        return out
    prefix = f"{slot}_"
    try:
        for f in root.iterdir():
            if f.is_file() and f.name.startswith(prefix) and f.suffix.lower() in _AUDIO_SUFFIXES:
                out[f"audio/npc_speech/{f.name}"] = f.read_bytes()
    except OSError:
        pass
    return out


def _gather_snitch_names(install_root: Path, slot: int) -> dict[str, bytes]:
    """`Speech/snitch/names/*_<slot>.ogg` (and names_alt) — other mercs naming THIS merc."""
    ctx = make_install_context(install_root)
    out: dict[str, bytes] = {}
    suffix_stem = f"_{slot}"  # files end with _<slot>.<ext>
    for alt in (False, True):
        d = ctx.snitch_names_dir(alt=alt)
        if not d.is_dir():
            continue
        bucket = "audio/snitch_names_alt" if alt else "audio/snitch_names"
        try:
            for f in d.iterdir():
                if not f.is_file():
                    continue
                if f.suffix.lower() not in _AUDIO_PLUS_GAP:
                    continue
                if not f.stem.endswith(suffix_stem):
                    continue
                out[f"{bucket}/{f.name}"] = f.read_bytes()
        except OSError:
            continue
    return out


def _gather_raw_face_stis(install_root: Path, face_index: int) -> dict[str, bytes]:
    """Verbatim original face STIs (33Face, 65Face, SmallFace, BigFace) plus
    any camo variants (DESERTCAMO/URBANCAMO/WOODCAMO) and IMPFaces."""
    ctx = make_install_context(install_root)
    out: dict[str, bytes] = {}

    face_subdirs = ("", "33Face", "65Face", "BigFaces", "DESERTCAMO", "URBANCAMO", "WOODCAMO",
                    "33face", "65face", "bigfaces")
    impface_subdirs = ("", "33Face", "65Face", "BigFaces", "DESERTCAMO", "URBANCAMO", "WOODCAMO")

    def collect(base_dir: Path, top: str) -> None:
        if not base_dir.is_dir():
            return
        for subdir in face_subdirs if top == "Faces" else impface_subdirs:
            d = base_dir / subdir if subdir else base_dir
            if not d.is_dir():
                continue
            for ext in ("sti", "STI"):
                for name in (f"{face_index}.{ext}", f"{face_index:02}.{ext}"):
                    f = d / name
                    if f.is_file():
                        rel = f"{top}/{subdir}/{name}" if subdir else f"{top}/{name}"
                        out[f"raw_stis/{rel}"] = f.read_bytes()

    collect(ctx.faces_dir(), "Faces")
    # IMPFaces — there's no dedicated InstallContext method, probe the mod content path
    impfaces = ctx.layout.mod_content_path("IMPFaces")
    collect(impfaces, "IMPFaces")
    return out


def _extract_sti_subframes_as_pngs(install_root: Path, face_index: int) -> dict[str, bytes]:
    """Decode the source install's SmallFace + BigFace STIs and emit PNGs:

      - `bigface_source.png` from BigFace's single frame (1024x1024 upscale,
        same convention as the manual Vengeance exporter — gives the importer
        a high-res canvas to work from when recompiling the 4 sizes).
      - `anim_eye_1.png`..`anim_eye_4.png` from SmallFace frames 1..4 (17x6).
      - `anim_mouth_1.png`..`anim_mouth_3.png` from SmallFace frames 5..7 (14x6).

    Each is a verbatim crop of what the mod artist authored. If the SmallFace
    isn't a canonical 8-frame STI (some mods ship single-frame stubs), only
    the BigFace is extracted. Missing STIs silently return an empty subset.

    The pre-fix bundle only carried frame 0 of SmallFace (via the legacy
    `extract_portrait_png` path in the Vengeance exporter). Animation pixels
    were lost on .wmerc round-trip. This function ensures the closure.
    """
    from io import BytesIO

    out: dict[str, bytes] = {}
    ctx = make_install_context(install_root)

    # --- BigFace -> bigface_source.png ----------------------------------
    bigface_path = ctx.face_sti_path(face_index, size="bigface")
    if bigface_path.is_file():
        bigface_png = _decode_sti_frame_as_png(bigface_path, frame_index=0)
        if bigface_png:
            out["bigface_source.png"] = bigface_png

    # --- SmallFace -> 4 eye + 3 mouth subframes -------------------------
    smallface_path = ctx.face_sti_path(face_index, size="smallface")
    if smallface_path.is_file():
        anim_pngs = _decode_smallface_animation_pngs(smallface_path)
        out.update(anim_pngs)

    return out


def _decode_sti_frame_as_png(sti_path: Path, frame_index: int) -> Optional[bytes]:
    """Load one frame from an STI and return its PNG bytes. None on failure."""
    from io import BytesIO
    try:
        from ja2py.fileformats.Sti import load_8bit_sti, is_8bit_sti
        with open(sti_path, "rb") as f:
            if not is_8bit_sti(f):
                return None  # 16-bit STIs unsupported by ja2py
        images = load_8bit_sti(str(sti_path))
        if not images.images or frame_index >= len(images.images):
            return None
        rgba = images.images[frame_index].image.convert("RGBA")
        buf = BytesIO()
        rgba.save(buf, format="PNG")
        return buf.getvalue()
    except Exception:
        return None


def _decode_smallface_animation_pngs(sti_path: Path) -> dict[str, bytes]:
    """Return {arcname: png_bytes} for `anim_eye_1..4` and `anim_mouth_1..3`
    if the STI has the 8-frame layout. Empty dict otherwise.

    Sub-frame sizes are mod-defined (per Faces.cpp:480-481 the engine reads
    `usEyesWidth`/`Height` from the STI's per-frame header). Vanilla 1.13
    uses 17x6 / 14x6 but Vengeance uses 31x13 / 32x21. We export at whatever
    native size the source ships.
    """
    from io import BytesIO
    try:
        from ja2py.fileformats.Sti import load_8bit_sti, is_8bit_sti
        with open(sti_path, "rb") as f:
            if not is_8bit_sti(f):
                return {}
        images = load_8bit_sti(str(sti_path))
        if len(images.images) != 8:
            return {}  # not an 8-frame SmallFace
        out: dict[str, bytes] = {}
        for i, arcname in enumerate(
            ("anim_eye_1.png", "anim_eye_2.png", "anim_eye_3.png", "anim_eye_4.png"), start=1
        ):
            img = images.images[i].image.convert("RGBA")
            buf = BytesIO()
            img.save(buf, format="PNG")
            out[arcname] = buf.getvalue()
        for i, arcname in enumerate(
            ("anim_mouth_1.png", "anim_mouth_2.png", "anim_mouth_3.png"), start=5
        ):
            img = images.images[i].image.convert("RGBA")
            buf = BytesIO()
            img.save(buf, format="PNG")
            out[arcname] = buf.getvalue()
        return out
    except Exception:
        return {}


def _gather_facegear_overlays(install_root: Path, face_index: int) -> dict[str, bytes]:
    """Extract frame[face_index] from every non-IMP Face_*.sti in the install.

    Returns {`facegear/<sti_stem>.png`: png_bytes} entries — one per FaceGear
    item the install ships. The importer matches by stem against the target
    install's FaceGear inventory, so an item the target doesn't have is
    silently skipped (no crash, just no custom hat for that gear).

    IMP variants aren't bundled separately because they normally mirror the
    base — the importer writes to both `Face_X.sti` and `Face_X_IMP.sti` for
    each bundled overlay automatically.

    A frame outside the STI's range (face_index >= frame_count) returns no
    bytes for that file — the merc had no custom overlay there in the source
    install, so there's nothing to preserve.
    """
    from ..install_context import make_install_context
    from ..facegear import detect_facegear_capacities, extract_overlay

    ctx = make_install_context(install_root)
    out: dict[str, bytes] = {}
    for info in detect_facegear_capacities(ctx):
        if info.is_imp_variant:
            continue
        try:
            png = extract_overlay(info.path, face_index)
        except OSError:
            continue
        if png is None:
            continue
        stem = info.path.stem  # e.g. "Face_SunGoggles"
        out[f"facegear/{stem}.png"] = png
    return out


def _gather_big_items(install_root: Path, slot: int) -> dict[str, bytes]:
    """`BigItems/*<slot>*.sti` — signature item STIs (gun218, p1item218, etc.).

    Substring matching would over-match: slot=21 would pick up gun218.sti
    because "21" appears inside "218". We require the slot number to appear
    as a complete numeric token — i.e. surrounded by non-digit characters
    or string boundaries.
    """
    import re
    ctx = make_install_context(install_root)
    out: dict[str, bytes] = {}
    d = ctx.big_items_dir()
    if not d.is_dir():
        return out
    # Token boundary: not preceded or followed by another digit.
    pattern = re.compile(rf"(?<!\d){slot}(?!\d)")
    try:
        for f in d.iterdir():
            if not f.is_file() or f.suffix.lower() != ".sti":
                continue
            if pattern.search(f.stem):
                out[f"big_items/{f.name}"] = f.read_bytes()
    except OSError:
        pass
    return out


def _gather_npc_script(install_root: Path, slot: int) -> Optional[bytes]:
    """Per-slot NPC dialogue script: `NPCData/<slot>.EDT`."""
    ctx = make_install_context(install_root)
    # Try a few path variants (case + location)
    candidates = [
        ctx.layout.mod_content_path(f"NPCData/{slot}.EDT"),
        ctx.layout.mod_content_path(f"NPCData/{slot}.edt"),
        ctx.layout.mod_content_path(f"NpcData/{slot}.EDT"),
        ctx.layout.mod_content_path(f"BinaryData/NPCDATA/{slot}.EDT"),
    ]
    for path in candidates:
        if path.is_file():
            return path.read_bytes()
    return None


def _extract_table_row_xml(table_path: Path, id_tag: str, slot: int) -> Optional[str]:
    """Return the verbatim XML element block whose child <id_tag> == slot.

    Returns the serialized fragment with one trailing newline, or None.
    """
    if not table_path.is_file():
        return None
    try:
        root = ET.parse(str(table_path)).getroot()
    except ET.ParseError:
        # cp1252/latin-1 source with no UTF-8-valid declaration (localized mods
        # ship accented names): re-encode the bytes as UTF-8 so expat accepts
        # them, mirroring backgrounds_xml.read_catalog. Without this, exporting a
        # merc whose row carries a high byte (e.g. an accented background) raised
        # ParseError here and the row was silently dropped from the bundle.
        try:
            data = table_path.read_bytes()
            if data.startswith(b"\xef\xbb\xbf"):
                data = data[3:]
            root = ET.fromstring(data.decode("latin-1").encode("utf-8"))
        except (ET.ParseError, OSError):
            return None
    for el in root:
        for child in el:
            if child.tag == id_tag and (child.text or "").strip() == str(slot):
                ET.indent(el, space="\t")
                return ET.tostring(el, encoding="unicode").strip() + "\n"
    return None


def _gather_table_rows(install_root: Path, slot: int) -> dict[str, str]:
    """Per-slot row fragments from each mod-specific XML table."""
    ctx = make_install_context(install_root)
    out: dict[str, str] = {}
    for key, (filename, id_tag) in EXTRA_TABLES.items():
        # MercAvailability is canonically carried by the manifest's
        # `merc_binding`. Bundling the verbatim row would clobber the
        # importer's MercBioID remap and reintroduce the source slot's
        # display index — same reason AIMAvailability is excluded.
        #
        # FaceGear is excluded for a different reason: FaceGear.xml's
        # <uiIndex> is an inventory ITEM id (SunGoggles == 212), not a merc
        # profile slot, so a per-slot row is wrong-keyed — a no-op row that
        # can clobber a real gear item's overlay on import. Per-merc face
        # gear rides the STI overlay path (facegear/<stem>.png) instead.
        #
        # Backgrounds is excluded for the same wrong-keying reason: Backgrounds.xml
        # is a SHARED CATALOG indexed by a merc's usBackground, NOT a per-merc slot
        # table, so the slot-keyed extraction below would grab an unrelated entry
        # (or nothing). The merc's actual background is bundled separately, keyed
        # by usBackground — see `_gather_background_definition`.
        #
        # CivGroupNames is excluded for the same wrong-keying reason as FaceGear:
        # CivGroupNames.xml's <uiIndex> is a CIV-GROUP id — a direct index into
        # the engine's zCivGroupName[NUM_CIV_GROUPS] array (XML_CivGroupNames.cpp),
        # NOT a merc profile slot. The slot-keyed extraction below is wrong-keyed
        # (usually a miss; a coincidental hit grabs an unrelated faction). A merc's
        # own civ-group membership already rides in the profile's ubCivilianGroup;
        # the catalog itself is shared engine-level faction data, not per-merc
        # content to bundle (like Vehicles.xml).
        if key in ("merc_availability", "face_gear", "backgrounds", "civ_group_names"):
            continue
        path = ctx.extra_table_path(key)
        if path is None or not path.is_file():
            continue
        block = _extract_table_row_xml(path, id_tag, slot)
        if block:
            out[f"table_rows/{filename}"] = block
    # AIMAvailability deliberately excluded — the manifest's `aim_binding`
    # already carries the canonical fields, and `deploy_import` remaps
    # `uiIndex`/`ProfilId`/`AimBioID` to the new slot in step 7. Bundling the
    # verbatim XML row would clobber that remap and reintroduce the source
    # slot number.
    return out


def _gather_background_definition(install_root: Path, us_background: int) -> dict[str, str]:
    """Bundle the merc's ACTUAL background — the catalog entry its usBackground
    points at — keyed by that entry's own uiIndex.

    Backgrounds.xml is a shared catalog indexed by usBackground, NOT a per-merc
    slot table, so (unlike MercOpinions/MercQuote) the row must NOT be keyed by
    the merc's profile slot. Carrying the definition lets the importer recreate
    it (create-if-missing, by its own id) in a target install that lacks it, so
    the merc keeps its background across mods. usBackground 0 (none/template) or
    an id the source catalog doesn't define → nothing to carry.
    """
    if us_background < 1:
        return {}
    ctx = make_install_context(install_root)
    path = ctx.extra_table_path("backgrounds")
    if path is None or not path.is_file():
        return {}
    block = _extract_table_row_xml(path, "uiIndex", us_background)
    if not block:
        return {}
    return {"table_rows/Backgrounds.xml": block}


def _compute_schema_fingerprint(
    install_root: Path,
    raw_profile_dict: Optional[dict],
) -> WmercSchemaFingerprint:
    """Capture the source install's schema shape for cross-mod compatibility checks."""
    from ..install_context import make_install_context
    ctx = make_install_context(install_root)

    fp = WmercSchemaFingerprint(
        source_install_path=str(install_root),
        source_vfs_config=(
            ctx.layout.vfs_config_path.name if ctx.layout.vfs_config_path else None
        ),
        source_mod=ctx.layout.mod_content_profile,
    )

    if raw_profile_dict:
        fp.profile_fields = sorted(raw_profile_dict.keys())
        fp.has_bEvolution = "bEvolution" in raw_profile_dict
        fp.has_fRegresses = "fRegresses" in raw_profile_dict
        fp.has_usVoiceIndex = "usVoiceIndex" in raw_profile_dict
        # Match both the engine's on-disk tag (<bGrowthModifier*>) and the
        # prefix-less spelling a pre-fix MercWizard may have written. This
        # fingerprint feeds a cross-mod compatibility warning; missing the
        # b-prefix here (as the old startswith did) made it blind to real
        # AIMNAS installs whose growth tags are all b-prefixed.
        fp.has_growth_modifiers = any(
            "GrowthModifier" in k for k in raw_profile_dict
        )
        fp.has_stomp_block = (
            "bRace" in raw_profile_dict
            and "bNationality" in raw_profile_dict
            and "usBackground" in raw_profile_dict
        )

    # MercOpinions format detection — sample the file
    opinions_path = ctx.extra_table_path("merc_opinions")
    if opinions_path and opinions_path.is_file():
        try:
            # Read a chunk and look for the sparse/dense signal
            with open(opinions_path, "r", encoding="utf-8", errors="replace") as f:
                head = f.read(20000)
            if "<AnOpinion" in head:
                fp.merc_opinions_format = "sparse"
            elif "<Opinion0>" in head or "<Opinion1>" in head:
                fp.merc_opinions_format = "dense"
        except OSError:
            pass

    # Which extra tables exist
    for key in ("merc_opinions", "merc_quote", "merc_availability",
                "face_gear", "backgrounds", "civ_group_names"):
        if ctx.extra_table_path(key) is not None:
            fp.extra_tables.append(key)

    return fp


def _read_merc_from_install(install_root: Path, ui_index: int) -> Optional[Merc]:
    """Build a Merc model from the install's MercProfiles.xml (VFS-aware)."""
    ctx = make_install_context(install_root)
    raw = profiles_xml.read_slot(ctx.profiles_xml_path(), ui_index)
    if raw is None:
        return None
    # Engine's b-prefixed growth tags → model field names, before the
    # model_fields filter below (which keys on field names) discards them.
    raw = profiles_xml.normalize_profile_tags(raw)

    kwargs: dict[str, object] = {}
    string_fields = {"zName", "zNickname", "PANTS", "VEST", "SKIN", "HAIR",
                     "biographyText", "additionalInfoText"}
    for k, v in raw.items():
        if k in string_fields:
            kwargs[k] = v
        else:
            try:
                kwargs[k] = int(v.strip())
            except (ValueError, AttributeError):
                continue

    valid_fields = set(Merc.model_fields.keys())
    kwargs = {k: v for k, v in kwargs.items() if k in valid_fields}
    try:
        return Merc(**kwargs)
    except Exception:
        return None


def export_merc(
    install_root: Path,
    ui_index: int,
    out_path: Path,
    portrait_source_png: Optional[Path] = None,
    extreme_master_png: Optional[Path] = None,
    bigface_source_png: Optional[Path] = None,
    anim_eye_1: Optional[Path] = None,
    anim_eye_2: Optional[Path] = None,
    anim_mouth_1: Optional[Path] = None,
    anim_mouth_2: Optional[Path] = None,
    anim_mouth_3: Optional[Path] = None,
    preview_png: Optional[Path] = None,
    readme_text: Optional[str] = None,
    author_name: Optional[str] = None,
    license: str = "unspecified",
    intended_mod: str = "any",
    notes: Optional[str] = None,
    include_voice: bool = True,
    include_extras: bool = True,
) -> Path:
    """Build a .wmerc bundle for `ui_index` from `install_root`.

    Returns the written `out_path`.
    """
    install_root = Path(install_root)
    out_path = Path(out_path)

    merc = _read_merc_from_install(install_root, ui_index)
    if merc is None:
        raise FileNotFoundError(f"No merc at slot {ui_index} in install {install_root}")

    ctx = make_install_context(install_root)
    profiles_path = ctx.profiles_xml_path()
    gear_path = ctx.gear_xml_path()
    gear_block = starting_gear.read_slot(gear_path, ui_index)
    gear_kits: list[GearKit] = list(gear_block.kits) if gear_block else []

    aim_path = ctx.aim_xml_path()
    aim_map = aim_availability.read_all(aim_path)
    aim_binding: Optional[AimBinding] = aim_map.get(ui_index)

    merc_xml_path = ctx.merc_xml_path()
    merc_map = merc_availability.read_all(merc_xml_path)
    merc_binding: Optional[MercBinding] = merc_map.get(ui_index)

    # Bio + additional info live in EDT files, not MercProfiles.xml. Read
    # them so the bundle actually carries the merc's biography text.
    try:
        bio, additional = edt_mod.read_bio(
            install_root,
            ui_index=ui_index,
            aim_bio_id=aim_binding.AimBioID if aim_binding else None,
            merc_bio_id=merc_binding.MercBioID if merc_binding else None,
            ctx=ctx,
        )
        if bio:
            merc = merc.model_copy(update={"biographyText": bio})
        if additional:
            merc = merc.model_copy(update={"additionalInfoText": additional})
    except (FileNotFoundError, ValueError):
        # EDT file missing or unroutable — bio just stays empty.
        pass

    voice_meta: Optional[WmercVoiceMeta] = None
    voice_clips: list = []
    if include_voice:
        # Match the voice route's fallback: use raw <usVoiceIndex> if present,
        # else slot. The Pydantic default of 15 isn't trustworthy here because
        # it fires whenever the XML omits the tag.
        raw_profile = profiles_xml.read_slot(profiles_path, ui_index)
        voice_index = ui_index
        if raw_profile is not None:
            raw_uvi = raw_profile.get("usVoiceIndex")
            if raw_uvi is not None:
                try:
                    voice_index = int(raw_uvi.strip())
                except (ValueError, AttributeError):
                    pass
        voice_clips = voice_mod.list_clips(install_root, voice_index)
        if voice_clips:
            voice_meta = WmercVoiceMeta(
                voice_index=voice_index,
                count=len(voice_clips),
                filenames=[c.name for c in voice_clips],
            )

    # Capture schema fingerprint for cross-mod compatibility checks on import
    raw_profile = profiles_xml.read_slot(profiles_path, ui_index)
    fingerprint = _compute_schema_fingerprint(install_root, raw_profile)

    manifest = WmercManifest(
        merc=merc,
        gear=gear_kits,
        aim_binding=aim_binding,
        merc_binding=merc_binding,
        author=WmercAuthor(name=author_name) if author_name else WmercAuthor(),
        license=license,
        notes=notes,
        compat=WmercCompat(intended_mod=intended_mod),  # type: ignore[arg-type]
        portrait=WmercPortraitMeta(),
        voice=voice_meta,
        schema_fingerprint=fingerprint,
        exported_at=datetime.now(timezone.utc).isoformat(),
    )

    # Auto-extract animation sub-frames + BigFace source from the existing
    # SmallFace / BigFace STIs in the install. Each PNG slot is independently
    # auto-filled: if the caller supplied an explicit `bigface_source_png`
    # path, the auto BigFace is dropped; if the caller supplied
    # `anim_eye_1`, the auto eye_1 is dropped; etc. Pre-fix the gate was
    # all-or-nothing — supplying any single explicit path turned off ALL
    # auto-extraction, so a caller wanting to override just the BigFace
    # would lose every auto-extracted animation frame too.
    raw_auto = _extract_sti_subframes_as_pngs(install_root, merc.ubFaceIndex)
    explicit_arc_names = set()
    if bigface_source_png is not None:
        explicit_arc_names.add("bigface_source.png")
    if anim_eye_1 is not None:
        explicit_arc_names.add("anim_eye_1.png")
    if anim_eye_2 is not None:
        explicit_arc_names.add("anim_eye_2.png")
    if anim_mouth_1 is not None:
        explicit_arc_names.add("anim_mouth_1.png")
    if anim_mouth_2 is not None:
        explicit_arc_names.add("anim_mouth_2.png")
    if anim_mouth_3 is not None:
        explicit_arc_names.add("anim_mouth_3.png")
    auto_extracted = {k: v for k, v in raw_auto.items() if k not in explicit_arc_names}

    out_path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(out_path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("manifest.json", json.dumps(manifest.model_dump(mode="json"), indent=2))

        # Portrait sources (optional — the player may export without them, in
        # which case the bundle is metadata-only and import requires
        # supplying portraits)
        for name, path in [
            ("portrait_source.png", portrait_source_png),
            ("extreme_master.png", extreme_master_png),
            ("bigface_source.png", bigface_source_png),
            ("anim_eye_1.png", anim_eye_1),
            ("anim_eye_2.png", anim_eye_2),
            ("anim_mouth_1.png", anim_mouth_1),
            ("anim_mouth_2.png", anim_mouth_2),
            ("anim_mouth_3.png", anim_mouth_3),
            ("preview.png", preview_png),
        ]:
            if path is not None and Path(path).is_file():
                zf.write(path, arcname=name)

        # Drop auto-extracted PNGs in for any slot the caller didn't supply
        # explicitly. Skip if the slot has already been written by an
        # explicit path above (the explicit-path branch already ran for
        # the same arcname).
        already_written = set(zf.namelist())
        for arcname, data in auto_extracted.items():
            if arcname not in already_written:
                zf.writestr(arcname, data)

        for clip in voice_clips:
            try:
                zf.write(clip.path, arcname=f"voice/{clip.name}")
            except OSError:
                continue
            # Bundle the lip-sync sidecar beside the clip when present, so an
            # authored .gap survives the round-trip. Critical for Vengeance .ogg
            # clips: their gaps are hand-authored and can't be regenerated on
            # import (stdlib `wave` can't decode ogg), so without carrying the
            # .gap here the imported clip would lose lip-sync. Last-dot suffix
            # swap mirrors gap._gap_path_for.
            gap_path = Path(clip.path).with_suffix(".gap")
            if gap_path.is_file():
                try:
                    zf.write(str(gap_path), arcname=f"voice/{gap_path.name}")
                except OSError:
                    pass

        # Mod-specific extras: Battlesnds, NPC_Speech, snitch audio, raw face
        # STIs (incl. camos), BigItems, NPC dialogue scripts, mod XML table
        # rows. Skip if disabled. The importer auto-installs each category
        # into the target install's matching directory via InstallContext,
        # renaming slot-encoded filenames as needed.
        if include_extras:
            for arc, data in _gather_battlesnds(install_root, ui_index).items():
                zf.writestr(arc, data)
            for arc, data in _gather_npc_speech(install_root, ui_index).items():
                zf.writestr(arc, data)
            for arc, data in _gather_snitch_names(install_root, ui_index).items():
                zf.writestr(arc, data)
            for arc, data in _gather_raw_face_stis(install_root, merc.ubFaceIndex).items():
                zf.writestr(arc, data)
            for arc, data in _gather_big_items(install_root, ui_index).items():
                zf.writestr(arc, data)
            for arc, data in _gather_facegear_overlays(install_root, merc.ubFaceIndex).items():
                zf.writestr(arc, data)
            for arc, text in _gather_table_rows(install_root, ui_index).items():
                zf.writestr(arc, text)
            # The merc's actual background definition (keyed by usBackground,
            # not slot) — recreated create-if-missing on import so the merc
            # keeps its background in a target install that lacks it.
            for arc, text in _gather_background_definition(
                install_root, merc.usBackground
            ).items():
                zf.writestr(arc, text)
            npc_script = _gather_npc_script(install_root, ui_index)
            if npc_script:
                zf.writestr(f"npc_script/{ui_index}.EDT", npc_script)

        if readme_text:
            zf.writestr("README.md", readme_text)

    return out_path
