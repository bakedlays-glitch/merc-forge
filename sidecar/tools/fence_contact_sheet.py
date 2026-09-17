"""Fence contact-sheet tool (tileset 72, GECKO).

Renders every sub-frame of each fence-category slot (from sitekit's
t72_categories.json) onto one labelled PNG so the six placement roles
(ns/ew run, nw/ne/sw/se corners) can be picked by EYE, never by filename
(memory reference_fence_subs_sti_specific).

Art source: sitekit's baked atlas (zero-HTTP; no sidecar/server needed)
  Headless_Compiler/sitekit/data/t72_atlas.png   — the packed sprite sheet
  Headless_Compiler/sitekit/data/t72_cells_xy.json — "slot.sub" -> {x,y,w,h}
    atlas-pixel rects (read-only; sitekit owns this dir).
STI filenames for the row headers come from the running sidecar's palette
endpoint (http://127.0.0.1:8773/api/v1/mapforge/tileset/palette) if
reachable, else fall back to the FENCE_SLOTS table below.

Usage:
    <sidecar venv python> fence_contact_sheet.py
Writes fences_t72_sheet_v101.png always, and
fences_t72_sheet_proposed_v101.png (roles stamped) once PROPOSED below is
filled in by eye from the first sheet.
"""
from __future__ import annotations

import os
import tempfile
import json
import textwrap
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

SITEKIT_DATA = Path(os.environ.get("MERCWIZARD_HEADLESS_COMPILER", "")) / "sitekit" / "data"
ATLAS_PNG = SITEKIT_DATA / "t72_atlas.png"
CELLS_XY = SITEKIT_DATA / "t72_cells_xy.json"

OUT_DIR = Path(os.environ.get("MW2_CONTACT_SHEET_OUT") or tempfile.gettempdir())

# Tileset 72 fence-category slots, per Headless_Compiler/sitekit/data/t72_categories.json
# ("fence": 15, 77, 86, 89, 104). Filenames confirmed against the live sidecar
# palette endpoint; kept here as a static fallback so this tool has
# no hard runtime dependency on the sidecar being up.
FENCE_SLOTS: dict[int, str] = {
    15: "border.sti",
    77: "junkfen.sti",
    86: "wirefenc.sti",
    89: "gecko_chainlink.sti",
    104: "oldfence.sti",
}

# Filled in BY EYE from fences_t72_sheet_v101.png. sitekit place.fence corner
# convention: neighbours {E,S}->nw, {S,W}->ne, {E,N}->sw, {N,W}->se.
# "corners": false means the family has no distinct corner art — nw/ne/sw/se
# all just repeat the run piece, and the generator stamps corners=false in
# the data file for that family.
PROPOSED: dict[int, dict] = {
    # border.sti — HIGH UNCERTAINTY: this STI mixes at least two unrelated
    # visual styles (a thin low rail, subs 4/8/9/10; and a tall solid
    # reed/wattle wall, subs 5/6/7) plus posts (1,2) and a sparse
    # signpost/gate-arm (3). ns/ew below are the cleanest same-style
    # matched pair (thin rail); no corner sprite found at all.
    15: {"ns": 9, "ew": 4, "nw": 9, "ne": 9, "sw": 9, "se": 9, "corners": False},
    # junkfen.sti — tall junk-stockade panels. Clean undecorated NS/EW pair;
    # everything else is a sign/junk-decor/damage variant of one of the two
    # (see NOTES). No two-wing corner sprite found (junk fence just butts).
    77: {"ns": 2, "ew": 1, "nw": 2, "ne": 2, "sw": 2, "se": 2, "corners": False},
    # wirefenc.sti — chain-link. sub 2 is a genuine two-wing post (mesh
    # radiating both ways from a shared post, near-zero roofline slope) —
    # ONE shared corner sprite reused for all 4 rotations (a mesh corner
    # reads the same from any of the 4 angles at this fidelity).
    86: {"ns": 7, "ew": 6, "nw": 2, "ne": 2, "sw": 2, "se": 2, "corners": True},
    # gecko_chainlink.sti — rusted board+chainlink, same post-corner
    # convention as wirefenc (sub 17 = two-wing post, shared for all 4).
    89: {"ns": 1, "ew": 4, "nw": 17, "ne": 17, "sw": 17, "se": 17, "corners": True},
    # oldfence.sti — wood rail. 10/11/12 (NS) mirror 13/14/15 (EW) almost
    # exactly (roofline slope magnitudes match to 2 decimal places) — a
    # clean authored pair. Corner pick (sub 5, a 3-post branch cluster) is
    # LOW CONFIDENCE — no clearly two-wing post like 86/89 was found here.
    104: {"ns": 10, "ew": 15, "nw": 5, "ne": 5, "sw": 5, "se": 5, "corners": True},
}

# Subs that are NOT one of the six run/corner roles (gate leaves, end posts,
# junk/sign decor, damage stages, etc) — informational only, never wired by
# MapForge. Every guess here was made by eye off the sheet + an objective
# roofline-slope check (top-of-alpha-per-column vs x, least-squares slope:
# >0 = NS \, <0 = EW /, ~0 = symmetric/corner-shaped).
NOTES: dict[int, dict[int, str]] = {
    15: {
        1: "post/bollard", 2: "post/bollard",
        3: "sparse tall pole — signpost or gate-arm, not a run piece",
        5: "alt NS style: narrow reed/wattle wall (unmatched, no EW pair)",
        6: "alt NS style: wide wedge reed/wattle wall (unmatched)",
        7: "alt NS style: full reed/wattle wall panel (unmatched)",
        8: "alt EW rail (duplicate of 4)",
        10: "alt NS rail (duplicate of 9)",
    },
    77: {
        3: "junk-decor NS (hanging sack)", 4: "junk-decor NS (hanging crate)",
        5: "junk-decor EW (small box)", 6: "plain NS (dup style of 2)",
        7: "junk-decor EW", 8: "junk-decor EW", 9: "junk-decor NS",
        10: "junk-decor EW", 11: "junk-decor EW",
        12: "sign \"KEEP OUT\" EW", 13: "sign \"DANGER\" NS",
        14: "sign (partial, \"...ER!\") NS", 15: "sign \"CAME.../JUNK\" EW",
        16: "sign \"...DEPOT\" EW", 17: "damaged/short NS",
        18: "damaged/short EW", 19: "damaged/broken EW (staggered slats)",
        20: "damaged/jagged NS", 21: "plain EW (dup style of 1)",
        22: "junk-decor damaged EW",
    },
    86: {
        1: "narrow mesh strip, role unclear", 3: "narrow mesh strip, role unclear",
        4: "corner-post variant, denser mesh (alt of 2)",
        5: "clean EW (dup style of 6)", 8: "clean EW (dup of 6)", 9: "clean EW (dup of 6)",
        10: "torn/damaged NS", 11: "torn/damaged NS",
        12: "clean NS (dup style of 7, lighter mesh)",
        13: "clean EW (dup style of 6, lighter mesh)",
        14: "torn EW", 15: "torn EW", 16: "torn NS", 17: "torn NS",
        18: "heavily torn (near-gone)", 19: "heavily torn EW",
        20: "heavily torn EW", 21: "heavily torn EW (frame only)",
    },
    89: {
        2: "dup NS (post-left)", 3: "plain EW (dup of 4)",
        5: "post-left EW variant", 6: "post-left EW variant",
        7: "plain NS (dup of 1)", 8: "damaged/broken (angled crossing boards)",
        9: "dup NS", 10: "corner-post-ish, unconfirmed",
        11: "corner-post-ish, unconfirmed", 12: "post-right NS, weak slope",
        13: "corner-post variant (dup of 17)", 14: "corner-post-ish EW",
        15: "corner-post variant (dup, small)", 16: "corner-post-ish EW",
        18: "corner-post variant (dup, dense)", 19: "corner-post-ish, mild NS",
        20: "corner-post variant (dup, thin)", 21: "plain EW, no post",
        22: "corner-post-ish EW", 23: "corner-post variant (dup)",
    },
    104: {
        1: "short/flat variant EW", 2: "post/flat variant, role unclear",
        3: "short/flat variant NS", 4: "post/flat variant (dup of 5)",
        6: "clean EW (dup style of 15, narrower)",
        7: "clean NS (dup style of 10, narrower)",
        8: "post/flat variant (dup of 5)", 9: "post/flat variant (dup of 5)",
        11: "NS dup (alt shade of 10)", 12: "NS dup (alt shade of 10)",
        13: "EW dup (alt shade of 15)", 14: "EW dup (alt shade of 15)",
    },
}

SCALE = 2
BG = (28, 28, 32, 255)
CELL_PAD = 10
LABEL_H = 16
ROLE_H = 32  # 2 wrapped lines of role_font, for the longer NOTES strings
ROLE_WRAP_CHARS = 17
HEADER_H = 30


def _load_font(size: int) -> ImageFont.ImageFont:
    for candidate in ("arial.ttf", "DejaVuSans.ttf"):
        try:
            return ImageFont.truetype(candidate, size)
        except Exception:
            continue
    return ImageFont.load_default()


def _sti_filenames() -> dict[int, str]:
    """Try the live sidecar for authoritative filenames; fall back to the
    static FENCE_SLOTS table (this tool must not require the sidecar)."""
    try:
        import urllib.parse
        import urllib.request

        xml = (
            str(Path(os.environ.get("JA2_INSTALL", ""))
                / "Data-1.13" / "Ja2Set.dat.xml")
        )
        url = (
            "http://127.0.0.1:8773/api/v1/mapforge/tileset/palette"
            f"?xml={urllib.parse.quote(xml)}&tileset=72"
        )
        with urllib.request.urlopen(url, timeout=5) as resp:
            data = json.load(resp)
        names = {s["slot"]: s["sti_filename"] for s in data["slots"]}
        return {slot: names.get(slot, fallback) for slot, fallback in FENCE_SLOTS.items()}
    except Exception:
        return dict(FENCE_SLOTS)


def _load_cells() -> dict[str, dict]:
    with open(CELLS_XY, encoding="utf-8") as f:
        return json.load(f)


def _subs_for(cells: dict, slot: int) -> list[int]:
    return sorted(int(k.split(".")[1]) for k in cells if k.startswith(f"{slot}."))


def _role_label(slot: int, sub: int) -> str:
    roles = PROPOSED.get(slot)
    if roles:
        for role in ("ns", "ew", "nw", "ne", "sw", "se"):
            if roles.get(role) == sub:
                tag = role.upper()
                if role in ("nw", "ne", "sw", "se") and not roles.get("corners", True):
                    tag += "(=run)"
                return tag
    note = NOTES.get(slot, {}).get(sub)
    return note or ""


def build_sheet(atlas: Image.Image, cells: dict, sti_names: dict[int, str], stamp_roles: bool) -> Image.Image:
    per_slot = {slot: _subs_for(cells, slot) for slot in FENCE_SLOTS}

    max_w = max(cells[f"{slot}.{s}"]["w"] for slot, subs in per_slot.items() for s in subs)
    max_h = max(cells[f"{slot}.{s}"]["h"] for slot, subs in per_slot.items() for s in subs)
    max_cols = max(len(subs) for subs in per_slot.values())

    cell_w = max(max_w * SCALE + CELL_PAD * 2, 150)  # floor so wrapped NOTES text fits
    extra_label = ROLE_H if stamp_roles else 0
    cell_h = max_h * SCALE + CELL_PAD * 2 + LABEL_H + extra_label

    header_w = 300
    sheet_w = header_w + max_cols * cell_w + 10
    # Matches the loop below exactly: 10px top margin, then N rows of
    # (header + cell + 10px gap) — the last "gap" doubles as the bottom
    # margin. (A previous version under-counted these gaps and clipped
    # the last row's role-label text off the bottom of the canvas.)
    sheet_h = 10 + len(FENCE_SLOTS) * (HEADER_H + cell_h + 10)

    sheet = Image.new("RGBA", (sheet_w, sheet_h), BG)
    draw = ImageDraw.Draw(sheet)
    hdr_font = _load_font(15)
    label_font = _load_font(11)
    role_font = _load_font(12)

    y = 10
    for slot, sti in sti_names.items():
        subs = per_slot[slot]
        corners_note = ""
        if stamp_roles and slot in PROPOSED and not PROPOSED[slot].get("corners", True):
            corners_note = "  (no corner art)"
        draw.text(
            (10, y + 6),
            f"slot {slot} \u2014 {sti} ({len(subs)} subs){corners_note}",
            font=hdr_font, fill=(255, 255, 255, 255),
        )
        row_y = y + HEADER_H
        for i, sub in enumerate(subs):
            r = cells[f"{slot}.{sub}"]
            crop = atlas.crop((r["x"], r["y"], r["x"] + r["w"], r["y"] + r["h"]))
            crop2x = crop.resize((r["w"] * SCALE, r["h"] * SCALE), Image.NEAREST)

            cx = header_w + i * cell_w
            draw.rectangle(
                [cx, row_y, cx + cell_w - 4, row_y + cell_h - 4],
                outline=(85, 85, 92, 255),
            )
            label = f"s{slot}.{sub}"
            draw.text((cx + 4, row_y + 2), label, font=label_font, fill=(255, 210, 110, 255))

            paste_x = cx + (cell_w - crop2x.width) // 2
            paste_y = row_y + LABEL_H + (cell_h - LABEL_H - extra_label - crop2x.height) // 2
            sheet.alpha_composite(crop2x, (paste_x, paste_y))

            if stamp_roles:
                role = _role_label(slot, sub)
                if role:
                    is_tag = role.isupper() or "=" in role  # NS/EW/NW.../NW(=run), vs a NOTES sentence
                    color = (120, 230, 255, 255) if is_tag else (190, 190, 190, 255)
                    wrapped = role if is_tag else "\n".join(textwrap.wrap(role, ROLE_WRAP_CHARS)[:2])
                    draw.multiline_text(
                        (cx + 4, row_y + cell_h - ROLE_H - 4),
                        wrapped, font=role_font, fill=color, spacing=2,
                    )
        y = row_y + cell_h + 10

    return sheet


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    atlas = Image.open(ATLAS_PNG).convert("RGBA")
    cells = _load_cells()
    sti_names = _sti_filenames()

    plain = build_sheet(atlas, cells, sti_names, stamp_roles=False)
    plain_path = OUT_DIR / "fences_t72_sheet_v101.png"
    plain.convert("RGB").save(plain_path)
    print(f"wrote {plain_path}")

    if PROPOSED:
        proposed = build_sheet(atlas, cells, sti_names, stamp_roles=True)
        proposed_path = OUT_DIR / "fences_t72_sheet_proposed_v101.png"
        proposed.convert("RGB").save(proposed_path)
        print(f"wrote {proposed_path}")
    else:
        print("PROPOSED is empty — look at the plain sheet, fill PROPOSED, re-run.")


def _demo_role_label() -> None:
    """Smallest runnable self-check: every PROPOSED sub number must be a
    real sub of that slot (catches typos that would KeyError the renderer),
    and ns must differ from ew (they're supposed to be two distinct
    pieces, even in the corners:false fallback case)."""
    cells = _load_cells()
    for slot, roles in PROPOSED.items():
        valid = set(_subs_for(cells, slot))
        for role in ("ns", "ew", "nw", "ne", "sw", "se"):
            assert roles[role] in valid, (slot, role, roles[role], "not a real sub")
        assert roles["ns"] != roles["ew"], (slot, "ns == ew", roles["ns"])
    print("demo ok")


if __name__ == "__main__":
    import sys

    if len(sys.argv) > 1 and sys.argv[1] == "--demo":
        _demo_role_label()
    else:
        main()
