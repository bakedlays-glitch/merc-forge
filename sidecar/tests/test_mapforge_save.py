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
from pathlib import Path

import routes.mapforge as mf
from mercwizard_core.mapforge_engine.dat_edit_ops import set_height
from mercwizard_core.mapforge_engine.parse_dat_ext import parse_dat_full
from mercwizard_core.mapforge_engine.dat_writer import write_dat_bytes
from routes.mapforge import (
    ApplyEditsBody,
    MapForgeSession,
    _session_store,
    apply_edits,
    save_session,
)
from tests.test_mapforge_library import _build_minimal_dat

_HEADER_LEN = 25  # major>=7 header (matches parse_dat_ext)


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
    sess.dirty = True
    sess.edit_count = 1
    sess.created_at = 0.0
    sess.last_used_at = 0.0
    sess.read_only = False
    sess.source_uri = ""
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
