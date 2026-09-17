"""Crash-recoverable Voice Lab deployment primitives.

Only loose files are ever mutable.  The inventory may read an SLF member, but
the target planner always materializes its reviewed result in the writable
content layer.
"""
from __future__ import annotations

import base64
from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path, PureWindowsPath
from typing import Any, Callable, Iterable
from uuid import uuid4

from mercwizard_core import backup
from mercwizard_core.inject._atomic_xml import write_bytes_atomic
from mercwizard_core.install_context import make_install_context

from .dialogue_edt import DialogueDocument
from .inventory import InventorySnapshot, read_asset_bytes, scan_inventory, scan_profile_catalog
from .journal import DeploymentJournal, TargetState, journals_dir, pending_journals_for_install
from .models import DeployPlan, DeploymentResult, EditRecipe, ImportedVoiceAsset, TargetAction, UndoResult
from .recipes import RecipeRepository
from .store import VoiceLabStore
from .triggers import TriggerCatalog


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _hash(data: bytes) -> str:
    return sha256(data).hexdigest()


def _canonical_hash(value: Any) -> str:
    return _hash(json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8"))


def _safe_relative(value: str) -> Path:
    """Accept one Windows-safe install-relative target path."""
    path = PureWindowsPath(value)
    if not value or path.is_absolute() or path.drive or path.root or any(
        part in {"", ".", ".."} or ":" in part for part in path.parts
    ):
        raise ValueError("unsafe Voice Lab target path")
    return Path(*path.parts)


def _relative(root: Path, path: Path) -> str:
    try:
        return str(path.resolve().relative_to(root.resolve())).replace("\\", "/")
    except ValueError as exc:
        raise ValueError("Voice Lab target escapes the selected install") from exc


class VoiceLabDeployer:
    """Build, apply, recover, and undo complete reviewed Voice Lab plans."""

    def __init__(
        self,
        *,
        install_id: str,
        install_root: Path,
        store: VoiceLabStore,
        recipes: RecipeRepository,
        catalog: TriggerCatalog,
        audio_toolchain: Any,
        manifests_root: Path,
        backup_base: Path | None = None,
        crash_after_target: Callable[[int], None] | None = None,
        transaction_hook: Callable[[str], None] | None = None,
    ) -> None:
        self.install_id = install_id
        self.install_root = Path(install_root).resolve()
        self.store = store
        self.recipes = recipes
        self.catalog = catalog
        self.audio_toolchain = audio_toolchain
        self.manifests_root = Path(manifests_root)
        self.backup_base = backup_base
        self.crash_after_target = crash_after_target
        self.transaction_hook = transaction_hook

    def _boundary(self, name: str) -> None:
        """Testable durable-transaction boundary; production leaves it inert."""
        if self.transaction_hook is not None:
            self.transaction_hook(name)

    def scan(
        self,
        *,
        progress: Callable[[int, int, str], None] | None = None,
        cancelled: Callable[[], bool] | None = None,
        voice_indexes: set[int] | None = None,
    ) -> InventorySnapshot | None:
        snapshot = scan_inventory(
            self.install_id, self.install_root, self.catalog, self.store,
            progress=progress, cancelled=cancelled, voice_indexes=voice_indexes,
        )
        if snapshot is None:
            return None
        if voice_indexes is None:
            self.store.replace_inventory(snapshot)
        else:
            self.store.replace_banks(snapshot)
        return snapshot

    def profile_catalog(self) -> InventorySnapshot:
        """Return merc names and voice ownership without scanning recordings."""
        return scan_profile_catalog(self.install_id, self.install_root, self.catalog)

    def preflight(self, recipe_id: str, *, plan_id: str | None = None) -> DeployPlan:
        recipe = self.recipes.load(recipe_id)
        return self._build_plan(recipe, plan_id=plan_id or uuid4().hex)

    def _build_plan(self, recipe: EditRecipe, *, plan_id: str) -> DeployPlan:
        if recipe.install_id != self.install_id:
            raise ValueError("recipe belongs to a different install")
        snapshot = self.scan(voice_indexes={recipe.voice_index})
        if snapshot is None:
            raise RuntimeError("Voice Lab inventory scan was cancelled")
        line = snapshot.line(recipe.family, recipe.voice_index, recipe.line_id)
        try:
            source = self.store.asset(recipe.input_asset_id)
        except KeyError:
            source = snapshot.asset(recipe.input_asset_id)
        if isinstance(source, ImportedVoiceAsset) and source.source_kind != "import":
            raise ValueError("preview-cache assets cannot be recipe inputs")
        if source.sha256 != recipe.input_sha256:
            raise ValueError("STALE_PLAN: reviewed source bytes changed")
        if line.audio_winner is None:
            raise ValueError("STALE_PLAN: destination no longer has an active winner")
        target_asset_id = source.asset_id
        target_sha256 = source.sha256
        if isinstance(source, ImportedVoiceAsset):
            binding = recipe.replacement_source or {}
            target_asset_id = binding.get("target_asset_id")
            target_sha256 = binding.get("target_sha256")
            if not isinstance(target_asset_id, str) or not isinstance(target_sha256, str):
                raise ValueError("STALE_PLAN: imported recipe has no original target binding")
        if line.audio_winner.asset_id != target_asset_id or line.audio_winner.sha256 != target_sha256:
            raise ValueError("STALE_PLAN: destination live winner changed")
        if not recipe.preview_asset_path or not recipe.preview_sha256:
            raise ValueError("recipe has no complete reviewed preview")
        preview = Path(recipe.preview_asset_path)
        preview_bytes = preview.read_bytes()
        if _hash(preview_bytes) != recipe.preview_sha256:
            raise ValueError("STALE_PLAN: preview bytes do not match recipe evidence")
        if recipe.preview_source_sha256 != source.sha256:
            raise ValueError("STALE_PLAN: preview source binding changed")

        stage = self._stage_dir(plan_id)
        audio_stage = stage / "audio.ogg"
        write_bytes_atomic(audio_stage, preview_bytes)
        gap = self.audio_toolchain.generate_gap(audio_stage)
        gap_stage = stage / "audio.gap"
        write_bytes_atomic(gap_stage, gap.gap_bytes)

        context = make_install_context(self.install_root)
        output_target = self._audio_target(context, line.audio_winner, recipe)
        targets: list[TargetAction] = [
            self._write_action("audio", output_target, audio_stage, "replace"),
            self._write_action("gap", output_target.with_suffix(".gap"), gap_stage, "replace"),
        ]
        # An existing .mp3 would still beat our deliberately canonical .ogg.
        for variant in line.audio_variants:
            if variant.extension != ".mp3":
                continue
            if variant.source_kind != "loose":
                raise ValueError(
                    "ACTIVE_ARCHIVED_MP3_SHADOW: active archived MP3 would continue "
                    "to override the deployed OGG"
                )
            path = Path(variant.source_locator)
            if path.exists() and path.resolve() != output_target.resolve():
                targets.append(self._remove_action(f"shadow-{variant.asset_id[:12]}", path))

        before_subtitle: str | None = None
        if recipe.subtitle is not None:
            edt_target, document = self._dialogue_target(context, snapshot, recipe)
            before_record = document.record_bytes(int(recipe.line_id))
            before_subtitle = document.text(int(recipe.line_id))
            after = document.replace_text(int(recipe.line_id), recipe.subtitle)
            edt_stage = stage / "dialogue.EDT"
            write_bytes_atomic(edt_stage, after)
            targets.append(
                self._write_action(
                    "dialogue", edt_target, edt_stage, "replace",
                    metadata={
                        "edt_slot": int(recipe.line_id),
                        "preimage_record_b64": base64.b64encode(before_record).decode("ascii"),
                        "postimage_record_sha256": _hash(DialogueDocument.from_bytes(after).record_bytes(int(recipe.line_id))),
                        "before_subtitle": before_subtitle,
                    },
                )
            )
        evidence = self._snapshot_evidence(snapshot, recipe, targets)
        return DeployPlan(
            plan_id=plan_id, recipe_id=recipe.recipe_id, created_utc=_utc_now(),
            evidence_hash=_canonical_hash(evidence), affected_profiles=[p.profile_id for p in snapshot.bank(recipe.voice_index).profiles],
            targets=targets, snapshot=evidence,
        )

    def _stage_dir(self, plan_id: str) -> Path:
        root = self.manifests_root.parent / "staging" / plan_id
        root.mkdir(parents=True, exist_ok=True)
        return root

    def _audio_target(self, context: Any, source: Any, recipe: EditRecipe) -> Path:
        root = context.speech_root(for_write=True) if recipe.family == "speech" else context.battlesnds_root(for_write=True)
        if source.source_kind == "loose":
            candidate = Path(source.source_locator).with_suffix(".ogg")
            try:
                candidate.resolve().relative_to(root.resolve())
                return candidate
            except ValueError:
                pass
        if source.source_kind == "slf":
            try:
                locator = json.loads(source.source_locator)
                parts = Path(str(locator["member"]).replace("\\", "/")).parts
                lower = [part.lower() for part in parts]
                marker = "speech" if recipe.family == "speech" else "battlesnds"
                relative = Path(*parts[lower.index(marker) + 1:]) if marker in lower else Path(parts[-1])
                return (root / relative).with_suffix(".ogg")
            except (KeyError, TypeError, ValueError):
                pass
        return root / f"{recipe.voice_index}_{recipe.line_id}.ogg"

    def _dialogue_target(self, context: Any, snapshot: InventorySnapshot, recipe: EditRecipe) -> tuple[Path, DialogueDocument]:
        try:
            source = snapshot.line("dialogue_edt", recipe.voice_index, "document").dialogue_edt
        except KeyError:
            source = None
        if source is None:
            raise ValueError("cannot deploy a subtitle without a 70080-byte MercEdt source")
        document = DialogueDocument.from_bytes(read_asset_bytes(source))
        if source.source_kind == "loose":
            candidate = Path(source.source_locator)
            try:
                candidate.resolve().relative_to(self.install_root)
                return candidate, document
            except ValueError:
                pass
        return context.layout.mod_content_path(f"MercEdt/{recipe.voice_index}.EDT"), document

    def _write_action(self, target_id: str, target: Path, stage: Path, kind: str, metadata: dict[str, Any] | None = None) -> TargetAction:
        existed = target.is_file()
        preimage = _hash(target.read_bytes()) if existed else None
        actual_kind = "replace" if existed else "create"
        return TargetAction(target_id=target_id, relative_path=_relative(self.install_root, target), kind=actual_kind,
            staged_path=str(stage), staged_sha256=_hash(stage.read_bytes()), preimage_sha256=preimage,
            existed_before=existed, metadata=metadata or {})

    def _remove_action(self, target_id: str, target: Path) -> TargetAction:
        return TargetAction(target_id=target_id, relative_path=_relative(self.install_root, target), kind="remove_shadowing_variant",
            staged_path=None, staged_sha256=None, preimage_sha256=_hash(target.read_bytes()), existed_before=True)

    def _snapshot_evidence(self, snapshot: InventorySnapshot, recipe: EditRecipe, targets: Iterable[TargetAction]) -> dict[str, Any]:
        bank = snapshot.bank(recipe.voice_index)
        return {
            "recipe_id": recipe.recipe_id, "recipe_hash": recipe.serialized_recipe_hash(),
            "profiles": [profile.model_dump(mode="json") for profile in bank.profiles],
            "source": {"asset_id": recipe.input_asset_id, "sha256": recipe.input_sha256},
            "winners": [
                {"family": line.family, "line": line.line_id,
                 "audio": line.audio_winner.sha256 if line.audio_winner else None,
                 "dialogue": line.dialogue_edt.sha256 if line.dialogue_edt else None}
                for line in bank.lines
            ],
            "targets": [action.model_dump(mode="json", exclude={"staged_path"}) for action in targets],
        }

    def deploy(self, plan: DeployPlan) -> DeploymentResult:
        recipe = self.recipes.load(plan.recipe_id)
        locked = self._build_plan(recipe, plan_id=plan.plan_id)
        if locked.evidence_hash != plan.evidence_hash or locked.affected_profiles != plan.affected_profiles:
            raise ValueError("STALE_PLAN: live inputs changed after preflight")
        # Target actions must also be stable; a changed extension winner or a
        # newly-created loose override is a stale plan, never a surprise write.
        if [a.model_dump(exclude={"staged_path"}) for a in locked.targets] != [a.model_dump(exclude={"staged_path"}) for a in plan.targets]:
            raise ValueError("STALE_PLAN: live target set changed after preflight")
        backup_entry = backup.snapshot(self.install_root, self.install_id, [self.install_root / _safe_relative(a.relative_path) for a in plan.targets],
            reason="voice_lab_deploy", base=self.backup_base, pinned=True)
        created = [Path(a.relative_path) for a in plan.targets if not a.existed_before]
        backup.record_files_created(backup_entry.id, self.install_id, created, base=self.backup_base)
        deployment_id = uuid4().hex
        manifest = self._manifest(deployment_id, recipe, plan, backup_entry.id)
        manifest_path = self._manifest_path(deployment_id)
        manifest_sha256 = _hash(self._manifest_bytes(manifest))
        journal = DeploymentJournal.prepare_for_install(self.install_id, {
            "journal_id": deployment_id, "operation": "deploy", "backup_id": backup_entry.id,
            "targets": [self._journal_target(action) for action in plan.targets],
            "metadata": {
                "recipe_id": recipe.recipe_id, "plan_id": plan.plan_id,
                "manifest_filename": manifest_path.name, "manifest_sha256": manifest_sha256,
            },
        }, base=self.backup_base)
        try:
            for index, action in enumerate(plan.targets, start=1):
                self._apply_action(action)
                journal.mark_target_applied(index - 1)
                if self.crash_after_target is not None:
                    self.crash_after_target(index)
            self._verify_deployed_audio_winner(recipe, plan.targets)
            # The durable manifest is written while the transaction remains
            # recoverable.  If its immutable authoring write fails, recovery
            # still restores the preimage instead of leaving a live change
            # that has no history record.
            manifest_path = self._write_manifest(deployment_id, manifest)
            deployed_recipe = recipe.model_copy(update={"deployment_status": "deployed", "deployment_id": deployment_id})
            self._boundary("deploy_before_index")
            self.store.commit_deployment_state(
                deployment_id=deployment_id, install_id=self.install_id, recipe=deployed_recipe,
                status="deployed", manifest=manifest, targets=[a.model_dump(mode="json") for a in plan.targets],
            )
            self._boundary("deploy_after_index")
            self._boundary("deploy_before_complete")
            journal.complete()
            self._boundary("deploy_after_complete")
        except BaseException:
            self._recover_journal(journal)
            raise
        return DeploymentResult(deployment_id=deployment_id, plan_id=plan.plan_id, recipe_id=recipe.recipe_id,
            backup_id=backup_entry.id, before_subtitle=self._before_subtitle(plan), manifest_path=str(manifest_path), targets=plan.targets)

    def _verify_deployed_audio_winner(
        self, recipe: EditRecipe, targets: Iterable[TargetAction],
    ) -> None:
        """Prove the just-written OGG is the active VFS winner before success."""
        audio = next((action for action in targets if action.target_id == "audio"), None)
        if audio is None or audio.staged_sha256 is None:
            raise RuntimeError("Voice Lab deployment has no staged audio target")
        snapshot = scan_inventory(self.install_id, self.install_root, self.catalog, self.store)
        if snapshot is None:
            raise RuntimeError("Voice Lab inventory scan was cancelled during deploy verification")
        line = snapshot.line(recipe.family, recipe.voice_index, recipe.line_id)
        winner = line.audio_winner
        if (
            winner is None
            or winner.extension != ".ogg"
            or winner.sha256 != audio.staged_sha256
        ):
            raise RuntimeError("Voice Lab deployed OGG did not become the active audio winner")

    def _journal_target(self, action: TargetAction) -> dict[str, Any]:
        return {"target_id": action.target_id, "target_path": action.relative_path, "kind": action.kind,
            "preimage_sha256": action.preimage_sha256, "staged_sha256": action.staged_sha256,
            "existed_before": action.existed_before, "data": action.metadata}

    def _apply_action(self, action: TargetAction) -> None:
        target = self.install_root / _safe_relative(action.relative_path)
        if action.kind == "remove_shadowing_variant":
            if target.is_file():
                target.unlink()
            if target.exists():
                raise RuntimeError("Voice Lab could not remove shadowing variant")
            return
        if not action.staged_path or not action.staged_sha256:
            raise ValueError("write action has no staged bytes")
        data = Path(action.staged_path).read_bytes()
        if _hash(data) != action.staged_sha256:
            raise ValueError("staged Voice Lab bytes changed")
        write_bytes_atomic(target, data)
        if _hash(target.read_bytes()) != action.staged_sha256:
            raise RuntimeError("Voice Lab verify-after failed")

    def _manifest(self, deployment_id: str, recipe: EditRecipe, plan: DeployPlan, backup_id: str) -> dict[str, Any]:
        return {"schema_version": 1, "deployment_id": deployment_id, "install_id": self.install_id,
            "recipe_id": recipe.recipe_id, "recipe_hash": recipe.serialized_recipe_hash(), "backup_id": backup_id,
            "created_utc": _utc_now(), "plan_evidence_hash": plan.evidence_hash,
            "affected_profiles": plan.affected_profiles, "targets": [a.model_dump(mode="json") for a in plan.targets]}

    def _write_manifest(self, deployment_id: str, manifest: dict[str, Any]) -> Path:
        self.manifests_root.mkdir(parents=True, exist_ok=True)
        path = self._manifest_path(deployment_id)
        if path.exists():
            raise FileExistsError("immutable Voice Lab manifest already exists")
        write_bytes_atomic(path, self._manifest_bytes(manifest))
        return path

    def _manifest_path(self, deployment_id: str) -> Path:
        return self.manifests_root / f"{deployment_id}.v0001.json"

    @staticmethod
    def _manifest_bytes(manifest: dict[str, Any]) -> bytes:
        return json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode("utf-8") + b"\n"

    @staticmethod
    def _before_subtitle(plan: DeployPlan) -> str | None:
        for action in plan.targets:
            if "before_subtitle" in action.metadata:
                return action.metadata["before_subtitle"]
        return None

    def recover_pending(self) -> None:
        for journal in pending_journals_for_install(self.install_id, base=self.backup_base):
            self._recover_journal(journal)
        # A kill after terminalization but before unpinning is not pending.
        # Revisit terminal recovery and Undo journals idempotently to finish
        # retention housekeeping without weakening the original deploy backup.
        directory = journals_dir(self.install_id, base=self.backup_base)
        if directory.is_dir():
            for path in sorted(directory.glob("*.json")):
                journal = DeploymentJournal.load(path)
                if journal.state.value == "recovered":
                    self._finalize_recovered_journal(journal)
                elif journal.operation == "undo" and journal.state.value == "complete":
                    self._finalize_terminal_undo(journal)

    def _recover_journal(self, journal: DeploymentJournal) -> None:
        if journal.is_terminal:
            if journal.state.value == "recovered":
                self._finalize_recovered_journal(journal)
            return
        active = journal.begin_recovery()
        backup.restore(active.backup_id, self.install_id, self.install_root, base=self.backup_base)
        self._verify_recovery_preimage(active)
        for index, target in enumerate(active.targets):
            if target.state is not TargetState.recovered:
                active.mark_target_recovered(index)
        metadata = active.metadata
        recovery_name = f"recover_{active.operation}"
        self._boundary(f"{recovery_name}_before_reconcile")
        if active.operation == "deploy":
            manifest = self._validated_recovery_manifest(active)
            self.store.reconcile_interrupted_deployment(
                deployment_id=active.journal_id, recipe_id=str(metadata.get("recipe_id", "")),
                status="recovered", deployment_status="not_deployed", deployment_id_for_recipe=None,
                install_id=self.install_id, backup_id=active.backup_id,
                manifest=manifest, targets=manifest["targets"] if manifest else (),
            )
        elif active.operation == "undo":
            self.store.reconcile_interrupted_deployment(
                deployment_id=str(metadata.get("deployment_id", "")), recipe_id=str(metadata.get("recipe_id", "")),
                status="deployed", deployment_status="deployed", deployment_id_for_recipe=str(metadata.get("deployment_id", "")),
                install_id=self.install_id, backup_id=active.backup_id,
            )
        self._boundary(f"{recovery_name}_after_reconcile")
        self._boundary(f"{recovery_name}_before_complete_recovery")
        active.complete_recovery()
        self._boundary(f"{recovery_name}_after_complete_recovery")
        self._finalize_recovered_journal(active)

    def _verify_recovery_preimage(self, journal: DeploymentJournal) -> None:
        """Refuse recovery terminalization unless every restored target is exact."""
        entry = next(
            (item for item in backup.list_backups(self.install_id, base=self.backup_base) if item.id == journal.backup_id),
            None,
        )
        if entry is None:
            raise FileNotFoundError("Voice Lab recovery backup is missing")
        captured = {path.replace("\\", "/") for path in entry.files}
        created = {path.replace("\\", "/") for path in entry.files_created}
        for target in journal.targets:
            path = self.install_root / _safe_relative(target.target_path)
            relative = target.target_path.replace("\\", "/")
            if relative in captured:
                source = entry.root_dir / "snapshot" / _safe_relative(target.target_path)
                if not path.is_file() or not source.is_file() or _hash(path.read_bytes()) != _hash(source.read_bytes()):
                    raise RuntimeError("Voice Lab recovery verify-after failed")
            elif relative in created:
                if path.exists():
                    raise RuntimeError("Voice Lab recovery left a created target behind")
            else:
                raise RuntimeError("Voice Lab recovery backup lacks a journal target")

    def _validated_recovery_manifest(self, journal: DeploymentJournal) -> dict[str, Any] | None:
        """Return the exact immutable manifest only when journal evidence matches it."""
        metadata = journal.metadata
        filename = metadata.get("manifest_filename")
        expected_sha = metadata.get("manifest_sha256")
        if not isinstance(filename, str) or not isinstance(expected_sha, str):
            return None
        expected_path = self._manifest_path(journal.journal_id)
        if filename != expected_path.name or not expected_path.is_file():
            return None
        payload = expected_path.read_bytes()
        if _hash(payload) != expected_sha:
            raise ValueError("Voice Lab recovery manifest hash mismatch")
        try:
            manifest = json.loads(payload)
        except json.JSONDecodeError as exc:
            raise ValueError("Voice Lab recovery manifest is unreadable") from exc
        if not isinstance(manifest, dict):
            raise ValueError("Voice Lab recovery manifest is invalid")
        if (
            manifest.get("deployment_id") != journal.journal_id
            or manifest.get("install_id") != self.install_id
            or manifest.get("recipe_id") != metadata.get("recipe_id")
            or manifest.get("backup_id") != journal.backup_id
            or not isinstance(manifest.get("targets"), list)
        ):
            raise ValueError("Voice Lab recovery manifest does not match journal")
        manifest_targets = [TargetAction.model_validate(item) for item in manifest["targets"]]
        if len(manifest_targets) != len(journal.targets):
            raise ValueError("Voice Lab recovery manifest target count changed")
        for action, target in zip(manifest_targets, journal.targets):
            expected = self._journal_target(action)
            # Journal validation normalizes every target path to Windows
            # separators; the immutable manifest retains the plan spelling.
            expected["target_path"] = target.target_path
            observed = {key: value for key, value in target.data.items() if key != "state"}
            if expected != observed:
                raise ValueError("Voice Lab recovery manifest target order changed")
        return manifest

    def _finalize_recovered_journal(self, journal: DeploymentJournal) -> None:
        """Release only the rollback snapshot after a durable recovered terminal state."""
        if journal.state.value != "recovered":
            return
        backup.set_backup_pinned(journal.backup_id, self.install_id, False, base=self.backup_base)

    def undo(self, deployment_id: str) -> UndoResult:
        deployment = self.store.deployment(deployment_id)
        if deployment["install_id"] != self.install_id:
            raise ValueError("deployment belongs to a different install")
        actions = [TargetAction.model_validate(raw) for raw in deployment["targets"]]
        conflicts = [action.target_id for action in actions if not self._postimage_matches(action)]
        if conflicts:
            return UndoResult(deployment_id=deployment_id, status="UNDO_CONFLICT", message=", ".join(conflicts))
        original_backup = str(deployment["manifest"]["backup_id"])
        undo_backup = backup.snapshot(self.install_root, self.install_id, [self.install_root / _safe_relative(a.relative_path) for a in actions],
            reason="voice_lab_undo", base=self.backup_base, pinned=True)
        # An Undo of a prior removal recreates the shadowing variant.  Record
        # that as a created file in the Undo snapshot so a killed Undo can be
        # recovered back to the deployed (absent) postimage.
        backup.record_files_created(
            undo_backup.id,
            self.install_id,
            [Path(action.relative_path) for action in actions if action.kind == "remove_shadowing_variant"],
            base=self.backup_base,
        )
        journal = DeploymentJournal.prepare_for_install(self.install_id, {
            "journal_id": uuid4().hex, "operation": "undo", "backup_id": undo_backup.id,
            "targets": [self._journal_target(action) for action in actions],
            "metadata": {"deployment_id": deployment_id, "original_backup_id": original_backup, "recipe_id": deployment["recipe_id"]},
        }, base=self.backup_base)
        try:
            for index, action in enumerate(actions):
                self._undo_action(action, original_backup)
                self._verify_undo_action(action)
                journal.mark_target_applied(index)
            undone_recipe = self.store.recipe(deployment["recipe_id"]).model_copy(
                update={"deployment_status": "undone", "deployment_id": deployment_id}
            )
            manifest = dict(deployment["manifest"])
            manifest["undo_completed_utc"] = _utc_now()
            self._boundary("undo_before_index")
            self.store.commit_deployment_state(
                deployment_id=deployment_id, install_id=self.install_id, recipe=undone_recipe,
                status="undone", manifest=manifest, targets=[a.model_dump(mode="json") for a in actions],
            )
            self._boundary("undo_after_index")
            self._boundary("undo_before_complete")
            journal.complete()
            self._boundary("undo_after_complete")
        except BaseException:
            self._recover_journal(journal)
            raise
        self._finalize_terminal_undo(journal)
        return UndoResult(deployment_id=deployment_id, status="undone")

    def _finalize_terminal_undo(self, journal: DeploymentJournal) -> None:
        """Unpin only after a durable terminal Undo, safely repeatable on startup."""
        if journal.operation != "undo" or journal.state.value != "complete":
            return
        metadata = journal.metadata
        original = metadata.get("original_backup_id")
        if isinstance(original, str) and original:
            backup.set_backup_pinned(original, self.install_id, False, base=self.backup_base)
        backup.set_backup_pinned(journal.backup_id, self.install_id, False, base=self.backup_base)

    def _postimage_matches(self, action: TargetAction) -> bool:
        target = self.install_root / _safe_relative(action.relative_path)
        if action.kind == "remove_shadowing_variant":
            return not target.exists()
        if not target.is_file() or not action.staged_sha256:
            return False
        if "edt_slot" in action.metadata:
            try:
                record = DialogueDocument.from_bytes(target.read_bytes()).record_bytes(int(action.metadata["edt_slot"]))
            except ValueError:
                return False
            return _hash(record) == action.metadata.get("postimage_record_sha256")
        return _hash(target.read_bytes()) == action.staged_sha256

    def _undo_action(self, action: TargetAction, backup_id: str) -> None:
        target = self.install_root / _safe_relative(action.relative_path)
        if "edt_slot" in action.metadata:
            document = DialogueDocument.from_bytes(target.read_bytes())
            record = base64.b64decode(action.metadata["preimage_record_b64"])
            restored = document.replace_record_bytes(int(action.metadata["edt_slot"]), record)
            write_bytes_atomic(target, restored)
            return
        if not action.existed_before:
            if target.is_file():
                target.unlink()
            return
        entry = next((item for item in backup.list_backups(self.install_id, base=self.backup_base) if item.id == backup_id), None)
        if entry is None:
            raise FileNotFoundError("original Voice Lab backup is missing")
        source = entry.root_dir / "snapshot" / _safe_relative(action.relative_path)
        data = source.read_bytes()
        write_bytes_atomic(target, data)
        if _hash(target.read_bytes()) != action.preimage_sha256:
            raise RuntimeError("Voice Lab Undo verify-after failed")

    def _verify_undo_action(self, action: TargetAction) -> None:
        """Verify the exact Undo postcondition before advancing its journal."""
        target = self.install_root / _safe_relative(action.relative_path)
        if "edt_slot" in action.metadata:
            expected = base64.b64decode(action.metadata["preimage_record_b64"])
            actual = DialogueDocument.from_bytes(target.read_bytes()).record_bytes(int(action.metadata["edt_slot"]))
            if _hash(actual) != _hash(expected):
                raise RuntimeError("Voice Lab Undo EDT verify-after failed")
            return
        if not action.existed_before:
            if target.exists():
                raise RuntimeError("Voice Lab Undo failed to remove created target")
            return
        if not target.is_file() or _hash(target.read_bytes()) != action.preimage_sha256:
            raise RuntimeError("Voice Lab Undo file verify-after failed")
