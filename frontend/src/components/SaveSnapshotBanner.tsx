import { useState } from "react";
import { useQuery } from "@tanstack/react-query";

import { getSavesRefs } from "../lib/api";

interface Props {
  /** uiIndex of the merc being edited / moved / deleted. */
  slot: number;
  /**
   * Verb tweak per host route. The advice is identical — "edits to
   * MercProfiles.xml don't retroactively apply to existing saves" — but
   * surfacing "Edits" on Edit.tsx vs "Move" on Move.tsx vs "Deletion"
   * on Delete.tsx makes the warning feel like it belongs to the
   * surrounding flow instead of a generic interjection.
   */
  action?: "edit" | "move" | "delete";
}

/**
 * Surfaces the engine's save-snapshot trap: when a merc is hired, their
 * MercProfiles.xml stats are snapshotted into the SOLDIERTYPE struct in
 * the save file. Post-hire edits to MercProfiles.xml — stats, portrait,
 * bio, anything — do NOT retroactively rewrite the snapshot. Only NEW
 * hires (in NEW campaigns, or re-hires after firing in the same save)
 * pick up the new values.
 *
 * Without this banner, users hit a confusing "I bumped Buns to 95
 * marksmanship in MercWizard, reloaded my save, she's still at 75 —
 * MercWizard corrupted my save!" loop. This warns them at the moment
 * they're about to commit a change.
 *
 * Hidden when:
 *  - The query is still in flight (fail-soft — no flicker).
 *  - The query errored (e.g. install gone or sidecar down; we silently
 *    drop the banner rather than surface a scary error for a soft warning).
 *  - The slot is referenced in zero saves (the common case for newly-
 *    created mercs who haven't been hired anywhere yet).
 *
 * The full list of matching save paths is collapsed behind a
 * <details> — most users don't care which file, just that there ARE
 * existing references.
 */
export default function SaveSnapshotBanner({ slot, action = "edit" }: Props) {
  const [open, setOpen] = useState(false);

  const refs = useQuery({
    queryKey: ["saves-refs", slot],
    queryFn: () => getSavesRefs(slot),
    // Saves don't change while the user is on this page; cache for the
    // session. We don't bother invalidating after the user's own edit
    // either — the warning is about EXISTING saves, which a MercWizard
    // edit didn't touch.
    staleTime: 5 * 60 * 1000,
    // Don't surface the soft warning's loading state — fail invisibly.
    retry: false,
  });

  if (refs.isLoading || refs.isError) return null;
  const saves = refs.data?.saves ?? [];
  if (saves.length === 0) return null;

  const verbSubject = ({
    edit: "Stat edits",
    move: "Moving this merc",
    delete: "Deleting this merc",
  } as const)[action];
  const verbEffect = ({
    edit: "won't change any save that already includes this merc",
    move: "won't update the save's snapshot",
    delete: "won't remove them from the save",
  } as const)[action];

  return (
    <div className="rounded border border-yellow-500/40 bg-yellow-500/10 p-3 text-sm text-yellow-200">
      <div className="font-medium text-yellow-100 mb-1">
        ⚠ This merc appears in {saves.length} existing save file
        {saves.length === 1 ? "" : "s"}
      </div>
      <div className="text-xs text-yellow-200/90">
        JA2 bakes a merc's abilities and stats into your save the first time
        that merc appears in it, and reloads them from the save every time you
        open it — so {verbSubject.toLowerCase()} {verbEffect}. Those saves keep
        the stats from before your edit, and re-hiring the merc inside an
        existing save just restores those same old stats. Only a brand-new
        campaign picks up the new stats. (Portrait and bio-text changes are read
        from the game's files, so those do show up in existing saves.)
      </div>
      <button
        type="button"
        className="mt-2 text-[11px] text-yellow-300/80 hover:text-yellow-100 underline-offset-2 hover:underline"
        onClick={() => setOpen((v) => !v)}
      >
        {open
          ? "Hide save files"
          : `Show ${saves.length} matching save file${saves.length === 1 ? "" : "s"}`}
      </button>
      {open && (
        <ul className="mt-2 max-h-40 overflow-y-auto space-y-0.5 font-mono text-[11px] text-yellow-200/80">
          {saves.map((path) => (
            <li key={path} className="truncate" title={path}>
              {path}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
