"""INI presets — load, validate, author, apply (docs/INI_PRESETS_SPEC.md).

Two sources:
  builtin  — mercwizard_core/data/ini_presets/*.json (shipped, read-only)
  install  — <install root>/MercForgePresets.json (authored in-app;
             engine-invisible, atomic writes, snapshot before overwrite)

Load-time rules (per spec — every rule maps to an adversarial-review
finding): Ja2.ini changes are coerced to target=canon (advisory note);
AI.ini under effective target=override disables apply (load error, never
an apply-time half-failure); validation warnings are advisory only;
effect_timing + savegame_risk derived per change so the UI can say
"affects new games only" before commit; corrupt preset files are skipped
with a surfaced warning, never fatal.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from .ini_editor import (
    IniChange,
    IniEditorError,
    canonical_ini_name,
    load_schema,
    schema_key_index,
    validate_against_schema,
)

PRESETS_DIR = Path(__file__).parent / "data" / "ini_presets"
INSTALL_PRESET_FILENAME = "MercForgePresets.json"
INSTALL_FILE_CAP = 512 * 1024

# Files whose loaders only run at process startup (engine-facts §2/§5);
# everything else GameSettings re-reads on each new-game start.
RELAUNCH_ONLY = {"item_settings.ini", "mod_settings.ini", "ai.ini", "ja2.ini"}


@dataclass
class PresetChange:
    ini_file: str
    section: str
    key: str
    value: Optional[str] = None
    delete: bool = False
    target: Optional[str] = None          # None = inherit preset default


@dataclass
class Preset:
    id: str
    name: str
    description: str
    default_target: str = "override"
    source: str = "builtin"               # "builtin" | "install"
    changes: list[PresetChange] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    apply_disabled: Optional[str] = None  # reason string when set
    effect_timing: str = "new_game"       # rolled-up: "new_game" | "relaunch"
    savegame_risk: bool = False

    @property
    def wire_id(self) -> str:
        return f"{self.source}:{self.id}"


def _is_savegame_risk(section: str, prop: Optional[dict]) -> bool:
    if "system limit" in section.lower():
        return True
    desc = (prop or {}).get("description") or ""
    return "UNLOADABLE" in desc or "NOT RECOMMENDED" in desc


def _effective_target(p: Preset, c: PresetChange) -> str:
    return c.target or p.default_target


def _validate_preset(p: Preset) -> None:
    """Apply the load-time rules in place (coercions, warnings,
    apply_disabled, effect_timing, savegame_risk)."""
    worst_timing = "new_game"
    schema_cache: dict[str, dict] = {}
    idx_cache: dict[str, dict] = {}

    for c in p.changes:
        try:
            canon = canonical_ini_name(c.ini_file)
        except IniEditorError:
            p.warnings.append(f"{c.ini_file}: not an editable INI — change will be rejected at apply")
            continue
        c.ini_file = canon

        # Rule 1: Ja2.ini → canon, always.
        if canon == "Ja2.ini" and _effective_target(p, c) != "canon":
            c.target = "canon"
            p.warnings.append(
                f"Ja2.ini {c.section}/{c.key}: written directly (Ja2.ini has no override mechanism)")
        # Rule 2: AI.ini under override → apply-disabled.
        if canon == "AI.ini" and _effective_target(p, c) == "override":
            p.apply_disabled = (
                "AI.ini has no override mechanism; set target to canon for its changes")

        if canon.lower() in RELAUNCH_ONLY:
            worst_timing = "relaunch"

        # Rule 3: advisory schema validation.
        if canon not in schema_cache:
            try:
                schema_cache[canon] = load_schema(canon)
                idx_cache[canon] = schema_key_index(schema_cache[canon])
            except IniEditorError:
                schema_cache[canon] = {"sections": []}
                idx_cache[canon] = {}
        if c.value is not None:
            w = validate_against_schema(
                schema_cache[canon],
                IniChange(section=c.section, key=c.key, value=c.value, ini_file=canon))
            if w:
                p.warnings.append(f"{canon} {c.section}/{c.key}: {w}")

        # Rule 4: savegame risk.
        prop = idx_cache[canon].get(f"{c.section}/{c.key}".lower())
        if _is_savegame_risk(c.section, prop):
            p.savegame_risk = True

    p.effect_timing = worst_timing


def _parse_preset(raw: dict, source: str) -> Preset:
    changes = [
        PresetChange(
            ini_file=ch.get("ini_file", ""),
            section=ch.get("section", ""),
            key=ch.get("key", ""),
            value=ch.get("value"),
            delete=bool(ch.get("delete", False)),
            target=ch.get("target"),
        )
        for ch in raw.get("changes", [])
    ]
    p = Preset(
        id=str(raw.get("id", "")).strip(),
        name=str(raw.get("name", "")).strip(),
        description=str(raw.get("description", "")).strip(),
        default_target=raw.get("default_target", "override"),
        source=source,
        changes=changes,
    )
    if not p.id or not p.name:
        raise ValueError("preset requires id and name")
    if p.default_target not in ("override", "canon"):
        raise ValueError(f"bad default_target: {p.default_target!r}")
    _validate_preset(p)
    return p


def install_preset_path(install_root: Path) -> Path:
    return install_root / INSTALL_PRESET_FILENAME


def load_presets(install_root: Optional[Path]) -> tuple[list[Preset], list[str]]:
    """All presets (builtin + install-local). Returns (presets,
    file_warnings) — corrupt files are skipped with a warning."""
    presets: list[Preset] = []
    file_warnings: list[str] = []

    if PRESETS_DIR.is_dir():
        for f in sorted(PRESETS_DIR.glob("*.json")):
            try:
                presets.append(_parse_preset(
                    json.loads(f.read_text(encoding="utf-8")), "builtin"))
            except (OSError, ValueError, json.JSONDecodeError) as e:
                file_warnings.append(f"builtin {f.name}: skipped ({e})")

    if install_root is not None:
        path = install_preset_path(install_root)
        if path.is_file():
            try:
                if path.stat().st_size > INSTALL_FILE_CAP:
                    raise ValueError(f"file exceeds {INSTALL_FILE_CAP} bytes")
                data = json.loads(path.read_text(encoding="utf-8"))
                if not isinstance(data, list):
                    raise ValueError("top level must be a JSON array")
                for raw in data:
                    try:
                        presets.append(_parse_preset(raw, "install"))
                    except (ValueError, AttributeError) as e:
                        file_warnings.append(f"{path.name}: preset skipped ({e})")
            except (OSError, ValueError, json.JSONDecodeError) as e:
                file_warnings.append(f"{path.name}: unreadable ({e})")

    # Install presets shadow builtins with the same bare id in listings.
    seen_install = {p.id for p in presets if p.source == "install"}
    presets = [p for p in presets
               if not (p.source == "builtin" and p.id in seen_install)] \
        if seen_install else presets
    return presets, file_warnings


def find_preset(install_root: Optional[Path], wire_id: str) -> Optional[Preset]:
    presets, _ = load_presets(install_root)
    for p in presets:
        if p.wire_id == wire_id:
            return p
    return None


def preset_to_ini_changes(p: Preset) -> dict[str, list[IniChange]]:
    """Split into {target: [IniChange]} honoring per-change targets.
    Raises IniEditorError when the preset is apply-disabled."""
    if p.apply_disabled:
        raise IniEditorError("PRESET_APPLY_DISABLED", p.apply_disabled)
    out: dict[str, list[IniChange]] = {}
    for c in p.changes:
        target = _effective_target(p, c)
        out.setdefault(target, []).append(IniChange(
            section=c.section, key=c.key,
            value=None if c.delete else c.value,
            ini_file=c.ini_file))
    return out


def save_install_preset(install_root: Path, raw: dict) -> Preset:
    """Create/replace an install-local preset (by id). Atomic write;
    caller is responsible for lock + backup snapshot of the file."""
    from .backup import write_bytes_atomic

    new_preset = _parse_preset(raw, "install")
    path = install_preset_path(install_root)
    existing: list[dict] = []
    if path.is_file():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(data, list):
                existing = [e for e in data
                            if isinstance(e, dict) and e.get("id") != new_preset.id]
        except (OSError, json.JSONDecodeError):
            pass  # replaced wholesale; snapshot preserved the corrupt original
    existing.append({
        "id": new_preset.id,
        "name": new_preset.name,
        "description": new_preset.description,
        "default_target": new_preset.default_target,
        "changes": [
            {k: v for k, v in {
                "ini_file": c.ini_file, "section": c.section, "key": c.key,
                "value": c.value, "delete": c.delete or None,
                "target": c.target,
            }.items() if v is not None}
            for c in new_preset.changes
        ],
    })
    write_bytes_atomic(path, json.dumps(existing, indent=1).encode("utf-8"))
    return new_preset


def delete_install_preset(install_root: Path, preset_id: str) -> bool:
    from .backup import write_bytes_atomic

    path = install_preset_path(install_root)
    if not path.is_file():
        return False
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    if not isinstance(data, list):
        return False
    kept = [e for e in data if not (isinstance(e, dict) and e.get("id") == preset_id)]
    if len(kept) == len(data):
        return False
    write_bytes_atomic(path, json.dumps(kept, indent=1).encode("utf-8"))
    return True
