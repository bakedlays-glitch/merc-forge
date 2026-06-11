"""MapForge map generators — first-class compiled subsystem.

A *generator* is a Python class that turns a MapForge session into a
new map by issuing a stream of primitive edit ops (place_tile,
stamp_struct, set_room, ...). Generators run INSIDE the session — they
don't write raw .dat bytes. Every op they emit goes through the same
`_apply_single_edit` path the brush uses, so:

  - Undo/redo works mid-generation (each generator's run = one
    grouped undo entry from the user's POV)
  - The canvas re-paints incrementally as ops arrive — user sees the
    map build itself in real time
  - Generators compose: run one, then another, on the same session
  - Generators are testable against the same op API the UI calls

This module ships compiled into the PyInstaller bundle. There is NO
plugin loader, NO subprocess shell-out, NO arbitrary-code-eval path.
Users pick from a fixed menu in the UI; authoring a new generator
means adding a class here, rebuilding, and shipping a new
distributable.

Adding a generator:
  1. Subclass `Generator` below.
  2. Declare `name`, `label`, `description`, and `params`.
  3. Implement `iter_ops(ctx, params)` as a generator function that
     yields `EditOp` dicts (matching the shape `EditOp` in
     `routes/mapforge.py` expects).
  4. Register the class in `REGISTRY` at the bottom of this file.

The route layer (`routes/mapforge.py::run_generator`) handles streaming,
op application, error wrapping, and snapshot/rollback.
"""
from __future__ import annotations

import math
import random
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, Callable, Iterator, Optional

from mercwizard_core.mapforge import shadow_pairs


# ──────────────────────────────────────────────────────────────────────────
#  Param schema
# ──────────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Param:
    """One parameter of a generator. Surfaced into the UI's param form
    AND parsed from the console's `:gen <name> k=v ...` syntax.

    `type` controls the form widget AND the value coercion in the
    console parser. Use the smallest type that fits — int for counts,
    float for fractions, str for free text, bool for toggles.
    """
    name: str
    type: str  # "int" | "float" | "str" | "bool"
    default: Any
    description: str = ""
    min: Optional[float] = None
    max: Optional[float] = None

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "type": self.type,
            "default": self.default,
            "description": self.description,
            "min": self.min,
            "max": self.max,
        }


# Two optional params shared by the layer-targeting generators. Pick a
# corpus SOURCE + BIOME and the generator auto-fills its subframe variety
# from the distilled corpus (real maps of that source+biome) instead of
# stamping one flat `sub`. Both default OFF (blank) → byte-identical legacy
# behavior; an explicit `subs` always wins over the corpus.
_BIOME_CHOICES = "urban/desert/tropical/temperate/farm/swamp/cave/cliff/arctic/wasteland"
_CORPUS_PARAMS = [
    Param(name="corpus_source", type="str", default="",
          description=("Corpus source for subframe variety: ''(off)/stock/redux/"
                       "combined. Auto-fills the sub mix from real maps of this "
                       "source+biome. An explicit `subs` overrides it.")),
    Param(name="biome", type="str", default="",
          description=f"Biome for corpus variety ({_BIOME_CHOICES}). Needs corpus_source."),
]

# Default-on clip that keeps placement inside the iso playable diamond. A JA2
# sector is a 160×160 tile SQUARE, but tile (x,y) renders isometrically, so the
# playable battlefield is a DIAMOND inscribed in that square — the four grid
# corners are off-map border. Shared by every layer-targeting generator.
_PLAYABLE_PARAM = Param(
    name="clip_to_playable", type="bool", default=True,
    description=("Keep tiles inside the iso playable diamond — skip the off-map "
                 "border corners. A sector is a square but the playable map is a "
                 "diamond; turn this off only to paint the map border itself."),
)

# Same toggle, default OFF for the precise region tools (fill, rect): they paint
# exactly the region you specify — including the off-map border, the way vanilla
# maps texture the whole grid. Flip true to restrict them to the diamond.
_PLAYABLE_PARAM_OFF = Param(
    name="clip_to_playable", type="bool", default=False,
    description=("Restrict to the iso playable diamond — skip the off-map border "
                 "corners. Default OFF here so the tool fills exactly the region "
                 "you specify (vanilla maps texture the border too)."),
)


# ──────────────────────────────────────────────────────────────────────────
#  Generator context
# ──────────────────────────────────────────────────────────────────────────


@dataclass
class GeneratorContext:
    """The slice of session state a generator can read while computing
    its op stream. Deliberately read-only — generators must NOT mutate
    `parsed` directly. To change the map, yield an op.

    `rows`/`cols` are the iso grid dimensions (160×160 for stock JA2).
    `parsed` is the current sector dict — a generator that augments
    (e.g. add ruins to existing buildings) reads it; a generator that
    wipes-and-rebuilds (e.g. WipeGenerator) ignores it.

    `slot_map` and `frame_count` are OPTIONAL tileset metadata the route
    layer wires up so a generator can validate against the active
    tileset before emitting ops:
      - `slot_map[slot]` → the STI filename for that slot (or absent /
        empty if the tileset doesn't define it).
      - `frame_count(slot)` → number of sub-frames in that slot's STI
        (0 if missing/unloadable). Lazily computed + cached by the route.
    Both are None in bare/test contexts; generators that need them
    must degrade gracefully when they're absent (see AutoShadow).
    """
    rows: int
    cols: int
    parsed: dict  # Read-only — mutate via yielded ops, not direct edit
    slot_map: Optional[dict[int, str]] = None
    frame_count: Optional[Callable[[int], int]] = None


# ──────────────────────────────────────────────────────────────────────────
#  Generator base
# ──────────────────────────────────────────────────────────────────────────


class Generator(ABC):
    """Abstract base for all built-in map generators.

    Subclasses MUST set `name` (machine-readable, used in console
    `:gen <name>` and route URLs), `label` (human-readable for the UI),
    `description` (1-2 sentences shown in the picker), and `params`
    (the typed list of input controls).

    Subclasses MUST implement `iter_ops(ctx, params)` as a generator
    function that yields op dicts. Each yielded op should have the
    same shape as `EditOp` in routes/mapforge.py — the route layer
    validates them before applying.

    `iter_ops` may emit `{"phase": <name>, "status": "start"|"done",
    "label": "..."}` events that don't carry a mutation; the route
    layer forwards these to the frontend's log/progress UI without
    treating them as edits.
    """
    name: str = ""
    label: str = ""
    description: str = ""
    params: list[Param] = []

    @abstractmethod
    def iter_ops(self, ctx: GeneratorContext, params: dict) -> Iterator[dict]:
        """Yield a stream of edit ops + phase events.

        Each yielded dict is one of:

          - `{"x": int, "y": int, "op": str, "layer": str, ...}` — a
            mutation op routed to `_apply_single_edit`. See
            `routes/mapforge.py::EditOp` for the full schema.

          - `{"phase": str, "status": "start"|"done", "label": str}`
            — a structured progress event the frontend renders in
            the log/progress UI. Use these to chunk a long generator
            into named phases ("roads", "buildings", "details") the
            user can follow.

        Either kind may be yielded in any order. The route layer
        classifies each by the keys present.

        Generators SHOULD NOT raise inside `iter_ops` if it can be
        avoided — surface failures as a final `{"phase": "error",
        "status": "done", "label": <msg>}` and return. Raising aborts
        the stream and the user sees a generic "generator failed"
        message instead of the specific cause.
        """

    def to_dict(self) -> dict:
        """Serialize the generator's metadata for the `/generators`
        listing route. Excludes `iter_ops` — that's invoked via
        `/run-generator` only."""
        return {
            "name": self.name,
            "label": self.label,
            "description": self.description,
            "params": [p.to_dict() for p in self.params],
        }


# ──────────────────────────────────────────────────────────────────────────
#  WipeGenerator — minimal end-to-end proof
# ──────────────────────────────────────────────────────────────────────────


class WipeGenerator(Generator):
    """Clear every layer at every tile.

    Useful as:
      - A "start from scratch" base before running a town/dungeon
        generator.
      - A test of the streaming pipeline — 160×160 grid × 6 layers =
        153,600 set_entries ops streaming live to the canvas; if
        anything's wrong with the streaming-ops contract this generator
        surfaces it.

    Skips room-id resets by default; the engine's empty-grid room is
    0 and room IDs persist on a wipe so the user's room boundaries
    don't disappear with the tiles. Pass `reset_rooms=true` to clear
    them too.
    """
    name = "wipe"
    label = "Wipe sector (clear all tiles)"
    description = (
        "Clear every tile from every layer. Optionally reset room IDs. "
        "Use as a clean base before running a town/dungeon generator."
    )
    params = [
        Param(
            name="reset_rooms",
            type="bool",
            default=False,
            description="Also clear room IDs (defaults to keeping them).",
        ),
    ]

    LAYERS = ("land", "objs", "shadows", "structs", "roofs", "onroofs")

    def iter_ops(self, ctx: GeneratorContext, params: dict) -> Iterator[dict]:
        reset_rooms = bool(params.get("reset_rooms", False))

        # Expected op count for the progress bar. Each tile emits one op
        # per layer; if reset_rooms is set, also one set_room op per tile.
        total = ctx.rows * ctx.cols * len(self.LAYERS)
        if reset_rooms:
            total += ctx.rows * ctx.cols
        yield {
            "phase": "wipe",
            "status": "start",
            "label": f"Clearing {ctx.rows * ctx.cols} tiles across {len(self.LAYERS)} layers…",
            "total": total,
        }

        # Per-tile op stream. Order: y outer, x inner so the visible
        # render fills top-to-bottom (looks like erasing line by line).
        for y in range(ctx.rows):
            for x in range(ctx.cols):
                for layer in self.LAYERS:
                    yield {
                        "x": x,
                        "y": y,
                        "op": "set_entries",
                        "layer": layer,
                        "entries": [],
                    }
                if reset_rooms:
                    yield {
                        "x": x,
                        "y": y,
                        "op": "set_room",
                        "room_id": 0,
                    }

        yield {"phase": "wipe", "status": "done", "label": "Cleared."}


# ──────────────────────────────────────────────────────────────────────────
#  Layer constants — used by the layer-targeting generators
# ──────────────────────────────────────────────────────────────────────────


# The six editable layers in a JA2 sector. Mirrors the LAYERS tuple on
# WipeGenerator but exposed at module level so other generators can
# validate user-supplied layer params consistently. Order doesn't
# matter here — it's just an allow-list.
ALL_LAYERS = ("land", "objs", "shadows", "structs", "roofs", "onroofs")


def _validate_layer(layer: str) -> str:
    """Coerce a user-supplied layer string. Raises ValueError on miss.
    Used by the layer-targeting generators (Fill, Rectangle) to fail
    fast inside iter_ops rather than letting the route layer's
    `BAD_LAYER` 400 absorb the error mid-stream."""
    if layer not in ALL_LAYERS:
        raise ValueError(
            f"unknown layer {layer!r}; valid: {', '.join(ALL_LAYERS)}"
        )
    return layer


# ──────────────────────────────────────────────────────────────────────────
#  FillLayerGenerator — paint every tile of one layer with one slot/sub
# ──────────────────────────────────────────────────────────────────────────


class FillLayerGenerator(Generator):
    """Replace every tile's entry on one layer with a single (slot, sub).

    The flagship use case: "set ground to grass" — pick the grass STI's
    slot+sub in the palette, run `:gen fill layer=land slot=N sub=M`,
    and the whole sector's ground layer becomes that tile.

    The op is `place` (the "replace this tile's entries with exactly
    one" semantic from `routes/mapforge.py::_apply_single_edit`), so
    pre-existing stacks on the target layer are wiped — same as if the
    user clicked every tile with a normal brush in normal paint mode.

    For structs/roofs the engine multi-tile JSD footprint isn't
    respected (this generator stamps one entry per tile, not a real
    `stamp_struct` with the JSD walk). Use BuildingStamp once it lands
    for that.
    """
    name = "fill"
    label = "Fill layer with one tile"
    description = (
        "Replace every tile's entry on the chosen layer with a single "
        "(slot, sub). Useful for 'set ground to grass' style operations."
    )
    params = [
        Param(
            name="layer",
            type="str",
            default="land",
            description="One of: land, objs, shadows, structs, roofs, onroofs",
        ),
        Param(
            name="slot",
            type="int",
            default=0,
            description="STI slot in the tileset (0-based; slot 0 = FIRSTTEXTURE)",
            min=0,
            max=255,
        ),
        Param(
            name="sub",
            type="int",
            default=1,
            description=(
                "Sub-index within the STI — 1-BASED per the JA2 .dat "
                "convention. sub=1 is the first frame of the STI; sub=0 "
                "is invalid and renders as nothing."
            ),
            min=1,
            max=255,
        ),
        Param(name="seed", type="int", default=42,
              description="RNG seed for corpus variant picks (only used when corpus_source is set)"),
    ] + _CORPUS_PARAMS + [_PLAYABLE_PARAM_OFF]

    def iter_ops(self, ctx: GeneratorContext, params: dict) -> Iterator[dict]:
        layer = _validate_layer(str(params.get("layer", "land")))
        slot = int(params.get("slot", 0))
        sub = int(params.get("sub", 1))
        rng = random.Random(int(params.get("seed", 42)))
        pick_sub = _make_sub_picker(rng, sub, _resolve_corpus_subs(params, layer, slot, ""))
        playable = _make_playable_predicate(ctx, bool(params.get("clip_to_playable", False)))

        yield {
            "phase": "fill",
            "status": "start",
            "label": f"Filling {layer} with slot {slot} sub {sub} ({ctx.rows * ctx.cols} tiles)…",
            "total": ctx.rows * ctx.cols,
        }

        for y in range(ctx.rows):
            for x in range(ctx.cols):
                if playable is not None and not playable(x, y):
                    continue
                yield {
                    "x": x,
                    "y": y,
                    "op": "place",
                    "layer": layer,
                    "slot": slot,
                    "sub": pick_sub(),
                }

        yield {"phase": "fill", "status": "done", "label": f"Filled {layer}."}


# ──────────────────────────────────────────────────────────────────────────
#  RectangleGenerator — outline or filled rect with one slot/sub
# ──────────────────────────────────────────────────────────────────────────


class RectangleGenerator(Generator):
    """Draw a rectangle (outline or filled) of one (slot, sub) onto one layer.

    Corner coords are inclusive. The two extremes don't need to be
    "top-left + bottom-right" — the generator normalizes so e.g.
    `x1=80 y1=50 x2=10 y2=20` traces the same rectangle as the
    canonical orientation.

    `mode=outline` walks only the perimeter (single-tile-wide border).
    `mode=fill` paints every tile inside the rect.

    The op semantic is `place` — same as FillLayerGenerator. Pre-fix
    `add` was tempting (it'd let you stack a fence on top of existing
    grass), but rectangle-as-fence is by far the most common use case
    and `place`-with-empty-other-layers is the cleanest baseline. If
    you want to overlay onto an existing layer without replacing it,
    paint by hand with the brush in `add` mode.
    """
    name = "rect"
    label = "Rectangle (outline or filled)"
    description = (
        "Draw a rectangle of one (slot, sub) on the chosen layer. "
        "Coordinates are inclusive; orientation is normalized so "
        "(x1,y1) and (x2,y2) can be in any order."
    )
    params = [
        Param(name="x1", type="int", default=0, description="One corner X", min=0, max=255),
        Param(name="y1", type="int", default=0, description="One corner Y", min=0, max=255),
        Param(name="x2", type="int", default=0, description="Other corner X", min=0, max=255),
        Param(name="y2", type="int", default=0, description="Other corner Y", min=0, max=255),
        Param(
            name="layer",
            type="str",
            default="land",
            description="One of: land, objs, shadows, structs, roofs, onroofs",
        ),
        Param(name="slot", type="int", default=0, description="STI slot (0-based)", min=0, max=255),
        Param(
            name="sub", type="int", default=1,
            description="STI sub-index — 1-BASED (sub=1 = first frame). sub=0 renders nothing.",
            min=1, max=255,
        ),
        Param(
            name="mode",
            type="str",
            default="outline",
            description="`outline` (perimeter only) or `fill` (every tile inside)",
        ),
        Param(name="seed", type="int", default=42,
              description="RNG seed for corpus variant picks (only used when corpus_source is set)"),
    ] + _CORPUS_PARAMS + [_PLAYABLE_PARAM_OFF]

    def iter_ops(self, ctx: GeneratorContext, params: dict) -> Iterator[dict]:
        layer = _validate_layer(str(params.get("layer", "land")))
        slot = int(params.get("slot", 0))
        sub = int(params.get("sub", 0))
        mode = str(params.get("mode", "outline")).lower()
        rng = random.Random(int(params.get("seed", 42)))
        pick_sub = _make_sub_picker(rng, sub, _resolve_corpus_subs(params, layer, slot, ""))
        playable = _make_playable_predicate(ctx, bool(params.get("clip_to_playable", False)))
        if mode not in ("outline", "fill"):
            raise ValueError(f"mode must be 'outline' or 'fill', got {mode!r}")

        # Normalize + clamp to grid. Out-of-bounds inputs are clamped
        # instead of raising so a user typing slightly past the edge
        # still gets a sensible rectangle (no surprise nothing-happens).
        x1 = max(0, min(ctx.cols - 1, int(params.get("x1", 0))))
        y1 = max(0, min(ctx.rows - 1, int(params.get("y1", 0))))
        x2 = max(0, min(ctx.cols - 1, int(params.get("x2", 0))))
        y2 = max(0, min(ctx.rows - 1, int(params.get("y2", 0))))
        if x1 > x2:
            x1, x2 = x2, x1
        if y1 > y2:
            y1, y2 = y2, y1

        width = x2 - x1 + 1
        height = y2 - y1 + 1
        total_tiles = width * height if mode == "fill" else (
            # outline perimeter — handle degenerate cases (1xN or Nx1
            # collapse to a line, not an "outline rect" with double-
            # counted tiles)
            width * height if width == 1 or height == 1
            else 2 * width + 2 * (height - 2)
        )

        yield {
            "phase": "rect",
            "status": "start",
            "label": (
                f"Drawing {mode} rectangle ({x1},{y1})→({x2},{y2}) on {layer} "
                f"with slot {slot} sub {sub} — {total_tiles} tiles…"
            ),
            "total": total_tiles,
        }

        for y in range(y1, y2 + 1):
            for x in range(x1, x2 + 1):
                if playable is not None and not playable(x, y):
                    continue
                if mode == "outline" and width > 1 and height > 1:
                    # Skip interior tiles for outline mode (but always
                    # emit when width or height collapses to 1 — at
                    # that point the rect IS a line and we paint it).
                    on_edge = (x == x1 or x == x2 or y == y1 or y == y2)
                    if not on_edge:
                        continue
                yield {
                    "x": x,
                    "y": y,
                    "op": "place",
                    "layer": layer,
                    "slot": slot,
                    "sub": pick_sub(),
                }

        yield {"phase": "rect", "status": "done", "label": f"Rectangle done."}


# ──────────────────────────────────────────────────────────────────────────
#  Shared helpers for scatter-family generators
# ──────────────────────────────────────────────────────────────────────────


def _normalize_region(
    ctx: GeneratorContext,
    x1: Optional[int], y1: Optional[int],
    x2: Optional[int], y2: Optional[int],
) -> tuple[int, int, int, int]:
    """Coerce 4 corner params into a clamped (x1, y1, x2, y2) tuple.

    Any None → defaults to the full grid. Negative or out-of-bounds
    values clamp to grid edges. Reversed corners normalize (x1 ≤ x2,
    y1 ≤ y2). Same rules as RectangleGenerator — kept consistent so
    `:gen scatter region_x1=10 ...` and `:gen rect x1=10 ...` interpret
    coordinates the same way.
    """
    a = 0 if x1 is None else max(0, min(ctx.cols - 1, int(x1)))
    b = 0 if y1 is None else max(0, min(ctx.rows - 1, int(y1)))
    c = (ctx.cols - 1) if x2 is None else max(0, min(ctx.cols - 1, int(x2)))
    d = (ctx.rows - 1) if y2 is None else max(0, min(ctx.rows - 1, int(y2)))
    if a > c:
        a, c = c, a
    if b > d:
        b, d = d, b
    return a, b, c, d


# ──────────────────────────────────────────────────────────────────────────
#  Variant + mask helpers — shared by the scatter-family generators
# ──────────────────────────────────────────────────────────────────────────
#
# Two quality knobs the stochastic placers (scatter / cluster /
# density-falloff) share. Both default OFF (blank-string params) so a
# generator called without them streams exactly as before.
#
#   VARIANTS — instead of stamping one (slot, sub) everywhere, pick a sub
#   per placement from a weighted set. Turns a field of identical sprites
#   into a believable mix (bush A/B/C, three rock shapes, …).
#
#   MASKING — skip tiles that already hold something you don't want to
#   cover. Reads the live `ctx.parsed` layer grid, so "keep trees out of
#   the lake" = avoid_layer=land avoid_slots=<water slots>, and "keep the
#   roads clear" = avoid_layer=structs (blank avoid_slots = any content).


def _parse_weighted_subs(spec: str) -> list[tuple[int, float]]:
    """Parse the `subs` param into a [(sub, weight), …] list.

    Comma-separated subs, each optionally `sub:weight`:
      ""            → []                      (no variants — use single `sub`)
      "1,2,3"       → [(1,1.0),(2,1.0),(3,1.0)]   (equal weight)
      "1:5,2:2,3:1" → [(1,5.0),(2,2.0),(3,1.0)]   (weighted)

    Enforces the 1-based sub rule (sub=0 renders nothing) and rejects
    non-positive weights — raises ValueError so the route reports a clear
    GENERATOR_FAILED instead of silently producing invisible scatter.
    """
    spec = (spec or "").strip()
    if not spec:
        return []
    out: list[tuple[int, float]] = []
    for tok in spec.split(","):
        tok = tok.strip()
        if not tok:
            continue
        if ":" in tok:
            s_str, w_str = tok.split(":", 1)
            sub = int(s_str.strip())
            weight = float(w_str.strip())
        else:
            sub = int(tok)
            weight = 1.0
        if sub < 1:
            raise ValueError(
                f"sub variant must be >= 1 (1-based; sub=0 renders nothing); got {sub}"
            )
        if weight <= 0:
            raise ValueError(f"sub variant weight must be > 0; got {weight}")
        out.append((sub, weight))
    return out


def _make_sub_picker(rng: random.Random, default_sub: int, subs_spec: str):
    """Return a zero-arg callable yielding the sub for one placement.

    No variants → always returns `default_sub` AND draws nothing from
    `rng`, so back-compat seed streams are byte-identical. With variants
    → weighted-picks from `rng` per call (same seed + params ⇒ same
    sequence)."""
    variants = _parse_weighted_subs(subs_spec)
    if not variants:
        return lambda: default_sub
    subs = [s for s, _ in variants]
    weights = [w for _, w in variants]
    return lambda: rng.choices(subs, weights=weights, k=1)[0]


def _resolve_corpus_subs(params: dict, layer: str, slot: int, explicit: str) -> str:
    """Weighted `subs` spec for `_make_sub_picker`, sourced from the distilled
    corpus when `corpus_source` + `biome` are set and no explicit `subs` was
    given. Explicit `subs` always wins; missing corpus data → '' (so the
    picker falls back to the single `sub`). The import is lazy + guarded so a
    missing/corrupt corpus file can never break a generator run."""
    explicit = (explicit or "").strip()
    if explicit:
        return explicit
    source = str(params.get("corpus_source", "") or "").strip().lower()
    biome = str(params.get("biome", "") or "").strip().lower()
    if not (source and biome):
        return ""
    try:
        from mercwizard_core.mapforge import corpus
        return corpus.resolve_subs(source, biome, layer, int(slot))
    except Exception:
        return ""


def _parse_int_csv(spec: str) -> list[int]:
    """Parse a comma-separated int list ("12, 13" → [12, 13]). Blank → []."""
    spec = (spec or "").strip()
    if not spec:
        return []
    return [int(tok.strip()) for tok in spec.split(",") if tok.strip()]


def _make_mask_predicate(ctx: GeneratorContext, avoid_layer: str, avoid_slots: str):
    """Build an `is_masked(x, y) -> bool` predicate from the live parsed
    grid, or return None when masking is off.

    - `avoid_layer` blank → None (no masking).
    - `avoid_layer` set but not a real layer → ValueError (fail fast on a
      typo, same policy as `_validate_layer`).
    - `avoid_layer` valid but absent from `ctx.parsed` (e.g. a minimal
      test context with no layer grids) → None — nothing known to avoid.
    - `avoid_slots` blank → mask any tile with ANY entry on the layer.
    - `avoid_slots` set → mask only tiles holding one of those slots.

    Reads `ctx.parsed[avoid_layer][y*cols + x]`, a list of (slot, sub)
    tuples (see routes/mapforge.py::_apply_single_edit). The generator
    writes to its TARGET layer, not the avoid layer, so this reads stable
    pre-existing data even though the route applies ops live.
    """
    avoid_layer = (avoid_layer or "").strip()
    if not avoid_layer:
        return None
    _validate_layer(avoid_layer)
    layer_data = ctx.parsed.get(avoid_layer)
    if not isinstance(layer_data, list):
        return None
    avoid = set(_parse_int_csv(avoid_slots))
    cols = ctx.cols
    n = len(layer_data)

    def is_masked(x: int, y: int) -> bool:
        gridno = y * cols + x
        if not (0 <= gridno < n):
            return False
        entries = layer_data[gridno]
        if not entries:
            return False
        if not avoid:
            return True
        return any(int(e[0]) in avoid for e in entries)

    return is_masked


# Off-map border inset, in tiles. The playable diamond is the inscribed diamond
# minus this border ring. 10 reproduces Headless_Compiler's in_engine_diamond
# (x+y ∈ [90,230], |x−y| ≤ 70) on a 160-wide sector.
_PLAYABLE_BORDER = 10


def _make_playable_predicate(ctx: GeneratorContext, enabled: bool):
    """Return `is_playable(x, y) -> bool` for the iso playable diamond, or None
    when clipping is disabled (None = no clip, every tile allowed).

    Tile (x,y) renders to screen ((x−y), (x+y)); the screen-aligned playable
    battlefield is therefore a diamond in tile space. Equivalent to
    Headless_Compiler's `in_engine_diamond` generalized to the session's
    dimensions: |（x+y) − map_center_sum| ≤ R and |(x−y) − map_center_dif| ≤ R,
    with R = min(cols, rows)/2 − border. Tiles failing it are off-map border."""
    if not enabled:
        return None
    cx = ctx.cols / 2.0
    cy = ctx.rows / 2.0
    r = min(cx, cy) - _PLAYABLE_BORDER
    if r <= 0:
        return None
    sum_c = cx + cy
    dif_c = cx - cy

    def is_playable(x: int, y: int) -> bool:
        return abs((x + y) - sum_c) <= r and abs((x - y) - dif_c) <= r

    return is_playable


# ──────────────────────────────────────────────────────────────────────────
#  ScatterGenerator — Poisson-disk-ish random scatter
# ──────────────────────────────────────────────────────────────────────────


class ScatterGenerator(Generator):
    """Place `count` copies of one (slot, sub) at random positions in a
    region, respecting a minimum spacing constraint.

    Use case: scatter 200 small bushes across the playable area, or
    rocks on a beach, or any "natural-feeling distribution of one
    object type". The min-distance constraint prevents overlapping
    stacks; lowering it produces denser scatter.

    Algorithm: rejection sampling — pick a random (x, y) in the
    region, check Chebyshev (L∞) distance to all placed points,
    accept if ≥ min_distance. Repeats until `count` placed OR until
    max_attempts × count rejections, whichever first. Bails out
    gracefully if the region is too dense to fit `count` — emits a
    `phase=warn` event with the actual count placed.

    The op is `add` (vegetation overlays existing terrain rather than
    replacing it). Pre-existing tiles on the layer stay intact.
    """
    name = "scatter"
    label = "Scatter (random with min-distance)"
    description = (
        "Place N copies of one (slot, sub) randomly across a region, "
        "respecting a minimum-distance spacing. Good for vegetation, "
        "rocks, debris, etc. Supports weighted sub variants + tile "
        "masking (keep out of water/roads)."
    )
    params = [
        Param(name="count", type="int", default=100,
              description="How many to place", min=1, max=10000),
        Param(name="min_distance", type="int", default=2,
              description="Min Chebyshev distance between scatters (tiles)", min=1, max=80),
        Param(name="layer", type="str", default="objs",
              description="Target layer (objs for small vegetation, structs for trees)"),
        Param(name="slot", type="int", default=0,
              description="STI slot (0-based)", min=0, max=255),
        Param(name="sub", type="int", default=1,
              description="STI sub-index — 1-BASED (sub=1 = first frame). sub=0 renders nothing.",
              min=1, max=255),
        Param(name="seed", type="int", default=42,
              description="RNG seed — same seed + params = same scatter"),
        Param(name="region_x1", type="int", default=0,
              description="Region top-left X (0 = whole map)", min=0, max=255),
        Param(name="region_y1", type="int", default=0,
              description="Region top-left Y (0 = whole map)", min=0, max=255),
        Param(name="region_x2", type="int", default=0,
              description="Region bottom-right X (0 = whole map)", min=0, max=255),
        Param(name="region_y2", type="int", default=0,
              description="Region bottom-right Y (0 = whole map)", min=0, max=255),
        Param(name="subs", type="str", default="",
              description=(
                  "Optional sub VARIANTS — comma list, each optionally sub:weight. "
                  "e.g. '1,2,3' (equal) or '1:5,2:2,3:1' (weighted). Overrides `sub` "
                  "when set; picked per placement so the scatter isn't one repeated "
                  "sprite. No spaces."
              )),
        Param(name="avoid_layer", type="str", default="",
              description=(
                  "Optional MASK layer — skip tiles that already hold content on this "
                  "layer (land/objs/shadows/structs/roofs/onroofs). Blank = no masking."
              )),
        Param(name="avoid_slots", type="str", default="",
              description=(
                  "With avoid_layer set: comma list of slots to avoid (e.g. water "
                  "tiles). Blank = avoid ANY content on avoid_layer."
              )),
    ] + _CORPUS_PARAMS + [_PLAYABLE_PARAM]

    def iter_ops(self, ctx: GeneratorContext, params: dict) -> Iterator[dict]:
        layer = _validate_layer(str(params.get("layer", "objs")))
        slot = int(params.get("slot", 0))
        sub = int(params.get("sub", 0))
        count = int(params.get("count", 100))
        min_dist = int(params.get("min_distance", 2))
        seed = int(params.get("seed", 42))
        rng = random.Random(seed)
        pick_sub = _make_sub_picker(
            rng, sub,
            _resolve_corpus_subs(params, layer, slot, str(params.get("subs", "") or "")),
        )
        mask = _make_mask_predicate(
            ctx,
            str(params.get("avoid_layer", "") or ""),
            str(params.get("avoid_slots", "") or ""),
        )
        playable = _make_playable_predicate(ctx, bool(params.get("clip_to_playable", True)))

        # If region params are 0/0/0/0 (the default), use the whole map.
        rx1 = int(params.get("region_x1", 0))
        ry1 = int(params.get("region_y1", 0))
        rx2 = int(params.get("region_x2", 0))
        ry2 = int(params.get("region_y2", 0))
        if rx1 == 0 and ry1 == 0 and rx2 == 0 and ry2 == 0:
            x1, y1, x2, y2 = 0, 0, ctx.cols - 1, ctx.rows - 1
        else:
            x1, y1, x2, y2 = _normalize_region(ctx, rx1, ry1, rx2, ry2)

        yield {
            "phase": "scatter",
            "status": "start",
            "label": (
                f"Scattering up to {count} tiles of slot {slot} sub {sub} "
                f"in ({x1},{y1})→({x2},{y2}) on {layer}, min_distance={min_dist}, seed={seed}…"
            ),
            # Upper bound — rejection sampling may place fewer than
            # `count` if the region is too dense for the spacing constraint.
            # The progress bar will stop short if so; the done event's
            # label surfaces the actual count.
            "total": count,
        }

        placed: list[tuple[int, int]] = []
        attempts = 0
        max_attempts = count * 30  # generous — rejections-per-success grows with density
        while len(placed) < count and attempts < max_attempts:
            attempts += 1
            x = rng.randint(x1, x2)
            y = rng.randint(y1, y2)
            # Masked tile (e.g. water/road) — reject like a spacing miss.
            # The max_attempts guard bounds the loop if the mask leaves
            # little room, and the done-phase reports the actual count.
            if mask is not None and mask(x, y):
                continue
            if playable is not None and not playable(x, y):
                continue
            # Chebyshev distance check against everything placed so far.
            # O(N²) overall; fine for N ≤ ~1000 in a 160×160 grid where
            # each iteration is a few dozen CPU instructions.
            ok = True
            for (px, py) in placed:
                if abs(px - x) < min_dist and abs(py - y) < min_dist:
                    ok = False
                    break
            if not ok:
                continue
            placed.append((x, y))
            yield {
                "x": x, "y": y,
                "op": "add",
                "layer": layer,
                "slot": slot,
                "sub": pick_sub(),
            }

        actually_placed = len(placed)
        if actually_placed < count:
            yield {
                "phase": "scatter",
                "status": "done",
                "label": (
                    f"Placed {actually_placed} of {count} requested — region too "
                    f"dense at min_distance={min_dist}. Lower min_distance or "
                    "widen region to fit more."
                ),
            }
        else:
            yield {
                "phase": "scatter",
                "status": "done",
                "label": f"Placed {actually_placed} scatters.",
            }


# ──────────────────────────────────────────────────────────────────────────
#  ClusterScatterGenerator — N clusters of k objects each
# ──────────────────────────────────────────────────────────────────────────


class ClusterScatterGenerator(Generator):
    """Place `cluster_count` clusters at random centers across a region,
    each containing `objects_per_cluster` copies of one (slot, sub)
    distributed inside a circle of `cluster_radius` around the center.

    Use case: forest patches, rock piles, debris fields — anything
    where the spatial pattern is "groups, not uniform sprinkle". The
    procedural alternative to corpus-driven forest stamping.

    Cluster centers are placed via the same rejection-sampling
    approach as ScatterGenerator (min-distance = cluster_radius × 2
    so clusters don't overlap each other). Within each cluster,
    objects are placed at random offsets sampled from a uniform-in-
    circle distribution. Inner min-distance is 1 so objects within
    a cluster can sit on adjacent tiles.
    """
    name = "cluster"
    label = "Cluster scatter (forest patches, debris fields)"
    description = (
        "N clusters of k objects each, with random centers + "
        "uniform-in-circle distribution within each cluster. Procedural "
        "alternative to corpus-driven forest stamping. Supports weighted "
        "sub variants + tile masking (keep out of water/roads)."
    )
    params = [
        Param(name="cluster_count", type="int", default=5,
              description="How many clusters to create", min=1, max=200),
        Param(name="objects_per_cluster", type="int", default=12,
              description="Objects per cluster", min=1, max=500),
        Param(name="cluster_radius", type="int", default=4,
              description="Cluster spread radius (tiles)", min=1, max=40),
        Param(name="layer", type="str", default="objs",
              description="Target layer"),
        Param(name="slot", type="int", default=0,
              description="STI slot (0-based)", min=0, max=255),
        Param(name="sub", type="int", default=1,
              description="STI sub-index — 1-BASED (sub=1 = first frame). sub=0 renders nothing.",
              min=1, max=255),
        Param(name="seed", type="int", default=42, description="RNG seed"),
        Param(name="region_x1", type="int", default=0,
              description="Region corner 1 X (all four 0 = whole map)", min=0, max=255),
        Param(name="region_y1", type="int", default=0,
              description="Region corner 1 Y", min=0, max=255),
        Param(name="region_x2", type="int", default=0,
              description="Region corner 2 X", min=0, max=255),
        Param(name="region_y2", type="int", default=0,
              description="Region corner 2 Y", min=0, max=255),
        Param(name="subs", type="str", default="",
              description=(
                  "Optional sub VARIANTS — comma list, each optionally sub:weight. "
                  "e.g. '1,2,3' (equal) or '1:5,2:2,3:1' (weighted). Overrides `sub` "
                  "when set; picked per object so a cluster mixes shapes. No spaces."
              )),
        Param(name="avoid_layer", type="str", default="",
              description=(
                  "Optional MASK layer — skip tiles that already hold content on this "
                  "layer (land/objs/shadows/structs/roofs/onroofs). Blank = no masking."
              )),
        Param(name="avoid_slots", type="str", default="",
              description=(
                  "With avoid_layer set: comma list of slots to avoid (e.g. water "
                  "tiles). Blank = avoid ANY content on avoid_layer."
              )),
    ] + _CORPUS_PARAMS + [_PLAYABLE_PARAM]

    def iter_ops(self, ctx: GeneratorContext, params: dict) -> Iterator[dict]:
        layer = _validate_layer(str(params.get("layer", "objs")))
        slot = int(params.get("slot", 0))
        sub = int(params.get("sub", 1))
        cluster_count = int(params.get("cluster_count", 5))
        objects_per_cluster = int(params.get("objects_per_cluster", 12))
        radius = int(params.get("cluster_radius", 4))
        seed = int(params.get("seed", 42))
        rng = random.Random(seed)
        pick_sub = _make_sub_picker(
            rng, sub,
            _resolve_corpus_subs(params, layer, slot, str(params.get("subs", "") or "")),
        )
        mask = _make_mask_predicate(
            ctx,
            str(params.get("avoid_layer", "") or ""),
            str(params.get("avoid_slots", "") or ""),
        )
        playable = _make_playable_predicate(ctx, bool(params.get("clip_to_playable", True)))

        yield {
            "phase": "cluster",
            "status": "start",
            "label": (
                f"Placing {cluster_count} clusters × {objects_per_cluster} objects "
                f"(radius {radius}) on {layer}, seed={seed}…"
            ),
            # Upper bound — rejection sampling on cluster centers plus
            # in-cluster dedup may yield fewer than the full product.
            "total": cluster_count * objects_per_cluster,
        }

        # Optional region (same 0/0/0/0 = whole-map sentinel as scatter)
        # so "forest patch HERE" doesn't require post-hoc cleanup of a
        # whole-map spray.
        rx1 = int(params.get("region_x1", 0))
        ry1 = int(params.get("region_y1", 0))
        rx2 = int(params.get("region_x2", 0))
        ry2 = int(params.get("region_y2", 0))
        explicit_region = not (rx1 == 0 and ry1 == 0 and rx2 == 0 and ry2 == 0)
        if explicit_region:
            gx1, gy1, gx2, gy2 = _normalize_region(ctx, rx1, ry1, rx2, ry2)
        else:
            gx1, gy1, gx2, gy2 = 0, 0, ctx.cols - 1, ctx.rows - 1

        # Place cluster CENTERS with rejection sampling against each
        # other so clusters don't overlap. Center inset by `radius` so
        # the whole cluster stays inside the region/grid. A hand-drawn
        # region narrower than 2×radius collapses the center band to
        # its midline (the user clearly wants clusters THERE); a whole-
        # map run that can't fit the radius bails with an explanation.
        x_lo, x_hi = gx1 + radius, gx2 - radius
        y_lo, y_hi = gy1 + radius, gy2 - radius
        if x_hi < x_lo or y_hi < y_lo:
            if explicit_region:
                if x_hi < x_lo:
                    x_lo = x_hi = (gx1 + gx2) // 2
                if y_hi < y_lo:
                    y_lo = y_hi = (gy1 + gy2) // 2
            else:
                yield {
                    "phase": "cluster", "status": "done",
                    "label": f"cluster_radius={radius} too large for {ctx.cols}×{ctx.rows} grid.",
                }
                return

        centers: list[tuple[int, int]] = []
        center_min_dist = max(1, 2 * radius)  # so clusters don't collide
        attempts = 0
        while len(centers) < cluster_count and attempts < cluster_count * 100:
            attempts += 1
            cx = rng.randint(x_lo, x_hi)
            cy = rng.randint(y_lo, y_hi)
            if all(abs(px - cx) >= center_min_dist or abs(py - cy) >= center_min_dist
                   for (px, py) in centers):
                centers.append((cx, cy))

        # Per-cluster: sample objects_per_cluster offsets inside the
        # circle, dedupe so we don't stack on the same tile within one
        # cluster, emit one op per unique tile.
        total_placed = 0
        for ci, (cx, cy) in enumerate(centers):
            placed_in_cluster: set[tuple[int, int]] = set()
            inner_attempts = 0
            while (len(placed_in_cluster) < objects_per_cluster
                   and inner_attempts < objects_per_cluster * 20):
                inner_attempts += 1
                # Uniform-in-circle: sample r from sqrt(uniform) so the
                # density is even across the disk (without the sqrt,
                # points cluster toward the center).
                r = radius * math.sqrt(rng.random())
                theta = rng.uniform(0, 2 * math.pi)
                ox = cx + int(round(r * math.cos(theta)))
                oy = cy + int(round(r * math.sin(theta)))
                if not (0 <= ox < ctx.cols and 0 <= oy < ctx.rows):
                    continue
                tile = (ox, oy)
                if tile in placed_in_cluster:
                    continue
                if mask is not None and mask(ox, oy):
                    continue
                if playable is not None and not playable(ox, oy):
                    continue
                placed_in_cluster.add(tile)
                total_placed += 1
                yield {
                    "x": ox, "y": oy,
                    "op": "add",
                    "layer": layer,
                    "slot": slot,
                    "sub": pick_sub(),
                }

        yield {
            "phase": "cluster",
            "status": "done",
            "label": f"Placed {total_placed} objects across {len(centers)} clusters.",
        }


# ──────────────────────────────────────────────────────────────────────────
#  DensityFalloffGenerator — density drops with distance from a focal point
# ──────────────────────────────────────────────────────────────────────────


class DensityFalloffGenerator(Generator):
    """Place objects with probability that falls off linearly with
    distance from a focal point. Inside the falloff radius, every
    tile has a non-zero chance to get an object; outside, zero.

    Use case: dense forest near a river/road, sparse at the edges.
    A "natural-looking gradient" alternative to uniform scatter.

    Algorithm: iterate every tile inside the falloff radius (with
    bounding box optimization so we don't touch all 160×160 for a
    small focal area). For each, sample `random() < (1 - dist/radius)`;
    on success, emit an `add` op. Result is deterministic per (seed,
    params) tuple.
    """
    name = "density-falloff"
    label = "Density falloff (dense near focal, sparse at edges)"
    description = (
        "Place objects with probability that decays with distance from "
        "a focal point. Inside the radius every tile has a chance; "
        "outside, zero. Good for dense-near-X / sparse-far patterns. "
        "Supports weighted sub variants + tile masking (keep out of "
        "water/roads)."
    )
    params = [
        Param(name="center_x", type="int", default=80, description="Focal point X", min=0, max=255),
        Param(name="center_y", type="int", default=80, description="Focal point Y", min=0, max=255),
        Param(name="radius", type="int", default=30, description="Falloff radius (tiles)", min=1, max=200),
        Param(name="peak_density", type="float", default=0.5,
              description="P(place) at the focal point (decays to 0 at radius)",
              min=0.0, max=1.0),
        Param(name="layer", type="str", default="objs",
              description="Target layer"),
        Param(name="slot", type="int", default=0, description="STI slot (0-based)", min=0, max=255),
        Param(
            name="sub", type="int", default=1,
            description="STI sub-index — 1-BASED (sub=1 = first frame). sub=0 renders nothing.",
            min=1, max=255,
        ),
        Param(name="seed", type="int", default=42, description="RNG seed"),
        Param(name="subs", type="str", default="",
              description=(
                  "Optional sub VARIANTS — comma list, each optionally sub:weight. "
                  "e.g. '1,2,3' (equal) or '1:5,2:2,3:1' (weighted). Overrides `sub` "
                  "when set; picked per placement for a varied field. No spaces."
              )),
        Param(name="avoid_layer", type="str", default="",
              description=(
                  "Optional MASK layer — skip tiles that already hold content on this "
                  "layer (land/objs/shadows/structs/roofs/onroofs). Blank = no masking."
              )),
        Param(name="avoid_slots", type="str", default="",
              description=(
                  "With avoid_layer set: comma list of slots to avoid (e.g. water "
                  "tiles). Blank = avoid ANY content on avoid_layer."
              )),
    ] + _CORPUS_PARAMS + [_PLAYABLE_PARAM]

    def iter_ops(self, ctx: GeneratorContext, params: dict) -> Iterator[dict]:
        layer = _validate_layer(str(params.get("layer", "objs")))
        slot = int(params.get("slot", 0))
        sub = int(params.get("sub", 1))
        cx = int(params.get("center_x", 80))
        cy = int(params.get("center_y", 80))
        radius = int(params.get("radius", 30))
        peak = float(params.get("peak_density", 0.5))
        seed = int(params.get("seed", 42))
        rng = random.Random(seed)
        pick_sub = _make_sub_picker(
            rng, sub,
            _resolve_corpus_subs(params, layer, slot, str(params.get("subs", "") or "")),
        )
        mask = _make_mask_predicate(
            ctx,
            str(params.get("avoid_layer", "") or ""),
            str(params.get("avoid_slots", "") or ""),
        )
        playable = _make_playable_predicate(ctx, bool(params.get("clip_to_playable", True)))

        # Bounding box around the focal point — skip tiles obviously
        # outside the falloff disk so we don't waste time on 160² tile
        # checks when the radius is small.
        x_lo = max(0, cx - radius)
        x_hi = min(ctx.cols - 1, cx + radius)
        y_lo = max(0, cy - radius)
        y_hi = min(ctx.rows - 1, cy + radius)

        yield {
            "phase": "density",
            "status": "start",
            "label": (
                f"Density falloff from ({cx},{cy}) radius {radius} peak {peak:.2f} "
                f"on {layer} slot {slot} sub {sub}…"
            ),
            # Upper bound — every tile in the bbox is iterated; only a
            # fraction (proportional to peak × falloff curve) becomes a
            # placement. The bar will stop short of 100%; that's expected
            # for probabilistic generators.
            "total": (x_hi - x_lo + 1) * (y_hi - y_lo + 1),
        }

        placed = 0
        for y in range(y_lo, y_hi + 1):
            for x in range(x_lo, x_hi + 1):
                dx = x - cx
                dy = y - cy
                dist = math.hypot(dx, dy)
                if dist > radius:
                    continue
                if mask is not None and mask(x, y):
                    continue
                if playable is not None and not playable(x, y):
                    continue
                # Linear falloff: peak at center, 0 at radius.
                p = peak * (1 - dist / radius) if radius > 0 else peak
                if rng.random() < p:
                    placed += 1
                    yield {
                        "x": x, "y": y,
                        "op": "add",
                        "layer": layer,
                        "slot": slot,
                        "sub": pick_sub(),
                    }

        yield {
            "phase": "density",
            "status": "done",
            "label": f"Placed {placed} tiles within radius {radius} of ({cx},{cy}).",
        }


# ──────────────────────────────────────────────────────────────────────────
#  Registry
# ──────────────────────────────────────────────────────────────────────────


class AutoShadowGenerator(Generator):
    """Ensure every shadow-casting tile on the map has its paired shadow.

    Walks the structs/objs layers; for each tile entry whose slot has a
    paired shadow type in the engine's TileType table
    (`shadow_pairs.STRUCT_TO_SHADOW`), it adds the matching shadow entry
    on the shadows layer at the SAME gridno with the SAME sub-index —
    exactly what auto-pair-on-paint does per stroke, applied as a
    full-map sweep.

    This fixes maps authored without tree/bush/fence/vehicle shadows
    (e.g. A10, which shipped building shadows but no foliage shadows).
    Buildings are intentionally OUT OF SCOPE — they carry their shadow as
    a frame inside the building STI, a separate mechanism not driven by
    struct→shadow pairing, and maps that have buildings already ship
    those shadows.

    Safety properties:
      - Idempotent: skips any tile that already has the exact paired
        shadow (slot, sub), so re-running is a no-op and existing shadows
        (including building shadows) are never disturbed.
      - Tileset-aware: only adds a shadow if the active tileset actually
        defines that shadow STI (`ctx.slot_map`) AND the sub is within
        its frame count (`ctx.frame_count`). Skips otherwise instead of
        writing a dangling (slot, sub) the engine would read out of
        range. When that metadata is absent (bare context), it trusts the
        frame-aligned pairing convention and places anyway.
      - Position is automatic: the shadow STI's own sub-frame offset
        places it relative to the gridno — same as paint.
    """
    name = "autoshadow"
    label = "Auto-shadow (add missing shadows)"
    description = (
        "Add the paired shadow for every tree, bush, rock, fence, vehicle, "
        "door, and debris on the map that's missing one. Idempotent — "
        "won't touch tiles that already have their shadow. Buildings are "
        "skipped (they carry their own shadow)."
    )
    params = [
        Param(
            name="obstacles", type="bool", default=True,
            description="Trees, bushes, rocks, cliffs, debris (O-structs / full-structs).",
        ),
        Param(
            name="doors", type="bool", default=True,
            description="Door structures.",
        ),
        Param(
            name="vehicles_fences", type="bool", default=True,
            description="Vehicles and fences.",
        ),
        Param(
            name="source_layers", type="str", default="structs,objs",
            description=(
                "Comma list of layers to scan for shadow-casters "
                "(structs is where they normally live). No spaces."
            ),
        ),
    ]

    def iter_ops(self, ctx: GeneratorContext, params: dict) -> Iterator[dict]:
        # Which struct slots are in scope, per the category toggles.
        in_scope: set[int] = set()
        if bool(params.get("obstacles", True)):
            in_scope |= shadow_pairs.OBSTACLE_STRUCTS
        if bool(params.get("doors", True)):
            in_scope |= shadow_pairs.DOOR_STRUCTS
        if bool(params.get("vehicles_fences", True)):
            in_scope |= shadow_pairs.VEHICLE_FENCE_STRUCTS
        pairs = {s: sh for s, sh in shadow_pairs.STRUCT_TO_SHADOW.items()
                 if s in in_scope}

        raw_layers = str(params.get("source_layers", "structs,objs"))
        source_layers: list[str] = []
        for tok in raw_layers.split(","):
            tok = tok.strip()
            if not tok:
                continue
            if tok not in ALL_LAYERS:
                yield {"phase": "error", "status": "done",
                       "label": f"unknown source layer {tok!r}; valid: {', '.join(ALL_LAYERS)}"}
                return
            source_layers.append(tok)

        if not pairs or not source_layers:
            yield {"phase": "autoshadow", "status": "done",
                   "label": "Nothing to do — no categories or source layers selected."}
            return

        # Snapshot existing shadow entries so we never duplicate one.
        existing: set[tuple[int, int, int]] = set()
        for gn, tile in enumerate(ctx.parsed.get("shadows", []) or []):
            for entry in tile:
                if len(entry) >= 2:
                    existing.add((gn, entry[0], entry[1]))

        # Build the plan (one pass), validating against the tileset.
        cols = ctx.cols
        plan: list[tuple[int, int, int, int]] = []   # (x, y, shadow_slot, sub)
        planned: set[tuple[int, int, int]] = set()    # (gn, shadow_slot, sub)
        skipped_missing_sti = 0
        skipped_out_of_range = 0
        for layer in source_layers:
            for gn, tile in enumerate(ctx.parsed.get(layer, []) or []):
                for entry in tile:
                    if len(entry) < 2:
                        continue
                    slot, sub = entry[0], entry[1]
                    sh = pairs.get(slot)
                    if sh is None:
                        continue
                    # Tileset must actually define this shadow STI.
                    if ctx.slot_map is not None and not ctx.slot_map.get(sh):
                        skipped_missing_sti += 1
                        continue
                    # Sub must be within the shadow STI's frame range.
                    if ctx.frame_count is not None:
                        fc = ctx.frame_count(sh)
                        if fc and not (1 <= sub <= fc):
                            skipped_out_of_range += 1
                            continue
                    key = (gn, sh, sub)
                    if key in existing or key in planned:
                        continue
                    planned.add(key)
                    y, x = divmod(gn, cols)
                    plan.append((x, y, sh, sub))

        if not plan:
            note = ""
            if skipped_missing_sti or skipped_out_of_range:
                note = (f" (skipped {skipped_missing_sti} with no shadow STI in "
                        f"this tileset, {skipped_out_of_range} out of frame range)")
            yield {"phase": "autoshadow", "status": "done",
                   "label": f"No missing shadows — every shadow-caster already has one.{note}"}
            return

        yield {"phase": "autoshadow", "status": "start",
               "label": f"Adding {len(plan)} missing shadow tiles…",
               "total": len(plan)}

        # Emit top-to-bottom so the canvas fills in iso order.
        plan.sort(key=lambda t: (t[1], t[0]))
        for (x, y, sh, sub) in plan:
            yield {"x": x, "y": y, "op": "add", "layer": "shadows",
                   "slot": sh, "sub": sub}

        done = f"Added {len(plan)} shadow tiles."
        if skipped_missing_sti or skipped_out_of_range:
            done += (f" Skipped {skipped_missing_sti} with no shadow STI in this "
                     f"tileset, {skipped_out_of_range} out of frame range.")
        yield {"phase": "autoshadow", "status": "done", "label": done}


# ──────────────────────────────────────────────────────────────────────────
#  BuildingStampGenerator — corpus-driven rectangular building
# ──────────────────────────────────────────────────────────────────────────


_BUILDING_POS9 = ("NW", "N", "NE", "W", "Interior", "E", "SW", "S", "SE")


def _merge_subs_specs(specs) -> str:
    """Merge several 'sub:weight,…' specs into one summed spec."""
    agg: dict[int, int] = {}
    for spec in specs:
        for tok in (spec or "").split(","):
            tok = tok.strip()
            if not tok:
                continue
            s, sep, w = tok.partition(":")
            try:
                si = int(s)
                wi = int(w) if sep else 1
            except ValueError:
                continue
            if si >= 1 and wi > 0:
                agg[si] = agg.get(si, 0) + wi
    return ",".join(f"{s}:{w}" for s, w in agg.items())


def _dominant_door(doors: dict) -> tuple[Optional[int], str]:
    """Pick the most-used door slot + its weighted sub spec from a corpus
    door table. (None, '') when the table has no doors."""
    by_slot = (doors or {}).get("by_slot", {}) or {}
    if not by_slot:
        return None, ""
    slot = max(by_slot, key=lambda s: sum(int(v) for v in by_slot[s].values()))
    spec = ",".join(
        f"{sub}:{w}" for sub, w in by_slot[slot].items()
        if int(sub) >= 1 and int(w) > 0
    )
    return int(slot), spec


def _choose_door_tile(rng: random.Random, doors: dict,
                      x0: int, y0: int, x1: int, y1: int) -> tuple[int, int]:
    """Pick a door tile on a weighted-random perimeter edge (corners excluded
    when the edge is long enough), using the corpus per-edge door frequency."""
    by_edge = {e: int(c) for e, c in (doors or {}).get("by_edge", {}).items()
               if e in ("N", "S", "E", "W") and int(c) > 0}
    if by_edge:
        edges = list(by_edge)
        edge = rng.choices(edges, weights=[by_edge[e] for e in edges], k=1)[0]
    else:
        edge = "S"
    if edge in ("N", "S"):
        yy = y0 if edge == "N" else y1
        xs = list(range(x0 + 1, x1)) or [(x0 + x1) // 2]
        return rng.choice(xs), yy
    xx = x0 if edge == "W" else x1
    ys = list(range(y0 + 1, y1)) or [(y0 + y1) // 2]
    return xx, rng.choice(ys)


class BuildingStampGenerator(Generator):
    """Stamp one rectangular building whose wall / roof / door subframes are
    drawn from the distilled corpus for the chosen source + biome.

    The footprint (size + anchor) is user-controlled; everything inside is
    corpus-driven: the dominant wall slot and roof slot for the biome, the
    per-corner subframe distribution mappers actually used (so the SE corner
    gets its corner-cap variant, the N row its wall-top variant, etc.), and a
    door on a weighted-random edge. Perimeter tiles get a wall (`place`),
    positions that carried a roof in the corpus get a roof, interior tiles get
    a room id.

    This is the "variant-fill" philosophy applied to buildings: the generator
    owns the STRUCTURE (rectangle, 9-position classes, one door), the corpus
    owns the LOOK (which slot + which subframe per position). It does NOT
    reproduce vanilla's dual-struct SE corner or roof-overhang W column — it's
    a clean, readable building, not a byte-for-byte vanilla clone.

    Subs are clamped to the active tileset's frame count when the route wires
    `ctx.frame_count`, so a corpus sub past the live STI's range is skipped
    rather than written out of range.
    """
    name = "building"
    label = "Building stamp (corpus-driven walls/roof/door)"
    description = (
        "Stamp a rectangular building whose wall/roof/door subframes come "
        "from real maps of the chosen corpus source + biome. You set the "
        "footprint; the corpus picks the slot + subframe per corner. One door "
        "on a weighted-random edge; interior gets a room id."
    )
    params = [
        Param(name="x", type="int", default=40,
              description="Top-left X of the footprint", min=0, max=255),
        Param(name="y", type="int", default=40,
              description="Top-left Y of the footprint", min=0, max=255),
        Param(name="width", type="int", default=7,
              description="Footprint width (tiles)", min=3, max=40),
        Param(name="height", type="int", default=6,
              description="Footprint height (tiles)", min=3, max=40),
        Param(name="corpus_source", type="str", default="combined",
              description="Corpus source: stock/redux/combined — drives wall/roof/door slot + subframe per biome."),
        Param(name="biome", type="str", default="urban",
              description=f"Biome ({_BIOME_CHOICES}). Building patterns differ a lot by biome."),
        Param(name="room_id", type="int", default=1,
              description="Room id assigned to interior tiles", min=0, max=255),
        Param(name="seed", type="int", default=42,
              description="RNG seed — same seed + params = same building"),
        _PLAYABLE_PARAM,
    ]

    def iter_ops(self, ctx: GeneratorContext, params: dict) -> Iterator[dict]:
        x0 = max(0, min(ctx.cols - 1, int(params.get("x", 40))))
        y0 = max(0, min(ctx.rows - 1, int(params.get("y", 40))))
        w = max(3, int(params.get("width", 7)))
        h = max(3, int(params.get("height", 6)))
        x1 = min(ctx.cols - 1, x0 + w - 1)
        y1 = min(ctx.rows - 1, y0 + h - 1)
        W, H = x1 - x0 + 1, y1 - y0 + 1
        if W < 3 or H < 3:
            yield {"phase": "error", "status": "done",
                   "label": f"Footprint too small after clamping to the grid ({W}×{H}); need ≥3×3."}
            return

        source = str(params.get("corpus_source", "combined") or "combined").strip().lower()
        biome = str(params.get("biome", "urban") or "urban").strip().lower()
        room_id = int(params.get("room_id", 1))
        rng = random.Random(int(params.get("seed", 42)))

        try:
            from mercwizard_core.mapforge import corpus
        except Exception:
            corpus = None
        table = corpus.get_building_table(source, biome) if corpus else None
        if not table:
            yield {"phase": "error", "status": "done",
                   "label": (f"No building corpus for source={source!r} biome={biome!r}. "
                             "Try corpus_source=combined or a biome with buildings (urban/farm/temperate).")}
            return

        wall_slot = corpus.building_dominant_slot(table, 36, 39) or 36
        roof_slot = corpus.building_dominant_slot(table, 64, 67, kind="roofs") or 64
        door_slot, door_spec = _dominant_door(corpus.building_doors(table))
        door_tile = _choose_door_tile(rng, corpus.building_doors(table), x0, y0, x1, y1)
        door_pick = _make_sub_picker(rng, 1, door_spec)

        # Wall fallback: union of all positions' wall subs so a position the
        # corpus left empty (e.g. pattern-A W column) still closes the wall.
        wall_fallback = _merge_subs_specs(
            corpus.building_position_subs(table, p, wall_slot, "structs")
            for p in _BUILDING_POS9
        )
        wall_pickers: dict[str, Any] = {}
        roof_pickers: dict[str, Any] = {}
        for p in _BUILDING_POS9:
            wspec = corpus.building_position_subs(table, p, wall_slot, "structs") or wall_fallback
            wall_pickers[p] = _make_sub_picker(rng, 1, wspec) if wspec else None
            rspec = corpus.building_position_subs(table, p, roof_slot, "roofs")
            roof_pickers[p] = _make_sub_picker(rng, 1, rspec) if rspec else None

        def clamp(slot: int, sub: int) -> Optional[int]:
            if ctx.frame_count is not None:
                fc = ctx.frame_count(slot)
                if fc and not (1 <= sub <= fc):
                    return None
            return sub

        def position_of(x: int, y: int) -> str:
            nx, ex = x == x0, x == x1
            ny, sy = y == y0, y == y1
            if ny and nx: return "NW"
            if ny and ex: return "NE"
            if sy and nx: return "SW"
            if sy and ex: return "SE"
            if ny: return "N"
            if sy: return "S"
            if nx: return "W"
            if ex: return "E"
            return "Interior"

        playable = _make_playable_predicate(ctx, bool(params.get("clip_to_playable", True)))

        yield {"phase": "building", "status": "start",
               "label": (f"Stamping {W}×{H} building at ({x0},{y0}) — {biome}/{source}, "
                         f"wall slot {wall_slot}, roof slot {roof_slot}…"),
               "total": W * H}

        walls = roofs = 0
        door_placed = False
        for y in range(y0, y1 + 1):
            for x in range(x0, x1 + 1):
                if playable is not None and not playable(x, y):
                    continue
                pos = position_of(x, y)
                perim = pos != "Interior"
                if perim and (x, y) == door_tile and door_slot is not None:
                    ds = clamp(door_slot, door_pick())
                    if ds is not None:
                        yield {"x": x, "y": y, "op": "place", "layer": "structs",
                               "slot": door_slot, "sub": ds}
                        door_placed = True
                elif perim and wall_pickers[pos] is not None:
                    ws = clamp(wall_slot, wall_pickers[pos]())
                    if ws is not None:
                        yield {"x": x, "y": y, "op": "place", "layer": "structs",
                               "slot": wall_slot, "sub": ws}
                        walls += 1
                if roof_pickers[pos] is not None:
                    rs = clamp(roof_slot, roof_pickers[pos]())
                    if rs is not None:
                        yield {"x": x, "y": y, "op": "place", "layer": "roofs",
                               "slot": roof_slot, "sub": rs}
                        roofs += 1
                if not perim:
                    yield {"x": x, "y": y, "op": "set_room", "room_id": room_id}

        door_note = f"door at {door_tile}" if door_placed else "no door (no corpus door data)"
        yield {"phase": "building", "status": "done",
               "label": f"Built {W}×{H}: {walls} walls, {roofs} roofs, {door_note}."}


# The engine's one terrain-raise unit (worlddef.h:53). Engine-authored
# heights are exclusively multiples of this, max 3 raises (0/80/160/240).
WORLD_CLIFF_HEIGHT = 80

# ── Cliff-face sprites (R2, reworked R3 against vanilla run-walks) ─────────
#
# PROVENANCE — empirical corpus scan + per-run walk, cross-checked against
# the editor C++:
#
#   Scan: sidecar/.venv/Scripts/python.exe tools/scan_cliff_faces.py \
#             --installs-dir "C:/Jagged Alliance 2" --top 12
#   Run-walk: scratch/clifftest/analyze_runs.py A6 F5 G5 A8 (2026-06-10) —
#   walks every cliff RUN in those vanilla maps in gridno order and prints
#   anchor→anchor deltas, per-sub chains, base companions, land textures.
#
#   * LAYER + PAIRING: every cliff anchor in real maps is a DUAL entry —
#     structs (slot 10, FIRSTCLIFF) + objs (slot 9, FIRSTCLIFFHANG), SAME
#     sub, SAME gridno (on the canonical install 1059/1059 perfectly
#     paired). This is exactly what the in-game editor does: PasteBanks
#     (Editor/edit_sys.cpp:672) calls AddStructToHead(FIRSTCLIFF…) then
#     AddObjectToHead(FIRSTCLIFFHANG…). The FIRSTCLIFFSHADOW (slot 11) add
#     there is commented OUT (0.1% corpus-wide noise) → no shadow entry.
#   * ADD vs PLACE: AddStructToHead/AddObjectToHead are additive and the
#     corpus tiles keep their land texture under the cliff → op = "add".
#     Vanilla even stacks multiple cliff anchors on ONE gridno (A6 has 12
#     such tiles), so a corner sharing a tile with a face piece is fine.
#   * CHAINING (the run-walk truth that fixed the clumpy v1 ring):
#     straight faces chain with a ONE-TILE OVERLAP, not butt-jointed.
#       E face: subs 5/6 alternate down the column at Δ(0,+4) — each piece
#         covers 5 rows (CliffOffsetData (0,-4)..(0,0)), so consecutive
#         pieces overlap one row. Observed: every E-run in A6/F5/G5/A8.
#       S face: subs 7/8 chain east along the row at Δ(+4,0) — each covers
#         cols x-4..x-1 (rows y-1,y), 1-col overlap via the anchor walk.
#         Observed: F5 (47,35)→(51,35)→(55,35)→(59,35) = 8,8,8,7.
#       Runs that jog use the elbows (3/4 at Δ(-3,+3)) and diagonals
#         (1/2/15/16 at Δ(+6,+6)) — those are for escarpment mode, not the
#         axis-aligned plateau ring.
#   * CORNER ROLES (corpus role histogram, down-sides = face direction):
#       SE (down=ES) → sub 7 anchored AT the corner: art covers the 4 cols
#         west of it; the corner column's own face comes from the E-face
#         bottom piece anchored on the same tile (vanilla multi-anchor).
#       SW (down=SW/ESW) → sub 8 anchored AT the corner: its footprint
#         (-4..-1,-1),(-4,0) lies WEST of the plateau on low ground — the
#         vanilla run-end taper wrapping around the corner (F5 47,35).
#       NE (down=NE) → sub 13 anchored AT the corner — doubles as the
#         N-face chain start.
#       NW: ZERO anchors corpus-wide (hidden behind the raised terrain in
#         iso view) → deliberately no NW piece, exactly like vanilla.
#   * BACK LIPS (mostly hidden in iso view, but vanilla places them):
#       N face → sub 13 (covers x-2..x on the edge row, plus an off-edge
#         wrap NW that hangs over the back lip), exact-tiled at stride 3.
#       W face → sub 11 (covers y-3..y on the edge column plus an off-edge
#         wrap), exact-tiled at stride 4.
#   * BASE DEBRIS: measured object density on ground tiles within 2 of a
#     cliff run vs the rest of the map — NO consistent enrichment (ratios
#     0.3-2.3, sign flips per map). The rubble seen at vanilla cliff bases
#     is BAKED INTO the cliff sprites. → no debris pass; don't invent one.
#   * LAND: raised/ground tiles share the same texture families (TEX4/TEX3
#     dominate both in A6) → no land retexture under or atop the plateau.
#   * ENGINE CROSS-CHECK: the in-game editor's RaiseWorldLand
#     (edit_sys.cpp:1363) recomputes heights from these sprites' RAISE
#     flags (CliffRaiseData, edit_sys.cpp:1265): E-face subs 5/6 are
#     RAISE_LAND_START, W-face sub 11 RAISE_LAND_END, SW sub 8 carries the
#     row-end END — matching the role assignment.

CLIFF_STRUCT_SLOT = 10   # FIRSTCLIFF      → structs layer
CLIFF_HANG_SLOT = 9      # FIRSTCLIFFHANG  → objs layer (paired entry)

# Per-sub multi-tile footprints, verbatim from CliffOffsetData
# (Editor/edit_sys.cpp:1210). (dx, dy) offsets relative to the anchor.
# The ring grammar's strides derive from these spans; tests assert against
# them so a stride can never drift from the engine's footprint table.
CLIFF_FOOTPRINT: dict[int, list[tuple[int, int]]] = {
    1:  [(-6, -7), (-5, -7), (-6, -6), (-4, -6), (-3, -5), (-2, -4),
         (-1, -3), (-1, -2), (-1, -1), (-1, 0), (0, 0)],
    2:  [(-7, -6), (-7, -5), (-6, -4), (-5, -3), (-4, -2), (-3, -1),
         (-2, -1), (-1, -1), (0, -1), (0, 0)],
    3:  [(3, -3), (2, -2), (0, -1), (1, -1), (2, -1), (0, 0)],
    4:  [(2, -3), (3, -3), (0, -2), (1, -2), (0, -1), (0, 0)],
    5:  [(0, -4), (0, -3), (0, -2), (0, -1), (0, 0)],
    6:  [(0, -4), (1, -3), (1, -2), (0, -1), (1, -1), (0, 0), (1, 0)],
    7:  [(-4, -1), (-3, -1), (-2, -1), (-1, -1),
         (-4, 0), (-3, 0), (-2, 0), (-1, 0)],
    8:  [(-4, -1), (-3, -1), (-2, -1), (-1, -1), (-4, 0), (0, 0)],
    9:  [(2, -3), (3, -3), (1, -2), (2, -2), (0, -1), (1, -1), (0, 0)],
    10: [(-4, 0), (-3, 0), (-2, 0), (-1, 0), (0, 0)],
    11: [(-2, -5), (-2, -4), (-1, -4), (0, -3), (0, -2), (0, -1), (0, 0)],
    12: [(-2, -2), (-2, -1), (-1, 0), (0, 0)],
    13: [(-5, -2), (-4, -2), (-3, -1), (-2, 0), (-1, 0), (0, 0)],
    14: [(-2, -2), (-1, -2), (0, -1), (0, 0)],
    15: [(-6, -7), (-5, -7), (-6, -6), (-4, -6), (-3, -5), (-2, -4),
         (-1, -3), (-1, -2), (-1, -1), (-1, 0)],
    16: [(-7, -6), (-7, -5), (-6, -4), (-5, -3), (-4, -2), (-3, -1),
         (-2, -1), (-1, -1), (0, -1), (0, 0)],
    17: [(0, -4), (0, -3), (0, -2), (0, -1), (0, 0)],
}

CLIFF_FACE_LUT: dict[str, dict] = {
    # subs: [(sub, corpus weight), …] — seeded weighted pick per anchor.
    # stride: anchor-to-anchor distance along the edge axis, from the
    #   vanilla run-walk (pieces span 5 → 1-tile overlap per joint).
    "edge_E":    {"subs": [(6, 1811), (5, 1663)], "stride": 4},
    "edge_S":    {"subs": [(7, 919), (8, 609)],   "stride": 4},
    "corner_SE": {"subs": [(7, 1682)]},
    "corner_SW": {"subs": [(8, 1192)]},
    # edge_N / edge_W / corner_NE / corner_NW: intentionally ABSENT.
    # Measured on A6/F5/G5/A8: vanilla leaves down=N boundary tiles
    # 76-98% bare and down=W tiles 62-84% bare (vs E faces ~85-89%
    # covered) — the N/W sides face away from the iso camera. The
    # corpus sub-11/13 entries live in diagonal zigzag SEQUENCES
    # (15→11→12, 16→13→14), not straight runs; chained straight they
    # render as disjoint pillars/claws (verified on iter_1b render).
}


def _face_chain(hi: int, cap: int) -> list[int]:
    """Anchor positions for one straight face run, walking from the
    corner end `hi` toward the far end: first anchor AT `hi`, last AT
    `cap` (flush — art never overshoots the plateau), joints as close
    to the vanilla stride 4 as possible and NEVER wider. Every joint in
    every vanilla run is Δ4 (pieces span 5 → 1-tile overlap): the piece
    art has ragged ends DESIGNED to tuck under the neighbor, so a Δ5
    butt joint reads as a visible crack (seen on iteration 2). When the
    span doesn't divide by 4 the slack is spread evenly across joints
    (Δ3/Δ2 overdraw is harmless — vanilla multi-anchors tiles too)."""
    if cap >= hi:
        return [hi]
    span = hi - cap
    njoints = -(-span // 4)          # ceil — max Δ4 per joint
    base, extra = divmod(span, njoints)
    ys, y = [hi], hi
    for i in range(njoints):
        y -= base + (1 if i < extra else 0)
        ys.append(y)
    return ys


class BankGenerator(Generator):
    """Raise a rectangular region into a uniform CLIFF PLATEAU by editing
    per-tile HEIGHTS (not sprites), in the engine's native 80-unit raise
    steps — i.e. exactly what vanilla maps do with terrain height.

    Engine reality (verified against the 1.13 C++ source, 2026-06-10):
    - ANY height difference between adjacent tiles is hard-impassable.
      Pathing blocks it at three sites (CompileTileMovementCosts
      worlddef.cpp:880, WantToTraverse PATHAI.cpp:2011, legacy
      FindBestPath PATHAI.cpp:2815), and 1.13 additionally compiles
      raised tiles as OFF-MAP (GridNoOnWalkableWorldTile, Isometric
      Utils.cpp:1221). No climb mechanism crosses a terrain-height
      delta — raised terrain is decorative / route-blocking scenery,
      exactly like vanilla cliffs. (Real cliff climbing = STATUS.md
      Phase 3e: resurrecting the engine's dead CLIMB_CLIFF animation.)
    - Heights that aren't multiples of 80 load, but mis-stack render
      layers (IGNORE_WORLD_HEIGHT quantizes to 80s, renderworld.cpp:1454).
    - The in-game Map Editor recomputes ALL heights from cliff-face
      sprites on save (RaiseWorldLand zeroes sHeight) — resaving there
      wipes any height not backed by cliff art.

    This replaces the v1 "stepped terrace" design, whose edge band just
    produced concentric impassable rings around an unreachable plateau —
    the engine has no climbable slope for the steps to soften.

    The plateau is ORTHOGONAL by construction (a rectangle has no
    diagonal edges → no diagonal cliffs), which makes cliff-face art
    DETERMINISTIC: every border position has a known role (N/S/E/W edge,
    NE/SE/SW corner), so a fixed role→(layer, slot, sub) LUT suffices —
    no autotiler needed. After the set_height ops, the generator emits
    the visible cliff-face sprites around the border per CLIFF_FACE_LUT
    (provenance comment above it): each anchor is the vanilla dual entry
    — structs (slot 10 FIRSTCLIFF) + objs (slot 9 FIRSTCLIFFHANG), same
    sub, same tile, op "add" — placed ON the raised border tile. Cliff
    pieces are multi-tile sprites (CLIFF_FOOTPRINT) CHAINED the way the
    vanilla run-walks show (see `_iter_face_anchors`): E/S faces overlap
    one tile per joint, corners anchor AT the corner tiles (SE sub 7,
    SW sub 8 taper, NE sub 13), N/W back lips exact-tile, and the NW
    corner gets no piece (vanilla places none — it's hidden behind the
    raised terrain in iso view). Rects under 5×5 get no face art (smaller
    than the smallest vanilla cliff piece). `place_cliff_faces=false`
    restores the old heights-only behavior; `levels=0` (flatten) never
    emits faces.

    CORRECTNESS ORACLE: the in-game editor's RaiseWorldLand
    (Editor/edit_sys.cpp:1363) recomputes ALL heights from cliff-face
    sprites on save — a generated plateau is right iff its heights AND
    faces survive an in-game-editor resave. The LUT's RAISE-flag
    cross-check (see provenance) is designed for exactly that; verify
    in-game on first use.

    No object-count guard is needed: set_height touches the fixed header
    region, and the handful of border face entries are nowhere near the
    layer caps.
    """
    name = "bank"
    label = "Cliff plateau / bank (heights + cliff faces)"
    description = (
        "Raise a rectangle into a uniform cliff plateau in the engine's "
        "native 80-unit steps (levels × 80; 0 flattens back to ground) "
        "and dress its border with the vanilla cliff-face sprites "
        "(FIRSTCLIFF struct + FIRSTCLIFFHANG obj pairs, corpus-derived LUT). "
        "Mercs cannot cross ANY height difference — this is route-blocking "
        "scenery, like vanilla cliffs."
    )
    params = [
        Param(name="x1", type="int", default=0, description="One corner X", min=0, max=255),
        Param(name="y1", type="int", default=0, description="One corner Y", min=0, max=255),
        Param(name="x2", type="int", default=0, description="Other corner X", min=0, max=255),
        Param(name="y2", type="int", default=0, description="Other corner Y", min=0, max=255),
        Param(name="levels", type="int", default=1,
              description="Cliff raises (×80 height units each, the engine's "
                          "native step). 0 = flatten back to ground level.",
              min=0, max=3),
        Param(name="place_cliff_faces", type="bool", default=True,
              description="Dress the plateau border with visible cliff-face "
                          "sprites (vanilla FIRSTCLIFF/FIRSTCLIFFHANG pairs). "
                          "Off = heights only (invisible ledge)."),
        Param(name="seed", type="int", default=42,
              description="RNG seed for the cliff-face variant picks — same "
                          "seed + params = same faces."),
    ] + [_PLAYABLE_PARAM_OFF]

    def _iter_face_anchors(
        self, x1: int, y1: int, x2: int, y2: int, rng: random.Random,
    ) -> Iterator[tuple[int, int, int]]:
        """Yield (x, y, sub) cliff-face anchors around the border of the
        raised rect — the vanilla CHAIN grammar from the A6/F5/G5/A8
        run-walks (see CLIFF_FACE_LUT provenance):

          E face: bottom piece anchored AT the SE corner, chained north
            via `_face_chain` (stride 4 = 1-row overlap, top piece flush
            at y1 — pieces cover rows y-4..y).
          S face: sub 7 anchored AT the SE corner (covers the 4 cols west;
            the corner column's face comes from the E-face bottom piece on
            the same tile — vanilla multi-anchors gridnos), chained west
            via `_face_chain`; sub 8 anchored AT the SW corner wraps the
            face onto the low ground west (the vanilla run-end taper) and
            covers col x1.
          N + W edges and both their corners: NOTHING — vanilla leaves the
            away-facing edges bare (see CLIFF_FACE_LUT provenance).
        """
        def pick(role: str) -> int:
            pool = CLIFF_FACE_LUT[role]["subs"]
            return rng.choices([s for s, _ in pool],
                               weights=[w for _, w in pool], k=1)[0]

        # ── E face (x2 column, the visually dominant right side) ──
        # Pieces cover rows y-4..y: corner anchor y2, flush cap y1+4.
        # (A sub-14 NE top cap per the vanilla 16→13→14 sequence was
        # A/B-rendered on 2026-06-11 and looked WORSE — it notches the
        # face top; that piece only blends on a diagonal step. The flush
        # 5/6 ending matches how vanilla straight runs terminate.)
        for y in _face_chain(y2, y1 + 4):
            yield x2, y, pick("edge_E")

        # ── S face (y2 row, the visually dominant left side) ──
        # Pieces cover cols x-4..x-1 on rows y2-1..y2: corner anchor x2
        # (forced sub 7 per the corpus SE role), flush cap x1+4 (whose
        # piece covers x1..x1+3, reaching the corner column — vanilla
        # F5 run 3 anchors exactly so: sub8@47 corner, next piece @51).
        for x in _face_chain(x2, min(x1 + 4, x2)):
            yield x, y2, 7 if x == x2 else pick("edge_S")
        # SW corner taper (also the only piece covering col x1's face).
        yield x1, y2, 8

    def iter_ops(self, ctx: GeneratorContext, params: dict) -> Iterator[dict]:
        levels = max(0, min(3, int(params.get("levels", 1))))
        target = levels * WORLD_CLIFF_HEIGHT
        place_faces = bool(params.get("place_cliff_faces", True))
        rng = random.Random(int(params.get("seed", 42)))
        playable = _make_playable_predicate(
            ctx, bool(params.get("clip_to_playable", False)))

        x1 = max(0, min(ctx.cols - 1, int(params.get("x1", 0))))
        y1 = max(0, min(ctx.rows - 1, int(params.get("y1", 0))))
        x2 = max(0, min(ctx.cols - 1, int(params.get("x2", 0))))
        y2 = max(0, min(ctx.rows - 1, int(params.get("y2", 0))))
        if x1 > x2:
            x1, x2 = x2, x1
        if y1 > y2:
            y1, y2 = y2, y1

        # Honest progress total: count the tiles that will actually emit
        # (clip_to_playable can skip part of the rect).
        coords = [
            (x, y)
            for y in range(y1, y2 + 1)
            for x in range(x1, x2 + 1)
            if playable is None or playable(x, y)
        ]
        total = len(coords)

        # Cliff-face anchors (computed up front for the honest total).
        # levels=0 is a FLATTEN — no faces; the smallest vanilla face
        # pieces span 5 tiles (CLIFF_FOOTPRINT subs 5/6/7/8), so a rect
        # under 5×5 can't carry a coherent ring — no art, like vanilla.
        anchors: list[tuple[int, int, int]] = []
        if place_faces and levels > 0 and x2 - x1 >= 4 and y2 - y1 >= 4:
            anchors = [
                (x, y, sub)
                for (x, y, sub) in self._iter_face_anchors(x1, y1, x2, y2, rng)
                if playable is None or playable(x, y)
            ]
        total += 2 * len(anchors)   # each anchor = structs + objs entry

        yield {
            "phase": "bank",
            "status": "start",
            "label": (
                f"Raising ({x1},{y1})→({x2},{y2}) to height {target} "
                f"({levels} cliff level{'s' if levels != 1 else ''}) — "
                f"{len(coords)} tiles, {len(anchors)} cliff-face anchors…"
            ),
            "total": total,
        }

        for x, y in coords:
            yield {"x": x, "y": y, "op": "set_height", "height": target}

        # Border face art — the vanilla dual entry per anchor (PasteBanks:
        # struct FIRSTCLIFF + object FIRSTCLIFFHANG, same sub, same tile).
        for x, y, sub in anchors:
            yield {"x": x, "y": y, "op": "add", "layer": "structs",
                   "slot": CLIFF_STRUCT_SLOT, "sub": sub}
            yield {"x": x, "y": y, "op": "add", "layer": "objs",
                   "slot": CLIFF_HANG_SLOT, "sub": sub}

        if anchors:
            face_note = f"{len(anchors)} cliff-face anchors placed."
        elif levels == 0:
            face_note = "no cliff faces (flatten)."
        elif not place_faces:
            face_note = "no cliff faces (disabled)."
        else:
            face_note = ("no cliff faces (rect under 5×5 — smaller than "
                         "the smallest vanilla cliff piece).")
        yield {"phase": "bank", "status": "done",
               "label": (
                   f"Plateau done — {len(coords)} tiles at height {target}; "
                   f"{face_note} "
                   "Raised terrain is impassable scenery (no engine climb). "
                   "Oracle: heights+faces must survive an in-game-editor "
                   "RaiseWorldLand resave."
               )}


REGISTRY: dict[str, Generator] = {
    g.name: g for g in [
        WipeGenerator(),
        FillLayerGenerator(),
        RectangleGenerator(),
        ScatterGenerator(),
        ClusterScatterGenerator(),
        DensityFalloffGenerator(),
        # AutoShadowGenerator retired 2026-05-31: the renderer now overlays
        # these buddy shadows (effectiveShadowEntries) and the engine re-adds
        # them at load (HAS_SHADOW_BUDDY), so baking them only doubled in-game.
        # Class kept for reference; no longer offered to users.
        BuildingStampGenerator(),
        BankGenerator(),
    ]
}


def get(name: str) -> Optional[Generator]:
    """Look up a generator by name. Returns None if not registered."""
    return REGISTRY.get(name)


def list_all() -> list[Generator]:
    """All registered generators in stable name order."""
    return [REGISTRY[k] for k in sorted(REGISTRY.keys())]
