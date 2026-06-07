import { useState } from "react";
import { applyPresetChanges, clearAllOverrides } from "../api/launcher";
import type { Preset } from "../types/modpack";
import { PRESETS } from "./presets";

interface Props {
  folder: string;
  onError: (msg: string) => void;
}

export function PresetsTab({ folder, onError }: Props) {
  const [busy, setBusy] = useState<string | null>(null);
  const [confirming, setConfirming] = useState<Preset | null>(null);
  const [lastResult, setLastResult] = useState<string | null>(null);

  const applyPreset = async (preset: Preset) => {
    setBusy(`Applying ${preset.name}…`);
    setLastResult(null);
    try {
      if (preset.is_reset) {
        const n = await clearAllOverrides(folder);
        setLastResult(`Cleared ${n} override file${n === 1 ? "" : "s"} from Data-User/.`);
      } else {
        const n = await applyPresetChanges(folder, preset.changes);
        setLastResult(
          `Applied ${n}/${preset.changes.length} change${preset.changes.length === 1 ? "" : "s"} from ${preset.name}.`
        );
      }
    } catch (e) {
      onError(String(e));
    } finally {
      setBusy(null);
      setConfirming(null);
    }
  };

  return (
    <div className="flex flex-col gap-4 max-w-4xl">
      <header>
        <p className="text-sm text-ja2-dim">
          Each preset is a curated bundle of settings that writes to{" "}
          <code>Data-User/*.ini</code> (the top VFS layer) or <code>Ja2.ini</code>{" "}
          directly. Presets are <strong>additive</strong> — applying one
          doesn't undo another. Use <strong>Default 1.13</strong> to clear all
          overrides before starting fresh. Edit individual keys later via the{" "}
          <strong>Settings</strong> tab.
        </p>
      </header>

      {busy && <p className="text-sm text-ja2-accent">{busy}</p>}
      {lastResult && !busy && (
        <p className="text-sm text-ja2-text border border-ja2-border bg-ja2-panel rounded px-3 py-2">
          ✓ {lastResult}
        </p>
      )}

      <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
        {PRESETS.map((preset) => (
          <PresetCard
            key={preset.id}
            preset={preset}
            onClick={() => setConfirming(preset)}
            disabled={!!busy}
          />
        ))}
      </div>

      {confirming && (
        <ConfirmPresetModal
          preset={confirming}
          onCancel={() => setConfirming(null)}
          onConfirm={() => applyPreset(confirming)}
          busy={!!busy}
        />
      )}
    </div>
  );
}

function PresetCard({
  preset,
  onClick,
  disabled,
}: {
  preset: Preset;
  onClick: () => void;
  disabled: boolean;
}) {
  return (
    <button
      onClick={onClick}
      disabled={disabled}
      className={[
        "text-left p-4 rounded border transition-colors",
        "border-ja2-border bg-ja2-panel hover:border-ja2-accent",
        "disabled:opacity-50 disabled:cursor-not-allowed",
        preset.is_reset && "border-ja2-danger hover:border-ja2-danger",
      ]
        .filter(Boolean)
        .join(" ")}
    >
      <div className="flex items-baseline justify-between gap-2">
        <h3 className="text-base font-semibold text-ja2-text">{preset.name}</h3>
        <span className="text-xs text-ja2-dim">
          {preset.is_reset
            ? "reset"
            : `${preset.changes.length} change${preset.changes.length === 1 ? "" : "s"}`}
        </span>
      </div>
      <p className="mt-2 text-sm text-ja2-dim leading-relaxed">
        {preset.description}
      </p>
      {preset.tags.length > 0 && (
        <div className="mt-3 flex flex-wrap gap-1.5">
          {preset.tags.map((t) => (
            <span
              key={t}
              className="px-1.5 py-0.5 rounded bg-ja2-bg text-xs text-ja2-dim border border-ja2-border"
            >
              {t}
            </span>
          ))}
        </div>
      )}
    </button>
  );
}

function ConfirmPresetModal({
  preset,
  onCancel,
  onConfirm,
  busy,
}: {
  preset: Preset;
  onCancel: () => void;
  onConfirm: () => void;
  busy: boolean;
}) {
  return (
    <div
      className="fixed inset-0 bg-black/60 flex items-center justify-center z-50 p-4"
      onClick={onCancel}
    >
      <div
        className="bg-ja2-panel border border-ja2-border rounded p-5 max-w-2xl w-full max-h-[80vh] overflow-y-auto"
        onClick={(e) => e.stopPropagation()}
      >
        <h2 className="text-lg font-semibold text-ja2-accent">{preset.name}</h2>
        <p className="text-sm text-ja2-dim mt-2">{preset.description}</p>

        {preset.is_reset ? (
          <div className="mt-4 p-3 border border-ja2-danger rounded text-sm text-ja2-text">
            <strong>This deletes every file in Data-User/.</strong> All overrides
            you've set via the Settings tab or any prior preset will be removed.
            Your campaign saves (Profiles/&lt;campaign&gt;/) are NOT affected.
          </div>
        ) : (
          <div className="mt-4">
            <h3 className="text-sm font-semibold text-ja2-text mb-2">
              {preset.changes.length} change{preset.changes.length === 1 ? "" : "s"} will be applied:
            </h3>
            <table className="w-full text-xs">
              <thead className="text-ja2-dim">
                <tr>
                  <th className="text-left pb-1">File</th>
                  <th className="text-left pb-1">Section</th>
                  <th className="text-left pb-1">Key</th>
                  <th className="text-left pb-1">→ Value</th>
                </tr>
              </thead>
              <tbody>
                {preset.changes.map((c, i) => (
                  <tr key={i} className="border-t border-ja2-border">
                    <td className="py-1 text-ja2-dim font-mono">
                      {c.target === "ja2_ini" ? "Ja2.ini" : `Data-User/${c.ini_file}`}
                    </td>
                    <td className="py-1 text-ja2-dim">{c.section}</td>
                    <td className="py-1 text-ja2-text font-mono">{c.key}</td>
                    <td className="py-1 text-ja2-accent font-mono">{c.value}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}

        <div className="mt-5 flex gap-2 justify-end">
          <button
            className="ja2-btn"
            onClick={onCancel}
            disabled={busy}
          >
            Cancel
          </button>
          <button
            className="ja2-btn-primary"
            onClick={onConfirm}
            disabled={busy}
          >
            {preset.is_reset ? "Clear all overrides" : `Apply ${preset.name}`}
          </button>
        </div>
      </div>
    </div>
  );
}
