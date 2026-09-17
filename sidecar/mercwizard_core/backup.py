"""Auto-backup of game files before destructive operations.

Before any write that could be hard to reverse (Edit/Move/Delete or
overwriting a filled slot), the wizard snapshots the affected files into:

    %APPDATA%/MercWizard/backups/<install_id>/<timestamp>__<reason>/

A `manifest.json` is written alongside listing what was backed up, why, when,
and the install id. Restore copies files back from a chosen snapshot.

Backups always run — there is no mode knob. (A BackupMode enum +
Settings docs promising ALWAYS/DESTRUCTIVE_ONLY/OFF/PRISTINE_ONLY lived
here for a while, but nothing ever read it — a lying surface since
removed. tests/test_backup_discipline.py enforces that every
mutating route snapshots.)
"""
from __future__ import annotations

import json
import os
import shutil
import threading
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path, PureWindowsPath
from typing import Callable, Optional
from uuid import uuid4

import portalocker

from .inject._atomic_xml import write_bytes_atomic


_BACKUP_LOCKS_GUARD = threading.Lock()
_BACKUP_LOCKS: dict[str, threading.RLock] = {}


@dataclass
class BackupEntry:
    """One snapshot of one or more game files."""
    id: str                       # e.g. "2026_05_12_143200__create_slot220"
    timestamp: str                # ISO 8601 UTC
    install_id: str
    reason: str
    root_dir: Path                # The snapshot folder
    files: list[str] = field(default_factory=list)  # relative paths from install root
    total_size_bytes: int = 0
    # Files CREATED during the operation that this backup belongs to.
    # On restore, these get DELETED (they didn't exist before the op, so
    # restoring to pre-op state means removing them). Distinguishes a
    # full rollback from a partial overwrite-restore.
    files_created: list[str] = field(default_factory=list)
    # Voice Lab deployment snapshots stay pinned until their conflict-safe
    # Undo completes.  Old manifests predate this field and deserialize as
    # False so existing backups remain fully usable.
    pinned: bool = False

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "timestamp": self.timestamp,
            "install_id": self.install_id,
            "reason": self.reason,
            "root_dir": str(self.root_dir),
            "files": self.files,
            "total_size_bytes": self.total_size_bytes,
            "files_created": self.files_created,
            "pinned": self.pinned,
        }


def _appdata_root() -> Path:
    """Where backups live: %APPDATA%/MercWizard/ on Windows, ~/.config/MercWizard/ elsewhere."""
    appdata = os.environ.get("APPDATA")
    if appdata:
        return Path(appdata) / "MercWizard"
    return Path.home() / ".config" / "MercWizard"


@dataclass(frozen=True)
class _BackupIdentity:
    """Validated paths and Windows-canonical key for one backup snapshot."""

    backups_root: Path
    install_dir: Path
    snapshot_dir: Optional[Path]
    canonical_snapshot_key: Optional[str]


def _validate_backup_component(value: str, field: str) -> str:
    """Accept exactly one safe Windows path component.

    Backup IDs and install IDs originate outside this module.  Treat them as
    names, never paths: validation deliberately uses :class:`PureWindowsPath`
    even when tests run on another platform, because the backing store is a
    Windows application-data layout.
    """
    if not isinstance(value, str) or not value or value != value.strip():
        raise ValueError(f"{field} must be one non-empty path component")
    win_path = PureWindowsPath(value)
    if (
        value in {".", ".."}
        or "/" in value
        or "\\" in value
        or ":" in value
        or value.endswith((".", " "))
        or win_path.is_absolute()
        or bool(win_path.drive)
        or bool(win_path.root)
        or len(win_path.parts) != 1
        or win_path.name != value
    ):
        raise ValueError(f"{field} must be one safe path component")
    return value


def _backup_identity(
    install_id: str,
    backup_id: Optional[str] = None,
    base: Optional[Path] = None,
) -> _BackupIdentity:
    """Return canonical, bounded backup paths without creating anything.

    The resolved candidate is the one identity used for both backup-path work
    and per-snapshot locking.  Its key is case-folded because NTFS treats case
    aliases as the same directory.  Backup storage and the install are trusted
    same-user local state: this rejects pre-existing path escapes, but does not
    claim to defend against a hostile same-user process swapping junctions
    after validation, which could already modify both roots directly.
    """
    configured_root = Path(base) if base is not None else _appdata_root()
    backups_root = (configured_root / "backups").resolve()
    safe_install_id = _validate_backup_component(install_id, "install_id")
    install_dir = (backups_root / safe_install_id).resolve()
    if install_dir.parent != backups_root:
        raise ValueError("install_id escapes the configured backups root")

    if backup_id is None:
        return _BackupIdentity(backups_root, install_dir, None, None)

    safe_backup_id = _validate_backup_component(backup_id, "backup_id")
    snapshot_dir = (install_dir / safe_backup_id).resolve()
    if snapshot_dir.parent != install_dir:
        raise ValueError("backup_id escapes the install backups directory")
    # Normalize slash spelling before case folding so this key expressly
    # models Windows identity even when a non-Windows test host is used.
    canonical_key = str(snapshot_dir).replace("/", "\\").casefold()
    return _BackupIdentity(backups_root, install_dir, snapshot_dir, canonical_key)


def backups_dir(install_id: str, base: Optional[Path] = None) -> Path:
    """Directory holding all backups for a given install."""
    return _backup_identity(install_id, base=base).install_dir


def _make_backup_id(reason: str) -> str:
    # Seconds were not unique: concurrent Voice Lab writers could enter the
    # same snapshot directory and merge rollback material.  The random suffix
    # is an identity, not a cosmetic label; mkdir below is still exclusive.
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S_%f")
    safe_reason = "".join(c if c.isalnum() or c in "-_" else "_" for c in reason)[:48]
    return f"{ts}__{safe_reason}_{uuid4().hex[:10]}"


DEFAULT_MAX_BACKUPS_PER_INSTALL = 50


def snapshot(
    install_root: Path,
    install_id: str,
    files_to_back_up: list[Path],
    reason: str,
    base: Optional[Path] = None,
    auto_prune: bool = True,
    progress_cb: Optional[Callable[[int, int, str], None]] = None,
    pinned: bool = False,
) -> BackupEntry:
    """Copy the listed files into a new snapshot directory.

    `files_to_back_up` are absolute paths; each must be under install_root.
    Files that don't exist are silently skipped (e.g. a slot's STI doesn't
    exist yet because the slot is being created fresh).

    If `auto_prune` is True (the default), older snapshots beyond the
    `DEFAULT_MAX_BACKUPS_PER_INSTALL` threshold are deleted afterward.

    If `progress_cb` is provided, it's called as
    `progress_cb(index, total, rel_path)` after each successful copy.
    `total` is the count of `files_to_back_up` (some may be skipped if they
    don't exist on disk); `index` is the loop position (1-based) of the
    item just processed. Callback failures are intentionally NOT caught —
    a buggy callback should surface during dev rather than swallow.

    Returns the BackupEntry describing the snapshot.
    """
    # Directory creation is the final collision guard.  Do not ever reuse an
    # existing manifest/snapshot directory, even if an ID generator or clock
    # is monkeypatched in a regression test.
    for _attempt in range(32):
        backup_id = _make_backup_id(reason)
        bdir = _backup_identity(install_id, backup_id, base).snapshot_dir
        assert bdir is not None
        try:
            bdir.mkdir(parents=True, exist_ok=False)
            break
        except FileExistsError:
            continue
    else:  # pragma: no cover - protects a pathological monkeypatched UUID source.
        raise FileExistsError("could not reserve a unique backup snapshot directory")
    snapshot_subdir = bdir / "snapshot"
    snapshot_subdir.mkdir()

    entry = BackupEntry(
        id=backup_id,
        timestamp=datetime.now(timezone.utc).isoformat(),
        install_id=install_id,
        reason=reason,
        root_dir=bdir,
        pinned=bool(pinned),
    )

    install_root = install_root.resolve()
    total = len(files_to_back_up)
    for idx, src in enumerate(files_to_back_up, start=1):
        src = Path(src).resolve()
        if not src.is_file():
            continue
        try:
            rel = src.relative_to(install_root)
        except ValueError:
            # File isn't under install_root — skip (we shouldn't back up
            # arbitrary external files anyway)
            continue
        dst = snapshot_subdir / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
        entry.files.append(str(rel).replace(os.sep, "/"))
        entry.total_size_bytes += dst.stat().st_size
        if progress_cb is not None:
            progress_cb(idx, total, str(rel).replace(os.sep, "/"))

    manifest = bdir / "manifest.json"
    write_bytes_atomic(manifest, _manifest_bytes(entry))

    if auto_prune:
        prune_backups(install_id, base=base)

    return entry


def prune_backups(
    install_id: str,
    keep: int = DEFAULT_MAX_BACKUPS_PER_INSTALL,
    base: Optional[Path] = None,
) -> int:
    """Delete oldest snapshots beyond `keep` newest. Returns the count deleted.

    Used to bound `%APPDATA%\\MercWizard\\backups\\<install_id>\\` growth.
    Called automatically at the end of `snapshot()` unless suppressed.
    """
    entries = list_backups(install_id, base=base)  # newest first
    ordinary_entries = [entry for entry in entries if not entry.pinned]
    if len(ordinary_entries) <= keep:
        return 0
    deleted = 0
    for entry in ordinary_entries[keep:]:
        # The earlier list is advisory only. A Voice Lab deploy may pin this
        # backup between selection and deletion, so lock + re-read its latest
        # manifest before removing anything.
        with _backup_lock(install_id, entry.id, base=base):
            identity = _backup_identity(install_id, entry.id, base)
            assert identity.snapshot_dir is not None
            manifest_path = identity.snapshot_dir / "manifest.json"
            if not manifest_path.is_file():
                continue
            try:
                current = _entry_from_manifest(
                    _read_manifest(manifest_path, entry.id), manifest_path.parent, install_id,
                )
            except ValueError:
                continue
            if current.pinned:
                continue
            if _delete_backup_locked(entry.id, install_id, base=base):
                deleted += 1
    return deleted


def list_backups(install_id: str, base: Optional[Path] = None) -> list[BackupEntry]:
    """List all snapshots for an install, sorted newest-first."""
    bdir = backups_dir(install_id, base)
    if not bdir.is_dir():
        return []
    entries: list[BackupEntry] = []
    for sub in bdir.iterdir():
        if not sub.is_dir():
            continue
        manifest = sub / "manifest.json"
        if not manifest.is_file():
            continue
        try:
            data = json.loads(manifest.read_text())
            # The directory name, rather than untrusted manifest content, is
            # the only identity that may later select a snapshot for mutation.
            _backup_identity(install_id, sub.name, base)
            entry = _entry_from_manifest(data, sub, install_id)
            entry.id = sub.name
            entries.append(entry)
        except (json.JSONDecodeError, OSError, TypeError, ValueError):
            continue
    entries.sort(key=lambda e: e.timestamp, reverse=True)
    return entries


def set_backup_pinned(
    backup_id: str,
    install_id: str,
    pinned: bool,
    base: Optional[Path] = None,
) -> BackupEntry:
    """Atomically change whether one retained backup may be auto-pruned.

    Voice Lab calls this to unpin a deployment only after its separately
    journaled Undo has completed.  The replacement manifest is fsynced by
    :func:`write_bytes_atomic`, so a crash leaves either the old pin state or
    the complete new state on disk -- never a torn JSON manifest.
    """
    identity = _backup_identity(install_id, backup_id, base)
    assert identity.snapshot_dir is not None
    manifest_path = identity.snapshot_dir / "manifest.json"
    if not manifest_path.is_file():
        raise FileNotFoundError(f"Backup '{backup_id}' not found at {manifest_path.parent}")
    with _backup_lock(install_id, backup_id, base=base):
        if not manifest_path.is_file():
            raise FileNotFoundError(f"Backup '{backup_id}' not found at {manifest_path.parent}")
        data = _read_manifest(manifest_path, backup_id)
        data["pinned"] = bool(pinned)
        entry = _entry_from_manifest(data, manifest_path.parent, install_id)
        # Keep unrecognized future manifest keys intact.  Pinning must not
        # become a lossy migration just because a later writer added metadata.
        write_bytes_atomic(
            manifest_path,
            json.dumps(data, indent=2, ensure_ascii=False).encode("utf-8"),
        )
        return entry


def record_files_created(
    backup_id: str,
    install_id: str,
    files: list[Path],
    base: Optional[Path] = None,
) -> int:
    """Append files created during an operation to an existing snapshot's manifest.

    On restore, these will be deleted to fully roll back the operation.
    No-op if the snapshot doesn't exist. Returns the count appended.
    """
    identity = _backup_identity(install_id, backup_id, base)
    assert identity.snapshot_dir is not None
    bdir = identity.snapshot_dir
    manifest_path = bdir / "manifest.json"
    if not manifest_path.is_file():
        return 0
    with _backup_lock(install_id, backup_id, base=base):
        if not manifest_path.is_file():
            return 0
        try:
            data = _read_manifest(manifest_path, backup_id)
        except ValueError:
            return 0
        existing = list(data.get("files_created", []))
        added = 0
        for f in files:
            s = str(Path(f)).replace(os.sep, "/")
            if s not in existing:
                existing.append(s)
                added += 1
        data["files_created"] = existing
        try:
            write_bytes_atomic(
                manifest_path,
                json.dumps(data, indent=2, ensure_ascii=False).encode("utf-8"),
            )
        except OSError:
            return 0
        return added


def _backup_lock_path(install_id: str, backup_id: str, base: Optional[Path] = None) -> Path:
    """Return a stable sibling lock path for the canonical backup identity."""
    identity = _backup_identity(install_id, backup_id, base)
    assert identity.canonical_snapshot_key is not None
    digest = sha256(identity.canonical_snapshot_key.encode("utf-8")).hexdigest()
    return identity.backups_root / ".locks" / f"{digest}.lock"


@contextmanager
def _backup_lock(install_id: str, backup_id: str, base: Optional[Path] = None):
    """Serialize one backup's mutation/deletion across sidecars.

    The sentinel is a sibling beneath ``.locks``, not inside the snapshot, so
    Windows may delete the snapshot while this lock remains held.
    """
    lock_path = _backup_lock_path(install_id, backup_id, base=base)
    resolved = str(lock_path.resolve()).casefold()
    with _BACKUP_LOCKS_GUARD:
        local_lock = _BACKUP_LOCKS.setdefault(resolved, threading.RLock())
    with local_lock:
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        with portalocker.Lock(
            str(lock_path),
            mode="a+",
            timeout=10.0,
            check_interval=0.05,
            flags=portalocker.LOCK_EX,
        ):
            yield


def _read_manifest(manifest_path: Path, backup_id: str) -> dict:
    try:
        data = json.loads(manifest_path.read_text())
    except (json.JSONDecodeError, OSError) as exc:
        raise ValueError(f"Backup '{backup_id}' has an unreadable manifest") from exc
    if not isinstance(data, dict):
        raise ValueError(f"Backup '{backup_id}' has an unreadable manifest")
    return data


def _entry_from_manifest(data: dict, root_dir: Path, default_install_id: str) -> BackupEntry:
    """Build an entry while preserving compatibility with historical manifests."""
    return BackupEntry(
        id=str(data.get("id", root_dir.name)),
        timestamp=str(data.get("timestamp", "")),
        install_id=str(data.get("install_id", default_install_id)),
        reason=str(data.get("reason", "")),
        root_dir=root_dir,
        files=list(data.get("files", [])),
        total_size_bytes=int(data.get("total_size_bytes", 0)),
        files_created=list(data.get("files_created", [])),
        pinned=bool(data.get("pinned", False)),
    )


def _manifest_bytes(entry: BackupEntry) -> bytes:
    """Return a stable UTF-8 manifest payload for an atomic replacement."""
    return json.dumps(entry.to_dict(), indent=2, ensure_ascii=False).encode("utf-8")


def _validated_created_file_targets(
    manifest_path: Path,
    backup_id: str,
    install_root: Path,
) -> list[Path]:
    """Read and validate every manifest-derived cleanup target before restore.

    Historical manifests may contain absolute file names, but only resolved
    paths under ``install_root`` are accepted. Relative names use Windows
    component rules regardless of host platform so traversal, rooted, drive,
    and ADS-like spellings never acquire meaning while restoring.
    """
    data = _read_manifest(manifest_path, backup_id)
    files_created = data.get("files_created", [])
    if not isinstance(files_created, list):
        raise ValueError(f"Backup '{backup_id}' has invalid files_created entries")

    targets: list[Path] = []
    for raw_path in files_created:
        if not isinstance(raw_path, str) or not raw_path or raw_path != raw_path.strip():
            raise ValueError(f"Backup '{backup_id}' has invalid files_created entry")
        windows_path = PureWindowsPath(raw_path)
        native_path = Path(raw_path)
        absolute_windows_path = windows_path.is_absolute()

        if absolute_windows_path:
            # Absolute legacy entries are supported only after the final
            # resolved containment check below. Reject ADS-style components
            # even in an otherwise in-root absolute spelling.
            components = windows_path.parts[1:]
            if not native_path.is_absolute():
                raise ValueError(f"Backup '{backup_id}' has invalid files_created entry")
            candidate = native_path
        else:
            # A drive-relative path (C:foo), rooted path (\\foo), UNC spelling,
            # or a POSIX absolute path is ambiguous as a relative entry.
            if native_path.is_absolute() or windows_path.drive or windows_path.root:
                raise ValueError(f"Backup '{backup_id}' has invalid files_created entry")
            components = raw_path.replace("/", "\\").split("\\")
            if (
                not components
                or any(
                    not component
                    or component in {".", ".."}
                    or ":" in component
                    or component.endswith((".", " "))
                    for component in components
                )
            ):
                raise ValueError(f"Backup '{backup_id}' has invalid files_created entry")
            candidate = install_root / native_path

        if any(
            not component
            or ":" in component
            or component.endswith((".", " "))
            for component in components
        ):
            raise ValueError(f"Backup '{backup_id}' has invalid files_created entry")
        resolved_target = candidate.resolve()
        try:
            resolved_target.relative_to(install_root)
        except ValueError as exc:
            raise ValueError(
                f"Backup '{backup_id}' has files_created entry outside install root"
            ) from exc
        targets.append(resolved_target)
    return targets


def restore(
    backup_id: str,
    install_id: str,
    install_root: Path,
    base: Optional[Path] = None,
) -> int:
    """Copy snapshot files back over the install. Returns count of restored files.

    Two-phase restore:
      1. Copy snapshot files back over the install at their relative paths
         (files that existed pre-op get their pre-op contents back).
      2. Delete any `files_created` listed in the manifest (files that DIDN'T
         exist pre-op but were added by the operation — restoring to
         pre-op state means deleting them).

    The two phases together produce a clean rollback even when the operation
    created brand-new files (e.g. battlesnds at a new slot, voice clips in
    a fresh subdir).
    """
    # Keep the same canonical snapshot lock for the full source lifetime:
    # existence check, enumeration, reads, manifest read, and created-file
    # cleanup.  Delete/prune/RMW writers take this lock too, so a snapshot
    # cannot disappear or change halfway through a rollback.
    with _backup_lock(install_id, backup_id, base=base):
        return _restore_locked(backup_id, install_id, install_root, base=base)


def _restore_locked(
    backup_id: str,
    install_id: str,
    install_root: Path,
    base: Optional[Path] = None,
) -> int:
    """Restore while caller holds :func:`_backup_lock` (no recursive lock)."""
    identity = _backup_identity(install_id, backup_id, base)
    assert identity.snapshot_dir is not None
    bdir = identity.snapshot_dir
    manifest_path = bdir / "manifest.json"
    snapshot_dir = bdir / "snapshot"
    if not snapshot_dir.is_dir():
        raise FileNotFoundError(f"Backup '{backup_id}' not found at {bdir}")

    install_root = Path(install_root).resolve()
    # Validate every manifest-derived cleanup before the first live write.
    cleanup_targets = _validated_created_file_targets(
        manifest_path, backup_id, install_root,
    )

    # Phase 1 — restore captured files
    restored = 0
    for src in snapshot_dir.rglob("*"):
        if not src.is_file():
            continue
        rel = src.relative_to(snapshot_dir)
        dst = install_root / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        # Atomic replace (tempfile + os.replace) rather than shutil.copy2: a
        # crash mid-rollback must not leave a truncated live game file — the same
        # boot-failure the forward write path uses write_bytes_atomic to avoid.
        # (Restored files get a fresh mtime; irrelevant for game data.)
        write_bytes_atomic(dst, src.read_bytes())
        restored += 1

    # Phase 2 — delete files the op created
    deleted = 0
    for target in cleanup_targets:
        if target.is_file():
            try:
                target.unlink()
                deleted += 1
            except OSError:
                pass

    return restored + deleted


def delete_backup(backup_id: str, install_id: str, base: Optional[Path] = None) -> bool:
    """Delete a snapshot folder under its sibling cross-process lock."""
    with _backup_lock(install_id, backup_id, base=base):
        return _delete_backup_locked(backup_id, install_id, base=base)


def _delete_backup_locked(backup_id: str, install_id: str, base: Optional[Path] = None) -> bool:
    """Delete while caller holds :func:`_backup_lock` (no recursive lock)."""
    identity = _backup_identity(install_id, backup_id, base)
    assert identity.snapshot_dir is not None
    bdir = identity.snapshot_dir
    if not bdir.is_dir():
        return False
    shutil.rmtree(bdir)
    return True


def files_for_merc(install_root: Path, ui_index: int, face_index: Optional[int]) -> list[Path]:
    """Return the file paths the wizard touches for one merc slot.

    Used by callers to assemble the backup file list for create/edit/delete.
    Files that don't exist (e.g. fresh slot) are still listed — snapshot()
    skips missing files silently.

    VFS-aware: routes to the active install's mod content layer rather
    than the empty vanilla Data-1.13/ copy for modded installs.

    Coverage was later expanded: the list now also includes
    voice clips (vanilla subdir + slot-prefix layouts), Battlesnds /
    NPC_Speech / snitch slot-keyed audio, IMPFaces / camo face variants,
    BigItems for the slot, and the per-slot XML rows from mod-specific
    tables. Before the expansion, restoring a Delete operation left
    voice/audio orphans on disk and silently lost mod-specific rows.
    """
    from .install_context import EXTRA_TABLES, make_install_context
    ctx = make_install_context(Path(install_root))

    files: list[Path] = [
        ctx.profiles_xml_path(),
        ctx.aim_xml_path(),
        ctx.gear_xml_path(),
        ctx.aim_bios_edt_path(),
        ctx.merc_bios_edt_path(),
        ctx.per_file_merc_edt_path(ui_index),
        ctx.per_file_npc_edt_path(ui_index),
    ]
    merc_xml = ctx.merc_xml_path()
    if merc_xml is not None:
        files.append(merc_xml)

    # Mod-specific per-slot XML tables (MercOpinions, MercQuote, FaceGear,
    # Backgrounds, CivGroupNames). We back up the WHOLE file because the
    # row-level restore is harder than file-level; the snapshot includes
    # other slots' rows but that's safe.
    for key in EXTRA_TABLES:
        extra = ctx.extra_table_path(key)
        if extra is not None:
            files.append(extra)

    if face_index is not None:
        for size in ("smallface", "face_65", "face_33", "bigface"):
            files.append(ctx.face_sti_path(face_index, size))
            if face_index < 100:
                padded_path = ctx.face_sti_path(face_index, size)
                files.append(padded_path.parent / f"{face_index:02}{padded_path.suffix}")
        # IMPFaces parallel hierarchy and camo variants — Edit doesn't
        # normally touch these but Delete leaves them orphaned.
        impfaces_dir = ctx.layout.mod_content_path("IMPFaces")
        for sub in ("", "33Face", "65Face", "BigFaces"):
            for ext in ("sti", "STI"):
                files.append((impfaces_dir / sub / f"{face_index}.{ext}") if sub
                             else impfaces_dir / f"{face_index}.{ext}")
        faces_dir = ctx.faces_dir()
        for camo in ("DESERTCAMO", "URBANCAMO", "WOODCAMO"):
            for ext in ("sti", "STI"):
                files.append(faces_dir / camo / f"{face_index}.{ext}")

    # Voice clips. Vanilla layout: Speech/<voice_index>/<file>.{wav,ogg}.
    # Slot-prefix (Vengeance): Speech/<slot>_<idx>.<ext> at root. We can't
    # know voice_index without reading the profile, so back up both
    # patterns keyed on `ui_index` — the default voice_index per the
    # Pydantic model is the slot, and Create flow auto-fills voice_index
    # to match. Also covers Battlesnds + NPC_Speech + snitch_names.
    speech_root = ctx.speech_root()
    files.extend(_glob_audio_for_slot(speech_root, ui_index, slot_prefix=True))
    legacy_voice_dir = speech_root / str(ui_index)
    if legacy_voice_dir.is_dir():
        try:
            files.extend(p for p in legacy_voice_dir.iterdir() if p.is_file())
        except OSError:
            pass
    files.extend(_glob_audio_for_slot(ctx.battlesnds_root(), ui_index, slot_prefix=True))
    files.extend(_glob_audio_for_slot(ctx.npc_speech_root(), ui_index, slot_prefix=True))
    for alt in (False, True):
        snitch_dir = ctx.snitch_names_dir(alt=alt)
        files.extend(_glob_audio_for_slot(snitch_dir, ui_index, slot_suffix=True))

    # Signature item STIs at BigItems/. Convention (when a mod uses it):
    # filename stem is either the slot number on its own (`216.sti`) or
    # `P1ITEM<slot>` (`P1ITEM216.sti`). Pre-fix this used substring match
    # (`str(ui_index) in p.name`) which catastrophically over-matched for
    # low slot numbers — slot 0 backed up every BigItems STI containing
    # the digit '0' (P1ITEM101, P1ITEM102, …P1ITEM209, …): 372 files for
    # a user's slot 0 → 216 duplicate. Whole-stem equality only.
    big_items_dir = ctx.big_items_dir()
    if big_items_dir.is_dir():
        slot_str = str(ui_index)
        # Accept both bare-number and P1ITEM<slot> conventions, case-
        # insensitive (some mods write filenames in upper-case).
        valid_stems = {
            slot_str,
            f"P1ITEM{slot_str}",
            f"BIGITEM{slot_str}",
        }
        valid_stems_upper = {s.upper() for s in valid_stems}
        try:
            files.extend(
                p for p in big_items_dir.iterdir()
                if p.is_file()
                   and p.suffix.lower() == ".sti"
                   and p.stem.upper() in valid_stems_upper
            )
        except OSError:
            pass

    return files


def _glob_audio_for_slot(
    root: Path,
    slot: int,
    *,
    slot_prefix: bool = False,
    slot_suffix: bool = False,
) -> list[Path]:
    """Probe `root` for audio files keyed on `slot`.

    `slot_prefix=True` matches `<slot>_<rest>.<ext>` (Battlesnds, slot-prefix
    Speech, NPC_Speech). `slot_suffix=True` matches `<rest>_<slot>.<ext>`
    (snitch names where another merc says THIS slot's name). Either or both
    can be True. Returns existing files only; missing dirs return [].
    """
    if not root.is_dir():
        return []
    audio_exts = {".ogg", ".wav", ".mp3", ".gap"}
    out: list[Path] = []
    slot_str = str(slot)
    try:
        for f in root.iterdir():
            if not f.is_file() or f.suffix.lower() not in audio_exts:
                continue
            stem = f.stem
            if slot_prefix and "_" in stem:
                prefix = stem.split("_", 1)[0]
                if prefix == slot_str:
                    out.append(f)
                    continue
            if slot_suffix and "_" in stem:
                suffix = stem.rsplit("_", 1)[-1]
                if suffix == slot_str:
                    out.append(f)
    except OSError:
        pass
    return out
