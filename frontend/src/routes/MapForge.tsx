/**
 * MapForge hub — lists .dat sector files in the active install. Click a
 * sector to open it in the viewer/inspector.
 *
 * Phase 0 (read-only): no edit ops yet. This is the entry point for the
 * editor-module-to-be.
 */
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { formatApiError } from "../lib/api";
import {
  getMapForgeHealth,
  getSectorInfo,
  streamAtlasBuild,
  streamInstallMaps,
  type ScanEvent,
} from "../lib/mapforge";

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
  const [filter, setFilter] = useState("");
  const [sourceFilter, setSourceFilter] = useState<"all" | "loose" | "slf">("all");
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
            JA2 sector inspector and editor (Phase 0: read-only inspector).
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
            <button
              type="button"
              onClick={() => rescan.mutate()}
              disabled={rescan.isPending}
              className="shrink-0 rounded border border-gray-700 bg-gray-900 px-3 py-1 text-xs hover:border-blue-500 hover:bg-gray-800 disabled:opacity-50"
              title="Force a fresh scan of all loose Maps dirs + SLF archives."
            >
              {rescan.isPending ? "Rescanning…" : "Rescan"}
            </button>
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
