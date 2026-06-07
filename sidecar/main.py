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
import logging
import os
import socket
import sys
from pathlib import Path


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
# If absent (running the sidecar standalone for dev/pytest), auth is skipped
# so existing pytest/TestClient flows keep working.
_TOKEN = os.environ.get("MERCWIZARD_TOKEN") or ""


class TokenAuthMiddleware(BaseHTTPMiddleware):
    """Require X-MercWizard-Token: <token> on every request when configured.

    Defends the loopback HTTP API against drive-by webpages that scan
    localhost ports — without the token they get 401 on every endpoint.
    Constant-time compare against timing-oracle attacks (overkill given the
    threat, but free).
    """

    async def dispatch(self, request: Request, call_next):
        if not _TOKEN:
            return await call_next(request)
        # CORS preflight requests carry no token (browser never sends custom
        # headers on OPTIONS) — let CORSMiddleware handle them downstream.
        if request.method == "OPTIONS":
            return await call_next(request)
        # Accept the token via either the X-MercWizard-Token header
        # (preferred for XHR + fetch — header is invisible in logs) or
        # the `_t` query param (required for <img src=>, <audio src=>,
        # <video src=>, and other browser-driven media loads where the
        # browser CAN'T attach custom headers). A user-reported bug: roster
        # portraits + edit-tab BigFace + voice clips all hit this case
        # and 401'd silently. The query param is acceptable here because
        # the sidecar only listens on 127.0.0.1 and the token is a
        # per-launch random — same exposure as a session cookie.
        supplied = (
            request.headers.get("x-mercwizard-token", "")
            or request.query_params.get("_t", "")
        )
        if not hmac.compare_digest(supplied, _TOKEN):
            return JSONResponse(
                status_code=401,
                content={"detail": {"error": "UNAUTHORIZED", "message": "Bad or missing token"}},
            )
        return await call_next(request)

# Make sure vendored ja2py is importable when running directly
_HERE = Path(__file__).parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

from routes import (
    app_settings,
    backgrounds,
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
    mapforge,
    mapforge_library,
    merc,
    portrait,
    roster,
    saves,
    setup,
    slot_picker,
    slots,
    tools,
    traits,
    voice,
)


def _pick_port() -> int:
    """Ask the OS for a free port and return it.

    There is a microsecond-scale race between closing this probe socket
    and uvicorn binding the same port. For a single-user desktop talking
    only to 127.0.0.1 the collision risk is effectively zero. Passing a
    pre-bound socket to `uvicorn.Server.serve(sockets=...)` would close
    the race but is broken on Windows (uvicorn accepts one request and
    then stops accepting), so we accept the race.
    """
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def create_app() -> FastAPI:
    app = FastAPI(
        title="MercWizard Sidecar",
        version="2.0.0",
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
        ],
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*", "X-MercWizard-Token"],
    )
    app.add_middleware(TokenAuthMiddleware)

    api_prefix = "/api/v1"
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
    app.include_router(voice.router, prefix=api_prefix, tags=["voice"])
    app.include_router(mapforge.router, prefix=api_prefix, tags=["mapforge"])
    app.include_router(mapforge_library.router, prefix=api_prefix, tags=["mapforge"])
    app.include_router(tools.router, prefix=api_prefix, tags=["tools"])

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

    port = args.port if args.port else _pick_port()
    # Tauri shell parses this line from stdout to discover the bound port.
    # Print before redirecting so the parent's pipe receives it.
    print(f"SIDECAR_PORT={port}", flush=True)

    # Redirect stdout/stderr to a logfile. Otherwise a Tauri-style pipe
    # parent that drains slowly can fill the pipe buffer and block our
    # next print() — which uvicorn calls per-request, freezing the
    # server. We've already emitted the one line the parent needs.
    _redirect_streams_to_logfile()

    logging.basicConfig(level=args.log_level.upper())
    # access_log=False: uvicorn's access logger writes the full request URL —
    # including the ?_t=<token> auth param used for <img>/<audio> element loads
    # — to sidecar.log on every request. That token is a per-launch loopback
    # secret, and users routinely attach sidecar.log to bug reports, so keep it
    # out of the log entirely. Errors still log with full tracebacks via the
    # global exception handler, so triage isn't affected. (Also removes the
    # per-asset-request log spam — a roster paint can fire 256 requests.)
    uvicorn.run(app, host=args.host, port=port, log_level=args.log_level,
                access_log=False)


if __name__ == "__main__":
    main()
