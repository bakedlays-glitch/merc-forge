import { useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";

import { deleteBackup, formatApiError, listBackups, restoreBackup, takeSnapshot } from "../lib/api";
import ConfirmModal from "../components/ConfirmModal";

function humanSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}

/** Bucket a backup by its age relative to now. Drives the timeline
 * section headers. */
function ageBucket(ts: Date, now: Date): "Today" | "Yesterday" | "This week" | "Earlier" {
  const sameDay = (a: Date, b: Date) =>
    a.getFullYear() === b.getFullYear()
    && a.getMonth() === b.getMonth()
    && a.getDate() === b.getDate();
  if (sameDay(ts, now)) return "Today";
  const y = new Date(now);
  y.setDate(now.getDate() - 1);
  if (sameDay(ts, y)) return "Yesterday";
  // "This week" = within the last 7 days, exclusive of today/yesterday.
  const weekAgo = new Date(now);
  weekAgo.setDate(now.getDate() - 7);
  if (ts >= weekAgo) return "This week";
  return "Earlier";
}

interface BackupEntryView {
  id: string;
  reason: string;
  timestamp: string;
  fileCount: number;
}

export default function Backups() {
  const qc = useQueryClient();
  const backups = useQuery({ queryKey: ["backups"], queryFn: () => listBackups() });
  // User-provided description for the next "Take snapshot now" click.
  // Backend's snapshot reason is a free-form string; prefixing "user: "
  // makes the source obvious in the listing.
  const [snapshotDescription, setSnapshotDescription] = useState("");
  const snapshot = useMutation({
    mutationFn: (reason: string) => takeSnapshot(reason),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["backups"] });
      setSnapshotDescription("");
    },
  });
  const restore = useMutation({
    mutationFn: (id: string) => restoreBackup(id),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["roster"] });
      qc.invalidateQueries({ queryKey: ["backups"] });
      // Restore can roll back any number of slots — invalidate the
      // picker so the next Create/Edit sees the restored state.
      // Bug-review finding E4.
      qc.invalidateQueries({ queryKey: ["slot-picker"] });
      setRestorePrompt(null);
    },
  });
  const del = useMutation({
    mutationFn: (id: string) => deleteBackup(id),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["backups"] });
      setDeletePrompt(null);
    },
  });

  // Restore + Delete are the two highest-stakes ops in the app. Promote
  // them from window.confirm() (which looks identical to a "really delete
  // this notification?" prompt) to the real ConfirmModal — destructive
  // styling, focus-on-Cancel by default, type-to-confirm for Restore.
  const [restorePrompt, setRestorePrompt] = useState<BackupEntryView | null>(null);
  const [deletePrompt, setDeletePrompt] = useState<BackupEntryView | null>(null);

  // Group by age bucket for the timeline view. Computed once per
  // backups.data change; the now-bucket reference is stable for the
  // duration of the render so groupings don't shift mid-frame as time
  // ticks past midnight.
  type BackupItem = NonNullable<typeof backups.data>[number];
  type Bucket = "Today" | "Yesterday" | "This week" | "Earlier";
  const grouped = useMemo<Record<Bucket, BackupItem[]>>(() => {
    const buckets: Record<Bucket, BackupItem[]> = {
      "Today": [], "Yesterday": [], "This week": [], "Earlier": [],
    };
    const now = new Date();
    for (const b of backups.data ?? []) {
      const ts = new Date(b.timestamp);
      buckets[ageBucket(ts, now)].push(b);
    }
    // Newest first within each bucket.
    for (const k of Object.keys(buckets) as Bucket[]) {
      buckets[k].sort((a, b) =>
        new Date(b.timestamp).getTime() - new Date(a.timestamp).getTime(),
      );
    }
    return buckets;
  }, [backups.data]);

  const orderedBuckets: Bucket[] = [
    "Today", "Yesterday", "This week", "Earlier",
  ];

  function reasonFromUI(): string {
    const trimmed = snapshotDescription.trim();
    return trimmed ? `user: ${trimmed}` : "manual";
  }

  return (
    <div className="mx-auto max-w-4xl px-6 py-8">
      <div className="flex items-center justify-between mb-4">
        <h1 className="text-2xl font-bold">Backups</h1>
        <Link to="/" className="btn-ghost text-sm">
          ← Back to Hub
        </Link>
      </div>

      {/* Take-snapshot row with a description input. The text becomes
          the snapshot's reason field, which is what's shown in the
          timeline below — so a clear description ("before MAJOR
          rebalance pass") makes the rollback list far easier to read
          a week later than a sea of "manual" entries. */}
      <div className="card mb-6 flex flex-col gap-2 sm:flex-row sm:items-end">
        <div className="flex-1">
          <label className="block text-xs text-wasteland-400 mb-1">
            Description (optional)
          </label>
          <input
            type="text"
            value={snapshotDescription}
            onChange={(e) => setSnapshotDescription(e.target.value)}
            placeholder="e.g. before rebalance pass, after merc import…"
            maxLength={120}
            disabled={snapshot.isPending}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !snapshot.isPending) {
                snapshot.mutate(reasonFromUI());
              }
            }}
            className="input w-full"
          />
        </div>
        <button
          className="btn-primary text-sm whitespace-nowrap"
          onClick={() => snapshot.mutate(reasonFromUI())}
          disabled={snapshot.isPending}
        >
          {snapshot.isPending ? "Snapshotting…" : "Take snapshot now"}
        </button>
      </div>

      {backups.data && backups.data.length === 0 && (
        <div className="card text-wasteland-300">
          No backups yet. They're created automatically before destructive operations.
        </div>
      )}

      {/* Timeline view — section header per age bucket, entries sorted
          newest-first within each. Buckets with zero entries are
          hidden. */}
      <div className="space-y-6">
        {orderedBuckets.map((bucket) => {
          const entries = grouped[bucket];
          if (!entries || entries.length === 0) return null;
          return (
            <section key={bucket}>
              <h2 className="mb-2 flex items-baseline gap-2 text-sm font-semibold uppercase tracking-wider text-wasteland-400">
                <span>{bucket}</span>
                <span className="text-[10px] font-mono normal-case text-wasteland-600">
                  {entries.length}
                </span>
              </h2>
              <div className="space-y-2">
                {entries.map((b) => {
                  const isDeleting = del.isPending && del.variables === b.id;
                  const isRestoring = restore.isPending && restore.variables === b.id;
                  const view: BackupEntryView = {
                    id: b.id,
                    reason: b.reason,
                    timestamp: new Date(b.timestamp).toLocaleString(),
                    fileCount: b.files.length,
                  };
                  // Strip the "user: " prefix for display — the user
                  // never wrote that prefix, we added it to distinguish
                  // their snapshots from auto-snapshots. Their original
                  // text is what they want to see.
                  const displayReason = b.reason.startsWith("user: ")
                    ? b.reason.slice("user: ".length)
                    : b.reason;
                  const isUserSnapshot = b.reason.startsWith("user: ");
                  return (
                    <div
                      key={b.id}
                      className="card flex items-center justify-between gap-4"
                    >
                      <div className="flex-1 min-w-0">
                        <div className="flex items-center gap-2">
                          {isUserSnapshot && (
                            <span className="badge bg-rust-500/20 text-rust-400 text-[10px]">
                              manual
                            </span>
                          )}
                          <div className="font-medium truncate">{displayReason}</div>
                        </div>
                        <div className="text-xs text-wasteland-400 mt-1">
                          {view.timestamp} · {b.files.length} files ·{" "}
                          {humanSize(b.total_size_bytes)}
                        </div>
                      </div>
                      <div className="flex items-center gap-2 shrink-0">
                        <button
                          className="btn-secondary text-sm"
                          onClick={() => setRestorePrompt(view)}
                          disabled={restore.isPending || isDeleting}
                        >
                          {isRestoring ? "Restoring..." : "Restore"}
                        </button>
                        <button
                          className="btn-ghost text-sm text-rust-400 hover:text-rust-300"
                          onClick={() => setDeletePrompt(view)}
                          disabled={isDeleting || restore.isPending}
                        >
                          {isDeleting ? "Deleting..." : "Delete"}
                        </button>
                      </div>
                    </div>
                  );
                })}
              </div>
            </section>
          );
        })}
      </div>

      {restore.isSuccess && (
        <div className="mt-4 text-sm text-rust-400">
          Restored {restore.data?.files_restored} files.
        </div>
      )}
      {restore.isError && (
        <div className="mt-4 text-sm text-rust-400">
          Restore failed: {formatApiError(restore.error)}
        </div>
      )}
      {del.isError && (
        <div className="mt-4 text-sm text-rust-400">
          Delete failed: {formatApiError(del.error)}
        </div>
      )}

      {/* Restore confirmation — the most consequential op in the app.
          Type-to-confirm gate ("restore") prevents a stray Enter from
          overwriting current files. */}
      <ConfirmModal
        open={!!restorePrompt}
        destructive
        title="Restore this backup?"
        confirmLabel="Restore"
        typeToConfirm="restore"
        busy={restore.isPending}
        body={
          restorePrompt ? (
            <div className="space-y-2">
              <p>
                This will overwrite{" "}
                <span className="font-semibold">{restorePrompt.fileCount} file{restorePrompt.fileCount === 1 ? "" : "s"}</span>{" "}
                in your active install with the snapshot from{" "}
                <span className="font-mono text-wasteland-100">{restorePrompt.timestamp}</span>.
              </p>
              <p className="text-xs text-wasteland-400">
                Snapshot reason:{" "}
                <span className="text-wasteland-200">{restorePrompt.reason}</span>
              </p>
              <p className="text-xs text-amber-300">
                Tip: take a fresh snapshot first if you might want to roll
                back to the CURRENT state — restore overwrites what's there now.
              </p>
            </div>
          ) : null
        }
        onCancel={() => setRestorePrompt(null)}
        onConfirm={() => restorePrompt && restore.mutate(restorePrompt.id)}
      />

      {/* Delete confirmation — drops the snapshot from disk. Recoverable
          only if the user takes a fresh one before any further destructive
          op happens. */}
      <ConfirmModal
        open={!!deletePrompt}
        destructive
        title="Delete this backup snapshot?"
        confirmLabel="Delete snapshot"
        busy={del.isPending}
        body={
          deletePrompt ? (
            <div className="space-y-2">
              <p>
                This permanently removes the snapshot files from disk.
              </p>
              <div className="rounded border border-wasteland-700 bg-wasteland-900 p-2 text-xs">
                <div className="text-wasteland-200">{deletePrompt.reason}</div>
                <div className="text-wasteland-500 font-mono mt-0.5">
                  {deletePrompt.timestamp} · {deletePrompt.fileCount} file
                  {deletePrompt.fileCount === 1 ? "" : "s"}
                </div>
              </div>
            </div>
          ) : null
        }
        onCancel={() => setDeletePrompt(null)}
        onConfirm={() => deletePrompt && del.mutate(deletePrompt.id)}
      />
    </div>
  );
}
