"""Backup discipline is a convention, not a mechanism — this test makes
it structural. Every mutating route (POST/PUT/DELETE) must either
reference the snapshot machinery in its source or carry an explicit
allowlist entry below WITH a reason.

History: the discipline was convention-only and three destructive paths
simply forgot it — gear PUT/DELETE (no snapshot at all), voice deletes
(unrecoverable clip loss), and PUT /merc/{slot} with a partial payload
(mutated availability/gear XML with a no-op rollback). Since fixed;
this test keeps the class closed: a new destructive route that neither
snapshots nor gets a conscious allowlist entry fails CI.
"""
from __future__ import annotations

import ast
import importlib
import inspect
import textwrap
from pathlib import Path

from fastapi import HTTPException

ROUTE_MODULES = [
    "app_settings", "backgrounds", "backgrounds_library", "backup",
    "bundle", "facegear", "game", "gear", "graphics", "health",
    "ini_editor", "ini_presets", "installs", "items", "mapforge",
    "mapforge_library", "mapforge_placement", "merc", "portrait", "roster", "saves", "setup",
    "slot_picker", "slots", "state", "tools", "traits", "voice",
]

# Tokens that count as "this handler participates in the backup
# discipline" — matched against the AST (called-function names + string
# literals), not raw source, so a comment or docstring mentioning
# "backup" no longer satisfies the tripwire. Still loose on purpose —
# the goal is a conscious decision per route, not a proof.
CALL_TOKENS = ("backup", "snapshot")
STRING_TOKENS = (".bak",)


def _calls_and_strings(fn) -> tuple[set[str], set[str]]:
    """Dotted call names + string constants from fn's AST. Empty sets on
    source/parse failure — the route then falls through to the allowlist
    check, same as the old getsource OSError path."""
    try:
        tree = ast.parse(textwrap.dedent(inspect.getsource(fn)))
    except (OSError, SyntaxError):
        return set(), set()
    calls: set[str] = set()
    strings: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            f = node.func
            parts = []
            while isinstance(f, ast.Attribute):
                parts.append(f.attr)
                f = f.value
            if isinstance(f, ast.Name):
                parts.append(f.id)
            if parts:
                calls.add(".".join(reversed(parts)).lower())
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            strings.add(node.value.lower())
    return calls, strings


def _is_terminal_legacy_refusal(fn) -> bool:
    """Recognize only a handler whose entire body is the 409 refusal."""
    try:
        tree = ast.parse(textwrap.dedent(inspect.getsource(fn)))
    except (OSError, SyntaxError):
        return False
    definitions = [n for n in ast.walk(tree)
                   if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]
    if len(definitions) != 1:
        return False
    body = definitions[0].body
    if len(body) != 1 or not isinstance(body[0], ast.Raise):
        return False
    call = body[0].exc
    if not isinstance(call, ast.Call) or not isinstance(call.func, ast.Name):
        return False
    if call.func.id != "HTTPException" or call.args:
        return False
    keywords = {kw.arg: kw.value for kw in call.keywords}
    status = keywords.get("status_code")
    detail = keywords.get("detail")
    if not (isinstance(status, ast.Constant) and status.value == 409):
        return False
    if not isinstance(detail, ast.Call) or len(detail.args) != 1:
        return False
    detail_func = detail.func
    if not (isinstance(detail_func, ast.Attribute)
            and detail_func.attr == "legacy_mutation_detail"):
        return False
    return isinstance(detail.args[0], ast.Name) and detail.args[0].id == "slot"

# (module, endpoint-name) pairs that mutate WITHOUT snapshotting, each
# with the reason that makes that OK. Adding a route here is a conscious
# reviewable act — that's the point.
ALLOWLIST: dict[tuple[str, str], str] = {
    ("app_settings", "put_settings"):
        "app state (state.json settings block), not install files",
    ("bundle", "export_bundle"):
        "writes a NEW .wmerc outside the install; touches nothing existing",
    ("bundle", "import_bundle"):
        "snapshots inside bundle deploy_import (import_.py, step-1 "
        "backup_mod.snapshot before any write)",
    ("bundle", "import_preview"):
        "read-only preview; deploy_import (which writes) snapshots",
    ("game", "launch"):
        "spawns ja2.exe; no file mutation",
    ("ini_editor", "open_profile_folder"):
        "opens Explorer; no file mutation",
    ("installs", "scan_vfs_configs"):
        "read-only scan of candidate installs",
    ("installs", "add_install"):
        "mutates app state (AppData state.json), not install files",
    ("installs", "remove_install"):
        "unregisters from app state; install files untouched",
    ("installs", "set_active"):
        "app state only",
    ("installs", "refresh"):
        "re-reads install metadata; no writes",
    ("mapforge", "extract_slf_to_loose"):
        "creates a NEW loose file from an SLF; refuses overwrite",
    ("mapforge", "open_session"):
        "in-memory session; no disk writes",
    ("mapforge", "close_session"):
        "in-memory session teardown",
    ("mapforge", "new_sector"):
        "creates a NEW .dat; overwrite requires explicit flag",
    ("mapforge", "session_recovery"):
        "restore swaps in-memory session state only; discard deletes the "
        "autosave snapshot OUTSIDE the install (the recovery file is itself "
        "the backup). No install file is written",
    ("mapforge", "set_appendix_model"):
        "in-memory session model only (sess.parsed under lock); disk writes "
        "happen at /save (snapshots) or /save-copy-as (new copy, allowlisted)",
    ("mapforge", "save_copy_as"):
        "writes a NEW copy outside the session's .dat; refuses an existing "
        "dest unless overwrite=true is explicit",
    ("mapforge", "sector_radar"):
        "captures the active install and atomically backs up + writes the "
        "derived radar under the physical-root/state transaction",
    ("mapforge", "update_sti_jsd"):
        "captures the active install and atomically backs up + writes the "
        "loose JSD under the physical-root/state transaction",
    ("mapforge_placement", "placement_check"):
        "read-only sitekit oracle over the in-memory session; mutates nothing on disk",
    ("merc", "update_merc"):
        "snapshots inside the _run_update worker (step 1, always)",
    ("merc", "move_merc"):
        "snapshots inside its move workers (_run_move_same_install and "
        "bundle move_between_installs' source snapshot)",
    ("merc", "duplicate_merc"):
        "snapshots inside its streaming worker",
    ("portrait", "preview"):
        "in-memory image preview; no disk writes",
    ("setup", "mark_offered"):
        "app state only",
    ("tools", "sti_save_frame"):
        "exports a PNG to a user-picked path OUTSIDE the install",
    ("tools", "slf_extract"):
        "extracts to a user-picked destination; additive",
    ("tools", "slf_extract_stream"):
        "extracts to a user-picked destination; additive",
}


def _mutating_endpoints():
    for mod_name in ROUTE_MODULES:
        mod = importlib.import_module(f"routes.{mod_name}")
        router = getattr(mod, "router", None)
        if router is None:
            continue
        for route in router.routes:
            methods = getattr(route, "methods", None) or set()
            if methods & {"POST", "PUT", "DELETE"}:
                yield mod_name, route


def test_every_mutating_route_snapshots_or_is_allowlisted():
    violations = []
    for mod_name, route in _mutating_endpoints():
        fn = route.endpoint
        key = (mod_name, fn.__name__)
        calls, strings = _calls_and_strings(fn)
        # Retired legacy handlers are deliberately still mounted for client
        # compatibility, but refuse before touching the install. They are no
        # longer mutating routes and therefore do not need backup coverage.
        if _is_terminal_legacy_refusal(fn):
            continue
        has_snapshot = (
            any(t in c for t in CALL_TOKENS for c in calls)
            or any(t in s for t in STRING_TOKENS for s in strings)
        )
        if not has_snapshot and key not in ALLOWLIST:
            violations.append(
                f"{mod_name}.{fn.__name__} ({sorted(route.methods)} "
                f"{route.path}) mutates without snapshotting — either "
                "snapshot before writing (see backup.snapshot / "
                "_backup_before_write) or add a reasoned ALLOWLIST entry."
            )
    assert not violations, "\n".join(violations)


def test_allowlist_has_no_stale_entries():
    """An allowlist entry whose route vanished (or now snapshots) is
    noise — prune it so the list stays an honest inventory."""
    live = {(m, r.endpoint.__name__) for m, r in _mutating_endpoints()}
    stale = [k for k in ALLOWLIST if k not in live]
    assert not stale, f"ALLOWLIST entries for nonexistent routes: {stale}"


def test_legacy_refusal_exemption_requires_terminal_raise():
    """Only an exact terminal 409 refusal may bypass snapshot discipline."""
    from routes import voice

    real = {
        route.endpoint.__name__: route.endpoint
        for route in voice.router.routes
        if route.path in {"/voice/{slot}/upload", "/voice/{slot}/{filename}", "/voice/{slot}"}
        and route.methods & {"POST", "PUT", "DELETE"}
    }
    assert all(_is_terminal_legacy_refusal(fn) for fn in real.values())

    def helper_then_write(slot: int):
        raise HTTPException(status_code=409, detail=voice.legacy_mutation_detail(slot))
        Path("install-file").write_bytes(b"unsafe")

    assert not _is_terminal_legacy_refusal(helper_then_write)
