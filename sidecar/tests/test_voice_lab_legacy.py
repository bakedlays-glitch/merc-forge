"""Legacy voice HTTP routes are read-only after Voice Lab retirement."""
from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from main import create_app
from routes.state import get_state


@pytest.fixture(autouse=True)
def reset_state():
    state = get_state()
    state._installs = {}
    state._active_install_id = None
    state._scan_done = False
    yield
    state._installs = {}
    state._active_install_id = None
    state._scan_done = False


@pytest.fixture
def client() -> TestClient:
    return TestClient(create_app())


@pytest.fixture
def registered_install(client: TestClient, tmp_path: Path) -> dict:
    root = tmp_path / "fake_install"
    (root / "Data-1.13" / "TableData").mkdir(parents=True)
    (root / "JA2.exe").touch()
    (root / "Data-1.13" / "TableData" / "MercProfiles.xml").write_text(
        "<PROFILES />"
    )
    (root / "Data-1.13" / "TableData" / "AIMAvailability.xml").write_text(
        "<AIM_AVAILABLES />"
    )
    response = client.post("/api/v1/installs", json={"path": str(root)})
    assert response.status_code == 200, response.text
    info = response.json()
    active = client.post("/api/v1/installs/active", json={"install_id": info["id"]})
    assert active.status_code == 200, active.text
    return info


@pytest.fixture
def existing_clip(registered_install: dict) -> Path:
    clip = Path(registered_install["path"]) / "Data-1.13" / "Speech" / "108" / "108_001.ogg"
    clip.parent.mkdir(parents=True)
    clip.write_bytes(b"original-ogg-bytes")
    return clip


def _assert_voice_lab_required(response, slot: int) -> None:
    assert response.status_code == 409, response.text
    assert response.json() == {
        "detail": {
            "error": "VOICE_LAB_REQUIRED",
            "message": "Voice changes require preview and verified deployment in Voice Lab.",
            "voice_lab_path": f"/voice-lab?profile={slot}",
        }
    }


def test_legacy_get_list_remains_compatible(client, registered_install, existing_clip):
    response = client.get("/api/v1/voice/108")
    assert response.status_code == 200, response.text
    assert [clip["name"] for clip in response.json()["clips"]] == [existing_clip.name]


def test_legacy_upload_requires_voice_lab(client, registered_install, existing_clip):
    before = existing_clip.read_bytes()
    response = client.post(
        "/api/v1/voice/108/upload",
        files={"files": (existing_clip.name, b"replacement")},
    )
    _assert_voice_lab_required(response, 108)
    assert existing_clip.read_bytes() == before


def test_legacy_delete_requires_voice_lab(client, registered_install, existing_clip):
    response = client.delete(f"/api/v1/voice/108/{existing_clip.name}")
    _assert_voice_lab_required(response, 108)
    assert existing_clip.exists()


def test_legacy_delete_all_requires_voice_lab(client, registered_install, existing_clip):
    response = client.delete("/api/v1/voice/108")
    _assert_voice_lab_required(response, 108)
    assert existing_clip.exists()
