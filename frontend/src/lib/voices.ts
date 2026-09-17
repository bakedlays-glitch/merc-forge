/**
 * Vanilla JA2 voice donor table: usVoiceIndex 0-39 -> display name.
 *
 * A merc's usVoiceIndex points at the slot whose voice-clip set it plays
 * in-game (battle barks, hire/dismiss lines). Slots 0-39 ship voiced in the
 * base game; picking one lets a new merc borrow a canonical voice until it
 * gets its own clips. This is the single source shared by the Create and Edit
 * forms — do not re-copy the list into a component.
 */
export const VANILLA_VOICE_OPTIONS: ReadonlyArray<readonly [number, string]> = [
  [0, "Chosen One"], [1, "Sulik"], [2, "Trader"], [3, "Cassidy"],
  [4, "Ivan"], [5, "Steroid"], [6, "Wolf"], [7, "Grizzly"],
  [8, "Hitman"], [9, "Lynx"], [10, "Magic"], [11, "Stephen"],
  [12, "Scope"], [13, "Reaper"], [14, "Buns"], [15, "Tycho"],
  [16, "Buzz"], [17, "Raider"], [18, "Raven"], [19, "Static"],
  [20, "Len"], [21, "Danny"], [22, "Spider"], [23, "Igor"],
  [24, "Razor"], [25, "Fox"], [26, "Lynx (orig)"], [27, "Shadow"],
  [28, "Leech"], [29, "Numb"], [30, "Bull"], [31, "Vicki"],
  [32, "Nails"], [33, "Bubba"], [34, "Killian"], [35, "Fidel"],
  [36, "Dr. Q"], [37, "Meltdown"], [38, "Stogie"], [39, "Gus"],
];

/** Display label for a voice index: "Slot 9 — Lynx" for the vanilla range,
 *  or "Slot N — (custom)" for anything a mod install pushed past 39. */
export function voiceLabel(index: number): string {
  const hit = VANILLA_VOICE_OPTIONS.find(([idx]) => idx === index);
  return hit ? `Slot ${index} — ${hit[1]}` : `Slot ${index} — (custom)`;
}
