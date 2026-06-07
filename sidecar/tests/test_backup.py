"""Tests for backup snapshot/restore."""
from __future__ import annotations

from pathlib import Path

from mercwizard_core import backup


def _make_fake_install(install_root: Path) -> tuple[Path, Path]:
    """Create a fake install with two files we can snapshot."""
    table_data = install_root / "Data-1.13" / "TableData"
    table_data.mkdir(parents=True)
    profiles_xml = table_data / "MercProfiles.xml"
    aim_xml = table_data / "AIMAvailability.xml"
    profiles_xml.write_text("<PROFILES />")
    aim_xml.write_text("<AIM_AVAILABLES />")
    return profiles_xml, aim_xml


def test_snapshot_copies_files(tmp_path: Path) -> None:
    install = tmp_path / "install"
    profiles_xml, aim_xml = _make_fake_install(install)
    backup_base = tmp_path / "backups"

    entry = backup.snapshot(
        install_root=install,
        install_id="testinst",
        files_to_back_up=[profiles_xml, aim_xml],
        reason="testing",
        base=backup_base,
    )

    assert entry.id.endswith("__testing")
    assert "Data-1.13/TableData/MercProfiles.xml" in entry.files
    assert "Data-1.13/TableData/AIMAvailability.xml" in entry.files
    snapshot_dir = entry.root_dir / "snapshot"
    assert (snapshot_dir / "Data-1.13" / "TableData" / "MercProfiles.xml").is_file()
    assert (entry.root_dir / "manifest.json").is_file()


def test_snapshot_skips_missing_files(tmp_path: Path) -> None:
    install = tmp_path / "install"
    profiles_xml, _ = _make_fake_install(install)
    backup_base = tmp_path / "backups"

    missing = install / "Data-1.13" / "BinaryData" / "AIMBIOS.EDT"
    entry = backup.snapshot(
        install_root=install,
        install_id="t",
        files_to_back_up=[profiles_xml, missing],
        reason="mixed",
        base=backup_base,
    )
    # Only the existing file is backed up
    assert len(entry.files) == 1
    assert "MercProfiles.xml" in entry.files[0]


def test_list_backups_sorted_newest_first(tmp_path: Path) -> None:
    import time
    install = tmp_path / "install"
    profiles_xml, _ = _make_fake_install(install)
    backup_base = tmp_path / "backups"

    backup.snapshot(install, "t", [profiles_xml], "first", base=backup_base)
    time.sleep(1.05)  # ensure timestamp difference (suffix granularity = 1s)
    backup.snapshot(install, "t", [profiles_xml], "second", base=backup_base)

    entries = backup.list_backups("t", base=backup_base)
    assert len(entries) == 2
    assert entries[0].reason == "second"
    assert entries[1].reason == "first"


def test_restore_replaces_files(tmp_path: Path) -> None:
    install = tmp_path / "install"
    profiles_xml, _ = _make_fake_install(install)
    backup_base = tmp_path / "backups"
    original_content = profiles_xml.read_text()

    entry = backup.snapshot(install, "t", [profiles_xml], "before-edit", base=backup_base)

    # Modify the file
    profiles_xml.write_text("<DIFFERENT />")
    assert profiles_xml.read_text() != original_content

    restored_count = backup.restore(entry.id, "t", install, base=backup_base)
    assert restored_count == 1
    assert profiles_xml.read_text() == original_content


def test_files_for_merc_includes_all_artifact_paths(tmp_path: Path) -> None:
    install = tmp_path / "install"
    files = backup.files_for_merc(install, ui_index=220, face_index=220)
    paths_str = [str(f) for f in files]
    assert any("MercProfiles.xml" in p for p in paths_str)
    assert any("AIMAvailability.xml" in p for p in paths_str)
    assert any("MercStartingGear.xml" in p for p in paths_str)
    assert any("AIMBIOS.EDT" in p for p in paths_str)
    assert any("MERCBIOS.EDT" in p for p in paths_str)
    assert any("MercEdt" in p and "220.EDT" in p for p in paths_str)
    assert any("220.sti" in p for p in paths_str)
    assert any("BigFaces" in p for p in paths_str)


# ─────────────────────────────────────────────────────────────────────
#  Regression: BigItems backup must NOT use substring slot match
# ─────────────────────────────────────────────────────────────────────
#
# A user-reported bug: a Duplicate slot 0 → 216 backed up 372
# unrelated BigItems STI files. Root cause: the matcher used
# `str(ui_index) in p.name` (substring), so slot 0 matched every
# filename containing the digit '0' — P1ITEM101, P1ITEM102, …
# P1ITEM209, …, P1ITEM2200, …
#
# Fix: whole-stem equality only. These tests lock the new behavior in
# so a future "simplify the matcher" refactor doesn't reintroduce the
# overreach.


def test_files_for_merc_bigitems_does_not_substring_match_slot_0(tmp_path: Path) -> None:
    """Slot 0 must not pull in BigItems files just because they contain
    the digit '0' in an unrelated item index (P1ITEM101, P1ITEM200, etc.)."""
    install = tmp_path / "install"
    big_items = install / "Data-1.13" / "BigItems"
    big_items.mkdir(parents=True)
    # Create a realistic BigItems directory: items indexed 100..210 plus
    # one file at the literal slot-0 convention. Pre-fix, the substring
    # match would have returned ALL of these (every one contains '0' in
    # the item index). Post-fix, only the slot-0 conventional names match.
    for idx in range(100, 211):
        (big_items / f"P1ITEM{idx}.STI").write_bytes(b"")
    # Slot-0 conventional names that SHOULD be included:
    (big_items / "0.sti").write_bytes(b"")
    (big_items / "P1ITEM0.STI").write_bytes(b"")

    files = backup.files_for_merc(install, ui_index=0, face_index=None)
    bigitems_matches = [
        f for f in files
        if "BigItems" in str(f) and f.exists()
    ]
    matched_names = sorted(p.name for p in bigitems_matches)

    # Only the two conventional slot-0 files. Not P1ITEM101 / P1ITEM200 /
    # P1ITEM210 / etc.
    assert matched_names == ["0.sti", "P1ITEM0.STI"], (
        f"BigItems backup over-matched: got {len(matched_names)} files: "
        f"{matched_names[:10]}{'…' if len(matched_names) > 10 else ''}"
    )


def test_files_for_merc_bigitems_slot_216_does_not_match_item_2160(tmp_path: Path) -> None:
    """Slot 216 must not pull in P1ITEM2160.sti just because '216' is a
    prefix substring. Even multi-digit slots need whole-stem equality."""
    install = tmp_path / "install"
    big_items = install / "Data-1.13" / "BigItems"
    big_items.mkdir(parents=True)
    # Adjacent item indices that share '216' as a substring.
    (big_items / "P1ITEM215.STI").write_bytes(b"")
    (big_items / "P1ITEM216.STI").write_bytes(b"")  # this one IS for slot 216
    (big_items / "P1ITEM2160.STI").write_bytes(b"")
    (big_items / "P1ITEM2161.STI").write_bytes(b"")

    files = backup.files_for_merc(install, ui_index=216, face_index=None)
    bigitems_matches = [
        f for f in files
        if "BigItems" in str(f) and f.exists()
    ]
    matched_names = sorted(p.name for p in bigitems_matches)
    assert matched_names == ["P1ITEM216.STI"], (
        f"BigItems backup over-matched on slot 216: {matched_names}"
    )


def test_files_for_merc_bigitems_accepts_uppercase_and_bare_number(tmp_path: Path) -> None:
    """Both `<slot>.sti` and `P1ITEM<slot>.sti` are accepted (the two
    conventions mods use), and the match is case-insensitive.

    NB: Windows NTFS is case-insensitive, so `P1ITEM42.STI` and
    `p1item42.sti` resolve to the SAME on-disk file (last write wins
    for the cased name). The case-insensitive matcher just has to find
    whichever casing happens to be on disk. The test only creates one
    of each distinct file."""
    install = tmp_path / "install"
    big_items = install / "Data-1.13" / "BigItems"
    big_items.mkdir(parents=True)
    (big_items / "42.sti").write_bytes(b"")
    (big_items / "P1ITEM42.STI").write_bytes(b"")
    (big_items / "BIGITEM42.STI").write_bytes(b"")  # alt prefix
    (big_items / "P1ITEM_42.STI").write_bytes(b"")  # underscore variant (NOT a convention)
    (big_items / "GUN42.STI").write_bytes(b"")  # unrelated

    files = backup.files_for_merc(install, ui_index=42, face_index=None)
    bigitems_matches = sorted(
        p.name for p in files
        if "BigItems" in str(p) and p.exists()
    )
    # Three conventional variants accepted, none of the unrelated ones.
    expected = sorted(["42.sti", "P1ITEM42.STI", "BIGITEM42.STI"])
    assert bigitems_matches == expected


def test_delete_backup_removes_folder(tmp_path: Path) -> None:
    install = tmp_path / "install"
    profiles_xml, _ = _make_fake_install(install)
    backup_base = tmp_path / "backups"
    entry = backup.snapshot(install, "t", [profiles_xml], "doomed", base=backup_base)
    assert entry.root_dir.is_dir()
    assert backup.delete_backup(entry.id, "t", base=backup_base) is True
    assert not entry.root_dir.exists()


# ─────────────────────────────────────────────────────────────────────
#  Files-created tracking + restore that deletes them
# ─────────────────────────────────────────────────────────────────────


def test_record_files_created_appends_to_manifest(tmp_path: Path) -> None:
    install = tmp_path / "install"
    profiles_xml, _ = _make_fake_install(install)
    base = tmp_path / "backups"
    entry = backup.snapshot(install, "id1", [profiles_xml], "test", base=base)

    new_file_1 = install / "newdir" / "created1.ogg"
    new_file_2 = install / "newdir" / "created2.ogg"
    added = backup.record_files_created(entry.id, "id1", [new_file_1, new_file_2], base=base)
    assert added == 2

    # Manifest now lists both files
    import json
    manifest = json.loads((entry.root_dir / "manifest.json").read_text())
    assert len(manifest["files_created"]) == 2


def test_restore_deletes_files_that_were_created_during_op(tmp_path: Path) -> None:
    """The exact scenario that bit us on the slot-199 import: orphan files."""
    install = tmp_path / "install"
    profiles_xml, _ = _make_fake_install(install)
    base = tmp_path / "backups"

    # 1. Capture pre-op snapshot
    entry = backup.snapshot(install, "id1", [profiles_xml], "import_slot_199", base=base)

    # 2. Simulate the op writing NEW files at slot-199 paths
    orphan_1 = install / "Data-1.13" / "Battlesnds" / "199_ATTN.ogg"
    orphan_2 = install / "Data-1.13" / "NPC_Speech" / "199_000.ogg"
    orphan_1.parent.mkdir(parents=True)
    orphan_2.parent.mkdir(parents=True)
    orphan_1.write_bytes(b"hit")
    orphan_2.write_bytes(b"hello")

    # 3. Op also modifies profiles_xml (already in snapshot)
    profiles_xml.write_text("<PROFILES>modified</PROFILES>")

    # 4. Record the new files in the snapshot
    backup.record_files_created(entry.id, "id1", [orphan_1, orphan_2], base=base)

    # 5. Restore
    count = backup.restore(entry.id, "id1", install, base=base)

    # Profiles.xml is back to original
    assert profiles_xml.read_text() == "<PROFILES />"
    # Orphan files are gone
    assert not orphan_1.is_file()
    assert not orphan_2.is_file()
    # Count covers both restored (1) and deleted (2) = 3
    assert count == 3


def test_record_files_created_is_noop_for_missing_backup(tmp_path: Path) -> None:
    """If the backup ID doesn't exist, record_files_created returns 0 silently."""
    base = tmp_path / "backups"
    added = backup.record_files_created("does_not_exist", "id1", [Path("/tmp/x.ogg")], base=base)
    assert added == 0
