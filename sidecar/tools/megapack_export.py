"""Export scanned MegaPack sources into a new content-addressed staging tree.

The orphaned bytecode from the original exporter is intentionally unused.
This implementation calls the public ``mercwizard_core.bundle.export_merc``
API and treats JSONL as an append-only execution log, never as authority.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import shutil
import sys
from typing import Callable
import zipfile

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from mercwizard_core.bundle import export_merc, read_wmerc
from tools.megapack_reconcile import build_physical_manifest


@dataclass(frozen=True)
class ExportItem:
    source: str
    install_root: Path
    ui_index: int
    name: str
    verdict: str
    differing_components: tuple[str, ...]
    filename: str

    @property
    def relative_path(self) -> Path:
        return Path("bundles") / self.source / self.filename


def _safe_name(name: str) -> str:
    value = re.sub(r"[^A-Za-z0-9-]+", "_", str(name)).strip("_")
    return value or "Unnamed"


def load_export_plan(
    source_paths: list[Path], *, include_mod_prototype: bool = False,
    install_overrides: dict[str, Path] | None = None,
) -> list[ExportItem]:
    items = []
    seen: set[str] = set()
    overrides = install_overrides or {}
    for source_path in source_paths:
        path = Path(source_path)
        scan = json.loads(path.read_text(encoding="utf-8"))
        source = str(scan["source"])
        if source == "mod_prototype" and not include_mod_prototype:
            raise ValueError(
                "mod_prototype requires the explicit --include-mod-prototype decision flag"
            )
        install_root = Path(overrides.get(source, scan["install_root"]))
        for row in scan.get("results", []):
            verdict = str(row.get("verdict") or "")
            if verdict not in {"modified", "new_slot", "baseline"}:
                continue
            ui_index = int(row["uiIndex"])
            name = str(row.get("name") or "Unnamed")
            filename = f"{ui_index:03d}_{_safe_name(name)}.wmerc"
            rel = (Path("bundles") / source / filename).as_posix()
            folded = rel.casefold()
            if folded in seen:
                raise ValueError(f"duplicate planned bundle path: {rel}")
            seen.add(folded)
            items.append(ExportItem(
                source=source,
                install_root=install_root,
                ui_index=ui_index,
                name=name,
                verdict=verdict,
                differing_components=tuple(row.get("differing_components") or ()),
                filename=filename,
            ))
        expected = scan.get("totals", {}).get("will_export")
        source_count = sum(1 for item in items if item.source == source)
        if expected is not None and source_count != int(expected):
            raise ValueError(
                f"{path.name}: planned {source_count} bundles but scan declares {expected}"
            )
    return sorted(items, key=lambda item: (item.source.casefold(), item.ui_index, item.filename))


def _staging_path(out_root: Path, version: str) -> Path:
    if not re.fullmatch(r"[A-Za-z0-9_.-]+", version):
        raise ValueError("version must contain only letters, digits, dot, underscore, or hyphen")
    return Path(out_root) / f"megapack-{version}.staging"


def create_staging_root(out_root: Path, *, version: str) -> Path:
    staging = _staging_path(Path(out_root), version)
    staging.mkdir(parents=True, exist_ok=False)
    return staging


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _append_log(path: Path, record: dict) -> None:
    with path.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(record, ensure_ascii=False) + "\n")
        handle.flush()


def _bundle_compat(source: str) -> str:
    return {
        "vanilla_113": "vanilla",
        "aimnas": "aimnas",
        "wildfire": "wildfire",
        "wasteland": "wasteland",
    }.get(source, "any")


def _atomic_json(path: Path, value) -> None:
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def _merc_class(merc_type: int) -> str:
    return {1: "AIM", 2: "MERC", 3: "IMP", 4: "RPC"}.get(int(merc_type), "OTHER")


def _character_id(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", name.casefold()).strip("_") or "unnamed"


def _catalog_record(staging_root: Path, bundle_path: Path) -> dict:
    contents = read_wmerc(bundle_path)
    merc = contents.manifest.merc
    rel = bundle_path.relative_to(staging_root).as_posix()
    parts = Path(rel).parts
    voice_count = contents.manifest.voice.count if contents.manifest.voice else 0
    has_face = any(name.casefold().startswith("raw_stis/faces/") for name in contents.files)
    return {
        "path": rel,
        "source_mod": parts[1],
        "source_slot": int(merc.uiIndex),
        "name": merc.zName,
        "nickname": merc.zNickname,
        "merc_class": _merc_class(merc.Type),
        "face_index": int(merc.ubFaceIndex),
        "has_voice": contents.has_voice,
        "voice_count": int(voice_count),
        "has_face": has_face,
        "stats": {
            "str": int(merc.bStrength),
            "agi": int(merc.bAgility),
            "dex": int(merc.bDexterity),
            "wis": int(merc.bWisdom),
            "life": int(merc.bLife),
            "mark": int(merc.bMarksmanship),
            "lead": int(merc.bLeadership),
            "mech": int(merc.bMechanical),
            "exp": int(merc.bExplosive),
            "med": int(merc.bMedical),
        },
    }


def _variant_record(record: dict) -> dict:
    return {
        "source_mod": record["source_mod"],
        "source_slot": record["source_slot"],
        "bundle_path": record["path"],
        "name": record["name"],
        "nickname": record["nickname"],
        "has_voice": record["has_voice"],
        "voice_count": record["voice_count"],
        "has_face": record["has_face"],
        "stats": record["stats"],
    }


def rebuild_distribution_metadata(
    template_root: Path,
    staging_root: Path,
    *,
    excluded_sources: set[str] | None = None,
) -> dict:
    """Rebuild full catalogs and carry forward an unchanged lite selection.

    The template supplies established canonical character grouping and the
    curated lite choice. Physical bundles in staging remain the membership
    authority; catalog rows absent from that tree are removed.
    """
    template = Path(template_root)
    staging = Path(staging_root)
    excluded = set(excluded_sources or ())
    physical_paths = {
        path.relative_to(staging).as_posix(): path
        for path in staging.joinpath("bundles").rglob("*.wmerc")
    }
    flat_path = template / "catalog_flat.json"
    flat = json.loads(flat_path.read_text(encoding="utf-8")) if flat_path.is_file() else []
    flat = [record for record in flat if record.get("path") in physical_paths]
    known = {record["path"] for record in flat}
    added = []
    for rel, path in sorted(physical_paths.items(), key=lambda pair: pair[0].casefold()):
        if rel not in known:
            record = _catalog_record(staging, path)
            flat.append(record)
            added.append(record)
    flat.sort(key=lambda record: (
        str(record["source_mod"]).casefold(), int(record["source_slot"]), record["path"].casefold()
    ))

    grouped_path = template / "catalog.json"
    grouped = json.loads(grouped_path.read_text(encoding="utf-8")) if grouped_path.is_file() else []
    physical_set = set(physical_paths)
    for character in grouped:
        character["variants"] = [
            variant for variant in character.get("variants", [])
            if variant.get("bundle_path") in physical_set
        ]
    for record in added:
        identity = _character_id(record["name"])
        character = next(
            (item for item in grouped if item.get("character_id") == identity), None
        )
        if character is None:
            character = next((
                item for item in grouped
                if item.get("vanilla_slot") == record["source_slot"]
                and item.get("canonical_name") == record["name"]
            ), None)
        if character is None:
            character = {
                "character_id": identity,
                "canonical_name": record["name"],
                "merc_class": record["merc_class"],
                "vanilla_slot": (
                    record["source_slot"] if record["source_mod"] == "vanilla_113" else None
                ),
                "variants": [],
            }
            grouped.append(character)
        character["variants"].append(_variant_record(record))
    for character in grouped:
        character["variants"].sort(key=lambda variant: (
            variant["source_mod"] != "vanilla_113",
            str(variant["source_mod"]).casefold(),
            int(variant["source_slot"]),
        ))
    grouped = [character for character in grouped if character.get("variants")]
    grouped.sort(key=lambda character: str(character["canonical_name"]).casefold())
    _atomic_json(staging / "catalog_flat.json", flat)
    _atomic_json(staging / "catalog.json", grouped)

    lite_source = template / "bundles_lite"
    if lite_source.is_dir():
        shutil.copytree(lite_source, staging / "bundles_lite", dirs_exist_ok=False)
    for name in ("catalog_lite.json", "MercForge_Lite.zip", "MercForge_Faces.zip"):
        source = template / name
        if source.is_file():
            shutil.copy2(source, staging / name)
    for pattern in ("_scan_*.json", "_vanilla_baseline.json"):
        for source in template.glob(pattern):
            shutil.copy2(source, staging / source.name)
    _atomic_json(staging / "_excluded_sources.json", sorted(excluded))
    reconciliation = template / "RECONCILIATION.md"
    if reconciliation.is_file():
        shutil.copy2(reconciliation, staging / reconciliation.name)

    manifest = build_physical_manifest(staging)
    source_lines = "\n".join(
        f"| `{source}` | {count} |"
        for source, count in manifest["source_counts"].items()
    )
    (staging / "_delta_audit.md").write_text(
        "# Mega Merc Pack — Reconciled Forecast\n\n"
        f"**Total bundles the export will produce: {manifest['file_count']}**\n\n"
        "Excluded historical scan sources: "
        + (", ".join(f"`{source}`" for source in sorted(excluded)) or "none")
        + ".\n\n"
        "| Source | Bundles |\n| --- | ---: |\n" + source_lines + "\n",
        encoding="utf-8",
    )
    (staging / "_quality_report.md").write_text(
        "# Mega Merc Pack — Quality Report\n\n"
        f"Catalog: {len(grouped)} characters, {len(flat)} variants\n\n"
        f"Variants with voice: {sum(1 for item in flat if item['has_voice'])}\n\n"
        f"Variants with bundled face STI: {sum(1 for item in flat if item['has_face'])}\n",
        encoding="utf-8",
    )
    return {
        "catalog_count": len(flat),
        "character_count": len(grouped),
        "added_paths": [record["path"] for record in added],
    }


def build_full_archive(staging_root: Path) -> Path:
    staging = Path(staging_root)
    archive_path = staging / "MercForge_Full.zip"
    temporary = archive_path.with_name(archive_path.name + ".tmp")
    with zipfile.ZipFile(temporary, "w", compression=zipfile.ZIP_STORED, allowZip64=True) as archive:
        for bundle in sorted(
            staging.joinpath("bundles").rglob("*.wmerc"),
            key=lambda path: path.as_posix().casefold(),
        ):
            archive.write(bundle, bundle.relative_to(staging).as_posix())
    temporary.replace(archive_path)
    return archive_path


def _resume_entries(manifest_path: Path | None) -> tuple[Path | None, dict[str, dict]]:
    if manifest_path is None:
        return None, {}
    path = Path(manifest_path).resolve()
    manifest = json.loads(path.read_text(encoding="utf-8"))
    entries = {}
    for entry in manifest.get("files", []):
        rel = str(entry["path"]).replace("\\", "/")
        if rel.startswith("/") or ".." in Path(rel).parts:
            raise ValueError(f"unsafe resume manifest path: {rel}")
        entries[rel] = entry
    return path.parent, entries


def execute_export(
    plan: list[ExportItem],
    staging_root: Path,
    *,
    resume_manifest: Path | None = None,
    dry_run: bool = False,
    exporter: Callable = export_merc,
) -> dict:
    staging = Path(staging_root)
    if dry_run:
        return {
            "dry_run": True,
            "planned_count": len(plan),
            "paths": [item.relative_path.as_posix() for item in plan],
        }
    if not staging.is_dir():
        raise FileNotFoundError(f"staging root does not exist: {staging}")
    resume_root, resume = _resume_entries(resume_manifest)
    log_path = staging / "_export_log.jsonl"
    success_count = 0
    error_count = 0
    resumed_paths: set[str] = set()
    if resume_root is not None:
        for rel, entry in sorted(resume.items(), key=lambda pair: pair[0].casefold()):
            previous = resume_root / Path(rel)
            expected_hash = entry.get("sha256")
            if not previous.is_file() or _sha256(previous) != expected_hash:
                raise ValueError(f"resume manifest entry does not verify: {rel}")
            target = staging / Path(rel)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(previous, target)
            parts = Path(rel).parts
            filename = parts[-1]
            try:
                ui_index = int(filename[:3])
            except ValueError:
                ui_index = None
            _append_log(log_path, {
                "source": parts[1] if len(parts) > 2 else None,
                "uiIndex": ui_index,
                "bundle_path": rel,
                "sha256": expected_hash,
                "resumed": True,
                "error": None,
            })
            resumed_paths.add(rel)
            success_count += 1
    for item in plan:
        rel = item.relative_path.as_posix()
        if rel in resumed_paths:
            continue
        target = staging / item.relative_path
        record = {
            "source": item.source,
            "uiIndex": item.ui_index,
            "name": item.name,
            "verdict": item.verdict,
            "differing_components": list(item.differing_components),
            "bundle_path": rel,
            "error": None,
        }
        partial = target.with_name(target.name + ".partial")
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            exporter(
                install_root=item.install_root,
                ui_index=item.ui_index,
                out_path=partial,
                intended_mod=_bundle_compat(item.source),
            )
            partial.replace(target)
            record["sha256"] = _sha256(target)
            success_count += 1
        except Exception as exc:  # noqa: BLE001 - every attempted row belongs in the log
            partial.unlink(missing_ok=True)
            record["error"] = f"{type(exc).__name__}: {exc}"
            error_count += 1
        _append_log(log_path, record)
    manifest = build_physical_manifest(staging)
    manifest_path = staging / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return {
        "dry_run": False,
        "planned_count": len(plan),
        "success_count": success_count,
        "error_count": error_count,
        "manifest_path": str(manifest_path),
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", action="append", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--resume-manifest")
    parser.add_argument(
        "--template-root",
        help="Prior pack root supplying canonical catalog grouping and lite selection.",
    )
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--include-mod-prototype", action="store_true")
    parser.add_argument(
        "--install-root", action="append", default=[], metavar="SOURCE=PATH",
        help="Override a stale scan install root for one named source.",
    )
    parser.add_argument("--version")
    return parser


def _parse_install_overrides(values: list[str]) -> dict[str, Path]:
    overrides = {}
    for value in values:
        source, separator, raw_path = value.partition("=")
        if not separator or not source.strip() or not raw_path.strip():
            raise ValueError(f"invalid --install-root value: {value!r}")
        source = source.strip()
        if source in overrides:
            raise ValueError(f"duplicate --install-root source: {source}")
        overrides[source] = Path(raw_path.strip())
    return overrides


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    plan = load_export_plan(
        [Path(path) for path in args.source],
        include_mod_prototype=args.include_mod_prototype,
        install_overrides=_parse_install_overrides(args.install_root),
    )
    version = args.version or datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    staging = _staging_path(Path(args.out), version)
    if not args.dry_run:
        staging = create_staging_root(Path(args.out), version=version)
    result = execute_export(
        plan,
        staging,
        resume_manifest=Path(args.resume_manifest) if args.resume_manifest else None,
        dry_run=args.dry_run,
    )
    if args.template_root and not args.dry_run and not result.get("error_count"):
        result["distribution_metadata"] = rebuild_distribution_metadata(
            Path(args.template_root), staging,
            excluded_sources=(set() if args.include_mod_prototype else {"mod_prototype"}),
        )
        result["full_archive"] = str(build_full_archive(staging))
    result["staging_root"] = str(staging)
    print(json.dumps(result, indent=2))
    return 1 if result.get("error_count") else 0


if __name__ == "__main__":
    sys.exit(main())
