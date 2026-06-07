"""MapForge STI library — browse + import from the asset-library catalog.

The asset library is an external content store that scans every JA2
install on the machine and deduplicates per-tile assets by SHA-256,
ending with ~4070 unique STIs spread across `tile_sti` / `tilecache_sti`
kinds. The catalog also auto-tags subframes from filename heuristics +
JA2 1.13 Items.xml classification.

This module exposes a thin read layer over the catalog plus a write
action that COPIES an asset into the active install's tileset and
registers it in `Ja2Set.dat.xml`. The catalog itself stays read-only.

Path coupling: the catalog DB and thumbnail shards live at a location
the sidecar is pointed at via the MERCWIZARD_ASSET_BROWSER_ROOT env var
(see "Catalog paths" below). The library is not part of the public
distribution; when the env var is unset, every endpoint here reports the
catalog as unavailable and degrades gracefully.
"""
from __future__ import annotations

import os
import re
import shutil
import sqlite3
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import Response
from pydantic import BaseModel

from .state import get_state


# ─── Catalog paths ────────────────────────────────────────────────────
# The asset-library catalog lives in a sibling content store that is NOT
# part of the public MercForge distribution — the Browse-Assets feature
# (wired separately) points the sidecar at it via the
# MERCWIZARD_ASSET_BROWSER_ROOT env var. When the env var is unset (the
# default for a beta build), `_ASSET_BROWSER_ROOT` resolves to a path
# that won't exist, so `_catalog()` / `library_health()` report the
# library as "not installed" and every library endpoint degrades
# gracefully instead of leaking a development path. No absolute
# dev-machine path is baked into the shipped source.
_ASSET_BROWSER_ROOT = Path(
    os.environ.get("MERCWIZARD_ASSET_BROWSER_ROOT")
    or (Path(__file__).resolve().parent.parent / "asset_browser")
)
_CATALOG_DB = _ASSET_BROWSER_ROOT / "data" / "catalog.sqlite3"
_THUMBS_DIR = _ASSET_BROWSER_ROOT / "data" / "thumbs"
# Per-subframe PNG cache (sharded by first two hex chars of the
# subframe's sha256). Asset_Browser's `subframe` table tracks per-sub
# sha256; the PNGs live here. We proxy them through the sidecar so
# the frontend can authenticate against the MercWizard2 token rather
# than fanning out to a second origin.
_SUBFRAMES_DIR = _ASSET_BROWSER_ROOT / "data" / "subframes"


router = APIRouter(prefix="/mapforge/library")


def _catalog() -> sqlite3.Connection:
    """Open the Asset Browser catalog read-only. Each request gets its
    own connection — SQLite is fast and connections are cheap."""
    if not _CATALOG_DB.is_file():
        raise HTTPException(
            status_code=503,
            detail={
                "error": "CATALOG_NOT_FOUND",
                "message": ("The asset library catalog is not available in "
                            "this install."),
            },
        )
    # `file:...?mode=ro` opens read-only; no chance of accidentally
    # writing to the catalog from the editor sidecar.
    uri = f"file:{_CATALOG_DB}?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    conn.row_factory = sqlite3.Row
    return conn


# ─── Models ───────────────────────────────────────────────────────────
class LibrarySti(BaseModel):
    """One row in the STI library grid."""
    sha256: str
    width: Optional[int] = None
    height: Optional[int] = None
    frame_count: Optional[int] = None
    has_jsd: bool
    kind: str  # tile_sti | tilecache_sti
    # Representative filename (just the basename — the source-install path
    # is in detail). Multiple installs may have the same SHA at different
    # paths; we surface one for display.
    name: str
    install_count: int
    # True when the active install's selected tileset already contains
    # an STI with this basename (so the UI can de-emphasize / badge it).
    in_current_tileset: bool = False
    # Auto-tags inherited from any sub-frame of this STI. Asset Browser
    # tags subframes, not assets directly, so this is the union across
    # subframes.
    tags: list[str] = []


class LibraryStiList(BaseModel):
    page: int
    per_page: int
    total: int
    tag_filter: Optional[str] = None
    query: Optional[str] = None
    items: list[LibrarySti]


class LibraryStiOccurrence(BaseModel):
    """Where a given STI file lives on disk."""
    install_id: int
    install_label: str
    install_root: str
    relpath: str
    is_in_slf: bool
    slf_member: Optional[str] = None


class LibraryStiDetail(BaseModel):
    sha256: str
    kind: str
    width: Optional[int] = None
    height: Optional[int] = None
    frame_count: Optional[int] = None
    has_jsd: bool
    size_bytes: int
    tags: list[str]
    occurrences: list[LibraryStiOccurrence]


class LibraryTag(BaseModel):
    name: str
    subframe_count: int
    source: Optional[str] = None


class AddStiToTilesetBody(BaseModel):
    sha256: str
    tileset: int
    target_slot: Optional[int] = None  # auto-pick lowest free if None
    # Filename to register the STI under in the destination tileset.
    # Defaults to the source asset's basename; user can override (e.g.
    # to avoid collision with an existing slot using the same filename).
    target_filename: Optional[str] = None
    # Highest tile-type slot the running ja2.exe accepts. Stock 1.13 is
    # built with NUMBEROFTILETYPES = 151 (slot 0-150 inclusive). The
    # add path REFUSES to allocate above this — auto-pick searches only
    # 0..cap, and manual picks above cap return 400 SLOT_ABOVE_ENGINE_CAP.
    # The frontend reads this from user settings (mapforgeSettings.ts).
    # Defaulted to 150 so old clients that don't send it still get the
    # safe behavior.
    engine_max_tile_slot: int = 150
    # Opt-in escape hatch for users staging XML for a forked ja2.exe
    # with a higher cap. When True the above checks emit a warning
    # header on the response (X-MercForge-Warning) instead of erroring.
    allow_above_cap: bool = False
    # When set, only the chosen sub-frame of the source STI is copied
    # into the destination — a fresh single-frame STI is written, not
    # the full bytes. Lets the user import "just sub 3" of a multi-sub
    # source instead of the whole bundle. None (default) preserves
    # the existing whole-STI copy behavior.
    target_sub: Optional[int] = None


class LibrarySub(BaseModel):
    """One sub-frame of a library STI. Used by the per-sub viewer
    so the user can preview each frame before importing individually."""
    sub_idx: int
    sha256: str  # subframe sha256 — drives the per-sub thumb endpoint
    width: int
    height: int
    tags: list[str] = []


class LibrarySubList(BaseModel):
    sti_sha256: str
    subs: list[LibrarySub]


class LooseSlot(BaseModel):
    """One slot in the active tileset whose STI is a loose file on
    disk — i.e. mutable. Powers the destination dropdown of the
    inject-sub flow; SLF-only slots can't be appended to without
    extracting first, so they're excluded."""
    slot: int
    filename: str
    path: str
    frame_count: int


class LooseSlotList(BaseModel):
    tileset: int
    slots: list[LooseSlot]


class InjectSubBody(BaseModel):
    """Append a single sub-frame from a library STI onto the existing
    STI at `target_slot` in `tileset`. User request:

        "It should also allow injecting a subframe into an existing
         slot that has space for it."

    Constraints (see ASSET_BROWSER_PLAN.md §4):
      - Destination STI must be loose on disk (not SLF-resident).
      - Destination's 8-bit palette must match source's, unless
        `force=true`.
      - Source's sub_idx must be in range.
    """
    src_sha256: str
    tileset: int
    target_slot: int
    src_sub: int
    # Bypass the PALETTE_MISMATCH refusal — appended frames will
    # render with the destination's palette regardless, so the
    # source frame will look colorshifted if the user forces.
    force: bool = False


class InjectSubResult(BaseModel):
    tileset: int
    slot: int
    sti_filename: str
    new_sub_index: int
    frames_after: int
    backup_path: Optional[str] = None


class AddStiToTilesetResult(BaseModel):
    sha256: str
    tileset: int
    slot: int
    filename: str
    install_root: str
    # Where the file was written inside the install.
    written_to: str
    # Path of the Ja2Set.dat.xml backup created on first edit.
    xml_backup_path: Optional[str] = None
    jsd_copied: bool


class CopyTileToTilesetBody(BaseModel):
    """Copy a tile from one LIVE tileset (a slot already registered in
    the active install's Ja2Set.dat.xml) into another tileset of the
    SAME install.

    Engine-safety contract (see the route docstring):
      - The destination slot defaults to the SOURCE slot index, so the
        copied tile lands in the same tile-type family (correct flags /
        layer / shadow-buddy / animation behavior).
      - Appends a NEW `<file index>` entry; never grows an existing
        slot's STI and never rewrites/reindexes existing entries — so
        every (type, subindex) already stored in any sector stays valid.
      - HARD cap at `engine_max_tile_slot` (stock 150); above-cap writes
        require `allow_above_cap` (forked ja2.exe staging).
    """
    # Destination tileset index in the active install. The source
    # tileset + slot come from the path params.
    dest_tileset: int
    # Slot to register the tile under in dest_tileset. None → use the
    # SOURCE slot index (the safe default — same tile-type family).
    target_slot: Optional[int] = None
    # Cap-bounded auto-pick of the lowest free slot, used as the recovery
    # path after a 409 SLOT_TAKEN. Overrides both target_slot and the
    # src-slot default — the lowest free slot in 0..cap is chosen
    # instead. NOTE: a different slot puts the tile in a DIFFERENT
    # tile-type family (different flags/layer/behavior); the UI warns
    # before sending this.
    auto_pick: bool = False
    # When set, only this sub-frame of the source STI is copied (a fresh
    # single-frame STI is written, filename suffixed _subN). None copies
    # the whole STI verbatim (and its sibling .jsd if present).
    target_sub: Optional[int] = None
    # Highest tile-type slot the running ja2.exe accepts (stock 1.13 =
    # 150). Bounds auto-pick and rejects manual slots above it.
    engine_max_tile_slot: int = 150
    # Opt-in escape hatch for a forked ja2.exe with a higher cap.
    allow_above_cap: bool = False


class CopyTileToTilesetResult(BaseModel):
    src_tileset: int
    src_slot: int
    dest_tileset: int
    slot: int
    filename: str
    install_root: str
    written_to: str
    xml_backup_path: Optional[str] = None
    jsd_copied: bool


# ─── Helpers ──────────────────────────────────────────────────────────
def _names_in_tileset(xml_path: Path, tileset: int) -> set[str]:
    """Set of lowercased STI filenames currently registered in
    `tileset`'s block of Ja2Set.dat.xml (with tile-0 inheritance).
    Used to mark catalog entries that the user has already imported,
    so the UI can show a "✓ In tileset" badge."""
    try:
        tree = ET.parse(xml_path)
    except (OSError, ET.ParseError):
        return set()

    def _gather(idx: int) -> set[str]:
        out: set[str] = set()
        for ts in tree.getroot().iter("Tileset"):
            if int(ts.get("index", -1)) == idx:
                fnode = ts.find("Files")
                if fnode is None:
                    return out
                for f in fnode.findall("file"):
                    txt = (f.text or "").strip()
                    if txt:
                        out.add(txt.lower())
                return out
        return out

    base = _gather(0) if tileset != 0 else set()
    return base | _gather(tileset)


def _next_free_slot(
    xml_path: Path,
    tileset: int,
    engine_max_tile_slot: int = 150,
) -> int:
    """Lowest slot index not used in `tileset`'s block. Includes
    tile-0 inheritance so we don't pick a slot the engine considers
    taken via the base tileset.

    Search is bounded by `engine_max_tile_slot` (default 150 = stock
    ja2.exe NUMBEROFTILETYPES - 1). Previously this walked 0..255
    regardless of cap, which would silently park a freshly-added STI
    in a slot the engine could never address — sector load crash on
    first reference. A user hit this 2026-05-24:

        "the 150 slot warning, is this something we can adjust or
         would that have large impacts? if we cant adjust it why
         does auto put it there when settings prevent it?"

    When no slot is free under the cap we raise NO_FREE_SLOT_UNDER_CAP
    (distinct from TILESET_FULL, which would mean the entire 0..255
    range was full — practically unreachable). The frontend uses the
    error code to decide between "raise cap in Settings" vs "pick a
    slot to overwrite" recovery CTAs.
    """
    try:
        tree = ET.parse(xml_path)
    except (OSError, ET.ParseError):
        return 1
    used: set[int] = set()
    for ts in tree.getroot().iter("Tileset"):
        ts_idx = int(ts.get("index", -1))
        if ts_idx not in (0, tileset):
            continue
        fnode = ts.find("Files")
        if fnode is None:
            continue
        for f in fnode.findall("file"):
            idx = f.get("index")
            if idx is not None:
                try:
                    used.add(int(idx))
                except ValueError:
                    pass
    # Cap-bounded search. Iterate 0..cap inclusive; first unused wins.
    for candidate in range(0, engine_max_tile_slot + 1):
        if candidate not in used:
            return candidate
    raise HTTPException(
        status_code=409,
        detail={
            "error": "NO_FREE_SLOT_UNDER_CAP",
            "message": (
                f"Tileset {tileset} has no free slot in 0..{engine_max_tile_slot}. "
                "Raise engineMaxTileSlot in Settings if your ja2.exe is a custom "
                "build with a higher NUMBEROFTILETYPES, or replace an existing "
                "slot via the inject-sub flow."
            ),
            "tileset": tileset,
            "engine_max_tile_slot": engine_max_tile_slot,
        },
    )


def _find_loose_tileset_stis(
    install_root: Path, xml_path: Path, tileset: int,
) -> list[LooseSlot]:
    """Walk the tileset's XML block (+ tile-0 inheritance) and return
    every slot whose registered STI is present as a loose file on disk
    in `<install>/<layer>/Tilesets/<N>/`. SLF-only slots are EXCLUDED
    because they can't be appended to without extracting the SLF.

    Used as the destination filter for the inject-sub flow: only loose
    slots show up in the "where to inject" dropdown.
    """
    try:
        from ja2py.fileformats.Sti import load_8bit_sti
    except ImportError:
        load_8bit_sti = None  # type: ignore[assignment]
    try:
        tree = ET.parse(xml_path)
    except (OSError, ET.ParseError):
        return []
    # Collect (slot, filename) pairs from this tileset + tile 0.
    slots: dict[int, str] = {}
    for ts in tree.getroot().iter("Tileset"):
        ts_idx = int(ts.get("index", -1))
        if ts_idx not in (0, tileset):
            continue
        fnode = ts.find("Files")
        if fnode is None:
            continue
        for f in fnode.findall("file"):
            try:
                idx = int(f.get("index", -1))
            except ValueError:
                continue
            name = (f.text or "").strip()
            if not name:
                continue
            # Inheritance: a per-tileset entry wins over tile 0 at the
            # same slot index.
            if ts_idx == tileset or idx not in slots:
                slots[idx] = name

    # Resolve each filename against the data-layer's Tilesets dir.
    # The XML's parent dir IS the data layer (Data-1.13 / Data-DMK /
    # Data), so loose tile dirs sit at <xml_parent>/Tilesets/<N>/.
    layer_dir = xml_path.parent
    tilesets_dir = layer_dir / "Tilesets" / str(tileset)
    out: list[LooseSlot] = []
    for slot, fname in sorted(slots.items()):
        full = tilesets_dir / fname
        if not full.is_file():
            continue
        # Best-effort frame count read for the UI. If ja2py refuses
        # the file (rare — non-8bit STI etc.) we fall back to 0
        # rather than dropping the slot from the list; the user can
        # still inject and discover the issue then.
        frame_count = 0
        if load_8bit_sti is not None:
            try:
                with full.open("rb") as fh:
                    img = load_8bit_sti(fh)
                frame_count = len(img.images)
            except Exception:  # noqa: BLE001
                frame_count = 0
        out.append(LooseSlot(
            slot=slot, filename=fname, path=str(full),
            frame_count=frame_count,
        ))
    return out


def _palettes_match(p_a, p_b) -> bool:
    """True when two PIL palettes carry the same color bytes. Used to
    refuse cross-palette sub injections — appending a frame whose
    palette indices mean different colors than the destination's
    would look completely wrong on screen.

    We compare the raw 256×3 bytes (`palette` attribute) rather than
    ImagePalette equality because PIL's __eq__ on ImagePalette is
    pickier than we need (rawmode etc.). The byte content is what
    actually affects rendering."""
    a = bytes(p_a.palette) if hasattr(p_a, "palette") else None
    b = bytes(p_b.palette) if hasattr(p_b, "palette") else None
    return a is not None and b is not None and a == b


def _extract_single_sub_bytes(source_sti_bytes: bytes, sub_idx: int) -> bytes:
    """Re-encode `source_sti_bytes` as a fresh single-frame STI that
    contains only frame `sub_idx` from the source. Used by
    add-to-tileset when the user picks a specific sub to import
    (user request 2026-05-24: "import an indiviudal subframe").

    Implementation: read the source STI via ja2py's load_8bit_sti,
    rebuild a one-element Images8Bit with the chosen frame, write
    back with save_8bit_sti. The destination keeps the original
    palette + per-frame offsets, so subsequent renderers see the
    sub at the same on-screen position as in the source.

    Raises HTTPException on:
      - 400 SUB_OUT_OF_RANGE — sub_idx >= frame_count
      - 500 STI_REENCODE_FAILED — source is unreadable or non-8bit
    """
    import io as _io
    try:
        from ja2py.fileformats.Sti import load_8bit_sti, save_8bit_sti
        from ja2py.content.Image import Images8Bit
    except ImportError as e:
        raise HTTPException(500, {
            "error": "JA2PY_UNAVAILABLE",
            "message": f"single-sub extraction requires ja2py: {e}",
        })
    try:
        ja2_images = load_8bit_sti(_io.BytesIO(source_sti_bytes))
    except Exception as e:  # noqa: BLE001
        # Non-8bit STIs (RGB stills) aren't sub-indexed in the same
        # way — refuse cleanly rather than trying to munge them.
        raise HTTPException(500, {
            "error": "STI_REENCODE_FAILED",
            "message": (
                f"could not load source STI as 8-bit: {type(e).__name__}: {e}. "
                "Single-sub extraction only supports indexed STIs (the format "
                "every tile-layer STI uses); RGB stills must be imported whole."
            ),
        })
    if sub_idx < 0 or sub_idx >= len(ja2_images.images):
        raise HTTPException(400, {
            "error": "SUB_OUT_OF_RANGE",
            "message": (
                f"sub {sub_idx} out of range — source STI has "
                f"{len(ja2_images.images)} frame(s) (0..{len(ja2_images.images) - 1})"
            ),
            "sub_idx": sub_idx,
            "frame_count": len(ja2_images.images),
        })
    one_image = ja2_images.images[sub_idx]
    # Width/height on the container are the GROUP dimensions, used by
    # consumers that draw the sub against a uniform bounding box. For
    # a single-sub extract the sub IS the bounding box, so use its own
    # dimensions — matches what Asset_Browser tag inference does.
    new_container = Images8Bit(
        images=[one_image],
        palette=ja2_images.palette,
        width=one_image.image.size[0],
        height=one_image.image.size[1],
    )
    out = _io.BytesIO()
    save_8bit_sti(new_container, out)
    return out.getvalue()


def _resolve_asset_bytes(asset_id: int, conn: sqlite3.Connection) -> tuple[bytes, dict]:
    """Read the raw asset bytes from the FIRST available occurrence
    (preferring loose files over SLF members). Returns (bytes, info).
    Used by the add-to-tileset path to copy an asset out of the catalog
    into the destination install."""
    occs = conn.execute(
        """SELECT ao.relpath, ao.is_in_slf, ao.slf_member,
                  i.root_path, i.label
           FROM asset_occurrence ao
           JOIN install i ON i.id = ao.install_id
           WHERE ao.asset_id = ?
           ORDER BY ao.is_in_slf ASC, ao.id ASC""",
        (asset_id,),
    ).fetchall()
    if not occs:
        raise HTTPException(
            status_code=404,
            detail={"error": "ASSET_NO_OCCURRENCE",
                    "message": f"asset id {asset_id} has no recorded occurrence"},
        )
    for occ in occs:
        root = Path(occ["root_path"])
        rel = occ["relpath"]
        if not occ["is_in_slf"]:
            full = root / rel
            if full.is_file():
                return full.read_bytes(), dict(occ)
        else:
            # Need to extract from the SLF.
            try:
                from ja2py.fileformats.SlfFS import SlfFS  # noqa
            except ImportError:
                continue
            slf_path = root / rel
            if not slf_path.is_file():
                continue
            try:
                fs = SlfFS(str(slf_path))
                data = fs.readbytes(occ["slf_member"])
                return data, dict(occ)
            except Exception:  # noqa: BLE001
                continue
    raise HTTPException(
        status_code=404,
        detail={"error": "ASSET_UNREACHABLE",
                "message": f"asset id {asset_id} occurrences all failed to read"},
    )


class _CommitResult(BaseModel):
    """Outcome of writing an STI into a tileset (shared by the
    catalog-import and cross-tileset-copy paths)."""
    slot: int
    filename: str
    written_to: str
    xml_backup_path: Optional[str] = None
    jsd_copied: bool


def _commit_sti_to_tileset(
    *,
    xml_path: Path,
    tileset: int,
    sti_bytes: bytes,
    jsd_bytes: Optional[bytes],
    target_filename: str,
    target_slot: Optional[int],
    engine_max_tile_slot: int,
    allow_above_cap: bool,
    response: Optional[Response],
) -> _CommitResult:
    """Write `sti_bytes` (+ optional `jsd_bytes`) into `tileset` and
    register the new slot in `Ja2Set.dat.xml` — the shared back half of
    `add_sti_to_tileset` and `copy_tile_to_tileset`.

    This is steps 3-8 of `add_sti_to_tileset`'s docstring, lifted
    verbatim so BOTH callers get the identical engine-safety behavior:
      3. Slot pick (caller slot with cap-guard + SLOT_TAKEN refusal, or
         cap-bounded auto-pick incl. tile-0 inheritance).
      4. Write the .sti to `<layer>/Tilesets/<tileset>/<filename>`.
      5. Copy the sibling .jsd when the caller supplies its bytes.
      6. Back up Ja2Set.dat.xml to .bak on first edit (idempotent).
      7. ATOMIC `<file index="slot">filename</file>` append (tempfile +
         fsync + os.replace) — a half-written XML makes the engine
         refuse to boot.
      8. Bust the on-disk atlas cache for this tileset.

    The caller is responsible for resolving `sti_bytes` / `jsd_bytes` /
    `target_filename` (catalog vs live tileset) and for validating the
    filename shape; everything that touches the destination install +
    Ja2Set.dat.xml lives here so the two import surfaces can never drift
    on the index-append invariant or the atomic-write discipline.

    APPEND-ONLY INVARIANT: a new tile always goes into a fresh `<file
    index>` entry — existing entries are never rewritten or reindexed,
    so every (type, subindex) already stored in any sector stays valid
    (the engine rebuilds the global tile index from a compile-time
    gNumTilesPerType table, keyed by slot index). We NEVER grow an
    existing slot's STI here (that is the inject-sub route's job and a
    frame-truncation landmine); this path only ADDS a slot.
    """
    # Engine filename cap: TILESET.TileSurfaceFilenames is CHAR8[32]
    # (TileEngine/WorldDat.h) and the tileset loader strncpy()s 32 bytes
    # into it (TileEngine/XML_TileSet.hpp). A name longer than 31 chars is
    # stored WITHOUT a null terminator, so the engine then resolves a
    # garbage filename and the STI silently fails to load — a missing tile
    # in-game, not a crash, but a silent one that's hard to diagnose.
    # Refuse over-long names here, at the shared write choke point, so it
    # covers BOTH the catalog-add and cross-tileset-copy callers (the
    # latter's single-sub `_subN` suffix can push a borderline source name
    # over the limit).
    if len(target_filename) > 31:
        raise HTTPException(400, {
            "error": "FILENAME_TOO_LONG",
            "message": (
                f"target filename {target_filename!r} is {len(target_filename)} "
                "characters; the engine's per-slot filename buffer holds 31 max "
                "(CHAR8[32] including the null terminator). Pick a shorter name "
                "(the optional target_filename field lets you rename on import)."
            ),
            "filename": target_filename,
            "max_len": 31,
        })

    # Windows resolves reserved device names (CON/PRN/AUX/NUL, COM1-9, LPT1-9)
    # regardless of extension or directory — "<dir>\con.sti" hits the console
    # device, not a file. An all-ASCII .sti name passes the callers' filename
    # regex, so reject device names here at the shared choke point so it covers
    # BOTH the catalog-add and cross-tileset-copy surfaces. Split on the FIRST
    # dot because device resolution keys there (so "com1.foo.sti" is caught too).
    _stem = target_filename.split(".", 1)[0].upper()
    if _stem in {"CON", "PRN", "AUX", "NUL"} or re.fullmatch(r"COM[1-9]|LPT[1-9]", _stem):
        raise HTTPException(400, {
            "error": "BAD_FILENAME",
            "message": "filename uses a Windows reserved device name",
        })

    # 3. Slot pick — identical policy to add_sti_to_tileset.
    if target_slot is not None:
        slot = target_slot
        # Engine-cap guard for manual picks. ja2.exe HARD-crashes on
        # sector load if it dereferences a slot >= NUMBEROFTILETYPES, so
        # the backend enforces the cap independently of any UI
        # pre-screen. `allow_above_cap` is the opt-in escape hatch for
        # users staging XML against a forked ja2.exe.
        if slot > engine_max_tile_slot and not allow_above_cap:
            raise HTTPException(400, {
                "error": "SLOT_ABOVE_ENGINE_CAP",
                "message": (
                    f"slot {slot} exceeds engine cap {engine_max_tile_slot}. "
                    "ja2.exe can't reference slots above its compiled "
                    "NUMBEROFTILETYPES — referencing one crashes the engine on "
                    "sector load. Pick a slot <= cap, raise the cap in Settings "
                    "if your ja2.exe is a custom build, or send allow_above_cap=true "
                    "to override (you'll get an X-MercForge-Warning header)."
                ),
                "slot": slot,
                "engine_max_tile_slot": engine_max_tile_slot,
            })
        # Refuse only if the slot is taken in the TARGET tileset's OWN entries.
        # A slot merely INHERITED from tile-0 (the base tileset) is overridable:
        # the engine honors a per-tileset entry over the inherited one, and in a
        # non-base tileset most slots are inherited, so counting them would 409
        # nearly every manual import (a regression from the pre-refactor add
        # path, which scanned the target tileset only). NEVER overwrite an entry
        # the target tileset itself defines. (Auto-pick below uses
        # _next_free_slot, which DOES include tile-0 inheritance so it never
        # lands on an inherited slot.)
        used: dict[int, str] = {}
        try:
            tree = ET.parse(xml_path)
        except (OSError, ET.ParseError) as e:
            raise HTTPException(500, {"error": "XML_PARSE",
                                       "message": str(e)})
        for ts in tree.getroot().iter("Tileset"):
            if int(ts.get("index", -1)) != tileset:
                continue
            fnode = ts.find("Files")
            if fnode is None:
                continue
            for f in fnode.findall("file"):
                idx = f.get("index")
                if idx is None:
                    continue
                try:
                    i = int(idx)
                except ValueError:
                    continue
                used[i] = (f.text or "").strip()
        if slot in used:
            occupant = used[slot] or "(unnamed)"
            raise HTTPException(409, {
                "error": "SLOT_TAKEN",
                "message": (
                    f"slot {slot} is already taken by {occupant} in "
                    f"tileset {tileset}. Replacing existing slots "
                    "isn't supported yet — pick a different slot, or "
                    "leave the Slot field blank for auto-pick."
                ),
                "occupant_filename": occupant,
                "slot": slot,
                "tileset": tileset,
            })
    else:
        # Cap-bounded auto-pick (widened to 255 only when above-cap is
        # explicitly allowed). _next_free_slot includes tile-0
        # inheritance and raises 409 NO_FREE_SLOT_UNDER_CAP when full.
        ceiling = 255 if allow_above_cap else engine_max_tile_slot
        slot = _next_free_slot(xml_path, tileset, ceiling)

    # 4. Write to <layer>/Tilesets/<tileset>/ — the same data layer that
    # owns the XML, so the engine VFS resolves it consistently.
    layer_dir = xml_path.parent
    tilesets_dir = layer_dir / "Tilesets" / str(tileset)
    tilesets_dir.mkdir(parents=True, exist_ok=True)
    sti_dest = tilesets_dir / target_filename
    # Defense-in-depth: confirm the resolved target stays inside
    # tilesets_dir. The callers' filename guards already guarantee this
    # today; this catches any future regression that loosens the
    # filename grammar, and unlike the original inline check it now
    # covers BOTH the add and copy import surfaces.
    try:
        sti_dest.resolve().relative_to(tilesets_dir.resolve())
    except ValueError:
        raise HTTPException(400, {"error": "BAD_FILENAME",
            "message": "resolved path escapes the tileset directory"})
    if sti_dest.exists():
        raise HTTPException(409, {"error": "FILE_EXISTS",
            "message": f"{sti_dest} already exists — pick a different target_filename"})
    sti_dest.write_bytes(sti_bytes)
    jsd_copied = False
    if jsd_bytes is not None:
        jsd_dest = sti_dest.with_suffix(".jsd")
        try:
            jsd_dest.write_bytes(jsd_bytes)
            jsd_copied = True
        except OSError:
            pass  # non-fatal — the STI is still placed

    # 6. Back up XML (idempotent .bak — first save per session wins).
    bak_path = xml_path.with_suffix(xml_path.suffix + ".bak")
    bak_str: Optional[str] = None
    if not bak_path.exists():
        try:
            shutil.copy2(xml_path, bak_path)
            bak_str = str(bak_path)
        except OSError as e:
            sti_dest.unlink(missing_ok=True)
            if jsd_copied:
                sti_dest.with_suffix(".jsd").unlink(missing_ok=True)
            raise HTTPException(500, {"error": "BACKUP_FAILED",
                "message": f"could not write {bak_path}: {e}"})
    else:
        bak_str = str(bak_path)

    # 7. Atomic XML edit — add `<file index="slot">filename</file>`.
    # Serialize → tempfile in the same dir → fsync → os.replace, so a
    # power loss / OOM / AV truncation can't leave a half-written XML
    # that makes the engine refuse to boot.
    try:
        tree = ET.parse(xml_path)
        root = tree.getroot()
        ts_node = None
        for ts in root.iter("Tileset"):
            if int(ts.get("index", -1)) == tileset:
                ts_node = ts
                break
        if ts_node is None:
            ts_node = ET.SubElement(root, "Tileset")
            ts_node.set("index", str(tileset))
        files_node = ts_node.find("Files")
        if files_node is None:
            files_node = ET.SubElement(ts_node, "Files")
        new_file = ET.SubElement(files_node, "file")
        new_file.set("index", str(slot))
        new_file.text = target_filename
        import os as _os
        import tempfile as _tempfile
        from io import BytesIO as _BytesIO
        buf = _BytesIO()
        tree.write(buf, encoding="utf-8", xml_declaration=True)
        fd, tmp = _tempfile.mkstemp(
            dir=str(xml_path.parent),
            prefix=xml_path.name + ".",
            suffix=".tmp",
        )
        try:
            with _os.fdopen(fd, "wb") as tf:
                tf.write(buf.getvalue())
                tf.flush()
                _os.fsync(tf.fileno())
            _os.replace(tmp, xml_path)
        except BaseException:
            try:
                _os.unlink(tmp)
            except OSError:
                pass
            raise
    except Exception as e:  # noqa: BLE001
        sti_dest.unlink(missing_ok=True)
        if jsd_copied:
            sti_dest.with_suffix(".jsd").unlink(missing_ok=True)
        raise HTTPException(500, {"error": "XML_WRITE_FAILED",
            "message": f"{type(e).__name__}: {e}"})

    # 8. Invalidate the on-disk atlas cache for this tileset.
    from .mapforge import _ATLAS_CACHE  # circular-safe (function-scoped)
    for d in _ATLAS_CACHE.glob(f"{tileset}_*"):
        try:
            shutil.rmtree(d, ignore_errors=True)
        except OSError:
            pass

    # Above-cap warning header — only when the user opted into
    # allow_above_cap AND the resolved slot is in the danger zone.
    if response is not None and slot > engine_max_tile_slot:
        response.headers["X-MercForge-Warning"] = (
            f"slot {slot} exceeds engineMaxTileSlot {engine_max_tile_slot}; "
            "stock ja2.exe will crash on sector load"
        )

    return _CommitResult(
        slot=slot,
        filename=target_filename,
        written_to=str(sti_dest),
        xml_backup_path=bak_str,
        jsd_copied=jsd_copied,
    )


# ─── Endpoints ────────────────────────────────────────────────────────
@router.get("/health")
def library_health():
    """Quick check that the catalog DB is reachable.

    `catalog_path` is a human-readable status string, NOT an absolute
    filesystem path — the asset library lives at an internal location
    that is not surfaced to end users."""
    if not _CATALOG_DB.is_file():
        return {"available": False,
                "catalog_path": "(asset library not installed)",
                "message": "catalog not found"}
    conn = _catalog()
    try:
        c = conn.execute(
            "SELECT COUNT(*) FROM asset WHERE kind IN ('tile_sti', 'tilecache_sti')"
        ).fetchone()
        return {
            "available": True,
            "catalog_path": "(asset library installed)",
            "sti_count": c[0],
        }
    finally:
        conn.close()


@router.get("/stis", response_model=LibraryStiList)
def list_stis(
    page: int = Query(1, ge=1),
    per_page: int = Query(48, ge=1, le=200),
    q: Optional[str] = Query(None, description="Filename substring filter"),
    tag: Optional[str] = Query(None, description="Filter by subframe tag (any frame)"),
    kind: Optional[str] = Query("tile_sti", description="tile_sti | tilecache_sti | any"),
    has_jsd: Optional[bool] = Query(None),
    width: Optional[int] = Query(None, description="Exact STI width (e.g. 40 for floors)"),
    height: Optional[int] = Query(None),
    xml: Optional[str] = Query(None, description="Path to active Ja2Set.dat.xml — set this to get the 'in_current_tileset' badge"),
    tileset: Optional[int] = Query(None, description="Tileset index for the in-tileset check"),
):
    """Paginated browse over the Asset Browser catalog.

    The catalog has ~4070 unique tile STIs after SHA-256 dedup across
    23 installs. We expose them as a single browseable library that
    the user can pick from to import into their current tileset.

    Tag filtering: tags live on SUBFRAMES (an STI's frames may each
    have different tags). We expose the union — an asset has tag "wall"
    if ANY of its subframes does. This matches the user's mental model
    ("show me wall STIs"), even if the STI also contains floor frames
    etc.

    `in_current_tileset`: set when both `xml` and `tileset` are
    provided. Lets the UI badge entries the user has already imported.
    """
    conn = _catalog()
    try:
        # Filter clauses + params built incrementally.
        clauses: list[str] = []
        params: list = []
        if kind and kind != "any":
            clauses.append("a.kind = ?")
            params.append(kind)
        else:
            clauses.append("a.kind IN ('tile_sti', 'tilecache_sti')")
        if has_jsd is True:
            clauses.append("a.has_jsd = 1")
        elif has_jsd is False:
            clauses.append("a.has_jsd = 0")
        if width is not None:
            clauses.append("a.width = ?")
            params.append(width)
        if height is not None:
            clauses.append("a.height = ?")
            params.append(height)
        if q:
            # Substring match against an occurrence relpath. Using EXISTS
            # so we don't multiply rows when an asset has many
            # occurrences.
            clauses.append(
                "EXISTS (SELECT 1 FROM asset_occurrence ao "
                "WHERE ao.asset_id = a.id AND ao.relpath LIKE ?)"
            )
            params.append(f"%{q}%")
        if tag:
            clauses.append(
                "EXISTS (SELECT 1 FROM subframe_in_asset sfia "
                "JOIN subframe_tag st ON st.subframe_id = sfia.subframe_id "
                "JOIN tag t ON t.id = st.tag_id "
                "WHERE sfia.asset_id = a.id AND t.name = ?)"
            )
            params.append(tag)
        where = " AND ".join(clauses) if clauses else "1=1"

        # Total for pagination.
        total = conn.execute(
            f"SELECT COUNT(*) FROM asset a WHERE {where}",
            params,
        ).fetchone()[0]

        # Page query. We surface a representative relpath (the lex-min
        # occurrence) and an install count. Subqueries are cheap here
        # since we're only paginating a single page of rows.
        offset = (page - 1) * per_page
        rows = conn.execute(
            f"""SELECT a.id, a.sha256, a.kind, a.width, a.height, a.frame_count,
                       a.has_jsd,
                       (SELECT ao.relpath FROM asset_occurrence ao
                         WHERE ao.asset_id = a.id ORDER BY ao.id ASC LIMIT 1) AS relpath,
                       (SELECT COUNT(DISTINCT ao2.install_id) FROM asset_occurrence ao2
                         WHERE ao2.asset_id = a.id) AS install_count
                FROM asset a
                WHERE {where}
                ORDER BY a.id ASC
                LIMIT ? OFFSET ?""",
            (*params, per_page, offset),
        ).fetchall()

        # In-tileset check — names live in Ja2Set.dat.xml. Build the
        # set once for all rows we're about to serialize.
        names_in_ts: set[str] = set()
        if xml and tileset is not None:
            try:
                names_in_ts = _names_in_tileset(Path(xml), tileset)
            except Exception:  # noqa: BLE001
                pass

        # Per-row tag union.
        items: list[LibrarySti] = []
        for row in rows:
            name = (row["relpath"] or "").replace("\\", "/").split("/")[-1]
            tag_rows = conn.execute(
                """SELECT DISTINCT t.name FROM subframe_in_asset sfia
                   JOIN subframe_tag st ON st.subframe_id = sfia.subframe_id
                   JOIN tag t ON t.id = st.tag_id
                   WHERE sfia.asset_id = ?
                   ORDER BY t.name""",
                (row["id"],),
            ).fetchall()
            tags = [r["name"] for r in tag_rows]
            items.append(LibrarySti(
                sha256=row["sha256"],
                width=row["width"],
                height=row["height"],
                frame_count=row["frame_count"],
                has_jsd=bool(row["has_jsd"]),
                kind=row["kind"],
                name=name or "(unknown)",
                install_count=row["install_count"],
                in_current_tileset=name.lower() in names_in_ts,
                tags=tags,
            ))

        return LibraryStiList(
            page=page,
            per_page=per_page,
            total=total,
            tag_filter=tag,
            query=q,
            items=items,
        )
    finally:
        conn.close()


@router.get("/stis/{sha256}", response_model=LibraryStiDetail)
def get_sti_detail(sha256: str):
    """Per-STI detail — occurrences across installs, tags, dimensions.
    Used by the library detail modal."""
    if not re.fullmatch(r"[0-9a-f]{64}", sha256):
        raise HTTPException(400, {"error": "BAD_SHA",
                                   "message": "sha256 must be 64 hex chars"})
    conn = _catalog()
    try:
        asset = conn.execute(
            """SELECT id, sha256, kind, width, height, frame_count,
                      has_jsd, size_bytes
               FROM asset WHERE sha256 = ?""",
            (sha256,),
        ).fetchone()
        if not asset:
            raise HTTPException(404, {"error": "STI_NOT_FOUND"})

        occs = conn.execute(
            """SELECT ao.relpath, ao.is_in_slf, ao.slf_member,
                      i.id AS install_id, i.label, i.root_path
               FROM asset_occurrence ao
               JOIN install i ON i.id = ao.install_id
               WHERE ao.asset_id = ?
               ORDER BY ao.is_in_slf ASC, ao.id ASC""",
            (asset["id"],),
        ).fetchall()
        occurrences = [LibraryStiOccurrence(
            install_id=r["install_id"],
            install_label=r["label"],
            install_root=r["root_path"],
            relpath=r["relpath"],
            is_in_slf=bool(r["is_in_slf"]),
            slf_member=r["slf_member"],
        ) for r in occs]

        tag_rows = conn.execute(
            """SELECT DISTINCT t.name FROM subframe_in_asset sfia
               JOIN subframe_tag st ON st.subframe_id = sfia.subframe_id
               JOIN tag t ON t.id = st.tag_id
               WHERE sfia.asset_id = ?
               ORDER BY t.name""",
            (asset["id"],),
        ).fetchall()
        tags = [r["name"] for r in tag_rows]

        return LibraryStiDetail(
            sha256=asset["sha256"],
            kind=asset["kind"],
            width=asset["width"],
            height=asset["height"],
            frame_count=asset["frame_count"],
            has_jsd=bool(asset["has_jsd"]),
            size_bytes=asset["size_bytes"],
            tags=tags,
            occurrences=occurrences,
        )
    finally:
        conn.close()


@router.get("/stis/{sha256}/thumb")
def get_sti_thumb(sha256: str):
    """Serve the cached thumbnail PNG. Asset Browser shards by first
    two hex chars: `data/thumbs/<ab>/<sha256>.png`."""
    if not re.fullmatch(r"[0-9a-f]{64}", sha256):
        raise HTTPException(400, {"error": "BAD_SHA"})
    path = _THUMBS_DIR / sha256[:2] / f"{sha256}.png"
    if not path.is_file():
        raise HTTPException(404, {"error": "THUMB_NOT_FOUND",
                                   "message": str(path)})
    return Response(
        content=path.read_bytes(),
        media_type="image/png",
        headers={"Cache-Control": "max-age=86400"},
    )


@router.get("/stis/{sha256}/subs", response_model=LibrarySubList)
def list_sti_subs(sha256: str):
    """List every sub-frame of the given STI. Powers the per-sub viewer
    that opens when the user clicks "View subs" on a library entry —
    user request 2026-05-24:

        "the viewer there needs to allow you to view subframes if you
         want and import an indiviudal or all subframes when you are
         importing."

    The Asset Browser already tracks sub identity via the `subframe`
    table + the (subframe_id, asset_id, sub_idx) `subframe_in_asset`
    rows; we just join + return. Tags are joined separately because
    the per-sub tag table is many-to-many.
    """
    if not re.fullmatch(r"[0-9a-f]{64}", sha256):
        raise HTTPException(400, {"error": "BAD_SHA"})
    conn = _catalog()
    try:
        asset = conn.execute(
            "SELECT id FROM asset WHERE sha256 = ?", (sha256,),
        ).fetchone()
        if not asset:
            raise HTTPException(404, {"error": "STI_NOT_FOUND"})
        # One row per (sub_idx, subframe). An STI can technically
        # reference the same sub-frame at two sub-indices (rare —
        # happens when a designer dupes a frame); we surface both
        # rows so the UI shows the duplicates honestly.
        rows = conn.execute(
            """SELECT sfia.sub_idx, sf.sha256, sf.width, sf.height
               FROM subframe_in_asset sfia
               JOIN subframe sf ON sf.id = sfia.subframe_id
               WHERE sfia.asset_id = ?
               ORDER BY sfia.sub_idx ASC""",
            (asset["id"],),
        ).fetchall()
        subs: list[LibrarySub] = []
        for r in rows:
            tag_rows = conn.execute(
                """SELECT DISTINCT t.name FROM subframe_tag st
                   JOIN tag t ON t.id = st.tag_id
                   WHERE st.subframe_id = (SELECT id FROM subframe WHERE sha256 = ?)""",
                (r["sha256"],),
            ).fetchall()
            subs.append(LibrarySub(
                sub_idx=r["sub_idx"],
                sha256=r["sha256"],
                width=r["width"],
                height=r["height"],
                tags=[tr["name"] for tr in tag_rows],
            ))
        return LibrarySubList(sti_sha256=sha256, subs=subs)
    finally:
        conn.close()


@router.get("/subframes/{sub_sha256}/thumb")
def get_subframe_thumb(sub_sha256: str):
    """Per-sub thumbnail PNG. Asset_Browser shards by first two hex
    chars: `data/subframes/<ab>/<sha256>.png`. We proxy through the
    sidecar so the frontend's auth token works for both STI and
    sub-level thumbs from a single origin."""
    if not re.fullmatch(r"[0-9a-f]{64}", sub_sha256):
        raise HTTPException(400, {"error": "BAD_SHA"})
    path = _SUBFRAMES_DIR / sub_sha256[:2] / f"{sub_sha256}.png"
    if not path.is_file():
        raise HTTPException(404, {
            "error": "SUB_THUMB_NOT_FOUND",
            "message": str(path),
        })
    return Response(
        content=path.read_bytes(),
        media_type="image/png",
        headers={"Cache-Control": "max-age=86400"},
    )


@router.get("/tilesets/{tileset}/loose-slots",
            response_model=LooseSlotList)
def list_loose_slots(tileset: int):
    """List every slot in `tileset` whose registered STI is a loose
    file on disk (mutable). Drives the destination dropdown of the
    inject-sub flow — SLF-only slots are excluded because the v1
    inject path can't extract from SLFs.
    """
    state = get_state()
    info = state.active()
    if info is None:
        raise HTTPException(400, {"error": "NO_ACTIVE_INSTALL"})
    install_root = Path(info.path)
    xml_path: Optional[Path] = None
    for layer in ("Data-1.13", "Data-DMK", "Data"):
        for name in ("Ja2Set.dat.xml", "JA2SET.DAT.XML", "ja2set.dat.xml"):
            candidate = install_root / layer / name
            if candidate.is_file():
                xml_path = candidate
                break
        if xml_path:
            break
    if not xml_path:
        raise HTTPException(404, {"error": "JA2SET_XML_NOT_FOUND"})
    slots = _find_loose_tileset_stis(install_root, xml_path, tileset)
    return LooseSlotList(tileset=tileset, slots=slots)


@router.post("/stis/{src_sha256}/inject-sub", response_model=InjectSubResult)
def inject_sub(src_sha256: str, body: InjectSubBody):
    """Append one sub-frame from a library STI onto an existing
    tileset slot's STI. v1 constraints:
      - Destination must be loose-on-disk (SLF-resident STIs aren't
        supported — extract first, then inject).
      - Palettes must match unless force=true.
      - Source's frame must be reachable from the catalog.

    Writes a `.sti.bak` on first edit per session (separate from
    Ja2Set.dat.xml's .bak — different file, separate backup).
    Side-effects:
      - Atlas cache for this tileset is invalidated so the new sub
        renders on next paint.

    Returns the assigned `new_sub_index` (== old frame count) so the
    UI can immediately surface the resulting sub as a brush option.
    """
    if body.src_sha256 != src_sha256:
        raise HTTPException(400, {"error": "SHA_MISMATCH"})
    if not re.fullmatch(r"[0-9a-f]{64}", src_sha256):
        raise HTTPException(400, {"error": "BAD_SHA"})

    # Locate active install + xml.
    state = get_state()
    info = state.active()
    if info is None:
        raise HTTPException(400, {"error": "NO_ACTIVE_INSTALL"})
    install_root = Path(info.path)
    xml_path: Optional[Path] = None
    for layer in ("Data-1.13", "Data-DMK", "Data"):
        for name in ("Ja2Set.dat.xml", "JA2SET.DAT.XML", "ja2set.dat.xml"):
            candidate = install_root / layer / name
            if candidate.is_file():
                xml_path = candidate
                break
        if xml_path:
            break
    if not xml_path:
        raise HTTPException(404, {"error": "JA2SET_XML_NOT_FOUND"})

    # Resolve destination slot → file on disk.
    loose_slots = _find_loose_tileset_stis(install_root, xml_path, body.tileset)
    dest = next((s for s in loose_slots if s.slot == body.target_slot), None)
    if dest is None:
        raise HTTPException(404, {
            "error": "DEST_SLOT_NOT_LOOSE",
            "message": (
                f"slot {body.target_slot} in tileset {body.tileset} is not "
                "loose on disk — either the slot isn't registered, or its "
                "STI resolves only via an SLF archive. Inject-sub v1 only "
                "supports loose-file destinations; extract the SLF first."
            ),
            "tileset": body.tileset,
            "slot": body.target_slot,
        })
    dest_path = Path(dest.path)

    # Fetch source bytes from catalog.
    conn = _catalog()
    try:
        asset = conn.execute(
            "SELECT id FROM asset WHERE sha256 = ?", (src_sha256,),
        ).fetchone()
        if not asset:
            raise HTTPException(404, {"error": "STI_NOT_FOUND"})
        src_bytes, _src_info = _resolve_asset_bytes(asset["id"], conn)
    finally:
        conn.close()

    # Load both STIs via ja2py + validate.
    try:
        from ja2py.fileformats.Sti import load_8bit_sti, save_8bit_sti
        from ja2py.content.Image import Images8Bit
    except ImportError as e:
        raise HTTPException(500, {
            "error": "JA2PY_UNAVAILABLE",
            "message": f"inject-sub requires ja2py: {e}",
        })
    import io as _io
    try:
        src_img = load_8bit_sti(_io.BytesIO(src_bytes))
    except Exception as e:  # noqa: BLE001
        raise HTTPException(500, {
            "error": "SRC_LOAD_FAILED",
            "message": (
                f"could not load source as 8-bit STI: {type(e).__name__}: {e}. "
                "Only indexed STIs support sub-injection."
            ),
        })
    if body.src_sub < 0 or body.src_sub >= len(src_img.images):
        raise HTTPException(400, {
            "error": "SUB_OUT_OF_RANGE",
            "message": (
                f"source sub {body.src_sub} out of range — STI has "
                f"{len(src_img.images)} frame(s)"
            ),
            "sub_idx": body.src_sub,
            "frame_count": len(src_img.images),
        })
    try:
        with dest_path.open("rb") as fh:
            dest_img = load_8bit_sti(fh)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(500, {
            "error": "DEST_LOAD_FAILED",
            "message": (
                f"could not load destination STI {dest_path.name}: "
                f"{type(e).__name__}: {e}"
            ),
        })

    # Palette compatibility check. Refusing here protects the user
    # from a silent visual disaster — appended frames render with the
    # destination's palette regardless of where they came from.
    if not _palettes_match(src_img.palette, dest_img.palette) and not body.force:
        raise HTTPException(409, {
            "error": "PALETTE_MISMATCH",
            "message": (
                "source and destination STIs use different 8-bit palettes. "
                "Appending the source frame would render with the destination's "
                "palette and look color-shifted. Set force=true to inject anyway, "
                "or add the source as a new slot via the regular add flow."
            ),
            "tileset": body.tileset,
            "slot": body.target_slot,
        })

    # Backup the destination on first edit per session. The .bak
    # convention mirrors Ja2Set.dat.xml's first-edit backup; same
    # idempotency (won't clobber existing .bak).
    bak_path = dest_path.with_suffix(dest_path.suffix + ".bak")
    bak_str: Optional[str] = None
    if not bak_path.exists():
        try:
            shutil.copy2(dest_path, bak_path)
            bak_str = str(bak_path)
        except OSError as e:
            raise HTTPException(500, {
                "error": "BACKUP_FAILED",
                "message": f"could not write {bak_path}: {e}",
            })
    else:
        bak_str = str(bak_path)

    # Append the source frame onto the destination + write back.
    new_sub_index = len(dest_img.images)
    new_images = list(dest_img.images) + [src_img.images[body.src_sub]]
    # Re-establish container dimensions. If the appended frame would
    # widen the bounding box, the existing dimensions stay (per-frame
    # render uses its own size); we only push wider if the dest was
    # narrower than the new frame. Matches what facegear extension
    # does for transparent backfill — see mercwizard_core.facegear.
    new_w = max(dest_img.width, src_img.images[body.src_sub].image.size[0])
    new_h = max(dest_img.height, src_img.images[body.src_sub].image.size[1])
    try:
        new_container = Images8Bit(
            images=new_images,
            palette=dest_img.palette,
            width=new_w, height=new_h,
        )
    except ValueError as e:
        # ja2py validates per-frame palette equality. force=true got
        # us past the palette guard above; this catches the actual
        # write rejection.
        raise HTTPException(409, {
            "error": "PALETTE_MISMATCH_HARD",
            "message": (
                "ja2py refused to write the combined STI because the per-frame "
                f"palettes don't match: {e}. force=true only bypasses the soft "
                "check; the underlying STI format requires palette parity."
            ),
        })
    try:
        with dest_path.open("wb") as fh:
            save_8bit_sti(new_container, fh)
    except OSError as e:
        # Try to restore from the .bak we just wrote.
        if bak_path.exists():
            try:
                shutil.copy2(bak_path, dest_path)
            except OSError:
                pass
        raise HTTPException(500, {
            "error": "WRITE_FAILED",
            "message": f"could not write {dest_path}: {e}",
        })

    # Invalidate atlas cache for this tileset.
    from .mapforge import _ATLAS_CACHE
    for d in _ATLAS_CACHE.glob(f"{body.tileset}_*"):
        try:
            shutil.rmtree(d, ignore_errors=True)
        except OSError:
            pass

    return InjectSubResult(
        tileset=body.tileset,
        slot=body.target_slot,
        sti_filename=dest.filename,
        new_sub_index=new_sub_index,
        frames_after=len(new_images),
        backup_path=bak_str,
    )


@router.get("/tags", response_model=list[LibraryTag])
def list_tags():
    """Available tags + how many subframes each carries. Powers the
    library's tag-filter dropdown."""
    conn = _catalog()
    try:
        rows = conn.execute(
            """SELECT t.name, t.source, COUNT(st.id) AS n
               FROM tag t LEFT JOIN subframe_tag st ON st.tag_id = t.id
               GROUP BY t.id
               HAVING n > 0
               ORDER BY n DESC""",
        ).fetchall()
        return [LibraryTag(name=r["name"], subframe_count=r["n"],
                            source=r["source"]) for r in rows]
    finally:
        conn.close()


@router.post("/stis/{sha256}/add-to-tileset",
             response_model=AddStiToTilesetResult)
def add_sti_to_tileset(
    sha256: str,
    body: AddStiToTilesetBody,
    response: Response,
):
    """Copy a catalog STI into the active install and register it in
    `Ja2Set.dat.xml`.

    Steps (each atomic with rollback on failure):
      1. Resolve the asset's bytes from the FIRST available occurrence
         (loose preferred over SLF).
      2. Locate the active install + its Ja2Set.dat.xml.
      3. Pick the target slot (caller-supplied or lowest free).
      4. Write the .sti to `<install>/Data-1.13/Tilesets/<tileset>/`.
      5. If the asset has a sibling .jsd in any occurrence, copy that
         too (catalog tracks .jsd presence via asset.has_jsd flag).
      6. Back up Ja2Set.dat.xml to .bak on FIRST edit per session
         (idempotent — won't clobber an existing .bak).
      7. Edit the XML to add `<file index="N">filename.sti</file>`
         under the tileset's `<Files>` block.
      8. Invalidate the on-disk atlas cache for this tileset so the
         next render rebuilds (the cache fingerprint includes the slot
         map, so changing the map naturally invalidates anyway, but
         we delete eagerly to avoid serving stale entries from any
         in-memory pyobject reuse).
    """
    if body.sha256 != sha256:
        raise HTTPException(400, {"error": "SHA_MISMATCH"})
    if not re.fullmatch(r"[0-9a-f]{64}", sha256):
        raise HTTPException(400, {"error": "BAD_SHA"})

    # 1+2. Active install + xml path
    state = get_state()
    info = state.active()
    if info is None:
        raise HTTPException(400, {"error": "NO_ACTIVE_INSTALL"})
    install_root = Path(info.path)
    # Find Ja2Set.dat.xml under any data-layer dir.
    xml_path: Optional[Path] = None
    for layer in ("Data-1.13", "Data-DMK", "Data"):
        for name in ("Ja2Set.dat.xml", "JA2SET.DAT.XML", "ja2set.dat.xml"):
            candidate = install_root / layer / name
            if candidate.is_file():
                xml_path = candidate
                break
        if xml_path:
            break
    if not xml_path:
        raise HTTPException(404, {"error": "JA2SET_XML_NOT_FOUND"})

    # 1. Resolve bytes
    conn = _catalog()
    try:
        asset = conn.execute(
            "SELECT id, kind, has_jsd, size_bytes FROM asset WHERE sha256 = ?",
            (sha256,),
        ).fetchone()
        if not asset:
            raise HTTPException(404, {"error": "STI_NOT_FOUND"})
        sti_bytes, src_info = _resolve_asset_bytes(asset["id"], conn)
        # Single-sub extraction: when target_sub is set, write a fresh
        # STI containing only that one frame instead of copying the
        # source verbatim. The user gets one slot per imported sub
        # (caller loops over chosen subs at the UI layer). JSD is NOT
        # copied — a single-sub extract is treated as a 1×1 floor/object
        # not a multi-tile struct, regardless of the source's JSD.
        if body.target_sub is not None:
            sti_bytes = _extract_single_sub_bytes(sti_bytes, body.target_sub)
        # Try to also locate the .jsd companion (same basename, .jsd
        # suffix, same occurrence directory). Skipped for single-sub
        # extracts per the comment above.
        jsd_bytes: Optional[bytes] = None
        if asset["has_jsd"] and body.target_sub is None:
            src_root = Path(src_info["root_path"])
            src_rel = src_info["relpath"]
            if not src_info["is_in_slf"]:
                src_full = src_root / src_rel
                jsd_candidate = src_full.with_suffix(".jsd")
                if jsd_candidate.is_file():
                    jsd_bytes = jsd_candidate.read_bytes()
                else:
                    jsd_candidate_upper = src_full.with_suffix(".JSD")
                    if jsd_candidate_upper.is_file():
                        jsd_bytes = jsd_candidate_upper.read_bytes()
            # SLF .jsd lookup is more complex; skip for now and let the
            # user copy it manually if the asset comes from an SLF.
    finally:
        conn.close()

    # Resolve filename (catalog-specific — basename of the source asset,
    # _subN-suffixed for a single-sub extract).
    src_relpath = src_info["relpath"].replace("\\", "/")
    src_filename = src_relpath.split("/")[-1]
    if body.target_filename:
        target_filename = body.target_filename
    elif body.target_sub is not None:
        # Single-sub extract — suffix with _subN so the file doesn't
        # collide with a future whole-STI import of the same source.
        # Source basename "foo.sti" → "foo_sub3.sti".
        stem, _, ext = src_filename.rpartition(".")
        target_filename = f"{stem}_sub{body.target_sub}.{ext}" if stem else src_filename
    else:
        target_filename = src_filename
    if not re.fullmatch(r"[A-Za-z0-9_.-]+\.[Ss][Tt][Ii]", target_filename):
        raise HTTPException(400, {"error": "BAD_FILENAME",
            "message": "filename must be ASCII basename.sti"})
    # The Windows reserved-device-name guard now lives in the shared
    # _commit_sti_to_tileset choke point, so it also covers copy_tile_to_tileset.

    # Steps 3-8 (slot pick + write + jsd + backup + atomic XML + cache
    # bust) are shared with copy_tile_to_tileset via _commit_sti_to_tileset
    # so the two import surfaces can't drift on the append-only /
    # atomic-write engine-safety invariants.
    committed = _commit_sti_to_tileset(
        xml_path=xml_path,
        tileset=body.tileset,
        sti_bytes=sti_bytes,
        jsd_bytes=jsd_bytes,
        target_filename=target_filename,
        target_slot=body.target_slot,
        engine_max_tile_slot=body.engine_max_tile_slot,
        allow_above_cap=body.allow_above_cap,
        response=response,
    )

    return AddStiToTilesetResult(
        sha256=sha256,
        tileset=body.tileset,
        slot=committed.slot,
        filename=committed.filename,
        install_root=str(install_root),
        written_to=committed.written_to,
        xml_backup_path=committed.xml_backup_path,
        jsd_copied=committed.jsd_copied,
    )


def _resolve_live_tileset_sti(
    xml_path: Path, tileset: int, slot: int,
) -> tuple[str, bytes]:
    """Resolve (tileset, slot) of the ACTIVE install to its STI filename
    + raw bytes, reading the LIVE tileset (not the catalog).

    Filename comes from `Ja2Set.dat.xml` via `load_tileset_xml` (which
    overlays tile-0 inheritance the same way the engine's LoadMapTileset
    does). Bytes come from the install's tileset asset roots via the
    vendored `StiCache._find_loose` (loose dirs) / `_extract_from_slf`
    (Tilesets.slf) — the SAME dual lookup the renderer uses, so a tile
    that renders in the browser is resolvable here.

    Raises:
      404 SRC_SLOT_EMPTY    — no STI registered at (tileset, slot).
      404 SRC_STI_NOT_FOUND — registered but unresolvable on disk/SLF.
    """
    from mercwizard_core.mapforge_engine.iso_renderer import (
        StiCache, load_tileset_xml,
    )
    from .mapforge import _tileset_paths_for

    slot_map = load_tileset_xml(xml_path, tileset)
    sti_filename = slot_map.get(slot)
    if not sti_filename:
        raise HTTPException(404, {
            "error": "SRC_SLOT_EMPTY",
            "message": (
                f"tileset {tileset} has no STI registered at slot {slot} "
                "(checked the tileset block + tile-0 inheritance)."
            ),
            "src_tileset": tileset,
            "src_slot": slot,
        })
    loose_dirs, slf_paths = _tileset_paths_for(xml_path)
    cache = StiCache(tileset, loose_dirs=loose_dirs, slf_paths=slf_paths)
    loose = cache._find_loose(sti_filename)
    if loose is not None:
        data = loose.read_bytes()
    else:
        data = cache._extract_from_slf(sti_filename)
    if data is None:
        raise HTTPException(404, {
            "error": "SRC_STI_NOT_FOUND",
            "message": (
                f"slot {slot} of tileset {tileset} points at {sti_filename!r} "
                "but that file couldn't be found in the install's loose "
                "tileset dirs or Tilesets.slf."
            ),
            "src_tileset": tileset,
            "src_slot": slot,
            "sti_filename": sti_filename,
        })
    return sti_filename, data


@router.post(
    "/tilesets/{src_tileset}/slots/{src_slot}/copy-to-tileset",
    response_model=CopyTileToTilesetResult,
)
def copy_tile_to_tileset(
    src_tileset: int,
    src_slot: int,
    body: CopyTileToTilesetBody,
    response: Response,
):
    """Copy a tile from one LIVE tileset slot into another tileset of the
    SAME active install, registering it as a NEW slot in Ja2Set.dat.xml.

    This is the "add a tile from another tileset into the current
    tileset" action behind the stock-tileset browser. Unlike
    `add_sti_to_tileset` (which copies from the external asset-library
    catalog), the source here is a slot already live in the install's
    own Ja2Set.dat.xml — resolved via the renderer's StiCache, never the
    catalog (the catalog isn't shipped in the public build).

    ENGINE-SAFETY (the design this implements proved these):
      - A sector stores tiles as (type, subindex); the engine rebuilds
        the global tile index from a COMPILE-TIME gNumTilesPerType table
        keyed by slot index, so APPENDING a new `<file index>` slot never
        shifts existing indices — existing maps stay valid. We only ever
        ADD a slot; we NEVER grow an existing slot's STI (that's the
        inject-sub route's job and a frame-truncation landmine).
      - target_slot DEFAULTS to the SOURCE slot index, keeping the copied
        tile in the same tile-type family (correct flags / layer /
        shadow-buddy / animation). Copying to a DIFFERENT slot changes
        the tile's type/behavior — the caller must opt into that.
      - HARD cap at engine_max_tile_slot (stock 150): a slot index >=
        NUMBEROFTILETYPES crashes the engine on sector load. Manual slots
        above the cap → 400 SLOT_ABOVE_ENGINE_CAP unless allow_above_cap.
      - Occupied destination slot → 409 SLOT_TAKEN (no silent overwrite),
        free-slot scan includes tile-0 inheritance.
      - Ja2Set.dat.xml write is atomic (tempfile + fsync + os.replace)
        with a first-edit .bak backup; atlas cache busted after.
      - If the source slot's STI has a sibling .jsd (multi-tile struct),
        the whole-STI copy carries the .jsd too (skipped for single-sub).
    """
    # 1. Active install + its Ja2Set.dat.xml (same resolution the other
    # write paths use).
    state = get_state()
    info = state.active()
    if info is None:
        raise HTTPException(400, {"error": "NO_ACTIVE_INSTALL"})
    install_root = Path(info.path)
    xml_path: Optional[Path] = None
    for layer in ("Data-1.13", "Data-DMK", "Data"):
        for name in ("Ja2Set.dat.xml", "JA2SET.DAT.XML", "ja2set.dat.xml"):
            candidate = install_root / layer / name
            if candidate.is_file():
                xml_path = candidate
                break
        if xml_path:
            break
    if not xml_path:
        raise HTTPException(404, {"error": "JA2SET_XML_NOT_FOUND"})

    # No-op guard: copying a tile onto ITS OWN slot adds nothing (the
    # slot is already there). Only fires for the exact self-copy — same
    # tileset, same effective slot, no auto-pick (auto_pick / an explicit
    # different slot is a legit duplicate into a new slot). The frontend
    # hides the action when dest == src tileset anyway; this guards other
    # clients.
    if (
        body.dest_tileset == src_tileset
        and not body.auto_pick
        and (body.target_slot is None or body.target_slot == src_slot)
    ):
        raise HTTPException(400, {
            "error": "SAME_TILESET_NOOP",
            "message": (
                f"slot {src_slot} is already in tileset {src_tileset} — "
                "copying it onto itself is a no-op. Pick a different "
                "destination tileset (or a different target slot)."
            ),
        })

    # 2. Resolve the SOURCE bytes from the LIVE tileset (not the catalog).
    sti_filename, sti_bytes = _resolve_live_tileset_sti(
        xml_path, src_tileset, src_slot,
    )

    # JSD companion of the source slot — only for a whole-STI copy. Uses
    # mapforge's loose+SLF JSD lookup so a struct shipped inside
    # Tilesets.slf still carries its .jsd. Single-sub extracts are a 1×1
    # tile, never a multi-tile struct, so they skip the JSD.
    jsd_bytes: Optional[bytes] = None
    if body.target_sub is None:
        from .mapforge import _find_jsd_bytes
        found = _find_jsd_bytes(xml_path, src_tileset, sti_filename)
        if found is not None:
            jsd_bytes = found[0]
    else:
        sti_bytes = _extract_single_sub_bytes(sti_bytes, body.target_sub)

    # 3. Destination filename. Whole-STI keeps the source basename;
    # single-sub gets an _subN suffix so it can't collide with a future
    # whole-STI copy of the same source.
    target_filename = sti_filename.replace("\\", "/").split("/")[-1]
    if body.target_sub is not None:
        stem, _, ext = target_filename.rpartition(".")
        if stem:
            target_filename = f"{stem}_sub{body.target_sub}.{ext}"
    if not re.fullmatch(r"[A-Za-z0-9_.-]+\.[Ss][Tt][Ii]", target_filename):
        raise HTTPException(400, {"error": "BAD_FILENAME",
            "message": "source filename isn't an ASCII basename.sti"})

    # 4. Resolve the destination slot:
    #   - auto_pick → None, so _commit_sti_to_tileset runs its
    #     cap-bounded free-slot scan (the 409 SLOT_TAKEN recovery path).
    #   - explicit target_slot → honored (cap-guarded downstream).
    #   - neither → default to the SOURCE slot (same tile-type family —
    #     the safe default the design mandates).
    if body.auto_pick:
        target_slot = None
    elif body.target_slot is not None:
        target_slot = body.target_slot
    else:
        target_slot = src_slot

    # 5. Commit — shared write/append/backup/cache-bust machinery.
    committed = _commit_sti_to_tileset(
        xml_path=xml_path,
        tileset=body.dest_tileset,
        sti_bytes=sti_bytes,
        jsd_bytes=jsd_bytes,
        target_filename=target_filename,
        target_slot=target_slot,
        engine_max_tile_slot=body.engine_max_tile_slot,
        allow_above_cap=body.allow_above_cap,
        response=response,
    )

    return CopyTileToTilesetResult(
        src_tileset=src_tileset,
        src_slot=src_slot,
        dest_tileset=body.dest_tileset,
        slot=committed.slot,
        filename=committed.filename,
        install_root=str(install_root),
        written_to=committed.written_to,
        xml_backup_path=committed.xml_backup_path,
        jsd_copied=committed.jsd_copied,
    )
