/**
 * Tauri command wrappers (Tauri 2.x).
 *
 * In Tauri 2 the invoke API is imported from `@tauri-apps/api/core` — the old
 * `window.__TAURI__.invoke` approach (v1) is gone. We detect "are we inside
 * Tauri?" by checking for `window.__TAURI_INTERNALS__`, which the shell
 * injects automatically.
 *
 * When running outside Tauri (browser dev mode), these helpers fall back to
 * env-var defaults so the frontend still loads for non-Tauri testing.
 */

import { invoke as tauriInvoke } from "@tauri-apps/api/core";
import { open as openDialog, save as saveDialog } from "@tauri-apps/plugin-dialog";

let cachedPort: number | null = null;
let cachedToken: string | null = null;

export function isRunningInTauri(): boolean {
  // Tauri 2 sets this global on every webview window
  return typeof (window as unknown as { __TAURI_INTERNALS__?: unknown }).__TAURI_INTERNALS__ !== "undefined";
}

/** Invalidate cached port AND token so the next call re-queries the shell.
 * Called from App.tsx when the shell emits `sidecar:restarted`. Token is
 * actually stable across respawns, but invalidating both keeps the cache
 * model simple. */
export function clearCachedPort(): void {
  cachedPort = null;
  cachedToken = null;
}

export async function getServerPort(): Promise<number> {
  if (cachedPort !== null) return cachedPort;
  if (isRunningInTauri()) {
    cachedPort = await tauriInvoke<number>("get_server_port");
    return cachedPort;
  }
  // Browser dev fallback — assume sidecar started manually on a known port
  cachedPort = Number(import.meta.env.VITE_SIDECAR_PORT ?? 8000);
  return cachedPort;
}

/** Per-session shared secret the sidecar requires on the X-MercWizard-Token
 * header. Stable across watchdog respawns. In browser dev mode (no Tauri
 * shell), returns the empty string — the sidecar skips auth when its own
 * env var is also unset. */
export async function getServerToken(): Promise<string> {
  if (cachedToken !== null) return cachedToken;
  if (isRunningInTauri()) {
    cachedToken = await tauriInvoke<string>("get_server_token");
    return cachedToken;
  }
  cachedToken = "";
  return cachedToken;
}

// ─── Last-directory persistence (auto-remember per picker type) ────
//
// Tauri's file dialog defaults to the OS-level "last folder" which is
// shared across every app. After picking a .wmerc from Downloads, the
// next pick of a .sti opens at Downloads too — even though the user
// almost certainly wants their last STI folder. Worse, Tauri's
// "last folder" doesn't even persist across our own app's restarts on
// some Windows setups; the user re-navigates from the install root
// every launch.
//
// Fix: remember the parent dir of the LAST successfully-picked file
// per "purpose", and pass it as `defaultPath` on the next pick of the
// same purpose. Purpose is keyed off either an explicit `lastDirKey`
// or the first filter extension ("sti", "slf", "wmerc", "png", …).
// Falls back to a single shared key when neither is provided.
//
// Storage: localStorage. Survives restart, scoped to the app's
// webview origin, no backend roundtrip.
const LAST_DIR_PREFIX = "mw2:lastDir:";

function lastDirStorageKey(purpose: string): string {
  // Normalize so "STI"/"sti"/".sti" all hit the same bucket.
  const norm = purpose.toLowerCase().replace(/^\./, "");
  return `${LAST_DIR_PREFIX}${norm}`;
}

function deriveLastDirKey(
  explicitKey: string | undefined,
  filters?: { name: string; extensions: string[] }[],
): string {
  if (explicitKey) return explicitKey;
  const ext = filters?.[0]?.extensions?.[0];
  if (ext) return ext;
  return "_any";
}

function readLastDir(purpose: string): string | undefined {
  try {
    const v = localStorage.getItem(lastDirStorageKey(purpose));
    return v && v.length > 0 ? v : undefined;
  } catch {
    // localStorage can throw in private-mode or quota-exceeded
    // scenarios; just behave as if no remembered dir.
    return undefined;
  }
}

function writeLastDir(purpose: string, picked: string | undefined): void {
  if (!picked) return;
  // Extract the parent dir. Works for forward and back slashes on
  // Windows; Tauri returns Windows-native paths on Windows.
  const lastSep = Math.max(picked.lastIndexOf("/"), picked.lastIndexOf("\\"));
  if (lastSep <= 0) return;
  const dir = picked.slice(0, lastSep);
  try {
    localStorage.setItem(lastDirStorageKey(purpose), dir);
  } catch {
    // ignore — quota or private-mode
  }
}

export async function pickDirectory(
  title = "Pick a folder",
  lastDirKey?: string,
): Promise<string | null> {
  if (!isRunningInTauri()) return null;
  // Pick-a-folder lacks the ext-fingerprint pickFile uses to bucket
  // its memory. Default to a generic "_dir" key when the caller
  // doesn't provide one. Callers with distinct purposes (e.g.
  // "slf-extract-dest" vs "bundle-export-dest") should pass an
  // explicit key so the dialogs remember independently.
  const purpose = lastDirKey ?? "_dir";
  const defaultPath = readLastDir(purpose);
  try {
    const result = await openDialog({
      title,
      directory: true,
      multiple: false,
      defaultPath,
    });
    if (typeof result === "string") {
      // For a directory pick, the picked path IS the dir to remember.
      try { localStorage.setItem(lastDirStorageKey(purpose), result); }
      catch { /* ignore */ }
      return result;
    }
    return null;
  } catch {
    return null;
  }
}

export async function pickFile(
  title = "Pick a file",
  filters?: { name: string; extensions: string[] }[],
  lastDirKey?: string,
): Promise<string | null> {
  if (!isRunningInTauri()) return null;
  const purpose = deriveLastDirKey(lastDirKey, filters);
  const defaultPath = readLastDir(purpose);
  try {
    const result = await openDialog({
      title,
      directory: false,
      multiple: false,
      filters,
      defaultPath,
    });
    if (typeof result === "string") {
      writeLastDir(purpose, result);
      return result;
    }
    return null;
  } catch {
    return null;
  }
}

export async function pickSaveFile(
  defaultName?: string,
  filters?: { name: string; extensions: string[] }[],
  title = "Save as...",
  lastDirKey?: string,
): Promise<string | null> {
  if (!isRunningInTauri()) return null;
  const purpose = deriveLastDirKey(lastDirKey, filters);
  const rememberedDir = readLastDir(purpose);
  // Tauri's save dialog takes a single `defaultPath` that's used as
  // both starting-dir + suggested filename. Concatenate when we have
  // a remembered dir; otherwise pass the bare filename and let the
  // dialog default to whatever Tauri remembers.
  let defaultPath: string | undefined;
  if (rememberedDir && defaultName) {
    // Use the same separator the remembered dir uses (Windows-style
    // backslash on Windows, forward elsewhere).
    const sep = rememberedDir.includes("\\") ? "\\" : "/";
    defaultPath = `${rememberedDir}${sep}${defaultName}`;
  } else if (rememberedDir) {
    defaultPath = rememberedDir;
  } else {
    defaultPath = defaultName;
  }
  try {
    const result = await saveDialog({ title, defaultPath, filters });
    if (typeof result === "string") {
      writeLastDir(purpose, result);
      return result;
    }
    return null;
  } catch {
    return null;
  }
}
