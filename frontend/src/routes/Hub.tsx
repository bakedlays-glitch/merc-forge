import { useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";

import {
  applyVfsConfig,
  formatApiError,
  getGameStatus,
  getHealth,
  getIniDiagnostic,
  launchGame,
  listInstalls,
  setActiveInstall,
} from "../lib/api";
import type { InstallInfo } from "../lib/schema";
import FaceGearOrphanBanner from "../components/FaceGearOrphanBanner";
import VfsMismatchBanner from "../components/VfsMismatchBanner";

// Three subject-first primary tiles. The verb-first action list
// (Create / Edit / Copy / Move / Delete / Export / Import) used to
// live here as separate Hub cards; that was confusing because the
// user thinks "which MERC do I want to act on" before they think
// about the verb. Merc Wizard now opens the roster grid and the
// per-slot actions surface there via context menu + action bar.
// Map Forge is the sector editor. Tileset Editor edits tilesets.
//
// The Hub is laid out in two rows: a primary row of the three editors
// (game content authoring), and a secondary row of accessory tiles
// (Tools + Settings) below the section heading. Tools is install-
// independent utilities (STI viewer, SLF extractor, future toys);
// Settings configures the app.
const primary = [
  {
    id: "merc-wizard",
    label: "Merc Wizard",
    href: "/merc-wizard",
    icon: "🪪",
    description: "Browse your roster. Click any slot to create, edit, copy, move, delete, export, or import a merc — all from the same grid.",
  },
  {
    id: "mapforge",
    label: "Map Forge",
    href: "/mapforge",
    icon: "🗺️",
    description: "Open the JA2 sector editor. Paint tiles, stamp multi-tile structs, run generators.",
  },
  {
    id: "tileset-editor",
    label: "Tileset Editor",
    href: "/tileset-editor",
    icon: "🧱",
    description: "Build a tileset. Add STIs from the library, inject sub-frames into existing slots, view and edit JSD companions.",
  },
];

const secondary = [
  {
    id: "ini-editor",
    label: "INI Editor",
    href: "/ini-editor",
    icon: "🎛️",
    description: "Edit engine settings across 15 INI files: combat, economy, skills, AI. Override mode writes per-campaign override files; Edit INI mode modifies the files directly.",
  },
  {
    id: "backgrounds",
    label: "Backgrounds",
    href: "/backgrounds",
    icon: "📋",
    description: "Create, edit, and delete the stat/AP/perk background bundles mercs can carry — the catalog the merc Background dropdown picks from.",
  },
  {
    id: "tools",
    label: "Tools",
    href: "/tools",
    icon: "🧰",
    description: "Standalone utilities — open any .sti to inspect frames, or crack open a .slf archive to extract its files. Works on assets outside the active install.",
  },
  {
    id: "settings",
    label: "Settings",
    href: "/settings",
    icon: "⚙️",
    description: "Backup mode, mod-detection rules, debug tools.",
  },
];

// Secondary nav removed 2026-05-24 — "Browse Roster (raw)" was a
// duplicate of the Merc Wizard's roster grid; "Backups" now lives
// inside Settings. The four primary tiles cover everything.

export default function Hub() {
  const qc = useQueryClient();
  const health = useQuery({ queryKey: ["health"], queryFn: getHealth });
  const installs = useQuery({ queryKey: ["installs"], queryFn: listInstalls });
  const launch = useMutation({ mutationFn: () => launchGame() });
  const switchInstall = useMutation({
    mutationFn: (id: string) => setActiveInstall(id),
    onSuccess: () => {
      // Switching the active install changes EVERYTHING the wizard reads —
      // roster, per-slot detail, backups, voice clips, install metadata.
      // Reset the entire cache rather than invalidating prefix-by-prefix
      // so no stale data leaks from the previous install (Edit tab open
      // on slot 42 of install A would still show A's data after switch
      // to B unless we wipe).
      qc.resetQueries();
      setSwitcherOpen(false);
    },
  });
  const [switcherOpen, setSwitcherOpen] = useState(false);
  const [vfsConfirmOpen, setVfsConfirmOpen] = useState(false);
  // Click-outside dismiss for the install switcher popover. Without
  // this, the popover stays pinned until the user clicks Switch again,
  // which feels like a stuck overlay when they meant to click past
  // it. (Replace the expanding card with a contained popover that
  // doesn't shove page content down.)
  const switcherWrapRef = useRef<HTMLDivElement | null>(null);
  useEffect(() => {
    if (!switcherOpen) return;
    const onDocClick = (e: MouseEvent) => {
      if (!switcherWrapRef.current) return;
      if (switcherWrapRef.current.contains(e.target as Node)) return;
      setSwitcherOpen(false);
    };
    const onEsc = (e: KeyboardEvent) => {
      if (e.key === "Escape") setSwitcherOpen(false);
    };
    // Schedule the listener attach for the next tick so the click that
    // OPENED the popover (still bubbling) doesn't immediately close it.
    const t = window.setTimeout(() => {
      window.addEventListener("click", onDocClick);
    }, 0);
    window.addEventListener("keydown", onEsc);
    return () => {
      window.clearTimeout(t);
      window.removeEventListener("click", onDocClick);
      window.removeEventListener("keydown", onEsc);
    };
  }, [switcherOpen]);
  const applyVfs = useMutation({
    mutationFn: (id: string) => applyVfsConfig(id),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["installs"] });
      qc.invalidateQueries({ queryKey: ["health"] });
      // Apply-VFS changes the engine's active campaign → the INI editor's
      // profile root / override file / effective values are all stale now.
      // (2026-06-07 review finding: this path doesn't go through the
      // install-switch resetQueries(), so invalidate explicitly.)
      qc.invalidateQueries({ queryKey: ["ini-effective"] });
      qc.invalidateQueries({ queryKey: ["ini-overrides"] });
      qc.invalidateQueries({ queryKey: ["ini-summary"] });
      qc.invalidateQueries({ queryKey: ["ini-schemas"] });
    },
  });
  const gameStatus = useQuery({
    queryKey: ["game-status"],
    queryFn: getGameStatus,
    refetchInterval: 5000,
    enabled: Boolean(health.data?.active_install_id),
  });
  const diagnostic = useQuery({
    queryKey: ["ini-diagnostic"],
    queryFn: getIniDiagnostic,
    staleTime: 30_000,
    enabled: Boolean(health.data?.active_install_id),
  });

  const active = installs.data?.find((i) => i.id === health.data?.active_install_id);
  const others = (installs.data ?? []).filter((i) => i.id !== active?.id);

  return (
    <div className="mx-auto max-w-5xl px-6 py-8">
      <div className="flex items-center justify-between mb-6">
        <div className="min-w-0 flex-1">
          <h1 className="text-2xl font-bold">Merc Forge</h1>
          {active && (
            <div className="text-sm text-wasteland-300 mt-1 flex items-center gap-2 flex-wrap">
              <span className="badge bg-rust-500/20 text-rust-400">{active.mod_display}</span>
              <span className="font-mono text-xs truncate" title={active.path}>{active.path}</span>
              <span ref={switcherWrapRef} className="relative inline-block">
                <button
                  type="button"
                  className="text-xs text-wasteland-400 hover:text-rust-400 underline underline-offset-2"
                  onClick={() => setSwitcherOpen((v) => !v)}
                  aria-expanded={switcherOpen}
                  aria-haspopup="listbox"
                >
                  Switch install ▾
                </button>
                {/* Anchored popover. `absolute` + high z-index so it
                    overlays content below instead of pushing the
                    primary tile grid down. The old "expanding card"
                    placement was shoving the whole Hub down on click,
                    which felt like a layout glitch. */}
                {switcherOpen && (
                  <div className="absolute left-0 top-full z-30 mt-2 w-[28rem] max-w-[calc(100vw-3rem)] rounded-lg border border-wasteland-700 bg-wasteland-900 p-3 shadow-2xl">
                    <div className="flex items-center justify-between mb-2">
                      <h3 className="text-sm font-semibold">Switch active install</h3>
                      <Link
                        to="/first-run"
                        className="text-xs text-wasteland-400 hover:text-rust-400"
                        onClick={() => setSwitcherOpen(false)}
                      >
                        Add a different folder…
                      </Link>
                    </div>
                    {others.length === 0 ? (
                      <p className="text-sm text-wasteland-400">
                        No other installs registered. Use the link above to add one.
                      </p>
                    ) : (
                      <ul className="space-y-1.5 max-h-72 overflow-y-auto">
                        {others.map((info: InstallInfo) => (
                          <li
                            key={info.id}
                            className="flex items-center justify-between gap-3 rounded border border-wasteland-700 p-2"
                          >
                            <div className="min-w-0 flex-1">
                              <div className="flex items-center gap-2">
                                <span className="text-sm font-medium">{info.mod_display}</span>
                                <span className="badge bg-wasteland-700 text-wasteland-200">{info.mod_id}</span>
                              </div>
                              <div className="text-xs text-wasteland-400 font-mono truncate" title={info.path}>
                                {info.path}
                              </div>
                            </div>
                            <button
                              className="btn-ghost text-xs shrink-0"
                              disabled={switchInstall.isPending}
                              onClick={() => switchInstall.mutate(info.id)}
                            >
                              Switch
                            </button>
                          </li>
                        ))}
                      </ul>
                    )}
                  </div>
                )}
              </span>
              {/* Apply-VFS button — only shown when the active install
                  has a specific vfs_config (multi-VFS install). Bug
                  #11 follow-up: activation no longer auto-mutates
                  Ja2.ini; the user has to click this to opt in. The
                  confirmation modal explains save-game implications. */}
              {active.vfs_config_path && (
                <button
                  type="button"
                  className="text-xs text-amber-400 hover:text-amber-200 underline underline-offset-2"
                  title="Write this mod's vfs_config path into JA2.ini so the game engine loads this mod's content. Affects saved games."
                  onClick={() => setVfsConfirmOpen(true)}
                >
                  Apply VFS to JA2.ini
                </button>
              )}
            </div>
          )}

          {/* Apply-VFS confirmation modal. Bug #11 fix: explicit user
              consent before mutating the install's Ja2.ini. */}
          {vfsConfirmOpen && active && active.vfs_config_path && (
            <div
              className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm"
              onClick={() => setVfsConfirmOpen(false)}
            >
              <div
                className="w-[36rem] max-w-[90vw] rounded-lg border border-amber-700 bg-wasteland-900 p-4 shadow-2xl"
                onClick={(e) => e.stopPropagation()}
                role="dialog"
                aria-modal="true"
              >
                <h3 className="text-base font-semibold text-amber-200 mb-2">
                  Apply VFS config to JA2.ini?
                </h3>
                <p className="text-sm text-wasteland-200 mb-2">
                  This rewrites <code className="font-mono text-amber-300">JA2.ini</code>'s{" "}
                  <code className="font-mono text-amber-300">VFS_CONFIG_INI</code> line in the
                  install at:
                </p>
                <pre className="mb-3 rounded bg-wasteland-950 px-2 py-1 font-mono text-[11px] text-wasteland-300 overflow-x-auto">
                  {active.path}
                </pre>
                <p className="text-sm text-wasteland-200 mb-2">
                  New value: <code className="font-mono text-amber-300">{active.vfs_config_path}</code>
                </p>
                <div className="mb-3 rounded border border-amber-700/60 bg-amber-900/20 p-2 text-xs text-amber-200">
                  ⚠ Switching VFS configs redirects the game engine to a different mod's
                  content layer. Your existing saved games + custom settings under the
                  PREVIOUS VFS profile won't be visible in-game until you switch back.
                  They're not deleted — just on a different profile path.
                </div>
                <p className="text-[10px] text-wasteland-400 mb-3">
                  A backup of your current JA2.ini is saved at{" "}
                  <code className="font-mono">JA2.ini.mwbak</code> on the first apply
                  (idempotent — won't overwrite an existing one).
                </p>
                {applyVfs.error && (
                  <div className="mb-2 rounded bg-red-950/60 px-2 py-1 text-[11px] text-red-200">
                    {formatApiError(applyVfs.error)}
                  </div>
                )}
                {applyVfs.data && (
                  <div className="mb-2 rounded bg-emerald-950/60 px-2 py-1 text-[11px] text-emerald-200">
                    ✓ Wrote <code className="font-mono">{applyVfs.data.vfs_config_written}</code>.
                    {applyVfs.data.backup_path && ` Backup at ${applyVfs.data.backup_path}.`}
                  </div>
                )}
                <div className="flex justify-end gap-2">
                  <button
                    type="button"
                    onClick={() => setVfsConfirmOpen(false)}
                    disabled={applyVfs.isPending}
                    className="rounded border border-wasteland-700 bg-wasteland-800 px-3 py-1 text-xs text-wasteland-200 hover:border-wasteland-500 disabled:opacity-50"
                  >
                    {applyVfs.data ? "Close" : "Cancel"}
                  </button>
                  {!applyVfs.data && (
                    <button
                      type="button"
                      onClick={() => applyVfs.mutate(active.id)}
                      disabled={applyVfs.isPending}
                      className="rounded border border-amber-600 bg-amber-700/50 px-3 py-1 text-xs font-semibold text-amber-50 hover:bg-amber-700/70 disabled:opacity-50"
                    >
                      {applyVfs.isPending ? "Applying…" : "Confirm & Apply"}
                    </button>
                  )}
                </div>
              </div>
            </div>
          )}
          {/* Switcher popover lives anchored to its trigger button
              above (inside the active-install row). The old expanding
              card here was deleted 2026-05-25. */}
        </div>
        {active && (
          <div className="ml-4 flex flex-col items-end gap-1">
            <button
              className="btn-primary"
              onClick={() => launch.mutate()}
              disabled={launch.isPending || Boolean(gameStatus.data?.running)}
              title={gameStatus.data?.running ? "ja2.exe is already running" : undefined}
            >
              {gameStatus.data?.running
                ? "JA2 is running"
                : launch.isPending
                  ? "Launching..."
                  : "Launch JA2"}
            </button>
            {/* Last-launch health chip — deep-links to the INI editor where
                the offending keys can actually be fixed. */}
            {diagnostic.data?.last_launch_raw && (
              <Link
                to="/ini-editor"
                className={
                  "text-xs underline underline-offset-2 " +
                  (diagnostic.data.errors.filter((e) => !e.is_first_boot_noise).length > 0
                    ? "text-amber-400 hover:text-amber-200"
                    : "text-wasteland-500 hover:text-wasteland-300")
                }
                title={`Last launch: ${diagnostic.data.last_launch_raw}`}
              >
                {(() => {
                  const real = diagnostic.data.errors.filter((e) => !e.is_first_boot_noise).length;
                  return real > 0
                    ? `${real} INI error${real === 1 ? "" : "s"} last launch`
                    : "Last launch: no INI errors";
                })()}
              </Link>
            )}
          </div>
        )}
      </div>

      {/* Install-level diagnostics. Each banner renders nothing when
          its check passes; they only surface real install-level
          issues. Pulled out of per-merc displays per bug #1 / bug #9. */}
      <div className="mb-6 space-y-3">
        <VfsMismatchBanner />
        <FaceGearOrphanBanner />
      </div>

      <h2 className="text-xl text-wasteland-100 mb-4">What would you like to do?</h2>
      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4 mb-4">
        {primary.map((p) => (
          <Link
            key={p.id}
            to={p.href}
            className="card flex flex-col gap-3 hover:border-rust-500 transition-colors group min-h-[12rem]"
          >
            <div className="flex items-center justify-between">
              <div className="flex items-center gap-3">
                <span className="text-3xl" aria-hidden>{p.icon}</span>
                <span className="text-xl font-semibold group-hover:text-rust-400 transition-colors">
                  {p.label}
                </span>
              </div>
              <div className="text-rust-400 opacity-0 group-hover:opacity-100 transition-opacity">→</div>
            </div>
            <div className="text-sm text-wasteland-300 flex-1">
              {p.description}
            </div>
          </Link>
        ))}
      </div>
      <div className="grid grid-cols-1 md:grid-cols-2 gap-4 mb-8">
        {secondary.map((s) => (
          <Link
            key={s.id}
            to={s.href}
            className="card flex flex-col gap-3 hover:border-rust-500 transition-colors group min-h-[10rem]"
          >
            <div className="flex items-center justify-between">
              <div className="flex items-center gap-3">
                <span className="text-3xl" aria-hidden>{s.icon}</span>
                <span className="text-xl font-semibold group-hover:text-rust-400 transition-colors">
                  {s.label}
                </span>
              </div>
              <div className="text-rust-400 opacity-0 group-hover:opacity-100 transition-opacity">→</div>
            </div>
            <div className="text-sm text-wasteland-300 flex-1">
              {s.description}
            </div>
          </Link>
        ))}
      </div>

      {launch.isError && (
        <div className="mt-4 text-sm text-rust-400">
          {formatApiError(launch.error)}
        </div>
      )}
    </div>
  );
}
