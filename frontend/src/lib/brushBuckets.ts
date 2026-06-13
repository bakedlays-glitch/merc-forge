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
import type { ClipboardRegion } from "./mapClipboard";

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

// ─── Per-(xmlPath, tileset) copy/paste clipboard (R6) ──────────────────
// One ClipboardRegion per bucket, so a region copied in one sector can be
// pasted in another sector of the SAME tileset (cross-tileset paste is
// disabled anyway). Survives reloads. Big regions that blow the localStorage
// quota silently fall back to in-session-only (the write try/catch).
export const CLIPBOARD_KEY = "mapforge.clipboard.v1";

function readClipboard(key: string): ClipboardRegion | null {
  try {
    const raw = localStorage.getItem(CLIPBOARD_KEY);
    if (!raw) return null;
    const parsed = JSON.parse(raw) as Record<string, ClipboardRegion>;
    return parsed[key] ?? null;
  } catch {
    return null;
  }
}

function writeClipboard(key: string, clip: ClipboardRegion | null): void {
  try {
    const raw = localStorage.getItem(CLIPBOARD_KEY);
    const parsed = raw ? (JSON.parse(raw) as Record<string, ClipboardRegion>) : {};
    if (clip === null) delete parsed[key];
    else parsed[key] = clip;
    localStorage.setItem(CLIPBOARD_KEY, JSON.stringify(parsed));
  } catch {
    // Quota (huge region) or parse — non-fatal; clipboard stays in-session.
  }
}

// ─── Edit-journal: dirty-session recovery across reload/crash (R6) ─────
// The sidecar never evicts a DIRTY session (committed), so a session with
// unsaved edits survives in-memory until the sidecar process itself
// restarts. We persist a tiny per-datPath breadcrumb — the sessionId +
// edit count — so that on reopening the SAME sector we can probe whether
// that session is still live server-side and RECONNECT to it (recovering
// the user's unsaved work) instead of opening a fresh one.
//
// Keyed by datPath (one entry per sector file). Cleared on save (clean)
// and on session close. Stale entries (sidecar restarted → session gone)
// are cleared by the recovery probe. Same quota/parse try-catch as the
// brush/clipboard buckets — a journal failure is non-fatal (the user just
// loses cross-reload recovery, never data already on disk).
export const JOURNAL_KEY = "mapforge.journal.v1";

export interface JournalEntry {
  /** The live sidecar session id holding the unsaved edits. */
  sessionId: string;
  /** edit_count at last write — used only to phrase the recovery banner
   * ("Recovered N unsaved edits"); the authoritative count comes from the
   * reconnected SessionInfo. */
  editCount: number;
  /** Epoch ms the entry was last written (Date.now() — frontend app code,
   * allowed; only workflow SCRIPTS forbid wall-clock). Advisory only. */
  savedAt: number;
}

export function readJournalEntry(datPath: string | undefined): JournalEntry | null {
  if (!datPath) return null;
  try {
    const raw = localStorage.getItem(JOURNAL_KEY);
    if (!raw) return null;
    const parsed = JSON.parse(raw) as Record<string, JournalEntry>;
    return parsed[datPath] ?? null;
  } catch {
    return null;
  }
}

export function writeJournalEntry(
  datPath: string | undefined,
  entry: JournalEntry,
): void {
  if (!datPath) return;
  try {
    const raw = localStorage.getItem(JOURNAL_KEY);
    const parsed = raw ? (JSON.parse(raw) as Record<string, JournalEntry>) : {};
    parsed[datPath] = entry;
    localStorage.setItem(JOURNAL_KEY, JSON.stringify(parsed));
  } catch {
    // Quota or parse — non-fatal; recovery just won't be available.
  }
}

export function clearJournalEntry(datPath: string | undefined): void {
  if (!datPath) return;
  try {
    const raw = localStorage.getItem(JOURNAL_KEY);
    if (!raw) return;
    const parsed = JSON.parse(raw) as Record<string, JournalEntry>;
    if (datPath in parsed) {
      delete parsed[datPath];
      localStorage.setItem(JOURNAL_KEY, JSON.stringify(parsed));
    }
  } catch {
    // Non-fatal.
  }
}

// ─── Recently-opened sectors (strategic hub grid, R6) ──────────────────
// A tiny global MRU list of sector .dat paths opened from the hub, so the
// hub can surface a "Recent sectors" row for quick re-entry. Distinct from
// the edit-journal (which only tracks sectors with UNSAVED edits): this is
// a plain "you looked at these lately" history, cleared by nothing but its
// own LRU rollover. Same quota/parse try-catch — non-fatal on failure.
export const RECENT_SECTORS_KEY = "mapforge.recentSectors.v1";
export const RECENT_SECTORS_CAP = 12;

export interface RecentSector {
  /** Absolute .dat path (or slf:// URI) — the navigation key. */
  datPath: string;
  /** Display name, e.g. "A9.DAT" or "The Den (A9)". Advisory; the hub
   * re-derives a fresh label from current sector-names anyway. */
  label: string;
  /** Sector grid code (e.g. "A9"), when derivable from the filename. */
  grid?: string;
  /** Epoch ms last opened (Date.now() — frontend app code, allowed). */
  openedAt: number;
}

export function readRecentSectors(): RecentSector[] {
  try {
    const raw = localStorage.getItem(RECENT_SECTORS_KEY);
    if (!raw) return [];
    const parsed = JSON.parse(raw) as RecentSector[];
    return Array.isArray(parsed) ? parsed : [];
  } catch {
    return [];
  }
}

/** Push a sector to the front of the MRU list (de-duped by datPath),
 * capped at RECENT_SECTORS_CAP. Returns the new list so a caller holding
 * it in state can update without a re-read. */
export function pushRecentSector(entry: RecentSector): RecentSector[] {
  const next = [entry, ...readRecentSectors().filter((r) => r.datPath !== entry.datPath)]
    .slice(0, RECENT_SECTORS_CAP);
  try {
    localStorage.setItem(RECENT_SECTORS_KEY, JSON.stringify(next));
  } catch {
    // Quota or parse — non-fatal; recent row just won't persist.
  }
  return next;
}

/**
 * `[clipboard, setClipboard]` persisted per (xmlPath, tileset). Rehydrates
 * on tileset switch; a same-tileset sector switch keeps the same key, so the
 * clipboard persists for cross-sector paste.
 */
export function usePersistentClipboard(
  xmlPath: string | undefined,
  tileset: number,
): [ClipboardRegion | null, Dispatch<SetStateAction<ClipboardRegion | null>>] {
  const key = bucketKey(xmlPath, tileset);
  const [clip, setClip] = useState<ClipboardRegion | null>(() => readClipboard(key));
  useEffect(() => {
    setClip(readClipboard(key));
  }, [key]);
  useEffect(() => {
    writeClipboard(key, clip);
  }, [key, clip]);
  return [clip, setClip];
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
