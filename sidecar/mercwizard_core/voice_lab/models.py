"""Domain models shared by the Voice Lab scanner and later API layers."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
import json
from typing import Annotated, Any, Literal, Union

from pydantic import BaseModel, Field, field_validator, model_validator


@dataclass(frozen=True)
class TriggerAlias:
    """One engine enum name and the source comment that explains it."""

    name: str
    meaning: str
    source_comment: str


@dataclass(frozen=True)
class VoiceTrigger:
    """The profile-type-specific interpretation of one dialogue slot."""

    slot: int
    name: str
    meaning: str
    aliases: tuple[TriggerAlias, ...]


class VoiceProfile(BaseModel):
    """One populated MercProfiles row attached to a voice bank."""

    profile_id: int
    profile_type: int
    name: str
    nickname: str
    face_index: int | None
    voice_index: int


class VoiceAsset(BaseModel):
    """One physical source candidate for a logical Voice Lab asset."""

    asset_id: str
    family: Literal["speech", "battle", "gap", "dialogue_edt"]
    voice_index: int
    line_id: str
    extension: str
    source_kind: Literal["loose", "slf"]
    source_locator: str
    layer_rank: int
    size_bytes: int
    mtime_ns: int
    sha256: str
    writable: bool
    winner: bool


class ImportedVoiceAsset(BaseModel):
    """A durable authoring upload kept outside rebuilt scanned inventory."""

    asset_id: str
    install_id: str
    extension: str
    source_locator: str
    size_bytes: int = Field(ge=1)
    sha256: str
    duration_ms: int = Field(gt=0)
    source_kind: Literal["import", "preview"] = "import"
    recipe_id: str | None = None


class TriggerMeaning(BaseModel):
    """A serializable trigger interpretation for one attached profile."""

    slot: int
    name: str
    meaning: str


class VoiceLine(BaseModel):
    """All physical variants grouped under one logical voice-bank line."""

    family: Literal["speech", "battle", "dialogue_edt"]
    voice_index: int
    line_id: str
    audio_variants: list[VoiceAsset] = Field(default_factory=list)
    audio_winner: VoiceAsset | None = None
    gap_variants: list[VoiceAsset] = Field(default_factory=list)
    gap_winner: VoiceAsset | None = None
    dialogue_variants: list[VoiceAsset] = Field(default_factory=list)
    dialogue_edt: VoiceAsset | None = None
    trigger_by_profile_id: dict[int, TriggerMeaning] = Field(default_factory=dict)


class VoiceBank(BaseModel):
    """A voice-index resource with one or more attached merc profiles."""

    voice_index: int
    profiles: list[VoiceProfile] = Field(default_factory=list)
    lines: list[VoiceLine] = Field(default_factory=list)

    @property
    def is_shared(self) -> bool:
        """Whether more than one profile resolves to this voice bank."""
        return len(self.profiles) > 1


class InventorySnapshot(BaseModel):
    """A complete, point-in-time inventory for one registered install."""

    install_id: str
    banks: list[VoiceBank] = Field(default_factory=list)

    def bank(self, voice_index: int) -> VoiceBank:
        """Return one bank or raise a clear error for an unknown index."""
        for bank in self.banks:
            if bank.voice_index == voice_index:
                return bank
        raise KeyError(f"unknown voice bank {voice_index}")

    def line(self, family: str, voice_index: int, line_id: str) -> VoiceLine:
        """Return one logical line or raise a clear error when absent."""
        for line in self.bank(voice_index).lines:
            if line.family == family and line.line_id == str(line_id):
                return line
        raise KeyError(f"unknown {family} line {voice_index}:{line_id}")

    def asset(self, asset_id: str) -> VoiceAsset:
        """Look up a scanned physical candidate by its opaque identifier."""
        for bank in self.banks:
            for line in bank.lines:
                for asset in (
                    *line.audio_variants,
                    *line.gap_variants,
                    *line.dialogue_variants,
                ):
                    if asset.asset_id == asset_id:
                        return asset
        raise KeyError(f"unknown voice asset {asset_id}")


FindingState = Literal[
    "needs_review",
    "accepted",
    "fixed",
    "intentional",
    "false_positive",
    "deferred",
]


class Finding(BaseModel):
    """One regenerated Voice Lab audit finding and its review disposition."""

    stable_key: str
    evidence_hash: str
    code: str
    severity: Literal["critical", "high", "warning", "medium", "low", "info"]
    confidence: float = Field(ge=0.0, le=1.0)
    state: FindingState = "needs_review"
    asset_ids: list[str] = Field(default_factory=list)
    evidence: dict[str, Any] = Field(default_factory=dict)
    suggested_action: str | None = None


class Transcription(BaseModel):
    """A model-provenanced transcript tied to exactly one input hash."""

    asset_id: str
    source_sha256: str
    text: str
    confidence: float = Field(ge=0.0, le=1.0)
    language: str
    model_id: str
    model_version: str
    word_timestamps: list[dict[str, Any]] = Field(default_factory=list)
    created_utc: str


class CutOperation(BaseModel):
    """Remove one inclusive-start, exclusive-end millisecond range."""

    kind: Literal["cut"]
    start_ms: int = Field(ge=0)
    end_ms: int = Field(ge=0)

    @model_validator(mode="after")
    def _has_positive_duration(self) -> "CutOperation":
        if self.end_ms <= self.start_ms:
            raise ValueError("cut range must not be zero-length")
        return self


class TrimOperation(BaseModel):
    """Keep one inclusive-start, exclusive-end millisecond range."""

    kind: Literal["trim"]
    start_ms: int = Field(ge=0)
    end_ms: int = Field(ge=0)

    @model_validator(mode="after")
    def _has_positive_duration(self) -> "TrimOperation":
        if self.end_ms <= self.start_ms:
            raise ValueError("trim range must not be zero-length")
        return self


class NormalizeOperation(BaseModel):
    """Apply the pinned loudness target used by Voice Lab previews."""

    kind: Literal["normalize"]
    target_lufs: float = -16.0
    true_peak_db: float = -1.0


RecipeOperation = Annotated[
    Union[CutOperation, TrimOperation, NormalizeOperation],
    Field(discriminator="kind"),
]


CANONICAL_OUTPUT_FORMAT_CONTRACT = {
    "container": "ogg",
    "codec": "libvorbis",
    "quality": 4,
    "sample_rate_hz": 22050,
    "channels": 1,
}
_PREVIEW_EVIDENCE_FIELDS = (
    "preview_asset_path",
    "preview_sha256",
    "preview_pcm_sha256",
    "preview_ffmpeg_version",
    "preview_recipe_sha256",
    "preview_source_sha256",
)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class EditRecipe(BaseModel):
    """A non-destructive, reproducible proposal for one logical voice line."""

    recipe_id: str
    install_id: str
    voice_index: int
    family: Literal["speech", "battle"]
    line_id: str
    input_asset_id: str
    input_sha256: str
    input_duration_ms: int | None = Field(default=None, gt=0)
    operations: list[RecipeOperation] = Field(default_factory=list)
    replacement_source: dict[str, Any] | None = None
    subtitle: str | None = None
    output_extension: Literal[".ogg"]
    output_format_contract: dict[str, Any] = Field(
        default_factory=lambda: dict(CANONICAL_OUTPUT_FORMAT_CONTRACT)
    )
    preview_asset_path: str | None = None
    preview_sha256: str | None = None
    preview_pcm_sha256: str | None = None
    preview_gap_sha256: str | None = None
    preview_ffmpeg_version: str | None = None
    preview_recipe_sha256: str | None = None
    preview_source_sha256: str | None = None
    preview_gap_present: bool | None = None
    review_status: str = "draft"
    deployment_status: str = "not_deployed"
    deployment_id: str | None = None
    created_utc: str = Field(default_factory=_utc_now)
    updated_utc: str = Field(default_factory=_utc_now)

    @field_validator("output_format_contract")
    @classmethod
    def _validate_output_format_contract(cls, value: dict[str, Any]) -> dict[str, Any]:
        if set(value) != set(CANONICAL_OUTPUT_FORMAT_CONTRACT) or any(
            value.get(key) != expected or type(value.get(key)) is not type(expected)
            for key, expected in CANONICAL_OUTPUT_FORMAT_CONTRACT.items()
        ):
            raise ValueError("output format contract must match canonical OGG/Vorbis q4 output")
        return dict(CANONICAL_OUTPUT_FORMAT_CONTRACT)

    @model_validator(mode="after")
    def _validate_operations(self) -> "EditRecipe":
        cuts = sorted(
            (operation for operation in self.operations if isinstance(operation, CutOperation)),
            key=lambda operation: operation.start_ms,
        )
        for prior, current in zip(cuts, cuts[1:]):
            if current.start_ms < prior.end_ms:
                raise ValueError("cut ranges overlap")
        if self.input_duration_ms is not None:
            for operation in self.operations:
                if isinstance(operation, (CutOperation, TrimOperation)) and operation.end_ms > self.input_duration_ms:
                    raise ValueError("operation range exceeds input duration")
        if self.preview_source_sha256 is not None and self.preview_source_sha256 != self.input_sha256:
            raise ValueError("preview source hash must match the recipe input hash")
        if self.preview_recipe_sha256 is not None and self.preview_recipe_sha256 != self.serialized_recipe_hash():
            raise ValueError("preview recipe hash does not match serialized recipe")
        self._validate_preview_evidence()
        return self

    def _validate_preview_evidence(self) -> None:
        """Require complete, bound preview provenance whenever a preview exists."""
        has_preview_evidence = any(
            getattr(self, field) is not None for field in _PREVIEW_EVIDENCE_FIELDS
        ) or self.preview_gap_present is not None or self.preview_gap_sha256 is not None
        if not has_preview_evidence:
            return
        missing = [field for field in _PREVIEW_EVIDENCE_FIELDS if not getattr(self, field)]
        if missing:
            raise ValueError(f"preview evidence is incomplete: missing {', '.join(missing)}")
        if self.preview_gap_present is None:
            raise ValueError("preview_gap_present must be explicit when preview evidence exists")
        if self.preview_gap_present is True and not self.preview_gap_sha256:
            raise ValueError("preview_gap_sha256 is required when preview_gap_present is true")
        if self.preview_gap_present is not True and self.preview_gap_sha256 is not None:
            raise ValueError("preview_gap_sha256 requires preview_gap_present to be true")

    def serialized_recipe_hash(self) -> str:
        """Hash stable authoring fields without recursively hashing preview results."""
        excluded = {
            "preview_asset_path", "preview_sha256", "preview_pcm_sha256",
            "preview_gap_sha256", "preview_ffmpeg_version", "preview_recipe_sha256",
            "preview_source_sha256", "preview_gap_present", "review_status", "deployment_status",
            "deployment_id", "updated_utc",
        }
        payload = self.model_dump(mode="json", exclude=excluded)
        canonical = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        return sha256(canonical).hexdigest()


def revalidate_edit_recipe(recipe: EditRecipe | dict[str, Any]) -> EditRecipe:
    """Reconstruct recipe data so Pydantic model copies cannot bypass validation."""
    if isinstance(recipe, EditRecipe):
        return EditRecipe.model_validate(recipe.model_dump(mode="python"))
    return EditRecipe.model_validate(recipe)


class TargetAction(BaseModel):
    """One fully staged, install-relative deployment mutation."""

    target_id: str
    relative_path: str
    kind: Literal["replace", "create", "remove_shadowing_variant"]
    staged_path: str | None = None
    staged_sha256: str | None = None
    preimage_sha256: str | None = None
    existed_before: bool
    metadata: dict[str, Any] = Field(default_factory=dict)


class DeployPlan(BaseModel):
    """A reviewed staging snapshot that must be revalidated under the writer locks."""

    plan_id: str
    recipe_id: str
    created_utc: str
    evidence_hash: str
    affected_profiles: list[int] = Field(default_factory=list)
    targets: list[TargetAction] = Field(default_factory=list)
    snapshot: dict[str, Any] = Field(default_factory=dict)


class DeploymentResult(BaseModel):
    """A successful transactional deployment suitable for the history UI."""

    deployment_id: str
    plan_id: str
    recipe_id: str
    backup_id: str
    before_subtitle: str | None = None
    manifest_path: str
    targets: list[TargetAction] = Field(default_factory=list)


class UndoResult(BaseModel):
    """Outcome of a conflict-aware deployment rollback."""

    deployment_id: str
    status: Literal["undone", "UNDO_CONFLICT"]
    message: str | None = None


class DeploymentCapabilities(BaseModel):
    """Writer readiness reported without exposing deployment internals."""

    game_running: bool | None = None
    toolchain_available: bool | None = None
    recovery_required: bool | None = None
