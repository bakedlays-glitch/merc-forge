import { useEffect, useState } from "react";
import { listSchemas, loadSchema } from "../api/launcher";
import type { SchemaAvailability, SchemaDoc } from "../types/modpack";
import { PropertyCard } from "./PropertyCard";

interface Props {
  folder: string;
  onError: (msg: string) => void;
}

// Same INI labels as SettingsTab; duplicated to avoid coupling. If we add more
// INIs, update both lists OR pull the labels into a shared constant file.
const WIZARD_INI_LABELS: Array<{ ini: string; label: string }> = [
  { ini: "Ja2.ini", label: "Install settings" },
  { ini: "Ja2_Options.ini", label: "Game options" },
  { ini: "APBPConstants.ini", label: "AP / BP costs" },
  { ini: "Skills_Settings.INI", label: "Skills + traits" },
  { ini: "Mod_Settings.ini", label: "Mod feature toggles" },
  { ini: "RebelCommand_Settings.ini", label: "Rebel Command" },
  { ini: "Item_Settings.ini", label: "Items + repair" },
  { ini: "Morale_Settings.INI", label: "Morale events" },
  { ini: "CTHConstants.ini", label: "Combat math" },
  { ini: "Taunts_Settings.INI", label: "Battle taunts" },
  { ini: "Helicopter_Settings.INI", label: "Skyrider" },
  { ini: "Reputation_Settings.INI", label: "Town reputation" },
  { ini: "AI.ini", label: "AI behavior" },
  { ini: "IntroVideos.ini", label: "Intro videos" },
  { ini: "Creatures_Settings.INI", label: "Creatures" },
];

type WizardState =
  | { kind: "idle" }                                                              // pre-start landing page
  | { kind: "loading" }
  | { kind: "error"; message: string }
  | { kind: "running"; schema: SchemaDoc; sectionIndex: number; iniFile: string }
  | { kind: "done"; iniFile: string; sectionsVisited: number };

export function WizardTab({ folder, onError }: Props) {
  const [state, setState] = useState<WizardState>({ kind: "idle" });
  const [iniFile, setIniFile] = useState("Ja2.ini");

  // Just so the idle landing page can show "schema present in modpack" badges
  const [availability, setAvailability] = useState<SchemaAvailability[] | null>(null);

  useEffect(() => {
    (async () => {
      try {
        setAvailability(await listSchemas(folder));
      } catch (e) {
        onError(String(e));
      }
    })();
  }, [folder, onError]);

  const startWizard = async () => {
    setState({ kind: "loading" });
    try {
      const schema = await loadSchema(folder, iniFile);
      if (schema.sections.length === 0) {
        setState({ kind: "error", message: `${iniFile} has no sections.` });
        return;
      }
      setState({ kind: "running", schema, sectionIndex: 0, iniFile });
    } catch (e) {
      setState({ kind: "error", message: String(e) });
    }
  };

  if (state.kind === "loading") {
    return <p className="p-4 text-ja2-dim">Loading schema…</p>;
  }

  if (state.kind === "error") {
    return (
      <div className="p-4">
        <p className="text-ja2-danger text-sm">{state.message}</p>
        <button
          className="ja2-btn mt-3"
          onClick={() => setState({ kind: "idle" })}
        >
          ← Back
        </button>
      </div>
    );
  }

  if (state.kind === "done") {
    return (
      <DonePanel
        iniFile={state.iniFile}
        sectionsVisited={state.sectionsVisited}
        onRestart={() => setState({ kind: "idle" })}
      />
    );
  }

  if (state.kind === "idle") {
    return (
      <IdlePanel
        availableInis={WIZARD_INI_LABELS}
        availability={availability}
        selectedIni={iniFile}
        onSelect={setIniFile}
        onStart={startWizard}
      />
    );
  }

  // Running
  const { schema, sectionIndex } = state;
  const section = schema.sections[sectionIndex];
  const progress = ((sectionIndex + 1) / schema.sections.length) * 100;
  const isLast = sectionIndex >= schema.sections.length - 1;
  const isFirst = sectionIndex === 0;

  const goNext = () => {
    if (isLast) {
      setState({
        kind: "done",
        iniFile: state.iniFile,
        sectionsVisited: schema.sections.length,
      });
    } else {
      setState({ ...state, sectionIndex: sectionIndex + 1 });
    }
  };

  const goPrev = () => {
    if (!isFirst) setState({ ...state, sectionIndex: sectionIndex - 1 });
  };

  const finish = () => {
    setState({
      kind: "done",
      iniFile: state.iniFile,
      sectionsVisited: sectionIndex + 1,
    });
  };

  return (
    <div className="flex flex-col h-full max-w-4xl">
      {/* Top bar: ini + progress */}
      <header className="flex-shrink-0">
        <div className="flex items-baseline justify-between gap-3 mb-2">
          <h2 className="text-base font-semibold text-ja2-accent">
            {WIZARD_INI_LABELS.find((l) => l.ini === state.iniFile)?.label ?? state.iniFile}
          </h2>
          <span className="text-xs text-ja2-dim">
            Section {sectionIndex + 1} of {schema.sections.length}
          </span>
        </div>
        <div className="h-1 bg-ja2-bg rounded overflow-hidden border border-ja2-border">
          <div
            className="h-full bg-ja2-accent transition-all"
            style={{ width: `${progress}%` }}
          />
        </div>
      </header>

      {/* Section body */}
      <div className="flex-1 overflow-y-auto py-4">
        <h3 className="text-lg font-semibold text-ja2-text">{section.name}</h3>
        {section.description && (
          <p className="text-xs text-ja2-dim mt-1 whitespace-pre-line">
            {section.description}
          </p>
        )}
        <p className="text-xs text-ja2-dim mt-1 mb-4">
          {section.properties.length} settings
        </p>

        {section.properties.length === 0 ? (
          <p className="text-sm text-ja2-dim italic">
            This section has no editable settings — click Next to continue.
          </p>
        ) : (
          <div className="flex flex-col gap-3">
            {section.properties.map((p) => (
              <PropertyCard
                key={p.name}
                section={section.name}
                prop={p}
                folder={folder}
                iniFile={state.iniFile}
                onError={onError}
              />
            ))}
          </div>
        )}
      </div>

      {/* Nav controls */}
      <footer className="flex-shrink-0 flex items-center justify-between gap-3 pt-3 border-t border-ja2-border">
        <button className="ja2-btn text-sm" onClick={finish}>
          Finish wizard
        </button>
        <div className="flex gap-2">
          <button
            className="ja2-btn text-sm"
            onClick={goPrev}
            disabled={isFirst}
          >
            ← Previous
          </button>
          <button className="ja2-btn text-sm" onClick={goNext}>
            Skip section
          </button>
          <button className="ja2-btn-primary text-sm px-5" onClick={goNext}>
            {isLast ? "Finish →" : "Next →"}
          </button>
        </div>
      </footer>
    </div>
  );
}

function IdlePanel({
  availableInis,
  availability,
  selectedIni,
  onSelect,
  onStart,
}: {
  availableInis: Array<{ ini: string; label: string }>;
  availability: SchemaAvailability[] | null;
  selectedIni: string;
  onSelect: (ini: string) => void;
  onStart: () => void;
}) {
  const sel = availability?.find((a) => a.ini_file === selectedIni);
  return (
    <div className="max-w-2xl flex flex-col gap-4">
      <header>
        <h2 className="text-lg font-semibold text-ja2-accent">Setup wizard</h2>
        <p className="text-sm text-ja2-dim mt-1">
          Walks you through every section of the selected INI file, one page at
          a time, with descriptions and form widgets for each setting. Edits
          save immediately when you click Apply on a card. Click <strong>Next</strong>{" "}
          to advance, <strong>Skip section</strong> to skip without editing,{" "}
          <strong>Finish</strong> to exit at any time.
        </p>
      </header>

      <div className="ja2-panel">
        <label className="text-sm text-ja2-dim block mb-2">
          Which INI to walk through?
        </label>
        <select
          value={selectedIni}
          onChange={(e) => onSelect(e.target.value)}
          className="bg-ja2-bg border border-ja2-border text-ja2-text rounded px-2 py-1.5 text-sm w-full
                     focus:outline-none focus:border-ja2-accent"
        >
          {availableInis.map((m) => {
            const a = availability?.find((x) => x.ini_file === m.ini);
            const tag = a?.in_modpack ? "modpack" : a?.embedded_available ? "embedded" : "missing";
            return (
              <option key={m.ini} value={m.ini}>
                {m.label} ({m.ini}) · {tag}
              </option>
            );
          })}
        </select>
        {sel && (
          <p className="text-xs text-ja2-dim mt-2">
            Schema source:{" "}
            <span className={sel.in_modpack ? "text-ja2-accent" : ""}>
              {sel.in_modpack ? "modpack folder" : "embedded fallback"}
            </span>
            . Files without a schema in the dropdown above will fall back to
            the embedded copy bundled in JA2Launcher.exe.
          </p>
        )}
      </div>

      <p className="text-xs text-ja2-dim">
        Recommended starting point for a new player: <strong>Install settings</strong>{" "}
        (Ja2.ini, only 1 section, sets resolution + intro + tooltip scale).
        Then <strong>Game options</strong> (Ja2_Options.ini, 53 sections — the
        full game balance tree).
      </p>

      <div>
        <button
          className="ja2-btn-primary text-base px-6 py-2"
          onClick={onStart}
        >
          Start wizard
        </button>
      </div>
    </div>
  );
}

function DonePanel({
  iniFile,
  sectionsVisited,
  onRestart,
}: {
  iniFile: string;
  sectionsVisited: number;
  onRestart: () => void;
}) {
  return (
    <div className="max-w-xl flex flex-col gap-4">
      <h2 className="text-xl font-semibold text-ja2-accent">Wizard complete</h2>
      <p className="text-sm text-ja2-text">
        Visited {sectionsVisited} section{sectionsVisited === 1 ? "" : "s"} of{" "}
        <code>{iniFile}</code>. Any changes you applied are saved to{" "}
        {iniFile === "Ja2.ini" ? "Ja2.ini" : `Data-User/${iniFile}`} and take
        effect on next launch.
      </p>
      <p className="text-sm text-ja2-dim">
        Want to walk another INI file, or revisit settings later? You can
        re-run the wizard, jump directly into any setting via the{" "}
        <strong>Settings</strong> tab, or use a one-click bundle from the{" "}
        <strong>Presets</strong> tab.
      </p>
      <div>
        <button className="ja2-btn-primary text-sm px-5 py-2" onClick={onRestart}>
          Start another wizard
        </button>
      </div>
    </div>
  );
}
