import { useState } from "react";

import type { VoiceDeployment, VoiceUndoResult } from "../../lib/voiceLabSchema";
import { formatApiError } from "../../lib/api";
import { useDialog } from "../DialogProvider";

export function undoAvailability({ status, recoveryRequired }: { status: string; recoveryRequired: boolean | null }) {
  const needsRecoveryReview = recoveryRequired !== false || status === "UNDO_CONFLICT";
  return { canUndo: status === "deployed" && !needsRecoveryReview, needsRecoveryReview };
}

interface Props {
  deployments: readonly VoiceDeployment[];
  recoveryRequired?: boolean | null;
  onUndo: (deploymentId: string) => Promise<VoiceUndoResult>;
  onOpenRecovery: () => void;
  undoingId?: string | null;
}

export default function HistoryPanel({ deployments, recoveryRequired = false, onUndo, onOpenRecovery, undoingId = null }: Props) {
  const { confirm } = useDialog();
  const [conflictedIds, setConflictedIds] = useState<Set<string>>(new Set());
  const [message, setMessage] = useState<string | null>(null);
  const isRecoveryRequired = recoveryRequired !== false || conflictedIds.size > 0;

  async function undo(deploymentId: string) {
    setMessage(null);
    const approved = await confirm({
      title: "Undo this Voice Lab deployment?",
      body: "Undo restores only the reviewed targets after the server confirms they still match the deployment output.",
      confirmLabel: "Undo deployment",
      destructive: true,
    });
    if (!approved) return;
    let result: VoiceUndoResult;
    try {
      result = await onUndo(deploymentId);
    } catch (error) {
      setMessage(formatApiError(error));
      return;
    }
    if (result.status === "UNDO_CONFLICT") {
      setConflictedIds((current) => new Set(current).add(deploymentId));
      setMessage(result.message ?? "Undo needs a recovery review.");
      return;
    }
    setMessage("Undo completed. The deployment remains in history.");
  }

  return <section aria-label="Deployment history" className="mt-4 border border-wasteland-700 bg-wasteland-900/50 p-4">
    <div className="flex flex-wrap items-baseline justify-between gap-2"><div><p className="font-mono text-[10px] uppercase tracking-[.2em] text-wasteland-500">Transaction log</p><h2 className="text-lg font-semibold text-wasteland-100">Deployment history</h2></div>{isRecoveryRequired && <button type="button" className="btn-secondary text-xs" onClick={onOpenRecovery}>Open recovery review</button>}</div>
    {isRecoveryRequired && <p className="mt-3 border border-amber-500/60 bg-amber-950/30 p-2 text-sm text-amber-200">{recoveryRequired === null ? "Recovery status is unavailable. Undo remains blocked until it can be reviewed." : "Recovery is required. Deployment and Undo remain blocked until the server-side recovery review is resolved."}</p>}
    {deployments.length === 0 ? <p className="mt-3 text-sm text-wasteland-400">No reviewed Voice Lab deployments have been recorded.</p> : <ol className="mt-3 divide-y divide-wasteland-800 border border-wasteland-700">
      {deployments.map((deployment) => {
        const availability = undoAvailability({ status: conflictedIds.has(deployment.deploymentId) ? "UNDO_CONFLICT" : deployment.status, recoveryRequired: isRecoveryRequired });
        return <li key={deployment.deploymentId} className="flex flex-wrap items-center justify-between gap-3 px-3 py-2 text-xs"><div><p className="font-mono text-wasteland-200">{deployment.deploymentId.slice(0, 12)}</p><p className="text-wasteland-500">{deployment.targetActions.length} target{deployment.targetActions.length === 1 ? "" : "s"} · {deployment.backupPinned ? "backup pinned" : "backup state unavailable"}</p></div><div className="flex items-center gap-2">{availability.needsRecoveryReview ? <button type="button" className="btn-secondary text-xs" onClick={onOpenRecovery}>Open recovery review</button> : <button type="button" className="btn-ghost text-xs" disabled={!availability.canUndo || undoingId === deployment.deploymentId} onClick={() => void undo(deployment.deploymentId)}>{undoingId === deployment.deploymentId ? "Undoing…" : "Undo"}</button>}</div></li>;
      })}
    </ol>}
    {message && <p className="mt-3 text-xs text-wasteland-300">{message}</p>}
  </section>;
}
