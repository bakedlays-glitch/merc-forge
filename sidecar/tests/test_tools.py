"""Tests for the standalone STI Viewer + SLF Extractor routes added
2026-05-25 (sidecar/routes/tools.py).

Both surfaces are file-pick driven — no install / tileset context — so
these tests synthesize fixture STI and SLF byte streams in tmp_path and
hit the endpoints via FastAPI's TestClient.
"""
from __future__ import annotations

import io
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from PIL import Image, ImagePalette

from main import create_app


@pytest.fixture
def client() -> TestClient:
    return TestClient(create_app())


# ─── Fixture builders ────────────────────────────────────────────────

def _build_multiframe_sti_bytes(frame_count: int = 3) -> bytes:
    """Synthesize an N-frame 8-bit STI. Each frame is a tiny 4x4
    indexed image. Same pattern as test_mapforge_library's helper."""
    from ja2py.content.Image import Images8Bit, SubImage8Bit
    from ja2py.fileformats.Sti import save_8bit_sti

    palette = ImagePalette.raw("RGB", bytes(range(256)) * 3)
    images = []
    for i in range(frame_count):
        img = Image.new("P", (4, 4), color=i + 1)
        img.putpalette(palette.palette)
        # Synthesize varied per-frame offsets so the viewer's frame-info
        # surface has something distinctive to assert on.
        images.append(SubImage8Bit(img, offsets=(i, i * 2), aux_data=None))
    container = Images8Bit(images=images, palette=palette, width=4, height=4)
    out = io.BytesIO()
    save_8bit_sti(container, out)
    return out.getvalue()


def _build_synthetic_slf(tmp_path: Path,
                         entries: list[tuple[str, bytes]]) -> Path:
    """Pack `entries` (list of (relpath, bytes)) into a synthetic SLF
    file using ja2py's BufferedSlfFS. Returns the on-disk SLF path.

    ja2py's BufferedSlfFS is the canonical writer (SlfFS itself is
    read-only); we round-trip through that.
    """
    from ja2py.fileformats.SlfFS import BufferedSlfFS

    slf = BufferedSlfFS()
    slf.library_name = "TEST"
    slf.library_path = "Test.slf"
    for relpath, data in entries:
        # BufferedSlfFS uses forward-slash paths. Ensure leading "/".
        slf_path = "/" + relpath.lstrip("/").replace("\\", "/")
        # Parent dirs need to exist before writing.
        parts = slf_path.strip("/").split("/")
        if len(parts) > 1:
            try:
                slf.makedirs("/" + "/".join(parts[:-1]), recreate=True)
            except Exception:
                pass
        with slf.open(slf_path, "wb") as f:
            f.write(data)
    out_path = tmp_path / "synthetic.slf"
    with open(out_path, "wb") as f:
        slf.save(f)
    return out_path


# ─── STI Viewer endpoints ────────────────────────────────────────────

def test_sti_decode_happy_path(client: TestClient, tmp_path: Path) -> None:
    """A 3-frame STI returns metadata with frame_count=3 plus per-frame
    width/height/offset_x/offset_y."""
    sti_bytes = _build_multiframe_sti_bytes(frame_count=3)
    sti_path = tmp_path / "thing.sti"
    sti_path.write_bytes(sti_bytes)

    r = client.get(f"/api/v1/tools/sti/decode?path={sti_path}")
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["frame_count"] == 3
    assert data["is_8bit"] is True
    assert data["palette_present"] is True
    assert data["has_jsd"] is False
    assert data["jsd_path"] is None
    # Per-frame info covers all frames; offsets were (0,0), (1,2), (2,4).
    frames = data["frames"]
    assert len(frames) == 3
    assert frames[0]["width"] == 4
    assert frames[0]["height"] == 4
    assert frames[0]["offset_x"] == 0
    assert frames[0]["offset_y"] == 0
    assert frames[1]["offset_x"] == 1
    assert frames[1]["offset_y"] == 2
    assert frames[2]["offset_x"] == 2
    assert frames[2]["offset_y"] == 4


def test_sti_decode_404_when_missing(client: TestClient, tmp_path: Path) -> None:
    bogus = tmp_path / "does_not_exist.sti"
    r = client.get(f"/api/v1/tools/sti/decode?path={bogus}")
    assert r.status_code == 404
    assert r.json()["detail"]["error"] == "FILE_NOT_FOUND"


def test_sti_decode_400_when_wrong_suffix(client: TestClient, tmp_path: Path) -> None:
    """A .txt file still gets rejected even if it happens to exist."""
    p = tmp_path / "not_an_sti.txt"
    p.write_text("hello")
    r = client.get(f"/api/v1/tools/sti/decode?path={p}")
    assert r.status_code == 400
    assert r.json()["detail"]["error"] == "BAD_SUFFIX"


def test_sti_decode_reports_jsd_companion(client: TestClient,
                                          tmp_path: Path) -> None:
    """When a sibling .jsd exists, has_jsd=True + jsd_path is the
    sibling's path."""
    sti_bytes = _build_multiframe_sti_bytes(frame_count=1)
    sti_path = tmp_path / "thing.sti"
    sti_path.write_bytes(sti_bytes)
    jsd_path = tmp_path / "thing.jsd"
    # Real JSDs are 70+ bytes; the viewer only stat()s the file, doesn't
    # parse it on /decode.
    jsd_path.write_bytes(b"\x00" * 100)

    r = client.get(f"/api/v1/tools/sti/decode?path={sti_path}")
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["has_jsd"] is True
    assert data["jsd_path"] == str(jsd_path)


def test_sti_frame_returns_png(client: TestClient, tmp_path: Path) -> None:
    sti_bytes = _build_multiframe_sti_bytes(frame_count=2)
    sti_path = tmp_path / "thing.sti"
    sti_path.write_bytes(sti_bytes)

    r = client.get(f"/api/v1/tools/sti/frame?path={sti_path}&frame=0")
    assert r.status_code == 200, r.text
    assert r.headers["content-type"] == "image/png"
    # PNG signature: 89 50 4E 47 0D 0A 1A 0A
    assert r.content[:8] == b"\x89PNG\r\n\x1a\n"


def test_sti_frame_400_when_frame_oob(client: TestClient, tmp_path: Path) -> None:
    sti_bytes = _build_multiframe_sti_bytes(frame_count=1)
    sti_path = tmp_path / "thing.sti"
    sti_path.write_bytes(sti_bytes)
    r = client.get(f"/api/v1/tools/sti/frame?path={sti_path}&frame=99")
    assert r.status_code == 400
    assert r.json()["detail"]["error"] == "STI_FRAME_DECODE_FAILED"


def test_sti_jsd_404_when_missing(client: TestClient, tmp_path: Path) -> None:
    sti_bytes = _build_multiframe_sti_bytes(frame_count=1)
    sti_path = tmp_path / "no_jsd.sti"
    sti_path.write_bytes(sti_bytes)
    r = client.get(f"/api/v1/tools/sti/jsd?path={sti_path}")
    assert r.status_code == 404
    assert r.json()["detail"]["error"] == "JSD_NOT_FOUND"


# ─── SLF Extractor endpoints ─────────────────────────────────────────

def test_slf_list_happy_path(client: TestClient, tmp_path: Path) -> None:
    slf_path = _build_synthetic_slf(tmp_path, [
        ("alpha.txt", b"alpha-content"),
        ("subdir/beta.bin", b"\x00\x01\x02"),
        ("gamma.dat", b"gamma" * 100),
    ])
    r = client.get(f"/api/v1/tools/slf/list?path={slf_path}")
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["entry_count"] == 3
    relpaths = [e["relpath"] for e in data["entries"]]
    assert "alpha.txt" in relpaths
    assert "gamma.dat" in relpaths
    # Subdir entry path normalized to forward slashes, no leading slash.
    assert "subdir/beta.bin" in relpaths
    # Sizes carried through accurately.
    size_by_path = {e["relpath"]: e["size"] for e in data["entries"]}
    assert size_by_path["alpha.txt"] == len(b"alpha-content")
    assert size_by_path["gamma.dat"] == len(b"gamma" * 100)


def test_slf_list_404_on_missing(client: TestClient, tmp_path: Path) -> None:
    bogus = tmp_path / "nope.slf"
    r = client.get(f"/api/v1/tools/slf/list?path={bogus}")
    assert r.status_code == 404
    assert r.json()["detail"]["error"] == "FILE_NOT_FOUND"


def test_slf_extract_all(client: TestClient, tmp_path: Path) -> None:
    slf_path = _build_synthetic_slf(tmp_path, [
        ("alpha.txt", b"AAA"),
        ("subdir/beta.bin", b"BBB"),
    ])
    dest = tmp_path / "out"
    r = client.post("/api/v1/tools/slf/extract", json={
        "slf_path": str(slf_path),
        "dest_dir": str(dest),
    })
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["extracted"] == 2
    assert data["skipped"] == 0
    assert data["errors"] == []
    # Files landed on disk with expected contents.
    assert (dest / "alpha.txt").read_bytes() == b"AAA"
    assert (dest / "subdir" / "beta.bin").read_bytes() == b"BBB"


def test_slf_extract_subset_via_members(client: TestClient,
                                        tmp_path: Path) -> None:
    """Passing `members` extracts only the named entries."""
    slf_path = _build_synthetic_slf(tmp_path, [
        ("alpha.txt", b"AAA"),
        ("beta.txt",  b"BBB"),
        ("gamma.txt", b"GGG"),
    ])
    dest = tmp_path / "selective"
    r = client.post("/api/v1/tools/slf/extract", json={
        "slf_path": str(slf_path),
        "dest_dir": str(dest),
        "members": ["alpha.txt", "gamma.txt"],
    })
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["extracted"] == 2
    assert (dest / "alpha.txt").is_file()
    assert (dest / "gamma.txt").is_file()
    assert not (dest / "beta.txt").exists()


def test_slf_extract_overwrite_false_skips_existing(client: TestClient,
                                                    tmp_path: Path) -> None:
    """overwrite=False keeps existing files put and bumps skipped."""
    slf_path = _build_synthetic_slf(tmp_path, [
        ("alpha.txt", b"new-content"),
    ])
    dest = tmp_path / "out"
    dest.mkdir()
    (dest / "alpha.txt").write_bytes(b"existing-content")
    r = client.post("/api/v1/tools/slf/extract", json={
        "slf_path": str(slf_path),
        "dest_dir": str(dest),
        "overwrite": False,
    })
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["extracted"] == 0
    assert data["skipped"] == 1
    # File untouched.
    assert (dest / "alpha.txt").read_bytes() == b"existing-content"


def test_slf_extract_creates_missing_dest(client: TestClient,
                                          tmp_path: Path) -> None:
    slf_path = _build_synthetic_slf(tmp_path, [
        ("alpha.txt", b"AAA"),
    ])
    dest = tmp_path / "does_not_yet_exist" / "nested"
    assert not dest.exists()
    r = client.post("/api/v1/tools/slf/extract", json={
        "slf_path": str(slf_path),
        "dest_dir": str(dest),
    })
    assert r.status_code == 200, r.text
    assert dest.is_dir()
    assert (dest / "alpha.txt").read_bytes() == b"AAA"


def test_slf_extract_stream_emits_done_event(client: TestClient,
                                              tmp_path: Path) -> None:
    """NDJSON streaming variant ends with a 'done' event carrying the
    final SlfExtractResult payload."""
    import json as _json
    slf_path = _build_synthetic_slf(tmp_path, [
        ("alpha.txt", b"AAA"),
        ("beta.txt",  b"BBB"),
    ])
    dest = tmp_path / "stream_out"
    with client.stream("POST", "/api/v1/tools/slf/extract/stream", json={
        "slf_path": str(slf_path),
        "dest_dir": str(dest),
    }) as r:
        assert r.status_code == 200
        events = []
        for raw_line in r.iter_lines():
            if not raw_line:
                continue
            events.append(_json.loads(raw_line))
    assert any(e.get("event") == "phase" for e in events)
    done = [e for e in events if e.get("event") == "done"]
    assert len(done) == 1
    payload = done[0]["data"]
    assert payload["extracted"] == 2
    assert payload["skipped"] == 0
    assert (dest / "alpha.txt").read_bytes() == b"AAA"
    assert (dest / "beta.txt").read_bytes() == b"BBB"
