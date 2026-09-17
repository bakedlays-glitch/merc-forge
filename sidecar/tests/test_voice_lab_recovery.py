"""Recovery contracts use a killed child process, never exception simulation."""
from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys

import pytest


@pytest.mark.parametrize("fail_after", ["1", "2", "3", "deploy_before_index", "deploy_after_index", "deploy_before_complete"])
def test_startup_recovers_after_abrupt_process_exit(tmp_path: Path, fail_after: int, monkeypatch) -> None:
    """A hard process exit after any target boundary leaves a recoverable journal."""
    # The helper is deliberately a process boundary; the service owns the
    # actual deployment details and the child calls its test-only crash hook.
    script = Path(__file__).with_name("voice_lab_crash_child.py")
    if not script.is_file():
        pytest.fail("missing real process crash helper")
    environment = dict(os.environ, APPDATA=str(tmp_path / "appdata"), PYTHONPATH=str(Path(__file__).parents[1]))
    result = subprocess.run([sys.executable, str(script), str(tmp_path), str(fail_after)], env=environment, check=False)
    assert result.returncode == 91
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    from mercwizard_core.voice_lab.service import VoiceLabService
    service = VoiceLabService.reopen_for_test(tmp_path)
    service.recover_pending()
    assert (tmp_path / "install" / "Data-1.13" / "Speech" / "108_111.wav").read_bytes() == b"preimage"
    assert (tmp_path / "install" / "Data-1.13" / "MercEdt" / "108.EDT").read_bytes() == (tmp_path / "original.edt").read_bytes()
    assert not (tmp_path / "install" / "Data-1.13" / "Speech" / "108_111.ogg").exists()
    assert service.pending_journals() == []


def test_exit_after_terminal_deploy_keeps_undo_capable_history(tmp_path: Path, monkeypatch) -> None:
    script = Path(__file__).with_name("voice_lab_crash_child.py")
    environment = dict(os.environ, APPDATA=str(tmp_path / "appdata"), PYTHONPATH=str(Path(__file__).parents[1]))
    result = subprocess.run([sys.executable, str(script), str(tmp_path), "deploy_after_complete"], env=environment, check=False)
    assert result.returncode == 91
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    from mercwizard_core.voice_lab.service import VoiceLabService
    service = VoiceLabService.reopen_for_test(tmp_path)
    service.recover_pending()
    assert (tmp_path / "install" / "Data-1.13" / "Speech" / "108_111.ogg").is_file()
    history = service.deployment_history()
    assert len(history) == 1 and history[0]["status"] == "deployed"


def test_pre_index_deploy_recovery_indexes_immutable_recovered_manifest(tmp_path: Path, monkeypatch) -> None:
    """A manifest written before the SQLite commit remains auditable after rollback."""
    script = Path(__file__).with_name("voice_lab_crash_child.py")
    environment = dict(os.environ, APPDATA=str(tmp_path / "appdata"), PYTHONPATH=str(Path(__file__).parents[1]))
    result = subprocess.run([sys.executable, str(script), str(tmp_path), "deploy_before_index"], env=environment, check=False)
    assert result.returncode == 91
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    from mercwizard_core.voice_lab.service import VoiceLabService
    service = VoiceLabService.reopen_for_test(tmp_path)
    service.recover_pending()
    history = service.deployment_history()
    assert len(history) == 1
    assert history[0]["status"] == "recovered"
    manifest = history[0]["manifest"]
    assert manifest["deployment_id"] == history[0]["deployment_id"]
    assert manifest["install_id"] == "crash-fixture"
    assert manifest["recipe_id"] == "crash"
    assert manifest["backup_id"]
    assert service.store.recipe("crash").deployment_status == "not_deployed"


@pytest.mark.parametrize(
    "interrupted, recovery_exit, expected_status",
    [
        ("deploy_before_index", "recover_deploy_before_reconcile", "recovered"),
        ("deploy_before_index", "recover_deploy_after_reconcile", "recovered"),
        ("deploy_before_index", "recover_deploy_before_complete_recovery", "recovered"),
        ("deploy_before_index", "recover_deploy_after_complete_recovery", "recovered"),
        ("undo_before_index", "recover_undo_before_reconcile", "deployed"),
        ("undo_before_index", "recover_undo_after_reconcile", "deployed"),
        ("undo_before_index", "recover_undo_before_complete_recovery", "deployed"),
        ("undo_before_index", "recover_undo_after_complete_recovery", "deployed"),
    ],
)
def test_recovery_crash_boundaries_repeat_to_a_coherent_terminal_state(
    tmp_path: Path, monkeypatch, interrupted: str, recovery_exit: str, expected_status: str,
) -> None:
    """Recovery itself has a durable rollback commit point on either operation."""
    script = Path(__file__).with_name("voice_lab_crash_child.py")
    environment = dict(os.environ, APPDATA=str(tmp_path / "appdata"), PYTHONPATH=str(Path(__file__).parents[1]))
    interrupted_result = subprocess.run(
        [sys.executable, str(script), str(tmp_path), interrupted], env=environment, check=False,
    )
    assert interrupted_result.returncode == 91
    recovery_result = subprocess.run(
        [sys.executable, str(script), str(tmp_path), recovery_exit], env=environment, check=False,
    )
    assert recovery_result.returncode == 91
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    from mercwizard_core.voice_lab.service import VoiceLabService
    service = VoiceLabService.reopen_for_test(tmp_path)
    service.recover_pending()
    history = service.deployment_history()
    assert len(history) == 1 and history[0]["status"] == expected_status
    assert service.pending_journals() == []
    ogg = tmp_path / "install" / "Data-1.13" / "Speech" / "108_111.ogg"
    assert ogg.exists() is (expected_status == "deployed")
    original_backup = history[0]["manifest"]["backup_id"]
    if expected_status == "recovered":
        assert not service.backup_is_pinned(original_backup)
    else:
        from mercwizard_core.voice_lab.journal import DeploymentJournal, journals_dir
        assert service.backup_is_pinned(original_backup)
        undo_journals = []
        for path in journals_dir("crash-fixture").glob("*.json"):
            journal = DeploymentJournal.load(path)
            if journal.operation == "undo":
                undo_journals.append(journal)
        assert len(undo_journals) == 1
        assert not service.backup_is_pinned(undo_journals[0].backup_id)


@pytest.mark.parametrize("fail_after, expected_status", [
    ("undo_before_index", "deployed"), ("undo_after_index", "deployed"),
    ("undo_before_complete", "deployed"), ("undo_after_complete", "undone"),
])
def test_undo_crash_boundaries_reconcile_history_and_postimage(tmp_path: Path, monkeypatch, fail_after: str, expected_status: str) -> None:
    script = Path(__file__).with_name("voice_lab_crash_child.py")
    environment = dict(os.environ, APPDATA=str(tmp_path / "appdata"), PYTHONPATH=str(Path(__file__).parents[1]))
    result = subprocess.run([sys.executable, str(script), str(tmp_path), fail_after], env=environment, check=False)
    assert result.returncode == 91
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    from mercwizard_core.voice_lab.service import VoiceLabService
    service = VoiceLabService.reopen_for_test(tmp_path)
    service.recover_pending()
    history = service.deployment_history()
    assert len(history) == 1 and history[0]["status"] == expected_status
    ogg = tmp_path / "install" / "Data-1.13" / "Speech" / "108_111.ogg"
    assert ogg.exists() is (expected_status == "deployed")
