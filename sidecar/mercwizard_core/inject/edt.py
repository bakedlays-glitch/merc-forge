"""EDT binary file I/O for merc biographies.

Engine-truth narrative for bio-file routing and the AimBioID × 1120
bug-fix is summarized inline below.

Every JA2 1.13 merc biography is stored as a 1120-byte record in one of three
locations, depending on the merc's Type:

| Type / slot range        | EDT file                          | Offset                  |
|--------------------------|-----------------------------------|-------------------------|
| AIM 0–39                 | Data-1.13/BinaryData/AIMBIOS.EDT  | uiIndex × 1120          |
| AIM 170–177, 186–187, 215+ | Data-1.13/BinaryData/AIMBIOS.EDT | AimBioID × 1120  ←★     |
| MERC 40–50               | Data-1.13/BinaryData/MERCBIOS.EDT | (uiIndex − 40) × 1120   |
| MERC 178–199, 244, 247…  | Data-1.13/BinaryData/MERCBIOS.EDT | MercBioID × 1120 ←★★    |
| NPC 51–169               | Data-1.13/BinaryData/NPCDATA/     | one record per file     |

★ THE DOCUMENTED AIM COMPILER BUG (merc_integration.md):
   Headless_Compiler/compile_merc.py line ~670 uses `uiIndex × 1120` for ALL
   AIMBIOS writes. This is correct for slots 0–39 only. For slots 170+, the
   correct offset is AimBioID × 1120 (read from AIMAvailability.xml). The
   bug causes expanded-AIM mercs to inherit a vanilla merc's bio because
   their bytes get written at the wrong offset.

★★ THE MERC ROUTING BUG (discovered 2026-05-14, Eskimo Vengeance import):
   MercWizard 1.x and earlier MercWizard 2 routed Type=2 expansion bios to
   `Data-1.13/BinaryData/MercEdt/<uiIndex>.EDT`. The engine doesn't read
   those files — it reads MERCBIOS.EDT at `MercBioID × 1120` for every
   Type=2 merc, whether vanilla (40–50) or expansion (178+). Bytes written
   to MercEdt are silently ignored and the M.E.R.C. site shows whichever
   merc the engine finds at offset `MercBioID × 1120` in MERCBIOS.EDT
   (almost always the wrong one). Symptom: Eskimo's M.E.R.C. profile read
   "Herman Regents, probation officer" — that's Turtle's bio at record 26
   of Vengeance's MERCBIOS.EDT.

   This module FIXES both bugs: route_bio() requires explicit aim_bio_id
   for AIM ranges and explicit merc_bio_id for MERC ranges. Per-file
   MercEdt/ remains routable as a fallback for installs that don't carry
   a MERCBIOS.EDT, but the canonical path is now MERCBIOS.EDT for every
   MERC bio.

Each record layout (1120 bytes total):
    Offset 0x000–0x31F (800 bytes): biography (UTF-16LE, conditional ROT+1)
    Offset 0x320–0x45F (320 bytes): additional info (same encoding)

Conditional ROT+1 encoding:
    Per character, ord(c) >= 33 → val += 1; ord(c) == 32 (space) → unchanged.
    Encoded as little-endian 16-bit (UTF-16LE).
    Naïve "shift every char" breaks because spaces become '!' and corrupt
    the bio renderer.

Reference: Headless_Compiler/compile_merc.py lines 671–690 (the original
implementation we're porting and bug-fixing).
"""
from __future__ import annotations

import os
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from ..install_context import InstallContext


# ──────────────────────────────────────────────────────────────────────────
#  Constants
# ──────────────────────────────────────────────────────────────────────────

RECORD_SIZE = 1120
BIO_FIELD_SIZE = 800     # bytes (UTF-16LE → 400 chars max)
ADDL_FIELD_SIZE = 320    # bytes (UTF-16LE → 160 chars max)
BIO_CHAR_MAX = 400
ADDL_CHAR_MAX = 160

# Engine constants
ROT_THRESHOLD = 33       # chars with ord >= 33 get +1
SPACE = 32               # ord(' ') — NEVER shifted

# Slot range boundaries (uiIndex)
VANILLA_AIM_MAX = 39
VANILLA_MERC_MIN = 40
VANILLA_MERC_MAX = 50
NPC_MAX = 169
EXPANDED_AIM_FIRST_GROUP = range(170, 178)      # 170–177
EXPANDED_AIM_SECOND_GROUP = {186, 187}
SCATTERED_AIM_SLOTS = frozenset({
    215, 223, 228,
    230, 231, 232, 233, 234, 235, 236, 237, 238, 239,
    240, 241, 242, 243,
    245, 246, 248, 250, 251,
})
EXPANDED_MERC_FIRST_GROUP = range(178, 186)      # 178–185
EXPANDED_MERC_SECOND_GROUP = range(188, 200)     # 188–199
EXPANDED_MERC_TAIL = {244, 247, 249, 252, 253}


# ──────────────────────────────────────────────────────────────────────────
#  Encoding / decoding
# ──────────────────────────────────────────────────────────────────────────

def encode_field(text: str, byte_size: int, char_max: int) -> bytes:
    """Encode `text` as UTF-16LE with conditional ROT+1 into a `byte_size`
    fixed-width buffer (zero-padded if shorter, truncated at `char_max`).

    Per JA2's conditional ROT+1 convention:
      ord(c) >= 33 → val = ord(c) + 1
      ord(c) == 32 (space) → val = 32 (UNCHANGED)
      ord(c) < 32 → val = ord(c)  (control codes left alone)

    The result is a 16-bit little-endian sequence. Output buffer is exactly
    `byte_size` bytes, zero-padded after the encoded text.

    Supplementary-plane characters (anything that would encode above
    0xFFFE post-ROT) are clamped to 0xFFFE — the engine's UTF-16 reader
    has no surrogate-pair support, so we can't preserve them. The audit
    layer surfaces a CONTAINS_UNENCODABLE warning when the bio carries
    such characters; this clamp is the last-resort safety net.
    """
    if char_max < 0:
        raise ValueError(f"char_max must be non-negative, got {char_max}")
    if byte_size != char_max * 2:
        raise ValueError(
            f"Inconsistent sizes: byte_size={byte_size} should be 2 × char_max ({char_max * 2})"
        )

    buf = bytearray(byte_size)
    truncated = text[:char_max]
    for i, char in enumerate(truncated):
        val = ord(char)
        if val >= ROT_THRESHOLD:
            val += 1
        if val > 0xFFFE:
            # Defensive clamp; emoji + supplementary-plane chars land here.
            # See find_unencodable_chars() for the audit-side preview.
            val = 0xFFFE
        buf[i * 2] = val & 0xFF
        buf[i * 2 + 1] = (val >> 8) & 0xFF
    return bytes(buf)


def find_unencodable_chars(text: str) -> list[tuple[int, str]]:
    """Return (index, char) pairs for characters `encode_field` would clamp.

    A character is unencodable if its codepoint (post-ROT) would exceed
    0xFFFE — i.e. anything in the supplementary plane (emoji, rare CJK,
    musical symbols, etc.). The engine reads bio strings via a 16-bit
    UTF-16 reader with no surrogate-pair handling, so these characters
    would be rendered as `□` (the U+FFFE sentinel glyph) in-game.

    Used by the audit layer to warn the user BEFORE they save, so they
    can decide whether to remove the offending characters or accept the
    mangled in-game display.
    """
    bad: list[tuple[int, str]] = []
    for i, char in enumerate(text):
        val = ord(char)
        if val >= ROT_THRESHOLD:
            val += 1
        if val > 0xFFFE:
            bad.append((i, char))
    return bad


def decode_field(data: bytes) -> str:
    """Reverse of encode_field. Stops at the first null word."""
    chars: list[str] = []
    for i in range(0, len(data), 2):
        if i + 1 >= len(data):
            break
        val = data[i] | (data[i + 1] << 8)
        if val == 0:
            break
        if val >= ROT_THRESHOLD + 1:
            val -= 1
        chars.append(chr(val))
    return "".join(chars)


def encode_record(biography: str, additional: str) -> bytes:
    """Pack (biography, additional) into a single 1120-byte EDT record."""
    bio_bytes = encode_field(biography, BIO_FIELD_SIZE, BIO_CHAR_MAX)
    addl_bytes = encode_field(additional, ADDL_FIELD_SIZE, ADDL_CHAR_MAX)
    record = bio_bytes + addl_bytes
    assert len(record) == RECORD_SIZE, (
        f"Record size mismatch: expected {RECORD_SIZE}, got {len(record)}"
    )
    return record


def decode_record(data: bytes) -> tuple[str, str]:
    """Unpack a 1120-byte EDT record into (biography, additional)."""
    if len(data) < RECORD_SIZE:
        return ("", "")
    return (
        decode_field(data[:BIO_FIELD_SIZE]),
        decode_field(data[BIO_FIELD_SIZE:RECORD_SIZE]),
    )


# ──────────────────────────────────────────────────────────────────────────
#  Routing — the three-way EDT path resolver
# ──────────────────────────────────────────────────────────────────────────

class EDTRoute:
    """Where to write a merc's bio: file path + record index within that file.

    For per-file EDTs (NPC / expanded MERC), record_index is always 0 because
    each file holds exactly one record.
    """
    __slots__ = ("path", "record_index", "kind")

    def __init__(self, path: Path, record_index: int, kind: str) -> None:
        self.path = path
        self.record_index = record_index
        self.kind = kind  # "aimbios" | "mercbios" | "per_file_merc" | "per_file_npc"

    @property
    def offset(self) -> int:
        return self.record_index * RECORD_SIZE

    def __repr__(self) -> str:
        return (
            f"EDTRoute(kind={self.kind!r}, path={self.path!s}, "
            f"record_index={self.record_index}, offset=0x{self.offset:X})"
        )


def route_bio(
    install_root: Path,
    ui_index: int,
    aim_bio_id: Optional[int] = None,
    *,
    merc_bio_id: Optional[int] = None,
    for_write: bool = False,
    ctx: Optional["InstallContext"] = None,
) -> EDTRoute:
    """Compute the canonical EDT route for a merc.

    Args:
        install_root: Path to the JA2 install root (the folder containing
            Data-1.13/ or the mod's content directory).
        ui_index: The merc's MercProfiles.xml uiIndex.
        aim_bio_id: REQUIRED for any AIM-bound slot ≥ 170. Read from
            AIMAvailability.xml's <AimBioID> field. Omit (or pass None) for
            slots 0–39 (vanilla AIM uses uiIndex directly) and for
            MERC/NPC/Vehicle slots.
        merc_bio_id: REQUIRED for any MERC expansion slot (178–199, 244,
            247, 249, 252–253). Read from MercAvailability.xml's
            <MercBioID> field. Omit for vanilla MERC 40–50 (canonical
            `MercBioID = uiIndex − 40` is applied automatically).
        for_write: If True, route to the writable layer's path (where new
            edits should land) rather than the topmost existing layer.
            See `mercwizard_core.install_context` for the resolution rules.
        ctx: Optional pre-built `InstallContext` for `install_root`. When
            supplied, route_bio reuses it instead of rebuilding (saving the
            ~50-100 ms VFS parse + flavor probe). Callers in a hot path —
            relocator move/duplicate, bundle import/export — build ctx
            once at entry and thread it through every read_bio / write_bio
            / clear_bio call. Defaults to lazy build so call sites that
            only have `install_root` keep working unchanged.

    Raises:
        ValueError: If aim_bio_id is missing for an expanded-AIM slot, if
            merc_bio_id is missing for an expanded-MERC slot, or if
            ui_index is out of [0, 255].

    Returns:
        EDTRoute pointing at the correct file + offset. The file path is
        resolved through the install's VFS chain, so modded layouts
        (Vengeance, AIMNAS, etc.) route to the mod's content layer
        rather than the empty vanilla Data-1.13/ copy.
    """
    if not 0 <= ui_index <= 255:
        raise ValueError(f"uiIndex must be in [0, 255], got {ui_index}")

    if ctx is None:
        # Lazy import to avoid a cycle (install_context doesn't import inject/).
        from ..install_context import make_install_context
        ctx = make_install_context(install_root)

    # Vanilla AIM (0–39): default is AIMBIOS at uiIndex × 1120, but a
    # Type=2 merc placed at one of these slots routes to MERCBIOS — the
    # caller signals this by supplying merc_bio_id while leaving
    # aim_bio_id=None (mirrors the expansion-AIM convention below).
    # Without this branch, route_bio would write the M.E.R.C. merc's bio
    # to AIMBIOS at slot N × 1120, silently corrupting the vanilla AIM
    # bio (Reaper at slot 5, Magic at slot 16, etc.) while the engine
    # reads MERCBIOS for the laptop row and finds no content.
    if 0 <= ui_index <= VANILLA_AIM_MAX:
        if aim_bio_id is None and merc_bio_id is not None:
            if not 0 <= merc_bio_id <= 199:
                raise ValueError(f"merc_bio_id must be in [0, 199], got {merc_bio_id}")
            return EDTRoute(
                ctx.merc_bios_edt_path(for_write=for_write),
                merc_bio_id,
                "mercbios",
            )
        return EDTRoute(ctx.aim_bios_edt_path(for_write=for_write), ui_index, "aimbios")

    # Vanilla MERC (40–50): default is MERCBIOS at (uiIndex − 40) × 1120,
    # but a Type=1 merc placed at one of these slots routes to AIMBIOS —
    # the caller signals this by supplying aim_bio_id while leaving
    # merc_bio_id=None (mirrors the expansion-MERC convention below).
    # Without this branch, route_bio would write the AIM merc's bio to
    # MERCBIOS at (slot N − 40) × 1120, silently corrupting Biff /
    # Haywire / Tony / etc. while the engine reads AIMBIOS for the AIM
    # laptop row and finds no content.
    if VANILLA_MERC_MIN <= ui_index <= VANILLA_MERC_MAX:
        if merc_bio_id is None and aim_bio_id is not None:
            if not 0 <= aim_bio_id <= 199:
                raise ValueError(f"aim_bio_id must be in [0, 199], got {aim_bio_id}")
            return EDTRoute(
                ctx.aim_bios_edt_path(for_write=for_write),
                aim_bio_id,
                "aimbios",
            )
        # Canonical MercBioID for vanilla MERC is uiIndex − 40, unless
        # the caller explicitly overrode it via merc_bio_id.
        record = merc_bio_id if merc_bio_id is not None else (ui_index - VANILLA_MERC_MIN)
        if not 0 <= record <= 199:
            raise ValueError(f"merc_bio_id must be in [0, 199], got {record}")
        return EDTRoute(
            ctx.merc_bios_edt_path(for_write=for_write),
            record,
            "mercbios",
        )

    # NPC range (51–169)
    if ui_index <= NPC_MAX:
        return EDTRoute(ctx.per_file_npc_edt_path(ui_index, for_write=for_write), 0, "per_file_npc")

    # Expanded AIM groups — REQUIRE aim_bio_id (the bug fix)
    is_expanded_aim = (
        ui_index in EXPANDED_AIM_FIRST_GROUP
        or ui_index in EXPANDED_AIM_SECOND_GROUP
        or ui_index in SCATTERED_AIM_SLOTS
    )
    if is_expanded_aim:
        # Engine-faithful: Type drives bio routing, not slot. A Type=2 merc
        # placed at an expansion-AIM slot routes to MERCBIOS — only the
        # laptop site cares about the slot range, the EDT file is chosen by
        # which laptop site the merc belongs to.
        if aim_bio_id is None and merc_bio_id is not None:
            if not 0 <= merc_bio_id <= 199:
                raise ValueError(f"merc_bio_id must be in [0, 199], got {merc_bio_id}")
            return EDTRoute(ctx.merc_bios_edt_path(for_write=for_write), merc_bio_id, "mercbios")
        if aim_bio_id is None:
            # For reads, degrade gracefully to the legacy per-file path so
            # callers (relocator.move_within_install / duplicate, bundle
            # export, etc.) can still surface whatever bio bytes are on
            # disk for installs that haven't wired the AIM/MERC bindings.
            # Without this, Move/Duplicate of any expansion-AIM slot on a
            # minimal install raised ValueError from read_bio that
            # propagated past relocator.py:128 / :279 (no try/except).
            # Bug-review finding C3. Writes still raise — silent
            # mis-routing on writes is the original bug we're fixing.
            if not for_write:
                return EDTRoute(
                    ctx.per_file_npc_edt_path(ui_index, for_write=for_write),
                    0,
                    "per_file_npc",
                )
            raise ValueError(
                f"uiIndex {ui_index} is an expanded-AIM slot; you must pass "
                "aim_bio_id (read it from AIMAvailability.xml). Using "
                "uiIndex × 1120 here is the compile_merc.py bug we're "
                "fixing — bios would be written at the wrong offset and "
                "expanded-AIM mercs would inherit a vanilla merc's text."
            )
        if not 0 <= aim_bio_id <= 199:
            raise ValueError(f"aim_bio_id must be in [0, 199], got {aim_bio_id}")
        return EDTRoute(ctx.aim_bios_edt_path(for_write=for_write), aim_bio_id, "aimbios")

    # Expanded MERC groups: MERCBIOS.EDT at MercBioID × 1120 (the second
    # routing-bug fix). Per-file MercEdt/<n>.EDT is kept available as a
    # fallback for installs that have those files but no MERCBIOS row;
    # see _route_merc_expansion for the resolution policy.
    is_expanded_merc = (
        ui_index in EXPANDED_MERC_FIRST_GROUP
        or ui_index in EXPANDED_MERC_SECOND_GROUP
        or ui_index in EXPANDED_MERC_TAIL
    )
    if is_expanded_merc:
        # Mirror of the expanded-AIM case: a Type=1 merc placed at an
        # expansion-MERC slot routes to AIMBIOS.
        if merc_bio_id is None and aim_bio_id is not None:
            if not 0 <= aim_bio_id <= 199:
                raise ValueError(f"aim_bio_id must be in [0, 199], got {aim_bio_id}")
            return EDTRoute(ctx.aim_bios_edt_path(for_write=for_write), aim_bio_id, "aimbios")
        return _route_merc_expansion(ctx, ui_index, merc_bio_id, for_write=for_write)

    # Unassigned slot in the 200–254 range. Vanilla 1.13 puts no mercs here;
    # mods like Vengeance Reloaded put Type=1 (AIM) or Type=2 (MERC) mercs at
    # these slots via their own AIMAvailability.xml / MercAvailability.xml
    # rows.
    #
    # Routing priority:
    #   1. If aim_bio_id is supplied → AIMBIOS.EDT (Type=1 case). This
    #      handles Vengeance's slot 203 etc. which carries a real AIM row.
    #      The earlier fall-through to NPCDATA silently wrote AIM bios to
    #      a file the engine never reads for the AIM site — bug fix
    #      2026-05-15.
    #   2. If merc_bio_id is supplied → MERCBIOS.EDT (Type=2 case).
    #   3. Else probe disk: per-file MercEdt/ if present, NPCDATA otherwise.
    #      Read-side fallback for installs whose XML doesn't bind the slot
    #      to either site.
    if aim_bio_id is not None:
        if not 0 <= aim_bio_id <= 199:
            raise ValueError(f"aim_bio_id must be in [0, 199], got {aim_bio_id}")
        return EDTRoute(ctx.aim_bios_edt_path(for_write=for_write), aim_bio_id, "aimbios")
    if merc_bio_id is not None:
        return _route_merc_expansion(ctx, ui_index, merc_bio_id, for_write=for_write)
    # No bio_ids supplied AND in the 200-254 range. Probe disk for a
    # legacy per-file `MercEdt/<n>.EDT` — useful for READING bios that a
    # pre-fix MercWizard 1.x left behind. For WRITES, never land in
    # MercEdt: CLAUDE.md ("MercWizard 2 fixes BOTH symmetrically") +
    # mercwizard_core/inject/edt.py:_route_merc_expansion both call
    # `MercEdt/<n>.EDT` dead routing — the engine never reads those
    # files for Type=1 or Type=2 bios. Bug-review finding E3 — the
    # earlier code path landed write_bio on this dead route whenever a
    # legacy install retained the file. Reads can still surface the
    # legacy bytes for back-compat. Writes fall through to per-file NPC
    # so the data lands somewhere readable for diagnostics rather than
    # silently into a file the engine ignores.
    if not for_write:
        merc_edt_candidate = ctx.per_file_merc_edt_path(ui_index)
        if merc_edt_candidate.is_file():
            return EDTRoute(
                ctx.per_file_merc_edt_path(ui_index, for_write=for_write),
                0,
                "per_file_merc",
            )
    return EDTRoute(ctx.per_file_npc_edt_path(ui_index, for_write=for_write), 0, "per_file_npc")


def _route_merc_expansion(
    ctx,
    ui_index: int,
    merc_bio_id: Optional[int],
    *,
    for_write: bool,
) -> EDTRoute:
    """Resolve the EDT route for an expansion MERC slot (178+, 244, etc.).

    Canonical path: MERCBIOS.EDT at `merc_bio_id × 1120` (the routing-bug
    fix). Falls back to `MercEdt/<ui_index>.EDT` only when no merc_bio_id
    is available AND a per-file EDT already exists on disk — that's the
    pre-fix layout, kept readable for back-compat reads (export) so we
    can ingest bios from installs that were authored against the old
    routing.
    """
    if merc_bio_id is not None:
        if not 0 <= merc_bio_id <= 199:
            raise ValueError(f"merc_bio_id must be in [0, 199], got {merc_bio_id}")
        return EDTRoute(
            ctx.merc_bios_edt_path(for_write=for_write),
            merc_bio_id,
            "mercbios",
        )
    # No merc_bio_id — caller didn't supply one. For writes this is a bug
    # the caller has to fix (compute one via merc_availability.compute);
    # for reads we degrade gracefully to the legacy per-file path so we
    # can still surface whatever bio bytes are on disk.
    if for_write:
        raise ValueError(
            f"uiIndex {ui_index} is an expansion-MERC slot; you must pass "
            "merc_bio_id (read it from MercAvailability.xml, or allocate "
            "one via merc_availability.compute_merc_bio_id). Writing to "
            "MercEdt/<n>.EDT instead is the routing bug we're fixing — "
            "the engine reads MERCBIOS.EDT for every MERC bio."
        )
    return EDTRoute(
        ctx.per_file_merc_edt_path(ui_index, for_write=for_write),
        0,
        "per_file_merc",
    )


# ──────────────────────────────────────────────────────────────────────────
#  EDT file class — read/write/clear records
# ──────────────────────────────────────────────────────────────────────────

class EDT:
    """A wrapper for one EDT file (AIMBIOS, MERCBIOS, or per-file)."""

    def __init__(self, path: Path) -> None:
        self.path = path

    def ensure_directory(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def ensure_size(self, min_size: int) -> None:
        """Pad the file with zeros up to at least `min_size` bytes.

        Creates the file if it doesn't exist. Used before writing a record at
        an offset that may be beyond the current end-of-file.
        """
        self.ensure_directory()
        if not self.path.exists():
            self.path.write_bytes(b"\x00" * min_size)
            return
        current = self.path.stat().st_size
        if current < min_size:
            with open(self.path, "ab") as f:
                f.write(b"\x00" * (min_size - current))

    def read_record(self, record_index: int) -> tuple[str, str]:
        """Read one record. Returns ('', '') if file too short or missing."""
        if not self.path.exists():
            return ("", "")
        offset = record_index * RECORD_SIZE
        with open(self.path, "rb") as f:
            f.seek(offset)
            data = f.read(RECORD_SIZE)
        return decode_record(data)

    def _atomic_write_bytes(self, data: bytes) -> None:
        """Write `data` to `self.path` atomically: temp file in same dir,
        fsync, then `os.replace()`. Guarantees the file is either fully
        pre-write or fully post-write on disk — never a partial overwrite
        even if the process dies mid-call.
        """
        self.ensure_directory()
        fd, tmp = tempfile.mkstemp(
            dir=str(self.path.parent),
            prefix=self.path.name + ".",
            suffix=".tmp",
        )
        try:
            with os.fdopen(fd, "wb") as tf:
                tf.write(data)
                tf.flush()
                os.fsync(tf.fileno())
            os.replace(tmp, self.path)
        except BaseException:
            # Clean up the temp file on any failure (including KeyboardInterrupt)
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise

    def write_record(self, record_index: int, biography: str, additional: str) -> None:
        """Pack and write one record at the given index.

        Atomic: reads the existing file (so other records in shared EDTs like
        AIMBIOS.EDT are preserved), splices the new record at the offset,
        and replaces the file via temp+rename. A crash mid-write leaves
        either the pre-edit or post-edit file — never a partial record.
        """
        record = encode_record(biography, additional)
        offset = record_index * RECORD_SIZE

        if self.path.exists():
            with open(self.path, "rb") as f:
                data = bytearray(f.read())
        else:
            data = bytearray()
        target_size = max(len(data), offset + RECORD_SIZE)
        if len(data) < target_size:
            data.extend(b"\x00" * (target_size - len(data)))
        data[offset : offset + RECORD_SIZE] = record

        self._atomic_write_bytes(bytes(data))

    def clear_record(self, record_index: int) -> None:
        """Zero out one record (1120 bytes) at the given index.

        Atomic via the same temp+rename path as write_record.
        """
        if not self.path.exists():
            return  # nothing to clear
        offset = record_index * RECORD_SIZE
        if self.path.stat().st_size < offset + RECORD_SIZE:
            return  # record never existed

        with open(self.path, "rb") as f:
            data = bytearray(f.read())
        data[offset : offset + RECORD_SIZE] = b"\x00" * RECORD_SIZE

        self._atomic_write_bytes(bytes(data))


# ──────────────────────────────────────────────────────────────────────────
#  High-level write API
# ──────────────────────────────────────────────────────────────────────────

def write_bio(
    install_root: Path,
    ui_index: int,
    biography: str,
    additional: str,
    aim_bio_id: Optional[int] = None,
    merc_bio_id: Optional[int] = None,
    *,
    ctx: Optional["InstallContext"] = None,
) -> EDTRoute:
    """Write a merc's biography to the correct EDT file + offset.

    See route_bio() for the routing logic. `aim_bio_id` is required for any
    AIM slot ≥ 170. `merc_bio_id` is required for any expansion MERC slot
    (178+, 244, 247, 249, 252-253). For vanilla MERC 40-50 you can omit
    `merc_bio_id` — the canonical `MercBioID = uiIndex − 40` is applied.

    Returns the EDTRoute that was written, so callers can report it in audit
    output / backup manifests.

    `ctx` is an optional pre-built `InstallContext`; see `route_bio` for the
    perf rationale.
    """
    route = route_bio(
        install_root, ui_index, aim_bio_id,
        merc_bio_id=merc_bio_id, for_write=True, ctx=ctx,
    )
    edt = EDT(route.path)
    edt.write_record(route.record_index, biography, additional)
    return route


def read_bio(
    install_root: Path,
    ui_index: int,
    aim_bio_id: Optional[int] = None,
    merc_bio_id: Optional[int] = None,
    *,
    ctx: Optional["InstallContext"] = None,
) -> tuple[str, str]:
    """Read a merc's biography from the correct EDT file + offset."""
    route = route_bio(
        install_root, ui_index, aim_bio_id,
        merc_bio_id=merc_bio_id, for_write=False, ctx=ctx,
    )
    edt = EDT(route.path)
    return edt.read_record(route.record_index)


def clear_bio(
    install_root: Path,
    ui_index: int,
    aim_bio_id: Optional[int] = None,
    merc_bio_id: Optional[int] = None,
    *,
    ctx: Optional["InstallContext"] = None,
) -> EDTRoute:
    """Zero out a merc's biography (1120 bytes) at the correct offset."""
    route = route_bio(
        install_root, ui_index, aim_bio_id,
        merc_bio_id=merc_bio_id, for_write=True, ctx=ctx,
    )
    edt = EDT(route.path)
    edt.clear_record(route.record_index)
    return route
