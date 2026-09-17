import { useMemo, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { duplicateMercStreaming, formatApiError, getRoster } from "../lib/api";
import ConfirmModal from "../components/ConfirmModal";
import SaveProgressBar from "../components/SaveProgressBar";
import SlotPicker from "../components/SlotPicker";
import SourceMercCard from "../components/SourceMercCard";
import { SlotLockWarningModal } from "../components/SlotLockWarningModal";
import { isLockSuppressed, useSlotLockGuard } from "../lib/slotLocks";
import { categoryLabel, useSlotPicker } from "../lib/slotPicker";
import { buildRelocateNotice } from "../lib/relocateNotice";
import { useSaveProgressFade } from "../lib/useSaveProgressFade";

export default function Duplicate() {
  const qc = useQueryClient();
  const lockGuard = useSlotLockGuard();
  const picker = useSlotPicker();
  const roster = useQuery({ queryKey: ["roster"], queryFn: () => getRoster() });

  // Honor the URL ?from= and ?to= query params from the roster's context menu:
  //   - "Copy to…" on a filled slot navigates here with ?from=<slot>
  //   - "Copy existing merc here" on an empty slot navigates here with ?to=<slot>
  // In both cases, the slot the user already selected on the roster should
  // pre-populate so they don't have to re-pick it from a dropdown.
  const [params] = useSearchParams();
  const initialFrom = params.get("from");
  const initialTo = params.get("to");
  const [source, setSource] = useState<number | null>(
    initialFrom !== null && /^\d+$/.test(initialFrom) ? Number(initialFrom) : null
  );
  const [dest, setDest] = useState<number | null>(
    initialTo !== null && /^\d+$/.test(initialTo) ? Number(initialTo) : null
  );
  const sourceLocked = source !== null && initialFrom !== null;
  const destLocked = dest !== null && initialTo !== null;
  const [confirm, setConfirm] = useState(false);
  const [snapshot, setSnapshot] = useState<{ name: string; from: number; to: number } | null>(null);
  // Live progress events from the streaming /duplicate endpoint (see
  // useSaveProgressFade for the streaming + fade rationale).
  const progress = useSaveProgressFade();

  const dup = useMutation({
    mutationFn: () => {
      progress.begin();
      return duplicateMercStreaming(source!, dest!, undefined, progress.push);
    },
    onMutate: () => {
      const entry = roster.data?.find((e) => e.slot === source);
      const name = entry?.nickname ?? entry?.name ?? `slot ${source}`;
      setSnapshot({ name, from: source!, to: dest! });
    },
    onSuccess: () => {
      progress.succeed();
      qc.invalidateQueries({ queryKey: ["roster"] });
      // Slot picker.
      qc.invalidateQueries({ queryKey: ["slot-picker"] });
      // Destination slot detail + voice, so an open Edit / voice view of the
      // new slot refreshes instead of showing stale pre-duplicate data
      // (matches Move/Create's dest invalidation).
      if (dest !== null) {
        qc.invalidateQueries({ queryKey: ["slot", dest] });
        qc.invalidateQueries({ queryKey: ["voice", dest] });
      }
    },
    onError: () => {
      progress.fail();
    },
    // No onSettled here — modal is closed in onConfirm below so the
    // progress bar isn't hidden behind the still-open ConfirmModal
    // during backup+copy.
  });

  const filled = (roster.data ?? []).filter((e) => !e.is_empty);
  const sourceEntry = roster.data?.find((e) => e.slot === source);
  const sourceName = sourceEntry?.nickname ?? sourceEntry?.name ?? "?";
  const sourceInfo = source !== null ? picker.data?.slots[source] : undefined;
  const destInfo = dest !== null ? picker.data?.slots[dest] : undefined;
  const sourceClass = sourceInfo?.category ?? null;
  const destClass = destInfo?.category ?? null;

  // "What changes at the destination" notice — shared case table with the
  // Move flow, phrased for copy. See lib/relocateNotice.ts.
  const crossCategoryNotice = useMemo(
    () => buildRelocateNotice({
      mode: "copy",
      sourceClass,
      destClass,
      source,
      dest,
      sourceName,
      sourceType: sourceEntry?.profile_type ?? null,
    }),
    [sourceClass, destClass, source, dest, sourceEntry, sourceName],
  );

  return (
    <div className="mx-auto max-w-4xl px-6 py-8 space-y-6">
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-bold">Copy Merc</h1>
        <Link to="/" className="btn-ghost text-sm">← Back to Hub</Link>
      </div>

      <div className="rounded border border-rust-500/30 bg-rust-500/5 p-3 text-sm text-wasteland-200">
        <strong>How this differs from Cut:</strong> the source slot stays filled. You'll end
        up with two mercs — the original and a copy at the destination. Both share the same
        portrait (ubFaceIndex) by default; edit the duplicate later if you want different
        artwork.
      </div>

      <SourceMercCard
        source={source}
        sourceLocked={sourceLocked}
        sourceName={sourceName}
        sourceClass={sourceClass}
        filled={filled}
        stepTitle="Step 1: Pick the merc to copy"
        placeholder="Choose a merc..."
        onChange={setSource}
      />

      {source !== null && (
        destLocked ? (
          <section className="card">
            <div className="flex items-center justify-between gap-3">
              <div>
                <div className="text-xs uppercase text-wasteland-500 mb-1">Destination slot</div>
                <div className="text-wasteland-100">
                  <span className="font-mono text-rust-400">Slot {dest}</span>
                  {destClass && (
                    <span className="badge bg-wasteland-700 text-wasteland-200 ml-2">{categoryLabel(destClass)}</span>
                  )}
                </div>
              </div>
              <button
                type="button"
                className="text-xs text-rust-400 hover:underline underline-offset-2"
                onClick={() => setDest(null)}
              >
                Change
              </button>
            </div>
          </section>
        ) : (
          <section className="card">
            <h2 className="text-lg font-semibold mb-3">
              {sourceLocked ? "Pick a destination slot for" : "Step 2: Pick a destination slot for"} the copy of{" "}
              <span className="text-rust-400">{sourceName}</span>
            </h2>
            <p className="text-sm text-wasteland-300 mb-4">
              Only empty slots are selectable.
            </p>
            <SlotPicker selected={dest} onSelect={setDest} />
            {destClass && (
              <div className="mt-3 text-xs text-wasteland-400">
                Slot {dest} is <span className="badge bg-wasteland-700 text-wasteland-200">{categoryLabel(destClass)}</span>
              </div>
            )}
          </section>
        )
      )}

      {source !== null && dest !== null && (
        <section className="card">
          <h2 className="text-lg font-semibold mb-2">Step 3: Confirm</h2>
          <p className="text-sm text-wasteland-200 mb-3">
            Copy <span className="text-rust-400 font-medium">{sourceName}</span> from slot{" "}
            <span className="font-mono">{source}</span> to slot{" "}
            <span className="font-mono">{dest}</span>. The original at slot {source} stays.
          </p>
          {crossCategoryNotice && (
            <div
              className={
                crossCategoryNotice.severity === "warn"
                  ? "mb-3 rounded border border-yellow-500/40 bg-yellow-500/10 p-3 text-sm text-yellow-300"
                  : "mb-3 rounded border border-sky-500/40 bg-sky-500/10 p-3 text-sm text-sky-200"
              }
            >
              <div className="font-medium mb-1">
                {crossCategoryNotice.severity === "warn"
                  ? "⚠ Heads up"
                  : "ℹ What MercForge will do"}
              </div>
              <div>{crossCategoryNotice.text}</div>
            </div>
          )}
          <p className="text-xs text-wasteland-400 mb-4">
            Writes a MercProfiles entry at {dest}, copies the gear block, and writes a
            new EDT bio. {sourceEntry?.profile_type === 1
              && "An AIMAvailability row is added with a fresh AimBioID. "}
            {sourceEntry?.profile_type === 2
              && "A MercAvailability row is added with a fresh MercBioID. "}
            A backup is taken first.
          </p>
          {progress.events && (
            <div className="mb-3">
              <SaveProgressBar
                events={progress.events}
                done={progress.done}
                error={dup.error}
              />
            </div>
          )}
          {dup.isError && !progress.events && (
            <div className="mb-3 rounded border border-rust-500/40 bg-rust-500/10 p-3 text-sm text-rust-400">
              <div className="font-medium">Last attempt failed:</div>
              <div className="font-mono text-xs mt-1 break-words text-rust-300">
                {formatApiError(dup.error)}
              </div>
            </div>
          )}
          {dup.isSuccess && snapshot && (
            <div className="mb-3 rounded border border-green-500/40 bg-green-500/10 p-3 text-sm text-green-400">
              <div>✓ Copied {snapshot.name} to slot {snapshot.to}. Original at slot {snapshot.from} unchanged.</div>
              <div className="text-xs text-wasteland-400 mt-1">
                Don't like the result? Restore the latest backup from the{" "}
                <Link to="/backups" className="underline">Backups page</Link>, or use Delete
                to remove just the copy at slot {snapshot.to}.
              </div>
            </div>
          )}
          <button
            type="button"
            className="btn-primary"
            onClick={() => {
              // If the destination has an active slot lock (vanilla
              // overwrite, quest-bound, hardcoded, etc.) the
              // SlotLockWarningModal carries everything the user
              // needs to confirm — what the slot is, why it matters,
              // and a Continue/Cancel pair. Stacking the basic
              // ConfirmModal on top of it forces 3 clicks for one
              // intent. Skip the basic confirm in that case; route
              // straight to the lock guard.
              const destLockTier = destInfo?.tier;
              const lockNeedsModal = !!destLockTier
                && destLockTier !== "safe"
                && !isLockSuppressed(destLockTier);
              if (lockNeedsModal && dest !== null) {
                lockGuard.guard(dest, () => dup.mutate());
              } else {
                setConfirm(true);
              }
            }}
            // Disable on isSuccess too — a second click would re-run the
            // duplicate (now SLOT_OCCUPIED at the dest) and take another
            // backup. The user can pick a new dest if they want another copy.
            disabled={dup.isPending || dup.isSuccess}
          >
            {dup.isPending
              ? "Copying..."
              : dup.isSuccess
                ? "Copied ✓"
                : dup.isError
                  ? "Try again"
                  : "Copy"}
          </button>
        </section>
      )}

      <ConfirmModal
        open={confirm}
        title="Confirm duplicate"
        body={
          <>
            About to copy <strong>{sourceName}</strong> from slot {source} to slot {dest}.
            The original stays. A backup is taken first.
            {crossCategoryNotice && (
              <div
                className={`mt-2 text-xs ${
                  crossCategoryNotice.severity === "warn" ? "text-yellow-400" : "text-wasteland-300"
                }`}
              >
                {crossCategoryNotice.text}
              </div>
            )}
          </>
        }
        confirmLabel="Copy"
        onConfirm={() => {
          // Close the modal IMMEDIATELY so the progress bar in the Step 3
          // card behind it becomes visible. Pre-#110 the modal stayed up
          // until the mutation completed, hiding the per-file backup
          // labels the user wanted to see.
          setConfirm(false);
          if (dest !== null) lockGuard.guard(dest, () => dup.mutate());
        }}
        onCancel={() => setConfirm(false)}
      />
      {lockGuard.pending && (
        <SlotLockWarningModal
          lock={lockGuard.pending.lock}
          action="duplicate"
          onConfirm={lockGuard.confirm}
          onCancel={lockGuard.cancel}
        />
      )}
    </div>
  );
}
