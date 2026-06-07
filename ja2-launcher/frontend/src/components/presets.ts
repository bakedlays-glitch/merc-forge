// Preset bundles for the Presets tab.
//
// Each preset is a curated diff that writes ~5-20 keys to Data-User/*.ini
// (or Ja2.ini directly via target: "ja2_ini") in one click. Designed to be
// additive — applying "Beginner-friendly" doesn't wipe "Engine compatibility";
// users click "Default 1.13" to clear all overrides first.
//
// To add a preset later: append a new entry below. Each PresetChange must
// reference an actual key in one of the schema-driven INIs (Ja2_Options.ini,
// Skills_Settings.INI, etc.) — the launcher doesn't validate these against
// the schema at apply time, so a typo'd key name silently writes a no-op
// override that the engine ignores.

import type { Preset } from "../types/modpack";

export const PRESETS: Preset[] = [
  {
    id: "default-113",
    name: "Default 1.13",
    description:
      "Removes every override you've made. Game runs on the modpack's stock 1.13 defaults — no Data-User tweaks. Use this to start clean before applying another preset, or to undo experimentation.",
    tags: ["reset", "vanilla"],
    is_reset: true,
    changes: [],
  },

  {
    id: "engine-compat",
    name: "Engine compatibility",
    description:
      "The 4 known-working overrides for this modpack's ja2.exe build. Data-1.13 ships values that exceed the engine's compile-time limits; without these, every launch spams 'outside valid range' warnings into iniErrorReport.log. Recommended for every player on this modpack.",
    tags: ["fix", "recommended"],
    changes: [
      {
        ini_file: "Ja2_Options.ini",
        section: "Mini Events Settings",
        key: "MINI_EVENTS_MIN_HOURS_BETWEEN_EVENTS",
        value: "24",
        target: "user",
      },
      {
        ini_file: "Ja2_Options.ini",
        section: "System Limit Settings",
        key: "MAX_NUMBER_CIVS_IN_TACTICAL",
        value: "40",
        target: "user",
      },
      {
        ini_file: "Ja2_Options.ini",
        section: "System Limit Settings",
        key: "MAX_NUMBER_CREATURES_IN_TACTICAL",
        value: "40",
        target: "user",
      },
      {
        ini_file: "Ja2_Options.ini",
        section: "System Limit Settings",
        key: "MAX_NUMBER_PLAYER_MERCS",
        value: "32",
        target: "user",
      },
    ],
  },

  {
    id: "beginner-friendly",
    name: "Beginner-friendly",
    description:
      "Sensible defaults for someone new to JA2: skip the Sir-Tech intro, larger tooltips for modern displays, 1280×720 safe resolution. Touches Ja2.ini only — no game-rule changes.",
    tags: ["beginner", "qol"],
    changes: [
      {
        ini_file: "Ja2.ini",
        section: "Ja2 Settings",
        key: "PLAY_INTRO",
        value: "0",
        target: "ja2_ini",
      },
      {
        ini_file: "Ja2.ini",
        section: "Ja2 Settings",
        key: "TOOLTIP_SCALE_FACTOR",
        value: "150",
        target: "ja2_ini",
      },
      {
        ini_file: "Ja2.ini",
        section: "Ja2 Settings",
        key: "SCREEN_RESOLUTION",
        value: "4",
        target: "ja2_ini",
      },
      {
        ini_file: "Ja2.ini",
        section: "Ja2 Settings",
        key: "SCREEN_MODE_WINDOWED",
        value: "0",
        target: "ja2_ini",
      },
    ],
  },

  {
    id: "easy",
    name: "Easy mode",
    description:
      "Forgiving tactical settings: fewer enemies per battle, smaller squad to manage. Doesn't change the campaign — same maps, same NPCs, just less attrition per fight. Stackable with Engine compatibility.",
    tags: ["beginner", "difficulty"],
    changes: [
      {
        ini_file: "Ja2_Options.ini",
        section: "System Limit Settings",
        key: "MAX_NUMBER_ENEMIES_IN_TACTICAL",
        value: "32",
        target: "user",
      },
      {
        ini_file: "Ja2_Options.ini",
        section: "System Limit Settings",
        key: "MAX_NUMBER_PLAYER_MERCS",
        value: "20",
        target: "user",
      },
    ],
  },

  {
    id: "hard",
    name: "Hard mode",
    description:
      "Cranks up tactical pressure: maximum enemy density per sector, slightly constrained squad. For players who've cleared 1.13 once and want the engine pushed to its limits. Stackable with Engine compatibility.",
    tags: ["veteran", "difficulty"],
    changes: [
      {
        ini_file: "Ja2_Options.ini",
        section: "System Limit Settings",
        key: "MAX_NUMBER_ENEMIES_IN_TACTICAL",
        value: "40",
        target: "user",
      },
      {
        ini_file: "Ja2_Options.ini",
        section: "System Limit Settings",
        key: "MAX_NUMBER_PLAYER_MERCS",
        value: "16",
        target: "user",
      },
    ],
  },

  {
    id: "cinematic",
    name: "Cinematic",
    description:
      "Visual polish: skip intro, big tooltips, 1080p (if your monitor supports it). Doesn't change gameplay — just makes the UI feel more modern. Adjust resolution after to your monitor's native size.",
    tags: ["visual"],
    changes: [
      {
        ini_file: "Ja2.ini",
        section: "Ja2 Settings",
        key: "SCREEN_RESOLUTION",
        value: "20",
        target: "ja2_ini",
      },
      {
        ini_file: "Ja2.ini",
        section: "Ja2 Settings",
        key: "TOOLTIP_SCALE_FACTOR",
        value: "175",
        target: "ja2_ini",
      },
      {
        ini_file: "Ja2.ini",
        section: "Ja2 Settings",
        key: "PLAY_INTRO",
        value: "0",
        target: "ja2_ini",
      },
    ],
  },
];
