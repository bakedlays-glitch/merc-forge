/**
 * MapForge — frontend API client for the /api/v1/mapforge/* sidecar routes.
 *
 * Phase 0 (read-only): lists sector .dat files from the active install,
 * fetches sector metadata + room lists, renders sector PNGs, and inspects
 * individual tiles.
 *
 * Mirrors the Pydantic models in sidecar/routes/mapforge.py — keep these
 * shapes in sync when adding fields backend-side.
 */

import { getServerPort, getServerToken } from "./tauri";

// ─── Types (must match sidecar/routes/mapforge.py) ──────────────────────
export interface MapForgeHealth {
  renderer_available: boolean;
  renderer_import_error: string | null;
  headless_compiler_path: string;
  active_install_id: string | null;
}

export interface SectorMapFile {
  name: string;
  /** Absolute filesystem path, or a `slf://<archive>!<internal>` URI
   * for SLF-bundled sectors. Either form is accepted by `/sector/*`
   * endpoints — the backend extracts SLF entries to a temp cache
   * on first use. */
  path: string;
  rel_path: string;
  size_bytes: number;
  source: "loose" | "slf";
  slf_archive: string | null;
}

export interface InstallMaps {
  install_id: string;
  install_path: string;
  data_layers: string[];
  maps: SectorMapFile[];
  ja2set_xml: string | null;
  /** True when the payload was served from the on-disk fingerprint cache.
   * Pass `rescan=true` to `listInstallMaps` to bypass the cache. */
  cached: boolean;
  cache_fingerprint: string;
  scanned_at: number;
}

export interface RoomSummary {
  room_id: number;
  tile_count: number;
  bbox: [number, number, number, number];  // x0, y0, x1, y1
}

export interface SectorInfo {
  dat_path: string;
  rows: number;
  cols: number;
  tileset_in_header: number;
  rooms: RoomSummary[];
  layer_totals: Record<string, number>;
}

export interface LayerEntry {
  slot: number;
  sub: number;
  sti_filename: string | null;
  sti_frame_index_0based: number;
  /** True when the slot's STI has a sibling .jsd file. Populated by
   * the LOCAL inspectTile path (which reads from the renderer's
   * slot_has_jsd map sourced from the atlas manifest). The remote
   * /sessions/{sid}/tile path may leave this undefined. */
  has_jsd?: boolean;
}

export interface TileInspection {
  x: number;
  y: number;
  gridno: number;
  room_id: number;
  height: number;
  world_flags: number;
  layers: Record<string, LayerEntry[]>;
}

// ─── Internal helpers ──────────────────────────────────────────────────
async function authedFetch(path: string, init?: RequestInit): Promise<Response> {
  const port = await getServerPort();
  const token = await getServerToken();
  const headers: Record<string, string> = {
    ...(init?.headers as Record<string, string>),
  };
  if (token) headers["X-MercWizard-Token"] = token;
  return fetch(`http://127.0.0.1:${port}/api/v1${path}`, { ...init, headers });
}

async function jsonGet<T>(path: string): Promise<T> {
  const res = await authedFetch(path);
  if (!res.ok) {
    let detail: unknown = null;
    try { detail = await res.json(); } catch {}
    throw new Error(
      `MapForge ${path} → HTTP ${res.status}: ${JSON.stringify(detail)}`
    );
  }
  return res.json() as Promise<T>;
}

async function jsonBody<T>(
  method: "POST" | "PUT" | "DELETE",
  path: string,
  body?: unknown,
): Promise<T> {
  const init: RequestInit = { method };
  if (body !== undefined) {
    init.headers = { "Content-Type": "application/json" };
    init.body = JSON.stringify(body);
  }
  const res = await authedFetch(path, init);
  if (!res.ok) {
    let detail: unknown = null;
    try { detail = await res.json(); } catch {}
    throw new Error(
      `MapForge ${method} ${path} → HTTP ${res.status}: ${JSON.stringify(detail)}`
    );
  }
  if (res.status === 204) return undefined as T;
  return res.json() as Promise<T>;
}

function jsonPost<T>(path: string, body: unknown): Promise<T> {
  return jsonBody<T>("POST", path, body);
}
function jsonPut<T>(path: string, body: unknown): Promise<T> {
  return jsonBody<T>("PUT", path, body);
}
function jsonDelete<T>(path: string): Promise<T> {
  return jsonBody<T>("DELETE", path);
}

function qs(params: Record<string, string | number | undefined>): string {
  const u = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) {
    if (v !== undefined && v !== null && v !== "") u.set(k, String(v));
  }
  return u.toString();
}

// ─── Endpoints ─────────────────────────────────────────────────────────
export function getMapForgeHealth(): Promise<MapForgeHealth> {
  return jsonGet<MapForgeHealth>("/mapforge/health");
}

export function listInstallMaps(opts?: { rescan?: boolean }): Promise<InstallMaps> {
  const q = opts?.rescan ? "?rescan=true" : "";
  return jsonGet<InstallMaps>(`/mapforge/installs/maps${q}`);
}

/** Streaming variant of `listInstallMaps`. The backend yields per-phase
 * progress events while it walks the install, so the UI can show
 * "Walking Maps.slf (3/5)" instead of a generic spinner.
 *
 * Event shapes:
 *   {event: "phase",    phase, label}
 *   {event: "progress", current, total, detail}
 *   {event: "done",     data: InstallMaps}
 *   {event: "error",    message}
 *
 * Returns the final InstallMaps payload (resolves on the "done"
 * event). Errors out if the stream closes without "done". */
export type ScanEvent =
  | { event: "phase";    phase: string; label: string }
  | { event: "progress"; current: number; total: number; detail: string }
  | { event: "done";     data: InstallMaps }
  | { event: "error";    message: string };

export async function streamInstallMaps(
  opts: { rescan?: boolean; onEvent?: (e: ScanEvent) => void } = {},
): Promise<InstallMaps> {
  const q = opts.rescan ? "?rescan=true" : "";
  const res = await authedFetch(`/mapforge/installs/maps/stream${q}`);
  if (!res.ok || !res.body) {
    let detail: unknown = null;
    try { detail = await res.json(); } catch {}
    throw new Error(`stream failed: HTTP ${res.status}: ${JSON.stringify(detail)}`);
  }
  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buf = "";
  let finalData: InstallMaps | null = null;
  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buf += decoder.decode(value, { stream: true });
    // Each NDJSON line is one event. We split greedy so a partial last
    // line stays in `buf` for the next chunk.
    let nl = buf.indexOf("\n");
    while (nl !== -1) {
      const line = buf.slice(0, nl).trim();
      buf = buf.slice(nl + 1);
      if (line.length > 0) {
        // Parse inside try, dispatch outside — TODO #14 fix mirroring
        // the streamExtractSlf pattern in lib/tools.ts. Pre-fix the
        // backend "error" event re-throw was caught by the same
        // try/catch handling JSON.parse failures and silently logged
        // as "bad scan event line", swallowing the real backend error.
        let evt: ScanEvent | null = null;
        try {
          evt = JSON.parse(line) as ScanEvent;
        } catch (err) {
          // eslint-disable-next-line no-console
          console.warn("bad scan event line", line, err);
        }
        if (evt) {
          opts.onEvent?.(evt);
          if (evt.event === "done") finalData = evt.data;
          else if (evt.event === "error") {
            throw new Error(`scan failed: ${evt.message}`);
          }
        }
      }
      nl = buf.indexOf("\n");
    }
  }
  if (!finalData) {
    throw new Error("scan stream closed without 'done' event");
  }
  return finalData;
}

export function getSectorInfo(datPath: string): Promise<SectorInfo> {
  return jsonGet<SectorInfo>(
    `/mapforge/sector/info?${qs({ dat: datPath })}`
  );
}

export function inspectTile(
  datPath: string,
  xmlPath: string,
  tileset: number,
  x: number,
  y: number,
): Promise<TileInspection> {
  return jsonGet<TileInspection>(
    `/mapforge/sector/tile?${qs({
      dat: datPath, xml: xmlPath, tileset, x, y,
    })}`
  );
}

// ─── Pre-flight validation (A4) ─────────────────────────────────────────
// Mirrors ValidationFinding / ValidationReport in sidecar/routes/mapforge.py.
export type ValidationSeverity = "error" | "warn" | "info";

export interface ValidationFinding {
  severity: ValidationSeverity;
  code: string;
  message: string;
  /** Affected gridnos (sampled, capped at 50 server-side). Convert to
   * (x, y) with x = g % cols, y = Math.floor(g / cols). */
  tiles: number[];
  /** Total affected when it exceeds the sampled `tiles` list. */
  count: number | null;
  /** Tileset slot index for JSD findings (else null). */
  slot: number | null;
  /** True when the as-opened file already carried this finding — the
   * user's edits did not introduce it. Always false without a session. */
  preexisting?: boolean;
}

export interface ValidationReport {
  dat_path: string;
  rows: number;
  cols: number;
  errors: number;
  warnings: number;
  infos: number;
  /** Whether the (heavier) tileset JSD frame-match check ran. */
  jsd_checked: boolean;
  findings: ValidationFinding[];
}

/** Validate a .dat on disk. Pass xml + tileset to also run the JSD
 * frame-match check (otherwise only structure/playability checks run). */
export function validateSector(
  datPath: string,
  opts?: { xmlPath?: string; tileset?: number; checkJsd?: boolean },
): Promise<ValidationReport> {
  const params: Record<string, string | number | undefined> = { dat: datPath };
  if (opts?.xmlPath) params.xml = opts.xmlPath;
  if (opts?.tileset !== undefined) params.tileset = opts.tileset;
  if (opts?.checkJsd !== undefined) params.check_jsd = String(opts.checkJsd);
  return jsonGet<ValidationReport>(`/mapforge/sector/validate?${qs(params)}`);
}

/** Validate a session's in-memory (uncommitted) state — run before save. */
export function validateSession(
  sessionId: string,
  opts?: { checkJsd?: boolean },
): Promise<ValidationReport> {
  const query = qs(
    opts?.checkJsd !== undefined ? { check_jsd: String(opts.checkJsd) } : {},
  );
  return jsonGet<ValidationReport>(
    `/mapforge/sessions/${encodeURIComponent(sessionId)}/validate${query ? `?${query}` : ""}`,
  );
}

// ─── Radar / minimap generation (A3) ────────────────────────────────────
export interface RadarResult {
  output_path: string;
  bytes_written: number;
  width: number;
  height: number;
  /** True when a same-named radar exists in Radarmaps.slf (we're overriding
   * the bundled vanilla minimap). Informational. */
  overrides_bundled: boolean;
  /** base64 PNG of the generated 88×44 image. */
  preview_png_b64: string;
}

/** Generate the sector's 88×44 radar/minimap STI and write it into the
 * install's writable VFS profile (the layer the engine reads first). */
export function generateRadar(
  datPath: string, xmlPath: string, tileset: number,
): Promise<RadarResult> {
  return jsonPost<RadarResult>(
    `/mapforge/sector/radar?${qs({ dat: datPath, xml: xmlPath, tileset })}`,
    undefined,
  );
}

// ─── Edit op (Phase 2) ──────────────────────────────────────────────
export type EditOp =
  | "replace"
  | "add"
  | "place"
  | "remove"
  | "set_entries"
  | "set_room"
  | "set_height";
export type LayerName =
  "land" | "objs" | "shadows" | "structs" | "roofs" | "onroofs";

export interface EditTileBody {
  dat: string;
  x: number;
  y: number;
  layer?: LayerName;
  op: EditOp;
  entry_index?: number;
  slot?: number;
  sub?: number;
  room_id?: number;
}

export interface EditTileResult {
  ok: boolean;
  op: EditOp;
  before: number[][] | null;
  after: number[][];
  backup_path: string | null;
  bytes_written: number;
}

// ─── JSD viewer (Phase 4: tile inspector) ────────────────────────────
// Parsed representation of a slot's .jsd companion file. Used by the
// tile inspector's per-entry "View JSD" panel to surface multi-tile
// footprint + passability flags + PROFILE voxel grids.

export interface JsdProfileTile {
  bXPos: number;             // signed offset from base tile (X)
  bYPos: number;             // signed offset from base tile (Y)
  sPosRelToBase: number;     // 16-bit gridno offset from base
  profile: number[][];       // 5x5 grid of Z-occupancy bytes
}

export interface JsdParsed {
  sti_filename: string;
  jsd_path: string;
  size_bytes: number;
  szId: string;              // "J2SD" magic
  n_struct: number;
  n_stored: number;
  struct_data_size: number;
  n_image_tile_locs: number;
  flags_int: number;
  flag_names: string[];      // human-readable subset of flags_int bits
  ubArmour: number;
  ubHP: number;
  ubDensity: number;
  ubNumberOfTiles: number;   // footprint tile count (1 = single-tile)
  bZTileOffsetX: number;
  bZTileOffsetY: number;
  tiles: JsdProfileTile[];
}

export function getStiJsd(
  xmlPath: string,
  tileset: number,
  slot: number,
): Promise<JsdParsed> {
  return jsonGet<JsdParsed>(
    `/mapforge/sti/jsd?${qs({ xml: xmlPath, tileset, slot })}`,
  );
}

// ─── JSD writer (Tileset Editor full-JSD-editor scope) ───────────────
// Mirrors sidecar/routes/mapforge.py JsdEditBody / JsdEditResult.

export interface JsdTileEdit {
  index: number;
  bXPos?: number;
  bYPos?: number;
  sPosRelToBase?: number;
  /** 5x5 grid of unsigned bytes 0..255. Must be exactly 5 rows of 5
   * if provided. */
  profile?: number[][];
}

export interface JsdEditBody {
  xml: string;
  tileset: number;
  slot: number;
  fflags?: number;
  ubArmour?: number;
  ubHP?: number;
  ubDensity?: number;
  bZTileOffsetX?: number;
  bZTileOffsetY?: number;
  tiles?: JsdTileEdit[];
}

export interface JsdEditResult {
  sti_filename: string;
  jsd_path: string;
  bytes_written: number;
  backup_path: string | null;
  parsed: JsdParsed;
}

export function updateStiJsd(body: JsdEditBody): Promise<JsdEditResult> {
  return jsonPut<JsdEditResult>("/mapforge/sti/jsd", body);
}

// ─── Tileset enumerator (Tileset Editor screen 1) ────────────────────

export interface TilesetInfo {
  index: number;
  name: string | null;
  slot_count: number;
  inherits_from_0: boolean;
}

export interface TilesetList {
  xml_path: string;
  tilesets: TilesetInfo[];
}

export function listTilesets(xmlPath: string): Promise<TilesetList> {
  return jsonGet<TilesetList>(
    `/mapforge/tilesets?${qs({ xml: xmlPath })}`,
  );
}

/**
 * Fetch a single STI sub-frame as a blob: URL suitable for <img src=...>.
 * Same auth + CSP reasoning as fetchSectorRender — img tags can't send
 * the token, and Tauri's img-src CSP forbids http://127.0.0.1:*.
 *
 * Caller MUST revoke the returned URL when done (or use the React
 * component below which handles lifecycle).
 */
export async function fetchStiFrameBlobUrl(
  xmlPath: string,
  tileset: number,
  slot: number,
  sub: number,
): Promise<string> {
  const query = qs({ xml: xmlPath, tileset, slot, sub });
  const res = await authedFetch(`/mapforge/sti/frame?${query}`);
  if (!res.ok) {
    let detail: unknown = null;
    try { detail = await res.json(); } catch {}
    throw new Error(
      `STI frame fetch failed (HTTP ${res.status}): ${JSON.stringify(detail)}`
    );
  }
  const blob = await res.blob();
  return URL.createObjectURL(blob);
}

// ─── Tileset palette (Phase 2B) ──────────────────────────────────────
export interface PaletteSlot {
  slot: number;
  sti_filename: string;
  frame_count: number;
  category: string;
  has_jsd: boolean;
}

export interface TilesetPalette {
  tileset: number;
  xml_path: string;
  slots: PaletteSlot[];
  category_order: string[];
}

export function getTilesetPalette(
  xmlPath: string,
  tileset: number,
): Promise<TilesetPalette> {
  return jsonGet<TilesetPalette>(
    `/mapforge/tileset/palette?${qs({ xml: xmlPath, tileset })}`
  );
}

export interface PaletteSheetCell {
  slot: number;
  sti_filename: string;
  cell_x: number;
  cell_y: number;
  px: number;
  py: number;
  w: number;
  h: number;
}

export interface PaletteSheetMeta {
  tileset: number;
  cell: number;
  cols: number;
  rows: number;
  sheet_w: number;
  sheet_h: number;
  cells: PaletteSheetCell[];
  fingerprint: string;
}

export function getPaletteSheetMeta(
  xmlPath: string,
  tileset: number,
): Promise<PaletteSheetMeta> {
  return jsonGet<PaletteSheetMeta>(
    `/mapforge/tileset/palette-sheet-meta?${qs({ xml: xmlPath, tileset })}`
  );
}

/** Fetch the whole palette sprite sheet as a single blob URL. The
 * frontend slices via CSS background-position. Caller MUST revoke. */
export async function fetchPaletteSheetBlobUrl(
  xmlPath: string,
  tileset: number,
): Promise<string> {
  const res = await authedFetch(
    `/mapforge/tileset/palette-sheet?${qs({ xml: xmlPath, tileset })}`
  );
  if (!res.ok) throw new Error(`palette sheet failed: HTTP ${res.status}`);
  const blob = await res.blob();
  return URL.createObjectURL(blob);
}

// ─── Shared, app-lifetime palette-sheet blob-URL cache ──────────────────
// The Asset Browser used to re-stream the bake AND re-download the PNG
// blob on EVERY open, even when the sidecar's disk cache was already warm
// (the session-open preload only warmed the *sidecar* disk cache, never
// the browser). So "first open of a tileset" always paid a build-stream
// round-trip + a multi-MB blob download — it looked broken/slow.
//
// This cache holds ONE persistent blob URL per (xml, tileset). Both the
// background preload and the viewer go through it, so:
//   • preload (session open + tileset switch) populates it in the
//     background → the viewer's open is an already-resolved promise
//     (instant, zero network);
//   • the blob URL is intentionally NEVER revoked while cached — it's
//     shared across mounts, and there are only a handful of tilesets per
//     session (mirrors how the atlas blob is kept alive). Bounded leak.
//
// Keyed on `xml|tileset`; a slot-map change invalidates server-side via
// the sidecar fingerprint (a new bake overwrites the same disk path), and
// callers that mutate a tileset (STI inject) should call
// `invalidatePaletteSheet` to drop the stale browser blob.
const _paletteSheetBlobCache = new Map<string, Promise<string>>();

function _paletteSheetKey(
  xmlPath: string,
  tileset: number,
  fingerprint?: string,
): string {
  // Versioned by the slot-map fingerprint so a tileset mutation yields a
  // new key -> fresh fetch (no stale sheet after add/replace/fork).
  return `${xmlPath} ${tileset} ${fingerprint ?? ""}`;
}

/** Get the palette sprite-sheet blob URL for (xml, tileset), reusing a
 * cached, app-lifetime blob URL when one exists. The returned URL must
 * NOT be revoked by the caller — the cache owns its lifetime. A warm call
 * resolves from an already-settled promise (no network). On fetch failure
 * the rejected promise is evicted so a later call can retry. */
export function getCachedPaletteSheetBlobUrl(
  xmlPath: string,
  tileset: number,
  fingerprint?: string,
): Promise<string> {
  if (fingerprint === undefined) {
    // Resolve the current fingerprint so this call keys identically to the
    // display components; fall back to an unversioned fetch if meta fails.
    return getPaletteSheetMeta(xmlPath, tileset)
      .then((meta) =>
        getCachedPaletteSheetBlobUrl(xmlPath, tileset, meta.fingerprint),
      )
      .catch(() => fetchPaletteSheetBlobUrl(xmlPath, tileset));
  }
  const key = _paletteSheetKey(xmlPath, tileset, fingerprint);
  const existing = _paletteSheetBlobCache.get(key);
  if (existing) return existing;
  // New fingerprint for this (xml, tileset) → the slot map changed, so any
  // prior-fingerprint blob is dead. Evict + revoke it so the cache doesn't
  // accumulate one Blob per mutation. (Display consumers reset sheetUrl to
  // null on the fingerprint-change re-render, so the old blob isn't on
  // screen when we reach here.)
  const stalePrefix = `${xmlPath} ${tileset} `;
  for (const k of [..._paletteSheetBlobCache.keys()]) {
    if (k !== key && k.startsWith(stalePrefix)) {
      const stale = _paletteSheetBlobCache.get(k);
      _paletteSheetBlobCache.delete(k);
      if (stale) stale.then((u) => URL.revokeObjectURL(u)).catch(() => {});
    }
  }
  const p = fetchPaletteSheetBlobUrl(xmlPath, tileset).catch((err) => {
    // Don't cache failures — drop so the next open retries from scratch.
    _paletteSheetBlobCache.delete(key);
    throw err;
  });
  _paletteSheetBlobCache.set(key, p);
  return p;
}

/** Drop the cached browser blob for (xml, tileset) (revoking it) so the
 * next open re-fetches the freshly-baked sheet. Call after any mutation
 * that changes the tileset's slot map (e.g. importing an STI). */
export function invalidatePaletteSheet(xmlPath: string, tileset: number): void {
  // The cache is fingerprint-versioned (`xml tileset fp`), so match by the
  // (xml, tileset) prefix and drop every version, revoking each blob.
  const prefix = `${xmlPath} ${tileset} `;
  for (const key of [..._paletteSheetBlobCache.keys()]) {
    if (key.startsWith(prefix)) {
      const p = _paletteSheetBlobCache.get(key);
      _paletteSheetBlobCache.delete(key);
      if (p) p.then((u) => URL.revokeObjectURL(u)).catch(() => {});
    }
  }
}

/** Warm everything the Asset Browser needs for (xml, tileset): the
 * sidecar disk cache (via the bake stream) AND the browser-side blob URL,
 * so a subsequent open is instant. Safe to fire-and-forget; errors are
 * the caller's to swallow. `onEvent` forwards bake progress so a caller
 * can surface it, but most callers pass nothing (silent background warm).
 *
 * Returns the blob URL so an eager caller could await it, but the common
 * path is `prefetchPaletteSheet(...).catch(() => {})`. */
export async function prefetchPaletteSheet(
  xmlPath: string,
  tileset: number,
  onEvent?: (e: PaletteSheetBuildEvent) => void,
  fingerprint?: string,
): Promise<string> {
  // (1) Ensure the disk cache is baked (cache-hit returns ~instantly).
  await streamPaletteSheetBuild(xmlPath, tileset, onEvent ?? (() => {}));
  // (2) Warm — and cache — the browser blob URL off the now-warm disk
  //     cache. Reuses the shared cache so the viewer's open is a hit.
  return getCachedPaletteSheetBlobUrl(xmlPath, tileset, fingerprint);
}

// ─── Session-based editing (Phase 2A) ───────────────────────────────
export interface SessionInfo {
  session_id: string;
  dat_path: string;
  xml_path: string;
  tileset: number;
  rows: number;
  cols: number;
  dirty: boolean;
  edit_count: number;
  created_at: number;
  last_used_at: number;
  /** True when the session was opened from a slf:// URI. Backend
   * refuses /edits and /save for read-only sessions; the UI should
   * gate editing controls accordingly. The atlas/parsed render path
   * still works. */
  read_only?: boolean;
  /** Original URI the client passed to /sessions (slf://... or a
   * filesystem path). Useful for debug + status display. */
  source_uri?: string;
}

export interface SessionEdit {
  x: number;
  y: number;
  op: EditOp;
  layer?: LayerName;
  entry_index?: number;
  slot?: number;
  sub?: number;
  room_id?: number;
  /** Per-tile terrain height (0–255), for `set_height`. */
  height?: number;
  /** Used by `set_entries` to restore an undo snapshot: full entry
   * list for (x, y, layer). Each entry is a [slot, sub] pair. */
  entries?: number[][];
}

export interface ApplyEditsResult {
  applied: number;
  session: SessionInfo;
}

export interface SaveResult {
  session: SessionInfo;
  bytes_written: number;
  backup_path: string | null;
}

export function openSession(
  datPath: string,
  xmlPath: string,
  tileset: number,
): Promise<SessionInfo> {
  return jsonPost<SessionInfo>("/mapforge/sessions", {
    dat: datPath, xml: xmlPath, tileset,
  });
}

export function closeSession(sessionId: string): Promise<{ closed: string }> {
  return jsonDelete<{ closed: string }>(`/mapforge/sessions/${sessionId}`);
}

export function applyEdits(
  sessionId: string,
  edits: SessionEdit[],
): Promise<ApplyEditsResult> {
  return jsonPut<ApplyEditsResult>(
    `/mapforge/sessions/${sessionId}/edits`,
    { edits },
  );
}

export function saveSession(sessionId: string): Promise<SaveResult> {
  return jsonPost<SaveResult>(`/mapforge/sessions/${sessionId}/save`, {});
}

// ────────────────────────────────────────────────────────────────────────
//  Generators — first-class map generation subsystem (task #114)
// ────────────────────────────────────────────────────────────────────────

export interface GeneratorParamSchema {
  name: string;
  type: "int" | "float" | "str" | "bool";
  default: unknown;
  description: string;
  min: number | null;
  max: number | null;
}

export interface GeneratorInfo {
  name: string;
  label: string;
  description: string;
  params: GeneratorParamSchema[];
}

/** One event from the generator stream. Three shapes:
 *
 * - **phase**: `{phase: str, status: "start"|"done", label: str}` —
 *   bracketed progress markers. Used by the log/progress UI to show
 *   "Clearing 25600 tiles…" then "Cleared.".
 *
 * - **op**: `{op: SessionEdit}` — one edit op that's ALREADY been
 *   applied server-side. The client mirrors it to local atlas state
 *   so the canvas updates incrementally without a roundtrip.
 *
 * - **done**: `{done: true, ok: bool, applied: int, error?: str,
 *   message?: str}` — the stream's final event. Always emitted.
 */
export interface GeneratorPhaseEvent {
  phase: string;
  status: "start" | "done";
  label: string;
  /** Expected total ops for this phase (or for the whole run if the
   *  generator only emits one phase). When present, the wizard's
   *  RunningView renders a real fill bar with width = opCount/total.
   *  For probabilistic generators (scatter, density-falloff) this is
   *  an upper bound; the bar may stop short of 100%. */
  total?: number;
}

export interface GeneratorOpEvent {
  op: SessionEdit;
}

export interface GeneratorDoneEvent {
  done: true;
  ok: boolean;
  applied: number;
  error?: string;
  message?: string;
  generator?: string;
  /** Present (true) when this was a dry-run preview — nothing applied. */
  dry_run?: boolean;
  /** Dry-run only: how many in-bounds ops the generator emitted. */
  op_count?: number;
}

export type GeneratorEvent =
  | GeneratorPhaseEvent
  | GeneratorOpEvent
  | GeneratorDoneEvent;

/** List the built-in generators with their param schemas. */
export function listGenerators(): Promise<GeneratorInfo[]> {
  return jsonGet<GeneratorInfo[]>("/mapforge/generators");
}

/** One (source, biome) cell of the distilled-corpus coverage report. */
export interface CorpusCoverageCell {
  n_maps: number;
  n_buildings: number;
  layers: string[];
  has_buildings: boolean;
}

/** Coverage of the distilled generator corpus — which (source, biome) cells
 *  carry data. Drives the corpus_source + biome dropdowns in the generator
 *  param form and the gray-out of empty cells. `available` is false when the
 *  corpus JSON isn't shipped with this build. */
export interface CorpusCoverage {
  available: boolean;
  sources: string[];
  biomes: string[];
  layers: string[];
  source_installs: Record<string, string>;
  coverage: Record<string, Record<string, CorpusCoverageCell>>;
}

/** Fetch the distilled-corpus coverage for the generator param dropdowns. */
export function getCorpusCoverage(): Promise<CorpusCoverage> {
  return jsonGet<CorpusCoverage>("/mapforge/corpus/coverage");
}

/** One pickable building kind for the StarCraft-style placement flow — a
 *  (corpus_source, biome) cell with building data. wall/roof (slot, sub)
 *  are the dominant pieces (thumbnail representatives); the size range is
 *  empirical from the corpus. */
export interface BuildingCatalogEntry {
  id: string;
  label: string;
  corpus_source: string;
  biome: string;
  wall_slot: number;
  wall_sub: number;
  roof_slot: number;
  roof_sub: number;
  has_door: boolean;
  n_buildings: number;
  min_w: number;
  max_w: number;
  min_h: number;
  max_h: number;
  default_w: number;
  default_h: number;
}

/** List the building catalog (empty when the corpus isn't shipped). */
export function listBuildings(): Promise<BuildingCatalogEntry[]> {
  return jsonGet<BuildingCatalogEntry[]>("/mapforge/buildings");
}

// ────────────────────────────────────────────────────────────────────────
//  Canon building library — verbatim building grafts from real maps
// ────────────────────────────────────────────────────────────────────────

/** One tile of a library building — shape-compatible with
 *  mapClipboard's ClipTile (dx/dy/layers/room/height) so pasteEdits
 *  works on a composed region without translation. */
export interface BuildingLibraryTile {
  dx: number;
  dy: number;
  layers: Record<LayerName, number[][]>;
  /** Normalized source room id (1..N within the building; 0 = none). */
  room: number;
  height: number;
}

/** One building extracted verbatim from a real map of this tileset. */
export interface BuildingLibraryEntry {
  id: string;
  /** "Bar — C5 (The Den) · 9×7 · 2 rooms" */
  label: string;
  /** Contents-heuristic function ("Bar", "House", … or "Building"). */
  function: string;
  town: string;
  sector: string;
  source_map: string;
  tileset: number;
  w: number;
  h: number;
  room_count: number;
  /** How many maps carry this exact building (dedupe count). */
  seen_in: number;
  thumb_png_b64: string;
  /** STRUCTURE: land/floors, walls+doors+windows+decals, roofs,
   *  onroofs, wall drop shadows. */
  tiles: BuildingLibraryTile[];
  /** CONTENTS: objs layer, furniture structs, furniture shadows. */
  contents_tiles: BuildingLibraryTile[];
}

export interface BuildingLibraryResponse {
  tileset: number;
  install_root: string;
  entries: BuildingLibraryEntry[];
  scanned_maps: number;
  matching_maps: number;
  skipped_clusters: number;
  build_ms: number;
  from_cache?: boolean;
}

/** Fetch the canon building library for (install, tileset). The first
 *  call per pair scans + renders thumbnails server-side (can take tens
 *  of seconds); afterwards it's served from a fingerprinted cache. */
export function listBuildingLibrary(
  xmlPath: string,
  tileset: number,
): Promise<BuildingLibraryResponse> {
  const qs = new URLSearchParams({
    xml: xmlPath, tileset: String(tileset),
  });
  return jsonGet<BuildingLibraryResponse>(
    `/mapforge/building-library?${qs.toString()}`,
  );
}

/**
 * Run a generator against a session, streaming each emitted op (+ each
 * phase event) to `onEvent` as it arrives.
 *
 * Returns the final `{done, ok, applied}` event after the stream
 * closes. Throws on HTTP errors (failed to open stream); generator-
 * internal failures arrive as `{done: true, ok: false, error: ...}`
 * and DON'T throw — the caller decides whether to surface them as a
 * mutation-failed UI state or as a partial-success.
 */
export async function runGenerator(
  sessionId: string,
  name: string,
  params: Record<string, unknown>,
  onEvent: (e: GeneratorEvent) => void,
  opts?: {
    /** Stream the ops WITHOUT applying anything (live-preview ghosting).
     * Generators are seeded-deterministic, so a dry run with the same
     * params is exactly what a real run would apply. */
    dryRun?: boolean;
    /** Abort the stream early (e.g. params changed mid-preview). */
    signal?: AbortSignal;
  },
): Promise<GeneratorDoneEvent> {
  const url = `/mapforge/sessions/${sessionId}/run-generator?name=${encodeURIComponent(name)}`;
  const res = await authedFetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ params, dry_run: opts?.dryRun === true }),
    signal: opts?.signal,
  });
  if (!res.ok || !res.body) {
    let detail: unknown = null;
    try { detail = await res.json(); } catch {}
    throw new Error(`generator stream failed: HTTP ${res.status}: ${JSON.stringify(detail)}`);
  }
  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buf = "";
  let finalEvt: GeneratorDoneEvent | null = null;
  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buf += decoder.decode(value, { stream: true });
    let nl = buf.indexOf("\n");
    while (nl !== -1) {
      const line = buf.slice(0, nl).trim();
      buf = buf.slice(nl + 1);
      if (line.length > 0) {
        // Parse inside try, dispatch outside — TODO #14 fix mirroring
        // streamExtractSlf in lib/tools.ts.
        let evt: GeneratorEvent | null = null;
        try {
          evt = JSON.parse(line) as GeneratorEvent;
        } catch (err) {
          // eslint-disable-next-line no-console
          console.warn("bad generator event line", line, err);
        }
        if (evt) {
          onEvent(evt);
          if ("done" in evt && evt.done) {
            finalEvt = evt as GeneratorDoneEvent;
          }
        }
      }
      nl = buf.indexOf("\n");
    }
  }
  if (!finalEvt) {
    throw new Error("generator stream closed without 'done' event");
  }
  return finalEvt;
}

export function sessionInspectTile(
  sessionId: string,
  x: number,
  y: number,
): Promise<TileInspection> {
  const q = qs({ x, y });
  return jsonGet<TileInspection>(`/mapforge/sessions/${sessionId}/tile?${q}`);
}

export async function fetchSessionRender(params: {
  sessionId: string;
  room?: number;
  bbox?: string;
  ring?: number;
  full?: boolean;
  highlight?: boolean;
  skipLayers?: string;
  scale?: number;
}): Promise<RenderResult> {
  const query = qs({
    room: params.room,
    bbox: params.bbox,
    ring: params.ring,
    full: params.full ? "true" : undefined,
    highlight: params.highlight === false ? "false" : undefined,
    skip_layers: params.skipLayers,
    scale: params.scale,
    _: Date.now(),  // cache buster — see fetchSectorRender
  });
  const res = await authedFetch(
    `/mapforge/sessions/${params.sessionId}/render?${query}`
  );
  if (!res.ok) {
    let detail: unknown = null;
    try { detail = await res.json(); } catch {}
    throw new Error(
      `Render failed (HTTP ${res.status}): ${JSON.stringify(detail)}`
    );
  }
  const meta: RenderMeta = {
    ixMin: parseInt(res.headers.get("X-MapForge-IxMin") ?? "0", 10),
    iyMin: parseInt(res.headers.get("X-MapForge-IyMin") ?? "0", 10),
    canvasW: parseInt(res.headers.get("X-MapForge-CanvasW") ?? "0", 10),
    canvasH: parseInt(res.headers.get("X-MapForge-CanvasH") ?? "0", 10),
    tileW: parseInt(res.headers.get("X-MapForge-TileW") ?? "40", 10),
    tileH: parseInt(res.headers.get("X-MapForge-TileH") ?? "20", 10),
  };
  const blob = await res.blob();
  return { url: URL.createObjectURL(blob), meta };
}

// ─── Stateless edit (Phase 2 fallback, will be replaced) ─────────────
export async function editTile(body: EditTileBody): Promise<EditTileResult> {
  const res = await authedFetch("/mapforge/sector/edit-tile", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!res.ok) {
    let detail: unknown = null;
    try { detail = await res.json(); } catch {}
    throw new Error(
      `Edit failed (HTTP ${res.status}): ${JSON.stringify(detail)}`
    );
  }
  return res.json() as Promise<EditTileResult>;
}

/**
 * Fetch the sector render PNG and return a blob: URL suitable for
 * <img src=...>. Caller MUST call URL.revokeObjectURL on the returned
 * url when the image is no longer needed (otherwise memory leaks).
 *
 * Why blob URL instead of direct URL:
 *   1. Tauri's default CSP (tauri.conf.json) allows img-src from
 *      'self' tauri://localhost data: blob: — NOT http://127.0.0.1:*.
 *      A direct <img src="http://127.0.0.1:PORT/..."> gets blocked.
 *   2. <img> tags can't set custom HTTP headers, so they can't send
 *      the X-MercWizard-Token. When the sidecar runs with auth on,
 *      a direct-URL img would 401.
 *   Fetch-then-blob solves both: fetch sends the token, the blob URL
 *   is on the allowed scheme list.
 */
export async function fetchSectorRenderBlobUrl(params: {
  datPath: string;
  xmlPath: string;
  tileset: number;
  room?: number;
  bbox?: string;       // "x0,y0,x1,y1"
  ring?: number;
  full?: boolean;
  highlight?: boolean;
  skipLayers?: string; // comma-separated
  scale?: number;
}): Promise<string> {
  const query = qs({
    dat: params.datPath,
    xml: params.xmlPath,
    tileset: params.tileset,
    room: params.room,
    bbox: params.bbox,
    ring: params.ring,
    full: params.full ? "true" : undefined,
    highlight: params.highlight === false ? "false" : undefined,
    skip_layers: params.skipLayers,
    scale: params.scale,
  });
  const res = await authedFetch(`/mapforge/sector/render?${query}`);
  if (!res.ok) {
    let detail: unknown = null;
    try { detail = await res.json(); } catch {}
    throw new Error(
      `Render failed (HTTP ${res.status}): ${JSON.stringify(detail)}`
    );
  }
  const blob = await res.blob();
  return URL.createObjectURL(blob);
}

/**
 * Iso-projection metadata for one render. Frontend uses these to invert
 * the projection: turn (clickX, clickY) on the rendered <img> into a
 * (tile_x, tile_y) sector coord.
 *
 * Math (mirrors iso_renderer.py:266-268):
 *   canvas_x = (tx - ty) * tileW/2 - ix_min
 *   canvas_y = (tx + ty) * tileH/2 - iy_min
 * Inverse:
 *   A = (canvas_x + ix_min) / (tileW/2)  =  tx - ty
 *   B = (canvas_y + iy_min) / (tileH/2)  =  tx + ty
 *   tx = (A + B) / 2
 *   ty = (B - A) / 2
 */
export interface RenderMeta {
  ixMin: number;
  iyMin: number;
  canvasW: number;
  canvasH: number;
  tileW: number;
  tileH: number;
}

export interface RenderResult {
  url: string;          // blob: URL — caller MUST URL.revokeObjectURL when done
  meta: RenderMeta;
}

export async function fetchSectorRender(params: {
  datPath: string;
  xmlPath: string;
  tileset: number;
  room?: number;
  bbox?: string;
  ring?: number;
  full?: boolean;
  highlight?: boolean;
  skipLayers?: string;
  scale?: number;
}): Promise<RenderResult> {
  const query = qs({
    dat: params.datPath,
    xml: params.xmlPath,
    tileset: params.tileset,
    room: params.room,
    bbox: params.bbox,
    ring: params.ring,
    full: params.full ? "true" : undefined,
    highlight: params.highlight === false ? "false" : undefined,
    skip_layers: params.skipLayers,
    scale: params.scale,
    // Cache buster — even though the response sets Cache-Control:
    // no-store, some webview / proxy layers still serve cached PNGs
    // for identical URLs. Adding a per-call nonce guarantees the URL
    // differs whenever any input (incl. skip_layers) changes.
    _: Date.now(),
  });
  const res = await authedFetch(`/mapforge/sector/render?${query}`);
  if (!res.ok) {
    let detail: unknown = null;
    try { detail = await res.json(); } catch {}
    throw new Error(
      `Render failed (HTTP ${res.status}): ${JSON.stringify(detail)}`
    );
  }
  const meta: RenderMeta = {
    ixMin: parseInt(res.headers.get("X-MapForge-IxMin") ?? "0", 10),
    iyMin: parseInt(res.headers.get("X-MapForge-IyMin") ?? "0", 10),
    canvasW: parseInt(res.headers.get("X-MapForge-CanvasW") ?? "0", 10),
    canvasH: parseInt(res.headers.get("X-MapForge-CanvasH") ?? "0", 10),
    tileW: parseInt(res.headers.get("X-MapForge-TileW") ?? "40", 10),
    tileH: parseInt(res.headers.get("X-MapForge-TileH") ?? "20", 10),
  };
  const blob = await res.blob();
  return { url: URL.createObjectURL(blob), meta };
}

/**
 * Invert iso projection: image pixel (px, py) → tile (x, y).
 * Returns null when the result falls outside the sector. Clamps within
 * bounds otherwise.
 */
export function imagePixelToTile(
  px: number,
  py: number,
  meta: RenderMeta,
  sectorCols: number,
  sectorRows: number,
): { x: number; y: number } | null {
  // See IsoRenderer.ts:imagePixelToTile for the convention + derivation.
  // tileToCanvasPixel returns the TOP-LEFT of the tile bounding box
  // (west apex of the diamond), and the diamond extends down-right.
  // Inverse: tx = round((A+B)/2 - 1), ty = round((B-A)/2).
  const hw = meta.tileW / 2;
  const hh = meta.tileH / 2;
  const A = (px + meta.ixMin) / hw;
  const B = (py + meta.iyMin) / hh;
  const tx = Math.round((A + B) / 2 - 1);
  const ty = Math.round((B - A) / 2);
  if (tx < 0 || ty < 0 || tx >= sectorCols || ty >= sectorRows) return null;
  return { x: tx, y: ty };
}

/**
 * Forward iso projection: tile (x, y) → image pixel of the south apex
 * of that tile's diamond on the rendered canvas.
 */
export function tileToCanvasPixel(
  tx: number,
  ty: number,
  meta: RenderMeta,
): { x: number; y: number } {
  const hw = meta.tileW / 2;
  const hh = meta.tileH / 2;
  return {
    x: (tx - ty) * hw - meta.ixMin,
    y: (tx + ty) * hh - meta.iyMin,
  };
}

/**
 * The 4 corners of a tile's diamond in canvas pixel space, in the
 * order [north, east, south, west] — useful as polygon points.
 */
export function tileDiamondCorners(
  tx: number,
  ty: number,
  meta: RenderMeta,
): [[number, number], [number, number], [number, number], [number, number]] {
  // (sx, sy) is the tile's TOP-LEFT bounding-box corner. Diamond
  // extends down-right. See IsoRenderer.ts for the convention.
  const { x: sx, y: sy } = tileToCanvasPixel(tx, ty, meta);
  const hw = meta.tileW / 2;
  const hh = meta.tileH / 2;
  return [
    [sx + hw, sy],            // N (top)
    [sx + 2 * hw, sy + hh],   // E (right)
    [sx + hw, sy + 2 * hh],   // S (bottom)
    [sx, sy + hh],            // W (left)
  ];
}

// ─── Phase 3: client-side renderer (atlas + parsed dict) ────────────
// Together these power IsoRenderer.ts — sidecar serves data only; the
// browser composites with ctx.drawImage so edits feel instant.

/** Per-frame Z-strip metadata for multi-tile structures. Mirror of
 * the Python `ZStripInfo` Pydantic model (sidecar/routes/mapforge.py).
 *
 * Populated on AtlasCells whose JSD DB_STRUCTURE has
 * `ubNumberOfTiles > 1` — lawless4 sub 16 furniture, multi-tile
 * vehicles, trees, etc. The WebGL renderer reads this to:
 *   - split the sprite into N depth-distinct quads (one per strip)
 *   - depth = base + (running Z sum) × DEPTH_PER_STRIP_UNIT
 *   - dispatch to a batch whose depthFunc honors `burns_through`:
 *       false → gl.LESS  (STRICT — equal Z SKIPS; non-wall blitter
 *                          at renderworld.cpp:5061-5063 uses `JAE`)
 *       true  → gl.LEQUAL (BurnsThrough — equal Z draws; wall blitter
 *                          at renderworld.cpp:5221+)
 *
 * Each strip is 20 px wide (= WORLD_TILE_X / 2) except the first one
 * which may be 1-20 px (stored in `first_strip_width`). Total strip
 * count is `1 + z_changes.length`. */
export interface ZStripInfo {
  initial_z_change: number;     // bInitialZChange, signed -127..127
  first_strip_width: number;    // ubFirstZStripWidth, 1-20 px
  z_changes: number[];          // pbZChange[], each in {-1, 0, +1}
  burns_through: boolean;       // true → wall LEQUAL; false → non-wall LESS
}

export interface AtlasCell {
  slot: number;
  sub: number;
  /** Pixel rect of this sprite inside the atlas PNG. */
  x: number;
  y: number;
  w: number;
  h: number;
  /** STI offset_x / offset_y (engine-semantic INT16 — already sign-corrected
   * by the sidecar). Used as sprite top-left offset from the tile's south
   * apex when compositing: paste_x = tile_screen_x + ox. */
  ox: number;
  oy: number;
  /** Z-strip metadata for engine-faithful clipping. Set when the
   * sprite's DB_STRUCTURE has nTiles > 1; absent/null otherwise. The
   * WebGL renderer dispatches differently per (zstrip == null) vs
   * (zstrip != null, with burns_through). See ZStripInfo docs. */
  zstrip?: ZStripInfo | null;
}

export interface JsdFootprintTile {
  /** Tile-coordinate X offset from the anchor (the clicked tile). */
  bX: number;
  /** Tile-coordinate Y offset from the anchor. */
  bY: number;
  /** 1-based STI sub to place at this offset. The painter uses this
   * directly as the entry's `sub` field. */
  sub: number;
}

export interface JsdFootprint {
  /** Ordered list of footprint tiles. Always length >= 2 (single-tile
   * slots don't appear in `slot_jsd_footprint` at all). The first
   * tile is the anchor (bX=0, bY=0) with sub=1. */
  tiles: JsdFootprintTile[];
}

export interface AtlasManifest {
  tileset: number;
  xml_path: string;
  atlas_w: number;
  atlas_h: number;
  fingerprint: string;
  cells: AtlasCell[];
  slot_filenames: Record<number, string>;
  /** Slot → true when the slot's STI has a sibling .jsd somewhere in
   * the install (loose or in Tilesets.slf). The inspector uses this
   * to show "View JSD" buttons only for struct entries that actually
   * have multi-tile footprint data. Pre-existing cached manifests
   * may not contain this field; treat absent as no-JSD. */
  slot_has_jsd?: Record<number, boolean>;
  /** Slot → stamp recipe for slots whose JSD has more than one
   * footprint tile. When the user paints with a brush whose slot
   * appears here AND the paintMode setting is "stamp" (or Shift is
   * NOT held), one click emits one paint op per `tiles[i]` so the
   * whole heli / vehicle / multi-tile struct lands in one action.
   * Slots not in this map are single-tile and behave as today. */
  slot_jsd_footprint?: Record<number, JsdFootprint>;
  /** True for a full-tileset atlas; false for a sector-specific
   * PARTIAL atlas the frontend requests via the `sessionId` parameter
   * on getAtlasManifest/fetchAtlasBlobUrl. Partial atlases contain
   * only the sprites the open sector references — sufficient for
   * sector RENDERING but missing JSD/footprint data, so multi-tile
   * stamp + "View JSD" UI should degrade gracefully until the
   * complete atlas swap completes. Older cached manifests may not
   * carry this field — treat absent as true (existing complete bake). */
  complete?: boolean;
}

export function getAtlasManifest(
  xmlPath: string,
  tileset: number,
  opts: { bypassCache?: boolean; sessionId?: string } = {},
): Promise<AtlasManifest> {
  const params: Record<string, string | number | undefined> = { xml: xmlPath, tileset };
  // After an add-to-tileset, the slot map changes → backend computes
  // a new fingerprint + bakes fresh. But the URL is identical, so
  // the BROWSER may serve the previous manifest from HTTP cache and
  // miss the new cells. The bypass flag adds a per-call timestamp
  // to force a network round-trip.
  if (opts.bypassCache) params._ = Date.now();
  // sessionId triggers the sector-specific partial bake on the backend
  // — returns a manifest covering only sprites the session's sector
  // uses, plus `complete: false`. Pair with a follow-up call WITHOUT
  // sessionId to fetch the complete atlas in the background.
  if (opts.sessionId) params.session_id = opts.sessionId;
  return jsonGet<AtlasManifest>(
    `/mapforge/tileset/atlas-manifest?${qs(params)}`,
  );
}

/**
 * NDJSON stream of atlas bake progress. Call BEFORE `fetchAtlasBlobUrl`
 * — the bake takes 1-5 seconds on a cold cache (loading ~150 STIs from
 * SLF archives) and the subsequent PNG fetch is fast only AFTER the
 * bake has written the atlas to disk. Streaming the bake's phases here
 * lets the UI show real progress through the slow part; the PNG fetch
 * is then instant.
 *
 * On cache hit, the stream emits a single "cache-hit" phase + "done"
 * within ~50 ms — barely a flicker. Cold builds advance through
 * load-stis / pack / render / encode / persist phases.
 */
export type AtlasBuildEvent =
  | { event: "phase";    phase: string; label: string }
  | { event: "progress"; current: number; total: number; detail: string }
  | { event: "done";     atlas_w: number; atlas_h: number;
                          fingerprint: string; png_size?: number }
  | { event: "error";    message: string };

export async function streamAtlasBuild(
  xmlPath: string,
  tileset: number,
  onEvent: (e: AtlasBuildEvent) => void,
  opts: { sessionId?: string } = {},
): Promise<{ atlas_w: number; atlas_h: number; fingerprint: string }> {
  // sessionId triggers a partial bake — same semantics as
  // getAtlasManifest. The progress stream emits a `jsd-cache-hit` or
  // `jsd-harvest` phase only on FULL bakes (no sessionId).
  const params: Record<string, string | number | undefined> = {
    xml: xmlPath, tileset,
  };
  if (opts.sessionId) params.session_id = opts.sessionId;
  const res = await authedFetch(
    `/mapforge/tileset/atlas/build?${qs(params)}`,
  );
  if (!res.ok || !res.body) {
    let detail: unknown = null;
    try { detail = await res.json(); } catch {}
    throw new Error(
      `atlas build stream failed: HTTP ${res.status}: ${JSON.stringify(detail)}`,
    );
  }
  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buf = "";
  let final: { atlas_w: number; atlas_h: number; fingerprint: string } | null = null;
  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buf += decoder.decode(value, { stream: true });
    let nl = buf.indexOf("\n");
    while (nl !== -1) {
      const line = buf.slice(0, nl).trim();
      buf = buf.slice(nl + 1);
      if (line.length > 0) {
        // Parse inside try, dispatch outside — TODO #14 fix mirroring
        // streamExtractSlf in lib/tools.ts.
        let evt: AtlasBuildEvent | null = null;
        try {
          evt = JSON.parse(line) as AtlasBuildEvent;
        } catch (err) {
          // eslint-disable-next-line no-console
          console.warn("bad atlas build event line", line, err);
        }
        if (evt) {
          onEvent(evt);
          if (evt.event === "done") {
            final = {
              atlas_w: evt.atlas_w,
              atlas_h: evt.atlas_h,
              fingerprint: evt.fingerprint,
            };
          } else if (evt.event === "error") {
            throw new Error(`atlas build failed: ${evt.message}`);
          }
        }
      }
      nl = buf.indexOf("\n");
    }
  }
  if (!final) {
    throw new Error("atlas build stream closed without 'done' event");
  }
  return final;
}

/** NDJSON stream of palette-SHEET bake progress. Parallel to
 * `streamAtlasBuild` but for the asset palette's per-slot thumbnail
 * sprite sheet (used by both MapForgePalette and TilesetEditor's slot
 * grid). Cold bake is the dominant wait on opening the Asset Browser
 * for a tileset whose sheet isn't cached — up to 60+ seconds on large
 * tilesets. Call BEFORE `fetchPaletteSheetBlobUrl` so the subsequent
 * sheet fetch hits the disk cache instantly. */
export type PaletteSheetBuildEvent =
  | { event: "phase";    phase: string; label: string; total?: number }
  | { event: "progress"; current: number; total: number; detail: string }
  | { event: "done";     from_cache: boolean; size: number }
  | { event: "error";    message: string };

export async function streamPaletteSheetBuild(
  xmlPath: string,
  tileset: number,
  onEvent: (e: PaletteSheetBuildEvent) => void,
): Promise<{ from_cache: boolean; size: number }> {
  const res = await authedFetch(
    `/mapforge/tileset/palette-sheet/build?${qs({ xml: xmlPath, tileset })}`,
  );
  if (!res.ok || !res.body) {
    let detail: unknown = null;
    try { detail = await res.json(); } catch {}
    throw new Error(
      `palette-sheet build stream failed: HTTP ${res.status}: ${JSON.stringify(detail)}`,
    );
  }
  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buf = "";
  let final: { from_cache: boolean; size: number } | null = null;
  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buf += decoder.decode(value, { stream: true });
    let nl = buf.indexOf("\n");
    while (nl !== -1) {
      const line = buf.slice(0, nl).trim();
      buf = buf.slice(nl + 1);
      if (line.length > 0) {
        // Parse inside try, dispatch outside — TODO #14 fix mirroring
        // streamExtractSlf in lib/tools.ts.
        let evt: PaletteSheetBuildEvent | null = null;
        try {
          evt = JSON.parse(line) as PaletteSheetBuildEvent;
        } catch (err) {
          // eslint-disable-next-line no-console
          console.warn("bad palette-sheet build event line", line, err);
        }
        if (evt) {
          onEvent(evt);
          if (evt.event === "done") {
            final = { from_cache: evt.from_cache, size: evt.size };
          } else if (evt.event === "error") {
            throw new Error(`palette-sheet build failed: ${evt.message}`);
          }
        }
      }
      nl = buf.indexOf("\n");
    }
  }
  if (!final) {
    throw new Error("palette-sheet build stream closed without 'done' event");
  }
  return final;
}


/** Fetch the atlas PNG as a blob URL — caller MUST URL.revokeObjectURL.
 * Atlas is large (~2-8 MB) so the cache layer matters; the sidecar
 * fingerprint-keys the URL so a slot map change invalidates without
 * any client-side coordination.
 *
 * Optional `onProgress(loadedBytes, totalBytes | null)`: when the
 * response advertises a Content-Length header, the callback receives
 * real download progress as bytes stream in. When the header is
 * missing (rare; the sidecar always sets it) `totalBytes` is null and
 * the caller should show indeterminate progress. */
export async function fetchAtlasBlobUrl(
  xmlPath: string,
  tileset: number,
  onProgress?: (loaded: number, total: number | null) => void,
  opts: { bypassCache?: boolean; sessionId?: string } = {},
): Promise<string> {
  const params: Record<string, string | number | undefined> = { xml: xmlPath, tileset };
  // See getAtlasManifest for the rationale: after an STI is added to
  // the tileset, the URL is unchanged but the underlying PNG has
  // been re-baked with the new cells. The browser's HTTP cache
  // (max-age=86400 on this endpoint) would serve the pre-add PNG to
  // the reload path. Cache-bust to force a network fetch.
  if (opts.bypassCache) params._ = Date.now();
  // sessionId triggers the partial-atlas fetch (sector-specific subset).
  if (opts.sessionId) params.session_id = opts.sessionId;
  const res = await authedFetch(
    `/mapforge/tileset/atlas?${qs(params)}`,
  );
  if (!res.ok) throw new Error(`atlas fetch failed: HTTP ${res.status}`);
  // Stream the body so we can report byte progress to the UI. Without
  // streaming, .blob() resolves only after the whole download, leaving
  // the user staring at 0% for the entire fetch.
  const lenHeader = res.headers.get("Content-Length");
  const total = lenHeader ? parseInt(lenHeader, 10) : null;
  if (!res.body || total === null) {
    // Fallback: no streaming or no length header — just .blob() and
    // emit a single 100% on completion.
    const blob = await res.blob();
    onProgress?.(blob.size, total);
    return URL.createObjectURL(blob);
  }
  const reader = res.body.getReader();
  const chunks: Uint8Array[] = [];
  let loaded = 0;
  // Throttle onProgress to ~once per 32 KB so React isn't re-rendering
  // a progress bar on every TCP read (those can arrive every few KB).
  const reportEvery = Math.max(32 * 1024, Math.floor((total ?? 0) / 50));
  let nextReport = reportEvery;
  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    chunks.push(value);
    loaded += value.length;
    if (onProgress && loaded >= nextReport) {
      onProgress(loaded, total);
      nextReport = loaded + reportEvery;
    }
  }
  onProgress?.(loaded, total);
  // Reassemble — Blob constructor takes BufferSource[] so we cast
  // Uint8Array chunks to satisfy TS's BlobPart union.
  const blob = new Blob(chunks as BlobPart[], { type: "image/png" });
  return URL.createObjectURL(blob);
}

/**
 * The full parsed dict for a session, suitable for client-side rendering
 * and edit-time mutation. Mirror of ParsedSector in mapforge.py.
 *
 * Layer arrays: each one is `list[rows*cols] of list[[slot, sub], ...]`.
 * `[slot, sub]` is exactly what's stored in the .dat (sub is 1-based).
 */
export interface ParsedSector {
  session_id: string;
  rows: number;
  cols: number;
  tileset: number;
  land: number[][][];
  objs: number[][][];
  shadows: number[][][];
  structs: number[][][];
  roofs: number[][][];
  onroofs: number[][][];
  rooms: number[];
  heights: number[];
  world_flags: number[];
  edit_count: number;
  dirty: boolean;
}

export function getSessionParsed(sessionId: string): Promise<ParsedSector> {
  // Cache-buster: the Tauri webview / dev proxy can serve a stale GET
  // even when FastAPI sends no Cache-Control. After a generator run the
  // resync MUST see the just-mutated server state — without `_=…` we
  // sometimes get the pre-generator parsed dict and `setParsed(stale)`
  // overwrites the correctly-mirrored client mutations. Same pattern as
  // fetchSectorRender at line ~638 (bug #30).
  return jsonGet<ParsedSector>(
    `/mapforge/sessions/${sessionId}/parsed?_=${Date.now()}`,
  );
}

// ─── SLF → loose extraction (read-only SLF map → editable loose) ────
// JA2's VFS prefers loose `.dat` files over SLF entries at load time,
// so once a sector is extracted to the layer the active VFS profile
// mounts, it transparently replaces the SLF version both in-engine
// and in the editor. No repacking needed.
//
// The destination layer is now resolved via the install's actual VFS
// config (vfs_config.<active>.ini → highest-priority writable
// directory profile), NOT a hardcoded "Data-1.13/Maps" guess. Fixes
// the H4-saga case where a reference install + Vanilla VFS + MapForge
// writing to Data-1.13/Maps meant the engine never saw the edit.

export interface ExtractSlfMapResult {
  loose_path: string;
  install_root: string;
  overwrote_existing: boolean;
  /** VFS profile the destination was resolved into (e.g. "v113",
   * "Vanilla", "UserProf"). Helps the user verify the file landed
   * in the layer the running engine actually reads. */
  target_profile?: string | null;
  target_layer_path?: string | null;
  /** "vfs_config" = resolved via the install's active VFS chain
   * (preferred). "heuristic-fallback" = couldn't introspect the VFS
   * so we used the old "first Data dir wins" heuristic; means the
   * destination MAY not match what the engine reads. */
  target_layer_source?: string;
}

export interface ExtractSlfPreview {
  proposed_loose_path: string;
  target_profile?: string | null;
  target_layer_path?: string | null;
  target_layer_source?: string;
  already_exists: boolean;
  install_root: string;
}

export function extractSlfToLoose(
  slfUri: string,
): Promise<ExtractSlfMapResult> {
  return jsonPost<ExtractSlfMapResult>(
    "/mapforge/sector/extract-slf-to-loose",
    { slf_uri: slfUri },
  );
}

/** Preview where extract-to-loose would write WITHOUT actually
 * writing. Used by the SLF read-only banner to show the user the
 * destination + VFS profile before they click Extract — so they can
 * cancel if the layer doesn't match the install's running VFS
 * config (which would make their edits invisible in-game). */
export function previewExtractSlfToLoose(
  slfUri: string,
): Promise<ExtractSlfPreview> {
  return jsonGet<ExtractSlfPreview>(
    `/mapforge/sector/extract-slf-preview?${qs({ slf_uri: slfUri })}`,
  );
}

// ─── Phase 4: STI library (Asset Browser catalog) ────────────────────
// Browse the 4000+ unique STIs that the sibling Asset Browser project
// has cataloged across all 23 JA2 installs on the machine, then import
// chosen ones into the active install's tileset.

export interface LibrarySti {
  sha256: string;
  width: number | null;
  height: number | null;
  frame_count: number | null;
  has_jsd: boolean;
  kind: string;            // "tile_sti" | "tilecache_sti"
  name: string;            // basename of one source occurrence
  install_count: number;
  in_current_tileset: boolean;
  tags: string[];
}

export interface LibraryStiList {
  page: number;
  per_page: number;
  total: number;
  tag_filter: string | null;
  query: string | null;
  items: LibrarySti[];
}

export interface LibraryStiOccurrence {
  install_id: number;
  install_label: string;
  install_root: string;
  relpath: string;
  is_in_slf: boolean;
  slf_member: string | null;
}

export interface LibraryStiDetail {
  sha256: string;
  kind: string;
  width: number | null;
  height: number | null;
  frame_count: number | null;
  has_jsd: boolean;
  size_bytes: number;
  tags: string[];
  occurrences: LibraryStiOccurrence[];
}

export interface LibraryTag {
  name: string;
  subframe_count: number;
  source: string | null;
}

export interface LibraryHealth {
  available: boolean;
  catalog_path: string;
  thumbs_dir?: string;
  sti_count?: number;
  message?: string;
}

export interface AddStiToTilesetResult {
  sha256: string;
  tileset: number;
  slot: number;
  filename: string;
  install_root: string;
  written_to: string;
  xml_backup_path: string | null;
  jsd_copied: boolean;
}

/** Tracked entry for the rail's "Just added" panel — survives session
 * reloads via localStorage. Each entry combines the catalog sha256
 * (so we can fetch thumbs + sub grids later) with the post-add
 * placement (slot + tileset + filename), plus enough source metadata
 * to render without round-tripping the detail endpoint on every
 * paint of the rail. `added_at` is epoch ms used for sort + a soft
 * 24h fade-out in the UI. */
export interface RecentAddition {
  sha256: string;
  sti_filename: string;
  slot: number;
  tileset: number;
  added_at: number;
  frame_count: number | null;
  has_jsd: boolean;
}

/** One sub-frame of a library STI, as returned by the Phase 3
 * `/stis/{sha256}/subs` endpoint. `sha256` is the per-sub sha (NOT the
 * parent STI's), suitable for the `/subframes/{sha}/thumb` PNG
 * endpoint. */
export interface LibrarySub {
  sub_idx: number;
  sha256: string;
  width: number;
  height: number;
  tags: string[];
}

export interface LibrarySubList {
  sti_sha256: string;
  subs: LibrarySub[];
}

export function getLibraryHealth(): Promise<LibraryHealth> {
  return jsonGet<LibraryHealth>("/mapforge/library/health");
}

export function listLibraryStis(opts: {
  page?: number;
  per_page?: number;
  q?: string;
  tag?: string;
  kind?: string;
  has_jsd?: boolean;
  width?: number;
  height?: number;
  xml?: string;      // for in-current-tileset badging
  tileset?: number;
}): Promise<LibraryStiList> {
  const params: Record<string, string | number | undefined> = {
    page: opts.page ?? 1,
    per_page: opts.per_page ?? 48,
    q: opts.q,
    tag: opts.tag,
    kind: opts.kind ?? "tile_sti",
    has_jsd: opts.has_jsd !== undefined ? (opts.has_jsd ? "true" : "false") : undefined,
    width: opts.width,
    height: opts.height,
    xml: opts.xml,
    tileset: opts.tileset,
  };
  return jsonGet<LibraryStiList>(`/mapforge/library/stis?${qs(params)}`);
}

export function getLibraryStiDetail(sha256: string): Promise<LibraryStiDetail> {
  return jsonGet<LibraryStiDetail>(`/mapforge/library/stis/${sha256}`);
}

export function getLibraryTags(): Promise<LibraryTag[]> {
  return jsonGet<LibraryTag[]>("/mapforge/library/tags");
}

/** Build the authed thumbnail URL. Thumbnails are PNGs cached by the
 * Asset Browser; the sidecar serves them with a long cache header so
 * the grid scrolls smoothly. NOTE: <img src=...> can't send the
 * X-MercWizard-Token header — for that we'd need fetch+blob, but the
 * Tauri CSP allows http://127.0.0.1:* for img-src in dev (and the
 * token check is bypassed for local-only same-origin requests when
 * auth is off). For now the URL is suitable for the dev workflow;
 * upgrade to blob-fetched if auth bites. */
export async function getLibraryStiThumbBlobUrl(sha256: string): Promise<string> {
  const res = await authedFetch(`/mapforge/library/stis/${sha256}/thumb`);
  if (!res.ok) throw new Error(`thumb fetch failed: HTTP ${res.status}`);
  const blob = await res.blob();
  return URL.createObjectURL(blob);
}

export function addStiToTileset(
  sha256: string,
  body: {
    tileset: number;
    target_slot?: number;
    target_filename?: string;
    /** Cap from user settings (mapforgeSettings.ts `engineMaxTileSlot`).
     * Backend uses this to bound auto-pick AND to reject manual slots
     * above the cap. Stock JA2 1.13 = 150. */
    engine_max_tile_slot?: number;
    /** Opt-in to silence cap-rejection (for users staging XML against
     * a forked ja2.exe). Backend returns the result with an
     * X-MercForge-Warning header instead of erroring. */
    allow_above_cap?: boolean;
    /** When set, the destination STI contains ONLY this one sub-frame
     * (re-encoded via ja2py.save_8bit_sti). Filename is auto-suffixed
     * with `_subN` to avoid collision with a future whole-STI import. */
    target_sub?: number;
  },
): Promise<AddStiToTilesetResult> {
  return jsonPost<AddStiToTilesetResult>(
    `/mapforge/library/stis/${sha256}/add-to-tileset`,
    { sha256, ...body },
  );
}

/** Result of copying a tile from one LIVE tileset slot into another
 * tileset of the same install (the stock-tileset browser's
 * "Add to current tileset" action). */
export interface CopyTileToTilesetResult {
  src_tileset: number;
  src_slot: number;
  dest_tileset: number;
  slot: number;
  filename: string;
  install_root: string;
  written_to: string;
  xml_backup_path: string | null;
  jsd_copied: boolean;
}

/** Copy a tile from `srcTileset`/`srcSlot` (a slot already registered in
 * the active install's Ja2Set.dat.xml) into `dest_tileset`, registering
 * it as a NEW slot. Unlike addStiToTileset this reads the LIVE tileset,
 * not the (unshipped) asset-library catalog.
 *
 * Engine-safety mirrors the backend contract: the copy APPENDS a new
 * `<file index>` (never shifts existing indices, so saved sectors stay
 * valid); `target_slot` defaults to the SOURCE slot (same tile-type
 * family); slots above `engine_max_tile_slot` are rejected. `auto_pick`
 * is the SLOT_TAKEN recovery path — it lands the tile in the lowest free
 * slot, which puts it in a DIFFERENT tile-type family (warn the user). */
export function copyTileToTileset(
  srcTileset: number,
  srcSlot: number,
  body: {
    dest_tileset: number;
    /** Destination slot. Omit → backend defaults to the SOURCE slot. */
    target_slot?: number;
    /** Cap-bounded auto-pick of the lowest free slot (SLOT_TAKEN
     * recovery). Overrides target_slot + the src-slot default. */
    auto_pick?: boolean;
    /** Copy only this sub-frame as a fresh single-frame STI (filename
     * suffixed `_subN`). Omit → whole STI (+ sibling .jsd if present). */
    target_sub?: number;
    /** Engine cap from settings (mapforgeSettings.ts). Stock 1.13 = 150. */
    engine_max_tile_slot?: number;
    /** Opt-in to silence cap-rejection (forked ja2.exe staging). */
    allow_above_cap?: boolean;
  },
): Promise<CopyTileToTilesetResult> {
  return jsonPost<CopyTileToTilesetResult>(
    `/mapforge/library/tilesets/${srcTileset}/slots/${srcSlot}/copy-to-tileset`,
    body,
  );
}

/** Phase 3: list every sub-frame of the given library STI. Powers
 * the sub-grid in AddStiToTilesetModal + the "View subs" affordance
 * on RecentAdditionCard. Read-only — the catalog is the source of
 * truth, no edits ever go via this endpoint. */
export function listLibrarySubs(sha256: string): Promise<LibrarySubList> {
  return jsonGet<LibrarySubList>(`/mapforge/library/stis/${sha256}/subs`);
}

/** Phase 3: blob URL for a single sub-frame's thumbnail. The PNG is
 * cached by the Asset_Browser scanner and proxied through the
 * MercWizard2 sidecar so a single auth token works for STI + sub
 * thumbs from the same origin. */
export async function getLibrarySubThumbBlobUrl(sub_sha256: string): Promise<string> {
  const res = await authedFetch(`/mapforge/library/subframes/${sub_sha256}/thumb`);
  if (!res.ok) throw new Error(`sub thumb fetch failed: HTTP ${res.status}`);
  const blob = await res.blob();
  return URL.createObjectURL(blob);
}


// ─── Phase 4: inject-sub flow ────────────────────────────────────────

export interface LooseSlot {
  slot: number;
  filename: string;
  path: string;
  frame_count: number;
}

export interface LooseSlotList {
  tileset: number;
  slots: LooseSlot[];
}

export interface InjectSubResult {
  tileset: number;
  slot: number;
  sti_filename: string;
  new_sub_index: number;
  frames_after: number;
  backup_path: string | null;
}

/** Phase 4: list every slot in the active install's tileset whose
 * STI is loose-on-disk (mutable). Drives the destination dropdown
 * of the inject-sub modal — SLF-only slots are excluded because v1
 * inject can't extract from SLFs first. */
export function listLooseSlots(tileset: number): Promise<LooseSlotList> {
  return jsonGet<LooseSlotList>(`/mapforge/library/tilesets/${tileset}/loose-slots`);
}

/** Phase 4: append one sub-frame from a library STI onto an existing
 * tileset slot's STI binary. The destination must be loose-on-disk
 * (use listLooseSlots to enumerate) and have a matching 8-bit
 * palette (unless `force=true`). On success the destination .sti is
 * backed up to .sti.bak (first edit per session) and the atlas cache
 * for the tileset is invalidated so the new sub renders on next paint. */
export function injectSubToTileset(
  src_sha256: string,
  body: {
    tileset: number;
    target_slot: number;
    src_sub: number;
    force?: boolean;
  },
): Promise<InjectSubResult> {
  return jsonPost<InjectSubResult>(
    `/mapforge/library/stis/${src_sha256}/inject-sub`,
    { src_sha256, ...body },
  );
}
