"""Tests for FaceGear capacity detection + extend-on-demand.

The engine bounds-checks the frame index in a FaceGear STI at
vobject.cpp:958 — failing the check throws an exception that propagates
to exit(0). A merc with ubFaceIndex >= STI frame count CTDs the moment
they equip the corresponding item. These tests cover the detection +
extension primitives that close that gap.
"""
from __future__ import annotations

from pathlib import Path

from PIL import Image
import pytest

from mercwizard_core.facegear import (
    FaceGearInfo,
    crash_risk,
    detect_facegear_capacities,
    extend_facegear_sti,
    inject_overlay,
    read_frame_offset,
    set_overlay_offset,
)
from mercwizard_core.portrait.sti import write_static_sti
from mercwizard_core.install_context import make_install_context


def _make_minimal_facegear_sti(path: Path, num_frames: int = 5) -> None:
    """Synthesize a tiny FaceGear-shaped STI for tests.

    Builds one by writing a 48x43 multi-color frame (skin + a small red and
    blue patch so the palette has enough variety for inject_overlay's
    quantize-against-palette to produce recognisable results), then extends
    to num_frames via the production extend function — same write path the
    wizard would use.
    """
    base = Image.new("RGBA", (48, 43), (180, 140, 110, 255))   # skin tone
    # Sprinkle distinct colors so the palette covers reds, greens, blues
    for x in range(2, 12):
        for y in range(2, 12):
            base.putpixel((x, y), (220, 60, 60, 255))           # red corner
    for x in range(20, 30):
        for y in range(2, 12):
            base.putpixel((x, y), (60, 200, 60, 255))           # green
    for x in range(36, 46):
        for y in range(2, 12):
            base.putpixel((x, y), (60, 60, 220, 255))           # blue corner
    for x in range(2, 46):
        for y in range(30, 40):
            base.putpixel((x, y), (30, 30, 30, 255))            # dark band
    path.parent.mkdir(parents=True, exist_ok=True)
    write_static_sti(path, base)
    if num_frames > 1:
        extend_facegear_sti(path, num_frames)


def test_extend_facegear_appends_transparent_frames(tmp_path: Path) -> None:
    sti_path = tmp_path / "Face_TestGear.sti"
    _make_minimal_facegear_sti(sti_path, num_frames=10)

    result = extend_facegear_sti(sti_path, target_count=25)
    assert result["previous_frame_count"] == 10
    assert result["new_frame_count"] == 25
    assert result["frames_appended"] == 15
    assert result["noop"] is False

    # Reload and confirm the file actually grew on disk
    from ja2py.fileformats.Sti import load_8bit_sti
    with open(sti_path, "rb") as f:
        images = load_8bit_sti(f)
    assert len(images.images) == 25


def test_extend_facegear_idempotent_when_already_long_enough(tmp_path: Path) -> None:
    sti_path = tmp_path / "Face_Idempotent.sti"
    _make_minimal_facegear_sti(sti_path, num_frames=50)

    result = extend_facegear_sti(sti_path, target_count=30)
    assert result["frames_appended"] == 0
    assert result["noop"] is True
    assert result["previous_frame_count"] == 50
    assert result["new_frame_count"] == 50


def test_crash_risk_filters_by_frame_count() -> None:
    infos = [
        FaceGearInfo(path=Path("a"), name="Face_A.sti", relative_path="a", frame_count=50, canvas_size=(48, 43), is_imp_variant=False),
        FaceGearInfo(path=Path("b"), name="Face_B.sti", relative_path="b", frame_count=100, canvas_size=(48, 43), is_imp_variant=False),
        FaceGearInfo(path=Path("c"), name="Face_C.sti", relative_path="c", frame_count=200, canvas_size=(48, 43), is_imp_variant=False),
    ]
    # Face index 75: A would crash (50 frames), B would NOT (100 > 75), C would not
    risky = crash_risk(infos, face_index=75)
    assert [r.name for r in risky] == ["Face_A.sti"]

    # Face index 250: all three crash
    assert {r.name for r in crash_risk(infos, face_index=250)} == {"Face_A.sti", "Face_B.sti", "Face_C.sti"}

    # Face index 5: none crash
    assert crash_risk(infos, face_index=5) == []


def test_crash_risk_includes_equality_case() -> None:
    """frame_count == face_index: the highest valid frame is count-1, so
    indexing at `count` reads out of bounds → CTD. Equality counts as risk.
    """
    infos = [FaceGearInfo(path=Path("x"), name="Face_X.sti", relative_path="x", frame_count=100, canvas_size=(48, 43), is_imp_variant=False)]
    assert crash_risk(infos, face_index=100) == infos


def test_detect_facegear_capacities_finds_synthesized_install(tmp_path: Path) -> None:
    """Stand up a fake install with Data-1.13/faces/FACESGEAR/*.sti and
    confirm detect walks the tree and returns the expected files.
    """
    install_root = tmp_path / "fake_install"
    facegear_dir = install_root / "Data-1.13" / "faces" / "FACESGEAR"
    facegear_dir.mkdir(parents=True)

    _make_minimal_facegear_sti(facegear_dir / "Face_Goggles.sti", num_frames=100)
    _make_minimal_facegear_sti(facegear_dir / "Face_Goggles_IMP.sti", num_frames=100)
    _make_minimal_facegear_sti(facegear_dir / "Face_Hat.sti", num_frames=150)

    # Minimal Ja2.ini so make_install_context doesn't choke on missing config
    (install_root / "Ja2.ini").write_text("[Ja2 Settings]\nCD=c\n")

    ctx = make_install_context(install_root)
    infos = detect_facegear_capacities(ctx)
    names = {info.name for info in infos}
    assert "Face_Goggles.sti" in names
    assert "Face_Goggles_IMP.sti" in names
    assert "Face_Hat.sti" in names

    by_name = {info.name: info for info in infos}
    assert by_name["Face_Goggles.sti"].frame_count == 100
    assert by_name["Face_Goggles_IMP.sti"].is_imp_variant is True
    assert by_name["Face_Hat.sti"].is_imp_variant is False
    assert by_name["Face_Hat.sti"].frame_count == 150


def test_detect_returns_empty_when_no_facegear_dir(tmp_path: Path) -> None:
    install_root = tmp_path / "no_facegear"
    install_root.mkdir()
    (install_root / "Ja2.ini").write_text("[Ja2 Settings]\nCD=c\n")
    ctx = make_install_context(install_root)
    assert detect_facegear_capacities(ctx) == []


def test_find_orphan_variants_detects_missing_imp(tmp_path: Path) -> None:
    """A Face_X.sti without its Face_X_IMP.sti partner is a boot-crash trap.

    InitializeFaceGearGraphics calls AddVideoObject on both at boot; missing
    one returns NULL and vobject.cpp:1092 dereferences for a hard CTD.
    """
    install_root = tmp_path / "orphan_install"
    facegear_dir = install_root / "Data-1.13" / "faces" / "FACESGEAR"
    facegear_dir.mkdir(parents=True)
    _make_minimal_facegear_sti(facegear_dir / "Face_Paired.sti", num_frames=50)
    _make_minimal_facegear_sti(facegear_dir / "Face_Paired_IMP.sti", num_frames=50)
    _make_minimal_facegear_sti(facegear_dir / "Face_LoneBase.sti", num_frames=50)
    _make_minimal_facegear_sti(facegear_dir / "Face_LoneImp_IMP.sti", num_frames=50)
    (install_root / "Ja2.ini").write_text("[Ja2 Settings]\nCD=c\n")

    from mercwizard_core.facegear import find_orphan_variants
    from mercwizard_core.install_context import make_install_context

    ctx = make_install_context(install_root)
    infos = detect_facegear_capacities(ctx)
    orphans = find_orphan_variants(infos)
    by_stem = {o["stem"]: o for o in orphans}
    assert "Face_LoneBase" in by_stem
    assert by_stem["Face_LoneBase"]["missing"] == "imp"
    assert "Face_LoneImp" in by_stem
    assert by_stem["Face_LoneImp"]["missing"] == "base"
    assert "Face_Paired" not in by_stem


def test_find_orphan_variants_empty_for_complete_install(tmp_path: Path) -> None:
    install_root = tmp_path / "complete_install"
    facegear_dir = install_root / "Data-1.13" / "faces" / "FACESGEAR"
    facegear_dir.mkdir(parents=True)
    _make_minimal_facegear_sti(facegear_dir / "Face_X.sti", num_frames=50)
    _make_minimal_facegear_sti(facegear_dir / "Face_X_IMP.sti", num_frames=50)
    (install_root / "Ja2.ini").write_text("[Ja2 Settings]\nCD=c\n")

    from mercwizard_core.facegear import find_orphan_variants
    from mercwizard_core.install_context import make_install_context

    ctx = make_install_context(install_root)
    infos = detect_facegear_capacities(ctx)
    assert find_orphan_variants(infos) == []


# ── FaceGear.xml registry cross-reference (bug #79) ────────────────────


def _write_facegear_xml(path: Path, entries: list[tuple[int, str]]) -> None:
    """Build a minimal Data-1.13/TableData/FaceGear.xml for the orphan
    registry tests. Each entry is (uiIndex, szFile_basename). Use an
    empty string for szFile to write an unregistered slot stub."""
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = ['<?xml version="1.0" encoding="UTF-8"?>', "<FACEGEAR_LIST>"]
    for ui_index, sz in entries:
        lines.append("\t<ITEM>")
        lines.append(f"\t\t<uiIndex>{ui_index}</uiIndex>")
        lines.append("\t\t<Type>0</Type>")
        if sz:
            lines.append(f"\t\t<szFile>FACES\\FACESGEAR\\{sz}</szFile>")
        else:
            lines.append("\t\t<szFile />")
        lines.append("\t</ITEM>")
    lines.append("</FACEGEAR_LIST>")
    path.write_text("\n".join(lines), encoding="utf-8")


def test_read_registered_facegear_stis_extracts_basenames(tmp_path: Path) -> None:
    """FaceGear.xml entries with non-empty szFile become lowercased stems."""
    install_root = tmp_path / "fg_registry_install"
    install_root.mkdir()
    (install_root / "Ja2.ini").write_text("[Ja2 Settings]\nCD=c\n")
    _write_facegear_xml(
        install_root / "Data-1.13" / "TableData" / "FaceGear.xml",
        [
            (0, "Face_Hat"),
            (1, "Face_Goggles.sti"),    # with extension
            (2, ""),                     # unregistered slot stub
            (3, "Face_Beard_IMP"),       # _IMP variant should strip to Face_Beard
        ],
    )
    from mercwizard_core.facegear import read_registered_facegear_stis
    from mercwizard_core.install_context import make_install_context

    ctx = make_install_context(install_root)
    stems = read_registered_facegear_stis(ctx)
    assert stems == {"face_hat", "face_goggles", "face_beard"}


def test_read_registered_facegear_stis_empty_when_xml_missing(tmp_path: Path) -> None:
    """Missing FaceGear.xml returns empty set — caller's fallback is
    to behave as the old filesystem-only orphan scan."""
    install_root = tmp_path / "no_facegear_xml"
    install_root.mkdir()
    (install_root / "Ja2.ini").write_text("[Ja2 Settings]\nCD=c\n")
    from mercwizard_core.facegear import read_registered_facegear_stis
    from mercwizard_core.install_context import make_install_context

    ctx = make_install_context(install_root)
    assert read_registered_facegear_stis(ctx) == set()


def test_find_orphan_variants_filters_unregistered_stems(tmp_path: Path) -> None:
    """The KGoggles case: an unregistered orphan STI on disk should NOT
    be flagged when the FaceGear.xml registry is consulted, because the
    engine never loads it — no boot CTD possible."""
    install_root = tmp_path / "filtered_orphans_install"
    facegear_dir = install_root / "Data-1.13" / "faces" / "FACESGEAR"
    facegear_dir.mkdir(parents=True)
    # Registered orphan — engine WILL try to load this; missing _IMP CTD-risks
    _make_minimal_facegear_sti(facegear_dir / "Face_Registered.sti", num_frames=50)
    # Unregistered orphan — leftover/WIP, engine ignores it
    _make_minimal_facegear_sti(facegear_dir / "Face_KGoggles.sti", num_frames=50)
    (install_root / "Ja2.ini").write_text("[Ja2 Settings]\nCD=c\n")
    _write_facegear_xml(
        install_root / "Data-1.13" / "TableData" / "FaceGear.xml",
        [
            (0, "Face_Registered"),
            (1, ""),
        ],
    )

    from mercwizard_core.facegear import (
        find_orphan_variants, read_registered_facegear_stis,
    )
    from mercwizard_core.install_context import make_install_context

    ctx = make_install_context(install_root)
    infos = detect_facegear_capacities(ctx)
    registered = read_registered_facegear_stis(ctx)

    # Without filter: both orphans flagged (filesystem-level scan).
    unfiltered = find_orphan_variants(infos)
    unfiltered_stems = {o["stem"] for o in unfiltered}
    assert "Face_Registered" in unfiltered_stems
    assert "Face_KGoggles" in unfiltered_stems

    # With filter: only registered orphan flagged. KGoggles is invisible
    # to the engine and never causes a CTD.
    filtered = find_orphan_variants(infos, registered_stems=registered)
    filtered_stems = {o["stem"] for o in filtered}
    assert "Face_Registered" in filtered_stems
    assert "Face_KGoggles" not in filtered_stems


# ── Orphan one-click repair (bug #89) ──────────────────────────────────


def test_repair_orphan_pair_copies_bytes_to_missing_partner(tmp_path: Path) -> None:
    """Repairing an orphan = copying the present STI verbatim to the
    missing partner's path. Output must equal input byte-for-byte;
    FaceGear STIs are universal containers so the engine doesn't care
    that base/IMP are identical (vanilla 1.13 ships several pairs that
    way too)."""
    install_root = tmp_path / "repair_install"
    facegear_dir = install_root / "Data-1.13" / "faces" / "FACESGEAR"
    facegear_dir.mkdir(parents=True)
    _make_minimal_facegear_sti(facegear_dir / "Face_Lone.sti", num_frames=50)
    (install_root / "Ja2.ini").write_text("[Ja2 Settings]\nCD=c\n")

    from mercwizard_core.facegear import (
        find_orphan_variants, repair_orphan_pair, resolve_orphan_repair_paths,
    )
    from mercwizard_core.install_context import make_install_context

    ctx = make_install_context(install_root)
    infos = detect_facegear_capacities(ctx)
    orphans = find_orphan_variants(infos)
    assert len(orphans) == 1
    assert orphans[0]["stem"] == "Face_Lone"
    assert orphans[0]["missing"] == "imp"

    src, dst = resolve_orphan_repair_paths(infos, orphans[0])
    assert src.name == "Face_Lone.sti"
    assert dst.name == "Face_Lone_IMP.sti"
    assert not dst.exists()

    n_bytes = repair_orphan_pair(src, dst)
    assert dst.exists()
    assert dst.read_bytes() == src.read_bytes()
    assert n_bytes == src.stat().st_size

    # And the re-scan now reports the install as clean — orphan resolved.
    fresh_orphans = find_orphan_variants(detect_facegear_capacities(ctx))
    assert fresh_orphans == []


def test_repair_orphan_pair_refuses_to_overwrite_existing(tmp_path: Path) -> None:
    """If the target appeared between the user's scan and their Repair
    click (concurrent edit, manual fix), the repair must NOT clobber
    it. Caller catches FileExistsError and surfaces a 'target appeared'
    skip reason rather than silently overwriting."""
    src = tmp_path / "Face_A.sti"
    dst = tmp_path / "Face_A_IMP.sti"
    src.write_bytes(b"source-bytes")
    dst.write_bytes(b"existing-target")
    from mercwizard_core.facegear import repair_orphan_pair

    with pytest.raises(FileExistsError):
        repair_orphan_pair(src, dst)
    # The pre-existing target is untouched
    assert dst.read_bytes() == b"existing-target"


# ── Nudge offset (bug-review #102) ─────────────────────────────────────


def test_nudge_overlay_offset_shifts_signed_int16(tmp_path: Path) -> None:
    """Header-only edit: pure offset shift, palette + pixels unchanged.

    Verifies (a) the signed INT16 semantics are preserved through the
    UINT16 storage round-trip, (b) the previous + new offsets match
    expectation, (c) other frames in the STI are not touched.
    """
    from mercwizard_core.facegear import (
        nudge_overlay_offset, read_frame_offset, inject_overlay,
    )
    from PIL import Image as PImage
    import io as _io

    sti_path = tmp_path / "Face_Nudge.sti"
    _make_minimal_facegear_sti(sti_path, num_frames=30)

    # Plant a known offset at frame 5 via inject_overlay with explicit offset_xy
    overlay = PImage.new("RGBA", (48, 43), (200, 60, 60, 255))
    buf = _io.BytesIO()
    overlay.save(buf, format="PNG")
    inject_overlay(sti_path, face_index=5, overlay_png_bytes=buf.getvalue(), offset_xy=(10, -5))
    assert read_frame_offset(sti_path, 5) == (10, -5)

    # Nudge right + down by 1
    result = nudge_overlay_offset(sti_path, face_index=5, dx=1, dy=1)
    assert result["previous_offset_xy"] == (10, -5)
    assert result["new_offset_xy"] == (11, -4)
    assert read_frame_offset(sti_path, 5) == (11, -4)

    # Frame 0's offset must be unchanged
    assert read_frame_offset(sti_path, 0) == (0, 0)


def test_nudge_negative_delta_decreases_offset(tmp_path: Path) -> None:
    """Negative dx/dy works (left/up nudges) and stays signed."""
    from mercwizard_core.facegear import nudge_overlay_offset, read_frame_offset, inject_overlay
    from PIL import Image as PImage
    import io as _io

    sti_path = tmp_path / "Face_Neg.sti"
    _make_minimal_facegear_sti(sti_path, num_frames=10)
    overlay = PImage.new("RGBA", (48, 43), (60, 60, 200, 255))
    buf = _io.BytesIO()
    overlay.save(buf, format="PNG")
    inject_overlay(sti_path, face_index=3, overlay_png_bytes=buf.getvalue(), offset_xy=(0, 0))

    nudge_overlay_offset(sti_path, face_index=3, dx=-2, dy=-3)
    assert read_frame_offset(sti_path, 3) == (-2, -3)


def test_nudge_out_of_range_frame_raises(tmp_path: Path) -> None:
    """Can't nudge a frame that doesn't exist — surface a clear error."""
    from mercwizard_core.facegear import nudge_overlay_offset

    sti_path = tmp_path / "Face_Short.sti"
    _make_minimal_facegear_sti(sti_path, num_frames=10)
    with pytest.raises(ValueError, match="frame count"):
        nudge_overlay_offset(sti_path, face_index=50, dx=1, dy=1)


def test_nudge_int16_overflow_raises(tmp_path: Path) -> None:
    """Refuse a nudge that would overflow INT16 — large deltas usually
    indicate a logic bug, not the user's intent."""
    from mercwizard_core.facegear import nudge_overlay_offset, inject_overlay
    from PIL import Image as PImage
    import io as _io

    sti_path = tmp_path / "Face_Overflow.sti"
    _make_minimal_facegear_sti(sti_path, num_frames=10)
    overlay = PImage.new("RGBA", (48, 43), (60, 200, 60, 255))
    buf = _io.BytesIO()
    overlay.save(buf, format="PNG")
    # Plant near the INT16 boundary
    inject_overlay(sti_path, face_index=4, overlay_png_bytes=buf.getvalue(), offset_xy=(32760, 0))
    with pytest.raises(ValueError, match="overflows INT16"):
        nudge_overlay_offset(sti_path, face_index=4, dx=10, dy=0)


def test_repair_orphan_handles_imp_present_base_missing(tmp_path: Path) -> None:
    """The other orientation: when the IMP variant is present and the
    base is missing, repair copies IMP → base."""
    install_root = tmp_path / "imp_only_install"
    facegear_dir = install_root / "Data-1.13" / "faces" / "FACESGEAR"
    facegear_dir.mkdir(parents=True)
    _make_minimal_facegear_sti(facegear_dir / "Face_ImpOnly_IMP.sti", num_frames=50)
    (install_root / "Ja2.ini").write_text("[Ja2 Settings]\nCD=c\n")

    from mercwizard_core.facegear import (
        find_orphan_variants, repair_orphan_pair, resolve_orphan_repair_paths,
    )
    from mercwizard_core.install_context import make_install_context

    ctx = make_install_context(install_root)
    infos = detect_facegear_capacities(ctx)
    orphans = find_orphan_variants(infos)
    assert orphans[0]["missing"] == "base"

    src, dst = resolve_orphan_repair_paths(infos, orphans[0])
    assert src.name == "Face_ImpOnly_IMP.sti"
    assert dst.name == "Face_ImpOnly.sti"
    repair_orphan_pair(src, dst)
    assert dst.exists()


def test_inject_overlay_round_trips_at_target_face_index(tmp_path: Path) -> None:
    """Inject a custom overlay into a Face_*.sti at frame N, read it back,
    confirm the bytes survived. Other frames must be untouched (we only
    replace one sub-image, not the whole file)."""
    from PIL import Image as PImage
    from mercwizard_core.facegear import inject_overlay, extract_overlay

    sti_path = tmp_path / "Face_TestGear.sti"
    _make_minimal_facegear_sti(sti_path, num_frames=50)

    # Build a recognizable overlay: a red square at center
    overlay = PImage.new("RGBA", (48, 43), (0, 0, 0, 0))
    for x in range(15, 35):
        for y in range(10, 30):
            overlay.putpixel((x, y), (200, 60, 60, 255))
    buf = __import__("io").BytesIO()
    overlay.save(buf, format="PNG")

    result = inject_overlay(sti_path, face_index=25, overlay_png_bytes=buf.getvalue())
    assert result["face_index"] == 25
    assert result["new_frame_count"] >= 50
    assert result["extended"] is False  # 25 < 50

    extracted = extract_overlay(sti_path, face_index=25)
    assert extracted is not None
    extracted_img = PImage.open(__import__("io").BytesIO(extracted)).convert("RGBA")
    assert extracted_img.size == (48, 43)
    # Center pixel should be non-transparent and roughly red
    cx, cy = 24, 20
    px = extracted_img.getpixel((cx, cy))
    assert px[3] > 0, "center pixel went transparent"
    assert px[0] > px[1] and px[0] > px[2], f"center pixel not red-ish: {px}"


def test_inject_overlay_extends_when_face_index_beyond_frame_count(tmp_path: Path) -> None:
    """If the target face_index is beyond the STI's current frame count,
    inject_overlay extends with transparent placeholders first, then writes
    the overlay at the target index."""
    from PIL import Image as PImage
    from mercwizard_core.facegear import inject_overlay
    from ja2py.fileformats.Sti import load_8bit_sti

    sti_path = tmp_path / "Face_ShortGear.sti"
    _make_minimal_facegear_sti(sti_path, num_frames=10)

    overlay = PImage.new("RGBA", (48, 43), (50, 50, 200, 255))
    buf = __import__("io").BytesIO()
    overlay.save(buf, format="PNG")

    result = inject_overlay(sti_path, face_index=100, overlay_png_bytes=buf.getvalue())
    assert result["extended"] is True
    assert result["new_frame_count"] == 101

    with open(sti_path, "rb") as f:
        images = load_8bit_sti(f)
    assert len(images.images) == 101


def test_inject_overlay_rejects_too_small_input(tmp_path: Path) -> None:
    """Overlays smaller than 48×43 are rejected — upscaling would blur."""
    from PIL import Image as PImage
    from mercwizard_core.facegear import inject_overlay

    sti_path = tmp_path / "Face_RejectSize.sti"
    _make_minimal_facegear_sti(sti_path, num_frames=50)

    tiny = PImage.new("RGBA", (10, 10), (255, 255, 255, 255))
    buf = __import__("io").BytesIO()
    tiny.save(buf, format="PNG")

    with pytest.raises(ValueError, match="at least 48"):
        inject_overlay(sti_path, face_index=20, overlay_png_bytes=buf.getvalue())


def test_inject_overlay_preserves_other_frames(tmp_path: Path) -> None:
    """Injecting at frame N must leave frame M (M != N) byte-identical to before."""
    from PIL import Image as PImage
    from mercwizard_core.facegear import inject_overlay
    from ja2py.fileformats.Sti import load_8bit_sti

    sti_path = tmp_path / "Face_Preservation.sti"
    _make_minimal_facegear_sti(sti_path, num_frames=20)

    # Snapshot frame 5's pixels before
    with open(sti_path, "rb") as f:
        before = load_8bit_sti(f)
    frame_5_before = list(before.images[5].image.getdata())

    overlay = PImage.new("RGBA", (48, 43), (100, 200, 100, 255))
    buf = __import__("io").BytesIO()
    overlay.save(buf, format="PNG")
    inject_overlay(sti_path, face_index=15, overlay_png_bytes=buf.getvalue())

    with open(sti_path, "rb") as f:
        after = load_8bit_sti(f)
    frame_5_after = list(after.images[5].image.getdata())
    assert frame_5_before == frame_5_after, "non-target frame got modified"


def test_extract_overlay_returns_none_for_out_of_range(tmp_path: Path) -> None:
    from mercwizard_core.facegear import extract_overlay
    sti_path = tmp_path / "Face_OutOfRange.sti"
    _make_minimal_facegear_sti(sti_path, num_frames=10)
    assert extract_overlay(sti_path, face_index=50) is None


def test_inject_overlay_honors_explicit_offset_xy(tmp_path: Path) -> None:
    """When the caller passes offset_xy, the written frame's sub-image header
    carries those exact values (engine adds them to the bottom-anchored
    blit position; vobject_blitters.cpp:319-320)."""
    from PIL import Image as PImage
    from mercwizard_core.facegear import inject_overlay
    from ja2py.fileformats.Sti import load_8bit_sti

    sti_path = tmp_path / "Face_OffsetTest.sti"
    _make_minimal_facegear_sti(sti_path, num_frames=50)

    overlay = PImage.new("RGBA", (48, 43), (180, 80, 80, 255))
    buf = __import__("io").BytesIO()
    overlay.save(buf, format="PNG")

    from mercwizard_core.facegear import read_frame_offset
    result = inject_overlay(
        sti_path,
        face_index=25,
        overlay_png_bytes=buf.getvalue(),
        offset_xy=(-2, 7),
    )
    assert result["offset_xy"] == (-2, 7)
    # read_frame_offset converts ja2py's UINT16 storage back to signed INT16
    # (matches what the engine reads via vobject_blitters.cpp:319-320)
    assert read_frame_offset(sti_path, 25) == (-2, 7)


def test_inject_overlay_falls_back_to_png_metadata_offset(tmp_path: Path) -> None:
    """A PNG that carries mw2_offset_x/y tEXt metadata (set by extract_overlay)
    drives the written frame's offset when no explicit offset_xy is passed.
    This is how bundle import preserves source-merc offsets across round-trip."""
    from PIL import Image as PImage
    from PIL.PngImagePlugin import PngInfo
    from mercwizard_core.facegear import inject_overlay
    from ja2py.fileformats.Sti import load_8bit_sti

    sti_path = tmp_path / "Face_MetaOffset.sti"
    _make_minimal_facegear_sti(sti_path, num_frames=50)

    overlay = PImage.new("RGBA", (48, 43), (100, 150, 100, 255))
    meta = PngInfo()
    meta.add_text("mw2_offset_x", "-5")
    meta.add_text("mw2_offset_y", "12")
    buf = __import__("io").BytesIO()
    overlay.save(buf, format="PNG", pnginfo=meta)

    from mercwizard_core.facegear import read_frame_offset
    result = inject_overlay(sti_path, face_index=15, overlay_png_bytes=buf.getvalue())
    assert result["offset_xy"] == (-5, 12)
    assert read_frame_offset(sti_path, 15) == (-5, 12)


def test_extract_overlay_embeds_offset_in_png_metadata(tmp_path: Path) -> None:
    """extract_overlay returns a PNG that carries the source frame's offsets
    as mw2_offset_x/y tEXt metadata. Re-decoding the PNG should surface them."""
    from PIL import Image as PImage
    from mercwizard_core.facegear import extract_overlay, inject_overlay

    sti_path = tmp_path / "Face_RoundtripOffset.sti"
    _make_minimal_facegear_sti(sti_path, num_frames=50)

    # Set a recognizable offset on frame 20 first
    overlay = PImage.new("RGBA", (48, 43), (200, 60, 60, 255))
    buf = __import__("io").BytesIO()
    overlay.save(buf, format="PNG")
    inject_overlay(sti_path, 20, buf.getvalue(), offset_xy=(3, -8))

    extracted = extract_overlay(sti_path, 20)
    assert extracted is not None
    decoded = PImage.open(__import__("io").BytesIO(extracted))
    assert decoded.info.get("mw2_offset_x") == "3"
    assert decoded.info.get("mw2_offset_y") == "-8"


def test_auto_position_overlay_computes_delta_from_eye_coords(tmp_path: Path) -> None:
    """auto_position_overlay copies source pixels into target frame and writes
    an offset equal to (source_offset + (target_eye - source_eye))."""
    from PIL import Image as PImage
    from mercwizard_core.facegear import auto_position_overlay, inject_overlay
    from ja2py.fileformats.Sti import load_8bit_sti

    sti_path = tmp_path / "Face_AutoPos.sti"
    _make_minimal_facegear_sti(sti_path, num_frames=50)

    # Seed frame[0] as the "source" with a recognizable solid color + offset (1, 1)
    source_overlay = PImage.new("RGBA", (48, 43), (50, 200, 100, 255))
    buf = __import__("io").BytesIO()
    source_overlay.save(buf, format="PNG")
    inject_overlay(sti_path, 0, buf.getvalue(), offset_xy=(1, 1))

    # Auto-position to face_index=30 with target eyes at (8, 13)
    # and source eyes at (10, 10) → delta (-2, +3)
    # expected offset = source_offset (1,1) + delta (-2,+3) = (-1, +4)
    result = auto_position_overlay(
        sti_path,
        target_face_index=30,
        target_eye_xy=(8, 13),
        source_eye_xy=(10, 10),
        source_face_index=0,
    )
    from mercwizard_core.facegear import read_frame_offset
    assert result["source_face_index"] == 0
    assert result["source_offset_xy"] == (1, 1)
    assert result["delta_xy"] == (-2, 3)
    assert result["applied_offset_xy"] == (-1, 4)
    assert read_frame_offset(sti_path, 30) == (-1, 4)


def test_auto_position_overlay_auto_picks_first_non_empty_source(tmp_path: Path) -> None:
    """When source_face_index is None, the function picks the first frame
    with non-zero pixel content (skipping placeholder/transparent frames)."""
    from PIL import Image as PImage
    from mercwizard_core.facegear import auto_position_overlay, inject_overlay

    sti_path = tmp_path / "Face_AutoSrc.sti"
    _make_minimal_facegear_sti(sti_path, num_frames=50)
    # Frame 0 is non-empty by virtue of _make_minimal_facegear_sti's seed.
    # Confirm the auto-detect lands there.
    result = auto_position_overlay(
        sti_path,
        target_face_index=40,
        target_eye_xy=(10, 10),
        source_eye_xy=(10, 10),
    )
    assert result["source_face_index"] == 0


def test_extend_then_detect_reports_new_count(tmp_path: Path) -> None:
    """End-to-end: synthesize a 50-frame STI, extend to 120, detect reports 120."""
    install_root = tmp_path / "ext_install"
    facegear_dir = install_root / "Data-1.13" / "faces" / "FACESGEAR"
    facegear_dir.mkdir(parents=True)
    sti_path = facegear_dir / "Face_Custom.sti"
    _make_minimal_facegear_sti(sti_path, num_frames=50)
    (install_root / "Ja2.ini").write_text("[Ja2 Settings]\nCD=c\n")

    ctx = make_install_context(install_root)
    before = detect_facegear_capacities(ctx)
    assert before[0].frame_count == 50

    extend_facegear_sti(sti_path, target_count=120)
    after = detect_facegear_capacities(ctx)
    assert after[0].frame_count == 120


# ──────────────────────────────────────────────────────────────────────────
#  set_overlay_offset primitive — absolute coord editing (companion to nudge)
# ──────────────────────────────────────────────────────────────────────────


def test_set_overlay_offset_writes_absolute_value(tmp_path: Path) -> None:
    """Inject a frame with one offset, set_overlay_offset to a different
    absolute value, assert read_frame_offset returns the exact new value
    (signed, INT16-roundtripping)."""
    from io import BytesIO

    sti_path = tmp_path / "Face_TestGear.sti"
    _make_minimal_facegear_sti(sti_path, num_frames=10)

    overlay = Image.new("RGBA", (48, 43), (220, 60, 60, 255))
    buf = BytesIO()
    overlay.save(buf, format="PNG")
    inject_overlay(sti_path, face_index=3, overlay_png_bytes=buf.getvalue(), offset_xy=(5, 5))
    assert read_frame_offset(sti_path, 3) == (5, 5)

    result = set_overlay_offset(sti_path, face_index=3, offset_x=-7, offset_y=12)
    assert result["face_index"] == 3
    assert result["previous_offset_xy"] == (5, 5)
    assert result["new_offset_xy"] == (-7, 12)

    # On-disk truth, not just the return dict
    assert read_frame_offset(sti_path, 3) == (-7, 12)


def test_set_overlay_offset_does_not_modify_pixels(tmp_path: Path) -> None:
    """Header-only edit: the frame's pixel data must be byte-identical
    before and after a set_overlay_offset call."""
    from io import BytesIO
    from ja2py.fileformats.Sti import load_8bit_sti

    sti_path = tmp_path / "Face_TestGear.sti"
    _make_minimal_facegear_sti(sti_path, num_frames=10)

    overlay = Image.new("RGBA", (48, 43), (60, 200, 60, 255))
    buf = BytesIO()
    overlay.save(buf, format="PNG")
    inject_overlay(sti_path, face_index=4, overlay_png_bytes=buf.getvalue(), offset_xy=(0, 0))

    with open(sti_path, "rb") as f:
        before = load_8bit_sti(f)
    pixels_before = list(before.images[4].image.getdata())

    set_overlay_offset(sti_path, face_index=4, offset_x=15, offset_y=-8)

    with open(sti_path, "rb") as f:
        after = load_8bit_sti(f)
    pixels_after = list(after.images[4].image.getdata())

    assert pixels_before == pixels_after, "set_overlay_offset must not touch pixels"


def test_set_overlay_offset_rejects_int16_overflow(tmp_path: Path) -> None:
    """The engine reads sOffsetX/Y as INT16. Values outside ±32768 raise
    ValueError (matches the nudge_overlay_offset guard)."""
    from io import BytesIO

    sti_path = tmp_path / "Face_TestGear.sti"
    _make_minimal_facegear_sti(sti_path, num_frames=5)

    overlay = Image.new("RGBA", (48, 43), (180, 140, 110, 255))
    buf = BytesIO()
    overlay.save(buf, format="PNG")
    inject_overlay(sti_path, face_index=2, overlay_png_bytes=buf.getvalue(), offset_xy=(0, 0))

    with pytest.raises(ValueError, match="overflows INT16"):
        set_overlay_offset(sti_path, face_index=2, offset_x=40000, offset_y=0)
    with pytest.raises(ValueError, match="overflows INT16"):
        set_overlay_offset(sti_path, face_index=2, offset_x=0, offset_y=-40000)


def test_set_overlay_offset_rejects_out_of_range_frame(tmp_path: Path) -> None:
    """Calling on a frame index beyond the STI's frame count raises
    ValueError — matches nudge_overlay_offset's pre-existing-frame
    requirement. Avoids accidentally creating placeholder frames as a
    side-effect of a coord edit."""
    sti_path = tmp_path / "Face_TestGear.sti"
    _make_minimal_facegear_sti(sti_path, num_frames=5)

    with pytest.raises(ValueError, match=">= frame count"):
        set_overlay_offset(sti_path, face_index=10, offset_x=0, offset_y=0)
