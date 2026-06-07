import { useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { deleteMerc, formatApiError, getRoster } from "../lib/api";
import ConfirmModal from "../components/ConfirmModal";
import SaveSnapshotBanner from "../components/SaveSnapshotBanner";

export default function Delete() {
  const qc = useQueryClient();
  const roster = useQuery({ queryKey: ["roster"], queryFn: () => getRoster() });
  // Pre-select from ?slot= when the roster's "Delete" action drives us here.
  const [params] = useSearchParams();
  const initialSlot = params.get("slot");
  const [slot, setSlot] = useState<number | null>(
    initialSlot !== null && /^\d+$/.test(initialSlot) ? Number(initialSlot) : null
  );
  const [confirm, setConfirm] = useState(false);

  const del = useMutation({
    mutationFn: () => deleteMerc(slot!),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["roster"] });
      qc.invalidateQueries({ queryKey: ["backups"] });
      qc.invalidateQueries({ queryKey: ["slot", slot!] });
      // Slot picker — the just-deleted slot should flip back to empty
      // for the next Create. Bug-review finding E4.
      qc.invalidateQueries({ queryKey: ["slot-picker"] });
      setConfirm(false);
      setSlot(null);
    },
  });

  const filled = (roster.data ?? []).filter((e) => !e.is_empty);
  const entry = roster.data?.find((e) => e.slot === slot);
  const nickname = entry?.nickname ?? "";

  return (
    <div className="mx-auto max-w-3xl px-6 py-8 space-y-6">
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-bold">Delete Merc</h1>
        <Link to="/" className="btn-ghost text-sm">← Back to Hub</Link>
      </div>

      <section className="card">
        <h2 className="text-lg font-semibold mb-3">Pick a merc to remove</h2>
        <select
          className="input max-w-md"
          value={slot ?? ""}
          onChange={(e) => setSlot(e.target.value ? Number(e.target.value) : null)}
        >
          <option value="">Choose a merc...</option>
          {filled.map((e) => (
            <option key={e.slot} value={e.slot}>
              Slot {e.slot}: {e.nickname ?? e.name}
            </option>
          ))}
        </select>
      </section>

      {slot !== null && entry && (
        <section className="card">
          <h2 className="text-lg font-semibold mb-2">Confirm deletion</h2>
          <p className="text-sm text-wasteland-200 mb-3">
            About to remove <span className="text-rust-400 font-medium">{nickname}</span> from
            slot <span className="font-mono">{slot}</span>.
          </p>
          {/* Save-snapshot warning — deleting a merc from MercProfiles
              does NOT remove them from saves they're already hired in.
              The engine reads stats from the per-soldier snapshot, not
              the live XML, so the merc stays playable in existing saves
              even after MercWizard scrubs their row. Hidden when no
              existing save references the slot. */}
          <div className="mb-4">
            <SaveSnapshotBanner slot={slot} action="delete" />
          </div>
          <p className="text-xs text-wasteland-400 mb-4">
            This clears MercProfiles.xml, AIMAvailability.xml, MercAvailability.xml,
            MercStartingGear.xml, and zero-fills the EDT bio record. A backup is taken
            automatically before any writes.
          </p>
          <button
            type="button"
            className="btn-primary bg-rust-600 hover:bg-rust-500"
            onClick={() => setConfirm(true)}
            disabled={del.isPending}
          >
            {del.isPending ? "Deleting..." : "Delete"}
          </button>
          {del.isError && (
            <div className="mt-3 text-sm text-rust-400">
              <div>Delete failed: {formatApiError(del.error)}</div>
              <div className="text-xs text-rust-300 mt-1">
                If files were partially removed, restore the most recent automatic snapshot
                from the Backups page.
              </div>
            </div>
          )}
        </section>
      )}

      <ConfirmModal
        open={confirm}
        title={`Delete ${nickname}?`}
        body={
          <>
            This will remove all of {nickname}'s data from your game. A backup is taken
            automatically and can be restored from the Backups page.
          </>
        }
        confirmLabel="Delete"
        destructive
        typeToConfirm={nickname}
        onConfirm={() => del.mutate()}
        onCancel={() => setConfirm(false)}
      />
    </div>
  );
}
