/**
 * Settings — app-level configuration that is not owned by one editor.
 *
 * Deliberately a composition only: every section is its own component
 * under components/settings/. Ordered by how often it is needed —
 * installs first, tool configuration next, About last.
 *
 * MapForge's own editor preferences (hotkeys, brush defaults, engine slot
 * cap) live in the editor, not here; this page only points at them.
 */
import { useState } from "react";
import { Link } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";

import { getHealth } from "../lib/api";
import { SLOT_LOCK_TIERS, unsuppressLockTier } from "../lib/slotLocks";
import AboutPanel from "../components/settings/AboutPanel";
import InstallList from "../components/settings/InstallList";
import ReferenceInstallField from "../components/settings/ReferenceInstallField";
import VoiceLabSettings from "../components/settings/VoiceLabSettings";

export default function Settings() {
  const health = useQuery({ queryKey: ["health"], queryFn: getHealth });

  return (
    <div className="mx-auto max-w-3xl px-6 py-8 space-y-6">
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-bold">Settings</h1>
        <Link to="/" className="btn-ghost text-sm">
          ← Back to Hub
        </Link>
      </div>

      <InstallList />

      <section className="card">
        <h2 className="text-lg font-semibold mb-2">INI Editor — reference install</h2>
        <p className="text-sm text-wasteland-300 mb-3">
          Edit INI can diff your mod against this install and offer "Reset to
          reference value". It is a reference <em>you</em> pick, not necessarily
          stock 1.13.
        </p>
        <ReferenceInstallField />
      </section>

      {/* The Graphics stack section lived here. Removed for beta.4.
          components/settings/GraphicsStation.tsx and the /graphics endpoints
          remain; restoring it means re-adding the import and this section. */}

      <VoiceLabSettings />

      <section className="card space-y-3">
        <h2 className="text-lg font-semibold">Elsewhere</h2>
        <SettingsPointer
          to="/backups"
          label="Backups"
          note="Every edit snapshots the files it touches. The 50 most recent snapshots per install are kept; older ones are pruned automatically."
        />
        <div className="text-sm">
          <div className="text-wasteland-200">MapForge editor</div>
          <p className="text-xs text-wasteland-400">
            Hotkeys, brush defaults and the engine slot cap live in the map
            editor's own settings, reachable from its toolbar.
          </p>
        </div>
        <HiddenWarningsReset />
      </section>

      <AboutPanel sidecarVersion={health.data?.version} />
    </div>
  );
}

function SettingsPointer({ to, label, note }: { to: string; label: string; note: string }) {
  return (
    <div className="flex items-start justify-between gap-4 text-sm">
      <div>
        <div className="text-wasteland-200">{label}</div>
        <p className="text-xs text-wasteland-400">{note}</p>
      </div>
      <Link to={to} className="btn-ghost shrink-0 text-sm">
        Open →
      </Link>
    </div>
  );
}

/** "Don't show this again" on a slot-lock warning writes to localStorage
 * and there was no way back — this is the way back. */
function HiddenWarningsReset() {
  const [done, setDone] = useState(false);
  return (
    <div className="flex items-start justify-between gap-4 text-sm">
      <div>
        <div className="text-wasteland-200">Hidden slot warnings</div>
        <p className="text-xs text-wasteland-400">
          Show the overwrite warnings you dismissed with "Don't show again".
        </p>
      </div>
      <button
        className="btn-ghost shrink-0 text-sm"
        onClick={() => {
          for (const tier of SLOT_LOCK_TIERS) unsuppressLockTier(tier);
          setDone(true);
        }}
      >
        {done ? "✓ Restored" : "Show again"}
      </button>
    </div>
  );
}
