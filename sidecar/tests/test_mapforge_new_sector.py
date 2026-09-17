"""R6 "New sector" + "Save a copy as…" — create / clone sectors.

build_empty_dat_bytes synthesizes a fresh empty .dat (no original bytes to
pass through); these tests pin that the result round-trips byte-exactly
through parse_dat_full + write_dat_bytes (so it loads in the editor) and
that the two endpoints behave: new-sector refuses to clobber without
overwrite, save-copy-as writes a NEW file and never touches the original.
"""
import threading
from contextlib import contextmanager, nullcontext
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace

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
    open_session,
    OpenSessionBody,
    save_copy_as,
)
from tests.test_mapforge_library import _build_minimal_dat

from fastapi import HTTPException


_TEST_INSTALL_ID = "new-sector-test-install"


@pytest.fixture(autouse=True)
def _active_install_is_tmp(tmp_path, monkeypatch):
    """Model the active install that a real opened session captures."""
    state = SimpleNamespace(
        active=lambda: SimpleNamespace(id=_TEST_INSTALL_ID, path=str(tmp_path)),
        write_lock=threading.RLock(),
    )
    monkeypatch.setattr(mf, "get_state", lambda: state)
    monkeypatch.setattr(mf, "_active_install_root", lambda: tmp_path)
    # Route behavior is under test here; the substituted runtime lacks the
    # pywin32 backend used by portalocker's production lock.
    monkeypatch.setattr(
        mf, "cross_process_install_lock", lambda _install_id: nullcontext(),
    )
    monkeypatch.setattr(
        mf, "acquire_writable_map_session_lease",
        lambda *_args: SimpleNamespace(release=lambda: None),
    )


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


def test_empty_dat_map_version_clears_engine_minimum():
    """The MAPCREATE tail's ubMapVersion must be >= 15 — the engine's
    UpdateOldVersionMap asserts 'Map is less than minimum supported
    version' below that, and Will's builds run asserts-on. The engine
    stamps ubMapVersion = minor on save (Map Information.cpp:154), so
    the synthesized map mirrors it. Regression: the tail used to be all
    zeros → ubMapVersion=0 → assert on first tactical load."""
    from mercwizard_core.mapforge_engine.validate import validate_parsed

    data = build_empty_dat_bytes(tileset=71)
    d = parse_dat_full(data, "<empty>")
    assert d["tail"]["ubMapVersion"] == d["minor"] == 31
    codes = {f.code for f in validate_parsed(d)}
    assert "MAPVERSION_TOO_LOW" not in codes


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


def test_new_sector_overwrite_keeps_recoverable_destination_backup(tmp_path, monkeypatch):
    """An explicit replace still preserves the exact map it displaces."""
    monkeypatch.setattr(mf, "_iso_renderer_available", True)
    monkeypatch.setattr(mf, "_DAT_BACKUP_DIR", tmp_path / "backups")
    dest = tmp_path / "A1.dat"
    previous = _build_minimal_dat(land={0: [(2, 2)]})
    dest.write_bytes(previous)

    new_sector(NewSectorBody(dat_path=str(dest), tileset=7, overwrite=True))

    backup_dir = mf._session_backup_dir(dest)
    assert any(p.read_bytes() == previous for p in backup_dir.glob("*.dat"))


def test_new_sector_overwrite_fails_closed_when_destination_backup_fails(tmp_path, monkeypatch):
    """Never replace an existing sector when its recovery copy cannot be written."""
    monkeypatch.setattr(mf, "_iso_renderer_available", True)
    monkeypatch.setattr(mf, "_DAT_BACKUP_DIR", tmp_path / "backups")
    dest = tmp_path / "A1.dat"
    previous = b"existing-map-bytes"
    dest.write_bytes(previous)
    real_atomic = mf.write_bytes_atomic

    def fail_backup(path, data):
        if mf._session_backup_dir(dest) in Path(path).parents:
            raise OSError("backup volume unavailable")
        real_atomic(path, data)

    monkeypatch.setattr(mf, "write_bytes_atomic", fail_backup)
    with pytest.raises(HTTPException) as exc:
        new_sector(NewSectorBody(dat_path=str(dest), tileset=7, overwrite=True))

    assert exc.value.detail["error"] == "BACKUP_FAILED"
    assert dest.read_bytes() == previous


def test_new_sector_rejects_destination_with_active_writable_session(tmp_path, monkeypatch):
    """Creating a map cannot race a session that already owns that pathname."""
    monkeypatch.setattr(mf, "_iso_renderer_available", True)
    dest = tmp_path / "A1.dat"
    dest.write_bytes(b"session-owned")
    monkeypatch.setattr(
        mf, "acquire_writable_map_session_lease",
        lambda *_args: (_ for _ in ()).throw(RuntimeError("active writer")),
    )

    with pytest.raises(HTTPException) as exc:
        new_sector(NewSectorBody(dat_path=str(dest), tileset=7, overwrite=True))

    assert exc.value.status_code == 409
    assert exc.value.detail["error"] == "WRITABLE_SESSION_EXISTS"
    assert dest.read_bytes() == b"session-owned"


def test_new_sector_holds_install_transaction_against_extraction(tmp_path, monkeypatch):
    """A no-clobber create owns its check through its atomic write.

    The barrier makes the old exists()-then-write implementation lose: SLF
    extraction writes the destination while new-sector is paused after its
    initial check, then new-sector silently replaces it.  With the shared
    install transaction, extraction cannot reach its writer until new-sector
    completes, and it then refuses the newly-created loose file.
    """
    monkeypatch.setattr(mf, "_iso_renderer_available", True)
    install_root = tmp_path
    archive = install_root / "Data-1.13" / "Maps.slf"
    archive.parent.mkdir(parents=True)
    archive.write_bytes(b"fixture")
    dest = install_root / "Data-1.13" / "Maps" / "SLF_A1.dat"
    build_entered = threading.Event()
    release_build = threading.Event()
    extract_wrote = threading.Event()
    install_lock = threading.Lock()
    real_build = mf.build_empty_dat_bytes
    real_write = mf.write_bytes_atomic

    def pause_new_sector_build(*args, **kwargs):
        build_entered.set()
        assert release_build.wait(5)
        return real_build(*args, **kwargs)

    @contextmanager
    def controlled_install_lock(_install_id):
        with install_lock:
            yield

    def observe_write(path, data):
        if path == dest and data == b"from-slf":
            extract_wrote.set()
        real_write(path, data)

    import importlib
    slf_module = importlib.import_module("ja2py.fileformats.SlfFS")

    class ControlledSlf:
        def __init__(self, _path):
            pass

        def readbytes(self, _internal):
            return b"from-slf"

    monkeypatch.setattr(mf, "build_empty_dat_bytes", pause_new_sector_build)
    monkeypatch.setattr(mf, "cross_process_install_lock", controlled_install_lock)
    monkeypatch.setattr(mf, "write_bytes_atomic", observe_write)
    monkeypatch.setattr(slf_module, "SlfFS", ControlledSlf)
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            new_future = pool.submit(
                new_sector, NewSectorBody(dat_path=str(dest), tileset=7),
            )
            assert build_entered.wait(5)
            extract_future = pool.submit(
                mf.extract_slf_to_loose,
                mf.ExtractSlfMapBody(slf_uri=f"slf://{archive}!/SLF_A1.dat"),
            )
            assert not extract_wrote.wait(0.25)
            release_build.set()
            new_future.result(timeout=5)
            with pytest.raises(HTTPException) as exc:
                extract_future.result(timeout=5)
        assert exc.value.status_code == 409
        assert exc.value.detail["error"] == "LOOSE_EXISTS"
        assert parse_dat_full(dest.read_bytes(), str(dest))["tileset"] == 7
    finally:
        release_build.set()


def test_new_sector_refuses_active_install_switch_inside_transaction(
    tmp_path, monkeypatch,
):
    """A path validated for A must never be written after A -> B switches."""
    monkeypatch.setattr(mf, "_iso_renderer_available", True)
    install_a = tmp_path / "install-a"
    install_b = tmp_path / "install-b"
    install_a.mkdir()
    install_b.mkdir()
    current = {"info": SimpleNamespace(id="install-a", path=str(install_a))}
    state = SimpleNamespace(
        active=lambda: current["info"], write_lock=threading.RLock(),
    )
    monkeypatch.setattr(mf, "get_state", lambda: state)

    @contextmanager
    def switch_on_lock(_install_id):
        current["info"] = SimpleNamespace(id="install-b", path=str(install_b))
        yield

    monkeypatch.setattr(mf, "cross_process_install_lock", switch_on_lock)
    dest = install_a / "Data-1.13" / "Maps" / "A1.dat"
    with pytest.raises(HTTPException) as exc:
        new_sector(NewSectorBody(dat_path=str(dest), tileset=7))
    assert exc.value.status_code == 409
    assert exc.value.detail["error"] == "ACTIVE_INSTALL_CHANGED"
    assert not dest.exists()


def test_open_session_refuses_active_switch_before_session_creation(
    tmp_path, monkeypatch,
):
    """Opening under A cannot bind A paths while B becomes active."""
    monkeypatch.setattr(mf, "_iso_renderer_available", True)
    install_a = tmp_path / "install-a"
    install_b = tmp_path / "install-b"
    dat_path = install_a / "Data-1.13" / "Maps" / "A1.dat"
    xml_path = install_a / "Data-1.13" / "Ja2Set.dat.xml"
    dat_path.parent.mkdir(parents=True)
    dat_path.write_bytes(_build_minimal_dat(land={0: [(1, 1)]}))
    xml_path.write_text("<tilesets/>", encoding="utf-8")
    install_b.mkdir()
    current = {"info": SimpleNamespace(id="install-a", path=str(install_a))}
    state = SimpleNamespace(
        active=lambda: current["info"], write_lock=threading.RLock(),
    )
    monkeypatch.setattr(mf, "get_state", lambda: state)

    @contextmanager
    def switch_on_lock(_install_id):
        current["info"] = SimpleNamespace(id="install-b", path=str(install_b))
        yield

    monkeypatch.setattr(mf, "cross_process_install_lock", switch_on_lock)
    with pytest.raises(HTTPException) as exc:
        open_session(OpenSessionBody(
            dat=str(dat_path), xml=str(xml_path), tileset=7,
        ))
    assert exc.value.status_code == 409
    assert exc.value.detail["error"] == "ACTIVE_INSTALL_CHANGED"
    assert _session_store.list_all() == []


def test_new_sector_rejects_non_dat_suffix(tmp_path, monkeypatch):
    monkeypatch.setattr(mf, "_iso_renderer_available", True)
    with pytest.raises(HTTPException) as exc:
        new_sector(NewSectorBody(dat_path=str(tmp_path / "x.txt"), tileset=1))
    assert exc.value.status_code == 400


@pytest.mark.parametrize("tileset", [-1, 2**32])
def test_new_sector_rejects_tileset_that_would_wrap_uint32(tmp_path, monkeypatch, tileset):
    monkeypatch.setattr(mf, "_iso_renderer_available", True)
    with pytest.raises(HTTPException) as exc:
        new_sector(NewSectorBody(dat_path=str(tmp_path / "bad.dat"), tileset=tileset))
    assert exc.value.status_code == 400
    assert exc.value.detail["error"] == "BAD_PARAMS"


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
    sess.disk_baseline = data
    sess.dirty = True
    sess.mutation_seq = 1
    sess.autosaved_seq = 0
    sess.edit_count = 1
    sess.created_at = 0.0
    sess.last_used_at = 0.0
    sess.read_only = False
    sess.source_uri = ""
    sess.install_id = _TEST_INSTALL_ID
    sess.closed = False
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


def test_save_copy_as_overwrite_keeps_recoverable_destination_backup(tmp_path, monkeypatch):
    monkeypatch.setattr(mf, "_iso_renderer_available", True)
    monkeypatch.setattr(mf, "_DAT_BACKUP_DIR", tmp_path / "backups")
    data = _build_minimal_dat(land={0: [(1, 1)]})
    sess = _session_on(tmp_path, data, "copyas-overwrite-backup")
    dest = tmp_path / "copy" / "A9_copy.dat"
    dest.parent.mkdir(parents=True)
    previous = _build_minimal_dat(land={0: [(3, 3)]})
    dest.write_bytes(previous)
    try:
        save_copy_as(sess.id, SaveCopyAsBody(dat_path=str(dest), overwrite=True))
        backup_dir = mf._session_backup_dir(dest)
        assert any(p.read_bytes() == previous for p in backup_dir.glob("*.dat"))
    finally:
        _session_store._sessions.pop(sess.id, None)


def test_save_copy_as_overwrite_fails_closed_when_destination_backup_fails(tmp_path, monkeypatch):
    monkeypatch.setattr(mf, "_iso_renderer_available", True)
    monkeypatch.setattr(mf, "_DAT_BACKUP_DIR", tmp_path / "backups")
    data = _build_minimal_dat(land={0: [(1, 1)]})
    sess = _session_on(tmp_path, data, "copyas-backup-failure")
    dest = tmp_path / "copy" / "A9_copy.dat"
    dest.parent.mkdir(parents=True)
    previous = b"copy-as-previous"
    dest.write_bytes(previous)
    real_atomic = mf.write_bytes_atomic

    def fail_backup(path, written):
        if mf._session_backup_dir(dest) in Path(path).parents:
            raise OSError("backup volume unavailable")
        real_atomic(path, written)

    monkeypatch.setattr(mf, "write_bytes_atomic", fail_backup)
    try:
        with pytest.raises(HTTPException) as exc:
            save_copy_as(sess.id, SaveCopyAsBody(dat_path=str(dest), overwrite=True))
        assert exc.value.detail["error"] == "BACKUP_FAILED"
        assert dest.read_bytes() == previous
    finally:
        _session_store._sessions.pop(sess.id, None)


def test_save_copy_as_rejects_destination_with_active_writable_session(tmp_path, monkeypatch):
    monkeypatch.setattr(mf, "_iso_renderer_available", True)
    data = _build_minimal_dat(land={0: [(1, 1)]})
    sess = _session_on(tmp_path, data, "copyas-active-writer")
    dest = tmp_path / "copy" / "A9_copy.dat"
    dest.parent.mkdir(parents=True)
    dest.write_bytes(b"session-owned")
    monkeypatch.setattr(
        mf, "acquire_writable_map_session_lease",
        lambda *_args: (_ for _ in ()).throw(RuntimeError("active writer")),
    )
    try:
        with pytest.raises(HTTPException) as exc:
            save_copy_as(sess.id, SaveCopyAsBody(dat_path=str(dest), overwrite=True))
        assert exc.value.status_code == 409
        assert exc.value.detail["error"] == "WRITABLE_SESSION_EXISTS"
        assert dest.read_bytes() == b"session-owned"
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
