import { useMemo, useState } from "react";
import { useQuery, useMutation } from "@tanstack/react-query";

import {
  listBackgroundLibrary,
  getBackgroundLibraryEntry,
  assignBackgroundFromLibrary,
  formatApiError,
  type LibraryBackground,
} from "../lib/api";

// Cap rendered rows so the union (~2000) never paints 2000 DOM nodes at once.
const RENDER_CAP = 400;

interface Props {
  /** In-wizard modal: a successful import calls this with the assigned <500 id
   *  so the caller sets the merc's usBackground. Standalone: omitted. */
  onAssigned?: (uiIndex: number, name: string) => void;
}

// ── Power word (neutral label, from the net BP band) ─────────────────────────
// Bands anchored on the corpus player baseline (p25≈12, p50≈19, p90≈33).
function powerWord(net: number): string {
  if (net < 12) return "Weak";
  if (net < 26) return "Balanced";
  if (net < 42) return "Strong";
  return "Very strong";
}
const POWER_ORDER = ["Weak", "Balanced", "Strong", "Very strong"];

// ── Modifier → {group, label, good} classification ───────────────────────────
// "good" drives green/red: for most fields + is good; for "lower is better"
// fields (AP cost, food/water/sleep need, vices) a NEGATIVE value is the benefit
// (invert). Keeps the coloring honest instead of coloring by raw sign.
type Grp = "Attributes" | "Combat" | "Resistances" | "Movement & AP" | "Survival" | "Skills" | "Other";
const GROUP_ORDER: Grp[] = ["Attributes", "Combat", "Resistances", "Movement & AP", "Survival", "Skills", "Other"];

const META: Record<string, { group: Grp; label: string; invert?: boolean }> = {
  strength: { group: "Attributes", label: "strength" },
  agility: { group: "Attributes", label: "agility" },
  dexterity: { group: "Attributes", label: "dexterity" },
  wisdom: { group: "Attributes", label: "wisdom" },
  marksmanship: { group: "Attributes", label: "marksmanship" },
  leadership: { group: "Attributes", label: "leadership" },
  mechanical: { group: "Attributes", label: "mechanical" },
  explosives: { group: "Attributes", label: "explosives" },
  medical: { group: "Attributes", label: "medical" },
  meleedamage: { group: "Combat", label: "melee damage" },
  croucheddefense: { group: "Combat", label: "crouched defense" },
  speed_run: { group: "Movement & AP", label: "run speed" },
  travel_foot: { group: "Movement & AP", label: "travel on foot" },
  travel_boat: { group: "Movement & AP", label: "travel by boat" },
  ap_inventory: { group: "Movement & AP", label: "inventory AP", invert: true },
  ap_swimming: { group: "Movement & AP", label: "swimming AP", invert: true },
  ap_fortify: { group: "Movement & AP", label: "fortify AP", invert: true },
  ap_artillery: { group: "Movement & AP", label: "artillery AP", invert: true },
  ap_airdrop: { group: "Movement & AP", label: "airdrop AP" },
  ap_assault: { group: "Movement & AP", label: "assault AP" },
  stealth: { group: "Survival", label: "stealth" },
  carrystrength: { group: "Survival", label: "carry strength" },
  food: { group: "Survival", label: "food need", invert: true },
  water: { group: "Survival", label: "water need", invert: true },
  sleep: { group: "Survival", label: "sleep need", invert: true },
  smoker: { group: "Survival", label: "smoker", invert: true },
  druguse: { group: "Survival", label: "drug use", invert: true },
  speed_bandaging: { group: "Skills", label: "bandaging speed" },
  interrogation: { group: "Skills", label: "interrogation" },
  drink_energyregen: { group: "Skills", label: "energy regen" },
  capitulation: { group: "Skills", label: "capitulation" },
  betterprices: { group: "Skills", label: "better prices" },
  approach_friendly: { group: "Skills", label: "approach (friendly)" },
  approach_recruit: { group: "Skills", label: "approach (recruit)" },
};
// AP fields that are an ACTIVITY cost, not terrain movement (so the terrain
// collapse below skips them — they're classified individually via META).
const ACTIVITY_AP = new Set([
  "ap_inventory", "ap_swimming", "ap_fortify", "ap_artillery", "ap_airdrop", "ap_assault",
]);

function prettify(k: string): string {
  return k.replace(/_/g, " ");
}
function classify(k: string, value: number): { group: Grp; label: string; good: boolean } {
  const m = META[k];
  if (m) return { group: m.group, label: m.label, good: m.invert ? value < 0 : value > 0 };
  if (k.startsWith("resistance_")) return { group: "Resistances", label: prettify(k.slice(11)), good: value > 0 };
  if (k.startsWith("cth_")) return { group: "Combat", label: `to-hit (${prettify(k.slice(4))})`, good: value > 0 };
  if (k.startsWith("ap_")) return { group: "Movement & AP", label: `${prettify(k.slice(3))} AP`, good: value > 0 };
  return { group: "Other", label: prettify(k), good: value > 0 };
}
const sign = (v: number) => (v > 0 ? `+${v}` : `${v}`);
function rangeStr(vals: number[]): string {
  const lo = Math.min(...vals), hi = Math.max(...vals);
  if (lo === hi) return sign(lo);
  return lo >= 0 ? `+${lo}–${hi}` : `${sign(lo)}–${sign(hi)}`;
}

interface LedgerRow { label: string; display: string; good: boolean; mag: number; }
interface LedgerGroup { group: Grp; rows: LedgerRow[]; max: number; }

// Build the grouped ledger: collapse the terrain-AP family and day/night hearing
// into one row each, classify the rest, sort each group by magnitude.
function buildLedger(modifiers: [string, number][]): LedgerGroup[] {
  const byGroup: Record<string, LedgerRow[]> = {};
  const terrain: number[] = [];
  const hearing: number[] = [];
  for (const [key, value] of modifiers) {
    const k = key.toLowerCase();
    if (k === "no_male" || k === "no_female") continue;       // → IMP-hidden note
    if (k.startsWith("ap_") && !ACTIVITY_AP.has(k)) { terrain.push(value); continue; }
    if (k === "hearing_day" || k === "hearing_night") { hearing.push(value); continue; }
    const c = classify(k, value);
    (byGroup[c.group] ??= []).push({ label: c.label, display: sign(value), good: c.good, mag: Math.abs(value) });
  }
  if (terrain.length) {
    (byGroup["Movement & AP"] ??= []).push({
      label: "terrain move", display: rangeStr(terrain), good: true,
      mag: Math.max(...terrain.map(Math.abs)),
    });
  }
  if (hearing.length) {
    (byGroup["Survival"] ??= []).push({
      label: "hearing", display: rangeStr(hearing), good: true,
      mag: Math.max(...hearing.map(Math.abs)),
    });
  }
  const out: LedgerGroup[] = [];
  for (const g of GROUP_ORDER) {
    const rows = byGroup[g];
    if (!rows) continue;
    rows.sort((a, b) => b.mag - a.mag);
    out.push({ group: g, rows, max: Math.max(...rows.map((r) => r.mag)) });
  }
  return out;
}

export default function BackgroundLibraryBrowser({ onAssigned }: Props) {
  const lib = useQuery({
    queryKey: ["bg-library"],
    queryFn: () => listBackgroundLibrary(),
    staleTime: 10 * 60 * 1000,
  });

  const [q, setQ] = useState("");
  const [source, setSource] = useState("");
  const [power, setPower] = useState("");
  const [selectedUid, setSelectedUid] = useState<string | null>(null);

  const rows = lib.data?.backgrounds ?? [];
  const facets = lib.data?.meta?.facets;

  const filtered = useMemo(() => {
    const ql = q.trim().toLowerCase();
    return rows.filter((r) => {
      if (ql && !r.name.toLowerCase().includes(ql)
        && !r.short_name.toLowerCase().includes(ql)
        && !r.description.toLowerCase().includes(ql)) return false;
      if (source && !r.sources.includes(source)) return false;
      if (power && powerWord(r.net) !== power) return false;
      return true;
    });
  }, [rows, q, source, power]);

  const shown = filtered.slice(0, RENDER_CAP);

  if (lib.isLoading) {
    return <div className="card text-sm text-wasteland-400">Loading background library…</div>;
  }
  if (lib.isError) {
    return (
      <div className="card text-sm">
        <p className="text-rust-400">Couldn't load the background library.</p>
        <p className="text-wasteland-400 mt-1">{formatApiError(lib.error)}</p>
      </div>
    );
  }

  return (
    <div className="space-y-3">
      {/* Filters */}
      <div className="card p-3 grid grid-cols-1 md:grid-cols-12 gap-2 items-end">
        <label className="block md:col-span-6">
          <span className="text-xs font-medium text-wasteland-300">Search name / description</span>
          <input className="input mt-1" placeholder="e.g. doctor, sniper, elder…"
            value={q} onChange={(e) => setQ(e.target.value)} />
        </label>
        <label className="block md:col-span-4">
          <span className="text-xs font-medium text-wasteland-300">Source mod</span>
          <select className="input mt-1" value={source} onChange={(e) => setSource(e.target.value)}>
            <option value="">All sources</option>
            {(facets?.sources ?? []).map((s) => <option key={s} value={s}>{s}</option>)}
          </select>
        </label>
        <label className="block md:col-span-2">
          <span className="text-xs font-medium text-wasteland-300">Power</span>
          <select className="input mt-1" value={power} onChange={(e) => setPower(e.target.value)}>
            <option value="">Any</option>
            {POWER_ORDER.map((p) => <option key={p} value={p}>{p}</option>)}
          </select>
        </label>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-[24rem_1fr] gap-4">
        {/* LEFT: list — name · power · brief description */}
        <section className="card p-0 overflow-hidden self-start">
          <div className="flex items-center justify-between p-3 border-b border-wasteland-700">
            <h2 className="text-sm font-semibold">
              {filtered.length.toLocaleString()} of {rows.length.toLocaleString()} backgrounds
            </h2>
            {(q || source || power) && (
              <button className="text-xs text-wasteland-400 hover:text-rust-400"
                onClick={() => { setQ(""); setSource(""); setPower(""); }}>
                Clear
              </button>
            )}
          </div>
          <ul className="max-h-[64vh] overflow-y-auto divide-y divide-wasteland-800">
            {shown.map((r) => (
              <li key={r.uid}>
                <button onClick={() => setSelectedUid(r.uid)}
                  className={`w-full text-left px-3 py-2 hover:bg-wasteland-800/60 ${selectedUid === r.uid ? "bg-wasteland-800" : ""}`}>
                  <div className="flex items-center justify-between gap-2">
                    <span className="text-sm font-medium truncate">{r.name || "(unnamed)"}</span>
                    <span className="flex items-center gap-1 shrink-0">
                      {r.source_kinds.includes("wasteland") && (
                        <span className="text-[10px] text-rust-400" title="Already in The Wasteland">yours</span>
                      )}
                      <span className="badge bg-wasteland-700 text-wasteland-200 text-[10px]">{powerWord(r.net)}</span>
                    </span>
                  </div>
                  <div className="text-xs text-wasteland-400 truncate mt-0.5">
                    {r.description || (r.short_name && r.short_name !== r.name ? r.short_name : "—")}
                  </div>
                </button>
              </li>
            ))}
            {filtered.length > RENDER_CAP && (
              <li className="px-3 py-2 text-xs text-wasteland-500">
                Showing first {RENDER_CAP} of {filtered.length.toLocaleString()} — refine the search.
              </li>
            )}
            {filtered.length === 0 && (
              <li className="px-3 py-6 text-center text-xs text-wasteland-500">No backgrounds match these filters.</li>
            )}
          </ul>
        </section>

        {/* RIGHT: detail */}
        <section className="min-w-0">
          {selectedUid
            ? <Detail uid={selectedUid} onAssigned={onAssigned} />
            : <div className="card text-sm text-wasteland-400">Pick a background to see its full effects and provenance.</div>}
        </section>
      </div>
    </div>
  );
}

function Detail({ uid, onAssigned }: { uid: string; onAssigned?: (uiIndex: number, name: string) => void }) {
  const detail = useQuery({
    queryKey: ["bg-library", uid],
    queryFn: () => getBackgroundLibraryEntry(uid),
    staleTime: 10 * 60 * 1000,
  });
  const [copied, setCopied] = useState(false);
  const [showBalance, setShowBalance] = useState(false);

  const assign = useMutation({
    mutationFn: () => assignBackgroundFromLibrary({ uid }),
    onSuccess: (res) => { if (onAssigned) onAssigned(res.ui_index, res.name); },
  });

  const ledger = useMemo(
    () => (detail.data ? buildLedger(detail.data.modifiers) : []),
    [detail.data],
  );

  if (detail.isLoading) return <div className="card text-sm text-wasteland-400">Loading…</div>;
  if (detail.isError || !detail.data) {
    return <div className="card text-sm text-rust-400">{formatApiError(detail.error)}</div>;
  }
  const e: LibraryBackground = detail.data;

  const copyXml = async () => {
    try {
      await navigator.clipboard.writeText(e.block_xml ?? "");
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    } catch { /* clipboard blocked — Copy XML still available via balance details / download */ }
  };
  const downloadXml = () => {
    const safe = (e.name || "background").replace(/[^a-z0-9]+/gi, "_").slice(0, 40);
    const blob = new Blob([e.block_xml ?? ""], { type: "application/xml" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url; a.download = `${safe}.xml`;
    document.body.appendChild(a); a.click(); a.remove();
    URL.revokeObjectURL(url);
  };

  const provenance = e.sources.length === 1
    ? `from ${e.sources[0]}`
    : `from ${e.sources[0]} +${e.sources.length - 1} more`;

  return (
    <div className="space-y-3">
      {/* Header */}
      <div className="card">
        <div className="flex items-start justify-between gap-3">
          <h2 className="text-lg font-bold truncate">{e.name || "(unnamed)"}</h2>
          <span className="badge bg-wasteland-700 text-wasteland-200 shrink-0">{powerWord(e.net)}</span>
        </div>
        <div className="text-xs text-wasteland-400 mt-1">
          {!e.imp_pickable && <span className="text-amber-300">hidden from IMP creation · </span>}
          {provenance}
          {e.source_kinds.includes("wasteland") && <span className="text-rust-400"> · already yours</span>}
        </div>
        {e.description && (
          <p className="text-sm text-wasteland-300 mt-2 whitespace-pre-wrap leading-relaxed">{e.description}</p>
        )}
      </div>

      {/* Effects ledger */}
      <div className="card">
        {ledger.length === 0 ? (
          <p className="text-sm text-wasteland-500">No stat effects (flavor-only background).</p>
        ) : (
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-x-6 gap-y-4">
            {ledger.map((g) => (
              <div key={g.group} className={g.group === "Skills" ? "sm:col-span-2" : ""}>
                <div className="text-[11px] font-medium uppercase tracking-wide text-wasteland-500 border-b border-wasteland-800 pb-1 mb-1.5">
                  {g.group}
                </div>
                <div className={`text-[13px] ${g.group === "Skills" ? "grid grid-cols-1 sm:grid-cols-2 gap-x-6" : ""}`}>
                  {g.rows.map((r, i) => (
                    <div key={i} className="flex items-baseline justify-between gap-3 py-[3px]">
                      <span className="text-wasteland-300 truncate">{r.label}</span>
                      <span className={`font-mono shrink-0 ${r.good ? "text-emerald-400" : "text-red-400"} ${r.mag === g.max ? "font-semibold" : ""}`}>
                        {r.display}
                      </span>
                    </div>
                  ))}
                </div>
              </div>
            ))}
          </div>
        )}
      </div>

      {/* Actions */}
      <div className="card">
        <div className="flex flex-wrap items-center gap-2">
          <button className="btn-primary" disabled={assign.isPending} onClick={() => assign.mutate()}>
            {assign.isPending ? "Importing…" : onAssigned ? "Use this background" : "Import into this install"}
          </button>
          <button className="btn-secondary" onClick={copyXml}>{copied ? "Copied ✓" : "Copy XML"}</button>
          <button className="btn-ghost" onClick={downloadXml}>Download XML</button>
        </div>
        {assign.isError && (
          <div className="mt-2 rounded bg-red-950/60 px-2 py-1 text-xs text-red-200">{formatApiError(assign.error)}</div>
        )}
        {assign.data && (
          <div className="mt-2 rounded bg-emerald-950/60 px-2 py-1 text-xs text-emerald-200">
            ✓ Imported as <span className="font-mono">{assign.data.name}</span> at free index{" "}
            <span className="font-mono">{assign.data.ui_index}</span> (&lt;500).{" "}
            {onAssigned ? "Assigned to this merc." : <>Backup <span className="font-mono">{assign.data.backup_id}</span>.</>}
          </div>
        )}
        <p className="text-[11px] text-wasteland-500 mt-2">
          Import renumbers this background to the lowest free slot in this install's Backgrounds.xml,
          splices the full block verbatim, and snapshots the file first (restore via Backups).
        </p>
      </div>

      {/* Balance details (collapsed corpus jargon) */}
      <div className="card">
        <button type="button" onClick={() => setShowBalance((v) => !v)}
          className="flex items-center gap-1.5 text-xs text-wasteland-400 hover:text-wasteland-200">
          <span className="font-mono">{showBalance ? "⌄" : "›"}</span> balance details
        </button>
        {showBalance && (
          <div className="mt-2 space-y-2 text-[11px] text-wasteland-400 font-mono">
            <div>net {e.net} BP · core {e.core_net} · +{e.gross_pos} / −{e.gross_neg} · {e.n_fields} fields · cluster {e.cluster}</div>
            <div>sources: {e.sources.join(", ")} · orig idx {e.indices_seen.join(", ")}</div>
            <details>
              <summary className="cursor-pointer hover:text-wasteland-200">full &lt;BACKGROUND&gt; XML (src idx {e.raw_index})</summary>
              <pre className="mt-1 max-h-[30vh] overflow-auto p-2 bg-wasteland-950/60 rounded text-wasteland-300 whitespace-pre">{e.block_xml}</pre>
            </details>
          </div>
        )}
      </div>
    </div>
  );
}
