"""Codec for the 146 fixed-size records in a merc dialogue EDT file."""
from __future__ import annotations

from dataclasses import dataclass


RECORD_SIZE = 480
RECORD_COUNT = 146
FILE_SIZE = RECORD_SIZE * RECORD_COUNT
MAX_TEXT_CHARS = (RECORD_SIZE // 2) - 1


def decode_record(record: bytes) -> str:
    if len(record) != RECORD_SIZE:
        raise ValueError(f"dialogue record must be {RECORD_SIZE} bytes")
    chars: list[str] = []
    for offset in range(0, RECORD_SIZE, 2):
        value = int.from_bytes(record[offset:offset + 2], "little")
        if value == 0:
            break
        chars.append(chr(value - 1 if value >= 34 else value))
    return "".join(chars)


def encode_record(text: str) -> bytes:
    if len(text) > MAX_TEXT_CHARS:
        raise ValueError(f"dialogue text exceeds {MAX_TEXT_CHARS} characters")
    output = bytearray(RECORD_SIZE)
    for index, character in enumerate(text):
        value = ord(character)
        if value >= 33:
            value += 1
        output[index * 2:index * 2 + 2] = value.to_bytes(2, "little")
    return bytes(output)


@dataclass(frozen=True)
class DialogueDocument:
    """An in-memory merc dialogue EDT document with fixed record boundaries."""

    _data: bytes

    @classmethod
    def from_bytes(cls, data: bytes) -> "DialogueDocument":
        if len(data) != FILE_SIZE:
            raise ValueError(f"dialogue document must be {FILE_SIZE} bytes")
        return cls(bytes(data))

    def text(self, slot: int) -> str:
        return decode_record(self.record_bytes(slot))

    def record_bytes(self, slot: int) -> bytes:
        self._validate_slot(slot)
        start = slot * RECORD_SIZE
        return self._data[start:start + RECORD_SIZE]

    def replace_text(self, slot: int, text: str) -> bytes:
        return self.replace_record_bytes(slot, encode_record(text))

    def replace_record_bytes(self, slot: int, record: bytes) -> bytes:
        self._validate_slot(slot)
        if len(record) != RECORD_SIZE:
            raise ValueError(f"dialogue record must be {RECORD_SIZE} bytes")
        start = slot * RECORD_SIZE
        return self._data[:start] + bytes(record) + self._data[start + RECORD_SIZE:]

    @staticmethod
    def _validate_slot(slot: int) -> None:
        if not 0 <= slot < RECORD_COUNT:
            raise ValueError(f"dialogue slot must be between 0 and {RECORD_COUNT - 1}")
