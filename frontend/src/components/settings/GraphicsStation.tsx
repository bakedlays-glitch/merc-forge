/**
 * NOT CURRENTLY RENDERED. The Settings page stopped showing this section in
 * v1.0.0-beta.4; the component and the /graphics endpoints behind it are kept
 * intact, so restoring it is an import and a section in routes/Settings.tsx.
 *
 * The "golden" graphics config: cnc-ddraw + ReShade with the
 * ja2_remastered preset. Reports how the active install's config compares
 * to the golden keys and can re-apply them. The runtimes themselves are
 * external installs — this checks for them but does not ship them.
 */
import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { deployGraphics, formatApiError, getGraphicsStatus } from "../../lib/api";
import ConfirmModal from "../ConfirmModal";

export default function GraphicsStation() {
  const qc = useQueryClient();
  const status = useQuery({ queryKey: ["graphics-status"], queryFn: getGraphicsStatus });
  const [confirmOpen, setConfirmOpen] = useState(false);
  const deploy = useMutation({
    mutationFn: deployGraphics,
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["graphics-status"] });
      // The deploy takes a snapshot and reports its id below, so the
      // Backups page must not still be showing a list without it.
      qc.invalidateQueries({ queryKey: ["backups"] });
      setConfirmOpen(false);
    },
  });

  const comps = status.data?.components ?? [];
  const runtimesMissing = comps.filter((c) => c.kind === "runtime" && !c.present);
  const customized = comps.filter(
    (c) => c.kind !== "runtime" && c.present && !c.matches,
  );
  const allGreen = comps.length > 0 && comps.every((c) => c.matches);

  const stateLabel = (c: (typeof comps)[number]) => {
    if (c.kind === "runtime") {
      return c.present ? (
        <span className="text-emerald-400">✓ installed</span>
      ) : (
        <span className="text-wasteland-400">
          ⛔ not installed
          {c.download_url && (
            <>
              {" — "}
              <a className="underline" href={c.download_url} target="_blank" rel="noreferrer">
                download
              </a>
            </>
          )}
        </span>
      );
    }
    if (!c.present) return <span className="text-amber-400">✗ missing (deploy will create)</span>;
    if (c.matches) return <span className="text-emerald-400">✓ matches golden</span>;
    return (
      <span className="text-amber-300" title={(c.mismatched_keys ?? []).join(", ")}>
        ⚠ differs — yours is customized
        {c.mismatched_keys?.length ? ` (${c.mismatched_keys.length} key${c.mismatched_keys.length === 1 ? "" : "s"})` : ""}
      </span>
    );
  };

  return (
    <div className="space-y-3">
      {status.isError && (
        <div className="text-xs text-red-300">{formatApiError(status.error)}</div>
      )}
      <table className="w-full text-sm">
        <tbody>
          {comps.map((c) => (
            <tr key={c.component} className="border-b border-wasteland-800 last:border-0">
              <td className="py-1.5 font-mono text-wasteland-200">{c.component}</td>
              <td className="py-1.5 text-xs text-wasteland-500">{c.note}</td>
              <td className="py-1.5 text-right text-xs">{stateLabel(c)}</td>
            </tr>
          ))}
        </tbody>
      </table>
      <div className="flex items-center gap-3">
        <button
          className="btn-primary text-sm"
          disabled={runtimesMissing.length > 0 || deploy.isPending || allGreen}
          title={
            runtimesMissing.length > 0
              ? `Install the missing runtime${runtimesMissing.length === 1 ? "" : "s"} first: ${runtimesMissing.map((c) => c.component).join(", ")}`
              : allGreen
                ? "Everything already matches the golden config"
                : undefined
          }
          onClick={() => setConfirmOpen(true)}
        >
          {deploy.isPending ? "Deploying…" : allGreen ? "✓ Golden config active" : "Deploy golden config"}
        </button>
        {deploy.isError && (
          <span className="text-xs text-red-300">{formatApiError(deploy.error)}</span>
        )}
        {deploy.isSuccess && (
          <span className="text-xs text-emerald-300">
            ✓ {deploy.data.actions.join("; ")} (backup {deploy.data.backup_id})
          </span>
        )}
      </div>

      <ConfirmModal
        open={confirmOpen}
        title="Deploy golden graphics config?"
        destructive={customized.length > 0}
        body={
          <div className="space-y-2 text-sm">
            <p>
              This merges the golden keys into <code className="font-mono">ddraw.ini</code> +{" "}
              <code className="font-mono">ReShade.ini</code> (your other keys are preserved) and
              copies the <code className="font-mono">ja2_remastered.ini</code> preset.
            </p>
            {customized.length > 0 && (
              <div className="rounded border border-amber-700/60 bg-amber-900/20 p-2 text-xs text-amber-200">
                ⚠ These files differ from golden — your customizations to the listed keys will be
                overwritten:{" "}
                {customized
                  .map((c) => `${c.component} (${(c.mismatched_keys ?? []).join(", ") || "content"})`)
                  .join("; ")}
              </div>
            )}
            <p className="text-xs text-wasteland-400">
              A backup snapshot of all three files is taken first (restorable from Backups).
            </p>
          </div>
        }
        confirmLabel="Deploy"
        busy={deploy.isPending}
        onConfirm={() => deploy.mutate()}
        onCancel={() => setConfirmOpen(false)}
      />
    </div>
  );
}
