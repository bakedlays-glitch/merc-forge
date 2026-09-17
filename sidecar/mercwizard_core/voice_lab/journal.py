"""Durable, install-scoped journals for Voice Lab deployment and Undo.

The journal is deliberately independent from the SQLite review index.  It is
the recovery record that survives a killed sidecar between atomic target
replacements.  Callers must record ``mark_target_applied`` *after* a target
replacement and before attempting the next target.  Each public transition
persists and fsyncs its full JSON document before this object exposes the new
state to its caller.
"""
from __future__ import annotations

from copy import deepcopy
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import Enum
import json
import os
from pathlib import Path
import threading
from typing import Any, Mapping
from uuid import uuid4

import portalocker

from ..inject._atomic_xml import write_bytes_atomic


JOURNAL_SCHEMA_VERSION = 1


class JournalState(str, Enum):
    """Lifecycle states for a deployment or Undo transaction."""

    prepared = "prepared"
    applying = "applying"
    complete = "complete"
    recovering = "recovering"
    recovered = "recovered"
    failed = "failed"


class TargetState(str, Enum):
    """Per-target progress retained across a process restart."""

    pending = "pending"
    applied = "applied"
    recovered = "recovered"


_TERMINAL_STATES = {JournalState.complete, JournalState.recovered}
_RECOVERABLE_STATES = {
    JournalState.prepared,
    JournalState.applying,
    JournalState.recovering,
    JournalState.failed,
}
_JOURNAL_LOCKS_GUARD = threading.Lock()
_JOURNAL_LOCKS: dict[str, threading.RLock] = {}


class JournalValidationError(ValueError):
    """Raised when a journal is incomplete, corrupt, or illegally transitioned."""


@dataclass(frozen=True)
class JournalTarget:
    """A validated target entry from an on-disk journal."""

    target_id: str
    target_path: str
    preimage_sha256: str | None
    staged_sha256: str | None
    existed_before: bool
    state: TargetState
    data: dict[str, Any]


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _appdata_root() -> Path:
    appdata = os.environ.get("APPDATA")
    return Path(appdata) / "MercWizard" if appdata else Path.home() / ".config" / "MercWizard"


def journals_dir(install_id: str, base: Path | None = None) -> Path:
    """Return the durable per-install journal directory without probing a repo."""
    if not _is_nonempty_string(install_id) or install_id in {".", ".."} or any(
        separator in install_id for separator in ("/", "\\")
    ):
        raise ValueError("install_id must be a non-empty opaque identifier")
    root = Path(base) if base is not None else _appdata_root()
    return root / "voice_lab" / install_id / "journals"


class DeploymentJournal:
    """A validated journal whose transitions are atomic and durable.

    ``prepare`` receives a serializable deployment mapping with ``install_id``,
    ``backup_id``, and an ordered ``targets`` list.  Every target needs a
    stable ``target_id``, a relative/absolute ``target_path``, its preimage and
    staged SHA-256 values (``None`` is valid only for a genuinely new target),
    and an ``existed_before`` boolean.  Additional JSON metadata is preserved
    so deploy/Undo can carry exact staged paths and record-level evidence.
    """

    def __init__(self, path: Path, document: Mapping[str, Any]) -> None:
        self.path = Path(path)
        self._document = _validate_document(document)

    @classmethod
    def prepare(
        cls,
        directory: Path,
        deployment: Mapping[str, Any],
    ) -> "DeploymentJournal":
        """Create a fsynced ``prepared`` journal before live target writes."""
        raw = deepcopy(dict(deployment))
        journal_id = str(raw.pop("journal_id", raw.pop("deployment_id", "")) or uuid4().hex)
        if (
            not _is_nonempty_string(journal_id)
            or journal_id in {".", ".."}
            or any(sep in journal_id for sep in ("/", "\\"))
        ):
            raise JournalValidationError("journal_id must be a non-empty filename-safe string")
        now = _utc_now()
        document: dict[str, Any] = {
            "schema_version": JOURNAL_SCHEMA_VERSION,
            "journal_id": journal_id,
            "install_id": raw.pop("install_id", None),
            "operation": raw.pop("operation", "deploy"),
            "backup_id": raw.pop("backup_id", None),
            "created_utc": now,
            "updated_utc": now,
            "revision": 0,
            "state": JournalState.prepared.value,
            "targets": raw.pop("targets", None),
            "metadata": raw.pop("metadata", {}),
        }
        if raw:
            # Preserve future deployment fields without letting them shadow the
            # journal protocol's top-level state/evidence fields.
            metadata = document["metadata"]
            if not isinstance(metadata, Mapping):
                raise JournalValidationError("metadata must be a JSON object")
            document["metadata"] = {**dict(metadata), **raw}
        validated = _validate_document(document)
        directory = Path(directory)
        path = directory / f"{journal_id}.json"
        with _journal_lock(path):
            if path.exists():
                raise FileExistsError(f"journal already exists: {path}")
            # A generated UUID makes collisions negligible; callers passing a
            # deterministic deployment id get an explicit refusal instead of
            # an accidental replacement of recovery history.
            write_bytes_atomic(path, _encode_document(validated))
        return cls(path, validated)

    @classmethod
    def prepare_for_install(
        cls,
        install_id: str,
        deployment: Mapping[str, Any],
        *,
        base: Path | None = None,
    ) -> "DeploymentJournal":
        """Prepare under Merc Forge app data for one registered install."""
        payload = dict(deployment)
        payload_install_id = payload.get("install_id")
        if payload_install_id is not None and payload_install_id != install_id:
            raise JournalValidationError("payload install_id does not match prepare_for_install install_id")
        payload["install_id"] = install_id
        return cls.prepare(journals_dir(install_id, base), payload)

    @classmethod
    def load(cls, path: Path) -> "DeploymentJournal":
        """Load and strictly validate one durable journal document."""
        path = Path(path)
        try:
            document = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            raise
        except (OSError, json.JSONDecodeError) as exc:
            raise JournalValidationError(f"cannot read journal {path}: {exc}") from exc
        return cls(path, document)

    @property
    def journal_id(self) -> str:
        return str(self._document["journal_id"])

    @property
    def install_id(self) -> str:
        return str(self._document["install_id"])

    @property
    def operation(self) -> str:
        return str(self._document["operation"])

    @property
    def backup_id(self) -> str:
        return str(self._document["backup_id"])

    @property
    def state(self) -> JournalState:
        return JournalState(str(self._document["state"]))

    @property
    def revision(self) -> int:
        return int(self._document["revision"])

    @property
    def created_utc(self) -> str:
        return str(self._document["created_utc"])

    @property
    def updated_utc(self) -> str:
        return str(self._document["updated_utc"])

    @property
    def metadata(self) -> dict[str, Any]:
        return deepcopy(self._document["metadata"])

    @property
    def targets(self) -> tuple[JournalTarget, ...]:
        return tuple(_target_from_data(target) for target in self._document["targets"])

    @property
    def document(self) -> dict[str, Any]:
        """A copy suitable for durable manifests and recovery diagnostics."""
        return deepcopy(self._document)

    @property
    def is_terminal(self) -> bool:
        return self.state in _TERMINAL_STATES

    def mark_target_applied(self, target: int | str) -> "DeploymentJournal":
        """Durably record one successful live replacement before the next one."""
        self._require_state({JournalState.prepared, JournalState.applying})
        index = self._target_index(target)
        if self.targets[index].state is not TargetState.pending:
            raise JournalValidationError(f"target {self.targets[index].target_id!r} is not pending")
        document = self.document
        document["targets"][index]["state"] = TargetState.applied.value
        document["state"] = JournalState.applying.value
        return self._transition(document)

    def complete(self) -> "DeploymentJournal":
        """Mark a deployment/Undo complete only after every target was recorded."""
        self._require_state({JournalState.prepared, JournalState.applying})
        pending = [target.target_id for target in self.targets if target.state is TargetState.pending]
        if pending:
            raise JournalValidationError(f"cannot complete journal with pending targets: {', '.join(pending)}")
        if any(target.state is not TargetState.applied for target in self.targets):
            raise JournalValidationError("cannot complete journal with recovered targets")
        document = self.document
        document["state"] = JournalState.complete.value
        return self._transition(document)

    def begin_recovery(self) -> "DeploymentJournal":
        """Durably block new writes while a recovery owner restores targets."""
        self._require_state(_RECOVERABLE_STATES)
        document = self.document
        document["state"] = JournalState.recovering.value
        return self._transition(document)

    def mark_target_recovered(self, target: int | str) -> "DeploymentJournal":
        """Record one restoration after it has been verified on disk."""
        self._require_state({JournalState.recovering})
        index = self._target_index(target)
        if self.targets[index].state is TargetState.recovered:
            raise JournalValidationError(f"target {self.targets[index].target_id!r} is already recovered")
        document = self.document
        document["targets"][index]["state"] = TargetState.recovered.value
        return self._transition(document)

    def complete_recovery(self) -> "DeploymentJournal":
        """Mark recovery terminal after every target has a durable recovered state."""
        self._require_state({JournalState.recovering})
        incomplete = [target.target_id for target in self.targets if target.state is not TargetState.recovered]
        if incomplete:
            raise JournalValidationError(
                f"cannot complete recovery with unrecovered targets: {', '.join(incomplete)}"
            )
        document = self.document
        document["state"] = JournalState.recovered.value
        return self._transition(document)

    def fail(self, reason: str) -> "DeploymentJournal":
        """Persist a recoverable failure without discarding the target evidence."""
        self._require_state({JournalState.prepared, JournalState.applying, JournalState.recovering})
        if not _is_nonempty_string(reason):
            raise JournalValidationError("failure reason must be non-empty")
        document = self.document
        document["state"] = JournalState.failed.value
        document["failure"] = {"reason": reason, "recorded_utc": _utc_now()}
        return self._transition(document)

    def _target_index(self, target: int | str) -> int:
        if isinstance(target, bool):
            raise JournalValidationError("target index must be an integer or target_id")
        if isinstance(target, int):
            if 0 <= target < len(self.targets):
                return target
            raise JournalValidationError(f"target index out of range: {target}")
        if isinstance(target, str):
            for index, item in enumerate(self.targets):
                if item.target_id == target:
                    return index
            raise JournalValidationError(f"unknown target id: {target}")
        raise JournalValidationError("target must be an integer index or target_id")

    def _require_state(self, allowed: set[JournalState]) -> None:
        if self.state not in allowed:
            choices = ", ".join(state.value for state in sorted(allowed, key=lambda item: item.value))
            raise JournalValidationError(f"journal state {self.state.value!r} does not permit this transition ({choices})")

    def _transition(self, document: Mapping[str, Any]) -> "DeploymentJournal":
        """Persist a complete next revision before publishing it in memory."""
        expected_revision = document.get("revision")
        if isinstance(expected_revision, bool) or not isinstance(expected_revision, int):
            raise JournalValidationError("transition document must carry an integer revision")
        with _journal_lock(self.path):
            durable = DeploymentJournal.load(self.path)
            if durable.revision != expected_revision:
                raise JournalValidationError(
                    f"stale journal revision {expected_revision}; durable revision is {durable.revision}"
                )
            next_document = deepcopy(dict(document))
            next_document["revision"] = durable.revision + 1
            next_document["updated_utc"] = _next_utc_after(durable.updated_utc)
            validated = _validate_document(next_document)
            # This returns only after durable replacement. Until then ``self``
            # still describes the old persisted state, so a caller cannot
            # mutate another live target based on memory-only progress.
            write_bytes_atomic(self.path, _encode_document(validated))
            self._document = validated
            return self


def pending_journals(directory: Path) -> list[DeploymentJournal]:
    """Return recoverable journals in deterministic creation/id order.

    Corrupt journals are intentionally surfaced instead of skipped: silently
    ignoring a partial transaction would permit a later write over an unknown
    live install state.
    """
    directory = Path(directory)
    if not directory.is_dir():
        return []
    journals = [
        DeploymentJournal.load(path)
        for path in sorted(directory.glob("*.json"))
    ]
    return sorted(
        (journal for journal in journals if journal.state not in _TERMINAL_STATES),
        key=lambda journal: (journal.created_utc, journal.journal_id),
    )


def pending_journals_for_install(
    install_id: str,
    *,
    base: Path | None = None,
) -> list[DeploymentJournal]:
    """Read nonterminal journals from the normal per-install app-data home."""
    directory = journals_dir(install_id, base)
    if not directory.is_dir():
        return []
    # Validate every record, including terminal history. A misplaced terminal
    # journal is still evidence that app-data was crossed and must not be
    # silently accepted merely because it no longer blocks a writer.
    journals = [DeploymentJournal.load(path) for path in sorted(directory.glob("*.json"))]
    mismatched = [journal.journal_id for journal in journals if journal.install_id != install_id]
    if mismatched:
        raise JournalValidationError(
            f"journal install_id mismatch for {install_id}: {', '.join(mismatched)}"
        )
    return sorted(
        (journal for journal in journals if journal.state not in _TERMINAL_STATES),
        key=lambda journal: (journal.created_utc, journal.journal_id),
    )


def _validate_document(raw: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(raw, Mapping):
        raise JournalValidationError("journal document must be an object")
    document = deepcopy(dict(raw))
    _require_exact_int(document.get("schema_version"), JOURNAL_SCHEMA_VERSION, "schema_version")
    for field in ("journal_id", "install_id", "operation", "backup_id", "created_utc", "updated_utc"):
        if not _is_nonempty_string(document.get(field)):
            raise JournalValidationError(f"{field} must be a non-empty string")
    _validate_utc(str(document["created_utc"]), "created_utc")
    _validate_utc(str(document["updated_utc"]), "updated_utc")
    revision = document.get("revision")
    if isinstance(revision, bool) or not isinstance(revision, int) or revision < 0:
        raise JournalValidationError("revision must be a non-negative integer")
    try:
        state = JournalState(str(document.get("state")))
    except ValueError as exc:
        raise JournalValidationError("journal state is invalid") from exc
    targets = document.get("targets")
    if not isinstance(targets, list):
        raise JournalValidationError("targets must be a list")
    normalized_targets = [_validate_target(target) for target in targets]
    target_ids = [target["target_id"] for target in normalized_targets]
    if len(set(target_ids)) != len(target_ids):
        raise JournalValidationError("target_id values must be unique")
    normalized_paths = [target["target_path"].casefold() for target in normalized_targets]
    if len(set(normalized_paths)) != len(normalized_paths):
        raise JournalValidationError("duplicate target_path after Windows normalization")
    metadata = document.get("metadata")
    if not isinstance(metadata, Mapping):
        raise JournalValidationError("metadata must be a JSON object")
    if state is JournalState.complete and any(target["state"] != TargetState.applied.value for target in normalized_targets):
        raise JournalValidationError("complete journals require every target to be applied")
    if state is JournalState.recovered and any(target["state"] != TargetState.recovered.value for target in normalized_targets):
        raise JournalValidationError("recovered journals require every target to be recovered")
    document["state"] = state.value
    document["targets"] = normalized_targets
    document["metadata"] = deepcopy(dict(metadata))
    _assert_json_serializable(document)
    return document


def _validate_target(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, Mapping):
        raise JournalValidationError("every target must be an object")
    target = deepcopy(dict(raw))
    if not _is_nonempty_string(target.get("target_id")):
        raise JournalValidationError("target target_id must be a non-empty string")
    target["target_path"] = _normalize_target_path(target.get("target_path"))
    existed_before = target.get("existed_before")
    if not isinstance(existed_before, bool):
        raise JournalValidationError("target existed_before must be a boolean")
    for field in ("preimage_sha256", "staged_sha256"):
        if field not in target:
            raise JournalValidationError(f"target {field} is required (use null when absent)")
    _validate_sha256(target.get("preimage_sha256"), "preimage_sha256")
    kind = target.get("kind")
    if kind is not None and not _is_nonempty_string(kind):
        raise JournalValidationError("target kind must be a non-empty string when present")
    removal = kind in {"remove", "remove_shadowing_variant"}
    _validate_sha256(target.get("staged_sha256"), "staged_sha256")
    state = target.get("state", TargetState.pending.value)
    try:
        target_state = TargetState(str(state))
    except ValueError as exc:
        raise JournalValidationError("target state is invalid") from exc
    if existed_before and target.get("preimage_sha256") is None:
        raise JournalValidationError("existing target requires preimage_sha256")
    if not existed_before and target.get("preimage_sha256") is not None:
        raise JournalValidationError("new target must not declare preimage_sha256")
    if target.get("staged_sha256") is None and not removal:
        raise JournalValidationError("target staged_sha256 is required")
    target["state"] = target_state.value
    return target


def _target_from_data(data: Mapping[str, Any]) -> JournalTarget:
    return JournalTarget(
        target_id=str(data["target_id"]),
        target_path=str(data["target_path"]),
        preimage_sha256=data["preimage_sha256"],
        staged_sha256=data["staged_sha256"],
        existed_before=bool(data["existed_before"]),
        state=TargetState(str(data["state"])),
        data=deepcopy(dict(data)),
    )


def _validate_sha256(value: Any, field: str) -> None:
    if value is None:
        return
    if not isinstance(value, str) or len(value) != 64 or any(char not in "0123456789abcdef" for char in value.lower()):
        raise JournalValidationError(f"target {field} must be a SHA-256 hex string or null")


def _normalize_target_path(value: Any) -> str:
    """Accept exactly one safe, install-root-relative Windows target path."""
    if not _is_nonempty_string(value):
        raise JournalValidationError("target_path must be a non-empty relative Windows path")
    raw = str(value)
    # Reject rooted/UNC/device forms before separator normalization.  A colon
    # is never valid in a JA2 relative target component: it would be a drive
    # prefix or a Windows alternate data stream.
    if raw.startswith(("/", "\\")) or ":" in raw:
        raise JournalValidationError("target_path must be install-root-relative and colon-free")
    normalized = raw.replace("/", "\\")
    segments = normalized.split("\\")
    if any(
        not segment
        or segment in {".", ".."}
        or segment.endswith((".", " "))
        or ":" in segment
        for segment in segments
    ):
        raise JournalValidationError("target_path contains an unsafe Windows path segment")
    return "\\".join(segments)


@contextmanager
def _journal_lock(path: Path):
    """Serialize one journal's create/transition path across processes."""
    path = Path(path)
    resolved = str(path.resolve()).casefold()
    with _JOURNAL_LOCKS_GUARD:
        local_lock = _JOURNAL_LOCKS.setdefault(resolved, threading.RLock())
    with local_lock:
        path.parent.mkdir(parents=True, exist_ok=True)
        with portalocker.Lock(
            str(path.parent / f".{path.name}.lock"),
            mode="a+",
            timeout=10.0,
            check_interval=0.05,
            flags=portalocker.LOCK_EX,
        ):
            yield


def _next_utc_after(previous: str) -> str:
    """Return a UTC timestamp strictly later than the durable prior value."""
    prior = _parse_utc(previous, "updated_utc")
    now = _parse_utc(_utc_now(), "updated_utc")
    return (now if now > prior else prior + timedelta(microseconds=1)).isoformat()


def _require_exact_int(value: Any, expected: int, field: str) -> None:
    if type(value) is not int or value != expected:
        raise JournalValidationError(f"{field} must be {expected}")


def _validate_utc(value: str, field: str) -> None:
    try:
        _parse_utc(value, field)
    except ValueError as exc:
        raise JournalValidationError(f"{field} must be a UTC ISO-8601 timestamp") from exc


def _parse_utc(value: str, field: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() != timedelta(0):
        raise ValueError(f"{field} must be a UTC ISO-8601 timestamp")
    return parsed


def _is_nonempty_string(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _assert_json_serializable(value: Any) -> None:
    try:
        json.dumps(value, sort_keys=True, separators=(",", ":"))
    except (TypeError, ValueError) as exc:
        raise JournalValidationError("journal fields must be JSON serializable") from exc


def _encode_document(document: Mapping[str, Any]) -> bytes:
    return (json.dumps(document, indent=2, sort_keys=True, ensure_ascii=False) + "\n").encode("utf-8")
