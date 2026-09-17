/**
 * The install the INI Editor diffs against in Edit INI mode. Whatever is
 * picked here is the *reference*, not necessarily stock 1.13 — engine-true
 * defaults are shown per key regardless of this setting.
 */
import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { formatApiError, getAppSettings, updateAppSettings } from "../../lib/api";
import { isRunningInTauri, pickDirectory } from "../../lib/tauri";

export default function ReferenceInstallField() {
  const qc = useQueryClient();
  const settings = useQuery({ queryKey: ["app-settings"], queryFn: getAppSettings });
  const [draft, setDraft] = useState<string | null>(null);
  const save = useMutation({
    mutationFn: (path: string) => updateAppSettings({ baseline_install_path: path }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["app-settings"] });
      qc.invalidateQueries({ queryKey: ["ini-effective"] });
      qc.invalidateQueries({ queryKey: ["ini-summary"] });
      setDraft(null);
    },
  });

  const committed = settings.data?.baseline_install_path ?? "";
  const value = draft ?? committed;

  return (
    <div className="space-y-2">
      <div className="flex items-center gap-2">
        <input
          className="input flex-1 font-mono text-sm"
          placeholder="C:\Games\JA2_113  (folder containing JA2.exe + Data-1.13)"
          value={value}
          onChange={(e) => setDraft(e.target.value)}
        />
        <button
          className="btn-ghost text-sm"
          disabled={!isRunningInTauri()}
          title={isRunningInTauri() ? undefined : "Folder picking needs the desktop app."}
          onClick={async () => {
            const picked = await pickDirectory("Pick the reference install folder");
            if (picked) setDraft(picked);
          }}
        >
          Browse…
        </button>
        <button
          className="btn-primary text-sm"
          disabled={draft == null || draft === committed || save.isPending}
          onClick={() => save.mutate(value)}
        >
          Save
        </button>
        {committed && (
          <button
            className="btn-ghost text-sm"
            disabled={save.isPending}
            onClick={() => save.mutate("")}
            title="Clear the reference install"
          >
            Clear
          </button>
        )}
      </div>
      {save.isError && (
        <div className="text-xs text-red-300">{formatApiError(save.error)}</div>
      )}
      {save.isSuccess && draft == null && (
        <div className="text-xs text-emerald-300">✓ Saved.</div>
      )}
    </div>
  );
}
