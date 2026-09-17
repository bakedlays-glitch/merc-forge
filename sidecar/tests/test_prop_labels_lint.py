"""Selected-tileset naming gate regressions; no game install or UI required."""
from __future__ import annotations

import json
import struct

import pytest

from mercwizard_core.mapforge import prop_labels as PL
from tools import prop_labels_lint as lint


def _sti(path, frames: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    header = bytearray(30)
    header[:4] = b"STCI"
    struct.pack_into("<H", header, 28, frames)
    path.write_bytes(header)


def test_selected_xml_and_tileset_root_are_used(tmp_path, monkeypatch, capsys):
    live = tmp_path / "live"
    live.mkdir()
    (live / "Ja2Set.dat.xml").write_text(
        '<root><Tileset index="89"><Files><file index="74">nv_unrelated.sti</file></Files></Tileset></root>',
        encoding="utf-8")
    selected = tmp_path / "selected"
    selected.mkdir()
    xml = selected / "Ja2Set.dat.xml"
    xml.write_text(
        '<root><Tileset index="89"><Files><file index="74">nv_selected.sti</file></Files></Tileset></root>',
        encoding="utf-8")
    _sti(selected / "tilesets/89/nv_selected.sti", 2)
    catalog = tmp_path / "labels.json"
    catalog.write_text(json.dumps({"custom_prefixes": ["nv_"], "aliases": {},
        "sheets": {"nv_selected.sti": {"name": "Selected art", "category": "furniture",
            "per_sub": {"1": "Prop A", "2": "Prop B"}}}}), encoding="utf-8")
    monkeypatch.setattr(PL, "_JSON", catalog)
    monkeypatch.setattr("sys.argv", ["prop_labels_lint.py", "--install", str(live),
        "--xml", str(xml), "--tileset-root", str(selected / "tilesets"),
        "--tileset", "89", "--strict"])
    assert lint.main() == 0
    assert "1 registered custom sheets named OK" in capsys.readouterr().out
    PL.reload()


def test_sparse_per_sub_names_fail_even_when_dict_length_matches():
    rec = {"category": "furniture", "per_sub": {"1": "Chair", "3": "Desk"}}
    assert lint.missing_frame_names(rec, 2, strict=False) == [2]
    assert lint.missing_frame_names({"category": "furniture"}, 2, strict=True) == [1, 2]
    assert lint.missing_frame_names({"category": "terrain"}, 400, strict=True) == []
    assert lint.invalid_frame_keys(rec, 2) == ["3"]


def test_naming_uses_basename_prefix_not_substring(monkeypatch):
    monkeypatch.setattr(PL, "_CACHE", {"custom_prefixes": ["nv_"], "sheets": {}, "aliases": {}})
    assert PL.is_custom(r"C:\custom\nv_house.sti")
    assert not PL.is_custom("renv_something.sti")
    PL.reload()


def test_catalog_refreshes_after_json_change(tmp_path, monkeypatch):
    path = tmp_path / "labels.json"
    monkeypatch.setattr(PL, "_JSON", path)
    path.write_text(json.dumps({"custom_prefixes": ["nv_"], "aliases": {},
        "sheets": {"nv_sample.sti": {"name": "Old"}}}), encoding="utf-8")
    PL.reload()
    assert PL.display_name("nv_sample.sti") == "Old"
    path.write_text(json.dumps({"custom_prefixes": ["nv_"], "aliases": {},
        "sheets": {"nv_sample.sti": {"name": "Updated label"}}}), encoding="utf-8")
    assert PL.display_name("nv_sample.sti") == "Updated label"
    PL.reload()


def test_missing_selected_tileset_is_error(tmp_path):
    xml = tmp_path / "Ja2Set.dat.xml"
    xml.write_text('<root><Tileset index="73"><Files/></Tileset></root>', encoding="utf-8")
    with pytest.raises(ValueError, match="Tileset 89 is absent"):
        lint.own_block(xml, 89)


def test_zero_frame_or_malformed_sti_is_unreadable(tmp_path):
    p = tmp_path / "bad.sti"
    _sti(p, 0)
    assert lint.frame_count(p) == -1
    p.write_bytes(b"STCI")
    assert lint.frame_count(p) == -1
