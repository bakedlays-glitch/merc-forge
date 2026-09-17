import pytest

from mercwizard_core.voice_lab.dialogue_edt import (
    FILE_SIZE,
    MAX_TEXT_CHARS,
    RECORD_SIZE,
    DialogueDocument,
    encode_record,
)


def test_dialogue_edit_changes_one_record_only() -> None:
    """Replacing a line must preserve every surrounding fixed-size record."""
    original = bytes((index % 251 for index in range(FILE_SIZE)))
    document = DialogueDocument.from_bytes(original)

    changed = document.replace_text(111, "An offer, huh? All right, let's hear it.")

    assert changed[:111 * RECORD_SIZE] == original[:111 * RECORD_SIZE]
    assert changed[112 * RECORD_SIZE:] == original[112 * RECORD_SIZE:]
    assert DialogueDocument.from_bytes(changed).text(111).startswith("An offer")


def test_dialogue_codec_rejects_biography_record() -> None:
    """A 70,080-byte merc-dialogue document must not accept 1,120-byte bio data."""
    with pytest.raises(ValueError, match="70080"):
        DialogueDocument.from_bytes(bytes(1120))


def test_dialogue_codec_preserves_preencoded_record_bytes() -> None:
    """Replacing raw record 7 must retain its encoded content exactly."""
    original = bytes(FILE_SIZE)
    record = encode_record("No punctuation shift is lost!")

    changed = DialogueDocument.from_bytes(original).replace_record_bytes(7, record)

    assert changed[7 * RECORD_SIZE:8 * RECORD_SIZE] == record
    assert DialogueDocument.from_bytes(changed).text(7) == "No punctuation shift is lost!"


def test_dialogue_codec_rejects_text_that_would_overflow_a_record() -> None:
    """Writing 240 characters would overwrite the next dialogue record."""
    document = DialogueDocument.from_bytes(bytes(FILE_SIZE))

    with pytest.raises(ValueError, match=str(MAX_TEXT_CHARS)):
        document.replace_text(0, "x" * (MAX_TEXT_CHARS + 1))
