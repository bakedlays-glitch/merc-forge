import { useMemo } from "react";

import { ApiError, formatApiError, type SaveProgressEvent } from "../lib/api";

interface Props {
  /** All progress events received so far. null = no save in flight (hidden). */
  events: SaveProgressEvent[] | null;
  /** True once the final {done: true, ...} event landed (success or failure). */
  done: boolean;
  /** Set when the React Query mutation reported an error. */
  error: unknown;
}

interface StepRow {
  step: string;
  label: string;
  status: "start" | "progress" | "done";
  /** Optional progress sub-label, e.g. "12 / 47 files" for backup. */
  subLabel?: string;
  /** For the backup step: the list of file paths emitted via per-file
   *  progress events so far. Accumulates so the user can SEE what was
   *  backed up — pre-fix the label updated faster than React could
   *  paint and the user just saw "Backing up files…" with no detail. */
  files?: string[];
}

const STEP_FALLBACK_LABELS: Record<string, string> = {
  backup: "Backing up files",
  profiles: "Writing merc profile",
  edt: "Writing biography",
  aim_avail: "Writing AIM availability",
  merc_avail: "Writing MERC availability",
  gear: "Writing starting gear",
  copy: "Copying merc data",
  move: "Moving merc data",
};

/** Roll up a stream of SaveProgressEvent into one row per step. The latest
 * `status` win; backup's per-file "progress" events ACCUMULATE into a
 * `files` list under the row instead of overwriting the label. Pre-fix
 * each file flashed for ~50ms then got replaced by the next — the user
 * couldn't see what was backed up. Now the list persists and stays
 * visible after the step finishes. */
function rollupRows(events: SaveProgressEvent[]): StepRow[] {
  const byStep = new Map<string, StepRow>();
  const order: string[] = [];

  for (const ev of events) {
    if (!ev.step || ev.done) continue;
    if (!byStep.has(ev.step)) {
      order.push(ev.step);
      byStep.set(ev.step, {
        step: ev.step,
        label: STEP_FALLBACK_LABELS[ev.step] ?? ev.label ?? ev.step,
        status: ev.status ?? "start",
      });
    }
    const row = byStep.get(ev.step)!;
    if (ev.status) row.status = ev.status;

    if (ev.status === "progress" && ev.index != null && ev.total != null) {
      row.subLabel = `${ev.index} / ${ev.total} files`;
      // Capture the per-file relative path. The sidecar emits
      // `Backing up: faces/220.sti` as the label; the file part comes
      // after the first colon-space. If the format doesn't match, fall
      // back to using the whole label so we never silently lose info.
      if (ev.label) {
        const m = /^[^:]+:\s*(.+)$/.exec(ev.label);
        const filePath = m && m[1] ? m[1].trim() : ev.label;
        row.files = row.files ? [...row.files, filePath] : [filePath];
      }
    }
    // On "done": keep the file list visible (it's the receipt of what
    // got backed up). Switch the label to "Backed up N files" so the
    // user gets a clear count instead of the in-progress phrasing.
    if (ev.status === "done") {
      row.subLabel = undefined;
      if (row.files && row.files.length > 0 && row.step === "backup") {
        row.label = `Backed up ${row.files.length} file${row.files.length === 1 ? "" : "s"}`;
      } else {
        row.label = STEP_FALLBACK_LABELS[ev.step] ?? row.label;
      }
    }
  }

  return order.map((s) => byStep.get(s)!);
}

export default function SaveProgressBar({ events, done, error }: Props) {
  const rows = useMemo(() => (events ? rollupRows(events) : []), [events]);

  // Pull the final event to know success vs failure + error context.
  const finalEvent = useMemo<SaveProgressEvent | null>(() => {
    if (!events) return null;
    for (let i = events.length - 1; i >= 0; i--) {
      const ev = events[i];
      if (ev && ev.done) return ev;
    }
    return null;
  }, [events]);

  if (!events) return null;

  const succeeded = done && finalEvent?.ok === true;
  const failed = done && (finalEvent?.ok === false || error != null);

  // If we don't have a final event yet but React Query reported an error
  // (e.g. the stream dropped mid-flight), build a synthetic error message
  // from the ApiError detail.
  const fallbackErrorMessage = error != null && finalEvent?.ok !== false
    ? formatApiError(error)
    : null;

  return (
    <div
      className={`rounded border p-3 space-y-2 text-sm transition-opacity ${
        failed
          ? "border-rust-500/40 bg-rust-500/10"
          : succeeded
            ? "border-emerald-500/40 bg-emerald-500/10"
            : "border-wasteland-700 bg-wasteland-800/60"
      }`}
    >
      <ul className="space-y-1">
        {rows.map((row) => (
          <li key={row.step} className="space-y-0.5">
            <div className="flex items-baseline gap-2">
              <StepIcon status={row.status} failedHere={failed && finalEvent?.error_step === row.step} />
              <span
                className={
                  failed && finalEvent?.error_step === row.step
                    ? "text-rust-300 font-medium"
                    : row.status === "done"
                      ? "text-wasteland-300"
                      : "text-wasteland-100"
                }
              >
                {row.label}
              </span>
              {row.subLabel && (
                <span className="text-xs text-wasteland-500 font-mono">{row.subLabel}</span>
              )}
            </div>
            {/* Per-file list: accumulates during the step, stays visible
                after the step completes so the user has a "receipt" of
                what was actually written. Scrollable so a 100-file
                backup doesn't push the rest of the UI off-screen. */}
            {row.files && row.files.length > 0 && (
              <ul className="ml-6 max-h-32 overflow-y-auto rounded border border-wasteland-700/60 bg-wasteland-950/40 px-2 py-1 text-[10px] font-mono text-wasteland-400 space-y-0.5">
                {row.files.map((f, i) => (
                  <li key={`${row.step}-${i}`} className="truncate" title={f}>
                    {f}
                  </li>
                ))}
              </ul>
            )}
          </li>
        ))}
      </ul>
      {succeeded && (
        <div className="text-emerald-300 font-medium pt-1">Saved.</div>
      )}
      {failed && (
        <FailureMessage finalEvent={finalEvent} fallbackMessage={fallbackErrorMessage} />
      )}
    </div>
  );
}

function StepIcon({
  status,
  failedHere,
}: {
  status: "start" | "progress" | "done";
  failedHere: boolean;
}) {
  if (failedHere) {
    return <span className="text-rust-400 font-mono w-4 inline-block">✕</span>;
  }
  if (status === "done") {
    return <span className="text-emerald-400 font-mono w-4 inline-block">✓</span>;
  }
  // start or progress
  return (
    <span
      className="inline-block w-4 h-4 rounded-full border-2 border-wasteland-600 border-t-rust-400 animate-spin"
      aria-label="in progress"
    />
  );
}

function FailureMessage({
  finalEvent,
  fallbackMessage,
}: {
  finalEvent: SaveProgressEvent | null;
  fallbackMessage: string | null;
}) {
  // Synthesize an ApiError-shaped detail so formatApiError gives us the
  // mapped ERROR_MESSAGES text (SAVE_FAILED / SAVE_FAILED_ROLLBACK_FAILED).
  const message = useMemo(() => {
    if (!finalEvent || finalEvent.ok !== false) return fallbackMessage ?? "Save failed.";
    const synthetic = new ApiError(500, {
      detail: {
        error: finalEvent.error,
        message: finalEvent.message,
        error_step: finalEvent.error_step,
        steps_completed: finalEvent.steps_completed,
      },
    });
    return formatApiError(synthetic);
  }, [finalEvent, fallbackMessage]);

  const rollbackOk = finalEvent?.rollback_ok ?? false;

  return (
    <div className="text-rust-200 text-xs space-y-1 pt-1">
      <div>{message}</div>
      {finalEvent?.error_step && (
        <div className="text-wasteland-400">
          Failed at: <span className="font-mono text-rust-300">{finalEvent.error_step}</span>
          {finalEvent.steps_completed && finalEvent.steps_completed.length > 0 && (
            <>
              {" · Completed: "}
              <span className="font-mono">{finalEvent.steps_completed.join(", ")}</span>
            </>
          )}
        </div>
      )}
      {finalEvent?.backup_id && (
        <div className="text-wasteland-400">
          Backup snapshot: <span className="font-mono">{finalEvent.backup_id}</span>
          {rollbackOk
            ? " · auto-restored ✓"
            : " · auto-restore FAILED — restore manually from Backups page"}
        </div>
      )}
    </div>
  );
}
