"""Tests for the in-process SidecarState (concurrency, persistence shims)."""
from __future__ import annotations

import threading
import time
from pathlib import Path

import pytest

from mercwizard_core.install_detect import InstallInfo
from routes.state import SidecarState, get_state


@pytest.fixture(autouse=True)
def _reset_singleton():
    """Wipe the module-level singleton between tests so state doesn't bleed."""
    state = get_state()
    state._installs = {}
    state._active_install_id = None
    state._scan_done = False
    state._persisted_installs = []
    state._pending_active_install_id = None
    yield
    state._installs = {}
    state._active_install_id = None
    state._scan_done = False
    state._persisted_installs = []


def _make_fake_install(tmp_path: Path, name: str) -> Path:
    """A directory that validate_install() accepts as a real JA2 install."""
    root = tmp_path / name
    root.mkdir()
    (root / "JA2.exe").touch()
    table = root / "Data-1.13" / "TableData"
    table.mkdir(parents=True)
    (table / "MercProfiles.xml").write_text("<MERCPROFILES />")
    (table / "AIMAvailability.xml").write_text("<AIM_AVAILABLES />")
    return root


def test_refresh_installs_preserves_concurrent_register(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Bug A4 — refresh_installs() releases the lock during validation.
    A concurrent register_manual_install that arrives during that
    window writes into self._installs; a naive
    `self._installs = new_map` swap would clobber the concurrent
    registration. The merge has to overlay new entries on top of the
    existing dict so concurrent additions survive.

    After bug #12 (auto-detect removed), the slow path is now
    `validate_install` itself instead of `detect_installs`. We patch
    that to widen the lockless window.

    Repro:
      1. Pre-populate state with one install (so existing_snapshot at
         refresh start is non-empty).
      2. Start refresh_installs on a background thread with a slowed
         validate_install to widen the unlocked window.
      3. From the main thread mid-refresh, register a NEW install manually.
      4. After the refresh completes, both installs must be present.
    """
    state = get_state()

    pre_install_path = _make_fake_install(tmp_path, "pre_existing")
    new_install_path = _make_fake_install(tmp_path, "concurrent_manual")

    # Seed: pretend the pre-existing install was registered last session.
    from mercwizard_core.install_detect import validate_install as real_validate
    pre_info = real_validate(pre_install_path)
    assert pre_info.valid
    state._installs[pre_info.id] = pre_info

    # Slow down validation inside refresh_installs so the lockless
    # window is wide enough to race against. Only the pre-existing
    # path runs through this wrapper (it's the only entry in
    # _persisted_installs / _installs at the moment).
    def slow_validate(path):
        time.sleep(0.5)
        return real_validate(path)

    monkeypatch.setattr("routes.state.validate_install", slow_validate)

    # Kick off refresh on a background thread
    scan_thread = threading.Thread(
        target=state.refresh_installs, name="test-scan", daemon=True
    )
    scan_thread.start()

    # Wait long enough that validate_install is mid-sleep (lock released)
    time.sleep(0.15)

    # Concurrent manual registration during the unlocked window
    manual_info = state.register_manual_install(new_install_path)
    assert manual_info.valid

    # Let the scan finish
    scan_thread.join(timeout=5)
    assert not scan_thread.is_alive(), "refresh thread didn't complete"

    # Both installs must survive the swap
    final_ids = set(state._installs.keys())
    assert pre_info.id in final_ids, "pre-existing install lost"
    assert manual_info.id in final_ids, (
        f"concurrent manual registration was clobbered by the swap. "
        f"Got {final_ids}, expected to contain {manual_info.id}"
    )


def test_refresh_installs_drops_invalid_existing_entries(
    tmp_path: Path,
) -> None:
    """The merge must still drop pre-existing entries whose paths no
    longer validate (path deleted between sessions / mod re-installed
    at a different location). After bug #12 there's no detect_installs
    to monkeypatch — refresh_installs just re-runs validate_install on
    every known path, which naturally fails for deleted paths and
    drops them.
    """
    state = get_state()

    pre_path = _make_fake_install(tmp_path, "pre_existing")
    from mercwizard_core.install_detect import validate_install
    pre_info = validate_install(pre_path)
    state._installs[pre_info.id] = pre_info

    # Wipe the install on disk so revalidation fails
    import shutil
    shutil.rmtree(pre_path)

    state.refresh_installs()

    assert pre_info.id not in state._installs, (
        "pre-existing entry whose path no longer validates should be dropped"
    )


def test_state_persistence_round_trip(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Registering an install + setting active should survive a sidecar
    restart. Persists to `%APPDATA%/MercWizard/state.json` (APPDATA is
    redirected to tmp_path by the autouse `isolate_appdata` fixture).
    """
    # Persistence is gated on _persistence_enabled() which returns False
    # under pytest. Force-enable it for this test only.
    monkeypatch.setattr("routes.state._persistence_enabled", lambda: True)

    install_path = _make_fake_install(tmp_path, "persisted_install")

    # First session: register + activate.
    state_a = SidecarState()
    info = state_a.register_manual_install(install_path)
    assert info.valid, f"setup failed: {info.errors}"
    assert state_a.set_active(info.id) is True

    # state.json should now exist under the (tmp) APPDATA.
    import os
    state_file = Path(os.environ["APPDATA"]) / "MercWizard" / "state.json"
    assert state_file.is_file(), "state.json was never written"
    import json
    saved = json.loads(state_file.read_text(encoding="utf-8"))
    # v2 persistence format: {installs: [{path, vfs_config_path?}, ...]}
    assert saved["version"] == 2
    persisted_paths = [entry["path"] for entry in saved["installs"]]
    assert str(install_path) in persisted_paths
    assert saved["active_install_id"] == info.id

    # Second session: a fresh SidecarState should pick up the persisted
    # state at construction.
    state_b = SidecarState()
    persisted_paths = [p for (p, _vfs) in state_b._persisted_installs]
    assert install_path in persisted_paths, (
        "install_path was not loaded into _persisted_installs from state.json"
    )
    assert state_b._pending_active_install_id == info.id, (
        "active_install_id wasn't restored as pending across the restart"
    )
