import { describe, it, expect } from "vitest";
import {
  categoryOf, footprintTiles, buildOccupancy, localCheck, worstOf, refKey,
  sliceGroup, groupRefsAt, groupPasteEdits, deleteEdits, moveEdits, cycleSub, groupToRegionTiles,
  mergeVerdicts, armKindFor, oracleToVerdicts, fenceSubsForLine, queueCommitEdits,
  type PlacementTables, type ParsedLayers, type SpriteRef, type TileVerdict, type FenceSubs,
} from "../mapPlacement";
import type { JsdFootprint } from "../mapforge";

const T: PlacementTables = {
  categories: { "79": "vehicle", "86": "fence", "12": "vegetation", "36": "building", "57": "sign" },
  subs: { "79.3": "landmark" },
  tiers: { TILE: "blocking", ROAD: "blocking", RING: "blocking", CONTACT: "big-blocking", INVERSION: "big-blocking" },
  fences: {},
  roadSlot: 50,
};
// slot 79 = a 2-tile truck: anchor + the tile to its WEST (bX=-1)
const FP: Record<number, JsdFootprint> = {
  79: { tiles: [{ bX: 0, bY: 0, sub: 1 }, { bX: -1, bY: 0, sub: 2 }] } as JsdFootprint,
};
const fp = (slot: number) => FP[slot] ?? null;

function empty(cols = 8, rows = 8): ParsedLayers {
  const n = cols * rows;
  const mk = () => Array.from({ length: n }, () => [] as number[][]);
  return { cols, rows, structs: mk(), shadows: mk(), objs: mk(), roofs: mk(), onroofs: mk() };
}
const g = (p: ParsedLayers, x: number, y: number) => y * p.cols + x;

describe("categoryOf", () => {
  it("prefers the sub override, then the slot, else null", () => {
    expect(categoryOf(T, 79, 3)).toBe("landmark");
    expect(categoryOf(T, 79, 1)).toBe("vehicle");
    expect(categoryOf(T, 1, 1)).toBeNull();
  });
});

describe("footprintTiles", () => {
  it("returns the anchor alone for a slot without a JSD", () => {
    expect(footprintTiles(3, 4, 86, 5, fp)).toEqual([{ x: 3, y: 4, sub: 5 }]);
  });
  it("expands a multi-tile JSD with the variant stride", () => {
    // stride 2: sub 3 is the second variant → tiles get subs 3, 4
    expect(footprintTiles(3, 4, 79, 3, fp)).toEqual([
      { x: 3, y: 4, sub: 3 }, { x: 2, y: 4, sub: 4 },
    ]);
  });
});

describe("buildOccupancy", () => {
  it("indexes every footprint tile of every categorised struct", () => {
    const p = empty();
    p.structs[g(p, 3, 4)] = [[79, 1]];
    p.structs[g(p, 0, 0)] = [[1, 1]];          // uncategorised = ground, never solid
    const occ = buildOccupancy(p, T, fp);
    expect(occ.get(g(p, 3, 4))?.[0]?.cat).toBe("vehicle");
    expect(occ.get(g(p, 2, 4))?.[0]?.ref).toEqual({ x: 3, y: 4, layer: "structs", slot: 79, sub: 1 });
    expect(occ.has(g(p, 0, 0))).toBe(false);
  });
});

describe("localCheck", () => {
  const cand = (x: number, y: number, slot: number, sub = 1): SpriteRef =>
    ({ x, y, layer: "structs", slot, sub });
  it("is green on empty ground", () => {
    const p = empty();
    const v = localCheck(p, T, buildOccupancy(p, T, fp), [cand(3, 3, 86)], fp);
    expect(v).toEqual([{ x: 3, y: 3, test: null, tier: null }]);
    expect(worstOf(v)).toBe("ok");
  });
  it("flags BOUNDS per off-map footprint tile", () => {
    const p = empty();
    const v = localCheck(p, T, buildOccupancy(p, T, fp), [cand(0, 0, 79)], fp);
    expect(v.find((t) => t.x === -1)?.test).toBe("BOUNDS");
    expect(worstOf(v)).toBe("blocking");
  });
  it("flags ROOF and ROAD on struct candidates", () => {
    const p = empty();
    p.roofs[g(p, 1, 1)] = [[64, 1]];
    p.objs[g(p, 2, 2)] = [[50, 3]];
    const occ = buildOccupancy(p, T, fp);
    expect(localCheck(p, T, occ, [cand(1, 1, 86)], fp)[0]!.test).toBe("ROOF");
    expect(localCheck(p, T, occ, [cand(2, 2, 86)], fp)[0]!.test).toBe("ROAD");
  });
  it("flags TILE against an existing footprint tile, honouring same-category exemptions", () => {
    const p = empty();
    p.structs[g(p, 3, 4)] = [[79, 1]];          // truck occupies (3,4)+(2,4)
    p.structs[g(p, 5, 5)] = [[86, 1]];          // fence
    const occ = buildOccupancy(p, T, fp);
    expect(localCheck(p, T, occ, [cand(2, 4, 86)], fp)[0]!.test).toBe("TILE");
    expect(localCheck(p, T, occ, [cand(5, 5, 86)], fp)[0]!.test).toBeNull();   // fence+fence ok
    expect(localCheck(p, T, occ, [cand(5, 5, 36)], fp)[0]!.test).toBe("TILE"); // building on fence
    expect(localCheck(p, T, occ, [cand(5, 5, 57)], fp)[0]!.test).toBe("TILE"); // sign on fence
  });
  it("ignores the candidate's own current position when asked (nudge/move)", () => {
    const p = empty();
    p.structs[g(p, 3, 4)] = [[79, 1]];
    const occ = buildOccupancy(p, T, fp);
    const self = refKey({ x: 3, y: 4, layer: "structs", slot: 79, sub: 1 });
    const v = localCheck(p, T, occ, [cand(4, 4, 79)], fp, new Set([self]));
    expect(worstOf(v)).toBe("ok");
  });
  it("treats earlier candidates in the same batch as occupied", () => {
    const p = empty();
    const occ = buildOccupancy(p, T, fp);
    const v = localCheck(p, T, occ, [cand(3, 3, 86), cand(3, 3, 36)], fp);
    expect(v[1]!.test).toBe("TILE");
  });
});

const shadowOnly = (slot: number) => slot === 24;   // pretend 24 is an auto-buddy shadow slot

describe("sliceGroup", () => {
  it("stores anchors relative to the bbox and carries explicit same-tile shadows", () => {
    const p = empty();
    p.structs[g(p, 5, 6)] = [[79, 1]];
    p.shadows[g(p, 5, 6)] = [[80, 1]];          // explicit Estoni-style shadow (slot+1, same sub)
    p.structs[g(p, 2, 3)] = [[86, 4]];
    p.shadows[g(p, 2, 3)] = [[24, 4]];          // auto-buddy — must NOT be copied
    const grp = sliceGroup(p, [
      { x: 5, y: 6, layer: "structs", slot: 79, sub: 1 },
      { x: 2, y: 3, layer: "structs", slot: 86, sub: 4 },
      { x: 2, y: 3, layer: "shadows", slot: 24, sub: 4 },
    ], 72, "lab", shadowOnly)!;
    expect(grp.w).toBe(4); expect(grp.h).toBe(4);
    expect(grp.items).toEqual(expect.arrayContaining([
      { dx: 3, dy: 3, layer: "structs", slot: 79, sub: 1 },
      { dx: 3, dy: 3, layer: "shadows", slot: 80, sub: 1 },
      { dx: 0, dy: 0, layer: "structs", slot: 86, sub: 4 },
    ]));
    expect(grp.items.some((i) => i.slot === 24)).toBe(false);
  });
  it("copy carries a non-slot+1 shadow (tank 55 -> fo2_prop_shadows 91) so paste keeps it", () => {
    const p = empty();
    p.structs[g(p, 4, 4)] = [[55, 1]];
    p.shadows[g(p, 4, 4)] = [[91, 4]];            // slot 91 != 55+1; 55 has no engine pair
    const grp = sliceGroup(p, [{ x: 4, y: 4, layer: "structs", slot: 55, sub: 1 }], 72, "lab", (s) => s === 91)!;
    expect(grp.items).toEqual(expect.arrayContaining([
      { dx: 0, dy: 0, layer: "structs", slot: 55, sub: 1 },
      { dx: 0, dy: 0, layer: "shadows", slot: 91, sub: 4 },
    ]));
  });
  it("returns null for an empty selection", () => {
    expect(sliceGroup(empty(), [], 72, "lab", shadowOnly)).toBeNull();
  });
});

describe("groupPasteEdits", () => {
  const grp = { sourceTileset: 72, sourceSector: "lab", w: 2, h: 1, items: [
    { dx: 0, dy: 0, layer: "structs" as const, slot: 86, sub: 1 },
    { dx: 1, dy: 0, layer: "structs" as const, slot: 86, sub: 4 },
  ] };
  it("emits place ops only, at anchor + offset", () => {
    const r = groupPasteEdits(grp, { x: 3, y: 3 }, 8, 8);
    expect(r.edits).toEqual([
      { x: 3, y: 3, op: "place", layer: "structs", slot: 86, sub: 1 },
      { x: 4, y: 3, op: "place", layer: "structs", slot: 86, sub: 4 },
    ]);
    expect(r.placed.length).toBe(2); expect(r.dropped).toBe(0);
  });
  it("drops off-map anchors and counts them", () => {
    const r = groupPasteEdits(grp, { x: 7, y: 7 }, 8, 8);
    expect(r.edits.length).toBe(1); expect(r.dropped).toBe(1);
  });
});

describe("deleteEdits / moveEdits", () => {
  it("removes by descending entry index per tile and carries the explicit shadow", () => {
    const p = empty();
    p.structs[g(p, 5, 6)] = [[12, 1], [79, 1]];
    p.shadows[g(p, 5, 6)] = [[80, 1]];
    const r = deleteEdits(p, [{ x: 5, y: 6, layer: "structs", slot: 79, sub: 1 }], (s) => s === 80);
    expect(r.edits).toEqual([
      { x: 5, y: 6, op: "remove", layer: "structs", entry_index: 1 },
      { x: 5, y: 6, op: "remove", layer: "shadows", entry_index: 0 },
    ]);
    expect(r.touched).toEqual(expect.arrayContaining([
      { x: 5, y: 6, layer: "structs" }, { x: 5, y: 6, layer: "shadows" },
    ]));
  });
  it("two refs on one tile remove higher index first", () => {
    const p = empty();
    p.structs[g(p, 1, 1)] = [[86, 1], [86, 4]];
    const r = deleteEdits(p, [
      { x: 1, y: 1, layer: "structs", slot: 86, sub: 1 },
      { x: 1, y: 1, layer: "structs", slot: 86, sub: 4 },
    ], (s) => s === 87);
    expect(r.edits.map((e) => e.entry_index)).toEqual([1, 0]);
  });
  it("moveEdits = removes then places at the offset, returning the moved refs", () => {
    const p = empty();
    p.structs[g(p, 5, 6)] = [[79, 1]];
    p.shadows[g(p, 5, 6)] = [[80, 1]];
    const r = moveEdits(p, [{ x: 5, y: 6, layer: "structs", slot: 79, sub: 1 }], 1, 0, (s) => s === 80);
    expect(r.edits.slice(0, 2).every((e) => e.op === "remove")).toBe(true);
    expect(r.edits.slice(2)).toEqual([
      { x: 6, y: 6, op: "place", layer: "structs", slot: 79, sub: 1 },
      { x: 6, y: 6, op: "place", layer: "shadows", slot: 80, sub: 1 },
    ]);
    expect(r.moved).toEqual([{ x: 6, y: 6, layer: "structs", slot: 79, sub: 1 }]);
  });
  it("moving a struct + its explicit shadow together places the shadow once and moved holds only the struct ref", () => {
    const p = empty();
    p.structs[g(p, 5, 6)] = [[79, 1]];
    p.shadows[g(p, 5, 6)] = [[80, 1]];
    const r = moveEdits(p, [
      { x: 5, y: 6, layer: "structs", slot: 79, sub: 1 },
      { x: 5, y: 6, layer: "shadows", slot: 80, sub: 1 },
    ], 1, 0, (s) => s === 80);
    const shadowPlaces = r.edits.filter((e) => e.op === "place" && e.layer === "shadows");
    expect(shadowPlaces).toEqual([{ x: 6, y: 6, op: "place", layer: "shadows", slot: 80, sub: 1 }]);
    expect(r.moved).toEqual([{ x: 6, y: 6, layer: "structs", slot: 79, sub: 1 }]);
  });
  it("carries a NON-slot+1 explicit shadow (tank slot 55 -> fo2_prop_shadows 91) on delete and move", () => {
    const p = empty();
    p.structs[g(p, 4, 4)] = [[55, 1]];
    p.shadows[g(p, 4, 4)] = [[91, 4]];              // remapped sub, slot 91 != 55+1
    const shadow91 = (s: number) => s === 91;
    const del = deleteEdits(p, [{ x: 4, y: 4, layer: "structs", slot: 55, sub: 1 }], shadow91);
    expect(del.edits).toEqual([
      { x: 4, y: 4, op: "remove", layer: "structs", entry_index: 0 },
      { x: 4, y: 4, op: "remove", layer: "shadows", entry_index: 0 },
    ]);
    const mv = moveEdits(p, [{ x: 4, y: 4, layer: "structs", slot: 55, sub: 1 }], 1, 0, shadow91);
    expect(mv.edits.filter((e) => e.op === "place")).toEqual([
      { x: 5, y: 4, op: "place", layer: "structs", slot: 55, sub: 1 },
      { x: 5, y: 4, op: "place", layer: "shadows", slot: 91, sub: 4 },
    ]);
  });
  it("refuses an off-map move: no edits, source untouched, dropped counts it", () => {
    const p = empty();
    p.structs[g(p, 7, 7)] = [[79, 1]];
    const r = moveEdits(p, [{ x: 7, y: 7, layer: "structs", slot: 79, sub: 1 }], 1, 0, (s) => s === 80);
    expect(r.edits).toEqual([]);
    expect(r.moved).toEqual([]);
    expect(r.touched).toEqual([]);
    expect(r.dropped).toBe(1);
  });
});

describe("cycleSub", () => {
  it("wraps within 1..subCount", () => {
    expect(cycleSub(3, 3, 1)).toBe(1);
    expect(cycleSub(1, 3, -1)).toBe(3);
    expect(cycleSub(2, 3, 1)).toBe(3);
  });
});

describe("mergeVerdicts", () => {
  const v = (x: number, y: number, tier: TileVerdict["tier"], test: TileVerdict["test"] = null): TileVerdict =>
    ({ x, y, test, tier });
  it("a local blocking verdict always wins, even if the oracle disagrees", () => {
    const local = [v(1, 1, "blocking", "TILE")];
    const oracle = [v(1, 1, null)];
    expect(mergeVerdicts(local, oracle)).toEqual([v(1, 1, "blocking", "TILE")]);
  });
  it("the oracle replaces a null local verdict (can upgrade to advisory or blocking)", () => {
    const local = [v(2, 2, null)];
    const oracle = [v(2, 2, "advisory", "CONTACT")];
    expect(mergeVerdicts(local, oracle)).toEqual([v(2, 2, "advisory", "CONTACT")]);
  });
  it("an oracle verdict replaces a non-blocking local one", () => {
    const local = [v(3, 3, "advisory", "CONTACT")];
    const oracle = [v(3, 3, "blocking", "RING")];
    expect(mergeVerdicts(local, oracle)).toEqual([v(3, 3, "blocking", "RING")]);
  });
  it("keeps a local-only tile untouched when the oracle has no result for it", () => {
    const local = [v(4, 4, null)];
    expect(mergeVerdicts(local, [])).toEqual([v(4, 4, null)]);
  });
  it("appends an oracle-only tile the local pass never touched", () => {
    const local = [v(1, 1, null)];
    const oracle = [v(1, 1, null), v(9, 9, "blocking", "RING")];
    expect(mergeVerdicts(local, oracle)).toEqual([v(1, 1, null), v(9, 9, "blocking", "RING")]);
  });
  it("keeps the worst oracle verdict when two candidates share a tile, regardless of listing order", () => {
    // Two co-located candidates (legal per tilePairOk — e.g. building+sign)
    // can each get their own verdict for the shared tile; the blocking one
    // must win no matter which order the sidecar happened to list them in.
    const local = [v(7, 7, null)];
    const advisory = { x: 7, y: 7, test: "CONTACT", tier: "advisory", detail: "CONTACT: a" } as TileVerdict;
    const blocking = { x: 7, y: 7, test: "RING", tier: "blocking", detail: "RING: b" } as TileVerdict;
    const expected = [blocking];
    expect(mergeVerdicts(local, [advisory, blocking])).toEqual(expected);
    expect(mergeVerdicts(local, [blocking, advisory])).toEqual(expected);
  });
});

// oracleToVerdicts turns the sidecar response shape (one row per
// CANDIDATE, ok/test/tier/tile/detail) into the TileVerdict shape
// mergeVerdicts already knows how to merge (tested above) — no need to
// re-cover mergeVerdicts' own semantics here, just the translation plus
// one pipe-through so the real oracle payload shape is exercised end to end.
describe("oracleToVerdicts", () => {
  const noFootprint = () => null;
  it("emits two verdicts for a 1x1 candidate refused against a different tile (own tile + offender), skipping ok:true and null-test rows", () => {
    const out = oracleToVerdicts([
      { x: 5, y: 5, layer: "structs", slot: 86, sub: 1, ok: false, test: "RING", tier: "blocking", tile: [6, 5], detail: "RING: vehicle (79,1)@(6,5)" },
      { x: 9, y: 9, layer: "structs", slot: 86, sub: 1, ok: true, test: null, tier: null, tile: null, detail: null },
      { x: 3, y: 3, layer: "objs", slot: 50, sub: 1, ok: false, test: null, tier: null, tile: null, detail: null },
    ], noFootprint);
    expect(out).toEqual([
      { x: 5, y: 5, test: "RING", tier: "blocking", detail: "RING: vehicle (79,1)@(6,5)" },
      { x: 6, y: 5, test: "RING", tier: "blocking", detail: "RING: vehicle (79,1)@(6,5)" },
    ]);
  });
  it("falls back to the candidate's own (x,y) when the sidecar reports no offending tile", () => {
    const out = oracleToVerdicts([
      { x: 2, y: 7, layer: "structs", slot: 12, sub: 1, ok: false, test: "CONTACT", tier: "advisory", tile: null, detail: null },
    ], noFootprint);
    expect(out).toEqual([{ x: 2, y: 7, test: "CONTACT", tier: "advisory" }]);
  });
  it("expands a multi-tile candidate to one verdict per footprint tile, plus the offender", () => {
    // slot 79 anchored at (10,10): footprint tiles (10,10) + (9,10) (see FP above).
    const out = oracleToVerdicts([
      { x: 10, y: 10, layer: "structs", slot: 79, sub: 1, ok: false, test: "RING", tier: "blocking", tile: [11, 9], detail: "RING: fence (86,1)@(11,9)" },
    ], fp);
    expect(out).toEqual([
      { x: 10, y: 10, test: "RING", tier: "blocking", detail: "RING: fence (86,1)@(11,9)" },
      { x: 9, y: 10, test: "RING", tier: "blocking", detail: "RING: fence (86,1)@(11,9)" },
      { x: 11, y: 9, test: "RING", tier: "blocking", detail: "RING: fence (86,1)@(11,9)" },
    ]);
  });
  it("does not duplicate the offender when it's already one of the footprint tiles", () => {
    const out = oracleToVerdicts([
      { x: 10, y: 10, layer: "structs", slot: 79, sub: 1, ok: false, test: "TILE", tier: "blocking", tile: [9, 10], detail: "TILE: fence at (9,10)" },
    ], fp);
    expect(out).toEqual([
      { x: 10, y: 10, test: "TILE", tier: "blocking", detail: "TILE: fence at (9,10)" },
      { x: 9, y: 10, test: "TILE", tier: "blocking", detail: "TILE: fence at (9,10)" },
    ]);
  });
  it("an ok:true result yields no verdicts even with a multi-tile footprint", () => {
    const out = oracleToVerdicts([
      { x: 10, y: 10, layer: "structs", slot: 79, sub: 1, ok: true, test: null, tier: null, tile: null, detail: null },
    ], fp);
    expect(out).toEqual([]);
  });
  it("collapses two DIFFERENT candidates sharing a tile to the worst verdict, regardless of row order", () => {
    // building+sign (tilePairOk) can legally sit on the same tile and each
    // get its own refusal against a third party — the shared tile must not
    // silently take whichever row happened to be listed last.
    const advisoryFirst = oracleToVerdicts([
      { x: 5, y: 5, layer: "structs", slot: 36, sub: 1, ok: false, test: "CONTACT", tier: "advisory", tile: [5, 5], detail: "CONTACT: a" },
      { x: 5, y: 5, layer: "structs", slot: 57, sub: 1, ok: false, test: "RING", tier: "blocking", tile: [5, 5], detail: "RING: b" },
    ], noFootprint);
    const blockingFirst = oracleToVerdicts([
      { x: 5, y: 5, layer: "structs", slot: 57, sub: 1, ok: false, test: "RING", tier: "blocking", tile: [5, 5], detail: "RING: b" },
      { x: 5, y: 5, layer: "structs", slot: 36, sub: 1, ok: false, test: "CONTACT", tier: "advisory", tile: [5, 5], detail: "CONTACT: a" },
    ], noFootprint);
    const expected = [{ x: 5, y: 5, test: "RING", tier: "blocking", detail: "RING: b" }];
    expect(advisoryFirst).toEqual(expected);
    expect(blockingFirst).toEqual(expected);
  });
  it("feeds mergeVerdicts so a local blocking verdict still beats the oracle", () => {
    const oracle = oracleToVerdicts([
      { x: 5, y: 5, layer: "structs", slot: 86, sub: 1, ok: false, test: "RING", tier: "blocking", tile: [5, 5], detail: "RING: vehicle (79,1)@(6,5)" },
    ], noFootprint);
    const local: TileVerdict[] = [{ x: 5, y: 5, test: "TILE", tier: "blocking" }];
    expect(mergeVerdicts(local, oracle)).toEqual(local);
  });
});

describe("armKindFor", () => {
  it("ground-class layers always arm a brush", () => {
    expect(armKindFor({ layer: "land", category: "floor" })).toBe("brush");
  });
  it("the wall family arms a brush even on the structs layer", () => {
    expect(armKindFor({ layer: "structs", category: "wall" })).toBe("brush");
  });
  it("a vehicle on structs arms a ghost", () => {
    expect(armKindFor({ layer: "structs", category: "vehicle" })).toBe("ghost");
  });
  it("scatter on objs arms a ghost", () => {
    expect(armKindFor({ layer: "objs", category: "scatter" })).toBe("ghost");
  });
});

describe("groupToRegionTiles", () => {
  it("expands JSD footprints so the ghost shows the whole struct", () => {
    const grp = { sourceTileset: 72, sourceSector: "lab", w: 1, h: 1, items: [
      { dx: 1, dy: 0, layer: "structs" as const, slot: 79, sub: 1 },
    ] };
    const tiles = groupToRegionTiles(grp, fp);
    expect(tiles).toEqual([
      { dx: 1, dy: 0, layers: { land: [], objs: [], shadows: [], structs: [[79, 1]], roofs: [], onroofs: [] } },
      { dx: 0, dy: 0, layers: { land: [], objs: [], shadows: [], structs: [[79, 2]], roofs: [], onroofs: [] } },
    ]);
  });
});

// slot 86 wirefenc (has distinct corner art) and slot 77 junkfen (no corner
// art — the table just repeats the ns piece for every corner), per the
// approved T72 table.
const WIREFENC: FenceSubs = { ns: 7, ew: 6, nw: 2, ne: 2, sw: 2, se: 2 };
const JUNKFEN: FenceSubs = { ns: 2, ew: 1, nw: 2, ne: 2, sw: 2, se: 2 };
// Distinct per-corner values so the four corner tests below actually pin
// WHICH branch fired — WIREFENC's corners are all the same sub (2), so it
// can't tell nw from ne from sw from se apart.
const DISTINCTFENC: FenceSubs = { ns: 7, ew: 6, nw: 11, ne: 12, sw: 13, se: 14 };
const noExisting = () => false;
const noRoad = () => false;

describe("fenceSubsForLine", () => {
  it("a horizontal 3-tile line is all ew (neighbours are E/W-only or empty)", () => {
    const line = [{ x: 0, y: 0 }, { x: 1, y: 0 }, { x: 2, y: 0 }];
    const out = fenceSubsForLine(line, 86, WIREFENC, noExisting, noRoad);
    expect(out).toEqual([
      { x: 0, y: 0, sub: 6 }, { x: 1, y: 0, sub: 6 }, { x: 2, y: 0, sub: 6 },
    ]);
  });
  it("a vertical 3-tile line is all ns (neighbours are N/S-only)", () => {
    const line = [{ x: 0, y: 0 }, { x: 0, y: 1 }, { x: 0, y: 2 }];
    const out = fenceSubsForLine(line, 86, WIREFENC, noExisting, noRoad);
    expect(out).toEqual([
      { x: 0, y: 0, sub: 7 }, { x: 0, y: 1, sub: 7 }, { x: 0, y: 2, sub: 7 },
    ]);
  });
  it("an L (east then south): the corner's neighbour set is {S,W} -> the rule gives `ne`, not `nw`", () => {
    // (0,0)-(1,0)-(2,0) east, then (2,0)-(2,1)-(2,2) south. At the corner
    // (2,0): E has no neighbour, W=(1,0) is in-line, S=(2,1) is in-line ->
    // nb={S,W} -> per place.fence `key == ("S","W") -> ne`.
    const line = [
      { x: 0, y: 0 }, { x: 1, y: 0 }, { x: 2, y: 0 }, { x: 2, y: 1 }, { x: 2, y: 2 },
    ];
    const out = fenceSubsForLine(line, 86, WIREFENC, noExisting, noRoad);
    expect(out).toEqual([
      { x: 0, y: 0, sub: 6 }, { x: 1, y: 0, sub: 6 },
      { x: 2, y: 0, sub: 2 },   // ne piece, nb={S,W}
      { x: 2, y: 1, sub: 7 }, { x: 2, y: 2, sub: 7 },
    ]);
  });

  describe("the four corner cases (built as a 3-tile L so the centre tile sees exactly the named pair; DISTINCTFENC pins WHICH branch fired)", () => {
    it("nb={E,S} -> nw", () => {
      const line = [{ x: 1, y: 1 }, { x: 2, y: 1 }, { x: 1, y: 2 }];
      const out = fenceSubsForLine(line, 86, DISTINCTFENC, noExisting, noRoad);
      expect(out[0]).toEqual({ x: 1, y: 1, sub: 11 });   // nw piece
    });
    it("nb={S,W} -> ne", () => {
      const line = [{ x: 1, y: 1 }, { x: 0, y: 1 }, { x: 1, y: 2 }];
      const out = fenceSubsForLine(line, 86, DISTINCTFENC, noExisting, noRoad);
      expect(out[0]).toEqual({ x: 1, y: 1, sub: 12 });   // ne piece
    });
    it("nb={E,N} -> sw", () => {
      const line = [{ x: 1, y: 1 }, { x: 2, y: 1 }, { x: 1, y: 0 }];
      const out = fenceSubsForLine(line, 86, DISTINCTFENC, noExisting, noRoad);
      expect(out[0]).toEqual({ x: 1, y: 1, sub: 13 });   // sw piece
    });
    it("nb={N,W} -> se", () => {
      const line = [{ x: 1, y: 1 }, { x: 0, y: 1 }, { x: 1, y: 0 }];
      const out = fenceSubsForLine(line, 86, DISTINCTFENC, noExisting, noRoad);
      expect(out[0]).toEqual({ x: 1, y: 1, sub: 14 });   // se piece
    });
  });

  it("a road tile in the middle of a horizontal line is skipped (sub = the ew run piece) and the flanking tiles are still ew", () => {
    const line = [{ x: 0, y: 0 }, { x: 1, y: 0 }, { x: 2, y: 0 }];
    const isRoad = (x: number, y: number) => x === 1 && y === 0;
    const out = fenceSubsForLine(line, 86, WIREFENC, noExisting, isRoad);
    expect(out).toEqual([
      { x: 0, y: 0, sub: 6 },
      // sub is still the computed ew piece (what WOULD have gone here) —
      // `skipped: "ROAD"` is what tells the caller to leave this gridno alone.
      { x: 1, y: 0, sub: 6, skipped: "ROAD" },
      { x: 2, y: 0, sub: 6 },
    ]);
  });

  it("joining an existing vertical fence to the SOUTH of the line's east end turns that end into ne (nb={W,S})", () => {
    const line = [{ x: 0, y: 0 }, { x: 1, y: 0 }, { x: 2, y: 0 }];
    const existing = (x: number, y: number) => x === 2 && y === 1;   // fence already south of the end tile
    const out = fenceSubsForLine(line, 86, WIREFENC, existing, noRoad);
    expect(out).toEqual([
      { x: 0, y: 0, sub: 6 }, { x: 1, y: 0, sub: 6 },
      { x: 2, y: 0, sub: 2 },   // ne piece, nb={W,S}
    ]);
  });

  it("a single tile has no neighbours -> ew", () => {
    const out = fenceSubsForLine([{ x: 5, y: 5 }], 86, WIREFENC, noExisting, noRoad);
    expect(out).toEqual([{ x: 5, y: 5, sub: 6 }]);
  });

  it("slot 77 (no corner art) gives the corner the ns piece because the table repeats it", () => {
    const line = [{ x: 1, y: 1 }, { x: 0, y: 1 }, { x: 1, y: 2 }];   // nb={S,W} -> ne
    const out = fenceSubsForLine(line, 77, JUNKFEN, noExisting, noRoad);
    expect(out[0]).toEqual({ x: 1, y: 1, sub: 2 });
    expect(JUNKFEN.ne).toBe(JUNKFEN.ns);   // the table itself repeats ns into every corner slot
  });
});

describe("queueCommitEdits", () => {
  const grpA = { sourceTileset: 72, sourceSector: "lab", w: 1, h: 1, items: [
    { dx: 0, dy: 0, layer: "structs" as const, slot: 86, sub: 1 },
  ] };
  const grpB = { sourceTileset: 72, sourceSector: "lab", w: 2, h: 1, items: [
    { dx: 0, dy: 0, layer: "structs" as const, slot: 86, sub: 4 },
    { dx: 1, dy: 0, layer: "structs" as const, slot: 86, sub: 5 },
  ] };
  it("concatenates edits/placed in queue order and sums dropped", () => {
    const r = queueCommitEdits([
      { anchor: { x: 1, y: 1 }, group: grpA },
      { anchor: { x: 3, y: 3 }, group: grpB },
    ], 8, 8);
    expect(r.edits).toEqual([
      { x: 1, y: 1, op: "place", layer: "structs", slot: 86, sub: 1 },
      { x: 3, y: 3, op: "place", layer: "structs", slot: 86, sub: 4 },
      { x: 4, y: 3, op: "place", layer: "structs", slot: 86, sub: 5 },
    ]);
    expect(r.placed.length).toBe(3);
    expect(r.dropped).toBe(0);
  });
  it("a queued group partly off-map contributes to dropped", () => {
    const r = queueCommitEdits([
      { anchor: { x: 1, y: 1 }, group: grpA },
      { anchor: { x: 7, y: 7 }, group: grpB },   // (7,7) fits, (8,7) is off-map
    ], 8, 8);
    expect(r.edits.length).toBe(2);
    expect(r.dropped).toBe(1);
  });
  it("localCheck over [...queued refs, ...current refs] flags a current building over a queued fence as TILE", () => {
    const p = empty();
    const occ = buildOccupancy(p, T, fp);   // nothing pre-existing on the map itself
    const queuedRefs: SpriteRef[] = [{ x: 3, y: 3, layer: "structs", slot: 86, sub: 1 }];    // queued fence
    const currentRefs: SpriteRef[] = [{ x: 3, y: 3, layer: "structs", slot: 36, sub: 1 }];   // current building candidate
    const v = localCheck(p, T, occ, [...queuedRefs, ...currentRefs], fp);
    expect(v[0]!.test).toBeNull();          // the queued fence candidate itself is clean
    expect(v[1]!.test).toBe("TILE");        // building overlaps the queued fence, not exempt
  });
});
