import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";

import { listBackgrounds, type BackgroundModifier } from "../lib/api";

interface Props {
  value: number;
  onChange: (id: number) => void;
}

// "ap_forest" -> "AP forest", "resistance_fear" -> "Resist fear",
// "travel_foot" -> "Travel foot", "explosives" -> "Explosives".
function prettyKey(key: string): string {
  if (key.startsWith("ap_")) return "AP " + key.slice(3);
  if (key.startsWith("resistance_")) return "Resist " + key.slice(11);
  if (key.startsWith("travel_")) return "Travel " + key.slice(7);
  return key.charAt(0).toUpperCase() + key.slice(1);
}

function formatModifier(m: BackgroundModifier): string {
  return `${prettyKey(m.key)} ${m.value > 0 ? "+" : ""}${m.value}`;
}

/** Background dropdown sourced from the active install's Backgrounds.xml.
 *  Falls back to "no backgrounds in this install" if the file is missing.
 *
 *  "No background" is value 0 (the engine default; uiIndex 0 in Backgrounds.xml
 *  is the zero-modifier template row, surfaced here as "None" rather than
 *  listed). Every other id is a real background — including 255 ("Explosives
 *  Technician", Barry's), which an older build wrongly hardcoded as "None"
 *  because 255 used to be the no-background sentinel back when usBackground
 *  was a UINT8. The selected background's description + stat/AP bonuses are
 *  shown below the dropdown. */
export default function BackgroundPicker({ value, onChange }: Props) {
  const bgs = useQuery({
    queryKey: ["backgrounds"],
    queryFn: () => listBackgrounds(),
    staleTime: 5 * 60 * 1000,
  });

  if (bgs.isLoading) {
    return (
      <label className="block">
        <span className="text-sm font-medium text-wasteland-100">Background</span>
        <div className="text-xs text-wasteland-500 mt-1">Loading backgrounds…</div>
      </label>
    );
  }
  if (bgs.isError || !bgs.data) {
    return (
      <label className="block">
        <span className="text-sm font-medium text-wasteland-100">Background</span>
        <p className="text-xs text-rust-400 mt-1">
          Couldn't load backgrounds for this install.
        </p>
      </label>
    );
  }

  const items = bgs.data.backgrounds;
  if (!bgs.data.file_present || items.length === 0) {
    return (
      <label className="block">
        <span className="text-sm font-medium text-wasteland-100">Background</span>
        <p className="text-xs text-wasteland-500 mt-1">
          This install has no background list, so this merc will start with no background.
        </p>
      </label>
    );
  }

  const selected = items.find((b) => b.id === value);
  // Real, selectable backgrounds. Skip the conventional uiIndex-0 template
  // ("Background name (128 letters)") — it's surfaced as "None" instead.
  const real = items.filter((b) => b.id !== 0);
  const mods = selected?.modifiers ?? [];

  return (
    <label className="block">
      <span className="flex items-center justify-between">
        <span className="text-sm font-medium text-wasteland-100">Background</span>
        <Link
          to="/backgrounds"
          className="text-xs text-wasteland-400 hover:text-rust-400 font-normal"
        >
          Manage…
        </Link>
      </span>
      <select
        className="input mt-1"
        value={value}
        onChange={(e) => onChange(Number(e.target.value))}
      >
        <option value={0}>None (no background)</option>
        {real.map((bg) => (
          <option key={bg.id} value={bg.id}>
            {bg.short_name || bg.name || `#${bg.id}`} (id {bg.id})
          </option>
        ))}
        {/* Keep an out-of-catalog value visible so it round-trips losslessly */}
        {value !== 0 && !selected && (
          <option value={value}>(unknown background — id {value})</option>
        )}
      </select>
      {selected && (selected.description || mods.length > 0) && (
        <div className="mt-1 space-y-0.5">
          {selected.description && (
            <p className="text-xs text-wasteland-400">{selected.description}</p>
          )}
          {mods.length > 0 && (
            <p className="text-xs font-mono text-wasteland-300">
              {mods.map(formatModifier).join("  ·  ")}
            </p>
          )}
        </div>
      )}
      {!selected && value !== 0 && (
        <p className="text-xs text-yellow-400/90 mt-1">
          ⚠ usBackground={value} doesn't match any entry in this install's Backgrounds.xml.
        </p>
      )}
    </label>
  );
}
