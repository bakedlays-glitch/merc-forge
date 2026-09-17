/**
 * MapForge — frontend client for the sidecar's StarCraft-style placement
 * oracle: `GET /mapforge/placement/tables` (once per tileset) and
 * `POST /mapforge/sessions/{id}/placement/check` (per hovered anchor while a
 * ghost is armed). No React here — the route owns the debounce
 * wiring, request-seq guarding, and verdict state; this module is just the
 * fetch shapes + the generic debounce helper it's built from.
 */
import { jsonGet, jsonPost } from "./mapforge";
import type { LayerName } from "./mapforge";
import type { PlacementTables, PlacementTest, SpriteRef, Tier } from "./mapPlacement";

/** One candidate's verdict from the sidecar's per-tile placement check.
 * Mirrors sitekit's resolved-tier response (routes/mapforge.py) — `tier`
 * has already folded "big-blocking" down to blocking/advisory per
 * candidate, unlike `PlacementTables.tiers`'s raw strings. */
export interface OracleVerdict {
  x: number; y: number; layer: LayerName; slot: number; sub: number;
  ok: boolean;
  test: PlacementTest | null;
  tier: Tier | null;
  tile: [number, number] | null;
  detail: string | null;
}
export interface OracleResult {
  results: OracleVerdict[];
  stale: boolean;
  world_ms: number;
}

export async function getPlacementTables(tileset: number): Promise<PlacementTables> {
  const r = await jsonGet<{
    categories: Record<string, string>;
    subs: Record<string, string>;
    tiers: Record<string, string>;
    fences: PlacementTables["fences"];
    road_slot: number;
  }>(`/mapforge/placement/tables?tileset=${tileset}`);
  return { categories: r.categories, subs: r.subs, tiers: r.tiers, fences: r.fences, roadSlot: r.road_slot };
}

export function checkPlacement(sessionId: string, cands: SpriteRef[]): Promise<OracleResult> {
  return jsonPost<OracleResult>(`/mapforge/sessions/${sessionId}/placement/check`, { candidates: cands });
}

/** Trailing-edge debounce: `call` (re)starts a `ms` window; when the window
 * elapses with no further `call`, `fn` fires once with that last call's
 * args. `cancel` drops a pending fire outright (no trailing call). */
export function trailingDebounce<A extends unknown[]>(
  ms: number,
  fn: (...a: A) => void,
): { call: (...a: A) => void; cancel: () => void } {
  let t: ReturnType<typeof setTimeout> | null = null;
  return {
    call: (...a: A) => {
      if (t) clearTimeout(t);
      t = setTimeout(() => { t = null; fn(...a); }, ms);
    },
    cancel: () => {
      if (t) clearTimeout(t);
      t = null;
    },
  };
}
