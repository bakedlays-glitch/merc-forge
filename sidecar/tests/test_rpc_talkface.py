"""RPC talk-panel face compiler tests.

The engine needs a 90x100 base plus exactly four eye and three mouth
subimages.  MercProfiles stores the returned 90x100 coordinates; the tactical
48x43 coordinates remain a separate RPCFacesSmall override.
"""
from __future__ import annotations

import io

from PIL import Image, ImageDraw

from mercwizard_core.portrait.animate_skip import BoundingBox
from mercwizard_core.portrait.sti import verify_animated_face_sti
from mercwizard_core.portrait.talkface import build_talkface, write_talkface
from mercwizard_core.install_context import make_install_context


def _png(*, eye_shift: int = 0, mouth_open: int = 0) -> bytes:
    img = Image.new("RGBA", (200, 200), (61, 48, 39, 255))
    d = ImageDraw.Draw(img)
    d.ellipse((58, 65 + eye_shift, 82, 76 + eye_shift), fill=(220, 210, 185, 255))
    d.ellipse((118, 65 + eye_shift, 142, 76 + eye_shift), fill=(220, 210, 185, 255))
    d.rectangle((77, 122, 123, 128 + mouth_open), fill=(155, 55, 48, 255))
    out = io.BytesIO()
    img.save(out, format="PNG")
    return out.getvalue()


def test_build_talkface_detects_changed_regions_in_90x100_space():
    built = build_talkface(
        _png(),
        explicit_eye_pngs=[_png(eye_shift=4), _png(eye_shift=7)],
        explicit_mouth_pngs=[_png(mouth_open=3), _png(mouth_open=8), _png(mouth_open=12)],
        small_eye_box=BoundingBox(10, 8, 17, 6),
        small_mouth_box=BoundingBox(7, 28, 14, 6),
    )

    assert built.base.size == (90, 100)
    assert len(built.frames) == 7
    assert all(frame.size == built.frames[0].size for frame in built.frames[:4])
    assert all(frame.size == built.frames[4].size for frame in built.frames[4:])
    assert built.animated_eyes is True
    assert built.animated_mouth is True
    ex, ey = built.eyes_xy
    mx, my = built.mouth_xy
    assert 0 <= ex < 90 and 0 <= ey < 100
    assert 0 <= mx < 90 and 0 <= my < 100
    assert built.frames[0].getbbox() is not None
    assert built.frames[4].getbbox() is not None


def test_build_talkface_without_variants_emits_valid_noop_frames():
    built = build_talkface(
        _png(),
        explicit_eye_pngs=None,
        explicit_mouth_pngs=None,
        small_eye_box=BoundingBox(10, 8, 17, 6),
        small_mouth_box=BoundingBox(7, 28, 14, 6),
    )

    assert len(built.frames) == 7
    assert built.animated_eyes is False
    assert built.animated_mouth is False
    assert all(frame.getbbox() is None for frame in built.frames)
    assert built.eyes_xy != (0, 0)
    assert built.mouth_xy != (0, 0)


def test_written_talkface_reads_back_as_eight_frame_90x100_sti(tmp_path):
    built = build_talkface(
        _png(),
        explicit_eye_pngs=[_png(eye_shift=4), _png(eye_shift=7)],
        explicit_mouth_pngs=[_png(mouth_open=4), _png(mouth_open=9)],
        small_eye_box=BoundingBox(10, 8, 17, 6),
        small_mouth_box=BoundingBox(7, 28, 14, 6),
    )
    path = tmp_path / "B63.sti"
    write_talkface(path, built)

    info = verify_animated_face_sti(path, expected_base_size=(90, 100))
    assert info["valid"] is True
    assert info["frame_count"] == 8
    assert info["base_size"] == (90, 100)

    from_bytes = verify_animated_face_sti(path.read_bytes(), expected_base_size=(90, 100))
    assert from_bytes["valid"] is True
    assert from_bytes["frame_count"] == 8


def test_rpc_talkface_path_matches_engine_zero_padding(tmp_path):
    """The engine formats B-face ids as %02d below 100 and %03d above it."""
    ctx = make_install_context(tmp_path)

    assert ctx.rpc_talkface_path(7, for_write=True).name == "B07.sti"
    assert ctx.rpc_talkface_path(63, for_write=True).name == "B63.sti"
    assert ctx.rpc_talkface_path(100, for_write=True).name == "B100.sti"
