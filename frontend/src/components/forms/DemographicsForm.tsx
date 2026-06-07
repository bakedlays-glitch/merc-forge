import type { Merc } from "../../lib/schema";
import { NATIONALITY_OPTIONS } from "../../lib/nationalities";
import { RACE_OPTIONS } from "../../lib/races";

/**
 * Race, nationality, body type. Engine-side these drive idle audio
 * pools, AI dialogue triggers (bHatedNationality), and which animation
 * pack is loaded for the tactical sprite.
 *
 * Body type values come from JA2 1.13's SoldierBodyTypes enum
 * (TacticalAnimation/AnimationData.h). REGMALE / BIGMALE / STOCKYMALE /
 * REGFEMALE are the four humanoid body types we surface; the rest are
 * non-humanoid (creatures, robots) and don't make sense for merc
 * profiles. Mods occasionally extend with custom IDs; users who need
 * them can punch into the raw value via the override field.
 *
 * Race + nationality options come from the canonical engine-derived tables
 * in ../../lib/races and ../../lib/nationalities (verbatim mirrors of the
 * engine's szRaceText[] / szNationalityText[]). Body type stays local — we
 * only surface the four humanoid types.
 */

export interface DemographicsFormProps {
  merc: Merc;
  onChange: <K extends keyof Merc>(field: K, value: Merc[K]) => void;
}

const BODY_TYPE_OPTIONS: ReadonlyArray<readonly [number, string]> = [
  [0, "REGMALE — average male"],
  [1, "BIGMALE — large male"],
  [2, "STOCKYMALE — broad/stocky male"],
  [3, "REGFEMALE — average female"],
];

export default function DemographicsForm({
  merc, onChange,
}: DemographicsFormProps) {
  return (
    <fieldset className="block border border-wasteland-700 rounded p-3">
      <legend className="text-sm font-medium text-wasteland-100 px-1">
        Demographics
      </legend>
      <div className="grid grid-cols-3 gap-3 mt-2">
        <label className="block">
          <span className="text-xs text-wasteland-300">Race</span>
          <select
            className="input mt-1"
            value={merc.bRace}
            onChange={(e) => onChange("bRace", Number(e.target.value))}
          >
            {RACE_OPTIONS.map(([v, l]) => (
              <option key={v} value={v}>{l}</option>
            ))}
            {/* Preserve mod-extended values by showing them too */}
            {!RACE_OPTIONS.some(([v]) => v === merc.bRace) && (
              <option value={merc.bRace}>(custom: {merc.bRace})</option>
            )}
          </select>
        </label>
        <label className="block">
          <span className="text-xs text-wasteland-300">Nationality</span>
          <select
            className="input mt-1"
            value={merc.bNationality}
            onChange={(e) => onChange("bNationality", Number(e.target.value))}
          >
            {NATIONALITY_OPTIONS.map(([v, l]) => (
              <option key={v} value={v}>{l}</option>
            ))}
            {!NATIONALITY_OPTIONS.some(([v]) => v === merc.bNationality) && (
              <option value={merc.bNationality}>
                (custom: {merc.bNationality})
              </option>
            )}
          </select>
        </label>
        <label className="block">
          <span className="text-xs text-wasteland-300">Body type</span>
          <select
            className="input mt-1"
            value={merc.ubBodyType}
            onChange={(e) => onChange("ubBodyType", Number(e.target.value))}
          >
            {BODY_TYPE_OPTIONS.map(([v, l]) => (
              <option key={v} value={v}>{l}</option>
            ))}
            {!BODY_TYPE_OPTIONS.some(([v]) => v === merc.ubBodyType) && (
              <option value={merc.ubBodyType}>
                (custom: {merc.ubBodyType})
              </option>
            )}
          </select>
        </label>
      </div>
      <p className="text-xs text-wasteland-400 mt-2">
        Race + nationality drive AI dialogue triggers (hated nationality,
        etc.). Body type selects the tactical-sprite animation pack — only
        the four humanoid types load merc sprites correctly.
      </p>
    </fieldset>
  );
}
