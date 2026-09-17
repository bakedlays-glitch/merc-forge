"""Reload-wins for the caller's OWN session (POST /sessions previous_session_id).

A page reload cannot close its old session first, so it names it on the next
open. Contract (routes/mapforge.py open_session):
  * own session DIRTY  -> RECONNECT: the same session comes back, edits kept;
  * own session clean  -> reclaimed (closed) and a fresh one opens;
  * previous id for a DIFFERENT map -> left alone.
Pinned because the dirty branch shipped without a test.
"""
import threading
from contextlib import nullcontext
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

import routes.mapforge as mf
from routes.mapforge import OpenSessionBody, _session_store, open_session
from tests.test_mapforge_library import _build_minimal_dat

_INSTALL = "reconnect-test-install"


@pytest.fixture(autouse=True)
def _env(tmp_path, monkeypatch):
    state = SimpleNamespace(
        active=lambda: SimpleNamespace(id=_INSTALL, path=str(tmp_path)),
        write_lock=threading.RLock(),
    )
    monkeypatch.setattr(mf, "get_state", lambda: state)
    monkeypatch.setattr(mf, "_active_install_root", lambda: tmp_path)
    monkeypatch.setattr(mf, "_iso_renderer_available", True)
    monkeypatch.setattr(mf, "cross_process_install_lock", lambda _i: nullcontext())
    monkeypatch.setattr(mf, "acquire_writable_map_session_lease",
                        lambda *_a: SimpleNamespace(release=lambda: None))
    yield
    for s in list(_session_store.list_all()):
        try:
            _session_store.close(s.id)
        except Exception:
            pass


def _map(tmp_path, name):
    dat = tmp_path / "Data-1.13" / "Maps" / name
    xml = tmp_path / "Data-1.13" / "Ja2Set.dat.xml"
    dat.parent.mkdir(parents=True, exist_ok=True)
    dat.write_bytes(_build_minimal_dat(land={0: [(1, 1)]}))
    if not xml.exists():
        xml.write_text("<tilesets/>", encoding="utf-8")
    return OpenSessionBody(dat=str(dat), xml=str(xml), tileset=7)


def _reopen(body, previous_session_id):
    return OpenSessionBody(dat=body.dat, xml=body.xml, tileset=body.tileset,
                           previous_session_id=previous_session_id)


def test_dirty_own_session_reconnects_and_keeps_edits(tmp_path):
    body = _map(tmp_path, "A1.dat")
    first = open_session(body)
    sess = _session_store.get(first.session_id)
    sess.dirty = True                     # unsaved edits live only here
    again = open_session(_reopen(body, first.session_id))
    assert again.session_id == first.session_id
    assert _session_store.get(first.session_id) is sess and sess.dirty
    assert len(_session_store.list_all()) == 1


def test_clean_own_session_is_reclaimed_and_reopened(tmp_path):
    body = _map(tmp_path, "A1.dat")
    first = open_session(body)
    again = open_session(_reopen(body, first.session_id))
    assert again.session_id != first.session_id
    assert [s.id for s in _session_store.list_all()] == [again.session_id]


def test_previous_id_for_another_map_is_left_alone(tmp_path):
    a = open_session(_map(tmp_path, "A1.dat"))
    _session_store.get(a.session_id).dirty = True
    b_body = _map(tmp_path, "B1.dat")
    b = open_session(_reopen(b_body, a.session_id))
    assert b.session_id != a.session_id
    assert {s.id for s in _session_store.list_all()} == {a.session_id, b.session_id}


def test_close_refuses_dirty_session_without_force(tmp_path):
    """DELETE /sessions/{id} on a DIRTY session must 409 (SESSION_DIRTY) unless
    force=true — the page's mount-effect cleanup (re-run by StrictMode/HMR)
    used to discard 1092 unsaved edits this way."""
    from routes.mapforge import close_session
    body = _map(tmp_path, "A1.dat")
    info = open_session(body)
    _session_store.get(info.session_id).dirty = True
    with pytest.raises(HTTPException) as exc:
        close_session(info.session_id)
    assert exc.value.status_code == 409
    assert exc.value.detail["error"] == "SESSION_DIRTY"
    assert _session_store.get(info.session_id).dirty          # still alive
    assert close_session(info.session_id, force=True) == {"closed": info.session_id}
    assert _session_store.list_all() == []
