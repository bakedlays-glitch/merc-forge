/**
 * Generator panel — the GUI face of MapForge's compiled generator
 * subsystem. Lists every built-in generator from `GET /mapforge/generators`,
 * renders a form per generator's param schema, runs the selected one
 * via the streaming endpoint with per-op live updates.
 *
 * Sits in the right-rail tab area as a peer to Library + Palette. The
 * console (`:gen <name> k=v`) is the keyboard-driven equivalent — both
 * route through the same `runGenerator()` helper so behavior is
 * identical regardless of which entry point the user picks.
 */
import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";

import {
  type CorpusCoverage,
  type GeneratorEvent,
  type GeneratorInfo,
  type GeneratorParamSchema,
  getCorpusCoverage,
  listGenerators,
  runGenerator,
} from "../lib/mapforge";
import { useMapForgeLog } from "./MapForgeLog";

interface Props {
  sessionId: string | null;
  /** Called after a generator completes successfully so the parent can
   *  invalidate render queries + force a canvas refresh. The per-op
   *  events stream straight to the canvas via the same parent's
   *  `onGeneratorOp` callback; this `onDone` is just the "we're
   *  finished, you can stop showing the progress bar" signal. */
  onDone?: (applied: number) => void;
  /** Per-op callback so the parent can mirror each emitted edit into
   *  its local atlas state (for instant canvas updates without a
   *  full re-fetch). Pass undefined to skip — the panel still works
   *  with a single end-of-stream refresh. */
  onGeneratorOp?: (op: unknown) => void;
}

export default function MapForgeGeneratorPanel({ sessionId, onDone, onGeneratorOp }: Props) {
  const log = useMapForgeLog();
  const list = useQuery({
    queryKey: ["mapforge-generators"],
    queryFn: listGenerators,
    staleTime: Infinity,  // Generators are baked into the sidecar — they don't change at runtime
  });
  const coverageQuery = useQuery({
    queryKey: ["mapforge-corpus-coverage"],
    queryFn: getCorpusCoverage,
    staleTime: Infinity,  // Corpus is baked into the sidecar build too
  });
  const coverage = coverageQuery.data;

  const [selectedName, setSelectedName] = useState<string | null>(null);
  const selected = useMemo<GeneratorInfo | null>(() => {
    if (!list.data || !selectedName) return null;
    return list.data.find((g) => g.name === selectedName) ?? null;
  }, [list.data, selectedName]);

  // Live param values, keyed by param name. Initialized from each
  // generator's `default` when selection changes.
  const [paramValues, setParamValues] = useState<Record<string, unknown>>({});
  const onSelect = (g: GeneratorInfo) => {
    setSelectedName(g.name);
    const defaults: Record<string, unknown> = {};
    for (const p of g.params) defaults[p.name] = p.default;
    setParamValues(defaults);
    setLastRun(null);
  };

  const [running, setRunning] = useState(false);
  const [lastRun, setLastRun] = useState<{
    name: string;
    applied: number;
    ok: boolean;
    error?: string;
  } | null>(null);

  const onRun = async () => {
    if (!sessionId || !selected || running) return;
    setRunning(true);
    setLastRun(null);
    const start = performance.now();
    try {
      const final = await runGenerator(sessionId, selected.name, paramValues, (evt) => {
        // Phase events: log them. Op events: mirror into canvas state.
        if ("phase" in evt) {
          log?.append({
            severity: "info",
            message: `[${evt.phase}] ${evt.label}`,
          });
        } else if ("op" in evt) {
          onGeneratorOp?.(evt.op);
        }
      });
      const ms = Math.round(performance.now() - start);
      setLastRun({
        name: selected.name,
        applied: final.applied,
        ok: final.ok,
        error: final.ok ? undefined : final.message ?? final.error ?? "unknown",
      });
      log?.append({
        severity: final.ok ? "success" : "error",
        message: final.ok
          ? `${selected.label}: ${final.applied} ops applied in ${ms} ms`
          : `${selected.label} failed: ${final.message ?? final.error ?? "unknown"}`,
      });
      if (final.ok) onDone?.(final.applied);
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      setLastRun({ name: selected.name, applied: 0, ok: false, error: msg });
      log?.append({ severity: "error", message: `Generator stream failed: ${msg}` });
    } finally {
      setRunning(false);
    }
  };

  if (list.isLoading) {
    return <div className="text-xs text-wasteland-500 p-2">Loading generators…</div>;
  }
  if (list.isError) {
    return (
      <div className="text-xs text-rust-400 p-2">
        Couldn't load generators: {String(list.error)}
      </div>
    );
  }
  const gens = list.data ?? [];

  return (
    <div className="flex flex-col h-full overflow-hidden">
      <div className="border-b border-wasteland-700 px-2 py-1.5">
        <h3 className="text-xs font-semibold uppercase text-wasteland-400">
          Generators
        </h3>
        <p className="text-[10px] text-wasteland-500 mt-0.5">
          Built into MapForge. Each one streams ops live to the canvas + into the undo stack.
        </p>
      </div>

      {/* Generator picker */}
      <ul className="flex-shrink-0 max-h-40 overflow-y-auto border-b border-wasteland-800">
        {gens.length === 0 && (
          <li className="px-2 py-1 text-[10px] text-wasteland-600">
            No generators registered in this build.
          </li>
        )}
        {gens.map((g) => (
          <li key={g.name}>
            <button
              type="button"
              onClick={() => onSelect(g)}
              className={
                "w-full text-left px-2 py-1.5 text-xs hover:bg-wasteland-800/60 "
                + (selectedName === g.name
                  ? "bg-wasteland-800 text-wasteland-100"
                  : "text-wasteland-300")
              }
            >
              <div className="font-medium">{g.label}</div>
              <div className="text-[10px] text-wasteland-500 truncate">
                {g.description}
              </div>
            </button>
          </li>
        ))}
      </ul>

      {/* Param form + run button */}
      <div className="flex-1 overflow-y-auto p-2">
        {selected ? (
          <div className="space-y-2">
            <ParamForm
              params={selected.params}
              values={paramValues}
              coverage={coverage}
              onChange={(name, val) =>
                setParamValues((prev) => ({ ...prev, [name]: val }))
              }
            />
            <button
              type="button"
              onClick={onRun}
              disabled={!sessionId || running}
              className={
                "w-full rounded border px-3 py-1.5 text-xs font-medium "
                + (running
                  ? "border-blue-500/40 bg-blue-500/10 text-blue-200 cursor-wait"
                  : !sessionId
                    ? "border-wasteland-700 bg-wasteland-900 text-wasteland-600 cursor-not-allowed"
                    : "border-rust-500/60 bg-rust-500/15 text-rust-100 hover:bg-rust-500/25")
              }
              title={!sessionId
                ? "Open a sector first"
                : `Run ${selected.label} against the current session`}
            >
              {running
                ? "Running…"
                : !sessionId
                  ? "Open a sector first"
                  : `Run ${selected.label}`}
            </button>
            {lastRun && (
              <div
                className={
                  "rounded border px-2 py-1 text-[10px] "
                  + (lastRun.ok
                    ? "border-emerald-500/40 bg-emerald-500/10 text-emerald-200"
                    : "border-rust-500/40 bg-rust-500/10 text-rust-200")
                }
              >
                {lastRun.ok
                  ? `✓ ${lastRun.applied} ops applied`
                  : `✕ ${lastRun.error ?? "failed"}`}
              </div>
            )}
          </div>
        ) : (
          <p className="text-[10px] text-wasteland-500">
            Pick a generator above. The param form appears here.
          </p>
        )}
      </div>
    </div>
  );
}

function ParamForm({
  params,
  values,
  coverage,
  onChange,
}: {
  params: GeneratorParamSchema[];
  values: Record<string, unknown>;
  coverage?: CorpusCoverage;
  onChange: (name: string, value: unknown) => void;
}) {
  if (params.length === 0) {
    return (
      <p className="text-[10px] text-wasteland-500">
        No parameters. Click Run.
      </p>
    );
  }
  return (
    <div className="space-y-1.5">
      {params.map((p) => (
        <ParamRow
          key={p.name}
          param={p}
          value={values[p.name]}
          allValues={values}
          coverage={coverage}
          onChange={(v) => onChange(p.name, v)}
        />
      ))}
    </div>
  );
}

function ParamRow({
  param,
  value,
  onChange,
  coverage,
  allValues,
}: {
  param: GeneratorParamSchema;
  value: unknown;
  onChange: (v: unknown) => void;
  coverage?: CorpusCoverage;
  allValues?: Record<string, unknown>;
}) {
  const labelEl = (
    <label
      htmlFor={`gen-param-${param.name}`}
      className="block text-[10px] uppercase tracking-wide text-wasteland-500"
      title={param.description || undefined}
    >
      {param.name}
    </label>
  );
  // Corpus dropdowns: render corpus_source + biome as <select>s populated
  // from the live coverage report (map counts per cell; empty cells grayed).
  if (coverage?.available && (param.name === "corpus_source" || param.name === "biome")) {
    const isSource = param.name === "corpus_source";
    const selectedSource = String(allValues?.corpus_source ?? "");
    const options = isSource ? coverage.sources : coverage.biomes;
    return (
      <div>
        {labelEl}
        <select
          id={`gen-param-${param.name}`}
          value={(value as string) ?? ""}
          onChange={(e) => onChange(e.target.value)}
          className="w-full mt-0.5 rounded border border-wasteland-700 bg-wasteland-900 px-2 py-1 text-xs text-wasteland-100"
        >
          <option value="">(off)</option>
          {options.map((opt) => {
            const n = coverage.coverage?.[selectedSource]?.[opt]?.n_maps ?? 0;
            const empty = !isSource && !!selectedSource && n === 0;
            const optLabel = isSource
              ? opt
              : `${opt}${selectedSource ? ` (${n} maps)` : ""}${empty ? " — none" : ""}`;
            return (
              <option key={opt} value={opt} disabled={empty}>
                {optLabel}
              </option>
            );
          })}
        </select>
        {param.description && (
          <div className="text-[10px] text-wasteland-600 mt-0.5">{param.description}</div>
        )}
      </div>
    );
  }
  if (param.type === "bool") {
    return (
      <div className="flex items-center gap-2">
        <input
          id={`gen-param-${param.name}`}
          type="checkbox"
          checked={value === true}
          onChange={(e) => onChange(e.target.checked)}
          className="accent-rust-500"
        />
        <label
          htmlFor={`gen-param-${param.name}`}
          className="text-xs text-wasteland-200"
          title={param.description || undefined}
        >
          {param.name}
        </label>
      </div>
    );
  }
  if (param.type === "int" || param.type === "float") {
    return (
      <div>
        {labelEl}
        <input
          id={`gen-param-${param.name}`}
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
          className="w-full mt-0.5 rounded border border-wasteland-700 bg-wasteland-900 px-2 py-1 text-xs text-wasteland-100"
        />
        {param.description && (
          <div className="text-[10px] text-wasteland-600 mt-0.5">{param.description}</div>
        )}
      </div>
    );
  }
  // str (default)
  return (
    <div>
      {labelEl}
      <input
        id={`gen-param-${param.name}`}
        type="text"
        value={(value as string) ?? ""}
        onChange={(e) => onChange(e.target.value)}
        className="w-full mt-0.5 rounded border border-wasteland-700 bg-wasteland-900 px-2 py-1 text-xs text-wasteland-100 font-mono"
      />
      {param.description && (
        <div className="text-[10px] text-wasteland-600 mt-0.5">{param.description}</div>
      )}
    </div>
  );
}
