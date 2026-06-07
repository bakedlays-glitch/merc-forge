/**
 * MapForge status log — bottom-of-viewport collapsible panel that
 * surfaces ephemeral events (saves, atlas reloads, STI imports,
 * errors) instead of scattering them as floating toasts in random
 * corners of the canvas.
 *
 * Design choices:
 * - Exposes a `useMapForgeLog()` hook + a context provider. Any
 *   component can call `log.append({...})` without lifting state.
 * - Renders as a fixed-height bar at the bottom of the editor.
 *   Click the latest entry to expand the full history (last 50).
 * - Auto-collapses 4 s after the last event so the user can keep
 *   working without the panel chewing screen real estate.
 * - Severity tints the entry: info (gray), success (emerald), warn
 *   (amber), error (red). Click-to-dismiss on individual entries.
 */
import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from "react";

export type LogSeverity = "info" | "success" | "warn" | "error";

export interface LogEntry {
  id: number;
  ts: number;             // Date.now()
  severity: LogSeverity;
  message: string;
  /** Optional secondary text shown when the entry is expanded.
   * Useful for things like backup paths, error stack traces. */
  detail?: string;
}

interface LogApi {
  entries: LogEntry[];
  append: (e: Omit<LogEntry, "id" | "ts">) => void;
  clear: () => void;
}

const LogContext = createContext<LogApi | null>(null);

const MAX_ENTRIES = 50;

export function MapForgeLogProvider({ children }: { children: ReactNode }) {
  const [entries, setEntries] = useState<LogEntry[]>([]);
  // Stable id counter survives re-renders.
  const nextId = useRef(1);
  const append = useCallback((e: Omit<LogEntry, "id" | "ts">) => {
    setEntries((prev) => {
      const entry: LogEntry = {
        id: nextId.current++,
        ts: Date.now(),
        ...e,
      };
      const next = [...prev, entry];
      // Cap history. Older entries fall off the end.
      while (next.length > MAX_ENTRIES) next.shift();
      return next;
    });
  }, []);
  const clear = useCallback(() => setEntries([]), []);
  const api = useMemo(
    () => ({ entries, append, clear }),
    [entries, append, clear],
  );
  return <LogContext.Provider value={api}>{children}</LogContext.Provider>;
}

/** Hook for any component below the provider. Returns null when
 * called outside the provider (so feature work doesn't crash if the
 * provider isn't mounted yet). */
export function useMapForgeLog(): LogApi | null {
  return useContext(LogContext);
}

const SEV_TINT: Record<LogSeverity, { bg: string; fg: string; ring: string }> = {
  info:    { bg: "bg-gray-900",    fg: "text-gray-300",    ring: "ring-gray-700" },
  success: { bg: "bg-emerald-950", fg: "text-emerald-200", ring: "ring-emerald-700" },
  warn:    { bg: "bg-amber-950",   fg: "text-amber-200",   ring: "ring-amber-700" },
  error:   { bg: "bg-red-950",     fg: "text-red-200",     ring: "ring-red-700" },
};

function fmtTime(ts: number): string {
  const d = new Date(ts);
  return `${d.getHours().toString().padStart(2, "0")}:` +
         `${d.getMinutes().toString().padStart(2, "0")}:` +
         `${d.getSeconds().toString().padStart(2, "0")}`;
}

/** Bottom status-bar component. Mount once inside the editor layout.
 * Self-collapses 4 s after the last entry; click to re-expand. */
export function MapForgeLogPanel() {
  const log = useMapForgeLog();
  const [expanded, setExpanded] = useState(false);
  const collapseTimer = useRef<number | null>(null);

  // Auto-collapse 4 s after a new entry arrives — but only if the
  // panel was opened by the entry itself, not by a manual click.
  // We track lastEntryId so we don't keep resetting the timer.
  const lastEntryId = useRef<number | null>(null);
  useEffect(() => {
    if (!log) return;
    const newest = log.entries[log.entries.length - 1];
    if (!newest) return;
    if (lastEntryId.current === newest.id) return;
    lastEntryId.current = newest.id;
    setExpanded(true);
    if (collapseTimer.current !== null) {
      window.clearTimeout(collapseTimer.current);
    }
    // Errors stay open longer (8 s) since the user needs time to read.
    const dwell = newest.severity === "error" ? 8000 : 4000;
    collapseTimer.current = window.setTimeout(() => {
      setExpanded(false);
      collapseTimer.current = null;
    }, dwell);
    return () => {
      if (collapseTimer.current !== null) {
        window.clearTimeout(collapseTimer.current);
        collapseTimer.current = null;
      }
    };
  }, [log]);

  if (!log) return null;
  const newest = log.entries[log.entries.length - 1];

  // Sits as a normal block element in the parent's flow, but the
  // INNER bar is width-capped and horizontally centered so the
  // status bar above it (zoom / hover coords) stays unblocked at
  // the edges. Edge-to-edge was visually competing with everything
  // around it; centered + compact reads as a toast / status pill.
  return (
    <div className="flex w-full justify-center">
      <div className="w-full max-w-2xl mt-2 px-2">
        {expanded ? (
          // Expanded — full history (scrollable, capped at MAX_ENTRIES)
          <div className="rounded-t-md border border-gray-700 bg-gray-950/95 shadow-lg">
            <div className="flex items-center justify-between border-b border-gray-800 px-2 py-1">
              <span className="text-[11px] font-semibold text-gray-300">
                Log <span className="text-gray-500">
                  ({log.entries.length})
                </span>
              </span>
              <div className="flex items-center gap-1.5">
                <button
                  type="button"
                  onClick={log.clear}
                  className="rounded border border-gray-700 bg-gray-900 px-1.5 py-0.5 text-[10px] text-gray-400 hover:border-gray-500 hover:text-gray-200"
                  title="Clear all entries"
                >
                  Clear
                </button>
                <button
                  type="button"
                  onClick={() => setExpanded(false)}
                  className="rounded border border-gray-700 bg-gray-900 px-1.5 py-0.5 text-[10px] text-gray-400 hover:border-gray-500 hover:text-gray-200"
                  title="Collapse"
                >
                  ▾
                </button>
              </div>
            </div>
            <ul className="max-h-48 overflow-y-auto p-1">
              {log.entries.length === 0 && (
                <li className="px-2 py-1 text-[11px] text-gray-500">
                  No events yet.
                </li>
              )}
              {[...log.entries].reverse().map((e) => {
                const tint = SEV_TINT[e.severity];
                return (
                  <li
                    key={e.id}
                    className={`my-0.5 rounded px-2 py-1 text-[11px] ${tint.bg} ${tint.fg} ring-1 ring-inset ${tint.ring}`}
                  >
                    <div className="flex items-baseline gap-2">
                      <span className="font-mono text-[9px] text-gray-500">
                        {fmtTime(e.ts)}
                      </span>
                      <span className="flex-1">{e.message}</span>
                    </div>
                    {e.detail && (
                      <div className="mt-0.5 text-[10px] opacity-70">
                        {e.detail}
                      </div>
                    )}
                  </li>
                );
              })}
            </ul>
          </div>
        ) : newest ? (
          // Collapsed — just the newest entry as a thin bar.
          <button
            type="button"
            onClick={() => setExpanded(true)}
            className={`flex w-full items-center gap-2 rounded border px-2 py-1 text-left text-[11px] shadow-md ${SEV_TINT[newest.severity].bg} ${SEV_TINT[newest.severity].fg} ${SEV_TINT[newest.severity].ring} ring-1 ring-inset hover:opacity-90`}
            title="Click to see full log"
          >
            <span className="font-mono text-[9px] opacity-60">
              {fmtTime(newest.ts)}
            </span>
            <span className="flex-1 truncate">{newest.message}</span>
            {log.entries.length > 1 && (
              <span className="text-[9px] opacity-60">
                +{log.entries.length - 1} more
              </span>
            )}
            <span className="opacity-60">▴</span>
          </button>
        ) : null}
      </div>
    </div>
  );
}
