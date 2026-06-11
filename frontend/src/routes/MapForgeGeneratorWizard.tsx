/**
 * Generator wizard — modal GUI for the MapForge generator subsystem.
 *
 * Built as a 2-step flow:
 *   Step 1: Pick a generator from a card grid.
 *   Step 2: Configure parameters (form auto-built from the schema)
 *           and click Generate.
 *
 * While the run is in flight the wizard shows a progress overlay with
 * phase labels + per-op count. Cancel is "hard-close" — the stream is
 * abandoned (ops already applied stay applied; the user can Ctrl+Z to
 * unwind).
 *
 * This is the GUI peer to the `:gen <name> k=v ...` console command —
 * both call `runGenerator()` from lib/mapforge.ts so behavior is
 * identical regardless of entry surface.
 */
import { useEffect, useMemo, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";

import {
  type GeneratorEvent,
  type GeneratorInfo,
  type GeneratorParamSchema,
  listGenerators,
  runGenerator,
} from "../lib/mapforge";
import type { IsoRenderer } from "../lib/IsoRenderer";
import { useMapForgeLog } from "./MapForgeLog";

interface WizardProps {
  open: boolean;
  onClose(): void;
  sessionId: string | null;
  /** Called after a generator completes (success OR failure). Used by
   *  the parent to bump renderEpoch + undoDepth so the canvas refresh
   *  and undo stack reflect the generator's output. Passed `applied`
   *  = number of ops actually committed. */
  onComplete?(applied: number, ok: boolean): void;
  /** Called for each emitted op event during the stream. Parent
   *  uses this to mirror the op into the local IsoRenderer so the
   *  canvas reflects the generator output. Without this the
   *  backend's session updates but the frontend's renderer keeps
   *  showing pre-generator state (a user-reported bug). */
  onOp?(op: unknown): void;
  /** Local IsoRenderer instance, when a sector is open. Used by the
   *  slot+sub form rows to render live thumbnails + show STI
   *  filenames so the user can see what their `slot=N sub=M` inputs
   *  actually point at. Falls back to text-only inputs when null. */
  renderer?: IsoRenderer | null;
  /** When provided + non-null, the wizard opens directly to the
   *  configure step for this generator with `initialValues` prefilled.
   *  Used to restore wizard state after a side-trip (e.g. the
   *  rectangle corner picker closes the modal, lets the user click
   *  two tiles, then reopens with x1/y1/x2/y2 set). */
  initialGenerator?: string | null;
  initialValues?: Record<string, unknown> | null;
  /** Wizard asks the parent to enter rectangle-corner-pick mode.
   *  Parent closes the wizard, lets the user click two tiles on the
   *  canvas, then reopens the wizard via `initialGenerator` +
   *  `initialValues` with x1/y1/x2/y2 filled in from the picks. */
  onPickRectCorners?(currentValues: Record<string, unknown>): void;
}

type Step = "pick" | "configure" | "running" | "result";

export default function MapForgeGeneratorWizard({
  open, onClose, sessionId, onComplete, onOp, renderer,
  initialGenerator, initialValues, onPickRectCorners,
}: WizardProps) {
  const log = useMapForgeLog();
  const list = useQuery({
    queryKey: ["mapforge-generators"],
    queryFn: listGenerators,
    staleTime: Infinity,  // baked into sidecar; can't change at runtime
    enabled: open,
  });

  const [step, setStep] = useState<Step>("pick");
  const [selected, setSelected] = useState<GeneratorInfo | null>(null);
  const [paramValues, setParamValues] = useState<Record<string, unknown>>({});
  const [progress, setProgress] = useState<{
    phase: string;
    label: string;
    opCount: number;
    /** Expected total op count for the bar denominator. null/undefined
     *  → indeterminate spinner. Comes from the phase-start event's
     *  `total` field (added 2026-05-24 — generators now emit an upper
     *  bound on ops so the wizard can render a real fill bar). */
    total?: number;
  } | null>(null);
  const [result, setResult] = useState<{
    ok: boolean;
    applied: number;
    durationMs: number;
    error?: string;
  } | null>(null);

  // Reset all state when the wizard re-opens. The user expects a fresh
  // slate each time, not the last run's leftovers.
  //
  // EXCEPT when reopening from a side-trip (rectangle corner picker):
  // `initialGenerator` + `initialValues` tell us to restore the
  // pre-trip state instead of a fresh pick step. The list of
  // generators is staleTime: Infinity so it's already in cache from
  // the prior open.
  useEffect(() => {
    if (!open) return;
    if (initialGenerator && initialValues && (list.data ?? []).length > 0) {
      const g = (list.data ?? []).find((x) => x.name === initialGenerator);
      if (g) {
        setSelected(g);
        setParamValues(initialValues);
        setStep("configure");
        setProgress(null);
        setResult(null);
        return;
      }
    }
    setStep("pick");
    setSelected(null);
    setParamValues({});
    setProgress(null);
    setResult(null);
  }, [open, initialGenerator, initialValues, list.data]);

  // ESC key closes (only when not actively running — we don't want a
  // stray keypress mid-stream to dismiss the progress overlay).
  useEffect(() => {
    if (!open) return undefined;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape" && step !== "running") {
        e.preventDefault();
        onClose();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, step, onClose]);

  // Region generators must have a real region before Generate unlocks —
  // running with the degenerate default (0,0)→(0,0) was a silent
  // one-tile no-op that read as "the generator did nothing".
  const needsRegionPick = useMemo(() => {
    if (!selected || !onPickRectCorners) return false;
    const hasCorners = ["x1", "y1", "x2", "y2"].every(
      (n) => selected.params.some((p) => p.name === n),
    );
    if (!hasCorners) return false;
    const n = (v: unknown) => (typeof v === "number" ? v : 0);
    const w = Math.abs(n(paramValues.x2) - n(paramValues.x1)) + 1;
    const h = Math.abs(n(paramValues.y2) - n(paramValues.y1)) + 1;
    return w * h <= 1;
  }, [selected, paramValues, onPickRectCorners]);

  const pickGenerator = (g: GeneratorInfo) => {
    setSelected(g);
    // Initialize each param to its declared default.
    const defaults: Record<string, unknown> = {};
    for (const p of g.params) defaults[p.name] = p.default;
    setParamValues(defaults);
    setStep("configure");
  };

  const runSelected = async () => {
    if (!selected || !sessionId) return;
    setStep("running");
    setProgress({ phase: "init", label: "Starting…", opCount: 0 });
    setResult(null);
    const start = performance.now();
    let opCount = 0;
    // Open an undo stroke for the entire generator run so Ctrl+Z
    // reverts it as a single step. _mirrorGeneratorOp records the
    // pre-mutation snapshot for every (x, y, layer) before applying
    // the op; endStroke (called in the finally) commits the
    // accumulated snapshots as one UndoEntry. User feedback:
    // "fill worked but ctrl-z isn't undoing it".
    if (renderer) renderer.beginStroke(`Generator: ${selected.label}`);
    try {
      // Latched `total` — once a phase-start event provides it, keep
      // using it for the bar denominator across subsequent throttled
      // setProgress updates (the per-op branch doesn't re-emit total).
      let total: number | undefined = undefined;
      const final = await runGenerator(sessionId, selected.name, paramValues, (evt) => {
        if ("phase" in evt) {
          if (typeof evt.total === "number" && evt.total > 0) {
            total = evt.total;
          }
          setProgress({
            phase: evt.phase,
            label: evt.label,
            opCount,
            total,
          });
          log?.append({
            severity: "info",
            message: `[${selected.name}/${evt.phase}] ${evt.label}`,
          });
        } else if ("op" in evt) {
          opCount += 1;
          // Mirror the op into the parent's IsoRenderer so the canvas
          // updates as the stream progresses. Without this the
          // backend session ticks forward but the canvas keeps
          // showing pre-generator tiles (a user-reported wipe bug).
          onOp?.(evt.op);
          // Throttle progress UI updates to every 50 ops so React
          // doesn't choke on 25,000 setState calls during a wipe.
          // The bar's denominator (`total`) is latched from the
          // phase-start event so the bar smoothly grows even though
          // the count updates 1-in-50.
          if (opCount % 50 === 0) {
            setProgress((prev) => prev ? { ...prev, opCount, total } : prev);
          }
        }
      });
      // Flush the final progress count — the % 50 throttle in the op
      // branch can leave the displayed count up to 49 short of `applied`
      // for non-50-divisible totals (e.g. a cluster generator with
      // 12,347 ops would display 12,300).
      setProgress({
        phase: "done",
        label: `${final.applied} ops applied`,
        opCount: final.applied,
        total,
      });
      const durationMs = Math.round(performance.now() - start);
      setResult({
        ok: final.ok,
        applied: final.applied,
        durationMs,
        error: final.ok ? undefined : (final.message ?? final.error),
      });
      log?.append({
        severity: final.ok ? "success" : "error",
        message: final.ok
          ? `${selected.label}: ${final.applied} ops in ${durationMs} ms`
          : `${selected.label} failed: ${final.message ?? final.error ?? "unknown"}`,
      });
      // Commit the undo stroke BEFORE onComplete fires so the parent
      // can read the accurate renderer.undoDepth(). endStroke is a
      // no-op when no snapshots were recorded (e.g. applied=0).
      if (renderer) renderer.endStroke();
      onComplete?.(final.applied, final.ok);
      setStep("result");
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      setResult({ ok: false, applied: opCount, durationMs: Math.round(performance.now() - start), error: msg });
      log?.append({ severity: "error", message: `Generator stream failed: ${msg}` });
      // Commit the partial stroke so whatever the mirror managed to
      // apply before the throw is still undoable.
      if (renderer) renderer.endStroke();
      // Notify the parent so renderEpoch bumps + the canvas reflects
      // whatever the per-op mirror managed to apply before the stream
      // threw. `applied: opCount` reports how many ops the mirror saw
      // (best estimate; backend may have applied more before the
      // connection dropped). Without this call, a mid-stream throw
      // leaves the canvas stale despite real client-side mutations.
      onComplete?.(opCount, false);
      setStep("result");
    }
  };

  if (!open) return null;

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-labelledby="generator-wizard-title"
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 p-4"
      onClick={(e) => {
        // Backdrop click closes — except during a run, same as ESC.
        if (e.target === e.currentTarget && step !== "running") onClose();
      }}
    >
      <div className="w-full max-w-2xl max-h-[85vh] flex flex-col rounded-lg border border-rust-500/40 bg-wasteland-950 shadow-2xl overflow-hidden">
        {/* Header */}
        <div className="flex items-center justify-between px-4 py-3 border-b border-wasteland-800">
          <div>
            <h2 id="generator-wizard-title" className="text-lg font-semibold text-wasteland-100">
              {step === "pick" && "Generate map content"}
              {step === "configure" && `Configure: ${selected?.label}`}
              {step === "running" && `Running: ${selected?.label}`}
              {step === "result" && (result?.ok ? "✓ Complete" : "✕ Failed")}
            </h2>
            <p className="text-xs text-wasteland-500 mt-0.5">
              {step === "pick" && "Pick a generator. Each one streams ops live into the canvas; Ctrl+Z reverts the whole run."}
              {step === "configure" && (selected?.description ?? "")}
              {step === "running" && "Streaming ops into the session…"}
              {step === "result" && (result?.ok
                ? `${result.applied} ops in ${result.durationMs} ms`
                : "See the log panel for details.")}
            </p>
          </div>
          {step !== "running" && (
            <button
              type="button"
              onClick={onClose}
              aria-label="Close"
              className="text-wasteland-500 hover:text-wasteland-200 text-2xl leading-none px-2 -mt-1"
            >
              ×
            </button>
          )}
        </div>

        {/* Body — switches by step */}
        <div className="flex-1 overflow-y-auto p-4">
          {step === "pick" && (
            <GeneratorPicker
              list={list.data ?? []}
              loading={list.isLoading}
              error={list.error ? String(list.error) : null}
              onPick={pickGenerator}
            />
          )}
          {step === "configure" && selected && (
            <ConfigureForm
              generator={selected}
              values={paramValues}
              renderer={renderer ?? null}
              onChange={(name, value) =>
                setParamValues((prev) => ({ ...prev, [name]: value }))
              }
              onPickRectCorners={onPickRectCorners
                ? () => onPickRectCorners(paramValues)
                : undefined}
            />
          )}
          {step === "running" && (
            <RunningView progress={progress} />
          )}
          {step === "result" && result && (
            <ResultView result={result} />
          )}
        </div>

        {/* Footer — nav buttons */}
        <div className="flex items-center justify-between px-4 py-3 border-t border-wasteland-800 bg-wasteland-950/80">
          {step === "pick" && (
            <>
              <span className="text-xs text-wasteland-600">
                {list.data ? `${list.data.length} generators available` : ""}
              </span>
              <button
                type="button"
                onClick={onClose}
                className="text-xs px-3 py-1.5 rounded border border-wasteland-700 text-wasteland-300 hover:bg-wasteland-800"
              >
                Cancel
              </button>
            </>
          )}
          {step === "configure" && selected && (
            <>
              <button
                type="button"
                onClick={() => setStep("pick")}
                className="text-xs px-3 py-1.5 rounded border border-wasteland-700 text-wasteland-300 hover:bg-wasteland-800"
              >
                ← Back to generators
              </button>
              <button
                type="button"
                onClick={runSelected}
                disabled={!sessionId || needsRegionPick}
                className={
                  "text-xs px-4 py-1.5 rounded font-medium "
                  + (!sessionId || needsRegionPick
                    ? "border border-wasteland-700 bg-wasteland-900 text-wasteland-600 cursor-not-allowed"
                    : "border border-rust-500/60 bg-rust-500/25 text-rust-50 hover:bg-rust-500/40")
                }
                title={!sessionId
                  ? "Open a sector first"
                  : needsRegionPick
                    ? "Drag a region on the map first (🖱 button above)"
                    : "Run the generator against the current session"}
              >
                {!sessionId
                  ? "Open a sector first"
                  : needsRegionPick
                    ? "Pick a region first"
                    : "Generate →"}
              </button>
            </>
          )}
          {step === "running" && (
            <>
              <span className="text-xs text-wasteland-500">
                Streaming… cancel by closing the app or Ctrl+Z after completion.
              </span>
              <span className="text-xs text-wasteland-500">
                {progress?.opCount ?? 0} ops applied
              </span>
            </>
          )}
          {step === "result" && (
            <>
              <button
                type="button"
                onClick={() => setStep("pick")}
                className="text-xs px-3 py-1.5 rounded border border-wasteland-700 text-wasteland-300 hover:bg-wasteland-800"
              >
                ← Run another
              </button>
              <button
                type="button"
                onClick={onClose}
                className="text-xs px-4 py-1.5 rounded border border-rust-500/60 bg-rust-500/25 text-rust-50 hover:bg-rust-500/40"
              >
                Close
              </button>
            </>
          )}
        </div>
      </div>
    </div>
  );
}

// ──────────────────────────────────────────────────────────────────────────
//  Step 1: Pick a generator
// ──────────────────────────────────────────────────────────────────────────

function GeneratorPicker({
  list, loading, error, onPick,
}: {
  list: GeneratorInfo[];
  loading: boolean;
  error: string | null;
  onPick(g: GeneratorInfo): void;
}) {
  if (loading) {
    return <p className="text-sm text-wasteland-500">Loading generators…</p>;
  }
  if (error) {
    return <p className="text-sm text-rust-400">Couldn't load generators: {error}</p>;
  }
  if (list.length === 0) {
    return <p className="text-sm text-wasteland-500">No generators registered in this build.</p>;
  }
  return (
    <div className="grid grid-cols-1 sm:grid-cols-2 gap-2">
      {list.map((g) => (
        <button
          key={g.name}
          type="button"
          onClick={() => onPick(g)}
          className="text-left rounded border border-wasteland-700 hover:border-rust-500 bg-wasteland-900 hover:bg-wasteland-800 p-3 transition-colors"
        >
          <div className="text-sm font-semibold text-wasteland-100">{g.label}</div>
          <div className="text-xs text-wasteland-400 mt-1 line-clamp-3">
            {g.description}
          </div>
          <div className="text-[10px] text-wasteland-600 mt-2 font-mono">
            :gen {g.name} · {g.params.length} param{g.params.length === 1 ? "" : "s"}
          </div>
        </button>
      ))}
    </div>
  );
}

// ──────────────────────────────────────────────────────────────────────────
//  Step 2: Configure parameters
// ──────────────────────────────────────────────────────────────────────────

function ConfigureForm({
  generator, values, renderer, onChange, onPickRectCorners,
}: {
  generator: GeneratorInfo;
  values: Record<string, unknown>;
  renderer: IsoRenderer | null;
  onChange(name: string, value: unknown): void;
  /** When provided + the generator declares x1/y1/x2/y2 params, a
   *  "Pick corners on map" button appears. Clicking it asks the
   *  parent to close the wizard, run the canvas corner-picker, then
   *  reopen the wizard with the picked values. */
  onPickRectCorners?(): void;
}) {
  if (generator.params.length === 0) {
    return (
      <p className="text-sm text-wasteland-400">
        This generator has no parameters. Click <strong>Generate</strong> to run it.
      </p>
    );
  }
  // Rectangle-region generators get a drag-on-map picker as the PRIMARY
  // way to set the region — typing four corner coordinates into number
  // boxes for something you can see on screen was the single worst
  // piece of UI friction (user feedback 2026-06-10).
  const hasRectCorners = useMemo(
    () => ["x1", "y1", "x2", "y2"].every(
      (n) => generator.params.some((p) => p.name === n),
    ),
    [generator.params],
  );
  // When the canvas picker is available, the raw x1/y1/x2/y2 rows are
  // HIDDEN from the form (the region block shows the picked values, and
  // the picker is how you change them). They fall back to visible rows
  // only when no picker is wired (defensive).
  const hideCornerRows = hasRectCorners && !!onPickRectCorners;
  // Group params into "key" (essentials) vs "advanced" (region bounds,
  // seeds, etc.) for a cleaner form. Heuristic — anything with name
  // starting "region_" or named "seed" is advanced. Everything else is
  // a primary control.
  const advanced = useMemo(() => {
    return generator.params.filter(
      (p) => p.name.startsWith("region_") || p.name === "seed",
    );
  }, [generator.params]);
  const primary = useMemo(() => {
    return generator.params.filter(
      (p) => !p.name.startsWith("region_") && p.name !== "seed"
        && !(hideCornerRows && ["x1", "y1", "x2", "y2"].includes(p.name)),
    );
  }, [generator.params, hideCornerRows]);
  const [advancedOpen, setAdvancedOpen] = useState(false);

  // If the generator declares BOTH slot and sub params, we render a
  // single visual preview after them so the user sees what their input
  // points at. Pre-fix: number inputs with no visual feedback meant
  // typing "slot=1 sub=0" filled the whole map with frame[-1] and
  // produced a blank canvas.
  const hasSlotSub = useMemo(
    () => generator.params.some((p) => p.name === "slot")
       && generator.params.some((p) => p.name === "sub"),
    [generator.params],
  );
  const currentSlot = typeof values.slot === "number" ? values.slot : 0;
  const currentSub = typeof values.sub === "number" ? values.sub : 1;

  // Region readout for the picker block.
  const rx1 = typeof values.x1 === "number" ? values.x1 : 0;
  const ry1 = typeof values.y1 === "number" ? values.y1 : 0;
  const rx2 = typeof values.x2 === "number" ? values.x2 : 0;
  const ry2 = typeof values.y2 === "number" ? values.y2 : 0;
  const regionW = Math.abs(rx2 - rx1) + 1;
  const regionH = Math.abs(ry2 - ry1) + 1;
  const regionPicked = regionW * regionH > 1;

  return (
    <div className="space-y-3">
      {hideCornerRows && (
        <div className="rounded border border-rust-500/40 bg-rust-500/10 p-3 flex items-center justify-between gap-3">
          <div className="text-xs text-wasteland-200">
            <div className="font-medium text-rust-200">Region</div>
            {regionPicked ? (
              <div className="mt-0.5 font-mono text-wasteland-300">
                ({rx1},{ry1}) → ({rx2},{ry2})
                <span className="ml-2 text-wasteland-500">
                  {regionW}×{regionH} = {(regionW * regionH).toLocaleString()} tiles
                </span>
              </div>
            ) : (
              <div className="mt-0.5 text-wasteland-400 italic">
                No region picked yet — drag a box on the map.
              </div>
            )}
          </div>
          <button
            type="button"
            onClick={onPickRectCorners}
            className="text-sm px-4 py-2 rounded border border-rust-500/60 bg-rust-500/25 text-rust-50 hover:bg-rust-500/40 whitespace-nowrap font-medium"
            title="The wizard steps aside; drag a box on the canvas (or click two corners) and it reopens with the region set"
          >
            🖱 {regionPicked ? "Re-pick region on map" : "Drag region on map"} →
          </button>
        </div>
      )}
      {primary.map((p) => (
        <ParamRow
          key={p.name}
          param={p}
          value={values[p.name]}
          onChange={(v) => onChange(p.name, v)}
        />
      ))}
      {hasSlotSub && renderer && (
        <SlotSubPreview
          renderer={renderer}
          slot={currentSlot}
          sub={currentSub}
        />
      )}
      {advanced.length > 0 && (
        <div className="pt-3 border-t border-wasteland-800">
          <button
            type="button"
            onClick={() => setAdvancedOpen((o) => !o)}
            className="text-xs text-wasteland-500 hover:text-wasteland-300"
          >
            {advancedOpen ? "▼" : "▶"} Advanced ({advanced.length} options)
          </button>
          {advancedOpen && (
            <div className="space-y-3 mt-2">
              {advanced.map((p) => (
                <ParamRow
                  key={p.name}
                  param={p}
                  value={values[p.name]}
                  onChange={(v) => onChange(p.name, v)}
                />
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  );
}

// ──────────────────────────────────────────────────────────────────────
//  SlotSubPreview — live thumbnail + STI metadata
// ──────────────────────────────────────────────────────────────────────
//
// Renders alongside the slot/sub number inputs in ConfigureForm. The
// canvas re-draws whenever slot or sub changes — uses the same
// `drawCellInto` the inspector / palette use, so the appearance matches
// what'll land on the map at run time.
//
// When the requested (slot, sub) isn't in the cellMap (off-by-one
// 1-based sub mistake, slot out of range, etc.), the canvas falls back
// to a red "no sprite" placeholder so the user can see the misconfiguration
// BEFORE running the generator and discovering 25,600 invisible ops.

function SlotSubPreview({
  renderer, slot, sub,
}: {
  renderer: IsoRenderer;
  slot: number;
  sub: number;
}) {
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const slotInfo = useMemo(
    () => renderer.getSlotInfo(slot),
    // Cell-Map shape doesn't change inside the wizard's life — only
    // slot does. renderer reference change (e.g. atlas reload) is rare
    // and unmounts/remounts the wizard.
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
    // sprites read against the dark modal correctly.
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
const LAYER_NAMES = ["land", "objs", "shadows", "structs", "roofs", "onroofs"] as const;

function ParamRow({
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

// ──────────────────────────────────────────────────────────────────────────
//  Step 3: Running view (streaming progress)
// ──────────────────────────────────────────────────────────────────────────

function RunningView({
  progress,
}: {
  progress: {
    phase: string;
    label: string;
    opCount: number;
    total?: number;
  } | null;
}) {
  const total = progress?.total ?? 0;
  const opCount = progress?.opCount ?? 0;
  const hasTotal = total > 0;
  // Cap at 100% — the displayed count can briefly exceed `total` for
  // probabilistic generators if our upper bound was loose, and a bar
  // overflowing its track looks broken.
  const pct = hasTotal ? Math.min(100, Math.round((opCount / total) * 100)) : 0;
  return (
    <div className="space-y-3 py-4">
      <div className="flex items-center gap-3">
        <span className="inline-block w-5 h-5 rounded-full border-2 border-wasteland-700 border-t-rust-400 animate-spin" />
        <span className="text-sm text-wasteland-100">
          {progress?.label ?? "Starting…"}
        </span>
      </div>
      {/* Progress bar — determinate when the generator emitted a total
          (Wipe/Fill/Rect: exact; Scatter/Cluster/DensityFalloff: upper
          bound, may stop short of 100%). Indeterminate animated bar
          when the generator didn't report a denominator. */}
      <div className="ml-8 mr-2">
        {hasTotal ? (
          <div className="space-y-1">
            <div className="h-2 bg-wasteland-900 rounded-full overflow-hidden border border-wasteland-800">
              <div
                className="h-full bg-gradient-to-r from-rust-600 to-rust-400 transition-[width] duration-100 ease-out"
                style={{ width: `${pct}%` }}
                role="progressbar"
                aria-valuenow={pct}
                aria-valuemin={0}
                aria-valuemax={100}
              />
            </div>
            <div className="flex justify-between text-[10px] text-wasteland-500 font-mono">
              <span>
                {opCount.toLocaleString()} / {total.toLocaleString()} ops
              </span>
              <span>{pct}%</span>
            </div>
          </div>
        ) : (
          // No `total` yet — indeterminate pulsing bar. Tailwind's
          // built-in animate-pulse cycles opacity so the bar reads as
          // "active but no fixed denominator yet". Cheaper than wiring
          // up a custom @keyframes shimmer in the Tailwind config.
          <div className="h-2 bg-wasteland-900 rounded-full overflow-hidden border border-wasteland-800">
            <div className="h-full w-full bg-gradient-to-r from-rust-700/30 via-rust-500/60 to-rust-700/30 animate-pulse" />
          </div>
        )}
      </div>
      <div className="ml-8 text-xs text-wasteland-500">
        Phase: <span className="font-mono text-wasteland-300">{progress?.phase ?? "—"}</span>
      </div>
    </div>
  );
}

// ──────────────────────────────────────────────────────────────────────────
//  Step 4: Result view (success or failure)
// ──────────────────────────────────────────────────────────────────────────

function ResultView({
  result,
}: {
  result: { ok: boolean; applied: number; durationMs: number; error?: string };
}) {
  return (
    <div className="space-y-3 py-2">
      {result.ok ? (
        <div className="rounded border border-emerald-500/50 bg-emerald-500/10 p-3">
          <div className="text-emerald-200 font-medium">
            ✓ Applied {result.applied.toLocaleString()} ops in {result.durationMs} ms
          </div>
          <p className="text-xs text-emerald-300/80 mt-1">
            Canvas refreshes incrementally as ops arrive. Press Ctrl+Z to undo
            the whole run as a single step.
          </p>
        </div>
      ) : (
        <div className="rounded border border-rust-500/50 bg-rust-500/10 p-3">
          <div className="text-rust-200 font-medium">✕ Failed after {result.applied} ops</div>
          <p className="text-xs text-rust-300/80 mt-1 font-mono whitespace-pre-wrap">
            {result.error ?? "(unknown error)"}
          </p>
          {/* Detect the sidecar-restart symptom and surface a more
              actionable explanation. SESSION_NOT_FOUND specifically
              means our in-memory session_id is stale — the sidecar
              re-opens one automatically on the `sidecar:restarted`
              event, so by the time the user reads this and clicks
              "Run another" the new session should be live. */}
          {(result.error ?? "").includes("SESSION_NOT_FOUND") ? (
            <p className="text-xs text-amber-300 mt-2">
              The sidecar was restarted (rebuild or watchdog). A fresh
              session has been auto-opened in the background — click
              "Run another" to try again.
            </p>
          ) : result.applied > 0 ? (
            <p className="text-xs text-rust-300/80 mt-2">
              Whatever was applied stays in the undo stack — Ctrl+Z reverts.
            </p>
          ) : (
            <p className="text-xs text-rust-300/80 mt-2">
              No ops landed — nothing to undo.
            </p>
          )}
        </div>
      )}
    </div>
  );
}
