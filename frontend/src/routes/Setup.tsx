/**
 * Setup flow — six skippable single-decision steps (docs/SETUP_FLOW_SPEC.md).
 *
 * Anti-treadmill affordances (binding, from the Phase-3 review):
 *  - clickable step rail, any order
 *  - every step's default = "Keep current" (skip writes nothing)
 *  - nothing is written until Review (one batch, one backup; graphics
 *    deploy is a separate route with its own backup — Review says so)
 *  - persistent Close that exits to the Hub
 */
import { useMemo, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import {
  deployGraphics,
  formatApiError,
  getIniPresets,
  getSetupState,
  launchGame,
  markSetupOffered,
  setupApply,
} from "../lib/api";
import type { GraphicsComponent } from "../lib/schema";

const STEPS = ["Display", "Intro & UI", "Difficulty", "Quality of life", "Graphics", "Review"] as const;
type Step = (typeof STEPS)[number];

// Engine SCREEN_RESOLUTION codes (hardcoded — the schema's list is empty;
// verified against the engine's own table).
const ENGINE_RESOLUTIONS = [
  { code: "4", label: "1280 x 720" },
  { code: "5", label: "1024 x 768" },
  { code: "11", label: "1600 x 900" },
  { code: "19", label: "1680 x 1050" },
  { code: "20", label: "1920 x 1080" },
  { code: "22", label: "1920 x 1200" },
  { code: "23", label: "2560 x 1440" },
  { code: "24", label: "2560 x 1600" },
];
const DDRAW_RESOLUTIONS = ENGINE_RESOLUTIONS.map((r) => r.label.replace(/ /g, ""));

interface Staged {
  windowed: boolean | null;        // null = keep
  resolution: string | null;       // null = keep
  playIntro: boolean | null;
  tooltipScale: number | null;
  difficulty: string | null;       // preset wire id or null = keep
  qol: boolean;                    // apply builtin:quality_of_life
  deployGraphics: boolean;
}

const KEEP: Staged = {
  windowed: null, resolution: null, playIntro: null, tooltipScale: null,
  difficulty: null, qol: false, deployGraphics: false,
};

export default function Setup() {
  const navigate = useNavigate();
  const qc = useQueryClient();
  const [step, setStep] = useState<Step>("Display");
  const [staged, setStaged] = useState<Staged>(KEEP);
  const [done, setDone] = useState<{ applied: number; backups: string[] } | null>(null);

  const stateQ = useQuery({ queryKey: ["setup-state"], queryFn: getSetupState });
  const presetsQ = useQuery({ queryKey: ["ini-presets"], queryFn: getIniPresets });

  const offered = useMutation({ mutationFn: markSetupOffered });
  useMemo(() => {
    // Opening the flow counts as "offered" — the Hub banner won't re-show.
    offered.mutate();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const presetIds = useMemo(() => {
    const ids: string[] = [];
    if (staged.difficulty) ids.push(staged.difficulty);
    if (staged.qol) ids.push("builtin:quality_of_life");
    return ids;
  }, [staged]);

  const buildPayload = (dryRun: boolean) => ({
    display:
      staged.windowed != null || staged.resolution != null
        ? {
            ...(staged.windowed != null ? { windowed: staged.windowed } : {}),
            ...(staged.resolution != null ? { resolution: staged.resolution } : {}),
          }
        : undefined,
    intro:
      staged.playIntro != null || staged.tooltipScale != null
        ? {
            ...(staged.playIntro != null ? { play_intro: staged.playIntro } : {}),
            ...(staged.tooltipScale != null ? { tooltip_scale: staged.tooltipScale } : {}),
          }
        : undefined,
    preset_ids: presetIds,
    dry_run: dryRun,
  });

  const hasChanges =
    staged.windowed != null || staged.resolution != null ||
    staged.playIntro != null || staged.tooltipScale != null ||
    presetIds.length > 0 || staged.deployGraphics;

  const dryRunQ = useQuery({
    queryKey: ["setup-dryrun", JSON.stringify(staged)],
    queryFn: () => setupApply(buildPayload(true)),
    enabled: step === "Review" && hasChanges && (presetIds.length > 0 ||
      staged.windowed != null || staged.resolution != null ||
      staged.playIntro != null || staged.tooltipScale != null),
  });

  const applyAll = useMutation({
    mutationFn: async () => {
      const backups: string[] = [];
      let applied = 0;
      const needsIni = presetIds.length > 0 ||
        staged.windowed != null || staged.resolution != null ||
        staged.playIntro != null || staged.tooltipScale != null;
      if (needsIni) {
        const r = await setupApply(buildPayload(false));
        applied += r.applied ?? 0;
        if (r.backup_id) backups.push(r.backup_id);
      }
      if (staged.deployGraphics) {
        const g = await deployGraphics();
        backups.push(g.backup_id);
      }
      return { applied, backups };
    },
    onSuccess: (res) => {
      qc.invalidateQueries({ queryKey: ["ini-effective"] });
      qc.invalidateQueries({ queryKey: ["ini-overrides"] });
      qc.invalidateQueries({ queryKey: ["ini-summary"] });
      qc.invalidateQueries({ queryKey: ["graphics-status"] });
      qc.invalidateQueries({ queryKey: ["setup-state"] });
      setDone(res);
    },
  });
  const launch = useMutation({ mutationFn: () => launchGame() });

  const display = stateQ.data?.display;
  const intro = stateQ.data?.intro ?? {};
  const graphics = stateQ.data?.graphics.components ?? [];
  const runtimesMissing = graphics.filter((c: GraphicsComponent) => c.kind === "runtime" && !c.present);
  const difficultyPresets = (presetsQ.data?.presets ?? []).filter(
    (p) => p.id === "builtin:easier_combat" || p.id === "builtin:harder_combat");

  if (done) {
    return (
      <div className="mx-auto max-w-2xl px-6 py-10 space-y-4">
        <h1 className="text-2xl font-bold">Setup complete</h1>
        <p className="text-sm text-wasteland-300">
          {done.applied} change{done.applied === 1 ? "" : "s"} applied.
          {done.backups.length > 0 &&
            ` Backup${done.backups.length === 1 ? "" : "s"}: ${done.backups.join(", ")} (restorable from Settings → Backups).`}
        </p>
        <p className="text-sm text-wasteland-400">
          INI changes take effect on the next new game; display changes on the next launch.
        </p>
        <div className="flex gap-3">
          <button
            className="btn-primary"
            onClick={() => launch.mutate()}
            disabled={launch.isPending}
          >
            {launch.isPending ? "Launching..." : "Launch JA2"}
          </button>
          <Link to="/hub" className="btn-ghost text-sm self-center">Back to Hub</Link>
        </div>
        {launch.isError && (
          <div className="text-xs text-red-300">{formatApiError(launch.error)}</div>
        )}
      </div>
    );
  }

  return (
    <div className="mx-auto max-w-3xl px-6 py-8">
      <div className="flex items-center justify-between mb-4">
        <h1 className="text-2xl font-bold">Setup</h1>
        <button className="btn-ghost text-sm" onClick={() => navigate("/hub")}>
          Close
        </button>
      </div>

      {/* Clickable step rail — any order */}
      <nav className="flex gap-1 mb-6 flex-wrap" aria-label="Setup steps">
        {STEPS.map((s) => (
          <button
            key={s}
            onClick={() => setStep(s)}
            className={
              "px-3 py-1.5 text-sm rounded border " +
              (s === step
                ? "border-rust-500 text-rust-300 bg-rust-500/10"
                : "border-wasteland-700 text-wasteland-400 hover:text-wasteland-200")
            }
            aria-current={s === step ? "step" : undefined}
          >
            {s}
          </button>
        ))}
      </nav>

      {stateQ.isError && (
        <div className="text-sm text-red-300 mb-4">{formatApiError(stateQ.error)}</div>
      )}

      <div className="card min-h-[20rem]">
        {step === "Display" && display && (
          <StepShell
            title="Display"
            blurb={
              display.renderer === "cnc-ddraw"
                ? "This install uses cnc-ddraw; window settings are written to ddraw.ini."
                : "Window settings are written to Ja2.ini."
            }
          >
            <Choice
              label="Window mode"
              current={display.windowed ? "Windowed" : "Fullscreen"}
              value={staged.windowed}
              options={[
                { v: true, label: "Windowed" },
                { v: false, label: "Fullscreen" },
              ]}
              onChange={(v) => setStaged((s) => ({ ...s, windowed: v }))}
            />
            <Choice
              label="Resolution"
              current={String(
                display.renderer === "cnc-ddraw"
                  ? display.resolution || "renderer default"
                  : ENGINE_RESOLUTIONS.find((r) => r.code === display.resolution)?.label ?? display.resolution,
              )}
              value={staged.resolution}
              options={
                display.renderer === "cnc-ddraw"
                  ? DDRAW_RESOLUTIONS.map((v) => ({ v, label: v }))
                  : ENGINE_RESOLUTIONS.map((r) => ({ v: r.code, label: r.label }))
              }
              onChange={(v) => setStaged((s) => ({ ...s, resolution: v }))}
            />
          </StepShell>
        )}

        {step === "Intro & UI" && (
          <StepShell title="Intro & UI" blurb="Written to Ja2.ini; applies to all campaigns on this install.">
            <Choice
              label="Intro video"
              current={intro.PLAY_INTRO === "0" ? "Skipped" : "Plays"}
              value={staged.playIntro}
              options={[
                { v: false, label: "Skip" },
                { v: true, label: "Play" },
              ]}
              onChange={(v) => setStaged((s) => ({ ...s, playIntro: v }))}
            />
            <Choice
              label="Tooltip scale"
              current={`${intro.TOOLTIP_SCALE_FACTOR ?? "100"}%`}
              value={staged.tooltipScale}
              options={[100, 125, 150, 175, 200].map((v) => ({ v, label: `${v}%` }))}
              onChange={(v) => setStaged((s) => ({ ...s, tooltipScale: v }))}
            />
          </StepShell>
        )}

        {step === "Difficulty" && (
          <StepShell
            title="Difficulty"
            blurb="Applies a combat preset as per-campaign overrides. Takes effect on the next new game."
          >
            <Choice
              label="Combat difficulty"
              current="mod default"
              value={staged.difficulty}
              options={difficultyPresets.map((p) => ({ v: p.id, label: p.name }))}
              onChange={(v) => setStaged((s) => ({ ...s, difficulty: v }))}
            />
          </StepShell>
        )}

        {step === "Quality of life" && (
          <StepShell title="Quality of life" blurb="Skips the intro and enlarges tooltips (written to Ja2.ini).">
            <Choice
              label="Quality of life preset"
              current="not applied"
              value={staged.qol ? true : null}
              options={[{ v: true, label: "Apply" }]}
              onChange={(v) => setStaged((s) => ({ ...s, qol: v === true }))}
            />
          </StepShell>
        )}

        {step === "Graphics" && (
          <StepShell
            title="Graphics"
            blurb="Optional. Applies the golden cnc-ddraw + ReShade configuration."
          >
            {runtimesMissing.length > 0 ? (
              <div className="text-sm text-wasteland-400 space-y-2">
                <p>
                  Requires {runtimesMissing.map((c) => c.component).join(", ")} — not installed.
                  Set this up later in Settings → Graphics stack.
                </p>
              </div>
            ) : (
              <Choice
                label="Golden graphics config"
                current={
                  graphics.every((c) => c.matches) ? "active" : "not deployed"
                }
                value={staged.deployGraphics ? true : null}
                options={[{ v: true, label: "Deploy" }]}
                onChange={(v) => setStaged((s) => ({ ...s, deployGraphics: v === true }))}
              />
            )}
          </StepShell>
        )}

        {step === "Review" && (
          <div className="space-y-3">
            <h2 className="text-lg font-semibold">Review</h2>
            <ul className="text-sm space-y-1">
              <SummaryRow label="Window mode" staged={staged.windowed != null ? (staged.windowed ? "Windowed" : "Fullscreen") : null} />
              <SummaryRow label="Resolution" staged={staged.resolution} />
              <SummaryRow label="Intro video" staged={staged.playIntro != null ? (staged.playIntro ? "Play" : "Skip") : null} />
              <SummaryRow label="Tooltip scale" staged={staged.tooltipScale != null ? `${staged.tooltipScale}%` : null} />
              <SummaryRow
                label="Difficulty"
                staged={staged.difficulty
                  ? difficultyPresets.find((p) => p.id === staged.difficulty)?.name ?? staged.difficulty
                  : null}
              />
              <SummaryRow label="Quality of life" staged={staged.qol ? "Apply" : null} />
              <SummaryRow label="Graphics" staged={staged.deployGraphics ? "Deploy golden config" : null} />
            </ul>
            {!hasChanges && (
              <p className="text-sm text-wasteland-400">
                Nothing staged — every setting keeps its current value.
              </p>
            )}
            {dryRunQ.data?.plan && dryRunQ.data.plan.length > 0 && (
              <p className="text-xs text-wasteland-500">
                {dryRunQ.data.plan.reduce((n, f) => n + f.changes.length, 0)} INI change(s) across{" "}
                {dryRunQ.data.plan.length} file(s); one backup is taken first.
                {staged.deployGraphics && " Graphics deploy takes its own backup."}
              </p>
            )}
            {dryRunQ.isError && (
              <div className="text-xs text-red-300">{formatApiError(dryRunQ.error)}</div>
            )}
            {applyAll.isError && (
              <div className="text-xs text-red-300">{formatApiError(applyAll.error)}</div>
            )}
            <div className="flex gap-2">
              <button
                className="btn-primary"
                disabled={!hasChanges || applyAll.isPending || (dryRunQ.isLoading && hasChanges)}
                onClick={() => applyAll.mutate()}
              >
                {applyAll.isPending ? "Applying..." : "Apply"}
              </button>
              <button className="btn-ghost text-sm" onClick={() => navigate("/hub")}>
                {hasChanges ? "Cancel" : "Close"}
              </button>
            </div>
          </div>
        )}
      </div>

      <p className="text-xs text-wasteland-500 mt-3">
        Nothing is written until Review. Skipped settings keep their current values.
      </p>
    </div>
  );
}

function StepShell({ title, blurb, children }: { title: string; blurb: string; children: React.ReactNode }) {
  return (
    <div className="space-y-4">
      <div>
        <h2 className="text-lg font-semibold">{title}</h2>
        <p className="text-xs text-wasteland-400 mt-0.5">{blurb}</p>
      </div>
      {children}
    </div>
  );
}

function Choice<T extends string | number | boolean>({
  label,
  current,
  value,
  options,
  onChange,
}: {
  label: string;
  current: string;
  value: T | null;
  options: Array<{ v: T; label: string }>;
  onChange: (v: T | null) => void;
}) {
  return (
    <fieldset className="space-y-1">
      <legend className="text-sm text-wasteland-300 mb-1">{label}</legend>
      <label className="flex items-center gap-2 text-sm cursor-pointer">
        <input type="radio" checked={value == null} onChange={() => onChange(null)} />
        <span>Keep current ({current})</span>
      </label>
      {options.map((o) => (
        <label key={String(o.v)} className="flex items-center gap-2 text-sm cursor-pointer">
          <input type="radio" checked={value === o.v} onChange={() => onChange(o.v)} />
          <span>{o.label}</span>
        </label>
      ))}
    </fieldset>
  );
}

function SummaryRow({ label, staged }: { label: string; staged: string | null }) {
  return (
    <li className="flex justify-between border-b border-wasteland-800 pb-1">
      <span className="text-wasteland-300">{label}</span>
      {staged ? (
        <span className="text-rust-300">{staged}</span>
      ) : (
        <span className="text-wasteland-500">kept</span>
      )}
    </li>
  );
}
