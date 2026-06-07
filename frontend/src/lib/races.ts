/**
 * Canonical JA2 1.13 race table: bRace index -> display label.
 *
 * Verbatim mirror of the engine's own enum + string array; DO NOT invent
 * labels. Sources:
 *   - enum Races        Tactical/soldier profile type.h
 *       (WHITE = 0, BLACK = 1, ASIAN = 2, ESKIMO = 3, HISPANIC = 4; NUM_RACES = 5)
 *   - labels szRaceText[]  i18n/_EnglishText.cpp (English build)
 *
 * bRace is clamped to [0, NUM_RACES - 1] at load (Tactical/XML_Profiles.cpp,
 * "min(NUM_RACES-1, ...)"), so only 0..4 are ever valid. The beta's
 * American / Hispanic / European / African / Asian list was fabricated and
 * mislabeled every value (e.g. a merc stored 1 = Black showed as "Hispanic").
 *
 * "Eskimo" is the engine's own term, kept verbatim to match in-game text.
 * Labels are Title-cased from the engine's lowercase strings for the dropdown.
 */
export const RACE_OPTIONS: ReadonlyArray<readonly [number, string]> = [
  [0, "White"], [1, "Black"], [2, "Asian"], [3, "Eskimo"], [4, "Hispanic"],
];
