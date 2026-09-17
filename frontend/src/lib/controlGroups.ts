/**
 * Persisted, per-(xmlPath, tileset) control groups — the
 * StarCraft-style Ctrl+1..9 save / 1..9 recall bar that absorbs the old
 * Favorites row. A slot holds either an armed brush or a copied sprite
 * group; seeded once from the existing favorites list so upgrading users
 * don't lose their pinned brushes.
 *
 * Storage shape (one localStorage key, all buckets):
 *   localStorage["mapforge.controlGroups.v1"] = { "<xmlPath>::<tileset>": ControlGroup[9] }
 *
 * Mirrors usePersistentBrushBucket's read/write/rehydrate contract from
 * brushBuckets.ts (same bucketKey, same non-fatal quota/parse handling).
 */
import { useCallback, useEffect, useState } from "react";

import { bucketKey } from "./brushBuckets";
import type { ActiveBrush } from "../routes/MapForgePalette";
import type { SpriteGroup } from "./mapPlacement";

export type ControlGroup =
  | { kind: "brush"; brush: ActiveBrush }
  | { kind: "group"; group: SpriteGroup }
  | null;

export const CONTROL_GROUPS_KEY = "mapforge.controlGroups.v1";

/** Addressable by number keys 1-9. */
const SLOT_COUNT = 9;

function normalizeTo9(groups: ControlGroup[]): ControlGroup[] {
  const out: ControlGroup[] = new Array(SLOT_COUNT).fill(null);
  for (let i = 0; i < SLOT_COUNT; i++) out[i] = groups[i] ?? null;
  return out;
}

/** Fills empty slots 0..8 from `favorites` IN ORDER, but only when every
 * existing slot is null (a fresh bucket) — otherwise the caller already
 * has real group state and seeding would clobber it. Always returns a
 * 9-slot array either way (input padded/truncated). */
export function seedFromFavorites(groups: ControlGroup[], favorites: ActiveBrush[]): ControlGroup[] {
  const padded = normalizeTo9(groups);
  const allEmpty = padded.every((g) => g === null);
  if (!allEmpty) return padded;
  const seeded: ControlGroup[] = [...padded];
  for (let i = 0; i < SLOT_COUNT && i < favorites.length; i++) {
    const b = favorites[i];
    if (b) seeded[i] = { kind: "brush", brush: b };
  }
  return seeded;
}

function readGroups(key: string): ControlGroup[] {
  try {
    const raw = localStorage.getItem(CONTROL_GROUPS_KEY);
    if (!raw) return normalizeTo9([]);
    const parsed = JSON.parse(raw) as Record<string, ControlGroup[]>;
    return normalizeTo9(parsed[key] ?? []);
  } catch {
    return normalizeTo9([]);
  }
}

function writeGroups(key: string, groups: ControlGroup[]): void {
  try {
    const raw = localStorage.getItem(CONTROL_GROUPS_KEY);
    const parsed = raw ? (JSON.parse(raw) as Record<string, ControlGroup[]>) : {};
    parsed[key] = groups;
    localStorage.setItem(CONTROL_GROUPS_KEY, JSON.stringify(parsed));
  } catch {
    // Quota or JSON parse — non-fatal; user loses persistence only.
  }
}

/**
 * `[groups, setGroup]` persisted per (xmlPath, tileset), always length 9.
 * Rehydrates on key change (install/tileset switch mid-session); writes
 * the whole bucket on every `setGroup` call, mirroring
 * `usePersistentBrushBucket`.
 */
export function usePersistentControlGroups(
  xmlPath: string | undefined,
  tileset: number,
): [ControlGroup[], (i: number, v: ControlGroup) => void] {
  const key = bucketKey(xmlPath, tileset);
  const [groups, setGroups] = useState<ControlGroup[]>(() => readGroups(key));

  useEffect(() => {
    setGroups(readGroups(key));
  }, [key]);

  useEffect(() => {
    writeGroups(key, groups);
  }, [key, groups]);

  const setGroup = useCallback((i: number, v: ControlGroup) => {
    setGroups((prev) => {
      const next = normalizeTo9(prev);
      if (i >= 0 && i < SLOT_COUNT) next[i] = v;
      return next;
    });
  }, []);

  return [groups, setGroup];
}
