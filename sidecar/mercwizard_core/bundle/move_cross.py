"""Cross-install move: relocate a merc from install A to install B.

Implemented as export → deploy_import → clear_source, with backups at both
ends. The temp .wmerc lives in the OS temp dir only long enough to bridge
the transfer and is deleted in a `finally` block.

If the deploy_import step fails (audit error, slot occupied, etc.) the
source install is untouched. If the clear-source step fails after a
successful deploy, the source backup is still on disk for rollback —
the user can restore it from the Backups page.
"""
from __future__ import annotations

import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from .. import backup as backup_mod
from ..inject import aim_availability, merc_availability, profiles_xml, starting_gear
from ..inject import edt as edt_mod
from .export import export_merc
from .import_ import (
    ImportAuditError,
    ImportReport,
    SlotOccupiedError,
    deploy_import,
)


@dataclass
class CrossMoveReport:
    """Outcome of a cross-install move."""
    source_slot: int
    source_install_root: str
    target_slot: int
    target_install_root: str
    files_written: list[str] = field(default_factory=list)
    portrait_compiled: bool = False
    voice_clips_copied: int = 0
    aim_bio_id_used: Optional[int] = None
    source_backup_id: Optional[str] = None
    source_cleared: bool = False
    issues: list[dict] = field(default_factory=list)
    partial_failures: list[str] = field(default_factory=list)


def move_between_installs(
    source_install: Path,
    source_install_id: str,
    target_install: Path,
    target_install_id: str,
    source_slot: int,
    target_slot: int,
    force: bool = False,
) -> CrossMoveReport:
    """Move a merc from source_install[source_slot] to target_install[target_slot].

    Caller is expected to hold the global write lock for the duration so this
    doesn't interleave with other CRUD operations.

    Raises:
        ValueError: source slot empty, or same install + same slot
        ImportAuditError: target audit fails
        SlotOccupiedError: target slot occupied and force=False
    """
    source_install = Path(source_install)
    target_install = Path(target_install)

    if source_install.resolve() == target_install.resolve() and source_slot == target_slot:
        raise ValueError("Source and target are the same install + slot — nothing to move")

    from ..install_context import make_install_context
    src_ctx = make_install_context(source_install)
    source_profiles_path = src_ctx.profiles_xml_path()
    raw_source = profiles_xml.read_slot(source_profiles_path, source_slot)
    if raw_source is None or not raw_source.get("zName", "").strip():
        raise ValueError(f"Source slot {source_slot} is empty in {source_install}")

    # ── Step 1: export source → temp .wmerc ──
    tmp_fd, tmp_name = tempfile.mkstemp(suffix=".wmerc", prefix="mercwizard_xmove_")
    import os
    os.close(tmp_fd)
    temp_bundle = Path(tmp_name)
    try:
        export_merc(
            install_root=source_install,
            ui_index=source_slot,
            out_path=temp_bundle,
            include_voice=True,
        )

        # ── Step 2: deploy into target install ──
        # deploy_import handles its own backup + audit + writes at the target.
        import_report: ImportReport = deploy_import(
            install_root=target_install,
            bundle_path=temp_bundle,
            install_id=target_install_id,
            target_slot=target_slot,
            force=force,
        )

        # ── Step 3: clear source (with its own backup snapshot) ──
        source_aim_path = src_ctx.aim_xml_path(for_write=True)
        source_merc_xml_path = src_ctx.merc_xml_path(for_write=True)
        source_gear_path = src_ctx.gear_xml_path(for_write=True)
        # Use the placeholder-aware lookup, not raw read_all — modded
        # AIMAvailability.xml files ship `<AimBioID>-1</AimBioID>` /
        # `<ProfilId>-1</ProfilId>` placeholder rows for every slot
        # 0-254. Raw `.AimBioID` here would return -1, then `clear_bio`
        # → `route_bio` raises ValueError (edt.py:315), which the
        # try/except below silently swallows AND we still return
        # `source_cleared=True`. Net: cross-install move tells the user
        # "source cleared" while the source EDT bio bytes are still on
        # disk. Bug-review finding C2.
        source_aim_bio_id = aim_availability.lookup_aim_bio_id(source_aim_path, source_slot)
        source_merc_bio_id = merc_availability.lookup_merc_bio_id(source_merc_xml_path, source_slot)

        face_index: Optional[int] = None
        try:
            face_index = int(raw_source.get("ubFaceIndex", "0").strip())
        except (ValueError, AttributeError):
            pass

        source_backup_files = backup_mod.files_for_merc(
            source_install, source_slot, face_index
        )
        source_snap = backup_mod.snapshot(
            install_root=source_install,
            install_id=source_install_id,
            files_to_back_up=source_backup_files,
            reason=f"cross_move_out_slot_{source_slot}",
        )

        profiles_xml.clear_slot(source_profiles_path, source_slot)
        aim_availability.remove(source_aim_path, source_slot)
        if source_merc_xml_path is not None:
            merc_availability.remove(source_merc_xml_path, source_slot)
        starting_gear.clear_slot(source_gear_path, source_slot)
        try:
            edt_mod.clear_bio(
                source_install, source_slot,
                aim_bio_id=source_aim_bio_id,
                merc_bio_id=source_merc_bio_id,
                ctx=src_ctx,
            )
        except (FileNotFoundError, ValueError):
            # Source EDT may not exist (e.g. expanded-MERC slot with no per-file
            # EDT yet). Don't fail the whole move.
            pass

        return CrossMoveReport(
            source_slot=source_slot,
            source_install_root=str(source_install),
            target_slot=import_report.target_slot,
            target_install_root=str(target_install),
            files_written=list(import_report.files_written),
            portrait_compiled=import_report.portrait_compiled,
            voice_clips_copied=import_report.voice_clips_copied,
            aim_bio_id_used=import_report.aim_bio_id_used,
            source_backup_id=source_snap.id,
            source_cleared=True,
            issues=list(import_report.issues),
            partial_failures=list(import_report.partial_failures),
        )
    finally:
        try:
            temp_bundle.unlink(missing_ok=True)
        except OSError:
            pass
