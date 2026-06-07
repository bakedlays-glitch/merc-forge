/**
 * Pure tile-geometry helpers for MapForge's drag-to-define shape tools.
 *
 * Each function takes two corner/endpoint tiles and returns the list of
 * {x, y} tiles the shape covers. No bounds clamping happens here — the
 * caller filters to the sector's cols/rows (mirrors the bounds guard the
 * pencil tool already applies before committing edits).
 *
 * Coordinates are tile-space (gridno = y * cols + x), the same space the
 * canvas click→tile inversion produces.
 */

export interface Tile {
  x: number;
  y: number;
}

export type ShapeKind = "rect-fill" | "rect-outline" | "line" | "room";

/** Normalize two corners into [x0,y0,x1,y1] with x0<=x1, y0<=y1. */
export function normalizeRect(
  a: Tile,
  b: Tile,
): { x0: number; y0: number; x1: number; y1: number } {
  return {
    x0: Math.min(a.x, b.x),
    y0: Math.min(a.y, b.y),
    x1: Math.max(a.x, b.x),
    y1: Math.max(a.y, b.y),
  };
}

/** Every tile inside the bounding box of a and b (inclusive). */
export function rectFillTiles(a: Tile, b: Tile): Tile[] {
  const { x0, y0, x1, y1 } = normalizeRect(a, b);
  const out: Tile[] = [];
  for (let y = y0; y <= y1; y++) {
    for (let x = x0; x <= x1; x++) {
      out.push({ x, y });
    }
  }
  return out;
}

/** Only the perimeter tiles of the bounding box. A 1-wide or 1-tall drag
 * collapses to the full line/run (no interior to omit). Deduped — corners
 * would otherwise appear twice. */
export function rectOutlineTiles(a: Tile, b: Tile): Tile[] {
  const { x0, y0, x1, y1 } = normalizeRect(a, b);
  const seen = new Set<string>();
  const out: Tile[] = [];
  const push = (x: number, y: number) => {
    const k = `${x},${y}`;
    if (seen.has(k)) return;
    seen.add(k);
    out.push({ x, y });
  };
  for (let x = x0; x <= x1; x++) {
    push(x, y0);
    push(x, y1);
  }
  for (let y = y0; y <= y1; y++) {
    push(x0, y);
    push(x1, y);
  }
  return out;
}

/** Bresenham line between two tiles (inclusive of both endpoints). */
export function lineTiles(a: Tile, b: Tile): Tile[] {
  const out: Tile[] = [];
  let x0 = a.x;
  let y0 = a.y;
  const x1 = b.x;
  const y1 = b.y;
  const dx = Math.abs(x1 - x0);
  const dy = -Math.abs(y1 - y0);
  const sx = x0 < x1 ? 1 : -1;
  const sy = y0 < y1 ? 1 : -1;
  let err = dx + dy;
  // Guard against a pathological infinite loop from NaN inputs.
  let guard = 0;
  const maxIter = dx - dy + 2;
  while (guard++ <= maxIter) {
    out.push({ x: x0, y: y0 });
    if (x0 === x1 && y0 === y1) break;
    const e2 = 2 * err;
    if (e2 >= dy) {
      err += dy;
      x0 += sx;
    }
    if (e2 <= dx) {
      err += dx;
      y0 += sy;
    }
  }
  return out;
}

/** Dispatch to the right generator for a shape kind. "room" shares the
 * rectangle-fill footprint (it just writes room-ids instead of tiles). */
export function shapeTiles(kind: ShapeKind, a: Tile, b: Tile): Tile[] {
  switch (kind) {
    case "rect-fill":
    case "room":
      return rectFillTiles(a, b);
    case "rect-outline":
      return rectOutlineTiles(a, b);
    case "line":
      return lineTiles(a, b);
    default:
      return [];
  }
}
