/**
 * INI presets panel — body-swap view inside the INI editor (Phase 3,
 * docs/INI_PRESETS_SPEC.md). Cross-file scope: lists builtin +
 * install-local presets, previews via server dry-run (current -> new),
 * applies through the locked/backed-up batch path.
 */
import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import {
  applyIniPreset,
  deleteIniPreset,
  formatApiError,
  getIniPresets,
} from "../lib/api";
import type { IniPreset, PresetApplyResult } from "../lib/schema";
import ConfirmModal from "./ConfirmModal";

export default function IniPresetsPanel() {
  const qc = useQueryClient();
  const presetsQ = useQuery({ queryKey: ["ini-presets"], queryFn: getIniPresets });
  const [preview, setPreview] = useState<{ preset: IniPreset; plan: PresetApplyResult } | null>(null);
  const [confirmApply, setConfirmApply] = useState<IniPreset | null>(null);
  const [lastApplied, setLastApplied] = useState<string | null>(null);

  const dryRun = useMutation({
    mutationFn: (p: IniPreset) => applyIniPreset(p.id, true),
  });
  const apply = useMutation({
    mutationFn: (p: IniPreset) => applyIniPreset(p.id, false),
    onSuccess: (res, p) => {
      qc.invalidateQueries({ queryKey: ["ini-effective"] });
      qc.invalidateQueries({ queryKey: ["ini-overrides"] });
      qc.invalidateQueries({ queryKey: ["ini-summary"] });
      setLastApplied(
        `${p.name}: ${res.applied} change${res.applied === 1 ? "" : "s"} applied` +
        (res.effect_timing === "new_game" ? ". Takes effect on the next new game." : ". Takes effect on the next launch."));
      setPreview(null);
      setConfirmApply(null);
    },
  });
  const remove = useMutation({
    mutationFn: (p: IniPreset) => deleteIniPreset(p.id),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["ini-presets"] }),
  });

  const openPreview = async (p: IniPreset) => {
    const plan = await dryRun.mutateAsync(p);
    setPreview({ preset: p, plan });
  };

  const presets = presetsQ.data?.presets ?? [];
  const builtin = presets.filter((p) => p.source === "builtin");
  const installP = presets.filter((p) => p.source === "install");

  return (
    <div className="max-h-[70vh] overflow-y-auto space-y-5">
      {presetsQ.isError && (
        <div className="text-sm text-red-300">{formatApiError(presetsQ.error)}</div>
      )}
      {(presetsQ.data?.file_warnings ?? []).map((w) => (
        <div key={w} className="text-xs text-amber-300">{w}</div>
      ))}
      {lastApplied && (
        <div className="rounded border border-emerald-800 bg-emerald-950/40 px-3 py-2 text-sm text-emerald-200">
          {lastApplied}
        </div>
      )}

      {[["Built-in", builtin] as const, ["This install", installP] as const].map(
        ([label, group]) =>
          group.length > 0 && (
            <section key={label}>
              <h3 className="text-sm font-semibold text-wasteland-300 mb-2">{label}</h3>
              <div className="space-y-2">
                {group.map((p) => (
                  <div key={p.id} className="card flex items-start justify-between gap-4 p-3">
                    <div className="min-w-0 flex-1">
                      <div className="flex items-center gap-2 flex-wrap">
                        <span className="font-medium">{p.name}</span>
                        <span className="text-xs text-wasteland-500">
                          {p.changes.length} change{p.changes.length === 1 ? "" : "s"}
                        </span>
                        <span className="text-xs text-wasteland-500">
                          {p.effect_timing === "new_game" ? "next new game" : "next launch"}
                        </span>
                        {p.savegame_risk && (
                          <span className="text-xs text-amber-400">can affect saved games</span>
                        )}
                      </div>
                      <p className="text-sm text-wasteland-300 mt-1">{p.description}</p>
                      {p.apply_disabled && (
                        <p className="text-xs text-red-300 mt-1">{p.apply_disabled}</p>
                      )}
                      {p.warnings.length > 0 && (
                        <details className="text-xs text-wasteland-500 mt-1">
                          <summary className="cursor-pointer">
                            {p.warnings.length} note{p.warnings.length === 1 ? "" : "s"}
                          </summary>
                          <ul className="mt-1 space-y-0.5">
                            {p.warnings.map((w) => <li key={w}>{w}</li>)}
                          </ul>
                        </details>
                      )}
                    </div>
                    <div className="flex items-center gap-2 shrink-0">
                      {p.source === "install" && (
                        <button
                          className="btn-ghost text-xs"
                          onClick={() => remove.mutate(p)}
                          disabled={remove.isPending}
                        >
                          Delete
                        </button>
                      )}
                      <button
                        className="btn-primary text-xs"
                        disabled={Boolean(p.apply_disabled) || dryRun.isPending}
                        onClick={() => void openPreview(p)}
                      >
                        Preview
                      </button>
                    </div>
                  </div>
                ))}
              </div>
            </section>
          ),
      )}
      {presets.length === 0 && !presetsQ.isLoading && (
        <p className="text-sm text-wasteland-400">No presets available.</p>
      )}

      {/* Preview modal */}
      {preview && (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm"
          onClick={() => setPreview(null)}
        >
          <div
            className="w-[44rem] max-w-[92vw] max-h-[85vh] overflow-y-auto rounded-lg border border-wasteland-700 bg-wasteland-900 p-4 shadow-2xl"
            onClick={(e) => e.stopPropagation()}
            role="dialog"
            aria-modal="true"
          >
            <h3 className="text-base font-semibold mb-1">{preview.preset.name}</h3>
            <p className="text-xs text-wasteland-400 mb-3">
              {preview.preset.changes.length} change{preview.preset.changes.length === 1 ? "" : "s"} ·{" "}
              {preview.preset.effect_timing === "new_game"
                ? "takes effect on the next new game"
                : "takes effect on the next launch"}
            </p>
            {(preview.plan.batches ?? []).map((batch) =>
              batch.files.map((f) => (
                <div key={`${batch.target}/${f.ini_file}`} className="mb-3">
                  <div className="text-xs font-mono text-wasteland-400 mb-1">
                    {f.ini_file}
                    <span className="text-wasteland-600">
                      {" "}· {batch.target === "canon" ? "written directly" : "override file"}
                    </span>
                  </div>
                  <table className="w-full text-xs">
                    <thead className="text-wasteland-500">
                      <tr>
                        <th className="text-left font-normal pb-1">section · key</th>
                        <th className="text-right font-normal pb-1 w-24">current</th>
                        <th className="text-right font-normal pb-1 w-24">new</th>
                      </tr>
                    </thead>
                    <tbody>
                      {f.changes.map((c) => {
                        const noop = c.current != null && c.current === c.value;
                        return (
                          <tr
                            key={`${c.section}/${c.key}`}
                            className={"border-t border-wasteland-800 " + (noop ? "text-wasteland-600" : "")}
                          >
                            <td className="py-1">
                              <span className="text-wasteland-500">{c.section} · </span>
                              <span className="font-mono">{c.key}</span>
                            </td>
                            <td className="py-1 text-right font-mono">{c.current ?? "—"}</td>
                            <td className="py-1 text-right font-mono">
                              {c.value ?? "(removed)"}
                              {noop && <span className="text-wasteland-600"> (no change)</span>}
                            </td>
                          </tr>
                        );
                      })}
                    </tbody>
                  </table>
                </div>
              )),
            )}
            <div className="flex justify-end gap-2 mt-4">
              <button className="btn-ghost text-sm" onClick={() => setPreview(null)}>
                Cancel
              </button>
              <button
                className="btn-primary text-sm"
                disabled={apply.isPending}
                onClick={() => {
                  if (preview.preset.savegame_risk) {
                    setConfirmApply(preview.preset);
                  } else {
                    apply.mutate(preview.preset);
                  }
                }}
              >
                {apply.isPending ? "Applying…" : "Apply"}
              </button>
            </div>
            {apply.isError && (
              <div className="mt-2 text-xs text-red-300">{formatApiError(apply.error)}</div>
            )}
          </div>
        </div>
      )}

      <ConfirmModal
        open={confirmApply != null}
        title={`Apply ${confirmApply?.name ?? ""}?`}
        destructive
        body={
          <p className="text-sm">
            This preset changes keys that can invalidate existing saved games.
          </p>
        }
        confirmLabel="Apply"
        busy={apply.isPending}
        onConfirm={() => confirmApply && apply.mutate(confirmApply)}
        onCancel={() => setConfirmApply(null)}
      />
    </div>
  );
}
