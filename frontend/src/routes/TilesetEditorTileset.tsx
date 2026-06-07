/**
 * Tileset Editor — per-tileset screen.
 *
 * Reads `:tileset` from the URL and shows the slot grid + per-slot
 * detail panel for the active install's tileset N. This is where the
 * library tab, AddStiToTilesetModal, MapForgeInjectSubModal, and the
 * JSD viewer/editor all live now — they used to be buried inside the
 * sector editor's pop-out.
 *
 * Layout:
 *   [ Slot grid ][ Slot detail + JSD ][ Library tab ]
 *
 * See `docs/TILESET_EDITOR_SPLIT.md` for the design rationale.
 */
import { useEffect, useMemo, useState } from "react";
import { Link, useParams, useSearchParams } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";

import { formatApiError } from "../lib/api";
import {
  fetchPaletteSheetBlobUrl,
  getMapForgeHealth,
  getPaletteSheetMeta,
  getTilesetPalette,
  listInstallMaps,
  streamPaletteSheetBuild,
  type PaletteSheetBuildEvent,
  type PaletteSheetCell,
  type PaletteSlot,
} from "../lib/mapforge";
import { loadSettings } from "../lib/mapforgeSettings";
import { pushRecentAddition } from "../lib/recentAdditions";
import { MapForgeLibrary } from "./MapForgeLibrary";
import { MapForgeInjectSubModal } from "./MapForgeInjectSubModal";
import { TilesetEditorJsdPanel } from "./TilesetEditorJsdPanel";

export default function TilesetEditorTileset() {
  const { tileset: tilesetParam } = useParams<{ tileset: string }>();
  const tileset = tilesetParam ? parseInt(tilesetParam, 10) : NaN;
  const [searchParams, setSearchParams] = useSearchParams();
  const slotParam = searchParams.get("slot");
  const initialSlot = slotParam ? parseInt(slotParam, 10) : null;

  const settings = useMemo(() => loadSettings(), []);
  const engineMaxTileSlot = settings.engineMaxTileSlot;

  // Get xmlPath via the existing install-maps endpoint (same pattern
  // the picker screen uses — keeps the sidecar surface minimal).
  const health = useQuery({
    queryKey: ["mapforge", "health"],
    queryFn: getMapForgeHealth,
  });
  const maps = useQuery({
    queryKey: ["mapforge", "installs", "maps"],
    queryFn: () => listInstallMaps(),
    enabled: health.data?.renderer_available === true
      && health.data.active_install_id !== null,
    staleTime: 5 * 60 * 1000,
  });
  const xmlPath = maps.data?.ja2set_xml ?? null;

  // Selected-slot state. URL-driven via ?slot=N so a "Open in Tileset
  // Editor" deep-link from MapForge lands on the right slot.
  const [selectedSlot, setSelectedSlot] = useState<number | null>(
    Number.isFinite(initialSlot) ? initialSlot : null,
  );
  // Keep URL in sync (no history thrash).
  useEffect(() => {
    if (selectedSlot === null) {
      if (searchParams.has("slot")) {
        const np = new URLSearchParams(searchParams);
        np.delete("slot");
        setSearchParams(np, { replace: true });
      }
      return;
    }
    if (searchParams.get("slot") !== String(selectedSlot)) {
      const np = new URLSearchParams(searchParams);
      np.set("slot", String(selectedSlot));
      setSearchParams(np, { replace: true });
    }
  }, [selectedSlot, searchParams, setSearchParams]);

  // Inject state — opens the inject-sub modal with the chosen source
  // sha. The user picks dest slot + src sub inside the modal.
  const [injectingFrom, setInjectingFrom] = useState<
    { sha256: string; sti_filename?: string } | null
  >(null);
  // Library mode — controls what the library pane does on click.
  // "add" is the default (opens AddStiToTilesetModal). "inject" makes
  // library clicks open the inject-sub modal instead. The user toggles
  // mode via a button in the slot detail panel.
  const [libraryMode, setLibraryMode] = useState<"add" | "inject">("add");

  if (!Number.isFinite(tileset)) {
    return (
      <div className="mx-auto max-w-5xl px-6 py-8">
        <Link to="/tileset-editor" className="text-sm text-blue-400 hover:underline">
          ← Back to tileset list
        </Link>
        <p className="mt-3 rounded border border-red-700 bg-red-950 p-3 text-sm">
          Invalid tileset index in URL.
        </p>
      </div>
    );
  }

  return (
    <div className="mx-auto max-w-7xl px-6 py-6">
      <div className="mb-4">
        <Link to="/tileset-editor" className="text-sm text-blue-400 hover:underline">
          ← Back to tileset list
        </Link>
        <h1 className="mt-2 text-xl font-semibold">
          Tileset Editor · Tileset {tileset}
        </h1>
        {xmlPath && (
          <p className="text-xs text-gray-500">
            Editing <code>{xmlPath}</code> · engine cap {engineMaxTileSlot}
          </p>
        )}
      </div>

      {health.data && !health.data.renderer_available && (
        <p className="rounded border border-amber-700 bg-amber-950 p-3 text-sm">
          MapForge backend not ready — the iso renderer isn't importable.
        </p>
      )}
      {maps.data && !maps.data.ja2set_xml && (
        <p className="rounded border border-amber-700 bg-amber-950 p-3 text-sm">
          No Ja2Set.dat.xml in this install.
        </p>
      )}

      {xmlPath && (
        <div
          className="grid gap-4 lg:grid-cols-[2fr_3fr_2fr]"
          style={{ minHeight: "72vh" }}
        >
          <SlotGrid
            xmlPath={xmlPath}
            tileset={tileset}
            selectedSlot={selectedSlot}
            engineMaxTileSlot={engineMaxTileSlot}
            onPick={setSelectedSlot}
          />
          <SlotDetail
            xmlPath={xmlPath}
            tileset={tileset}
            selectedSlot={selectedSlot}
            libraryMode={libraryMode}
            onSetLibraryMode={setLibraryMode}
          />
          <LibraryPane
            xmlPath={xmlPath}
            tileset={tileset}
            engineMaxTileSlot={engineMaxTileSlot}
            mode={libraryMode}
            onPickForInject={(sha256, sti_filename) => {
              setInjectingFrom({ sha256, sti_filename });
              // Reset to add mode so subsequent picks default-add.
              setLibraryMode("add");
            }}
          />
        </div>
      )}

      {/* Inject-sub modal */}
      {injectingFrom && (
        <MapForgeInjectSubModal
          srcSha256={injectingFrom.sha256}
          srcFilename={injectingFrom.sti_filename}
          tileset={tileset}
          onClose={() => setInjectingFrom(null)}
          onInjected={() => {
            // Nothing extra to do here — the modal already invalidates
            // the loose-slots + palette caches.
          }}
        />
      )}
    </div>
  );
}

// ─── Slot grid (left column) ─────────────────────────────────────────

function SlotGrid({
  xmlPath, tileset, selectedSlot, engineMaxTileSlot, onPick,
}: {
  xmlPath: string;
  tileset: number;
  selectedSlot: number | null;
  engineMaxTileSlot: number;
  onPick: (slot: number) => void;
}) {
  const palette = useQuery({
    queryKey: ["mapforge", "palette", xmlPath, tileset],
    queryFn: () => getTilesetPalette(xmlPath, tileset),
    enabled: !!xmlPath && tileset >= 0,
    staleTime: 5 * 60 * 1000,
  });
  const sheetMeta = useQuery({
    queryKey: ["mapforge", "palette-sheet-meta", xmlPath, tileset],
    queryFn: () => getPaletteSheetMeta(xmlPath, tileset),
    enabled: !!xmlPath && tileset >= 0,
    staleTime: 5 * 60 * 1000,
  });
  const [sheetUrl, setSheetUrl] = useState<string | null>(null);
  // Live bake progress — same streaming pattern as MapForgePalette.
  // Cold first-load of a tileset triggers a server-side bake of the
  // sprite sheet; without streaming progress the UI sat on bare
  // "Loading…" text for up to a minute.
  const [bakeProgress, setBakeProgress] = useState<{
    phase: string;
    label: string;
    current?: number;
    total?: number;
    detail?: string;
  } | null>(null);
  useEffect(() => {
    if (!xmlPath || tileset < 0) {
      setSheetUrl(null);
      setBakeProgress(null);
      return;
    }
    let cancelled = false;
    let created: string | null = null;
    setBakeProgress({ phase: "starting", label: "Starting bake" });
    streamPaletteSheetBuild(xmlPath, tileset, (evt: PaletteSheetBuildEvent) => {
      if (cancelled) return;
      if (evt.event === "phase") {
        setBakeProgress((p) => ({
          phase: evt.phase,
          label: evt.label,
          total: evt.total ?? p?.total,
          current: p?.current,
          detail: p?.detail,
        }));
      } else if (evt.event === "progress") {
        setBakeProgress((p) => ({
          phase: p?.phase ?? "bake",
          label: p?.label ?? "Baking",
          current: evt.current,
          total: evt.total,
          detail: evt.detail,
        }));
      } else if (evt.event === "done") {
        setBakeProgress(null);
      }
    })
      .then(() => {
        if (cancelled) return;
        return fetchPaletteSheetBlobUrl(xmlPath, tileset);
      })
      .then((u) => {
        if (!u) return;
        if (cancelled) { URL.revokeObjectURL(u); return; }
        created = u;
        setSheetUrl(u);
      })
      .catch(() => {
        if (!cancelled) setBakeProgress(null);
      });
    return () => {
      cancelled = true;
      if (created) URL.revokeObjectURL(created);
    };
    // sheetMeta.data?.fingerprint in deps: re-bake + re-fetch when the
    // tileset's slot map changes (the editor mutates slots), so the sheet
    // stays in sync after an edit.
  }, [xmlPath, tileset, sheetMeta.data?.fingerprint]);

  // slot → palette entry lookup for O(1) occupancy queries.
  const slotByIndex = useMemo(() => {
    const m = new Map<number, PaletteSlot>();
    for (const s of palette.data?.slots ?? []) m.set(s.slot, s);
    return m;
  }, [palette.data]);
  // cell-by-slot for sprite sheet positioning.
  const cellBySlot = useMemo(() => {
    const m = new Map<number, PaletteSheetCell>();
    for (const c of sheetMeta.data?.cells ?? []) m.set(c.slot, c);
    return m;
  }, [sheetMeta.data]);

  // Slot range we render: 0..engineMaxTileSlot inclusive. Above-cap
  // slots get a separate advisory row at the bottom.
  const aboveCapSlots = useMemo(() => {
    return (palette.data?.slots ?? [])
      .filter((s) => s.slot > engineMaxTileSlot)
      .map((s) => s.slot);
  }, [palette.data, engineMaxTileSlot]);

  return (
    <div className="flex flex-col rounded border border-gray-700 bg-gray-950">
      <div className="border-b border-gray-800 bg-gray-900 px-2 py-1.5 text-xs">
        <strong className="text-gray-200">Slots</strong>{" "}
        <span className="text-gray-500">
          0–{engineMaxTileSlot} ·{" "}
          {slotByIndex.size} registered
        </span>
      </div>
      <div className="min-h-0 flex-1 overflow-y-auto p-2">
        {(palette.isLoading || sheetMeta.isLoading || !sheetUrl) && (
          <div className="space-y-1.5" role="status" aria-busy="true">
            <p className="text-xs text-gray-200">
              {bakeProgress?.label ? `${bakeProgress.label}…` : "Loading slot grid…"}
            </p>
            {bakeProgress?.detail && (
              <p className="truncate font-mono text-[10px] text-blue-300">
                {bakeProgress.detail}
              </p>
            )}
            {bakeProgress?.current !== undefined && bakeProgress?.total ? (
              <>
                <div className="h-1.5 w-full overflow-hidden rounded-full border border-gray-800 bg-gray-900">
                  <div
                    className="h-full bg-blue-500 transition-[width] duration-100 ease-linear"
                    style={{
                      width: `${Math.min(100, Math.round((bakeProgress.current / bakeProgress.total) * 100))}%`,
                    }}
                  />
                </div>
                <p className="text-[10px] text-gray-500">
                  {bakeProgress.current} / {bakeProgress.total}{" "}
                  ({Math.round((bakeProgress.current / bakeProgress.total) * 100)}%)
                </p>
              </>
            ) : (
              <div className="h-1.5 w-full overflow-hidden rounded-full border border-gray-800 bg-gray-900">
                <div className="h-full w-full bg-gradient-to-r from-blue-700/30 via-blue-400/60 to-blue-700/30 animate-pulse" />
              </div>
            )}
            <p className="text-[10px] text-gray-600">
              First load of a tileset can take up to a minute while the
              sprite sheet bakes. Subsequent opens are instant.
            </p>
          </div>
        )}
        {palette.error && (
          <p className="text-xs text-red-400">{formatApiError(palette.error)}</p>
        )}
        {palette.data && sheetMeta.data && sheetUrl && (
          <div className="grid grid-cols-8 gap-1">
            {Array.from({ length: engineMaxTileSlot + 1 }, (_, slot) => {
              const entry = slotByIndex.get(slot) ?? null;
              const cell = cellBySlot.get(slot) ?? null;
              const isSelected = selectedSlot === slot;
              const empty = entry === null;
              return (
                <button
                  key={slot}
                  type="button"
                  onClick={() => onPick(slot)}
                  title={
                    entry
                      ? `slot ${slot} · ${entry.sti_filename} (${entry.frame_count} frame${entry.frame_count === 1 ? "" : "s"})${entry.has_jsd ? " · JSD" : ""}`
                      : `slot ${slot} · empty`
                  }
                  className={`relative flex aspect-square flex-col items-center justify-center rounded border text-[8px] ${
                    isSelected
                      ? "border-emerald-500 bg-emerald-950/50 ring-1 ring-emerald-500"
                      : empty
                        ? "border-gray-800 bg-gray-950 text-gray-700 hover:border-gray-600"
                        : "border-gray-700 bg-gray-900 hover:border-gray-500"
                  }`}
                >
                  {entry && cell ? (
                    <div
                      style={{
                        width: cell.w, height: cell.h,
                        backgroundImage: `url(${sheetUrl})`,
                        backgroundPosition: `-${cell.px}px -${cell.py}px`,
                        backgroundSize: `${sheetMeta.data.sheet_w}px ${sheetMeta.data.sheet_h}px`,
                        backgroundRepeat: "no-repeat",
                        imageRendering: "pixelated",
                      }}
                    />
                  ) : empty ? (
                    <span className="text-gray-700">·</span>
                  ) : (
                    <span className="inline-block h-6 w-6 rounded bg-gray-800" />
                  )}
                  <span className={`absolute bottom-0 left-0 rounded-tr px-0.5 text-[7px] ${
                    isSelected ? "bg-emerald-900/80 text-emerald-200" : "bg-gray-800/80 text-gray-400"
                  }`}>
                    {slot}
                  </span>
                  {entry?.has_jsd && (
                    <span
                      className="absolute right-0 top-0 rounded-bl rounded-tr bg-amber-700/85 px-0.5 text-[6px] text-amber-50"
                      title="JSD companion exists"
                    >J</span>
                  )}
                </button>
              );
            })}
          </div>
        )}
        {aboveCapSlots.length > 0 && (
          <details className="mt-3 rounded border border-amber-800 bg-amber-950/20 p-2 text-[11px] text-amber-300">
            <summary className="cursor-pointer">
              {aboveCapSlots.length} slot{aboveCapSlots.length === 1 ? "" : "s"}{" "}
              above engine cap {engineMaxTileSlot}
            </summary>
            <p className="mt-1 text-[10px] text-amber-400/80">
              These slots are in the XML but the engine's compiled
              NUMBEROFTILETYPES is lower — painting them CTDs the game.
              Raise the cap in Settings only if your ja2.exe is a forked
              build that supports more.
            </p>
            <ul className="mt-1 flex flex-wrap gap-1">
              {aboveCapSlots.map((slot) => (
                <li key={slot} className="rounded bg-amber-900/40 px-1 font-mono">
                  {slot}
                </li>
              ))}
            </ul>
          </details>
        )}
      </div>
    </div>
  );
}

// ─── Slot detail (middle column) ─────────────────────────────────────

function SlotDetail({
  xmlPath, tileset, selectedSlot, libraryMode, onSetLibraryMode,
}: {
  xmlPath: string;
  tileset: number;
  selectedSlot: number | null;
  libraryMode: "add" | "inject";
  onSetLibraryMode: (m: "add" | "inject") => void;
}) {
  const palette = useQuery({
    queryKey: ["mapforge", "palette", xmlPath, tileset],
    queryFn: () => getTilesetPalette(xmlPath, tileset),
    enabled: !!xmlPath && tileset >= 0,
    staleTime: 5 * 60 * 1000,
  });
  const entry = useMemo(() => {
    if (selectedSlot === null) return null;
    return palette.data?.slots.find((s) => s.slot === selectedSlot) ?? null;
  }, [palette.data, selectedSlot]);

  return (
    <div className="flex flex-col rounded border border-gray-700 bg-gray-950">
      <div className="border-b border-gray-800 bg-gray-900 px-2 py-1.5 text-xs">
        <strong className="text-gray-200">Slot detail</strong>
      </div>
      <div className="min-h-0 flex-1 overflow-y-auto p-3 space-y-3">
        {selectedSlot === null && (
          <p className="text-xs italic text-gray-500">
            Pick a slot from the grid to inspect or edit.
          </p>
        )}
        {selectedSlot !== null && !entry && (
          <div className="text-xs text-gray-400">
            <p>Slot <span className="font-mono">{selectedSlot}</span> is empty.</p>
            <p className="mt-1 text-gray-500">
              Drop a library STI into this slot using the Library pane on
              the right (or pick "auto" to let the allocator place it).
            </p>
          </div>
        )}
        {entry && (
          <>
            <div>
              <h3 className="font-mono text-sm text-emerald-200">
                {entry.sti_filename}
              </h3>
              <p className="text-[11px] text-gray-500">
                slot {entry.slot} · {entry.frame_count} frame
                {entry.frame_count === 1 ? "" : "s"} · category{" "}
                <span className="text-gray-300">{entry.category}</span>
                {entry.has_jsd && (
                  <> · <span className="text-amber-300">has JSD</span></>
                )}
              </p>
            </div>

            {/* JSD panel — view or edit. Renders nothing if no JSD. */}
            {entry.has_jsd && (
              <TilesetEditorJsdPanel
                xmlPath={xmlPath}
                tileset={tileset}
                slot={entry.slot}
              />
            )}

            {/* Inject-sub entry point. Toggles the Library pane's
                click mode — in inject mode, picking an STI in the
                library opens the InjectSubModal with that source pre-
                set. The user then picks the destination slot + which
                source sub to append inside the modal. */}
            <div className="rounded border border-gray-800 p-2 text-xs">
              <p className="mb-1 font-semibold text-gray-300">
                Inject sub-frame
              </p>
              <p className="mb-2 text-[11px] text-gray-500">
                Append a single sub-frame from any library STI onto
                this tileset's existing STI binaries. Click the button
                below, then pick the source STI from the Library pane.
              </p>
              {libraryMode === "inject" ? (
                <div className="space-y-1">
                  <p className="rounded border border-amber-700 bg-amber-950/40 px-2 py-1 text-[11px] text-amber-200">
                    Pick a library STI on the right to use as the
                    inject source.
                  </p>
                  <button
                    type="button"
                    onClick={() => onSetLibraryMode("add")}
                    className="rounded border border-gray-700 px-2 py-0.5 text-[11px] text-gray-300 hover:border-gray-500"
                  >
                    Cancel inject — back to add mode
                  </button>
                </div>
              ) : (
                <button
                  type="button"
                  onClick={() => onSetLibraryMode("inject")}
                  className="rounded border border-amber-700 bg-amber-950/40 px-3 py-1 text-[11px] text-amber-200 hover:border-amber-500"
                >
                  + Pick source from library
                </button>
              )}
              <p className="mt-2 text-[10px] text-gray-600">
                Safety: source + dest palettes must match unless you
                force; dest STI must be loose on disk (not SLF). See
                <code className="mx-0.5">docs/ASSET_BROWSER_PLAN.md §4</code>.
              </p>
            </div>
          </>
        )}
      </div>
    </div>
  );
}

// ─── Library pane (right column) ─────────────────────────────────────

function LibraryPane({
  xmlPath, tileset, engineMaxTileSlot, mode, onPickForInject,
}: {
  xmlPath: string;
  tileset: number;
  engineMaxTileSlot: number;
  mode: "add" | "inject";
  onPickForInject: (sha256: string, sti_filename: string) => void;
}) {
  // MapForgeLibrary manages its own AddStiToTilesetModal lifecycle in
  // "add" mode. In "inject" mode we override clicks via onPickSha so
  // the parent can open the inject-sub modal instead.
  return (
    <div className="flex flex-col rounded border border-gray-700 bg-gray-950">
      <div className="flex items-center justify-between border-b border-gray-800 bg-gray-900 px-2 py-1.5 text-xs">
        <div>
          <strong className="text-gray-200">Library</strong>{" "}
          <span className="text-gray-500">
            (catalog across all installs)
          </span>
        </div>
        <span className={`rounded px-1.5 py-0.5 text-[10px] ${
          mode === "inject"
            ? "bg-amber-900/60 text-amber-200"
            : "bg-emerald-900/40 text-emerald-200"
        }`}>
          {mode === "inject" ? "inject mode" : "add mode"}
        </span>
      </div>
      <div className="min-h-0 flex-1 overflow-hidden">
        <MapForgeLibrary
          xmlPath={xmlPath}
          tileset={tileset}
          engineMaxTileSlot={engineMaxTileSlot}
          onAdded={(addition) => pushRecentAddition(xmlPath, tileset, addition)}
          {...(mode === "inject" ? { onPickSha: onPickForInject } : {})}
        />
      </div>
    </div>
  );
}
