"""Regression coverage for the standalone sector generator's seed writer."""
from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path

import pytest

import generate_sector as generator
from mercwizard_core.mapforge_engine import building_library


def test_ensure_seed_holds_physical_root_lock_through_atomic_write(
    tmp_path: Path, monkeypatch
):
    """The seed existence check, source scan, and replacement are one lock scope."""
    install_root = tmp_path / "Game"
    seed_path = install_root / "Data-1.13" / "Maps" / "GENSEED.DAT"
    source = {"name": "generic.dat", "kind": "slf"}
    events: list[str] = []
    lock_held = False

    @contextmanager
    def fake_install_lock(root: str | Path):
        nonlocal lock_held
        assert Path(root) == install_root
        events.append("lock-enter")
        lock_held = True
        try:
            yield
        finally:
            lock_held = False
            events.append("lock-exit")

    real_exists = Path.exists

    def observe_seed_existence(path: Path) -> bool:
        if path == seed_path:
            assert lock_held, "seed existence check escaped the install-root lock"
            events.append("exists")
        return real_exists(path)

    def read_source(_source: dict) -> bytes:
        assert lock_held, "seed build escaped the install-root lock"
        events.append("build")
        return b"generic-ts0-seed"

    def atomic_write(path: Path, data: bytes) -> None:
        assert lock_held, "seed write escaped the install-root lock"
        events.append("atomic-write")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)

    monkeypatch.setattr(generator, "INSTALL", str(install_root))
    monkeypatch.setattr(generator, "SEED_DAT", str(seed_path))
    monkeypatch.setattr(generator, "OUT_DAT", str(install_root / "Data-1.13/Maps/GENSECTOR.DAT"))
    monkeypatch.setattr(generator, "cross_process_install_root_lock", fake_install_lock, raising=False)
    monkeypatch.setattr(generator, "write_bytes_atomic", atomic_write, raising=False)
    monkeypatch.setattr(Path, "exists", observe_seed_existence)
    monkeypatch.setattr(building_library, "list_map_sources", lambda root: [source])
    monkeypatch.setattr(building_library, "_read_source_bytes", read_source)
    monkeypatch.setattr(building_library, "_header_tileset", lambda _data: generator.TILESET)

    generator.ensure_seed()

    assert seed_path.read_bytes() == b"generic-ts0-seed"
    assert events == ["lock-enter", "exists", "build", "atomic-write", "lock-exit"]


def test_ensure_seed_does_not_hide_atomic_write_failure(tmp_path: Path, monkeypatch):
    install_root = tmp_path / "Game"
    seed_path = install_root / "Data-1.13" / "Maps" / "GENSEED.DAT"
    source = {"name": "generic.dat", "kind": "slf"}
    monkeypatch.setattr(generator, "INSTALL", str(install_root))
    monkeypatch.setattr(generator, "SEED_DAT", str(seed_path))
    monkeypatch.setattr(generator, "OUT_DAT", str(install_root / "Data-1.13/Maps/GENSECTOR.DAT"))
    monkeypatch.setattr(generator, "cross_process_install_root_lock", lambda _root: contextmanager(lambda: (yield))())
    monkeypatch.setattr(building_library, "list_map_sources", lambda _root: [source])
    monkeypatch.setattr(building_library, "_read_source_bytes", lambda _source: b"generic-ts0-seed")
    monkeypatch.setattr(building_library, "_header_tileset", lambda _data: generator.TILESET)

    def fail_atomic(_path: Path, _data: bytes) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(generator, "write_bytes_atomic", fail_atomic)
    with pytest.raises(OSError, match="disk full"):
        generator.ensure_seed()
