/**
 * Pure placement math for the mode-less MapForge editor.
 * No React, no I/O — type-only imports — so it runs under vitest standalone
 * and every rule here has a test. The route layer supplies the live data
 * (parsed sector, renderer footprints, placement tables) and turns the
 * emitted SessionEdit[] into one undo stroke.
 */
import type { JsdFootprint, LayerName, SessionEdit } from "./mapforge";
import type { OracleVerdict } from "./placementApi";
import { findShadowSlot } from "./jaSlotPairs";   // pure struct->shadow table (no I/O; vitest-safe)

export interface SpriteRef { x: number; y: number; layer: LayerName; slot: number; sub: number }
export interface GroupItem { dx: number; dy: number; layer: LayerName; slot: number; sub: number }
export interface SpriteGroup {
  sourceTileset: number; sourceSector: string; w: number; h: number; items: GroupItem[];
}
export type PlacementTest = "BOUNDS" | "ROOF" | "ROAD" | "TILE" | "RING" | "CONTACT" | "INVERSION";
export type Tier = "blocking" | "advisory";
export interface TileVerdict { x: number; y: number; test: PlacementTest | null; tier: Tier | null; detail?: string }
export interface FenceSubs { ns: number; ew: number; nw: number; ne: number; sw: number; se: number }
export interface PlacementTables {
  /** slot → category (building | landmark | vehicle | fence | sign | scatter | vegetation). */
  categories: Record<string, string>;
  /** "slot.sub" → category override. */
  subs: Record<string, string>;
  /** test name → "blocking" | "advisory" | "big-blocking" (sitekit t<N>_categories.json). */
  tiers: Record<string, string>;
  fences: Record<string, FenceSubs>;
  roadSlot: number;
}
export interface ParsedLayers {
  cols: number; rows: number;
  structs: number[][][]; shadows: number[][][]; objs: number[][][]; roofs: number[][][];
  onroofs: number[][][];
}
export type FootprintFn = (slot: number) => JsdFootprint | null;
export type Occupancy = Map<number, { ref: SpriteRef; cat: string }[]>;

export const EMPTY_TABLES: PlacementTables = {
  categories: {}, subs: {}, tiers: {}, fences: {}, roadSlot: 50,
};
/** Categories sitekit treats as solid instances; anything else = ground. */
export const SOLID = new Set(["building", "landmark", "vehicle", "fence", "sign", "scatter", "vegetation"]);
export const BIG = new Set(["vehicle", "landmark"]);

export function refKey(r: SpriteRef): string {
  return `${r.x},${r.y},${r.layer},${r.slot},${r.sub}`;
}

export function categoryOf(t: PlacementTables, slot: number, sub: number): string | null {
  return t.subs[`${slot}.${sub}`] ?? t.categories[String(slot)] ?? null;
}

/** Same-tile pairs sitekit accepts by construction (World.tile_pair_ok). */
export function tilePairOk(a: string, b: string): boolean {
  if (a === b && (a === "building" || a === "fence")) return true;
  return (a === "building" && b === "sign") || (a === "sign" && b === "building");
}

/** Expand a (slot, sub) at an anchor into its JSD footprint tiles with the
 * per-tile sub (variant stride — mirrors MapForgeSector's brushGhost). */
export function footprintTiles(
  x: number, y: number, slot: number, sub: number, fp: FootprintFn,
): { x: number; y: number; sub: number }[] {
  const f = fp(slot);
  if (!f || f.tiles.length < 2) return [{ x, y, sub }];
  const stride = f.tiles.length;
  const subDelta = Math.floor((sub - 1) / stride) * stride;
  return f.tiles.map((ft) => ({ x: x + ft.bX, y: y + ft.bY, sub: ft.sub + subDelta }));
}

function inBounds(p: { cols: number; rows: number }, x: number, y: number): boolean {
  return x >= 0 && y >= 0 && x < p.cols && y < p.rows;
}

/** gridno → every categorised struct instance whose footprint covers it. */
export function buildOccupancy(p: ParsedLayers, t: PlacementTables, fp: FootprintFn): Occupancy {
  const occ: Occupancy = new Map();
  for (let g = 0; g < p.structs.length; g++) {
    const ents = p.structs[g];
    if (!ents || ents.length === 0) continue;
    const x = g % p.cols; const y = Math.floor(g / p.cols);
    for (const e of ents) {
      const slot = e[0] ?? 0; const sub = e[1] ?? 0;
      const cat = categoryOf(t, slot, sub);
      if (!cat || !SOLID.has(cat)) continue;
      const ref: SpriteRef = { x, y, layer: "structs", slot, sub };
      for (const ft of footprintTiles(x, y, slot, sub, fp)) {
        if (!inBounds(p, ft.x, ft.y)) continue;
        const k = ft.y * p.cols + ft.x;
        const list = occ.get(k) ?? [];
        list.push({ ref, cat });
        occ.set(k, list);
      }
    }
  }
  return occ;
}

function tierFor(t: PlacementTables, test: PlacementTest, cat: string | null): Tier {
  const raw = t.tiers[test] ?? "blocking";
  if (raw === "big-blocking") return cat && BIG.has(cat) ? "blocking" : "advisory";
  return raw === "advisory" ? "advisory" : "blocking";
}

/** Instant per-tile validity: BOUNDS / ROOF / ROAD / TILE. `ignore` holds
 * refKeys of instances the check must not collide with (the candidate's own
 * current position during a nudge/move). Earlier candidates in `cands`
 * count as occupied for later ones (group paste, queue). */
export function localCheck(
  p: ParsedLayers, t: PlacementTables, occ: Occupancy, cands: SpriteRef[], fp: FootprintFn,
  ignore: Set<string> = new Set(),
): TileVerdict[] {
  const out: TileVerdict[] = [];
  const batch = new Map<number, string>();   // gridno → category of an earlier candidate
  for (const c of cands) {
    const cat = categoryOf(t, c.slot, c.sub);
    const solidStruct = c.layer === "structs" && cat !== null && SOLID.has(cat);
    for (const ft of footprintTiles(c.x, c.y, c.slot, c.sub, fp)) {
      let test: PlacementTest | null = null;
      let detail: string | undefined;
      if (!inBounds(p, ft.x, ft.y)) {
        test = "BOUNDS";
      } else if (solidStruct) {
        const g = ft.y * p.cols + ft.x;
        if ((p.roofs[g]?.length ?? 0) > 0) test = "ROOF";
        else if ((p.objs[g] ?? []).some((e) => e[0] === t.roadSlot)) test = "ROAD";
        else {
          const prior = batch.get(g);
          if (prior !== undefined && !tilePairOk(prior, cat!)) {
            test = "TILE"; detail = `TILE: overlaps a queued ${prior}`;
          } else {
            for (const o of occ.get(g) ?? []) {
              if (ignore.has(refKey(o.ref))) continue;
              if (tilePairOk(o.cat, cat!)) continue;
              test = "TILE";
              detail = `TILE: ${o.cat} (${o.ref.slot},${o.ref.sub})@(${o.ref.x},${o.ref.y})`;
              break;
            }
          }
        }
      }
      out.push({ x: ft.x, y: ft.y, test, tier: test ? tierFor(t, test, cat) : null, ...(detail ? { detail } : {}) });
      if (solidStruct && inBounds(p, ft.x, ft.y)) batch.set(ft.y * p.cols + ft.x, cat!);
    }
  }
  return out;
}

export function worstOf(v: TileVerdict[]): "ok" | "advisory" | "blocking" {
  let w: "ok" | "advisory" | "blocking" = "ok";
  for (const t of v) {
    if (t.tier === "blocking") return "blocking";
    if (t.tier === "advisory") w = "advisory";
  }
  return w;
}

/** blocking > advisory > null — for picking the more severe of two
 * verdicts about the SAME tile. */
function tierRank(t: Tier | null): number {
  return t === "blocking" ? 2 : t === "advisory" ? 1 : 0;
}

/** Collapse a verdict list down to the single worst verdict per (x,y)
 * tile. Ties (equal tier) keep the FIRST verdict seen, so its `detail`
 * string wins. Two co-located candidates in the same group legitimately
 * share a tile (building+building / building+sign, per `tilePairOk`), so
 * either `oracleToVerdicts` or the oracle pass as a whole can otherwise
 * emit two different verdicts for one tile — without this, whichever one
 * happened to land last would silently win, letting an advisory hide a
 * blocking finding underneath it. */
function worstPerTile(verdicts: TileVerdict[]): Map<string, TileVerdict> {
  const out = new Map<string, TileVerdict>();
  for (const v of verdicts) {
    const k = `${v.x},${v.y}`;
    const prev = out.get(k);
    if (!prev || tierRank(v.tier) > tierRank(prev.tier)) out.set(k, v);
  }
  return out;
}

/** Merge the instant local pass with the debounced sidecar oracle pass
 *: keyed by `${x},${y}`. A local BLOCKING verdict always wins
 * (never downgraded by a slower oracle round-trip). Otherwise, when the
 * oracle has an entry for the same tile, it REPLACES the local one — this
 * is how the oracle upgrades a local null/advisory tile to red/yellow
 * (RING/CONTACT/INVERSION). It can never clear a local advisory back to
 * green: `oracleToVerdicts` drops every `ok:true` row, so a tile the
 * oracle found clean simply has no entry here and the local verdict is
 * kept as-is. Tiles the oracle reports that the local pass never touched
 * (a differently-shaped check) are appended. The oracle side is collapsed
 * through `worstPerTile` first — two co-located candidates can each
 * report a different verdict for a tile they share, and the worse one
 * (never whichever happened to be listed last) is what must win. */
export function mergeVerdicts(local: TileVerdict[], oracle: TileVerdict[]): TileVerdict[] {
  const key = (v: TileVerdict) => `${v.x},${v.y}`;
  const oracleByKey = worstPerTile(oracle);
  const seen = new Set<string>();
  const out: TileVerdict[] = [];
  for (const l of local) {
    const k = key(l);
    seen.add(k);
    if (l.tier === "blocking") { out.push(l); continue; }
    out.push(oracleByKey.get(k) ?? l);
  }
  for (const [k, o] of oracleByKey) {
    if (!seen.has(k)) out.push(o);
  }
  return out;
}

/** The per-candidate sidecar response → the TileVerdict shape
 * `mergeVerdicts` merges. Only refusals (`!ok`) produce verdicts. StarCraft
 * paints the whole GHOST red on a conflict, not just the tile it conflicts
 * with, so this emits one verdict per footprint tile of the candidate
 * (`footprintTiles` — the same expansion the ghost itself is drawn with),
 * plus the sidecar-reported offending tile (`tile`) when it isn't already
 * one of them, so the user also sees what it's conflicting with. A
 * defensive `test === null` guard skips a malformed refusal row rather
 * than emitting a blank verdict. Two DIFFERENT candidates in the same
 * check batch can legitimately share a footprint tile (building+building
 * / building+sign, per `tilePairOk`) and disagree about it — `worstPerTile`
 * collapses those down to the single worst verdict per tile so a later
 * advisory row can never quietly overwrite an earlier blocking one (or
 * vice versa, depending on response order). */
export function oracleToVerdicts(r: OracleVerdict[], fp: FootprintFn): TileVerdict[] {
  const raw: TileVerdict[] = [];
  for (const { x, y, slot, sub, ok, test, tier, tile, detail } of r) {
    if (ok || test === null) continue;
    const footprint = footprintTiles(x, y, slot, sub, fp);
    for (const ft of footprint) {
      raw.push({ x: ft.x, y: ft.y, test, tier, detail: detail ?? undefined });
    }
    if (tile && !footprint.some((ft) => ft.x === tile[0] && ft.y === tile[1])) {
      raw.push({ x: tile[0], y: tile[1], test, tier, detail: detail ?? undefined });
    }
  }
  return [...worstPerTile(raw).values()];
}

/** What a palette pick arms (spec D8). Ground-class art and the wall family
 * keep the drag-paint brush — walls/roofs/floors are laid in runs, and a
 * click-per-tile ghost would lose that; the palette's family keys come from
 * sidecar tile_families.py. ponytail: wall runs stay a brush; a wall
 * line-drag with socket_lut subs is the upgrade path (fence line-drag P4). */
export const BRUSH_FAMILIES = new Set(["floor", "shadow", "wall", "door", "window", "roof"]);
export function armKindFor(b: { layer: string; category: string }): "brush" | "ghost" {
  if (b.layer === "land" || b.layer === "roofs" || b.layer === "onroofs") return "brush";
  return BRUSH_FAMILIES.has(b.category) ? "brush" : "ghost";
}

const emptyLayers = (): Record<LayerName, number[][]> =>
  ({ land: [], objs: [], shadows: [], structs: [], roofs: [], onroofs: [] });

/** Explicit shadow riding on a struct: a shadow-layer entry sitting on the SAME
 * tile whose slot is a shadow-only slot. The engine's auto-buddy shadows are
 * never written to the shadow layer, so any STORED shadow on a struct's tile is
 * that struct's explicit shadow. This covers the engine pairings (drums 18->30,
 * vehicles 88->90 / 89->91, fence 86->87) AND the Wasteland conventions
 * (decoration tanks 53/54/55 -> fo2_prop_shadows 91) — the old `slot+1` rule
 * matched almost none of them, so delete/move orphaned nearly every prop shadow.
 * Returns the entry so callers act on its ACTUAL slot/sub, not an assumed slot+1. */
/** Copy-time explicit shadow: a MANUAL shadow-layer entry at [slot+1, sub] on the
 * struct's tile (not an engine auto-pair). Carried on COPY because paste won't
 * regenerate a non-auto shadow. Kept separate from explicitShadow (delete/move),
 * whose job is to never orphan ANY co-located shadow. */
function explicitShadowIndex(p: ParsedLayers, r: SpriteRef): number {
  if (r.layer !== "structs") return -1;
  const sh = p.shadows[r.y * p.cols + r.x] ?? [];
  for (let i = sh.length - 1; i >= 0; i--) {
    const e = sh[i];
    if (e && e[0] === r.slot + 1 && e[1] === r.sub) return i;
  }
  return -1;
}

function explicitShadow(
  p: ParsedLayers, r: SpriteRef, isShadowOnly: (slot: number) => boolean,
): { index: number; slot: number; sub: number } | null {
  if (r.layer !== "structs") return null;
  const sh = p.shadows[r.y * p.cols + r.x] ?? [];
  for (let i = sh.length - 1; i >= 0; i--) {
    const e = sh[i];
    if (e && e.length >= 2 && isShadowOnly(e[0]!)) return { index: i, slot: e[0]!, sub: e[1]! };
  }
  return null;
}

/** Selection → relative sprite group (anchors only; explicit shadows carried;
 * auto-buddy shadows dropped). Null when nothing copyable remains. */
export function sliceGroup(
  p: ParsedLayers, sel: SpriteRef[], sourceTileset: number, sourceSector: string,
  isShadowOnly: (slot: number) => boolean,
): SpriteGroup | null {
  const keep = sel.filter((r) => !(r.layer === "shadows" && isShadowOnly(r.slot)));
  if (keep.length === 0) return null;
  const minX = Math.min(...keep.map((r) => r.x)); const minY = Math.min(...keep.map((r) => r.y));
  const maxX = Math.max(...keep.map((r) => r.x)); const maxY = Math.max(...keep.map((r) => r.y));
  const items: GroupItem[] = [];
  const seen = new Set<string>();
  const push = (it: GroupItem) => {
    const k = `${it.dx},${it.dy},${it.layer},${it.slot},${it.sub}`;
    if (seen.has(k)) return; seen.add(k); items.push(it);
  };
  for (const r of keep) {
    push({ dx: r.x - minX, dy: r.y - minY, layer: r.layer, slot: r.slot, sub: r.sub });
    if (explicitShadowIndex(p, r) >= 0) {
      push({ dx: r.x - minX, dy: r.y - minY, layer: "shadows", slot: r.slot + 1, sub: r.sub });
    } else {
      // Non-slot+1 shadow: carry the co-located shadow-only entry when it's the
      // struct's canonical engine pair (drums 18->30, vehicles 89->91) or, for a
      // struct with NO engine pair (Wasteland decoration tanks 53/54/55 -> 91),
      // any co-located shadow-only entry. Paste places verbatim (no auto-pair),
      // so an uncarried shadow leaves the pasted prop shadowless.
      const esh = explicitShadow(p, r, isShadowOnly);
      const pair = findShadowSlot(r.slot);
      if (esh && (pair === null || esh.slot === pair)) {
        push({ dx: r.x - minX, dy: r.y - minY, layer: "shadows", slot: esh.slot, sub: esh.sub });
      }
    }
  }
  return { sourceTileset, sourceSector, w: maxX - minX + 1, h: maxY - minY + 1, items };
}

export function groupRefsAt(group: SpriteGroup, anchor: { x: number; y: number }): SpriteRef[] {
  return group.items.map((it) => ({ x: anchor.x + it.dx, y: anchor.y + it.dy, layer: it.layer, slot: it.slot, sub: it.sub }));
}

/** Paste = `place` ops ONLY (never set_entries — that would wipe co-tenants). */
export function groupPasteEdits(
  group: SpriteGroup, anchor: { x: number; y: number }, cols: number, rows: number,
): { edits: SessionEdit[]; placed: SpriteRef[]; dropped: number } {
  const edits: SessionEdit[] = []; const placed: SpriteRef[] = []; let dropped = 0;
  for (const r of groupRefsAt(group, anchor)) {
    if (!inBounds({ cols, rows }, r.x, r.y)) { dropped++; continue; }
    edits.push({ x: r.x, y: r.y, op: "place", layer: r.layer, slot: r.slot, sub: r.sub });
    placed.push(r);
  }
  return { edits, placed, dropped };
}

/** Neighbour-topology role for one fence tile (sitekit place.fence, mirrored
 * branch-for-branch): N/S-only (or empty ⇒ E/W-only, incl. no neighbours at
 * all) collapse to the straight run; the four L-corners are exact 2-item
 * matches; a junction where both N and S are present (plus an E/W leg) still
 * reads as `ns`; anything else (a 3-way E/W/N-or-S tee) falls back to `ew`. */
function fenceRole(nb: Set<"E" | "W" | "N" | "S">, subs: FenceSubs): number {
  const only = (...allowed: string[]) => [...nb].every((d) => allowed.includes(d));
  if (nb.size > 0 && only("N", "S")) return subs.ns;
  if (nb.size === 0 || only("E", "W")) return subs.ew;
  const key = [...nb].sort().join("");
  if (key === "ES") return subs.nw;
  if (key === "SW") return subs.ne;
  if (key === "EN") return subs.sw;
  if (key === "NW") return subs.se;
  if (nb.has("N") && nb.has("S")) return subs.ns;
  return subs.ew;
}

/** Fence line-drag topology (sitekit place.fence mirrored). The neighbour set
 * used for topology is the drawn line MINUS any tile it skips as `isRoad`,
 * PLUS whatever same-slot fence already stands there per `existing` — this
 * is how a new line joins an old fence run. A ROAD tile still gets a
 * computed `sub` (what would have gone there) but is marked `skipped` so
 * the caller leaves that gridno alone. `slot` is not used by the topology
 * math itself — it documents which fence family this line belongs to for
 * callers that scope `existing`/`isRoad` per-slot. */
export function fenceSubsForLine(
  line: { x: number; y: number }[], slot: number, subs: FenceSubs,
  existing: (x: number, y: number) => boolean, isRoad: (x: number, y: number) => boolean,
): { x: number; y: number; sub: number; skipped?: "ROAD" }[] {
  void slot;
  const lineKey = new Set(line.map((t) => `${t.x},${t.y}`));
  const inFenceSet = (x: number, y: number): boolean => {
    if (lineKey.has(`${x},${y}`) && !isRoad(x, y)) return true;
    return existing(x, y);
  };
  return line.map(({ x, y }) => {
    const nb = new Set<"E" | "W" | "N" | "S">();
    if (inFenceSet(x + 1, y)) nb.add("E");
    if (inFenceSet(x - 1, y)) nb.add("W");
    if (inFenceSet(x, y + 1)) nb.add("S");
    if (inFenceSet(x, y - 1)) nb.add("N");
    const sub = fenceRole(nb, subs);
    return isRoad(x, y) ? { x, y, sub, skipped: "ROAD" as const } : { x, y, sub };
  });
}

/** A StarCraft-style ghost stacked in the placement queue (Shift+click). */
export interface QueuedGhost { anchor: { x: number; y: number }; group: SpriteGroup }

/** Commit the whole queue as one stroke: `groupPasteEdits` per entry, in
 * queue order, concatenated. Fence lines never enter this queue — a line
 * commits on mouseup as its own single stroke (`fenceSubsForLine` above). */
export function queueCommitEdits(
  queue: QueuedGhost[], cols: number, rows: number,
): { edits: SessionEdit[]; placed: SpriteRef[]; dropped: number } {
  const edits: SessionEdit[] = []; const placed: SpriteRef[] = []; let dropped = 0;
  for (const q of queue) {
    const r = groupPasteEdits(q.group, q.anchor, cols, rows);
    edits.push(...r.edits); placed.push(...r.placed); dropped += r.dropped;
  }
  return { edits, placed, dropped };
}

/** Remove every selected entry (+ its explicit shadow). Per (tile, layer) the
 * removes are emitted with DESCENDING entry_index so earlier removes never
 * shift a later index. */
export function deleteEdits(
  p: ParsedLayers, sel: SpriteRef[], isShadowOnly: (slot: number) => boolean = () => false,
): { edits: SessionEdit[]; touched: { x: number; y: number; layer: LayerName }[] } {
  const byTile = new Map<string, { x: number; y: number; layer: LayerName; idx: number[] }>();
  const add = (x: number, y: number, layer: LayerName, idx: number) => {
    const k = `${x},${y},${layer}`;
    const cur = byTile.get(k) ?? { x, y, layer, idx: [] };
    if (!cur.idx.includes(idx)) cur.idx.push(idx);
    byTile.set(k, cur);
  };
  for (const r of sel) {
    const arr = (p as unknown as Record<string, number[][][] | undefined>)[r.layer];
    if (!arr) continue;
    const list = arr[r.y * p.cols + r.x] ?? [];
    for (let i = list.length - 1; i >= 0; i--) {
      const e = list[i];
      if (e && e[0] === r.slot && e[1] === r.sub) { add(r.x, r.y, r.layer, i); break; }
    }
    const esh = explicitShadow(p, r, isShadowOnly);
    if (esh) add(r.x, r.y, "shadows", esh.index);
  }
  const edits: SessionEdit[] = []; const touched: { x: number; y: number; layer: LayerName }[] = [];
  for (const t of byTile.values()) {
    touched.push({ x: t.x, y: t.y, layer: t.layer });
    for (const i of [...t.idx].sort((a, b) => b - a)) {
      edits.push({ x: t.x, y: t.y, op: "remove", layer: t.layer, entry_index: i });
    }
  }
  return { edits, touched };
}

/** Nudge/move: removes first, then places at (x+dx, y+dy). Explicit shadows
 * follow their struct and are placed once even when the selection already
 * names the shadow ref directly. All-or-nothing: if ANY destination is
 * off-map, nothing is removed or placed — `dropped` reports the count. */
export function moveEdits(
  p: ParsedLayers, sel: SpriteRef[], dx: number, dy: number, isShadowOnly: (slot: number) => boolean = () => false,
): { edits: SessionEdit[]; moved: SpriteRef[]; touched: { x: number; y: number; layer: LayerName }[]; dropped: number } {
  const dests = sel.map((r) => ({ r, nx: r.x + dx, ny: r.y + dy }));
  const dropped = dests.filter((d) => !inBounds(p, d.nx, d.ny)).length;
  if (dropped > 0) return { edits: [], moved: [], touched: [], dropped };

  const del = deleteEdits(p, sel, isShadowOnly);
  const edits = [...del.edits]; const moved: SpriteRef[] = [];
  const touched = [...del.touched];
  const placedKey = new Set<string>();
  const isExplicitShadowOfSelected = (r: SpriteRef) => r.layer === "shadows" && isShadowOnly(r.slot) && sel.some(
    (o) => o.layer === "structs" && o.x === r.x && o.y === r.y,
  );
  const place = (x: number, y: number, layer: LayerName, slot: number, sub: number) => {
    const key = `${x},${y},${layer},${slot},${sub}`;
    if (placedKey.has(key)) return;
    placedKey.add(key);
    edits.push({ x, y, op: "place", layer, slot, sub });
    touched.push({ x, y, layer });
  };
  for (const { r, nx, ny } of dests) {
    if (isExplicitShadowOfSelected(r)) continue;
    place(nx, ny, r.layer, r.slot, r.sub);
    if (r.layer !== "shadows") moved.push({ x: nx, y: ny, layer: r.layer, slot: r.slot, sub: r.sub });
    const esh = explicitShadow(p, r, isShadowOnly);
    if (esh) place(nx, ny, "shadows", esh.slot, esh.sub);
  }
  return { edits, moved, touched, dropped: 0 };
}

export function cycleSub(sub: number, subCount: number, dir: 1 | -1): number {
  if (subCount <= 1) return sub;
  return ((sub - 1 + dir + subCount) % subCount) + 1;
}

/** Group → tiles for IsoRenderer.renderRegionToCanvas, JSD footprints expanded. */
export function groupToRegionTiles(
  group: SpriteGroup, fp: FootprintFn,
): { dx: number; dy: number; layers: Record<LayerName, number[][]> }[] {
  const byTile = new Map<string, { dx: number; dy: number; layers: Record<LayerName, number[][]> }>();
  for (const it of group.items) {
    const tiles = it.layer === "structs"
      ? footprintTiles(it.dx, it.dy, it.slot, it.sub, fp)
      : [{ x: it.dx, y: it.dy, sub: it.sub }];
    for (const t of tiles) {
      const k = `${t.x},${t.y}`;
      const cur = byTile.get(k) ?? { dx: t.x, dy: t.y, layers: emptyLayers() };
      cur.layers[it.layer].push([it.slot, t.sub]);
      byTile.set(k, cur);
    }
  }
  return [...byTile.values()];
}

// Re-export so later tasks import one module for edits too.
export type { SessionEdit };
