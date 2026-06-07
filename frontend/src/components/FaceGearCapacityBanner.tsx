import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { extendFaceGear, getFaceGearCapacity } from "../lib/api";

interface Props {
  /** The merc's ubFaceIndex — the frame slot the engine will read from
   *  each Face_*.sti when the merc equips that gear. */
  faceIndex: number;
}

/**
 * Surfaces whether the active install's FaceGear STIs cover this merc's
 * face index. If any STI has fewer frames than (faceIndex + 1), equipping
 * the corresponding item in-game crashes (sgp/vobject.cpp:958
 * SGP_THROW_IFFALSE → exit(0); verified in source 2026-05-16).
 *
 * Renders nothing while loading or if no FaceGear STIs exist in the install.
 */
export default function FaceGearCapacityBanner({ faceIndex }: Props) {
  const qc = useQueryClient();
  const capacity = useQuery({
    queryKey: ["facegear-capacity"],
    queryFn: () => getFaceGearCapacity(),
    staleTime: 5 * 60 * 1000,
  });

  const extend = useMutation({
    mutationFn: () => extendFaceGear(faceIndex),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["facegear-capacity"] });
    },
  });

  if (capacity.isLoading) {
    return (
      <div className="mt-3 text-xs text-wasteland-500">
        Checking FaceGear capacity…
      </div>
    );
  }
  if (capacity.isError) {
    return null;
  }
  const data = capacity.data;
  if (!data || data.items.length === 0) {
    return null;
  }

  const required = faceIndex + 1;
  const insufficient = data.items.filter((i) => i.frame_count < required);

  // Note: install-wide ORPHAN warnings used to render here too,
  // showing the same install-level alert on every merc's profile.
  // Moved to the Hub's diagnostics panel (FaceGearOrphanBanner) so
  // this component stays focused on the per-merc capacity question.
  // Bug #1 in MERC_FORGE_BUG_LIST.md.
  if (insufficient.length === 0) {
    return (
      <div className="mt-3 rounded border border-wasteland-700 bg-wasteland-800/40 px-3 py-2 text-xs text-wasteland-400">
        ✓ All {data.items.length} FaceGear STIs cover face index {faceIndex}
        {data.lowest_frame_count !== null && (
          <span className="text-wasteland-500"> (lowest = {data.lowest_frame_count} frames)</span>
        )}
        . The merc can safely equip any face gear in this install.
      </div>
    );
  }

  return (
    <div className="mt-3 space-y-3">
      {insufficient.length > 0 && (
        <div className="rounded border border-rust-500/60 bg-rust-500/10 p-3 text-xs text-rust-200 space-y-2">
          <div className="font-semibold text-rust-100">
            ⚠ FaceGear capacity too low — game will crash if this merc equips the affected items
          </div>
          <div>
            {insufficient.length} of {data.items.length} FaceGear STIs in this install have fewer than{" "}
            {required} frames. The engine bounds-checks the frame index at{" "}
            <code className="font-mono">vobject.cpp:958</code> and exits the process on overflow.
          </div>
          <details>
            <summary className="cursor-pointer text-rust-300 hover:text-rust-100">
              Show affected files ({insufficient.length})
            </summary>
            <ul className="mt-2 space-y-0.5 font-mono text-[11px] text-rust-200/80 max-h-32 overflow-y-auto">
              {insufficient.map((i) => (
                <li key={i.relative_path}>
                  {i.name} <span className="text-rust-300/60">— {i.frame_count} frames</span>
                </li>
              ))}
            </ul>
          </details>
          <div className="flex items-center gap-3 pt-1">
            <button
              type="button"
              className="px-3 py-1 rounded bg-rust-500 hover:bg-rust-400 text-wasteland-950 text-xs font-medium disabled:bg-rust-700 disabled:text-wasteland-700"
              disabled={extend.isPending}
              onClick={() => extend.mutate()}
            >
              {extend.isPending ? "Extending…" : `Extend to ${required} frames (with backup)`}
            </button>
            <span className="text-[11px] text-rust-300/70">
              Appends transparent frames; backup taken first. Reversible from the Backups page.
            </span>
          </div>
          {extend.isSuccess && extend.data && (
            <div className="text-green-300/90">
              ✓ Extended {extend.data.extended.filter((e) => !e.noop).length} file(s)
              {extend.data.backup_id && (
                <span className="text-wasteland-400"> · backup {extend.data.backup_id}</span>
              )}
            </div>
          )}
          {extend.isError && (
            <div className="text-rust-100">Extend failed — check the Backups page.</div>
          )}
        </div>
      )}
    </div>
  );
}
