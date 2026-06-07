import type { Merc } from "./schema";

/**
 * Suggest AIM-style salary tiers from a merc's stats + level.
 *
 * Vanilla AIM observations:
 *   - L3, ~50s stats (Vicki tier) → ~$500-700/day
 *   - L5, ~70s stats (Lynx tier)  → ~$1,200-1,500/day
 *   - L7, ~80s stats (Magic tier) → ~$2,400/day
 *   - L9, ~90s stats (Gus tier)   → ~$3,000+/day
 *
 * Formula: daily = avg_combat_stat × exp_level × 1.6, rounded to $50.
 * Weekly ≈ daily × 6 (small discount for committing to a week).
 * Bi-weekly ≈ daily × 11 (bigger discount for 2-week contract).
 *
 * Returned values are advisory — the user can override on the Attributes step.
 */
export interface SuggestedSalary {
  daily: number;
  weekly: number;
  biWeekly: number;
  averageCombatStat: number;
}

export function suggestSalary(merc: Pick<
  Merc,
  "bStrength" | "bAgility" | "bDexterity" | "bWisdom" | "bMarksmanship" | "bExpLevel"
>): SuggestedSalary {
  const combat = [
    merc.bStrength,
    merc.bAgility,
    merc.bDexterity,
    merc.bWisdom,
    merc.bMarksmanship,
  ];
  const avg = combat.reduce((s, x) => s + x, 0) / combat.length;
  const level = Math.max(1, merc.bExpLevel);
  const raw = avg * level * 1.6;
  const daily = Math.max(50, Math.round(raw / 50) * 50);
  const weekly = Math.max(daily * 5, Math.round((daily * 6) / 50) * 50);
  const biWeekly = Math.max(daily * 10, Math.round((daily * 11) / 50) * 50);
  return { daily, weekly, biWeekly, averageCombatStat: Math.round(avg) };
}

/** Returns true if any of the merc's salary fields diverge >50% from suggestion. */
export function salaryIsOutOfBand(
  merc: Pick<
    Merc,
    "sSalary" | "uiWeeklySalary" | "uiBiWeeklySalary"
    | "bStrength" | "bAgility" | "bDexterity" | "bWisdom" | "bMarksmanship" | "bExpLevel"
  >,
): { outOfBand: boolean; suggestion: SuggestedSalary } {
  const sug = suggestSalary(merc);
  const off = (actual: number, expected: number) => {
    if (expected === 0) return false;
    return Math.abs(actual - expected) / expected > 0.5;
  };
  return {
    outOfBand:
      off(merc.sSalary, sug.daily)
      || off(merc.uiWeeklySalary, sug.weekly)
      || off(merc.uiBiWeeklySalary, sug.biWeekly),
    suggestion: sug,
  };
}
