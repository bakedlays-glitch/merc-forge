#!/usr/bin/env node
/**
 * Verification of MapForge's mode-less "StarCraft-style"
 * placement core, against THIS lane's own dev servers and the
 * gecko_town_c16_v8_placement_lab.dat scratch copy.
 *
 * WHY THIS IS A STANDALONE SCRIPT, NOT A runner.mjs AGENDA:
 * runner.mjs's fixed verb table (read in full: caption, wait, camera,
 * zoom, moveMouse, click, drag, press, clickText, activateTab, waitFor,
 * countdown, shot, done) has no `eval`, no text-`type`, no modifier-held
 * click, no fetch, and no canvas.toDataURL capture — and
 * window.__mapforgeDemo exposes only panTo/zoomTo/caption/getState/
 * tileToScreen. Every assertion this task needs (reading data-verdict /
 * data-status, hitting the sidecar's /parsed, holding Shift through a
 * click, exporting a canvas as PNG) requires one of those. Per this
 * task's own fallback clause ("if the runner truly cannot express a
 * step... do that step with a small standalone Playwright script"),
 * this file IS that script — same shape as the existing precedent
 * `verify_genui.mjs` in this directory. Run it directly with node, not
 * through runner.mjs:
 *
 *   node agenda_placement_p1.mjs [--base http://localhost:1421]
 *                                 [--sidecar http://127.0.0.1:8773]
 *                                 [--shots <dir>]
 *
 * SECOND DEVIATION FROM THE BRIEF — fence (slot 86, wirefenc.sti) does
 * NOT arm a placement ghost. Per frontend/src/lib/mapPlacement.ts
 * `armKindFor` + `BRUSH_FAMILIES` (floor/shadow/wall/door/window/roof),
 * and sidecar mercwizard_core/mapforge/tile_families.py (slot 86 =
 * FENCESTRUCT = family "wall"), picking wirefenc from the Brush Box
 * arms a drag-paint BRUSH, not a ghost — by design (walls/fences are
 * laid in runs; see the comment on armKindFor). This script instead
 * arms a "vehicle"-family item (slot 89 gecko_chainlink.sti, falling
 * back to slot 88 build_24a_rust.sti), which DOES arm a ghost and
 * lands on the "structs" layer like the brief's fence example would
 * have. Tileset 72 has no literal "truck" either; the brief's mention
 * of one was aspirational for this tileset.
 *
 * THIRD DEVIATION (SUPERSEDED — see the fix note below) —
 * occupancy-based "blocking" (hovering an armed ghost over an existing
 * building/vehicle) was UNREACHABLE when this script was first written:
 * MapForgeSector.tsx shipped `placementTables = EMPTY_TABLES` and
 * `oracleVerdicts` was a permanently-empty state (P2's sidecar oracle was
 * a later phase, not yet landed). With every `categoryOf()` lookup
 * returning null, `solidStruct` in mapPlacement.ts's `localCheck` was
 * always false, so ROOF/ROAD/TILE (occupancy) checks could never fire —
 * only BOUNDS could, and only for a MULTI-TILE footprint anchored so part
 * of it runs off the map edge (a real mouse position can never itself
 * resolve to an out-of-bounds tile — frontend/src/lib/mapforge.ts
 * imagePixelToTile returns null past the edge — so a single-tile item's
 * footprint IS its anchor and can never overflow). Every ghost-arming
 * palette item live-sampled on tileset 72 at the time (the 3
 * GHOST_CANDIDATES plus 9 more) reported a 1-tile footprint, so step 3
 * could never produce a BOUNDS hit through the Brush Box.
 *
 * FIX — step 3 now arms a genuinely multi-tile ghost instead:
 * the lab map's own PRE-EXISTING truck (gastuf1.sti, slot 79 sub 1,
 * already sitting on the structs layer — see TRUCK_SLOT/TRUCK_SUB),
 * copy/pasted via Ctrl+C/Ctrl+V rather than armed fresh from the Brush
 * Box (a fresh Brush Box arm of this slot lands on the "objs" layer per
 * agenda_placement_p2.mjs's own finding #1, invisible to the client-local
 * bounds check same as everything else). Its JSD footprint runs
 * dx in [-5,0], dy in [-1,0] relative to its anchor (west + one row
 * north) — hovering the pasted ghost's anchor near the sector's WEST edge
 * (x=3) pushes 2 footprint columns to x<0, off the map, which the local
 * BOUNDS check catches unconditionally regardless of what's shipped in
 * placementTables/oracleVerdicts. GHOST_CANDIDATES / the single-tile
 * chainlink ghost are unaffected and still used for every other step.
 *
 * FOURTH FINDING (a real app bug, worked around here, not fixed —
 * fixing it would mean editing MapForgeSector.tsx, out of this task's
 * scope): on this 360x360 bigmap, calling `window.__mapforgeDemo.panTo`
 * a SECOND time while a placement ghost is armed computes a wrong pan
 * target — reproduced deterministically with a small standalone probe
 * (arm a ghost, panTo(anyTile) twice, compare against the same calls
 * made before arming / after Escape). Before arming or after Escape,
 * panTo(76,125) reliably lands tile (76,125) at ~(920,465) in a
 * 1920x1080 viewport (dead-center, iso-rounding aside); the SAME call
 * issued while a ghost is armed lands at (-40,-976) instead — off-
 * screen — and the corruption persists (every subsequent panTo while
 * still armed keeps returning the wrong value) until the ghost is
 * disarmed, at which point the very next panTo is correct again. So this
 * script never calls panTo while a ghost is armed — full stop — but IS
 * willing to call it multiple times as long as each call happens fully
 * disarmed (verified safe empirically while building the fix
 * above, which pans to the truck's own location to select/copy it, then
 * to a clear row near the west edge to paste/hover it, then — from step
 * 3d, guarded on the live zoom so it still runs on step 3's early-exit
 * paths, not just its happy path — pans back to the original empty-tile
 * strip while STILL zoomed out (a far jump straight back at zoom 1 would
 * hit the same bug) before restoring zoom and re-arming the chainlink
 * ghost for step 4 onward — 4 total pans across the script, every one
 * issued with nothing armed).
 *
 * FIFTH FINDING — the app's Shift+click semantics
 * changed underneath this script since it was first written: Shift+click
 * on an armed ghost now ENQUEUES ("Shift keeps") instead of
 * placing-and-staying-armed. Step 5 is rewritten below to match: it
 * asserts the queue path/count instead of the old "still armed == status
 * contains 'place'" check (that check now reads "N queued ..." instead
 * and would always fail), confirms nothing lands in /parsed until a
 * plain click, and that one Ctrl+Z undoes the whole multi-tile commit.
 *
 * SIXTH FINDING — the FOURTH FINDING's panTo bug
 * above was root-caused and FIXED this task (MapForgeSector.tsx's demo
 * hook now uses fullMetaRef instead of the cropped renderMeta once the
 * bigmap detail window is active — commit 94919ee), confirmed with a
 * standalone before/after repro that never even armed a ghost (the
 * mismatch tracks the detail-window crop, not arm state — arming just
 * happened to correlate with it in the original report). This script's
 * own pan-avoidance workaround (one pan only, always fully disarmed,
 * the zoom-out dance in step 3/3d) is left AS-IS — still correct, just
 * no longer strictly required — rewriting it is out of this task's scope.
 *
 * SAFETY: only ever opens the caller's lab .dat copy (never
 * gecko_town_c16_v8.dat), never clicks Save / Ctrl+S / calls /save, and
 * force-deletes every sidecar session opened against that lab dat at
 * the end so nothing in this run is left dirty in sidecar memory.
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
const SHOTS = path.resolve(flag("--shots", path.join(__dirname, "..", "..", "..", "scratch", "p1_placement_frames")));
fs.mkdirSync(SHOTS, { recursive: true });

const INSTALL_ROOT = process.env.JA2_INSTALL
  || "C:/Games/Jagged Alliance 2/<your-1.13-install>";
const LAB_DAT = `${INSTALL_ROOT}/_gecko_qa/gecko_town_c16_v8_placement_lab.dat`;
const XML_PATH = `${INSTALL_ROOT}/Data-1.13/Ja2Set.dat.xml`;
const TILESET = 72;
const API = `${SIDECAR}/api/v1/mapforge`;
const SETTINGS_KEY = "mapforge.settings.v1"; // frontend/src/lib/mapforgeSettings.ts STORAGE_KEY

// Ghost-arming candidates in priority order — all "vehicle" family per
// the live tileset/palette API (armKindFor("vehicle") = "ghost"), unlike
// slot 86's "wall" family. Verified live against tileset 72:
//   GET /api/v1/mapforge/tileset/palette?xml=...&tileset=72
const GHOST_CANDIDATES = [
  { term: "chainlink", filename: "gecko_chainlink.sti" },
  { term: "build_24a_rust", filename: "build_24a_rust.sti" },
  { term: "vertibird_flying", filename: "fo2_vertibird_flying.sti" },
];

// The lab map's PRE-EXISTING truck (gastuf1.sti sub 1) — already on the
// structs layer (legacy content, never routed through the Brush Box) —
// used ONLY by step 3's BOUNDS probe (see header). A
// fresh Brush Box arm of this slot lands on "objs", invisible to the
// bounds/oracle checks; copy/paste of the existing instance keeps its
// current (structs) layer.
const TRUCK_SLOT = 79, TRUCK_SUB = 1;

// ─── Result tracking ────────────────────────────────────────────────
let failCount = 0;
const results = [];
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

// ─── Sidecar HTTP (Node-side — avoids any cross-origin question the
// page's own fetch to 127.0.0.1:8773 might raise) ──────────────────
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
function countEntries(layerArr, gridno) { return (layerArr[gridno] ?? []).length; }

async function pollUntil(fn, { timeout = 6000, interval = 250 } = {}) {
  const t0 = Date.now();
  for (;;) {
    const v = await fn();
    if (v) return v;
    if (Date.now() - t0 > timeout) return null;
    await new Promise((r) => setTimeout(r, interval));
  }
}

// ─── Page-level helpers ─────────────────────────────────────────────
async function tileToScreen(page, x, y) {
  return page.evaluate(([tx, ty]) => window.__mapforgeDemo?.tileToScreen(tx, ty), [x, y]);
}
const VIEWPORT = { width: 1920, height: 1080 };
/** Pan so tile (x, y) lands at the viewport center, verifying it actually
 * did (via tileToScreen) and retrying — on this 360x360 bigmap, a panTo
 * issued while a prior one's rAF-driven animation promise is still
 * settling (observed empirically: back-to-back panTo calls during the
 * BOUNDS corner probe) can resolve without moving `pan` at all, silently
 * leaving stale (and wildly off-screen) coordinates for every subsequent
 * tileToScreen call. Cheaper to verify-and-retry than to chase the exact
 * root cause in app code this task may not edit. */
async function panTo(page, x, y, ms = 0) {
  for (let attempt = 0; attempt < 3; attempt++) {
    await page.evaluate(([tx, ty, m]) => window.__mapforgeDemo?.panTo(tx, ty, m), [x, y, ms]);
    await page.waitForTimeout(ms + 150);
    // Loose bounds check, not an exact-center check: iso half-tile
    // rounding legitimately lands the "centered" tile a few dozen px off
    // dead-center. The failure mode actually observed put it thousands
    // of px off-screen (a stale/uncommitted pan), so a generous margin
    // distinguishes "fine" from "broken" without false-failing on normal
    // rounding.
    const p = await tileToScreen(page, x, y);
    const okX = p && p.x > VIEWPORT.width * 0.15 && p.x < VIEWPORT.width * 0.85;
    const okY = p && p.y > VIEWPORT.height * 0.15 && p.y < VIEWPORT.height * 0.85;
    if (okX && okY) return;
    if (process.env.P1_DEBUG) console.log(`  [dbg] panTo(${x},${y}) attempt ${attempt} landed off-center:`, p);
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
/** `dyPx`: pixel offset from the tile's geometric center. Placement
 * clicks (a ghost armed) resolve via the HOVERED TILE, not exact
 * pixels, so 0 is correct for those. A plain click-to-SELECT instead
 * needs to land on the sprite's actual drawn/opaque pixels
 * (renderer.pickSpriteAt does a precise hit test) — an iso sprite
 * draws upward from the tile's south apex, so its visible body sits
 * ABOVE the diamond's geometric center. Empirically confirmed on
 * gecko_chainlink: dy=0 misses, dy=-10 (10px up) hits. */
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
async function marqueeSelect(page, from, to) {
  const p1 = await tileToScreen(page, from[0], from[1]);
  const p2 = await tileToScreen(page, to[0], to[1]);
  await page.mouse.move(p1.x, p1.y);
  await page.waitForTimeout(100);
  await page.mouse.down();
  await page.waitForTimeout(100);
  await page.mouse.move((p1.x + p2.x) / 2, (p1.y + p2.y) / 2, { steps: 5 });
  await page.mouse.move(p2.x, p2.y, { steps: 6 });
  await page.waitForTimeout(100);
  // Hold Shift through mouseup + the trailing synthetic click: the
  // mode-less select-tool's onCanvasClick (inspectLike branch) fires a
  // sprite-pick/clear AFTER the marquee's own mouseup commits the
  // selection (both handlers live on the same <canvas>) — a plain
  // click on empty ground there would immediately wipe the multi-select
  // back to []. Shift makes that trailing click a no-op on empty ground
  // (see MapForgeSector.tsx onCanvasClick: `else if (!e.shiftKey)
  // setSelection([])`). Verified by reading the source, not guessed.
  await page.keyboard.down("Shift");
  await page.mouse.up();
  await page.waitForTimeout(150);
  await page.keyboard.up("Shift");
}
async function statusText(page) {
  return page.evaluate(() => document.querySelector('[data-status="placement"]')?.textContent ?? null);
}
function normStatus(t) { return (t ?? "").replace(/^[\s·]+/, ""); }
async function verdictExists(page, tier) {
  return page.evaluate((t) => !!document.querySelector(`svg path[data-verdict="${t}"]`), tier);
}
async function selectionPathExists(page) {
  return page.evaluate(() => !!document.querySelector('svg path[data-selection="tiles"]'));
}
async function queuedPathExists(page) {
  return page.evaluate(() => !!document.querySelector('svg path[data-queued="tiles"]'));
}
async function ghostCanvasDisplay(page) {
  return page.evaluate(() => document.querySelectorAll("canvas.z-20")[0]?.style.display ?? null);
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

/** Type into the Brush Box search box and click the first matching
 * thumbnail; if that opens the inline sub-frame picker (multi-frame,
 * single-tile slots — see SlotTile's `opensPicker`), click sub 1. */
async function armPaletteItem(page, term, filenamePrefix) {
  const search = page.locator('input[type="search"]').first();
  await search.click();
  await search.fill("");
  await search.fill(term);
  await page.waitForTimeout(400); // debounce (mapforgeSettings' filter is debounced)
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

/** Find a rectangular strip of `want` contiguous empty tiles (no
 * structs, no objs) on one row, within `maxDist` tiles of the SE
 * corner (cols-1, rows-1) — close enough that the WHOLE strip and the
 * true corner sit inside one viewport from a single pan. One pan
 * serves the entire script this way: arm + hover-ok + walk-to-corner
 * (bounds probe) all happen in the same un-panned view as the later
 * place/select/copy/paste/delete/nudge steps, so panTo is only ever
 * called once, before anything is armed — see the header's
 * panTo-while-armed finding (a second pan while armed corrupts
 * tileToScreen for the rest of the session; re-panning is not an
 * option once armed). Falls back to a center-of-map strip (no corner
 * nearby) only if nothing is found within `maxDist`, in which case the
 * bounds/blocking probe (step 3) is skipped rather than risking a
 * second pan. */
function findEmptyStrip(parsed, want = 8, maxDist = 40) {
  const { cols, rows, structs, objs } = parsed;
  const isEmpty = (x, y) => {
    const g = y * cols + x;
    return (structs[g]?.length ?? 0) === 0 && (objs[g]?.length ?? 0) === 0;
  };
  const cornerX = cols - 1, cornerY = rows - 1;
  const y0 = Math.max(0, cornerY - maxDist), x0 = Math.max(0, cornerX - maxDist);
  // Scan from a few rows OFF the true edge outward — panTo(x, rows-1)
  // (the literal last row) was observed to fail its own centering
  // check, apparently clamped so the camera can't scroll past the map
  // edge to put it dead-center. Starting 5 rows in keeps the PAN TARGET
  // comfortably centerable while the bounds-probe walk (mouse moves
  // only) can still reach the true corner from there.
  for (let y = cornerY - 5; y >= y0; y--) {
    let run = 0, startX = -1;
    for (let x = x0; x <= cornerX; x++) {
      if (isEmpty(x, y)) {
        if (run === 0) startX = x;
        run++;
        if (run >= want) return { x: startX, y, cornerX, cornerY };
      } else run = 0;
    }
  }
  // Fallback: center-of-map strip, no corner reachable from it.
  const bands = [
    [Math.floor(rows * 0.35), Math.floor(rows * 0.65), Math.floor(cols * 0.2), Math.floor(cols * 0.8)],
    [4, rows - 5, 4, cols - 5],
  ];
  for (const [by0, by1, bx0, bx1] of bands) {
    for (let y = by0; y <= by1; y++) {
      let run = 0, startX = -1;
      for (let x = bx0; x <= bx1; x++) {
        if (isEmpty(x, y)) { if (run === 0) startX = x; run++; if (run >= want) return { x: startX, y, cornerX: null, cornerY: null }; }
        else run = 0;
      }
    }
  }
  return null;
}

/** A row near the sector's WEST edge (x 0..8) with no structs/objs on
 * any of those columns, so hovering a pasted truck ghost there produces
 * BOUNDS and nothing else (no TILE/RING confusion from unrelated content
 * — the sidecar oracle has since landed, per agenda_placement_p2.mjs, so
 * occupancy-based verdicts ARE reachable now and a crowded row could mask
 * or out-race the bounds signal). Scans every 3rd row for speed. */
function findClearEdgeRow(parsed, edgeWidth = 8, maxY = 359, step = 3) {
  const isEmpty = (x, y) => {
    const g = y * parsed.cols + x;
    return (parsed.structs[g]?.length ?? 0) === 0 && (parsed.objs[g]?.length ?? 0) === 0;
  };
  for (let y = 10; y < Math.min(maxY, parsed.rows - 2); y += step) {
    let ok = true;
    for (let x = 0; x <= edgeWidth && ok; x++) if (!isEmpty(x, y)) ok = false;
    if (ok) return y;
  }
  return null;
}

/** Click-select a single sprite near `anchor`, sweeping candidate tiles
 * and vertical pixel offsets — an iso sprite draws upward from its
 * tile's south apex, so a plain click at the tile's geometric center can
 * miss it (reused here verbatim for the truck). */
async function selectSpriteNear(page, anchor) {
  const candidates = [anchor,
    { x: anchor.x - 2, y: anchor.y }, { x: anchor.x - 3, y: anchor.y - 1 },
    { x: anchor.x - 1, y: anchor.y }, { x: anchor.x - 4, y: anchor.y }];
  for (const c of candidates) {
    for (const dyPx of [-10, -20, -30, -40, -50, 0]) {
      await clickTile(page, c.x, c.y, { dyPx });
      const t = normStatus(await statusText(page));
      if (t.includes("1 selected")) return t;
      await page.keyboard.press("Escape"); await page.waitForTimeout(100);
    }
  }
  return "";
}

// ─── Main ───────────────────────────────────────────────────────────
const url = `${BASE}/mapforge/sector?dat=${encodeURIComponent(LAB_DAT)}`
  + `&xml=${encodeURIComponent(XML_PATH)}&tileset=${TILESET}&demo=1`;
console.log("open:", url);

// --disable-web-security: the sidecar's CORS allowlist (main.py) is
// hardcoded to the standard dev pair's origin (localhost:1420 /
// 127.0.0.1:1420) — this lane intentionally runs on an alternate pair
// (1421/8773) to stay isolated from another lane's shared servers, so
// every browser-side fetch to the sidecar is cross-origin and would
// otherwise be CORS-blocked (confirmed live: preflight fails, no
// Access-Control-Allow-Origin for this origin). That's app config, not
// something this script may edit — so the workaround lives entirely in
// THIS throwaway Playwright browser's own launch flags, same trick any
// cross-port E2E harness uses; it has no effect on the real app or any
// other browser.
const browser = await chromium.launch({
  headless: true,
  args: ["--disable-web-security", "--disable-features=IsolateOrigins,site-per-process"],
});
const page = await browser.newPage({ viewport: { width: 1920, height: 1080 } });
page.on("pageerror", (e) => console.error("PAGE ERROR:", e.message));
if (process.env.P1_DEBUG) {
  page.on("console", (m) => console.log("CONSOLE:", m.type(), m.text()));
  page.on("response", (r) => { if (!r.ok() && r.url().includes("8773")) console.log("HTTP", r.status(), r.url()); });
}

let empty = null; // { x, y } — start of the found empty 8-tile strip
let armedFilename = null;

try {
  await page.goto(url, { waitUntil: "domcontentloaded" });

  await step("0. editor ready (session open + atlas painted, <=90s)", async () => {
    await page.waitForFunction(
      () => window.__mapforgeDemo?.getState?.().ready === true,
      null, { timeout: 90000, polling: 500 },
    );
    // "ready" fires once the FIRST (small) atlas pass paints; a fuller
    // atlas can still be baking in the background and swaps the
    // renderer/canvas in place a little later (observed: 467 cells then
    // 9001 cells in the console log). A pan issued between those two
    // swaps briefly computed against a canvas size that didn't match
    // the DOM yet. Letting it settle here removes that source of
    // flakiness independently of the panTo-while-armed finding (header).
    await page.waitForTimeout(12000);
    record("0. editor ready", true);
  });

  // ── Step 1: Brush Box visible; mode-less default (no Inspect button) ──
  await step("1. Brush Box tab visible", async () => {
    await page.locator('.dv-tab:has-text("Brush Box")').first().waitFor({ state: "visible", timeout: 90000 });
    await page.locator('input[type="search"]').first().waitFor({ state: "visible", timeout: 15000 });
    record("1. Brush Box tab + search box visible", true);
  });
  await step("1b. no legacy Inspect tool button by default (mode-less)", async () => {
    const n = await page.locator('button:has-text("Inspect")').count();
    record("1b. Inspect button count === 0", n === 0, `count=${n}`);
  });

  // ── Session + parsed sector; ONE combined empty-strip-near-corner ──
  // anchor for the whole script (see header: a second panTo while a
  // ghost is armed corrupts tileToScreen for the rest of the session,
  // so this pans exactly once, before arming anything, at a spot that
  // serves both the corner-proximity bounds probe AND the place/select/
  // copy/paste/delete/nudge steps that follow).
  const sessionId = await findSession();
  console.log("sidecar session:", sessionId);
  let parsed = await getParsed(sessionId);
  console.log(`sector ${parsed.cols}x${parsed.rows}`);
  empty = findEmptyStrip(parsed, 8);
  if (!empty) throw new Error("could not find an 8-tile empty strip anywhere in the lab sector");
  console.log("empty strip (near-corner if cornerX/Y set):", empty);
  const A = [empty.x, empty.y], B = [empty.x + 1, empty.y], C = [empty.x + 2, empty.y];
  const PASTE_ANCHOR = [empty.x + 5, empty.y]; // -> items land at +5,+6,+7

  await panTo(page, empty.x + 3, empty.y, 0);

  // ── Step 2: arm a ghost; assert "place" status + visible ghost canvas + ok verdict ──
  await step("2. arm a placement ghost from the Brush Box", async () => {
    let armed = false;
    for (const cand of GHOST_CANDIDATES) {
      await armPaletteItem(page, cand.term, cand.filename);
      const txt = await pollUntil(async () => {
        const t = await statusText(page);
        return t && t.includes("place") ? t : null;
      }, { timeout: 2000 });
      if (txt) { armedFilename = cand.filename; armed = true; console.log(`  armed: ${cand.filename}`); break; }
      await page.keyboard.press("Escape"); // clear whatever this candidate armed (or a brush) before trying next
      await page.waitForTimeout(150);
    }
    record("2. a ghost armed (status contains 'place')", armed, armedFilename ?? "none of the candidates armed a ghost");
  });

  if (armedFilename) {
    await step("2b. hover empty ground -> ok verdict + ghost canvas visible", async () => {
      await hoverTile(page, A[0], A[1]);
      const disp = await ghostCanvasDisplay(page);
      const ok = await verdictExists(page, "ok");
      record("2b. ghost canvas display === 'block'", disp === "block", `display=${disp}`);
      record("2c. path[data-verdict=ok] exists over empty ground", ok, `ok=${ok}`);
      const png = await canvasPngDataUrl(page, 0);
      if (png.dataUrl) writeDataUrlPng(png.dataUrl, path.join(SHOTS, "p1_ghost_green_v101.png"));
      else record("2d. ghost canvas toDataURL (green)", false, png.error);
    });

    // -- Step 3 (see header): BOUNDS via the lab map's
    // own pre-existing truck, copy/pasted (not armed fresh -- see header)
    // and hovered near the sector's west edge so its 6-wide footprint
    // spills off the map. Disarms the step-2 chainlink ghost first
    // (never panTo while armed -- see header's Fourth Finding), and
    // re-arms it again afterward so step 4 finds the SAME live ghost it
    // always expected. --
    // A second finding while wiring this up: on this bigmap,
    // MapForgeSector.tsx auto-crops rendering to a "detail window"
    // (detailBbox/fullMetaRef) any time the full sector's raster would
    // exceed a 32MB buffer AND zoom >= 0.75 (debounced ~280ms after
    // zoom/pan settle). window.__mapforgeDemo.panTo computes its target
    // from `renderMeta` — which becomes that CROPPED region's meta once
    // detail mode engages, not the full-map one the wrapper div stays
    // sized to — so a panTo aimed outside the current detail window
    // computes a wildly wrong target (confirmed live: panTo(213,159)
    // right after the corner-area pan alternated between two constant,
    // equally-wrong screen values, never converging, across 8 retries).
    // `zoom < 0.75` unconditionally sets detailBbox to null (full-map
    // meta, no region) per that effect's own first branch — so every far
    // pan below happens zoomed OUT past that threshold, and the return
    // trip re-zooms back to 1 only once back at the original location.
    let boundsHit = null;
    await step("3. blocking verdict reachable (BOUNDS via a copy/pasted multi-tile truck near the west edge)", async () => {
      await page.keyboard.press("Escape"); // disarm the step-2 chainlink ghost before panning
      await page.waitForTimeout(150);
      await page.evaluate(() => window.__mapforgeDemo?.zoomTo(0.5, 0));
      await page.waitForTimeout(1500); // debounce (280ms) + detailBbox-null re-render settle

      parsed = await getParsed(sessionId);
      let truckAnchor = null;
      for (let g = 0; g < parsed.structs.length; g++) {
        const ents = parsed.structs[g] || [];
        if (ents.some((e) => Array.isArray(e) && e[0] === TRUCK_SLOT && e[1] === TRUCK_SUB)) {
          truckAnchor = { x: g % parsed.cols, y: Math.floor(g / parsed.cols) };
          break;
        }
      }
      if (!truckAnchor) { record("3. no pre-existing truck (slot 79 sub 1) found on the structs layer", false); return; }
      console.log("  truckAnchor:", JSON.stringify(truckAnchor));

      await panTo(page, truckAnchor.x, truckAnchor.y, 0);
      const selectedText = await selectSpriteNear(page, truckAnchor);
      if (!selectedText.includes("1 selected")) {
        record("3. click-select the existing truck", false, JSON.stringify(selectedText));
        return;
      }
      await page.keyboard.press("Control+c");
      await page.waitForTimeout(300);
      await page.keyboard.press("Escape"); await page.waitForTimeout(300); // drop selection, keep clipboard

      const edgeY = findClearEdgeRow(parsed);
      if (edgeY === null) { record("3. no clear row found near the west edge to probe", false); return; }
      const EDGE_ANCHOR_X = 3; // truck footprint dx in [-5,0] -> x=-2,-1 fall off the west edge
      console.log("  edge probe: anchor x =", EDGE_ANCHOR_X, "row y =", edgeY);
      await panTo(page, EDGE_ANCHOR_X + 2, edgeY, 0);

      let armedForPaste = null;
      for (let attempt = 0; attempt < 3 && !armedForPaste; attempt++) {
        await page.keyboard.press("Control+v");
        // A freshly-pasted ghost can land on its default hover position
        // already BLOCKED -- still armed, just not showing the literal
        // "place" substring -- so check for any non-null status.
        armedForPaste = await pollUntil(async () => (await statusText(page)) !== null ? true : null, { timeout: 2000 });
        if (!armedForPaste) { await page.keyboard.press("Escape"); await page.waitForTimeout(200); }
      }
      if (!armedForPaste) { record("3. Ctrl+V arms a ghost of the copied truck", false); return; }

      await hoverTile(page, EDGE_ANCHOR_X, edgeY);
      const blocked = await pollUntil(async () => ((await verdictExists(page, "blocking")) ? true : null), { timeout: 2500 });
      const t = normStatus(await statusText(page));
      boundsHit = blocked ? { x: EDGE_ANCHOR_X, y: edgeY } : null;
      record("3. path[data-verdict=blocking] found hovering the truck ghost off the west edge", !!blocked, JSON.stringify(t));
      if (blocked) {
        record("3a. status text contains 'BOUNDS'", t.includes("BOUNDS"), JSON.stringify(t));
        record("3b. status text starts with the X mark while blocked", t.startsWith("✕"), JSON.stringify(t));
        const png = await canvasPngDataUrl(page, 0);
        if (png.dataUrl) writeDataUrlPng(png.dataUrl, path.join(SHOTS, "p1_ghost_red_v101.png"));
        else record("3c. ghost canvas toDataURL (red)", false, png.error);
        // NOTE per the header's advisor-confirmed finding: the ghost
        // CANVAS holds the sprite bitmap only -- the red/green tint lives
        // on the separate SVG data-verdict overlay, so the two PNGs
        // above are pixel-identical; the full-page screenshot below is
        // what actually shows red.
        try {
          await page.screenshot({ path: path.join(SHOTS, "p1_editor_red_v101.png"), timeout: 8000 });
          console.log(`  png -> ${path.join(SHOTS, "p1_editor_red_v101.png")}`);
        } catch (e) {
          console.log(`  SKIP full-page red screenshot (timed out / animating canvas): ${e.message}`);
        }
      }
      await page.keyboard.press("Escape"); await page.waitForTimeout(150); // cancel the paste -- never commit it
      // NOTE: the return trip to the corner-area pan happens in step 3d
      // below, not here -- this function has 4 early `return`s above
      // (no truck found / select failed / no clear edge row / paste
      // never armed) that would otherwise skip the return-trip entirely,
      // leaving the view zoomed out and panned to the truck or the edge
      // for every step from here on. 3d checks the live zoom and pans
      // back FIRST if still zoomed out, unconditionally, regardless of
      // which exit path step 3 took.
    });

    // Restore the original pan (if step 3 left it zoomed out and panned
    // elsewhere -- true on every early-exit path, not just the happy
    // one) + zoom + re-arm the chainlink ghost for step 4 onward, OUTSIDE
    // the step() above so a step-3 failure never skips steps 4-10 (a
    // distinct bug from the pan issue itself -- the first
    // cut of this fix let panTo's rejection here throw past every
    // remaining step uncaught, turning one failed check into eighteen
    // silently-skipped ones).
    await step("3d. restore pan/zoom + re-arm the chainlink ghost for step 4 onward", async () => {
      // Pan back to the corner-area strip FIRST, while still zoomed out
      // (if step 3 left it that way -- every one of its early exits does,
      // not just the happy path) -- panning at zoom 1 straight from the
      // truck/edge location is the exact far-jump that broke step 3 in
      // the first place. Only THEN restore zoom.
      const st = await page.evaluate(() => window.__mapforgeDemo?.getState());
      if (st && st.zoom < 0.75) {
        await panTo(page, empty.x + 3, empty.y, 0);
      }
      await page.evaluate(() => window.__mapforgeDemo?.zoomTo(1, 0));
      await page.waitForTimeout(1500); // let the detail window reform around the restored pan
      await armPaletteItem(page, armedFilename.replace(/\.sti$/i, ""), armedFilename);
      const rearmed = await pollUntil(async () => (await statusText(page))?.includes("place") ? true : null, { timeout: 2000 });
      await hoverTile(page, A[0], A[1]);
      record("3d. chainlink ghost re-armed and hovering A", !!rearmed);
    });

    // -- Step 4: back on empty ground, plain click places + disarms --
    // The chainlink ghost re-armed by step 3d just above (step 3 itself
    // used a different asset and left the session panned/armed
    // differently).
    await step("4. plain click on empty ground places + disarms the ghost", async () => {
      await clickTile(page, A[0], A[1]);
      const gA = A[1] * parsed.cols + A[0];
      const gotEntry = await pollUntil(async () => {
        parsed = await getParsed(sessionId);
        return countEntries(parsed.structs, gA) > 0 ? true : null;
      }, { timeout: 6000 });
      record("4a. /parsed structs at A gained an entry", !!gotEntry, `count=${countEntries(parsed.structs, gA)}`);
      const t = await pollUntil(async () => {
        const txt = await statusText(page);
        return (txt === null || !txt.includes("place")) ? { txt } : null;
      }, { timeout: 3000 });
      record("4b. status no longer contains 'place' (ghost gone)", !!t, JSON.stringify(t?.txt));
    });

    // ── Step 5 (rewritten, see header): Shift+click ENQUEUES
    // and keeps the ghost armed -- it no longer places immediately (that
    // was the OLD "Shift keeps it armed AND places" behavior this script
    // originally tested; the shipped app now queues instead, per the
    // final fix wave's F6 banner text and mapPlacement.ts's
    // queueCommitEdits). Once queue.length > 0 the bottom-left status
    // line (data-status="placement") STOPS showing "... place ..." at
    // all -- it switches wholesale to "N queued - click = build all -
    // Esc = drop" (MapForgeSector.tsx ~L6310), so "still armed" is now
    // asserted via the ghost canvas + the data-queued SVG path, not the
    // old `.includes("place")` check (confirmed dead: a later re-run
    // already found the old checks failing this exact way).
    //
    // T (=empty.x+3) is a throwaway probe tile -- part of the same
    // 8-wide empty strip `findEmptyStrip` guaranteed, otherwise unused
    // (it's only ever a camera-hover x-offset elsewhere in this script)
    // -- used ONLY to exercise a full queue-commit-then-undo cycle
    // without disturbing B/C, which steps 6-9 still need standing on
    // the map afterward exactly as before this rewrite.
    const T = [empty.x + 3, empty.y];
    await step("5. re-arm; Shift+click enqueues + stays armed; nothing lands until a plain click; one Ctrl+Z undoes the whole commit", async () => {
      await armPaletteItem(page, armedFilename.replace(/\.sti$/i, ""), armedFilename);
      const armed1 = await pollUntil(async () => (await statusText(page))?.includes("place") ? true : null, { timeout: 2000 });
      record("5a. re-armed", !!armed1);

      await clickTile(page, B[0], B[1], { shift: true });
      const qAfterB = await pollUntil(async () => (await queuedPathExists(page)) ? true : null, { timeout: 3000 });
      const stAfterB = normStatus(await statusText(page));
      record("5b. Shift+click at B enqueues (data-queued path present)", !!qAfterB, JSON.stringify(stAfterB));
      record("5b2. status shows '1 queued'", stAfterB.includes("1 queued"), JSON.stringify(stAfterB));
      const ghostStillUp = await ghostCanvasDisplay(page);
      record("5b3. ghost canvas still visible (still armed, not placed)", ghostStillUp === "block", `display=${ghostStillUp}`);

      await clickTile(page, C[0], C[1], { shift: true });
      const stAfterC = normStatus(await statusText(page));
      record("5c. status shows '2 queued' after a second Shift+click", stAfterC.includes("2 queued"), JSON.stringify(stAfterC));

      parsed = await getParsed(sessionId);
      const gB = B[1] * parsed.cols + B[0], gC = C[1] * parsed.cols + C[0];
      record("5d. NOTHING landed in /parsed yet from either Shift+click", countEntries(parsed.structs, gB) === 0 && countEntries(parsed.structs, gC) === 0,
        `B=${countEntries(parsed.structs, gB)} C=${countEntries(parsed.structs, gC)}`);

      // A plain click commits queue(B,C) + the clicked tile T as ONE stroke.
      await clickTile(page, T[0], T[1]);
      const gT = T[1] * parsed.cols + T[0];
      const committed = await pollUntil(async () => {
        parsed = await getParsed(sessionId);
        return (countEntries(parsed.structs, gB) > 0 && countEntries(parsed.structs, gC) > 0 && countEntries(parsed.structs, gT) > 0)
          ? true : null;
      }, { timeout: 6000 });
      record("5e. plain click builds the queue + the clicked tile (B, C, T all gain an entry)", !!committed,
        `B=${countEntries(parsed.structs, gB)} C=${countEntries(parsed.structs, gC)} T=${countEntries(parsed.structs, gT)}`);
      const disarmed = await pollUntil(async () => (!(await statusText(page))?.includes("queued")) ? true : null, { timeout: 2000 });
      record("5f. ghost disarms after the commit (status no longer shows queued)", !!disarmed);

      await page.keyboard.press("Control+z");
      const reverted = await pollUntil(async () => {
        parsed = await getParsed(sessionId);
        return (countEntries(parsed.structs, gB) === 0 && countEntries(parsed.structs, gC) === 0 && countEntries(parsed.structs, gT) === 0)
          ? true : null;
      }, { timeout: 6000 });
      record("5g. ONE Ctrl+Z removes all three (one stroke)", !!reverted,
        `B=${countEntries(parsed.structs, gB)} C=${countEntries(parsed.structs, gC)} T=${countEntries(parsed.structs, gT)}`);
    });

    // ── Step 5h: leave B and C actually PLACED (via the same queue path)
    // so steps 6-9 below find exactly A/B/C standing, same as before the
    // Step 5 rewrite -- step 5 above proved + undid the queue mechanic on
    // B/C/T; this repeats it once more WITHOUT the undo so B/C are real. ──
    await step("5h. re-arm; queue B, plain-click C -> both land for real (kept for steps 6-9)", async () => {
      await armPaletteItem(page, armedFilename.replace(/\.sti$/i, ""), armedFilename);
      await pollUntil(async () => (await statusText(page))?.includes("place") ? true : null, { timeout: 2000 });
      await clickTile(page, B[0], B[1], { shift: true });
      await pollUntil(async () => (await queuedPathExists(page)) ? true : null, { timeout: 3000 });
      await clickTile(page, C[0], C[1]);
      const gB = B[1] * parsed.cols + B[0], gC = C[1] * parsed.cols + C[0];
      const placed = await pollUntil(async () => {
        parsed = await getParsed(sessionId);
        return (countEntries(parsed.structs, gB) > 0 && countEntries(parsed.structs, gC) > 0) ? true : null;
      }, { timeout: 6000 });
      record("5h. B and C both placed for real (kept standing)", !!placed,
        `B=${countEntries(parsed.structs, gB)} C=${countEntries(parsed.structs, gC)}`);
      await page.keyboard.press("Escape"); await page.waitForTimeout(150); // belt-and-suspenders: guarantee disarmed before step 6's marquee
    });

    // ── Step 6: marquee-select the three (nothing armed) ──
    await step("6. marquee-drag selects the three placed sprites", async () => {
      await marqueeSelect(page, [A[0] - 2, A[1] - 2], [C[0] + 2, C[1] + 2]);
      const sel = await selectionPathExists(page);
      const t = normStatus(await statusText(page));
      record("6a. path[data-selection=tiles] exists", sel);
      record("6b. status contains '3 selected'", t.includes("3 selected"), JSON.stringify(t));
      const png = await canvasPngDataUrl(page, 2);
      if (png.dataUrl) writeDataUrlPng(png.dataUrl, path.join(SHOTS, "p1_selection_v101.png"));
      else record("6c. selection canvas toDataURL", false, png.error);
    });

    // ── Step 7: Ctrl+C, Ctrl+V, move+click, Ctrl+Z (one stroke) ──
    await step("7. copy -> paste -> undo (one stroke)", async () => {
      await page.keyboard.press("Control+c");
      await page.waitForTimeout(150);
      await page.keyboard.press("Control+v");
      const armedForPaste = await pollUntil(async () => (await statusText(page))?.includes("place") ? true : null, { timeout: 2000 });
      record("7a. Ctrl+V arms a ghost of the 3-sprite clipboard", !!armedForPaste);
      await hoverTile(page, PASTE_ANCHOR[0], PASTE_ANCHOR[1]);
      await clickTile(page, PASTE_ANCHOR[0], PASTE_ANCHOR[1]);
      const g5 = empty.y * parsed.cols + (empty.x + 5);
      const g6 = empty.y * parsed.cols + (empty.x + 6);
      const g7 = empty.y * parsed.cols + (empty.x + 7);
      const placedThree = await pollUntil(async () => {
        parsed = await getParsed(sessionId);
        return (countEntries(parsed.structs, g5) > 0 && countEntries(parsed.structs, g6) > 0 && countEntries(parsed.structs, g7) > 0)
          ? true : null;
      }, { timeout: 6000 });
      record("7b. three new entries appear at the paste offset", !!placedThree,
        `[${countEntries(parsed.structs, g5)},${countEntries(parsed.structs, g6)},${countEntries(parsed.structs, g7)}]`);
      await page.keyboard.press("Control+z");
      const revertedThree = await pollUntil(async () => {
        parsed = await getParsed(sessionId);
        return (countEntries(parsed.structs, g5) === 0 && countEntries(parsed.structs, g6) === 0 && countEntries(parsed.structs, g7) === 0)
          ? true : null;
      }, { timeout: 6000 });
      record("7c. ONE Ctrl+Z reverts all three (one stroke)", !!revertedThree,
        `[${countEntries(parsed.structs, g5)},${countEntries(parsed.structs, g6)},${countEntries(parsed.structs, g7)}]`);
    });

    // ── Step 8: re-select the three, Delete, Ctrl+Z restores ──
    await step("8. select three -> Delete -> Ctrl+Z restores", async () => {
      await page.keyboard.press("Escape"); // drop any stale selection/ghost first (advisor: state after undo is unpredictable)
      await page.waitForTimeout(150);
      await marqueeSelect(page, [A[0] - 2, A[1] - 2], [C[0] + 2, C[1] + 2]);
      const t = normStatus(await statusText(page));
      record("8a. re-select shows '3 selected'", t.includes("3 selected"), JSON.stringify(t));
      const gA = A[1] * parsed.cols + A[0], gB = B[1] * parsed.cols + B[0], gC = C[1] * parsed.cols + C[0];
      await page.keyboard.press("Delete");
      const gone = await pollUntil(async () => {
        parsed = await getParsed(sessionId);
        return (countEntries(parsed.structs, gA) === 0 && countEntries(parsed.structs, gB) === 0 && countEntries(parsed.structs, gC) === 0)
          ? true : null;
      }, { timeout: 6000 });
      record("8b. Delete removes all three", !!gone,
        `[${countEntries(parsed.structs, gA)},${countEntries(parsed.structs, gB)},${countEntries(parsed.structs, gC)}]`);
      await page.keyboard.press("Control+z");
      const restored = await pollUntil(async () => {
        parsed = await getParsed(sessionId);
        return (countEntries(parsed.structs, gA) > 0 && countEntries(parsed.structs, gB) > 0 && countEntries(parsed.structs, gC) > 0)
          ? true : null;
      }, { timeout: 6000 });
      record("8c. Ctrl+Z restores all three", !!restored,
        `[${countEntries(parsed.structs, gA)},${countEntries(parsed.structs, gB)},${countEntries(parsed.structs, gC)}]`);
    });

    // ── Step 9: select ONE, ArrowRight nudges +1 x ──
    await step("9. select one sprite -> ArrowRight nudges it +1 x", async () => {
      await page.keyboard.press("Escape");
      await page.waitForTimeout(150);
      // A click-to-SELECT needs the sprite's actual drawn pixels, not
      // the tile's geometric center — an iso sprite draws upward from
      // the tile's south apex (confirmed empirically: dy=0 misses
      // gecko_chainlink, dy=-10 hits). Sweep a few upward offsets so
      // this isn't pinned to one candidate's exact sprite height.
      let t = "";
      for (const dyPx of [-10, -20, -30, 0, -45]) {
        await clickTile(page, C[0], C[1], { dyPx }); // plain click on a single sprite selects just it
        t = normStatus(await statusText(page));
        if (t.includes("1 selected")) break;
        if (process.env.P1_DEBUG) console.log(`  [dbg] step9 click dyPx=${dyPx} status:`, JSON.stringify(t));
        await page.keyboard.press("Escape");
        await page.waitForTimeout(150);
      }
      record("9a. plain click selects just one ('1 selected')", t.includes("1 selected"), JSON.stringify(t));
      const gCbefore = C[1] * parsed.cols + C[0];
      const gCafter = C[1] * parsed.cols + (C[0] + 1);
      const beforeCount = countEntries(parsed.structs, gCbefore);
      await page.keyboard.press("ArrowRight");
      const moved = await pollUntil(async () => {
        parsed = await getParsed(sessionId);
        return countEntries(parsed.structs, gCafter) > 0 ? true : null;
      }, { timeout: 6000 });
      record("9b. /parsed shows the entry moved to x+1", !!moved,
        `before(${C[0]},${C[1]})=${beforeCount} after(${C[0] + 1},${C[1]})=${countEntries(parsed.structs, gCafter)}`);
    });
  } else {
    console.log("SKIP steps 2-9: no candidate armed a ghost (see step 2's observed value).");
  }

  // ── Step 10: legacyTools toggle shows the old Inspect tool bar ──
  await step("10. legacyTools:true reveals the legacy Inspect button; revert", async () => {
    await page.evaluate((key) => {
      const raw = localStorage.getItem(key);
      const s = raw ? JSON.parse(raw) : {};
      s.legacyTools = true;
      localStorage.setItem(key, JSON.stringify(s));
    }, SETTINGS_KEY);
    await page.reload({ waitUntil: "domcontentloaded" });
    await page.waitForFunction(() => window.__mapforgeDemo?.getState?.().ready === true, null, { timeout: 90000, polling: 500 });
    await page.locator('.dv-tab:has-text("Brush Box")').first().waitFor({ state: "visible", timeout: 30000 });
    const n = await page.locator('button:has-text("Inspect")').count();
    record("10a. Inspect button visible with legacyTools:true", n > 0, `count=${n}`);

    await page.evaluate((key) => {
      const raw = localStorage.getItem(key);
      const s = raw ? JSON.parse(raw) : {};
      s.legacyTools = false;
      localStorage.setItem(key, JSON.stringify(s));
    }, SETTINGS_KEY);
    await page.reload({ waitUntil: "domcontentloaded" });
    await page.waitForFunction(() => window.__mapforgeDemo?.getState?.().ready === true, null, { timeout: 90000, polling: 500 });
    const n2 = await page.locator('button:has-text("Inspect")').count();
    record("10b. Inspect button gone again after reverting legacyTools", n2 === 0, `count=${n2}`);
  });
} catch (e) {
  console.error("SCRIPT FAILED:", e);
  failCount++;
} finally {
  await browser.close();
  // ── Cleanup: force-delete every sidecar session opened against the
  // lab dat (a reload in step 10 may have opened a second one) — the
  // lab session's edits must never be saved, and must not linger. ──
  try {
    const sessions = await sidecarJson("/sessions");
    for (const s of sessions) {
      if (norm(s.dat_path) !== norm(LAB_DAT)) continue;
      // SESSION_BUSY (409) right after browser.close() is transient —
      // the sidecar hasn't yet noticed the last in-flight request from
      // the closed browser finished. A couple of retries clears it.
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
}

console.log(`\n${results.length} checks, ${failCount} failed.`);
process.exit(failCount === 0 ? 0 : 1);
