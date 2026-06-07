import { useRef, useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";

import { deleteVoiceClip, listVoiceClips, uploadVoiceClips } from "../lib/api";
import { BARK_EVENTS } from "../lib/barkEvents";

interface Props {
  slot: number;
  /** Optional label shown above the manager (e.g. for the Create flow) */
  title?: string;
}

/** A dropped clip awaiting an event assignment + upload. */
interface Staged {
  id: number;
  file: File;
  bark: number | null; // null = keep the uploaded filename as-is
}

function humanSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}

function pad3(n: number): string {
  return String(n).padStart(3, "0");
}

export default function VoiceFileManager({ slot, title }: Props) {
  const qc = useQueryClient();
  const state = useQuery({
    queryKey: ["voice", slot],
    queryFn: () => listVoiceClips(slot),
  });
  const fileInput = useRef<HTMLInputElement | null>(null);
  const [dragging, setDragging] = useState(false);
  const [staged, setStaged] = useState<Staged[]>([]);
  const nextId = useRef(1);

  const upload = useMutation({
    mutationFn: (items: Staged[]) =>
      uploadVoiceClips(slot, items.map((s) => s.file), items.map((s) => s.bark)),
    onSuccess: () => {
      setStaged([]);
      qc.invalidateQueries({ queryKey: ["voice", slot] });
    },
  });
  const del = useMutation({
    mutationFn: (name: string) => deleteVoiceClip(slot, name),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["voice", slot] }),
  });

  function addFiles(list: FileList | null) {
    if (!list || list.length === 0) return;
    const files = Array.from(list).filter((f) =>
      [".wav", ".ogg", ".mp3"].some((ext) => f.name.toLowerCase().endsWith(ext))
    );
    if (files.length === 0) return;
    setStaged((prev) => [
      ...prev,
      ...files.map((file) => ({ id: nextId.current++, file, bark: null as number | null })),
    ]);
  }
  function setBark(id: number, bark: number | null) {
    setStaged((prev) => prev.map((s) => (s.id === id ? { ...s, bark } : s)));
  }
  function removeStaged(id: number) {
    setStaged((prev) => prev.filter((s) => s.id !== id));
  }

  const voiceIndex = state.data?.voice_index ?? slot;

  return (
    <div className="space-y-3">
      {title && <h3 className="text-base font-semibold">{title}</h3>}

      <div className="text-xs text-wasteland-400">
        Voice clips for slot <span className="font-mono text-rust-400">{slot}</span>
        {state.data && state.data.voice_index !== slot && (
          <> (writes to <span className="font-mono">usVoiceIndex={state.data.voice_index}</span>)</>
        )}
      </div>

      {/* Drop zone — stages files for naming; nothing is written until you
          pick events and hit "Add". */}
      <div
        className={`rounded border-2 border-dashed p-4 text-center transition-colors cursor-pointer ${
          dragging ? "border-rust-500 bg-rust-500/5" : "border-wasteland-700"
        }`}
        onDragOver={(e) => { e.preventDefault(); setDragging(true); }}
        onDragLeave={() => setDragging(false)}
        onDrop={(e) => {
          e.preventDefault();
          setDragging(false);
          addFiles(e.dataTransfer.files);
        }}
        onClick={() => fileInput.current?.click()}
        role="button"
        tabIndex={0}
      >
        <div className="text-sm text-wasteland-200">Drop audio here, or click to browse</div>
        <div className="text-xs text-wasteland-400 mt-1">
          .wav, .ogg, .mp3 — pick the in-game event for each and Merc Forge names it{" "}
          <code className="font-mono">{pad3(voiceIndex)}_NNN</code> for you.
        </div>
        <input
          ref={fileInput}
          type="file"
          accept=".wav,.ogg,.mp3,audio/*"
          multiple
          className="hidden"
          onChange={(e) => addFiles(e.target.files)}
        />
      </div>

      {/* Staging list — assign each clip a bark event, then upload. */}
      {staged.length > 0 && (
        <div className="space-y-2 rounded border border-wasteland-700 bg-wasteland-900/40 p-2">
          <div className="text-xs text-wasteland-300">
            {staged.length} clip{staged.length === 1 ? "" : "s"} to add — pick the event for each:
          </div>
          <ul className="space-y-1.5">
            {staged.map((s) => (
              <li key={s.id} className="flex items-center gap-2">
                <span className="font-mono text-xs truncate min-w-0 flex-1" title={s.file.name}>
                  {s.file.name}
                </span>
                <select
                  className="input text-xs py-0.5 max-w-[15rem]"
                  value={s.bark ?? ""}
                  onChange={(e) =>
                    setBark(s.id, e.target.value === "" ? null : Number(e.target.value))
                  }
                >
                  <option value="">Keep filename as-is</option>
                  {BARK_EVENTS.map((b) => (
                    <option key={b.value} value={b.value}>
                      {pad3(b.value)} — {b.label}
                    </option>
                  ))}
                </select>
                <span className="font-mono text-[10px] text-wasteland-500 w-28 truncate text-right">
                  {s.bark === null ? s.file.name : `${pad3(voiceIndex)}_${pad3(s.bark)}`}
                </span>
                <button
                  type="button"
                  className="text-xs text-wasteland-400 hover:text-rust-300 px-1"
                  onClick={() => removeStaged(s.id)}
                  aria-label={`Remove ${s.file.name}`}
                >
                  ✕
                </button>
              </li>
            ))}
          </ul>
          <button
            type="button"
            className="rounded bg-rust-600 px-3 py-1 text-xs text-wasteland-50 hover:bg-rust-500 disabled:opacity-50"
            disabled={upload.isPending}
            onClick={() => upload.mutate(staged)}
          >
            {upload.isPending ? "Adding…" : `Add ${staged.length} clip${staged.length === 1 ? "" : "s"}`}
          </button>
        </div>
      )}

      {upload.data && upload.data.skipped.length > 0 && (
        <div className="text-xs text-yellow-400 space-y-0.5">
          <div>Skipped {upload.data.skipped.length} file(s):</div>
          <ul className="list-disc list-inside">
            {upload.data.skipped.map((s) => (
              <li key={s.name}>
                <span className="font-mono">{s.name}</span>
                {s.reason && <span className="text-yellow-300"> — {s.reason}</span>}
              </li>
            ))}
          </ul>
        </div>
      )}

      {state.isLoading && (
        <div role="status" aria-busy="true" className="space-y-1.5">
          <div className="flex items-center gap-2 text-sm text-wasteland-300">
            <span className="inline-block h-3 w-3 animate-spin rounded-full border-2 border-rust-500 border-t-transparent" />
            Loading voice files…
          </div>
          <div className="h-1.5 w-full overflow-hidden rounded-full border border-wasteland-800 bg-wasteland-900">
            <div className="h-full w-full bg-gradient-to-r from-rust-700/30 via-rust-500/60 to-rust-700/30 animate-pulse" />
          </div>
          <p className="text-[10px] text-wasteland-500">
            Reading <code className="font-mono">Speech/{voiceIndex}/</code>{" "}
            and (if applicable) probing <code className="font-mono">Speech.slf</code>{" "}
            for vanilla donor clips.
          </p>
        </div>
      )}
      {state.data && (
        <div>
          <div className="text-sm text-wasteland-300 mb-1">
            {state.data.clips.length === 0
              ? "No voice clips on disk yet."
              : `${state.data.clips.length} clip${state.data.clips.length === 1 ? "" : "s"} on disk`}
          </div>
          {state.data.clips.length > 0 && (
            <ul className="border border-wasteland-700 rounded divide-y divide-wasteland-700 max-h-64 overflow-y-auto">
              {state.data.clips.map((c) => (
                <li key={c.name} className="flex items-center justify-between gap-2 p-2 text-sm">
                  <div className="min-w-0 flex-1">
                    <div className="font-mono text-xs truncate">{c.name}</div>
                    <div className="text-xs text-wasteland-400">{humanSize(c.size_bytes)}</div>
                  </div>
                  <button
                    type="button"
                    className="text-xs text-rust-400 hover:text-rust-300 px-2"
                    onClick={() => {
                      if (window.confirm(`Permanently delete ${c.name}?`)) {
                        del.mutate(c.name);
                      }
                    }}
                    disabled={del.isPending}
                  >
                    {del.isPending && del.variables === c.name ? "Deleting..." : "Delete"}
                  </button>
                </li>
              ))}
            </ul>
          )}
        </div>
      )}

      <div className="text-xs text-wasteland-500 mt-2">
        <strong>How naming works:</strong> JA2 plays clips named{" "}
        <code className="font-mono">{"<voiceIndex>_<event>.wav"}</code> — e.g.{" "}
        <code className="font-mono">{pad3(voiceIndex)}_001.wav</code> (no "MERC" prefix).
        Pick an event above and Merc Forge writes the correct name; clips you've already
        named right can use "Keep filename as-is".
      </div>
    </div>
  );
}
