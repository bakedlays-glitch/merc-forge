/**
 * MapForge pre-flight validation panel (A4).
 *
 * An on-demand report of crash traps + playability + tileset JSD
 * frame-match issues for the open sector — the cheap feedback loop
 * that catches "will this crash / is it playable?" BEFORE the expensive
 * in-game launch.
 *
 * Rendered as `MapForgeValidateBody` inside the dock's "Validation"
 * panel. (A legacy FloatingPanel wrapper existed for the old non-dock
 * layout; it was removed with that layout.)
 *
 * Self-contained: given the open sector's paths it fetches its own
 * report (preferring the live session's uncommitted state when a
 * session is open, so you can validate edits before saving).
 *
 * Findings the as-opened file already carried are tagged `preexisting`
 * by the backend (session baseline) and rendered dimmer with a
 * "pre-existing" badge — so a paste isn't blamed for e.g. C6.DAT's 40
 * native room-ID gaps.
 */
import { useCallback, useEffect, useState } from "react";
import {
  validateSector,
  validateSession,
  type ValidationReport,
  type ValidationSeverity,
} from "../lib/mapforge";

interface BodyProps {
  datPath: string;
  xmlPath: string;
  tileset: number;
  /** Prefer the session's uncommitted state when a session is open. */
  sessionId: string | null;
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

export function MapForgeValidateBody({
  datPath, xmlPath, tileset, sessionId,
}: BodyProps) {
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
  // "New" = introduced by this session's edits (not in the as-opened
  // file). Without a session nothing is tagged, so everything counts.
  const fresh = report?.findings.filter((f) => !f.preexisting) ?? [];
  const freshErrors = fresh.filter((f) => f.severity === "error").length;
  const freshWarnings = fresh.filter((f) => f.severity === "warn").length;
  const preexistingCount =
    (report?.findings.length ?? 0) - fresh.length;
  const clean =
    report != null && report.errors === 0 && report.warnings === 0;
  const cleanOfNew =
    report != null && !clean && freshErrors === 0 && freshWarnings === 0;

  return (
    <div className="p-2 text-xs">
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
        <div className="flex items-center gap-2">
          <label className="flex items-center gap-1 text-[10px] text-gray-400" title="Also check tileset JSD frame counts (slower)">
            <input
              type="checkbox"
              checked={checkJsd}
              onChange={(e) => setCheckJsd(e.target.checked)}
              className="h-3 w-3"
            />
            JSD check
          </label>
          <button
            type="button"
            onClick={() => void run()}
            disabled={loading}
            title="Re-run validation"
            className="rounded border border-gray-700 bg-gray-900 px-1.5 py-0.5 text-[10px] text-gray-300 hover:border-gray-500 hover:text-gray-100 disabled:opacity-50"
          >
            {loading ? "Checking…" : "↻ Re-run"}
          </button>
        </div>
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

      {cleanOfNew && (
        <div className="mb-1 rounded border border-emerald-800 bg-emerald-950 px-2 py-1.5 text-[11px] text-emerald-200">
          ✓ Your edits introduced no new problems. The findings below were
          already in the file when it was opened.
        </div>
      )}

      {report && report.findings.length > 0 && (
        <ul className="space-y-1">
          {SEV_ORDER.flatMap((sev) =>
            report.findings
              .filter((f) => f.severity === sev)
              // New findings sort above pre-existing ones within a severity.
              .sort((a, b) => Number(a.preexisting ?? false) - Number(b.preexisting ?? false))
              .map((f, i) => {
                const tint = SEV_TINT[f.severity];
                const tiles = fmtTiles(f.tiles, f.count, cols);
                return (
                  <li
                    key={`${f.code}-${i}`}
                    className={`rounded px-2 py-1 text-[11px] ${tint.bg} ${tint.fg} ring-1 ring-inset ${tint.ring} ${f.preexisting ? "opacity-60" : ""}`}
                  >
                    <div className="flex items-baseline gap-1.5">
                      <span>{tint.icon}</span>
                      <span className="font-mono text-[9px] opacity-60">{f.code}</span>
                      {f.slot != null && (
                        <span className="font-mono text-[9px] opacity-60">slot {f.slot}</span>
                      )}
                      {f.preexisting && (
                        <span
                          className="ml-auto rounded bg-gray-800 px-1 py-px text-[8px] uppercase tracking-wide text-gray-400 ring-1 ring-inset ring-gray-600"
                          title="This finding was already present when the file was opened — your edits did not introduce it."
                        >
                          pre-existing
                        </span>
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

      {preexistingCount > 0 && !cleanOfNew && !clean && (
        <p className="mt-2 text-[9px] italic text-gray-600">
          {preexistingCount} finding(s) marked pre-existing were already in
          the file when it was opened.
        </p>
      )}

      {report && !report.jsd_checked && checkJsd && (
        <p className="mt-2 text-[9px] italic text-gray-600">
          JSD check skipped (needs the tileset XML + index).
        </p>
      )}
    </div>
  );
}
