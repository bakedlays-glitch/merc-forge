/**
 * INI Editor — schema-driven engine-config editing (MercForge UI Phase 2).
 *
 * Two modes, one route (labels chosen by Will 2026-06-07 — name the thing
 * being written, not the intent):
 *  - OVERRIDE mode (default, session-only): writes `<stem>.Override` next
 *    to the saves (engine write profile); the game reads it on top of the
 *    INI. The INI itself is never touched; delete the override to undo.
 *  - EDIT INI mode (per-session opt-in, amber-tinted route, first-write
 *    confirm): edits the mod's actual INI files in Data-1.13 in place.
 *
 * Design rules from the 2026-06-07 adversarial review:
 *  - cross-file search is the primary navigation (all 2,141 keys)
 *  - dense rows with exactly ONE scan signal (the changed-dot)
 *  - tiered apply weight: silent commit / inline advisory / destructive
 *    confirm for savegame-risk keys
 *  - confidence + engine-loader metadata live in the EXPANDED row only
 *  - Author "Reset to reference value" (honest label), never "Revert"
 */
import { useEffect, useMemo, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import {
  applyIniChanges,
  createIniPreset,
  formatApiError,
  getAppSettings,
  getGameStatus,
  getIniEffective,
  getIniOverrides,
  getIniSchema,
  getIniSchemas,
  getIniSummary,
  openProfileFolder,
} from "../lib/api";
import IniPresetsPanel from "../components/IniPresetsPanel";
import type {
  IniChangeItem,
  IniEffectiveEntry,
  IniProperty,
  IniSchemaDoc,
} from "../lib/schema";
import ConfirmModal from "../components/ConfirmModal";

type Mode = "override" | "edit_ini";

// Session-scoped (NOT localStorage): Edit-INI mode must never survive an
// app restart — the canon-corruption footgun from the review.
const MODE_KEY = "mw2:ini.mode.session";
const FILE_KEY = "mw2:ini.lastFile.session";
const EDIT_INI_GATE_KEY = "mw2:ini.editIniConfirmed.session";

function storedMode(): Mode {
  const v = sessionStorage.getItem(MODE_KEY);
  return v === "edit_ini" ? "edit_ini" : "override";
}

// Selector groups. Options show filename + changed-count only; the
// selected file's content summary renders as one muted line under the
// header (a native <option> can't carry styled descriptions).
const FILE_GROUPS: Array<{ label: string; files: string[] }> = [
  { label: "Game Options", files: ["Ja2_Options.ini"] },
  { label: "Combat & Skills", files: ["APBPConstants.ini", "CTHConstants.ini", "Skills_Settings.INI", "Item_Settings.ini", "Morale_Settings.INI", "Taunts_Settings.INI"] },
  { label: "Strategic", files: ["RebelCommand_Settings.ini", "Reputation_Settings.INI", "Helicopter_Settings.INI", "AI.ini"] },
  { label: "System & Niche", files: ["Ja2.ini", "Mod_Settings.ini", "IntroVideos.ini", "Creatures_Settings.INI"] },
];

// Neutral content summaries for the selected file.
const FILE_SUMMARY: Record<string, string> = {
  "Ja2_Options.ini": "Game rules: combat, economy, AI, items, vehicles, system limits",
  "APBPConstants.ini": "Action point and breath point costs",
  "CTHConstants.ini": "Chance-to-hit calculation constants",
  "Skills_Settings.INI": "Traits, skills, experience, IMP creation",
  "Item_Settings.ini": "Item behavior: repair, attachments, ammo",
  "Morale_Settings.INI": "Morale changes per event",
  "Taunts_Settings.INI": "Combat taunt frequency and content",
  "RebelCommand_Settings.ini": "Militia and rebel network",
  "Reputation_Settings.INI": "Town loyalty changes",
  "Helicopter_Settings.INI": "Helicopter repair, refuel, SAM accuracy",
  "AI.ini": "Tactical AI plan factories",
  "Ja2.ini": "Display, install paths, active campaign",
  "Mod_Settings.ini": "Mod feature flags",
  "IntroVideos.ini": "Intro video selection",
  "Creatures_Settings.INI": "Creature spawning",
};

const PLAY_REFUSED = new Set(["ai.ini"]);

function isSavegameRisk(section: string, prop: IniProperty): boolean {
  return (
    /system limit/i.test(section) ||
    /UNLOADABLE|NOT RECOMMENDED/i.test(prop.description ?? "")
  );
}

// ───────────────────────────────────────────────────────────────────────

export default function IniEditor() {
  const qc = useQueryClient();
  const [mode, setModeRaw] = useState<Mode>(storedMode);
  const [file, setFileRaw] = useState<string>(
    () => sessionStorage.getItem(FILE_KEY) || "Ja2_Options.ini",
  );
  const [section, setSection] = useState<string | null>(null);
  const [search, setSearch] = useState("");
  const [thisFileOnly, setThisFileOnly] = useState(false);
  const [myChangesOnly, setMyChangesOnly] = useState(false);
  const [presetsOpen, setPresetsOpen] = useState(false);
  const [savePresetOpen, setSavePresetOpen] = useState(false);
  const [editIniGateOpen, setEditIniGateOpen] = useState(false);
  const [pendingEditIniWrite, setPendingEditIniWrite] = useState<(() => void) | null>(null);

  const setMode = (m: Mode) => {
    setModeRaw(m);
    sessionStorage.setItem(MODE_KEY, m);
  };
  const setFile = (f: string) => {
    setFileRaw(f);
    setSection(null);
    sessionStorage.setItem(FILE_KEY, f);
  };

  const schemasIndex = useQuery({ queryKey: ["ini-schemas"], queryFn: getIniSchemas, staleTime: 300_000 });
  const summaryQ = useQuery({ queryKey: ["ini-summary"], queryFn: getIniSummary, staleTime: 60_000 });
  const overridesQ = useQuery({ queryKey: ["ini-overrides"], queryFn: getIniOverrides, staleTime: 60_000 });
  const schemaQ = useQuery({
    queryKey: ["ini-schema", file],
    queryFn: () => getIniSchema(file),
    staleTime: Infinity,
  });
  const effectiveQ = useQuery({
    queryKey: ["ini-effective", file],
    queryFn: () => getIniEffective(file),
  });
  const settingsQ = useQuery({ queryKey: ["app-settings"], queryFn: getAppSettings, staleTime: 300_000 });
  const gameStatus = useQuery({
    queryKey: ["game-status"],
    queryFn: getGameStatus,
    refetchInterval: 5000,
    refetchOnWindowFocus: true,
  });

  // Cross-file search corpus: lazily fetch all schemas on first search.
  const [corpus, setCorpus] = useState<Record<string, IniSchemaDoc>>({});
  const corpusLoading = useRef(false);
  useEffect(() => {
    if (!search.trim() || thisFileOnly || corpusLoading.current) return;
    if (!schemasIndex.data) return;
    const missing = schemasIndex.data.editable.filter((f) => !corpus[f]);
    if (missing.length === 0) return;
    corpusLoading.current = true;
    // allSettled, not all: one failed schema fetch must not silently kill
    // cross-file search for every other file.
    Promise.allSettled(missing.map((f) => getIniSchema(f).then((doc) => [f, doc] as const)))
      .then((results) => {
        const pairs = results
          .filter((r): r is PromiseFulfilledResult<readonly [string, IniSchemaDoc]> => r.status === "fulfilled")
          .map((r) => r.value);
        if (pairs.length) setCorpus((c) => ({ ...c, ...Object.fromEntries(pairs) }));
      })
      .finally(() => {
        corpusLoading.current = false;
      });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [search, thisFileOnly, schemasIndex.data]);

  const apply = useMutation({
    mutationFn: (payload: { target: "canon" | "override"; changes: IniChangeItem[] }) =>
      applyIniChanges(payload),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["ini-effective"] });
      qc.invalidateQueries({ queryKey: ["ini-overrides"] });
      qc.invalidateQueries({ queryKey: ["ini-summary"] });
    },
  });

  // The write executor with the Edit-INI first-write-of-session gate.
  const executeWrite = (changes: IniChangeItem[], onDone?: (warning: string | null) => void) => {
    const target = mode === "edit_ini" ? "canon" : "override";
    const fire = () =>
      apply.mutate(
        { target, changes },
        {
          onSuccess: (res) => onDone?.(res.results[0]?.warning ?? null),
        },
      );
    if (mode === "edit_ini" && !sessionStorage.getItem(EDIT_INI_GATE_KEY)) {
      setPendingEditIniWrite(() => fire);
      setEditIniGateOpen(true);
      return;
    }
    fire();
  };

  const meta = effectiveQ.data;
  const overrideRefused = PLAY_REFUSED.has(file.toLowerCase()) && mode === "override";
  const summaryByFile = useMemo(() => {
    const m: Record<string, { override: number; edit_ini: number | null }> = {};
    for (const f of summaryQ.data?.files ?? []) {
      m[f.ini_file] = { override: f.override_changed, edit_ini: f.author_changed };
    }
    return m;
  }, [summaryQ.data]);

  // ── Search results (cross-file or this-file) ──
  const searchResults = useMemo(() => {
    const q = search.trim().toLowerCase();
    if (!q) return null;
    const docs: Array<[string, IniSchemaDoc]> = thisFileOnly
      ? schemaQ.data
        ? [[file, schemaQ.data]]
        : []
      : Object.entries(corpus).length
        ? Object.entries(corpus)
        : schemaQ.data
          ? [[file, schemaQ.data]]
          : [];
    // Rank: key-name hit (2) > section-name hit (1) > description hit (0).
    // Section matching matters: e.g. Skills_Settings.INI's [Ranger]
    // section's keys never say "ranger" — without it the trait sections
    // are unfindable (Gate-3 finding).
    const out: Array<{ file: string; section: string; prop: IniProperty; nameHit: boolean; sectHit: boolean; rank: number }> = [];
    for (const [fname, doc] of docs) {
      for (const sect of doc.sections) {
        const sectHit = sect.name.toLowerCase().includes(q);
        for (const prop of sect.properties) {
          const nameHit = prop.name.toLowerCase().includes(q);
          const descHit = (prop.description ?? "").toLowerCase().includes(q);
          if (nameHit || sectHit || descHit) {
            out.push({
              file: fname, section: sect.name, prop, nameHit, sectHit,
              rank: nameHit ? 2 : sectHit ? 1 : 0,
            });
          }
        }
      }
    }
    out.sort((a, b) => b.rank - a.rank || a.prop.name.localeCompare(b.prop.name));
    return out.slice(0, 150);
  }, [search, thisFileOnly, corpus, schemaQ.data, file]);

  const currentSchema = schemaQ.data;
  const sections = currentSchema?.sections ?? [];
  const activeSection = section ?? sections[0]?.name ?? null;

  const sectionChangedCounts = useMemo(() => {
    const counts: Record<string, number> = {};
    const sum = summaryQ.data?.files.find((f) => f.ini_file === file);
    if (!sum) return counts;
    const src = mode === "override" ? sum.play_sections : sum.author_sections;
    return src ?? counts;
  }, [summaryQ.data, file, mode]);

  const effEntry = (sectName: string, key: string): IniEffectiveEntry | undefined => {
    const sects = meta?.sections;
    if (!sects) return undefined;
    for (const [sn, keys] of Object.entries(sects)) {
      if (sn.toLowerCase() !== sectName.toLowerCase()) continue;
      for (const [k, v] of Object.entries(keys)) {
        if (k.toLowerCase() === key.toLowerCase()) return v;
      }
    }
    return undefined;
  };

  const isChanged = (sectName: string, prop: IniProperty): boolean => {
    const e = effEntry(sectName, prop.name);
    if (!e) return false;
    if (mode === "override") return e.override_active;
    if (e.stock_value == null || e.value == null) return false;
    return e.source !== "override" && e.source !== "default" && e.value !== e.stock_value;
  };

  const hasBaseline = Boolean(settingsQ.data?.baseline_install_path);

  return (
    <div
      className={
        "min-h-screen " +
        (mode === "edit_ini" ? "bg-amber-950/15 border-t-2 border-amber-600" : "")
      }
    >
      <div className="mx-auto max-w-7xl px-6 py-6">
        {/* ── Header ── */}
        <div className="flex items-center justify-between mb-3">
          <h1 className="text-2xl font-bold">INI Editor</h1>
          <Link to="/hub" className="text-sm text-wasteland-400 hover:text-rust-400">
            ← Back to Hub
          </Link>
        </div>

        <div className="flex items-center gap-3 flex-wrap mb-2">
          {/* File selector — grouped, with changed counts */}
          <select
            className="input w-auto min-w-[20rem]"
            value={file}
            onChange={(e) => setFile(e.target.value)}
          >
            {FILE_GROUPS.map((g) => (
              <optgroup key={g.label} label={g.label}>
                {g.files.map((f) => {
                  const c = summaryByFile[f];
                  const n = mode === "override" ? c?.override : c?.edit_ini;
                  return (
                    <option key={f} value={f}>
                      {f}
                      {n ? `  (${n} changed)` : ""}
                    </option>
                  );
                })}
              </optgroup>
            ))}
          </select>

          {/* Mode switch — session-scoped. Labels name the thing being
              written: an override file vs the INI itself. */}
          <div className="flex rounded border border-wasteland-700 overflow-hidden" role="group" aria-label="Write mode">
            <button
              className={
                "px-4 py-1.5 text-sm font-semibold " +
                (mode === "override"
                  ? "bg-rust-500/30 text-rust-300"
                  : "bg-wasteland-900 text-wasteland-400 hover:text-wasteland-200")
              }
              aria-pressed={mode === "override"}
              onClick={() => setMode("override")}
              title="Write a separate override file next to your saves — the INI itself stays untouched"
            >
              Override
            </button>
            <button
              className={
                "px-4 py-1.5 text-sm font-semibold " +
                (mode === "edit_ini"
                  ? "bg-amber-600/40 text-amber-200"
                  : "bg-wasteland-900 text-wasteland-400 hover:text-wasteland-200")
              }
              aria-pressed={mode === "edit_ini"}
              onClick={() => setMode("edit_ini")}
              title="Edit the INI file itself (Data-1.13) — changes the mod's actual file"
            >
              Edit INI
            </button>
          </div>

          <button
            className={
              "text-sm px-3 py-1.5 rounded border " +
              (myChangesOnly
                ? "border-rust-500 text-rust-300 bg-rust-500/10"
                : "border-wasteland-700 text-wasteland-400 hover:text-wasteland-200")
            }
            onClick={() => setMyChangesOnly((v) => !v)}
          >
            My changes
            {mode === "override" && overridesQ.data ? ` (${overridesQ.data.overrides.length})` : ""}
          </button>

          <button
            className={
              "text-sm px-3 py-1.5 rounded border " +
              (presetsOpen
                ? "border-rust-500 text-rust-300 bg-rust-500/10"
                : "border-wasteland-700 text-wasteland-400 hover:text-wasteland-200")
            }
            onClick={() => {
              setPresetsOpen((v) => !v);
              setSearch("");
            }}
          >
            Presets
          </button>

          <button
            className="text-xs text-wasteland-400 hover:text-rust-400 underline underline-offset-2 disabled:opacity-40 disabled:no-underline"
            disabled={mode !== "override" || !(overridesQ.data?.overrides.length)}
            title={
              mode !== "override"
                ? "Presets are saved from Override mode (Edit INI changes are diffs against your reference install, not portable values)"
                : !overridesQ.data?.overrides.length
                  ? "No overrides to save"
                  : "Save the current overrides as an install preset"
            }
            onClick={() => setSavePresetOpen(true)}
          >
            Save as preset
          </button>

          <button
            className="text-xs text-wasteland-400 hover:text-rust-400 underline underline-offset-2"
            onClick={() => void openProfileFolder().catch(() => undefined)}
            title="Open the active campaign's profile folder in Explorer"
          >
            Open profile folder
          </button>
        </div>

        {/* Selected-file summary + write destination. Neutral, factual. */}
        <p className="text-xs text-wasteland-500 mb-2">
          {FILE_SUMMARY[file]}
          {(() => {
            const idx = schemasIndex.data?.schemas.find((s) => s.ini_file === file);
            return idx ? ` · ${idx.properties} settings in ${idx.sections} section${idx.sections === 1 ? "" : "s"}` : "";
          })()}
        </p>
        <div
          className={
            "rounded border px-3 py-2 text-sm mb-3 " +
            (mode === "edit_ini"
              ? "border-amber-700 bg-amber-900/20 text-amber-200"
              : "border-wasteland-700 bg-wasteland-900 text-wasteland-300")
          }
        >
          {mode === "edit_ini" ? (
            <>
              Writes to{" "}
              <code className="font-mono">
                {file === "Ja2.ini" ? "Ja2.ini" : `Data-1.13\\${file}`}
              </code>{" "}
              directly. Applies to all campaigns.
            </>
          ) : file === "Ja2.ini" ? (
            <>
              <code className="font-mono">Ja2.ini</code> has no override mechanism. Edits change
              the file directly and apply to all campaigns.
            </>
          ) : overrideRefused ? (
            <>
              <code className="font-mono">{file}</code> has no override mechanism. Use Edit INI
              mode to change the file directly.
            </>
          ) : (
            <>
              Writes to{" "}
              <code className="font-mono">
                {meta?.profile_root ? `${meta.profile_root}\\` : ""}
                {meta?.override_file ?? ""}
              </code>
              . The INI file is not modified; removing the override restores it.
            </>
          )}
        </div>

        {/* Status banners */}
        {gameStatus.data?.running && (
          <div className="rounded border border-red-800 bg-red-950/40 px-3 py-2 text-sm text-red-200 mb-3">
            {gameStatus.data.exe_name} is running. Writes are disabled until the game closes.
          </div>
        )}
        {meta?.vfs_mismatch && (
          <div className="rounded border border-amber-700 bg-amber-900/20 px-3 py-2 text-sm text-amber-200 mb-3">
            Ja2.ini's active VFS config differs from this install registration. Overrides may
            target a different campaign than the engine loads. Use "Apply VFS" on the Hub first.
          </div>
        )}
        {mode === "edit_ini" && !hasBaseline && (
          <div className="rounded border border-wasteland-700 bg-wasteland-900 px-3 py-2 text-xs text-wasteland-400 mb-3">
            No reference install set. Change indicators and "Reset to reference" are unavailable.
            Configure one in <Link to="/settings" className="underline">Settings</Link>.
          </div>
        )}

        {/* Search */}
        <div className="flex items-center gap-3 mb-4">
          <input
            className="input flex-1"
            placeholder={thisFileOnly ? `Search ${file}` : "Search all INI files"}
            value={search}
            onChange={(e) => setSearch(e.target.value)}
          />
          <label className="flex items-center gap-1.5 text-xs text-wasteland-400 cursor-pointer whitespace-nowrap">
            <input
              type="checkbox"
              checked={thisFileOnly}
              onChange={(e) => setThisFileOnly(e.target.checked)}
            />
            this file only
          </label>
        </div>

        {/* ── Body ── */}
        {presetsOpen ? (
          <IniPresetsPanel />
        ) : searchResults ? (
          <SearchResults
            results={searchResults}
            currentFile={file}
            query={search}
            onPick={(f, s) => {
              if (f !== file) setFile(f);
              setSection(s);
              setSearch("");
            }}
          />
        ) : (
          <div className="flex gap-4">
            {/* Section sidebar */}
            <nav className="w-64 shrink-0 max-h-[70vh] overflow-y-auto border border-wasteland-700 rounded">
              {sections.map((s) => {
                const active = s.name === activeSection;
                const changed = sectionChangedCounts[s.name];
                return (
                  <button
                    key={s.name}
                    onClick={() => setSection(s.name)}
                    className={
                      "w-full text-left px-3 py-1.5 text-sm border-l-2 flex items-center justify-between gap-2 " +
                      (active
                        ? "border-rust-500 bg-wasteland-800 text-rust-300"
                        : "border-transparent text-wasteland-300 hover:bg-wasteland-800")
                    }
                  >
                    <span className="truncate">{s.name}</span>
                    <span className="text-xs text-wasteland-500 whitespace-nowrap">
                      {changed ? <span className="text-rust-400 font-semibold">{changed}● </span> : null}
                      {s.properties.length}
                    </span>
                  </button>
                );
              })}
            </nav>

            {/* Rows for the active section (or My-changes across sections) */}
            <div className="flex-1 min-w-0 max-h-[70vh] overflow-y-auto pr-1">
              {effectiveQ.isError && (
                <div className="text-sm text-red-300 p-3">{formatApiError(effectiveQ.error)}</div>
              )}
              {myChangesOnly ? (
                <MyChangesList
                  mode={mode}
                  file={file}
                  sections={sections}
                  isChanged={isChanged}
                  effEntry={effEntry}
                  disabled={overrideRefused || Boolean(gameStatus.data?.running)}
                  hasBaseline={hasBaseline}
                  executeWrite={executeWrite}
                  applyPending={apply.isPending}
                  applyError={apply.isError ? formatApiError(apply.error) : null}
                />
              ) : (
                sections
                  .filter((s) => s.name === activeSection)
                  .map((s) => (
                    <div key={s.name}>
                      {s.description && (
                        <p className="text-xs text-wasteland-400 whitespace-pre-line mb-3 px-1">
                          {s.description.slice(0, 400)}
                        </p>
                      )}
                      {s.properties.map((p) => (
                        <KeyRow
                          key={p.name}
                          file={file}
                          section={s.name}
                          prop={p}
                          entry={effEntry(s.name, p.name)}
                          changed={isChanged(s.name, p)}
                          mode={mode}
                          disabled={overrideRefused || Boolean(gameStatus.data?.running)}
                          hasBaseline={hasBaseline}
                          executeWrite={executeWrite}
                          applyPending={apply.isPending}
                        />
                      ))}
                    </div>
                  ))
              )}
              {apply.isError && (
                <div className="text-sm text-red-300 p-2">{formatApiError(apply.error)}</div>
              )}
            </div>
          </div>
        )}

        {savePresetOpen && overridesQ.data && (
          <SavePresetDialog
            overrides={overridesQ.data.overrides}
            onClose={() => setSavePresetOpen(false)}
            onSaved={() => {
              setSavePresetOpen(false);
              qc.invalidateQueries({ queryKey: ["ini-presets"] });
              setPresetsOpen(true);
            }}
          />
        )}

        {/* Edit-INI first-write-of-session gate */}
        <ConfirmModal
          open={editIniGateOpen}
          title="Edit INI file?"
          destructive
          body={
            <div className="space-y-2 text-sm">
              <p>
                This modifies the file under <code className="font-mono">Data-1.13\</code>{" "}
                directly. It applies to all campaigns.
              </p>
              <p className="text-wasteland-400 text-xs">
                A backup snapshot is taken first (restorable from Settings → Backups). This
                confirmation appears once per session.
              </p>
            </div>
          }
          confirmLabel="Edit INI"
          onConfirm={() => {
            sessionStorage.setItem(EDIT_INI_GATE_KEY, "1");
            setEditIniGateOpen(false);
            pendingEditIniWrite?.();
            setPendingEditIniWrite(null);
          }}
          onCancel={() => {
            setEditIniGateOpen(false);
            setPendingEditIniWrite(null);
          }}
        />
      </div>
    </div>
  );
}

// ───────────────────────────────────────────────────────────────────────
//  Search results
// ───────────────────────────────────────────────────────────────────────

function Highlight({ text, query }: { text: string; query: string }) {
  const i = text.toLowerCase().indexOf(query.toLowerCase());
  if (i < 0) return <>{text}</>;
  return (
    <>
      {text.slice(0, i)}
      <mark className="bg-rust-500/40 text-inherit rounded-sm">{text.slice(i, i + query.length)}</mark>
      {text.slice(i + query.length)}
    </>
  );
}

function SearchResults({
  results,
  currentFile,
  query,
  onPick,
}: {
  results: Array<{ file: string; section: string; prop: IniProperty; nameHit: boolean; sectHit: boolean }>;
  currentFile: string;
  query: string;
  onPick: (file: string, section: string) => void;
}) {
  const byFile = useMemo(() => {
    const m = new Map<string, typeof results>();
    for (const r of results) {
      if (!m.has(r.file)) m.set(r.file, []);
      m.get(r.file)!.push(r);
    }
    return [...m.entries()];
  }, [results]);

  if (results.length === 0) {
    return <p className="text-sm text-wasteland-400 p-4">No keys match.</p>;
  }
  return (
    <div className="max-h-[70vh] overflow-y-auto space-y-4">
      {byFile.map(([fname, rows]) => (
        <div key={fname}>
          <h3 className="text-sm font-semibold text-wasteland-300 mb-1">
            {fname}
            {fname === currentFile && <span className="text-xs text-wasteland-500"> (current)</span>}
            <span className="text-xs text-wasteland-500"> · {rows.length}</span>
          </h3>
          <div className="space-y-0.5">
            {rows.map((r) => (
              <button
                key={`${r.file}/${r.section}/${r.prop.name}`}
                className="w-full text-left px-3 py-1.5 rounded hover:bg-wasteland-800 flex items-baseline gap-3"
                onClick={() => onPick(r.file, r.section)}
              >
                <span className="font-mono text-sm text-wasteland-100">
                  <Highlight text={r.prop.name} query={query} />
                </span>
                <span className="text-xs text-wasteland-500 truncate">
                  {r.sectHit ? <Highlight text={r.section} query={query} /> : r.section}
                </span>
                {!r.nameHit && !r.sectHit && (
                  <span className="text-xs text-wasteland-500 truncate flex-1">
                    <Highlight text={(r.prop.description ?? "").slice(0, 90)} query={query} />
                  </span>
                )}
              </button>
            ))}
          </div>
        </div>
      ))}
    </div>
  );
}

// ───────────────────────────────────────────────────────────────────────
//  My changes
// ───────────────────────────────────────────────────────────────────────

function MyChangesList(props: {
  mode: Mode;
  file: string;
  sections: Array<{ name: string; description: string; properties: IniProperty[] }>;
  isChanged: (s: string, p: IniProperty) => boolean;
  effEntry: (s: string, k: string) => IniEffectiveEntry | undefined;
  disabled: boolean;
  hasBaseline: boolean;
  executeWrite: (changes: IniChangeItem[], onDone?: (w: string | null) => void) => void;
  applyPending: boolean;
  applyError: string | null;
}) {
  const rows: Array<{ section: string; prop: IniProperty }> = [];
  for (const s of props.sections) {
    for (const p of s.properties) {
      if (props.isChanged(s.name, p)) rows.push({ section: s.name, prop: p });
    }
  }
  if (rows.length === 0) {
    return (
      <p className="text-sm text-wasteland-400 p-4">
        No {props.mode === "override" ? "overrides" : "deviations from the reference"} in this file.
      </p>
    );
  }
  return (
    <div>
      {rows.map(({ section, prop }) => (
        <KeyRow
          key={`${section}/${prop.name}`}
          file={props.file}
          section={section}
          prop={prop}
          entry={props.effEntry(section, prop.name)}
          changed
          showSection
          mode={props.mode}
          disabled={props.disabled}
          hasBaseline={props.hasBaseline}
          executeWrite={props.executeWrite}
          applyPending={props.applyPending}
        />
      ))}
    </div>
  );
}

// ───────────────────────────────────────────────────────────────────────
//  One dense row
// ───────────────────────────────────────────────────────────────────────

function KeyRow({
  file,
  section,
  prop,
  entry,
  changed,
  mode,
  disabled,
  hasBaseline,
  executeWrite,
  applyPending,
  showSection = false,
}: {
  file: string;
  section: string;
  prop: IniProperty;
  entry: IniEffectiveEntry | undefined;
  changed: boolean;
  mode: Mode;
  disabled: boolean;
  hasBaseline: boolean;
  executeWrite: (changes: IniChangeItem[], onDone?: (w: string | null) => void) => void;
  applyPending: boolean;
  showSection?: boolean;
}) {
  const [expanded, setExpanded] = useState(false);
  const [draft, setDraft] = useState<string | null>(null);
  const [warning, setWarning] = useState<string | null>(null);
  const [flash, setFlash] = useState(false);
  const [confirmRisk, setConfirmRisk] = useState(false);

  const committed = entry?.value ?? prop.default ?? "";
  const value = draft ?? committed;
  const dirty = draft != null && draft !== committed;
  const risky = isSavegameRisk(section, prop);
  const editLocked = disabled || (risky && !expanded);

  const dotColor = !changed
    ? "bg-wasteland-700"
    : entry?.source === "override"
      ? "bg-rust-400"
      : "bg-amber-400";

  const commit = () => {
    if (!dirty) return;
    const fire = () =>
      executeWrite(
        [{ ini_file: file, section, key: prop.name, value: draft as string }],
        (w) => {
          setWarning(w);
          setDraft(null);
          setFlash(true);
          setTimeout(() => setFlash(false), 1500);
        },
      );
    if (risky) {
      setConfirmRisk(true);
      return;
    }
    fire();
  };

  const fireRisky = () => {
    setConfirmRisk(false);
    executeWrite(
      [{ ini_file: file, section, key: prop.name, value: draft as string }],
      (w) => {
        setWarning(w);
        setDraft(null);
        setFlash(true);
        setTimeout(() => setFlash(false), 1500);
      },
    );
  };

  const revert = () => {
    if (mode === "override") {
      executeWrite([{ ini_file: file, section, key: prop.name, delete: true }], () => {
        setDraft(null);
        setFlash(true);
        setTimeout(() => setFlash(false), 1500);
      });
    } else if (entry?.stock_value != null) {
      executeWrite(
        [{ ini_file: file, section, key: prop.name, value: entry.stock_value }],
        () => {
          setDraft(null);
          setFlash(true);
          setTimeout(() => setFlash(false), 1500);
        },
      );
    }
  };

  return (
    <div
      className={
        "border-b border-wasteland-800 transition-colors " +
        (flash ? "bg-emerald-900/30 " : "") +
        (expanded ? "bg-wasteland-900/60" : "hover:bg-wasteland-900/40")
      }
    >
      <div className="flex items-center gap-3 px-2 py-1">
        <span className={`w-2 h-2 rounded-full shrink-0 ${dotColor}`} title={entry?.source ?? ""} />
        <button
          className="flex-1 min-w-0 text-left font-mono text-sm text-wasteland-100 truncate"
          onClick={() => setExpanded((v) => !v)}
          title={prop.name}
        >
          {showSection && <span className="text-wasteland-500 text-xs mr-2">{section} ·</span>}
          {prop.name}
          {risky && <span className="ml-2 text-amber-400" title="Savegame-risk key">⚠</span>}
        </button>
        <ValueEditor
          prop={prop}
          value={value}
          disabled={editLocked || applyPending}
          locked={risky && !expanded}
          onChange={setDraft}
          onCommit={commit}
          onEscape={() => setDraft(null)}
        />
        {dirty && !editLocked && (
          <button className="btn-primary text-xs px-2 py-0.5" onClick={commit} disabled={applyPending}>
            Apply
          </button>
        )}
        <button
          className="text-wasteland-500 hover:text-wasteland-200 text-sm px-1"
          onClick={() => setExpanded((v) => !v)}
          aria-expanded={expanded}
        >
          {expanded ? "▾" : "▸"}
        </button>
      </div>

      {warning && (
        <div className="px-7 pb-1 text-xs text-amber-300">{warning}</div>
      )}

      {expanded && (
        <div className="px-7 pb-3 text-xs space-y-1.5">
          {prop.description && (
            <p className="text-wasteland-300 whitespace-pre-line max-w-3xl">{prop.description}</p>
          )}
          <div className="flex gap-4 flex-wrap text-wasteland-400">
            {prop.default != null && <span>shipped default: <code>{prop.default}</code></span>}
            {prop.engine?.default != null && (
              <span>engine default: <code>{prop.engine.default}</code></span>
            )}
            {prop.min != null && <span>min {prop.min}</span>}
            {prop.max != null && <span>max {prop.max}</span>}
            <span className="text-wasteland-500">
              {prop.confidence === "engine"
                ? `range from engine loader${prop.engine?.loader ? ` (${prop.engine.loader})` : ""}`
                : prop.confidence === "official"
                  ? "metadata from the official 1.13 schema"
                  : prop.confidence === "curated"
                    ? "hand-verified metadata"
                    : "range inferred from INI comments (advisory)"}
            </span>
          </div>
          <div className="flex gap-4 flex-wrap text-wasteland-400 items-center">
            <span>
              current source: <code>{entry?.source ?? "—"}</code>
              {entry?.override_active && " (override active)"}
            </span>
            {entry?.stock_value != null && (
              <span>reference value: <code>{entry.stock_value}</code></span>
            )}
            {risky && (
              <span className="text-amber-300">Can invalidate existing saved games</span>
            )}
            {changed && mode === "override" && (
              <button className="btn-ghost text-xs px-2 py-0.5" onClick={revert} disabled={disabled || applyPending}>
                Remove override
              </button>
            )}
            {changed && mode === "edit_ini" && (
              <button
                className="btn-ghost text-xs px-2 py-0.5"
                onClick={revert}
                disabled={disabled || applyPending || !hasBaseline || entry?.stock_value == null}
                title={!hasBaseline ? "No reference install configured in Settings" : undefined}
              >
                Reset to reference value
              </button>
            )}
          </div>
        </div>
      )}

      <ConfirmModal
        open={confirmRisk}
        title={`Change ${prop.name}?`}
        destructive
        body={
          <div className="space-y-2 text-sm">
            <p>
              Changing <code className="font-mono">{prop.name}</code> can make existing saved
              games unloadable.
            </p>
            <p>
              New value: <code className="font-mono text-amber-300">{draft}</code>
            </p>
          </div>
        }
        confirmLabel="Apply"
        onConfirm={fireRisky}
        onCancel={() => setConfirmRisk(false)}
      />
    </div>
  );
}

function ValueEditor({
  prop,
  value,
  disabled,
  locked,
  onChange,
  onCommit,
  onEscape,
}: {
  prop: IniProperty;
  value: string;
  disabled: boolean;
  locked: boolean;
  onChange: (v: string) => void;
  onCommit: () => void;
  onEscape: () => void;
}) {
  const dt = (prop.datatype || "").toLowerCase();
  const keys = (e: React.KeyboardEvent) => {
    if (e.key === "Enter") onCommit();
    if (e.key === "Escape") onEscape();
  };

  if (locked) {
    return (
      <span className="text-xs text-wasteland-500 italic w-48 text-right" title="Expand the row to edit this savegame-risk key">
        expand to edit
      </span>
    );
  }
  if (dt === "boolean") {
    const isTrue = value.toUpperCase() === "TRUE" || value === "1";
    return (
      <label className="flex items-center gap-1.5 w-48 justify-end cursor-pointer">
        <input
          type="checkbox"
          checked={isTrue}
          disabled={disabled}
          onChange={(e) => onChange(e.target.checked ? "TRUE" : "FALSE")}
        />
        <span className="text-xs font-mono text-wasteland-300 w-12">{isTrue ? "TRUE" : "FALSE"}</span>
      </label>
    );
  }
  if (dt === "list" && prop.list_values.length > 0) {
    return (
      <select
        className="input w-48 py-0.5 text-sm"
        value={value}
        disabled={disabled}
        onChange={(e) => onChange(e.target.value)}
      >
        {prop.list_values.map((v) => (
          <option key={v} value={v}>
            {v}
          </option>
        ))}
      </select>
    );
  }
  if (dt === "numeric") {
    return (
      <input
        type="number"
        className="input w-48 py-0.5 text-sm text-right font-mono"
        value={value}
        min={prop.min ?? undefined}
        max={prop.max ?? undefined}
        step={prop.interval ?? "any"}
        disabled={disabled}
        onChange={(e) => onChange(e.target.value)}
        onKeyDown={keys}
        onBlur={onCommit}
      />
    );
  }
  return (
    <input
      type="text"
      className="input w-48 py-0.5 text-sm font-mono"
      value={value}
      disabled={disabled}
      onChange={(e) => onChange(e.target.value)}
      onKeyDown={keys}
      onBlur={onCommit}
    />
  );
}

// ───────────────────────────────────────────────────────────────────────
//  Save-as-preset dialog (Override mode only — see button title)
// ───────────────────────────────────────────────────────────────────────

function SavePresetDialog({
  overrides,
  onClose,
  onSaved,
}: {
  overrides: Array<{ ini_file: string; section: string; key: string; value: string }>;
  onClose: () => void;
  onSaved: () => void;
}) {
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [checked, setChecked] = useState<Set<number>>(
    () => new Set(overrides.map((_, i) => i)),
  );
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const toggle = (i: number) =>
    setChecked((s) => {
      const n = new Set(s);
      if (n.has(i)) n.delete(i);
      else n.add(i);
      return n;
    });

  const save = async () => {
    setBusy(true);
    setError(null);
    try {
      await createIniPreset({
        name: name.trim(),
        description: description.trim(),
        changes: overrides
          .filter((_, i) => checked.has(i))
          .map((o) => ({
            ini_file: o.ini_file, section: o.section, key: o.key, value: o.value,
          })),
      });
      onSaved();
    } catch (e) {
      setError(formatApiError(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm"
      onClick={onClose}
    >
      <div
        className="w-[36rem] max-w-[92vw] max-h-[85vh] overflow-y-auto rounded-lg border border-wasteland-700 bg-wasteland-900 p-4 shadow-2xl"
        onClick={(e) => e.stopPropagation()}
        role="dialog"
        aria-modal="true"
      >
        <h3 className="text-base font-semibold mb-3">Save overrides as preset</h3>
        <div className="space-y-2 mb-3">
          <input
            className="input w-full"
            placeholder="Preset name"
            value={name}
            onChange={(e) => setName(e.target.value)}
            autoFocus
          />
          <input
            className="input w-full"
            placeholder="Description (optional)"
            value={description}
            onChange={(e) => setDescription(e.target.value)}
          />
        </div>
        <p className="text-xs text-wasteland-500 mb-1">
          {checked.size} of {overrides.length} overrides included. Saved to{" "}
          <code className="font-mono">MercForgePresets.json</code> in the install.
        </p>
        <div className="max-h-60 overflow-y-auto border border-wasteland-800 rounded mb-3">
          {overrides.map((o, i) => (
            <label
              key={`${o.ini_file}/${o.section}/${o.key}`}
              className="flex items-center gap-2 px-2 py-1 text-xs border-b border-wasteland-800 last:border-0 cursor-pointer hover:bg-wasteland-800"
            >
              <input type="checkbox" checked={checked.has(i)} onChange={() => toggle(i)} />
              <span className="text-wasteland-500">{o.ini_file}</span>
              <span className="font-mono text-wasteland-200 flex-1 truncate">
                {o.section} · {o.key}
              </span>
              <span className="font-mono text-wasteland-300">{o.value}</span>
            </label>
          ))}
        </div>
        {error && <div className="text-xs text-red-300 mb-2">{error}</div>}
        <div className="flex justify-end gap-2">
          <button className="btn-ghost text-sm" onClick={onClose} disabled={busy}>
            Cancel
          </button>
          <button
            className="btn-primary text-sm"
            disabled={busy || !name.trim() || checked.size === 0}
            onClick={() => void save()}
          >
            {busy ? "Saving…" : "Save preset"}
          </button>
        </div>
      </div>
    </div>
  );
}
