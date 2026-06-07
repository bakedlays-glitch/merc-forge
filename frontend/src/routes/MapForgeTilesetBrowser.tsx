/**
 * MapForge — stock-tileset browser ("browse + inspect, nothing fancy").
 *
 * A VIEWER for the slots defined in any tileset of the active install's
 * `Ja2Set.dat.xml`. It replaces the catalog-gated "Browse Assets" panel
 * in the map editor (which depended on the 71 MB Asset_Browser catalog
 * that isn't shipped). This browser uses ONLY the existing tileset
 * endpoints — no catalog, no health check, no new backend route:
 *
 *   - `listTilesets(xml)`           → the tileset selector.
 *   - `getTilesetPalette(xml, ts)`  → per-slot metadata + `category_order`.
 *   - `getPaletteSheetMeta(xml, ts)`→ per-slot crop rect into the sheet.
 *   - `prefetchPaletteSheet(...)`   → streams the (cold) sprite-sheet bake
 *                                     with live progress, then warms the
 *                                     shared, app-lifetime blob-URL cache.
 *   - `getCachedPaletteSheetBlobUrl`→ the single sprite-sheet PNG; each
 *                                     thumbnail is a CSS background-image
 *                                     slice (background-position) of it.
 *
 * The thumbnail rendering deliberately mirrors `MapForgePalette` /
 * `MapForgePaletteRail` — same blob-URL + CSS-slice technique, same
 * React Query usage, same prefetch path — so the browser is visually and
 * technically consistent with the working painting palette.
 *
 * INTERACTION = viewer + one write action. Clicking a tile shows its
 * details (slot #, STI filename, category, frame count, JSD presence).
 * It does NOT set the paint brush (a sector paints from exactly one
 * tileset, so cross-tileset painting isn't engine-valid). The one write
 * it offers is "Add to current tileset": COPY the browsed tile into the
 * ACTIVE SECTOR's tileset as a new slot (engine-safe append), shown only
 * when the browsed tileset differs from the active sector's tileset.
 */
import { useEffect, useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import {
  copyTileToTileset,
  getPaletteSheetMeta,
  getTilesetPalette,
  listTilesets,
  prefetchPaletteSheet,
  type PaletteSheetBuildEvent,
  type PaletteSheetCell,
  type PaletteSlot,
} from "../lib/mapforge";
import { loadSettings } from "../lib/mapforgeSettings";

// Human-readable category labels. Kept local (not imported from
// MapForgePalette) so this viewer stays self-contained — it's a separate
// surface with no shared lifecycle. Unknown categories fall back to the
// raw key.
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

export interface MapForgeTilesetBrowserProps {
  /** Path to the active install's `Ja2Set.dat.xml`. Same value the
   * painting palette + sector renderer receive (from the `?xml=` URL
   * param in MapForgeSector). Empty string → the browser shows an
   * "unavailable" empty state. */
  xmlPath: string;
  /** Tileset to pre-select when the browser opens — typically the
   * active sector's tileset, so the user lands on the tiles they're
   * editing. The user can switch to any other tileset via the
   * selector. */
  defaultTileset?: number;
  /** The ACTIVE SECTOR's tileset — the destination for the "Add to
   * current tileset" action. Distinct from the browsed tileset (the
   * user can navigate the browser to ANY tileset; the destination must
   * stay pinned to the sector being edited). When undefined the add
   * action is hidden (no sector context → no safe destination). */
  activeSectorTileset?: number;
  /** Read-only mode: hide the "Add to current tileset" copy action. Used
   * by the "Tileset Viewer" panel (cross-tileset import is shelved). */
  readOnly?: boolean;
}

export function MapForgeTilesetBrowser({
  xmlPath,
  defaultTileset,
  activeSectorTileset,
  readOnly,
}: MapForgeTilesetBrowserProps) {
  // The list of tilesets in this install's Ja2Set.dat.xml drives the
  // selector. Reuses the same query key shape as the Tileset Editor.
  const tilesets = useQuery({
    queryKey: ["mapforge", "tilesets", xmlPath],
    queryFn: () => listTilesets(xmlPath),
    enabled: !!xmlPath,
    staleTime: 5 * 60 * 1000,
  });

  // Selected tileset index. Initialized to defaultTileset; once the
  // tileset list lands we make sure the selection is a real tileset
  // (falls back to the first one if defaultTileset isn't present).
  const [selected, setSelected] = useState<number | null>(
    defaultTileset ?? null,
  );
  useEffect(() => {
    if (!tilesets.data || tilesets.data.tilesets.length === 0) return;
    const indices = tilesets.data.tilesets.map((t) => t.index);
    setSelected((cur) => {
      if (cur !== null && indices.includes(cur)) return cur;
      if (defaultTileset !== undefined && indices.includes(defaultTileset)) {
        return defaultTileset;
      }
      return indices[0] ?? null;
    });
  }, [tilesets.data, defaultTileset]);

  if (!xmlPath) {
    return (
      <div className="flex h-full flex-col rounded border border-gray-700 bg-gray-950">
        <div className="p-3 text-xs text-gray-500">
          No <code>Ja2Set.dat.xml</code> for this install — the tileset
          browser is unavailable.
        </div>
      </div>
    );
  }

  return (
    <div className="flex h-full flex-col rounded border border-gray-700 bg-gray-950">
      {/* Tileset selector */}
      <div className="border-b border-gray-800 p-2">
        <label className="mb-1 block text-[10px] uppercase tracking-wider text-gray-500">
          Tileset
        </label>
        {tilesets.isLoading ? (
          <p className="text-xs text-gray-400">Loading tilesets…</p>
        ) : tilesets.error ? (
          <p className="text-xs text-red-400">
            {tilesets.error instanceof Error
              ? tilesets.error.message
              : String(tilesets.error)}
          </p>
        ) : tilesets.data && tilesets.data.tilesets.length > 0 ? (
          <select
            value={selected ?? ""}
            onChange={(e) => setSelected(parseInt(e.target.value, 10))}
            title="Pick a tileset to browse. Each Ja2Set.dat.xml block defines its own slot map; tile 0 is inherited where the block opts in."
            className="w-full rounded border border-gray-700 bg-gray-900 px-2 py-1 text-xs"
          >
            {tilesets.data.tilesets.map((t) => (
              <option key={t.index} value={t.index}>
                #{t.index} — {t.name ?? "(unnamed)"} · {t.slot_count} slot
                {t.slot_count === 1 ? "" : "s"}
                {t.inherits_from_0 ? " · +tile0" : ""}
              </option>
            ))}
          </select>
        ) : (
          <p className="text-xs text-gray-500">
            No tilesets defined in this install.
          </p>
        )}
      </div>

      {/* Body: the selected tileset's slots, grouped by category. */}
      {selected !== null ? (
        <TilesetSlotBrowser
          key={`${xmlPath}::${selected}`}
          xmlPath={xmlPath}
          tileset={selected}
          activeSectorTileset={activeSectorTileset}
          readOnly={readOnly}
        />
      ) : (
        <div className="flex-1 p-3 text-xs text-gray-500">
          Select a tileset to browse its tiles.
        </div>
      )}
    </div>
  );
}

/** The slot grid for ONE tileset: fetches palette + sheet meta + the
 * sprite-sheet blob (with bake progress), groups by `category_order`,
 * supports a name/slot/category filter, and shows a detail panel for the
 * clicked tile. */
function TilesetSlotBrowser({
  xmlPath,
  tileset,
  activeSectorTileset,
  readOnly,
}: {
  xmlPath: string;
  tileset: number;
  activeSectorTileset?: number;
  readOnly?: boolean;
}) {
  // Palette: per-slot metadata + the canonical category_order. Same query
  // key shape as MapForgePalette so React Query dedupes if both are
  // mounted for the same (xml, tileset).
  const palette = useQuery({
    queryKey: ["mapforge", "palette", xmlPath, tileset],
    queryFn: () => getTilesetPalette(xmlPath, tileset),
    enabled: !!xmlPath && tileset >= 0,
    staleTime: 5 * 60 * 1000,
  });

  // Sheet meta: per-slot crop rectangle (px, py, w, h) into the sprite
  // sheet PNG. Same key shape as MapForgePalette.
  const sheetMeta = useQuery({
    queryKey: ["mapforge", "palette-sheet-meta", xmlPath, tileset],
    queryFn: () => getPaletteSheetMeta(xmlPath, tileset),
    enabled: !!xmlPath && tileset >= 0,
    staleTime: 5 * 60 * 1000,
  });

  // Dedupe support: when browsing a tileset OTHER than the one the sector
  // is on, fetch the active sector tileset's palette so we can hide tiles
  // the user already has there. Same query key as the paint palette + the
  // Add-to-current pre-flight, so in the Sector context this is a cache
  // hit (no extra round-trip). No-op when browsing your own tileset.
  const canDedupe =
    activeSectorTileset !== undefined && activeSectorTileset !== tileset;
  const currentPalette = useQuery({
    queryKey: ["mapforge", "palette", xmlPath, activeSectorTileset],
    queryFn: () => getTilesetPalette(xmlPath, activeSectorTileset!),
    enabled: !!xmlPath && canDedupe && (activeSectorTileset ?? -1) >= 0,
    staleTime: 5 * 60 * 1000,
  });
  // Lowercased STI filenames already registered in the active sector's
  // tileset. getTilesetPalette overlays tile-0 inheritance (same as the
  // engine), so this includes the shared base tiles — the bulk of the
  // cross-tileset overlap and the whole point of the dedupe.
  const currentNames = useMemo(() => {
    const s = new Set<string>();
    if (canDedupe && currentPalette.data) {
      for (const slot of currentPalette.data.slots) {
        s.add(slot.sti_filename.toLowerCase());
      }
    }
    return s;
  }, [canDedupe, currentPalette.data]);

  // Sprite-sheet blob URL + live bake progress. Mirrors MapForgePalette's
  // effect exactly: prefetchPaletteSheet streams the (cold) bake for
  // progress, then resolves the SHARED, app-lifetime blob URL from cache.
  // The cache owns the blob URL's lifetime, so we never revoke it here.
  const [sheetUrl, setSheetUrl] = useState<string | null>(null);
  // Sprite-sheet bake failure (network / bake error). Tracked separately
  // so a failed bake surfaces an error instead of an infinite spinner —
  // without it the `loading` guard (which waits on a non-null sheetUrl)
  // would never clear.
  const [sheetError, setSheetError] = useState<string | null>(null);
  const [bakeProgress, setBakeProgress] = useState<{
    label: string;
    current?: number;
    total?: number;
    detail?: string;
  } | null>(null);
  useEffect(() => {
    if (!xmlPath || tileset < 0) {
      setSheetUrl(null);
      setBakeProgress(null);
      setSheetError(null);
      return;
    }
    let cancelled = false;
    setSheetUrl(null);
    setSheetError(null);
    setBakeProgress({ label: "Starting bake" });
    prefetchPaletteSheet(xmlPath, tileset, (evt: PaletteSheetBuildEvent) => {
      if (cancelled) return;
      if (evt.event === "phase") {
        setBakeProgress((p) => ({
          label: evt.label,
          total: evt.total ?? p?.total,
          current: p?.current,
          detail: p?.detail,
        }));
      } else if (evt.event === "progress") {
        setBakeProgress({
          label: "Baking sprite sheet",
          current: evt.current,
          total: evt.total,
          detail: evt.detail,
        });
      } else if (evt.event === "done") {
        setBakeProgress(null);
      }
    }, sheetMeta.data?.fingerprint)
      .then((u) => {
        if (cancelled || !u) return;
        setSheetUrl(u);
        setBakeProgress(null);
      })
      .catch((err) => {
        if (cancelled) return;
        setBakeProgress(null);
        setSheetError(err instanceof Error ? err.message : String(err));
      });
    return () => {
      cancelled = true;
    };
    // sheetMeta.data?.fingerprint in deps: re-fetch the sheet when this
    // tileset's slot map changes (e.g. an "Add to current tileset"), so the
    // grid never slices a stale sheet against fresh cell coords.
  }, [xmlPath, tileset, sheetMeta.data?.fingerprint]);

  // slot → crop-cell lookup, so each tile can find its slice in O(1).
  const cellBySlot = useMemo(() => {
    const m = new Map<number, PaletteSheetCell>();
    if (sheetMeta.data) for (const c of sheetMeta.data.cells) m.set(c.slot, c);
    return m;
  }, [sheetMeta.data]);

  // Filter input — matches STI filename, slot number, or category name.
  // Debounced so typing doesn't re-walk the whole slot list per keystroke
  // (the palette can be 150+ slots). Mirrors MapForgePalette's 100 ms.
  const [filter, setFilter] = useState("");
  const [debouncedFilter, setDebouncedFilter] = useState("");
  // Hide tiles already in the active sector's tileset. Default ON — when
  // you switch to another tileset to find something to add, the useful
  // view is "what's NEW here", not the long list of shared base tiles you
  // already have. Reversible via the checkbox; no-op when canDedupe false.
  const [hideDuplicates, setHideDuplicates] = useState(true);
  useEffect(() => {
    const id = window.setTimeout(() => setDebouncedFilter(filter), 100);
    return () => window.clearTimeout(id);
  }, [filter]);

  // The currently-inspected slot (detail panel). Cleared when the tileset
  // changes (the component is keyed on (xml, tileset) by the parent, so a
  // tileset switch remounts and resets this to null anyway).
  const [selectedSlot, setSelectedSlot] = useState<number | null>(null);

  // Group the (filtered) slots by category, following category_order.
  const byCategory = useMemo(() => {
    if (!palette.data) return null;
    const groups: Record<string, PaletteSlot[]> = {};
    const needle = debouncedFilter.trim().toLowerCase();
    const dedupe = hideDuplicates && canDedupe;
    for (const s of palette.data.slots) {
      // Dedupe: skip tiles already registered in the active sector's
      // tileset (matched by STI filename, case-insensitive).
      if (dedupe && currentNames.has(s.sti_filename.toLowerCase())) continue;
      if (
        needle &&
        !s.sti_filename.toLowerCase().includes(needle) &&
        !String(s.slot).includes(needle) &&
        !s.category.toLowerCase().includes(needle)
      ) {
        continue;
      }
      (groups[s.category] ??= []).push(s);
    }
    return groups;
  }, [palette.data, debouncedFilter, hideDuplicates, canDedupe, currentNames]);

  // The slot object behind the detail panel (looked up by slot number so
  // it survives a filter that hides the tile from the grid).
  const selectedSlotInfo = useMemo(() => {
    if (selectedSlot === null || !palette.data) return null;
    return palette.data.slots.find((s) => s.slot === selectedSlot) ?? null;
  }, [selectedSlot, palette.data]);

  // Total visible after filter — used for the empty-state ("no matches")
  // vs "tileset has no slots" distinction.
  const visibleCount = useMemo(() => {
    if (!byCategory) return 0;
    return Object.values(byCategory).reduce((n, arr) => n + arr.length, 0);
  }, [byCategory]);

  // How many of this tileset's slots are already in the active sector's
  // tileset — i.e. how many the dedupe toggle hides. Independent of the
  // text filter; labels the checkbox so the user knows what's being hidden.
  const dupCount = useMemo(() => {
    if (!canDedupe || !palette.data) return 0;
    let n = 0;
    for (const s of palette.data.slots) {
      if (currentNames.has(s.sti_filename.toLowerCase())) n++;
    }
    return n;
  }, [canDedupe, palette.data, currentNames]);

  const loading =
    palette.isLoading ||
    sheetMeta.isLoading ||
    (!sheetUrl && !palette.error && !sheetMeta.error && !sheetError);

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      {/* Filter */}
      <div className="space-y-1.5 border-b border-gray-800 p-2">
        <input
          type="search"
          value={filter}
          onChange={(e) => setFilter(e.target.value)}
          placeholder="Filter by name / slot / category…"
          title="Filter by STI filename, slot number, or category name"
          className="w-full rounded border border-gray-700 bg-gray-900 px-2 py-1 text-xs"
        />
        {/* Dedupe toggle — only when browsing a different tileset that
            actually shares tiles with the current one. */}
        {canDedupe && dupCount > 0 && (
          <label
            className="flex items-center gap-1.5 text-[11px] text-gray-400"
            title="Hide tiles whose STI is already registered in the tileset you're editing — including the shared tile-0 base tiles — leaving only what's genuinely new to add."
          >
            <input
              type="checkbox"
              checked={hideDuplicates}
              onChange={(e) => setHideDuplicates(e.target.checked)}
            />
            <span>
              Hide tiles already in current tileset
              <span className="text-gray-600"> ({dupCount})</span>
            </span>
          </label>
        )}
      </div>

      {/* Slot grid (scrolls) */}
      <div className="min-h-0 flex-1 overflow-y-auto p-1">
        {loading && (
          <div className="space-y-1.5 p-2" role="status" aria-busy="true">
            <p className="text-xs text-gray-200">
              {bakeProgress?.label ? `${bakeProgress.label}…` : "Loading tileset…"}
            </p>
            {bakeProgress?.detail && (
              <p className="truncate font-mono text-[10px] text-blue-300">
                {bakeProgress.detail}
              </p>
            )}
            {bakeProgress?.current !== undefined && bakeProgress?.total ? (
              <>
                <div className="h-1.5 w-full overflow-hidden rounded-full border border-gray-800 bg-gray-900">
                  <div
                    className="h-full bg-blue-500 transition-[width] duration-100 ease-linear"
                    style={{
                      width: `${Math.min(
                        100,
                        Math.round((bakeProgress.current / bakeProgress.total) * 100),
                      )}%`,
                    }}
                  />
                </div>
                <p className="text-[10px] text-gray-500">
                  {bakeProgress.current} / {bakeProgress.total} (
                  {Math.round((bakeProgress.current / bakeProgress.total) * 100)}%)
                </p>
              </>
            ) : (
              <div className="h-1.5 w-full overflow-hidden rounded-full border border-gray-800 bg-gray-900">
                <div className="h-full w-full animate-pulse bg-gradient-to-r from-blue-700/30 via-blue-400/60 to-blue-700/30" />
              </div>
            )}
            <p className="text-[10px] text-gray-600">
              First load of a tileset can take up to a minute while the sprite
              sheet bakes. Subsequent opens are instant from cache.
            </p>
          </div>
        )}

        {palette.error && (
          <p className="p-2 text-xs text-red-400">
            {palette.error instanceof Error
              ? palette.error.message
              : String(palette.error)}
          </p>
        )}
        {sheetMeta.error && !palette.error && (
          <p className="p-2 text-xs text-red-400">
            {sheetMeta.error instanceof Error
              ? sheetMeta.error.message
              : String(sheetMeta.error)}
          </p>
        )}
        {sheetError && !palette.error && !sheetMeta.error && (
          <p className="p-2 text-xs text-red-400">
            Sprite sheet failed to load: {sheetError}
          </p>
        )}

        {/* Empty states: no slots at all vs no filter matches. */}
        {!loading && palette.data && palette.data.slots.length === 0 && (
          <p className="p-2 text-xs text-gray-500">
            This tileset has no slots.
          </p>
        )}
        {!loading &&
          palette.data &&
          palette.data.slots.length > 0 &&
          visibleCount === 0 &&
          (debouncedFilter.trim() ? (
            <p className="p-2 text-xs text-gray-500">
              No tiles match “{debouncedFilter.trim()}”.
            </p>
          ) : hideDuplicates && canDedupe ? (
            <p className="p-2 text-xs text-gray-500">
              Every tile in this tileset is already in your current tileset.{" "}
              <button
                type="button"
                onClick={() => setHideDuplicates(false)}
                className="underline hover:text-gray-300"
              >
                Show them anyway
              </button>
            </p>
          ) : (
            <p className="p-2 text-xs text-gray-500">No tiles to show.</p>
          ))}

        {/* Categorized grid — iterate category_order; render only the
            categories that have (filtered) slots. */}
        {!loading &&
          palette.data &&
          byCategory &&
          sheetUrl &&
          sheetMeta.data &&
          palette.data.category_order.map((cat) => {
            const slots = byCategory[cat];
            if (!slots || slots.length === 0) return null;
            return (
              <div key={cat} className="mb-2">
                <div className="sticky top-0 z-10 flex items-center justify-between rounded bg-gray-900 px-1.5 py-1 text-xs font-semibold text-gray-100 ring-1 ring-gray-700">
                  <span>{CATEGORY_LABELS[cat] ?? cat}</span>
                  <span className="text-[10px] text-gray-500">{slots.length}</span>
                </div>
                <div className="grid grid-cols-3 gap-1 p-1">
                  {slots.map((s) => (
                    <SlotThumb
                      key={s.slot}
                      slot={s}
                      cell={cellBySlot.get(s.slot) ?? null}
                      sheetUrl={sheetUrl}
                      sheetW={sheetMeta.data!.sheet_w}
                      sheetH={sheetMeta.data!.sheet_h}
                      isSelected={selectedSlot === s.slot}
                      onClick={() =>
                        setSelectedSlot((cur) => (cur === s.slot ? null : s.slot))
                      }
                    />
                  ))}
                </div>
              </div>
            );
          })}
      </div>

      {/* Detail panel for the inspected tile — pinned to the bottom so it
          doesn't push the grid around as the user clicks different
          tiles. Hidden until a tile is clicked. */}
      {selectedSlotInfo && (
        <SlotDetail
          slot={selectedSlotInfo}
          cell={cellBySlot.get(selectedSlotInfo.slot) ?? null}
          sheetUrl={sheetUrl}
          sheetW={sheetMeta.data?.sheet_w ?? 0}
          sheetH={sheetMeta.data?.sheet_h ?? 0}
          xmlPath={xmlPath}
          tileset={tileset}
          activeSectorTileset={activeSectorTileset}
          readOnly={readOnly}
          onClose={() => setSelectedSlot(null)}
        />
      )}
    </div>
  );
}

/** One slot thumbnail, sliced out of the shared sprite-sheet PNG via
 * CSS background-image + background-position. This is the EXACT rendering
 * technique `MapForgePalette`'s SlotTile uses for the painting palette —
 * a `<div>` sized to the cell, the sheet as its background, shifted to
 * the cell's (px, py), `imageRendering: pixelated`. */
function SlotThumb({
  slot,
  cell,
  sheetUrl,
  sheetW,
  sheetH,
  isSelected,
  onClick,
}: {
  slot: PaletteSlot;
  cell: PaletteSheetCell | null;
  sheetUrl: string;
  sheetW: number;
  sheetH: number;
  isSelected: boolean;
  onClick: () => void;
}) {
  const tip =
    [
      slot.sti_filename,
      `slot ${slot.slot}`,
      `${slot.frame_count} frame${slot.frame_count === 1 ? "" : "s"}`,
      slot.category,
      ...(slot.has_jsd ? ["has JSD (multi-tile struct)"] : []),
    ].join(" · ") + "\nClick to inspect";
  return (
    <button
      type="button"
      onClick={onClick}
      title={tip}
      className={`relative flex flex-col items-center rounded border p-1 text-[9px] hover:bg-gray-800 ${
        isSelected
          ? "border-blue-500 bg-blue-950/50"
          : "border-gray-700 bg-gray-900"
      }`}
    >
      {cell ? (
        <div
          style={{
            width: cell.w,
            height: cell.h,
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
      {slot.frame_count > 1 && (
        <span
          className="absolute right-0 top-0 rounded-bl rounded-tr bg-blue-700/80 px-1 text-[8px] text-blue-50"
          title={`${slot.frame_count} frames`}
        >
          {slot.frame_count}f
        </span>
      )}
      {slot.has_jsd && (
        <span
          className="absolute left-0 top-0 rounded-br rounded-tl bg-amber-700/85 px-1 text-[8px] text-amber-50"
          title="Has a .jsd companion (multi-tile structural piece)"
        >
          jsd
        </span>
      )}
      <div className="mt-0.5 truncate text-gray-400" style={{ maxWidth: 64 }}>
        {slot.sti_filename.replace(/\.sti$/i, "")}
      </div>
      <div className="text-gray-600">s{slot.slot}</div>
    </button>
  );
}

/** Detail panel for the inspected slot. Shows a larger preview (the same
 * sheet slice, scaled up) + the slot's metadata, and — when the browsed
 * tileset differs from the ACTIVE SECTOR's tileset — an "Add to current
 * tileset" action that COPIES this tile into the sector's tileset as a
 * new slot (engine-safe append; see AddToCurrentTileset).
 *
 * It still does NOT set the paint brush: a sector paints from exactly one
 * tileset and this browser can show any tileset, so cross-tileset
 * painting isn't engine-valid. "Add to current tileset" is the supported
 * bridge — it brings the tile INTO the sector's tileset first. */
function SlotDetail({
  slot,
  cell,
  sheetUrl,
  sheetW,
  sheetH,
  xmlPath,
  tileset,
  activeSectorTileset,
  readOnly,
  onClose,
}: {
  slot: PaletteSlot;
  cell: PaletteSheetCell | null;
  sheetUrl: string | null;
  sheetW: number;
  sheetH: number;
  xmlPath: string;
  tileset: number;
  activeSectorTileset?: number;
  readOnly?: boolean;
  onClose: () => void;
}) {
  // Scale the preview up to ~3× the cell, clamped so a big multi-tile
  // sprite doesn't blow out the panel. Pure CSS scale of the same slice.
  const scale = cell ? Math.min(3, Math.max(1, Math.floor(120 / Math.max(cell.w, cell.h)))) : 1;
  // Show the add action only when we have a sector destination AND the
  // browsed tileset is a DIFFERENT one (adding a tile to its own tileset
  // is a no-op — the slot is already there).
  const canAddToCurrent =
    !readOnly &&
    activeSectorTileset !== undefined && activeSectorTileset !== tileset;
  return (
    <div className="border-t border-gray-800 bg-gray-900/60 p-2">
      <div className="mb-1 flex items-center justify-between">
        <span className="text-[10px] uppercase tracking-wider text-gray-500">
          Tile details
        </span>
        <button
          type="button"
          onClick={onClose}
          title="Close details"
          className="rounded border border-gray-700 px-1.5 text-[11px] leading-none text-gray-400 hover:bg-gray-800 hover:text-gray-100"
        >
          ✕
        </button>
      </div>
      <div className="flex gap-3">
        {/* Preview — the same sprite-sheet slice, scaled up. */}
        <div className="flex shrink-0 items-center justify-center rounded border border-gray-800 bg-gray-950 p-1">
          {cell && sheetUrl ? (
            <div
              style={{
                width: cell.w * scale,
                height: cell.h * scale,
                backgroundImage: `url(${sheetUrl})`,
                // Scale the background up by the same factor as the box so
                // the slice fills it. background-position scales with
                // background-size, hence the px*scale offsets.
                backgroundPosition: `-${cell.px * scale}px -${cell.py * scale}px`,
                backgroundSize: `${sheetW * scale}px ${sheetH * scale}px`,
                backgroundRepeat: "no-repeat",
                imageRendering: "pixelated",
              }}
            />
          ) : (
            <span className="inline-block h-16 w-16 rounded bg-gray-800" />
          )}
        </div>
        {/* Metadata */}
        <dl className="min-w-0 flex-1 space-y-0.5 text-[11px] text-gray-300">
          <div className="flex gap-1">
            <dt className="text-gray-500">slot</dt>
            <dd className="font-mono">{slot.slot}</dd>
            <dt className="ml-2 text-gray-500">tileset</dt>
            <dd className="font-mono">{tileset}</dd>
          </div>
          <div className="flex gap-1">
            <dt className="text-gray-500">file</dt>
            <dd className="truncate font-mono" title={slot.sti_filename}>
              {slot.sti_filename}
            </dd>
          </div>
          <div className="flex gap-1">
            <dt className="text-gray-500">category</dt>
            <dd>{CATEGORY_LABELS[slot.category] ?? slot.category}</dd>
          </div>
          <div className="flex gap-1">
            <dt className="text-gray-500">frames</dt>
            <dd className="font-mono">{slot.frame_count}</dd>
          </div>
          <div className="flex gap-1">
            <dt className="text-gray-500">JSD</dt>
            <dd>
              {slot.has_jsd ? (
                <span className="text-amber-300">
                  yes — multi-tile structural piece
                </span>
              ) : (
                <span className="text-gray-500">none</span>
              )}
            </dd>
          </div>
        </dl>
      </div>

      {/* "Add to current tileset" — copy this tile into the ACTIVE
          SECTOR's tileset as a new slot. Only when browsing a different
          tileset than the sector's. */}
      {canAddToCurrent && (
        <AddToCurrentTileset
          xmlPath={xmlPath}
          srcTileset={tileset}
          srcSlot={slot.slot}
          srcFilename={slot.sti_filename}
          srcFrameCount={slot.frame_count}
          srcHasJsd={slot.has_jsd}
          destTileset={activeSectorTileset!}
        />
      )}
    </div>
  );
}

/** The "Add to current tileset" control + pre-flight, shown in SlotDetail
 * when the browsed tileset differs from the active sector's tileset.
 *
 * Copies the browsed tile into the ACTIVE SECTOR's tileset (destTileset)
 * as a NEW slot via copyTileToTileset. Default destination slot = the
 * SOURCE slot index (keeps the tile in the same tile-type family). A
 * pre-flight line shows the target tileset + slot and whether that slot
 * is free / occupied / above the engine cap (read from the active
 * tileset's palette + mapforgeSettings). On 409 SLOT_TAKEN it offers an
 * "auto-pick a free slot" recovery, warning that a different slot changes
 * the tile's type/behavior. Whole-STI vs single-sub is offered for
 * multi-frame sources. On success the active tileset's palette +
 * sprite-sheet queries are invalidated so the new tile appears in the
 * paint palette. */
function AddToCurrentTileset({
  xmlPath,
  srcTileset,
  srcSlot,
  srcFilename,
  srcFrameCount,
  srcHasJsd,
  destTileset,
}: {
  xmlPath: string;
  srcTileset: number;
  srcSlot: number;
  srcFilename: string;
  srcFrameCount: number;
  srcHasJsd: boolean;
  destTileset: number;
}) {
  const qc = useQueryClient();
  // Engine cap from user settings — bounds the slot and powers the
  // above-cap pre-flight warning.
  const engineMaxTileSlot = loadSettings().engineMaxTileSlot;
  // Destination slot defaults to the source slot (same tile-type
  // family). Auto-pick (SLOT_TAKEN recovery) overrides this.
  const targetSlot = srcSlot;

  // The destination (active sector) tileset's palette — used to tell the
  // user, BEFORE they click, whether targetSlot is free or occupied.
  // Same query key shape as MapForgePalette so it dedupes with the paint
  // palette if both are mounted.
  const destPalette = useQuery({
    queryKey: ["mapforge", "palette", xmlPath, destTileset],
    queryFn: () => getTilesetPalette(xmlPath, destTileset),
    enabled: !!xmlPath && destTileset >= 0,
    staleTime: 5 * 60 * 1000,
  });
  const occupant = useMemo(() => {
    if (!destPalette.data) return undefined;
    return destPalette.data.slots.find((s) => s.slot === targetSlot);
  }, [destPalette.data, targetSlot]);

  // Whole-STI vs single-sub. Only meaningful for multi-frame sources;
  // single-frame tiles always copy whole. "sub" picks one frame index.
  const [mode, setMode] = useState<"whole" | "sub">("whole");
  const [subIdx, setSubIdx] = useState(0);
  const multiFrame = srcFrameCount > 1;

  const [lastResult, setLastResult] = useState<string | null>(null);
  const [slotTaken, setSlotTaken] = useState<string | null>(null);

  const invalidateDest = () => {
    // Make the new tile show up in the paint palette + browser. The
    // specific (xml, dest tileset) keys cover the active sector's
    // palette + sprite sheet; the broad prefixes mirror what
    // AddStiToTilesetModal invalidates so any other mounted palette
    // view (e.g. the browser still showing the dest tileset) refreshes
    // too. NOTE: the IsoRenderer's in-memory atlas cellMap (the paint
    // SURFACE) is reloaded separately via MapForgeSector's
    // onAtlasChanged — this browser doesn't hold that callback yet, so a
    // freshly-copied tile is paintable after the next atlas reload
    // (tileset re-open / sidecar refresh). The palette/brush list — the
    // task's success target — updates immediately.
    qc.invalidateQueries({ queryKey: ["mapforge", "palette"] });
    qc.invalidateQueries({ queryKey: ["mapforge", "palette-sheet-meta"] });
  };

  const copy = useMutation({
    mutationFn: (opts: { autoPick?: boolean }) =>
      copyTileToTileset(srcTileset, srcSlot, {
        dest_tileset: destTileset,
        // target_slot omitted → backend defaults to src slot; auto_pick
        // overrides to the lowest free slot.
        auto_pick: opts.autoPick || undefined,
        target_sub: mode === "sub" && multiFrame ? subIdx : undefined,
        engine_max_tile_slot: engineMaxTileSlot,
      }),
    onSuccess: (res) => {
      setSlotTaken(null);
      setLastResult(
        `Added ${res.filename} to tileset ${res.dest_tileset} at slot ${res.slot}` +
          (res.jsd_copied ? " (with .jsd)" : "") +
          (res.slot !== srcSlot
            ? " — note: a different slot changes the tile's type/behavior."
            : ""),
      );
      invalidateDest();
    },
    onError: (err) => {
      const msg = err instanceof Error ? err.message : String(err);
      // The lib throws Error with the JSON detail embedded; detect the
      // SLOT_TAKEN code so we can offer the auto-pick recovery.
      if (msg.includes("SLOT_TAKEN")) {
        setSlotTaken(
          occupant
            ? `Slot ${targetSlot} in tileset ${destTileset} is taken by ${occupant.sti_filename}.`
            : `Slot ${targetSlot} in tileset ${destTileset} is already taken.`,
        );
      } else {
        setSlotTaken(null);
      }
    },
  });

  const aboveCap = targetSlot > engineMaxTileSlot;

  return (
    <div className="mt-2 rounded border border-gray-800 bg-gray-950/60 p-2">
      <div className="mb-1 text-[10px] uppercase tracking-wider text-gray-500">
        Add to current tileset
      </div>

      {/* Pre-flight: destination tileset + slot + occupied/cap status. */}
      <div className="mb-2 space-y-1 text-[11px]">
        <div className="flex flex-wrap items-center gap-1 text-gray-300">
          <span>Copies into tileset</span>
          <span className="font-mono text-gray-100">{destTileset}</span>
          <span>at slot</span>
          <span className="font-mono text-gray-100">{targetSlot}</span>
          <span className="text-gray-500">(same as source slot)</span>
        </div>
        {aboveCap ? (
          <div className="rounded border border-red-700/50 bg-red-900/20 px-2 py-1 text-red-200">
            Slot {targetSlot} exceeds the engine cap ({engineMaxTileSlot}).
            The game crashes on sector load referencing a slot above its
            NUMBEROFTILETYPES. Raise the cap in Settings only if your
            ja2.exe is a custom build.
          </div>
        ) : destPalette.isLoading ? (
          <div className="text-gray-500">Checking slot {targetSlot}…</div>
        ) : occupant ? (
          <div className="rounded border border-amber-700/40 bg-amber-900/20 px-2 py-1 text-amber-100">
            Slot {targetSlot} is occupied by{" "}
            <span className="font-mono text-amber-200">{occupant.sti_filename}</span>
            . The copy will be refused — use “auto-pick a free slot” below,
            but note a different slot changes the tile's type/behavior.
          </div>
        ) : (
          <div className="rounded border border-emerald-700/40 bg-emerald-900/20 px-2 py-1 text-emerald-200">
            Slot {targetSlot} is free — the tile will be added here.
          </div>
        )}
        {srcHasJsd && mode === "whole" && (
          <div className="text-[10px] text-gray-500">
            Source has a .jsd (multi-tile struct) — it will be copied too.
          </div>
        )}
      </div>

      {/* Whole-STI vs single-sub (multi-frame sources only). */}
      {multiFrame && (
        <div className="mb-2 flex items-center gap-2 text-[11px]">
          <label className="flex items-center gap-1">
            <input
              type="radio"
              name={`copymode-${srcTileset}-${srcSlot}`}
              checked={mode === "whole"}
              onChange={() => setMode("whole")}
            />
            <span>Whole STI ({srcFrameCount} frames)</span>
          </label>
          <label className="flex items-center gap-1">
            <input
              type="radio"
              name={`copymode-${srcTileset}-${srcSlot}`}
              checked={mode === "sub"}
              onChange={() => setMode("sub")}
            />
            <span>Single sub</span>
          </label>
          {mode === "sub" && (
            <select
              value={subIdx}
              onChange={(e) => setSubIdx(parseInt(e.target.value, 10))}
              title="Which sub-frame to copy as its own single-frame STI"
              className="rounded border border-gray-700 bg-gray-900 px-1 py-0.5 text-[11px]"
            >
              {Array.from({ length: srcFrameCount }, (_, i) => (
                <option key={i} value={i}>
                  sub {i}
                </option>
              ))}
            </select>
          )}
        </div>
      )}

      {/* Action + recovery. */}
      <div className="flex items-center gap-2">
        <button
          type="button"
          disabled={copy.isPending || aboveCap}
          onClick={() => copy.mutate({})}
          title="Copy this tile into the active sector's tileset as a new slot. The paint palette reloads so the tile becomes paintable."
          className="rounded border border-emerald-600 bg-emerald-700 px-3 py-1 text-[11px] font-semibold text-emerald-50 hover:bg-emerald-600 disabled:opacity-50"
        >
          {copy.isPending ? "Adding…" : "Add to current tileset"}
        </button>
        {slotTaken && (
          <button
            type="button"
            disabled={copy.isPending}
            onClick={() => copy.mutate({ autoPick: true })}
            title="Pick the lowest free slot in the destination tileset. WARNING: a different slot puts the tile in a different tile-type family — its flags/layer/behavior change."
            className="rounded border border-amber-600 bg-amber-800/70 px-3 py-1 text-[11px] font-semibold text-amber-50 hover:bg-amber-700 disabled:opacity-50"
          >
            Auto-pick a free slot
          </button>
        )}
      </div>

      {slotTaken && (
        <p className="mt-1 text-[11px] text-amber-300">
          {slotTaken} Auto-pick lands it in a different slot (changes the
          tile's type/behavior).
        </p>
      )}
      {copy.isError && !slotTaken && (
        <p className="mt-1 text-[11px] text-red-400">
          {copy.error instanceof Error ? copy.error.message : String(copy.error)}
        </p>
      )}
      {lastResult && (
        <p className="mt-1 text-[11px] text-emerald-300">{lastResult}</p>
      )}
    </div>
  );
}
