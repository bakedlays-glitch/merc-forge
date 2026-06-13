/**
 * Context bridging the MapForge editor's live state into dockview panels.
 *
 * dockview instantiates panel components itself (from a `components`
 * map), so they can't close over MapForgeSector's scope directly. Rather
 * than thread ~40 bindings through a typed interface (or duplicate the
 * ~200-line canvas viewport), MapForgeSector publishes a map of *render
 * functions* — each a closure over its own state — and the generic dock
 * panel renders the one matching its panel id.
 *
 * This keeps the dock layout a thin, generic shell: all editor logic and
 * state stays in MapForgeSector; the dock just decides WHERE each
 * panel's content is drawn. The same render functions feed the legacy
 * fixed-grid layout, so docked and undocked render identical content
 * with no duplication.
 */
import { createContext, useContext, type ReactNode } from "react";

/** Stable panel ids. Used as dockview panel ids AND keys into the
 * render-function map. Keep in sync with the panels MapForgeSector
 * publishes + the default layout in MapForgeDock. */
export type DockPanelId =
  | "canvas"
  // "assets" is the consolidated Brush Box (Favorites + Recent + the
  // searchable categorized grid). The old separate "palette" rail and
  // "variants" sub-frame panels were folded in (R3 Brush Box, 2026-06-13).
  | "assets"
  | "tilesetViewer"
  | "inspector"
  | "history"
  // "minimap" is the in-editor overview navigator: a downscaled flat
  // top-down map of the whole sector + a draggable viewport rectangle
  // that re-centers the main canvas (R6, 2026-06-13). NOT the in-game
  // radar STI (that's radar.py / generateRadar).
  | "minimap"
  | "log"
  | "validate"
  | "generate";

export interface MapForgeDockValue {
  /** Panel id → render function (a closure over MapForgeSector state).
   * A missing id renders an empty-state placeholder, so the dock layout
   * degrades gracefully if a panel hasn't been wired yet. */
  panels: Partial<Record<DockPanelId, () => ReactNode>>;
}

export const MapForgeDockContext = createContext<MapForgeDockValue>({ panels: {} });

export function useMapForgeDock(): MapForgeDockValue {
  return useContext(MapForgeDockContext);
}
