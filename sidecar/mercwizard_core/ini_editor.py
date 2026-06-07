"""INI editor core — schema-driven, engine-faithful INI read/write.

Write strategies are gated by docs/INI_EDITOR_ENGINE_FACTS.md (the
engine-facts matrix). Do not change strategy here without updating that
doc, and vice versa. Summary:

  AUTHOR mode  — edits the mod's shipped canon in place (the layer the
                 engine resolves the base file from, via
                 VfsLayout.resolve_write). Ja2.ini → install root.
  PLAY mode    — per-campaign overrides that never touch canon:
                 * Ja2.ini            → install root (no per-campaign
                                        mechanism exists; install-global)
                 * AI.ini             → REFUSED (2-arg CIniReader ctor has
                                        no Override hook; partial edits can
                                        crash PlanFactoryLibrary)
                 * every other file   → `<stem>.Override` written to the
                                        engine write profile's root
                                        (Profiles\\UserProfile_<Mod>).
                                        CIniReader applies it as a per-key
                                        overlay AFTER base load/merge
                                        (Utils/INIReader.cpp:62-69).

All writes go through comment-preserving line surgery with a re-parse
self-check: the writer re-reads its own output and verifies the intended
keys changed and NOTHING else did; on mismatch the pre-image is restored
and IniWriteError raised. A malformed file can never persist.

Effective-value resolution mirrors the engine: profile stack (per-key
merge for MERGE_INI_FILES-registered files, whole-file top-resolve
otherwise) → `.Override` overlay → schema default; provenance names the
winning layer. An optional stock-baseline install supplies `stock_value`
for the author-hat "vs stock 1.13" diff.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from .vfs import VfsConfigError, VfsLayout

SCHEMAS_DIR = Path(__file__).parent / "data" / "ini_schemas"

# Canonical editable set (must match tools/build_ini_schemas.py and the
# engine-facts matrix). Maps lowercase name -> canonical name.
EDITABLE_INIS: dict[str, str] = {f.lower(): f for f in (
    "Ja2.ini",
    "Ja2_Options.ini",
    "APBPConstants.ini",
    "AI.ini",
    "CTHConstants.ini",
    "Creatures_Settings.INI",
    "Helicopter_Settings.INI",
    "IntroVideos.ini",
    "Item_Settings.ini",
    "Mod_Settings.ini",
    "Morale_Settings.INI",
    "RebelCommand_Settings.ini",
    "Reputation_Settings.INI",
    "Skills_Settings.INI",
    "Taunts_Settings.INI",
)}

# Files with NO Play-mode mechanism (see module docstring).
PLAY_MODE_REFUSED = {"ai.ini"}

# Engine-authored files the editor must never touch (engine-facts §4).
HANDS_OFF = {
    "ja2_settings.ini", "ja2_features.ini", "ja2_sp.ini", "ja2_mp.ini",
}

# Key/value sanitization: keys are engine identifiers; values must not
# break the line model. (INI injection guard — review finding D7.)
_VALID_KEY = re.compile(r"^[A-Za-z0-9_.\-]+$")
_VALID_SECTION = re.compile(r"^[^\[\]\r\n;#]+$")


class IniEditorError(Exception):
    """Structured failure; `code` maps to an API error code."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


class IniWriteError(IniEditorError):
    pass


# ───────────────────────────── schemas ──────────────────────────────────────

def canonical_ini_name(ini_file: str) -> str:
    """Whitelist gate: resolve any-cased name to the canonical entry or
    raise. Also kills path traversal — only bare known names pass."""
    canon = EDITABLE_INIS.get(ini_file.strip().lower())
    if canon is None:
        raise IniEditorError(
            "INI_FILE_UNKNOWN",
            f"Not an editable INI: {ini_file!r}. Editable: "
            + ", ".join(sorted(EDITABLE_INIS.values())),
        )
    return canon


def load_schema(ini_file: str) -> dict:
    canon = canonical_ini_name(ini_file)
    path = SCHEMAS_DIR / (Path(canon).stem + ".json")
    if not path.is_file():
        raise IniEditorError("SCHEMA_NOT_FOUND", f"No schema JSON for {canon}")
    return json.loads(path.read_text(encoding="utf-8"))


def list_schemas() -> list[dict]:
    index_path = SCHEMAS_DIR / "index.json"
    if not index_path.is_file():
        return []
    return json.loads(index_path.read_text(encoding="utf-8"))["schemas"]


def schema_key_index(schema: dict) -> dict[str, dict]:
    """{'Section/KEY' (lowercased): property} for validation lookups."""
    idx: dict[str, dict] = {}
    for sect in schema["sections"]:
        for p in sect["properties"]:
            idx[f"{sect['name']}/{p['name']}".lower()] = p
    return idx


# ───────────────────────── INI text model (surgery) ─────────────────────────

_SECTION_RE = re.compile(r"^\s*\[(?P<name>[^\]]*)\]\s*$")
_KV_RE = re.compile(r"^\s*(?P<key>[^=;#\[\]]+?)\s*=(?P<rest>.*)$")


def _decode(raw: bytes) -> tuple[str, str]:
    """Byte-safe decode. Returns (text, bom) where bom is '' or the
    UTF-8 BOM. surrogateescape round-trips any non-UTF-8 byte."""
    bom = ""
    if raw.startswith(b"\xef\xbb\xbf"):
        bom = "﻿"
        raw = raw[3:]
    return raw.decode("utf-8", errors="surrogateescape"), bom


def _encode(text: str, bom: str) -> bytes:
    out = text.encode("utf-8", errors="surrogateescape")
    if bom:
        out = b"\xef\xbb\xbf" + out
    return out


def _dominant_eol(text: str) -> str:
    crlf = text.count("\r\n")
    lf = text.count("\n") - crlf
    return "\r\n" if crlf >= lf else "\n"


def parse_ini_map(text: str) -> dict[str, dict[str, str]]:
    """Engine-equivalent read: {section: {key: value}} (last write wins,
    inline `;` NOT stripped from values — matching vfs PropertyContainer).
    Section/key lookups by callers should lowercase both sides."""
    out: dict[str, dict[str, str]] = {}
    current = ""
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped[0] in ";#":
            continue
        m = _SECTION_RE.match(line)
        if m:
            current = m.group("name").strip()
            continue
        m = _KV_RE.match(line)
        if m:
            out.setdefault(current, {})[m.group("key").strip()] = m.group("rest").strip()
    return out


def _ci_get(mapping: dict[str, dict[str, str]], section: str, key: str) -> Optional[str]:
    for sname, keys in mapping.items():
        if sname.lower() == section.lower():
            for k, v in keys.items():
                if k.lower() == key.lower():
                    return v
    return None


@dataclass
class IniChange:
    section: str
    key: str
    value: Optional[str]          # None = delete the key
    ini_file: str = ""            # filled by the caller

    def validate(self) -> None:
        if not _VALID_SECTION.match(self.section or ""):
            raise IniEditorError("BAD_SECTION", f"Invalid section name: {self.section!r}")
        if not _VALID_KEY.match(self.key or ""):
            raise IniEditorError("BAD_KEY", f"Invalid key name: {self.key!r}")
        if self.value is not None and re.search(r"[\r\n]", self.value):
            raise IniEditorError("BAD_VALUE", "Value must not contain newlines")


def surgical_upsert(path: Path, changes: list[IniChange],
                    new_file_header: str | None = None) -> dict:
    """Comment-preserving upsert/delete of keys in one INI file.

    Rules (review-derived, see engine-facts §7):
      - existing key: edit the LAST occurrence in its section (engine
        merge semantics are last-wins); earlier duplicates are left
        untouched but the self-check uses last-wins parsing so the
        result is engine-correct.
      - delete: remove EVERY occurrence in the section (removing only
        the last would resurrect an earlier duplicate).
      - missing section: appended at EOF with one separating blank line.
      - missing file: created (with optional header comment).
      - untouched lines stay byte-identical (comments, spacing, EOLs).

    Self-check: re-parse the result; the engine-visible map must equal
    the pre-image map with exactly the requested mutations applied.
    On any mismatch the pre-image bytes are restored and IniWriteError
    raised. Returns {created: bool, mutated: [...]}.
    """
    for c in changes:
        c.validate()

    existed = path.is_file()
    pre_raw = path.read_bytes() if existed else b""
    text, bom = _decode(pre_raw) if existed else ("", "")
    eol = _dominant_eol(text) if text else "\r\n"

    pre_map = parse_ini_map(text)

    # Compute the expected post map (deep copy + mutations, case-insensitive
    # section/key matching against existing entries).
    import copy
    expected = copy.deepcopy(pre_map)

    def _find_section_name(m: dict, section: str) -> Optional[str]:
        for s in m:
            if s.lower() == section.lower():
                return s
        return None

    for c in changes:
        sname = _find_section_name(expected, c.section)
        if c.value is None:
            if sname is not None:
                keys = expected[sname]
                for k in [k for k in keys if k.lower() == c.key.lower()]:
                    del keys[k]
            continue
        if sname is None:
            expected[c.section] = {c.key: c.value}
        else:
            keys = expected[sname]
            kname = next((k for k in keys if k.lower() == c.key.lower()), c.key)
            keys.pop(kname, None)
            keys[kname if kname.lower() == c.key.lower() else c.key] = c.value

    # ---- line surgery ----
    lines = text.splitlines(keepends=True) if text else []

    # Index sections: name -> (header_idx, [kv line indices by key-lower])
    sec_order: list[tuple[str, int]] = []          # (name, header line idx)
    kv_at: dict[tuple[str, str], list[int]] = {}   # (sec-lower, key-lower) -> line idxs
    last_content_in_sec: dict[str, int] = {}       # sec-lower -> last non-blank idx
    current = ""
    for i, line in enumerate(lines):
        m = _SECTION_RE.match(line)
        if m:
            current = m.group("name").strip()
            sec_order.append((current, i))
            last_content_in_sec[current.lower()] = i
            continue
        stripped = line.strip()
        if stripped and stripped[0] not in ";#":
            m = _KV_RE.match(line)
            if m:
                kv_at.setdefault((current.lower(), m.group("key").strip().lower()), []).append(i)
        if stripped:
            last_content_in_sec[current.lower()] = i

    deletions: set[int] = set()
    replacements: dict[int, str] = {}
    insertions: dict[int, list[str]] = {}   # insert AFTER line idx
    appended_sections: dict[str, list[str]] = {}
    appended_lower: dict[str, str] = {}     # lower → original spelling
    known_secs = {s.lower() for s, _ in sec_order}

    for c in changes:
        sec_l, key_l = c.section.lower(), c.key.lower()
        idxs = kv_at.get((sec_l, key_l), [])
        if c.value is None:
            deletions.update(idxs)
            continue
        if idxs:
            # edit LAST occurrence; preserve the original key spelling
            i = idxs[-1]
            m = _KV_RE.match(lines[i])
            orig_key = m.group("key").strip() if m else c.key
            replacements[i] = f"{orig_key} = {c.value}{eol}"
        elif sec_l in appended_lower:
            # second/later key for a section this batch is CREATING —
            # append to that pending section, not to the original text.
            appended_sections[appended_lower[sec_l]].append(
                f"{c.key} = {c.value}{eol}")
        elif sec_l in known_secs:
            anchor = last_content_in_sec[sec_l]
            insertions.setdefault(anchor, []).append(f"{c.key} = {c.value}{eol}")
        else:
            appended_sections.setdefault(c.section, []).append(
                f"{c.key} = {c.value}{eol}")
            appended_lower[sec_l] = c.section

    out: list[str] = []
    for i, line in enumerate(lines):
        if i in deletions:
            continue
        out.append(replacements.get(i, line))
        if i in insertions:
            out.extend(insertions[i])
    # ensure trailing newline before appending sections
    if out and not out[-1].endswith(("\n", "\r")):
        out[-1] += eol
    for sec_name, kv_lines in appended_sections.items():
        if out:
            out.append(eol)
        elif new_file_header:
            out.extend(h + eol for h in new_file_header.splitlines())
            out.append(eol)
        out.append(f"[{sec_name}]{eol}")
        out.extend(kv_lines)

    new_text = "".join(out)

    # ---- self-check: engine-visible result must match expectations ----
    post_map = parse_ini_map(new_text)

    def _norm(m: dict[str, dict[str, str]]) -> dict:
        return {
            s.lower(): {k.lower(): v for k, v in keys.items()}
            for s, keys in m.items() if keys
        }

    if _norm(post_map) != _norm(expected):
        if existed:
            path.write_bytes(pre_raw)   # restore pre-image
        else:
            path.unlink(missing_ok=True)
        raise IniWriteError(
            "WRITE_SELFCHECK_FAILED",
            f"Post-write parse of {path.name} did not match the expected "
            "result; file restored to its pre-write state.",
        )

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(_encode(new_text, bom))

    # belt-and-braces: re-read from disk and re-verify
    post_disk = parse_ini_map(_decode(path.read_bytes())[0])
    if _norm(post_disk) != _norm(expected):
        if existed:
            path.write_bytes(pre_raw)
        else:
            path.unlink(missing_ok=True)
        raise IniWriteError(
            "WRITE_SELFCHECK_FAILED",
            f"On-disk re-read of {path.name} did not match; restored.",
        )

    return {"created": not existed, "path": str(path),
            "mutated": [f"{c.section}/{c.key}" for c in changes]}


# ─────────────────────── engine-running guard ───────────────────────────────

def game_running(exe_name: str = "ja2.exe") -> bool:
    """True if a process with this image name is running (Windows).

    The engine rewrites its profile-dir INIs on options-apply/save/exit
    (engine-facts §4) — writing while it runs risks mutual clobber.

    CREATE_NO_WINDOW is essential: this runs on a 5s poll from the UI,
    and without it every tasklist spawn flashes a console window when
    the sidecar is the windowed PyInstaller exe (user-visible strobe,
    found in Phase-2 Gate 3)."""
    try:
        out = subprocess.run(
            ["tasklist", "/FI", f"IMAGENAME eq {exe_name}", "/FO", "CSV", "/NH"],
            capture_output=True, text=True, timeout=10,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        ).stdout
    except (OSError, subprocess.TimeoutExpired):
        return False  # can't tell — don't hard-block on guard failure
    return exe_name.lower() in out.lower()


# ─────────────────────── effective-value resolution ─────────────────────────

def _merge_registered(install_root: Path) -> set[str]:
    """Lowercased filenames listed in MERGE_INI_FILES / MERGE_INI_FILES_UB
    in the root Ja2.ini."""
    ja2 = _root_ja2_ini(install_root)
    if ja2 is None:
        return set()
    m = parse_ini_map(_decode(ja2.read_bytes())[0])
    names: set[str] = set()
    for key in ("MERGE_INI_FILES", "MERGE_INI_FILES_UB"):
        val = _ci_get(m, "Ja2 Settings", key)
        if val:
            names.update(x.strip().lower() for x in val.split(",") if x.strip())
    return names


def _root_ja2_ini(install_root: Path) -> Optional[Path]:
    for name in ("Ja2.ini", "ja2.ini", "JA2.INI"):
        p = install_root / name
        if p.is_file():
            return p
    return None


def override_filename(ini_file: str) -> str:
    """`Skills_Settings.INI` -> `Skills_Settings.Override` — exactly what
    the engine's _splitpath/_makepath builds (INIReader.cpp:62-69)."""
    return Path(ini_file).stem + ".Override"


def _find_in_dir_ci(directory: Path, filename: str) -> Optional[Path]:
    exact = directory / filename
    if exact.is_file():
        return exact
    if directory.is_dir():
        low = filename.lower()
        for p in directory.iterdir():
            if p.is_file() and p.name.lower() == low:
                return p
    return None


@dataclass
class EffectiveEntry:
    value: Optional[str]
    source: str                   # profile name | 'override' | 'ja2_ini' | 'default' | 'unset'
    stock_value: Optional[str] = None
    override_active: bool = False


class IniEditor:
    """Per-install editor session. `layout` must come from
    parse_vfs_config(install_root); `baseline_root` (optional) is the
    frozen stock install used for `stock_value`."""

    def __init__(self, layout: VfsLayout,
                 baseline_root: Optional[Path] = None):
        self.layout = layout
        self.install_root = layout.install_root
        self.baseline_root = baseline_root

    # ---- resolution ----

    def _layer_copies(self, ini_file: str) -> list[tuple[str, Path]]:
        """(profile_name, path) for every layer holding the file,
        lowest priority first (engine merge order)."""
        hits: list[tuple[str, Path]] = []
        for profile in self.layout.profiles:
            for loc in profile.locations:
                if not loc.is_directory:
                    continue
                p = _find_in_dir_ci(loc.path, ini_file)
                if p is not None:
                    hits.append((profile.name, p))
        return hits

    def _override_copy(self, ini_file: str) -> Optional[tuple[str, Path]]:
        """Topmost layer holding `<stem>.Override` (engine resolves it
        through the normal read chain)."""
        ovr = override_filename(ini_file)
        for profile in reversed(self.layout.profiles):
            for loc in profile.locations:
                if not loc.is_directory:
                    continue
                p = _find_in_dir_ci(loc.path, ovr)
                if p is not None:
                    return profile.name, p
        return None

    def effective(self, ini_file: str) -> dict:
        """Bulk effective map for one INI: engine-faithful resolution +
        provenance + stock baseline. Returns
        {sections: {Section: {KEY: EffectiveEntry-dict}}, meta: {...}}."""
        canon = canonical_ini_name(ini_file)
        schema = load_schema(canon)

        if canon == "Ja2.ini":
            return self._effective_root_ja2(schema)

        merged_names = _merge_registered(self.install_root)
        copies = self._layer_copies(canon)

        result: dict[str, dict[str, dict]] = {}
        if canon.lower() in merged_names:
            layer_seq = copies                      # per-key merge, all layers
        else:
            layer_seq = copies[-1:]                 # whole-file: topmost only

        for profile_name, path in layer_seq:
            m = parse_ini_map(_decode(path.read_bytes())[0])
            for sect, keys in m.items():
                for k, v in keys.items():
                    result.setdefault(sect, {})[k] = {
                        "value": v, "source": profile_name,
                        "override_active": False,
                    }

        ovr = self._override_copy(canon)
        if ovr is not None:
            _, ovr_path = ovr
            m = parse_ini_map(_decode(ovr_path.read_bytes())[0])
            for sect, keys in m.items():
                for k, v in keys.items():
                    entry = result.setdefault(sect, {}).setdefault(k, {})
                    entry.update({"value": v, "source": "override",
                                  "override_active": True})

        self._add_schema_and_baseline(canon, schema, result)
        ewp = self.layout.engine_write_profile()
        return {
            "ini_file": canon,
            "merge_registered": canon.lower() in merged_names,
            "override_file": override_filename(canon),
            "override_present": ovr is not None,
            "writable_profile": ewp.name if ewp else None,
            "profile_root": str(ewp.profile_root) if ewp and ewp.profile_root else None,
            "sections": result,
        }

    def _effective_root_ja2(self, schema: dict) -> dict:
        ja2 = _root_ja2_ini(self.install_root)
        result: dict[str, dict[str, dict]] = {}
        if ja2 is not None:
            m = parse_ini_map(_decode(ja2.read_bytes())[0])
            for sect, keys in m.items():
                for k, v in keys.items():
                    result.setdefault(sect, {})[k] = {
                        "value": v, "source": "ja2_ini", "override_active": False,
                    }
        self._add_schema_and_baseline("Ja2.ini", schema, result)
        return {
            "ini_file": "Ja2.ini",
            "merge_registered": False,
            "override_file": None,
            "override_present": False,
            "writable_profile": None,
            "profile_root": None,
            "sections": result,
        }

    def _add_schema_and_baseline(self, canon: str, schema: dict,
                                 result: dict[str, dict[str, dict]]) -> None:
        """Fill schema defaults for unset keys + stock baseline values."""
        baseline_map: Optional[dict] = None
        if self.baseline_root is not None:
            if canon == "Ja2.ini":
                base_path = _root_ja2_ini(self.baseline_root)
            else:
                base_path = _find_in_dir_ci(self.baseline_root / "Data-1.13", canon)
            if base_path is not None and base_path.is_file():
                baseline_map = parse_ini_map(_decode(base_path.read_bytes())[0])

        for sect in schema["sections"]:
            for p in sect["properties"]:
                entry = None
                # case-insensitive lookup into result
                for sname, keys in result.items():
                    if sname.lower() == sect["name"].lower():
                        for k in keys:
                            if k.lower() == p["name"].lower():
                                entry = keys[k]
                                break
                        break
                if entry is None:
                    dflt = p.get("default")
                    entry = {
                        "value": dflt,
                        "source": "default" if dflt is not None else "unset",
                        "override_active": False,
                    }
                    result.setdefault(sect["name"], {})[p["name"]] = entry
                if baseline_map is not None:
                    sv = _ci_get(baseline_map, sect["name"], p["name"])
                    if sv is not None:
                        entry["stock_value"] = sv

    def overrides(self) -> list[dict]:
        """Every Play-mode override this editor owns: keys in the engine
        write profile's `*.Override` files (+ partial merge copies)."""
        ewp = self.layout.engine_write_profile()
        if ewp is None or ewp.profile_root is None:
            return []
        out: list[dict] = []
        root = ewp.profile_root
        if not root.is_dir():
            return out
        for p in sorted(root.iterdir()):
            if not p.is_file():
                continue
            name_l = p.name.lower()
            base: Optional[str] = None
            if name_l.endswith(".override"):
                stem = p.stem
                base = next((c for c in EDITABLE_INIS.values()
                             if Path(c).stem.lower() == stem.lower()), None)
            elif name_l in EDITABLE_INIS and name_l not in HANDS_OFF:
                base = EDITABLE_INIS[name_l]
            if base is None:
                continue
            m = parse_ini_map(_decode(p.read_bytes())[0])
            for sect, keys in m.items():
                for k, v in keys.items():
                    out.append({
                        "ini_file": base, "section": sect, "key": k,
                        "value": v, "file": str(p),
                    })
        return out

    # ---- write targets ----

    def write_target(self, ini_file: str, target: str) -> Path:
        """The path a change to `ini_file` must be written to, per the
        engine-facts matrix. `target` is 'canon' or 'override'."""
        canon = canonical_ini_name(ini_file)
        if canon == "Ja2.ini":
            ja2 = _root_ja2_ini(self.install_root)
            if ja2 is None:
                raise IniEditorError("JA2_INI_NOT_FOUND",
                                     f"No Ja2.ini under {self.install_root}")
            return ja2

        if target == "canon":
            existing = self.layout.resolve_read(canon)
            if existing is not None:
                return existing
            return self.layout.mod_content_path(canon)

        if target == "override":
            if canon.lower() in PLAY_MODE_REFUSED:
                raise IniEditorError(
                    "PLAY_MODE_UNSUPPORTED",
                    f"{canon} has no engine override mechanism (2-arg "
                    "CIniReader ctor; see INI_EDITOR_ENGINE_FACTS.md). "
                    "Edit it in Author mode instead.",
                )
            return self.layout.resolve_override_write(override_filename(canon))

        raise IniEditorError("BAD_TARGET", f"target must be 'canon' or 'override', got {target!r}")

    def apply_changes(self, changes: list[IniChange], target: str,
                      exe_name: str = "ja2.exe",
                      dry_run: bool = False) -> dict:
        """Atomic-per-file batch apply. Groups changes by ini_file,
        computes targets, runs the guards, then performs one surgical
        write per file. Returns per-file results; raises IniEditorError
        on guard failure BEFORE any write."""
        if not changes:
            return {"applied": 0, "files": []}
        if game_running(exe_name):
            raise IniEditorError(
                "GAME_RUNNING",
                f"{exe_name} is running — the engine rewrites its profile "
                "INIs on options-apply/save/exit; close it before editing.",
            )

        by_file: dict[str, list[IniChange]] = {}
        for c in changes:
            canon = canonical_ini_name(c.ini_file)
            c.validate()
            by_file.setdefault(canon, []).append(c)

        plans: list[tuple[str, Path, list[IniChange]]] = []
        for canon, file_changes in by_file.items():
            path = self.write_target(canon, target)
            plans.append((canon, path, file_changes))

        if dry_run:
            return {
                "applied": 0, "dry_run": True,
                "files": [{
                    "ini_file": canon, "path": str(path),
                    "changes": [
                        {"section": c.section, "key": c.key, "value": c.value}
                        for c in fc],
                } for canon, path, fc in plans],
            }

        results = []
        header = (
            ";; Play-mode override written by MercForge's INI editor.\n"
            ";; Per-key overlay applied by the engine after the base file\n"
            ";; (CIniReader Override hook). Safe to delete to revert."
        )
        for canon, path, fc in plans:
            res = surgical_upsert(
                path, fc,
                new_file_header=header if target == "override" else None,
            )
            res["ini_file"] = canon
            results.append(res)
        return {"applied": sum(len(fc) for _, _, fc in plans), "files": results}


# ─────────────────────── diagnostics (engine logs) ──────────────────────────

def parse_vfs_log(path: Path) -> list[dict]:
    """Parse the engine's vfs.log into mounted layers.

    Lines look like:
      [0.476243] :   Reading profile : SLF Libs
      [0.476248] :     library : "Data\\Ambient.slf"
      [0.600463] :     directory : "Data"
    """
    if not path.is_file():
        return []
    layers: list[dict] = []
    current: Optional[str] = None
    text = path.read_text(encoding="utf-8", errors="replace")
    for raw in text.splitlines():
        if "] :" not in raw:
            continue
        payload = raw.split("] :", 1)[1].strip()
        if payload.startswith("Reading profile :"):
            current = payload.split(":", 1)[1].strip()
        elif payload.startswith("library :") and current:
            layers.append({"name": current, "kind": "library",
                           "path": payload.split(":", 1)[1].strip().strip('"')})
        elif payload.startswith("directory :") and current:
            layers.append({"name": current, "kind": "directory",
                           "path": payload.split(":", 1)[1].strip().strip('"')})
    return layers


def parse_ini_error_report(path: Path) -> tuple[list[dict], int]:
    """Parse iniErrorReport.log → (errors, first_boot_noise_count).

    Faithful port of the frozen launcher's classifier with two fixes:
    identical (section,key,message) rows are DEDUPED (the engine logs a
    re-read pass twice per launch), and bracket-prefixed lines (incl.
    the CD-key line) are never emitted. `empty_toption` classification
    is advisory — no real log has exercised it on this engine yet.
    """
    if not path.is_file():
        return [], 0
    errors: list[dict] = []
    seen: set[tuple[str, str, str]] = set()
    noise = 0
    text = path.read_text(encoding="utf-8", errors="replace")
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("***") or line.startswith("["):
            # timestamp banner / CD-key line / decorative — but NOTE the
            # engine prefixes real rows with "[3.28e-05] : " too, so only
            # skip if no payload follows a "] :" marker.
            if "] :" in line:
                line = line.split("] :", 1)[1].strip()
            else:
                continue
        if "The value [" not in line:
            continue
        rest = line.split("The value [", 1)[1]
        if "][" not in rest:
            continue
        section, after = rest.split("][", 1)
        if "]" not in after:
            continue
        key = after.split("]", 1)[0]
        dedupe_key = (section, key, line)
        if dedupe_key in seen:
            continue
        seen.add(dedupe_key)
        if "outside the valid range" in line:
            kind, is_noise = "out_of_range", False
        elif "neither TRUE nor FALSE" in line and '= ""' in line:
            kind, is_noise = "empty_toption", True
            noise += 1
        elif "Error when opening file" in line:
            kind, is_noise = "file_not_found", False
        else:
            kind, is_noise = "other", False
        errors.append({
            "section": section, "key": key, "message": line,
            "kind": kind, "is_first_boot_noise": is_noise,
        })
    return errors, noise


def read_log_timestamp(path: Path) -> Optional[str]:
    """First '***'-banner line of a log, raw (NOT ISO — it's a ctime-ish
    locale string like ' *** Sat Jun  6 21:26:19 2026 *** ')."""
    if not path.is_file():
        return None
    text = path.read_text(encoding="utf-8", errors="replace")
    for line in text.splitlines():
        if "***" in line:
            return line.strip().strip("*").strip()
    return None


def diagnostic_report(layout: VfsLayout) -> dict:
    """Last-launch health for the active campaign's profile."""
    ewp = layout.engine_write_profile()
    if ewp is None or ewp.profile_root is None:
        return {"profile_root": None, "vfs_layers": [], "errors": [],
                "first_boot_noise_count": 0, "last_launch_raw": None,
                "log_mtime": None}
    root = ewp.profile_root
    err_log = root / "iniErrorReport.log"
    errors, noise = parse_ini_error_report(err_log)
    return {
        "profile_root": str(root),
        "writable_profile": ewp.name,
        "vfs_layers": parse_vfs_log(root / "vfs.log"),
        "errors": errors,
        "first_boot_noise_count": noise,
        "last_launch_raw": read_log_timestamp(root / "vfs.log"),
        "log_mtime": err_log.stat().st_mtime if err_log.is_file() else None,
    }


def summary(editor: "IniEditor") -> list[dict]:
    """Per-file changed counts for the file selector: Play overrides
    (cheap: one profile scan) + Author vs-reference diffs (only when a
    baseline is configured), with per-section breakdowns."""
    ov = editor.overrides()
    play_by_file: dict[str, dict[str, int]] = {}
    for o in ov:
        play_by_file.setdefault(o["ini_file"], {}).setdefault(o["section"], 0)
        play_by_file[o["ini_file"]][o["section"]] += 1

    out: list[dict] = []
    for canon in sorted(EDITABLE_INIS.values()):
        play_sections = play_by_file.get(canon, {})
        entry: dict = {
            "ini_file": canon,
            "override_changed": sum(play_sections.values()),
            "play_sections": play_sections,
            "author_changed": None,
            "author_sections": None,
        }
        if editor.baseline_root is not None:
            try:
                eff = editor.effective(canon)
                a_sections: dict[str, int] = {}
                for sname, keys in eff["sections"].items():
                    for entry_k in keys.values():
                        sv = entry_k.get("stock_value")
                        if (sv is not None and entry_k.get("value") is not None
                                and entry_k.get("source") not in ("default", "unset", "override")
                                and entry_k["value"] != sv):
                            a_sections[sname] = a_sections.get(sname, 0) + 1
                entry["author_changed"] = sum(a_sections.values())
                entry["author_sections"] = a_sections
            except IniEditorError:
                pass
        out.append(entry)
    return out


def validate_against_schema(schema: dict, change: IniChange) -> Optional[str]:
    """Advisory validation. Returns a warning string or None. NEVER
    blocks: scraped ranges are unreliable and the engine clamps+logs
    anyway (engine-facts §1)."""
    idx = schema_key_index(schema)
    p = idx.get(f"{change.section}/{change.key}".lower())
    if p is None:
        return f"Key not in schema (unknown keys are written verbatim): {change.section}/{change.key}"
    if change.value is None:
        return None
    dt = (p.get("datatype") or "").lower()
    v = change.value.strip()
    if dt == "boolean" and v.upper() not in ("TRUE", "FALSE", "0", "1"):
        return f"Schema says boolean; got {v!r}"
    if dt == "numeric":
        try:
            fv = float(v)
        except ValueError:
            return f"Schema says numeric; got {v!r}"
        lo, hi = p.get("min"), p.get("max")
        conf = p.get("confidence", "scraped")
        try:
            if lo is not None and fv < float(lo):
                return (f"Below {conf} min {lo} — engine will "
                        f"{'clamp' if conf == 'engine' else 'maybe clamp'} to {lo}")
            if hi is not None and fv > float(hi):
                return (f"Above {conf} max {hi} — engine will "
                        f"{'clamp' if conf == 'engine' else 'maybe clamp'} to {hi}")
        except ValueError:
            pass
    return None
