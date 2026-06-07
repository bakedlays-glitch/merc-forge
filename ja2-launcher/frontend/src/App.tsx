import { useEffect, useState } from "react";
import {
  detectModpackFolder,
  loadModpack,
  getActiveCampaign,
  setActiveCampaign,
  launchGame,
} from "./api/launcher";
import type { ModpackManifest, Campaign } from "./types/modpack";
import { CampaignList } from "./components/CampaignList";
import { Tabs } from "./components/Tabs";
import { SettingsTab } from "./components/SettingsTab";
import { PresetsTab } from "./components/PresetsTab";
import { WizardTab } from "./components/WizardTab";
import { DiagnosticTab } from "./components/DiagnosticTab";

type LoadState =
  | { kind: "loading" }
  | { kind: "no-modpack" }
  | { kind: "error"; message: string }
  | { kind: "ready"; folder: string; manifest: ModpackManifest; activeVfs: string };

type TabId = "campaigns" | "settings" | "presets" | "wizard" | "diagnostic";

export default function App() {
  const [state, setState] = useState<LoadState>({ kind: "loading" });
  const [busy, setBusy] = useState<string | null>(null);
  const [tab, setTab] = useState<TabId>("campaigns");

  useEffect(() => {
    (async () => {
      try {
        const folder = await detectModpackFolder();
        if (!folder) {
          setState({ kind: "no-modpack" });
          return;
        }
        const manifest = await loadModpack(folder);
        const activeVfs = await getActiveCampaign(folder).catch(() => "");
        setState({ kind: "ready", folder, manifest, activeVfs });
      } catch (e) {
        setState({ kind: "error", message: String(e) });
      }
    })();
  }, []);

  if (state.kind === "loading") {
    return <CenteredStatus title="Loading modpack…" />;
  }

  if (state.kind === "no-modpack") {
    return (
      <CenteredStatus
        title="No modpack found"
        body="JA2 Launcher looks for a modpack.json file next to the launcher executable, or in a folder you pass on the command line. Place JA2Launcher.exe inside an Arulco Stories (or similar) install folder."
      />
    );
  }

  if (state.kind === "error") {
    return <CenteredStatus title="Error" body={state.message} />;
  }

  const { folder, manifest, activeVfs } = state;
  const activeCampaign: Campaign | undefined = manifest.campaigns.find(
    (c) => c.vfs_config === activeVfs
  );

  const handleSelect = async (campaign: Campaign) => {
    setBusy(`Selecting ${campaign.display_name}…`);
    try {
      await setActiveCampaign(folder, campaign.vfs_config);
      setState({ ...state, activeVfs: campaign.vfs_config });
    } catch (e) {
      alert(`Couldn't switch campaign: ${e}`);
    } finally {
      setBusy(null);
    }
  };

  const handlePlay = async () => {
    setBusy("Launching JA2…");
    try {
      await launchGame(folder, manifest.modpack.engine_binary);
      setTimeout(() => setBusy(null), 3000);
    } catch (e) {
      alert(`Couldn't launch: ${e}`);
      setBusy(null);
    }
  };

  return (
    <div className="h-full flex flex-col">
      <header className="px-6 pt-4 pb-3">
        <h1 className="text-2xl font-semibold text-ja2-accent">
          {manifest.modpack.name}
        </h1>
        <p className="text-sm text-ja2-dim mt-1">
          v{manifest.modpack.version} · Active:{" "}
          <span className="text-ja2-text">
            {activeCampaign?.display_name ?? activeVfs ?? "(none)"}
          </span>
        </p>
      </header>

      <Tabs
        tabs={[
          { id: "campaigns", label: "Campaigns" },
          { id: "settings", label: "Settings" },
          { id: "presets", label: "Presets" },
          { id: "wizard", label: "Wizard" },
          { id: "diagnostic", label: "Diagnostic" },
        ]}
        activeId={tab}
        onChange={(id) => setTab(id as TabId)}
      />

      <main className="flex-1 overflow-y-auto px-6 py-4">
        {tab === "campaigns" && (
          <CampaignList
            campaigns={manifest.campaigns}
            activeVfsConfig={activeVfs}
            onSelect={handleSelect}
          />
        )}
        {tab === "settings" && (
          <SettingsTab folder={folder} onError={(m) => alert(m)} />
        )}
        {tab === "presets" && (
          <PresetsTab folder={folder} onError={(m) => alert(m)} />
        )}
        {tab === "wizard" && (
          <WizardTab folder={folder} onError={(m) => alert(m)} />
        )}
        {tab === "diagnostic" && (
          <DiagnosticTab
            folder={folder}
            activeSaveDir={activeCampaign?.save_dir ?? `Profiles/${activeCampaign?.id ?? "113"}`}
            activeCampaignDisplay={
              activeCampaign?.display_name ?? "(active campaign)"
            }
            onError={(m) => alert(m)}
          />
        )}
      </main>

      <footer className="px-6 py-3 border-t border-ja2-border flex items-center justify-between gap-4">
        <span className="text-xs text-ja2-dim">
          {tab === "campaigns" &&
            "Click a campaign to make it active, then press Play."}
          {tab === "settings" &&
            "Click a card's value, change it, then Apply. Edits go to Data-User (override layer) or Ja2.ini."}
          {tab === "presets" &&
            "Click a preset card to preview the changes, then Apply. Presets stack — use 'Default 1.13' to clear first."}
          {tab === "wizard" &&
            "Linear walkthrough: edits inside save immediately. Use Skip section to skim, Finish to exit early."}
          {tab === "diagnostic" &&
            "Logs are read-only; use Open log folder for raw access."}
        </span>
        <div className="flex items-center gap-3">
          {busy && <span className="text-sm text-ja2-dim">{busy}</span>}
          <button
            className="ja2-btn-primary px-8 py-3 text-lg"
            onClick={handlePlay}
            disabled={!!busy}
          >
            ▶ Play
          </button>
        </div>
      </footer>
    </div>
  );
}

function CenteredStatus({ title, body }: { title: string; body?: string }) {
  return (
    <div className="h-full flex items-center justify-center px-8">
      <div className="text-center max-w-md">
        <h1 className="text-xl text-ja2-accent mb-2">{title}</h1>
        {body && <p className="text-ja2-dim text-sm">{body}</p>}
      </div>
    </div>
  );
}
