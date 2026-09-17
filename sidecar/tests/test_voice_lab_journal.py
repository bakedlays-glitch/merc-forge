"""Durability and schema tests for Voice Lab deployment journals."""
from __future__ import annotations

import json
from pathlib import Path
import threading

import pytest

from mercwizard_core.voice_lab.journal import (
    DeploymentJournal,
    JournalState,
    JournalValidationError,
    pending_journals,
    pending_journals_for_install,
)


def deployment_fixture() -> dict:
    return {
        "journal_id": "deploy-1",
        "install_id": "test-install",
        "operation": "deploy",
        "backup_id": "backup-1",
        "targets": [
            {
                "target_id": "speech/15/001.ogg",
                "target_path": "Data-1.13/Speech/15/001.ogg",
                "preimage_sha256": None,
                "staged_sha256": "a" * 64,
                "existed_before": False,
            },
            {
                "target_id": "edt/15",
                "target_path": "Data-1.13/MercEdt/15.EDT",
                "preimage_sha256": "b" * 64,
                "staged_sha256": "c" * 64,
                "existed_before": True,
            },
        ],
        "metadata": {"recipe_id": "recipe-1"},
    }


def test_prepared_journal_survives_reopen(tmp_path: Path) -> None:
    journal = DeploymentJournal.prepare(tmp_path, deployment_fixture())

    reopened = DeploymentJournal.load(journal.path)

    assert reopened.state == JournalState.prepared
    assert reopened.targets[0].state == "pending"
    assert reopened.revision == 0
    assert reopened.backup_id == "backup-1"


def test_target_transition_is_durable_before_memory_changes(tmp_path: Path, monkeypatch) -> None:
    journal = DeploymentJournal.prepare(tmp_path, deployment_fixture())
    before = journal.path.read_bytes()

    import mercwizard_core.voice_lab.journal as journal_module

    def fail_write(_path: Path, _data: bytes) -> None:
        raise OSError("simulated durable-write failure")

    monkeypatch.setattr(journal_module, "write_bytes_atomic", fail_write)
    with pytest.raises(OSError, match="durable-write"):
        journal.mark_target_applied(0)

    assert journal.state == JournalState.prepared
    assert journal.targets[0].state == "pending"
    assert journal.path.read_bytes() == before


def test_transitions_increment_revision_and_terminal_journals_are_not_pending(tmp_path: Path) -> None:
    first = DeploymentJournal.prepare(tmp_path, deployment_fixture())
    first.mark_target_applied("speech/15/001.ogg")
    first.mark_target_applied(1)
    first.complete()
    assert first.revision == 3
    assert first.state == JournalState.complete

    recovered_payload = deployment_fixture()
    recovered_payload["journal_id"] = "recover-1"
    recovered = DeploymentJournal.prepare(tmp_path, recovered_payload)
    recovered.begin_recovery()
    recovered.mark_target_recovered(0)
    recovered.mark_target_recovered(1)
    recovered.complete_recovery()

    failed_payload = deployment_fixture()
    failed_payload["journal_id"] = "failed-1"
    failed = DeploymentJournal.prepare(tmp_path, failed_payload)
    failed.fail("verification failed")

    pending = pending_journals(tmp_path)
    assert [item.journal_id for item in pending] == ["failed-1"]
    assert all(item.state in {JournalState.prepared, JournalState.applying, JournalState.failed} for item in pending)


def test_load_rejects_invalid_target_state(tmp_path: Path) -> None:
    path = tmp_path / "invalid.json"
    document = deployment_fixture()
    document.update({
        "schema_version": 1,
        "created_utc": "2026-01-01T00:00:00+00:00",
        "updated_utc": "2026-01-01T00:00:00+00:00",
        "revision": 0,
        "state": "prepared",
    })
    document["targets"][0]["state"] = "not-a-target-state"
    path.write_text(json.dumps(document))

    with pytest.raises(JournalValidationError, match="target state"):
        DeploymentJournal.load(path)


def test_load_rejects_non_utc_transition_time(tmp_path: Path) -> None:
    path = tmp_path / "invalid-time.json"
    document = deployment_fixture()
    document.update({
        "schema_version": 1,
        "created_utc": "2026-01-01T00:00:00+05:00",
        "updated_utc": "2026-01-01T00:00:00+05:00",
        "revision": 0,
        "state": "prepared",
    })
    path.write_text(json.dumps(document))

    with pytest.raises(JournalValidationError, match="UTC"):
        DeploymentJournal.load(path)


def test_complete_requires_all_targets_to_be_durably_applied(tmp_path: Path) -> None:
    journal = DeploymentJournal.prepare(tmp_path, deployment_fixture())

    with pytest.raises(JournalValidationError, match="pending targets"):
        journal.complete()


def test_prepare_for_install_rejects_mismatched_payload_install(tmp_path: Path) -> None:
    payload = deployment_fixture()
    payload["install_id"] = "other-install"

    with pytest.raises(JournalValidationError, match="install_id"):
        DeploymentJournal.prepare_for_install("expected-install", payload, base=tmp_path)


def test_pending_for_install_rejects_misplaced_journal(tmp_path: Path) -> None:
    payload = deployment_fixture()
    payload["install_id"] = "expected-install"
    journal = DeploymentJournal.prepare_for_install("expected-install", payload, base=tmp_path)
    document = json.loads(journal.path.read_text())
    document["install_id"] = "other-install"
    journal.path.write_text(json.dumps(document))

    with pytest.raises(JournalValidationError, match="install_id"):
        pending_journals_for_install("expected-install", base=tmp_path)


def test_pending_for_install_rejects_misplaced_terminal_history(tmp_path: Path) -> None:
    payload = deployment_fixture()
    payload["install_id"] = "expected-install"
    journal = DeploymentJournal.prepare_for_install("expected-install", payload, base=tmp_path)
    journal.mark_target_applied(0)
    journal.mark_target_applied(1)
    journal.complete()
    document = json.loads(journal.path.read_text())
    document["install_id"] = "other-install"
    journal.path.write_text(json.dumps(document))

    with pytest.raises(JournalValidationError, match="install_id"):
        pending_journals_for_install("expected-install", base=tmp_path)


@pytest.mark.parametrize("unsafe_path", [
    "/Data-1.13/Speech/15/001.ogg",
    "\\\\server\\share\\voice.ogg",
    "C:\\Data-1.13\\Speech\\15\\001.ogg",
    "Data-1.13/../outside.ogg",
    "Data-1.13/./Speech/15/001.ogg",
    "Data-1.13//Speech/15/001.ogg",
    "Data-1.13/Speech:stream/15/001.ogg",
    "Data-1.13/Speech/15/001.ogg/",
    "Data-1.13/Speech/15/001.ogg.",
    "Data-1.13/Speech/15/alias ",
])
def test_journal_rejects_unsafe_target_paths(tmp_path: Path, unsafe_path: str) -> None:
    payload = deployment_fixture()
    payload["targets"][0]["target_path"] = unsafe_path

    with pytest.raises(JournalValidationError, match="target_path"):
        DeploymentJournal.prepare(tmp_path, payload)


def test_journal_normalizes_target_path_and_rejects_casefold_alias(tmp_path: Path) -> None:
    payload = deployment_fixture()
    payload["targets"][0]["target_path"] = "Data-1.13/Speech/15/001.ogg"
    payload["targets"][1]["target_path"] = "data-1.13\\speech\\15\\001.OGG"

    with pytest.raises(JournalValidationError, match="duplicate target_path"):
        DeploymentJournal.prepare(tmp_path, payload)

    payload["targets"] = payload["targets"][:1]
    journal = DeploymentJournal.prepare(tmp_path, payload)
    assert journal.targets[0].target_path == "Data-1.13\\Speech\\15\\001.ogg"


def test_stale_loaded_journal_cannot_overwrite_newer_transition(tmp_path: Path) -> None:
    journal = DeploymentJournal.prepare(tmp_path, deployment_fixture())
    stale = DeploymentJournal.load(journal.path)
    journal.mark_target_applied(0)

    with pytest.raises(JournalValidationError, match="stale"):
        stale.mark_target_applied(0)


def test_transition_timestamp_is_strictly_monotonic_when_clock_regresses(tmp_path: Path, monkeypatch) -> None:
    journal = DeploymentJournal.prepare(tmp_path, deployment_fixture())
    before = journal.updated_utc
    import mercwizard_core.voice_lab.journal as journal_module
    monkeypatch.setattr(journal_module, "_utc_now", lambda: "2000-01-01T00:00:00+00:00")

    journal.mark_target_applied(0)

    assert journal.updated_utc > before


def test_same_instance_concurrent_transitions_reject_stale_document(tmp_path: Path, monkeypatch) -> None:
    journal = DeploymentJournal.prepare(tmp_path, deployment_fixture())
    barrier = threading.Barrier(2)
    original_transition = journal._transition
    results: list[str] = []

    def gated_transition(document):
        barrier.wait(timeout=5)
        return original_transition(document)

    monkeypatch.setattr(journal, "_transition", gated_transition)

    def apply(target: int) -> None:
        try:
            journal.mark_target_applied(target)
        except JournalValidationError as exc:
            results.append(f"error:{exc}")
        else:
            results.append("success")

    first = threading.Thread(target=apply, args=(0,))
    second = threading.Thread(target=apply, args=(1,))
    first.start()
    second.start()
    first.join(timeout=5)
    second.join(timeout=5)

    assert not first.is_alive() and not second.is_alive()
    assert results.count("success") == 1
    assert sum(result.startswith("error:stale") for result in results) == 1
    reopened = DeploymentJournal.load(journal.path)
    assert reopened.revision == 1
    assert sum(target.state == "applied" for target in reopened.targets) == 1
