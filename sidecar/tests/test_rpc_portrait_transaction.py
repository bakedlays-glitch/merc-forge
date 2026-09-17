"""Route-level regression tests for RPC portrait/profile atomicity."""
from __future__ import annotations

import io
import json
from contextlib import contextmanager
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from main import create_app
from mercwizard_core import body_types, rpc_facessmall as SF
from mercwizard_core import backup
from mercwizard_core.install_context import make_install_context
from mercwizard_core.inject import profiles_xml
from mercwizard_core.portrait.sti import verify_animated_face_sti
from routes import portrait as portrait_routes
from routes.state import get_state


def _png() -> bytes:
    out = io.BytesIO()
    Image.new("RGBA", (200, 200), (120, 90, 65, 255)).save(out, format="PNG")
    return out.getvalue()


@pytest.fixture(autouse=True)
def reset_state(monkeypatch: pytest.MonkeyPatch):
    state = get_state()
    state._installs = {}
    state._active_install_id = None
    state._scan_done = False
    @contextmanager
    def no_process_lock(_install_id):
        yield
    # The portable Codex test runtime reuses the project's site-packages but
    # does not ship pywin32. Other route tests make the same substitution; the
    # dedicated race test below replaces this no-op with its competing writer.
    monkeypatch.setattr(portrait_routes, "cross_process_install_root_lock", no_process_lock)
    @contextmanager
    def no_backup_lock(*_args, **_kwargs):
        yield
    monkeypatch.setattr(backup, "_backup_lock", no_backup_lock)
    yield
    state._installs = {}
    state._active_install_id = None
    state._scan_done = False


@pytest.fixture
def registered(tmp_path: Path) -> tuple[TestClient, dict, Path]:
    client = TestClient(create_app())
    root = tmp_path / "install"
    root.mkdir()
    (root / "JA2.exe").touch()
    table = root / "Data-1.13" / "TableData"
    table.mkdir(parents=True)
    (table / "MercProfiles.xml").write_text("<PROFILES />", encoding="utf-8")
    (table / "AIMAvailability.xml").write_text("<AIM_AVAILABLES />", encoding="utf-8")
    response = client.post("/api/v1/installs", json={"path": str(root)})
    assert response.status_code == 200, response.text
    info = response.json()
    client.post("/api/v1/installs/active", json={"install_id": info["id"]})
    return client, info, root


def _rpc_form(slot: int = 220, *, operation: str = "create", body_type: int = 0) -> dict[str, str]:
    return {
        "operation": operation,
        "merc_json": json.dumps({
            "uiIndex": slot,
            "ubFaceIndex": slot,
            "Type": 3,
            "zName": "Atomic RPC",
            "zNickname": "Atomic",
            "usVoiceIndex": slot,
            "ubBodyType": body_type,
        }),
        "eye_x": "10",
        "eye_y": "8",
        "eye_w": "17",
        "eye_h": "6",
        "mouth_x": "7",
        "mouth_y": "28",
        "mouth_w": "14",
        "mouth_h": "6",
    }


def test_compile_rollback_does_not_delete_file_created_before_lock(
    registered, monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, info, root = registered
    target = root / "Data-1.13" / "faces" / "B63.sti"

    @contextmanager
    def competing_writer(_install_id):
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"OTHER_PROCESS_FACE")
        yield

    def fail_compile(*_args, **_kwargs):
        raise RuntimeError("forced compile failure")

    recorded_created: list[Path] = []
    real_record = portrait_routes.backup.record_files_created

    def capture_record(*args, **kwargs):
        recorded_created.extend(kwargs.get("files", args[2] if len(args) > 2 else []))
        return real_record(*args, **kwargs)

    monkeypatch.setattr(portrait_routes, "cross_process_install_root_lock", competing_writer)
    monkeypatch.setattr(portrait_routes, "compile_and_write_all", fail_compile)
    monkeypatch.setattr(portrait_routes.backup, "record_files_created", capture_record)

    response = client.post(
        "/api/v1/portrait/compile",
        data={"face_index": "63", "rpc_talkface": "true"},
        files={"image": ("portrait.png", _png(), "image/png")},
    )

    assert response.status_code == 500
    assert target.read_bytes() == b"OTHER_PROCESS_FACE"
    assert target not in recorded_created
    entry = backup.list_backups(info["id"])[0]
    assert str(target).replace("\\", "/") not in entry.files_created


def test_rpc_save_rolls_back_faces_profile_and_small_coords_as_one_unit(
    registered, monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, _info, root = registered
    ctx = make_install_context(root)
    targets = [
        ctx.face_sti_path(220, size=size, for_write=True)
        for size in ("smallface", "face_65", "face_33", "bigface")
    ]
    targets.append(ctx.rpc_talkface_path(220, for_write=True))
    for i, target in enumerate(targets):
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(f"OLD_{i}".encode("ascii"))
    small = ctx.rpc_faces_small_path(for_write=True)
    small.parent.mkdir(parents=True, exist_ok=True)
    small.write_text(SF.upsert("", 220, "Old", 1, 2, 3, 4), encoding="utf-8")
    before_profile = ctx.profiles_xml_path(for_write=True).read_bytes()
    before_small = small.read_bytes()

    def fail_profile_write(*_args, **_kwargs):
        raise RuntimeError("forced profile failure after face compile")

    monkeypatch.setattr(profiles_xml, "upsert", fail_profile_write)

    response = client.post(
        "/api/v1/portrait/rpc-save",
        data=_rpc_form(),
        files={"image": ("portrait.png", _png(), "image/png")},
    )

    assert response.status_code == 500, response.text
    assert response.json()["detail"]["rollback_ok"] is True, response.text
    assert ctx.profiles_xml_path(for_write=True).read_bytes() == before_profile
    assert small.read_bytes() == before_small
    assert [target.read_bytes() for target in targets] == [
        f"OLD_{i}".encode("ascii") for i in range(len(targets))
    ]


def test_rpc_save_creates_profile_both_face_systems_and_one_backup(registered) -> None:
    client, _info, root = registered

    response = client.post(
        "/api/v1/portrait/rpc-save",
        data=_rpc_form(),
        files={"image": ("portrait.png", _png(), "image/png")},
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["ok"] is True
    assert body["backup_id"]
    ctx = make_install_context(root)
    profile = profiles_xml.read_slot(ctx.profiles_xml_path(), 220)
    assert profile is not None and profile["Type"] == "3"
    assert int(profile["usEyesX"]) == body["talkface"]["eyes_x"]
    assert int(profile["usMouthY"]) == body["talkface"]["mouth_y"]
    assert SF.read_override(ctx.rpc_faces_small_path().read_text(encoding="utf-8"), 220) is not None
    assert verify_animated_face_sti(
        ctx.rpc_talkface_path(220), expected_base_size=(90, 100),
    )["valid"] is True


def test_rpc_save_observed_body_type_is_create_rejected_but_edit_preserved(registered) -> None:
    client, _info, root = registered
    profiles_path = make_install_context(root).profiles_xml_path(for_write=True)
    profiles_xml.upsert(
        profiles_path,
        portrait_routes.Merc(
            uiIndex=219, ubFaceIndex=219, Type=3,
            zName="Witness", zNickname="Witness", ubBodyType=91,
        ),
    )
    create = client.post(
        "/api/v1/portrait/rpc-save",
        data=_rpc_form(220, body_type=91),
        files={"image": ("portrait.png", _png(), "image/png")},
    )
    assert create.status_code == 400
    assert create.json()["detail"]["error"] == "BODY_TYPE_PRESERVE_ONLY"

    profiles_xml.upsert(
        profiles_path,
        portrait_routes.Merc(
            uiIndex=220, ubFaceIndex=220, Type=3,
            zName="Observed", zNickname="Obs", ubBodyType=91,
        ),
    )
    edit = client.post(
        "/api/v1/portrait/rpc-save",
        data=_rpc_form(220, operation="edit", body_type=91),
        files={"image": ("portrait.png", _png(), "image/png")},
    )
    assert edit.status_code == 200, edit.text


def test_rpc_edit_rejects_unknown_body_type_in_engine_known_registry(
    registered, monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, _info, root = registered
    profiles_path = make_install_context(root).profiles_xml_path(for_write=True)
    profiles_xml.upsert(
        profiles_path,
        portrait_routes.Merc(
            uiIndex=220, ubFaceIndex=220, Type=3,
            zName="Invalid", zNickname="Invalid", ubBodyType=44,
        ),
    )
    monkeypatch.setattr(body_types, "_has_wasteland_body_type_fingerprint", lambda _root: True)
    monkeypatch.setattr(body_types, "_has_canonical_wasteland_engine", lambda _root: True)

    edit = client.post(
        "/api/v1/portrait/rpc-save",
        data=_rpc_form(220, operation="edit", body_type=44),
        files={"image": ("portrait.png", _png(), "image/png")},
    )

    assert edit.status_code == 400
    detail = edit.json()["detail"]
    assert detail["error"] == "AUDIT_FAILED"
    assert {issue["code"] for issue in detail["issues"]} == {"BODY_TYPE_UNKNOWN"}
    assert profiles_xml.read_slot(profiles_path, 220)["ubBodyType"] == "44"
