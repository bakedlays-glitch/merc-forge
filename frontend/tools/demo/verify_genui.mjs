#!/usr/bin/env node
/**
 * DOM-verify the Generate panel's new visual controls (gen-UI revamp):
 *   1. generator picker cards render and select
 *   2. picking scatter with a multi-sub brush shows the variant
 *      thumbnail grid with ALL subs included
 *   3. the "Don't place on" row renders with Occupied checked
 *   4. bank shows the segmented mode control + the high-side compass
 *
 * Headless Playwright against the dev rig (vite :1420 → sidecar :8774).
 * Uses the scratch A2 copy — never a live install map, never saves.
 * Screenshots land in scratch/genui_frames/.
 *
 *   node verify_genui.mjs [--base http://localhost:1420]
 */
import { chromium } from "playwright";
import path from "node:path";
import fs from "node:fs";
import { fileURLToPath } from "node:url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const REPO = path.resolve(__dirname, "..", "..", "..");
const SHOTS = path.join(REPO, "scratch", "genui_frames");
fs.mkdirSync(SHOTS, { recursive: true });

const argv = process.argv.slice(2);
const flag = (n) => { const i = argv.indexOf(n); return i >= 0 ? argv[i + 1] : undefined; };
const base = flag("--base") ?? "http://localhost:1420";

const params = new URLSearchParams({
  dat: "C:/AI Projects/The Wasteland/MercWizard2/scratch/clifftest/A2.DAT",
  xml: "C:/Jagged Alliance 2/Jagged Alliance 2 Gold 1.13 Mod Prototype - Copy/Data-1.13/Ja2Set.dat.xml",
  tileset: "0",
  demo: "1",
});
const url = `${base}/mapforge/sector?${params}`;

let failures = 0;
const ok = (cond, msg) => {
  console.log(`${cond ? "PASS" : "FAIL"}  ${msg}`);
  if (!cond) failures++;
};

const browser = await chromium.launch({ headless: true });
const page = await browser.newPage({ viewport: { width: 1920, height: 1080 } });
page.on("pageerror", (e) => console.error("PAGE ERROR:", e.message));

console.log("open:", url);
await page.goto(url, { waitUntil: "domcontentloaded" });

// Wait for the editor + demo hook to be fully up.
await page.waitForFunction(
  () => window.__mapforgeDemo?.getState?.()?.ready === true,
  null, { timeout: 120000 },
);
const shot = (name) => page.screenshot({ path: path.join(SHOTS, `${name}.png`) });

// ── Open the Generate tab (dockview activates on pointerdown) ─────────
const tab = page.locator('.dv-tab:has-text("Generate")').first();
await tab.waitFor({ state: "visible", timeout: 30000 });
await tab.dispatchEvent("pointerdown");
await tab.dispatchEvent("pointerup");
await tab.click();

// ── 1. Generator cards ────────────────────────────────────────────────
await page.locator("[data-gen-card]").first().waitFor({ state: "visible", timeout: 30000 });
const cardNames = await page.locator("[data-gen-card]").evaluateAll(
  (els) => els.map((e) => e.getAttribute("data-gen-card")));
console.log("cards:", cardNames.join(", "));
for (const want of ["scatter", "cluster", "density-falloff", "fill", "rect", "bank", "wipe"]) {
  ok(cardNames.includes(want), `card present: ${want}`);
}
ok(cardNames.at(-1) === "building", "building card demoted to last");
await shot("01_generator_cards");

// Select fill → card activates, form appears.
await page.locator('[data-gen-card="fill"]').click();
ok(await page.locator('[data-gen-card="fill"][data-active="1"]').count() === 1,
  "clicking a card selects it (data-active)");
await shot("02_fill_selected");

// ── 2. Multi-sub brush → variant grid all-included ───────────────────
// Arm a brush via the canvas eyedropper: right-click the tree at tile
// (123,50) — structs slot 20, a multi-sub tree STI in tileset 0.
await page.evaluate(() => window.__mapforgeDemo.panTo(123, 50, 0));
await page.waitForTimeout(600);
const pt = await page.evaluate(() => window.__mapforgeDemo.tileToScreen(123, 50));
ok(!!pt, `tileToScreen resolved tree tile (${JSON.stringify(pt)})`);
await page.mouse.click(pt.x, pt.y, { button: "right" });
await page.waitForTimeout(400);

// Pick scatter — it must inherit the brush and show the variant grid.
await page.locator('[data-gen-card="scatter"]').click();
await page.locator("[data-variant-grid]").waitFor({ state: "visible", timeout: 15000 });
const thumbs = await page.locator("[data-variant-thumb]").count();
const included = await page.locator('[data-variant-thumb][data-included="1"]').count();
console.log(`variant grid: ${thumbs} thumbs, ${included} included`);
ok(thumbs > 1, `variant grid shows multiple subs (${thumbs})`);
ok(included === thumbs, "ALL subs start included (variety by default)");

// Toggle one off → composed selection shrinks; "All" restores.
await page.locator("[data-variant-thumb]").first().click();
ok(await page.locator('[data-variant-thumb][data-included="1"]').count() === thumbs - 1,
  "clicking a thumb excludes it");
await page.locator("[data-variant-grid] button:has-text('All')").click();
ok(await page.locator('[data-variant-thumb][data-included="1"]').count() === thumbs,
  "All button re-includes everything");

// ── 3. Don't-place-on row, Occupied default ON ────────────────────────
await page.locator("[data-avoid-row]").waitFor({ state: "visible", timeout: 5000 });
ok(await page.locator('[data-avoid="occupied"]').isChecked(), "Occupied checked by default");
for (const id of ["water", "roads", "structures", "trees"]) {
  ok(!(await page.locator(`[data-avoid="${id}"]`).isChecked()), `${id} unchecked by default`);
}
await shot("03_scatter_variants_avoid");

// ── 4. Bank: segmented control + compass ─────────────────────────────
await page.locator('[data-gen-card="bank"]').click();
await page.locator("[data-bank-mode]").waitFor({ state: "visible", timeout: 5000 });
ok(await page.locator('[data-bank-mode-opt="escarpment"]').count() === 1
  && await page.locator('[data-bank-mode-opt="plateau"]').count() === 1,
  "bank mode segmented control (Escarpment | Plateau)");
await page.locator("[data-bank-compass]").waitFor({ state: "visible", timeout: 5000 });
for (const side of ["N", "W", "NW", "NE", "SW", "SE"]) {
  ok(await page.locator(`[data-high-side="${side}"]:not([disabled])`).count() === 1,
    `compass ${side} enabled`);
}
for (const side of ["S", "E"]) {
  ok(await page.locator(`[data-high-side="${side}"][disabled]`).count() === 1,
    `compass ${side} disabled (faces away from iso camera)`);
}
await shot("04_bank_segmented_compass");

// Plateau hides the compass (no high side on a mesa).
await page.locator('[data-bank-mode-opt="plateau"]').click();
await page.waitForTimeout(200);
ok(await page.locator("[data-bank-compass]").count() === 0,
  "plateau mode hides the compass");
await shot("05_bank_plateau");

await browser.close();
console.log(failures === 0 ? "\nALL DOM CHECKS PASSED" : `\n${failures} CHECKS FAILED`);
process.exit(failures === 0 ? 0 : 1);
