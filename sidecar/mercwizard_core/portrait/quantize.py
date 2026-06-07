"""255-color palette quantization with the JA2-specific gotcha fixes baked in.

Output is always a PIL P-mode image with:
  - palette.rawmode == 'RGB' (defeats ja2py's interleaving bug)
  - palette index 0 == (0,0,0), reserved for transparent
  - Transparent input pixels (alpha < 1) map to index 0
  - Opaque input pixels map to indices 1..255 — STRUCTURALLY guaranteed by
    quantizing to 255 colors and shifting all indices up by 1, leaving index 0
    completely free for the transparent reservation. No opaque pixel can
    accidentally land at index 0 regardless of its color.

Load-bearing rules (sti_engine.md):

1. **MAXCOVERAGE not MEDIANCUT**: PIL's MEDIANCUT collapses similar dark colors
   together (dark lips merge into dark face). Use method=1 (MAXCOVERAGE).

2. **707 Hole transparency by construction**: palette index 0 is transparent
   in JA2 STIs. Previous fixes tried to chase down which index the quantizer
   gave to (0,0,0) and swap it to 0, but the swap could drag dark hair pixels
   with it whenever the quantizer clustered them with the (0,0,0) background.
   This version sidesteps the whole problem: quantize to 255, shift every
   pixel's index +1, write (0,0,0) at palette[0]. Now index 0 belongs to
   nothing but the alpha-mask forcing step, so no opaque pixel can land
   there.

3. **ja2py rawmode='RGB' fix**: PIL's `.quantize()` produces palettes with
   `rawmode=None`, but the bytes are already RGB-interleaved. ja2py's
   `_palette_to_bytes()` treats `rawmode=None` as planar (RRR...GGG...BBB)
   and re-interleaves into rainbow garbage. We force `rawmode='RGB'` post-quantize.

4. **Alpha threshold = 1**: JA2 STIs are binary alpha (transparent OR opaque,
   no gradient). Any non-zero input alpha is treated as opaque. Threshold = 1
   preserves anti-aliased edges that earlier code (threshold = 128) cut into
   hard-transparent strips around hair, glasses, and any soft outline.
"""
from __future__ import annotations

from PIL import Image


class PortraitPaletteTooFewColors(ValueError):
    """PIL's quantizer returned fewer than 255 palette colors.

    Raised by quantize_with_anchor (and downstream paths) when the input
    image has too few distinct colors for the 255-color shift-up
    invariant to hold. Common triggers: a hand-painted near-monochrome
    eye/mouth sub-frame, an accidentally-blank explicit animation
    frame, or a transparent-passing test fixture.

    Route handlers should map this to a 400 with the message preserved
    — the user can re-export their source PNG with anti-aliasing or
    add color variation. Mapping AssertionError to a friendlier
    exception was bug-review finding C6.
    """


def _force_transparent_to_index_0(p_img: Image.Image, alpha_mask: Image.Image) -> Image.Image:
    """Ensure pixels marked transparent in alpha_mask use palette index 0.
    `alpha_mask` is an L-mode image: 0 = transparent, 255 = opaque.
    """
    pixels = list(p_img.getdata())
    a_data = list(alpha_mask.getdata())
    new = [0 if a == 0 else p for p, a in zip(pixels, a_data)]
    p_img.putdata(new)
    return p_img


def quantize_with_anchor(rgba_img: Image.Image) -> Image.Image:
    """Quantize an RGBA image to a P-mode image suitable for STI writing.

    Output:
      - mode='P'
      - 256-color palette in RGB-interleaved bytes
      - palette.rawmode='RGB'
      - palette index 0 = (0,0,0) (transparent — RESERVED, never collides
        with opaque pixels)
      - Transparent input pixels (alpha == 0) → palette index 0
      - Opaque input pixels → palette indices 1-255

    Algorithm:

    1. Compute binary alpha mask (any non-zero alpha is opaque).
    2. Composite transparent pixels over (0,0,0) black so the quantizer doesn't
       try to allocate palette slots for whatever was under the alpha mask
       in the source PNG.
    3. Quantize to **255 colors** (not 256) using MAXCOVERAGE.
    4. Shift every pixel's palette index up by 1, and shift the palette entries
       up too. Now indices 1..255 hold the 255 quantized colors; index 0 is
       free.
    5. Write (0,0,0) at palette[0].
    6. Force every alpha-mask-transparent input pixel to palette index 0.

    Because step 4 moves all opaque pixel indices into [1, 255] BEFORE we
    introduce the transparent reservation at index 0, there is no path for
    an opaque hair pixel to end up at index 0 — no matter how close its color
    is to (0,0,0). The earlier "anchor + swap" approach could fail when the
    quantizer clustered the anchor with dark hair; this approach cannot fail
    that way by construction.
    """
    if rgba_img.mode != "RGBA":
        rgba_img = rgba_img.convert("RGBA")
    w, h = rgba_img.size

    # 1. Binary alpha — any non-zero alpha is opaque. Threshold = 1 preserves
    #    anti-aliased edges (vs. the old 128 which cut hair edges to holes).
    r, g, b, a = rgba_img.split()
    a_bin = a.point(lambda v: 255 if v >= 1 else 0)

    # 2. Composite transparent pixels over pure black so the quantizer's input
    #    is a clean RGB image — no alpha channel surprises.
    rgb = rgba_img.convert("RGB")
    black_bg = Image.new("RGB", (w, h), (0, 0, 0))
    composited = Image.composite(rgb, black_bg, a_bin)

    # 3. Quantize to 255 colors using MAXCOVERAGE. We deliberately request
    #    one less than the full 256 so we can reserve index 0 for transparency
    #    in step 4 without sacrificing any quantized color.
    p_img = composited.quantize(colors=255, method=1, dither=Image.Dither.NONE)

    # 4. Shift all palette indices up by 1, opening up index 0.
    #    - Pixels: every index p becomes p+1 (now in [1, 255]).
    #    - Palette: insert a (0,0,0) entry at position 0, push the other 255
    #      entries up. Resulting palette has 256 entries, indices 0..255.
    pal = list(p_img.getpalette())  # 255 colors × 3 = 765 bytes
    # Bug-review #100: assert the palette is at least the size we requested
    # before slicing. PIL returns 768 bytes (256 colors × 3) when padded,
    # 765 bytes (255 × 3) when not — both fine. But a quantizer that emitted
    # fewer colors than asked (zero-pixel input, totally-transparent frame
    # passing through here by mistake) would slice shorter than expected and
    # the resulting palette would be malformed without an error trace. The
    # safety net is cheap; the corner-case is hard to debug without it.
    if len(pal) < 255 * 3:
        # Surface a stable, catchable exception rather than AssertionError
        # — sti.py and the portrait/animation pipelines don't catch
        # AssertionError, so a near-monochrome animation frame (e.g. a
        # hand-painted blink with ~20 distinct colors, well under 255)
        # propagated up as an uncaught 500 with no recovery path.
        # PortraitPaletteTooFewColors keeps the same diagnostic message
        # but lets route handlers map it to a friendly 400. Bug-review
        # finding C6.
        raise PortraitPaletteTooFewColors(
            f"PIL quantizer returned only {len(pal) // 3} palette colors "
            f"when 255 were requested — input image may have been empty / "
            f"single-color / fully transparent. Check the caller before "
            f"shipping a malformed STI palette."
        )
    if len(pal) > 255 * 3:
        # PIL sometimes returns a 768-byte palette padded with zeros — trim to
        # exactly the 255 quantized colors before shifting up.
        pal = pal[:255 * 3]
    new_pal = [0, 0, 0] + pal  # palette[0] = (0,0,0); quantized colors at 1..255
    # Pad to 256 entries if needed
    while len(new_pal) < 256 * 3:
        new_pal.append(0)

    pixels = list(p_img.getdata())
    shifted = [p + 1 for p in pixels]
    p_img.putdata(shifted)
    p_img.putpalette(new_pal)

    # 5. Force transparent input pixels to use index 0. Only alpha-mask-zero
    #    pixels are touched; all opaque pixels keep their shifted indices.
    _force_transparent_to_index_0(p_img, a_bin)

    # 6. The ja2py rawmode fix
    if p_img.palette is not None:
        p_img.palette.rawmode = "RGB"

    return p_img


def quantize_against_palette(rgba_img: Image.Image, reference_p: Image.Image) -> Image.Image:
    """Quantize `rgba_img` using `reference_p`'s palette so all sub-frames in
    an STI share one palette. Returns a P-mode image.

    Required because an 8-frame SmallFace STI's sub-frames must share a single
    palette or the ETRLE offsets desync and the result is rainbow corruption.

    Index-0 reservation rule (parallel to `quantize_with_anchor`): palette
    index 0 is reserved for transparency. PIL's `quantize(palette=...)` uses
    nearest-neighbor matching against the full palette including index 0;
    when palette[0] == (0,0,0) (our convention), any opaque-near-black pixel
    in `rgba_img` quantizes to index 0 and the engine renders it transparent.
    Post-quantize we remap any OPAQUE pixel at index 0 to the next-nearest
    non-zero palette entry so only alpha-mask-transparent pixels stay at
    index 0. Without this fix the union-palette quantize for SmallFace
    animation frames could re-introduce the Eskimo "transparent hair" bug.
    """
    if rgba_img.mode != "RGBA":
        rgba_img = rgba_img.convert("RGBA")
    if reference_p.mode != "P":
        raise ValueError(f"reference_p must be mode='P', got {reference_p.mode}")

    # Alpha mask for transparency (threshold = 1 matches quantize_with_anchor)
    a_bin = rgba_img.split()[-1].point(lambda v: 255 if v >= 1 else 0)

    # Composite transparent over black
    rgb = rgba_img.convert("RGB")
    black_bg = Image.new("RGB", rgba_img.size, (0, 0, 0))
    composited = Image.composite(rgb, black_bg, a_bin)

    # Quantize against the reference's palette
    p_img = composited.quantize(palette=reference_p, dither=Image.Dither.NONE)

    # Defensive remap: any opaque pixel that PIL mapped to index 0 must go
    # to its next-nearest non-zero palette entry. We compute the
    # second-closest by re-quantizing JUST those pixels against a copy of
    # the reference palette with palette[0] overwritten by a sentinel that
    # forces them away from slot 0.
    _remap_opaque_index_0_to_nearest_nonzero(p_img, a_bin, reference_p)

    # Force transparent pixels to index 0 (matches reference convention)
    _force_transparent_to_index_0(p_img, a_bin)

    # rawmode fix
    if p_img.palette is not None:
        p_img.palette.rawmode = "RGB"

    return p_img


def _remap_opaque_index_0_to_nearest_nonzero(
    p_img: Image.Image,
    alpha_mask: Image.Image,
    reference_p: Image.Image,
) -> None:
    """In-place: every pixel where alpha_mask is non-zero AND palette index
    is 0 gets remapped to the next-nearest palette entry (index 1..255).

    Approach: build a "remap" palette where slot 0 is replaced by a sentinel
    color very far from any natural pixel (white). Re-quantize the entire
    image against that remap palette; PIL's nearest-neighbor matcher will
    pick a non-zero slot for any opaque-near-black pixel that originally
    landed at slot 0 (since the sentinel is now white, dark pixels won't
    match it). Then OR-merge: for opaque pixels that were at index 0 in
    the original quantize, use the new index; for everything else, keep
    the original.
    """
    pixels = list(p_img.getdata())
    a_data = list(alpha_mask.getdata())

    # Quick exit: no opaque pixels at index 0 → nothing to fix.
    needs_remap = any(p == 0 and a != 0 for p, a in zip(pixels, a_data))
    if not needs_remap:
        return

    # Build the remap palette: same as reference but slot 0 swapped to a
    # sentinel color (white) so dark pixels won't pick it as nearest.
    ref_palette_bytes = bytes(reference_p.getpalette())[: 256 * 3]
    if len(ref_palette_bytes) < 768:
        ref_palette_bytes = ref_palette_bytes + b"\x00" * (768 - len(ref_palette_bytes))
    remap_pal = bytearray(ref_palette_bytes)
    remap_pal[0:3] = b"\xff\xff\xff"  # sentinel: pure white at slot 0
    remap_ref = Image.new("P", (1, 1))
    remap_ref.putpalette(bytes(remap_pal), rawmode="RGB")

    # Re-quantize the composited image against the remap palette. We need
    # the same composited RGB the caller produced, but it's not retained;
    # the cheapest path is to reconstruct via the alpha mask and palette
    # lookup. Simpler: convert p_img back to RGB via its current palette,
    # then quantize against remap_ref.
    rgb_via_current = p_img.convert("RGB")
    remapped = rgb_via_current.quantize(palette=remap_ref, dither=Image.Dither.NONE)
    remapped_pixels = list(remapped.getdata())

    fixed = [
        (remapped_pixels[i] if (pixels[i] == 0 and a_data[i] != 0) else pixels[i])
        for i in range(len(pixels))
    ]
    p_img.putdata(fixed)
