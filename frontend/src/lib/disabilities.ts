/**
 * Canonical JA2 1.13 disability table: bDisability index -> display label.
 *
 * Mirror of the engine's disabilities enum `PersonalityTrait`
 * (Tactical/soldier profile type.h: NO_DISABILITY=0 … SELF_HARM=12,
 * NUM_DISABILITIES=13). Also documented in the MercProfiles.xml header legend
 * ("STOMP Disabilities"). Shared by Create and Edit so the two can't drift.
 * DO NOT eyeball — verify against the engine.
 */
export const DISABILITY_OPTIONS: ReadonlyArray<readonly [number, string]> = [
  [0, "None"], [1, "Heat Intolerant"], [2, "Nervous"], [3, "Claustrophobic"],
  [4, "Non-Swimmer"], [5, "Fear of Insects"], [6, "Forgetful"], [7, "Psycho"],
  [8, "Deaf"], [9, "Shortsighted"], [10, "Hemophiliac"], [11, "Fear of Heights"],
  [12, "Self-Harming"],
];
