"""Security tests — path traversal in .wmerc imports, etc.

The review caught a real zip-slip: `_is_safe_arcname` used
`all(...) or parts[-1] != ""` which let `"../etc/passwd"` through because
the trailing-segment-nonempty check short-circuited the traversal check.
These tests lock down the safe path so the regression can't return.
"""
from __future__ import annotations

import io
import json
import os
import queue
import socket
import subprocess
import sys
import threading
import zipfile
from http.client import HTTPConnection
from pathlib import Path

import pytest

import main
from mercwizard_core.bundle.import_ import _is_safe_arcname, deploy_import, read_wmerc
from mercwizard_core.bundle.manifest import WmercManifest
from mercwizard_core.models import Merc


# ──────────────────────────────────────────────────────────────────────────
#  Sidecar startup binding and host policy
# ──────────────────────────────────────────────────────────────────────────


def test_non_loopback_without_token_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    """Remote binding must require the sidecar's session token."""
    monkeypatch.delenv("MERCWIZARD_TOKEN", raising=False)

    with pytest.raises(SystemExit):
        main.validate_bind_security("0.0.0.0")


@pytest.mark.parametrize("host", ["127.0.0.1", "::1"])
def test_loopback_hosts_without_token_are_accepted(
    monkeypatch: pytest.MonkeyPatch, host: str
) -> None:
    """Every resolved loopback candidate remains safe without a token."""
    monkeypatch.delenv("MERCWIZARD_TOKEN", raising=False)

    main.validate_bind_security(host)


def test_localhost_resolving_non_loopback_without_token_is_rejected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A misleading hostname must not bypass the remote-bind token gate."""
    monkeypatch.delenv("MERCWIZARD_TOKEN", raising=False)
    monkeypatch.setattr(
        main.socket,
        "getaddrinfo",
        lambda *_args, **_kwargs: [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("203.0.113.7", 0))
        ],
    )

    with pytest.raises(SystemExit):
        main.validate_bind_security("localhost")


def test_bound_socket_owns_port_before_marker() -> None:
    """The announced port must already be listening, with no bind race."""
    sock, port = main.bind_server_socket("127.0.0.1", 0)
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=1):
            pass
    finally:
        sock.close()


@pytest.mark.skipif(sys.platform != "win32", reason="Windows socket exclusivity regression")
def test_bound_socket_exclusively_owns_port_on_windows() -> None:
    """A second loopback listener must not share the announced port."""
    sock, port = main.bind_server_socket("127.0.0.1", 0)
    contender = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        contender.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            contender.bind(("127.0.0.1", port))
            contender.listen(socket.SOMAXCONN)
            acquired_endpoint = True
        except OSError:
            acquired_endpoint = False
        assert not acquired_endpoint
    finally:
        contender.close()
        sock.close()


def _wait_for_sidecar_port(process: subprocess.Popen[str], timeout: float = 10.0) -> int:
    """Read the one startup marker without risking an unbounded pipe wait."""
    assert process.stdout is not None
    marker_lines: queue.Queue[str] = queue.Queue()
    reader = threading.Thread(
        target=lambda: marker_lines.put(process.stdout.readline()),
        name="sidecar-port-marker-reader",
        daemon=True,
    )
    reader.start()
    try:
        marker = marker_lines.get(timeout=timeout).strip()
    except queue.Empty as error:
        raise AssertionError("sidecar did not announce SIDECAR_PORT before timeout") from error
    assert marker.startswith("SIDECAR_PORT="), f"unexpected sidecar startup marker: {marker!r}"
    return int(marker.removeprefix("SIDECAR_PORT="))


def _get_sidecar_health(port: int, token: str | None) -> tuple[int, dict[str, object]]:
    """Exercise the real HTTP listener, not an in-process ASGI test client."""
    headers = {"X-MercWizard-Token": token} if token is not None else {}
    connection = HTTPConnection("127.0.0.1", port, timeout=3)
    try:
        connection.request("GET", "/api/v1/health", headers=headers)
        response = connection.getresponse()
        body = json.loads(response.read())
        return response.status, body
    finally:
        connection.close()


def _terminate_subprocess(process: subprocess.Popen[str]) -> None:
    """Stop only the subprocess started by this test, including a stuck server."""
    if process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)


@pytest.mark.skipif(sys.platform != "win32", reason="Windows pre-bound-socket regression")
def test_prebound_socket_serves_multiple_authenticated_requests_on_windows() -> None:
    """Uvicorn must retain a pre-bound Windows socket after its first request.

    This catches the release-only regression where the Tauri-side port marker
    was correct but `Server.run(sockets=[sock])` stopped accepting after one
    request.  Removing the `sockets=[sock]` handoff or breaking the auth layer
    makes this real-process test fail.
    """
    token = "test-prebound-socket-token"
    env = os.environ.copy()
    env["MERCWIZARD_TOKEN"] = token
    env.pop("MERCWIZARD_LIFELINE_PORT", None)
    env.pop("MERCWIZARD_LIFELINE_TOKEN", None)
    creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    process = subprocess.Popen(
        [sys.executable, "main.py", "--port", "0", "--log-level", "warning"],
        cwd=Path(__file__).resolve().parents[1],
        env=env,
        creationflags=creationflags,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        port = _wait_for_sidecar_port(process)
        for _ in range(5):
            status, payload = _get_sidecar_health(port, token)
            assert status == 200
            assert payload["ok"] is True
        status, payload = _get_sidecar_health(port, None)
        assert status == 401
        assert payload["detail"]["error"] == "UNAUTHORIZED"
        assert process.poll() is None, "sidecar exited after serving its requests"
    finally:
        _terminate_subprocess(process)


# ──────────────────────────────────────────────────────────────────────────
#  Shell lifeline — authenticated parent-loss shutdown
# ──────────────────────────────────────────────────────────────────────────


def _lifeline_listener() -> tuple[socket.socket, int]:
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    listener.settimeout(1)
    return listener, int(listener.getsockname()[1])


def test_lifeline_accepts_the_shell_token_before_the_backend_starts() -> None:
    """A valid shell lifeline is acknowledged and remains open."""
    listener, port = _lifeline_listener()
    token = "a" * 64
    observed = threading.Event()

    def shell() -> None:
        try:
            connection, _ = listener.accept()
        except OSError:
            return
        with connection:
            assert connection.recv(64) == token.encode("ascii")
            connection.sendall(b"MWL1")
            observed.set()

    thread = threading.Thread(target=shell, daemon=True)
    thread.start()
    connection: socket.socket | None = None
    try:
        connection = main.connect_parent_lifeline(port, token)
        assert observed.wait(1)
        assert connection.fileno() != -1
    finally:
        if connection is not None:
            connection.close()
        listener.close()
        thread.join(timeout=1)


def test_lifeline_rejects_a_listener_that_does_not_acknowledge_the_token() -> None:
    """The backend must not run after an unauthenticated lifeline handshake."""
    listener, port = _lifeline_listener()

    def impostor() -> None:
        try:
            connection, _ = listener.accept()
        except OSError:
            return
        with connection:
            connection.recv(64)
            connection.sendall(b"NOPE")

    thread = threading.Thread(target=impostor, daemon=True)
    thread.start()
    try:
        with pytest.raises(RuntimeError, match="lifeline"):
            main.connect_parent_lifeline(port, "b" * 64)
    finally:
        listener.close()
        thread.join(timeout=1)


def test_lifeline_watcher_requests_backend_exit_when_the_shell_connection_closes() -> None:
    """Hard shell loss closes the lifeline and terminates the backend runtime."""
    shell_connection, backend_connection = socket.socketpair()
    exited = threading.Event()
    exit_codes: list[int] = []

    def request_exit(code: int) -> None:
        exit_codes.append(code)
        exited.set()

    watcher = threading.Thread(
        target=main.watch_parent_lifeline,
        args=(backend_connection, request_exit),
        daemon=True,
    )
    watcher.start()
    shell_connection.close()

    assert exited.wait(1), "backend watcher did not react to parent loss"
    assert exit_codes == [0]
    watcher.join(timeout=1)


def test_lifeline_close_exits_a_real_backend_runtime() -> None:
    """A hard shell loss must stop the actual long-lived Python runtime."""
    listener, port = _lifeline_listener()
    listener.settimeout(5)
    token = "c" * 64
    sidecar_dir = Path(__file__).resolve().parents[1]
    site_packages = sidecar_dir / ".venv" / "Lib" / "site-packages"
    env = os.environ.copy()
    env["PYTHONPATH"] = os.pathsep.join([str(sidecar_dir), str(site_packages)])
    code = (
        "import main; "
        f"connection = main.connect_parent_lifeline({port}, {token!r}); "
        "main.watch_parent_lifeline(connection)"
    )
    creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    process = subprocess.Popen(
        [sys.executable, "-c", code],
        env=env,
        creationflags=creationflags,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        try:
            connection, _ = listener.accept()
        except TimeoutError as error:
            process.terminate()
            stdout, stderr = process.communicate(timeout=3)
            raise AssertionError(
                f"backend runtime did not connect to its lifeline: {stdout}{stderr}"
            ) from error
        with connection:
            assert connection.recv(64) == token.encode("ascii")
            connection.sendall(b"MWL1")
        assert process.wait(timeout=3) == 0
    finally:
        listener.close()
        if process.poll() is None:
            process.terminate()
            process.wait(timeout=3)


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
#  Voice write path — defense-in-depth containment (LOW-2)
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
