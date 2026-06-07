/**
 * Phase 4 — inject-sub modal. Lets the user append a single sub-frame
 * from a library STI onto an existing tileset slot's STI binary.
 *
 * Wired via the rail's RecentAdditionCard "Inject" chip + a future
 * "Inject from library..." entry in the Asset Viewer toolbar.
 *
 * Three panes top-to-bottom:
 *   1. Source: thumbnail of the chosen library STI + its sub-grid.
 *   2. Destination: dropdown of loose-on-disk slots in the active
 *      tileset (SLF-only slots are filtered out by the backend).
 *   3. Preview: shows current frame count → new, palette-mismatch
 *      warning, force-anyway toggle.
 *
 * On confirm, fires POST /mapforge/library/stis/{sha}/inject-sub.
 * On success: shows the new sub index + auto-closes after 2s.
 */
import { useEffect, useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import {
  getLibraryStiDetail,
  getLibraryStiThumbBlobUrl,
  getLibrarySubThumbBlobUrl,
  injectSubToTileset,
  listLibrarySubs,
  listLooseSlots,
  type LibrarySub,
  type LooseSlot,
} from "../lib/mapforge";

export function MapForgeInjectSubModal({
  srcSha256, srcFilename, tileset, onClose, onInjected,
}: {
  /** Catalog sha of the SOURCE STI — the one we'll pull a sub from. */
  srcSha256: string;
  /** Just for the modal title — falls back to "library STI" if absent. */
  srcFilename?: string;
  tileset: number;
  onClose: () => void;
  /** Fires once on successful inject. Parent uses this to log the
   * event + bump the atlas-reload epoch. */
  onInjected: (result: {
    slot: number;
    sti_filename: string;
    new_sub_index: number;
    frames_after: number;
  }) => void;
}) {
  const qc = useQueryClient();
  const [selectedSub, setSelectedSub] = useState<number | null>(null);
  const [destSlot, setDestSlot] = useState<number | null>(null);
  const [force, setForce] = useState(false);
  // Inline error/success state — separate from the mutation's own
  // pending/error because we want to surface PALETTE_MISMATCH
  // specifically with a force-anyway recovery.
  const [inlineError, setInlineError] = useState<string | null>(null);
  const [paletteMismatch, setPaletteMismatch] = useState(false);
  const [success, setSuccess] = useState<{
    slot: number;
    sti_filename: string;
    new_sub_index: number;
    frames_after: number;
  } | null>(null);

  // Source detail (for STI thumb + frame count surfacing).
  const srcDetail = useQuery({
    queryKey: ["mapforge", "library", "sti", srcSha256],
    queryFn: () => getLibraryStiDetail(srcSha256),
    staleTime: 5 * 60 * 1000,
  });
  const srcSubs = useQuery({
    queryKey: ["mapforge", "library", "sti", srcSha256, "subs"],
    queryFn: () => listLibrarySubs(srcSha256),
    staleTime: 5 * 60 * 1000,
  });
  const looseSlots = useQuery({
    queryKey: ["mapforge", "loose-slots", tileset],
    queryFn: () => listLooseSlots(tileset),
    staleTime: 30 * 1000,
  });
  const [srcThumbUrl, setSrcThumbUrl] = useState<string | null>(null);
  useEffect(() => {
    let cancelled = false;
    let created: string | null = null;
    getLibraryStiThumbBlobUrl(srcSha256)
      .then((u) => {
        if (cancelled) { URL.revokeObjectURL(u); return; }
        created = u;
        setSrcThumbUrl(u);
      })
      .catch(() => {});
    return () => {
      cancelled = true;
      if (created) URL.revokeObjectURL(created);
    };
  }, [srcSha256]);
  // Close-on-Escape — standard modal UX.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  const destSlotInfo: LooseSlot | undefined = useMemo(
    () => looseSlots.data?.slots.find((s) => s.slot === destSlot),
    [looseSlots.data, destSlot],
  );

  const inject = useMutation({
    mutationFn: (body: {
      tileset: number;
      target_slot: number;
      src_sub: number;
      force?: boolean;
    }) => injectSubToTileset(srcSha256, body),
    onSuccess: (res) => {
      setSuccess({
        slot: res.slot,
        sti_filename: res.sti_filename,
        new_sub_index: res.new_sub_index,
        frames_after: res.frames_after,
      });
      setPaletteMismatch(false);
      setInlineError(null);
      onInjected({
        slot: res.slot,
        sti_filename: res.sti_filename,
        new_sub_index: res.new_sub_index,
        frames_after: res.frames_after,
      });
      // Cause the loose-slots cache to update so frame_count reflects
      // the new sub the next time the modal opens.
      qc.invalidateQueries({ queryKey: ["mapforge", "loose-slots", tileset] });
      qc.invalidateQueries({ queryKey: ["mapforge", "palette"] });
      qc.invalidateQueries({ queryKey: ["mapforge", "palette-sheet-meta"] });
      window.setTimeout(() => onClose(), 2200);
    },
    onError: (err) => {
      // PALETTE_MISMATCH gets a special recovery — show the
      // force-anyway toggle. All other errors render plainly.
      const msg = err instanceof Error ? err.message : String(err);
      if (msg.includes("PALETTE_MISMATCH")) {
        setPaletteMismatch(true);
        setInlineError(msg);
      } else {
        setPaletteMismatch(false);
        setInlineError(msg);
      }
    },
  });

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm"
      onClick={onClose}
      role="dialog"
      aria-modal="true"
    >
      <div
        className="w-[36rem] max-w-[94vw] rounded-lg border border-amber-700 bg-gray-950 shadow-2xl"
        onClick={(e) => e.stopPropagation()}
      >
        {/* Header */}
        <div className="flex items-start justify-between gap-3 border-b border-gray-800 p-3">
          <div className="min-w-0">
            <h3 className="text-sm font-semibold text-amber-200">
              Inject sub into existing slot
            </h3>
            <p className="mt-0.5 truncate font-mono text-xs text-gray-400">
              source: {srcFilename ?? "library STI"}
            </p>
          </div>
          <button
            type="button"
            onClick={onClose}
            title="Cancel (Esc)"
            className="text-gray-500 hover:text-gray-200"
          >
            ✕
          </button>
        </div>

        {/* Success */}
        {success ? (
          <div className="p-4">
            <div className="rounded border border-emerald-700 bg-emerald-950/60 px-3 py-2 text-sm text-emerald-100">
              ✓ Appended as sub{" "}
              <span className="font-mono font-bold">{success.new_sub_index}</span>{" "}
              of <span className="font-mono">{success.sti_filename}</span>{" "}
              <span className="text-emerald-300">
                (now {success.frames_after} frames)
              </span>
            </div>
            <p className="mt-2 text-[10px] text-gray-500">
              Atlas is reloading… the new sub will be paintable as
              slot {success.slot} / sub {success.new_sub_index} once
              the reload completes. Modal closes automatically.
            </p>
          </div>
        ) : (
          <div className="space-y-3 p-3">
            {/* Source pane */}
            <section>
              <h4 className="mb-1 text-[10px] uppercase tracking-wider text-gray-500">
                1 · Pick source sub
              </h4>
              <div className="flex gap-3">
                {srcThumbUrl ? (
                  <img
                    src={srcThumbUrl}
                    alt={srcFilename ?? "source"}
                    className="block h-20 w-20 rounded border border-gray-800 bg-gray-950"
                    style={{ imageRendering: "pixelated", objectFit: "contain" }}
                  />
                ) : (
                  <span className="inline-block h-20 w-20 rounded bg-gray-800 animate-pulse" />
                )}
                <div className="flex-1">
                  {srcSubs.isLoading && (
                    <p className="text-[11px] text-gray-500">Loading subs…</p>
                  )}
                  {srcSubs.error && (
                    <p className="text-[11px] text-red-400">
                      {srcSubs.error instanceof Error
                        ? srcSubs.error.message
                        : String(srcSubs.error)}
                    </p>
                  )}
                  {srcSubs.data && (
                    <SourceSubGrid
                      subs={srcSubs.data.subs}
                      selected={selectedSub}
                      onSelect={(idx) => setSelectedSub(idx)}
                    />
                  )}
                </div>
              </div>
            </section>

            {/* Destination pane */}
            <section>
              <h4 className="mb-1 text-[10px] uppercase tracking-wider text-gray-500">
                2 · Pick destination slot
              </h4>
              {looseSlots.isLoading && (
                <p className="text-[11px] text-gray-500">Scanning tileset for loose-file slots…</p>
              )}
              {looseSlots.data && looseSlots.data.slots.length === 0 && (
                <p className="rounded border border-gray-800 bg-gray-900/40 p-2 text-[11px] text-amber-300">
                  No loose-file slots in tileset {tileset}. All slots
                  resolve via SLF archives, which the v1 inject-sub
                  flow can't append to. Extract the slot's SLF to a
                  loose file first, or use the regular add-to-tileset
                  flow to bring this sub in as a new slot.
                </p>
              )}
              {looseSlots.data && looseSlots.data.slots.length > 0 && (
                <select
                  value={destSlot ?? ""}
                  onChange={(e) =>
                    setDestSlot(e.target.value ? parseInt(e.target.value, 10) : null)
                  }
                  className="w-full rounded border border-gray-700 bg-gray-900 px-2 py-1 text-xs"
                >
                  <option value="">— pick a slot —</option>
                  {looseSlots.data.slots.map((s) => (
                    <option key={s.slot} value={s.slot}>
                      slot {s.slot} · {s.filename} ({s.frame_count} frames)
                    </option>
                  ))}
                </select>
              )}
            </section>

            {/* Preview pane */}
            <section>
              <h4 className="mb-1 text-[10px] uppercase tracking-wider text-gray-500">
                3 · Preview
              </h4>
              {selectedSub !== null && destSlotInfo ? (
                <div className="rounded border border-gray-800 bg-gray-900/40 p-2 text-[11px] text-gray-300">
                  Source sub <span className="font-mono">{selectedSub}</span>{" "}
                  → <span className="font-mono">{destSlotInfo.filename}</span>{" "}
                  (slot {destSlotInfo.slot})
                  <div className="text-gray-500">
                    frames: {destSlotInfo.frame_count} →{" "}
                    <span className="text-emerald-300">{destSlotInfo.frame_count + 1}</span>
                    {" · new sub index: "}
                    <span className="font-mono text-emerald-300">
                      {destSlotInfo.frame_count}
                    </span>
                  </div>
                </div>
              ) : (
                <p className="text-[11px] italic text-gray-500">
                  Pick a source sub + destination slot above.
                </p>
              )}
            </section>

            {/* Errors / force */}
            {inlineError && (
              <div className="rounded bg-red-950/60 px-2 py-1.5 text-[11px] text-red-300">
                {inlineError}
                {paletteMismatch && (
                  <label className="mt-1.5 flex cursor-pointer items-center gap-1.5 text-amber-300">
                    <input
                      type="checkbox"
                      checked={force}
                      onChange={(e) => setForce(e.target.checked)}
                      className="accent-amber-500"
                    />
                    Inject anyway (appended frame will render with the
                    destination's palette and may look colorshifted)
                  </label>
                )}
              </div>
            )}

            {/* Footer */}
            <div className="flex justify-end gap-2 border-t border-gray-800 pt-2">
              <button
                type="button"
                onClick={onClose}
                disabled={inject.isPending}
                className="rounded border border-gray-700 bg-gray-900 px-3 py-1.5 text-xs text-gray-300 hover:border-gray-500 disabled:opacity-50"
              >
                Cancel
              </button>
              <button
                type="button"
                disabled={
                  inject.isPending
                  || selectedSub === null
                  || destSlot === null
                  || (paletteMismatch && !force)
                }
                onClick={() => {
                  if (selectedSub === null || destSlot === null) return;
                  setInlineError(null);
                  inject.mutate({
                    tileset,
                    target_slot: destSlot,
                    src_sub: selectedSub,
                    force,
                  });
                }}
                className="rounded border border-amber-600 bg-amber-700 px-4 py-1.5 text-xs font-semibold text-amber-50 hover:bg-amber-600 disabled:opacity-50"
              >
                {inject.isPending ? "Injecting…" : "+ Inject sub"}
              </button>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}

/** Source-sub picker grid — single-select (radio model). The inject
 * flow only takes one sub at a time, so this is a flat grid not a
 * multi-select like the AddStiToTilesetModal's SubframeGrid. */
function SourceSubGrid({
  subs, selected, onSelect,
}: {
  subs: LibrarySub[];
  selected: number | null;
  onSelect: (sub_idx: number) => void;
}) {
  if (subs.length === 0) {
    return (
      <p className="text-[11px] text-gray-500">No subs in catalog.</p>
    );
  }
  return (
    <div className="grid max-h-32 grid-cols-7 gap-1 overflow-y-auto rounded border border-gray-800 bg-gray-900/60 p-1">
      {subs.map((s) => (
        <SourceSubCell
          key={`${s.sub_idx}-${s.sha256}`}
          sub={s}
          isSelected={selected === s.sub_idx}
          onClick={() => onSelect(s.sub_idx)}
        />
      ))}
    </div>
  );
}

function SourceSubCell({
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
      title={`sub ${sub.sub_idx} · ${sub.width}×${sub.height}`}
      className={`flex flex-col items-center rounded border p-0.5 ${
        isSelected
          ? "border-amber-500 bg-amber-950/60"
          : "border-gray-700 bg-gray-900 hover:border-gray-500"
      }`}
    >
      {err ? (
        <span className="inline-flex h-9 w-9 items-center justify-center rounded bg-red-950 text-[8px] text-red-400">
          ?
        </span>
      ) : url ? (
        <img
          src={url}
          alt={`sub ${sub.sub_idx}`}
          className="block h-9 w-9 bg-gray-950"
          style={{ imageRendering: "pixelated", objectFit: "contain" }}
        />
      ) : (
        <span className="inline-block h-9 w-9 animate-pulse rounded bg-gray-800" />
      )}
      <span className="mt-0.5 text-[8px] text-gray-500">{sub.sub_idx}</span>
    </button>
  );
}
