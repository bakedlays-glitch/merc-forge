/**
 * Demo agenda — "Canon Building Library" showcase (~80 s).
 *
 * Beats: countdown for OBS sync → title caption → camera glides to an
 * open area → Generate tab → browse three library cards → arm one →
 * the real sprite ghost follows the cursor across the map → stamp two
 * buildings (each gets its own room) → ESC → camera pass over the
 * result → undo both → outro.
 *
 * SAFETY: uses the scratch test copy of C6 — NEVER a live install map —
 * and never saves (the two stamps are undone at the end, and no verb in
 * this agenda touches the Save button). The Ja2Set.dat.xml of the Copy
 * install is read-only input for the tileset art.
 *
 * Verb reference: README.md in this directory.
 *
 * Paths: the .dat is the in-repo scratch test copy of C6; the install
 * (read-only tileset art) comes from JA2_INSTALL, defaulting to a generic
 * placeholder so this rig carries no machine-specific path.
 */
import path from "node:path";
import { fileURLToPath } from "node:url";

const REPO = path.resolve(
  path.dirname(fileURLToPath(import.meta.url)), "..", "..", "..");
const INSTALL = process.env.JA2_INSTALL || "C:/Jagged Alliance 2/<your-1.13-install>";

export default {
  viewport: { width: 1920, height: 1080 },
  dat: path.join(REPO, "scratch", "clifftest", "C6_test.DAT"),
  xml: path.join(INSTALL, "Data-1.13", "Ja2Set.dat.xml"),
  steps: [
    // ── OBS sync + title ────────────────────────────────────────────
    ["countdown", 3],
    ["caption", "MapForge — Canon Building Library"],
    ["shot", "01_title"],
    ["wait", 1800],

    // ── Glide the camera to an open area of the test sector ────────
    ["camera", 80, 80, 2200],
    ["zoom", 1.25, 1100],
    ["wait", 600],

    // ── Open the Generate panel ─────────────────────────────────────
    ["activateTab", "Generate"],
    ["caption", "Real buildings, grafted from the game's own maps"],
    ["waitFor", "[data-demo-card]", 120000],
    ["wait", 800],
    ["shot", "02_generate_tab"],

    // ── Browse the library: slow glide over three cards ─────────────
    ["moveMouse", ":nth-match([data-demo-card], 1)", 1100],
    ["wait", 900],
    ["moveMouse", ":nth-match([data-demo-card], 2)", 900],
    ["wait", 900],
    ["moveMouse", ":nth-match([data-demo-card], 3)", 900],
    ["wait", 900],

    // ── Arm placement with a mid-size card ──────────────────────────
    ["click", ":nth-match([data-demo-card], 3)"],
    ["caption", "Click to place — the real building follows your cursor"],
    ["wait", 700],

    // ── Carry the ghost across the map (it follows the cursor) ──────
    ["moveMouse", { tile: [68, 88] }, 1400],
    ["wait", 500],
    ["moveMouse", { tile: [83, 72] }, 1500],
    ["wait", 500],
    ["shot", "03_ghost_midpath"],
    ["moveMouse", { tile: [72, 74] }, 1300],
    ["wait", 700],

    // ── Stamp building #1 ────────────────────────────────────────────
    ["click", { tile: [72, 74] }],
    ["wait", 1200],
    ["shot", "04_first_stamp"],

    // ── Stamp building #2 elsewhere ─────────────────────────────────
    ["moveMouse", { tile: [90, 87] }, 1500],
    ["wait", 600],
    ["click", { tile: [90, 87] }],
    ["caption", "Each gets its own room — automatically"],
    ["wait", 1400],
    ["shot", "05_second_stamp"],

    // ── Exit placement, admire the result ───────────────────────────
    ["press", "Escape"],
    ["wait", 500],
    ["caption", null],
    ["camera", 75, 77, 1800],
    ["wait", 800],
    ["camera", 91, 90, 2000],
    ["wait", 800],
    ["shot", "06_both_buildings"],

    // ── Undo per building ────────────────────────────────────────────
    ["caption", "Undo works per building"],
    ["wait", 1000],
    ["press", "Control+z"],
    ["wait", 1300],
    ["press", "Control+z"],
    ["wait", 1300],
    ["shot", "07_after_undo"],

    // ── Outro ────────────────────────────────────────────────────────
    ["done", "That's the building library."],
  ],
};
