from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
import zipfile

import pytest

from mercwizard_core.bundle.manifest import WmercManifest
from mercwizard_core.models import Merc
from tools import megapack_export
from tools.megapack_export import (
    ExportItem,
    build_parser as build_export_parser,
    create_staging_root,
    execute_export,
    load_export_plan,
)
from tools.megapack_reconcile import build_physical_manifest, reconcile_pack


SOURCE_COUNTS = {
    "ai_modpack": 213,
    "aimnas": 214,
    "arulco_revisited": 184,
    "arulco_vacations": 224,
    "deidranna_lives": 213,
    "fff_ww2": 148,
    "redux": 7,
    "sdo": 1,
    "sog69_vietnam": 213,
    "type_p": 213,
    "urban_chaos": 232,
    "vanilla_113": 214,
    "vengeance": 228,
}
MISSING_FFF = (
    "073_Darrel_Jr.wmerc",
    "074_Alish_Perkopoulos.wmerc",
    "075_Queen_Deidranna.wmerc",
    "076_Auntie.wmerc",
    "077_Enrico_Chivaldori.wmerc",
)


def _write_json(path: Path, value) -> None:
    path.write_text(json.dumps(value), encoding="utf-8")


def _write_minimal_wmerc(path: Path, *, slot: int, name: str, merc_type: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    manifest = WmercManifest(merc=Merc(
        uiIndex=slot,
        ubFaceIndex=slot,
        Type=merc_type,
        zName=name,
        zNickname=name.split()[0],
        bStrength=78,
        bAgility=70,
        bDexterity=69,
        bWisdom=68,
        bLife=67,
        bMarksmanship=66,
        bLeadership=65,
        bMechanical=64,
        bExplosive=63,
        bMedical=62,
    ))
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr(
            "manifest.json", json.dumps(manifest.model_dump(mode="json"))
        )


def _make_discrepant_pack(root: Path) -> None:
    log_rows = []
    for source, count in SOURCE_COUNTS.items():
        for index in range(count):
            name = f"{index:03d}_{source}_{index:03d}.wmerc"
            rel = Path("bundles") / source / name
            path = root / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(f"{source}:{index}".encode("ascii"))
            log_rows.append({
                "source": source,
                "uiIndex": index,
                "bundle_path": rel.as_posix(),
                "error": None,
            })
    for name in MISSING_FFF:
        log_rows.append({
            "source": "fff_ww2",
            "uiIndex": int(name[:3]),
            "bundle_path": f"bundles/fff_ww2/{name}",
            "error": None,
        })
    (root / "_export_log.jsonl").write_text(
        "".join(json.dumps(row) + "\n" for row in log_rows), encoding="utf-8"
    )
    (root / "_delta_audit.md").write_text(
        "**Total bundles the export will produce: 2521**\n", encoding="utf-8"
    )
    _write_json(root / "_scan_mod_prototype.json", {
        "source": "mod_prototype",
        "totals": {"will_export": 212},
        "results": [],
    })


def test_reconciliation_preserves_the_observed_2304_vs_2309_discrepancy(
    tmp_path: Path,
) -> None:
    _make_discrepant_pack(tmp_path)

    report = reconcile_pack(tmp_path)

    assert report["manifest"]["file_count"] == 2304
    assert len(report["manifest"]["files"]) == 2304
    assert all(len(entry["sha256"]) == 64 for entry in report["manifest"]["files"])
    assert report["execution_log"]["success_count"] == 2309
    assert report["execution_log"]["error_count"] == 0
    assert report["execution_log"]["missing_physical_paths"] == [
        f"bundles/fff_ww2/{name}" for name in MISSING_FFF
    ]
    assert report["forecast"]["total"] == 2521
    assert report["forecast"]["absent_sources"] == {
        "mod_prototype": 212,
    }


def test_reconciliation_reports_duplicate_catalog_paths(tmp_path: Path) -> None:
    bundle = tmp_path / "bundles" / "source" / "001_Test.wmerc"
    bundle.parent.mkdir(parents=True)
    bundle.write_bytes(b"bundle")
    catalog = tmp_path / "catalog_flat.json"
    _write_json(catalog, [{"path": "bundles/source/001_Test.wmerc"}] * 2)

    report = reconcile_pack(tmp_path, catalog_paths=[catalog])

    assert report["catalogs"]["catalog_flat.json"]["duplicate_paths"] == [
        "bundles/source/001_Test.wmerc"
    ]


def test_reconciliation_reports_stale_absolute_log_paths(tmp_path: Path) -> None:
    bundle = tmp_path / "bundles" / "source" / "001_Test.wmerc"
    bundle.parent.mkdir(parents=True)
    bundle.write_bytes(b"bundle")
    stale = Path("C:/old/location/bundles/source/001_Test.wmerc")
    (tmp_path / "_export_log.jsonl").write_text(
        json.dumps({"source": "source", "bundle_path": str(stale), "error": None}) + "\n",
        encoding="utf-8",
    )

    report = reconcile_pack(tmp_path)

    assert report["execution_log"]["stale_absolute_paths"] == [str(stale)]


def test_reconciliation_reports_corrupt_distribution_zip(tmp_path: Path) -> None:
    archive = tmp_path / "broken.zip"
    archive.write_bytes(b"not a zip")

    report = reconcile_pack(tmp_path, zip_paths=[archive])

    assert report["archives"]["broken.zip"]["error"] == "corrupt_zip"


def test_reconciliation_reports_catalog_entry_without_physical_file(
    tmp_path: Path,
) -> None:
    catalog = tmp_path / "catalog_flat.json"
    _write_json(catalog, [{"path": "bundles/source/404_Missing.wmerc"}])

    report = reconcile_pack(tmp_path, catalog_paths=[catalog])

    assert report["catalogs"]["catalog_flat.json"]["missing_physical_paths"] == [
        "bundles/source/404_Missing.wmerc"
    ]


def test_reconciliation_reads_nested_bundle_path_catalog_entries(tmp_path: Path) -> None:
    catalog = tmp_path / "catalog.json"
    _write_json(catalog, [{
        "character_id": "missing",
        "variants": [{"bundle_path": "bundles/source/404_Missing.wmerc"}],
    }])

    report = reconcile_pack(tmp_path, catalog_paths=[catalog])

    assert report["catalogs"]["catalog.json"]["entry_count"] == 1
    assert report["catalogs"]["catalog.json"]["missing_physical_paths"] == [
        "bundles/source/404_Missing.wmerc"
    ]


def test_lite_catalog_is_compared_to_the_lite_tree(tmp_path: Path) -> None:
    bundle = tmp_path / "bundles_lite" / "source" / "001_Test.wmerc"
    bundle.parent.mkdir(parents=True)
    bundle.write_bytes(b"lite")
    catalog = tmp_path / "catalog_lite.json"
    _write_json(catalog, [{
        "chosen_variant": {"bundle_path": "bundles_lite/source/001_Test.wmerc"},
    }])

    report = reconcile_pack(tmp_path)

    assert report["lite_manifest"]["file_count"] == 1
    assert report["catalogs"]["catalog_lite.json"]["missing_physical_paths"] == []


def test_distribution_metadata_adds_new_bundle_to_flat_and_grouped_catalogs(
    tmp_path: Path,
) -> None:
    template = tmp_path / "template"
    staging = tmp_path / "staging"
    template.mkdir()
    staging.mkdir()
    _write_json(template / "catalog_flat.json", [])
    _write_json(template / "catalog.json", [{
        "character_id": "darrel_jr",
        "canonical_name": "Darrel Jr.",
        "merc_class": "IMP",
        "vanilla_slot": 73,
        "variants": [],
    }])
    _write_json(template / "catalog_lite.json", [])
    bundle = staging / "bundles" / "fff_ww2" / "073_Darrel_Jr.wmerc"
    _write_minimal_wmerc(bundle, slot=73, name="Darrel Jr.", merc_type=3)

    rebuild = getattr(
        megapack_export, "rebuild_distribution_metadata", lambda *_args, **_kwargs: None
    )
    rebuild(template, staging)

    flat = json.loads((staging / "catalog_flat.json").read_text())
    grouped = json.loads((staging / "catalog.json").read_text())
    assert flat == [{
        "path": "bundles/fff_ww2/073_Darrel_Jr.wmerc",
        "source_mod": "fff_ww2",
        "source_slot": 73,
        "name": "Darrel Jr.",
        "nickname": "Darrel",
        "merc_class": "IMP",
        "face_index": 73,
        "has_voice": False,
        "voice_count": 0,
        "has_face": False,
        "stats": {
            "str": 78, "agi": 70, "dex": 69, "wis": 68, "life": 67,
            "mark": 66, "lead": 65, "mech": 64, "exp": 63, "med": 62,
        },
    }]
    assert grouped[0]["variants"][0]["bundle_path"] == flat[0]["path"]
    assert grouped[0]["variants"][0]["source_mod"] == "fff_ww2"


def test_distribution_metadata_records_excluded_scan_as_non_forecast(
    tmp_path: Path,
) -> None:
    template = tmp_path / "template"
    staging = tmp_path / "staging"
    template.mkdir()
    (staging / "bundles").mkdir(parents=True)
    _write_json(template / "catalog_flat.json", [])
    _write_json(template / "catalog.json", [])
    _write_json(template / "_scan_mod_prototype.json", {
        "source": "mod_prototype",
        "totals": {"will_export": 212},
    })

    megapack_export.rebuild_distribution_metadata(
        template, staging, excluded_sources={"mod_prototype"}
    )
    report = reconcile_pack(staging, zip_paths=[])

    assert json.loads((staging / "_excluded_sources.json").read_text()) == [
        "mod_prototype"
    ]
    assert report["forecast"]["absent_sources"] == {}


def test_distribution_zip_members_are_compared_to_physical_manifest(
    tmp_path: Path,
) -> None:
    bundle = tmp_path / "bundles" / "source" / "001_Test.wmerc"
    bundle.parent.mkdir(parents=True)
    bundle.write_bytes(b"bundle")
    archive = tmp_path / "pack.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("bundles/source/404_Missing.wmerc", b"other")

    report = reconcile_pack(tmp_path, zip_paths=[archive])

    assert report["archives"]["pack.zip"]["missing_members"] == [
        "bundles/source/001_Test.wmerc"
    ]
    assert report["archives"]["pack.zip"]["extra_members"] == [
        "bundles/source/404_Missing.wmerc"
    ]


def test_full_distribution_archive_contains_every_physical_bundle_once(
    tmp_path: Path,
) -> None:
    for rel, data in (
        ("bundles/a/001_One.wmerc", b"one"),
        ("bundles/b/002_Two.wmerc", b"two"),
    ):
        path = tmp_path / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)

    build_archive = getattr(
        megapack_export, "build_full_archive", lambda root: root / "missing.zip"
    )
    archive = build_archive(tmp_path)

    with zipfile.ZipFile(archive) as zipped:
        assert zipped.namelist() == [
            "bundles/a/001_One.wmerc",
            "bundles/b/002_Two.wmerc",
        ]


def test_export_cli_exposes_required_recovery_options() -> None:
    parser = build_export_parser()

    args = parser.parse_args([
        "--source", "_scan_fff_ww2.json",
        "--out", "staging",
        "--resume-manifest", "old/manifest.json",
        "--template-root", "old-pack",
        "--dry-run",
        "--include-mod-prototype",
    ])

    assert args.source == ["_scan_fff_ww2.json"]
    assert args.out == "staging"
    assert args.resume_manifest == "old/manifest.json"
    assert args.template_root == "old-pack"
    assert args.dry_run is True
    assert args.include_mod_prototype is True


def test_export_cli_runs_as_a_direct_script() -> None:
    script = Path(__file__).resolve().parents[1] / "tools" / "megapack_export.py"

    completed = subprocess.run(
        [sys.executable, str(script), "--help"],
        cwd=script.parents[1],
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    assert "--resume-manifest" in completed.stdout


def test_export_plan_requires_explicit_mod_prototype_opt_in(tmp_path: Path) -> None:
    scan = tmp_path / "_scan_mod_prototype.json"
    _write_json(scan, {
        "source": "mod_prototype",
        "install_root": "C:/game/mod_prototype",
        "totals": {"will_export": 1},
        "results": [{"uiIndex": 0, "name": "Narg", "verdict": "modified"}],
    })

    with pytest.raises(ValueError, match="include-mod-prototype"):
        load_export_plan([scan], include_mod_prototype=False)

    plan = load_export_plan([scan], include_mod_prototype=True)
    assert [(item.source, item.ui_index, item.filename) for item in plan] == [
        ("mod_prototype", 0, "000_Narg.wmerc")
    ]


def test_export_plan_uses_explicit_source_install_override(tmp_path: Path) -> None:
    stale_root = tmp_path / "retired-name"
    active_root = tmp_path / "actual-install"
    scan = tmp_path / "_scan_fff_ww2.json"
    _write_json(scan, {
        "source": "fff_ww2",
        "install_root": str(stale_root),
        "totals": {"will_export": 1},
        "results": [{"uiIndex": 73, "name": "Darrel Jr", "verdict": "modified"}],
    })

    plan = load_export_plan(
        [scan], install_overrides={"fff_ww2": active_root}
    )

    assert plan[0].install_root == active_root


def test_resume_manifest_seeds_all_verified_files_before_delta_export(
    tmp_path: Path,
) -> None:
    previous = tmp_path / "previous"
    prior_bundle = previous / "bundles" / "vanilla_113" / "000_Barry.wmerc"
    prior_bundle.parent.mkdir(parents=True)
    prior_bundle.write_bytes(b"prior")
    prior_manifest = build_physical_manifest(previous)
    prior_manifest_path = previous / "manifest.json"
    _write_json(prior_manifest_path, prior_manifest)

    staging = tmp_path / "staging"
    staging.mkdir()
    plan = [ExportItem(
        source="fff_ww2",
        install_root=tmp_path / "fff",
        ui_index=73,
        name="Darrel Jr",
        verdict="modified",
        differing_components=("identity",),
        filename="073_Darrel_Jr.wmerc",
    )]

    def fake_exporter(**kwargs):
        Path(kwargs["out_path"]).write_bytes(b"restored")

    result = execute_export(
        plan,
        staging,
        resume_manifest=prior_manifest_path,
        exporter=fake_exporter,
    )

    assert (staging / "bundles/vanilla_113/000_Barry.wmerc").read_bytes() == b"prior"
    assert (staging / "bundles/fff_ww2/073_Darrel_Jr.wmerc").read_bytes() == b"restored"
    assert result["success_count"] == 2
    assert json.loads((staging / "manifest.json").read_text())["file_count"] == 2
    log_rows = [json.loads(line) for line in (staging / "_export_log.jsonl").read_text().splitlines()]
    assert [row["bundle_path"] for row in log_rows] == [
        "bundles/vanilla_113/000_Barry.wmerc",
        "bundles/fff_ww2/073_Darrel_Jr.wmerc",
    ]


def test_export_maps_catalog_source_to_supported_bundle_compat(tmp_path: Path) -> None:
    staging = tmp_path / "staging"
    staging.mkdir()
    plan = [ExportItem(
        source="fff_ww2",
        install_root=tmp_path / "fff",
        ui_index=73,
        name="Darrel Jr",
        verdict="modified",
        differing_components=(),
        filename="073_Darrel_Jr.wmerc",
    )]
    observed = []

    def fake_exporter(**kwargs):
        observed.append(kwargs["intended_mod"])
        Path(kwargs["out_path"]).write_bytes(b"restored")

    execute_export(plan, staging, exporter=fake_exporter)

    assert observed == ["any"]


def test_export_staging_root_is_versioned_and_never_reused(tmp_path: Path) -> None:
    staging = create_staging_root(tmp_path, version="2026-08-12T010203Z")

    assert staging == tmp_path / "megapack-2026-08-12T010203Z.staging"
    assert staging.is_dir()
    with pytest.raises(FileExistsError):
        create_staging_root(tmp_path, version="2026-08-12T010203Z")
