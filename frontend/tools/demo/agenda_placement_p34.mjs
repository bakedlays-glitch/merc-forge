#!/usr/bin/env node
/**
 * Verification of MapForge's mode-less "StarCraft-
 * style" placement: control groups, the command card, fence line-drag,
 * and the placement queue, against THIS lane's own dev servers and the
 * gecko_town_c16_v8_placement_lab.dat scratch copy.
 *
 * Same standalone-Playwright-script shape as agenda_placement_p1.mjs /
 * agenda_placement_p2.mjs, for the identical reason (read that file's own
 * header): runner.mjs's fixed verb table has no eval/fetch/canvas-capture/
 * modifier-click primitives this needs. Run directly with node:
 *
 *   node agenda_placement_p34.mjs [--base http://localhost:1421]
 *                                  [--sidecar http://127.0.0.1:8773]
 *                                  [--shots <dir>]
 *
 * GEOGRAPHY (all found/verified live against the lab map before writing
 * this script, via a throwaway recon session — never guessed):
 *   - Existing fence: slot 77 (junkfen.sti, family "furniture" -> arms a
 *     GHOST directly, oracle category "fence") sits at (221,106)+ on the
 *     structs layer. Used for the control-groups + command-card
 *     "selection" tests -- no need to place anything first.
 *   - Existing truck: slot 79 sub 1 (gastuf1.sti) at (213,159), footprint
 *     dx in [-5,0] dy in [-1,0]. Copy/pasted for the
 *     RING screenshot AND the placement-queue test (same technique
 *     used: a copy of an EXISTING structs-layer sprite carries its layer
 *     forward; a FRESH Brush Box arm of slot 79 lands on "objs" instead,
 *     invisible to the oracle -- see agenda_placement_p2.mjs's finding #1).
 *   - Existing road: slot 50 (roadtile.sti) objs entries in a dense grid
 *     from (186,0). No isolated road tile with clear neighbours exists
 *     anywhere on this town map (verified: an exhaustive scan at radius
 *     1-3, both axes, found zero). A FRESH Brush Box arm of slot 50 also
 *     lands in the WRONG layer for this purpose -- its palette category is
 *     "floor" -> CATEGORY_TO_LAYER routes it to "land", but the fence
 *     line-drag's ROAD gate reads `parsed.objs` (spec's own "roads live in
 *     the object layer"). So: copy an EXISTING road objs entry (D3's
 *     Ctrl+C scope explicitly includes objs) and paste it into a clean
 *     strip -- same layer-preservation trick as the truck.
 *   - Slot 86 (wirefenc.sti) is "wall" family -> a Brush Box arm paints a
 *     BRUSH, not a ghost, so it can never satisfy `fenceArmed` (which
 *     requires `placingBuilding.group`, i.e. a GHOST) -- read live in
 *     MapForgeSector.tsx (`fenceArmed` useMemo) before assuming otherwise.
 *     Same workaround as the truck: brush-paint ONE seed tile, select it,
 *     Ctrl+C/Ctrl+V -- the pasted copy is a single-item GHOST on the
 *     structs layer, category "fence" per /placement/tables -> satisfies
 *     `fenceArmed`, and the line-drag becomes reachable.
 *   - Slot 89 (gecko_chainlink.sti) IS "vehicle" family -> arms a ghost
 *     straight from the Brush Box (no copy/paste needed), and is ALSO
 *     oracle category "fence" -- used directly for the R-cycle test and
 *     the RING-near-truck screenshot.
 *   - Four confirmed-clear 14x14 blocks used so sub-tests never collide:
 *     BLOCK_D (70,310) for the R-cycle placement; BLOCK_B (300,250) for
 *     both fence-line sub-tests; BLOCK_C (200,280) for the truck queue.
 *     The command-card rect-fill paints LAND only (no occupancy gate), so
 *     it just uses a spot near wherever the camera already is.
 *
 * fenceRole() (mapPlacement.ts) verified by hand for slot 86 (ns=7 ew=6
 * nw=ne=sw=se=2): a straight run (any orientation, any position in the
 * run including both ends) always gets ns or ew; only a genuine 90-degree
 * MEET (an existing perpendicular fence tile as an extra neighbour) picks
 * a corner sub. The L-shape test's second drag STARTS at the first drag's
 * end tile, so that shared corner tile's neighbour set becomes {S, W} at
 * commit time (S from the second line, W from the first line's own
 * already-placed fence via `existing()`) -> fenceRole's "SW" key ->
 * subs.ne = 2. Confirmed against the live /placement/tables response
 * before writing the assertions, not assumed from the spec text alone.
 *
 * SAFETY: only ever opens the caller's lab .dat copy (never
 * gecko_town_c16_v8.dat, never _v8.dat), never clicks Save / Ctrl+S /
 * calls /save, and force-deletes every sidecar session opened against
 * that lab dat at the end so nothing in this run is left dirty in
 * sidecar memory.
 */
import { chromium } from "playwright";
import path from "node:path";
import fs from "node:fs";
import { fileURLToPath } from "node:url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));

// ─── CLI ────────────────────────────────────────────────────────────
const argv = process.argv.slice(2);
const flag = (name, def) => { const i = argv.indexOf(name); return i >= 0 ? argv[i + 1] : def; };
const BASE = flag("--base", "http://localhost:1421");
const SIDECAR = flag("--sidecar", "http://127.0.0.1:8773");
const SHOTS = path.resolve(flag("--shots", path.join(__dirname, "..", "..", "..", "scratch", "p34_placement_frames")));
fs.mkdirSync(SHOTS, { recursive: true });

const INSTALL_ROOT = process.env.JA2_INSTALL
  || "C:/Games/Jagged Alliance 2/<your-1.13-install>";
const LAB_DAT = `${INSTALL_ROOT}/_gecko_qa/gecko_town_c16_v8_placement_lab.dat`;
const XML_PATH = `${INSTALL_ROOT}/Data-1.13/Ja2Set.dat.xml`;
const TILESET = 72;
const API = `${SIDECAR}/api/v1/mapforge`;
const SETTINGS_KEY = "mapforge.settings.v1";
const GROUPS_KEY = "mapforge.controlGroups.v1";

const FENCE77 = { x: 221, y: 106, slot: 77, sub: 4 }; // existing junkfen -- ghost family, oracle category "fence"
const FENCE89 = { term: "chainlink", filename: "gecko_chainlink.sti", slot: 89 };
const FENCE86 = { term: "wirefenc", filename: "wirefenc.sti", slot: 86 };
const TRUCK = { x: 213, y: 159, slot: 79, sub: 1 };
const ROAD_SRC = { x: 186, y: 0, slot: 50 }; // existing road objs entry, sub verified live below

const BLOCK_D = { x0: 70, y0: 310 };  // R-cycle
const BLOCK_B = { x0: 300, y0: 250 }; // fence line-drag (both sub-tests)
const BLOCK_C = { x0: 200, y0: 280 }; // truck queue

// ─── Result tracking ────────────────────────────────────────────────
let failCount = 0;
const results = [];
const evidence = [];
function record(name, cond, observed) {
  const status = cond ? "PASS" : "FAIL";
  console.log(`${status}  ${name}${observed !== undefined ? `  [observed: ${observed}]` : ""}`);
  results.push({ name, status, observed });
  if (!cond) failCount++;
  return cond;
}
async function step(name, fn) {
  try {
    await fn();
  } catch (e) {
    record(name, false, `threw: ${e instanceof Error ? e.message : String(e)}`);
  }
}

// ─── Sidecar HTTP (Node-side) ───────────────────────────────────────
async function sidecarJson(pathAndQuery, opts) {
  const res = await fetch(`${API}${pathAndQuery}`, opts);
  const text = await res.text();
  let body; try { body = JSON.parse(text); } catch { body = text; }
  if (!res.ok) throw new Error(`${opts?.method ?? "GET"} ${pathAndQuery} -> ${res.status}: ${text.slice(0, 300)}`);
  return body;
}
const norm = (p) => String(p).replace(/\\/g, "/").toLowerCase();

async function findSession() {
  for (let i = 0; i < 40; i++) {
    const sessions = await sidecarJson("/sessions");
    const hit = sessions.find((s) => norm(s.dat_path) === norm(LAB_DAT));
    if (hit) return hit.session_id;
    await new Promise((r) => setTimeout(r, 500));
  }
  throw new Error("no sidecar session found for the lab dat after 20s");
}
async function getParsed(sessionId) {
  return sidecarJson(`/sessions/${encodeURIComponent(sessionId)}/parsed?_=${Date.now()}`);
}
function entriesAt(layerArr, gridno) { return layerArr[gridno] ?? []; }
function countEntries(layerArr, gridno) { return entriesAt(layerArr, gridno).length; }
function hasSlotSub(layerArr, gridno, slot, sub) {
  return entriesAt(layerArr, gridno).some((e) => e[0] === slot && (sub === undefined || e[1] === sub));
}
function gridnoOf(cols, x, y) { return y * cols + x; }

async function pollUntil(fn, { timeout = 6000, interval = 250 } = {}) {
  const t0 = Date.now();
  for (;;) {
    const v = await fn();
    if (v) return v;
    if (Date.now() - t0 > timeout) return null;
    await new Promise((r) => setTimeout(r, interval));
  }
}

// ─── Page-level helpers (same shapes as agenda_placement_p1.mjs) ───
async function tileToScreen(page, x, y) {
  return page.evaluate(([tx, ty]) => window.__mapforgeDemo?.tileToScreen(tx, ty), [x, y]);
}
const VIEWPORT = { width: 1920, height: 1080 };
async function panTo(page, x, y, ms = 0) {
  for (let attempt = 0; attempt < 3; attempt++) {
    await page.evaluate(([tx, ty, m]) => window.__mapforgeDemo?.panTo(tx, ty, m), [x, y, ms]);
    await page.waitForTimeout(ms + 400);
    const p = await tileToScreen(page, x, y);
    const okX = p && p.x > VIEWPORT.width * 0.15 && p.x < VIEWPORT.width * 0.85;
    const okY = p && p.y > VIEWPORT.height * 0.15 && p.y < VIEWPORT.height * 0.85;
    if (okX && okY) return;
    if (process.env.P34_DEBUG) console.log(`  [dbg] panTo(${x},${y}) attempt ${attempt} landed off-center:`, p);
  }
  throw new Error(`panTo(${x},${y}) never centered the tile after 3 attempts`);
}
async function hoverTile(page, x, y, dyPx = 0) {
  const p = await tileToScreen(page, x, y);
  if (!p) throw new Error(`tileToScreen(${x},${y}) returned null`);
  const target = { x: p.x, y: p.y + dyPx };
  await page.mouse.move(target.x, target.y, { steps: 3 });
  await page.waitForTimeout(150);
  return target;
}
async function clickTile(page, x, y, { shift = false, dyPx = 0 } = {}) {
  const p = await hoverTile(page, x, y, dyPx);
  if (shift) await page.keyboard.down("Shift");
  await page.mouse.down();
  await page.waitForTimeout(80);
  await page.mouse.up();
  await page.waitForTimeout(150);
  if (shift) await page.keyboard.up("Shift");
  return p;
}
/** Real left-drag from tile a to tile b -- mousedown at a, move (steps) to
 * b, mouseup at b. Used for the shape-tool rect-fill and the fence
 * line-drag (both commit ON MOUSEUP, not on a synthetic click). */
async function dragTile(page, a, b, { dyPx = 0 } = {}) {
  const pa = await hoverTile(page, a.x, a.y, dyPx);
  await page.mouse.down();
  await page.waitForTimeout(80);
  const pb = await tileToScreen(page, b.x, b.y);
  await page.mouse.move(pb.x, pb.y + dyPx, { steps: 6 });
  await page.waitForTimeout(150);
  await page.mouse.up();
  await page.waitForTimeout(200);
  return { pa, pb };
}
async function statusText(page) {
  return page.evaluate(() => document.querySelector('[data-status="placement"]')?.textContent ?? null);
}
function normStatus(t) { return (t ?? "").replace(/^[\s·]+/, ""); }
async function verdictExists(page, tier) {
  return page.evaluate((t) => !!document.querySelector(`svg path[data-verdict="${t}"]`), tier);
}
/** Count the number of tile-diamonds drawn in a data-verdict path -- each
 * tile is its own "M...z" subpath, so the number of "M" tokens = number
 * of red (or ok/advisory) tiles currently shown (trick,
 * reused here to confirm "own tile + offending tile" = 2 red diamonds
 * without needing to know exact on-screen pixel positions). */
async function verdictTileCount(page, tier) {
  return page.evaluate((t) => {
    const el = document.querySelector(`svg path[data-verdict="${t}"]`);
    if (!el) return 0;
    const d = el.getAttribute("d") ?? "";
    return (d.match(/M/g) ?? []).length;
  }, tier);
}
async function queuedPathExists(page) {
  return page.evaluate(() => !!document.querySelector('svg path[data-queued="tiles"]'));
}
async function ghostCanvasDisplay(page) {
  return page.evaluate(() => document.querySelectorAll("canvas.z-20")[0]?.style.display ?? null);
}
async function cardCellIds(page) {
  return page.evaluate(() => [...document.querySelectorAll("[data-card] [data-card-cell]")]
    .map((el) => el.getAttribute("data-card-cell")));
}
async function clickCardCell(page, id) {
  await page.locator(`[data-card-cell="${id}"]`).first().click();
  await page.waitForTimeout(150);
}
async function brushChipTitle(page) {
  return page.evaluate(() => {
    const el = [...document.querySelectorAll("[title^='Active brush:']")][0];
    return el ? el.getAttribute("title") : null;
  });
}
async function groupsRowCount(page) {
  return page.evaluate(() => {
    const label = [...document.querySelectorAll("span")].find((s) => s.textContent?.trim().startsWith("Groups"));
    const ul = label?.parentElement?.querySelector("ul");
    return ul ? ul.querySelectorAll("li").length : 0;
  });
}
/** The stored value is `Record<bucketKey, ControlGroup[]>` (per
 * xmlPath+tileset, see lib/controlGroups.ts readGroups/writeGroups) --
 * this session only ever touches one bucket, so return ITS array. */
async function controlGroupsArray(page) {
  return page.evaluate((key) => {
    const raw = localStorage.getItem(key);
    if (!raw) return null;
    const rec = JSON.parse(raw);
    const buckets = Object.values(rec ?? {});
    return buckets.length > 0 ? buckets[0] : null;
  }, GROUPS_KEY);
}
async function canvasPngDataUrl(page, idx) {
  return page.evaluate((i) => {
    const cv = document.querySelectorAll("canvas.z-20")[i];
    if (!cv) return { error: "no such canvas.z-20" };
    try { return { dataUrl: cv.toDataURL("image/png"), display: cv.style.display, w: cv.width, h: cv.height }; }
    catch (e) { return { error: String(e && e.message || e), display: cv.style.display }; }
  }, idx);
}
function writeDataUrlPng(dataUrl, outPath) {
  const b64 = dataUrl.replace(/^data:image\/png;base64,/, "");
  fs.writeFileSync(outPath, Buffer.from(b64, "base64"));
  console.log(`  png -> ${outPath}`);
}
async function shot(page, name, { status } = {}) {
  const outPath = path.join(SHOTS, name);
  try {
    await page.screenshot({ path: outPath, fullPage: true, timeout: 60000 });
    console.log(`  screenshot -> ${outPath}`);
    evidence.push({ file: name, status: status ?? await statusText(page) ?? null, result: "PASS" });
  } catch (e) {
    console.log(`  SKIP screenshot ${name}: ${e.message}`);
    evidence.push({ file: name, status: status ?? null, result: `FAIL: ${e.message}` });
  }
}

/** Type into the Brush Box search box and click the first matching
 * thumbnail; if that opens the inline sub-frame picker, click sub 1. */
async function armPaletteItem(page, term, filenamePrefix) {
  const search = page.locator('input[type="search"]').first();
  await search.click();
  await search.fill("");
  await search.fill(term);
  await page.waitForTimeout(400);
  const thumb = page.locator(`button[title^="${filenamePrefix}"]`).first();
  await thumb.waitFor({ state: "visible", timeout: 8000 });
  await thumb.click();
  await page.waitForTimeout(300);
  const subBtn = page.locator('button[title="Pick sub 1"]').first();
  if (await subBtn.count() > 0) {
    await subBtn.click();
    await page.waitForTimeout(250);
  }
}

/** Click-select a single sprite near `anchor`, sweeping candidate tiles
 * and vertical pixel offsets -- an iso sprite draws upward from its
 * tile's south apex (agenda_placement_p1.mjs finding #5, reused
 * verbatim). */
async function selectSpriteNear(page, anchor, { wantSelected = 1 } = {}) {
  const candidates = [anchor,
    { x: anchor.x - 2, y: anchor.y }, { x: anchor.x - 3, y: anchor.y - 1 },
    { x: anchor.x - 1, y: anchor.y }, { x: anchor.x - 4, y: anchor.y },
    { x: anchor.x + 1, y: anchor.y }, { x: anchor.x, y: anchor.y - 1 }];
  for (const c of candidates) {
    for (const dyPx of [-10, -20, -30, -40, -50, 0]) {
      await clickTile(page, c.x, c.y, { dyPx });
      const t = normStatus(await statusText(page));
      if (t.includes(`${wantSelected} selected`)) return t;
      await page.keyboard.press("Escape"); await page.waitForTimeout(100);
    }
  }
  return "";
}

/** Find a rectangular strip of `want` contiguous empty tiles (structs +
 * objs both empty) starting at (x0,y0) going east, verified live against
 * /parsed (not assumed from the earlier recon dump). */
async function findClearRun(sessionId, x0, y0, want) {
  const parsed = await getParsed(sessionId);
  const isEmpty = (x, y) => {
    const g = gridnoOf(parsed.cols, x, y);
    return (parsed.structs[g]?.length ?? 0) === 0 && (parsed.objs[g]?.length ?? 0) === 0;
  };
  for (let x = x0; x < x0 + want; x++) if (!isEmpty(x, y0)) return null;
  return { x: x0, y: y0 };
}

// ─── Main ───────────────────────────────────────────────────────────
const url = `${BASE}/mapforge/sector?dat=${encodeURIComponent(LAB_DAT)}`
  + `&xml=${encodeURIComponent(XML_PATH)}&tileset=${TILESET}&demo=1`;
console.log("open:", url);

const browser = await chromium.launch({
  headless: true,
  args: ["--disable-web-security", "--disable-features=IsolateOrigins,site-per-process"],
});
const page = await browser.newPage({ viewport: { width: 1920, height: 1080 } });
page.on("pageerror", (e) => console.error("PAGE ERROR:", e.message));
if (process.env.P34_DEBUG) {
  page.on("console", (m) => console.log("CONSOLE:", m.type(), m.text()));
  page.on("response", (r) => { if (!r.ok() && r.url().includes("8773")) console.log("HTTP", r.status(), r.url()); });
}

let sessionId = null;

try {
  await page.goto(url, { waitUntil: "domcontentloaded" });

  await step("0. editor ready", async () => {
    await page.waitForFunction(
      () => window.__mapforgeDemo?.getState?.().ready === true,
      null, { timeout: 90000, polling: 500 },
    );
    await page.waitForTimeout(12000);
    record("0. editor ready", true);
  });

  sessionId = await findSession();
  console.log("sidecar session:", sessionId);
  let parsed = await getParsed(sessionId);
  console.log(`sector ${parsed.cols}x${parsed.rows}`);

  // ══════════════════════════════════════════════════════════════════
  // (a) Control groups
  // ══════════════════════════════════════════════════════════════════
  await step("a1. click-select the existing fence (77) and Ctrl+1 saves it as a group", async () => {
    await panTo(page, FENCE77.x, FENCE77.y, 0);
    const t = await selectSpriteNear(page, FENCE77);
    record("a1. click-select the existing fence -> '1 selected'", t.includes("1 selected"), JSON.stringify(t));

    const urlBefore = page.url();
    await page.keyboard.press("Control+1");
    await page.waitForTimeout(200);
    record("a2. page did not navigate (Ctrl+1 intercepted, no browser tab-switch)", page.url() === urlBefore, page.url());
    const groups1 = await pollUntil(async () => {
      const arr = await controlGroupsArray(page);
      return (arr && arr[0]) ? arr : null;
    }, { timeout: 3000 });
    record("a3. group slot 0 now holds a saved group (localStorage)", !!groups1, JSON.stringify(groups1?.[0]));

    await page.keyboard.press("Escape"); await page.waitForTimeout(150);
    await page.keyboard.press("1");
    const armed = await pollUntil(async () => (await statusText(page))?.includes("place") ? true : null, { timeout: 2000 });
    record("a4. press '1' recalls the group -> a ghost is armed (status contains 'place')", !!armed, JSON.stringify(await statusText(page)));
    await page.keyboard.press("Escape"); await page.waitForTimeout(150);
  });

  await step("a5. arm a land brush (dirt1), Ctrl+2 saves it, Escape disarms, '2' re-arms it", async () => {
    await armPaletteItem(page, "dirt1", "dirt1.sti");
    const chip0 = await brushChipTitle(page);
    record("a5. dirt1 armed as a brush (chip shows it)", !!chip0 && /dirt1/i.test(chip0), JSON.stringify(chip0));

    await page.keyboard.press("Control+2");
    await page.waitForTimeout(200);
    const groups2 = await pollUntil(async () => {
      const arr = await controlGroupsArray(page);
      return (arr && arr[1]?.kind === "brush") ? arr : null;
    }, { timeout: 3000 });
    record("a6. group slot 1 now holds the saved brush", !!groups2, JSON.stringify(groups2?.[1]));

    await page.keyboard.press("Escape"); await page.waitForTimeout(150);
    const chipAfterEscape = await brushChipTitle(page);
    record("a7. Escape disarmed the brush (chip no longer shows an active brush)", !chipAfterEscape, JSON.stringify(chipAfterEscape));

    await page.keyboard.press("2");
    await page.waitForTimeout(200);
    const chip1 = await brushChipTitle(page);
    record("a8. press '2' re-arms the brush (chip shows dirt1 again)", !!chip1 && /dirt1/i.test(chip1), JSON.stringify(chip1));
  });

  await step("a9. Groups row shows exactly the 2 saved groups", async () => {
    const n = await groupsRowCount(page);
    record("a9. Groups row count === 2", n === 2, `count=${n}`);
    await shot(page, "p34_groups_row_v101.png", { status: `groups row count=${n}` });
  });

  // ══════════════════════════════════════════════════════════════════
  // (b) Command card
  // ══════════════════════════════════════════════════════════════════
  await step("b1. selection card = 6 cells with the spec's ids", async () => {
    await page.keyboard.press("Escape"); await page.waitForTimeout(150);
    const t = await selectSpriteNear(page, FENCE77);
    if (!t.includes("1 selected")) { record("b1. re-select the fence", false, JSON.stringify(t)); return; }
    const ids = await cardCellIds(page);
    const want = ["sel-copy", "sel-cut", "sel-delete", "sel-cycle-next", "nudge", "save-group"];
    record("b1. selection card cells === 6 with the spec ids", ids.length === 6 && want.every((w) => ids.includes(w)), JSON.stringify(ids));
    await page.keyboard.press("Escape"); await page.waitForTimeout(150);
  });

  await step("b2. ghost-armed card = 4 cells with the spec's ids", async () => {
    await armPaletteItem(page, FENCE89.term, FENCE89.filename);
    const armed = await pollUntil(async () => (await statusText(page))?.includes("place") ? true : null, { timeout: 2000 });
    if (!armed) { record("b2. arm a ghost first", false); return; }
    const ids = await cardCellIds(page);
    const want = ["place", "queue", "sel-cycle-next", "cancel"];
    record("b2. ghost card cells === 4 with the spec ids", ids.length === 4 && want.every((w) => ids.includes(w)), JSON.stringify(ids));
    await page.keyboard.press("Escape"); await page.waitForTimeout(150);
  });

  await step("b3. brush-armed card = 5 cells with the spec's ids; Rect + a 3x3 drag writes 9 land entries", async () => {
    await armPaletteItem(page, "dirt1", "dirt1.sti");
    const chip = await brushChipTitle(page);
    if (!chip) { record("b3. arm the dirt1 brush first", false); return; }
    const ids = await cardCellIds(page);
    const want = ["shape-rect", "shape-line", "shape-flood", "radius", "cancel"];
    record("b3a. brush card cells === 5 with the spec ids", ids.length === 5 && want.every((w) => ids.includes(w)), JSON.stringify(ids));
    await shot(page, "p34_card_brush_v101.png", { status: `brush card ids=${JSON.stringify(ids)}` });

    // Slot/sub actually armed (verify from the DOM title rather than
    // assuming "sub 1" -- armPaletteItem's sub-picker click already
    // resolved which one).
    const slotSubMatch = /slot (\d+)\D+sub (\d+)/.exec(chip ?? "");
    const dirtSlot = slotSubMatch ? Number(slotSubMatch[1]) : 0;
    const dirtSub = slotSubMatch ? Number(slotSubMatch[2]) : 1;

    // A random 3x3 patch near wherever the camera already is (fence77's
    // area) -- LAND painting has no occupancy gate, so it doesn't need
    // to be clear of structs/objs, just needs a known BEFORE snapshot.
    const rx0 = FENCE77.x + 9, ry0 = FENCE77.y - 6;
    const before = await getParsed(sessionId);
    await clickCardCell(page, "shape-rect");
    const { pa } = await dragTile(page, { x: rx0, y: ry0 }, { x: rx0 + 2, y: ry0 + 2 });
    void pa;
    const after = await pollUntil(async () => {
      parsed = await getParsed(sessionId);
      for (let dy = 0; dy < 3; dy++) for (let dx = 0; dx < 3; dx++) {
        const g = gridnoOf(parsed.cols, rx0 + dx, ry0 + dy);
        if (!hasSlotSub(parsed.land, g, dirtSlot, dirtSub)) return null;
      }
      return parsed;
    }, { timeout: 6000 });
    let changed = 0;
    if (after) {
      for (let dy = 0; dy < 3; dy++) for (let dx = 0; dx < 3; dx++) {
        const g = gridnoOf(before.cols, rx0 + dx, ry0 + dy);
        if (hasSlotSub(after.land, g, dirtSlot, dirtSub)) changed++;
      }
    }
    record("b3b. 3x3 rect-fill wrote 9 land entries (slot/sub match the armed brush)", changed === 9, `changed=${changed}/9`);
  });

  // ══════════════════════════════════════════════════════════════════
  // (c) R cycles a single selected fence to the next VALID sub (slot 89)
  // ══════════════════════════════════════════════════════════════════
  await step("c. R cycles a selected fence (slot 89) to the next valid sub", async () => {
    await page.keyboard.press("Escape"); await page.waitForTimeout(150);
    const manifest = await sidecarJson(`/tileset/atlas-manifest?xml=${encodeURIComponent(XML_PATH)}&tileset=${TILESET}`);
    const subs89 = [...new Set(manifest.cells.filter((c) => c.slot === FENCE89.slot).map((c) => c.sub))].sort((a, b) => a - b);
    record("c0. slot 89's real sub list fetched (non-empty)", subs89.length > 0, JSON.stringify(subs89.slice(0, 6)) + (subs89.length > 6 ? "..." : ""));

    const spot = { x: BLOCK_D.x0 + 2, y: BLOCK_D.y0 + 2 };
    await panTo(page, BLOCK_D.x0 + 5, BLOCK_D.y0 + 5, 0);
    await armPaletteItem(page, FENCE89.term, FENCE89.filename);
    await clickTile(page, spot.x, spot.y);
    const g = gridnoOf(parsed.cols, spot.x, spot.y);
    const placed = await pollUntil(async () => {
      parsed = await getParsed(sessionId);
      const e = (parsed.structs[g] ?? []).find((x) => x[0] === FENCE89.slot);
      return e ? e : null;
    }, { timeout: 5000 });
    if (!placed) { record("c1. placed a fence-89 to select", false); return; }
    const beforeSub = placed[1];

    const selText = await selectSpriteNear(page, spot);
    if (!selText.includes("1 selected")) { record("c2. select the placed fence", false, JSON.stringify(selText)); return; }

    await page.keyboard.press("r");
    const afterEntry = await pollUntil(async () => {
      parsed = await getParsed(sessionId);
      const e = (parsed.structs[g] ?? []).find((x) => x[0] === FENCE89.slot);
      return (e && e[1] !== beforeSub) ? e : null;
    }, { timeout: 4000 });
    const afterSub = afterEntry ? afterEntry[1] : beforeSub;
    const idx = subs89.indexOf(beforeSub);
    const expected = idx >= 0 ? subs89[(idx + 1) % subs89.length] : null;
    record("c3. R advances to the NEXT sub in the real (atlas-manifest) sub list", afterSub === expected,
      `before=${beforeSub} after=${afterSub} expected=${expected}`);
    await page.keyboard.press("Escape"); await page.waitForTimeout(150);
  });

  // ══════════════════════════════════════════════════════════════════
  // (d) Fence line-drag -- slot 86, ns=7 ew=6 corners=2
  // ══════════════════════════════════════════════════════════════════
  const fenceTable = await sidecarJson(`/placement/tables?tileset=${TILESET}`);
  const t86 = fenceTable.fences?.["86"];
  console.log("  fence table for slot 86:", JSON.stringify(t86));

  await step("d0. seed one wirefenc (86) tile via brush-paint, then copy/paste it as a GHOST (fenceArmed needs placingBuilding.group)", async () => {
    await panTo(page, BLOCK_B.x0 + 6, BLOCK_B.y0 + 1, 0);
    const seed = { x: BLOCK_B.x0, y: BLOCK_B.y0 }; // off to the side of the drawn lines below
    await armPaletteItem(page, FENCE86.term, FENCE86.filename);
    await clickTile(page, seed.x, seed.y);
    const g = gridnoOf(parsed.cols, seed.x, seed.y);
    const seeded = await pollUntil(async () => {
      parsed = await getParsed(sessionId);
      return (parsed.structs[g] ?? []).some((e) => e[0] === FENCE86.slot) ? true : null;
    }, { timeout: 5000 });
    record("d0a. seed fence(86) painted onto structs (brush, not ghost, per family=wall)", !!seeded);
    await page.keyboard.press("Escape"); await page.waitForTimeout(150);

    const selText = await selectSpriteNear(page, seed);
    if (!selText.includes("1 selected")) { record("d0b. select the seed fence", false, JSON.stringify(selText)); return; }
    await page.keyboard.press("Control+c");
    await page.waitForTimeout(200);
    await page.keyboard.press("Escape"); await page.waitForTimeout(200);
    await page.keyboard.press("Control+v");
    const armed = await pollUntil(async () => (await statusText(page)) !== null ? true : null, { timeout: 2000 });
    record("d0c. Ctrl+V arms a GHOST of the copied fence (fenceArmed reachable)", !!armed, JSON.stringify(await statusText(page)));
  });

  await step("d1. straight 6-tile horizontal line -> all 6 entries get sub=ew", async () => {
    const y = BLOCK_B.y0 + 3;
    const a = { x: BLOCK_B.x0 + 1, y }, b = { x: BLOCK_B.x0 + 6, y };
    await dragTile(page, a, b);
    const gs = [];
    for (let x = a.x; x <= b.x; x++) gs.push(gridnoOf(parsed.cols, x, y));
    const got = await pollUntil(async () => {
      parsed = await getParsed(sessionId);
      return gs.every((g) => hasSlotSub(parsed.structs, g, FENCE86.slot)) ? parsed : null;
    }, { timeout: 6000 });
    const subs = gs.map((g) => (entriesAt((got ?? parsed).structs, g).find((e) => e[0] === FENCE86.slot) ?? [null, null])[1]);
    record("d1. 6 entries, all sub=ew", got && subs.every((s) => s === t86.ew), `subs=${JSON.stringify(subs)} expected ew=${t86.ew}`);
  });

  await step("d2. L-drag (east 3, then south 3 from the same armed fence) -> corner=nw/ne/sw/se, runs=ew/ns", async () => {
    const still = await pollUntil(async () => (await statusText(page)) !== null ? true : null, { timeout: 1500 });
    record("d2pre. fence still armed after the first line commit (no re-arm needed)", !!still, JSON.stringify(await statusText(page)));

    const y0 = BLOCK_B.y0 + 6;
    const corner = { x: BLOCK_B.x0 + 3, y: y0 };
    const eastEnd = { x: BLOCK_B.x0, y: y0 };   // drag1: west end -> corner (3 east)
    await dragTile(page, eastEnd, corner);
    const southEnd = { x: corner.x, y: corner.y + 3 };
    await dragTile(page, corner, southEnd); // drag2 starts AT the corner tile

    const gCorner = gridnoOf(parsed.cols, corner.x, corner.y);
    const gEastRun = gridnoOf(parsed.cols, eastEnd.x, y0);       // pure horizontal run tile
    const gSouthRun = gridnoOf(parsed.cols, corner.x, corner.y + 2); // pure vertical run tile
    const got = await pollUntil(async () => {
      parsed = await getParsed(sessionId);
      return [gCorner, gEastRun, gSouthRun].every((g) => hasSlotSub(parsed.structs, g, FENCE86.slot)) ? parsed : null;
    }, { timeout: 6000 });
    const subOf = (g) => (entriesAt((got ?? parsed).structs, g).find((e) => e[0] === FENCE86.slot) ?? [null, null])[1];
    const cornerSub = subOf(gCorner), eastSub = subOf(gEastRun), southSub = subOf(gSouthRun);
    const expectCorner = [t86.nw, t86.ne, t86.sw, t86.se]; // all identical by design (S8 note) -- accept any
    record("d2a. corner tile got a corner sub (nw/ne/sw/se family value)", expectCorner.includes(cornerSub), `corner=${cornerSub} expected one of ${JSON.stringify(expectCorner)}`);
    record("d2b. horizontal run tile stayed ew", eastSub === t86.ew, `east=${eastSub} expected ew=${t86.ew}`);
    record("d2c. vertical run tile is ns", southSub === t86.ns, `south=${southSub} expected ns=${t86.ns}`);
    await shot(page, "p34_fence_line_v101.png", { status: `corner=${cornerSub} east=${eastSub} south=${southSub}` });
  });

  await step("d3. a line across a road tile leaves that tile empty", async () => {
    // Re-arm: the L-drag's second commit disarms same as any group commit
    // once the drag's END tile isn't itself fence-adjacent -- re-select
    // the corner fence, copy/paste a fresh single-tile ghost for this
    // probe (cheaper + more certain than assuming arm state survived).
    await page.keyboard.press("Escape"); await page.waitForTimeout(150);
    const cornerTile = { x: BLOCK_B.x0 + 3, y: BLOCK_B.y0 + 6 };
    const selText = await selectSpriteNear(page, cornerTile);
    if (!selText.includes("1 selected")) { record("d3pre. select a placed fence(86) to re-clone", false, JSON.stringify(selText)); return; }
    await page.keyboard.press("Control+c");
    await page.waitForTimeout(200);
    await page.keyboard.press("Escape"); await page.waitForTimeout(200);

    // Paste a copy of the EXISTING road (objs) tile into a clean row here
    // first (D3: Ctrl+C scope includes objs) -- a fresh Brush Box arm of
    // slot 50 lands on "land", invisible to the ROAD gate (see header).
    await panTo(page, ROAD_SRC.x, ROAD_SRC.y, 0);
    const roadSelText = await selectSpriteNear(page, ROAD_SRC);
    if (!roadSelText.includes("1 selected")) { record("d3a. select an existing road tile", false, JSON.stringify(roadSelText)); return; }
    await page.keyboard.press("Control+c");
    await page.waitForTimeout(200);
    await page.keyboard.press("Escape"); await page.waitForTimeout(200);

    const y = BLOCK_B.y0 + 9;
    const roadTile = { x: BLOCK_B.x0 + 2, y };
    await panTo(page, BLOCK_B.x0 + 5, y, 0);
    await page.keyboard.press("Control+v");
    const armedRoad = await pollUntil(async () => (await statusText(page)) !== null ? true : null, { timeout: 2000 });
    if (!armedRoad) { record("d3b. Ctrl+V arms the copied road tile", false); return; }
    await clickTile(page, roadTile.x, roadTile.y);
    const gRoad = gridnoOf(parsed.cols, roadTile.x, roadTile.y);
    const roadPlaced = await pollUntil(async () => {
      parsed = await getParsed(sessionId);
      return (parsed.objs[gRoad] ?? []).some((e) => e[0] === ROAD_SRC.slot) ? true : null;
    }, { timeout: 5000 });
    record("d3c. a road(50) objs entry now sits at the probe tile", !!roadPlaced);

    // Now the fence ghost: Ctrl+V again re-arms the LAST clipboard, which
    // is now the road copy, not the fence -- re-copy the corner fence.
    await panTo(page, BLOCK_B.x0 + 3, BLOCK_B.y0 + 6, 0);
    const selText2 = await selectSpriteNear(page, cornerTile);
    if (!selText2.includes("1 selected")) { record("d3d. re-select the corner fence", false, JSON.stringify(selText2)); return; }
    await page.keyboard.press("Control+c");
    await page.waitForTimeout(200);
    await page.keyboard.press("Escape"); await page.waitForTimeout(200);
    await panTo(page, BLOCK_B.x0 + 5, y, 0);
    await page.keyboard.press("Control+v");
    const armedFence = await pollUntil(async () => (await statusText(page)) !== null ? true : null, { timeout: 2000 });
    if (!armedFence) { record("d3e. Ctrl+V re-arms the fence ghost", false); return; }

    const lineA = { x: BLOCK_B.x0 + 1, y }, lineB = { x: BLOCK_B.x0 + 3, y };
    await dragTile(page, lineA, lineB); // 3-tile line, roadTile is the middle
    const gA = gridnoOf(parsed.cols, lineA.x, y), gB = gridnoOf(parsed.cols, lineB.x, y);
    const got = await pollUntil(async () => {
      parsed = await getParsed(sessionId);
      return (hasSlotSub(parsed.structs, gA, FENCE86.slot) && hasSlotSub(parsed.structs, gB, FENCE86.slot)) ? parsed : null;
    }, { timeout: 6000 });
    parsed = got ?? parsed;
    const roadStillEmpty = !hasSlotSub(parsed.structs, gRoad, FENCE86.slot);
    record("d3f. the road tile got NO fence entry (ROAD gate skipped it)", roadStillEmpty,
      `road tile structs=${JSON.stringify(entriesAt(parsed.structs, gRoad))}`);
    record("d3g. the two flanking tiles DID get a fence entry", !!got, `A=${countEntries(parsed.structs, gA)} B=${countEntries(parsed.structs, gB)}`);
    await page.keyboard.press("Escape"); await page.waitForTimeout(150);
  });

  // ══════════════════════════════════════════════════════════════════
  // RING screenshot near the real truck (fence's OWN tile + offending
  // tile both red, per the F5 addendum)
  // ══════════════════════════════════════════════════════════════════
  await step("ring. fence ghost near the existing truck -> RING, own tile + offending tile both red", async () => {
    await panTo(page, TRUCK.x, TRUCK.y - 2, 0);
    await armPaletteItem(page, FENCE89.term, FENCE89.filename);
    await hoverTile(page, TRUCK.x, TRUCK.y - 2); // (213,157) -- 8-neighbours the truck's footprint
    const blocked = await pollUntil(async () => (await verdictExists(page, "blocking")) ? true : null, { timeout: 3000 });
    const t = normStatus(await statusText(page));
    record("ring1. RING fires for a fence candidate near the truck (symmetric rule)", !!blocked, JSON.stringify(t));
    record("ring2. status names RING", t.includes("RING"), JSON.stringify(t));
    const tileCount = await verdictTileCount(page, "blocking");
    record("ring3. TWO tiles painted red (the ghost's own footprint + the offending tile)", tileCount === 2, `tileCount=${tileCount}`);
    await shot(page, "p34_fence_near_truck_red_v102.png", { status: t });
    await page.keyboard.press("Escape"); await page.waitForTimeout(150);
  });

  // ══════════════════════════════════════════════════════════════════
  // (e) Placement queue -- 3 queued trucks, 4th commits
  // ══════════════════════════════════════════════════════════════════
  const Q1 = { x: BLOCK_C.x0 + 11, y: BLOCK_C.y0 + 3 };
  const Q2 = { x: BLOCK_C.x0 + 11, y: BLOCK_C.y0 + 7 };
  const Q3 = { x: BLOCK_C.x0 + 11, y: BLOCK_C.y0 + 11 };
  const Q4 = { x: BLOCK_C.x0 + 5, y: BLOCK_C.y0 + 3 };

  await step("e0. Ctrl+C the existing truck, travel to a clean block, Ctrl+V arms it as a ghost", async () => {
    await panTo(page, TRUCK.x, TRUCK.y, 0);
    const t = await selectSpriteNear(page, TRUCK);
    if (!t.includes("1 selected")) { record("e0a. select the existing truck", false, JSON.stringify(t)); return; }
    await page.keyboard.press("Control+c");
    await page.waitForTimeout(200);
    await page.keyboard.press("Escape"); await page.waitForTimeout(200);
    await panTo(page, BLOCK_C.x0 + 8, BLOCK_C.y0 + 7, 0);
    await page.keyboard.press("Control+v");
    const armed = await pollUntil(async () => (await statusText(page)) !== null ? true : null, { timeout: 2000 });
    record("e0b. Ctrl+V arms a truck ghost (structs layer, oracle-visible)", !!armed, JSON.stringify(await statusText(page)));
  });

  await step("e1. Shift+click 3 well-separated spots -> queued, nothing in /parsed yet", async () => {
    for (const [i, q] of [Q1, Q2, Q3].entries()) {
      await clickTile(page, q.x, q.y, { shift: true });
      const qp = await pollUntil(async () => (await queuedPathExists(page)) ? true : null, { timeout: 3000 });
      record(`e1.${i + 1}. Shift+click at (${q.x},${q.y}) enqueues`, !!qp);
    }
    const t = normStatus(await statusText(page));
    record("e1b. status shows '3 queued'", t.includes("3 queued"), JSON.stringify(t));
    parsed = await getParsed(sessionId);
    const none = [Q1, Q2, Q3].every((q) => !hasSlotSub(parsed.structs, gridnoOf(parsed.cols, q.x, q.y), TRUCK.slot, TRUCK.sub));
    record("e1c. /parsed unchanged at all three queued anchors", none);
    await shot(page, "p34_queue_v101.png", { status: t });
  });

  await step("e2. plain click a 4th spot -> all four commit as one stroke; ONE Ctrl+Z removes all four", async () => {
    await clickTile(page, Q4.x, Q4.y);
    const gs = [Q1, Q2, Q3, Q4].map((q) => gridnoOf(parsed.cols, q.x, q.y));
    const got = await pollUntil(async () => {
      parsed = await getParsed(sessionId);
      return gs.every((g) => hasSlotSub(parsed.structs, g, TRUCK.slot, TRUCK.sub)) ? parsed : null;
    }, { timeout: 6000 });
    record("e2a. four trucks now in /parsed", !!got, `counts=${JSON.stringify(gs.map((g) => countEntries((got ?? parsed).structs, g)))}`);

    await page.keyboard.press("Control+z");
    const gone = await pollUntil(async () => {
      parsed = await getParsed(sessionId);
      return gs.every((g) => !hasSlotSub(parsed.structs, g, TRUCK.slot, TRUCK.sub)) ? true : null;
    }, { timeout: 6000 });
    record("e2b. ONE Ctrl+Z removes all four (one stroke)", !!gone, `counts=${JSON.stringify(gs.map((g) => countEntries(parsed.structs, g)))}`);
  });

  // ══════════════════════════════════════════════════════════════════
  // (f) legacy untouched
  // ══════════════════════════════════════════════════════════════════
  await step("f. legacyTools:true keeps the old tool bar working; restore false", async () => {
    await page.evaluate((key) => {
      const raw = localStorage.getItem(key);
      const s = raw ? JSON.parse(raw) : {};
      s.legacyTools = true;
      localStorage.setItem(key, JSON.stringify(s));
    }, SETTINGS_KEY);
    await page.reload({ waitUntil: "domcontentloaded" });
    await page.waitForFunction(() => window.__mapforgeDemo?.getState?.().ready === true, null, { timeout: 90000, polling: 500 });
    await page.waitForTimeout(8000);

    const barButtons = ["Inspect", "Pencil", "Shape", "Select"];
    const counts = {};
    for (const b of barButtons) counts[b] = await page.locator(`button:has-text("${b}")`).count();
    record("f1. Inspect/Pencil/Shape/Select bar all visible with legacyTools:true", barButtons.every((b) => counts[b] > 0), JSON.stringify(counts));

    sessionId = await findSession();
    await panTo(page, BLOCK_D.x0 + 8, BLOCK_D.y0 + 8, 0);
    // The legacy Pencil button is disabled ("Pick a tile from the palette
    // first") until a brush is armed -- arm dirt2 BEFORE clicking Pencil.
    await armPaletteItem(page, "dirt2", "dirt2.sti");
    await page.locator('button:has-text("Pencil")').first().click();
    await page.waitForTimeout(200);
    const spot = { x: BLOCK_D.x0 + 8, y: BLOCK_D.y0 + 8 };
    await clickTile(page, spot.x, spot.y);
    const g = gridnoOf(parsed.cols, spot.x, spot.y);
    const painted = await pollUntil(async () => {
      const p = await getParsed(sessionId);
      parsed = p;
      return hasSlotSub(p.land, g, 1) ? true : null; // dirt2.sti = slot 1 (dirt1=0, dirt2=1, ...)
    }, { timeout: 5000 });
    record("f2. legacy Pencil + a picked brush still paints a tile", !!painted, JSON.stringify(entriesAt(parsed.land, g)));

    await page.evaluate((key) => {
      const raw = localStorage.getItem(key);
      const s = raw ? JSON.parse(raw) : {};
      s.legacyTools = false;
      localStorage.setItem(key, JSON.stringify(s));
    }, SETTINGS_KEY);
    await page.reload({ waitUntil: "domcontentloaded" });
    await page.waitForFunction(() => window.__mapforgeDemo?.getState?.().ready === true, null, { timeout: 90000, polling: 500 });
    const n2 = await page.locator('button:has-text("Inspect")').count();
    record("f3. legacyTools reverted to false (Inspect button gone again)", n2 === 0, `count=${n2}`);
    sessionId = await findSession();
  });
} catch (e) {
  console.error("SCRIPT FAILED:", e);
  failCount++;
} finally {
  await browser.close();
  try {
    const sessions = await sidecarJson("/sessions");
    for (const s of sessions) {
      if (norm(s.dat_path) !== norm(LAB_DAT)) continue;
      let deleted = false;
      for (let attempt = 0; attempt < 4 && !deleted; attempt++) {
        try {
          await sidecarJson(`/sessions/${encodeURIComponent(s.session_id)}?force=true`, { method: "DELETE" });
          console.log(`cleanup: force-deleted session ${s.session_id}`);
          deleted = true;
        } catch (e) {
          if (attempt < 3) await new Promise((r) => setTimeout(r, 1500));
          else console.error(`cleanup: failed to delete session ${s.session_id}:`, e.message);
        }
      }
    }
  } catch (e) {
    console.error("cleanup: failed to list sidecar sessions:", e.message);
  }
  fs.writeFileSync(path.join(SHOTS, "evidence.json"), JSON.stringify(evidence, null, 2));
  console.log(`evidence -> ${path.join(SHOTS, "evidence.json")}`);
}

console.log(`\n${results.length} checks, ${failCount} failed.`);
process.exit(failCount === 0 ? 0 : 1);
