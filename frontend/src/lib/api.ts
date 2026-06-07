/**
 * HTTP client for the Python sidecar.
 *
 * All endpoints under /api/v1. The port is discovered via the Tauri shell;
 * see `tauri.ts:getServerPort()`.
 */

import { getServerPort, getServerToken } from "./tauri";
import type {
  AimBinding,
  AuditIssue,
  BackupEntry,
  Gear,
  InstallInfo,
  Merc,
  RosterEntry,
} from "./schema";

// ───────────────────────────────────────────────────────────────────────
//  Error type
// ───────────────────────────────────────────────────────────────────────

export class ApiError extends Error {
  status: number;
  detail: unknown;
  constructor(status: number, detail: unknown, message?: string) {
    super(message ?? `API error ${status}`);
    this.status = status;
    this.detail = detail;
  }
}

// ───────────────────────────────────────────────────────────────────────
//  Error message catalog
// ───────────────────────────────────────────────────────────────────────

// Map of sidecar error codes → user-friendly text. Anything not in here
// falls through to the raw `message` field from the response.
// Centralized friendly-text map for sidecar error codes. Codes not in here
// fall through to the raw `message` field from the response. When the sidecar
// adds a new error code, add a row here so the UI doesn't surface the bare
// code identifier. Audit-issue codes (FIELD_TOO_LONG, TYPE_NO_AIM_ROW, etc.)
// are NOT in this map — those carry their own human-readable `message` field
// straight from the audit Issue model.
const ERROR_MESSAGES: Record<string, string> = {
  // ── Install lifecycle ─────────────────────────────────────────────────
  NO_ACTIVE_INSTALL: "No JA2 install is active. Pick one from Settings or run the First Run flow.",
  // The backend appends the specific validation failures (missing JA2.exe,
  // missing MercProfiles.xml, VFS config errors, etc.) into the `message`
  // field so they surface in the parenthetical.
  INVALID_INSTALL: "Not a valid JA2 1.13 install",
  INSTALL_NOT_FOUND: "That install isn't registered. Refresh installs in Settings.",
  // Two backend cases now produce distinct messages — friendly text just
  // names the category.
  INVALID_TARGET_INSTALL: "Target install isn't usable",
  PATH_NOT_DIR: "That path isn't a folder. Pick the install's root directory (the one that contains JA2.exe).",
  VFS_CONFIG_NOT_FOUND: "The chosen vfs_config.*.ini file doesn't exist at that path. Re-pick from the FirstRun wizard.",
  NO_VFS_CONFIG: "This install has no specific vfs_config bound — nothing to apply. Pick a mod profile in the FirstRun wizard first.",
  EXE_NOT_FOUND: "Couldn't find ja2.exe under the active install. Check the install path in Settings.",

  // ── INI editor ───────────────────────────────────────────────────────
  GAME_RUNNING: "JA2 is running — the engine rewrites its config files on save/exit, so INI edits are blocked until you close the game.",
  PLAY_MODE_UNSUPPORTED: "This file has no per-campaign override mechanism in the engine. Switch to Author mode to edit it (changes the shipped file).",
  VFS_CONFIG_BROKEN: "This install's VFS config couldn't be parsed — fix Ja2.ini's VFS_CONFIG_INI line (or the vfs_config file) before editing.",
  BASELINE_NOT_FOUND: "The reference-install path doesn't exist. Update it in Settings.",
  INI_FILE_UNKNOWN: "That isn't one of the editable INI files.",
  SCHEMA_NOT_FOUND: "No schema is bundled for that INI file.",
  JA2_INI_NOT_FOUND: "No Ja2.ini found at the install root.",
  BAD_VALUE: "That value can't be written (newlines aren't allowed).",
  BAD_SECTION: "Invalid section name.",
  BAD_KEY: "Invalid key name.",
  BAD_TARGET: "Internal: unknown write target.",
  WRITE_SELFCHECK_FAILED: "The write didn't verify cleanly and was rolled back — the file is unchanged. Check the file isn't locked, then retry.",
  NO_PROFILE_FOLDER: "This campaign has no profile folder yet — launch the game once to create it.",

  // ── Auth ─────────────────────────────────────────────────────────────
  UNAUTHORIZED: "The sidecar rejected the auth token. Restart Merc Forge so the shell and sidecar share a fresh token.",

  // ── Slot / merc CRUD ─────────────────────────────────────────────────
  SLOT_EMPTY: "That slot is empty — pick a slot with a merc in it.",
  SLOT_OCCUPIED: "Target slot already holds a merc. Use the 'overwrite' option to replace, or pick an empty slot.",
  SLOT_MISMATCH: "Slot mismatch between URL and payload. Reload the page and try again.",
  AUDIT_FAILED: "Engine-correctness checks blocked this write — see the issue list below.",
  NO_GEAR_FOR_SLOT: "No starting gear is defined for that slot.",
  PORTRAIT_NOT_FOUND: "The slot's portrait STI is missing from disk. Re-export the merc or pick a different face index.",
  PORTRAIT_DECODE_FAILED: "Couldn't decode the slot's portrait STI",

  // ── Move / Duplicate (streaming since 2026-05-23) ────────────────────
  // *_INVALID is a pre-write rejection — the sidecar's `message` field carries
  // the exact reason (e.g. "Source slot 5 is empty", "Destination slot 198 is
  // occupied"). Keep the friendly text as a bare label so formatApiError
  // appends the specific reason in parens rather than overriding it with a
  // guess.
  MOVE_INVALID: "Move blocked",
  MOVE_FAILED: "Move failed partway through. A backup was taken before the write — restore it from the Backups page if anything looks wrong in-game.",
  DUPLICATE_INVALID: "Duplicate blocked",
  DUPLICATE_FAILED: "Duplicate failed partway through. A backup was taken before the write — restore it from the Backups page if anything looks wrong in-game.",

  // ── Bundles (.wmerc) ─────────────────────────────────────────────────
  BUNDLE_NOT_FOUND: "Couldn't find the .wmerc file at that path.",
  BUNDLE_INVALID: "That .wmerc bundle is corrupt or not a valid bundle.",
  EXPORT_FAILED: "Couldn't write the .wmerc bundle",

  // ── Backup ───────────────────────────────────────────────────────────
  BACKUP_NOT_FOUND: "That backup snapshot doesn't exist (or was deleted).",

  // ── Voice ────────────────────────────────────────────────────────────
  CLIP_NOT_FOUND: "Voice clip not found.",

  // ── Save / Create rollback ───────────────────────────────────────────
  SAVE_FAILED: "Save failed partway through. The install was rolled back to its previous state — check the Backups page if anything looks wrong in-game.",
  SAVE_FAILED_ROLLBACK_FAILED: "Save failed AND the automatic rollback also failed. Use the Backups page to restore the snapshot manually.",
  CREATE_FAILED: "Create failed partway through. The install was rolled back to its previous state — check the Backups page if anything looks wrong in-game.",
  CREATE_FAILED_ROLLBACK_FAILED: "Create failed AND the automatic rollback also failed. Use the Backups page to restore the snapshot manually.",

  // ── Catch-all ────────────────────────────────────────────────────────
  INTERNAL_ERROR: "Something went wrong inside the sidecar. The log is at %APPDATA%\\MercWizard\\logs\\sidecar.log — open it and look for the most recent traceback.",
};

interface ApiErrorDetail {
  detail?: {
    error?: string;
    message?: string;
    issues?: unknown[];
    slot?: number;
    error_step?: string;
    steps_completed?: string[];
  };
}

/** FastAPI's RequestValidationError (HTTP 422) returns `detail` as a list
 * of `{loc, msg, type}` records — different shape than the sidecar's
 * own error envelope. Pull out the first 1-2 messages so the user sees
 * "biographyText: ensure this value has at most 400 characters" instead
 * of just "Request failed (HTTP 422)."
 */
interface FastApiValidationError {
  loc: (string | number)[];
  msg: string;
  type: string;
  input?: unknown;
}

function format422Detail(detail: unknown): string | null {
  if (!Array.isArray(detail) || detail.length === 0) return null;
  const errors = detail as FastApiValidationError[];
  // Show the first 2 errors max — beyond that gets noisy. Skip the
  // "body" / "merc" prefix of loc since every error has it.
  const lines = errors.slice(0, 2).map((e) => {
    const fieldPath = e.loc
      .filter((p) => p !== "body" && p !== "merc")
      .join(".");
    return fieldPath ? `${fieldPath}: ${e.msg}` : e.msg;
  });
  const suffix = errors.length > 2 ? ` (+${errors.length - 2} more)` : "";
  return lines.join("; ") + suffix;
}

/**
 * Convert any caught error into a user-readable single-line message.
 * Handles:
 *  - ApiError with structured sidecar error codes (preferred mapping)
 *  - ApiError with no code (falls back to raw message)
 *  - FastAPI validation errors (HTTP 422)
 *  - Network / fetch failures (sidecar down, abort, etc.)
 *  - Anything else (returns "Unknown error.")
 */
export function formatApiError(err: unknown): string {
  if (err instanceof ApiError) {
    // FastAPI 422: detail is an array of validation errors. Surface
    // the offending field + message so the user knows what to fix.
    if (err.status === 422) {
      const rawDetail = (err.detail as { detail?: unknown } | null)?.detail;
      const formatted = format422Detail(rawDetail);
      if (formatted) return `Validation failed — ${formatted}`;
    }
    const inner = (err.detail as ApiErrorDetail | null)?.detail;
    if (inner && !Array.isArray(inner)) {
      const code = inner.error;
      const friendly = code ? ERROR_MESSAGES[code] : undefined;
      // Append message if both present and adds info
      if (friendly && inner.message && !friendly.toLowerCase().includes(inner.message.toLowerCase())) {
        return `${friendly} (${inner.message})`;
      }
      if (friendly) return friendly;
      if (inner.message) return inner.message;
      if (code) return code;
    }
    if (err.status === 0) {
      return "Couldn't reach the sidecar. Try restarting Merc Forge — the background process may have stopped.";
    }
    return `Request failed (HTTP ${err.status}).`;
  }
  if (err instanceof TypeError && err.message.includes("fetch")) {
    // Same root cause as ApiError status=0 — the sidecar is unreachable.
    // Different surface (raw fetch error) because the fetch never produced
    // a Response object. Most common cause: the sidecar crashed and the
    // shell hasn't respawned it yet, or the cached port is stale after a
    // manual launch_current.ps1 restart.
    return "Couldn't reach the sidecar. Try restarting Merc Forge — the background process may have stopped, or the port cache is stale after a manual rebuild.";
  }
  // Reader-level network errors during a streaming response. The fetch
  // already returned a Response (so it's not the "couldn't reach" case
  // above), but the body reader's read() threw mid-stream — the sidecar
  // most likely crashed partway through, the cross-process lock got
  // released abruptly, or the Tauri webview lost the IPC channel.
  // Pre-fix this surfaced as a raw "network error" / "NetworkError"
  // string in the SaveProgressBar's fallback — a user hit it
  // on a Duplicate after the backup step. Surface actionable text
  // instead of the cryptic raw message.
  if (
    err instanceof Error &&
    /^(network error|NetworkError|net::ERR_|Failed to fetch)/i.test(err.message)
  ) {
    return (
      "Connection to the sidecar dropped mid-operation. Whatever finished "
      + "before the drop is committed on disk; whatever was in progress was "
      + "rolled back to the last good state. Check the Backups page if you "
      + "need to verify, then try the operation again. If it keeps failing, "
      + "restart Merc Forge — the sidecar may have crashed."
    );
  }
  // AbortError surfaces as `DOMException: signal is aborted without reason`
  // when our fetchWithTimeout fires its AbortController. Replace the raw
  // message with something the user can act on.
  if (
    err instanceof Error &&
    (err.name === "AbortError" || /aborted/i.test(err.message))
  ) {
    return "The request timed out. The sidecar may still be working on it — wait a moment, then check the Backups page before retrying.";
  }
  if (err instanceof Error) {
    return err.message || "Unknown error.";
  }
  return "Unknown error.";
}

/** Pull the audit issues out of a 400 AUDIT_FAILED response, if present. */
export function extractAuditIssues(err: unknown): { code: string; message: string; severity: string }[] {
  if (!(err instanceof ApiError)) return [];
  const inner = (err.detail as ApiErrorDetail | null)?.detail;
  if (!inner?.issues || !Array.isArray(inner.issues)) return [];
  return inner.issues as { code: string; message: string; severity: string }[];
}

let baseUrl: string | null = null;

/** Invalidate the cached base URL so the next call re-discovers the port.
 * Pair with `clearCachedPort()` on `sidecar:restarted`. */
export function clearApiBaseCache(): void {
  baseUrl = null;
}

async function getBaseUrl(): Promise<string> {
  if (baseUrl) return baseUrl;
  const port = await getServerPort();
  baseUrl = `http://127.0.0.1:${port}/api/v1`;
  return baseUrl;
}

/**
 * Public wrapper around `getBaseUrl` for callers that need to build URLs
 * directly (e.g. `<img src>` attributes). Result is cached after first
 * resolution; safe to call from inside a React component on every render.
 */
export function getApiBaseUrl(): Promise<string> {
  return getBaseUrl();
}

/**
 * Build an authenticated media URL for `<img>` / `<audio>` / `<video>`
 * elements. These can't attach the X-MercWizard-Token header (browser
 * limitation on element-driven loads), so the token rides as a
 * `?_t=<token>` query param that the sidecar's auth middleware also
 * accepts. A user-reported bug: roster portraits + Edit BigFace + voice
 * playback all 401'd silently before this existed.
 *
 * `pathWithQs` should already include any non-auth query params the
 * route needs (e.g. `?size=bigface&v=...`). The helper appends `_t`
 * with the right separator.
 */
export async function mediaUrl(pathWithQs: string): Promise<string> {
  const base = await getBaseUrl();
  const token = await getServerToken();
  if (!token) return `${base}${pathWithQs}`;
  const sep = pathWithQs.includes("?") ? "&" : "?";
  return `${base}${pathWithQs}${sep}_t=${encodeURIComponent(token)}`;
}

/** Headers to send on every sidecar request. Token is empty in browser dev
 * mode (the sidecar also skips auth when its env var is unset). */
async function authHeaders(): Promise<Record<string, string>> {
  const token = await getServerToken();
  return token ? { "X-MercWizard-Token": token } : {};
}

const LONG_OP_TIMEOUT_MS = 5 * 60 * 1000;

/** Wraps fetch with an AbortController that times out after `ms`. */
async function fetchWithTimeout(
  url: string,
  init: RequestInit,
  ms = LONG_OP_TIMEOUT_MS,
): Promise<Response> {
  const controller = new AbortController();
  const handle = setTimeout(() => controller.abort(), ms);
  try {
    return await fetch(url, { ...init, signal: controller.signal });
  } finally {
    clearTimeout(handle);
  }
}

// Most routes are fast (XML/EDT writes, roster reads). A short default
// timeout (30s) keeps the UI responsive when the sidecar wedges. Long-running
// routes (portrait compile, bundle import, voice upload) opt into the
// longer LONG_OP_TIMEOUT_MS via their own fetchWithTimeout calls.
const DEFAULT_REQUEST_TIMEOUT_MS = 30 * 1000;

async function request<T>(
  path: string,
  opts?: RequestInit,
  timeoutMs: number = DEFAULT_REQUEST_TIMEOUT_MS,
): Promise<T> {
  const base = await getBaseUrl();
  const auth = await authHeaders();
  const res = await fetchWithTimeout(
    `${base}${path}`,
    {
      ...opts,
      headers: { "Content-Type": "application/json", ...auth, ...(opts?.headers ?? {}) },
    },
    timeoutMs,
  );
  if (!res.ok) {
    let detail: unknown = null;
    try {
      detail = await res.json();
    } catch {
      // not JSON
    }
    throw new ApiError(res.status, detail);
  }
  if (res.status === 204) return undefined as T;
  return (await res.json()) as T;
}

// ───────────────────────────────────────────────────────────────────────
//  Slot locks (static — engine-source-derived)
// ───────────────────────────────────────────────────────────────────────

export interface SlotLockInfoApi {
  slot: number;
  tier: "safe" | "vanilla_overwrite" | "quest_bound" | "locked";
  name: string | null;
  role: string | null;
}

export function getSlotLocks() {
  return request<SlotLockInfoApi[]>("/slots/locks");
}

// ───────────────────────────────────────────────────────────────────────
//  Slot picker (engine-faithful — joins live XML rows + named-slot table)
// ───────────────────────────────────────────────────────────────────────

export type SlotPickerTier = "safe" | "vanilla_overwrite" | "quest_bound" | "locked";
export type SlotPickerCategory = "aim" | "merc" | "rpc" | "npc" | "locked" | "unassigned";

export interface AimRowInfoApi {
  present: boolean;
  ProfilId: number | null;
  AimBioID: number | null;
  description: string | null;
}

export interface MercRowInfoApi {
  present: boolean;
  ProfilId: number | null;
  MercBioID: number | null;
  Name: string | null;
  uiIndex: number | null;
}

export interface SlotInfoApi {
  slot: number;
  tier: SlotPickerTier;
  category: SlotPickerCategory;
  is_empty: boolean;
  engine_name: string | null;
  engine_role: string | null;
  profile_name: string | null;
  profile_nickname: string | null;
  profile_type: number | null;
  aim_row: AimRowInfoApi;
  merc_row: MercRowInfoApi;
}

export interface SlotPickerResponseApi {
  slots: SlotInfoApi[];
  engine_flags: {
    is_ub: boolean;
    reads_profiles_from_xml: boolean;
  };
  aim_row_count: number;
  merc_row_count: number;
  laptop_aim_display_cap: number;
}

export function getSlotPicker(install_id?: string) {
  const qs = install_id ? `?install_id=${encodeURIComponent(install_id)}` : "";
  return request<SlotPickerResponseApi>(`/slots/picker${qs}`);
}

// ───────────────────────────────────────────────────────────────────────
//  Health
// ───────────────────────────────────────────────────────────────────────

export function getHealth() {
  return request<{
    ok: boolean;
    version: string;
    install_count: number;
    active_install_id: string | null;
    /** Bug-review B5: true when the active install's bound
     * `vfs_config_path` doesn't match the VFS_CONFIG_INI line currently
     * in its Ja2.ini. Null when no install is active or the sidecar
     * couldn't compute the comparison. Drives the Hub's
     * VfsMismatchBanner + its "Apply VFS to Ja2.ini" CTA. */
    vfs_mismatch: boolean | null;
  }>("/health");
}

// ───────────────────────────────────────────────────────────────────────
//  Installs
// ───────────────────────────────────────────────────────────────────────

export function listInstalls() {
  return request<InstallInfo[]>("/installs");
}

export function addInstall(
  path: string,
  preferred_vfs_config_path?: string | null,
) {
  return request<InstallInfo>("/installs", {
    method: "POST",
    body: JSON.stringify({
      path,
      preferred_vfs_config_path: preferred_vfs_config_path ?? null,
    }),
  });
}

/** One detected `vfs_config.*.ini` in a candidate install folder.
 *  Returned by `scanVfsConfigs`; consumed by FirstRun's picker. */
export interface VfsConfigEntry {
  /** Absolute path to the config file on disk. Passed back to
   * `addInstall` as `preferred_vfs_config_path` once the user picks. */
  path: string;
  /** Path relative to the install root, with forward slashes. */
  relative_path: string;
  /** Display-friendly mod name extracted from the filename (e.g. "AIMNAS"). */
  mod_name: string;
  /** True when this config is the one currently named in JA2.ini's
   * VFS_CONFIG_INI line. Picking it is a no-op — no save-game warning,
   * no JA2.ini mutation. */
  is_active: boolean;
  /** True for the bundled 1.13 fallback configs (JA2113, UB113, etc.).
   * The UI flags these with a "stock" badge — they're rarely what a
   * modder is looking for. */
  is_stock: boolean;
}

export interface ScanVfsConfigsResponse {
  install_path: string;
  configs: VfsConfigEntry[];
  /** The raw VFS_CONFIG_INI value read from JA2.ini, with forward
   * slashes. Null if JA2.ini is missing or has no VFS_CONFIG_INI line. */
  active_relative_path: string | null;
}

/** Preflight scan: which `vfs_config.*.ini` files live in this folder,
 * and which one is currently active in JA2.ini. The FirstRun VFS
 * Selector Wizard uses this to render the mod-profile picker before
 * the user commits to registering the install. */
export function scanVfsConfigs(path: string) {
  return request<ScanVfsConfigsResponse>("/installs/scan-vfs-configs", {
    method: "POST",
    body: JSON.stringify({ path }),
  });
}

export function setActiveInstall(install_id: string) {
  return request<{ active_install_id: string | null }>("/installs/active", {
    method: "POST",
    body: JSON.stringify({ install_id }),
  });
}

/** Response from POST /installs/{install_id}/apply-vfs-config. */
export interface ApplyVfsResult {
  install_id: string;
  ja2_ini_path: string;
  vfs_config_written: string;
  backup_path: string | null;
  /** True when a `.mwbak` backup already existed (i.e. this isn't the
   * first time we've applied VFS to this install). False = this call
   * just created the backup, so the user has a clean restore point. */
  already_active: boolean;
}

/** Explicit "write VFS_CONFIG_INI to Ja2.ini" action. Bug #11 fix:
 * activation no longer mutates Ja2.ini silently — the user has to
 * click a Hub button (which calls this) to apply the VFS config they
 * picked. A .mwbak backup is taken on first apply per install so the
 * user can restore their original config if needed. */
export function applyVfsConfig(install_id: string) {
  return request<ApplyVfsResult>(
    `/installs/${encodeURIComponent(install_id)}/apply-vfs-config`,
    { method: "POST" },
  );
}

export function refreshInstalls() {
  // The install scan synchronously walks every fixed disk for `Jagged
  // Alliance*` folders, validates each, reads multiple vfs_configs per
  // install, and parses each install's PE / changelog for the engine
  // revision. On a many-drive / many-mod setup this can take 20-30s,
  // which trips the 30s default timeout. Use LONG_OP_TIMEOUT_MS (5 min)
  // so the request waits for the real scan to finish instead of
  // aborting with "signal is aborted without reason".
  return request<InstallInfo[]>("/installs/refresh", { method: "POST" }, LONG_OP_TIMEOUT_MS);
}

// ───────────────────────────────────────────────────────────────────────
//  Roster
// ───────────────────────────────────────────────────────────────────────

export function getRoster(install_id?: string) {
  const qs = install_id ? `?install_id=${encodeURIComponent(install_id)}` : "";
  return request<RosterEntry[]>(`/roster${qs}`);
}

// ─── Roster portrait sprite-sheet ────────────────────────────────────
// Replaces the N+1 per-slot `/merc/{slot}/portrait` fetches with ONE
// PNG (every filled slot packed into a 16-col grid) + ONE JSON manifest
// (slot → grid position). Mirrors the MapForge palette pattern.
// User feedback: "i want it to be fast".
//
// Frontend usage:
//   const sheet = await getRosterPortraitSheet();
//   const cellsBySlot = new Map(sheet.manifest.cells.map((c) => [c.slot, c]));
//   // For each filled slot, render:
//   //   <div style={{
//   //     backgroundImage: `url(${sheet.blobUrl})`,
//   //     backgroundPosition: `-${cell.x}px -${cell.y}px`,
//   //     width: sheet.manifest.cell_w, height: sheet.manifest.cell_h,
//   //   }} />

export interface PortraitSheetCell {
  slot: number;
  face_index: number;
  x: number;
  y: number;
}

export interface PortraitSheetManifest {
  size: string;
  cell_w: number;
  cell_h: number;
  sheet_w: number;
  sheet_h: number;
  cells: PortraitSheetCell[];
  errors: { slot: number; face_index: number; reason: string }[];
}

export interface PortraitSheet {
  /** Blob URL the caller owns — revoke on unmount / refetch. */
  blobUrl: string;
  manifest: PortraitSheetManifest;
}

/** Fetch the roster portrait sprite sheet + manifest in parallel.
 *  Caller is responsible for `URL.revokeObjectURL(blobUrl)` when done. */
export async function getRosterPortraitSheet(opts?: {
  install_id?: string;
  size?: string;
  cacheBust?: string | number;
}): Promise<PortraitSheet> {
  const base = await getBaseUrl();
  const token = await getServerToken();
  const params = new URLSearchParams();
  if (opts?.install_id) params.set("install_id", opts.install_id);
  if (opts?.size) params.set("size", opts.size);
  if (opts?.cacheBust !== undefined) params.set("_", String(opts.cacheBust));
  const qs = params.toString() ? `?${params.toString()}` : "";
  const headers: Record<string, string> = {};
  if (token) headers["X-MercWizard-Token"] = token;

  // Fetch both in parallel — they share the same on-disk cache so the
  // second call hits the in-memory cache without re-baking.
  const [pngRes, jsonRes] = await Promise.all([
    fetch(`${base}/roster/portrait-sheet.png${qs}`, { headers }),
    fetch(`${base}/roster/portrait-sheet.json${qs}`, { headers }),
  ]);
  if (!pngRes.ok) {
    let detail: unknown = null;
    try { detail = await pngRes.json(); } catch {}
    throw new ApiError(pngRes.status, detail);
  }
  if (!jsonRes.ok) {
    let detail: unknown = null;
    try { detail = await jsonRes.json(); } catch {}
    throw new ApiError(jsonRes.status, detail);
  }
  const pngBlob = await pngRes.blob();
  const blobUrl = URL.createObjectURL(pngBlob);
  const manifest = (await jsonRes.json()) as PortraitSheetManifest;
  return { blobUrl, manifest };
}

export function getSlot(slot: number, install_id?: string) {
  const qs = install_id ? `?install_id=${encodeURIComponent(install_id)}` : "";
  return request<{
    slot: number;
    profile: Record<string, string>;
    aim_binding: AimBinding | null;
    gear: Gear | null;
  }>(`/roster/${slot}${qs}`);
}

// ───────────────────────────────────────────────────────────────────────
//  Merc CRUD
// ───────────────────────────────────────────────────────────────────────

export interface CreateMercPayload {
  merc: Merc;
  gear?: Gear;
  aim_binding?: AimBinding;
  force?: boolean;
}

export function createMerc(payload: CreateMercPayload, install_id?: string) {
  const qs = install_id ? `?install_id=${encodeURIComponent(install_id)}` : "";
  return request<{ ok: boolean; slot: number; issues: AuditIssue[] }>(
    `/merc${qs}`,
    { method: "POST", body: JSON.stringify(payload) }
  );
}

export function updateMerc(
  slot: number,
  payload: { merc?: Merc; gear?: Gear; aim_binding?: AimBinding },
  install_id?: string
) {
  const qs = install_id ? `?install_id=${encodeURIComponent(install_id)}` : "";
  return request<{ ok: boolean; slot: number }>(`/merc/${slot}${qs}`, {
    method: "PUT",
    body: JSON.stringify(payload),
  });
}

// ───────────────────────────────────────────────────────────────────────
//  Streaming PUT /merc/{slot} (NDJSON; emits one event per save step)
// ───────────────────────────────────────────────────────────────────────

/** One event from the save-progress stream. Mirrors the sidecar's emit shape.
 * Named `SaveProgressEvent` to avoid collision with the browser's built-in
 * `ProgressEvent` interface.
 *
 * Reused across:
 *   - PUT /merc/{slot}                (update_merc)
 *   - POST /merc/{slot}/duplicate     (duplicate_merc)
 *   - POST /merc/{slot}/move          (move_merc — planned)
 * The `step` enum is the union of every emitter's set of step names; not
 * every route emits all of them. */
export interface SaveProgressEvent {
  /** Per-step events have a `step` name. Done events have `done: true`. */
  step?: "backup" | "profiles" | "edt" | "aim_avail" | "merc_avail" | "gear" | "copy" | "move";
  /** "start" before the step runs, "progress" mid-step (only backup emits these),
   * "done" after it completes. */
  status?: "start" | "progress" | "done";
  /** Human-readable label, e.g. "Backing up files...". */
  label?: string;
  /** Set on `status: "progress"` events from the backup loop. */
  index?: number;
  total?: number;

  /** Final event. */
  done?: boolean;
  ok?: boolean;
  slot?: number;
  /** Duplicate/Move final-event extras. */
  from?: number;
  to?: number;
  to_install_id?: string;
  cross_install?: boolean;
  /** Cross-install move's bundle-pipeline report. Same shape as the legacy
   * non-streaming /move response.report — kept so the Move.tsx success card
   * can show "X voice clips copied, portrait STIs compiled" etc. */
  report?: {
    source_install_root?: string;
    target_install_root?: string;
    files_written?: string[];
    portrait_compiled?: boolean;
    voice_clips_copied?: number;
    aim_bio_id_used?: number | null;
    source_backup_id?: string | null;
    issues?: AuditIssue[];
    partial_failures?: string[];
  };

  /** Error path (when `done: true, ok: false`). */
  error?: string;
  error_step?: string;
  steps_completed?: string[];
  backup_id?: string | null;
  rollback_ok?: boolean;
  rollback_error?: string | null;
  message?: string;
}

/** PUT /merc/{slot} with NDJSON streaming progress events.
 *
 * Calls `onProgress(event)` for each line in the response stream. Resolves
 * with the final `{done: true, ok: true, slot}` event on success. Throws
 * `ApiError(500, ...)` on a `done: true, ok: false` event so the standard
 * React Query error path picks it up — `formatApiError` reads the
 * `error` / `error_step` / `steps_completed` / `message` fields out of the
 * detail and renders a friendly message via `ERROR_MESSAGES`.
 *
 * Uses `LONG_OP_TIMEOUT_MS` (5 min) instead of the 30s default because
 * backup on large modded installs can exceed 30s — the latent timeout bug
 * the streaming refactor incidentally fixes. */
export async function updateMercStreaming(
  slot: number,
  payload: { merc?: Merc; gear?: Gear; aim_binding?: AimBinding },
  install_id: string | undefined,
  onProgress: (ev: SaveProgressEvent) => void,
): Promise<SaveProgressEvent> {
  const base = await getBaseUrl();
  const auth = await authHeaders();
  const qs = install_id ? `?install_id=${encodeURIComponent(install_id)}` : "";
  const url = `${base}/merc/${slot}${qs}`;

  const res = await fetchWithTimeout(
    url,
    {
      method: "PUT",
      headers: { "Content-Type": "application/json", ...auth },
      body: JSON.stringify(payload),
    },
    LONG_OP_TIMEOUT_MS,
  );

  if (!res.ok) {
    // Non-streaming error path — e.g. 400 AUDIT_FAILED before stream opens.
    let detail: unknown = null;
    try {
      detail = await res.json();
    } catch {
      // not JSON
    }
    throw new ApiError(res.status, detail);
  }

  const reader = res.body?.getReader();
  if (!reader) {
    throw new ApiError(500, { detail: { error: "INTERNAL_ERROR", message: "Response had no readable body." } });
  }

  const decoder = new TextDecoder("utf-8");
  let buffer = "";
  let finalEvent: SaveProgressEvent | null = null;

  while (true) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });

    // Process complete lines (NDJSON: one JSON object per newline)
    let newlineIdx = buffer.indexOf("\n");
    while (newlineIdx !== -1) {
      const line = buffer.slice(0, newlineIdx).trim();
      buffer = buffer.slice(newlineIdx + 1);
      if (line) {
        try {
          const ev = JSON.parse(line) as SaveProgressEvent;
          onProgress(ev);
          if (ev.done) {
            finalEvent = ev;
          }
        } catch {
          // Malformed line — skip
        }
      }
      newlineIdx = buffer.indexOf("\n");
    }
  }

  // Handle any trailing buffered content (in case the last line had no newline)
  if (buffer.trim()) {
    try {
      const ev = JSON.parse(buffer.trim()) as SaveProgressEvent;
      onProgress(ev);
      if (ev.done) {
        finalEvent = ev;
      }
    } catch {
      // Malformed final fragment — skip
    }
  }

  if (!finalEvent) {
    throw new ApiError(500, {
      detail: {
        error: "INTERNAL_ERROR",
        message: "Save stream closed without a 'done' event.",
      },
    });
  }

  if (!finalEvent.ok) {
    // Build an ApiError that formatApiError + extractAuditIssues understand.
    throw new ApiError(500, {
      detail: {
        error: finalEvent.error ?? "SAVE_FAILED",
        message: finalEvent.message,
        error_step: finalEvent.error_step,
        steps_completed: finalEvent.steps_completed,
      },
    });
  }

  return finalEvent;
}

export function deleteMerc(slot: number, install_id?: string) {
  const qs = install_id ? `?install_id=${encodeURIComponent(install_id)}` : "";
  return request<{ ok: boolean }>(`/merc/${slot}${qs}`, { method: "DELETE" });
}

/**
 * Resolve the absolute URL for a slot's portrait thumbnail. Uses an async
 * one-shot resolver because the sidecar port is discovered at startup.
 *
 * Resolves to `null` when no install is registered (the request would 400)
 * — callers should fall back to rendering slot number alone.
 *
 * The endpoint returns:
 *   - 200 image/png : decoded face STI frame[0]
 *   - 204            : ubFaceIndex=0 ("no portrait" vanilla convention)
 *   - 404            : empty slot OR face STI missing on disk
 *
 * `<img>` only fires `onLoad` for status 200 — 204 / 404 trip `onError`,
 * so the cell can fall back to the slot number without further branching.
 */
export async function getMercPortraitUrl(
  slot: number,
  opts?: { install_id?: string; size?: "smallface" | "face_65" | "face_33" | "bigface" },
): Promise<string> {
  const base = await getBaseUrl();
  const params = new URLSearchParams();
  if (opts?.install_id) params.set("install_id", opts.install_id);
  if (opts?.size) params.set("size", opts.size);
  const qs = params.toString();
  return `${base}/merc/${slot}/portrait${qs ? `?${qs}` : ""}`;
}

export interface MoveResult {
  ok: boolean;
  from: number;
  to: number;
  to_install_id?: string;
  cross_install?: boolean;
  steps?: string[];
  report?: {
    source_install_root: string;
    target_install_root: string;
    files_written: string[];
    portrait_compiled: boolean;
    voice_clips_copied: number;
    aim_bio_id_used: number | null;
    source_backup_id: string | null;
    issues: AuditIssue[];
    partial_failures: string[];
  };
}

/** POST /merc/{slot}/move with NDJSON streaming progress.
 *
 * Streaming since 2026-05-23 (same refactor as /duplicate). The same-install
 * branch emits backup + move events; the cross-install branch emits one
 * coarse "move" step (the bundle pipeline doesn't surface internal steps).
 * Both end with `{done: True, ok, from, to, ...}`. */
export async function moveMercStreaming(
  slot: number,
  to_slot: number,
  opts: { install_id?: string; to_install_id?: string; force?: boolean } | undefined,
  onProgress: (ev: SaveProgressEvent) => void,
): Promise<SaveProgressEvent> {
  const base = await getBaseUrl();
  const auth = await authHeaders();
  const qs = opts?.install_id ? `?install_id=${encodeURIComponent(opts.install_id)}` : "";
  const url = `${base}/merc/${slot}/move${qs}`;

  const res = await fetchWithTimeout(
    url,
    {
      method: "POST",
      headers: { "Content-Type": "application/json", ...auth },
      body: JSON.stringify({
        to_slot,
        to_install_id: opts?.to_install_id,
        force: opts?.force ?? false,
      }),
    },
    LONG_OP_TIMEOUT_MS,
  );

  if (!res.ok) {
    let detail: unknown = null;
    try {
      detail = await res.json();
    } catch {
      // not JSON
    }
    throw new ApiError(res.status, detail);
  }

  const reader = res.body?.getReader();
  if (!reader) {
    throw new ApiError(500, {
      detail: { error: "INTERNAL_ERROR", message: "Response had no readable body." },
    });
  }

  const decoder = new TextDecoder("utf-8");
  let buffer = "";
  let finalEvent: SaveProgressEvent | null = null;

  while (true) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    let newlineIdx = buffer.indexOf("\n");
    while (newlineIdx !== -1) {
      const line = buffer.slice(0, newlineIdx).trim();
      buffer = buffer.slice(newlineIdx + 1);
      if (line) {
        try {
          const ev = JSON.parse(line) as SaveProgressEvent;
          onProgress(ev);
          if (ev.done) finalEvent = ev;
        } catch {
          // Malformed line — skip
        }
      }
      newlineIdx = buffer.indexOf("\n");
    }
  }
  if (buffer.trim()) {
    try {
      const ev = JSON.parse(buffer.trim()) as SaveProgressEvent;
      onProgress(ev);
      if (ev.done) finalEvent = ev;
    } catch {
      // Malformed final fragment — skip
    }
  }

  if (!finalEvent) {
    throw new ApiError(500, {
      detail: {
        error: "INTERNAL_ERROR",
        message: "Move stream closed without a 'done' event.",
      },
    });
  }
  if (!finalEvent.ok) {
    throw new ApiError(500, {
      detail: {
        error: finalEvent.error ?? "MOVE_FAILED",
        message: finalEvent.message,
        error_step: finalEvent.error_step,
        steps_completed: finalEvent.steps_completed,
      },
    });
  }
  return finalEvent;
}

/** POST /merc/{slot}/duplicate with NDJSON streaming progress events.
 *
 * Same envelope as `updateMercStreaming`. Throws `ApiError(500, ...)` on a
 * `{done: true, ok: false}` event so React Query's error path picks it up
 * and `formatApiError` can resolve the `DUPLICATE_FAILED` / `DUPLICATE_INVALID`
 * codes against the ERROR_MESSAGES map.
 *
 * Uses LONG_OP_TIMEOUT_MS — backup on a heavily-modded install (Wasteland,
 * AIMNAS, etc.) can take well over the 30s default that the pre-streaming version
 * was using, which surfaced as "Couldn't reach the sidecar" 2026-05-23. */
export async function duplicateMercStreaming(
  slot: number,
  to_slot: number,
  install_id: string | undefined,
  onProgress: (ev: SaveProgressEvent) => void,
): Promise<SaveProgressEvent> {
  const base = await getBaseUrl();
  const auth = await authHeaders();
  const qs = install_id ? `?install_id=${encodeURIComponent(install_id)}` : "";
  const url = `${base}/merc/${slot}/duplicate${qs}`;

  const res = await fetchWithTimeout(
    url,
    {
      method: "POST",
      headers: { "Content-Type": "application/json", ...auth },
      body: JSON.stringify({ to_slot }),
    },
    LONG_OP_TIMEOUT_MS,
  );

  if (!res.ok) {
    let detail: unknown = null;
    try {
      detail = await res.json();
    } catch {
      // not JSON
    }
    throw new ApiError(res.status, detail);
  }

  const reader = res.body?.getReader();
  if (!reader) {
    throw new ApiError(500, {
      detail: { error: "INTERNAL_ERROR", message: "Response had no readable body." },
    });
  }

  const decoder = new TextDecoder("utf-8");
  let buffer = "";
  let finalEvent: SaveProgressEvent | null = null;

  while (true) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    let newlineIdx = buffer.indexOf("\n");
    while (newlineIdx !== -1) {
      const line = buffer.slice(0, newlineIdx).trim();
      buffer = buffer.slice(newlineIdx + 1);
      if (line) {
        try {
          const ev = JSON.parse(line) as SaveProgressEvent;
          onProgress(ev);
          if (ev.done) finalEvent = ev;
        } catch {
          // Malformed line — skip
        }
      }
      newlineIdx = buffer.indexOf("\n");
    }
  }
  if (buffer.trim()) {
    try {
      const ev = JSON.parse(buffer.trim()) as SaveProgressEvent;
      onProgress(ev);
      if (ev.done) finalEvent = ev;
    } catch {
      // Malformed final fragment — skip
    }
  }

  if (!finalEvent) {
    throw new ApiError(500, {
      detail: {
        error: "INTERNAL_ERROR",
        message: "Duplicate stream closed without a 'done' event.",
      },
    });
  }
  if (!finalEvent.ok) {
    throw new ApiError(500, {
      detail: {
        error: finalEvent.error ?? "DUPLICATE_FAILED",
        message: finalEvent.message,
        error_step: finalEvent.error_step,
        steps_completed: finalEvent.steps_completed,
      },
    });
  }
  return finalEvent;
}

// ───────────────────────────────────────────────────────────────────────
//  Portrait
// ───────────────────────────────────────────────────────────────────────

export async function compilePortrait(
  image: File,
  face_index: number,
  opts?: {
    eye_x?: number; eye_y?: number;
    eye_w?: number; eye_h?: number;
    mouth_x?: number; mouth_y?: number;
    mouth_w?: number; mouth_h?: number;
    skip_animation?: boolean;
    install_id?: string;
    // Optional alternate-authoring uploads. Each is independent — supply
    // bigface_image alone for a separately-framed hero portrait, or any
    // subset of anim_eye_*/anim_mouth_* to make the merc blink/talk.
    // See docs/WMERC_FORMAT.md for the auto-pad rules.
    bigface_image?: File;
    anim_eye_1?: File;
    anim_eye_2?: File;
    anim_eye_3?: File;
    anim_eye_4?: File;
    anim_mouth_1?: File;
    anim_mouth_2?: File;
    anim_mouth_3?: File;
  }
) {
  const fd = new FormData();
  fd.append("image", image);
  fd.append("face_index", String(face_index));
  if (opts?.eye_x !== undefined) fd.append("eye_x", String(opts.eye_x));
  if (opts?.eye_y !== undefined) fd.append("eye_y", String(opts.eye_y));
  if (opts?.eye_w !== undefined) fd.append("eye_w", String(opts.eye_w));
  if (opts?.eye_h !== undefined) fd.append("eye_h", String(opts.eye_h));
  if (opts?.mouth_x !== undefined) fd.append("mouth_x", String(opts.mouth_x));
  if (opts?.mouth_y !== undefined) fd.append("mouth_y", String(opts.mouth_y));
  if (opts?.mouth_w !== undefined) fd.append("mouth_w", String(opts.mouth_w));
  if (opts?.mouth_h !== undefined) fd.append("mouth_h", String(opts.mouth_h));
  if (opts?.skip_animation !== undefined)
    fd.append("skip_animation", String(opts.skip_animation));
  if (opts?.bigface_image) fd.append("bigface_image", opts.bigface_image);
  if (opts?.anim_eye_1) fd.append("anim_eye_1", opts.anim_eye_1);
  if (opts?.anim_eye_2) fd.append("anim_eye_2", opts.anim_eye_2);
  if (opts?.anim_eye_3) fd.append("anim_eye_3", opts.anim_eye_3);
  if (opts?.anim_eye_4) fd.append("anim_eye_4", opts.anim_eye_4);
  if (opts?.anim_mouth_1) fd.append("anim_mouth_1", opts.anim_mouth_1);
  if (opts?.anim_mouth_2) fd.append("anim_mouth_2", opts.anim_mouth_2);
  if (opts?.anim_mouth_3) fd.append("anim_mouth_3", opts.anim_mouth_3);

  const base = await getBaseUrl();
  const auth = await authHeaders();
  const qs = opts?.install_id ? `?install_id=${encodeURIComponent(opts.install_id)}` : "";
  const res = await fetchWithTimeout(`${base}/portrait/compile${qs}`, {
    method: "POST",
    headers: auth,
    body: fd,
  });
  if (!res.ok) {
    let detail: unknown = null;
    try { detail = await res.json(); } catch {}
    throw new ApiError(res.status, detail);
  }
  return (await res.json()) as {
    ok: boolean;
    face_index: number;
    files_written: string[];
    frame_count: number;
    explicit_animation?: boolean;
    bigface_override?: boolean;
  };
}

// ───────────────────────────────────────────────────────────────────────
//  Backgrounds
// ───────────────────────────────────────────────────────────────────────

export interface BackgroundModifier {
  key: string;
  value: number;
}

export interface BackgroundEntry {
  id: number;
  name: string;
  short_name: string;
  description: string;
  modifiers: BackgroundModifier[];
  /** True when the IMP creation picker enumerates this id (id <=
   * num_found_background, i.e. at or before the last physical entry). */
  imp_selectable: boolean;
  /** True when the entry carries nested drug lists or non-zero columns outside
   * the editor schema — preserved verbatim on save, not shown in the form. */
  has_advanced_data: boolean;
}

/** One editable field, engine-derived (see sidecar backgrounds_schema.py). */
export interface BackgroundFieldSpec {
  key: string;
  label: string;
  group: string;
  kind: "int" | "flag" | "enum";
  min: number;
  max: number;
  options?: { value: number; label: string }[];
  note?: string;
}

export interface BackgroundsResponse {
  backgrounds: BackgroundEntry[];
  schema_fields: BackgroundFieldSpec[];
  install_id: string;
  file_present: boolean;
  writable: boolean;
  write_path: string | null;
  /** Engine IMP-picker bound = the last physical entry's uiIndex. */
  num_found_background: number;
  max_index: number;
  name_max: number;
  short_name_max: number;
  description_max: number;
  duplicate_ids: number[];
}

/** A value the sidecar clamped to the engine's range (or a flag it coerced). */
export interface BackgroundClamp {
  key: string;
  requested: number;
  stored: number;
}

export interface BackgroundWriteResult {
  ok: boolean;
  backup_id?: string;
  ui_index?: number;
  num_found_background?: number;
  imp_selectable?: boolean;
  was_physical_last?: boolean;
  moved?: boolean;
  clamps?: BackgroundClamp[];
}

export interface BackgroundCreatePayload {
  name: string;
  short_name?: string;
  description?: string;
  fields?: Record<string, number>;
  /** Omit to auto-pick the next free id; provide to claim a specific id (1..max). */
  ui_index?: number | null;
  /** Place the new entry physically last so it (and currently-hidden higher ids)
   * appear in IMP character creation. */
  make_imp_selectable?: boolean;
}

export interface BackgroundUpdatePayload {
  name: string;
  short_name?: string;
  description?: string;
  /** Owned fields to sync: non-zero set, zero removed. The form submits all. */
  fields?: Record<string, number>;
}

export function listBackgrounds(install_id?: string) {
  const qs = install_id ? `?install_id=${encodeURIComponent(install_id)}` : "";
  return request<BackgroundsResponse>(`/backgrounds${qs}`);
}

export function createBackground(payload: BackgroundCreatePayload, install_id?: string) {
  const qs = install_id ? `?install_id=${encodeURIComponent(install_id)}` : "";
  return request<BackgroundWriteResult>(`/backgrounds${qs}`, {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export function updateBackground(
  id: number, payload: BackgroundUpdatePayload, install_id?: string,
) {
  const qs = install_id ? `?install_id=${encodeURIComponent(install_id)}` : "";
  return request<BackgroundWriteResult>(`/backgrounds/${id}${qs}`, {
    method: "PUT",
    body: JSON.stringify(payload),
  });
}

export function deleteBackground(id: number, install_id?: string) {
  const qs = install_id ? `?install_id=${encodeURIComponent(install_id)}` : "";
  return request<BackgroundWriteResult>(`/backgrounds/${id}${qs}`, { method: "DELETE" });
}

/** Control IMP-creation visibility: pass `all` to expose every background, or
 * `ui_index` to make that id (and everything below it) selectable. */
export function setBackgroundImpThreshold(
  body: { ui_index?: number; all?: boolean }, install_id?: string,
) {
  const qs = install_id ? `?install_id=${encodeURIComponent(install_id)}` : "";
  return request<BackgroundWriteResult>(`/backgrounds/imp-threshold${qs}`, {
    method: "POST",
    body: JSON.stringify(body),
  });
}

// ───────────────────────────────────────────────────────────────────────
//  Traits system
// ───────────────────────────────────────────────────────────────────────

export interface TraitCatalogEntry {
  id: number;
  name: string;
  tier: "Major" | "Minor";
}

export interface TraitSystemInfo {
  system: "NT" | "OT";
  catalog: TraitCatalogEntry[];
  install_id: string;
}

export function getTraitSystem(install_id?: string) {
  const qs = install_id ? `?install_id=${encodeURIComponent(install_id)}` : "";
  return request<TraitSystemInfo>(`/traits/system${qs}`);
}

// ───────────────────────────────────────────────────────────────────────
//  FaceGear capacity
// ───────────────────────────────────────────────────────────────────────

export interface FaceGearItem {
  name: string;
  relative_path: string;
  frame_count: number;
  canvas_width: number;
  canvas_height: number;
  is_imp_variant: boolean;
}

export interface FaceGearOrphan {
  stem: string;
  missing: "base" | "imp";
  present_path: string;
}

export interface FaceGearCapacity {
  items: FaceGearItem[];
  lowest_frame_count: number | null;
  orphans: FaceGearOrphan[];
  install_id: string;
}

export function getFaceGearCapacity(install_id?: string) {
  const qs = install_id ? `?install_id=${encodeURIComponent(install_id)}` : "";
  return request<FaceGearCapacity>(`/facegear/capacity${qs}`);
}

export interface FaceGearOrphanRepairResult {
  stem: string;
  source: string;
  target: string;
  bytes_written: number;
}

export interface FaceGearOrphanRepairResponse {
  repaired: FaceGearOrphanRepairResult[];
  skipped: Array<{ stem: string; reason: string }>;
  backup_id: string | null;
  install_id: string;
}

/**
 * Mirror each requested orphan's present STI to its missing partner so
 * `InitializeFaceGearGraphics()` finds both at boot. Pass `stems = null`
 * (or omit) to repair every currently-visible registered orphan.
 *
 * The sidecar re-scans the install before acting, so this is safe to
 * call after a long delay — it only repairs what's still orphaned at
 * the moment the request arrives.
 */
export function repairFaceGearOrphans(
  stems?: string[] | null,
  install_id?: string,
) {
  const qs = install_id ? `?install_id=${encodeURIComponent(install_id)}` : "";
  return request<FaceGearOrphanRepairResponse>(`/facegear/orphans/repair${qs}`, {
    method: "POST",
    body: JSON.stringify({ stems: stems && stems.length > 0 ? stems : null }),
  });
}

export interface FaceGearExtendResult {
  name: string;
  relative_path: string;
  previous_frame_count: number;
  new_frame_count: number;
  frames_appended: number;
  noop: boolean;
}

export interface FaceGearExtendResponse {
  extended: FaceGearExtendResult[];
  backup_id: string | null;
  install_id: string;
}

export interface VoiceIndexProbe {
  voice_index: number;
  folder: string;
  folder_exists: boolean;
  /** Loose clips on disk (Speech/<n>/ or Speech/<n>_NNN.<ext>). */
  clip_count: number;
  /** Clips inside Data/Speech.slf for this index. Vanilla classic
   * donors ship their barks bundled here, not on the filesystem. */
  slf_clip_count?: number;
  /** Convenience flag: loose=0 but SLF has clips. Frontend uses this
   * to show a reassuring "vanilla voice from the game archive" hint
   * instead of the alarmist "merc will be silent" warning. */
  is_vanilla_archive?: boolean;
}

export function probeVoiceIndex(voice_index: number, install_id?: string) {
  const qs = install_id ? `?install_id=${encodeURIComponent(install_id)}` : "";
  return request<VoiceIndexProbe>(`/voice/probe/${voice_index}${qs}`);
}

export function extendFaceGear(
  face_index: number,
  install_id?: string,
  only_crash_risk: boolean = true,
) {
  const qs = install_id ? `?install_id=${encodeURIComponent(install_id)}` : "";
  return request<FaceGearExtendResponse>(
    `/facegear/extend${qs}`,
    {
      method: "POST",
      body: JSON.stringify({ face_index, only_crash_risk }),
    },
    LONG_OP_TIMEOUT_MS,
  );
}

export interface FaceGearOverlayResult {
  name: string;
  relative_path: string;
  previous_frame_count: number;
  new_frame_count: number;
  extended: boolean;
}

export interface FaceGearOverlayResponse {
  written: FaceGearOverlayResult[];
  backup_id: string | null;
  install_id: string;
}

/** Inject a custom overlay PNG into a FaceGear STI at the merc's face index.
 *  PNG can be any size >= 48×43; auto-cropped + resized. Mirrors to the
 *  `_IMP.sti` partner when `applyToImp` is true (the default). */
export function injectFaceGearOverlay(
  sti_name: string,
  face_index: number,
  pngFile: File,
  install_id?: string,
  apply_to_imp: boolean = true,
) {
  return new Promise<FaceGearOverlayResponse>((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = async () => {
      const dataUrl = reader.result as string;
      const png_b64 = dataUrl.slice(dataUrl.indexOf(",") + 1);
      try {
        const qs = install_id ? `?install_id=${encodeURIComponent(install_id)}` : "";
        const res = await request<FaceGearOverlayResponse>(
          `/facegear/overlay${qs}`,
          {
            method: "POST",
            body: JSON.stringify({ sti_name, face_index, png_b64, apply_to_imp }),
          },
          LONG_OP_TIMEOUT_MS,
        );
        resolve(res);
      } catch (e) {
        reject(e);
      }
    };
    reader.onerror = () => reject(reader.error ?? new Error("File read failed"));
    reader.readAsDataURL(pngFile);
  });
}

export interface FaceGearAutoPositionResult {
  name: string;
  relative_path: string;
  source_face_index: number;
  source_offset_xy: [number, number];
  applied_offset_xy: [number, number];
  delta_xy: [number, number];
  source_eye_xy: [number, number];
  target_eye_xy: [number, number];
  extended: boolean;
}

export interface FaceGearAutoPositionResponse {
  written: FaceGearAutoPositionResult[];
  backup_id: string | null;
  install_id: string;
}

/** Copy a stock FaceGear graphic into the merc's frame with offset computed
 *  from the merc's usEyesX/Y. Sidecar auto-picks the first non-empty
 *  frame as the source pixels. Mirrors to _IMP.sti when apply_to_imp is
 *  true.
 *
 *  The previous source_face_index/source_eye_x/y override knobs were
 *  removed in favor of direct sOffsetX/sOffsetY editing via
 *  `setFaceGearOffset` — users fine-tune position with absolute coords
 *  rather than picking a different source merc to inherit a different
 *  painted baseline.
 */
export function autoPositionFaceGear(
  sti_name: string,
  target_face_index: number,
  target_eye_x: number,
  target_eye_y: number,
  options?: {
    install_id?: string;
    apply_to_imp?: boolean;
  },
) {
  const qs = options?.install_id ? `?install_id=${encodeURIComponent(options.install_id)}` : "";
  const body: Record<string, unknown> = {
    sti_name,
    target_face_index,
    target_eye_x,
    target_eye_y,
    apply_to_imp: options?.apply_to_imp ?? true,
  };
  return request<FaceGearAutoPositionResponse>(
    `/facegear/auto-position${qs}`,
    { method: "POST", body: JSON.stringify(body) },
    LONG_OP_TIMEOUT_MS,
  );
}

export interface FaceGearNudgeResult {
  name: string;
  relative_path: string;
  previous_offset_xy: [number, number];
  new_offset_xy: [number, number];
}

export interface FaceGearNudgeResponse {
  nudged: FaceGearNudgeResult[];
  backup_id: string | null;
  install_id: string;
}

/**
 * Shift one FaceGear frame's sOffsetX/sOffsetY by (dx, dy) pixels —
 * pure header edit, no quantize. Used by the post-auto-position nudge
 * UI to fine-tune positioning when the eye-coord-delta math lands
 * close but not perfect.
 */
export function nudgeFaceGearOffset(
  sti_name: string,
  face_index: number,
  dx: number,
  dy: number,
  install_id?: string,
  apply_to_imp: boolean = true,
) {
  const qs = install_id ? `?install_id=${encodeURIComponent(install_id)}` : "";
  return request<FaceGearNudgeResponse>(
    `/facegear/nudge${qs}`,
    {
      method: "POST",
      body: JSON.stringify({ sti_name, face_index, dx, dy, apply_to_imp }),
    },
  );
}

export interface FaceGearSetOffsetResult {
  name: string;
  relative_path: string;
  previous_offset_xy: [number, number];
  new_offset_xy: [number, number];
}

export interface FaceGearSetOffsetResponse {
  written: FaceGearSetOffsetResult[];
  backup_id: string | null;
  install_id: string;
}

/**
 * Set one FaceGear frame's sOffsetX/sOffsetY to absolute (offset_x,
 * offset_y) — pure header edit, no quantize. Companion to
 * `nudgeFaceGearOffset` (which shifts by a delta). Used by the X/Y
 * coordinate inputs in FaceGearOverlayAuthor: the user types a target
 * value and the wizard sets the offset to exactly that, rather than
 * accumulating ±1 nudges to get there.
 */
export function setFaceGearOffset(
  sti_name: string,
  face_index: number,
  offset_x: number,
  offset_y: number,
  install_id?: string,
  apply_to_imp: boolean = true,
) {
  const qs = install_id ? `?install_id=${encodeURIComponent(install_id)}` : "";
  return request<FaceGearSetOffsetResponse>(
    `/facegear/set-offset${qs}`,
    {
      method: "POST",
      body: JSON.stringify({ sti_name, face_index, offset_x, offset_y, apply_to_imp }),
    },
  );
}

export interface FaceGearOverlayPreview {
  sti_name: string;
  face_index: number;
  png_b64: string | null;
  /** Signed (sOffsetX, sOffsetY) for the frame, or null when the frame
   *  doesn't exist. Surfaced so the FaceGear overlay tab can show the
   *  nudge widget for frames authored in a prior session — not just
   *  ones touched in the current session via Auto / nudge. */
  offset_xy: [number, number] | null;
}

/** Read the merc's current overlay in one FaceGear STI as base64 PNG. */
export function previewFaceGearOverlay(
  sti_name: string,
  face_index: number,
  install_id?: string,
) {
  const params = new URLSearchParams({
    sti_name,
    face_index: String(face_index),
  });
  if (install_id) params.set("install_id", install_id);
  return request<FaceGearOverlayPreview>(`/facegear/overlay?${params.toString()}`);
}

// ───────────────────────────────────────────────────────────────────────
//  Gear
// ───────────────────────────────────────────────────────────────────────

export interface GearPreset {
  id: string;
  name: string;
  description: string;
  gear: Omit<import("./schema").GearKit, "mAbsolutePrice"> & { mAbsolutePrice: -1 };
}

export function listGearPresets() {
  return request<GearPreset[]>("/gear/presets");
}

// ───────────────────────────────────────────────────────────────────────
//  Backup
// ───────────────────────────────────────────────────────────────────────

export function listBackups(install_id?: string) {
  const qs = install_id ? `?install_id=${encodeURIComponent(install_id)}` : "";
  return request<BackupEntry[]>(`/backup${qs}`);
}

export function takeSnapshot(reason: string, install_id?: string) {
  const qs = install_id ? `?install_id=${encodeURIComponent(install_id)}` : "";
  // Full-install snapshot copies XML + EDT + STIs + voice — minutes on a
  // large install. Use the long-op timeout.
  return request<BackupEntry>(
    `/backup${qs}`,
    { method: "POST", body: JSON.stringify({ reason }) },
    LONG_OP_TIMEOUT_MS,
  );
}

export function restoreBackup(backup_id: string, install_id?: string) {
  const qs = install_id ? `?install_id=${encodeURIComponent(install_id)}` : "";
  return request<{ ok: boolean; files_restored: number }>(
    `/backup/restore${qs}`,
    { method: "POST", body: JSON.stringify({ backup_id }) },
    LONG_OP_TIMEOUT_MS,
  );
}

export function deleteBackup(backup_id: string, install_id?: string) {
  const qs = install_id ? `?install_id=${encodeURIComponent(install_id)}` : "";
  return request<{ ok: boolean; removed: boolean }>(
    `/backup/${encodeURIComponent(backup_id)}${qs}`,
    { method: "DELETE" }
  );
}

// ───────────────────────────────────────────────────────────────────────
//  Game launch
// ───────────────────────────────────────────────────────────────────────

export function launchGame(install_id?: string) {
  const qs = install_id ? `?install_id=${encodeURIComponent(install_id)}` : "";
  return request<{ ok: boolean; pid: number; exe: string }>(`/game/launch${qs}`, {
    method: "POST",
  });
}

// ───────────────────────────────────────────────────────────────────────
//  Voice files
// ───────────────────────────────────────────────────────────────────────

export interface VoiceClip {
  name: string;
  size_bytes: number;
  path: string;
}

export interface VoiceFolderState {
  slot: number;
  voice_index: number;
  folder: string;
  folder_exists: boolean;
  clips: VoiceClip[];
}

export function listVoiceClips(slot: number, install_id?: string) {
  const qs = install_id ? `?install_id=${encodeURIComponent(install_id)}` : "";
  return request<VoiceFolderState>(`/voice/${slot}${qs}`);
}

export async function uploadVoiceClips(
  slot: number,
  files: File[],
  barks?: (number | null)[],
  install_id?: string
) {
  const fd = new FormData();
  for (const f of files) fd.append("files", f);
  // `barks` is parallel to `files`: a JA2 quote number to auto-name the clip
  // as <voiceIndex>_<bark>.<ext>, or null/absent to keep the uploaded filename.
  if (barks) fd.append("barks", JSON.stringify(barks));
  const base = await getBaseUrl();
  const auth = await authHeaders();
  const qs = install_id ? `?install_id=${encodeURIComponent(install_id)}` : "";
  const res = await fetchWithTimeout(`${base}/voice/${slot}/upload${qs}`, {
    method: "POST",
    headers: auth,
    body: fd,
  });
  if (!res.ok) {
    let detail: unknown = null;
    try { detail = await res.json(); } catch {}
    throw new ApiError(res.status, detail);
  }
  return (await res.json()) as {
    ok: boolean;
    slot: number;
    voice_index: number;
    added: { name: string; size_bytes: number }[];
    skipped: { name: string; reason: string }[];
  };
}

export function deleteVoiceClip(slot: number, filename: string, install_id?: string) {
  const qs = install_id ? `?install_id=${encodeURIComponent(install_id)}` : "";
  return request<{ ok: boolean; removed: string }>(
    `/voice/${slot}/${encodeURIComponent(filename)}${qs}`,
    { method: "DELETE" }
  );
}

// ───────────────────────────────────────────────────────────────────────
//  .wmerc bundle (export + import)
// ───────────────────────────────────────────────────────────────────────

export interface ExportBundlePayload {
  slot: number;
  out_path: string;
  author_name?: string;
  license?: string;
  intended_mod?: string;
  notes?: string;
  include_voice?: boolean;
  portrait_source_png?: string;
  extreme_master_png?: string;
  bigface_source_png?: string;
  preview_png?: string;
  // Explicit-animation eye/mouth frame PNGs. Up to 4 eye + 3 mouth
  // frames. The backend's ExportPayload accepts these to round-trip
  // hand-authored animations through the bundle; without them in the
  // TS interface the frontend couldn't request explicit-animation
  // exports even when the user had authored the frames. Cross-cutting
  // review fix 2026-05-25.
  anim_eye_1?: string;
  anim_eye_2?: string;
  anim_eye_3?: string;
  anim_eye_4?: string;
  anim_mouth_1?: string;
  anim_mouth_2?: string;
  anim_mouth_3?: string;
}

export interface WmercVoiceMeta {
  voice_index: number;
  count: number;
  filenames: string[];
}

/** Per-bundle portrait metadata. Mirrors the `portrait` block of
 * WmercManifest on the backend — describes how the portrait was
 * baked (animation mode, sub-frame coords) so the importer can
 * reproduce the same artifacts. */
export interface WmercPortraitMeta {
  animation_mode: "skip" | "procedural" | "explicit" | string;
  eye_box: { x: number; y: number; w: number; h: number } | null;
  mouth_box: { x: number; y: number; w: number; h: number } | null;
  has_explicit_frames: boolean;
}

export interface WmercManifestSummary {
  wmerc_version: number;
  tool: string;
  tool_version: string;
  exported_at: string;
  author: { name: string | null; contact: string | null };
  license: string;
  notes: string | null;
  merc: import("./schema").Merc;
  gear: import("./schema").GearKit[];
  aim_binding: import("./schema").AimBinding | null;
  // Type=2 expansion-MERC binding. Added 2026-05-14 as the fix for the
  // Eskimo bug (bundled MercBioID was clobbering the importer's
  // auto-allocation). The importer ALWAYS rederives MercBioID against
  // the target install — this field is descriptive metadata only, used
  // by the preview UI to show "this merc was on Speck's M.E.R.C. site
  // in its source install". Null for AIM-only bundles.
  merc_binding: import("./schema").MercBinding | null;
  // Portrait baking metadata — surfaces animation mode + sub-frame
  // coords for the import preview. Null when no portrait shipped.
  portrait: WmercPortraitMeta | null;
  // sha-ish of the source install's MercProfiles.xml schema, used by
  // the importer to warn about schema-mismatch bundles (e.g. a
  // Vengeance-exported merc imported into pre-STOMP AR). Optional —
  // older bundles may not carry it.
  schema_fingerprint?: string | null;
  voice: WmercVoiceMeta | null;
  compat: {
    intended_mod: "vanilla" | "wasteland" | "aimnas" | "wildfire" | "any";
    intended_slot_range: "aim" | "merc" | "either";
    trait_system: "NT" | "OT" | "either";
    min_game_version: string;
  };
}

export interface ImportPreview {
  manifest: WmercManifestSummary;
  files: string[];
  has_portrait: boolean;
  has_animation_frames: boolean;
  has_voice: boolean;
}

export interface ImportReport {
  target_slot: number;
  files_written: string[];
  bio_route: string;
  portrait_compiled: boolean;
  voice_clips_copied: number;
  aim_bio_id_used: number | null;
  /** Type=2 expansion-MERC bios route to MERCBIOS.EDT at
   * MercBioID × 1120; the importer rederives this against the target
   * install (never trusts the bundled value). null for AIM imports
   * and Type=2 imports where no MERC binding existed. */
  merc_bio_id_used: number | null;
  issues: AuditIssue[];
  partial_failures: string[];
}

export function exportBundle(payload: ExportBundlePayload, install_id?: string) {
  const qs = install_id ? `?install_id=${encodeURIComponent(install_id)}` : "";
  // Export reads STIs, voice clips, all 8 face variants — long op.
  return request<{ ok: boolean; out_path: string }>(
    `/bundle/export${qs}`,
    { method: "POST", body: JSON.stringify(payload) },
    LONG_OP_TIMEOUT_MS,
  );
}

export function importPreview(bundle_path: string) {
  return request<ImportPreview>(`/bundle/import-preview`, {
    method: "POST",
    body: JSON.stringify({ bundle_path }),
  });
}

export function importBundle(
  bundle_path: string,
  target_slot?: number,
  force?: boolean,
  install_id?: string
) {
  const qs = install_id ? `?install_id=${encodeURIComponent(install_id)}` : "";
  // Import = unzip + portrait recompile (4 sizes) + voice copy + EDT writes.
  // Long-op timeout matches compilePortrait's budget.
  return request<{ ok: boolean; report: ImportReport }>(
    `/bundle/import${qs}`,
    { method: "POST", body: JSON.stringify({ bundle_path, target_slot, force: force ?? false }) },
    LONG_OP_TIMEOUT_MS,
  );
}

// ───────────────────────────────────────────────────────────────────────
//  Save scanner — "this merc appears in N saves" surfacing
// ───────────────────────────────────────────────────────────────────────
//
// The engine snapshots a merc's MercProfiles.xml stats into the
// SOLDIERTYPE struct at HIRE time, and that snapshot is what ends up in
// the save file. So edits to MercProfiles.xml do NOT retroactively rewrite
// the stats of mercs already hired in an existing save — only new hires
// pick up the new stats. SaveSnapshotBanner consumes this endpoint to
// warn the user before they edit/move/delete a merc who has live save
// references.

export interface SavesRefsTargeted {
  slot: number;
  saves: string[];   // absolute paths to .SAV files referencing this slot
}

/**
 * Ask the sidecar which save files (in the standard JA2 save folders)
 * contain a UTF-16LE byte-match of the slot's `zNickname`. False
 * positives are possible (a 3+ char nickname could collide with arbitrary
 * bytes in the save) but are harmless — the banner is a soft warning,
 * not a gating check.
 *
 * Returns the targeted shape `{slot, saves: [...]}`. The bulk
 * `{all: {slot: [...]}}` variant is available on the same route by
 * omitting `slot` — used by future Hub diagnostics, not by the banner.
 */
export function getSavesRefs(slot: number, install_id?: string) {
  const params = new URLSearchParams();
  params.set("slot", String(slot));
  if (install_id) params.set("install_id", install_id);
  return request<SavesRefsTargeted>(`/saves/refs?${params.toString()}`);
}

// ───────────────────────────────────────────────────────────────────────
//  INI editor + game status + app settings (MercForge UI Phase 2)
// ───────────────────────────────────────────────────────────────────────

import type {
  AppSettings,
  GameStatus,
  IniApplyResult,
  IniChangeItem,
  IniDiagnostic,
  IniEffectiveResponse,
  IniOverridesResponse,
  IniSchemaDoc,
  IniSchemasResponse,
  IniSummaryResponse,
} from "./schema";

export async function getIniSchemas(): Promise<IniSchemasResponse> {
  return request<IniSchemasResponse>("/ini/schemas");
}

export async function getIniSchema(file: string): Promise<IniSchemaDoc> {
  return request<IniSchemaDoc>(`/ini/schema/${encodeURIComponent(file)}`);
}

export async function getIniEffective(file: string): Promise<IniEffectiveResponse> {
  return request<IniEffectiveResponse>(`/ini/effective/${encodeURIComponent(file)}`);
}

export async function getIniOverrides(): Promise<IniOverridesResponse> {
  return request<IniOverridesResponse>("/ini/overrides");
}

export async function getIniSummary(): Promise<IniSummaryResponse> {
  return request<IniSummaryResponse>("/ini/summary");
}

export async function getIniDiagnostic(): Promise<IniDiagnostic> {
  return request<IniDiagnostic>("/ini/diagnostic");
}

export async function applyIniChanges(payload: {
  target: "canon" | "override";
  changes: IniChangeItem[];
  dry_run?: boolean;
}): Promise<IniApplyResult> {
  return request<IniApplyResult>("/ini/changes", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export async function openProfileFolder(): Promise<{ ok: boolean; opened: string }> {
  return request<{ ok: boolean; opened: string }>("/ini/open-profile-folder", {
    method: "POST",
  });
}

export async function getGameStatus(): Promise<GameStatus> {
  return request<GameStatus>("/game/status");
}

export async function getAppSettings(): Promise<AppSettings> {
  return request<AppSettings>("/settings");
}

export async function updateAppSettings(patch: AppSettings): Promise<AppSettings> {
  return request<AppSettings>("/settings", {
    method: "PUT",
    body: JSON.stringify(patch),
  });
}

import type { GraphicsDeployResult, GraphicsStatusResponse } from "./schema";

export async function getGraphicsStatus(): Promise<GraphicsStatusResponse> {
  return request<GraphicsStatusResponse>("/graphics/status");
}

export async function deployGraphics(): Promise<GraphicsDeployResult> {
  return request<GraphicsDeployResult>("/graphics/deploy", { method: "POST" });
}
