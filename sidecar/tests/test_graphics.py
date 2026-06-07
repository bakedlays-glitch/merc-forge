"""Graphics-stack verify/deploy tests (tmp fixtures, no live install)."""
from __future__ import annotations

from pathlib import Path

import pytest

from mercwizard_core.graphics import (
    GraphicsDeployError,
    deploy_graphics,
    graphics_status,
)
from mercwizard_core.ini_editor import parse_ini_map


def _by_component(rows: list[dict]) -> dict[str, dict]:
    return {r["component"]: r for r in rows}


def test_status_fresh_install_runtime_missing(tmp_path: Path):
    rows = _by_component(graphics_status(tmp_path))
    assert rows["ddraw.dll"]["present"] is False
    assert rows["ddraw.dll"]["download_url"]
    assert rows["ja2_remastered.ini"]["present"] is False
    assert rows["ddraw.ini"]["present"] is False


def test_status_classifies_customized_vs_golden(tmp_path: Path):
    (tmp_path / "ddraw.dll").write_bytes(b"x")
    (tmp_path / "ddraw.ini").write_text(
        "[ddraw]\nrenderer=gdi\nfullscreen=true\n")  # renderer differs
    rows = _by_component(graphics_status(tmp_path))
    dd = rows["ddraw.ini"]
    assert dd["present"] is True and dd["matches"] is False
    assert "ddraw/renderer" in dd["mismatched_keys"]


def test_deploy_refuses_without_runtime(tmp_path: Path):
    with pytest.raises(GraphicsDeployError) as e:
        deploy_graphics(tmp_path)
    assert e.value.code == "RUNTIME_MISSING"


def test_deploy_merges_preserving_user_keys(tmp_path: Path):
    (tmp_path / "ddraw.dll").write_bytes(b"x")
    (tmp_path / "opengl32.dll").write_bytes(b"x")
    (tmp_path / "ddraw.ini").write_text(
        "; user file\n[ddraw]\nrenderer=gdi\nMY_CUSTOM=1\n\n[game]\npreset=ja2\n")
    result = deploy_graphics(tmp_path)
    assert result["ok"] is True
    m = parse_ini_map((tmp_path / "ddraw.ini").read_text())
    assert m["ddraw"]["renderer"] == "opengl"        # golden key applied
    assert m["ddraw"]["MY_CUSTOM"] == "1"            # user key preserved
    assert m["game"]["preset"] == "ja2"              # unrelated section intact
    assert "; user file" in (tmp_path / "ddraw.ini").read_text()
    # ReShade.ini created with preset keys; preset copied
    rs = parse_ini_map((tmp_path / "ReShade.ini").read_text())
    assert rs["GENERAL"]["CurrentPresetPath"] == r".\ja2_remastered.ini"
    assert rs["GENERAL"]["PresetPath"] == r".\ja2_remastered.ini"
    assert (tmp_path / "ja2_remastered.ini").is_file()
    # status now green across the board
    rows = _by_component(graphics_status(tmp_path))
    assert rows["ja2_remastered.ini"]["matches"] is True
    assert rows["ddraw.ini"]["matches"] is True
    assert rows["ReShade.ini"]["matches"] is True
