/**
 * Standalone SLF Extractor.
 *
 * Pick a `.slf` archive → list its entries → optionally select a
 * subset → pick a destination directory → extract.
 *
 * Long extracts surface NDJSON progress via /tools/slf/extract/stream;
 * short ones use the simpler one-shot endpoint.
 */
import { useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { useMutation, useQuery } from "@tanstack/react-query";

import { pickDirectory, pickFile } from "../lib/tauri";
import {
  listSlf,
  streamExtractSlf,
  type SlfEntry,
  type SlfExtractEvent,
  type SlfExtractResult,
} from "../lib/tools";

// Most common SLF content extensions, in display order. Extension chips
// below + "other" catches everything else. Pre-built so the chip order
// stays stable across renders.
const EXT_CHIPS: { key: string; label: string }[] = [
  { key: ".sti", label: ".sti" },
  { key: ".jsd", label: ".jsd" },
  { key: ".dat", label: ".dat" },
  { key: ".pcx", label: ".pcx" },
  { key: ".wav", label: ".wav" },
  { key: ".edt", label: ".edt" },
  { key: ".xml", label: ".xml" },
  { key: ".ini", label: ".ini" },
  { key: ".txt", label: ".txt" },
  { key: "other", label: "other" },
];

function extOf(relpath: string): string {
  const dot = relpath.lastIndexOf(".");
  if (dot < 0) return "other";
  const ext = relpath.slice(dot).toLowerCase();
  return EXT_CHIPS.some((c) => c.key === ext) ? ext : "other";
}

export default function ToolsSlfExtractor() {
  const [slfPath, setSlfPath] = useState<string | null>(null);
  const [destDir, setDestDir] = useState<string | null>(null);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [search, setSearch] = useState("");
  const [overwrite, setOverwrite] = useState<boolean>(true);
  // Selected ext chips. Empty set = no ext filter (show all). Multi-
  // select so the user can scope to ".sti + .jsd" together when
  // harvesting an asset bundle.
  const [extFilter, setExtFilter] = useState<Set<string>>(new Set());

  const listing = useQuery({
    queryKey: ["tools", "slf-list", slfPath],
    queryFn: () => listSlf(slfPath!),
    enabled: !!slfPath,
    retry: false,
    staleTime: 60 * 1000,
  });

  const entries: SlfEntry[] = listing.data?.entries ?? [];

  // Per-extension counts — used for the chip labels AND determines the
  // working set when ext filters are active. Computed in one pass.
  const extCounts = useMemo(() => {
    const c: Record<string, number> = {};
    for (const e of entries) {
      const k = extOf(e.relpath);
      c[k] = (c[k] ?? 0) + 1;
    }
    return c;
  }, [entries]);

  const matches = useMemo(() => {
    const q = search.trim().toLowerCase();
    const hasExt = extFilter.size > 0;
    if (!q && !hasExt) return entries;
    return entries.filter((e) => {
      if (q && !e.relpath.toLowerCase().includes(q)) return false;
      if (hasExt && !extFilter.has(extOf(e.relpath))) return false;
      return true;
    });
  }, [entries, search, extFilter]);

  async function onPickSlf() {
    const picked = await pickFile("Pick an .slf archive", [
      { name: "SLF archive", extensions: ["slf", "SLF"] },
    ]);
    if (picked) {
      setSlfPath(picked);
      setSelected(new Set());
      setExtFilter(new Set());
    }
  }
  async function onPickDest() {
    // Explicit key so SLF extract-dest is remembered independently
    // from any other "Pick a folder" dialog (e.g. a future "Pick an
    // install root" would want its own memory).
    const picked = await pickDirectory(
      "Pick a destination folder",
      "slf-extract-dest",
    );
    if (picked) setDestDir(picked);
  }

  function toggle(relpath: string) {
    const next = new Set(selected);
    if (next.has(relpath)) next.delete(relpath);
    else next.add(relpath);
    setSelected(next);
  }

  function toggleExt(ext: string) {
    const next = new Set(extFilter);
    if (next.has(ext)) next.delete(ext);
    else next.add(ext);
    setExtFilter(next);
  }

  function selectAllVisible() {
    const next = new Set(selected);
    for (const e of matches) next.add(e.relpath);
    setSelected(next);
  }
  function selectAllEntries() {
    setSelected(new Set(entries.map((e) => e.relpath)));
  }
  function clearSelection() {
    setSelected(new Set());
  }

  // Progress + result state for the streaming extract.
  const [progress, setProgress] = useState<{
    phase: string;
    current: number;
    total: number;
    detail: string;
  } | null>(null);
  const [result, setResult] = useState<SlfExtractResult | null>(null);

  const extract = useMutation({
    mutationFn: async () => {
      if (!slfPath || !destDir) return null;
      setResult(null);
      setProgress({ phase: "Starting", current: 0, total: 0, detail: "" });
      const members =
        selected.size > 0 ? Array.from(selected) : undefined;
      const onEvent = (e: SlfExtractEvent) => {
        if (e.event === "phase") {
          setProgress((p) => ({
            phase: e.label,
            current: p?.current ?? 0,
            total: p?.total ?? 0,
            detail: p?.detail ?? "",
          }));
        } else if (e.event === "progress") {
          setProgress((p) => ({
            phase: p?.phase ?? "",
            current: e.current,
            total: e.total,
            detail: e.detail,
          }));
        }
      };
      // try/finally so a stream-side error doesn't leave the progress
      // card stuck on "Starting" while the red error card also renders.
      // The bug: previously `setProgress(null)` only ran after the
      // success path; a thrown error inside streamExtractSlf left
      // `progress` populated forever (2026-05-25 review HIGH).
      try {
        const final = await streamExtractSlf(
          {
            slf_path: slfPath,
            dest_dir: destDir,
            members,
            overwrite,
          },
          onEvent,
        );
        setResult(final);
        return final;
      } finally {
        setProgress(null);
      }
    },
  });

  const canExtract = !!slfPath && !!destDir && !extract.isPending;

  return (
    <div className="mx-auto max-w-6xl px-6 py-8 space-y-4">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold">SLF Extractor</h1>
          <p className="text-sm text-wasteland-300 mt-1">
            Open a .slf archive and extract files to a folder. Pick specific
            entries or take the whole archive.
          </p>
        </div>
        <Link to="/tools" className="text-sm text-wasteland-400 hover:text-rust-400">
          ← Tools
        </Link>
      </div>

      {/* File pickers */}
      <div className="card space-y-3">
        <div className="flex items-center gap-3 flex-wrap">
          <button onClick={onPickSlf} className="btn-primary">
            {slfPath ? "Pick another .slf..." : "Pick .slf archive..."}
          </button>
          {slfPath && (
            <span
              className="text-xs font-mono text-wasteland-400 truncate min-w-0"
              title={slfPath}
            >
              {slfPath}
            </span>
          )}
        </div>
        <div className="flex items-center gap-3 flex-wrap">
          <button onClick={onPickDest} className="btn-primary">
            {destDir ? "Pick another folder..." : "Pick destination folder..."}
          </button>
          {destDir && (
            <span
              className="text-xs font-mono text-wasteland-400 truncate min-w-0"
              title={destDir}
            >
              {destDir}
            </span>
          )}
        </div>
        <label className="flex items-center gap-2 text-xs text-wasteland-300">
          <input
            type="checkbox"
            checked={overwrite}
            onChange={(e) => setOverwrite(e.target.checked)}
          />
          Overwrite files that already exist in the destination
        </label>
      </div>

      {listing.isLoading && (
        <div className="card text-sm text-wasteland-300">Reading archive...</div>
      )}
      {listing.isError && (
        <div className="card border-rust-500/40 bg-rust-500/10 text-sm text-rust-200">
          {String(
            listing.error instanceof Error
              ? listing.error.message
              : listing.error,
          )}
        </div>
      )}

      {listing.data && (
        <div className="card space-y-3">
          <div className="flex items-center justify-between gap-3 flex-wrap">
            <h2 className="text-base font-semibold">
              Entries ({listing.data.entry_count.toLocaleString()})
              {matches.length !== entries.length && (
                <span className="ml-2 text-xs font-normal text-wasteland-500">
                  · {matches.length.toLocaleString()} shown
                </span>
              )}
            </h2>
            <div className="flex items-center gap-2">
              <input
                className="input max-w-xs text-xs"
                placeholder="Filter by relpath..."
                value={search}
                onChange={(e) => setSearch(e.target.value)}
              />
              <button
                onClick={selectAllEntries}
                type="button"
                className="text-xs rounded border border-wasteland-700 px-2 py-0.5 hover:border-rust-500"
                title="Select every entry in the archive (ignores filters)"
              >
                Select all ({entries.length.toLocaleString()})
              </button>
              <button
                onClick={selectAllVisible}
                type="button"
                className="text-xs rounded border border-wasteland-700 px-2 py-0.5 hover:border-rust-500"
                disabled={matches.length === entries.length}
                title="Select only the entries currently visible after filters"
              >
                Select visible ({matches.length.toLocaleString()})
              </button>
              <button
                onClick={clearSelection}
                type="button"
                className="text-xs rounded border border-wasteland-700 px-2 py-0.5 hover:border-rust-500"
                disabled={selected.size === 0}
              >
                Clear ({selected.size})
              </button>
            </div>
          </div>

          {/* Extension chips — multi-select scope filter. Empty
              selection = show all. Counts are pre-filter so the user
              can see how many of each ext exist. */}
          <div className="flex items-center gap-1.5 flex-wrap">
            <span className="text-[10px] uppercase tracking-wider text-wasteland-500 mr-1">
              ext
            </span>
            {EXT_CHIPS.map((c) => {
              const n = extCounts[c.key] ?? 0;
              if (n === 0) return null;
              const active = extFilter.has(c.key);
              return (
                <button
                  key={c.key}
                  type="button"
                  onClick={() => toggleExt(c.key)}
                  className={`rounded border px-1.5 py-0.5 text-[10px] font-mono ${
                    active
                      ? "border-rust-500 bg-rust-500/20 text-rust-200"
                      : "border-wasteland-700 bg-wasteland-900 text-wasteland-300 hover:border-wasteland-500"
                  }`}
                >
                  {c.label}{" "}
                  <span className="text-wasteland-500">{n}</span>
                </button>
              );
            })}
            {extFilter.size > 0 && (
              <button
                type="button"
                onClick={() => setExtFilter(new Set())}
                className="rounded px-1.5 py-0.5 text-[10px] text-wasteland-400 hover:text-rust-400 underline underline-offset-2"
              >
                clear ext filter
              </button>
            )}
          </div>

          {listing.data.library_name && (
            <div className="text-[11px] text-wasteland-400 font-mono">
              library: {listing.data.library_name}
              {listing.data.library_path && ` • ${listing.data.library_path}`}
            </div>
          )}
          <p className="text-[11px] text-wasteland-400">
            With no selection, the Extract button takes every entry in the
            archive. Use the chips + filter to narrow the table, then
            "Select visible" to subset the extract.
          </p>
          <div className="rounded border border-wasteland-800 overflow-hidden">
            <div className="max-h-96 overflow-y-auto">
              <table className="w-full text-xs">
                <thead className="sticky top-0 bg-wasteland-900 text-wasteland-400">
                  <tr>
                    <th className="px-2 py-1 w-8"></th>
                    <th className="px-2 py-1 text-left">relpath</th>
                    <th className="px-2 py-1 text-right">size</th>
                  </tr>
                </thead>
                <tbody>
                  {matches.map((e) => {
                    const checked = selected.has(e.relpath);
                    return (
                      <tr
                        key={e.relpath}
                        className="border-t border-wasteland-800 hover:bg-wasteland-800/40"
                      >
                        <td className="px-2 py-1">
                          <input
                            type="checkbox"
                            checked={checked}
                            onChange={() => toggle(e.relpath)}
                          />
                        </td>
                        <td className="px-2 py-1 font-mono">{e.relpath}</td>
                        <td className="px-2 py-1 text-right font-mono text-wasteland-400">
                          {e.size.toLocaleString()}
                        </td>
                      </tr>
                    );
                  })}
                  {matches.length === 0 && (
                    <tr>
                      <td
                        colSpan={3}
                        className="px-2 py-3 text-center text-wasteland-500"
                      >
                        No entries match "{search}".
                      </td>
                    </tr>
                  )}
                </tbody>
              </table>
            </div>
          </div>
        </div>
      )}

      {/* Extract button + progress */}
      {listing.data && (
        <div className="card space-y-2">
          <div className="flex items-center gap-3">
            <button
              type="button"
              onClick={() => extract.mutate()}
              disabled={!canExtract}
              className="btn-primary disabled:opacity-50"
            >
              {extract.isPending
                ? "Extracting..."
                : selected.size > 0
                  ? `Extract ${selected.size} selected`
                  : `Extract all (${listing.data.entry_count})`}
            </button>
            {!destDir && (
              <span className="text-xs text-amber-300">
                Pick a destination folder first.
              </span>
            )}
          </div>

          {progress && (
            <div className="space-y-1">
              <div className="text-xs text-wasteland-300">{progress.phase}</div>
              {progress.total > 0 && (
                <div>
                  <div className="h-2 w-full bg-wasteland-800 rounded overflow-hidden">
                    <div
                      className="h-full bg-rust-500 transition-all"
                      style={{
                        width: `${Math.round(
                          (progress.current / Math.max(1, progress.total)) * 100,
                        )}%`,
                      }}
                    />
                  </div>
                  <div className="text-[10px] text-wasteland-500 font-mono mt-0.5 truncate">
                    {progress.current} / {progress.total} • {progress.detail}
                  </div>
                </div>
              )}
              {progress.total === 0 && (
                <div className="h-2 w-full bg-wasteland-800 rounded overflow-hidden">
                  <div
                    className="h-full w-1/3 bg-rust-500/50 animate-pulse"
                  />
                </div>
              )}
            </div>
          )}

          {extract.isError && (
            <div className="rounded border border-rust-500/40 bg-rust-500/10 p-2 text-xs text-rust-200">
              {String(
                extract.error instanceof Error
                  ? extract.error.message
                  : extract.error,
              )}
            </div>
          )}

          {result && (
            <div className="rounded border border-emerald-600/40 bg-emerald-900/20 p-3 text-xs text-emerald-100 space-y-1">
              <div>
                <span className="font-semibold">Extracted</span>{" "}
                {result.extracted} file{result.extracted === 1 ? "" : "s"}
                {result.skipped > 0 && (
                  <>
                    {" "}
                    • <span className="font-semibold">Skipped</span>{" "}
                    {result.skipped}
                  </>
                )}
              </div>
              <div className="text-[11px] font-mono truncate" title={result.dest_dir}>
                → {result.dest_dir}
              </div>
              {result.errors.length > 0 && (
                <details className="mt-1">
                  <summary className="cursor-pointer text-amber-300">
                    {result.errors.length} error
                    {result.errors.length === 1 ? "" : "s"}
                  </summary>
                  <ul className="mt-1 max-h-32 overflow-y-auto text-amber-200">
                    {result.errors.map((e, i) => (
                      <li key={i} className="font-mono">{e}</li>
                    ))}
                  </ul>
                </details>
              )}
            </div>
          )}
        </div>
      )}

      {!slfPath && !listing.isLoading && (
        <div className="card text-sm text-wasteland-300">
          Pick a .slf archive to see its contents.
        </div>
      )}
    </div>
  );
}
