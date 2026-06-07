import { useMemo } from "react";
import { useQuery } from "@tanstack/react-query";

import { getTraitSystem } from "../lib/api";
import type { Merc } from "../lib/schema";

interface Props {
  merc: Merc;
  onChange: (next: Merc) => void;
}

/**
 * Trait picker that reflects the active install's trait system.
 *
 * NT (New Traits / STOMP) — picks land in bNewSkillTrait1..N. Same Major
 *   trait twice grants Expert tier (engine reads slot-count, no separate ID).
 *   UI exposes 8 slots; engine supports up to 30. Slots 9-30 stay zero.
 *
 * OT (Old Traits) — picks land in bOldSkillTrait + bOldSkillTrait2. 2 slots;
 *   picking the same trait in both makes it an expert trait (stronger),
 *   except Electronics, Ambidextrous, and Camouflage, which can't be doubled.
 *
 * Same integer ID means DIFFERENT trait between systems (NT 13 = Night Ops,
 * OT 13 = Knifing). The picker disambiguates by sourcing the install's catalog.
 */
const NT_SLOT_COUNT = 8;

export default function TraitPicker({ merc, onChange }: Props) {
  const sys = useQuery({
    queryKey: ["trait-system"],
    queryFn: () => getTraitSystem(),
    staleTime: 5 * 60 * 1000,
  });

  const ntSlotKeys = useMemo(
    () => Array.from({ length: NT_SLOT_COUNT }, (_, i) => `bNewSkillTrait${i + 1}` as keyof Merc),
    [],
  );

  if (sys.isLoading) {
    return <p className="text-sm text-wasteland-500">Detecting trait system…</p>;
  }
  if (sys.isError || !sys.data) {
    return <p className="text-sm text-rust-400">Couldn't detect the trait system for this install.</p>;
  }

  const { system, catalog } = sys.data;
  const byId = new Map(catalog.map((c) => [c.id, c]));
  const noneOption = { id: 0, name: "None", tier: "Minor" as const };
  const options = catalog.find((c) => c.id === 0) ? catalog : [noneOption, ...catalog];

  const updateNt = (slotKey: keyof Merc, id: number) => {
    onChange({ ...merc, [slotKey]: id } as Merc);
  };
  const updateOt = (which: 1 | 2, id: number) => {
    onChange({
      ...merc,
      ...(which === 1 ? { bOldSkillTrait: id } : { bOldSkillTrait2: id }),
    } as Merc);
  };

  if (system === "OT") {
    return (
      <div className="space-y-3">
        <div className="rounded border border-wasteland-700 bg-wasteland-800/30 px-3 py-2 text-xs text-wasteland-400">
          Old Trait System active. Two trait slots. Picking the same trait in both
          makes it an expert trait (stronger effect); Electronics, Ambidextrous,
          and Camouflage can't be doubled. Switch systems in your game options.
        </div>
        <div className="grid grid-cols-2 gap-3">
          <label className="block">
            <span className="text-xs text-wasteland-300">Slot 1</span>
            <select
              className="input mt-1"
              value={merc.bOldSkillTrait}
              onChange={(e) => updateOt(1, Number(e.target.value))}
            >
              {options.map((t) => (
                <option key={t.id} value={t.id}>{t.name}</option>
              ))}
            </select>
          </label>
          <label className="block">
            <span className="text-xs text-wasteland-300">Slot 2</span>
            <select
              className="input mt-1"
              value={merc.bOldSkillTrait2}
              onChange={(e) => updateOt(2, Number(e.target.value))}
            >
              {options.map((t) => (
                <option key={t.id} value={t.id}>{t.name}</option>
              ))}
            </select>
          </label>
        </div>
      </div>
    );
  }

  // NT
  const ntPicks: number[] = ntSlotKeys.map((k) => Number(merc[k] ?? 0));
  const filledMajors = ntPicks.filter((id) => {
    const t = byId.get(id);
    return t && t.tier === "Major";
  });
  const expertTraits = new Set<number>();
  for (const id of filledMajors) {
    if (filledMajors.filter((x) => x === id).length >= 2) expertTraits.add(id);
  }
  const majorCount = new Set(filledMajors).size;
  const fillCount = ntPicks.filter((id) => id > 0).length;

  return (
    <div className="space-y-3">
      <div className="rounded border border-wasteland-700 bg-wasteland-800/30 px-3 py-2 text-xs text-wasteland-400">
        New Trait System (STOMP) active. {NT_SLOT_COUNT} slots shown; engine supports up to 30. Same Major
        trait twice = Expert tier (e.g. Auto Weapons + Auto Weapons = Auto Weapons Expert).
      </div>
      <div className="grid grid-cols-2 md:grid-cols-4 gap-2">
        {ntSlotKeys.map((slotKey, idx) => {
          const v = Number(merc[slotKey] ?? 0);
          const t = byId.get(v);
          return (
            <label key={String(slotKey)} className="block">
              <span className="text-xs text-wasteland-300">
                Slot {idx + 1}
                {t && t.tier === "Major" && <span className="ml-1 text-rust-400">★</span>}
              </span>
              <select
                className="input mt-1 text-xs"
                value={v}
                onChange={(e) => updateNt(slotKey, Number(e.target.value))}
              >
                {options.map((opt) => (
                  <option key={opt.id} value={opt.id}>
                    {opt.name}
                    {opt.tier === "Major" ? " ★" : ""}
                  </option>
                ))}
              </select>
            </label>
          );
        })}
      </div>
      <div className="text-xs text-wasteland-500 space-y-1">
        <div>
          {fillCount} / {NT_SLOT_COUNT} slots filled · {majorCount} Major trait
          {majorCount === 1 ? "" : "s"} (★)
        </div>
        {expertTraits.size > 0 && (
          <div className="text-rust-400">
            Expert tier:{" "}
            {Array.from(expertTraits)
              .map((id) => byId.get(id)?.name ?? `#${id}`)
              .join(", ")}
          </div>
        )}
      </div>
      <p className="text-xs text-wasteland-500">
        Leave slots at None unless you want the trait — empty slots are fine. Major (★) traits
        unlock per-weapon/per-skill bonuses; Minor traits modify utility (Stealth, Athletics, etc.).
      </p>
    </div>
  );
}
