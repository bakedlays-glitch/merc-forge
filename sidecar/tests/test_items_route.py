# sidecar/tests/test_items_route.py
"""Route integration tests for the items editor endpoints."""
from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from main import create_app  # noqa: E402
from routes.state import get_state  # noqa: E402

# ── Sample XML (reused verbatim from unit-test fixtures) ─────────────────────

ITEMS_SAMPLE = (
    "<ITEMLIST>\r\n"
    "\t<ITEM>\r\n\t\t<uiIndex>0</uiIndex>\r\n\t\t<szItemName>Nada</szItemName>\r\n"
    "\t\t<usItemClass>128</usItemClass>\r\n\t\t<ubClassIndex>0</ubClassIndex>\r\n"
    "\t\t<usPrice>0</usPrice>\r\n\t\t<ubCoolness>0</ubCoolness>\r\n"
    "\t\t<ubGraphicType>0</ubGraphicType>\r\n\t\t<ubGraphicNum>0</ubGraphicNum>\r\n\t</ITEM>\r\n"
    "\t<ITEM>\r\n\t\t<uiIndex>1</uiIndex>\r\n\t\t<szItemName>Glock 17</szItemName>\r\n"
    "\t\t<szItemDesc>A pistol.</szItemDesc>\r\n\t\t<usItemClass>2</usItemClass>\r\n"
    "\t\t<ubClassIndex>1</ubClassIndex>\r\n\t\t<usPrice>225</usPrice>\r\n\t\t<ubCoolness>3</ubCoolness>\r\n"
    "\t\t<ubGraphicType>0</ubGraphicType>\r\n\t\t<ubGraphicNum>5</ubGraphicNum>\r\n\t</ITEM>\r\n"
    "</ITEMLIST>"
)

WEAPONS_SAMPLE = (
    "﻿<WEAPONLIST>\r\n"
    "\t<WEAPON>\r\n\t\t<uiIndex>0</uiIndex>\r\n\t\t<szWeaponName>Nothing</szWeaponName>\r\n"
    "\t\t<ubImpact>0</ubImpact>\r\n\t\t<usRange>0</usRange>\r\n\t</WEAPON>\r\n"
    "\t<WEAPON>\r\n\t\t<uiIndex>1</uiIndex>\r\n\t\t<szWeaponName>Glock 17</szWeaponName>\r\n"
    "\t\t<ubImpact>25</ubImpact>\r\n\t\t<usRange>115</usRange>\r\n\t</WEAPON>\r\n"
    "</WEAPONLIST>"
)

AMMO_STRINGS_SAMPLE = (
    "<AMMOLIST>\r\n"
    "\t<AMMO>\r\n\t\t<uiIndex>0</uiIndex>\r\n\t\t<AmmoCaliber>0</AmmoCaliber>\r\n\t</AMMO>\r\n"
    "\t<AMMO>\r\n\t\t<uiIndex>2</uiIndex>\r\n\t\t<AmmoCaliber>9x19mm</AmmoCaliber>\r\n\t</AMMO>\r\n"
    "</AMMOLIST>"
)

AMMO_TYPES_SAMPLE = (
    "<AMMOTYPELIST>\r\n"
    "\t<AMMOTYPE>\r\n\t\t<uiIndex>0</uiIndex>\r\n\t\t<name>Ball</name>\r\n\t</AMMOTYPE>\r\n"
    "</AMMOTYPELIST>"
)


# ── Fixtures ─────────────────────────────────────────────────────────────────

@pytest.fixture
def _reset_state():
    st = get_state()
    st._installs = {}
    st._active_install_id = None
    st._scan_done = False
    yield
    st._installs = {}
    st._active_install_id = None
    st._scan_done = False


@pytest.fixture
def client(_reset_state) -> TestClient:
    return TestClient(create_app())


@pytest.fixture
def active_items_install(client: TestClient, tmp_path: Path) -> dict:
    root = tmp_path / "install"
    table = root / "Data-1.13" / "TableData"
    items_dir = table / "Items"
    items_dir.mkdir(parents=True)
    (root / "JA2.exe").touch()
    (table / "MercProfiles.xml").write_text("<MERCPROFILES />")
    (table / "AIMAvailability.xml").write_text("<AIM_AVAILABLES />")
    (items_dir / "Items.xml").write_bytes(ITEMS_SAMPLE.encode("utf-8"))
    (items_dir / "Weapons.xml").write_bytes(WEAPONS_SAMPLE.encode("utf-8"))
    (items_dir / "AmmoStrings.xml").write_bytes(AMMO_STRINGS_SAMPLE.encode("utf-8"))
    (items_dir / "AmmoTypes.xml").write_bytes(AMMO_TYPES_SAMPLE.encode("utf-8"))
    resp = client.post("/api/v1/installs", json={"path": str(root)})
    assert resp.status_code == 200, resp.text
    info = resp.json()
    client.post("/api/v1/installs/active", json={"install_id": info["id"]})
    return info


# ── Tests ─────────────────────────────────────────────────────────────────────

def test_list_items_returns_schema_and_rows(client: TestClient, active_items_install) -> None:
    r = client.get("/api/v1/items")
    assert r.status_code == 200, r.text
    body = r.json()
    assert any(it["name"] == "Glock 17" for it in body["items"])
    assert any(f["key"] == "usPrice" for f in body["common_schema"])


def test_get_item_resolves_weapon_family(client: TestClient, active_items_install) -> None:
    # Glock 17 = uiIndex 1, class IC_GUN (2), classindex 1 → Weapons row present.
    r = client.get("/api/v1/items/1")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["family"] == "Weapon"
    assert body["class_fields"]["ubImpact"] == 25
    assert any(f["key"] == "usRange" for f in body["class_schema"])


def test_put_item_clamps_and_writes_both_files(client: TestClient, active_items_install) -> None:
    r = client.put("/api/v1/items/1", json={
        "strings": {"szItemName": "Glock 18"},
        "ints": {"ubCoolness": 999},   # clamps to 10
        "class_fields": {"ubImpact": 30},
    })
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is True and body["backup_id"]
    assert any(c["key"] == "ubCoolness" and c["stored"] == 10 for c in body["clamps"])
    # Re-read reflects both files.
    g = client.get("/api/v1/items/1").json()
    assert g["strings"]["szItemName"] == "Glock 18"
    assert g["class_fields"]["ubImpact"] == 30


def test_put_item_refuses_template(client: TestClient, active_items_install) -> None:
    r = client.put("/api/v1/items/0", json={"strings": {"szItemName": "x"},
                                            "ints": {}, "class_fields": {}})
    assert r.status_code == 400, r.text


def test_put_item_missing_returns_404(client, active_items_install):
    r = client.put("/api/v1/items/9999", json={"strings": {"szItemName": "x"},
                                               "ints": {}, "class_fields": {}})
    assert r.status_code == 404


def test_bigitems_catalog_lists_graphics(client: TestClient, active_items_install, tmp_path: Path) -> None:
    """list_bigitem_graphics globs filenames only — an empty STI file is enough."""
    # Locate the install root that active_items_install registered.
    installs = client.get("/api/v1/installs").json()
    install_root = Path(installs[0]["path"])
    bigitems_dir = install_root / "Data-1.13" / "BigItems"
    bigitems_dir.mkdir(parents=True, exist_ok=True)
    (bigitems_dir / "GUN24.STI").write_bytes(b"")  # filename-only glob; content irrelevant
    r = client.get("/api/v1/bigitems-catalog")
    assert r.status_code == 200, r.text
    graphics = r.json()["graphics"]
    assert any(g["type"] == 0 and g["num"] == 24 for g in graphics)


def test_items_payload_has_category_and_counts(client, active_items_install):
    body = client.get("/api/v1/items").json()
    assert any(c["key"] == "guns" and c["count"] >= 1 for c in body["categories"])
    glock = next(i for i in body["items"] if i["ui_index"] == 1)
    assert glock["category"] == "guns"


def test_item_detail_has_enums_and_class_label(client, active_items_install):
    body = client.get("/api/v1/items/1").json()
    assert "WEAPON" not in body["class_label"]  # decoded human bits, e.g. "GUN"
    assert "GUN" in body["class_label"]
    assert "ubCalibre" in body["enum_options"]   # gun has calibre enum


def test_put_rejects_class_change(client, active_items_install):
    r = client.put("/api/v1/items/1", json={
        "strings": {}, "ints": {"usItemClass": 2048}, "class_fields": {}})
    assert r.status_code == 400
    assert r.json()["detail"]["error"] == "CLASS_IMMUTABLE"
