import { useState } from "react";

import { useDialog } from "../DialogProvider";
import { formatApiError } from "../../lib/api";
import type { VoiceDeployPlan, VoiceProfile } from "../../lib/voiceLabSchema";

export function shortenHash(value: string | null): string {
  return value ? value.slice(0, 12) : "—";
}

export function deploymentActionLabel(kind: VoiceDeployPlan["targetActions"][number]["kind"]): string {
  if (kind === "create") return "Create";
  if (kind === "remove_shadowing_variant") return "Remove shadowing variant";
  return "Replace";
}

interface Props {
  plan: VoiceDeployPlan;
  profiles: readonly VoiceProfile[];
  onDeploy: (planId: string) => Promise<void>;
  onClose: () => void;
  deploying?: boolean;
}

/**
 * A deliberately literal deploy receipt: it is the last client-side review,
 * not a substitute for the sidecar's locked stale-plan validation.
 */
export default function DeployDialog({ plan, profiles, onDeploy, onClose, deploying = false }: Props) {
  const { confirm } = useDialog();
  const [error, setError] = useState<string | null>(null);
  const namesById = new Map(profiles.map((profile) => [profile.profileId, profile.name || `Profile ${profile.profileId}`]));
  const sharedBank = plan.affectedProfiles.length > 1;
  const blocked = plan.gameRunning !== false || plan.toolchainAvailable !== true || plan.recoveryRequired !== false;

  async function deploy() {
    const approved = await confirm({
      title: "Deploy reviewed Voice Lab changes?",
      body: `This writes ${plan.targetActions.length} reviewed target${plan.targetActions.length === 1 ? "" : "s"} and keeps a pinned backup for Undo. The sidecar will recheck every source and target before writing.`,
      confirmLabel: "Deploy reviewed changes",
      destructive: true,
    });
    if (!approved) return;
    setError(null);
    try {
      await onDeploy(plan.planId);
    } catch (caught) {
      setError(formatApiError(caught));
    }
  }

  return (
    <section aria-label="Deploy review" className="mt-4 border border-rust-500/70 bg-wasteland-900 p-4 shadow-[inset_3px_0_0_#d05a36]">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <p className="font-mono text-[10px] uppercase tracking-[.2em] text-rust-400">Reviewed write manifest</p>
          <h2 className="text-lg font-semibold text-wasteland-100">Deploy selected fix</h2>
        </div>
        <button type="button" className="btn-ghost text-xs" onClick={onClose} disabled={deploying}>Close review</button>
      </div>

      {sharedBank && (
        <p className="mt-3 border border-amber-500/60 bg-amber-950/30 px-3 py-2 text-sm text-amber-200">
          Shared voice bank: this deployment affects {plan.affectedProfiles.length} attached profiles.
        </p>
      )}
      <dl className="mt-3 grid gap-x-4 gap-y-1 text-xs sm:grid-cols-[9rem_1fr]">
        <dt className="text-wasteland-500">Affected profiles</dt>
        <dd className="text-wasteland-200">{plan.affectedProfiles.map((id) => `${namesById.get(id) ?? `Profile ${id}`} (${id})`).join(", ") || "None"}</dd>
        <dt className="text-wasteland-500">Recovery backup</dt>
        <dd className={plan.backupPinned ? "text-emerald-300" : "text-wasteland-300"}>{plan.backupPinned ? "Pinned until Undo is resolved" : plan.backupWillBePinned ? "A pinned backup will be created on successful deployment" : "Backup status is unavailable"}</dd>
        <dt className="text-wasteland-500">Plan evidence</dt>
        <dd className="font-mono text-wasteland-300">{shortenHash(plan.evidenceHash)}</dd>
      </dl>

      <div className="mt-4 overflow-x-auto border border-wasteland-700">
        <table className="w-full min-w-[38rem] text-left text-xs">
          <thead className="bg-wasteland-800 text-wasteland-400"><tr><th className="px-3 py-2 font-medium">Action</th><th className="px-3 py-2 font-medium">Target</th><th className="px-3 py-2 font-medium">Current</th><th className="px-3 py-2 font-medium">Output</th></tr></thead>
          <tbody>
            {plan.targetActions.map((target) => <tr key={target.targetId} className="border-t border-wasteland-800 text-wasteland-200">
              <td className="px-3 py-2">{deploymentActionLabel(target.kind)}</td><td className="px-3 py-2 font-mono">{target.relativePath}{target.shadowingVariants.length > 0 && <span className="mt-1 block text-[10px] text-amber-300">Shadows: {target.shadowingVariants.join(", ")}</span>}</td><td className="px-3 py-2 font-mono">{shortenHash(target.currentSha256)}</td><td className="px-3 py-2 font-mono">{shortenHash(target.outputSha256)}</td>
            </tr>)}
          </tbody>
        </table>
      </div>
      {plan.targetActions.some((target) => target.shadowingVariants.length > 0) && <p className="mt-2 text-xs text-amber-300">Shadowing variants will be removed so the reviewed OGG becomes the active in-game audio.</p>}
      {plan.gameRunning === true && <p className="mt-3 text-sm text-rust-300">Close JA2 before deployment. The game is currently running.</p>}
      {plan.gameRunning === null && <p className="mt-3 text-sm text-rust-300">Game-running status is unavailable. Refresh before deployment.</p>}
      {plan.toolchainAvailable === false && <p className="mt-3 text-sm text-rust-300">Configure an available FFmpeg toolchain in Settings before deployment.</p>}
      {plan.toolchainAvailable === null && <p className="mt-3 text-sm text-rust-300">Toolchain status is unavailable. Refresh before deployment.</p>}
      {plan.recoveryRequired === true && <p className="mt-3 text-sm text-rust-300">Recovery is required before deploying another Voice Lab change.</p>}
      {plan.recoveryRequired === null && <p className="mt-3 text-sm text-rust-300">Recovery status is unavailable. Refresh before deploying.</p>}
      {error && <p className="mt-3 text-sm text-rust-300">{error}</p>}
      <div className="mt-4 flex items-center gap-3">
        <button type="button" className="btn-primary text-sm" disabled={blocked || deploying} onClick={() => void deploy()}>{deploying ? "Deploying…" : "Deploy reviewed changes"}</button>
        <span className="text-xs text-wasteland-500">The server validates this plan again under its write locks.</span>
      </div>
    </section>
  );
}
