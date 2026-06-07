import type { Merc } from "../../lib/schema";

/**
 * AIM-website economics — salary tiers, medical deposit, optional gear
 * cost. These drive the per-merc hire/refresh experience on the
 * Association of International Mercenaries page in-game.
 *
 * The four salary fields work together:
 *   - sSalary: per-day rate while contracted
 *   - uiWeeklySalary: 7-day contract price (bulk discount)
 *   - uiBiWeeklySalary: 14-day contract price (deeper discount)
 *   - usOptionalGearCost: surcharge if the player picks "supply gear"
 *
 * Vanilla pricing tiers cluster around the AIM tier (Magic/Buns
 * ~600-800/day, Raven ~1200, Steroid ~1600, top-tier ~2500-4000).
 * Mods sometimes use much higher numbers — the merc model accepts
 * up to UINT16 so any value round-trips.
 *
 * `sMedicalDepositAmount` is the upfront deposit the player pays if
 * `bMedicalDeposit=1`; refunded on safe return.
 */

export interface AimEconomicsFormProps {
  merc: Merc;
  onChange: <K extends keyof Merc>(field: K, value: Merc[K]) => void;
}

export default function AimEconomicsForm({
  merc, onChange,
}: AimEconomicsFormProps) {
  // Only AIM mercs (Type=1) have meaningful economics fields. Other
  // types (MERC, RPC, NPC) ignore most of these but still round-trip
  // the values if present in the source XML.
  const isAim = merc.Type === 1;
  return (
    <fieldset className="block border border-wasteland-700 rounded p-3">
      <legend className="text-sm font-medium text-wasteland-100 px-1">
        AIM economics
        {!isAim && (
          <span className="ml-2 text-xs text-wasteland-500">
            (this isn't an AIM merc — depending on how they're hired in-game, some of these costs may not be used)
          </span>
        )}
      </legend>
      <div className="grid grid-cols-2 sm:grid-cols-3 gap-3 mt-2">
        <label className="block">
          <span className="text-xs text-wasteland-300">Daily rate ($)</span>
          <input
            type="number"
            className="input mt-1"
            value={merc.sSalary}
            min={0}
            max={65535}
            onChange={(e) => onChange("sSalary", Number(e.target.value))}
          />
        </label>
        <label className="block">
          <span className="text-xs text-wasteland-300">Weekly contract ($)</span>
          <input
            type="number"
            className="input mt-1"
            value={merc.uiWeeklySalary}
            min={0}
            onChange={(e) => onChange("uiWeeklySalary", Number(e.target.value))}
          />
        </label>
        <label className="block">
          <span className="text-xs text-wasteland-300">Bi-weekly contract ($)</span>
          <input
            type="number"
            className="input mt-1"
            value={merc.uiBiWeeklySalary}
            min={0}
            onChange={(e) => onChange("uiBiWeeklySalary", Number(e.target.value))}
          />
        </label>
        <label className="block">
          <span className="text-xs text-wasteland-300">Optional gear cost ($)</span>
          <input
            type="number"
            className="input mt-1"
            value={merc.usOptionalGearCost}
            min={0}
            max={65535}
            onChange={(e) => onChange("usOptionalGearCost", Number(e.target.value))}
          />
        </label>
        <label className="block">
          <span className="text-xs text-wasteland-300">Medical deposit ($)</span>
          <input
            type="number"
            className="input mt-1"
            value={merc.sMedicalDepositAmount}
            min={0}
            max={32767}
            onChange={(e) =>
              onChange("sMedicalDepositAmount", Number(e.target.value))
            }
          />
        </label>
        <label className="block">
          <span className="text-xs text-wasteland-300">Requires deposit?</span>
          <select
            className="input mt-1"
            value={merc.bMedicalDeposit}
            onChange={(e) =>
              onChange(
                "bMedicalDeposit",
                Number(e.target.value) as Merc["bMedicalDeposit"],
              )
            }
          >
            <option value={0}>No</option>
            <option value={1}>Yes</option>
          </select>
        </label>
      </div>
    </fieldset>
  );
}
