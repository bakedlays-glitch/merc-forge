import json
from contextlib import nullcontext
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from main import create_app
from mercwizard_core.audit import audit_full
from mercwizard_core import body_types
from mercwizard_core.body_types import body_types_for_install
from mercwizard_core.inject import profiles_xml
from mercwizard_core.models import Merc
from routes.state import get_state
from routes import merc as merc_routes


VANILLA_BODY_TYPE_NAMES = {
    0: "REGMALE", 1: "BIGMALE", 2: "STOCKYMALE", 3: "REGFEMALE",
    4: "ADULTFEMALEMONSTER", 5: "AM_MONSTER", 6: "YAF_MONSTER",
    7: "YAM_MONSTER", 8: "LARVAE_MONSTER", 9: "INFANT_MONSTER",
    10: "QUEENMONSTER", 11: "FATCIV", 12: "MANCIV", 13: "MINICIV",
    14: "DRESSCIV", 15: "HATKIDCIV", 16: "KIDCIV", 17: "CRIPPLECIV",
    18: "COW", 19: "CROW", 20: "BLOODCAT", 21: "ROBOTNOWEAPON",
    22: "HUMVEE", 23: "TANK_NW", 24: "TANK_NE", 25: "ELDORADO",
    26: "ICECREAMTRUCK", 27: "JEEP", 28: "COMBAT_JEEP",
}
WASTELAND_EXTENSION_NAMES = {
    29: "DOG", 30: "GORISCLAW", 31: "GRUTHARCLAW", 32: "MOMCLAW",
    33: "MUTANT", 34: "ALPHACLAW", 35: "NIGHTKIN", 36: "GHOUL",
    37: "FERALGHOUL", 38: "GLOWGHOUL", 39: "RADSCORPION", 40: "HULK",
    41: "MARCUS", 42: "JAY", 43: "SILENTBOB",
}
CANONICAL_WASTELAND_EXE_SIZE = 10_231_808
CANONICAL_WASTELAND_EXE_SHA256 = "eaeacace4e958aa96f4a5e7bd36dea084632ccf7f2c1932e1217305e8159de77"
FROZEN_STOCK_EXE_SHA256 = "8c95e5c4d26506416eb6ca81048a758b9f68f627b280ad8277819462ed443038"


@pytest.fixture
def client() -> TestClient:
    state = get_state()
    state._installs = {}
    state._active_install_id = None
    state._scan_done = False
    with TestClient(create_app()) as test_client:
        yield test_client
    state._installs = {}
    state._active_install_id = None
    state._scan_done = False


def _register_install(client: TestClient, install: Path) -> dict:
    install.mkdir(parents=True, exist_ok=True)
    (install / "JA2.exe").touch()
    table = install / "Data-1.13" / "TableData"
    table.mkdir(parents=True, exist_ok=True)
    (table / "MercProfiles.xml").touch(exist_ok=True)
    (table / "AIMAvailability.xml").write_text("<AIM_AVAILABLES />", encoding="utf-8")
    response = client.post("/api/v1/installs", json={"path": str(install)})
    assert response.status_code == 200, response.text
    return response.json()


@pytest.fixture(autouse=True)
def canonical_digest_for_synthetic_test_exes(monkeypatch: pytest.MonkeyPatch) -> None:
    """Never copy the game executable into test fixtures."""
    monkeypatch.setattr(
        body_types, "_sha256_file", lambda _path: CANONICAL_WASTELAND_EXE_SHA256, raising=False,
    )


def _wasteland_install(tmp_path: Path, *, exe_name: str = "ja2.exe") -> Path:
    install = tmp_path / "Wasteland test install"
    (install / "Data-1.13" / "TileSets" / "Tileset 70").mkdir(parents=True)
    # Synthetic fixed-size bytes plus the autouse digest fake model the
    # canonical executable without distributing or copying it into tests.
    (install / exe_name).write_bytes(b"\0" * CANONICAL_WASTELAND_EXE_SIZE)
    return install


def test_wasteland_registry_accepts_custom_ids_but_not_sentinel(tmp_path: Path) -> None:
    registry = body_types_for_install(_wasteland_install(tmp_path))

    assert registry.options[29].name == "DOG"
    assert registry.options[43].name == "SILENTBOB"
    assert 44 not in registry.options


def test_folder_name_alone_does_not_grant_unobserved_wasteland_body_types(tmp_path: Path) -> None:
    """A stock/custom install can mention Fallout without its custom engine."""
    install = tmp_path / "Fallout themed stock 1.13"
    profiles_xml.upsert(
        install / "Data-1.13" / "TableData" / "MercProfiles.xml",
        Merc(uiIndex=220, ubFaceIndex=220, zName="Observed", zNickname="Obs", ubBodyType=91),
    )

    registry = body_types_for_install(install)

    assert registry.source == "observed-extension"
    assert registry.options[91].name == "Observed 91"
    assert 29 not in registry.options
    assert 43 not in registry.options


def test_wasteland_data_with_frozen_stock_engine_stays_engine_unverified(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    install = _wasteland_install(tmp_path)
    profiles_xml.upsert(
        install / "Data-1.13" / "TableData" / "MercProfiles.xml",
        Merc(uiIndex=220, ubFaceIndex=220, zName="Observed", zNickname="Obs", ubBodyType=91),
    )
    monkeypatch.setattr(
        body_types, "_sha256_file", lambda _path: FROZEN_STOCK_EXE_SHA256, raising=False,
    )

    registry = body_types_for_install(install)

    assert registry.source == "engine-unverified"
    assert {id: body.name for id, body in registry.options.items()} == {
        **VANILLA_BODY_TYPE_NAMES,
        91: "Observed 91",
    }


def test_wasteland_data_ignores_non_normal_executable_names(tmp_path: Path) -> None:
    registry = body_types_for_install(_wasteland_install(tmp_path, exe_name="JA2_ENGLISH.exe"))

    assert registry.source == "engine-unverified"
    assert set(registry.options) == set(VANILLA_BODY_TYPE_NAMES)


@pytest.mark.parametrize("marker", ["tileset", "ja2set"])
def test_wasteland_data_fingerprint_uses_the_active_vfs_overlay(
    tmp_path: Path, marker: str,
) -> None:
    install = tmp_path / "Wasteland VFS install"
    (install / "JA2.exe").parent.mkdir(parents=True, exist_ok=True)
    (install / "JA2.exe").write_bytes(b"\0" * CANONICAL_WASTELAND_EXE_SIZE)
    (install / "Ja2.ini").write_text("[Ja2 Settings]\nVFS_CONFIG_INI = vfs_config.test.ini\n")
    (install / "vfs_config.test.ini").write_text("\n".join([
        "[vfs_config]", "PROFILES = base, overlay", "",
        "[PROFILE_base]", "LOCATIONS = loc_base", "",
        "[PROFILE_overlay]", "LOCATIONS = loc_overlay", "",
        "[LOC_loc_base]", "TYPE = DIRECTORY", "PATH = Data-1.13", "",
        "[LOC_loc_overlay]", "TYPE = DIRECTORY", "PATH = Data-Overlay",
    ]))
    if marker == "tileset":
        (install / "Data-Overlay" / "TileSets" / "Tileset 70").mkdir(parents=True)
    else:
        (install / "Data-1.13").mkdir(exist_ok=True)
        (install / "Data-1.13" / "Ja2Set.dat.xml").write_text("ordinary base data")
        (install / "Data-Overlay").mkdir(exist_ok=True)
        (install / "Data-Overlay" / "Ja2Set.dat.xml").write_text("FALLOUT VAULT")

    registry = body_types_for_install(install)

    assert registry.source == "engine-known"
    assert registry.options[43].name == "SILENTBOB"


def test_known_registries_have_the_exact_engine_id_name_mapping(tmp_path: Path) -> None:
    vanilla = body_types_for_install(tmp_path / "Vanilla 1.13")
    wasteland = body_types_for_install(_wasteland_install(tmp_path))

    assert {id: body.name for id, body in vanilla.options.items()} == VANILLA_BODY_TYPE_NAMES
    assert {id: body.name for id, body in wasteland.options.items()} == {
        **VANILLA_BODY_TYPE_NAMES,
        **WASTELAND_EXTENSION_NAMES,
    }


def test_wasteland_registry_rejects_totalbodytypes_via_real_target_path(tmp_path: Path) -> None:
    registry = body_types_for_install(_wasteland_install(tmp_path))
    merc = Merc(uiIndex=220, ubFaceIndex=220, zName="Test", zNickname="Test", ubBodyType=44)

    assert any(
        issue.code == "BODY_TYPE_UNKNOWN"
        for issue in audit_full(merc, body_types=registry.options)
    )


def test_unknown_registry_keeps_observed_body_type_with_provenance(tmp_path: Path) -> None:
    install = tmp_path / "Custom Test Mod"
    profiles_path = install / "Data-1.13" / "TableData" / "MercProfiles.xml"
    profiles_xml.upsert(
        profiles_path,
        Merc(uiIndex=220, ubFaceIndex=220, zName="Observed", zNickname="Obs", ubBodyType=91),
    )

    registry = body_types_for_install(install)

    assert registry.source == "observed-extension"
    assert registry.options[91].name == "Observed 91"
    assert registry.options[91].category == "observed"


def test_observed_body_types_are_preserve_only_for_merc_writes(
    client: TestClient, tmp_path: Path,
) -> None:
    install = tmp_path / "Custom observed body types"
    profiles_path = install / "Data-1.13" / "TableData" / "MercProfiles.xml"
    profiles_xml.upsert(
        profiles_path,
        Merc(uiIndex=220, ubFaceIndex=220, zName="Observed", zNickname="Obs", ubBodyType=91),
    )
    profiles_xml.upsert(
        profiles_path,
        Merc(uiIndex=221, ubFaceIndex=221, zName="Other", zNickname="Other", ubBodyType=92),
    )
    info = _register_install(client, install)
    body_types_response = client.get(f"/api/v1/merc/body-types?install_id={info['id']}")
    assert body_types_response.status_code == 200
    observed = {option["id"]: option for option in body_types_response.json()["options"]}
    assert observed[91]["authorable"] is False
    assert observed[92]["authorable"] is False

    create = client.post("/api/v1/merc", params={"install_id": info["id"]}, json={"merc": {
        "uiIndex": 222, "ubFaceIndex": 222, "Type": 1,
        "zName": "New", "zNickname": "New", "ubBodyType": 91,
    }})
    assert create.status_code == 400
    assert create.json()["detail"]["error"] == "BODY_TYPE_PRESERVE_ONLY"

    unchanged = client.put("/api/v1/merc/220", params={"install_id": info["id"]}, json={"merc": {
        "uiIndex": 220, "ubFaceIndex": 220, "Type": 1,
        "zName": "Observed", "zNickname": "Obs", "ubBodyType": 91,
    }})
    assert unchanged.status_code == 200, unchanged.text

    changed = client.put("/api/v1/merc/220", params={"install_id": info["id"]}, json={"merc": {
        "uiIndex": 220, "ubFaceIndex": 220, "Type": 1,
        "zName": "Observed", "zNickname": "Obs", "ubBodyType": 92,
    }})
    assert changed.status_code == 400
    assert changed.json()["detail"]["error"] == "BODY_TYPE_PRESERVE_ONLY"


def test_force_create_rejects_replacement_even_when_observed_body_type_matches(
    client: TestClient, tmp_path: Path,
) -> None:
    install = tmp_path / "force same observed body type"
    profiles_path = install / "Data-1.13" / "TableData" / "MercProfiles.xml"
    profiles_xml.upsert(
        profiles_path,
        Merc(uiIndex=220, ubFaceIndex=220, Type=1, zName="Existing", zNickname="Existing", ubBodyType=91),
    )
    info = _register_install(client, install)

    response = client.post(
        "/api/v1/merc",
        params={"install_id": info["id"]},
        json={"force": True, "merc": {
            "uiIndex": 220, "ubFaceIndex": 221, "Type": 1,
            "zName": "Replace", "zNickname": "Replace", "ubBodyType": 91,
        }},
    )

    assert response.status_code == 400
    assert response.json()["detail"]["error"] == "BODY_TYPE_PRESERVE_ONLY"
    existing = profiles_xml.read_slot(profiles_path, 220)
    assert existing["zName"] == "Existing"
    assert existing["ubBodyType"] == "91"


def test_create_without_force_keeps_slot_occupied_precedence_for_observed_body_type(
    client: TestClient, tmp_path: Path,
) -> None:
    install = tmp_path / "occupied observed body type"
    profiles_path = install / "Data-1.13" / "TableData" / "MercProfiles.xml"
    profiles_xml.upsert(
        profiles_path,
        Merc(uiIndex=220, ubFaceIndex=220, Type=1, zName="Existing", zNickname="Existing", ubBodyType=91),
    )
    info = _register_install(client, install)

    response = client.post(
        "/api/v1/merc",
        params={"install_id": info["id"]},
        json={"merc": {
            "uiIndex": 220, "ubFaceIndex": 221, "Type": 1,
            "zName": "Replace", "zNickname": "Replace", "ubBodyType": 91,
        }},
    )

    assert response.status_code == 409
    assert response.json()["detail"]["error"] == "SLOT_OCCUPIED"


def test_create_race_keeps_slot_occupied_precedence_for_observed_body_type(
    client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    install = tmp_path / "raced occupied observed body type"
    profiles_path = install / "Data-1.13" / "TableData" / "MercProfiles.xml"
    profiles_xml.upsert(
        profiles_path,
        Merc(uiIndex=221, ubFaceIndex=221, Type=1, zName="Witness", zNickname="Witness", ubBodyType=91),
    )
    info = _register_install(client, install)
    real_picker = merc_routes.build_slot_picker
    raced = False

    def occupy_after_preflight(*args, **kwargs):
        nonlocal raced
        if not raced:
            raced = True
            profiles_xml.upsert(
                profiles_path,
                Merc(uiIndex=220, ubFaceIndex=220, Type=1, zName="Racer", zNickname="Racer", ubBodyType=91),
            )
        return real_picker(*args, **kwargs)

    monkeypatch.setattr(merc_routes, "build_slot_picker", occupy_after_preflight)
    response = client.post(
        "/api/v1/merc",
        params={"install_id": info["id"]},
        json={"merc": {
            "uiIndex": 220, "ubFaceIndex": 222, "Type": 1,
            "zName": "New", "zNickname": "New", "ubBodyType": 91,
        }},
    )

    assert response.status_code == 409
    assert response.json()["detail"]["error"] == "SLOT_OCCUPIED"
    assert profiles_xml.read_slot(profiles_path, 220)["zName"] == "Racer"


def test_force_create_rejects_observed_body_type_for_different_occupied_slot(
    client: TestClient, tmp_path: Path,
) -> None:
    install = tmp_path / "force different observed body type"
    profiles_path = install / "Data-1.13" / "TableData" / "MercProfiles.xml"
    profiles_xml.upsert(
        profiles_path,
        Merc(uiIndex=220, ubFaceIndex=220, Type=1, zName="Existing", zNickname="Existing", ubBodyType=92),
    )
    profiles_xml.upsert(
        profiles_path,
        Merc(uiIndex=221, ubFaceIndex=221, Type=1, zName="Observed", zNickname="Observed", ubBodyType=91),
    )
    info = _register_install(client, install)

    response = client.post(
        "/api/v1/merc",
        params={"install_id": info["id"]},
        json={"force": True, "merc": {
            "uiIndex": 220, "ubFaceIndex": 221, "Type": 1,
            "zName": "Replace", "zNickname": "Replace", "ubBodyType": 91,
        }},
    )

    assert response.status_code == 400
    assert response.json()["detail"]["error"] == "BODY_TYPE_PRESERVE_ONLY"
    assert response.json()["detail"]["body_type"] == 91


def test_force_create_rejects_observed_body_type_for_unoccupied_slot(
    client: TestClient, tmp_path: Path,
) -> None:
    install = tmp_path / "force unoccupied observed body type"
    profiles_path = install / "Data-1.13" / "TableData" / "MercProfiles.xml"
    profiles_xml.upsert(
        profiles_path,
        Merc(uiIndex=220, ubFaceIndex=220, Type=1, zName="Existing", zNickname="Existing", ubBodyType=91),
    )
    info = _register_install(client, install)

    response = client.post(
        "/api/v1/merc",
        params={"install_id": info["id"]},
        json={"force": True, "merc": {
            "uiIndex": 221, "ubFaceIndex": 221, "Type": 1,
            "zName": "New", "zNickname": "New", "ubBodyType": 91,
        }},
    )

    assert response.status_code == 400
    assert response.json()["detail"]["error"] == "BODY_TYPE_PRESERVE_ONLY"
    assert response.json()["detail"]["body_type"] == 91


def test_same_install_move_and_duplicate_return_preserve_only_identity(
    client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    install = tmp_path / "relocation observed body type"
    profiles_path = install / "Data-1.13" / "TableData" / "MercProfiles.xml"
    profiles_xml.upsert(
        profiles_path,
        Merc(
            uiIndex=5, ubFaceIndex=165, Type=1, zName="Observed",
            zNickname="Observed", ubBodyType=91,
        ),
    )
    info = _register_install(client, install)
    monkeypatch.setattr(merc_routes, "cross_process_install_root_lock", lambda _root: nullcontext())

    def final_event(response) -> dict:
        return next(
            json.loads(line)
            for line in response.text.splitlines()
            if line and json.loads(line).get("done")
        )

    moved = final_event(client.post(
        "/api/v1/merc/5/move",
        params={"install_id": info["id"]},
        json={"to_slot": 10},
    ))
    duplicated = final_event(client.post(
        "/api/v1/merc/5/duplicate",
        params={"install_id": info["id"]},
        json={"to_slot": 11},
    ))

    for event in (moved, duplicated):
        assert event["ok"] is False
        assert event["error"] == "BODY_TYPE_PRESERVE_ONLY", event
        assert event["body_type"] == 91


def test_update_rechecks_preserve_only_body_type_inside_its_write_transaction(
    client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A profile changed after preflight must not make an observed ID writable."""
    install = tmp_path / "observed update race"
    profiles_path = install / "Data-1.13" / "TableData" / "MercProfiles.xml"
    profiles_xml.upsert(
        profiles_path,
        Merc(uiIndex=220, ubFaceIndex=220, Type=1, zName="Observed", zNickname="Obs", ubBodyType=91),
    )
    profiles_xml.upsert(
        profiles_path,
        Merc(uiIndex=221, ubFaceIndex=221, Type=1, zName="Witness", zNickname="Witness", ubBodyType=91),
    )
    profiles_xml.upsert(
        profiles_path,
        Merc(uiIndex=222, ubFaceIndex=222, Type=1, zName="Other", zNickname="Other", ubBodyType=92),
    )
    info = _register_install(client, install)
    real_picker = merc_routes.build_slot_picker
    raced = False

    def change_before_worker(*args, **kwargs):
        nonlocal raced
        if not raced:
            raced = True
            profiles_xml.upsert(
                profiles_path,
                Merc(uiIndex=220, ubFaceIndex=220, Type=1, zName="Observed", zNickname="Obs", ubBodyType=92),
            )
        return real_picker(*args, **kwargs)

    monkeypatch.setattr(merc_routes, "build_slot_picker", change_before_worker)
    response = client.put("/api/v1/merc/220", params={"install_id": info["id"]}, json={"merc": {
        "uiIndex": 220, "ubFaceIndex": 220, "Type": 1,
        "zName": "Observed", "zNickname": "Obs", "ubBodyType": 91,
    }})

    assert response.status_code == 200
    events = [json.loads(line) for line in response.text.splitlines() if line]
    assert events[-1]["error"] == "BODY_TYPE_PRESERVE_ONLY"
    assert profiles_xml.read_slot(profiles_path, 220)["ubBodyType"] == "92"


def test_unknown_registry_reads_only_the_active_vfs_overlay_profile(tmp_path: Path) -> None:
    install = tmp_path / "Custom VFS Mod"
    (install / "Ja2.ini").parent.mkdir(parents=True)
    (install / "Ja2.ini").write_text("[Ja2 Settings]\nVFS_CONFIG_INI = vfs_config.test.ini\n")
    (install / "vfs_config.test.ini").write_text("\n".join([
        "[vfs_config]", "PROFILES = base, overlay", "",
        "[PROFILE_base]", "LOCATIONS = loc_base", "",
        "[PROFILE_overlay]", "LOCATIONS = loc_overlay", "",
        "[LOC_loc_base]", "TYPE = DIRECTORY", "PATH = Data-1.13", "",
        "[LOC_loc_overlay]", "TYPE = DIRECTORY", "PATH = Data-Overlay",
    ]))
    base_profiles = install / "Data-1.13" / "TableData" / "MercProfiles.xml"
    overlay_profiles = install / "Data-Overlay" / "TableData" / "MercProfiles.xml"
    profiles_xml.upsert(
        base_profiles,
        Merc(uiIndex=220, ubFaceIndex=220, zName="Base", zNickname="Base", ubBodyType=91),
    )
    profiles_xml.upsert(
        overlay_profiles,
        Merc(uiIndex=221, ubFaceIndex=221, zName="Overlay", zNickname="Overlay", ubBodyType=92),
    )

    registry = body_types_for_install(install)

    assert 92 in registry.options
    assert 91 not in registry.options


def test_body_type_route_honors_explicit_install_ids(client: TestClient, tmp_path: Path) -> None:
    vanilla_info = _register_install(client, tmp_path / "Vanilla 1.13")
    wasteland_root = tmp_path / "Wasteland target"
    (wasteland_root / "Data-1.13" / "TileSets" / "Tileset 70").mkdir(parents=True)
    (wasteland_root / "JA2.exe").write_bytes(b"\0" * CANONICAL_WASTELAND_EXE_SIZE)
    wasteland_info = _register_install(client, wasteland_root)
    unknown_root = tmp_path / "Custom target"
    profiles_xml.upsert(
        unknown_root / "Data-1.13" / "TableData" / "MercProfiles.xml",
        Merc(uiIndex=220, ubFaceIndex=220, zName="Observed", zNickname="Obs", ubBodyType=91),
    )
    unknown_info = _register_install(client, unknown_root)

    vanilla = client.get(f"/api/v1/merc/body-types?install_id={vanilla_info['id']}")
    wasteland = client.get(f"/api/v1/merc/body-types?install_id={wasteland_info['id']}")
    unknown = client.get(f"/api/v1/merc/body-types?install_id={unknown_info['id']}")

    assert vanilla.status_code == wasteland.status_code == unknown.status_code == 200
    assert vanilla.json()["mod_id"] == "vanilla"
    assert {option["id"] for option in vanilla.json()["options"]} == set(VANILLA_BODY_TYPE_NAMES)
    assert wasteland.json()["mod_id"] == "wasteland"
    assert {option["id"] for option in wasteland.json()["options"]} == set(range(44))
    assert unknown.json()["source"] == "observed-extension"
    assert {option["id"] for option in unknown.json()["options"]} == {*VANILLA_BODY_TYPE_NAMES, 91}
