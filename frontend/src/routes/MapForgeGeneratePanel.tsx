/**
 * Generate dock panel — the live-preview home of the MapForge generator
 * subsystem (UX Phase 2).
 *
 * Unlike the modal wizard (which covers the canvas), this is a DOCK
 * panel: the map stays visible while you configure, so the flow is
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
  listGenerators,
  runGenerator,
} from "../lib/mapforge";
import type { IsoRenderer } from "../lib/IsoRenderer";
import { useMapForgeLog } from "./MapForgeLog";
import { LAYER_NAMES, ParamRow, SlotSubPreview } from "./MapForgeGeneratorWizard";

interface XY { x: number; y: number }

export interface GeneratePanelProps {
  sessionId: string | null;
  renderer: IsoRenderer | null;
  readOnly: boolean;
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
  if (scheme === "corners" || scheme === "region") {
    const p = scheme === "corners" ? "" : "region_";
    const w = Math.abs(n(`${p}x2`) - n(`${p}x1`)) + 1;
    const h = Math.abs(n(`${p}y2`) - n(`${p}y1`)) + 1;
    return {
      picked: w * h > 1,
      text: `(${n(`${p}x1`)},${n(`${p}y1`)}) → (${n(`${p}x2`)},${n(`${p}y2`)}) · ${w}×${h} = ${(w * h).toLocaleString()} tiles`,
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
  sessionId, renderer, readOnly,
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

  const selectGenerator = (name: string) => {
    previewAbortRef.current?.abort();
    clearGhost();
    setPreviewCount(null);
    setSelectedName(name);
    const g = (list.data ?? []).find((x) => x.name === name);
    const defaults: Record<string, unknown> = {};
    if (g) for (const p of g.params) defaults[p.name] = p.default;
    setValues(defaults);
  };

  // ── Live preview: dry-run on every (debounced) param change ────────
  const valuesKey = JSON.stringify(values);
  useEffect(() => {
    if (!autoPreview || !sessionId || !selected || running || readOnly) return;
    if (scheme && !region.picked) return;   // wait for a region first
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
          applyGhostOps(ops);
          setPreviewCount(final.op_count ?? ops.length);
        })
        .catch(() => { /* aborted (params changed) or failed — keep last ghost */ })
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
    setRunning(true);
    const start = performance.now();
    renderer.beginStroke(`Generator: ${selected.label}`);
    try {
      const final = await runGenerator(sessionId, selected.name, values, (e) => {
        if ("op" in e) onOp((e as { op: unknown }).op);
      });
      renderer.endStroke();
      const ms = Math.round(performance.now() - start);
      log?.append({
        severity: final.ok ? "success" : "error",
        message: final.ok
          ? `${selected.label}: ${final.applied.toLocaleString()} ops in ${ms} ms (Ctrl+Z undoes the whole run)`
          : `${selected.label} failed: ${final.message ?? final.error ?? "unknown"}`,
      });
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
  const hasSlotSub = useMemo(
    () => (selected?.params ?? []).some((p) => p.name === "slot")
      && (selected?.params ?? []).some((p) => p.name === "sub"),
    [selected],
  );
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
        <p className="text-[10px] leading-snug text-gray-500">{selected.description}</p>
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
              onChange={(v) => setValues((prev) => ({ ...prev, [p.name]: v }))}
            />
          ))}
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
                      onChange={(v) => setValues((prev) => ({ ...prev, [p.name]: v }))}
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
