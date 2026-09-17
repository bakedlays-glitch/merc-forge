"""Crash-recovery autosave: dirty sessions snapshot to a recovery .dat
outside the install; open offers a differing snapshot back; restore swaps
the in-memory state without touching the on-disk file; save + explicit
close both clear the snapshot.

The invariant under test end-to-end: sidecar death between explicit saves
must not lose edits — the autosaver's snapshot is a complete, parseable
.dat that a NEW process can offer on the next open of the same map.
"""
import json
import hashlib
import threading
from contextlib import nullcontext
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

import routes.mapforge as mf
from mercwizard_core.mapforge_engine.parse_dat_ext import parse_dat_full
from routes.mapforge import (
    MapForgeSession,
    RecoveryActionBody,
    _session_store,
    autosave_flush_all,
    close_session,
    save_session,
    session_recovery,
)
from tests.test_mapforge_library import _build_minimal_dat


def _disk_session(tmp_path: Path, sess_id: str = "test-autosave"):
    maps_dir = tmp_path / "install" / "Data-1.13" / "Maps"
    maps_dir.mkdir(parents=True, exist_ok=True)
    dat_path = maps_dir / "A1.dat"
    data = _build_minimal_dat(land={0: [(1, 1)]})
    dat_path.write_bytes(data)
    sess = MapForgeSession.__new__(MapForgeSession)
    sess.id = sess_id
    sess.dat_path = dat_path
    sess.xml_path = tmp_path / "nonexistent.xml"
    sess.tileset = 7
    sess.parsed = parse_dat_full(data, str(dat_path))
    sess.original_bytes = data
    sess.disk_baseline = data
    sess.dirty = False
    sess.edit_count = 0
    sess.mutation_seq = 0
    sess.autosaved_seq = 0
    sess.created_at = 0.0
    sess.last_used_at = 0.0
    sess.read_only = False
    sess.source_uri = ""
    sess.baseline_findings = {}
    sess.install_id = "test-install"
    sess.closed = False
    sess._lock = threading.Lock()
    _session_store._sessions[sess.id] = sess
    return sess


def _mutate(sess):
    """A real state change: bump a height, mark dirty like the edit route."""
    sess.parsed["heights"][0] = sess.parsed["heights"][0] + 1
    sess.dirty = True
    sess.edit_count += 1
    sess.mutation_seq += 1


def _cleanup(sess):
    _session_store._sessions.pop(sess.id, None)


@pytest.fixture(autouse=True)
def _in_process_install_lock(monkeypatch, tmp_path):
    """Recovery tests exercise MapForge behavior, not pywin32 bindings."""
    monkeypatch.setattr(
        mf, "cross_process_install_lock", lambda _install_id: nullcontext(),
    )
    monkeypatch.setattr(mf, "get_state", lambda: SimpleNamespace(
        active=lambda: SimpleNamespace(
            id="test-install", path=str(tmp_path / "install"),
        ),
        write_lock=threading.RLock(),
    ))


def test_autosave_writes_parseable_snapshot(tmp_path, monkeypatch):
    monkeypatch.setattr(mf, "_DAT_BACKUP_DIR", tmp_path / "backups")
    sess = _disk_session(tmp_path)
    try:
        _mutate(sess)
        assert autosave_flush_all() == 1
        rec_dat, rec_meta = mf._recovery_paths(sess.dat_path)
        assert rec_dat.is_file() and rec_meta.is_file()
        # Snapshot parses and carries the edit; the real file is untouched.
        snap = parse_dat_full(rec_dat.read_bytes(), str(rec_dat))
        assert snap["heights"][0] == sess.parsed["heights"][0]
        assert sess.dat_path.read_bytes() == sess.disk_baseline
        meta = json.loads(rec_meta.read_text("utf-8"))
        assert meta["source_dat"] == str(sess.dat_path)
        assert meta["edit_count"] == 1
    finally:
        _cleanup(sess)


def test_autosave_skips_clean_and_unchanged(tmp_path, monkeypatch):
    monkeypatch.setattr(mf, "_DAT_BACKUP_DIR", tmp_path / "backups")
    sess = _disk_session(tmp_path)
    try:
        assert autosave_flush_all() == 0  # clean session: nothing to do
        _mutate(sess)
        assert autosave_flush_all() == 1
        # No new mutation since the last snapshot: sweep is a no-op.
        assert autosave_flush_all() == 0
        _mutate(sess)
        assert autosave_flush_all() == 1
    finally:
        _cleanup(sess)


def test_save_clears_recovery(tmp_path, monkeypatch):
    monkeypatch.setattr(mf, "_DAT_BACKUP_DIR", tmp_path / "backups")
    sess = _disk_session(tmp_path)
    try:
        _mutate(sess)
        autosave_flush_all()
        rec_dat, rec_meta = mf._recovery_paths(sess.dat_path)
        assert rec_dat.is_file()
        save_session(sess.id)
        assert not rec_dat.exists() and not rec_meta.exists()
        # Post-save the session is in sync: nothing to autosave.
        assert autosave_flush_all() == 0
    finally:
        _cleanup(sess)


def test_recovery_offer_and_restore_roundtrip(tmp_path, monkeypatch):
    """The crash scenario: snapshot exists from a dead process; a fresh
    session over the same map is offered it; restore swaps the state in
    and a subsequent save lands the recovered edit on disk."""
    monkeypatch.setattr(mf, "_DAT_BACKUP_DIR", tmp_path / "backups")
    sess = _disk_session(tmp_path)
    _mutate(sess)
    recovered_height = sess.parsed["heights"][0]
    autosave_flush_all()
    _session_store._sessions.pop(sess.id)  # simulate process death (no close)

    fresh = _disk_session(tmp_path, sess_id="fresh-after-crash")
    try:
        offer = mf._recovery_offer(fresh.dat_path, fresh.original_bytes)
        assert offer is not None and offer.edit_count == 1
        info = session_recovery(fresh.id, RecoveryActionBody(action="restore"))
        assert info.dirty is True
        assert fresh.parsed["heights"][0] == recovered_height
        # Restore did NOT touch the on-disk file...
        assert fresh.dat_path.read_bytes() == fresh.disk_baseline
        # ...and save persists the recovered state cleanly.
        save_session(fresh.id)
        on_disk = parse_dat_full(fresh.dat_path.read_bytes(),
                                 str(fresh.dat_path))
        assert on_disk["heights"][0] == recovered_height
    finally:
        _cleanup(fresh)


def test_restore_then_double_save_keeps_appendix_intact(tmp_path, monkeypatch):
    """The C6-class invariant, post-restore: original_bytes/appendix_offset
    must stay self-consistent through restore -> save -> edit -> save."""
    monkeypatch.setattr(mf, "_DAT_BACKUP_DIR", tmp_path / "backups")
    sess = _disk_session(tmp_path)
    _mutate(sess)
    autosave_flush_all()
    _session_store._sessions.pop(sess.id)

    fresh = _disk_session(tmp_path, sess_id="fresh-double-save")
    try:
        appendix_before = fresh.original_bytes[fresh.parsed["appendix_offset"]:]
        session_recovery(fresh.id, RecoveryActionBody(action="restore"))
        save_session(fresh.id)
        _mutate(fresh)
        save_session(fresh.id)
        final = fresh.dat_path.read_bytes()
        assert final[fresh.parsed["appendix_offset"]:] == appendix_before
        # And the result still parses.
        parse_dat_full(final, str(fresh.dat_path))
    finally:
        _cleanup(fresh)


def test_recovery_discard_deletes_snapshot(tmp_path, monkeypatch):
    monkeypatch.setattr(mf, "_DAT_BACKUP_DIR", tmp_path / "backups")
    sess = _disk_session(tmp_path)
    try:
        _mutate(sess)
        autosave_flush_all()
        rec_dat, _ = mf._recovery_paths(sess.dat_path)
        assert rec_dat.is_file()
        session_recovery(sess.id, RecoveryActionBody(action="discard"))
        assert not rec_dat.exists()
        assert mf._recovery_offer(sess.dat_path, sess.disk_baseline) is None
    finally:
        _cleanup(sess)


def test_offer_suppressed_when_snapshot_matches_disk(tmp_path, monkeypatch):
    """A snapshot byte-identical to the file carries nothing to restore —
    it's cleaned up rather than offered."""
    monkeypatch.setattr(mf, "_DAT_BACKUP_DIR", tmp_path / "backups")
    sess = _disk_session(tmp_path)
    try:
        rec_dat, rec_meta = mf._recovery_paths(sess.dat_path)
        rec_dat.parent.mkdir(parents=True, exist_ok=True)
        rec_dat.write_bytes(sess.disk_baseline)
        rec_meta.write_text(json.dumps({
            "source_dat": str(sess.dat_path),
            "saved_at": 1.0, "edit_count": 3, "tileset": 7,
            "disk_baseline_sha256": hashlib.sha256(sess.disk_baseline).hexdigest(),
        }), encoding="utf-8")
        assert mf._recovery_offer(sess.dat_path, sess.disk_baseline) is None
        assert not rec_dat.exists()  # cleaned up on sight
    finally:
        _cleanup(sess)


def test_stale_autosave_or_restore_cannot_resurrect_newer_save(tmp_path, monkeypatch):
    """A session based on old bytes cannot leave a recovery that overwrites
    a newer explicit save from another session."""
    monkeypatch.setattr(mf, "_DAT_BACKUP_DIR", tmp_path / "backups")
    stale = _disk_session(tmp_path, "stale-autosave")
    newer = _disk_session(tmp_path, "newer-save")
    try:
        _mutate(stale)
        stale_bytes = mf.write_dat_bytes(stale.parsed, stale.original_bytes)
        newer.parsed["heights"][0] = 99
        newer.dirty = True
        newer.mutation_seq += 1
        save_session(newer.id)
        canonical = newer.dat_path.read_bytes()

        # The stale session's baseline no longer matches canonical disk, so
        # it cannot emit a recovery snapshot after the newer save.
        assert mf._write_recovery(stale) is False
        rec_dat, rec_meta = mf._recovery_paths(stale.dat_path)
        assert not rec_dat.exists() and not rec_meta.exists()

        # A stale snapshot surviving from a crash is refused and deleted by
        # restore too; it cannot be saved back over the canonical bytes.
        rec_dat.parent.mkdir(parents=True, exist_ok=True)
        rec_dat.write_bytes(stale_bytes)
        rec_meta.write_text(json.dumps({
            "source_dat": str(stale.dat_path), "saved_at": 1.0,
            "edit_count": stale.edit_count, "tileset": stale.tileset,
            "install_id": stale.install_id,
            "disk_baseline_sha256": hashlib.sha256(
                stale.disk_baseline,
            ).hexdigest(),
        }), encoding="utf-8")
        fresh = _disk_session(tmp_path, "fresh-after-newer-save")
        try:
            # _disk_session supplies a complete session shell but starts from
            # the minimal fixture bytes; restore the actual canonical version
            # a new process would have opened after the newer save.
            fresh.dat_path.write_bytes(canonical)
            fresh.original_bytes = canonical
            fresh.disk_baseline = canonical
            fresh.parsed = parse_dat_full(canonical, str(fresh.dat_path))
            with pytest.raises(HTTPException) as exc:
                session_recovery(fresh.id, RecoveryActionBody(action="restore"))
            assert exc.value.status_code == 409
            assert exc.value.detail["error"] == "STALE_RECOVERY"
            assert fresh.dat_path.read_bytes() == canonical
            assert not rec_dat.exists() and not rec_meta.exists()
        finally:
            _cleanup(fresh)
    finally:
        _cleanup(stale)
        _cleanup(newer)


def test_restore_missing_snapshot_404s(tmp_path, monkeypatch):
    monkeypatch.setattr(mf, "_DAT_BACKUP_DIR", tmp_path / "backups")
    sess = _disk_session(tmp_path)
    try:
        with pytest.raises(HTTPException) as exc:
            session_recovery(sess.id, RecoveryActionBody(action="restore"))
        assert exc.value.status_code == 404
    finally:
        _cleanup(sess)


def test_external_guard_still_fires_after_restore(tmp_path, monkeypatch):
    """disk_baseline (not the restored recovery bytes) is the external-
    change reference: an external write after restore must still 409."""
    monkeypatch.setattr(mf, "_DAT_BACKUP_DIR", tmp_path / "backups")
    sess = _disk_session(tmp_path)
    _mutate(sess)
    autosave_flush_all()
    _session_store._sessions.pop(sess.id)

    fresh = _disk_session(tmp_path, sess_id="fresh-external")
    try:
        session_recovery(fresh.id, RecoveryActionBody(action="restore"))
        # Someone else writes the file while we sit on recovered state.
        external = bytearray(fresh.dat_path.read_bytes())
        external[-1] ^= 0xFF
        fresh.dat_path.write_bytes(bytes(external))
        with pytest.raises(HTTPException) as exc:
            save_session(fresh.id)
        assert exc.value.status_code == 409
        assert exc.value.detail["error"] == "EXTERNAL_MODIFICATION"
    finally:
        _cleanup(fresh)


def test_autosave_refuses_closed_session(tmp_path, monkeypatch):
    monkeypatch.setattr(mf, "_DAT_BACKUP_DIR", tmp_path / "backups")
    session = _disk_session(tmp_path, "closed-autosave")
    try:
        session.closed = True
        session.dirty = True
        assert mf._write_recovery(session) is False
        assert not mf._recovery_paths(session.dat_path)[0].exists()
    finally:
        _cleanup(session)


def test_close_waits_for_inflight_recovery_then_removes_snapshot(
    tmp_path, monkeypatch,
):
    """Explicit discard cannot race an already-started recovery write.

    The session is dirty, so this is the force=True path: an unforced close of a
    dirty session is refused outright (409 SESSION_DIRTY) and never reaches the
    lock whose ordering this test exercises.
    """
    monkeypatch.setattr(mf, "_DAT_BACKUP_DIR", tmp_path / "backups")
    session = _disk_session(tmp_path, "close-during-recovery")
    _mutate(session)
    recovery_entered = threading.Event()
    release_recovery = threading.Event()
    real_write = mf.write_bytes_atomic
    recovery_path, _ = mf._recovery_paths(session.dat_path)

    def pause_recovery(path, data):
        if path == recovery_path:
            recovery_entered.set()
            assert release_recovery.wait(5)
        real_write(path, data)

    monkeypatch.setattr(mf, "write_bytes_atomic", pause_recovery)
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            recovery_future = pool.submit(mf._write_recovery, session)
            assert recovery_entered.wait(5)
            close_future = pool.submit(close_session, session.id, force=True)
            assert not close_future.done()
            release_recovery.set()
            assert recovery_future.result(timeout=5) is True
            assert close_future.result(timeout=5) == {"closed": session.id}
        rec_dat, rec_meta = mf._recovery_paths(session.dat_path)
        assert not rec_dat.exists() and not rec_meta.exists()
    finally:
        release_recovery.set()
        _cleanup(session)
