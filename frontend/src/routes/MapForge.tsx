/**
 * MapForge hub — lists .dat sector files in the active install. Click a
 * sector to open it in the viewer/inspector.
 *
 * Phase 0 (read-only): no edit ops yet. This is the entry point for the
 * editor-module-to-be.
 */
import { useCallback, useEffect, useMemo, useRef, useState, type CSSProperties } from "react";
import { Link, useNavigate } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { formatApiError } from "../lib/api";
import {
  fetchRadarThumbSheetUrl,
  getMapForgeHealth,
  getRadarThumbMeta,
  getSectorInfo,
  getSectorNames,
  streamAtlasBuild,
  streamInstallMaps,
  type ScanEvent,
  type SectorMapFile,
} from "../lib/mapforge";
import {
  pushRecentSector,
  readJournalEntry,
  readRecentSectors,
  type RecentSector,
} from "../lib/brushBuckets";

// ─── Strategic-grid geometry ────────────────────────────────────────────
// JA2's surface strategic map is 16 rows (A–P, top→bottom) × 16 columns
// (1–16, left→right). A sector code is <rowLetter><colNumber>, e.g. the
// classic starting sector "A9" = row A, column 9. The .dat filenames use
// exactly this code (A9.DAT), so the grid cell → file lookup is a direct
// match on the derived code.
const GRID_ROWS = "ABCDEFGHIJKLMNOP".split(""); // 16 rows
const GRID_COLS = Array.from({ length: 16 }, (_, i) => i + 1); // 1..16

// Radar-thumbnail sheet handed down to the grid: the sprite-sheet blob URL,
// a code→cell-origin map, and the sheet geometry. Each cell paints its
// sector's 88x44 minimap via CSS background-position. null = off / loading.
interface GridThumbs {
  sheetUrl: string;
  cells: Map<string, { x: number; y: number }>;
  cols: number;
  rows: number;
  cellW: number;
  cellH: number;
}

/** `A9.DAT` → "A9"; `a9_b1.dat` → "A9" (basements share the surface
 * sector's code). Mirrors building_library.sector_grid_from_name. Returns
 * "" when the stem doesn't start with the <letter><number> pattern. */
function sectorGridFromName(name: string): string {
  const stem = name.replace(/\.[^.]*$/, "").toUpperCase();
  const base = stem.split("_")[0] ?? "";
  return /^[A-P]([1-9]|1[0-6])$/.test(base) ? base : "";
}

/** Live phase / progress state populated as the scan stream emits
 * events. Drives the inline progress UI under the Map Forge header. */
interface ScanProgress {
  phase: string;
  label: string;
  current?: number;
  total?: number;
  detail?: string;
}

export default function MapForge() {
  const health = useQuery({
    queryKey: ["mapforge", "health"],
    queryFn: getMapForgeHealth,
  });
  const qc = useQueryClient();
  const navigate = useNavigate();
  const [filter, setFilter] = useState("");
  const [sourceFilter, setSourceFilter] = useState<"all" | "loose" | "slf">("all");
  // Strategic-grid vs flat-list view. Default to the strategic grid — it's
  // the orientation most users want ("which sector is the bar in?").
  const [view, setView] = useState<"grid" | "list">("grid");
  // Radar map thumbnails on the strategic grid (opt-in, persisted). Off by
  // default — the multi-MB sprite sheet only fetches when the user enables
  // it, so the hub stays light for users who just want the sector picker.
  const [showThumbs, setShowThumbs] = useState<boolean>(() => {
    try { return localStorage.getItem("mapforge.showThumbs") === "1"; }
    catch { return false; }
  });
  const toggleThumbs = useCallback(() => {
    setShowThumbs((v) => {
      const next = !v;
      try { localStorage.setItem("mapforge.showThumbs", next ? "1" : "0"); }
      catch { /* private mode / quota — non-fatal */ }
      return next;
    });
  }, []);
  // Recently-opened sectors (MRU, persisted). Seeded from localStorage and
  // bumped whenever a sector is opened from this hub.
  const [recent, setRecent] = useState<RecentSector[]>(() => readRecentSectors());
  // The scan endpoint streams progress; we surface the latest event
  // here so the UI can show real-time phase + per-SLF counters.
  const [scanProgress, setScanProgress] = useState<ScanProgress | null>(null);

  const handleScanEvent = (evt: ScanEvent) => {
    if (evt.event === "phase") {
      setScanProgress({ phase: evt.phase, label: evt.label });
    } else if (evt.event === "progress") {
      setScanProgress((p) => p
        ? { ...p, current: evt.current, total: evt.total, detail: evt.detail }
        : { phase: "scan", label: "Scanning",
            current: evt.current, total: evt.total, detail: evt.detail });
    } else if (evt.event === "done") {
      // Final event — let the query layer take over rendering.
      setScanProgress(null);
    }
  };

  const maps = useQuery({
    queryKey: ["mapforge", "installs", "maps"],
    queryFn: () => streamInstallMaps({ onEvent: handleScanEvent }),
    enabled: health.data?.renderer_available === true && health.data.active_install_id !== null,
    retry: false,
    // Cache is on disk — no need to re-fetch every mount.
    staleTime: 24 * 60 * 60 * 1000,
  });
  const rescan = useMutation({
    mutationFn: () => streamInstallMaps({ rescan: true, onEvent: handleScanEvent }),
    onSuccess: (fresh) => {
      qc.setQueryData(["mapforge", "installs", "maps"], fresh);
      setScanProgress(null);
    },
  });

  // SectorNames.xml grid→town-name map for the strategic grid's cell
  // labels. Non-fatal if absent: the endpoint returns {} and the grid
  // falls back to bare sector codes.
  const sectorNames = useQuery({
    queryKey: ["mapforge", "installs", "sector-names"],
    queryFn: getSectorNames,
    enabled: health.data?.renderer_available === true
      && health.data.active_install_id !== null,
    retry: false,
    staleTime: 60 * 60 * 1000,
  });

  // Radar-thumbnail sheet (opt-in). Two queries: the manifest (code→cell
  // origin) + the sprite-sheet blob URL. Only fetched in grid view with
  // thumbnails enabled, so the multi-MB PNG is never pulled otherwise.
  const activeInstallId = health.data?.active_install_id ?? null;
  const thumbsActive = view === "grid" && showThumbs
    && health.data?.renderer_available === true
    && activeInstallId !== null;
  const thumbMeta = useQuery({
    queryKey: ["mapforge", "radar-thumbs", "meta", activeInstallId],
    queryFn: getRadarThumbMeta,
    enabled: thumbsActive,
    retry: false,
    staleTime: 60 * 60 * 1000,
  });
  const thumbSheet = useQuery({
    queryKey: ["mapforge", "radar-thumbs", "sheet", activeInstallId],
    queryFn: fetchRadarThumbSheetUrl,
    enabled: thumbsActive,
    retry: false,
    // gcTime:0 evicts the cache entry on unmount so the revoke below can
    // never leave a REVOKED blob URL cached for a return visit (the blank-
    // mosaic trap main.tsx documents for the roster sheet). Each mount
    // re-fetches a fresh URL; the server sheet is disk-cached so it's cheap.
    gcTime: 0,
  });
  // Revoke the blob URL when it changes or the component unmounts — the
  // sheet is multi-MB, so a leak per install switch / toggle adds up. Safe
  // because gcTime:0 drops the cache entry in lockstep (no stale reuse).
  useEffect(() => {
    const url = thumbSheet.data;
    return () => { if (url) URL.revokeObjectURL(url); };
  }, [thumbSheet.data]);
  // Sector code → cell origin (O(1) lookup from each grid cell).
  const thumbCells = useMemo(() => {
    const m = new Map<string, { x: number; y: number }>();
    for (const c of thumbMeta.data?.cells ?? []) {
      m.set(c.code.toUpperCase(), { x: c.x, y: c.y });
    }
    return m;
  }, [thumbMeta.data]);
  const thumbs = useMemo<GridThumbs | null>(() => {
    if (!thumbsActive || !thumbSheet.data || !thumbMeta.data) return null;
    return {
      sheetUrl: thumbSheet.data,
      cells: thumbCells,
      cols: thumbMeta.data.cols,
      rows: thumbMeta.data.rows,
      cellW: thumbMeta.data.cell_w,
      cellH: thumbMeta.data.cell_h,
    };
  }, [thumbsActive, thumbSheet.data, thumbMeta.data, thumbCells]);
  const thumbsLoading = thumbsActive
    && (thumbMeta.isLoading || thumbSheet.isLoading);

  // Filter the map list by name substring + source (loose/slf/all). The
  // user typically knows the sector code (e.g. "C13" or "a9") and just
  // wants to jump straight to it.
  const filteredMaps = useMemo(() => {
    if (!maps.data) return [];
    const needle = filter.trim().toLowerCase();
    return maps.data.maps.filter((m) => {
      if (sourceFilter !== "all" && m.source !== sourceFilter) return false;
      if (needle && !m.name.toLowerCase().includes(needle)) return false;
      return true;
    });
  }, [maps.data, filter, sourceFilter]);

  // Hover-intent prefetch for sector cards. Opening a sector cold runs:
  //   getSectorInfo → openSession → streamAtlasBuild → fetchAtlasBlobUrl
  // which adds up to several seconds on a SLF-bundled sector.
  //
  // The earlier eager-on-hover version saturated the sidecar's
  // single-writer queue: a mouse-sweep across 50 cards queued 50
  // sector/info + 50 atlas builds back-to-back, and a subsequent
  // CLICK got stuck behind that queue — its GET literally couldn't
  // fire until the prefetch backlog drained. A user hit
  // this on C6: OPTIONS preflighted, GET never followed, "Parsing
  // .dat…" forever.
  //
  // New strategy:
  //   1. HOVER-INTENT — delay 250ms before firing. If the cursor
  //      moves off the card within that window, drop the request.
  //      A real "intent to click" usually pauses on the card.
  //   2. CONCURRENCY CAP — at most 2 prefetches in flight at any
  //      moment, so a real click can still race through the queue
  //      ahead of stragglers.
  //   3. DEDUPE — once a path is prefetched (or in flight), don't
  //      fire again.
  const prefetched = useRef<Set<string>>(new Set());
  const inFlight = useRef<Set<string>>(new Set());
  const hoverTimer = useRef<number | null>(null);
  const xmlPath = maps.data?.ja2set_xml ?? null;
  const MAX_CONCURRENT_PREFETCHES = 2;
  const HOVER_INTENT_MS = 250;

  // Build the /mapforge/sector URL for a sector .dat. Tileset is omitted:
  // the sector route auto-detects it from the .dat header (tileset=0
  // sentinel) — same as the flat-list cards below. So a grid click only
  // needs dat (+ xml when available).
  const sectorHref = useCallback((datPath: string): string => {
    const xmlQ = maps.data?.ja2set_xml
      ? `&xml=${encodeURIComponent(maps.data.ja2set_xml)}`
      : "";
    return `/mapforge/sector?dat=${encodeURIComponent(datPath)}${xmlQ}`;
  }, [maps.data?.ja2set_xml]);

  // Record a sector in the recent-MRU then navigate to it. Used by grid
  // cells + recent-row chips (the flat-list <Link>s record on click via
  // a shared onClick — see below).
  const openSector = useCallback((m: { path: string; name: string; grid?: string }) => {
    const label = m.grid
      ? `${m.name} (${m.grid})`
      : m.name;
    setRecent(pushRecentSector({
      datPath: m.path,
      label,
      grid: m.grid,
      openedAt: Date.now(),
    }));
    navigate(sectorHref(m.path));
  }, [navigate, sectorHref]);

  // Index the scanned .dat files by sector grid code (A9, C5, …) so the
  // 16×16 grid can resolve each cell to a file in O(1). Basement maps
  // (a9_b1.dat) collapse onto the surface cell — the surface .dat wins
  // (loose before SLF is already the maps[] sort/merge order, but we
  // prefer an EXACT code match over a basement-derived one regardless).
  const sectorIndex = useMemo(() => {
    const idx = new Map<string, SectorMapFile>();
    if (!maps.data) return idx;
    for (const m of maps.data.maps) {
      const code = sectorGridFromName(m.name);
      if (!code) continue;
      const exact = m.name.replace(/\.[^.]*$/, "").toUpperCase() === code;
      const prior = idx.get(code);
      // Prefer an exact surface map (A9.DAT) over a basement (A9_B1.DAT);
      // otherwise first-wins (maps[] is already loose-before-SLF ordered).
      if (!prior || exact) idx.set(code, m);
    }
    return idx;
  }, [maps.data]);

  const runPrefetch = useCallback((datPath: string) => {
    if (prefetched.current.has(datPath)) return;
    if (inFlight.current.has(datPath)) return;
    if (inFlight.current.size >= MAX_CONCURRENT_PREFETCHES) return;
    inFlight.current.add(datPath);
    qc.prefetchQuery({
      queryKey: ["mapforge", "sector", "info", datPath],
      queryFn: () => getSectorInfo(datPath),
      staleTime: 5 * 60 * 1000,
    }).then(() => {
      prefetched.current.add(datPath);
      if (!xmlPath) return;
      const info = qc.getQueryData<{ tileset_in_header: number }>(
        ["mapforge", "sector", "info", datPath],
      );
      if (!info || typeof info.tileset_in_header !== "number") return;
      streamAtlasBuild(xmlPath, info.tileset_in_header, () => {}).catch(() => {
        prefetched.current.delete(datPath);
      });
    }).catch(() => {
      // Drop from the cache so a future hover can retry.
    }).finally(() => {
      inFlight.current.delete(datPath);
    });
  }, [qc, xmlPath]);

  const prefetchSector = useCallback((datPath: string) => {
    // Cancel any pending hover-intent timer — the user has moved to
    // a NEW card, so the previous timer's target is no longer the
    // intent.
    if (hoverTimer.current !== null) {
      window.clearTimeout(hoverTimer.current);
    }
    hoverTimer.current = window.setTimeout(() => {
      hoverTimer.current = null;
      runPrefetch(datPath);
    }, HOVER_INTENT_MS);
  }, [runPrefetch]);

  const cancelHoverIntent = useCallback(() => {
    if (hoverTimer.current !== null) {
      window.clearTimeout(hoverTimer.current);
      hoverTimer.current = null;
    }
  }, []);

  // Cleanup pending hover-intent timer on unmount so a navigation
  // click doesn't fire a stale prefetch on the wrong page.
  useEffect(() => {
    return () => {
      if (hoverTimer.current !== null) {
        window.clearTimeout(hoverTimer.current);
        hoverTimer.current = null;
      }
    };
  }, []);

  return (
    <div className="mx-auto max-w-5xl px-6 py-8">
      <div className="mb-6 flex items-center justify-between">
        <div>
          <Link to="/" className="text-sm text-blue-400 hover:underline">
            ← MercForge Hub
          </Link>
          <h1 className="mt-2 text-2xl font-semibold">Map Forge</h1>
          <p className="text-sm text-gray-400">
            JA2 sector editor — inspect, paint, generate, validate, and
            save sectors.
          </p>
        </div>
      </div>

      {/* Renderer / install gate */}
      {health.isLoading && (
        <p className="text-sm text-gray-400">Checking MapForge backend...</p>
      )}
      {health.error && (
        <div className="rounded border border-red-700 bg-red-950 p-3 text-sm">
          <strong>MapForge backend unreachable.</strong>
          <br />
          {formatApiError(health.error)}
        </div>
      )}
      {health.data && !health.data.renderer_available && (
        <div className="rounded border border-amber-700 bg-amber-950 p-3 text-sm">
          <strong>iso_renderer.py is not importable.</strong>
          <br />
          MapForge needs the Headless_Compiler dir at{" "}
          <code className="text-amber-300">{health.data.headless_compiler_path}</code>.
          <br />
          Import error: <code>{health.data.renderer_import_error}</code>
        </div>
      )}
      {health.data?.renderer_available && health.data.active_install_id === null && (
        <div className="rounded border border-amber-700 bg-amber-950 p-3 text-sm">
          <strong>No active install.</strong> Activate one in MercForge
          Settings (or run First Run), then come back here.
        </div>
      )}

      {/* Sector list */}
      {maps.isFetching && (
        <ScanProgressPanel progress={scanProgress} />
      )}
      {maps.error && (
        <div className="rounded border border-red-700 bg-red-950 p-3 text-sm">
          {formatApiError(maps.error)}
        </div>
      )}
      {maps.data && (
        <div>
          {/* Cleaner header: a chip row (stats + cache state) on top,
              install + XML paths collapsed into a "Details" disclosure
              below. Replaces the dense paragraph that previously
              dumped everything on one line. User feedback: "this
              section is confusing and should be cleaned up". */}
          <div className="mb-3 flex items-center justify-between gap-3">
            <div className="flex flex-wrap items-center gap-2 text-xs">
              <span className="rounded bg-gray-800 px-2 py-0.5 font-mono text-gray-100">
                {maps.data.maps.length} sectors
              </span>
              <span className="text-gray-500">
                {maps.data.maps.filter((m) => m.source === "loose").length} loose ·{" "}
                {maps.data.maps.filter((m) => m.source === "slf").length} in SLF
              </span>
              <span
                className="rounded-full px-2 py-0.5 text-[10px] ring-1 ring-inset"
                style={
                  maps.data.cached
                    ? { backgroundColor: "rgb(6 78 59 / 0.4)", color: "rgb(110 231 183)", boxShadow: "inset 0 0 0 1px rgb(5 150 105 / 0.5)" }
                    : { backgroundColor: "rgb(30 58 138 / 0.4)", color: "rgb(147 197 253)", boxShadow: "inset 0 0 0 1px rgb(37 99 235 / 0.5)" }
                }
                title={`Scanned ${new Date(maps.data.scanned_at * 1000).toLocaleString()}`}
              >
                {maps.data.cached ? "✓ Cached" : "↻ Fresh"}
              </span>
              {!maps.data.ja2set_xml && (
                <span className="text-amber-400" title="No Ja2Set.dat.xml in this install — tileset palette is unavailable.">
                  ⚠ no Ja2Set.dat.xml
                </span>
              )}
            </div>
            <div className="flex shrink-0 items-center gap-2">
              {/* View toggle: strategic 16×16 grid vs flat searchable list. */}
              <div className="flex items-center overflow-hidden rounded border border-gray-700 text-xs">
                {(["grid", "list"] as const).map((v) => (
                  <button
                    key={v}
                    type="button"
                    onClick={() => setView(v)}
                    className={`px-2.5 py-1 ${
                      view === v
                        ? "bg-blue-900 text-blue-100"
                        : "bg-gray-900 text-gray-400 hover:bg-gray-800"
                    }`}
                    title={v === "grid"
                      ? "Strategic 16×16 sector grid"
                      : "Flat searchable sector list"}
                  >
                    {v === "grid" ? "▦ Grid" : "≣ List"}
                  </button>
                ))}
              </div>
              {view === "grid" && (
                <button
                  type="button"
                  onClick={toggleThumbs}
                  className={`rounded border px-2.5 py-1 text-xs ${
                    showThumbs
                      ? "border-blue-500 bg-blue-900 text-blue-100"
                      : "border-gray-700 bg-gray-900 text-gray-400 hover:bg-gray-800"
                  }`}
                  title="Show each sector's radar minimap as a thumbnail in the grid"
                >
                  {thumbsLoading ? "🗺 Maps…" : showThumbs ? "🗺 Maps on" : "🗺 Maps"}
                </button>
              )}
              <button
                type="button"
                onClick={() => rescan.mutate()}
                disabled={rescan.isPending}
                className="rounded border border-gray-700 bg-gray-900 px-3 py-1 text-xs hover:border-blue-500 hover:bg-gray-800 disabled:opacity-50"
                title="Force a fresh scan of all loose Maps dirs + SLF archives."
              >
                {rescan.isPending ? "Rescanning…" : "Rescan"}
              </button>
            </div>
          </div>
          <details className="mb-3 text-[10px] text-gray-500">
            <summary className="cursor-pointer hover:text-gray-300">Paths</summary>
            <div className="mt-1 space-y-0.5 font-mono">
              <div>install: <code>{maps.data.install_path}</code></div>
              {maps.data.ja2set_xml && (
                <div>tileset XML: <code>{maps.data.ja2set_xml}</code></div>
              )}
            </div>
          </details>
          {maps.data.maps.length === 0 && (
            <p className="text-sm text-gray-400">
              No .dat files in any of: {maps.data.data_layers.join(", ")}
            </p>
          )}

          {/* Recent sectors row — quick re-entry to lately-opened sectors,
              shown in both views. Each chip flags an unsaved-edits journal
              entry with a •. Stale entries (file rescanned away) still
              navigate; the sector route shows its own not-found state. */}
          {recent.length > 0 && (
            <div className="mb-3">
              <div className="mb-1 text-[10px] uppercase tracking-wide text-gray-500">
                Recent
              </div>
              <div className="flex flex-wrap gap-1.5">
                {recent.map((r) => {
                  const hasUnsaved = readJournalEntry(r.datPath) !== null;
                  return (
                    <button
                      key={r.datPath}
                      type="button"
                      onClick={() => openSector({
                        path: r.datPath, name: r.label, grid: r.grid,
                      })}
                      className="flex items-center gap-1 rounded-full border border-gray-700 bg-gray-900 px-2.5 py-1 text-xs hover:border-blue-500 hover:bg-gray-800"
                      title={r.datPath}
                    >
                      {hasUnsaved && (
                        <span
                          className="h-1.5 w-1.5 rounded-full bg-amber-400"
                          title="Has unsaved edits in this session"
                        />
                      )}
                      <span className="font-mono text-blue-300">{r.label}</span>
                    </button>
                  );
                })}
              </div>
            </div>
          )}

          {view === "grid" ? (
            <StrategicGrid
              sectorIndex={sectorIndex}
              names={sectorNames.data?.names ?? {}}
              namesLoading={sectorNames.isLoading}
              onOpen={openSector}
              thumbs={thumbs}
            />
          ) : (
          <div>
          {/* Filter row */}
          <div className="mb-3 flex flex-wrap items-center gap-2">
            <input
              type="search"
              value={filter}
              onChange={(e) => setFilter(e.target.value)}
              placeholder={`Filter ${maps.data.maps.length} sectors by name…`}
              className="min-w-[16rem] flex-1 rounded border border-gray-700 bg-gray-900 px-3 py-1.5 text-sm focus:border-blue-500 focus:outline-none"
            />
            <div className="flex items-center gap-1 text-xs">
              {(["all", "loose", "slf"] as const).map((s) => (
                <button
                  key={s}
                  type="button"
                  onClick={() => setSourceFilter(s)}
                  className={`rounded border px-2 py-1 ${
                    sourceFilter === s
                      ? "border-blue-600 bg-blue-900 text-blue-100"
                      : "border-gray-700 bg-gray-900 text-gray-300"
                  }`}
                >
                  {s === "all"
                    ? `All (${maps.data.maps.length})`
                    : s === "loose"
                      ? `Loose (${maps.data.maps.filter((m) => m.source === "loose").length})`
                      : `SLF (${maps.data.maps.filter((m) => m.source === "slf").length})`}
                </button>
              ))}
            </div>
            {(filter || sourceFilter !== "all") && (
              <span className="text-xs text-gray-500">
                Showing {filteredMaps.length}
              </span>
            )}
          </div>

          {filteredMaps.length === 0 && (
            <p className="text-sm text-gray-400">
              No sectors match this filter.
            </p>
          )}

          <div className="grid grid-cols-2 gap-2 md:grid-cols-3 lg:grid-cols-4 xl:grid-cols-6">
            {filteredMaps.map((m) => (
              <Link
                key={m.path}
                to={`/mapforge/sector?dat=${encodeURIComponent(m.path)}${
                  maps.data.ja2set_xml
                    ? `&xml=${encodeURIComponent(maps.data.ja2set_xml)}`
                    : ""
                }`}
                onMouseEnter={() => prefetchSector(m.path)}
                onMouseLeave={cancelHoverIntent}
                onFocus={() => prefetchSector(m.path)}
                onBlur={cancelHoverIntent}
                onClick={() => {
                  const grid = sectorGridFromName(m.name);
                  setRecent(pushRecentSector({
                    datPath: m.path,
                    label: grid ? `${m.name} (${grid})` : m.name,
                    grid: grid || undefined,
                    openedAt: Date.now(),
                  }));
                }}
                className="rounded border border-gray-700 bg-gray-900 px-3 py-2 text-sm hover:border-blue-500 hover:bg-gray-800"
                title={m.source === "slf"
                  ? `Bundled inside ${m.slf_archive}`
                  : m.path}
              >
                <div className="flex items-center justify-between gap-2">
                  <span className="truncate font-mono text-blue-300">{m.name}</span>
                  <span
                    className={`shrink-0 rounded px-1.5 text-[10px] uppercase ${
                      m.source === "slf"
                        ? "bg-amber-950 text-amber-300"
                        : "bg-gray-800 text-gray-400"
                    }`}
                  >
                    {m.source}
                  </span>
                </div>
                <div className="text-xs text-gray-500">
                  {(m.size_bytes / 1024).toFixed(1)} KB
                </div>
              </Link>
            ))}
          </div>
          </div>
          )}
        </div>
      )}
    </div>
  );
}

// ─── Strategic sector grid ───────────────────────────────────────────
// The 16×16 (A–P × 1–16) overview of the install's strategic map. Each
// cell resolves to a sector .dat (when one exists on disk) and is tinted
// by status:
//   • has-map           — a .dat exists (blue-ish, clickable)
//   • recently-edited   — has-map AND an unsaved-edits journal entry
//                         (amber accent + dot)
//   • untouched         — no .dat for this code (dim, non-clickable;
//                         the sector route can still create one from the
//                         flat list, but the grid only opens existing maps)
function StrategicGrid({
  sectorIndex,
  names,
  namesLoading,
  onOpen,
  thumbs,
}: {
  sectorIndex: Map<string, SectorMapFile>;
  names: Record<string, string>;
  namesLoading: boolean;
  onOpen: (m: { path: string; name: string; grid: string }) => void;
  thumbs: GridThumbs | null;
}) {
  const haveAny = sectorIndex.size > 0;
  return (
    <div className="overflow-x-auto">
      {/* Legend */}
      <div className="mb-2 flex flex-wrap items-center gap-3 text-[10px] text-gray-500">
        <span className="flex items-center gap-1">
          <span className="inline-block h-3 w-3 rounded-sm border border-blue-700 bg-blue-950" />
          Has map
        </span>
        <span className="flex items-center gap-1">
          <span className="inline-block h-3 w-3 rounded-sm border border-amber-600 bg-amber-950" />
          Unsaved edits
        </span>
        <span className="flex items-center gap-1">
          <span className="inline-block h-3 w-3 rounded-sm border border-gray-800 bg-gray-950" />
          No map
        </span>
        {namesLoading && <span className="text-gray-600">loading names…</span>}
        {!haveAny && (
          <span className="text-amber-400">
            No sector .dat files resolved — switch to List view for the raw
            file list.
          </span>
        )}
      </div>

      {/* 17-column grid: a leading row-label column + 16 sector columns. */}
      <div
        className={`grid ${thumbs ? "gap-0" : "gap-px"}`}
        style={{ gridTemplateColumns: "1.5rem repeat(16, minmax(2.75rem, 1fr))" }}
      >
        {/* Header row: corner cell + 1..16 column numbers. */}
        <div />
        {GRID_COLS.map((c) => (
          <div
            key={`col-${c}`}
            className="pb-1 text-center text-[10px] font-mono text-gray-500"
          >
            {c}
          </div>
        ))}

        {/* Body rows. */}
        {GRID_ROWS.map((rowLetter) => (
          <GridRow
            key={rowLetter}
            rowLetter={rowLetter}
            sectorIndex={sectorIndex}
            names={names}
            onOpen={onOpen}
            thumbs={thumbs}
          />
        ))}
      </div>
    </div>
  );
}

// One row of the strategic grid (its row-letter label + 16 sector cells).
// Split out so React only re-renders the touched row, and to keep the
// fragment-of-cells tidy.
function GridRow({
  rowLetter,
  sectorIndex,
  names,
  onOpen,
  thumbs,
}: {
  rowLetter: string;
  sectorIndex: Map<string, SectorMapFile>;
  names: Record<string, string>;
  onOpen: (m: { path: string; name: string; grid: string }) => void;
  thumbs: GridThumbs | null;
}) {
  return (
    <>
      <div className="flex items-center justify-center text-[10px] font-mono text-gray-500">
        {rowLetter}
      </div>
      {GRID_COLS.map((c) => {
        const code = `${rowLetter}${c}`;
        const file = sectorIndex.get(code);
        const name = names[code] ?? "";
        const hasMap = file !== undefined;
        const unsaved = hasMap && readJournalEntry(file.path) !== null;
        // Radar thumbnail for this cell (only on has-map cells — those are
        // the clickable, editable sectors). The single sprite sheet is
        // positioned to this sector's cell with the standard responsive
        // sprite trick: size the sheet to cols×rows of the cell box, then
        // shift by a percentage so the one cell fills the square.
        // Radar thumbnail for this cell. Painted wherever a radar exists (not
        // only editable .dat sectors) so the cells abut into a continuous
        // mosaic of the whole world map. Clickability stays gated on hasMap.
        const cell = thumbs ? thumbs.cells.get(code) : undefined;
        let bgStyle: CSSProperties | undefined;
        if (cell && thumbs) {
          const col = cell.x / thumbs.cellW;
          const row = cell.y / thumbs.cellH;
          bgStyle = {
            backgroundImage: `url(${thumbs.sheetUrl})`,
            backgroundSize: `${thumbs.cols * 100}% ${thumbs.rows * 100}%`,
            backgroundPosition:
              `${thumbs.cols > 1 ? (col / (thumbs.cols - 1)) * 100 : 0}% ` +
              `${thumbs.rows > 1 ? (row / (thumbs.rows - 1)) * 100 : 0}%`,
            backgroundRepeat: "no-repeat",
            imageRendering: "pixelated",
          };
        }
        const hasThumb = bgStyle !== undefined;
        // Mosaic mode = thumbnails loaded. Drop gaps/borders/rounding so the
        // per-sector maps connect edge-to-edge into one larger map; a sector
        // with no radar becomes a dark filler tile.
        const mosaic = thumbs != null;
        const tint = mosaic
          ? (hasThumb
              ? (unsaved ? "text-amber-100" : "text-blue-100")
              : "bg-gray-950 text-gray-700")
          : (!hasMap
              ? "border-gray-800 bg-gray-950 text-gray-600"
              : unsaved
                ? "border-amber-600 bg-amber-950/60 text-amber-100 hover:border-amber-400 hover:bg-amber-900/60"
                : "border-blue-800 bg-blue-950/50 text-blue-100 hover:border-blue-500 hover:bg-blue-900/50");
        return (
          <button
            key={code}
            type="button"
            disabled={!hasMap}
            style={bgStyle}
            onClick={() => {
              if (file) onOpen({ path: file.path, name: file.name, grid: code });
            }}
            className={`relative flex aspect-square flex-col items-center justify-center gap-0.5 overflow-hidden px-0.5 text-center leading-tight transition-colors ${mosaic ? "" : "rounded-sm border"} ${tint} ${
              hasMap ? "cursor-pointer hover:brightness-125" : "cursor-default"
            }`}
            title={hasMap
              ? `${code}${name ? ` — ${name}` : ""}${file.source === "slf" ? " (in SLF)" : ""}\n${file.path}`
              : `${code}${name ? ` — ${name}` : " — no map"}`}
          >
            {hasThumb ? (
              // Map painted: tiny code on a faint scrim so the map shows through.
              <span className="rounded bg-black/55 px-0.5 font-mono text-[8px] leading-tight text-white/90">
                {code}
              </span>
            ) : mosaic ? (
              <span className="font-mono text-[8px] text-gray-600">{code}</span>
            ) : (
              <>
                <span className="font-mono text-[10px]">{code}</span>
                {name && (
                  <span className="line-clamp-2 w-full truncate text-[9px] opacity-80">
                    {name}
                  </span>
                )}
              </>
            )}
            {unsaved && (
              <span className="absolute right-0.5 top-0.5 h-1 w-1 rounded-full bg-amber-300" />
            )}
          </button>
        );
      })}
    </>
  );
}

// ─── Scan progress panel ─────────────────────────────────────────────
// Replaces the old generic "Scanning..." spinner with a phase + per-SLF
// counter driven by the streaming /installs/maps/stream endpoint. The
// fast cache-hit path (combined cache match) emits a single "done"
// event so this panel barely flickers before disappearing.
function ScanProgressPanel({ progress }: { progress: ScanProgress | null }) {
  const phaseLabel = progress?.label ?? "Starting scan";
  const counter = progress?.current !== undefined && progress?.total !== undefined
    ? `${progress.current}/${progress.total}`
    : null;
  const pct = progress?.current !== undefined && progress?.total
    ? Math.round((progress.current / progress.total) * 100)
    : null;
  return (
    <div className="mb-3 rounded border border-blue-800 bg-blue-950/40 p-3 text-sm">
      <div className="flex items-center justify-between gap-3">
        <div className="flex items-center gap-2 text-blue-200">
          <span
            className="inline-block h-3 w-3 animate-spin rounded-full border-2 border-blue-300 border-t-transparent"
            aria-label="loading"
          />
          {phaseLabel}…
          {progress?.detail && (
            <span className="ml-1 font-mono text-blue-400">
              {progress.detail}
            </span>
          )}
        </div>
        {counter && (
          <span className="font-mono text-xs text-blue-300">
            {counter}{pct !== null && ` · ${pct}%`}
          </span>
        )}
      </div>
      {pct !== null && (
        <div className="mt-2 h-1 overflow-hidden rounded bg-gray-800">
          <div
            className="h-full bg-blue-500 transition-[width] duration-100 ease-linear"
            style={{ width: `${pct}%` }}
          />
        </div>
      )}
    </div>
  );
}
