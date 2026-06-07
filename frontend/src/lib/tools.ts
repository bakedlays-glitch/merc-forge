/**
 * Tools — frontend API client for the /api/v1/tools/* sidecar routes.
 *
 * These are install-independent utilities: pick a file → inspect it.
 * STI Viewer + SLF Extractor share this client; mirrors the Pydantic
 * models in sidecar/routes/tools.py.
 */

import { getServerPort, getServerToken } from "./tauri";

// ─── Types (must match sidecar/routes/tools.py) ─────────────────────

export interface StiFrameInfo {
  index: number;
  width: number;
  height: number;
  /** Signed engine-canonical offset. ja2py stores as UINT16; sidecar
   * converts to INT16 here. */
  offset_x: number;
  offset_y: number;
}

export interface StiViewerMeta {
  path: string;
  size_bytes: number;
  is_8bit: boolean;
  /** Canvas width from the STI's top-level header. */
  width: number;
  height: number;
  frame_count: number;
  palette_present: boolean;
  has_jsd: boolean;
  jsd_path: string | null;
  frames: StiFrameInfo[];
}

/** Parsed JSD payload for the read-only viewer. Mirrors `JsdParsed`
 * in routes/mapforge.py — same shape, but exposed by the standalone
 * tool so the STI viewer doesn't have to depend on tileset context. */
export interface ToolsJsdProfileTile {
  bXPos: number;
  bYPos: number;
  sPosRelToBase: number;
  profile: number[][];
}

export interface ToolsJsdParsed {
  sti_filename: string;
  jsd_path: string;
  size_bytes: number;
  szId: string;
  n_struct: number;
  n_stored: number;
  struct_data_size: number;
  n_image_tile_locs: number;
  flags_int: number;
  flag_names: string[];
  ubArmour: number;
  ubHP: number;
  ubDensity: number;
  ubNumberOfTiles: number;
  bZTileOffsetX: number;
  bZTileOffsetY: number;
  tiles: ToolsJsdProfileTile[];
}

export interface SlfEntry {
  /** Forward-slash relative path inside the archive. No leading slash. */
  relpath: string;
  size: number;
}

export interface SlfListing {
  path: string;
  library_name: string;
  library_path: string;
  entry_count: number;
  entries: SlfEntry[];
}

export interface SlfExtractBody {
  slf_path: string;
  dest_dir: string;
  /** When omitted, extracts every entry. When set, only entries whose
   * relpath matches (case-insensitive) get extracted. */
  members?: string[];
  /** Default: true. When false, existing target files stay put and
   * `skipped` counts them. */
  overwrite?: boolean;
}

export interface SlfExtractResult {
  extracted: number;
  skipped: number;
  errors: string[];
  dest_dir: string;
}

export type SlfExtractEvent =
  | { event: "phase";    label: string }
  | { event: "progress"; current: number; total: number; detail: string }
  | { event: "done";     data: SlfExtractResult }
  | { event: "error";    message: string };

// ─── Internal helpers ───────────────────────────────────────────────

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
    try { detail = await res.json(); } catch { /* fall-through */ }
    throw new Error(
      `Tools ${path} → HTTP ${res.status}: ${JSON.stringify(detail)}`
    );
  }
  return res.json() as Promise<T>;
}

async function jsonPost<T>(path: string, body: unknown): Promise<T> {
  const res = await authedFetch(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!res.ok) {
    let detail: unknown = null;
    try { detail = await res.json(); } catch { /* fall-through */ }
    throw new Error(
      `Tools POST ${path} → HTTP ${res.status}: ${JSON.stringify(detail)}`
    );
  }
  return res.json() as Promise<T>;
}

function qs(params: Record<string, string | number | boolean | undefined>): string {
  // Widened to accept boolean per TODO #18 — a future caller passing
  // `qs({ flag: true })` was previously excluded from the type union,
  // which would compile-pass and then drop the param at runtime
  // (silent bug). Booleans serialize as "true" / "false" to match
  // Pydantic's permissive bool parsing on the backend.
  const u = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) {
    if (v !== undefined && v !== null && v !== "") u.set(k, String(v));
  }
  return u.toString();
}

// ─── STI viewer ─────────────────────────────────────────────────────

export function decodeStiMeta(path: string): Promise<StiViewerMeta> {
  return jsonGet<StiViewerMeta>(`/tools/sti/decode?${qs({ path })}`);
}

/** Fetch one STI frame as a blob URL. Caller is responsible for
 * revoking via `URL.revokeObjectURL`. Same pattern as
 * `fetchStiFrameBlobUrl` in lib/mapforge.ts (used by the embedded
 * palette viewer). */
export async function fetchStiFrameBlobUrl(
  path: string,
  frame: number,
): Promise<string> {
  const blob = await fetchStiFrameBlob(path, frame);
  return URL.createObjectURL(blob);
}

/** Same as `fetchStiFrameBlobUrl` but returns the raw Blob — keep it
 * around for clipboard writes via `navigator.clipboard.write` (which
 * needs the Blob, not the object URL). */
export async function fetchStiFrameBlob(
  path: string,
  frame: number,
): Promise<Blob> {
  const res = await authedFetch(`/tools/sti/frame?${qs({ path, frame })}`);
  if (!res.ok) {
    let detail: unknown = null;
    try { detail = await res.json(); } catch { /* fall-through */ }
    throw new Error(
      `STI frame fetch failed (HTTP ${res.status}): ${JSON.stringify(detail)}`
    );
  }
  return res.blob();
}

export function getStiJsd(path: string): Promise<ToolsJsdParsed> {
  return jsonGet<ToolsJsdParsed>(`/tools/sti/jsd?${qs({ path })}`);
}

export interface SaveFrameResult {
  out_path: string;
  bytes_written: number;
}

/** Decode + write one frame to disk via the backend. Paired with
 * Tauri's `pickSaveFile` dialog for the destination path. */
export function saveStiFrameAsPng(
  stiPath: string,
  frame: number,
  outPath: string,
): Promise<SaveFrameResult> {
  return jsonPost<SaveFrameResult>("/tools/sti/save-frame", {
    sti_path: stiPath,
    frame,
    out_path: outPath,
  });
}

// ─── SLF extractor ──────────────────────────────────────────────────

export function listSlf(path: string): Promise<SlfListing> {
  return jsonGet<SlfListing>(`/tools/slf/list?${qs({ path })}`);
}

export function extractSlf(body: SlfExtractBody): Promise<SlfExtractResult> {
  return jsonPost<SlfExtractResult>("/tools/slf/extract", body);
}

/** Streaming variant of `extractSlf`. Emits per-entry progress events;
 * resolves with the final result on the "done" event. Mirrors the
 * NDJSON pattern from lib/mapforge.ts::streamInstallMaps. */
export async function streamExtractSlf(
  body: SlfExtractBody,
  onEvent?: (e: SlfExtractEvent) => void,
): Promise<SlfExtractResult> {
  const res = await authedFetch("/tools/slf/extract/stream", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!res.ok || !res.body) {
    let detail: unknown = null;
    try { detail = await res.json(); } catch { /* fall-through */ }
    throw new Error(
      `extract stream failed: HTTP ${res.status}: ${JSON.stringify(detail)}`
    );
  }
  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buf = "";
  let finalData: SlfExtractResult | null = null;
  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buf += decoder.decode(value, { stream: true });
    let nl = buf.indexOf("\n");
    while (nl !== -1) {
      const line = buf.slice(0, nl).trim();
      buf = buf.slice(nl + 1);
      if (line.length > 0) {
        // Parse inside try, dispatch outside. The previous pattern
        // wrapped BOTH steps in a try/catch, so a backend "error" event
        // got rethrown into the same catch that handled JSON.parse
        // failures — the surfaced error swallowed it and the function
        // returned the generic "stream closed without 'done' event"
        // instead of the real backend error message. Found by the
        // 2026-05-25 code review (HIGH).
        let evt: SlfExtractEvent | null = null;
        try {
          evt = JSON.parse(line) as SlfExtractEvent;
        } catch (err) {
          // eslint-disable-next-line no-console
          console.warn("bad extract event line", line, err);
        }
        if (evt) {
          onEvent?.(evt);
          if (evt.event === "done") finalData = evt.data;
          else if (evt.event === "error") {
            throw new Error(`extract failed: ${evt.message}`);
          }
        }
      }
      nl = buf.indexOf("\n");
    }
  }
  if (!finalData) {
    throw new Error("extract stream closed without 'done' event");
  }
  return finalData;
}
