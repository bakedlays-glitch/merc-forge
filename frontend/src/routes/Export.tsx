import { useEffect, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { useMutation, useQuery } from "@tanstack/react-query";

import { exportBundle, formatApiError, getRoster } from "../lib/api";
import type { RosterEntry } from "../lib/schema";
import { pickSaveFile } from "../lib/tauri";

function safeFilename(name: string, slot: number): string {
  return name.replace(/[^A-Za-z0-9_-]/g, "_") || `slot_${slot}`;
}

export default function Export() {
  // When the roster's "Export .wmerc" action navigates here it passes
  // ?slot=<n>. Pre-filter the picker to that slot so the user doesn't have
  // to find it again in the grid.
  const [params] = useSearchParams();
  const initialSlot = params.get("slot");
  const [search, setSearch] = useState(
    initialSlot !== null && /^\d+$/.test(initialSlot) ? initialSlot : ""
  );
  const [savedPath, setSavedPath] = useState<string | null>(null);
  const roster = useQuery({ queryKey: ["roster"], queryFn: () => getRoster() });

  const exportMut = useMutation({
    mutationFn: async (entry: RosterEntry) => {
      const name = entry.nickname ?? entry.name ?? `slot_${entry.slot}`;
      const path = await pickSaveFile(`${safeFilename(name, entry.slot)}.wmerc`, [
        { name: "Merc Forge bundle", extensions: ["wmerc"] },
      ]);
      if (!path) return null;
      const result = await exportBundle({ slot: entry.slot, out_path: path, include_voice: true });
      return result.out_path;
    },
    onSuccess: (path) => {
      if (path) setSavedPath(path);
    },
  });

  const filled = (roster.data ?? []).filter((e) => !e.is_empty);
  const matches = filled.filter((e) => {
    if (!search) return true;
    const q = search.toLowerCase();
    return (
      (e.name ?? "").toLowerCase().includes(q) ||
      (e.nickname ?? "").toLowerCase().includes(q) ||
      String(e.slot) === q
    );
  });

  return (
    <div className="mx-auto max-w-5xl px-6 py-8 space-y-4">
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-bold">Export Merc</h1>
        <Link to="/" className="btn-ghost text-sm">← Back to Hub</Link>
      </div>

      <p className="text-sm text-wasteland-300">
        Pick a merc to save as a portable <span className="font-mono">.wmerc</span> bundle —
        profile, gear, AIM binding, EDT bio, portrait, and voice clips in one file.
      </p>

      <div className="flex items-center gap-3">
        <input
          className="input max-w-sm"
          type="text"
          placeholder="Search by name or slot..."
          value={search}
          onChange={(e) => setSearch(e.target.value)}
        />
        <span className="text-sm text-wasteland-400">{matches.length} shown</span>
      </div>

      {exportMut.isPending && (
        <div className="card text-wasteland-300 text-sm">Writing bundle...</div>
      )}
      {exportMut.isError && (
        <div className="rounded border border-rust-500/40 bg-rust-500/10 p-3 text-sm text-rust-200">
          {formatApiError(exportMut.error)}
        </div>
      )}
      {savedPath && !exportMut.isPending && (
        <div className="rounded border border-rust-500/40 bg-wasteland-800 p-3 text-sm flex items-center justify-between gap-3">
          <div className="min-w-0">
            <div className="text-rust-400 font-semibold">Saved.</div>
            <div className="text-xs text-wasteland-300 font-mono truncate" title={savedPath}>
              {savedPath}
            </div>
          </div>
          <button
            className="text-xs text-wasteland-400 hover:text-rust-400"
            onClick={() => setSavedPath(null)}
          >
            dismiss
          </button>
        </div>
      )}

      {roster.isLoading && <div className="text-wasteland-300">Loading roster...</div>}
      {roster.isError && (
        <div className="card text-rust-400">
          Couldn't load the roster. Make sure an install is active in Settings.
        </div>
      )}

      <div className="grid grid-cols-2 sm:grid-cols-3 md:grid-cols-4 lg:grid-cols-5 gap-2">
        {matches.map((entry) => (
          <button
            key={entry.slot}
            type="button"
            disabled={exportMut.isPending}
            onClick={() => exportMut.mutate(entry)}
            className="text-left rounded border border-wasteland-700 bg-wasteland-800 hover:border-rust-500 transition-colors p-3 flex flex-col gap-1 disabled:opacity-50"
          >
            <div className="flex items-center justify-between text-xs">
              <span className="font-mono text-wasteland-400">slot {entry.slot}</span>
              {entry.profile_type === 1 && <span className="badge-aim">AIM</span>}
              {entry.profile_type === 2 && <span className="badge-merc">MERC</span>}
              {entry.profile_type === 3 && <span className="badge-npc">RPC</span>}
              {entry.profile_type === 4 && <span className="badge-npc">NPC</span>}
            </div>
            <div className="text-sm font-medium truncate">
              {entry.nickname ?? entry.name ?? "?"}
            </div>
            {entry.nickname && entry.name && entry.nickname !== entry.name && (
              <div className="text-xs text-wasteland-400 truncate">{entry.name}</div>
            )}
          </button>
        ))}
      </div>
    </div>
  );
}
