"""Tests for backup snapshot/restore."""
from __future__ import annotations

from pathlib import Path
import json
import subprocess
import threading
from contextlib import contextmanager

import pytest

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

    assert "__testing_" in entry.id
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


def test_same_reason_same_clock_snapshots_reserve_distinct_pinned_material(tmp_path: Path, monkeypatch) -> None:
    """A backup-id collision must never merge two rollback manifests."""
    install = tmp_path / "install"
    profiles_xml, _ = _make_fake_install(install)
    backup_base = tmp_path / "backups"
    monkeypatch.setattr(backup, "_make_backup_id", lambda reason: "fixed__same")

    # The exclusive mkdir remains the final guard even under a pathological
    # deterministic id generator.  The second call exhausts rather than
    # silently reusing the first snapshot.
    first = backup.snapshot(install, "t", [profiles_xml], "same", base=backup_base, pinned=True)
    with pytest.raises(FileExistsError):
        backup.snapshot(install, "t", [profiles_xml], "same", base=backup_base, pinned=True)
    assert first.pinned and (first.root_dir / "manifest.json").is_file()


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


@pytest.mark.parametrize("delete_via", ["delete", "prune"])
def test_restore_holds_snapshot_lock_until_source_copy_completes(
    tmp_path: Path,
    monkeypatch,
    delete_via: str,
) -> None:
    """A retention/delete writer cannot remove restore's source mid-copy."""
    install = tmp_path / "install"
    profiles_xml, _ = _make_fake_install(install)
    secondary = install / "Data-1.13" / "TableData" / "Secondary.xml"
    secondary.parent.mkdir(parents=True, exist_ok=True)
    secondary.write_text("<SECONDARY>original</SECONDARY>")
    base = tmp_path / "app"
    entry = backup.snapshot(
        install, "i1", [profiles_xml, secondary], "restore-race",
        base=base, auto_prune=False,
    )
    profiles_xml.write_text("<PROFILES>changed</PROFILES>")
    secondary.write_text("<SECONDARY>changed</SECONDARY>")

    first_target_written = threading.Event()
    continue_restore = threading.Event()
    delete_entered = threading.Event()
    real_write = backup.write_bytes_atomic
    real_delete = backup._delete_backup_locked

    def pause_after_first_live_write(path: Path, payload: bytes) -> None:
        real_write(path, payload)
        if threading.current_thread().name == "restore" and not first_target_written.is_set():
            first_target_written.set()
            assert continue_restore.wait(timeout=5)

    def observe_delete(*args, **kwargs):
        delete_entered.set()
        return real_delete(*args, **kwargs)

    monkeypatch.setattr(backup, "write_bytes_atomic", pause_after_first_live_write)
    monkeypatch.setattr(backup, "_delete_backup_locked", observe_delete)
    restored: dict[str, object] = {}
    deleted: dict[str, object] = {}

    def run_restore() -> None:
        try:
            restored["count"] = backup.restore(entry.id, "i1", install, base=base)
        except Exception as exc:  # pragma: no cover - assertion below reports it
            restored["error"] = exc

    def run_delete() -> None:
        if delete_via == "delete":
            deleted["result"] = backup.delete_backup(entry.id, "i1", base=base)
        else:
            deleted["result"] = backup.prune_backups("i1", keep=0, base=base)

    restore_worker = threading.Thread(target=run_restore, name="restore")
    delete_worker = threading.Thread(target=run_delete, name=delete_via)
    restore_worker.start()
    assert first_target_written.wait(timeout=5)
    delete_worker.start()
    try:
        # If restore does not share the per-snapshot lock, this reaches the
        # destructive helper while restore is intentionally paused mid-copy.
        assert not delete_entered.wait(timeout=0.25)
    finally:
        continue_restore.set()
        restore_worker.join(timeout=5)
        delete_worker.join(timeout=5)

    assert not restore_worker.is_alive() and not delete_worker.is_alive()
    assert "error" not in restored
    assert restored["count"] == 2
    assert profiles_xml.read_text() == "<PROFILES />"
    assert secondary.read_text() == "<SECONDARY>original</SECONDARY>"
    assert deleted["result"] is (True if delete_via == "delete" else 1)
    assert not entry.root_dir.exists()


@pytest.mark.parametrize("unsafe_created_path", [
    "..\\outside.ogg",
    "C:\\outside.ogg",
])
def test_restore_rejects_unsafe_created_target_before_live_write(
    tmp_path: Path,
    unsafe_created_path: str,
) -> None:
    """A bad cleanup path must not allow even the first snapshot copy."""
    install = tmp_path / "install"
    profiles_xml, _ = _make_fake_install(install)
    base = tmp_path / "app"
    entry = backup.snapshot(install, "i1", [profiles_xml], "bad-cleanup", base=base)
    profiles_xml.write_text("<PROFILES>changed</PROFILES>")
    manifest_path = entry.root_dir / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["files_created"] = [unsafe_created_path]
    manifest_path.write_text(json.dumps(manifest))

    with pytest.raises(ValueError, match="files_created"):
        backup.restore(entry.id, "i1", install, base=base)

    assert profiles_xml.read_text() == "<PROFILES>changed</PROFILES>"


def test_restore_accepts_legacy_absolute_created_path_within_install(tmp_path: Path) -> None:
    install = tmp_path / "install"
    profiles_xml, _ = _make_fake_install(install)
    base = tmp_path / "app"
    entry = backup.snapshot(install, "i1", [profiles_xml], "legacy-cleanup", base=base)
    profiles_xml.write_text("<PROFILES>changed</PROFILES>")
    created = install / "Data-1.13" / "Speech" / "new.ogg"
    created.parent.mkdir(parents=True, exist_ok=True)
    created.write_bytes(b"new")
    manifest_path = entry.root_dir / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["files_created"] = [str(created.resolve())]
    manifest_path.write_text(json.dumps(manifest))

    assert backup.restore(entry.id, "i1", install, base=base) == 2
    assert profiles_xml.read_text() == "<PROFILES />"
    assert not created.exists()


def test_restore_rejects_static_link_escape_before_live_write(tmp_path: Path) -> None:
    """Final resolution catches a pre-existing link from the install outward."""
    install = tmp_path / "install"
    profiles_xml, _ = _make_fake_install(install)
    base = tmp_path / "app"
    entry = backup.snapshot(install, "i1", [profiles_xml], "link-cleanup", base=base)
    profiles_xml.write_text("<PROFILES>changed</PROFILES>")
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "new.ogg").write_bytes(b"outside")
    link = install / "Data-1.13" / "linked-outside"
    try:
        link.symlink_to(outside, target_is_directory=True)
    except OSError as exc:
        junction = subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(link), str(outside)],
            capture_output=True,
            text=True,
            check=False,
        )
        if junction.returncode != 0:
            pytest.skip(f"directory links unavailable: {exc}; {junction.stderr}")
    manifest_path = entry.root_dir / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["files_created"] = ["Data-1.13/linked-outside/new.ogg"]
    manifest_path.write_text(json.dumps(manifest))

    with pytest.raises(ValueError, match="files_created"):
        backup.restore(entry.id, "i1", install, base=base)

    assert profiles_xml.read_text() == "<PROFILES>changed</PROFILES>"
    assert (outside / "new.ogg").read_bytes() == b"outside"


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


def test_prune_keeps_pinned_snapshot_and_limits_ordinary_snapshots(tmp_path: Path) -> None:
    """An active Voice Lab Undo must survive the normal retention sweep."""
    install = tmp_path / "install"
    profiles_xml, _ = _make_fake_install(install)
    base = tmp_path / "app"

    pinned = backup.snapshot(
        install, "i1", [profiles_xml], "voice_deploy", base=base, pinned=True,
    )
    for index in range(55):
        backup.snapshot(install, "i1", [profiles_xml], f"ordinary_{index}", base=base)

    entries = backup.list_backups("i1", base=base)
    assert any(entry.id == pinned.id and entry.pinned for entry in entries)
    assert sum(not entry.pinned for entry in entries) == backup.DEFAULT_MAX_BACKUPS_PER_INSTALL


def test_legacy_manifest_defaults_to_unpinned(tmp_path: Path) -> None:
    """Older manifests have no pinned key and must remain readable."""
    root = backup.backups_dir("i1", tmp_path / "app") / "legacy"
    root.mkdir(parents=True)
    (root / "manifest.json").write_text(json.dumps({
        "id": "legacy",
        "timestamp": "2026-01-01T00:00:00+00:00",
        "install_id": "i1",
        "reason": "old",
        "files": [],
        "total_size_bytes": 0,
    }))

    [entry] = backup.list_backups("i1", base=tmp_path / "app")
    assert entry.pinned is False


def test_set_backup_pinned_writes_manifest_atomically(tmp_path: Path, monkeypatch) -> None:
    """A failed manual unpin leaves both the manifest and returned state unchanged."""
    install = tmp_path / "install"
    profiles_xml, _ = _make_fake_install(install)
    base = tmp_path / "app"
    entry = backup.snapshot(install, "i1", [profiles_xml], "voice", base=base, pinned=True)
    before = (entry.root_dir / "manifest.json").read_bytes()

    def fail_write(_path: Path, _data: bytes) -> None:
        raise OSError("simulated durable-write failure")

    monkeypatch.setattr(backup, "write_bytes_atomic", fail_write)
    try:
        backup.set_backup_pinned(entry.id, "i1", False, base=base)
    except OSError:
        pass
    else:  # pragma: no cover - makes the intended failure explicit
        raise AssertionError("set_backup_pinned swallowed an atomic-write failure")

    assert (entry.root_dir / "manifest.json").read_bytes() == before


def test_set_backup_pinned_can_unpin_without_losing_manifest_metadata(tmp_path: Path) -> None:
    install = tmp_path / "install"
    profiles_xml, _ = _make_fake_install(install)
    base = tmp_path / "app"
    entry = backup.snapshot(install, "i1", [profiles_xml], "voice", base=base, pinned=True)
    manifest_path = entry.root_dir / "manifest.json"
    data = json.loads(manifest_path.read_text())
    data["future_metadata"] = {"keep": "me"}
    manifest_path.write_text(json.dumps(data))

    updated = backup.set_backup_pinned(entry.id, "i1", False, base=base)

    assert updated.pinned is False
    persisted = json.loads(manifest_path.read_text())
    assert persisted["pinned"] is False
    assert persisted["future_metadata"] == {"keep": "me"}


def test_manifest_mutations_survive_forced_interleaving(tmp_path: Path, monkeypatch) -> None:
    """Pin and created-file RMW operations share one per-backup process lock."""
    install = tmp_path / "install"
    profiles_xml, _ = _make_fake_install(install)
    base = tmp_path / "app"
    entry = backup.snapshot(install, "i1", [profiles_xml], "voice", base=base, pinned=True)
    first_inside_write = threading.Event()
    release_first = threading.Event()
    real_write = backup.write_bytes_atomic

    def block_first_write(path: Path, data: bytes) -> None:
        if threading.current_thread().name == "unpin":
            first_inside_write.set()
            assert release_first.wait(timeout=5)
        real_write(path, data)

    monkeypatch.setattr(backup, "write_bytes_atomic", block_first_write)
    created = install / "Data-1.13" / "Speech" / "15" / "001.ogg"
    errors: list[BaseException] = []

    def unpin() -> None:
        try:
            backup.set_backup_pinned(entry.id, "i1", False, base=base)
        except BaseException as exc:  # pragma: no cover - assertion below reports it
            errors.append(exc)

    def record_created() -> None:
        try:
            backup.record_files_created(entry.id, "i1", [created], base=base)
        except BaseException as exc:  # pragma: no cover - assertion below reports it
            errors.append(exc)

    first = threading.Thread(target=unpin, name="unpin")
    second = threading.Thread(target=record_created, name="record")
    first.start()
    assert first_inside_write.wait(timeout=5)
    second.start()
    release_first.set()
    first.join(timeout=5)
    second.join(timeout=5)

    assert not first.is_alive() and not second.is_alive()
    assert not errors
    persisted = json.loads((entry.root_dir / "manifest.json").read_text())
    assert persisted["pinned"] is False
    assert str(created).replace("\\", "/") in persisted["files_created"]


def test_prune_rechecks_pin_under_sibling_lock_before_delete(tmp_path: Path, monkeypatch) -> None:
    """A snapshot pinned after prune selects it must survive the delete pass."""
    install = tmp_path / "install"
    profiles_xml, _ = _make_fake_install(install)
    base = tmp_path / "app"
    victim = backup.snapshot(install, "i1", [profiles_xml], "victim", base=base, auto_prune=False)
    backup.snapshot(install, "i1", [profiles_xml], "newer", base=base, auto_prune=False)
    selected = threading.Event()
    release = threading.Event()
    real_lock = backup._backup_lock

    @contextmanager
    def delay_prune_lock(install_id: str, backup_id: str, base=None):
        if backup_id == victim.id and threading.current_thread().name == "prune":
            selected.set()
            assert release.wait(timeout=5)
        with real_lock(install_id, backup_id, base=base):
            yield

    monkeypatch.setattr(backup, "_backup_lock", delay_prune_lock)
    worker = threading.Thread(
        target=lambda: backup.prune_backups("i1", keep=1, base=base), name="prune",
    )
    worker.start()
    assert selected.wait(timeout=5)
    backup.set_backup_pinned(victim.id, "i1", True, base=base)
    release.set()
    worker.join(timeout=5)

    assert not worker.is_alive()
    assert (victim.root_dir / "manifest.json").is_file()
    assert backup.list_backups("i1", base=base)[-1].pinned is True


def test_backup_lock_is_sibling_and_manual_delete_succeeds(tmp_path: Path) -> None:
    install = tmp_path / "install"
    profiles_xml, _ = _make_fake_install(install)
    base = tmp_path / "app"
    entry = backup.snapshot(install, "i1", [profiles_xml], "delete", base=base)

    lock_path = backup._backup_lock_path("i1", entry.id, base=base)
    assert lock_path.parent != entry.root_dir
    assert entry.root_dir not in lock_path.parents
    assert backup.delete_backup(entry.id, "i1", base=base) is True
    assert not entry.root_dir.exists()


def test_backup_lock_path_hashes_unsafe_backup_id(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="backup_id"):
        backup._backup_lock_path("i1", "..\\not-a-lock-path", base=tmp_path)


def test_case_aliases_share_one_windows_backup_lock_identity(tmp_path: Path) -> None:
    first = backup._backup_lock_path("Install-A", "ExampleID", base=tmp_path)
    second = backup._backup_lock_path("install-a", "exampleid", base=tmp_path)

    assert first == second


@pytest.mark.parametrize("unsafe_backup_id", [
    "..", ".", "../escape", "..\\escape", "nested/name", "nested\\name",
    "C:\\outside", "\\\\server\\share", "alias.", "alias ", "line:stream",
])
def test_backup_identity_rejects_unsafe_backup_ids_before_mutation(tmp_path: Path, unsafe_backup_id: str) -> None:
    with pytest.raises(ValueError, match="backup_id"):
        backup._backup_identity("i1", unsafe_backup_id, base=tmp_path)
    assert not (tmp_path / "escape").exists()


@pytest.mark.parametrize("unsafe_install_id", [
    "..", ".", "../escape", "..\\escape", "nested/name", "nested\\name",
    "C:\\outside", "\\\\server\\share", "alias.", "line:stream",
])
def test_backups_dir_rejects_unsafe_install_id_before_mutation(tmp_path: Path, unsafe_install_id: str) -> None:
    with pytest.raises(ValueError, match="install_id"):
        backup.backups_dir(unsafe_install_id, base=tmp_path)
    assert not (tmp_path / "escape").exists()
