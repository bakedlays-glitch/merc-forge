"""Golden graphics stack (cnc-ddraw + ReShade) — verify + deploy.

Model (per the 2026-06-07 review — this is NOT a file-copy "golden
master"):
  - RUNTIME components (ddraw.dll, opengl32.dll, reshade-shaders/) are
    external downloads we don't ship. Status = presence only, with a
    download pointer. Deploy REFUSES to touch a config whose runtime
    is absent (merging [ddraw] keys into a ddraw.ini that cnc-ddraw
    never created would be fiction).
  - ja2_remastered.ini (the 7-shader preset) is OURS: bundled at
    mercwizard_core/data/graphics/, strict-hash compared, copied on
    deploy.
  - ddraw.ini / ReShade.ini are USER files that mutate at runtime.
    Status = key-subset check against the bundled snippets; deploy =
    surgical per-key merge (comment-preserving, self-verifying — the
    INI editor's writer), never wholesale replace.

The GPU registry preference from Install-JA2Graphics.ps1 is out of
scope here (registry writes are a different blast radius).
"""
from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Optional

from .ini_editor import IniChange, parse_ini_map, surgical_upsert, _decode

GRAPHICS_DIR = Path(__file__).parent / "data" / "graphics"

CNC_DDRAW_URL = "https://github.com/FunkyFr3sh/cnc-ddraw/releases"
RESHADE_URL = "https://reshade.me/"


def _snippet_keys(snippet_name: str) -> dict[str, dict[str, str]]:
    path = GRAPHICS_DIR / snippet_name
    if not path.is_file():
        return {}
    return parse_ini_map(_decode(path.read_bytes())[0])


def _key_subset_status(target: Path, wanted: dict[str, dict[str, str]]) -> dict:
    """Do the golden keys carry the golden values in the user's file?"""
    if not target.is_file():
        return {"present": False, "matches": False, "mismatched_keys": []}
    have = parse_ini_map(_decode(target.read_bytes())[0])
    mismatched: list[str] = []
    for section, keys in wanted.items():
        have_sect = next(
            (v for s, v in have.items() if s.lower() == section.lower()), {})
        for k, v in keys.items():
            actual = next(
                (av for ak, av in have_sect.items() if ak.lower() == k.lower()),
                None)
            if actual is None or actual.strip().lower() != v.strip().lower():
                mismatched.append(f"{section}/{k}")
    return {"present": True, "matches": not mismatched,
            "mismatched_keys": mismatched}


def _md5(path: Path) -> str:
    return hashlib.md5(path.read_bytes()).hexdigest()


def graphics_status(install_root: Path) -> list[dict]:
    """Per-component status. check_kind drives what `matches` means."""
    out: list[dict] = []

    # Runtimes — presence only.
    for name, url, note in (
        ("ddraw.dll", CNC_DDRAW_URL, "cnc-ddraw renderer"),
        ("opengl32.dll", RESHADE_URL, "ReShade runtime"),
        ("reshade-shaders", RESHADE_URL, "ReShade shader pack"),
    ):
        p = install_root / name
        present = p.is_dir() if name == "reshade-shaders" else p.is_file()
        out.append({
            "component": name, "kind": "runtime", "check_kind": "presence",
            "present": present, "matches": present, "note": note,
            "download_url": None if present else url,
        })

    # The preset we own — strict hash.
    master = GRAPHICS_DIR / "ja2_remastered.ini"
    target = install_root / "ja2_remastered.ini"
    master_ok = master.is_file()
    present = target.is_file()
    out.append({
        "component": "ja2_remastered.ini", "kind": "managed_file",
        "check_kind": "strict_hash", "present": present,
        "matches": bool(master_ok and present and _md5(master) == _md5(target)),
        "note": "the 7-shader preset (LumaSharpen→…→Deband)",
        "source_available": master_ok,
    })

    # User configs — key-subset.
    for target_name, snippet in (
        ("ddraw.ini", "ddraw_config_snippet.ini"),
        ("ReShade.ini", "ReShade_config_snippet.ini"),
    ):
        wanted = _snippet_keys(snippet)
        st = _key_subset_status(install_root / target_name, wanted)
        out.append({
            "component": target_name, "kind": "config_overlay",
            "check_kind": "key_subset", "note": f"golden keys from {snippet}",
            "source_available": bool(wanted), **st,
        })
    return out


class GraphicsDeployError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def deploy_graphics(install_root: Path) -> dict:
    """Merge the golden config into the install. Caller is responsible
    for the lock + backup snapshot of (ddraw.ini, ReShade.ini,
    ja2_remastered.ini). Refuses when a needed runtime is absent."""
    actions: list[str] = []

    ddraw_ini = install_root / "ddraw.ini"
    reshade_ini = install_root / "ReShade.ini"

    # Runtime guards: configs only make sense with their runtime present.
    if not (install_root / "ddraw.dll").is_file():
        raise GraphicsDeployError(
            "RUNTIME_MISSING",
            f"cnc-ddraw (ddraw.dll) isn't installed in this install — get it "
            f"from {CNC_DDRAW_URL} first; the [ddraw] config keys mean "
            "nothing without it.")
    if not (install_root / "opengl32.dll").is_file():
        raise GraphicsDeployError(
            "RUNTIME_MISSING",
            f"ReShade (opengl32.dll) isn't installed in this install — run "
            f"the installer from {RESHADE_URL} first.")

    # 1. The preset file (ours, copy wholesale).
    master = GRAPHICS_DIR / "ja2_remastered.ini"
    if not master.is_file():
        raise GraphicsDeployError(
            "SOURCE_MISSING", "Bundled ja2_remastered.ini missing from the app package.")
    (install_root / "ja2_remastered.ini").write_bytes(master.read_bytes())
    actions.append("copied ja2_remastered.ini")

    # 2 + 3. Key merges via the self-verifying surgical writer.
    for target, snippet in ((ddraw_ini, "ddraw_config_snippet.ini"),
                            (reshade_ini, "ReShade_config_snippet.ini")):
        wanted = _snippet_keys(snippet)
        changes = [
            IniChange(section=s, key=k, value=v)
            for s, keys in wanted.items() for k, v in keys.items()
        ]
        if not target.is_file():
            # ReShade.ini may legitimately not exist yet even with the
            # runtime present (created on first game launch) — create it
            # with just our keys; cnc-ddraw's ddraw.ini ships with the dll
            # so absence there is unusual but harmless to create.
            surgical_upsert(target, changes,
                            new_file_header=";; created by MercForge graphics deploy")
            actions.append(f"created {target.name} with golden keys")
        else:
            surgical_upsert(target, changes)
            actions.append(f"merged {len(changes)} golden keys into {target.name}")

    return {"ok": True, "actions": actions}
