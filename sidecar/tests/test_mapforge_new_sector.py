"""R6 "New sector" + "Save a copy as…" — create / clone sectors.

build_empty_dat_bytes synthesizes a fresh empty .dat (no original bytes to
pass through); these tests pin that the result round-trips byte-exactly
through parse_dat_full + write_dat_bytes (so it loads in the editor) and
that the two endpoints behave: new-sector refuses to clobber without
overwrite, save-copy-as writes a NEW file and never touches the original.
"""
import threading
from pathlib import Path

import pytest

import routes.mapforge as mf
from mercwizard_core.mapforge_engine.dat_writer import (
    build_empty_dat_bytes,
    write_dat_bytes,
)
from mercwizard_core.mapforge_engine.parse_dat_ext import parse_dat_full
from routes.mapforge import (
    MapForgeSession,
    NewSectorBody,
    SaveCopyAsBody,
    _session_store,
    new_sector,
    save_copy_as,
)
from tests.test_mapforge_library import _build_minimal_dat

from fastapi import HTTPException


@pytest.fixture(autouse=True)
def _active_install_is_tmp(tmp_path, monkeypatch):
    """Point the active install at this test's tmp_path so the new
    `_confine_install_path` guard (which requires every .dat write to live
    inside the active install) passes — every dest these tests use is under
    tmp_path. Confinement itself is covered by test_mapforge_path_confinement."""
    monkeypatch.setattr(mf, "_active_install_root", lambda: tmp_path)


# ── build_empty_dat_bytes: byte-faithful empty map ──────────────────────


def test_empty_dat_roundtrips_byte_exactly():
    """parse(empty) → write_back must equal the original bytes — the B0
    identity gate the corpus harness proves for real maps, here for the
    synthetic empty one."""
    data = build_empty_dat_bytes(tileset=71)
    parsed = parse_dat_full(data, "<empty>")
    out = write_dat_bytes(parsed, data)
    assert out == data


def test_empty_dat_layer_contents():
    """Every tile = one (0,1) land entry; all other layers empty; rooms +
    heights all 0; flags 0 with a parseable 32-byte tail."""
    data = build_empty_dat_bytes(tileset=42)
    d = parse_dat_full(data, "<empty>")
    assert d["major"] == 7.0
    assert d["minor"] == 31
    assert d["rows"] == 160 and d["cols"] == 160
    assert d["flags"] == 0
    assert d["tileset"] == 42
    assert d["tail"] is not None  # flags==0 → tail is parseable
    assert all(t == [(0, 1)] for t in d["land"])
    for layer in ("objs", "structs", "shadows", "roofs", "onroofs"):
        assert all(len(t) == 0 for t in d[layer]), layer
    assert all(r == 0 for r in d["rooms"])
    assert all(h == 0 for h in d["heights"])


def test_empty_dat_custom_dims_roundtrip():
    data = build_empty_dat_bytes(tileset=1, rows=8, cols=8)
    d = parse_dat_full(data, "<empty>")
    assert d["rows"] == 8 and d["cols"] == 8
    assert write_dat_bytes(d, data) == data
    assert all(t == [(0, 1)] for t in d["land"])


def test_empty_dat_rejects_bad_dims():
    with pytest.raises(ValueError):
        build_empty_dat_bytes(tileset=1, rows=0, cols=8)
    with pytest.raises(ValueError):
        build_empty_dat_bytes(tileset=1, rows=2048, cols=8)


# ── POST /mapforge/new-sector ───────────────────────────────────────────


def test_new_sector_writes_loadable_file(tmp_path, monkeypatch):
    monkeypatch.setattr(mf, "_iso_renderer_available", True)
    dest = tmp_path / "maps" / "Z9.dat"
    res = new_sector(NewSectorBody(dat_path=str(dest), tileset=71))
    assert dest.is_file()
    assert res.tileset == 71 and res.rows == 160 and res.cols == 160
    assert res.bytes_written == dest.stat().st_size
    # Loads through the editor's parser with the expected ground fill.
    d = parse_dat_full(dest.read_bytes(), str(dest))
    assert d["tileset"] == 71
    assert all(t == [(0, 1)] for t in d["land"])


def test_new_sector_refuses_existing_without_overwrite(tmp_path, monkeypatch):
    monkeypatch.setattr(mf, "_iso_renderer_available", True)
    dest = tmp_path / "A1.dat"
    dest.write_bytes(b"existing")
    with pytest.raises(HTTPException) as exc:
        new_sector(NewSectorBody(dat_path=str(dest), tileset=1))
    assert exc.value.status_code == 409
    assert exc.value.detail["error"] == "FILE_EXISTS"
    assert dest.read_bytes() == b"existing"  # untouched


def test_new_sector_overwrites_when_confirmed(tmp_path, monkeypatch):
    monkeypatch.setattr(mf, "_iso_renderer_available", True)
    dest = tmp_path / "A1.dat"
    dest.write_bytes(b"existing")
    new_sector(NewSectorBody(dat_path=str(dest), tileset=7, overwrite=True))
    d = parse_dat_full(dest.read_bytes(), str(dest))
    assert d["tileset"] == 7


def test_new_sector_rejects_non_dat_suffix(tmp_path, monkeypatch):
    monkeypatch.setattr(mf, "_iso_renderer_available", True)
    with pytest.raises(HTTPException) as exc:
        new_sector(NewSectorBody(dat_path=str(tmp_path / "x.txt"), tileset=1))
    assert exc.value.status_code == 400


# ── POST /mapforge/sessions/{id}/save-copy-as ───────────────────────────


def _session_on(tmp_path: Path, data: bytes, sid: str = "copyas-sess"):
    src = tmp_path / "src" / "A9.dat"
    src.parent.mkdir(parents=True, exist_ok=True)
    src.write_bytes(data)
    sess = MapForgeSession.__new__(MapForgeSession)
    sess.id = sid
    sess.dat_path = src
    sess.xml_path = tmp_path / "nonexistent.xml"
    sess.tileset = 7
    sess.parsed = parse_dat_full(data, str(src))
    sess.original_bytes = data
    sess.dirty = True
    sess.edit_count = 1
    sess.created_at = 0.0
    sess.last_used_at = 0.0
    sess.read_only = False
    sess.source_uri = ""
    sess._lock = threading.Lock()
    _session_store._sessions[sess.id] = sess
    return sess


def test_save_copy_as_writes_new_file_leaves_original(tmp_path, monkeypatch):
    monkeypatch.setattr(mf, "_iso_renderer_available", True)
    data = _build_minimal_dat(land={0: [(1, 1)]})
    sess = _session_on(tmp_path, data)
    try:
        # Make an in-memory edit that has NOT been saved to the source.
        sess.parsed["heights"][3] = 80
        original_src_bytes = sess.dat_path.read_bytes()
        dest = tmp_path / "copy" / "A9_copy.dat"
        res = save_copy_as(sess.id, SaveCopyAsBody(dat_path=str(dest)))
        # New file carries the edit.
        d = parse_dat_full(dest.read_bytes(), str(dest))
        assert d["heights"][3] == 80
        assert res.bytes_written == dest.stat().st_size
        # Original on disk is byte-untouched, session stays dirty + same path.
        assert sess.dat_path.read_bytes() == original_src_bytes
        assert sess.dirty is True
        assert str(sess.dat_path).endswith("A9.dat")
    finally:
        _session_store._sessions.pop(sess.id, None)


def test_save_copy_as_refuses_existing_without_overwrite(tmp_path, monkeypatch):
    monkeypatch.setattr(mf, "_iso_renderer_available", True)
    data = _build_minimal_dat(land={0: [(1, 1)]})
    sess = _session_on(tmp_path, data, "copyas-exists")
    try:
        dest = tmp_path / "taken.dat"
        dest.write_bytes(b"keep")
        with pytest.raises(HTTPException) as exc:
            save_copy_as(sess.id, SaveCopyAsBody(dat_path=str(dest)))
        assert exc.value.status_code == 409
        assert dest.read_bytes() == b"keep"
    finally:
        _session_store._sessions.pop(sess.id, None)


def test_save_copy_as_refuses_same_as_source(tmp_path, monkeypatch):
    monkeypatch.setattr(mf, "_iso_renderer_available", True)
    data = _build_minimal_dat(land={0: [(1, 1)]})
    sess = _session_on(tmp_path, data, "copyas-same")
    try:
        with pytest.raises(HTTPException) as exc:
            save_copy_as(sess.id, SaveCopyAsBody(dat_path=str(sess.dat_path)))
        assert exc.value.status_code == 409
        assert exc.value.detail["error"] == "SAME_AS_SOURCE"
    finally:
        _session_store._sessions.pop(sess.id, None)
