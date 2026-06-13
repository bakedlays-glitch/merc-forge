"""Tests for the strategic-hub-grid sector-names endpoint
(GET /mapforge/installs/sector-names).

The endpoint surfaces the active install's SectorNames.xml grid→town-name
map so the MapForge hub grid can label each A1–P16 cell. It must:
  • return the parsed names when SectorNames.xml exists,
  • return an EMPTY map (not error) when it's absent or malformed,
  • 400 when there's no active install.

These call the route function directly with `get_state` /
`_require_renderer` monkeypatched, mirroring the building-library
endpoint tests — no renderer or game launch needed.
"""

from __future__ import annotations

import sys
import xml.etree.ElementTree as ET
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import routes.mapforge as mf  # noqa: E402


def _write_sector_names(install: Path, names: dict[str, str]) -> None:
    root = ET.Element("SECTOR_NAMES")
    for grid, name in names.items():
        sec = ET.SubElement(root, "SECTOR")
        ET.SubElement(sec, "SectorGrid").text = grid
        ET.SubElement(sec, "szExploredName").text = name
    p = install / "Data-1.13" / "TableData" / "Map" / "SectorNames.xml"
    p.parent.mkdir(parents=True, exist_ok=True)
    ET.ElementTree(root).write(p, encoding="utf-8", xml_declaration=True)


def _patch_active(monkeypatch, install: Path | None) -> None:
    """Make the endpoint see (or not see) an active install at `install`."""
    monkeypatch.setattr(mf, "_require_renderer", lambda: None)
    active = (
        SimpleNamespace(id="testinstall", path=str(install))
        if install is not None
        else None
    )
    fake_state = SimpleNamespace(active=lambda: active)
    monkeypatch.setattr(mf, "get_state", lambda: fake_state)


def test_returns_parsed_sector_names(tmp_path: Path, monkeypatch) -> None:
    install = tmp_path / "install"
    install.mkdir()
    _write_sector_names(install, {"A9": "Omerta", "C5": "The Den"})
    _patch_active(monkeypatch, install)

    res = mf.list_active_install_sector_names()
    assert res.install_id == "testinstall"
    assert res.names["A9"] == "Omerta"
    assert res.names["C5"] == "The Den"


def test_empty_when_no_sector_names_file(tmp_path: Path, monkeypatch) -> None:
    install = tmp_path / "install"
    (install / "Data-1.13").mkdir(parents=True)
    _patch_active(monkeypatch, install)

    res = mf.list_active_install_sector_names()
    assert res.names == {}


def test_empty_when_sector_names_malformed(tmp_path: Path, monkeypatch) -> None:
    install = tmp_path / "install"
    p = install / "Data-1.13" / "TableData" / "Map" / "SectorNames.xml"
    p.parent.mkdir(parents=True)
    p.write_text("<SECTOR_NAMES><SECTOR><not-closed",
                 encoding="utf-8")
    _patch_active(monkeypatch, install)

    # load_sector_names swallows ParseError → {} ; the endpoint must not
    # raise either.
    res = mf.list_active_install_sector_names()
    assert res.names == {}


def test_400_when_no_active_install(monkeypatch) -> None:
    _patch_active(monkeypatch, None)
    with pytest.raises(HTTPException) as exc:
        mf.list_active_install_sector_names()
    assert exc.value.status_code == 400
    assert exc.value.detail["error"] == "NO_ACTIVE_INSTALL"
