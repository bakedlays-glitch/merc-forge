/**
 * Persisted, per-(xmlPath, tileset) brush lists for the MapForge Brush
 * Box — Recent picks and Favorites.
 *
 * Storage shape (one localStorage key per kind):
 *   localStorage["mapforge.recentBrushes.v1"]  = { "<xmlPath>::<tileset>": ActiveBrush[] }
 *   localStorage["mapforge.favoriteBrushes.v1"] = { "<xmlPath>::<tileset>": ActiveBrush[] }
 *
 * This is the same per-(install, tileset) bucket model the "Just added"
 * panel uses (see recentAdditions.ts), generalized for brushes. Recent
 * was previously RAM-only (lost on reload); Favorites is new. Persistence
 * failures (quota / JSON parse) are non-fatal — the user just loses
 * cross-session visibility.
 */
import { useEffect, useState, type Dispatch, type SetStateAction } from "react";

import type { ActiveBrush } from "../routes/MapForgePalette";

export const RECENT_BRUSHES_KEY = "mapforge.recentBrushes.v1";
export const FAVORITE_BRUSHES_KEY = "mapforge.favoriteBrushes.v1";

/** Recent grid cap — a touch deeper than the rail can show without a
 * short scroll, rolling over LRU-style. */
export const RECENT_BRUSHES_CAP = 16;
/** Favorites are addressable by number keys 1-9, so the bar holds 9. */
export const FAVORITE_BRUSHES_CAP = 9;

/** Two brushes are "the same" for dedup/pin purposes when they point at
 * the same (slot, sub). Layer/category/filename are derived. */
export function sameBrush(a: ActiveBrush, b: ActiveBrush): boolean {
  return a.slot === b.slot && a.sub === b.sub;
}

function bucketKey(xmlPath: string | undefined, tileset: number): string {
  return `${xmlPath || "_"}::${tileset}`;
}

function readBucket(storageKey: string, key: string): ActiveBrush[] {
  try {
    const raw = localStorage.getItem(storageKey);
    if (!raw) return [];
    const parsed = JSON.parse(raw) as Record<string, ActiveBrush[]>;
    return parsed[key] ?? [];
  } catch {
    return [];
  }
}

function writeBucket(storageKey: string, key: string, list: ActiveBrush[]): void {
  try {
    const raw = localStorage.getItem(storageKey);
    const parsed = raw ? (JSON.parse(raw) as Record<string, ActiveBrush[]>) : {};
    parsed[key] = list;
    localStorage.setItem(storageKey, JSON.stringify(parsed));
  } catch {
    // Quota or JSON parse — non-fatal; user loses persistence only.
  }
}

/**
 * `[list, setList]` for a persisted per-(xmlPath, tileset) brush bucket.
 * Rehydrates when the install/tileset changes mid-session and rewrites
 * the whole bucket map on every change (cheap at this size, and avoids
 * the read-modify-write race a per-bucket scheme would have). Mirrors the
 * proven recentAdditions persistence pattern.
 */
export function usePersistentBrushBucket(
  storageKey: string,
  xmlPath: string | undefined,
  tileset: number,
): [ActiveBrush[], Dispatch<SetStateAction<ActiveBrush[]>>] {
  const key = bucketKey(xmlPath, tileset);
  const [list, setList] = useState<ActiveBrush[]>(() => readBucket(storageKey, key));
  // Rehydrate on key change (install/tileset switch within a session).
  useEffect(() => {
    setList(readBucket(storageKey, key));
  }, [storageKey, key]);
  // Persist on every change.
  useEffect(() => {
    writeBucket(storageKey, key, list);
  }, [storageKey, key, list]);
  return [list, setList];
}
