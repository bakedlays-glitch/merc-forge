"""Tests for the VFS layout parser.

Covers:
- Legacy (no VFS config) → synthesized single-layer layout
- Vengeance-style chain with mod content profile pointing at Data-Vengeance
- Read resolution walks profiles high→low priority
- Write resolution falls back to mod content layer for new files
- Multi-location profiles (Vengeance's vengcore points at three dirs)
"""
from __future__ import annotations

from pathlib import Path

import pytest

from mercwizard_core.vfs import (
    VfsLayout,
    VfsLocation,
    VfsProfile,
    compute_vfs_mismatch,
    parse_vfs_config,
)


def _write_ja2_ini(install: Path, vfs_config_name: str | None = None) -> None:
    install.mkdir(parents=True, exist_ok=True)
    lines = [
        "[Ja2 Settings]",
        "CD = C",
    ]
    if vfs_config_name:
        lines.append(f"VFS_CONFIG_INI = {vfs_config_name}")
    (install / "Ja2.ini").write_text("\n".join(lines))


def _write_vfs_config(install: Path, name: str, profiles: dict[str, dict]) -> None:
    """Helper: write a vfs_config file given a dict of profile_name → spec.

    spec is dict with optional keys:
        locations: list of (loc_name, dir_path) tuples
        write: bool
    """
    lines = ["[vfs_config]", f"PROFILES = {', '.join(profiles.keys())}", ""]

    # Profile sections
    all_loc_names: set[str] = set()
    for pname, spec in profiles.items():
        locations = spec.get("locations", [])
        loc_names = [l[0] for l in locations]
        lines.append(f"[PROFILE_{pname}]")
        lines.append(f"NAME = {pname}")
        lines.append(f"LOCATIONS = {', '.join(loc_names)}")
        if spec.get("write"):
            lines.append("WRITE = true")
        lines.append("")
        all_loc_names.update(loc_names)

    # Location sections
    seen: set[str] = set()
    for pname, spec in profiles.items():
        for loc_name, dir_path in spec.get("locations", []):
            if loc_name in seen:
                continue
            seen.add(loc_name)
            lines.append(f"[LOC_{loc_name}]")
            lines.append("TYPE = DIRECTORY")
            lines.append(f"PATH = {dir_path}")
            lines.append("")

    (install / name).write_text("\n".join(lines))


def test_legacy_install_with_no_vfs_config(tmp_path: Path) -> None:
    install = tmp_path / "vanilla"
    install.mkdir()
    # No Ja2.ini at all
    layout = parse_vfs_config(install)
    assert layout.is_legacy
    assert layout.mod_content_profile == "v113-legacy"
    assert len(layout.profiles) == 1
    assert layout.profiles[0].locations[0].path == (install / "Data-1.13").resolve()


def test_legacy_install_with_ja2_ini_but_no_vfs_line(tmp_path: Path) -> None:
    install = tmp_path / "vanilla"
    _write_ja2_ini(install, vfs_config_name=None)
    layout = parse_vfs_config(install)
    assert layout.is_legacy


def test_vfs_config_referenced_but_file_missing_raises(tmp_path: Path) -> None:
    """Bug A2 — silently degrading to the legacy Data-1.13/ layout caused
    invisible failures (writes landed where the modded engine never
    reads). Now we raise so the user fixes Ja2.ini before continuing.
    """
    from mercwizard_core.vfs import VfsConfigError
    install = tmp_path / "broken"
    _write_ja2_ini(install, vfs_config_name="vfs_config.does_not_exist.ini")
    with pytest.raises(VfsConfigError, match="VFS_CONFIG_INI"):
        parse_vfs_config(install)


def test_vengeance_style_chain_picks_mod_content_profile(tmp_path: Path) -> None:
    install = tmp_path / "vengeance"
    _write_ja2_ini(install, "vfs_config.Vengeance.ini")
    _write_vfs_config(install, "vfs_config.Vengeance.ini", {
        "SlfLibs": {"locations": [("slfroot", "")]},  # System layer
        "v113": {"locations": [("datav113_dir", "Data-1.13")]},  # System layer
        "vengcore": {"locations": [
            ("datavengeance_dir", "Data-Vengeance"),
            ("datamaptiles_dir", "Data-Maps-Tiles"),
            ("dataaimv53_dir", "Data-AIMv53"),
        ]},  # Mod content
        "music": {"locations": [("datamusic_dir", "Data-Music")]},  # Media, skip
        "UserProf": {"locations": [("uprof_root", "Profiles")], "write": True},  # User
    })
    layout = parse_vfs_config(install)
    assert not layout.is_legacy
    assert layout.mod_content_profile == "vengcore"
    write_profile = layout.writable_profile()
    assert write_profile is not None
    assert len(write_profile.locations) == 3
    assert write_profile.locations[0].path == (install / "Data-Vengeance").resolve()


def test_resolve_read_walks_chain_high_to_low(tmp_path: Path) -> None:
    install = tmp_path / "vengeance"
    _write_ja2_ini(install, "vfs_config.Vengeance.ini")
    _write_vfs_config(install, "vfs_config.Vengeance.ini", {
        "v113": {"locations": [("datav113_dir", "Data-1.13")]},
        "vengcore": {"locations": [("datavengeance_dir", "Data-Vengeance")]},
    })
    # Create the file in BOTH layers — vengcore (higher) should win
    (install / "Data-1.13" / "TableData").mkdir(parents=True)
    (install / "Data-1.13" / "TableData" / "MercProfiles.xml").write_text("vanilla")
    (install / "Data-Vengeance" / "TableData").mkdir(parents=True)
    (install / "Data-Vengeance" / "TableData" / "MercProfiles.xml").write_text("vengeance")

    layout = parse_vfs_config(install)
    resolved = layout.resolve_read("TableData/MercProfiles.xml")
    assert resolved is not None
    assert resolved.read_text() == "vengeance"


def test_resolve_read_falls_through_to_lower_priority_when_missing(tmp_path: Path) -> None:
    install = tmp_path / "vengeance"
    _write_ja2_ini(install, "vfs_config.Vengeance.ini")
    _write_vfs_config(install, "vfs_config.Vengeance.ini", {
        "v113": {"locations": [("datav113_dir", "Data-1.13")]},
        "vengcore": {"locations": [("datavengeance_dir", "Data-Vengeance")]},
    })
    # Only the vanilla layer has it
    (install / "Data-1.13" / "BinaryData").mkdir(parents=True)
    (install / "Data-1.13" / "BinaryData" / "AIMBIOS.EDT").write_bytes(b"x" * 100)
    (install / "Data-Vengeance").mkdir()

    layout = parse_vfs_config(install)
    resolved = layout.resolve_read("BinaryData/AIMBIOS.EDT")
    assert resolved is not None
    assert resolved == (install / "Data-1.13" / "BinaryData" / "AIMBIOS.EDT").resolve()


def test_resolve_write_uses_existing_file_layer(tmp_path: Path) -> None:
    """Even if the mod content profile is vengcore, if a file actually
    lives in a lower-priority layer, writes go to that lower layer to
    preserve in-place modification semantics."""
    install = tmp_path / "vengeance"
    _write_ja2_ini(install, "vfs_config.Vengeance.ini")
    _write_vfs_config(install, "vfs_config.Vengeance.ini", {
        "v113": {"locations": [("datav113_dir", "Data-1.13")]},
        "vengcore": {"locations": [("datavengeance_dir", "Data-Vengeance")]},
    })
    # AIMBIOS.EDT only in v113 layer
    (install / "Data-1.13" / "BinaryData").mkdir(parents=True)
    (install / "Data-1.13" / "BinaryData" / "AIMBIOS.EDT").write_bytes(b"x" * 100)

    layout = parse_vfs_config(install)
    write_target = layout.resolve_write("BinaryData/AIMBIOS.EDT")
    assert write_target == (install / "Data-1.13" / "BinaryData" / "AIMBIOS.EDT").resolve()


def test_resolve_write_falls_back_to_mod_content_layer_for_new_files(tmp_path: Path) -> None:
    install = tmp_path / "vengeance"
    _write_ja2_ini(install, "vfs_config.Vengeance.ini")
    _write_vfs_config(install, "vfs_config.Vengeance.ini", {
        "v113": {"locations": [("datav113_dir", "Data-1.13")]},
        "vengcore": {"locations": [("datavengeance_dir", "Data-Vengeance")]},
    })

    layout = parse_vfs_config(install)
    write_target = layout.resolve_write("TableData/NewlyCreatedFile.xml")
    # Since the file doesn't exist anywhere, it goes to the mod content
    # profile's first location (Data-Vengeance).
    assert write_target == (install / "Data-Vengeance" / "TableData" / "NewlyCreatedFile.xml").resolve()


def test_all_existing_copies_returns_high_to_low(tmp_path: Path) -> None:
    install = tmp_path / "vengeance"
    _write_ja2_ini(install, "vfs_config.Vengeance.ini")
    _write_vfs_config(install, "vfs_config.Vengeance.ini", {
        "v113": {"locations": [("datav113_dir", "Data-1.13")]},
        "vengcore": {"locations": [("datavengeance_dir", "Data-Vengeance")]},
    })
    (install / "Data-1.13" / "TableData").mkdir(parents=True)
    (install / "Data-1.13" / "TableData" / "MercProfiles.xml").write_text("v")
    (install / "Data-Vengeance" / "TableData").mkdir(parents=True)
    (install / "Data-Vengeance" / "TableData" / "MercProfiles.xml").write_text("m")

    layout = parse_vfs_config(install)
    copies = layout.all_existing_copies("TableData/MercProfiles.xml")
    assert len(copies) == 2
    assert "Data-Vengeance" in str(copies[0])
    assert "Data-1.13" in str(copies[1])


def test_legacy_layout_resolve_write_for_new_file(tmp_path: Path) -> None:
    """Pre-VFS install: writes go to Data-1.13."""
    install = tmp_path / "vanilla"
    install.mkdir()
    layout = parse_vfs_config(install)
    write_target = layout.resolve_write("TableData/MercProfiles.xml")
    assert write_target == (install / "Data-1.13" / "TableData" / "MercProfiles.xml").resolve()


# ─────────────────────────────────────────────────────────────────────
#  Bug-fix tests (corrections from real-world variation research)
# ─────────────────────────────────────────────────────────────────────


def test_case_insensitive_profile_section_lookup(tmp_path: Path) -> None:
    """Vengeance's config has `PROFILES = ... pcm ...` but the section is
    `[PROFILE_PCM]`. The case-sensitive ConfigParser default silently
    dropped these. Verify we now match them."""
    install = tmp_path / "ci"
    _write_ja2_ini(install, "vfs_config.test.ini")
    # Write a config with intentionally mismatched casing
    (install / "vfs_config.test.ini").write_text("\n".join([
        "[vfs_config]",
        "PROFILES = v113, modcore, pcm",
        "",
        "[PROFILE_v113]",
        "LOCATIONS = loc_v113",
        "",
        "[PROFILE_modcore]",
        "LOCATIONS = loc_modcore",
        "",
        "[PROFILE_PCM]",           # UPPER, but PROFILES line has lowercase
        "LOCATIONS = loc_pcm",
        "",
        "[LOC_loc_v113]",
        "TYPE = DIRECTORY",
        "PATH = Data-1.13",
        "",
        "[LOC_loc_modcore]",
        "TYPE = DIRECTORY",
        "PATH = Data-Mod",
        "",
        "[LOC_loc_pcm]",
        "TYPE = DIRECTORY",
        "PATH = Data-PCM",
    ]))
    layout = parse_vfs_config(install)
    profile_names = [p.name for p in layout.profiles]
    assert "v113" in profile_names
    assert "modcore" in profile_names
    # The lowercase `pcm` reference must resolve to the uppercase section
    assert "pcm" in profile_names
    pcm_profile = next(p for p in layout.profiles if p.name == "pcm")
    assert len(pcm_profile.locations) == 1


def test_broken_vfs_config_ini_raises(tmp_path: Path) -> None:
    """SDO case (Ja2.ini references a vfs_config that doesn't exist on
    disk): post-A2, raise VfsConfigError instead of silently falling
    back. Otherwise writes via mod_content_path land in Data-1.13/
    where the engine never reads them.
    """
    from mercwizard_core.vfs import VfsConfigError
    install = tmp_path / "sdo"
    install.mkdir()
    (install / "Ja2.ini").write_text(
        "[Ja2 Settings]\n"
        "VFS_CONFIG_INI = Data-UB\\Addons\\Data-Mod-SOG69-Vietnam\\vfs_config.UB_SOG69-Vietnam.ini\n"
    )
    with pytest.raises(VfsConfigError, match="VFS_CONFIG_INI"):
        parse_vfs_config(install)


def test_malformed_vfs_config_ini_raises(tmp_path: Path) -> None:
    """A vfs_config file that exists but doesn't parse as INI must raise
    VfsConfigError, not silently fall back to legacy.
    """
    from mercwizard_core.vfs import VfsConfigError
    install = tmp_path / "malformed"
    install.mkdir()
    (install / "Ja2.ini").write_text(
        "[Ja2 Settings]\nVFS_CONFIG_INI = vfs_config.Broken.ini\n"
    )
    (install / "vfs_config.Broken.ini").write_text(
        "[vfs_config\nthis is not valid ini at all = = =\n\x00\x01\x02"
    )
    with pytest.raises(VfsConfigError, match="parse"):
        parse_vfs_config(install)


def test_resolve_write_refuses_when_vfs_config_broken(tmp_path: Path) -> None:
    """Bug-review #98: when the VFS config couldn't be parsed and we fell
    back to a legacy layout, writes must refuse rather than silently
    landing in `Data-1.13/` where the modded engine never reads them.

    The install scan tolerates the broken config (so other installs in
    the same scan still show up) and reports it via `layout.errors`,
    but any attempt to ACT on the broken install through `resolve_write`
    must raise so the user is forced to fix Ja2.ini before any merc
    edits happen.
    """
    from mercwizard_core.vfs import VfsConfigError, _legacy_layout
    install = tmp_path / "broken_then_writes"
    install.mkdir()
    (install / "Data-1.13").mkdir()
    # Simulate what `make_install_context` does when parse_vfs_config raises:
    # fall back to a legacy layout with the error captured.
    layout = _legacy_layout(install.resolve())
    layout.errors.append("VFS_CONFIG_INI references 'vfs_config.Foo.ini' but no file exists")

    with pytest.raises(VfsConfigError, match="VFS config"):
        layout.resolve_write("Data-1.13/TableData/MercProfiles.xml")


def test_resolve_write_works_on_clean_legacy_install(tmp_path: Path) -> None:
    """The error-gate above must NOT fire for a legitimate pre-VFS install
    (no Ja2.ini VFS line at all). Those installs have `errors == []` and
    should write freely to their Data-1.13/ layer."""
    install = tmp_path / "vanilla"
    install.mkdir()
    (install / "Data").mkdir()
    (install / "Data-1.13").mkdir()
    (install / "Ja2.ini").write_text("[Ja2 Settings]\nGAME_DIR = .\n")
    layout = parse_vfs_config(install)
    assert layout.errors == []
    # No raise — clean legacy installs write normally.
    target = layout.resolve_write("Data-1.13/TableData/MercProfiles.xml")
    assert target.suffix == ".xml"


def test_pre_vfs_install_without_vfs_config_line_still_falls_back(tmp_path: Path) -> None:
    """Pre-VFS installs (Ja2.ini with NO VFS_CONFIG_INI line at all) are
    legitimate legacy installs, not broken configs. They must still get a
    synthesized legacy layout — A2's raise is only for the broken-config
    cases, not the missing-line case.
    """
    install = tmp_path / "vanilla_pre_vfs"
    install.mkdir()
    (install / "Data").mkdir()
    (install / "Data-1.13").mkdir()
    (install / "Ja2.ini").write_text("[Ja2 Settings]\nGAME_DIR = .\n")
    layout = parse_vfs_config(install)
    assert layout.is_legacy
    assert layout.errors == []


def test_pre_vfs_install_synthesizes_chain_from_data_dirs(tmp_path: Path) -> None:
    """Pre-VFS install with Data/ and Data-1.13/ should synthesize a
    two-layer chain (matching what the old engine actually reads)."""
    install = tmp_path / "renegade"
    install.mkdir()
    (install / "Data").mkdir()
    (install / "Data-1.13").mkdir()
    (install / "Data-Renegade").mkdir()
    (install / "Ja2.ini").write_text(
        "[Ja2 Settings]\n"
        "CUSTOM_DATA_LOCATION = Data-Renegade\n"
    )
    layout = parse_vfs_config(install)
    assert layout.is_legacy
    # Expect three profiles in order: data-legacy, v113-legacy, custom-Data-Renegade
    profile_names = [p.name for p in layout.profiles]
    assert profile_names == ["data-legacy", "v113-legacy", "custom-Data-Renegade"]
    # The mod content profile is the topmost = custom one
    assert layout.mod_content_profile == "custom-Data-Renegade"


def test_pre_vfs_install_only_data_dir(tmp_path: Path) -> None:
    """Pure pre-1.13 vanilla: only Data/ exists, no Data-1.13/."""
    install = tmp_path / "gold"
    install.mkdir()
    (install / "Data").mkdir()
    layout = parse_vfs_config(install)
    assert layout.is_legacy
    assert [p.name for p in layout.profiles] == ["data-legacy"]
    assert layout.mod_content_profile == "data-legacy"


def test_overlay_profile_is_skipped_for_mod_content(tmp_path: Path) -> None:
    """DL/UC pattern: mod core + content overlay on top. Pick the core."""
    install = tmp_path / "dl"
    _write_ja2_ini(install, "vfs_config.DL113.ini")
    _write_vfs_config(install, "vfs_config.DL113.ini", {
        "SlfLibs": {"locations": [("slfroot", "")]},
        "v113": {"locations": [("v113_dir", "Data-1.13")]},
        "DL113": {"locations": [("dl_dir", "Data-DL113")]},
        "FSGraphics": {"locations": [("fs_dir", "Data-FSGraphics")]},
        "UserProf": {"locations": [("uprof", "Profiles")], "write": True},
    })
    # Create the directories so MercProfiles.xml probing can find them
    (install / "Data-DL113" / "TableData").mkdir(parents=True)
    (install / "Data-DL113" / "TableData" / "MercProfiles.xml").write_text("dl")
    (install / "Data-FSGraphics").mkdir()
    (install / "Profiles").mkdir()

    layout = parse_vfs_config(install)
    # FSGraphics is the topmost non-system, but it's an overlay — skip it
    assert layout.mod_content_profile == "DL113"


def test_mod_content_probing_picks_profile_with_merc_profiles_xml(tmp_path: Path) -> None:
    """When multiple non-system profiles exist, prefer the one that
    actually contains MercProfiles.xml on disk."""
    install = tmp_path / "multi"
    _write_ja2_ini(install, "vfs_config.test.ini")
    _write_vfs_config(install, "vfs_config.test.ini", {
        "v113": {"locations": [("v113_dir", "Data-1.13")]},
        "ModCore": {"locations": [("core", "Data-Core")]},
        "ModExtras": {"locations": [("extras", "Data-Extras")]},
        "UserProf": {"locations": [("uprof", "Profiles")], "write": True},
    })
    # Only ModCore has MercProfiles.xml; ModExtras is the topmost
    (install / "Data-Core" / "TableData").mkdir(parents=True)
    (install / "Data-Core" / "TableData" / "MercProfiles.xml").write_text("x")
    (install / "Data-Extras").mkdir()
    (install / "Profiles").mkdir()

    layout = parse_vfs_config(install)
    assert layout.mod_content_profile == "ModCore"


# ─────────────────────────────────────────────────────────────────────
#  compute_vfs_mismatch — bug-review B5
# ─────────────────────────────────────────────────────────────────────


def test_vfs_mismatch_false_when_no_config_bound(tmp_path: Path) -> None:
    """Install with no specific vfs_config_path (legacy registration)
    can never mismatch — there's no expectation to violate."""
    install = tmp_path / "legacy"
    _write_ja2_ini(install, "vfs_config.AIMNAS.ini")
    assert compute_vfs_mismatch(install, None) is False


def test_vfs_mismatch_false_when_ja2_ini_missing(tmp_path: Path) -> None:
    """No Ja2.ini on disk = can't tell. Don't false-positive (the user
    might be mid-install or running a portable copy without one)."""
    install = tmp_path / "no_ini"
    install.mkdir()
    cfg = install / "vfs_config.AIMNAS.ini"
    cfg.touch()
    assert compute_vfs_mismatch(install, cfg) is False


def test_vfs_mismatch_false_when_paths_match(tmp_path: Path) -> None:
    """Same config bound and named in Ja2.ini = no mismatch."""
    install = tmp_path / "match"
    _write_ja2_ini(install, "vfs_config.AIMNAS.ini")
    cfg = install / "vfs_config.AIMNAS.ini"
    cfg.touch()
    assert compute_vfs_mismatch(install, cfg) is False


def test_vfs_mismatch_true_when_paths_differ(tmp_path: Path) -> None:
    """The B5 failure mode: install registered to Wildfire but Ja2.ini
    still names AIMNAS. Engine reads AIMNAS; wizard writes Wildfire;
    user's edits silently miss the engine. Surface as mismatch."""
    install = tmp_path / "differ"
    _write_ja2_ini(install, "vfs_config.AIMNAS.ini")
    wildfire_cfg = install / "vfs_config.Wildfire.ini"
    wildfire_cfg.touch()
    assert compute_vfs_mismatch(install, wildfire_cfg) is True


def test_vfs_mismatch_true_when_ja2_ini_has_no_vfs_line(tmp_path: Path) -> None:
    """Ja2.ini exists but has no VFS_CONFIG_INI line (legacy-mode
    Ja2.ini), and the registration is bound to a specific config. The
    user picked AIMNAS but the engine is in legacy mode — apply will
    add the line."""
    install = tmp_path / "no_vfs_line"
    install.mkdir()
    (install / "Ja2.ini").write_text("[Ja2 Settings]\nCD = C\n")
    cfg = install / "vfs_config.AIMNAS.ini"
    cfg.touch()
    assert compute_vfs_mismatch(install, cfg) is True


def test_vfs_mismatch_false_with_slash_direction_difference(tmp_path: Path) -> None:
    """Ja2.ini commonly writes backslash paths on Windows; the bound
    config uses native slashes via pathlib. The comparison normalizes
    both to forward slashes so a slash-direction mismatch isn't
    surfaced as a real disagreement."""
    install = tmp_path / "slashy"
    install.mkdir()
    # Use a nested config path to exercise the slash-normalization
    nested_dir = install / "Addons" / "AIMNAS"
    nested_dir.mkdir(parents=True)
    cfg = nested_dir / "vfs_config.AIMNAS.ini"
    cfg.touch()
    (install / "Ja2.ini").write_text(
        "[Ja2 Settings]\n"
        r"VFS_CONFIG_INI = Addons\AIMNAS\vfs_config.AIMNAS.ini" + "\n"
    )
    assert compute_vfs_mismatch(install, cfg) is False


def test_vfs_mismatch_false_case_insensitive_path(tmp_path: Path) -> None:
    """Ja2.ini path values are case-insensitive on Windows. A case-only
    difference between the bound config and the Ja2.ini line isn't a
    real mismatch."""
    install = tmp_path / "case"
    install.mkdir()
    cfg = install / "vfs_config.AIMNAS.ini"
    cfg.touch()
    (install / "Ja2.ini").write_text(
        "[Ja2 Settings]\nVFS_CONFIG_INI = vfs_config.aimnas.ini\n"
    )
    assert compute_vfs_mismatch(install, cfg) is False


# ──────────────────────────────────────────────────────────────────────────
#  PROFILE_ROOT + engine write profile (INI-editor Phase 1, Step 0)
# ──────────────────────────────────────────────────────────────────────────


def _write_stock_113_vfs_config(install: Path) -> None:
    """A faithful miniature of the stock 1.13 vfs_config.JA2113.ini shape:
    the engine's write profile mounts an empty-PATH location whose real
    directory comes from PROFILE_ROOT (the UserProf pattern)."""
    (install / "Data").mkdir(parents=True, exist_ok=True)
    (install / "Data-1.13").mkdir(parents=True, exist_ok=True)
    (install / "vfs_config.JA2113.ini").write_text(
        "[vfs_config]\n"
        "PROFILES = Vanilla, v113, UserProf\n"
        "\n"
        "[PROFILE_Vanilla]\n"
        "NAME = Vanilla Dirs\n"
        "LOCATIONS = data_dir\n"
        "PROFILE_ROOT = \n"
        "\n"
        "[PROFILE_v113]\n"
        "NAME = v1.13\n"
        "LOCATIONS = datav113_dir\n"
        "PROFILE_ROOT = \n"
        "\n"
        "[PROFILE_UserProf]\n"
        "NAME = Player Profile\n"
        "LOCATIONS = uprof_root\n"
        "PROFILE_ROOT = Profiles\\UserProfile_JA2113\n"
        "WRITE = true\n"
        "\n"
        "[LOC_data_dir]\n"
        "TYPE = DIRECTORY\n"
        "PATH = Data\n"
        "\n"
        "[LOC_datav113_dir]\n"
        "TYPE = DIRECTORY\n"
        "PATH = Data-1.13\n"
        "\n"
        "[LOC_uprof_root]\n"
        "TYPE = DIRECTORY\n"
        "PATH = \n"
    )
    _write_ja2_ini(install, "vfs_config.JA2113.ini")


def test_profile_root_gives_write_profile_a_location(tmp_path: Path) -> None:
    """The stock UserProf pattern (empty PATH + PROFILE_ROOT) must yield a
    profile with a real mounted location — previously the empty PATH made
    the location vanish entirely."""
    install = tmp_path / "stock113"
    _write_stock_113_vfs_config(install)
    layout = parse_vfs_config(install)
    uprof = next(p for p in layout.profiles if p.name == "UserProf")
    assert uprof.write_allowed is True
    assert uprof.profile_root == (install / "Profiles" / "UserProfile_JA2113").resolve()
    assert len(uprof.locations) == 1
    assert uprof.locations[0].path == uprof.profile_root


def test_engine_write_profile_picks_write_true_profile(tmp_path: Path) -> None:
    install = tmp_path / "stock113"
    _write_stock_113_vfs_config(install)
    layout = parse_vfs_config(install)
    ewp = layout.engine_write_profile()
    assert ewp is not None
    assert ewp.name == "UserProf"


def test_resolve_override_write_targets_profile_root(tmp_path: Path) -> None:
    """The INI-override write path must land under the engine write
    profile's PROFILE_ROOT — even when the same file already exists in a
    lower layer (no in-place fallback, unlike resolve_write)."""
    install = tmp_path / "stock113"
    _write_stock_113_vfs_config(install)
    # Base copy exists in Data-1.13 — must NOT attract the override write.
    (install / "Data-1.13" / "Ja2_Options.ini").write_text("[x]\nA = 1\n")
    layout = parse_vfs_config(install)
    target = layout.resolve_override_write("Ja2_Options.ini")
    assert target == (install / "Profiles" / "UserProfile_JA2113" / "Ja2_Options.ini").resolve()


def test_resolve_override_write_refuses_on_legacy_layout(tmp_path: Path) -> None:
    """Legacy installs have no engine write profile — overrides must be
    refused, not guessed."""
    from mercwizard_core.vfs import VfsConfigError

    install = tmp_path / "legacy"
    install.mkdir()
    (install / "Data-1.13").mkdir()
    _write_ja2_ini(install)  # no VFS_CONFIG_INI line
    layout = parse_vfs_config(install)
    assert layout.engine_write_profile() is None
    with pytest.raises(VfsConfigError):
        layout.resolve_override_write("Ja2_Options.ini")


def test_profile_root_does_not_disturb_mod_content_selection(tmp_path: Path) -> None:
    """UserProf is a system profile — gaining a location must not make it
    the mod-content pick, and resolve_write for mod files stays in-place."""
    install = tmp_path / "stock113"
    _write_stock_113_vfs_config(install)
    (install / "Data-1.13" / "TableData").mkdir(parents=True)
    (install / "Data-1.13" / "TableData" / "MercProfiles.xml").write_text("<x/>")
    layout = parse_vfs_config(install)
    assert layout.mod_content_profile == "v113"
    assert layout.resolve_write("TableData/MercProfiles.xml") == (
        install / "Data-1.13" / "TableData" / "MercProfiles.xml"
    ).resolve()
