/**
 * Shared persistence for the "Just added" panel that lives on both
 * /mapforge (read-only rail) and /tileset-editor (the surface that
 * actually adds STIs).
 *
 * Storage shape:
 *   localStorage["mapforge.recentAdditions.v1"] = {
 *     "<xmlPath>::<tileset>": [RecentAddition, ...],
 *     ...
 *   }
 *
 * Keyed by (xmlPath, tileset) so each tileset gets its own LRU queue.
 * Bucket cap = 24 entries; queue dedupes by (sha256, slot) so re-adds
 * don't pile up. Persistence failures (quota / JSON parse) are
 * non-fatal — the user just loses cross-session visibility.
 */
import type { RecentAddition } from "./mapforge";

const STORAGE_KEY = "mapforge.recentAdditions.v1";
const BUCKET_CAP = 24;

function bucketKey(xmlPath: string, tileset: number): string {
  return `${xmlPath || "_"}::${tileset}`;
}

function loadAll(): Record<string, RecentAddition[]> {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (!raw) return {};
    return JSON.parse(raw) as Record<string, RecentAddition[]>;
  } catch {
    return {};
  }
}

function saveAll(map: Record<string, RecentAddition[]>): void {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(map));
  } catch {
    // Quota or other persistence failure — non-fatal.
  }
}

/** Read the addition list for one (xmlPath, tileset) bucket. */
export function readRecentAdditions(
  xmlPath: string, tileset: number,
): RecentAddition[] {
  return loadAll()[bucketKey(xmlPath, tileset)] ?? [];
}

/** Push a new addition onto the front of its bucket. Dedupes by
 * (sha256, slot) so re-adds don't double up; caps at 24 entries. */
export function pushRecentAddition(
  xmlPath: string, tileset: number, addition: RecentAddition,
): void {
  const all = loadAll();
  const key = bucketKey(xmlPath, tileset);
  const prev = all[key] ?? [];
  const filtered = prev.filter(
    (a) => !(a.sha256 === addition.sha256 && a.slot === addition.slot),
  );
  all[key] = [addition, ...filtered].slice(0, BUCKET_CAP);
  saveAll(all);
}
