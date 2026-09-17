"""Normalize biography text for the JA2 EDT font.

JA2 stores biographies in UTF-16LE records, but the in-game biography font
only renders codepoints through Latin-1.  Keep author-facing text unchanged
everywhere else and normalize only at the EDT encode boundary.
"""
from __future__ import annotations

import unicodedata


TYPOGRAPHY_MAP: dict[str, str] = {
    "—": "-", "–": "-", "‒": "-", "―": "-",
    "‘": "'", "’": "'", "‚": ",", "‛": "'",
    "“": '"', "”": '"', "„": '"', "‟": '"',
    "…": "...", "•": "*", " ": " ", " ": " ",
    " ": " ", " ": " ", " ": " ", " ": " ",
    "​": "", "﻿": "", "­": "",
    "‰": "%", "′": "'", "″": '"', "⁄": "/",
    "€": "EUR", "™": "(TM)", "℗": "(P)",
    "←": "<-", "→": "->", "≤": "<=", "≥": ">=",
}


def edt_safe(text: str | None) -> str | None:
    """Return text whose codepoints are all renderable by the EDT font."""
    if not text:
        return text

    output: list[str] = []
    for char in text:
        if ord(char) <= 255:
            output.append(char)
            continue
        if char in TYPOGRAPHY_MAP:
            output.append(TYPOGRAPHY_MAP[char])
            continue

        folded = "".join(
            candidate
            for candidate in unicodedata.normalize("NFKD", char)
            if not unicodedata.combining(candidate)
        )
        output.append(
            folded
            if folded and all(ord(candidate) <= 255 for candidate in folded)
            else "?"
        )
    return "".join(output)


def unrenderable(text: str | None) -> list[str]:
    """Return the distinct codepoints the EDT font cannot render."""
    return sorted({char for char in (text or "") if ord(char) > 255})
