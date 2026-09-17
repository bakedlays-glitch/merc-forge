import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";

import { formatApiError, listVoiceClips } from "../lib/api";

interface Props {
  slot: number;
  title?: string;
  /** Retained only so existing create/edit callers remain source-compatible. */
  deferred?: boolean;
  onStagedChange?: (items: { file: File; bark: number | null }[]) => void;
  previewVoiceIndex?: number;
}

function humanSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}

/**
 * Legacy merc pages may inspect the resolved clip list, but Voice Lab is the
 * only managed writer. This prevents unreviewed uploads or deletion from
 * bypassing preview, preflight, and rollback.
 */
export default function VoiceFileManager({ slot, title }: Props) {
  const state = useQuery({ queryKey: ["voice", slot], queryFn: () => listVoiceClips(slot) });
  const voiceIndex = state.data?.voice_index ?? slot;

  return <div className="space-y-3">
    {title && <h3 className="text-base font-semibold">{title}</h3>}
    <div className="flex flex-wrap items-start justify-between gap-3 border border-wasteland-700 bg-wasteland-900/40 p-3">
      <div>
        <p className="text-xs text-wasteland-400">Voice clips for slot <span className="font-mono text-rust-400">{slot}</span>{state.data && state.data.voice_index !== slot && <> (shared bank <span className="font-mono">{voiceIndex}</span>)</>}</p>
        <p className="mt-1 text-xs text-wasteland-500">Playback and edits are managed through reviewed Voice Lab recipes.</p>
      </div>
      <Link className="btn-primary text-xs" to={`/voice-lab?profile=${encodeURIComponent(String(slot))}`}>Open in Voice Lab</Link>
    </div>
    {state.isLoading && <p className="text-sm text-wasteland-400">Reading resolved voice clips…</p>}
    {state.isError && <p className="text-sm text-rust-300">{formatApiError(state.error)}</p>}
    {state.data && <div>
      <p className="mb-1 text-sm text-wasteland-300">{state.data.clips.length === 0 ? "No resolved voice clips on disk yet." : `${state.data.clips.length} resolved clip${state.data.clips.length === 1 ? "" : "s"}`}</p>
      {state.data.clips.length > 0 && <ul className="max-h-64 divide-y divide-wasteland-700 overflow-y-auto rounded border border-wasteland-700">
        {state.data.clips.map((clip) => <li key={clip.name} className="flex items-center justify-between gap-2 p-2 text-sm"><span className="min-w-0 truncate font-mono text-xs">{clip.name}</span><span className="shrink-0 text-xs text-wasteland-400">{humanSize(clip.size_bytes)}</span></li>)}
      </ul>}
    </div>}
  </div>;
}
