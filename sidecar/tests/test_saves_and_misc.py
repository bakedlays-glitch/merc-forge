"""Tests for save scanner, INI reader, traits catalog, procedural animation."""
from __future__ import annotations

from pathlib import Path

from PIL import Image

from mercwizard_core.ini import read_options
from mercwizard_core.portrait.animate_skip import EYE_SUBFRAME_SIZE, MOUTH_SUBFRAME_SIZE
from mercwizard_core.saves import find_refs_in_save, scan_saves_for_mercs
from mercwizard_core.traits import (
    NEW_TRAITS,
    OLD_TRAITS,
    get_active_traits,
    is_expert,
    trait_name,
)


# ──────────────────────────────────────────────────────────────────────────
#  Save scanner
# ──────────────────────────────────────────────────────────────────────────

def test_find_refs_finds_utf16le_nickname(tmp_path: Path) -> None:
    """A merc's nickname appears in the save as UTF-16LE bytes."""
    save_path = tmp_path / "test.SAV"
    # Embed "Tycho" as UTF-16LE in a fake save file
    fake_save = b"random binary data " + "Tycho".encode("utf-16-le") + b" more data"
    save_path.write_bytes(fake_save)

    hits = find_refs_in_save(save_path, ["Tycho", "Carter", "Bob"])
    assert "Tycho" in hits
    assert "Carter" not in hits  # not in the file
    assert "Bob" not in hits     # too short — filtered by min_nick_len=3


def test_find_refs_respects_min_length(tmp_path: Path) -> None:
    save_path = tmp_path / "test.SAV"
    save_path.write_bytes(b"\x00" + "Vi".encode("utf-16-le") + b"\x00")
    hits = find_refs_in_save(save_path, ["Vi"])
    assert hits == []  # filtered by length


def test_scan_saves_for_mercs_handles_missing_save_dir(tmp_path: Path) -> None:
    """When no save folders exist, returns {}."""
    nicknames = {1: "Tycho", 2: "Carter"}
    # Point at an empty folder; the function probes its candidate set which
    # may or may not exist on this machine. The result should still be a dict.
    result = scan_saves_for_mercs(nicknames, extra_save_folder=tmp_path / "nope")
    assert isinstance(result, dict)


# ──────────────────────────────────────────────────────────────────────────
#  INI reader
# ──────────────────────────────────────────────────────────────────────────

def test_read_options_handles_missing_file(tmp_path: Path) -> None:
    cfg = read_options(tmp_path)
    assert cfg.enable_new_trait_system is True   # safe default
    assert cfg.data_dir_override is None


def test_read_options_parses_enable_new_trait_system(tmp_path: Path) -> None:
    ini = tmp_path / "Ja2_Options.ini"
    ini.write_text(
        "[Strategic Gameplay Settings]\n"
        "ENABLE_NEW_TRAIT_SYSTEM = FALSE\n"
    )
    cfg = read_options(tmp_path)
    assert cfg.enable_new_trait_system is False


def test_read_options_handles_bom_and_comments(tmp_path: Path) -> None:
    ini = tmp_path / "Ja2_Options.ini"
    ini.write_bytes(
        b"\xef\xbb\xbf"  # UTF-8 BOM
        b"; A comment line\n"
        b"[Section]\n"
        b"# Another comment\n"
        b"// Yet another comment\n"
        b'ENABLE_NEW_TRAIT_SYSTEM = "TRUE"\n'
    )
    cfg = read_options(tmp_path)
    assert cfg.enable_new_trait_system is True


# ──────────────────────────────────────────────────────────────────────────
#  Trait catalogs
# ──────────────────────────────────────────────────────────────────────────

def test_old_trait_catalog_has_15_entries_plus_none() -> None:
    assert 0 in OLD_TRAITS
    assert OLD_TRAITS[0].name == "None"
    assert OLD_TRAITS[14].name == "Rooftop Sniping"


def test_new_trait_catalog_has_23_entries_plus_none() -> None:
    assert 0 in NEW_TRAITS
    assert NEW_TRAITS[0].name == "None"
    assert NEW_TRAITS[1].name == "Auto Weapons"
    assert NEW_TRAITS[20].name == "Covert Ops"
    assert NEW_TRAITS[23].name == "Survival"


def test_trait_name_lookup() -> None:
    assert trait_name(3, use_new_traits=True) == "Marksman"
    assert trait_name(3, use_new_traits=False) == "Electronics"
    assert "Unknown" in trait_name(99, use_new_traits=True)


def test_is_expert_detects_duplicate_major_trait() -> None:
    # Two slots both holding Marksman (3) = Expert tier
    assert is_expert([3, 3, 0, 0], target_trait_id=3) is True
    assert is_expert([3, 0, 0, 0], target_trait_id=3) is False
    # Trait 0 (None) doesn't count as Expert even if repeated
    assert is_expert([0, 0, 0, 0], target_trait_id=0) is False


def test_get_active_traits_swaps_catalogs() -> None:
    assert get_active_traits(True) == NEW_TRAITS
    assert get_active_traits(False) == OLD_TRAITS


# Procedural animation module was removed in the 2.0c arc — vertical-squash +
# skin-tone-fill output was format-valid but not visually convincing.
# Real animation now comes only from animate_explicit (user-supplied PNGs).
