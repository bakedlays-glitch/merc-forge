import type { Merc } from "../../lib/schema";

/**
 * Growth modifiers — per-stat adjustments to how fast a merc improves
 * each stat through use. Negative = faster growth; positive = slower.
 * (The engine adds this value to the work a stat needs to level up, so a
 * negative number lowers that bar and a positive number raises it.)
 * Ignored entirely for a merc whose stats decline with age (fRegresses),
 * and only active when the install enables merc growth modifiers.
 *
 * Most vanilla mercs leave these at 0. The slider covers -100..+100;
 * larger values from other tools are kept as-is on round-trip.
 *
 * Hidden behind a collapsed `<details>` section because most users
 * never touch these. Expanding shows the full grid of all 11 growth
 * modifiers; collapsed it's a single header line that says how many
 * are non-zero.
 */

export interface GrowthModifiersFormProps {
  merc: Merc;
  onChange: <K extends keyof Merc>(field: K, value: Merc[K]) => void;
}

const GROWTH_FIELDS: ReadonlyArray<readonly [keyof Merc, string]> = [
  ["GrowthModifierLife", "Life Max"],
  ["GrowthModifierStrength", "Strength"],
  ["GrowthModifierAgility", "Agility"],
  ["GrowthModifierDexterity", "Dexterity"],
  ["GrowthModifierWisdom", "Wisdom"],
  ["GrowthModifierMarksmanship", "Marksmanship"],
  ["GrowthModifierExplosive", "Explosives"],
  ["GrowthModifierLeadership", "Leadership"],
  ["GrowthModifierMedical", "Medical"],
  ["GrowthModifierMechanical", "Mechanical"],
  ["GrowthModifierExpLevel", "Exp Level"],
];

export default function GrowthModifiersForm({
  merc, onChange,
}: GrowthModifiersFormProps) {
  const nonZeroCount = GROWTH_FIELDS.filter(
    ([k]) => (merc[k] as number) !== 0,
  ).length;
  return (
    <details className="block border border-wasteland-700 rounded p-3 open:bg-wasteland-900/40">
      <summary className="cursor-pointer text-sm font-medium text-wasteland-100 select-none">
        Growth Modifiers
        <span className="ml-2 text-xs text-wasteland-500">
          {nonZeroCount === 0
            ? "(all zero — default)"
            : `(${nonZeroCount} non-zero)`}
        </span>
      </summary>
      <div className="grid grid-cols-2 sm:grid-cols-3 gap-3 mt-3">
        {GROWTH_FIELDS.map(([key, label]) => (
          <label key={key} className="block">
            <span className="text-xs text-wasteland-300">{label}</span>
            <div className="flex items-center gap-2 mt-1">
              <input
                type="range"
                min={-100}
                max={100}
                value={(merc[key] as number) ?? 0}
                onChange={(e) =>
                  onChange(key as keyof Merc, Number(e.target.value) as Merc[typeof key])
                }
                className="flex-1 accent-rust-500"
              />
              <input
                type="number"
                min={-100}
                max={100}
                value={(merc[key] as number) ?? 0}
                onChange={(e) =>
                  onChange(key as keyof Merc, Number(e.target.value) as Merc[typeof key])
                }
                className="w-16 rounded border border-wasteland-700 bg-wasteland-900 px-1.5 py-0.5 text-xs font-mono text-wasteland-100"
              />
            </div>
          </label>
        ))}
      </div>
      <p className="text-xs text-wasteland-400 mt-2">
        Per-stat tweak to how fast this merc improves that stat through use.
        0 leaves the normal rate unchanged. Negative speeds growth up;
        positive slows it down. Most stock mercs are all zero, and speeding
        a stat up has a built-in limit (it can roughly double the rate at
        most). These only take effect if the install has merc growth
        modifiers turned on, and they're ignored for mercs whose stats
        decline with age. The slider covers -100 to +100; larger values
        from other tools are kept as-is.
      </p>
    </details>
  );
}
