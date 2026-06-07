"""Tests for the portrait pipeline:
- quantize: rawmode, anchor, transparency
- animate_skip: dummy-frames guarantee (8 sub-frames at canonical sizes)
- sti: write + verify round-trip
"""
from __future__ import annotations

from pathlib import Path

from PIL import Image

from mercwizard_core.portrait.animate_skip import (
    DEFAULT_EYE_BOX,
    DEFAULT_MOUTH_BOX,
    EYE_SUBFRAME_SIZE,
    MOUTH_SUBFRAME_SIZE,
    BoundingBox,
    make_skip_frames,
)
from mercwizard_core.portrait.quantize import quantize_with_anchor
from mercwizard_core.portrait.sti import (
    SMALLFACE_BASE_SIZE,
    verify_smallface_sti,
    write_smallface_sti,
    write_static_sti,
)


# ──────────────────────────────────────────────────────────────────────────
#  quantize_with_anchor — palette + rawmode + anchor pixel
# ──────────────────────────────────────────────────────────────────────────

def test_quantize_sets_rawmode_to_RGB(synthetic_smallface: Image.Image) -> None:
    """ja2py corrupts palettes whose rawmode is None — we force 'RGB'."""
    q = quantize_with_anchor(synthetic_smallface)
    assert q.palette is not None
    assert q.palette.rawmode == "RGB"


def test_quantize_returns_p_mode(synthetic_smallface: Image.Image) -> None:
    q = quantize_with_anchor(synthetic_smallface)
    assert q.mode == "P"


def test_quantize_index_0_is_pure_black(synthetic_smallface: Image.Image) -> None:
    """The transparent index must be (0,0,0)."""
    q = quantize_with_anchor(synthetic_smallface)
    pal = q.getpalette()
    assert pal[0:3] == [0, 0, 0]


def test_quantize_anchor_pixel_is_index_0(synthetic_smallface: Image.Image) -> None:
    """The bottom-left pixel (0, h-1) is the anchor — uses index 0."""
    q = quantize_with_anchor(synthetic_smallface)
    h = q.size[1]
    assert q.getpixel((0, h - 1)) == 0


def test_quantize_transparent_pixels_map_to_index_0() -> None:
    """Input transparent pixels should map to palette index 0."""
    img = Image.new("RGBA", (48, 43), (0, 0, 0, 0))  # fully transparent
    # Add some opaque content in the middle
    for x in range(20, 30):
        for y in range(20, 30):
            img.putpixel((x, y), (200, 100, 50, 255))
    q = quantize_with_anchor(img)
    # Corner pixels (transparent in input) should be index 0
    assert q.getpixel((0, 0)) == 0
    assert q.getpixel((47, 0)) == 0
    # Opaque pixel should NOT be index 0
    assert q.getpixel((25, 25)) != 0


def test_quantize_opaque_dark_hair_pixels_never_collide_with_transparent_index() -> None:
    """Regression: dark hair pixels (RGB close to (0,0,0) but opaque) must NOT
    land at palette index 0.

    The pre-fix anchor-based swap could fail when MAXCOVERAGE clustered the
    anchor pixel with dark hair: swapping the cluster's index to 0 dragged
    the hair with it, leaving Eskimo with see-through hair (the user saw the
    yellow M.E.R.C. canvas through his hair, 2026-05-14). The shift-by-1
    fix reserves index 0 by construction — no opaque pixel can ever land
    there regardless of color.
    """
    # 48x43 canvas, mostly transparent. Block of near-black "hair" pixels
    # that the quantizer can't avoid clustering near (0,0,0).
    img = Image.new("RGBA", (48, 43), (0, 0, 0, 0))
    # Big block of opaque almost-black hair across the top
    for x in range(8, 40):
        for y in range(5, 18):
            img.putpixel((x, y), (3, 3, 3, 255))   # very dark hair
    # Skin tones below
    for x in range(8, 40):
        for y in range(18, 35):
            img.putpixel((x, y), (200, 150, 120, 255))

    q = quantize_with_anchor(img)

    # Every opaque dark hair pixel must be at index >= 1 (i.e. NOT transparent)
    for x in range(8, 40):
        for y in range(5, 18):
            idx = q.getpixel((x, y))
            assert idx != 0, (
                f"Hair pixel at ({x},{y}) quantized to index 0 - "
                f"engine will render it transparent (Eskimo regression)"
            )

    # And palette[0] is still (0,0,0)
    pal = q.getpalette()
    assert pal[0:3] == [0, 0, 0]


def test_quantize_against_palette_remaps_opaque_dark_pixels_away_from_index_0() -> None:
    """Sibling-frame quantize must respect the index-0 reservation too.

    `quantize_with_anchor` reserves index 0 by the shift+1 trick. The
    sibling path `quantize_against_palette` uses PIL's nearest-neighbor
    matching which CAN map an opaque-near-black pixel to index 0 because
    palette[0] = (0,0,0). Without the remap fix the engine renders that
    pixel transparent — re-introducing the Eskimo "transparent hair" bug
    on shared-palette animation frames.

    This test builds a reference palette with palette[0]=(0,0,0) plus
    several distinct colors, then quantizes an RGBA image of opaque
    near-black pixels against it. Every resulting pixel must be at
    index >= 1.
    """
    from mercwizard_core.portrait.quantize import quantize_against_palette

    # Reference: a P-mode image whose palette has (0,0,0) at slot 0 +
    # several other colors at 1..N. Mimics the union-palette quantize
    # output from quantize_with_anchor.
    ref = Image.new("P", (1, 1))
    pal = [0, 0, 0]                # slot 0: transparent reservation
    pal += [10, 10, 10]            # slot 1: very dark grey (will match)
    pal += [200, 150, 120]         # slot 2: skin tone
    pal += [50, 30, 20]            # slot 3: dark hair
    pal += [0, 0, 0] * (256 - 4)   # pad
    ref.putpalette(bytes(pal), rawmode="RGB")

    # Image of pure opaque near-black pixels — without the remap fix
    # PIL's nearest-neighbor would map every pixel to slot 0 (palette[0]
    # is exactly (0,0,0), the closest match to (3,3,3)).
    img = Image.new("RGBA", (8, 8), (3, 3, 3, 255))

    quantized = quantize_against_palette(img, ref)
    pixel_indices = set(quantized.getdata())
    assert 0 not in pixel_indices, (
        f"opaque-near-black pixels mapped to transparent index 0; "
        f"got indices {pixel_indices}"
    )


def test_quantize_palette_uses_indices_1_through_255_for_opaque() -> None:
    """All opaque content lives in palette indices 1..255; index 0 is the
    transparent reservation only.
    """
    img = Image.new("RGBA", (48, 43), (128, 64, 32, 255))
    q = quantize_with_anchor(img)
    indices = set(q.getdata())
    assert 0 not in indices, "opaque-only image has a pixel at the transparent index"
    assert max(indices) <= 255


# ----------------------------------------------------------------------
#  animate_skip - the dummy-frames guarantee
# ----------------------------------------------------------------------

def test_skip_returns_exactly_7_frames(synthetic_smallface: Image.Image) -> None:
    frames = make_skip_frames(synthetic_smallface)
    assert len(frames) == 7


def test_skip_frame_sizes_match_canonical(synthetic_smallface: Image.Image) -> None:
    """Frames 1–4 are 17×6 (eye); frames 5–7 are 14×6 (mouth)."""
    frames = make_skip_frames(synthetic_smallface)
    for i in range(4):
        assert frames[i].size == EYE_SUBFRAME_SIZE, f"Frame {i+1} (eye): {frames[i].size}"
    for i in range(4, 7):
        assert frames[i].size == MOUTH_SUBFRAME_SIZE, f"Frame {i+1} (mouth): {frames[i].size}"


def test_skip_rejects_wrong_base_size() -> None:
    """make_skip_frames must reject non-48×43 input — would crash the game."""
    wrong_size = Image.new("RGBA", (100, 100), (255, 0, 0, 255))
    import pytest
    with pytest.raises(AssertionError, match="48×43"):
        make_skip_frames(wrong_size)


def test_skip_with_edge_eye_box_still_produces_valid_frames(synthetic_smallface: Image.Image) -> None:
    """Eye box at the canvas edge should clamp to a valid 17×6 crop."""
    # Put the eye box at the bottom-right corner — would overflow without clamping
    edge_eye = BoundingBox(x=40, y=40, w=0, h=0)
    edge_mouth = BoundingBox(x=40, y=40, w=0, h=0)
    frames = make_skip_frames(synthetic_smallface, edge_eye, edge_mouth)
    assert len(frames) == 7
    for i in range(4):
        assert frames[i].size == EYE_SUBFRAME_SIZE
    for i in range(4, 7):
        assert frames[i].size == MOUTH_SUBFRAME_SIZE


# ──────────────────────────────────────────────────────────────────────────
#  STI write — the binary format, with engine-crash protection
# ──────────────────────────────────────────────────────────────────────────

def test_write_smallface_round_trip(tmp_path: Path, synthetic_smallface: Image.Image) -> None:
    """The full skip-animation path: base + 7 dummies → 8-frame STI on disk."""
    frames = make_skip_frames(synthetic_smallface)
    sti_path = tmp_path / "merc.sti"
    write_smallface_sti(sti_path, synthetic_smallface, frames)
    assert sti_path.is_file()
    assert sti_path.stat().st_size > 0


def test_skip_mode_produces_8_valid_frames(tmp_path: Path, synthetic_smallface: Image.Image) -> None:
    """Skip-animation STIs must still satisfy the engine's 8-frame layout.

    'Skip' is a UX label — the binary always has 8 sub-frames at the canonical
    sizes (48×43 / 17×6 ×4 / 14×6 ×3) or the engine crashes on render.
    """
    frames = make_skip_frames(synthetic_smallface)
    sti_path = tmp_path / "skip.sti"
    write_smallface_sti(sti_path, synthetic_smallface, frames)
    info = verify_smallface_sti(sti_path)
    assert info["frame_count"] == 8
    assert info["frame_sizes"] == [
        SMALLFACE_BASE_SIZE,
        EYE_SUBFRAME_SIZE, EYE_SUBFRAME_SIZE, EYE_SUBFRAME_SIZE, EYE_SUBFRAME_SIZE,
        MOUTH_SUBFRAME_SIZE, MOUTH_SUBFRAME_SIZE, MOUTH_SUBFRAME_SIZE,
    ]
    assert info["is_smallface"] is True


def test_write_smallface_rejects_wrong_base_size(tmp_path: Path) -> None:
    import pytest
    wrong_base = Image.new("RGBA", (100, 100), (255, 0, 0, 255))
    fake_frames = [Image.new("RGBA", (17, 6), (0, 0, 0, 0)) for _ in range(4)] + \
                  [Image.new("RGBA", (14, 6), (0, 0, 0, 0)) for _ in range(3)]
    with pytest.raises(AssertionError, match=r"48.?43"):
        write_smallface_sti(tmp_path / "fail.sti", wrong_base, fake_frames)


def test_write_smallface_rejects_wrong_frame_count(tmp_path: Path, synthetic_smallface: Image.Image) -> None:
    import pytest
    too_few = [Image.new("RGBA", (17, 6), (0, 0, 0, 0)) for _ in range(5)]
    with pytest.raises(AssertionError, match="7 animation frames"):
        write_smallface_sti(tmp_path / "fail.sti", synthetic_smallface, too_few)


def test_write_smallface_accepts_consistent_non_vanilla_sizes(
    tmp_path: Path, synthetic_smallface: Image.Image,
) -> None:
    """Non-vanilla but internally-consistent sub-frame sizes are accepted —
    Vengeance ships 31x13 / 32x21, AIMNAS variants ship others. The
    engine reads dimensions from per-frame headers (Faces.cpp:480-481).
    """
    eye_31x13 = [Image.new("RGBA", (31, 13), (200, 100, 100, 255)) for _ in range(4)]
    mouth_32x21 = [Image.new("RGBA", (32, 21), (100, 200, 100, 255)) for _ in range(3)]
    write_smallface_sti(tmp_path / "vengeance_sized.sti", synthetic_smallface,
                        eye_31x13 + mouth_32x21)
    assert (tmp_path / "vengeance_sized.sti").is_file()


def test_skip_honors_custom_subframe_sizes_from_bounding_box(
    tmp_path: Path, synthetic_smallface: Image.Image,
) -> None:
    """When the Create wizard's drag-rect supplies w/h on the BoundingBox,
    make_skip_frames produces sub-frames at those sizes — not the canonical
    17x6 / 14x6. Round-trips through the STI writer + verifier.
    """
    eye_box = BoundingBox(x=8, y=6, w=31, h=13)    # Vengeance-style eye
    mouth_box = BoundingBox(x=8, y=22, w=32, h=21)  # Vengeance-style mouth
    frames = make_skip_frames(synthetic_smallface, eye_box, mouth_box)

    for i in range(4):
        assert frames[i].size == (31, 13), f"eye frame {i+1}: {frames[i].size}"
    for i in range(4, 7):
        assert frames[i].size == (32, 21), f"mouth frame {i+1}: {frames[i].size}"

    sti_path = tmp_path / "custom_size.sti"
    write_smallface_sti(sti_path, synthetic_smallface, frames)
    info = verify_smallface_sti(sti_path)
    assert info["is_smallface"] is True
    assert info["eye_subframe_size"] == (31, 13)
    assert info["mouth_subframe_size"] == (32, 21)


def test_skip_falls_back_to_canonical_when_box_has_zero_size(
    synthetic_smallface: Image.Image,
) -> None:
    """A BoundingBox with w=0 or h=0 means 'use vanilla canonical' —
    matches the existing default behavior so the schema change is
    backward-compatible with callers that don't supply sizes."""
    box_no_size = BoundingBox(x=10, y=8)  # default w=h=0
    frames = make_skip_frames(synthetic_smallface, box_no_size, box_no_size)
    assert frames[0].size == EYE_SUBFRAME_SIZE
    assert frames[4].size == MOUTH_SUBFRAME_SIZE


def test_skip_clamps_box_size_to_canvas(
    synthetic_smallface: Image.Image,
) -> None:
    """If the user drags a rectangle larger than the 48x43 SmallFace, the
    wizard clamps to the canvas size rather than crashing."""
    huge_box = BoundingBox(x=0, y=0, w=100, h=100)
    frames = make_skip_frames(synthetic_smallface, huge_box, huge_box)
    w, h = frames[0].size
    assert w <= 48 and h <= 43, f"eye sub-frame {(w, h)} exceeds canvas"
    assert frames[4].size[0] <= 48 and frames[4].size[1] <= 43


def test_write_static_bigface(tmp_path: Path, synthetic_smallface: Image.Image) -> None:
    big = synthetic_smallface.resize((106, 122), Image.Resampling.LANCZOS)
    big_path = tmp_path / "bigface.sti"
    write_static_sti(big_path, big)
    info = verify_smallface_sti(big_path)
    assert info["frame_count"] == 1
    assert info["is_static_single_frame"] is True
    assert info["canvas_size"] == (106, 122)


def test_saved_sti_palette_is_not_rainbow_noise(tmp_path: Path) -> None:
    """Regression test for the 'rainbow / pink noise' bug.

    `ja2py.fileformats.Sti._palette_to_bytes` checks `palette.rawmode` —
    if falsy, it treats the bytes as planar (RRR…GGG…BBB) and reorders
    into garbage. `sti._build_image_palette` MUST set `rawmode='RGB'`
    on the ImagePalette it constructs so the SHARED palette (the one
    `Images8Bit(...)` passes through to `save_8bit_sti`) survives the
    round trip with source colors intact.

    Previously fixed only on the per-frame palettes; this asserts the
    shared palette case.
    """
    from ja2py.fileformats.Sti import load_8bit_sti

    img = Image.new("RGBA", (32, 32))
    pixels = img.load()
    for y in range(32):
        for x in range(32):
            if y < 16:
                pixels[x, y] = (255, 0, 0, 255)   # top half pure red
            else:
                pixels[x, y] = (0, 0, 255, 255)   # bottom half pure blue
            if x == 16:
                pixels[x, y] = (0, 255, 0, 255)   # middle column pure green

    out = tmp_path / "rainbow_check.sti"
    write_static_sti(out, img)

    with open(out, "rb") as f:
        loaded = load_8bit_sti(f)

    pal_bytes = list(loaded.palette.tobytes())
    # We expect at least one clean red, blue, and green triple in the palette.
    has_red = any(
        pal_bytes[i * 3] > 200 and pal_bytes[i * 3 + 1] < 50 and pal_bytes[i * 3 + 2] < 50
        for i in range(256)
    )
    has_blue = any(
        pal_bytes[i * 3] < 50 and pal_bytes[i * 3 + 1] < 50 and pal_bytes[i * 3 + 2] > 200
        for i in range(256)
    )
    has_green = any(
        pal_bytes[i * 3] < 50 and pal_bytes[i * 3 + 1] > 200 and pal_bytes[i * 3 + 2] < 50
        for i in range(256)
    )
    assert has_red, "Rainbow-noise regression: red not in saved palette"
    assert has_blue, "Rainbow-noise regression: blue not in saved palette"
    assert has_green, "Rainbow-noise regression: green not in saved palette"


# ----------------------------------------------------------------------
#  animate_explicit: user-supplied or bundle-supplied animation frames
# ----------------------------------------------------------------------

def test_explicit_pre_cropped_eye_frames_used_verbatim() -> None:
    """If the caller supplies a 17x6 PNG, it lands in the slot verbatim
    (no re-cropping). The verbatim pixels must show up in the resulting
    sub-frame so a hand-painted blink survives the pipeline.
    """
    from mercwizard_core.portrait.animate_explicit import make_explicit_frames

    base = Image.new("RGBA", (48, 43), (200, 150, 120, 255))
    # Mark the eye source with a distinctive color that the base doesn't use
    eye_src = Image.new("RGBA", (17, 6), (255, 0, 255, 255))
    mouth_src = Image.new("RGBA", (14, 6), (0, 255, 255, 255))

    frames = make_explicit_frames(
        base, eye_sources=[eye_src], mouth_sources=[mouth_src],
        eye_box=DEFAULT_EYE_BOX, mouth_box=DEFAULT_MOUTH_BOX,
    )
    assert len(frames) == 7
    # All four eye frames should be the distinctive magenta (auto-padded)
    for i in range(4):
        assert frames[i].size == EYE_SUBFRAME_SIZE
        assert frames[i].getpixel((8, 3)) == (255, 0, 255, 255), \
            f"Eye frame {i} lost its hand-painted color"
    # All three mouth frames should be cyan
    for i in range(4, 7):
        assert frames[i].size == MOUTH_SUBFRAME_SIZE
        assert frames[i].getpixel((7, 3)) == (0, 255, 255, 255)


def test_explicit_full_face_source_auto_cropped_at_eye_box() -> None:
    """When the supplied PNG is full-face-sized (not the 17x6 sub-frame),
    the wizard crops a canonical-sized window at eye_box.x/y matching where
    the engine will render the strip.
    """
    from mercwizard_core.portrait.animate_explicit import make_explicit_frames

    base = Image.new("RGBA", (48, 43), (200, 150, 120, 255))
    # Full-face source with a bright row exactly at the eye region
    full_face = Image.new("RGBA", (48, 43), (50, 50, 50, 255))
    for y in range(DEFAULT_EYE_BOX.y, DEFAULT_EYE_BOX.y + 6):
        for x in range(DEFAULT_EYE_BOX.x, DEFAULT_EYE_BOX.x + 17):
            full_face.putpixel((x, y), (255, 128, 0, 255))

    frames = make_explicit_frames(
        base, eye_sources=[full_face], mouth_sources=[base],
        eye_box=DEFAULT_EYE_BOX, mouth_box=DEFAULT_MOUTH_BOX,
    )
    # The cropped eye region should be all bright orange
    assert frames[0].size == EYE_SUBFRAME_SIZE
    assert frames[0].getpixel((0, 0)) == (255, 128, 0, 255)
    assert frames[0].getpixel((16, 5)) == (255, 128, 0, 255)


def test_explicit_three_eye_sources_use_engine_dup_convention() -> None:
    """Three eye sources fill slots [1, 2, slot1-dup, 3] per the engine's
    'frame 3 is hardware duplicate of frame 1' convention.
    """
    from mercwizard_core.portrait.animate_explicit import make_explicit_frames

    base = Image.new("RGBA", (48, 43), (100, 100, 100, 255))
    e1 = Image.new("RGBA", (17, 6), (255, 0, 0, 255))    # red
    e2 = Image.new("RGBA", (17, 6), (0, 255, 0, 255))    # green
    e3 = Image.new("RGBA", (17, 6), (0, 0, 255, 255))    # blue
    mouth = Image.new("RGBA", (14, 6), (255, 255, 255, 255))

    frames = make_explicit_frames(
        base, eye_sources=[e1, e2, e3], mouth_sources=[mouth],
        eye_box=DEFAULT_EYE_BOX, mouth_box=DEFAULT_MOUTH_BOX,
    )
    # slot 1: red, slot 2: green, slot 3: red (dup of slot 1), slot 4: blue
    assert frames[0].getpixel((0, 0))[:3] == (255, 0, 0)
    assert frames[1].getpixel((0, 0))[:3] == (0, 255, 0)
    assert frames[2].getpixel((0, 0))[:3] == (255, 0, 0), \
        "Slot 3 should duplicate slot 1 per engine convention"
    assert frames[3].getpixel((0, 0))[:3] == (0, 0, 255)


def test_explicit_inconsistent_eye_frame_sizes_rejected_at_write() -> None:
    """All 4 eye frames must share a size (engine reads usEyesWidth from
    frame 1 and reuses it). Mixing 17x6 and 31x13 eye frames would garble
    the animation cycle, so write_smallface_sti refuses.
    """
    import pytest
    from mercwizard_core.portrait.sti import write_smallface_sti
    from pathlib import Path
    import tempfile

    base = Image.new("RGBA", (48, 43), (255, 255, 255, 255))
    eye_a = Image.new("RGBA", (17, 6), (255, 0, 0, 255))
    eye_b = Image.new("RGBA", (31, 13), (0, 255, 0, 255))   # different size
    mouth = Image.new("RGBA", (14, 6), (0, 0, 255, 255))
    mixed_eye_frames = [eye_a, eye_b, eye_a, eye_a, mouth, mouth, mouth]

    with tempfile.TemporaryDirectory() as td:
        with pytest.raises(AssertionError, match="All 4 eye frames must share"):
            write_smallface_sti(Path(td) / "fail.sti", base, mixed_eye_frames)


def test_explicit_smaller_than_canonical_now_valid() -> None:
    """Sub-frame sizes are mod-defined (Faces.cpp:480-481). Vengeance uses
    31x13 / 32x21; vanilla uses 17x6 / 14x6; a hypothetical mod could even
    use 10x4. The wizard accepts whatever size the first source declares,
    as long as all sources in a region agree.
    """
    from mercwizard_core.portrait.animate_explicit import make_explicit_frames

    base = Image.new("RGBA", (48, 43), (255, 255, 255, 255))
    sub = Image.new("RGBA", (10, 4), (255, 0, 0, 255))
    mouth = Image.new("RGBA", (14, 6), (0, 0, 255, 255))

    frames = make_explicit_frames(
        base, eye_sources=[sub], mouth_sources=[mouth],
        eye_box=DEFAULT_EYE_BOX, mouth_box=DEFAULT_MOUTH_BOX,
    )
    assert len(frames) == 7
    for i in range(4):
        assert frames[i].size == (10, 4)
    for i in range(4, 7):
        assert frames[i].size == (14, 6)


# ----------------------------------------------------------------------
#  compile_and_write_all - explicit-frames + bigface-override paths
# ----------------------------------------------------------------------

def test_compile_with_explicit_eye_pngs_writes_distinctive_frames(
    tmp_path: Path, synthetic_smallface: Image.Image,
) -> None:
    """End-to-end: passing explicit_eye_pngs to compile_and_write_all
    produces a SmallFace STI whose eye sub-frames carry the explicit
    pixels (not just re-cropped from the base).
    """
    import io
    from ja2py.fileformats.Sti import load_8bit_sti
    from mercwizard_core.portrait.compile import compile_and_write_all

    install_root = tmp_path
    (install_root / "Data-1.13" / "TableData").mkdir(parents=True, exist_ok=True)
    (install_root / "Data-1.13" / "faces").mkdir(parents=True, exist_ok=True)

    base_buf = io.BytesIO()
    synthetic_smallface.convert("RGBA").save(base_buf, format="PNG")

    # Hand-painted blink: a 17x6 fully-magenta strip (pre-cropped)
    eye_blink = Image.new("RGBA", (17, 6), (255, 0, 255, 255))
    eye_buf = io.BytesIO()
    eye_blink.save(eye_buf, format="PNG")

    compile_and_write_all(
        install_root=install_root,
        face_index=42,
        source_png_bytes=base_buf.getvalue(),
        explicit_eye_pngs=[eye_buf.getvalue()],
    )

    sti_path = install_root / "Data-1.13" / "faces" / "42.sti"
    with open(sti_path, "rb") as f:
        loaded = load_8bit_sti(f)

    assert len(loaded.images) == 8, "SmallFace STI must have 8 frames"
    # Eye sub-frames are at indices 1..4
    eye_frame = loaded.images[1].image
    # The frame should be 17x6 and contain magenta pixels (vs the
    # tan/skin synthetic_smallface base it would have if skip-mode ran)
    assert eye_frame.size == EYE_SUBFRAME_SIZE
    palette = list(loaded.palette.tobytes())
    pixels = list(eye_frame.getdata())
    # Look up at least one palette entry that's magenta-ish, and confirm
    # it shows up in the eye sub-frame pixel indices.
    magenta_indices = {
        i for i in range(256)
        if palette[i*3] > 200 and palette[i*3+1] < 80 and palette[i*3+2] > 200
    }
    assert magenta_indices, "magenta not present in SmallFace shared palette"
    assert any(p in magenta_indices for p in pixels), \
        "explicit magenta eye frame did not survive to the STI"


def test_compile_with_bigface_override_uses_separate_source(
    tmp_path: Path, synthetic_smallface: Image.Image,
) -> None:
    """Passing bigface_source_png makes the 106x122 BigFace come from that
    image instead of the main portrait. Useful when the artist authored a
    wider hero shot for the AIM/M.E.R.C. website.
    """
    import io
    from ja2py.fileformats.Sti import load_8bit_sti
    from mercwizard_core.portrait.compile import compile_and_write_all

    install_root = tmp_path
    (install_root / "Data-1.13" / "TableData").mkdir(parents=True, exist_ok=True)
    (install_root / "Data-1.13" / "faces").mkdir(parents=True, exist_ok=True)

    main_buf = io.BytesIO()
    synthetic_smallface.convert("RGBA").save(main_buf, format="PNG")

    # BigFace source: a solid cyan image. Center-crop+resize will keep it cyan.
    bigface_src = Image.new("RGBA", (1024, 1024), (0, 200, 200, 255))
    bigface_buf = io.BytesIO()
    bigface_src.save(bigface_buf, format="PNG")

    compile_and_write_all(
        install_root=install_root,
        face_index=42,
        source_png_bytes=main_buf.getvalue(),
        bigface_source_png=bigface_buf.getvalue(),
    )

    bigface_path = install_root / "Data-1.13" / "faces" / "BigFaces" / "42.sti"
    with open(bigface_path, "rb") as f:
        loaded = load_8bit_sti(f)
    assert loaded.images[0].image.size == (106, 122)
    pal = list(loaded.palette.tobytes())
    has_cyan = any(
        pal[i*3] < 50 and pal[i*3+1] > 150 and pal[i*3+2] > 150
        for i in range(256)
    )
    assert has_cyan, "BigFace did not pick up the override source's cyan"
