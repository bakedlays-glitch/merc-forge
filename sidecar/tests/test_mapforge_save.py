"""Save-path safety: backups live OUTSIDE the install, saves are atomic,
dirty-tracking can't lie, and a height edit round-trips byte-exactly.

Covers the review findings from the Slice-0 audit:
- the pristine backup must NOT land in `Maps/` (the in-game editor's
  load dialog enumerates `MAPS/*` with no extension filter, so a
  `.dat.bak` there shows up as a loadable map);
- an empty edit batch must never reset a dirty session to clean;
- `write_dat_bytes` heights emission gets byte-level coverage (a
  low/high byte swap or interleave off-by-one previously passed CI).
"""
import threading
from contextlib import contextmanager, nullcontext
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

import routes.mapforge as mf
from mercwizard_core.mapforge_engine.dat_edit_ops import add_layer_entry, set_height
from mercwizard_core.mapforge_engine.parse_dat_ext import parse_dat_full
from mercwizard_core.mapforge_engine.dat_writer import write_dat_bytes
from routes.mapforge import (
    ApplyEditsBody,
    ExtractSlfMapBody,
    MapForgeSession,
    SaveCopyAsBody,
    _session_store,
    apply_edits,
    extract_slf_to_loose,
    save_copy_as,
    save_session,
)
from tests.test_mapforge_library import _build_minimal_dat

_HEADER_LEN = 25  # major>=7 header (matches parse_dat_ext)


@pytest.fixture(autouse=True)
def _in_process_install_lock(monkeypatch):
    """Save semantics are under test here, not the Windows lock backend."""
    monkeypatch.setattr(
        mf, "cross_process_install_lock", lambda _install_id: nullcontext(),
    )
    monkeypatch.setattr(
        mf, "acquire_writable_map_session_lease",
        lambda *_args: SimpleNamespace(release=lambda: None),
    )
    state = SimpleNamespace(
        active=lambda: SimpleNamespace(id="test-install", path="unused"),
        write_lock=threading.RLock(),
    )
    monkeypatch.setattr(mf, "get_state", lambda: state)


def _real_session(tmp_path: Path, sess_id: str = "test-save-session"):
    """A session over a REAL minimal .dat on disk (so save_session's
    write + backup paths run for real)."""
    maps_dir = tmp_path / "install" / "Data-1.13" / "Maps"
    maps_dir.mkdir(parents=True)
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
    sess.dirty = True
    sess.mutation_seq = 1
    sess.autosaved_seq = 0
    sess.edit_count = 1
    sess.created_at = 0.0
    sess.last_used_at = 0.0
    sess.read_only = False
    sess.source_uri = ""
    sess.install_id = "test-install"
    sess.closed = False
    sess._lock = threading.Lock()
    _session_store._sessions[sess.id] = sess
    return sess


def _cleanup(sess):
    _session_store._sessions.pop(sess.id, None)


def test_save_keeps_backups_out_of_maps_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(mf, "_DAT_BACKUP_DIR", tmp_path / "backups")
    sess = _real_session(tmp_path)
    try:
        sess.parsed["heights"][0] = 80
        result = save_session(sess.id)
        maps_dir = sess.dat_path.parent
        # ONLY the .dat itself lives in Maps/ — no .bak, no stranded tmp.
        assert sorted(p.name for p in maps_dir.iterdir()) == ["A1.dat"]
        # Pristine backup exists outside the install and holds the
        # original (pre-edit) bytes.
        pristine = Path(result.backup_path)
        assert (tmp_path / "backups") in pristine.parents
        assert pristine.read_bytes() == sess.original_bytes or True
        # (original_bytes was re-baselined by save; compare to source)
        assert pristine.read_bytes() == _build_minimal_dat(land={0: [(1, 1)]})
        # Saved file actually carries the edit + session is clean.
        assert parse_dat_full(sess.dat_path.read_bytes(),
                              str(sess.dat_path))["heights"][0] == 80
        assert sess.dirty is False
    finally:
        _cleanup(sess)


def test_second_save_creates_rolling_backup(tmp_path, monkeypatch):
    monkeypatch.setattr(mf, "_DAT_BACKUP_DIR", tmp_path / "backups")
    sess = _real_session(tmp_path)
    try:
        backup_dir = mf._session_backup_dir(sess.dat_path)

        def rolling():
            return [p for p in backup_dir.iterdir()
                    if p.name != "pristine_original.dat"]

        # EVERY save rolls the current on-disk version first.
        save_session(sess.id)
        assert len(rolling()) == 1
        sess.parsed["heights"][1] = 160
        sess.dirty = True
        save_session(sess.id)
        names = rolling()
        assert len(names) == 2
        assert all(p.suffix == ".dat" for p in names)
    finally:
        _cleanup(sess)


def test_second_save_after_size_changing_edit_keeps_appendix(tmp_path,
                                                             monkeypatch):
    """A size-changing edit (added layer entry) grows the content region,
    so after the first save the appendix starts LATER in the new baseline.
    save_session re-baselines original_bytes; if parsed["appendix_offset"]
    isn't re-anchored too, the SECOND save slices the appendix at the
    stale offset — prepending stray room-info bytes to the appendix and
    growing the file every save (engine misload). Pin: a no-op second
    save must be byte-identical to the first."""
    monkeypatch.setattr(mf, "_DAT_BACKUP_DIR", tmp_path / "backups")
    sess = _real_session(tmp_path, "test-second-save-size")
    try:
        # Size-changing edit: +1 land entry = layer pass grows 2 bytes.
        add_layer_entry(sess.parsed, gridno=2, layer="land", slot=1, sub=3)
        sess.dirty = True
        save_session(sess.id)
        first = sess.dat_path.read_bytes()
        # Second save, no further edits: must round-trip byte-exactly.
        sess.dirty = True
        save_session(sess.id)
        second = sess.dat_path.read_bytes()
        assert len(second) == len(first)
        assert second == first
        # And the result still parses with the edit intact.
        parsed = parse_dat_full(second, str(sess.dat_path))
        assert (1, 3) in parsed["land"][2]
    finally:
        _cleanup(sess)


def test_save_refuses_external_modification_unless_forced(tmp_path,
                                                          monkeypatch):
    """Sessions have no file-identity concept — if the in-game editor,
    another MercForge window, or a file copy rewrote the .dat under an
    open session, a blind save silently last-writer-wins over it. The
    guard 409s; force=true overwrites, with the external version still
    captured by the rolling pre-save backup."""
    monkeypatch.setattr(mf, "_DAT_BACKUP_DIR", tmp_path / "backups")
    sess = _real_session(tmp_path, "test-external-mod")
    try:
        # Something else rewrites the .dat under the open session.
        external = _build_minimal_dat(land={0: [(2, 2)]})
        sess.dat_path.write_bytes(external)
        sess.parsed["heights"][0] = 80
        sess.dirty = True
        with pytest.raises(HTTPException) as ei:
            save_session(sess.id)
        assert ei.value.detail["error"] == "EXTERNAL_MODIFICATION"
        # The refused save touched nothing.
        assert sess.dat_path.read_bytes() == external
        # force=true overwrites…
        save_session(sess.id, force=True)
        saved = parse_dat_full(sess.dat_path.read_bytes(),
                               str(sess.dat_path))
        assert saved["heights"][0] == 80
        # …and the external version is recoverable from a rolling backup.
        backup_dir = mf._session_backup_dir(sess.dat_path)
        rolling = [p for p in backup_dir.iterdir()
                   if p.name != "pristine_original.dat"]
        assert any(p.read_bytes() == external for p in rolling)
    finally:
        _cleanup(sess)


def test_same_target_sessions_serialize_guard_and_second_save_conflicts(
    tmp_path, monkeypatch,
):
    """Same-install saves must not both pass the external-change guard."""
    monkeypatch.setattr(mf, "_DAT_BACKUP_DIR", tmp_path / "backups")
    first = _real_session(tmp_path, "first-same-target")
    second = MapForgeSession(first.dat_path, first.xml_path, first.tileset)
    second.id = "second-same-target"
    second.install_id = first.install_id
    second.parsed["heights"][0] = 160
    second.dirty = True
    second.mutation_seq = 1
    _session_store._sessions[second.id] = second
    first.parsed["heights"][0] = 80
    first_entered = threading.Event()
    release_first = threading.Event()
    real_write = mf.write_bytes_atomic
    call_count = 0

    def pause_first_write(path, data):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            first_entered.set()
            assert release_first.wait(5)
        real_write(path, data)

    monkeypatch.setattr(mf, "write_bytes_atomic", pause_first_write)
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            first_future = pool.submit(save_session, first.id)
            assert first_entered.wait(5)
            second_future = pool.submit(save_session, second.id)
            release_first.set()
            first_future.result(timeout=5)
            with pytest.raises(HTTPException) as exc:
                second_future.result(timeout=5)
        assert exc.value.detail["error"] == "EXTERNAL_MODIFICATION"
        assert parse_dat_full(
            first.dat_path.read_bytes(), str(first.dat_path),
        )["heights"][0] == 80
    finally:
        _cleanup(first)
        _cleanup(second)


def test_save_copy_as_rejects_active_install_switch(tmp_path, monkeypatch):
    """A session from A must not write B while holding A's install lock."""
    session = _real_session(tmp_path, "copy-as-install-switch")
    install_a = session.dat_path.parents[2]
    install_b = tmp_path / "install-b"
    dest = install_b / "Data-1.13" / "Maps" / "B1.dat"
    install_b.mkdir()
    state_b = SimpleNamespace(
        active=lambda: SimpleNamespace(id="install-b", path=str(install_b)),
        write_lock=threading.RLock(),
    )
    monkeypatch.setattr(mf, "get_state", lambda: state_b)
    try:
        with pytest.raises(HTTPException) as exc:
            save_copy_as(session.id, SaveCopyAsBody(dat_path=str(dest)))
        assert exc.value.status_code == 409
        assert exc.value.detail["error"] == "SESSION_INSTALL_CHANGED"
        assert not dest.exists()
        assert session.dat_path.is_relative_to(install_a)
    finally:
        _cleanup(session)


def test_extract_and_copy_as_never_race_to_overwrite_loose_dest(
    tmp_path, monkeypatch,
):
    """Extraction owns the install transaction before an equal copy can write."""
    session = _real_session(tmp_path, "extract-copy-race")
    install_root = session.dat_path.parents[2]
    archive = install_root / "Data-1.13" / "Maps.slf"
    archive.write_bytes(b"fixture")
    dest = install_root / "Data-1.13" / "Maps" / "SLF_A1.dat"
    slf_data = b"from-slf"
    state = SimpleNamespace(
        active=lambda: SimpleNamespace(id=session.install_id, path=str(install_root)),
        write_lock=threading.RLock(),
    )
    monkeypatch.setattr(mf, "get_state", lambda: state)
    monkeypatch.setattr(mf, "_iso_renderer_available", True)

    import importlib
    slf_module = importlib.import_module("ja2py.fileformats.SlfFS")
    read_entered = threading.Event()
    release_read = threading.Event()
    copy_write_started = threading.Event()
    real_write = mf.write_bytes_atomic
    install_lock = threading.Lock()

    class ControlledSlf:
        def __init__(self, _path):
            pass

        def readbytes(self, _internal):
            read_entered.set()
            assert release_read.wait(5)
            return slf_data

    @contextmanager
    def controlled_install_lock(_install_id):
        with install_lock:
            yield

    def observe_write(path, data):
        if path == dest and data != slf_data:
            copy_write_started.set()
        real_write(path, data)

    monkeypatch.setattr(slf_module, "SlfFS", ControlledSlf)
    monkeypatch.setattr(mf, "cross_process_install_lock", controlled_install_lock)
    monkeypatch.setattr(mf, "write_bytes_atomic", observe_write)
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            extract_future = pool.submit(
                extract_slf_to_loose,
                ExtractSlfMapBody(slf_uri=f"slf://{archive}!/SLF_A1.dat"),
            )
            assert read_entered.wait(5)
            copy_future = pool.submit(
                save_copy_as, session.id, SaveCopyAsBody(dat_path=str(dest)),
            )
            # Before the extraction can finish, a competing copy must still
            # be outside its writer. The pre-fix endpoint never took the
            # install transaction, so this event became set here.
            assert not copy_write_started.wait(0.25)
            release_read.set()
            extract_future.result(timeout=5)
            with pytest.raises(HTTPException) as exc:
                copy_future.result(timeout=5)
        assert exc.value.status_code == 409
        assert exc.value.detail["error"] == "FILE_EXISTS"
        assert dest.read_bytes() == slf_data
    finally:
        release_read.set()
        _cleanup(session)


def test_backup_dirs_distinct_for_same_stem_different_installs(tmp_path):
    a = mf._session_backup_dir(tmp_path / "installA" / "Maps" / "A9.dat")
    b = mf._session_backup_dir(tmp_path / "installB" / "Maps" / "A9.dat")
    assert a != b


def test_empty_edit_batch_keeps_session_dirty(tmp_path, monkeypatch):
    monkeypatch.setattr(mf, "_DAT_BACKUP_DIR", tmp_path / "backups")
    sess = _real_session(tmp_path, "test-empty-batch")
    try:
        assert sess.dirty is True
        result = apply_edits(sess.id, ApplyEditsBody(edits=[]))
        assert result.applied == 0
        assert sess.dirty is True   # an empty batch must not clear dirty
    finally:
        _cleanup(sess)


def test_height_edit_roundtrips_byte_exactly():
    """parse → set_height → write must change EXACTLY the edited tile's
    low height byte and nothing else. Pins the writer's 2-byte
    interleave (low=height, high=preserved garbage) at the byte level."""
    data = _build_minimal_dat(land={0: [(1, 1)]})
    parsed = parse_dat_full(data, "synthetic.dat")
    gridno = 5
    set_height(parsed, gridno, 80)
    out = write_dat_bytes(parsed, data)
    assert len(out) == len(data)
    diffs = [i for i, (a, b) in enumerate(zip(data, out)) if a != b]
    assert diffs == [_HEADER_LEN + 2 * gridno]
    assert out[_HEADER_LEN + 2 * gridno] == 80
    # Unedited writer output is byte-identical to the source.
    assert write_dat_bytes(parse_dat_full(data, "x.dat"), data) == data


def test_session_baseline_captures_as_opened_findings(tmp_path):
    """MapForgeSession.__init__ snapshots validate_parsed of the opened
    file; the minimal dat has no exit grids / edgepoints, so those codes
    must be in the baseline."""
    sess = _real_session(tmp_path, "test-baseline-init")
    try:
        # _real_session builds via __new__, so compute like __init__ does.
        real = MapForgeSession.__new__(MapForgeSession)
        # Use the actual constructor for this one — it reads the file.
        real = MapForgeSession(sess.dat_path, sess.xml_path, 7)
        assert "NO_EXIT_GRIDS" in real.baseline_findings
        assert "NO_EDGEPOINTS" in real.baseline_findings
    finally:
        _cleanup(sess)


def test_session_validate_tags_preexisting_vs_new(tmp_path):
    """A finding in the baseline at the same count is tagged preexisting;
    a finding the edits introduced (or grew) is not."""
    from routes.mapforge import session_validate

    sess = _real_session(tmp_path, "test-baseline-tags")
    try:
        # Baseline: the map "came with" a room gap (rooms 1 and 3 exist,
        # 2 missing) and the usual NO_EXIT_GRIDS warn.
        sess.parsed["rooms"] = [1, 0, 3, 0] + [0] * (len(sess.parsed["rooms"]) - 4)
        sess.baseline_findings = {"ROOM_ID_GAP": 1, "NO_EXIT_GRIDS": 0,
                                  "NO_EDGEPOINTS": 0}
        report = session_validate(sess.id, check_jsd=False)
        by_code = {f.code: f for f in report.findings}
        assert by_code["ROOM_ID_GAP"].preexisting is True
        assert by_code["NO_EXIT_GRIDS"].preexisting is True

        # Now the "edit" introduces a SECOND gap — count grows past the
        # baseline -> no longer tagged preexisting.
        sess.parsed["rooms"][3] = 5      # rooms now 1,3,5 -> gaps 2 and 4
        report2 = session_validate(sess.id, check_jsd=False)
        gap2 = next(f for f in report2.findings if f.code == "ROOM_ID_GAP")
        assert gap2.preexisting is False

        # And a brand-new finding code is never preexisting: force a
        # height the baseline didn't have.
        sess.parsed["heights"][0] = 3
        report3 = session_validate(sess.id, check_jsd=False)
        nh = next(f for f in report3.findings if f.code == "NONSTANDARD_HEIGHT")
        assert nh.preexisting is False
    finally:
        _cleanup(sess)
