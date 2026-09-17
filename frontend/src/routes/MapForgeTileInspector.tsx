// Tile inspector cluster + load progress bar, extracted verbatim from
// MapForgeSector.tsx (first slice of the god-file split). No behavior
// change — imports below re-supply what the parent module provided.

import { useEffect, useMemo, useRef, useState } from "react";
import type { ReactNode } from "react";
import { useQuery } from "@tanstack/react-query";

import {
  applyEdits,
  fetchStiFrameBlobUrl,
  getStiJsd,
  type LayerName,
  type PaletteSlot,
  type SessionEdit,
  type SessionInfo,
  type TileInspection,
} from "../lib/mapforge";
import {
  IsoRenderer,
  PROGRESS_PHASE_LABELS,
  type ProgressPhase,
} from "../lib/IsoRenderer";
import { useDialog } from "../components/DialogProvider";
import { propFrameLabel } from "../lib/mapforgePropLabels";


/** Per-flag tooltip text for the JSD viewer's flag chips. Mirrors the
 *  bit definitions in JA2 1.13's worlddat.h. Keep in sync with the
 *  backend's `flag_names` decoder — if a new flag bit gets surfaced
 *  there but isn't named here, the chip falls back to "(no description
 *  available)". */
function _jsdFlagTooltip(flag: string): string {
  const table: Record<string, string> = {
    TILE_ON_ROOF: "Renders on the upper floor (roof level) — appears only when the user is on or peering at the roof.",
    HAS_SHADOW_BUDDY: "Slot has a paired shadow sprite at slot+1; engine auto-draws both.",
    DAMAGED: "Marks the struct as the damaged variant — used for ruin / blasted-wall states.",
    EXPLOSIVE: "Triggers an explosion when destroyed (mines, gas tanks, etc.).",
    PARTIAL_WALL: "Half-height or fragmentary wall — engine treats it as cover but not as full sight-block.",
    FULL_WALL: "Full-height wall — blocks line of sight + walking.",
    WIREFRAME: "Drawn in wireframe overlay above other tiles for editor / debug visibility.",
    PASSABLE: "Mercs and projectiles can pass through this struct (vegetation, smoke).",
    EXIT_GRID: "Tile marks a sector boundary or strategic exit point.",
    BLOCKS_LOS: "Hard line-of-sight block — engine treats as an opaque obstacle.",
    OBSTACLE: "Treated as an obstacle for pathfinding even when visually subtle (rope, low fence).",
    SLIDING_DOOR: "Door variant — slides open horizontally rather than swinging.",
    DOOR: "Engine recognizes this as an openable/closeable door.",
    OPENABLE: "Tile responds to the 'open' action (containers, hatches).",
    SEETHROUGH: "Visible-through tile — engine renders behind it but treats as light cover.",
    BURNABLE: "Catches fire when exposed to flame attacks.",
    TALL_OBJECT: "Renders with a height lift so it occludes tiles to the south correctly.",
    STRUCTURE: "Solid structural piece (walls, big rocks) — engine snaps shadows + LOS to it.",
    GENERIC: "Default flag with no special engine behavior — usually surface decoration.",
  };
  return table[flag] ?? `${flag} — engine flag; no description in our table yet.`;
}

/**
 * Atlas-backed thumb — renders a single (slot, sub) sprite from the
 * already-loaded IsoRenderer atlas image. Zero HTTP, ~50 microseconds
 * per render (one ctx.drawImage call). Used for in-tileset entries
 * where the renderer's cellMap has the data.
 *
 * Falls back to nothing (placeholder) when the (slot, sub) isn't in
 * the cellMap. The caller can use StiFrameImage as a fallback for
 * arbitrary slot/sub the renderer doesn't know about (e.g., a slot
 * the user is typing into the edit form).
 */
export function AtlasFrameThumb({
  renderer, slot, sub, size = 48, zoom = false, className, fallback,
}: {
  renderer: IsoRenderer | null;
  slot: number;
  sub: number;
  size?: number;
  /** Display at twice the sampled size for a larger pixel-art view. */
  zoom?: boolean;
  className?: string;
  /** Rendered instead of the "?" placeholder when the (slot, sub) isn't
   * in the atlas — lets callers supply e.g. an HTTP StiFrameImage
   * without running their own presence probe. */
  fallback?: ReactNode;
}) {
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const [missing, setMissing] = useState(false);
  const renderSize = zoom ? Math.ceil(size / 2) : size;
  useEffect(() => {
    if (!renderer || !canvasRef.current) return;
    const ctx = canvasRef.current.getContext("2d");
    if (!ctx) return;
    const ok = renderer.drawCellInto(ctx, slot, sub, renderSize, renderSize);
    setMissing(!ok);
  }, [renderer, slot, sub, renderSize]);
  // The canvas stays mounted (hidden while missing) so the ref survives
  // and the effect can re-probe when slot/sub change — unmounting it
  // left `missing` stuck true forever after one absent pair.
  return (
    <>
      <canvas
        ref={canvasRef}
        width={renderSize}
        height={renderSize}
        className={`inline-block bg-gray-900 ${className ?? ""}`}
        style={{
          imageRendering: "pixelated",
          width: size,
          height: size,
          display: missing ? "none" : undefined,
        }}
      />
      {missing && (fallback !== undefined ? fallback : (
        <span
          className={`inline-flex items-center justify-center rounded bg-gray-800 text-[8px] text-gray-500 ${className ?? ""}`}
          style={{ width: size, height: size }}
          title={`slot ${slot} sub ${sub} — not in atlas`}
        >?</span>
      ))}
    </>
  );
}

/**
 * Render one STI sub-frame as an inline <img>. Handles the
 * authedFetch → blob → object URL → revoke lifecycle so the parent
 * tree doesn't have to. Re-fetches when (xmlPath, tileset, slot, sub)
 * change. Used in the edit form (live preview of the proposed
 * slot/sub which may not yet be in the loaded atlas).
 *
 * For inspector entry previews — where the slot/sub IS in the atlas
 * — prefer AtlasFrameThumb above. Zero HTTP, instant render.
 */
function StiFrameImage({
  xmlPath, tileset, slot, sub, maxSize = 48, zoom = false, className,
}: {
  xmlPath: string;
  tileset: number;
  slot: number;
  sub: number;
  maxSize?: number;
  zoom?: boolean;
  className?: string;
}) {
  const [url, setUrl] = useState<string | null>(null);
  const [err, setErr] = useState(false);
  useEffect(() => {
    if (!xmlPath) { setUrl(null); return; }
    let cancelled = false;
    let created: string | null = null;
    setErr(false);
    fetchStiFrameBlobUrl(xmlPath, tileset, slot, sub)
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
  }, [xmlPath, tileset, slot, sub]);
  if (err) {
    return (
      <span
        className={`inline-block text-[8px] text-red-400 ${className ?? ""}`}
        style={{ width: maxSize, height: maxSize, lineHeight: `${maxSize}px`, textAlign: "center" }}
        title={`No frame for slot ${slot} sub ${sub}`}
      >?</span>
    );
  }
  if (!url) {
    return (
      <span
        className={`inline-block animate-pulse rounded bg-gray-800 ${className ?? ""}`}
        style={{ width: maxSize, height: maxSize }}
      />
    );
  }
  return (
    <img
      src={url}
      alt={`slot ${slot} sub ${sub}`}
      className={`inline-block bg-gray-900 ${className ?? ""}`}
      style={{
        width: zoom ? maxSize : undefined,
        height: zoom ? maxSize : undefined,
        maxWidth: maxSize, maxHeight: maxSize,
        imageRendering: "pixelated",
        objectFit: "contain",
      }}
    />
  );
}

// Default-export wrapper that mounts the log provider. The inner
// `MapForgeSectorInner` is what owns all the state + effects; that
// keeps `useMapForgeLog()` callable anywhere in the tree without
// having to wire props through.

export function TileInspectorPanel({
  xmlPath, tileset, session, renderer, propSlots, renderEpoch, isSlfBundled,
  cols, rows, pinned, onPin, onEditApplied, onPickAsBrush,
}: {
  xmlPath: string;
  tileset: number;
  session: SessionInfo | null;
  renderer: IsoRenderer | null;
  propSlots: ReadonlyMap<number, PaletteSlot>;
  renderEpoch: number;
  isSlfBundled: boolean;
  cols: number;
  rows: number;
  pinned: { x: number; y: number } | null;
  onPin: (p: { x: number; y: number } | null) => void;
  onEditApplied: (updatedSession: SessionInfo) => void;
  /** Click on an entry's thumbnail to load it as the active brush.
   * Lets the user pick a specific layer's entry from a multi-layer
   * tile — the right-click eyedropper only picks the topmost.
   */
  onPickAsBrush: (slot: number, sub: number, layer: LayerName, sti_filename: string) => void;
}) {
  // Local inspect — reads straight from the renderer's parsed dict so
  // uncommitted edits show up immediately. `renderEpoch` is in the
  // dependency list so a paint stroke that touches the pinned tile
  // refreshes the inspector without an HTTP fetch.
  const inspectionData: TileInspection | null = useMemo(() => {
    if (!pinned || !renderer) return null;
    return renderer.inspectTile(pinned.x, pinned.y);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [pinned?.x, pinned?.y, renderer, renderEpoch]);

  return (
    <div className="rounded border border-gray-700 bg-gray-950 p-3">
      <h2 className="mb-2 text-sm font-semibold text-gray-300">Tile Inspector</h2>
      {/* Uncontrolled inputs, remounted (via key) whenever the pin
          changes so a map click refreshes the fields — replaces the old
          x/y useState mirrored from the `pinned` prop via an effect. */}
      <form
        key={pinned ? `${pinned.x},${pinned.y}` : "unpinned"}
        className="mb-1 flex items-end gap-2"
        onSubmit={(e) => {
          e.preventDefault();
          const fd = new FormData(e.currentTarget);
          onPin({
            x: parseInt(String(fd.get("x")), 10) || 0,
            y: parseInt(String(fd.get("y")), 10) || 0,
          });
        }}
      >
        <div>
          <label className="block text-xs text-gray-400">X (0–{cols - 1})</label>
          <input
            name="x" type="number" min={0} max={cols - 1}
            defaultValue={pinned?.x ?? 0}
            className="w-20 rounded border border-gray-700 bg-gray-900 px-2 py-1 text-sm"
          />
        </div>
        <div>
          <label className="block text-xs text-gray-400">Y (0–{rows - 1})</label>
          <input
            name="y" type="number" min={0} max={rows - 1}
            defaultValue={pinned?.y ?? 0}
            className="w-20 rounded border border-gray-700 bg-gray-900 px-2 py-1 text-sm"
          />
        </div>
      </form>
      <p className="mb-3 text-[10px] text-gray-500">
        Click any tile on the render — or type X/Y and press Enter.
      </p>

      {/* Previously a persistent amber "This sector lives inside an
          SLF archive — editing is disabled" banner sat here. Removed
          per user feedback — every other MapForge status message
          routes through the log panel; the inspector banner ate space
          on every load. The same advisory is now logged on sector open
          (see the isSlfBundled useEffect upstream); the actionable
          "Extract to loose" button still floats over the top-left of
          the canvas. */}

      {pinned && !inspectionData && (
        <p className="text-xs text-gray-400">Loading...</p>
      )}
      {inspectionData && (
        <TileInspectionView
          t={inspectionData}
          xmlPath={xmlPath}
          tileset={tileset}
          session={session}
          renderer={renderer}
          propSlots={propSlots}
          editable={
            !isSlfBundled
            && session !== null
            && !session.read_only
            && renderer !== null
          }
          onEdited={onEditApplied}
          onPickAsBrush={onPickAsBrush}
        />
      )}
    </div>
  );
}

function TileInspectionView({
  t, xmlPath, tileset, session, renderer, propSlots, editable, onEdited, onPickAsBrush,
}: {
  t: TileInspection;
  xmlPath: string;
  tileset: number;
  session: SessionInfo | null;
  renderer: IsoRenderer | null;
  propSlots: ReadonlyMap<number, PaletteSlot>;
  editable: boolean;
  onEdited: (updated: SessionInfo) => void;
  onPickAsBrush: (slot: number, sub: number, layer: LayerName, sti_filename: string) => void;
}) {
  const { confirm } = useDialog();
  // Composite key = "layer:index" of the entry currently being edited
  // (so only one inline form is open at a time).
  const [editingKey, setEditingKey] = useState<string | null>(null);
  const [editError, setEditError] = useState<string | null>(null);
  const [editBusy, setEditBusy] = useState(false);
  // When set, opens the JSD viewer modal for that slot. JSD data is
  // small (~100 bytes) and fetched on demand.
  const [jsdSlot, setJsdSlot] = useState<number | null>(null);

  // When the inspected tile changes (parent reuses this component with
  // new props rather than remounting), the local view-state above is
  // stale — the JSD panel would still show the previous tile's slot,
  // the inline edit form would still be open against an entry index
  // that no longer matches. Reset on every tile change.
  useEffect(() => {
    setJsdSlot(null);
    setEditingKey(null);
    setEditError(null);
  }, [t.x, t.y]);

  async function applyEdit(
    op: "replace" | "remove",
    layer: LayerName,
    entryIdx: number,
    slot?: number,
    sub?: number,
  ) {
    if (!session || !renderer) return;
    setEditBusy(true); setEditError(null);
    // Mirror the paintBrush flow: mutate local first for instant
    // canvas feedback, then send to backend. Routed through the stroke
    // machinery so inspector edits are (a) undoable, (b) invalidate the
    // redo timeline like every other mutation, and (c) count toward
    // dirty tracking — previously an inspector-only session showed
    // "Saved" and refused Ctrl+S, losing the edits on close.
    renderer.beginStroke(`Edit ${layer}[${entryIdx}] (${t.x},${t.y})`);
    renderer.recordSnapshot(t.x, t.y, layer);
    renderer.applyLocalEdit({
      x: t.x, y: t.y, op,
      layer, slot, sub, entryIndex: entryIdx,
    });
    renderer.endStroke();
    try {
      const edit: SessionEdit = {
        x: t.x, y: t.y, op, layer,
        entry_index: entryIdx, slot, sub,
      };
      const res = await applyEdits(session.session_id, [edit]);
      setEditingKey(null);
      onEdited(res.session);
    } catch (e) {
      // Backend rejected — the server session is untouched, so revert
      // the optimistic local mirror and DISCARD the stroke (no redo
      // mirror: Ctrl+Y must not replay a rejected edit).
      const entry = renderer.discardLastUndo();
      if (entry) {
        for (const s of entry.snapshots) {
          renderer.applyLocalEdit({
            x: s.x, y: s.y, op: "set_entries", layer: s.layer, entries: s.entries,
          });
        }
      }
      // Same-session callback so the parent repaints + resyncs the
      // history/dirty UI after the revert.
      onEdited(session);
      setEditError(e instanceof Error ? e.message : String(e));
    } finally {
      setEditBusy(false);
    }
  }

  // Per-layer entry counts — used for the layer-filter dropdown labels
  // ("structs (3)") and to show the user which layers have any data
  // on this tile without expanding.

  return (
    <div className="space-y-3 text-xs">
      <div className="rounded bg-gray-900 p-2 font-mono">
        ({t.x},{t.y}) g={t.gridno} room={t.room_id} height={t.height} flags={t.world_flags}
      </div>

      {/* "Saved" feedback now lives on the top-bar Save button —
          edits go to memory until the user explicitly saves. */}
      {editError && (
        <div className="rounded bg-red-950 px-2 py-1 text-[10px] text-red-300">
          {editError}
        </div>
      )}

      {(["land", "objs", "shadows", "structs", "roofs", "onroofs"] as const).map(
        (layer) => {
          const entries = t.layers[layer] ?? [];
          if (entries.length === 0) return null;
          return (
            <div key={layer}>
              <div className="mb-1 text-xs font-semibold uppercase text-gray-400">
                {layer} ({entries.length})
              </div>
              <ul className="space-y-1">
                {entries.map((e, i) => {
                  const key = `${layer}:${i}`;
                  const editing = editingKey === key;
                  const label = propFrameLabel(e.slot, e.sub, propSlots.get(e.slot), e.sti_filename);
                  return (
                    <li key={i} className="rounded bg-gray-900 px-2 py-1 font-mono text-xs">
                      <div className="flex items-start justify-between gap-2">
                        <div className="flex min-w-0 items-center gap-2">
                          {/* Atlas-backed preview — instant, no HTTP.
                              CLICK to load this entry as the active
                              brush (lets the user pick a specific
                              layer from a multi-layer tile — the
                              right-click eyedropper only grabs the
                              topmost). */}
                          <button
                            type="button"
                            onClick={() => onPickAsBrush(
                              e.slot, e.sub, layer,
                              e.sti_filename ?? `slot ${e.slot}`,
                            )}
                            title={
                              `Load ${label.primary} (${label.technical}; ${label.filename ?? "unknown file"}) `
                              + `as the active brush, painting onto layer ${layer}. `
                              + `Switches the tool to Pencil.`
                            }
                            className="rounded ring-0 hover:ring-2 hover:ring-emerald-500/60 focus:outline-none focus:ring-2 focus:ring-emerald-500"
                          >
                            <AtlasFrameThumb
                              renderer={renderer}
                              slot={e.slot} sub={e.sub} size={88} zoom
                              fallback={<StiFrameImage
                                xmlPath={xmlPath} tileset={tileset}
                                slot={e.slot} sub={e.sub} maxSize={88} zoom
                              />}
                            />
                          </button>
                          <div className="min-w-0">
                            <div className="break-words font-sans text-sm font-medium text-gray-100">{label.primary}</div>
                            {label.context && <div className="text-gray-400">{label.context}</div>}
                            <div className="text-[10px] text-gray-500">{label.technical} · index {e.sti_frame_index_0based}</div>
                            {label.filename && <div className="break-all text-[10px] text-gray-500" title={label.filename}>{label.filename}</div>}
                          </div>
                        </div>
                        <span className="flex shrink-0 gap-1">
                          {/* JSD viewer button — shown only when the
                              slot's STI has a sibling .jsd. The
                              has_jsd flag is populated from the atlas
                              manifest's slot_has_jsd map. */}
                          {e.has_jsd && (
                            <button
                              type="button"
                              title={`View .jsd (multi-tile footprint, passability, PROFILE voxel grid)`}
                              onClick={() => setJsdSlot(e.slot)}
                              className="rounded border border-amber-700 bg-amber-950/40 px-1.5 py-0.5 text-[10px] text-amber-300 hover:border-amber-500 hover:bg-amber-900/50"
                            >
                              JSD
                            </button>
                          )}
                          {editable && !editing && (
                            <>
                              <button
                                type="button"
                                title="Edit slot/sub"
                                disabled={editBusy}
                                onClick={() => { setEditingKey(key); setEditError(null); }}
                                className="rounded border border-gray-700 px-1.5 py-0.5 text-[10px] text-gray-300 hover:border-blue-500 hover:text-blue-300 disabled:opacity-50"
                              >
                                ✎
                              </button>
                              <button
                                type="button"
                                title="Remove this entry"
                                disabled={editBusy}
                                onClick={() => {
                                  void confirm({
                                    title: "Remove tile entry?",
                                    body: `Remove ${layer}[${i}] = slot ${e.slot} sub ${e.sub}?`,
                                    confirmLabel: "Remove entry",
                                    destructive: true,
                                    onConfirm: () => applyEdit("remove", layer, i),
                                  });
                                }}
                                className="rounded border border-gray-700 px-1.5 py-0.5 text-[10px] text-gray-300 hover:border-red-500 hover:text-red-300 disabled:opacity-50"
                              >
                                ✕
                              </button>
                            </>
                          )}
                        </span>
                      </div>
                      <details className="mt-1 text-[10px] text-gray-400">
                        <summary className="w-fit cursor-pointer hover:text-gray-200">View larger</summary>
                        <div className="mt-1 w-fit rounded border border-gray-700 bg-gray-950 p-1">
                          <AtlasFrameThumb
                            renderer={renderer} slot={e.slot} sub={e.sub} size={192} zoom
                            fallback={<StiFrameImage
                              xmlPath={xmlPath} tileset={tileset}
                              slot={e.slot} sub={e.sub} maxSize={192} zoom
                            />}
                          />
                        </div>
                      </details>
                      {editing && (
                        <EditRow
                          xmlPath={xmlPath} tileset={tileset}
                          renderer={renderer}
                          initialSlot={e.slot}
                          initialSub={e.sub}
                          busy={editBusy}
                          onCancel={() => setEditingKey(null)}
                          onApply={(slot, sub) => applyEdit("replace", layer, i, slot, sub)}
                        />
                      )}
                    </li>
                  );
                })}
              </ul>
            </div>
          );
        }
      )}

      {jsdSlot !== null && (
        <JsdViewer
          xmlPath={xmlPath}
          tileset={tileset}
          slot={jsdSlot}
          onClose={() => setJsdSlot(null)}
        />
      )}
    </div>
  );
}

// ─── JSD viewer ───────────────────────────────────────────────────────
// Renders a parsed .jsd: header (flag names + HP/armour/density) +
// per-footprint-tile PROFILE 5x5 voxel grids. Used by the tile
// inspector when the user clicks the "JSD" button on a struct entry.
function JsdViewer({
  xmlPath, tileset, slot, onClose,
}: {
  xmlPath: string;
  tileset: number;
  slot: number;
  onClose: () => void;
}) {
  const jsd = useQuery({
    queryKey: ["mapforge", "jsd", xmlPath, tileset, slot],
    queryFn: () => getStiJsd(xmlPath, tileset, slot),
    staleTime: 5 * 60 * 1000,
    retry: false,
  });
  return (
    <div className="rounded border border-amber-700 bg-amber-950/30 p-2 text-[10px]">
      <div className="mb-1 flex items-center justify-between">
        <span className="font-mono text-amber-300">
          JSD · slot {slot}
        </span>
        <button
          type="button"
          onClick={onClose}
          className="text-amber-400 hover:text-amber-200"
          title="Close JSD view"
        >✕</button>
      </div>
      {jsd.isLoading && <p className="text-gray-500">Reading .jsd…</p>}
      {jsd.error && (
        <p className="text-red-400">
          {jsd.error instanceof Error ? jsd.error.message : String(jsd.error)}
        </p>
      )}
      {jsd.data && (() => {
        const d = jsd.data;
        return (
          <div className="space-y-1.5 font-mono">
            <div className="text-amber-200">
              {d.sti_filename} <span className="text-amber-500">·</span>
              {" "}{d.size_bytes} B
              {" "}<span className="text-amber-500">·</span>{" "}
              <span className="text-amber-400">{d.szId}</span>
            </div>
            <div title="Bitmask of structural behavior flags the engine checks at render + interaction time. Hover each chip for the per-flag meaning.">
              <span className="text-gray-500">flags 0x{d.flags_int.toString(16).padStart(4, "0")}:</span>{" "}
              {d.flag_names.length === 0 ? (
                <span className="text-gray-600">(none)</span>
              ) : (
                d.flag_names.map((f) => (
                  <span
                    key={f}
                    className="mr-1 inline-block rounded bg-amber-900/60 px-1 text-amber-200"
                    title={_jsdFlagTooltip(f)}
                  >
                    {f}
                  </span>
                ))
              )}
            </div>
            <div className="grid grid-cols-2 gap-1 text-gray-300">
              <div title="Hit points — how much damage the struct absorbs before destruction. 0 = indestructible. Engine field ubHitPoints.">
                HP: <span className="text-amber-200">{d.ubHP}</span>
              </div>
              <div title="Damage resistance against bullets/explosives. Higher = takes less damage per hit. Engine field ubArmour.">
                armour: <span className="text-amber-200">{d.ubArmour}</span>
              </div>
              <div title="Visual + AI density on a 0–100 scale. Influences merc cover bonus + line-of-sight blocking. Engine field ubDensity.">
                density: <span className="text-amber-200">{d.ubDensity}</span>
              </div>
              <div title="Number of grid cells this struct occupies. >1 means it's a multi-tile footprint (cars, big trees, walls).">
                tiles: <span className="text-amber-200">{d.ubNumberOfTiles}</span>
              </div>
              <div title="Z-offset X — horizontal shift applied to the sprite at render time. Used by struct-shadow pairs and tall sprites that need to lift off their anchor.">
                zOff X: <span className="text-amber-200">{d.bZTileOffsetX}</span>
              </div>
              <div title="Z-offset Y — vertical shift applied to the sprite at render time. Negative = lifted up; positive = pushed down. Walls/roofs use this so they sit on the right floor row.">
                zOff Y: <span className="text-amber-200">{d.bZTileOffsetY}</span>
              </div>
            </div>
            {d.tiles.length > 0 && (
              <div className="mt-2 space-y-2">
                <div className="text-gray-500" title="Per-tile Z-occupancy profiles. Each cell of the 5×5 grid is a hex byte where bits represent which Z-slabs (height layers) of that cell are blocked. Used by the engine for cover, LOS, and collision.">
                  Footprint ({d.tiles.length} tile{d.tiles.length === 1 ? "" : "s"}):
                </div>
                {d.tiles.map((tt, i) => (
                  <ProfileGrid key={i} index={i} tile={tt} />
                ))}
              </div>
            )}
            <details className="mt-1 text-gray-500">
              <summary className="cursor-pointer hover:text-gray-300">
                source
              </summary>
              <div className="mt-0.5 max-w-full truncate text-[9px]" title={d.jsd_path}>
                {d.jsd_path}
              </div>
            </details>
          </div>
        );
      })()}
    </div>
  );
}

/** One footprint tile's 5×5 PROFILE — each cell's byte is a Z-occupancy
 * mask (which Z-slabs of the cell are blocked). The grid shows the
 * mask in hex; non-zero cells are tinted amber so you can see the
 * shape at a glance. */
function ProfileGrid({ index, tile }: {
  index: number;
  tile: import("../lib/mapforge").JsdProfileTile;
}) {
  return (
    <div className="rounded border border-amber-900/50 bg-gray-950/60 p-1.5">
      <div className="mb-1 text-gray-400">
        tile[{index}] bX={tile.bXPos} bY={tile.bYPos}
        {" "}<span className="text-gray-600">(sPos={tile.sPosRelToBase})</span>
      </div>
      <div className="inline-grid gap-px"
        style={{ gridTemplateColumns: "repeat(5, 1fr)" }}>
        {tile.profile.map((row, y) => row.map((v, x) => (
          <div
            key={`${y}-${x}`}
            className="flex h-5 w-6 items-center justify-center font-mono text-[8px]"
            style={{
              backgroundColor: v === 0
                ? "rgb(20, 20, 20)"
                : `rgba(255, 200, 100, ${Math.min(1, v / 255 + 0.2)})`,
              color: v === 0 ? "rgb(80, 80, 80)" : "rgb(20, 20, 20)",
            }}
            title={`(${x},${y}) = 0x${v.toString(16).padStart(2, "0")} = ${v}`}
          >
            {v === 0 ? "·" : v.toString(16)}
          </div>
        )))}
      </div>
    </div>
  );
}

/** Live preview thumb for the inline EditRow form. Prefers
 * AtlasFrameThumb (zero HTTP, instant per keystroke) and falls back
 * to StiFrameImage (HTTP fetch) only when the slot/sub the user is
 * typing isn't in the loaded atlas. Most edits land in the atlas
 * since the entry the user is editing came from the current
 * tileset; the fallback is for cases like typing a slot number
 * that exists in the XML but whose sub hasn't been loaded yet. */
function EditRowPreview({
  renderer, xmlPath, tileset, slot, sub,
}: {
  renderer: IsoRenderer | null;
  xmlPath: string;
  tileset: number;
  slot: number;
  sub: number;
}) {
  // Atlas presence is detected by AtlasFrameThumb's own draw attempt —
  // the HTTP fallback rides its `fallback` slot (this component used to
  // run a duplicate 1x1-canvas probe of the same drawCellInto check).
  const fallback = (
    <StiFrameImage
      xmlPath={xmlPath} tileset={tileset}
      slot={slot} sub={sub} maxSize={56}
      className="rounded border border-emerald-700"
    />
  );
  if (!renderer) return fallback;
  return (
    <AtlasFrameThumb
      renderer={renderer} slot={slot} sub={sub} size={56}
      className="rounded border border-emerald-700"
      fallback={fallback}
    />
  );
}


function EditRow({
  xmlPath, tileset, renderer, initialSlot, initialSub, busy, onCancel, onApply,
}: {
  xmlPath: string;
  tileset: number;
  /** Renderer for the live preview's atlas lookup. When the typed
   * (slot, sub) is in the cellMap we render from the atlas (instant);
   * otherwise we fall back to the HTTP path so the user still sees
   * something — e.g., typing a slot that exists in the tileset XML
   * but whose sub they haven't picked yet. */
  renderer: IsoRenderer | null;
  initialSlot: number;
  initialSub: number;
  busy: boolean;
  onCancel: () => void;
  onApply: (slot: number, sub: number) => void;
}) {
  const [slot, setSlot] = useState(initialSlot);
  const [sub, setSub] = useState(initialSub);
  return (
    <form
      className="mt-2 flex items-end gap-2"
      onSubmit={(e) => { e.preventDefault(); onApply(slot, sub); }}
    >
      {/* Live preview of the chosen (slot, sub). Updates instantly
          via atlas lookup; falls back to HTTP for slots/subs not in
          the current cellMap. */}
      <div className="flex flex-col items-center gap-0.5">
        <span className="text-[9px] text-gray-500">preview</span>
        <EditRowPreview
          renderer={renderer}
          xmlPath={xmlPath}
          tileset={tileset}
          slot={slot}
          sub={sub}
        />
      </div>
      <div>
        <label className="block text-[9px] text-gray-500">slot</label>
        <input
          type="number" min={0} max={255}
          value={slot}
          onChange={(e) => setSlot(parseInt(e.target.value, 10) || 0)}
          className="w-16 rounded border border-gray-700 bg-gray-900 px-1.5 py-0.5 text-[11px]"
        />
      </div>
      <div>
        <label className="block text-[9px] text-gray-500">sub</label>
        <input
          type="number" min={1} max={65535}
          value={sub}
          onChange={(e) => setSub(parseInt(e.target.value, 10) || 1)}
          className="w-16 rounded border border-gray-700 bg-gray-900 px-1.5 py-0.5 text-[11px]"
        />
      </div>
      <button
        type="submit" disabled={busy}
        title="Replace this entry with the typed slot/sub (preserves layer + entry position)"
        className="rounded border border-emerald-700 bg-emerald-900 px-2 py-0.5 text-[10px] text-emerald-100 hover:bg-emerald-800 disabled:opacity-50"
      >
        {busy ? "…" : "Apply"}
      </button>
      <button
        type="button" disabled={busy} onClick={onCancel}
        title="Discard pending changes and close the edit form"
        className="rounded border border-gray-700 bg-gray-900 px-2 py-0.5 text-[10px] text-gray-300 hover:bg-gray-800 disabled:opacity-50"
      >
        Cancel
      </button>
    </form>
  );
}

// ─── Load progress bar ────────────────────────────────────────────────
// Replaces the old indeterminate "Loading tileset atlas…" spinner with
// a real percent bar driven by IsoRenderer.create's onProgress callback.
// Atlas fetch reports bytes-loaded, decode + bake report sub-phase
// percents, manifest + parsed are short fixed-weight slots.
export function LoadProgressBar({
  phase, phasePct, overallPct,
}: {
  phase: ProgressPhase;
  phasePct: number;
  overallPct: number;
}) {
  return (
    <div className="absolute inset-0 z-20 flex items-center justify-center bg-gray-950/70 backdrop-blur-sm">
      <div className="w-80 rounded-lg border border-blue-800 bg-gray-900 p-4 shadow-lg">
        <div className="mb-2 flex items-center justify-between text-xs">
          <span className="text-blue-200">
            {PROGRESS_PHASE_LABELS[phase]}…
          </span>
          <span className="font-mono text-blue-300">{overallPct}%</span>
        </div>
        {/* Outer bar: overall progress across all phases. */}
        <div className="relative h-2 overflow-hidden rounded bg-gray-800">
          <div
            className="h-full bg-blue-500 transition-[width] duration-100 ease-linear"
            style={{ width: `${overallPct}%` }}
          />
        </div>
        {/* Inner bar: current phase sub-progress. Useful when the atlas
            fetch is slow — the inner bar shows the download is
            actually moving, not just the phase label flipping. */}
        <div className="mt-2 h-1 overflow-hidden rounded bg-gray-800">
          <div
            className="h-full bg-blue-400/60 transition-[width] duration-75 ease-linear"
            style={{ width: `${phasePct}%` }}
          />
        </div>
      </div>
    </div>
  );
}
