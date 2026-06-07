"""INI presets + setup flow tests (docs/INI_PRESETS_SPEC.md, SETUP_FLOW_SPEC.md)."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).parent))
from test_ini_editor import make_vfs_install  # noqa: E402

from main import create_app  # noqa: E402
from mercwizard_core import ini_editor as ie  # noqa: E402
from mercwizard_core.ini_presets import (  # noqa: E402
    install_preset_path,
    load_presets,
    save_install_preset,
)
from routes.state import get_state  # noqa: E402


@pytest.fixture(autouse=True)
def reset_state():
    state = get_state()
    state._installs = {}
    state._active_install_id = None
    state._scan_done = False
    state._settings = {}
    yield
    state._installs = {}
    state._active_install_id = None
    state._scan_done = False
    state._settings = {}


@pytest.fixture(autouse=True)
def no_game_running(monkeypatch):
    monkeypatch.setattr(ie, "game_running", lambda exe_name="ja2.exe": False)


@pytest.fixture
def client() -> TestClient:
    return TestClient(create_app())


@pytest.fixture
def registered(client: TestClient, tmp_path: Path) -> dict:
    root = make_vfs_install(tmp_path / "preset_install")
    resp = client.post("/api/v1/installs", json={"path": str(root)})
    assert resp.status_code == 200, resp.text
    info = resp.json()
    client.post("/api/v1/installs/active", json={"install_id": info["id"]})
    info["root"] = str(root)
    return info


# -- loader rules ------------------------------------------------------


def test_builtins_load_with_rules():
    presets, warns = load_presets(None)
    by_id = {p.wire_id: p for p in presets}
    assert "builtin:easier_combat" in by_id
    assert "builtin:harder_combat" in by_id
    qol = by_id["builtin:quality_of_life"]
    # Ja2.ini changes coerced to canon with advisory notes
    assert all(c.target == "canon" for c in qol.changes)
    assert qol.effect_timing == "relaunch"
    assert by_id["builtin:easier_combat"].effect_timing == "new_game"
    assert not by_id["builtin:easier_combat"].savegame_risk
    assert warns == []


def test_ai_ini_override_preset_is_apply_disabled(tmp_path: Path):
    root = make_vfs_install(tmp_path / "x")
    save_install_preset(root, {
        "id": "bad_ai", "name": "Bad AI",
        "changes": [{"ini_file": "AI.ini", "section": "Modularized Tactical AI",
                     "key": "NumFactories", "value": "12"}],
    })
    presets, _ = load_presets(root)
    bad = next(p for p in presets if p.wire_id == "install:bad_ai")
    assert bad.apply_disabled


def test_savegame_risk_flagged(tmp_path: Path):
    root = make_vfs_install(tmp_path / "x")
    save_install_preset(root, {
        "id": "risky", "name": "Risky",
        "changes": [{"ini_file": "Ja2_Options.ini", "section": "System Limit Settings",
                     "key": "MAX_NUMBER_PLAYER_MERCS", "value": "60"}],
    })
    presets, _ = load_presets(root)
    risky = next(p for p in presets if p.wire_id == "install:risky")
    assert risky.savegame_risk is True


def test_corrupt_install_file_skipped_not_fatal(tmp_path: Path):
    root = make_vfs_install(tmp_path / "x")
    install_preset_path(root).write_text("{not json", encoding="utf-8")
    presets, warns = load_presets(root)
    assert any("unreadable" in w for w in warns)
    assert any(p.source == "builtin" for p in presets)  # builtins survive


def test_install_preset_shadows_builtin(tmp_path: Path):
    root = make_vfs_install(tmp_path / "x")
    save_install_preset(root, {
        "id": "easier_combat", "name": "My easier",
        "changes": [{"ini_file": "Ja2_Options.ini", "section": "Tactical Difficulty Settings",
                     "key": "REGULAR_CTH_BONUS_PERCENT", "value": "-5"}],
    })
    presets, _ = load_presets(root)
    matches = [p for p in presets if p.id == "easier_combat"]
    assert len(matches) == 1 and matches[0].source == "install"


# -- routes ------------------------------------------------------------


def test_route_list_and_dry_run_has_current(client: TestClient, registered: dict):
    r = client.get("/api/v1/ini/presets")
    assert r.status_code == 200, r.text
    ids = [p["id"] for p in r.json()["presets"]]
    assert "builtin:easier_combat" in ids

    r2 = client.post("/api/v1/ini/presets/apply",
                     json={"id": "builtin:easier_combat", "dry_run": True})
    assert r2.status_code == 200, r2.text
    body = r2.json()
    assert body["dry_run"] is True
    changes = body["batches"][0]["files"][0]["changes"]
    assert all("current" in c for c in changes)


def test_route_apply_writes_override_and_backs_up(client: TestClient, registered: dict):
    r = client.post("/api/v1/ini/presets/apply",
                    json={"id": "builtin:easier_combat"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["applied"] == 6 and body["backup_id"]
    ovr = Path(registered["root"]) / "Profiles" / "UserProfile_JA2113" / "Ja2_Options.Override"
    assert ovr.is_file()
    assert b"REGULAR_CTH_BONUS_PERCENT = -10" in ovr.read_bytes()
    # canon untouched
    assert b"CTH_BONUS" not in (Path(registered["root"]) / "Data-1.13" / "Ja2_Options.ini").read_bytes()


def test_route_author_roundtrip_and_delete(client: TestClient, registered: dict):
    r = client.post("/api/v1/ini/presets", json={
        "name": "My tuning", "description": "test",
        "changes": [{"ini_file": "Ja2_Options.ini", "section": "Tactical Difficulty Settings",
                     "key": "REGULAR_CTH_BONUS_PERCENT", "value": "-5"}],
    })
    assert r.status_code == 200, r.text
    wire = r.json()["id"]
    assert wire == "install:my_tuning"
    assert (Path(registered["root"]) / "MercForgePresets.json").is_file()

    ids = [p["id"] for p in client.get("/api/v1/ini/presets").json()["presets"]]
    assert wire in ids

    assert client.delete("/api/v1/ini/presets/builtin:easier_combat").status_code == 403
    assert client.delete(f"/api/v1/ini/presets/{wire}").status_code == 200
    ids = [p["id"] for p in client.get("/api/v1/ini/presets").json()["presets"]]
    assert wire not in ids


# -- setup flow --------------------------------------------------------


def test_setup_state_engine_renderer(client: TestClient, registered: dict):
    r = client.get("/api/v1/setup/state")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["display"]["renderer"] == "engine"
    assert body["display"]["resolution"] == "5"
    assert body["offered"] is False


def test_setup_state_detects_cnc_ddraw(client: TestClient, registered: dict):
    root = Path(registered["root"])
    (root / "ddraw.dll").write_bytes(b"x")
    (root / "ddraw.ini").write_text(
        "[ddraw]\nwindowed=true\nfullscreen=false\ninject_resolution=1280x720\n")
    body = client.get("/api/v1/setup/state").json()
    assert body["display"]["renderer"] == "cnc-ddraw"
    assert body["display"]["windowed"] is True
    assert body["display"]["resolution"] == "1280x720"


def test_setup_apply_one_batch_engine(client: TestClient, registered: dict):
    payload = {
        "display": {"windowed": True, "resolution": "20"},
        "intro": {"play_intro": False, "tooltip_scale": 150},
        "preset_ids": ["builtin:easier_combat"],
    }
    dry = client.post("/api/v1/setup/apply", json={**payload, "dry_run": True})
    assert dry.status_code == 200, dry.text
    assert len(dry.json()["plan"]) >= 2

    r = client.post("/api/v1/setup/apply", json=payload)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["applied"] == 4 + 6 and body["backup_id"]
    ja2 = (Path(registered["root"]) / "Ja2.ini").read_bytes()
    assert b"SCREEN_RESOLUTION = 20" in ja2
    assert b"SCREEN_MODE_WINDOWED = 1" in ja2
    assert b"PLAY_INTRO = 0" in ja2


def test_setup_apply_ddraw_path(client: TestClient, registered: dict):
    root = Path(registered["root"])
    (root / "ddraw.dll").write_bytes(b"x")
    (root / "ddraw.ini").write_text(
        "; cnc-ddraw\n[ddraw]\nwindowed=false\nfullscreen=true\ninject_resolution=1024x768\nMY_KEY=1\n")
    r = client.post("/api/v1/setup/apply", json={
        "display": {"windowed": True, "resolution": "1920x1080"}})
    assert r.status_code == 200, r.text
    m = ie.parse_ini_map((root / "ddraw.ini").read_text())
    assert m["ddraw"]["windowed"] == "true"
    assert m["ddraw"]["fullscreen"] == "false"
    assert m["ddraw"]["inject_resolution"] == "1920x1080"
    assert m["ddraw"]["MY_KEY"] == "1"          # user key preserved
    # Ja2.ini resolution untouched on the ddraw path
    assert b"SCREEN_RESOLUTION = 5" in (root / "Ja2.ini").read_bytes()


def test_setup_offered_persists(client: TestClient, registered: dict):
    assert client.get("/api/v1/setup/state").json()["offered"] is False
    r = client.post("/api/v1/setup/offered")
    assert r.status_code == 200
    assert client.get("/api/v1/setup/state").json()["offered"] is True
