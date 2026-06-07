"""Import a .wmerc bundle into a target install at a chosen slot."""
from __future__ import annotations

import json
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from .. import audit as audit_mod
from .. import backup as backup_mod
from .. import gap as gap_mod
from .. import voice as voice_mod
from ..inject import aim_availability, merc_availability, profiles_xml, starting_gear
from ..inject import edt as edt_mod
from ..inject._atomic_xml import write_bytes_atomic
from ..models import AimBinding, Gear, GearKit, Merc, MercBinding
from ..portrait.animate_skip import BoundingBox, DEFAULT_EYE_BOX, DEFAULT_MOUTH_BOX
from ..portrait.compile import compile_and_write_all
from .manifest import WmercManifest


@dataclass
class WmercContents:
    """The unpacked contents of a .wmerc bundle, in memory."""
    manifest: WmercManifest
    files: dict[str, bytes] = field(default_factory=dict)  # filename → raw bytes
    # Phase 2.2: entries we tried to extract but couldn't. List of
    # `(arcname, error_type_name)` tuples. Surfaced to the user via
    # `ImportReport.partial_failures` so corrupt/permission-denied entries
    # don't silently disappear.
    read_errors: list[tuple[str, str]] = field(default_factory=list)

    @property
    def has_portrait_source(self) -> bool:
        return "portrait_source.png" in self.files

    @property
    def has_extreme_master(self) -> bool:
        return "extreme_master.png" in self.files

    @property
    def has_animation_frames(self) -> bool:
        names = {"anim_eye_1.png", "anim_eye_2.png", "anim_mouth_1.png",
                 "anim_mouth_2.png", "anim_mouth_3.png"}
        return names.issubset(self.files.keys())

    @property
    def has_voice(self) -> bool:
        return any(n.startswith("voice/") for n in self.files)


@dataclass
class ImportReport:
    """Outcome of a deploy_import call — what got written, where, and any warnings."""
    target_slot: int
    files_written: list[str] = field(default_factory=list)
    bio_route: str = ""
    portrait_compiled: bool = False
    voice_clips_copied: int = 0
    aim_bio_id_used: Optional[int] = None
    merc_bio_id_used: Optional[int] = None
    issues: list[dict] = field(default_factory=list)
    # Non-fatal failures (e.g. portrait PNG corrupt, voice file write denied)
    # that didn't block the profile/AIM/gear/EDT writes.
    partial_failures: list[str] = field(default_factory=list)


class ImportAuditError(Exception):
    """Raised when the imported merc fails audit_full() with severity=ERROR."""
    def __init__(self, issues: list[dict]) -> None:
        super().__init__(f"Audit failed with {len(issues)} error(s)")
        self.issues = issues


class SlotOccupiedError(Exception):
    """Raised when the target slot already holds a merc and force=False."""
    def __init__(self, slot: int) -> None:
        super().__init__(f"Slot {slot} is occupied. Pass force=True to overwrite.")
        self.slot = slot


def _rollback_and_raise(
    orig_exc: Exception,
    step_label: str,
    *,
    backup_entry,
    install_id: str,
    install_root: Path,
    report: "ImportReport",
) -> None:
    """Record every file written so far + restore the snapshot + re-raise.

    Shared by steps 7-10's failure paths in `deploy_import`. The snapshot
    was taken at step 6 with the pre-write contents of all the merc's
    files; `record_files_created` adds the in-progress writes to its
    manifest so `restore` can delete them as part of its second phase.

    Never returns — always raises RuntimeError chained from `orig_exc`.
    """
    try:
        backup_mod.record_files_created(
            backup_id=backup_entry.id,
            install_id=install_id,
            files=[Path(p) for p in report.files_written],
        )
        backup_mod.restore(
            backup_id=backup_entry.id,
            install_id=install_id,
            install_root=install_root,
        )
    except Exception as rollback_err:
        raise RuntimeError(
            f"Import failed at {step_label} "
            f"({type(orig_exc).__name__}: {orig_exc}). "
            f"Automatic rollback ALSO failed "
            f"({type(rollback_err).__name__}: {rollback_err}). "
            f"Manual restore via Backups page is required "
            f"(snapshot id {backup_entry.id})."
        ) from orig_exc
    raise RuntimeError(
        f"Import failed at {step_label} "
        f"({type(orig_exc).__name__}: {orig_exc}). "
        f"Auto-rollback succeeded — install is back to the pre-import state."
    ) from orig_exc


def _is_safe_arcname(name: str) -> bool:
    """Reject any zip entry name that could escape its intended directory.

    A safe arcname is a non-empty relative path made of non-trivial segments.
    Reject:
      - Empty names or names containing NUL
      - Absolute paths (leading `/` or `\\`)
      - Windows drive letters (`C:...`)
      - Any segment that is empty, `.`, or `..` (path traversal)

    Defense in depth: callers should additionally verify the final write
    target resolves inside their expected destination directory
    (`Path.resolve().is_relative_to(base.resolve())`).

    Bug history: an earlier version returned
    `all(p not in ("", "..") for p in parts) or parts[-1] != ""`
    which due to `or` precedence let `"../etc/passwd"` through (the
    trailing-segment-nonempty check short-circuited the traversal check).
    """
    if not name or "\x00" in name:
        return False
    if name.startswith("/") or name.startswith("\\"):
        return False
    # Windows drive letters
    if len(name) >= 2 and name[1] == ":":
        return False
    parts = name.replace("\\", "/").split("/")
    return all(p not in ("", ".", "..") for p in parts)


# Caps on .wmerc decompression to stop a zip-bomb from OOMing the sidecar: a
# crafted bundle can ship a tiny deflated entry that expands to many GB. The
# declared uncompressed size (ZipInfo.file_size) is ATTACKER-CONTROLLED and is
# used ONLY as a cheap early reject; the real bound is enforced on the ACTUAL
# decompressed bytes by streaming each member through `_read_member_capped`.
# (zf.read() inflates the whole stream into RAM before its CRC check, so a lying
# header defeats a size-only gate -- see test_read_wmerc_forged_header_bomb_*.)
MAX_WMERC_ENTRY_BYTES = 256 * 1024 * 1024     # 256 MB per entry
MAX_WMERC_TOTAL_BYTES = 1024 * 1024 * 1024    # 1 GB total uncompressed
MAX_WMERC_ENTRIES = 10_000                     # member-count cap (vs millions of tiny entries)
_ZIP_READ_CHUNK = 1 << 20                      # 1 MiB streamed-read chunk


class _MemberTooLarge(Exception):
    """A zip member's ACTUAL decompressed size exceeded the passed cap."""


def _read_member_capped(zf: zipfile.ZipFile, name: str, max_bytes: int) -> bytes:
    """Decompress one zip member, bounding ACTUAL output to `max_bytes`.

    Streams via `zf.open()` in bounded chunks and aborts the moment the running
    total exceeds `max_bytes`, so a member whose declared `file_size` lies (a
    zip-bomb) can't inflate past the cap in RAM. `ZipExtFile.read(n)` bounds each
    decompress to ~n bytes, so peak stays ~`max_bytes` + one chunk regardless of
    the real (lied-about) size. Raises `_MemberTooLarge` on breach.
    """
    out = bytearray()
    with zf.open(name) as fh:
        while True:
            chunk = fh.read(_ZIP_READ_CHUNK)
            if not chunk:
                break
            out += chunk
            if len(out) > max_bytes:
                raise _MemberTooLarge(name)
    return bytes(out)


def read_wmerc(bundle_path: Path) -> WmercContents:
    """Open a .wmerc and parse the manifest. Doesn't yet write anything."""
    bundle_path = Path(bundle_path)
    if not bundle_path.is_file():
        raise FileNotFoundError(f"Bundle not found: {bundle_path}")

    with zipfile.ZipFile(bundle_path, "r") as zf:
        names = zf.namelist()
        # Member-count cap (alongside the per-entry / total BYTE caps): a bundle
        # with millions of tiny members would bloat namelist() + the files dict.
        if len(names) > MAX_WMERC_ENTRIES:
            raise ValueError(
                f"{bundle_path}: too many entries ({len(names)} > {MAX_WMERC_ENTRIES} cap)"
            )
        if "manifest.json" not in names:
            raise ValueError(f"{bundle_path}: missing manifest.json")
        # The manifest is the FIRST member read and can itself be the bomb, so
        # bound its ACTUAL decompressed size (declared-size pre-check is a cheap
        # early-out; the streaming read is the real bound).
        if zf.getinfo("manifest.json").file_size > MAX_WMERC_ENTRY_BYTES:
            raise ValueError(f"{bundle_path}: manifest.json is implausibly large")
        try:
            manifest_text = _read_member_capped(
                zf, "manifest.json", MAX_WMERC_ENTRY_BYTES
            ).decode("utf-8")
        except _MemberTooLarge:
            raise ValueError(f"{bundle_path}: manifest.json is implausibly large")
        except (zipfile.BadZipFile, UnicodeDecodeError) as e:
            # A corrupt/forged manifest member (e.g. a forged-header bomb whose CRC
            # check raises BadZipFile, or non-UTF-8 bytes) must surface as a clean
            # ValueError -> HTTP 400, not an unhandled exception -> 500 on
            # /bundle/import (the deploy route maps ValueError to 400).
            raise ValueError(f"{bundle_path}: manifest.json is unreadable ({type(e).__name__})")
        try:
            manifest_data = json.loads(manifest_text)
        except json.JSONDecodeError as e:
            raise ValueError(f"{bundle_path}: manifest.json is not valid JSON ({e})")
        manifest = WmercManifest.model_validate(manifest_data)

        files: dict[str, bytes] = {}
        read_errors: list[tuple[str, str]] = []
        total_bytes = 0
        for name in names:
            if name == "manifest.json":
                continue
            if not _is_safe_arcname(name):
                # Skip anything that looks like a path-traversal attempt
                continue
            # Cheap early reject on the (untrusted) declared size — skips an
            # honest oversized entry without streaming it at all.
            if zf.getinfo(name).file_size > MAX_WMERC_ENTRY_BYTES:
                read_errors.append((name, "EntryTooLarge"))
                continue
            # Real bound: enforce on ACTUAL decompressed bytes, capped also by the
            # remaining total budget so the sum across entries can't blow past it.
            remaining = MAX_WMERC_TOTAL_BYTES - total_bytes
            cap = min(MAX_WMERC_ENTRY_BYTES, remaining)
            try:
                data = _read_member_capped(zf, name, cap)
            except _MemberTooLarge:
                # cap == entry cap -> the entry itself is too big; otherwise the
                # remaining total budget was the binding limit.
                read_errors.append(
                    (name, "EntryTooLarge" if cap == MAX_WMERC_ENTRY_BYTES else "BundleTooLarge")
                )
                continue
            except (zipfile.BadZipFile, RuntimeError, OSError) as e:
                # Documented zf.read()/open() failure modes (LargeZipFile inherits
                # from BadZipFile; KeyError unreachable since `name` came from
                # namelist()). Surface to read_errors so deploy_import can put
                # them in partial_failures.
                read_errors.append((name, type(e).__name__))
                continue
            total_bytes += len(data)
            files[name] = data

    return WmercContents(manifest=manifest, files=files, read_errors=read_errors)


def import_merc(
    bundle_path: Path,
    install_root: Path,
    target_slot: Optional[int] = None,
    out_portrait_dir: Optional[Path] = None,
) -> WmercContents:
    """Read a .wmerc and stage it for import (preview mode — no writes).

    Kept for backward compatibility with the preview endpoint. For actual
    deployment use `deploy_import()`.
    """
    contents = read_wmerc(bundle_path)

    if target_slot is not None:
        # Rewrite the manifest's uiIndex to match the chosen slot
        merc_dict = contents.manifest.merc.model_dump()
        merc_dict["uiIndex"] = target_slot
        contents.manifest.merc = type(contents.manifest.merc)(**merc_dict)
        if contents.manifest.aim_binding is not None:
            ab = contents.manifest.aim_binding.model_dump()
            ab["uiIndex"] = target_slot
            ab["ProfilId"] = target_slot
            contents.manifest.aim_binding = type(contents.manifest.aim_binding)(**ab)
        if contents.manifest.merc_binding is not None:
            mb = contents.manifest.merc_binding.model_dump()
            mb["ProfilId"] = target_slot
            contents.manifest.merc_binding = type(contents.manifest.merc_binding)(**mb)

    # Optionally extract portrait PNGs to a working directory
    if out_portrait_dir is not None:
        out_portrait_dir = Path(out_portrait_dir)
        out_portrait_dir.mkdir(parents=True, exist_ok=True)
        for name, data in contents.files.items():
            if name.endswith(".png"):
                (out_portrait_dir / name).write_bytes(data)

    return contents


def deploy_import(
    install_root: Path,
    bundle_path: Path,
    install_id: str = "import",
    target_slot: Optional[int] = None,
    force: bool = False,
) -> ImportReport:
    """Actually write a .wmerc bundle into a target install.

    Steps (caller is expected to hold any cross-route write lock):
      1. Parse the bundle
      2. Remap slot-coupled fields (uiIndex, ProfilId, AimBioID, gear.mIndex)
      3. Audit (errors block; warnings pass through into the report)
      4. Slot occupancy check (block unless force=True)
      5. Clear old EDT bio on force-overwrite (in case AimBioID differs)
      6. Backup everything we're about to touch
      7. Write profile + AIM binding + gear + EDT bio
      8. Compile + write portrait STIs if PNGs are bundled
      9. Copy voice clips into Speech/<usVoiceIndex>/ if WAVs are bundled
    """
    install_root = Path(install_root)
    contents = read_wmerc(bundle_path)
    manifest = contents.manifest

    from ..install_context import make_install_context
    # Build the target's InstallContext ONCE and reuse downstream. Building
    # it parses the VFS config and probes flavor (~50-100 ms on a modded
    # install). Previously we rebuilt 4× per import call.
    target_ctx = make_install_context(install_root)
    ctx = target_ctx  # historical alias kept for the rest of the function
    profiles_path = ctx.profiles_xml_path(for_write=True)
    aim_path = ctx.aim_xml_path(for_write=True)
    merc_xml_path = ctx.merc_xml_path(for_write=True)
    gear_path = ctx.gear_xml_path(for_write=True)

    # ── Step 2: resolve target slot and rebuild slot-coupled models ──
    resolved_slot = target_slot if target_slot is not None else manifest.merc.uiIndex
    source_slot = manifest.merc.uiIndex  # captured BEFORE we mutate the merc

    merc_dict = manifest.merc.model_dump()
    merc_dict["uiIndex"] = resolved_slot
    merc = Merc(**merc_dict)

    gear_kits: list[GearKit] = list(manifest.gear) if manifest.gear else []
    gear: Optional[Gear] = None
    if gear_kits:
        gear = Gear(mIndex=resolved_slot, mName=merc.zName, kits=gear_kits)

    # AIM binding: bundle may carry one (the source install had an <AIM> row)
    # OR we auto-derive when the imported merc is Type=1 (AIM) but the source
    # carried no AIMAvailability row — export.py records `aim_binding=None` for
    # a Type=1 merc whose slot was never wired to the AIM laptop. Without this
    # fallback the profile is written as Type=1 but no AIM row is added, so the
    # merc never appears on the AIM laptop: the Marcus-at-slot-57 invisibility
    # trap, and exactly the case the audit's TYPE_NO_AIM_ROW warning and the
    # Import.tsx banner promise MercForge will fix on save. Mirrors the Type=2
    # → MERC-row auto-derive below and the Create flow's auto-fill in
    # routes/merc.py. Either way, rederive AimBioID from the target install to
    # avoid AimBioID collisions when porting bundles between installs / slot
    # ranges. Wrapped in try/except ValueError (matching the MERC path) so an
    # exhausted AimBioID pool degrades to "no row" rather than crashing import.
    aim_binding: Optional[AimBinding] = None
    needs_aim_row = manifest.aim_binding is not None or merc.Type == 1
    if needs_aim_row:
        try:
            new_bio_id = aim_availability.compute_aim_bio_id(aim_path, resolved_slot)
            if manifest.aim_binding is not None:
                ab_dict = manifest.aim_binding.model_dump()
            else:
                ab_dict = {"description": merc.zName or merc.zNickname}
            ab_dict["uiIndex"] = resolved_slot
            ab_dict["ProfilId"] = resolved_slot
            ab_dict["AimBioID"] = new_bio_id
            aim_binding = AimBinding(**ab_dict)
        except ValueError:
            pass

    # MERC binding: bundle may carry one (Type=2 source) OR we auto-derive
    # if the imported merc is Type=2. Either way, rederive MercBioID + the
    # display uiIndex from the target install to avoid collisions when
    # porting between mods. The bundle's `merc_binding.uiIndex` is the
    # source mod's M.E.R.C.-website display order — meaningless in the
    # target.
    merc_binding: Optional[MercBinding] = None
    needs_merc_row = manifest.merc_binding is not None or merc.Type == 2
    if needs_merc_row and merc_xml_path is not None:
        try:
            new_merc_bio_id = merc_availability.compute_merc_bio_id(merc_xml_path, resolved_slot)
            existing_rows = merc_availability.read_all(merc_xml_path)
            new_ui_idx = existing_rows[resolved_slot].uiIndex if resolved_slot in existing_rows \
                else merc_availability.compute_ui_index(merc_xml_path)
            if manifest.merc_binding is not None:
                mb_dict = manifest.merc_binding.model_dump()
            else:
                mb_dict = {
                    "Name": merc.zName or merc.zNickname,
                    "Drunk": 0,
                    "uiAlternateIndex": -1,
                    "StartMercsAvailable": 1,
                    "NewMercsAvailable": 0,
                    "usMoneyPaid": 0,
                    "usDay": 0,
                }
            mb_dict["uiIndex"] = new_ui_idx
            mb_dict["ProfilId"] = resolved_slot
            mb_dict["MercBioID"] = new_merc_bio_id
            merc_binding = MercBinding(**mb_dict)
        except ValueError:
            pass

    # ── Step 3a: audit ──
    from ..slot_picker import build_slot_picker
    picker = build_slot_picker(install_root, ctx=target_ctx)
    slot_info = picker.slots[resolved_slot] if 0 <= resolved_slot < len(picker.slots) else None
    issues = audit_mod.audit_full(merc, gear=gear, aim_binding=aim_binding, slot_info=slot_info)
    if audit_mod.has_errors(issues):
        raise ImportAuditError([i.model_dump() for i in issues])

    # ── Step 3b: cross-mod schema compatibility (must run BEFORE writes) ──
    # Compare source fingerprint against target schema. Severe mismatches
    # (sparse vs dense MercOpinions) corrupt the target if we write first.
    # Surface warnings into a pre-write report; the caller can choose to
    # proceed (warnings populate `report.partial_failures`) or abort.
    pre_write_warnings: list[str] = []
    _check_cross_mod_compatibility_into(manifest, install_root, pre_write_warnings)

    # ── Step 4: slot-occupancy check ──
    is_occupied = profiles_xml.is_slot_occupied(profiles_path, resolved_slot)
    if is_occupied and not force:
        raise SlotOccupiedError(resolved_slot)

    # ── Step 6: backup ──
    # MUST happen BEFORE the force-overwrite clear_bio below — otherwise the
    # snapshot captures the post-clear state and a step-7 rollback can't
    # recover the previous occupant's bio (Phase 2.1 fix). The snapshot is
    # cheap if nothing's been touched yet, and idempotent.
    backup_files = backup_mod.files_for_merc(install_root, resolved_slot, merc.ubFaceIndex)
    backup_entry = backup_mod.snapshot(
        install_root=install_root,
        install_id=install_id,
        files_to_back_up=backup_files,
        reason=f"import_slot_{resolved_slot}",
    )

    # ── Step 5: clear old bio on force-overwrite ──
    # Now safe to clear: the snapshot above captured the pre-clear bytes,
    # so a later step-7 failure rolls back to the previous occupant's bio.
    if is_occupied and force:
        old_aim_bio_id = aim_availability.lookup_aim_bio_id(aim_path, resolved_slot)
        old_merc_bio_id = merc_availability.lookup_merc_bio_id(merc_xml_path, resolved_slot)
        try:
            edt_mod.clear_bio(
                install_root,
                ui_index=resolved_slot,
                aim_bio_id=old_aim_bio_id,
                merc_bio_id=old_merc_bio_id,
                ctx=target_ctx,
            )
        except (FileNotFoundError, ValueError):
            # No prior bio is fine — fresh install, or expanded MERC slot with
            # no per-file EDT yet.
            pass

    report = ImportReport(
        target_slot=resolved_slot,
        aim_bio_id_used=aim_binding.AimBioID if aim_binding else None,
        merc_bio_id_used=merc_binding.MercBioID if merc_binding else None,
        issues=[i.model_dump() for i in issues],
    )
    # Surface the pre-write schema warnings on the report
    report.partial_failures.extend(pre_write_warnings)
    # Phase 2.2: surface any zip-read errors so the user knows which
    # bundle entries couldn't be extracted instead of them silently
    # disappearing into nothing.
    for arc_name, err_type in contents.read_errors:
        report.partial_failures.append(
            f"bundle entry {arc_name!r} failed to read: {err_type}"
        )

    # ── Steps 7-10 are wrapped in a rollback try/except. If ANY write
    # raises mid-way (file permission denied because user has the file
    # open in another editor; disk full; corrupted XML), restore from
    # the snapshot we just took and re-raise with context.
    try:
        # ── Step 7: profile + AIM + MERC + gear + EDT bio ──
        profiles_xml.upsert(profiles_path, merc)
        report.files_written.append(str(profiles_path))

        if aim_binding is not None:
            aim_availability.upsert(aim_path, aim_binding)
            report.files_written.append(str(aim_path))

        if merc_binding is not None and merc_xml_path is not None:
            merc_availability.upsert(merc_xml_path, merc_binding)
            report.files_written.append(str(merc_xml_path))

        if gear is not None:
            starting_gear.upsert(gear_path, gear)
            report.files_written.append(str(gear_path))

        route = edt_mod.write_bio(
            install_root,
            ui_index=resolved_slot,
            biography=merc.biographyText,
            additional=merc.additionalInfoText,
            aim_bio_id=aim_binding.AimBioID if aim_binding else None,
            merc_bio_id=merc_binding.MercBioID if merc_binding else None,
            ctx=target_ctx,
        )
    except (ImportAuditError, SlotOccupiedError):
        # These are caller-actionable; no destructive writes occurred yet.
        raise
    except Exception as e:
        _rollback_and_raise(
            e, "Step 7 (profile/AIM/MERC/gear/EDT)",
            backup_entry=backup_entry,
            install_id=install_id,
            install_root=install_root,
            report=report,
        )

    # Compact bio_route (kind + record_index + filename) rather than the
    # full EDTRoute repr (which leaks the absolute file path into the
    # HTTP response with no consumer-side use).
    report.bio_route = f"{route.kind}#{route.record_index}@{route.path.name}"
    report.files_written.append(str(route.path))

    # ── Step 8: portrait STIs ──
    # Step-level failures (anything that escapes the inner try/except below)
    # trigger a full rollback of everything written so far. Per-file failures
    # inside the wrapper land in partial_failures and don't roll back.
    try:
        _step8_portrait(
            contents=contents,
            manifest=manifest,
            merc=merc,
            install_root=install_root,
            report=report,
        )
    except Exception as e:
        _rollback_and_raise(
            e, "Step 8 (portrait STIs)",
            backup_entry=backup_entry,
            install_id=install_id,
            install_root=install_root,
            report=report,
        )

    # ── Step 9: voice clips ──
    try:
        _step9_voice(
            contents=contents,
            manifest=manifest,
            target_ctx=target_ctx,
            install_root=install_root,
            source_slot=source_slot,
            resolved_slot=resolved_slot,
            report=report,
        )
    except Exception as e:
        _rollback_and_raise(
            e, "Step 9 (voice clips)",
            backup_entry=backup_entry,
            install_id=install_id,
            install_root=install_root,
            report=report,
        )

    # ── Step 10: mod-specific extras ──
    # Battlesnds, NPC_Speech, snitch audio, raw face STIs, BigItems, NPC
    # dialogue script, mod XML table rows. Reuse the InstallContext built
    # at the top — no need to re-parse the VFS chain.
    try:
        _install_extras(
            contents=contents,
            target_ctx=target_ctx,
            source_slot=source_slot,
            target_slot=resolved_slot,
            face_index=merc.ubFaceIndex,
            report=report,
            portrait_already_compiled=report.portrait_compiled,
        )
    except Exception as e:
        _rollback_and_raise(
            e, "Step 10 (mod extras)",
            backup_entry=backup_entry,
            install_id=install_id,
            install_root=install_root,
            report=report,
        )

    # Record every file the import wrote into the snapshot manifest so a
    # restore can fully roll back (including files the op CREATED that
    # weren't in the pre-write snapshot's `files`).
    backup_mod.record_files_created(
        backup_id=backup_entry.id,
        install_id=install_id,
        files=[Path(p) for p in report.files_written],
    )

    return report


# ──────────────────────────────────────────────────────────────────────────
#  Extras installation (battlesnds, npc_speech, snitch, raw STIs, BigItems,
#  NPC script, table rows). Each runs in a defensive try-block; failures
#  land in `report.partial_failures` rather than aborting the import.
# ──────────────────────────────────────────────────────────────────────────


def _check_cross_mod_compatibility_into(manifest, install_root: Path, warnings: list[str]) -> None:
    """Compare bundle's source fingerprint to target install's schema.

    Appends a warning string per detected incompatibility to `warnings`.
    Does not raise — caller decides whether to proceed. Runs BEFORE the
    write phase so the caller can refuse on severe mismatches without
    leaving a partially-written merc.
    """
    src = manifest.schema_fingerprint
    if src is None:
        return  # old bundle without fingerprint; nothing to compare

    from ..install_context import make_install_context
    from xml.etree import ElementTree as ET
    ctx = make_install_context(install_root)

    # Probe target MercOpinions format
    target_opinions_format: Optional[str] = None
    opinions_path = ctx.extra_table_path("merc_opinions")
    if opinions_path and opinions_path.is_file():
        try:
            with open(opinions_path, "r", encoding="utf-8", errors="replace") as f:
                head = f.read(20000)
            if "<AnOpinion" in head:
                target_opinions_format = "sparse"
            elif "<Opinion0>" in head or "<Opinion1>" in head:
                target_opinions_format = "dense"
        except OSError:
            pass

    if (
        src.merc_opinions_format
        and target_opinions_format
        and src.merc_opinions_format != target_opinions_format
    ):
        warnings.append(
            f"MercOpinions format mismatch: source uses {src.merc_opinions_format!r} "
            f"({'<AnOpinion ...>' if src.merc_opinions_format == 'sparse' else '<OpinionN>'}), "
            f"target uses {target_opinions_format!r}. "
            "Cross-mod opinions can't be ported automatically — "
            "the row was skipped or only the slot key was upserted."
        )

    # Probe target MercProfiles for STOMP block presence (sample one row)
    target_has_stomp = False
    target_has_growth_mods = False
    target_has_bEvolution = False
    target_has_fRegresses = False
    first_row = None  # init OUTSIDE the try so the unrelated later branch can read it
    try:
        tree = ET.parse(str(ctx.profiles_xml_path()))
        first_row = next(iter(tree.getroot()), None)
        if first_row is not None:
            tags = {child.tag for child in first_row}
            target_has_stomp = "bRace" in tags and "bNationality" in tags and "usBackground" in tags
            # Match both the engine's on-disk <bGrowthModifier*> and the
            # prefix-less spelling — startswith("GrowthModifier") alone misses
            # the b-prefixed tags every real growth-mod install actually ships,
            # which made this warning a false positive for AIMNAS/Vengeance.
            target_has_growth_mods = any("GrowthModifier" in t for t in tags)
            target_has_bEvolution = "bEvolution" in tags
            target_has_fRegresses = "fRegresses" in tags
    except (ET.ParseError, OSError, StopIteration):
        pass

    if src.has_stomp_block and not target_has_stomp:
        warnings.append(
            "Source profile has STOMP-era fields (bRace, bNationality, usBackground, etc.) "
            "but target install uses a pre-STOMP schema — those fields will be ignored by "
            "the target engine."
        )

    if src.has_growth_modifiers and not target_has_growth_mods:
        warnings.append(
            "Source profile carries 11 AIMNAS-only GrowthModifier* fields that the target "
            "install's engine won't read — stat-growth tuning will fall back to engine defaults."
        )

    if src.has_bEvolution and target_has_fRegresses and not target_has_bEvolution:
        warnings.append(
            "Source uses <bEvolution> but target uses <fRegresses> (AIMNAS-style rename). "
            "The evolution/regression flag will not transfer cleanly."
        )
    if src.has_fRegresses and target_has_bEvolution and not target_has_fRegresses:
        warnings.append(
            "Source uses <fRegresses> (AIMNAS-style) but target uses <bEvolution>. "
            "The regression flag will not transfer cleanly."
        )

    if src.has_usVoiceIndex is False:
        # Source (e.g. Vengeance) lacks usVoiceIndex per the research; the target
        # may inherit the default value 15 (Tycho's voice) for the imported merc.
        # Surface only if the target schema HAS usVoiceIndex (i.e. it's a divergence).
        if first_row is not None and any(c.tag == "usVoiceIndex" for c in first_row):
            warnings.append(
                "Source profile has no <usVoiceIndex> field. The imported merc will "
                "fall back to the model default (15, Tycho's voice) unless set manually."
            )


def _rename_slot_in_filename(name: str, source_slot: int, target_slot: int) -> str:
    """Rewrite a filename's slot references.

    Handles:
      - Prefix:   `<source>_<rest>.ogg`  ->  `<target>_<rest>.ogg`
      - Suffix:   `<other>_<source>.ogg` ->  `<other>_<target>.ogg`
      - Whole:    `<source>.EDT`         ->  `<target>.EDT`
      - Embedded: `gun<source>.sti`      ->  `gun<target>.sti` (BigItems pattern)

    Idempotent if source==target.
    """
    if source_slot == target_slot:
        return name
    s_str = str(source_slot)
    t_str = str(target_slot)
    # Most reliable: rename only if the source slot appears as a standalone
    # token in the filename. Use boundaries of '_', '.', or string ends.
    import re
    # Wrap the slot in (?<![A-Za-z0-9]) / (?![A-Za-z0-9]) so we don't replace
    # digit-runs that happen to contain the slot (e.g. "12180" if slot is 218).
    return re.sub(
        rf"(?<![A-Za-z0-9]){s_str}(?![A-Za-z0-9])",
        t_str,
        name,
    )


def _step8_portrait(
    *,
    contents: WmercContents,
    manifest: WmercManifest,
    merc: Merc,
    install_root: Path,
    report: ImportReport,
) -> None:
    """Compile + write the 4 canonical face STIs from the bundled portrait
    PNG. No-op if the bundle has no portrait_source.png.

    Wrapped by `deploy_import`'s step 8 try/except — any unhandled
    exception escapes here and triggers full rollback. Per-call failures
    (corrupt PNG, palette quantize error) are absorbed into
    `report.partial_failures` and don't roll back.
    """
    if not contents.has_portrait_source:
        return

    # eye_box / mouth_box determine where compile.py crops the 17×6 (eye)
    # and 14×6 (mouth) sub-frames out of the new SmallFace. The engine
    # READS the SAME coords from MercProfiles.xml's usEyesX/Y and
    # usMouthX/Y to position the strips at render time. If compile-crop
    # and engine-render coords don't match, the animation strips appear
    # at the wrong spot — a user saw this as "eyes and mouth floating" on
    # Eskimo 2026-05-14.
    #
    # Priority for choosing the box:
    #   1. Explicit bundle portrait metadata (rare — most exports don't
    #      set it).
    #   2. The merc's profile coords (usEyesX/Y, usMouthX/Y) from the
    #      bundle — guaranteed to match what the engine reads from the
    #      written MercProfiles row.
    #   3. DEFAULT_EYE_BOX / DEFAULT_MOUTH_BOX as last resort.
    if manifest.portrait.eye_box:
        eye_box = BoundingBox(
            x=manifest.portrait.eye_box.get("x", DEFAULT_EYE_BOX.x),
            y=manifest.portrait.eye_box.get("y", DEFAULT_EYE_BOX.y),
        )
    elif merc.usEyesX > 0 or merc.usEyesY > 0:
        eye_box = BoundingBox(x=merc.usEyesX, y=merc.usEyesY)
    else:
        eye_box = DEFAULT_EYE_BOX

    if manifest.portrait.mouth_box:
        mouth_box = BoundingBox(
            x=manifest.portrait.mouth_box.get("x", DEFAULT_MOUTH_BOX.x),
            y=manifest.portrait.mouth_box.get("y", DEFAULT_MOUTH_BOX.y),
        )
    elif merc.usMouthX > 0 or merc.usMouthY > 0:
        mouth_box = BoundingBox(x=merc.usMouthX, y=merc.usMouthY)
    else:
        mouth_box = DEFAULT_MOUTH_BOX
    # Optional alternate-authoring inputs from the bundle:
    #   - bigface_source.png: separately-framed AIM/M.E.R.C. hero portrait
    #   - anim_eye_1..4.png:   per-slot eye animation sub-frames
    #   - anim_mouth_1..3.png: per-slot mouth animation sub-frames
    # Each is optional; if present, compile_and_write_all takes the
    # explicit-frames path for that region. This is how a Vengeance
    # merc's hand-authored blink survives a .wmerc round-trip.
    bigface_source = contents.files.get("bigface_source.png")
    explicit_eye = [
        contents.files[name]
        for name in ("anim_eye_1.png", "anim_eye_2.png", "anim_eye_3.png", "anim_eye_4.png")
        if name in contents.files
    ] or None
    explicit_mouth = [
        contents.files[name]
        for name in ("anim_mouth_1.png", "anim_mouth_2.png", "anim_mouth_3.png")
        if name in contents.files
    ] or None
    try:
        sti_paths = compile_and_write_all(
            install_root=install_root,
            face_index=merc.ubFaceIndex,
            source_png_bytes=contents.files["portrait_source.png"],
            skip_animation=True,
            eye_box=eye_box,
            mouth_box=mouth_box,
            bigface_source_png=bigface_source,
            explicit_eye_pngs=explicit_eye,
            explicit_mouth_pngs=explicit_mouth,
        )
        report.files_written.extend(sti_paths)
        report.portrait_compiled = True
    except Exception as e:
        report.partial_failures.append(
            f"portrait_compile_failed: {type(e).__name__}: {e}"
        )


def _safe_write(
    dest: Path,
    data: bytes,
    category: str,
    *,
    install_root_resolved: Path,
    report: ImportReport,
) -> bool:
    """Write `data` to `dest`, but only if `dest` resolves inside
    `install_root_resolved`. The shared zip-slip backstop for every
    .wmerc-driven file write — voice clips, carried `.gap` sidecars, and
    all the `_install_extras` categories.

    Defense in depth: even though `read_wmerc` already drops traversal
    arcnames via `_is_safe_arcname`, re-verify the FINAL write target
    resolves inside the install root. Catches a `..` accidentally
    introduced by rename / join code paths (e.g. `_rename_slot_in_filename`)
    and the historical zip-slip where the arcname check let "../foo"
    through. Returns True on a successful write; False if the path was
    rejected or the OS write failed (both recorded in
    `report.partial_failures`).
    """
    try:
        dest_resolved = dest.resolve() if dest.exists() else (dest.parent.resolve() / dest.name)
        try:
            dest_resolved.relative_to(install_root_resolved)
        except ValueError:
            report.partial_failures.append(
                f"{category} write rejected (path escapes install root): {dest}"
            )
            return False

        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(data)
        report.files_written.append(str(dest))
        return True
    except OSError as e:
        report.partial_failures.append(
            f"{category} write failed at {dest}: {type(e).__name__}: {e}"
        )
        return False


def _step9_voice(
    *,
    contents: WmercContents,
    manifest: WmercManifest,
    target_ctx,
    install_root: Path,
    source_slot: int,
    resolved_slot: int,
    report: ImportReport,
) -> None:
    """Copy bundled voice clips into the target install's Speech layer, keeping
    each clip's `.gap` lip-sync sidecar in sync.

    Flavor-aware: vanilla layout writes Speech/<voice_index>/<name>;
    Vengeance-style mods use Speech/<slot>_<idx>.<ext> at the Speech/
    root. Uses the TARGET install's flavor, not the source's.

    `.gap` handling (mirrors the voice-upload path's `write_gap_beside`):
      - A bundle-CARRIED `<stem>.gap` always wins and is written verbatim —
        never regenerated, never deleted. This is the only way an authored
        Vengeance `.ogg` gap (which can't be regenerated from ogg) survives a
        round-trip.
      - Otherwise, for a `.wav` clip a fresh gap is generated; for any other
        clip (or a silence-free wav) a stale same-named `.gap` left from a
        prior import is cleared so it can't mis-sync the new audio.
    Carried gaps are written in a final pass, so the outcome is independent of
    `contents.files` dict iteration order.

    Wrapped by `deploy_import`'s step 9 try/except — per-clip OSError
    lands in partial_failures and continues; anything else escapes and
    triggers full rollback.
    """
    if not (contents.has_voice and manifest.voice is not None):
        return

    source_voice_index = manifest.voice.voice_index
    voice_ctx = target_ctx
    # Resolve once for the per-write traversal-containment backstop shared
    # with `_install_extras`: every raw write below routes through
    # `_safe_write` so a traversal-injected dest can't escape the install.
    install_root_resolved = target_ctx.install_root.resolve()
    is_slot_prefix = voice_ctx.flavor.voice_layout == "slot_prefix"
    # In slot_prefix layout (Vengeance) the audio filenames are keyed by the
    # merc's SLOT, not the voice_index. The bundle's `voice_index` and
    # `source_slot` are usually identical on Vengeance (no voice donors), but
    # for safety rename by the source slot — matching how battlesnds/npc_speech
    # do it.
    slot_token = source_slot if is_slot_prefix else source_voice_index

    def _final_name(entry_name: str) -> str:
        # slot_prefix renames by the source slot; vanilla keeps the name
        # verbatim. Applied identically to a clip and its carried .gap so their
        # stems still match after renaming.
        if is_slot_prefix:
            return _rename_slot_in_filename(entry_name, slot_token, resolved_slot)
        return entry_name

    def _dest_for(final_name: str) -> Path:
        if is_slot_prefix:
            return voice_ctx.speech_root(for_write=True) / final_name
        return voice_ctx.speech_root(for_write=True) / str(source_voice_index) / final_name

    # Split the bundle's voice/ entries into clips and CARRIED .gap sidecars,
    # keyed by the final on-disk stem so a clip and its carried gap match even
    # after slot-renaming.
    clip_entries: list[tuple[str, bytes]] = []   # (final_name, data)
    carried_gaps: dict[str, bytes] = {}          # final_stem -> verbatim gap bytes
    for arcname, data in contents.files.items():
        if not arcname.startswith("voice/"):
            continue
        entry_name = arcname[len("voice/"):]
        if not entry_name:
            continue
        final_name = _final_name(entry_name)
        if final_name.lower().endswith(".gap"):
            carried_gaps[Path(final_name).stem] = data
        else:
            clip_entries.append((final_name, data))
    clip_stems = {Path(name).stem for name, _ in clip_entries}

    # ── Pass 1: write the clips, and (only when the bundle didn't carry a gap
    # for that clip) manage the clip's own gap. ──
    for final_name, data in clip_entries:
        stem = Path(final_name).stem
        try:
            if is_slot_prefix:
                dest = _dest_for(final_name)
                if not _safe_write(
                    dest, data, "voice_clip",
                    install_root_resolved=install_root_resolved, report=report,
                ):
                    continue
                report.voice_clips_copied += 1
                # Generate from a decodable .wav, or clear a stale sidecar left
                # by a prior import overwrite. Skipped when a carried gap will
                # win in pass 2. The gap lands beside `dest`, already proven
                # inside the install root by _safe_write above.
                if stem not in carried_gaps:
                    generated = gap_mod.write_gap_beside(dest, data)
                    if generated is not None:
                        report.files_written.append(str(generated))
            else:
                # Vanilla subdir layout: Speech/<voice_index>/<name>.
                # add_clip_bytes already generates/clears this clip's gap.
                clip = voice_mod.add_clip_bytes(
                    install_root,
                    source_voice_index,
                    final_name,
                    data,
                )
                report.files_written.append(clip.path)
                report.voice_clips_copied += 1
                # Track a freshly-generated gap for rollback when no carried
                # gap will overwrite it in pass 2.
                if stem not in carried_gaps:
                    gen_gap = Path(clip.path).with_suffix(".gap")
                    if gen_gap.is_file():
                        report.files_written.append(str(gen_gap))
        except ValueError:
            # Unsupported extension — skip with no fanfare
            continue
        except OSError as e:
            report.partial_failures.append(
                f"voice_copy_failed for {final_name}: {type(e).__name__}: {e}"
            )

    # ── Pass 2: write every CARRIED gap verbatim, beside its clip. A carried
    # gap is authored and must win over any generated/cleared sidecar, so it's
    # written last; it is NEVER deleted. Skip a carried gap whose clip wasn't
    # imported so a stray gap can't litter the install. ──
    for stem, gap_bytes in carried_gaps.items():
        if stem not in clip_stems:
            continue
        dest = _dest_for(f"{stem}.gap")
        _safe_write(
            dest, gap_bytes, "voice_gap",
            install_root_resolved=install_root_resolved, report=report,
        )


def _install_extras(
    contents: WmercContents,
    target_ctx,
    source_slot: int,
    target_slot: int,
    face_index: int,
    report: ImportReport,
    portrait_already_compiled: bool = False,
) -> None:
    """Route every category of bundle extras into the target install.

    If `portrait_already_compiled` is True (step 8 succeeded), skip the
    `raw_stis/` writes — the canonical 4 STI sizes already landed via the
    portrait pipeline, and dropping the verbatim originals on top would
    just double-write the same paths (or worse, shadow them with a stale
    pre-quantize copy from the source). The raw STIs are only useful
    when the bundle has NO portrait_source.png.
    """

    # Resolve the install root once for traversal-containment checks below.
    install_root_resolved = target_ctx.install_root.resolve()

    def safe_write(dest: Path, data: bytes, category: str) -> bool:
        # Thin binding of the shared backstop to this call's root + report.
        return _safe_write(
            dest, data, category,
            install_root_resolved=install_root_resolved, report=report,
        )

    # ── audio/battlesnds/ ──────────────────────────────────────────────
    bs_root = target_ctx.battlesnds_root(for_write=True)
    for arcname, data in contents.files.items():
        if not arcname.startswith("audio/battlesnds/"):
            continue
        name = arcname[len("audio/battlesnds/"):]
        new_name = _rename_slot_in_filename(name, source_slot, target_slot)
        safe_write(bs_root / new_name, data, "battlesnds")

    # ── audio/npc_speech/ ──────────────────────────────────────────────
    ns_root = target_ctx.npc_speech_root(for_write=True)
    for arcname, data in contents.files.items():
        if not arcname.startswith("audio/npc_speech/"):
            continue
        name = arcname[len("audio/npc_speech/"):]
        new_name = _rename_slot_in_filename(name, source_slot, target_slot)
        safe_write(ns_root / new_name, data, "npc_speech")

    # ── audio/snitch_names/ and snitch_names_alt/ ──────────────────────
    for alt_flag, prefix in ((False, "audio/snitch_names/"), (True, "audio/snitch_names_alt/")):
        sn_root = target_ctx.snitch_names_dir(alt=alt_flag, for_write=True)
        for arcname, data in contents.files.items():
            if not arcname.startswith(prefix):
                continue
            name = arcname[len(prefix):]
            new_name = _rename_slot_in_filename(name, source_slot, target_slot)
            safe_write(sn_root / new_name, data, "snitch_names_alt" if alt_flag else "snitch_names")

    # ── raw_stis/ — only install if portrait wasn't already compiled in step 8.
    # When `compile_and_write_all` succeeded, the canonical 4 STIs are already
    # written from the bundled portrait PNG via the engine-correct quantize
    # pipeline. Layering the raw STIs on top would just shadow those with a
    # potentially-different pre-quantize copy. The verbatim raw STIs are
    # only useful as a fallback for metadata-only bundles that didn't ship
    # a portrait PNG. (See bug-sweep #4.)
    if not portrait_already_compiled:
        for arcname, data in contents.files.items():
            if not arcname.startswith("raw_stis/"):
                continue
            rel = arcname[len("raw_stis/"):]
            # rel looks like: "Faces/BigFaces/218.STI" or "Faces/DESERTCAMO/218.sti"
            # File names carry the source's face_index. Since deploy_import
            # preserves merc.ubFaceIndex verbatim, this lands at the same
            # face index on the target — no rename needed.
            top_dir = target_ctx.layout.mod_content_path("")
            safe_write(top_dir / rel, data, "raw_stis")

    # ── big_items/ — slot-encoded filenames need rename ────────────────
    bi_root = target_ctx.big_items_dir(for_write=True)
    for arcname, data in contents.files.items():
        if not arcname.startswith("big_items/"):
            continue
        name = arcname[len("big_items/"):]
        new_name = _rename_slot_in_filename(name, source_slot, target_slot)
        safe_write(bi_root / new_name, data, "big_items")

    # ── npc_script/<source_slot>.EDT ──────────────────────────────────
    for arcname, data in contents.files.items():
        if not arcname.startswith("npc_script/"):
            continue
        name = arcname[len("npc_script/"):]
        new_name = _rename_slot_in_filename(name, source_slot, target_slot)
        # Write into the target's NPCData/ dir at the mod content root
        dest = target_ctx.layout.mod_content_path(f"NPCData/{new_name}")
        safe_write(dest, data, "npc_script")

    # ── facegear/ — per-merc overlays into matching Face_*.sti partners ─
    _install_facegear_overlays(contents, target_ctx, face_index, report)

    # ── table_rows/ — upsert each row into the target install's same table ─
    _install_table_rows(contents, target_ctx, source_slot, target_slot, report)


def _install_facegear_overlays(
    contents: WmercContents,
    target_ctx,
    face_index: int,
    report: ImportReport,
) -> None:
    """Inject each bundled per-merc FaceGear overlay into the matching target STI.

    Bundle layout: `facegear/<sti_stem>.png` (e.g. `facegear/Face_SunGoggles.png`).
    Source: extracted from the source install's frame[face_index] of that STI.

    On import, the matching `<sti_stem>.sti` is found in the target install's
    `Data*/faces/FACESGEAR/` and the overlay is injected at the target's
    face_index. The matching `_IMP.sti` partner is mirrored to as well. If
    the target install lacks the FaceGear item (vanilla doesn't ship every
    mod's gear), the bundled overlay is silently skipped with a warning.

    `inject_overlay` extends the target STI when face_index >= frame count,
    so the new merc can equip the gear even if the target's STI was shorter
    than the source's.
    """
    from ..facegear import detect_facegear_capacities, inject_overlay

    facegear_arcs = [
        (arcname, data)
        for arcname, data in contents.files.items()
        if arcname.startswith("facegear/") and arcname.lower().endswith(".png")
    ]
    if not facegear_arcs:
        return

    target_infos = detect_facegear_capacities(target_ctx)
    by_stem_lower = {
        info.path.stem.lower(): info
        for info in target_infos
        if not info.is_imp_variant
    }

    for arcname, data in facegear_arcs:
        fname = arcname[len("facegear/"):]
        stem = fname[: -len(".png")] if fname.lower().endswith(".png") else fname
        target_info = by_stem_lower.get(stem.lower())
        if target_info is None:
            report.partial_failures.append(
                f"facegear overlay '{fname}' has no matching STI in target install — skipped"
            )
            continue
        paths_to_write = [target_info.path]
        imp_candidate = target_info.path.with_name(
            f"{target_info.path.stem}_IMP{target_info.path.suffix}"
        )
        if imp_candidate.exists():
            paths_to_write.append(imp_candidate)
        for p in paths_to_write:
            try:
                inject_overlay(p, face_index, data)
                report.files_written.append(str(p))
            except (ValueError, OSError) as e:
                report.partial_failures.append(
                    f"facegear overlay {p.name} failed: {type(e).__name__}: {e}"
                )


def _install_table_rows(
    contents: WmercContents,
    target_ctx,
    source_slot: int,
    target_slot: int,
    report: ImportReport,
) -> None:
    """Parse each `table_rows/<filename>` fragment and upsert it into the
    target install's same-named XML table at the new slot.

    Skips tables the target install doesn't have (graceful — the user gets
    a warning in `report.partial_failures`).
    """
    from ..install_context import EXTRA_TABLES

    # Reverse lookup: filename -> (key, id_tag)
    filename_to_key: dict[str, tuple[str, str]] = {
        filename: (key, id_tag) for key, (filename, id_tag) in EXTRA_TABLES.items()
    }

    # These XML tables are intentionally NOT bundle-extras data. They appear
    # in legacy bundles (e.g. the hand-coded Vengeance Eskimo export) but
    # must be silently ignored on import:
    #
    #   AIMAvailability.xml  — canonically carried by `manifest.aim_binding`;
    #                          the import already writes the auto-allocated
    #                          AimBioID row. Overlaying the source's verbatim
    #                          row would clobber that with stale BioID values
    #                          that point past the target install's AIMBIOS.EDT.
    #
    #   MercAvailability.xml — same logic with `manifest.merc_binding`.
    #                          Concrete failure mode observed 2026-05-14:
    #                          Eskimo's import wrote MercBioID=42 (auto) into
    #                          a clean row, then table_rows processing
    #                          overwrote it with MercBioID=47 from the
    #                          Vengeance source. The target install's MERCBIOS.EDT
    #                          had only 43 records → bio landed past EOF →
    #                          the M.E.R.C. display fell back to slot 0
    #                          (Narg). Never process this here.
    #
    #   Vehicles.xml         — vehicles aren't merc data; bundling the row
    #                          is a no-op-by-mistake from the hand-coded
    #                          Vengeance export.
    #
    # We skip them WITHOUT a warning because a "warning" implies the user
    # should act on something. Here the right action is "do nothing" — the
    # importer is making the canonical choice automatically.
    INTENTIONAL_SKIPS = frozenset({
        "AIMAvailability.xml",
        "MercAvailability.xml",
        "Vehicles.xml",
        # FaceGear.xml's <uiIndex> is an inventory ITEM id (e.g. SunGoggles
        # == 212), NOT a merc profile slot. Upserting it as a slot row is a
        # no-op at best; when target_slot collides with a real gear item id
        # (211/212/213/246/250) the appended last-wins entry clobbers that
        # item's overlay for ALL mercs in the target install. It was also
        # being rewritten with whole-file ElementTree reflow, which the
        # engine's dual-entry FaceGear parser can choke on at boot. Per-merc
        # face gear is carried correctly by the STI overlay path
        # (facegear/<stem>.png). New bundles no longer export this row; this
        # skip also neutralizes legacy bundles that still carry it. See the
        # project_mercwizard2_facegear_tablerow analysis.
        "FaceGear.xml",
        # CivGroupNames.xml's <uiIndex> is a CIV-GROUP id — a direct index into
        # the engine's zCivGroupName[NUM_CIV_GROUPS=255] array
        # (XML_CivGroupNames.cpp), NOT a merc profile slot. The generic ET path
        # below would rewrite the row's uiIndex to target_slot then reflow the
        # whole file: that clobbers an unrelated civ group's name/loyalty/side
        # (target_slot 0..254) or, for an expanded-roster slot >= 255, writes
        # out of bounds past zCivGroupName[255] at engine boot. A merc's
        # ubCivilianGroup rides in the imported profile; the catalog is shared
        # engine-level faction data. New bundles no longer export this row; this
        # skip neutralizes legacy bundles that still carry it.
        "CivGroupNames.xml",
    })

    for arcname, text in contents.files.items():
        if not arcname.startswith("table_rows/"):
            continue
        filename = arcname[len("table_rows/"):]
        if filename in INTENTIONAL_SKIPS:
            # Surface in the partial-failures list with a clear "by
            # design" note so the import report makes the skip visible
            # to the user (TODO #12). AIM/MERC availability bindings
            # are remapped via `manifest.aim_binding` / `merc_binding`
            # (not bundled XML); Vehicles.xml is unrelated to merc data.
            # Bundling the verbatim row would clobber the importer's
            # MercBioID remap — see CLAUDE.md "INTENTIONAL_SKIPS".
            report.partial_failures.append(
                f"table_rows/{filename}: intentionally skipped (binding "
                "carried by manifest, not the XML row)"
            )
            continue
        key, id_tag = filename_to_key.get(filename, (None, None))
        if key is None:
            report.partial_failures.append(
                f"table_rows/{filename}: unrecognized table name, skipped"
            )
            continue

        target_path = target_ctx.extra_table_path(key, for_write=True)

        if target_path is None or not target_path.is_file():
            report.partial_failures.append(
                f"table_rows/{filename}: target install has no {filename} — skipped"
            )
            continue

        # Decode the bundled row fragment once.
        try:
            row_text = text.decode("utf-8") if isinstance(text, bytes) else text
        except UnicodeDecodeError as e:
            report.partial_failures.append(
                f"table_rows/{filename}: decode failure: {e}"
            )
            continue

        # ── FaceGear.xml: NEVER round-trip through ElementTree. The engine's
        # dual-entry "last wins" architecture is silently corrupted by an ET
        # reflow (documented KGoggles boot-CTD — MercWizard2/CLAUDE.md
        # "FaceGear is overlay, not portrait paint"; wasteland-facegear
        # skill). The documented-safe mutation is to append a fresh <ITEM>
        # block before the root close via byte-level string insertion +
        # atomic write; every existing (stock + custom) entry stays
        # byte-for-byte intact and the appended entry overrides any stock
        # entry with the same uiIndex at runtime.
        if key == "face_gear":
            _upsert_facegear_row_text(
                target_path, row_text, id_tag, target_slot, filename, report
            )
            continue

        # ── Backgrounds.xml: a merc's background is a SHARED-CATALOG entry keyed
        # by its own uiIndex (= the merc's usBackground), NOT a per-merc slot row.
        # Recreate it in the target ONLY if missing — by its own id, spliced
        # before the physical-last entry so num_found_background (the IMP picker
        # bound) is unchanged, preserved verbatim (nested drug lists / mod columns
        # intact), and NEVER clobbering an existing entry. The merc's usBackground
        # already rode in via the imported profile; this just ensures the
        # definition exists in a target that lacks it. Legacy slot-keyed rows flow
        # through the same safe create-if-missing path. See
        # reference_ja2_backgrounds_engine + the carry-it-correctly design.
        if key == "backgrounds":
            from ..inject import backgrounds_xml
            try:
                result = backgrounds_xml.upsert_background_block(
                    target_path, block_text=row_text
                )
                if result.get("created"):
                    report.files_written.append(str(target_path))
            except backgrounds_xml.BackgroundError as e:
                report.partial_failures.append(
                    f"table_rows/{filename}: {e.code}: {e.message}"
                )
            except (OSError, UnicodeDecodeError) as e:
                # A non-UTF-8 / unreadable target Backgrounds.xml must degrade to
                # a partial failure — exactly like the generic ET path's
                # ParseError — and NEVER abort + roll back the whole merc import.
                # (JA2 mod tables routinely carry Windows-1252 accented bytes.)
                report.partial_failures.append(
                    f"table_rows/{filename}: write failed: {type(e).__name__}: {e}"
                )
            continue

        # ── Other extra tables (MercOpinions/MercQuote) are genuinely keyed by
        # the merc's profile slot. Re-key the row to target_slot and splice it
        # into the target table at the BYTE level (lossless latin-1 round-trip,
        # atomic write) — NEVER an ET whole-file reflow. The old reflow rewrote
        # the entire file as UTF-8, which silently mojibaked every OTHER merc's
        # row in a target whose <?xml?> declared Windows-1252 (accented nicknames
        # are common in localized mods; the engine reads them with encoding-aware
        # expat — XML_Opinions.cpp / XML_Qarray.cpp). Byte-splice touches only
        # this slot's row, so the declaration, CRLF, BOM, and every sibling row's
        # bytes are preserved verbatim. Mirrors the Backgrounds/FaceGear
        # discipline; both tables store by <uiIndex> value so row order is
        # irrelevant. See inject/slot_table_xml.
        from ..inject import slot_table_xml
        try:
            slot_table_xml.upsert_slot_row(
                target_path, row_text=row_text, id_tag=id_tag, target_slot=target_slot
            )
            report.files_written.append(str(target_path))
        except slot_table_xml.SlotTableError as e:
            report.partial_failures.append(
                f"table_rows/{filename}: {e.code}: {e.message}"
            )
        except (OSError, UnicodeError) as e:
            # An unreadable/unwritable target must degrade to a partial failure —
            # exactly like the old ET.ParseError path — and NEVER abort + roll
            # back the whole merc import.
            report.partial_failures.append(
                f"table_rows/{filename} upsert into {target_path}: {type(e).__name__}: {e}"
            )


def _reindent_facegear_block(row_text: str, eol: str) -> str:
    r"""Normalize a bundled FaceGear ``<ITEM>`` fragment for in-place appending.

    The fragment was serialized by the exporter via ``ET.tostring`` after
    ``ET.indent(space="\t")`` — LF line endings, ``<ITEM>`` at column 0, its
    child tags at one tab. To nest it as a child of the root element we add
    one leading tab to every non-blank line and convert the line endings to
    the target file's style (``eol``). Returns the block with NO trailing
    newline so the caller controls the separator before the root close tag.
    """
    lines = row_text.replace("\r\n", "\n").replace("\r", "\n").strip("\n").split("\n")
    return eol.join(("\t" + ln) if ln.strip() else ln for ln in lines)


def _upsert_facegear_row_text(
    target_path: Path,
    row_text: str,
    id_tag: str,
    target_slot: int,
    filename: str,
    report: ImportReport,
) -> None:
    r"""Append a FaceGear.xml ``<ITEM>`` row with byte-level string insertion.

    FaceGear.xml MUST NOT be rewritten with ElementTree — the engine's
    dual-entry "last wins" architecture is silently corrupted by an ET reflow
    (documented KGoggles boot-CTD; MercWizard2/CLAUDE.md "FaceGear is overlay,
    not portrait paint"; wasteland-facegear skill). The documented-safe
    mutation is to append a fresh ``<ITEM>`` block before the root closing
    tag — the appended entry overrides any stock entry with the same uiIndex
    at runtime, and every existing (stock + custom) entry is left
    byte-for-byte intact.

    The write is atomic (tempfile + ``os.replace`` via ``write_bytes_atomic``)
    per the same discipline the four core XML writers follow.

    Idempotent: a re-import of the exact same block (same slot + same row
    content) is a no-op rather than appending a duplicate.
    """
    import re

    # Rewrite the row's own id tag (uiIndex) to the target slot at the string
    # level — we never hand FaceGear.xml content to ET's serializer.
    rewritten = re.sub(
        rf"(<{re.escape(id_tag)}>)\s*[^<]*(</{re.escape(id_tag)}>)",
        lambda m: f"{m.group(1)}{target_slot}{m.group(2)}",
        row_text,
        count=1,
    )

    try:
        raw = target_path.read_bytes()
    except OSError as e:
        report.partial_failures.append(
            f"table_rows/{filename}: read failed for {target_path.name}: "
            f"{type(e).__name__}: {e}"
        )
        return

    eol = "\r\n" if b"\r\n" in raw else "\n"
    block_bytes = _reindent_facegear_block(rewritten, eol).encode("utf-8")

    # Idempotent re-import: this exact block is already present → no-op.
    if block_bytes in raw:
        report.partial_failures.append(
            f"table_rows/{filename}: entry for slot {target_slot} already "
            "present (idempotent re-import) — left unchanged"
        )
        return

    # The root closing tag is the LAST "</...>" in a well-formed document.
    # Root-name-agnostic: real 1.13 ships <FACE_GEAR>; some installs/fixtures
    # use <FACEGEAR_LIST>. Both close with the final "</".
    close_pos = raw.rfind(b"</")
    if close_pos == -1:
        report.partial_failures.append(
            f"table_rows/{filename}: no closing root tag in {target_path.name} "
            "— cannot safely append, skipped"
        )
        return

    eol_bytes = eol.encode("utf-8")
    # Guarantee the appended block starts on its own line even if the root
    # close tag isn't already newline-separated (defends against a minified
    # "...</ITEM></FACE_GEAR>" target).
    needs_leading_eol = close_pos == 0 or raw[close_pos - 1:close_pos] != b"\n"
    insertion = (eol_bytes if needs_leading_eol else b"") + block_bytes + eol_bytes
    new_raw = raw[:close_pos] + insertion + raw[close_pos:]

    try:
        write_bytes_atomic(target_path, new_raw)
    except OSError as e:
        report.partial_failures.append(
            f"table_rows/{filename} append into {target_path}: "
            f"{type(e).__name__}: {e}"
        )
        return
    report.files_written.append(str(target_path))
