/**
 * MapForge pre-flight validation panel (A4).
 *
 * A floating, on-demand report of crash traps + playability + tileset
 * JSD frame-match issues for the open sector — the cheap feedback loop
 * that catches "will this crash / is it playable?" BEFORE the expensive
 * in-game launch.
 *
 * Self-contained: given the open sector's paths it fetches its own
 * report (preferring the live session's uncommitted state when a
 * session is open, so you can validate edits before saving). The parent
 * only owns open/closed state.
 */
import { useCallback, useEffect, useState } from "react";
import { FloatingPanel } from "../components/FloatingPanel";
import {
  validateSector,
  validateSession,
  type ValidationReport,
  type ValidationSeverity,
} from "../lib/mapforge";

interface Props {
  datPath: string;
  xmlPath: string;
  tileset: number;
  /** Prefer the session's uncommitted state when a session is open. */
  sessionId: string | null;
  onClose: () => void;
}

const SEV_TINT: Record<ValidationSeverity, { bg: string; fg: string; ring: string; icon: string }> = {
  error: { bg: "bg-red-950",   fg: "text-red-200",   ring: "ring-red-700",   icon: "⛔" },
  warn:  { bg: "bg-amber-950", fg: "text-amber-200", ring: "ring-amber-700", icon: "⚠" },
  info:  { bg: "bg-gray-900",  fg: "text-gray-300",  ring: "ring-gray-700",  icon: "ℹ" },
};

const SEV_ORDER: ValidationSeverity[] = ["error", "warn", "info"];

function fmtTiles(tiles: number[], total: number | null, cols: number): string {
  if (!tiles.length) return "";
  const shown = tiles
    .slice(0, 8)
    .map((g) => `(${g % cols},${Math.floor(g / cols)})`)
    .join(" ");
  const n = total ?? tiles.length;
  const more = n > Math.min(tiles.length, 8) ? ` …+${n - Math.min(tiles.length, 8)} more` : "";
  return `${shown}${more}`;
}

export function MapForgeValidatePanel({
  datPath, xmlPath, tileset, sessionId, onClose,
}: Props) {
  const [report, setReport] = useState<ValidationReport | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [checkJsd, setCheckJsd] = useState(true);

  const run = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const r = sessionId
        ? await validateSession(sessionId, { checkJsd })
        : await validateSector(datPath, { xmlPath, tileset, checkJsd });
      setReport(r);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }, [sessionId, datPath, xmlPath, tileset, checkJsd]);

  // Auto-run on open and whenever the JSD toggle changes.
  useEffect(() => {
    void run();
  }, [run]);

  const cols = report?.cols ?? 160;
  const clean =
    report != null && report.errors === 0 && report.warnings === 0;

  return (
    <FloatingPanel
      id="validate"
      title="Validation"
      defaultRect={{ x: Math.max(8, window.innerWidth - 430), y: 90, w: 410, h: 470 }}
      minW={300}
      minH={220}
      onClose={onClose}
      headerRight={
        <button
          type="button"
          onClick={() => void run()}
          disabled={loading}
          title="Re-run validation"
          className="rounded border border-gray-700 bg-gray-900 px-1.5 py-0.5 text-[10px] text-gray-300 hover:border-gray-500 hover:text-gray-100 disabled:opacity-50"
        >
          {loading ? "Checking…" : "↻ Re-run"}
        </button>
      }
    >
      {/* Summary + options */}
      <div className="mb-2 flex items-center justify-between gap-2">
        <div className="flex items-center gap-1.5 text-[11px]">
          <span className="rounded bg-red-950 px-1.5 py-0.5 text-red-200 ring-1 ring-inset ring-red-800">
            {report?.errors ?? 0} err
          </span>
          <span className="rounded bg-amber-950 px-1.5 py-0.5 text-amber-200 ring-1 ring-inset ring-amber-800">
            {report?.warnings ?? 0} warn
          </span>
          <span className="rounded bg-gray-900 px-1.5 py-0.5 text-gray-300 ring-1 ring-inset ring-gray-700">
            {report?.infos ?? 0} info
          </span>
        </div>
        <label className="flex items-center gap-1 text-[10px] text-gray-400" title="Also check tileset JSD frame counts (slower)">
          <input
            type="checkbox"
            checked={checkJsd}
            onChange={(e) => setCheckJsd(e.target.checked)}
            className="h-3 w-3"
          />
          JSD check
        </label>
      </div>

      {sessionId == null && (
        <p className="mb-2 text-[10px] italic text-gray-500">
          Validating the on-disk file (no live edit session).
        </p>
      )}

      {error && (
        <div className="mb-2 rounded border border-red-800 bg-red-950 px-2 py-1 text-[11px] text-red-200">
          {error}
        </div>
      )}

      {loading && !report && (
        <p className="text-[11px] italic text-gray-500">Running checks…</p>
      )}

      {clean && (
        <div className="rounded border border-emerald-800 bg-emerald-950 px-2 py-1.5 text-[11px] text-emerald-200">
          ✓ No errors or warnings.{" "}
          {report && report.infos > 0 && `${report.infos} advisory note(s) below.`}
        </div>
      )}

      {report && report.findings.length > 0 && (
        <ul className="space-y-1">
          {SEV_ORDER.flatMap((sev) =>
            report.findings
              .filter((f) => f.severity === sev)
              .map((f, i) => {
                const tint = SEV_TINT[f.severity];
                const tiles = fmtTiles(f.tiles, f.count, cols);
                return (
                  <li
                    key={`${f.code}-${i}`}
                    className={`rounded px-2 py-1 text-[11px] ${tint.bg} ${tint.fg} ring-1 ring-inset ${tint.ring}`}
                  >
                    <div className="flex items-baseline gap-1.5">
                      <span>{tint.icon}</span>
                      <span className="font-mono text-[9px] opacity-60">{f.code}</span>
                      {f.slot != null && (
                        <span className="font-mono text-[9px] opacity-60">slot {f.slot}</span>
                      )}
                    </div>
                    <div className="mt-0.5">{f.message}</div>
                    {tiles && (
                      <div className="mt-0.5 font-mono text-[9px] opacity-60">
                        tiles: {tiles}
                      </div>
                    )}
                  </li>
                );
              }),
          )}
        </ul>
      )}

      {report && !report.jsd_checked && checkJsd && (
        <p className="mt-2 text-[9px] italic text-gray-600">
          JSD check skipped (needs the tileset XML + index).
        </p>
      )}
    </FloatingPanel>
  );
}
