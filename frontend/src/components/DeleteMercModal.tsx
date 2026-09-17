import { useMutation, useQueryClient } from "@tanstack/react-query";

import { deleteMerc, formatApiError } from "../lib/api";
import ConfirmModal from "./ConfirmModal";
import SaveSnapshotBanner from "./SaveSnapshotBanner";
import { SlotLockWarningModal } from "./SlotLockWarningModal";
import { useSlotLockGuard } from "../lib/slotLocks";

/**
 * In-grid merc delete: the type-to-confirm modal + slot-lock warning that
 * used to live on the standalone /delete page, now openable straight from
 * the roster. Same server contract — `force: true` after the lock guard,
 * automatic backup before any writes.
 */
export default function DeleteMercModal({
  slot,
  nickname,
  name,
  onClose,
}: {
  slot: number;
  nickname: string | null;
  name: string | null;
  onClose: () => void;
}) {
  const qc = useQueryClient();
  const lockGuard = useSlotLockGuard();

  const del = useMutation({
    mutationFn: () => deleteMerc(slot, { force: true }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["roster"] });
      qc.invalidateQueries({ queryKey: ["backups"] });
      qc.invalidateQueries({ queryKey: ["slot", slot] });
      // Slot picker — the just-deleted slot should flip back to empty
      // for the next Create.
      qc.invalidateQueries({ queryKey: ["slot-picker"] });
      onClose();
    },
  });

  // The type-to-confirm gate must never be empty — a merc with a blank
  // nickname made the modal confirm on zero keystrokes. Fall back to
  // name, then the slot number.
  const confirmText = nickname || name || `slot ${slot}`;

  return (
    <>
      {!lockGuard.pending && (
        <ConfirmModal
          open
          title={`Delete ${confirmText}?`}
          body={
            <>
              <p>
                This removes all of {confirmText}'s data from your game — MercProfiles.xml,
                AIMAvailability.xml, MercAvailability.xml, MercStartingGear.xml, and the EDT bio
                record. A backup is taken automatically and can be restored from the Backups page.
              </p>
              {/* Deleting a merc from MercProfiles does NOT remove them from
                  saves they're already hired in — the engine reads the
                  per-soldier snapshot, not the live XML. Hidden when no save
                  references the slot. */}
              <div className="mt-3">
                <SaveSnapshotBanner slot={slot} action="delete" />
              </div>
              {del.isError && (
                <div className="mt-3 text-sm text-rust-400">
                  <div>Delete failed: {formatApiError(del.error)}</div>
                  <div className="text-xs text-rust-300 mt-1">
                    If files were partially removed, restore the most recent automatic snapshot
                    from the Backups page.
                  </div>
                </div>
              )}
            </>
          }
          confirmLabel={del.isPending ? "Deleting..." : "Delete"}
          destructive
          busy={del.isPending}
          typeToConfirm={confirmText}
          onConfirm={() => {
            // Deleting a quest-bound / engine-named merc deserves the same
            // warning creating over one gets — the blast radius is the same.
            lockGuard.guard(slot, () => del.mutate());
          }}
          onCancel={onClose}
        />
      )}
      {lockGuard.pending && (
        <SlotLockWarningModal
          lock={lockGuard.pending.lock}
          action="delete"
          onConfirm={lockGuard.confirm}
          onCancel={lockGuard.cancel}
        />
      )}
    </>
  );
}
