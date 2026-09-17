/**
 * Explicit locations for the Voice Lab's external tools. Voice Lab never
 * searches the system path for these, so an unset row means that part of
 * the workflow is simply unavailable.
 */
import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import {
  formatApiError,
  getAppSettings,
  getVoiceLabStatus,
  updateAppSettings,
} from "../../lib/api";
import { isRunningInTauri, pickDirectory, pickFile } from "../../lib/tauri";

type VoiceKey =
  | "voice_authoring_workspace"
  | "voice_ffmpeg_path"
  | "voice_transcriber_python";

export default function VoiceLabSettings() {
  const qc = useQueryClient();
  const settings = useQuery({ queryKey: ["app-settings"], queryFn: getAppSettings });
  const capability = useQuery({
    queryKey: ["voice-lab-status"],
    queryFn: () => getVoiceLabStatus(),
  });
  // Which row the last successful save belongs to. One mutation backs all
  // three rows, so without this the confirmation reads as a claim about
  // whichever row the user is editing now.
  const [savedKey, setSavedKey] = useState<VoiceKey | null>(null);
  const save = useMutation({
    mutationFn: updateAppSettings,
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["app-settings"] });
      void qc.invalidateQueries({ queryKey: ["voice-lab-status"] });
    },
  });
  const [drafts, setDrafts] = useState<Partial<Record<VoiceKey, string>>>({});
  const value = (key: VoiceKey) => drafts[key] ?? settings.data?.[key] ?? "";
  const setValue = (key: VoiceKey, next: string) => {
    setSavedKey(null);
    setDrafts((current) => ({ ...current, [key]: next }));
  };
  const commit = (key: VoiceKey) => {
    save.mutate(
      { [key]: value(key) } as Parameters<typeof updateAppSettings>[0],
      { onSuccess: () => setSavedKey(key) },
    );
  };
  const configured = (available: boolean, label: string) => (
    <span className={available ? "text-emerald-300" : "text-amber-300"}>
      {available ? `${label} configured` : `${label} not configured`}
    </span>
  );

  const rows: Array<{
    key: VoiceKey;
    label: string;
    note: string;
    pick: () => Promise<string | null>;
  }> = [
    {
      key: "voice_authoring_workspace",
      label: "Authoring workspace",
      note: "Holds recipes and exported manifests, outside the game install.",
      pick: () => pickDirectory("Pick Voice Lab authoring workspace", "voice-authoring-workspace"),
    },
    {
      key: "voice_ffmpeg_path",
      label: "FFmpeg executable",
      note: "Needed to render previews and generate lip-sync data.",
      pick: () => pickFile("Pick ffmpeg executable", [{ name: "Executable", extensions: ["exe"] }], "voice-ffmpeg"),
    },
    {
      key: "voice_transcriber_python",
      label: "Transcription interpreter",
      note: "Optional. Runs the local transcription worker.",
      pick: () => pickFile("Pick transcription interpreter", [{ name: "Interpreter", extensions: ["exe", "py"] }], "voice-transcriber"),
    },
  ];

  return (
    <section className="card">
      <h2 className="text-lg font-semibold">Voice Lab tools</h2>
      <p className="mb-2 text-sm text-wasteland-300">
        Voice Lab uses these exact paths and never searches the system path.
      </p>
      <div className="mb-3 flex flex-wrap gap-x-4 gap-y-1 text-xs">
        {configured(Boolean(capability.data?.authoringWorkspaceConfigured), "Workspace")}
        {configured(Boolean(capability.data?.ffmpegConfigured), "FFmpeg")}
        {configured(Boolean(capability.data?.transcriberConfigured), "Transcription interpreter")}
      </div>
      {capability.isError && (
        <p className="mb-3 text-xs text-rust-300">
          Capability status unavailable: {formatApiError(capability.error)}
        </p>
      )}
      <div className="space-y-3">
        {rows.map((row) => (
          <div key={row.key}>
            <label className="block text-sm font-medium text-wasteland-200">{row.label}</label>
            <p className="mb-1 text-xs text-wasteland-500">{row.note}</p>
            <div className="flex gap-2">
              <input
                className="input min-w-0 flex-1 font-mono text-xs"
                value={value(row.key)}
                onChange={(event) => setValue(row.key, event.target.value)}
                placeholder="Not configured"
              />
              <button
                type="button"
                className="btn-ghost text-xs"
                disabled={!isRunningInTauri()}
                title={isRunningInTauri() ? undefined : "Folder picking needs the desktop app."}
                onClick={() => void row.pick().then((picked) => {
                  if (picked) setValue(row.key, picked);
                })}
              >
                Browse…
              </button>
              <button
                type="button"
                className="btn-primary text-xs"
                disabled={save.isPending || value(row.key) === (settings.data?.[row.key] ?? "")}
                onClick={() => commit(row.key)}
              >
                Save
              </button>
            </div>
            {savedKey === row.key && (
              <p className="mt-1 text-xs text-emerald-300">✓ Saved.</p>
            )}
          </div>
        ))}
      </div>
      {save.isError && (
        <p className="mt-3 text-xs text-rust-300">{formatApiError(save.error)}</p>
      )}
    </section>
  );
}
