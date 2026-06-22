"""Security regression: MapForge path confinement.

Pins that the .dat/.xml HTTP endpoints can't be steered to read or write
outside the active install — the `_confine_install_path` guard on
`_validate_path` / `new_sector` / `save_copy_as`, plus the VFS `resolve_*`
path-join backstop (`VfsLayout._reject_unsafe_rel`).
"""
from pathlib import Path

import pytest
from fastapi import HTTPException

import routes.mapforge as mf
from routes.mapforge import (
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
