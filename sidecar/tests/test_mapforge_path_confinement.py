"""Security regression: MapForge path confinement.

Pins that the .dat/.xml HTTP endpoints can't be steered to read or write
outside the active install — the `_confine_install_path` guard on
`_validate_path` / `new_sector` / `save_copy_as`, plus the VFS `resolve_*`
path-join backstop (`VfsLayout._reject_unsafe_rel`).
"""
import importlib
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

import routes.mapforge as mf
from routes.mapforge import (
    ExtractSlfMapBody,
    NewSectorBody,
    _confine_install_path,
    _validate_path,
    new_sector,
)
from mercwizard_core.vfs import VfsLayout


def _active(monkeypatch, root):
    monkeypatch.setattr(mf, "_active_install_root", lambda: root)


# ── _confine_install_path ─────────────────────────────────────────────────


def test_confine_requires_active_install(monkeypatch, tmp_path):
    _active(monkeypatch, None)
    with pytest.raises(HTTPException) as exc:
        _confine_install_path(str(tmp_path / "x.dat"))
    assert exc.value.status_code == 409
    assert exc.value.detail["error"] == "NO_ACTIVE_INSTALL"


def test_confine_accepts_path_inside_install(monkeypatch, tmp_path):
    install = tmp_path / "install"
    _active(monkeypatch, install)
    got = _confine_install_path(str(install / "Maps" / "A1.dat"))
    assert got == (install / "Maps" / "A1.dat").resolve()


def test_confine_rejects_path_outside_install(monkeypatch, tmp_path):
    install = tmp_path / "install"
    _active(monkeypatch, install)
    with pytest.raises(HTTPException) as exc:
        _confine_install_path(str(tmp_path / "outside" / "evil.dat"))
    assert exc.value.status_code == 403
    assert exc.value.detail["error"] == "PATH_NOT_ALLOWED"


def test_confine_rejects_dotdot_escape(monkeypatch, tmp_path):
    install = tmp_path / "install"
    _active(monkeypatch, install)
    sneaky = install / "Maps" / ".." / ".." / "evil.dat"  # resolves outside
    with pytest.raises(HTTPException) as exc:
        _confine_install_path(str(sneaky))
    assert exc.value.status_code == 403


def test_confine_rejects_absolute_system_path(monkeypatch, tmp_path):
    _active(monkeypatch, tmp_path / "install")
    with pytest.raises(HTTPException) as exc:
        _confine_install_path("C:/Windows/System32/drivers/etc/hosts.dat")
    assert exc.value.status_code == 403


# ── endpoint-level enforcement ─────────────────────────────────────────────


def test_new_sector_rejects_out_of_install_dest(monkeypatch, tmp_path):
    monkeypatch.setattr(mf, "_iso_renderer_available", True)
    _active(monkeypatch, tmp_path / "install")
    with pytest.raises(HTTPException) as exc:
        new_sector(NewSectorBody(
            dat_path=str(tmp_path / "outside" / "Z9.dat"), tileset=71))
    assert exc.value.status_code == 403


def test_new_sector_writes_inside_install(monkeypatch, tmp_path):
    monkeypatch.setattr(mf, "_iso_renderer_available", True)
    install = tmp_path / "install"
    _active(monkeypatch, install)
    dest = install / "Data-1.13" / "Maps" / "Z9.dat"
    new_sector(NewSectorBody(dat_path=str(dest), tileset=71))
    assert dest.is_file()


def test_validate_path_rejects_out_of_install(monkeypatch, tmp_path):
    install = tmp_path / "install"
    install.mkdir()
    _active(monkeypatch, install)
    outside = tmp_path / "outside.dat"
    outside.write_bytes(b"x")  # exists, but outside the install tree
    with pytest.raises(HTTPException) as exc:
        _validate_path(str(outside), ".dat")
    assert exc.value.status_code == 403


def test_slf_archive_outside_active_install_is_rejected(monkeypatch, tmp_path):
    install = tmp_path / "install"
    install.mkdir()
    _active(monkeypatch, install)
    outside = tmp_path / "outside.slf"
    outside.write_bytes(b"not-an-slf")
    with pytest.raises(HTTPException) as exc:
        mf._resolve_slf_uri(f"slf://{outside}!/A1.dat")
    assert exc.value.status_code == 403


def test_extract_slf_endpoint_rejects_external_archive_before_opening_it(
    monkeypatch, tmp_path,
):
    """The extraction endpoint must use the same SLF confinement guard."""
    install = tmp_path / "install"
    (install / "Data-1.13").mkdir(parents=True)
    outside = tmp_path / "outside.slf"
    outside.write_bytes(b"not-an-slf")
    state = SimpleNamespace(
        active=lambda: SimpleNamespace(id="active", path=str(install)),
        write_lock=threading.RLock(),
    )
    opened = False

    def fail_if_opened(_path):
        nonlocal opened
        opened = True
        raise AssertionError("external SLF must not be opened")

    slf_module = importlib.import_module("ja2py.fileformats.SlfFS")
    monkeypatch.setattr(mf, "get_state", lambda: state)
    monkeypatch.setattr(mf, "_iso_renderer_available", True)
    monkeypatch.setattr(slf_module, "SlfFS", fail_if_opened)
    with pytest.raises(HTTPException) as exc:
        mf.extract_slf_to_loose(
            ExtractSlfMapBody(slf_uri=f"slf://{outside}!/A1.dat"),
        )
    assert exc.value.status_code == 403
    assert opened is False
    assert not (install / "Data-1.13" / "Maps" / "A1.dat").exists()


# ── VFS resolve_* path-join backstop ───────────────────────────────────────


@pytest.mark.parametrize(
    "bad", ["../x", "a/../../b", "/etc/passwd", "C:\\Windows\\x", "..\\..\\x"])
def test_vfs_reject_unsafe_rel(bad):
    with pytest.raises(ValueError):
        VfsLayout._reject_unsafe_rel(bad)


@pytest.mark.parametrize(
    "ok", ["TableData/Items/Items.xml", "Maps/A1.dat", "a/b/c.xml"])
def test_vfs_accepts_safe_rel(ok):
    VfsLayout._reject_unsafe_rel(ok)  # must not raise
