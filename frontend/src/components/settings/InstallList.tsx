/**
 * The registered game installs: add one, pick the active one, drop one.
 *
 * Every other page resolves "the active install" server-side, so changing
 * it here resets the whole query cache rather than a hand-kept list of
 * keys — the previous list missed graphics, backups, voice lab and the
 * INI editor, all of which kept showing the old install's data.
 */
import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import {
  addInstall,
  formatApiError,
  getHealth,
  listInstalls,
  refreshInstalls,
  removeInstall,
  setActiveInstall,
} from "../../lib/api";
import type { InstallInfo } from "../../lib/schema";
import { isRunningInTauri, pickDirectory } from "../../lib/tauri";
import ConfirmModal from "../ConfirmModal";

/** True when a path lives under Windows' UAC-protected program dirs.
 * Matches the English folder names only — a localized Windows names them
 * differently, so treat a false here as "not proven protected", never as
 * "proven safe". */
export function isUacProtectedPath(p: string): boolean {
  if (!p) return false;
  const norm = p.replace(/\//g, "\\").toLowerCase();
  return (
    norm.includes("\\program files")
    || norm.includes("\\programdata")
    || norm.includes("\\windows\\")
  );
}

const UAC_EXPLANATION =
  "Windows blocks writes under Program Files for non-admin processes, so "
  + "edits (merc saves, backups, SLF extract, .dat paint, JSD writes) may "
  + "fail or raise UAC prompts. Copying the install somewhere like "
  + "C:\\Games\\JA2_113 and adding it from there avoids the whole class.";

export default function InstallList() {
  const qc = useQueryClient();
  const installs = useQuery({ queryKey: ["installs"], queryFn: listInstalls });
  const health = useQuery({ queryKey: ["health"], queryFn: getHealth });
  const [browseError, setBrowseError] = useState<string | null>(null);
  const [rescanFeedback, setRescanFeedback] = useState<string | null>(null);
  const [pendingRemoval, setPendingRemoval] = useState<InstallInfo | null>(null);

  const activeId = health.data?.active_install_id ?? null;

  /** Switching install changes what almost every endpoint returns, and a
   * cached page can otherwise keep showing the previous install's data.
   * Same reset the Hub's install switcher does, for the same reason. */
  const resetForInstallChange = () => { qc.resetQueries(); };

  const refresh = useMutation({
    mutationFn: refreshInstalls,
    onSuccess: (list) => {
      qc.invalidateQueries({ queryKey: ["installs"] });
      setRescanFeedback(
        `Re-checked ${list.length} install${list.length === 1 ? "" : "s"}.`,
      );
      window.setTimeout(() => setRescanFeedback(null), 4000);
    },
    onError: (e) => {
      setRescanFeedback(formatApiError(e));
      window.setTimeout(() => setRescanFeedback(null), 6000);
    },
  });

  const setActive = useMutation({
    mutationFn: setActiveInstall,
    onSuccess: resetForInstallChange,
    onError: (e) => {
      setBrowseError(formatApiError(e));
      window.setTimeout(() => setBrowseError(null), 6000);
    },
  });

  const remove = useMutation({
    mutationFn: (id: string) => removeInstall(id),
    onSuccess: () => {
      setPendingRemoval(null);
      resetForInstallChange();
    },
  });

  const browse = useMutation({
    mutationFn: async () => {
      setBrowseError(null);
      const path = await pickDirectory(
        "Pick the folder that contains JA2.exe",
      );
      if (!path) return null;
      const info = await addInstall(path);
      await setActiveInstall(info.id);
      return info;
    },
    onSuccess: (info) => {
      if (info) resetForInstallChange();
    },
    onError: (e) => setBrowseError(formatApiError(e)),
  });

  return (
    <section className="card">
      <div className="flex items-center justify-between mb-3">
        <h2 className="text-lg font-semibold">Game installs</h2>
        <div className="flex items-center gap-2">
          {rescanFeedback && (
            <span className="text-xs text-rust-400">{rescanFeedback}</span>
          )}
          <button
            className="btn-secondary text-sm"
            onClick={() => refresh.mutate()}
            disabled={refresh.isPending}
            title="Re-check the paths below and re-read each engine revision."
          >
            {refresh.isPending ? "Re-checking..." : "Re-check paths"}
          </button>
          <button
            className="btn-primary text-sm"
            onClick={() => browse.mutate()}
            disabled={!isRunningInTauri() || browse.isPending}
            title={
              isRunningInTauri()
                ? "Pick the folder that contains JA2.exe."
                : "Folder picking needs the desktop app."
            }
          >
            {browse.isPending ? "Opening..." : "Add install..."}
          </button>
        </div>
      </div>

      {browseError && (
        <div className="mb-3 text-xs text-rust-400">{browseError}</div>
      )}

      {installs.data?.length === 0 && (
        <p className="text-sm text-wasteland-400">
          No installs yet. Use <strong>Add install</strong> and pick the folder
          that contains <code className="text-rust-400">JA2.exe</code>.
        </p>
      )}

      <ul className="space-y-2">
        {installs.data?.map((info) => {
          const isActive = info.id === activeId;
          const uac = isUacProtectedPath(info.path);
          return (
            <li
              key={info.id}
              className={`flex items-center justify-between gap-4 rounded border p-3 ${
                isActive
                  ? "border-rust-500/60 bg-wasteland-800"
                  : "border-wasteland-700"
              }`}
            >
              <div className="flex-1 min-w-0">
                <div className="flex items-center gap-2 flex-wrap">
                  <span className="font-medium">{info.mod_display}</span>
                  <span className="badge bg-wasteland-700 text-wasteland-200">
                    {info.mod_id}
                  </span>
                  {isActive && (
                    <span className="badge bg-rust-500/20 text-rust-400">Active</span>
                  )}
                  {uac && (
                    <span
                      className="badge bg-amber-500/15 text-amber-300 border border-amber-500/40"
                      title={UAC_EXPLANATION}
                    >
                      ⚠ UAC-protected
                    </span>
                  )}
                </div>
                <div className="text-xs text-wasteland-400 truncate font-mono">
                  {info.path}
                </div>
                {uac && (
                  <p className="mt-1 text-[11px] text-amber-300/90">
                    Under Program Files — Windows may block edits here. Copying
                    the install to somewhere like <code>C:\Games\JA2_113</code>{" "}
                    and adding that folder avoids it.
                  </p>
                )}
              </div>
              <div className="flex items-center gap-2 shrink-0">
                {isActive ? (
                  <span className="text-xs text-wasteland-400">Current</span>
                ) : (
                  <button
                    className="btn-ghost text-sm"
                    onClick={() => setActive.mutate(info.id)}
                    disabled={setActive.isPending}
                  >
                    {setActive.isPending && setActive.variables === info.id
                      ? "Switching..."
                      : "Set active"}
                  </button>
                )}
                <button
                  className="btn-ghost text-sm text-wasteland-400 hover:text-rust-400"
                  onClick={() => setPendingRemoval(info)}
                  disabled={remove.isPending}
                  title="Forget this install. Nothing inside the game folder changes."
                >
                  Remove
                </button>
              </div>
            </li>
          );
        })}
      </ul>

      <ConfirmModal
        open={pendingRemoval != null}
        title="Remove this install?"
        body={
          <div className="space-y-2 text-sm">
            <p>
              Merc Forge forgets{" "}
              <code className="font-mono">{pendingRemoval?.path}</code>.
            </p>
            <p className="text-xs text-wasteland-400">
              Nothing inside the game folder is touched — no saves, backups or
              mod files are deleted. Add the folder again at any time to get it
              back. {pendingRemoval?.id === activeId
                && "This is the active install, so nothing will be active until you pick another."}
            </p>
            {remove.isError && (
              <p className="text-xs text-red-300">{formatApiError(remove.error)}</p>
            )}
          </div>
        }
        confirmLabel="Remove"
        busy={remove.isPending}
        onConfirm={() => pendingRemoval && remove.mutate(pendingRemoval.id)}
        onCancel={() => setPendingRemoval(null)}
      />
    </section>
  );
}
