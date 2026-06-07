import { useEffect, useMemo, useRef, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { formatApiError, getHealth, getRoster, listInstalls, moveMercStreaming, type SaveProgressEvent } from "../lib/api";
import ConfirmModal from "../components/ConfirmModal";
import SaveProgressBar from "../components/SaveProgressBar";
import SaveSnapshotBanner from "../components/SaveSnapshotBanner";
import SlotPicker from "../components/SlotPicker";
import { SlotLockWarningModal } from "../components/SlotLockWarningModal";
import { useSlotLockGuard } from "../lib/slotLocks";
import { categoryLabel, useSlotPicker } from "../lib/slotPicker";

export default function Move() {
  const qc = useQueryClient();
  const lockGuard = useSlotLockGuard();
  const health = useQuery({ queryKey: ["health"], queryFn: getHealth });
  const installs = useQuery({ queryKey: ["installs"], queryFn: listInstalls });
  const activeInstallId = health.data?.active_install_id ?? null;

  const roster = useQuery({ queryKey: ["roster"], queryFn: () => getRoster() });

  // Pre-fill source when the roster's "Move to…" action navigates here with
  // ?from=<slot>. The user already picked the slot they want to move; making
  // them re-select it from a dropdown is the bug a user hit.
  const [params] = useSearchParams();
  const initialFrom = params.get("from");
  const [source, setSource] = useState<number | null>(
    initialFrom !== null && /^\d+$/.test(initialFrom) ? Number(initialFrom) : null
  );
  const sourceLocked = source !== null && initialFrom !== null;
  const [destInstall, setDestInstall] = useState<string | null>(null);
  const [dest, setDest] = useState<number | null>(null);
  const [forceOverwrite, setForceOverwrite] = useState(false);
  const [confirm, setConfirm] = useState(false);

  // Sync destInstall default to active install once both are known
  useEffect(() => {
    if (destInstall === null && activeInstallId) setDestInstall(activeInstallId);
  }, [destInstall, activeInstallId]);

  // When destination changes, clear any prior slot pick + force flag
  useEffect(() => {
    setDest(null);
    setForceOverwrite(false);
  }, [destInstall]);

  const isCrossInstall =
    destInstall !== null && activeInstallId !== null && destInstall !== activeInstallId;

  // Roster for the destination install (used to detect occupancy on cross-install)
  const destRoster = useQuery({
    queryKey: ["roster", destInstall ?? "active"],
    queryFn: () => getRoster(destInstall ?? undefined),
    enabled: destInstall !== null,
  });
  const destEntry = dest !== null ? destRoster.data?.find((e) => e.slot === dest) : undefined;
  const destOccupied = !!destEntry && !destEntry.is_empty;

  // Capture a snapshot of source name + slot BEFORE the mutation, so the
  // success/error message remains stable after the roster invalidates.
  const [snapshot, setSnapshot] = useState<{
    name: string;
    from: number;
    to: number;
    toInstall: string | null;
  } | null>(null);

  // Streaming progress (2026-05-23). Previously /move returned a single dict;
  // now it streams NDJSON like /duplicate so the UI can show a progress bar.
  const [progressEvents, setProgressEvents] = useState<SaveProgressEvent[] | null>(null);
  const [progressDone, setProgressDone] = useState(false);
  // Tracks the success-fade timeout so we can cancel it on unmount.
  // See Duplicate.tsx for the full rationale (bug-review #113).
  const fadeTimeoutRef = useRef<number | null>(null);
  useEffect(() => {
    return () => {
      if (fadeTimeoutRef.current !== null) {
        window.clearTimeout(fadeTimeoutRef.current);
        fadeTimeoutRef.current = null;
      }
    };
  }, []);

  const move = useMutation({
    mutationFn: () => {
      if (fadeTimeoutRef.current !== null) {
        window.clearTimeout(fadeTimeoutRef.current);
        fadeTimeoutRef.current = null;
      }
      setProgressEvents([]);
      setProgressDone(false);
      return moveMercStreaming(
        source!,
        dest!,
        {
          to_install_id: isCrossInstall ? destInstall ?? undefined : undefined,
          force: isCrossInstall ? forceOverwrite : false,
        },
        (ev) => {
          setProgressEvents((prev) => (prev ? [...prev, ev] : [ev]));
        },
      );
    },
    onMutate: () => {
      const entry = roster.data?.find((e) => e.slot === source);
      const name = entry?.nickname ?? entry?.name ?? `slot ${source}`;
      setSnapshot({ name, from: source!, to: dest!, toInstall: isCrossInstall ? destInstall : null });
    },
    onSuccess: () => {
      setProgressDone(true);
      qc.invalidateQueries({ queryKey: ["roster"] });
      qc.invalidateQueries({ queryKey: ["roster", destInstall ?? "active"] });
      qc.invalidateQueries({ queryKey: ["backups"] });
      qc.invalidateQueries({ queryKey: ["slot", source] });
      qc.invalidateQueries({ queryKey: ["slot", dest] });
      // Slot picker — both source-cleared and dest-occupied affect
      // the picker's tier/category surface. Bug-review finding E4.
      qc.invalidateQueries({ queryKey: ["slot-picker"] });
      fadeTimeoutRef.current = window.setTimeout(() => {
        setProgressEvents(null);
        setProgressDone(false);
        fadeTimeoutRef.current = null;
      }, 2500);
    },
    onError: () => {
      setProgressDone(true);
    },
    // No onSettled here — modal is closed in onConfirm so the progress
    // bar isn't hidden during backup+move. Bug-review #110.
  });

  const destInstallInfo = installs.data?.find((i) => i.id === destInstall);
  const activeInstallInfo = installs.data?.find((i) => i.id === activeInstallId);

  const filled = (roster.data ?? []).filter((e) => !e.is_empty);

  const sourceEntry = roster.data?.find((e) => e.slot === source);
  const sourceName = sourceEntry?.nickname ?? sourceEntry?.name ?? "?";

  const picker = useSlotPicker();
  const sourceInfo = source !== null ? picker.data?.slots[source] : undefined;
  const destInfo = dest !== null ? picker.data?.slots[dest] : undefined;
  const sourceClass = sourceInfo?.category ?? null;
  const destClass = destInfo?.category ?? null;
  // Parallel structure to Duplicate's notice (see Duplicate.tsx for the
  // full rationale). Move IS more destructive than Duplicate — the
  // source is wiped — but the cross-category effects on the DEST are
  // identical, so we use the same severity/text split here.
  const crossCategoryNotice = useMemo<{ severity: "info" | "warn"; text: string } | null>(() => {
    if (sourceClass === null || destClass === null) return null;
    const sourceType = sourceEntry?.profile_type ?? null;

    if (sourceType === 1 && destClass === "unassigned") {
      return {
        severity: "info",
        text: `Slot ${dest} isn't currently on the AIM roster — MercForge will register ${sourceName} there automatically so they stay hireable on AIM after the move. (A fresh AimBioID is computed; the old AIM row at slot ${source} is removed.)`,
      };
    }

    if (sourceType === 2 && destClass === "unassigned") {
      return {
        severity: "info",
        text: `Slot ${dest} isn't currently on Speck's M.E.R.C. roster — MercForge will register ${sourceName} there automatically. (A fresh MercBioID is computed; the old row at slot ${source} is removed.)`,
      };
    }

    if (sourceType === 1 && destClass === "merc") {
      return {
        severity: "warn",
        text: `Slot ${dest} has a leftover M.E.R.C. row from a previous occupant. After the move, ${sourceName} would appear on BOTH AIM (new row) and M.E.R.C. (stale row) — pick an unassigned slot, or clear MercAvailability.xml at ${dest} first.`,
      };
    }

    if (sourceType === 2 && destClass === "aim") {
      return {
        severity: "warn",
        text: `Slot ${dest} has a leftover AIM row from a previous occupant. After the move, ${sourceName} would appear on BOTH M.E.R.C. (new row) and AIM (stale row) — pick an unassigned slot, or clear AIMAvailability.xml at ${dest} first.`,
      };
    }

    if ((sourceType === 3 || sourceType === 4)
        && (destClass === "aim" || destClass === "merc")) {
      const typeLabel = sourceType === 3 ? "NPC" : "RPC";
      const site = destClass === "aim" ? "AIM website" : "M.E.R.C. website (Speck's service)";
      return {
        severity: "warn",
        text: `${sourceName} is ${typeLabel} (scripted). The move keeps Type=${typeLabel}, so they WON'T appear on the ${site} even though slot ${dest} has a row there. Change Type to 1 (AIM) or 2 (M.E.R.C.) after the move if you want them hireable.`,
      };
    }

    if (sourceType === 1 && (destClass === "rpc" || destClass === "npc")) {
      return {
        severity: "warn",
        text: `Slot ${dest} is in the engine's named ${destClass.toUpperCase()} range. Quest scripts may call this slot by name — moving ${sourceName} here redirects whatever scripted dialogue used to play for the original occupant.`,
      };
    }

    if (sourceClass === destClass) return null;

    return {
      severity: "info",
      text: `Slot category changes from ${categoryLabel(sourceClass)} to ${categoryLabel(destClass)}. ${sourceName}'s Type stays the same (${sourceType ?? "?"}); MercForge writes whichever XML rows are needed so they stay on the same hire list.`,
    };
  }, [sourceClass, destClass, source, dest, sourceName, sourceEntry]);

  return (
    <div className="mx-auto max-w-4xl px-6 py-8 space-y-6">
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-bold">Cut Merc</h1>
        <Link to="/" className="btn-ghost text-sm">← Back to Hub</Link>
      </div>

      {sourceLocked ? (
        <>
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
          {/* Save-snapshot warning for the source slot — moving a merc
              who's hired in an existing save doesn't change anything in
              the save; their old slot still has the SOLDIERTYPE snapshot.
              Hidden when no existing save references this slot. */}
          {source !== null && <SaveSnapshotBanner slot={source} action="move" />}
        </>
      ) : (
        <section className="card">
          <h2 className="text-lg font-semibold mb-3">Step 1: Pick source merc</h2>
          <select
            className="input max-w-md"
            value={source ?? ""}
            onChange={(e) => setSource(e.target.value ? Number(e.target.value) : null)}
          >
            <option value="">Choose a merc to move...</option>
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

      {/* Save-snapshot warning shows for the source slot once one is
          picked, in both the locked and dropdown-picked paths. Hidden
          when no existing save references this slot. */}
      {!sourceLocked && source !== null && <SaveSnapshotBanner slot={source} action="move" />}

      {source !== null && (installs.data?.length ?? 0) > 1 && (
        <section className="card">
          <h2 className="text-lg font-semibold mb-3">Step 2a: Destination install</h2>
          <select
            className="input max-w-xl"
            value={destInstall ?? ""}
            onChange={(e) => setDestInstall(e.target.value || null)}
          >
            {installs.data?.map((info) => (
              <option key={info.id} value={info.id}>
                {info.mod_display}{info.id === activeInstallId ? " (this install)" : ""} — {info.path}
              </option>
            ))}
          </select>
          {isCrossInstall && (
            <p className="text-xs text-yellow-300 mt-2">
              Cross-install move: {sourceName} will be exported from{" "}
              <span className="font-mono">{activeInstallInfo?.mod_display ?? "source"}</span> and
              imported into{" "}
              <span className="font-mono">{destInstallInfo?.mod_display ?? "target"}</span>.
              A backup is taken on both sides. The source slot is cleared after the target write succeeds.
            </p>
          )}
        </section>
      )}

      {source !== null && (
        <section className="card">
          <h2 className="text-lg font-semibold mb-3">
            Step 2{(installs.data?.length ?? 0) > 1 ? "b" : ""}: Pick destination slot for{" "}
            <span className="text-rust-400">{sourceName}</span>
          </h2>
          <p className="text-sm text-wasteland-300 mb-4">
            {isCrossInstall
              ? `Slots already filled in ${destInstallInfo?.mod_display ?? "the target install"} appear greyed out. Tick "overwrite" below if you want to replace one.`
              : "Only empty slots are selectable. The slot-category badge below the picker updates as you select."}
          </p>
          <SlotPicker
            selected={dest}
            onSelect={setDest}
            allowFilled={isCrossInstall}
            installId={isCrossInstall ? destInstall ?? undefined : undefined}
          />
          {destClass && (
            <div className="mt-3 text-xs text-wasteland-400">
              Slot {dest} is <span className="badge bg-wasteland-700 text-wasteland-200">{categoryLabel(destClass)}</span>
            </div>
          )}
          {isCrossInstall && destOccupied && (
            <label className="mt-3 flex items-center gap-2 text-sm text-wasteland-200">
              <input
                type="checkbox"
                checked={forceOverwrite}
                onChange={(e) => setForceOverwrite(e.target.checked)}
              />
              Overwrite {destEntry?.nickname ?? destEntry?.name ?? `slot ${dest}`} at the target
            </label>
          )}
        </section>
      )}

      {source !== null && dest !== null && (
        <section className="card">
          <h2 className="text-lg font-semibold mb-2">Step 3: Confirm</h2>
          <p className="text-sm text-wasteland-200 mb-3">
            Cut <span className="text-rust-400 font-medium">{sourceName}</span> from slot{" "}
            <span className="font-mono">{source}</span> ({sourceClass ? categoryLabel(sourceClass) : "?"}) to slot{" "}
            <span className="font-mono">{dest}</span> ({destClass ? categoryLabel(destClass) : "?"}).
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
            This rewrites MercProfiles.xml, AIMAvailability.xml (recomputing AimBioID for the
            new slot), MercStartingGear.xml, and relocates the EDT bio. A backup is taken
            automatically before any writes — you can roll back from Backups if needed.
          </p>
          {progressEvents && (
            <div className="mb-3">
              <SaveProgressBar
                events={progressEvents}
                done={progressDone}
                error={move.error}
              />
            </div>
          )}
          {move.isError && !progressEvents && (
            <div className="mb-3 rounded border border-rust-500/40 bg-rust-500/10 p-3 text-sm text-rust-400">
              <div className="font-medium">Last attempt failed:</div>
              <div className="font-mono text-xs mt-1 break-words text-rust-300">
                {formatApiError(move.error)}
              </div>
              <div className="text-wasteland-400 text-xs mt-2">
                Adjust source/dest or restore from Backups if anything looks wrong in-game.
              </div>
            </div>
          )}
          {move.isSuccess && snapshot && (() => {
            // Pull `report` into a local so TypeScript's narrowing inside
            // the && chain doesn't lose it (TS2339 otherwise: each fresh
            // access goes back through SaveProgressEvent['report?'] which
            // is optional). Cross-install moves carry the bundle-pipeline
            // report; same-install moves don't, so `voiceClipsCopied`
            // gracefully degrades to undefined.
            const report = move.data?.report;
            const voiceClipsCopied = report?.voice_clips_copied ?? 0;
            const portraitCompiled = report?.portrait_compiled ?? false;
            return (
              <div className="mb-3 rounded border border-green-500/40 bg-green-500/10 p-3 text-sm text-green-400">
                <div>
                  ✓ Cut {snapshot.name} from slot {snapshot.from} to slot {snapshot.to}
                  {snapshot.toInstall && ` in ${installs.data?.find((i) => i.id === snapshot.toInstall)?.mod_display ?? "the other install"}`}.
                </div>
                {voiceClipsCopied > 0 && (
                  <div className="text-xs text-wasteland-400 mt-1">
                    {voiceClipsCopied} voice clip(s) copied.
                    {portraitCompiled && " Portrait STIs compiled."}
                  </div>
                )}
                <div className="text-xs text-wasteland-400 mt-1">
                  Want to undo? Restore the most recent backup from the{" "}
                  <Link to="/backups" className="underline">Backups page</Link>
                  {!snapshot.toInstall && ` (or move slot ${snapshot.to} back to slot ${snapshot.from})`}.
                </div>
              </div>
            );
          })()}
          <button
            type="button"
            className="btn-primary"
            onClick={() => setConfirm(true)}
            // Disable on isSuccess too so a second click doesn't re-cut
            // (the source is now empty, but the call would still hit the
            // server and produce a confusing 404 / second backup).
            disabled={
              move.isPending
              || move.isSuccess
              || (isCrossInstall && destOccupied && !forceOverwrite)
            }
          >
            {move.isPending
              ? "Cutting..."
              : move.isSuccess
                ? "Cut ✓"
                : move.isError
                  ? "Try again"
                  : "Cut"}
          </button>
        </section>
      )}

      <ConfirmModal
        open={confirm}
        title="Confirm cut"
        body={
          <>
            About to cut <strong>{sourceName}</strong> from slot {source}
            {isCrossInstall && activeInstallInfo
              ? ` in ${activeInstallInfo.mod_display}`
              : ""}{" "}
            to slot {dest}
            {isCrossInstall && destInstallInfo
              ? ` in ${destInstallInfo.mod_display}`
              : ""}
            . A backup will be taken first on
            {isCrossInstall ? " both installs" : " this install"}.
            {isCrossInstall && destOccupied && forceOverwrite && (
              <div className="mt-2 text-rust-400 text-xs">
                Target slot is occupied — the existing merc will be overwritten.
              </div>
            )}
            {crossCategoryNotice && !isCrossInstall && (
              <div
                className={
                  crossCategoryNotice.severity === "warn"
                    ? "mt-2 text-yellow-400 text-xs"
                    : "mt-2 text-sky-300 text-xs"
                }
              >
                {crossCategoryNotice.text}
              </div>
            )}
          </>
        }
        confirmLabel="Cut"
        onConfirm={() => {
          // Close the modal IMMEDIATELY so the progress bar (rendered in
          // the Step 3 card behind it) is visible during backup+move.
          // Pre-#110 the modal stayed up until onSettled, hiding the
          // per-file backup labels.
          setConfirm(false);
          if (dest !== null) lockGuard.guard(dest, () => move.mutate());
        }}
        onCancel={() => setConfirm(false)}
      />
      {lockGuard.pending && (
        <SlotLockWarningModal
          lock={lockGuard.pending.lock}
          action="move"
          onConfirm={lockGuard.confirm}
          onCancel={lockGuard.cancel}
        />
      )}
    </div>
  );
}
