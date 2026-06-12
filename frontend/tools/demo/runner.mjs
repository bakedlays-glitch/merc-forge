#!/usr/bin/env node
/**
 * MapForge scripted demo runner.
 *
 * Plays a written "agenda" (a scene list of verbs) against the real
 * MapForge editor UI in its own Chromium window, with human-paced mouse
 * glides, eased camera pans and on-screen captions — so feature videos
 * can be recorded with OBS without performing anything by hand.
 * Re-recording after a UI change = edit the agenda, re-run.
 *
 * Usage:
 *   node runner.mjs <agenda.mjs|agenda.json> [options]
 *
 * Options:
 *   --record <file.webm>   also capture Playwright's own video of the run
 *                          (self-verification / bonus output — the real
 *                          recordings are OBS over the headed window)
 *   --headless             run without a visible window (verification)
 *   --shots <dir>          where the `shot` verb saves screenshots
 *                          (default: <repo>/scratch/demo_frames)
 *   --base <url>           editor origin (default http://localhost:1420)
 *
 * The agenda module must `export default { viewport?, dat, xml,
 * tileset?, steps: [...] }` — see agenda_building_library.mjs and
 * README.md for the verb reference.
 *
 * Requires the app's demo hook: the page is opened with &demo=1, which
 * makes MapForgeSector expose window.__mapforgeDemo
 * { panTo, zoomTo, caption, getState, tileToScreen }.
 */
import { chromium } from "playwright";
import { pathToFileURL, fileURLToPath } from "node:url";
import path from "node:path";
import fs from "node:fs";

const __dirname = path.dirname(fileURLToPath(import.meta.url));

// ─── CLI ──────────────────────────────────────────────────────────────
const argv = process.argv.slice(2);
if (argv.length === 0 || argv[0].startsWith("--")) {
  console.error("usage: node runner.mjs <agenda.mjs|json> [--record out.webm] [--headless] [--shots dir] [--base url]");
  process.exit(2);
}
const agendaPath = path.resolve(argv[0]);
const flag = (name) => {
  const i = argv.indexOf(name);
  return i >= 0 ? argv[i + 1] : undefined;
};
const recordFile = flag("--record") ? path.resolve(flag("--record")) : null;
const headless = argv.includes("--headless");
const baseUrl = flag("--base") ?? "http://localhost:1420";
const shotsDir = path.resolve(flag("--shots")
  ?? path.join(__dirname, "..", "..", "..", "scratch", "demo_frames"));

// ─── Load agenda ──────────────────────────────────────────────────────
let agenda;
if (agendaPath.endsWith(".json")) {
  agenda = JSON.parse(fs.readFileSync(agendaPath, "utf8"));
} else {
  agenda = (await import(pathToFileURL(agendaPath).href)).default;
}
if (!agenda || !Array.isArray(agenda.steps)) {
  console.error(`agenda ${agendaPath} must export default { dat, xml, steps: [...] }`);
  process.exit(2);
}
const viewport = agenda.viewport ?? { width: 1920, height: 1080 };

const params = new URLSearchParams();
params.set("dat", agenda.dat);
params.set("xml", agenda.xml);
if (agenda.tileset !== undefined) params.set("tileset", String(agenda.tileset));
params.set("demo", "1");
const url = `${baseUrl}/mapforge/sector?${params.toString()}`;

// ─── Helpers ──────────────────────────────────────────────────────────
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const easeInOutCubic = (t) => (t < 0.5 ? 4 * t * t * t : 1 - Math.pow(-2 * t + 2, 3) / 2);

function log(msg) {
  const t = ((Date.now() - t0) / 1000).toFixed(1).padStart(6);
  console.log(`[${t}s] ${msg}`);
}

// ─── Launch ───────────────────────────────────────────────────────────
const t0 = Date.now();
log(`agenda: ${path.basename(agendaPath)}`);
log(`url:    ${url}`);
const browser = await chromium.launch({
  headless,
  args: [`--window-size=${viewport.width},${viewport.height + 88}`],
});
const contextOpts = { viewport };
if (recordFile) {
  fs.mkdirSync(path.dirname(recordFile), { recursive: true });
  contextOpts.recordVideo = { dir: path.dirname(recordFile), size: viewport };
}
const context = await browser.newContext(contextOpts);
const page = await context.newPage();
let mouse = { x: viewport.width / 2, y: viewport.height / 2 }; // tracked cursor

// Surface page errors loudly — a crashed React tree would otherwise just
// stall the agenda.
page.on("pageerror", (e) => console.error("PAGE ERROR:", e.message));

// ─── Verb implementations ─────────────────────────────────────────────

/** Evaluate against the demo hook. */
const hook = (fnBody, ...args) =>
  page.evaluate(
    ({ body, a }) =>
      // eslint-disable-next-line no-new-func
      new Function("demo", "...args", `return (${body})(demo, ...args);`)(
        window.__mapforgeDemo, ...a),
    { body: fnBody, a: args },
  );

/** Resolve a target spec → client point.
 *  - "css selector"        → center of the first matching element
 *  - { x, y }              → client px as-is
 *  - { tile: [x, y] }      → via the hook's tileToScreen
 */
async function resolvePoint(target) {
  if (typeof target === "string") {
    const el = page.locator(target).first();
    await el.waitFor({ state: "visible", timeout: 15000 });
    await el.scrollIntoViewIfNeeded();
    const box = await el.boundingBox();
    if (!box) throw new Error(`no bounding box for ${target}`);
    return { x: box.x + box.width / 2, y: box.y + box.height / 2 };
  }
  if (Array.isArray(target?.tile)) {
    const p = await page.evaluate(
      ([tx, ty]) => window.__mapforgeDemo?.tileToScreen(tx, ty),
      target.tile,
    );
    if (!p) throw new Error(`tileToScreen(${target.tile}) returned null`);
    return p;
  }
  if (typeof target?.x === "number" && typeof target?.y === "number") return target;
  throw new Error(`bad mouse target: ${JSON.stringify(target)}`);
}

/** Human-paced eased glide of the real mouse to a target over `ms`. */
async function glideTo(target, ms = 900) {
  const to = await resolvePoint(target);
  const from = { ...mouse };
  const start = Date.now();
  for (;;) {
    const t = Math.min(1, (Date.now() - start) / Math.max(1, ms));
    const k = easeInOutCubic(t);
    mouse = { x: from.x + (to.x - from.x) * k, y: from.y + (to.y - from.y) * k };
    await page.mouse.move(mouse.x, mouse.y);
    if (t >= 1) break;
    await sleep(16);
  }
  // Re-resolve once: panels/canvas may have shifted during the glide.
  const settled = await resolvePoint(target);
  if (Math.abs(settled.x - mouse.x) > 1 || Math.abs(settled.y - mouse.y) > 1) {
    mouse = settled;
    await page.mouse.move(mouse.x, mouse.y);
  }
}

const verbs = {
  /** caption(text|null) — show/hide the big demo caption bar. */
  caption: (text) => hook("(d, t) => d.caption(t)", text),

  /** wait(ms) — deliberate pause (narration beats). */
  wait: (ms) => sleep(ms),

  /** camera(x, y, ms) — eased pan centering tile (x, y). */
  camera: async (x, y, ms = 1500) => {
    await hook("(d, x, y, ms) => d.panTo(x, y, ms)", x, y, ms);
  },

  /** zoom(z, ms) — eased zoom about the viewport center. */
  zoom: async (z, ms = 900) => {
    await hook("(d, z, ms) => d.zoomTo(z, ms)", z, ms);
  },

  /** moveMouse(target, ms) — smooth glide; target = selector | {x,y} |
   * {tile:[x,y]}. */
  moveMouse: (target, ms = 900) => glideTo(target, ms),

  /** click(target) — glide briefly if not already there, then click. */
  click: async (target) => {
    await glideTo(target, 350);
    await sleep(120);
    await page.mouse.down();
    await sleep(70);
    await page.mouse.up();
  },

  /** drag(from, to, ms) — press, eased move, release (region picks). */
  drag: async (from, to, ms = 1000) => {
    await glideTo(from, 500);
    await sleep(120);
    await page.mouse.down();
    await sleep(120);
    await glideTo(to, ms);
    await sleep(120);
    await page.mouse.up();
  },

  /** press(key) — keyboard, e.g. "Escape", "Control+z". */
  press: async (key) => {
    await page.keyboard.press(key);
  },

  /** clickText(text) — glide to + click the button containing text. */
  clickText: async (text) => {
    const sel = `button:has-text(${JSON.stringify(text)})`;
    await glideTo(sel, 700);
    await sleep(150);
    await page.mouse.down();
    await sleep(70);
    await page.mouse.up();
  },

  /** activateTab(title) — activate a dockview tab by its title. Glides
   * the mouse there for the camera, then uses the pointerdown/up +
   * click dispatch pattern (dockview activates on pointerdown). */
  activateTab: async (title) => {
    const tab = page
      .locator(`.dv-tab:has-text(${JSON.stringify(title)})`)
      .first();
    await tab.waitFor({ state: "visible", timeout: 15000 });
    await glideTo(`.dv-tab:has-text(${JSON.stringify(title)})`, 700);
    await sleep(150);
    await tab.dispatchEvent("pointerdown");
    await tab.dispatchEvent("pointerup");
    await tab.click();
    await sleep(250);
  },

  /** waitFor(selector, timeoutMs) — wait until an element is visible. */
  waitFor: async (selector, timeout = 30000) => {
    await page.locator(selector).first().waitFor({ state: "visible", timeout });
  },

  /** countdown(n) — big n..1 caption beat for OBS sync. */
  countdown: async (n = 3) => {
    for (let i = n; i >= 1; i--) {
      await hook("(d, t) => d.caption(t)", `${i}`);
      await sleep(1000);
    }
    await hook("(d) => d.caption(null)");
    await sleep(300);
  },

  /** shot(name) — save a verification screenshot to --shots dir. */
  shot: async (name) => {
    fs.mkdirSync(shotsDir, { recursive: true });
    const file = path.join(shotsDir, `${name}.png`);
    await page.screenshot({ path: file });
    log(`  shot → ${file}`);
  },

  /** done(text) — closing caption, hold, fade out. */
  done: async (text) => {
    await hook("(d, t) => d.caption(t)", text);
    await sleep(3000);
    await hook("(d) => d.caption(null)");
    await sleep(500);
  },
};

// ─── Run ──────────────────────────────────────────────────────────────
let exitCode = 0;
try {
  log("navigating…");
  await page.goto(url, { waitUntil: "domcontentloaded" });

  // Wait for the demo hook + editor readiness (session open, atlas
  // decoded, first paint). First-ever atlas bake / library scan can be
  // slow — generous timeout.
  log("waiting for editor ready (atlas + session)…");
  await page.waitForFunction(
    () => window.__mapforgeDemo?.getState().ready === true,
    null,
    { timeout: 240000, polling: 500 },
  );
  log("editor ready — running agenda");
  // Park the mouse somewhere neutral so the first glide has an origin.
  await page.mouse.move(mouse.x, mouse.y);

  for (const [i, step] of agenda.steps.entries()) {
    const [verb, ...args] = step;
    const fn = verbs[verb];
    if (!fn) throw new Error(`step ${i}: unknown verb "${verb}"`);
    log(`step ${String(i + 1).padStart(2)}/${agenda.steps.length}  ${verb}(${args.map((a) => JSON.stringify(a)).join(", ")})`);
    await fn(...args);
  }
  log("agenda complete");
} catch (e) {
  console.error("RUNNER FAILED:", e);
  exitCode = 1;
} finally {
  // Finalize video before closing the browser.
  let video = null;
  if (recordFile) video = page.video();
  await context.close();
  if (video) {
    const tmp = await video.path();
    try {
      fs.renameSync(tmp, recordFile);
    } catch {
      fs.copyFileSync(tmp, recordFile);
      fs.unlinkSync(tmp);
    }
    log(`video → ${recordFile}`);
  }
  await browser.close();
}
process.exit(exitCode);
