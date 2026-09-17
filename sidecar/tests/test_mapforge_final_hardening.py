"""Deterministic regressions for the MapForge hardening findings."""
import threading
import time
import os
import subprocess
import sys
import asyncio
from contextlib import contextmanager, nullcontext
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

import routes.mapforge as mf
from mercwizard_core.cross_lock import (
    acquire_writable_map_session_lease, normalized_install_root_lock_scope,
)
from mercwizard_core.mapforge_engine.dat_writer import build_empty_dat_bytes
from mercwizard_core.mapforge_engine.parse_dat_ext import parse_dat_full
from routes.mapforge import (
    AppendixModelBody, MapForgeSession, _session_store, close_session,
    OpenSessionBody, open_session, save_session, set_appendix_model, session_appendix,
)


def _state(root, ident="profile-a"):
    return SimpleNamespace(active=lambda: SimpleNamespace(id=ident, path=str(root)),
                           write_lock=threading.RLock())


def _session(tmp_path, sid="s"):
    root = tmp_path / "install"
    path = root / "Data-1.13" / "Maps" / "A1.dat"
    path.parent.mkdir(parents=True)
    path.write_bytes(build_empty_dat_bytes(tileset=71))
    xml = root / "Data-1.13" / "Ja2Set.dat.xml"
    xml.write_text("<tilesets/>", encoding="utf-8")
    sess = MapForgeSession(path, xml, 71,
                           install_id="profile-a", install_root=root)
    sess.id = sid
    _session_store._sessions[sid] = sess
    return sess, root


@pytest.fixture(autouse=True)
def _locks(monkeypatch):
    monkeypatch.setattr(mf, "cross_process_install_lock", lambda _root: nullcontext())
    monkeypatch.setattr(
        mf, "acquire_writable_map_session_lease",
        lambda *_args: SimpleNamespace(release=lambda: None),
    )
    monkeypatch.setattr(mf, "_iso_renderer_available", True)


def test_authored_appendix_rebases_offset_for_get_and_second_save(tmp_path, monkeypatch):
    sess, root = _session(tmp_path, "appendix")
    monkeypatch.setattr(mf, "get_state", lambda: _state(root))
    try:
        set_appendix_model(sess.id, AppendixModelBody(
            ambient={"basement": 0, "caves": 0, "level": 12},
            exit_grids=[{"map_index": 10, "grid_no": 11, "sx": 1, "sy": 1, "sz": 0}],
        ))
        save_session(sess.id)
        first = sess.dat_path.read_bytes()
        assert len(first) - sess.parsed["appendix_offset"] == 49
        assert session_appendix(sess.id).exit_grids[0].gridno == 10
        sess.dirty = True
        save_session(sess.id)
        assert sess.dat_path.read_bytes() == first
    finally:
        _session_store._sessions.pop(sess.id, None)


def test_ordinary_save_rejects_active_a_to_b_switch_without_writing_a(tmp_path, monkeypatch):
    sess, root = _session(tmp_path, "switch")
    before = sess.dat_path.read_bytes()
    monkeypatch.setattr(mf, "get_state", lambda: _state(root, "profile-b"))
    try:
        sess.parsed["heights"][0] = 7
        sess.dirty = True
        with pytest.raises(HTTPException) as exc:
            save_session(sess.id)
        assert exc.value.detail["error"] == "SESSION_INSTALL_CHANGED"
        assert sess.dat_path.read_bytes() == before
    finally:
        _session_store._sessions.pop(sess.id, None)


def test_ordinary_save_rejects_when_no_install_is_active(tmp_path, monkeypatch):
    sess, _root = _session(tmp_path, "inactive-save")
    before = sess.dat_path.read_bytes()
    monkeypatch.setattr(
        mf, "get_state",
        lambda: SimpleNamespace(active=lambda: None, write_lock=threading.RLock()),
    )
    try:
        sess.parsed["heights"][0] = 7
        sess.dirty = True
        with pytest.raises(HTTPException) as exc:
            save_session(sess.id)
        assert exc.value.status_code == 409
        assert exc.value.detail["error"] == "SESSION_INSTALL_CHANGED"
        assert sess.dat_path.read_bytes() == before
    finally:
        _session_store._sessions.pop(sess.id, None)


def test_missing_source_requires_force_to_recreate(tmp_path, monkeypatch):
    sess, root = _session(tmp_path, "missing")
    monkeypatch.setattr(mf, "get_state", lambda: _state(root))
    sess.dat_path.unlink()
    try:
        with pytest.raises(HTTPException) as exc:
            save_session(sess.id)
        assert exc.value.detail["error"] == "EXTERNAL_MODIFICATION"
        save_session(sess.id, force=True)
        assert sess.dat_path.is_file()
    finally:
        _session_store._sessions.pop(sess.id, None)


def test_same_physical_root_profiles_have_one_cross_process_scope(tmp_path):
    root = tmp_path / "install"
    assert normalized_install_root_lock_scope(root) == normalized_install_root_lock_scope(root.resolve())


def test_close_releases_writable_lease_after_recovery_delete(tmp_path, monkeypatch):
    """A close removes recovery before lease release, so a fresh open may own it."""
    sess, root = _session(tmp_path, "close")
    monkeypatch.setattr(mf, "get_state", lambda: _state(root))
    released = []
    sess.map_lease = SimpleNamespace(release=lambda: released.append(True))
    rec, meta = mf._recovery_paths(sess.dat_path)
    rec.parent.mkdir(parents=True, exist_ok=True)
    rec.write_bytes(b"stale")
    meta.write_text("{}", encoding="utf-8")
    assert close_session(sess.id) == {"closed": sess.id}
    assert not rec.exists() and not meta.exists()
    assert released == [True]


@pytest.mark.parametrize("metadata", [[], {"saved_at": "later", "edit_count": 1},
                                        {"saved_at": 1.0, "edit_count": "one"}])
def test_malformed_recovery_metadata_is_deleted_without_open_failure(tmp_path, metadata):
    sess, _ = _session(tmp_path, "malformed")
    rec, meta = mf._recovery_paths(sess.dat_path)
    rec.parent.mkdir(parents=True, exist_ok=True)
    rec.write_bytes(sess.original_bytes + b"x")
    import json
    meta.write_text(json.dumps(metadata), encoding="utf-8")
    try:
        assert mf._recovery_offer(sess.dat_path, sess.original_bytes, "profile-a") is None
        assert not rec.exists() and not meta.exists()
    finally:
        _session_store._sessions.pop(sess.id, None)


def test_public_open_evicts_clean_idle_same_map_before_lease_admission(tmp_path, monkeypatch):
    old, root = _session(tmp_path, "idle-old")
    released = []
    old.map_lease = SimpleNamespace(release=lambda: released.append("old"))
    old.last_used_at = time.time() - mf._SESSION_IDLE_TIMEOUT - 1
    monkeypatch.setattr(mf, "get_state", lambda: _state(root))
    acquired = []
    class Lease:
        def release(self):
            released.append("new")
    monkeypatch.setattr(mf, "acquire_writable_map_session_lease",
                        lambda *_: acquired.append(True) or Lease())
    info = open_session(OpenSessionBody(dat=str(old.dat_path),
                                        xml=str(old.xml_path), tileset=71))
    try:
        assert released == ["old"] and acquired == [True]
        assert info.session_id != old.id
    finally:
        close_session(info.session_id)


def test_real_second_process_map_lease_fails_promptly_then_releases(tmp_path, monkeypatch):
    """Portalocker's non-blocking flag is exercised across real processes."""
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    root, target = tmp_path / "install", tmp_path / "install" / "A1.dat"
    root.mkdir()
    child = (
        "from mercwizard_core.cross_lock import acquire_writable_map_session_lease as a; "
        f"x=a(r'{root}', r'{target}'); print('READY', flush=True); import sys; sys.stdin.read(); x.release()"
    )
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    proc = subprocess.Popen([sys.executable, "-c", child], stdin=subprocess.PIPE,
                            stdout=subprocess.PIPE, text=True,
                            env=os.environ.copy(), creationflags=flags)
    try:
        assert proc.stdout.readline().strip() == "READY"
        started = time.monotonic()
        with pytest.raises(RuntimeError):
            acquire_writable_map_session_lease(root, target)
        assert time.monotonic() - started < 1.0
        proc.stdin.close()
        assert proc.wait(timeout=5) == 0
        lease = acquire_writable_map_session_lease(root, target)
        lease.release()
    finally:
        if proc.poll() is None:
            proc.terminate()
            proc.wait(timeout=5)


def test_borrow_pins_clean_session_during_idle_eviction(tmp_path):
    sess, _root = _session(tmp_path, "borrowed-idle")
    released = []
    sess.map_lease = SimpleNamespace(release=lambda: released.append("old"))
    sess.last_used_at = time.time() - mf._SESSION_IDLE_TIMEOUT - 1
    try:
        with _session_store.borrow(sess.id) as borrowed:
            assert borrowed is sess
            assert sess.borrow_count == 1
            _session_store.evict_idle()
            assert sess.id in _session_store._sessions
            assert released == []
        assert sess.borrow_count == 0
        sess.last_used_at = time.time() - mf._SESSION_IDLE_TIMEOUT - 1
        _session_store.evict_idle()
        assert sess.id not in _session_store._sessions
        assert released == ["old"]
    finally:
        _session_store._sessions.pop(sess.id, None)


def test_capacity_admission_rejects_all_borrowed_sessions_without_overflow(tmp_path, monkeypatch):
    old, root = _session(tmp_path, "borrowed-cap")
    released = []
    old.map_lease = SimpleNamespace(release=lambda: released.append("old"))
    new_path = root / "Data-1.13" / "Maps" / "B1.dat"
    new_path.write_bytes(old.dat_path.read_bytes())
    new_lease = SimpleNamespace(release=lambda: released.append("new"))
    monkeypatch.setattr(mf, "_MAX_SESSIONS", 1)
    try:
        with _session_store.borrow(old.id):
            with pytest.raises(mf.SessionAdmissionError):
                _session_store.open(new_path, old.xml_path, 71,
                                    install_id="profile-a", install_root=root,
                                    map_lease=new_lease)
            assert set(_session_store._sessions) == {old.id}
            assert released == ["new"]
        admitted = _session_store.open(
            new_path, old.xml_path, 71,
            install_id="profile-a", install_root=root,
        )
        assert set(_session_store._sessions) == {admitted.id}
        assert released == ["new", "old"]
    finally:
        _session_store._sessions.pop(old.id, None)
        if "admitted" in locals():
            _session_store._sessions.pop(admitted.id, None)


def test_capacity_zero_rejects_and_releases_new_lease_once(tmp_path, monkeypatch):
    old, root = _session(tmp_path, "cap-zero-source")
    new_path = root / "Data-1.13" / "Maps" / "C1.dat"
    new_path.write_bytes(old.dat_path.read_bytes())
    released = []
    new_lease = SimpleNamespace(release=lambda: released.append("new"))
    monkeypatch.setattr(mf, "_MAX_SESSIONS", 0)
    try:
        _session_store._sessions.pop(old.id, None)
        with pytest.raises(mf.SessionAdmissionError):
            _session_store.open(new_path, old.xml_path, 71,
                                install_id="profile-a", install_root=root,
                                map_lease=new_lease)
        assert _session_store._sessions == {}
        assert released == ["new"]
    finally:
        _session_store._sessions.pop(old.id, None)


def test_negative_capacity_rejects_without_evicting_existing_session(tmp_path, monkeypatch):
    old, root = _session(tmp_path, "cap-negative-source")
    released = []
    old.map_lease = SimpleNamespace(release=lambda: released.append("old"))
    new_path = root / "Data-1.13" / "Maps" / "D1.dat"
    new_path.write_bytes(old.dat_path.read_bytes())
    new_lease = SimpleNamespace(release=lambda: released.append("new"))
    monkeypatch.setattr(mf, "_MAX_SESSIONS", -1)
    try:
        with pytest.raises(mf.SessionAdmissionError):
            _session_store.open(new_path, old.xml_path, 71,
                                install_id="profile-a", install_root=root,
                                map_lease=new_lease)
        assert set(_session_store._sessions) == {old.id}
        assert old.closed is False
        assert released == ["new"]
    finally:
        _session_store._sessions.pop(old.id, None)


def test_public_open_capacity_zero_releases_acquired_lease_once(tmp_path, monkeypatch):
    source, root = _session(tmp_path, "public-cap-zero")
    released = []
    monkeypatch.setattr(mf, "get_state", lambda: _state(root))
    monkeypatch.setattr(mf, "_MAX_SESSIONS", 0)
    monkeypatch.setattr(
        mf, "acquire_writable_map_session_lease",
        lambda *_: SimpleNamespace(release=lambda: released.append("new")),
    )
    try:
        _session_store._sessions.pop(source.id, None)
        with pytest.raises(HTTPException) as exc:
            open_session(OpenSessionBody(
                dat=str(source.dat_path), xml=str(source.xml_path), tileset=71,
            ))
        assert exc.value.status_code == 409
        assert exc.value.detail["error"] == "SESSION_CAPACITY"
        assert released == ["new"]
        assert _session_store._sessions == {}
    finally:
        _session_store._sessions.pop(source.id, None)


def test_close_rejects_busy_borrow_and_closes_after_release(tmp_path, monkeypatch):
    sess, root = _session(tmp_path, "close-borrow")
    released = []
    sess.map_lease = SimpleNamespace(release=lambda: released.append("lease"))
    monkeypatch.setattr(mf, "get_state", lambda: _state(root))
    try:
        with _session_store.borrow(sess.id):
            with pytest.raises(HTTPException) as exc:
                close_session(sess.id)
            assert exc.value.status_code == 409
            assert exc.value.detail["error"] == "SESSION_BUSY"
            assert sess.id in _session_store._sessions
            assert released == []
        assert close_session(sess.id) == {"closed": sess.id}
        assert sess.id not in _session_store._sessions
        assert released == ["lease"]
    finally:
        _session_store._sessions.pop(sess.id, None)


def test_streaming_endpoint_holds_borrow_until_body_consumed(tmp_path):
    sess, _root = _session(tmp_path, "stream-borrow")

    @mf._borrow_session_endpoint
    def stream_endpoint(session_id):
        borrowed = _session_store.borrowed(session_id)

        async def body():
            yield str(borrowed.borrow_count).encode("ascii")

        return mf.StreamingResponse(body(), media_type="text/plain")

    try:
        response = stream_endpoint(sess.id)
        assert sess.borrow_count == 1

        async def consume():
            return [chunk async for chunk in response.body_iterator]

        assert asyncio.run(consume()) == [b"1"]
        assert sess.borrow_count == 0
    finally:
        _session_store._sessions.pop(sess.id, None)


def test_borrowed_requires_current_endpoint_access_context(tmp_path):
    sess, _root = _session(tmp_path, "borrow-authority")
    try:
        with _session_store.borrow(sess.id):
            with pytest.raises(RuntimeError, match="current endpoint borrow"):
                _session_store.borrowed(sess.id)

        @mf._borrow_session_endpoint
        def endpoint(session_id):
            return _session_store.borrowed(session_id).id

        assert endpoint(sess.id) == sess.id
        assert sess.borrow_count == 0
    finally:
        _session_store._sessions.pop(sess.id, None)


def test_copy_as_confines_only_after_a_to_b_to_a_transaction_recheck(tmp_path, monkeypatch):
    sess, root_a = _session(tmp_path, "copy-barrier")
    root_b = tmp_path / "install-b"
    root_b.mkdir()
    current = {"value": SimpleNamespace(id="profile-a", path=str(root_a))}
    state = SimpleNamespace(active=lambda: current["value"], write_lock=threading.RLock())
    monkeypatch.setattr(mf, "get_state", lambda: state)
    observed = []
    @contextmanager
    def barrier(_root):
        current["value"] = SimpleNamespace(id="profile-b", path=str(root_b))
        current["value"] = SimpleNamespace(id="profile-a", path=str(root_a))
        yield
    monkeypatch.setattr(mf, "cross_process_install_lock", barrier)
    real_confine = mf._confine_install_path
    def confined(raw):
        observed.append(current["value"].path)
        return real_confine(raw)
    monkeypatch.setattr(mf, "_confine_install_path", confined)
    from routes.mapforge import SaveCopyAsBody, save_copy_as
    dest = root_a / "Data-1.13" / "Maps" / "copy.dat"
    try:
        save_copy_as(sess.id, SaveCopyAsBody(dat_path=str(dest)))
        assert observed == [str(root_a)]
        assert dest.is_file()
    finally:
        _session_store._sessions.pop(sess.id, None)


def test_radar_rejects_active_install_switch_before_any_write(tmp_path, monkeypatch):
    """Radar must not write a captured root after A changes to B."""
    root_a = tmp_path / "install-a"
    root_b = tmp_path / "install-b"
    xml = root_a / "Data-1.13" / "Ja2Set.dat.xml"
    dat = root_a / "Data-1.13" / "Maps" / "A1.dat"
    xml.parent.mkdir(parents=True)
    dat.parent.mkdir(parents=True)
    xml.write_text("<tilesets />", encoding="utf-8")
    dat.write_bytes(b"dat")
    current = {"value": SimpleNamespace(id="a", path=str(root_a))}
    state = SimpleNamespace(active=lambda: current["value"], write_lock=threading.RLock())
    events: list[str] = []

    @contextmanager
    def barrier(_root):
        events.append("root-lock")
        current["value"] = SimpleNamespace(id="b", path=str(root_b))
        yield

    monkeypatch.setattr(mf, "get_state", lambda: state)
    monkeypatch.setattr(mf, "cross_process_install_lock", barrier)
    monkeypatch.setattr(mf, "_iso_renderer_available", True)
    writes: list[Path] = []
    monkeypatch.setattr(mf, "write_bytes_atomic", lambda path, data: writes.append(path))
    monkeypatch.setattr(mf, "_resolve_dat_path", lambda _raw: dat)

    with pytest.raises(HTTPException) as exc:
        mf.sector_radar(str(dat), str(xml), 71)

    assert exc.value.status_code == 409
    assert exc.value.detail["error"] in {"ACTIVE_INSTALL_CHANGED", "SESSION_INSTALL_CHANGED"}
    assert writes == []
    assert events == ["root-lock"]


def test_radar_keeps_captured_root_through_a_to_b_to_a_barrier(tmp_path, monkeypatch):
    """A transient active switch cannot make radar resolve a second root."""
    root = tmp_path / "install-a"
    xml = root / "Data-1.13" / "Ja2Set.dat.xml"
    dat = root / "Data-1.13" / "Maps" / "A1.dat"
    out = root / "Profiles" / "User" / "RADARMAPS" / "A1.STI"
    xml.parent.mkdir(parents=True)
    dat.parent.mkdir(parents=True)
    xml.write_text("<tilesets />", encoding="utf-8")
    dat.write_bytes(b"dat")
    current = {"value": SimpleNamespace(id="a", path=str(root))}
    state = SimpleNamespace(active=lambda: current["value"], write_lock=threading.RLock())
    events: list[str] = []

    @contextmanager
    def barrier(_root):
        events.append("root-lock")
        current["value"] = SimpleNamespace(id="b", path=str(tmp_path / "install-b"))
        current["value"] = SimpleNamespace(id="a", path=str(root))
        yield

    class Layout:
        def resolve_override_write(self, _name):
            return out

        def engine_write_profile(self):
            return SimpleNamespace(profile_root=out.parent)

    monkeypatch.setattr(mf, "get_state", lambda: state)
    monkeypatch.setattr(mf, "cross_process_install_lock", barrier)
    monkeypatch.setattr(mf, "parse_vfs_config", lambda _root: Layout())
    monkeypatch.setattr(mf, "_resolve_dat_path", lambda _raw: dat)
    monkeypatch.setattr(mf, "render_radar_image", lambda *_args: None, raising=False)
    monkeypatch.setattr(mf, "_radarmaps_slf_has", lambda *_args: False)
    monkeypatch.setattr(mf, "write_radar_sti", lambda _img, path: path.write_bytes(b"radar"), raising=False)
    monkeypatch.setattr(mf, "decode_sti_frame_to_png", lambda *_args: b"png", raising=False)

    # The route imports the radar helpers locally; patch their module owner.
    import mercwizard_core.mapforge_engine.radar as radar
    import mercwizard_core.sti_decode as sti_decode
    monkeypatch.setattr(radar, "render_radar_image", lambda *_args: radar.Image.new("RGBA", (88, 44)))
    def write_radar(_img, path):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"radar")
    monkeypatch.setattr(radar, "write_radar_sti", write_radar)
    monkeypatch.setattr(sti_decode, "decode_sti_frame_to_png", lambda *_args: b"png")

    result = mf.sector_radar(str(dat), str(xml), 71)

    assert Path(result.output_path) == out
    assert out.read_bytes() == b"radar"
    assert events == ["root-lock"]


def test_radar_backups_are_namespaced_by_physical_install(tmp_path):
    root_a = tmp_path / "install-a"
    root_b = tmp_path / "install-b"
    assert mf._radar_backup_path(root_a, "A1") != mf._radar_backup_path(root_b, "A1")
    assert mf._radar_backup_path(root_a, "A1") == mf._radar_backup_path(root_a.resolve(), "A1")


@pytest.mark.parametrize(
    "prior, decoder_failure",
    [(b"exact-prior-radar", False), (None, True)],
)
def test_radar_unverified_write_restores_prior_bytes_or_removes_new_output(
    tmp_path, monkeypatch, prior, decoder_failure,
):
    """A failed post-write verification must not leave a corrupt override live."""
    monkeypatch.setattr(mf, "_RADAR_BACKUP_DIR", tmp_path / "radar-backups")
    root = tmp_path / "install"
    dat = root / "Data-1.13" / "Maps" / "A1.dat"
    xml = root / "Data-1.13" / "Ja2Set.dat.xml"
    out = root / "Profiles" / "User" / "RADARMAPS" / "A1.STI"
    dat.parent.mkdir(parents=True)
    dat.write_bytes(b"source-map")
    xml.write_text("<tilesets />", encoding="utf-8")
    if prior is not None:
        out.parent.mkdir(parents=True)
        out.write_bytes(prior)

    class Layout:
        def resolve_override_write(self, _name):
            return out

        def engine_write_profile(self):
            return SimpleNamespace(profile_root=out.parent)

    monkeypatch.setattr(mf, "get_state", lambda: _state(root))
    monkeypatch.setattr(mf, "parse_vfs_config", lambda _root: Layout())
    monkeypatch.setattr(mf, "_install_tileset_paths", lambda _root: ([], []))
    monkeypatch.setattr(mf, "_radarmaps_slf_has", lambda *_args: False)
    import mercwizard_core.mapforge_engine.radar as radar
    import mercwizard_core.sti_decode as sti_decode
    monkeypatch.setattr(radar, "render_radar_image", lambda *_args: radar.Image.new("RGBA", (88, 44)))
    monkeypatch.setattr(
        radar, "write_radar_sti",
        lambda _img, path: (path.parent.mkdir(parents=True, exist_ok=True), path.write_bytes(b"corrupt")),
    )
    def unverified_decoder(*_args):
        if decoder_failure:
            raise RuntimeError("corrupt STI decoder path")
        return None
    monkeypatch.setattr(sti_decode, "decode_sti_frame_to_png", unverified_decoder)

    with pytest.raises(HTTPException) as exc:
        mf.sector_radar(str(dat), str(xml), 71)

    assert exc.value.detail["error"] == "RADAR_WRITE_UNVERIFIED"
    if prior is None:
        assert not out.exists()
    else:
        assert out.read_bytes() == prior


def test_jsd_writer_rejects_active_install_switch_before_backup(tmp_path, monkeypatch):
    """JSD backup/write must not begin when the active install changes."""
    root_a = tmp_path / "install-a"
    root_b = tmp_path / "install-b"
    xml = root_a / "Data-1.13" / "Ja2Set.dat.xml"
    jsd = root_a / "Data-1.13" / "Tilesets" / "71" / "rock.jsd"
    xml.parent.mkdir(parents=True)
    jsd.parent.mkdir(parents=True)
    xml.write_text("<tilesets />", encoding="utf-8")
    original = bytes(range(64))
    jsd.write_bytes(original)
    current = {"value": SimpleNamespace(id="a", path=str(root_a))}
    state = SimpleNamespace(active=lambda: current["value"], write_lock=threading.RLock())

    @contextmanager
    def barrier(_root):
        current["value"] = SimpleNamespace(id="b", path=str(root_b))
        yield

    monkeypatch.setattr(mf, "get_state", lambda: state)
    monkeypatch.setattr(mf, "cross_process_install_lock", barrier)
    monkeypatch.setattr(mf, "load_tileset_xml", lambda *_args: {0: "rock.sti"})
    monkeypatch.setattr(mf, "_find_jsd_bytes", lambda *_args: (original, str(jsd)))
    monkeypatch.setattr(mf, "_parse_jsd_bytes", lambda *_args: SimpleNamespace(ubNumberOfTiles=1))

    with pytest.raises(HTTPException) as exc:
        mf.update_sti_jsd(mf.JsdEditBody(xml=str(xml), tileset=71, slot=0, ubArmour=1))

    assert exc.value.status_code == 409
    assert not jsd.with_suffix(".jsd.bak").exists()
    assert jsd.read_bytes() == original


def test_jsd_writer_verifies_disk_bytes_and_restores_on_mismatch(tmp_path, monkeypatch):
    root = tmp_path / "install"
    xml = root / "Data-1.13" / "Ja2Set.dat.xml"
    jsd = root / "Data-1.13" / "Tilesets" / "71" / "rock.jsd"
    xml.parent.mkdir(parents=True)
    jsd.parent.mkdir(parents=True)
    xml.write_text("<tilesets />", encoding="utf-8")
    original = bytes(range(64))
    jsd.write_bytes(original)
    monkeypatch.setattr(mf, "load_tileset_xml", lambda *_args: {0: "rock.sti"})
    monkeypatch.setattr(mf, "_find_jsd_bytes", lambda *_args: (original, str(jsd)))
    monkeypatch.setattr(
        mf, "_parse_jsd_bytes",
        lambda data, *_args: SimpleNamespace(ubNumberOfTiles=1, data=data),
    )
    real_atomic = mf.write_bytes_atomic
    corrupted = False

    def corrupt_first_live_write(path, data):
        nonlocal corrupted
        if Path(path) == jsd and not corrupted:
            corrupted = True
            real_atomic(Path(path), b"x" * len(data))
        else:
            real_atomic(Path(path), data)

    monkeypatch.setattr(mf, "write_bytes_atomic", corrupt_first_live_write)
    with pytest.raises(HTTPException) as exc:
        mf._update_sti_jsd_locked(
            mf.JsdEditBody(xml=str(xml), tileset=71, slot=0, ubArmour=1), root,
        )

    assert exc.value.status_code == 500
    assert exc.value.detail["error"] == "JSD_WRITE_UNVERIFIED"
    assert corrupted is True
    assert jsd.read_bytes() == original
