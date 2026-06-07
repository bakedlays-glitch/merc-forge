"""Security tests — path traversal in .wmerc imports, etc.

The 2026-05-15 review caught a real zip-slip: `_is_safe_arcname` used
`all(...) or parts[-1] != ""` which let `"../etc/passwd"` through because
the trailing-segment-nonempty check short-circuited the traversal check.
These tests lock down the safe path so the regression can't return.
"""
from __future__ import annotations

import io
import json
import zipfile
from pathlib import Path

import pytest

from mercwizard_core.bundle.import_ import _is_safe_arcname, deploy_import, read_wmerc
from mercwizard_core.bundle.manifest import WmercManifest
from mercwizard_core.models import Merc


# ──────────────────────────────────────────────────────────────────────────
#  _is_safe_arcname — the boolean predicate
# ──────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("name", [
    "manifest.json",
    "voice/MERC220_001.wav",
    "raw_stis/Faces/218.STI",
    "audio/battlesnds/218_ATTN.ogg",
    "table_rows/Backgrounds.xml",
    "anim_eye_1.png",
    "deeply/nested/legit/file.txt",
])
def test_is_safe_arcname_accepts_legit_paths(name: str) -> None:
    assert _is_safe_arcname(name), f"legit arcname rejected: {name!r}"


@pytest.mark.parametrize("name", [
    # Traversal — these used to slip through the old `or` short-circuit.
    "../etc/passwd",
    "..\\etc\\passwd",
    "foo/../bar",
    "foo/..",
    "raw_stis/../../../Windows/System32/foo.dll",
    "voice/../secret.png",
    "../../escape.txt",
    "./hidden",
    "foo/./bar",
    # Absolute paths
    "/etc/passwd",
    "\\Windows\\System32\\foo.dll",
    # Windows drive letters
    "C:/Windows/foo.dll",
    "D:\\foo",
    # Empty / NUL
    "",
    "foo\x00bar.png",
    # Pure traversal token
    "..",
    ".",
])
def test_is_safe_arcname_rejects_unsafe_paths(name: str) -> None:
    assert not _is_safe_arcname(name), f"unsafe arcname accepted: {name!r}"


# ──────────────────────────────────────────────────────────────────────────
#  End-to-end: deploy_import containment
# ──────────────────────────────────────────────────────────────────────────


def _hand_built_bundle_with_traversal_entry(out_path: Path, arc_traversal: str) -> Path:
    """Build a minimal valid .wmerc plus one malicious arcname for zip-slip
    testing. The traversal entry uses one of the categories the importer's
    `_install_extras` routes (raw_stis/, audio/, big_items/) — those are the
    code paths that take attacker-controlled remainder strings and concat
    them onto disk paths.
    """
    merc = Merc(
        uiIndex=220, ubFaceIndex=220, Type=1,
        zName="Tycho", zNickname="Tycho",
    )
    manifest = WmercManifest(merc=merc, gear=[])
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(out_path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("manifest.json", json.dumps(manifest.model_dump(mode="json")))
        zf.writestr(arc_traversal, b"PWNED")
    return out_path


def test_read_wmerc_silently_drops_traversal_entries(tmp_path: Path) -> None:
    """`read_wmerc` filters arcnames via `_is_safe_arcname`. A bundle with a
    `../../escape.png` entry should not surface that entry in `contents.files`.
    """
    bundle = _hand_built_bundle_with_traversal_entry(
        tmp_path / "evil.wmerc", "raw_stis/../../../escape.dll"
    )
    contents = read_wmerc(bundle)
    assert "raw_stis/../../../escape.dll" not in contents.files


def test_deploy_import_rejects_writes_outside_install_root(tmp_path: Path) -> None:
    """Even if a future bug let a traversal arcname slip past `read_wmerc`,
    `_install_extras::safe_write` must still refuse writes whose resolved
    target escapes the install root.

    We exercise this by hand-crafting a bundle whose `raw_stis/` entry has a
    traversal suffix, then asserting the file outside the install root does
    NOT appear after deploy.
    """
    install = tmp_path / "install"
    (install / "Data-1.13" / "TableData").mkdir(parents=True)
    (install / "Data-1.13" / "TableData" / "MercProfiles.xml").write_text(
        "<MERCPROFILES></MERCPROFILES>", encoding="utf-8"
    )

    # The file we DON'T want created — outside the install root entirely.
    canary = tmp_path / "ESCAPED.dll"
    assert not canary.exists()

    bundle = _hand_built_bundle_with_traversal_entry(
        tmp_path / "evil.wmerc",
        # Path that, if naively joined to install root, escapes via `..`.
        "raw_stis/../../ESCAPED.dll",
    )

    # The traversal arcname is dropped at read_wmerc time (the primary fix),
    # so deploy_import won't even see it in contents.files. This also confirms
    # the defense-in-depth resolve()+is_relative_to() check would catch it
    # if read_wmerc ever regressed.
    deploy_import(
        install_root=install, bundle_path=bundle,
        install_id="test", target_slot=220, force=True,
    )

    assert not canary.exists(), \
        "zip-slip succeeded: file created outside install root"


# ──────────────────────────────────────────────────────────────────────────
#  Voice write path — defense-in-depth containment (LOW-2, 2026-06-06 review)
# ──────────────────────────────────────────────────────────────────────────


def test_step9_voice_rejects_clip_resolving_outside_install_root(tmp_path: Path) -> None:
    """The voice write path must carry the same `relative_to(install_root)`
    backstop every other bundle write has. Even if a traversal voice entry
    bypassed `read_wmerc`'s `_is_safe_arcname` filter, `_step9_voice` must
    refuse a clip whose resolved dest escapes the install root.

    Build a slot_prefix (Vengeance) target — the layout that takes the raw
    `dest.write_bytes` path — and hand `_step9_voice` a `WmercContents` whose
    voice entry climbs out of the Speech dir, the way a future regression in
    the arcname guard would deliver it. `source_slot == resolved_slot` keeps
    `_rename_slot_in_filename` a no-op so the traversal reaches the write
    target verbatim.
    """
    from mercwizard_core.bundle.import_ import (
        ImportReport,
        WmercContents,
        _step9_voice,
    )
    from mercwizard_core.bundle.manifest import WmercVoiceMeta
    from mercwizard_core.install_context import make_install_context

    from .test_bundle import _populate_vengeance_install

    install = tmp_path / "veng_install"
    merc = _populate_vengeance_install(install, slot=218)
    target_ctx = make_install_context(install)
    assert target_ctx.flavor.voice_layout == "slot_prefix"

    # Aim the escape at tmp_path (just above the install root) so the canary
    # stays inside the test sandbox even if the backstop were absent. Derive
    # the climb depth from the real Speech root instead of hard-coding `..`s.
    speech_root = target_ctx.speech_root(for_write=True).resolve()
    depth = len(speech_root.relative_to(tmp_path.resolve()).parts)
    evil_name = "/".join([".."] * depth + ["ESCAPED.ogg"])
    canary = tmp_path / "ESCAPED.ogg"
    assert not canary.exists()

    manifest = WmercManifest(
        merc=merc,
        gear=[],
        voice=WmercVoiceMeta(voice_index=218, count=1, filenames=[evil_name]),
    )
    contents = WmercContents(
        manifest=manifest,
        files={f"voice/{evil_name}": b"PWNED"},
    )
    report = ImportReport(target_slot=218)

    _step9_voice(
        contents=contents,
        manifest=manifest,
        target_ctx=target_ctx,
        install_root=install,
        source_slot=218,
        resolved_slot=218,
        report=report,
    )

    assert not canary.exists(), \
        "voice zip-slip succeeded: clip written outside install root"
    assert report.voice_clips_copied == 0
    assert any("escapes install root" in f for f in report.partial_failures), \
        f"expected a containment rejection; got {report.partial_failures}"
