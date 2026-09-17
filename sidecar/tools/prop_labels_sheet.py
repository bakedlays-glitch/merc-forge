"""Stitch the verify_prop_labels.mjs screenshots into one contact sheet per tileset.

    python sidecar/tools/prop_labels_sheet.py <shots_dir> <tileset> <out.png>

Top row: the palette panel filtered by each custom prefix. Below: every
sub-picker capture for that tileset, in slot order. Pure PIL; no game data.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

from PIL import Image, ImageDraw

shots, tileset, out = Path(sys.argv[1]), sys.argv[2], Path(sys.argv[3])
pal = sorted(shots.glob(f"palette_T{tileset}_*_v101.png"))
subs = sorted(shots.glob(f"subs_T{tileset}_s*_v101.png"),
              key=lambda p: int(re.search(r"_s(\d+)_", p.name).group(1)))
if not pal:
    sys.exit(f"no palette shots for T{tileset} in {shots}")

GAP, LABEL = 12, 18
imgs_pal = [Image.open(p).convert("RGB") for p in pal]
imgs_sub = [Image.open(p).convert("RGB") for p in subs]
# Palette panels are tall; cap the row height and let the picker grid flow.
pal_h = max(i.height for i in imgs_pal)
pal_w = sum(i.width for i in imgs_pal) + GAP * (len(imgs_pal) + 1)
cols = max(1, pal_w // (max((i.width for i in imgs_sub), default=260) + GAP))
sub_w = max((i.width for i in imgs_sub), default=0)
rows = [imgs_sub[i:i + cols] for i in range(0, len(imgs_sub), cols)]
sub_h = sum(max(i.height for i in r) + LABEL + GAP for r in rows)
W = max(pal_w, cols * (sub_w + GAP) + GAP)
H = LABEL + pal_h + GAP * 2 + sub_h + LABEL
sheet = Image.new("RGB", (W, H), (12, 14, 18))
d = ImageDraw.Draw(sheet)
d.text((GAP, 2), f"T{tileset} palette, filtered by custom prefix (verify_prop_labels.mjs)", fill=(200, 200, 200))
x, y = GAP, LABEL
for p, im in zip(pal, imgs_pal):
    sheet.paste(im, (x, y))
    d.text((x, y + im.height + 2), p.stem.split("_")[2], fill=(140, 160, 200))
    x += im.width + GAP
y = LABEL + pal_h + GAP * 2
d.text((GAP, y - LABEL + 2), f"{len(subs)} sub-pickers (slot order)", fill=(200, 200, 200))
for r in rows:
    x = GAP
    for im in r:
        sheet.paste(im, (x, y))
        x += sub_w + GAP
    y += max(i.height for i in r) + LABEL + GAP
sheet.save(out)
print(out, sheet.size, f"{len(pal)} palette shots + {len(subs)} pickers")
