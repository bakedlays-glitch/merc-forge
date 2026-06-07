/**
 * Asset palette sidebar for MapForge — categorized list of every slot
 * in the active tileset with sprite-sheet thumbnails.
 *
 * Perf note: instead of 150+ separate /sti/frame requests (1 per
 * thumbnail), we fetch ONE sprite sheet PNG containing every slot's
 * frame[0] packed into a 64×64 grid, plus a JSON metadata response
 * with per-slot cell offsets. Each <SlotTile> just uses a
 * background-image + background-position CSS pair to crop "its" cell
 * out of the sheet. Page load goes from ~10-15s sequential fetches
 * to ~1-2s single fetch + cached on disk for subsequent loads.
 */
import { memo, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";

import {
  getPaletteSheetMeta,
  getTilesetPalette,
  prefetchPaletteSheet,
  type PaletteSheetBuildEvent,
  type PaletteSheetCell,
  type PaletteSlot,
} from "../lib/mapforge";
import type { IsoRenderer } from "../lib/IsoRenderer";
import { isShadowOnlySlot } from "../lib/jaSlotPairs";

const CATEGORY_LABELS: Record<string, string> = {
  floor: "Floors / Ground",
  wall: "Walls / Fences",
  door: "Doors",
  window: "Windows",
  roof: "Roofs",
  furniture: "Furniture",
  veg: "Vegetation",
  scatter: "Scatter / Decals",
  vehicle: "Vehicles",
  other: "Other / Uncategorized",
};

// Default-category → suggested layer mapping. The pencil tool uses this
// to decide WHICH LAYER an `add` edit targets when painting a brush of
// a given category. (e.g. floors go into "land", walls into "structs".)
export const CATEGORY_TO_LAYER: Record<string, string> = {
  floor: "land",
  wall: "structs",
  door: "structs",
  window: "structs",
  roof: "roofs",
  furniture: "structs",
  veg: "objs",
  scatter: "objs",
  vehicle: "structs",
  other: "structs",
};

export interface ActiveBrush {
  slot: number;
  sub: number;
  category: string;
  layer: string;
  sti_filename: string;
  /** When true, the painter places ONE tile at the click position
   * even if the slot has a multi-tile JSD footprint. Used by the
   * tile-inspector pick path so "click this thumbnail" copies that
   * exact (slot, sub) instead of expanding into the full struct
   * (which would offset the clicked piece from the click point).
   * Default false — palette picks still stamp multi-tile structs. */
  forceSingleTile?: boolean;
}

export function MapForgePalette({
  xmlPath, tileset, renderer, activeBrush, onPick,
  showShadowSlots, engineMaxTileSlot,
}: {
  xmlPath: string;
  tileset: number;
  /** IsoRenderer instance — needed by the subframe picker to render
   * per-sub thumbnails from the atlas (zero-HTTP). Null while the
   * renderer is still loading; the sub picker is disabled until then. */
  renderer: IsoRenderer | null;
  activeBrush: ActiveBrush | null;
  onPick: (b: ActiveBrush | null) => void;
  /** When false, hide shadow-only slots (FIRSTSHADOW, FENCESHADOW,
   * etc.) from the palette — they ride along with their paired struct
   * via auto-pair and aren't user-pickable. When true (because the
   * user disabled auto-pair, or just wants direct shadow control),
   * the shadow-only slots show up like any other slot. */
  showShadowSlots: boolean;
  /** Cap on which slots the palette will display. Slots above this
   * are filtered out — they can't be painted anyway because the
   * engine's compiled NUMBEROFTILETYPES is lower than the slot index
   * (writing them produces .dat entries the engine can't render and
   * the game crashes on sector load). Single source of truth is the
   * `engineMaxTileSlot` setting. */
  engineMaxTileSlot: number;
}) {
  const palette = useQuery({
    queryKey: ["mapforge", "palette", xmlPath, tileset],
    queryFn: () => getTilesetPalette(xmlPath, tileset),
    enabled: !!xmlPath && tileset >= 0,
    staleTime: 5 * 60 * 1000,
  });

  const sheetMeta = useQuery({
    queryKey: ["mapforge", "palette-sheet-meta", xmlPath, tileset],
    queryFn: () => getPaletteSheetMeta(xmlPath, tileset),
    enabled: !!xmlPath && tileset >= 0,
    staleTime: 5 * 60 * 1000,
  });

  const [sheetUrl, setSheetUrl] = useState<string | null>(null);
  // Live bake progress, populated from the NDJSON stream. Drives the
  // loading panel below. null when no bake is in flight (cache hit or
  // completed); set with phase + counter while the backend bakes the
  // sprite sheet. Without this, cold loads of a big tileset would sit
  // on "Loading…" text for up to a minute.
  const [bakeProgress, setBakeProgress] = useState<{
    phase: string;
    label: string;
    current?: number;
    total?: number;
    detail?: string;
  } | null>(null);
  useEffect(() => {
    if (!xmlPath || tileset < 0) {
      setSheetUrl(null);
      setBakeProgress(null);
      return;
    }
    let cancelled = false;
    setSheetUrl(null);
    // Single load path via prefetchPaletteSheet:
    //   (1) it streams the bake for live progress — but on a warm cache
    //       (the common case, because the session-open / tileset-change
    //       preload already ran) the stream emits an instant cache-hit
    //       event, so the spinner never really shows;
    //   (2) it then resolves the SHARED, app-lifetime blob URL from cache
    //       — zero extra network when warm, deduped with the preload.
    // The cache owns the blob URL's lifetime, so we never revoke it here.
    // Using one path (vs. a parallel cache-read + progress-stream) avoids
    // a cold-open double-bake of the same sheet.
    setBakeProgress({ phase: "starting", label: "Starting bake" });
    prefetchPaletteSheet(xmlPath, tileset, (evt: PaletteSheetBuildEvent) => {
      if (cancelled) return;
      if (evt.event === "phase") {
        setBakeProgress((p) => ({
          phase: evt.phase,
          label: evt.label,
          total: evt.total ?? p?.total,
          current: p?.current,
          detail: p?.detail,
        }));
      } else if (evt.event === "progress") {
        setBakeProgress((p) => ({
          phase: p?.phase ?? "bake",
          label: p?.label ?? "Baking",
          current: evt.current,
          total: evt.total,
          detail: evt.detail,
        }));
      } else if (evt.event === "done") {
        setBakeProgress(null);
      }
    }, sheetMeta.data?.fingerprint)
      .then((u) => {
        if (cancelled || !u) return;
        setSheetUrl(u);
        setBakeProgress(null);
      })
      .catch(() => {
        if (!cancelled) setBakeProgress(null);
      });
    return () => {
      cancelled = true;
    };
    // sheetMeta.data?.fingerprint in deps: re-bake + re-slice when the
    // tileset's slot map changes (e.g. a tile was added), so the palette
    // never renders a stale sheet against fresh cell coords.
  }, [xmlPath, tileset, sheetMeta.data?.fingerprint]);

  // Build a quick slot → cell lookup so each tile can find its crop.
  const cellBySlot = useMemo(() => {
    if (!sheetMeta.data) return new Map<number, PaletteSheetCell>();
    const m = new Map<number, PaletteSheetCell>();
    for (const c of sheetMeta.data.cells) m.set(c.slot, c);
    return m;
  }, [sheetMeta.data]);

  const [filter, setFilter] = useState("");
  // Debounce the filter feeding `byCategory`. With ~150 slots (or 200+
  // in "Other / Uncategorized") and an unmemoized re-render of every
  // SlotTile, each keystroke previously walked the whole list synchronously.
  // 100 ms is short enough that the user perceives the filter as instant
  // but skips intermediate "ab", "ab " keystrokes during typing.
  const [debouncedFilter, setDebouncedFilter] = useState("");
  useEffect(() => {
    const id = window.setTimeout(() => setDebouncedFilter(filter), 100);
    return () => window.clearTimeout(id);
  }, [filter]);
  // The library tab moved to the Tileset Editor route — this palette is
  // now tileset-only (just the slots already registered in Ja2Set.dat.xml).
  // See docs/TILESET_EDITOR_SPLIT.md.
  // Subframe picker: when a tile is clicked, expand to show every sub
  // of that slot so the user can pick the exact frame they want.
  // Fixes the BLTRUCK case where the palette tile shows the shadow
  // (frame 0) but the actual usable frames are 2-4. Null when no
  // tile is expanded.
  const [expandedSlot, setExpandedSlot] = useState<number | null>(null);
  // Accordion model: at most one category is open at a time. Solves
  // the "Other has 200 slots and blocks every other category once
  // you open it" scroll wall. Initial state is null (nothing open) —
  // the user picks what to drill into.
  //
  // Exception: when the search filter is active, ALL categories show
  // their matching slots simultaneously so the user can see hits
  // across the whole tileset. Filter takes priority over the
  // accordion's single-expand rule.
  const [expandedCategory, setExpandedCategory] =
    useState<string | null>(null);
  // Per-category refs so we can scrollIntoView the just-opened
  // category header up to the top of the sidebar — otherwise opening
  // a category near the bottom of the list leaves the header off-
  // screen and the user has to scroll back up to see what they
  // expanded.
  const categoryRefs = useRef<Map<string, HTMLDivElement | null>>(new Map());

  function toggleCategory(c: string) {
    setExpandedCategory((prev) => {
      const next = prev === c ? null : c;
      // Defer the scroll until after React has rendered the new
      // expanded state — otherwise we'd scroll to the header BEFORE
      // its children mount, which the browser computes wrong.
      if (next !== null) {
        requestAnimationFrame(() => {
          const el = categoryRefs.current.get(next);
          if (el) el.scrollIntoView({ block: "start", behavior: "smooth" });
        });
      }
      return next;
    });
  }

  const byCategory = useMemo(() => {
    if (!palette.data) return null;
    const groups: Record<string, PaletteSlot[]> = {};
    const needle = debouncedFilter.trim().toLowerCase();
    for (const s of palette.data.slots) {
      // Engine cap — painting slots above ja2.exe's NUMBEROFTILETYPES
      // produces .dat entries the engine can't render. Hide them so
      // the user can't accidentally pick a brush the engine rejects.
      // The setting defaults to 150 (stock 1.13); users with custom
      // builds bump it in the settings modal.
      if (s.slot > engineMaxTileSlot) continue;
      // Hide shadow-only slots (FIRSTSHADOW, FENCESHADOW, etc.) unless
      // the user has explicitly chosen to see them. With auto-pair on,
      // shadows ride along with their struct and the user never picks
      // them directly — surfacing them clutters the palette.
      if (!showShadowSlots && isShadowOnlySlot(s.slot)) continue;
      if (needle && !s.sti_filename.toLowerCase().includes(needle)
          && !String(s.slot).includes(needle)
          && !s.category.includes(needle)) {
        continue;
      }
      (groups[s.category] ??= []).push(s);
    }
    return groups;
  }, [palette.data, debouncedFilter, showShadowSlots, engineMaxTileSlot]);

  // Count of slots filtered solely by the engine cap (not by search
  // or shadow-hiding) — surfaced as a small advisory so the user
  // knows there ARE more slots in the XML, they're just out of the
  // engine's compiled range.
  const slotsHiddenByCap = useMemo(() => {
    if (!palette.data) return 0;
    return palette.data.slots.filter((s) => s.slot > engineMaxTileSlot).length;
  }, [palette.data, engineMaxTileSlot]);

  return (
    <div className="flex h-full flex-col rounded border border-gray-700 bg-gray-950">
      {/* Search header. The "active brush" block that used to live
          here was redundant with the toolbar BrushChip and ate
          sidebar space — pick events now flow through the log
          (transient confirmation) and the toolbar chip (durable
          indicator of what's loaded). */}
      <div className="border-b border-gray-800 p-2">
        <input
          type="search"
          value={filter}
          onChange={(e) => setFilter(e.target.value)}
          placeholder="Filter by name / slot…"
          className="w-full rounded border border-gray-700 bg-gray-900 px-2 py-1 text-xs"
          title="Filter by STI filename, slot number, or category"
        />
      </div>

      {/* Tileset palette body. Library tab moved to /tileset-editor —
          this palette is tileset-only now. */}
      <div className="flex-1 overflow-y-auto p-1">
        {(palette.isLoading || sheetMeta.isLoading || !sheetUrl) && (
          <div className="p-2 space-y-1.5" role="status" aria-busy="true">
            <p className="text-xs text-gray-200">
              {bakeProgress?.label
                ? `${bakeProgress.label}…`
                : "Loading palette + sprite sheet…"}
            </p>
            {bakeProgress?.detail && (
              <p className="truncate font-mono text-[10px] text-blue-300">
                {bakeProgress.detail}
              </p>
            )}
            {/* Determinate bar when the bake stream provides current /
                total counters; falls back to the pulsing indeterminate
                strip for the brief windows before/after the bake (e.g.
                "Locating tileset STIs", "Encoding PNG"). */}
            {bakeProgress?.current !== undefined && bakeProgress?.total ? (
              <>
                <div className="h-1.5 w-full overflow-hidden rounded-full border border-gray-800 bg-gray-900">
                  <div
                    className="h-full bg-blue-500 transition-[width] duration-100 ease-linear"
                    style={{
                      width: `${Math.min(100, Math.round((bakeProgress.current / bakeProgress.total) * 100))}%`,
                    }}
                  />
                </div>
                <p className="text-[10px] text-gray-500">
                  {bakeProgress.current} / {bakeProgress.total}{" "}
                  ({Math.round((bakeProgress.current / bakeProgress.total) * 100)}%)
                </p>
              </>
            ) : (
              <div className="h-1.5 w-full overflow-hidden rounded-full border border-gray-800 bg-gray-900">
                <div className="h-full w-full bg-gradient-to-r from-blue-700/30 via-blue-400/60 to-blue-700/30 animate-pulse" />
              </div>
            )}
            <p className="text-[10px] text-gray-600">
              First load of a tileset can take up to a minute while the
              sprite sheet bakes. Subsequent opens are instant from
              cache.
            </p>
          </div>
        )}
        {palette.error && (
          <p className="p-2 text-xs text-red-400">
            {palette.error instanceof Error ? palette.error.message : String(palette.error)}
          </p>
        )}
        {slotsHiddenByCap > 0 && (
          <div
            className="m-1 rounded border border-amber-800 bg-amber-950/40 px-2 py-1.5 text-[10px] text-amber-300"
            title={
              `Ja2Set.dat.xml defines slots above the engine's compiled tile-type cap. `
              + `Those slots are hidden so they can't be painted — the game crashes when `
              + `it loads a sector referencing them. Adjust the cap in Settings if your `
              + `ja2.exe is a custom build that supports a higher NUMBEROFTILETYPES.`
            }
          >
            <b>{slotsHiddenByCap}</b> slot{slotsHiddenByCap === 1 ? "" : "s"}{" "}
            hidden — above engine cap {engineMaxTileSlot}.
            Adjust in Settings if your <code>ja2.exe</code> supports more.
          </div>
        )}
        {palette.data && byCategory && sheetUrl && sheetMeta.data
            && palette.data.category_order.map((cat) => {
          const slots = byCategory[cat];
          if (!slots || slots.length === 0) return null;
          // Filter-active mode shows everything regardless of which
          // category the user expanded; otherwise the accordion rule
          // applies (only `expandedCategory` is open). Use the
          // debounced filter so the expand-all behavior tracks what
          // byCategory just filtered on, not the in-flight keystroke.
          const filterActive = debouncedFilter.trim().length > 0;
          const isExpanded = filterActive || expandedCategory === cat;
          return (
            <div
              key={cat}
              className="mb-2"
              ref={(el) => {
                if (el) categoryRefs.current.set(cat, el);
                else categoryRefs.current.delete(cat);
              }}
            >
              <button
                type="button"
                onClick={() => toggleCategory(cat)}
                title={`${isExpanded ? "Collapse" : "Expand"} ${CATEGORY_LABELS[cat] ?? cat} (${slots.length} slot${slots.length === 1 ? "" : "s"})${filterActive ? " — filter active, all categories shown" : ""}`}
                className={`sticky top-0 z-10 flex w-full items-center justify-between rounded px-1.5 py-1 text-left text-xs font-semibold ${
                  isExpanded
                    ? "bg-gray-900 text-gray-100 ring-1 ring-gray-700"
                    : "bg-gray-950 text-gray-300 hover:bg-gray-900"
                }`}
              >
                <span>
                  {isExpanded ? "▾" : "▸"} {CATEGORY_LABELS[cat] ?? cat}
                </span>
                <span className="text-[10px] text-gray-500">{slots.length}</span>
              </button>
              {isExpanded && (
                <CategoryGrid
                  slots={slots}
                  cellBySlot={cellBySlot}
                  sheetUrl={sheetUrl}
                  sheetW={sheetMeta.data!.sheet_w}
                  sheetH={sheetMeta.data!.sheet_h}
                  expandedSlot={expandedSlot}
                  activeBrush={activeBrush}
                  renderer={renderer}
                  onExpandToggle={(slot) => setExpandedSlot(
                    expandedSlot === slot ? null : slot,
                  )}
                  onPickSub={(slot, sub, sti_filename, category) => {
                    onPick({
                      slot, sub, category,
                      layer: CATEGORY_TO_LAYER[category] ?? "structs",
                      sti_filename,
                    });
                    setExpandedSlot(null);
                  }}
                />
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}

/** Per-category grid that supports inline subframe expansion. When a
 * tile is clicked, the row containing it gets a sub-picker rendered
 * directly below the clicked tile (full-width). Click a sub thumb to
 * commit it as the active brush. */
function CategoryGrid({
  slots, cellBySlot, sheetUrl, sheetW, sheetH,
  expandedSlot, activeBrush, renderer,
  onExpandToggle, onPickSub,
}: {
  slots: PaletteSlot[];
  cellBySlot: Map<number, PaletteSheetCell>;
  sheetUrl: string;
  sheetW: number;
  sheetH: number;
  expandedSlot: number | null;
  activeBrush: ActiveBrush | null;
  renderer: IsoRenderer | null;
  onExpandToggle: (slot: number) => void;
  onPickSub: (slot: number, sub: number, sti_filename: string, category: string) => void;
}) {
  // We can't pause-and-insert inside a CSS grid mid-flow, so render
  // the grid as a flat list of cells, and the expanded sub-picker is
  // a row that spans all 3 columns immediately after the row
  // containing the expanded tile.
  //
  // Layout strategy: split `slots` into 3-tile rows, render each row
  // as its own grid container. If the expanded slot lives in a row,
  // render the sub-picker below that row only.
  const rows = useMemo(() => {
    const out: PaletteSlot[][] = [];
    for (let i = 0; i < slots.length; i += 3) {
      out.push(slots.slice(i, i + 3));
    }
    return out;
  }, [slots]);
  const expandedSlotInfo = expandedSlot !== null
    ? slots.find((s) => s.slot === expandedSlot)
    : null;
  // Stable callbacks for SlotTile.memo. Without these every SlotTile
  // sees a fresh onPickSlot identity per parent render and the memo
  // is dead weight.
  const handlePickSlot = useCallback(
    (slot: number, sti_filename: string, category: string) => {
      onPickSub(slot, 1, sti_filename, category);
    },
    [onPickSub],
  );
  const handleExpandSlot = useCallback(
    (slot: number) => onExpandToggle(slot),
    [onExpandToggle],
  );
  return (
    <div className="space-y-1 p-1">
      {rows.map((row, rowIdx) => {
        const expandedHere = expandedSlotInfo
          && row.some((s) => s.slot === expandedSlotInfo.slot);
        return (
          <div key={rowIdx}>
            <div className="grid grid-cols-3 gap-1">
              {row.map((s) => {
                const cell = cellBySlot.get(s.slot);
                // Multi-tile-JSD slots (helis, vehicles, big debris)
                // get auto-committed at sub=1 because the painter
                // expands the click into the full footprint via the
                // atlas manifest's slot_jsd_footprint map.
                const footprint = renderer?.getFootprint(s.slot) ?? null;
                const footprintSize = footprint?.tiles.length ?? 0;
                return (
                  <SlotTile
                    key={s.slot}
                    slot={s}
                    sheetUrl={sheetUrl}
                    sheetW={sheetW}
                    sheetH={sheetH}
                    cell={cell ?? null}
                    isActive={activeBrush?.slot === s.slot}
                    isExpanded={expandedSlot === s.slot}
                    footprintSize={footprintSize}
                    onPickSlot={handlePickSlot}
                    onExpandSlot={handleExpandSlot}
                  />
                );
              })}
            </div>
            {expandedHere && expandedSlotInfo && (
              <SubframePicker
                slot={expandedSlotInfo}
                renderer={renderer}
                activeBrush={activeBrush}
                onPickSub={(sub) => onPickSub(
                  expandedSlotInfo.slot, sub,
                  expandedSlotInfo.sti_filename,
                  expandedSlotInfo.category,
                )}
                onClose={() => onExpandToggle(expandedSlotInfo.slot)}
              />
            )}
          </div>
        );
      })}
    </div>
  );
}

/** Inline subframe picker. Shows N small thumbnails (one per sub) for
 * the expanded slot's STI. Click a sub thumb to commit. The renderer's
 * atlas backs the per-sub thumbs — zero HTTP, instant. */
function SubframePicker({
  slot, renderer, activeBrush, onPickSub, onClose,
}: {
  slot: PaletteSlot;
  renderer: IsoRenderer | null;
  activeBrush: ActiveBrush | null;
  onPickSub: (sub: number) => void;
  onClose: () => void;
}) {
  const subCount = slot.frame_count;
  return (
    <div className="mt-1 rounded border border-blue-700 bg-blue-950/40 p-1.5">
      <div className="mb-1 flex items-center justify-between text-[10px]">
        <span className="font-mono text-blue-200">
          {slot.sti_filename} · slot {slot.slot} · {subCount} frame
          {subCount === 1 ? "" : "s"}
        </span>
        <button
          type="button"
          onClick={onClose}
          className="text-blue-400 hover:text-blue-200"
          title="Close subframe picker"
        >✕</button>
      </div>
      {!renderer ? (
        <p className="text-[10px] text-gray-500">Atlas still loading…</p>
      ) : (
        <div className="grid grid-cols-4 gap-1">
          {Array.from({ length: subCount }, (_, i) => i + 1).map((sub) => {
            const isPicked = activeBrush?.slot === slot.slot
                              && activeBrush?.sub === sub;
            return (
              <button
                key={sub}
                type="button"
                onClick={() => onPickSub(sub)}
                title={`Pick sub ${sub}`}
                className={`flex flex-col items-center rounded border p-0.5 hover:bg-gray-800 ${
                  isPicked
                    ? "border-emerald-500 bg-emerald-950/50"
                    : "border-gray-700 bg-gray-900"
                }`}
              >
                <AtlasSubThumb
                  renderer={renderer}
                  slot={slot.slot}
                  sub={sub}
                  size={40}
                />
                <span className={`text-[9px] ${isPicked ? "text-emerald-300" : "text-gray-500"}`}>
                  sub {sub}
                </span>
              </button>
            );
          })}
        </div>
      )}
    </div>
  );
}

/** Renderer-backed thumbnail for a single (slot, sub). Mirror of the
 * AtlasFrameThumb in MapForgeSector — but lives here to avoid a
 * circular import. */
function AtlasSubThumb({
  renderer, slot, sub, size,
}: {
  renderer: IsoRenderer;
  slot: number;
  sub: number;
  size: number;
}) {
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const [missing, setMissing] = useState(false);
  useEffect(() => {
    if (!canvasRef.current) return;
    const ctx = canvasRef.current.getContext("2d");
    if (!ctx) return;
    const ok = renderer.drawCellInto(ctx, slot, sub, size, size);
    setMissing(!ok);
  }, [renderer, slot, sub, size]);
  if (missing) {
    return (
      <span
        className="inline-flex items-center justify-center rounded bg-gray-800 text-[8px] text-gray-500"
        style={{ width: size, height: size }}
        title="not in atlas"
      >?</span>
    );
  }
  return (
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
  );
}

interface SlotTileProps {
  slot: PaletteSlot;
  sheetUrl: string;
  sheetW: number;
  sheetH: number;
  cell: PaletteSheetCell | null;
  isActive: boolean;
  isExpanded: boolean;
  /** Number of tiles in this slot's JSD footprint. 0 = single-tile
   * (no footprint, no badge). >= 2 = multi-tile (badge + stamp on
   * click). The painter uses the manifest's footprint to expand
   * each click into per-tile placements. */
  footprintSize: number;
  /** Stable parent callback fired when the user clicks a tile that
   * should commit at sub=1 (multi-tile stamp or single-frame STI). */
  onPickSlot: (slot: number, sti_filename: string, category: string) => void;
  /** Stable parent callback fired when the user clicks a tile that
   * should open the inline sub-frame picker. */
  onExpandSlot: (slot: number) => void;
}

const SlotTile = memo(function SlotTile({
  slot, sheetUrl, sheetW, sheetH, cell, isActive, isExpanded,
  footprintSize, onPickSlot, onExpandSlot,
}: SlotTileProps) {
  const isMultiTile = footprintSize >= 2;
  const isSingleFrame = slot.frame_count <= 1;
  const titleParts = [
    slot.sti_filename,
    `slot ${slot.slot}`,
    `${slot.frame_count} frame${slot.frame_count === 1 ? "" : "s"}`,
  ];
  if (isMultiTile) {
    titleParts.push(`multi-tile struct (${footprintSize} tiles — one click stamps the whole thing)`);
  } else if (slot.has_jsd) titleParts.push("has JSD (single-tile)");
  const tip = titleParts.join(" · ") + "\nClick to set as active brush"
    + (slot.frame_count > 1 ? " — pick the sub-frame in the Variants panel" : "");
  return (
    <button
      type="button"
      // Always commit the slot at sub 1 — sub-frame selection moved to the
      // Variants panel (the inline expander was removed). Multi-tile slots
      // still stamp their whole footprint via the painter.
      onClick={() => onPickSlot(slot.slot, slot.sti_filename, slot.category)}
      title={tip}
      className={`relative flex flex-col items-center rounded border p-1 text-[9px] hover:bg-gray-800 ${
        isExpanded
          ? "border-blue-500 bg-blue-950/40"
          : isActive
            ? "border-emerald-500 bg-emerald-950/50"
            : "border-gray-700 bg-gray-900"
      }`}
    >
      {cell ? (
        <div
          style={{
            width: cell.w, height: cell.h,
            backgroundImage: `url(${sheetUrl})`,
            backgroundPosition: `-${cell.px}px -${cell.py}px`,
            backgroundSize: `${sheetW}px ${sheetH}px`,
            backgroundRepeat: "no-repeat",
            imageRendering: "pixelated",
          }}
        />
      ) : (
        <span className="inline-block h-10 w-10 rounded bg-gray-800" />
      )}
      {/* Multi-tile stamp badge takes priority over the frame-count
          badge: a multi-tile slot's frame count IS its footprint
          size, and the "click pick" semantics are different — for
          stamps, one click drops the whole footprint instead of
          opening a sub picker. The amber-on-dark tint distinguishes
          stamp-eligible slots at a glance. */}
      {isMultiTile ? (
        <span
          className="absolute right-0 top-0 rounded-bl rounded-tr bg-amber-700/85 px-1 text-[8px] text-amber-50"
          title={`Multi-tile struct (${footprintSize} pieces) — click stamps the whole footprint`}
        >
          ▦{footprintSize}
        </span>
      ) : slot.frame_count > 1 ? (
        <span
          className="absolute right-0 top-0 rounded-bl rounded-tr bg-blue-700/80 px-1 text-[8px] text-blue-50"
          title={`${slot.frame_count} frames — click to pick a sub`}
        >
          {slot.frame_count}f
        </span>
      ) : null}
      <div className="mt-0.5 truncate text-gray-400" style={{ maxWidth: 64 }}>
        {slot.sti_filename.replace(/\.sti$/i, "")}
      </div>
      <div className="text-gray-600">s{slot.slot}</div>
    </button>
  );
});

