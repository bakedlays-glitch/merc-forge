"""MercRelocator: the Move flow — relocate a merc from slot A to slot B.

This is the most delicate operation because it crosses EDT files when source
and destination have different roles (AIM → MERC, MERC → AIM, etc.):
- Source AIM (any range) → AIMBIOS or MercEdt depending on slot
- Destination AIM (any range) → AIMBIOS at AimBioID×1120
- Source MERC vanilla (40-50) → MERCBIOS
- Destination MERC expanded (178+) → MercEdt/{N}.EDT (one file per record)

Plus all of these have to update:
- MercProfiles.xml: clear at N, write at M (with uiIndex field updated)
- AIMAvailability.xml: remove N's entry, add M's entry (recomputing AimBioID)
- MercStartingGear.xml: change <mIndex>N</mIndex> to M
- STI files: rename {faceIndex_N}.sti → {faceIndex_M}.sti at all 4 sizes
  (or keep faceIndex stable and only update the XML reference)

For v1 we keep ubFaceIndex stable across moves (no STI renaming) because:
- It's strictly safer (no chance of file-rename race)
- It's simpler
- The faceIndex namespace is decoupled from uiIndex anyway

The MercRelocator class wraps everything in a transaction: if any step
fails, it surfaces the partial state and the caller can restore from
backup. The library doesn't auto-rollback (that's the wizard's job, with
the backup module).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Optional

from .inject import aim_availability, edt, merc_availability, profiles_xml, starting_gear
from .models import AimBinding, Gear, Merc, MercBinding
from .roster import load_roster

if TYPE_CHECKING:
    from .install_context import InstallContext


@dataclass
class MoveReport:
    """Summary of a move operation: what changed, what failed."""
    source_slot: int
    dest_slot: int
    success: bool = False
    steps_completed: list[str] = field(default_factory=list)
    error: Optional[str] = None
    error_step: Optional[str] = None


class MoveError(Exception):
    """Raised when a move can't even start (validation failure)."""


def _read_source_merc(
    install_root: Path,
    source_slot: int,
    *,
    ctx: "InstallContext | None" = None,
) -> tuple[Optional[Merc], Optional[str]]:
    """Construct a Merc model from the MercProfiles.xml entry at source_slot.

    Returns (merc, error). On success: (Merc, None). On failure: (None, error_str)
    where error_str is a pydantic validation message naming the offending field.
    """
    if ctx is None:
        from .install_context import make_install_context
        ctx = make_install_context(install_root)
    profiles_path = ctx.profiles_xml_path()
    raw = profiles_xml.read_slot(profiles_path, source_slot)
    if raw is None:
        return None, f"Slot {source_slot} has no <PROFILE> entry"
    # Map the engine's b-prefixed growth-modifier tags to model field names
    # before the model_fields filter below strips them (the filter keys on
    # field names, not on-disk tags). Without this, moving a merc on a b-tag
    # install (e.g. AIMNAS) silently zeroes the growth modifiers.
    raw = profiles_xml.normalize_profile_tags(raw)

    kwargs: dict[str, object] = {}
    string_fields = {"zName", "zNickname", "PANTS", "VEST", "SKIN", "HAIR",
                     "biographyText", "additionalInfoText"}
    for k, v in raw.items():
        if k in string_fields:
            kwargs[k] = v
            continue
        try:
            kwargs[k] = int(v.strip())
        except (ValueError, AttributeError):
            kwargs[k] = v

    valid_fields = set(Merc.model_fields.keys())
    kwargs = {k: v for k, v in kwargs.items() if k in valid_fields}

    try:
        return Merc(**kwargs), None
    except Exception as e:
        # Surface the specific validation error
        return None, f"Parse error: {type(e).__name__}: {str(e)[:300]}"


def move(install_root: Path, source_slot: int, dest_slot: int) -> MoveReport:
    """Relocate a merc from `source_slot` to `dest_slot`.

    Caller is expected to:
    1. Take a backup BEFORE calling this (via backup.snapshot)
    2. Confirm source is filled and dest is empty (via roster.load_roster)

    This function performs the multi-file rewrite as a best-effort sequence;
    on failure, it returns a MoveReport with `success=False` and the step
    that failed. Caller can then restore from backup.
    """
    install_root = Path(install_root)
    report = MoveReport(source_slot=source_slot, dest_slot=dest_slot)

    if source_slot == dest_slot:
        raise MoveError("Source and destination slots are the same")

    # Build the InstallContext once and thread it through `_read_source_merc`
    # and every edt.read_bio/clear_bio/write_bio call below. Pre-fix each of
    # those rebuilt the ctx (parse_vfs_config + detect_flavor, ~50-100 ms on
    # a modded install) — 5-6 rebuilds per move. Bug-review finding C4.
    from .install_context import make_install_context
    ctx = make_install_context(install_root)

    # 1. Read source merc
    source_merc, read_err = _read_source_merc(install_root, source_slot, ctx=ctx)
    if source_merc is None:
        raise MoveError(
            f"Could not read source slot {source_slot}: {read_err or 'unknown error'}"
        )

    profiles_path = ctx.profiles_xml_path(for_write=True)
    aim_path = ctx.aim_xml_path(for_write=True)
    merc_xml_path = ctx.merc_xml_path(for_write=True)
    gear_path = ctx.gear_xml_path(for_write=True)

    # Verify dest is empty (matches the roster's definition of "empty" — a
    # stub <PROFILE> block with no zName/zNickname counts as empty).
    if profiles_xml.is_slot_occupied(profiles_path, dest_slot):
        raise MoveError(f"Destination slot {dest_slot} is occupied")

    # 2. Read source state (gear, AIM/MERC bindings, EDT bio)
    source_gear = starting_gear.read_slot(gear_path, source_slot)
    source_aim = aim_availability.lookup_aim_bio_id(aim_path, source_slot)
    source_merc_bio_id = merc_availability.lookup_merc_bio_id(merc_xml_path, source_slot)
    source_bio, source_addl = edt.read_bio(
        install_root, source_slot,
        aim_bio_id=source_aim if source_aim is not None else None,
        merc_bio_id=source_merc_bio_id,
        ctx=ctx,
    )

    # 3. Construct the destination merc (everything copies, uiIndex changes)
    dest_merc = source_merc.model_copy(update={"uiIndex": dest_slot})

    # If the destination merc is Type=AIM, compute an AIM binding for it.
    # We don't gate on `is_aim_bound_slot` — AIM membership is XML-driven
    # (AIMAvailability.xml row), not engine-range-hardcoded. Any slot
    # 0-254 can be made AIM-visible by writing the row.
    dest_aim_binding: Optional[AimBinding] = None
    if dest_merc.Type == 1:
        new_aim_bio_id = aim_availability.compute_aim_bio_id(aim_path, dest_slot)
        dest_aim_binding = AimBinding(
            uiIndex=dest_slot,
            description=dest_merc.zName,
            ProfilId=dest_slot,
            AimBioID=new_aim_bio_id,
        )

    # Mirror logic for Type=MERC. The MercBioID becomes the offset in
    # MERCBIOS.EDT, so getting a fresh one for the new slot is mandatory.
    dest_merc_binding: Optional[MercBinding] = None
    if dest_merc.Type == 2:
        try:
            new_merc_bio_id = merc_availability.compute_merc_bio_id(merc_xml_path, dest_slot)
            ui_idx = merc_availability.compute_ui_index(merc_xml_path)
            dest_merc_binding = MercBinding(
                uiIndex=ui_idx,
                Name=dest_merc.zName,
                ProfilId=dest_slot,
                MercBioID=new_merc_bio_id,
            )
        except ValueError:
            pass

    # 4. Execute the rewrites
    try:
        # 4a. Profile: clear source, write dest
        profiles_xml.clear_slot(profiles_path, source_slot)
        report.steps_completed.append(f"cleared MercProfiles[{source_slot}]")
        profiles_xml.upsert(profiles_path, dest_merc)
        report.steps_completed.append(f"wrote MercProfiles[{dest_slot}]")

        # 4b. AIM binding: remove source, add dest (if applicable)
        if source_aim is not None:
            aim_availability.remove(aim_path, source_slot)
            report.steps_completed.append(f"removed AIMAvailability[{source_slot}]")
        if dest_aim_binding is not None:
            aim_availability.upsert(aim_path, dest_aim_binding)
            report.steps_completed.append(
                f"wrote AIMAvailability[{dest_slot}] AimBioID={dest_aim_binding.AimBioID}"
            )

        # 4b'. MERC binding: same shape as AIM
        if source_merc_bio_id is not None and merc_xml_path is not None:
            merc_availability.remove(merc_xml_path, source_slot)
            report.steps_completed.append(f"removed MercAvailability[{source_slot}]")
        if dest_merc_binding is not None and merc_xml_path is not None:
            merc_availability.upsert(merc_xml_path, dest_merc_binding)
            report.steps_completed.append(
                f"wrote MercAvailability[{dest_slot}] MercBioID={dest_merc_binding.MercBioID}"
            )

        # 4c. Starting gear: rewrite mIndex
        if source_gear is not None:
            starting_gear.clear_slot(gear_path, source_slot)
            report.steps_completed.append(f"cleared MercStartingGear[{source_slot}]")
            new_gear = Gear(
                mIndex=dest_slot,
                mName=source_gear.mName,
                kits=source_gear.kits,
            )
            starting_gear.upsert(gear_path, new_gear)
            report.steps_completed.append(f"wrote MercStartingGear[{dest_slot}]")

        # 4d. EDT bio: cross-route — clear source's EDT, write dest's EDT
        edt.clear_bio(
            install_root, source_slot,
            aim_bio_id=source_aim,
            merc_bio_id=source_merc_bio_id,
            ctx=ctx,
        )
        report.steps_completed.append(
            f"cleared EDT[{source_slot}, aim_bio_id={source_aim}, merc_bio_id={source_merc_bio_id}]"
        )
        dest_aim_bio_id = dest_aim_binding.AimBioID if dest_aim_binding else None
        dest_merc_bio_id_val = dest_merc_binding.MercBioID if dest_merc_binding else None
        edt.write_bio(
            install_root, dest_slot,
            biography=source_bio,
            additional=source_addl,
            aim_bio_id=dest_aim_bio_id,
            merc_bio_id=dest_merc_bio_id_val,
            ctx=ctx,
        )
        report.steps_completed.append(
            f"wrote EDT[{dest_slot}, aim_bio_id={dest_aim_bio_id}, merc_bio_id={dest_merc_bio_id_val}]"
        )

        report.success = True
        return report
    except Exception as e:
        report.error = str(e)
        report.error_step = report.steps_completed[-1] if report.steps_completed else "init"
        return report


def duplicate(install_root: Path, source_slot: int, dest_slot: int) -> MoveReport:
    """Copy a merc from `source_slot` to `dest_slot` WITHOUT clearing source.

    Differs from move() in three ways:
      1. Source's MercProfiles entry is NOT cleared
      2. Source's AIMAvailability entry is NOT removed
      3. Source's EDT bio bytes stay where they are
      4. Source's MercStartingGear entry is NOT cleared

    The destination receives a complete independent copy: its own profile entry,
    its own gear block, its own AIM binding (if AIM-bound) with a fresh
    AimBioID, and its own EDT bio at the destination's offset.

    `ubFaceIndex` is copied from source — both mercs share STI portraits. This
    is fine engine-side (multiple uiIndexes can point at the same portrait);
    the Edit flow lets the player assign a different face later if desired.
    """
    install_root = Path(install_root)
    report = MoveReport(source_slot=source_slot, dest_slot=dest_slot)

    if source_slot == dest_slot:
        raise MoveError("Source and destination slots are the same")

    # Build ctx once; thread through `_read_source_merc` + every edt call.
    # See `move()` for rationale (bug-review C4).
    from .install_context import make_install_context
    ctx = make_install_context(install_root)

    source_merc, read_err = _read_source_merc(install_root, source_slot, ctx=ctx)
    if source_merc is None:
        raise MoveError(
            f"Could not read source slot {source_slot}: {read_err or 'unknown error'}"
        )

    profiles_path = ctx.profiles_xml_path(for_write=True)
    aim_path = ctx.aim_xml_path(for_write=True)
    merc_xml_path = ctx.merc_xml_path(for_write=True)
    gear_path = ctx.gear_xml_path(for_write=True)

    if profiles_xml.is_slot_occupied(profiles_path, dest_slot):
        raise MoveError(f"Destination slot {dest_slot} is occupied")

    source_gear = starting_gear.read_slot(gear_path, source_slot)
    source_aim = aim_availability.lookup_aim_bio_id(aim_path, source_slot)
    source_merc_bio_id = merc_availability.lookup_merc_bio_id(merc_xml_path, source_slot)
    source_bio, source_addl = edt.read_bio(
        install_root, source_slot,
        aim_bio_id=source_aim if source_aim is not None else None,
        merc_bio_id=source_merc_bio_id,
        ctx=ctx,
    )

    dest_merc = source_merc.model_copy(update={"uiIndex": dest_slot})

    # AIM binding for duplicate's destination — Type=AIM mercs always
    # need a row; the engine doesn't hardcode AIM ranges.
    dest_aim_binding: Optional[AimBinding] = None
    if dest_merc.Type == 1:
        new_aim_bio_id = aim_availability.compute_aim_bio_id(aim_path, dest_slot)
        dest_aim_binding = AimBinding(
            uiIndex=dest_slot,
            description=dest_merc.zName,
            ProfilId=dest_slot,
            AimBioID=new_aim_bio_id,
        )

    dest_merc_binding: Optional[MercBinding] = None
    if dest_merc.Type == 2:
        try:
            new_merc_bio_id = merc_availability.compute_merc_bio_id(merc_xml_path, dest_slot)
            ui_idx = merc_availability.compute_ui_index(merc_xml_path)
            dest_merc_binding = MercBinding(
                uiIndex=ui_idx,
                Name=dest_merc.zName,
                ProfilId=dest_slot,
                MercBioID=new_merc_bio_id,
            )
        except ValueError:
            pass

    try:
        # Profile: write dest only (source untouched)
        profiles_xml.upsert(profiles_path, dest_merc)
        report.steps_completed.append(f"wrote MercProfiles[{dest_slot}] (source kept)")

        # AIM binding: add dest if applicable (source's binding stays)
        if dest_aim_binding is not None:
            aim_availability.upsert(aim_path, dest_aim_binding)
            report.steps_completed.append(
                f"wrote AIMAvailability[{dest_slot}] AimBioID={dest_aim_binding.AimBioID}"
            )

        # MERC binding: same pattern
        if dest_merc_binding is not None and merc_xml_path is not None:
            merc_availability.upsert(merc_xml_path, dest_merc_binding)
            report.steps_completed.append(
                f"wrote MercAvailability[{dest_slot}] MercBioID={dest_merc_binding.MercBioID}"
            )

        # Gear: duplicate the block at dest (source kept)
        if source_gear is not None:
            new_gear = Gear(
                mIndex=dest_slot,
                mName=source_gear.mName,
                kits=source_gear.kits,
            )
            starting_gear.upsert(gear_path, new_gear)
            report.steps_completed.append(f"wrote MercStartingGear[{dest_slot}] (source kept)")

        # EDT: write dest's bio (source bio stays)
        dest_aim_bio_id = dest_aim_binding.AimBioID if dest_aim_binding else None
        dest_merc_bio_id_val = dest_merc_binding.MercBioID if dest_merc_binding else None
        edt.write_bio(
            install_root, dest_slot,
            biography=source_bio,
            additional=source_addl,
            aim_bio_id=dest_aim_bio_id,
            merc_bio_id=dest_merc_bio_id_val,
            ctx=ctx,
        )
        report.steps_completed.append(
            f"wrote EDT[{dest_slot}, aim_bio_id={dest_aim_bio_id}, merc_bio_id={dest_merc_bio_id_val}] (source kept)"
        )

        report.success = True
        return report
    except Exception as e:
        report.error = str(e)
        report.error_step = report.steps_completed[-1] if report.steps_completed else "init"
        return report
