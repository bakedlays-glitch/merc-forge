import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import {
  addInstall,
  ApiError,
  applyVfsConfig,
  formatApiError,
  listInstalls,
  scanVfsConfigs,
  setActiveInstall,
  type ScanVfsConfigsResponse,
  type VfsConfigEntry,
} from "../lib/api";
import { pickDirectory, isRunningInTauri } from "../lib/tauri";
import type { InstallInfo } from "../lib/schema";

/** Bug #12: the FirstRun page no longer runs background auto-detection.
 * Detection is a wizard:
 *   1. User picks an install folder.
 *   2. We scan for `vfs_config.*.ini` files in that folder.
 *   3. If any exist, the user picks one. The picker flags whichever
 *      config is currently active in `JA2.ini`.
 *   4. Picking the active one = silent register. Picking a different
 *      one = save-game warning modal → register + apply VFS.
 *   5. Legacy installs (no vfs_configs) skip the picker.
 */
export default function FirstRun() {
  const qc = useQueryClient();
  const navigate = useNavigate();
  const installs = useQuery({ queryKey: ["installs"], queryFn: listInstalls });

  const [manualError, setManualError] = useState<string | null>(null);
  /** Folder + vfs_configs the user just selected. Open => picker visible. */
  const [vfsPicker, setVfsPicker] = useState<ScanVfsConfigsResponse | null>(null);
  /** The non-active config the user clicked. Open => save-game warning visible. */
  const [vfsConfirm, setVfsConfirm] = useState<VfsConfigEntry | null>(null);
  /** Search filter for the registered-installs list. */
  const [search, setSearch] = useState("");

  const setActive = useMutation({
    mutationFn: (id: string) => setActiveInstall(id),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["health"] });
      navigate("/hub");
    },
  });

  /** Helper: register the install (optionally with a chosen vfs_config),
   *  optionally apply that vfs_config to JA2.ini, then activate +
   *  navigate to the hub. */
  async function finishRegistration(
    path: string,
    cfgPath: string | null,
    applyVfs: boolean,
  ): Promise<void> {
    const info = await addInstall(path, cfgPath);
    qc.invalidateQueries({ queryKey: ["installs"] });
    await setActiveInstall(info.id);
    if (applyVfs && cfgPath) {
      try {
        await applyVfsConfig(info.id);
      } catch (e) {
        // Don't lose the registration if the apply fails — surface and stay.
        setManualError(
          `Registered the install, but couldn't write the VFS config to JA2.ini: ${formatApiError(e)}`,
        );
        qc.invalidateQueries({ queryKey: ["health"] });
        navigate("/hub");
        return;
      }
    }
    qc.invalidateQueries({ queryKey: ["health"] });
    navigate("/hub");
  }

  const addManual = useMutation({
    mutationFn: async () => {
      setManualError(null);
      const path = await pickDirectory(
        "Pick the folder that contains your JA2 executable",
      );
      if (!path) return null;
      // Program-Files write-protection guard (carry-over from Bug #75).
      // Windows blocks ordinary programs from writing inside
      // `C:\Program Files` / `C:\Program Files (x86)`. For a 64-bit
      // manifested app like this one, writes are simply refused (Access
      // Denied / UAC), so merc edits never reach the game. Catch this
      // BEFORE registering so the user can move the folder first.
      const normalized = path.replace(/\\/g, "/").toLowerCase();
      if (
        normalized.startsWith("c:/program files/") ||
        normalized.startsWith("c:/program files (x86)/")
      ) {
        setManualError(
          `${path} is inside Program Files. Windows blocks ordinary programs ` +
            `from writing there, so Merc Forge can't save this merc's files, ` +
            `portrait, or starting gear into the game — the changes are refused ` +
            `and never reach JA2. Move the JA2 folder somewhere unprotected ` +
            `(e.g. C:\\Games\\JA2_113) first, then re-pick.`,
        );
        return null;
      }
      // Preflight: enumerate vfs_configs so the user can pick a mod profile.
      try {
        const scan = await scanVfsConfigs(path);
        if (scan.configs.length === 0) {
          // Legacy / single-layer install — no picker needed.
          await finishRegistration(path, null, false);
          return null;
        }
        setVfsPicker(scan);
        return null;
      } catch (e) {
        if (e instanceof ApiError) {
          const detail = e.detail as { errors?: string[]; message?: string } | null;
          setManualError(
            detail?.errors?.[0] ??
              detail?.message ??
              "That folder didn't look like a JA2 install.",
          );
        } else {
          setManualError(formatApiError(e));
        }
        return null;
      }
    },
  });

  const registerFromPicker = useMutation({
    mutationFn: async (args: {
      cfg: VfsConfigEntry;
      applyVfs: boolean;
    }) => {
      if (!vfsPicker) return null;
      await finishRegistration(vfsPicker.install_path, args.cfg.path, args.applyVfs);
      setVfsPicker(null);
      setVfsConfirm(null);
      return null;
    },
  });

  function handlePickerClick(cfg: VfsConfigEntry) {
    if (cfg.is_active) {
      // Same config the engine already mounts — no JA2.ini mutation
      // needed, no save-game implications, no warning.
      registerFromPicker.mutate({ cfg, applyVfs: false });
    } else {
      // Different from JA2.ini's current line — show the save-game
      // warning before mutating.
      setVfsConfirm(cfg);
    }
  }

  // Registered-installs list. With auto-detect gone, every entry here
  // is something the user manually added; the count is small (no more
  // 300-card multi-VFS expansions). Plain search + flat list.
  const rawItems = installs.data ?? [];
  const items = rawItems.filter((info: InstallInfo) => {
    if (!search.trim()) return true;
    const q = search.trim().toLowerCase();
    return (
      info.mod_display.toLowerCase().includes(q) ||
      info.path.toLowerCase().includes(q) ||
      info.mod_id.toLowerCase().includes(q)
    );
  });

  return (
    <div className="mx-auto max-w-2xl px-6 py-12">
      <h1 className="text-3xl font-bold text-wasteland-50 mb-2">Welcome to Merc Forge</h1>
      <p className="text-wasteland-300 mb-8">
        Manage mercenaries in your Jagged Alliance 2 v1.13 game. Point it at your install
        folder to get started.
      </p>

      {/* PRIMARY action: pick a folder */}
      <div className="card mb-4 border-rust-500/40 bg-wasteland-800">
        <div className="flex items-start justify-between gap-4">
          <div className="flex-1">
            <h2 className="text-lg font-semibold mb-1">Choose your JA2 install folder</h2>
            <p className="text-sm text-wasteland-300">
              Pick the folder that contains <code className="text-rust-400">JA2.exe</code>
              {" "}(or <code className="text-rust-400">ja2_1.13.exe</code>).
            </p>
            {manualError && (
              <div className="text-sm text-rust-400 mt-2">{manualError}</div>
            )}
          </div>
          <button
            className="btn-primary"
            onClick={() => addManual.mutate()}
            disabled={!isRunningInTauri() || addManual.isPending || registerFromPicker.isPending}
          >
            {addManual.isPending ? "Opening..." : "Browse..."}
          </button>
        </div>
      </div>

      {/* Registered installs (anything previously added). */}
      <div className="card">
        <div className="flex items-center justify-between mb-2">
          <h2 className="text-sm font-semibold text-wasteland-200">
            Registered installs
          </h2>
        </div>

        {rawItems.length > 0 && (
          <div className="mb-2">
            <input
              type="text"
              className="input text-sm w-full"
              placeholder="Filter installs by name or path..."
              value={search}
              onChange={(e) => setSearch(e.target.value)}
            />
          </div>
        )}

        {rawItems.length === 0 && (
          <p className="text-wasteland-400 text-sm">
            No installs yet. Use the Browse button above to add one.
          </p>
        )}

        {rawItems.length > 0 && items.length === 0 && (
          <p className="text-wasteland-400 text-sm">
            Nothing matches that filter.
          </p>
        )}

        {items.length > 0 && (
          <ul className="space-y-2 mt-2">
            {items.map((info) => (
              <li
                key={info.id}
                className="flex items-center justify-between gap-3 rounded border border-wasteland-700 p-2.5"
              >
                <div className="min-w-0 flex-1">
                  <div className="flex items-center gap-2 mb-1 flex-wrap">
                    <span className="font-medium text-sm">{info.mod_display}</span>
                    {info.mod_id !== "unknown" && info.mod_id !== "vanilla" && !info.vfs_config_path && (
                      <span className="badge bg-wasteland-700 text-wasteland-200">{info.mod_id}</span>
                    )}
                    {info.engine_version && (
                      <span className="badge bg-wasteland-700 text-rust-300 font-mono">
                        {info.engine_version}
                      </span>
                    )}
                  </div>
                  <div className="text-xs text-wasteland-400 truncate font-mono">
                    {info.path}
                  </div>
                </div>
                <button
                  className="btn-secondary text-xs py-1 whitespace-nowrap"
                  onClick={() => setActive.mutate(info.id)}
                  disabled={setActive.isPending}
                >
                  Use this
                </button>
              </li>
            ))}
          </ul>
        )}
      </div>

      {vfsPicker && (
        <VfsPickerModal
          scan={vfsPicker}
          busy={registerFromPicker.isPending}
          onPick={handlePickerClick}
          onCancel={() => {
            setVfsPicker(null);
            setVfsConfirm(null);
          }}
        />
      )}

      {vfsConfirm && vfsPicker && (
        <VfsApplyConfirmModal
          cfg={vfsConfirm}
          installPath={vfsPicker.install_path}
          busy={registerFromPicker.isPending}
          onCancel={() => setVfsConfirm(null)}
          onConfirm={() =>
            registerFromPicker.mutate({ cfg: vfsConfirm, applyVfs: true })
          }
        />
      )}
    </div>
  );
}

interface VfsPickerProps {
  scan: ScanVfsConfigsResponse;
  busy: boolean;
  onPick: (cfg: VfsConfigEntry) => void;
  onCancel: () => void;
}

function VfsPickerModal({ scan, busy, onPick, onCancel }: VfsPickerProps) {
  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm"
      onClick={onCancel}
    >
      <div
        className="w-[42rem] max-w-[92vw] rounded-lg border border-rust-700 bg-wasteland-900 p-4 shadow-2xl"
        onClick={(e) => e.stopPropagation()}
        role="dialog"
        aria-modal="true"
      >
        <h3 className="text-base font-semibold text-rust-200 mb-2">
          Pick a mod profile
        </h3>
        <p className="text-sm text-wasteland-300 mb-2">
          This install ships {scan.configs.length} VFS config{scan.configs.length === 1 ? "" : "s"}.
          Each one mounts a different stack of game directories — different mod content,
          different saved-games folder. Pick the one you want to edit.
        </p>
        <pre className="mb-3 rounded bg-wasteland-950 px-2 py-1 font-mono text-[11px] text-wasteland-300 overflow-x-auto">
          {scan.install_path}
        </pre>
        {scan.active_relative_path === null && (
          <div className="mb-2 rounded border border-amber-700/60 bg-amber-900/20 p-2 text-xs text-amber-200">
            <code className="font-mono">JA2.ini</code> has no{" "}
            <code className="font-mono">VFS_CONFIG_INI</code> line. Whichever profile you
            pick will be written to JA2.ini (a one-time backup is taken first).
          </div>
        )}
        <ul className="space-y-1.5 mb-3 max-h-[18rem] overflow-y-auto">
          {scan.configs.map((cfg) => (
            <li key={cfg.path}>
              <button
                type="button"
                className={`w-full text-left rounded border p-2 transition-colors ${
                  cfg.is_active
                    ? "border-emerald-700/60 bg-emerald-900/20 hover:border-emerald-500"
                    : "border-wasteland-700 bg-wasteland-800 hover:border-rust-500"
                }`}
                onClick={() => onPick(cfg)}
                disabled={busy}
              >
                <div className="flex items-center gap-2 mb-0.5">
                  <span className="text-sm font-medium">{cfg.mod_name}</span>
                  {cfg.is_active && (
                    <span className="badge bg-emerald-500/20 text-emerald-300">
                      Current in JA2.ini
                    </span>
                  )}
                  {cfg.is_stock && (
                    <span
                      className="badge bg-wasteland-700 text-wasteland-300"
                      title="Bundled 1.13 fallback config — usually not what a modder wants to pick"
                    >
                      stock
                    </span>
                  )}
                </div>
                <div className="text-[11px] text-wasteland-400 font-mono truncate">
                  {cfg.relative_path}
                </div>
              </button>
            </li>
          ))}
        </ul>
        <p className="text-[11px] text-wasteland-400 mb-3">
          Picking the currently-active config registers the install with zero changes
          to <code className="font-mono">JA2.ini</code>. Picking a different one shows a
          save-game warning first.
        </p>
        <div className="flex justify-end gap-2">
          <button
            type="button"
            onClick={onCancel}
            disabled={busy}
            className="rounded border border-wasteland-700 bg-wasteland-800 px-3 py-1 text-xs text-wasteland-200 hover:border-wasteland-500 disabled:opacity-50"
          >
            Cancel
          </button>
        </div>
      </div>
    </div>
  );
}

interface VfsApplyConfirmProps {
  cfg: VfsConfigEntry;
  installPath: string;
  busy: boolean;
  onCancel: () => void;
  onConfirm: () => void;
}

function VfsApplyConfirmModal({
  cfg,
  installPath,
  busy,
  onCancel,
  onConfirm,
}: VfsApplyConfirmProps) {
  return (
    <div
      className="fixed inset-0 z-[60] flex items-center justify-center bg-black/70 backdrop-blur-sm"
      onClick={onCancel}
    >
      <div
        className="w-[36rem] max-w-[90vw] rounded-lg border border-amber-700 bg-wasteland-900 p-4 shadow-2xl"
        onClick={(e) => e.stopPropagation()}
        role="dialog"
        aria-modal="true"
      >
        <h3 className="text-base font-semibold text-amber-200 mb-2">
          Switch the install's active VFS profile?
        </h3>
        <p className="text-sm text-wasteland-200 mb-2">
          You picked a profile that's <strong>different</strong> from what{" "}
          <code className="font-mono text-amber-300">JA2.ini</code> currently mounts.
          To make this profile load in-game, Merc Forge will rewrite{" "}
          <code className="font-mono text-amber-300">JA2.ini</code>'s{" "}
          <code className="font-mono text-amber-300">VFS_CONFIG_INI</code> line in:
        </p>
        <pre className="mb-3 rounded bg-wasteland-950 px-2 py-1 font-mono text-[11px] text-wasteland-300 overflow-x-auto">
          {installPath}
        </pre>
        <p className="text-sm text-wasteland-200 mb-2">
          New value:{" "}
          <code className="font-mono text-amber-300">{cfg.relative_path}</code>
        </p>
        <div className="mb-3 rounded border border-amber-700/60 bg-amber-900/20 p-2 text-xs text-amber-200">
          ⚠ Switching VFS profiles redirects the game engine to a different mod's
          content layer. Your existing saved games + custom settings under the
          PREVIOUS VFS profile won't be visible in-game until you switch back.
          They're not deleted — just on a different profile path.
        </div>
        <p className="text-[10px] text-wasteland-400 mb-3">
          A backup of your current JA2.ini is saved at{" "}
          <code className="font-mono">JA2.ini.mwbak</code> on the first apply
          (idempotent — won't overwrite an existing one).
        </p>
        <div className="flex justify-end gap-2">
          <button
            type="button"
            onClick={onCancel}
            disabled={busy}
            className="rounded border border-wasteland-700 bg-wasteland-800 px-3 py-1 text-xs text-wasteland-200 hover:border-wasteland-500 disabled:opacity-50"
          >
            Cancel
          </button>
          <button
            type="button"
            onClick={onConfirm}
            disabled={busy}
            className="rounded border border-amber-600 bg-amber-700/50 px-3 py-1 text-xs font-semibold text-amber-50 hover:bg-amber-700/70 disabled:opacity-50"
          >
            {busy ? "Applying…" : "Confirm & Update VFS"}
          </button>
        </div>
      </div>
    </div>
  );
}
