/**
 * MapForge sector viewer + tile inspector.
 *
 * Reads `?dat=<path>&xml=<path>` from the URL, fetches sector info, lets
 * the user pick a room (or render the full sector), shows the iso PNG
 * with an SVG overlay (diamond grid, hover highlight, pinned tile,
 * optional room-number labels), and an inspector panel that auto-updates
 * on canvas clicks.
 *
 * Phase 0.6 polish:
 *   - mouse-wheel zoom + drag pan (CSS transform on a wrapper div)
 *   - SVG overlay with diamond grid for room-scope views (skipped for
 *     full-sector renders where 25k diamonds would tank performance)
 *   - hover diamond highlight + live (x,y) readout
 *   - pinned diamond highlight (the tile shown in the inspector)
 *   - room-number labels (toggleable)
 *   - "Reset view" snaps zoom/pan back to 1×/origin
 *
 * Phase 1 (next):
 *   - sub-frame visual picker
 *   - first writable edit op
 */
import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";

import { formatApiError } from "../lib/api";
import {
  applyEdits,
  closeSession,
  extractSlfToLoose,
  previewExtractSlfToLoose,
  fetchAtlasBlobUrl,
  fetchStiFrameBlobUrl,
  getAtlasManifest,
  getSectorInfo,
  getSessionParsed,
  getStiJsd,
  openSession,
  prefetchPaletteSheet,
  saveSession,
  streamAtlasBuild,
  validateSession,
  type LayerName,
  type RecentAddition,
  type RoomSummary,
  type SectorInfo,
  type SessionEdit,
  type SessionInfo,
  type TileInspection,
  type TilesetInfo,
} from "../lib/mapforge";
import {
  IsoRenderer,
  imagePixelToTile,
  tileDiamondCorners,
  tileToCanvasPixel,
  PHASE_WEIGHTS,
  PROGRESS_PHASE_LABELS,
  type ProgressPhase,
  type RenderMeta,
} from "../lib/IsoRenderer";
import { IsoRendererGL } from "../lib/IsoRendererGL";
import { type ActiveBrush } from "./MapForgePalette";
import { MapForgeAssetBrowserBody } from "./MapForgeAssetViewer";
import { MapForgeTilesetBrowser } from "./MapForgeTilesetBrowser";
import { MapForgePaletteRail } from "./MapForgePaletteRail";
import {
  MapForgeLogFull,
  MapForgeLogProvider,
  useMapForgeLog,
} from "./MapForgeLog";
import { MapForgeSettingsModal } from "./MapForgeSettingsModal";
import MapForgeConsole, { type CommandSpec } from "./MapForgeConsole";
import { MapForgeGeneratePanel } from "./MapForgeGeneratePanel";
import { MapForgeHelpOverlay } from "./MapForgeHelpOverlay";
import { MapForgeValidateBody } from "./MapForgeValidatePanel";
import ConfirmModal from "../components/ConfirmModal";
import {
  MapForgeDock,
  PANEL_ORDER,
  PANEL_TITLE,
  reopenDockPanel,
  resetDockLayout,
  saveUserDefaultLayout,
} from "./MapForgeDock";
import { MapForgeDockContext, type DockPanelId } from "./mapForgeDockContext";
import type { DockviewApi } from "dockview";
import {
  generateRadar,
  listGenerators,
  listTilesets,
  runGenerator,
} from "../lib/mapforge";
import {
  actionForBinding,
  bindingFor,
  encodeWheelEvent,
  loadSettings,
  type MapForgeActionId,
  type MapForgeSettings,
} from "../lib/mapforgeSettings";
import { findShadowSlot, isShadowOnlySlot } from "../lib/jaSlotPairs";
import {
  usePersistentBrushBucket, sameBrush,
  RECENT_BRUSHES_KEY, FAVORITE_BRUSHES_KEY,
  RECENT_BRUSHES_CAP, FAVORITE_BRUSHES_CAP,
} from "../lib/brushBuckets";
import { useUnsavedGuard } from "../lib/useUnsavedGuard";
import {
  shapeTiles,
  type ShapeKind,
  type Tile,
} from "../lib/mapShapes";
import {
  sliceRegion,
  pasteEdits,
  stripBuddyShadows,
  type ClipboardRegion,
} from "../lib/mapClipboard";

/** UI tool modes. Inspect = click-to-pin; Pencil = click/drag to paint
 * the active brush; Shape = drag to define a rectangle / line / room that
 * commits as one undoable stroke. */
type Tool = "inspect" | "pencil" | "shape" | "select" | "height";

/** Compile-time exhaustiveness guard. When a new `Tool` is added to the
 * union, any `if`/`switch` that forwards an unhandled value here stops
 * compiling (the argument is no longer narrowed to `never`) — so a new
 * tool can't silently fall through the canvas dispatch. Throws at
 * runtime as a backstop. */
function assertNever(x: never): never {
  throw new Error(`Unhandled Tool case: ${JSON.stringify(x)}`);
}

/** What a committed stroke writes to each tile. `place` paints the active
 * brush into a layer; `set_room` stamps a room id. Mirrors the subset of
 * edit ops the shape + pencil tools emit. */
type StrokeSpec =
  | { op: "place"; layer: LayerName; slot: number; sub: number }
  | { op: "set_room"; roomId: number };

/** Per-flag tooltip text for the JSD viewer's flag chips. Mirrors the
 *  bit definitions in JA2 1.13's worlddat.h. Keep in sync with the
 *  backend's `flag_names` decoder — if a new flag bit gets surfaced
 *  there but isn't named here, the chip falls back to "(no description
 *  available)". */
function _jsdFlagTooltip(flag: string): string {
  const table: Record<string, string> = {
    TILE_ON_ROOF: "Renders on the upper floor (roof level) — appears only when the user is on or peering at the roof.",
    HAS_SHADOW_BUDDY: "Slot has a paired shadow sprite at slot+1; engine auto-draws both.",
    DAMAGED: "Marks the struct as the damaged variant — used for ruin / blasted-wall states.",
    EXPLOSIVE: "Triggers an explosion when destroyed (mines, gas tanks, etc.).",
    PARTIAL_WALL: "Half-height or fragmentary wall — engine treats it as cover but not as full sight-block.",
    FULL_WALL: "Full-height wall — blocks line of sight + walking.",
    WIREFRAME: "Drawn in wireframe overlay above other tiles for editor / debug visibility.",
    PASSABLE: "Mercs and projectiles can pass through this struct (vegetation, smoke).",
    EXIT_GRID: "Tile marks a sector boundary or strategic exit point.",
    BLOCKS_LOS: "Hard line-of-sight block — engine treats as an opaque obstacle.",
    OBSTACLE: "Treated as an obstacle for pathfinding even when visually subtle (rope, low fence).",
    SLIDING_DOOR: "Door variant — slides open horizontally rather than swinging.",
    DOOR: "Engine recognizes this as an openable/closeable door.",
    OPENABLE: "Tile responds to the 'open' action (containers, hatches).",
    SEETHROUGH: "Visible-through tile — engine renders behind it but treats as light cover.",
    BURNABLE: "Catches fire when exposed to flame attacks.",
    TALL_OBJECT: "Renders with a height lift so it occludes tiles to the south correctly.",
    STRUCTURE: "Solid structural piece (walls, big rocks) — engine snaps shadows + LOS to it.",
    GENERIC: "Default flag with no special engine behavior — usually surface decoration.",
  };
  return table[flag] ?? `${flag} — engine flag; no description in our table yet.`;
}

/**
 * Translate one generator-emitted op (backend snake_case shape from
 * `EditOp` in routes/mapforge.py) into the renderer's LocalEdit
 * shape (camelCase, used by `IsoRenderer.applyLocalEdit`) and apply.
 *
 * Without this mirror, generator ops apply server-side but the
 * frontend's IsoRenderer keeps showing the pre-generator parsed
 * dict — a user hit this: ":gen wipe says 179,200 ops
 * applied but the map still shows trees". The fix: each streamed op
 * goes through the same `applyLocalEdit` the paint brush uses.
 */
function _mirrorGeneratorOp(renderer: IsoRenderer, op: unknown): void {
  if (op === null || typeof op !== "object") return;
  const o = op as Record<string, unknown>;
  const opName = o.op as string;
  const x = o.x as number;
  const y = o.y as number;
  const layer = o.layer as LayerName | undefined;
  // Record the pre-edit snapshot BEFORE mutating so Ctrl+Z can revert
  // the whole generator run as a single undo step. recordSnapshot /
  // recordRoomSnapshot early-return when no stroke is active, so this
  // is safe outside a beginStroke/endStroke pair (called by the
  // console + Generate-panel generator handlers, nobody else right now).
  // User-reported: Ctrl+Z does nothing after a generator run.
  if (opName === "set_room") {
    renderer.recordRoomSnapshot(x, y);
  } else if (opName === "set_height") {
    renderer.recordHeightSnapshot(x, y);
  } else if (layer) {
    renderer.recordSnapshot(x, y, layer);
  }
  // Translate snake_case → camelCase for the two fields where the
  // shapes differ. Everything else (x, y, op, layer, slot, sub,
  // entries) keeps the same key name.
  const translated = {
    x,
    y,
    op: opName as "place" | "add" | "remove" | "replace" | "set_entries"
      | "set_room" | "set_height",
    layer,
    slot: o.slot as number | undefined,
    sub: o.sub as number | undefined,
    entryIndex: o.entry_index as number | undefined,
    entries: o.entries as number[][] | undefined,
    roomId: o.room_id as number | undefined,
    height: o.height as number | undefined,
  };
  renderer.applyLocalEdit(translated);
}

// Cap on grid rendering. Switched to a single <path d="..."/> element
// (instead of 25k <polygon> nodes), so the perf cliff is much higher
// than before — 160×160 sectors render fine. We still cap as a defense
// against pathological maps, but at a much larger limit.
const GRID_MAX_TILES = 80_000;

/**
 * Atlas-backed thumb — renders a single (slot, sub) sprite from the
 * already-loaded IsoRenderer atlas image. Zero HTTP, ~50 microseconds
 * per render (one ctx.drawImage call). Used for in-tileset entries
 * where the renderer's cellMap has the data.
 *
 * Falls back to nothing (placeholder) when the (slot, sub) isn't in
 * the cellMap. The caller can use StiFrameImage as a fallback for
 * arbitrary slot/sub the renderer doesn't know about (e.g., a slot
 * the user is typing into the edit form).
 */
function AtlasFrameThumb({
  renderer, slot, sub, size = 48, className,
}: {
  renderer: IsoRenderer | null;
  slot: number;
  sub: number;
  size?: number;
  className?: string;
}) {
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const [missing, setMissing] = useState(false);
  useEffect(() => {
    if (!renderer || !canvasRef.current) return;
    const ctx = canvasRef.current.getContext("2d");
    if (!ctx) return;
    const ok = renderer.drawCellInto(ctx, slot, sub, size, size);
    setMissing(!ok);
  }, [renderer, slot, sub, size]);
  if (missing) {
    return (
      <span
        className={`inline-flex items-center justify-center rounded bg-gray-800 text-[8px] text-gray-500 ${className ?? ""}`}
        style={{ width: size, height: size }}
        title={`slot ${slot} sub ${sub} — not in atlas`}
      >?</span>
    );
  }
  return (
    <canvas
      ref={canvasRef}
      width={size}
      height={size}
      className={`inline-block bg-gray-900 ${className ?? ""}`}
      style={{
        imageRendering: "pixelated",
        width: size,
        height: size,
      }}
    />
  );
}

/**
 * Render one STI sub-frame as an inline <img>. Handles the
 * authedFetch → blob → object URL → revoke lifecycle so the parent
 * tree doesn't have to. Re-fetches when (xmlPath, tileset, slot, sub)
 * change. Used in the edit form (live preview of the proposed
 * slot/sub which may not yet be in the loaded atlas).
 *
 * For inspector entry previews — where the slot/sub IS in the atlas
 * — prefer AtlasFrameThumb above. Zero HTTP, instant render.
 */
function StiFrameImage({
  xmlPath, tileset, slot, sub, maxSize = 48, className,
}: {
  xmlPath: string;
  tileset: number;
  slot: number;
  sub: number;
  maxSize?: number;
  className?: string;
}) {
  const [url, setUrl] = useState<string | null>(null);
  const [err, setErr] = useState(false);
  useEffect(() => {
    if (!xmlPath) { setUrl(null); return; }
    let cancelled = false;
    let created: string | null = null;
    setErr(false);
    fetchStiFrameBlobUrl(xmlPath, tileset, slot, sub)
      .then((u) => {
        if (cancelled) { URL.revokeObjectURL(u); return; }
        created = u;
        setUrl(u);
      })
      .catch(() => { if (!cancelled) setErr(true); });
    return () => {
      cancelled = true;
      if (created) URL.revokeObjectURL(created);
    };
  }, [xmlPath, tileset, slot, sub]);
  if (err) {
    return (
      <span
        className={`inline-block text-[8px] text-red-400 ${className ?? ""}`}
        style={{ width: maxSize, height: maxSize, lineHeight: `${maxSize}px`, textAlign: "center" }}
        title={`No frame for slot ${slot} sub ${sub}`}
      >?</span>
    );
  }
  if (!url) {
    return (
      <span
        className={`inline-block animate-pulse rounded bg-gray-800 ${className ?? ""}`}
        style={{ width: maxSize, height: maxSize }}
      />
    );
  }
  return (
    <img
      src={url}
      alt={`slot ${slot} sub ${sub}`}
      className={`inline-block bg-gray-900 ${className ?? ""}`}
      style={{
        maxWidth: maxSize, maxHeight: maxSize,
        imageRendering: "pixelated",
        objectFit: "contain",
      }}
    />
  );
}

// Default-export wrapper that mounts the log provider. The inner
// `MapForgeSectorInner` is what owns all the state + effects; that
// keeps `useMapForgeLog()` callable anywhere in the tree without
// having to wire props through.
export default function MapForgeSector() {
  return (
    <MapForgeLogProvider>
      <MapForgeSectorInner />
    </MapForgeLogProvider>
  );
}

function MapForgeSectorInner() {
  const log = useMapForgeLog();
  // User-customizable editor settings (hotkeys, default tool/brush).
  // Loaded once at mount; updated by the settings modal. Persists to
  // localStorage via lib/mapforgeSettings.
  const [settings, setSettings] = useState<MapForgeSettings>(() => loadSettings());
  const [settingsOpen, setSettingsOpen] = useState(false);
  // Vim-style command console — toggled by `:`. State lives at the
  // top of the component so the keybinding effect + command handlers
  // can read it. See MapForgeConsole.tsx + task #114.
  const [consoleOpen, setConsoleOpen] = useState(false);
  // `?` cheatsheet overlay (MapForgeHelpOverlay).
  const [showHelp, setShowHelp] = useState(false);
  const [radarBusy, setRadarBusy] = useState(false);
  const [params, setParams] = useSearchParams();
  const datPath = params.get("dat") ?? "";
  const xmlPath = params.get("xml") ?? "";
  const tilesetParam = params.get("tileset");
  const roomParam = params.get("room");

  const info = useQuery({
    queryKey: ["mapforge", "sector", "info", datPath],
    queryFn: () => getSectorInfo(datPath),
    enabled: !!datPath,
  });

  // Tileset list (index + NAME) for the command bar's tileset dropdown —
  // the same enumerator the Tileset Editor uses, so the user picks
  // "#70 — FALLOUT VAULT" instead of typing a bare number.
  const tilesetList = useQuery({
    queryKey: ["mapforge", "tilesets", xmlPath],
    queryFn: () => listTilesets(xmlPath),
    enabled: !!xmlPath,
    staleTime: 60 * 1000,
  });

  // parseInt returns NaN on non-numeric input (e.g. ?room=abc, a stale
  // bookmark from before room IDs were normalized). NaN downstream
  // makes `parsed.rooms[g] === selectedRoom` always false (NaN !== NaN)
  // and `info.rooms.find((r) => r.room_id === NaN)` returns undefined,
  // which then crashes `room.tiles.size` reads in the zoom modal.
  // Normalize NaN to null. Bug-review finding D5.
  const selectedRoom = (() => {
    if (roomParam === null) return null;
    const n = parseInt(roomParam, 10);
    return Number.isFinite(n) ? n : null;
  })();

  // ─── Editing session ────────────────────────────────────────────────
  // One session per (dat, xml, tileset) tuple. Opened when the page
  // loads + closed on unmount or when those inputs change. All edits
  // / renders / inspects go through this session so the parsed dict
  // is held in RAM and never re-parsed per operation.
  const [session, setSession] = useState<SessionInfo | null>(null);
  // Bumped on `sidecar:restarted`. Sessions live in the sidecar's
  // in-memory dict; a restart wipes them all, leaving the frontend
  // holding a stale session_id. Without this counter the open-session
  // effect's deps don't change on restart and the user keeps hitting
  // SESSION_NOT_FOUND from generators / saves / applyEdits forever.
  // User-reported: generator failed with SESSION_NOT_FOUND after a
  // rebuild-induced sidecar restart.
  const [sessionRestartEpoch, setSessionRestartEpoch] = useState(0);

  const tileset = useMemo(() => {
    if (tilesetParam !== null) {
      // Same NaN-guard treatment as selectedRoom — bug-review D5.
      const parsed = parseInt(tilesetParam, 10);
      if (Number.isFinite(parsed)) return parsed;
    }
    // Prefer the session's tileset (arrives first now that we open the
    // session in parallel) over info.data's. Both should agree.
    return session?.tileset ?? info.data?.tileset_in_header ?? 0;
  }, [tilesetParam, session, info.data]);
  const [sessionError, setSessionError] = useState<string | null>(null);
  const isSlfBundled = datPath.startsWith("slf://");

  // Surface the SLF-bundled / read-only status through the log instead
  // of as a persistent banner in the Tile Inspector. Every other
  // MapForge status message routes through the log; the inspector
  // banner was an inconsistency that ate inspector space on every
  // load. The "Extract to loose" floating prompt at the top-left of
  // the canvas (with the destination preview + extract button) is the
  // actionable surface; the log entry is just the explanatory note.
  useEffect(() => {
    if (!isSlfBundled || !log) return;
    log.append({
      severity: "warn",
      message:
        `This sector lives inside an SLF archive — editing is disabled. ` +
        `Use the "Extract to loose" button at the top-left to drop a loose ` +
        `copy into Data-1.13/Maps/ and reopen.`,
    });
    // Re-fire when the user opens a different SLF-bundled sector
    // (datPath changes). Deliberately omit `log` from deps — the log
    // instance is stable across renders and including it would cause a
    // re-fire on every render of the parent.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [isSlfBundled, datPath]);

  useEffect(() => {
    // Open the session AS SOON AS we have datPath + xmlPath — don't
    // wait for sector info to land. Both calls parse the .dat on the
    // backend, but they run in parallel so the user only waits for
    // one parse, not two stacked. tileset=0 tells the backend "auto-
    // detect from the .dat header", so we don't need to know the
    // tileset up front either.
    //
    // SLF sectors open in read-only mode. The backend's open_session
    // handles the SLF URI itself (extracts to a temp cache); the
    // returned session has `read_only: true`, which gates the editing
    // UI here.
    setSession(null);
    setSessionError(null);
    if (!datPath || !xmlPath) return;
    let cancelled = false;
    let openedId: string | null = null;
    const initialTileset = tilesetParam !== null ? parseInt(tilesetParam, 10) : 0;
    openSession(datPath, xmlPath, initialTileset)
      .then((s) => {
        if (cancelled) {
          closeSession(s.session_id).catch(() => {});
          return;
        }
        openedId = s.session_id;
        setSession(s);
        // Fire-and-forget preload of the palette sprite sheet. The
        // sheet is what the Asset Browser needs, and its cold bake is
        // the dominant wait when the user opens "Browse assets" for the
        // first time on a tileset. prefetchPaletteSheet warms BOTH the
        // sidecar disk cache AND the browser-side blob URL (shared,
        // app-lifetime cache), so the viewer's open is a true cache hit
        // with zero network — not just a warm disk cache the browser
        // still has to download from. Errors are swallowed — a failed
        // preload just falls back to the on-demand bake.
        // User feedback: "Can you make it load faster and/or preload?"
        prefetchPaletteSheet(xmlPath, s.tileset).catch(() => {});
      })
      .catch((err) => {
        if (cancelled) return;
        setSessionError(err instanceof Error ? err.message : String(err));
      });
    return () => {
      cancelled = true;
      if (openedId) closeSession(openedId).catch(() => {});
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps -- sessionRestartEpoch
    // is intentionally in deps so a sidecar restart re-fires the open.
  }, [datPath, xmlPath, tilesetParam, sessionRestartEpoch]);

  // Preload the palette sprite sheet whenever the RESOLVED tileset
  // changes — not just on session open. Switching tilesets (via the
  // SectorControls dropdown / ?tileset=) previously left the Asset
  // Browser cold for the new tileset, so the first open after a switch
  // paid the full bake again. Keyed on the memoized `tileset` so it
  // tracks both the URL-param path and the session-derived fallback.
  // Idempotent with the session-open preload (warm disk cache → instant
  // cache hit), so the overlap on first load is harmless.
  useEffect(() => {
    if (!xmlPath || tileset < 0) return;
    prefetchPaletteSheet(xmlPath, tileset).catch(() => {});
  }, [xmlPath, tileset]);

  // Listen for sidecar restarts. The shell emits this when its
  // watchdog respawns mercwizard_core.exe (port flip) — App.tsx
  // already invalidates React Query, but our session state is plain
  // useState. Bump the epoch so the open-session effect re-fires
  // with a fresh session_id.
  useEffect(() => {
    let cleanup: (() => void) | undefined;
    let cancelled = false;
    import("@tauri-apps/api/event").then(({ listen }) => {
      if (cancelled) return;
      listen("sidecar:restarted", () => {
        log?.append({
          severity: "warn",
          message: "Sidecar restarted — re-opening sector session.",
        });
        // Drop the stale session ref immediately so downstream calls
        // can short-circuit instead of hitting 404s before the new
        // open completes.
        setSession(null);
        setSessionRestartEpoch((n) => n + 1);
      }).then((unlisten) => {
        if (cancelled) unlisten();
        else cleanup = unlisten;
      });
    }).catch(() => {});
    return () => {
      cancelled = true;
      if (cleanup) cleanup();
    };
    // log is from a context; safe to omit. We never want this listener
    // to re-attach on rerender.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // ─── Dock plumbing + recent-brush history ──────────────────────────
  // Live DockviewApi — lets the command bar + rail drive the dock
  // imperatively (focus/re-open panels, reset layout). Null before the
  // dock is ready.
  const dockApiRef = useRef<DockviewApi | null>(null);
  // Open panel ids, reported by MapForgeDock — drives the Panels▾ menu
  // (re-open closed panels).
  const [dockOpenIds, setDockOpenIds] = useState<string[]>([]);
  // "Browse assets" handler: focus (and re-open if the user closed it)
  // the docked Browse Assets panel.
  const onBrowseAssets = useCallback(() => {
    const api = dockApiRef.current;
    if (!api) return;
    const existing = api.getPanel("assets");
    if (existing) {
      existing.api.setActive();
    } else {
      // The user closed the panel's tab — re-add it beside Palette.
      api.addPanel({
        id: "assets",
        component: "default",
        title: PANEL_TITLE.assets,
        position: api.getPanel("palette")
          ? { referencePanel: "palette", direction: "within" }
          : undefined,
      });
    }
  }, []);
  // Hotkey ("A") variant — same as the button (focus the docked panel).
  const toggleBrowseAssets = onBrowseAssets;
  // Ref to the latest handler so the global keydown effect can invoke
  // the current version without re-binding.
  const toggleBrowseAssetsRef = useRef(toggleBrowseAssets);
  toggleBrowseAssetsRef.current = toggleBrowseAssets;
  // "Validate" opener: focus the docked Validation tab (pre-created as
  // an inactive tab of the inspector group in the default layout), or
  // re-add it into that group if the user closed it.
  const openValidatePanel = useCallback(() => {
    const api = dockApiRef.current;
    if (!api) return;
    const existing = api.getPanel("validate");
    if (existing) {
      existing.api.setActive();
    } else {
      api.addPanel({
        id: "validate",
        component: "default",
        title: PANEL_TITLE.validate,
        position: api.getPanel("inspector")
          ? { referencePanel: "inspector", direction: "within" }
          : undefined,
      });
    }
  }, []);
  // Full-screen / focus mode — overlay the editor over the whole window
  // and hide the page header chrome. Transient (not persisted), so a
  // reload always starts un-focused.
  const [focusMode, setFocusMode] = useState(false);
  // Recent picks + Favorites — persisted per (xmlPath, tileset), the same
  // bucket model as "Just added" (recentAdditions). Recent was RAM-only
  // before (lost on reload); Favorites is new and addressable by number
  // keys 1-9.
  const [recentBrushes, setRecentBrushes] =
    usePersistentBrushBucket(RECENT_BRUSHES_KEY, xmlPath, tileset);
  const [favorites, setFavorites] =
    usePersistentBrushBucket(FAVORITE_BRUSHES_KEY, xmlPath, tileset);
  const toggleFavorite = useCallback((b: ActiveBrush) => {
    setFavorites((prev) =>
      prev.some((f) => sameBrush(f, b))
        ? prev.filter((f) => !sameBrush(f, b))
        // Newest pin wins when the 1-9 bar is full (oldest drops off).
        : [...prev, b].slice(-FAVORITE_BRUSHES_CAP),
    );
  }, [setFavorites]);
  // Ref mirror so the global keydown listener reads favorites without
  // re-binding on every pin/unpin (matches the toggleBrowseAssetsRef pattern).
  const favoritesRef = useRef<ActiveBrush[]>([]);
  useEffect(() => { favoritesRef.current = favorites; }, [favorites]);

  // ─── Recent additions panel (user request) ──────────────
  // "Just added" is a parallel surface to "Recent picks": when the user
  // imports an STI from the library into the active tileset, push it
  // here so the rail can offer one-click access to inspect subframes,
  // inject more subs, or undo the add. Persists in localStorage keyed
  // by (xml, tileset) so a page reload doesn't wipe the queue. Cap at
  // 24 entries — already more than the rail can show without scrolling,
  // and the queue rolls over LRU-style.
  const RECENT_ADDS_STORAGE_KEY = "mapforge.recentAdditions.v1";
  const recentAdditionsKey = useMemo(
    // xmlPath might be empty before session-open — fall back to a
    // bucket scoped to the tileset only so the panel still hydrates.
    () => `${xmlPath ?? "_"}::${tileset}`,
    [xmlPath, tileset],
  );
  const [recentAdditions, setRecentAdditions] = useState<RecentAddition[]>(
    () => {
      // Lazy initializer runs once on mount with the initial key.
      // The effect below re-loads when the key changes mid-session.
      try {
        const raw = localStorage.getItem(RECENT_ADDS_STORAGE_KEY);
        if (!raw) return [];
        const parsed = JSON.parse(raw) as Record<string, RecentAddition[]>;
        return parsed[recentAdditionsKey] ?? [];
      } catch {
        return [];
      }
    },
  );
  useEffect(() => {
    // Rehydrate when the user switches install/tileset within a session.
    try {
      const raw = localStorage.getItem(RECENT_ADDS_STORAGE_KEY);
      const parsed = raw
        ? (JSON.parse(raw) as Record<string, RecentAddition[]>)
        : {};
      setRecentAdditions(parsed[recentAdditionsKey] ?? []);
    } catch {
      setRecentAdditions([]);
    }
  }, [recentAdditionsKey]);
  useEffect(() => {
    // Persist on every change. Rewrite the whole map — at ~10KB even
    // with 50 buckets this is well under the localStorage 5MB ceiling
    // and avoids the read-modify-write race a per-bucket scheme has.
    try {
      const raw = localStorage.getItem(RECENT_ADDS_STORAGE_KEY);
      const parsed = raw
        ? (JSON.parse(raw) as Record<string, RecentAddition[]>)
        : {};
      parsed[recentAdditionsKey] = recentAdditions;
      localStorage.setItem(RECENT_ADDS_STORAGE_KEY, JSON.stringify(parsed));
    } catch {
      // Quota or JSON parse — non-fatal; user loses persistence only.
    }
  }, [recentAdditions, recentAdditionsKey]);
  // recentAdditions is now read-only here — new adds happen in the
  // Tileset Editor (which writes to the same localStorage key). The
  // rail still shows past additions for navigation purposes.

  // Library + inject UIs moved to /tileset-editor — see
  // docs/TILESET_EDITOR_SPLIT.md. The rail's "Just added" card chip
  // now navigates to the Tileset Editor rather than mounting a modal
  // here. `navigate` is set up below in the navigation callback.
  const navigate = useNavigate();

  // ─── Tool + active brush (Phase 2B/C) ──────────────────────────────
  // Initial tool comes from user settings. Subsequent changes (e.g.
  // via the hotkey dispatcher or the toolbar selector) override.
  const [tool, setTool] = useState<Tool>(() => loadSettings().defaultTool);
  const [activeBrush, setActiveBrush] = useState<ActiveBrush | null>(null);
  // Track recent picks for the rail's quick-switch grid. On every
  // activeBrush change (non-null), prepend to the list and dedupe by
  // (slot, sub). Cap at RECENT_BRUSHES_CAP, rolling over LRU-style.
  // Eyedropper picks count too — they go through setActiveBrush (via
  // armBrush) like any other pick. The list is persisted per tileset by
  // usePersistentBrushBucket.
  useEffect(() => {
    if (!activeBrush) return;
    const brush = activeBrush;
    setRecentBrushes((prev) =>
      [brush, ...prev.filter((b) => !sameBrush(b, brush))].slice(0, RECENT_BRUSHES_CAP)
    );
  }, [activeBrush]);
  // Arming a brush from ANY surface (palette, recent rail, just-added,
  // eyedropper, inspector) selects the pencil too — a pick means the user
  // wants to paint with it. Centralized so no pick site can forget: the
  // palette + rail picks used to set the brush but leave the tool on
  // Inspect, so the user's first click silently did nothing.
  const armBrush = useCallback((b: ActiveBrush | null) => {
    setActiveBrush(b);
    if (b) setTool("pencil");
  }, []);
  // Paint stroke buffer — accumulates tiles during a drag so we don't
  // re-apply the same edit twice. Each tile in the stroke fires its
  // own applyEdits round-trip in the background; the local renderer
  // shows the result instantly without waiting.
  const strokeRef = useRef<Set<string> | null>(null);
  // ─── Shape tool state ──────────────────────────────────────────────
  // Which shape a drag produces; anchor = where the drag started; cursor
  // = the live end-point (updated on mousemove to drive the preview).
  // Both null when no shape drag is in progress.
  const [shapeKind, setShapeKind] = useState<ShapeKind>("rect-fill");
  const [shapeAnchor, setShapeAnchor] = useState<Tile | null>(null);
  const [shapeCursor, setShapeCursor] = useState<Tile | null>(null);
  // ─── Select / region copy-paste state (A5 Phase 4) ────────────────
  // selectAnchor/selectCursor mirror shapeAnchor/shapeCursor: anchor =
  // where the marquee drag started, cursor = the live end-point. Both
  // null when no select drag is in progress. selectRect is the COMMITTED
  // rectangle (set on mouseup) — it persists so the Copy button has a
  // region to slice. clipboard holds the last copied region (cleared on
  // sector/tileset/session change). pasteMode = modal "click the map to
  // drop the clipboard"; the next canvas click places the paste.
  const [selectAnchor, setSelectAnchor] = useState<Tile | null>(null);
  const [selectCursor, setSelectCursor] = useState<Tile | null>(null);
  const [selectRect, setSelectRect] = useState<{ a: Tile; b: Tile } | null>(null);
  const [clipboard, setClipboard] = useState<ClipboardRegion | null>(null);
  const [pasteMode, setPasteMode] = useState(false);
  // Synchronous re-entrancy latch for paste (see doPaste). A ref, not
  // state, because it must read/write within a single event tick.
  const pasteBusyRef = useRef(false);
  // Clipboard + selection lifecycle. The route never remounts on a
  // `?tileset=` / `?dat=` change (same component, new search params) and
  // a session id is reused across the sidecar restart epoch, so a stale
  // clipboard would otherwise survive into a different sector/tileset.
  // Key on the RAW `tilesetParam` — NOT the resolved `tileset` memo,
  // which flickers 0→N while the session opens and would spuriously
  // clear a just-copied region.
  useEffect(() => {
    setClipboard(null);
    setSelectRect(null);
    setSelectAnchor(null);
    setSelectCursor(null);
    setPasteMode(false);
  }, [datPath, tilesetParam, sessionRestartEpoch]);
  // Leaving the select tool disarms paste mode, so switching to pencil
  // and back doesn't leave a primed paste that fires on the next click.
  useEffect(() => {
    if (tool !== "select") setPasteMode(false);
  }, [tool]);
  // Esc cancels an armed paste (mirrors the rect-corner picker's Esc).
  useEffect(() => {
    if (!pasteMode) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") { e.preventDefault(); setPasteMode(false); }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [pasteMode]);
  // Room id written by the "Mark region as room" shape. The toolbar lets
  // the user retarget an existing room or pick 0 to clear membership.
  const [roomId, setRoomId] = useState(1);
  // Per-generator-stream op counter used to throttle setRenderEpoch
  // bumps during a long stream. Mirrors each op into the renderer, but
  // only triggers a React repaint every N ops — without throttling
  // either we choke on 25k repaints (bump per op) or freeze the canvas
  // for 10 s (no bump at all).
  const genPanelOpCount = useRef(0);
  const consoleOpCount = useRef(0);

  // ─── Phase 3: client-side renderer state ──────────────────────────
  // The renderer holds the atlas, the darken atlas, and a local copy
  // of the parsed sector. Edits mutate `renderer.parsed` directly so
  // re-renders are instant; the backend session is still authoritative
  // for save, and applyEdits round-trips mirror local mutations.
  const [renderer, setRenderer] = useState<IsoRenderer | null>(null);
  const [renderMeta, setRenderMeta] = useState<RenderMeta | null>(null);
  const [renderError, setRenderError] = useState<string | null>(null);
  const [rendererLoading, setRendererLoading] = useState(false);
  // Phase + per-phase percent for the load progress bar. `null` when
  // not loading or when the bar has finished. The whole-load percent
  // is derived from PHASE_WEIGHTS in the UI helper below.
  const [loadPhase, setLoadPhase] = useState<ProgressPhase | null>(null);
  const [phasePct, setPhasePct] = useState(0);
  // Per-phase completion accumulator: how many percent of the OVERALL
  // bar has finished by the time we entered `loadPhase`. Lets the bar
  // monotonically increase across phases instead of resetting to 0%
  // at every phase boundary.
  const [phaseFloor, setPhaseFloor] = useState(0);
  // Tracks whether the canvas has actually painted pixels since the
  // session opened. The progress bar stays up through the React-mount
  // and first-paint window (otherwise the bar hits 100% while the
  // viewport is still empty — "progress completed before render").
  const [firstPaintDone, setFirstPaintDone] = useState(false);
  // Bumped when the user imports an STI from the library so the
  // existing renderer's atlas + cellMap can pick up the new cells.
  // A full session reload would also drop the undo stack and
  // refetch parsed (wasteful); replaceAtlas keeps everything else.
  const [atlasReloadEpoch, setAtlasReloadEpoch] = useState(0);
  const [atlasReloading, setAtlasReloading] = useState(false);
  // Tracks whether the currently-loaded atlas is the COMPLETE tileset
  // atlas vs a sector-specific PARTIAL one. Set to false when the
  // session-open effect lands a partial atlas; set to true once the
  // background swap-to-complete effect finishes. While false, the
  // JSD-dependent UI surfaces (multi-tile stamp recipes, "View JSD"
  // button in the inspector) degrade gracefully — they're back as
  // soon as the complete atlas swaps in.
  const [atlasComplete, setAtlasComplete] = useState(true);

  // Diagnostic overlay: when enabled, the last click's raw coords +
  // resolved tile + expected diamond bounds get printed to a HUD AND
  // dumped to console.log. Use this to confirm whether the
  // click→tile inversion is doing what we think when something looks
  // mis-aligned on screen. Disabled by default — costs nothing to
  // leave in place once the dust settles.
  const [debugClickHud, setDebugClickHud] = useState(false);
  const [lastClickDebug, setLastClickDebug] = useState<{
    clientX: number; clientY: number;
    rectLeft: number; rectTop: number; rectW: number; rectH: number;
    canvasW: number; canvasH: number;
    px: number; py: number;
    tile: { x: number; y: number } | null;
    southApex?: { x: number; y: number };
    diamondCenter?: { x: number; y: number };
  } | null>(null);
  // Inspector pin + hover
  const [pinned, setPinned] = useState<{ x: number; y: number } | null>(null);
  const [hovered, setHovered] = useState<{ x: number; y: number } | null>(null);
  // Live Shift tracking. Drives the multi-tile stamp preview: hover
  // a multi-tile brush over the canvas → outline diamonds appear on
  // every footprint tile. Holding Shift inverts stamp/manual mode for
  // the next paint, so we hide the preview when Shift flips us into
  // manual mode (no stamp will happen). Keeps the preview honest.
  const [shiftHeld, setShiftHeld] = useState(false);
  useEffect(() => {
    const down = (e: KeyboardEvent) => { if (e.key === "Shift") setShiftHeld(true); };
    const up = (e: KeyboardEvent) => { if (e.key === "Shift") setShiftHeld(false); };
    window.addEventListener("keydown", down);
    window.addEventListener("keyup", up);
    // If the window loses focus while Shift is held, the keyup never
    // fires. Reset on blur to avoid a stuck Shift state.
    const blur = () => setShiftHeld(false);
    window.addEventListener("blur", blur);
    return () => {
      window.removeEventListener("keydown", down);
      window.removeEventListener("keyup", up);
      window.removeEventListener("blur", blur);
    };
  }, []);
  // Overlay toggles
  // Both overlays default OFF — they clutter the render for users who
  // just want to see the map. The toolbar buttons + their hotkeys
  // (G for grid; no hotkey for room labels yet) make them one click
  // away when actually needed (e.g. precise tile painting, room-id
  // debugging).
  const [showGrid, setShowGrid] = useState(false);
  const [showRoomLabels, setShowRoomLabels] = useState(false);
  // Per-layer visibility. The renderer skips the layer in its draw
  // loop; no HTTP fetch needed.
  const [hiddenLayers, setHiddenLayers] = useState<Set<LayerName>>(new Set());
  // Optional override of which layer the pencil tool paints into.
  // null = use the brush's category-implied default (CATEGORY_TO_LAYER).
  const [paintLayer, setPaintLayer] = useState<LayerName | null>(null);
  // Brush radius in tiles. 1 = single tile (default), 2 = the clicked
  // tile + 4 neighbors (diamond of side 3), etc. The brush footprint
  // is Manhattan-distance so it stays diamond-shaped in iso space —
  // a Euclidean radius would project to an ellipse, weird to aim with.
  // Initial value comes from user settings.
  const [brushRadius, setBrushRadius] = useState(
    () => loadSettings().defaultBrushRadius);
  // Height brush (P5): mode + value. "raise"/"lower" step the touched
  // tile's current height by `heightValue` (clamped 0..255); "set" writes
  // `heightValue` absolutely. Both renderers ignore height for Z, so the
  // height OVERLAY (numbers/tint, shown only while this tool is active) is
  // what makes the brush observable.
  const [heightMode, setHeightMode] = useState<"raise" | "lower" | "set">("raise");
  const [heightValue, setHeightValue] = useState(1);
  // Bumped after every undo/redo so React re-renders the toolbar
  // button label ("Undo: Paint floor (12 tiles)" → "Undo: empty").
  const [undoDepth, setUndoDepth] = useState(0);
  const [redoDepth, setRedoDepth] = useState(0);
  // Stack depth at the last save — kept for the Save button's
  // "N strokes" label only; dirty itself is generation-based (below).
  const [savedAtDepth, setSavedAtDepth] = useState(0);
  // Edit GENERATION tracking (monotonic renderer counter, bumped on
  // every committed stroke / undo / redo / rollback discard). Depth
  // comparison lied: save→undo→new-stroke lands back on the saved
  // depth ("Saved" while two strokes differ from disk), and the
  // 100-entry stack cap pins depth forever. Generation can collide
  // neither way — worst case it reads dirty when undo returned the
  // content to the exact save point, which errs safe.
  const [histGen, setHistGen] = useState(0);
  const [savedAtGen, setSavedAtGen] = useState(0);
  // Frontend-computed dirty flag for the Save button.
  const localDirty = histGen !== savedAtGen;
  // Window-close / refresh guard while dirty (in-app nav links already
  // confirm via their own handlers; this covers the Tauri window X and
  // F5, which previously discarded unsaved edits silently).
  useUnsavedGuard(localDirty);
  // Pending tileset switch — set when the user clicks a tileset
  // option while the sector has unsaved changes. Holds the requested
  // value until the confirm modal resolves; null when no prompt
  // is active. Without this, the URL-param change would re-open the
  // session and discard every unsaved edit silently.
  const [pendingTilesetSwitch, setPendingTilesetSwitch] = useState<number | null>(null);
  // Canvas corner-picker for the rectangle generator. When set, the
  // canvas onClick captures (x, y) tiles instead of inspect/paint.
  // Stage 0 = waiting for first corner; stage 1 = waiting for second.
  // onComplete fires with the two corners; onCancel fires on ESC.
  const [pickingRect, setPickingRect] = useState<{
    stage: 0 | 1;
    corner1?: { x: number; y: number };
    onComplete: (c1: { x: number; y: number }, c2: { x: number; y: number }) => void;
    onCancel: () => void;
  } | null>(null);
  // Region-pick event plumbing: the anchoring mousedown also fires a
  // click (swallow it so it can't double as the second corner), and a
  // drag-complete on mouseup is followed by a click (swallow that too
  // so it can't pin the inspector).
  const pickJustAnchoredRef = useRef(false);
  const pickSuppressClickRef = useRef(false);
  // ─── StarCraft-style building placement mode ────────────────────────
  // Armed by the Generate panel's building-library flow. While set, the
  // canvas shows the building's w×h FOOTPRINT outline at the cursor (the
  // hovered tile = footprint top-left) PLUS — when `region` carries the
  // building's verbatim tiles — a REAL SPRITE GHOST of the building on a
  // dedicated overlay canvas stacked above the grid SVG (see the
  // placement-ghost effect below). LEFT CLICK calls run(x, y) — the
  // panel stamps the building there via the pasteEdits batch path — and
  // STAYS armed so repeated clicks stamp more buildings (room ids are
  // renumbered per stamp). ESC or a tool change exits. `label` feeds the
  // canvas banner. A Promise-returning run() gates the ghost + further
  // clicks while a stamp is in flight.
  const [placingBuilding, setPlacingBuilding] = useState<{
    w: number;
    h: number;
    label: string;
    region?: ClipboardRegion;
    run: (x: number, y: number) => void | Promise<void>;
  } | null>(null);
  // True while a placement stamp's backend round-trip is in flight —
  // hides the sprite ghost overlay (so the freshly-stamped tiles are
  // visible, not double-drawn under it) and blocks further stamp clicks.
  const [placementStampBusy, setPlacementStampBusy] = useState(false);
  // Exit placement mode when the user switches tools, sectors, tilesets
  // or the sidecar restarts — a stale run() closure must never fire
  // against a different session.
  useEffect(() => {
    setPlacingBuilding(null);
  }, [tool, datPath, tilesetParam, sessionRestartEpoch]);
  // ESC exits placement mode (mirrors the region picker's ESC effect).
  useEffect(() => {
    if (!placingBuilding) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        e.preventDefault();
        setPlacingBuilding(null);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [placingBuilding]);
  // Bumped on every local mutation that should re-paint the canvas.
  // The render effect depends on this so React schedules a paint after
  // each edit. (Mutating `renderer.parsed` doesn't itself trigger React.)
  const [renderEpoch, setRenderEpoch] = useState(0);
  // ─── Generator ghost preview (UX Phase 2) ───────────────────────────
  // A dry-run's ops applied to the LOCAL renderer only — completely
  // outside the undo/dirty machinery. First-touch pre-state per axis is
  // kept here and restored on clear, so the backend session never sees
  // the ghost and Ctrl+Z history is untouched. While a ghost is live
  // the canvas paint tools are blocked (banner below) so user edits
  // can't interleave with ghost state.
  const ghostSnapsRef = useRef<{
    layers: Map<string, { x: number; y: number; layer: LayerName; entries: number[][] }>;
    rooms: Map<string, { x: number; y: number; roomId: number }>;
    heights: Map<string, { x: number; y: number; height: number }>;
  } | null>(null);
  const [ghostActive, setGhostActive] = useState(false);
  // True when the live ghost contains set_height ops — heights are
  // invisible in the iso render, so the height overlay force-shows
  // while such a ghost is up (otherwise a cliff preview LOOKS like
  // the generator did nothing — user-reported).
  const [ghostHasHeights, setGhostHasHeights] = useState(false);

  const clearGhost = useCallback(() => {
    const g = ghostSnapsRef.current;
    ghostSnapsRef.current = null;
    if (g && renderer) {
      for (const s of g.layers.values()) {
        renderer.applyLocalEdit({
          x: s.x, y: s.y, op: "set_entries", layer: s.layer, entries: s.entries,
        });
      }
      for (const r of g.rooms.values()) {
        renderer.applyLocalEdit({ x: r.x, y: r.y, op: "set_room", roomId: r.roomId });
      }
      for (const h of g.heights.values()) {
        renderer.applyLocalEdit({ x: h.x, y: h.y, op: "set_height", height: h.height });
      }
      setRenderEpoch((e) => e + 1);
    }
    setGhostActive(false);
    setGhostHasHeights(false);
  }, [renderer]);

  const applyGhostOps = useCallback((ops: unknown[]) => {
    if (!renderer) return;
    clearGhost();
    const parsed = renderer.getParsed();
    if (!parsed) return;
    const g: NonNullable<typeof ghostSnapsRef.current> = {
      layers: new Map(), rooms: new Map(), heights: new Map(),
    };
    for (const op of ops) {
      if (op === null || typeof op !== "object") continue;
      const o = op as Record<string, unknown>;
      const opName = o.op as string;
      const x = o.x as number;
      const y = o.y as number;
      const layer = o.layer as LayerName | undefined;
      const gn = y * parsed.cols + x;
      if (opName === "set_room") {
        const k = `${x},${y}`;
        if (!g.rooms.has(k)) {
          g.rooms.set(k, { x, y, roomId: parsed.rooms[gn] ?? 0 });
        }
      } else if (opName === "set_height") {
        const k = `${x},${y}`;
        if (!g.heights.has(k)) {
          g.heights.set(k, { x, y, height: parsed.heights[gn] ?? 0 });
        }
      } else if (layer) {
        const k = `${x},${y},${layer}`;
        if (!g.layers.has(k)) {
          const cur = parsed[layer][gn] ?? [];
          g.layers.set(k, {
            x, y, layer,
            entries: cur.map((e) => [e[0] as number, e[1] as number]),
          });
        }
      }
      renderer.applyLocalEdit({
        x, y,
        op: opName as "place" | "add" | "remove" | "replace" | "set_entries"
          | "set_room" | "set_height",
        layer,
        slot: o.slot as number | undefined,
        sub: o.sub as number | undefined,
        entryIndex: o.entry_index as number | undefined,
        entries: o.entries as number[][] | undefined,
        roomId: o.room_id as number | undefined,
        height: o.height as number | undefined,
      });
    }
    ghostSnapsRef.current = g;
    setGhostActive(true);
    setGhostHasHeights(g.heights.size > 0);
    setRenderEpoch((e) => e + 1);
  }, [renderer, clearGhost]);

  // ─── Placement sprite ghost (canon building library) ────────────────
  // While placement mode carries a verbatim building `region`, show its
  // REAL sprites at the hovered anchor on a DEDICATED OVERLAY CANVAS
  // stacked ABOVE the grid SVG (the grid must render BELOW the building
  // ghost — owner feedback; the old path applied ghost ops into the MAIN
  // canvas via the ghost engine, so the grid mesh + footprint tint drew
  // over the sprites and washed them out).
  //
  // The building is rendered ONCE per armed region into a tight
  // offscreen canvas (IsoRenderer.renderRegionToCanvas — same cell
  // lookup / offset math / draw order as the main render, ~70% alpha);
  // per hovered-tile change we only retranslate the overlay canvas via
  // CSS transform. The generator previews in the Generate panel keep
  // using the ghost engine (applyGhostOps) — this overlay is placement-
  // only, so ghost-engine snapshots can no longer interleave with the
  // stamp's local edits at all.
  const ghostCanvasRef = useRef<HTMLCanvasElement | null>(null);
  const placementGhost = useMemo(() => {
    const region = placingBuilding?.region;
    if (!region || !renderer) return null;
    return renderer.renderRegionToCanvas(region.tiles, 0.7);
    // renderEpoch: re-render after an atlas hot-swap (replaceAtlas keeps
    // the renderer identity but changes the cellMap).
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [placingBuilding, renderer, renderEpoch]);
  useEffect(() => {
    const cv = ghostCanvasRef.current;
    if (!cv) return;
    // Hidden while: no armed region, cursor off-canvas, a stamp is in
    // flight (the stamped tiles should be visible, not double-drawn),
    // or no meta yet. ESC / tool change clears placingBuilding which
    // lands here too.
    if (!placementGhost || !hovered || placementStampBusy || !renderMeta) {
      cv.style.display = "none";
      return;
    }
    if (cv.width !== placementGhost.canvas.width
        || cv.height !== placementGhost.canvas.height) {
      cv.width = placementGhost.canvas.width;
      cv.height = placementGhost.canvas.height;
    }
    const ctx = cv.getContext("2d");
    if (!ctx) return;
    ctx.clearRect(0, 0, cv.width, cv.height);
    ctx.drawImage(placementGhost.canvas, 0, 0);
    // Anchor alignment: the offscreen render's (0,0) tile must land on
    // the hovered tile — same tileToCanvasPixel math as the SVG overlay,
    // plus the region render's own bbox origin. Zoom needs no special
    // handling: the overlay canvas lives inside the same CSS-transformed
    // wrapper as the main canvas + SVG.
    const p = tileToCanvasPixel(hovered.x, hovered.y, renderMeta);
    cv.style.transform =
      `translate(${p.x + placementGhost.originX}px, `
      + `${p.y + placementGhost.originY}px)`;
    cv.style.display = "block";
  }, [placementGhost, hovered, placementStampBusy, renderMeta]);

  // Region pick for the Generate panel — drag/click two corners on the
  // canvas while the panel stays docked.
  const pickRegionForPanel = useCallback(
    (cb: (c1: { x: number; y: number }, c2: { x: number; y: number }) => void) => {
      setPickingRect({
        stage: 0,
        onComplete: (c1, c2) => cb(c1, c2),
        onCancel: () => { /* ESC — keep the previous region */ },
      });
    }, [],
  );

  // "Generate" opener: focus the docked Generate tab (pre-created as an
  // inactive tab of the inspector group in the default layout), or
  // re-add it into that group if the user closed it.
  const openGeneratePanel = useCallback(() => {
    const api = dockApiRef.current;
    if (!api) return;
    const existing = api.getPanel("generate");
    if (existing) {
      existing.api.setActive();
    } else {
      api.addPanel({
        id: "generate",
        component: "default",
        title: PANEL_TITLE.generate,
        position: api.getPanel("inspector")
          ? { referencePanel: "inspector", direction: "within" }
          : undefined,
      });
    }
  }, []);
  // requestAnimationFrame-coalesced epoch bump for the paint hot path.
  // A held-and-drag pencil-paint fires one paintBrush() per mousemove
  // (potentially 60+/sec on a fast drag); each previously called
  // setRenderEpoch synchronously, which triggered an immediate re-render
  // + full canvas repaint. Now multiple bumps in the same frame
  // coalesce into one rAF tick, so the canvas repaints at most once
  // per displayed frame regardless of mousemove rate. Non-hot callers
  // (undo, atlas reload, inspector edit) still bump synchronously so
  // their single-shot edits update immediately.
  const rafEpochScheduled = useRef(false);
  const scheduleRenderEpoch = useCallback(() => {
    if (rafEpochScheduled.current) return;
    rafEpochScheduled.current = true;
    requestAnimationFrame(() => {
      rafEpochScheduled.current = false;
      setRenderEpoch((e) => e + 1);
    });
  }, []);
  // Zoom + pan (applied to the CANVAS+SVG wrapper via CSS transform)
  const [zoom, setZoom] = useState(1);
  const [pan, setPan] = useState({ x: 0, y: 0 });
  const dragRef = useRef<{ startX: number; startY: number; panX: number; panY: number } | null>(null);

  const canvasRef = useRef<HTMLCanvasElement | null>(null);

  // ─── Demo hook (?demo=1) ────────────────────────────────────────────
  // Scripted-demo automation surface for the YouTube demo runner
  // (frontend/tools/demo/runner.mjs). Gated on &demo=1 in the URL —
  // without it nothing below renders or attaches, zero effect on
  // normal use. Exposes window.__mapforgeDemo with eased camera moves
  // (panTo/zoomTo), an on-screen caption bar, a readiness probe for
  // the runner's waits, and tileToScreen so the runner can aim real
  // mouse events at tile coordinates.
  const demoMode = params.get("demo") === "1";
  const [demoCaption, setDemoCaption] = useState<string | null>(null);
  const demoAnimRef = useRef<number | null>(null);
  // Latest-value ref so the (mount-once) hook closures never go stale.
  const demoRef = useRef({
    zoom, pan, renderMeta,
    ready: false,
  });
  demoRef.current = {
    zoom, pan, renderMeta,
    ready: !!(session && renderer && renderMeta && firstPaintDone),
  };
  useEffect(() => {
    if (!demoMode) return;
    const easeInOutCubic = (t: number) =>
      t < 0.5 ? 4 * t * t * t : 1 - Math.pow(-2 * t + 2, 3) / 2;
    /** Drive `apply(k)` with k eased 0→1 over `ms` via rAF. */
    const animate = (apply: (k: number) => void, ms: number) =>
      new Promise<void>((resolve) => {
        if (demoAnimRef.current !== null) {
          cancelAnimationFrame(demoAnimRef.current);
          demoAnimRef.current = null;
        }
        const t0 = performance.now();
        const step = (now: number) => {
          const t = Math.min(1, (now - t0) / Math.max(1, ms));
          apply(easeInOutCubic(t));
          if (t < 1) {
            demoAnimRef.current = requestAnimationFrame(step);
          } else {
            demoAnimRef.current = null;
            resolve();
          }
        };
        demoAnimRef.current = requestAnimationFrame(step);
      });
    /** Canvas-pixel CENTER of tile (x, y). */
    const tileCenterPx = (x: number, y: number, meta: RenderMeta) => {
      const p = tileToCanvasPixel(x, y, meta);
      return { x: p.x + meta.tileW / 2, y: p.y + meta.tileH / 2 };
    };
    const api = {
      /** Eased pan so tile (x, y) lands at the viewport center, at the
       * current zoom. The wrapper transform maps canvas point p to
       * viewportCenter + pan + (p − canvasCenter) · zoom, so the pan
       * that centers p is (canvasCenter − p) · zoom. */
      panTo: (x: number, y: number, ms = 1200): Promise<void> => {
        const meta = demoRef.current.renderMeta;
        if (!meta) return Promise.resolve();
        const z = demoRef.current.zoom;
        const c = tileCenterPx(x, y, meta);
        const target = {
          x: (meta.canvasW / 2 - c.x) * z,
          y: (meta.canvasH / 2 - c.y) * z,
        };
        const from = { ...demoRef.current.pan };
        return animate((k) => setPan({
          x: from.x + (target.x - from.x) * k,
          y: from.y + (target.y - from.y) * k,
        }), ms);
      },
      /** Eased zoom that keeps the current viewport center fixed —
       * pan scales proportionally with zoom (pan ∝ zoom for a fixed
       * centered point). */
      zoomTo: (z: number, ms = 900): Promise<void> => {
        const fromZ = demoRef.current.zoom;
        const fromPan = { ...demoRef.current.pan };
        const toZ = Math.max(0.25, Math.min(8, z));
        return animate((k) => {
          const nz = fromZ + (toZ - fromZ) * k;
          const s = nz / fromZ;
          setZoom(nz);
          setPan({ x: fromPan.x * s, y: fromPan.y * s });
        }, ms);
      },
      /** Show (or hide with null) the big bottom-center caption bar. */
      caption: (text: string | null) => setDemoCaption(text),
      getState: () => ({
        ready: demoRef.current.ready,
        zoom: demoRef.current.zoom,
        pan: { ...demoRef.current.pan },
      }),
      /** Client (CSS px) coordinates of tile (x, y)'s center — where a
       * real mouse event must land to hover/click that tile. Uses the
       * canvas's live bounding rect so it stays correct mid-pan/zoom. */
      tileToScreen: (x: number, y: number): { x: number; y: number } | null => {
        const meta = demoRef.current.renderMeta;
        const cv = canvasRef.current;
        if (!meta || !cv) return null;
        const rect = cv.getBoundingClientRect();
        const scale = rect.width / meta.canvasW;   // == effective zoom
        const c = tileCenterPx(x, y, meta);
        return { x: rect.left + c.x * scale, y: rect.top + c.y * scale };
      },
    };
    (window as unknown as { __mapforgeDemo?: typeof api }).__mapforgeDemo = api;
    return () => {
      if (demoAnimRef.current !== null) cancelAnimationFrame(demoAnimRef.current);
      delete (window as unknown as { __mapforgeDemo?: typeof api }).__mapforgeDemo;
    };
  }, [demoMode]);

  // ─── Load atlas + manifest + parsed in parallel, build IsoRenderer ──
  // One fetch per session open. The atlas is large (~2-8 MB PNG) but
  // ships from disk cache on second open, and the result is held in
  // RAM for the lifetime of the session.
  useEffect(() => {
    setRenderer(null);
    setRenderMeta(null);
    setRenderError(null);
    // SLF-bundled sectors now also load their atlas + parsed — the
    // session itself is marked read_only by the backend, which gates
    // editing UI further down. There's no reason to skip rendering
    // for SLF maps; they just can't be saved.
    if (!session || !xmlPath) return;
    let cancelled = false;
    let createdUrl: string | null = null;
    setRendererLoading(true);
    setLoadPhase("building-atlas");
    setPhaseFloor(0);
    setPhasePct(0);
    setFirstPaintDone(false);
    // Fresh session → fresh undo/redo state. savedAtDepth + undoDepth
    // reset to 0 so the Save button starts clean; redo clears too.
    // Generations reset together (a fresh IsoRenderer starts at 0).
    setUndoDepth(0);
    setRedoDepth(0);
    setSavedAtDepth(0);
    setHistGen(0);
    setSavedAtGen(0);

    // Helpers that advance phases monotonically. `accFloor` runs in a
    // local accumulator (not React state) so back-to-back phase
    // transitions inside the same async tick stack correctly.
    let accFloor = 0;
    const enterPhase = (next: ProgressPhase, prevWeight: number) => {
      if (cancelled) return;
      accFloor += prevWeight;
      setPhaseFloor(accFloor);
      setLoadPhase(next);
      setPhasePct(0);
    };
    const reportPhase = (pct: number) => {
      if (cancelled) return;
      setPhasePct(pct);
    };

    // Lazy pre-bake — open the session, then ask the backend for a
    // SECTOR-SPECIFIC partial atlas (only sprites this sector uses).
    // ~2 s vs ~11 s cold-bake on tileset 18 because the JSD harvest is
    // skipped + 80%+ of slots are unloaded. Renders the sector
    // immediately; the COMPLETE atlas is fetched in a background
    // effect (below) and hot-swapped via renderer.replaceAtlas when
    // it lands.
    const sessionId = session.session_id;
    (async () => {
      // 0. BAKE atlas with real progress streaming. session_id triggers
      //    the partial bake path on the backend; ~2 s on cold partial,
      //    ~50 ms on cache hit.
      let stiTotal = 0;
      await streamAtlasBuild(xmlPath, session.tileset, (evt) => {
        if (cancelled) return;
        if (evt.event === "phase") {
          // Bake phases all collapse into the single "building-atlas"
          // outer phase from the load progress bar's POV — we just
          // surface their labels inline. The sub-bar shows phase pct.
          // Track total slots from the "load-stis" label since that's
          // where almost all bake time goes; other phases jump phasePct
          // through completion ranges.
          const labelMatch = evt.label.match(/Loading (\d+) STI/);
          if (labelMatch) stiTotal = parseInt(labelMatch[1] ?? "0", 10);
          if (evt.phase === "cache-hit") reportPhase(100);
        } else if (evt.event === "progress" && evt.total > 0) {
          // The bake's load-stis phase reports current/total slots.
          // Map that onto 0-90% of the overall building-atlas phase
          // (leave 10% headroom for the post-load pack/render/encode
          // /persist phases that emit phase-only events).
          reportPhase(Math.min(90, Math.round((evt.current / evt.total) * 90)));
        }
      }, { sessionId });
      if (cancelled) return;
      reportPhase(100);
      enterPhase("fetching-atlas", PHASE_WEIGHTS["building-atlas"]);

      // 1. Atlas PNG — now cached on disk after the bake, fast fetch.
      const url = await fetchAtlasBlobUrl(xmlPath, session.tileset, (loaded, total) => {
        if (total && total > 0) reportPhase(Math.round((loaded / total) * 100));
      }, { sessionId });
      createdUrl = url;
      if (cancelled) return;
      enterPhase("fetching-manifest", PHASE_WEIGHTS["fetching-atlas"]);

      // 2. Manifest JSON — tiny.
      const manifest = await getAtlasManifest(xmlPath, session.tileset, { sessionId });
      if (cancelled) return;
      // Treat absent `complete` as true (older sidecars don't set it).
      // When false, the background effect below will swap to a complete
      // atlas as soon as the bake finishes. The `cancelled` guard above
      // protects against a session-change race: if the user switched
      // sectors before this manifest arrived, the new session's effect
      // is the one that should drive atlasComplete.
      setAtlasComplete(manifest.complete !== false);
      reportPhase(100);
      enterPhase("fetching-parsed", PHASE_WEIGHTS["fetching-manifest"]);

      // 3. Parsed sector JSON — ~1-2 MB.
      const parsed = await getSessionParsed(session.session_id);
      if (cancelled) return;
      reportPhase(100);
      enterPhase("decoding-atlas", PHASE_WEIGHTS["fetching-parsed"]);

      // 4 + 5. Decode atlas + bake shadow atlas, reported by the
      // renderer. Always WebGL2 since 2026-05-26 — the Canvas2D
      // painter's algorithm can't reproduce the engine's per-strip
      // Z-buffer clipping (e.g. lawless4 sub 16 sticking through walls
      // at C6 (62, 86)). The base IsoRenderer class is kept only as a
      // home for the shared state/edit/undo logic that IsoRendererGL
      // extends. See docs/HANDOFF_iso_renderer_z_buffer.md (closed).
      let lastPhase: ProgressPhase = "decoding-atlas";
      const r = await IsoRendererGL.createGL(url, manifest, parsed, (phase, pct) => {
        if (phase !== lastPhase) {
          enterPhase(phase, PHASE_WEIGHTS[lastPhase]);
          lastPhase = phase;
        }
        reportPhase(pct);
      });
      if (cancelled) return;
      // 6. Rendering phase — covers the React-commit + meta-compute +
      // canvas-mount + first-paint window. We DON'T clear rendererLoading
      // here; the paint useEffect does that once it sees firstPaintDone
      // flip. Without this phase the bar would hit 100% while the user
      // is still staring at the loading panel disappearing to reveal a
      // blank viewport for ~50-200 ms.
      enterPhase("rendering", PHASE_WEIGHTS[lastPhase]);
      // Show the inner sub-bar at ~50% so the phase doesn't look stuck
      // at 0% during the brief render wait. The phase itself is
      // indeterminate (no per-tile feedback yet) — this is purely
      // cosmetic to communicate "almost there".
      reportPhase(50);
      setRenderer(r);
      // Reset zoom/pan + hover when a fresh sector loads.
      setZoom(1);
      setPan({ x: 0, y: 0 });
      setHovered(null);
    })().catch((e) => {
      if (cancelled) return;
      setRenderError(e instanceof Error ? e.message : String(e));
      setRendererLoading(false);
      setLoadPhase(null);
    });
    return () => {
      cancelled = true;
      if (createdUrl) URL.revokeObjectURL(createdUrl);
    };
  }, [session?.session_id, session?.tileset, xmlPath]);

  // Derived overall percent — floor (sum of completed phase weights)
  // plus the current phase's weight × phasePct.
  const loadOverallPct = useMemo(() => {
    if (!loadPhase) return 0;
    const phaseWeight = PHASE_WEIGHTS[loadPhase];
    return Math.min(100, Math.round(phaseFloor + phaseWeight * (phasePct / 100)));
  }, [loadPhase, phaseFloor, phasePct]);

  // ─── Atlas reload after STI imports from the library ──────────────
  // When the user adds a new STI to the tileset (Library tab → +
  // Add to tileset), the backend writes the file + invalidates the
  // disk-cached atlas. But the in-memory IsoRenderer's cellMap
  // doesn't know about the new (slot, sub) yet — paint clicks for
  // that slot would resolve a cellMap miss and silently skip.
  //
  // This effect listens for atlasReloadEpoch bumps and calls
  // renderer.replaceAtlas to swap in fresh atlas + manifest without
  // touching parsed dict or undo stack. The user keeps editing
  // through it.
  useEffect(() => {
    if (atlasReloadEpoch === 0 || !renderer || !session || !xmlPath) return;
    let cancelled = false;
    setAtlasReloading(true);
    (async () => {
      log?.append({ severity: "info", message: "Reloading atlas…" });
      try {
        // Run the streaming bake so the cold-rebuild progress is
        // visible (atlas cache was just invalidated by the add
        // action). NDJSON streams aren't browser-cached.
        await streamAtlasBuild(xmlPath, session.tileset, () => {});
        if (cancelled) return;
        // bypassCache: true on the actual atlas/manifest fetches.
        // The endpoints set Cache-Control: max-age=86400 so the
        // browser would otherwise serve the pre-add PNG (URL hasn't
        // changed — only the underlying cache dir's fingerprint has).
        // That was the "added STI but can't paint or see it" bug.
        const [url, manifest] = await Promise.all([
          fetchAtlasBlobUrl(xmlPath, session.tileset, undefined,
                             { bypassCache: true }),
          getAtlasManifest(xmlPath, session.tileset,
                            { bypassCache: true }),
        ]);
        if (cancelled) {
          URL.revokeObjectURL(url);
          return;
        }
        await renderer.replaceAtlas(url, manifest);
        URL.revokeObjectURL(url);
        // Bump renderEpoch so the paint effect re-runs with the new
        // cellMap. (renderer is the same instance — React doesn't
        // see the mutation otherwise.)
        setRenderEpoch((e) => e + 1);
        log?.append({
          severity: "success",
          message: `Atlas reloaded (${manifest.cells.length} sprites)`,
        });
      } catch (e) {
        // eslint-disable-next-line no-console
        console.warn("atlas reload failed", e);
        log?.append({
          severity: "error",
          message: "Atlas reload failed",
          detail: e instanceof Error ? e.message : String(e),
        });
      } finally {
        if (!cancelled) setAtlasReloading(false);
      }
    })();
    return () => { cancelled = true; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [atlasReloadEpoch]);

  // ─── Background swap to COMPLETE atlas (after partial pre-bake) ────
  // Session-open got us a sector-specific partial atlas in ~2 s so the
  // canvas paints fast. Now in the background, fetch the COMPLETE
  // atlas (~3.4 s warm JSD cache / ~11 s cold) and hot-swap via
  // renderer.replaceAtlas. While the swap is pending, multi-tile
  // stamp recipes are missing and the inspector's "View JSD" button
  // is hidden; both come back automatically when atlasComplete flips
  // to true.
  useEffect(() => {
    if (atlasComplete || !renderer || !session || !xmlPath) return;
    let cancelled = false;
    (async () => {
      log?.append({
        severity: "info",
        message: "Loading complete atlas in background…",
      });
      try {
        // No sessionId — backend serves the full tileset atlas. Stream
        // build first so the disk cache is populated when the blob
        // fetch follows; on second-and-onward sector opens this hits
        // the warm JSD index cache and finishes in ~3-4 s.
        await streamAtlasBuild(xmlPath, session.tileset, () => {});
        if (cancelled) return;
        const [url, manifest] = await Promise.all([
          fetchAtlasBlobUrl(xmlPath, session.tileset),
          getAtlasManifest(xmlPath, session.tileset),
        ]);
        if (cancelled) {
          URL.revokeObjectURL(url);
          return;
        }
        await renderer.replaceAtlas(url, manifest);
        URL.revokeObjectURL(url);
        setRenderEpoch((e) => e + 1);
        setAtlasComplete(true);
        log?.append({
          severity: "success",
          message: `Complete atlas loaded (${manifest.cells.length} sprites). Multi-tile stamps + JSD viewer enabled.`,
        });
      } catch (e) {
        // Failure isn't fatal — the partial atlas still works for
        // sector rendering. Just log and leave atlasComplete=false so
        // the JSD-dependent UI stays disabled.
        // eslint-disable-next-line no-console
        console.warn("background complete-atlas swap failed", e);
        log?.append({
          severity: "warn",
          message: "Background atlas swap failed — multi-tile stamps + JSD viewer unavailable.",
          detail: e instanceof Error ? e.message : String(e),
        });
      }
    })();
    return () => { cancelled = true; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [atlasComplete, renderer, session?.session_id, xmlPath]);

  // ─── Highlight tiles for the selected room (canvas-side green tint) ─
  // Matches the Python iso_renderer's `--room` behavior. The SVG
  // overlay still draws hover + pinned highlights on top.
  const highlightTiles = useMemo(() => {
    if (selectedRoom === null || !renderer) return new Set<string>();
    const parsed = renderer.getParsed();
    const tiles = new Set<string>();
    for (let g = 0; g < parsed.rooms.length; g++) {
      if (parsed.rooms[g] === selectedRoom) {
        const x = g % parsed.cols;
        const y = Math.floor(g / parsed.cols);
        tiles.add(`${x},${y}`);
      }
    }
    return tiles;
  }, [selectedRoom, renderer, renderEpoch]);

  // ─── Compute renderMeta when renderer or region changes ────────────
  // Must run BEFORE the paint effect — the canvas wrapper only mounts
  // when renderMeta is non-null, and the paint effect needs the
  // mounted canvas to have a ref. If we set renderMeta inside the
  // paint effect we get a chicken-and-egg (canvas waits on meta, meta
  // waits on canvas ref → nothing ever renders).
  // `computeMeta` only depends on the parsed dict + region, NOT the
  // canvas, so this effect can run anytime.
  //
  // IMPORTANT: renderEpoch is deliberately NOT in deps. Adding it here
  // caused a regression (user feedback: "the painter doesn't paint
  // where you click") — every paint stroke bumps renderEpoch, which
  // would re-run this effect, which calls setRenderMeta with a fresh
  // object reference, which invalidates anything memoized on
  // renderMeta (click → tile inverse, hover preview). Even when the
  // numeric values match, the reference change cascades stale data.
  // If a future generator changes parsed.rooms (none currently do),
  // wire a SEPARATE recompute trigger here, not renderEpoch.
  useEffect(() => {
    if (!renderer) return;
    const meta = renderer.computeMeta({
      roomId: selectedRoom,
      ring: 5,
    });
    setRenderMeta(meta);
  }, [renderer, selectedRoom]);

  // ─── Paint the canvas. Runs after the canvas has mounted (renderMeta
  // is non-null → the JSX renders the <canvas>, the ref is populated,
  // and this effect's deps fire). Each subsequent render trigger
  // (layer toggle, edit, highlight change) re-paints in-place.
  //
  // After the FIRST successful paint, flips `firstPaintDone` and
  // tears down the load progress bar. Subsequent paints (edits, layer
  // toggles, etc.) skip the loading-state cleanup. ─────────────────
  useEffect(() => {
    if (!renderer || !canvasRef.current || !renderMeta) return;
    // IsoRenderer.render now takes the canvas element directly (not the
    // 2d context) so IsoRendererGL can acquire its own webgl2 context
    // off the same element without conflicting. The base IsoRenderer
    // (Canvas2D) acquires "2d" inside render.
    renderer.render(canvasRef.current, {
      roomId: selectedRoom,
      ring: 5,
      skipLayers: hiddenLayers,
      highlightTiles,
    });
    if (!firstPaintDone) {
      setFirstPaintDone(true);
      setRendererLoading(false);
      setLoadPhase(null);
    }
  }, [renderer, renderMeta, selectedRoom, hiddenLayers, renderEpoch, highlightTiles, firstPaintDone]);

  // Repaint when full-screen flips — the <canvas> remounts (dock slot
  // ↔ render-only view), so bump renderEpoch to redraw into the new
  // element.
  useEffect(() => {
    setRenderEpoch((e) => e + 1);
  }, [focusMode]);

  // Convert a mouse event on the canvas to canvas-native pixel coords.
  // Canvas backing-store size (set by IsoRenderer.render) may differ
  // from on-screen size when zoom is non-1 — same scaling as the
  // previous img approach.
  const eventToCanvasPixel = useCallback((e: { clientX: number; clientY: number }) => {
    if (!canvasRef.current) return null;
    const rect = canvasRef.current.getBoundingClientRect();
    const scaleX = canvasRef.current.width / rect.width;
    const scaleY = canvasRef.current.height / rect.height;
    return {
      px: (e.clientX - rect.left) * scaleX,
      py: (e.clientY - rect.top) * scaleY,
    };
  }, []);

  function pixelToTile(
    e: { clientX: number; clientY: number },
    opts: { logToConsole?: boolean } = {},
  ): { x: number; y: number } | null {
    if (!renderMeta || !info.data) return null;
    const p = eventToCanvasPixel(e);
    if (!p) return null;
    const tile = imagePixelToTile(
      p.px, p.py, renderMeta, info.data.cols, info.data.rows,
    );
    // Diagnostic: stash the full chain (event → canvas pixel → tile →
    // forward-projected south apex / diamond center) so the HUD can
    // display it AND, on real clicks, we get a console line for offline
    // review. Hover events update the HUD but skip console (otherwise
    // mouse moves spam the dev tools).
    if (debugClickHud && canvasRef.current) {
      const rect = canvasRef.current.getBoundingClientRect();
      const southApex = tile
        ? tileToCanvasPixel(tile.x, tile.y, renderMeta)
        : undefined;
      const diamondCenter = southApex
        ? { x: southApex.x, y: southApex.y - renderMeta.tileH / 2 }
        : undefined;
      const dbg = {
        clientX: e.clientX, clientY: e.clientY,
        rectLeft: rect.left, rectTop: rect.top,
        rectW: rect.width, rectH: rect.height,
        canvasW: canvasRef.current.width, canvasH: canvasRef.current.height,
        px: p.px, py: p.py,
        tile,
        ...(southApex ? { southApex } : {}),
        ...(diamondCenter ? { diamondCenter } : {}),
      };
      setLastClickDebug(dbg);
      if (opts.logToConsole) {
        // eslint-disable-next-line no-console
        console.log("[MapForge debug] click → tile", dbg);
      }
    }
    return tile;
  }

  // Bumped while a paint or batch edit is in flight so the UI can
  // show a "writing edit…" indicator (otherwise paint felt unresponsive
  // — user clicked, nothing changed for half a second, no signal that
  // the click was even seen).
  const [editsInFlight, setEditsInFlight] = useState(0);

  /** Paint a single tile with the active brush. The local renderer
   * mutates immediately + we re-render right away; the backend edit
   * fires in the background. If the backend disagrees we'd refetch
   * parsed, but in practice the local and remote dicts apply the same
   * `place` op so they stay in sync.
   *
   * Snapshots the tile's pre-edit state into the renderer's pending
   * stroke so Ctrl+Z can revert. The stroke is committed in
   * `onCanvasMouseUp`.
   *
   * `shiftHeld` inverts the configured paintMode for this one paint —
   * stamp mode + Shift = drop a single piece, manual mode + Shift =
   * stamp the whole footprint. Lets the user override the setting
   * tactically without opening the settings modal. */
  /** Apply one stroke spec to a list of tiles: snapshot each for undo,
   * mutate the local renderer (instant repaint), and fire ONE batched
   * applyEdits round-trip in the background. Shared by the shape tools.
   * Does NOT open/close the undo stroke — the caller wraps it in
   * beginStroke/endStroke. The synchronous part (snapshots + local
   * mutate) runs before the first await, so the caller may call
   * endStroke() right after without awaiting the backend. Read-only
   * sessions no-op. */
  async function applyTileEdits(tiles: Tile[], spec: StrokeSpec) {
    if (!session || !renderer || session.read_only || tiles.length === 0) return;
    // Engine-cap guard for `place` (same rule as paintBrush): a slot above
    // the compiled NUMBEROFTILETYPES renders NULL → CTD on sector load.
    if (spec.op === "place" && spec.slot > settings.engineMaxTileSlot) {
      log?.append({
        severity: "error",
        message: `Shape refused: slot ${spec.slot} exceeds engine cap ${settings.engineMaxTileSlot}.`,
      });
      return;
    }
    // 1. Snapshot pre-edit state for undo (per affected axis).
    for (const t of tiles) {
      if (spec.op === "set_room") renderer.recordRoomSnapshot(t.x, t.y);
      else renderer.recordSnapshot(t.x, t.y, spec.layer);
    }
    // 2. Mutate local — canvas re-paints next tick.
    for (const t of tiles) {
      renderer.applyLocalEdit(
        spec.op === "set_room"
          ? { x: t.x, y: t.y, op: "set_room", roomId: spec.roomId }
          : { x: t.x, y: t.y, op: "place",
              layer: spec.layer, slot: spec.slot, sub: spec.sub },
      );
    }
    setRenderEpoch((e) => e + 1);
    // 3. Send to backend as ONE batch (one HTTP for the whole list).
    setEditsInFlight((n) => n + 1);
    try {
      const edits: SessionEdit[] = tiles.map((t) =>
        spec.op === "set_room"
          ? { x: t.x, y: t.y, op: "set_room", room_id: spec.roomId }
          : { x: t.x, y: t.y, op: "place",
              layer: spec.layer, slot: spec.slot, sub: spec.sub },
      );
      const res = await applyEdits(session.session_id, edits);
      setSession(res.session);
    } catch (e) {
      // eslint-disable-next-line no-console
      console.warn("shape backend sync failed", e);
      log?.append({
        severity: "error",
        message: "Shape sync failed — backend rejected an edit",
        detail: e instanceof Error ? e.message : String(e),
      });
    } finally {
      setEditsInFlight((n) => Math.max(0, n - 1));
    }
  }

  async function paintBrush(
    tile: { x: number; y: number },
    shiftHeld: boolean = false,
  ) {
    if (!session || !activeBrush || !renderer) return;
    // Read-only sessions (SLF-sourced) can render but not edit.
    if (session.read_only) return;
    // Engine-cap guard. Painting a slot above the engine's compiled
    // NUMBEROFTILETYPES produces a .dat entry whose tile-type lookup
    // returns NULL on sector load → unhandled exception in the running
    // game (it's how a user hit the H4 CTD). Refuse here
    // BEFORE any local-render or backend round-trip happens. The
    // palette filter should prevent this from being reachable but
    // belt-and-suspenders against eyedrop on a tile that was painted
    // by some other tool with an out-of-range slot.
    if (activeBrush.slot > settings.engineMaxTileSlot) {
      log?.append({
        severity: "error",
        message: `Paint refused: brush slot ${activeBrush.slot} exceeds engine cap ${settings.engineMaxTileSlot}. `
               + "Adjust the cap in Settings if your ja2.exe supports more, or pick a brush at slot ≤ cap.",
      });
      return;
    }
    const layer = (paintLayer ?? activeBrush.layer) as LayerName;

    // ── Multi-tile stamping decision ─────────────────────────────────
    // The active brush's slot has a multi-tile JSD ⇒ it's stampable.
    // Effective mode = settings.paintMode XOR shiftHeld. When the
    // resolved mode is "stamp" we expand each anchor tile into the
    // JSD's footprint; "manual" falls through to today's single-sub
    // place behavior.
    const footprint = renderer.getFootprint(activeBrush.slot);
    // forceSingleTile (set by tile-inspector picks) suppresses stamp
    // expansion even on multi-tile JSDs — the user picked one specific
    // (slot, sub) and wants that exact tile placed at the click point.
    // Without this, clicking a chair SEAT thumbnail in the inspector
    // would stamp the whole chair with the seat offset from the click.
    const stampEligible = footprint !== null && !activeBrush.forceSingleTile;
    const baseMode = settings.paintMode;
    const effectiveStamp = stampEligible
      && ((baseMode === "stamp") !== shiftHeld);

    // Brush radius interacts badly with stamps — a heli at radius 2
    // would draw 5 overlapping helis. Force radius 1 when stamping.
    const effectiveRadius = effectiveStamp ? 1 : brushRadius;

    // ONE stamp per stroke. Drag-paint calls paintBrush again for every
    // tile the cursor crosses while the button is held — each new anchor
    // stamped ANOTHER full footprint, so a click with 1 tile of mouse
    // wobble placed 2 overlapping cars and a short drag placed a pile
    // (user screenshot, A10 car). A stamp commits on the initial press
    // only; click again for another copy.
    if (effectiveStamp && strokeRef.current && strokeRef.current.size > 0) {
      return;
    }

    // Compute the tiles inside the brush radius. radius=1 means just
    // the clicked tile; radius>1 paints a Manhattan-style square in
    // tile coords (matches iso-grid intuition because the visible
    // brush footprint is a diamond shape after iso projection).
    const r = effectiveRadius - 1;
    const anchorTiles: Array<{ x: number; y: number }> = [];
    for (let dy = -r; dy <= r; dy++) {
      for (let dx = -r; dx <= r; dx++) {
        // Manhattan filter to keep the brush diamond-shaped in iso
        // space (square would look stretched on the diagonal).
        if (Math.abs(dx) + Math.abs(dy) > r) continue;
        anchorTiles.push({ x: tile.x + dx, y: tile.y + dy });
      }
    }

    // Expand anchors into per-edit placement records. Each record is
    // one (x, y, sub) to apply. For stamps, each anchor contributes
    // footprint.tiles.length records (one per visible piece). For
    // single-tile paints, each anchor contributes one record (sub =
    // activeBrush.sub).
    //
    // Multi-variant structs (furn_mix, vehicle STIs, etc.) pack N
    // variants × M tiles into a single STI as contiguous sub-frames:
    // variant 0 = subs 1..M, variant 1 = subs M+1..2M, etc. The JSD
    // only encodes variant 0's footprint (tiles[0].sub=1,
    // tiles[1].sub=2, ...).
    //
    // To place the variant the user picked from the strip, SNAP the
    // brush sub to its variant anchor (the first sub of the variant
    // it belongs to), then shift every ft.sub by (anchor - 1). Without
    // the snap, picking sub 2 of a 2-tile vehicle places subs 2+3 —
    // the right half of variant 0 + the left half of variant 1
    // (visually "two trucks half-merged"). With the snap, picking any
    // sub in a variant lands the WHOLE variant: sub 2 → snap to 1 →
    // place (1, 2); sub 4 → snap to 3 → place (3, 4). The inspector's
    // forceSingleTile pick is the escape hatch for "I really do want
    // just this one sub at the anchor". (Fixes the
    // double-car bug on 2-tile vehicle STIs.)
    type Placement = { x: number; y: number; sub: number };
    const placements: Placement[] = [];
    if (effectiveStamp && footprint) {
      const stride = footprint.tiles.length;
      const variantAnchor =
        Math.floor((activeBrush.sub - 1) / stride) * stride + 1;
      const subDelta = variantAnchor - 1;
      if (variantAnchor !== activeBrush.sub) {
        log?.append({
          severity: "info",
          message:
            `Stamp snapped sub ${activeBrush.sub} → ${variantAnchor} ` +
            `(${stride}-tile variant anchor). Shift+click to drop just one sub.`,
        });
      }
      for (const a of anchorTiles) {
        for (const ft of footprint.tiles) {
          placements.push({ x: a.x + ft.bX, y: a.y + ft.bY, sub: ft.sub + subDelta });
        }
      }
    } else {
      for (const a of anchorTiles) {
        placements.push({ x: a.x, y: a.y, sub: activeBrush.sub });
      }
    }

    // Filter to fresh placements only — clip to map bounds + dedupe
    // tiles already touched in this stroke. We dedupe on (x, y, sub)
    // because a stamp legitimately writes multiple subs to different
    // tiles within one stroke; we just don't want the SAME sub at the
    // SAME tile to fire twice.
    const cols = info.data?.cols ?? 0;
    const rows = info.data?.rows ?? 0;
    const fresh: Placement[] = [];
    let droppedOOB = 0;
    for (const p of placements) {
      if (p.x < 0 || p.y < 0 || p.x >= cols || p.y >= rows) {
        droppedOOB++;
        continue;
      }
      const key = `${p.x},${p.y},${p.sub}`;
      if (!strokeRef.current) strokeRef.current = new Set();
      if (strokeRef.current.has(key)) continue;
      strokeRef.current.add(key);
      fresh.push(p);
    }
    if (effectiveStamp && droppedOOB > 0) {
      log?.append({
        severity: "warn",
        message: `Stamp clipped: ${droppedOOB} footprint tile${droppedOOB === 1 ? "" : "s"} fell outside the sector`,
      });
    }
    if (fresh.length === 0) return;

    // Resolve the auto-shadow companion. JA2's TileType enum pairs
    // struct slots (FIRSTOSTRUCT, FENCESTRUCT, FIRSTVEHICLE, etc.)
    // with shadow slots (FIRSTSHADOW, FENCESHADOW, etc.) — see
    // lib/jaSlotPairs. When the active brush's slot has a pair AND
    // the user hasn't disabled auto-pair in settings, also place the
    // matching shadow on the shadow layer with the same sub at each
    // placement (so stamped multi-tile structs get N shadows, one
    // per footprint piece).
    // Resolve shadow companion + guard against engine-cap overrun.
    // If the paired shadow slot is itself above the engine cap, skip
    // the auto-pair rather than refuse the whole paint — the struct
    // is still valid; the user just won't get an under-shadow on the
    // shadows layer for this tile. Log a one-time advisory so the
    // user knows why their shadows aren't appearing.
    let shadowSlot = settings.autoPairShadows
      ? findShadowSlot(activeBrush.slot)
      : null;
    if (shadowSlot !== null && shadowSlot > settings.engineMaxTileSlot) {
      log?.append({
        severity: "warn",
        message: `Auto-pair skipped: paired shadow slot ${shadowSlot} exceeds engine cap ${settings.engineMaxTileSlot}. Struct placed without shadow.`,
      });
      shadowSlot = null;
    }

    // 1. Snapshot pre-edit state for undo. recordSnapshot is idempotent
    //    per (x, y, layer) within a stroke so dragging across the same
    //    tile twice still leaves the FIRST snapshot intact. We snapshot
    //    by UNIQUE TILE (not by placement) because all placements at
    //    the same tile share one entries-array per layer.
    const snappedTiles = new Set<string>();
    for (const p of fresh) {
      const key = `${p.x},${p.y}`;
      if (snappedTiles.has(key)) continue;
      snappedTiles.add(key);
      renderer.recordSnapshot(p.x, p.y, layer);
      if (shadowSlot !== null) {
        renderer.recordSnapshot(p.x, p.y, "shadows");
      }
    }
    // 2. Mutate local — canvas re-paints next tick. Struct first,
    //    then shadow per placement — order matters for layer stacking
    //    but both go in the same stroke so Ctrl+Z reverts the whole
    //    stamp atomically.
    for (const p of fresh) {
      renderer.applyLocalEdit({
        x: p.x, y: p.y, op: "place",
        layer, slot: activeBrush.slot, sub: p.sub,
      });
      if (shadowSlot !== null) {
        renderer.applyLocalEdit({
          x: p.x, y: p.y, op: "place",
          layer: "shadows", slot: shadowSlot, sub: p.sub,
        });
      }
    }
    // rAF-coalesced epoch bump (see scheduleRenderEpoch). A held-and-
    // drag with a radius-2 brush previously fired one synchronous
    // setRenderEpoch + full canvas repaint per mousemove (~60+/sec);
    // now they coalesce into one repaint per displayed frame.
    scheduleRenderEpoch();
    // 3. Send to backend in the background as one batch (one HTTP per
    //    paint call, not one per tile in the brush — the loop above
    //    accumulated all fresh placements). For each placement we send
    //    the struct edit + (when applicable) the shadow edit,
    //    interleaved so the backend applies them in the same order
    //    the local renderer did.
    setEditsInFlight((n) => n + 1);
    try {
      const edits: SessionEdit[] = [];
      for (const p of fresh) {
        edits.push({
          x: p.x, y: p.y, op: "place",
          layer,
          slot: activeBrush.slot, sub: p.sub,
        });
        if (shadowSlot !== null) {
          edits.push({
            x: p.x, y: p.y, op: "place",
            layer: "shadows",
            slot: shadowSlot, sub: p.sub,
          });
        }
      }
      const res = await applyEdits(session.session_id, edits);
      setSession(res.session);
    } catch (e) {
      // eslint-disable-next-line no-console
      console.warn("paint backend sync failed", e);
      log?.append({
        severity: "error",
        message: "Paint sync failed — backend rejected an edit",
        detail: e instanceof Error ? e.message : String(e),
      });
    } finally {
      setEditsInFlight((n) => Math.max(0, n - 1));
    }
  }

  /** Height brush (P5): apply a height edit to the clicked tile + its
   * radius footprint as part of the open stroke. "raise"/"lower" read each
   * tile's CURRENT height and step it by `heightValue` (clamped 0..255);
   * "set" writes the value absolutely. `strokeRef` dedupes so one
   * click/drag steps each tile exactly once. Snapshot → local apply →
   * background applyEdits, mirroring paintBrush; bumps renderEpoch so the
   * height overlay refreshes. */
  async function paintHeight(tile: { x: number; y: number }) {
    if (!session || !renderer || session.read_only) return;
    const cols = info.data?.cols ?? 0;
    const rows = info.data?.rows ?? 0;
    const parsed = renderer.getParsed();
    const r = brushRadius - 1;
    if (!strokeRef.current) strokeRef.current = new Set();
    const edits: SessionEdit[] = [];
    for (let dy = -r; dy <= r; dy++) {
      for (let dx = -r; dx <= r; dx++) {
        // Manhattan filter → diamond brush in iso space (matches paintBrush).
        if (Math.abs(dx) + Math.abs(dy) > r) continue;
        const x = tile.x + dx;
        const y = tile.y + dy;
        if (x < 0 || y < 0 || x >= cols || y >= rows) continue;
        const key = `${x},${y}`;
        if (strokeRef.current.has(key)) continue;  // once per stroke
        strokeRef.current.add(key);
        const cur = parsed.heights[y * cols + x] ?? 0;
        const next = heightMode === "set"
          ? Math.max(0, Math.min(255, heightValue))
          : heightMode === "raise"
            ? Math.min(255, cur + heightValue)
            : Math.max(0, cur - heightValue);
        if (next === cur) continue;  // no-op (already at clamp / same value)
        renderer.recordHeightSnapshot(x, y);
        renderer.applyLocalEdit({ x, y, op: "set_height", height: next });
        edits.push({ x, y, op: "set_height", height: next });
      }
    }
    if (edits.length === 0) return;
    scheduleRenderEpoch();
    setEditsInFlight((n) => n + 1);
    try {
      const res = await applyEdits(session.session_id, edits);
      setSession(res.session);
    } catch (e) {
      // eslint-disable-next-line no-console
      console.warn("height brush backend sync failed", e);
      log?.append({
        severity: "error",
        message: "Height sync failed — backend rejected an edit",
        detail: e instanceof Error ? e.message : String(e),
      });
    } finally {
      setEditsInFlight((n) => Math.max(0, n - 1));
    }
  }

  /** Pop one undo entry: translates its snapshots back into set_entries
   * + set_room ops and dispatches them via the same applyEdits path
   * (backend + local in lock-step). */
  async function undo() {
    if (!session || !renderer || session.read_only) return;
    // Ghost preview live: undoing under it would interleave with the
    // snapshots the ghost will restore — so the FIRST Ctrl+Z acts as
    // "Clear preview" (a silent no-op here just felt broken, user
    // feedback 2026-06-11). The next Ctrl+Z undoes map edits normally.
    if (ghostActive) {
      clearGhost();
      log?.append({
        severity: "info",
        message: "Cleared the generator preview (nothing was applied). "
          + "Undo again for map edits.",
      });
      return;
    }
    const entry = renderer.popUndo();
    if (!entry) return;
    setEditsInFlight((n) => n + 1);
    try {
      // Local apply first so the canvas reflects the revert immediately.
      const edits: SessionEdit[] = [];
      for (const s of entry.snapshots) {
        renderer.applyLocalEdit({
          x: s.x, y: s.y, op: "set_entries",
          layer: s.layer, entries: s.entries,
        });
        edits.push({
          x: s.x, y: s.y, op: "set_entries",
          layer: s.layer, entries: s.entries,
        });
      }
      for (const r of entry.roomSnapshots) {
        renderer.applyLocalEdit({
          x: r.x, y: r.y, op: "set_room", roomId: r.roomId,
        });
        edits.push({
          x: r.x, y: r.y, op: "set_room", room_id: r.roomId,
        });
      }
      for (const h of entry.heightSnapshots) {
        renderer.applyLocalEdit({
          x: h.x, y: h.y, op: "set_height", height: h.height,
        });
        edits.push({
          x: h.x, y: h.y, op: "set_height", height: h.height,
        });
      }
      setRenderEpoch((e) => e + 1);
      if (edits.length > 0) {
        const res = await applyEdits(session.session_id, edits);
        setSession(res.session);
      }
    } catch (e) {
      // eslint-disable-next-line no-console
      console.warn("undo backend sync failed", e);
    } finally {
      setEditsInFlight((n) => Math.max(0, n - 1));
    }
  }

  /** Sync both history depths from the renderer. Called after every stroke
   * commit, undo, and redo so the Undo/Redo buttons + dirty flag track the
   * real stacks (endStroke clears redo → a fresh paint disables Redo). */
  function bumpHistory() {
    if (!renderer) return;
    const u = renderer.undoDepth();
    const r = renderer.redoDepth();
    setUndoDepth(u);
    setRedoDepth(r);
    setHistGen(renderer.generation());
  }

  /** Mirror one streamed generator op into the local IsoRenderer so the
   * canvas reflects output incrementally — used by the Generate dock
   * panel. Throttled paint trigger: bump
   * renderEpoch every 1000 ops so the canvas updates ~10× during a
   * 10k-op stream without choking React on 25k re-renders
   * (mirror-only-no-bump was "canvas frozen for the whole stream"). */
  function mirrorGeneratorOpThrottled(op: unknown) {
    if (!renderer) return;
    _mirrorGeneratorOp(renderer, op);
    genPanelOpCount.current += 1;
    if (genPanelOpCount.current % 1000 === 0) {
      setRenderEpoch((e) => e + 1);
    }
  }

  /** Generate the in-game minimap STI. Success lands in the log WITH
   * the preview thumbnail (LogEntry.imageDataUrl) — visible proof the
   * STI was written (the engine gives no feedback until a game load). */
  async function generateRadarNow() {
    if (!datPath || !xmlPath || radarBusy) return;
    setRadarBusy(true);
    try {
      const r = await generateRadar(datPath, xmlPath, tileset);
      const fn = r.output_path.split(/[\\/]/).pop();
      log?.append({
        severity: "success",
        message: `Radar map written: ${fn}`
          + (r.overrides_bundled ? " (overrides bundled radar)" : ""),
        detail: r.output_path,
        imageDataUrl: `data:image/png;base64,${r.preview_png_b64}`,
      });
    } catch (e) {
      log?.append({
        severity: "error",
        message: "Radar generation failed",
        detail: e instanceof Error ? e.message : String(e),
      });
    } finally {
      setRadarBusy(false);
    }
  }

  /** Post-generator-run resync — called by the Generate panel. Always paint regardless of `ok`: the per-op mirror already
   * mutated renderer.parsed, so the canvas MUST repaint to reflect
   * client state (an `ok`-gated repaint left partial-fail mirror
   * mutations invisible — code-review finding). On success, a
   * kitchen-sink getSessionParsed resync guarantees client == server
   * even if an op raced the React state machine. */
  function genRunComplete(applied: number, ok: boolean) {
    void ok;
    genPanelOpCount.current = 0;
    if (renderer && session && applied > 0) {
      getSessionParsed(session.session_id).then((parsed) => {
        renderer.setParsed(parsed);
        setRenderEpoch((e) => e + 1);
        bumpHistory();
      }).catch((e) => {
        log?.append({
          severity: "warn",
          message: `Canvas resync failed: ${e instanceof Error ? e.message : String(e)}. Click any tile to force refresh.`,
        });
        setRenderEpoch((e2) => e2 + 1);
        bumpHistory();
      });
    } else {
      setRenderEpoch((e) => e + 1);
      if (renderer) bumpHistory();
    }
  }

  /** Re-apply the last undone stroke. Mirror of `undo()` but pulls from the
   * renderer's redo stack (popRedo also pushes the inverse back onto the
   * undo stack, so a redo can itself be undone). */
  async function redo() {
    if (!session || !renderer || session.read_only) return;
    if (ghostActive) { clearGhost(); return; }   // same behavior as undo()
    const entry = renderer.popRedo();
    if (!entry) return;
    setEditsInFlight((n) => n + 1);
    try {
      const edits: SessionEdit[] = [];
      for (const s of entry.snapshots) {
        renderer.applyLocalEdit({
          x: s.x, y: s.y, op: "set_entries", layer: s.layer, entries: s.entries,
        });
        edits.push({
          x: s.x, y: s.y, op: "set_entries", layer: s.layer, entries: s.entries,
        });
      }
      for (const r of entry.roomSnapshots) {
        renderer.applyLocalEdit({ x: r.x, y: r.y, op: "set_room", roomId: r.roomId });
        edits.push({ x: r.x, y: r.y, op: "set_room", room_id: r.roomId });
      }
      for (const h of entry.heightSnapshots) {
        renderer.applyLocalEdit({ x: h.x, y: h.y, op: "set_height", height: h.height });
        edits.push({ x: h.x, y: h.y, op: "set_height", height: h.height });
      }
      setRenderEpoch((e) => e + 1);
      if (edits.length > 0) {
        const res = await applyEdits(session.session_id, edits);
        setSession(res.session);
      }
    } catch (e) {
      // eslint-disable-next-line no-console
      console.warn("redo backend sync failed", e);
    } finally {
      setEditsInFlight((n) => Math.max(0, n - 1));
    }
  }

  /**
   * Canvas-button cycle for pencil:
   *   mousedown   → beginStroke, paint first tile
   *   mousemove   → paint additional tiles (if button held)
   *   mouseup     → endStroke (commits undo entry, releases stroke buffer)
   *
   * Click events (mousedown + mouseup + click) are NOT used for paint
   * because they fire AFTER mouseup. Hooking on click meant the stroke
   * stayed open until the NEXT mouseup, which (a) delayed undo-stack
   * commit and (b) let Ctrl+Z pop the previous stroke instead of the
   * latest one. Mousedown-driven paint closes the stroke inside one
   * click cycle so Ctrl+Z always reverts what the user just did.
   *
   * Inspect tool still uses click (we want the click-and-release pin
   * behavior, not "press to inspect"). The handler is split because
   * onMouseDown also has to coexist with the wrapper's alt/middle pan.
   */
  function onCanvasMouseDown(e: React.MouseEvent<HTMLCanvasElement>) {
    // Plain left button only; alt+left and middle are reserved for pan
    // (the wrapper div handles those).
    if (e.button !== 0 || e.altKey) return;
    // While picking a region (Generate panel), the canvas is a
    // region-picker — not a paint/shape surface. Mousedown ANCHORS the
    // first corner so the user can drag a box (release completes), or
    // release in place and click the opposite corner instead.
    if (pickingRect) {
      if (pickingRect.stage === 0) {
        const tile = hovered ?? pixelToTile(e);
        if (tile) {
          pickJustAnchoredRef.current = true;
          setPickingRect({ ...pickingRect, stage: 1, corner1: tile });
        }
      }
      return;
    }
    // Building placement mode (StarCraft-style): left click stamps the
    // building anchored at the hovered tile — the same top-left the
    // footprint ghost shows — and STAYS in placement mode so repeated
    // clicks stamp more buildings. Placement takes PRECEDENCE over the
    // ghostActive block below (the sprite ghost is placement's own).
    // The panel's run() also guards re-entrancy while a stamp flies.
    if (placingBuilding) {
      if (placementStampBusy) return;
      const tile = hovered ?? pixelToTile(e);
      if (!tile) return;
      // The placement sprite ghost lives on its own overlay canvas (it
      // never touches the parsed dict), so the stamp's snapshots always
      // capture the real pre-stamp tiles — no ghost clearing needed.
      const r = placingBuilding.run(tile.x, tile.y);
      if (r instanceof Promise) {
        setPlacementStampBusy(true);
        void r.finally(() => setPlacementStampBusy(false));
      }
      return;
    }
    // A generator ghost is being previewed — painting now would tangle
    // user edits with ghost state that's about to be reverted/applied.
    if (ghostActive) return;
    if (!renderer) return;
    // Prefer the HOVERED tile (what the user visually sees) over the
    // re-resolved mousedown coords. The physical button press often
    // jitters the cursor 1-3 px, which can flip the resolved tile when
    // the click landed near a diamond edge. "What you see is what you get."
    const tile = hovered ?? pixelToTile(e, { logToConsole: true });
    if (!tile) return;
    if (tool === "inspect") {
      // Inspect pins on click (onCanvasClick) — nothing to do on mousedown.
    } else if (tool === "pencil") {
      if (!activeBrush) return;
      strokeRef.current = new Set();
      // Stroke label reflects what the user actually did: "Stamp 2_HELI
      // (3 tiles)" for footprint paints, "Paint w_dec01" for singles.
      const footprint = renderer.getFootprint(activeBrush.slot);
      const willStamp = footprint !== null
        && ((settings.paintMode === "stamp") !== e.shiftKey);
      const label = willStamp && footprint
        ? `Stamp ${activeBrush.sti_filename.replace(/\.sti$/i, "")} `
          + `(${footprint.tiles.length} tile${footprint.tiles.length === 1 ? "" : "s"})`
        : `Paint ${activeBrush.sti_filename.replace(/\.sti$/i, "")}`;
      renderer.beginStroke(label);
      paintBrush(tile, e.shiftKey);
    } else if (tool === "shape") {
      // Shapes need a brush except the room tool (writes a room id, not
      // a tile). Anchor the drag; the commit happens on mouseup. A
      // non-null shapeAnchor also drives the live preview overlay.
      if (shapeKind !== "room" && !activeBrush) return;
      setShapeAnchor(tile);
      setShapeCursor(tile);
    } else if (tool === "select") {
      if (pasteMode) {
        // Armed paste: this click drops the clipboard with its top-left
        // at the clicked tile (async — one stroke, then auto-validate).
        if (clipboard) void doPaste(tile);
      } else {
        // Anchor a marquee drag; mouseup commits the selection rect.
        setSelectAnchor(tile);
        setSelectCursor(tile);
        setSelectRect(null);
      }
    } else if (tool === "height") {
      // Height brush: open a stroke + step the first tile. The drag
      // continues in onCanvasMove; mouseup closes the stroke (strokeRef
      // is non-null, so the existing endStroke block fires).
      if (session?.read_only) return;
      strokeRef.current = new Set();
      const verb = heightMode === "set"
        ? `Set height ${heightValue}`
        : heightMode === "raise"
          ? `Raise height +${heightValue}`
          : `Lower height -${heightValue}`;
      renderer.beginStroke(verb);
      void paintHeight(tile);
    } else {
      assertNever(tool);
    }
  }

  function onCanvasClick(e: React.MouseEvent<HTMLCanvasElement>) {
    // A region pick just completed on mouseup — swallow the click that
    // follows it so it can't pin the inspector / start a tool action.
    if (pickSuppressClickRef.current) {
      pickSuppressClickRef.current = false;
      return;
    }
    // Region-picker mode (Generate-panel side-trip). Mousedown anchors
    // (see onCanvasMouseDown), mouseup-on-another-tile completes; this
    // click path is the click-then-click fallback's second corner.
    if (pickingRect) {
      if (pickJustAnchoredRef.current) {
        pickJustAnchoredRef.current = false;
        return;
      }
      const tile = pixelToTile(e);
      if (!tile) return;
      if (pickingRect.stage === 1 && pickingRect.corner1) {
        const cb = pickingRect.onComplete;
        const c1 = pickingRect.corner1;
        setPickingRect(null);
        cb(c1, tile);
      }
      return;
    }
    // Building placement: the mousedown already stamped — the click
    // that follows must not pin the inspector.
    if (placingBuilding) return;
    // Ghost preview live — block tool actions (see onCanvasMouseDown).
    if (ghostActive) return;
    // Inspect mode only. Pencil + shape act via mousedown/move/up above;
    // their click event fires AFTER mouseup and must not pin a tile.
    if (tool !== "inspect") return;
    const tile = pixelToTile(e, { logToConsole: true });
    if (!tile) return;
    setPinned(tile);
  }

  function onCanvasMove(e: React.MouseEvent<HTMLCanvasElement>) {
    const tile = pixelToTile(e);
    // Dedupe: mousemove fires far more often than the hovered TILE
    // changes — returning the previous object when (x, y) is unchanged
    // skips the setState re-render entirely.
    setHovered((prev) =>
      prev && tile && prev.x === tile.x && prev.y === tile.y ? prev : tile,
    );
    // Drag-paint: if pencil + brush + left button held + stroke active.
    // Inherit the Shift state from the live mousemove so the user can
    // toggle stamp/manual mid-drag if they want to (rare but coherent).
    if (
      tool === "pencil" && activeBrush && tile
      && e.buttons === 1 && strokeRef.current !== null
    ) {
      paintBrush(tile, e.shiftKey);
    }
    // Shape drag: track the cursor tile so the preview overlay updates
    // live while the left button is held after an anchor.
    if (tool === "shape" && tile && e.buttons === 1 && shapeAnchor) {
      setShapeCursor(tile);
    }
    // Select drag: track the marquee end-point while the button is held.
    if (tool === "select" && tile && e.buttons === 1 && selectAnchor) {
      setSelectCursor(tile);
    }
    // Height brush drag: step each freshly entered tile (strokeRef dedupes
    // so a tile already touched this stroke isn't stepped again).
    if (tool === "height" && tile && e.buttons === 1 && strokeRef.current !== null) {
      void paintHeight(tile);
    }
  }

  function onCanvasMouseUp() {
    // Region pick: releasing a drag over a DIFFERENT tile completes the
    // region. Releasing on the anchor tile keeps the picker armed, so
    // click-then-click still works for precision picks.
    if (pickingRect?.stage === 1 && pickingRect.corner1) {
      const c1 = pickingRect.corner1;
      const tile = hovered;
      if (tile && (tile.x !== c1.x || tile.y !== c1.y)) {
        const cb = pickingRect.onComplete;
        pickSuppressClickRef.current = true;
        setPickingRect(null);
        cb(c1, tile);
      }
      return;
    }
    // Close the pencil stroke if one is open. (Inspect = no-op.)
    if (strokeRef.current !== null && renderer) {
      renderer.endStroke();
      bumpHistory();
    }
    strokeRef.current = null;
    // Commit a shape drag released over the canvas. (Releases off-canvas
    // are cancelled by the wrapper's mouseup/leave handler.)
    if (tool === "shape" && shapeAnchor) {
      commitShape(shapeAnchor, shapeCursor ?? shapeAnchor);
      setShapeAnchor(null);
      setShapeCursor(null);
    }
    // Finalize a selection drag released over the canvas → committed rect
    // (the Copy button slices this). Releases off-canvas are cancelled by
    // onMouseUpDrag below.
    if (tool === "select" && selectAnchor) {
      setSelectRect({ a: selectAnchor, b: selectCursor ?? selectAnchor });
      setSelectAnchor(null);
      setSelectCursor(null);
    }
  }

  /** Commit the in-progress shape drag as ONE undoable stroke. Computes
   * the shape's tiles, bounds-filters, optionally confirms a very large
   * fill, then runs snapshot + local-apply + background batch via
   * applyTileEdits wrapped in begin/endStroke so Ctrl+Z reverts the whole
   * shape. (applyTileEdits' snapshot+local work is synchronous, so calling
   * endStroke right after — without awaiting the backend — is safe.) */
  function commitShape(anchor: Tile, cursor: Tile) {
    if (!session || session.read_only || !renderer) return;
    const cols = info.data?.cols ?? 0;
    const rows = info.data?.rows ?? 0;
    const tiles = shapeTiles(shapeKind, anchor, cursor).filter(
      (t) => t.x >= 0 && t.y >= 0 && t.x < cols && t.y < rows,
    );
    if (tiles.length === 0) return;
    // Soft guard for huge fills — one cheap confirm, not a hard block.
    if (
      tiles.length > 2000
      && !window.confirm(`This shape covers ${tiles.length} tiles. Apply?`)
    ) {
      return;
    }
    if (shapeKind === "room") {
      renderer.beginStroke(
        roomId === 0
          ? `Clear room (${tiles.length} tiles)`
          : `Mark room ${roomId} (${tiles.length} tiles)`,
      );
      void applyTileEdits(tiles, { op: "set_room", roomId });
    } else {
      if (!activeBrush) return;
      const layer = (paintLayer ?? activeBrush.layer) as LayerName;
      const name = activeBrush.sti_filename.replace(/\.sti$/i, "");
      const verb = shapeKind === "line"
        ? "Line"
        : shapeKind === "rect-outline"
          ? "Outline"
          : "Fill";
      renderer.beginStroke(`${verb} ${name} (${tiles.length} tiles)`);
      void applyTileEdits(tiles, {
        op: "place", layer, slot: activeBrush.slot, sub: activeBrush.sub,
      });
    }
    renderer.endStroke();
    bumpHistory();
  }

  /** Copy the committed selection rectangle into the clipboard. Reads the
   * live (uncommitted) parsed sector, slices the rect into relative tiles
   * + room ids + heights, then strips buddy-eligible shadow entries (the
   * engine auto-re-adds those at load via HAS_SHADOW_BUDDY — keeping them
   * would double-shadow in-game). Read-only-safe: copy never mutates. */
  async function doCopy() {
    if (!session || !renderer || !selectRect) return;
    const name = datPath.split(/[\\/]/).pop() ?? "sector";
    try {
      const parsed = await getSessionParsed(session.session_id);
      const raw = sliceRegion(parsed, selectRect.a, selectRect.b, name);
      if (raw.tiles.length === 0) {
        log?.append({ severity: "warn", message: "Selection is empty — nothing to copy." });
        return;
      }
      const clip = stripBuddyShadows(raw, (slot) => isShadowOnlySlot(slot));
      setClipboard(clip);
      log?.append({
        severity: "info",
        message: `Copied ${clip.w}×${clip.h} region (${clip.tiles.length} tiles) from ${name}.`,
      });
    } catch (e) {
      log?.append({
        severity: "error",
        message: "Copy failed — could not read the sector.",
        detail: e instanceof Error ? e.message : String(e),
      });
    }
  }

  /** Place the clipboard at `anchor` (its top-left) as ONE undoable,
   * transactional paste. Snapshots every touched axis (layers / room /
   * height) BEFORE applying so a single Ctrl+Z reverts the whole paste;
   * mirrors locally for an instant repaint, then persists via the
   * transactional `applyEdits`, then auto-validates. Same-tileset only —
   * cross-tileset is deferred (guarded here AND by a disabled button). */
  async function doPaste(anchor: Tile) {
    if (!session || session.read_only || !renderer || !clipboard) return;
    // Re-entrancy guard: a double-click in paste mode fires two mousedowns
    // before React re-renders pasteMode→false, so this synchronous ref is
    // what actually prevents a double-paste (two strokes + two divergent
    // room-id ranges). Cleared on every exit path.
    if (pasteBusyRef.current) return;
    pasteBusyRef.current = true;
    setPasteMode(false);  // one Paste press = one placement attempt
    if (clipboard.sourceTileset !== tileset) {
      pasteBusyRef.current = false;
      log?.append({
        severity: "error",
        message: `Cross-tileset paste isn't supported yet (clipboard tileset `
          + `${clipboard.sourceTileset} → ${tileset}). Copy within the same tileset.`,
      });
      return;
    }
    const cols = info.data?.cols ?? renderer.getParsed().cols;
    const rows = info.data?.rows ?? renderer.getParsed().rows;
    setEditsInFlight((n) => n + 1);
    // Flips true once the local stroke is committed — gates the catch
    // rollback so a failure BEFORE the stroke (e.g. getSessionParsed) can't
    // pop an unrelated earlier stroke.
    let strokeCommitted = false;
    try {
      // The target's CURRENT room ids drive the remap to fresh unused ids.
      const parsed = await getSessionParsed(session.session_id);
      const { edits, targetTiles, droppedTiles } = pasteEdits(
        clipboard, anchor, cols, rows, { existingRoomIds: parsed.rooms },
      );
      if (edits.length === 0) {
        log?.append({
          severity: "warn",
          message: "Nothing pasted — the region fell entirely outside the map.",
        });
        return;
      }
      // Destructive-overwrite guard (stricter than fills' 2000 — paste
      // replaces every layer of every target tile).
      if (
        targetTiles > 500
        && !window.confirm(
          `Paste over ${targetTiles} tiles? This replaces their current `
          + `terrain, objects, structures, rooms and heights.`,
        )
      ) {
        return;
      }
      // One stroke for the whole paste → one Ctrl+Z reverts it all.
      renderer.beginStroke(
        `Paste ${clipboard.w}×${clipboard.h} (${targetTiles} tiles)`,
      );
      for (const ed of edits) {
        // Snapshot the right axis BEFORE the local mutation overwrites it.
        if (ed.op === "set_entries" && ed.layer) {
          renderer.recordSnapshot(ed.x, ed.y, ed.layer);
        } else if (ed.op === "set_room") {
          renderer.recordRoomSnapshot(ed.x, ed.y);
        } else if (ed.op === "set_height") {
          renderer.recordHeightSnapshot(ed.x, ed.y);
        }
        renderer.applyLocalEdit({
          x: ed.x, y: ed.y, op: ed.op,
          layer: ed.layer, slot: ed.slot, sub: ed.sub,
          entries: ed.entries, roomId: ed.room_id, height: ed.height,
        });
      }
      renderer.endStroke();
      strokeCommitted = true;
      bumpHistory();
      setRenderEpoch((e) => e + 1);
      // Persist to the backend session (transactional: rolls back on any
      // mid-batch failure, leaving the live session untouched).
      const res = await applyEdits(session.session_id, edits);
      setSession(res.session);
      log?.append({
        severity: "info",
        message: `Pasted ${targetTiles} tiles`
          + (droppedTiles > 0 ? ` (${droppedTiles} clipped at the map edge)` : "")
          + ".",
      });
      // Auto-validate the post-paste state — but only surface findings
      // the PASTE introduced. Findings the file already carried when it
      // was opened come back tagged `preexisting` (session baseline);
      // popping the panel for those blamed the paste for e.g. C6.DAT's
      // 40 native room-ID gaps and couldn't be cleared by undo.
      try {
        const report = await validateSession(session.session_id);
        const fresh = report.findings.filter((f) => !f.preexisting);
        const freshErrors = fresh.filter((f) => f.severity === "error").length;
        const freshWarnings = fresh.filter((f) => f.severity === "warn").length;
        const preexisting = report.findings.filter(
          (f) => f.preexisting && f.severity !== "info",
        ).length;
        if (freshErrors > 0 || freshWarnings > 0) {
          const top = fresh.find((f) => f.severity !== "info");
          log?.append({
            severity: freshErrors > 0 ? "error" : "warn",
            message: `Paste introduced ${freshErrors} error(s), `
              + `${freshWarnings} warning(s). Opening the Validation panel.`,
            detail: top ? `${top.code}: ${top.message}` : undefined,
          });
          openValidatePanel();
        } else {
          log?.append({
            severity: "info",
            message: "Paste validated clean."
              + (preexisting > 0
                ? ` (${preexisting} pre-existing map finding(s) unchanged — `
                  + "see ✓ Validate for details.)"
                : ""),
          });
        }
      } catch (e) {
        log?.append({
          severity: "warn",
          message: "Post-paste validation could not run.",
          detail: e instanceof Error ? e.message : String(e),
        });
      }
    } catch (e) {
      // The backend `applyEdits` is transactional — on rejection the live
      // session is untouched, so the optimistic local mirror is now ahead
      // of the server. Revert it (no backend round-trip — the server is
      // already at pre-paste state) and drop the dangling undo stroke so
      // Ctrl+Z can't push a revert the server never needed.
      if (strokeCommitted) {
        // discardLastUndo, NOT popUndo: popUndo pushes a redo mirror of
        // the rejected paste, letting Ctrl+Y replay it locally with no
        // second rollback — local mirror diverges from the server again.
        const entry = renderer.discardLastUndo();
        if (entry) {
          for (const s of entry.snapshots) {
            renderer.applyLocalEdit({
              x: s.x, y: s.y, op: "set_entries", layer: s.layer, entries: s.entries,
            });
          }
          for (const r of entry.roomSnapshots) {
            renderer.applyLocalEdit({ x: r.x, y: r.y, op: "set_room", roomId: r.roomId });
          }
          for (const h of entry.heightSnapshots) {
            renderer.applyLocalEdit({ x: h.x, y: h.y, op: "set_height", height: h.height });
          }
          bumpHistory();
          setRenderEpoch((e2) => e2 + 1);
        }
      }
      log?.append({
        severity: "error",
        message: "Paste failed — the edit batch was rejected; reverted local changes.",
        detail: e instanceof Error ? e.message : String(e),
      });
    } finally {
      setEditsInFlight((n) => Math.max(0, n - 1));
      pasteBusyRef.current = false;
    }
  }

  /**
   * Advance the active brush to the next/previous sub-frame and log
   * the change. Used by three surfaces:
   *   - `,` / `.` keys (dispatcher cases below)
   *   - Keyboard shortcut bindings (`,` / `.` by default)
   *   - Floating BrushSubStrip click (in the toolbar)
   *
   * Sparse-aware: uses `renderer.listValidSubs(slot)` so we only land
   * on subs that actually exist in the atlas (some slots have gaps,
   * e.g. sub 2 missing between 1 and 3 — cycling 1→2 would land on a
   * "?" placeholder). Wraps at both ends. No-op when the brush has 0
   * or 1 valid subs — logged once so the user gets feedback rather
   * than silent nothing.
   */
  function cycleSub(delta: 1 | -1) {
    if (!activeBrush || !renderer) return;
    const subs = renderer.listValidSubs(activeBrush.slot);
    if (subs.length <= 1) {
      log?.append({
        severity: "info",
        message: `Slot ${activeBrush.slot} has only one sub-frame — nothing to cycle.`,
      });
      return;
    }
    const idx = subs.indexOf(activeBrush.sub);
    // -1 (current sub somehow not in the list) → treat as before-first
    // so +1 lands on subs[0]. Defensive; shouldn't fire in practice.
    const base = idx < 0 ? -1 : idx;
    const next = subs[(base + delta + subs.length) % subs.length];
    // `subs.length > 1` checked above, so the modulo index is in bounds;
    // the explicit guard satisfies `noUncheckedIndexedAccess` without
    // a non-null assertion.
    if (next === undefined) return;
    setActiveBrush({ ...activeBrush, sub: next });
    log?.append({
      severity: "info",
      message: `Sub ${activeBrush.sub} → ${next} `
             + `(${activeBrush.sti_filename.replace(/\.sti$/i, "")}, `
             + `${subs.length} variants)`,
    });
  }

  /**
   * Right-click on the canvas = eyedropper. Samples the topmost
   * entry at the clicked tile and sets it as the active brush, so
   * the next paint click reproduces what was already there.
   *
   * Layer priority (high → low) matches what's visually "on top"
   * in the iso render: structs → onroofs → roofs → objs → shadows →
   * land. The LAST entry on the highest-priority non-empty layer
   * wins, because newer entries are drawn over older ones on the
   * same layer.
   *
   * Returning false from the event handler isn't necessary — the
   * parent `<div onContextMenu={e => e.preventDefault()}>` already
   * killed the browser menu. We still preventDefault on the canvas
   * specifically for belt-and-suspenders.
   */
  function onCanvasContextMenu(e: React.MouseEvent<HTMLCanvasElement>) {
    e.preventDefault();
    // Alt+right-click cycles the active brush's sub-frame (Shift+Alt
    // reverses). Per SUBFRAME_SWITCH_UX option E.
    // CRITICAL: branch on altKey BEFORE anything else so plain right-
    // click stays pure eyedropper (the prior implementation tangled
    // the two and broke eyedropper).
    if (e.altKey) {
      cycleSub(e.shiftKey ? -1 : 1);
      return;
    }
    // Plain right-click on canvas = eyedropper.
    if (!renderer || !info.data) return;
    const tile = pixelToTile(e);
    if (!tile) return;
    // Local inspect — already excludes anything not in the loaded
    // parsed dict. Gives us all 6 layers' entries for this tile.
    const ins = renderer.inspectTile(tile.x, tile.y);
    if (!ins) return;
    const priority: LayerName[] = [
      "structs", "onroofs", "roofs", "objs", "shadows", "land",
    ];
    // Capture the previous brush so we can log slot/sub changes
    // explicitly. Without this, "Eyedropped roadtile (objs sub 24)"
    // hides the fact that the user's prior brush was on a different
    // sub (or slot) — user feedback: "alt right click is trying to
    // copy the tile and go to the next subframe at the same time."
    // The sub-change WAS happening (eyedrop adopts whatever sub was
    // painted at the clicked tile), just invisibly. Now we surface it.
    const prev = activeBrush;
    for (const layer of priority) {
      const entries = ins.layers[layer] ?? [];
      if (entries.length === 0) continue;
      // The LAST entry is the most recently added → drawn on top of
      // the others on this layer.
      const top = entries[entries.length - 1];
      if (!top) continue;
      armBrush({
        slot: top.slot,
        sub: top.sub,
        category: "(eyedropped)",
        layer,
        sti_filename: top.sti_filename ?? `slot ${top.slot}`,
      });
      // Build a delta-aware log line so the user can see what
      // changed (slot, sub, or both) instead of just "eyedropped X".
      const stiLabel = top.sti_filename ?? `slot ${top.slot}`;
      let delta = "";
      if (prev) {
        const slotChanged = prev.slot !== top.slot;
        const subChanged = prev.sub !== top.sub;
        if (slotChanged && subChanged) {
          delta = ` (was slot ${prev.slot}/sub ${prev.sub} → slot ${top.slot}/sub ${top.sub})`;
        } else if (slotChanged) {
          delta = ` (slot ${prev.slot} → ${top.slot})`;
        } else if (subChanged) {
          delta = ` (sub ${prev.sub} → ${top.sub})`;
        } else {
          delta = ` (same brush)`;
        }
      }
      log?.append({
        severity: "info",
        message: `Eyedropped ${stiLabel} (${layer} sub ${top.sub}) from (${tile.x},${tile.y})${delta}`,
      });
      return;
    }
    // Empty tile — nothing to pick.
    log?.append({
      severity: "info",
      message: `Eyedrop: tile (${tile.x},${tile.y}) is empty`,
    });
  }

  // ─── Save action (used by hotkey + the SaveButton) ─────────────────
  // SaveButton owns its own state (busy spinner, last-saved info) so
  // we duplicate the bare-minimum save call here for the hotkey.
  // Both go through the same backend POST + atlas-reload-isn't-needed
  // path.
  const saveFromHotkey = async () => {
    if (!session || session.read_only || !localDirty) return;
    try {
      const res = await saveSession(session.session_id);
      setSession(res.session);
      setSavedAtDepth(undoDepth);
      setSavedAtGen(renderer ? renderer.generation() : histGen);
      log?.append({
        severity: "success",
        message: `Saved ${(res.bytes_written / 1024).toFixed(1)} KB to disk`,
        detail: res.backup_path ? `backup: ${res.backup_path}` : undefined,
      });
    } catch (e) {
      log?.append({
        severity: "error",
        message: "Save failed",
        detail: e instanceof Error ? e.message : String(e),
      });
    }
  };

  // ─── Console command registry ──────────────────────────────────────
  // Built once per render (cheap — small dict). The console's submit
  // path looks up by name; commands receive a `ctx` with log + print
  // helpers and run their handler against the in-scope closures
  // (session, renderer, undo, saveFromHotkey, etc.). Keep the surface
  // small — every command here should mirror something the GUI does;
  // the console is a keyboard accelerator, not a scripting language.
  const consoleCommands: CommandSpec[] = useMemo(() => {
    return [
      {
        name: "save",
        summary: "Save the current session to disk",
        handler: async (_p, ctx) => {
          if (!session) { ctx.print("No session open.", "warn"); return; }
          if (session.read_only) { ctx.print("Session is read-only.", "warn"); return; }
          await saveFromHotkey();
        },
      },
      {
        name: "undo",
        summary: "Undo the last edit",
        handler: async (_p, ctx) => {
          if (!renderer || !session || session.read_only) {
            ctx.print("Nothing to undo.", "warn");
            return;
          }
          await undo();
          bumpHistory();
        },
      },
      {
        name: "reload",
        summary: "Re-fetch the sector from disk (discards unsaved edits)",
        handler: async (_p, ctx) => {
          // Re-mount the session by toggling the search params so the
          // open-session effect re-fires. Cheaper than re-implementing
          // the open path here.
          if (!datPath) { ctx.print("No sector open.", "warn"); return; }
          ctx.print("Reloading sector…");
          // Bump the restart epoch — it's in the session-open effect's
          // dep array, so a fresh session opens from disk. (The old
          // `setSession(null)` re-opened NOTHING — `session` is not a
          // dep of that effect — leaving a dead blank viewport until
          // the URL changed or the sidecar restarted.)
          setSessionRestartEpoch((n) => n + 1);
        },
      },
      {
        name: "help",
        summary: "List all console commands",
        handler: (_p, ctx) => {
          ctx.print("Available commands:");
          for (const c of consoleCommands) {
            ctx.print(`  :${c.name} — ${c.summary}`);
          }
        },
      },
      {
        name: "gen",
        summary: "Run a built-in map generator (`:gen <name> k=v ...`)",
        help: "List generators: GET /mapforge/generators. Each generator's params surface as keyword args.",
        complete: (partial: string) => {
          // Static completion list — pulled lazily at first Tab press
          // since the registry is in the sidecar. Mirrors the names
          // registered in `mercwizard_core/mapforge/generators.py`.
          // A future pass can populate this from listGenerators()
          // at console-mount time so it auto-syncs.
          const known = ["wipe", "fill", "rect", "scatter", "cluster", "density-falloff"];
          const lastTok = partial.split(/\s+/).pop() ?? "";
          return known.filter((n) => n.startsWith(lastTok));
        },
        handler: async (parsed, ctx) => {
          if (!session) { ctx.print("No session open.", "warn"); return; }
          if (session.read_only) { ctx.print("Session is read-only.", "warn"); return; }
          const namePos = parsed.args.find((a) => a.kind === "positional");
          if (!namePos) {
            ctx.print("usage: :gen <name> [k=v ...]", "warn");
            try {
              const gens = await listGenerators();
              ctx.print(`available: ${gens.map((g) => g.name).join(", ")}`);
            } catch (err) {
              ctx.print(`(couldn't list: ${err instanceof Error ? err.message : String(err)})`, "warn");
            }
            return;
          }
          const name = namePos.value;
          // Build params dict from keyword args. Type coercion is
          // best-effort: try int → float → leave as string. Booleans
          // surface as "true"/"false" strings; the backend Pydantic
          // model accepts both shapes.
          const params: Record<string, unknown> = {};
          for (const a of parsed.args) {
            if (a.kind !== "keyword") continue;
            const v = a.value;
            const asInt = parseInt(v, 10);
            if (!Number.isNaN(asInt) && String(asInt) === v) {
              params[a.key] = asInt;
            } else {
              const asFloat = parseFloat(v);
              if (!Number.isNaN(asFloat) && /^-?\d+(\.\d+)?$/.test(v)) {
                params[a.key] = asFloat;
              } else if (v === "true" || v === "false") {
                params[a.key] = v === "true";
              } else {
                params[a.key] = v;
              }
            }
          }
          ctx.print(`Running :gen ${name} ${JSON.stringify(params)}…`);
          try {
            // Mirror each emitted op into the local IsoRenderer's
            // parsed dict so the canvas reflects the generator's
            // output. The sidecar applies the op server-side; without
            // this mirror the frontend's IsoRenderer keeps its stale
            // copy and the canvas never refreshes (a user-reported bug:
            // ":gen wipe says 179,200 ops applied but the
            // map still shows trees"). Throttled paint: bump
            // renderEpoch every 1000 ops so the canvas updates
            // visibly during the stream without choking React.
            //
            // beginStroke + endStroke wrap the run so every snapshot
            // recorded inside _mirrorGeneratorOp commits as a single
            // undoable entry. Without this Ctrl+Z would be a no-op
            // (user feedback: "fill worked but ctrl-z isn't undoing
            // it"). Stroke commits in finally so partial-fail runs
            // are still revertible.
            if (renderer) renderer.beginStroke(`:gen ${name}`);
            consoleOpCount.current = 0;
            let final;
            try {
              final = await runGenerator(session.session_id, name, params, (evt) => {
                if ("phase" in evt) {
                  ctx.print(`[${evt.phase}] ${evt.label}`);
                } else if ("op" in evt && renderer) {
                  _mirrorGeneratorOp(renderer, evt.op);
                  consoleOpCount.current += 1;
                  if (consoleOpCount.current % 1000 === 0) {
                    setRenderEpoch((e) => e + 1);
                  }
                }
              });
            } finally {
              if (renderer) renderer.endStroke();
              consoleOpCount.current = 0;
            }
            if (final.ok) {
              ctx.print(`✓ generator '${name}' applied ${final.applied} ops`, "success");
            } else {
              ctx.print(`✕ generator '${name}' failed: ${final.message ?? final.error}`, "error");
            }
            // Resync + paint runs UNCONDITIONALLY when applied > 0 —
            // the per-op mirror has already mutated renderer.parsed
            // for `applied` ops regardless of `ok`, so the canvas
            // MUST repaint to reflect client state. Earlier the bumps
            // lived inside `if (final.ok)` which left partial-fail
            // mirror mutations invisible. (code review finding)
            if (final.applied > 0) {
              if (renderer && session) {
                try {
                  const parsed = await getSessionParsed(session.session_id);
                  renderer.setParsed(parsed);
                } catch (e) {
                  ctx.print(
                    `Warning: resync failed (${e instanceof Error ? e.message : String(e)}). Canvas may be stale until next interaction.`,
                    "warn",
                  );
                }
              }
              setRenderEpoch((e) => e + 1);
              // Sync undoDepth to the renderer's actual stack — the
              // earlier `d + 1` lie was wrong (no snapshots recorded
              // pre-fix). Now beginStroke/endStroke + recordSnapshot
              // make this accurate.
              if (renderer) bumpHistory();
            }
          } catch (err) {
            ctx.print(
              `:gen failed: ${err instanceof Error ? err.message : String(err)}`,
              "error",
            );
          }
        },
      },
    ];
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [session?.session_id, session?.read_only, renderer, datPath, localDirty, undoDepth]);

  // ─── Central hotkey dispatcher ─────────────────────────────────────
  // One window-level keydown listener translates each event into an
  // action via the settings.keybindings map and runs the handler. This
  // replaces the previous hardcoded Ctrl+Z effect — the binding is now
  // rebindable from the settings modal.
  //
  // Why action → handler dispatch instead of per-action effects: with
  // 11 actions we'd otherwise need 11 effects each parsing the event.
  // The action layer also gives the settings modal something coherent
  // to list ("Undo: Ctrl+Z" instead of "Some random keybinding").
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      // Skip when typing into a form field — those have their own
      // undo/save semantics, and we don't want our hotkeys to fire
      // while the user is editing a slot # or filter text.
      const tag = (e.target as HTMLElement | null)?.tagName?.toLowerCase();
      if (tag === "input" || tag === "textarea" || tag === "select") return;
      // `:` opens the command console. Vim-style — single keystroke,
      // no modifier. Runs BEFORE the action dispatcher so users can
      // open the console even if `:` is bound to some other action.
      // The console handles its own Escape/Enter; once open, document-
      // level hotkeys are gated by the tag check above (the input
      // takes focus on open).
      if (e.key === ":" && !e.ctrlKey && !e.metaKey && !e.altKey) {
        e.preventDefault();
        setConsoleOpen(true);
        return;
      }
      // `?` toggles the shortcut cheatsheet. Like `:`, it runs before
      // the action dispatcher so it's always reachable.
      if (e.key === "?" && !e.ctrlKey && !e.metaKey && !e.altKey) {
        e.preventDefault();
        setShowHelp((h) => !h);
        return;
      }
      // Number keys 1-9 arm the matching Favorites brush (Brush Box
      // favorites row). Special-cased here rather than as 9 rebindable
      // registry actions; skipped with any modifier so Ctrl+1 etc. stay
      // free for the browser. 0 is left to the reset-view binding.
      if (/^[1-9]$/.test(e.key) && !e.ctrlKey && !e.metaKey && !e.altKey && !e.shiftKey) {
        const fav = favoritesRef.current[Number(e.key) - 1];
        if (fav) {
          e.preventDefault();
          armBrush(fav);
        }
        return;
      }
      // Encode the event into our canonical combo format + look up.
      const combo = (() => {
        const mods: string[] = [];
        if (e.ctrlKey || e.metaKey) mods.push("Ctrl");
        if (e.altKey) mods.push("Alt");
        if (e.shiftKey) mods.push("Shift");
        if (e.key === "Control" || e.key === "Shift" || e.key === "Alt" || e.key === "Meta") return "";
        let key = e.key;
        if (key.length === 1) key = key.toUpperCase();
        return [...mods, key].join("+");
      })();
      const action: MapForgeActionId | undefined = actionForBinding(settings, combo);
      if (!action) return;
      e.preventDefault();
      switch (action) {
        case "undo":
          if (renderer && session && !session.read_only) {
            undo().then(() => bumpHistory());
          }
          break;
        case "redo":
          if (renderer && session && !session.read_only) {
            redo().then(() => bumpHistory());
          }
          break;
        case "save":
          saveFromHotkey();
          break;
        case "tool-pencil":
          if (activeBrush) setTool("pencil");
          break;
        case "tool-inspect":
          setTool("inspect");
          break;
        case "zoom-in":
          setZoom((z) => Math.min(8, z * 1.15));
          break;
        case "zoom-out":
          setZoom((z) => Math.max(0.25, z / 1.15));
          break;
        case "reset-view":
          setZoom(1);
          setPan({ x: 0, y: 0 });
          break;
        case "toggle-grid":
          setShowGrid((s) => !s);
          break;
        case "toggle-debug":
          setDebugClickHud((s) => !s);
          break;
        case "brush-size-up":
          setBrushRadius((r) => Math.min(8, r + 1));
          break;
        case "brush-size-down":
          setBrushRadius((r) => Math.max(1, r - 1));
          break;
        case "cycle-sub-next":
          cycleSub(1);
          break;
        case "cycle-sub-prev":
          cycleSub(-1);
          break;
        case "open-asset-viewer":
          // Focuses the docked Browse Assets panel. Routed through a
          // ref so this global listener needn't re-bind.
          toggleBrowseAssetsRef.current();
          break;
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
    // Captured-by-closure deps: settings + the various setters and
    // session state. eslint can't see the switch statement's deps
    // statically; we list the load-bearing ones explicitly.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [settings, renderer, session?.session_id, session?.read_only,
      activeBrush, undoDepth, localDirty]);

  // ESC cancels the rectangle corner picker. Separate from the main
  // shortcut effect so it can react instantly when the picker mounts
  // without re-binding every other shortcut.
  useEffect(() => {
    if (!pickingRect) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        e.preventDefault();
        const cb = pickingRect.onCancel;
        setPickingRect(null);
        cb();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [pickingRect]);

  // Zoom: scale around the cursor position (so the point under the
  // mouse stays put while zooming in/out). Without this, the image
  // jumps away from the cursor on zoom — annoying for inspecting an
  // area at high zoom.
  // IMPORTANT: capture rect + cursor BEFORE setZoom/setPan. React's
  // synthetic event becomes invalid (currentTarget=null) after the
  // handler returns — accessing it inside a setState updater blows up
  // with "Cannot read properties of null".
  // Cycle the active tool inspect → pencil → shape (wraps). Driven by the
  // bindable "wheel-cycle-tool" gesture (default = plain scroll).
  function cycleTool(dir: 1 | -1) {
    const order: Tool[] = ["inspect", "pencil", "shape"];
    setTool((t) => {
      const i = order.indexOf(t);
      const n = (((i < 0 ? 0 : i) + dir) % order.length + order.length) % order.length;
      return order[n] ?? t;
    });
  }

  // Canvas wheel → bindable gesture system. By default plain scroll cycles
  // the tool and Alt+scroll zooms around the cursor; both rebind via the
  // settings modal (wheel-cycle-tool / wheel-zoom). preventDefault always so
  // the wheel never bleeds into page/panel scroll over the canvas.
  function onWheel(e: React.WheelEvent<HTMLDivElement>) {
    e.preventDefault();
    const action = actionForBinding(settings, encodeWheelEvent(e));
    if (action === "wheel-cycle-tool") {
      cycleTool(e.deltaY > 0 ? 1 : -1);
      return;
    }
    if (action !== "wheel-zoom") return;  // combo not bound to a wheel gesture
    // Zoom around the cursor. Capture rect + cursor BEFORE setZoom — React's
    // synthetic event is invalid inside the setState updater.
    const delta = e.deltaY < 0 ? 1.15 : 1 / 1.15;
    const container = e.currentTarget.getBoundingClientRect();
    const cx = e.clientX - container.left - container.width / 2;
    const cy = e.clientY - container.top - container.height / 2;
    setZoom((z) => {
      const next = Math.max(0.25, Math.min(8, z * delta));
      const scale = next / z;
      setPan((p) => ({
        x: p.x + (cx - p.x) * (1 - scale),
        y: p.y + (cy - p.y) * (1 - scale),
      }));
      return next;
    });
  }

  function onMouseDown(e: React.MouseEvent<HTMLDivElement>) {
    // Middle-click or alt+left to start a pan. Plain left-click is
    // reserved for tile pinning.
    if (e.button === 1 || (e.button === 0 && e.altKey)) {
      e.preventDefault();
      dragRef.current = {
        startX: e.clientX, startY: e.clientY,
        panX: pan.x, panY: pan.y,
      };
    }
  }
  function onMouseMoveDrag(e: React.MouseEvent<HTMLDivElement>) {
    if (!dragRef.current) return;
    const dx = e.clientX - dragRef.current.startX;
    const dy = e.clientY - dragRef.current.startY;
    setPan({ x: dragRef.current.panX + dx, y: dragRef.current.panY + dy });
  }
  function onMouseUpDrag() {
    dragRef.current = null;
    // Cancel a shape drag that ended (or left the viewport) off-canvas.
    // A release OVER the canvas commits in onCanvasMouseUp before this
    // bubbles, so clearing here is then a harmless no-op.
    if (shapeAnchor !== null) {
      setShapeAnchor(null);
      setShapeCursor(null);
    }
    // Same for an in-progress selection drag that ended off-canvas. A
    // release OVER the canvas commits the rect in onCanvasMouseUp first,
    // clearing selectAnchor, so this is then a harmless no-op. The
    // committed selectRect is left intact.
    if (selectAnchor !== null) {
      setSelectAnchor(null);
      setSelectCursor(null);
    }
  }

  // ─── Live shape preview ─────────────────────────────────────────────
  // Tiles the in-progress shape drag would write. Above ~6000 tiles we
  // preview only the perimeter (cheap) — the commit still fills the
  // whole region.
  const previewTiles = useMemo<Tile[] | null>(() => {
    const cols = info.data?.cols ?? 0;
    const rows = info.data?.rows ?? 0;
    // Region corner-pick (Generate-panel side-trip): live-tint the
    // rectangle between the anchored corner and the cursor so the user
    // sees the region before confirming — overrides any tool preview.
    if (pickingRect?.stage === 1 && pickingRect.corner1 && hovered) {
      let tiles = shapeTiles("rect-fill", pickingRect.corner1, hovered);
      if (tiles.length > 6000) {
        tiles = shapeTiles("rect-outline", pickingRect.corner1, hovered);
      }
      return tiles.filter(
        (t) => t.x >= 0 && t.y >= 0 && t.x < cols && t.y < rows,
      );
    }
    // Building placement (StarCraft-style): the building's w×h footprint
    // rect anchored top-left at the hovered tile — exactly where a click
    // will stamp it. OUTLINE only (not fill): the real sprite ghost on
    // the overlay canvas sits above this, and a fill tint would wash the
    // sprites out (owner feedback — the footprint indicator must not sit
    // on the building art).
    if (placingBuilding && hovered) {
      return shapeTiles(
        "rect-outline",
        { x: hovered.x, y: hovered.y },
        {
          x: hovered.x + placingBuilding.w - 1,
          y: hovered.y + placingBuilding.h - 1,
        },
      ).filter((t) => t.x >= 0 && t.y >= 0 && t.x < cols && t.y < rows);
    }
    // Select tool: marquee (active drag, else the committed rect) and,
    // in paste mode, a ghost of the clipboard footprint at the cursor.
    if (tool === "select") {
      if (pasteMode && clipboard && hovered) {
        if (clipboard.tiles.length > 6000) {
          // Too many to fill cheaply — outline the footprint bbox instead.
          return shapeTiles(
            "rect-outline",
            { x: hovered.x, y: hovered.y },
            { x: hovered.x + clipboard.w - 1, y: hovered.y + clipboard.h - 1 },
          ).filter((t) => t.x >= 0 && t.y >= 0 && t.x < cols && t.y < rows);
        }
        const out: Tile[] = [];
        for (const t of clipboard.tiles) {
          const x = hovered.x + t.dx;
          const y = hovered.y + t.dy;
          if (x >= 0 && y >= 0 && x < cols && y < rows) out.push({ x, y });
        }
        return out;
      }
      const a = selectAnchor ?? selectRect?.a ?? null;
      const b = selectAnchor ? (selectCursor ?? selectAnchor) : (selectRect?.b ?? null);
      if (!a || !b) return null;
      let tiles = shapeTiles("rect-fill", a, b);
      if (tiles.length > 6000) tiles = shapeTiles("rect-outline", a, b);
      return tiles.filter((t) => t.x >= 0 && t.y >= 0 && t.x < cols && t.y < rows);
    }
    if (tool !== "shape" || !shapeAnchor) return null;
    const cursor = shapeCursor ?? shapeAnchor;
    let tiles = shapeTiles(shapeKind, shapeAnchor, cursor);
    if (tiles.length > 6000) {
      tiles = shapeTiles("rect-outline", shapeAnchor, cursor);
    }
    return tiles.filter(
      (t) => t.x >= 0 && t.y >= 0 && t.x < cols && t.y < rows,
    );
  }, [tool, shapeAnchor, shapeCursor, shapeKind, info.data,
      selectAnchor, selectCursor, selectRect, pasteMode, clipboard, hovered,
      pickingRect, placingBuilding]);

  // Dimensions readout for the status bar while a shape drag is active.
  // Count is computed analytically (not from previewTiles, which is
  // capped to the outline for huge fills) so the readout is accurate.
  const previewDims = useMemo<
    { w: number; h: number; count: number; label: string } | null
  >(() => {
    // Region corner-pick: dims of the in-progress region drag.
    if (pickingRect?.stage === 1 && pickingRect.corner1 && hovered) {
      const w = Math.abs(hovered.x - pickingRect.corner1.x) + 1;
      const h = Math.abs(hovered.y - pickingRect.corner1.y) + 1;
      return { w, h, count: w * h, label: "Region" };
    }
    // Building placement: the armed footprint's dims.
    if (placingBuilding) {
      return {
        w: placingBuilding.w,
        h: placingBuilding.h,
        count: placingBuilding.w * placingBuilding.h,
        label: "Building",
      };
    }
    // Select tool: dims of the paste footprint, else the marquee rect.
    if (tool === "select") {
      if (pasteMode && clipboard) {
        return { w: clipboard.w, h: clipboard.h, count: clipboard.tiles.length, label: "Paste" };
      }
      const a = selectAnchor ?? selectRect?.a ?? null;
      const b = selectAnchor ? (selectCursor ?? selectAnchor) : (selectRect?.b ?? null);
      if (!a || !b) return null;
      const w = Math.abs(b.x - a.x) + 1;
      const h = Math.abs(b.y - a.y) + 1;
      return { w, h, count: w * h, label: "Selection" };
    }
    if (tool !== "shape" || !shapeAnchor) return null;
    const cursor = shapeCursor ?? shapeAnchor;
    const w = Math.abs(cursor.x - shapeAnchor.x) + 1;
    const h = Math.abs(cursor.y - shapeAnchor.y) + 1;
    let count: number;
    if (shapeKind === "line") count = Math.max(w, h);
    else if (shapeKind === "rect-outline") {
      count = w === 1 || h === 1 ? w * h : 2 * (w + h) - 4;
    } else if (shapeKind === "rect-fill" || shapeKind === "room") {
      count = w * h;
    } else {
      // diamond / cross / triangle / hexagon — exact count from the geometry
      count = shapeTiles(shapeKind, shapeAnchor, cursor).length;
    }
    return { w, h, count, label: "Shape" };
  }, [tool, shapeAnchor, shapeCursor, shapeKind,
      selectAnchor, selectCursor, selectRect, pasteMode, clipboard,
      pickingRect, hovered, placingBuilding]);

  // ─── Height overlay ─────────────────────────────────────────────────
  // Per-tile heights are invisible in the iso render (neither renderer
  // uses height for Z), so while the height brush is active we overlay the
  // non-zero-height tiles — tinted by height, numbered when zoomed in.
  // Recomputed on every edit (renderEpoch) so it tracks the brush live.
  const heightOverlay = useMemo<Array<{ x: number; y: number; h: number }> | null>(() => {
    // Shown while the height TOOL is active — or while a generator
    // ghost containing heights is up (heights are invisible in the iso
    // render; without this a cliff preview looks like a no-op).
    if ((tool !== "height" && !ghostHasHeights) || !renderer) return null;
    const parsed = renderer.getParsed();
    const heights = parsed?.heights;
    if (!heights) return null;
    const cols = parsed.cols;
    const rows = parsed.rows;
    const out: Array<{ x: number; y: number; h: number }> = [];
    for (let y = 0; y < rows; y++) {
      for (let x = 0; x < cols; x++) {
        const h = heights[y * cols + x] ?? 0;
        if (h > 0) out.push({ x, y, h });
      }
    }
    return out;
  }, [tool, renderer, renderEpoch, ghostHasHeights]);

  function resetView() { setZoom(1); setPan({ x: 0, y: 0 }); }

  const showGridForThisView =
    showGrid &&
    renderMeta !== null &&
    info.data !== undefined &&
    countVisibleTiles(info.data, selectedRoom) <= GRID_MAX_TILES;

  // ─── Dock-panel render closures ──────────────────────────────────────
  // Each returns the content for one dockview panel (via MapForgeDock),
  // closing over editor state so no props/context-shape threading is
  // needed. These are COPIES of the fixed-layout JSX (the fixed grid
  // below is left untouched as the safe fallback); Phase 3 will retire
  // the fixed layout and dedupe. The canvas copy fills its panel
  // (h-full) instead of the fixed layout's 70vh.
  // Browse Assets (dock panel) = the full MapForgePalette brush picker
  // (categorized, searchable, sub-frame picker), wired to set the active
  // brush. This is the brush-picking surface; the read-only cross-tileset
  // "Tileset Viewer" (MapForgeTilesetBrowser) is a SEPARATE panel. The
  // compact rail's "Browse assets" button focuses this panel.
  const renderAssetsPanel = () => (
    xmlPath ? (
      <MapForgeAssetBrowserBody
        xmlPath={xmlPath}
        tileset={tileset}
        renderer={renderer}
        activeBrush={activeBrush}
        onPick={(b) => {
          armBrush(b);
          if (b) {
            log?.append({
              severity: "info",
              message: `Brush: ${b.sti_filename.replace(/\.sti$/i, "")} `
                     + `· slot ${b.slot} sub ${b.sub} → ${b.layer}`,
            });
          }
        }}
        showShadowSlots={settings.showShadowSlots || !settings.autoPairShadows}
        engineMaxTileSlot={settings.engineMaxTileSlot}
      />
    ) : (
      <div className="rounded border border-gray-700 bg-gray-950 p-3 text-xs text-gray-500">
        No Ja2Set.dat.xml — asset browser unavailable
      </div>
    )
  );

  // Tileset Viewer (dock panel) = the read-only cross-tileset browser.
  // Browse ANY tileset's slots (its tileset selector is the value the
  // single-tileset Palette lacks), with details + dedupe. Read-only: no
  // brush, no copy (cross-tileset import is shelved pending the fork
  // design). To paint, use Browse Assets / the Palette.
  const renderTilesetViewerPanel = () => (
    xmlPath ? (
      <MapForgeTilesetBrowser
        xmlPath={xmlPath}
        defaultTileset={tileset}
        activeSectorTileset={tileset}
        readOnly
      />
    ) : (
      <div className="rounded border border-gray-700 bg-gray-950 p-3 text-xs text-gray-500">
        No Ja2Set.dat.xml — tileset viewer unavailable
      </div>
    )
  );

  const renderPalettePanel = () => (
    xmlPath ? (
      <MapForgePaletteRail
        renderer={renderer}
        activeBrush={activeBrush}
        recentBrushes={recentBrushes}
        recentAdditions={recentAdditions}
        favorites={favorites}
        onToggleFavorite={toggleFavorite}
        onPick={(b) => {
          armBrush(b);
          log?.append({
            severity: "info",
            message: `Brush: ${b.sti_filename.replace(/\.sti$/i, "")} `
                   + `· slot ${b.slot} sub ${b.sub} → ${b.layer} (recent)`,
          });
        }}
        onPickAddition={(a) => {
          armBrush({
            slot: a.slot,
            sub: 0,
            category: "Library",
            layer: "land",
            sti_filename: a.sti_filename,
          });
          log?.append({
            severity: "info",
            message: `Brush: ${a.sti_filename.replace(/\.sti$/i, "")} `
                   + `· slot ${a.slot} sub 0 (just-added)`,
          });
        }}
        onOpenInTilesetEditor={(a) => {
          if (localDirty) {
            const ok = window.confirm(
              "You have unsaved edits in this sector. Open Tileset "
              + "Editor anyway and discard them?\n\n"
              + "OK = discard + go. Cancel = stay here so you can Save first."
            );
            if (!ok) return;
          }
          navigate(`/tileset-editor/${a.tileset}?slot=${a.slot}`);
        }}
        onOpenViewer={onBrowseAssets}
        hideBrowseButton
      />
    ) : (
      <div className="rounded border border-gray-700 bg-gray-950 p-3 text-xs text-gray-500">
        No Ja2Set.dat.xml — palette unavailable
      </div>
    )
  );

  const renderCanvasPanel = () => (
    <div
      className="relative h-full w-full overflow-hidden bg-gray-950"
      style={{ cursor: pickingRect || placingBuilding ? "crosshair" : undefined }}
      onWheel={onWheel}
      onMouseDown={onMouseDown}
      onMouseMove={onMouseMoveDrag}
      onMouseUp={onMouseUpDrag}
      onMouseLeave={onMouseUpDrag}
    >
      {pickingRect && (
        <div className="absolute inset-x-0 top-0 z-30 flex items-center justify-between bg-rust-700/95 text-rust-50 px-3 py-2 text-sm border-b border-rust-500 shadow-md pointer-events-none">
          <div className="flex items-center gap-2">
            <span className="font-mono text-xs px-1.5 py-0.5 rounded bg-rust-900/70">
              {pickingRect.stage === 0 ? "1 / 2" : "2 / 2"}
            </span>
            <span>
              {pickingRect.stage === 0
                ? "Drag a box over the region (or click two corners)"
                : `Corner pinned at (${pickingRect.corner1?.x}, ${pickingRect.corner1?.y}) — release or click the opposite corner`}
            </span>
          </div>
          <span className="text-xs text-rust-200">ESC to cancel</span>
        </div>
      )}
      {!pickingRect && placingBuilding && (
        <div className="absolute inset-x-0 top-0 z-30 flex items-center justify-between bg-sky-800/95 text-sky-50 px-3 py-2 text-sm border-b border-sky-500 shadow-md pointer-events-none">
          <span>
            Placing {placingBuilding.w}×{placingBuilding.h} building
            ({placingBuilding.label}) — click to stamp, click again for
            another (each gets its own room)
          </span>
          <span className="text-xs text-sky-200">ESC to cancel</span>
        </div>
      )}
      {!pickingRect && !placingBuilding && ghostActive && (
        <div className="pointer-events-none absolute inset-x-0 top-0 z-30 flex items-center justify-between border-b border-emerald-600 bg-emerald-800/95 px-3 py-2 text-sm text-emerald-50 shadow-md">
          <span>
            Previewing generator output — nothing is applied yet. Adjust
            the sliders to update the ghost.
          </span>
          <span className="text-xs text-emerald-200">
            ✓ Apply or ✕ Clear in the Generate panel
          </span>
        </div>
      )}
      {!info.data && info.isLoading && (
        <p className="absolute inset-0 flex items-center justify-center text-sm text-gray-400">
          Parsing .dat...
        </p>
      )}
      {info.error && (
        <p className="absolute inset-2 text-sm text-red-400">
          {formatApiError(info.error)}
        </p>
      )}
      {sessionError && (
        <p className="absolute inset-2 text-sm text-red-400">
          Failed to open editing session: {sessionError}
        </p>
      )}
      {(isSlfBundled || session?.read_only) && (
        <SlfReadOnlyBanner
          slfUri={datPath}
          onExtracted={(loose_path) => {
            const np = new URLSearchParams(params);
            np.set("dat", loose_path);
            setParams(np);
          }}
        />
      )}
      {rendererLoading && loadPhase && (
        <LoadProgressBar
          phase={loadPhase}
          phasePct={phasePct}
          overallPct={loadOverallPct}
        />
      )}
      {debugClickHud && lastClickDebug && (
        <DebugClickHud d={lastClickDebug} />
      )}
      {renderError && (
        <p className="absolute inset-2 text-sm text-red-400">{renderError}</p>
      )}
      {renderer && renderMeta && (
        <div
          className="absolute left-1/2 top-1/2"
          style={{
            transform: `translate(calc(-50% + ${pan.x}px), calc(-50% + ${pan.y}px)) scale(${zoom})`,
            transformOrigin: "center center",
            cursor: dragRef.current ? "grabbing" : "default",
          }}
        >
          <div className="relative" style={{ width: renderMeta.canvasW, height: renderMeta.canvasH }}>
            {/* Stacking inside this wrapper (explicit z-indexes):
                  z-0  main canvas (sector render)
                  z-10 SVG overlay (grid / footprint outline / markers)
                  z-20 placement ghost canvas — the building's real
                       sprites must render ABOVE the grid mesh.
                All three share the wrapper's CSS pan/zoom transform. */}
            <canvas
              ref={canvasRef}
              className="relative z-0 block cursor-crosshair select-none"
              style={{
                imageRendering: "pixelated",
                width: renderMeta.canvasW,
                height: renderMeta.canvasH,
              }}
              onMouseDown={onCanvasMouseDown}
              onClick={onCanvasClick}
              onMouseMove={onCanvasMove}
              onMouseUp={onCanvasMouseUp}
              onMouseLeave={() => setHovered(null)}
              onContextMenu={onCanvasContextMenu}
            />
            <IsoOverlay
              meta={renderMeta}
              info={info.data}
              selectedRoom={selectedRoom}
              hovered={hovered}
              pinned={pinned}
              previewTiles={previewTiles}
              showGrid={showGridForThisView}
              showRoomLabels={showRoomLabels}
              debugClick={debugClickHud ? lastClickDebug : null}
              stampPreview={(() => {
                if (tool !== "pencil" || !hovered || !activeBrush || !renderer) {
                  return null;
                }
                const fp = renderer.getFootprint(activeBrush.slot);
                if (!fp) return null;
                const willStamp = (settings.paintMode === "stamp") !== shiftHeld;
                if (!willStamp) return null;
                return fp.tiles
                  .filter((t) => t.bX !== 0 || t.bY !== 0)
                  .map((t) => ({
                    x: hovered.x + t.bX,
                    y: hovered.y + t.bY,
                  }));
              })()}
              brushRadiusPreview={(() => {
                // Height brush: plain radius footprint (no brush/stamp logic).
                if (tool === "height") {
                  if (!hovered || brushRadius <= 1) return null;
                  const r = brushRadius - 1;
                  const cols = info.data?.cols ?? 0;
                  const rows = info.data?.rows ?? 0;
                  const tiles: Array<{ x: number; y: number; safe: boolean }> = [];
                  for (let dy = -r; dy <= r; dy++) {
                    for (let dx = -r; dx <= r; dx++) {
                      if (Math.abs(dx) + Math.abs(dy) > r) continue;
                      if (dx === 0 && dy === 0) continue;
                      const tx = hovered.x + dx;
                      const ty = hovered.y + dy;
                      const safe = tx >= 0 && ty >= 0 && tx < cols && ty < rows;
                      tiles.push({ x: tx, y: ty, safe });
                    }
                  }
                  return tiles;
                }
                if (tool !== "pencil" || !hovered || !activeBrush || !renderer) {
                  return null;
                }
                if (brushRadius <= 1) return null;
                const fp = renderer.getFootprint(activeBrush.slot);
                const willStamp = fp !== null
                  && ((settings.paintMode === "stamp") !== shiftHeld);
                if (willStamp) return null;
                const r = brushRadius - 1;
                const cols = info.data?.cols ?? 0;
                const rows = info.data?.rows ?? 0;
                const tiles: Array<{ x: number; y: number; safe: boolean }> = [];
                for (let dy = -r; dy <= r; dy++) {
                  for (let dx = -r; dx <= r; dx++) {
                    if (Math.abs(dx) + Math.abs(dy) > r) continue;
                    if (dx === 0 && dy === 0) continue;
                    const tx = hovered.x + dx;
                    const ty = hovered.y + dy;
                    const safe = tx >= 0 && ty >= 0 && tx < cols && ty < rows;
                    tiles.push({ x: tx, y: ty, safe });
                  }
                }
                return tiles;
              })()}
              heightOverlay={heightOverlay}
            />
            {/* Building-placement sprite ghost — drawn + positioned
                imperatively by the placement-ghost effect. Above the
                grid SVG (z-20 vs z-10) so the building's sprites read
                clearly over the grid mesh; pointer-events pass through
                to the main canvas for hover + the stamp click. */}
            <canvas
              ref={ghostCanvasRef}
              className="pointer-events-none absolute left-0 top-0 z-20"
              style={{ imageRendering: "pixelated", display: "none" }}
            />
          </div>
        </div>
      )}
      <div className="absolute bottom-1 left-2 right-2 z-20 flex items-center justify-between text-xs text-gray-400 pointer-events-none">
        <span>
          {hovered
            ? `Hover: (${hovered.x},${hovered.y})`
            : "Hover the render to preview a tile"}
          {pinned && ` · Pinned: (${pinned.x},${pinned.y})`}
          {previewDims && (
            <span className="ml-2 text-emerald-300">
              · {previewDims.label}: {previewDims.w}×{previewDims.h} = {previewDims.count} tile{previewDims.count === 1 ? "" : "s"}
            </span>
          )}
          {showGrid && !showGridForThisView && (
            <span className="ml-2 text-amber-400">
              · grid hidden ({"too many tiles for this view"})
            </span>
          )}
        </span>
        <span className="text-gray-500">
          Zoom {zoom.toFixed(2)}× · {bindingFor(settings, "wheel-cycle-tool") || "—"} = tool · {bindingFor(settings, "wheel-zoom") || "—"} = zoom · Alt+drag or middle-drag = pan
        </span>
      </div>
    </div>
  );

  const renderInspectorPanel = () => (
    <TileInspectorPanel
      datPath={datPath}
      xmlPath={xmlPath}
      tileset={tileset}
      session={session}
      renderer={renderer}
      renderEpoch={renderEpoch}
      isSlfBundled={isSlfBundled}
      cols={info.data?.cols ?? 160}
      rows={info.data?.rows ?? 160}
      pinned={pinned}
      onPin={setPinned}
      onPickAsBrush={(slot, sub, layer, sti_filename) => {
        armBrush({
          slot, sub, layer,
          category: "(picked from tile)",
          sti_filename,
          forceSingleTile: true,
        });
        log?.append({
          severity: "info",
          message: `Brush ← ${sti_filename} (slot ${slot} sub ${sub} → ${layer}, single-tile)`,
        });
      }}
      onEditApplied={(updatedSession) => {
        setSession(updatedSession);
        setRenderEpoch((e) => e + 1);
        // Inspector edits commit a stroke — sync undo/redo/dirty UI.
        bumpHistory();
      }}
    />
  );

  const renderLogPanel = () => <MapForgeLogFull />;

  const renderVariantsDockPanel = () => {
    // In the dock, the variant grid fills the whole panel and wraps to
    // fit the width — no max-h cap (that's only for the compact header
    // strip), so there's no stranded scrollbar + dead space below.
    const subs = activeBrush && renderer ? renderer.listValidSubs(activeBrush.slot) : [];
    if (!activeBrush || !renderer || subs.length <= 1) {
      return (
        <p className="p-3 text-[11px] italic text-gray-500">
          Pick a multi-sub brush (floor, wall, road…) to see its variants.
        </p>
      );
    }
    return (
      <div className="h-full w-full overflow-y-auto p-2">
        <VariantTileGrid
          subs={subs}
          currentSub={activeBrush.sub}
          slot={activeBrush.slot}
          renderer={renderer}
          onPickSub={(sub) => {
            if (sub === activeBrush.sub) return;
            setActiveBrush({ ...activeBrush, sub });
            log?.append({
              severity: "info",
              message: `Sub ${activeBrush.sub} → ${sub} `
                     + `(${activeBrush.sti_filename.replace(/\.sti$/i, "")})`,
            });
          }}
          tileSize={40}
        />
      </div>
    );
  };

  const dockPanels = {
    canvas: renderCanvasPanel,
    palette: renderPalettePanel,
    assets: renderAssetsPanel,
    tilesetViewer: renderTilesetViewerPanel,
    inspector: renderInspectorPanel,
    variants: renderVariantsDockPanel,
    log: renderLogPanel,
    validate: () => (datPath ? (
      <MapForgeValidateBody
        datPath={datPath}
        xmlPath={xmlPath}
        tileset={tileset}
        sessionId={session?.session_id ?? null}
      />
    ) : null),
    generate: () => (
      <MapForgeGeneratePanel
        sessionId={session?.session_id ?? null}
        renderer={renderer}
        readOnly={session?.read_only ?? false}
        xmlPath={xmlPath}
        tileset={tileset}
        activeBrush={activeBrush}
        pickRegion={pickRegionForPanel}
        applyGhostOps={applyGhostOps}
        clearGhost={clearGhost}
        ghostActive={ghostActive}
        setPlacement={setPlacingBuilding}
        placementActive={placingBuilding !== null}
        onOp={mirrorGeneratorOpThrottled}
        onComplete={genRunComplete}
      />
    ),
  };

  return (
    // MapForge uses the full window width — the iso viewer + inspector
    // need every pixel on a wide monitor. The editor fills the viewport
    // (flex column) so the dock takes all remaining height below the
    // two fixed toolbar rows.
    //
    // onContextMenu prevents the browser's default right-click menu
    // ("Save image", "Copy", etc.) inside the editor. Specific
    // right-click semantics (eyedropper on canvas) are added per-
    // element below. Anything that doesn't have its own handler just
    // suppresses the default menu.
    <MapForgeDockContext.Provider value={{ panels: dockPanels }}>
    <div
      className={
        focusMode
          ? "fixed inset-0 z-40 flex flex-col overflow-hidden bg-gray-950 px-2 py-1"
          : "flex h-screen w-full flex-col overflow-hidden px-4 py-2"
      }
      onContextMenu={(e) => e.preventDefault()}
    >
      {focusMode ? (
        // ─── Focus mode: render-only canvas + a minimal exit strip ────
        <>
          <div className="mb-1 flex items-center gap-2">
            <a
              href="/mapforge"
              className="text-sm text-blue-400 hover:underline"
              onClick={(e) => {
                e.preventDefault();
                if (localDirty) {
                  const ok = window.confirm(
                    "You have unsaved edits in this sector. Leave anyway "
                    + "and discard them?\n\n"
                    + "OK = discard + go. Cancel = stay so you can Save first."
                  );
                  if (!ok) return;
                }
                navigate("/mapforge");
              }}
            >
              ← Map Forge
            </a>
            <button
              type="button"
              onClick={() => setFocusMode(false)}
              title="Exit focus mode — restore the toolbars and panels"
              className="ml-auto rounded border border-gray-700 bg-gray-900 px-2 py-1 text-xs text-gray-300 hover:bg-gray-800 hover:text-gray-100"
            >
              ⛶ Exit focus
            </button>
          </div>
          <div className="min-h-0 flex-1">
            {renderCanvasPanel()}
          </div>
        </>
      ) : (
        <>
          {/* ─── Command bar (fixed row 1) ────────────────────────────
              back-link · map name · dirty dot │ Undo / Redo / Save │
              Generate / Validate / Radar (dock-tab openers) │ Tileset ·
              Room │ spacer │ Panels▾ · Layout▾ · Settings · Help ·
              Focus. ONE accent total: the Save button keeps emerald
              while dirty (the dirty dot echoes it); everything else is
              neutral — groups read via the thin dividers, not hue. */}
          <div className="mb-1 flex flex-wrap items-center gap-2">
            <a
              href="/mapforge"
              className="text-sm text-blue-400 hover:underline"
              onClick={(e) => {
                e.preventDefault();
                if (localDirty) {
                  const ok = window.confirm(
                    "You have unsaved edits in this sector. Leave anyway "
                    + "and discard them?\n\n"
                    + "OK = discard + go. Cancel = stay so you can Save first."
                  );
                  if (!ok) return;
                }
                navigate("/mapforge");
              }}
            >
              ← Map Forge
            </a>
            <h1
              className="max-w-[14rem] truncate font-mono text-sm text-gray-200"
              title={info.data
                ? `${datPath}\n${info.data.rows}×${info.data.cols} · tileset `
                  + `${info.data.tileset_in_header} · ${info.data.rooms.length} rooms`
                : datPath}
            >
              {datPath.split(/[\\/]/).pop()}
            </h1>
            {localDirty && (
              <span
                className="text-xs leading-none text-emerald-400"
                title="Unsaved changes — Save writes them to disk"
              >
                ●
              </span>
            )}
            <ToolbarDivider />
            {session && !session.read_only && renderer && (
              <>
                <UndoButton
                  undoDepth={undoDepth}
                  label={renderer.peekUndoLabel()}
                  onUndo={() => {
                    undo().then(() => bumpHistory());
                  }}
                />
                <RedoButton
                  redoDepth={redoDepth}
                  label={renderer.peekRedoLabel()}
                  onRedo={() => {
                    redo().then(() => bumpHistory());
                  }}
                />
              </>
            )}
            {session && !session.read_only && (
              <SaveButton
                session={session}
                localDirty={localDirty}
                undoDepth={undoDepth}
                savedAtDepth={savedAtDepth}
                onSaved={(updated) => {
                  setSession(updated);
                  setSavedAtDepth(undoDepth);
                  setSavedAtGen(renderer ? renderer.generation() : histGen);
                }}
              />
            )}
            <ToolbarDivider />
            {session && !session.read_only && (
              <button
                type="button"
                onClick={openGeneratePanel}
                title="Open the Generate panel — pick a generator, drag its region on the map, watch the live preview, Apply"
                className="rounded border border-gray-700 bg-gray-900 px-2.5 py-1 text-xs font-medium text-gray-200 hover:bg-gray-800"
              >
                ✨ Generate
              </button>
            )}
            {datPath && (
              <button
                type="button"
                onClick={openValidatePanel}
                title="Pre-flight validate this sector (crash traps, playability, JSD frame match)"
                className="rounded border border-gray-700 bg-gray-900 px-2.5 py-1 text-xs font-medium text-gray-200 hover:bg-gray-800"
              >
                ✓ Validate
              </button>
            )}
            {datPath && xmlPath && (
              <button
                type="button"
                disabled={radarBusy}
                onClick={() => void generateRadarNow()}
                title="Generate the 88×44 minimap STI the engine loads (writes to the install's user profile, above Radarmaps.slf). The preview lands in the Log."
                className="rounded border border-gray-700 bg-gray-900 px-2.5 py-1 text-xs font-medium text-gray-200 hover:bg-gray-800 disabled:opacity-50"
              >
                {radarBusy ? "Radar…" : "🛰 Radar"}
              </button>
            )}
            <ToolbarDivider />
            <span className="text-[10px] text-gray-500">Tileset</span>
            <TilesetSelect
              tilesets={tilesetList.data?.tilesets}
              tileset={tileset}
              onChange={(t) => {
                if (localDirty && t !== tileset) {
                  setPendingTilesetSwitch(t);
                  return;
                }
                const np = new URLSearchParams(params);
                np.set("tileset", String(t));
                setParams(np);
              }}
            />
            <span className="text-[10px] text-gray-500">Room</span>
            <select
              value={selectedRoom === null ? "" : String(selectedRoom)}
              onChange={(e) => {
                const np = new URLSearchParams(params);
                if (e.target.value === "") np.delete("room");
                else np.set("room", e.target.value);
                setParams(np);
              }}
              title="Room scope — render the full sector or zoom to a single room"
              className="rounded border border-gray-700 bg-gray-900 px-2 py-1 text-xs text-gray-200"
            >
              <option value="">— full sector —</option>
              {info.data?.rooms.map((r) => (
                <option key={r.room_id} value={r.room_id}>
                  Room {r.room_id} ({r.tile_count} tiles)
                </option>
              ))}
            </select>
            <span className="flex-1" />
            <ToolbarMenu label="Panels">
              {(() => {
                const openSet = new Set(dockOpenIds);
                const closed = PANEL_ORDER.filter((id) => !openSet.has(id));
                if (closed.length === 0) {
                  return (
                    <ToolbarMenuItem disabled>All panels open</ToolbarMenuItem>
                  );
                }
                return closed.map((id) => (
                  <ToolbarMenuItem
                    key={id}
                    title={`Re-open the ${PANEL_TITLE[id]} panel`}
                    onClick={() => {
                      const api = dockApiRef.current;
                      if (api) reopenDockPanel(api, id);
                    }}
                  >
                    + {PANEL_TITLE[id]}
                  </ToolbarMenuItem>
                ));
              })()}
            </ToolbarMenu>
            <ToolbarMenu label="Layout">
              <ToolbarMenuItem
                title="Discard your saved arrangement and restore the default layout"
                onClick={() => {
                  const api = dockApiRef.current;
                  if (!api) return;
                  resetDockLayout(api);
                  log?.append({
                    severity: "info",
                    message: "Dock layout reset to default.",
                  });
                }}
              >
                Reset layout
              </ToolbarMenuItem>
              <ToolbarMenuItem
                title="Save the current arrangement as your default — Reset layout and fresh sessions open this."
                onClick={() => {
                  const api = dockApiRef.current;
                  if (!api) return;
                  saveUserDefaultLayout(api);
                  log?.append({
                    severity: "success",
                    message: "Current dock arrangement saved as default.",
                  });
                }}
              >
                Set as default
              </ToolbarMenuItem>
            </ToolbarMenu>
            <button
              type="button"
              onClick={() => setSettingsOpen(true)}
              title="MapForge settings — hotkeys, defaults, engine cap, etc."
              className="rounded border border-gray-700 bg-gray-900 px-2 py-1 text-xs text-gray-300 hover:bg-gray-800 hover:text-gray-100"
            >
              ⚙ Settings
            </button>
            <button
              type="button"
              onClick={() => setShowHelp(true)}
              title="Controls & shortcuts (?)"
              className="rounded border border-gray-700 bg-gray-900 px-2 py-1 text-xs text-gray-300 hover:bg-gray-800 hover:text-gray-100"
            >
              ? Help
            </button>
            <button
              type="button"
              onClick={() => setFocusMode(true)}
              title="Focus mode — show ONLY the map render (no toolbars or panels). Click ⛶ Exit focus to return."
              className="rounded border border-gray-700 bg-gray-900 px-2 py-1 text-xs text-gray-300 hover:bg-gray-800 hover:text-gray-100"
            >
              ⛶ Focus
            </button>
          </div>

          {/* ─── Tool options bar (fixed row 2) ───────────────────────
              ToolSelector │ BrushChip │ per-tool options │ spacer │
              layer visibility │ Grid · R# · Reset view. */}
          <div className="mb-1 flex flex-wrap items-end gap-3">
            <ToolSelector
              tool={tool} setTool={setTool}
              hasBrush={activeBrush !== null}
            />
            <BrushChip
              brush={activeBrush}
              renderer={renderer}
              onClear={() => {
                setActiveBrush(null);
                log?.append({ severity: "info", message: "Brush cleared." });
              }}
            />
            <BrushOptions
              tool={tool}
              activeBrush={activeBrush}
              paintLayer={paintLayer}
              setPaintLayer={setPaintLayer}
              brushRadius={brushRadius}
              setBrushRadius={setBrushRadius}
            />
            <ShapeOptions
              tool={tool}
              shapeKind={shapeKind}
              setShapeKind={setShapeKind}
              activeBrush={activeBrush}
              hasBrush={activeBrush !== null}
              paintLayer={paintLayer}
              setPaintLayer={setPaintLayer}
              roomId={roomId}
              setRoomId={setRoomId}
              rooms={info.data?.rooms ?? []}
              suggestedRoomId={
                (info.data?.rooms.reduce((m, r) => Math.max(m, r.room_id), 0) ?? 0) + 1
              }
            />
            <SelectOptions
              tool={tool}
              hasSelection={selectRect !== null}
              clipboard={clipboard}
              pasteMode={pasteMode}
              readOnly={session?.read_only ?? false}
              activeTileset={tileset}
              busy={editsInFlight > 0}
              onCopy={() => void doCopy()}
              onArmPaste={() => setPasteMode(true)}
              onCancelPaste={() => setPasteMode(false)}
            />
            <HeightOptions
              tool={tool}
              heightMode={heightMode}
              setHeightMode={setHeightMode}
              heightValue={heightValue}
              setHeightValue={setHeightValue}
            />
            <span className="flex-1" />
            <LayerVisibilityToggles
              hiddenLayers={hiddenLayers}
              setHiddenLayers={setHiddenLayers}
            />
            <div className="flex items-end gap-1">
              <button
                type="button"
                onClick={() => setShowGrid((s) => !s)}
                className={`rounded border px-2 py-1 text-xs ${
                  showGrid
                    ? showGridForThisView
                      ? "border-gray-500 bg-gray-700 text-gray-100"
                      : "border-gray-600 bg-gray-800 text-gray-500"
                    : "border-gray-700 bg-gray-900 text-gray-300 hover:bg-gray-800"
                }`}
                title={
                  showGrid && !showGridForThisView
                    ? "Grid is enabled but auto-hidden for this view (too many tiles). Pick a room from the dropdown to see it."
                    : "Toggle diamond tile grid"
                }
              >
                Grid{showGrid && !showGridForThisView ? " (n/a)" : ""}
              </button>
              <button
                type="button"
                onClick={() => setShowRoomLabels((s) => !s)}
                className={`rounded border px-2 py-1 text-xs ${
                  showRoomLabels
                    ? "border-gray-500 bg-gray-700 text-gray-100"
                    : "border-gray-700 bg-gray-900 text-gray-300 hover:bg-gray-800"
                }`}
                title="Toggle room number labels"
              >
                R#
              </button>
              <button
                type="button"
                onClick={resetView}
                className="rounded border border-gray-700 bg-gray-900 px-2 py-1 text-xs text-gray-300 hover:bg-gray-800"
                title={`Reset zoom (currently ${zoom.toFixed(2)}×) and pan`}
              >
                Reset view
              </button>
            </div>
          </div>

          {/* ─── The dock — fills all remaining height ───────────────── */}
          <div className="min-h-0 flex-1">
            <MapForgeDock
              onApi={(api) => { dockApiRef.current = api; }}
              onOpenPanelsChange={setDockOpenIds}
            />
          </div>
        </>
      )}

      {/* Settings modal — mounted at the top level of the editor so
          its fixed-position overlay clears every other UI layer. */}
      {settingsOpen && (
        <MapForgeSettingsModal
          settings={settings}
          onChange={setSettings}
          onClose={() => setSettingsOpen(false)}
        />
      )}

      {/* Command console — vim-style `:` bar. Mounted top-level so its
          fixed-bottom positioning doesn't fight other layers. The
          console handles its own Escape; clicking outside also closes
          it via the onClose passed here. Task #114. */}
      <MapForgeConsole
        open={consoleOpen}
        onClose={() => setConsoleOpen(false)}
        commands={consoleCommands}
      />

      {/* Tileset-switch guard — re-opening the session on a new tileset
          discards every unsaved edit. A user hit this when
          experimenting with generators across tilesets. */}
      <ConfirmModal
        open={pendingTilesetSwitch !== null}
        title="Discard unsaved changes?"
        body={
          <>
            Switching tilesets re-opens the sector and reverts it to the
            on-disk version. <strong>Any unsaved generator output,
            paint strokes, or other edits will be lost.</strong>
            <br /><br />
            Save the sector first, or confirm to discard.
          </>
        }
        confirmLabel="Discard and switch"
        cancelLabel="Cancel"
        destructive
        onCancel={() => setPendingTilesetSwitch(null)}
        onConfirm={() => {
          const t = pendingTilesetSwitch;
          if (t === null) return;
          setPendingTilesetSwitch(null);
          const np = new URLSearchParams(params);
          np.set("tileset", String(t));
          setParams(np);
        }}
      />

      {/* `?` shortcut cheatsheet — reachable from the Help button in
          the command bar and the `?` key. */}
      <MapForgeHelpOverlay
        open={showHelp}
        onClose={() => setShowHelp(false)}
        settings={settings}
      />

      {/* Demo caption bar (?demo=1 only) — narration captions for the
          scripted demo runner. Bottom-center dark pill, large readable
          text, above every editor layer, never intercepts the mouse. */}
      {demoMode && demoCaption !== null && (
        <div className="pointer-events-none fixed inset-x-0 bottom-12 z-50 flex justify-center">
          <div
            className="max-w-[70vw] rounded-full border border-gray-600 bg-gray-950/90 px-8 py-3 text-center font-semibold text-gray-50 shadow-2xl"
            style={{ fontSize: 24, lineHeight: 1.35 }}
          >
            {demoCaption}
          </div>
        </div>
      )}

    </div>
    </MapForgeDockContext.Provider>
  );
}

// ─── Helpers ──────────────────────────────────────────────────────────
function countVisibleTiles(info: SectorInfo, selectedRoom: number | null): number {
  if (selectedRoom === null) return info.rows * info.cols;
  const room = info.rooms.find((r) => r.room_id === selectedRoom);
  if (!room) return 0;
  const [x0, y0, x1, y1] = room.bbox;
  // The renderer expands by ring=5; cap at sector bounds.
  const rx0 = Math.max(0, x0 - 5);
  const ry0 = Math.max(0, y0 - 5);
  const rx1 = Math.min(info.cols - 1, x1 + 5);
  const ry1 = Math.min(info.rows - 1, y1 + 5);
  return (rx1 - rx0 + 1) * (ry1 - ry0 + 1);
}

// ─── SVG overlay ──────────────────────────────────────────────────────
function IsoOverlay({
  meta,
  info,
  selectedRoom,
  hovered,
  pinned,
  previewTiles,
  showGrid,
  showRoomLabels,
  debugClick,
  stampPreview,
  brushRadiusPreview,
  heightOverlay,
}: {
  meta: RenderMeta;
  info: SectorInfo | undefined;
  selectedRoom: number | null;
  hovered: { x: number; y: number } | null;
  pinned: { x: number; y: number } | null;
  /** Tiles the in-progress shape drag would write — drawn as one tinted
   * fill path. Null when no shape drag is active. */
  previewTiles: Tile[] | null;
  showGrid: boolean;
  showRoomLabels: boolean;
  debugClick: {
    px: number; py: number;
    tile: { x: number; y: number } | null;
  } | null;
  /** Footprint tiles to outline when previewing a multi-tile stamp.
   * Null when the user isn't about to stamp anything (single-tile
   * brush, inspect tool, manual mode, etc.). Anchor tile is omitted
   * from this list — it's drawn by the existing `hovered` marker
   * with a brighter tint so the user can tell which tile is the
   * anchor. */
  stampPreview: Array<{ x: number; y: number }> | null;
  /** Brush-radius footprint preview. Drawn when the user has a
   * radius > 1 brush; each tile gets a safety check (in-bounds, etc.)
   * so the user can see what they'll paint AND whether any tiles will
   * be clipped. safe=false → red outline. */
  brushRadiusPreview: Array<{ x: number; y: number; safe: boolean }> | null;
  /** Non-zero-height tiles to overlay while the height brush is active —
   * tinted by height, numbered when zoomed in. Null for other tools. */
  heightOverlay: Array<{ x: number; y: number; h: number }> | null;
}) {
  // Compute the tile rect being rendered (mirrors IsoRenderer._resolve_region).
  const rect = useMemo(() => {
    if (!info) return null;
    if (selectedRoom !== null) {
      const room = info.rooms.find((r) => r.room_id === selectedRoom);
      if (room) {
        const [x0, y0, x1, y1] = room.bbox;
        return {
          x0: Math.max(0, x0 - 5),
          y0: Math.max(0, y0 - 5),
          x1: Math.min(info.cols - 1, x1 + 5),
          y1: Math.min(info.rows - 1, y1 + 5),
        };
      }
    }
    return { x0: 0, y0: 0, x1: info.cols - 1, y1: info.rows - 1 };
  }, [info, selectedRoom]);

  // Single SVG <path d="..."/> with subpath-per-tile. Far cheaper than
  // emitting one <polygon> per tile (the React/SVG layout pass on 25k
  // separate polygons was the perf bottleneck behind the previous
  // tile-count cap). One path renders ~100k subpaths comfortably.
  const gridPath = useMemo(() => {
    if (!showGrid || !rect) return null;
    const parts: string[] = [];
    for (let ty = rect.y0; ty <= rect.y1; ty++) {
      for (let tx = rect.x0; tx <= rect.x1; tx++) {
        const c = tileDiamondCorners(tx, ty, meta);
        parts.push(
          `M${c[0][0]} ${c[0][1]}L${c[1][0]} ${c[1][1]}L${c[2][0]} ${c[2][1]}L${c[3][0]} ${c[3][1]}Z`
        );
      }
    }
    return parts.join("");
  }, [showGrid, rect, meta]);

  // Live shape-drag preview — one filled <path> over the tiles the shape
  // would write. Same single-path technique as the grid so even a
  // full-sector preview stays cheap to rebuild while dragging.
  const previewPath = useMemo(() => {
    if (!previewTiles || previewTiles.length === 0) return null;
    const parts: string[] = [];
    for (const t of previewTiles) {
      const c = tileDiamondCorners(t.x, t.y, meta);
      parts.push(
        `M${c[0][0]} ${c[0][1]}L${c[1][0]} ${c[1][1]}L${c[2][0]} ${c[2][1]}L${c[3][0]} ${c[3][1]}Z`,
      );
    }
    return parts.join("");
  }, [previewTiles, meta]);

  const roomLabels = useMemo(() => {
    if (!showRoomLabels || !info) return null;
    return info.rooms.map((r) => {
      const cx = (r.bbox[0] + r.bbox[2]) / 2;
      const cy = (r.bbox[1] + r.bbox[3]) / 2;
      const { x: sx, y: sy } = tileToCanvasPixel(cx, cy, meta);
      // Center the label horizontally on the diamond, not on the
      // bbox top-left (sx is the west-apex column).
      const x = sx + meta.tileW / 2;
      // Lift the label above the building's roof. The roof STIs draw
      // ~60 px above the tile's top (oy ≈ -10 + WALL_HEIGHT 50 lift).
      return { room: r, x, y: sy - 60 };
    });
  }, [showRoomLabels, info, meta]);

  return (
    // z-10: above the main canvas (z-0), BELOW the placement ghost
    // canvas (z-20) — the building ghost's sprites must beat the grid.
    <svg
      className="pointer-events-none absolute inset-0 z-10"
      width={meta.canvasW}
      height={meta.canvasH}
      viewBox={`0 0 ${meta.canvasW} ${meta.canvasH}`}
      shapeRendering="crispEdges"
    >
      {gridPath && (
        <path
          d={gridPath}
          stroke="rgba(120,200,255,0.45)"
          fill="none"
          strokeWidth={1}
          vectorEffect="non-scaling-stroke"
        />
      )}

      {previewPath && (
        <path
          d={previewPath}
          fill="rgba(80,255,160,0.30)"
          stroke="rgba(80,255,160,0.9)"
          strokeWidth={1}
          vectorEffect="non-scaling-stroke"
        />
      )}

      {/* Height overlay — only while the height brush is active. Tint each
          non-zero tile by its height (opacity ramped to the max present),
          and draw the value when zoomed in enough to read + the count is
          modest. Capped at 4000 nodes so a fully-sculpted map can't emit
          25k SVG elements. */}
      {heightOverlay && heightOverlay.length > 0 && (() => {
        const maxH = heightOverlay.reduce((m, t) => Math.max(m, t.h), 1);
        const labels = meta.tileW >= 22 && heightOverlay.length <= 1200;
        return (
          <g>
            {heightOverlay.slice(0, 4000).map((t) => (
              <TileMarker
                key={`h-${t.x},${t.y}`}
                tile={t} meta={meta}
                fill={`rgba(255,150,40,${(0.15 + (t.h / maxH) * 0.45).toFixed(3)})`}
                stroke="rgba(255,170,60,0.55)"
                strokeWidth={0.5}
              />
            ))}
            {labels && heightOverlay.map((t) => {
              const p = tileToCanvasPixel(t.x, t.y, meta);
              return (
                <text
                  key={`ht-${t.x},${t.y}`}
                  x={p.x}
                  y={p.y - meta.tileH / 2}
                  textAnchor="middle"
                  dominantBaseline="middle"
                  fontSize={9}
                  fill="#fff"
                  stroke="#000"
                  strokeWidth={0.5}
                  style={{ paintOrder: "stroke" }}
                >
                  {t.h}
                </text>
              );
            })}
          </g>
        );
      })()}
      {hovered && (
        <TileMarker tile={hovered} meta={meta}
          fill="rgba(120,220,255,0.22)" stroke="rgba(120,220,255,0.85)" />
      )}
      {/* Brush-radius footprint preview — green for tiles inside the
          sector, red for tiles that would be clipped. Drawn UNDER the
          stamp preview so multi-tile-struct stamps still show their
          amber outline on top of the brush-radius indication. */}
      {brushRadiusPreview && brushRadiusPreview.length > 0 && (
        <g>
          {brushRadiusPreview.map((t, i) => (
            <TileMarker
              key={`bp-${t.x},${t.y},${i}`}
              tile={t} meta={meta}
              fill={t.safe
                ? "rgba(80,220,120,0.10)"
                : "rgba(255,80,80,0.18)"}
              stroke={t.safe
                ? "rgba(80,220,120,0.60)"
                : "rgba(255,80,80,0.85)"}
              strokeWidth={1}
            />
          ))}
        </g>
      )}
      {/* Multi-tile stamp footprint preview — amber outline at every
          tile the next click will stamp. The anchor (hovered) tile
          stays cyan via the marker above; the rest get a dimmer
          amber so the user can see the difference. Rendered BEFORE
          the pinned marker so pinned (yellow, high-saturation) wins
          on overlap. */}
      {stampPreview && stampPreview.length > 0 && (
        <g>
          {stampPreview.map((t, i) => (
            <TileMarker key={`${t.x},${t.y},${i}`}
              tile={t} meta={meta}
              fill="rgba(255,180,60,0.18)" stroke="rgba(255,180,60,0.85)"
              strokeWidth={1.5} />
          ))}
        </g>
      )}
      {pinned && (
        <TileMarker tile={pinned} meta={meta}
          fill="rgba(255,210,90,0.28)" stroke="rgba(255,210,90,1)" strokeWidth={2} />
      )}

      {roomLabels && (
        <g>
          {roomLabels.map(({ room, x, y }) => (
            <RoomLabel key={room.room_id} room={room} x={x} y={y} />
          ))}
        </g>
      )}

      {/* Debug click markers — only when the debug toggle is on. The
          red dot is the actual canvas-pixel click point. The yellow
          outline is the diamond of the tile imagePixelToTile resolved
          to. The green dot is THAT tile's diamond center. If everything
          is aligned, the red dot should sit inside the yellow diamond
          and close to the green dot. If the red dot is in one diamond
          but the yellow outline is around a neighbor, the inversion
          formula is wrong. */}
      {debugClick && (
        <g>
          {debugClick.tile && (
            <>
              <TileMarker tile={debugClick.tile} meta={meta}
                fill="rgba(255, 200, 0, 0.15)"
                stroke="rgba(255, 200, 0, 0.95)"
                strokeWidth={2} />
              {(() => {
                const sa = tileToCanvasPixel(
                  debugClick.tile.x, debugClick.tile.y, meta);
                const cy = sa.y - meta.tileH / 2;
                return (
                  <circle cx={sa.x} cy={cy} r={3}
                    fill="rgba(80, 255, 120, 0.95)"
                    stroke="rgba(0, 0, 0, 0.8)" strokeWidth={1} />
                );
              })()}
            </>
          )}
          <circle cx={debugClick.px} cy={debugClick.py} r={4}
            fill="rgba(255, 60, 60, 0.95)"
            stroke="rgba(255, 255, 255, 0.95)" strokeWidth={1.5} />
        </g>
      )}
    </svg>
  );
}

function TileMarker({
  tile, meta, fill, stroke, strokeWidth = 1.5,
}: {
  tile: { x: number; y: number };
  meta: RenderMeta;
  fill: string;
  stroke: string;
  strokeWidth?: number;
}) {
  const points = tileDiamondCorners(tile.x, tile.y, meta)
    .map((p) => p.join(",")).join(" ");
  return <polygon points={points} fill={fill} stroke={stroke} strokeWidth={strokeWidth} />;
}

function RoomLabel({ room, x, y }: { room: RoomSummary; x: number; y: number }) {
  const text = `R${room.room_id}`;
  const halfW = text.length * 4.5 + 4;
  return (
    <g>
      <rect
        x={x - halfW} y={y - 9}
        width={halfW * 2} height={16}
        rx={3} ry={3}
        fill="rgba(0,0,0,0.72)" stroke="rgba(255,210,90,0.6)" strokeWidth={1}
      />
      <text
        x={x} y={y + 3}
        fontSize={11} fontFamily="monospace"
        fill="rgb(255,220,120)" textAnchor="middle"
      >
        {text}
      </text>
    </g>
  );
}

// ─── SLF read-only banner with "Extract for editing" action ──────────
// SLF-bundled sectors open as read-only sessions (paint/save disabled).
// JA2's VFS makes loose `.dat` files in Data-1.13/Maps/ shadow the
// SLF version transparently — so extracting once + editing the loose
// copy is the standard workflow. No repacking needed.
function SlfReadOnlyBanner({
  slfUri, onExtracted,
}: {
  slfUri: string;
  onExtracted: (loosePath: string) => void;
}) {
  const log = useMapForgeLog();
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  // Preview the destination BEFORE the user clicks Extract so they
  // can see which VFS profile + layer the loose copy will land in.
  // Catches the H4-saga case where the install's running VFS doesn't
  // mount the layer MapForge would write to.
  const preview = useQuery({
    queryKey: ["mapforge", "extract-slf-preview", slfUri],
    queryFn: () => previewExtractSlfToLoose(slfUri),
    staleTime: 60 * 1000,
    retry: false,
  });
  const handleExtract = async () => {
    setBusy(true); setErr(null);
    try {
      const res = await extractSlfToLoose(slfUri);
      log?.append({
        severity: "success",
        message: `Extracted to ${res.target_profile ?? "loose"} layer`,
        detail: res.loose_path,
      });
      onExtracted(res.loose_path);
    } catch (e) {
      const msg = e instanceof Error ? e.message : String(e);
      setErr(msg);
      log?.append({
        severity: "error",
        message: "SLF extract failed",
        detail: msg,
      });
    } finally {
      setBusy(false);
    }
  };
  const p = preview.data;
  const fellBackToHeuristic = p?.target_layer_source === "heuristic-fallback";
  return (
    <div className="absolute top-2 left-2 z-10 max-w-md rounded border border-amber-800 bg-amber-950/90 px-3 py-2 text-[11px] text-amber-200">
      <div className="font-semibold">SLF-bundled — read-only</div>
      <p className="mt-0.5 text-amber-300/80">
        This sector lives inside an SLF archive. JA2's VFS lets loose
        files override SLF entries — extract once and edit the loose
        copy. No SLF repack needed.
      </p>

      {/* Destination preview — surfaces the resolved write path + VFS
          profile so the user knows where the file will land BEFORE
          they commit. Bug #61 in the internal followup list. */}
      {p && (
        <div className={`mt-2 rounded px-2 py-1.5 text-[10px] ${
          fellBackToHeuristic
            ? "border border-red-700/60 bg-red-950/40 text-red-200"
            : "border border-amber-700/40 bg-amber-900/30 text-amber-100"
        }`}>
          <div>
            Will write to:{" "}
            <code className="font-mono">{p.proposed_loose_path}</code>
          </div>
          {p.target_profile && (
            <div className="text-amber-300/80">
              VFS profile:{" "}
              <code className="font-mono">{p.target_profile}</code>
              {fellBackToHeuristic && (
                <span className="ml-2 text-red-300">
                  ⚠ VFS introspection failed — using heuristic. The
                  engine may not read from this layer.
                </span>
              )}
            </div>
          )}
          {p.already_exists && (
            <div className="mt-0.5 text-red-300">
              ⚠ A file already exists at this path. Extract will refuse.
              Rename or delete it first.
            </div>
          )}
        </div>
      )}

      {err && (
        <p className="mt-1 rounded bg-red-950/60 px-2 py-1 text-[10px] text-red-200">
          {err}
        </p>
      )}
      <button
        type="button"
        onClick={handleExtract}
        disabled={busy || p?.already_exists}
        title="Copy this sector .dat out of its SLF library into a loose file on disk so MapForge can save edits to it. The SLF stays untouched. The destination is resolved via the install's active VFS config."
        className="mt-2 rounded border border-amber-600 bg-amber-800 px-3 py-1 text-[11px] text-amber-50 hover:bg-amber-700 disabled:opacity-50"
      >
        {busy ? "Extracting…" : "Extract to loose for editing"}
      </button>
    </div>
  );
}

// ─── Debug click HUD ──────────────────────────────────────────────────
// Shown when the user turns on the "Debug" toggle. Surfaces the full
// chain of values that pixelToTile computes for each click — the same
// data that gets dumped to console.log. Use this to diagnose grid /
// paint misalignment without needing devtools open.
function DebugClickHud({
  d,
}: {
  d: {
    clientX: number; clientY: number;
    rectLeft: number; rectTop: number; rectW: number; rectH: number;
    canvasW: number; canvasH: number;
    px: number; py: number;
    tile: { x: number; y: number } | null;
    southApex?: { x: number; y: number };
    diamondCenter?: { x: number; y: number };
  };
}) {
  const fmt = (n: number) => n.toFixed(1);
  return (
    <div className="pointer-events-none absolute left-2 bottom-8 z-10 max-w-sm rounded border border-purple-700 bg-purple-950/90 p-2 font-mono text-[10px] text-purple-100 shadow-lg">
      <div className="mb-1 font-bold text-purple-300">click → tile diagnostic</div>
      <div>
        client = ({fmt(d.clientX)}, {fmt(d.clientY)})
      </div>
      <div>
        rect = ({fmt(d.rectLeft)}, {fmt(d.rectTop)}) {fmt(d.rectW)}×{fmt(d.rectH)}
      </div>
      <div>
        canvas backing = {d.canvasW}×{d.canvasH}
        {" "}<span className="text-purple-400">
          (scale = {fmt(d.canvasW / d.rectW)})
        </span>
      </div>
      <div>
        canvas px = ({fmt(d.px)}, {fmt(d.py)})
      </div>
      <div className="text-purple-300">
        resolved tile = {d.tile ? `(${d.tile.x}, ${d.tile.y})` : "OUT OF BOUNDS"}
      </div>
      {d.southApex && (
        <div>
          tile S-apex = ({fmt(d.southApex.x)}, {fmt(d.southApex.y)})
        </div>
      )}
      {d.diamondCenter && (
        <div>
          tile center = ({fmt(d.diamondCenter.x)}, {fmt(d.diamondCenter.y)})
          {" "}<span className="text-purple-400">
            (Δ from click = {fmt(d.px - d.diamondCenter.x)},
            {fmt(d.py - d.diamondCenter.y)})
          </span>
        </div>
      )}
    </div>
  );
}

// ─── Tool selector ────────────────────────────────────────────────────
const ALL_LAYERS: LayerName[] = [
  "land", "objs", "shadows", "structs", "roofs", "onroofs",
];
const LAYER_SHORT: Record<LayerName, string> = {
  land: "Land", objs: "Obj", shadows: "Shad",
  structs: "Struct", roofs: "Roof", onroofs: "OnRf",
};

// Shape-kind segmented control options + per-kind tooltip.
const SHAPE_KINDS: ReadonlyArray<{ kind: ShapeKind; label: string }> = [
  { kind: "rect-fill", label: "▦ Fill" },
  { kind: "rect-outline", label: "▢ Outline" },
  { kind: "line", label: "╱ Line" },
  { kind: "diamond", label: "◆ Diamond" },
  { kind: "cross", label: "✛ Cross" },
  { kind: "triangle", label: "▲ Triangle" },
  { kind: "hexagon", label: "⬡ Hex" },
  { kind: "room", label: "⌂ Room" },
];
const SHAPE_HINTS: Record<ShapeKind, string> = {
  "rect-fill": "Drag a box → fill the whole area with the active tile",
  "rect-outline": "Drag a box → paint just the perimeter (walls / fences)",
  "line": "Drag A→B → a straight run of the active tile (roads, fences)",
  "diamond": "Drag a box → filled diamond inscribed in it",
  "cross": "Drag a box → a plus/cross through the center",
  "triangle": "Drag a box → filled triangle, apex at top",
  "hexagon": "Drag a box → filled flat-top hexagon",
  "room": "Drag a box → mark it as a room (engine hides the roof inside)",
};

function ToolSelector({
  tool, setTool, hasBrush,
}: {
  tool: Tool;
  setTool: (t: Tool) => void;
  hasBrush: boolean;
}) {
  // Minimal — just the Inspect/Pencil/Shape toggle. Pencil-only controls
  // (Paint to layer, Brush size) live in BrushOptions; shape controls in
  // ShapeOptions — so this component's width is constant across tools.
  const activeClass: Record<Tool, string> = {
    inspect: "bg-blue-900 text-blue-100",
    pencil: "bg-emerald-900 text-emerald-100",
    shape: "bg-teal-900 text-teal-100",
    select: "bg-purple-900 text-purple-100",
    height: "bg-orange-900 text-orange-100",
  };
  const toolLabel: Record<Tool, string> = {
    inspect: "⌖ Inspect", pencil: "✎ Pencil", shape: "▦ Shape", select: "⬚ Select",
    height: "⛰ Height",
  };
  return (
    <div>
      <span className="block text-xs text-gray-400">Tool</span>
      <div className="flex overflow-hidden rounded border border-gray-700">
        {(["inspect", "pencil", "shape", "select", "height"] as const).map((t) => {
          // Pencil requires a brush; the shape tool's room sub-tool works
          // without one, and select/inspect/height need no brush — so only
          // pencil is ever hard-disabled here.
          const disabled = t === "pencil" && !hasBrush;
          return (
            <button
              key={t}
              type="button"
              disabled={disabled}
              onClick={() => setTool(t)}
              className={`px-2 py-1 text-xs ${
                tool === t
                  ? activeClass[t]
                  : "bg-gray-900 text-gray-300 hover:bg-gray-800"
              } disabled:cursor-not-allowed disabled:opacity-40`}
              title={
                t === "pencil" && !hasBrush
                  ? "Pick a tile from the palette first"
                  : t === "pencil"
                    ? "Paint the active brush (click or drag)"
                    : t === "shape"
                      ? "Drag to define a rectangle / line / room"
                      : t === "select"
                        ? "Drag a rectangle to copy a region; paste it elsewhere"
                        : t === "height"
                          ? "Raise / lower / set per-tile terrain height (drag to sculpt)"
                          : "Click tiles to inspect them"
              }
            >
              {toolLabel[t]}
            </button>
          );
        })}
      </div>
    </div>
  );
}

/** Pencil-mode-only controls (Paint to layer + Brush size). Hidden
 * in inspect mode so the toolbar doesn't change width when switching
 * tools. Positioned in the toolbar alongside BrushChip — these are
 * "what to do with the brush", which belongs visually next to the
 * brush itself, not next to the Tool toggle. */
function BrushOptions({
  tool, activeBrush, paintLayer, setPaintLayer, brushRadius, setBrushRadius,
}: {
  tool: Tool;
  activeBrush: ActiveBrush | null;
  paintLayer: LayerName | null;
  setPaintLayer: (l: LayerName | null) => void;
  brushRadius: number;
  setBrushRadius: (r: number) => void;
}) {
  if (tool !== "pencil") return null;
  return (
    <div className="flex items-end gap-2">
      <div>
        <span className="block text-xs text-gray-400">Paint to layer</span>
        <select
          value={paintLayer ?? ""}
          onChange={(e) =>
            setPaintLayer(e.target.value === "" ? null : e.target.value as LayerName)
          }
          className="rounded border border-gray-700 bg-gray-900 px-2 py-1 text-xs"
          title="Override which layer the pencil paints into. Default = picked from the brush's category."
        >
          <option value="">
            auto ({activeBrush?.layer ?? "—"})
          </option>
          {ALL_LAYERS.map((l) => (
            <option key={l} value={l}>{LAYER_SHORT[l]}</option>
          ))}
        </select>
      </div>
      <div>
        <span className="block text-xs text-gray-400">
          Brush size: <span className="font-mono text-gray-200">{brushRadius}</span>
          <span className="text-gray-500">
            {" "}({brushRadius === 1
              ? "single tile"
              : `diamond Ø${brushRadius * 2 - 1}, `
                + `${1 + 2 * brushRadius * (brushRadius - 1)} tiles`})
          </span>
        </span>
        <input
          type="range"
          min={1} max={8}
          value={brushRadius}
          onChange={(e) => setBrushRadius(parseInt(e.target.value, 10) || 1)}
          className="w-32 align-middle"
          title="Brush radius in tiles. The footprint is a diamond (Manhattan) so it looks symmetric on the iso grid."
        />
      </div>
    </div>
  );
}

/** Shape-mode-only controls: which shape to draw + its target (a layer
 * for fill/outline/line, or a room id for the room tool). Hidden in other
 * tools so the toolbar width stays stable. Mirrors BrushOptions. */
function ShapeOptions({
  tool, shapeKind, setShapeKind, activeBrush, hasBrush,
  paintLayer, setPaintLayer, roomId, setRoomId, rooms, suggestedRoomId,
}: {
  tool: Tool;
  shapeKind: ShapeKind;
  setShapeKind: (k: ShapeKind) => void;
  activeBrush: ActiveBrush | null;
  hasBrush: boolean;
  paintLayer: LayerName | null;
  setPaintLayer: (l: LayerName | null) => void;
  roomId: number;
  setRoomId: (n: number) => void;
  rooms: RoomSummary[];
  suggestedRoomId: number;
}) {
  if (tool !== "shape") return null;
  const wallHint = (shapeKind === "rect-outline" || shapeKind === "line")
    && activeBrush?.category === "wall";
  return (
    <div className="flex items-end gap-2">
      <div>
        <span className="block text-xs text-gray-400">Shape</span>
        <div className="flex overflow-hidden rounded border border-gray-700">
          {SHAPE_KINDS.map(({ kind, label }) => (
            <button
              key={kind}
              type="button"
              onClick={() => setShapeKind(kind)}
              className={`px-2 py-1 text-xs ${
                shapeKind === kind
                  ? "bg-teal-900 text-teal-100"
                  : "bg-gray-900 text-gray-300 hover:bg-gray-800"
              }`}
              title={SHAPE_HINTS[kind]}
            >
              {label}
            </button>
          ))}
        </div>
      </div>

      {shapeKind === "room" ? (
        <div>
          <span className="block text-xs text-gray-400">Room id</span>
          <select
            value={roomId}
            onChange={(e) => setRoomId(parseInt(e.target.value, 10) || 0)}
            className="rounded border border-gray-700 bg-gray-900 px-2 py-1 text-xs"
            title="Room id written to the dragged region. New = a fresh id; Clear = mark as outdoors (room 0)."
          >
            {/* Fallback so the select value always matches a rendered
                option (e.g. default id 1 in a sector whose rooms don't
                include it) — otherwise the box silently desyncs. */}
            {!new Set<number>([suggestedRoomId, 0, ...rooms.map((r) => r.room_id)]).has(roomId) && (
              <option value={roomId}>Room {roomId}</option>
            )}
            <option value={suggestedRoomId}>New room ({suggestedRoomId})</option>
            {rooms.map((r) => (
              <option key={r.room_id} value={r.room_id}>
                Room {r.room_id} ({r.tile_count})
              </option>
            ))}
            <option value={0}>Clear (outdoors)</option>
          </select>
        </div>
      ) : (
        <div>
          <span className="block text-xs text-gray-400">Paint to layer</span>
          <select
            value={paintLayer ?? ""}
            onChange={(e) =>
              setPaintLayer(e.target.value === "" ? null : e.target.value as LayerName)
            }
            className="rounded border border-gray-700 bg-gray-900 px-2 py-1 text-xs"
            title="Override which layer the shape paints into. Default = picked from the brush's category."
          >
            <option value="">auto ({activeBrush?.layer ?? "—"})</option>
            {ALL_LAYERS.map((l) => (
              <option key={l} value={l}>{LAYER_SHORT[l]}</option>
            ))}
          </select>
        </div>
      )}

      {shapeKind !== "room" && !hasBrush && (
        <p className="max-w-[12rem] self-center text-[10px] text-amber-400">
          Pick a tile from the palette to fill the shape with.
        </p>
      )}
      {wallHint && (
        <p className="max-w-[12rem] self-center text-[10px] text-gray-500">
          Paints one wall tile on every edge — per-edge wall orientation
          comes later.
        </p>
      )}
    </div>
  );
}

/** Select-mode-only controls: Copy the marquee selection into the
 * clipboard, then arm Paste (click-to-place). Hidden in other tools so
 * the toolbar width stays stable. Mirrors ShapeOptions. Same-tileset
 * only for now — a clipboard from a different tileset disables Paste
 * (cross-tileset slot remap is deferred). */
function SelectOptions({
  tool, hasSelection, clipboard, pasteMode, readOnly, activeTileset, busy,
  onCopy, onArmPaste, onCancelPaste,
}: {
  tool: Tool;
  hasSelection: boolean;
  clipboard: ClipboardRegion | null;
  pasteMode: boolean;
  readOnly: boolean;
  activeTileset: number;
  busy: boolean;
  onCopy: () => void;
  onArmPaste: () => void;
  onCancelPaste: () => void;
}) {
  if (tool !== "select") return null;
  const crossTileset = clipboard !== null && clipboard.sourceTileset !== activeTileset;
  const canPaste = clipboard !== null && !readOnly && !crossTileset && !busy;
  return (
    <div className="flex items-end gap-2">
      <div>
        <span className="block text-xs text-gray-400">Region</span>
        <div className="flex overflow-hidden rounded border border-gray-700">
          <button
            type="button"
            onClick={onCopy}
            disabled={!hasSelection || busy}
            className="bg-gray-900 px-2 py-1 text-xs text-gray-300 hover:bg-gray-800 disabled:cursor-not-allowed disabled:opacity-40"
            title={hasSelection
              ? "Copy the selected rectangle (all layers, rooms + heights) to the clipboard"
              : "Drag a rectangle on the map first"}
          >
            ⧉ Copy
          </button>
          {pasteMode ? (
            <button
              type="button"
              onClick={onCancelPaste}
              className="bg-amber-900 px-2 py-1 text-xs text-amber-100 hover:bg-amber-800"
              title="Cancel paste (Esc)"
            >
              ✕ Cancel
            </button>
          ) : (
            <button
              type="button"
              onClick={onArmPaste}
              disabled={!canPaste}
              className="bg-purple-900 px-2 py-1 text-xs text-purple-100 hover:bg-purple-800 disabled:cursor-not-allowed disabled:opacity-40"
              title={
                clipboard === null
                  ? "Copy a region first"
                  : readOnly
                    ? "This sector is read-only (open a loose copy to edit)"
                    : crossTileset
                      ? "Cross-tileset paste isn't supported yet — copy within the same tileset"
                      : "Click the map to drop the copied region"
              }
            >
              ⎘ Paste
            </button>
          )}
        </div>
      </div>

      <div className="max-w-[16rem] self-center text-[10px] leading-tight">
        {clipboard ? (
          <span className={crossTileset ? "text-amber-400" : "text-gray-400"}>
            Clipboard: {clipboard.w}×{clipboard.h} · {clipboard.tiles.length} tiles
            {" "}from {clipboard.sourceSector}
            {crossTileset && " · different tileset (paste disabled)"}
          </span>
        ) : (
          <span className="italic text-gray-500">
            Clipboard empty — drag a rectangle, then Copy.
          </span>
        )}
        {pasteMode && (
          <span className="block text-purple-300">
            Click the map to place · Esc to cancel.
          </span>
        )}
        {!pasteMode && clipboard !== null && !readOnly && !crossTileset && (
          <span className="block text-gray-500">
            Tip: select generously — multi-tile structures clipped at the
            edge can look broken.
          </span>
        )}
        {readOnly && (
          <span className="block text-amber-400">
            Read-only sector — paste is disabled.
          </span>
        )}
      </div>
    </div>
  );
}

/** Height-brush-only controls: mode (Raise / Lower / Set) + the value
 * (a step for raise/lower, an absolute level for set). Hidden in other
 * tools so the toolbar width stays stable. Mirrors SelectOptions. */
function HeightOptions({
  tool, heightMode, setHeightMode, heightValue, setHeightValue,
}: {
  tool: Tool;
  heightMode: "raise" | "lower" | "set";
  setHeightMode: (m: "raise" | "lower" | "set") => void;
  heightValue: number;
  setHeightValue: (v: number) => void;
}) {
  if (tool !== "height") return null;
  const modes: Array<{ id: "raise" | "lower" | "set"; label: string; title: string }> = [
    { id: "raise", label: "▲ Raise", title: "Add the step to each tile's current height (clamped at 255)" },
    { id: "lower", label: "▼ Lower", title: "Subtract the step from each tile's current height (clamped at 0)" },
    { id: "set", label: "= Set", title: "Write the value as the tile's absolute height" },
  ];
  return (
    <div className="flex items-end gap-2">
      <div>
        <span className="block text-xs text-gray-400">Height</span>
        <div className="flex overflow-hidden rounded border border-gray-700">
          {modes.map((m) => (
            <button
              key={m.id}
              type="button"
              onClick={() => setHeightMode(m.id)}
              title={m.title}
              className={`px-2 py-1 text-xs ${
                heightMode === m.id
                  ? "bg-orange-900 text-orange-100"
                  : "bg-gray-900 text-gray-300 hover:bg-gray-800"
              }`}
            >
              {m.label}
            </button>
          ))}
        </div>
      </div>
      <div>
        <span className="block text-xs text-gray-400">
          {heightMode === "set" ? "Level" : "Step"}
          <span className="ml-1 font-mono text-gray-200">{heightValue}</span>
        </span>
        <input
          type="number"
          min={heightMode === "set" ? 0 : 1}
          max={255}
          value={heightValue}
          onChange={(e) => {
            const n = parseInt(e.target.value, 10);
            if (Number.isNaN(n)) return;
            setHeightValue(Math.max(0, Math.min(255, n)));
          }}
          className="w-16 rounded border border-gray-700 bg-gray-900 px-2 py-1 text-xs"
          title={heightMode === "set"
            ? "Absolute height (0–255) painted onto each tile"
            : "How many height units each click/drag steps a tile (1–255)"}
        />
      </div>
      <span className="max-w-[14rem] self-center text-[10px] leading-tight text-gray-500">
        Heights don't change the iso render (the engine uses them in-game);
        the orange overlay shows current values — drag to sculpt.
      </span>
    </div>
  );
}

// ─── Undo button ──────────────────────────────────────────────────────
function UndoButton({
  undoDepth, label, onUndo,
}: {
  undoDepth: number;
  label: string | null;
  onUndo: () => void;
}) {
  const enabled = undoDepth > 0 && label !== null;
  // No "Ctrl+Z" caption below the button — it broke toolbar alignment
  // by adding an extra row. The hint lives in the title attribute and
  // in Settings → Hotkeys.
  return (
    <button
      type="button"
      onClick={enabled ? onUndo : undefined}
      disabled={!enabled}
      className={`rounded border px-3 py-1.5 text-xs ${
        enabled
          ? "border-amber-600 bg-amber-900 text-amber-100 hover:bg-amber-800"
          : "border-gray-700 bg-gray-900 text-gray-500"
      } disabled:opacity-50`}
      title={enabled
        ? `Undo last edit: ${label} (Ctrl+Z)`
        : "Nothing to undo (Ctrl+Z)"}
    >
      ↶ Undo
      {enabled && <span className="ml-1 text-amber-300">({undoDepth})</span>}
    </button>
  );
}

// ─── Redo button ──────────────────────────────────────────────────────
function RedoButton({
  redoDepth, label, onRedo,
}: {
  redoDepth: number;
  label: string | null;
  onRedo: () => void;
}) {
  const enabled = redoDepth > 0 && label !== null;
  return (
    <button
      type="button"
      onClick={enabled ? onRedo : undefined}
      disabled={!enabled}
      className={`rounded border px-3 py-1.5 text-xs ${
        enabled
          ? "border-amber-600 bg-amber-900 text-amber-100 hover:bg-amber-800"
          : "border-gray-700 bg-gray-900 text-gray-500"
      } disabled:opacity-50`}
      title={enabled
        ? `Redo: ${label} (Ctrl+Y)`
        : "Nothing to redo (Ctrl+Y)"}
    >
      ↷ Redo
      {enabled && <span className="ml-1 text-amber-300">({redoDepth})</span>}
    </button>
  );
}

// ─── Active brush chip ────────────────────────────────────────────────
// Compact toolbar indicator that shows what brush is currently loaded.
// Replaced the larger sidebar block that used to live at the top of
// the palette — see commit history. The pick CONFIRMATION now flows
// through the log (transient toast); this chip is the durable "what
// am I holding right now" indicator that stays visible while the user
// hunts down the right tile to paint.
function BrushChip({
  brush, renderer, onClear,
}: {
  brush: ActiveBrush | null;
  renderer: IsoRenderer | null;
  onClear: () => void;
}) {
  if (!brush) {
    return (
      <div
        className="flex flex-col items-start gap-0.5"
        title="No brush loaded. Pick a tile in the palette to load one."
      >
        <span className="block text-xs text-gray-400">Brush</span>
        <div className="flex items-center gap-1.5 rounded border border-gray-800 bg-gray-950 px-2 py-1 text-[10px] italic text-gray-600">
          <span className="inline-block h-7 w-7 rounded bg-gray-900" />
          <span>none — pick a tile</span>
        </div>
      </div>
    );
  }
  const stiLabel = brush.sti_filename.replace(/\.sti$/i, "");
  const footprint = renderer?.getFootprint(brush.slot) ?? null;
  const isMultiTile = footprint !== null;
  return (
    <div className="flex flex-col items-start gap-0.5">
      <span className="block text-xs text-gray-400">Brush</span>
      <div
        className={`flex items-center gap-1.5 rounded border px-2 py-1 text-[10px] ${
          isMultiTile
            ? "border-amber-700 bg-amber-950/40 text-amber-200"
            : "border-emerald-700 bg-emerald-950/40 text-emerald-200"
        }`}
        title={
          `Active brush: ${brush.sti_filename}\n`
          + `slot ${brush.slot} · sub ${brush.sub}\n`
          + `paints onto layer: ${brush.layer} (${brush.category})\n`
          + (isMultiTile && footprint
            ? `MULTI-TILE STAMP (${footprint.tiles.length} pieces) — one click drops the whole footprint. Shift+click to drop just sub 1.\n`
            : "")
          + `Pencil click paints this. Right-click on a tile = eyedropper.\n`
          + `Switch sub-frame: , / . keys, or click a thumbnail in the Variants strip.`
        }
      >
        <AtlasFrameThumb
          renderer={renderer}
          slot={brush.slot}
          sub={brush.sub}
          size={28}
          className={`rounded border ${isMultiTile ? "border-amber-800" : "border-emerald-800"}`}
        />
        <div className="flex min-w-0 flex-col leading-tight">
          <span
            className={`truncate font-mono ${isMultiTile ? "text-amber-100" : "text-emerald-100"}`}
            style={{ maxWidth: "9rem" }}
          >
            {stiLabel}
            {isMultiTile && footprint && (
              <span className="ml-1 rounded bg-amber-800/60 px-1 py-px text-[8px]">
                ▦{footprint.tiles.length}
              </span>
            )}
          </span>
          <span className={`font-mono text-[9px] ${isMultiTile ? "text-amber-400" : "text-emerald-400"}`}>
            s{brush.slot}/{brush.sub} → {brush.layer}
          </span>
        </div>
        <button
          type="button"
          onClick={onClear}
          className={`ml-1 hover:opacity-100 ${isMultiTile ? "text-amber-400" : "text-emerald-400"}`}
          title="Clear the active brush (you'll need to pick another before painting)"
        >✕</button>
      </div>
    </div>
  );
}

/** The subs→thumbnail grid for the docked Variants panel. The caller
 * owns the container (scroll / size caps) and the `tileSize`. The
 * sub-number label scales with the tile so big thumbnails stay
 * legible. */
function VariantTileGrid({
  subs, currentSub, slot, renderer, onPickSub, tileSize,
}: {
  subs: number[];
  currentSub: number;
  slot: number;
  renderer: IsoRenderer | null;
  onPickSub: (sub: number) => void;
  tileSize: number;
}) {
  return (
    <div className="flex flex-wrap items-start gap-1">
      {subs.map((sub) => {
        const isCurrent = sub === currentSub;
        return (
          <button
            key={sub}
            type="button"
            onClick={() => onPickSub(sub)}
            title={`Sub ${sub}`}
            className={`flex flex-col items-center rounded border p-0.5 hover:bg-gray-800 ${
              isCurrent
                ? "border-emerald-500 bg-emerald-950/50"
                : "border-gray-700 bg-gray-900"
            }`}
          >
            <AtlasFrameThumb
              renderer={renderer}
              slot={slot}
              sub={sub}
              size={tileSize}
            />
            <span
              className={`mt-px leading-none ${
                isCurrent ? "text-emerald-300" : "text-gray-500"
              }`}
              style={{ fontSize: Math.max(8, Math.round(tileSize / 5)) }}
            >
              {sub}
            </span>
          </button>
        );
      })}
    </div>
  );
}

// ─── Layer visibility toggles ─────────────────────────────────────────
function LayerVisibilityToggles({
  hiddenLayers, setHiddenLayers,
}: {
  hiddenLayers: Set<LayerName>;
  setHiddenLayers: (s: Set<LayerName>) => void;
}) {
  function toggle(l: LayerName) {
    const next = new Set(hiddenLayers);
    if (next.has(l)) next.delete(l);
    else next.add(l);
    setHiddenLayers(next);
  }
  function showOnly(l: LayerName) {
    const next = new Set<LayerName>(ALL_LAYERS.filter((x) => x !== l));
    setHiddenLayers(next);
  }
  function showAll() {
    setHiddenLayers(new Set());
  }
  return (
    <div>
      <span className="block text-xs text-gray-400">Layers (click to hide; shift-click = solo)</span>
      <div className="flex overflow-hidden rounded border border-gray-700">
        {ALL_LAYERS.map((l) => {
          const hidden = hiddenLayers.has(l);
          return (
            <button
              key={l}
              type="button"
              onClick={(e) => {
                if (e.shiftKey) showOnly(l);
                else toggle(l);
              }}
              className={`px-1.5 py-1 text-[10px] ${
                hidden
                  ? "bg-gray-900 text-gray-500 line-through"
                  : "bg-blue-950 text-blue-100"
              } hover:bg-gray-800`}
              title={`${l} — ${hidden ? "hidden" : "visible"}. Shift+click to solo.`}
            >
              {LAYER_SHORT[l]}
            </button>
          );
        })}
        {/* Always rendered (rather than conditional on hiddenLayers
            size) so the row's width stays stable as the user toggles
            visibility. Disabled state when nothing's hidden makes it
            obviously inert. */}
        <button
          type="button"
          onClick={showAll}
          disabled={hiddenLayers.size === 0}
          className="border-l border-gray-700 bg-gray-900 px-2 py-1 text-[10px] text-emerald-300 hover:bg-gray-800 disabled:cursor-default disabled:bg-gray-950 disabled:text-gray-700"
          title={hiddenLayers.size === 0
            ? "All layers already visible"
            : "Show all layers"}
        >
          all
        </button>
      </div>
    </div>
  );
}

// ─── Command-bar primitives ───────────────────────────────────────────
// The fixed command bar groups its controls with thin vertical rules
// instead of color — one accent total (the Save button's emerald-when-
// dirty); everything else stays neutral gray.
function ToolbarDivider() {
  return <span aria-hidden className="mx-0.5 h-5 w-px self-center bg-gray-700" />;
}

/** Minimal dropdown for the command bar (Panels▾ / Layout▾). Click the
 * label to open; any item click, outside click, or Escape closes it.
 * No portal — the bar sits at the top of the editor so a z-50 absolute
 * flyout clears the dock below it. */
function ToolbarMenu({ label, children }: { label: string; children: ReactNode }) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement | null>(null);
  useEffect(() => {
    if (!open) return;
    const onDown = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setOpen(false);
    };
    window.addEventListener("mousedown", onDown);
    window.addEventListener("keydown", onKey);
    return () => {
      window.removeEventListener("mousedown", onDown);
      window.removeEventListener("keydown", onKey);
    };
  }, [open]);
  return (
    <div ref={ref} className="relative">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="rounded border border-gray-700 bg-gray-900 px-2 py-1 text-xs text-gray-300 hover:bg-gray-800 hover:text-gray-100"
      >
        {label} ▾
      </button>
      {open && (
        <div
          className="absolute right-0 top-full z-50 mt-1 min-w-[11rem] rounded border border-gray-700 bg-gray-900 py-1 shadow-lg"
          // Any (enabled) item click closes the menu — the items'
          // own onClick handlers run first via bubbling.
          onClick={() => setOpen(false)}
        >
          {children}
        </div>
      )}
    </div>
  );
}

function ToolbarMenuItem({
  children, onClick, title, disabled = false,
}: {
  children: ReactNode;
  onClick?: () => void;
  title?: string;
  disabled?: boolean;
}) {
  return (
    <button
      type="button"
      disabled={disabled}
      onClick={onClick}
      title={title}
      className="block w-full px-3 py-1.5 text-left text-xs text-gray-200 hover:bg-gray-800 disabled:cursor-default disabled:text-gray-600 disabled:hover:bg-transparent"
    >
      {children}
    </button>
  );
}

/** Named tileset dropdown for the command bar — "#70 — FALLOUT VAULT"
 * instead of a bare number. Falls back to a numeric input while the
 * enumerator list is loading (or when no Ja2Set.dat.xml is wired). */
function TilesetSelect({
  tilesets, tileset, onChange,
}: {
  tilesets: TilesetInfo[] | undefined;
  tileset: number;
  onChange: (t: number) => void;
}) {
  if (!tilesets || tilesets.length === 0) {
    return (
      <input
        type="number"
        value={tileset}
        onChange={(e) => onChange(parseInt(e.target.value, 10) || 0)}
        title="Tileset index (name list unavailable)"
        className="w-16 rounded border border-gray-700 bg-gray-900 px-2 py-1 text-xs text-gray-200"
      />
    );
  }
  const known = tilesets.some((t) => t.index === tileset);
  return (
    <select
      value={tileset}
      onChange={(e) => onChange(parseInt(e.target.value, 10))}
      title="Active tileset — switching re-opens the sector (you'll be prompted if there are unsaved edits)"
      className="max-w-[14rem] rounded border border-gray-700 bg-gray-900 px-2 py-1 text-xs text-gray-200"
    >
      {/* A header tileset outside the enumerated list (custom/modded
          index) still needs to render as selected — synthesize its
          option so the select doesn't silently snap to the first row. */}
      {!known && <option value={tileset}>#{tileset} — (not in list)</option>}
      {tilesets.map((t) => (
        <option key={t.index} value={t.index}>
          #{t.index} — {t.name ?? "(unnamed)"}
        </option>
      ))}
    </select>
  );
}

// ─── Save button ──────────────────────────────────────────────────────
function SaveButton({
  session, localDirty, undoDepth, savedAtDepth, onSaved,
}: {
  session: SessionInfo;
  /** True when the undo stack has moved off the saved position (i.e.,
   * actual unsaved changes exist relative to what's on disk). Distinct
   * from session.dirty, which the backend leaves true after ANY
   * applyEdits call — including the set_entries ops we send during
   * undo. localDirty handles the "paint then undo back to baseline"
   * case correctly. */
  localDirty: boolean;
  undoDepth: number;
  savedAtDepth: number;
  onSaved: (s: SessionInfo) => void;
}) {
  const log = useMapForgeLog();
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [lastSaved, setLastSaved] = useState<{
    bytes: number; backup: string | null; at: number;
  } | null>(null);

  async function save() {
    setBusy(true); setErr(null);
    try {
      const res = await saveSession(session.session_id);
      setLastSaved({
        bytes: res.bytes_written,
        backup: res.backup_path,
        at: Date.now(),
      });
      onSaved(res.session);
      log?.append({
        severity: "success",
        message: `Saved ${(res.bytes_written / 1024).toFixed(1)} KB to disk`,
        detail: res.backup_path ? `backup: ${res.backup_path}` : undefined,
      });
    } catch (e) {
      const msg = e instanceof Error ? e.message : String(e);
      setErr(msg);
      log?.append({ severity: "error", message: "Save failed", detail: msg });
    } finally {
      setBusy(false);
    }
  }

  // Net strokes since last save — positive when painted forward,
  // negative when undone past the save point. Displayed for clarity
  // ("Save (3 strokes ahead)" or "Save (2 strokes behind)").
  const netStrokes = undoDepth - savedAtDepth;
  // Depth-delta is a LABEL hint only — `localDirty` (generation-based)
  // is the truth. They can disagree (save→undo→repaint lands back on
  // the saved depth), so a dirty button never reads "Saved".
  const label = netStrokes > 0
    ? `Save (${netStrokes} stroke${netStrokes === 1 ? "" : "s"})`
    : netStrokes < 0
      ? `Save (rollback ${-netStrokes})`
      : localDirty
        ? "Save"
        : "Saved";
  // No status captions below the button — same toolbar-alignment fix
  // as UndoButton. Save success / failure already lands in the log
  // panel below (with backup path + byte count); a second copy here
  // pushed the toolbar row 12px taller.
  const titleText = localDirty
    ? "Save changes to disk (backups land outside the install)"
    : "No unsaved changes";
  return (
    <button
      type="button"
      onClick={save}
      disabled={busy || !localDirty}
      className={`rounded border px-3 py-1.5 text-xs ${
        err
          ? "border-red-600 bg-red-900 text-red-100"
          : localDirty
            ? "border-emerald-600 bg-emerald-900 text-emerald-100 hover:bg-emerald-800"
            : "border-gray-700 bg-gray-900 text-gray-500"
      } disabled:opacity-50`}
      title={err ? `Save failed: ${err}` : titleText}
    >
      {busy
        ? "Saving…"
        : err
          ? "Save failed (retry)"
          : !localDirty && lastSaved
            ? `Saved (${(lastSaved.bytes / 1024).toFixed(1)} KB)`
            : label}
    </button>
  );
}

// ─── Inspector ─────────────────────────────────────────────────────────
function TileInspectorPanel({
  xmlPath, tileset, session, renderer, renderEpoch, isSlfBundled,
  cols, rows, pinned, onPin, onEditApplied, onPickAsBrush,
}: {
  datPath: string;
  xmlPath: string;
  tileset: number;
  session: SessionInfo | null;
  renderer: IsoRenderer | null;
  renderEpoch: number;
  isSlfBundled: boolean;
  cols: number;
  rows: number;
  pinned: { x: number; y: number } | null;
  onPin: (p: { x: number; y: number } | null) => void;
  onEditApplied: (updatedSession: SessionInfo) => void;
  /** Click on an entry's thumbnail to load it as the active brush.
   * Lets the user pick a specific layer's entry from a multi-layer
   * tile — the right-click eyedropper only picks the topmost.
   */
  onPickAsBrush: (slot: number, sub: number, layer: LayerName, sti_filename: string) => void;
}) {
  const [x, setX] = useState(0);
  const [y, setY] = useState(0);
  useEffect(() => {
    if (pinned) { setX(pinned.x); setY(pinned.y); }
  }, [pinned]);

  // Local inspect — reads straight from the renderer's parsed dict so
  // uncommitted edits show up immediately. `renderEpoch` is in the
  // dependency list so a paint stroke that touches the pinned tile
  // refreshes the inspector without an HTTP fetch.
  const inspectionData: TileInspection | null = useMemo(() => {
    if (!pinned || !renderer) return null;
    return renderer.inspectTile(pinned.x, pinned.y);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [pinned?.x, pinned?.y, renderer, renderEpoch]);

  return (
    <div className="rounded border border-gray-700 bg-gray-950 p-3">
      <h2 className="mb-2 text-sm font-semibold text-gray-300">Tile Inspector</h2>
      <form
        className="mb-1 flex items-end gap-2"
        onSubmit={(e) => { e.preventDefault(); onPin({ x, y }); }}
      >
        <div>
          <label className="block text-xs text-gray-400">X (0–{cols - 1})</label>
          <input
            type="number" min={0} max={cols - 1} value={x}
            onChange={(e) => setX(parseInt(e.target.value, 10) || 0)}
            className="w-20 rounded border border-gray-700 bg-gray-900 px-2 py-1 text-sm"
          />
        </div>
        <div>
          <label className="block text-xs text-gray-400">Y (0–{rows - 1})</label>
          <input
            type="number" min={0} max={rows - 1} value={y}
            onChange={(e) => setY(parseInt(e.target.value, 10) || 0)}
            className="w-20 rounded border border-gray-700 bg-gray-900 px-2 py-1 text-sm"
          />
        </div>
      </form>
      <p className="mb-3 text-[10px] text-gray-500">
        Click any tile on the render — or type X/Y and press Enter.
      </p>

      {/* Pre-#bug-review: a persistent amber "This sector lives inside an
          SLF archive — editing is disabled" banner sat here. Removed
          per user feedback — every other MapForge status message
          routes through the log panel; the inspector banner ate space
          on every load. The same advisory is now logged on sector open
          (see the isSlfBundled useEffect upstream); the actionable
          "Extract to loose" button still floats over the top-left of
          the canvas. */}

      {pinned && !inspectionData && (
        <p className="text-xs text-gray-400">Loading...</p>
      )}
      {inspectionData && (
        <TileInspectionView
          t={inspectionData}
          xmlPath={xmlPath}
          tileset={tileset}
          session={session}
          renderer={renderer}
          editable={
            !isSlfBundled
            && session !== null
            && !session.read_only
            && renderer !== null
          }
          onEdited={onEditApplied}
          onPickAsBrush={onPickAsBrush}
        />
      )}
    </div>
  );
}

function TileInspectionView({
  t, xmlPath, tileset, session, renderer, editable, onEdited, onPickAsBrush,
}: {
  t: TileInspection;
  xmlPath: string;
  tileset: number;
  session: SessionInfo | null;
  renderer: IsoRenderer | null;
  editable: boolean;
  onEdited: (updated: SessionInfo) => void;
  onPickAsBrush: (slot: number, sub: number, layer: LayerName, sti_filename: string) => void;
}) {
  // Composite key = "layer:index" of the entry currently being edited
  // (so only one inline form is open at a time).
  const [editingKey, setEditingKey] = useState<string | null>(null);
  const [editError, setEditError] = useState<string | null>(null);
  const [editBusy, setEditBusy] = useState(false);
  // When set, opens the JSD viewer modal for that slot. JSD data is
  // small (~100 bytes) and fetched on demand.
  const [jsdSlot, setJsdSlot] = useState<number | null>(null);

  // When the inspected tile changes (parent reuses this component with
  // new props rather than remounting), the local view-state above is
  // stale — the JSD panel would still show the previous tile's slot,
  // the inline edit form would still be open against an entry index
  // that no longer matches. Reset on every tile change.
  useEffect(() => {
    setJsdSlot(null);
    setEditingKey(null);
    setEditError(null);
  }, [t.x, t.y]);

  async function applyEdit(
    op: "replace" | "remove",
    layer: LayerName,
    entryIdx: number,
    slot?: number,
    sub?: number,
  ) {
    if (!session || !renderer) return;
    setEditBusy(true); setEditError(null);
    // Mirror the paintBrush flow: mutate local first for instant
    // canvas feedback, then send to backend. Routed through the stroke
    // machinery so inspector edits are (a) undoable, (b) invalidate the
    // redo timeline like every other mutation, and (c) count toward
    // dirty tracking — previously an inspector-only session showed
    // "Saved" and refused Ctrl+S, losing the edits on close.
    renderer.beginStroke(`Edit ${layer}[${entryIdx}] (${t.x},${t.y})`);
    renderer.recordSnapshot(t.x, t.y, layer);
    renderer.applyLocalEdit({
      x: t.x, y: t.y, op,
      layer, slot, sub, entryIndex: entryIdx,
    });
    renderer.endStroke();
    try {
      const edit: SessionEdit = {
        x: t.x, y: t.y, op, layer,
        entry_index: entryIdx, slot, sub,
      };
      const res = await applyEdits(session.session_id, [edit]);
      setEditingKey(null);
      onEdited(res.session);
    } catch (e) {
      // Backend rejected — the server session is untouched, so revert
      // the optimistic local mirror and DISCARD the stroke (no redo
      // mirror: Ctrl+Y must not replay a rejected edit).
      const entry = renderer.discardLastUndo();
      if (entry) {
        for (const s of entry.snapshots) {
          renderer.applyLocalEdit({
            x: s.x, y: s.y, op: "set_entries", layer: s.layer, entries: s.entries,
          });
        }
      }
      // Same-session callback so the parent repaints + resyncs the
      // history/dirty UI after the revert.
      onEdited(session);
      setEditError(e instanceof Error ? e.message : String(e));
    } finally {
      setEditBusy(false);
    }
  }

  // Per-layer entry counts — used for the layer-filter dropdown labels
  // ("structs (3)") and to show the user which layers have any data
  // on this tile without expanding.

  return (
    <div className="space-y-3 text-xs">
      <div className="rounded bg-gray-900 p-2 font-mono">
        ({t.x},{t.y}) g={t.gridno} room={t.room_id} height={t.height} flags={t.world_flags}
      </div>

      {/* "Saved" feedback now lives on the top-bar Save button —
          edits go to memory until the user explicitly saves. */}
      {editError && (
        <div className="rounded bg-red-950 px-2 py-1 text-[10px] text-red-300">
          {editError}
        </div>
      )}

      {(["land", "objs", "shadows", "structs", "roofs", "onroofs"] as const).map(
        (layer) => {
          const entries = t.layers[layer] ?? [];
          if (entries.length === 0) return null;
          return (
            <div key={layer}>
              <div className="mb-1 text-xs font-semibold uppercase text-gray-400">
                {layer} ({entries.length})
              </div>
              <ul className="space-y-1">
                {entries.map((e, i) => {
                  const key = `${layer}:${i}`;
                  const editing = editingKey === key;
                  return (
                    <li key={i} className="rounded bg-gray-900 px-2 py-1 font-mono text-xs">
                      <div className="flex items-start justify-between gap-2">
                        <div className="flex min-w-0 items-center gap-2">
                          {/* Atlas-backed preview — instant, no HTTP.
                              CLICK to load this entry as the active
                              brush (lets the user pick a specific
                              layer from a multi-layer tile — the
                              right-click eyedropper only grabs the
                              topmost). */}
                          <button
                            type="button"
                            onClick={() => onPickAsBrush(
                              e.slot, e.sub, layer,
                              e.sti_filename ?? `slot ${e.slot}`,
                            )}
                            title={
                              `Load slot ${e.slot} sub ${e.sub} (${e.sti_filename ?? "?"}) `
                              + `as the active brush, painting onto layer ${layer}. `
                              + `Switches the tool to Pencil.`
                            }
                            className="rounded ring-0 hover:ring-2 hover:ring-emerald-500/60 focus:outline-none focus:ring-2 focus:ring-emerald-500"
                          >
                            <AtlasFrameThumb
                              renderer={renderer}
                              slot={e.slot} sub={e.sub} size={40}
                            />
                          </button>
                          <span className="min-w-0 truncate">
                            <span className="text-blue-300">slot {e.slot}</span>{" "}
                            <span className="text-amber-300">sub {e.sub}</span>{" "}
                            <span className="text-gray-500">→ frame[{e.sti_frame_index_0based}]</span>
                            <br/>
                            <span className="text-gray-300">{e.sti_filename ?? "?"}</span>
                          </span>
                        </div>
                        <span className="flex shrink-0 gap-1">
                          {/* JSD viewer button — shown only when the
                              slot's STI has a sibling .jsd. The
                              has_jsd flag is populated from the atlas
                              manifest's slot_has_jsd map. */}
                          {e.has_jsd && (
                            <button
                              type="button"
                              title={`View .jsd (multi-tile footprint, passability, PROFILE voxel grid)`}
                              onClick={() => setJsdSlot(e.slot)}
                              className="rounded border border-amber-700 bg-amber-950/40 px-1.5 py-0.5 text-[10px] text-amber-300 hover:border-amber-500 hover:bg-amber-900/50"
                            >
                              JSD
                            </button>
                          )}
                          {editable && !editing && (
                            <>
                              <button
                                type="button"
                                title="Edit slot/sub"
                                disabled={editBusy}
                                onClick={() => { setEditingKey(key); setEditError(null); }}
                                className="rounded border border-gray-700 px-1.5 py-0.5 text-[10px] text-gray-300 hover:border-blue-500 hover:text-blue-300 disabled:opacity-50"
                              >
                                ✎
                              </button>
                              <button
                                type="button"
                                title="Remove this entry"
                                disabled={editBusy}
                                onClick={() => {
                                  if (confirm(`Remove ${layer}[${i}] = slot ${e.slot} sub ${e.sub}?`)) {
                                    applyEdit("remove", layer, i);
                                  }
                                }}
                                className="rounded border border-gray-700 px-1.5 py-0.5 text-[10px] text-gray-300 hover:border-red-500 hover:text-red-300 disabled:opacity-50"
                              >
                                ✕
                              </button>
                            </>
                          )}
                        </span>
                      </div>
                      {editing && (
                        <EditRow
                          xmlPath={xmlPath} tileset={tileset}
                          renderer={renderer}
                          initialSlot={e.slot}
                          initialSub={e.sub}
                          busy={editBusy}
                          onCancel={() => setEditingKey(null)}
                          onApply={(slot, sub) => applyEdit("replace", layer, i, slot, sub)}
                        />
                      )}
                    </li>
                  );
                })}
              </ul>
            </div>
          );
        }
      )}

      {jsdSlot !== null && (
        <JsdViewer
          xmlPath={xmlPath}
          tileset={tileset}
          slot={jsdSlot}
          onClose={() => setJsdSlot(null)}
        />
      )}
    </div>
  );
}

// ─── JSD viewer ───────────────────────────────────────────────────────
// Renders a parsed .jsd: header (flag names + HP/armour/density) +
// per-footprint-tile PROFILE 5x5 voxel grids. Used by the tile
// inspector when the user clicks the "JSD" button on a struct entry.
function JsdViewer({
  xmlPath, tileset, slot, onClose,
}: {
  xmlPath: string;
  tileset: number;
  slot: number;
  onClose: () => void;
}) {
  const jsd = useQuery({
    queryKey: ["mapforge", "jsd", xmlPath, tileset, slot],
    queryFn: () => getStiJsd(xmlPath, tileset, slot),
    staleTime: 5 * 60 * 1000,
    retry: false,
  });
  return (
    <div className="rounded border border-amber-700 bg-amber-950/30 p-2 text-[10px]">
      <div className="mb-1 flex items-center justify-between">
        <span className="font-mono text-amber-300">
          JSD · slot {slot}
        </span>
        <button
          type="button"
          onClick={onClose}
          className="text-amber-400 hover:text-amber-200"
          title="Close JSD view"
        >✕</button>
      </div>
      {jsd.isLoading && <p className="text-gray-500">Reading .jsd…</p>}
      {jsd.error && (
        <p className="text-red-400">
          {jsd.error instanceof Error ? jsd.error.message : String(jsd.error)}
        </p>
      )}
      {jsd.data && (() => {
        const d = jsd.data;
        return (
          <div className="space-y-1.5 font-mono">
            <div className="text-amber-200">
              {d.sti_filename} <span className="text-amber-500">·</span>
              {" "}{d.size_bytes} B
              {" "}<span className="text-amber-500">·</span>{" "}
              <span className="text-amber-400">{d.szId}</span>
            </div>
            <div title="Bitmask of structural behavior flags the engine checks at render + interaction time. Hover each chip for the per-flag meaning.">
              <span className="text-gray-500">flags 0x{d.flags_int.toString(16).padStart(4, "0")}:</span>{" "}
              {d.flag_names.length === 0 ? (
                <span className="text-gray-600">(none)</span>
              ) : (
                d.flag_names.map((f) => (
                  <span
                    key={f}
                    className="mr-1 inline-block rounded bg-amber-900/60 px-1 text-amber-200"
                    title={_jsdFlagTooltip(f)}
                  >
                    {f}
                  </span>
                ))
              )}
            </div>
            <div className="grid grid-cols-2 gap-1 text-gray-300">
              <div title="Hit points — how much damage the struct absorbs before destruction. 0 = indestructible. Engine field ubHitPoints.">
                HP: <span className="text-amber-200">{d.ubHP}</span>
              </div>
              <div title="Damage resistance against bullets/explosives. Higher = takes less damage per hit. Engine field ubArmour.">
                armour: <span className="text-amber-200">{d.ubArmour}</span>
              </div>
              <div title="Visual + AI density on a 0–100 scale. Influences merc cover bonus + line-of-sight blocking. Engine field ubDensity.">
                density: <span className="text-amber-200">{d.ubDensity}</span>
              </div>
              <div title="Number of grid cells this struct occupies. >1 means it's a multi-tile footprint (cars, big trees, walls).">
                tiles: <span className="text-amber-200">{d.ubNumberOfTiles}</span>
              </div>
              <div title="Z-offset X — horizontal shift applied to the sprite at render time. Used by struct-shadow pairs and tall sprites that need to lift off their anchor.">
                zOff X: <span className="text-amber-200">{d.bZTileOffsetX}</span>
              </div>
              <div title="Z-offset Y — vertical shift applied to the sprite at render time. Negative = lifted up; positive = pushed down. Walls/roofs use this so they sit on the right floor row.">
                zOff Y: <span className="text-amber-200">{d.bZTileOffsetY}</span>
              </div>
            </div>
            {d.tiles.length > 0 && (
              <div className="mt-2 space-y-2">
                <div className="text-gray-500" title="Per-tile Z-occupancy profiles. Each cell of the 5×5 grid is a hex byte where bits represent which Z-slabs (height layers) of that cell are blocked. Used by the engine for cover, LOS, and collision.">
                  Footprint ({d.tiles.length} tile{d.tiles.length === 1 ? "" : "s"}):
                </div>
                {d.tiles.map((tt, i) => (
                  <ProfileGrid key={i} index={i} tile={tt} />
                ))}
              </div>
            )}
            <details className="mt-1 text-gray-500">
              <summary className="cursor-pointer hover:text-gray-300">
                source
              </summary>
              <div className="mt-0.5 max-w-full truncate text-[9px]" title={d.jsd_path}>
                {d.jsd_path}
              </div>
            </details>
          </div>
        );
      })()}
    </div>
  );
}

/** One footprint tile's 5×5 PROFILE — each cell's byte is a Z-occupancy
 * mask (which Z-slabs of the cell are blocked). The grid shows the
 * mask in hex; non-zero cells are tinted amber so you can see the
 * shape at a glance. */
function ProfileGrid({ index, tile }: {
  index: number;
  tile: import("../lib/mapforge").JsdProfileTile;
}) {
  return (
    <div className="rounded border border-amber-900/50 bg-gray-950/60 p-1.5">
      <div className="mb-1 text-gray-400">
        tile[{index}] bX={tile.bXPos} bY={tile.bYPos}
        {" "}<span className="text-gray-600">(sPos={tile.sPosRelToBase})</span>
      </div>
      <div className="inline-grid gap-px"
        style={{ gridTemplateColumns: "repeat(5, 1fr)" }}>
        {tile.profile.map((row, y) => row.map((v, x) => (
          <div
            key={`${y}-${x}`}
            className="flex h-5 w-6 items-center justify-center font-mono text-[8px]"
            style={{
              backgroundColor: v === 0
                ? "rgb(20, 20, 20)"
                : `rgba(255, 200, 100, ${Math.min(1, v / 255 + 0.2)})`,
              color: v === 0 ? "rgb(80, 80, 80)" : "rgb(20, 20, 20)",
            }}
            title={`(${x},${y}) = 0x${v.toString(16).padStart(2, "0")} = ${v}`}
          >
            {v === 0 ? "·" : v.toString(16)}
          </div>
        )))}
      </div>
    </div>
  );
}

/** Live preview thumb for the inline EditRow form. Prefers
 * AtlasFrameThumb (zero HTTP, instant per keystroke) and falls back
 * to StiFrameImage (HTTP fetch) only when the slot/sub the user is
 * typing isn't in the loaded atlas. Most edits land in the atlas
 * since the entry the user is editing came from the current
 * tileset; the fallback is for cases like typing a slot number
 * that exists in the XML but whose sub hasn't been loaded yet. */
function EditRowPreview({
  renderer, xmlPath, tileset, slot, sub,
}: {
  renderer: IsoRenderer | null;
  xmlPath: string;
  tileset: number;
  slot: number;
  sub: number;
}) {
  // Detect atlas presence by attempting to draw into a hidden 1x1
  // canvas — if drawCellInto returns false the (slot, sub) isn't in
  // the cellMap. We use a sentinel ref + effect to track.
  const [missing, setMissing] = useState(false);
  // The check fires when renderer / slot / sub change. We don't
  // actually draw here (the visible thumb's own effect handles
  // drawing); this effect only sets `missing` so we know whether to
  // show the AtlasFrameThumb or the HTTP-fallback StiFrameImage.
  useEffect(() => {
    if (!renderer) { setMissing(true); return; }
    // Use a temp canvas just for the presence check.
    const tmp = document.createElement("canvas");
    tmp.width = 1; tmp.height = 1;
    const ctx = tmp.getContext("2d");
    if (!ctx) { setMissing(true); return; }
    const ok = renderer.drawCellInto(ctx, slot, sub, 1, 1);
    setMissing(!ok);
  }, [renderer, slot, sub]);

  if (renderer && !missing) {
    return (
      <AtlasFrameThumb
        renderer={renderer} slot={slot} sub={sub} size={56}
        className="rounded border border-emerald-700"
      />
    );
  }
  return (
    <StiFrameImage
      xmlPath={xmlPath} tileset={tileset}
      slot={slot} sub={sub} maxSize={56}
      className="rounded border border-emerald-700"
    />
  );
}


function EditRow({
  xmlPath, tileset, renderer, initialSlot, initialSub, busy, onCancel, onApply,
}: {
  xmlPath: string;
  tileset: number;
  /** Renderer for the live preview's atlas lookup. When the typed
   * (slot, sub) is in the cellMap we render from the atlas (instant);
   * otherwise we fall back to the HTTP path so the user still sees
   * something — e.g., typing a slot that exists in the tileset XML
   * but whose sub they haven't picked yet. */
  renderer: IsoRenderer | null;
  initialSlot: number;
  initialSub: number;
  busy: boolean;
  onCancel: () => void;
  onApply: (slot: number, sub: number) => void;
}) {
  const [slot, setSlot] = useState(initialSlot);
  const [sub, setSub] = useState(initialSub);
  return (
    <form
      className="mt-2 flex items-end gap-2"
      onSubmit={(e) => { e.preventDefault(); onApply(slot, sub); }}
    >
      {/* Live preview of the chosen (slot, sub). Updates instantly
          via atlas lookup; falls back to HTTP for slots/subs not in
          the current cellMap. */}
      <div className="flex flex-col items-center gap-0.5">
        <span className="text-[9px] text-gray-500">preview</span>
        <EditRowPreview
          renderer={renderer}
          xmlPath={xmlPath}
          tileset={tileset}
          slot={slot}
          sub={sub}
        />
      </div>
      <div>
        <label className="block text-[9px] text-gray-500">slot</label>
        <input
          type="number" min={0} max={255}
          value={slot}
          onChange={(e) => setSlot(parseInt(e.target.value, 10) || 0)}
          className="w-16 rounded border border-gray-700 bg-gray-900 px-1.5 py-0.5 text-[11px]"
        />
      </div>
      <div>
        <label className="block text-[9px] text-gray-500">sub</label>
        <input
          type="number" min={1} max={65535}
          value={sub}
          onChange={(e) => setSub(parseInt(e.target.value, 10) || 1)}
          className="w-16 rounded border border-gray-700 bg-gray-900 px-1.5 py-0.5 text-[11px]"
        />
      </div>
      <button
        type="submit" disabled={busy}
        title="Replace this entry with the typed slot/sub (preserves layer + entry position)"
        className="rounded border border-emerald-700 bg-emerald-900 px-2 py-0.5 text-[10px] text-emerald-100 hover:bg-emerald-800 disabled:opacity-50"
      >
        {busy ? "…" : "Apply"}
      </button>
      <button
        type="button" disabled={busy} onClick={onCancel}
        title="Discard pending changes and close the edit form"
        className="rounded border border-gray-700 bg-gray-900 px-2 py-0.5 text-[10px] text-gray-300 hover:bg-gray-800 disabled:opacity-50"
      >
        Cancel
      </button>
    </form>
  );
}

// ─── Load progress bar ────────────────────────────────────────────────
// Replaces the old indeterminate "Loading tileset atlas…" spinner with
// a real percent bar driven by IsoRenderer.create's onProgress callback.
// Atlas fetch reports bytes-loaded, decode + bake report sub-phase
// percents, manifest + parsed are short fixed-weight slots.
function LoadProgressBar({
  phase, phasePct, overallPct,
}: {
  phase: ProgressPhase;
  phasePct: number;
  overallPct: number;
}) {
  return (
    <div className="absolute inset-0 z-20 flex items-center justify-center bg-gray-950/70 backdrop-blur-sm">
      <div className="w-80 rounded-lg border border-blue-800 bg-gray-900 p-4 shadow-lg">
        <div className="mb-2 flex items-center justify-between text-xs">
          <span className="text-blue-200">
            {PROGRESS_PHASE_LABELS[phase]}…
          </span>
          <span className="font-mono text-blue-300">{overallPct}%</span>
        </div>
        {/* Outer bar: overall progress across all phases. */}
        <div className="relative h-2 overflow-hidden rounded bg-gray-800">
          <div
            className="h-full bg-blue-500 transition-[width] duration-100 ease-linear"
            style={{ width: `${overallPct}%` }}
          />
        </div>
        {/* Inner bar: current phase sub-progress. Useful when the atlas
            fetch is slow — the inner bar shows the download is
            actually moving, not just the phase label flipping. */}
        <div className="mt-2 h-1 overflow-hidden rounded bg-gray-800">
          <div
            className="h-full bg-blue-400/60 transition-[width] duration-75 ease-linear"
            style={{ width: `${phasePct}%` }}
          />
        </div>
      </div>
    </div>
  );
}
