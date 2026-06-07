"""Tests for install detection + mod fingerprinting."""
from __future__ import annotations

from pathlib import Path

from mercwizard_core.install_detect import find_exe, validate_install
from mercwizard_core.mod_detect import ModId, detect_mod


def test_validate_install_rejects_missing_dir(tmp_path: Path) -> None:
    fake = tmp_path / "does_not_exist"
    info = validate_install(fake)
    assert info.valid is False
    assert any("does not exist" in e for e in info.errors)


def test_validate_install_rejects_missing_exe(tmp_path: Path) -> None:
    info = validate_install(tmp_path)
    assert info.valid is False
    assert any("JA2 executable" in e for e in info.errors)


def test_validate_install_finds_exe(tmp_path: Path) -> None:
    """Create a fake JA2 install layout."""
    (tmp_path / "JA2.exe").touch()
    (tmp_path / "Data-1.13" / "TableData").mkdir(parents=True)
    (tmp_path / "Data-1.13" / "TableData" / "MercProfiles.xml").touch()
    (tmp_path / "Data-1.13" / "TableData" / "AIMAvailability.xml").touch()

    info = validate_install(tmp_path)
    assert info.valid is True
    assert info.errors == []
    assert info.exe_path.name == "JA2.exe"


def test_find_exe_finds_lowercase_variant(tmp_path: Path) -> None:
    (tmp_path / "ja2.exe").touch()
    exe = find_exe(tmp_path)
    assert exe is not None
    assert exe.name.lower() == "ja2.exe"


def test_detect_mod_finds_wasteland_via_tileset_70(tmp_path: Path) -> None:
    (tmp_path / "Data-1.13" / "TileSets" / "Tileset 70").mkdir(parents=True)
    mod = detect_mod(tmp_path)
    assert mod.id == ModId.WASTELAND
    assert mod.confidence > 0


def test_detect_mod_returns_unknown_when_no_signals(tmp_path: Path) -> None:
    """An empty dir with no recognizable name or content fingerprint returns UNKNOWN.

    More honest than the old behavior of defaulting to vanilla — the player can
    still use the install; we just don't pretend to know what mod it is.
    """
    mod = detect_mod(tmp_path)
    assert mod.id == ModId.UNKNOWN


def test_detect_mod_finds_aimnas_via_folder_name(tmp_path: Path) -> None:
    install = tmp_path / "Jagged Alliance 2 Gold 1.13 AIMNAS"
    install.mkdir()
    mod = detect_mod(install)
    assert mod.id == ModId.AIMNAS
    assert "AIMNAS" in mod.display_name


def test_detect_mod_finds_urban_chaos_via_folder_name(tmp_path: Path) -> None:
    install = tmp_path / "Jagged Alliance 2 Gold Urban Chaos 1.13"
    install.mkdir()
    mod = detect_mod(install)
    assert mod.id == ModId.URBAN_CHAOS


def test_detect_mod_finds_vanilla_via_113_in_name(tmp_path: Path) -> None:
    install = tmp_path / "Jagged Alliance 2 Gold 1.13"
    install.mkdir()
    mod = detect_mod(install)
    assert mod.id == ModId.VANILLA
