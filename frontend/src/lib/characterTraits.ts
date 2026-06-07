/**
 * Canonical JA2 1.13 STOMP character-trait table: bCharacterTrait index -> label.
 *
 * Mirror of the engine's `CharacterTraits` enum (Tactical/soldier profile type.h:
 * CHAR_TRAIT_NORMAL=0 … CHAR_TRAIT_COWARD=13, NUM_CHAR_TRAITS=14) and its display
 * strings `gzIMPCharacterTraitText[]` (i18n/_EnglishText.cpp) — note index 0 shows
 * in-game as "Neutral" even though the enum constant is CHAR_TRAIT_NORMAL. Also in
 * the MercProfiles.xml header legend ("STOMP Character Traits"). Shared by Create
 * and Edit so the two can't drift. DO NOT eyeball — verify against the engine.
 */
export const CHARACTER_TRAIT_OPTIONS: ReadonlyArray<readonly [number, string]> = [
  [0, "Neutral"], [1, "Sociable"], [2, "Loner"], [3, "Optimist"], [4, "Assertive"],
  [5, "Intellectual"], [6, "Primitive"], [7, "Aggressive"], [8, "Phlegmatic"],
  [9, "Dauntless"], [10, "Pacifist"], [11, "Malicious"], [12, "Show-off"], [13, "Coward"],
];
