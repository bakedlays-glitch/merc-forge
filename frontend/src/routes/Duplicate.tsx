import { useEffect, useMemo, useRef, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { duplicateMercStreaming, formatApiError, getRoster, type SaveProgressEvent } from "../lib/api";
import ConfirmModal from "../components/ConfirmModal";
import SaveProgressBar from "../components/SaveProgressBar";
import SlotPicker from "../components/SlotPicker";
import { SlotLockWarningModal } from "../components/SlotLockWarningModal";
import { isLockSuppressed, useSlotLockGuard } from "../lib/slotLocks";
import { categoryLabel, useSlotPicker } from "../lib/slotPicker";

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
  // Live progress events from the streaming /duplicate endpoint. Pre-streaming
  // (2026-05-23) the Copy button just showed "Copying..." with no feedback
  // and a 30s default timeout that surfaced as "Couldn't reach the sidecar"
  // on a heavily-modded install where backup-snapshot can take longer than 30s.
  const [progressEvents, setProgressEvents] = useState<SaveProgressEvent[] | null>(null);
  const [progressDone, setProgressDone] = useState(false);
  // Track the success-fade timeout so we can cancel it on unmount or
  // when a fresh mutation starts. Pre-#113 the timeout fired
  // unconditionally — if the user navigated away inside the 2.5s
  // window, React warned "Can't perform a state update on an unmounted
  // component" and the cleanup never ran.
  const fadeTimeoutRef = useRef<number | null>(null);

  // When the roster supplied ?from= for a filled source, also fill in the
  // sourceName lookup as soon as roster.data loads. No state needed — the
  // existing sourceEntry computation below does this.
  useEffect(() => {
    // no-op; placeholder kept so a future maintainer sees this section
    // exists and where the URL-param branch lives.
  }, [initialFrom, initialTo]);

  // Cancel any pending fade timeout on unmount.
  useEffect(() => {
    return () => {
      if (fadeTimeoutRef.current !== null) {
        window.clearTimeout(fadeTimeoutRef.current);
        fadeTimeoutRef.current = null;
      }
    };
  }, []);

  const dup = useMutation({
    mutationFn: () => {
      // Cancel any leftover fade from a previous run so the old events
      // don't blink in and then out while the new mutation starts.
      if (fadeTimeoutRef.current !== null) {
        window.clearTimeout(fadeTimeoutRef.current);
        fadeTimeoutRef.current = null;
      }
      setProgressEvents([]);
      setProgressDone(false);
      return duplicateMercStreaming(source!, dest!, undefined, (ev) => {
        setProgressEvents((prev) => (prev ? [...prev, ev] : [ev]));
      });
    },
    onMutate: () => {
      const entry = roster.data?.find((e) => e.slot === source);
      const name = entry?.nickname ?? entry?.name ?? `slot ${source}`;
      setSnapshot({ name, from: source!, to: dest! });
    },
    onSuccess: () => {
      setProgressDone(true);
      qc.invalidateQueries({ queryKey: ["roster"] });
      // Slot picker — bug-review finding E4.
      qc.invalidateQueries({ queryKey: ["slot-picker"] });
      // Destination slot detail + voice, so an open Edit / voice view of the
      // new slot refreshes instead of showing stale pre-duplicate data
      // (matches Move/Create's dest invalidation).
      if (dest !== null) {
        qc.invalidateQueries({ queryKey: ["slot", dest] });
        qc.invalidateQueries({ queryKey: ["voice", dest] });
      }
      // Fade the progress bar out a moment after success so the user
      // sees the green ✓ before it disappears. Tracked via ref so
      // unmount can cancel.
      fadeTimeoutRef.current = window.setTimeout(() => {
        setProgressEvents(null);
        setProgressDone(false);
        fadeTimeoutRef.current = null;
      }, 2500);
    },
    onError: () => {
      // Keep the progress bar visible on error so the user can see which
      // step failed (matches Edit.tsx's behavior).
      setProgressDone(true);
    },
    // No onSettled here — modal is closed in onConfirm below so the
    // progress bar isn't hidden behind the still-open ConfirmModal
    // during backup+copy. Pre-#110 the modal closed onSettled (after
    // mutation completed), but the per-file backup label was invisible
    // because the modal sat z-50 over the Step 3 card. Bug-review #110.
  });

  const filled = (roster.data ?? []).filter((e) => !e.is_empty);
  const sourceEntry = roster.data?.find((e) => e.slot === source);
  const sourceName = sourceEntry?.nickname ?? sourceEntry?.name ?? "?";
  const sourceInfo = source !== null ? picker.data?.slots[source] : undefined;
  const destInfo = dest !== null ? picker.data?.slots[dest] : undefined;
  const sourceClass = sourceInfo?.category ?? null;
  const destClass = destInfo?.category ?? null;

  // What this is: a single notice underneath the confirm prompt that
  // tells the user what'll be different about the duplicate vs the
  // original AT THE DESTINATION. Two flavors:
  //
  //   - severity: "info"  → blue, positive ("here's what we'll do for you")
  //   - severity: "warn"  → yellow, attention-needed ("you might not want this")
  //
  // Pre-#90 this was always a yellow "Slot category change" warning
  // even for the routine case where the duplicate WILL appear on AIM
  // (because relocator.duplicate auto-writes the row, see
  // sidecar/mercwizard_core/relocator.py:290-297). Telling the user the
  // copy "won't appear" and then telling them MercForge will write the
  // row anyway is internally contradictory and was a user's #1 source of
  // Duplicate-flow confusion.
  const crossCategoryNotice = useMemo<{ severity: "info" | "warn"; text: string } | null>(() => {
    if (sourceClass === null || destClass === null) return null;
    const sourceType = sourceEntry?.profile_type ?? null;

    // Type=1 (AIM) source landing in an unassigned slot. The duplicate
    // route's relocator unconditionally writes a fresh AIMAvailability
    // row for the dest, so the copy IS hireable on AIM after the write.
    // No warning — but the user benefits from a confirmation of what's
    // about to happen since the dest slot looked "empty" in the picker.
    if (sourceType === 1 && destClass === "unassigned") {
      return {
        severity: "info",
        text: `Slot ${dest} isn't currently on the AIM roster — MercForge will register it automatically so the copy is hireable on AIM right away. (A fresh AimBioID + bio entry are written at the same time.)`,
      };
    }

    // Type=1 (AIM) source landing on a slot that already has a M.E.R.C.
    // row. Duplicate refuses occupied dest slots so this case only
    // fires when the dest had a M.E.R.C. row from a previous occupant
    // that survived delete (rare; usually merc_availability.remove
    // cleans it up). Flag it because the duplicate would write an AIM
    // row alongside the lingering MERC row, leaving the merc on BOTH
    // hire lists.
    if (sourceType === 1 && destClass === "merc") {
      return {
        severity: "warn",
        text: `Slot ${dest} still has a leftover M.E.R.C. row from a previous occupant. After the copy, the new merc would appear on BOTH AIM (new row) and M.E.R.C. (stale row) — usually not what you want. Pick an unassigned slot, or clean up MercAvailability.xml first.`,
      };
    }

    // Type=2 (M.E.R.C. / Speck's) source landing in an unassigned slot.
    // Same auto-write story as AIM but for MercAvailability.xml.
    if (sourceType === 2 && destClass === "unassigned") {
      return {
        severity: "info",
        text: `Slot ${dest} isn't currently on Speck's M.E.R.C. roster — MercForge will register it automatically so the copy shows up on the M.E.R.C. website. (A fresh MercBioID + bio entry are written at the same time.)`,
      };
    }

    // RPC / NPC source (Type 3/4) — the duplicate inherits the unhireable
    // Type regardless of the dest slot's category, so even if dest is
    // categorized "aim" the copy won't show up on the AIM laptop. This
    // genuinely surprises people.
    if ((sourceType === 3 || sourceType === 4)
        && (destClass === "aim" || destClass === "merc")) {
      const sourceTypeLabel = sourceType === 3 ? "NPC" : "RPC";
      const siteLabel = destClass === "aim" ? "AIM website" : "M.E.R.C. website (Speck's service)";
      return {
        severity: "warn",
        text: `${sourceName} is ${sourceTypeLabel} (scripted). The duplicate stays ${sourceTypeLabel}, so it WON'T appear on the ${siteLabel} even though slot ${dest} has a row there. Change Type to 1 (AIM) or 2 (M.E.R.C.) on the duplicate after the copy if you want it hireable.`,
      };
    }

    // Same-category copy → nothing surprising, skip the notice.
    if (sourceClass === destClass) return null;

    // Generic fallback for any combination we didn't enumerate above.
    return {
      severity: "info",
      text: `Slot category changes from ${categoryLabel(sourceClass)} to ${categoryLabel(destClass)}. The duplicate's Type stays the same (${sourceType ?? "?"}); MercForge will write whichever XML rows are needed so the copy lands in the same hire list as the original.`,
    };
  }, [sourceClass, destClass, dest, sourceEntry, sourceName]);

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

      {sourceLocked ? (
        <section className="card">
          <div className="flex items-center justify-between gap-3">
            <div>
              <div className="text-xs uppercase text-wasteland-500 mb-1">Source merc</div>
              <div className="text-wasteland-100">
                <span className="font-mono text-rust-400">Slot {source}</span>
                {" · "}
                <span className="font-medium">{sourceName}</span>
                {sourceClass && (
                  <span className="badge bg-wasteland-700 text-wasteland-200 ml-2">{categoryLabel(sourceClass)}</span>
                )}
              </div>
            </div>
            <button
              type="button"
              className="text-xs text-rust-400 hover:underline underline-offset-2"
              onClick={() => setSource(null)}
            >
              Change
            </button>
          </div>
        </section>
      ) : (
        <section className="card">
          <h2 className="text-lg font-semibold mb-3">Step 1: Pick the merc to copy</h2>
          <select
            className="input max-w-md"
            value={source ?? ""}
            onChange={(e) => setSource(e.target.value ? Number(e.target.value) : null)}
          >
            <option value="">Choose a merc...</option>
            {filled.map((e) => (
              <option key={e.slot} value={e.slot}>
                Slot {e.slot}: {e.nickname ?? e.name}
              </option>
            ))}
          </select>
          {sourceClass && (
            <div className="mt-2 text-xs text-wasteland-400">
              Slot {source} is <span className="badge bg-wasteland-700 text-wasteland-200">{categoryLabel(sourceClass)}</span>
            </div>
          )}
        </section>
      )}

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
          {progressEvents && (
            <div className="mb-3">
              <SaveProgressBar
                events={progressEvents}
                done={progressDone}
                error={dup.error}
              />
            </div>
          )}
          {dup.isError && !progressEvents && (
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
