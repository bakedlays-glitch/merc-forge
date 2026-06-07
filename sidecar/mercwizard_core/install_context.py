"""`InstallContext` — VFS-aware façade over a JA2 1.13 install.

Replaces the wizard's old "everything lives at install_root/Data-1.13/..."
assumption with a layout-aware view. Every file the wizard reads or
writes goes through one of this class's methods, which consult the
`VfsLayout` to find the right layer and the install's local convention
to pick the right path inside it.

Layout-flavor detection (set once at construction):
- `merc_edt_root_layout`: vanilla puts per-file merc EDTs at
  `BinaryData/MercEdt/<n>.EDT`. Vengeance puts them at `MercEdt/<n>.EDT`
  (no `BinaryData/` prefix). Detected by probing both during init.
- `voice_layout`: vanilla uses `Speech/<voice_index>/*.wav`. Vengeance
  uses `Speech/<slot>_<idx>.ogg` (slot-prefixed at root). Detected by
  scanning whichever Speech dir actually exists.
- `gear_subdir`: vanilla puts gear directly under `TableData/`.
  AIMv53-derived mods (incl. Vengeance) use `TableData/Inventory/`.
  Detected by probing for the file.

The context is read-write-aware: `xxx_path(...)` returns the right path
for either operation (writes use `VfsLayout.resolve_write`, which
prefers the layer the file already lives in, falling back to the mod
content layer for new files).
"""
from __future__ import annotations

from dataclasses import dataclass, field
import logging
import os
import threading
from pathlib import Path
from typing import Optional

from .vfs import VfsConfigError, VfsLayout, _legacy_layout, parse_vfs_config

_logger = logging.getLogger(__name__)

# Module-level SLF open cache. Parsing an SLF header + entry table is
# ~50ms for a 100 MB archive (Faces.slf). The roster portrait endpoint
# hits this hot path 100+ times per page load — parsing once and
# reusing the SlfFS instance turns 100×50ms into 1×50ms + 100×0.1ms
# (dict lookup). Keyed by (resolved_path, mtime_ns) so an external
# write to the SLF invalidates naturally on the next call.
#
# Lock added 2026-05-25: FastAPI runs handlers in a threadpool, so a
# burst of parallel roster-cell portrait requests (16+ at once on the
# 16-column grid) can race here. Without the lock, two threads can
# both miss the cache, both open a fresh SlfFS for the same path,
# both insert — the loser's SlfFS leaks (file handle stays open
# until process exit). Lock also makes FIFO eviction atomic so the
# dict can't be mutated mid-iteration.
_SLF_CACHE: dict[tuple[str, int], object] = {}
_SLF_CACHE_MAX = 8  # Faces.slf + BigFaces.slf + a few aliases is plenty
_SLF_CACHE_LOCK = threading.Lock()


def _open_slf_cached(slf_path: Path):
    """Return a cached SlfFS handle for `slf_path`, or None on failure.

    Cache key includes mtime so an external rewrite invalidates without
    explicit eviction. Capped at _SLF_CACHE_MAX entries (FIFO) so
    pathological installs with many SLFs can't grow unbounded.

    Thread-safe: the lock spans the read-check + insert window so two
    threads racing on the same key never both open + insert.
    """
    try:
        st = slf_path.stat()
    except OSError:
        return None
    key = (str(slf_path.resolve()), st.st_mtime_ns)
    with _SLF_CACHE_LOCK:
        cached = _SLF_CACHE.get(key)
        if cached is not None:
            return cached
    # Open OUTSIDE the lock — SlfFS construction reads from disk and
    # we don't want to serialize that on every cache miss. Two threads
    # missing on the same key may both open; the loser gets discarded
    # in the second critical section below.
    try:
        from ja2py.fileformats.SlfFS import SlfFS
        slf = SlfFS(str(slf_path))
    except Exception as e:  # noqa: BLE001 — SLF lib raises misc errors
        # Pre-fix this swallowed silently; a corrupt SLF (mid-write mod
        # update, malformed archive) returned None for every portrait
        # read with no breadcrumb. Log at WARNING so the user has
        # something to chase. Bug-review finding D3.
        _logger.warning(
            "SlfFS open failed for %s: %s: %s",
            slf_path, type(e).__name__, e,
        )
        return None
    with _SLF_CACHE_LOCK:
        # Re-check: another thread may have populated while we were
        # opening. If so, drop ours and return theirs.
        winner = _SLF_CACHE.get(key)
        if winner is not None:
            try:
                slf.close()
            except Exception:  # noqa: BLE001
                pass
            return winner
        if len(_SLF_CACHE) >= _SLF_CACHE_MAX:
            try:
                first_key = next(iter(_SLF_CACHE))
                evicted = _SLF_CACHE.pop(first_key)
                # Close the evicted handle so its underlying file
                # descriptor releases. Without this, on Windows the
                # SLF stays locked even after eviction, which blocks
                # mod updates that want to overwrite Faces.slf.
                try:
                    evicted.close()  # type: ignore[attr-defined]
                except Exception:  # noqa: BLE001
                    pass
            except StopIteration:
                pass
        _SLF_CACHE[key] = slf
        return slf


# ──────────────────────────────────────────────────────────────────────────
#  Layout flavor enums (set during init, then used by the path helpers)
# ──────────────────────────────────────────────────────────────────────────


@dataclass
class LayoutFlavor:
    """The set of mod-specific conventions for THIS install."""
    # Where per-file merc EDTs live:
    #   "binarydata"  → BinaryData/MercEdt/<n>.EDT  (vanilla 1.13)
    #   "root"        → MercEdt/<n>.EDT             (Vengeance, AIMNAS, etc.)
    merc_edt_root: str = "binarydata"

    # Where voice clips live:
    #   "subdir"      → Speech/<voice_index>/<file>.wav  (vanilla)
    #   "slot_prefix" → Speech/<slot>_<idx>.ogg          (Vengeance)
    voice_layout: str = "subdir"

    # Whether MercStartingGear sits under a subfolder:
    #   "flat"        → TableData/MercStartingGear.xml          (vanilla)
    #   "inventory"   → TableData/Inventory/MercStartingGear.xml (AIMv53/Vengeance)
    gear_subdir: str = "flat"

    # Whether the mod has any of these extra per-slot XML tables.
    # (None of these exist in vanilla 1.13.)
    has_merc_opinions: bool = False
    has_merc_quote: bool = False
    has_merc_availability: bool = False
    has_face_gear: bool = False
    has_backgrounds: bool = False
    has_civ_group_names: bool = False

    # Whether the mod organizes audio by slot-prefix in extra dirs
    # (Battlesnds/<slot>_*.ogg, NPC_Speech/<slot>_*.ogg).
    has_battlesnds: bool = False
    has_npc_speech: bool = False
    # Snitch/names is a sub-system of Speech/ for "other merc says THIS
    # merc's name" clips. Used by Vengeance.
    has_snitch_names: bool = False


# Extra-table → (filename, slot-tag) lookup used everywhere.
# `slot_tag` is the XML element name that holds the per-slot key.
EXTRA_TABLES: dict[str, tuple[str, str]] = {
    "merc_opinions":     ("MercOpinions.xml",     "uiIndex"),
    "merc_quote":        ("MercQuote.xml",        "uiIndex"),
    "merc_availability": ("MercAvailability.xml", "ProfilId"),
    "face_gear":         ("FaceGear.xml",         "uiIndex"),
    "backgrounds":       ("Backgrounds.xml",      "uiIndex"),
    "civ_group_names":   ("CivGroupNames.xml",    "uiIndex"),
}


# ──────────────────────────────────────────────────────────────────────────
#  InstallContext
# ──────────────────────────────────────────────────────────────────────────


@dataclass
class InstallContext:
    """VFS-aware view of one JA2 install.

    Constructed by `make_install_context(install_root)`. Every wizard
    operation that touches the install should go through this object
    instead of building paths from `install_root` directly.
    """
    layout: VfsLayout
    flavor: LayoutFlavor

    @property
    def install_root(self) -> Path:
        return self.layout.install_root

    @property
    def is_legacy(self) -> bool:
        return self.layout.is_legacy

    # ── XML tables (TableData/) ──────────────────────────────────────────

    def profiles_xml_path(self, *, for_write: bool = False) -> Path:
        rel = "TableData/MercProfiles.xml"
        return self.layout.resolve_write(rel) if for_write else (
            self.layout.resolve_read(rel) or self.layout.resolve_write(rel)
        )

    def aim_xml_path(self, *, for_write: bool = False) -> Path:
        rel = "TableData/AIMAvailability.xml"
        return self.layout.resolve_write(rel) if for_write else (
            self.layout.resolve_read(rel) or self.layout.resolve_write(rel)
        )

    def merc_xml_path(self, *, for_write: bool = False) -> Path:
        """MercAvailability.xml — the M.E.R.C. equivalent of AIMAvailability.

        Returns the install's canonical path. ALWAYS returns a Path —
        never None. For reads with no existing file, returns the
        would-be-created path; downstream parsers (`read_all`,
        `lookup_merc_bio_id`) detect "file missing" and return empty
        results without exploding. Bug-review finding E5 noted that
        callers wrote `if merc_xml_path is not None` guards expecting
        None for missing files; those guards were dead code. Return
        type narrowed from `Optional[Path]` to `Path` so static
        callers no longer have a misleading None to handle.

        Vanilla path is TableData/MercAvailability.xml.
        """
        rel = "TableData/MercAvailability.xml"
        if for_write:
            # On write, fall back to the canonical mod-content layer even if
            # the file doesn't exist yet — the upsert path needs to create it.
            return self.layout.resolve_write(rel)
        existing = self.layout.resolve_read(rel)
        if existing is not None:
            return existing
        # For reads, returning the write-target path lets the parser silently
        # treat it as "file missing → no rows" without exploding.
        return self.layout.resolve_write(rel)

    def gear_xml_path(self, *, for_write: bool = False) -> Path:
        subdir = "Inventory/" if self.flavor.gear_subdir == "inventory" else ""
        rel = f"TableData/{subdir}MercStartingGear.xml"
        return self.layout.resolve_write(rel) if for_write else (
            self.layout.resolve_read(rel) or self.layout.resolve_write(rel)
        )

    def extra_table_path(self, key: str, *, for_write: bool = False) -> Optional[Path]:
        """Look up a mod-specific table path (e.g. MercOpinions.xml).

        Returns None if the install doesn't have that table.
        """
        if key not in EXTRA_TABLES:
            raise ValueError(f"Unknown extra table: {key}")
        flavor_flag = f"has_{key}"
        if not getattr(self.flavor, flavor_flag, False):
            return None
        filename, _slot_tag = EXTRA_TABLES[key]
        rel = f"TableData/{filename}"
        return self.layout.resolve_write(rel) if for_write else (
            self.layout.resolve_read(rel) or self.layout.resolve_write(rel)
        )

    # ── EDT bios (BinaryData/ or root, depending on flavor) ──────────────

    def aim_bios_edt_path(self, *, for_write: bool = False) -> Path:
        rel = "BinaryData/AIMBIOS.EDT"
        return self.layout.resolve_write(rel) if for_write else (
            self.layout.resolve_read(rel) or self.layout.resolve_write(rel)
        )

    def merc_bios_edt_path(self, *, for_write: bool = False) -> Path:
        rel = "BinaryData/MERCBIOS.EDT"
        return self.layout.resolve_write(rel) if for_write else (
            self.layout.resolve_read(rel) or self.layout.resolve_write(rel)
        )

    def per_file_merc_edt_path(self, ui_index: int, *, for_write: bool = False) -> Path:
        """Per-file expanded-MERC EDT — convention varies by mod."""
        prefix = "" if self.flavor.merc_edt_root == "root" else "BinaryData/"
        rel = f"{prefix}MercEdt/{ui_index}.EDT"
        return self.layout.resolve_write(rel) if for_write else (
            self.layout.resolve_read(rel) or self.layout.resolve_write(rel)
        )

    def per_file_npc_edt_path(self, ui_index: int, *, for_write: bool = False) -> Path:
        """NPC dialogue EDT — at NPCData/<n>.EDT for vanilla, sometimes also
        at root for mods. We assume BinaryData/NPCDATA/ vanilla convention
        and let VFS resolve_read fall through to wherever it actually is."""
        rel = f"BinaryData/NPCDATA/{ui_index}.EDT"
        return self.layout.resolve_write(rel) if for_write else (
            self.layout.resolve_read(rel) or self.layout.resolve_write(rel)
        )

    # ── Faces ────────────────────────────────────────────────────────────

    def faces_dir(self, *, for_write: bool = False) -> Path:
        """The base `faces/` (or `Faces/`) directory in the mod content layer.

        Subdirs (33Face, 65Face, BigFaces) are children of this. Camo dirs
        (DESERTCAMO etc.) are also children when the mod ships them.
        Tries lowercase first, then capitalized — mods are inconsistent.
        """
        for candidate in ("faces", "Faces"):
            mod_path = self.layout.mod_content_path(candidate)
            if mod_path.is_dir():
                return mod_path
        # No faces dir exists — return the lowercase write path as default
        return self.layout.mod_content_path("faces")

    def face_sti_path(self, face_index: int, size: str = "smallface", *, for_write: bool = False) -> Path:
        """A face STI at the requested size in the mod content layer.

        size: 'smallface' | 'face_65' | 'face_33' | 'bigface'

        For reads, probes for existing files under both case-variant
        subdirs (mods use `33face/` lowercase, `33Face/` mixed, etc.)
        and both extensions (`.sti`, `.STI`).

        For writes (for_write=True), bypasses the existing-file probe
        and returns the canonical lowercase write path directly. Pre-
        fix the for_write kwarg was accepted but ignored — writes
        landed wherever the read probe found a case-variant first
        (e.g. `Faces/65FACE/12.STI`), and subsequent reads using the
        wizard's other paths probed the lowercase variants and missed
        the write. On case-sensitive filesystems (Linux/macOS, WSL,
        case-sensitive Windows volumes) this silently lost the file.
        Bug-review finding E8.

        NB: This method only checks LOOSE files. Vanilla portraits for
        face_index 0-159 live inside `Data/Faces.slf` and won't be found
        by this method — use `face_sti_bytes()` instead for read paths
        that need SLF fallback (e.g. the roster portrait endpoint).
        """
        subdir_map = {
            "smallface": "",
            "face_65": "65Face/",
            "face_33": "33Face/",
            "bigface": "BigFaces/",
        }
        if size not in subdir_map:
            raise ValueError(f"Unknown face size: {size}")
        subdir = subdir_map[size]

        if for_write:
            # Canonical lowercase write target — consistent regardless
            # of what case-variant files already exist. Writers land
            # here so reads probing the lowercase set find them
            # immediately on next call.
            return self.layout.mod_content_path(f"faces/{subdir}{face_index}.sti")

        for face_base in ("faces", "Faces"):
            for subdir_variant in (subdir, subdir.lower()) if subdir else (subdir,):
                for ext in ("sti", "STI"):
                    rel = f"{face_base}/{subdir_variant}{face_index}.{ext}"
                    existing = self.layout.resolve_in_mod_content(rel)
                    if existing is not None:
                        return existing
        # No existing file — return the canonical lowercase read-target
        # path (the same path writes would use).
        return self.layout.mod_content_path(f"faces/{subdir}{face_index}.sti")

    def face_sti_bytes(
        self, face_index: int, size: str = "smallface",
    ) -> Optional[tuple[bytes, str]]:
        """Read a face STI's bytes + a versioned source-id.

        Returns `(bytes, source_id)` where `source_id` is a stable
        string that changes ONLY when the underlying art changes:

          - For loose files: `"file:<mtime_ns>:<size_bytes>"` — any
            external write (recompile, manual replace) bumps mtime.
          - For SLF entries: `"slf:<slf_mtime_ns>:<entry_path>"` — any
            external write to the SLF archive bumps mtime.

        Callers (notably the portrait route's PNG LRU) include
        source_id in their cache key so the cache invalidates by
        construction on any disk change. User feedback: "wont it
        have to refresh after you make a change or at least refresh
        the ones you changed?"

        Read path used by the roster portrait endpoint. Searches in order:
          1. Loose files in mod content (same probe as `face_sti_path`).
          2. SLF archives in every install data layer (Data-1.13, Data-DMK,
             Data, etc.) — extracts the entry bytes in memory, no disk
             round-trip.

        Returns None when neither location has the file.
        """
        # 1. Loose first — same probe order as face_sti_path so existing
        #    user-customized files override SLF entries (correct VFS
        #    precedence: mod content > vanilla SLF).
        subdir_map = {
            "smallface": "",
            "face_65": "65Face/",
            "face_33": "33Face/",
            "bigface": "BigFaces/",
        }
        if size not in subdir_map:
            raise ValueError(f"Unknown face size: {size}")
        subdir = subdir_map[size]

        # Loose probe (same as face_sti_path but across ALL data layers,
        # not just mod content — vanilla loose copies exist too on some
        # installs).
        for face_base in ("faces", "Faces"):
            for subdir_variant in (subdir, subdir.lower()) if subdir else (subdir,):
                for ext in ("sti", "STI"):
                    rel = f"{face_base}/{subdir_variant}{face_index}.{ext}"
                    # Check mod content first
                    existing = self.layout.resolve_in_mod_content(rel)
                    if existing is None:
                        # Fall through to any data layer
                        existing = self.layout.resolve_read(rel)
                    if existing is not None and existing.is_file():
                        try:
                            data = existing.read_bytes()
                            st = existing.stat()
                            source_id = f"file:{st.st_mtime_ns}:{st.st_size}"
                            return (data, source_id)
                        except OSError:
                            pass

        # 2. SLF fallback — scan every data layer for archives that might
        #    contain Faces/<idx>.<ext>. The vanilla pattern is
        #    `Data/Faces.slf` but some mods use `Data-1.13/Faces.slf` or
        #    different archive names; we probe all *.slf files in each
        #    data layer + check for the entry.
        # SLF entry layout: the vanilla/AIMNAS Faces.slf stores faces at the
        # ARCHIVE ROOT (e.g. "/65.STI"), NOT under a "Faces/" prefix, and
        # zero-pads single-digit indices to two digits ("/00.STI".."/09.STI").
        # The old "/Faces/{idx}.STI" candidate matched neither, so every
        # SLF-only face came back "not found" — the bulk of the blank roster.
        # Probe both prefixes (root + nested "Faces/") and both the plain and
        # 2-digit-padded names so every archive layout resolves.
        names = sorted({str(face_index), f"{face_index:02d}"})
        # Subdir case varies by archive (BigFaces vs BIGFACES vs bigfaces), so
        # probe each casing. smallface ("" subdir) collapses to just root.
        prefixes: list[str] = []
        for sv in {subdir, subdir.upper(), subdir.lower()}:
            prefixes.append(sv)
            prefixes.append(f"Faces/{sv}")
        candidates_in_slf: list[str] = []
        for pre in prefixes:
            for name in names:
                for ext in ("STI", "sti"):
                    candidates_in_slf.append(f"/{pre}{name}.{ext}")
        # Walk every data-layer directory's SLF list. Layer priority =
        # mod content first (the engine's VFS reads in profile order,
        # mod data layers before vanilla), so a mod-shipped Faces.slf
        # wins over vanilla Data/Faces.slf — matches the engine.
        seen_dirs: set[str] = set()
        for profile in reversed(self.layout.profiles):
            for loc in profile.locations:
                if not loc.is_directory:
                    continue
                key = str(loc.path)
                if key in seen_dirs:
                    continue
                seen_dirs.add(key)
                try:
                    slfs = sorted(loc.path.glob("*.slf")) + sorted(loc.path.glob("*.SLF"))
                except OSError:
                    continue
                for slf_path in slfs:
                    # Heuristic: only Faces*.slf / Data.slf-style archives
                    # are worth opening for portraits. Skip Maps.slf,
                    # speech.slf, etc. — they don't carry face STIs.
                    lname = slf_path.name.lower()
                    if "face" not in lname and lname != "data.slf":
                        continue
                    slf = _open_slf_cached(slf_path)
                    if slf is None:
                        continue
                    # Capture the SLF's mtime for the source_id so a mod
                    # update that replaces Faces.slf invalidates the
                    # caller's cache.
                    try:
                        slf_mtime_ns = slf_path.stat().st_mtime_ns
                    except OSError:
                        slf_mtime_ns = 0
                    for candidate in candidates_in_slf:
                        try:
                            if slf.isfile(candidate):
                                with slf.openbin(candidate, "r") as f:
                                    data = f.read()
                                source_id = (
                                    f"slf:{slf_mtime_ns}:{candidate}"
                                )
                                return (data, source_id)
                        except Exception as e:  # noqa: BLE001 — SLF lib raises misc errors
                            # Pre-fix this swallowed silently — a corrupt
                            # SLF made every roster portrait return None
                            # with no breadcrumb pointing at which SLF was
                            # at fault. Log + continue. Bug-review
                            # finding D6.
                            _logger.warning(
                                "SLF read failed for %s in %s: %s: %s",
                                candidate, slf_path,
                                type(e).__name__, e,
                            )
                            continue
        return None

    # ── Voice / audio ────────────────────────────────────────────────────

    # Audio / faces / big-items methods target the mod content profile
    # directly. Higher-priority media-only profiles (PCM, weapsounds,
    # music) often mount their own empty Speech/ or faces/ directories
    # that would otherwise shadow the real mod content. The wizard always
    # wants the mod's editable copy.

    def voice_dir_legacy(self, voice_index: int, *, for_write: bool = False) -> Path:
        """Vanilla-convention `Speech/<voice_index>/` directory."""
        return self.layout.mod_content_path(f"Speech/{voice_index}")

    def _resolve_mod_content_dir(self, rel: str) -> Path:
        """For reads: pick the FIRST location in the mod content profile
        whose `rel` directory exists. For writes: fall back to the first
        location (where new files would be created)."""
        existing = self.layout.resolve_in_mod_content(rel)
        if existing is not None and existing.is_dir():
            return existing
        return self.layout.mod_content_path(rel)

    def speech_root(self, *, for_write: bool = False) -> Path:
        return self._resolve_mod_content_dir("Speech")

    def battlesnds_root(self, *, for_write: bool = False) -> Path:
        return self._resolve_mod_content_dir("Battlesnds")

    def npc_speech_root(self, *, for_write: bool = False) -> Path:
        return self._resolve_mod_content_dir("NPC_Speech")

    def snitch_names_dir(self, *, alt: bool = False, for_write: bool = False) -> Path:
        rel = "Speech/snitch/names_alt" if alt else "Speech/snitch/names"
        return self._resolve_mod_content_dir(rel)

    # ── BigItems (signature item portraits) ─────────────────────────────

    def big_items_dir(self, *, for_write: bool = False) -> Path:
        return self._resolve_mod_content_dir("BigItems")


# ──────────────────────────────────────────────────────────────────────────
#  Construction
# ──────────────────────────────────────────────────────────────────────────


def detect_flavor(layout: VfsLayout) -> LayoutFlavor:
    """Probe the install to figure out which conventions it follows."""
    flavor = LayoutFlavor()

    # Per-file MercEdt: root-level vs BinaryData
    if layout.resolve_read("MercEdt") is not None:
        flavor.merc_edt_root = "root"
    elif layout.resolve_read("BinaryData/MercEdt") is not None:
        flavor.merc_edt_root = "binarydata"

    # Voice layout: subdir Speech/<idx>/ vs slot-prefix Speech/<slot>_*.ogg
    speech_root = layout.resolve_read("Speech")
    if speech_root is not None and speech_root.is_dir():
        # slot-prefix layout = direct-child files named <digits>_<...>.<ext>;
        # subdir layout = direct-child dirs named <digits>. When both appear,
        # slot-prefix wins (the modern mod convention).
        #
        # PERF: use os.scandir + early break, NOT
        # Path.iterdir() + .is_file()/.is_dir(). Two reasons:
        #   1. os.scandir's DirEntry caches the entry type from the directory
        #      read, so .is_file()/.is_dir() need no extra stat() syscall;
        #      Path.iterdir() + .is_file() stat()s every entry.
        #   2. slot-prefix is the deciding flag, so we can stop the instant we
        #      see one — there's no need to keep scanning for subdirs.
        # Without this, an install whose Speech/ holds thousands of loose
        # slot-prefixed clips stat()'d every single one: a reference install's
        # 10,918 files cost ~2.2 s — the bulk of make_install_context's
        # ~2.2 s and the single biggest reason "the roster loads slowly".
        has_slot_prefixed = False
        has_subdir = False
        try:
            with os.scandir(speech_root) as it:
                for entry in it:
                    name = entry.name
                    if not has_slot_prefixed and "_" in name and entry.is_file():
                        head = name.split("_", 1)[0]
                        if head.isdigit():
                            has_slot_prefixed = True
                            break  # slot-prefix wins — decision made, stop scanning
                    if not has_subdir and name.isdigit() and entry.is_dir():
                        has_subdir = True
        except OSError:
            pass
        # If we saw both, prefer slot_prefix (it's the modern mod convention)
        if has_slot_prefixed:
            flavor.voice_layout = "slot_prefix"
        elif has_subdir:
            flavor.voice_layout = "subdir"

    # Gear subdir: TableData/Inventory/ vs flat
    if layout.resolve_read("TableData/Inventory/MercStartingGear.xml") is not None:
        flavor.gear_subdir = "inventory"
    elif layout.resolve_read("TableData/MercStartingGear.xml") is not None:
        flavor.gear_subdir = "flat"

    # Extra tables — probe for each
    flavor.has_merc_opinions = layout.resolve_read("TableData/MercOpinions.xml") is not None
    flavor.has_merc_quote = layout.resolve_read("TableData/MercQuote.xml") is not None
    flavor.has_merc_availability = layout.resolve_read("TableData/MercAvailability.xml") is not None
    flavor.has_face_gear = layout.resolve_read("TableData/FaceGear.xml") is not None
    flavor.has_backgrounds = layout.resolve_read("TableData/Backgrounds.xml") is not None
    flavor.has_civ_group_names = layout.resolve_read("TableData/CivGroupNames.xml") is not None

    # Extra audio dirs
    flavor.has_battlesnds = layout.resolve_read("Battlesnds") is not None
    flavor.has_npc_speech = layout.resolve_read("NPC_Speech") is not None
    flavor.has_snitch_names = layout.resolve_read("Speech/snitch/names") is not None

    return flavor


def make_install_context(install_root: Path) -> InstallContext:
    """Build a complete InstallContext for the given install root.

    Catches `VfsConfigError` and falls back to a legacy single-layer layout
    with the error captured in `layout.errors`. This lets the rest of the
    install-detection scan continue when one install has a broken
    `Ja2.ini` (e.g. references a missing `vfs_config.*.ini`) — pre-fix,
    one bad install raised `VfsConfigError` all the way up through
    `refresh_installs`, killing the scan for every other install. The
    caller (`validate_install`) already reads `layout.errors` and surfaces
    them as soft warnings on the install's `errors` list.

    NOTE: this is ~50-100 ms per call on a modded install (parse_vfs_config
    + detect_flavor's TableData/*.xml + Speech/ probes). A previous
    attempt to memoize this on `(install_root, Ja2.ini mtime, vfs_config*.ini
    mtime)` was reverted because the cache key didn't track TableData/*.xml
    mtimes — bundle exports that wrote an extra-table file after a context
    was built saw a stale `flavor.has_*` flag. Instead, hot paths
    (`route_bio` / `read_bio` / `write_bio` / `clear_bio`,
    `relocator.move` / `duplicate`, `bundle.export_merc` / `deploy_import` /
    `move_between_installs`, `routes.merc` create/update/delete) build the
    context ONCE at their entry point and thread it through every downstream
    call. Bug-review C4.
    """
    install_root = Path(install_root)
    try:
        layout = parse_vfs_config(install_root)
    except VfsConfigError as e:
        layout = _legacy_layout(install_root.resolve())
        layout.errors.append(str(e))
    flavor = detect_flavor(layout)
    return InstallContext(layout=layout, flavor=flavor)
