"""Standalone modder utilities — STI Viewer + SLF Extractor.

These tools operate on arbitrary files the user picks via the desktop
shell's file dialog, independent of any active install or registered
tileset. They cover the cases the embedded surfaces don't:

  - The asset-palette STI viewer only sees STIs registered in the active
    install's Ja2Set.dat.xml.
  - The library catalog only sees STIs ingested into the Asset Browser
    DB.

A modder who has a loose .sti sitting in a download folder, or wants to
crack open a third-party .slf to see what it carries, needs neither of
those surfaces — just a "open this file → show me what's inside" view.

Path safety: every path arg is validated to exist and have the expected
suffix before any decode/read happens. We deliberately do NOT restrict
to the active install's tree because the explicit user-pick is the
authorization gate — Tauri's dialog requires user interaction.
"""
from __future__ import annotations

import io
import json
import os
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import Response, StreamingResponse
from pydantic import BaseModel

from mercwizard_core.sti_decode import decode_sti_frame_to_png


router = APIRouter(prefix="/tools")


# ─── Path validation ─────────────────────────────────────────────────
# Modeled on _validate_path in routes/mapforge.py. We don't constrain
# to allowed dirs — the file picker IS the allowed-dirs gate.

def _validate_file(raw: str, suffix: str) -> Path:
    """Resolve `raw` to an absolute file path with the expected suffix.

    Rejects:
      - empty / missing
      - path that doesn't point at an existing file
      - wrong extension (case-insensitive)

    Returns the resolved Path on success.
    """
    if not raw:
        raise HTTPException(
            status_code=400,
            detail={"error": "PATH_REQUIRED", "message": "path is empty"},
        )
    p = Path(raw)
    if not p.is_file():
        raise HTTPException(
            status_code=404,
            detail={"error": "FILE_NOT_FOUND", "message": f"{raw} not found"},
        )
    if p.suffix.lower() != suffix.lower():
        raise HTTPException(
            status_code=400,
            detail={"error": "BAD_SUFFIX",
                    "message": f"Expected {suffix}, got {p.suffix}"},
        )
    return p


# ─── STI viewer models ────────────────────────────────────────────────

class StiFrameInfo(BaseModel):
    index: int
    width: int
    height: int
    offset_x: int      # signed; ja2py stores as UINT16 but engine reads INT16
    offset_y: int


class StiViewerMeta(BaseModel):
    path: str
    size_bytes: int
    is_8bit: bool
    width: int                 # canvas width from STI header
    height: int                # canvas height from STI header
    frame_count: int
    palette_present: bool
    has_jsd: bool
    jsd_path: Optional[str] = None
    frames: list[StiFrameInfo]


# ─── STI Viewer endpoints ─────────────────────────────────────────────

def _read_offset_signed(raw: int) -> int:
    """Convert ja2py's UINT16 offset to engine-canonical INT16.

    StiSubImageHeader declares offset_x/y as 'H' (UINT16), but the
    engine reads them as INT16. The on-disk bytes are identical
    (two's complement); only the Python interpretation differs.
    """
    if raw >= 32768:
        return raw - 65536
    return raw


@router.get("/sti/decode", response_model=StiViewerMeta)
def sti_decode(
    path: str = Query(..., description="Absolute path to a .sti file"),
):
    """Return STI metadata: canvas size, frame count, per-frame size +
    signed offsets, and JSD presence at `<path>.jsd`. Does NOT return
    pixel data — use /tools/sti/frame for individual frames.
    """
    sti_path = _validate_file(path, ".sti")
    try:
        from ja2py.fileformats.Sti import (  # type: ignore
            is_8bit_sti,
            load_8bit_sti,
            StiHeader,
        )
    except Exception as e:  # noqa: BLE001
        raise HTTPException(
            status_code=500,
            detail={"error": "STI_LIB_MISSING",
                    "message": f"{type(e).__name__}: {e}"},
        )

    size_bytes = sti_path.stat().st_size
    # We expose 8-bit STIs only — 16-bit STIs don't carry per-frame
    # sub-image headers in the way the viewer expects, and JA2's portrait
    # / sprite pipeline uses 8-bit exclusively. We still report `is_8bit`
    # so the frontend can show "16-bit STI, frame view unsupported" rather
    # than a generic error.
    with open(sti_path, "rb") as f:
        head = f.read(StiHeader.get_size())
    try:
        header = StiHeader.from_bytes(head)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(
            status_code=400,
            detail={"error": "STI_HEADER_BAD",
                    "message": f"{type(e).__name__}: {e}"},
        )

    width = int(header["width"])
    height = int(header["height"])

    with open(sti_path, "rb") as f:
        try:
            is8 = is_8bit_sti(f)
        except Exception:
            is8 = False

    frames: list[StiFrameInfo] = []
    palette_present = False
    if is8:
        try:
            images = load_8bit_sti(str(sti_path))
        except Exception as e:  # noqa: BLE001
            raise HTTPException(
                status_code=400,
                detail={"error": "STI_DECODE_FAILED",
                        "message": f"{type(e).__name__}: {e}"},
            )
        palette_present = images.palette is not None
        for i, sub in enumerate(images.images):
            ox, oy = sub.offsets
            frames.append(StiFrameInfo(
                index=i,
                width=int(sub.image.size[0]),
                height=int(sub.image.size[1]),
                offset_x=_read_offset_signed(int(ox)),
                offset_y=_read_offset_signed(int(oy)),
            ))

    # Check for sibling .jsd companion. JA2 stores JSD with the same
    # stem as the STI; the viewer can render it read-only when present.
    jsd_candidate = sti_path.with_suffix(".jsd")
    has_jsd = jsd_candidate.is_file()
    # Try a few case variants — Windows filesystems are case-insensitive
    # but the bundle may have been authored on Linux.
    if not has_jsd:
        for variant in (".JSD", ".Jsd"):
            cand = sti_path.with_suffix(variant)
            if cand.is_file():
                jsd_candidate = cand
                has_jsd = True
                break

    return StiViewerMeta(
        path=str(sti_path),
        size_bytes=size_bytes,
        is_8bit=is8,
        width=width,
        height=height,
        frame_count=len(frames),
        palette_present=palette_present,
        has_jsd=has_jsd,
        jsd_path=str(jsd_candidate) if has_jsd else None,
        frames=frames,
    )


@router.get("/sti/frame")
def sti_frame(
    path: str = Query(..., description="Absolute path to a .sti file"),
    frame: int = Query(0, ge=0, description="0-based frame index"),
):
    """Return one decoded STI frame as PNG bytes.

    Frame index is 0-based — same convention as the underlying
    `frames[]` array in the STI binary. (.dat sectors use 1-based sub
    indices when referencing slot frames; the viewer is a raw STI
    inspector and uses the binary's own indexing.)
    """
    sti_path = _validate_file(path, ".sti")
    png = decode_sti_frame_to_png(sti_path, frame_index=frame)
    if png is None:
        raise HTTPException(
            status_code=400,
            detail={"error": "STI_FRAME_DECODE_FAILED",
                    "message": f"could not decode frame {frame} of {sti_path.name}"},
        )
    return Response(content=png, media_type="image/png")


class SaveFrameBody(BaseModel):
    sti_path: str
    frame: int
    out_path: str


class SaveFrameResult(BaseModel):
    out_path: str
    bytes_written: int


@router.post("/sti/save-frame", response_model=SaveFrameResult)
def sti_save_frame(body: SaveFrameBody):
    """Decode one STI frame and write it as PNG to `out_path`.

    The frontend pairs this with Tauri's save-file dialog: the dialog
    yields a destination path, the wizard writes through this endpoint.
    `out_path` must end in `.png`; the directory is created if missing.
    """
    sti_path = _validate_file(body.sti_path, ".sti")
    out_path = Path(body.out_path)
    if out_path.suffix.lower() != ".png":
        raise HTTPException(
            status_code=400,
            detail={"error": "BAD_OUT_SUFFIX",
                    "message": f"out_path must end in .png, got {out_path.suffix}"},
        )
    png = decode_sti_frame_to_png(sti_path, frame_index=body.frame)
    if png is None:
        raise HTTPException(
            status_code=400,
            detail={"error": "STI_FRAME_DECODE_FAILED",
                    "message": f"could not decode frame {body.frame} of {sti_path.name}"},
        )
    try:
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_bytes(png)
    except OSError as e:
        raise HTTPException(
            status_code=500,
            detail={"error": "WRITE_FAILED",
                    "message": f"{type(e).__name__}: {e}"},
        )
    return SaveFrameResult(out_path=str(out_path), bytes_written=len(png))


# ─── STI JSD read-only viewer ─────────────────────────────────────────
# Re-uses the parser from routes/mapforge.py so the surfaced fields
# match the embedded viewer exactly. Lives here so the standalone STI
# viewer can show a JSD panel without depending on a tileset XML.

class StiViewerJsd(BaseModel):
    """Parsed JSD payload. Mirrors `JsdParsed` in mapforge.py but is
    re-declared here so this router stays import-safe even if the
    mapforge module fails to load (renderer deps missing)."""
    sti_filename: str
    jsd_path: str
    size_bytes: int
    szId: str
    n_struct: int
    n_stored: int
    struct_data_size: int
    n_image_tile_locs: int
    flags_int: int
    flag_names: list[str]
    ubArmour: int
    ubHP: int
    ubDensity: int
    ubNumberOfTiles: int
    bZTileOffsetX: int
    bZTileOffsetY: int
    tiles: list[dict]   # list of {bXPos, bYPos, sPosRelToBase, profile}


@router.get("/sti/jsd", response_model=StiViewerJsd)
def sti_jsd(
    path: str = Query(..., description="Absolute path to a .sti file"),
):
    """Parse the .jsd companion of an STI (sibling file with the same
    stem). 404 when no companion exists.
    """
    sti_path = _validate_file(path, ".sti")

    jsd_candidate: Optional[Path] = None
    for suffix in (".jsd", ".JSD", ".Jsd"):
        cand = sti_path.with_suffix(suffix)
        if cand.is_file():
            jsd_candidate = cand
            break
    if jsd_candidate is None:
        raise HTTPException(
            status_code=404,
            detail={"error": "JSD_NOT_FOUND",
                    "message": f"no .jsd companion for {sti_path.name}"},
        )

    try:
        from routes.mapforge import _parse_jsd_bytes  # type: ignore
    except Exception as e:  # noqa: BLE001
        raise HTTPException(
            status_code=500,
            detail={"error": "JSD_PARSER_UNAVAILABLE",
                    "message": f"{type(e).__name__}: {e}"},
        )
    data = jsd_candidate.read_bytes()
    parsed = _parse_jsd_bytes(data, jsd_candidate, sti_path.name)
    # Pydantic models from another router serialize fine through
    # model_dump; we use that to fill our local response shape.
    payload = parsed.model_dump()
    return StiViewerJsd(**payload)


# ─── SLF Extractor models ────────────────────────────────────────────

class SlfEntry(BaseModel):
    relpath: str       # path inside the SLF archive (forward slashes)
    size: int


class SlfListing(BaseModel):
    path: str
    library_name: str
    library_path: str
    entry_count: int
    entries: list[SlfEntry]


class SlfExtractBody(BaseModel):
    slf_path: str
    dest_dir: str
    # When None, extract all entries. When a list, only extract those
    # whose relpath matches case-insensitively.
    members: Optional[list[str]] = None
    overwrite: bool = True


class SlfExtractResult(BaseModel):
    extracted: int
    skipped: int
    errors: list[str]
    dest_dir: str


# ─── SLF Extractor endpoints ─────────────────────────────────────────

def _open_slf(slf_path: Path):
    """Open a SLF archive via ja2py's SlfFS. Raises HTTPException on
    failure (rather than letting the underlying CreateFailed propagate
    as a 500)."""
    try:
        from ja2py.fileformats.SlfFS import SlfFS  # type: ignore
    except Exception as e:  # noqa: BLE001
        raise HTTPException(
            status_code=500,
            detail={"error": "SLF_LIB_MISSING",
                    "message": f"{type(e).__name__}: {e}"},
        )
    try:
        return SlfFS(str(slf_path))
    except Exception as e:  # noqa: BLE001
        raise HTTPException(
            status_code=400,
            detail={"error": "SLF_OPEN_FAILED",
                    "message": f"{type(e).__name__}: {e}"},
        )


@router.get("/slf/list", response_model=SlfListing)
def slf_list(
    path: str = Query(..., description="Absolute path to a .slf file"),
):
    """Enumerate the contents of an SLF archive. Returns one entry per
    file (no directory entries).
    """
    slf_path = _validate_file(path, ".slf")
    fs = _open_slf(slf_path)
    entries: list[SlfEntry] = []
    try:
        for entry_path in fs.walk.files():
            try:
                info = fs.getdetails(entry_path)
                size = int(info.size)
            except Exception:
                size = 0
            # Strip leading slash for a friendlier display path.
            rel = entry_path[1:] if entry_path.startswith("/") else entry_path
            entries.append(SlfEntry(relpath=rel, size=size))
    except Exception as e:  # noqa: BLE001
        raise HTTPException(
            status_code=500,
            detail={"error": "SLF_WALK_FAILED",
                    "message": f"{type(e).__name__}: {e}"},
        )
    # SLF library headers carry display metadata — surface them.
    library_name = getattr(fs, "library_name", "") or ""
    library_path = getattr(fs, "library_path", "") or ""
    entries.sort(key=lambda e: e.relpath.lower())
    return SlfListing(
        path=str(slf_path),
        library_name=str(library_name),
        library_path=str(library_path),
        entry_count=len(entries),
        entries=entries,
    )


def _safe_dest_path(dest_root: Path, relpath: str) -> Path:
    """Resolve `dest_root / relpath`, normalize, and verify the result
    is still inside `dest_root`. Defense against an SLF entry that
    declares `../../foo` as its path."""
    # SlfFS yields forward-slash paths; normalize.
    cleaned = relpath.replace("\\", "/").lstrip("/")
    target = (dest_root / cleaned).resolve()
    try:
        target.relative_to(dest_root.resolve())
    except ValueError:
        raise HTTPException(
            status_code=400,
            detail={"error": "PATH_ESCAPE",
                    "message": f"entry '{relpath}' escapes dest_dir"},
        )
    return target


@router.post("/slf/extract", response_model=SlfExtractResult)
def slf_extract(body: SlfExtractBody):
    """Extract all (or a subset of) entries from an SLF archive into a
    destination directory. Creates the dest directory if missing.
    Idempotent re-runs are fine — existing files are overwritten by
    default. Returns counts + any per-file errors.

    For long extracts, the streaming variant /slf/extract/stream
    surfaces NDJSON progress events instead.
    """
    slf_path = _validate_file(body.slf_path, ".slf")
    dest_root = Path(body.dest_dir)
    if not dest_root.exists():
        try:
            dest_root.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            raise HTTPException(
                status_code=400,
                detail={"error": "DEST_MKDIR_FAILED",
                        "message": f"{type(e).__name__}: {e}"},
            )
    if not dest_root.is_dir():
        raise HTTPException(
            status_code=400,
            detail={"error": "DEST_NOT_DIR",
                    "message": f"{dest_root} is not a directory"},
        )

    fs = _open_slf(slf_path)
    # Build the working set: all entries, or a filtered subset.
    wanted: Optional[set[str]] = None
    if body.members is not None:
        wanted = {m.replace("\\", "/").lstrip("/").lower() for m in body.members}
    extracted = 0
    skipped = 0
    errors: list[str] = []
    try:
        for entry_path in fs.walk.files():
            cleaned = entry_path.replace("\\", "/").lstrip("/")
            if wanted is not None and cleaned.lower() not in wanted:
                continue
            try:
                target = _safe_dest_path(dest_root, cleaned)
            except HTTPException as e:
                errors.append(f"{cleaned}: {e.detail.get('message', 'path escape')}")
                continue
            if target.exists() and not body.overwrite:
                skipped += 1
                continue
            try:
                data = fs.readbytes(entry_path)
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(data)
                extracted += 1
            except Exception as e:  # noqa: BLE001
                errors.append(f"{cleaned}: {type(e).__name__}: {e}")
    except Exception as e:  # noqa: BLE001
        raise HTTPException(
            status_code=500,
            detail={"error": "SLF_WALK_FAILED",
                    "message": f"{type(e).__name__}: {e}"},
        )

    return SlfExtractResult(
        extracted=extracted,
        skipped=skipped,
        errors=errors,
        dest_dir=str(dest_root.resolve()),
    )


@router.post("/slf/extract/stream")
def slf_extract_stream(body: SlfExtractBody):
    """NDJSON-streaming variant of /slf/extract.

    Emits one line per event:
      {"event": "phase",     "label": <human-text>}
      {"event": "progress",  "current": <int>, "total": <int>, "detail": <str>}
      {"event": "done",      "data": <SlfExtractResult>}
      {"event": "error",     "message": <str>}

    Frontend opens this via fetch + body.getReader, parses lines, and
    updates a progress UI. Same NDJSON pattern as
    /api/v1/mapforge/installs/maps/stream.
    """
    slf_path = _validate_file(body.slf_path, ".slf")
    dest_root = Path(body.dest_dir)
    if not dest_root.exists():
        try:
            dest_root.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            raise HTTPException(
                status_code=400,
                detail={"error": "DEST_MKDIR_FAILED",
                        "message": f"{type(e).__name__}: {e}"},
            )
    if not dest_root.is_dir():
        raise HTTPException(
            status_code=400,
            detail={"error": "DEST_NOT_DIR",
                    "message": f"{dest_root} is not a directory"},
        )

    def event(**kw) -> bytes:
        return (json.dumps(kw) + "\n").encode("utf-8")

    def gen():
        # Open the SlfFS inside gen() and close it in finally. The
        # pre-fix code opened it at request entry — if the client
        # aborted mid-stream (closed tab, navigated away mid-extract),
        # the generator was GC'd but the outer-scope SlfFS handle never
        # closed. On Windows that kept the SLF exclusively open until
        # the sidecar restarted. Sweep bug-review finding.
        try:
            fs = _open_slf(slf_path)
        except HTTPException as e:
            yield event(event="error",
                        message=e.detail.get("message", "open failed"))
            return
        try:
            wanted: Optional[set[str]] = None
            if body.members is not None:
                wanted = {m.replace("\\", "/").lstrip("/").lower()
                          for m in body.members}
            yield event(event="phase", label="Enumerating entries")
            try:
                all_entries = list(fs.walk.files())
            except Exception as e:  # noqa: BLE001
                yield event(event="error",
                            message=f"walk failed: {type(e).__name__}: {e}")
                return

            # Apply member filter.
            if wanted is not None:
                entries = [p for p in all_entries
                           if p.replace("\\", "/").lstrip("/").lower() in wanted]
            else:
                entries = all_entries

            total = len(entries)
            extracted = 0
            skipped = 0
            errors: list[str] = []
            yield event(event="phase",
                        label=f"Extracting {total} entr"
                              f"{'y' if total == 1 else 'ies'}")
            for i, entry_path in enumerate(entries):
                cleaned = entry_path.replace("\\", "/").lstrip("/")
                yield event(event="progress",
                            current=i, total=total, detail=cleaned)
                try:
                    target = _safe_dest_path(dest_root, cleaned)
                except HTTPException as e:
                    errors.append(
                        f"{cleaned}: {e.detail.get('message', 'path escape')}")
                    continue
                if target.exists() and not body.overwrite:
                    skipped += 1
                    continue
                try:
                    data = fs.readbytes(entry_path)
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_bytes(data)
                    extracted += 1
                except Exception as e:  # noqa: BLE001
                    errors.append(f"{cleaned}: {type(e).__name__}: {e}")
            yield event(event="progress",
                        current=total, total=total, detail="done")
            result = SlfExtractResult(
                extracted=extracted,
                skipped=skipped,
                errors=errors,
                dest_dir=str(dest_root.resolve()),
            )
            yield event(event="done", data=result.model_dump())
        finally:
            try:
                fs.close()
            except Exception:  # noqa: BLE001
                pass

    return StreamingResponse(gen(), media_type="application/x-ndjson")
