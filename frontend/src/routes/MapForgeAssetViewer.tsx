/**
 * The MapForge "Browse assets" tile browser. Wraps the full
 * `MapForgePalette` (categorized, searchable, sub-frame picker) and can
 * render in two shapes:
 *
 *  1. **Modal pop-out** (legacy fixed-grid layout, dock mode OFF) —
 *     `MapForgeAssetViewer`. A centered overlay whose own scroll context
 *     captures wheel events so browsing the asset list can't scroll the
 *     page, and the canvas keeps full width when you're not browsing.
 *     Picking a tile auto-closes so the "pick → paint" loop is fast.
 *     Esc / backdrop click cancel.
 *
 *  2. **Embedded dock panel** (dock mode ON) —
 *     `MapForgeAssetBrowserBody`. The same palette body rendered inline
 *     inside a dockview panel (no overlay, no chrome, no auto-close):
 *     the browser is a first-class, dockable/resizable surface alongside
 *     Palette / Inspector / etc.
 *
 * The user opens the modal via the rail's "Browse assets" button or the
 * hotkey (default A); the dock panel is always present in its group.
 */
import { useEffect } from "react";

import type { IsoRenderer } from "../lib/IsoRenderer";
import { MapForgePalette, type ActiveBrush } from "./MapForgePalette";

/** Shared props for the palette body, independent of how it's framed
 * (modal vs. embedded panel). */
export interface AssetBrowserBodyProps {
  xmlPath: string;
  tileset: number;
  renderer: IsoRenderer | null;
  activeBrush: ActiveBrush | null;
  /** Called when the user picks (or clears) a brush. The embedded panel
   * forwards this straight through (it stays open — it's a persistent
   * surface); the modal additionally auto-closes after forwarding. */
  onPick: (b: ActiveBrush | null) => void;
  showShadowSlots: boolean;
  engineMaxTileSlot: number;
}

/** The asset-browser CONTENT, with no modal overlay / chrome. Rendered
 * directly inside a dockview panel in dock mode. Just the searchable,
 * categorized palette — it fills its container. */
export function MapForgeAssetBrowserBody({
  xmlPath, tileset, renderer, activeBrush, onPick,
  showShadowSlots, engineMaxTileSlot,
}: AssetBrowserBodyProps) {
  return (
    <div className="h-full min-h-0 w-full overflow-hidden">
      <MapForgePalette
        xmlPath={xmlPath}
        tileset={tileset}
        renderer={renderer}
        activeBrush={activeBrush}
        onPick={onPick}
        showShadowSlots={showShadowSlots}
        engineMaxTileSlot={engineMaxTileSlot}
      />
    </div>
  );
}

export interface MapForgeAssetViewerProps extends AssetBrowserBodyProps {
  open: boolean;
  onClose: () => void;
}

export function MapForgeAssetViewer({
  open, onClose, xmlPath, tileset, renderer, activeBrush,
  onPick, showShadowSlots, engineMaxTileSlot,
}: MapForgeAssetViewerProps) {
  // Esc closes. Listener attached only while the modal is open so it
  // doesn't interfere with the sector's own Esc handlers (which
  // unpin tiles etc.).
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        e.stopPropagation();
        onClose();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, onClose]);

  if (!open) return null;

  return (
    <div
      className="fixed inset-0 z-40 flex items-center justify-center bg-black/60 backdrop-blur-sm"
      onClick={onClose}
      role="dialog"
      aria-modal="true"
    >
      <div
        className="flex h-[80vh] w-[60rem] max-w-[92vw] flex-col overflow-hidden rounded-lg border border-gray-700 bg-gray-950 shadow-2xl"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center justify-between border-b border-gray-800 px-3 py-2">
          <h3 className="text-sm font-semibold text-gray-200">Asset Browser</h3>
          <button
            type="button"
            onClick={onClose}
            title="Close (Esc)"
            className="rounded border border-gray-700 bg-gray-900 px-2 py-0.5 text-xs text-gray-300 hover:border-gray-500 hover:text-gray-100"
          >
            ✕
          </button>
        </div>
        {/* Embedded MapForgePalette. The wheel events on its overflow
            container stay inside this fixed-positioned modal — they
            can't bubble to the page because the modal is its own
            scroll context. That's the whole point of this redesign. */}
        <div className="min-h-0 flex-1 overflow-hidden">
          <MapForgePalette
            xmlPath={xmlPath}
            tileset={tileset}
            renderer={renderer}
            activeBrush={activeBrush}
            onPick={(b) => {
              onPick(b);
              // Pick = done browsing. Close so the user can paint
              // immediately. Clear-brush picks (b === null) also
              // close — there's nothing more to do here after
              // dropping the active brush.
              onClose();
            }}
            showShadowSlots={showShadowSlots}
            engineMaxTileSlot={engineMaxTileSlot}
          />
        </div>
      </div>
    </div>
  );
}
