/**
 * dockview-backed rearrangeable layout for the MapForge editor — the
 * ONLY editor layout (the legacy fixed grid was removed in the IA
 * cleanup, 2026-06-10).
 *
 * Thin, generic shell: every panel renders through the same `DockSlot`,
 * which looks up its content by panel id in the render-function map
 * published via MapForgeDockContext. All editor state/logic stays in
 * MapForgeSector — the dock only decides geometry (dock zones, splits,
 * tabs, floating groups) and lets the user drag it around.
 *
 * Layout meta-actions (Reset layout / Set as default / re-open closed
 * panels) live in the command bar's Panels▾/Layout▾ menus in
 * MapForgeSector; this component exports the helpers those menus need
 * (`resetDockLayout`, `saveUserDefaultLayout`, `reopenDockPanel`,
 * `PANEL_TITLE`, `PANEL_ORDER`) and reports the set of open panel ids
 * via `onOpenPanelsChange`.
 *
 * Layout persistence: toJSON/fromJSON to localStorage, versioned by
 * LAYOUT_VERSION. The default arrangement is the procedural
 * `buildDefaultLayout` below — the single source of truth (a baked
 * captured-JSON default existed once, silently rotted a version behind,
 * and was deleted).
 */
import { type CSSProperties, useCallback, useEffect, useRef } from "react";
import {
  DockviewReact,
  type DockviewApi,
  type DockviewReadyEvent,
  type IDockviewPanelProps,
} from "dockview";
import "dockview/dist/styles/dockview.css";

import { useMapForgeDock, type DockPanelId } from "./mapForgeDockContext";

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

export const PANEL_TITLE: Record<DockPanelId, string> = {
  canvas: "Canvas",
  assets: "Brush Box",
  tilesetViewer: "Tileset Viewer",
  inspector: "Inspector",
  history: "History",
  log: "Log",
  validate: "Validation",
  generate: "Generate",
};
export const PANEL_ORDER: DockPanelId[] = [
  "canvas", "assets", "tilesetViewer", "inspector",
  "history", "log", "validate", "generate",
];

/** Re-add a closed panel. Used by the command bar's Panels▾ menu. */
export function reopenDockPanel(api: DockviewApi, id: DockPanelId): void {
  if (api.getPanel(id)) {
    api.getPanel(id)?.api.setActive();
    return;
  }
  api.addPanel({ id, component: "default", title: PANEL_TITLE[id] });
}

/** Lay out (or re-lay-out) the default arrangement: Brush Box column
 * (the consolidated picker, with the read-only Tileset Viewer as an
 * inactive tab) | big canvas | inspector group (with Validation +
 * Generate pre-created as inactive tabs), log along the bottom. Clears
 * any existing panels first so it doubles as "Reset layout". A user-saved
 * default ("Set as default") wins over this procedural layout. */
function buildDefaultLayout(api: DockviewApi) {
  // A user-saved default ("Set as default") wins over the built-in layout —
  // for Reset and for fresh sessions. Falls through if absent/corrupt.
  const userDefault = loadUserDefaultLayout();
  if (userDefault) {
    try {
      api.fromJSON(userDefault);
      if (api.panels.length > 0) return;
    } catch { /* corrupt → rebuild the procedural layout below */ }
  }
  for (const p of [...api.panels]) api.removePanel(p);
  api.addPanel({ id: "canvas", component: "default", title: PANEL_TITLE.canvas });
  // Left column: the Brush Box, with the read-only Tileset Viewer as an
  // inactive tab beside it.
  const brushbox = api.addPanel({
    id: "assets", component: "default", title: PANEL_TITLE.assets,
    position: { referencePanel: "canvas", direction: "left" },
  });
  api.addPanel({
    id: "tilesetViewer", component: "default", title: PANEL_TITLE.tilesetViewer,
    position: { referencePanel: "assets", direction: "within" }, inactive: true,
  });
  // Right column: Inspector with Validation + Generate pre-created as
  // INACTIVE tabs in the same group — the command bar's Generate /
  // Validate buttons just setActive() them (no add-with-position +
  // setSize dance on first open).
  const inspector = api.addPanel({
    id: "inspector", component: "default", title: PANEL_TITLE.inspector,
    position: { referencePanel: "canvas", direction: "right" },
  });
  api.addPanel({
    id: "validate", component: "default", title: PANEL_TITLE.validate,
    position: { referencePanel: "inspector", direction: "within" }, inactive: true,
  });
  api.addPanel({
    id: "generate", component: "default", title: PANEL_TITLE.generate,
    position: { referencePanel: "inspector", direction: "within" }, inactive: true,
  });
  api.addPanel({
    id: "history", component: "default", title: PANEL_TITLE.history,
    position: { referencePanel: "inspector", direction: "within" }, inactive: true,
  });
  const log = api.addPanel({
    id: "log", component: "default", title: PANEL_TITLE.log,
    position: { referencePanel: "canvas", direction: "below" },
  });
  // Proportions. Best-effort — wrapped so a future dockview that renames
  // setSize can't break the whole layout build.
  try { brushbox.group.api.setSize({ width: 280 }); } catch { /* best effort */ }
  try { inspector.group.api.setSize({ width: 360 }); } catch { /* best effort */ }
  try { log.group.api.setSize({ height: 140 }); } catch { /* best effort */ }
}

// ─── Layout persistence ───────────────────────────────────────────────
// Save the user's arrangement to localStorage so it survives reloads.
// Versioned: bump LAYOUT_VERSION whenever the panel id set changes so a
// stale saved layout (referencing removed ids) is discarded rather than
// restoring blank/placeholder panels.
const LAYOUT_STORAGE_KEY = "mapforge.dockLayout";
// Bump when the panel id set changes. v7: the tool / layers / view
// panels were removed (their content moved to the fixed command +
// tool-options bars above the dock), and Validation/Generate became
// default inactive tabs of the inspector group. v8: the "palette" rail
// and "variants" sub-frame panels were folded into the "assets" panel,
// now the consolidated "Brush Box" (R3). v9: added the "history" panel
// (R4 undo/redo stroke list with click-to-revert).
const LAYOUT_VERSION = 9;

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

/** Discard the saved arrangement and rebuild the default layout.
 * Used by the command bar's Layout▾ → "Reset layout". */
export function resetDockLayout(api: DockviewApi): void {
  clearSavedLayout();
  buildDefaultLayout(api);
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

/** Save the current arrangement as the user's default. Used by the
 * command bar's Layout▾ → "Set as default". */
export function saveUserDefaultLayout(api: DockviewApi): void {
  try {
    localStorage.setItem(
      DEFAULT_LAYOUT_KEY,
      JSON.stringify({ v: LAYOUT_VERSION, layout: api.toJSON() }),
    );
  } catch { /* quota / disabled — silently no-op */ }
}

export interface MapForgeDockProps {
  /** Receives the live DockviewApi once the dock is ready (and null on
   * unmount). Lets the parent drive the dock imperatively — open/focus
   * panels, reset layout, save the default arrangement. */
  onApi?: (api: DockviewApi | null) => void;
  /** Reports the set of currently-open panel ids whenever it changes,
   * so the command bar's Panels▾ menu knows which panels are closed
   * (closing a dockview tab otherwise strands it). */
  onOpenPanelsChange?: (openIds: string[]) => void;
}

export function MapForgeDock({ onApi, onOpenPanelsChange }: MapForgeDockProps = {}) {
  // Keep the latest callbacks in refs so onReady (a stable useCallback)
  // can call them without re-creating the dockview on every parent render.
  const onApiRef = useRef(onApi);
  onApiRef.current = onApi;
  const onOpenPanelsChangeRef = useRef(onOpenPanelsChange);
  onOpenPanelsChangeRef.current = onOpenPanelsChange;

  const onReady = useCallback((event: DockviewReadyEvent) => {
    const api = event.api;
    onApiRef.current?.(api);
    // Restore the saved arrangement; fall back to the default layout.
    if (!tryRestoreLayout(api)) buildDefaultLayout(api);
    const refresh = () => {
      onOpenPanelsChangeRef.current?.(api.panels.map((p) => p.id));
    };
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

  return (
    <div
      className="dockview-theme-abyss h-full w-full"
      style={{
        // Shrink the grey tab/header bars (default 35px) — they were
        // eating a lot of vertical space across many panels.
        "--dv-tabs-and-actions-container-height": "24px",
        "--dv-tabs-and-actions-container-font-size": "11px",
      } as CSSProperties}
    >
      <DockviewReact components={DOCK_COMPONENTS} onReady={onReady} />
    </div>
  );
}
