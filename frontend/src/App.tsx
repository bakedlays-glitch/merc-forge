import { lazy, Suspense, useEffect } from "react";
import { Routes, Route, Navigate } from "react-router-dom";
import { useQuery, useQueryClient } from "@tanstack/react-query";

import { getHealth, clearApiBaseCache } from "./lib/api";
import { clearCachedPort, isRunningInTauri } from "./lib/tauri";
import { ErrorBoundary } from "./components/ErrorBoundary";

// Eagerly-loaded routes: small surfaces that need to be ready on first
// paint. Hub is the landing route; FirstRun is the gate before Hub; the
// health-error and starting-up screens render before any route resolves.
import FirstRun from "./routes/FirstRun";
import Hub from "./routes/Hub";
import Settings from "./routes/Settings";

// Lazy-loaded routes: heavier surfaces (multi-step wizards, MapForge
// sector editor, Tileset editor, roster grid). React.lazy code-splits
// each into its own chunk so the initial bundle parsed at startup
// shrinks. The user pays the chunk-load cost only on first navigation
// to that route. Tauri's local file: scheme makes the chunk fetch
// effectively instant (no network roundtrip).
const MercWizardRoster = lazy(() => import("./routes/MercWizardRoster"));
const Backups = lazy(() => import("./routes/Backups"));
const Create = lazy(() => import("./routes/Create"));
const Edit = lazy(() => import("./routes/Edit"));
const Backgrounds = lazy(() => import("./routes/Backgrounds"));
const Move = lazy(() => import("./routes/Move"));
const Delete = lazy(() => import("./routes/Delete"));
const Duplicate = lazy(() => import("./routes/Duplicate"));
const Import = lazy(() => import("./routes/Import"));
const Export = lazy(() => import("./routes/Export"));
const MapForge = lazy(() => import("./routes/MapForge"));
const MapForgeSector = lazy(() => import("./routes/MapForgeSector"));
const TilesetEditor = lazy(() => import("./routes/TilesetEditor"));
const TilesetEditorTileset = lazy(() => import("./routes/TilesetEditorTileset"));
const Tools = lazy(() => import("./routes/Tools"));
const IniEditor = lazy(() => import("./routes/IniEditor"));
const Setup = lazy(() => import("./routes/Setup"));
const ToolsStiViewer = lazy(() => import("./routes/ToolsStiViewer"));
const ToolsSlfExtractor = lazy(() => import("./routes/ToolsSlfExtractor"));

// Suspense fallback while a lazy route's chunk is downloading. Matches
// the "Starting up..." style so route transitions feel consistent with
// app startup. Should flash for <100 ms on Tauri's local fetch path.
function RouteFallback() {
  return (
    <div className="flex h-full items-center justify-center">
      <div className="text-wasteland-300 text-sm">Loading...</div>
    </div>
  );
}

export default function App() {
  const queryClient = useQueryClient();

  // When the shell respawns the sidecar (watchdog or panic recovery), it
  // emits `sidecar:restarted`. The new sidecar has a new port, so both the
  // tauri.ts port cache and the api.ts baseUrl cache must be cleared and
  // every in-flight React Query refetched.
  useEffect(() => {
    if (!isRunningInTauri()) return;
    let cancelled = false;
    let cleanup: (() => void) | undefined;
    import("@tauri-apps/api/event").then(({ listen }) => {
      if (cancelled) return;
      listen("sidecar:restarted", () => {
        clearCachedPort();
        clearApiBaseCache();
        queryClient.invalidateQueries();
      }).then((unlisten) => {
        if (cancelled) {
          unlisten();
        } else {
          cleanup = unlisten;
        }
      });
    });
    return () => {
      cancelled = true;
      if (cleanup) cleanup();
    };
  }, [queryClient]);

  const health = useQuery({
    queryKey: ["health"],
    queryFn: getHealth,
    refetchInterval: 5_000,
  });

  // Show first-run flow whenever no install is active. We deliberately
  // include the scan-in-progress window: FirstRun has its own "Scanning
  // Steam, GOG, and common paths..." indicator, and routing there
  // immediately is better than rendering an empty Hub for ~30s while
  // auto-detect runs (which made the app feel broken — users would click
  // around and miss the prompt entirely when it finally fired).
  const needsFirstRun =
    health.isSuccess
    && !health.data?.active_install_id;

  if (health.isError) {
    return (
      <div className="flex h-full items-center justify-center">
        <div className="card max-w-md">
          <h1 className="text-xl font-bold text-rust-400 mb-2">Sidecar not responding</h1>
          <p className="text-wasteland-200 text-sm">
            The background process that does the heavy lifting isn't reachable. The wizard
            will retry automatically. If the problem persists, restart the app.
          </p>
        </div>
      </div>
    );
  }

  if (health.isLoading) {
    return (
      <div className="flex h-full items-center justify-center">
        <div className="text-wasteland-300 text-sm">Starting up...</div>
      </div>
    );
  }

  return (
    <ErrorBoundary>
      <Suspense fallback={<RouteFallback />}>
        <Routes>
          <Route
            path="/"
            element={needsFirstRun ? <Navigate to="/first-run" replace /> : <Hub />}
          />
          <Route path="/first-run" element={<FirstRun />} />
          <Route path="/hub" element={<Hub />} />
          {/* Legacy /roster (V1 raw table) deleted 2026-05-25;
              redirect deep links to the new Merc Wizard roster. */}
          <Route path="/roster" element={<Navigate to="/merc-wizard" replace />} />
          <Route path="/merc-wizard" element={<MercWizardRoster />} />
          <Route path="/settings" element={<Settings />} />
          <Route path="/backups" element={<Backups />} />
          <Route path="/create" element={<Create />} />
          <Route path="/edit" element={<Edit />} />
          <Route path="/backgrounds" element={<Backgrounds />} />
          <Route path="/move" element={<Move />} />
          <Route path="/duplicate" element={<Duplicate />} />
          <Route path="/delete" element={<Delete />} />
          <Route path="/import" element={<Import />} />
          <Route path="/export" element={<Export />} />
          <Route path="/mapforge" element={<MapForge />} />
          <Route path="/mapforge/sector" element={<MapForgeSector />} />
          <Route path="/tileset-editor" element={<TilesetEditor />} />
          <Route path="/tileset-editor/:tileset" element={<TilesetEditorTileset />} />
          <Route path="/ini-editor" element={<IniEditor />} />
          <Route path="/setup" element={<Setup />} />
          <Route path="/tools" element={<Tools />} />
          <Route path="/tools/sti-viewer" element={<ToolsStiViewer />} />
          <Route path="/tools/slf-extractor" element={<ToolsSlfExtractor />} />
          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
      </Suspense>
    </ErrorBoundary>
  );
}
