"""Regression coverage for physical-install mutation lock scopes."""
from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
from threading import RLock
from types import SimpleNamespace

import pytest

from mercwizard_core import cross_lock
from routes import merc as merc_route


def test_install_root_lock_scope_is_shared_by_profile_aliases(tmp_path: Path):
    root = tmp_path / "Game"
    alias = root / ".." / "Game"
    assert cross_lock.normalized_install_root_lock_scope(root) == (
        cross_lock.normalized_install_root_lock_scope(alias)
    )


def test_install_root_locks_deduplicate_aliases_and_order_distinct_roots(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    root_a = tmp_path / "A"
    root_b = tmp_path / "B"
    entered: list[Path] = []

    @contextmanager
    def fake_lock(root: str | Path):
        entered.append(Path(root).resolve())
        yield

    monkeypatch.setattr(cross_lock, "cross_process_install_root_lock", fake_lock)
    with cross_lock.cross_process_install_roots_lock(
        root_b, root_a / ".." / "A", root_a
    ):
        pass

    assert entered == [root_a.resolve(), root_b.resolve()]


def test_cross_profile_move_acquires_one_physical_lock(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Profile IDs may differ while a cross-install move targets one root."""
    root = tmp_path / "Game"
    source = SimpleNamespace(id="profile-a", path=root)
    target = SimpleNamespace(id="profile-b", path=root / ".")
    state = SimpleNamespace(write_lock=RLock())
    entered: list[Path] = []

    @contextmanager
    def fake_lock(install_root: str | Path):
        entered.append(Path(install_root).resolve())
        yield

    monkeypatch.setattr(cross_lock, "cross_process_install_root_lock", fake_lock)
    monkeypatch.setattr(
        merc_route.bundle_mod,
        "move_between_installs",
        lambda **kwargs: SimpleNamespace(
            source_install_root=str(kwargs["source_install"]),
            target_install_root=str(kwargs["target_install"]),
            files_written=[], portrait_compiled=False, voice_clips_copied=0,
            aim_bio_id_used=None, source_backup_id=None, issues=[],
            partial_failures=[],
        ),
    )

    events: list[dict] = []
    merc_route._run_move_cross_install(
        info=source, target_info=target, state=state,
        source_slot=1, dest_slot=2, force=False, emit=events.append,
    )

    assert entered == [root.resolve()]
    assert events[-1]["done"] is True
    assert events[-1]["ok"] is True


def test_apply_vfs_config_locks_physical_root_not_profile_id(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    from routes import installs as installs_route

    root = tmp_path / "Game"
    root.mkdir()
    ini = root / "Ja2.ini"
    ini.write_text("VFS_CONFIG_INI = old.ini\n", encoding="utf-8")
    vfs = root / "new.ini"
    vfs.write_text("[vfs_config]\n", encoding="utf-8")
    info = SimpleNamespace(id="profile-vfs", path=root, vfs_config_path=vfs)
    state = SimpleNamespace(installs=lambda: {info.id: info}, write_lock=RLock())
    entered: list[Path] = []

    @contextmanager
    def fake_lock(install_root: str | Path):
        entered.append(Path(install_root).resolve())
        yield

    monkeypatch.setattr(installs_route, "get_state", lambda: state)
    monkeypatch.setattr(cross_lock, "cross_process_install_root_lock", fake_lock)
    monkeypatch.setattr(
        "mercwizard_core.vfs.write_vfs_config_to_ja2_ini",
        lambda *_args: None,
    )
    monkeypatch.setattr(
        "mercwizard_core.backup.snapshot",
        lambda *_args, **_kwargs: None,
    )

    result = installs_route.apply_vfs_config(info.id)

    assert entered == [root.resolve()]
    assert result.install_id == info.id


@pytest.mark.parametrize("operation", ["deploy", "recover", "undo"])
def test_voice_lab_writers_lock_deployers_physical_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, operation: str
):
    from mercwizard_core.voice_lab import service as voice_service

    root = tmp_path / "Game"
    deployer = SimpleNamespace(
        install_id="profile-voice", install_root=root,
        recover_pending=lambda: None,
        deploy=lambda _plan: "deployed",
        undo=lambda _deployment: "undone",
    )
    service = voice_service.VoiceLabService.__new__(voice_service.VoiceLabService)
    service._deployer = deployer
    service._state = SimpleNamespace(write_lock=RLock())
    service._plans = {"plan": object()}
    entered: list[Path] = []

    @contextmanager
    def fake_lock(install_root: str | Path):
        entered.append(Path(install_root).resolve())
        yield

    monkeypatch.setattr(voice_service, "cross_process_install_root_lock", fake_lock)
    monkeypatch.setattr(service, "_game_running", lambda: False)
    monkeypatch.setattr(service, "deployment_status", lambda: None)
    monkeypatch.setattr(service, "_require_writer_ready", lambda *_args, **_kwargs: None)

    if operation == "deploy":
        assert service.deploy("plan") == "deployed"
    elif operation == "recover":
        assert service.recover_pending() is None
    else:
        assert service.undo("deployment") == "undone"

    assert entered == [root.resolve()]
