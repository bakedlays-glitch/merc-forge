"""MapForge — JA2 .dat sector editor (Phase 0: read-only inspector).

Wraps the vendored iso renderer + .dat parser
(`mercwizard_core.mapforge_engine`) as a FastAPI router so the React
frontend can render sectors and inspect tiles through the MercForge sidecar.

Phase 0 endpoints (all read-only — no .dat writes):

  GET  /mapforge/health             — confirms the renderer imports
  GET  /mapforge/installs/maps      — lists .dat files in the active install
  GET  /mapforge/sector/info        — dimensions + room list for one .dat
  GET  /mapforge/sector/render      — PNG of sector (full / room / bbox)
  GET  /mapforge/sector/tile        — JSON describing one tile

Future phases (NOT in this file yet):
  - POST /mapforge/sessions          — open a .dat for editing, returns session_id
  - PUT  /mapforge/sessions/{sid}/tile/{x}/{y}/struct  — edit op
  - POST /mapforge/sessions/{sid}/save  — write .dat back

The renderer + parser are vendored inside the sidecar package
(`mercwizard_core/mapforge_engine/`) so they bundle cleanly under
PyInstaller and run against ANY user install — tileset asset roots are
derived from the active install at request time, not hardcoded.
"""
from __future__ import annotations

import hashlib
import io
import os
import tempfile
from pathlib import Path
import json
from typing import Any, Callable, Optional

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import Response, StreamingResponse
from pydantic import BaseModel

from .state import get_state


# ─── PyInstaller hint imports ─────────────────────────────────────────
# The vendored iso renderer + ja2py SlfFS load these at module load.
# PyInstaller's static analysis can miss a few of these transitive deps
# (fs.* submodules are imported dynamically by ja2py; pkg_resources is a
# transitive dep of fs), so without these explicit re-imports the bundle
# could ship without PIL.ImageDraw / PIL.ImageFont / fs and
# /mapforge/health would report the renderer as unavailable. Importing
# them here makes PyInstaller's analysis pick them up and bundle them.
# Do NOT remove even though they look unused — they're hidden-import
# beacons for PyInstaller, not application code.
import PIL.Image        # noqa: F401  - used by iso_renderer
import PIL.ImageDraw    # noqa: F401  - used by iso_renderer.add_title
import PIL.ImageFont    # noqa: F401  - used by iso_renderer.add_title
# fs.* submodules used by ja2py/fileformats/SlfFS.py:26-30. PyFilesystem
# loads these via direct imports, but PyInstaller can't see SlfFS.py
# because it lives outside the bundle. Hint each one explicitly.
import fs               # noqa: F401
import fs.base          # noqa: F401  - FS base class
import fs.errors        # noqa: F401  - CreateFailed, FileExpected, etc.
import fs.memoryfs      # noqa: F401  - MemoryFS
import fs.multifs       # noqa: F401  - MultiFS
import fs.info          # noqa: F401  - Info
import fs.osfs          # noqa: F401  - opening loose files
import pkg_resources    # noqa: F401  - transitive dep of fs (setuptools<81)


# ─── Vendored renderer + parser imports ────────────────────────────────
# The iso renderer, .dat parser, writer, and edit ops are vendored into
# the sidecar package (mercwizard_core/mapforge_engine/) so they bundle
# under PyInstaller and carry NO hardcoded install paths — asset roots
# are derived from the active install per-request (see
# `_install_tileset_paths`). No sys.path manipulation is needed.
_iso_renderer_available = False
_iso_renderer_import_error: Optional[str] = None
try:
    from mercwizard_core.mapforge_engine.iso_renderer import (  # noqa: E402
        IsoRenderer, add_title, load_tileset_xml, StiCache,
    )
    from mercwizard_core.mapforge_engine.parse_dat_ext import (  # noqa: E402
        parse_dat_file, parse_dat_full,
    )
    from mercwizard_core.mapforge_engine.dat_writer import (  # noqa: E402
        write_dat_bytes, build_empty_dat_bytes,
    )
    from mercwizard_core.mapforge_engine.dat_edit_ops import (  # noqa: E402
        replace_layer_entry, add_layer_entry, remove_layer_entry,
        place_layer_entry, set_layer_entries, set_room_id, set_height,
        EditOpError,
    )
    # SlfFS is what we use to enumerate + extract from .slf archives.
    # Pulled from the vendored ja2py inside the sidecar bundle.
    from ja2py.fileformats.SlfFS import SlfFS  # noqa: E402
    _iso_renderer_available = True
except Exception as e:  # noqa: BLE001
    _iso_renderer_import_error = f"{type(e).__name__}: {e}"

# validate.py is pure (stdlib only) — import it OUTSIDE the renderer try
# so the pre-flight validator's structure checks work even if the heavy
# renderer deps (PIL / fs / ja2py) above failed to import.
from mercwizard_core.mapforge_engine.validate import (  # noqa: E402
    validate_parsed, Finding,
)
from mercwizard_core.mapforge_engine.appendix_extract import extract_appendix_entities  # noqa: E402
from mercwizard_core.vfs import parse_vfs_config, VfsConfigError  # noqa: E402
# tile_families is pure (a static enum table) — import alongside validate.
from mercwizard_core.mapforge.tile_families import slot_family, MAX_TILE_SLOT  # noqa: E402


# ─── Install-relative tileset asset resolution ─────────────────────────
# The vendored renderer ships NO hardcoded install paths — the caller
# derives tileset asset roots from the ACTIVE install and passes them in.
# JA2 1.13 stores tile graphics two ways, mirrored across VFS layers:
#   - loose:  <install>/<layer>/Tilesets/<tileset-index>/<file>.sti
#   - packed: <install>/<layer>/Tilesets.slf   (one archive per layer)
# We search every content layer the install exposes, in VFS priority
# order, so a mod's overriding tileset (Data-1.13) shadows the vanilla
# base (Data) — same ordering the engine's VFS uses.

# Content-layer subdirs, highest VFS priority first. Matches the
# `layer_candidates` tuple used throughout the install-scan endpoints.
_TILESET_LAYERS = ("Data-1.13", "Data-DMK", "Data")


def _install_tileset_paths(install_root: Path) -> tuple[list[Path], list[Path]]:
    """Return (loose_dirs, slf_paths) for the install's tilesets across
    every content layer it exposes, in VFS priority order.

    loose_dirs:  each `<install>/<layer>/Tilesets` directory that exists
                 (StiCache appends `/<tileset-index>/<file>` and `/0/<file>`).
    slf_paths:   each `<install>/<layer>/Tilesets.slf` archive that exists
                 (case-insensitive match — some installs ship `TILESETS.SLF`).

    Only existing paths are returned, so StiCache never wastes a stat on a
    layer the install doesn't have. Returns ([], []) for an install with no
    Tilesets anywhere — the renderer still runs, just finds no tile art."""
    loose: list[Path] = []
    slf: list[Path] = []
    for layer in _TILESET_LAYERS:
        layer_root = install_root / layer
        if not layer_root.is_dir():
            continue
        ts_dir = layer_root / "Tilesets"
        if ts_dir.is_dir():
            loose.append(ts_dir)
        # Tilesets.slf — match case-insensitively (NTFS is case-insensitive
        # but a bundle copied from a case-sensitive FS may differ).
        for entry in layer_root.iterdir() if layer_root.is_dir() else []:
            if entry.is_file() and entry.name.lower() == "tilesets.slf":
                slf.append(entry)
    return loose, slf


def _active_install_root() -> Optional[Path]:
    """Active install root from app state, or None when none is active."""
    info = get_state().active()
    return Path(info.path) if info is not None else None


def _tileset_paths_for(xml_path: Path) -> tuple[list[Path], list[Path]]:
    """Resolve tileset asset roots for a request whose Ja2Set.dat.xml is
    `xml_path`. Prefers the active install (the canonical source). Falls
    back to deriving the install root from the xml path itself — the
    engine stores `Ja2Set.dat.xml` at `<install>/<layer>/Ja2Set.dat.xml`,
    so its grandparent is the install root — so renders work even if no
    install is marked active in state (e.g. a direct API call)."""
    root = _active_install_root()
    if root is not None:
        loose, slf = _install_tileset_paths(root)
        if loose or slf:
            return loose, slf
    # Fallback: <install>/<layer>/Ja2Set.dat.xml → grandparent == install.
    try:
        derived = Path(xml_path).resolve().parent.parent
        if derived.is_dir():
            return _install_tileset_paths(derived)
    except OSError:
        pass
    return [], []


# ─── SLF helpers ───────────────────────────────────────────────────────
# Map sectors are often shipped inside `Data/Maps.slf` (vanilla packaging)
# rather than as loose `Data/Maps/*.dat` files. MapForge needs to surface
# those, but iso_renderer takes a real filesystem path — so we extract
# SLF-bundled .dats to a temp cache and pass the cached path through.
#
# Cache layout: %TEMP%/mapforge_slf_cache/<slfhash>/<INTERNAL_NAME>
# Cache invalidation: <slfhash> = sha1 of (slf_abs_path + slf_mtime), so
# any rebuild of the SLF (mtime bump) creates a fresh cache dir.

_SLF_CACHE_ROOT = Path(tempfile.gettempdir()) / "mapforge_slf_cache"

# Persisted scan results live under %APPDATA% so they survive sidecar
# respawns. Two caches per install:
#   <id>.json     — combined (loose + SLF). Hit when nothing has changed.
#   <id>_slf.json — SLF-only. Hit when loose files changed but SLFs
#                   haven't (the common case — editing a single map).
# Splitting the cache means a single .dat write only invalidates the
# fast (loose-enumeration) path, not the slow (SLF directory-walk) one.
_INSTALLS_MAPS_CACHE = (
    Path(os.environ.get("APPDATA") or Path.home() / ".config")
    / "MercWizard" / "mapforge" / "installs_maps_cache"
)

SLF_URI_PREFIX = "slf://"


def _install_maps_cache_path(install_id: str) -> Path:
    safe = "".join(c if c.isalnum() or c in "_-." else "_" for c in install_id)
    return _INSTALLS_MAPS_CACHE / f"{safe}.json"


def _install_slf_cache_path(install_id: str) -> Path:
    """Path to the SLF-only cache for an install. Holds just the SLF
    entries + the SLF-only fingerprint, so edits to loose maps don't
    invalidate the slow SLF directory walk."""
    safe = "".join(c if c.isalnum() or c in "_-." else "_" for c in install_id)
    return _INSTALLS_MAPS_CACHE / f"{safe}_slf.json"


def _slf_fingerprint(install_root: Path, layer_candidates: tuple[str, ...]) -> str:
    """Fingerprint covering ONLY the SLF archives in the install. Editing
    a single loose .dat does NOT change this fingerprint, so the
    (expensive) SLF directory walk stays cached across map edits.

    Hashes: install path + per-SLF (relative path, mtime_ns, size)."""
    h = hashlib.sha1()
    h.update(str(install_root.resolve()).encode("utf-8", "replace"))
    for layer in layer_candidates:
        layer_root = install_root / layer
        if not layer_root.is_dir():
            continue
        # Only Maps.slf affects the install scan's SLF results — other
        # SLFs (speech.slf, ambient.slf, anims.slf, etc.) don't carry
        # `.dat` sectors. Including them in the fingerprint would
        # invalidate the SLF cache when an unrelated SLF's mtime
        # changes (e.g., a voice mod install).
        slfs = sorted(layer_root.glob("*.slf")) + sorted(layer_root.glob("*.SLF"))
        for slf_path in slfs:
            if slf_path.name.lower() != "maps.slf":
                continue
            try:
                st = slf_path.stat()
                h.update(f"|{layer}/{slf_path.name}:{st.st_mtime_ns}:{st.st_size}".encode())
            except OSError:
                pass
    return h.hexdigest()


def _loose_fingerprint(install_root: Path, layer_candidates: tuple[str, ...]) -> str:
    """Fingerprint covering loose Maps/<sector>.dat files. Cheap to
    recompute (one stat per dat); used to invalidate the combined-list
    cache when a loose map is edited or a new .dat appears."""
    h = hashlib.sha1()
    h.update(str(install_root.resolve()).encode("utf-8", "replace"))
    for layer in layer_candidates:
        layer_root = install_root / layer
        maps_dir = layer_root / "Maps"
        if not maps_dir.is_dir():
            continue
        try:
            h.update(f"|{layer}/Maps:{maps_dir.stat().st_mtime_ns}".encode())
        except OSError:
            pass
        for p in sorted(maps_dir.iterdir()):
            if p.suffix.lower() == ".dat":
                try:
                    st = p.stat()
                    h.update(f"|{p.name}:{st.st_mtime_ns}:{st.st_size}".encode())
                except OSError:
                    pass
    return h.hexdigest()


def _cache_fingerprint(install_root: Path, layer_candidates: tuple[str, ...]) -> str:
    """Combined fingerprint: changes if EITHER the SLF or loose layer
    has changed. Used as a cache key for the unified /installs/maps
    response so we get an O(1) early-return when nothing's changed."""
    return (_slf_fingerprint(install_root, layer_candidates)
            + ":" + _loose_fingerprint(install_root, layer_candidates))


def _slf_cache_dir(slf_path: Path) -> Path:
    try:
        mtime_ns = slf_path.stat().st_mtime_ns
    except OSError:
        mtime_ns = 0
    key = f"{slf_path.resolve()}|{mtime_ns}".encode("utf-8", "replace")
    digest = hashlib.sha1(key).hexdigest()[:16]
    return _SLF_CACHE_ROOT / digest


def _list_slf_maps(slf_path: Path) -> list[tuple[str, int]]:
    """Enumerate (internal_path, size) entries inside an SLF that look
    like .dat sector files. Two acceptance modes:
      - If the SLF basename is `Maps.slf` (case-insensitive), accept ALL
        .dat entries — vanilla packaging stores sectors at the SLF root
        (e.g. `/A1.DAT`), NOT under a `MAPS/` subdir.
      - Otherwise (e.g. a custom-packed bundle), require the file to sit
        under a `Maps/` path component, so we don't sweep up incidental
        .dat files from unrelated SLFs.
    Returns [] if the SLF can't be opened.
    """
    out: list[tuple[str, int]] = []
    try:
        fs = SlfFS(str(slf_path))
    except Exception:
        return out
    is_maps_slf = slf_path.name.lower() == "maps.slf"
    try:
        for path in fs.walk.files():
            base = os.path.basename(path).lower()
            if not base.endswith(".dat"):
                continue
            if not is_maps_slf:
                parts_lower = path.lower().split("/")
                if "maps" not in parts_lower:
                    continue
            try:
                info = fs.getdetails(path)
                size = info.size
            except Exception:
                size = 0
            out.append((path, size))
    except Exception:
        pass
    return out


def _extract_dat_from_slf(slf_path: Path, internal_path: str) -> Path:
    """Extract `internal_path` from `slf_path` into the on-disk cache
    (if not already cached) and return the cached file path. Extraction
    is cheap (.dats are typically <100 KB) so we keep it simple and
    eager-extract on first access."""
    cache_dir = _slf_cache_dir(slf_path)
    cache_dir.mkdir(parents=True, exist_ok=True)
    out_name = os.path.basename(internal_path) or "map.dat"
    out_path = cache_dir / out_name
    if out_path.is_file() and out_path.stat().st_size > 0:
        return out_path
    fs = SlfFS(str(slf_path))
    data = fs.readbytes(internal_path)
    out_path.write_bytes(data)
    return out_path


def _build_slf_uri(slf_path: Path, internal_path: str) -> str:
    """Build a `slf://<abs>!<internal>` URI the API uses to refer to an
    SLF-bundled file without leaking the temp-cache path into the
    frontend."""
    abs_str = str(slf_path.resolve()).replace("\\", "/")
    return f"{SLF_URI_PREFIX}{abs_str}!{internal_path}"


def _parse_slf_uri(uri: str) -> tuple[Path, str]:
    """Inverse of `_build_slf_uri`. Returns (slf_path, internal_path)."""
    rest = uri[len(SLF_URI_PREFIX):]
    if "!" not in rest:
        raise ValueError(f"Bad SLF URI (missing '!'): {uri}")
    slf_str, internal = rest.split("!", 1)
    return Path(slf_str), internal


def _resolve_dat_path(raw: str) -> Path:
    """Accept either a real filesystem path or an `slf://...!...` URI.
    For SLF URIs, extract on demand to the temp cache and return the
    cached path. Raises HTTPException on bad input."""
    if raw.startswith(SLF_URI_PREFIX):
        try:
            slf_path, internal = _parse_slf_uri(raw)
        except ValueError as e:
            raise HTTPException(
                status_code=400,
                detail={"error": "BAD_SLF_URI", "message": str(e)},
            )
        if not slf_path.is_file():
            raise HTTPException(
                status_code=404,
                detail={"error": "SLF_NOT_FOUND",
                        "message": f"{slf_path} not found"},
            )
        try:
            return _extract_dat_from_slf(slf_path, internal)
        except Exception as e:  # noqa: BLE001
            raise HTTPException(
                status_code=500,
                detail={"error": "SLF_EXTRACT_FAILED",
                        "message": f"{type(e).__name__}: {e}"},
            )
    return _validate_dat_path(raw)


router = APIRouter(prefix="/mapforge")


# ─── Models ────────────────────────────────────────────────────────────
class MapForgeHealth(BaseModel):
    renderer_available: bool
    renderer_import_error: Optional[str] = None
    # Where the renderer code lives. Now that the renderer is vendored
    # inside the sidecar package, this is the vendored module's directory
    # (a diagnostic shown when the renderer fails to import) — NOT an
    # external dev-machine path. Field name kept for frontend compat.
    headless_compiler_path: str
    active_install_id: Optional[str] = None


class SectorMapFile(BaseModel):
    name: str           # filename, e.g. "A9.DAT"
    path: str           # absolute path, or slf:// URI when bundled
    rel_path: str       # path relative to install root (best-effort for SLF entries)
    size_bytes: int
    source: str         # "loose" or "slf"
    slf_archive: Optional[str] = None   # absolute SLF path if source=="slf"


class InstallMaps(BaseModel):
    install_id: str
    install_path: str
    data_layers: list[str]  # list of Data dirs scanned (Data-1.13, Data-DMK, Data)
    maps: list[SectorMapFile]
    ja2set_xml: Optional[str] = None
    cached: bool = False           # True if served from on-disk cache
    cache_fingerprint: str = ""    # hash of all SLF + loose-dir mtimes
    scanned_at: float = 0.0        # unix epoch seconds


class RoomSummary(BaseModel):
    room_id: int
    tile_count: int
    bbox: tuple[int, int, int, int]  # x0, y0, x1, y1


class SectorInfo(BaseModel):
    dat_path: str
    rows: int
    cols: int
    tileset_in_header: int
    rooms: list[RoomSummary]
    layer_totals: dict[str, int]  # land, objs, structs, shadows, roofs, onroofs


class LayerEntry(BaseModel):
    slot: int
    sub: int
    sti_filename: Optional[str] = None
    sti_frame_index_0based: int  # = sub - 1 (engine convention)


class TileInspection(BaseModel):
    x: int
    y: int
    gridno: int
    room_id: int
    height: int
    world_flags: int
    layers: dict[str, list[LayerEntry]]


# ─── Tileset enumerator (Tileset Editor screen 1) ────────────────────
# Reads the active install's Ja2Set.dat.xml and surfaces the list of
# tileset blocks. Used by the new /tileset-editor route to render the
# picker grid. Lightweight wrapper around ElementTree — same parsing
# style as the existing _names_in_tileset helper in mapforge_library.

class TilesetInfo(BaseModel):
    index: int
    name: Optional[str] = None
    slot_count: int      # number of <file> entries in this tileset's block
    inherits_from_0: bool  # true when index != 0 (engine inheritance)


class TilesetList(BaseModel):
    xml_path: str
    tilesets: list[TilesetInfo]


# ─── Endpoints ─────────────────────────────────────────────────────────
@router.get("/health", response_model=MapForgeHealth)
def health():
    """Phase 0 wiring check: confirms the renderer + parser are importable."""
    state = get_state()
    active = state.active()
    return MapForgeHealth(
        renderer_available=_iso_renderer_available,
        renderer_import_error=_iso_renderer_import_error,
        headless_compiler_path=str(
            Path(__file__).resolve().parent.parent
            / "mercwizard_core" / "mapforge_engine"
        ),
        active_install_id=active.id if active else None,
    )


@router.get("/installs/maps", response_model=InstallMaps)
def list_active_install_maps(
    rescan: bool = Query(False,
        description="Force a fresh scan and overwrite the cache."),
):
    """List .dat sector files in the currently-active install.

    Scans the common content-layer subdirs in priority order:
      Data-1.13/Maps  (1.13 + mods)
      Data-DMK/Maps   (Redux DMK)
      Data/Maps       (vanilla)

    Results are cached to disk at
    `%APPDATA%/MercWizard/mapforge/installs_maps_cache/<install_id>.json`
    keyed by a fingerprint of all relevant file mtimes. The cache is
    served if the fingerprint matches the current install state; pass
    `?rescan=true` to skip the cache.
    """
    import json
    import time

    _require_renderer()
    state = get_state()
    info = state.active()
    if info is None:
        raise HTTPException(
            status_code=400,
            detail={"error": "NO_ACTIVE_INSTALL",
                    "message": "Activate an install in MercForge first."},
        )
    install_root = Path(info.path)
    layer_candidates = ("Data-1.13", "Data-DMK", "Data")

    # Fast path: combined cache matches → return immediately. Hits when
    # neither SLFs nor loose .dats changed since the last scan.
    current_fp = _cache_fingerprint(install_root, layer_candidates)
    cache_path = _install_maps_cache_path(info.id)
    if not rescan and cache_path.is_file():
        try:
            cached = json.loads(cache_path.read_text(encoding="utf-8"))
            if cached.get("cache_fingerprint") == current_fp:
                cached["cached"] = True
                return InstallMaps(**cached)
        except (OSError, json.JSONDecodeError, ValueError):
            pass

    # Mid path: SLF cache matches → reuse cached SLF listing, only
    # re-walk the (cheap) loose Maps dirs. This is the common case
    # when the user is editing a single .dat (mtime bumped → combined
    # cache invalidates → SLF cache still valid).
    slf_fp = _slf_fingerprint(install_root, layer_candidates)
    cached_slf_maps: Optional[list[SectorMapFile]] = None
    slf_cache_path = _install_slf_cache_path(info.id)
    if not rescan and slf_cache_path.is_file():
        try:
            slf_cached = json.loads(slf_cache_path.read_text(encoding="utf-8"))
            if slf_cached.get("slf_fingerprint") == slf_fp:
                cached_slf_maps = [SectorMapFile(**m)
                                    for m in slf_cached.get("maps", [])]
        except (OSError, json.JSONDecodeError, ValueError):
            cached_slf_maps = None

    layers_scanned: list[str] = []
    loose_maps: list[SectorMapFile] = []
    fresh_slf_maps: list[SectorMapFile] = []
    ja2set_xml: Optional[Path] = None
    for layer in layer_candidates:
        layers_scanned.append(layer)
        layer_root = install_root / layer
        if not layer_root.is_dir():
            continue

        # 1) Loose .dat files in <layer>/Maps/ — ALWAYS re-enumerate.
        # iterdir + stat on a typical Maps dir takes <10 ms even with
        # 100+ sectors, so re-walking is cheaper than maintaining a
        # cache for it.
        maps_dir = layer_root / "Maps"
        if maps_dir.is_dir():
            candidates = (sorted(maps_dir.glob("*.dat"))
                          + sorted(maps_dir.glob("*.DAT")))
            for p in candidates:
                try:
                    size = p.stat().st_size
                except OSError:
                    continue
                try:
                    rel = p.relative_to(install_root)
                except ValueError:
                    rel = p
                loose_maps.append(SectorMapFile(
                    name=p.name, path=str(p),
                    rel_path=str(rel), size_bytes=size,
                    source="loose", slf_archive=None,
                ))

        # 2) SLF-bundled .dat files — SKIP if SLF cache hit (reused
        # below). Otherwise walk Maps.slf only (other SLFs don't carry
        # sectors; see stream_install_maps for the same filter).
        if cached_slf_maps is None:
            slfs_in_layer = (sorted(layer_root.glob("*.slf"))
                             + sorted(layer_root.glob("*.SLF")))
            for slf_path in slfs_in_layer:
                if slf_path.name.lower() != "maps.slf":
                    continue
                for internal_path, size in _list_slf_maps(slf_path):
                    name = os.path.basename(internal_path)
                    rel = f"{layer}/{slf_path.name}!{internal_path}"
                    fresh_slf_maps.append(SectorMapFile(
                        name=name,
                        path=_build_slf_uri(slf_path, internal_path),
                        rel_path=rel, size_bytes=size,
                        source="slf", slf_archive=str(slf_path),
                    ))

        # Find the Ja2Set.dat.xml in this layer if not yet found.
        if ja2set_xml is None:
            for xml_name in ("Ja2Set.dat.xml", "JA2SET.DAT.XML",
                             "ja2set.dat.xml"):
                candidate = layer_root / xml_name
                if candidate.is_file():
                    ja2set_xml = candidate
                    break

    slf_maps = cached_slf_maps if cached_slf_maps is not None else fresh_slf_maps

    # Persist the SLF cache (only) if we just rebuilt it. This survives
    # subsequent loose-map edits.
    if cached_slf_maps is None:
        try:
            slf_cache_path.parent.mkdir(parents=True, exist_ok=True)
            slf_cache_path.write_text(
                json.dumps({
                    "slf_fingerprint": slf_fp,
                    "maps": [m.model_dump() for m in slf_maps],
                }, indent=2),
                encoding="utf-8",
            )
        except OSError:
            pass

    # Merge loose + SLF with loose shadowing SLF on filename collisions.
    # JA2's VFS does this at load time; we mirror it for the inspector.
    seen_names: set[str] = set()
    maps: list[SectorMapFile] = []
    for m in loose_maps:
        key = m.name.lower()
        if key in seen_names:
            continue
        seen_names.add(key)
        maps.append(m)
    for m in slf_maps:
        key = m.name.lower()
        if key in seen_names:
            continue
        seen_names.add(key)
        maps.append(m)

    # Sort alphabetically — looks nicer than filesystem-walk order.
    maps.sort(key=lambda m: m.name.lower())
    result = InstallMaps(
        install_id=info.id,
        install_path=str(install_root),
        data_layers=layers_scanned,
        maps=maps,
        ja2set_xml=str(ja2set_xml) if ja2set_xml else None,
        cached=False,
        cache_fingerprint=current_fp,
        scanned_at=time.time(),
    )
    # Persist the combined cache too — saves the merge + iterdir on
    # cold sidecar restart.
    try:
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        cache_path.write_text(result.model_dump_json(indent=2), encoding="utf-8")
    except OSError:
        pass
    return result


@router.get("/installs/maps/stream")
def stream_install_maps(rescan: bool = Query(False)):
    """Newline-delimited JSON stream of the install scan.

    Each line is one JSON object of shape:
      {"event": "phase",     "phase": <id>, "label": <human-text>}
      {"event": "progress",  "current": <int>, "total": <int>, "detail": <str>}
      {"event": "done",      "data": <InstallMaps payload>}
      {"event": "error",     "message": <str>}

    Frontend opens this via fetch + response.body.getReader, parses
    lines, and updates a phase/progress UI in real time. The endpoint
    is otherwise functionally equivalent to GET /installs/maps — same
    cache logic, same final payload.

    Why NDJSON over SSE: SSE strips custom request headers (no token
    auth) and adds an "event: ...\\ndata: ..." framing on every line.
    NDJSON is one JSON object per line with no framing tax and works
    fine through Tauri's fetch + ReadableStream.
    """
    import json
    import time

    _require_renderer()
    state = get_state()
    info = state.active()
    if info is None:
        raise HTTPException(
            status_code=400,
            detail={"error": "NO_ACTIVE_INSTALL",
                    "message": "Activate an install in MercForge first."},
        )
    install_root = Path(info.path)
    layer_candidates = ("Data-1.13", "Data-DMK", "Data")

    def event(**kw) -> bytes:
        return (json.dumps(kw) + "\n").encode("utf-8")

    def gen():
        # 1. Combined cache check (instant return when nothing changed).
        yield event(event="phase", phase="check-cache",
                    label="Checking on-disk cache")
        current_fp = _cache_fingerprint(install_root, layer_candidates)
        cache_path = _install_maps_cache_path(info.id)
        if not rescan and cache_path.is_file():
            try:
                cached = json.loads(cache_path.read_text(encoding="utf-8"))
                if cached.get("cache_fingerprint") == current_fp:
                    cached["cached"] = True
                    yield event(event="done", data=cached)
                    return
            except (OSError, json.JSONDecodeError, ValueError):
                pass

        # 2. SLF cache check (loose changes don't invalidate this).
        slf_fp = _slf_fingerprint(install_root, layer_candidates)
        cached_slf_maps = None
        slf_cache_path = _install_slf_cache_path(info.id)
        if not rescan and slf_cache_path.is_file():
            try:
                slf_cached = json.loads(
                    slf_cache_path.read_text(encoding="utf-8"))
                if slf_cached.get("slf_fingerprint") == slf_fp:
                    cached_slf_maps = [SectorMapFile(**m)
                                        for m in slf_cached.get("maps", [])]
            except (OSError, json.JSONDecodeError, ValueError):
                cached_slf_maps = None

        # 3. Loose enumeration (always — cheap).
        yield event(event="phase", phase="loose",
                    label="Reading loose Maps directories")
        layers_scanned: list[str] = []
        loose_maps: list[SectorMapFile] = []
        ja2set_xml: Optional[Path] = None
        for layer in layer_candidates:
            layers_scanned.append(layer)
            layer_root = install_root / layer
            if not layer_root.is_dir():
                continue
            maps_dir = layer_root / "Maps"
            if maps_dir.is_dir():
                candidates = (sorted(maps_dir.glob("*.dat"))
                              + sorted(maps_dir.glob("*.DAT")))
                for p in candidates:
                    try:
                        size = p.stat().st_size
                    except OSError:
                        continue
                    try:
                        rel = p.relative_to(install_root)
                    except ValueError:
                        rel = p
                    loose_maps.append(SectorMapFile(
                        name=p.name, path=str(p),
                        rel_path=str(rel), size_bytes=size,
                        source="loose", slf_archive=None,
                    ))
            if ja2set_xml is None:
                for xml_name in ("Ja2Set.dat.xml", "JA2SET.DAT.XML",
                                 "ja2set.dat.xml"):
                    candidate = layer_root / xml_name
                    if candidate.is_file():
                        ja2set_xml = candidate
                        break

        # 4. SLF walk (if cache missed) — emit per-SLF progress.
        if cached_slf_maps is None:
            yield event(event="phase", phase="slf",
                        label="Walking Maps SLF archives")
            # Enumerate SLFs up-front to know the total for progress.
            # ONLY maps.slf — other SLFs (speech.slf, ambient.slf,
            # anims.slf, faces.slf, etc.) don't contain `.dat` sectors,
            # but `_list_slf_maps` would still open + walk them and
            # filter out the non-Maps entries. That's seconds of wasted
            # I/O per cold scan. The Maps.slf filter cuts the SLF
            # phase from O(all-slfs) to O(1) in practice (vanilla ships
            # exactly one Maps.slf per layer).
            slf_paths: list[Path] = []
            for layer in layer_candidates:
                layer_root = install_root / layer
                if not layer_root.is_dir():
                    continue
                for cand in (sorted(layer_root.glob("*.slf"))
                             + sorted(layer_root.glob("*.SLF"))):
                    if cand.name.lower() == "maps.slf":
                        slf_paths.append(cand)
            fresh_slf_maps: list[SectorMapFile] = []
            for idx, slf_path in enumerate(slf_paths):
                yield event(event="progress",
                            current=idx, total=len(slf_paths),
                            detail=slf_path.name)
                for internal_path, size in _list_slf_maps(slf_path):
                    name = os.path.basename(internal_path)
                    try:
                        rel_layer = slf_path.relative_to(install_root).parts[0]
                    except (ValueError, IndexError):
                        rel_layer = ""
                    rel = f"{rel_layer}/{slf_path.name}!{internal_path}"
                    fresh_slf_maps.append(SectorMapFile(
                        name=name,
                        path=_build_slf_uri(slf_path, internal_path),
                        rel_path=rel, size_bytes=size,
                        source="slf", slf_archive=str(slf_path),
                    ))
            yield event(event="progress",
                        current=len(slf_paths), total=len(slf_paths),
                        detail="done")
            slf_maps = fresh_slf_maps
            try:
                slf_cache_path.parent.mkdir(parents=True, exist_ok=True)
                slf_cache_path.write_text(
                    json.dumps({
                        "slf_fingerprint": slf_fp,
                        "maps": [m.model_dump() for m in slf_maps],
                    }, indent=2),
                    encoding="utf-8",
                )
            except OSError:
                pass
        else:
            yield event(event="phase", phase="slf-cached",
                        label=f"SLF archives cached "
                              f"({len(cached_slf_maps)} entries)")
            slf_maps = cached_slf_maps

        # 5. Merge + write combined cache.
        yield event(event="phase", phase="merge", label="Merging listings")
        seen_names: set[str] = set()
        maps: list[SectorMapFile] = []
        for m in loose_maps:
            key = m.name.lower()
            if key in seen_names:
                continue
            seen_names.add(key)
            maps.append(m)
        for m in slf_maps:
            key = m.name.lower()
            if key in seen_names:
                continue
            seen_names.add(key)
            maps.append(m)
        maps.sort(key=lambda m: m.name.lower())
        result = InstallMaps(
            install_id=info.id,
            install_path=str(install_root),
            data_layers=layers_scanned,
            maps=maps,
            ja2set_xml=str(ja2set_xml) if ja2set_xml else None,
            cached=False,
            cache_fingerprint=current_fp,
            scanned_at=time.time(),
        )
        try:
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            cache_path.write_text(result.model_dump_json(indent=2),
                                   encoding="utf-8")
        except OSError:
            pass
        yield event(event="done", data=result.model_dump())

    return StreamingResponse(gen(), media_type="application/x-ndjson",
                              headers={"Cache-Control": "no-store"})


# ─── Sector / town names (strategic hub grid, R6) ────────────────────────
# The hub-grid sector picker labels each A1–P16 cell with its town/sector
# name. We reuse building_library.load_sector_names, which reads the active
# install's TableData/Map/SectorNames.xml (The Wasteland renames towns
# there, so it's the canonical label source). Returns {} when absent —
# the grid then falls back to bare sector codes.

class SectorNamesResult(BaseModel):
    install_id: str
    install_path: str
    # SectorGrid (e.g. "C5") → explored display name (e.g. "The Den").
    names: dict[str, str]


@router.get("/installs/sector-names", response_model=SectorNamesResult)
def list_active_install_sector_names():
    """Grid→town-name map from the active install's SectorNames.xml.

    Drives the strategic hub grid's cell labels. Cheap (one small XML
    parse); no caching needed. Returns an empty `names` map rather than
    erroring when SectorNames.xml is absent, so the grid degrades to bare
    sector codes instead of crashing."""
    from mercwizard_core.mapforge_engine import building_library as bl

    _require_renderer()
    state = get_state()
    info = state.active()
    if info is None:
        raise HTTPException(
            status_code=400,
            detail={"error": "NO_ACTIVE_INSTALL",
                    "message": "Activate an install in MercForge first."},
        )
    install_root = Path(info.path)
    try:
        names = bl.load_sector_names(install_root)
    except Exception:
        # A malformed SectorNames.xml must not break the picker — fall
        # back to no names (bare grid codes).
        names = {}
    return SectorNamesResult(
        install_id=info.id,
        install_path=str(install_root),
        names=names,
    )


# ─── Radar-thumbnail sprite sheet (hub strategic-grid map previews) ──────
# The hub grid showed sector codes + town names as TEXT only. JA2 ships a
# pre-baked 88x44, 8-bit ETRLE radar STI per sector inside Radarmaps.slf,
# keyed by sector code (A9.STI, C5.STI, A10_B1.STI). We pack every sector's
# radar into ONE PNG sprite sheet + a code→cell manifest (mirroring the
# roster portrait sheet) so the grid paints a map thumbnail per cell with
# zero per-cell HTTP. Measured ~1.5 ms/decode, ~0.5 s for a full install.
#
# Pure READ path — opens Radarmaps.slf + any loose RADARMAPS/<code>.STI
# override, decodes, composites. NO .dat writes, so the MapForge
# data-safety gate does not apply. Distinct from sector_radar's
# WRITE/generate path (which renders + ETRLE-encodes a NEW radar).
import threading as _threading

_RADAR_THUMB_CACHE_VERSION = 1  # bump if the bake/compositing logic changes
_RADAR_CELL_W = 88
_RADAR_CELL_H = 44
_RADAR_THUMB_COLS = 16
_RADAR_THUMB_MEM: dict[tuple[str, str], tuple[bytes, dict]] = {}
_RADAR_THUMB_MEM_MAX = 4
_RADAR_THUMB_LOCK = _threading.Lock()


def _radar_thumb_cache_dir() -> Path:
    base = os.environ.get("APPDATA")
    root = Path(base) / "MercWizard" if base else Path.home() / ".config" / "MercWizard"
    return root / "mapforge" / "radar_thumbs"


def _radarmaps_slf_paths(install_root: Path) -> list[Path]:
    """Existing Radarmaps.slf across content layers, VFS priority order
    (Data-1.13 before Data → a mod's radars override vanilla)."""
    out: list[Path] = []
    for layer in _TILESET_LAYERS:
        layer_root = install_root / layer
        if not layer_root.is_dir():
            continue
        direct = layer_root / "Radarmaps.slf"
        if direct.is_file():
            out.append(direct)
            continue
        # Case-insensitive fallback (a bundle from a case-sensitive FS).
        for e in layer_root.iterdir():
            if e.is_file() and e.name.lower() == "radarmaps.slf":
                out.append(e)
                break
    return out


def _radar_override_dir(install_root: Path) -> Optional[Path]:
    """The writable VFS profile's RADARMAPS dir — the ACTUAL target
    sector_radar regenerates radars into via resolve_override_write
    (Profiles/UserProfile_*/RADARMAPS). It is front-of-stack and overrides
    Radarmaps.slf, so the thumb bake must read it FIRST and the fingerprint
    must track it; otherwise a user-regenerated radar is never shown and
    never busts the cache. None for legacy layouts with no write profile."""
    try:
        layout = parse_vfs_config(install_root)
        ewp = layout.engine_write_profile()
        if ewp is not None and ewp.profile_root is not None:
            return ewp.profile_root / "RADARMAPS"
    except Exception:  # noqa: BLE001 — VfsConfigError / any parse failure
        return None
    return None


def _radar_loose_dirs(install_root: Path) -> list[Path]:
    """RADARMAPS override dirs, highest priority first: the writable VFS
    profile (where regen lands) ahead of the content-layer RADARMAPS dirs."""
    dirs: list[Path] = []
    odir = _radar_override_dir(install_root)
    if odir is not None:
        dirs.append(odir)
    for layer in _TILESET_LAYERS:
        dirs.append(install_root / layer / "RADARMAPS")
    return dirs


def _radar_thumb_fingerprint(install_root: Path) -> str:
    """Cache key over the install's radar sources — bundled Radarmaps.slf
    archives + any loose RADARMAPS override dir. Bumps when a user
    regenerates a radar (sector_radar) so the thumb refreshes."""
    h = hashlib.sha1()
    h.update(str(install_root.resolve()).encode("utf-8", "replace"))
    for slf in _radarmaps_slf_paths(install_root):
        try:
            st = slf.stat()
            h.update(f"|slf:{slf.name}:{st.st_mtime_ns}:{st.st_size}".encode())
        except OSError:
            pass
    # Loose RADARMAPS dirs (writable profile first). Hash PER FILE, not by
    # dir mtime — a regenerate OVERWRITES an existing <code>.STI in place,
    # which need not bump the directory's mtime.
    for rd in _radar_loose_dirs(install_root):
        if not rd.is_dir():
            continue
        try:
            entries = sorted(rd.iterdir())
        except OSError:
            continue
        for p in entries:
            if p.suffix.upper() != ".STI":
                continue
            try:
                st = p.stat()
                h.update(f"|loose:{p.name}:{st.st_mtime_ns}:{st.st_size}".encode())
            except OSError:
                pass
    return h.hexdigest()[:20]


def _decode_radar_to_rgba(data: bytes):
    """Decode a radar STI's frame 0 to a PIL RGBA image, or None."""
    from mercwizard_core.sti_decode import decode_sti_frame_to_png
    png = decode_sti_frame_to_png(data, frame_index=0)
    if png is None:
        return None
    try:
        return PIL.Image.open(io.BytesIO(png)).convert("RGBA")
    except Exception:  # noqa: BLE001
        return None


def _bake_radar_thumb_sheet(install_root: Path) -> tuple[bytes, dict]:
    """Pack every sector's radar STI into one PNG grid + a code→cell
    manifest. Loose RADARMAPS/<code>.STI overrides win over the bundled
    Radarmaps.slf entry (matches the engine's VFS read precedence)."""
    from ja2py.fileformats.SlfFS import SlfFS

    images: dict[str, "PIL.Image.Image"] = {}  # CODE → image (first writer wins)
    errors: list[str] = []

    def _put(code: str, img) -> None:
        key = code.upper()
        if img is not None and key not in images:
            images[key] = img

    # 1) Loose override RADARMAPS/<code>.STI (highest priority — a radar the
    #    user just (re)generated lands in the writable VFS profile, scanned
    #    first, above the content-layer dirs and the bundled SLF).
    for rd in _radar_loose_dirs(install_root):
        if not rd.is_dir():
            continue
        try:
            entries = sorted(rd.iterdir())
        except OSError:
            continue
        for p in entries:
            if not (p.is_file() and p.suffix.upper() == ".STI"):
                continue
            code = p.stem.upper()
            if code in images:
                continue  # higher-priority dir already supplied it
            try:
                data = p.read_bytes()
            except OSError:
                continue
            img = _decode_radar_to_rgba(data)
            if img is None:
                errors.append(code)  # mirror the SLF branch — don't drop silently
            else:
                _put(p.stem, img)

    # 2) Bundled Radarmaps.slf (vanilla minimaps).
    for slf_path in _radarmaps_slf_paths(install_root):
        try:
            fs_slf = SlfFS(str(slf_path))
        except Exception:  # noqa: BLE001
            continue
        try:
            members = list(fs_slf.walk.files())
        except Exception:  # noqa: BLE001
            continue
        for member in members:
            name = os.path.basename(member)
            if not name.upper().endswith(".STI"):
                continue
            code = name[:-4].upper()
            if code in images:
                continue
            try:
                with fs_slf.openbin(member, "r") as f:
                    data = f.read()
            except Exception:  # noqa: BLE001
                continue
            img = _decode_radar_to_rgba(data)
            if img is None:
                errors.append(code)
            else:
                _put(code, img)

    codes = sorted(images.keys())
    n = len(codes)
    cols = _RADAR_THUMB_COLS
    rows = max(1, (n + cols - 1) // cols)
    sheet_w = cols * _RADAR_CELL_W
    sheet_h = rows * _RADAR_CELL_H
    sheet = PIL.Image.new("RGBA", (sheet_w, sheet_h), (0, 0, 0, 0))
    cells: list[dict] = []
    for i, code in enumerate(codes):
        img = images[code]
        if img.size != (_RADAR_CELL_W, _RADAR_CELL_H):
            img = img.resize((_RADAR_CELL_W, _RADAR_CELL_H), PIL.Image.Resampling.BOX)
        x = (i % cols) * _RADAR_CELL_W
        y = (i // cols) * _RADAR_CELL_H
        sheet.paste(img, (x, y), img)
        cells.append({"code": code, "x": x, "y": y})

    buf = io.BytesIO()
    sheet.save(buf, format="PNG", optimize=True)
    manifest = {
        "cell_w": _RADAR_CELL_W, "cell_h": _RADAR_CELL_H,
        "cols": cols, "rows": rows,
        "sheet_w": sheet_w, "sheet_h": sheet_h,
        "cells": cells,
        "errors": sorted(set(errors)),
        "count": n,
    }
    return buf.getvalue(), manifest


def _radar_thumb_get_or_bake(install_id: str, install_root: Path) -> tuple[bytes, dict]:
    """(png, manifest) via mem → disk → bake, keyed on a radar-source
    fingerprint so a regenerated radar refreshes the sheet."""
    fp = _radar_thumb_fingerprint(install_root)
    mem_key = (install_id, fp)
    with _RADAR_THUMB_LOCK:
        hit = _RADAR_THUMB_MEM.get(mem_key)
    if hit is not None:
        return hit

    d = _radar_thumb_cache_dir()
    prefix = hashlib.md5(
        f"v{_RADAR_THUMB_CACHE_VERSION}|{install_id}".encode("utf-8")
    ).hexdigest()
    png_path = d / f"{prefix}__{fp}.png"
    json_path = d / f"{prefix}__{fp}.json"
    try:
        if png_path.is_file() and json_path.is_file():
            res = (png_path.read_bytes(),
                   json.loads(json_path.read_text(encoding="utf-8")))
            if isinstance(res[1], dict):
                with _RADAR_THUMB_LOCK:
                    _RADAR_THUMB_MEM[mem_key] = res
                return res
    except (OSError, ValueError):
        pass  # missing / half-written / corrupt → re-bake

    # Serialize the bake so the parallel .png + .json fetch bakes once.
    with _RADAR_THUMB_LOCK:
        hit = _RADAR_THUMB_MEM.get(mem_key)
        if hit is not None:
            return hit
        res = _bake_radar_thumb_sheet(install_root)
        if len(_RADAR_THUMB_MEM) >= _RADAR_THUMB_MEM_MAX:
            try:
                del _RADAR_THUMB_MEM[next(iter(_RADAR_THUMB_MEM))]
            except StopIteration:
                pass
        _RADAR_THUMB_MEM[mem_key] = res

    try:
        d.mkdir(parents=True, exist_ok=True)
        tmp_png = d / f"{prefix}__{fp}.png.tmp"
        tmp_json = d / f"{prefix}__{fp}.json.tmp"
        tmp_png.write_bytes(res[0])
        tmp_json.write_text(json.dumps(res[1]), encoding="utf-8")
        os.replace(tmp_png, png_path)
        os.replace(tmp_json, json_path)
        for p in list(d.glob(f"{prefix}__*.png")) + list(d.glob(f"{prefix}__*.json")):
            if p.name not in (png_path.name, json_path.name):
                try:
                    p.unlink()
                except OSError:
                    pass
    except OSError:
        pass  # disk cache best-effort
    return res


def _active_install_or_400():
    info = get_state().active()
    if info is None:
        raise HTTPException(
            status_code=400,
            detail={"error": "NO_ACTIVE_INSTALL",
                    "message": "Activate an install in MercForge first."},
        )
    return info


@router.get("/installs/radar-thumbs.png")
def get_radar_thumb_sheet() -> Response:
    """One PNG packing every sector's 88x44 radar minimap into a 16-column
    grid. Pair with /installs/radar-thumbs.json for the code→cell offsets;
    the hub grid then paints each cell via CSS background-position."""
    _require_renderer()
    info = _active_install_or_400()
    png, _manifest = _radar_thumb_get_or_bake(info.id, Path(info.path))
    return Response(
        content=png,
        media_type="image/png",
        headers={
            "Cache-Control": "private, max-age=300, must-revalidate",
            "ETag": f'"{hashlib.md5(png[:4096]).hexdigest()[:16]}-{len(png)}"',
        },
    )


@router.get("/installs/radar-thumbs.json")
def get_radar_thumb_meta() -> dict:
    """Companion manifest for /installs/radar-thumbs.png — sector code →
    (x, y) origin inside the sheet, plus cell/grid dims."""
    _require_renderer()
    info = _active_install_or_400()
    _png, manifest = _radar_thumb_get_or_bake(info.id, Path(info.path))
    return manifest


@router.get("/sector/info", response_model=SectorInfo)
def sector_info(
    dat: str = Query(..., description="Absolute path to .dat sector file "
                                       "or slf:// URI"),
):
    """Parse a .dat and return its dimensions, room list, and layer totals."""
    _require_renderer()
    dat_path = _resolve_dat_path(dat)
    parsed = parse_dat_file(dat_path)
    cols = parsed["cols"]
    rows = parsed["rows"]
    # Aggregate rooms.
    room_tiles: dict[int, list[tuple[int, int]]] = {}
    for g, r in enumerate(parsed["rooms"]):
        if r == 0:
            continue  # 0 = "no room"
        room_tiles.setdefault(r, []).append((g % cols, g // cols))
    rooms: list[RoomSummary] = []
    for r_id, tiles in sorted(room_tiles.items()):
        xs = [t[0] for t in tiles]
        ys = [t[1] for t in tiles]
        rooms.append(RoomSummary(
            room_id=r_id, tile_count=len(tiles),
            bbox=(min(xs), min(ys), max(xs), max(ys)),
        ))
    layer_totals = {k: parsed["counts"][k]
                    for k in ("land", "obj", "struct", "shadow", "roof", "onroof")}
    return SectorInfo(
        dat_path=str(dat_path),
        rows=rows, cols=cols,
        tileset_in_header=parsed["tileset"],
        rooms=rooms,
        layer_totals=layer_totals,
    )


@router.get("/sector/render")
def sector_render(
    dat: str = Query(..., description="Absolute path to .dat sector file"),
    xml: str = Query(..., description="Absolute path to Ja2Set.dat.xml"),
    tileset: int = Query(..., description="Tileset index"),
    room: Optional[int] = Query(None, description="Frame around this room"),
    bbox: Optional[str] = Query(None, description="x0,y0,x1,y1 (overrides room)"),
    ring: int = Query(5, description="Tile padding around room"),
    full: bool = Query(False, description="Render entire sector"),
    highlight: bool = Query(True, description="Tint the targeted room green"),
    skip_layers: str = Query("", description="Comma-separated layers to skip"),
    scale: int = Query(1, description="Integer NEAREST upscale"),
):
    """Render a sector to PNG. Returns image/png bytes.

    Mirrors iso_renderer.py CLI surface. The frontend hits this URL
    directly as an <img src=...>, so it must work as a plain GET.
    """
    _require_renderer()
    dat_path = _resolve_dat_path(dat)
    xml_path = _validate_path(xml, ".xml")
    loose_dirs, slf_paths = _tileset_paths_for(xml_path)
    renderer = IsoRenderer(dat_path, xml_path, tileset, ring=ring,
                           loose_dirs=loose_dirs, slf_paths=slf_paths)
    region_bbox = None
    target_room = room
    if full:
        target_room = None
        region_bbox = None
    elif bbox:
        try:
            region_bbox = tuple(int(v) for v in bbox.split(","))
        except (ValueError, TypeError):
            raise HTTPException(
                status_code=400,
                detail={"error": "BAD_BBOX",
                        "message": "bbox must be 'x0,y0,x1,y1'"},
            )
        target_room = None
    skip_set = {s.strip() for s in skip_layers.split(",") if s.strip()}
    try:
        canvas = renderer.render(
            room_id=target_room, bbox=region_bbox,
            highlight_room=highlight, skip_layers=skip_set,
        )
    except ValueError as e:
        # IsoRenderer raises ValueError for "Room X not found" and
        # similar input-validation problems. Surface as 404 so the
        # frontend can distinguish "user picked a bad room" from a
        # real internal error.
        raise HTTPException(
            status_code=404,
            detail={"error": "INVALID_REGION", "message": str(e)},
        )
    # Capture iso anchor + canvas dims for the click-to-tile inversion
    # the frontend needs. These attributes are set by IsoRenderer.render()
    # immediately before drawing — they're how the renderer itself maps
    # tile coords to canvas pixels (iso_renderer.py:266-268).
    canvas_w, canvas_h = canvas.size
    ix_min = renderer._ix_min
    iy_min = renderer._iy_min
    title = (
        f"{dat_path.name} ts={tileset}"
        + (f" room={target_room}" if target_room is not None else "")
        + (f" bbox={bbox}" if region_bbox else "")
        + (f" skip={','.join(sorted(skip_set))}" if skip_set else "")
    )
    add_title(canvas, title)
    if scale > 1:
        from PIL import Image
        canvas = canvas.resize(
            (canvas.size[0] * scale, canvas.size[1] * scale),
            Image.NEAREST,
        )
    buf = io.BytesIO()
    canvas.save(buf, format="PNG", optimize=False)
    png_bytes = buf.getvalue()
    return Response(
        content=png_bytes,
        media_type="image/png",
        headers={
            "Cache-Control": "no-store",  # editor renders change with edits
            "Content-Length": str(len(png_bytes)),
            # Click-to-tile inversion data. The renderer puts the SOUTH
            # apex of tile (tx,ty) at canvas pixel:
            #   canvas_x = (tx - ty) * 20 - ix_min
            #   canvas_y = (tx + ty) * 10 - iy_min
            # Inverse (frontend does this on click):
            #   A = (click_x + ix_min) / 20  = tx - ty
            #   B = (click_y + iy_min) / 10  = tx + ty
            #   tx = (A + B) / 2
            #   ty = (B - A) / 2
            # Header names must be ASCII-safe and Tauri's CSP must allow
            # custom response headers to reach JS (default fetch does).
            "X-MapForge-IxMin": str(ix_min),
            "X-MapForge-IyMin": str(iy_min),
            "X-MapForge-CanvasW": str(canvas_w),
            "X-MapForge-CanvasH": str(canvas_h),
            "X-MapForge-TileW": "40",
            "X-MapForge-TileH": "20",
            # CORS: custom headers must be in Access-Control-Expose-Headers
            # for browser-side fetch().headers.get() to see them. Without
            # this the headers ARE on the wire but JS gets undefined.
            "Access-Control-Expose-Headers":
                "X-MapForge-IxMin, X-MapForge-IyMin, "
                "X-MapForge-CanvasW, X-MapForge-CanvasH, "
                "X-MapForge-TileW, X-MapForge-TileH",
        },
    )


@router.get("/sector/tile", response_model=TileInspection)
def sector_tile(
    dat: str = Query(...),
    xml: str = Query(...),
    tileset: int = Query(...),
    x: int = Query(..., ge=0, le=1023),
    y: int = Query(..., ge=0, le=1023),
):
    """Return everything stored at one tile across all layers, with each
    entry's STI filename resolved through the tileset's slot map.

    This is the Phase 0 "what's on this tile?" inspector endpoint.
    """
    _require_renderer()
    dat_path = _resolve_dat_path(dat)
    xml_path = _validate_path(xml, ".xml")
    parsed = parse_dat_file(dat_path)
    cols = parsed["cols"]
    rows = parsed["rows"]
    if x >= cols or y >= rows:
        raise HTTPException(
            status_code=400,
            detail={"error": "OUT_OF_BOUNDS",
                    "message": f"({x},{y}) outside {cols}x{rows}"},
        )
    gridno = y * cols + x
    # Load slot map for STI-name resolution.
    slot_map = load_tileset_xml(xml_path, tileset)
    layers_out: dict[str, list[LayerEntry]] = {}
    for layer in ("land", "objs", "shadows", "structs", "roofs", "onroofs"):
        entries = parsed[layer][gridno]
        layers_out[layer] = [
            LayerEntry(
                slot=slot, sub=sub,
                sti_filename=slot_map.get(slot),
                sti_frame_index_0based=sub - 1,
            )
            for slot, sub in entries
        ]
    return TileInspection(
        x=x, y=y, gridno=gridno,
        room_id=parsed["rooms"][gridno],
        height=parsed["heights"][gridno],
        world_flags=parsed["world_flags"][gridno],
        layers=layers_out,
    )


# ─── STI frame preview (Phase 2 visual picker) ────────────────────────
@router.get("/sti/frame")
def sti_frame(
    xml: str = Query(..., description="Path to Ja2Set.dat.xml"),
    tileset: int = Query(..., description="Tileset index"),
    slot: int = Query(..., description="Slot number from the .dat layer entry"),
    sub: int = Query(..., description="1-based sub-index from the .dat entry"),
    pad: int = Query(2, description="Transparent pixels of padding"),
):
    """Render a single STI sub-frame as a PNG (transparent background).

    Used by the MapForge tile inspector to show what each (slot, sub)
    actually looks like — much easier than reasoning about "slot 39
    sub 14" in the abstract.

    Engine convention: stored sub-indices are 1-based, STI frame arrays
    are 0-based (tiledef.cpp:1024). Sub=1 → frame[0].
    """
    _require_renderer()
    xml_path = _validate_path(xml, ".xml")
    # Reuse iso_renderer's STI cache / loader infrastructure.
    slot_map = load_tileset_xml(xml_path, tileset)
    name = slot_map.get(slot)
    if not name:
        raise HTTPException(
            status_code=404,
            detail={"error": "SLOT_NOT_DEFINED",
                    "message": f"slot {slot} not defined in tileset {tileset}"},
        )
    # Asset roots: the active install's tileset dirs across its VFS layers.
    loose, slf = _tileset_paths_for(xml_path)
    cache = StiCache(tileset, loose_dirs=loose, slf_paths=slf)
    frames = cache.get(name)
    if not frames:
        raise HTTPException(
            status_code=404,
            detail={"error": "STI_NOT_FOUND",
                    "message": f"could not load {name} for slot {slot}"},
        )
    frame_idx = sub - 1
    if not 0 <= frame_idx < len(frames):
        raise HTTPException(
            status_code=404,
            detail={"error": "SUB_OUT_OF_RANGE",
                    "message": (f"sub {sub} (frame index {frame_idx}) "
                                f"out of range — {name} has {len(frames)} frames")},
        )
    pil, _ox, _oy = frames[frame_idx]
    # Pad with transparent border for visual breathing room.
    from PIL import Image
    w, h = pil.size
    canvas = Image.new("RGBA", (w + pad * 2, h + pad * 2), (0, 0, 0, 0))
    canvas.paste(pil, (pad, pad), pil)
    buf = io.BytesIO()
    canvas.save(buf, format="PNG", optimize=False)
    png_bytes = buf.getvalue()
    return Response(
        content=png_bytes,
        media_type="image/png",
        headers={
            # Frames don't change unless the STI file itself does, so a
            # short cache is safe. 1h covers an editing session.
            "Cache-Control": "max-age=3600",
            "Content-Length": str(len(png_bytes)),
            "X-MapForge-StiName": name,
            "X-MapForge-FrameCount": str(len(frames)),
            "Access-Control-Expose-Headers":
                "X-MapForge-StiName, X-MapForge-FrameCount",
        },
    )


@router.get("/sti/frame-count")
def sti_frame_count(
    xml: str = Query(...),
    tileset: int = Query(...),
    slot: int = Query(...),
):
    """Cheap query for how many sub-frames a slot's STI has, so the
    frontend can iterate the picker without trial-and-erroring 404s."""
    _require_renderer()
    xml_path = _validate_path(xml, ".xml")
    slot_map = load_tileset_xml(xml_path, tileset)
    name = slot_map.get(slot)
    if not name:
        raise HTTPException(
            status_code=404,
            detail={"error": "SLOT_NOT_DEFINED",
                    "message": f"slot {slot} not defined in tileset {tileset}"},
        )
    loose, slf = _tileset_paths_for(xml_path)
    cache = StiCache(tileset, loose_dirs=loose, slf_paths=slf)
    frames = cache.get(name)
    return {
        "sti_filename": name,
        "frame_count": len(frames),
    }


# ─── Tileset palette (Phase 2B) ───────────────────────────────────────
# Categorized slot inventory the frontend uses to render the asset
# sidebar. Categories come from the authoritative TileDat slot→family
# table (`tile_families.slot_family`, baked from the TileTypeDefines enum)
# — the slot number IS the engine tile-type, so this is exact and empties
# the old 200-slot "Other" bucket. The filename heuristic below is kept
# only as a fallback for slots the table doesn't cover (item/UI slots,
# which the palette filters out anyway).

# Filename-prefix → category mapping (FALLBACK). Order matters (first match wins).
_PALETTE_RULES: list[tuple[str, str]] = [
    # (substring-case-insensitive, category)
    ("floor",      "floor"),
    ("cvflr",      "floor"),
    ("trpgras",    "floor"),
    ("grass",      "floor"),
    ("sand",       "floor"),
    ("gravel",     "floor"),
    ("conc",       "floor"),
    ("asphalt",    "floor"),
    ("dirt",       "floor"),
    ("road",       "floor"),
    ("water",      "floor"),
    # Building structure
    ("door",       "door"),
    ("window",     "window"),
    ("frame",      "window"),
    ("build_",     "wall"),
    ("wall",       "wall"),
    ("brick",      "wall"),
    ("fence",      "wall"),
    ("wirefenc",   "wall"),
    # Roofs
    ("flat_r",     "roof"),
    ("slant",      "roof"),
    ("roof",       "roof"),
    # Furniture
    ("furn",       "furniture"),
    ("basefrn",    "furniture"),
    ("bed",        "furniture"),
    ("chair",      "furniture"),
    # Vegetation
    ("tree",       "veg"),
    ("bush",       "veg"),
    ("cactus",     "veg"),
    ("flower",     "veg"),
    ("veg",        "veg"),
    # Rubble / decals / scatter
    ("rubble",     "scatter"),
    ("debris",     "scatter"),
    ("decal",      "scatter"),
    ("blood",      "scatter"),
    ("crater",     "scatter"),
    # Vehicles
    ("car",        "vehicle"),
    ("truck",      "vehicle"),
    ("tank",       "vehicle"),
]

# Category display order in the UI. "shadow" groups the drop-shadow slots
# (only visible when the Show-shadows toggle is on); "other" catches any
# fallback-classified slot.
PALETTE_CATEGORY_ORDER = [
    "floor", "wall", "door", "window", "roof",
    "furniture", "veg", "scatter", "vehicle", "shadow", "other",
]


def _categorize_sti(filename: str) -> str:
    """Return a category key for an STI filename (lowercased substring
    matching against `_PALETTE_RULES`)."""
    fn = filename.lower()
    for needle, cat in _PALETTE_RULES:
        if needle in fn:
            return cat
    return "other"


class PaletteSlot(BaseModel):
    slot: int
    sti_filename: str
    frame_count: int
    category: str
    has_jsd: bool   # multi-tile structures have JSDs; useful UX hint


class TilesetPalette(BaseModel):
    tileset: int
    xml_path: str
    slots: list[PaletteSlot]
    category_order: list[str]


@router.get("/tileset/palette", response_model=TilesetPalette)
def tileset_palette(
    xml: str = Query(..., description="Path to Ja2Set.dat.xml"),
    tileset: int = Query(..., description="Tileset index"),
):
    """Categorized inventory of every slot defined in `tileset` (with
    tile-0 inheritance). For each slot: the resolved STI filename,
    how many sub-frames it has, a category guess, and whether a
    matching .jsd exists (= multi-tile structural piece)."""
    _require_renderer()
    xml_path = _validate_path(xml, ".xml")
    slot_map = load_tileset_xml(xml_path, tileset)

    loose, slf = _tileset_paths_for(xml_path)
    cache = StiCache(tileset, loose_dirs=loose, slf_paths=slf)

    slots: list[PaletteSlot] = []
    for slot_idx in sorted(slot_map):
        name = slot_map[slot_idx]
        if not name:
            continue
        # Skip slots above the real tile-content boundary. Slots 123+ are
        # GUNS / P*ITEMS / cursors / effects (TileTypeDefines past
        # SECONDREVEALEDHIGHROOFS) — never paintable map tiles. They'd only
        # land in "Other"; dropping them keeps the brush palette terrain-only.
        if slot_idx > MAX_TILE_SLOT:
            continue
        frames = cache.get(name)
        # JSD heuristic: matching .jsd lives next to the .sti (or in
        # the same SLF). Loose check only (fast); SLF check would
        # require iterating both archives.
        jsd_name = name.replace(".sti", ".jsd").replace(".STI", ".JSD")
        has_jsd = False
        for base in loose:
            if not base.exists():
                continue
            for sub in (str(tileset), "0"):
                cand = base / sub / jsd_name
                if cand.exists() or (base / sub / jsd_name.lower()).exists():
                    has_jsd = True
                    break
            if has_jsd:
                break
        slots.append(PaletteSlot(
            slot=slot_idx,
            sti_filename=name,
            frame_count=len(frames),
            # Authoritative slot→family table first; filename heuristic only
            # for slots the table doesn't cover (item/UI slots, filtered out).
            category=slot_family(slot_idx) or _categorize_sti(name),
            has_jsd=has_jsd,
        ))
    return TilesetPalette(
        tileset=tileset,
        xml_path=str(xml_path),
        slots=slots,
        category_order=PALETTE_CATEGORY_ORDER,
    )


@router.get("/tilesets", response_model=TilesetList)
def list_tilesets(
    xml: str = Query(..., description="Path to Ja2Set.dat.xml"),
):
    """Enumerate the tileset blocks in `Ja2Set.dat.xml`. Used by the
    Tileset Editor's first screen (`/tileset-editor`) to render the
    picker grid.

    For each <Tileset index="N">: returns the index, optional <Name>
    text, count of <file> entries, and whether it inherits from
    tileset 0 (i.e. all non-zero tilesets do, by engine convention).
    Sorted by index ascending.
    """
    import xml.etree.ElementTree as _ET
    xml_path = _validate_path(xml, ".xml")
    try:
        tree = _ET.parse(xml_path)
    except (OSError, _ET.ParseError) as e:
        raise HTTPException(500, {
            "error": "JA2SET_PARSE_FAILED",
            "message": f"could not parse {xml_path.name}: {e}",
        })
    out: list[TilesetInfo] = []
    for ts in tree.getroot().iter("Tileset"):
        try:
            idx = int(ts.get("index", "-1"))
        except ValueError:
            continue
        if idx < 0:
            continue
        name_node = ts.find("Name")
        name = (name_node.text or "").strip() if name_node is not None else None
        fnode = ts.find("Files")
        slot_count = 0
        if fnode is not None:
            slot_count = sum(1 for _ in fnode.findall("file"))
        out.append(TilesetInfo(
            index=idx,
            name=name or None,
            slot_count=slot_count,
            inherits_from_0=(idx != 0),
        ))
    out.sort(key=lambda t: t.index)
    return TilesetList(xml_path=str(xml_path), tilesets=out)


# ─── Palette sprite sheet (Phase 2B perf) ─────────────────────────────
# One PNG with EVERY slot's frame-0 packed into a grid. Replaces ~150
# individual /sti/frame requests with one, which (a) saves the per-
# request HTTP overhead, (b) gets past the browser's 6-concurrent-
# connections-per-host limit, (c) lets the backend amortize the
# StiCache.get() cost across the whole tileset in one pass.
#
# The grid is uniform 64×64 cells (sprites are scaled-down to fit, with
# letterboxing for non-square). Frontend uses CSS background-position
# to crop each cell.
#
# Cached to %APPDATA%/MercWizard/mapforge/palette_sheets/<tileset>.png
# keyed by the tileset's slot map (any STI change invalidates).

_PALETTE_SHEET_CACHE = (
    Path(os.environ.get("APPDATA") or Path.home() / ".config")
    / "MercWizard" / "mapforge" / "palette_sheets"
)
PALETTE_SHEET_CELL = 64
PALETTE_SHEET_COLS = 8


def _palette_sheet_fingerprint(xml_path: Path, tileset: int,
                                slot_map: dict[int, str]) -> str:
    """Hash of the slot map for cache invalidation. Bump the suffix
    constant whenever the bake algorithm itself changes — otherwise
    the on-disk PNG cache will keep serving old thumbnails."""
    h = hashlib.sha1()
    # Bake-algorithm version. Bumped when the thumbnail-picking logic
    # changed to scan past blank frame[0]s for multi-tile structs
    # (2_HELI etc.) so the user sees the actual sprite.
    h.update(b"palette-bake-v2|")
    h.update(f"{xml_path.resolve()}|{tileset}".encode("utf-8", "replace"))
    for slot, name in sorted(slot_map.items()):
        h.update(f"|{slot}={name}".encode("utf-8", "replace"))
    return h.hexdigest()[:16]


def _frame_has_visible_pixels(pil) -> bool:
    """True when an STI frame has meaningfully visible content.

    "Meaningful" = the alpha channel sums to more than a small threshold
    relative to the frame area. We compare against a fraction of the
    total to ignore frames that are mostly transparent with a handful
    of stray pixels (which still look blank at thumbnail size).

    Why this exists: multi-tile structs (2_HELI, vehicles, big debris)
    ship with a deliberately blank or near-blank frame[0] that acts as
    the JSD anchor / registration sub. The first visible piece is at
    frame[1] or later. Palette thumbnails sampled at frame[0] come out
    empty for these slots, which looks broken to the user.
    """
    if pil.mode != "RGBA":
        # Indexed / RGB frames don't have alpha — treat as visible.
        return True
    bbox = pil.getbbox()
    if bbox is None:
        return False
    w, h = pil.size
    bw, bh = bbox[2] - bbox[0], bbox[3] - bbox[1]
    if w * h == 0 or bw * bh == 0:
        return False
    # The bbox of any non-transparent pixel must occupy at least 4% of
    # the frame area. Tunable: low enough to catch tiny sprites, high
    # enough to skip "single stray pixel" registration frames.
    return (bw * bh) / (w * h) >= 0.04


class PaletteSheetCell(BaseModel):
    slot: int
    sti_filename: str
    cell_x: int   # column index on the sheet
    cell_y: int   # row index on the sheet
    px: int       # pixel x of cell top-left
    py: int       # pixel y of cell top-left
    w: int        # cell size (cell width)
    h: int        # cell size


class PaletteSheetMeta(BaseModel):
    tileset: int
    cell: int                 # px size per cell (square)
    cols: int                 # columns on the sheet
    rows: int                 # rows on the sheet (computed)
    sheet_w: int
    sheet_h: int
    cells: list[PaletteSheetCell]
    fingerprint: str          # for client-side cache validation


@router.get("/tileset/palette-sheet-meta", response_model=PaletteSheetMeta)
def palette_sheet_meta(
    xml: str = Query(...),
    tileset: int = Query(...),
):
    _require_renderer()
    xml_path = _validate_path(xml, ".xml")
    slot_map = load_tileset_xml(xml_path, tileset)
    sorted_slots = [(s, n) for s, n in sorted(slot_map.items()) if n]
    rows = (len(sorted_slots) + PALETTE_SHEET_COLS - 1) // PALETTE_SHEET_COLS
    cells: list[PaletteSheetCell] = []
    for i, (slot, name) in enumerate(sorted_slots):
        cx = i % PALETTE_SHEET_COLS
        cy = i // PALETTE_SHEET_COLS
        cells.append(PaletteSheetCell(
            slot=slot, sti_filename=name,
            cell_x=cx, cell_y=cy,
            px=cx * PALETTE_SHEET_CELL, py=cy * PALETTE_SHEET_CELL,
            w=PALETTE_SHEET_CELL, h=PALETTE_SHEET_CELL,
        ))
    return PaletteSheetMeta(
        tileset=tileset,
        cell=PALETTE_SHEET_CELL,
        cols=PALETTE_SHEET_COLS,
        rows=rows,
        sheet_w=PALETTE_SHEET_COLS * PALETTE_SHEET_CELL,
        sheet_h=rows * PALETTE_SHEET_CELL,
        cells=cells,
        fingerprint=_palette_sheet_fingerprint(xml_path, tileset, slot_map),
    )


def _build_palette_sheet(
    xml_path: Path,
    tileset: int,
    emit: Optional[Callable[[dict], None]] = None,
) -> tuple[bytes, bool]:
    """Bake the palette sprite sheet for one tileset. Returns
    (png_bytes, from_cache).

    When `emit` is provided, calls it with NDJSON-style event dicts at
    bake checkpoints so callers can stream progress to the frontend.
    Mirrors the `_build_atlas` pattern used by /tileset/atlas/build.
    """
    from PIL import Image  # noqa: E402

    def _emit(evt: dict) -> None:
        if emit is None:
            return
        try:
            emit(evt)
        except Exception:  # noqa: BLE001
            pass  # progress reporting must not break the bake

    slot_map = load_tileset_xml(xml_path, tileset)
    fp = _palette_sheet_fingerprint(xml_path, tileset, slot_map)
    cache_dir = _PALETTE_SHEET_CACHE / f"{tileset}_{fp}"
    cache_path = cache_dir / "sheet.png"
    # Debug mode bypass (mirrors _build_atlas) — MERCWIZARD_DEBUG=1
    # forces every bake to run from scratch so the user sees real load
    # times during testing instead of warm-cache 50ms returns.
    _debug = os.environ.get("MERCWIZARD_DEBUG", "").strip() not in ("", "0", "false", "False")
    if cache_path.is_file() and not _debug:
        _emit({"event": "phase", "phase": "cache-hit",
               "label": "Cached palette sheet"})
        png_bytes = cache_path.read_bytes()
        _emit({"event": "done", "from_cache": True, "size": len(png_bytes)})
        return png_bytes, True
    if _debug and cache_path.is_file():
        _emit({"event": "phase", "phase": "debug-bypass",
               "label": "MERCWIZARD_DEBUG=1 — skipping palette sheet cache"})

    _emit({"event": "phase", "phase": "scan-tilesets",
           "label": "Locating tileset STIs"})
    loose, slf = _tileset_paths_for(xml_path)
    cache = StiCache(tileset, loose_dirs=loose, slf_paths=slf)

    sorted_slots = [(s, n) for s, n in sorted(slot_map.items()) if n]
    total_slots = len(sorted_slots)
    rows = (total_slots + PALETTE_SHEET_COLS - 1) // PALETTE_SHEET_COLS
    sheet = Image.new(
        "RGBA",
        (PALETTE_SHEET_COLS * PALETTE_SHEET_CELL, rows * PALETTE_SHEET_CELL),
        (0, 0, 0, 0),
    )
    _emit({"event": "phase", "phase": "bake",
           "label": "Baking sprite sheet",
           "total": total_slots})

    # Parallelize the per-slot STI load + frame-pick + thumbnail.
    # The bottleneck is the SLF read + ETRLE decode + PIL Image
    # construction inside `cache.get(name)`, which is independent
    # per STI. A thread pool with 4 workers cuts a 30-second cold
    # bake on a ~300-slot tileset down to ~8 seconds. User feedback:
    # "the sprite sheet takes FOREVER to load." StiCache appears
    # thread-safe for distinct keys; we never call .get on the same
    # name from multiple threads, and PIL Image construction on
    # distinct buffers doesn't share state. The final PIL `paste`
    # onto the sheet stays serial — Image.paste mutates one shared
    # Image which is NOT thread-safe.
    from concurrent.futures import ThreadPoolExecutor, as_completed

    def _prepare_thumb(idx_name: tuple[int, tuple[int, str]]):
        """Worker: load the STI, pick the first non-empty frame,
        scale to PALETTE_SHEET_CELL. Returns (index_in_sorted, name,
        thumb_or_None). Index is the position in sorted_slots so
        the main thread knows where to paste."""
        i, (_slot, name) = idx_name
        try:
            frames = cache.get(name)
        except Exception:  # noqa: BLE001
            return (i, name, None)
        if not frames:
            return (i, name, None)
        pil = None
        for fr in frames[:6]:
            cand = fr[0]
            if _frame_has_visible_pixels(cand):
                pil = cand
                break
        if pil is None:
            pil = frames[0][0]
        thumb = pil.copy()
        thumb.thumbnail((PALETTE_SHEET_CELL, PALETTE_SHEET_CELL),
                        Image.NEAREST)
        return (i, name, thumb)

    completed = 0
    # 4 workers is the sweet spot on consumer hardware — STI loads
    # are I/O + CPU mix; pushing past 4 hits diminishing returns and
    # can starve the main thread doing the paste.
    with ThreadPoolExecutor(max_workers=4) as ex:
        futures = [
            ex.submit(_prepare_thumb, (i, slot_pair))
            for i, slot_pair in enumerate(sorted_slots)
        ]
        for fut in as_completed(futures):
            try:
                i, name, thumb = fut.result()
            except Exception:  # noqa: BLE001
                completed += 1
                continue
            if thumb is not None:
                cx = (i % PALETTE_SHEET_COLS) * PALETTE_SHEET_CELL
                cy = (i // PALETTE_SHEET_COLS) * PALETTE_SHEET_CELL
                ox = cx + (PALETTE_SHEET_CELL - thumb.size[0]) // 2
                oy = cy + (PALETTE_SHEET_CELL - thumb.size[1]) // 2
                sheet.paste(thumb, (ox, oy), thumb)
            completed += 1
            # Throttle progress emission to ~every 8 completions so the
            # stream doesn't dominate CPU on small tilesets; ALWAYS
            # emit on the last completion so the bar reaches 100%.
            if completed % 8 == 0 or completed == total_slots:
                _emit({"event": "progress",
                       "current": completed, "total": total_slots,
                       "detail": name})

    _emit({"event": "phase", "phase": "encode",
           "label": "Encoding PNG"})
    cache_dir.mkdir(parents=True, exist_ok=True)
    buf = io.BytesIO()
    sheet.save(buf, format="PNG", optimize=False)
    png_bytes = buf.getvalue()
    try:
        cache_path.write_bytes(png_bytes)
    except OSError:
        pass
    _emit({"event": "done", "from_cache": False, "size": len(png_bytes)})
    return png_bytes, False


@router.get("/tileset/palette-sheet")
def palette_sheet(
    xml: str = Query(...),
    tileset: int = Query(...),
):
    """Pre-built sprite sheet of every slot's frame[0] in a uniform
    64×64 grid, used by the asset palette to render thumbnails as
    one image (CSS background-position crops each cell).

    Cached to disk per (xml, tileset, slot-map fingerprint)."""
    _require_renderer()
    xml_path = _validate_path(xml, ".xml")
    png_bytes, from_cache = _build_palette_sheet(xml_path, tileset)
    return Response(
        content=png_bytes,
        media_type="image/png",
        headers={
            "Cache-Control": "max-age=86400",
            "Content-Length": str(len(png_bytes)),
            "X-MapForge-FromCache": "1" if from_cache else "0",
        },
    )


@router.get("/tileset/palette-sheet/build")
def palette_sheet_build(
    xml: str = Query(..., description="Path to Ja2Set.dat.xml"),
    tileset: int = Query(..., description="Tileset index"),
):
    """NDJSON stream of palette-sheet bake progress. Call BEFORE
    GET /tileset/palette-sheet so the subsequent sheet fetch hits the
    disk cache instantly while showing real progress during the slow
    bake. Mirrors /tileset/atlas/build for the renderer atlas.

    Events emitted (NDJSON, one JSON per line):
      {"event": "phase",     "phase": <id>, "label": <human-text>, "total"?: <int>}
      {"event": "progress",  "current": <int>, "total": <int>, "detail": <str>}
      {"event": "done",      "from_cache": <bool>, "size": <int>}
      {"event": "error",     "message": <str>}
    """
    _require_renderer()
    xml_path = _validate_path(xml, ".xml")
    import queue
    import threading

    # Thread + queue pattern. Without this, the StreamingResponse would
    # block on the bake's synchronous PIL work and yield nothing until
    # the bake completes — defeating the purpose. Worker thread emits
    # into a queue; the generator drains the queue and yields each event
    # as bytes for live streaming.
    event_queue: "queue.Queue[Optional[dict]]" = queue.Queue()
    result_holder: dict = {}

    def emit(evt: dict) -> None:
        event_queue.put(evt)

    def worker() -> None:
        try:
            _build_palette_sheet(xml_path, tileset, emit=emit)
        except Exception as e:  # noqa: BLE001
            result_holder["error"] = f"{type(e).__name__}: {e}"
        finally:
            event_queue.put(None)  # sentinel — close stream

    threading.Thread(target=worker, daemon=True).start()

    def gen():
        while True:
            evt = event_queue.get()
            if evt is None:
                if "error" in result_holder:
                    yield (json.dumps({
                        "event": "error",
                        "message": result_holder["error"],
                    }) + "\n").encode("utf-8")
                return
            yield (json.dumps(evt) + "\n").encode("utf-8")

    return StreamingResponse(gen(), media_type="application/x-ndjson",
                             headers={"Cache-Control": "no-store"})


# ─── Edit endpoint (Phase 2) ──────────────────────────────────────────
class EditTileBody(BaseModel):
    dat: str   # filesystem path; SLF URIs are refused (read-only)
    x: int
    y: int
    layer: str  # "land" | "objs" | "shadows" | "structs" | "roofs" | "onroofs"
    op: str     # "replace" | "add" | "remove" | "set_room"
    # replace + remove
    entry_index: Optional[int] = None
    # replace + add
    slot: Optional[int] = None
    sub: Optional[int] = None
    # set_room
    room_id: Optional[int] = None


class EditTileResult(BaseModel):
    ok: bool
    op: str
    before: Optional[list[list[int]]] = None  # [[slot,sub], ...] before
    after: list[list[int]]                     # entries after the edit
    backup_path: Optional[str] = None          # .bak path if one was created
    bytes_written: int


@router.post("/sector/edit-tile", response_model=EditTileResult)
def edit_tile(body: EditTileBody):
    """Apply a single edit to one tile and save the .dat back to disk.

    On the first edit per file, a `<dat>.bak` snapshot of the original
    is created (idempotent — if the .bak already exists we don't
    overwrite it, so successive edits keep the original-pristine copy).
    SLF-bundled sectors are refused; extract to a loose path first.
    """
    _require_renderer()
    if body.dat.startswith(SLF_URI_PREFIX):
        raise HTTPException(
            status_code=400,
            detail={
                "error": "SLF_WRITE_UNSUPPORTED",
                "message": ("Editing SLF-bundled .dat is not supported in "
                            "Phase 2 — copy the file out of the SLF first "
                            "(e.g. into the install's Data-1.13/Maps/ dir "
                            "where loose files shadow SLF entries)."),
            },
        )
    dat_path = _validate_dat_path(body.dat)
    original_bytes = dat_path.read_bytes()
    parsed = parse_dat_full(original_bytes, str(dat_path))
    cols = parsed["cols"]
    rows = parsed["rows"]
    if not (0 <= body.x < cols and 0 <= body.y < rows):
        raise HTTPException(
            status_code=400,
            detail={"error": "OUT_OF_BOUNDS",
                    "message": f"({body.x},{body.y}) outside {cols}x{rows}"},
        )
    gridno = body.y * cols + body.x

    # Snapshot the BEFORE-edit state for the response.
    before: Optional[list[list[int]]] = None
    if body.op == "set_room":
        before = [[parsed["rooms"][gridno]]]
    else:
        if body.layer not in (
            "land", "objs", "shadows", "structs", "roofs", "onroofs"
        ):
            raise HTTPException(
                status_code=400,
                detail={"error": "BAD_LAYER",
                        "message": f"unknown layer {body.layer!r}"},
            )
        before = [list(t) for t in parsed[body.layer][gridno]]

    # Dispatch the op. EditOpError → 400; anything else → 500.
    try:
        if body.op == "replace":
            if body.entry_index is None or body.slot is None or body.sub is None:
                raise HTTPException(400, {"error": "MISSING_FIELDS",
                    "message": "replace needs entry_index, slot, sub"})
            replace_layer_entry(parsed, gridno, body.layer,
                                body.entry_index, body.slot, body.sub)
        elif body.op == "add":
            if body.slot is None or body.sub is None:
                raise HTTPException(400, {"error": "MISSING_FIELDS",
                    "message": "add needs slot, sub"})
            add_layer_entry(parsed, gridno, body.layer, body.slot, body.sub)
        elif body.op == "remove":
            if body.entry_index is None:
                raise HTTPException(400, {"error": "MISSING_FIELDS",
                    "message": "remove needs entry_index"})
            remove_layer_entry(parsed, gridno, body.layer, body.entry_index)
        elif body.op == "set_room":
            if body.room_id is None:
                raise HTTPException(400, {"error": "MISSING_FIELDS",
                    "message": "set_room needs room_id"})
            set_room_id(parsed, gridno, body.room_id)
        else:
            raise HTTPException(400, {"error": "BAD_OP",
                "message": f"unknown op {body.op!r}; "
                           f"valid: replace | add | remove | set_room"})
    except EditOpError as e:
        raise HTTPException(
            status_code=400,
            detail={"error": "INVALID_EDIT", "message": str(e)},
        )

    # Encode the new .dat bytes.
    new_bytes = write_dat_bytes(parsed, original_bytes)

    # Create .bak once per file (preserves the ORIGINAL-pristine state).
    bak_path = dat_path.with_suffix(dat_path.suffix + ".bak")
    backup_created: Optional[str] = None
    if not bak_path.exists():
        try:
            bak_path.write_bytes(original_bytes)
            backup_created = str(bak_path)
        except OSError as e:
            raise HTTPException(
                status_code=500,
                detail={"error": "BACKUP_FAILED",
                        "message": f"could not write {bak_path}: {e}"},
            )

    # Atomic-ish write: temp file + rename. Avoids leaving a half-written
    # .dat if the process is killed mid-write.
    tmp_path = dat_path.with_suffix(dat_path.suffix + ".mwtmp")
    try:
        tmp_path.write_bytes(new_bytes)
        tmp_path.replace(dat_path)
    except OSError as e:
        raise HTTPException(
            status_code=500,
            detail={"error": "WRITE_FAILED",
                    "message": f"could not write {dat_path}: {e}"},
        )

    # Re-parse to compute the after-state for the response.
    after_parsed = parse_dat_full(new_bytes, str(dat_path))
    if body.op == "set_room":
        after = [[after_parsed["rooms"][gridno]]]
    else:
        after = [list(t) for t in after_parsed[body.layer][gridno]]

    return EditTileResult(
        ok=True, op=body.op,
        before=before,
        after=after,
        backup_path=backup_created,
        bytes_written=len(new_bytes),
    )


# ─── Helpers ───────────────────────────────────────────────────────────
def _require_renderer() -> None:
    if not _iso_renderer_available:
        raise HTTPException(
            status_code=503,
            detail={
                "error": "RENDERER_UNAVAILABLE",
                "message": ("Map Forge's rendering engine failed to load. "
                            "See the import error for details."),
                "import_error": _iso_renderer_import_error,
            },
        )


def _validate_path(raw: str, suffix: str | None = None) -> Path:
    p = Path(raw)
    if not p.is_file():
        raise HTTPException(
            status_code=404,
            detail={"error": "FILE_NOT_FOUND", "message": f"{raw} not found"},
        )
    if suffix and p.suffix.lower() != suffix.lower():
        raise HTTPException(
            status_code=400,
            detail={"error": "BAD_SUFFIX",
                    "message": f"Expected {suffix}, got {p.suffix}"},
        )
    return p


def _validate_dat_path(raw: str) -> Path:
    p = _validate_path(raw)
    if p.suffix.lower() != ".dat":
        raise HTTPException(
            status_code=400,
            detail={"error": "BAD_SUFFIX",
                    "message": f"Expected .dat, got {p.suffix}"},
        )
    return p


# ─── Session model (Phase 2A) ──────────────────────────────────────────
# In-memory editing sessions. Each session caches the parsed dict + the
# original .dat bytes so subsequent edits / renders / inspects don't
# re-parse the file on every operation. Two-orders-of-magnitude perf
# win for the paint-stroke / live-preview workflow.
#
# Memory cost per session: ~10-50 MB for a typical 160×160 sector
# (parsed lists + per-tile data). Acceptable for a single-user desktop
# app; we cap to MAX_SESSIONS active.

import threading
import time
import uuid


_MAX_SESSIONS = 16
_SESSION_IDLE_TIMEOUT = 60 * 60  # 1h


class MapForgeSession:
    """One open .dat sector in editing mode. Mutated by edit endpoints,
    rendered + inspected by query endpoints, flushed by save.

    `read_only` is set True for SLF-bundled sectors — the parsed dict
    is still loaded so the client-side renderer works, but the save
    endpoint refuses to write back to the temp-extracted copy (which
    would silently strand edits in %TEMP%, not back into the SLF)."""

    __slots__ = (
        "id", "dat_path", "xml_path", "tileset",
        "parsed", "original_bytes",
        "dirty", "edit_count",
        "created_at", "last_used_at",
        "read_only", "source_uri",
        "baseline_findings",
        "_lock",
    )

    def __init__(self, dat_path: Path, xml_path: Path, tileset: int,
                 read_only: bool = False, source_uri: str = ""):
        self.id = uuid.uuid4().hex[:16]
        self.dat_path = dat_path
        self.xml_path = xml_path
        self.tileset = tileset
        self.original_bytes = dat_path.read_bytes()
        self.parsed = parse_dat_full(self.original_bytes, str(dat_path))
        self.dirty = False
        self.edit_count = 0
        self.created_at = time.time()
        self.last_used_at = self.created_at
        self.read_only = read_only
        # Original URI the client opened with (slf:// or filesystem path).
        # Surfaced in /sessions/{sid} so the UI can show "loaded from
        # SLF" without re-parsing the URL.
        self.source_uri = source_uri or str(dat_path)
        # Validation baseline: {code: count} of findings present in the
        # AS-OPENED file, so /validate can tag findings the user's edits
        # did NOT introduce as `preexisting` (e.g. C6.DAT ships with 40
        # room-ID gaps — a paste should not be blamed for them). A
        # finding counts as new again if its affected-count GREW past
        # the baseline. Pure + cheap (validate_parsed is pure).
        try:
            self.baseline_findings = {
                f.code: (f.count if f.count is not None else len(f.tiles))
                for f in validate_parsed(self.parsed)
            }
        except Exception:  # noqa: BLE001 — baseline is best-effort
            self.baseline_findings = {}
        # Each session has its own lock so concurrent edits from a
        # batch-paint don't trample n_per_tile counts.
        self._lock = threading.Lock()

    def touch(self) -> None:
        self.last_used_at = time.time()


class _SessionStore:
    """Singleton store. Thread-safe; sessions are looked up by id."""

    def __init__(self):
        self._sessions: dict[str, MapForgeSession] = {}
        self._lock = threading.Lock()

    def open(self, dat_path: Path, xml_path: Path, tileset: int,
             read_only: bool = False, source_uri: str = "") -> MapForgeSession:
        sess = MapForgeSession(dat_path, xml_path, tileset,
                               read_only=read_only, source_uri=source_uri)
        with self._lock:
            self._evict_idle_locked()
            if len(self._sessions) >= _MAX_SESSIONS:
                # Evict the oldest CLEAN session by last_used_at. NEVER a
                # dirty one — its unsaved edits live only here, so dropping
                # it silently loses the user's work (R6 trust fix). If every
                # session is dirty, we let the cap be exceeded rather than
                # lose anything.
                clean = [k for k, s in self._sessions.items() if not s.dirty]
                if clean:
                    oldest_id = min(clean,
                                    key=lambda k: self._sessions[k].last_used_at)
                    del self._sessions[oldest_id]
            self._sessions[sess.id] = sess
        return sess

    def get(self, session_id: str) -> MapForgeSession:
        with self._lock:
            sess = self._sessions.get(session_id)
        if sess is None:
            raise HTTPException(
                status_code=404,
                detail={"error": "SESSION_NOT_FOUND",
                        "message": f"No active MapForge session {session_id!r}. "
                                   "Open one with POST /mapforge/sessions."},
            )
        sess.touch()
        return sess

    def close(self, session_id: str) -> bool:
        with self._lock:
            return self._sessions.pop(session_id, None) is not None

    def list_all(self) -> list[MapForgeSession]:
        with self._lock:
            return list(self._sessions.values())

    def _evict_idle_locked(self) -> None:
        # Drop sessions idle past the timeout — but NEVER a dirty one. Its
        # unsaved edits live only in memory here, so evicting on idle would
        # silently lose them (R6 trust fix). Dirty idle sessions persist
        # until saved or explicitly closed.
        now = time.time()
        for sid in list(self._sessions):
            sess = self._sessions[sid]
            if not sess.dirty and now - sess.last_used_at > _SESSION_IDLE_TIMEOUT:
                del self._sessions[sid]


_session_store = _SessionStore()


# ─── Session-aware Pydantic models ─────────────────────────────────────
class OpenSessionBody(BaseModel):
    dat: str
    xml: str
    tileset: int


class SessionInfo(BaseModel):
    session_id: str
    dat_path: str
    xml_path: str
    tileset: int
    rows: int
    cols: int
    dirty: bool
    edit_count: int
    created_at: float
    last_used_at: float
    # True when the source was an SLF URI — the parsed dict and atlas
    # are still served for rendering, but /edits and /save are refused.
    read_only: bool = False
    # Original URI the client opened with (slf://... or filesystem path).
    source_uri: str = ""


class EditOp(BaseModel):
    """A single edit operation. Same shape as EditTileBody minus the
    `dat` (the session already knows which file)."""
    x: int
    y: int
    op: str  # "replace" | "add" | "remove" | "place" | "set_entries" | "set_room" | "set_height"
    layer: Optional[str] = None
    entry_index: Optional[int] = None
    slot: Optional[int] = None
    sub: Optional[int] = None
    room_id: Optional[int] = None
    height: Optional[int] = None
    # For `set_entries`: full entry list to write at (x, y, layer).
    # Each entry is a [slot, sub] pair. Empty list clears the tile.
    # Used by client-side undo to restore a snapshot in one op.
    entries: Optional[list[list[int]]] = None


class ApplyEditsBody(BaseModel):
    edits: list[EditOp]


class ApplyEditsResult(BaseModel):
    applied: int
    session: SessionInfo


class SaveResult(BaseModel):
    session: SessionInfo
    bytes_written: int
    backup_path: Optional[str]


def _session_info(sess: MapForgeSession) -> SessionInfo:
    return SessionInfo(
        session_id=sess.id,
        dat_path=str(sess.dat_path),
        xml_path=str(sess.xml_path),
        tileset=sess.tileset,
        rows=sess.parsed["rows"],
        cols=sess.parsed["cols"],
        dirty=sess.dirty,
        edit_count=sess.edit_count,
        created_at=sess.created_at,
        last_used_at=sess.last_used_at,
        read_only=sess.read_only,
        source_uri=sess.source_uri,
    )


# ─── Pre-flight validation (A4) ────────────────────────────────────────
class ValidationFinding(BaseModel):
    severity: str            # "error" | "warn" | "info"
    code: str
    message: str
    tiles: list[int] = []    # affected gridnos (sampled, capped at 50)
    count: Optional[int] = None
    slot: Optional[int] = None
    # True when the open session's BASELINE (the as-opened file) already
    # carried this finding at the same-or-larger count — i.e. the user's
    # edits did not introduce it. Always False for no-session validation.
    preexisting: bool = False


class ValidationReport(BaseModel):
    dat_path: str
    rows: int
    cols: int
    errors: int
    warnings: int
    infos: int
    jsd_checked: bool
    findings: list[ValidationFinding]


def _validate_tileset_jsds(xml_path: Path, tileset: int) -> list[Finding]:
    """JSD frame-match pre-flight. For each slot in `tileset` whose STI has
    a companion .jsd, confirm the JSD's usNumberOfStructures (or its stored
    count) matches the STI's sub-frame count. A mismatch is the documented
    cause of the worlddef.cpp LoadMapTileset assertion at map load.

    Uses the cached JSD harvest (one SLF walk, then a dict lookup per slot)
    rather than a per-slot SLF scan, so it's cheap after the first call."""
    import struct as _struct
    try:
        slot_map = load_tileset_xml(xml_path, tileset)
    except Exception as e:  # noqa: BLE001
        return [Finding("info", "JSD_CHECK_SKIPPED",
                        f"Could not load the tileset {tileset} slot map "
                        f"({e}); JSD frame-match check skipped.")]
    loose, slf = _tileset_paths_for(xml_path)
    jsd_lookup = _harvest_jsd_lookup(slf, loose, tileset)
    cache = StiCache(tileset, loose_dirs=loose, slf_paths=slf)
    findings: list[Finding] = []
    for slot_idx in sorted(slot_map):
        name = slot_map[slot_idx]
        if not name:
            continue
        stem = name[:-4] if name.lower().endswith(".sti") else name
        entry = jsd_lookup.get((stem + ".jsd").lower())
        if entry is None:
            continue
        data = entry[0]
        if len(data) < 10:
            continue
        try:
            n_struct, n_stored, _ = _struct.unpack("<HHH", data[4:10])
        except _struct.error:
            continue
        try:
            frame_count = len(cache.get(name))
        except Exception:  # noqa: BLE001
            continue
        # n_stored differs from n_struct only for dedup'd JSDs; accept a
        # match against EITHER so we don't cry wolf on those.
        if frame_count and frame_count not in (n_struct, n_stored):
            findings.append(Finding(
                "error", "JSD_FRAME_MISMATCH",
                f"Tileset {tileset} slot {slot_idx} ({name}): the JSD "
                f"declares {n_struct} structure(s) (stored {n_stored}) but "
                f"the STI has {frame_count} sub-frame(s). This mismatch "
                f"asserts at LoadMapTileset when the map loads.",
                slot=slot_idx,
            ))
    return findings


def _to_validation_report(dat_path: str, parsed: dict,
                          findings: list[Finding],
                          jsd_checked: bool) -> ValidationReport:
    return ValidationReport(
        dat_path=dat_path,
        rows=parsed.get("rows", 160),
        cols=parsed.get("cols", 160),
        errors=sum(1 for f in findings if f.severity == "error"),
        warnings=sum(1 for f in findings if f.severity == "warn"),
        infos=sum(1 for f in findings if f.severity == "info"),
        jsd_checked=jsd_checked,
        findings=[ValidationFinding(
            severity=f.severity, code=f.code, message=f.message,
            tiles=f.tiles, count=f.count, slot=f.slot,
        ) for f in findings],
    )


@router.get("/sector/validate", response_model=ValidationReport)
def sector_validate(
    dat: str = Query(..., description="Absolute path to .dat sector or slf:// URI"),
    xml: Optional[str] = Query(None, description="Ja2Set.dat.xml — enables the JSD check"),
    tileset: Optional[int] = Query(None, description="Tileset index — enables the JSD check"),
    check_jsd: bool = Query(True, description="Run the (heavier) JSD frame-match check"),
):
    """Pre-flight validate a .dat: crash traps + playability + (optionally)
    the tileset JSD frame-match check. Read-only — never writes."""
    _require_renderer()
    dat_path = _resolve_dat_path(dat)
    parsed = parse_dat_file(dat_path)
    findings = list(validate_parsed(parsed))
    jsd_checked = False
    if check_jsd and xml and tileset is not None:
        xml_path = _validate_path(xml, ".xml")
        findings.extend(_validate_tileset_jsds(xml_path, tileset))
        jsd_checked = True
    return _to_validation_report(str(dat_path), parsed, findings, jsd_checked)


@router.get("/sessions/{session_id}/validate", response_model=ValidationReport)
def session_validate(
    session_id: str,
    check_jsd: bool = Query(True, description="Run the (heavier) JSD frame-match check"),
):
    """Validate the session's in-memory (uncommitted) state — run this
    before saving to catch problems while they're still cheap to fix.

    Findings already present in the as-opened file (the session's
    baseline) are tagged `preexisting` so the UI can distinguish "your
    edit introduced this" from "this map came that way" — many shipped
    maps carry warn-grade findings natively (C6.DAT: 40 room-ID gaps)."""
    _require_renderer()
    sess = _session_store.get(session_id)
    findings = list(validate_parsed(sess.parsed))
    jsd_checked = False
    if check_jsd:
        findings.extend(_validate_tileset_jsds(sess.xml_path, sess.tileset))
        jsd_checked = True
    report = _to_validation_report(
        str(sess.dat_path), sess.parsed, findings, jsd_checked)
    baseline = getattr(sess, "baseline_findings", None) or {}
    for f in report.findings:
        if f.code in ("JSD_FRAME_MISMATCH", "JSD_CHECK_SKIPPED"):
            # Tileset-level: independent of map edits, so always
            # pre-existing relative to this session.
            f.preexisting = True
        elif f.code in baseline:
            cur = f.count if f.count is not None else len(f.tiles)
            f.preexisting = cur <= baseline[f.code]
    return report


# ─── Radar / minimap STI generation (A3) ───────────────────────────────
_RADAR_BACKUP_DIR = (
    Path(os.environ.get("APPDATA") or Path.home() / ".config")
    / "MercWizard" / "mapforge" / "radar_backups"
)


class RadarResult(BaseModel):
    output_path: str
    bytes_written: int
    width: int
    height: int
    # True when a same-named radar exists in Radarmaps.slf — i.e. we are
    # overriding the bundled vanilla minimap (informational only).
    overrides_bundled: bool
    # base64 PNG of the generated 88x44 image for an in-UI preview.
    preview_png_b64: str


def _radarmaps_slf_has(install_root: Path, name_upper: str) -> bool:
    """Best-effort: does any Radarmaps.slf in the install contain
    `<name>.STI`? Only for the informational `overrides_bundled` flag —
    `resolve_read` can't see SLF members."""
    try:
        from ja2py.fileformats.SlfFS import SlfFS  # noqa: E402
    except ImportError:
        return False
    target = f"{name_upper}.STI".lower()
    for layer in _TILESET_LAYERS:
        slf = install_root / layer / "Radarmaps.slf"
        if not slf.exists():
            continue
        try:
            fs = SlfFS(str(slf))
            for p in fs.walk.files():
                if os.path.basename(p).lower() == target:
                    return True
        except Exception:  # noqa: BLE001
            continue
    return False


@router.post("/sector/radar", response_model=RadarResult)
def sector_radar(
    dat: str = Query(..., description="Absolute path to .dat sector or slf:// URI"),
    xml: str = Query(..., description="Absolute path to Ja2Set.dat.xml"),
    tileset: int = Query(..., description="Tileset index"),
):
    """Generate the sector's 88x44 radar/minimap STI and write it into the
    install's WRITABLE VFS profile — the layer the engine reads first (above
    Radarmaps.slf), exactly where the engine's own editor Radar Map button
    writes. Reads the .dat; writes one derived STI."""
    _require_renderer()
    import base64 as _b64
    import shutil as _shutil
    from mercwizard_core.mapforge_engine.radar import (
        render_radar_image, write_radar_sti, RADAR_W, RADAR_H,
    )
    from mercwizard_core.sti_decode import decode_sti_frame_to_png

    dat_path = _resolve_dat_path(dat)
    xml_path = _validate_path(xml, ".xml")
    install_root = _active_install_root()
    if install_root is None:
        raise HTTPException(400, {"error": "NO_ACTIVE_INSTALL",
            "message": "No active install to write the radar map into."})
    loose_dirs, slf_paths = _tileset_paths_for(xml_path)

    # Sector name from the ORIGINAL arg (the resolved temp path for an
    # slf:// dat would carry a temp name, not the sector name).
    raw = dat.split("!")[-1] if dat.startswith("slf://") else dat
    name = Path(raw).stem.upper()

    # 1. Resolve the engine-read write target: the writable profile, which is
    #    front-of-stack and overrides Radarmaps.slf (correct by construction).
    try:
        layout = parse_vfs_config(install_root)
        out_path = layout.resolve_override_write(f"RADARMAPS/{name}.STI")
    except VfsConfigError as e:
        raise HTTPException(400, {"error": "NO_WRITE_PROFILE", "message": str(e)})

    # 2. Render → 88x44.
    img = render_radar_image(dat_path, xml_path, tileset, loose_dirs, slf_paths)

    # 3. Back up any prior override OUTSIDE mounted dirs, then write atomically.
    if out_path.exists():
        try:
            _RADAR_BACKUP_DIR.mkdir(parents=True, exist_ok=True)
            _shutil.copy2(out_path, _RADAR_BACKUP_DIR / f"{name}.STI.prev")
        except OSError:
            pass
    write_radar_sti(img, out_path)

    # 4. Read-back assertion: the file landed UNDER the engine write profile
    #    AND decodes as a valid STI. (We can't prove non-shadowing via
    #    resolve_read — it ignores SLFs — so correctness is by-construction:
    #    we wrote to the top-of-stack writable profile. This confirms the
    #    write took and is where the engine will look.)
    ewp = layout.engine_write_profile()
    under_profile = False
    if ewp is not None and ewp.profile_root is not None:
        try:
            under_profile = out_path.resolve().is_relative_to(ewp.profile_root.resolve())
        except (OSError, ValueError):
            under_profile = False
    decoded = decode_sti_frame_to_png(out_path.read_bytes(), 0)
    if not under_profile or decoded is None:
        raise HTTPException(500, {"error": "RADAR_WRITE_UNVERIFIED",
            "message": (f"Radar written to {out_path} but the read-back check "
                        "failed (location or decode) — the engine may not read it.")})

    buf = io.BytesIO()
    img.convert("RGB").save(buf, format="PNG")
    return RadarResult(
        output_path=str(out_path),
        bytes_written=out_path.stat().st_size,
        width=RADAR_W, height=RADAR_H,
        overrides_bundled=_radarmaps_slf_has(install_root, name),
        preview_png_b64=_b64.b64encode(buf.getvalue()).decode("ascii"),
    )


def _apply_single_edit(parsed: dict, edit: EditOp, rows: int, cols: int) -> None:
    """Dispatch a single edit op. Raises EditOpError / HTTPException on
    bad input. Mutates `parsed` in place."""
    if not (0 <= edit.x < cols and 0 <= edit.y < rows):
        raise HTTPException(
            status_code=400,
            detail={"error": "OUT_OF_BOUNDS",
                    "message": f"({edit.x},{edit.y}) outside {cols}x{rows}"},
        )
    gridno = edit.y * cols + edit.x
    if edit.op == "set_room":
        if edit.room_id is None:
            raise HTTPException(400, {"error": "MISSING_FIELDS",
                "message": "set_room needs room_id"})
        set_room_id(parsed, gridno, edit.room_id)
        return
    if edit.op == "set_height":
        if edit.height is None:
            raise HTTPException(400, {"error": "MISSING_FIELDS",
                "message": "set_height needs height"})
        set_height(parsed, gridno, edit.height)
        return
    if edit.layer not in ("land", "objs", "shadows", "structs", "roofs", "onroofs"):
        raise HTTPException(400, {"error": "BAD_LAYER",
            "message": f"unknown layer {edit.layer!r}"})
    if edit.op == "replace":
        if edit.entry_index is None or edit.slot is None or edit.sub is None:
            raise HTTPException(400, {"error": "MISSING_FIELDS",
                "message": "replace needs entry_index, slot, sub"})
        replace_layer_entry(parsed, gridno, edit.layer,
                            edit.entry_index, edit.slot, edit.sub)
    elif edit.op == "add":
        if edit.slot is None or edit.sub is None:
            raise HTTPException(400, {"error": "MISSING_FIELDS",
                "message": "add needs slot, sub"})
        add_layer_entry(parsed, gridno, edit.layer, edit.slot, edit.sub)
    elif edit.op == "place":
        # "place" = remove all existing entries on this layer + add one.
        # The standard paint semantic — floor brush replaces the floor,
        # doesn't stack a second one underneath that ends up invisible.
        if edit.slot is None or edit.sub is None:
            raise HTTPException(400, {"error": "MISSING_FIELDS",
                "message": "place needs slot, sub"})
        place_layer_entry(parsed, gridno, edit.layer, edit.slot, edit.sub)
    elif edit.op == "set_entries":
        # Full-replace the entry list for (x, y, layer). Used by undo
        # to restore a snapshot. Empty list clears the tile.
        if edit.entries is None:
            raise HTTPException(400, {"error": "MISSING_FIELDS",
                "message": "set_entries needs entries"})
        try:
            tuples = [(int(e[0]), int(e[1])) for e in edit.entries]
        except (IndexError, TypeError, ValueError):
            raise HTTPException(400, {"error": "BAD_ENTRIES",
                "message": "entries must be a list of [slot, sub] pairs"})
        set_layer_entries(parsed, gridno, edit.layer, tuples)
    elif edit.op == "remove":
        if edit.entry_index is None:
            raise HTTPException(400, {"error": "MISSING_FIELDS",
                "message": "remove needs entry_index"})
        remove_layer_entry(parsed, gridno, edit.layer, edit.entry_index)
    else:
        raise HTTPException(400, {"error": "BAD_OP",
            "message": f"unknown op {edit.op!r}; expected "
                       f"replace/add/place/set_entries/remove/set_room/"
                       f"set_height"})


# ─── SLF → loose extraction (turning a read-only SLF map into an
#       editable loose copy) ─────────────────────────────────────────
class ExtractSlfMapBody(BaseModel):
    slf_uri: str   # the `slf://<archive>!<internal>` URI


class ExtractSlfMapResult(BaseModel):
    loose_path: str
    install_root: str
    overwrote_existing: bool
    # Which VFS profile + layer the loose copy was written into. Lets
    # the frontend confirm to the user that the destination matches
    # what the running engine actually reads. Field added in the
    # post-H4 fix where MapForge was writing to Data-1.13/Maps blindly
    # while the install's running VFS config was Vanilla — game
    # couldn't see the edit. Now we route via the install's actual VFS.
    target_profile: Optional[str] = None
    target_layer_path: Optional[str] = None
    target_layer_source: str = ""  # "vfs_config" | "heuristic-fallback"


class ExtractSlfPreview(BaseModel):
    """What `extract-slf-to-loose` WOULD do, without actually doing it.

    Frontend uses this to show the user the destination path + VFS
    profile BEFORE they click the extract button. Bug-doc #61 — the
    user used to find out after the fact that MapForge wrote to a
    layer the engine doesn't read.
    """
    proposed_loose_path: str
    target_profile: Optional[str] = None
    target_layer_path: Optional[str] = None
    target_layer_source: str = ""
    already_exists: bool = False
    install_root: str


@router.get("/sector/extract-slf-preview", response_model=ExtractSlfPreview)
def extract_slf_preview(slf_uri: str = Query(...)):
    """Return where `extract-slf-to-loose` WOULD write, without writing.

    Same VFS resolution as the real extract endpoint — keeps them in
    lockstep so what the preview shows is what the user actually gets.
    """
    _require_renderer()
    if not slf_uri.startswith(SLF_URI_PREFIX):
        raise HTTPException(400, {"error": "NOT_SLF_URI"})
    try:
        slf_path, internal = _parse_slf_uri(slf_uri)
    except ValueError as e:
        raise HTTPException(400, {"error": "BAD_SLF_URI", "message": str(e)})
    state = get_state()
    info = state.active()
    if info is None:
        raise HTTPException(400, {"error": "NO_ACTIVE_INSTALL"})
    install_root = Path(info.path)
    out_name = os.path.basename(internal) or "extracted.dat"
    out_path: Optional[Path] = None
    target_profile: Optional[str] = None
    target_layer_source = "vfs_config"
    try:
        from mercwizard_core.install_context import make_install_context
        ctx = make_install_context(install_root)
        out_path = ctx.layout.resolve_write(f"Maps/{out_name}")
        writable = ctx.layout.writable_profile()
        if writable is not None:
            target_profile = writable.name
    except Exception:  # noqa: BLE001
        target_layer_source = "heuristic-fallback"
    if out_path is None:
        for layer in ("Data-1.13", "Data-DMK", "Data"):
            cand = install_root / layer
            if cand.is_dir():
                target_profile = layer
                out_path = cand / "Maps" / out_name
                break
    if out_path is None:
        raise HTTPException(500, {"error": "NO_DATA_LAYER"})
    return ExtractSlfPreview(
        proposed_loose_path=str(out_path),
        target_profile=target_profile,
        target_layer_path=str(out_path.parent),
        target_layer_source=target_layer_source,
        already_exists=out_path.exists(),
        install_root=str(install_root),
    )


@router.post("/sector/extract-slf-to-loose", response_model=ExtractSlfMapResult)
def extract_slf_to_loose(body: ExtractSlfMapBody):
    """Copy an SLF-bundled `.dat` sector into the active install's
    loose Maps directory. After this, the engine's VFS prefers the
    loose copy over the SLF entry (loose shadows SLF), so the user
    can edit + save normally — no repacking the SLF needed.

    Writes to `<install>/Data-1.13/Maps/<name>.dat`. If that path
    already exists, refuses (the user should rename or delete first
    to avoid silently clobbering their work). Creates the Maps dir
    if missing.

    The install scan's loose listing picks up the new file on its
    next refresh; the SLF listing stays cached (its fingerprint is
    keyed on Maps.slf mtime, which we don't touch)."""
    _require_renderer()
    if not body.slf_uri.startswith(SLF_URI_PREFIX):
        raise HTTPException(400, {"error": "NOT_SLF_URI",
            "message": "slf_uri must start with 'slf://'"})
    state = get_state()
    info = state.active()
    if info is None:
        raise HTTPException(400, {"error": "NO_ACTIVE_INSTALL"})

    try:
        slf_path, internal = _parse_slf_uri(body.slf_uri)
    except ValueError as e:
        raise HTTPException(400, {"error": "BAD_SLF_URI",
            "message": str(e)})
    if not slf_path.is_file():
        raise HTTPException(404, {"error": "SLF_NOT_FOUND",
            "message": f"{slf_path} not found"})

    # Pick the destination layer.
    #
    # Old behavior: hardcoded heuristic "Data-1.13/Maps → Data-DMK/Maps
    # → Data/Maps based on dir existence." This broke installs running
    # under Vanilla VFS (e.g. a reference install): the heuristic picked
    # Data-1.13/Maps but the engine never reads that layer when VFS
    # is set to vfs_config.JA2Vanilla.ini, so user paint edits were
    # invisible in-game. (Root cause of the 2026-05-22 H4 saga.)
    #
    # New behavior: route the destination through the install's active
    # VFS layout via `make_install_context().layout.resolve_write()`.
    # That walks the install's vfs_config.<active>.ini profile chain
    # and returns the path the engine WILL actually read from. Falls
    # back to the old heuristic if context construction fails — never
    # refuses the extract just because VFS introspection broke.
    install_root = Path(info.path)
    out_name = os.path.basename(internal) or "extracted.dat"
    out_path: Optional[Path] = None
    target_profile: Optional[str] = None
    target_layer_source = "vfs_config"
    try:
        from mercwizard_core.install_context import make_install_context
        ctx = make_install_context(install_root)
        # resolve_write returns the path the engine sees as "Maps/<file>"
        # via the highest-priority WRITABLE directory profile in the
        # active VFS config. For Vanilla VFS = Data/Maps/<file>; for
        # JA2113 VFS = Data-1.13/Maps/<file>. Exactly what we need.
        out_path = ctx.layout.resolve_write(f"Maps/{out_name}")
        # Tag the profile we landed in for the UI confirmation.
        writable = ctx.layout.writable_profile()
        if writable is not None:
            target_profile = writable.name
    except Exception:  # noqa: BLE001
        target_layer_source = "heuristic-fallback"
        out_path = None
    if out_path is None:
        # Fallback to the legacy heuristic.
        target_layer: Optional[Path] = None
        for layer in ("Data-1.13", "Data-DMK", "Data"):
            cand = install_root / layer
            if cand.is_dir():
                target_layer = cand
                target_profile = layer
                break
        if target_layer is None:
            raise HTTPException(500, {"error": "NO_DATA_LAYER",
                "message": f"no Data*/ dir under {install_root}"})
        out_path = target_layer / "Maps" / out_name

    out_path.parent.mkdir(parents=True, exist_ok=True)
    if out_path.exists():
        raise HTTPException(409, {"error": "LOOSE_EXISTS",
            "message": f"{out_path} already exists. Rename or delete it "
                       "first if you want to re-extract."})

    try:
        from ja2py.fileformats.SlfFS import SlfFS  # noqa
    except ImportError:
        raise HTTPException(500, {"error": "SLF_LIB_MISSING"})
    try:
        fs = SlfFS(str(slf_path))
        data = fs.readbytes(internal)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(500, {"error": "SLF_READ_FAILED",
            "message": f"{type(e).__name__}: {e}"})
    out_path.write_bytes(data)

    # Bust the install scan caches so the new loose file shows up
    # on the next list. The fingerprint logic does this automatically
    # via dir-mtime, but evicting the cached payload is belt-and-
    # suspenders.
    try:
        cache_path = _install_maps_cache_path(info.id)
        if cache_path.is_file():
            cache_path.unlink()
    except OSError:
        pass

    return ExtractSlfMapResult(
        loose_path=str(out_path),
        install_root=str(install_root),
        overwrote_existing=False,
        target_profile=target_profile,
        target_layer_path=str(out_path.parent),
        target_layer_source=target_layer_source,
    )


# ─── Session endpoints ─────────────────────────────────────────────────
@router.post("/sessions", response_model=SessionInfo)
def open_session(body: OpenSessionBody):
    """Open an editing session for a .dat sector. The parsed dict +
    original bytes are held in memory; subsequent edits + renders
    reuse them (no re-parse on every operation).

    `tileset` accepts 0 as "auto-detect from .dat header" — useful when
    the client doesn't know the tileset yet (lets the frontend open
    the session IN PARALLEL with fetching sector_info instead of
    waiting on it, which doubled page-load time).

    SLF-bundled sectors open in READ-ONLY mode — the parsed dict + atlas
    are still served so the client-side renderer works, but POST /edits
    and POST /save are refused for those sessions. To edit an SLF map,
    drop a loose copy into Data-1.13/Maps/ (loose shadows SLF) first.
    """
    _require_renderer()
    # _resolve_dat_path handles both filesystem paths and slf:// URIs.
    # For SLF URIs it extracts to the temp cache and returns the cached
    # path; the session then operates on that copy. We track the
    # original URI on the session so the response carries it back.
    is_slf = body.dat.startswith(SLF_URI_PREFIX)
    dat_path = _resolve_dat_path(body.dat)
    xml_path = _validate_path(body.xml, ".xml")
    sess = _session_store.open(
        dat_path, xml_path, body.tileset,
        read_only=is_slf,
        source_uri=body.dat,
    )
    # Auto-detect tileset from the .dat header if the client sent 0.
    # parsed["tileset"] is the value stored in the file at save time.
    if body.tileset == 0 and sess.parsed.get("tileset", 0) != 0:
        sess.tileset = sess.parsed["tileset"]
    return _session_info(sess)


@router.get("/sessions", response_model=list[SessionInfo])
def list_sessions():
    """List all active editing sessions (debug + UI cleanup)."""
    return [_session_info(s) for s in _session_store.list_all()]


@router.get("/sessions/{session_id}", response_model=SessionInfo)
def get_session(session_id: str):
    sess = _session_store.get(session_id)
    return _session_info(sess)


@router.delete("/sessions/{session_id}")
def close_session(session_id: str):
    """Discard the session and free its memory. Unsaved edits are LOST."""
    ok = _session_store.close(session_id)
    if not ok:
        raise HTTPException(404, {"error": "SESSION_NOT_FOUND",
            "message": f"No session {session_id!r}"})
    return {"closed": session_id}


# Rolling pre-save backups live OUTSIDE the install (the in-dir one-shot
# .bak only preserves the pristine original; a second bad save would
# otherwise overwrite the canonical .dat with no fresh recovery point).
_DAT_BACKUP_DIR = (
    Path(os.environ.get("APPDATA") or Path.home() / ".config")
    / "MercWizard" / "mapforge" / "dat_backups"
)

_EDIT_LAYER_PLURALS = ("land", "objs", "structs", "shadows", "roofs", "onroofs")


def _snapshot_tiles(parsed: dict, gridnos: list[int]) -> dict:
    """Capture full per-tile state (6 layer entry lists + their count
    nibbles + room + height) for `gridnos`, so a failed edit batch can be
    rolled back tile-by-tile — O(touched tiles), NOT a full-map deepcopy
    (which would regress the interactive paint hot path)."""
    npt = parsed.get("n_per_tile") or {}
    rooms = parsed.get("rooms")
    heights = parsed.get("heights")
    snap: dict[int, dict] = {}
    for g in gridnos:
        snap[g] = {
            "layers": {pl: list(parsed[pl][g]) for pl in _EDIT_LAYER_PLURALS},
            "counts": {ck: npt[ck][g] for ck in npt},
            "room": rooms[g] if rooms is not None else None,
            "height": (heights[g] if heights is not None and g < len(heights)
                       else None),
        }
    return snap


def _restore_tiles(parsed: dict, snap: dict) -> None:
    """Undo a partial edit batch from a `_snapshot_tiles` capture."""
    npt = parsed.get("n_per_tile") or {}
    rooms = parsed.get("rooms")
    heights = parsed.get("heights")
    for g, s in snap.items():
        for pl, entries in s["layers"].items():
            parsed[pl][g] = list(entries)
        for ck, c in s["counts"].items():
            npt[ck][g] = c
        if s["room"] is not None and rooms is not None:
            rooms[g] = s["room"]
        if s["height"] is not None and heights is not None and g < len(heights):
            heights[g] = s["height"]


@router.put("/sessions/{session_id}/edits", response_model=ApplyEditsResult)
def apply_edits(session_id: str, body: ApplyEditsBody):
    """Apply a batch of edits to the session's in-memory parsed dict.
    Marks the session dirty; no disk write until POST /save.

    Batching matters: a paint stroke that touches 30 tiles is ONE
    round-trip + ONE re-render, not 30."""
    _require_renderer()
    sess = _session_store.get(session_id)
    if sess.read_only:
        raise HTTPException(
            status_code=400,
            detail={"error": "SESSION_READ_ONLY",
                    "message": ("This session is read-only (loaded from "
                                "an SLF archive). Drop a loose copy into "
                                "Data-1.13/Maps/ to enable editing.")},
        )
    rows = sess.parsed["rows"]
    cols = sess.parsed["cols"]
    applied = 0
    with sess._lock:
        # Transactional: snapshot every touched tile BEFORE applying, so a
        # mid-batch failure rolls the WHOLE batch back — no half-applied
        # paste/paint left in the live session. `_apply_single_edit` raises
        # EditOpError (15-cap, etc.) AND HTTPException (OOB / BAD_LAYER /
        # BAD_ENTRIES); both must roll back. Snapshot is O(touched tiles).
        world_max = rows * cols
        touched: list[int] = []
        seen: set[int] = set()
        for edit in body.edits:
            g = edit.y * cols + edit.x
            if 0 <= g < world_max and g not in seen:
                seen.add(g)
                touched.append(g)
        snap = _snapshot_tiles(sess.parsed, touched)
        # The edit ops also mutate the map-global `counts` totals dict;
        # snapshot it so a rollback restores it too (defense-in-depth —
        # today only `sector_info` reads it, and it re-parses off disk).
        counts_before = dict(sess.parsed.get("counts") or {})

        def _rollback() -> None:
            _restore_tiles(sess.parsed, snap)
            c = sess.parsed.get("counts")
            if c is not None:
                c.clear()
                c.update(counts_before)

        try:
            for edit in body.edits:
                _apply_single_edit(sess.parsed, edit, rows, cols)
                applied += 1
        except EditOpError as e:
            _rollback()
            raise HTTPException(400, {"error": "EDIT_OP_ERROR",
                "message": str(e), "applied_before_error": applied})
        except Exception:
            _rollback()
            raise
        sess.edit_count += applied
        # `or`: an empty/no-op batch must never reset a dirty session to
        # clean (the UI would then refuse to save real earlier edits).
        sess.dirty = sess.dirty or applied > 0
    return ApplyEditsResult(applied=applied, session=_session_info(sess))


def _session_backup_dir(dat_path: Path) -> Path:
    """Per-map backup folder OUTSIDE the install. Keyed on stem + a hash
    of the full path so `A9.dat` from two different installs can't
    interleave their backups in one folder."""
    import hashlib as _hashlib
    tag = _hashlib.sha1(str(dat_path).encode("utf-8", "replace")).hexdigest()[:8]
    return _DAT_BACKUP_DIR / f"{dat_path.stem}_{tag}"


@router.post("/sessions/{session_id}/save", response_model=SaveResult)
def save_session(session_id: str):
    """Flush the session's in-memory state to disk. Keeps a one-shot
    pristine backup + rolling pre-save backups, all OUTSIDE the install
    (never inside `Maps/` — the in-game editor's load dialog enumerates
    `MAPS/*` with no extension filter, so a `.dat.bak` next to the live
    map shows up as a loadable map and invites editing a stale copy)."""
    _require_renderer()
    sess = _session_store.get(session_id)
    if sess.read_only:
        raise HTTPException(
            status_code=400,
            detail={"error": "SESSION_READ_ONLY",
                    "message": ("Cannot save a read-only session (SLF-bundled "
                                "source). The temp-extracted copy at "
                                f"{sess.dat_path} would not feed back into "
                                "the SLF archive. Drop a loose copy of "
                                "this sector into Data-1.13/Maps/ first.")},
        )
    backup_dir = _session_backup_dir(sess.dat_path)
    # First-save pristine backup. Don't clobber an existing one so
    # multiple save cycles keep the original as first opened.
    backup_path = backup_dir / "pristine_original.dat"
    backup_str: Optional[str] = None
    if not backup_path.exists():
        try:
            backup_dir.mkdir(parents=True, exist_ok=True)
            backup_path.write_bytes(sess.original_bytes)
            backup_str = str(backup_path)
        except OSError as e:
            raise HTTPException(500, {"error": "BACKUP_FAILED",
                "message": f"{type(e).__name__}: {e}"})
    else:
        backup_str = str(backup_path)
    # Rolling pre-save backup: copy the CURRENT on-disk .dat (the version
    # about to be overwritten) to a timestamped file OUTSIDE the install,
    # so EVERY save is recoverable — not just the pristine original.
    # Best-effort; never block the save on it.
    if sess.dat_path.exists():
        try:
            import shutil as _shutil
            stamp = time.strftime("%Y%m%d_%H%M%S")
            backup_dir.mkdir(parents=True, exist_ok=True)
            # uuid suffix so two saves in the same wall-clock second can't
            # collide and silently overwrite the earlier rolling backup.
            _shutil.copy2(sess.dat_path,
                          backup_dir / f"{stamp}_{uuid.uuid4().hex[:6]}.dat")
        except OSError:
            pass  # disk full / permission — the save itself still proceeds
    import logging as _logging
    _log = _logging.getLogger("mapforge.save")
    # Hold the session lock from the consistency check through the state
    # update: `apply_edits` and generator runs mutate `parsed` under this
    # lock (a generator can hold it for seconds), and an unlocked save
    # racing them could serialize a mid-mutation state to disk — or mark
    # in-flight edits clean via `dirty = False` and silently drop them.
    with sess._lock:
        # Pre-write consistency check. If the parsed dict's per-tile layer
        # counts disagree with the actual entry list lengths, the writer
        # will produce a file the engine can't load (count nibbles mislead
        # the file reader, MAPINFO ends up read from the wrong offset, and
        # the engine asserts "Map is less than minimum supported version").
        # A user hit this 2026-05-25 on a saved C6 — root cause not yet
        # localised; this validator surfaces it AT the save point so the
        # bad data never reaches disk + we get diagnostics for the repro.
        desync = _validate_parsed_consistency(sess.parsed)
        if desync is not None:
            _log.error("SAVE REFUSED — internal state inconsistent: %s", desync)
            raise HTTPException(500, {
                "error": "PARSED_STATE_CORRUPT",
                "message": (
                    "MercForge's in-memory map state is inconsistent — saving "
                    "would produce a .dat the game can't load. The bad save is "
                    "BLOCKED. Reopen the sector to discard the corrupt state. "
                    f"Specific desync: {desync}"
                ),
            })
        # Write the new .dat ATOMICALLY (tmp + replace) so a crash mid-write
        # can't truncate the canonical .dat.
        try:
            new_bytes = write_dat_bytes(sess.parsed, sess.original_bytes)
            tmp_path = sess.dat_path.with_suffix(sess.dat_path.suffix + ".mwtmp")
            tmp_path.write_bytes(new_bytes)
            tmp_path.replace(sess.dat_path)
        except Exception as e:  # noqa: BLE001
            raise HTTPException(500, {"error": "WRITE_FAILED",
                "message": f"{type(e).__name__}: {e}"})
        # After save: the on-disk file IS the new baseline. Update
        # original_bytes so subsequent saves diff against it (and so the
        # backup logic doesn't re-back-up the freshly-saved version).
        sess.original_bytes = new_bytes
        sess.dirty = False
    return SaveResult(
        session=_session_info(sess),
        bytes_written=len(new_bytes),
        backup_path=backup_str,
    )


# ─── New sector + Save-a-copy-as (R6 "create / clone sectors") ─────────
class NewSectorBody(BaseModel):
    """Create a fresh empty .dat at `dat_path`. `tileset` is written into
    the header (and detected on open). `overwrite` must be True to replace
    an existing file — the default refuses, so an accidental path collision
    can't clobber a real map."""
    dat_path: str
    tileset: int
    rows: int = 160
    cols: int = 160
    overwrite: bool = False


class NewSectorResult(BaseModel):
    dat_path: str
    tileset: int
    rows: int
    cols: int
    bytes_written: int


@router.post("/new-sector", response_model=NewSectorResult)
def new_sector(body: NewSectorBody):
    """Emit a minimal valid empty 160×160 sector .dat at `dat_path`.

    Every tile gets a single FIRSTTEXTURE ground entry; all other layers
    are empty, room 0, height 0, flags 0 (no appendix beyond the 32-byte
    MAPCREATE tail). The bytes round-trip through the editor's own parser
    (build_empty_dat_bytes is pinned byte-exact by the save tests) so the
    sector opens cleanly. The caller typically follows this with POST
    /mapforge/sessions to start editing it.

    Engine-validity note: the tail's edge/center gridnos are 0. That's the
    correct freshly-created state — the in-game editor recomputes scroll
    bounds + entry points on its first save; the bytes load fine."""
    _require_renderer()
    p = Path(body.dat_path)
    if p.suffix.lower() != ".dat":
        raise HTTPException(400, {"error": "BAD_SUFFIX",
            "message": f"Expected .dat, got {p.suffix or '(none)'}"})
    if p.exists() and not body.overwrite:
        raise HTTPException(409, {"error": "FILE_EXISTS",
            "message": (f"{p} already exists. Pass overwrite=true to replace "
                        "it (this destroys the existing map).")})
    if body.rows <= 0 or body.cols <= 0 or body.rows > 1024 or body.cols > 1024:
        raise HTTPException(400, {"error": "BAD_DIMENSIONS",
            "message": f"implausible dimensions {body.rows}x{body.cols}"})
    try:
        data = build_empty_dat_bytes(
            tileset=body.tileset, rows=body.rows, cols=body.cols)
    except ValueError as e:
        raise HTTPException(400, {"error": "BAD_PARAMS", "message": str(e)})
    # Atomic write (tmp + replace) so a crash mid-write can't leave a
    # truncated .dat behind. Create the parent dir if it doesn't exist yet.
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = p.with_suffix(p.suffix + ".mwtmp")
        tmp_path.write_bytes(data)
        tmp_path.replace(p)
    except OSError as e:
        raise HTTPException(500, {"error": "WRITE_FAILED",
            "message": f"{type(e).__name__}: {e}"})
    return NewSectorResult(
        dat_path=str(p),
        tileset=body.tileset,
        rows=body.rows,
        cols=body.cols,
        bytes_written=len(data),
    )


class SaveCopyAsBody(BaseModel):
    """Write the session's CURRENT state to a NEW .dat path. The original
    file the session was opened from is never touched. `overwrite` must be
    True to replace an existing destination."""
    dat_path: str
    overwrite: bool = False


class SaveCopyAsResult(BaseModel):
    dat_path: str
    bytes_written: int


@router.post("/sessions/{session_id}/save-copy-as", response_model=SaveCopyAsResult)
def save_copy_as(session_id: str, body: SaveCopyAsBody):
    """Write the session's current in-memory state to a NEW .dat path
    WITHOUT touching the original file or re-baselining the session.

    Unlike /save this never overwrites `sess.dat_path` and never resets
    `sess.dirty` — it's a snapshot-to-a-copy. The same pre-write
    consistency guard runs so a corrupt state can't reach disk. Read-only
    (SLF-bundled) sessions ARE allowed here, since we write to a brand-new
    loose path the user chose, not back into the archive."""
    _require_renderer()
    sess = _session_store.get(session_id)
    dest = Path(body.dat_path)
    if dest.suffix.lower() != ".dat":
        raise HTTPException(400, {"error": "BAD_SUFFIX",
            "message": f"Expected .dat, got {dest.suffix or '(none)'}"})
    # Refuse to clobber the session's OWN source (that's what /save is for —
    # /save keeps backups; this path doesn't) or any existing file unless
    # the caller explicitly confirmed.
    try:
        same_as_source = dest.resolve() == sess.dat_path.resolve()
    except OSError:
        same_as_source = str(dest) == str(sess.dat_path)
    if same_as_source:
        raise HTTPException(409, {"error": "SAME_AS_SOURCE",
            "message": ("Destination is the session's own source file. Use "
                        "the normal Save (which keeps backups) instead.")})
    if dest.exists() and not body.overwrite:
        raise HTTPException(409, {"error": "FILE_EXISTS",
            "message": (f"{dest} already exists. Pass overwrite=true to "
                        "replace it.")})
    with sess._lock:
        desync = _validate_parsed_consistency(sess.parsed)
        if desync is not None:
            raise HTTPException(500, {
                "error": "PARSED_STATE_CORRUPT",
                "message": (
                    "MercForge's in-memory map state is inconsistent — saving "
                    "would produce a .dat the game can't load. The copy is "
                    f"BLOCKED. Specific desync: {desync}"),
            })
        try:
            new_bytes = write_dat_bytes(sess.parsed, sess.original_bytes)
            dest.parent.mkdir(parents=True, exist_ok=True)
            tmp_path = dest.with_suffix(dest.suffix + ".mwtmp")
            tmp_path.write_bytes(new_bytes)
            tmp_path.replace(dest)
        except Exception as e:  # noqa: BLE001
            raise HTTPException(500, {"error": "WRITE_FAILED",
                "message": f"{type(e).__name__}: {e}"})
    return SaveCopyAsResult(dat_path=str(dest), bytes_written=len(new_bytes))


# ──────────────────────────────────────────────────────────────────────────
#  Generators — first-class compiled subsystem (see mercwizard_core/mapforge/generators.py)
# ──────────────────────────────────────────────────────────────────────────


class GeneratorParamSchema(BaseModel):
    name: str
    type: str
    default: Any
    description: str = ""
    min: Optional[float] = None
    max: Optional[float] = None


class GeneratorInfo(BaseModel):
    name: str
    label: str
    description: str
    params: list[GeneratorParamSchema] = []


class RunGeneratorBody(BaseModel):
    """Params for one generator invocation. Keys are generator-defined;
    the route validates against the registered Param schema before
    invoking iter_ops.

    `dry_run=True` streams the SAME op events without applying anything
    — the session is untouched (no lock-held mutation, no dirty, no
    edit_count). The frontend uses it to ghost a generator's full
    result on the canvas before the user commits (live preview)."""
    params: dict[str, Any] = {}
    dry_run: bool = False


@router.get("/generators", response_model=list[GeneratorInfo])
def list_generators():
    """List every built-in generator with its param schema. The
    Generators panel UI binds form widgets to these; the console
    binds tab-completion to them.
    """
    from mercwizard_core.mapforge.generators import list_all as list_generators_all
    return [GeneratorInfo(**g.to_dict()) for g in list_generators_all()]


class CorpusCoverage(BaseModel):
    """Coverage of the distilled generator corpus — which (source, biome)
    cells carry map/building data. Drives the Generators panel's
    corpus_source + biome dropdowns and the gray-out of empty cells."""
    available: bool
    sources: list[str] = []
    biomes: list[str] = []
    layers: list[str] = []
    source_installs: dict[str, str] = {}
    coverage: dict[str, Any] = {}


@router.get("/corpus/coverage", response_model=CorpusCoverage)
def corpus_coverage():
    """Report the distilled corpus's per-(source, biome) coverage so the UI
    can offer corpus_source + biome as real choices and warn on empty cells.
    Degrades to available=false when the corpus JSON isn't shipped."""
    from mercwizard_core.mapforge import corpus as gc
    return CorpusCoverage(
        available=gc.available(),
        sources=gc.list_sources(),
        biomes=gc.list_biomes(),
        layers=gc.list_layers(),
        source_installs=gc.source_installs(),
        coverage=gc.coverage() or {},
    )


class BuildingCatalogEntry(BaseModel):
    """One pickable building 'kind' for the StarCraft-style placement UI —
    a (corpus_source, biome) cell of the distilled corpus with building
    data. The frontend renders the wall/roof (slot, sub) pair as a card
    thumbnail; width/height are user-chosen within the suggested range and
    everything else (per-position subframes, door edge) stays corpus-driven
    inside BuildingStampGenerator."""
    id: str                  # "source:biome" — stable key
    label: str               # humanized, e.g. "Urban — Combined corpus"
    corpus_source: str
    biome: str
    wall_slot: int
    wall_sub: int            # dominant wall sub — thumbnail representative
    roof_slot: int
    roof_sub: int            # dominant roof sub — thumbnail representative
    has_door: bool
    n_buildings: int         # how many real buildings back this cell
    min_w: int
    max_w: int
    min_h: int
    max_h: int
    default_w: int
    default_h: int


_BUILDING_SOURCE_LABEL = {
    "stock": "Stock corpus",
    "redux": "Redux corpus",
    "combined": "Combined corpus",
}

# Fallback footprint range when a cell ships no size histograms.
_BUILDING_FALLBACK_RANGE = {"min_w": 4, "max_w": 12, "min_h": 4, "max_h": 10}


@router.get("/buildings", response_model=list[BuildingCatalogEntry])
def list_buildings():
    """Catalog every (corpus_source, biome) cell with building data — the
    'building kinds' menu for the visual placement flow. Cheap: reads the
    cached corpus JSON only (no STI decoding; the frontend renders
    thumbnails from its own atlas). Empty list when the corpus isn't
    shipped — the UI hides the section."""
    from mercwizard_core.mapforge import corpus as gc

    out: list[BuildingCatalogEntry] = []
    for source, biome in gc.list_building_cells():
        table = gc.get_building_table(source, biome)
        if not table:
            continue
        wall_slot = gc.building_dominant_slot(table, 36, 39) or 36
        roof_slot = gc.building_dominant_slot(table, 64, 67, kind="roofs") or 64
        sizes = gc.building_size_range(table) or _BUILDING_FALLBACK_RANGE
        # BuildingStampGenerator needs ≥3×3; its width/height params cap at 40.
        min_w = max(3, int(sizes["min_w"]))
        max_w = min(40, max(min_w, int(sizes["max_w"])))
        min_h = max(3, int(sizes["min_h"]))
        max_h = min(40, max(min_h, int(sizes["max_h"])))
        out.append(BuildingCatalogEntry(
            id=f"{source}:{biome}",
            label=f"{biome.title()} — {_BUILDING_SOURCE_LABEL.get(source, source.title())}",
            corpus_source=source,
            biome=biome,
            wall_slot=wall_slot,
            wall_sub=gc.building_dominant_sub(table, wall_slot) or 1,
            roof_slot=roof_slot,
            roof_sub=gc.building_dominant_sub(table, roof_slot, kind="roofs") or 1,
            has_door=bool(gc.building_doors(table).get("by_slot")),
            n_buildings=int(table.get("n_buildings") or 0),
            min_w=min_w, max_w=max_w, min_h=min_h, max_h=max_h,
            # 7×6 is the generator's long-standing default footprint;
            # clamp into the cell's empirical range.
            default_w=min(max(7, min_w), max_w),
            default_h=min(max(6, min_h), max_h),
        ))
    return out


# ─── Canon building library ─────────────────────────────────────────────
# Verbatim building grafts extracted from the install's real maps —
# the primary building-placement path (replaces the procedural stamp
# as the headline flow; the generator stays available in the generic
# dropdown). Built lazily on first request per (install, tileset) and
# cached under %APPDATA%/MercWizard/mapforge/building_library/ keyed on
# a fingerprint of the Maps sources, so map edits invalidate it.

_BUILDING_LIB_CACHE_DIR = (
    Path(os.environ.get("APPDATA") or Path.home() / ".config")
    / "MercWizard" / "mapforge" / "building_library"
)

# One build at a time per process — a cold build can take tens of
# seconds; concurrent first requests must not duplicate the work.
import threading as _bl_threading
_building_lib_lock = _bl_threading.Lock()


@router.get("/building-library")
def building_library(
    xml: str = Query(..., description="Absolute path to Ja2Set.dat.xml"),
    tileset: int = Query(..., description="Tileset index"),
    rebuild: bool = Query(False, description="Force a fresh scan"),
):
    """The canon building library for one (install, tileset): every
    building found in every map of that tileset in the install, as
    verbatim layer grafts + thumbnails + context labels.

    First call per (install, tileset) scans + renders (slow — up to
    ~60s on a big install); subsequent calls return the cached JSON
    instantly until any map in the install changes."""
    _require_renderer()
    if not isinstance(rebuild, bool):
        # Direct (non-HTTP) calls receive the truthy Query(...) sentinel
        # as the default — normalize so tests can call this function.
        rebuild = False
    xml_path = _validate_path(xml, ".xml")
    # <install>/<layer>/Ja2Set.dat.xml → grandparent == install root.
    install_root = xml_path.resolve().parent.parent
    loose_dirs, slf_paths = _tileset_paths_for(xml_path)
    from mercwizard_core.mapforge_engine import building_library as bl

    sources = bl.list_map_sources(install_root)
    fp = bl.fingerprint(install_root, tileset, xml_path, sources)
    cache_file = _BUILDING_LIB_CACHE_DIR / f"{fp}.json"

    def _from_cache() -> Optional[dict]:
        if rebuild or not cache_file.is_file():
            return None
        try:
            payload = json.loads(cache_file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        payload["from_cache"] = True
        return payload

    cached = _from_cache()
    if cached is not None:
        return cached

    with _building_lib_lock:
        # Re-check under the lock — a concurrent request may have just
        # finished the same build.
        cached = _from_cache()
        if cached is not None:
            return cached
        try:
            payload = bl.build_library(
                xml_path, tileset, install_root,
                loose_dirs=loose_dirs, slf_paths=slf_paths,
                sources=sources,
            )
        except Exception as e:  # noqa: BLE001
            raise HTTPException(500, {
                "error": "LIBRARY_BUILD_FAILED",
                "message": f"{type(e).__name__}: {e}",
            })
        payload["from_cache"] = False
        try:
            _BUILDING_LIB_CACHE_DIR.mkdir(parents=True, exist_ok=True)
            cache_file.write_text(
                json.dumps(payload, separators=(",", ":")),
                encoding="utf-8",
            )
            # Prune stale fingerprints for this install+tileset family —
            # cheap hygiene so edits don't accumulate dead multi-MB blobs.
            for old in _BUILDING_LIB_CACHE_DIR.glob("*.json"):
                if old.name != cache_file.name:
                    try:
                        meta = json.loads(old.read_text(encoding="utf-8"))
                        if (meta.get("install_root") == str(install_root)
                                and meta.get("tileset") == tileset):
                            old.unlink()
                    except (OSError, ValueError):
                        continue
        except OSError:
            pass  # cache write failure is non-fatal — just slower next time
        return payload


@router.post("/sessions/{session_id}/run-generator")
def run_generator(session_id: str, name: str = Query(...), body: RunGeneratorBody = RunGeneratorBody()):
    """Stream a generator's op output into the session.

    Returns NDJSON: one event per line. Two event shapes:
      - `{"phase": str, "status": "start"|"done", "label": str}`
        — progress markers the frontend renders in the log/progress UI
      - `{"op": {...}}` — a single edit op already APPLIED to the
        session's parsed dict. The frontend mirrors the op into its
        local atlas state for incremental canvas updates.

    Final event always:
      - `{"done": true, "ok": true, "applied": int}` on success
      - `{"done": true, "ok": false, "error": str, "applied": 0}`
        on failure — the run is TRANSACTIONAL: any mid-run failure
        rolls back every op applied so far, so the session is left
        exactly as it was before the run (nothing to undo; `applied`
        is 0). Mirrors `apply_edits`.

    The session is locked for the duration of the run (no concurrent
    user paint can race with generator ops). Generator runs go through
    the same edit op dispatcher the paint brush uses, so undo history
    + render invalidation work identically.
    """
    from mercwizard_core.mapforge.generators import get as get_generator, GeneratorContext

    _require_renderer()
    sess = _session_store.get(session_id)
    if sess.read_only:
        raise HTTPException(
            status_code=400,
            detail={
                "error": "SESSION_READ_ONLY",
                "message": ("Cannot run a generator on a read-only session "
                            "(SLF-bundled source). Extract the sector first."),
            },
        )

    gen = get_generator(name)
    if gen is None:
        raise HTTPException(
            status_code=404,
            detail={
                "error": "GENERATOR_NOT_FOUND",
                "message": f"No generator named {name!r}. List: GET /mapforge/generators",
            },
        )

    rows = sess.parsed["rows"]
    cols = sess.parsed["cols"]

    # Optional tileset metadata so tileset-aware generators (AutoShadow)
    # can validate a slot exists + a sub is in frame range before
    # emitting it. Best-effort: on any failure the generator degrades
    # gracefully (slot_map/frame_count stay None). frame_count is lazy —
    # it only decodes an STI when a generator actually asks, so
    # generators that don't need it (Wipe, Fill, …) pay nothing.
    slot_map: Optional[dict[int, str]] = None
    frame_count = None
    try:
        slot_map = load_tileset_xml(sess.xml_path, sess.tileset)
        _loose, _slf = _tileset_paths_for(sess.xml_path)
        _sti = StiCache(sess.tileset, loose_dirs=_loose, slf_paths=_slf)
        _fc_cache: dict[int, int] = {}

        def frame_count(slot: int) -> int:
            if slot not in _fc_cache:
                nm = slot_map.get(slot) if slot_map else None
                try:
                    _fc_cache[slot] = len(_sti.get(nm)) if nm else 0
                except Exception:  # noqa: BLE001
                    _fc_cache[slot] = 0
            return _fc_cache[slot]
    except Exception:  # noqa: BLE001
        slot_map = None
        frame_count = None

    ctx = GeneratorContext(rows=rows, cols=cols, parsed=sess.parsed,
                           slot_map=slot_map, frame_count=frame_count)

    def dry_run_stream():
        """Preview mode: iterate + shape-validate the generator's ops,
        apply NOTHING. Holds the lock only to give iter_ops a stable
        read of `parsed` (GeneratorContext is read-only by contract)."""
        try:
            buffered: list[dict] = []
            op_count = 0
            with sess._lock:
                for event in gen.iter_ops(ctx, body.params):
                    if "phase" in event:
                        buffered.append(event)
                        continue
                    op_obj = EditOp(**event)   # shape-validate only
                    g = op_obj.y * cols + op_obj.x
                    if not (0 <= g < rows * cols):
                        continue               # preview drops OOB ops
                    op_count += 1
                    buffered.append({"op": event})
            for ev in buffered:
                yield json.dumps(ev) + "\n"
            yield json.dumps({
                "done": True,
                "ok": True,
                "applied": 0,
                "dry_run": True,
                "op_count": op_count,
                "generator": name,
            }) + "\n"
        except Exception as e:  # noqa: BLE001
            yield json.dumps({
                "done": True,
                "ok": False,
                "error": "GENERATOR_FAILED",
                "message": f"{type(e).__name__}: {e}",
                "applied": 0,
                "dry_run": True,
            }) + "\n"

    if body.dry_run:
        return StreamingResponse(dry_run_stream(),
                                 media_type="application/x-ndjson")

    def event_stream():
        applied = 0
        try:
            # Apply all ops under the session lock (concurrent paints
            # queue behind, no interleaving) and buffer the events.
            # Stream AFTER the lock is released — `yield` inside `with
            # sess._lock:` suspends the generator between chunks, which
            # held the lock across the client's read window. A slow
            # consumer (backgrounded tab, mobile network) could keep the
            # lock held for 30+ seconds while concurrent paint/undo/
            # save calls blocked, and the Tauri shell watchdog could
            # respawn the sidecar mid-stream interpreting the frozen
            # health endpoint as a failure. Bug-review finding A5.
            #
            # Buffer footprint is bounded: even a WipeGenerator on a
            # 160×160 sector produces ~150k events × ~200 bytes ≈ 30MB,
            # well within desktop memory headroom. The user-visible
            # trade-off — no progress feedback until apply completes —
            # is acceptable for the safety win and matches what the
            # /edits-batch route already does.
            buffered: list[dict] = []
            with sess._lock:
                # Transactional, mirroring `apply_edits`: snapshot every
                # tile an op touches BEFORE applying it, so ANY mid-run
                # failure — an EditOpError (e.g. the 15-entry nibble cap),
                # an out-of-bounds op, a malformed EditOp, or the generator
                # itself raising — rolls the WHOLE run back. No half-applied
                # map is left stranded in the session. Generator ops STREAM
                # in, so (unlike apply_edits) the touched set isn't known up
                # front: capture each gridno's pre-state lazily the first
                # time an op hits it — O(touched tiles), not a full-map
                # deepcopy. Every generator mutation flows through
                # `_apply_single_edit` on an op's (x, y) — GeneratorContext
                # is read-only by contract — so the per-tile snapshot
                # captures all of them.
                world_max = rows * cols
                snap: dict[int, dict] = {}
                seen: set[int] = set()
                # The ops also bump the map-global `counts` totals; snapshot
                # it wholesale so a rollback restores it too (same defense-
                # in-depth as apply_edits).
                counts_before = dict(sess.parsed.get("counts") or {})

                def _rollback() -> None:
                    _restore_tiles(sess.parsed, snap)
                    c = sess.parsed.get("counts")
                    if c is not None:
                        c.clear()
                        c.update(counts_before)

                try:
                    for event in gen.iter_ops(ctx, body.params):
                        # Phase event: just buffer; no mutation.
                        if "phase" in event:
                            buffered.append(event)
                            continue
                        # Op event: validate + apply via the same path the
                        # /edits route uses.
                        op_obj = EditOp(**event)
                        # Snapshot this tile's pre-state the FIRST time an
                        # op touches it, BEFORE the mutation. Bounds filter
                        # mirrors apply_edits's touched-set computation.
                        g = op_obj.y * cols + op_obj.x
                        if 0 <= g < world_max and g not in seen:
                            seen.add(g)
                            snap.update(_snapshot_tiles(sess.parsed, [g]))
                        _apply_single_edit(sess.parsed, op_obj, rows, cols)
                        applied += 1
                        buffered.append({"op": event})
                except Exception:
                    # Roll the whole run back, then re-raise so the outer
                    # handler emits the proper done-event. edit_count /
                    # dirty are NOT bumped — the session is left untouched.
                    _rollback()
                    raise
                sess.edit_count += applied
                sess.dirty = sess.dirty or applied > 0
            # Lock released — safe to suspend on yield.
            for ev in buffered:
                yield json.dumps(ev) + "\n"
            yield json.dumps({
                "done": True,
                "ok": True,
                "applied": applied,
                "generator": name,
            }) + "\n"
        except EditOpError as e:
            # Rolled back above — 0 ops are live in the session now.
            yield json.dumps({
                "done": True,
                "ok": False,
                "error": "EDIT_OP_ERROR",
                "message": str(e),
                "applied": 0,
            }) + "\n"
        except Exception as e:  # noqa: BLE001
            # Generator (or an op) raised — the run was rolled back, so
            # the session is exactly as it was before. 0 ops applied.
            yield json.dumps({
                "done": True,
                "ok": False,
                "error": "GENERATOR_FAILED",
                "message": f"{type(e).__name__}: {e}",
                "applied": 0,
            }) + "\n"

    return StreamingResponse(event_stream(), media_type="application/x-ndjson")


@router.get("/sessions/{session_id}/tile", response_model=TileInspection)
def session_tile(
    session_id: str,
    x: int = Query(..., ge=0, le=1023),
    y: int = Query(..., ge=0, le=1023),
):
    """Like /sector/tile but reads from the session's in-memory parsed
    dict — sees all uncommitted edits."""
    _require_renderer()
    sess = _session_store.get(session_id)
    cols = sess.parsed["cols"]
    rows = sess.parsed["rows"]
    if x >= cols or y >= rows:
        raise HTTPException(400, {"error": "OUT_OF_BOUNDS",
            "message": f"({x},{y}) outside {cols}x{rows}"})
    gridno = y * cols + x
    slot_map = load_tileset_xml(sess.xml_path, sess.tileset)
    layers_out: dict[str, list[LayerEntry]] = {}
    for layer in ("land", "objs", "shadows", "structs", "roofs", "onroofs"):
        entries = sess.parsed[layer][gridno]
        layers_out[layer] = [
            LayerEntry(
                slot=slot, sub=sub,
                sti_filename=slot_map.get(slot),
                sti_frame_index_0based=sub - 1,
            )
            for slot, sub in entries
        ]
    return TileInspection(
        x=x, y=y, gridno=gridno,
        room_id=sess.parsed["rooms"][gridno],
        height=sess.parsed["heights"][gridno],
        world_flags=sess.parsed["world_flags"][gridno],
        layers=layers_out,
    )


@router.get("/sessions/{session_id}/render")
def session_render(
    session_id: str,
    room: Optional[int] = Query(None),
    bbox: Optional[str] = Query(None),
    ring: int = Query(5),
    full: bool = Query(False),
    highlight: bool = Query(True),
    skip_layers: str = Query(""),
    scale: int = Query(1),
):
    """Like /sector/render but renders from the session's parsed dict
    — sees uncommitted edits and skips the parse step (the slow part).
    Same response headers (X-MapForge-IxMin/IyMin/etc.) as the
    stateless render endpoint."""
    _require_renderer()
    sess = _session_store.get(session_id)
    # IsoRenderer accepts a pre-parsed dict so we don't re-parse on
    # every render. Construction is cheap when parsed is provided.
    loose_dirs, slf_paths = _tileset_paths_for(sess.xml_path)
    renderer = IsoRenderer(sess.dat_path, sess.xml_path, sess.tileset,
                           ring=ring, parsed=sess.parsed,
                           loose_dirs=loose_dirs, slf_paths=slf_paths)
    target_room = room
    region_bbox: Optional[tuple[int, int, int, int]] = None
    if full:
        target_room = None
    elif bbox:
        try:
            parts = tuple(int(v) for v in bbox.split(","))
            if len(parts) != 4:
                raise ValueError("expected 4 ints")
            region_bbox = (parts[0], parts[1], parts[2], parts[3])
        except (ValueError, TypeError):
            raise HTTPException(400, {"error": "BAD_BBOX",
                "message": "bbox must be 'x0,y0,x1,y1'"})
        target_room = None
    skip_set = {s.strip() for s in skip_layers.split(",") if s.strip()}
    try:
        canvas = renderer.render(room_id=target_room, bbox=region_bbox,
                                 highlight_room=highlight, skip_layers=skip_set)
    except ValueError as e:
        raise HTTPException(404, {"error": "INVALID_REGION", "message": str(e)})
    canvas_w, canvas_h = canvas.size
    ix_min = renderer._ix_min
    iy_min = renderer._iy_min
    title = (f"{sess.dat_path.name} ts={sess.tileset}"
             + (f" room={target_room}" if target_room is not None else "")
             + (f" bbox={bbox}" if region_bbox else "")
             + (" *EDITED*" if sess.dirty else ""))
    add_title(canvas, title)
    if scale > 1:
        from PIL import Image
        canvas = canvas.resize(
            (canvas.size[0] * scale, canvas.size[1] * scale),
            Image.NEAREST,
        )
    buf = io.BytesIO()
    canvas.save(buf, format="PNG", optimize=False)
    png_bytes = buf.getvalue()
    return Response(
        content=png_bytes,
        media_type="image/png",
        headers={
            "Cache-Control": "no-store",
            "Content-Length": str(len(png_bytes)),
            "X-MapForge-IxMin": str(ix_min),
            "X-MapForge-IyMin": str(iy_min),
            "X-MapForge-CanvasW": str(canvas_w),
            "X-MapForge-CanvasH": str(canvas_h),
            "X-MapForge-TileW": "40",
            "X-MapForge-TileH": "20",
            "X-MapForge-Dirty": "1" if sess.dirty else "0",
            "X-MapForge-EditCount": str(sess.edit_count),
            "Access-Control-Expose-Headers":
                "X-MapForge-IxMin, X-MapForge-IyMin, "
                "X-MapForge-CanvasW, X-MapForge-CanvasH, "
                "X-MapForge-TileW, X-MapForge-TileH, "
                "X-MapForge-Dirty, X-MapForge-EditCount",
        },
    )


# ─── Atlas (Phase 3 client-side renderer) ─────────────────────────────
# Goal: one PNG containing every (slot, sub) of every STI in the tileset,
# packed, plus a manifest with per-sprite atlas rect + engine offsets.
# The frontend draws iso views to <canvas> via ctx.drawImage(atlas, ...),
# which is ~100x faster than re-running PIL composite on the server.
#
# Differences from palette-sheet:
#   - palette-sheet: ONE 64x64 thumbnail per slot (frame 0 only)
#   - atlas: EVERY sub-frame, at NATIVE size, with original sti offsets
#     (so the renderer reproduces engine-faithful placement)
#
# Cache layout: %APPDATA%/MercWizard/mapforge/atlas/<tileset>_<fp>/
#   ├─ atlas.png      (the packed sprite sheet)
#   └─ manifest.json  (rect + offsets per (slot, sub))

_ATLAS_CACHE = (
    Path(os.environ.get("APPDATA") or Path.home() / ".config")
    / "MercWizard" / "mapforge" / "atlas"
)
# Single-flight locks keyed by atlas cache_dir name (e.g.
# "18_e26c1d5f2e36541e" or "18_<fp>_partial_<pairs_hash>"). When
# multiple concurrent _build_atlas calls target the same cache_dir
# (typical: two sectors in the same tileset opening back-to-back),
# only ONE actually bakes; the others block on the lock and then
# hit the now-warm cache. Without this lock, observed 2x-3x penalty
# on tileset 18 full bake (22-24s vs ~11s solo) because the bakes
# serialize anyway on the StiCache SLF read lock — but each one
# pays the JSD harvest separately.
import threading as _th
_BAKE_LOCKS_META = _th.Lock()
_BAKE_LOCKS: dict[str, _th.Lock] = {}


def _get_bake_lock(key: str) -> _th.Lock:
    """Return the per-cache-dir bake lock, creating it on first request."""
    with _BAKE_LOCKS_META:
        lock = _BAKE_LOCKS.get(key)
        if lock is None:
            lock = _th.Lock()
            _BAKE_LOCKS[key] = lock
        return lock


# Global semaphore that caps the number of concurrent FULL atlas bakes.
# Without this, opening N sectors in different tilesets back-to-back
# kicks off N concurrent background full bakes (each takes ~11 s solo).
# Python's GIL + PIL paste + sequential STI decode means they thrash
# rather than parallelise — observed 11s solo → 30-60s under 4-way
# contention on a typical machine.
#
# Capacity = 1 → strict serialisation. Each bake gets full CPU + disk
# bandwidth. Total time for N bakes is N × 11 s but each individual
# bake completes at solo speed, vs N × (30-60 s) under contention.
# Net wall time is roughly equal to the slow case but per-bake latency
# is bounded.
#
# PARTIAL bakes are NOT held by this semaphore — they're fast (~2 s
# even cold) and are the user-visible critical path. The single-flight
# per-cache_dir lock above already prevents duplicate work for the same
# (tileset, sector-pairs-hash).
_FULL_BAKE_SEMAPHORE = _th.Semaphore(1)
# Disk cache for the JSD index — a basename→(bytes, src) dict harvested
# from each install's SLFs + loose tileset dirs. Keyed by SLF/loose
# mtimes so the cache hits as long as the source files haven't changed.
# Without this, every cold atlas bake pays the ~8.6s walk-both-SLFs
# cost. With it, repeat bakes load the pre-walked index in ~20 ms.
_JSD_INDEX_CACHE = (
    Path(os.environ.get("APPDATA") or Path.home() / ".config")
    / "MercWizard" / "mapforge" / "jsd_index"
)
# Row-based packing with this max width. Wider atlases waste fewer rows
# but eat texture-size budgets on some hardware. 4096 is the safe upper
# bound on all modern browsers (Canvas2D max is typically 16384, WebGL
# texture max varies; 4096 stays well inside both).
ATLAS_MAX_WIDTH = 4096


class ZStripInfo(BaseModel):
    """Per-frame Z-strip metadata, ported from JA2 1.13's `ZStripInfo`
    struct (`SGP/vobject.h:44-50`). Computed for any sub-frame whose
    DB_STRUCTURE has `ubNumberOfTiles > 1` (multi-tile structure) —
    mirrors the engine's `AddZStripInfoToVObject` trigger at
    `TileEngine/structure.cpp:2186`.

    Each strip is `WORLD_TILE_X / 2 = 20` pixels wide. The strip-N depth
    offset from the sprite's base sZLevel is the running sum
    `initial_z_change + sum(z_changes[0..N-1])` multiplied by the
    engine's `Z_STRIP_DELTA_Y = Z_SUBLAYERS × 10 = 80`. The first strip
    may be narrower than 20 px (stored in `first_strip_width`).

    The frontend's WebGL renderer uses these to split a multi-tile
    sprite into N depth-distinct quads, then chooses depthFunc by
    `burns_through`:

    - `burns_through = False` (non-wall multi-Z, the common case —
      lawless4 furniture, trees, vehicles): use `gl.LESS` (STRICT).
      Mirrors the engine's `Blt8BPPDataTo16BPPBufferTransZIncClip`
      blitter at `renderworld.cpp:5061-5063` which uses `JAE` —
      existing >= sprite → SKIP. This is what clips lawless4 behind
      a neighboring wall whose flat Z matches a lawless4 strip's Z.
    - `burns_through = True` (wall multi-Z, rare in the reference install):
      use `gl.LEQUAL`. Mirrors `...ZSameZBurnsThrough` blitter at
      renderworld.cpp:5221+ — equal Z DRAWS, so wall corners meeting
      other walls at same Z still composite correctly.

    See `docs/HANDOFF_iso_renderer_z_buffer.md` for the bug history."""
    initial_z_change: int     # bInitialZChange, signed INT8
    first_strip_width: int    # ubFirstZStripWidth, 1-20 px
    z_changes: list[int]      # pbZChange[], each in {-1, 0, +1}
    burns_through: bool = False  # True for WALL multi-Z; False for non-wall (strict)


class AtlasCell(BaseModel):
    """One sub-frame's location in the atlas. Mirrors the Python
    iso_renderer's per-frame state: (image, ox, oy)."""
    slot: int
    sub: int                  # 1-based, engine convention
    x: int                    # atlas pixel rect
    y: int
    w: int
    h: int
    ox: int                   # STI offset_x (engine-semantic INT16)
    oy: int                   # STI offset_y
    # Per-frame Z-strip data for engine-faithful wall clipping. Set on
    # full bakes ONLY when the slot's JSD has the WALL flag (0x0001)
    # AND ubNumberOfTiles > 1 (matches engine's
    # `AddZStripInfoToVObject` trigger at structure.cpp:2186). None for
    # non-wall sprites and for ALL cells in partial bakes (which skip
    # the JSD harvest).
    zstrip: Optional[ZStripInfo] = None


class JsdFootprintTile(BaseModel):
    """One tile in a multi-tile struct's footprint.

    `bX`/`bY` is the offset in TILE coordinates from the anchor tile
    the user clicks; `sub` is the 1-based STI sub to place at that
    offset. Convention: the JSD's tile-record list is iterated in
    order — record N corresponds to sub N+1 of the STI."""
    bX: int
    bY: int
    sub: int


class JsdFootprint(BaseModel):
    """Stamp recipe for a multi-tile struct. Frontend reads this when
    the user paints a brush whose slot has `slot_jsd_footprint`
    populated — one click emits a paint op per `tiles[i]`.

    `tiles` is capped at `min(ubNumberOfTiles, frame_count)` so we
    never emit a sub the STI doesn't carry. The JSD's remaining
    footprint tiles are passability-only and the engine handles them
    on load via the .jsd companion file."""
    tiles: list[JsdFootprintTile]


class AtlasManifest(BaseModel):
    tileset: int
    xml_path: str
    atlas_w: int
    atlas_h: int
    fingerprint: str          # invalidates with slot map changes
    cells: list[AtlasCell]
    # slot -> sti_filename, so the frontend can label / debug
    slot_filenames: dict[int, str]
    # slot -> True when a .jsd companion exists for the slot's STI.
    # Computed once at bake time; the inspector uses it to decide
    # whether to surface a "View JSD" button per struct entry without
    # per-tile backend probes.
    slot_has_jsd: dict[int, bool] = {}
    # slot -> footprint recipe, ONLY populated for slots whose JSD
    # has ubNumberOfTiles > 1. Frontend reads this to stamp the whole
    # multi-tile struct (heli wreck, vehicle, big debris piece) on a
    # single click. Slots with single-tile JSDs and slots without a
    # JSD don't appear here.
    slot_jsd_footprint: dict[int, JsdFootprint] = {}
    # True for a full-tileset bake; False for a sector-specific PARTIAL
    # bake that contains only sprites that sector uses. The frontend
    # uses partial bakes to render the sector immediately on open
    # (~2 s vs ~11 s for the full bake), then requests the complete
    # atlas in the background and swaps via IsoRenderer.replaceAtlas
    # when it arrives. Default True for backwards compat with existing
    # on-disk caches that don't carry this field.
    complete: bool = True


def _atlas_fingerprint(xml_path: Path, tileset: int,
                       slot_map: dict[int, str]) -> str:
    """Hash of (xml path, tileset, slot map). Any STI change in the
    tileset re-bakes the atlas. Bake-algorithm version baked into the
    hash so format additions invalidate disk caches."""
    h = hashlib.sha1()
    # Bake-algorithm version. Bumped when the manifest schema changed
    # to include slot_jsd_footprint (multi-tile stamp recipes), and
    # again to invalidate caches built with the buggy JSD parser that
    # read PROFILE bytes as bX/bY offsets (off-by-100 stamp bug). Bumped
    # to v4 when AtlasCell gained `zstrip`. Bumped to v5 when the
    # zstrip computation widened from WALL+multi-tile to ANY multi-tile
    # sub-frame (engine's `AddZStripInfoToVObject` trigger), and
    # ZStripInfo gained `burns_through` to distinguish wall (LEQUAL) vs
    # non-wall (STRICT LESS) depth comparison in the WebGL renderer.
    # Bumped to v6 when partial bakes started running JSD harvest too,
    # so the initial fast-render manifest carries zstrip and the bug
    # fix fires on first paint (not just after the complete bake swap).
    h.update(b"atlas-bake-v6|")
    h.update(f"{xml_path.resolve()}|{tileset}".encode("utf-8", "replace"))
    for slot, name in sorted(slot_map.items()):
        h.update(f"|{slot}={name}".encode("utf-8", "replace"))
    return h.hexdigest()[:16]


# Engine constants used by the Z-strip port. World tile is 40×20 pixels;
# each Z-strip column is half-tile-X wide (20 px). Don't drift these
# without updating IsoRendererGL.ts — both sides must agree on the strip
# width or per-strip depths come out shifted.
_ZSTRIP_WORLD_TILE_X = 40
_ZSTRIP_WORLD_TILE_Y = 20
_ZSTRIP_HALF_TILE_X = _ZSTRIP_WORLD_TILE_X // 2  # 20 pixels per strip


def _compute_zstrip_for_frame(
    offset_x: int,
    offset_y: int,
    width: int,
    is_mobile_or_corpse: bool = False,
    bz_tile_offset_x: int = 0,
    bz_tile_offset_y: int = 0,
) -> Optional[ZStripInfo]:
    """Port of the engine's per-frame `AddZStripInfoToVObject` body
    (`TileEngine/structure.cpp:2306-2497`). Given an STI frame's
    ETRLE header values (`sOffsetX`, `sOffsetY`, `usWidth`) plus the
    DB_STRUCTURE's MOBILE/CORPSE adjustment flag, returns the
    `ZStripInfo` the engine would have computed at LoadTileSurface time.

    The engine's outer caller gates this on
    `pDBStructure->ubNumberOfTiles > 1 || STRUCTURE_CORPSE`
    (`structure.cpp:2186, 2201, 2278`). MercForge's bake applies that
    gate at the call site before invoking this helper — slots without a
    multi-tile WALL JSD never reach here.

    Returns None for sprite widths <= 0 (degenerate; nothing to strip).
    Otherwise always returns a populated `ZStripInfo`; degenerate strip
    counts (single-strip sprites) get a single dummy `0` z_change so
    the frontend's strip walker has at least one entry — same engine
    safeguard at structure.cpp:2476-2496."""
    if width <= 0:
        return None

    s_offset_x = offset_x
    s_offset_y = offset_y

    if is_mobile_or_corpse:
        # structure.cpp:2316-2323 — adjust for the animation-vs-structure
        # base-tile offset, then for the multi-tile bZTileOffset shift.
        s_offset_x += _ZSTRIP_HALF_TILE_X
        s_offset_y += _ZSTRIP_WORLD_TILE_Y // 2
        s_offset_x -= bz_tile_offset_x * _ZSTRIP_HALF_TILE_X
        s_offset_x += bz_tile_offset_y * _ZSTRIP_HALF_TILE_X
        s_offset_y -= bz_tile_offset_y * (_ZSTRIP_WORLD_TILE_Y // 2)
    # s_offset_y otherwise unused — engine writes it back to pETRLEObject
    # via the adjustment above, but the strip calc itself only reads X.
    _ = s_offset_y

    # structure.cpp:2326-2377 — partition width into left + right halves
    # relative to the tile's bottom corner. Five cases.
    if s_offset_x <= 0:
        s_right_half_width = width + s_offset_x - _ZSTRIP_HALF_TILE_X
        if s_right_half_width >= 0:
            # Case 1: negative offset, image straddles bottom corner.
            s_left_half_width = -s_offset_x + _ZSTRIP_HALF_TILE_X
        else:
            # Case 2: negative offset, image all on left side. Bump the
            # left-half-width to the right edge of the last tile-half so
            # the leftmost strip's portion comes out right.
            s_left_half_width = width - (s_right_half_width % _ZSTRIP_HALF_TILE_X)
            s_right_half_width = 0
    elif s_offset_x < _ZSTRIP_HALF_TILE_X:
        s_left_half_width = _ZSTRIP_HALF_TILE_X - s_offset_x
        s_right_half_width = width - s_left_half_width
        if s_right_half_width <= 0:
            # Case 3: positive offset < 20, image all on left side.
            # Engine comment: "should never happen because these images
            # are multi-tile". Mirror its defensive fallback.
            s_right_half_width = 0
            s_left_half_width = _ZSTRIP_HALF_TILE_X
        # else Case 4: straddles bottom corner — happy path.
    else:
        # Case 5: positive offset >= 20, image all on right side. Engine
        # comment: "should never happen either".
        s_left_half_width = 0
        s_right_half_width = width

    # structure.cpp:2379-2390 — count strips per region.
    ub_num_increasing = 0
    ub_num_stable = 0
    ub_num_decreasing = 0
    if s_left_half_width > 0:
        ub_num_increasing = s_left_half_width // _ZSTRIP_HALF_TILE_X
    if s_right_half_width > 0:
        ub_num_stable = 1
        if s_right_half_width > _ZSTRIP_HALF_TILE_X:
            ub_num_decreasing = s_right_half_width // _ZSTRIP_HALF_TILE_X

    # structure.cpp:2391-2416 — first-strip width with zero-guard.
    if s_left_half_width > 0:
        first_strip_width = s_left_half_width % _ZSTRIP_HALF_TILE_X
        if first_strip_width == 0:
            ub_num_increasing -= 1
            first_strip_width = _ZSTRIP_HALF_TILE_X
    else:
        # Right-side-only branch: offset is at least HALF_TILE_X.
        if s_offset_x > _ZSTRIP_WORLD_TILE_X:
            first_strip_width = (
                _ZSTRIP_HALF_TILE_X
                - (s_offset_x - _ZSTRIP_WORLD_TILE_X) % _ZSTRIP_HALF_TILE_X
            )
        else:
            first_strip_width = _ZSTRIP_WORLD_TILE_X - s_offset_x
        if first_strip_width == 0:
            ub_num_decreasing -= 1
            first_strip_width = _ZSTRIP_HALF_TILE_X

    # structure.cpp:2418-2474 — build pbZChange[] + bInitialZChange.
    ub_number_of_z_changes = ub_num_increasing + ub_num_stable + ub_num_decreasing
    if ub_number_of_z_changes > 0:
        z_changes: list[int] = (
            [1] * ub_num_increasing
            + [0] * ub_num_stable
            + [-1] * ub_num_decreasing
        )
        if ub_num_increasing > 0:
            initial_z_change = -ub_num_increasing
        elif ub_num_stable > 0:
            initial_z_change = 0
        else:
            initial_z_change = -ub_num_decreasing
    else:
        # structure.cpp:2478-2496 — degenerate frame, dummy strip so the
        # blitter (and frontend walker) have a valid entry to iterate.
        z_changes = [0]
        initial_z_change = 0

    return ZStripInfo(
        initial_z_change=initial_z_change,
        first_strip_width=first_strip_width,
        z_changes=z_changes,
    )


def _harvest_jsd_lookup(
    slf_paths: list[Path],
    loose_dirs: list[Path],
    tileset: int,
    emit: Optional[Callable[[dict], None]] = None,
) -> dict[str, tuple[bytes, str]]:
    """Build (or load from disk cache) a `{basename.lower(): (bytes, src)}`
    map of every .jsd file available to the renderer.

    The dominant cost in the JSD harvest is walking each SLF archive
    (~1.75 ms per entry × ~2000 entries × N archives = ~7-8s on Mod
    Prototype). This function caches the result to disk keyed by the
    SLFs' + loose dirs' mtimes — repeat runs against the same install
    skip the walk entirely, dropping the cold-bake budget by ~8 s.

    The cache file is per-tileset because the loose-dir scan looks at
    `<base>/<tileset>/` AND `<base>/0/`; a different tileset reads a
    different loose subdir.
    """
    import pickle  # noqa: E402

    def _emit(evt):
        if emit is not None:
            try:
                emit(evt)
            except Exception:  # noqa: BLE001
                pass

    # ── Build cache key from input mtimes ────────────────────────────
    key_parts: list[str] = []
    for slf in slf_paths:
        if slf.exists():
            try:
                m = slf.stat().st_mtime_ns
                key_parts.append(f"slf|{slf.resolve()}|{m}")
            except OSError:
                pass
    for base in loose_dirs:
        for sub_dir in (str(tileset), "0"):
            sub_path = base / sub_dir
            if sub_path.is_dir():
                try:
                    # Dir mtime catches add/remove; aggregate file
                    # mtimes catches in-place edits to existing JSDs.
                    parts_inner = [str(sub_path.resolve())]
                    parts_inner.append(str(sub_path.stat().st_mtime_ns))
                    for p in sub_path.iterdir():
                        if p.is_file() and p.suffix.lower() == ".jsd":
                            parts_inner.append(f"{p.name}:{p.stat().st_mtime_ns}")
                    key_parts.append("dir|" + "|".join(parts_inner))
                except OSError:
                    pass
    # Stable hash regardless of input order.
    key_parts.sort()
    cache_key = hashlib.sha1(("\x1f".join(key_parts)).encode("utf-8")).hexdigest()[:16]
    cache_file = _JSD_INDEX_CACHE / f"{tileset}_{cache_key}.pkl"

    # ── Cache hit? ───────────────────────────────────────────────────
    # Same debug-mode bypass as the atlas cache — MERCWIZARD_DEBUG=1
    # forces a cold harvest so the user sees real load times.
    _debug = os.environ.get("MERCWIZARD_DEBUG", "").strip() not in ("", "0", "false", "False")
    if cache_file.is_file() and not _debug:
        try:
            with open(cache_file, "rb") as f:
                cached = pickle.load(f)
            if isinstance(cached, dict):
                _emit({"event": "phase", "phase": "jsd-cache-hit",
                       "label": f"JSD index cache hit ({len(cached)} entries)"})
                return cached
        except (OSError, pickle.UnpicklingError, EOFError):
            pass  # corrupt cache — rebuild

    # ── Walk (the slow path) ──────────────────────────────────────────
    _emit({"event": "phase", "phase": "jsd-harvest",
           "label": "Walking SLFs for .jsd files"})
    jsd_lookup: dict[str, tuple[bytes, str]] = {}
    try:
        from ja2py.fileformats.SlfFS import SlfFS  # noqa: E402
        for slf_path in slf_paths:
            if not slf_path.exists():
                continue
            try:
                fs = SlfFS(str(slf_path))
                for p in fs.walk.files():
                    if not p.lower().endswith(".jsd"):
                        continue
                    bn = os.path.basename(p).lower()
                    # first-wins per archive; loose pass below overwrites.
                    if bn not in jsd_lookup:
                        try:
                            jsd_lookup[bn] = (fs.readbytes(p), f"slf://{slf_path}!{p}")
                        except Exception:  # noqa: BLE001
                            pass
            except Exception:  # noqa: BLE001
                pass
    except ImportError:
        pass
    # Loose JSDs override SLF ones (modder dropped a custom JSD into
    # Data-1.13/Tilesets/<N>/ to override the stock one).
    for base in loose_dirs:
        if not base.exists():
            continue
        for sub_dir in (str(tileset), "0"):
            sub_path = base / sub_dir
            if not sub_path.is_dir():
                continue
            try:
                for p in sub_path.iterdir():
                    if p.is_file() and p.suffix.lower() == ".jsd":
                        try:
                            jsd_lookup[p.name.lower()] = (p.read_bytes(), str(p))
                        except OSError:
                            pass
            except OSError:
                pass

    # ── Persist for next time ─────────────────────────────────────────
    try:
        cache_file.parent.mkdir(parents=True, exist_ok=True)
        with open(cache_file, "wb") as f:
            pickle.dump(jsd_lookup, f, protocol=pickle.HIGHEST_PROTOCOL)
    except OSError:
        pass  # disk full / permission — bake still works, just slow next time

    return jsd_lookup


def _validate_parsed_consistency(parsed: dict) -> Optional[str]:
    """Pre-save safety check. Walk the parsed dict and confirm
    `n_per_tile[layer][i] == len(parsed[layer][i])` for every tile,
    every layer. Returns None on success, an error string on first
    mismatch (so the caller can refuse the save with a precise reason).

    Why this matters: the writer encodes the layer-count nibbles from
    `n_per_tile` (`_pack_layer_counts` in dat_writer.py) AND writes the
    actual entries from `parsed[layer]`. If these disagree, the engine
    reads the wrong number of entries during LoadWorld, file pointer
    ends up misaligned, MAPINFO is read from the wrong offset,
    `gMapInformation.ubMapVersion` ends up < 15, and the engine asserts
    with "Map is less than minimum supported version" — which is the
    crash a user hit 2026-05-25 on a saved C6.

    Each edit op in dat_edit_ops.py is supposed to keep these in sync
    (it updates both `parsed[layer][gridno]` and
    `parsed["n_per_tile"][ck][gridno]` together), but if any code path
    mutates entries WITHOUT updating the count nibble, this catches it.
    """
    layer_to_count_key = {
        "land":    "land",
        "objs":    "obj",
        "structs": "struct",
        "shadows": "shadow",
        "roofs":   "roof",
        "onroofs": "onroof",
    }
    n_per_tile = parsed.get("n_per_tile") or {}
    for plural, ck in layer_to_count_key.items():
        entries = parsed.get(plural) or []
        counts = n_per_tile.get(ck) or []
        if len(entries) != len(counts):
            return (
                f"{plural}: layer array length {len(entries)} ≠ count array "
                f"length {len(counts)} (world_max corruption)"
            )
        for i, (tile_entries, declared) in enumerate(zip(entries, counts)):
            actual = len(tile_entries)
            if actual != declared:
                return (
                    f"{plural}: tile {i} (x={i % parsed.get('cols', 160)}, "
                    f"y={i // parsed.get('cols', 160)}) has {actual} entries "
                    f"but n_per_tile[{ck}][{i}] = {declared}. "
                    f"This would mis-align the engine's file reader and "
                    f"surface as 'Map is less than minimum supported version' "
                    f"at load time."
                )
            # Engine 4-bit cap: per-tile per-layer count fits in a nibble.
            if actual > 15:
                return (
                    f"{plural}: tile {i} has {actual} entries — exceeds the "
                    f"4-bit per-tile cap (15). Writer would truncate the "
                    f"count nibble and the engine would read fewer entries."
                )
    return None


def _collect_used_pairs(parsed: dict) -> set[tuple[int, int]]:
    """Walk a parsed sector (from `parse_dat_file`) and return the
    distinct set of (slot, sub) pairs the sector references across all
    layers. Used by the partial-atlas bake to skip sprites the sector
    doesn't need — typically ~15-20% of the tileset is referenced, so
    the partial atlas is ~5× smaller + correspondingly faster to bake.

    Each parsed[layer] is a list (one entry per tile cell) of lists of
    (slot, sub) tuples. We just flatten + dedup.
    """
    pairs: set[tuple[int, int]] = set()
    for layer_name in ("land", "structs", "roofs", "objs", "shadows", "onroofs"):
        for tile_entries in parsed.get(layer_name, []) or []:
            for entry in tile_entries:
                if isinstance(entry, tuple) and len(entry) >= 2:
                    pairs.add((entry[0], entry[1]))
    return pairs


def _build_atlas(xml_path: Path, tileset: int,
                  emit: Optional[Callable[[dict], None]] = None,
                  needed_pairs: Optional[set[tuple[int, int]]] = None,
                  ) -> tuple[bytes, AtlasManifest, Path]:
    """Bake the atlas + manifest. Returns (png_bytes, manifest, cache_dir).
    Hits the on-disk cache when fingerprint matches.

    `emit(event_dict)` is called at progress checkpoints when provided.
    Used by `/tileset/atlas/build` to stream NDJSON progress to the
    client so the load progress bar can advance during the 1-5 second
    cold bake (loading STIs, packing, encoding) — without this the bar
    sat at 0% for the whole bake then snapped to 100% when the PNG
    transfer started.

    The bake phases (full bake on a reference-install tileset 18, ~150 STIs):
      check-cache  — ~580 ms: load slot map XML + compute fingerprint
      load-stis    — ~2.6 s: decode 150+ STI files from SLFs (sequential
                     because StiCache's per-name SLF walk early-exits
                     fast — full SLF walks are O(N×1.75 ms) which kills
                     parallelism precompute attempts)
      pack         — ~6 ms: row-pack sprites into cells
      render       — ~10 ms: PIL.paste each sprite (fast despite ~4k
                     calls — this was misdiagnosed as the bottleneck
                     for hours on 2026-05-25; it never was)
      jsd-harvest  — ~8.6 s: walk both SLFs for .jsd files (THIS is
                     the dominant phase; ~1.75 ms × 4000 SLF entries)
                     Skipped entirely for partial bakes.
      footprints   — ~1 ms: parse JSDs + build stamp recipes
      encode       — ~245 ms: PNG-encode the 4096×1987 atlas image
      persist      — ~20 ms: write atlas.png + manifest.json

    `needed_pairs` (optional): when set, bake a SECTOR-SPECIFIC partial
    atlas containing only those (slot, sub) pairs. Sector typically
    references 15-20% of the tileset, so partial bake is ~5× faster
    (~2.2 s on tileset 18). The partial atlas omits the JSD-harvest
    phase entirely (saves ~8.6 s); JSD-dependent UI surfaces degrade
    until the COMPLETE atlas arrives in the background. Cache subdir
    gets a `_partial_<pairs_hash>` suffix to avoid colliding with
    complete bakes. Manifest carries `complete: False`.
    """
    from PIL import Image  # noqa: E402
    import logging as _logging
    import time as _time
    _log = _logging.getLogger("mapforge.bake")
    _bake_t0 = _time.perf_counter()
    _bake_mode = "partial" if needed_pairs is not None else "full"
    _log.info(
        "bake START mode=%s tileset=%s xml=%s%s",
        _bake_mode, tileset, Path(xml_path).name,
        f" pairs={len(needed_pairs)}" if needed_pairs is not None else "",
    )

    def _emit(event_dict):
        if emit is not None:
            try:
                emit(event_dict)
            except Exception:  # noqa: BLE001
                pass  # progress reporting must not break the bake

    _emit({"event": "phase", "phase": "check-cache",
           "label": "Checking atlas cache"})
    slot_map = load_tileset_xml(xml_path, tileset)
    fp = _atlas_fingerprint(xml_path, tileset, slot_map)
    # When needed_pairs is set we're baking a sector-specific PARTIAL
    # atlas — the cache subdir gets a `_partial_<hash>` suffix so it
    # doesn't collide with the complete-tileset bake. Partial atlases
    # are also valid complete atlases of their subset; manifests carry
    # a `complete: bool` so the frontend knows to request a full bake
    # in the background.
    if needed_pairs is not None:
        pairs_h = hashlib.sha1(
            "|".join(f"{s}:{u}" for s, u in sorted(needed_pairs)).encode()
        ).hexdigest()[:12]
        cache_dir = _ATLAS_CACHE / f"{tileset}_{fp}_partial_{pairs_h}"
    else:
        cache_dir = _ATLAS_CACHE / f"{tileset}_{fp}"
    # Single-flight per cache_dir — see _get_bake_lock docstring. The
    # lock acquires BEFORE the cache check so a 2nd concurrent call
    # for the same cache_dir blocks here, then hits the now-warm cache
    # after the 1st call finishes writing it.
    _bake_lock = _get_bake_lock(cache_dir.name)
    _bake_lock_acquired_t0 = _time.perf_counter()
    _bake_lock_acquired = _bake_lock.acquire()
    try:
        _bake_lock_wait_ms = (_time.perf_counter() - _bake_lock_acquired_t0) * 1000
        if _bake_lock_wait_ms > 50:
            _log.info(
                "bake LOCK-WAIT mode=%s tileset=%s ms=%.0f cache_dir=%s",
                _bake_mode, tileset, _bake_lock_wait_ms, cache_dir.name,
            )
        return _build_atlas_locked(
            xml_path, tileset, emit, needed_pairs,
            slot_map, fp, cache_dir,
            _bake_t0, _bake_mode, _log,
        )
    finally:
        if _bake_lock_acquired:
            _bake_lock.release()


def _build_atlas_locked(
    xml_path: Path, tileset: int,
    emit: Optional[Callable[[dict], None]],
    needed_pairs: Optional[set[tuple[int, int]]],
    slot_map: dict[int, str],
    fp: str,
    cache_dir: Path,
    _bake_t0: float,
    _bake_mode: str,
    _log,
) -> tuple[bytes, "AtlasManifest", Path]:
    """Inner implementation of `_build_atlas`, called with the
    single-flight lock for `cache_dir.name` held by the caller.

    Split out so we don't need to re-indent the existing 240-line body
    inside a `with` block. Locking + LOCK-WAIT logging stays in the
    outer `_build_atlas`; the cache check + actual bake + persist all
    happen here under the lock.
    """
    import time as _time  # local re-import since this is a separate function scope
    from PIL import Image  # noqa: E402

    def _emit(event_dict):
        if emit is not None:
            try:
                emit(event_dict)
            except Exception:  # noqa: BLE001
                pass

    png_path = cache_dir / "atlas.png"
    manifest_path = cache_dir / "manifest.json"
    # Debug mode bypass — MERCWIZARD_DEBUG=1 forces a cold bake every
    # time so the user sees real load times during testing. Set by
    # launch_debug.ps1; inherited by the sidecar via process env.
    _debug = os.environ.get("MERCWIZARD_DEBUG", "").strip() not in ("", "0", "false", "False")
    if png_path.is_file() and manifest_path.is_file() and not _debug:
        try:
            import json as _json
            mdict = _json.loads(manifest_path.read_text(encoding="utf-8"))
            _emit({"event": "phase", "phase": "cache-hit",
                   "label": "Atlas already built"})
            _bake_ms = (_time.perf_counter() - _bake_t0) * 1000
            _log.info(
                "bake CACHE-HIT mode=%s tileset=%s ms=%.0f cells=%d",
                _bake_mode, tileset, _bake_ms, len(mdict.get("cells", [])),
            )
            return png_path.read_bytes(), AtlasManifest(**mdict), cache_dir
        except (OSError, ValueError):
            pass  # cache corrupt — rebuild
    if _debug and png_path.is_file():
        _emit({"event": "phase", "phase": "debug-bypass",
               "label": "MERCWIZARD_DEBUG=1 — skipping atlas cache, re-baking"})

    # Global cap on concurrent FULL bakes (see _FULL_BAKE_SEMAPHORE
    # docstring). Acquired AFTER the cache-hit branch above so warm
    # cache loads don't queue behind a slow in-progress cold bake.
    # Partial bakes skip this — they're on the user-visible critical
    # path and need to run promptly even if a background full is busy.
    _full_sem_acquired = False
    if needed_pairs is None:
        _full_sem_t0 = _time.perf_counter()
        _FULL_BAKE_SEMAPHORE.acquire()
        _full_sem_acquired = True
        _full_sem_wait_ms = (_time.perf_counter() - _full_sem_t0) * 1000
        if _full_sem_wait_ms > 50:
            _log.info(
                "bake SEM-WAIT mode=full tileset=%s ms=%.0f",
                tileset, _full_sem_wait_ms,
            )
    try:
        return _build_atlas_unbaked(
            xml_path, tileset, _emit, needed_pairs,
            slot_map, fp, cache_dir, png_path, manifest_path,
            _bake_t0, _bake_mode, _log, _time,
        )
    finally:
        if _full_sem_acquired:
            _FULL_BAKE_SEMAPHORE.release()


def _build_atlas_unbaked(
    xml_path: Path, tileset: int,
    _emit: Callable[[dict], None],
    needed_pairs: Optional[set[tuple[int, int]]],
    slot_map: dict[int, str],
    fp: str,
    cache_dir: Path,
    png_path: Path,
    manifest_path: Path,
    _bake_t0: float,
    _bake_mode: str,
    _log,
    _time,
) -> tuple[bytes, "AtlasManifest", Path]:
    """Body of the bake — runs after the cache check has missed and
    (for full bakes) the global full-bake semaphore is acquired. Split
    out so the semaphore release in `_build_atlas_locked`'s finally
    block doesn't need to indent the rest of the function body.
    """
    from PIL import Image  # noqa: E402

    # Asset roots: the active install's tileset dirs across its VFS layers.
    loose, slf = _tileset_paths_for(xml_path)
    cache = StiCache(tileset, loose_dirs=loose, slf_paths=slf)

    # PHASE: load-stis. Parallelised. iso_renderer's StiCache got a
    # per-instance lock around SLF readbytes() on 2026-05-25 making
    # concurrent cache.get() on distinct names safe.
    #
    # 2026-05-25 measurements (tileset 18 cold):
    #   sequential:                          2,642 ms
    #   parallel 4-worker + lock:            2,827 ms  (lock contention)
    #   precompute SLF index + parallel:   load-stis 738ms BUT index-slf 7,138ms (net LOSS)
    #
    # The precompute walks both SLFs fully (~4000 file entries) at
    # ~1.75 ms/entry — that's because ja2py's SlfFS.walk is doing real
    # work per item (not just iterating cached metadata). Meanwhile the
    # sequential per-name walk is O(N/2) AVERAGE with early-exit, and
    # benefits from whatever lazy caching SlfFS has after the first
    # walk. The math: 151 sequential early-exit walks total LESS work
    # than 2 exhaustive walks.
    #
    # Parallel without precompute is also a slight loss because the
    # walks happen in parallel but the readbytes lock serializes the
    # I/O step, and ThreadPoolExecutor adds its own per-task overhead.
    # Net: sequential is the winner here.
    sorted_slots = sorted(slot_map.items())
    valid_slots = [(s, n) for s, n in sorted_slots if n]
    # Partial bake — drop slots not referenced by the sector. Saves
    # the per-slot SLF walk + STI decode for ~70-85% of slots in a
    # typical sector. The whole point of the partial bake.
    if needed_pairs is not None:
        needed_slots = {s for s, _u in needed_pairs}
        valid_slots = [(s, n) for s, n in valid_slots if s in needed_slots]
    total_slots = len(valid_slots)
    _emit({"event": "phase", "phase": "load-stis",
           "label": f"Loading {total_slots} STI assets"})

    sprites: list[tuple[int, int, int, int, int, int, "Image.Image"]] = []
    # (slot, sub, w, h, ox, oy, pil)
    for i, (slot, name) in enumerate(valid_slots):
        _emit({"event": "progress", "current": i, "total": total_slots,
               "detail": name})
        frames = cache.get(name)
        for frame_idx, (pil, ox, oy) in enumerate(frames):
            # sub is 1-based — atlas key uses the engine convention so
            # the renderer can look up `(slot, sub)` straight from the
            # parsed .dat layer entry.
            sub = frame_idx + 1
            # Partial bake — skip frames not referenced by the sector.
            # The STI decode above already paid for all this slot's
            # frames (load_8bit_sti returns the whole strip), but we
            # avoid pasting + tracking the unneeded ones, which is
            # where the render-phase win comes from.
            if needed_pairs is not None and (slot, sub) not in needed_pairs:
                continue
            sprites.append((slot, sub, pil.size[0], pil.size[1], ox, oy, pil))
    _emit({"event": "progress", "current": total_slots, "total": total_slots})

    # PHASE: pack. Row-pack sprites into atlas cells.
    _emit({"event": "phase", "phase": "pack",
           "label": f"Packing {len(sprites)} sprites"})
    cells: list[AtlasCell] = []
    cursor_x = 0
    cursor_y = 0
    row_h = 0
    for slot, sub, w, h, ox, oy, _pil in sprites:
        if cursor_x + w > ATLAS_MAX_WIDTH:
            cursor_x = 0
            cursor_y += row_h
            row_h = 0
        cells.append(AtlasCell(
            slot=slot, sub=sub,
            x=cursor_x, y=cursor_y, w=w, h=h,
            ox=ox, oy=oy,
        ))
        cursor_x += w
        if h > row_h:
            row_h = h
    atlas_w = ATLAS_MAX_WIDTH if cells else 1
    atlas_h = (cursor_y + row_h) if cells else 1

    # PHASE: render. The PIL.paste loop is fast (~100 ms even for 4k
    # sprites); the time you see in profile output also includes the
    # JSD harvest below, which is the actual ~7s bottleneck.
    _emit({"event": "phase", "phase": "render",
           "label": f"Rendering {atlas_w}×{atlas_h} atlas"})
    atlas_img = Image.new("RGBA", (atlas_w, atlas_h), (0, 0, 0, 0))
    for cell, (_slot, _sub, _w, _h, _ox, _oy, pil) in zip(cells, sprites):
        atlas_img.paste(pil, (cell.x, cell.y))

    # Slot filenames for the manifest.
    slot_filenames = {s: n for s, n in sorted_slots if n}

    # JSD harvest — name→bytes index of every .jsd file in the install.
    # Used by:
    #   - footprints phase (multi-tile stamp recipes — only useful for
    #     full bakes, gated below)
    #   - compute-zstrips phase (per-sub multi-tile detection — needed
    #     in BOTH full AND partial bakes so the engine-faithful wall
    #     clipping fires on the initial partial-render, not just after
    #     the complete bake swap completes ~2-3s later)
    # `_harvest_jsd_lookup` disk-caches by SLF/loose-dir mtimes — first
    # cold harvest is ~8.6s, subsequent runs hit cache in ~20 ms.
    jsd_lookup = _harvest_jsd_lookup(slf, loose, tileset, emit=_emit)

    # PHASE: footprints. Walk slots, resolve each to its JSD bytes via
    # the in-memory map, and (for multi-tile JSDs) build a stamp
    # recipe. Only the FIRST N footprint records get stamped (N = STI
    # frame count) because the JSD's remaining tile records are
    # passability-only — they don't need visible entries in the .dat;
    # the engine extends passability across them at load time from the
    # .jsd file alone. Reference: 2_HELI.STI has 3 frames but its JSD
    # lists ~32 footprint tiles, ~29 of which are passability-only.
    _emit({"event": "phase", "phase": "footprints",
           "label": "Indexing multi-tile struct footprints"})
    # Frame-count lookup from the cells we just packed — avoids
    # re-decoding STIs to count subs.
    frame_counts: dict[int, int] = {}
    for c in cells:
        if c.sub > frame_counts.get(c.slot, 0):
            frame_counts[c.slot] = c.sub
    slot_has_jsd: dict[int, bool] = {}
    slot_jsd_footprint: dict[int, JsdFootprint] = {}
    # Per-(slot, sub) multi-tile info from JSD's per-sub DB_STRUCTUREs.
    # Populated below alongside the footprint walk so the
    # compute-zstrips phase can iterate cells with O(1) lookup. Maps
    # (slot, sub_1based) → {nTiles, burns_through}. Cells whose
    # (slot, sub) isn't in this map are single-tile structures and skip
    # the Z-strip computation entirely.
    multitile_per_sub: dict[tuple[int, int], dict] = {}
    for slot, name in sorted_slots:
        if not name:
            continue
        jsd_name = name[:-4] + ".jsd" if name.lower().endswith(".sti") else name + ".jsd"
        pair = jsd_lookup.get(jsd_name.lower())
        if pair is None:
            slot_has_jsd[slot] = False
            continue
        slot_has_jsd[slot] = True
        jsd_bytes, jsd_src = pair
        try:
            parsed = _parse_jsd_bytes(jsd_bytes, Path(jsd_src), name)
        except Exception:  # noqa: BLE001
            continue  # malformed JSD — slot stays single-tile
        # Per-sub multi-tile detection. The first-DB_STRUCTURE summary
        # in `parsed` misses subs like lawless4 sub 16 (`nTiles=2`)
        # whose multi-tile-ness lives in a later DB_STRUCTURE record.
        # `_parse_jsd_substructures` walks all of them.
        try:
            sub_structs = _parse_jsd_substructures(jsd_bytes)
        except Exception:  # noqa: BLE001
            sub_structs = []
        slot_is_wall = bool(parsed.flags_int & 0x0001)
        for idx, ss in enumerate(sub_structs):
            if ss.get("nTiles", 0) > 1:
                multitile_per_sub[(slot, idx + 1)] = {
                    "nTiles": ss["nTiles"],
                    # The engine's MULTI_Z dispatch checks the
                    # tile-database-level WALL_TILE flag (not the JSD
                    # global flag). For the reference-install tilesets, the
                    # WALL_TILE element-range check correlates strongly
                    # with the JSD's WALL bit, so we use the JSD bit as
                    # a proxy. Frontend translates `burns_through` to
                    # depthFunc(LEQUAL) vs depthFunc(LESS).
                    "burns_through": slot_is_wall,
                }
        if parsed.ubNumberOfTiles <= 1:
            continue
        # Cap by STI frame count — emit at most one footprint tile
        # per visible sub. Bigger ubNumberOfTiles means the rest are
        # passability-only and the engine handles them on load.
        max_visible = frame_counts.get(slot, 0)
        if max_visible <= 1:
            continue
        n_visible = min(parsed.ubNumberOfTiles, max_visible)
        fp_tiles: list[JsdFootprintTile] = []
        for i in range(n_visible):
            t = parsed.tiles[i]
            fp_tiles.append(JsdFootprintTile(
                bX=t.bXPos, bY=t.bYPos, sub=i + 1,
            ))
        if fp_tiles:
            slot_jsd_footprint[slot] = JsdFootprint(tiles=fp_tiles)

    # PHASE: compute-zstrips. For each (slot, sub) cell whose JSD
    # DB_STRUCTURE has `nTiles > 1`, port the engine's per-frame
    # `AddZStripInfoToVObject` math and attach to `AtlasCell.zstrip`.
    # The engine fires this for ALL multi-tile structures (walls AND
    # non-walls — trees, furniture, vehicles); it only varies the
    # depth-comparison rule between the two via the dispatch at
    # `renderworld.cpp:2459` (non-wall, STRICT LESS) vs `:2450,2454`
    # (wall, LEQUAL BurnsThrough). The frontend reads
    # `zstrip.burns_through` to pick the right depthFunc.
    #
    # Cheap (~µs per cell — pure integer arithmetic on the STI's ETRLE
    # header values already captured at pack time). Skipped when
    # jsd_lookup is empty (partial bakes).
    if multitile_per_sub:
        _emit({"event": "phase", "phase": "compute-zstrips",
               "label": f"Computing Z-strips for {len(multitile_per_sub)} multi-tile sub-frames"})
        n_zstrips = 0
        n_burns = 0
        for cell in cells:
            info = multitile_per_sub.get((cell.slot, cell.sub))
            if info is None:
                continue
            zs = _compute_zstrip_for_frame(
                offset_x=cell.ox,
                offset_y=cell.oy,
                width=cell.w,
                is_mobile_or_corpse=False,  # static structures only
            )
            if zs is not None:
                zs.burns_through = bool(info["burns_through"])
                cell.zstrip = zs
                n_zstrips += 1
                if zs.burns_through:
                    n_burns += 1
        _log.info(
            "bake compute-zstrips tileset=%s multitile_subs=%d cells_with_zstrip=%d burns_through=%d",
            tileset, len(multitile_per_sub), n_zstrips, n_burns,
        )

    manifest = AtlasManifest(
        tileset=tileset,
        xml_path=str(xml_path),
        atlas_w=atlas_w,
        atlas_h=atlas_h,
        fingerprint=fp,
        cells=cells,
        slot_filenames=slot_filenames,
        slot_has_jsd=slot_has_jsd,
        slot_jsd_footprint=slot_jsd_footprint,
        complete=needed_pairs is None,
    )

    # PHASE: encode. PIL PNG encode is the slowest step after STI load.
    _emit({"event": "phase", "phase": "encode",
           "label": "Encoding atlas PNG"})
    buf = io.BytesIO()
    atlas_img.save(buf, format="PNG", optimize=False)
    png_bytes = buf.getvalue()

    # PHASE: persist. Write atlas + manifest to disk cache.
    _emit({"event": "phase", "phase": "persist",
           "label": f"Saving cache ({len(png_bytes) // 1024} KB)"})
    try:
        cache_dir.mkdir(parents=True, exist_ok=True)
        png_path.write_bytes(png_bytes)
        manifest_path.write_text(manifest.model_dump_json(), encoding="utf-8")
    except OSError:
        pass

    _bake_ms = (_time.perf_counter() - _bake_t0) * 1000
    _log.info(
        "bake DONE  mode=%s tileset=%s ms=%.0f cells=%d atlas=%dx%d png_kb=%d",
        _bake_mode, tileset, _bake_ms, len(manifest.cells),
        manifest.atlas_w, manifest.atlas_h, len(png_bytes) // 1024,
    )
    return png_bytes, manifest, cache_dir


@router.get("/tileset/atlas")
def tileset_atlas(
    xml: str = Query(..., description="Path to Ja2Set.dat.xml"),
    tileset: int = Query(..., description="Tileset index"),
    session_id: Optional[str] = Query(
        None,
        description=(
            "When set, serve a SECTOR-SPECIFIC partial atlas containing only "
            "the (slot, sub) pairs the session's sector references. Frontend "
            "uses this for fast first-render (~2 s vs ~11 s on cold full), "
            "then requests the full atlas without session_id in the "
            "background and hot-swaps via IsoRenderer.replaceAtlas."
        ),
    ),
):
    """Single PNG containing every (slot, sub) frame of every STI in the
    tileset, packed row-major. Companion to /tileset/atlas-manifest which
    returns the per-sprite atlas rects + engine offsets the frontend
    renderer needs.

    Cached to disk per (xml, tileset, slot-map fingerprint), or per
    (xml, tileset, fingerprint, sector-pairs-hash) for partial bakes."""
    _require_renderer()
    xml_path = _validate_path(xml, ".xml")
    needed_pairs: Optional[set[tuple[int, int]]] = None
    if session_id is not None:
        sess = _session_store.get(session_id)
        needed_pairs = _collect_used_pairs(sess.parsed)
    png_bytes, manifest, _cache_dir = _build_atlas(
        xml_path, tileset, needed_pairs=needed_pairs,
    )
    return Response(
        content=png_bytes,
        media_type="image/png",
        headers={
            # Long cache OK — fingerprint-keyed cache dir means a slot
            # map change generates a fresh URL via a different fingerprint.
            "Cache-Control": "max-age=86400",
            "Content-Length": str(len(png_bytes)),
            "X-MapForge-AtlasW": str(manifest.atlas_w),
            "X-MapForge-AtlasH": str(manifest.atlas_h),
            "X-MapForge-Fingerprint": manifest.fingerprint,
            "Access-Control-Expose-Headers":
                "X-MapForge-AtlasW, X-MapForge-AtlasH, X-MapForge-Fingerprint",
        },
    )


# ─── JSD parser ───────────────────────────────────────────────────────
# JA2 1.13 .jsd file format (from `Headless_Compiler/authoring/
# author_collision_jsd.py` + `Structure Internals.h`):
#
#   Header (16 bytes):
#     0..4   szId          — text identifier "J2SD" or similar
#     4..6   n_struct      — number of DB_STRUCTURE records (usually 1)
#     6..8   n_stored      — sub-count info
#     8..10  struct_data_size
#     10..11 fFlags        — global structure flags bit field
#     14..16 n_image_tile_locs
#
#   DB_STRUCTURE record (16 bytes, repeats n_struct times):
#     0      ubArmour
#     1      ubHP
#     2      ubDensity
#     3      ubNumberOfTiles
#     4..6   bZTileOffsetX, bZTileOffsetY (signed bytes)
#     ...    + more fields (we expose the useful ones)
#
#   DB_STRUCTURE_TILE record (32 bytes, repeats ubNumberOfTiles times):
#     0..2   sPosRelToBase (INT16)
#     2..3   bXPos (INT8)
#     3..4   bYPos (INT8)
#     4..29  PROFILE — 5x5 grid of UINT8 Z-occupancy masks
#     ...
#
# We surface a subset: footprint tiles + their PROFILE grids + the
# global structure flags + HP/armour/density. The full layout has
# pathfinding-only data the inspector doesn't need to display.

_JSD_FLAG_LABELS = [
    # Bit values from Structure Internals.h:60-121 (see
    # author_collision_jsd.py comments).
    (0x0001, "WALL"),
    (0x0002, "PARTIAL_WALL"),
    (0x0004, "ON_ROOF"),
    (0x0008, "ALL_ROOF"),
    (0x0010, "WIRE_FENCE"),
    (0x0020, "OPENABLE"),
    (0x0040, "PASSABLE"),
    (0x0080, "EXPLOSIVE"),
    (0x0100, "PERSON"),
    (0x0200, "BURNABLE"),
    (0x0400, "GAS_CLOUD"),
    (0x0800, "VEHICLE"),
    (0x1000, "TREE"),
    (0x2000, "BLOCKS_LIGHT"),
    (0x4000, "DROP_ITEM"),
    (0x8000, "STRUCTURE"),
]


class JsdProfileTile(BaseModel):
    """One footprint tile within a struct's JSD."""
    bXPos: int                 # signed offset from base tile (X)
    bYPos: int                 # signed offset from base tile (Y)
    sPosRelToBase: int         # 16-bit signed gridno offset from base
    profile: list[list[int]]   # 5x5 grid of Z-occupancy bytes


class JsdParsed(BaseModel):
    sti_filename: str
    jsd_path: str
    size_bytes: int
    szId: str
    n_struct: int
    n_stored: int
    struct_data_size: int
    n_image_tile_locs: int
    # Global structure flags. `flags_int` is the raw 16-bit value;
    # `flag_names` lists the bits that are set, for human display.
    flags_int: int
    flag_names: list[str]
    # First DB_STRUCTURE record (the typical case; multi-structure
    # JSDs are rare but exist in JA2 — vehicles, multi-room buildings).
    ubArmour: int
    ubHP: int
    ubDensity: int
    ubNumberOfTiles: int
    bZTileOffsetX: int
    bZTileOffsetY: int
    # Footprint tile records. Length == ubNumberOfTiles.
    tiles: list[JsdProfileTile]


def _find_jsd_bytes(xml_path: Path, tileset: int, sti_filename: str) -> Optional[tuple[bytes, str]]:
    """Locate + read the .jsd companion of `sti_filename`. Searches loose
    files first (Data-1.13/Tilesets/<tileset>/ then Tilesets/0/), then
    falls back to the SLF archives. Returns (bytes, source_path) or
    None when no JSD exists.

    Many JA2 1.13 JSDs live INSIDE Tilesets.slf rather than as loose
    files — looking only at loose paths reports has_jsd=False for half
    the struct slots. The Python iso_renderer's StiCache uses the same
    dual search; this mirrors it for JSDs."""
    # Asset roots: the active install's tileset dirs across its VFS layers.
    loose, slf_paths = _tileset_paths_for(xml_path)
    jsd_name = sti_filename[:-4] + ".jsd" if sti_filename.lower().endswith(".sti") else sti_filename + ".jsd"
    jsd_target = jsd_name.lower()
    # 1. Loose
    for base in loose:
        if not base.exists():
            continue
        for sub in (str(tileset), "0"):
            for variant in (jsd_name, jsd_name.upper(), jsd_name.lower()):
                p = base / sub / variant
                if p.is_file():
                    return p.read_bytes(), str(p)
    # 2. SLF — walk all candidate archives, find any file whose
    # basename matches case-insensitively. Most JSDs are in
    # Tilesets.slf at a path like `/Tilesets/N/NAME.jsd`.
    try:
        from ja2py.fileformats.SlfFS import SlfFS  # noqa: E402
    except ImportError:
        return None
    for slf in slf_paths:
        if not slf.exists():
            continue
        try:
            fs = SlfFS(str(slf))
            for path in fs.walk.files():
                if os.path.basename(path).lower() == jsd_target:
                    return (fs.readbytes(path),
                            f"slf://{slf}!{path}")
        except Exception:  # noqa: BLE001
            continue
    return None


def _parse_jsd_bytes(data: bytes, jsd_path: Path, sti_filename: str) -> JsdParsed:
    """Parse a .jsd into a structured representation. Best-effort —
    JSD format has more fields than we surface; we extract the ones
    the inspector renders. See `_JSD_FLAG_LABELS` for fFlags decoding."""
    import struct as _struct
    if len(data) < 16:
        raise HTTPException(400, {"error": "JSD_TOO_SHORT",
            "message": f"{jsd_path.name} is {len(data)} bytes, need ≥ 16"})
    sz_id = data[:4].decode("latin-1", errors="replace").rstrip("\x00")
    n_struct, n_stored, struct_data_size = _struct.unpack("<HHH", data[4:10])
    fflags = _struct.unpack("<H", data[10:12])[0]
    n_image_tile_locs = _struct.unpack("<H", data[14:16])[0]

    # First DB_STRUCTURE (16 bytes at offset 16)
    if len(data) < 32:
        raise HTTPException(400, {"error": "JSD_MISSING_STRUCT",
            "message": f"{jsd_path.name} too short for DB_STRUCTURE"})
    o = 16
    ubArmour = data[o]
    ubHP = data[o + 1]
    ubDensity = data[o + 2]
    ubNumberOfTiles = data[o + 3]
    bZTileOffsetX = _struct.unpack("<b", data[o + 4:o + 5])[0]
    bZTileOffsetY = _struct.unpack("<b", data[o + 5:o + 6])[0]

    flag_names = [name for bit, name in _JSD_FLAG_LABELS if fflags & bit]

    # DB_STRUCTURE_TILE records start RIGHT AFTER the first DB_STRUCTURE
    # header (at offset 32 for single-structure JSDs; multi-structure
    # JSDs interleave: header, its tiles, next header, its tiles, etc.).
    #
    # Previous bug: this used `tile_base = 16 + n_struct * 16`, which
    # treated all DB_STRUCTURE headers as a contiguous block before any
    # tiles. That's wrong — for an n_struct=8 JSD (e.g. mdrock.jsd) the
    # parser would start reading "tiles" at offset 144, deep inside the
    # PROFILE voxel grid of structure 0's last real tile. Random bytes
    # there get interpreted as sPos/bX/bY, producing nonsense footprint
    # offsets like (+97, +1). That nonsense propagated all the way to
    # stamp paint, which then dropped a struct entry 100+ tiles away
    # from the click point. Verified 2026-05-23 by hex-decoding
    # mdrock.jsd: real footprint is the expected 2×2 rock pattern.
    #
    # We only surface the FIRST DB_STRUCTURE's tiles (the primary
    # visible structure). Multi-structure JSDs exist for animated /
    # variant tiles but the painter / inspector only cares about the
    # primary footprint for stamp expansion.
    tiles: list[JsdProfileTile] = []
    tile_base = 16 + 16  # header (16) + first DB_STRUCTURE header (16)
    for i in range(ubNumberOfTiles):
        to = tile_base + i * 32
        if to + 29 > len(data):
            break
        sPos, bX, bY = _struct.unpack("<hbb", data[to:to + 4])
        profile_flat = data[to + 4:to + 29]  # 25 bytes
        profile = [list(profile_flat[y * 5:(y + 1) * 5]) for y in range(5)]
        tiles.append(JsdProfileTile(
            bXPos=bX, bYPos=bY, sPosRelToBase=sPos, profile=profile,
        ))

    return JsdParsed(
        sti_filename=sti_filename,
        jsd_path=str(jsd_path),
        size_bytes=len(data),
        szId=sz_id,
        n_struct=n_struct,
        n_stored=n_stored,
        struct_data_size=struct_data_size,
        n_image_tile_locs=n_image_tile_locs,
        flags_int=fflags,
        flag_names=flag_names,
        ubArmour=ubArmour,
        ubHP=ubHP,
        ubDensity=ubDensity,
        ubNumberOfTiles=ubNumberOfTiles,
        bZTileOffsetX=bZTileOffsetX,
        bZTileOffsetY=bZTileOffsetY,
        tiles=tiles,
    )


def _parse_jsd_substructures(data: bytes) -> list[dict]:
    """Walk every DB_STRUCTURE record in a JSD and return a list of
    dicts (one per record) with the fields the Z-strip detector needs:
    `{nTiles, flags_int, bZTileOffsetX, bZTileOffsetY}`.

    Unlike `_parse_jsd_bytes` (which surfaces only the FIRST
    DB_STRUCTURE for the inspector), this walks ALL of them. Critical
    for STIs like lawless4.sti where each sub-frame has its OWN
    DB_STRUCTURE: sub 1 is single-tile but sub 16 is `nTiles=2`. The
    engine's `AddZStripInfoToVObject` (structure.cpp:2152) triggers
    per-sub on `pDBStructure->ubNumberOfTiles > 1`, so we need the
    per-sub info to mirror it.

    Sub-frame N (1-based) maps to the Nth DB_STRUCTURE in the JSD when
    `n_struct == n_stored`. When they differ, the engine deduplicates
    via a sub→struct index table we don't currently parse — for those
    JSDs the mapping is approximate. Best-effort; degrades to "treat
    all subs as having the first struct's nTiles" for badly-truncated
    or oddly-formed JSDs.

    Each DB_STRUCTURE record is 16 bytes followed by `nTiles × 32`
    bytes of DB_STRUCTURE_TILE records. We skip the tile records to
    get to the next header."""
    import struct as _struct
    if len(data) < 16:
        return []
    n_struct = _struct.unpack("<H", data[4:6])[0]
    o = 16
    out: list[dict] = []
    for _ in range(n_struct):
        if o + 16 > len(data):
            break
        nTiles = data[o + 3]
        bZTOX = _struct.unpack("<b", data[o + 4:o + 5])[0]
        bZTOY = _struct.unpack("<b", data[o + 5:o + 6])[0]
        # fFlags at offset 8-10 from the structure header. Different
        # from the JSD global flags at file offset 10-12.
        sub_flags = _struct.unpack("<H", data[o + 8:o + 10])[0]
        out.append({
            "nTiles": nTiles,
            "flags_int": sub_flags,
            "bZTileOffsetX": bZTOX,
            "bZTileOffsetY": bZTOY,
        })
        # Advance past the header + its tile records.
        o += 16 + nTiles * 32
    return out


@router.get("/sti/jsd", response_model=JsdParsed)
def sti_jsd(
    xml: str = Query(..., description="Path to Ja2Set.dat.xml"),
    tileset: int = Query(..., description="Tileset index"),
    slot: int = Query(..., description="Slot whose STI's JSD to read"),
):
    """Parse the .jsd companion of a slot's STI. Returns the
    structured representation (header + first DB_STRUCTURE + footprint
    tiles with PROFILE grids). Used by the tile inspector's JSD
    viewer to surface multi-tile footprint + passability flags.

    404 when the slot has no .jsd companion (e.g., a pure-decorative
    sprite). The atlas manifest's `slot_has_jsd` map is the cheap way
    to know which slots have one before calling this."""
    _require_renderer()
    xml_path = _validate_path(xml, ".xml")
    slot_map = load_tileset_xml(xml_path, tileset)
    name = slot_map.get(slot)
    if not name:
        raise HTTPException(404, {"error": "SLOT_NOT_DEFINED",
            "message": f"slot {slot} not in tileset {tileset}"})
    found = _find_jsd_bytes(xml_path, tileset, name)
    if found is None:
        raise HTTPException(404, {"error": "JSD_NOT_FOUND",
            "message": f"no .jsd companion for {name} in tileset {tileset}"})
    data, source = found
    return _parse_jsd_bytes(data, Path(source), name)


# ─── JSD edit body (Tileset Editor full-JSD-editor scope) ────────────
# Patch fields are all Optional — set the ones you want to change,
# leave the rest None. The writer reads original bytes, applies only
# the requested patches, and writes back. Bytes outside the patched
# spans are byte-identical to the input (the parser only surfaces a
# subset of the JSD, and we don't touch what we don't surface).

class JsdTileEdit(BaseModel):
    """One footprint-tile patch. `index` is the position in the
    DB_STRUCTURE_TILE array (0..ubNumberOfTiles-1). Each field is
    independently optional — only the ones you set get patched."""
    index: int
    bXPos: Optional[int] = None       # signed int8
    bYPos: Optional[int] = None       # signed int8
    sPosRelToBase: Optional[int] = None  # signed int16
    # 5x5 grid of unsigned bytes 0..255. If provided, MUST be exactly
    # 5 rows of 5 bytes each — the writer flattens to 25 bytes.
    profile: Optional[list[list[int]]] = None


class JsdEditBody(BaseModel):
    xml: str
    tileset: int
    slot: int
    fflags: Optional[int] = None      # UINT16 raw value
    ubArmour: Optional[int] = None    # UINT8
    ubHP: Optional[int] = None        # UINT8
    ubDensity: Optional[int] = None   # UINT8
    bZTileOffsetX: Optional[int] = None  # INT8
    bZTileOffsetY: Optional[int] = None  # INT8
    tiles: Optional[list[JsdTileEdit]] = None


class JsdEditResult(BaseModel):
    sti_filename: str
    jsd_path: str
    bytes_written: int
    backup_path: Optional[str] = None
    # Echo of the updated parsed JSD so the frontend doesn't need a
    # round-trip GET to refresh its state.
    parsed: JsdParsed


@router.put("/sti/jsd", response_model=JsdEditResult)
def update_sti_jsd(body: JsdEditBody):
    """Write patched JSD bytes back to disk.

    Strategy: read original bytes, apply only the requested spans
    (header fields + per-tile triples + 5x5 profile grids), write back.
    Every byte outside the patched spans is preserved byte-for-byte
    so the unread parts of the JSD (multi-structure interleaved data,
    PROFILE voxel grids beyond the first, padding) stay intact.

    Refuses with 409 JSD_IN_SLF when the slot's JSD lives only inside
    an SLF archive — same as the inject-sub flow, since we can't write
    back to SLF without extracting first.

    Backs up the original at `<file>.jsd.bak` on first write per
    process. Idempotent: existing `.bak` is left alone (matches the
    `.mwbak` convention).
    """
    import struct as _struct
    _require_renderer()
    xml_path = _validate_path(body.xml, ".xml")
    slot_map = load_tileset_xml(xml_path, body.tileset)
    name = slot_map.get(body.slot)
    if not name:
        raise HTTPException(404, {
            "error": "SLOT_NOT_DEFINED",
            "message": f"slot {body.slot} not in tileset {body.tileset}",
        })
    found = _find_jsd_bytes(xml_path, body.tileset, name)
    if found is None:
        raise HTTPException(404, {
            "error": "JSD_NOT_FOUND",
            "message": f"no .jsd companion for {name} in tileset {body.tileset}",
        })
    data, source = found
    if source.startswith("slf://"):
        raise HTTPException(409, {
            "error": "JSD_IN_SLF",
            "message": (
                f"{name}'s .jsd lives inside an SLF archive ({source}) — "
                "can't write back to SLF. Extract the slot's STI+JSD to a "
                "loose file first, or use a different slot whose JSD is "
                "already on disk."
            ),
        })
    jsd_path = Path(source)
    if not jsd_path.is_file():
        raise HTTPException(404, {
            "error": "JSD_PATH_GONE",
            "message": f"resolved JSD path {jsd_path} is not a file",
        })

    # Parse first so we know ubNumberOfTiles + can sanity-check tile
    # indices in the edit body. Re-uses the existing parser.
    parsed_before = _parse_jsd_bytes(data, jsd_path, name)
    num_tiles = parsed_before.ubNumberOfTiles

    # Build the mutable byte array. bytearray is the right type — we
    # need in-place mutation of arbitrary spans.
    buf = bytearray(data)

    # Header field patches at fixed offsets (matching _parse_jsd_bytes).
    if body.fflags is not None:
        if not (0 <= body.fflags <= 0xFFFF):
            raise HTTPException(400, {
                "error": "FFLAGS_OUT_OF_RANGE",
                "message": f"fflags {body.fflags} must fit in UINT16 (0..65535)",
            })
        buf[10:12] = _struct.pack("<H", body.fflags)

    # DB_STRUCTURE at offset 16 (first one). Bytes:
    #   16: ubArmour    (UINT8)
    #   17: ubHP        (UINT8)
    #   18: ubDensity   (UINT8)
    #   19: ubNumberOfTiles (UINT8 — read-only here; size change not supported)
    #   20: bZTileOffsetX (INT8)
    #   21: bZTileOffsetY (INT8)
    def _u8(name: str, v: int) -> int:
        if not (0 <= v <= 255):
            raise HTTPException(400, {
                "error": "U8_OUT_OF_RANGE",
                "message": f"{name} {v} must fit in UINT8 (0..255)",
            })
        return v

    def _i8(name: str, v: int) -> int:
        if not (-128 <= v <= 127):
            raise HTTPException(400, {
                "error": "I8_OUT_OF_RANGE",
                "message": f"{name} {v} must fit in INT8 (-128..127)",
            })
        return v

    if body.ubArmour is not None:
        buf[16] = _u8("ubArmour", body.ubArmour)
    if body.ubHP is not None:
        buf[17] = _u8("ubHP", body.ubHP)
    if body.ubDensity is not None:
        buf[18] = _u8("ubDensity", body.ubDensity)
    # ubNumberOfTiles intentionally not editable — changing it would
    # require resizing the file. Out of scope per the doc.
    if body.bZTileOffsetX is not None:
        buf[20:21] = _struct.pack("<b", _i8("bZTileOffsetX", body.bZTileOffsetX))
    if body.bZTileOffsetY is not None:
        buf[21:22] = _struct.pack("<b", _i8("bZTileOffsetY", body.bZTileOffsetY))

    # Footprint tile patches. tile_base mirrors _parse_jsd_bytes:
    #   16 (header) + 16 (first DB_STRUCTURE) = 32
    # Each tile is 32 bytes; we patch bytes [0..29] (3-byte position
    # triple + 25-byte profile grid). Bytes [29..32] are preserved.
    TILE_BASE = 32
    TILE_STRIDE = 32
    if body.tiles is not None:
        for patch in body.tiles:
            if patch.index < 0 or patch.index >= num_tiles:
                raise HTTPException(400, {
                    "error": "TILE_INDEX_OUT_OF_RANGE",
                    "message": (
                        f"tile patch index {patch.index} out of range "
                        f"(JSD has {num_tiles} footprint tiles)"
                    ),
                })
            to = TILE_BASE + patch.index * TILE_STRIDE
            # Sanity: every tile we touch must fit in the file.
            if to + 29 > len(buf):
                raise HTTPException(500, {
                    "error": "JSD_TRUNCATED",
                    "message": (
                        f"JSD too short for tile {patch.index} "
                        f"(need bytes {to}..{to + 29}, have {len(buf)})"
                    ),
                })
            if patch.sPosRelToBase is not None:
                if not (-32768 <= patch.sPosRelToBase <= 32767):
                    raise HTTPException(400, {
                        "error": "I16_OUT_OF_RANGE",
                        "message": (
                            f"sPosRelToBase {patch.sPosRelToBase} must fit in INT16"
                        ),
                    })
                buf[to:to + 2] = _struct.pack("<h", patch.sPosRelToBase)
            if patch.bXPos is not None:
                buf[to + 2:to + 3] = _struct.pack("<b", _i8("bXPos", patch.bXPos))
            if patch.bYPos is not None:
                buf[to + 3:to + 4] = _struct.pack("<b", _i8("bYPos", patch.bYPos))
            if patch.profile is not None:
                if len(patch.profile) != 5 or any(len(r) != 5 for r in patch.profile):
                    raise HTTPException(400, {
                        "error": "PROFILE_WRONG_SHAPE",
                        "message": (
                            f"profile for tile {patch.index} must be 5x5, "
                            f"got {len(patch.profile)}x"
                            f"{len(patch.profile[0]) if patch.profile else 0}"
                        ),
                    })
                flat = bytearray(25)
                for r in range(5):
                    for c in range(5):
                        v = patch.profile[r][c]
                        if not (0 <= v <= 255):
                            raise HTTPException(400, {
                                "error": "PROFILE_BYTE_OUT_OF_RANGE",
                                "message": (
                                    f"profile[{r}][{c}] = {v} must be 0..255"
                                ),
                            })
                        flat[r * 5 + c] = v
                buf[to + 4:to + 29] = bytes(flat)

    # Backup-then-write. The .bak convention is idempotent — never
    # overwrite an existing backup, so the FIRST pre-edit state is the
    # one preserved.
    #
    # Refuses the write when the backup can't be taken — JSD corruption
    # is one of the engine-CTD risks (malformed sub-image profile data
    # makes the renderer dereference past the buffer), so silently
    # proceeding with `backup_path: null + ok: true` per the pre-fix
    # behavior was actively dangerous. Mirrors the inject-sub flow's
    # 500 BACKUP_FAILED pattern at mapforge.py:2456. TODO #7 fix.
    backup_path: Optional[Path] = jsd_path.with_suffix(jsd_path.suffix + ".bak")
    backup_emitted: Optional[str] = None
    if backup_path is not None and not backup_path.exists():
        try:
            backup_path.write_bytes(data)  # original bytes, pre-patch
            backup_emitted = str(backup_path)
        except OSError as e:
            raise HTTPException(500, {
                "error": "BACKUP_FAILED",
                "message": (
                    f"could not write {backup_path}: {e}. Refusing the "
                    "JSD overwrite — corrupting JSD without a recovery "
                    "path can CTD the engine at sub-image render time."
                ),
            })
    elif backup_path is not None:
        backup_emitted = str(backup_path)  # pre-existing backup

    new_bytes = bytes(buf)
    try:
        jsd_path.write_bytes(new_bytes)
    except OSError as e:
        raise HTTPException(500, {
            "error": "JSD_WRITE_FAILED",
            "message": f"could not write {jsd_path}: {e}",
        })

    parsed_after = _parse_jsd_bytes(new_bytes, jsd_path, name)
    return JsdEditResult(
        sti_filename=name,
        jsd_path=str(jsd_path),
        bytes_written=len(new_bytes),
        backup_path=backup_emitted,
        parsed=parsed_after,
    )


@router.get("/tileset/atlas-manifest", response_model=AtlasManifest)
def tileset_atlas_manifest(
    xml: str = Query(...),
    tileset: int = Query(...),
    session_id: Optional[str] = Query(
        None,
        description=(
            "When set, return the manifest for the SECTOR-SPECIFIC partial "
            "atlas (companion to /tileset/atlas with the same param). "
            "Manifest carries `complete: False` so the frontend knows to "
            "fetch the full atlas in the background."
        ),
    ),
):
    """Per-sprite metadata for the tileset atlas: each cell's (slot, sub)
    key, atlas pixel rect (x, y, w, h), and the STI's own offset_x /
    offset_y (engine INT16 semantics — already sign-corrected).

    Frontend builds a Map<slotXsub, AtlasCell> once on load and looks up
    cells per drawImage call. Cached together with the atlas PNG."""
    _require_renderer()
    xml_path = _validate_path(xml, ".xml")
    needed_pairs: Optional[set[tuple[int, int]]] = None
    if session_id is not None:
        sess = _session_store.get(session_id)
        needed_pairs = _collect_used_pairs(sess.parsed)
    _png_bytes, manifest, _cache_dir = _build_atlas(
        xml_path, tileset, needed_pairs=needed_pairs,
    )
    return manifest


@router.get("/tileset/atlas/build")
def tileset_atlas_build(
    xml: str = Query(..., description="Path to Ja2Set.dat.xml"),
    tileset: int = Query(..., description="Tileset index"),
    session_id: Optional[str] = Query(
        None,
        description=(
            "When set, stream progress for a PARTIAL bake (only sprites "
            "the session's sector uses). Mirrors the session_id param on "
            "/tileset/atlas + /tileset/atlas-manifest."
        ),
    ),
):
    """NDJSON stream of bake progress events. Call BEFORE GET /tileset/atlas
    so the subsequent atlas fetch hits the disk cache instantly while
    showing real progress during the slow bake.

    Cold bake breakdown (typical reference-install tileset):
      - check-cache:   1-50 ms
      - load-stis:    1-5 seconds (DOMINANT; ~150 SLF-bundled STIs)
      - pack:         10-50 ms
      - render:       50-200 ms
      - encode:       100-500 ms PNG encode
      - persist:      50-200 ms file write
    Warm bake (cache hit): emits cache-hit phase in <50 ms total.

    Why a separate endpoint instead of streaming progress headers on
    /tileset/atlas: HTTP can't update response headers mid-body, and
    mixing NDJSON progress with the binary PNG payload in one response
    would force ugly framing. Two endpoints keeps each one's response
    format clean.

    Events emitted (NDJSON, one JSON per line):
      {"event": "phase",     "phase": <id>, "label": <human-text>}
      {"event": "progress",  "current": <int>, "total": <int>, "detail": <str>}
      {"event": "done",      "atlas_w": <int>, "atlas_h": <int>,
                             "fingerprint": <str>, "cached": <bool>}
    """
    _require_renderer()
    xml_path = _validate_path(xml, ".xml")
    needed_pairs: Optional[set[tuple[int, int]]] = None
    if session_id is not None:
        sess = _session_store.get(session_id)
        needed_pairs = _collect_used_pairs(sess.parsed)
    import json
    import queue
    import threading

    # The bake runs synchronously and emits events via a callback.
    # We can't yield events from the streaming response while a
    # blocking _build_atlas call is running on the same thread — so
    # run the bake in a worker thread, drain events through a queue.
    event_queue: "queue.Queue[Optional[dict]]" = queue.Queue()
    result_holder: dict = {}

    def emit(evt: dict) -> None:
        event_queue.put(evt)

    def worker() -> None:
        try:
            png_bytes, manifest, _cache_dir = _build_atlas(
                xml_path, tileset, emit=emit, needed_pairs=needed_pairs,
            )
            result_holder["manifest"] = manifest
            result_holder["png_size"] = len(png_bytes)
        except Exception as e:  # noqa: BLE001
            result_holder["error"] = f"{type(e).__name__}: {e}"
        finally:
            event_queue.put(None)  # sentinel — close stream

    threading.Thread(target=worker, daemon=True).start()

    def gen():
        while True:
            evt = event_queue.get()
            if evt is None:
                # Worker finished — emit the final done event with the
                # baked atlas's dimensions, then exit.
                if "error" in result_holder:
                    yield (json.dumps({
                        "event": "error",
                        "message": result_holder["error"],
                    }) + "\n").encode("utf-8")
                else:
                    m = result_holder["manifest"]
                    yield (json.dumps({
                        "event": "done",
                        "atlas_w": m.atlas_w,
                        "atlas_h": m.atlas_h,
                        "fingerprint": m.fingerprint,
                        "png_size": result_holder.get("png_size", 0),
                    }) + "\n").encode("utf-8")
                break
            yield (json.dumps(evt) + "\n").encode("utf-8")

    return StreamingResponse(gen(), media_type="application/x-ndjson",
                              headers={"Cache-Control": "no-store"})


# ─── Session parsed-dict export (Phase 3 client-side renderer) ─────────
# The client-side iso renderer needs the full parsed dict in JSON form
# so it can iterate per-tile layer arrays in the browser. We serialize a
# minimal subset — only the fields the renderer (and the existing tile
# inspector) actually reads — to keep payload size sane.

class ParsedLayerEntries(BaseModel):
    """Compact representation of per-tile layer entries. Each tile is a
    flat array of slot/sub pairs (stored as [s,u] couples). Empty tiles
    are an empty list. Total payload for a 160×160 sector with average
    building density is ~500-2000 KB JSON."""
    # Top-level shape: list[world_max] of list[[slot, sub], ...].
    # Pydantic can validate but the size makes the type signature noisy;
    # we keep it as Any and the frontend types it as `number[][][]`.


class ParsedSector(BaseModel):
    session_id: str
    rows: int
    cols: int
    tileset: int
    # 6 layer arrays. Each = list[rows*cols] of list[[slot,sub], ...].
    land: list[list[list[int]]]
    objs: list[list[list[int]]]
    shadows: list[list[list[int]]]
    structs: list[list[list[int]]]
    roofs: list[list[list[int]]]
    onroofs: list[list[list[int]]]
    # Per-tile scalars
    rooms: list[int]
    heights: list[int]
    world_flags: list[int]
    # Session counter — frontend can compare to detect stale parsed
    # snapshots (e.g. if another tab opened the same session and edited).
    edit_count: int
    dirty: bool


class AppendixItem(BaseModel):
    gridno: int
    x: int
    y: int
    usItem: int
    level: int

class AppendixEntryPoint(BaseModel):
    kind: str
    gridno: int
    x: int
    y: int

class AppendixExitGrid(BaseModel):
    gridno: int
    x: int
    y: int
    dest_gridno: int
    sx: int
    sy: int
    sz: int

class AppendixSoldier(BaseModel):
    gridno: int
    x: int
    y: int
    team: int
    team_label: str
    facing: int
    soldier_class: int

class AppendixLight(BaseModel):
    x: int
    y: int
    gridno: int
    template: str

class AppendixDoor(BaseModel):
    gridno: int
    x: int
    y: int
    locked: bool

class AppendixEdgepoint(BaseModel):
    gridno: int
    x: int
    y: int
    edge: str

class AppendixSchedule(BaseModel):
    gridno: int
    x: int
    y: int
    schedule_id: int
    action: int

class AppendixEntities(BaseModel):
    session_id: str
    rows: int
    cols: int
    items: list[AppendixItem]
    entry_points: list[AppendixEntryPoint]
    exit_grids: list[AppendixExitGrid]
    soldiers: list[AppendixSoldier]
    lights: list[AppendixLight]
    doors: list[AppendixDoor]
    edgepoints: list[AppendixEdgepoint]
    schedules: list[AppendixSchedule]
    reached: list[str]
    blocked_at: str | None


def _serialize_layer(layer: list[list[tuple[int, int]]]) -> list[list[list[int]]]:
    """Convert list-of-tuples to list-of-lists for JSON. Tuples are not
    JSON-serializable by default; pydantic does this conversion anyway
    but doing it explicitly is faster + lets us share the type. Slot+sub
    are small ints so we don't need any compression scheme."""
    return [[[s, u] for s, u in tile] for tile in layer]


@router.get("/sessions/{session_id}/appendix", response_model=AppendixEntities)
def session_appendix(session_id: str):
    """Read-only positioned appendix entities (items / entry points / exit
    grids / lights) for the tactical overlay. Extracted from the on-disk bytes;
    never written. Later sections (soldiers, doors, edgepoints) report via
    `blocked_at` until their parsers land."""
    sess = _session_store.get(session_id)
    ents = extract_appendix_entities(sess.original_bytes, sess.parsed)
    return AppendixEntities(session_id=sess.id, **ents)


@router.get("/sessions/{session_id}/parsed", response_model=ParsedSector)
def session_parsed(session_id: str):
    """Full parsed sector data for the client-side renderer.

    Returned ONCE per session open — the frontend holds the data and
    mutates it locally on edits (then sends edits to backend via
    /edits). On any divergence the frontend can re-fetch.

    Payload size: ~500 KB to ~3 MB for typical 160×160 sectors,
    depending on building density. Single large response is faster than
    streaming + simpler to reason about."""
    _require_renderer()
    sess = _session_store.get(session_id)
    p = sess.parsed
    return ParsedSector(
        session_id=sess.id,
        rows=p["rows"],
        cols=p["cols"],
        tileset=sess.tileset,
        land=_serialize_layer(p["land"]),
        objs=_serialize_layer(p["objs"]),
        shadows=_serialize_layer(p["shadows"]),
        structs=_serialize_layer(p["structs"]),
        roofs=_serialize_layer(p["roofs"]),
        onroofs=_serialize_layer(p["onroofs"]),
        rooms=list(p["rooms"]),
        heights=list(p["heights"]),
        world_flags=list(p["world_flags"]),
        edit_count=sess.edit_count,
        dirty=sess.dirty,
    )
