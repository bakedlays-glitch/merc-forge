import { useState } from "react";
import { Link } from "react-router-dom";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";

import {
  addInstall,
  ApiError,
  getHealth,
  listInstalls,
  refreshInstalls,
  setActiveInstall,
} from "../lib/api";
import { isRunningInTauri, pickDirectory } from "../lib/tauri";

/** True when a path lives under Windows' UAC-protected program dirs.
 * Writing inside Program Files / Program Files (x86) requires
 * admin elevation — Merc Forge doesn't run elevated, so any save /
 * backup / extract op will silently fail or trigger UAC prompts.
 * Surface this BEFORE the user tries to edit. */
function isUacProtectedPath(p: string): boolean {
  if (!p) return false;
  // Compare case-insensitively against common variants. Windows paths
  // can come with either slash style; normalize then probe.
  const norm = p.replace(/\//g, "\\").toLowerCase();
  return (
    norm.includes("\\program files (x86)\\")
    || norm.includes("\\program files\\")
    || norm.endsWith("\\program files")
    || norm.endsWith("\\program files (x86)")
  );
}

export default function Settings() {
  const qc = useQueryClient();
  const installs = useQuery({ queryKey: ["installs"], queryFn: listInstalls });
  const health = useQuery({ queryKey: ["health"], queryFn: getHealth });
  const [browseError, setBrowseError] = useState<string | null>(null);
  const [rescanFeedback, setRescanFeedback] = useState<string | null>(null);

  const refresh = useMutation({
    mutationFn: refreshInstalls,
    onSuccess: (data) => {
      qc.invalidateQueries({ queryKey: ["installs"] });
      setRescanFeedback(`Found ${data.length} install${data.length === 1 ? "" : "s"}.`);
      setTimeout(() => setRescanFeedback(null), 3000);
    },
  });

  const setActive = useMutation({
    mutationFn: (id: string) => setActiveInstall(id),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["health"] });
      qc.invalidateQueries({ queryKey: ["roster"] });
    },
  });

  const browse = useMutation({
    mutationFn: async () => {
      setBrowseError(null);
      const path = await pickDirectory(
        "Pick the folder that contains your JA2 executable",
      );
      if (!path) return null;
      // UAC pre-flight. Bail BEFORE registering if the picked path is
      // under Program Files — writes inside there will fail or pop UAC
      // prompts for every save/edit, and registering it just to fail
      // later is worse UX than refusing up-front with a clear reason.
      if (isUacProtectedPath(path)) {
        setBrowseError(
          `"${path}" is under Windows' Program Files folder, which is `
          + `UAC-protected. Merc Forge can't reliably save/edit files there `
          + `without admin elevation. Copy the JA2 install to a folder `
          + `outside Program Files (e.g. C:\\Games\\JA2_113) and re-register `
          + `from there.`
        );
        return null;
      }
      let info;
      try {
        info = await addInstall(path);
        qc.invalidateQueries({ queryKey: ["installs"] });
      } catch (e) {
        if (e instanceof ApiError) {
          const detail = e.detail as { errors?: string[]; message?: string } | null;
          setBrowseError(
            detail?.errors?.[0] ??
              detail?.message ??
              "That folder didn't look like a JA2 install.",
          );
        } else {
          setBrowseError("Couldn't read that folder.");
        }
        return null;
      }
      try {
        await setActiveInstall(info.id);
        qc.invalidateQueries({ queryKey: ["health"] });
        qc.invalidateQueries({ queryKey: ["roster"] });
      } catch {
        // The install was added successfully but activating it failed.
        // Don't lose that work — leave it in the list and tell the user
        // how to activate it manually.
        setBrowseError(
          `Added "${info.path}", but couldn't make it active automatically. ` +
            `Click "Set active" next to it in the list below.`,
        );
      }
      return info;
    },
  });

  const activeId = health.data?.active_install_id ?? null;

  return (
    <div className="mx-auto max-w-3xl px-6 py-8 space-y-6">
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-bold">Settings</h1>
        <Link to="/" className="btn-ghost text-sm">
          ← Back to Hub
        </Link>
      </div>

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
            >
              {refresh.isPending ? "Re-scanning..." : "Re-scan"}
            </button>
          </div>
        </div>

        <div className="mb-3 rounded border border-rust-500/40 bg-wasteland-800 p-3">
          <div className="flex items-start justify-between gap-3">
            <div className="flex-1">
              <div className="text-sm font-medium">Add an install folder</div>
              <p className="text-xs text-wasteland-300 mt-0.5">
                Pick the folder that contains <code className="text-rust-400">JA2.exe</code>.
                Use this when auto-detect missed an install.
              </p>
              {browseError && (
                <div className="text-xs text-rust-400 mt-1.5">{browseError}</div>
              )}
            </div>
            <button
              className="btn-primary text-sm"
              onClick={() => browse.mutate()}
              disabled={!isRunningInTauri() || browse.isPending}
            >
              {browse.isPending ? "Opening..." : "Browse..."}
            </button>
          </div>
        </div>

        {installs.data && installs.data.length === 0 && (
          <p className="text-sm text-wasteland-400">
            No installs registered yet. Use Browse to add one, or Re-scan to auto-detect.
          </p>
        )}

        <ul className="space-y-2">
          {installs.data?.map((info) => {
            const isActive = info.id === activeId;
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
                    {isUacProtectedPath(info.path) && (
                      <span
                        className="badge bg-amber-500/15 text-amber-300 border border-amber-500/40"
                        title={
                          "This install lives under Program Files. Windows UAC blocks "
                          + "writes there for non-admin processes, so Merc Forge's edits "
                          + "(merc saves, backups, SLF extract, .dat paint, JSD writes) "
                          + "may silently fail or pop UAC prompts. Recommended fix: copy "
                          + "the JA2 install to a folder outside Program Files (e.g. "
                          + "C:\\Games\\JA2_113), then re-register from there."
                        }
                      >
                        ⚠ UAC-protected
                      </span>
                    )}
                  </div>
                  <div className="text-xs text-wasteland-400 truncate font-mono">
                    {info.path}
                  </div>
                  {isUacProtectedPath(info.path) && (
                    <p className="mt-1 text-[11px] text-amber-300/90">
                      This install sits under Program Files. Windows will block our edits.
                      Copy the JA2 folder somewhere like <code>C:\Games\JA2_113</code> and
                      re-register, or run Merc Forge as administrator (not recommended).
                    </p>
                  )}
                </div>
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
              </li>
            );
          })}
        </ul>
      </section>

      <section className="card">
        <div className="flex items-center justify-between mb-2">
          <h2 className="text-lg font-semibold">Diagnostics</h2>
        </div>
        <p className="text-sm text-wasteland-300 mb-3">
          When something looks wrong, the sidecar's logs are the first place to
          look. They live alongside the app's state and capture both Python
          tracebacks and the Tauri shell's stderr.
        </p>
        <LogsLocation />
      </section>

      <section className="card">
        <h2 className="text-lg font-semibold mb-2">Backups</h2>
        <p className="text-sm text-wasteland-300 mb-3">
          Every edit and save creates a snapshot of the affected files
          so you can roll back if something goes wrong. The Backups page
          lists every snapshot for the active install and lets you
          restore individual ones.
        </p>
        <Link
          to="/backups"
          className="inline-block rounded border border-wasteland-700 bg-wasteland-800 px-3 py-1.5 text-sm hover:border-rust-500 hover:bg-wasteland-700"
        >
          Open Backups →
        </Link>
      </section>

      <section className="card">
        <h2 className="text-lg font-semibold mb-2">About</h2>
        <p className="text-sm text-wasteland-300 mb-3">
          Merc Forge v2.0.0 — open source under the MIT license.
        </p>
        <BuildInfo
          sidecarVersion={health.data?.version}
        />
      </section>
    </div>
  );
}

/**
 * Surfaces the running app's build provenance: the frontend bundle's
 * build timestamp (injected by Vite at build time) and the sidecar
 * version reported by /health. Lets a user tell at a glance whether the
 * shell they're staring at matches what they just rebuilt — answers the
 * recurring "I edited the source but the running app shows old text"
 * question without having to dig through file mtimes. Bug-review #94.
 */
function BuildInfo({ sidecarVersion }: { sidecarVersion?: string }) {
  const built = __BUILD_TIMESTAMP__;
  // Convert ISO → local-time-formatted string. The raw ISO is exact but
  // hard to read; locale string surfaces "May 23, 2026, 4:47 PM" which
  // is easier to compare against "when did I last rebuild?".
  let pretty: string = built;
  try {
    const d = new Date(built);
    if (!Number.isNaN(d.getTime())) {
      pretty = d.toLocaleString();
    }
  } catch {
    // Keep raw ISO if locale formatting fails for any reason.
  }
  // Minutes-since-build is the actually-useful answer for the "is this
  // the rebuild I just kicked off?" question.
  const minutesAgo = (() => {
    try {
      const d = new Date(built);
      if (Number.isNaN(d.getTime())) return null;
      const diffMs = Date.now() - d.getTime();
      const mins = Math.floor(diffMs / 60_000);
      if (mins < 1) return "just now";
      if (mins < 60) return `${mins} min ago`;
      const hours = Math.floor(mins / 60);
      if (hours < 24) return `${hours} hr ago`;
      const days = Math.floor(hours / 24);
      return `${days} day${days === 1 ? "" : "s"} ago`;
    } catch {
      return null;
    }
  })();
  return (
    <div className="grid grid-cols-[max-content_1fr] gap-x-3 gap-y-1 text-xs">
      <div className="text-wasteland-500">Frontend built</div>
      <div className="text-wasteland-200 font-mono">
        {pretty}
        {minutesAgo && (
          <span className="text-wasteland-500 font-sans ml-2">({minutesAgo})</span>
        )}
      </div>
      <div className="text-wasteland-500">Sidecar version</div>
      <div className="text-wasteland-200 font-mono">
        {sidecarVersion ?? <span className="text-wasteland-500">unknown</span>}
      </div>
    </div>
  );
}

/** Diagnostics row that displays the sidecar log folder location and
 * offers a one-click open. Falls back to a copyable code block if the
 * Tauri shell open API isn't available. */
function LogsLocation() {
  const [copied, setCopied] = useState(false);
  // The path is hard-coded to the sidecar's known log dir under %APPDATA%.
  // This mirrors what main.py:setup_logging configures. If you change
  // the path there, change it here too.
  const logsPath = "%APPDATA%\\MercWizard\\logs";
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(logsPath);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1500);
    } catch {
      // Clipboard unavailable — silent.
    }
  };
  return (
    <div className="space-y-2">
      <div className="text-xs text-wasteland-400">Log folder</div>
      <div className="flex items-center gap-2">
        <code className="block flex-1 truncate rounded border border-wasteland-700 bg-wasteland-900 px-2 py-1.5 font-mono text-xs">
          {logsPath}
        </code>
        <button
          type="button"
          onClick={copy}
          className="rounded border border-wasteland-700 bg-wasteland-800 px-3 py-1.5 text-xs hover:border-rust-500 hover:bg-wasteland-700"
          title="Copy the logs path to the clipboard. Paste into Win+R or File Explorer to open."
        >
          {copied ? "Copied!" : "Copy path"}
        </button>
      </div>
      <p className="text-[11px] text-wasteland-500">
        Paste this into File Explorer's address bar or the Win+R "Run" dialog
        to open. The main files are <code>sidecar.log</code> (Python /
        FastAPI) and <code>shell_rCURRENT.log</code> (Tauri / Rust).
      </p>
    </div>
  );
}
