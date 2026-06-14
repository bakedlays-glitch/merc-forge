"""On-disk portrait-sheet cache (routes/roster.py).

The in-memory sheet cache is empty on every sidecar launch, so the disk
tier is what makes the FIRST roster view after a launch fast. These tests
exercise the disk get/put round-trip, stale-version pruning, and the
explicit invalidation hook the portrait-compile route relies on.

APPDATA is redirected to tmp_path so the suite never touches the user's
real %APPDATA%/MercWizard/cache.
"""
from __future__ import annotations

import threading
import time

import pytest

import mercwizard_core.install_context as IC
from routes import roster as R


class _FakeCtx:
    """Minimal InstallContext stand-in: only profiles_xml_path() is read by
    `_portrait_sheet_bytes_and_meta` (the bake itself is monkeypatched)."""

    def __init__(self, profiles_path):
        self._p = profiles_path

    def profiles_xml_path(self):
        return self._p


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


# ── In-flight dedup + generation guard (the hardened bake path) ──────


def _install_fake_ctx(tmp_path, monkeypatch):
    """Point make_install_context at a real on-disk MercProfiles.xml (so
    `.stat().st_mtime_ns` works and the cache key is stable) and return it."""
    pf = tmp_path / "MercProfiles.xml"
    pf.write_text("<MERCPROFILES/>", encoding="utf-8")
    monkeypatch.setattr(IC, "make_install_context", lambda p: _FakeCtx(pf))
    return pf


def test_concurrent_requests_bake_once(tmp_path, monkeypatch):
    """The frontend fires portrait-sheet.png + .json in parallel. On a cold
    cache both miss — the bake must run ONCE, not twice."""
    pf = _install_fake_ctx(tmp_path, monkeypatch)
    calls: list[str] = []

    def fake_bake(ctx, size):
        calls.append(size)
        time.sleep(0.2)  # widen the window so both threads collide on the lock
        return (b"png-bytes", {"size": size, "cells": []})

    monkeypatch.setattr(R, "_bake_portrait_sheet", fake_bake)

    results: list[tuple] = []

    def worker():
        results.append(R._portrait_sheet_bytes_and_meta("iid-dedup", tmp_path, "bigface"))

    t1 = threading.Thread(target=worker)
    t2 = threading.Thread(target=worker)
    t1.start(); t2.start(); t1.join(); t2.join()

    assert len(calls) == 1, "bake ran more than once under concurrency"
    assert len(results) == 2
    assert all(r[0] == b"png-bytes" for r in results)
    # Both the in-memory and on-disk tiers should now be warm.
    mtime = pf.stat().st_mtime_ns
    assert R._portrait_sheet_cache_get(("iid-dedup", mtime, "bigface")) is not None
    assert R._disk_sheet_get("iid-dedup", mtime, "bigface") is not None


def test_invalidate_during_bake_is_not_repopulated(tmp_path, monkeypatch):
    """A portrait recompile invalidates mid-bake (it does NOT bump
    MercProfiles.xml mtime). The in-flight bake must DISCARD its now-stale
    result instead of re-populating mem + disk under the unchanged key."""
    pf = _install_fake_ctx(tmp_path, monkeypatch)

    def fake_bake(ctx, size):
        # Simulate the racing recompile bumping the generation mid-bake.
        R.invalidate_portrait_sheet_cache("iid-gen")
        return (b"stale-bytes", {"size": size, "cells": []})

    monkeypatch.setattr(R, "_bake_portrait_sheet", fake_bake)

    out = R._portrait_sheet_bytes_and_meta("iid-gen", tmp_path, "bigface")
    # This caller still receives its freshly-baked bytes...
    assert out[0] == b"stale-bytes"
    # ...but the result must NOT have been committed to either cache tier,
    # so the next request re-bakes fresher art.
    mtime = pf.stat().st_mtime_ns
    assert R._portrait_sheet_cache_get(("iid-gen", mtime, "bigface")) is None
    assert R._disk_sheet_get("iid-gen", mtime, "bigface") is None


# ── Decode-aware face fallback (largest real face first) ────────────


def _tiny_png():
    import io as _io

    from PIL import Image as _Image

    b = _io.BytesIO()
    _Image.new("RGBA", (4, 4), (10, 20, 30, 255)).save(b, "PNG")
    return b.getvalue()


class _FallbackCtx:
    """ctx stub for _bake_portrait_sheet: returns per-size sentinel bytes so
    the decode-aware fallback can be exercised without real STIs."""

    def __init__(self, per_size):
        self._per_size = per_size  # {size: bytes | None}

    def profiles_xml_path(self):
        from pathlib import Path
        return Path("dummy-profiles.xml")

    def face_sti_bytes(self, face_index, size="smallface"):
        v = self._per_size.get(size)
        return None if v is None else (v, f"src:{size}")


def _patch_bake(monkeypatch, slot, per_size, decode):
    import mercwizard_core.sti_decode as _stidecode
    monkeypatch.setattr(
        R.profiles_xml, "read_all_slots",
        lambda p: {slot: {"ubFaceIndex": str(slot), "zName": "X"}},
    )
    monkeypatch.setattr(_stidecode, "decode_sti_frame_to_png", decode)
    return _FallbackCtx(per_size)


def test_bake_fallback_falls_through_undecodable_larger_face(monkeypatch):
    """BigFace absent, SmallFace present-but-undecodable, 65FACE good →
    the slot still renders (fell through to the 65FACE), tried largest first."""
    decoded_with: list = []

    def decode(b, frame_index=0):
        decoded_with.append(b)
        return None if b == b"SMALL-bad" else _tiny_png()

    ctx = _patch_bake(monkeypatch, 5, {
        "bigface": None, "smallface": b"SMALL-bad",
        "face_65": b"FACE65-good", "face_33": b"FACE33-good",
    }, decode)
    _png, manifest = R._bake_portrait_sheet(ctx, "bigface")

    assert any(c["slot"] == 5 for c in manifest["cells"])
    assert not any(e["slot"] == 5 for e in manifest["errors"])
    # Largest-real-face first: SmallFace decoded before 65FACE; BigFace never
    # decoded (no file); 33FACE not reached (65FACE succeeded).
    assert decoded_with == [b"SMALL-bad", b"FACE65-good"]


def test_bake_fallback_uses_largest_decodable_first(monkeypatch):
    """BigFace absent but SmallFace good → SmallFace wins over the smaller
    variants (no upscaling from a tiny face)."""
    decoded_with: list = []

    def decode(b, frame_index=0):
        decoded_with.append(b)
        return _tiny_png()

    ctx = _patch_bake(monkeypatch, 7, {
        "bigface": None, "smallface": b"SMALL-good",
        "face_65": b"FACE65-good", "face_33": b"FACE33-good",
    }, decode)
    _png, manifest = R._bake_portrait_sheet(ctx, "bigface")

    assert any(c["slot"] == 7 for c in manifest["cells"])
    assert decoded_with == [b"SMALL-good"]  # smaller variants never reached


def test_bake_fallback_no_decodable_face_errors_not_crashes(monkeypatch):
    """No face in any size → slot excluded with an error, never raises."""
    ctx = _patch_bake(monkeypatch, 9, {
        "bigface": None, "smallface": None, "face_65": None, "face_33": None,
    }, lambda b, frame_index=0: None)
    _png, manifest = R._bake_portrait_sheet(ctx, "bigface")

    assert not any(c["slot"] == 9 for c in manifest["cells"])
    assert any(e["slot"] == 9 for e in manifest["errors"])


def test_warm_install_dedups_per_install(tmp_path, monkeypatch):
    """At most one warm thread runs per install at a time; a second
    warm_install while the first is in flight is a no-op."""
    monkeypatch.setenv("APPDATA", str(tmp_path))
    started: list[tuple] = []
    entered = threading.Event()
    release = threading.Event()

    def fake_meta(iid, path, size):
        started.append((iid, size))
        entered.set()
        release.wait(2)
        return (b"x", {"cells": []})

    monkeypatch.setattr(R, "_portrait_sheet_bytes_and_meta", fake_meta)
    monkeypatch.setattr(R, "load_roster", lambda p: [])

    R.warm_install("iid-warm", tmp_path)
    assert entered.wait(2), "warm thread never reached the bake"
    # Second call while the first is still in flight must not spawn a bake.
    R.warm_install("iid-warm", tmp_path)
    release.set()
    time.sleep(0.15)  # let the first warm thread finish + clear the guard
    assert started.count(("iid-warm", "bigface")) == 1
