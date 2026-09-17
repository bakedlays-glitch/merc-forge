#!/usr/bin/env node
/**
 * LIVE-verify the MapForge palette prop labels (prop_labels.json).
 *
 * Headless Playwright against the dev rig (vite :1420 -> sidecar :8000).
 * Opens a sector on the given tileset, filters the palette by each custom
 * prefix, reads every tile card (label text + tooltip), opens the inline
 * sub-picker on every multi-frame custom slot and reads the per-sub names.
 * Compares what the UI shows against (a) the sidecar palette payload and
 * (b) the committed catalog resolved through its aliases.
 *
 * Read-only: never edits or saves the session. Prints PASS/FAIL rows and
 * writes <out>/verify_T<n>.json + <out>/palette_T<n>_<prefix>_v101.png
 * (locator screenshots of the palette panel only - the iso canvas animates,
 * so a full-page screenshot would time out).
 *
 *   node verify_prop_labels.mjs --tileset 72 --dat "<abs .dat>"
 *        [--base http://localhost:1420] [--sidecar http://127.0.0.1:8000]
 *        [--xml <abs Ja2Set.dat.xml>] [--out <dir>] [--pickers 6]
 */
import { chromium } from "playwright";
import path from "node:path";
import fs from "node:fs";
import { fileURLToPath } from "node:url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const MW2 = path.resolve(__dirname, "..", "..", "..");
const argv = process.argv.slice(2);
const flag = (n, d) => { const i = argv.indexOf(n); return i >= 0 ? argv[i + 1] : d; };
const base = flag("--base", "http://localhost:1420");
const sidecar = flag("--sidecar", "http://127.0.0.1:8000");
const tileset = flag("--tileset");
const dat = flag("--dat");
const INSTALL = process.env.JA2_INSTALL
  || "C:/Games/Jagged Alliance 2/<your-1.13-install>";
const xml = flag("--xml", path.join(INSTALL, "Data-1.13", "Ja2Set.dat.xml"));
const out = flag("--out", path.join(MW2, "scratch", "prop_labels_verify"));
const maxPickers = Number(flag("--pickers", "99"));
if (!tileset || !dat) {
  console.error("usage: node verify_prop_labels.mjs --tileset N --dat <abs .dat>");
  process.exit(2);
}
fs.mkdirSync(out, { recursive: true });

// ── Expected names: the committed catalog, resolved through aliases ──
const catalog = JSON.parse(fs.readFileSync(
  path.join(MW2, "sidecar", "mercwizard_core", "mapforge", "prop_labels.json"), "utf8"));
const canon = (f) => { const k = f.toLowerCase(); return catalog.aliases[k] ?? k; };
const expectFor = (f) => catalog.sheets[canon(f)] ?? null;
const isCustom = (f) => catalog.custom_prefixes.some((p) => f.toLowerCase().startsWith(p));

// ── Backend truth: the palette payload the UI consumes ──
const palUrl = `${sidecar}/api/v1/mapforge/tileset/palette?xml=${encodeURIComponent(xml)}&tileset=${tileset}`;
const palRes = await fetch(palUrl);
if (!palRes.ok) { console.error("palette API", palRes.status, await palRes.text()); process.exit(3); }
const palette = await palRes.json();
const bySlot = new Map(palette.slots.map((s) => [s.slot, s]));
const customSlots = palette.slots.filter((s) => isCustom(s.sti_filename));

let failures = 0;
const rows = [];
const row = (r) => { rows.push(r); if (r.status === "FAIL") failures++; };

// Backend vs catalog (no browser needed).
for (const s of customSlots) {
  const exp = expectFor(s.sti_filename);
  const okName = !!exp && s.display_name === exp.name;
  const okOrigin = !!exp && (s.origin ?? "") === (exp.origin ?? "");
  const subsExp = exp?.per_sub ? Object.keys(exp.per_sub).length : 0;
  const subsGot = s.sub_names ? Object.keys(s.sub_names).length : 0;
  row({
    where: "api", slot: s.slot, file: s.sti_filename, category: s.category,
    expected: exp?.name ?? "(no catalog entry)", shown: s.display_name ?? "(none)",
    subs: `${subsGot}/${subsExp}`,
    status: okName && okOrigin && subsGot === subsExp ? "PASS" : "FAIL",
  });
}

// ── Session hygiene: a map holds ONE writable lease, so a leftover CLEAN
// session on this .dat makes the open 409 WRITABLE_SESSION_EXISTS. Close
// clean ones only; a dirty session is someone's unsaved work - never forced.
const sameDat = (p) => path.normalize(String(p)).toLowerCase() === path.normalize(dat).toLowerCase();
async function closeCleanSessions(tag) {
  const list = await (await fetch(`${sidecar}/api/v1/mapforge/sessions`)).json();
  for (const s of list) {
    if (!sameDat(s.dat_path)) continue;
    if (s.dirty || s.edit_count || s.recovery) { console.error(`${tag}: session ${s.session_id} on this map is DIRTY - not closing`); process.exit(5); }
    const r = await fetch(`${sidecar}/api/v1/mapforge/sessions/${s.session_id}`, { method: "DELETE" });
    console.log(`${tag}: closed clean session ${s.session_id} (${r.status})`);
  }
}
await closeCleanSessions("pre");

// ── UI ──
const params = new URLSearchParams({ dat, xml, tileset: String(tileset), demo: "1" });
const url = `${base}/mapforge/sector?${params}`;
console.log("open:", url);
const browser = await chromium.launch({ headless: true });
const page = await browser.newPage({ viewport: { width: 1920, height: 1400 } });
page.on("pageerror", (e) => console.error("PAGE ERROR:", e.message));
await page.goto(url, { waitUntil: "domcontentloaded" });
page.on("console", (m) => { if (m.type() === "error") console.error("CONSOLE:", m.text().slice(0, 300)); });
try {
  await page.waitForFunction(
    () => window.__mapforgeDemo?.getState?.()?.ready === true,
    null, { timeout: 240000 },
  );
} catch (e) {
  // Dump what the page is showing (a modal? an error banner?) before bailing.
  const text = await page.evaluate(() => document.body.innerText.slice(0, 3000));
  console.error("NOT READY:", e.message, "\n--- page text ---\n", text);
  await page.screenshot({ path: path.join(out, `notready_T${tileset}_v101.png`), animations: "disabled", timeout: 15000 }).catch(() => {});
  await browser.close();
  process.exit(4);
}
const search = page.locator('input[placeholder="Filter by name / slot…"]');
await search.waitFor({ state: "visible", timeout: 60000 });
const panel = search.locator("xpath=ancestor::div[contains(@class,'bg-gray-950')][1]");
await panel.locator('[role="status"][aria-busy="true"]').waitFor({ state: "hidden", timeout: 180000 });
// The sub-picker thumbs draw from the client-side atlas; right after "ready"
// only the map's PARTIAL atlas is loaded, so subs not painted in this map show
// "?" (not in atlas) until the full atlas lands. --settle <ms> waits it out.
await page.waitForTimeout(Number(flag("--settle", "0")));

const cardSel = 'button[title*="slot "]';
const readCards = () => panel.locator(cardSel).evaluateAll((btns) => btns.map((b) => {
  const divs = [...b.children].filter((e) => e.tagName === "DIV");
  return {
    title: b.getAttribute("title") ?? "",
    label: divs.at(-2)?.textContent ?? "",
    slot: Number((divs.at(-1)?.textContent ?? "s-1").slice(1)),
    opensPicker: (b.getAttribute("title") ?? "").includes("pick a sub-frame"),
  };
}));

const seen = new Map(); // slot -> card
let pickersOpened = 0;
for (const prefix of catalog.custom_prefixes) {
  await search.fill(prefix);
  await page.waitForTimeout(900); // search debounce
  const cards = await readCards();
  await panel.screenshot({
    path: path.join(out, `palette_T${tileset}_${prefix.replace(/[^a-z0-9]/gi, "")}_v101.png`),
    animations: "disabled", timeout: 15000,
  });
  for (const c of cards) {
    if (seen.has(c.slot)) continue;
    seen.set(c.slot, c);
    const api = bySlot.get(c.slot);
    if (!api || !isCustom(api.sti_filename)) continue; // stock art matched the filter text
    const exp = expectFor(api.sti_filename);
    const okLabel = !!exp && c.label === exp.name;
    const okTip = c.title.includes(api.sti_filename) && (!exp?.origin || c.title.includes(exp.origin));
    row({
      where: "ui", slot: c.slot, file: api.sti_filename, category: api.category,
      expected: exp?.name ?? "(no catalog entry)", shown: c.label,
      subs: okTip ? "tip ok" : "tip MISSING file/origin",
      status: okLabel && okTip ? "PASS" : "FAIL",
    });
    // Sub-picker: per-sub names.
    if (c.opensPicker && pickersOpened < maxPickers) {
      pickersOpened++;
      const card = panel.locator(cardSel).filter({ hasText: `s${c.slot}` }).first();
      await card.click();
      const picker = panel.locator("div.border-blue-700").first();
      await picker.waitFor({ state: "visible", timeout: 15000 });
      await picker.locator('button[title*="sub "]').first().waitFor({ state: "visible", timeout: 30000 });
      const header = (await picker.locator("span").first().textContent()) ?? "";
      const subs = await picker.locator('button[title*="sub "]').evaluateAll((bs) => bs.map((b) => ({
        // Last span = the label; AtlasSubThumb renders a placeholder span while loading.
        title: b.getAttribute("title") ?? "", text: [...b.querySelectorAll("span")].at(-1)?.textContent ?? "",
      })));
      if (exp?.per_sub || api.sub_names) {
        await picker.screenshot({
          path: path.join(out, `subs_T${tileset}_s${c.slot}_${api.sti_filename.replace(/\.sti$/i, "")}_v101.png`),
          animations: "disabled", timeout: 15000,
        });
      }
      const expSubs = api.sub_names ?? {};
      let bad = 0;
      subs.forEach((sb, i) => {
        const want = expSubs[String(i + 1)];
        if (want && sb.text !== want) bad++;
        if (!want && !/^sub \d+$/.test(sb.text)) bad++;
      });
      const okHeader = !!exp && header.includes(exp.name) && header.includes(api.sti_filename);
      row({
        where: "ui-subs", slot: c.slot, file: api.sti_filename, category: api.category,
        expected: `${Object.keys(expSubs).length} named subs`, shown: `${subs.length} subs, ${bad} wrong`,
        subs: okHeader ? "header ok" : `header: ${header.slice(0, 60)}`,
        status: bad === 0 && okHeader ? "PASS" : "FAIL",
      });
      await picker.locator('button[title="Close subframe picker"]').click();
      await picker.waitFor({ state: "hidden", timeout: 10000 });
    }
  }
}
await search.fill("");

// Custom slots the API serves but the UI never listed (shadow-paired / engine cap).
for (const s of customSlots) {
  if (!seen.has(s.slot)) {
    row({
      where: "ui", slot: s.slot, file: s.sti_filename, category: s.category,
      expected: expectFor(s.sti_filename)?.name ?? "(no catalog entry)", shown: "(not listed)",
      subs: s.category === "shadow" ? "hidden shadow slot (expected)" : "hidden - not a shadow category",
      status: s.category === "shadow" ? "HIDDEN" : "FAIL",
    });
  }
}

await browser.close();
await closeCleanSessions("post");

rows.sort((a, b) => a.where.localeCompare(b.where) || a.slot - b.slot);
const pad = (s, n) => String(s).padEnd(n).slice(0, n);
console.log(`\n${pad("where", 8)}${pad("slot", 5)}${pad("file", 28)}${pad("expected", 32)}${pad("shown", 32)}${pad("note", 30)}status`);
for (const r of rows) {
  console.log(`${pad(r.where, 8)}${pad(r.slot, 5)}${pad(r.file, 28)}${pad(r.expected, 32)}${pad(r.shown, 32)}${pad(r.subs, 30)}${r.status}`);
}
const summary = {
  tileset: Number(tileset), dat, custom_slots: customSlots.length, ui_listed: seen.size,
  pickers_opened: pickersOpened, failures, rows,
};
fs.writeFileSync(path.join(out, `verify_T${tileset}.json`), JSON.stringify(summary, null, 2));
console.log(`\nT${tileset}: ${customSlots.length} custom slots in API, ${seen.size} listed in UI, ${pickersOpened} sub-pickers read, ${failures} FAIL`);
process.exit(failures ? 1 : 0);
