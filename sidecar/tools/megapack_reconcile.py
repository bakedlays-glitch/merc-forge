"""Build a content-addressed MegaPack manifest from physical `.wmerc` files.

The bundle tree is observed state. Catalogs, archives, and the append-only
export log are compared against it, but none of them can add files to the
manifest or substitute for a missing physical bundle.
"""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
import os
from pathlib import Path, PurePosixPath, PureWindowsPath
import re
import sys
import zipfile


_FORECAST_RE = re.compile(r"Total bundles the export will produce:\s*([0-9,]+)")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _relative_path(value: str) -> str:
    text = str(value).replace("\\", "/")
    path = PurePosixPath(text)
    if path.is_absolute() or PureWindowsPath(str(value)).is_absolute():
        raise ValueError(f"absolute path is not distribution-safe: {value}")
    if not path.parts or ".." in path.parts or "." in path.parts:
        raise ValueError(f"path escapes the distribution root: {value}")
    return path.as_posix()


def _build_tree_manifest(pack_root: Path, tree_name: str) -> dict:
    root = Path(pack_root).resolve()
    bundle_root = root / tree_name
    files = []
    seen: dict[str, str] = {}
    duplicate_paths = []
    source_counts: Counter[str] = Counter()
    total_bytes = 0
    if bundle_root.is_dir():
        paths = sorted(bundle_root.rglob("*.wmerc"), key=lambda p: p.as_posix().casefold())
    else:
        paths = []
    for path in paths:
        rel = path.relative_to(root).as_posix()
        folded = rel.casefold()
        if folded in seen:
            duplicate_paths.append(rel)
            continue
        seen[folded] = rel
        size = path.stat().st_size
        source = path.parent.name
        source_counts[source] += 1
        total_bytes += size
        files.append({"path": rel, "size": size, "sha256": _sha256(path)})
    return {
        "schema_version": 1,
        "authority": f"physical_{tree_name}_tree",
        "file_count": len(files),
        "total_bytes": total_bytes,
        "source_counts": dict(sorted(source_counts.items())),
        "duplicate_paths": sorted(duplicate_paths, key=str.casefold),
        "files": files,
    }


def build_physical_manifest(pack_root: Path) -> dict:
    return _build_tree_manifest(pack_root, "bundles")


def _expected_log_path(record: dict) -> str | None:
    raw = str(record.get("bundle_path") or "")
    source = str(record.get("source") or "").strip()
    if not raw or not source:
        return None
    name = raw.replace("\\", "/").rsplit("/", 1)[-1]
    if not name.lower().endswith(".wmerc"):
        return None
    return f"bundles/{source}/{name}"


def _same_windows_path(left: Path, raw_right: str) -> bool:
    right = str(PureWindowsPath(raw_right)).replace("/", "\\")
    expected = str(PureWindowsPath(str(left))).replace("/", "\\")
    return os.path.normcase(right) == os.path.normcase(expected)


def _read_execution_log(pack_root: Path, physical: set[str], log_path: Path) -> dict:
    result = {
        "row_count": 0,
        "success_count": 0,
        "error_count": 0,
        "invalid_json_lines": [],
        "duplicate_paths": [],
        "missing_physical_paths": [],
        "stale_absolute_paths": [],
    }
    if not log_path.is_file():
        return result
    successes = []
    stale = []
    for line_number, line in enumerate(log_path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        result["row_count"] += 1
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            result["invalid_json_lines"].append(line_number)
            continue
        if record.get("error"):
            result["error_count"] += 1
            continue
        result["success_count"] += 1
        rel = _expected_log_path(record)
        if rel is not None:
            successes.append(rel)
        raw = str(record.get("bundle_path") or "")
        if raw and PureWindowsPath(raw).is_absolute() and rel is not None:
            if not _same_windows_path(pack_root / PurePosixPath(rel), raw):
                stale.append(raw)
    counts = Counter(successes)
    result["duplicate_paths"] = sorted(
        (path for path, count in counts.items() if count > 1), key=str.casefold
    )
    result["missing_physical_paths"] = sorted(
        (path for path in counts if path not in physical), key=str.casefold
    )
    result["stale_absolute_paths"] = sorted(set(stale), key=str.casefold)
    return result


def _catalog_paths(value) -> list[str]:
    found = []
    if isinstance(value, dict):
        for key, child in value.items():
            if (
                key in {"path", "bundle_path"}
                and isinstance(child, str)
                and child.lower().endswith(".wmerc")
            ):
                found.append(child)
            else:
                found.extend(_catalog_paths(child))
    elif isinstance(value, list):
        for child in value:
            found.extend(_catalog_paths(child))
    return found


def _audit_catalog(path: Path, physical: set[str]) -> dict:
    result = {
        "entry_count": 0,
        "duplicate_paths": [],
        "missing_physical_paths": [],
        "invalid_paths": [],
        "error": None,
    }
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        result["error"] = "invalid_catalog"
        return result
    valid = []
    for raw in _catalog_paths(payload):
        try:
            valid.append(_relative_path(raw))
        except ValueError:
            result["invalid_paths"].append(raw)
    result["entry_count"] = len(valid)
    counts = Counter(valid)
    result["duplicate_paths"] = sorted(
        (name for name, count in counts.items() if count > 1), key=str.casefold
    )
    result["missing_physical_paths"] = sorted(
        (name for name in counts if name not in physical), key=str.casefold
    )
    result["invalid_paths"].sort(key=str.casefold)
    return result


def _audit_archive(path: Path, physical: set[str]) -> dict:
    result = {
        "member_count": 0,
        "duplicate_members": [],
        "missing_members": [],
        "extra_members": [],
        "invalid_members": [],
        "error": None,
    }
    try:
        with zipfile.ZipFile(path) as archive:
            bad = archive.testzip()
            if bad is not None:
                result["error"] = f"corrupt_member:{bad}"
                return result
            members = []
            for raw in archive.namelist():
                if not raw.lower().endswith(".wmerc"):
                    continue
                try:
                    members.append(_relative_path(raw))
                except ValueError:
                    result["invalid_members"].append(raw)
    except (OSError, zipfile.BadZipFile, RuntimeError):
        result["error"] = "corrupt_zip"
        return result
    counts = Counter(members)
    member_set = set(counts)
    result["member_count"] = len(members)
    result["duplicate_members"] = sorted(
        (name for name, count in counts.items() if count > 1), key=str.casefold
    )
    if members:
        result["missing_members"] = sorted(physical - member_set, key=str.casefold)
        result["extra_members"] = sorted(member_set - physical, key=str.casefold)
    result["invalid_members"].sort(key=str.casefold)
    return result


def _read_forecast(pack_root: Path, observed_sources: set[str]) -> dict:
    total = None
    audit = pack_root / "_delta_audit.md"
    if audit.is_file():
        match = _FORECAST_RE.search(audit.read_text(encoding="utf-8", errors="replace"))
        if match:
            total = int(match.group(1).replace(",", ""))
    absent = {}
    excluded = set()
    exclusion_path = pack_root / "_excluded_sources.json"
    if exclusion_path.is_file():
        try:
            excluded = {str(value) for value in json.loads(
                exclusion_path.read_text(encoding="utf-8")
            )}
        except (OSError, TypeError, json.JSONDecodeError):
            excluded = set()
    for path in sorted(pack_root.glob("_scan_*.json")):
        try:
            scan = json.loads(path.read_text(encoding="utf-8"))
            source = str(scan["source"])
            count = int(scan["totals"]["will_export"])
        except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError):
            continue
        if count > 0 and source not in observed_sources and source not in excluded:
            absent[source] = count
    return {
        "total": total,
        "absent_sources": dict(sorted(absent.items())),
    }


def reconcile_pack(
    pack_root: Path,
    *,
    log_path: Path | None = None,
    catalog_paths: list[Path] | None = None,
    zip_paths: list[Path] | None = None,
) -> dict:
    root = Path(pack_root).resolve()
    manifest = build_physical_manifest(root)
    physical = {entry["path"] for entry in manifest["files"]}
    lite_manifest = _build_tree_manifest(root, "bundles_lite")
    lite_physical = {entry["path"] for entry in lite_manifest["files"]}
    if log_path is None:
        log_path = root / "_export_log.jsonl"
    if catalog_paths is None:
        catalog_paths = sorted(root.glob("catalog*.json"))
    if zip_paths is None:
        zip_paths = sorted(root.glob("*.zip"))
    catalogs = {}
    for path in catalog_paths:
        path = Path(path)
        expected = lite_physical if "lite" in path.name.casefold() else physical
        catalogs[path.name] = _audit_catalog(path, expected)
    archives = {}
    for path in zip_paths:
        path = Path(path)
        expected = lite_physical if "lite" in path.name.casefold() else physical
        archives[path.name] = _audit_archive(path, expected)
    return {
        "manifest": manifest,
        "lite_manifest": lite_manifest,
        "execution_log": _read_execution_log(root, physical, Path(log_path)),
        "catalogs": catalogs,
        "archives": archives,
        "forecast": _read_forecast(root, set(manifest["source_counts"])),
    }


def _write_json(path: Path, value: dict) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("pack_root", type=Path)
    parser.add_argument("--manifest-out", type=Path)
    parser.add_argument("--report-out", type=Path)
    parser.add_argument("--catalog", action="append", default=[], type=Path)
    parser.add_argument("--zip", action="append", default=[], type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    report = reconcile_pack(
        args.pack_root,
        catalog_paths=args.catalog or None,
        zip_paths=args.zip or None,
    )
    if args.manifest_out:
        _write_json(args.manifest_out, report["manifest"])
    if args.report_out:
        _write_json(args.report_out, report)
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
