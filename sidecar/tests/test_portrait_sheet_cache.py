"""On-disk portrait-sheet cache (routes/roster.py).

The in-memory sheet cache is empty on every sidecar launch, so the disk
tier is what makes the FIRST roster view after a launch fast. These tests
exercise the disk get/put round-trip, stale-version pruning, and the
explicit invalidation hook the portrait-compile route relies on.

APPDATA is redirected to tmp_path so the suite never touches the user's
real %APPDATA%/MercWizard/cache.
"""
from __future__ import annotations

import pytest

from routes import roster as R


@pytest.fixture(autouse=True)
def _isolated_cache_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("APPDATA", str(tmp_path))
    # Clear any in-memory entries left by other tests in this process.
    R.invalidate_portrait_sheet_cache(None)
    yield
    R.invalidate_portrait_sheet_cache(None)


def test_disk_sheet_round_trip():
    iid, mtime, size = "install-abc", 12345, "smallface"
    png = b"\x89PNG\r\n\x1a\n-fake-bytes"
    manifest = {"size": size, "cell_w": 48, "cell_h": 43,
                "cells": [{"slot": 1, "x": 0, "y": 0, "face_index": 1}],
                "errors": []}

    assert R._disk_sheet_get(iid, mtime, size) is None  # cold

    R._disk_sheet_put(iid, mtime, size, (png, manifest))
    got = R._disk_sheet_get(iid, mtime, size)
    assert got is not None
    got_png, got_manifest = got
    assert got_png == png
    assert got_manifest["cells"][0]["slot"] == 1
    assert got_manifest["size"] == size


def test_disk_sheet_key_is_mtime_sensitive():
    iid, size = "install-abc", "smallface"
    R._disk_sheet_put(iid, 100, size, (b"a", {"cells": []}))
    # A different MercProfiles.xml mtime (i.e. the merc data changed) must
    # NOT hit the old cache entry.
    assert R._disk_sheet_get(iid, 999, size) is None
    assert R._disk_sheet_get(iid, 100, size) is not None


def test_disk_sheet_put_prunes_stale_versions():
    iid, size = "install-abc", "smallface"
    R._disk_sheet_put(iid, 100, size, (b"old", {"cells": []}))
    R._disk_sheet_put(iid, 200, size, (b"new", {"cells": []}))
    # Only the newest (install, size) pair survives on disk.
    assert R._disk_sheet_get(iid, 100, size) is None
    new = R._disk_sheet_get(iid, 200, size)
    assert new is not None and new[0] == b"new"
    # Exactly one png + one json for this prefix.
    prefix = R._disk_key_prefix(iid, size)
    d = R._sheet_cache_dir()
    assert len(list(d.glob(f"{prefix}__*.png"))) == 1
    assert len(list(d.glob(f"{prefix}__*.json"))) == 1


def test_disk_sheet_get_tolerates_corrupt_json():
    iid, mtime, size = "install-xyz", 7, "smallface"
    R._disk_sheet_put(iid, mtime, size, (b"png", {"cells": []}))
    # Corrupt the JSON sidecar — get() must return None, not raise.
    prefix = R._disk_key_prefix(iid, size)
    d = R._sheet_cache_dir()
    (d / f"{prefix}__{mtime}.json").write_text("{ not valid json", encoding="utf-8")
    assert R._disk_sheet_get(iid, mtime, size) is None


def test_invalidate_one_install_leaves_others():
    a, b, size = "install-A", "install-B", "smallface"
    R._disk_sheet_put(a, 1, size, (b"a", {"cells": []}))
    R._disk_sheet_put(b, 1, size, (b"b", {"cells": []}))

    R.invalidate_portrait_sheet_cache(a)
    assert R._disk_sheet_get(a, 1, size) is None      # cleared
    assert R._disk_sheet_get(b, 1, size) is not None  # untouched


def test_invalidate_all():
    a, b = "install-A", "install-B"
    R._disk_sheet_put(a, 1, "smallface", (b"a", {"cells": []}))
    R._disk_sheet_put(b, 1, "bigface", (b"b", {"cells": []}))
    R.invalidate_portrait_sheet_cache(None)
    assert R._disk_sheet_get(a, 1, "smallface") is None
    assert R._disk_sheet_get(b, 1, "bigface") is None


def test_invalidate_also_clears_in_memory():
    iid, mtime, size = "install-mem", 5, "smallface"
    key = (iid, mtime, size)
    R._portrait_sheet_cache_put(key, (b"png", {"cells": []}))
    assert R._portrait_sheet_cache_get(key) is not None
    R.invalidate_portrait_sheet_cache(iid)
    assert R._portrait_sheet_cache_get(key) is None
