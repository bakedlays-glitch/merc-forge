/**
 * MapForge STI library browser — Phase 4 UI for the
 * `/mapforge/library/*` endpoints.
 *
 * Surfaces the 4000+ unique tile STIs that the Asset Browser has
 * cataloged across the user's ~23 JA2 installs. Filter by tag /
 * substring / dimensions, grid of cached thumbnails, click → detail
 * panel with "Add to current tileset" action.
 *
 * Lives next to MapForgePalette.tsx and switches in via a tab in
 * MapForgeSector.tsx's sidebar.
 */
import { useEffect, useMemo, useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";

import {
  addStiToTileset,
  getLibraryHealth,
  getLibraryStiDetail,
  getLibraryStiThumbBlobUrl,
  getLibrarySubThumbBlobUrl,
  getLibraryTags,
  getTilesetPalette,
  listLibraryStis,
  listLibrarySubs,
  type LibrarySti,
  type LibraryStiDetail,
  type LibrarySub,
  type PaletteSlot,
  type RecentAddition,
} from "../lib/mapforge";

/** Tile-grid cell that fetches its own thumbnail blob URL. Lifecycle:
 * the blob URL is created on mount, revoked on unmount. We don't pool
 * them — each grid cell owns one. */
function ThumbImg({ sha256, alt, size = 56 }: {
  sha256: string;
  alt: string;
  size?: number;
}) {
  const [url, setUrl] = useState<string | null>(null);
  const [err, setErr] = useState(false);
  useEffect(() => {
    let cancelled = false;
    let created: string | null = null;
    setErr(false);
    setUrl(null);
    // Static import at the top of the file — previously this used a
    // dynamic `import("../lib/mapforge")` per cell, which fired one
    // micro-task promise per mounted thumbnail (48 per page) on top of
    // the actual network fetch. The module is already loaded, so the
    // dynamic import resolves trivially but still costs a render
    // round-trip. Direct call cuts the tile-sheet-flicker effect a user
    // saw on every page change.
    getLibraryStiThumbBlobUrl(sha256)
      .then((u) => {
        if (cancelled) { URL.revokeObjectURL(u); return; }
        created = u;
        setUrl(u);
      })
      .catch(() => { if (!cancelled) setErr(true); });
    return () => {
      cancelled = true;
      if (created) URL.revokeObjectURL(created);
    };
  }, [sha256]);
  if (err) {
    return (
      <span
        className="inline-flex items-center justify-center rounded bg-red-950 text-[8px] text-red-400"
        style={{ width: size, height: size }}
        title="thumbnail unavailable"
      >?</span>
    );
  }
  if (!url) {
    return (
      <span
        className="inline-block animate-pulse rounded bg-gray-800"
        style={{ width: size, height: size }}
      />
    );
  }
  return (
    <img
      src={url}
      alt={alt}
      className="block bg-gray-900"
      style={{
        width: size, height: size,
        imageRendering: "pixelated",
        objectFit: "contain",
      }}
    />
  );
}

export function MapForgeLibrary({
  xmlPath, tileset, onAtlasChanged, onAdded, engineMaxTileSlot,
  onPickSha,
}: {
  xmlPath: string;
  tileset: number;
  /** Called after a successful add-to-tileset so the parent can
   * reload the IsoRenderer's atlas. The new slot won't paint
   * anything without this — the IsoRenderer's cellMap is built at
   * session-open and doesn't know about mid-session additions. */
  onAtlasChanged?: () => void;
  /** Called with a structured payload describing what was just added,
   * so the parent can push it onto the rail's "Just added" panel.
   * Distinct from onAtlasChanged (which only signals "atlas dirty,
   * reload"); this carries the metadata the rail needs to render. */
  onAdded?: (addition: RecentAddition) => void;
  /** Cap on slot numbers the user can assign when adding an STI to
   * the tileset. Above this the engine can't reference the slot, so
   * we clamp the input + reject the form on submit. */
  engineMaxTileSlot: number;
  /** When set, REPLACES the default add-modal behavior on library
   * grid clicks. The library becomes a pure picker: clicks fire
   * onPickSha(sha256) and the parent decides what to do (e.g. open
   * an inject-sub modal). Used by the Tileset Editor's slot-inject
   * flow. */
  onPickSha?: (sha256: string, sti_filename: string) => void;
}) {
  const qc = useQueryClient();
  const [filter, setFilter] = useState("");
  const [tag, setTag] = useState<string>("");
  const [page, setPage] = useState(1);
  const [selectedSha, setSelectedSha] = useState<string | null>(null);

  // Reset page when filter or tag changes — we'd otherwise stay on
  // page N of a smaller result set.
  useEffect(() => { setPage(1); }, [filter, tag]);

  const health = useQuery({
    queryKey: ["mapforge", "library", "health"],
    queryFn: getLibraryHealth,
    staleTime: 5 * 60 * 1000,
  });

  const tags = useQuery({
    queryKey: ["mapforge", "library", "tags"],
    queryFn: getLibraryTags,
    enabled: health.data?.available === true,
    staleTime: 60 * 60 * 1000,
  });

  const list = useQuery({
    queryKey: ["mapforge", "library", "stis",
                { filter, tag, page, xmlPath, tileset }],
    queryFn: () => listLibraryStis({
      page,
      per_page: 48,
      q: filter.trim() || undefined,
      tag: tag || undefined,
      xml: xmlPath,
      tileset,
    }),
    enabled: health.data?.available === true,
    staleTime: 30 * 1000,
  });

  if (health.isLoading) {
    return (
      <div className="flex h-full items-center justify-center p-4 text-xs text-gray-500">
        Checking library…
      </div>
    );
  }
  if (health.data && !health.data.available) {
    return (
      <div className="p-3 text-xs text-amber-300">
        <p className="font-semibold">Asset library not available</p>
        <p className="mt-1 text-amber-400/80">
          {health.data.message ?? "Catalog missing"}
        </p>
        <p className="mt-2 text-[10px] text-gray-500">
          Expected at: <code>{health.data.catalog_path}</code>
        </p>
        <p className="mt-1 text-[10px] text-gray-500">
          Run the Wasteland Asset_Browser scan to build the catalog.
        </p>
      </div>
    );
  }

  const totalPages = list.data
    ? Math.max(1, Math.ceil(list.data.total / list.data.per_page))
    : 1;

  return (
    <div className="flex h-full flex-col">
      {/* Filters */}
      <div className="border-b border-gray-800 p-2">
        <input
          type="search"
          value={filter}
          onChange={(e) => setFilter(e.target.value)}
          placeholder={`Search ${health.data?.sti_count ?? "?"} cataloged STIs…`}
          className="w-full rounded border border-gray-700 bg-gray-900 px-2 py-1 text-xs"
        />
        <div className="mt-1.5 flex items-center gap-1.5">
          <label className="text-[10px] text-gray-500">Tag</label>
          <select
            value={tag}
            onChange={(e) => setTag(e.target.value)}
            className="flex-1 rounded border border-gray-700 bg-gray-900 px-1.5 py-0.5 text-[11px]"
          >
            <option value="">— any —</option>
            {tags.data?.map((t) => (
              <option key={t.name} value={t.name}>
                {t.name} ({t.subframe_count.toLocaleString()})
              </option>
            ))}
          </select>
          {(filter || tag) && (
            <button
              type="button"
              onClick={() => { setFilter(""); setTag(""); }}
              className="rounded border border-gray-700 bg-gray-900 px-2 py-0.5 text-[10px] text-gray-400 hover:border-gray-500"
              title="Clear all filters"
            >
              ✕ Clear
            </button>
          )}
        </div>
        {list.data && (
          <div className="mt-1.5 flex items-center justify-between text-[10px] text-gray-500">
            <span>
              {list.data.total.toLocaleString()} STI
              {list.data.total === 1 ? "" : "s"}
              {list.data.total > list.data.per_page &&
                ` · page ${page}/${totalPages}`}
            </span>
            {totalPages > 1 && (
              <div className="flex items-center gap-1">
                <button
                  type="button"
                  disabled={page <= 1}
                  onClick={() => setPage((p) => Math.max(1, p - 1))}
                  title="Previous page of catalog results"
                  className="rounded border border-gray-700 bg-gray-900 px-1.5 hover:border-gray-500 disabled:opacity-30"
                >
                  ‹
                </button>
                <button
                  type="button"
                  disabled={page >= totalPages}
                  onClick={() => setPage((p) => Math.min(totalPages, p + 1))}
                  title="Next page of catalog results"
                  className="rounded border border-gray-700 bg-gray-900 px-1.5 hover:border-gray-500 disabled:opacity-30"
                >
                  ›
                </button>
              </div>
            )}
          </div>
        )}
      </div>

      {/* Grid */}
      <div className="flex-1 overflow-y-auto p-1">
        {list.isLoading && (
          <p className="p-2 text-xs text-gray-500">
            <span className="mr-1 inline-block h-2 w-2 animate-pulse rounded-full bg-blue-400" />
            Querying catalog…
          </p>
        )}
        {list.error && (
          <p className="p-2 text-xs text-red-400">
            {list.error instanceof Error ? list.error.message : String(list.error)}
          </p>
        )}
        {list.data && list.data.items.length === 0 && (
          <p className="p-2 text-xs text-gray-500">
            No STIs match this filter.
          </p>
        )}
        {list.data && (
          <div className="grid grid-cols-3 gap-1">
            {list.data.items.map((sti) => (
              <button
                key={sti.sha256}
                type="button"
                onClick={() => {
                  if (onPickSha) {
                    onPickSha(sti.sha256, sti.name);
                  } else {
                    setSelectedSha(sti.sha256);
                  }
                }}
                className={`flex flex-col items-center rounded border p-1 text-[9px] hover:bg-gray-800 ${
                  selectedSha === sti.sha256
                    ? "border-blue-500 bg-blue-950/50"
                    : sti.in_current_tileset
                      ? "border-emerald-700 bg-emerald-950/30"
                      : "border-gray-700 bg-gray-900"
                }`}
                title={`${sti.name} — ${sti.frame_count ?? "?"} frames · ${sti.install_count} installs${sti.has_jsd ? " · multi-tile" : ""}${sti.in_current_tileset ? "\nAlready in this tileset" : ""}`}
              >
                <ThumbImg sha256={sti.sha256} alt={sti.name} size={48} />
                <div
                  className="mt-0.5 w-full truncate text-gray-400"
                  style={{ maxWidth: 56 }}
                >
                  {sti.name.replace(/\.sti$/i, "")}
                </div>
                {sti.in_current_tileset && (
                  <div className="text-[8px] text-emerald-400">✓ in tileset</div>
                )}
              </button>
            ))}
          </div>
        )}
      </div>

      {/* Modal dialog for the selected sha. Lives outside the sidebar
          flex layout because the inline-at-bottom version was easy to
          miss — the user clicked a thumbnail and the only feedback
          was a panel appearing 800px below their cursor. The modal
          centers in the viewport and dims the background so the
          "Add foo.sti to tileset N?" choice is unmissable. */}
      {selectedSha && (
        <AddStiToTilesetModal
          sha256={selectedSha}
          xmlPath={xmlPath}
          tileset={tileset}
          engineMaxTileSlot={engineMaxTileSlot}
          onClose={() => setSelectedSha(null)}
          onAdded={(addition) => {
            // Refetch palette + list so the new entry shows up + the
            // "in tileset" badge flips on the source row. Both queries
            // are tagged so invalidating by prefix catches them.
            qc.invalidateQueries({ queryKey: ["mapforge", "palette"] });
            qc.invalidateQueries({ queryKey: ["mapforge", "palette-sheet-meta"] });
            qc.invalidateQueries({ queryKey: ["mapforge", "library", "stis"] });
            // Tell the parent to reload the IsoRenderer's atlas — the
            // new (slot, sub) cells aren't in its cellMap yet, so
            // paint clicks would silently no-op.
            onAtlasChanged?.();
            // Forward the structured addition payload to the sector
            // orchestrator so the rail's "Just added" panel can append it.
            onAdded?.(addition);
            // Don't close the modal here — let the success state
            // render inside it so the user sees confirmation. Modal
            // owns its own close timing.
          }}
        />
      )}
    </div>
  );
}

/** Centered modal dialog for adding a library STI to the current
 * tileset. Replaces the previous bottom-of-sidebar panel (which the
 * user reported as easy to miss). Renders preview + metadata at the
 * top, slot/filename inputs in the middle, and a clear Cancel/Add
 * pair at the bottom. After a successful add, displays a confirmation
 * with the assigned slot + filename and auto-closes after 2 seconds
 * (or the user can dismiss manually).
 *
 * Exported so MapForgeSector can render it as a standalone modal
 * from the rail's "Just added" cards' View subs chip (Phase 3) — that
 * path opens the modal targeted at a known sha with importMode
 * pre-set to "subs". */
export function AddStiToTilesetModal({
  sha256, xmlPath, tileset, engineMaxTileSlot, onClose, onAdded,
  initialMode = "whole",
}: {
  sha256: string;
  xmlPath: string;
  tileset: number;
  /** Cap on which slot numbers the user may assign. Above this the
   * engine can't reference the slot — see MapForgePalette for the
   * full rationale. */
  engineMaxTileSlot: number;
  onClose: () => void;
  /** Fires once on a successful add, with enough metadata to populate
   * a RecentAddition card. Combines the backend's slot/filename
   * result with the prefetched detail (frame_count, has_jsd). */
  onAdded: (addition: RecentAddition) => void;
  /** Initial value for the whole-vs-subs import-mode toggle. Library
   * grid clicks default to "whole" (most common case); the
   * "View subs" chip on a RecentAdditionCard opens with "subs"
   * pre-selected so the sub picker is visible immediately. */
  initialMode?: "whole" | "subs";
}) {
  const detail = useQuery({
    queryKey: ["mapforge", "library", "sti", sha256],
    queryFn: () => getLibraryStiDetail(sha256),
    staleTime: 5 * 60 * 1000,
  });
  // Current tileset palette — used to surface "what's currently at
  // slot N" when the user types a slot number, so they don't unknowingly
  // try to replace an existing entry. User feedback: "i dont know
  // what slot 10 here is, the navigation needs to be improved for that".
  const palette = useQuery({
    queryKey: ["mapforge", "palette", xmlPath, tileset],
    queryFn: () => getTilesetPalette(xmlPath, tileset),
    enabled: !!xmlPath && tileset >= 0,
    staleTime: 5 * 60 * 1000,
  });
  // slot → palette entry lookup for O(1) occupant resolution.
  const paletteBySlot = useMemo(() => {
    const m = new Map<number, PaletteSlot>();
    for (const s of palette.data?.slots ?? []) m.set(s.slot, s);
    return m;
  }, [palette.data]);
  const [customSlot, setCustomSlot] = useState<string>("");
  const [customFilename, setCustomFilename] = useState<string>("");
  // Inline error for client-side rejection (e.g. slot above engine
  // cap). Cleared when the user successfully submits or edits the
  // slot input. Separate from `add.error` which captures BACKEND
  // failures.
  const [slotError, setSlotError] = useState<string | null>(null);
  const [success, setSuccess] = useState<{ slot: number; filename: string } | null>(null);
  const [thumbUrl, setThumbUrl] = useState<string | null>(null);
  // ─── Phase 3: import-mode toggle ─────────────────────────────────
  // "whole" copies the source STI verbatim into one slot (today's
  // behavior). "subs" lets the user multi-select individual frames;
  // each selected sub becomes its own slot via the backend's
  // single-sub extractor. Default depends on entry point: library-grid
  // clicks default to "whole" (most common case), "View subs" on a
  // RecentAdditionCard opens with "subs" pre-selected. User request:
  // "import an indiviudal or all subframes when you are importing"
  const [importMode, setImportMode] = useState<"whole" | "subs">(initialMode);
  const [selectedSubs, setSelectedSubs] = useState<Set<number>>(new Set());
  // Multi-add progress: when importing N subs we fire N sequential
  // POSTs (no batch endpoint yet). Track which one we're on so the
  // button can show "Adding 3/7…" instead of just spinning.
  const [subProgress, setSubProgress] = useState<{ done: number; total: number } | null>(null);
  // Load a preview thumbnail for the modal so the user can see what
  // they're adding without scrolling back to the grid.
  useEffect(() => {
    let cancelled = false;
    let created: string | null = null;
    import("../lib/mapforge").then(({ getLibraryStiThumbBlobUrl }) =>
      getLibraryStiThumbBlobUrl(sha256))
      .then((u) => {
        if (cancelled) { URL.revokeObjectURL(u); return; }
        created = u;
        setThumbUrl(u);
      })
      .catch(() => {});
    return () => {
      cancelled = true;
      if (created) URL.revokeObjectURL(created);
    };
  }, [sha256]);
  // Close-on-Escape — standard modal UX.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);
  const add = useMutation({
    mutationFn: (body: { target_slot?: number; target_filename?: string }) =>
      // Pass the engine cap on every add — the backend now refuses
      // auto-picks that would land above the cap (was silently picking
      // slot 200+ in tilesets with cap 150 → CTD on sector load, a user
      // report). We do NOT forward allow_above_cap from here;
      // that escape hatch is only reachable via the explicit
      // recovery CTA after a NO_FREE_SLOT_UNDER_CAP error.
      addStiToTileset(sha256, {
        tileset,
        engine_max_tile_slot: engineMaxTileSlot,
        ...body,
      }),
    onSuccess: (res) => {
      setSuccess({ slot: res.slot, filename: res.filename });
      // Build the structured addition record. detail.data is reliably
      // present here — the success callback fires AFTER the user
      // clicked the Add button, which is itself only mounted once
      // detail loaded. Falling back to nulls keeps types honest.
      onAdded({
        sha256,
        sti_filename: res.filename,
        slot: res.slot,
        tileset: res.tileset,
        added_at: Date.now(),
        frame_count: detail.data?.frame_count ?? null,
        has_jsd: detail.data?.has_jsd ?? false,
      });
      // Auto-close the modal after 2 s so the workflow flows back
      // into the editor. User can also dismiss manually.
      window.setTimeout(() => onClose(), 2000);
    },
  });

  return (
    // Fixed full-screen overlay; backdrop click dismisses (matches
    // typical modal UX). z-index needs to clear the editor's
    // top-of-canvas indicators (z-10/20) — 50 is plenty.
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm"
      onClick={onClose}
      role="dialog"
      aria-modal="true"
    >
      <div
        className="w-[28rem] max-w-[90vw] rounded-lg border border-emerald-700 bg-gray-950 shadow-2xl"
        onClick={(e) => e.stopPropagation()}  // don't close on inner clicks
      >
        {detail.isLoading && (
          <p className="p-4 text-sm text-gray-500">Loading detail…</p>
        )}
        {detail.error && (
          <p className="p-4 text-sm text-red-400">
            {detail.error instanceof Error ? detail.error.message : String(detail.error)}
          </p>
        )}
        {detail.data && (() => {
          const d: LibraryStiDetail = detail.data;
          const sampleName = d.occurrences[0]?.relpath
            .replace(/\\/g, "/").split("/").pop() ?? "asset.sti";
          return (
            <div>
              {/* Header */}
              <div className="flex items-start justify-between gap-3 border-b border-gray-800 p-3">
                <div className="min-w-0">
                  <h3 className="font-semibold text-sm text-emerald-200">
                    Add to tileset {tileset}?
                  </h3>
                  <p className="mt-0.5 truncate font-mono text-xs text-gray-300">
                    {sampleName}
                  </p>
                </div>
                <button
                  type="button"
                  onClick={onClose}
                  className="text-gray-500 hover:text-gray-200"
                  title="Cancel (Esc)"
                >
                  ✕
                </button>
              </div>

              {/* Preview + metadata */}
              <div className="flex gap-3 p-3">
                {thumbUrl ? (
                  <img
                    src={thumbUrl}
                    alt={sampleName}
                    className="block rounded border border-gray-800 bg-gray-900"
                    style={{
                      width: 96, height: 96,
                      imageRendering: "pixelated",
                      objectFit: "contain",
                    }}
                  />
                ) : (
                  <span className="inline-block h-24 w-24 rounded bg-gray-800 animate-pulse" />
                )}
                <div className="flex-1 space-y-1 text-xs text-gray-400">
                  <div>
                    <span className="text-gray-500">size:</span>{" "}
                    {d.width}×{d.height}px · {(d.size_bytes / 1024).toFixed(1)} KB
                  </div>
                  <div>
                    <span className="text-gray-500">frames:</span>{" "}
                    {d.frame_count ?? "?"}
                  </div>
                  {d.has_jsd && (
                    <div className="text-amber-300">
                      + .jsd companion (multi-tile struct)
                    </div>
                  )}
                  {d.tags.length > 0 && (
                    <div className="flex flex-wrap gap-1 pt-0.5">
                      {d.tags.map((t) => (
                        <span key={t} className="rounded bg-gray-800 px-1.5 py-0.5 text-[10px] text-gray-300">
                          {t}
                        </span>
                      ))}
                    </div>
                  )}
                  <details className="pt-0.5 text-[10px] text-gray-500">
                    <summary className="cursor-pointer hover:text-gray-300">
                      Found in {d.occurrences.length} install
                      {d.occurrences.length === 1 ? "" : "s"}
                    </summary>
                    <ul className="mt-1 max-h-24 overflow-y-auto pl-3">
                      {d.occurrences.slice(0, 8).map((o, i) => (
                        <li key={i} className="truncate" title={`${o.install_label}: ${o.relpath}`}>
                          {o.install_label}{o.is_in_slf && " (SLF)"}
                        </li>
                      ))}
                      {d.occurrences.length > 8 && (
                        <li className="text-gray-600">+ {d.occurrences.length - 8} more</li>
                      )}
                    </ul>
                  </details>
                </div>
              </div>

              {/* Form / success / footer */}
              {success ? (
                <div className="p-3">
                  <div className="rounded bg-emerald-950/60 border border-emerald-700 px-3 py-2 text-sm text-emerald-100">
                    ✓ Added as slot{" "}
                    <span className="font-mono font-bold">{success.slot}</span>{" "}
                    <span className="text-emerald-300">({success.filename})</span>
                  </div>
                  <p className="mt-2 text-[10px] text-gray-500">
                    Atlas is reloading… the new slot will appear in the
                    "In Tileset" tab momentarily. This dialog closes
                    automatically.
                  </p>
                </div>
              ) : (
                <form
                  className="border-t border-gray-800 p-3"
                  onSubmit={async (e) => {
                    e.preventDefault();
                    const slotNum = customSlot.trim()
                      ? parseInt(customSlot, 10) : undefined;
                    // Refuse out-of-engine-range manual slot picks
                    // BEFORE round-tripping to the backend. The XML
                    // would accept them but the engine would crash on
                    // sector load.
                    if (Number.isFinite(slotNum) && (slotNum as number) > engineMaxTileSlot) {
                      setSlotError(
                        `Slot ${slotNum} exceeds engine cap ${engineMaxTileSlot}. `
                        + "The engine can't load slots above its compiled NUMBEROFTILETYPES; "
                        + "pick a slot ≤ cap, or raise the cap in Settings if your ja2.exe supports more."
                      );
                      return;
                    }
                    setSlotError(null);
                    const fn = customFilename.trim() || undefined;

                    if (importMode === "whole") {
                      // Whole-STI path — single backend POST.
                      add.mutate({
                        target_slot: Number.isFinite(slotNum) ? slotNum : undefined,
                        target_filename: fn,
                      });
                      return;
                    }

                    // Multi-sub path — fire one POST per selected sub.
                    // No batch endpoint yet (see ASSET_BROWSER_PLAN.md
                    // for future work). Sequential so the backend's
                    // auto-pick allocator sees each one's effect on the
                    // tileset before picking the next slot.
                    const subs = Array.from(selectedSubs).sort((a, b) => a - b);
                    if (subs.length === 0) {
                      setSlotError("Pick at least one sub-frame to import.");
                      return;
                    }
                    if (Number.isFinite(slotNum) && subs.length > 1) {
                      setSlotError(
                        "Manual slot picks only support importing one sub at a time. "
                        + "Leave Slot blank to auto-allocate, or import the subs one-by-one."
                      );
                      return;
                    }
                    setSubProgress({ done: 0, total: subs.length });
                    let lastRes: { slot: number; filename: string } | null = null;
                    for (let i = 0; i < subs.length; i++) {
                      try {
                        const res = await addStiToTileset(sha256, {
                          tileset,
                          engine_max_tile_slot: engineMaxTileSlot,
                          target_sub: subs[i],
                          // Only forward target_slot for the (rare)
                          // single-sub manual case; multi-sub always
                          // auto-picks.
                          target_slot: (subs.length === 1 && Number.isFinite(slotNum))
                            ? (slotNum as number)
                            : undefined,
                          target_filename: fn,
                        });
                        lastRes = { slot: res.slot, filename: res.filename };
                        setSubProgress({ done: i + 1, total: subs.length });
                        // Fire the per-add onSuccess hook (atlas reload
                        // + recent-additions push) for each sub so the
                        // rail stays in sync as the loop progresses.
                        onAdded({
                          sha256,
                          sti_filename: res.filename,
                          slot: res.slot,
                          tileset: res.tileset,
                          added_at: Date.now(),
                          frame_count: 1,  // single-sub extracts are 1-frame
                          has_jsd: false,
                        });
                      } catch (err) {
                        // Stop on first failure; the user sees what
                        // got through plus the failure reason.
                        setSubProgress(null);
                        setSlotError(
                          `Sub ${subs[i]} failed: `
                          + (err instanceof Error ? err.message : String(err))
                          + ` (${i} of ${subs.length} imported before the error)`,
                        );
                        return;
                      }
                    }
                    setSubProgress(null);
                    if (lastRes) {
                      // Show last result in success banner; total count
                      // is on the secondary line via subProgress final
                      // state's total.
                      setSuccess(lastRes);
                      window.setTimeout(() => onClose(), 2000);
                    }
                  }}
                >
                  {/* Phase 3 import-mode toggle — radios are visually
                      louder than a dropdown and the two options are
                      mutually exclusive. Disable the "Pick subs" radio
                      when the source has 1 frame (nothing to pick). */}
                  <div className="mb-3 flex items-center gap-3 rounded border border-gray-800 bg-gray-900/40 p-2 text-[11px]">
                    <span className="text-gray-500">Import:</span>
                    <label className="flex cursor-pointer items-center gap-1 text-gray-300">
                      <input
                        type="radio"
                        name="import-mode"
                        checked={importMode === "whole"}
                        onChange={() => setImportMode("whole")}
                        className="accent-emerald-500"
                      />
                      Whole STI ({d.frame_count ?? "?"} frames)
                    </label>
                    <label
                      className={`flex items-center gap-1 ${
                        (d.frame_count ?? 0) > 1
                          ? "cursor-pointer text-gray-300"
                          : "cursor-not-allowed text-gray-600"
                      }`}
                      title={
                        (d.frame_count ?? 0) > 1
                          ? "Pick specific sub-frames to import — each becomes its own slot."
                          : "Source has 1 frame; nothing to pick."
                      }
                    >
                      <input
                        type="radio"
                        name="import-mode"
                        checked={importMode === "subs"}
                        onChange={() => setImportMode("subs")}
                        disabled={(d.frame_count ?? 0) <= 1}
                        className="accent-emerald-500"
                      />
                      Pick subs
                    </label>
                  </div>

                  {/* Sub-grid — visible only in "subs" mode. Multi-select
                      via click; selection count surfaced under the grid. */}
                  {importMode === "subs" && (
                    <div className="mb-3">
                      <SubframeGrid
                        stiSha256={sha256}
                        selected={selectedSubs}
                        onToggle={(idx) => {
                          setSelectedSubs((prev) => {
                            const next = new Set(prev);
                            if (next.has(idx)) next.delete(idx);
                            else next.add(idx);
                            return next;
                          });
                        }}
                      />
                      <div className="mt-1.5 flex items-center justify-between text-[10px] text-gray-500">
                        <span>
                          {selectedSubs.size} selected
                          {selectedSubs.size > 0 && " — each lands in its own slot"}
                        </span>
                        {selectedSubs.size > 0 && (
                          <button
                            type="button"
                            onClick={() => setSelectedSubs(new Set())}
                            className="rounded border border-gray-700 px-1.5 py-0.5 hover:border-gray-500"
                          >
                            Clear
                          </button>
                        )}
                      </div>
                    </div>
                  )}

                  <div className="mb-2 grid grid-cols-2 gap-2">
                    <div>
                      <label className="block text-[10px] text-gray-500">
                        Slot (blank = auto-pick free) · max {engineMaxTileSlot}
                      </label>
                      <input
                        type="number"
                        min={0} max={engineMaxTileSlot}
                        value={customSlot}
                        onChange={(e) => setCustomSlot(e.target.value)}
                        placeholder="auto"
                        disabled={importMode === "subs" && selectedSubs.size > 1}
                        title={
                          importMode === "subs" && selectedSubs.size > 1
                            ? "Multi-sub imports auto-pick contiguous slots — clear all but one to use a manual slot."
                            : `Slot index 0–${engineMaxTileSlot}. Higher slots crash the engine on sector load — change the cap in Settings if your ja2.exe supports more.`
                        }
                        className="w-full rounded border border-gray-700 bg-gray-900 px-2 py-1 text-xs disabled:opacity-40"
                      />
                    </div>
                    <div>
                      <label className="block text-[10px] text-gray-500">
                        Save as filename
                      </label>
                      <input
                        type="text"
                        value={customFilename}
                        onChange={(e) => setCustomFilename(e.target.value)}
                        placeholder={sampleName}
                        className="w-full rounded border border-gray-700 bg-gray-900 px-2 py-1 text-xs font-mono"
                      />
                    </div>
                  </div>
                  {/* Slot-occupant preview — tells the user what's
                      ALREADY at the slot they're typing. Empty slot →
                      green "free" tag. Occupied → amber "taken by foo.sti"
                      tag (and the backend rejects replace anyway, so
                      this is purely informational pre-flight). */}
                  <SlotOccupantPreview
                    rawSlot={customSlot}
                    paletteBySlot={paletteBySlot}
                    paletteLoading={palette.isLoading}
                  />
                  {slotError && (
                    <p className="mb-2 rounded bg-red-950/60 px-2 py-1 text-[11px] text-red-300">
                      {slotError}
                    </p>
                  )}
                  {add.error && (
                    <p className="mb-2 rounded bg-red-950/60 px-2 py-1 text-[11px] text-red-300">
                      {add.error instanceof Error ? add.error.message : String(add.error)}
                    </p>
                  )}
                  <div className="flex justify-end gap-2">
                    <button
                      type="button"
                      onClick={onClose}
                      disabled={add.isPending || subProgress !== null}
                      title="Close without adding"
                      className="rounded border border-gray-700 bg-gray-900 px-3 py-1.5 text-xs text-gray-300 hover:border-gray-500 disabled:opacity-50"
                    >
                      Cancel
                    </button>
                    <button
                      type="submit"
                      disabled={
                        add.isPending
                        || subProgress !== null
                        || (importMode === "subs" && selectedSubs.size === 0)
                      }
                      title={
                        importMode === "whole"
                          ? "Copy this STI into the current tileset and register it at the chosen slot. The atlas will reload so painted entries render."
                          : "Re-encode each selected sub as its own single-frame STI and register them in successive slots."
                      }
                      className="rounded border border-emerald-600 bg-emerald-700 px-4 py-1.5 text-xs font-semibold text-emerald-50 hover:bg-emerald-600 disabled:opacity-50"
                    >
                      {subProgress
                        ? `Adding ${subProgress.done}/${subProgress.total}…`
                        : add.isPending
                          ? "Adding…"
                          : importMode === "subs"
                            ? `+ Add ${selectedSubs.size || ""} sub${selectedSubs.size === 1 ? "" : "s"} → tileset ${tileset}`
                            : `+ Add to tileset ${tileset}`}
                    </button>
                  </div>
                </form>
              )}
            </div>
          );
        })()}
      </div>
    </div>
  );
}

/** Re-export so MapForgePalette can call it without circular imports. */
export type { LibrarySti };


/**
 * Inline pre-flight panel for the Slot input in AddStiToTilesetModal.
 * Reads the current tileset palette to tell the user what's at the slot
 * they're typing — empty → "free, will be added here"; occupied →
 * amber warning with the existing filename (backend will refuse the
 * add anyway, but knowing BEFORE submit beats reading a 409 error).
 *
 * No render when:
 *   - The user hasn't typed anything yet (auto-pick mode).
 *   - The typed value isn't a parseable non-negative integer.
 *
 * User feedback: "i dont know what slot 10 here is, the navigation
 * needs to be improved for that".
 */
function SlotOccupantPreview({
  rawSlot, paletteBySlot, paletteLoading,
}: {
  rawSlot: string;
  paletteBySlot: Map<number, PaletteSlot>;
  paletteLoading: boolean;
}) {
  const trimmed = rawSlot.trim();
  if (trimmed === "") return null;
  const slot = parseInt(trimmed, 10);
  if (!Number.isFinite(slot) || slot < 0) return null;

  if (paletteLoading) {
    return (
      <p className="mb-2 text-[10px] text-gray-500">
        Checking slot {slot}…
      </p>
    );
  }

  const occupant = paletteBySlot.get(slot);
  if (!occupant) {
    return (
      <div className="mb-2 flex items-center gap-2 rounded border border-emerald-700/40 bg-emerald-900/20 px-2 py-1 text-[11px] text-emerald-200">
        <span className="font-mono">slot {slot}</span>
        <span>is free — STI will be added here.</span>
      </div>
    );
  }
  // Occupant rendering: STI filename + frame count badge so the user
  // can see what they'd displace. Mirrors the tone of the SLOT_TAKEN
  // 409 message but appears BEFORE submit.
  return (
    <div className="mb-2 rounded border border-amber-700/40 bg-amber-900/20 px-2 py-1 text-[11px] text-amber-100">
      <div className="flex items-center gap-2">
        <span className="font-mono text-amber-300">slot {slot}</span>
        <span>currently has</span>
        <span className="font-mono text-amber-200">{occupant.sti_filename}</span>
        <span className="rounded bg-amber-800/50 px-1 text-[10px] text-amber-200">
          {occupant.frame_count} frame{occupant.frame_count === 1 ? "" : "s"}
        </span>
        {occupant.has_jsd && (
          <span
            className="rounded bg-amber-800/50 px-1 text-[10px] text-amber-200"
            title="Existing slot has a JSD (multi-tile struct profile). Replacing it requires keeping a compatible footprint."
          >
            JSD
          </span>
        )}
      </div>
      <div className="mt-0.5 text-[10px] text-amber-300/80">
        Replacing existing slots isn't supported yet — leave Slot blank
        to auto-pick a free one, or pick a different number.
      </div>
    </div>
  );
}


/** Grid of per-sub thumbnails for an STI. Click toggles selection.
 * Used inside AddStiToTilesetModal's "Pick subs" mode (Phase 3) to
 * let the user pick exactly which frames to import. The catalog
 * already has per-sub PNGs cached (Asset_Browser builds them during
 * its scan); we proxy them through the sidecar so the auth token
 * works across STI + sub thumb URLs from a single origin. */
function SubframeGrid({
  stiSha256, selected, onToggle,
}: {
  stiSha256: string;
  selected: Set<number>;
  onToggle: (sub_idx: number) => void;
}) {
  const subs = useQuery({
    queryKey: ["mapforge", "library", "sti", stiSha256, "subs"],
    queryFn: () => listLibrarySubs(stiSha256),
    staleTime: 5 * 60 * 1000,
  });
  if (subs.isLoading) {
    return (
      <p className="text-[11px] text-gray-500">Loading sub-frames…</p>
    );
  }
  if (subs.error) {
    return (
      <p className="text-[11px] text-red-400">
        {subs.error instanceof Error ? subs.error.message : String(subs.error)}
      </p>
    );
  }
  if (!subs.data || subs.data.subs.length === 0) {
    return (
      <p className="text-[11px] text-gray-500">No sub-frames in catalog.</p>
    );
  }
  return (
    <div className="grid max-h-48 grid-cols-6 gap-1 overflow-y-auto rounded border border-gray-800 bg-gray-900/60 p-1">
      {subs.data.subs.map((s) => (
        <SubThumbCell
          key={`${s.sub_idx}-${s.sha256}`}
          sub={s}
          isSelected={selected.has(s.sub_idx)}
          onClick={() => onToggle(s.sub_idx)}
        />
      ))}
    </div>
  );
}

/** Individual sub-thumbnail tile. Self-fetches its blob URL and
 * revokes on unmount. The visual selection state is a thick emerald
 * border so multi-selects are scannable. */
function SubThumbCell({
  sub, isSelected, onClick,
}: {
  sub: LibrarySub;
  isSelected: boolean;
  onClick: () => void;
}) {
  const [url, setUrl] = useState<string | null>(null);
  const [err, setErr] = useState(false);
  useEffect(() => {
    let cancelled = false;
    let created: string | null = null;
    setErr(false);
    setUrl(null);
    getLibrarySubThumbBlobUrl(sub.sha256)
      .then((u) => {
        if (cancelled) { URL.revokeObjectURL(u); return; }
        created = u;
        setUrl(u);
      })
      .catch(() => { if (!cancelled) setErr(true); });
    return () => {
      cancelled = true;
      if (created) URL.revokeObjectURL(created);
    };
  }, [sub.sha256]);

  return (
    <button
      type="button"
      onClick={onClick}
      title={
        `sub ${sub.sub_idx} · ${sub.width}×${sub.height}`
        + (sub.tags.length ? `\ntags: ${sub.tags.join(", ")}` : "")
      }
      className={`flex flex-col items-center rounded border p-0.5 ${
        isSelected
          ? "border-emerald-500 bg-emerald-950/60"
          : "border-gray-700 bg-gray-900 hover:border-gray-500"
      }`}
    >
      {err ? (
        <span className="inline-flex h-10 w-10 items-center justify-center rounded bg-red-950 text-[8px] text-red-400">
          ?
        </span>
      ) : url ? (
        <img
          src={url}
          alt={`sub ${sub.sub_idx}`}
          className="block h-10 w-10 bg-gray-950"
          style={{ imageRendering: "pixelated", objectFit: "contain" }}
        />
      ) : (
        <span className="inline-block h-10 w-10 animate-pulse rounded bg-gray-800" />
      )}
      <span className="mt-0.5 text-[8px] text-gray-500">{sub.sub_idx}</span>
    </button>
  );
}
