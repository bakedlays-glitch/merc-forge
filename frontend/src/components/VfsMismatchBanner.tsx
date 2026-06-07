/**
 * Hub-level diagnostic: warn when the active install's bound
 * `vfs_config_path` disagrees with the VFS_CONFIG_INI line currently
 * written into the install's Ja2.ini.
 *
 * Bug-review B5 (CONFIRMED). After bug #11 removed the auto-apply on
 * activation, the user can register the same install folder twice —
 * once bound to AIMNAS, once to Wildfire — and switch between them in
 * MercWizard without Ja2.ini following along. The game engine reads
 * what Ja2.ini says; MercWizard reads what the activated entry says;
 * the user's edits land in a mod content layer the engine isn't loading
 * and "don't show up in-game."
 *
 * Pre-fix this banner tried to infer the mismatch from the frontend by
 * detecting a Vanilla.ini-active + 1.13-sibling-registered combo. That
 * heuristic missed the more common shape (Wildfire-active + AIMNAS
 * sibling, both modded), and the inference is fundamentally one the
 * backend can answer authoritatively — it has the install path and can
 * read Ja2.ini directly.
 *
 * Now: `/health` reports `vfs_mismatch: boolean | null`; this banner
 * just surfaces that flag and offers a one-click Apply-VFS button that
 * rewrites Ja2.ini to match the activated registration.
 */
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { applyVfsConfig, formatApiError, getHealth, listInstalls } from "../lib/api";

export default function VfsMismatchBanner() {
  const qc = useQueryClient();
  const installs = useQuery({ queryKey: ["installs"], queryFn: listInstalls });
  const health = useQuery({ queryKey: ["health"], queryFn: getHealth });
  const applyVfs = useMutation({
    mutationFn: (id: string) => applyVfsConfig(id),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["installs"] });
      qc.invalidateQueries({ queryKey: ["health"] });
    },
  });

  if (!installs.data || !health.data) return null;
  if (health.data.vfs_mismatch !== true) return null;

  const active = installs.data.find((i) => i.id === health.data.active_install_id);
  if (!active) return null;

  const activeConfigName = (active.vfs_config_path ?? "").split(/[\\/]/).pop() ?? "";

  return (
    <div className="rounded border border-amber-700/60 bg-amber-900/20 p-3 text-xs text-amber-200 space-y-2">
      <div className="font-semibold">
        ⚠ The game engine and Merc Wizard disagree about which mod is active
      </div>
      <div>
        This install is currently set up to run a different mod (or none) from
        the one you picked here (
        <code className="font-mono text-amber-300">{activeConfigName || "no mod"}</code>
        ). Merc Wizard edits whichever mod the install's own launch settings
        point at — the same one the game loads when you start it — so right now
        you may be working on a different mod than you intended. Use the button
        below to switch the install over to the mod you selected so it matches
        your choice.
      </div>
      <div className="rounded bg-amber-950/40 px-2 py-1 text-[11px]">
        Click below to rewrite <code className="font-mono">Ja2.ini</code> so
        the engine loads the same mod Merc Wizard is editing. A{" "}
        <code className="font-mono">.mwbak</code> backup is taken on first
        apply. <b>Save games are profile-scoped:</b> any saves under the
        previous VFS profile won't be visible in-game until you switch back.
      </div>
      {applyVfs.error && (
        <div className="rounded bg-red-950/60 px-2 py-1 text-[11px] text-red-200">
          {formatApiError(applyVfs.error)}
        </div>
      )}
      {applyVfs.data && (
        <div className="rounded bg-emerald-950/60 px-2 py-1 text-[11px] text-emerald-200">
          ✓ Wrote <code className="font-mono">{applyVfs.data.vfs_config_written}</code> to Ja2.ini.
          {applyVfs.data.backup_path && ` Backup at ${applyVfs.data.backup_path}.`}
        </div>
      )}
      <div className="flex justify-end">
        <button
          type="button"
          onClick={() => applyVfs.mutate(active.id)}
          disabled={applyVfs.isPending || !!applyVfs.data}
          className="rounded border border-amber-600 bg-amber-700/50 px-3 py-1 text-xs font-semibold text-amber-50 hover:bg-amber-700/70 disabled:opacity-50"
        >
          {applyVfs.isPending
            ? "Applying…"
            : applyVfs.data
              ? "Applied"
              : "Apply VFS to Ja2.ini"}
        </button>
      </div>
    </div>
  );
}
