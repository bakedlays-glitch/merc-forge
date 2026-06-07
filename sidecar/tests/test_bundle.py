"""Tests for .wmerc export / import."""
from __future__ import annotations

import json
import zipfile
from pathlib import Path

import pytest

from mercwizard_core import gap, voice as voice_mod
from mercwizard_core.bundle import (
    ImportAuditError,
    SlotOccupiedError,
    deploy_import,
    export_merc,
    import_merc,
    move_between_installs,
    read_wmerc,
)
from mercwizard_core.bundle.manifest import WmercManifest
from mercwizard_core.inject import aim_availability, profiles_xml, starting_gear
from mercwizard_core.inject import edt as edt_mod
from mercwizard_core import backup as _bk_unused  # noqa: F401
from mercwizard_core.models import AimBinding, Gear, GearKit, Merc

from .test_voice_gap import _make_wav  # reuse the PCM-WAV-with-silence builder


def _populate_install(install_root: Path, slot: int = 220) -> Merc:
    table = install_root / "Data-1.13" / "TableData"
    table.mkdir(parents=True, exist_ok=True)
    merc = Merc(
        uiIndex=slot, ubFaceIndex=slot, Type=1,
        zName="Tycho", zNickname="Tycho",
        biographyText="A grizzled ranger.",
        additionalInfoText="Knows the wastes.",
        usVoiceIndex=slot,
    )
    profiles_xml.upsert(table / "MercProfiles.xml", merc)
    aim_availability.upsert(
        table / "AIMAvailability.xml",
        AimBinding(uiIndex=slot, description="Tycho", ProfilId=slot, AimBioID=52),
    )
    starting_gear.upsert(
        table / "MercStartingGear.xml",
        Gear(mIndex=slot, mName="Tycho", kits=[GearKit(mWeapon=4)]),
    )
    edt_mod.write_bio(
        install_root,
        ui_index=slot,
        biography=merc.biographyText,
        additional=merc.additionalInfoText,
        aim_bio_id=52,
    )
    return merc


def _empty_install(install_root: Path) -> Path:
    """Create an empty Data-1.13/TableData skeleton with no merc populated."""
    table = install_root / "Data-1.13" / "TableData"
    table.mkdir(parents=True, exist_ok=True)
    return install_root


def _seed_facegear_sti(install_root: Path, sti_name: str, frame_count: int) -> Path:
    """Create a minimal Face_*.sti + Face_*_IMP.sti pair under Data-1.13/faces/FACESGEAR/."""
    from PIL import Image
    from mercwizard_core.facegear import extend_facegear_sti
    from mercwizard_core.portrait.sti import write_static_sti

    dir_path = install_root / "Data-1.13" / "faces" / "FACESGEAR"
    dir_path.mkdir(parents=True, exist_ok=True)
    base = Image.new("RGBA", (48, 43), (180, 140, 110, 255))
    # Sprinkle distinct colors so quantize-against-palette has options
    for x in range(2, 12):
        for y in range(2, 12):
            base.putpixel((x, y), (220, 60, 60, 255))
    for x in range(20, 30):
        for y in range(2, 12):
            base.putpixel((x, y), (60, 200, 60, 255))
    base_path = dir_path / sti_name
    imp_path = dir_path / sti_name.replace(".sti", "_IMP.sti")
    write_static_sti(base_path, base)
    write_static_sti(imp_path, base)
    if frame_count > 1:
        extend_facegear_sti(base_path, frame_count)
        extend_facegear_sti(imp_path, frame_count)
    return base_path


def test_export_includes_facegear_overlays_when_present(tmp_path: Path) -> None:
    """Export should extract frame[face_index] from every non-IMP Face_*.sti
    in the install and bundle them as facegear/<stem>.png."""
    from PIL import Image
    from mercwizard_core.facegear import inject_overlay

    install = tmp_path / "install"
    _populate_install(install, slot=220)
    sti_path = _seed_facegear_sti(install, "Face_TestGear.sti", frame_count=250)

    # Author a custom overlay at the merc's face index so there's something
    # distinctive to round-trip.
    overlay = Image.new("RGBA", (48, 43), (50, 100, 200, 255))
    import io as _io
    buf = _io.BytesIO()
    overlay.save(buf, format="PNG")
    inject_overlay(sti_path, face_index=220, overlay_png_bytes=buf.getvalue())

    out = tmp_path / "tycho_with_overlay.wmerc"
    export_merc(install, ui_index=220, out_path=out)

    with zipfile.ZipFile(out, "r") as zf:
        names = zf.namelist()
    assert "facegear/Face_TestGear.png" in names, (
        f"FaceGear overlay missing from bundle. Contents: {[n for n in names if 'face' in n.lower()]}"
    )


def test_bundle_round_trip_preserves_facegear_overlay(tmp_path: Path) -> None:
    """Author overlay in source → export → import into target with the SAME
    FaceGear item → extract from target → confirm pixels survived."""
    from PIL import Image
    from mercwizard_core.facegear import (
        detect_facegear_capacities,
        extract_overlay,
        inject_overlay,
    )
    from mercwizard_core.install_context import make_install_context
    import io as _io

    source = tmp_path / "source"
    _populate_install(source, slot=220)
    source_sti = _seed_facegear_sti(source, "Face_RoundTrip.sti", frame_count=250)

    # Distinctive red overlay (red is in both source AND target palettes —
    # blue would quantize to skin since the seeded palette has no blues).
    overlay = Image.new("RGBA", (48, 43), (220, 60, 60, 255))
    buf = _io.BytesIO()
    overlay.save(buf, format="PNG")
    inject_overlay(source_sti, face_index=220, overlay_png_bytes=buf.getvalue())

    bundle = tmp_path / "merc.wmerc"
    export_merc(source, ui_index=220, out_path=bundle)

    target = tmp_path / "target"
    _empty_install(target)
    _seed_facegear_sti(target, "Face_RoundTrip.sti", frame_count=50)  # short — will get extended

    report = deploy_import(target, bundle, target_slot=175)
    assert report.target_slot == 175

    target_ctx = make_install_context(target)
    infos = detect_facegear_capacities(target_ctx)
    target_info = next(i for i in infos if i.name == "Face_RoundTrip.sti")
    # WMERC preserves the source's ubFaceIndex (per docs/WMERC_FORMAT.md);
    # the merc's portrait + facegear references still point at 220 in the
    # target install even though the merc moved to slot 175.
    extracted = extract_overlay(target_info.path, face_index=220)
    assert extracted is not None, "overlay missing in target after import"
    extracted_img = Image.open(_io.BytesIO(extracted)).convert("RGBA")
    assert extracted_img.size == (48, 43), f"extracted shape wrong: {extracted_img.size}"
    cx, cy = 24, 20
    px = extracted_img.getpixel((cx, cy))
    assert px[3] > 0, "imported overlay went transparent"
    # Red-dominant (palette includes a (220,60,60) entry on both sides)
    assert px[0] > px[1] and px[0] > px[2], f"imported overlay not red: {px}"


def test_bundle_round_trip_preserves_facegear_offset(tmp_path: Path) -> None:
    """When the source merc's FaceGear frame has a non-zero sOffsetX/sOffsetY
    (e.g. auto-positioned with eye-coord delta), the bundle round-trip must
    preserve that offset. extract_overlay embeds the offset in PNG metadata;
    inject_overlay reads it as the fallback when no explicit offset is given."""
    from PIL import Image
    from mercwizard_core.facegear import (
        detect_facegear_capacities,
        inject_overlay,
        read_frame_offset,
    )
    from mercwizard_core.install_context import make_install_context
    import io as _io

    source = tmp_path / "source"
    _populate_install(source, slot=220)
    source_sti = _seed_facegear_sti(source, "Face_OffsetRT.sti", frame_count=250)

    # Author source merc's frame at face_index=220 with a recognizable offset
    overlay = Image.new("RGBA", (48, 43), (220, 60, 60, 255))
    buf = _io.BytesIO()
    overlay.save(buf, format="PNG")
    inject_overlay(source_sti, face_index=220, overlay_png_bytes=buf.getvalue(), offset_xy=(-3, 5))

    bundle = tmp_path / "merc.wmerc"
    export_merc(source, ui_index=220, out_path=bundle)

    target = tmp_path / "target"
    _empty_install(target)
    _seed_facegear_sti(target, "Face_OffsetRT.sti", frame_count=50)

    report = deploy_import(target, bundle, target_slot=175)
    assert report.target_slot == 175

    target_ctx = make_install_context(target)
    infos = detect_facegear_capacities(target_ctx)
    target_info = next(i for i in infos if i.name == "Face_OffsetRT.sti")
    # ubFaceIndex preserved as 220 across import (per WMERC_FORMAT.md)
    target_offset = read_frame_offset(target_info.path, face_index=220)
    assert target_offset == (-3, 5), (
        f"offset lost on round-trip: target frame {target_offset}, expected (-3, 5)"
    )


def test_bundle_import_skips_facegear_items_missing_in_target(tmp_path: Path) -> None:
    """When the target install lacks a FaceGear item that the source had,
    the importer logs a partial_failure warning but doesn't error out."""
    from PIL import Image
    from mercwizard_core.facegear import inject_overlay
    import io as _io

    source = tmp_path / "source"
    _populate_install(source, slot=220)
    source_sti = _seed_facegear_sti(source, "Face_VanillaOnly.sti", frame_count=250)
    overlay = Image.new("RGBA", (48, 43), (200, 100, 50, 255))
    buf = _io.BytesIO()
    overlay.save(buf, format="PNG")
    inject_overlay(source_sti, face_index=220, overlay_png_bytes=buf.getvalue())

    bundle = tmp_path / "merc.wmerc"
    export_merc(source, ui_index=220, out_path=bundle)

    target = tmp_path / "target"
    _empty_install(target)
    # NO Face_VanillaOnly.sti in target — import should skip with warning
    report = deploy_import(target, bundle, target_slot=175)
    assert report.target_slot == 175
    matches = [w for w in report.partial_failures if "Face_VanillaOnly" in w]
    assert matches, f"expected partial_failures warning, got: {report.partial_failures}"


def test_export_produces_wmerc_zip(tmp_path: Path) -> None:
    install = tmp_path / "install"
    _populate_install(install)
    out = tmp_path / "tycho.wmerc"

    export_merc(install, ui_index=220, out_path=out, author_name="modder", license="CC-BY-SA")
    assert out.is_file()

    contents = read_wmerc(out)
    assert contents.manifest.merc.zName == "Tycho"
    assert contents.manifest.aim_binding is not None
    assert contents.manifest.aim_binding.AimBioID == 52
    assert len(contents.manifest.gear) == 1
    assert contents.manifest.gear[0].mWeapon == 4
    assert contents.manifest.author.name == "modder"
    assert contents.manifest.license == "CC-BY-SA"


def test_import_rewrites_ui_index(tmp_path: Path) -> None:
    install = tmp_path / "install"
    _populate_install(install, slot=220)
    out = tmp_path / "tycho.wmerc"
    export_merc(install, ui_index=220, out_path=out)

    # Import to a different slot
    contents = import_merc(out, install_root=install, target_slot=234)
    assert contents.manifest.merc.uiIndex == 234
    assert contents.manifest.aim_binding is not None
    assert contents.manifest.aim_binding.uiIndex == 234
    assert contents.manifest.aim_binding.ProfilId == 234


def test_export_with_portrait_png_includes_it(tmp_path: Path) -> None:
    from PIL import Image
    install = tmp_path / "install"
    _populate_install(install)
    out = tmp_path / "tycho.wmerc"

    portrait_path = tmp_path / "portrait.png"
    Image.new("RGBA", (1024, 1024), (100, 50, 30, 255)).save(portrait_path)

    export_merc(install, ui_index=220, out_path=out, portrait_source_png=portrait_path)
    contents = read_wmerc(out)
    assert contents.has_portrait_source
    assert "portrait_source.png" in contents.files


def test_export_includes_voice_clips(tmp_path: Path) -> None:
    install = tmp_path / "install"
    _populate_install(install)
    speech_dir = install / "Data-1.13" / "Speech" / "220"
    speech_dir.mkdir(parents=True, exist_ok=True)
    (speech_dir / "MERC220_001.wav").write_bytes(b"RIFFfake_wav_data")
    (speech_dir / "MERC220_002.wav").write_bytes(b"RIFFfake_wav_data_2")

    out = tmp_path / "tycho.wmerc"
    export_merc(install, ui_index=220, out_path=out)

    contents = read_wmerc(out)
    assert contents.manifest.voice is not None
    assert contents.manifest.voice.voice_index == 220
    assert contents.manifest.voice.count == 2
    assert "MERC220_001.wav" in contents.manifest.voice.filenames
    assert "voice/MERC220_001.wav" in contents.files
    assert "voice/MERC220_002.wav" in contents.files


def test_export_omits_voice_when_disabled(tmp_path: Path) -> None:
    install = tmp_path / "install"
    _populate_install(install)
    speech_dir = install / "Data-1.13" / "Speech" / "220"
    speech_dir.mkdir(parents=True, exist_ok=True)
    (speech_dir / "MERC220_001.wav").write_bytes(b"RIFFdata")

    out = tmp_path / "tycho.wmerc"
    export_merc(install, ui_index=220, out_path=out, include_voice=False)

    contents = read_wmerc(out)
    assert contents.manifest.voice is None
    assert not any(n.startswith("voice/") for n in contents.files)


def test_deploy_import_writes_profile_into_empty_slot(tmp_path: Path) -> None:
    source = tmp_path / "source"
    _populate_install(source, slot=220)
    bundle = tmp_path / "tycho.wmerc"
    export_merc(source, ui_index=220, out_path=bundle, author_name="modder")

    target = tmp_path / "target"
    _empty_install(target)

    report = deploy_import(target, bundle, target_slot=234)
    assert report.target_slot == 234
    assert any("MercProfiles.xml" in p for p in report.files_written)

    profile = profiles_xml.read_slot(target / "Data-1.13" / "TableData" / "MercProfiles.xml", 234)
    assert profile is not None
    assert profile["zName"] == "Tycho"

    aim_map = aim_availability.read_all(target / "Data-1.13" / "TableData" / "AIMAvailability.xml")
    assert 234 in aim_map
    assert aim_map[234].ProfilId == 234

    gear = starting_gear.read_slot(target / "Data-1.13" / "TableData" / "MercStartingGear.xml", 234)
    assert gear is not None
    assert gear.mIndex == 234
    assert gear.kits[0].mWeapon == 4


def test_deploy_import_remaps_aim_bio_id(tmp_path: Path) -> None:
    """Bundle's AimBioID=52 from slot 220, deployed to slot 175 → must remap to 45 (canonical)."""
    source = tmp_path / "source"
    _populate_install(source, slot=220)  # AimBioID=52
    bundle = tmp_path / "tycho.wmerc"
    export_merc(source, ui_index=220, out_path=bundle)

    target = tmp_path / "target"
    _empty_install(target)

    report = deploy_import(target, bundle, target_slot=175)
    aim_map = aim_availability.read_all(target / "Data-1.13" / "TableData" / "AIMAvailability.xml")
    assert 175 in aim_map
    assert aim_map[175].AimBioID == 45  # canonical for slot 175
    assert aim_map[175].AimBioID != 52  # NOT the bundle's original
    assert report.aim_bio_id_used == 45


def test_deploy_import_auto_derives_aim_row_for_type1_without_binding(tmp_path: Path) -> None:
    """A Type=1 bundle with NO aim_binding (the source slot had no <AIM> row)
    must STILL get an AIMAvailability row on import.

    Without the auto-derive the profile is written as Type=1 but no AIM row
    is added, so the merc never appears on the AIM laptop — the
    Marcus-at-slot-57 invisibility trap, the exact case the audit's
    TYPE_NO_AIM_ROW warning + Import.tsx banner promise MercForge fixes on
    save. Mirrors the MERC-side auto-derive and routes/merc.py's Create-flow
    auto-fill. Counterpart to test_deploy_import_remaps_aim_bio_id, which
    covers the bundle-carries-a-binding path."""
    # Build a bundle directly: Type=1 merc, aim_binding=None — exactly the gap
    # export.py:468 (`aim_map.get(ui_index)`) produces when the source install
    # had no <AIM> element for the slot.
    manifest = WmercManifest(
        merc=Merc(
            uiIndex=220, ubFaceIndex=220, Type=1,
            zName="Tycho", zNickname="Tycho",
            biographyText="A grizzled ranger.",
            additionalInfoText="Knows the wastes.",
            usVoiceIndex=220,
        ),
        gear=[GearKit(mWeapon=4)],
        aim_binding=None,
    )
    bundle = tmp_path / "tycho_no_aim_row.wmerc"
    with zipfile.ZipFile(bundle, "w") as zf:
        zf.writestr("manifest.json", manifest.model_dump_json())

    # Precondition: the bundle really carries Type=1 with no AIM binding.
    contents = read_wmerc(bundle)
    assert contents.manifest.merc.Type == 1
    assert contents.manifest.aim_binding is None

    target = tmp_path / "target"
    _empty_install(target)
    report = deploy_import(target, bundle, target_slot=175)

    aim_map = aim_availability.read_all(target / "Data-1.13" / "TableData" / "AIMAvailability.xml")
    assert 175 in aim_map, "Type=1 merc got no AIMAvailability row — invisible on the AIM laptop"
    assert aim_map[175].ProfilId == 175
    assert aim_map[175].AimBioID >= 0          # a valid bio offset, never the -1 placeholder
    assert aim_map[175].AimBioID == 45         # rederived against target (canonical for slot 175)
    assert report.aim_bio_id_used is not None
    assert report.aim_bio_id_used == aim_map[175].AimBioID


def test_deploy_import_writes_bio_at_corrected_offset(tmp_path: Path) -> None:
    """Slot 175 → AimBioID 45 → AIMBIOS.EDT offset 45*1120 (bug-fix path)."""
    source = tmp_path / "source"
    _populate_install(source, slot=220)
    bundle = tmp_path / "tycho.wmerc"
    export_merc(source, ui_index=220, out_path=bundle)

    target = tmp_path / "target"
    _empty_install(target)
    deploy_import(target, bundle, target_slot=175)

    # Read back via the high-level API; if the offset is wrong, this returns garbage.
    bio, additional = edt_mod.read_bio(target, ui_index=175, aim_bio_id=45)
    assert "grizzled ranger" in bio.lower() or "tycho" in bio.lower() or bio == "A grizzled ranger."
    # additional_info uses a separate slot inside the same record
    assert "wastes" in additional.lower() or additional == "Knows the wastes."


def test_deploy_import_compiles_portrait(tmp_path: Path) -> None:
    from PIL import Image
    source = tmp_path / "source"
    _populate_install(source, slot=220)

    portrait_path = tmp_path / "portrait.png"
    Image.new("RGBA", (1024, 1024), (100, 50, 30, 255)).save(portrait_path)

    bundle = tmp_path / "tycho.wmerc"
    export_merc(source, ui_index=220, out_path=bundle, portrait_source_png=portrait_path)

    target = tmp_path / "target"
    _empty_install(target)
    report = deploy_import(target, bundle, target_slot=234)

    assert report.portrait_compiled is True
    faces_dir = target / "Data-1.13" / "faces"
    # ubFaceIndex==220 (the merc's own field, not slot)
    assert (faces_dir / "220.sti").is_file()
    assert (faces_dir / "65FACE" / "220.sti").is_file()
    assert (faces_dir / "33FACE" / "220.sti").is_file()
    assert (faces_dir / "BigFaces" / "220.sti").is_file()


def test_deploy_import_copies_voice_clips(tmp_path: Path) -> None:
    source = tmp_path / "source"
    _populate_install(source, slot=220)
    speech_dir = source / "Data-1.13" / "Speech" / "220"
    speech_dir.mkdir(parents=True, exist_ok=True)
    (speech_dir / "MERC220_001.wav").write_bytes(b"RIFFfake")
    (speech_dir / "MERC220_002.wav").write_bytes(b"RIFFfake2")

    bundle = tmp_path / "tycho.wmerc"
    export_merc(source, ui_index=220, out_path=bundle)

    target = tmp_path / "target"
    _empty_install(target)
    report = deploy_import(target, bundle, target_slot=234)

    assert report.voice_clips_copied == 2
    # voice_index is the merc's usVoiceIndex (220), not the target slot
    assert (target / "Data-1.13" / "Speech" / "220" / "MERC220_001.wav").is_file()
    assert (target / "Data-1.13" / "Speech" / "220" / "MERC220_002.wav").is_file()


def test_deploy_import_blocks_occupied_slot_without_force(tmp_path: Path) -> None:
    source = tmp_path / "source"
    _populate_install(source, slot=220)
    bundle = tmp_path / "tycho.wmerc"
    export_merc(source, ui_index=220, out_path=bundle)

    target = tmp_path / "target"
    _populate_install(target, slot=234)  # target slot already populated

    with pytest.raises(SlotOccupiedError) as exc:
        deploy_import(target, bundle, target_slot=234, force=False)
    assert exc.value.slot == 234

    # With force=True it succeeds
    report = deploy_import(target, bundle, target_slot=234, force=True)
    assert report.target_slot == 234
    profile = profiles_xml.read_slot(
        target / "Data-1.13" / "TableData" / "MercProfiles.xml", 234
    )
    assert profile is not None
    assert profile["zName"] == "Tycho"


def test_deploy_import_blocks_on_audit_error(tmp_path: Path) -> None:
    """An RPC-typed merc deployed into an AIM-bound slot must raise ImportAuditError.

    Type=3 (RPC per Tactical/Soldier Profile.h:51) at slot 175 (expanded AIM)
    triggers audit's NPC_IN_AIM_SLOT ERROR — the merc is recruitable via quest
    events but not visible on the AIM website. The audit code name is kept for
    backwards compatibility; the underlying check is "non-AIM Type in AIM-bound slot".
    """
    source = tmp_path / "source"
    table = source / "Data-1.13" / "TableData"
    table.mkdir(parents=True, exist_ok=True)
    # Source has the merc at slot 220 (not AIM-bound), Type=3 → no audit error
    npc_merc = Merc(
        uiIndex=220, ubFaceIndex=220, Type=3,
        zName="Spook", zNickname="Spook",
        biographyText="Lurker.",
        usVoiceIndex=220,
    )
    profiles_xml.upsert(table / "MercProfiles.xml", npc_merc)
    aim_availability.upsert(
        table / "AIMAvailability.xml",
        AimBinding(uiIndex=220, description="Spook", ProfilId=220, AimBioID=52),
    )

    bundle = tmp_path / "spook.wmerc"
    export_merc(source, ui_index=220, out_path=bundle)

    target = tmp_path / "target"
    _empty_install(target)

    # Deploy to slot 175 (AIM-bound). The Type=3 (RPC) + is_aim_bound_slot
    # combination is exactly NPC_IN_AIM_SLOT (the Marcus-at-slot-57 bug —
    # an RPC inheriting MIGUEL's default Type, then sat in an AIM slot).
    with pytest.raises(ImportAuditError) as exc:
        deploy_import(target, bundle, target_slot=175)
    codes = {issue["code"] for issue in exc.value.issues}
    assert "NPC_IN_AIM_SLOT" in codes


# ─────────────────────────────────────────────────────────────────────
#  Cross-install move tests
# ─────────────────────────────────────────────────────────────────────


def test_cross_install_move_transfers_merc_and_clears_source(tmp_path: Path) -> None:
    source = tmp_path / "source"
    target = tmp_path / "target"
    _populate_install(source, slot=220)
    _empty_install(target)

    report = move_between_installs(
        source_install=source,
        source_install_id="source_id",
        target_install=target,
        target_install_id="target_id",
        source_slot=220,
        target_slot=234,
    )
    assert report.source_cleared is True
    assert report.target_slot == 234

    # Target now has the merc
    target_profile = profiles_xml.read_slot(
        target / "Data-1.13" / "TableData" / "MercProfiles.xml", 234
    )
    assert target_profile is not None
    assert target_profile["zName"] == "Tycho"

    # Source slot is empty (cleared)
    src_after = profiles_xml.read_slot(
        source / "Data-1.13" / "TableData" / "MercProfiles.xml", 220
    )
    # clear_slot leaves the <PROFILE> stub but with empty zName/zNickname
    assert src_after is None or not src_after.get("zName", "").strip()

    # Source AIMAvailability no longer has slot 220
    src_aim_map = aim_availability.read_all(
        source / "Data-1.13" / "TableData" / "AIMAvailability.xml"
    )
    assert 220 not in src_aim_map


def test_cross_install_move_rederives_aim_bio_id_at_target(tmp_path: Path) -> None:
    """Cross-install move into an expanded-AIM slot must remap AimBioID."""
    source = tmp_path / "source"
    target = tmp_path / "target"
    _populate_install(source, slot=220)  # AimBioID=52 in source
    _empty_install(target)

    report = move_between_installs(
        source_install=source,
        source_install_id="source_id",
        target_install=target,
        target_install_id="target_id",
        source_slot=220,
        target_slot=175,
    )
    target_aim = aim_availability.read_all(
        target / "Data-1.13" / "TableData" / "AIMAvailability.xml"
    )
    assert target_aim[175].AimBioID == 45  # canonical for slot 175
    assert report.aim_bio_id_used == 45


def test_cross_install_move_blocks_on_occupied_target(tmp_path: Path) -> None:
    source = tmp_path / "source"
    target = tmp_path / "target"
    _populate_install(source, slot=220)
    _populate_install(target, slot=234)  # target already populated

    with pytest.raises(SlotOccupiedError):
        move_between_installs(
            source_install=source,
            source_install_id="source_id",
            target_install=target,
            target_install_id="target_id",
            source_slot=220,
            target_slot=234,
            force=False,
        )

    # Source merc must still be intact after the failed move (no clear happens)
    src_profile = profiles_xml.read_slot(
        source / "Data-1.13" / "TableData" / "MercProfiles.xml", 220
    )
    assert src_profile is not None
    assert src_profile["zName"] == "Tycho"


def test_cross_install_move_with_force_overwrites_target(tmp_path: Path) -> None:
    source = tmp_path / "source"
    target = tmp_path / "target"
    _populate_install(source, slot=220)
    _populate_install(target, slot=234)

    report = move_between_installs(
        source_install=source,
        source_install_id="source_id",
        target_install=target,
        target_install_id="target_id",
        source_slot=220,
        target_slot=234,
        force=True,
    )
    assert report.source_cleared is True

    target_profile = profiles_xml.read_slot(
        target / "Data-1.13" / "TableData" / "MercProfiles.xml", 234
    )
    assert target_profile is not None
    assert target_profile["zName"] == "Tycho"


def test_export_bundles_battlesnds_and_npc_speech(tmp_path: Path) -> None:
    """Comprehensive export captures slot-prefixed audio dirs."""
    import zipfile
    install = tmp_path / "src"
    _populate_install(install, slot=220)
    # Synthesize battlesnds and npc_speech for slot 220
    bs_dir = install / "Data-1.13" / "Battlesnds"
    bs_dir.mkdir(parents=True)
    (bs_dir / "220_ATTN.ogg").write_bytes(b"x" * 100)
    (bs_dir / "220_DYING.ogg").write_bytes(b"y" * 100)
    (bs_dir / "221_OTHER.ogg").write_bytes(b"z")  # different slot, should NOT bundle
    ns_dir = install / "Data-1.13" / "NPC_Speech"
    ns_dir.mkdir(parents=True)
    (ns_dir / "220_000.ogg").write_bytes(b"a")

    out = tmp_path / "bundle.wmerc"
    export_merc(install, ui_index=220, out_path=out)
    with zipfile.ZipFile(out) as zf:
        names = zf.namelist()
    bs = [n for n in names if n.startswith("audio/battlesnds/")]
    ns = [n for n in names if n.startswith("audio/npc_speech/")]
    assert set(bs) == {"audio/battlesnds/220_ATTN.ogg", "audio/battlesnds/220_DYING.ogg"}
    assert ns == ["audio/npc_speech/220_000.ogg"]


def test_export_bundles_table_rows_when_extra_tables_present(tmp_path: Path) -> None:
    """Comprehensive export pulls per-slot XML rows from mod-specific tables."""
    import zipfile
    install = tmp_path / "src"
    _populate_install(install, slot=220)
    # Synthesize a MercOpinions table with a slot-220 row
    opinions_path = install / "Data-1.13" / "TableData" / "MercOpinions.xml"
    opinions_path.write_text(
        "<OPINION_LIST>\n"
        "\t<OPINION>\n"
        "\t\t<uiIndex>220</uiIndex>\n"
        "\t\t<Opinion0>5</Opinion0>\n"
        "\t</OPINION>\n"
        "\t<OPINION>\n"
        "\t\t<uiIndex>221</uiIndex>\n"
        "\t\t<Opinion0>0</Opinion0>\n"
        "\t</OPINION>\n"
        "</OPINION_LIST>\n"
    )

    out = tmp_path / "bundle.wmerc"
    export_merc(install, ui_index=220, out_path=out)
    with zipfile.ZipFile(out) as zf:
        text = zf.read("table_rows/MercOpinions.xml").decode("utf-8")
    assert "<uiIndex>220</uiIndex>" in text
    assert "<Opinion0>5</Opinion0>" in text
    # The slot-221 row should NOT be bundled
    assert "<Opinion0>0</Opinion0>" not in text


def test_comprehensive_import_routes_battlesnds_with_slot_rename(tmp_path: Path) -> None:
    """A bundle whose battlesnds files are `<source_slot>_*.ogg` imports
    into the target as `<target_slot>_*.ogg`."""
    import zipfile
    source = tmp_path / "src"
    _populate_install(source, slot=220)
    bs_dir = source / "Data-1.13" / "Battlesnds"
    bs_dir.mkdir(parents=True)
    (bs_dir / "220_ATTN.ogg").write_bytes(b"hit")
    (bs_dir / "220_DYING.ogg").write_bytes(b"argh")

    out = tmp_path / "bundle.wmerc"
    export_merc(source, ui_index=220, out_path=out)

    target = tmp_path / "tgt"
    _empty_install(target)

    deploy_import(target, out, target_slot=234)
    # The slot prefix must have been rewritten from 220 -> 234
    assert (target / "Data-1.13" / "Battlesnds" / "234_ATTN.ogg").is_file()
    assert (target / "Data-1.13" / "Battlesnds" / "234_DYING.ogg").is_file()
    # And the original 220-prefixed files should NOT exist in the target
    assert not (target / "Data-1.13" / "Battlesnds" / "220_ATTN.ogg").is_file()


def test_comprehensive_import_upserts_table_rows(tmp_path: Path) -> None:
    """Table-row fragments in the bundle upsert into matching tables on import,
    with the slot tag rewritten to the new target slot."""
    import zipfile
    source = tmp_path / "src"
    _populate_install(source, slot=220)
    opinions_path = source / "Data-1.13" / "TableData" / "MercOpinions.xml"
    opinions_path.write_text(
        "<OPINION_LIST>\n"
        "\t<OPINION>\n"
        "\t\t<uiIndex>220</uiIndex>\n"
        "\t\t<Opinion0>7</Opinion0>\n"
        "\t</OPINION>\n"
        "</OPINION_LIST>\n"
    )

    out = tmp_path / "bundle.wmerc"
    export_merc(source, ui_index=220, out_path=out)

    # Target also has MercOpinions.xml (otherwise the import skips with warning)
    target = tmp_path / "tgt"
    _empty_install(target)
    target_opinions = target / "Data-1.13" / "TableData" / "MercOpinions.xml"
    target_opinions.write_text("<OPINION_LIST>\n</OPINION_LIST>\n")

    deploy_import(target, out, target_slot=234)

    # Check the target table got the row at slot 234, not slot 220
    import xml.etree.ElementTree as ET
    tree = ET.parse(str(target_opinions))
    root = tree.getroot()
    rows = [el for el in root if el.tag == "OPINION"]
    assert len(rows) == 1, f"Expected exactly 1 row, got {len(rows)}"
    slot_tag = rows[0].find("uiIndex")
    assert slot_tag is not None and slot_tag.text.strip() == "234"
    op0 = rows[0].find("Opinion0")
    assert op0 is not None and op0.text.strip() == "7"


def test_comprehensive_import_warns_when_target_lacks_table(tmp_path: Path) -> None:
    """If a bundle has a MercOpinions row but the target install lacks that
    table, the import surfaces a partial_failures entry rather than crashing."""
    source = tmp_path / "src"
    _populate_install(source, slot=220)
    opinions_path = source / "Data-1.13" / "TableData" / "MercOpinions.xml"
    opinions_path.write_text(
        "<OPINION_LIST>\n"
        "\t<OPINION>\n"
        "\t\t<uiIndex>220</uiIndex>\n"
        "\t\t<Opinion0>7</Opinion0>\n"
        "\t</OPINION>\n"
        "</OPINION_LIST>\n"
    )

    out = tmp_path / "bundle.wmerc"
    export_merc(source, ui_index=220, out_path=out)

    # Target deliberately lacks MercOpinions.xml
    target = tmp_path / "tgt"
    _empty_install(target)

    report = deploy_import(target, out, target_slot=234)
    # The merc should still import, but the MercOpinions row gets a warning
    warnings = [w for w in report.partial_failures if "MercOpinions" in w]
    assert len(warnings) == 1
    assert "skipped" in warnings[0].lower()


def test_comprehensive_import_preserves_cp1252_sibling_rows(tmp_path: Path) -> None:
    """Importing a MercOpinions row into a target whose file declares
    encoding="Windows-1252" must NOT mojibake other mercs' rows. The byte-splice
    writer leaves the <?xml?> declaration, CRLF endings, and every sibling row
    byte-for-byte intact — regression for the old whole-file UTF-8 ET reflow,
    which dropped the declaration and transcoded cp1252 (é=0xE9) to UTF-8
    (0xC3 0xA9) for every OTHER merc in the shared file."""
    source = tmp_path / "src"
    _populate_install(source, slot=220)
    opinions_path = source / "Data-1.13" / "TableData" / "MercOpinions.xml"
    opinions_path.write_text(
        "<MERCOPINIONS>\n"
        "\t<OPINION>\n"
        "\t\t<uiIndex>220</uiIndex>\n"
        '\t\t<AnOpinion id="0" modifier="7" />\n'
        "\t</OPINION>\n"
        "</MERCOPINIONS>\n"
    )

    out = tmp_path / "bundle.wmerc"
    export_merc(source, ui_index=220, out_path=out)

    # Target MercOpinions.xml: cp1252 declaration + a SIBLING row (slot 30) whose
    # nickname carries a Windows-1252 'é' (0xE9), CRLF endings.
    target = tmp_path / "tgt"
    _empty_install(target)
    target_opinions = target / "Data-1.13" / "TableData" / "MercOpinions.xml"
    sibling = (
        b"\t<OPINION>\r\n\t\t<uiIndex>30</uiIndex>\r\n\t\t<zNickname>Ren\xe9e</zNickname>\r\n"
        b'\t\t<AnOpinion id = "0" modifier = "5" />\r\n\t</OPINION>\r\n'
    )
    target_opinions.write_bytes(
        b'<?xml version="1.0" encoding="Windows-1252"?>\r\n'
        b"<MERCOPINIONS>\r\n" + sibling + b"</MERCOPINIONS>\r\n"
    )

    deploy_import(target, out, target_slot=234)

    after = target_opinions.read_bytes()
    # The cp1252 sibling row + the encoding declaration survive byte-for-byte.
    assert sibling in after
    assert after.startswith(b'<?xml version="1.0" encoding="Windows-1252"?>\r\n')
    assert b"Ren\xe9e" in after          # 0xE9 NOT transcoded...
    assert b"\xc3\xa9" not in after      # ...and no UTF-8 mojibake introduced
    # The imported row landed at the new slot.
    assert b"<uiIndex>234</uiIndex>" in after


def test_export_excludes_facegear_table_row(tmp_path: Path) -> None:
    """FaceGear.xml's <uiIndex> is an inventory ITEM id, not a merc slot, so a
    bundle must NOT carry a table_rows/FaceGear.xml fragment — even when the
    source install has a FaceGear row at the merc's slot number."""
    import zipfile
    source = tmp_path / "src"
    _populate_install(source, slot=220)
    fg = source / "Data-1.13" / "TableData" / "FaceGear.xml"
    fg.write_text(
        "<FACE_GEAR>\n\t<FACEGEAR>\n\t\t<uiIndex>220</uiIndex>\n"
        "\t\t<szFile>Face_SunGoggles.sti</szFile>\n\t\t<Type>4</Type>\n"
        "\t</FACEGEAR>\n</FACE_GEAR>\n"
    )
    out = tmp_path / "bundle.wmerc"
    export_merc(source, ui_index=220, out_path=out)
    with zipfile.ZipFile(out) as zf:
        names = zf.namelist()
    assert "table_rows/FaceGear.xml" not in names, \
        f"FaceGear.xml must not be bundled as a table row; got {sorted(names)}"


def test_import_skips_legacy_facegear_table_row(tmp_path: Path) -> None:
    """Legacy bundles may carry a table_rows/FaceGear.xml fragment (older
    exporters wrongly bundled it, keyed by item id). Import must SKIP it —
    never rewrite the target's FaceGear.xml — and surface the skip."""
    import zipfile
    source = tmp_path / "src"
    _populate_install(source, slot=220)
    out = tmp_path / "bundle.wmerc"
    export_merc(source, ui_index=220, out_path=out)
    # Simulate a pre-fix bundle by injecting a FaceGear.xml table row.
    with zipfile.ZipFile(out, "a") as zf:
        zf.writestr(
            "table_rows/FaceGear.xml",
            "<FACEGEAR>\n\t<uiIndex>220</uiIndex>\n\t<Type>4</Type>\n</FACEGEAR>\n",
        )

    target = tmp_path / "tgt"
    _empty_install(target)
    target_fg = target / "Data-1.13" / "TableData" / "FaceGear.xml"
    target_fg.write_text(
        "<FACE_GEAR>\n\t<FACEGEAR>\n\t\t<uiIndex>212</uiIndex>\n"
        "\t\t<szFile>Face_SunGoggles.sti</szFile>\n\t</FACEGEAR>\n</FACE_GEAR>\n"
    )
    before = target_fg.read_bytes()

    report = deploy_import(target, out, target_slot=234)

    assert target_fg.read_bytes() == before, \
        "import must not rewrite the target's FaceGear.xml"
    assert any(
        "FaceGear.xml" in w and "skipped" in w.lower()
        for w in report.partial_failures
    ), f"expected an intentional-skip note for FaceGear.xml; got {report.partial_failures}"


def test_export_excludes_civgroupnames_table_row(tmp_path: Path) -> None:
    """CivGroupNames.xml's <uiIndex> is a civ-group id (a direct index into the
    engine's zCivGroupName[NUM_CIV_GROUPS] array), not a merc profile slot, so a
    bundle must NOT carry a table_rows/CivGroupNames.xml fragment — even when the
    source install happens to have a civ-group row at the merc's slot number."""
    import zipfile
    source = tmp_path / "src"
    _populate_install(source, slot=220)
    cgn = source / "Data-1.13" / "TableData" / "CivGroupNames.xml"
    cgn.write_text(
        "<CIV_GROUP_NAMES>\n\t<NAME>\n\t\t<uiIndex>220</uiIndex>\n"
        "\t\t<szGroup>Rebels</szGroup>\n\t\t<Enabled>1</Enabled>\n"
        "\t</NAME>\n</CIV_GROUP_NAMES>\n"
    )
    out = tmp_path / "bundle.wmerc"
    export_merc(source, ui_index=220, out_path=out)
    with zipfile.ZipFile(out) as zf:
        names = zf.namelist()
    assert "table_rows/CivGroupNames.xml" not in names, \
        f"CivGroupNames.xml must not be bundled as a table row; got {sorted(names)}"


def test_import_skips_legacy_civgroupnames_table_row(tmp_path: Path) -> None:
    """Legacy bundles may carry a table_rows/CivGroupNames.xml fragment (older
    exporters wrongly bundled it, keyed by the merc's slot). Import must SKIP it —
    never rewrite the target's CivGroupNames.xml — and surface the skip. Rewriting
    a civ-group row's uiIndex to a merc slot would clobber an unrelated civ group
    (or write out of bounds past zCivGroupName[NUM_CIV_GROUPS] at engine boot)."""
    import zipfile
    source = tmp_path / "src"
    _populate_install(source, slot=220)
    out = tmp_path / "bundle.wmerc"
    export_merc(source, ui_index=220, out_path=out)
    # Simulate a pre-fix bundle by injecting a CivGroupNames.xml table row.
    with zipfile.ZipFile(out, "a") as zf:
        zf.writestr(
            "table_rows/CivGroupNames.xml",
            "<NAME>\n\t<uiIndex>220</uiIndex>\n\t<szGroup>Rebels</szGroup>\n</NAME>\n",
        )

    target = tmp_path / "tgt"
    _empty_install(target)
    target_cgn = target / "Data-1.13" / "TableData" / "CivGroupNames.xml"
    target_cgn.write_text(
        "<CIV_GROUP_NAMES>\n\t<NAME>\n\t\t<uiIndex>5</uiIndex>\n"
        "\t\t<szGroup>Kingpin</szGroup>\n\t</NAME>\n</CIV_GROUP_NAMES>\n"
    )
    before = target_cgn.read_bytes()

    report = deploy_import(target, out, target_slot=234)

    assert target_cgn.read_bytes() == before, \
        "import must not rewrite the target's CivGroupNames.xml"
    assert any(
        "CivGroupNames.xml" in w and "skipped" in w.lower()
        for w in report.partial_failures
    ), f"expected an intentional-skip note for CivGroupNames.xml; got {report.partial_failures}"


def _populate_vengeance_install(install_root: Path, slot: int = 218) -> Merc:
    """Synthetic Vengeance-style install: VFS config + Data-Vengeance dir +
    slot-prefix Speech layout. Tests the flavor-aware code paths."""
    # Top-level Ja2.ini referencing the VFS config
    install_root.mkdir(parents=True, exist_ok=True)
    (install_root / "Ja2.ini").write_text(
        "[Ja2 Settings]\nVFS_CONFIG_INI = vfs_config.test.ini\n"
    )
    # Minimal VFS config: v113 (legacy) + vengcore (mod content)
    (install_root / "vfs_config.test.ini").write_text("\n".join([
        "[vfs_config]",
        "PROFILES = v113, vengcore",
        "",
        "[PROFILE_v113]",
        "LOCATIONS = loc_v113",
        "",
        "[PROFILE_vengcore]",
        "LOCATIONS = loc_vengeance",
        "",
        "[LOC_loc_v113]",
        "TYPE = DIRECTORY",
        "PATH = Data-1.13",
        "",
        "[LOC_loc_vengeance]",
        "TYPE = DIRECTORY",
        "PATH = Data-Vengeance",
        "",
    ]))
    # Create both data dirs
    (install_root / "Data-1.13" / "TableData").mkdir(parents=True)
    veng = install_root / "Data-Vengeance"
    (veng / "TableData").mkdir(parents=True)

    # Write the merc to Data-Vengeance — the mod content layer
    merc = Merc(
        uiIndex=slot, ubFaceIndex=slot, Type=2,
        zName="Tycho-V", zNickname="Tycho-V",
        biographyText="Vengeance test merc.",
        usVoiceIndex=slot,
    )
    profiles_xml.upsert(veng / "TableData" / "MercProfiles.xml", merc)
    starting_gear.upsert(
        veng / "TableData" / "Inventory" / "MercStartingGear.xml",
        Gear(mIndex=slot, mName="Tycho-V", kits=[GearKit(mWeapon=4)]),
    )

    # MercEdt at root (Vengeance convention)
    (veng / "MercEdt").mkdir(parents=True)
    edt_mod.write_bio(
        install_root, ui_index=slot,
        biography=merc.biographyText, additional="",
        aim_bio_id=None,
    )

    # Slot-prefix Speech layout: Speech/<slot>_NNN.ogg at root
    speech_root = veng / "Speech"
    speech_root.mkdir(parents=True)
    (speech_root / f"{slot}_000.ogg").write_bytes(b"\x00")
    (speech_root / f"{slot}_001.ogg").write_bytes(b"\x01")
    (speech_root / f"{slot}_017.ogg").write_bytes(b"\x02")
    # Battlesnds at slot prefix
    bs = veng / "Battlesnds"
    bs.mkdir()
    (bs / f"{slot}_ATTN.ogg").write_bytes(b"a")
    (bs / f"{slot}_DYING.ogg").write_bytes(b"d")

    return merc


def test_export_vengeance_install_captures_slot_prefix_voice(tmp_path: Path) -> None:
    """Export from a Vengeance-style install picks up Speech/<slot>_*.ogg."""
    import zipfile
    install = tmp_path / "veng_install"
    _populate_vengeance_install(install, slot=218)

    out = tmp_path / "veng.wmerc"
    export_merc(install, ui_index=218, out_path=out)

    with zipfile.ZipFile(out) as zf:
        names = zf.namelist()
    voice = [n for n in names if n.startswith("voice/")]
    assert len(voice) == 3, f"expected 3 voice clips, got: {voice}"
    bs = [n for n in names if n.startswith("audio/battlesnds/")]
    assert len(bs) == 2


def test_deploy_import_writes_voice_to_slot_prefix_root_on_vengeance_target(tmp_path: Path) -> None:
    """Import into a Vengeance-style target routes voice clips to
    Speech/<target_slot>_*.ogg at the Speech root, NOT Speech/<voice_index>/.

    Uses slot 189 (in the expanded-MERC 188-199 range) for the target so
    the bio routes cleanly without an aim_bio_id requirement.
    """
    src = tmp_path / "src"
    _populate_vengeance_install(src, slot=218)
    bundle = tmp_path / "bundle.wmerc"
    export_merc(src, ui_index=218, out_path=bundle)

    # Build a target Vengeance install with an empty slot 189 (expanded MERC)
    target = tmp_path / "tgt"
    _populate_vengeance_install(target, slot=219)

    deploy_import(target, bundle, target_slot=189)

    speech_root = target / "Data-Vengeance" / "Speech"
    # The 218_* files from the source should be renamed to 189_* at the root
    assert (speech_root / "189_000.ogg").is_file()
    assert (speech_root / "189_001.ogg").is_file()
    assert (speech_root / "189_017.ogg").is_file()
    # And there should be NO Speech/218/ subdir (the wrong, vanilla-flavored placement)
    assert not (speech_root / "218").is_dir()


def test_cross_mod_schema_diff_warns_on_opinions_format(tmp_path: Path) -> None:
    """Bundle with dense MercOpinions targets a sparse install — pre-write
    check emits a warning into report.partial_failures."""
    # Source: vanilla install + dense MercOpinions file (Vengeance-style)
    src = tmp_path / "src"
    _populate_install(src, slot=220)
    (src / "Data-1.13" / "TableData" / "MercOpinions.xml").write_text(
        "<OPINION_LIST>\n"
        "\t<OPINION>\n"
        "\t\t<uiIndex>220</uiIndex>\n"
        "\t\t<Opinion0>5</Opinion0>\n"
        "\t</OPINION>\n"
        "</OPINION_LIST>\n"
    )

    bundle = tmp_path / "bundle.wmerc"
    export_merc(src, ui_index=220, out_path=bundle)

    # Target: vanilla install with SPARSE MercOpinions format
    target = tmp_path / "tgt"
    _empty_install(target)
    (target / "Data-1.13" / "TableData" / "MercOpinions.xml").write_text(
        "<OPINION_LIST>\n"
        "\t<OPINION>\n"
        "\t\t<uiIndex>0</uiIndex>\n"
        "\t\t<AnOpinion id=\"1\" modifier=\"3\"/>\n"
        "\t</OPINION>\n"
        "</OPINION_LIST>\n"
    )

    report = deploy_import(target, bundle, target_slot=234)
    # Expect a partial_failure noting the format mismatch
    opinions_warning = [w for w in report.partial_failures if "MercOpinions" in w and "format" in w]
    assert len(opinions_warning) >= 1, f"Expected MercOpinions format warning, got: {report.partial_failures}"


def test_restore_after_failed_import_cleans_orphan_files(tmp_path: Path) -> None:
    """End-to-end: simulate the slot-199 disaster — orphan files left after
    a successful import. Restore the auto-backup; orphan files must be gone."""
    import importlib
    src = tmp_path / "src"
    _populate_install(src, slot=220)
    (src / "Data-1.13" / "Battlesnds").mkdir(parents=True)
    (src / "Data-1.13" / "Battlesnds" / "220_ATTN.ogg").write_bytes(b"hit")

    bundle = tmp_path / "bundle.wmerc"
    export_merc(src, ui_index=220, out_path=bundle)

    target = tmp_path / "tgt"
    _empty_install(target)
    backup_base = tmp_path / "backups"

    # Patch backup's _appdata_root for this test
    from mercwizard_core import backup as backup_mod
    orig = backup_mod._appdata_root
    backup_mod._appdata_root = lambda: backup_base
    try:
        report = deploy_import(target, bundle, target_slot=234)
        # Battlesnds 234_ATTN.ogg should exist after the import
        new_audio = target / "Data-1.13" / "Battlesnds" / "234_ATTN.ogg"
        assert new_audio.is_file()

        # Find the snapshot and restore
        backups = backup_mod.list_backups("target_install", base=backup_base)
        # Snapshot install_id defaults to "import" — let me use that
        snaps = backup_mod.list_backups("import", base=backup_base)
        assert len(snaps) >= 1
        backup_mod.restore(snaps[0].id, "import", target, base=backup_base)

        # The orphan should be gone now
        assert not new_audio.is_file(), "Restore should have deleted the orphan audio file"
    finally:
        backup_mod._appdata_root = orig


def test_cross_install_move_empty_source_raises(tmp_path: Path) -> None:
    source = tmp_path / "source"
    _empty_install(source)
    target = tmp_path / "target"
    _empty_install(target)

    with pytest.raises(ValueError):
        move_between_installs(
            source_install=source,
            source_install_id="source_id",
            target_install=target,
            target_install_id="target_id",
            source_slot=220,
            target_slot=234,
        )


def test_export_auto_extracts_animation_subframes_from_smallface_sti(tmp_path: Path) -> None:
    """When the source install has an 8-frame SmallFace STI, export bundles
    each animation sub-frame as a separate PNG so import can preserve
    hand-authored blink/talk pixels (Eskimo blink regression fix).
    """
    from PIL import Image
    from mercwizard_core.portrait.compile import compile_and_write_all

    install = tmp_path / "install"
    _populate_install(install, slot=220)

    # Compile a SmallFace STI for face_index=220 with a distinctive
    # explicit eye frame so the auto-extractor sees real animation pixels.
    import io
    base_buf = io.BytesIO()
    base = Image.new("RGBA", (48, 43), (200, 150, 120, 255))
    base.save(base_buf, format="PNG")
    eye_buf = io.BytesIO()
    distinct_eye = Image.new("RGBA", (17, 6), (255, 0, 255, 255))
    distinct_eye.save(eye_buf, format="PNG")

    compile_and_write_all(
        install_root=install,
        face_index=220,
        source_png_bytes=base_buf.getvalue(),
        explicit_eye_pngs=[eye_buf.getvalue()],
    )

    out = tmp_path / "tycho.wmerc"
    export_merc(install, ui_index=220, out_path=out)

    contents = read_wmerc(out)
    assert "anim_eye_1.png" in contents.files, "auto-extract missed anim_eye_1"
    assert "anim_eye_2.png" in contents.files
    assert "anim_mouth_1.png" in contents.files
    assert "bigface_source.png" in contents.files


def test_bundle_round_trip_preserves_explicit_animation_frames(tmp_path: Path) -> None:
    """End-to-end fidelity: explicit eye frames survive export -> import.

    Compile a SmallFace with a distinctive eye frame, export the merc,
    import it into a fresh install, decode the imported SmallFace STI,
    and confirm the eye sub-frame still carries the distinctive pixels.
    Before the auto-extract fix this round-trip lost animation — only
    frame 0 (the base) was bundled and the importer recompiled with
    skip-mode frames.
    """
    from PIL import Image
    from ja2py.fileformats.Sti import load_8bit_sti
    from mercwizard_core.portrait.compile import compile_and_write_all

    # --- Source install: compile a SmallFace with a magenta eye frame ---
    source = tmp_path / "source"
    _populate_install(source, slot=220)
    import io
    base = Image.new("RGBA", (48, 43), (180, 140, 100, 255))
    base_buf = io.BytesIO()
    base.save(base_buf, format="PNG")
    distinct_eye = Image.new("RGBA", (17, 6), (255, 0, 255, 255))
    eye_buf = io.BytesIO()
    distinct_eye.save(eye_buf, format="PNG")
    compile_and_write_all(
        install_root=source, face_index=220,
        source_png_bytes=base_buf.getvalue(),
        explicit_eye_pngs=[eye_buf.getvalue()],
    )

    # --- Export ---
    out = tmp_path / "bundle.wmerc"
    export_merc(source, ui_index=220, out_path=out)

    # --- Fresh target install ---
    target = tmp_path / "target"
    _empty_install(target)
    # Need a portrait_source.png for the import path to take the recompile
    # branch. The export should have included it; the test below relies on
    # the auto-extracted anim frames being USED on top of that recompile.
    deploy_import(
        install_root=target, bundle_path=out,
        install_id="target_install", target_slot=234, force=True,
    )

    # --- Decode the target's SmallFace and check the magenta eye survived ---
    sti_path = target / "Data-1.13" / "faces" / "220.sti"
    # ubFaceIndex stays at 220 (the source merc's), even though uiIndex moved
    assert sti_path.is_file(), f"SmallFace STI missing at {sti_path}"
    with open(sti_path, "rb") as f:
        loaded = load_8bit_sti(f)
    assert len(loaded.images) == 8
    palette = list(loaded.palette.tobytes())
    eye_frame_pixels = list(loaded.images[1].image.getdata())
    magenta_indices = {
        i for i in range(256)
        if palette[i*3] > 200 and palette[i*3+1] < 80 and palette[i*3+2] > 200
    }
    assert magenta_indices, "magenta missing from target STI palette after round-trip"
    assert any(p in magenta_indices for p in eye_frame_pixels), (
        "round-trip lost the distinctive magenta eye frame — animation "
        "preservation is broken"
    )


def test_legacy_bundle_with_stale_merc_availability_row_is_silently_ignored(
    tmp_path: Path,
) -> None:
    """Regression: a legacy bundle (Vengeance Eskimo export, pre-2026-05-14)
    carries `table_rows/MercAvailability.xml` for the source slot's row.
    Processing that row clobbers the importer's auto-allocated MercBioID
    and lands Eskimo's bio past the target install's MERCBIOS.EDT EOF
    (reference-install regression — Eskimo displayed as Narg).

    The fix: `_install_table_rows` silently skips AIMAvailability.xml,
    MercAvailability.xml, and Vehicles.xml. No partial_failure warning
    (because the right action is "do nothing" — they're canonically
    carried by manifest.aim_binding / manifest.merc_binding).
    """
    install = tmp_path / "install"
    _empty_install(install)
    # Pre-populate MercAvailability with a clean baseline so the importer
    # has a place to write the auto-allocated row.
    (install / "Data-1.13" / "TableData" / "MercAvailability.xml").write_text(
        "<MERC_AVAILABLES></MERC_AVAILABLES>", encoding="utf-8"
    )

    # Hand-build a wmerc bundle that mimics the Vengeance Eskimo export:
    # Type=2 merc + stale table_rows/MercAvailability.xml with MercBioID
    # that's NOT what the importer would auto-allocate, plus stray
    # AIMAvailability.xml + Vehicles.xml entries that earlier triggered
    # "unrecognized" warnings.
    merc = Merc(
        uiIndex=218, ubFaceIndex=218, Type=2,
        zName="Eskimo", zNickname="Eskimo",
        biographyText="I got yer.",
        additionalInfoText="",
        usVoiceIndex=218,
    )
    manifest = WmercManifest(merc=merc, gear=[])
    bundle_path = tmp_path / "eskimo.wmerc"
    stale_merc_avail_row = (
        "<MERC>\n"
        "  <uiIndex>45</uiIndex>\n"
        "  <Name>Eskimo</Name>\n"
        "  <MercBioID>47</MercBioID>\n"  # ← past the reference install's MERCBIOS.EDT EOF
        "  <ProfilId>218</ProfilId>\n"
        "</MERC>\n"
    )
    stale_aim_avail_row = (
        "<AIM>\n"
        "  <uiIndex>218</uiIndex>\n"
        "  <ProfilId>-1</ProfilId>\n"
        "  <AimBioID>-1</AimBioID>\n"
        "</AIM>\n"
    )
    stale_vehicles_row = "<VEHICLE><uiIndex>218</uiIndex></VEHICLE>\n"

    with zipfile.ZipFile(bundle_path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("manifest.json", json.dumps(manifest.model_dump(mode="json")))
        zf.writestr("table_rows/MercAvailability.xml", stale_merc_avail_row)
        zf.writestr("table_rows/AIMAvailability.xml", stale_aim_avail_row)
        zf.writestr("table_rows/Vehicles.xml", stale_vehicles_row)

    report = deploy_import(
        install_root=install, bundle_path=bundle_path,
        install_id="test_install", target_slot=198,
    )

    # No UNRECOGNIZED-table warnings for the three intentional skips
    # — they may appear as "intentionally skipped" info per TODO #12
    # (the import report now surfaces what was deliberately dropped
    # vs what was mystery-dropped), but they must NOT carry the
    # "unrecognized table name" wording reserved for actual mystery
    # entries from a future bundle format.
    failures_blob = "\n".join(report.partial_failures)
    assert "unrecognized" not in failures_blob.lower(), \
        f"unrecognized-table warning leaked through: {failures_blob!r}"
    # Intentional-skip notes are present (informational, not error).
    assert "intentionally skipped" in failures_blob, \
        "INTENTIONAL_SKIPS should surface as informational entries (TODO #12)"

    # Auto-allocated MercBioID stuck — stale 47 from the bundled row did NOT
    # win. compute_merc_bio_id picks the lowest free in [0, 199] for slot
    # 198 on an otherwise-empty install — typically 11 (just past vanilla 0-10).
    from mercwizard_core.inject import merc_availability as ma
    merc_xml = install / "Data-1.13" / "TableData" / "MercAvailability.xml"
    rows = ma.read_all(merc_xml)
    assert 198 in rows, "import didn't write the MercAvailability row"
    assert rows[198].MercBioID != 47, \
        "stale MercBioID from bundled table_rows row clobbered the auto-allocation"


def test_manifest_exported_at_preserved_across_round_trip(tmp_path: Path) -> None:
    """A v1 binary parsing a v2 bundle must NOT overwrite the source's
    exported_at with the parse-time timestamp. Pre-fix the field used
    `default_factory=lambda: datetime.now(...)` which re-fired at parse
    time too. Now it's Optional[str] = None — the export-side constructor
    sets it explicitly, parse-side leaves it alone.
    """
    original = "2026-01-01T00:00:00+00:00"
    merc = Merc(
        uiIndex=220, ubFaceIndex=220, Type=1,
        zName="Tycho", zNickname="Tycho",
        biographyText="A grizzled ranger.",
        additionalInfoText="",
        usVoiceIndex=220,
    )
    manifest = WmercManifest(merc=merc, gear=[], exported_at=original)
    bundle_path = tmp_path / "tycho.wmerc"
    with zipfile.ZipFile(bundle_path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("manifest.json", json.dumps(manifest.model_dump(mode="json")))

    parsed = read_wmerc(bundle_path)
    assert parsed.manifest.exported_at == original, (
        f"exported_at re-fired at parse time: got {parsed.manifest.exported_at!r}, "
        f"expected {original!r}"
    )


def test_import_rollback_covers_step_10_mid_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Bug A1 — pre-fix, a mid-step-10 failure left already-written
    files on disk because `record_files_created` was only invoked at the
    very end. Each step is now wrapped in try/except + `_rollback_and_raise`
    so a step-10 escape reverts everything written so far — step 7's
    profile/AIM/gear/EDT writes AND step 10's partial writes.
    """
    from mercwizard_core import backup as backup_mod
    from mercwizard_core.bundle import deploy_import, import_ as import_module

    # Isolate AppData so the test doesn't write to the user's real backups dir
    appdata_root = tmp_path / "appdata"
    appdata_root.mkdir()
    monkeypatch.setenv("APPDATA", str(appdata_root))

    target = tmp_path / "target"
    _empty_install(target)

    merc = Merc(
        uiIndex=220, ubFaceIndex=220, Type=1,
        zName="Tycho", zNickname="Tycho",
        biographyText="bio", additionalInfoText="",
        usVoiceIndex=220,
    )
    manifest = WmercManifest(
        merc=merc,
        gear=[GearKit(mWeapon=4)],
        aim_binding=AimBinding(
            uiIndex=220, description="Tycho", ProfilId=220, AimBioID=52
        ),
        exported_at="2026-01-01T00:00:00+00:00",
    )
    bundle_path = tmp_path / "tycho.wmerc"
    with zipfile.ZipFile(bundle_path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("manifest.json", json.dumps(manifest.model_dump(mode="json")))
        # An audio/battlesnds/ entry would normally flow through step 10
        # but the monkeypatch replaces _install_extras entirely so this
        # arc isn't exercised — included for realism.
        zf.writestr("audio/battlesnds/220_HIT.wav", b"FAKEWAV")

    # Replace _install_extras with a function that writes one sentinel file
    # then raises OSError. The wrap in deploy_import should catch it,
    # roll back, and re-raise as RuntimeError mentioning "Step 10".
    sentinel = target / "Data-1.13" / "Battlesnds" / "SENTINEL_220_HIT.wav"

    def failing_install_extras(*, contents, target_ctx, source_slot, target_slot,
                                face_index, report, portrait_already_compiled=False):
        sentinel.parent.mkdir(parents=True, exist_ok=True)
        sentinel.write_bytes(b"partial step-10 write")
        report.files_written.append(str(sentinel))
        raise OSError("simulated step 10 disk failure")

    monkeypatch.setattr(import_module, "_install_extras", failing_install_extras)

    with pytest.raises(RuntimeError, match="Step 10"):
        deploy_import(target, bundle_path, install_id="import")

    # Step 10's partial write got rolled back
    assert not sentinel.is_file(), (
        "Step 10's partial file write was not rolled back — A1 regression. "
        "The whole point of the fix is that mid-step-10 writes should be "
        "reverted by the auto-restore."
    )

    # Step 7's writes got rolled back too (full rollback semantics).
    # Target was an empty install before deploy_import, so profile/AIM/gear
    # were CREATED by step 7. Restore's files_created phase deletes them.
    profiles_path = target / "Data-1.13" / "TableData" / "MercProfiles.xml"
    if profiles_path.is_file():
        rows = profiles_xml.read_all(profiles_path)
        assert 220 not in rows, (
            "Step 7 profile upsert not rolled back. After the rollback, "
            "the freshly-created profile row should be gone."
        )

    # Snapshot still exists so the user could manually re-restore from
    # the Backups page if they want to investigate.
    snaps = backup_mod.list_backups("import")
    assert len(snaps) >= 1, (
        "Backup snapshot missing post-rollback — user has no recovery handle"
    )


# ─────────────────────────────────────────────────────────────────────
#  Voice .gap lip-sync sidecar: export bundling + import sync
#
#  The exporter must carry a clip's authored .gap (Change: export.py); the
#  importer's slot_prefix path must keep each clip's .gap in sync (Change:
#  import_.py _step9_voice) — preferring a CARRIED gap, else generating from a
#  .wav, else clearing a stale one. Hermetic tmp_path Vengeance installs only.
# ─────────────────────────────────────────────────────────────────────


def test_export_bundles_voice_gap_sidecar(tmp_path: Path) -> None:
    """Export carries a clip's authored .gap into the voice/ bucket.

    Without this, a Vengeance .ogg clip's hand-made gap (which can't be
    regenerated on import) is lost at export and lip-sync breaks at the source.
    """
    install = tmp_path / "veng"
    _populate_vengeance_install(install, slot=218)
    speech_root = install / "Data-Vengeance" / "Speech"
    authored = gap.gaps_to_bytes([(2, 73), (978, 1206)])
    (speech_root / "218_017.gap").write_bytes(authored)

    out = tmp_path / "veng.wmerc"
    export_merc(install, ui_index=218, out_path=out)

    contents = read_wmerc(out)
    assert "voice/218_017.gap" in contents.files, (
        "authored .gap not bundled; voice entries: "
        f"{sorted(n for n in contents.files if n.startswith('voice/'))}"
    )
    assert contents.files["voice/218_017.gap"] == authored, "gap bytes altered on export"


def test_import_preserves_carried_voice_gap_verbatim(tmp_path: Path) -> None:
    """A bundle that CARRIES an authored .gap (Vengeance .ogg clip) lands it
    verbatim beside the imported clip — never regenerated, never deleted. This
    is the whole point of the round-trip for ogg clips, whose gaps can't be
    regenerated by stdlib `wave`."""
    source = tmp_path / "src"
    _populate_vengeance_install(source, slot=218)
    speech_root = source / "Data-Vengeance" / "Speech"
    authored = gap.gaps_to_bytes([(2, 73), (978, 1206)])
    (speech_root / "218_017.gap").write_bytes(authored)

    bundle = tmp_path / "veng.wmerc"
    export_merc(source, ui_index=218, out_path=bundle)
    assert "voice/218_017.gap" in read_wmerc(bundle).files  # precondition

    target = tmp_path / "tgt"
    _populate_vengeance_install(target, slot=219)
    deploy_import(target, bundle, target_slot=189)

    tgt_speech = target / "Data-Vengeance" / "Speech"
    assert (tgt_speech / "189_017.ogg").is_file(), "clip not imported to slot-prefix root"
    gap_dst = tgt_speech / "189_017.gap"
    assert gap_dst.is_file(), "carried .gap was not written beside the imported clip"
    assert gap_dst.read_bytes() == authored, "carried .gap not preserved verbatim"


def test_import_generates_gap_for_wav_clip(tmp_path: Path) -> None:
    """A bundled .wav clip with NO carried .gap gets a fresh gap generated on
    import (slot_prefix path that previously did neither)."""
    source = tmp_path / "src"
    _populate_vengeance_install(source, slot=218)
    speech_root = source / "Data-Vengeance" / "Speech"
    # A real PCM WAV with an internal silence (300ms tone / 300ms silence / 300ms tone).
    (speech_root / "218_022.wav").write_bytes(
        _make_wav([(1.0, 300), (0.0, 300), (1.0, 300)])
    )

    bundle = tmp_path / "veng.wmerc"
    export_merc(source, ui_index=218, out_path=bundle)
    assert "voice/218_022.gap" not in read_wmerc(bundle).files  # no carried gap

    target = tmp_path / "tgt"
    _populate_vengeance_install(target, slot=219)
    deploy_import(target, bundle, target_slot=189)

    tgt_speech = target / "Data-Vengeance" / "Speech"
    assert (tgt_speech / "189_022.wav").is_file()
    gap_dst = tgt_speech / "189_022.gap"
    assert gap_dst.is_file(), "no gap generated for the .wav clip on slot_prefix import"
    pairs = gap.parse_gap_bytes(gap_dst.read_bytes())
    # Ascending, non-overlapping, with a gap roughly over the silent middle.
    prev_end = -1
    for s, e in pairs:
        assert e > s and s >= prev_end, f"malformed gap {(s, e)} after {prev_end}"
        prev_end = e
    assert any(abs(s - 300) <= 40 and abs(e - 600) <= 40 for s, e in pairs), pairs


def test_import_clears_stale_gap_on_overwrite(tmp_path: Path) -> None:
    """Re-importing a clip whose new bundle carries NEITHER a gap nor a
    decodable .wav must clear a stale .gap from a prior import, so the old
    silence map can't mis-sync the new audio."""
    source = tmp_path / "src"
    _populate_vengeance_install(source, slot=218)  # 218_017.ogg, no gap authored

    bundle = tmp_path / "veng.wmerc"
    export_merc(source, ui_index=218, out_path=bundle)
    assert "voice/218_017.gap" not in read_wmerc(bundle).files  # no carried gap

    target = tmp_path / "tgt"
    _populate_vengeance_install(target, slot=219)
    stale = target / "Data-Vengeance" / "Speech" / "189_017.gap"
    stale.write_bytes(gap.gaps_to_bytes([(100, 200)]))  # left from a prior import
    assert stale.is_file()

    deploy_import(target, bundle, target_slot=189)

    assert (target / "Data-Vengeance" / "Speech" / "189_017.ogg").is_file()
    assert not stale.exists(), "stale .gap survived an overwrite with a gap-less clip"


# ── Backgrounds: carry the merc's real background, recreate safely ───────────

def test_export_bundles_background_by_usbackground_not_slot(tmp_path: Path) -> None:
    """The bundle carries the background the merc's usBackground points at (its
    own id), NOT the background whose id coincides with the merc's slot."""
    source = tmp_path / "src"
    merc = _populate_install(source, slot=220)
    # usBackground 37 — and a decoy background whose id == the merc slot (220).
    profiles_xml.upsert(
        source / "Data-1.13" / "TableData" / "MercProfiles.xml",
        merc.model_copy(update={"usBackground": 37}),
    )
    (source / "Data-1.13" / "TableData" / "Backgrounds.xml").write_text(
        "<BACKGROUNDS>\n"
        "\t<BACKGROUND>\n\t\t<uiIndex>37</uiIndex>\n\t\t<szName>Hunter</szName>\n"
        "\t\t<szShortName>Hun</szShortName>\n\t\t<szDescription>Tracks game.</szDescription>\n"
        "\t</BACKGROUND>\n"
        "\t<BACKGROUND>\n\t\t<uiIndex>220</uiIndex>\n\t\t<szName>SlotCollision</szName>\n"
        "\t</BACKGROUND>\n"
        "</BACKGROUNDS>\n"
    )
    out = tmp_path / "bundle.wmerc"
    export_merc(source, ui_index=220, out_path=out)
    with zipfile.ZipFile(out) as zf:
        assert "table_rows/Backgrounds.xml" in zf.namelist()
        block = zf.read("table_rows/Backgrounds.xml").decode("utf-8")
    assert "<uiIndex>37</uiIndex>" in block and "Hunter" in block
    assert "<uiIndex>220</uiIndex>" not in block, "must key by usBackground (37), not slot (220)"


def test_export_omits_background_when_usbackground_none(tmp_path: Path) -> None:
    """usBackground 0 (none/template) → no background carried, even if the
    catalog has a decoy at the merc's slot id."""
    source = tmp_path / "src"
    _populate_install(source, slot=220)  # leaves usBackground at its 0 default
    (source / "Data-1.13" / "TableData" / "Backgrounds.xml").write_text(
        "<BACKGROUNDS>\n\t<BACKGROUND>\n\t\t<uiIndex>220</uiIndex>\n"
        "\t\t<szName>SlotCollision</szName>\n\t</BACKGROUND>\n</BACKGROUNDS>\n"
    )
    out = tmp_path / "bundle.wmerc"
    export_merc(source, ui_index=220, out_path=out)
    with zipfile.ZipFile(out) as zf:
        assert "table_rows/Backgrounds.xml" not in zf.namelist()


def test_import_recreates_missing_background_without_reorder(tmp_path: Path) -> None:
    """A bundled background missing from the target is recreated by its OWN id,
    spliced BEFORE the physical-last entry (num_found_background unchanged), with
    CRLF preserved and existing entries left intact."""
    source = tmp_path / "src"
    _populate_install(source, slot=220)
    out = tmp_path / "bundle.wmerc"
    export_merc(source, ui_index=220, out_path=out)
    with zipfile.ZipFile(out, "a") as zf:
        zf.writestr(
            "table_rows/Backgrounds.xml",
            "<BACKGROUND>\n\t<uiIndex>37</uiIndex>\n\t<szName>Hunter</szName>\n"
            "\t<szShortName>Hun</szShortName>\n\t<szDescription>Tracks game.</szDescription>\n"
            "</BACKGROUND>\n",
        )
    target = tmp_path / "tgt"
    _empty_install(target)
    target_bg = target / "Data-1.13" / "TableData" / "Backgrounds.xml"
    # CRLF, tab-indented, physical tail (#5) != max id (#50): proves no reorder.
    before = (
        "<BACKGROUNDS>\r\n"
        "\t<BACKGROUND>\r\n\t\t<uiIndex>1</uiIndex>\r\n\t\t<szName>Soldier</szName>\r\n\t</BACKGROUND>\r\n"
        "\t<BACKGROUND>\r\n\t\t<uiIndex>50</uiIndex>\r\n\t\t<szName>Doctor</szName>\r\n\t</BACKGROUND>\r\n"
        "\t<BACKGROUND>\r\n\t\t<uiIndex>5</uiIndex>\r\n\t\t<szName>Medic</szName>\r\n\t</BACKGROUND>\r\n"
        "</BACKGROUNDS>\r\n"
    ).encode("utf-8")
    target_bg.write_bytes(before)

    deploy_import(target, out, target_slot=234)

    data = target_bg.read_bytes()
    text = data.decode("utf-8")
    assert "<uiIndex>37</uiIndex>" in text and "Hunter" in text, "background #37 must be created"
    # CRLF preserved everywhere — no lone LF introduced by the splice.
    assert b"\r\n" in data and b"\n" not in data.replace(b"\r\n", b""), "CRLF must be preserved"
    # Physical tail (last uiIndex in document order) is still #5 → IMP bound unchanged.
    import re as _re
    ids = _re.findall(r"<uiIndex>(\d+)</uiIndex>", text)
    assert ids[-1] == "5", f"physical tail must stay #5 (no reorder); got {ids}"
    # The three original entries survive byte-for-byte (no whole-file reflow).
    for orig in (b"\t\t<uiIndex>1</uiIndex>", b"\t\t<uiIndex>50</uiIndex>", b"\t\t<uiIndex>5</uiIndex>"):
        assert orig in data


def test_import_background_noop_when_id_present(tmp_path: Path) -> None:
    """If the target already has the background id, import does NOT overwrite it
    (shared catalog entry preserved) — the file is left byte-identical."""
    source = tmp_path / "src"
    _populate_install(source, slot=220)
    out = tmp_path / "bundle.wmerc"
    export_merc(source, ui_index=220, out_path=out)
    with zipfile.ZipFile(out, "a") as zf:
        zf.writestr(
            "table_rows/Backgrounds.xml",
            "<BACKGROUND>\n\t<uiIndex>37</uiIndex>\n\t<szName>IMPORTED</szName>\n</BACKGROUND>\n",
        )
    target = tmp_path / "tgt"
    _empty_install(target)
    target_bg = target / "Data-1.13" / "TableData" / "Backgrounds.xml"
    before = (
        "<BACKGROUNDS>\r\n\t<BACKGROUND>\r\n\t\t<uiIndex>37</uiIndex>\r\n"
        "\t\t<szName>EXISTING</szName>\r\n\t</BACKGROUND>\r\n</BACKGROUNDS>\r\n"
    ).encode("utf-8")
    target_bg.write_bytes(before)

    deploy_import(target, out, target_slot=234)

    assert target_bg.read_bytes() == before, "existing background must not be overwritten"


def test_import_background_preserves_nested_and_multiline(tmp_path: Path) -> None:
    """A carried background with a multi-line description and nested <drugtypes>
    is recreated verbatim (structure intact, valid XML)."""
    from lxml import etree
    source = tmp_path / "src"
    _populate_install(source, slot=220)
    out = tmp_path / "bundle.wmerc"
    export_merc(source, ui_index=220, out_path=out)
    with zipfile.ZipFile(out, "a") as zf:
        zf.writestr(
            "table_rows/Backgrounds.xml",
            "<BACKGROUND>\n\t<uiIndex>88</uiIndex>\n\t<szName>Junkie</szName>\n"
            "\t<szDescription>Line one.\nLine two.</szDescription>\n"
            "\t<drugtypes>\n\t\t<DRUG>3</DRUG>\n\t\t<DRUG>7</DRUG>\n\t</drugtypes>\n"
            "</BACKGROUND>\n",
        )
    target = tmp_path / "tgt"
    _empty_install(target)
    target_bg = target / "Data-1.13" / "TableData" / "Backgrounds.xml"
    target_bg.write_bytes(
        ("<BACKGROUNDS>\r\n\t<BACKGROUND>\r\n\t\t<uiIndex>1</uiIndex>\r\n"
         "\t\t<szName>Soldier</szName>\r\n\t</BACKGROUND>\r\n</BACKGROUNDS>\r\n").encode("utf-8")
    )

    deploy_import(target, out, target_slot=234)

    root = etree.fromstring(target_bg.read_bytes())
    bg88 = [b for b in root.findall("BACKGROUND") if b.findtext("uiIndex") == "88"]
    assert len(bg88) == 1, "background #88 should exist exactly once"
    assert [d.text for d in bg88[0].findall("drugtypes/DRUG")] == ["3", "7"], "nested drug list preserved"
    assert "Line one." in (bg88[0].findtext("szDescription") or "")
    assert "Line two." in (bg88[0].findtext("szDescription") or "")


def test_import_background_into_nonutf8_target_succeeds_preserving_bytes(tmp_path: Path) -> None:
    """A non-UTF-8 (Windows-1252) target Backgrounds.xml is now read losslessly
    (latin-1), so importing a carried background SUCCEEDS rather than degrading:
    the block is spliced in, the target's existing high-byte entry is preserved
    byte-for-byte, and the import never aborts. (Previously this degraded to a
    Backgrounds.xml partial failure because the splice writer decoded utf-8.)"""
    source = tmp_path / "src"
    _populate_install(source, slot=220)
    out = tmp_path / "bundle.wmerc"
    export_merc(source, ui_index=220, out_path=out)
    with zipfile.ZipFile(out, "a") as zf:
        zf.writestr(
            "table_rows/Backgrounds.xml",
            "<BACKGROUND>\n\t<uiIndex>37</uiIndex>\n\t<szName>Hunter</szName>\n</BACKGROUND>\n",
        )
    target = tmp_path / "tgt"
    _empty_install(target)
    target_bg = target / "Data-1.13" / "TableData" / "Backgrounds.xml"
    # 0xe9 = 'é' in Windows-1252 — invalid as UTF-8.
    target_bg.write_bytes(
        b"<BACKGROUNDS>\r\n\t<BACKGROUND>\r\n\t\t<uiIndex>1</uiIndex>\r\n"
        b"\t\t<szName>Caf\xe9 Owner</szName>\r\n\t</BACKGROUND>\r\n</BACKGROUNDS>\r\n"
    )

    report = deploy_import(target, out, target_slot=234)  # must NOT raise

    assert any("MercProfiles.xml" in p for p in report.files_written), "merc must still import"
    # The cp1252 target is now readable, so the background import SUCCEEDS — no
    # Backgrounds.xml partial failure.
    assert not any("Backgrounds.xml" in w for w in report.partial_failures), \
        "cp1252 target should import cleanly now, not degrade"
    raw = target_bg.read_bytes()
    assert raw.count(b"Caf\xe9 Owner") == 1, "existing high-byte entry preserved byte-for-byte"
    assert b"<uiIndex>37</uiIndex>" in raw and b"Hunter" in raw, "carried background was created"


def test_read_wmerc_caps_oversized_entry(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A .wmerc entry whose declared uncompressed size exceeds the per-entry cap
    is skipped (-> read_errors) instead of being read into memory — the zip-bomb
    self-DoS guard. The (small) manifest still parses."""
    from mercwizard_core.bundle import import_ as imp

    # Borrow a real, valid manifest.json from an exported bundle.
    install = tmp_path / "install"
    _populate_install(install)
    base = tmp_path / "base.wmerc"
    export_merc(install, ui_index=220, out_path=base)
    with zipfile.ZipFile(base, "r") as zf:
        manifest_bytes = zf.read("manifest.json")

    # Cap above the manifest but below the oversized entry: manifest parses, the
    # big entry is rejected.
    monkeypatch.setattr(imp, "MAX_WMERC_ENTRY_BYTES", len(manifest_bytes) + 1024)
    bomb = tmp_path / "bomb.wmerc"
    with zipfile.ZipFile(bomb, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("manifest.json", manifest_bytes)
        zf.writestr("raw_stis/huge.sti", b"\x00" * (len(manifest_bytes) + 4096))

    contents = imp.read_wmerc(bomb)
    assert "raw_stis/huge.sti" not in contents.files
    assert ("raw_stis/huge.sti", "EntryTooLarge") in contents.read_errors


def test_read_wmerc_forged_header_bomb_is_memory_bounded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A member whose declared file_size LIES (tiny) but actually decompresses
    huge must NOT inflate past the cap in RAM. `zf.read()` decompresses the whole
    stream before its CRC check, so a declared-size-only gate is defeated (the
    bomb inflates, THEN raises a caught BadZipFile — the OOM already happened);
    the streaming `_read_member_capped` bounds ACTUAL bytes. Regression for the
    pre-push review's HIGH zip-bomb finding."""
    import struct
    import tracemalloc

    from mercwizard_core.bundle import import_ as imp

    install = tmp_path / "install"
    _populate_install(install)
    base = tmp_path / "base.wmerc"
    export_merc(install, ui_index=220, out_path=base)
    with zipfile.ZipFile(base, "r") as zf:
        manifest_bytes = zf.read("manifest.json")

    real = b"\x00" * (16 * 1024 * 1024)  # 16 MB -> deflates to ~16 KB
    bomb = tmp_path / "bomb.wmerc"
    with zipfile.ZipFile(bomb, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("manifest.json", manifest_bytes)
        zf.writestr("raw_stis/bomb.sti", real)
    del real
    raw = bytearray(bomb.read_bytes())

    # Forge ONLY the bomb entry's declared uncompressed-size (local + central
    # headers) down to 100 bytes, leaving the honest CRC — the exact attack. The
    # uncompressed-size field sits at +22 from a local file header (PK\x03\x04)
    # and +24 from a central-directory header (PK\x01\x02); the arcname follows
    # the 30-/46-byte fixed header, so locate each by its preceding signature.
    needle = b"raw_stis/bomb.sti"
    pos, patched = -1, 0
    while (pos := raw.find(needle, pos + 1)) != -1:
        if raw[pos - 30:pos - 26] == b"PK\x03\x04":      # local file header
            struct.pack_into("<I", raw, pos - 8, 100)
            patched += 1
        elif raw[pos - 46:pos - 42] == b"PK\x01\x02":    # central directory
            struct.pack_into("<I", raw, pos - 22, 100)
            patched += 1
    assert patched == 2, f"expected to forge 2 headers, patched {patched}"
    bomb.write_bytes(raw)

    # Sanity: the forged declared size now sails under any cap.
    with zipfile.ZipFile(bomb, "r") as zf:
        assert zf.getinfo("raw_stis/bomb.sti").file_size == 100

    monkeypatch.setattr(imp, "MAX_WMERC_ENTRY_BYTES", 2 * 1024 * 1024)   # 2 MB
    monkeypatch.setattr(imp, "MAX_WMERC_TOTAL_BYTES", 4 * 1024 * 1024)   # 4 MB

    tracemalloc.start()
    contents = imp.read_wmerc(bomb)
    _cur, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()

    # The bomb entry is rejected, not stored...
    assert "raw_stis/bomb.sti" not in contents.files
    assert any(n == "raw_stis/bomb.sti" for n, _ in contents.read_errors)
    # ...and the 16 MB stream was NEVER fully inflated (cap 2 MB; allow a chunk +
    # overhead). Pre-fix this peaked at ~16 MB+ (the full bomb, via zf.read).
    assert peak < 6 * 1024 * 1024, f"peak {peak / 1024 / 1024:.1f} MB -> stream fully inflated"


def test_extract_table_row_xml_handles_cp1252_source(tmp_path: Path) -> None:
    """A cp1252 source table (accented name, no <?xml?> decl) must still yield the
    row fragment on export. Previously ET.parse raised ParseError and the row was
    silently dropped -- e.g. a merc lost its accented background on .wmerc export.
    The export mirror of the import-side entitization fix (commit 7fb5165)."""
    from mercwizard_core.bundle.export import _extract_table_row_xml

    p = tmp_path / "Backgrounds.xml"
    p.write_bytes(  # \xe9 = 'é' in Windows-1252/latin-1, a lone high byte
        b"<BACKGROUNDS>\r\n"
        b"\t<BACKGROUND>\r\n\t\t<uiIndex>37</uiIndex>\r\n"
        b"\t\t<szName>Caf\xe9 Owner</szName>\r\n\t</BACKGROUND>\r\n"
        b"</BACKGROUNDS>"
    )
    frag = _extract_table_row_xml(p, "uiIndex", 37)
    assert frag is not None, "cp1252 source row was dropped on export"
    assert "Café Owner" in frag      # accented codepoint survived re-encode
    assert "<uiIndex>37</uiIndex>" in frag


def test_read_wmerc_corrupt_manifest_is_valueerror_not_unhandled(tmp_path: Path) -> None:
    """A corrupt/forged manifest.json must surface as ValueError (-> HTTP 400 on
    /bundle/import), not an unhandled BadZipFile / JSONDecodeError that escapes to
    FastAPI as a 500. Regression for the pre-push meta-review."""
    import struct
    from mercwizard_core.bundle import import_ as imp

    # (a) invalid-JSON manifest -> ValueError (the JSONDecodeError branch).
    bad_json = tmp_path / "badjson.wmerc"
    with zipfile.ZipFile(bad_json, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("manifest.json", b"{ not valid json ]")
    with pytest.raises(ValueError):
        imp.read_wmerc(bad_json)

    # (b) forged-header manifest bomb -> ValueError (the BadZipFile branch). The
    # forged-small declared size makes ZipExtFile truncate + CRC-fail, raising
    # BadZipFile out of the manifest read; pre-fix that propagated raw as a 500.
    bomb = tmp_path / "manifestbomb.wmerc"
    with zipfile.ZipFile(bomb, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("manifest.json", b"A" * (8 * 1024 * 1024))
    raw = bytearray(bomb.read_bytes())
    needle = b"manifest.json"
    pos, patched = -1, 0
    while (pos := raw.find(needle, pos + 1)) != -1:
        if raw[pos - 30:pos - 26] == b"PK\x03\x04":      # local file header
            struct.pack_into("<I", raw, pos - 8, 100)
            patched += 1
        elif raw[pos - 46:pos - 42] == b"PK\x01\x02":    # central directory
            struct.pack_into("<I", raw, pos - 22, 100)
            patched += 1
    assert patched == 2, f"expected to forge 2 headers, patched {patched}"
    bomb.write_bytes(raw)
    with pytest.raises(ValueError):
        imp.read_wmerc(bomb)


def test_read_wmerc_member_count_cap(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A bundle with more members than MAX_WMERC_ENTRIES is rejected with a clean
    ValueError before the per-member loop -- the count guard alongside the byte
    caps (a zip with millions of tiny entries would otherwise bloat namelist())."""
    from mercwizard_core.bundle import import_ as imp

    monkeypatch.setattr(imp, "MAX_WMERC_ENTRIES", 3)
    p = tmp_path / "many.wmerc"
    with zipfile.ZipFile(p, "w") as zf:
        zf.writestr("manifest.json", "{}")
        for i in range(5):  # 6 members total > cap 3
            zf.writestr(f"raw_stis/f{i}.sti", b"x")
    with pytest.raises(ValueError):
        imp.read_wmerc(p)
