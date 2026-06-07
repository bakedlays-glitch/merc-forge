"""Tests for the INI editor (mercwizard_core.ini_editor + routes).

Every finding from the 2026-06-07 adversarial reviews becomes a
regression test here:
  - comment/format preservation through surgical writes
  - duplicate keys: edit-last-occurrence (engine last-wins), delete-all
  - no commented-key revival (comments are never parsed as keys)
  - CRLF/LF preservation
  - injection + traversal rejection
  - merge-registered per-key layering vs whole-file top-resolve
  - `.Override` overlay wins; provenance + override_active
  - schema default fallback + stock baseline values
  - AI.ini Play-mode refusal; legacy installs refuse overrides
  - game-running guard (409); dry-run writes nothing; backups taken

What this suite CANNOT prove: that the real engine reads our override
files — that's Step 6's clone + iniErrorReport canary.
"""
from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from main import create_app
from mercwizard_core import ini_editor as ie
from mercwizard_core.ini_editor import (
    IniChange,
    IniEditor,
    IniEditorError,
    canonical_ini_name,
    override_filename,
    parse_ini_map,
    surgical_upsert,
)
from mercwizard_core.vfs import parse_vfs_config
from routes.state import get_state


# ──────────────────────────────────────────────────────────────────────────
#  Fixtures: a synthetic stock-1.13-shaped VFS install
# ──────────────────────────────────────────────────────────────────────────


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


@pytest.fixture(autouse=True)
def no_game_running(monkeypatch):
    """Default: the game is not running (individual tests override)."""
    monkeypatch.setattr(ie, "game_running", lambda exe_name="ja2.exe": False)


def make_vfs_install(root: Path, *, merge_ja2_options: bool = True) -> Path:
    """A miniature install faithful to the live Copy's layout."""
    root.mkdir(parents=True, exist_ok=True)
    (root / "JA2.exe").touch()
    (root / "Data").mkdir(exist_ok=True)
    d113 = root / "Data-1.13"
    (d113 / "TableData").mkdir(parents=True, exist_ok=True)
    (d113 / "TableData" / "MercProfiles.xml").write_text("<PROFILES />")
    (d113 / "TableData" / "AIMAvailability.xml").write_text("<AIM_AVAILABLES />")
    prof = root / "Profiles" / "UserProfile_JA2113"
    prof.mkdir(parents=True, exist_ok=True)

    merge_line = "MERGE_INI_FILES = Ja2_Options.ini\r\n" if merge_ja2_options else ""
    (root / "Ja2.ini").write_bytes(
        ("[Ja2 Settings]\r\n"
         "VFS_CONFIG_INI = vfs_config.JA2113.ini\r\n"
         f"{merge_line}"
         "SCREEN_RESOLUTION = 5\r\n"
         "PLAY_INTRO = 1\r\n").encode())

    (root / "vfs_config.JA2113.ini").write_text(
        "[vfs_config]\n"
        "PROFILES = Vanilla, v113, UserProf\n\n"
        "[PROFILE_Vanilla]\nLOCATIONS = data_dir\nPROFILE_ROOT = \n\n"
        "[PROFILE_v113]\nLOCATIONS = datav113_dir\nPROFILE_ROOT = \n\n"
        "[PROFILE_UserProf]\nLOCATIONS = uprof_root\n"
        "PROFILE_ROOT = Profiles\\UserProfile_JA2113\nWRITE = true\n\n"
        "[LOC_data_dir]\nTYPE = DIRECTORY\nPATH = Data\n\n"
        "[LOC_datav113_dir]\nTYPE = DIRECTORY\nPATH = Data-1.13\n\n"
        "[LOC_uprof_root]\nTYPE = DIRECTORY\nPATH = \n")

    # Base Ja2_Options in BOTH lower layers (merge test) — v113 must win.
    (root / "Data" / "Ja2_Options.ini").write_bytes(
        b"[System Limit Settings]\r\nMAX_NUMBER_PLAYER_MERCS = 18\r\n"
        b"VANILLA_ONLY_KEY = 7\r\n")
    (d113 / "Ja2_Options.ini").write_bytes(
        b"; modpack canon\r\n[System Limit Settings]\r\n"
        b"MAX_NUMBER_PLAYER_MERCS = 40\r\n"
        b"MAX_NUMBER_ENEMIES_IN_TACTICAL = 64\r\n")
    # An unregistered INI only in Data-1.13.
    (d113 / "Skills_Settings.INI").write_bytes(
        b"; skills canon\r\n[Generic Traits Settings]\r\n"
        b"MAX_NUMBER_OF_TRAITS = 10\r\n")
    return root


@pytest.fixture
def vfs_install(tmp_path: Path) -> Path:
    return make_vfs_install(tmp_path / "install")


@pytest.fixture
def editor(vfs_install: Path) -> IniEditor:
    return IniEditor(parse_vfs_config(vfs_install))


# ──────────────────────────────────────────────────────────────────────────
#  Whitelist / sanitization
# ──────────────────────────────────────────────────────────────────────────


def test_whitelist_rejects_unknown_and_traversal():
    with pytest.raises(IniEditorError):
        canonical_ini_name("NotAReal.ini")
    with pytest.raises(IniEditorError):
        canonical_ini_name("..\\..\\Ja2.ini")
    with pytest.raises(IniEditorError):
        canonical_ini_name("../Data/Ja2_Options.ini")
    assert canonical_ini_name("ja2_options.INI") == "Ja2_Options.ini"
    assert canonical_ini_name("SKILLS_SETTINGS.ini") == "Skills_Settings.INI"


def test_change_validation_rejects_injection():
    with pytest.raises(IniEditorError):
        IniChange("S", "K", "v\r\nINJECT = 1").validate()
    with pytest.raises(IniEditorError):
        IniChange("S", "BAD KEY", "v").validate()
    with pytest.raises(IniEditorError):
        IniChange("S]x", "K", "v").validate()
    with pytest.raises(IniEditorError):
        IniChange("S", "K=2", "v").validate()
    IniChange("Section Name", "GOOD_KEY.2", "any ; value").validate()


def test_override_filename_matches_engine_makepath():
    assert override_filename("Ja2_Options.ini") == "Ja2_Options.Override"
    assert override_filename("Skills_Settings.INI") == "Skills_Settings.Override"


# ──────────────────────────────────────────────────────────────────────────
#  Surgical writer
# ──────────────────────────────────────────────────────────────────────────


def test_surgery_preserves_comments_blanks_and_crlf(tmp_path: Path):
    f = tmp_path / "T.ini"
    f.write_bytes(b"; top doc\r\n\r\n[A]\r\n; key doc\r\nFOO = 1\r\n")
    surgical_upsert(f, [IniChange("A", "FOO", "2")])
    raw = f.read_bytes()
    assert b"; top doc\r\n" in raw and b"; key doc\r\n" in raw
    assert b"FOO = 2\r\n" in raw
    assert raw.count(b"\n") == raw.count(b"\r\n")


def test_surgery_edits_last_duplicate_only(tmp_path: Path):
    f = tmp_path / "T.ini"
    f.write_bytes(b"[A]\r\nFOO = 1\r\nFOO = 3\r\n")
    surgical_upsert(f, [IniChange("A", "FOO", "9")])
    raw = f.read_bytes()
    assert b"FOO = 1\r\n" in raw          # earlier duplicate untouched
    assert b"FOO = 9\r\n" in raw          # last occurrence edited
    assert b"FOO = 3" not in raw
    assert parse_ini_map(raw.decode())["A"]["FOO"] == "9"   # engine sees 9


def test_surgery_delete_removes_all_duplicates(tmp_path: Path):
    f = tmp_path / "T.ini"
    f.write_bytes(b"[A]\r\nFOO = 1\r\nBAR = 2\r\nFOO = 3\r\n")
    surgical_upsert(f, [IniChange("A", "FOO", None)])
    m = parse_ini_map(f.read_bytes().decode())
    assert "FOO" not in m["A"] and m["A"]["BAR"] == "2"


def test_surgery_does_not_revive_commented_keys(tmp_path: Path):
    """A commented `; FOO = old` line must never be uncommented or treated
    as the key (the frozen launcher's bug class)."""
    f = tmp_path / "T.ini"
    f.write_bytes(b"[A]\r\n; FOO = old\r\nFOO = 1\r\n")
    surgical_upsert(f, [IniChange("A", "FOO", "2")])
    raw = f.read_bytes()
    assert b"; FOO = old\r\n" in raw
    assert raw.count(b"\r\nFOO = ") == 1


def test_surgery_creates_missing_section_and_file(tmp_path: Path):
    f = tmp_path / "T.ini"
    f.write_bytes(b"[A]\r\nX = 1\r\n")
    surgical_upsert(f, [IniChange("B", "Y", "2")])
    m = parse_ini_map(f.read_bytes().decode())
    assert m["A"]["X"] == "1" and m["B"]["Y"] == "2"

    g = tmp_path / "New.Override"
    surgical_upsert(g, [IniChange("S", "K", "V")], new_file_header=";; hdr")
    raw = g.read_bytes()
    assert raw.startswith(b";; hdr")
    assert parse_ini_map(raw.decode())["S"]["K"] == "V"


def test_surgery_preserves_lf_only_files(tmp_path: Path):
    f = tmp_path / "T.ini"
    f.write_bytes(b"[A]\nX = 1\n")
    surgical_upsert(f, [IniChange("A", "Y", "2")])
    assert b"\r" not in f.read_bytes()


def test_surgery_preserves_unknown_keys_verbatim(tmp_path: Path):
    f = tmp_path / "T.ini"
    f.write_bytes(b"[A]\r\nMODDED_CUSTOM_KEY = special ; keep me\r\nX = 1\r\n")
    surgical_upsert(f, [IniChange("A", "X", "2")])
    assert b"MODDED_CUSTOM_KEY = special ; keep me\r\n" in f.read_bytes()


def test_surgery_selfcheck_restores_preimage(tmp_path: Path, monkeypatch):
    """Force the self-check to fail and verify the pre-image returns."""
    f = tmp_path / "T.ini"
    original = b"[A]\r\nX = 1\r\n"
    f.write_bytes(original)
    real_parse = ie.parse_ini_map
    calls = {"n": 0}

    def corrupt_second_parse(text):
        calls["n"] += 1
        if calls["n"] >= 2:               # the post-write verification parse
            return {"CORRUPT": {"Z": "9"}}
        return real_parse(text)

    monkeypatch.setattr(ie, "parse_ini_map", corrupt_second_parse)
    with pytest.raises(IniEditorError):
        surgical_upsert(f, [IniChange("A", "X", "2")])
    assert f.read_bytes() == original


# ──────────────────────────────────────────────────────────────────────────
#  Effective resolution
# ──────────────────────────────────────────────────────────────────────────


def test_effective_merge_registered_layers_per_key(editor: IniEditor):
    eff = editor.effective("Ja2_Options.ini")
    assert eff["merge_registered"] is True
    sls = eff["sections"]["System Limit Settings"]
    # v113 overrides Data per-key...
    assert sls["MAX_NUMBER_PLAYER_MERCS"] == {
        "value": "40", "source": "v113", "override_active": False}
    # ...but Data-only keys survive the merge (per-key, not whole-file).
    assert sls["VANILLA_ONLY_KEY"]["value"] == "7"
    assert sls["VANILLA_ONLY_KEY"]["source"] == "Vanilla"


def test_effective_unregistered_is_whole_file_top_resolve(vfs_install: Path):
    """For an UNregistered file a higher-layer copy shadows the whole
    file — lower-layer-only keys must NOT bleed through."""
    (vfs_install / "Data" / "Skills_Settings.INI").write_bytes(
        b"[Generic Traits Settings]\r\nDATA_ONLY_KEY = 1\r\n")
    editor = IniEditor(parse_vfs_config(vfs_install))
    eff = editor.effective("Skills_Settings.INI")
    assert eff["merge_registered"] is False
    gts = eff["sections"]["Generic Traits Settings"]
    assert gts["MAX_NUMBER_OF_TRAITS"]["value"] == "10"
    assert "DATA_ONLY_KEY" not in gts    # shadowed whole-file by Data-1.13


def test_effective_override_file_wins(vfs_install: Path):
    prof = vfs_install / "Profiles" / "UserProfile_JA2113"
    (prof / "Skills_Settings.Override").write_bytes(
        b"[Generic Traits Settings]\r\nMAX_NUMBER_OF_TRAITS = 12\r\n")
    editor = IniEditor(parse_vfs_config(vfs_install))
    eff = editor.effective("Skills_Settings.INI")
    assert eff["override_present"] is True
    e = eff["sections"]["Generic Traits Settings"]["MAX_NUMBER_OF_TRAITS"]
    assert e["value"] == "12" and e["source"] == "override"
    assert e["override_active"] is True


def test_effective_schema_default_and_stock_baseline(tmp_path: Path):
    install = make_vfs_install(tmp_path / "install")
    baseline = make_vfs_install(tmp_path / "stock")
    (baseline / "Data-1.13" / "Ja2_Options.ini").write_bytes(
        b"[System Limit Settings]\r\nMAX_NUMBER_PLAYER_MERCS = 24\r\n")
    editor = IniEditor(parse_vfs_config(install), baseline_root=baseline)
    eff = editor.effective("Ja2_Options.ini")
    e = eff["sections"]["System Limit Settings"]["MAX_NUMBER_PLAYER_MERCS"]
    assert e["value"] == "40" and e["stock_value"] == "24"
    # A schema-known key absent from every layer resolves to the schema
    # default with source='default' (spot one from the real shipped schema).
    found_default = any(
        entry.get("source") == "default"
        for sect in eff["sections"].values() for entry in sect.values())
    assert found_default, "schema defaults should fill unset keys"


def test_effective_ja2_ini_is_root_direct(editor: IniEditor):
    eff = editor.effective("Ja2.ini")
    e = eff["sections"]["Ja2 Settings"]["SCREEN_RESOLUTION"]
    assert e["value"] == "5" and e["source"] == "ja2_ini"


# ──────────────────────────────────────────────────────────────────────────
#  Write targets + apply
# ──────────────────────────────────────────────────────────────────────────


def test_write_target_canon_is_in_place(editor: IniEditor, vfs_install: Path):
    t = editor.write_target("Ja2_Options.ini", "canon")
    assert t == (vfs_install / "Data-1.13" / "Ja2_Options.ini").resolve()


def test_write_target_override_is_profile_override_file(editor: IniEditor, vfs_install: Path):
    t = editor.write_target("Skills_Settings.INI", "override")
    assert t == (vfs_install / "Profiles" / "UserProfile_JA2113"
                 / "Skills_Settings.Override").resolve()
    t2 = editor.write_target("Ja2_Options.ini", "override")
    assert t2.name == "Ja2_Options.Override"


def test_write_target_ai_ini_play_mode_refused(editor: IniEditor):
    with pytest.raises(IniEditorError) as exc:
        editor.write_target("AI.ini", "override")
    assert exc.value.code == "PLAY_MODE_UNSUPPORTED"
    # Author mode is still allowed.
    editor.write_target("AI.ini", "canon")


def test_write_target_ja2_ini_is_root(editor: IniEditor, vfs_install: Path):
    for target in ("canon", "override"):
        assert editor.write_target("Ja2.ini", target) == vfs_install / "Ja2.ini"


def test_apply_changes_play_mode_roundtrip(editor: IniEditor, vfs_install: Path):
    out = editor.apply_changes(
        [IniChange("Generic Traits Settings", "MAX_NUMBER_OF_TRAITS", "12",
                   ini_file="Skills_Settings.INI")],
        target="override")
    assert out["applied"] == 1
    ovr = vfs_install / "Profiles" / "UserProfile_JA2113" / "Skills_Settings.Override"
    assert ovr.is_file()
    assert b"MercForge" in ovr.read_bytes()       # provenance header present
    eff = editor.effective("Skills_Settings.INI")
    assert eff["sections"]["Generic Traits Settings"]["MAX_NUMBER_OF_TRAITS"]["value"] == "12"
    # canon untouched
    assert b"MAX_NUMBER_OF_TRAITS = 10" in (
        vfs_install / "Data-1.13" / "Skills_Settings.INI").read_bytes()
    # ...and the overrides listing sees it
    ovs = editor.overrides()
    assert any(o["key"] == "MAX_NUMBER_OF_TRAITS" and o["value"] == "12" for o in ovs)


def test_apply_changes_author_mode_edits_canon(editor: IniEditor, vfs_install: Path):
    editor.apply_changes(
        [IniChange("System Limit Settings", "MAX_NUMBER_ENEMIES_IN_TACTICAL",
                   "48", ini_file="Ja2_Options.ini")],
        target="canon")
    raw = (vfs_install / "Data-1.13" / "Ja2_Options.ini").read_bytes()
    assert b"MAX_NUMBER_ENEMIES_IN_TACTICAL = 48\r\n" in raw
    assert raw.startswith(b"; modpack canon")     # comment preserved


def test_apply_changes_blocked_while_game_running(editor: IniEditor, monkeypatch):
    monkeypatch.setattr(ie, "game_running", lambda exe_name="ja2.exe": True)
    with pytest.raises(IniEditorError) as exc:
        editor.apply_changes(
            [IniChange("S", "K", "1", ini_file="Skills_Settings.INI")],
            target="override")
    assert exc.value.code == "GAME_RUNNING"


def test_apply_changes_dry_run_writes_nothing(editor: IniEditor, vfs_install: Path):
    out = editor.apply_changes(
        [IniChange("Generic Traits Settings", "MAX_NUMBER_OF_TRAITS", "12",
                   ini_file="Skills_Settings.INI")],
        target="override", dry_run=True)
    assert out["dry_run"] is True and out["applied"] == 0
    assert not (vfs_install / "Profiles" / "UserProfile_JA2113"
                / "Skills_Settings.Override").exists()


def test_legacy_install_refuses_override_writes(tmp_path: Path):
    root = tmp_path / "legacy"
    (root / "Data-1.13").mkdir(parents=True)
    (root / "JA2.exe").touch()
    (root / "Ja2.ini").write_text("[Ja2 Settings]\nSCREEN_RESOLUTION = 5\n")
    editor = IniEditor(parse_vfs_config(root))
    with pytest.raises(Exception):    # VfsConfigError from resolve_override_write
        editor.write_target("Skills_Settings.INI", "override")
    # canon still works (legacy mod-content layer)
    assert editor.write_target("Skills_Settings.INI", "canon")


# ──────────────────────────────────────────────────────────────────────────
#  Routes
# ──────────────────────────────────────────────────────────────────────────


@pytest.fixture
def client() -> TestClient:
    return TestClient(create_app())


@pytest.fixture
def registered(client: TestClient, tmp_path: Path) -> dict:
    root = make_vfs_install(tmp_path / "route_install")
    resp = client.post("/api/v1/installs", json={"path": str(root)})
    assert resp.status_code == 200, resp.text
    info = resp.json()
    client.post("/api/v1/installs/active", json={"install_id": info["id"]})
    info["root"] = str(root)
    return info


def test_route_schemas(client: TestClient, registered: dict):
    r = client.get("/api/v1/ini/schemas")
    assert r.status_code == 200, r.text
    body = r.json()
    assert "Ja2_Options.ini" in body["editable"]
    assert body["writable_profile"] == "UserProf"


def test_route_schema_known_and_unknown(client: TestClient, registered: dict):
    r = client.get("/api/v1/ini/schema/Ja2_Options.ini")
    assert r.status_code == 200
    assert r.json()["ini_file"] == "Ja2_Options.ini"
    r2 = client.get("/api/v1/ini/schema/Bogus.ini")
    assert r2.status_code == 404
    assert r2.json()["detail"]["error"] == "INI_FILE_UNKNOWN"


def test_route_effective_and_overrides(client: TestClient, registered: dict):
    r = client.get("/api/v1/ini/effective/Ja2_Options.ini")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["merge_registered"] is True
    assert body["sections"]["System Limit Settings"]["MAX_NUMBER_PLAYER_MERCS"]["value"] == "40"
    r2 = client.get("/api/v1/ini/overrides")
    assert r2.status_code == 200
    assert r2.json()["overrides"] == []


def test_route_changes_apply_and_backup(client: TestClient, registered: dict):
    payload = {
        "target": "override",
        "changes": [{
            "ini_file": "Skills_Settings.INI",
            "section": "Generic Traits Settings",
            "key": "MAX_NUMBER_OF_TRAITS",
            "value": "12",
        }],
    }
    r = client.post("/api/v1/ini/changes", json=payload)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["applied"] == 1 and body["backup_id"]
    ovr = Path(registered["root"]) / "Profiles" / "UserProfile_JA2113" / "Skills_Settings.Override"
    assert ovr.is_file()
    # the override now shows in effective + overrides
    eff = client.get("/api/v1/ini/effective/Skills_Settings.INI").json()
    e = eff["sections"]["Generic Traits Settings"]["MAX_NUMBER_OF_TRAITS"]
    assert e["value"] == "12" and e["override_active"] is True
    ovs = client.get("/api/v1/ini/overrides").json()["overrides"]
    assert len(ovs) == 1 and ovs[0]["value"] == "12"
    # delete reverts to canon
    r2 = client.post("/api/v1/ini/changes", json={
        "target": "override",
        "changes": [{
            "ini_file": "Skills_Settings.INI",
            "section": "Generic Traits Settings",
            "key": "MAX_NUMBER_OF_TRAITS",
            "delete": True,
        }],
    })
    assert r2.status_code == 200, r2.text
    eff2 = client.get("/api/v1/ini/effective/Skills_Settings.INI").json()
    assert eff2["sections"]["Generic Traits Settings"]["MAX_NUMBER_OF_TRAITS"]["value"] == "10"


def test_route_changes_dry_run_and_warnings(client: TestClient, registered: dict):
    payload = {
        "target": "override",
        "dry_run": True,
        "changes": [{
            "ini_file": "Ja2_Options.ini",
            "section": "System Limit Settings",
            "key": "MAX_NUMBER_PLAYER_MERCS",
            "value": "9999",            # way above engine max 254
        }],
    }
    r = client.post("/api/v1/ini/changes", json=payload)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["dry_run"] is True and body["applied"] == 0
    assert body["results"][0]["warning"] is not None
    assert "254" in body["results"][0]["warning"]
    ovr = Path(registered["root"]) / "Profiles" / "UserProfile_JA2113" / "Ja2_Options.Override"
    assert not ovr.exists()


def test_route_changes_game_running_409(client: TestClient, registered: dict, monkeypatch):
    monkeypatch.setattr(ie, "game_running", lambda exe_name="ja2.exe": True)
    r = client.post("/api/v1/ini/changes", json={
        "target": "override",
        "changes": [{
            "ini_file": "Skills_Settings.INI",
            "section": "S", "key": "K", "value": "1",
        }],
    })
    assert r.status_code == 409
    assert r.json()["detail"]["error"] == "GAME_RUNNING"


def test_route_changes_ai_ini_play_refused(client: TestClient, registered: dict):
    r = client.post("/api/v1/ini/changes", json={
        "target": "override",
        "changes": [{
            "ini_file": "AI.ini",
            "section": "Modularized Tactical AI", "key": "NumFactories", "value": "12",
        }],
    })
    assert r.status_code == 422
    assert r.json()["detail"]["error"] == "PLAY_MODE_UNSUPPORTED"
