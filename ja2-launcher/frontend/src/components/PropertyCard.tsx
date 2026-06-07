// Single property editor card. Used by both SettingsBrowser (free browsing)
// and WizardTab (linear walkthrough). Pure-React props + callbacks so it
// also ports cleanly into MercForge when that integration happens.

import { useEffect, useState } from "react";
import {
  readEffectiveSetting,
  writeUserOption,
  deleteUserOption,
  setJa2iniKey,
} from "../api/launcher";
import type { SchemaProperty } from "../types/modpack";

interface PropertyCardProps {
  section: string;
  prop: SchemaProperty;
  folder: string;
  iniFile: string;
  onError: (msg: string) => void;
  /// Show the section name above the property name (used in search results).
  showSectionContext?: boolean;
}

export function PropertyCard({
  section,
  prop,
  folder,
  iniFile,
  onError,
  showSectionContext,
}: PropertyCardProps) {
  const [effective, setEffective] = useState<{
    value: string | null;
    source: string;
  } | null>(null);
  const [editing, setEditing] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [descExpanded, setDescExpanded] = useState(false);

  const loadEffective = async () => {
    try {
      const eff = await readEffectiveSetting(folder, iniFile, section, prop.name);
      setEffective(eff);
    } catch (e) {
      onError(String(e));
    }
  };

  useEffect(() => {
    void loadEffective();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [section, prop.name, iniFile, folder]);

  // Ja2.ini values live at the install root (no override layer); other INIs
  // use the Data-User override pattern. This drives where we write.
  const isJa2Ini = iniFile === "Ja2.ini";
  const isOverridden = effective?.source === "data_user";
  const canReset = isOverridden; // Ja2.ini has no "remove override" concept
  const displayValue =
    editing != null ? editing : effective?.value ?? prop.default_value ?? "";

  const apply = async (nextValue: string) => {
    setBusy(true);
    try {
      if (isJa2Ini) {
        await setJa2iniKey(folder, prop.name, nextValue);
      } else {
        await writeUserOption(folder, iniFile, section, prop.name, nextValue);
      }
      await loadEffective();
      setEditing(null);
    } catch (e) {
      onError(String(e));
    } finally {
      setBusy(false);
    }
  };

  const reset = async () => {
    setBusy(true);
    try {
      await deleteUserOption(folder, iniFile, section, prop.name);
      await loadEffective();
      setEditing(null);
    } catch (e) {
      onError(String(e));
    } finally {
      setBusy(false);
    }
  };

  // Truncate long descriptions; click-to-expand
  const shortDesc =
    prop.description.length > 220 && !descExpanded
      ? prop.description.slice(0, 220).trim() + "…"
      : prop.description;

  return (
    <div className="border border-ja2-border rounded p-3 bg-ja2-panel">
      <div className="flex items-baseline justify-between gap-2">
        <div className="flex-1 min-w-0">
          {showSectionContext && (
            <div className="text-xs text-ja2-dim mb-0.5">{section}</div>
          )}
          <div className="text-sm font-mono text-ja2-text truncate">{prop.name}</div>
        </div>
        <div className="flex items-center gap-2 text-xs">
          <span className="text-ja2-dim">{prop.datatype}</span>
          {isOverridden && (
            <span className="px-1.5 py-0.5 rounded bg-ja2-accent text-ja2-bg font-medium">
              OVERRIDE
            </span>
          )}
        </div>
      </div>

      {prop.description && (
        <p
          className="mt-2 text-xs text-ja2-dim whitespace-pre-line cursor-pointer"
          onClick={() =>
            prop.description.length > 220 && setDescExpanded((v) => !v)
          }
        >
          {shortDesc}
          {prop.description.length > 220 && (
            <span className="ml-1 text-ja2-accent">
              {descExpanded ? "(less)" : "(more)"}
            </span>
          )}
        </p>
      )}

      <div className="mt-3 flex items-center gap-3 flex-wrap">
        <PropertyEditor
          prop={prop}
          value={displayValue}
          disabled={busy}
          onChange={setEditing}
        />
        {editing != null &&
          editing !==
            (effective?.value ?? prop.default_value ?? "") && (
            <>
              <button
                className="ja2-btn-primary text-xs px-2 py-1"
                onClick={() => void apply(editing)}
                disabled={busy}
              >
                Apply
              </button>
              <button
                className="ja2-btn text-xs px-2 py-1"
                onClick={() => setEditing(null)}
                disabled={busy}
              >
                Cancel
              </button>
            </>
          )}
        {canReset && editing == null && (
          <button
            className="ja2-btn text-xs px-2 py-1"
            onClick={() => void reset()}
            disabled={busy}
          >
            Reset to default
          </button>
        )}
        <span className="text-xs text-ja2-dim ml-auto">
          {effective?.source === "data_user" && "From Data-User (override)"}
          {effective?.source === "data_113" && "From Data-1.13 (modpack default)"}
          {effective?.source === "ja2_ini" && "From Ja2.ini"}
          {effective?.source === "none" &&
            prop.default_value != null &&
            "Schema default"}
          {effective?.source === "none" && prop.default_value == null && "Unset"}
        </span>
      </div>

      {(prop.min_value != null ||
        prop.max_value != null ||
        prop.default_value != null ||
        prop.vanilla_value != null) && (
        <div className="mt-1 text-xs text-ja2-dim flex gap-3 flex-wrap">
          {prop.default_value != null && <span>default {prop.default_value}</span>}
          {prop.min_value != null && <span>min {prop.min_value}</span>}
          {prop.max_value != null && <span>max {prop.max_value}</span>}
          {prop.interval != null && prop.interval !== "1" && (
            <span>step {prop.interval}</span>
          )}
          {prop.vanilla_value != null && (
            <span className="text-ja2-accent">
              Vanilla JA2: {prop.vanilla_value}
            </span>
          )}
        </div>
      )}
    </div>
  );
}

interface PropertyEditorProps {
  prop: SchemaProperty;
  value: string;
  disabled: boolean;
  onChange: (v: string) => void;
}

export function PropertyEditor({
  prop,
  value,
  disabled,
  onChange,
}: PropertyEditorProps) {
  const dt = prop.datatype.toLowerCase();

  if (dt === "boolean") {
    const isTrue = value.toUpperCase() === "TRUE" || value === "1";
    return (
      <label className="flex items-center gap-2 text-sm cursor-pointer">
        <input
          type="checkbox"
          checked={isTrue}
          disabled={disabled}
          onChange={(e) => onChange(e.target.checked ? "TRUE" : "FALSE")}
          className="w-4 h-4 accent-ja2-accent"
        />
        <span className="text-ja2-text">{isTrue ? "TRUE" : "FALSE"}</span>
      </label>
    );
  }

  if (dt === "list" && prop.list_values.length > 0) {
    return (
      <select
        value={value}
        disabled={disabled}
        onChange={(e) => onChange(e.target.value)}
        className="bg-ja2-bg border border-ja2-border text-ja2-text rounded px-2 py-1 text-sm
                   focus:outline-none focus:border-ja2-accent"
      >
        {prop.list_values.map((v) => (
          <option key={v} value={v}>
            {v}
          </option>
        ))}
      </select>
    );
  }

  if (dt === "numeric") {
    return (
      <input
        type="number"
        value={value}
        disabled={disabled}
        min={prop.min_value ?? undefined}
        max={prop.max_value ?? undefined}
        step={prop.interval ?? "1"}
        onChange={(e) => onChange(e.target.value)}
        className="bg-ja2-bg border border-ja2-border text-ja2-text rounded px-2 py-1 text-sm w-32
                   focus:outline-none focus:border-ja2-accent"
      />
    );
  }

  // string / array / unknown: free text
  return (
    <input
      type="text"
      value={value}
      disabled={disabled}
      onChange={(e) => onChange(e.target.value)}
      className="bg-ja2-bg border border-ja2-border text-ja2-text rounded px-2 py-1 text-sm flex-1 min-w-[12rem]
                 focus:outline-none focus:border-ja2-accent"
    />
  );
}
