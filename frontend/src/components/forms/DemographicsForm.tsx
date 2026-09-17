import { useQuery } from "@tanstack/react-query";
import { getBodyTypes, getHealth, type BodyTypeOptionApi } from "../../lib/api";
import type { Merc } from "../../lib/schema";
import { NATIONALITY_OPTIONS } from "../../lib/nationalities";
import { RACE_OPTIONS } from "../../lib/races";

/**
 * Race, nationality, body type. Engine-side these drive idle audio
 * pools, AI dialogue triggers (bHatedNationality), and which animation
 * pack is loaded for the tactical sprite.
 *
 * Race + nationality options are static engine tables. Body types come from
 * the selected target's sidecar registry because modded engines can append
 * safe, custom animation IDs.
 */

export interface DemographicsFormProps {
  merc: Merc;
  onChange: <K extends keyof Merc>(field: K, value: Merc[K]) => void;
}

export function mergeBodyTypeOptions(
  options: readonly BodyTypeOptionApi[],
  currentValue: number,
): BodyTypeOptionApi[] {
  return options.some((option) => option.id === currentValue)
    ? [...options]
    : [...options, {
      id: currentValue,
      name: `Custom ${currentValue}`,
      category: "observed",
      authorable: false,
    }];
}

export function bodyTypeQueryKey(installId: string | null | undefined) {
  return ["body-types", installId ?? "active"] as const;
}

export default function DemographicsForm({
  merc, onChange,
}: DemographicsFormProps) {
  const health = useQuery({ queryKey: ["health"], queryFn: getHealth });
  const installId = health.data?.active_install_id;
  const bodyTypes = useQuery({
    queryKey: bodyTypeQueryKey(installId),
    queryFn: () => getBodyTypes(installId ?? undefined),
  });
  const bodyTypeOptions = mergeBodyTypeOptions(bodyTypes.data?.options ?? [], merc.ubBodyType);

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
            {bodyTypeOptions.map((option) => (
              <option
                key={option.id}
                value={option.id}
                disabled={option.authorable === false && option.id !== merc.ubBodyType}
              >
                {option.name}{option.category ? ` — ${option.category}` : ""}
                {option.authorable === false ? " (preserve only)" : ""}
              </option>
            ))}
          </select>
        </label>
      </div>
      <p className="text-xs text-wasteland-400 mt-2">
        Race + nationality drive AI dialogue triggers (hated nationality,
        etc.). Body type selects the tactical-sprite animation pack.
      </p>
      {bodyTypes.isLoading && (
        <p className="text-xs text-wasteland-400 mt-1">Loading target body types…</p>
      )}
      {bodyTypes.isError && (
        <p className="text-xs text-amber-300 mt-1">
          Couldn&apos;t load target body types; keeping the current value available.
        </p>
      )}
      {bodyTypes.data && (
        <p className="text-xs text-wasteland-400 mt-1">
          Target registry: {bodyTypes.data.mod_id} ({bodyTypes.data.source}).
        </p>
      )}
    </fieldset>
  );
}
