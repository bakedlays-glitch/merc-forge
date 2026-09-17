#!/usr/bin/env node
/**
 * Verification of the sidecar's StarCraft-style placement
 * ORACLE (GET /placement/tables, POST /sessions/{id}/placement/check,
 * with RING/CONTACT/INVERSION merged into the ghost overlay) against
 * THIS lane's own dev servers and the
 * gecko_town_c16_v8_placement_lab.dat scratch copy.
 *
 * Same standalone-script shape as agenda_placement_p1.mjs and for the same
 * reason (read that file's header first): runner.mjs's fixed verb table
 * has no eval/fetch/canvas-capture/modifier-click primitives this task
 * needs. Run directly with node, not through runner.mjs:
 *
 *   node agenda_placement_p2.mjs [--base http://localhost:1421]
 *                                 [--sidecar http://127.0.0.1:8773]
 *                                 [--shots <dir>]
 *
 * REAL FINDINGS surfaced while building this (read from source +
 * reproduced live — not guessed) — all three are load-bearing for how
 * steps 2/3/4 below are shaped:
 *
 * 1. LAYER-ROUTING GAP. Tileset 72's "vehicle"/"landmark" ORACLE-category
 *    overrides (sidecar's per-sub table: subs "79.1"/"79.2" = vehicle —
 *    gastuf1.sti's gas-station truck/pump; "79.3".."79.6" = landmark;
 *    "18.4"/"18.19"/"18.20" = vehicle) all sit on TileTypeDefines enum
 *    ranges that frontend/src/lib mercwizard_core/mapforge/tile_families.py
 *    classifies as "scatter" (79-84 = DEBRISROCKS..DEBRISMISC) / "veg"
 *    (12-23) — they're gas-station/junk SET-DRESSING slots repurposed for
 *    this tileset's art, not the engine's real FIRSTVEHICLE/SECONDVEHICLE
 *    pair (slots 88/89). frontend/src/routes/MapForgePalette.tsx's
 *    CATEGORY_TO_LAYER routes scatter/veg families to the "objs" layer —
 *    but sidecar/routes/mapforge_placement.py's placement_check()
 *    short-circuits any candidate whose `layer != "structs"` straight to
 *    `ok: true` (never calls sitekit at all). Confirmed live: arming
 *    gastuf1 sub 1 from the Brush Box and placing it lands the entry in
 *    `parsed.objs`, not `parsed.structs` — every RING sweep around a
 *    FRESHLY ARMED "truck" ghost comes back "ok" no matter how close.
 *
 * 2. THE WORKAROUND. The lab map's PRE-EXISTING truck (gridno for
 *    tile (213,159), entry `[79,1]`) was authored directly onto the
 *    STRUCTS layer (legacy content, never routed through the Brush Box's
 *    CATEGORY_TO_LAYER) — confirmed live via /parsed. A SELECTED-and-
 *    copy/pasted ghost of an existing struct carries its CURRENT layer
 *    forward (MapForgeSector.tsx's selection-hit builder uses
 *    `layer: hit.layer`, and groupRefsAt/paste preserve it), so copy/paste
 *    of THIS truck — not a fresh Brush Box arm — produces a genuine
 *    "structs"-layer, 12-JSD-tile, oracle-category "vehicle" ghost the
 *    sidecar DOES see. That is the only route to an actual RING pass in
 *    this build, and is what step 3 below does.
 *
 * 3. RING IS ONE-DIRECTIONAL BY DESIGN (not a bug — a design fact worth
 *    documenting). Headless_Compiler/sitekit/world.py:267-270
 *    (`ring_violators`) gates on `inst.cat in BIG`
 *    (`BIG = ("vehicle", "landmark")`) before doing anything else — RING
 *    only ever fires for the CANDIDATE ITSELF being a vehicle/landmark
 *    near a fence/building/sign, never the reverse (a fence/building
 *    candidate hovered near an EXISTING vehicle never triggers it, however
 *    close). So step 2's literal ask (arm a FENCE, hover near the existing
 *    truck, expect RING) is unreachable independent of finding #1 —
 *    confirmed by reading world.py, then reproduced live: swept a fence
 *    ghost across every 8-neighbour of the real truck's JSD footprint,
 *    including directly atop a footprint tile (a TILE-blocking sanity
 *    check — that DID fire, proving the truck is oracle-visible), and RING
 *    never appeared. Step 3 (arm the TRUCK, hover near a fence) is the
 *    direction the code actually supports.
 *
 * The JSD footprint for slot 79 (GET /sti/jsd?...&slot=79) is bXPos in
 * [-5,0], bYPos in [-1,0] relative to the anchor — 6 tiles wide, 2 tall,
 * running WEST and one row further NORTH of the anchor. This confirms the
 * task brief's "its JSD footprint runs to the WEST of the anchor" — but
 * the brief's own suggested probe tile, "one tile north of the anchor"
 * ((anchor.x, anchor.y-1)), is actually INSIDE that footprint (it's
 * offset (0,-1)); the true RING-adjacent (8-neighbour, not overlapping)
 * cell one step further out is (anchor.x, anchor.y-2), which is what this
 * script actually probes.
 *
 * SAFETY: only ever opens the caller's lab .dat copy (never
 * gecko_town_c16_v8.dat), never clicks Save / Ctrl+S / calls /save, never
 * commits the copy/paste ghost (Escape cancels it — a blocking verdict
 * would refuse the click anyway per D11), and force-deletes every sidecar
 * session opened against that lab dat at the end.
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
const SHOTS = path.resolve(flag("--shots", path.join(__dirname, "..", "..", "..", "scratch", "p2_placement_frames")));
fs.mkdirSync(SHOTS, { recursive: true });

const INSTALL_ROOT = process.env.JA2_INSTALL
  || "C:/Games/Jagged Alliance 2/<your-1.13-install>";
const LAB_DAT = `${INSTALL_ROOT}/_gecko_qa/gecko_town_c16_v8_placement_lab.dat`;
const XML_PATH = `${INSTALL_ROOT}/Data-1.13/Ja2Set.dat.xml`;
const TILESET = 72;
const API = `${SIDECAR}/api/v1/mapforge`;

const TRUCK_SLOT = 79, TRUCK_SUB = 1;           // gastuf1.sti sub 1 — oracle category "vehicle"
const FENCE_SLOT = 89, FENCE_TERM = "chainlink", FENCE_FILE = "gecko_chainlink.sti"; // oracle category "fence", family "vehicle" -> arms a GHOST (unlike slot 86, a brush)
const SCATTER_SLOT = 74, SCATTER_TERM = "wasteland_crate", SCATTER_FILE = "wasteland_crate.sti"; // family "furniture" -> structs layer; oracle category "scatter" (non-BIG, INVERSION-eligible)

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
  try { await fn(); }
  catch (e) { record(name, false, `threw: ${e instanceof Error ? e.message : String(e)}`); }
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
async function getParsed(sessionId) { return sidecarJson(`/sessions/${encodeURIComponent(sessionId)}/parsed?_=${Date.now()}`); }
function countEntries(layerArr, gridno) { return (layerArr[gridno] ?? []).length; }
async function pollUntil(fn, { timeout = 6000, interval = 200 } = {}) {
  const t0 = Date.now();
  for (;;) {
    const v = await fn();
    if (v) return v;
    if (Date.now() - t0 > timeout) return null;
    await new Promise((r) => setTimeout(r, interval));
  }
}

/** Unique (bXPos,bYPos) footprint offsets for a slot's first DB_STRUCTURE
 * (the .jsd stores each offset twice — two profile layers per cell — the
 * route's own docstring calls this "the first DB_STRUCTURE"). */
async function getFootprintOffsets(slot) {
  const j = await sidecarJson(`/sti/jsd?xml=${encodeURIComponent(XML_PATH)}&tileset=${TILESET}&slot=${slot}`);
  const seen = new Set(); const offs = [];
  for (const t of j.tiles ?? []) {
    const k = `${t.bXPos},${t.bYPos}`;
    if (!seen.has(k)) { seen.add(k); offs.push({ dx: t.bXPos, dy: t.bYPos }); }
  }
  return offs;
}
function footprintCells(anchor, offs) { return offs.map((o) => ({ x: anchor.x + o.dx, y: anchor.y + o.dy })); }

// ─── Page-level helpers (mirrors agenda_placement_p1.mjs) ──────────
async function tileToScreen(page, x, y) {
  return page.evaluate(([tx, ty]) => window.__mapforgeDemo?.tileToScreen(tx, ty), [x, y]);
}
const VIEWPORT = { width: 1920, height: 1080 };
/** Pan so tile (x,y) lands at the viewport center. NEVER call this while a
 * ghost is armed (agenda_placement_p1.mjs finding #4: panTo computes a
 * wrong target and stays wrong for every further call until the ghost is
 * disarmed) — every call site below pans only between fully-disarmed
 * steps. */
async function panTo(page, x, y, ms = 0) {
  for (let attempt = 0; attempt < 6; attempt++) {
    await page.evaluate(([tx, ty, m]) => window.__mapforgeDemo?.panTo(tx, ty, m), [x, y, ms]);
    await page.waitForTimeout(ms + 300);
    const p = await tileToScreen(page, x, y);
    const okX = p && p.x > VIEWPORT.width * 0.15 && p.x < VIEWPORT.width * 0.85;
    const okY = p && p.y > VIEWPORT.height * 0.15 && p.y < VIEWPORT.height * 0.85;
    if (okX && okY) return;
    if (process.env.P2_DEBUG) console.log(`  [dbg] panTo(${x},${y}) attempt ${attempt} landed off-center:`, p);
  }
  throw new Error(`panTo(${x},${y}) never centered the tile after 6 attempts`);
}
async function hoverTile(page, x, y, dyPx = 0) {
  const p = await tileToScreen(page, x, y);
  if (!p) return null;
  await page.mouse.move(p.x, p.y + dyPx, { steps: 3 });
  await page.waitForTimeout(150);
  return p;
}
async function clickTile(page, x, y, { shift = false, dyPx = 0 } = {}) {
  const p = await hoverTile(page, x, y, dyPx);
  if (shift) await page.keyboard.down("Shift");
  await page.mouse.down(); await page.waitForTimeout(80); await page.mouse.up();
  await page.waitForTimeout(150);
  if (shift) await page.keyboard.up("Shift");
  return p;
}
async function statusText(page) {
  return page.evaluate(() => document.querySelector('[data-status="placement"]')?.textContent ?? null);
}
function normStatus(t) { return (t ?? "").replace(/^[\s·]+/, ""); }
async function verdictExists(page, tier) {
  return page.evaluate((t) => !!document.querySelector(`svg path[data-verdict="${t}"]`), tier);
}
async function canvasPngDataUrl(page, idx = 0) {
  return page.evaluate((i) => {
    const cv = document.querySelectorAll("canvas.z-20")[i];
    if (!cv) return { error: "no such canvas.z-20" };
    try { return { dataUrl: cv.toDataURL("image/png"), display: cv.style.display }; }
    catch (e) { return { error: String(e && e.message || e), display: cv.style.display }; }
  }, idx);
}
function writeDataUrlPng(dataUrl, outPath) {
  const b64 = dataUrl.replace(/^data:image\/png;base64,/, "");
  fs.writeFileSync(outPath, Buffer.from(b64, "base64"));
  console.log(`  png -> ${outPath}`);
}
async function armPaletteItem(page, term, filenamePrefix) {
  const search = page.locator('input[type="search"]').first();
  await search.click(); await search.fill(""); await search.fill(term);
  await page.waitForTimeout(400);
  const thumb = page.locator(`button[title^="${filenamePrefix}"]`).first();
  await thumb.waitFor({ state: "visible", timeout: 8000 });
  await thumb.click(); await page.waitForTimeout(300);
  const subBtn = page.locator('button[title="Pick sub 1"]').first();
  if (await subBtn.count() > 0) { await subBtn.click(); await page.waitForTimeout(250); }
}
/** Hover + poll up to `timeout` ms for a definitive (blocking/advisory)
 * verdict, returning whatever was last observed (never throws — the
 * caller decides pass/fail). */
async function checkAt(page, x, y, { timeout = 500, dyPx = 0 } = {}) {
  await hoverTile(page, x, y, dyPx);
  const t0 = Date.now();
  let last = { blocking: false, advisory: false, ok: false, status: null };
  for (;;) {
    const [blocking, advisory, ok, status] = await Promise.all([
      verdictExists(page, "blocking"), verdictExists(page, "advisory"), verdictExists(page, "ok"), statusText(page),
    ]);
    last = { blocking, advisory, ok, status };
    if (blocking || advisory) return last;
    if (Date.now() - t0 > timeout) return last;
    await page.waitForTimeout(50);
  }
}
/** First empty (structs AND objs) w x h rectangle found scanning OUTWARD
 * from (cx, cy) — used to place the isolated fence NEAR the existing
 * truck, so the whole script fits inside a single pan (a second panTo
 * call to a tile far from the first — even fully disarmed — was observed
 * to compute a wildly wrong screen position on this 360x360 bigmap; a
 * short hop stays reliable, see the header). */
function findEmptyBlockNear(parsed, cx, cy, w, h, maxRadius = 40) {
  const { cols, rows, structs, objs } = parsed;
  const isEmpty = (x, y) => x >= 0 && y >= 0 && x < cols && y < rows
    && (structs[y * cols + x]?.length ?? 0) === 0 && (objs[y * cols + x]?.length ?? 0) === 0;
  for (let r = 4; r <= maxRadius; r++) {
    for (let y0 = cy - r; y0 <= cy + r - h; y0++) {
      for (let x0 = cx - r; x0 <= cx + r - w; x0++) {
        let ok = true;
        outer: for (let y = y0; y < y0 + h; y++) for (let x = x0; x < x0 + w; x++) if (!isEmpty(x, y)) { ok = false; break outer; }
        if (ok) return { x0, y0, radius: r };
      }
    }
  }
  return null;
}

// ─── Main ───────────────────────────────────────────────────────────
const url = `${BASE}/mapforge/sector?dat=${encodeURIComponent(LAB_DAT)}`
  + `&xml=${encodeURIComponent(XML_PATH)}&tileset=${TILESET}&demo=1`;
console.log("open:", url);

// --disable-web-security: same CORS workaround as agenda_placement_p1.mjs
// (this lane's sidecar/vite pair is 1421/8773, cross-origin from the
// standard 1420/8000 pair the sidecar's allowlist is hardcoded to) — a
// throwaway Playwright browser flag, zero effect on the real app.
const browser = await chromium.launch({
  headless: true,
  args: ["--disable-web-security", "--disable-features=IsolateOrigins,site-per-process"],
});
const page = await browser.newPage({ viewport: VIEWPORT });
page.on("pageerror", (e) => console.error("PAGE ERROR:", e.message));
if (process.env.P2_DEBUG) {
  page.on("console", (m) => console.log("CONSOLE:", m.type(), m.text()));
}

try {
  await page.goto(url, { waitUntil: "domcontentloaded" });

  await step("0. editor ready (session open + atlas painted, <=90s)", async () => {
    await page.waitForFunction(() => window.__mapforgeDemo?.getState?.().ready === true, null, { timeout: 90000, polling: 500 });
    await page.waitForTimeout(12000); // let the fuller atlas bake settle (see P1 header)
    record("0. editor ready", true);
  });

  const sessionId = await findSession();
  console.log("sidecar session:", sessionId);
  let parsed = await getParsed(sessionId);
  console.log(`sector ${parsed.cols}x${parsed.rows}`);

  // ── Step 1: GET /placement/tables via an in-page (eval) fetch ──
  await step("1. placement/tables (eval fetch): categories[86]===fence, fences[86].ns===7", async () => {
    const r = await page.evaluate(async (api) => {
      try {
        const res = await fetch(`${api}/placement/tables?tileset=72`);
        return { ok: res.ok, status: res.status, body: await res.json() };
      } catch (e) { return { error: String(e) }; }
    }, API);
    const cat86 = r.body?.categories?.["86"];
    const ns86 = r.body?.fences?.["86"]?.ns;
    record("1a. eval-fetch succeeded", r.ok === true, JSON.stringify({ status: r.status, error: r.error }));
    record("1b. categories[86] === 'fence'", cat86 === "fence", `categories[86]=${JSON.stringify(cat86)}`);
    record("1c. fences[86].ns === 7", ns86 === 7, `fences[86].ns=${JSON.stringify(ns86)}`);
  });

  // ── Locate the pre-existing truck (finding #2: it must already live on
  // the STRUCTS layer, not something this script places itself). ──
  let truckAnchor = null;
  for (let g = 0; g < parsed.structs.length; g++) {
    const ents = parsed.structs[g] || [];
    if (ents.some((e) => Array.isArray(e) && e[0] === TRUCK_SLOT && e[1] === TRUCK_SUB)) {
      truckAnchor = { x: g % parsed.cols, y: Math.floor(g / parsed.cols) };
      break;
    }
  }
  record("existing truck (slot 79 sub 1) found on the structs layer", !!truckAnchor, JSON.stringify(truckAnchor));
  if (!truckAnchor) throw new Error("no pre-existing slot-79/sub-1 struct in the lab map — cannot run steps 2/3 as designed");

  const offsets = await getFootprintOffsets(TRUCK_SLOT);
  console.log("truck JSD footprint offsets:", JSON.stringify(offsets));
  const truckFootprint = footprintCells(truckAnchor, offsets);
  const overlapCell = truckFootprint.find((c) => c.y === truckAnchor.y - 1) ?? truckFootprint[0];
  const ringCell = { x: truckAnchor.x, y: truckAnchor.y - 2 }; // one step past the footprint's north edge — see header
  console.log("overlapCell (should be TILE-blocking):", overlapCell, " ringCell (fence-candidate RING probe, expected unreachable per finding #3):", ringCell);

  // ── Place an isolated fence NEAR the truck (a clear block found
  // scanning outward from the truck's anchor — see findEmptyBlockNear's
  // header comment for why "near", not "anywhere"). Everything from here
  // on happens inside ONE pan.
  //
  // Sized to hold the truck's OWN 6x2 JSD footprint once it's later
  // pasted here (step 3), not just a bare 1-tile fence: a first pass at
  // this only verified clearance under the fence's own tile and got a
  // false pass — the paste-ghost's footprint reached 2 tiles further west
  // than the verified block and landed on an unrelated pre-existing fence
  // (slot 77) the clearance check never looked at. Layout (block-relative
  // x): 1 buffer col, then the footprint's 6 columns (x0+1..x0+6), then
  // the fence itself immediately east of the footprint's east edge
  // (x0+7 — an 8-neighbour, not an overlap), then 1 buffer col (x0+8). ──
  const block = findEmptyBlockNear(parsed, truckAnchor.x, truckAnchor.y, 9, 3, 40);
  if (!block) throw new Error("no clear 9x3 block found within 40 tiles of the truck");
  const pasteAnchor = { x: block.x0 + 6, y: block.y0 + 1 };   // truck-paste anchor; footprint spans x0+1..x0+6, y0..y0+1
  const fenceAnchor = { x: block.x0 + 7, y: block.y0 + 1 };   // 1 tile east of the footprint's east edge
  console.log("clear block near truck:", block, "pasteAnchor:", pasteAnchor, "fenceAnchor:", fenceAnchor);
  await panTo(page, Math.round((truckAnchor.x + fenceAnchor.x) / 2), Math.round((truckAnchor.y + fenceAnchor.y) / 2), 0);
  await step("place an isolated fence (slot 89) for steps 3/4", async () => {
    await armPaletteItem(page, FENCE_TERM, FENCE_FILE);
    await clickTile(page, fenceAnchor.x, fenceAnchor.y);
    const placed = await pollUntil(async () => {
      parsed = await getParsed(sessionId);
      const g = fenceAnchor.y * parsed.cols + fenceAnchor.x;
      return countEntries(parsed.structs, g) > 0 ? true : null;
    });
    record("fence placed on the structs layer", !!placed, JSON.stringify(parsed.structs[fenceAnchor.y * parsed.cols + fenceAnchor.x]));
  });
  await page.keyboard.press("Escape"); await page.waitForTimeout(150);

  // ── Step 2 (literal ask): FENCE ghost near the EXISTING truck (same
  // pan — truckAnchor is already on-screen, no re-pan needed). ──
  await step("2. FENCE ghost near the existing truck -> blocking + RING", async () => {
    await armPaletteItem(page, FENCE_TERM, FENCE_FILE);
    // Sanity check first: hovering an ACTUAL footprint tile of the truck
    // should be TILE-blocking (proves the truck is oracle-visible at all —
    // it is, being on the structs layer per finding #2).
    const sanity = await checkAt(page, overlapCell.x, overlapCell.y, { timeout: 600 });
    record("2a. sanity: fence ghost ON the truck's own footprint -> TILE-blocking",
      sanity.blocking, JSON.stringify(sanity.status));
    const png = await canvasPngDataUrl(page, 0);
    if (png.dataUrl) writeDataUrlPng(png.dataUrl, path.join(SHOTS, "p2_fence_on_truck_tile_v101.png"));
    // The actual ask: hover the true RING-adjacent cell (8-neighbour of the
    // footprint, not overlapping it).
    const r = await checkAt(page, ringCell.x, ringCell.y, { timeout: 400 });
    const t = normStatus(r.status);
    record("2b. fence ghost at the RING-adjacent cell -> blocking with 'RING' in status",
      r.blocking && t.includes("RING"),
      `blocking=${r.blocking} advisory=${r.advisory} status=${JSON.stringify(t)} (see header finding #3 — ring_violators() is gated on the CANDIDATE being vehicle/landmark; a fence candidate can never trigger it, however close)`);
  });
  await page.keyboard.press("Escape"); await page.waitForTimeout(150);

  // ── Step 3 (the reachable direction, per finding #2): select + copy the
  // EXISTING truck, paste it as a ghost, hover it next to the fence. ──
  await step("3. copy/paste TRUCK ghost near the fence -> blocking + RING", async () => {
    // Click-to-SELECT needs the sprite's drawn pixels, and
    // this sprite's visible body sits somewhere over its west-extending
    // footprint, not necessarily at the anchor's own diamond — sweep a few
    // candidate tiles x a few vertical offsets.
    const candidates = [truckAnchor,
      { x: truckAnchor.x - 2, y: truckAnchor.y }, { x: truckAnchor.x - 3, y: truckAnchor.y - 1 },
      { x: truckAnchor.x - 1, y: truckAnchor.y }, { x: truckAnchor.x - 4, y: truckAnchor.y }];
    let selectedText = "";
    outer: for (const c of candidates) {
      for (const dyPx of [-10, -20, -30, -40, -50, 0]) {
        await clickTile(page, c.x, c.y, { dyPx });
        selectedText = normStatus(await statusText(page));
        if (selectedText.includes("1 selected")) break outer;
        await page.keyboard.press("Escape"); await page.waitForTimeout(100);
      }
    }
    record("3a. click-select the existing truck -> '1 selected'", selectedText.includes("1 selected"), JSON.stringify(selectedText));

    await page.keyboard.press("Control+c");
    await page.waitForTimeout(300);
    await page.keyboard.press("Escape"); await page.waitForTimeout(300); // drop the selection only — clipboard survives (see header)

    // No re-pan: the fence is already on-screen in the same view opened
    // before step 2 (a second panTo to a tile far from the first — see
    // header — is what actually broke; a short hop within the current
    // view is fine, and no pan at all is safest).
    let armedForPaste = null;
    for (let attempt = 0; attempt < 3 && !armedForPaste; attempt++) {
      await page.keyboard.press("Control+v");
      armedForPaste = await pollUntil(async () => (await statusText(page))?.includes("place") ? true : null, { timeout: 2000 });
      if (!armedForPaste) { await page.keyboard.press("Escape"); await page.waitForTimeout(200); }
    }
    record("3b. Ctrl+V arms a ghost of the copied truck", !!armedForPaste);

    // Hover the paste-ghost at the pre-computed anchor (see the block
    // layout comment above) — its footprint's east edge lands exactly 1
    // tile (an 8-neighbour) west of the fence, overlapping nothing.
    const r = await checkAt(page, pasteAnchor.x, pasteAnchor.y, { timeout: 400 });
    const t = normStatus(r.status);
    record("3c. pasted truck ghost adjacent to the fence -> blocking with 'RING' in status",
      r.blocking && t.includes("RING"), `blocking=${r.blocking} advisory=${r.advisory} status=${JSON.stringify(t)}`);
    const png = await canvasPngDataUrl(page, 0);
    if (png.dataUrl) writeDataUrlPng(png.dataUrl, path.join(SHOTS, "p2_ring_red_v101.png"));
    else record("3d. ghost canvas toDataURL", false, png.error);
  });
  await page.keyboard.press("Escape"); await page.waitForTimeout(150); // cancel the paste — never commit it

  // ── Step 4: advisory yellow via a scatter-category item ──
  await step("4. scatter ghost (wasteland_crate) -> advisory (yellow), and a click on advisory places", async () => {
    // No pan here either — fence and truck are both already on-screen in
    // the one view opened before step 2 (see header re: repeated panTo).
    await armPaletteItem(page, SCATTER_TERM, SCATTER_FILE);
    // ~20 probe positions: the fence's own 8-neighbour ring plus the
    // truck's footprint 8-neighbour ring (both real "structs"-layer
    // obstacles the crate candidate — itself family "furniture" ->
    // structs, oracle category "scatter", so INVERSION-eligible per
    // header finding #1's layer gate — could plausibly draw over).
    const fx = fenceAnchor.x, fy = fenceAnchor.y;
    const fenceRing = [[fx-1,fy-1],[fx,fy-1],[fx+1,fy-1],[fx-1,fy],[fx+1,fy],[fx-1,fy+1],[fx,fy+1],[fx+1,fy+1],[fx+2,fy],[fx,fy+2]];
    let probes = fenceRing.map(([x,y]) => ({ x, y, near: "fence" }));
    let advisoryHit = null;
    for (const p of probes) {
      const r = await checkAt(page, p.x, p.y, { timeout: 350 });
      if (r.advisory) { advisoryHit = { ...p, ...r }; break; }
    }
    if (!advisoryHit) {
      // second batch: around the truck's footprint ring (same view, no re-pan)
      await page.keyboard.press("Escape"); await page.waitForTimeout(150);
      await armPaletteItem(page, SCATTER_TERM, SCATTER_FILE);
      const ax = truckAnchor.x, ay = truckAnchor.y;
      const truckRing = [];
      for (let x = ax - 6; x <= ax + 1; x++) { truckRing.push([x, ay - 2]); truckRing.push([x, ay + 1]); }
      for (let y = ay - 1; y <= ay; y++) { truckRing.push([ax - 6, y]); truckRing.push([ax + 1, y]); }
      for (const [x, y] of truckRing) {
        const r = await checkAt(page, x, y, { timeout: 350 });
        if (r.advisory) { advisoryHit = { x, y, near: "truck", ...r }; break; }
      }
    }
    record("4a. an advisory (yellow) verdict appeared in ~20 probes near the fence/truck",
      !!advisoryHit,
      advisoryHit ? JSON.stringify(advisoryHit)
        : "none of the ~20 probes produced advisory — CONTACT is gated to BIG (vehicle/landmark) candidates in sitekit/world.py, and this scatter sprite's drawn pixels never overlapped a neighbour's for INVERSION either; per the task's own fallback clause, recording honestly rather than faking a yellow verdict");
    if (advisoryHit) {
      const png = await canvasPngDataUrl(page, 0);
      if (png.dataUrl) writeDataUrlPng(png.dataUrl, path.join(SHOTS, "p2_yellow_v101.png"));
      await clickTile(page, advisoryHit.x, advisoryHit.y);
      const g = advisoryHit.y * parsed.cols + advisoryHit.x;
      const placed = await pollUntil(async () => {
        parsed = await getParsed(sessionId);
        return (countEntries(parsed.structs, g) > 0 || countEntries(parsed.objs, g) > 0) ? true : null;
      }, { timeout: 4000 });
      record("4b. clicking on an advisory (yellow) tile DOES place it", !!placed, JSON.stringify(parsed.structs[g] ?? parsed.objs[g] ?? []));
    } else {
      record("4b. click-on-advisory (skipped, no advisory observed)", true, "n/a — see 4a");
    }
  });
  await page.keyboard.press("Escape"); await page.waitForTimeout(150);

  // ── Step 5: oracle-pending glyph (" …" suffix while the debounced
  // sidecar check is in flight) — informational, not a hard fail. ──
  await step("5. oracle-pending glyph observed within 300ms of a fresh hover", async () => {
    await armPaletteItem(page, FENCE_TERM, FENCE_FILE);
    await hoverTile(page, fenceAnchor.x + 3, fenceAnchor.y + 3); // a fresh, previously-unhovered tile
    const t0 = Date.now();
    let seenPending = false, lastText = null;
    while (Date.now() - t0 < 300) {
      lastText = await statusText(page);
      if (lastText && lastText.trimEnd().endsWith("…")) { seenPending = true; break; }
      await page.waitForTimeout(20);
    }
    record("5. pending glyph ('…' suffix) seen within 300ms of a fresh hover (informational)",
      true, `seen=${seenPending} lastText=${JSON.stringify(lastText)}`);
  });
  await page.keyboard.press("Escape");
} catch (e) {
  console.error("SCRIPT FAILED:", e);
  failCount++;
} finally {
  await browser.close();
  // ── Step 6: cleanup — force-delete every sidecar session opened
  // against the lab dat. No edits were ever saved (no Ctrl+S / /save). ──
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
}

console.log(`\n${results.length} checks, ${failCount} failed.`);
process.exit(failCount === 0 ? 0 : 1);
