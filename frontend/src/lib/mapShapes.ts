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

export type ShapeKind =
  | "rect-fill"
  | "rect-outline"
  | "line"
  | "diamond"
  | "cross"
  | "triangle"
  | "hexagon"
  // Flood fill is click-driven (not a bbox drag) — handled specially in
  // MapForgeSector, so shapeTiles() returns [] for it (default branch).
  | "flood";

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

/** Filled diamond (rhombus) inscribed in the bbox of a and b. A 1-wide or
 * 1-tall drag falls back to the straight run so it never vanishes. */
export function diamondTiles(a: Tile, b: Tile): Tile[] {
  const { x0, y0, x1, y1 } = normalizeRect(a, b);
  const w = x1 - x0;
  const h = y1 - y0;
  if (w === 0 || h === 0) return rectFillTiles(a, b);
  const cx = (x0 + x1) / 2;
  const cy = (y0 + y1) / 2;
  const rx = w / 2;
  const ry = h / 2;
  const out: Tile[] = [];
  for (let y = y0; y <= y1; y++) {
    for (let x = x0; x <= x1; x++) {
      if (Math.abs(x - cx) / rx + Math.abs(y - cy) / ry <= 1 + 1e-9) {
        out.push({ x, y });
      }
    }
  }
  return out;
}

/** A plus/cross: the center row + center column spanning the bbox. */
export function crossTiles(a: Tile, b: Tile): Tile[] {
  const { x0, y0, x1, y1 } = normalizeRect(a, b);
  const cx = Math.round((x0 + x1) / 2);
  const cy = Math.round((y0 + y1) / 2);
  const seen = new Set<string>();
  const out: Tile[] = [];
  const push = (x: number, y: number) => {
    const k = `${x},${y}`;
    if (seen.has(k)) return;
    seen.add(k);
    out.push({ x, y });
  };
  for (let x = x0; x <= x1; x++) push(x, cy);
  for (let y = y0; y <= y1; y++) push(cx, y);
  return out;
}

/** Filled triangle, apex at top-center, base along the bottom edge. */
export function triangleTiles(a: Tile, b: Tile): Tile[] {
  const { x0, y0, x1, y1 } = normalizeRect(a, b);
  const h = y1 - y0;
  if (h === 0) return rectFillTiles(a, b);
  const cx = (x0 + x1) / 2;
  const rx = (x1 - x0) / 2;
  const out: Tile[] = [];
  for (let y = y0; y <= y1; y++) {
    const half = rx * ((y - y0) / h); // 0 at apex, full at base
    const lx = Math.ceil(cx - half);
    const rxi = Math.floor(cx + half);
    for (let x = lx; x <= rxi; x++) out.push({ x, y });
  }
  return out;
}

/** Filled flat-top hexagon inscribed in the bbox: left/right vertices at
 * mid-height, top/bottom edges inset by ~1/4 width. */
export function hexagonTiles(a: Tile, b: Tile): Tile[] {
  const { x0, y0, x1, y1 } = normalizeRect(a, b);
  const w = x1 - x0;
  const h = y1 - y0;
  if (w === 0 || h === 0) return rectFillTiles(a, b);
  const inset = w / 4;
  const cy = (y0 + y1) / 2;
  const out: Tile[] = [];
  for (let y = y0; y <= y1; y++) {
    let leftEdge: number;
    if (y <= cy) {
      const t = cy === y0 ? 1 : (y - y0) / (cy - y0); // 0 top → 1 mid
      leftEdge = x0 + inset * (1 - t);                // x0+inset → x0
    } else {
      const t = y1 === cy ? 1 : (y - cy) / (y1 - cy); // 0 mid → 1 bottom
      leftEdge = x0 + inset * t;                       // x0 → x0+inset
    }
    const lx = Math.ceil(leftEdge);
    const rxi = Math.floor(x1 - (leftEdge - x0));      // mirror across center
    for (let x = lx; x <= rxi; x++) out.push({ x, y });
  }
  return out;
}

/** Dispatch to the right generator for a shape kind. "room" shares the
 * rectangle-fill footprint (it just writes room-ids instead of tiles). */
export function shapeTiles(kind: ShapeKind, a: Tile, b: Tile): Tile[] {
  switch (kind) {
    case "rect-fill":
      return rectFillTiles(a, b);
    case "rect-outline":
      return rectOutlineTiles(a, b);
    case "line":
      return lineTiles(a, b);
    case "diamond":
      return diamondTiles(a, b);
    case "cross":
      return crossTiles(a, b);
    case "triangle":
      return triangleTiles(a, b);
    case "hexagon":
      return hexagonTiles(a, b);
    default:
      return [];
  }
}
