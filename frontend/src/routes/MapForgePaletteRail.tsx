/**
 * Compact left rail that replaces the old 18rem palette sidebar with
 * something narrower (~10rem) so wheel-scrolling inside it can never
 * bleed into the canvas viewport. The full asset browser now lives in
 * a popup modal — see `MapForgeAssetViewer` — opened via the Browse
 * button here or the configured hotkey ("A" by default).
 *
 * Contents of the rail:
 *   1. Recent-brush thumbnails (last N picks, dedup by slot+sub) so
 *      switching between two tiles you're using a lot doesn't require
 *      a round-trip to the modal.
 *   2. A "Browse all assets" button that opens the popup.
 *
 * The toolbar's BrushChip already surfaces the currently-active brush
 * + a clear button, so we deliberately omit a redundant "active brush"
 * panel here. Keeps the rail focused on "what to switch to next."
 */
import { useEffect, useRef, useState } from "react";

import type { IsoRenderer } from "../lib/IsoRenderer";
import { getLibraryStiThumbBlobUrl, type RecentAddition } from "../lib/mapforge";
import type { ActiveBrush } from "./MapForgePalette";

export interface MapForgePaletteRailProps {
  /** Renderer for atlas-backed thumbnails. May be null while the
   * atlas is still loading on first session open — the rail renders
   * placeholder boxes in that case. */
  renderer: IsoRenderer | null;
  activeBrush: ActiveBrush | null;
  /** Ordered list of recent picks. Index 0 = most recent. Capped by
   * the parent (typically at 8). The parent owns dedup + ordering. */
  recentBrushes: ActiveBrush[];
  /** Ordered list of STIs imported from the library this
   * (install, tileset) cycle. Index 0 = most recent. Persisted in
   * localStorage by the parent — survives page reloads. */
  recentAdditions: RecentAddition[];
  /** Re-select a recent brush. Fires the same onPick path as the
   * full palette so the parent's setActiveBrush + log emission run
   * exactly once per pick regardless of which UI surfaced the click. */
  onPick: (b: ActiveBrush) => void;
  /** "Pick as brush" on a Just-Added card. Distinct from `onPick`
   * because the addition doesn't carry full ActiveBrush metadata
   * (layer/category/sub default at the parent). */
  onPickAddition: (addition: RecentAddition) => void;
  /** "Open in Tileset Editor" chip on a Just-Added card. Navigates the
   * user to `/tileset-editor/:tileset?slot=N` so they can view/edit
   * this slot's JSD, inject subs, or add more STIs. Library and
   * inject UIs both live in the Tileset Editor route now — see
   * docs/TILESET_EDITOR_SPLIT.md. */
  onOpenInTilesetEditor: (addition: RecentAddition) => void;
  /** Opens the full asset viewer modal. The parent tracks the open
   * state so it can also be opened via hotkey or other means. */
  onOpenViewer: () => void;
  /** True when the Recent palette is currently popped out into a
   * floating window (rendered by the parent). The rail then shows a
   * compact stub in place of the grid. */
  recentPoppedOut?: boolean;
  /** Toggle the Recent pop-out. When omitted (other callers), no
   * pop-out button is shown and the grid always renders inline. */
  onTogglePopOutRecent?: () => void;
  /** Hide the "Browse assets" button. Dock mode has a dedicated Browse
   * Assets panel/tab, so the rail button is redundant there; the legacy
   * layout (default false) keeps it as the only way to open the browser. */
  hideBrowseButton?: boolean;
}

export function MapForgePaletteRail({
  renderer, activeBrush, recentBrushes, recentAdditions,
  onPick, onPickAddition, onOpenInTilesetEditor,
  onOpenViewer, recentPoppedOut = false, onTogglePopOutRecent,
  hideBrowseButton = false,
}: MapForgePaletteRailProps) {
  return (
    <div className="flex h-full flex-col rounded border border-gray-700 bg-gray-950 p-2 gap-2">
      {!hideBrowseButton && (
        <button
          type="button"
          onClick={onOpenViewer}
          title="Open the full asset browser — categories, search, sub-frame picker. Hotkey: A"
          className="rounded border border-blue-700 bg-blue-950/40 px-2 py-1.5 text-xs font-semibold text-blue-100 hover:border-blue-500 hover:bg-blue-900/50"
        >
          📚 Browse assets <span className="text-[9px] text-blue-300">(A)</span>
        </button>
      )}

      {/* Retracts entirely when popped out (item 3) — the palette lives
          in the floating window; dock back via that window's ✕. */}
      {!recentPoppedOut && (
        <div className="flex flex-col gap-0.5">
          <div className="flex items-center gap-1">
            <span className="text-[10px] uppercase tracking-wider text-gray-500">
              Recent
            </span>
            {recentBrushes.length > 0 && onTogglePopOutRecent && (
              <button
                type="button"
                onClick={onTogglePopOutRecent}
                title="Pop out into a resizable floating window with larger tiles"
                aria-label="Pop out Recent palette"
                className="ml-auto rounded border border-gray-700 px-1 py-0.5 text-[11px] leading-none text-gray-400 hover:bg-gray-800 hover:text-gray-100"
              >⤢</button>
            )}
          </div>
          {recentBrushes.length === 0 ? (
            <p className="rounded border border-dashed border-gray-800 bg-gray-900/40 p-2 text-[10px] italic text-gray-500">
              No recent brushes yet. Open <b>Browse Assets</b> (or press
              <code className="mx-0.5 rounded bg-gray-800 px-1">A</code>)
              to pick your first.
            </p>
          ) : (
            <RecentBrushGrid
              recentBrushes={recentBrushes}
              renderer={renderer}
              activeBrush={activeBrush}
              onPick={onPick}
              size={44}
              cols={2}
            />
          )}
        </div>
      )}

      {/* "Just added" panel — separate from "Recent" because the
          actions are different (Pick-as-brush for picks; View-subs /
          Inject / Undo for adds) and because the dedup keys are
          different (slot+sub for picks; sha+slot for adds). Hidden
          entirely when empty so the rail stays compact. */}
      {recentAdditions.length > 0 && (
        <div className="flex flex-col gap-0.5">
          <span className="text-[10px] uppercase tracking-wider text-emerald-400">
            Just added
          </span>
          <ul className="flex flex-col gap-1">
            {recentAdditions.map((a) => (
              <RecentAdditionCard
                key={`${a.sha256}-${a.slot}`}
                addition={a}
                isActive={activeBrush?.slot === a.slot}
                onPickAsBrush={() => onPickAddition(a)}
                onOpenInTilesetEditor={() => onOpenInTilesetEditor(a)}
              />
            ))}
          </ul>
        </div>
      )}

      {/* Flex spacer at the bottom keeps both sections stuck to the
          top even when their lists are short. */}
      <div className="flex-1" />
    </div>
  );
}

/** The recent-brush thumbnail grid, shared by the docked rail (44px, 2
 * columns) and the pop-out floating panel (larger tiles, more columns).
 * `cols` is applied via inline gridTemplateColumns so it can be any
 * number — Tailwind's grid-cols-N is build-time only. */
export function RecentBrushGrid({
  recentBrushes, renderer, activeBrush, onPick, size = 44, cols = 2,
}: {
  recentBrushes: ActiveBrush[];
  renderer: IsoRenderer | null;
  activeBrush: ActiveBrush | null;
  onPick: (b: ActiveBrush) => void;
  size?: number;
  /** Fixed column count, or <= 0 for responsive auto-fill keyed to
   * `size` (used by the resizable pop-out panel so tiles reflow). */
  cols?: number;
}) {
  const template = cols > 0
    ? `repeat(${cols}, minmax(0, 1fr))`
    : `repeat(auto-fill, minmax(${size + 12}px, 1fr))`;
  return (
    <ul
      className="grid gap-1"
      style={{ gridTemplateColumns: template }}
    >
      {recentBrushes.map((b) => (
        <RecentBrushTile
          key={`${b.slot}-${b.sub}`}
          brush={b}
          renderer={renderer}
          size={size}
          isActive={
            activeBrush?.slot === b.slot
            && activeBrush?.sub === b.sub
          }
          onClick={() => onPick(b)}
        />
      ))}
    </ul>
  );
}

/** Single recent-brush tile. Renders a thumbnail via the renderer's
 * atlas + a tiny slot/sub label. Highlight when this brush IS the
 * active one so the user can see "I'm holding this." `size` is the
 * thumbnail edge in px (docked rail passes 44; the pop-out panel passes
 * larger). */
function RecentBrushTile({
  brush, renderer, isActive, onClick, size = 44,
}: {
  brush: ActiveBrush;
  renderer: IsoRenderer | null;
  isActive: boolean;
  onClick: () => void;
  size?: number;
}) {
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const [missing, setMissing] = useState(false);
  useEffect(() => {
    if (!renderer || !canvasRef.current) return;
    const ctx = canvasRef.current.getContext("2d");
    if (!ctx) return;
    const ok = renderer.drawCellInto(ctx, brush.slot, brush.sub, size, size);
    setMissing(!ok);
  }, [renderer, brush.slot, brush.sub, size]);

  const stiLabel = brush.sti_filename.replace(/\.sti$/i, "");
  return (
    <li>
      <button
        type="button"
        onClick={onClick}
        title={
          `${brush.sti_filename}\n`
          + `slot ${brush.slot} sub ${brush.sub} → ${brush.layer}`
        }
        className={`flex w-full flex-col items-center rounded border p-1 text-[8px] hover:bg-gray-800 ${
          isActive
            ? "border-emerald-500 bg-emerald-950/50"
            : "border-gray-700 bg-gray-900"
        }`}
      >
        {missing ? (
          <span
            className="inline-flex items-center justify-center rounded bg-gray-800 text-[8px] text-gray-500"
            style={{ width: size, height: size }}
            title="not in atlas"
          >?</span>
        ) : (
          <canvas
            ref={canvasRef}
            width={size}
            height={size}
            style={{
              imageRendering: "pixelated",
              width: size,
              height: size,
            }}
          />
        )}
        <span className="mt-0.5 w-full truncate text-gray-400" style={{ maxWidth: "100%" }}>
          {stiLabel}
        </span>
        <span className="text-gray-600">s{brush.slot}/{brush.sub}</span>
      </button>
    </li>
  );
}

/** A single "Just added" card. Thumb from the library catalog
 * (NOT the renderer atlas — additions are fresh and may not be in
 * the atlas yet) + action row. Chips:
 *   - Pick as brush         : sets the active brush to (slot, sub 0)
 *   - Open in Tileset Editor: navigates to /tileset-editor/:tileset?slot=N
 *                             where the user can edit JSD / inject subs */
function RecentAdditionCard({
  addition, isActive, onPickAsBrush, onOpenInTilesetEditor,
}: {
  addition: RecentAddition;
  isActive: boolean;
  onPickAsBrush: () => void;
  onOpenInTilesetEditor: () => void;
}) {
  const [thumbUrl, setThumbUrl] = useState<string | null>(null);
  const [err, setErr] = useState(false);
  useEffect(() => {
    // Catalog thumb fetch — the library has a pre-rendered PNG keyed
    // by sha256. Lifecycle is one blob URL per card, revoked on
    // unmount.
    let cancelled = false;
    let created: string | null = null;
    setErr(false);
    setThumbUrl(null);
    getLibraryStiThumbBlobUrl(addition.sha256)
      .then((u) => {
        if (cancelled) { URL.revokeObjectURL(u); return; }
        created = u;
        setThumbUrl(u);
      })
      .catch(() => { if (!cancelled) setErr(true); });
    return () => {
      cancelled = true;
      if (created) URL.revokeObjectURL(created);
    };
  }, [addition.sha256]);

  const stiLabel = addition.sti_filename.replace(/\.sti$/i, "");
  const frames = addition.frame_count ?? 0;

  return (
    <li
      className={`rounded border bg-gray-900 ${
        isActive ? "border-emerald-500" : "border-gray-700"
      }`}
    >
      <div className="flex items-center gap-1.5 p-1">
        <button
          type="button"
          onClick={onPickAsBrush}
          title={
            `${addition.sti_filename}\n`
            + `slot ${addition.slot} · ${frames} frame${frames === 1 ? "" : "s"}`
            + `${addition.has_jsd ? " · multi-tile struct" : ""}\n`
            + "Click to set as active brush (sub 0)."
          }
          className="flex min-w-0 flex-1 items-center gap-1.5 text-left hover:bg-gray-800"
        >
          {err ? (
            <span
              className="inline-flex h-9 w-9 shrink-0 items-center justify-center rounded bg-red-950 text-[8px] text-red-400"
              title="thumbnail unavailable"
            >?</span>
          ) : thumbUrl ? (
            <img
              src={thumbUrl}
              alt={addition.sti_filename}
              className="block h-9 w-9 shrink-0 rounded bg-gray-950"
              style={{ imageRendering: "pixelated", objectFit: "contain" }}
            />
          ) : (
            <span
              className="inline-block h-9 w-9 shrink-0 animate-pulse rounded bg-gray-800"
            />
          )}
          <div className="min-w-0 flex-1">
            <div className="truncate text-[10px] text-gray-200">{stiLabel}</div>
            <div className="text-[9px] text-gray-500">
              slot {addition.slot} · {frames}f
              {addition.has_jsd && (
                <span className="ml-1 text-amber-300">+jsd</span>
              )}
            </div>
          </div>
        </button>
      </div>
      <div className="flex border-t border-gray-800 text-[9px] text-gray-400">
        <button
          type="button"
          onClick={onOpenInTilesetEditor}
          title="Open this slot in the Tileset Editor — view the JSD, inject more sub-frames, or add another STI."
          className="flex-1 px-1 py-0.5 hover:bg-gray-800"
        >
          Open in Tileset Editor →
        </button>
      </div>
    </li>
  );
}
