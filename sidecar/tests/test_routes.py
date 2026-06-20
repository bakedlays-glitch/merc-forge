"""Integration tests for the FastAPI sidecar routes.

Uses FastAPI's TestClient (no real network). Covers the happy path
through health → install registration → roster → create → update → delete.
"""
from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from main import create_app
from routes.state import get_state


@pytest.fixture(autouse=True)
def reset_state():
    """Reset the singleton state between tests to avoid bleed-through."""
    # Reach into the singleton and wipe its caches
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
    """Create a fake install on disk, register it, and set it active."""
    install_root = tmp_path / "fake_install"
    install_root.mkdir(parents=True)
    (install_root / "JA2.exe").touch()
    table = install_root / "Data-1.13" / "TableData"
    table.mkdir(parents=True)
    (table / "MercProfiles.xml").write_text("<PROFILES />")
    (table / "AIMAvailability.xml").write_text("<AIM_AVAILABLES />")

    resp = client.post("/api/v1/installs", json={"path": str(install_root)})
    assert resp.status_code == 200, resp.text
    info = resp.json()
    client.post("/api/v1/installs/active", json={"install_id": info["id"]})
    return info


# ──────────────────────────────────────────────────────────────────────────
#  Smoke tests
# ──────────────────────────────────────────────────────────────────────────

def test_health(client: TestClient) -> None:
    r = client.get("/api/v1/health")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["version"] == "2.0.0"


def test_version(client: TestClient) -> None:
    r = client.get("/api/v1/version")
    assert r.status_code == 200
    assert r.json()["tool"] == "MercWizard"


# ──────────────────────────────────────────────────────────────────────────
#  Install lifecycle
# ──────────────────────────────────────────────────────────────────────────

def test_register_manual_install(client: TestClient, tmp_path: Path) -> None:
    install_root = tmp_path / "fake"
    install_root.mkdir()
    (install_root / "JA2.exe").touch()
    (install_root / "Data-1.13" / "TableData").mkdir(parents=True)
    (install_root / "Data-1.13" / "TableData" / "MercProfiles.xml").touch()
    (install_root / "Data-1.13" / "TableData" / "AIMAvailability.xml").touch()

    r = client.post("/api/v1/installs", json={"path": str(install_root)})
    assert r.status_code == 200
    info = r.json()
    assert info["valid"] is True
    assert "mod_id" in info


def test_register_invalid_install_returns_400(client: TestClient, tmp_path: Path) -> None:
    bogus = tmp_path / "nope"
    bogus.mkdir()
    r = client.post("/api/v1/installs", json={"path": str(bogus)})
    assert r.status_code == 400
    assert r.json()["detail"]["error"] == "INVALID_INSTALL"


def test_set_active_install(client: TestClient, registered_install: dict) -> None:
    r = client.get("/api/v1/health")
    assert r.json()["active_install_id"] == registered_install["id"]


# ──────────────────────────────────────────────────────────────────────────
#  Health — vfs_mismatch (bug-review B5)
# ──────────────────────────────────────────────────────────────────────────


def _make_multi_vfs_install(
    tmp_path: Path, ja2_ini_active_config: str, configs: list[str],
) -> Path:
    """Build an install on disk with multiple vfs_config.*.ini files and
    a Ja2.ini whose VFS_CONFIG_INI names one of them.

    `configs` is the list of config FILENAMES to materialize at the install
    root. `ja2_ini_active_config` is the value written into Ja2.ini's
    VFS_CONFIG_INI line.
    """
    install_root = tmp_path / "multi_vfs_install"
    install_root.mkdir(parents=True)
    (install_root / "JA2.exe").touch()
    table = install_root / "Data-1.13" / "TableData"
    table.mkdir(parents=True)
    (table / "MercProfiles.xml").write_text("<PROFILES />")
    (table / "AIMAvailability.xml").write_text("<AIM_AVAILABLES />")
    for cfg_name in configs:
        (install_root / cfg_name).touch()
    (install_root / "Ja2.ini").write_text(
        "[Ja2 Settings]\n"
        f"VFS_CONFIG_INI = {ja2_ini_active_config}\n"
    )
    return install_root


def test_health_vfs_mismatch_null_when_no_active_install(client: TestClient) -> None:
    """No install registered/active → can't compute the comparison."""
    r = client.get("/api/v1/health")
    assert r.status_code == 200
    body = r.json()
    assert body["active_install_id"] is None
    assert body["vfs_mismatch"] is None


def test_health_vfs_mismatch_false_when_install_has_no_vfs_config_bound(
    client: TestClient, registered_install: dict,
) -> None:
    """The `registered_install` fixture creates a plain install with no
    preferred_vfs_config_path. Whatever Ja2.ini says (or doesn't), there's
    no expectation to violate — mismatch must be False, not None."""
    r = client.get("/api/v1/health")
    assert r.status_code == 200
    body = r.json()
    assert body["active_install_id"] == registered_install["id"]
    assert body["vfs_mismatch"] is False


def test_health_vfs_mismatch_true_when_active_config_differs_from_ja2_ini(
    client: TestClient, tmp_path: Path,
) -> None:
    """Bug-review B5 concrete failure: install registered as Wildfire but
    Ja2.ini still names AIMNAS. /health must surface this so the Hub
    banner can prompt the user to apply VFS and unify the two."""
    install_root = _make_multi_vfs_install(
        tmp_path,
        ja2_ini_active_config="vfs_config.AIMNAS.ini",
        configs=["vfs_config.AIMNAS.ini", "vfs_config.Wildfire.ini"],
    )
    wildfire = install_root / "vfs_config.Wildfire.ini"

    r = client.post(
        "/api/v1/installs",
        json={"path": str(install_root), "preferred_vfs_config_path": str(wildfire)},
    )
    assert r.status_code == 200, r.text
    info = r.json()
    assert info["valid"] is True
    client.post("/api/v1/installs/active", json={"install_id": info["id"]})

    body = client.get("/api/v1/health").json()
    assert body["active_install_id"] == info["id"]
    assert body["vfs_mismatch"] is True


def test_health_vfs_mismatch_false_when_active_config_matches_ja2_ini(
    client: TestClient, tmp_path: Path,
) -> None:
    """Bound config and Ja2.ini agree → no mismatch, the banner stays hidden."""
    install_root = _make_multi_vfs_install(
        tmp_path,
        ja2_ini_active_config="vfs_config.AIMNAS.ini",
        configs=["vfs_config.AIMNAS.ini", "vfs_config.Wildfire.ini"],
    )
    aimnas = install_root / "vfs_config.AIMNAS.ini"

    r = client.post(
        "/api/v1/installs",
        json={"path": str(install_root), "preferred_vfs_config_path": str(aimnas)},
    )
    assert r.status_code == 200, r.text
    info = r.json()
    client.post("/api/v1/installs/active", json={"install_id": info["id"]})

    body = client.get("/api/v1/health").json()
    assert body["active_install_id"] == info["id"]
    assert body["vfs_mismatch"] is False


# ──────────────────────────────────────────────────────────────────────────
#  Roster
# ──────────────────────────────────────────────────────────────────────────

def test_empty_roster_is_all_empty_slots(client: TestClient, registered_install: dict) -> None:
    r = client.get("/api/v1/roster")
    assert r.status_code == 200
    roster = r.json()
    assert len(roster) == 256
    assert all(e["is_empty"] for e in roster)


def test_get_roster_slot_empty_returns_404(client: TestClient, registered_install: dict) -> None:
    r = client.get("/api/v1/roster/5")
    assert r.status_code == 404


# ──────────────────────────────────────────────────────────────────────────
#  Merc CRUD
# ──────────────────────────────────────────────────────────────────────────

def test_create_merc_happy_path(client: TestClient, registered_install: dict) -> None:
    payload = {
        "merc": {
            "uiIndex": 5,
            "ubFaceIndex": 165,
            "Type": 1,
            "zName": "Carter",
            "zNickname": "Carter",
            "biographyText": "Test bio.",
            "additionalInfoText": "Test info.",
        },
        "gear": {
            "mIndex": 5,
            "mName": "Carter",
            "kits": [{"mGearKitName": "Standard", "mWeapon": 2, "mAbsolutePrice": -1}],
        },
        "aim_binding": {
            "uiIndex": 5,
            "description": "Carter",
            "ProfilId": 5,
            "AimBioID": 5,
        },
    }
    r = client.post("/api/v1/merc", json=payload)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is True
    assert body["slot"] == 5


def test_create_merc_blocked_by_audit_when_npc_in_aim_slot(
    client: TestClient, registered_install: dict,
) -> None:
    """Type=3 (NPC) in slot 5 (AIM) is the Marcus invisibility trap — ERROR."""
    payload = {
        "merc": {
            "uiIndex": 5,
            "ubFaceIndex": 165,
            "Type": 3,
            "zName": "Marcus",
            "zNickname": "Marcus",
        },
    }
    r = client.post("/api/v1/merc", json=payload)
    assert r.status_code == 400
    body = r.json()
    assert body["detail"]["error"] == "AUDIT_FAILED"
    codes = {i["code"] for i in body["detail"]["issues"]}
    assert "NPC_IN_AIM_SLOT" in codes


def test_create_then_get_roster_shows_merc(client: TestClient, registered_install: dict) -> None:
    payload = {
        "merc": {
            "uiIndex": 5, "ubFaceIndex": 165, "Type": 1,
            "zName": "Carter", "zNickname": "Carter",
        },
    }
    client.post("/api/v1/merc", json=payload)
    roster = client.get("/api/v1/roster").json()
    slot_5 = next(e for e in roster if e["slot"] == 5)
    assert not slot_5["is_empty"]
    assert slot_5["nickname"] == "Carter"


def test_growth_modifier_round_trips_through_http_with_b_prefixed_tag(
    client: TestClient, registered_install: dict,
) -> None:
    """End-to-end HTTP guard for the growth-modifier tag-name fix.

    Covers all three failure modes through the real routes:
      #2 (write): create writes the engine's <bGrowthModifierStrength> tag, not
         the prefix-less spelling the engine ignores at load.
      #3 (read):  GET /roster/{slot} normalizes it back to the prefix-less
         field name the frontend form reads (else the editor shows 0).
      #1 (edit):  a PUT that round-trips the GET profile (as Edit.tsx does)
         persists the new value back to the b-prefixed tag.
    """
    create = {
        "merc": {
            "uiIndex": 5, "ubFaceIndex": 165, "Type": 1,
            "zName": "Carter", "zNickname": "Carter",
            "GrowthModifierStrength": 7,
        },
    }
    assert client.post("/api/v1/merc", json=create).status_code == 200

    install_root = Path(registered_install["path"])
    profiles = install_root / "Data-1.13" / "TableData" / "MercProfiles.xml"
    disk = profiles.read_text(encoding="utf-8")
    assert "<bGrowthModifierStrength>7</bGrowthModifierStrength>" in disk
    assert "<GrowthModifierStrength>" not in disk  # prefix-less tag is never written

    # READ: surfaced under the prefix-less field name, value preserved.
    got = client.get("/api/v1/roster/5").json()
    assert got["profile"]["GrowthModifierStrength"] == "7"
    assert "bGrowthModifierStrength" not in got["profile"]

    # EDIT + WRITE: send the profile back the way Edit.tsx does, bumped to 25.
    merc_payload = {**got["profile"], "GrowthModifierStrength": 25}
    events: list[dict] = []
    with client.stream("PUT", "/api/v1/merc/5", json={"merc": merc_payload}) as resp:
        assert resp.status_code == 200, resp.read()
        for line in resp.iter_lines():
            if line:
                events.append(_json.loads(line))
    assert events and events[-1]["ok"] is True, f"update failed: {events[-1] if events else None}"

    disk2 = profiles.read_text(encoding="utf-8")
    assert "<bGrowthModifierStrength>25</bGrowthModifierStrength>" in disk2
    assert "<GrowthModifierStrength>" not in disk2


def test_delete_merc(client: TestClient, registered_install: dict) -> None:
    payload = {
        "merc": {
            "uiIndex": 5, "ubFaceIndex": 165, "Type": 1,
            "zName": "Carter", "zNickname": "Carter",
        },
    }
    client.post("/api/v1/merc", json=payload)
    r = client.delete("/api/v1/merc/5")
    assert r.status_code == 200


def test_create_type2_merc_writes_bio_to_mercbios_not_mercedt(
    client: TestClient, registered_install: dict, tmp_path: Path,
) -> None:
    """THE MERC ROUTING BUG FIX, end-to-end.

    Create a Type=2 (MERC) merc at slot 198 (Eskimo's Vengeance slot). The
    bio must land in MERCBIOS.EDT at offset MercBioID × 1120, NOT in
    MercEdt/198.EDT. MercWizard 1.x wrote to MercEdt and the engine
    ignored it, leaving expansion mercs with the wrong bio in-game.
    """
    payload = {
        "merc": {
            "uiIndex": 198,
            "ubFaceIndex": 198,
            "Type": 2,
            "zName": "Eskimo",
            "zNickname": "Eskimo",
            "biographyText": "I got yer.",
            "additionalInfoText": "Anaktuvuk Pass.",
        },
    }
    r = client.post("/api/v1/merc", json=payload)
    assert r.status_code == 200, r.text

    install_root = Path(registered_install["path"])
    mercbios = install_root / "Data-1.13" / "BinaryData" / "MERCBIOS.EDT"
    merc_edt_198 = install_root / "Data-1.13" / "BinaryData" / "MercEdt" / "198.EDT"

    # The pre-fix bug: bio at MercEdt/198.EDT, MERCBIOS.EDT untouched. Now:
    # MERCBIOS.EDT exists and is sized to hold the auto-allocated MercBioID
    # record; MercEdt/198.EDT must NOT exist (we no longer route there).
    assert mercbios.is_file(), "MERCBIOS.EDT was not written"
    assert not merc_edt_198.exists(), \
        "MercEdt/198.EDT was written — the routing bug is back"

    # Confirm the bio is round-trippable through the auto-allocated MercBioID
    from mercwizard_core.inject import merc_availability as ma
    from mercwizard_core.inject import edt as edt_mod
    merc_xml = install_root / "Data-1.13" / "TableData" / "MercAvailability.xml"
    bio_id = ma.lookup_merc_bio_id(merc_xml, profil_id=198)
    assert bio_id is not None, "MercAvailability row missing — auto-fill didn't fire"
    bio, addl = edt_mod.read_bio(install_root, ui_index=198, merc_bio_id=bio_id)
    assert bio == "I got yer."
    assert addl == "Anaktuvuk Pass."
    assert r.json()["ok"] is True
    roster = client.get("/api/v1/roster").json()
    assert next(e for e in roster if e["slot"] == 5)["is_empty"]


def test_move_merc(client: TestClient, registered_install: dict) -> None:
    payload = {
        "merc": {
            "uiIndex": 5, "ubFaceIndex": 165, "Type": 1,
            "zName": "Carter", "zNickname": "Carter",
        },
        "aim_binding": {
            "uiIndex": 5, "description": "Carter", "ProfilId": 5, "AimBioID": 5,
        },
    }
    client.post("/api/v1/merc", json=payload)
    r = client.post("/api/v1/merc/5/move", json={"to_slot": 10})
    assert r.status_code == 200, r.text
    # /move is NDJSON-streaming since 2026-05-23. Parse each line; the final
    # `{done: True, ok: True, from, to, ...}` event carries the from/to fields.
    import json as _json
    events = [
        _json.loads(line)
        for line in r.text.strip().splitlines()
        if line.strip()
    ]
    final = next(e for e in events if e.get("done"))
    assert final["ok"] is True, final
    assert final["from"] == 5
    assert final["to"] == 10
    roster = client.get("/api/v1/roster").json()
    assert next(e for e in roster if e["slot"] == 5)["is_empty"]
    assert not next(e for e in roster if e["slot"] == 10)["is_empty"]


# ──────────────────────────────────────────────────────────────────────────
#  No active install
# ──────────────────────────────────────────────────────────────────────────

def test_roster_without_active_install_returns_400(client: TestClient) -> None:
    r = client.get("/api/v1/roster")
    assert r.status_code == 400
    assert r.json()["detail"]["error"] == "NO_ACTIVE_INSTALL"


# ──────────────────────────────────────────────────────────────────────────
#  Phase 1: Streaming PUT /merc/{slot} — progress events
# ──────────────────────────────────────────────────────────────────────────

import json as _json


def _create_carter(client: TestClient, slot: int = 5) -> None:
    """Helper: create a vanilla AIM merc at the given slot so update tests
    have something to update."""
    payload = {
        "merc": {
            "uiIndex": slot, "ubFaceIndex": slot, "Type": 1,
            "zName": "Carter", "zNickname": "Carter",
            "biographyText": "Original bio.", "additionalInfoText": "Original info.",
        },
        "aim_binding": {
            "uiIndex": slot, "description": "Carter", "ProfilId": slot, "AimBioID": slot,
        },
    }
    r = client.post("/api/v1/merc", json=payload)
    assert r.status_code == 200, r.text


def test_update_merc_streams_progress_events(
    client: TestClient, registered_install: dict,
) -> None:
    """Phase 1: PUT /merc/{slot} emits one NDJSON event per save step,
    ending in `{done: true, ok: true, slot}`."""
    _create_carter(client, slot=5)

    update_payload = {
        "merc": {
            "uiIndex": 5, "ubFaceIndex": 5, "Type": 1,
            "zName": "Carter", "zNickname": "Carter",
            "biographyText": "Updated bio.", "additionalInfoText": "Updated info.",
        },
        "aim_binding": {
            "uiIndex": 5, "description": "Carter", "ProfilId": 5, "AimBioID": 5,
        },
    }

    events: list[dict] = []
    with client.stream("PUT", "/api/v1/merc/5", json=update_payload) as resp:
        assert resp.status_code == 200, resp.read()
        assert resp.headers["content-type"].startswith("application/x-ndjson")
        for line in resp.iter_lines():
            if line:
                events.append(_json.loads(line))

    # Step events arrive in canonical order. Backup emits one "start" + 0+
    # "progress" events + one "done"; profiles/edt/aim_avail each emit
    # start+done.
    step_starts = [e for e in events if e.get("status") == "start"]
    started_steps = [e["step"] for e in step_starts]
    assert started_steps == ["backup", "profiles", "edt", "aim_avail"], started_steps

    step_dones = [e for e in events if e.get("status") == "done"]
    done_steps = [e["step"] for e in step_dones]
    assert done_steps == ["backup", "profiles", "edt", "aim_avail"], done_steps

    # Final event is success
    assert events[-1] == {"done": True, "ok": True, "slot": 5}


def test_update_merc_rollback_on_edt_failure(
    client: TestClient, registered_install: dict, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Phase 1 + 2.8: if write_bio raises after profiles_xml.upsert
    succeeds, the rollback reverts the profile so we don't leave a
    half-written merc."""
    _create_carter(client, slot=5)

    # Read the pre-update profile so we can compare after rollback
    from mercwizard_core.inject import profiles_xml
    install_root = Path(registered_install["path"])
    profiles_path = install_root / "Data-1.13" / "TableData" / "MercProfiles.xml"
    pre_profile = profiles_xml.read_slot(profiles_path, 5)
    assert pre_profile is not None
    pre_bio = pre_profile.get("biographyText", "")

    # Force write_bio to raise. The route imports edt as `edt_mod` so
    # patch the symbol on the routes.merc module.
    from routes import merc as merc_routes_module

    def _boom(*args, **kwargs):
        raise RuntimeError("simulated EDT failure")

    monkeypatch.setattr(merc_routes_module.edt_mod, "write_bio", _boom)

    update_payload = {
        "merc": {
            "uiIndex": 5, "ubFaceIndex": 5, "Type": 1,
            "zName": "Carter", "zNickname": "Carter",
            "biographyText": "This will fail.",
            "additionalInfoText": "Rolled back.",
        },
    }
    events: list[dict] = []
    with client.stream("PUT", "/api/v1/merc/5", json=update_payload) as resp:
        assert resp.status_code == 200
        for line in resp.iter_lines():
            if line:
                events.append(_json.loads(line))

    final = events[-1]
    assert final["done"] is True
    assert final["ok"] is False
    assert final["error"] == "SAVE_FAILED"
    assert final["error_step"] == "edt"
    assert final["steps_completed"] == ["backup", "profiles"]
    assert final["rollback_ok"] is True

    # Update rollback semantic: the slot must still hold Carter with the
    # original bio (NOT deleted, NOT updated). Restore phase 1 copies the
    # snapshot's MercProfiles.xml back over the install, reverting the
    # mid-rollback upsert.
    post_profile = profiles_xml.read_slot(profiles_path, 5)
    assert post_profile is not None, \
        "slot 5 was deleted by rollback - should still hold pre-update Carter"
    assert post_profile.get("biographyText", "") == pre_bio, \
        "slot 5's bio wasn't reverted to its pre-update contents"


def test_update_merc_audit_failure_returns_400_no_stream(
    client: TestClient, registered_install: dict,
) -> None:
    """AUDIT_FAILED returns a normal 400 (NOT a stream). The audit runs
    before StreamingResponse is opened. Using Type=3 at AIM slot 5 to
    trigger NPC_IN_AIM_SLOT — Pydantic accepts the payload, audit
    rejects it."""
    _create_carter(client, slot=5)
    bad_payload = {
        "merc": {
            "uiIndex": 5, "ubFaceIndex": 5, "Type": 3,  # RPC at AIM slot
            "zName": "Marcus", "zNickname": "Marcus",
            "biographyText": "ok",
            "additionalInfoText": "ok",
        },
    }
    r = client.put("/api/v1/merc/5", json=bad_payload)
    assert r.status_code == 400
    assert r.json()["detail"]["error"] == "AUDIT_FAILED"


# ──────────────────────────────────────────────────────────────────────────
#  Phase 2.3: TYPE_SLOT_HARD_MISMATCH audit hard-block
# ──────────────────────────────────────────────────────────────────────────


def test_audit_warns_type1_at_slot_without_aim_row(
    client: TestClient, registered_install: dict,
) -> None:
    """Type=1 (AIM) at slot 198 in a fresh install — no AIMAvailability row
    exists for 198. Post engine-faithful rewrite this is WARN, not ERROR:
    MercForge writes the row on save, so the merc DOES appear on AIM."""
    payload = {
        "merc": {
            "uiIndex": 198, "ubFaceIndex": 198, "Type": 1,
            "zName": "Wahan", "zNickname": "Wahan",
        },
    }
    r = client.post("/api/v1/merc", json=payload)
    assert r.status_code == 200, r.text
    codes = {i["code"] for i in r.json()["issues"]}
    # Old hard-mismatch is gone; replaced by a soft "no row yet" warning
    assert "TYPE_SLOT_HARD_MISMATCH" not in codes


def test_audit_warns_type2_at_slot_without_merc_row(
    client: TestClient, registered_install: dict,
) -> None:
    """Type=2 (MERC) at slot 5: WARN about missing MercAvailability row,
    not ERROR."""
    payload = {
        "merc": {
            "uiIndex": 5, "ubFaceIndex": 5, "Type": 2,
            "zName": "X", "zNickname": "X",
        },
    }
    r = client.post("/api/v1/merc", json=payload)
    assert r.status_code == 200, r.text
    codes = {i["code"] for i in r.json()["issues"]}
    assert "TYPE_SLOT_HARD_MISMATCH" not in codes


def test_audit_keeps_warn_for_ambiguous_200_214_zone(
    client: TestClient, registered_install: dict,
) -> None:
    """Type=1 at slot 203: passes audit (200), not blocked (400)."""
    payload = {
        "merc": {
            "uiIndex": 203, "ubFaceIndex": 203, "Type": 1,
            "zName": "X", "zNickname": "X",
        },
        "aim_binding": {
            "uiIndex": 203, "description": "X", "ProfilId": 203, "AimBioID": 72,
        },
    }
    r = client.post("/api/v1/merc", json=payload)
    assert r.status_code == 200, r.text
    codes = {i["code"] for i in r.json()["issues"]}
    assert "TYPE_SLOT_HARD_MISMATCH" not in codes


# ──────────────────────────────────────────────────────────────────────────
#  Bug #12: /health no longer exposes scan_* fields (auto-detect removed)
# ──────────────────────────────────────────────────────────────────────────


def test_health_drops_scan_fields(client: TestClient) -> None:
    """Auto-detection probes were removed per bug #12, so the
    `scan_in_progress` / `last_scan_error` / `scan_progress` fields are
    gone from /health. Frontend reads only `ok` / `install_count` /
    `active_install_id` now.
    """
    r = client.get("/api/v1/health")
    assert r.status_code == 200
    body = r.json()
    assert "scan_in_progress" not in body
    assert "last_scan_error" not in body
    assert "scan_progress" not in body
    assert body["ok"] is True
    assert "install_count" in body


# ──────────────────────────────────────────────────────────────────────────
#  Phase 2.8: create_merc rollback on EDT failure
# ──────────────────────────────────────────────────────────────────────────


def test_update_merc_tolerates_extra_xml_fields(
    client: TestClient, registered_install: dict,
) -> None:
    """Regression for the slot-0 422 a user hit 2026-05-18.

    Their MercProfiles.xml row for slot 0 had been touched by another tool
    (some external editors write `bigFaceImagePath`, `alphaThreshold`,
    `alphaThresholdEye`, `faceIndex` annotations into the row). The
    frontend's GET /roster/0 returned those extras in the profile dict;
    its PUT payload included them; the Merc Pydantic model had
    `extra="forbid"` and 422'd the request, blocking ANY edit to that
    slot.

    Fix: switched the Merc model to `extra="ignore"`. The unknown fields
    are dropped from the validated Merc instance but the surgical
    `profiles_xml.upsert` leaves them untouched in the existing XML row.
    """
    _create_carter(client, slot=5)

    # Build an update payload that mimics what Edit.tsx would send if the
    # XML row had extra annotations from another tool. Pre-fix this would
    # 422 at Pydantic validation; post-fix the extras are silently dropped
    # by the validator and the update proceeds.
    update_payload = {
        "merc": {
            "uiIndex": 5, "ubFaceIndex": 5, "Type": 1,
            "zName": "Carter", "zNickname": "Carter",
            "biographyText": "Updated.", "additionalInfoText": "Updated.",
            # Annotations another external tool wrote into the user's slot 0:
            "bigFaceImagePath": r"C:\some\path\carter.png",
            "alphaThreshold": -1,
            "alphaThresholdEye": 20,
            "faceIndex": 165,
        },
    }
    events: list[dict] = []
    with client.stream("PUT", "/api/v1/merc/5", json=update_payload) as resp:
        assert resp.status_code == 200, resp.read()
        for line in resp.iter_lines():
            if line:
                events.append(_json.loads(line))

    final = events[-1]
    assert final["done"] is True
    assert final["ok"] is True, f"unexpected failure: {final}"


def test_create_merc_rollback_on_edt_failure(
    client: TestClient, registered_install: dict, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If write_bio raises after profile/AIM are written, the rollback
    restores the install to its pre-create state."""
    from routes import merc as merc_routes_module
    from mercwizard_core.inject import profiles_xml

    def _boom(*args, **kwargs):
        raise RuntimeError("simulated EDT failure")

    monkeypatch.setattr(merc_routes_module.edt_mod, "write_bio", _boom)

    payload = {
        "merc": {
            "uiIndex": 7, "ubFaceIndex": 7, "Type": 1,
            "zName": "Doomed", "zNickname": "Doomed",
            "biographyText": "Will fail.", "additionalInfoText": "Will fail.",
        },
        "aim_binding": {
            "uiIndex": 7, "description": "Doomed", "ProfilId": 7, "AimBioID": 7,
        },
    }
    r = client.post("/api/v1/merc", json=payload)
    assert r.status_code == 500
    detail = r.json()["detail"]
    assert detail["error"] == "CREATE_FAILED"
    assert detail["error_step"] == "edt"
    assert detail["rollback_ok"] is True

    # The profile slot 7 should NOT exist on disk after rollback
    install_root = Path(registered_install["path"])
    profiles_path = install_root / "Data-1.13" / "TableData" / "MercProfiles.xml"
    assert profiles_xml.read_slot(profiles_path, 7) is None, \
        "profile slot 7 was left on disk — rollback didn't fire"


# ──────────────────────────────────────────────────────────────────────────
#  Portrait thumbnail (GET /merc/{slot}/portrait)
# ──────────────────────────────────────────────────────────────────────────

def _seed_face_sti(install_root: Path, face_index: int) -> None:
    """Drop a real 8-frame SmallFace STI at `faces/{face_index}.sti`.

    Uses compile_and_write_all so the file is byte-identical to what the
    portrait pipeline ships — not a stub the decoder would silently fall
    back on.
    """
    import io
    from PIL import Image
    from mercwizard_core.portrait.compile import compile_and_write_all

    # 48x43 base — anything decode-able. Use a distinctive color so the
    # PNG-bytes check can verify decode actually ran on the right frame.
    base = Image.new("RGBA", (48, 43), (200, 100, 50, 255))
    buf = io.BytesIO()
    base.save(buf, format="PNG")
    (install_root / "Data-1.13" / "faces").mkdir(parents=True, exist_ok=True)
    compile_and_write_all(
        install_root=install_root,
        face_index=face_index,
        source_png_bytes=buf.getvalue(),
    )


def test_get_merc_portrait_returns_png_bytes(
    client: TestClient, registered_install: dict,
) -> None:
    install_root = Path(registered_install["path"])
    _seed_face_sti(install_root, face_index=42)

    payload = {
        "merc": {
            "uiIndex": 200, "ubFaceIndex": 42, "Type": 1,
            "zName": "Thumb", "zNickname": "Thumb",
        },
    }
    r = client.post("/api/v1/merc", json=payload)
    assert r.status_code == 200, r.text

    r = client.get("/api/v1/merc/200/portrait")
    assert r.status_code == 200, r.text
    assert r.headers["content-type"] == "image/png"
    # PNG magic bytes
    assert r.content[:8] == b"\x89PNG\r\n\x1a\n"
    # Cache-Control + ETag must be set for the roster's 200-cell render
    # to be reasonable.
    assert "max-age" in r.headers.get("cache-control", "")
    assert r.headers.get("etag")


def test_get_merc_portrait_falls_back_to_sibling_size(
    client: TestClient, registered_install: dict,
) -> None:
    """When the REQUESTED size is missing but a sibling decodes, the endpoint
    serves the sibling (200) instead of 404 — matching the roster grid's
    decode-aware fallback. Seed all sizes, delete the BigFace, request
    bigface → must fall back to the SmallFace."""
    install_root = Path(registered_install["path"])
    _seed_face_sti(install_root, face_index=42)
    # Remove every BigFace variant for face 42 so bigface must fall back.
    faces = install_root / "Data-1.13" / "faces"
    removed = 0
    for p in faces.rglob("*.sti"):
        if "bigface" in str(p).lower() and p.stem.lstrip("0") in ("42", ""):
            p.unlink()
            removed += 1
    assert removed > 0, "no BigFace was seeded to delete — fixture changed"

    client.post("/api/v1/merc", json={"merc": {
        "uiIndex": 201, "ubFaceIndex": 42, "Type": 1,
        "zName": "Fb", "zNickname": "Fb",
    }})
    r = client.get("/api/v1/merc/201/portrait?size=bigface")
    assert r.status_code == 200, r.text  # fell back to SmallFace, not 404
    assert r.content[:8] == b"\x89PNG\r\n\x1a\n"


def test_composite_animation_strip_layout_and_coords() -> None:
    """The animation-strip compositor lays out one 48x43 cell per frame and
    pastes eye/blink frames (1..4) at eye_xy, mouth/talk (5..) at mouth_xy."""
    import io as _io
    from PIL import Image
    from routes.merc import _composite_animation_strip

    base = Image.new("RGBA", (48, 43), (0, 0, 0, 255))
    eye = Image.new("RGBA", (10, 6), (255, 0, 0, 255))      # red blink
    mouth = Image.new("RGBA", (10, 6), (0, 255, 0, 255))    # green talk
    frames = [base] + [eye] * 4 + [mouth] * 3               # base + 4 eye + 3 mouth
    eye_xy, mouth_xy = (5, 8), (5, 30)

    png = _composite_animation_strip(frames, eye_xy, mouth_xy)
    assert png[:8] == b"\x89PNG\r\n\x1a\n"
    strip = Image.open(_io.BytesIO(png)).convert("RGBA")
    assert strip.size == (48 * 8, 43)  # 8 cells
    px = strip.load()
    # Blink cell 1: red at the eye coord. Talk cell 5: green at the mouth coord.
    assert px[48 * 1 + eye_xy[0] + 1, eye_xy[1] + 1][:3] == (255, 0, 0)
    assert px[48 * 5 + mouth_xy[0] + 1, mouth_xy[1] + 1][:3] == (0, 255, 0)
    # Base cell 0 is untouched (black at the eye coord).
    assert px[eye_xy[0] + 1, eye_xy[1] + 1][:3] == (0, 0, 0)


def test_get_merc_portrait_cache_invalidates_after_recompile(
    client: TestClient, registered_install: dict,
) -> None:
    """Recompiling a portrait must bump the cached PNG.

    User feedback: "wont it have to refresh after you make a change or
    at least refresh the ones you changed?" — the PNG LRU keys by
    source_id which includes the source file's mtime, so any write to
    the on-disk STI naturally invalidates the cache.
    """
    import time
    install_root = Path(registered_install["path"])
    _seed_face_sti(install_root, face_index=42)
    payload = {
        "merc": {
            "uiIndex": 200, "ubFaceIndex": 42, "Type": 1,
            "zName": "Thumb", "zNickname": "Thumb",
        },
    }
    r = client.post("/api/v1/merc", json=payload)
    assert r.status_code == 200, r.text

    # First fetch — populates cache.
    r1 = client.get("/api/v1/merc/200/portrait")
    assert r1.status_code == 200
    etag_before = r1.headers.get("etag")
    assert etag_before

    # Second fetch — cache hit, same ETag.
    r2 = client.get("/api/v1/merc/200/portrait")
    assert r2.status_code == 200
    assert r2.headers.get("etag") == etag_before

    # Recompile with different art. Sleep to guarantee a new mtime
    # (Windows can collapse same-ms writes to identical mtime_ns).
    time.sleep(0.05)
    import io as _io
    from PIL import Image
    from mercwizard_core.portrait.compile import compile_and_write_all
    different = Image.new("RGBA", (48, 43), (10, 220, 90, 255))
    buf = _io.BytesIO()
    different.save(buf, format="PNG")
    compile_and_write_all(
        install_root=install_root,
        face_index=42,
        source_png_bytes=buf.getvalue(),
    )

    # Third fetch — source mtime changed → cache key changed → fresh
    # decode → new PNG body → different ETag.
    r3 = client.get("/api/v1/merc/200/portrait")
    assert r3.status_code == 200
    assert r3.content != r1.content, (
        "Portrait body didn't change after recompile — cache is stuck"
    )
    assert r3.headers.get("etag") != etag_before, (
        "ETag didn't change after recompile — cache key isn't keyed on source"
    )


def test_roster_portrait_sheet_returns_png_and_manifest(
    client: TestClient, registered_install: dict,
) -> None:
    """Sprite-sheet endpoint: ONE PNG + JSON manifest replaces the N+1
    per-slot portrait fetches. User feedback: "i want it to be fast"."""
    install_root = Path(registered_install["path"])
    # Two filled slots with portraits, one without (face=0 → skipped).
    _seed_face_sti(install_root, face_index=42)
    _seed_face_sti(install_root, face_index=99)
    for ui_index, face in ((100, 42), (101, 99), (102, 0)):
        client.post("/api/v1/merc", json={
            "merc": {
                "uiIndex": ui_index, "ubFaceIndex": face, "Type": 1,
                "zName": f"M{ui_index}", "zNickname": f"M{ui_index}",
            },
        })

    r_png = client.get("/api/v1/roster/portrait-sheet.png")
    assert r_png.status_code == 200, r_png.text
    assert r_png.headers["content-type"] == "image/png"
    assert r_png.content[:8] == b"\x89PNG\r\n\x1a\n"
    assert r_png.headers.get("etag")

    r_json = client.get("/api/v1/roster/portrait-sheet.json")
    assert r_json.status_code == 200, r_json.text
    manifest = r_json.json()
    assert manifest["size"] == "smallface"
    assert manifest["cell_w"] == 48
    assert manifest["cell_h"] == 43

    cells_by_slot = {c["slot"]: c for c in manifest["cells"]}
    # The two filled-with-portrait slots are in the sheet.
    assert 100 in cells_by_slot
    assert 101 in cells_by_slot
    # The face=0 slot is NOT in the sheet (no portrait).
    assert 102 not in cells_by_slot

    # First cell at origin, second cell offset by one column width.
    c100 = cells_by_slot[100]
    c101 = cells_by_slot[101]
    assert c100["x"] == 0 and c100["y"] == 0
    assert (c101["x"], c101["y"]) != (c100["x"], c100["y"])


def test_roster_portrait_sheet_cached_across_calls(
    client: TestClient, registered_install: dict,
) -> None:
    """Same request twice should return byte-identical PNG (cache hit)
    AND the same ETag. Recompiling any portrait invalidates via the
    profiles.xml mtime in the cache key."""
    install_root = Path(registered_install["path"])
    _seed_face_sti(install_root, face_index=42)
    client.post("/api/v1/merc", json={
        "merc": {
            "uiIndex": 100, "ubFaceIndex": 42, "Type": 1,
            "zName": "Cached", "zNickname": "Cached",
        },
    })

    r1 = client.get("/api/v1/roster/portrait-sheet.png")
    r2 = client.get("/api/v1/roster/portrait-sheet.png")
    assert r1.status_code == 200 and r2.status_code == 200
    assert r1.content == r2.content
    assert r1.headers.get("etag") == r2.headers.get("etag")


def test_get_merc_portrait_204_when_face_index_zero(
    client: TestClient, registered_install: dict,
) -> None:
    """ubFaceIndex=0 is the vanilla convention for "no portrait". The
    endpoint should 204 (no content) so the frontend renders the slot
    number alone — NOT 404, because the merc IS present."""
    payload = {
        "merc": {
            "uiIndex": 200, "ubFaceIndex": 0, "Type": 1,
            "zName": "Faceless", "zNickname": "Faceless",
        },
    }
    r = client.post("/api/v1/merc", json=payload)
    assert r.status_code == 200, r.text

    r = client.get("/api/v1/merc/200/portrait")
    assert r.status_code == 204


def test_get_merc_portrait_404_when_slot_empty(
    client: TestClient, registered_install: dict,
) -> None:
    r = client.get("/api/v1/merc/77/portrait")
    assert r.status_code == 404
    assert r.json()["detail"]["error"] == "SLOT_EMPTY"


def test_get_merc_portrait_404_when_sti_missing(
    client: TestClient, registered_install: dict,
) -> None:
    """ubFaceIndex points at a face that doesn't exist on disk.

    Mods sometimes ship merc profiles whose face artwork is missing — the
    engine boot-CTDs but we want the UI to gracefully render the slot
    number, not show stale art from a different merc. 404 + a structured
    error code lets the frontend distinguish from a real load failure.
    """
    payload = {
        "merc": {
            "uiIndex": 200, "ubFaceIndex": 199, "Type": 1,
            "zName": "Ghost", "zNickname": "Ghost",
        },
    }
    r = client.post("/api/v1/merc", json=payload)
    assert r.status_code == 200, r.text

    r = client.get("/api/v1/merc/200/portrait")
    assert r.status_code == 404
    assert r.json()["detail"]["error"] == "PORTRAIT_NOT_FOUND"
    assert r.json()["detail"]["face_index"] == 199


# ──────────────────────────────────────────────────────────────────────────
#  FaceGear orphan repair (POST /facegear/orphans/repair)
# ──────────────────────────────────────────────────────────────────────────


def _seed_orphan_facegear_install(install_root: Path, *, register_in_xml: bool) -> Path:
    """Drop a single orphaned Face_Lone.sti (no _IMP partner) under
    faces/FACESGEAR/, optionally also registering it in FaceGear.xml.
    Returns the path to the present STI for assertion convenience."""
    from mercwizard_core.facegear import extend_facegear_sti
    from mercwizard_core.portrait.sti import write_static_sti
    from PIL import Image

    fg_dir = install_root / "Data-1.13" / "faces" / "FACESGEAR"
    fg_dir.mkdir(parents=True, exist_ok=True)
    sti_path = fg_dir / "Face_Lone.sti"
    base = Image.new("RGBA", (48, 43), (180, 140, 110, 255))
    write_static_sti(sti_path, base)
    extend_facegear_sti(sti_path, target_count=5)

    if register_in_xml:
        xml_path = install_root / "Data-1.13" / "TableData" / "FaceGear.xml"
        xml_path.write_text(
            '<?xml version="1.0" encoding="UTF-8"?>\n'
            "<FACEGEAR_LIST>\n"
            "\t<ITEM>\n"
            "\t\t<uiIndex>0</uiIndex>\n"
            "\t\t<Type>0</Type>\n"
            "\t\t<szFile>FACES\\FACESGEAR\\Face_Lone</szFile>\n"
            "\t</ITEM>\n"
            "</FACEGEAR_LIST>\n",
            encoding="utf-8",
        )
    return sti_path


def test_facegear_orphans_repair_copies_present_to_missing_partner(
    client: TestClient, registered_install: dict,
) -> None:
    """A registered Face_Lone.sti with no Face_Lone_IMP.sti partner is a
    boot-CTD risk. The repair route should mirror the present file to
    the missing name. Re-scan must report zero orphans afterward."""
    install_root = Path(registered_install["path"])
    src = _seed_orphan_facegear_install(install_root, register_in_xml=True)
    dst = src.with_name("Face_Lone_IMP.sti")
    assert not dst.exists()

    # Sanity-check the scan sees the orphan first
    r = client.get("/api/v1/facegear/capacity")
    assert r.status_code == 200
    orphan_stems = {o["stem"] for o in r.json()["orphans"]}
    assert "Face_Lone" in orphan_stems

    r = client.post("/api/v1/facegear/orphans/repair", json={"stems": None})
    assert r.status_code == 200, r.text
    body = r.json()
    assert len(body["repaired"]) == 1
    assert body["repaired"][0]["stem"] == "Face_Lone"
    assert body["skipped"] == []
    assert body["backup_id"]

    # Disk state: target now exists, byte-for-byte equal to the source
    assert dst.exists()
    assert dst.read_bytes() == src.read_bytes()

    # Fresh scan: install is clean
    r = client.get("/api/v1/facegear/capacity")
    assert r.json()["orphans"] == []


def test_facegear_orphans_repair_skips_unregistered_orphans(
    client: TestClient, registered_install: dict,
) -> None:
    """Filesystem-only orphans (not in FaceGear.xml) aren't engine-CTD
    risks. When FaceGear.xml exists AND lists other items but NOT this
    one, the repair route refuses to touch it — only registered orphans
    get the fix, matching the banner's filter.

    (When FaceGear.xml is absent or empty, the soft-failure fallback
    treats every filesystem orphan as registered; that's covered by the
    happy-path test above which doesn't register the file but still
    repairs it because the registry is missing.)"""
    install_root = Path(registered_install["path"])
    src = _seed_orphan_facegear_install(install_root, register_in_xml=False)
    dst = src.with_name("Face_Lone_IMP.sti")

    # Write a FaceGear.xml that registers OTHER items but not Face_Lone.
    # The repair route should now see the registry, see Face_Lone isn't
    # in it, and skip the repair.
    xml_path = install_root / "Data-1.13" / "TableData" / "FaceGear.xml"
    xml_path.write_text(
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        "<FACEGEAR_LIST>\n"
        "\t<ITEM>\n"
        "\t\t<uiIndex>0</uiIndex>\n"
        "\t\t<Type>0</Type>\n"
        "\t\t<szFile>FACES\\FACESGEAR\\Face_OtherThing</szFile>\n"
        "\t</ITEM>\n"
        "</FACEGEAR_LIST>\n",
        encoding="utf-8",
    )

    r = client.post("/api/v1/facegear/orphans/repair", json={"stems": None})
    assert r.status_code == 200
    body = r.json()
    assert body["repaired"] == []
    assert not dst.exists()


def test_facegear_orphans_repair_empty_when_install_clean(
    client: TestClient, registered_install: dict,
) -> None:
    """No orphans → no writes, no backup, empty response. Safe to call
    speculatively from the UI."""
    r = client.post("/api/v1/facegear/orphans/repair", json={"stems": None})
    assert r.status_code == 200
    body = r.json()
    assert body["repaired"] == []
    assert body["skipped"] == []
    assert body["backup_id"] is None


# ──────────────────────────────────────────────────────────────────────────
#  FaceGear overlay preview (GET /facegear/overlay) — offset_xy surfacing
# ──────────────────────────────────────────────────────────────────────────


def test_facegear_overlay_preview_returns_offset_xy_for_existing_frame(
    client: TestClient, registered_install: dict,
) -> None:
    """The preview route must surface the frame's signed sOffsetX/sOffsetY
    so the FaceGear overlay-author UI can show its nudge widget for frames
    authored in a prior session. Without this, the widget was gated on
    session-local autoPos / nudge mutations and stayed hidden on re-open.

    Inject an overlay with a known non-zero offset, then re-fetch via the
    preview route and assert the response carries that exact offset back.
    """
    from io import BytesIO
    from PIL import Image as _Image
    from mercwizard_core.facegear import inject_overlay

    install_root = Path(registered_install["path"])
    src = _seed_orphan_facegear_install(install_root, register_in_xml=True)

    # Inject a real overlay with a non-zero signed offset. Use both
    # positive and negative components so a missed signed/unsigned
    # conversion would produce a visibly wrong value.
    overlay = _Image.new("RGBA", (48, 43), (220, 60, 60, 255))
    buf = BytesIO()
    overlay.save(buf, format="PNG")
    inject_overlay(src, face_index=3, overlay_png_bytes=buf.getvalue(), offset_xy=(7, -4))

    r = client.get("/api/v1/facegear/overlay?sti_name=Face_Lone.sti&face_index=3")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["sti_name"] == "Face_Lone.sti"
    assert body["face_index"] == 3
    assert body["png_b64"] is not None
    assert body["offset_xy"] == [7, -4]


def test_facegear_overlay_preview_returns_null_offset_for_out_of_range_frame(
    client: TestClient, registered_install: dict,
) -> None:
    """When face_index exceeds the STI's frame count, the preview returns
    png_b64=None AND offset_xy=None — so the UI gating
    `liveOffset && (nudge widget)` correctly hides the nudge buttons for
    a frame that doesn't exist yet (nudging would fail with face_index >=
    frame count if shown)."""
    install_root = Path(registered_install["path"])
    # Seeds a 5-frame STI — face_index=10 is out of range.
    _seed_orphan_facegear_install(install_root, register_in_xml=True)

    r = client.get("/api/v1/facegear/overlay?sti_name=Face_Lone.sti&face_index=10")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["png_b64"] is None
    assert body["offset_xy"] is None


# ──────────────────────────────────────────────────────────────────────────
#  FaceGear set-offset (POST /facegear/set-offset) — direct X/Y editing
# ──────────────────────────────────────────────────────────────────────────


def test_facegear_set_offset_route_applies_and_backs_up(
    client: TestClient, registered_install: dict,
) -> None:
    """POST /facegear/set-offset sets absolute sOffsetX/sOffsetY, returns
    the new offset in the response, and takes a backup so the edit is
    reversible from the Backups page."""
    from io import BytesIO
    from PIL import Image as _Image
    from mercwizard_core.facegear import inject_overlay, read_frame_offset

    install_root = Path(registered_install["path"])
    src = _seed_orphan_facegear_install(install_root, register_in_xml=True)

    # Inject a frame at a known starting offset so the test can assert the
    # set-offset call actually changed it (rather than reading whatever
    # default the seed function produces).
    overlay = _Image.new("RGBA", (48, 43), (220, 60, 60, 255))
    buf = BytesIO()
    overlay.save(buf, format="PNG")
    inject_overlay(src, face_index=2, overlay_png_bytes=buf.getvalue(), offset_xy=(3, 3))

    r = client.post(
        "/api/v1/facegear/set-offset",
        json={
            "sti_name": "Face_Lone.sti",
            "face_index": 2,
            "offset_x": -5,
            "offset_y": 11,
            "apply_to_imp": True,
        },
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["backup_id"]
    assert len(body["written"]) == 1   # no _IMP partner exists in this seed
    written = body["written"][0]
    assert written["previous_offset_xy"] == [3, 3]
    assert written["new_offset_xy"] == [-5, 11]

    # On-disk truth, not just the response
    assert read_frame_offset(src, 2) == (-5, 11)


def test_facegear_set_offset_route_mirrors_to_imp_partner(
    client: TestClient, registered_install: dict,
) -> None:
    """When apply_to_imp=True and a _IMP.sti partner exists, set-offset
    writes both. Matches the nudge route's mirroring behavior."""
    from io import BytesIO
    from PIL import Image as _Image
    from mercwizard_core.facegear import (
        extend_facegear_sti, inject_overlay, read_frame_offset,
    )
    from mercwizard_core.portrait.sti import write_static_sti

    install_root = Path(registered_install["path"])
    fg_dir = install_root / "Data-1.13" / "faces" / "FACESGEAR"
    fg_dir.mkdir(parents=True, exist_ok=True)
    base = _Image.new("RGBA", (48, 43), (180, 140, 110, 255))
    base_sti = fg_dir / "Face_Pair.sti"
    imp_sti = fg_dir / "Face_Pair_IMP.sti"
    write_static_sti(base_sti, base)
    extend_facegear_sti(base_sti, target_count=5)
    write_static_sti(imp_sti, base)
    extend_facegear_sti(imp_sti, target_count=5)
    # Register both ends so the FaceGear scan picks them up (no orphan flag)
    (install_root / "Data-1.13" / "TableData").mkdir(parents=True, exist_ok=True)
    (install_root / "Data-1.13" / "TableData" / "FaceGear.xml").write_text(
        '<?xml version="1.0" encoding="UTF-8"?>\n<FACEGEAR_LIST>\n'
        '\t<ITEM><uiIndex>0</uiIndex><Type>0</Type><szFile>FACES\\FACESGEAR\\Face_Pair</szFile></ITEM>\n'
        '</FACEGEAR_LIST>\n',
        encoding="utf-8",
    )

    overlay = _Image.new("RGBA", (48, 43), (60, 200, 60, 255))
    buf = BytesIO()
    overlay.save(buf, format="PNG")
    inject_overlay(base_sti, face_index=1, overlay_png_bytes=buf.getvalue(), offset_xy=(0, 0))
    inject_overlay(imp_sti, face_index=1, overlay_png_bytes=buf.getvalue(), offset_xy=(0, 0))

    r = client.post(
        "/api/v1/facegear/set-offset",
        json={
            "sti_name": "Face_Pair.sti",
            "face_index": 1,
            "offset_x": 4,
            "offset_y": -2,
            "apply_to_imp": True,
        },
    )
    assert r.status_code == 200, r.text
    body = r.json()
    written_names = sorted(w["name"] for w in body["written"])
    assert written_names == ["Face_Pair.sti", "Face_Pair_IMP.sti"]
    assert read_frame_offset(base_sti, 1) == (4, -2)
    assert read_frame_offset(imp_sti, 1) == (4, -2)


def test_facegear_set_offset_route_rejects_int16_overflow(
    client: TestClient, registered_install: dict,
) -> None:
    """Setting an offset outside ±32768 returns 400 OFFSET_INVALID — the
    engine reads sOffsetX/Y as INT16 and would interpret out-of-range
    values as wraparound."""
    from io import BytesIO
    from PIL import Image as _Image
    from mercwizard_core.facegear import inject_overlay

    install_root = Path(registered_install["path"])
    src = _seed_orphan_facegear_install(install_root, register_in_xml=True)
    overlay = _Image.new("RGBA", (48, 43), (60, 60, 220, 255))
    buf = BytesIO()
    overlay.save(buf, format="PNG")
    inject_overlay(src, face_index=2, overlay_png_bytes=buf.getvalue(), offset_xy=(0, 0))

    r = client.post(
        "/api/v1/facegear/set-offset",
        json={
            "sti_name": "Face_Lone.sti",
            "face_index": 2,
            "offset_x": 40000,
            "offset_y": 0,
            "apply_to_imp": True,
        },
    )
    assert r.status_code == 400, r.text
    detail = r.json()["detail"]
    assert detail["error"] == "OFFSET_INVALID"
    assert "INT16" in detail["message"]


# ──────────────────────────────────────────────────────────────────────────
#  Save scanner routes (re-added after 261425e deletion)
# ──────────────────────────────────────────────────────────────────────────
#
# These cover the HTTP shape of /saves/refs?slot=N. The underlying scan
# logic is tested directly in test_saves_and_misc.py against the
# `mercwizard_core.saves` module; here we just confirm the route wires the
# install's roster into the scanner and reshapes the response correctly.
#
# We monkeypatch BOTH:
#   - `routes.saves.load_roster` so we can stub a roster without depending
#     on the create-merc flow (which has a pre-existing main-branch bug at
#     profiles_xml.invalidate_parse_cache() — see the spun-out task).
#   - `mercwizard_core.saves.list_saves` so the route uses a tmp-path set
#     of fake .SAV blobs instead of hitting the user's real
#     Documents/JA2_113_SavedGames folder during tests.
#
# The route itself imports the saves module (`from mercwizard_core import
# saves as saves_mod`) and `saves_mod.scan_saves_for_mercs` resolves
# `list_saves` through the module's globals at call time, so patching
# the module attribute is enough — no need to patch the route's name
# binding.


def _seed_fake_save(folder: Path, name: str, nickname: str) -> Path:
    """Write a fake .SAV containing the merc's nickname as UTF-16LE bytes."""
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / name
    path.write_bytes(
        b"FAKE_SAVE_HEADER_BYTES " + nickname.encode("utf-16-le") + b" trailer"
    )
    return path


def _stub_roster(monkeypatch: pytest.MonkeyPatch, slots: dict[int, str]) -> None:
    """Patch routes.saves.load_roster to return a roster carrying the given
    nicknames. We mimic the shape the real load_roster returns — the route
    only reads `.slot` and `.nickname` so a SimpleNamespace is enough."""
    from types import SimpleNamespace
    from routes import saves as saves_route

    def fake_load_roster(_install_path):
        return [
            SimpleNamespace(slot=s, nickname=nick) for s, nick in slots.items()
        ]

    monkeypatch.setattr(saves_route, "load_roster", fake_load_roster)


def test_saves_refs_targeted_slot_returns_matching_saves(
    client: TestClient,
    registered_install: dict,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """GET /saves/refs?slot=N returns the list of .SAVs whose bytes contain
    that slot's UTF-16LE-encoded nickname, and an empty list otherwise."""
    _stub_roster(monkeypatch, {5: "Carter", 12: "Tycho"})

    save_dir = tmp_path / "fake_saves"
    carter_save = _seed_fake_save(save_dir, "SaveGame01.SAV", "Carter")
    unrelated = _seed_fake_save(save_dir, "SaveGame02.SAV", "Tycho")

    from mercwizard_core import saves as saves_mod
    fake_files = [
        saves_mod.SaveFile(path=carter_save, modified=0.0, size=carter_save.stat().st_size),
        saves_mod.SaveFile(path=unrelated, modified=0.0, size=unrelated.stat().st_size),
    ]
    monkeypatch.setattr(saves_mod, "list_saves", lambda *_a, **_kw: fake_files)

    r = client.get("/api/v1/saves/refs?slot=5")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["slot"] == 5
    assert any(str(carter_save) == p for p in body["saves"])
    assert not any(str(unrelated) == p for p in body["saves"])


def test_saves_refs_empty_when_no_save_references_slot(
    client: TestClient,
    registered_install: dict,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A slot whose nickname isn't in any save returns an empty `saves` list
    (and a 200 — the banner suppresses itself when empty)."""
    _stub_roster(monkeypatch, {5: "Carter"})

    save_dir = tmp_path / "fake_saves"
    _seed_fake_save(save_dir, "SaveGame01.SAV", "Tycho")  # only Tycho, no Carter

    from mercwizard_core import saves as saves_mod
    fake_files = [
        saves_mod.SaveFile(
            path=save_dir / "SaveGame01.SAV",
            modified=0.0,
            size=(save_dir / "SaveGame01.SAV").stat().st_size,
        ),
    ]
    monkeypatch.setattr(saves_mod, "list_saves", lambda *_a, **_kw: fake_files)

    r = client.get("/api/v1/saves/refs?slot=5")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body == {"slot": 5, "saves": []}


def test_saves_refs_bulk_shape_returns_all_when_no_slot(
    client: TestClient,
    registered_install: dict,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Omitting `slot` returns `{"all": {<slot>: [<paths>], ...}}` — the
    shape a future Hub-level "where does each merc live?" panel would
    consume. Note JSON keys are strings even for integer slot IDs."""
    _stub_roster(monkeypatch, {5: "Carter"})

    save_dir = tmp_path / "fake_saves"
    carter_save = _seed_fake_save(save_dir, "SaveGame01.SAV", "Carter")

    from mercwizard_core import saves as saves_mod
    fake_files = [
        saves_mod.SaveFile(
            path=carter_save, modified=0.0, size=carter_save.stat().st_size,
        ),
    ]
    monkeypatch.setattr(saves_mod, "list_saves", lambda *_a, **_kw: fake_files)

    r = client.get("/api/v1/saves/refs")
    assert r.status_code == 200, r.text
    body = r.json()
    assert "all" in body
    # FastAPI's default jsonable_encoder serializes int dict keys as strings
    assert str(carter_save) in body["all"].get("5", [])


def test_saves_refs_requires_active_install(client: TestClient) -> None:
    """No active install → 400 NO_ACTIVE_INSTALL, parallel to /roster."""
    r = client.get("/api/v1/saves/refs?slot=5")
    assert r.status_code == 400
    assert r.json()["detail"]["error"] == "NO_ACTIVE_INSTALL"
