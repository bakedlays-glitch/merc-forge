"""Tests for MapForge's install-relative tileset asset resolution
(`_install_tileset_paths` / `_tileset_paths_for` in routes/mapforge.py).

This is the load-bearing de-coupling that lets Map Forge render against
ANY user's JA2 1.13 install instead of two hardcoded dev-machine installs.
The renderer module (mercwizard_core.mapforge_engine.iso_renderer) ships
NO install paths; the route derives tileset loose-dirs + Tilesets.slf
archives from the active install and passes them to IsoRenderer/StiCache.

These tests synthesize a fake install on tmp_path — no real JA2 dependency.
"""
from __future__ import annotations

from pathlib import Path


def _make_install(root: Path, layers_with_tilesets: dict[str, dict]) -> None:
    """Build a fake install tree under `root`.

    layers_with_tilesets maps a layer name (e.g. "Data-1.13") to a dict
    with optional keys: "tilesets_dir" (bool — create <layer>/Tilesets/),
    "tilesets_slf" (str — filename to create as <layer>/<name>) and
    "ja2set" (bool — create <layer>/Ja2Set.dat.xml)."""
    for layer, spec in layers_with_tilesets.items():
        layer_root = root / layer
        layer_root.mkdir(parents=True, exist_ok=True)
        if spec.get("tilesets_dir"):
            (layer_root / "Tilesets").mkdir(exist_ok=True)
        slf_name = spec.get("tilesets_slf")
        if slf_name:
            (layer_root / slf_name).write_bytes(b"\x00")  # placeholder file
        if spec.get("ja2set"):
            (layer_root / "Ja2Set.dat.xml").write_text(
                '<?xml version="1.0"?><TilesetFile/>', encoding="utf-8")


def test_install_tileset_paths_collects_loose_dirs_and_slf_across_layers(tmp_path):
    from routes.mapforge import _install_tileset_paths

    inst = tmp_path / "MyMod"
    _make_install(inst, {
        "Data-1.13": {"tilesets_dir": True},
        "Data": {"tilesets_dir": True, "tilesets_slf": "Tilesets.slf"},
    })

    loose, slf = _install_tileset_paths(inst)

    # Loose dirs found, Data-1.13 BEFORE Data (VFS priority order).
    assert loose == [inst / "Data-1.13" / "Tilesets",
                     inst / "Data" / "Tilesets"]
    # The one Tilesets.slf is found.
    assert slf == [inst / "Data" / "Tilesets.slf"]


def test_install_tileset_paths_skips_missing_layers(tmp_path):
    from routes.mapforge import _install_tileset_paths

    inst = tmp_path / "VanillaOnly"
    # Only the vanilla Data layer exists, with just an SLF (no loose dir).
    _make_install(inst, {"Data": {"tilesets_slf": "Tilesets.slf"}})

    loose, slf = _install_tileset_paths(inst)

    assert loose == []  # no Tilesets/ dir anywhere
    assert slf == [inst / "Data" / "Tilesets.slf"]


def test_install_tileset_paths_matches_slf_case_insensitively(tmp_path):
    from routes.mapforge import _install_tileset_paths

    inst = tmp_path / "ShoutyMod"
    _make_install(inst, {"Data-1.13": {"tilesets_slf": "TILESETS.SLF"}})

    _loose, slf = _install_tileset_paths(inst)

    # An uppercase TILESETS.SLF is still recognized.
    assert len(slf) == 1
    assert slf[0].name.lower() == "tilesets.slf"


def test_install_tileset_paths_empty_for_install_without_tilesets(tmp_path):
    from routes.mapforge import _install_tileset_paths

    inst = tmp_path / "Bare"
    (inst / "Data").mkdir(parents=True)  # layer exists but no tilesets

    loose, slf = _install_tileset_paths(inst)

    assert loose == []
    assert slf == []


def test_install_tileset_paths_ignores_unknown_layers(tmp_path):
    """Data-AIM etc. are NOT in the canonical layer set — the resolver
    mirrors mapforge's existing 3-layer `layer_candidates` convention.
    (Documents current behavior so a future widening is a conscious choice.)"""
    from routes.mapforge import _install_tileset_paths

    inst = tmp_path / "AimnasLike"
    _make_install(inst, {
        "Data-AIM": {"tilesets_dir": True},   # not in _TILESET_LAYERS
        "Data-1.13": {"tilesets_dir": True},
    })

    loose, _slf = _install_tileset_paths(inst)

    assert inst / "Data-1.13" / "Tilesets" in loose
    assert inst / "Data-AIM" / "Tilesets" not in loose


def test_tileset_paths_for_falls_back_to_xml_grandparent(tmp_path, monkeypatch):
    """When no install is active in state, `_tileset_paths_for` derives the
    install root from the Ja2Set.dat.xml path (its grandparent), so a direct
    API call still resolves tilesets without a configured active install."""
    import routes.mapforge as mf

    inst = tmp_path / "DerivedMod"
    _make_install(inst, {
        "Data-1.13": {"tilesets_dir": True, "ja2set": True},
        "Data": {"tilesets_slf": "Tilesets.slf"},
    })

    # Force "no active install" so the fallback path runs.
    monkeypatch.setattr(mf, "_active_install_root", lambda: None)

    xml_path = inst / "Data-1.13" / "Ja2Set.dat.xml"
    loose, slf = mf._tileset_paths_for(xml_path)

    assert inst / "Data-1.13" / "Tilesets" in loose
    assert slf == [inst / "Data" / "Tilesets.slf"]


def test_tileset_paths_for_prefers_active_install_over_xml(tmp_path, monkeypatch):
    """The active install is the canonical source — when set, it wins even
    if the xml path points somewhere else."""
    import routes.mapforge as mf

    active = tmp_path / "ActiveMod"
    _make_install(active, {"Data-1.13": {"tilesets_dir": True}})
    other = tmp_path / "OtherMod"
    _make_install(other, {
        "Data-1.13": {"tilesets_dir": True, "ja2set": True},
        "Data": {"tilesets_slf": "Tilesets.slf"},
    })

    monkeypatch.setattr(mf, "_active_install_root", lambda: active)

    # xml lives in `other`, but the active install is `active`.
    loose, slf = mf._tileset_paths_for(other / "Data-1.13" / "Ja2Set.dat.xml")

    assert loose == [active / "Data-1.13" / "Tilesets"]
    assert slf == []  # active has no Tilesets.slf; we did NOT fall through to `other`
