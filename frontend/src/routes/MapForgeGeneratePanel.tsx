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
  type GeneratorInfo,
  type GeneratorParamSchema,
  listGenerators,
  runGenerator,
} from "../lib/mapforge";
import type { IsoRenderer } from "../lib/IsoRenderer";
import { useMapForgeLog } from "./MapForgeLog";
import type { ActiveBrush } from "./MapForgePalette";

interface XY { x: number; y: number }

export interface GeneratePanelProps {
  sessionId: string | null;
  renderer: IsoRenderer | null;
  readOnly: boolean;
  /** The editor's active paint brush. Generators with slot/sub params
   * INHERIT it on selection (hunting slot numbers when the brush
   * already holds the right tree was the #1 usability complaint). */
  activeBrush: ActiveBrush | null;
  /** Ask the parent to enter region-pick mode on the canvas; `cb` fires
   * with the two picked corners. The panel stays open the whole time —
   * no modal close/reopen dance. */
  pickRegion(cb: (c1: XY, c2: XY) => void): void;
  /** Ghost the given (backend-shaped) ops into the local renderer ONLY
   * — bypasses undo/dirty entirely; parent blocks canvas tools while a
   * ghost is live. */
  applyGhostOps(ops: unknown[]): void;
  clearGhost(): void;
  ghostActive: boolean;
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

// Params hidden from the form (the region block owns them).
function hiddenParams(scheme: RegionScheme | null): Set<string> {
  return new Set(scheme ? SCHEME_PARAMS[scheme] : []);
}

export function MapForgeGeneratePanel({
  sessionId, renderer, readOnly, activeBrush,
  pickRegion, applyGhostOps, clearGhost, ghostActive,
  onOp, onComplete,
}: GeneratePanelProps) {
  const log = useMapForgeLog();
  const list = useQuery({
    queryKey: ["mapforge-generators"],
    queryFn: listGenerators,
    staleTime: Infinity,
  });

  const [selectedName, setSelectedName] = useState<string>("");
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
  const previewAbortRef = useRef<AbortController | null>(null);

  // Leaving the panel (or losing the session) always clears the ghost.
  useEffect(() => () => {
    previewAbortRef.current?.abort();
    clearGhostRef.current();
  }, []);
  useEffect(() => {
    if (!sessionId) clearGhostRef.current();
  }, [sessionId]);

  // True once the user EXPLICITLY set slot/sub (typed or clicked Use
  // brush) — from then on, brush changes stop auto-overwriting them.
  const slotTouchedRef = useRef(false);

  const selectGenerator = (name: string) => {
    previewAbortRef.current?.abort();
    clearGhost();
    setPreviewCount(null);
    setPreviewError(null);
    setSelectedName(name);
    slotTouchedRef.current = false;
    const g = (list.data ?? []).find((x) => x.name === name);
    const defaults: Record<string, unknown> = {};
    if (g) for (const p of g.params) defaults[p.name] = p.default;
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
    setValues(defaults);
  };

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
    }, 400);
    return () => clearTimeout(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [autoPreview, sessionId, selected, valuesKey, running, readOnly,
      scheme, region.picked]);

  // ── Apply (the real run) ────────────────────────────────────────────
  const apply = useCallback(async () => {
    if (!sessionId || !selected || !renderer || running) return;
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
  const primary = useMemo(
    () => (selected?.params ?? []).filter(
      (p) => !hidden.has(p.name) && p.name !== "seed"
        && !p.name.startsWith("corpus_") && p.name !== "biome",
    ),
    [selected, hidden],
  );
  const advanced = useMemo(
    () => (selected?.params ?? []).filter(
      (p) => !hidden.has(p.name)
        && (p.name === "seed" || p.name.startsWith("corpus_") || p.name === "biome"),
    ),
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
      {/* Generator picker */}
      <select
        value={selectedName}
        onChange={(e) => selectGenerator(e.target.value)}
        className="w-full rounded border border-gray-700 bg-gray-900 px-2 py-1.5 text-sm text-gray-100"
      >
        <option value="">— pick a generator —</option>
        {(list.data ?? []).map((g) => (
          <option key={g.name} value={g.name}>{g.label}</option>
        ))}
      </select>
      {selected && (
        <p className="line-clamp-3 text-[10px] leading-snug text-gray-500" title={selected.description}>
          {selected.description}
        </p>
      )}

      {selected && (
        <div className="min-h-0 flex-1 space-y-2 overflow-y-auto pr-1">
          {/* Region block */}
          {scheme && (
            <div className="rounded border border-amber-700/50 bg-amber-950/30 p-2">
              <div className="flex items-center justify-between gap-2">
                <div>
                  <div className="text-[10px] font-semibold uppercase tracking-wide text-amber-300">
                    Region
                  </div>
                  <div className={`mt-0.5 font-mono ${region.picked ? "text-gray-300" : "italic text-gray-500"}`}>
                    {region.picked ? region.text : "none — drag one on the map"}
                  </div>
                </div>
                <button
                  type="button"
                  onClick={() => pickRegion((c1, c2) => {
                    setValues((prev) => applyPickedRegion(scheme, prev, c1, c2));
                  })}
                  className="whitespace-nowrap rounded border border-amber-600/60 bg-amber-600/20 px-3 py-1.5 font-medium text-amber-100 hover:bg-amber-600/40"
                  title="Drag a box on the canvas (or click two corners)"
                >
                  🖱 {region.picked ? "Re-pick" : "Drag region"} →
                </button>
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

          {/* Params (sliders for bounded ints/floats via ParamRow) */}
          {primary.map((p) => (
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
          {hasSlotSub && renderer && (
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
  const labelEl = (
    <div>
      <label
        htmlFor={`gen-wiz-${param.name}`}
        className="block text-xs font-medium text-wasteland-200"
      >
        {param.name}
      </label>
      {param.description && (
        <p className="text-[11px] text-wasteland-500 mt-0.5">{param.description}</p>
      )}
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
