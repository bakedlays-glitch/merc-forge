"""In-place edit ops for parsed .dat dicts.

All ops mutate the `parsed` dict returned by parse_dat_full. After
applying edits, pass the mutated dict to dat_writer.write_dat_bytes
together with the original bytes to get a new .dat with the edits
baked in (everything else byte-identical).

Layer name convention (matches parse_dat_full's dict keys):
    "land", "objs", "structs", "shadows", "roofs", "onroofs"

Layer count keys are SINGULAR (matches parsed["n_per_tile"] / ["counts"]):
    "land", "obj", "struct", "shadow", "roof", "onroof"
"""
from __future__ import annotations

from typing import Any, Dict


# Per-layer dict-key → count-dict-key mapping. Layer arrays are plural
# ("structs"), count arrays are singular ("struct") — historical artifact
# of the parser. Centralize the mapping here so callers don't need to know.
_LAYER_TO_COUNT_KEY = {
    "land":    "land",
    "objs":    "obj",
    "structs": "struct",
    "shadows": "shadow",
    "roofs":   "roof",
    "onroofs": "onroof",
}

# Engine-side cap: per-tile per-layer counts are stored in a 4-bit nibble
# (parse_dat_ext.py:331-338), so the max is 15 entries per (tile, layer).
MAX_LAYER_ENTRIES_PER_TILE = 15


class EditOpError(ValueError):
    pass


def _validate(parsed: Dict[str, Any], gridno: int, layer: str) -> None:
    if layer not in _LAYER_TO_COUNT_KEY:
        raise EditOpError(f"unknown layer {layer!r}; valid: "
                          f"{sorted(_LAYER_TO_COUNT_KEY)}")
    world_max = parsed["rows"] * parsed["cols"]
    if not 0 <= gridno < world_max:
        raise EditOpError(f"gridno {gridno} out of range 0..{world_max - 1}")


def replace_layer_entry(
    parsed: Dict[str, Any],
    gridno: int,
    layer: str,
    entry_index: int,
    new_slot: int,
    new_sub: int,
) -> tuple[int, int]:
    """Replace the entry at `entry_index` in `layer` at `gridno`.
    Returns the (old_slot, old_sub) that was overwritten. No count
    change — just swaps the tuple in place."""
    _validate(parsed, gridno, layer)
    entries = parsed[layer][gridno]
    if not 0 <= entry_index < len(entries):
        raise EditOpError(
            f"entry_index {entry_index} out of range 0..{len(entries) - 1} "
            f"for tile {gridno} layer {layer}"
        )
    old = entries[entry_index]
    entries[entry_index] = (int(new_slot) & 0xFF,
                            int(new_sub) & (0xFFFF if layer == "objs" else 0xFF))
    return old


def add_layer_entry(
    parsed: Dict[str, Any],
    gridno: int,
    layer: str,
    slot: int,
    sub: int,
) -> int:
    """Append an entry to `layer` at `gridno`. Returns its new index.
    Bumps the per-tile count nibble and the global counts dict. Raises
    EditOpError if the per-tile cap (15) would be exceeded."""
    _validate(parsed, gridno, layer)
    entries = parsed[layer][gridno]
    if len(entries) >= MAX_LAYER_ENTRIES_PER_TILE:
        raise EditOpError(
            f"tile {gridno} layer {layer} already has "
            f"{MAX_LAYER_ENTRIES_PER_TILE} entries (engine 4-bit cap)"
        )
    sub_mask = 0xFFFF if layer == "objs" else 0xFF
    entries.append((int(slot) & 0xFF, int(sub) & sub_mask))
    ck = _LAYER_TO_COUNT_KEY[layer]
    parsed["n_per_tile"][ck][gridno] = len(entries)
    parsed["counts"][ck] += 1
    return len(entries) - 1


def place_layer_entry(
    parsed: Dict[str, Any],
    gridno: int,
    layer: str,
    slot: int,
    sub: int,
) -> tuple[int, list[tuple[int, int]]]:
    """REPLACE any same-slot entry on this (gridno, layer); preserve
    different-slot entries. Returns (new_index, [removed_entries]).

    The previous semantic ("REMOVE ALL entries + APPEND") destroyed
    surface decorations on multi-entry land tiles when the user
    repainted the floor (bug #64 in MERC_FORGE_BUG_LIST.md). Verified
    by the H4-debug diff: `(57,99) land = [(0,6), (6,30)]` became
    `[(0,4)]` after a slot-0 paint, silently wiping the slot-6 decal.

    New semantic matches what the user means by "paint slot X sub Y
    here": this tile gets exactly one slot X entry (with the new
    sub), and any non-matching slot entries on the same layer keep
    going. Same-slot repaints still snap to the latest sub. Stamps
    still work because each footprint piece writes to a different
    tile and/or slot.

    Multi-entry-per-slot use cases (rare — `objs` layer with two
    identical bottles) should call `add_layer_entry` directly.
    """
    _validate(parsed, gridno, layer)
    ck = _LAYER_TO_COUNT_KEY[layer]
    slot_byte = int(slot) & 0xFF
    sub_mask = 0xFFFF if layer == "objs" else 0xFF
    sub_val = int(sub) & sub_mask
    entries = parsed[layer][gridno]
    removed: list[tuple[int, int]] = [e for e in entries if e[0] == slot_byte]
    if removed:
        # In-place rewrite preserves the ordering of non-matching
        # entries on this layer.
        parsed[layer][gridno] = [e for e in entries if e[0] != slot_byte]
        entries = parsed[layer][gridno]
        parsed["counts"][ck] -= len(removed)
    entries.append((slot_byte, sub_val))
    parsed["n_per_tile"][ck][gridno] = len(entries)
    parsed["counts"][ck] += 1
    return len(entries) - 1, removed


def remove_layer_entry(
    parsed: Dict[str, Any],
    gridno: int,
    layer: str,
    entry_index: int,
) -> tuple[int, int]:
    """Remove the entry at `entry_index`. Returns the removed (slot, sub).
    Drops the per-tile count nibble and the global count."""
    _validate(parsed, gridno, layer)
    entries = parsed[layer][gridno]
    if not 0 <= entry_index < len(entries):
        raise EditOpError(
            f"entry_index {entry_index} out of range 0..{len(entries) - 1} "
            f"for tile {gridno} layer {layer}"
        )
    removed = entries.pop(entry_index)
    ck = _LAYER_TO_COUNT_KEY[layer]
    parsed["n_per_tile"][ck][gridno] = len(entries)
    parsed["counts"][ck] -= 1
    return removed


def set_layer_entries(
    parsed: Dict[str, Any],
    gridno: int,
    layer: str,
    entries: list[tuple[int, int]],
) -> list[tuple[int, int]]:
    """Replace the entire entry list for one (tile, layer). Returns the
    previous list. Primary use: undo — the UI snapshots a tile's
    pre-edit layer state, then sends a set_layer_entries with the
    snapshot to revert.

    Validates the same caps + masks as add_layer_entry. Empty `entries`
    is allowed (clears the tile's layer)."""
    _validate(parsed, gridno, layer)
    if len(entries) > MAX_LAYER_ENTRIES_PER_TILE:
        raise EditOpError(
            f"tile {gridno} layer {layer}: cannot set {len(entries)} entries "
            f"(engine 4-bit cap is {MAX_LAYER_ENTRIES_PER_TILE})"
        )
    sub_mask = 0xFFFF if layer == "objs" else 0xFF
    cleaned: list[tuple[int, int]] = []
    for slot, sub in entries:
        cleaned.append((int(slot) & 0xFF, int(sub) & sub_mask))
    old = list(parsed[layer][gridno])
    ck = _LAYER_TO_COUNT_KEY[layer]
    parsed["counts"][ck] -= len(old)
    parsed[layer][gridno] = cleaned
    parsed["counts"][ck] += len(cleaned)
    parsed["n_per_tile"][ck][gridno] = len(cleaned)
    return old


def set_room_id(parsed: Dict[str, Any], gridno: int, room_id: int) -> int:
    """Set the room ID for one tile. Returns the previous room_id."""
    world_max = parsed["rows"] * parsed["cols"]
    if not 0 <= gridno < world_max:
        raise EditOpError(f"gridno {gridno} out of range 0..{world_max - 1}")
    if not 0 <= room_id <= 0xFFFF:
        raise EditOpError(f"room_id {room_id} out of range 0..65535")
    if parsed["room_bytes_per_tile"] == 1 and room_id > 0xFF:
        raise EditOpError(
            f"this map stores rooms as 1 byte/tile (minor<29); "
            f"room_id {room_id} > 255"
        )
    old = parsed["rooms"][gridno]
    parsed["rooms"][gridno] = room_id
    return old


def set_height(parsed: Dict[str, Any], gridno: int, height: int) -> int:
    """Set the per-tile terrain height (the low byte of the 2-byte slot).
    Returns the previous height. The high byte (`ubAdjacentSoldierCnt`,
    runtime state) is preserved separately via `heights_high`, so the writer
    re-emits the slot byte-identically except this edited low byte."""
    world_max = parsed["rows"] * parsed["cols"]
    if not 0 <= gridno < world_max:
        raise EditOpError(f"gridno {gridno} out of range 0..{world_max - 1}")
    if not 0 <= height <= 0xFF:
        raise EditOpError(f"height {height} out of range 0..255")
    heights = parsed.get("heights")
    if heights is None or gridno >= len(heights):
        raise EditOpError("this parsed map has no heights array to edit")
    old = heights[gridno]
    heights[gridno] = height
    return old
