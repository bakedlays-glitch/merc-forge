"""Render every REGISTERED custom prop sheet of a tileset to a labeled contact
sheet, so the art can be eyeballed against its prop_labels name/per_sub.

The lint (prop_labels_lint.py) proves every custom sheet HAS a name; it cannot
prove the name matches the ART. This does the visual half: one montage per
tileset, each sheet captioned with its current prop_labels name + per-sub names,
so a human (or a vision model) can catch a label that lies about the pixels
(the fo2_junktown_gate "canvas tent" trap).

    python tools/prop_labels_audit_render.py --tileset 70 --tileset 71 [--out DIR]

Read-only. Uses ja2py load_8bit_sti; index-0 (transparent) painted flat gray.
"""
from __future__ import annotations
import os
import argparse, io, sys
from pathlib import Path
from PIL import Image, ImageDraw
import xml.etree.ElementTree as ET

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))  # sidecar/
from mercwizard_core.mapforge import prop_labels as PL  # noqa: E402
# ja2py lives in the repo's third-party toolset
for cand in (HERE.parents[2] / "ja2-open-toolset", Path(os.environ.get("JA2_OPEN_TOOLSET", ""))):
    if cand.exists():
        sys.path.insert(0, str(cand)); break
from ja2py.fileformats.Sti import load_8bit_sti, is_8bit_sti  # noqa: E402

DEFAULT_INSTALL = Path(os.environ.get("JA2_INSTALL", "")) / "Data-1.13"
BG = (78, 78, 84)


def own_block(xml_path: Path, ts: int) -> dict[int, str]:
    for t in ET.parse(xml_path).getroot().iter("Tileset"):
        if int(t.get("index", -1)) == ts:
            f = t.find("Files")
            return {int(x.get("index")): (x.text or "").strip()
                    for x in (f.findall("file") if f is not None else [])}
    return {}


def resolve(tsroot: Path, ts: int, name: str) -> Path | None:
    # VFS-ish: the tileset's own dir first, then any sibling that has it
    p = tsroot / str(ts) / name
    if p.exists():
        return p
    for d in sorted(tsroot.glob("*")):
        q = d / name
        if q.exists():
            return q
    return None


def render_cell(name: str, path: Path, label: dict | None) -> Image.Image:
    imgs = load_8bit_sti(io.BytesIO(path.read_bytes()))
    subs = imgs.images
    cw = min(max((s.image.size[0] for s in subs), default=16) + 6, 150)
    ch = min(max((s.image.size[1] for s in subs), default=16) + 14, 150)
    cols = min(len(subs), 10) or 1
    rows = (len(subs) + cols - 1) // cols
    cap_h = 30
    w = max(cols * cw, 360)
    img = Image.new("RGB", (w, rows * ch + cap_h), (28, 28, 30))
    d = ImageDraw.Draw(img)
    nm = (label or {}).get("name")
    origin = (label or {}).get("origin", "")
    head = f"{name}  ->  {nm or '(NO LABEL - filename shown)'}"
    d.text((3, 2), head, fill=(255, 230, 120) if nm else (255, 120, 120))
    ps = (label or {}).get("per_sub") or {}
    d.text((3, 15), (f"[{origin}]  " if origin else "") + (f"{len(ps)} sub-names" if ps else ""), fill=(150, 200, 255))
    for i, s in enumerate(subs):
        # No transparency mask: some FO2 sheets use index-0 as a real fill
        # colour, so masking it blanks the prop. Show full RGB on the cell;
        # genuinely-transparent regions render as the sheet's palette[0].
        cell = s.image.convert("RGB")
        if cell.size[0] > cw - 6 or cell.size[1] > ch - 12:
            cell.thumbnail((cw - 6, ch - 12))
        r, c = divmod(i, cols)
        img.paste(cell, (c * cw + 3, cap_h + r * ch + 10))
        sub_lbl = ps.get(str(i + 1), "")
        d.text((c * cw + 3, cap_h + r * ch), f"{i+1}", fill=(180, 220, 255))
    return img


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--install", default=str(DEFAULT_INSTALL))
    ap.add_argument("--tileset", type=int, action="append", required=True)
    ap.add_argument("--out", default=str(HERE.parent.parent.parent / "scratch" / "prop_labels_audit"))
    args = ap.parse_args()
    PL.reload()
    root = Path(args.install)
    xml_path = root / "Ja2Set.dat.xml"
    tsroot = root / "tilesets"
    out = Path(args.out); out.mkdir(parents=True, exist_ok=True)
    for ts in args.tileset:
        reg = own_block(xml_path, ts)
        cells = []
        for slot, fname in sorted(reg.items()):
            if not PL.is_custom(fname):
                continue
            p = resolve(tsroot, ts, fname)
            if p is None or not is_8bit_sti(io.BytesIO(p.read_bytes())):
                continue
            cells.append(render_cell(f"s{slot} {fname}", p, PL.label_for(fname)))
        if not cells:
            print(f"T{ts}: no custom 8-bit sheets"); continue
        W = max(c.width for c in cells) + 8
        H = sum(c.height for c in cells) + 6 * len(cells) + 6
        sheet = Image.new("RGB", (W, H), (18, 18, 20))
        y = 4
        for c in cells:
            sheet.paste(c, (4, y)); y += c.height + 6
        o = out / f"audit_T{ts}.png"
        sheet.save(o)
        print(f"T{ts}: {len(cells)} custom sheets -> {o}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
