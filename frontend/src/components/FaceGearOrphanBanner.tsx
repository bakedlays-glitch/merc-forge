/**
 * Install-level FaceGear orphan warning. Renders the "Face_X has no
 * _IMP partner" alarm exactly ONCE — on the Hub — instead of once per
 * merc profile (which is what the old per-merc banner did, dominating
 * every Create / Edit page with an install-wide warning unrelated to
 * the merc being edited; bug #1 in MERC_FORGE_BUG_LIST.md).
 *
 * Renders nothing when there are no orphans, when the FaceGear scan is
 * still loading, or when the active install has no FaceGear STIs at
 * all. Safe to mount anywhere; doesn't take any per-merc state.
 *
 * Bug #89: "Repair all" button copies each present STI to its missing
 * partner name. Backed by POST /facegear/orphans/repair — takes a
 * backup snapshot first, so undo via the Backups page works.
 */
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { formatApiError, getFaceGearCapacity, repairFaceGearOrphans } from "../lib/api";

export default function FaceGearOrphanBanner() {
  const qc = useQueryClient();
  const capacity = useQuery({
    queryKey: ["facegear-capacity"],
    queryFn: () => getFaceGearCapacity(),
    staleTime: 5 * 60 * 1000,
  });
  const repair = useMutation({
    mutationFn: () => repairFaceGearOrphans(null),
    onSuccess: () => {
      // Re-scan so the banner reflects whatever's still orphaned (the
      // backend already verifies before each copy, so the second scan
      // is the authoritative result). Also invalidate the install
      // diagnostics in case those mirror the orphan list.
      qc.invalidateQueries({ queryKey: ["facegear-capacity"] });
      qc.invalidateQueries({ queryKey: ["install-diagnostics"] });
    },
  });

  if (capacity.isLoading || capacity.isError || !capacity.data) return null;
  const orphans = capacity.data.orphans ?? [];
  if (orphans.length === 0) return null;

  return (
    <div className="rounded border border-red-500/70 bg-red-500/15 p-3 text-xs text-red-100 space-y-2">
      <div className="flex items-start justify-between gap-3">
        <div className="font-semibold">
          ⚠ {orphans.length} FaceGear item{orphans.length === 1 ? "" : "s"} missing
          one half of its image pair — affected gear may show wrong or not at all
        </div>
        <button
          type="button"
          onClick={() => repair.mutate()}
          disabled={repair.isPending}
          className="shrink-0 rounded border border-red-300/60 bg-red-500/30 px-2 py-1 text-[11px] font-semibold text-red-50 hover:bg-red-500/45 disabled:opacity-60 disabled:cursor-wait"
          title="Copy each present Face_*.sti to its missing partner name. A backup is taken first."
        >
          {repair.isPending
            ? "Repairing…"
            : repair.isSuccess && repair.data?.repaired.length
              ? `Repaired ${repair.data.repaired.length}`
              : "Repair all"}
        </button>
      </div>
      <div>
        Each item below is set up in this install's{" "}
        <code className="font-mono">FaceGear.xml</code>, but one of its two image
        files is missing from disk. FaceGear items use a matched pair — a normal
        version and an IMP version — and the game loads both. When a file in the
        pair is missing, the game stops loading the rest of the FaceGear images,
        so hats, goggles, and masks can show up wrong or not at all in-game. This
        affects the whole install, not just one merc.
      </div>
      <ul className="font-mono text-[11px] text-red-100/85 max-h-40 overflow-y-auto space-y-0.5">
        {orphans.map((o) => (
          <li key={o.stem}>
            {o.stem} — missing {o.missing === "imp" ? "_IMP variant" : "base variant"}{" "}
            <span className="text-red-300/70">(have {o.present_path})</span>
          </li>
        ))}
      </ul>
      {repair.error && (
        <div
          role="alert"
          className="rounded border border-red-300/60 bg-red-950/60 px-2 py-1 text-[11px]"
        >
          <strong>Repair failed:</strong> {formatApiError(repair.error)}
        </div>
      )}
      {repair.isSuccess && repair.data && repair.data.skipped.length > 0 && (
        <div className="rounded border border-amber-300/40 bg-amber-950/40 px-2 py-1 text-[11px] text-amber-100/90">
          <strong>Some pairs were skipped:</strong>
          <ul className="font-mono text-[10px] mt-0.5 space-y-0.5">
            {repair.data.skipped.map((s, i) => (
              <li key={`${s.stem}-${i}`}>
                {s.stem} — {s.reason}
              </li>
            ))}
          </ul>
        </div>
      )}
      <div className="text-red-200/80 text-[11px]">
        Click <strong>Repair all</strong> to copy each present file to the missing
        partner name automatically (the engine accepts identical base/IMP STIs —
        several vanilla pairs ship that way). Or remove the item from
        FaceGear.xml if you don't need it. A backup snapshot is taken before
        any writes; undo via the Backups page.
      </div>
    </div>
  );
}
