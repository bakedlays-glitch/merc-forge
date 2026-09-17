"""Mercwizard sidecar FastAPI entry point.

Usage:
    python main.py [--port N] [--host 127.0.0.1]

If --port is omitted, the OS picks one and it's printed to stdout as
`SIDECAR_PORT=<n>` so the Tauri shell can capture it.

The sidecar is single-writer (one mutating operation at a time, per
section F.2 of the plan). Concurrent reads are fine.
"""
from __future__ import annotations

import argparse
import ipaddress
import logging
import os
import secrets
import socket
import sys
import threading
import warnings
from pathlib import Path
from typing import Callable

# `fs` (PyFilesystem, needed by ja2py's SlfFS) still imports pkg_resources
# and emits a deprecation UserWarning on every startup. The actual risk is
# handled by the `setuptools<81` pin in requirements.txt; the warning is
# pure log noise, so drop it. Remove this filter when `fs` ships a
# pkg_resources-free release and the pin is lifted.
warnings.filterwarnings(
    "ignore",
    message="pkg_resources is deprecated as an API.*",
    category=UserWarning,
)


# ──────────────────────────────────────────────────────────────────────────
#  Defensive stdout/stderr patching (PyInstaller windowed safety)
# ──────────────────────────────────────────────────────────────────────────
# PyInstaller console=False bundles set sys.stdout/sys.stderr to None on
# Windows. Any print() or uvicorn log call then crashes with OSError 22.
# Even with console=True, certain spawn contexts can leave them broken.
# Redirect to a logfile under %APPDATA% if they're not usable.

_LOG_MAX_BYTES = 10 * 1024 * 1024  # 10 MB
_LOG_BACKUPS = 5


def _log_path() -> Path:
    return (
        Path(os.environ.get("APPDATA") or Path.home())
        / "MercWizard"
        / "logs"
        / "sidecar.log"
    )


def _rotate_log_if_oversized(path: Path) -> None:
    """Roll `sidecar.log` → `sidecar.log.1 .. .N` (deleting the oldest) when
    the current file exceeds `_LOG_MAX_BYTES`. Cheap one-shot check at
    startup — the sidecar process is short-lived enough relative to growth
    rate that per-session rotation is sufficient.
    """
    try:
        if not path.is_file() or path.stat().st_size <= _LOG_MAX_BYTES:
            return
        # Shift .N → .N+1, dropping the oldest
        for i in range(_LOG_BACKUPS, 0, -1):
            src = path.with_suffix(path.suffix + f".{i}")
            if not src.is_file():
                continue
            if i == _LOG_BACKUPS:
                src.unlink()
            else:
                src.rename(path.with_suffix(path.suffix + f".{i + 1}"))
        path.rename(path.with_suffix(path.suffix + ".1"))
    except OSError:
        # Best effort — logging must not crash startup.
        pass


def _patch_streams_if_needed() -> None:
    log_path = _log_path()
    needs_patch = False
    for stream_name in ("stdout", "stderr"):
        s = getattr(sys, stream_name, None)
        if s is None:
            needs_patch = True
            break
        try:
            s.write("")
            s.flush()
        except (OSError, ValueError, AttributeError):
            needs_patch = True
            break
    if needs_patch:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        _rotate_log_if_oversized(log_path)
        f = open(log_path, "a", encoding="utf-8", errors="replace")
        sys.stdout = f
        sys.stderr = f


def _redirect_streams_to_logfile() -> None:
    """Force stdout/stderr to the rotating logfile, replacing whatever they
    currently point to. Called AFTER `SIDECAR_PORT=<n>` is emitted so the
    parent (Tauri shell) gets that one line, then the pipe is closed.

    Why: when the parent captures stdout via a pipe and falls behind on
    draining (e.g. while logging to disk), the pipe buffer fills and the
    sidecar's next print() blocks forever. uvicorn's per-request logger
    calls print(), so blocked stdout = blocked request handling. We've
    already given the parent the only line it needs.
    """
    log_path = _log_path()
    log_path.parent.mkdir(parents=True, exist_ok=True)
    _rotate_log_if_oversized(log_path)
    f = open(log_path, "a", encoding="utf-8", errors="replace", buffering=1)
    try:
        sys.stdout.flush()
        sys.stderr.flush()
    except OSError:
        pass
    # Replace at OS fd level so even C-level writes follow.
    try:
        os.dup2(f.fileno(), 1)
        os.dup2(f.fileno(), 2)
    except OSError:
        pass
    sys.stdout = f
    sys.stderr = f


_patch_streams_if_needed()

import hmac

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

# Shared secret the Tauri shell injects via the MERCWIZARD_TOKEN env var.
# Without it the API refuses to serve. The sidecar executable also ships
# inside the install directory, so a copy started outside the shell (by
# hand, or by a restart path that loses the environment) must not come up
# unauthenticated with write access to the user's game data. Dev runs and
# pytest opt out deliberately with MERCWIZARD_ALLOW_NO_AUTH=1.
_TOKEN = os.environ.get("MERCWIZARD_TOKEN") or ""
_ALLOW_NO_AUTH = os.environ.get("MERCWIZARD_ALLOW_NO_AUTH") == "1"

# A SECOND per-launch secret, used only in the `?_t=` query param that
# <img>/<audio> element loads need (a browser attaches no request header
# to those). Because it travels in the URL it reaches places a header
# never does: the WebView's on-disk cache index, a dev access log. So it
# authorizes GET and nothing else, and a leaked copy can re-read a
# portrait but never write to the install. Both secrets die with the
# process, so neither is worth anything after the app closes.
_MEDIA_TOKEN = secrets.token_hex(32) if _TOKEN else ""

# This is deliberately separate from the HTTP API token.  The Rust shell
# binds a one-shot loopback listener before it starts the PyInstaller bundle;
# the long-lived Python runtime proves this secret, then exits itself if the
# shell's end of the socket goes away.  That covers hard shell termination,
# where the PyInstaller bootloader can otherwise leave its runtime child alive.
_LIFELINE_PORT_ENV = "MERCWIZARD_LIFELINE_PORT"
_LIFELINE_TOKEN_ENV = "MERCWIZARD_LIFELINE_TOKEN"
_LIFELINE_ACK = b"MWL1"
_LIFELINE_TOKEN_BYTES = 64
_LIFELINE_TIMEOUT_SECS = 5.0


def _recv_exact(connection: socket.socket, size: int) -> bytes:
    """Read a fixed-size lifeline frame or reject a truncated peer."""
    chunks: list[bytes] = []
    remaining = size
    while remaining:
        chunk = connection.recv(remaining)
        if not chunk:
            raise RuntimeError("lifeline closed during handshake")
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


def connect_parent_lifeline(port: int, token: str) -> socket.socket:
    """Prove the shell-issued token and return a connected parent lifeline."""
    if not (0 < port < 65536):
        raise RuntimeError("lifeline port is invalid")
    encoded_token = token.encode("ascii")
    if len(encoded_token) != _LIFELINE_TOKEN_BYTES:
        raise RuntimeError("lifeline token is invalid")

    connection = socket.create_connection(
        ("127.0.0.1", port), timeout=_LIFELINE_TIMEOUT_SECS
    )
    connection.settimeout(_LIFELINE_TIMEOUT_SECS)
    try:
        connection.sendall(encoded_token)
        if _recv_exact(connection, len(_LIFELINE_ACK)) != _LIFELINE_ACK:
            raise RuntimeError("lifeline handshake was rejected")
        connection.settimeout(None)
        return connection
    except Exception:
        connection.close()
        raise


def watch_parent_lifeline(
    connection: socket.socket,
    exit_process: Callable[[int], None] = os._exit,
) -> None:
    """Terminate this runtime promptly once the shell loses its socket."""
    try:
        while connection.recv(1):
            pass
    except OSError:
        # A reset is equivalent to EOF here: the shell can no longer own us.
        pass
    finally:
        connection.close()
    exit_process(0)


def start_parent_lifeline() -> None:
    """Connect the packaged runtime to its shell, or no-op for dev/pytest."""
    port_text = os.environ.get(_LIFELINE_PORT_ENV)
    token = os.environ.get(_LIFELINE_TOKEN_ENV)
    if port_text is None and token is None:
        return
    if not port_text or not token:
        raise RuntimeError("lifeline configuration is incomplete")
    try:
        port = int(port_text)
    except ValueError as error:
        raise RuntimeError("lifeline port is invalid") from error
    connection = connect_parent_lifeline(port, token)
    threading.Thread(
        target=watch_parent_lifeline,
        args=(connection,),
        name="mercwizard-parent-lifeline",
        daemon=True,
    ).start()


class TokenAuthMiddleware(BaseHTTPMiddleware):
    """Require X-MercWizard-Token: <token> on every request when configured.

    Defends the loopback HTTP API against drive-by webpages that scan
    localhost ports — without the token they get 401 on every endpoint.
    Constant-time compare against timing-oracle attacks (overkill given the
    threat, but free).
    """

    async def dispatch(self, request: Request, call_next):
        if not _TOKEN:
            if _ALLOW_NO_AUTH:
                return await call_next(request)
            return JSONResponse(
                status_code=503,
                content={"detail": {
                    "error": "AUTH_NOT_CONFIGURED",
                    "message": "Sidecar started without MERCWIZARD_TOKEN; "
                               "refusing to serve an unauthenticated API",
                }},
            )
        # CORS preflight requests carry no token (browser never sends custom
        # headers on OPTIONS) — let CORSMiddleware handle them downstream.
        if request.method == "OPTIONS":
            return await call_next(request)
        # The full-access session token travels on a header only, where no
        # URL log or cache index can capture it.
        header = request.headers.get("x-mercwizard-token", "")
        if header and hmac.compare_digest(header, _TOKEN):
            return await call_next(request)
        # <img src=>, <audio src=> and <video src=> cannot carry a header, so
        # those loads pass the GET-only media token in `_t` instead. A
        # user-reported bug: roster portraits, the edit-tab BigFace and voice
        # clips all 401'd silently before this path existed.
        query = request.query_params.get("_t", "")
        if (request.method == "GET" and query
                and hmac.compare_digest(query, _MEDIA_TOKEN)):
            return await call_next(request)
        return JSONResponse(
            status_code=401,
            content={"detail": {"error": "UNAUTHORIZED", "message": "Bad or missing token"}},
        )

# Make sure vendored ja2py is importable when running directly
_HERE = Path(__file__).parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

from routes import (
    app_settings,
    backgrounds,
    backgrounds_library,
    backup,
    bundle,
    facegear,
    game,
    gear,
    graphics,
    health,
    ini_editor,
    ini_presets,
    installs,
    items,
    mapforge,
    mapforge_library,
    mapforge_placement,
    merc,
    portrait,
    roster,
    rpc_dialogue,
    rpc_placement,
    saves,
    setup,
    slot_picker,
    slots,
    tools,
    traits,
    voice,
    voice_lab,
)
from mercwizard_core.voice_lab.service import recover_registered_installs
from routes.state import get_state


def _resolve_bind_addresses(host: str, port: int) -> list[tuple]:
    """Resolve every candidate socket address before security validation."""
    return socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)


def validate_bind_security(
    host: str,
    port: int = 0,
    addresses: list[tuple] | None = None,
) -> None:
    """Reject remote binding unless the shell configured token auth.

    The sidecar normally listens only on loopback.  A developer may opt in to
    another interface, but only when the per-launch bearer token protects the
    HTTP API.
    """
    candidates = addresses if addresses is not None else _resolve_bind_addresses(host, port)
    if os.environ.get("MERCWIZARD_TOKEN"):
        return
    if not candidates:
        raise SystemExit("Refusing to bind host with no resolved addresses")
    for _, _, _, _, sockaddr in candidates:
        try:
            is_loopback = ipaddress.ip_address(sockaddr[0]).is_loopback
        except ValueError:
            is_loopback = False
        if not is_loopback:
            raise SystemExit(
                "Refusing to bind a non-loopback host without MERCWIZARD_TOKEN"
            )


def bind_server_socket(host: str, port: int) -> tuple[socket.socket, int]:
    """Bind and listen before announcing the port to the Tauri shell."""
    addresses = _resolve_bind_addresses(host, port)
    validate_bind_security(host, port, addresses)
    last_error: OSError | None = None
    for family, socktype, proto, _, address in addresses:
        sock = socket.socket(family, socktype, proto)
        try:
            if sys.platform == "win32":
                # SO_REUSEADDR lets another Windows listener claim this port.
                # The shell publishes this endpoint as a private capability, so
                # claim it exclusively before binding.
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
            else:
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            sock.bind(address)
            sock.listen(socket.SOMAXCONN)
            return sock, int(sock.getsockname()[1])
        except OSError as error:
            last_error = error
            sock.close()
    if last_error is not None:
        raise last_error
    raise OSError(f"No address available for {host}:{port}")


def create_app() -> FastAPI:
    app = FastAPI(
        title="MercWizard Sidecar",
        version="1.0.0-beta.4",
        description="HTTP backend for the MercWizard 2 desktop tool",
    )

    @app.exception_handler(Exception)
    async def _log_uncaught_exception(request: Request, exc: Exception) -> JSONResponse:
        # FastAPI/Starlette dispatch HTTPException and RequestValidationError
        # through their own handlers before reaching this catch-all; we only
        # see truly unexpected errors here. Print the traceback to the
        # patched stderr (i.e. sidecar.log) so post-mortem debugging is
        # possible — uvicorn's default 500 path doesn't always surface it.
        import traceback
        tb = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
        print(
            f"[uncaught] {request.method} {request.url.path}\n{tb}",
            file=sys.stderr,
            flush=True,
        )
        return JSONResponse(
            status_code=500,
            content={
                "detail": {
                    "error": "INTERNAL_ERROR",
                    "message": str(exc) or type(exc).__name__,
                }
            },
        )

    # Per bug #12, the sidecar no longer kicks off a background install
    # scan at startup. Detection is purely user-driven through the
    # FirstRun VFS Selector Wizard, so the watchdog is no longer at risk
    # from a slow VFS-parsing crawl and there's nothing to schedule here.
    # Token middleware runs first (Starlette executes middlewares in reverse
    # add order, so CORS preflight responses get the auth check applied).
    # CORS is restricted to the Tauri webview origins; the shared-secret
    # token is the real gate, this is defense in depth against drive-by
    # browser hits on localhost.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[
            "tauri://localhost",
            "http://tauri.localhost",
            "https://tauri.localhost",
            "http://localhost:1420",  # vite dev server
            "http://127.0.0.1:1420",  # loopback-only Vite dev server
        ],
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*", "X-MercWizard-Token"],
    )
    app.add_middleware(TokenAuthMiddleware)

    @app.on_event("startup")
    def recover_voice_lab_transactions() -> None:
        """Recover registered Voice Lab journals before mutation routes run."""
        recover_registered_installs(get_state())

    api_prefix = "/api/v1"

    @app.get(f"{api_prefix}/auth/media-token")
    def issue_media_token() -> dict:
        """Hand the UI the GET-only secret used in <img>/<audio> URLs.

        Reaching this route already required the full session token on the
        request header, so it discloses nothing the caller did not hold.
        """
        return {"token": _MEDIA_TOKEN}
    app.include_router(health.router, prefix=api_prefix, tags=["health"])
    app.include_router(installs.router, prefix=api_prefix, tags=["installs"])
    app.include_router(roster.router, prefix=api_prefix, tags=["roster"])
    app.include_router(merc.router, prefix=api_prefix, tags=["merc"])
    app.include_router(portrait.router, prefix=api_prefix, tags=["portrait"])
    app.include_router(facegear.router, prefix=api_prefix, tags=["facegear"])
    app.include_router(gear.router, prefix=api_prefix, tags=["gear"])
    app.include_router(bundle.router, prefix=api_prefix, tags=["bundle"])
    app.include_router(backup.router, prefix=api_prefix, tags=["backup"])
    app.include_router(ini_editor.router, prefix=api_prefix, tags=["ini"])
    app.include_router(app_settings.router, prefix=api_prefix, tags=["settings"])
    app.include_router(graphics.router, prefix=api_prefix, tags=["graphics"])
    app.include_router(ini_presets.router, prefix=api_prefix, tags=["ini"])
    app.include_router(setup.router, prefix=api_prefix, tags=["setup"])
    app.include_router(game.router, prefix=api_prefix, tags=["game"])
    app.include_router(saves.router, prefix=api_prefix, tags=["saves"])
    app.include_router(slots.router, prefix=api_prefix, tags=["slots"])
    app.include_router(slot_picker.router, prefix=api_prefix, tags=["slots"])
    app.include_router(traits.router, prefix=api_prefix, tags=["traits"])
    app.include_router(backgrounds.router, prefix=api_prefix, tags=["backgrounds"])
    app.include_router(backgrounds_library.router, prefix=api_prefix, tags=["backgrounds"])
    app.include_router(items.router, prefix=api_prefix, tags=["items"])
    app.include_router(voice.router, prefix=api_prefix, tags=["voice"])
    app.include_router(voice_lab.router, prefix=api_prefix, tags=["voice-lab"])
    app.include_router(mapforge.router, prefix=api_prefix, tags=["mapforge"])
    app.include_router(mapforge_library.router, prefix=api_prefix, tags=["mapforge"])
    app.include_router(mapforge_placement.router, prefix=api_prefix, tags=["mapforge"])
    app.include_router(tools.router, prefix=api_prefix, tags=["tools"])
    app.include_router(rpc_placement.router, prefix=api_prefix, tags=["rpc"])
    app.include_router(rpc_dialogue.router, prefix=api_prefix, tags=["rpc"])

    return app


# Module-level app for uvicorn reload / pytest TestClient
app = create_app()


def main() -> None:
    import uvicorn

    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--port", type=int, default=0,
        help="Port to bind. 0 (default) = OS picks a free port.",
    )
    parser.add_argument("--host", type=str, default="127.0.0.1")
    parser.add_argument("--log-level", type=str, default="info")
    args = parser.parse_args()

    # Refuse to come up as an open API. The shell always sets the token, so
    # reaching this branch means the executable was started some other way:
    # by hand out of the install directory, or by a restart path that lost
    # the environment. Serving would expose write access to the game data
    # with no gate at all.
    if not _TOKEN and not _ALLOW_NO_AUTH:
        raise SystemExit(
            "Refusing to start without MERCWIZARD_TOKEN. The Merc Forge "
            "shell supplies it when it launches this process. To run the "
            "sidecar unauthenticated on purpose, set "
            "MERCWIZARD_ALLOW_NO_AUTH=1."
        )

    # The watcher runs in this long-lived Python runtime (not the transient
    # PyInstaller bootloader), so EOF from a hard-killed shell exits the real
    # HTTP backend before it can become an orphan.
    start_parent_lifeline()
    sock, port = bind_server_socket(args.host, args.port)
    # Tauri shell parses this line from stdout to discover the bound port.
    # Print only after the listening socket is owned, then redirect so the
    # parent's pipe receives the one discovery marker without becoming a
    # long-lived logging backpressure risk.
    print(f"SIDECAR_PORT={port}", flush=True)

    # Redirect stdout/stderr to a logfile. Otherwise a Tauri-style pipe
    # parent that drains slowly can fill the pipe buffer and block our
    # next print() — which uvicorn calls per-request, freezing the
    # server. We've already emitted the one line the parent needs.
    _redirect_streams_to_logfile()

    logging.basicConfig(level=args.log_level.upper())
    # access_log=False: uvicorn's access logger writes the full request URL —
    # including the ?_t=<token> media param used for <img>/<audio> element loads
    # — to sidecar.log on every request. That token is GET-only and dies
    # with the process, but users routinely attach sidecar.log to bug reports, so keep it
    # out of the log entirely. Errors still log with full tracebacks via the
    # global exception handler, so triage isn't affected. (Also removes the
    # per-asset-request log spam — a roster paint can fire 256 requests.)
    config = uvicorn.Config(
        app,
        host=args.host,
        port=port,
        log_level=args.log_level,
        access_log=False,
    )
    server = uvicorn.Server(config)
    try:
        server.run(sockets=[sock])
    finally:
        sock.close()


if __name__ == "__main__":
    main()
