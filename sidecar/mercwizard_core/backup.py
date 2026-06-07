"""Auto-backup of game files before destructive operations.

Before any write that could be hard to reverse (Edit/Move/Delete or
overwriting a filled slot), the wizard snapshots the affected files into:

    %APPDATA%/MercWizard/backups/<install_id>/<timestamp>__<reason>/

A `manifest.json` is written alongside listing what was backed up, why, when,
and the install id. Restore copies files back from a chosen snapshot.

Backup modes (configurable in Settings):
- ALWAYS — snapshot on every write
- DESTRUCTIVE_ONLY (default) — only when overwriting / editing / moving / deleting
- OFF — no backups (settings shows a red warning)
- PRISTINE_ONLY — one snapshot at first run; no per-op backups

This module owns the file copies. The wizard's transaction layer decides
when to invoke it based on the active mode.
"""
from __future__ import annotations

import json
import os
import shutil
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Callable, Optional

from .inject._atomic_xml import write_bytes_atomic


class BackupMode(str, Enum):
    ALWAYS = "always"
    DESTRUCTIVE_ONLY = "destructive_only"
    OFF = "off"
    PRISTINE_ONLY = "pristine_only"


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
        }


def _appdata_root() -> Path:
    """Where backups live: %APPDATA%/MercWizard/ on Windows, ~/.config/MercWizard/ elsewhere."""
    appdata = os.environ.get("APPDATA")
    if appdata:
        return Path(appdata) / "MercWizard"
    return Path.home() / ".config" / "MercWizard"


def backups_dir(install_id: str, base: Optional[Path] = None) -> Path:
    """Directory holding all backups for a given install."""
    root = base if base is not None else _appdata_root()
    return root / "backups" / install_id


def _make_backup_id(reason: str) -> str:
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    safe_reason = "".join(c if c.isalnum() or c in "-_" else "_" for c in reason)[:48]
    return f"{ts}__{safe_reason}"


DEFAULT_MAX_BACKUPS_PER_INSTALL = 50


def snapshot(
    install_root: Path,
    install_id: str,
    files_to_back_up: list[Path],
    reason: str,
    base: Optional[Path] = None,
    auto_prune: bool = True,
    progress_cb: Optional[Callable[[int, int, str], None]] = None,
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
    backup_id = _make_backup_id(reason)
    bdir = backups_dir(install_id, base) / backup_id
    snapshot_subdir = bdir / "snapshot"
    snapshot_subdir.mkdir(parents=True, exist_ok=True)

    entry = BackupEntry(
        id=backup_id,
        timestamp=datetime.now(timezone.utc).isoformat(),
        install_id=install_id,
        reason=reason,
        root_dir=bdir,
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
    manifest.write_text(json.dumps(entry.to_dict(), indent=2))

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
    if len(entries) <= keep:
        return 0
    deleted = 0
    for entry in entries[keep:]:
        if delete_backup(entry.id, install_id, base=base):
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
            entries.append(BackupEntry(
                id=data.get("id", sub.name),
                timestamp=data.get("timestamp", ""),
                install_id=data.get("install_id", install_id),
                reason=data.get("reason", ""),
                root_dir=sub,
                files=data.get("files", []),
                total_size_bytes=data.get("total_size_bytes", 0),
            ))
        except (json.JSONDecodeError, OSError):
            continue
    # also load files_created if present in the manifest
    for entry in entries:
        try:
            data = json.loads((entry.root_dir / "manifest.json").read_text())
            entry.files_created = data.get("files_created", [])
        except (json.JSONDecodeError, OSError):
            entry.files_created = []
    entries.sort(key=lambda e: e.timestamp, reverse=True)
    return entries


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
    bdir = backups_dir(install_id, base) / backup_id
    manifest_path = bdir / "manifest.json"
    if not manifest_path.is_file():
        return 0
    try:
        data = json.loads(manifest_path.read_text())
    except (json.JSONDecodeError, OSError):
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
        manifest_path.write_text(json.dumps(data, indent=2))
    except OSError:
        return 0
    return added


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
    bdir = backups_dir(install_id, base) / backup_id
    manifest_path = bdir / "manifest.json"
    snapshot_dir = bdir / "snapshot"
    if not snapshot_dir.is_dir():
        raise FileNotFoundError(f"Backup '{backup_id}' not found at {bdir}")

    install_root = Path(install_root).resolve()

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
    if manifest_path.is_file():
        try:
            data = json.loads(manifest_path.read_text())
            for rel in data.get("files_created", []):
                # Allow absolute paths in `files_created` (e.g. routes write
                # them as absolute) — handle both forms safely.
                p = Path(rel)
                if not p.is_absolute():
                    p = install_root / rel
                if p.is_file():
                    try:
                        p.unlink()
                        deleted += 1
                    except OSError:
                        pass
        except (json.JSONDecodeError, OSError):
            pass

    return restored + deleted


def delete_backup(backup_id: str, install_id: str, base: Optional[Path] = None) -> bool:
    """Delete a snapshot folder. Returns True if deleted."""
    bdir = backups_dir(install_id, base) / backup_id
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

    Coverage expanded 2026-05-15 (bug review): the list now also includes
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
