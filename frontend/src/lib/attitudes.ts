/**
 * Canonical JA2 1.13 attitude table: bAttitude index -> display label.
 *
 * Mirror of the engine's `Attitudes` enum (Tactical/soldier profile type.h:
 * ATT_NORMAL=0 … ATT_COWARD=9, NUM_ATTITUDES=10) and its display strings
 * `gzIMPAttitudesText[]` (i18n/_EnglishText.cpp). Also documented verbatim in
 * the MercProfiles.xml header legend ("Original Attitude"). Shared by Create
 * and Edit so the two can't drift. DO NOT eyeball — verify against the engine.
 */
export const ATTITUDE_OPTIONS: ReadonlyArray<readonly [number, string]> = [
  [0, "Normal"], [1, "Friendly"], [2, "Loner"], [3, "Optimist"], [4, "Pessimist"],
  [5, "Aggressive"], [6, "Arrogant"], [7, "Big Shot"], [8, "Asshole"], [9, "Coward"],
];
