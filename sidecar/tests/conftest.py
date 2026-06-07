"""Shared pytest fixtures."""
from __future__ import annotations

from pathlib import Path

import pytest
from PIL import Image

from mercwizard_core.models import AimBinding, Gear, GearKit, Merc, MercBinding


@pytest.fixture(autouse=True)
def isolate_appdata(monkeypatch, tmp_path):
    """Redirect APPDATA and LOCALAPPDATA to per-test tmp dirs.

    Without this fixture, any test that triggers backup.snapshot() writes to
    the user's real %APPDATA%\\MercWizard\\backups\\. saves.py reads both
    APPDATA and LOCALAPPDATA to scan JA2 save folders — both must be
    redirected to keep tests from hitting the real install's save data.

    state.py already disables its own persistence under pytest (via
    PYTEST_CURRENT_TEST detection), but it still resolves APPDATA, so this
    fixture keeps its env-var reads pointed at tmp too.

    Tests that already isolate (test_bundle.py monkeypatches
    backup_mod._appdata_root directly; test_backup.py passes base=...
    explicitly) coexist with this fixture — their isolation bypasses the
    env-var path entirely, so this fixture is a no-op for them.
    """
    # Use a non-conflicting subdir name so tests that also do
    # `tmp_path / "appdata"` (e.g. test_import_rollback_covers_step_10_mid_failure)
    # don't trip over an existing directory.
    appdata = tmp_path / "_mw_appdata"
    localappdata = tmp_path / "_mw_localappdata"
    appdata.mkdir(exist_ok=True)
    localappdata.mkdir(exist_ok=True)
    monkeypatch.setenv("APPDATA", str(appdata))
    monkeypatch.setenv("LOCALAPPDATA", str(localappdata))
    yield appdata


@pytest.fixture
def sample_merc() -> Merc:
    """A minimal valid Merc model for tests that just need a working instance."""
    return Merc(
        uiIndex=220,
        ubFaceIndex=220,
        Type=1,
        zName="Tycho",
        zNickname="Tycho",
        biographyText="A seasoned Desert Ranger from the Nevada wastes.",
        additionalInfoText="Elite marksmanship.",
    )


@pytest.fixture
def sample_gear() -> Gear:
    return Gear(
        mIndex=220,
        mName="Tycho",
        kits=[GearKit(mWeapon=2, mBig0=71, mBig0Quantity=3, mVest=161)],
    )


@pytest.fixture
def sample_aim_binding() -> AimBinding:
    return AimBinding(
        uiIndex=220,
        description="Tycho",
        ProfilId=220,
        AimBioID=52,
    )


@pytest.fixture
def sample_merc_binding() -> MercBinding:
    """A minimal valid MercBinding — slot 198 (Eskimo), MercBioID 42 (Vengeance)."""
    return MercBinding(
        uiIndex=12,
        Name="Eskimo",
        ProfilId=198,
        MercBioID=42,
        usMoneyPaid=100,
        usDay=2,
    )


@pytest.fixture
def fake_install(tmp_path: Path) -> Path:
    """Create a fake install tree just enough to support write tests.

    Returns the install root. Subdirectories created:
      tmp/Data-1.13/TableData/
      tmp/Data-1.13/BinaryData/
      tmp/Data-1.13/BinaryData/MercEdt/
      tmp/Data-1.13/BinaryData/NPCDATA/
      tmp/Data-1.13/faces/
    """
    root = tmp_path
    (root / "Data-1.13" / "TableData").mkdir(parents=True, exist_ok=True)
    (root / "Data-1.13" / "BinaryData" / "MercEdt").mkdir(parents=True, exist_ok=True)
    (root / "Data-1.13" / "BinaryData" / "NPCDATA").mkdir(parents=True, exist_ok=True)
    (root / "Data-1.13" / "faces").mkdir(parents=True, exist_ok=True)
    return root


@pytest.fixture
def synthetic_portrait_1024() -> Image.Image:
    """A 1024x1024 RGBA image with face-like features for portrait tests."""
    img = Image.new("RGBA", (1024, 1024), (0, 0, 0, 0))
    # Paint a face
    for x in range(200, 800):
        for y in range(100, 900):
            img.putpixel((x, y), (180, 140, 100, 255))
    # Eyes
    for x in range(300, 450):
        for y in range(380, 440):
            img.putpixel((x, y), (50, 50, 50, 255))
    for x in range(550, 700):
        for y in range(380, 440):
            img.putpixel((x, y), (50, 50, 50, 255))
    # Mouth
    for x in range(420, 600):
        for y in range(620, 680):
            img.putpixel((x, y), (120, 60, 60, 255))
    return img


@pytest.fixture
def synthetic_smallface() -> Image.Image:
    """A 48×43 RGBA image with face-like features."""
    img = Image.new("RGBA", (48, 43), (0, 0, 0, 0))
    for x in range(8, 40):
        for y in range(5, 35):
            img.putpixel((x, y), (180, 140, 100, 255))
    for x in range(12, 22):
        for y in range(12, 16):
            img.putpixel((x, y), (50, 50, 50, 255))
    for x in range(26, 36):
        for y in range(12, 16):
            img.putpixel((x, y), (50, 50, 50, 255))
    for x in range(18, 30):
        for y in range(26, 30):
            img.putpixel((x, y), (120, 60, 60, 255))
    return img
