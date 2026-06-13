/**
 * IsoRenderer — engine-faithful client-side iso renderer for JA2 .dat
 * sectors. TypeScript port of `Headless_Compiler/iso_renderer.py`.
 *
 * Why port to the client?
 *   The server-rendered PNG round-trip (parse → PIL composite → encode →
 *   HTTP → blob decode) was 200–2000 ms per edit. Drawing to <canvas>
 *   via ctx.drawImage(atlas, ...) is ~5–20 ms even for a full sector,
 *   so paint strokes / layer toggles / pan-zoom feel instant.
 *
 * Engine semantics encoded here (do NOT change without re-checking the
 * source-of-truth references in the Headless_Compiler memory files):
 *
 * 1. **Per-iso-row, level-major within row for STRUCT+ROOF+ONROOF**
 *    (renderworld.cpp:1340). Land / Objs / Shadows are each a separate
 *    whole-region pass. Pass 4 groups struct+roof+onroof and iterates
 *    levels WITHIN each iso row — otherwise back roofs overdraw front
 *    walls (the "stepped roof" bug).
 *
 * 2. **WALL_HEIGHT=50 Y-lift for roofs and onroofs** (renderworld.cpp:1830,1842).
 *    Roof STIs are authored at floor level — the engine lifts them at
 *    render time so they sit on top of walls.
 *
 * 3. **1-based sub indices** (tiledef.cpp:1024). A stored sub=14 means
 *    atlas key (slot, 14) — the atlas manifest already speaks the same
 *    1-based language so no -1 adjustment is needed here; just look up
 *    by (slot, sub) directly.
 *
 * 4. **STI palette index 0 = transparent**. The atlas PNG already ships
 *    RGBA with alpha=0 for those pixels, so drawImage handles it.
 *
 * 5. **Shadow sprites = darken-blend at 50% alpha**. We pre-bake a
 *    "darken atlas" from the main atlas (black with original-alpha/2)
 *    on first frame and use ctx.drawImage(darkenAtlas, ...) for the
 *    shadow pass.
 *
 * 6. **Iso projection** (WORLD_TILE_X=40, WORLD_TILE_Y=20):
 *      screen_x = (tile_x - tile_y) * 20
 *      screen_y = (tile_x + tile_y) * 10
 *    The (screen_x, screen_y) point is the SOUTH apex of the diamond.
 *
 * 7. **No smoothing LUT** — gbWallTileLUT is editor-only; engine renders
 *    stored subs verbatim. The renderer does the same.
 *
 * Reference Python: iso_renderer.py (separate Headless_Compiler dev project).
 */

import type {
  AtlasCell,
  AtlasManifest,
  JsdFootprint,
  LayerEntry,
  ParsedSector,
  TileInspection,
} from "./mapforge";
import { STRUCT_TO_SHADOW } from "./jaSlotPairs";

// Iso constants — must match engine WORLD_TILE_X / WORLD_TILE_Y.
export const TILE_W = 40;
export const TILE_H = 20;
const TILE_HW = TILE_W / 2;
const TILE_HH = TILE_H / 2;

// Shared empty result so the common (no struct/obj) tile path allocates
// nothing. Never mutated by callers — they only read the returned array.
// Entries are [slot, sub] number tuples (the runtime layer shape), not the
// richer `LayerEntry` object used by the inspector.
const EMPTY_ENTRIES: number[][] = [];

/** The effective shadow-layer entries for a tile: the stored shadows PLUS the
 * engine's auto-added buddy shadows, so the editor render matches in-game.
 *
 * JA2 never stores foliage/fence/vehicle/door shadows in the .dat — the engine
 * re-adds them at struct-placement time (TileDat.cpp `gForwardShadowBuddys` →
 * HAS_SHADOW_BUDDY; worldman.cpp:1156 `AddShadowToHead`). This mirrors that for
 * the renderer: for each struct/obj whose slot has a paired shadow type, add
 * the paired shadow (same sub-index) — UNLESS the tile is "indoors"
 * (worlddef.cpp:197,218: GridNoIndoors ⟺ FloorAtGridNo ⟺ a FIRSTFLOOR..
 * FOURTHFLOOR (60-63) entry on the LAND layer). Also requires the active
 * tileset to register the shadow STI frame (`cellMap` has it — exactly what
 * makes the engine's `sBuddyNum` resolve) and skips any shadow already present
 * so explicit/baked shadows don't double.
 *
 * PURE + EPHEMERAL: never mutates `parsed`, so these synthetic shadows are a
 * render-only overlay and are never written to the .dat (no double shadow on
 * reload). Mirror of `shadow_pairs.py` on the sidecar. */
export function effectiveShadowEntries(
  parsed: ParsedSector,
  gn: number,
  cellMap: ReadonlyMap<number, unknown>,
): number[][] {
  const stored = parsed.shadows[gn];
  const structs = parsed.structs[gn];
  const objs = parsed.objs[gn];
  const hasStructs = !!structs && structs.length > 0;
  const hasObjs = !!objs && objs.length > 0;
  // Fast path: no shadow-casting struct/obj here → nothing to add.
  if (!hasStructs && !hasObjs) return stored ?? EMPTY_ENTRIES;
  // Indoors suppression (engine GridNoIndoors): any FIRSTFLOOR..FOURTHFLOOR
  // (60-63) on the LAND layer flips the tile to "indoors" and the engine
  // skips the buddy shadow.
  const land = parsed.land[gn];
  if (land) {
    for (const e of land) {
      const s = e[0] as number;
      if (s >= 60 && s <= 63) return stored ?? EMPTY_ENTRIES;
    }
  }
  const base = stored ?? EMPTY_ENTRIES;
  let out: number[][] | null = null;
  const have = new Set<number>();
  for (const e of base) {
    if (e.length >= 2) have.add(((e[0] as number) << 16) | ((e[1] as number) & 0xffff));
  }
  for (const src of [structs, objs]) {
    if (!src) continue;
    for (const e of src) {
      if (e.length < 2) continue;
      const paired = STRUCT_TO_SHADOW.get(e[0] as number);
      if (paired === undefined) continue;
      const sub = e[1] as number;
      const key = (paired << 16) | (sub & 0xffff);
      if (have.has(key)) continue;       // already on the tile (explicit/dup)
      if (!cellMap.has(key)) continue;   // tileset doesn't register this shadow STI
      have.add(key);
      if (!out) out = base.slice();
      out.push([paired, sub]);
    }
  }
  return out ?? base;
}

// renderworld.cpp:1830 STATIC_ROOF and :1842 STATIC_ONROOF subtract
// WALL_HEIGHT from sYPos so roofs sit on top of walls instead of inside
// the building footprint.
export const WALL_HEIGHT = 50;

// Per-layer Y lift in pixels. Mirrors LAYER_Y_LIFT in the Python.
const LAYER_Y_LIFT: Record<LayerName, number> = {
  land: 0,
  objs: 0,
  shadows: 0,
  structs: 0,
  roofs: WALL_HEIGHT,
  onroofs: WALL_HEIGHT,
};

export type LayerName =
  "land" | "objs" | "shadows" | "structs" | "roofs" | "onroofs";

/** Phases reported via the `onProgress` callback. Frontend uses these
 * to drive a progress bar with phase-specific labels. Order matches
 * the typical loading sequence; consumers should expect repeated
 * calls per phase (0-100% within each).
 *
 * `rendering` is the final phase — entered after IsoRenderer.create
 * resolves but BEFORE the canvas's first paint actually happens. The
 * caller advances to 100% only once the paint useEffect has run, so
 * the progress bar covers the React-mount + first-paint gap instead
 * of completing while the screen is still blank. */
export type ProgressPhase =
  | "building-atlas"
  | "fetching-atlas"
  | "fetching-manifest"
  | "fetching-parsed"
  | "decoding-atlas"
  | "baking-shadow-atlas"
  | "rendering"
  | "ready";

export const PROGRESS_PHASE_LABELS: Record<ProgressPhase, string> = {
  "building-atlas":       "Building tileset atlas",
  "fetching-atlas":       "Downloading tileset atlas",
  "fetching-manifest":    "Loading atlas manifest",
  "fetching-parsed":      "Loading sector data",
  "decoding-atlas":       "Decoding atlas image",
  "baking-shadow-atlas":  "Building shadow atlas",
  "rendering":            "Painting canvas",
  "ready":                "Ready",
};

/** Phase ordering for the overall percent on the progress bar.
 * Weights sum to 100 and reflect approximate relative duration:
 *   - building-atlas: dominant on COLD cache (1-5 s loading STIs +
 *     encoding PNG). On warm cache this phase is a single 'cache-hit'
 *     event in <50 ms.
 *   - atlas fetch: ~150 ms over loopback (PNG already cached on disk
 *     after building-atlas finishes)
 *   - parsed fetch: secondary (JSON ~1-2 MB)
 *   - decode + bake: fixed work per atlas size (~50-150 ms)
 *   - rendering: React mount + first canvas paint (~50-200 ms)
 *   - manifest: tiny
 */
export const PHASE_WEIGHTS: Record<ProgressPhase, number> = {
  "building-atlas":      40,
  "fetching-atlas":      15,
  "fetching-parsed":     18,
  "decoding-atlas":       7,
  "baking-shadow-atlas": 10,
  "rendering":            6,
  "fetching-manifest":    4,
  "ready":                0,
};

export const ALL_LAYERS: LayerName[] = [
  "land", "objs", "shadows", "structs", "roofs", "onroofs",
];

/** Region-tile shape accepted by `renderRegionToCanvas`. Structurally
 * compatible with BOTH mapClipboard's ClipTile and the building
 * library's BuildingLibraryTile (dx/dy offsets from the region's
 * top-left + per-layer [slot, sub] entry lists). */
export interface GhostRegionTile {
  dx: number;
  dy: number;
  layers: Record<LayerName, number[][]>;
}

/** Result of `renderRegionToCanvas`: the rendered sprite canvas plus
 * the raw-pixel offset of its top-left corner RELATIVE to tile (0,0)'s
 * `tileToPixRaw` position. To place the ghost so its (0,0) tile lands
 * exactly on an anchor tile, position the canvas at
 * `tileToCanvasPixel(anchor) + (originX, originY)`. */
export interface RegionRender {
  canvas: HTMLCanvasElement;
  originX: number;
  originY: number;
}

/** Iso-projection metadata shared with the SVG overlay so the existing
 * grid/highlight/label code keeps working unchanged. */
export interface RenderMeta {
  ixMin: number;
  iyMin: number;
  canvasW: number;
  canvasH: number;
  tileW: number;
  tileH: number;
}

export interface RenderOptions {
  /** When set, render this room with `ring` tiles of context around it.
   * Mirrors iso_renderer.py --room. */
  roomId?: number | null;
  /** Explicit tile bbox [x0, y0, x1, y1]. Overrides roomId. */
  bbox?: [number, number, number, number] | null;
  /** Padding around a room region (default 5, matches Python). */
  ring?: number;
  /** Layers to skip at render time. The engine's other ordering rules
   * still hold for the layers that ARE drawn. */
  skipLayers?: Set<LayerName>;
  /** Tile-coord set to tint as the "selected room" highlight. The SVG
   * overlay also draws this, but the canvas can include it pre-render
   * to match the Python renderer's `--no-highlight` semantics. */
  highlightTiles?: Set<string>;  // "x,y" keys
  /** Background color (defaults to the Python (60, 50, 40, 255) tan). */
  bgColor?: string;
}

/**
 * Drives one <canvas>. Holds atlas + parsed sector in memory and offers
 * a single `render()` method that the React component calls on every
 * relevant state change (edit / layer toggle / room change / pan / zoom
 * isn't a re-render — pan/zoom is done via CSS transform on the canvas
 * wrapper, same as the previous img-based approach).
 *
 * Construction is async because we need to wait for the atlas image to
 * decode + the darken atlas to be baked. Use `IsoRenderer.create()`
 * instead of `new IsoRenderer()`.
 */
export class IsoRenderer {
  // protected so IsoRendererGL can read these (it uploads `atlas` as a
  // GL texture; doesn't need `darkenAtlas` since shadow blending is in
  // the fragment shader, but we keep it on the base for the Canvas2D
  // fallback path's continued use).
  protected atlas: HTMLImageElement;
  /** Pre-baked atlas where each pixel is (0, 0, 0, src.alpha / 2). Used
   * for the shadow pass — drawImage with default source-over composites
   * a semi-transparent black silhouette, which matches the Python
   * `alpha_composite(dark, ...)` darken-blend. */
  protected darkenAtlas: HTMLCanvasElement;
  private cellMap: Map<number, AtlasCell>;
  private slotFilenames: Map<number, string>;
  private slotHasJsd: Map<number, boolean>;
  /** slot → JSD footprint recipe for slots whose JSD has more than
   * one tile. Lookup table for the multi-tile stamper. Slots not in
   * this map are single-tile and ignore the stamp path. */
  private slotJsdFootprint: Map<number, JsdFootprint>;
  private parsed: ParsedSector;

  // Last computed region anchor — exposed so MapForgeSector can mirror
  // the existing tileToCanvasPixel / imagePixelToTile maths.
  private meta: RenderMeta = {
    ixMin: 0, iyMin: 0, canvasW: 0, canvasH: 0,
    tileW: TILE_W, tileH: TILE_H,
  };

  // protected (not private) so IsoRendererGL can extend this class —
  // it reuses all the non-render state (cellMap, slotFilenames, undo
  // stack, parsed) and overrides render() to paint via WebGL2 instead
  // of Canvas2D. Same atlas image is shared (WebGL uploads it as a
  // texture; Canvas2D uses drawImage). See IsoRendererGL.ts.
  protected constructor(
    atlas: HTMLImageElement,
    darkenAtlas: HTMLCanvasElement,
    manifest: AtlasManifest,
    parsed: ParsedSector,
  ) {
    this.atlas = atlas;
    this.darkenAtlas = darkenAtlas;
    this.parsed = parsed;
    // (slot, sub) -> cell. Pack into one int key so the lookup is a
    // single Map.get per drawn sprite (no allocation per lookup). 16
    // bits each is plenty: slot < 256, sub typically < 100.
    this.cellMap = new Map();
    for (const c of manifest.cells) {
      this.cellMap.set((c.slot << 16) | (c.sub & 0xffff), c);
    }
    // Slot -> STI filename for the inspector. Manifest ships it as
    // `Record<number, string>` from JSON; cast keys back to numbers.
    this.slotFilenames = new Map();
    for (const [k, v] of Object.entries(manifest.slot_filenames)) {
      this.slotFilenames.set(Number(k), v);
    }
    // Slot -> has-JSD flag. Used by inspectTile so the inspector can
    // show "View JSD" buttons only for entries that actually have one.
    this.slotHasJsd = new Map();
    for (const [k, v] of Object.entries(manifest.slot_has_jsd ?? {})) {
      this.slotHasJsd.set(Number(k), !!v);
    }
    // Slot -> multi-tile stamp recipe. Only populated for slots whose
    // JSD has ubNumberOfTiles > 1; single-tile slots stay out so the
    // painter can detect "needs stamping" with a simple map lookup.
    this.slotJsdFootprint = new Map();
    for (const [k, v] of Object.entries(manifest.slot_jsd_footprint ?? {})) {
      this.slotJsdFootprint.set(Number(k), v);
    }
  }

  static async create(
    atlasUrl: string,
    manifest: AtlasManifest,
    parsed: ParsedSector,
    onProgress?: (phase: ProgressPhase, pct: number) => void,
  ): Promise<IsoRenderer> {
    const { atlas, darkenAtlas } = await IsoRenderer.loadAtlasState(
      atlasUrl, onProgress,
    );
    return new IsoRenderer(atlas, darkenAtlas, manifest, parsed);
  }

  /** Shared atlas-load helper. Acquires the atlas image + bakes the
   * shadow atlas with progress reporting. Protected static so
   * subclasses (IsoRendererGL) can reuse it without going through an
   * IsoRenderer instance (TypeScript's protected access rule blocks
   * reading instance fields on a parent-typed reference). */
  protected static async loadAtlasState(
    atlasUrl: string,
    onProgress?: (phase: ProgressPhase, pct: number) => void,
  ): Promise<{ atlas: HTMLImageElement; darkenAtlas: HTMLCanvasElement }> {
    onProgress?.("decoding-atlas", 0);
    const atlas = await loadImage(atlasUrl);
    onProgress?.("decoding-atlas", 100);
    onProgress?.("baking-shadow-atlas", 0);
    const darkenAtlas = bakeDarkenAtlas(atlas, (p) =>
      onProgress?.("baking-shadow-atlas", p));
    onProgress?.("baking-shadow-atlas", 100);
    return { atlas, darkenAtlas };
  }

  /** Swap the atlas + manifest WITHOUT touching the parsed dict or
   * the undo stack. Used after add-to-tileset: the backend wrote a
   * new STI into the tileset, the disk-cached atlas was invalidated,
   * and we need to pick up the new (slot, sub) cells. Recreating the
   * whole renderer would also drop the undo stack and require
   * refetching the parsed sector — wasteful when only the atlas
   * changed.
   *
   * The new atlas/manifest replace the in-memory atlas image, the
   * darken atlas (rebaked from scratch), the cellMap, and the
   * slotFilenames lookup. Everything else (parsed dict, undo stack,
   * meta) is preserved. */
  async replaceAtlas(
    atlasUrl: string,
    manifest: AtlasManifest,
    onProgress?: (phase: ProgressPhase, pct: number) => void,
  ): Promise<void> {
    onProgress?.("decoding-atlas", 0);
    const atlas = await loadImage(atlasUrl);
    onProgress?.("decoding-atlas", 100);
    onProgress?.("baking-shadow-atlas", 0);
    const darkenAtlas = bakeDarkenAtlas(atlas, (p) =>
      onProgress?.("baking-shadow-atlas", p));
    onProgress?.("baking-shadow-atlas", 100);
    this.atlas = atlas;
    this.darkenAtlas = darkenAtlas;
    this.cellMap = new Map();
    for (const c of manifest.cells) {
      this.cellMap.set((c.slot << 16) | (c.sub & 0xffff), c);
    }
    this.slotFilenames = new Map();
    for (const [k, v] of Object.entries(manifest.slot_filenames)) {
      this.slotFilenames.set(Number(k), v);
    }
    this.slotHasJsd = new Map();
    for (const [k, v] of Object.entries(manifest.slot_has_jsd ?? {})) {
      this.slotHasJsd.set(Number(k), !!v);
    }
    this.slotJsdFootprint = new Map();
    for (const [k, v] of Object.entries(manifest.slot_jsd_footprint ?? {})) {
      this.slotJsdFootprint.set(Number(k), v);
    }
  }

  /** Multi-tile stamp recipe for a slot, or null when it's a single-
   * tile slot (or has no JSD at all). Frontend painter looks this up
   * to decide whether to expand a click into a footprint of N tiles. */
  getFootprint(slot: number): JsdFootprint | null {
    return this.slotJsdFootprint.get(slot) ?? null;
  }

  // ─── Undo / stroke bookkeeping ─────────────────────────────────────
  /** Per-stroke staging area. Filled by `recordSnapshot` calls during a
   * paint stroke; committed to `undoStack` by `endStroke()`. Null when
   * no stroke is active. */
  private pendingStroke: UndoEntry | null = null;
  /** Tile-keys already snapshotted in the current stroke. Prevents the
   * mid-stroke snapshots from clobbering the first-touch one (the
   * snapshot we actually want for revert). */
  private strokeSeenKeys: Set<string> = new Set();
  private undoStack: UndoEntry[] = [];
  /** Redo history. Populated when `popUndo` reverts a stroke (the
   * pre-revert state is captured here) and consumed by `popRedo`. Cleared
   * by `endStroke` — a fresh edit invalidates the redo timeline (standard
   * undo/redo semantics). */
  private redoStack: UndoEntry[] = [];
  /** Monotonic counter bumped on EVERY content-changing history event
   * (committed stroke, undo, redo, rollback discard). Dirty tracking
   * compares this against the value captured at save time — unlike
   * stack DEPTH, it can't collide after save→undo→new-stroke (the
   * classic false-"Saved" stack-position bug) or pin at the 100-cap. */
  private editGeneration = 0;

  /** Capture the CURRENT state of every axis named in `entry` into a new
   * UndoEntry — the inverse of applying `entry`. Used to build the redo
   * mirror before an undo reverts (and the undo mirror before a redo), so
   * undo/redo are perfectly symmetric. */
  private captureMirror(entry: UndoEntry): UndoEntry {
    const mirror: UndoEntry = {
      snapshots: [], roomSnapshots: [], heightSnapshots: [], label: entry.label,
    };
    for (const s of entry.snapshots) {
      const gn = s.y * this.parsed.cols + s.x;
      const cur = this.parsed[s.layer][gn] ?? [];
      mirror.snapshots.push({
        x: s.x, y: s.y, layer: s.layer,
        entries: cur.map((e) => [e[0] as number, e[1] as number]),
      });
    }
    for (const r of entry.roomSnapshots) {
      const gn = r.y * this.parsed.cols + r.x;
      mirror.roomSnapshots.push({ x: r.x, y: r.y, roomId: this.parsed.rooms[gn] ?? 0 });
    }
    for (const h of entry.heightSnapshots) {
      const gn = h.y * this.parsed.cols + h.x;
      mirror.heightSnapshots.push({ x: h.x, y: h.y, height: this.parsed.heights[gn] ?? 0 });
    }
    return mirror;
  }

  /** Begin a paint stroke. Subsequent `recordSnapshot` calls staple
   * onto this stroke; `endStroke` commits to the undo history. Calling
   * `beginStroke` twice without an `endStroke` between flushes the
   * pending one first. */
  beginStroke(label: string): void {
    if (this.pendingStroke) this.endStroke();
    this.pendingStroke = { snapshots: [], roomSnapshots: [], heightSnapshots: [], label };
    this.strokeSeenKeys = new Set();
  }

  /** Snapshot a tile's layer entries BEFORE an edit touches it. Idempotent
   * within a stroke — only the first call per (x, y, layer) records. */
  recordSnapshot(x: number, y: number, layer: LayerName): void {
    if (!this.pendingStroke) return;
    const key = `${x},${y},${layer}`;
    if (this.strokeSeenKeys.has(key)) return;
    this.strokeSeenKeys.add(key);
    const gn = y * this.parsed.cols + x;
    const cur = this.parsed[layer][gn] ?? [];
    // Deep copy — pre-edit state must not alias the array we're about
    // to mutate.
    const copy: number[][] = cur.map((e) => [e[0] as number, e[1] as number]);
    this.pendingStroke.snapshots.push({ x, y, layer, entries: copy });
  }

  /** Snapshot a tile's room id (separate undo axis from layer entries). */
  recordRoomSnapshot(x: number, y: number): void {
    if (!this.pendingStroke) return;
    const key = `room:${x},${y}`;
    if (this.strokeSeenKeys.has(key)) return;
    this.strokeSeenKeys.add(key);
    const gn = y * this.parsed.cols + x;
    const prev = this.parsed.rooms[gn] ?? 0;
    this.pendingStroke.roomSnapshots.push({ x, y, roomId: prev });
  }

  /** Snapshot a tile's height (separate undo axis from layers + rooms). */
  recordHeightSnapshot(x: number, y: number): void {
    if (!this.pendingStroke) return;
    const key = `height:${x},${y}`;
    if (this.strokeSeenKeys.has(key)) return;
    this.strokeSeenKeys.add(key);
    const gn = y * this.parsed.cols + x;
    const prev = this.parsed.heights[gn] ?? 0;
    this.pendingStroke.heightSnapshots.push({ x, y, height: prev });
  }

  /** Commit the pending stroke. Empty strokes (no snapshots) are dropped
   * to avoid polluting the undo stack with no-ops. Caps the stack so a
   * runaway batch doesn't eat unbounded memory. */
  endStroke(): void {
    const s = this.pendingStroke;
    this.pendingStroke = null;
    this.strokeSeenKeys = new Set();
    if (!s) return;
    if (s.snapshots.length === 0 && s.roomSnapshots.length === 0
        && s.heightSnapshots.length === 0) return;
    this.undoStack.push(s);
    this.editGeneration++;
    // A fresh committed edit invalidates the redo timeline.
    this.redoStack = [];
    // Cap at 100 strokes. Each snapshot is small (a few ints) so this
    // is generous — the cap is really just to avoid stale memory after
    // a marathon editing session.
    while (this.undoStack.length > 100) this.undoStack.shift();
  }

  /** Pop the top undo entry without applying it. The caller is expected
   * to translate the snapshots into a SessionEdit batch and send it to
   * the backend, which is how the parsed dict actually gets reverted
   * — `applyLocalEdit` runs on the same set_entries op via the standard
   * path. Returns null when the stack is empty. */
  popUndo(): UndoEntry | null {
    const entry = this.undoStack.pop();
    if (!entry) return null;
    // Capture the current (post-edit) state of the touched axes as the
    // redo entry BEFORE the caller applies the pre-edit snapshots.
    this.redoStack.push(this.captureMirror(entry));
    while (this.redoStack.length > 100) this.redoStack.shift();
    this.editGeneration++;
    return entry;
  }

  /** Pop the top undo entry WITHOUT pushing a redo mirror. For rollback
   * paths only (e.g. a paste the backend rejected): the caller is about
   * to revert the stroke locally, and offering it on the Redo stack
   * would let Ctrl+Y replay a server-rejected edit — re-diverging the
   * local mirror from the authoritative session. */
  discardLastUndo(): UndoEntry | null {
    const entry = this.undoStack.pop();
    if (!entry) return null;
    this.editGeneration++;
    return entry;
  }

  /** Pop the top redo entry, capturing current state onto the undo stack
   * first so the redo can itself be undone. Symmetric with `popUndo`; the
   * caller applies the returned entry's snapshots via the same path. */
  popRedo(): UndoEntry | null {
    const entry = this.redoStack.pop();
    if (!entry) return null;
    this.undoStack.push(this.captureMirror(entry));
    while (this.undoStack.length > 100) this.undoStack.shift();
    this.editGeneration++;
    return entry;
  }

  /** Monotonic content-change counter (see `editGeneration`). Compare
   * against the value captured at save time for dirty tracking. */
  generation(): number {
    return this.editGeneration;
  }

  /** Inspect the top undo label without popping. Used by the UI to
   * label the Undo button ("Undo: Paint floor (12 tiles)"). */
  peekUndoLabel(): string | null {
    const top = this.undoStack[this.undoStack.length - 1];
    return top ? top.label : null;
  }

  undoDepth(): number {
    return this.undoStack.length;
  }

  redoDepth(): number {
    return this.redoStack.length;
  }

  /** Inspect the top redo label without popping (for the Redo button). */
  peekRedoLabel(): string | null {
    const top = this.redoStack[this.redoStack.length - 1];
    return top ? top.label : null;
  }

  /** All undo-stack labels, NEWEST first — for the History panel. Index 0
   * is the most recent stroke (one Ctrl+Z away). */
  listUndoLabels(): string[] {
    return this.undoStack.map((e) => e.label).reverse();
  }

  /** All redo-stack labels, NEXT-to-redo first — strokes undo has shelved. */
  listRedoLabels(): string[] {
    return this.redoStack.map((e) => e.label).reverse();
  }

  /** Drop the entire undo + redo history. Called on session change /
   * refetch so undo/redo don't try to apply into a stale parsed dict. */
  clearUndo(): void {
    this.undoStack = [];
    this.redoStack = [];
    this.pendingStroke = null;
    this.strokeSeenKeys = new Set();
  }

  /** Inspect one tile from the local parsed dict — no HTTP round-trip,
   * so the inspector reflects uncommitted local edits immediately.
   * Returns the same shape as the sidecar's /sessions/{sid}/tile
   * endpoint (TileInspection), so the existing inspector UI works
   * unchanged. */
  /** Render a single (slot, sub) sprite into the given 2D context.
   * Used by the tile inspector's per-entry preview thumbs (the
   * StiFrameImage HTTP path was N requests per inspect, this is
   * zero HTTP — atlas + cellMap are already in RAM). Returns false
   * when the (slot, sub) isn't in the cellMap; caller can fall back
   * to a "missing" placeholder.
   *
   * Centers the sprite inside the dst rect, preserving aspect (and
   * shrinks if the sprite is bigger than the box). */
  drawCellInto(
    ctx: CanvasRenderingContext2D,
    slot: number, sub: number,
    dstW: number, dstH: number,
  ): boolean {
    const cell = this.cellMap.get((slot << 16) | (sub & 0xffff));
    if (!cell) return false;
    // Scale to fit within dstW x dstH while preserving aspect ratio.
    const scale = Math.min(dstW / cell.w, dstH / cell.h, 1);
    const drawW = cell.w * scale;
    const drawH = cell.h * scale;
    const offX = (dstW - drawW) / 2;
    const offY = (dstH - drawH) / 2;
    ctx.clearRect(0, 0, dstW, dstH);
    ctx.imageSmoothingEnabled = false;
    ctx.drawImage(
      this.atlas,
      cell.x, cell.y, cell.w, cell.h,
      offX, offY, drawW, drawH,
    );
    return true;
  }

  /** Look up the (sub-count, has_jsd, sti_filename) triple for a slot
   * without touching the atlas image. Used by the subframe picker
   * to know how many sub thumbnails to render per slot. */
  getSlotInfo(slot: number): { subCount: number; hasJsd: boolean; filename: string | null } {
    // Count cells with this slot to derive the sub count. Linear over
    // cellMap but cellMap is small (~3-5k entries) so it's fine.
    let max = 0;
    for (const key of this.cellMap.keys()) {
      const cellSlot = key >>> 16;
      const cellSub = key & 0xffff;
      if (cellSlot === slot && cellSub > max) max = cellSub;
    }
    return {
      subCount: max,
      hasJsd: this.slotHasJsd.get(slot) ?? false,
      filename: this.slotFilenames.get(slot) ?? null,
    };
  }

  /** Return every sub-index actually present in the atlas for a slot,
   * sorted ascending. Sparse-aware — `getSlotInfo` only returns the
   * max sub, but a slot can have gaps (e.g. subs 1, 3, 5 with 2 and 4
   * missing). The subframe cycler + floating sub-strip both need the
   * concrete list so they can skip gaps cleanly instead of landing on
   * an empty (slot, sub) and drawing a "?" placeholder. */
  listValidSubs(slot: number): number[] {
    const subs: number[] = [];
    for (const key of this.cellMap.keys()) {
      const cellSlot = key >>> 16;
      const cellSub = key & 0xffff;
      if (cellSlot === slot) subs.push(cellSub);
    }
    subs.sort((a, b) => a - b);
    return subs;
  }

  inspectTile(x: number, y: number): TileInspection | null {
    const { cols, rows } = this.parsed;
    if (x < 0 || y < 0 || x >= cols || y >= rows) return null;
    const gridno = y * cols + x;
    const layers: Record<string, LayerEntry[]> = {};
    for (const layer of ALL_LAYERS) {
      const entries = this.parsed[layer][gridno] ?? [];
      layers[layer] = entries.map((e) => {
        const slot = (e[0] as number) ?? 0;
        const sub = (e[1] as number) ?? 0;
        return {
          slot,
          sub,
          sti_filename: this.slotFilenames.get(slot) ?? null,
          sti_frame_index_0based: sub - 1,
          has_jsd: this.slotHasJsd.get(slot) ?? false,
        };
      });
    }
    return {
      x, y, gridno,
      room_id: this.parsed.rooms[gridno] ?? 0,
      height: this.parsed.heights[gridno] ?? 0,
      world_flags: this.parsed.world_flags[gridno] ?? 0,
      layers,
    };
  }

  /** Replace the in-memory parsed sector wholesale. Called after a server
   * refetch (the rare resync path) or when switching sectors. */
  setParsed(parsed: ParsedSector): void {
    this.parsed = parsed;
  }

  getParsed(): ParsedSector {
    return this.parsed;
  }

  getMeta(): RenderMeta {
    return this.meta;
  }

  /** Apply one edit op to the LOCAL parsed dict. Mirrors the backend's
   * `_apply_single_edit` semantics so the canvas re-renders show the
   * change immediately, before the round-trip confirms. The backend is
   * still authoritative; on confirmation it returns updated session
   * info which we use to bump the edit counter. */
  applyLocalEdit(edit: LocalEdit): void {
    const { x, y, op, layer } = edit;
    if (x < 0 || y < 0 || x >= this.parsed.cols || y >= this.parsed.rows) return;
    const g = y * this.parsed.cols + x;
    if (op === "set_room") {
      if (edit.roomId !== undefined) this.parsed.rooms[g] = edit.roomId;
      return;
    }
    if (op === "set_height") {
      if (edit.height !== undefined) this.parsed.heights[g] = edit.height;
      return;
    }
    if (!layer) return;
    const arr = this.parsed[layer];
    const existing = arr[g] ?? [];
    if (op === "set_entries") {
      // Wholesale replace — used by undo to restore a snapshot.
      // `entries` is a list of [slot, sub] pairs (possibly empty).
      if (edit.entries !== undefined) {
        // Deep-copy so the undo entry's snapshot doesn't alias the
        // current parsed dict (the next mutation would corrupt the
        // snapshot otherwise).
        arr[g] = edit.entries.map((e) =>
          [e[0] as number, e[1] as number]);
      }
    } else if (op === "place") {
      // Replace any existing entry with the SAME slot, preserve
      // different-slot entries on this layer. The previous semantic
      // ("remove all entries + add the one") destroyed surface
      // decorations on land tiles — bug #64 in
      // MERC_FORGE_BUG_LIST.md: painting a new floor wiped the
      // companion slot-6 decoration on multi-entry land tiles. The
      // new "place_same_slot" behavior matches the user's intent:
      // "paint slot X sub Y here" means this tile gets slot X sub Y
      // (replacing any prior slot X), but slot Y / slot Z entries on
      // this same layer keep going.
      if (edit.slot !== undefined && edit.sub !== undefined) {
        const filtered = existing.filter((e) => e[0] !== edit.slot);
        arr[g] = [...filtered, [edit.slot, edit.sub]];
      }
    } else if (op === "add") {
      if (edit.slot !== undefined && edit.sub !== undefined) {
        arr[g] = [...existing, [edit.slot, edit.sub]];
      }
    } else if (op === "remove") {
      if (edit.entryIndex !== undefined) {
        arr[g] = existing.filter((_, i) => i !== edit.entryIndex);
      }
    } else if (op === "replace") {
      if (
        edit.entryIndex !== undefined
        && edit.slot !== undefined
        && edit.sub !== undefined
      ) {
        const next = [...existing];
        next[edit.entryIndex] = [edit.slot, edit.sub];
        arr[g] = next;
      }
    }
  }

  /** Compute the render meta WITHOUT drawing — useful for the SVG
   * overlay to position itself on the first frame before the canvas
   * has been painted. The result is also stored on `this` for later
   * lookups (e.g. click → tile inversion). */
  computeMeta(opts: RenderOptions): RenderMeta {
    const { rx0, ry0, rx1, ry1 } = this.resolveRegion(opts);
    // Canvas size: bounding iso rect of the tile region + overhang
    // margins for tall sprites. Mirrors iso_renderer.py exactly.
    const corners: [number, number][] = [
      this.tileToPixRaw(rx0, ry0),
      this.tileToPixRaw(rx0, ry1),
      this.tileToPixRaw(rx1, ry0),
      this.tileToPixRaw(rx1, ry1),
    ];
    const xs = corners.map((p) => p[0]);
    const ys = corners.map((p) => p[1]);
    const ixMin = Math.min(...xs) - 80;
    const ixMax = Math.max(...xs) + 80;
    const iyMin = Math.min(...ys) - 200;
    const iyMax = Math.max(...ys) + 60;
    this.meta = {
      ixMin,
      iyMin,
      canvasW: ixMax - ixMin,
      canvasH: iyMax - iyMin,
      tileW: TILE_W,
      tileH: TILE_H,
    };
    return this.meta;
  }

  /**
   * Composite the sector onto `canvas`. Resizes the canvas's backing
   * store to match the iso bounding rect. Returns the final RenderMeta
   * for the SVG overlay's use.
   *
   * Takes the canvas element (not the context) so subclasses can pick
   * their own context type — IsoRendererGL acquires a WebGL2 context
   * instead of Canvas2D. Same backing store, different drawing API.
   *
   * Performance budget: a full 160×160 sector takes ~10–25 ms on a
   * laptop CPU. A single-room render with ring=5 is sub-5 ms — way
   * inside the 16 ms per-frame budget for 60 fps.
   */
  render(canvas: HTMLCanvasElement, opts: RenderOptions): RenderMeta {
    const ctx = canvas.getContext("2d");
    if (!ctx) {
      throw new Error("IsoRenderer.render: canvas has no 2d context. " +
        "If a webgl2 context was already acquired on this canvas, " +
        "use IsoRendererGL instead.");
    }
    const meta = this.computeMeta(opts);
    const { rx0, ry0, rx1, ry1 } = this.resolveRegion(opts);
    const skip = opts.skipLayers ?? new Set<LayerName>();

    // Resize backing store if needed. Browsers no-op when dims match.
    if (
      canvas.width !== meta.canvasW
      || canvas.height !== meta.canvasH
    ) {
      canvas.width = meta.canvasW;
      canvas.height = meta.canvasH;
    }
    // Background fill. Default matches the Python tan (60, 50, 40).
    ctx.globalCompositeOperation = "source-over";
    ctx.globalAlpha = 1;
    ctx.fillStyle = opts.bgColor ?? "rgb(60, 50, 40)";
    ctx.fillRect(0, 0, meta.canvasW, meta.canvasH);

    // Room highlight (canvas-side green tint — mirrors Python
    // _draw_room_highlight). Drawn UNDER the iso passes so structs
    // composite on top.
    if (opts.highlightTiles && opts.highlightTiles.size > 0) {
      this.drawHighlight(ctx, opts.highlightTiles, rx0, ry0, rx1, ry1);
    }

    // Iso row groups. Tiles with same (x + y) share one screen-Y row.
    // Within a row, left-to-right by (x - y).
    const rowsByXy = new Map<number, [number, number][]>();
    for (let ty = ry0; ty <= ry1; ty++) {
      for (let tx = rx0; tx <= rx1; tx++) {
        const k = tx + ty;
        let row = rowsByXy.get(k);
        if (!row) {
          row = [];
          rowsByXy.set(k, row);
        }
        row.push([tx, ty]);
      }
    }
    for (const row of rowsByXy.values()) {
      row.sort((a, b) => (a[0] - a[1]) - (b[0] - b[1]));
    }
    const orderedXy = [...rowsByXy.keys()].sort((a, b) => a - b);

    // PASS 1: LAND (single-layer)
    if (!skip.has("land")) {
      for (const xy of orderedXy) {
        const row = rowsByXy.get(xy)!;
        for (const [tx, ty] of row) {
          this.drawTileLayer(ctx, tx, ty, "land", false);
        }
      }
    }
    // PASS 2: OBJECTS
    if (!skip.has("objs")) {
      for (const xy of orderedXy) {
        const row = rowsByXy.get(xy)!;
        for (const [tx, ty] of row) {
          this.drawTileLayer(ctx, tx, ty, "objs", false);
        }
      }
    }
    // PASS 3: SHADOWS (darken-blend)
    if (!skip.has("shadows")) {
      for (const xy of orderedXy) {
        const row = rowsByXy.get(xy)!;
        for (const [tx, ty] of row) {
          this.drawTileLayer(ctx, tx, ty, "shadows", true);
        }
      }
    }
    // PASS 4: STRUCT + ROOF + ONROOF grouped — level-major WITHIN each
    // iso row. THIS IS THE CRITICAL FIDELITY FIX. Layer-major across
    // the whole map would let a back roof overdraw a front wall.
    const layers4: LayerName[] = (["structs", "roofs", "onroofs"] as const)
      .filter((l) => !skip.has(l));
    if (layers4.length > 0) {
      for (const xy of orderedXy) {
        const row = rowsByXy.get(xy)!;
        for (const layer of layers4) {
          for (const [tx, ty] of row) {
            this.drawTileLayer(ctx, tx, ty, layer, false);
          }
        }
      }
    }
    return meta;
  }

  // ─── Internals ─────────────────────────────────────────────────────
  private resolveRegion(opts: RenderOptions): {
    rx0: number; ry0: number; rx1: number; ry1: number;
  } {
    const { rows, cols } = this.parsed;
    if (opts.roomId !== undefined && opts.roomId !== null) {
      const ring = opts.ring ?? 5;
      let xs: number[] = [];
      let ys: number[] = [];
      for (let g = 0; g < this.parsed.rooms.length; g++) {
        if (this.parsed.rooms[g] === opts.roomId) {
          xs.push(g % cols);
          ys.push(Math.floor(g / cols));
        }
      }
      if (xs.length > 0) {
        return {
          rx0: Math.max(0, Math.min(...xs) - ring),
          ry0: Math.max(0, Math.min(...ys) - ring),
          rx1: Math.min(cols - 1, Math.max(...xs) + ring),
          ry1: Math.min(rows - 1, Math.max(...ys) + ring),
        };
      }
      // Room not found — fall through to full sector.
    }
    if (opts.bbox) {
      const [x0, y0, x1, y1] = opts.bbox;
      return { rx0: x0, ry0: y0, rx1: x1, ry1: y1 };
    }
    return { rx0: 0, ry0: 0, rx1: cols - 1, ry1: rows - 1 };
  }

  private tileToPixRaw(x: number, y: number): [number, number] {
    return [(x - y) * TILE_HW, (x + y) * TILE_HH];
  }

  private drawHighlight(
    ctx: CanvasRenderingContext2D,
    highlightTiles: Set<string>,
    rx0: number, ry0: number, rx1: number, ry1: number,
  ): void {
    ctx.save();
    ctx.fillStyle = "rgba(60, 120, 60, 0.27)";
    ctx.strokeStyle = "rgba(100, 200, 100, 0.59)";
    ctx.lineWidth = 1;
    for (const key of highlightTiles) {
      const parts = key.split(",");
      if (parts.length !== 2) continue;
      const tx = Number(parts[0]);
      const ty = Number(parts[1]);
      if (tx < rx0 || tx > rx1 || ty < ry0 || ty > ry1) continue;
      const [rawX, rawY] = this.tileToPixRaw(tx, ty);
      const px = rawX - this.meta.ixMin;
      const py = rawY - this.meta.iyMin;
      // Diamond extends DOWN-RIGHT from (px, py) (the west-apex /
      // bbox top-left). Corners in clockwise order from N.
      ctx.beginPath();
      ctx.moveTo(px + TILE_HW, py);                  // N
      ctx.lineTo(px + TILE_W, py + TILE_HH);         // E
      ctx.lineTo(px + TILE_HW, py + TILE_H);         // S
      ctx.lineTo(px, py + TILE_HH);                  // W
      ctx.closePath();
      ctx.fill();
      ctx.stroke();
    }
    ctx.restore();
  }

  private drawTileLayer(
    ctx: CanvasRenderingContext2D,
    tx: number, ty: number,
    layer: LayerName,
    shadow: boolean,
  ): void {
    const gn = ty * this.parsed.cols + tx;
    // Shadows: overlay the engine's auto-added buddy shadows so the editor
    // matches in-game (ephemeral — never written back to parsed/.dat).
    const entries = layer === "shadows"
      ? effectiveShadowEntries(this.parsed, gn, this.cellMap)
      : this.parsed[layer][gn];
    if (!entries || entries.length === 0) return;
    const [rawX, rawY] = this.tileToPixRaw(tx, ty);
    const px = rawX - this.meta.ixMin;
    const py = rawY - this.meta.iyMin;
    this.drawEntriesAt(ctx, entries, px, py, layer, shadow);
  }

  /** The per-entry sprite blit shared by the main render and the region
   * ghost render: cellMap lookup + per-cell ox/oy offsets + the layer's
   * WALL_HEIGHT yLift. (px, py) is the tile's raw iso projection point
   * already translated into the destination canvas's pixel space. Keeping
   * this in ONE place is what guarantees the placement ghost is pixel-
   * aligned with what stamping will produce. */
  private drawEntriesAt(
    ctx: CanvasRenderingContext2D,
    entries: number[][],
    px: number, py: number,
    layer: LayerName,
    shadow: boolean,
  ): void {
    const yLift = LAYER_Y_LIFT[layer];
    const src = shadow ? this.darkenAtlas : this.atlas;
    for (const entry of entries) {
      if (entry.length < 2) continue;
      // Stored shape is [slot, sub]. TypeScript can't narrow tuple
      // length under noUncheckedIndexedAccess, so coerce after the
      // length guard above.
      const slot = entry[0] as number;
      const sub = entry[1] as number;
      const cell = this.cellMap.get((slot << 16) | (sub & 0xffff));
      if (!cell) continue;  // unknown slot/sub — silently skip (matches Python)
      const pasteX = px + cell.ox;
      const pasteY = py + cell.oy - yLift;
      ctx.drawImage(
        src,
        cell.x, cell.y, cell.w, cell.h,
        pasteX, pasteY, cell.w, cell.h,
      );
    }
  }

  /**
   * Render a standalone tile REGION (a clipboard/building-library tile
   * list) into a tight transparent offscreen canvas at `alpha` opacity.
   * Used by the building-placement ghost overlay: the region is rendered
   * ONCE per armed building, then merely repositioned per hovered tile.
   *
   * Engine fidelity: reuses the exact same code paths as `render()` —
   * `tileToPixRaw` for the iso projection, `drawEntriesAt` for the
   * cellMap lookup + per-cell ox/oy + WALL_HEIGHT yLift, and the same
   * pass structure (land → objs → shadows(darken) → struct/roof/onroof
   * level-major within each iso row). No constants are forked, so the
   * ghost is pixel-identical to what stamping the region will paint.
   *
   * Differences vs the main render (intentional):
   *   - transparent background (it's an overlay);
   *   - shadows draw only the region's STORED shadow entries (no buddy-
   *     shadow synthesis — that needs the surrounding parsed sector, and
   *     the engine re-adds buddies at load anyway);
   *   - uniform `alpha` applied to the FINISHED composite (not per-draw,
   *     so internal sprite overlaps don't double-blend).
   *
   * Returns null when the region has nothing drawable (no known cells).
   */
  renderRegionToCanvas(
    tiles: GhostRegionTile[],
    alpha = 0.7,
  ): RegionRender | null {
    // Tight bbox over every drawable cell, in raw iso-pixel space where
    // tile (0,0)'s tileToPixRaw point is the origin.
    let x0 = Infinity; let y0 = Infinity;
    let x1 = -Infinity; let y1 = -Infinity;
    let drawable = 0;
    let unknownCells = 0;
    for (const t of tiles) {
      const [rawX, rawY] = this.tileToPixRaw(t.dx, t.dy);
      for (const layer of ALL_LAYERS) {
        const entries = t.layers[layer];
        if (!entries) continue;
        const yLift = LAYER_Y_LIFT[layer];
        for (const e of entries) {
          if (e.length < 2) continue;
          const cell = this.cellMap.get(
            ((e[0] as number) << 16) | ((e[1] as number) & 0xffff));
          if (!cell) { unknownCells++; continue; }
          drawable++;
          const cx = rawX + cell.ox;
          const cy = rawY + cell.oy - yLift;
          if (cx < x0) x0 = cx;
          if (cy < y0) y0 = cy;
          if (cx + cell.w > x1) x1 = cx + cell.w;
          if (cy + cell.h > y1) y1 = cy + cell.h;
        }
      }
    }
    // Dev-only visibility: a region whose entries mostly miss the
    // cellMap means the atlas/manifest doesn't carry its slots (e.g. a
    // stale atlas after add-to-tileset) — the ghost would silently show
    // holes that the stamp won't have.
    if (unknownCells > 0 && import.meta.env.DEV) {
      // eslint-disable-next-line no-console
      console.warn(
        `renderRegionToCanvas: ${unknownCells} entr(ies) missing from the `
        + `atlas cellMap (drawable: ${drawable}) — ghost will have holes`,
      );
    }
    if (drawable === 0) return null;

    const work = document.createElement("canvas");
    work.width = Math.ceil(x1 - x0);
    work.height = Math.ceil(y1 - y0);
    const ctx = work.getContext("2d");
    if (!ctx) return null;

    // Iso row groups — same ordering scheme as render(): rows keyed by
    // (dx + dy), left-to-right within a row by (dx - dy).
    const rowsByXy = new Map<number, GhostRegionTile[]>();
    for (const t of tiles) {
      const k = t.dx + t.dy;
      let row = rowsByXy.get(k);
      if (!row) { row = []; rowsByXy.set(k, row); }
      row.push(t);
    }
    for (const row of rowsByXy.values()) {
      row.sort((a, b) => (a.dx - a.dy) - (b.dx - b.dy));
    }
    const orderedXy = [...rowsByXy.keys()].sort((a, b) => a - b);

    const drawPass = (layer: LayerName, shadow: boolean) => {
      for (const xy of orderedXy) {
        const row = rowsByXy.get(xy)!;
        for (const t of row) {
          const entries = t.layers[layer];
          if (!entries || entries.length === 0) continue;
          const [rawX, rawY] = this.tileToPixRaw(t.dx, t.dy);
          this.drawEntriesAt(ctx, entries, rawX - x0, rawY - y0,
            layer, shadow);
        }
      }
    };
    // PASS 1-3: land, objs, shadows (darken-blend) — whole-region passes.
    drawPass("land", false);
    drawPass("objs", false);
    drawPass("shadows", true);
    // PASS 4: struct + roof + onroof grouped, level-major WITHIN each iso
    // row (mirrors render()'s critical fidelity rule).
    for (const xy of orderedXy) {
      const row = rowsByXy.get(xy)!;
      for (const layer of ["structs", "roofs", "onroofs"] as const) {
        for (const t of row) {
          const entries = t.layers[layer];
          if (!entries || entries.length === 0) continue;
          const [rawX, rawY] = this.tileToPixRaw(t.dx, t.dy);
          this.drawEntriesAt(ctx, entries, rawX - x0, rawY - y0,
            layer, false);
        }
      }
    }

    // Apply the uniform ghost alpha to the finished composite.
    const out = document.createElement("canvas");
    out.width = work.width;
    out.height = work.height;
    const outCtx = out.getContext("2d");
    if (!outCtx) return null;
    outCtx.globalAlpha = alpha;
    outCtx.drawImage(work, 0, 0);
    return { canvas: out, originX: x0, originY: y0 };
  }
}

// ─── Local edit shape (mirror of SessionEdit for client-side application) ──
export interface LocalEdit {
  x: number;
  y: number;
  op: "place" | "add" | "remove" | "replace" | "set_entries" | "set_room" | "set_height";
  layer?: LayerName;
  slot?: number;
  sub?: number;
  entryIndex?: number;
  entries?: number[][];  // for set_entries — list of [slot, sub] pairs
  roomId?: number;
  height?: number;       // for set_height (0–255)
}

/** One undo step. Records the AFFECTED tile's state BEFORE the edit so
 * a single set_entries (or set_room) op can restore it. Multiple tiles
 * touched by one stroke are bundled into a single UndoEntry so Ctrl+Z
 * reverts the whole stroke, not one tile at a time. */
export interface UndoEntry {
  /** Per-(x, y, layer) snapshot of the entries that were there before
   * this stroke wrote. If a tile/layer appears multiple times in a
   * stroke (e.g. drag-paint that crosses the same tile twice), only
   * the FIRST snapshot is kept. */
  snapshots: Array<{
    x: number;
    y: number;
    layer: LayerName;
    entries: number[][];  // pre-edit entry list (copy)
  }>;
  /** Per-tile room snapshots (separate axis from layer snapshots). */
  roomSnapshots: Array<{ x: number; y: number; roomId: number }>;
  /** Per-tile height snapshots (separate axis again). */
  heightSnapshots: Array<{ x: number; y: number; height: number }>;
  /** Human label for the undo UI ("Paint floor (12 tiles)"). */
  label: string;
}

// ─── Helpers ──────────────────────────────────────────────────────────
function loadImage(src: string): Promise<HTMLImageElement> {
  return new Promise((resolve, reject) => {
    const img = new Image();
    img.crossOrigin = "anonymous";
    img.onload = () => resolve(img);
    img.onerror = (e) => reject(e);
    img.src = src;
  });
}

/**
 * Bake a black silhouette of the atlas with alpha = src.alpha / 2.
 * Used as the source for shadow-pass drawImage calls — semi-transparent
 * black sprites drawn with default source-over darken the underlying
 * pixels by 50% (mimics the Python alpha_composite of half-alpha black
 * silhouette in iso_renderer.py:418-426).
 *
 * Implementation uses per-pixel `getImageData` + `putImageData` rather
 * than composite-op chaining: composite ops have spec subtleties (the
 * destination-in alpha-multiplication path works but is harder to
 * reason about across browser implementations). Pixel mutation is
 * O(atlas pixels) ≈ 50–100 ms for a 4096-wide atlas on a laptop, paid
 * ONCE per session open. Acceptable for the certainty.
 */
function bakeDarkenAtlas(
  atlas: HTMLImageElement,
  onProgress?: (pct: number) => void,
): HTMLCanvasElement {
  const w = atlas.naturalWidth;
  const h = atlas.naturalHeight;
  const c = document.createElement("canvas");
  c.width = w;
  c.height = h;
  const ctx = c.getContext("2d");
  if (!ctx) throw new Error("Could not get 2d context for darken atlas");
  ctx.drawImage(atlas, 0, 0);
  const imgData = ctx.getImageData(0, 0, w, h);
  const px = imgData.data;
  // Direct indexed reads on Uint8ClampedArray return number — TS infers
  // wider when noUncheckedIndexedAccess is on. Stash through locals so
  // the read is unambiguous.
  const total = px.length;
  // Report progress every ~10% of pixels so the bar advances visibly
  // on large atlases (~8M pixels for a 4096x2048 atlas).
  const reportEvery = Math.max(1, Math.floor(total / 40));
  let nextReport = reportEvery;
  for (let i = 0; i < total; i += 4) {
    const aIdx = i + 3;
    const a = px[aIdx] ?? 0;
    px[i] = 0;        // R
    px[i + 1] = 0;    // G
    px[i + 2] = 0;    // B
    px[aIdx] = a >> 1;  // halve alpha — matches `v // 2` in Python
    if (onProgress && i >= nextReport) {
      onProgress(Math.round((i / total) * 100));
      nextReport += reportEvery;
    }
  }
  ctx.putImageData(imgData, 0, 0);
  return c;
}

// ─── Iso projection helpers ───────────────────────────────────────────
// Mirror of the helpers in mapforge.ts that operate on a server-rendered
// PNG, so existing overlay code can keep working with our canvas's meta.

export function tileToCanvasPixel(
  tx: number, ty: number, meta: RenderMeta,
): { x: number; y: number } {
  const hw = meta.tileW / 2;
  const hh = meta.tileH / 2;
  return {
    x: (tx - ty) * hw - meta.ixMin,
    y: (tx + ty) * hh - meta.iyMin,
  };
}

export function imagePixelToTile(
  px: number, py: number, meta: RenderMeta,
  cols: number, rows: number,
): { x: number; y: number } | null {
  // Iso inverse projection.
  //
  // CONVENTION (verified against atlas STI offsets):
  //   tileToPixRaw(tx, ty) returns the TOP-LEFT of the tile bounding
  //   box (= the WEST apex of the diamond). The diamond extends
  //   DOWN-RIGHT from there: corners at
  //     W: (sx,        sy + hh)
  //     N: (sx + hw,   sy      )
  //     E: (sx + 2hw,  sy + hh)
  //     S: (sx + hw,   sy + 2hh)
  //   and center at (sx + hw, sy + hh).
  //
  // This is what the engine + Python iso_renderer ACTUALLY do (proven
  // by the floor STIs that ship with ox=0, oy=0 — drawn at (sx, sy)
  // those 40x20 sprites cover (sx, sy)..(sx+40, sy+20), the new
  // diamond. The Python iso_renderer.py docstring claiming "south
  // apex" was misleading).
  //
  // For a click at canvas pixel (px, py), the tile (tx, ty) whose
  // CENTER the click is closest to is:
  //   tx - ty = (px + ix_min)/hw - 1     = A - 1
  //   tx + ty = (py + iy_min)/hh - 1     = B - 1
  // hence tx = (A + B)/2 - 1, ty = (B - A)/2.
  const hw = meta.tileW / 2;
  const hh = meta.tileH / 2;
  const A = (px + meta.ixMin) / hw;
  const B = (py + meta.iyMin) / hh;
  const tx = Math.round((A + B) / 2 - 1);
  const ty = Math.round((B - A) / 2);
  if (tx < 0 || ty < 0 || tx >= cols || ty >= rows) return null;
  return { x: tx, y: ty };
}

export function tileDiamondCorners(
  tx: number, ty: number, meta: RenderMeta,
): [[number, number], [number, number], [number, number], [number, number]] {
  // (sx, sy) is the tile's TOP-LEFT bounding-box corner (= west apex).
  // The diamond extends down-right. See imagePixelToTile for the
  // convention rationale. Order: [N, E, S, W] (clockwise from top).
  const { x: sx, y: sy } = tileToCanvasPixel(tx, ty, meta);
  const hw = meta.tileW / 2;
  const hh = meta.tileH / 2;
  return [
    [sx + hw, sy],        // N (top)
    [sx + 2 * hw, sy + hh],  // E (right)
    [sx + hw, sy + 2 * hh],  // S (bottom)
    [sx, sy + hh],        // W (left)
  ];
}
