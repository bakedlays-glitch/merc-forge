/**
 * Generate dock panel — the live-preview home of the MapForge generator
 * subsystem (UX Phase 2).
 *
 * Unlike the old modal wizard (deleted — it covered the canvas), this
 * is a DOCK panel: the map stays visible while you configure, so the flow is
 *   pick a generator → drag its region on the map → watch the GHOST
 *   preview render on the canvas → drag sliders (ghost re-renders
 *   live) → Apply.
 *
 * Preview = a backend dry-run (`runGenerator(..., {dryRun: true})`):
 * the generator streams its op list without applying anything, and the
 * parent ghosts those ops into the local IsoRenderer only (see
 * applyGhostOps/clearGhost in MapForgeSector). Generators are
 * seeded-deterministic, so the ghost is EXACTLY what Apply will write.
 *
 * Region pick supports all four parameter schemes generators use:
 *   x1/y1/x2/y2 (rect, bank) · region_x1.. (scatter) ·
 *   center_x/center_y/radius (density-falloff) · x/y/width/height
 *   (building stamp).
 */
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";

import {
  type BuildingLibraryEntry,
  type GeneratorInfo,
  type GeneratorParamSchema,
  applyEdits,
  getSessionParsed,
  listBuildingLibrary,
  listGenerators,
  runGenerator,
} from "../lib/mapforge";
import {
  CLIP_LAYERS,
  pasteEdits,
  stripBuddyShadows,
  type ClipboardRegion,
  type ClipTile,
} from "../lib/mapClipboard";
import { isShadowOnlySlot } from "../lib/jaSlotPairs";
import type { IsoRenderer } from "../lib/IsoRenderer";
import { useMapForgeLog } from "./MapForgeLog";
import type { ActiveBrush } from "./MapForgePalette";

interface XY { x: number; y: number }

export interface GeneratePanelProps {
  sessionId: string | null;
  renderer: IsoRenderer | null;
  readOnly: boolean;
  /** Ja2Set.dat.xml path + tileset of the open sector — keys the canon
   * building library (per install + tileset). */
  xmlPath: string;
  tileset: number;
  /** The editor's active paint brush. Generators with slot/sub params
   * INHERIT it on selection (hunting slot numbers when the brush
   * already holds the right tree was the #1 usability complaint). */
  activeBrush: ActiveBrush | null;
  /** Ask the parent to enter STICKY box-region-pick mode on the canvas:
   * the user drags a box (or clicks two corners) and `cb` fires with the
   * two corners — and the mode RE-ARMS so the next drag re-aims the same
   * generator without pressing a button again. Stays armed until
   * `cancelRegionPick()` (panel deselect / unmount). */
  pickRegion(cb: (c1: XY, c2: XY) => void): void;
  /** Sticky single-click point pick — for center/focal generators
   * (density-falloff): every left click on the map fires `cb` with the
   * clicked tile (the focal point), staying armed for re-aim. The radius
   * is set by the slider, not derived from a box. */
  pickPoint(cb: (t: XY) => void): void;
  /** Disarm whatever region/point pick is active (panel switched
   * generators or unmounted). */
  cancelRegionPick(): void;
  /** Ghost the given (backend-shaped) ops into the local renderer ONLY
   * — bypasses undo/dirty entirely; parent blocks canvas tools while a
   * ghost is live. */
  applyGhostOps(ops: unknown[]): void;
  clearGhost(): void;
  ghostActive: boolean;
  /** Arm (or disarm with null) StarCraft-style building placement on the
   * canvas: the w×h footprint tints at the cursor; every left click
   * calls run(x, y) with the footprint's top-left tile and STAYS armed
   * for repeat stamps. ESC / tool change disarms (parent-side).
   * When `region` is set (canon building library), the parent ALSO
   * ghosts the real sprites at the cursor; a Promise-returning run()
   * lets the parent suppress the ghost while a stamp is in flight. */
  setPlacement(req: {
    w: number; h: number; label: string;
    region?: ClipboardRegion;
    run(x: number, y: number): void | Promise<void>;
  } | null): void;
  /** True while placement mode is armed on the canvas. */
  placementActive: boolean;
  /** Mirror one applied op into the local renderer (real run). */
  onOp(op: unknown): void;
  /** Parent's post-run resync (renderEpoch + history + session). */
  onComplete(applied: number, ok: boolean): void;
}

// ─── Region parameter schemes ─────────────────────────────────────────

type RegionScheme = "corners" | "region" | "center" | "rect";

const SCHEME_PARAMS: Record<RegionScheme, string[]> = {
  corners: ["x1", "y1", "x2", "y2"],
  region: ["region_x1", "region_y1", "region_x2", "region_y2"],
  center: ["center_x", "center_y", "radius"],
  rect: ["x", "y", "width", "height"],
};

function regionScheme(g: GeneratorInfo): RegionScheme | null {
  const has = (n: string) => g.params.some((p) => p.name === n);
  for (const scheme of ["corners", "region", "center", "rect"] as const) {
    if (SCHEME_PARAMS[scheme].every(has)) return scheme;
  }
  return null;
}

/** Translate a canvas drag (two corners) into the generator's region
 * params. Returns the merged values object. */
function applyPickedRegion(
  scheme: RegionScheme,
  values: Record<string, unknown>,
  c1: XY,
  c2: XY,
): Record<string, unknown> {
  const x1 = Math.min(c1.x, c2.x);
  const y1 = Math.min(c1.y, c2.y);
  const x2 = Math.max(c1.x, c2.x);
  const y2 = Math.max(c1.y, c2.y);
  switch (scheme) {
    case "corners":
      return { ...values, x1, y1, x2, y2 };
    case "region":
      return { ...values, region_x1: x1, region_y1: y1, region_x2: x2, region_y2: y2 };
    case "center": {
      const radius = Math.max(1, Math.ceil(Math.max(x2 - x1, y2 - y1) / 2));
      return {
        ...values,
        center_x: Math.round((x1 + x2) / 2),
        center_y: Math.round((y1 + y2) / 2),
        radius,
      };
    }
    case "rect":
      return {
        ...values,
        x: x1,
        y: y1,
        width: Math.max(3, x2 - x1 + 1),
        height: Math.max(3, y2 - y1 + 1),
      };
  }
}

function regionSummary(
  scheme: RegionScheme | null,
  values: Record<string, unknown>,
): { picked: boolean; text: string } {
  const n = (k: string) => (typeof values[k] === "number" ? (values[k] as number) : 0);
  if (scheme === "corners") {
    const w = Math.abs(n("x2") - n("x1")) + 1;
    const h = Math.abs(n("y2") - n("y1")) + 1;
    return {
      picked: w * h > 1,
      text: `(${n("x1")},${n("y1")}) → (${n("x2")},${n("y2")}) · ${w}×${h} = ${(w * h).toLocaleString()} tiles`,
    };
  }
  if (scheme === "region") {
    // region_* generators treat all-zeros as "whole map" (backend
    // sentinel) — a legitimate run, so it never gates Apply.
    const w = Math.abs(n("region_x2") - n("region_x1")) + 1;
    const h = Math.abs(n("region_y2") - n("region_y1")) + 1;
    if (w * h <= 1) {
      return { picked: true, text: "whole map — drag a box to limit it" };
    }
    return {
      picked: true,
      text: `(${n("region_x1")},${n("region_y1")}) → (${n("region_x2")},${n("region_y2")}) · ${w}×${h} = ${(w * h).toLocaleString()} tiles`,
    };
  }
  if (scheme === "center") {
    return {
      picked: true, // center+radius have sane defaults; never blocks
      text: `center (${n("center_x")},${n("center_y")}) · radius ${n("radius")}`,
    };
  }
  if (scheme === "rect") {
    return {
      picked: true, // building defaults are runnable
      text: `(${n("x")},${n("y")}) · ${n("width")}×${n("height")}`,
    };
  }
  return { picked: true, text: "whole map" };
}

// ─── Presets ──────────────────────────────────────────────────────────
// Minimal per-generator starting points so the first result is decent
// without touching a slider. Values merge OVER current ones (region and
// slot/sub picks are preserved).

const PRESETS: Record<string, Array<{ label: string; values: Record<string, unknown> }>> = {
  scatter: [
    { label: "Sparse", values: { count: 60, min_distance: 4 } },
    { label: "Medium", values: { count: 150, min_distance: 2 } },
    { label: "Dense", values: { count: 400, min_distance: 1 } },
  ],
  cluster: [
    { label: "Few groves", values: { cluster_count: 4, objects_per_cluster: 10, cluster_radius: 4 } },
    { label: "Many groves", values: { cluster_count: 12, objects_per_cluster: 16, cluster_radius: 5 } },
  ],
  "density-falloff": [
    { label: "Soft", values: { peak_density: 0.3 } },
    { label: "Heavy", values: { peak_density: 0.7 } },
  ],
};

// Params hidden from the form (the region block / map interaction owns
// them). For the "center" (focal) scheme we hide ONLY center_x/center_y —
// those come from clicking the map — and leave `radius` to render as a
// normal slider, so you set the focal point on the map and the size with
// a slider instead of dragging a box to imply a circle.
function hiddenParams(scheme: RegionScheme | null): Set<string> {
  if (!scheme) return new Set();
  if (scheme === "center") return new Set(["center_x", "center_y"]);
  return new Set(SCHEME_PARAMS[scheme]);
}

// ─── Generator picker cards ───────────────────────────────────────────
// Visual picker metadata: icon glyph + short name + one-line purpose +
// the section it lives under. Honest buttons, not thumbnails — the list
// is fixed (compiled-in registry), so unknown names fall back to the API
// label in the "Other" section.
//
// `group` sorts the cards into four labeled sections instead of one flat
// 2-col pile (the panel read as "uneven" with object-scatters, a terrain
// tool and destructive utilities all jumbled together). The generic
// `building` stamp is intentionally ABSENT — its card is removed (the
// Building Library above is the real flow); the backend generator stays
// registered so the `:gen building` console command still works.
type GenGroup = "Scatter" | "Shapes" | "Terrain" | "Utilities";
const GEN_META: Record<string, { icon: string; title: string; blurb: string; order: number; group: GenGroup }> = {
  scatter: { icon: "∴", title: "Scatter", blurb: "Random spread with spacing", order: 1, group: "Scatter" },
  cluster: { icon: "⁂", title: "Cluster", blurb: "Groves & clumped patches", order: 2, group: "Scatter" },
  "density-falloff": { icon: "◎", title: "Density falloff", blurb: "Dense near a focal point", order: 3, group: "Scatter" },
  fill: { icon: "▦", title: "Fill layer", blurb: "Flood one layer with a tile", order: 4, group: "Shapes" },
  rect: { icon: "▭", title: "Rectangle", blurb: "Outline or filled box", order: 5, group: "Shapes" },
  bank: { icon: "⛰", title: "Cliff / bank", blurb: "Raised plateau or escarpment", order: 6, group: "Terrain" },
  autoshadow: { icon: "◐", title: "Auto-shadow", blurb: "Add shadows to placed art", order: 7, group: "Utilities" },
  wipe: { icon: "⌫", title: "Wipe sector", blurb: "Clear every tile, every layer", order: 8, group: "Utilities" },
};
const GEN_GROUP_ORDER: GenGroup[] = ["Scatter", "Shapes", "Terrain", "Utilities"];

// ─── Named "don't place on" masks ─────────────────────────────────────
// Mirrors NAMED_MASKS in sidecar/mercwizard_core/mapforge/generators.py
// (slot families derived from the engine's TileTypeDefines enum). The
// panel composes the comma-list `avoid_named` param from checkboxes —
// the raw avoid_layer/avoid_slots params stay console-only (Advanced).
const NAMED_MASK_OPTIONS: Array<{ id: string; label: string; hint: string }> = [
  { id: "occupied", label: "Occupied", hint: "Tiles that already hold content on the target layer — stops stacking on existing sprites." },
  { id: "water", label: "Water", hint: "Water ground textures (regular + deep water)." },
  { id: "roads", label: "Roads", hint: "Road pieces (modern object-layer roads + legacy land roads)." },
  { id: "structures", label: "Structures", hint: "Anything on the structs layer — walls, buildings, doors, obstacles." },
  { id: "trees", label: "Trees", hint: "The tree / vegetation struct families (O-structs + full-structs)." },
];

function parseAvoidNamed(v: unknown): Set<string> {
  return new Set(
    String(v ?? "").split(",").map((t) => t.trim().toLowerCase()).filter(Boolean),
  );
}

export function MapForgeGeneratePanel({
  sessionId, renderer, readOnly, xmlPath, tileset, activeBrush,
  pickRegion, pickPoint, cancelRegionPick, applyGhostOps, clearGhost, ghostActive,
  setPlacement, placementActive,
  onOp, onComplete,
}: GeneratePanelProps) {
  const log = useMapForgeLog();
  const list = useQuery({
    queryKey: ["mapforge-generators"],
    queryFn: listGenerators,
    staleTime: Infinity,
  });

  // Which subsystem this panel is showing. The Building Library and the
  // procedural generators are distinct tools that were stacked in one
  // column (too crowded — user feedback); a top toggle shows one at a
  // time. Generators is the panel's namesake, so it's the default.
  const [mode, setMode] = useState<"generators" | "buildings">("generators");
  const [selectedName, setSelectedName] = useState<string>("");
  // Once a generator is picked the 8-card grid collapses to a compact
  // active row (the params need the vertical space); "Change" re-expands.
  const [pickerExpanded, setPickerExpanded] = useState(false);
  const [values, setValues] = useState<Record<string, unknown>>({});
  const [autoPreview, setAutoPreview] = useState(true);
  const [previewBusy, setPreviewBusy] = useState(false);
  const [previewCount, setPreviewCount] = useState<number | null>(null);
  // Surfaced in red under the form. A generator failure (e.g. missing
  // corpus data) previously vanished into a silent .catch — the user
  // saw nothing happen and read the generator as broken.
  const [previewError, setPreviewError] = useState<string | null>(null);
  const [running, setRunning] = useState(false);
  const [advancedOpen, setAdvancedOpen] = useState(false);

  const selected = useMemo(
    () => (list.data ?? []).find((g) => g.name === selectedName) ?? null,
    [list.data, selectedName],
  );
  const scheme = useMemo(() => (selected ? regionScheme(selected) : null), [selected]);
  const region = useMemo(() => regionSummary(scheme, values), [scheme, values]);

  // Latest-value refs so unmount cleanup doesn't need dep churn.
  const clearGhostRef = useRef(clearGhost);
  clearGhostRef.current = clearGhost;
  const setPlacementRef = useRef(setPlacement);
  setPlacementRef.current = setPlacement;
  const previewAbortRef = useRef<AbortController | null>(null);

  // Leaving the panel (or losing the session) always clears the ghost
  // AND disarms placement mode (its run() closes over panel state).
  useEffect(() => () => {
    previewAbortRef.current?.abort();
    clearGhostRef.current();
    setPlacementRef.current(null);
  }, []);
  useEffect(() => {
    if (!sessionId) {
      clearGhostRef.current();
      setPlacementRef.current(null);
    }
  }, [sessionId]);

  // True once the user EXPLICITLY set slot/sub (typed or clicked Use
  // brush) — from then on, brush changes stop auto-overwriting them.
  const slotTouchedRef = useRef(false);

  // Sticky region interaction: while a region generator is selected, the
  // CANVAS sets its region directly — no "Drag region" button first. Box
  // schemes capture a box drag; the focal (center) scheme captures a
  // single click as the focal point (radius stays on its slider). The
  // parent keeps the mode armed (re-aims on every pick); we disarm it
  // when the generator changes or the panel unmounts. Refs keep the
  // effect from re-arming just because a parent callback changed identity.
  const pickRegionRef = useRef(pickRegion);
  pickRegionRef.current = pickRegion;
  const pickPointRef = useRef(pickPoint);
  pickPointRef.current = pickPoint;
  const cancelRegionPickRef = useRef(cancelRegionPick);
  cancelRegionPickRef.current = cancelRegionPick;
  useEffect(() => {
    if (!selected || !scheme || readOnly) { cancelRegionPickRef.current(); return; }
    if (scheme === "center") {
      pickPointRef.current((t) => {
        setValues((prev) => ({ ...prev, center_x: t.x, center_y: t.y }));
      });
    } else {
      pickRegionRef.current((c1, c2) => {
        setValues((prev) => applyPickedRegion(scheme, prev, c1, c2));
      });
    }
    return () => cancelRegionPickRef.current();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectedName, scheme, readOnly]);

  const selectGenerator = (name: string) => {
    previewAbortRef.current?.abort();
    clearGhost();
    // Generators and building placement are mutually exclusive — picking
    // a generator card disarms any armed building (the parent disarms the
    // pick in the other direction).
    setPlacement(null);
    setPreviewCount(null);
    setPreviewError(null);
    setSelectedName(name);
    setPickerExpanded(false);   // collapse the card grid to the active row
    slotTouchedRef.current = false;
    const g = (list.data ?? []).find((x) => x.name === name);
    const defaults: Record<string, unknown> = {};
    if (g) for (const p of g.params) defaults[p.name] = p.default;
    // "Don't place on" UI default: Occupied ON (stop stacking on
    // existing content). The BACKEND default stays "" so console/API
    // runs with raw params are byte-identical to before.
    if (g && g.params.some((p) => p.name === "avoid_named")) {
      defaults.avoid_named = "occupied";
    }
    // Inherit the ACTIVE BRUSH: if the generator paints a (slot, sub),
    // start from what the user already picked in the palette instead
    // of the meaningless slot-0 default.
    if (g && activeBrush
        && g.params.some((p) => p.name === "slot")
        && g.params.some((p) => p.name === "sub")) {
      defaults.slot = activeBrush.slot;
      defaults.sub = activeBrush.sub;
      if (g.params.some((p) => p.name === "layer")) {
        defaults.layer = activeBrush.layer;
      }
    }
    // Seed a SENSIBLE DEFAULT REGION so the generator previews the moment
    // it's selected — no "drag a box before anything happens" dead state.
    // Only the GATED schemes need this: corners (rect, cliff) require a
    // box >1 tile to preview/Apply, so default them to a centered box; the
    // focal (center) scheme defaults its focal point to map-center.
    // Scatter/cluster ("region") already treat all-zeros as "whole map"
    // and preview immediately, so they keep that default untouched.
    const sc = g ? regionScheme(g) : null;
    const parsed = renderer?.getParsed();
    if (parsed && sc === "corners") {
      const cx = Math.floor(parsed.cols / 2);
      const cy = Math.floor(parsed.rows / 2);
      const hw = Math.max(2, Math.floor(parsed.cols / 4));
      const hh = Math.max(2, Math.floor(parsed.rows / 4));
      Object.assign(defaults, { x1: cx - hw, y1: cy - hh, x2: cx + hw, y2: cy + hh });
    } else if (parsed && sc === "center") {
      Object.assign(defaults, { center_x: Math.floor(parsed.cols / 2), center_y: Math.floor(parsed.rows / 2) });
    }
    setValues(defaults);
  };

  // ESC dismisses the whole generator: deselect it (which clears the
  // ghost) and the region-arm effect's cleanup disarms the canvas pick.
  // The single intuitive "get me out" gesture — the canvas pick owns the
  // map while a generator is selected, so the parent leaves ESC to us.
  // Ignored while typing in a param field.
  useEffect(() => {
    if (!selectedName) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== "Escape") return;
      const tag = (e.target as HTMLElement | null)?.tagName?.toLowerCase();
      if (tag === "input" || tag === "textarea" || tag === "select") return;
      e.preventDefault();
      selectGenerator("");
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectedName]);

  /** Re-sync slot/sub/layer from the current brush on demand. */
  const useBrush = () => {
    if (!activeBrush) return;
    slotTouchedRef.current = true;
    setValues((prev) => ({
      ...prev,
      slot: activeBrush.slot,
      sub: activeBrush.sub,
      ...(selected?.params.some((p) => p.name === "layer")
        ? { layer: activeBrush.layer }
        : {}),
    }));
  };

  const hasSlotSubParams = useMemo(
    () => (selected?.params ?? []).some((p) => p.name === "slot")
      && (selected?.params ?? []).some((p) => p.name === "sub"),
    [selected],
  );
  const hasSubsParam = useMemo(
    () => (selected?.params ?? []).some((p) => p.name === "subs"),
    [selected],
  );

  // ── Variant multi-select (replaces the typed `subs` string) ────────
  // All valid subs of the chosen slot, from the renderer's atlas. When
  // the slot has >1 sub we render a thumbnail toggle grid; the comma
  // `subs` param is COMPOSED from the selection (equal weights), so
  // console/API compatibility is untouched. Default = ALL subs included
  // (variety by default — the uniform-blob look was a core complaint).
  const slotNum = typeof values.slot === "number" ? (values.slot as number) : 0;
  const validSubs = useMemo(
    () => (renderer && hasSlotSubParams ? renderer.listValidSubs(slotNum) : []),
    // selectedName forces a re-list when the generator changes.
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [renderer, hasSlotSubParams, slotNum, selectedName],
  );
  const multiSub = hasSubsParam && validSubs.length > 1;
  const [includedSubs, setIncludedSubs] = useState<number[]>([]);

  // Re-seed the selection whenever the generator or the slot changes:
  // everything included, `subs` composed to match.
  useEffect(() => {
    if (!selected || !hasSlotSubParams || !hasSubsParam || !renderer) return;
    const subs = renderer.listValidSubs(slotNum);
    setIncludedSubs(subs);
    const spec = subs.length > 1 ? subs.join(",") : "";
    setValues((prev) =>
      (typeof prev.subs === "string" ? prev.subs : "") === spec
        ? prev
        : { ...prev, subs: spec });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectedName, slotNum, hasSlotSubParams, hasSubsParam, renderer]);

  const toggleSub = useCallback((sub: number) => {
    setIncludedSubs((prev) => {
      const has = prev.includes(sub);
      // Never allow an empty selection — the run would fall back to the
      // single `sub` and silently ignore the grid.
      if (has && prev.length === 1) return prev;
      const next = has
        ? prev.filter((s) => s !== sub)
        : [...prev, sub].sort((a, b) => a - b);
      setValues((v) => ({ ...v, subs: next.join(",") }));
      return next;
    });
  }, []);

  const includeAllSubs = useCallback(() => {
    setIncludedSubs(validSubs);
    setValues((v) => ({ ...v, subs: validSubs.join(",") }));
  }, [validSubs]);

  // LIVE brush adoption: the most common real flow is "open Generate
  // first, realize you need a brush, go arm one" — the generator must
  // pick it up the moment it's armed, not require a button press
  // (owner: "why can't it take what's in the brush by default?").
  // Only while the user hasn't explicitly chosen a slot/sub.
  useEffect(() => {
    if (!activeBrush || !selected || !hasSlotSubParams) return;
    if (slotTouchedRef.current) return;
    setValues((prev) => {
      if (prev.slot === activeBrush.slot && prev.sub === activeBrush.sub) {
        return prev;
      }
      return {
        ...prev,
        slot: activeBrush.slot,
        sub: activeBrush.sub,
        ...(selected.params.some((p) => p.name === "layer")
          ? { layer: activeBrush.layer }
          : {}),
      };
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activeBrush, selected, hasSlotSubParams]);

  // ── Live preview: dry-run on every (debounced) param change ────────
  const valuesKey = JSON.stringify(values);
  useEffect(() => {
    if (!autoPreview || !sessionId || !selected || running || readOnly) return;
    if (scheme && !region.picked) return;   // wait for a region first
    // Don't ghost known-junk: a slot/sub generator with no brush armed
    // and untouched defaults would preview slot-0 garbage right under
    // the "No brush armed" warning.
    if (hasSlotSubParams && !activeBrush && !slotTouchedRef.current) return;
    // 120ms debounce so the ghost MORPHS while a slider is dragged
    // (sliders fire per tick; stale dry-runs are aborted mid-flight) —
    // owner: "as you adjust the slider it shows you the shadow preview".
    // The old 400ms read as update-after-you-let-go.
    const t = setTimeout(() => {
      previewAbortRef.current?.abort();
      const ac = new AbortController();
      previewAbortRef.current = ac;
      setPreviewBusy(true);
      const ops: unknown[] = [];
      runGenerator(sessionId, selected.name, values, (e) => {
        if ("op" in e) ops.push((e as { op: unknown }).op);
      }, { dryRun: true, signal: ac.signal })
        .then((final) => {
          if (ac.signal.aborted) return;
          if (!final.ok) {
            // Generator failed (missing corpus data, bad params, …) —
            // SAY so. The silent version read as "generator is broken".
            clearGhost();
            setPreviewCount(null);
            setPreviewError(final.message ?? final.error ?? "preview failed");
            return;
          }
          setPreviewError(null);
          applyGhostOps(ops);
          setPreviewCount(final.op_count ?? ops.length);
        })
        .catch((err) => {
          // Aborts (params changed mid-preview) are routine; anything
          // else is a real failure the user must see.
          if (ac.signal.aborted) return;
          setPreviewError(err instanceof Error ? err.message : String(err));
        })
        .finally(() => {
          if (previewAbortRef.current === ac) setPreviewBusy(false);
        });
    }, 120);
    return () => clearTimeout(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [autoPreview, sessionId, selected, valuesKey, running, readOnly,
      scheme, region.picked]);

  // ── Apply (the real run) ────────────────────────────────────────────
  const apply = useCallback(async () => {
    if (!sessionId || !selected || !renderer || running) return;
    // Wipe nukes the sector with one click — make it a deliberate act.
    if (selected.name === "wipe"
        && !window.confirm("Wipe ALL layers across the whole sector?")) {
      return;
    }
    previewAbortRef.current?.abort();
    clearGhost();
    setPreviewError(null);
    setRunning(true);
    const start = performance.now();
    let wroteHeights = false;
    renderer.beginStroke(`Generator: ${selected.label}`);
    try {
      const final = await runGenerator(sessionId, selected.name, values, (e) => {
        if ("op" in e) {
          const op = (e as { op: unknown }).op;
          if ((op as { op?: string })?.op === "set_height") wroteHeights = true;
          onOp(op);
        }
      });
      renderer.endStroke();
      const ms = Math.round(performance.now() - start);
      log?.append({
        severity: final.ok ? "success" : "error",
        message: final.ok
          ? `${selected.label}: ${final.applied.toLocaleString()} ops in ${ms} ms (Ctrl+Z undoes the whole run)`
          : `${selected.label} failed: ${final.message ?? final.error ?? "unknown"}`,
      });
      if (final.ok && wroteHeights) {
        // Terrain heights don't show in the iso render — without this
        // note, a successful cliff run reads as "nothing happened".
        log?.append({
          severity: "info",
          message: "This run wrote terrain HEIGHTS — they're invisible "
            + "in the map view. Select the Height tool to see the "
            + "elevation overlay (cliff-face art comes with the autotiler).",
        });
      }
      onComplete(final.applied, final.ok);
    } catch (err) {
      renderer.endStroke();
      log?.append({
        severity: "error",
        message: `Generator stream failed: ${err instanceof Error ? err.message : String(err)}`,
      });
      onComplete(0, false);
    } finally {
      setRunning(false);
      setPreviewCount(null);
    }
  }, [sessionId, selected, renderer, running, values, clearGhost, onOp,
      onComplete, log]);

  // ── Form param split ────────────────────────────────────────────────
  const hidden = useMemo(() => hiddenParams(scheme), [scheme]);
  // Tuning/safety knobs live under Advanced — the primary form is the
  // handful of choices a run actually varies on (user feedback: clip /
  // levels are options, not front-and-center).
  // room_id is automatic invisible bookkeeping (0 = auto-assign on the
  // backend) — never a front-and-center slider the user sets by hand.
  // avoid_layer/avoid_slots are legacy console-power masks — superseded
  // in the panel by the named "Don't place on" checkboxes.
  const ADV_NAMES = ["seed", "biome", "clip_to_playable", "levels",
    "place_cliff_faces", "room_id", "avoid_layer", "avoid_slots"];
  // Params with DEDICATED visual controls — never rendered as raw rows.
  // slot/sub come from the brush (visual pick) and show in the preview
  // strip; subs is composed by the variant grid; avoid_named by the
  // checkbox row.
  const UI_OWNED = ["slot", "sub", "subs", "avoid_named"];
  const primary = useMemo(
    () => (selected?.params ?? []).filter(
      (p) => !hidden.has(p.name) && !ADV_NAMES.includes(p.name)
        && !UI_OWNED.includes(p.name)
        && !p.name.startsWith("corpus_"),
    ),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [selected, hidden],
  );
  const advanced = useMemo(
    () => (selected?.params ?? []).filter(
      (p) => !hidden.has(p.name)
        && (ADV_NAMES.includes(p.name) || p.name.startsWith("corpus_")),
    ),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [selected, hidden],
  );
  const hasSlotSub = hasSlotSubParams;
  const presets = selected ? PRESETS[selected.name] ?? null : null;

  if (!sessionId) {
    return (
      <p className="p-3 text-xs italic text-gray-500">
        Open a sector to use generators.
      </p>
    );
  }
  if (readOnly) {
    return (
      <p className="p-3 text-xs italic text-gray-500">
        This sector is read-only (SLF-bundled). Extract it to a loose
        map first.
      </p>
    );
  }

  return (
    <div className="flex h-full flex-col gap-2 p-2 text-xs">
      {/* Mode toggle — the Building Library and the procedural generators
          are distinct tools; show ONE at a time (stacked, they overcrowded
          the column — user feedback). */}
      <div className="flex shrink-0 overflow-hidden rounded border border-gray-700">
        {([["generators", "⚙ Generators"], ["buildings", "🏛 Buildings"]] as const).map(([m, label]) => (
          <button
            key={m}
            type="button"
            data-gen-mode={m}
            onClick={() => {
              // Leaving a subsystem tears down its canvas state so a ghost
              // / armed pick / armed building never lingers under the
              // other tab's controls.
              if (m === "buildings") selectGenerator("");  // drop the generator
              else setPlacement(null);                     // drop any building
              setMode(m);
            }}
            className={`flex-1 px-2 py-1.5 text-xs font-medium ${
              mode === m
                ? "bg-emerald-700/40 text-emerald-100"
                : "bg-gray-900 text-gray-400 hover:bg-gray-800 hover:text-gray-200"
            }`}
          >
            {label}
          </button>
        ))}
      </div>

      {/* Canon building library — the headline building flow. Real
          buildings grafted verbatim from this tileset's actual maps:
          click a thumbnail card → placement arms with a real sprite
          ghost at the cursor → click stamps it. */}
      {mode === "buildings" && (
        <BuildingLibrarySection
          sessionId={sessionId}
          renderer={renderer}
          xmlPath={xmlPath}
          tileset={tileset}
          running={running}
          clearGhost={() => {
            previewAbortRef.current?.abort();
            clearGhost();
            setPreviewCount(null);
          }}
          setPlacement={setPlacement}
          placementActive={placementActive}
          onComplete={onComplete}
        />
      )}

      {mode === "generators" && (
        <>
          {selected && !pickerExpanded ? (
        /* Collapsed picker: the chosen generator as one compact row, so
           the params own the vertical space. "Change" re-expands the grid. */
        <div className="flex shrink-0 items-center gap-1.5 rounded border border-emerald-500 bg-emerald-950/50 px-2 py-1">
          <span className="w-4 shrink-0 text-center text-sm leading-none text-emerald-300">
            {GEN_META[selectedName]?.icon ?? "⚙"}
          </span>
          <span className="min-w-0 flex-1 truncate text-[11px] font-medium text-emerald-100">
            {GEN_META[selectedName]?.title ?? selected.label}
          </span>
          <button
            type="button"
            data-gen-change
            onClick={() => setPickerExpanded(true)}
            className="shrink-0 rounded border border-gray-600 bg-gray-800 px-2 py-0.5 text-[10px] text-gray-200 hover:bg-gray-700"
          >
            Change ▾
          </button>
        </div>
      ) : (
        /* Full picker grid — visual cards grouped into labeled sections.
           The generic `building` stamp is dropped here (Buildings tab is
           the real flow); its backend generator stays for the console. */
        <div className="shrink-0 space-y-1.5">
          {GEN_GROUP_ORDER.map((groupName) => {
        const cards = [...(list.data ?? [])]
          .filter((g) => g.name !== "building" && GEN_META[g.name]?.group === groupName)
          .sort((a, b) =>
            (GEN_META[a.name]?.order ?? 99) - (GEN_META[b.name]?.order ?? 99)
            || a.name.localeCompare(b.name));
        if (cards.length === 0) return null;
        return (
          <div key={groupName} data-gen-group={groupName}>
            <div className="mb-0.5 text-[9px] font-semibold uppercase tracking-wider text-gray-500">
              {groupName}
            </div>
            <div className="grid grid-cols-2 gap-1" data-gen-cards>
              {cards.map((g) => {
                const meta = GEN_META[g.name]
                  ?? { icon: "⚙", title: g.label, blurb: "" };
                const active = g.name === selectedName;
                return (
                  <button
                    key={g.name}
                    type="button"
                    data-gen-card={g.name}
                    data-active={active ? "1" : undefined}
                    onClick={() => selectGenerator(active ? "" : g.name)}
                    title={`${g.label}\n\n${g.description}`}
                    className={`flex items-center gap-1.5 rounded border px-1.5 py-1 text-left ${
                      active
                        ? "border-emerald-500 bg-emerald-950/50"
                        : "border-gray-700 bg-gray-900/60 hover:border-gray-500"
                    }`}
                  >
                    <span className={`w-4 shrink-0 text-center text-sm leading-none ${
                      active ? "text-emerald-300" : "text-gray-400"
                    }`}>
                      {meta.icon}
                    </span>
                    <span className="min-w-0">
                      <span className={`block truncate text-[11px] leading-tight ${
                        active ? "text-emerald-100" : "text-gray-200"
                      }`}>
                        {meta.title}
                      </span>
                      <span className="block truncate text-[9px] leading-tight text-gray-500">
                        {meta.blurb}
                      </span>
                    </span>
                  </button>
                );
              })}
            </div>
          </div>
        );
      })}
            </div>
          )}
          {selected && (
            <p className="line-clamp-2 text-[10px] leading-snug text-gray-500" title={selected.description}>
              {selected.description}
            </p>
          )}

      {selected && (
        <div className="min-h-0 flex-1 space-y-2 overflow-y-auto pr-1">
          {/* Region readout — the canvas is already live (sticky pick),
              so this is a status line, not a gate. Box schemes: drag a
              box on the map. Focal scheme: click the map to set the
              focal point. "Whole map" resets a box back to the full
              sector. */}
          {scheme && (
            <div className="rounded border border-amber-700/50 bg-amber-950/30 px-2 py-1.5">
              <div className="flex items-center justify-between gap-2">
                <div className="min-w-0">
                  <div className="text-[10px] font-semibold uppercase tracking-wide text-amber-300">
                    {scheme === "center" ? "Focal point" : "Region"}
                  </div>
                  <div className="mt-0.5 truncate font-mono text-gray-300">
                    {region.text}
                  </div>
                  <div className="mt-0.5 text-[9px] italic text-amber-200/70">
                    {scheme === "center"
                      ? "🖱 click the map to move it · radius below"
                      : "🖱 drag a box on the map to aim it"}
                  </div>
                </div>
                {scheme !== "center" && renderer && (
                  <button
                    type="button"
                    onClick={() => {
                      const p = renderer.getParsed();
                      if (!p) return;
                      setValues((prev) =>
                        applyPickedRegion(scheme, prev,
                          { x: 0, y: 0 }, { x: p.cols - 1, y: p.rows - 1 }));
                    }}
                    className="shrink-0 whitespace-nowrap rounded border border-amber-700/50 bg-amber-900/30 px-2 py-1 text-[10px] text-amber-200 hover:bg-amber-800/40"
                    title="Reset the region to the whole sector"
                  >
                    ↺ whole map
                  </button>
                )}
              </div>
            </div>
          )}

          {/* Presets */}
          {presets && (
            <div className="flex flex-wrap items-center gap-1">
              <span className="text-[10px] text-gray-500">Presets:</span>
              {presets.map((ps) => (
                <button
                  key={ps.label}
                  type="button"
                  onClick={() => setValues((prev) => ({ ...prev, ...ps.values }))}
                  className="rounded border border-gray-700 bg-gray-900 px-2 py-0.5 text-[10px] text-gray-300 hover:border-gray-500 hover:text-gray-100"
                >
                  {ps.label}
                </button>
              ))}
            </div>
          )}

          {/* Params (sliders for bounded ints/floats via ParamRow).
              high_side only applies to escarpment mode — hidden for a
              plateau (it has no high side). */}
          {primary
            .filter((p) => !(p.name === "high_side" && values.bank_mode === "plateau"))
            .map((p) => (
              <ParamRow
                key={p.name}
                param={p}
                value={values[p.name]}
                onChange={(v) => {
                  if (p.name === "slot" || p.name === "sub") slotTouchedRef.current = true;
                  setValues((prev) => ({ ...prev, [p.name]: v }));
                }}
              />
            ))}
          {/* "Don't place on" — named masks as checkboxes. The raw
              avoid_layer/avoid_slots params stay console-only. */}
          {selected.params.some((p) => p.name === "avoid_named") && (
            <div
              data-avoid-row
              className="rounded border border-gray-700 bg-gray-900/60 px-2 py-1.5"
            >
              <div className="text-[10px] font-semibold uppercase tracking-wide text-gray-400">
                Don't place on
              </div>
              <div className="mt-1 flex flex-wrap gap-x-3 gap-y-1">
                {NAMED_MASK_OPTIONS.map((m) => {
                  const set = parseAvoidNamed(values.avoid_named);
                  const on = set.has(m.id);
                  return (
                    <label
                      key={m.id}
                      className="flex items-center gap-1 text-[10px] text-gray-300"
                      title={m.hint}
                    >
                      <input
                        type="checkbox"
                        data-avoid={m.id}
                        checked={on}
                        onChange={() => {
                          const next = parseAvoidNamed(values.avoid_named);
                          if (on) next.delete(m.id); else next.add(m.id);
                          // Stable order = NAMED_MASK_OPTIONS order.
                          const spec = NAMED_MASK_OPTIONS
                            .map((o) => o.id)
                            .filter((id) => next.has(id))
                            .join(",");
                          setValues((prev) => ({ ...prev, avoid_named: spec }));
                        }}
                        className="h-3 w-3"
                      />
                      {m.label}
                    </label>
                  );
                })}
              </div>
            </div>
          )}
          {hasSlotSub && !activeBrush && (
            <div className="rounded border border-amber-700/60 bg-amber-950/40 px-2 py-1.5 text-[10px] leading-snug text-amber-200">
              <strong>No brush armed</strong> — this generator will paint
              slot {String(values.slot ?? 0)}, which is almost certainly
              not what you want. Pick a tile in the Palette (or
              right-click one on the map) and the generator will use it
              automatically.
            </div>
          )}
          {hasSlotSub && activeBrush && (
            <div className="flex items-center justify-between gap-2 rounded border border-gray-700 bg-gray-900/60 px-2 py-1">
              <span className="truncate text-[10px] text-gray-400">
                Brush: <span className="text-gray-200">{activeBrush.sti_filename.replace(/\.sti$/i, "")}</span>
                {" "}· slot {activeBrush.slot} sub {activeBrush.sub}
              </span>
              <button
                type="button"
                onClick={useBrush}
                disabled={values.slot === activeBrush.slot && values.sub === activeBrush.sub}
                className="whitespace-nowrap rounded border border-gray-600 bg-gray-800 px-2 py-0.5 text-[10px] text-gray-200 hover:bg-gray-700 disabled:opacity-40"
                title="Copy the active brush's slot/sub (and layer) into this generator"
              >
                ⟵ Use brush
              </button>
            </div>
          )}
          {/* Variants: thumbnail multi-select for multi-sub slots —
              every sub starts INCLUDED; click a thumb to exclude it.
              The `subs` param ("1,2,3", equal weights) is composed from
              the selection. Single-sub slots keep the read-only
              SlotSubPreview as the fallback display. */}
          {hasSlotSub && renderer && multiSub && (
            <VariantMultiSelect
              renderer={renderer}
              slot={slotNum}
              subs={validSubs}
              included={includedSubs}
              onToggle={toggleSub}
              onAll={includeAllSubs}
            />
          )}
          {hasSlotSub && renderer && !multiSub && (
            <SlotSubPreview
              renderer={renderer}
              slot={typeof values.slot === "number" ? values.slot : 0}
              sub={typeof values.sub === "number" ? values.sub : 1}
            />
          )}
          {advanced.length > 0 && (
            <div className="border-t border-gray-800 pt-2">
              <button
                type="button"
                onClick={() => setAdvancedOpen((o) => !o)}
                className="text-[10px] text-gray-500 hover:text-gray-300"
              >
                {advancedOpen ? "▼" : "▶"} Advanced ({advanced.length})
              </button>
              {advancedOpen && (
                <div className="mt-2 space-y-2">
                  {advanced.map((p) => (
                    <ParamRow
                      key={p.name}
                      param={p}
                      value={values[p.name]}
                      onChange={(v) => {
                if (p.name === "slot" || p.name === "sub") slotTouchedRef.current = true;
                setValues((prev) => ({ ...prev, [p.name]: v }));
              }}
                    />
                  ))}
                </div>
              )}
            </div>
          )}
        </div>
      )}

      {/* Footer: preview status + Apply/Cancel */}
      {selected && (
        <div className="space-y-1.5 border-t border-gray-800 pt-2">
          {previewError && (
            <div className="rounded border border-red-800 bg-red-950 px-2 py-1 text-[10px] leading-snug text-red-200">
              Preview failed: {previewError}
            </div>
          )}
          <div className="flex items-center justify-between gap-2">
            <label className="flex items-center gap-1 text-[10px] text-gray-400"
              title="Re-preview on every change (dry run — nothing is applied until you click Apply)">
              <input
                type="checkbox"
                checked={autoPreview}
                onChange={(e) => {
                  setAutoPreview(e.target.checked);
                  if (!e.target.checked) { previewAbortRef.current?.abort(); clearGhost(); setPreviewCount(null); }
                }}
                className="h-3 w-3"
              />
              Live preview
            </label>
            <span className="text-[10px] text-gray-500">
              {previewBusy
                ? "previewing…"
                : ghostActive && previewCount != null
                  ? `ghost: ${previewCount.toLocaleString()} ops`
                  : scheme && !region.picked
                    ? "pick a region to preview"
                    : ""}
            </span>
          </div>
          <div className="flex items-center gap-2">
            <button
              type="button"
              onClick={() => void apply()}
              disabled={running || (scheme != null && !region.picked)}
              className={`flex-1 rounded px-3 py-2 text-sm font-medium ${
                running || (scheme != null && !region.picked)
                  ? "cursor-not-allowed border border-gray-700 bg-gray-900 text-gray-600"
                  : "border border-emerald-600/60 bg-emerald-600/20 text-emerald-100 hover:bg-emerald-600/40"
              }`}
              title={scheme != null && !region.picked
                ? "Drag a region on the map first"
                : "Apply the previewed result for real (Ctrl+Z undoes the whole run)"}
            >
              {running ? "Applying…" : "✓ Apply"}
            </button>
            <button
              type="button"
              onClick={() => {
                previewAbortRef.current?.abort();
                clearGhost();
                setPreviewCount(null);
              }}
              disabled={!ghostActive || running}
              className="rounded border border-gray-700 bg-gray-900 px-3 py-2 text-sm text-gray-300 hover:border-gray-500 hover:text-gray-100 disabled:cursor-not-allowed disabled:opacity-40"
              title="Discard the ghost preview (the map was never actually changed)"
            >
              ✕ Clear
            </button>
          </div>
        </div>
      )}
        </>
      )}
    </div>
  );
}

// ──────────────────────────────────────────────────────────────────────
//  BuildingLibrarySection — the canon building library
// ──────────────────────────────────────────────────────────────────────
//
// Real buildings grafted verbatim from real maps of the open sector's
// tileset (backend: GET /mapforge/building-library — every layer of
// every building, structure/contents split, context labels, rendered
// thumbnails). This REPLACES the procedural building stamp as the
// placement path; the building generator survives only in the generic
// generator dropdown below.
//
// Flow: click a thumbnail card → placement ARMS IMMEDIATELY (no size
// steppers, no extra button) → the canvas shows the footprint tint AND
// a real sprite ghost of the building at the cursor → click stamps it
// verbatim with automatic room renumbering and STAYS armed for repeat
// stamps → ESC / tool change disarms.
//
// "Include contents" ON (default) stamps everything inside (furniture,
// debris, objects); OFF stamps the structure only (walls + roofs +
// floors + doors). Cards can be renamed (pencil icon) — names persist
// client-side in localStorage keyed by library entry id.

const BUILDING_NAMES_LS_KEY = "mapforge-building-library-names";

function loadCustomNames(): Record<string, string> {
  try {
    const raw = localStorage.getItem(BUILDING_NAMES_LS_KEY);
    const parsed: unknown = raw ? JSON.parse(raw) : {};
    return parsed && typeof parsed === "object"
      ? (parsed as Record<string, string>) : {};
  } catch {
    return {};
  }
}

/** Compose the stampable region from a library entry: structure tiles
 * always; contents merged in when the toggle is ON. Buddy-eligible
 * shadow entries are stripped (the engine auto-re-adds them at load via
 * HAS_SHADOW_BUDDY — same rule as clipboard copy). */
function composeBuildingRegion(
  entry: BuildingLibraryEntry,
  includeContents: boolean,
): ClipboardRegion {
  const byKey = new Map<string, ClipTile>();
  const cloneLayers = (src: Record<string, number[][]>) => {
    const out = {} as ClipTile["layers"];
    for (const l of CLIP_LAYERS) {
      out[l] = (src[l] ?? []).map((p) => [p[0] ?? 0, p[1] ?? 0]);
    }
    return out;
  };
  for (const t of entry.tiles) {
    byKey.set(`${t.dx},${t.dy}`, {
      dx: t.dx, dy: t.dy, layers: cloneLayers(t.layers),
      room: t.room, height: t.height,
    });
  }
  if (includeContents) {
    for (const c of entry.contents_tiles) {
      const k = `${c.dx},${c.dy}`;
      const ex = byKey.get(k);
      if (!ex) {
        byKey.set(k, {
          dx: c.dx, dy: c.dy, layers: cloneLayers(c.layers),
          room: c.room, height: c.height,
        });
        continue;
      }
      // Structure entries first, contents appended after — matches the
      // dominant authored order (walls precede furniture in the stored
      // entry lists).
      for (const l of CLIP_LAYERS) {
        const extra = c.layers[l] ?? [];
        if (extra.length) {
          ex.layers[l] = [...ex.layers[l], ...extra.map((p) => [p[0] ?? 0, p[1] ?? 0])];
        }
      }
    }
  }
  const raw: ClipboardRegion = {
    sourceTileset: entry.tileset,
    sourceSector: entry.source_map,
    w: entry.w,
    h: entry.h,
    tiles: Array.from(byKey.values()),
  };
  return stripBuddyShadows(raw, (slot) => isShadowOnlySlot(slot));
}

function BuildingLibrarySection({
  sessionId, renderer, xmlPath, tileset, running, clearGhost,
  setPlacement, placementActive, onComplete,
}: {
  sessionId: string | null;
  renderer: IsoRenderer | null;
  xmlPath: string;
  tileset: number;
  running: boolean;
  clearGhost(): void;
  setPlacement: GeneratePanelProps["setPlacement"];
  placementActive: boolean;
  onComplete(applied: number, ok: boolean): void;
}) {
  const log = useMapForgeLog();
  const library = useQuery({
    queryKey: ["mapforge-building-library", xmlPath, tileset],
    queryFn: () => listBuildingLibrary(xmlPath, tileset),
    enabled: !!sessionId && !!xmlPath,
    staleTime: 5 * 60 * 1000,
    retry: false,
  });

  const [open, setOpen] = useState(true);
  const [search, setSearch] = useState("");
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [includeContents, setIncludeContents] = useState(true);
  const [customNames, setCustomNames] = useState<Record<string, string>>(
    loadCustomNames,
  );
  const [renamingId, setRenamingId] = useState<string | null>(null);
  const [renameDraft, setRenameDraft] = useState("");

  const entries = useMemo(() => library.data?.entries ?? [], [library.data]);
  const selected = entries.find((e) => e.id === selectedId) ?? null;

  const displayName = useCallback(
    (e: BuildingLibraryEntry) => customNames[e.id] ?? e.label,
    [customNames],
  );

  const filtered = useMemo(() => {
    const q = search.trim().toLowerCase();
    if (!q) return entries;
    return entries.filter((e) =>
      `${displayName(e)} ${e.label} ${e.town} ${e.sector} ${e.function} ${e.source_map}`
        .toLowerCase().includes(q));
  }, [entries, search, displayName]);

  // Latest-state refs for the stamp closure handed to the canvas.
  const stampStateRef = useRef<{
    sessionId: string | null;
    renderer: IsoRenderer | null;
  }>({ sessionId: null, renderer: null });
  stampStateRef.current = { sessionId, renderer };
  const logRef = useRef(log);
  logRef.current = log;
  const onCompleteRef = useRef(onComplete);
  onCompleteRef.current = onComplete;
  // Re-entrancy latch (double-click = one stamp, not two interleaved
  // pastes with divergent room-id ranges).
  const stampBusyRef = useRef(false);

  /** Stamp = a pasteEdits-style transactional batch through the
   * standard stroke path: set_entries per tile + set_room with fresh
   * room ids ABOVE the target's current max. One stamp = one undo. */
  const stampAt = useCallback(async (
    region: ClipboardRegion, label: string, x: number, y: number,
  ) => {
    const s = stampStateRef.current;
    if (!s.sessionId || !s.renderer || stampBusyRef.current) return;
    stampBusyRef.current = true;
    const start = performance.now();
    let strokeCommitted = false;
    const renderer_ = s.renderer;
    try {
      // The target's CURRENT room ids drive the remap to fresh ids —
      // repeat stamps never merge rooms.
      const parsed = await getSessionParsed(s.sessionId);
      const { edits, targetTiles, droppedTiles } = pasteEdits(
        region, { x, y }, parsed.cols, parsed.rows,
        // Heights are not part of a building graft — never flatten the
        // target's terrain elevation under the stamp.
        { existingRoomIds: parsed.rooms, includeHeights: false },
      );
      if (edits.length === 0) {
        logRef.current?.append({
          severity: "warn",
          message: "Nothing stamped — the building fell entirely outside the map.",
        });
        return;
      }
      renderer_.beginStroke(`Stamp ${label} (${targetTiles} tiles)`);
      for (const ed of edits) {
        if (ed.op === "set_entries" && ed.layer) {
          renderer_.recordSnapshot(ed.x, ed.y, ed.layer);
        } else if (ed.op === "set_room") {
          renderer_.recordRoomSnapshot(ed.x, ed.y);
        }
        renderer_.applyLocalEdit({
          x: ed.x, y: ed.y, op: ed.op,
          layer: ed.layer, entries: ed.entries, roomId: ed.room_id,
        });
      }
      renderer_.endStroke();
      strokeCommitted = true;
      await applyEdits(s.sessionId, edits);
      const ms = Math.round(performance.now() - start);
      logRef.current?.append({
        severity: "success",
        message: `${label} stamped at (${x},${y}) — ${targetTiles} tiles in ${ms} ms`
          + (droppedTiles > 0 ? ` (${droppedTiles} clipped at the map edge)` : "")
          + " (Ctrl+Z undoes it; click again to place another)",
      });
      onCompleteRef.current(edits.length, true);
    } catch (err) {
      // Backend applyEdits is transactional — on rejection the live
      // session is untouched; revert the optimistic local mirror and
      // drop the dangling undo stroke (mirrors doPaste's rollback).
      if (strokeCommitted) {
        const entry = renderer_.discardLastUndo();
        if (entry) {
          for (const sn of entry.snapshots) {
            renderer_.applyLocalEdit({
              x: sn.x, y: sn.y, op: "set_entries",
              layer: sn.layer, entries: sn.entries,
            });
          }
          for (const r of entry.roomSnapshots) {
            renderer_.applyLocalEdit({ x: r.x, y: r.y, op: "set_room", roomId: r.roomId });
          }
        }
      }
      logRef.current?.append({
        severity: "error",
        message: `Building stamp failed: ${err instanceof Error ? err.message : String(err)}`,
      });
      onCompleteRef.current(0, false);
    } finally {
      stampBusyRef.current = false;
    }
  }, []);

  /** Click a card = placement is ARMED immediately: footprint tint +
   * real sprite ghost at the cursor (the parent ghosts `region`). */
  const arm = useCallback((entry: BuildingLibraryEntry, contents: boolean) => {
    clearGhost();   // a live generator-preview ghost would block clicks
    const region = composeBuildingRegion(entry, contents);
    const label = customNames[entry.id] ?? `${entry.function} (${entry.sector})`;
    setPlacement({
      w: entry.w, h: entry.h, label, region,
      run: (x, y) => stampAt(region, label, x, y),
    });
  }, [clearGhost, setPlacement, stampAt, customNames]);

  const pick = (e: BuildingLibraryEntry) => {
    setSelectedId(e.id);
    arm(e, includeContents);
  };

  const toggleContents = (on: boolean) => {
    setIncludeContents(on);
    // Re-arm live so the ghost + stamp reflect the new composition.
    if (placementActive && selected) arm(selected, on);
  };

  const commitRename = (id: string) => {
    const name = renameDraft.trim();
    setCustomNames((prev) => {
      const next = { ...prev };
      if (name) next[id] = name; else delete next[id];
      try {
        localStorage.setItem(BUILDING_NAMES_LS_KEY, JSON.stringify(next));
      } catch { /* storage full/blocked — name still applies this session */ }
      return next;
    });
    setRenamingId(null);
  };

  if (!sessionId) return null;

  return (
    <div className="rounded border border-sky-800/60 bg-sky-950/20">
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        className="flex w-full items-center justify-between px-2 py-1.5 text-left"
      >
        <span className="text-[10px] font-semibold uppercase tracking-wide text-sky-300">
          {open ? "▼" : "▶"} Building library
        </span>
        <span className="text-[10px] text-gray-500">
          {library.isLoading
            ? "scanning…"
            : selected
              ? displayName(selected)
              : `${entries.length} buildings`}
        </span>
      </button>
      {open && (
        <div className="space-y-2 px-2 pb-2">
          {library.isLoading && (
            <p className="text-[10px] italic leading-snug text-gray-500">
              Scanning this tileset's maps for buildings… first time only
              — afterwards the library loads instantly.
            </p>
          )}
          {library.isError && (
            <p className="text-[10px] leading-snug text-red-300">
              Library scan failed: {library.error instanceof Error
                ? library.error.message : String(library.error)}
            </p>
          )}
          {library.isSuccess && entries.length === 0 && (
            <p className="text-[10px] italic leading-snug text-gray-500">
              No buildings found in this install's tileset-{tileset} maps.
            </p>
          )}
          {entries.length > 0 && (
            <>
              <div className="flex items-center gap-2">
                <input
                  type="text"
                  value={search}
                  onChange={(e) => setSearch(e.target.value)}
                  placeholder={`Search ${entries.length} buildings…`}
                  className="min-w-0 flex-1 rounded border border-gray-700 bg-gray-900 px-2 py-1 text-[11px] text-gray-100 placeholder:text-gray-600"
                />
                <label
                  className="flex shrink-0 items-center gap-1 text-[10px] text-gray-400"
                  title="ON: stamp everything inside (furniture, debris, objects). OFF: structure only (walls + roofs + floors + doors)."
                >
                  <input
                    type="checkbox"
                    checked={includeContents}
                    onChange={(e) => toggleContents(e.target.checked)}
                    className="h-3 w-3"
                  />
                  Include contents
                </label>
              </div>
              <div className="grid max-h-64 grid-cols-2 gap-1 overflow-y-auto pr-1">
                {filtered.map((e) => (
                  <div
                    key={e.id}
                    className={`relative flex flex-col items-center gap-1 rounded border p-1.5 text-center ${
                      e.id === selectedId && placementActive
                        ? "border-sky-400 bg-sky-900/50"
                        : "border-gray-700 bg-gray-900/60 hover:border-gray-500"
                    }`}
                  >
                    <button
                      type="button"
                      onClick={() => pick(e)}
                      disabled={running}
                      data-demo-card={e.id}
                      data-demo-dims={`${e.w}x${e.h}`}
                      className="flex w-full flex-col items-center gap-1 disabled:opacity-40"
                      title={`${e.label}\nfrom ${e.source_map}`
                        + (e.seen_in > 1 ? ` (seen in ${e.seen_in} maps)` : "")
                        + "\nClick to arm placement — then click the map to stamp."}
                    >
                      {e.thumb_png_b64 ? (
                        <img
                          src={`data:image/png;base64,${e.thumb_png_b64}`}
                          alt=""
                          className="h-20 w-full rounded border border-gray-800 bg-gray-950 object-contain"
                          loading="lazy"
                        />
                      ) : (
                        <div className="flex h-20 w-full items-center justify-center rounded border border-gray-800 bg-gray-950 text-[9px] text-gray-600">
                          no preview
                        </div>
                      )}
                      {renamingId !== e.id && (
                        <span className="line-clamp-2 text-[10px] leading-tight text-gray-200">
                          {displayName(e)}
                        </span>
                      )}
                      <span className="text-[9px] text-gray-500">
                        {e.w}×{e.h} · {e.room_count} room{e.room_count !== 1 ? "s" : ""}
                        {e.seen_in > 1 ? ` · ×${e.seen_in}` : ""}
                      </span>
                      {/* Family-sibling provenance badge — this building
                          was scanned from another tileset of the same
                          family and renders here with THIS tileset's
                          art (the intended per-sector reskin). */}
                      {e.source_tileset !== undefined
                        && e.source_tileset !== tileset && (
                        <span
                          className="rounded bg-amber-900/50 px-1 text-[8px] leading-3 text-amber-300/90"
                          title={`Scanned from family tileset #${e.source_tileset}`
                            + (e.source_tileset_name ? ` (${e.source_tileset_name})` : "")
                            + ` — stamps with this tileset's art.`}
                        >
                          from #{e.source_tileset}
                          {e.source_tileset_name ? ` ${e.source_tileset_name}` : ""}
                        </span>
                      )}
                    </button>
                    {renamingId === e.id ? (
                      <input
                        autoFocus
                        type="text"
                        value={renameDraft}
                        onChange={(ev) => setRenameDraft(ev.target.value)}
                        onBlur={() => commitRename(e.id)}
                        onKeyDown={(ev) => {
                          if (ev.key === "Enter") commitRename(e.id);
                          if (ev.key === "Escape") setRenamingId(null);
                          ev.stopPropagation();
                        }}
                        className="w-full rounded border border-sky-600 bg-gray-950 px-1 py-0.5 text-center text-[10px] text-gray-100"
                        placeholder={e.label}
                      />
                    ) : (
                      <button
                        type="button"
                        onClick={(ev) => {
                          ev.stopPropagation();
                          setRenamingId(e.id);
                          setRenameDraft(customNames[e.id] ?? "");
                        }}
                        className="absolute right-1 top-1 rounded bg-gray-950/70 px-1 text-[10px] text-gray-500 hover:text-gray-200"
                        title="Rename this building (saved on this machine)"
                      >
                        ✎
                      </button>
                    )}
                  </div>
                ))}
                {filtered.length === 0 && (
                  <p className="col-span-2 py-2 text-center text-[10px] italic text-gray-600">
                    No buildings match “{search}”.
                  </p>
                )}
              </div>
              {placementActive && selected && (
                <button
                  type="button"
                  onClick={() => setPlacement(null)}
                  className="w-full rounded border border-sky-500/70 bg-sky-600/30 px-3 py-2 text-sm font-medium text-sky-100 hover:bg-sky-600/50"
                >
                  ⏹ Done placing (or ESC)
                </button>
              )}
            </>
          )}
        </div>
      )}
    </div>
  );
}

// ──────────────────────────────────────────────────────────────────────
//  VariantMultiSelect — thumbnail toggle grid for the `subs` param
// ──────────────────────────────────────────────────────────────────────
//
// Adapted from MapForgeSector's VariantTileGrid (same drawCellInto
// drawing), but multi-select: each thumb toggles included/excluded
// instead of picking one. Equal weights, no typed strings — the panel
// composes the `subs` comma list from the selection.

function AtlasThumb({
  renderer, slot, sub, size,
}: {
  renderer: IsoRenderer;
  slot: number;
  sub: number;
  size: number;
}) {
  const ref = useRef<HTMLCanvasElement | null>(null);
  useEffect(() => {
    const c = ref.current;
    if (!c) return;
    const ctx = c.getContext("2d");
    if (!ctx) return;
    ctx.fillStyle = "#16120e";
    ctx.fillRect(0, 0, size, size);
    renderer.drawCellInto(ctx, slot, sub, size, size);
  }, [renderer, slot, sub, size]);
  return <canvas ref={ref} width={size} height={size} className="rounded-sm" />;
}

function VariantMultiSelect({
  renderer, slot, subs, included, onToggle, onAll,
}: {
  renderer: IsoRenderer;
  slot: number;
  subs: number[];
  included: number[];
  onToggle(sub: number): void;
  onAll(): void;
}) {
  const slotInfo = useMemo(() => renderer.getSlotInfo(slot),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [renderer, slot]);
  const allIn = included.length === subs.length;
  return (
    <div
      data-variant-grid
      className="rounded border border-wasteland-700 bg-wasteland-900/70 p-2"
    >
      <div className="flex items-baseline justify-between gap-2">
        <span
          className="min-w-0 truncate text-[10px] font-semibold uppercase tracking-wide text-wasteland-300"
          title={`${slotInfo.filename ?? `slot ${slot}`} — every checked variant is placed with equal probability. Click a thumbnail to exclude/include it.`}
        >
          Variants · {slotInfo.filename?.replace(/\.sti$/i, "") ?? `slot ${slot}`}
        </span>
        <span className="shrink-0 text-[10px] text-gray-500">
          {included.length}/{subs.length} in mix
          {!allIn && (
            <button
              type="button"
              onClick={onAll}
              className="ml-2 rounded border border-gray-600 bg-gray-800 px-1.5 py-px text-[9px] text-gray-200 hover:bg-gray-700"
              title="Include every variant again"
            >
              All
            </button>
          )}
        </span>
      </div>
      <div className="mt-1.5 flex max-h-44 flex-wrap items-start gap-1 overflow-y-auto pr-1">
        {subs.map((sub) => {
          const on = included.includes(sub);
          return (
            <button
              key={sub}
              type="button"
              data-variant-thumb={sub}
              data-included={on ? "1" : "0"}
              onClick={() => onToggle(sub)}
              title={on
                ? `Sub ${sub} — included. Click to exclude it from the mix.`
                : `Sub ${sub} — excluded. Click to include it.`}
              className={`flex flex-col items-center rounded border p-0.5 ${
                on
                  ? "border-emerald-500 bg-emerald-950/50"
                  : "border-gray-800 bg-gray-950 opacity-40 hover:opacity-70"
              }`}
            >
              <AtlasThumb renderer={renderer} slot={slot} sub={sub} size={36} />
              <span className={`mt-px text-[8px] leading-none ${
                on ? "text-emerald-300" : "text-gray-600"
              }`}>
                {sub}
              </span>
            </button>
          );
        })}
      </div>
    </div>
  );
}

// ──────────────────────────────────────────────────────────────────────
//  SlotSubPreview — live thumbnail + STI metadata
//  (moved here from the deleted modal MapForgeGeneratorWizard)
// ──────────────────────────────────────────────────────────────────────
//
// Renders alongside the slot/sub number inputs. The canvas re-draws
// whenever slot or sub changes — uses the same `drawCellInto` the
// inspector / palette use, so the appearance matches what'll land on
// the map at run time.
//
// When the requested (slot, sub) isn't in the cellMap (off-by-one
// 1-based sub mistake, slot out of range, etc.), the canvas falls back
// to a red "no sprite" placeholder so the user can see the misconfiguration
// BEFORE running the generator and discovering 25,600 invisible ops.

export function SlotSubPreview({
  renderer, slot, sub,
}: {
  renderer: IsoRenderer;
  slot: number;
  sub: number;
}) {
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const slotInfo = useMemo(
    () => renderer.getSlotInfo(slot),
    // Cell-Map shape doesn't change inside the panel's life — only
    // slot does. renderer reference change (e.g. atlas reload) is rare
    // and unmounts/remounts the panel.
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [renderer, slot],
  );
  // Whether the current (slot, sub) actually resolves to a cell. The
  // canvas draws nothing if not — we show a "no sprite" badge instead.
  const [hasCell, setHasCell] = useState<boolean>(true);

  useEffect(() => {
    const c = canvasRef.current;
    if (!c) return;
    const ctx = c.getContext("2d");
    if (!ctx) return;
    // Fill the box with a checkerboard background so transparent
    // sprites read against the dark panel correctly.
    const W = c.width;
    const H = c.height;
    ctx.fillStyle = "#1a1410";
    ctx.fillRect(0, 0, W, H);
    ctx.fillStyle = "#221a14";
    const cb = 8;
    for (let y = 0; y < H; y += cb) {
      for (let x = 0; x < W; x += cb) {
        if (((x / cb) + (y / cb)) % 2 === 0) {
          ctx.fillRect(x, y, cb, cb);
        }
      }
    }
    const ok = renderer.drawCellInto(ctx, slot, sub, W, H);
    setHasCell(ok);
  }, [renderer, slot, sub]);

  const filename = slotInfo.filename;
  const subCount = slotInfo.subCount;
  const subOutOfRange = sub < 1 || (subCount > 0 && sub > subCount);

  return (
    <div className="flex items-start gap-3 rounded border border-wasteland-700 bg-wasteland-900/70 p-2">
      <canvas
        ref={canvasRef}
        width={64}
        height={64}
        className="rounded border border-wasteland-800 bg-wasteland-950 flex-shrink-0"
      />
      <div className="flex-1 min-w-0 space-y-1">
        <div className="flex items-baseline gap-2">
          <span className="text-xs font-medium text-wasteland-200">
            slot {slot} · sub {sub}
          </span>
          {!hasCell && (
            <span
              className="text-[10px] text-rust-300 bg-rust-500/20 border border-rust-500/40 rounded px-1 py-0.5"
              title="The renderer's cellMap has no entry for this (slot, sub) pair. The fill will write data but nothing will draw."
            >
              ⚠ no sprite
            </span>
          )}
        </div>
        <div className="text-[11px] text-wasteland-500 font-mono truncate">
          {filename ?? <span className="italic">— no STI mapped to this slot —</span>}
        </div>
        {subCount > 0 && (
          <div className="text-[10px] text-wasteland-600">
            sub range: 1–{subCount}
            {subOutOfRange && (
              <span className="ml-2 text-rust-400">
                {sub < 1
                  ? "(sub must be ≥ 1 — JA2 .dat sub-indices are 1-based)"
                  : `(sub ${sub} exceeds this STI's frame count)`}
              </span>
            )}
          </div>
        )}
      </div>
    </div>
  );
}

/** Layer dropdown — special-cased instead of free-text because the
 *  set is fixed at six values and a typo would 400 the request. */
export const LAYER_NAMES = ["land", "objs", "shadows", "structs", "roofs", "onroofs"] as const;

export function ParamRow({
  param, value, onChange,
}: {
  param: GeneratorParamSchema;
  value: unknown;
  onChange(value: unknown): void;
}) {
  // De-wordified: the description lives in a hover tooltip, not an
  // inline paragraph (the panel read like documentation before). The ⓘ
  // affordance marks rows that actually carry one.
  const labelEl = (
    <div title={param.description || undefined}>
      <label
        htmlFor={`gen-wiz-${param.name}`}
        className="block text-xs font-medium text-wasteland-200"
      >
        {param.name}
        {param.description && (
          <span className="ml-1 cursor-help text-[9px] text-wasteland-600">ⓘ</span>
        )}
      </label>
    </div>
  );

  // bool → checkbox
  if (param.type === "bool") {
    return (
      <div className="flex items-start gap-3 py-1">
        <input
          id={`gen-wiz-${param.name}`}
          type="checkbox"
          checked={value === true}
          onChange={(e) => onChange(e.target.checked)}
          className="mt-1 accent-rust-500 w-4 h-4"
        />
        {labelEl}
      </div>
    );
  }

  // layer name → dropdown (instead of free text)
  if (param.name === "layer" && param.type === "str") {
    return (
      <div>
        {labelEl}
        <select
          id={`gen-wiz-${param.name}`}
          value={(value as string) ?? "land"}
          onChange={(e) => onChange(e.target.value)}
          className="mt-1 w-full rounded border border-wasteland-700 bg-wasteland-900 px-2 py-1.5 text-sm text-wasteland-100"
        >
          {LAYER_NAMES.map((l) => (
            <option key={l} value={l}>{l}</option>
          ))}
        </select>
      </div>
    );
  }

  // mode dropdown for the rect generator
  if (param.name === "mode" && param.type === "str") {
    return (
      <div>
        {labelEl}
        <select
          id={`gen-wiz-${param.name}`}
          value={(value as string) ?? "outline"}
          onChange={(e) => onChange(e.target.value)}
          className="mt-1 w-full rounded border border-wasteland-700 bg-wasteland-900 px-2 py-1.5 text-sm text-wasteland-100"
        >
          <option value="outline">outline (perimeter only)</option>
          <option value="fill">fill (every interior tile)</option>
        </select>
      </div>
    );
  }

  // bank generator: escarpment-vs-plateau as a 2-option segmented
  // control — one glance instead of two paragraph-length dropdowns.
  // Param values underneath are IDENTICAL ("escarpment" | "plateau").
  if (param.name === "bank_mode" && param.type === "str") {
    const cur = (value as string) ?? "escarpment";
    const opts = [
      { v: "escarpment", label: "Escarpment",
        hint: "The cliff line runs edge to edge across the whole map (vanilla's idiom); everything on the high side is raised." },
      { v: "plateau", label: "Plateau",
        hint: "Raise only the dragged rectangle — a freestanding mesa." },
    ];
    return (
      <div data-bank-mode>
        {labelEl}
        <div className="mt-1 flex overflow-hidden rounded border border-wasteland-700">
          {opts.map((o) => (
            <button
              key={o.v}
              type="button"
              data-bank-mode-opt={o.v}
              onClick={() => onChange(o.v)}
              title={o.hint}
              className={`flex-1 px-2 py-1.5 text-xs font-medium ${
                cur === o.v
                  ? "bg-emerald-700/40 text-emerald-100"
                  : "bg-wasteland-900 text-wasteland-400 hover:bg-wasteland-800 hover:text-wasteland-200"
              }`}
            >
              {o.label}
            </button>
          ))}
        </div>
      </div>
    );
  }
  // bank generator: which side is the high ground — a 3×3 compass of
  // toggle buttons. S / E (and center) are disabled: the engine only
  // draws S- and E-looking cliff faces, so an S/E-high ledge would face
  // away from the iso camera and render invisible. Values unchanged.
  if (param.name === "high_side" && param.type === "str") {
    const cur = (value as string) ?? "N";
    const HINTS: Record<string, string> = {
      N: "North half is high — the cliff line runs edge to edge.",
      W: "West half is high — the cliff line runs edge to edge.",
      NW: "NW quadrant is high — L-shaped cliff, both faces visible.",
      NE: "NE quadrant is high — south face visible.",
      SW: "SW quadrant is high — east face visible.",
      SE: "SE quadrant is high — ledge faces away from the camera (no visible cliff art, like vanilla's bare north rims).",
    };
    const DISABLED_HINT =
      "Not selectable — the engine only draws S- and E-looking cliff "
      + "faces, so this high side's ledge would face away from the iso "
      + "camera and be invisible.";
    const cells: Array<string | null> = [
      "NW", "N", "NE",
      "W", null, "E",
      "SW", "S", "SE",
    ];
    const enabled = new Set(["N", "W", "NW", "NE", "SW", "SE"]);
    return (
      <div data-bank-compass>
        {labelEl}
        <div className="mt-1 grid w-32 grid-cols-3 gap-px overflow-hidden rounded border border-wasteland-700 bg-wasteland-700">
          {cells.map((c, i) => {
            if (c === null) {
              return (
                <div
                  key={`c${i}`}
                  className="flex h-9 items-center justify-center bg-wasteland-950 text-wasteland-700"
                  title="Pick which side of your drag is the HIGH ground."
                >
                  ◈
                </div>
              );
            }
            const ok = enabled.has(c);
            return (
              <button
                key={c}
                type="button"
                data-high-side={c}
                disabled={!ok}
                onClick={() => ok && onChange(c)}
                title={ok ? HINTS[c] : DISABLED_HINT}
                className={`h-9 text-xs font-medium ${
                  !ok
                    ? "cursor-not-allowed bg-wasteland-950 text-wasteland-700 line-through"
                    : cur === c
                      ? "bg-emerald-700/50 text-emerald-100"
                      : "bg-wasteland-900 text-wasteland-300 hover:bg-wasteland-800 hover:text-wasteland-100"
                }`}
              >
                {c}
              </button>
            );
          })}
        </div>
      </div>
    );
  }

  // int/float with both min and max → slider + number input combo
  // (so the user can drag for rough exploration OR type for precision)
  if ((param.type === "int" || param.type === "float")
      && param.min !== null && param.max !== null) {
    const step = param.type === "int" ? 1 : (param.max - param.min) / 100;
    const numValue = typeof value === "number" ? value : (param.default as number) ?? param.min;
    return (
      <div>
        {labelEl}
        <div className="flex items-center gap-2 mt-1">
          <input
            type="range"
            min={param.min}
            max={param.max}
            step={step}
            value={numValue}
            onChange={(e) => {
              const raw = e.target.value;
              onChange(param.type === "int" ? parseInt(raw, 10) : parseFloat(raw));
            }}
            className="flex-1 accent-rust-500"
          />
          <input
            id={`gen-wiz-${param.name}`}
            type="number"
            min={param.min}
            max={param.max}
            step={param.type === "int" ? 1 : "any"}
            value={numValue}
            onChange={(e) => {
              const raw = e.target.value;
              const n = param.type === "int" ? parseInt(raw, 10) : parseFloat(raw);
              onChange(Number.isFinite(n) ? n : raw);
            }}
            className="w-20 rounded border border-wasteland-700 bg-wasteland-900 px-2 py-1 text-sm text-wasteland-100 font-mono"
          />
        </div>
        <div className="flex justify-between text-[10px] text-wasteland-600 mt-0.5 font-mono">
          <span>{param.min}</span>
          <span>{param.max}</span>
        </div>
      </div>
    );
  }

  // int/float without bounds → plain number input
  if (param.type === "int" || param.type === "float") {
    return (
      <div>
        {labelEl}
        <input
          id={`gen-wiz-${param.name}`}
          type="number"
          step={param.type === "int" ? 1 : "any"}
          min={param.min ?? undefined}
          max={param.max ?? undefined}
          value={value as number | string}
          onChange={(e) => {
            const raw = e.target.value;
            const n = param.type === "int" ? parseInt(raw, 10) : parseFloat(raw);
            onChange(Number.isFinite(n) ? n : raw);
          }}
          className="mt-1 w-full rounded border border-wasteland-700 bg-wasteland-900 px-2 py-1.5 text-sm text-wasteland-100 font-mono"
        />
      </div>
    );
  }

  // str default
  return (
    <div>
      {labelEl}
      <input
        id={`gen-wiz-${param.name}`}
        type="text"
        value={(value as string) ?? ""}
        onChange={(e) => onChange(e.target.value)}
        className="mt-1 w-full rounded border border-wasteland-700 bg-wasteland-900 px-2 py-1.5 text-sm text-wasteland-100 font-mono"
      />
    </div>
  );
}
