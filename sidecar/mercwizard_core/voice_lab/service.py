"""Sidecar-facing orchestration for the Voice Lab domain package."""
from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
from typing import Any, Callable
from uuid import uuid4

from mercwizard_core.cross_lock import cross_process_install_root_lock
from mercwizard_core.ini_editor import game_running

from .audit import AudioMetrics, AuditBank, AuditLine, run_content_audit, run_deterministic_audit
from .audio import validate_gap
from .dialogue_edt import DialogueDocument
from .inventory import read_asset_bytes
from .deploy import VoiceLabDeployer
from .inventory import InventorySnapshot
from .models import DeploymentCapabilities, DeployPlan, DeploymentResult, EditRecipe, Finding, ImportedVoiceAsset, UndoResult, VoiceAsset
from .recipes import RecipeRepository
from .store import VoiceLabStore
from .triggers import TriggerCatalog, load_trigger_catalog


@dataclass(frozen=True)
class AuditReport:
    """Stable scan/audit result returned to the eventual API route."""

    snapshot: InventorySnapshot
    findings: list[Finding]

    def findings_for_bank(self, voice_index: int) -> list[Finding]:
        bank = self.snapshot.bank(voice_index)
        asset_ids = {asset.asset_id for line in bank.lines for asset in (*line.audio_variants, *line.gap_variants, *line.dialogue_variants)}
        return [finding for finding in self.findings if asset_ids.intersection(finding.asset_ids)]


class VoiceLabOperationError(RuntimeError):
    """A stable, user-actionable writer rejection for the HTTP boundary."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message


class _RecoveryOnlyAudioToolchain:
    """Marker toolchain for startup recovery, which performs no audio work."""

    def __getattr__(self, name: str) -> Any:
        raise RuntimeError("Voice Lab audio tools are unavailable during startup recovery")


class VoiceLabService:
    """Single-install Voice Lab service; writers use both required locks."""

    def __init__(self, deployer: VoiceLabDeployer, *, state: Any, store: VoiceLabStore) -> None:
        self._deployer = deployer
        self._state = state
        self.store = store
        self._plans: dict[str, DeployPlan] = {}

    @classmethod
    def for_install(
        cls,
        install_id: str,
        install_root: Path | str,
        store_path: Path | str | None = None,
        transcriber: Any | None = None,
        state: Any | None = None,
        *,
        workspace: Path | str | None = None,
        audio_toolchain: Any | None = None,
        catalog: TriggerCatalog | None = None,
        backup_base: Path | None = None,
        crash_after_target: Any | None = None,
        transaction_hook: Any | None = None,
    ) -> "VoiceLabService":
        """Construct against an explicit install; this never probes another install."""
        if state is None:
            from routes.state import get_state
            state = get_state()
        root = Path(install_root).resolve()
        store = VoiceLabStore(Path(store_path)) if store_path is not None else VoiceLabStore.open(install_id, base=backup_base)
        registered_roots = [root]
        if hasattr(state, "list_installs"):
            registered_roots.extend(info.path for info in state.list_installs())
        configured_workspace = workspace
        if configured_workspace is None and hasattr(state, "get_settings"):
            configured_workspace = state.get_settings().get("voice_authoring_workspace")
        recipes = RecipeRepository(workspace=configured_workspace, install_roots=registered_roots)
        manifests_root = (
            recipes.root.parent / "voice_lab" / "deployments"
            if configured_workspace else store.database_path.parent / "manifests"
        )
        if audio_toolchain is None:
            from .audio import AudioToolchain
            settings = state.get_settings() if hasattr(state, "get_settings") else {}
            audio_toolchain = AudioToolchain.resolve(settings)
        deployer = VoiceLabDeployer(install_id=install_id, install_root=root, store=store, recipes=recipes,
            catalog=catalog or load_trigger_catalog(), audio_toolchain=audio_toolchain, manifests_root=manifests_root,
            backup_base=backup_base, crash_after_target=crash_after_target, transaction_hook=transaction_hook)
        return cls(deployer, state=state, store=store)

    def close(self) -> None:
        self.store.close()

    def scan(
        self,
        *,
        progress: Callable[[int, int, str], None] | None = None,
        cancelled: Callable[[], bool] | None = None,
        voice_indexes: set[int] | None = None,
    ) -> InventorySnapshot | None:
        return self._deployer.scan(
            progress=progress, cancelled=cancelled, voice_indexes=voice_indexes,
        )

    def profile_catalog(self) -> InventorySnapshot:
        """Return the cheap MercProfiles-only picker catalog."""
        return self._deployer.profile_catalog()

    def scan_and_audit(
        self,
        *,
        progress: Callable[[int, int, str], None] | None = None,
        cancelled: Callable[[], bool] | None = None,
    ) -> AuditReport | None:
        """Audit a snapshot cooperatively without publishing a partial result."""
        # Preserve the established full-audit callback contract: its progress
        # begins with audio analysis.  The quick-inventory route passes scan
        # callbacks directly to ``scan()`` instead.
        snapshot = self.scan()
        if snapshot is None:
            return None
        findings = self.audit_snapshot(snapshot, progress=progress, cancelled=cancelled)
        if findings is None:
            return None
        return AuditReport(snapshot=snapshot, findings=findings)

    def audit_snapshot(
        self,
        snapshot: InventorySnapshot,
        voice_index: int | None = None,
        *,
        line_ids: list[str] | None = None,
        progress: Callable[[int, int, str], None] | None = None,
        cancelled: Callable[[], bool] | None = None,
    ) -> list[Finding] | None:
        """Audit one explicitly selected bank (and optional lines) from inventory.

        The initial inventory scan intentionally does no decode work.  This
        method is the bounded, user-requested decode path; it publishes only
        after all selected lines finish, so cancellation cannot leak partial
        findings into the review queue.  ``voice_index=None`` preserves the
        original full-service audit for callers that explicitly need it.
        """
        report_progress = progress or (lambda completed, total, message: None)
        is_cancelled = cancelled or (lambda: False)
        if is_cancelled():
            return None
        banks = snapshot.banks if voice_index is None else [snapshot.bank(voice_index)]
        ordered_line_ids = tuple(dict.fromkeys(line_ids)) if line_ids is not None else None

        def selected_lines(bank: Any) -> list[Any]:
            candidates = [line for line in bank.lines if line.family in {"speech", "battle"}]
            if ordered_line_ids is None:
                return candidates
            # The caller chooses a contextual sequence for content comparison;
            # retain that order instead of falling back to inventory ordering.
            return [
                line for line_id in ordered_line_ids for line in candidates
                if line.line_id == line_id
            ]

        findings: list[Finding] = []
        total = sum(len(selected_lines(bank)) for bank in banks)
        completed = 0
        for bank in banks:
            if is_cancelled():
                return None
            edt = next((line.dialogue_edt for line in bank.lines if line.family == "dialogue_edt"), None)
            document = None
            edt_valid = edt is not None
            if edt is not None:
                try:
                    document = DialogueDocument.from_bytes(read_asset_bytes(edt))
                except (OSError, ValueError):
                    edt_valid = False
            audit_lines: list[AuditLine] = []
            for line in selected_lines(bank):
                if is_cancelled():
                    return None
                metrics = None
                gap_valid = True
                gap_duration = None
                if line.audio_winner is not None and hasattr(self._deployer.audio_toolchain, "decoded_metrics"):
                    try:
                        values = self._deployer.audio_toolchain.decoded_metrics(
                            self._stage_readable_asset(line.audio_winner)
                        )
                        metrics = AudioMetrics(
                            duration_ms=values.duration_ms, pcm_sha256=values.pcm_sha256,
                            sample_rate=values.sample_rate, channels=values.channels,
                            leading_silence_ms=values.leading_silence_ms, trailing_silence_ms=values.trailing_silence_ms,
                            peak_dbfs=values.peak_dbfs, rms_dbfs=values.rms_dbfs,
                        )
                    except (OSError, RuntimeError, ValueError):
                        metrics = None
                if line.gap_winner is not None and metrics is not None:
                    gap_duration = metrics.duration_ms
                    try:
                        validate_gap(read_asset_bytes(line.gap_winner), metrics.duration_ms)
                    except (OSError, ValueError):
                        gap_valid = False
                subtitle = document.text(int(line.line_id)) if document is not None and line.family == "speech" and line.line_id.isdigit() else None
                transcript = (
                    self.store.transcription(line.audio_winner.asset_id, line.audio_winner.sha256)
                    if line.audio_winner is not None else None
                )
                hashes = {
                    name: asset.sha256 for name, asset in (("audio", line.audio_winner), ("edt", edt), ("gap", line.gap_winner))
                    if asset is not None
                }
                audit_lines.append(AuditLine(
                    voice_index=bank.voice_index, family=line.family, line_id=line.line_id, audio=line.audio_winner,
                    subtitle=subtitle, transcription=transcript, metrics=metrics, dialogue=edt, gap=line.gap_winner,
                    audio_variants=line.audio_variants, expected_components=("audio", "gap"),
                    expected_gap_duration_ms=gap_duration, gap_valid=gap_valid, reviewed_component_hashes=hashes,
                    logical_line_evidence_id=f"voice-line:{bank.voice_index}:{line.family}:{line.line_id}",
                ))
                completed += 1
                report_progress(
                    completed, total,
                    "auditing selected voice audio" if voice_index is not None else "auditing voice audio",
                )
                if is_cancelled():
                    return None
            audit_bank = AuditBank(voice_index=bank.voice_index, profiles=bank.profiles, lines=audit_lines, edt_valid=edt_valid, edt_asset=edt)
            findings.extend(run_deterministic_audit(audit_bank))
            findings.extend(
                run_content_audit(
                    audit_lines,
                    preserve_context_order=ordered_line_ids is not None,
                )
            )
        if is_cancelled():
            return None
        self.store.upsert_findings(findings)
        return findings

    def register_imported_asset(
        self,
        *,
        asset_id: str,
        extension: str,
        source_locator: Path,
        size_bytes: int,
        sha256: str,
        duration_ms: int,
    ) -> ImportedVoiceAsset:
        """Register a validated authoring upload through the durable store."""
        return self.store.register_imported_asset(
            install_id=self._deployer.install_id,
            asset_id=asset_id,
            extension=extension,
            source_locator=str(source_locator),
            size_bytes=size_bytes,
            sha256=sha256,
            duration_ms=duration_ms,
        )

    def create_recipe_from_draft(self, draft: dict[str, Any]) -> EditRecipe:
        source = self.store.asset(str(draft["input_asset_id"]))
        if isinstance(source, ImportedVoiceAsset) and source.source_kind == "preview": raise ValueError("preview-cache assets cannot be recipe inputs")
        if source.sha256 != draft["input_sha256"]: raise ValueError("recipe input hash does not match selected asset")
        replacement = draft.get("replacement_source")
        if replacement is not None:
            candidate = self.store.asset(str(replacement["asset_id"]))
            if isinstance(candidate, ImportedVoiceAsset) and candidate.source_kind == "preview": raise ValueError("preview-cache assets cannot be replacement sources")
            if candidate.sha256 != replacement["sha256"]: raise ValueError("replacement source hash does not match selected asset")
        if isinstance(source, ImportedVoiceAsset):
            if source.source_kind != "import":
                raise ValueError("preview-cache assets cannot be recipe inputs")
            try:
                line = self._deployer.scan(
                    voice_indexes={int(draft["voice_index"])},
                ).line(draft["family"], draft["voice_index"], draft["line_id"])
            except KeyError as exc:
                raise ValueError("replacement target has no active live winner") from exc
            if line.audio_winner is None:
                raise ValueError("replacement target has no active live winner")
            # The client selects render bytes, never the deployment target.
            # Bind the scanned winner here so preflight can reject a later
            # layer/path/hash change instead of silently redirecting output.
            replacement = {
                "asset_id": source.asset_id,
                "sha256": source.sha256,
                "target_asset_id": line.audio_winner.asset_id,
                "target_sha256": line.audio_winner.sha256,
            }
        duration_ms = self._deployer.audio_toolchain.probe(self._stage_readable_asset(source)).duration_ms
        recipe = EditRecipe(recipe_id=uuid4().hex, install_id=self._deployer.install_id, voice_index=draft["voice_index"], family=draft["family"], line_id=draft["line_id"], input_asset_id=source.asset_id, input_sha256=source.sha256, input_duration_ms=duration_ms, operations=draft.get("operations", []), replacement_source=replacement, subtitle=draft.get("subtitle"), output_extension=draft["output_extension"])
        self.save_recipe(recipe); return recipe

    def register_preview_asset(self, *, asset_id: str, recipe_id: str, source_locator: Path, size_bytes: int, sha256: str, duration_ms: int) -> ImportedVoiceAsset:
        return self.store.register_imported_asset(install_id=self._deployer.install_id, asset_id=asset_id, extension=".ogg", source_locator=str(source_locator), size_bytes=size_bytes, sha256=sha256, duration_ms=duration_ms, source_kind="preview", recipe_id=recipe_id)

    def line_detail(self, snapshot: InventorySnapshot, voice_index: int, family: str, line_id: str) -> dict[str, Any]:
        line = snapshot.line(family, voice_index, line_id)
        subtitle = None
        if family == "speech" and line_id.isdigit():
            edt = next((candidate.dialogue_edt for candidate in snapshot.bank(voice_index).lines if candidate.family == "dialogue_edt"), None)
            if edt is not None:
                try: subtitle = DialogueDocument.from_bytes(read_asset_bytes(edt)).text(int(line_id))
                except (OSError, ValueError): subtitle = None
        transcript = None if line.audio_winner is None else self.store.transcription(line.audio_winner.asset_id, line.audio_winner.sha256)
        return {"line": line, "subtitle": subtitle, "transcription": transcript}

    def read_asset_bytes(self, asset: VoiceAsset | ImportedVoiceAsset) -> bytes:
        """Resolve an opaque stored asset only on the server side."""
        if isinstance(asset, ImportedVoiceAsset):
            return Path(asset.source_locator).read_bytes()
        return read_asset_bytes(asset)

    def _stage_readable_asset(self, asset: VoiceAsset | ImportedVoiceAsset) -> Path:
        """Give ffmpeg a real file for either loose or immutable SLF inventory bytes."""
        if asset.source_kind in {"loose", "import"}:
            return Path(asset.source_locator)
        stage = self._deployer.manifests_root.parent / "audit" / f"{asset.asset_id}{asset.extension}"
        if not stage.is_file() or stage.read_bytes() != self.read_asset_bytes(asset):
            from mercwizard_core.inject._atomic_xml import write_bytes_atomic
            write_bytes_atomic(stage, self.read_asset_bytes(asset))
        return stage

    def save_recipe(self, recipe: EditRecipe) -> None:
        self._deployer.recipes.save(recipe)
        self.store.save_recipe(recipe)

    def list_recipes(self) -> list[EditRecipe]:
        """List indexed recipes without exposing store internals to callers."""
        return self.store.recipes()

    def preflight(self, recipe_id: str) -> DeployPlan:
        capabilities = self.deployment_status()
        self._require_writer_ready(capabilities, require_toolchain=True)
        plan = self._deployer.preflight(recipe_id)
        snapshot = dict(plan.snapshot)
        snapshot.update({
            # The backup is created only after preflight, under writer locks.
            # `null` avoids representing a future pin as an existing one.
            "backup_pinned": None,
            "backup_will_be_pinned": True,
            "capabilities": capabilities.model_dump(mode="json"),
        })
        plan = plan.model_copy(update={"snapshot": snapshot})
        self.handoff_preflight_plan(plan)
        return plan

    def handoff_preflight_plan(self, plan: DeployPlan) -> None:
        """Accept a reviewed preflight plan for a later deploy request."""
        self._plans[plan.plan_id] = plan

    def deploy(self, plan_id: str) -> DeploymentResult:
        plan = self._plans.get(plan_id)
        if plan is None:
            raise KeyError(f"unknown Voice Lab deploy plan {plan_id}")
        self._require_writer_ready(self.deployment_status(), require_toolchain=True)
        # Lock order is intentional and shared with every other live writer.
        with cross_process_install_root_lock(self._deployer.install_root):
            with self._state.write_lock:
                self._require_writer_ready(self.deployment_status(), require_toolchain=True)
                self._deployer.recover_pending()
                result = self._deployer.deploy(plan)
        self._plans.pop(plan_id, None)
        return result

    def recover_pending(self) -> None:
        if self._game_running():
            raise RuntimeError("GAME_RUNNING: close JA2 before recovering Voice Lab changes")
        with cross_process_install_root_lock(self._deployer.install_root):
            with self._state.write_lock:
                if self._game_running():
                    raise RuntimeError("GAME_RUNNING: close JA2 before recovering Voice Lab changes")
                self._deployer.recover_pending()

    def undo(self, deployment_id: str) -> UndoResult:
        self._require_writer_ready(self.deployment_status(), require_toolchain=False)
        with cross_process_install_root_lock(self._deployer.install_root):
            with self._state.write_lock:
                self._require_writer_ready(self.deployment_status(), require_toolchain=False)
                self._deployer.recover_pending()
                return self._deployer.undo(deployment_id)

    def pending_journals(self):
        from .journal import pending_journals_for_install
        return pending_journals_for_install(self._deployer.install_id, base=self._deployer.backup_base)

    def deployment_status(self) -> DeploymentCapabilities:
        """Return only writer-readiness facts needed by the public UI."""
        try:
            recovery_required: bool | None = bool(self.pending_journals())
        except (OSError, RuntimeError, ValueError):
            recovery_required = None
        try:
            # The unavailable read-only toolchain intentionally raises here.
            getattr(self._deployer.audio_toolchain, "generate_gap")
            toolchain_available: bool | None = True
        except (AttributeError, OSError, RuntimeError, ValueError):
            toolchain_available = False
        try:
            game_is_running: bool | None = self._game_running()
        except (OSError, RuntimeError, ValueError):
            game_is_running = None
        return DeploymentCapabilities(
            game_running=game_is_running,
            toolchain_available=toolchain_available,
            recovery_required=recovery_required,
        )

    @staticmethod
    def _require_writer_ready(capabilities: DeploymentCapabilities, *, require_toolchain: bool) -> None:
        if capabilities.recovery_required is not False:
            raise VoiceLabOperationError("VOICE_RECOVERY_REQUIRED", "Voice Lab recovery must finish before another writer operation")
        if capabilities.game_running is not False:
            raise VoiceLabOperationError("VOICE_GAME_RUNNING", "Close JA2 before changing Voice Lab files")
        if require_toolchain and capabilities.toolchain_available is not True:
            raise VoiceLabOperationError("VOICE_TOOLCHAIN_UNAVAILABLE", "FFmpeg toolchain is unavailable")

    def deployment_history(self) -> list[dict[str, Any]]:
        rows = self.store.connection.execute("SELECT deployment_id FROM deployments ORDER BY rowid").fetchall()
        return [self.store.deployment(str(row["deployment_id"])) for row in rows]

    def backup_is_pinned(self, backup_id: str) -> bool:
        from mercwizard_core import backup
        return next(item.pinned for item in backup.list_backups(self._deployer.install_id, base=self._deployer.backup_base) if item.id == backup_id)

    def _game_running(self) -> bool:
        # Use the registered target's executable spelling (JA2.exe versus a
        # 1.13 variant) when this service is reached from the real sidecar.
        # Hermetic tests may intentionally supply no registered install.
        info = self._state.get_install(self._deployer.install_id) if hasattr(self._state, "get_install") else None
        executable = getattr(info, "exe_path", None)
        return bool(game_running(Path(executable).name if executable else "ja2.exe"))

    @classmethod
    def reopen_for_test(cls, root: Path) -> "VoiceLabService":  # pragma: no cover - exercised by child helper
        class _RecoveryToolchain:
            def generate_gap(self, audio: Path):
                from .audio import GapResult
                return GapResult(b"", 1, ())
        return cls.for_install("crash-fixture", Path(root) / "install", Path(root) / "store.sqlite3", workspace=Path(root) / "workspace", audio_toolchain=_RecoveryToolchain())

    def recovery_test_preimage_restored(self) -> bool:  # pragma: no cover - child fixture hook
        marker = self._deployer.install_root / "preimage.marker"
        return marker.is_file() and marker.read_bytes() == b"preimage"


def recover_registered_installs(state: Any) -> None:
    """Startup hook: recover only explicitly registered installs, never scan disks."""
    for info in state.list_installs():
        # Journal recovery restores already-rendered files from backups and
        # reconciles durable metadata; it never decodes or renders audio.
        # Supplying the recovery-only marker keeps optional FFmpeg authoring
        # setup from becoming a requirement for the entire sidecar to boot.
        service = VoiceLabService.for_install(
            info.id,
            info.path,
            state=state,
            audio_toolchain=_RecoveryOnlyAudioToolchain(),
        )
        try:
            service.recover_pending()
        finally:
            service.close()
