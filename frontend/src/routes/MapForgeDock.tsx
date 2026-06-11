/**
 * dockview-backed rearrangeable layout for the MapForge editor (opt-in,
 * behind the "Dock layout (beta)" toggle in MapForgeSector).
 *
 * Thin, generic shell: every panel renders through the same `DockSlot`,
 * which looks up its content by panel id in the render-function map
 * published via MapForgeDockContext. All editor state/logic stays in
 * MapForgeSector — the dock only decides geometry (dock zones, splits,
 * tabs, floating groups) and lets the user drag it around.
 *
 * A small control strip below the dock offers "Reset layout" and
 * re-opens any panel the user has closed (closing a dockview tab
 * otherwise strands it). The default layout mirrors the legacy fixed
 * grid (narrow palette | big canvas | inspector; log along the bottom).
 *
 * Layout persistence (toJSON/fromJSON to localStorage) + full-screen are
 * Phase 3.
 */
import { type CSSProperties, useCallback, useEffect, useRef, useState } from "react";
import {
  DockviewReact,
  type DockviewApi,
  type DockviewReadyEvent,
  type IDockviewPanelProps,
} from "dockview";
import "dockview/dist/styles/dockview.css";

import { useMapForgeDock, type DockPanelId } from "./mapForgeDockContext";
import { BUILT_IN_DEFAULT_LAYOUT_JSON } from "./mapForgeDefaultLayout";

/** The single dockview panel component. Renders the editor content for
 * whatever panel id dockview assigned it, pulled from the context map. */
function DockSlot(props: IDockviewPanelProps) {
  const { panels } = useMapForgeDock();
  const render = panels[props.api.id as DockPanelId];
  return (
    <div className="h-full w-full overflow-auto bg-gray-950">
      {render
        ? render()
        : (
          <div className="p-3 text-xs italic text-gray-500">
            No content wired for panel “{props.api.id}”.
          </div>
        )}
    </div>
  );
}

const DOCK_COMPONENTS = { default: DockSlot };

const PANEL_TITLE: Record<DockPanelId, string> = {
  canvas: "Canvas",
  palette: "Palette",
  assets: "Browse Assets",
  tilesetViewer: "Tileset Viewer",
  inspector: "Inspector",
  tool: "Tool",
  variants: "Variants",
  layers: "Layers",
  view: "View",
  log: "Log",
  validate: "Validation",
  generate: "Generate",
};
const PANEL_ORDER: DockPanelId[] = [
  "canvas", "palette", "assets", "tilesetViewer", "inspector", "tool", "variants", "layers", "view", "log", "validate", "generate",
];

/** Lay out (or re-lay-out) the default arrangement, mirroring the legacy
 * fixed grid: narrow palette | big canvas | inspector, with the log as a
 * short strip along the bottom. Clears any existing panels first so it
 * doubles as "Reset layout". */
function buildDefaultLayout(api: DockviewApi) {
  // A user-saved default ("Set as default") wins over the built-in layout —
  // for Reset and for fresh sessions. Falls through if absent/corrupt.
  const userDefault = loadUserDefaultLayout();
  if (userDefault) {
    try {
      api.fromJSON(userDefault);
      if (api.panels.length > 0) return;
    } catch { /* corrupt → rebuild the built-in layout below */ }
  }
  // Built-in default = the maintainer's captured arrangement. Applied only
  // when its `v` matches LAYOUT_VERSION, so a stale baked layout after a
  // panel-set change falls through to the procedural layout below instead of
  // restoring one that's missing the new panel.
  try {
    const baked = JSON.parse(BUILT_IN_DEFAULT_LAYOUT_JSON);
    if (baked.v === LAYOUT_VERSION) {
      api.fromJSON(baked.layout);
      if (api.panels.length > 0) return;
    }
  } catch { /* fall through to the procedural layout */ }
  for (const p of [...api.panels]) api.removePanel(p);
  api.addPanel({ id: "canvas", component: "default", title: PANEL_TITLE.canvas });
  // Left column: Palette + Browse Assets as tabs on top, Variants beneath.
  // The full asset browser lives as a tab beside the compact Palette rail
  // — same left column, one click to switch from quick-pick to the full
  // categorized/searchable grid. It's a first-class dock panel now, not a
  // modal pop-out.
  const palette = api.addPanel({
    id: "palette", component: "default", title: PANEL_TITLE.palette,
    position: { referencePanel: "canvas", direction: "left" },
  });
  api.addPanel({
    id: "assets", component: "default", title: PANEL_TITLE.assets,
    position: { referencePanel: "palette", direction: "within" }, inactive: true,
  });
  api.addPanel({
    id: "tilesetViewer", component: "default", title: PANEL_TITLE.tilesetViewer,
    position: { referencePanel: "palette", direction: "within" }, inactive: true,
  });
  const variants = api.addPanel({
    id: "variants", component: "default", title: PANEL_TITLE.variants,
    position: { referencePanel: "palette", direction: "below" },
  });
  const inspector = api.addPanel({
    id: "inspector", component: "default", title: PANEL_TITLE.inspector,
    position: { referencePanel: "canvas", direction: "right" },
  });
  // Top strip above the canvas: [Layers | View tabs]  +  [Tool].
  const tool = api.addPanel({
    id: "tool", component: "default", title: PANEL_TITLE.tool,
    position: { referencePanel: "canvas", direction: "above" },
  });
  const layers = api.addPanel({
    id: "layers", component: "default", title: PANEL_TITLE.layers,
    position: { referencePanel: "tool", direction: "left" },
  });
  api.addPanel({
    id: "view", component: "default", title: PANEL_TITLE.view,
    position: { referencePanel: "layers", direction: "within" }, inactive: true,
  });
  const log = api.addPanel({
    id: "log", component: "default", title: PANEL_TITLE.log,
    position: { referencePanel: "canvas", direction: "below" },
  });
  // Proportions. Best-effort — wrapped so a future dockview that renames
  // setSize can't break the whole layout build.
  try { palette.group.api.setSize({ width: 220 }); } catch { /* best effort */ }
  try { variants.group.api.setSize({ height: 260 }); } catch { /* best effort */ }
  try { inspector.group.api.setSize({ width: 360 }); } catch { /* best effort */ }
  try { tool.group.api.setSize({ height: 110 }); } catch { /* best effort */ }
  try { log.group.api.setSize({ height: 140 }); } catch { /* best effort */ }
}

// ─── Layout persistence ───────────────────────────────────────────────
// Save the user's arrangement to localStorage so it survives reloads.
// Versioned: bump LAYOUT_VERSION whenever the panel id set changes so a
// stale saved layout (referencing removed ids) is discarded rather than
// restoring blank/placeholder panels.
const LAYOUT_STORAGE_KEY = "mapforge.dockLayout";
// Bump when the panel id set changes. v2 adds the "assets" (Browse
// Assets) panel — a v1 saved layout has no such panel, so discard it and
// rebuild the default rather than restoring a layout missing the browser.
const LAYOUT_VERSION = 6;  // v6: "Validation" becomes a dockable panel (was floating-only)

function saveLayout(api: DockviewApi): void {
  try {
    localStorage.setItem(
      LAYOUT_STORAGE_KEY,
      JSON.stringify({ v: LAYOUT_VERSION, layout: api.toJSON() }),
    );
  } catch {
    /* quota / disabled — layout just won't persist */
  }
}

function tryRestoreLayout(api: DockviewApi): boolean {
  try {
    const raw = localStorage.getItem(LAYOUT_STORAGE_KEY);
    if (!raw) return false;
    const saved = JSON.parse(raw);
    if (!saved || saved.v !== LAYOUT_VERSION || !saved.layout) return false;
    api.fromJSON(saved.layout);
    if (api.panels.length === 0) return false; // corrupt → fall back to default
    return true;
  } catch {
    return false;
  }
}

function clearSavedLayout(): void {
  try { localStorage.removeItem(LAYOUT_STORAGE_KEY); } catch { /* ignore */ }
}

// ─── User-chosen default layout ───────────────────────────────────────
// "Set as default" stores the current arrangement here; buildDefaultLayout
// prefers it over the built-in procedural layout for Reset + fresh sessions.
const DEFAULT_LAYOUT_KEY = "mapforge.dockLayoutDefault";

function loadUserDefaultLayout(): ReturnType<DockviewApi["toJSON"]> | null {
  try {
    const raw = localStorage.getItem(DEFAULT_LAYOUT_KEY);
    if (!raw) return null;
    const saved = JSON.parse(raw);
    if (!saved || saved.v !== LAYOUT_VERSION || !saved.layout) return null;
    return saved.layout;
  } catch {
    return null;
  }
}

function saveUserDefaultLayout(api: DockviewApi): void {
  try {
    localStorage.setItem(
      DEFAULT_LAYOUT_KEY,
      JSON.stringify({ v: LAYOUT_VERSION, layout: api.toJSON() }),
    );
  } catch { /* quota / disabled — silently no-op */ }
}

export interface MapForgeDockProps {
  /** Receives the live DockviewApi once the dock is ready (and null on
   * unmount). Lets the parent drive the dock imperatively — e.g. the
   * compact rail's "Browse assets" button activating the docked Browse
   * Assets panel instead of opening the legacy modal. */
  onApi?: (api: DockviewApi | null) => void;
}

export function MapForgeDock({ onApi }: MapForgeDockProps = {}) {
  const apiRef = useRef<DockviewApi | null>(null);
  const [openIds, setOpenIds] = useState<Set<string>>(new Set());
  const [justSaved, setJustSaved] = useState(false);
  // Keep the latest onApi in a ref so onReady (a stable useCallback) can
  // call it without re-creating the dockview on every parent render.
  const onApiRef = useRef(onApi);
  onApiRef.current = onApi;

  const onReady = useCallback((event: DockviewReadyEvent) => {
    const api = event.api;
    apiRef.current = api;
    onApiRef.current?.(api);
    // Restore the saved arrangement; fall back to the default layout.
    if (!tryRestoreLayout(api)) buildDefaultLayout(api);
    const refresh = () => setOpenIds(new Set(api.panels.map((p) => p.id)));
    refresh();
    api.onDidAddPanel(refresh);
    api.onDidRemovePanel(refresh);
    // Persist on any layout change (move / resize / add / remove / tab),
    // debounced so a drag-resize doesn't hammer localStorage.
    let saveTimer: ReturnType<typeof setTimeout> | undefined;
    api.onDidLayoutChange(() => {
      if (saveTimer !== undefined) clearTimeout(saveTimer);
      saveTimer = setTimeout(() => saveLayout(api), 400);
    });
  }, []);

  // Hand the parent a null api on unmount so it doesn't hold a stale ref.
  useEffect(() => () => { onApiRef.current?.(null); }, []);

  const missing = PANEL_ORDER.filter((id) => !openIds.has(id));

  return (
    <div className="flex h-full w-full flex-col">
      <div
        className="dockview-theme-abyss min-h-0 flex-1"
        style={{
          // Shrink the grey tab/header bars (default 35px) — they were
          // eating a lot of vertical space across many panels.
          "--dv-tabs-and-actions-container-height": "24px",
          "--dv-tabs-and-actions-container-font-size": "11px",
        } as CSSProperties}
      >
        <DockviewReact components={DOCK_COMPONENTS} onReady={onReady} />
      </div>
      <div className="mt-1 flex flex-wrap items-center gap-1 text-xs">
        <button
          type="button"
          onClick={() => {
            if (!apiRef.current) return;
            clearSavedLayout();
            buildDefaultLayout(apiRef.current);
          }}
          title="Discard your saved arrangement and restore the default layout"
          className="rounded border border-gray-700 px-2 py-0.5 text-gray-300 hover:bg-gray-800 hover:text-gray-100"
        >
          Reset layout
        </button>
        <button
          type="button"
          onClick={() => {
            if (!apiRef.current) return;
            saveUserDefaultLayout(apiRef.current);
            setJustSaved(true);
            setTimeout(() => setJustSaved(false), 1500);
          }}
          title="Save the current arrangement as your default — Reset layout and fresh sessions open this."
          className="rounded border border-gray-700 px-2 py-0.5 text-gray-300 hover:bg-gray-800 hover:text-gray-100"
        >
          {justSaved ? "Saved ✓" : "Set as default"}
        </button>
        {missing.length > 0 && (
          <span className="ml-1 text-gray-500">Closed —</span>
        )}
        {missing.map((id) => (
          <button
            key={id}
            type="button"
            onClick={() => apiRef.current?.addPanel({
              id, component: "default", title: PANEL_TITLE[id],
            })}
            title={`Re-open the ${PANEL_TITLE[id]} panel`}
            className="rounded border border-emerald-800 bg-emerald-950/40 px-2 py-0.5 text-emerald-200 hover:bg-emerald-900/50"
          >
            + {PANEL_TITLE[id]}
          </button>
        ))}
      </div>
    </div>
  );
}
