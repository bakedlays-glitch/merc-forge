/**
 * Pure region copy/paste transforms for MapForge (A5, Phase 3).
 *
 * No I/O, no React, no runtime imports — only TYPE imports from ./mapforge —
 * so this module transpiles + runs standalone for assertion tests (there is
 * no frontend test runner). The route layer (MapForgeSector) supplies the
 * live data — parsed sector, the target's current room ids, the cross-tileset
 * slot-remap fn, the buddy-shadow predicate — and turns the emitted
 * SessionEdit[] into one transactional, single-undo-stroke paste.
 */
import type { LayerName, ParsedSector, SessionEdit } from "./mapforge";

/** The 6 tile layers a region carries (engine order). */
export const CLIP_LAYERS: readonly LayerName[] = [
  "land", "objs", "shadows", "structs", "roofs", "onroofs",
];

export interface ClipTile {
  /** Offset from the selection's top-left corner. */
  dx: number;
  dy: number;
  /** Per layer: list of [slot, sub] pairs. */
  layers: Record<LayerName, number[][]>;
  room: number;
  height: number;
}

export interface ClipboardRegion {
  /** Source tileset — paste warns / slot-remaps when it differs from target. */
  sourceTileset: number;
  /** Source sector basename (provenance / cross-sector note). */
  sourceSector: string;
  w: number;
  h: number;
  tiles: ClipTile[];
}

export interface PasteOptions {
  /** Per-layer include (default: all true). */
  includeLayers?: Partial<Record<LayerName, boolean>>;
  includeRooms?: boolean;   // default true
  includeHeights?: boolean; // default true
  /** The TARGET sector's current room ids, so remap picks fresh unused ids. */
  existingRoomIds?: number[];
}

export interface PasteResult {
  edits: SessionEdit[];
  /** source room id → fresh target room id. */
  roomRemap: Record<number, number>;
  /** Distinct in-bounds target tiles touched. */
  targetTiles: number;
  /** Tiles clipped because they fell outside the map. */
  droppedTiles: number;
}

interface Pt { x: number; y: number; }

/** Defensive copy of a [slot, sub] entry. (Index access is `number |
 * undefined` under noUncheckedIndexedAccess; entries are always 2-element.) */
function pair(e: number[]): number[] {
  return [e[0] ?? 0, e[1] ?? 0];
}

/** Capture an inclusive rectangle (clamped to the map) as a relative
 * clipboard. */
export function sliceRegion(
  parsed: ParsedSector,
  a: Pt,
  b: Pt,
  sourceSector: string,
): ClipboardRegion {
  const x0 = Math.max(0, Math.min(a.x, b.x));
  const y0 = Math.max(0, Math.min(a.y, b.y));
  const x1 = Math.min(parsed.cols - 1, Math.max(a.x, b.x));
  const y1 = Math.min(parsed.rows - 1, Math.max(a.y, b.y));
  if (x0 > x1 || y0 > y1) {
    // Selection fully off-map → empty clipboard (each axis is clamped on
    // only one side, so a fully-off-map drag can leave x0 > x1).
    return { sourceTileset: parsed.tileset, sourceSector, w: 0, h: 0, tiles: [] };
  }
  const tiles: ClipTile[] = [];
  for (let y = y0; y <= y1; y++) {
    for (let x = x0; x <= x1; x++) {
      const g = y * parsed.cols + x;
      const layers = {} as Record<LayerName, number[][]>;
      for (const l of CLIP_LAYERS) {
        const arr = (parsed[l] as number[][][])[g] ?? [];
        layers[l] = arr.map(pair);
      }
      tiles.push({
        dx: x - x0,
        dy: y - y0,
        layers,
        room: parsed.rooms[g] ?? 0,
        height: parsed.heights[g] ?? 0,
      });
    }
  }
  return {
    sourceTileset: parsed.tileset,
    sourceSector,
    w: x1 - x0 + 1,
    h: y1 - y0 + 1,
    tiles,
  };
}

/** Remap each distinct nonzero source room id to a fresh target id appended
 * ABOVE max(existingRoomIds). Pass the TARGET map's full rooms array as
 * `existingRoomIds` so the new ids can't collide with surviving target rooms.
 * Caveats: the new ids are contiguous among themselves but pre-existing gaps
 * in the target are preserved (not globally repacked); pasting a region onto
 * tiles it OVERLAPS (self-paste) leaves the non-overwritten source tiles on
 * their old ids — ambiguous by nature, so avoid overlapping self-paste. */
export function remapRoomIds(
  sourceRoomIds: number[],
  existingRoomIds: number[],
): Record<number, number> {
  const distinct = Array.from(new Set(sourceRoomIds.filter((r) => r > 0)))
    .sort((p, q) => p - q);
  let next = existingRoomIds.reduce((m, r) => (r > m ? r : m), 0) + 1;
  const remap: Record<number, number> = {};
  for (const r of distinct) remap[r] = next++;
  return remap;
}

/** Strip buddy-eligible shadow entries (the engine auto-adds these at load
 * via HAS_SHADOW_BUDDY; copying them double-shadows in-game). `isBuddy` is
 * supplied by the caller from the renderer's pairing data. */
export function stripBuddyShadows(
  clip: ClipboardRegion,
  isBuddy: (slot: number, sub: number) => boolean,
): ClipboardRegion {
  return {
    ...clip,
    tiles: clip.tiles.map((t) => ({
      ...t,
      layers: {
        ...t.layers,
        shadows: t.layers.shadows.filter((e) => !isBuddy(e[0] ?? 0, e[1] ?? 0)),
      },
    })),
  };
}

/** Cross-tileset slot remap (for the DEFERRED cross-tileset paste path —
 * revisit after Phase 5). `mapSlot(layer, slot, sub)` returns the target
 * tileset's [slot, sub] or null when no match exists. Unmappable entries are
 * dropped and reported. WARNING before wiring: per-entry silent drop is unsafe
 * for MULTI-TILE structures (dropping one footprint tile leaves a broken half)
 * and for struct/shadow pairs — make it footprint-aware OR have the caller
 * REFUSE the whole paste when `unmapped.length > 0`. */
export function remapSlots(
  clip: ClipboardRegion,
  mapSlot: (layer: LayerName, slot: number, sub: number) => number[] | null,
): {
  region: ClipboardRegion;
  unmapped: Array<{ layer: LayerName; slot: number; sub: number }>;
} {
  const unmapped: Array<{ layer: LayerName; slot: number; sub: number }> = [];
  const tiles = clip.tiles.map((t) => {
    const layers = {} as Record<LayerName, number[][]>;
    for (const l of CLIP_LAYERS) {
      const out: number[][] = [];
      for (const e of t.layers[l]) {
        const m = mapSlot(l, e[0] ?? 0, e[1] ?? 0);
        if (m) out.push([m[0] ?? 0, m[1] ?? 0]);
        else unmapped.push({ layer: l, slot: e[0] ?? 0, sub: e[1] ?? 0 });
      }
      layers[l] = out;
    }
    return { ...t, layers };
  });
  return { region: { ...clip, tiles }, unmapped };
}

/** Build the paste edits at `anchor`, bounds-clipped, with room remap +
 * per-layer toggles + heights. Pure — emits SessionEdit[] for one
 * transactional apply. */
export function pasteEdits(
  clip: ClipboardRegion,
  anchor: Pt,
  cols: number,
  rows: number,
  opts: PasteOptions = {},
): PasteResult {
  const inc = opts.includeLayers ?? {};
  const incRooms = opts.includeRooms !== false;
  const incHeights = opts.includeHeights !== false;
  const remap = incRooms
    ? remapRoomIds(clip.tiles.map((t) => t.room), opts.existingRoomIds ?? [])
    : {};
  const edits: SessionEdit[] = [];
  const touched = new Set<number>();
  let dropped = 0;
  for (const t of clip.tiles) {
    const x = anchor.x + t.dx;
    const y = anchor.y + t.dy;
    if (x < 0 || y < 0 || x >= cols || y >= rows) {
      dropped++;
      continue;
    }
    touched.add(y * cols + x);
    for (const l of CLIP_LAYERS) {
      if (inc[l] === false) continue;
      edits.push({ x, y, op: "set_entries", layer: l, entries: t.layers[l].map(pair) });
    }
    if (incRooms) {
      edits.push({ x, y, op: "set_room", room_id: t.room > 0 ? remap[t.room] : 0 });
    }
    if (incHeights) {
      edits.push({ x, y, op: "set_height", height: t.height });
    }
  }
  return { edits, roomRemap: remap, targetTiles: touched.size, droppedTiles: dropped };
}
