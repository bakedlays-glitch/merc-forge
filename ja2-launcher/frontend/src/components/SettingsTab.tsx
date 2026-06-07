import { useEffect, useState } from "react";
import { listSchemas } from "../api/launcher";
import type { SchemaAvailability } from "../types/modpack";
import { SettingsBrowser } from "./SettingsBrowser";

interface Props {
  folder: string;
  onError: (msg: string) => void;
}

// Display labels for every INI file we have a schema for.
// Order = recommended visit order; most-edited first, niche last.
// Source: "official" = JA2 1.13's hand-curated INIEditor*.xml; "auto" = extracted from INI comments.
const INI_FILE_LABELS: Array<{
  ini: string;
  label: string;
  blurb: string;
  source: "official" | "auto";
}> = [
  // Official (rich descriptions, ranges, datatypes)
  {
    ini: "Ja2_Options.ini",
    label: "Game options",
    blurb: "The big one: combat math, AI, economy, items, vehicles, mini-events, hardcore mode. 813 keys.",
    source: "official",
  },
  {
    ini: "APBPConstants.ini",
    label: "AP / BP costs",
    blurb: "Action-point and breath-point costs for every weapon class and action.",
    source: "official",
  },
  {
    ini: "Ja2.ini",
    label: "Install settings",
    blurb: "Top-level: display resolution, fullscreen, intro, tooltip scale, active campaign.",
    source: "official",
  },
  // Auto-extracted (descriptions from INI ; comments)
  {
    ini: "Skills_Settings.INI",
    label: "Skills + traits",
    blurb: "XP gain rates, stat caps, trait effects, IMP setup. 257 keys.",
    source: "auto",
  },
  {
    ini: "Mod_Settings.ini",
    label: "Mod feature toggles",
    blurb: "Per-mod feature flags: enable/disable overheating, ARS, suppression, NCTH, etc. 183 keys.",
    source: "auto",
  },
  {
    ini: "RebelCommand_Settings.ini",
    label: "Rebel Command",
    blurb: "Strategic militia/rebel network behavior. 171 keys.",
    source: "auto",
  },
  {
    ini: "Item_Settings.ini",
    label: "Items + repair",
    blurb: "Item behavior: repair, attachments, weapon mods, ammo. 159 keys.",
    source: "auto",
  },
  {
    ini: "Morale_Settings.INI",
    label: "Morale events",
    blurb: "Morale gain/loss values for combat events, town liberation, mercenary deaths, etc. 120 keys.",
    source: "auto",
  },
  {
    ini: "CTHConstants.ini",
    label: "Combat math (CTH)",
    blurb: "Chance-to-hit constants per weapon class. Use with care — affects all combat. 113 keys.",
    source: "auto",
  },
  {
    ini: "Taunts_Settings.INI",
    label: "Battle taunts",
    blurb: "Frequency and types of taunts during combat. 42 keys.",
    source: "auto",
  },
  {
    ini: "Helicopter_Settings.INI",
    label: "Skyrider helicopter",
    blurb: "Helicopter repair, refuel, SAM accuracy, passenger damage. 23 keys.",
    source: "auto",
  },
  {
    ini: "Reputation_Settings.INI",
    label: "Town reputation",
    blurb: "How town loyalty changes from your actions. 18 keys.",
    source: "auto",
  },
  {
    ini: "AI.ini",
    label: "AI behavior",
    blurb: "Strategic + tactical AI tunables. Small file, big impact. 12 keys.",
    source: "auto",
  },
  {
    ini: "IntroVideos.ini",
    label: "Intro videos",
    blurb: "Which Sir-Tech intro videos play. 12 keys.",
    source: "auto",
  },
  {
    ini: "Creatures_Settings.INI",
    label: "Creatures (bugs)",
    blurb: "Creature spawning + hive behavior. Tiny file. 3 keys.",
    source: "auto",
  },
];

export function SettingsTab({ folder, onError }: Props) {
  const [schemas, setSchemas] = useState<SchemaAvailability[] | null>(null);
  const [iniFile, setIniFile] = useState("Ja2_Options.ini");

  useEffect(() => {
    (async () => {
      try {
        setSchemas(await listSchemas(folder));
      } catch (e) {
        onError(String(e));
      }
    })();
  }, [folder, onError]);

  const currentMeta = INI_FILE_LABELS.find((m) => m.ini === iniFile);
  const currentAvail = schemas?.find((s) => s.ini_file === iniFile);

  return (
    <div className="flex flex-col gap-3 h-full">
      <header className="flex items-baseline gap-4 flex-wrap">
        <div className="flex items-center gap-2">
          <label className="text-sm text-ja2-dim">Editing:</label>
          <select
            value={iniFile}
            onChange={(e) => setIniFile(e.target.value)}
            className="bg-ja2-panel border border-ja2-border text-ja2-text rounded px-2 py-1 text-sm
                       focus:outline-none focus:border-ja2-accent"
          >
            {INI_FILE_LABELS.map((m) => (
              <option key={m.ini} value={m.ini}>
                {m.label} ({m.ini})
              </option>
            ))}
          </select>
          {currentAvail && (
            <span className="text-xs text-ja2-dim">
              schema from{" "}
              <span
                className={
                  currentAvail.in_modpack ? "text-ja2-accent" : "text-ja2-dim"
                }
              >
                {currentAvail.in_modpack ? "modpack" : "embedded"}
              </span>
            </span>
          )}
        </div>
        {currentMeta && (
          <p className="text-xs text-ja2-dim flex-1 min-w-0">{currentMeta.blurb}</p>
        )}
      </header>

      <div className="flex-1 min-h-0">
        <SettingsBrowser
          key={iniFile}
          folder={folder}
          iniFile={iniFile}
          onError={onError}
        />
      </div>
    </div>
  );
}
