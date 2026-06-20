"""Regression tests for SLF-packed face resolution + 16-bit STI decode.

This path silently served blank roster portraits for months before it was fixed
(commits d5fb268 / 7728655): a vanilla/AIMNAS ``Faces.slf`` stores faces at the
ARCHIVE ROOT (``/05.STI``), zero-padded to two digits, NOT under a ``Faces/``
prefix, and the BigFaces subdir's casing varies by archive; separately, many
1.13 / mod face packs ship 16-bit STIs that were undecodable. None of it had a
test, so a refactor of ``face_sti_bytes`` or ``decode_sti_frame_to_png`` could
re-break it unnoticed. Lock the contract here.
"""
from __future__ import annotations

import io
from pathlib import Path

from PIL import Image

from ja2py.content.Image import Image16Bit
from ja2py.fileformats.SlfFS import BufferedSlfFS
from ja2py.fileformats.Sti import save_16bit_sti

from mercwizard_core.install_context import make_install_context
from mercwizard_core.sti_decode import decode_sti_frame_to_png


def _faces_slf(path: Path, entries: list[tuple[str, bytes]]) -> None:
    """Pack ``entries`` ((relpath, bytes)) into an SLF written to ``path``.

    Mirrors test_tools' BufferedSlfFS packer (the canonical writer; SlfFS itself
    is read-only).
    """
    slf = BufferedSlfFS()
    slf.library_name = "TEST"
    slf.library_path = "Faces.slf"
    for relpath, data in entries:
        p = "/" + relpath.lstrip("/").replace("\\", "/")
        parts = p.strip("/").split("/")
        if len(parts) > 1:
            try:
                slf.makedirs("/" + "/".join(parts[:-1]), recreate=True)
            except Exception:
                pass
        with slf.open(p, "wb") as f:
            f.write(data)
    with open(path, "wb") as f:
        slf.save(f)


def test_face_sti_bytes_resolves_root_stored_zero_padded_slf_entry(tmp_path: Path) -> None:
    """Faces.slf stores faces at the ARCHIVE ROOT, zero-padded to two digits
    (``/05.STI``), NOT under a ``Faces/`` prefix. ``face_sti_bytes`` must resolve a
    single-digit index by its zero-padded root name — the exact miss that blanked
    most SLF-only rosters. Resolution returns the raw bytes (no decode), so the
    entries here are sentinel bytes to isolate the lookup logic."""
    install = tmp_path / "inst"
    (install / "Data-1.13").mkdir(parents=True)
    _faces_slf(install / "Data-1.13" / "Faces.slf", [
        ("05.STI", b"ROOT_FACE_5_BYTES"),         # root-stored, zero-padded
        ("BIGFACES/05.STI", b"BIG_FACE_5_BYTES"),  # uppercase subdir variant
    ])
    ctx = make_install_context(install)

    small = ctx.face_sti_bytes(5, "smallface")
    assert small is not None, "root-stored zero-padded smallface not resolved"
    data, source_id = small
    assert data == b"ROOT_FACE_5_BYTES"
    assert source_id.startswith("slf:")  # came from the SLF, with a versioned id

    # BigFaces lives under a case-varying subdir (BigFaces/BIGFACES/bigfaces);
    # the resolver probes every casing, so the uppercase variant resolves.
    big = ctx.face_sti_bytes(5, "bigface")
    assert big is not None, "uppercase BIGFACES subdir variant not resolved"
    assert big[0] == b"BIG_FACE_5_BYTES"


def test_face_sti_bytes_resolves_b_prefixed_big_face(tmp_path: Path) -> None:
    """Vanilla Faces.slf stores the 90x100 talking-head big face at the archive
    ROOT as ``B<NN>.STI`` (``/B75.STI``). ~75 story NPCs (Deidranna 75, Kingpin
    86, Walker 100, ...) ship ONLY this B-prefixed big face and no plain small
    face, so a bigface request must probe the B-prefix or they blank in the
    roster even though the engine shows them in dialogue. Lock that contract."""
    install = tmp_path / "inst"
    (install / "Data").mkdir(parents=True)
    _faces_slf(install / "Data" / "Faces.slf", [
        ("B75.STI", b"BIG_NPC_75_BYTES"),  # B-prefixed big face, no plain /75.STI
    ])
    ctx = make_install_context(install)

    big = ctx.face_sti_bytes(75, "bigface")
    assert big is not None, "B-prefixed root big face not resolved"
    assert big[0] == b"BIG_NPC_75_BYTES"
    assert big[1].startswith("slf:")

    # The same NPC has no plain small face, so smallface correctly resolves to
    # None — the roster bakes at bigface, so the B-prefix branch is what fills it.
    assert ctx.face_sti_bytes(75, "smallface") is None


def test_face_sti_bytes_returns_none_when_absent(tmp_path: Path) -> None:
    """A face index the archive doesn't carry resolves to None (the caller then
    falls back / serves a blank), not an exception."""
    install = tmp_path / "inst"
    (install / "Data-1.13").mkdir(parents=True)
    _faces_slf(install / "Data-1.13" / "Faces.slf", [("05.STI", b"x")])
    ctx = make_install_context(install)
    assert ctx.face_sti_bytes(99, "smallface") is None


def test_face_sti_bytes_resolves_imp_face_from_impfaces_tree(tmp_path: Path) -> None:
    """IMP pre-gen / player-character portraits live under IMPFaces/ (root
    <idx>.sti smallface; BigFaces/65Face/33Face subdirs), a tree the FACES
    probe never touches — so a Type=IMP face (e.g. an in-roster created IMP at
    index 200-219) used to resolve to nothing. The resolver now probes it as a
    last fallback. Casing of the subdir varies by layer (Data uses BIGFACES)."""
    install = tmp_path / "inst"
    impfaces = install / "Data" / "IMPFaces"
    (impfaces / "BIGFACES").mkdir(parents=True)
    (impfaces / "200.STI").write_bytes(b"IMP_SMALL_200")     # root = smallface
    (impfaces / "BIGFACES" / "200.STI").write_bytes(b"IMP_BIG_200")
    ctx = make_install_context(install)

    big = ctx.face_sti_bytes(200, "bigface")
    assert big is not None and big[0] == b"IMP_BIG_200"
    small = ctx.face_sti_bytes(200, "smallface")
    assert small is not None and small[0] == b"IMP_SMALL_200"
    assert ctx.face_sti_bytes(201, "bigface") is None  # absent index


def test_faces_tree_wins_over_impfaces(tmp_path: Path) -> None:
    """When the same index exists in BOTH the FACES tree and IMPFaces/, FACES
    wins — the IMPFACES probe runs LAST, so it can never shadow real face art."""
    install = tmp_path / "inst"
    loose_big = install / "Data-1.13" / "Faces" / "BigFaces"
    loose_big.mkdir(parents=True)
    (loose_big / "200.sti").write_bytes(b"FACES_WINS")
    imp_big = install / "Data" / "IMPFaces" / "BIGFACES"
    imp_big.mkdir(parents=True)
    (imp_big / "200.STI").write_bytes(b"IMP_LOSES")
    ctx = make_install_context(install)
    assert ctx.face_sti_bytes(200, "bigface")[0] == b"FACES_WINS"


def test_vehicle_icon_resolves_from_vehicles_xml_sti_face_icon(tmp_path: Path) -> None:
    """A Type=5 vehicle profile draws its Vehicles.xml StiFaceIcon (e.g.
    INTERFACE\\Jeep.sti), not FACES\\<idx>.sti. vehicle_icon_bytes joins by
    uiIndex==slot and resolves the icon from loose Interface/ + Interface*.slf."""
    install = tmp_path / "inst"
    td = install / "Data-1.13" / "TableData"
    td.mkdir(parents=True)
    (td / "Vehicles.xml").write_text(
        "<VEHICLES><VEHICLE><uiIndex>199</uiIndex><Name>Jeep</Name>"
        "<StiFaceIcon>INTERFACE\\Jeep.sti</StiFaceIcon></VEHICLE></VEHICLES>",
        encoding="utf-8",
    )
    (install / "Data").mkdir(parents=True, exist_ok=True)
    _faces_slf(install / "Data" / "Interface.slf", [("JEEP.STI", b"JEEP_ICON_BYTES")])
    ctx = make_install_context(install)

    assert ctx.vehicle_face_icon_rel(199) == "INTERFACE\\Jeep.sti"
    icon = ctx.vehicle_icon_bytes(199)
    assert icon is not None and icon[0] == b"JEEP_ICON_BYTES"
    assert ctx.vehicle_icon_bytes(160) is None  # no entry → None


def test_decode_sti_frame_to_png_handles_16bit_sti() -> None:
    """16-bit STIs (many 1.13 / mod face packs) decode to a PNG — previously
    unsupported, which left those mercs blank in the roster. A 16-bit STI carries
    a single RGB image, so only frame 0 is meaningful."""
    rgb = Image.new("RGB", (8, 6), (200, 100, 50))
    buf = io.BytesIO()
    save_16bit_sti(Image16Bit(rgb), buf)
    sti_bytes = buf.getvalue()

    png = decode_sti_frame_to_png(sti_bytes, 0)
    assert png is not None, "16-bit STI failed to decode"
    assert png[:8] == b"\x89PNG\r\n\x1a\n", "decoded output is not a PNG"
    # frame_index > 0 is meaningless for a single-image 16-bit STI.
    assert decode_sti_frame_to_png(sti_bytes, 1) is None
