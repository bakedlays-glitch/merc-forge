"""Strict reader for JA2 JSD structure identities.

The database structure footprint is collision/occupancy metadata. Every member
belongs to the same one-based STI anchor sub; it must never be converted into
"the next N frames".
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
from pathlib import Path
import struct


_HEADER_SIZE = 16
_AUX_SIZE = 16
_STRUCTURE_SIZE = 16
_STRUCTURE_TILE_SIZE = 32
_HAS_AUX_DATA = 0x01
_HAS_STRUCTURE_DATA = 0x02


class JsdFormatError(ValueError):
    """The bytes cannot safely describe JA2 structure identities."""


@dataclass(frozen=True)
class StructureIdentity:
    jsd_sha256: str
    anchor_sub: int
    members: tuple[tuple[int, int, int], ...]

    @property
    def is_multi_tile(self) -> bool:
        return len(self.members) > 1


def parse_structure_identities(data: bytes) -> dict[int, StructureIdentity]:
    if len(data) < _HEADER_SIZE or data[:4] != b"J2SD":
        raise JsdFormatError("missing J2SD header")
    n_subimages, n_stored, structure_data_size = struct.unpack_from("<HHH", data, 4)
    file_flags = struct.unpack_from("<H", data, 10)[0]
    n_tile_locations = struct.unpack_from("<H", data, 14)[0]
    if not (file_flags & _HAS_STRUCTURE_DATA):
        return {}

    offset = _HEADER_SIZE
    if file_flags & _HAS_AUX_DATA:
        offset += n_subimages * _AUX_SIZE
    offset += n_tile_locations * 2
    if offset > len(data):
        raise JsdFormatError("truncated JSD metadata")
    if structure_data_size and offset + structure_data_size > len(data):
        raise JsdFormatError("truncated JSD structure block")

    block_end = offset + structure_data_size if structure_data_size else len(data)
    digest = hashlib.sha256(data).hexdigest()
    identities: dict[int, StructureIdentity] = {}
    for _ in range(n_stored):
        if offset + _STRUCTURE_SIZE > block_end:
            raise JsdFormatError("truncated DB_STRUCTURE")
        n_tiles = data[offset + 3]
        structure_number = struct.unpack_from("<H", data, offset + 8)[0]
        if structure_number >= n_subimages:
            raise JsdFormatError("DB_STRUCTURE sub index is outside the STI range")
        if n_tiles < 1:
            raise JsdFormatError("DB_STRUCTURE has no footprint tiles")
        tiles_offset = offset + _STRUCTURE_SIZE
        next_offset = tiles_offset + n_tiles * _STRUCTURE_TILE_SIZE
        if next_offset > block_end:
            raise JsdFormatError("truncated DB_STRUCTURE_TILE array")

        anchor_sub = structure_number + 1
        members = []
        for tile_index in range(n_tiles):
            tile_offset = tiles_offset + tile_index * _STRUCTURE_TILE_SIZE
            _relative_pos, x, y = struct.unpack_from("<hbb", data, tile_offset)
            members.append((x, y, anchor_sub))
        if anchor_sub in identities:
            raise JsdFormatError("duplicate DB_STRUCTURE sub index")
        identities[anchor_sub] = StructureIdentity(
            jsd_sha256=digest,
            anchor_sub=anchor_sub,
            members=tuple(members),
        )
        offset = next_offset

    if structure_data_size and offset != block_end:
        raise JsdFormatError("JSD structure block size does not match its records")
    return identities


def load_structure_identity(path: Path, sub: int) -> StructureIdentity | None:
    try:
        data = Path(path).read_bytes()
    except OSError:
        return None
    return parse_structure_identities(data).get(int(sub))

