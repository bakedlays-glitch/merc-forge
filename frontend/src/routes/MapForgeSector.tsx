/**
 * MapForge sector viewer + tile inspector.
 *
 * Reads `?dat=<path>&xml=<path>` from the URL, fetches sector info, lets
 * the user pick a room (or render the full sector), shows the iso PNG
 * with an SVG overlay (diamond grid, hover highlight, pinned tile,
 * optional room-number labels), and an inspector panel that auto-updates
 * on canvas clicks.
 *
 * View controls:
 *   - mouse-wheel zoom + drag pan (CSS transform on a wrapper div)
 *   - SVG overlay with diamond grid for room-scope views (skipped for
 *     full-sector renders where 25k diamonds would tank performance)
 *   - hover diamond highlight + live (x,y) readout
 *   - pinned diamond highlight (the tile shown in the inspector)
 *   - room-number labels (toggleable)
 *   - "Reset view" snaps zoom/pan back to 1×/origin
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

import { formatApiError, mediaUrl } from "../lib/api";
import { isRunningInTauri } from "../lib/tauri";
import {
  applyEdits,
  closeSession,
  extractSlfToLoose,
  previewExtractSlfToLoose,
  fetchAtlasBlobUrl,
  fetchStiFrameBlobUrl,
  getAtlasManifest,
  getSectorInfo,
  getSession,
  getSessionAppendix,
  getSessionParsed,
  getStiJsd,
  getTilesetPalette,
  newSector,
  openSession,
  rememberOwnSession,
  sessionRecovery,
  prefetchPaletteSheet,
  saveCopyAs,
  saveSession,
  streamAtlasBuild,
  validateSession,
  type AppendixEntities,
  type LayerName,
  type PaletteSlot,
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
  type GhostRegionTile,
  type RegionRender,
  type SpriteHit,
  type UndoEntry,
} from "../lib/IsoRenderer";
import { IsoRendererGL } from "../lib/IsoRendererGL";
import { propFrameLabel, type PropFrameLabel } from "../lib/mapforgePropLabels";
import { type ActiveBrush } from "./MapForgePalette";
import { MapForgeAssetBrowserBody } from "./MapForgeAssetViewer";
import { MapForgeTilesetBrowser } from "./MapForgeTilesetBrowser";
import { MapForgePaletteRail } from "./MapForgePaletteRail";
import { CommandCard, cardCellsFor } from "../components/CommandCard";
import { usePersistentControlGroups, seedFromFavorites, type ControlGroup } from "../lib/controlGroups";
import { AtlasFrameThumb, LoadProgressBar, TileInspectorPanel } from "./MapForgeTileInspector";
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
import { useDialog } from "../components/DialogProvider";
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
  usePersistentBrushBucket, usePersistentClipboard, usePersistentSpriteClipboard, sameBrush,
  RECENT_BRUSHES_KEY, FAVORITE_BRUSHES_KEY,
  RECENT_BRUSHES_CAP,
  readJournalEntry, writeJournalEntry, clearJournalEntry,
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
  CLIP_LAYERS,
  type ClipboardRegion,
} from "../lib/mapClipboard";
import {
  EMPTY_TABLES,
  buildOccupancy,
  localCheck,
  worstOf,
  refKey,
  sliceGroup,
  groupRefsAt,
  groupPasteEdits,
  deleteEdits,
  moveEdits,
  groupToRegionTiles,
  mergeVerdicts,
  oracleToVerdicts,
  armKindFor,
  categoryOf,
  fenceSubsForLine,
  footprintTiles,
  queueCommitEdits,
  type SpriteRef,
  type SpriteGroup,
  type PlacementTables,
  type TileVerdict,
  type Occupancy,
  type QueuedGhost,
} from "../lib/mapPlacement";
import {
  getPlacementTables,
  checkPlacement,
  trailingDebounce,
} from "../lib/placementApi";

/** UI tool modes — they choose the REGION a stroke covers. Inspect =
 * click-to-pin; Pencil = click/drag (brush radius); Shape = drag a
 * rectangle/line/flood/…; Select = marquee + clipboard. (R4: Height is no
 * longer a tool — it's a payload.) */
type Tool = "inspect" | "pencil" | "shape" | "select";

/** What a stroke DOES to the tiles its tool covers (R4 payloads): place the
 * active brush, erase the non-ground layers, set per-tile height, or write a
 * room id. Pencil + Shape both honor the payload. */
type Payload = "tiles" | "erase" | "height" | "room";

/** Non-ground layers cleared by the Erase payload — keeps the floor. */
const ERASE_LAYERS: LayerName[] = ["objs", "shadows", "structs", "roofs", "onroofs"];

/** Rebindable actions that only mean something in the mode-less model
 * — gated off entirely (no dispatch, no preventDefault)
 * when settings.legacyTools is on, so their default bindings (Ctrl+C/X/V,
 * Delete, arrows, Escape, R) keep the page's normal behaviour instead of
 * being silently swallowed for an action that can never fire. */
const MODELESS_ONLY_ACTIONS = new Set<MapForgeActionId>([
  "sel-copy", "sel-cut", "sel-paste", "sel-delete",
  "sel-cycle-next", "sel-cycle-prev",
  "nudge-left", "nudge-right", "nudge-up", "nudge-down",
  "cancel",
]);

/** Placeholder `ActiveBrush.category` values used by pick paths that
 * source a brush from a tile ALREADY on the map (eyedropper, the tile
 * inspector's "pick as brush") rather than from the categorised palette
 * — they can't know the sidecar's real category classification, so
 * `armKindFor` (spec D8) can't tell a wall from a truck for them. Route
 * these through the plain paint brush (the pre-existing behaviour, and
 * the far more likely intent for "keep painting what I just clicked")
 * instead of guessing "ghost" for anything not in BRUSH_FAMILIES. */
const UNCATEGORIZED_BRUSH_SOURCES = new Set(["(eyedropped)", "(picked from tile)"]);

/** Step through a SPARSE sub list (gaps allowed — some slots are missing
 * a sub between others), wrapping at both ends. Review finding #2: a
 * dense `subCount` max (`getSlotInfo`) can land the ghost/selection R
 * cycle on a hole; only `renderer.listValidSubs(slot)` is safe to step
 * through (the file's existing per-brush `cycleSub(delta)` below already
 * does this for the active brush — same pattern, applied to ghosts and
 * selected sprites). No-op (returns `current`) on an empty list. */
function stepValidSub(subs: number[], current: number, dir: 1 | -1): number {
  if (subs.length === 0) return current;
  const idx = subs.indexOf(current);
  const base = idx < 0 ? -1 : idx;
  return subs[(base + dir + subs.length) % subs.length] ?? current;
}

/** Compile-time exhaustiveness guard. When a new `Tool` is added to the
 * union, any `if`/`switch` that forwards an unhandled value here stops
 * compiling (the argument is no longer narrowed to `never`) — so a new
 * tool can't silently fall through the canvas dispatch. Throws at
 * runtime as a backstop. */
function assertNever(x: never): never {
  throw new Error(`Unhandled Tool case: ${JSON.stringify(x)}`);
}

/** One placement-queue entry: the pure `QueuedGhost`
 * (anchor+group) mapPlacement.ts's `queueCommitEdits` needs, plus a
 * route-only pre-rendered ghost canvas for the queue overlay. A
 * `QueuedPlacement[]` still satisfies `queueCommitEdits(queue: QueuedGhost[], …)`
 * structurally — the extra `render` field is simply ignored by it. */
interface QueuedPlacement extends QueuedGhost { render: RegionRender | null }

/** What a committed stroke writes to each tile. `place` paints the active
 * brush into a layer; `set_room` stamps a room id. Mirrors the subset of
 * edit ops the shape + pencil tools emit. */
type StrokeSpec =
  | { op: "place"; layer: LayerName; slot: number; sub: number }
  | { op: "set_room"; roomId: number };

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

export default function MapForgeSector() {
  return (
    <MapForgeLogProvider>
      <MapForgeSectorInner />
    </MapForgeLogProvider>
  );
}

function MapForgeSectorInner() {
  const log = useMapForgeLog();
  const { confirm, prompt } = useDialog();
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
  // Normalize NaN to null.
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
  // Crash-recovery autosave offered by the open response: a snapshot from
  // a previous sidecar process exists for this map and differs from disk.
  const [recoveryOffer, setRecoveryOffer] =
    useState<{ saved_at: number; edit_count: number } | null>(null);
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
      // Same NaN-guard treatment as selectedRoom.
      const parsed = parseInt(tilesetParam, 10);
      if (Number.isFinite(parsed)) return parsed;
    }
    // Prefer the session's tileset (arrives first now that we open the
    // session in parallel) over info.data's. Both should agree.
    return session?.tileset ?? info.data?.tileset_in_header ?? 0;
  }, [tilesetParam, session, info.data]);
  // Share the palette's React Query entry: the Brush Box and inspector
  // read the same names, including the per-frame labels.
  const propLabels = useQuery({
    queryKey: ["mapforge", "palette", xmlPath, tileset],
    queryFn: () => getTilesetPalette(xmlPath, tileset),
    enabled: !!xmlPath && tileset >= 0,
    staleTime: 5 * 60 * 1000,
  });
  const propSlots = useMemo(
    () => new Map<number, PaletteSlot>(propLabels.data?.slots.map((slot) => [slot.slot, slot]) ?? []),
    [propLabels.data],
  );
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
    setRecoveryOffer(null);
    if (!datPath || !xmlPath) return;
    let cancelled = false;
    let openedId: string | null = null;
    const initialTileset = tilesetParam !== null ? parseInt(tilesetParam, 10) : 0;

    // Adopt a session as the live one: wire openedId, mirror state, warm
    // the palette sheet. Shared by the fresh-open and recovery paths.
    const adopt = (s: SessionInfo) => {
      openedId = s.session_id;
      // Per-tab breadcrumb so a reload can reclaim THIS session if the
      // page dies before closing it (reload-wins, own-session-only).
      rememberOwnSession(datPath, s.session_id);
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
      // A crash-recovery autosave snapshot exists (previous sidecar
      // process died with unsaved edits). Surface the restore/discard
      // choice — the modal is rendered at top level.
      if (s.recovery && !s.read_only) setRecoveryOffer(s.recovery);
    };

    (async () => {
      // ─── Recovery probe (R6) ───────────────────────────────────────
      // The sidecar never evicts a DIRTY session, so unsaved edits made
      // before a reload/crash survive in-memory until the sidecar
      // process restarts. If our journal has a breadcrumb for THIS
      // datPath, probe whether that session is still live + dirty and,
      // if so, RECONNECT to it instead of opening a fresh (empty)
      // session — recovering the user's uncommitted work. A missing,
      // closed, clean, or mismatched session must never crash the open
      // flow: on any failure we fall through to the normal fresh open.
      const journal = readJournalEntry(datPath);
      if (journal?.sessionId) {
        try {
          const live = await getSession(journal.sessionId);
          if (cancelled) return;
          const sameSector = live.dat_path === datPath;
          if (sameSector && live.dirty && !live.read_only) {
            adopt(live);
            log?.append({
              severity: "success",
              message: `Recovered ${live.edit_count} unsaved edit`
                + `${live.edit_count === 1 ? "" : "s"} from before the reload.`,
              detail: "Reconnected to the in-memory editing session. "
                + "Save to write them to disk.",
            });
            return;
          }
          // Live but not recoverable (saved/clean, read-only, or a
          // different sector reusing the id) — drop the stale breadcrumb.
          clearJournalEntry(datPath);
        } catch {
          if (cancelled) return;
          // 404 / network — the session is gone (sidecar restarted, most
          // likely). Clear the stale entry and tell the user their
          // pre-reload edits couldn't be recovered. Non-blocking.
          clearJournalEntry(datPath);
          log?.append({
            severity: "warn",
            message: `Couldn't recover ${journal.editCount} unsaved edit`
              + `${journal.editCount === 1 ? "" : "s"} — the editor or `
              + "sidecar was restarted.",
            detail: "The in-memory session that held them is gone. "
              + "Opening a fresh session from the file on disk.",
          });
        }
      }
      if (cancelled) return;
      // ─── Normal fresh open ─────────────────────────────────────────
      try {
        // Two page mounts can race for the same map's writable session
        // (React StrictMode double-mount in dev, or a reload while the
        // previous page's close is still in flight): the older sibling's
        // session closes moments after our POST 409s, so a
        // WRITABLE_SESSION_EXISTS here is usually transient. Retry
        // briefly before surfacing; a map genuinely open elsewhere
        // still 409s after the retries.
        let s: SessionInfo;
        for (let attempt = 0; ; attempt++) {
          try {
            s = await openSession(datPath, xmlPath, initialTileset);
            break;
          } catch (err) {
            const msg = err instanceof Error ? err.message : String(err);
            if (attempt < 4 && msg.includes("WRITABLE_SESSION_EXISTS")) {
              await new Promise((r) => setTimeout(r, 700));
              if (cancelled) return;
              continue;
            }
            throw err;
          }
        }
        if (cancelled) {
          closeSession(s.session_id).catch(() => {});
          return;
        }
        adopt(s);
      } catch (err) {
        if (cancelled) return;
        setSessionError(err instanceof Error ? err.message : String(err));
      }
    })();

    return () => {
      cancelled = true;
      if (openedId) {
        // Deliberately closing the session here destroys its in-memory
        // edits, so the journal breadcrumb pointing at it is now dead —
        // clear it (the dirty-tracking effect re-writes a fresh one if a
        // new session becomes dirty). This is what makes the journal a
        // RELOAD/CRASH recovery (cleanup doesn't run on a hard reload) and
        // not a phantom "couldn't recover" toast after a clean nav-away.
        // The sidecar REFUSES to close a dirty session without force
        // (StrictMode/HMR re-run this cleanup right after a reconnect to
        // a live dirty session — that used to destroy its edits), so
        // only clear the breadcrumb once the close actually happened.
        closeSession(openedId)
          .then(() => clearJournalEntry(datPath))
          .catch(() => {});
      }
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps -- sessionRestartEpoch
    // is intentionally in deps so a sidecar restart re-fires the open;
    // `log` is a stable context value, omitted to avoid re-fire churn.
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
    // Browser/vite dev mode has no Tauri shell — `listen` would throw
    // "Cannot read properties of undefined (reading 'transformCallback')".
    // No shell also means no watchdog to emit the event, so skip cleanly.
    if (!isRunningInTauri()) return;
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
      // The user closed the Brush Box tab — re-add it left of the canvas.
      api.addPanel({
        id: "assets",
        component: "default",
        title: PANEL_TITLE.assets,
        position: { referencePanel: "canvas", direction: "left" },
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
  // Favorites (frozen, read-only now) — kept alive for one release as the
  // seeding source for control groups below; nothing writes to it anymore
  // Groups absorb the Favorites row and its hotkeys.
  const [favorites] = usePersistentBrushBucket(FAVORITE_BRUSHES_KEY, xmlPath, tileset);
  // StarCraft-style control groups (1-9) — replaces Favorites. A slot
  // holds an armed brush or a copied sprite group; Ctrl+N saves, N
  // recalls (dispatcher below); the rail's star toggle writes into the
  // first empty slot (toggleFavorite, name kept for prop compatibility).
  const [controlGroups, setControlGroup] = usePersistentControlGroups(xmlPath, tileset);
  // One-time upgrade path: seed empty groups from Favorites the first
  // time this (xmlPath, tileset) bucket is seen with real favorites and
  // no group state yet. `favorites` is frozen (nothing writes to it any
  // more), so this settles after at most one real seed per bucket —
  // seedFromFavorites' own "every slot null" guard makes re-runs no-ops.
  useEffect(() => {
    if (favorites.length === 0) return;
    if (!controlGroups.every((g) => g === null)) return;
    const seeded = seedFromFavorites(controlGroups, favorites);
    seeded.forEach((g, i) => { if (g) setControlGroup(i, g); });
  }, [favorites, controlGroups, setControlGroup]);
  const toggleFavorite = useCallback((b: ActiveBrush) => {
    const idx = controlGroups.findIndex((g) => g === null);
    if (idx === -1) {
      log?.append({
        severity: "warn",
        message: "All 9 groups are full — recall one (1-9), then Ctrl+that number to overwrite it.",
      });
      return;
    }
    setControlGroup(idx, { kind: "brush", brush: b });
    log?.append({
      severity: "info",
      message: `Saved ${b.sti_filename.replace(/\.sti$/i, "")} to group ${idx + 1}.`,
    });
  }, [controlGroups, setControlGroup, log]);
  // Ref mirror so the global keydown listener reads control groups without
  // re-binding on every save (matches the toggleBrowseAssetsRef pattern).
  const controlGroupsRef = useRef<ControlGroup[]>([]);
  useEffect(() => { controlGroupsRef.current = controlGroups; }, [controlGroups]);

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

  // ─── Tool + active brush ───────────────────────────────────────────
  // Initial tool comes from user settings. Subsequent changes (e.g.
  // via the hotkey dispatcher or the toolbar selector) override.
  const [toolState, setTool] = useState<Tool>(() => loadSettings().defaultTool);
  const [activeBrush, setActiveBrush] = useState<ActiveBrush | null>(null);
  // One-shot shape armed from the command card (mode-less only — the card
  // itself is a later phase; nothing sets this yet, but the derivation
  // below already honours it so that phase is a pure UI add). The next
  // drag draws that shape, then the arm clears (commitShape →
  // setOneShotShape(null)).
  const [oneShotShape, setOneShotShape] = useState<ShapeKind | null>(null);
  // Payload (R4): what pencil/shape strokes DO — place the brush, erase the
  // non-ground layers, set per-tile height, or write a room id. Replaces the
  // old erase toggle + the Height tool + the Room shape-kind, so Erase /
  // Height / Room now work with BOTH the pencil radius and the shape tools.
  // MOVED UP from its original home (next to brushRadius/heightMode below)
  // because the mode-less `tool` derivation right after needs it, and
  // `tool` itself is read in several effect dependency arrays further
  // down the file — a `const` declared where `tool` used to live would
  // throw (TDZ) at render for those earlier reads.
  const [payload, setPayload] = useState<Payload>("tiles");
  // Mode-less model (spec D1): the internal tool is DERIVED from what is
  // armed — nothing → select (box-select + sprite pick), a brush or a
  // non-tile payload → pencil, a one-shot shape → shape. The legacy bar
  // (settings.legacyTools) restores the explicit tool state.
  const modeless = !settings.legacyTools;
  const tool: Tool = modeless
    ? (oneShotShape ? "shape" : (activeBrush || payload !== "tiles") ? "pencil" : "select")
    : toolState;
  // Stable mirror for closures that can't list `modeless` as a dep
  // (event handlers declared once, hotkey dispatcher, etc.).
  const modelessRef = useRef(modeless);
  useEffect(() => { modelessRef.current = modeless; }, [modeless]);
  // Sprite pick / hover outline / Shift+drag move live on the inspect tool
  // today; in the mode-less model they also live on select.
  const inspectLike = tool === "inspect" || (modeless && tool === "select");
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
  // `armGroup` (declared further down, once `renderer` exists) assigns
  // itself here every render — a ref indirection so `armBrush` (declared
  // now, before `renderer`) can arm a ghost without forward-referencing
  // a callback that isn't declared yet.
  const armGroupRef = useRef<((group: SpriteGroup, label?: string) => void) | null>(null);
  // Arming a brush from ANY surface (palette, recent rail, just-added,
  // eyedropper, inspector) selects the pencil too — a pick means the user
  // wants to paint with it. Centralized so no pick site can forget: the
  // palette + rail picks used to set the brush but leave the tool on
  // Inspect, so the user's first click silently did nothing.
  //
  // Mode-less (spec D8): sprite-family art (vehicle/landmark/scatter/
  // vegetation/sign) arms a placement GHOST instead of a paint brush;
  // walls/doors/windows/roofs/floors stay drag-brushes (laid in runs).
  const armBrush = useCallback((b: ActiveBrush | null) => {
    if (modelessRef.current && b && !UNCATEGORIZED_BRUSH_SOURCES.has(b.category) && armKindFor(b) === "ghost") {
      // This branch returns before `setActiveBrush` — the recent-picks
      // effect above only fires on an `activeBrush` change, so a ghost
      // arm would otherwise never land in the Recent rail / BrushChip
      // (review finding #12). Same bookkeeping, called directly.
      setRecentBrushes((prev) =>
        [b, ...prev.filter((x) => !sameBrush(x, b))].slice(0, RECENT_BRUSHES_CAP)
      );
      armGroupRef.current?.({
        sourceTileset: tileset, sourceSector: "palette", w: 1, h: 1,
        items: [{ dx: 0, dy: 0, layer: b.layer as LayerName, slot: b.slot, sub: b.sub }],
      }, b.sti_filename.replace(/\.sti$/i, ""));
      return;
    }
    setActiveBrush(b);
    if (b) setTool("pencil");
  }, [tileset]);
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
  // ─── Select / region copy-paste state ─────────────────────────────
  // selectAnchor/selectCursor mirror shapeAnchor/shapeCursor: anchor =
  // where the marquee drag started, cursor = the live end-point. Both
  // null when no select drag is in progress. selectRect is the COMMITTED
  // rectangle (set on mouseup) — it persists so the Copy button has a
  // region to slice. clipboard holds the last copied region — PERSISTED per
  // (xmlPath, tileset) (R6), so a region copied in one sector pastes in
  // another sector of the same tileset. pasteMode = modal "click the map to
  // drop the clipboard"; the next canvas click places the paste.
  const [selectAnchor, setSelectAnchor] = useState<Tile | null>(null);
  const [selectCursor, setSelectCursor] = useState<Tile | null>(null);
  const [selectRect, setSelectRect] = useState<{ a: Tile; b: Tile } | null>(null);
  const [clipboard, setClipboard] = usePersistentClipboard(xmlPath, tileset);
  const [pasteMode, setPasteMode] = useState(false);
  // Synchronous re-entrancy latch for paste (see doPaste). A ref, not
  // state, because it must read/write within a single event tick.
  const pasteBusyRef = useRef(false);
  // Selection lifecycle. The route never remounts on a `?tileset=` /
  // `?dat=` change (same component, new search params), so the
  // sector-specific marquee + armed-paste state must be reset by hand. The
  // clipboard is NOT reset here — it's persisted per (xmlPath, tileset)
  // (usePersistentClipboard) so a copied region survives a same-tileset
  // sector switch (cross-sector paste) and rehydrates on a tileset change.
  useEffect(() => {
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
  // Legacy-tools-only: mode-less routes Escape through the "cancel"
  // action/cancelOneLevel instead (this effect would double-handle it).
  useEffect(() => {
    if (modelessRef.current) return;
    if (!pasteMode) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") { e.preventDefault(); setPasteMode(false); }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [pasteMode]);
  // Delete / Backspace clears the committed marquee selection (R4). Own
  // effect (gated on a live selection) so it doesn't re-bind the global
  // dispatcher; the input-focus guard keeps it out of text fields.
  // Mode-less: Delete goes to the SPRITE selection below
  // instead — this REGION (terrain) delete is legacy-tools-only.
  useEffect(() => {
    if (modeless) return;
    if (tool !== "select" || !selectRect) return;
    const onKey = (e: KeyboardEvent) => {
      const tag = (e.target as HTMLElement | null)?.tagName?.toLowerCase();
      if (tag === "input" || tag === "textarea" || tag === "select") return;
      if (e.key === "Delete" || e.key === "Backspace") {
        e.preventDefault();
        void doDeleteSelection();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
    // doDeleteSelection reads live state via stable session_id/renderer +
    // the selectRect in deps; re-binding only on tool/selection change.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [modeless, tool, selectRect]);

  // ─── Mode-less sprite selection ───────────────────────────────────────
  const [selection, setSelection] = useState<SpriteRef[]>([]);
  const selectionRef = useRef<SpriteRef[]>([]);
  useEffect(() => { selectionRef.current = selection; }, [selection]);
  const [spriteClipboard, setSpriteClipboard] = usePersistentSpriteClipboard(xmlPath, tileset);
  // Placement tables (categories/tiers/fences/road slot) — fetched from the
  // sidecar's placement oracle whenever the resolved tileset (or a
  // sidecar restart) changes. Stay at EMPTY_TABLES while the fetch is in
  // flight or on failure — `categoryOf` returns null for every slot, so
  // `localCheck`'s solid-struct branch (ROOF/ROAD/TILE) never runs and
  // only BOUNDS can fire (the honest answer when the sidecar/sitekit isn't
  // reachable, rather than a stale or half-populated table).
  const [placementTables, setPlacementTables] = useState<PlacementTables>(EMPTY_TABLES);
  // True once a placement-tables fetch or placement-check round-trip has
  // failed since the last success — the status line prefixes
  // "oracle offline · " while set. Mirrored into a ref so the (frequent)
  // per-hover check effect below can tell "still offline" from "just went
  // offline" without depending on this state (which would re-fire it).
  const [oracleOffline, setOracleOffline] = useState(false);
  const oracleOfflineRef = useRef(false);
  // Shared offline/online transition logger for BOTH this tables fetch and
  // the per-hover check effect further down — whichever fails FIRST logs
  // once (via the ref guard), and a later success from EITHER path flips
  // it back with one "back online" line. Fixes review finding #1: before,
  // each effect gated its own log on this same ref but only the check
  // effect ever set it on failure with a log call — a sidecar that was
  // already down at load flipped the ref via the tables effect (silently)
  // and the check effect then saw "already offline" and never logged
  // either, so nothing was ever printed.
  const markOracleOnline = () => {
    if (oracleOfflineRef.current) {
      log?.append({ severity: "success", message: "Placement oracle back online." });
    }
    oracleOfflineRef.current = false;
    setOracleOffline(false);
  };
  const markOracleOffline = (detail: string) => {
    if (!oracleOfflineRef.current) {
      log?.append({ severity: "warn", message: detail });
    }
    oracleOfflineRef.current = true;
    setOracleOffline(true);
  };
  useEffect(() => {
    // tileset 0 is a REAL registered tileset ("GENERIC 1") — the sidecar's
    // session-open deliberately keeps sess.tileset = 0 when the map header
    // says so, so gating this fetch on `tileset > 0` would permanently
    // disable placement tables for those maps. Gate on a session being
    // open instead: no session → no fetch, which also makes the transient
    // pre-session `tileset` value moot (nothing to skip — there's simply
    // nothing to fetch yet).
    if (!session?.session_id) return;
    let live = true;
    getPlacementTables(tileset)
      .then((t) => {
        if (!live) return;
        setPlacementTables(t);
        markOracleOnline();
      })
      .catch(() => {
        if (!live) return;
        setPlacementTables(EMPTY_TABLES);
        markOracleOffline("Placement oracle offline — validity is local-only (BOUNDS).");
      });
    return () => { live = false; };
    // eslint-disable-next-line react-hooks/exhaustive-deps -- log is a
    // stable context value, omitted to avoid re-fire churn.
  }, [tileset, session?.session_id, sessionRestartEpoch]);
  // Selection cleared on sector/tileset switch or sidecar restart.
  useEffect(() => { setSelection([]); }, [datPath, tilesetParam, sessionRestartEpoch]);

  // Room id written by the "Mark region as room" shape. The toolbar lets
  // the user retarget an existing room or pick 0 to clear membership.
  const [roomId, setRoomId] = useState(1);
  // Highest room id actually painted since this editor mounted. The
  // "New room (N)" suggestion is otherwise computed from the `info`
  // query — a snapshot of the file as it was on disk at open — so a
  // second "new room" in the same sitting would offer the same N again
  // and silently merge the two regions into one room.
  const [maxPaintedRoomId, setMaxPaintedRoomId] = useState(0);
  // Per-generator-stream op counter used to throttle setRenderEpoch
  // bumps during a long stream. Mirrors each op into the renderer, but
  // only triggers a React repaint every N ops — without throttling
  // either we choke on 25k repaints (bump per op) or freeze the canvas
  // for 10 s (no bump at all).
  const genPanelOpCount = useRef(0);
  const consoleOpCount = useRef(0);

  // ─── Client-side renderer state ───────────────────────────────────
  // The renderer holds the atlas, the darken atlas, and a local copy
  // of the parsed sector. Edits mutate `renderer.parsed` directly so
  // re-renders are instant; the backend session is still authoritative
  // for save, and applyEdits round-trips mirror local mutations.
  const [renderer, setRenderer] = useState<IsoRenderer | null>(null);
  const [renderMeta, setRenderMeta] = useState<RenderMeta | null>(null);
  // Dynamic zoom floor: a 360x360 bigmap's canvas (14520x7440) can never fit
  // the viewport at the classic 0.25 floor -- let big maps zoom out until the
  // whole canvas fits the window (with a little margin); small maps keep 0.25.
  const minZoomRef = useRef(0.25);
  useEffect(() => {
    if (!renderMeta) { minZoomRef.current = 0.25; return; }
    const fit = Math.min(
      window.innerWidth / renderMeta.canvasW,
      window.innerHeight / renderMeta.canvasH,
    );
    minZoomRef.current = Math.max(0.02, Math.min(0.25, fit * 0.9));
  }, [renderMeta]);
  const [renderError, setRenderError] = useState<string | null>(null);
  // Tactical appendix overlay state.
  const [appendix, setAppendix] = useState<AppendixEntities | null>(null);
  const [showItems, setShowItems] = useState(false);
  const [showEntries, setShowEntries] = useState(true);
  const [showExits, setShowExits] = useState(true);
  const [showSoldiers, setShowSoldiers] = useState(true);
  const [showLights, setShowLights] = useState(false);
  const [showDoors, setShowDoors] = useState(false);
  const [showEdges, setShowEdges] = useState(false);
  const [showSchedules, setShowSchedules] = useState(false);
  // Sprite cache for soldier body-type sprites. Keyed by `${body_type}-${facing}`.
  // Value is {url, w, h} on success, null sentinel on failure (→ circle fallback).
  const [spriteCache, setSpriteCache] = useState<Map<string, { url: string; w: number; h: number } | null>>(new Map());
  useEffect(() => {
    if (!appendix || !showSoldiers) return;
    let cancelled = false;
    const pairs = new Map<string, { bt: number; dir: number }>();
    for (const s of appendix.soldiers) {
      const key = `${s.body_type}-${s.facing}`;
      if (!spriteCache.has(key)) pairs.set(key, { bt: s.body_type, dir: s.facing });
    }
    (async () => {
      await Promise.all(Array.from(pairs, async ([key, { bt, dir }]) => {
        try {
          const url = await mediaUrl(`/mapforge/soldier-sprite?bodytype=${bt}&dir=${dir}`);
          const img = new Image();
          await new Promise<void>((res, rej) => { img.onload = () => res(); img.onerror = () => rej(); img.src = url; });
          if (cancelled) return;
          setSpriteCache((m) => new Map(m).set(key, { url, w: img.naturalWidth, h: img.naturalHeight }));
        } catch {
          if (cancelled) return;
          setSpriteCache((m) => new Map(m).set(key, null));
        }
      }));
    })();
    return () => { cancelled = true; };
  }, [appendix, showSoldiers]); // eslint-disable-line react-hooks/exhaustive-deps
  // Item sprite cache for BIGITEMS graphics. Keyed by String(usItem).
  // Value is {url, w, h} on success, null sentinel on failure (→ circle fallback).
  const [itemCache, setItemCache] = useState<Map<string, { url: string; w: number; h: number } | null>>(new Map());
  useEffect(() => {
    if (!appendix || !showItems) return;
    let cancelled = false;
    const ids = new Set<number>();
    for (const it of appendix.items) {
      if (!itemCache.has(String(it.usItem))) ids.add(it.usItem);
    }
    (async () => {
      await Promise.all(Array.from(ids, async (usItem) => {
        const key = String(usItem);
        try {
          const url = await mediaUrl(`/mapforge/item-graphic?item=${usItem}`);
          const img = new Image();
          await new Promise<void>((res, rej) => { img.onload = () => res(); img.onerror = () => rej(); img.src = url; });
          if (cancelled) return;
          if (img.naturalWidth === 0 || img.naturalHeight === 0) {
            setItemCache((m) => new Map(m).set(key, null));
          } else {
            setItemCache((m) => new Map(m).set(key, { url, w: img.naturalWidth, h: img.naturalHeight }));
          }
        } catch {
          if (cancelled) return;
          setItemCache((m) => new Map(m).set(key, null));
        }
      }));
    })();
    return () => { cancelled = true; };
  }, [appendix, showItems]); // eslint-disable-line react-hooks/exhaustive-deps
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
  // Sprite-aware pick (inspect tool): the struct sprite under the cursor
  // + its OWNING tile. Solves "which square do I click to select the
  // cooling tower" — big sprites live on one anchor tile the visual
  // extends far away from. Probed on hovered-TILE change (not every
  // mousemove); Ctrl bypasses it for a raw tile pick.
  const [spriteHit, setSpriteHit] = useState<SpriteHit | null>(null);
  const spriteProbeTileRef = useRef<{ x: number; y: number } | null>(null);
  // The exact entry a sprite-click pinned (layer/slot/sub) — the Delete
  // hotkey removes THIS entry; null for plain tile pins (falls back to
  // the topmost visible entry on the pinned tile).
  const pinnedPickRef = useRef<{ layer: LayerName; slot: number; sub: number } | null>(null);
  // Grab-and-move — click to grab and move an object such as a
  // truck: Shift+mousedown on a sprite in INSPECT arms a move;
  // mouseup over another tile drops it (moveEntry). Escape cancels.
  const moveRef = useRef<{ hit: SpriteHit } | null>(null);
  const [moving, setMoving] = useState(false);
  useEffect(() => {
    if (!moving) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") { moveRef.current = null; setMoving(false); }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [moving]);
  // Delete / Backspace in INSPECT mode removes the pinned entry — the
  // sprite-picked one when the pin came from a sprite click, else the
  // topmost visible entry on the pinned tile. Same input-focus guard as
  // the select-tool delete; undoable, no confirm (the hotkey is for speed).
  // Mode-less: the sprite SELECTION delete wins whenever a
  // selection is live — checked inside onKey (against the live ref, not
  // the effect's own re-run condition) so a selection made/cleared after
  // this effect last bound still takes precedence correctly.
  useEffect(() => {
    if (!inspectLike || !pinned) return;
    const onKey = (e: KeyboardEvent) => {
      const tag = (e.target as HTMLElement | null)?.tagName?.toLowerCase();
      if (tag === "input" || tag === "textarea" || tag === "select") return;
      if (modeless && selectionRef.current.length > 0) return;
      if (e.key === "Delete" || e.key === "Backspace") {
        e.preventDefault();
        void deletePinnedEntry();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
    // deletePinnedEntry reads live state; re-bind on tool/pin change only.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [inspectLike, pinned, modeless]);
  // Live Shift tracking. Drives the multi-tile stamp preview: hover
  // a multi-tile brush over the canvas → outline diamonds appear on
  // every footprint tile. Holding Shift inverts stamp/manual mode for
  // the next paint, so we hide the preview when Shift flips us into
  // manual mode (no stamp will happen). Keeps the preview honest.
  const [shiftHeld, setShiftHeld] = useState(false);
  // Ref mirror for the placement ghost's `run()` closure (armGroup below)
  // — Shift+click keeps the ghost armed; that closure is created once per
  // arm and must read the LIVE shift state at click time, not whatever it
  // was when the ghost was armed.
  const shiftHeldRef = useRef(false);
  useEffect(() => { shiftHeldRef.current = shiftHeld; }, [shiftHeld]);
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
  // `payload` now declared up near `tool` (see the mode-less derivation
  // comment there) — `tool`'s derivation needs it before several effects
  // between here and there read `tool`.
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
  // Edit journal (R6 recovery). While the session is dirty, persist a
  // breadcrumb (sessionId + edit count) keyed by datPath so a reload/
  // crash can reconnect to the still-live in-memory session on reopen
  // (the sidecar never evicts a dirty session). Cleared the instant the
  // session goes clean (saved → on disk, nothing to recover) or when
  // there's no editable session. The recovery probe in the open-session
  // effect clears stale entries whose session is gone. Date.now() is
  // fine here — this is frontend app code, not a workflow script.
  useEffect(() => {
    if (session && !session.read_only && localDirty) {
      writeJournalEntry(datPath, {
        sessionId: session.session_id,
        editCount: session.edit_count,
        savedAt: Date.now(),
      });
    } else if (!localDirty) {
      // Clean (or never-dirtied) → nothing unsaved to recover.
      clearJournalEntry(datPath);
    }
  }, [datPath, session, localDirty]);
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
    // Sticky picks (the Generate panel's region aim) RE-ARM after each
    // completion instead of clearing, so dragging a new box re-aims the
    // generator without pressing a button. One-shot picks clear on
    // completion as before.
    sticky?: boolean;
  } | null>(null);
  // Single-click "focal point" pick for the density-falloff generator —
  // sticky like region pick: every left click on the map sets the focal
  // point (radius comes from the slider, not a box). Armed by the
  // Generate panel; cleared by cancelRegionPick / generator change / ESC.
  const [pickingPoint, setPickingPoint] = useState<{
    onPick: (t: { x: number; y: number }) => void;
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
    /** Set when this ghost is a mode-less sprite-group arm (armGroup) —
     * carries the SAME items `region` was built from, so the local
     * validity check below can re-run localCheck against the
     * live occupancy without re-deriving anchors from `region`. */
    group?: SpriteGroup;
    run: (x: number, y: number) => void | Promise<void>;
  } | null>(null);
  // True while a placement stamp's backend round-trip is in flight —
  // hides the sprite ghost overlay (so the freshly-stamped tiles are
  // visible, not double-drawn under it) and blocks further stamp clicks.
  const [placementStampBusy, setPlacementStampBusy] = useState(false);
  // Exit placement mode when the user switches tools, sectors, tilesets
  // or the sidecar restarts — a stale run() closure must never fire
  // against a different session. Depends on `toolState` (the EXPLICIT
  // legacy tool), not the derived `tool`: `armGroup` clears `activeBrush`,
  // which flips the derived tool pencil→select, and that would cancel
  // the ghost `armGroup` just armed if this effect watched `tool`.
  useEffect(() => {
    setPlacingBuilding(null);
  }, [toolState, datPath, tilesetParam, sessionRestartEpoch]);
  // Arming a paint brush is an explicit "I'm painting now, not placing" —
  // exit building placement. (The tool-change effect above misses it:
  // arming a brush leaves you on the pencil, so the tool often doesn't
  // change and placement would stay stuck armed — user-reported.)
  useEffect(() => {
    if (activeBrush) setPlacingBuilding(null);
  }, [activeBrush]);
  // Building placement and the generator's sticky canvas region-pick are
  // mutually exclusive: mousedown checks the region pick FIRST, so an
  // armed pick would swallow placement clicks. Arming a building cancels
  // any region pick. (The reverse — selecting a generator disarms a
  // building — is handled panel-side in selectGenerator.)
  useEffect(() => {
    if (placingBuilding) { setPickingRect(null); setPickingPoint(null); }
  }, [placingBuilding]);
  // ESC exits placement mode (mirrors the region picker's ESC effect).
  // Mode-less: `cancelOneLevel` already handles Esc on an
  // armed ghost via the rebindable "cancel" action — skip here so a
  // single Esc press doesn't get handled twice.
  useEffect(() => {
    if (modelessRef.current) return;
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
  // ─── Fence line-drag + placement queue ───────────────────────────────
  // `lineAnchor`: the tile a fence-armed drag started from (mousedown in
  // onCanvasMouseDown); null once no drag is in flight — everything else
  // about the line (topology subs, synthesized ghost group, verdicts) is
  // a DERIVED memo off (lineAnchor, hovered), same pattern as the
  // pre-existing placement ghost/verdicts below, not state written
  // imperatively from the mouse handlers.
  // `queue`: StarCraft-style stacked ghosts from Shift+click — `render`
  // is route-only display state alongside the pure QueuedGhost shape
  // (anchor+group) `queueCommitEdits` consumes.
  const [lineAnchor, setLineAnchor] = useState<Tile | null>(null);
  const [queue, setQueue] = useState<QueuedPlacement[]>([]);
  const queueRef = useRef<QueuedPlacement[]>([]);
  useEffect(() => { queueRef.current = queue; }, [queue]);
  // Disarming via ANY path (tool/session/sector change, arming a brush,
  // Esc dropping the ghost entirely, or a completed plain-click place)
  // must drop both — a stale line/queue must never survive into whatever
  // gets armed next. Re-arming a DIFFERENT group without disarming first
  // (e.g. recalling a control group while another is already armed)
  // deliberately does NOT clear the queue — only an actual `null` gets here.
  useEffect(() => {
    if (!placingBuilding) { setLineAnchor(null); setQueue([]); }
  }, [placingBuilding]);
  // Bumped on every local mutation that should re-paint the canvas.
  // The render effect depends on this so React schedules a paint after
  // each edit. (Mutating `renderer.parsed` doesn't itself trigger React.)
  const [renderEpoch, setRenderEpoch] = useState(0);
  // ─── Generator ghost preview ────────────────────────────────────────
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

  // ─── Fence line-drag ────────────────────────────────────────────────
  // Armed when the ghost is exactly ONE `structs` item whose category is
  // `fence`. `lineDrag` derives everything from (fenceArmed, lineAnchor,
  // hovered) — the topology-derived subs, a synthesized single-item-per-
  // tile SpriteGroup relative to the line's min tile, and whether a real
  // topology table exists for this slot (falls back to the armed sub for
  // every tile, straight runs only, when it doesn't).
  const fenceArmed = useMemo(() => {
    const items = placingBuilding?.group?.items;
    if (!placingBuilding?.group || !items || items.length !== 1) return null;
    const it = items[0]!;
    if (it.layer !== "structs") return null;
    if (categoryOf(placementTables, it.slot, it.sub) !== "fence") return null;
    return { slot: it.slot, sub: it.sub, group: placingBuilding.group };
  }, [placingBuilding, placementTables]);
  const lineDrag = useMemo(() => {
    if (!fenceArmed || !lineAnchor || !hovered || !renderer) return null;
    if (lineAnchor.x === hovered.x && lineAnchor.y === hovered.y) return null;
    const parsed = renderer.getParsed();
    const { slot, sub } = fenceArmed;
    const inBounds = (x: number, y: number) => x >= 0 && y >= 0 && x < parsed.cols && y < parsed.rows;
    const existing = (x: number, y: number) =>
      inBounds(x, y) && (parsed.structs[y * parsed.cols + x] ?? []).some((e) => e[0] === slot);
    const isRoad = (x: number, y: number) =>
      inBounds(x, y) && (parsed.objs[y * parsed.cols + x] ?? []).some((e) => e[0] === placementTables.roadSlot);
    const lineTiles = shapeTiles("line", lineAnchor, hovered);
    const subsTable = placementTables.fences[String(slot)];
    const entries = subsTable
      ? fenceSubsForLine(lineTiles, slot, subsTable, existing, isRoad)
      : lineTiles.map((t) => ({ x: t.x, y: t.y, sub, ...(isRoad(t.x, t.y) ? { skipped: "ROAD" as const } : {}) }));
    const kept = entries.filter((e) => e.skipped !== "ROAD");
    const xs = lineTiles.map((t) => t.x); const ys = lineTiles.map((t) => t.y);
    const minX = Math.min(...xs); const minY = Math.min(...ys);
    const group: SpriteGroup = {
      sourceTileset: fenceArmed.group.sourceTileset, sourceSector: fenceArmed.group.sourceSector,
      w: Math.max(...xs) - minX + 1, h: Math.max(...ys) - minY + 1,
      items: kept.map((e) => ({ dx: e.x - minX, dy: e.y - minY, layer: "structs" as const, slot, sub: e.sub })),
    };
    return { lineTiles, anchor: { x: minX, y: minY }, group, hasTable: !!subsTable };
    // renderEpoch: `existing`/`isRoad` read the live parsed sector, which
    // mutates in place (undo, an edit landing mid-drag) — same reasoning
    // as the sibling ghost/occupancy memos below.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [fenceArmed, lineAnchor, hovered, renderer, placementTables, renderEpoch]);

  const placementGhost = useMemo(() => {
    if (!renderer) return null;
    if (lineDrag) return renderer.renderRegionToCanvas(groupToRegionTiles(lineDrag.group, (s) => renderer.getFootprint(s)), 0.7);
    const region = placingBuilding?.region;
    if (!region) return null;
    return renderer.renderRegionToCanvas(region.tiles, 0.7);
    // renderEpoch: re-render after an atlas hot-swap (replaceAtlas keeps
    // the renderer identity but changes the cellMap).
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [placingBuilding, renderer, renderEpoch, lineDrag]);

  // ─── Placement validity ────────────────────────────────────────────
  // Struct occupancy index for the instant local check — rebuilt when the
  // parsed sector changes (renderEpoch bumps on every local edit). Review
  // finding #10: `buildOccupancy` walks the WHOLE sector (~130k tiles) —
  // only worth paying for while something can actually consult it (a
  // ghost armed, a selection to nudge, or a Shift+drag move in flight);
  // otherwise it's an empty index and the memo skips the scan.
  const occupancy = useMemo<Occupancy>(() => {
    if (!renderer || !(placingBuilding?.group || selection.length > 0 || moving)) {
      return new Map<number, { ref: SpriteRef; cat: string }[]>();
    }
    return buildOccupancy(renderer.getParsed(), placementTables, (s) => renderer.getFootprint(s));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [renderer, placementTables, renderEpoch, placingBuilding, selection, moving]);
  // Ref mirror so the hotkey dispatcher and other handlers reachable from
  // a stale closure (review finding #10b) always read the CURRENT
  // occupancy without needing it in their own effect's deps array.
  const occupancyRef = useRef<Occupancy>(occupancy);
  useEffect(() => { occupancyRef.current = occupancy; }, [occupancy]);
  // Local-only verdicts for the LINE — no queue, no oracle
  // (the instant local pass is all a line drag needs); ROAD entries are
  // already excluded from `lineDrag.group`'s items, so they never appear
  // here as a candidate at all (a gap, not a red tile).
  const lineVerdicts = useMemo<TileVerdict[]>(() => {
    if (!lineDrag || !renderer) return [];
    return localCheck(renderer.getParsed(), placementTables, occupancy,
      groupRefsAt(lineDrag.group, lineDrag.anchor), (s) => renderer.getFootprint(s));
  }, [lineDrag, renderer, placementTables, occupancy]);
  // Per-footprint-tile verdicts for the armed sprite ghost at the hovered
  // tile — the INSTANT local pass (BOUNDS/ROOF/ROAD/TILE); the debounced
  // sidecar oracle below refines it (RING/CONTACT/INVERSION, plus a
  // second opinion on the local tests) into `verdicts`. The
  // candidate list is `[...queued refs, ...current refs]` so an earlier
  // QUEUED ghost counts as occupied for the one about to be placed
  // `localCheck`'s own batch rule does that automatically — but
  // only the CURRENT group's tail entries are returned for display (the
  // queued ghosts get their own dashed outline via `queuedTiles`, not a
  // verdict tint). A second `localCheck` over JUST the current refs
  // (identical tiles, occupancy unaffected by queue-batching) tells us
  // how many tail entries are "ours" to slice off.
  const localVerdicts = useMemo<TileVerdict[]>(() => {
    const grp = placingBuilding?.group;
    if (!grp || !hovered || !renderer) return [];
    const fp = (s: number) => renderer.getFootprint(s);
    const currentRefs = groupRefsAt(grp, hovered);
    const currentOnly = localCheck(renderer.getParsed(), placementTables, occupancy, currentRefs, fp);
    if (queue.length === 0) return currentOnly;
    const queueRefs = queue.flatMap((q) => groupRefsAt(q.group, q.anchor));
    const combined = localCheck(renderer.getParsed(), placementTables, occupancy,
      [...queueRefs, ...currentRefs], fp);
    return combined.slice(combined.length - currentOnly.length);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [placingBuilding, hovered, renderer, placementTables, occupancy, queue]);
  // Oracle refinement (sidecar placement/check) for the SAME
  // (group, hovered) anchor as `localVerdicts` — populated by the debounced
  // check effect below. Empty whenever no ghost is armed or the last check
  // for the current anchor hasn't resolved yet (never a stale anchor's
  // verdicts merged into a live one).
  const [oracleVerdicts, setOracleVerdicts] = useState<TileVerdict[]>([]);
  const verdicts = useMemo(() => mergeVerdicts(localVerdicts, oracleVerdicts), [localVerdicts, oracleVerdicts]);
  const verdictsRef = useRef<TileVerdict[]>([]);
  useEffect(() => { verdictsRef.current = verdicts; }, [verdicts]);
  // (x,y) anchor a debounced oracle check is currently in flight for. The
  // status line shows a pending glyph only while this still matches
  // `hovered` AND the local pass is green (a local red/yellow already has
  // something definitive to say; the oracle can only refine it further).
  const [oraclePendingAnchor, setOraclePendingAnchor] = useState<{ x: number; y: number } | null>(null);
  // True latest values for the async check's resolve/catch to compare
  // against — NOT the anchor closed over when the request was issued.
  // Updated every render those values change, independent of the debounced
  // check effect's own dependency list, so a ghost disarmed or moved while
  // a check is in flight is detected even though `seq` alone wouldn't catch
  // a disarm (cancel() can't abort an already-in-flight fetch).
  const hoveredRef = useRef(hovered);
  useEffect(() => { hoveredRef.current = hovered; }, [hovered]);
  const placingGroupRef = useRef<SpriteGroup | null>(placingBuilding?.group ?? null);
  useEffect(() => { placingGroupRef.current = placingBuilding?.group ?? null; }, [placingBuilding]);
  const oracleSeqRef = useRef(0);
  // Lazily built so a throwaway debounce instance isn't constructed on
  // every render — only the very first mount pays for it.
  const oracleDebounceRef = useRef<ReturnType<typeof trailingDebounce<[() => void]>> | null>(null);
  if (!oracleDebounceRef.current) {
    oracleDebounceRef.current = trailingDebounce(80, (run: () => void) => run());
  }
  // (group, x, y) of the last anchor a check was ISSUED for — read fresh on
  // every effect run, not just on resolve. Review finding #2: clearing
  // `oracleVerdicts` on every effect run (including a `renderEpoch` bump at
  // the SAME anchor+group) blanked a just-confirmed RING/CONTACT block back
  // to green for the debounce+round-trip window, and `commitQueueAndPlace`
  // (which reads `verdictsRef.current`) could place during that window. Now the
  // clear only happens when the anchor or the group actually changed; a
  // same-key recheck (an edit at the same hover) leaves the last-confirmed
  // verdict on screen until the fresh response replaces it in place.
  const prevOracleCheckKeyRef = useRef<{ grp: SpriteGroup; x: number; y: number } | null>(null);
  useEffect(() => {
    let live = true;
    const grp = placingBuilding?.group;
    if (!grp || !hovered || !session) {
      oracleDebounceRef.current!.cancel();
      prevOracleCheckKeyRef.current = null;
      setOracleVerdicts((v) => (v.length > 0 ? [] : v));
      setOraclePendingAnchor(null);
      return () => { live = false; };
    }
    const sessionId = session.session_id;
    const anchor = { x: hovered.x, y: hovered.y };
    const prevKey = prevOracleCheckKeyRef.current;
    const sameAnchorAndGroup = prevKey?.grp === grp && prevKey.x === anchor.x && prevKey.y === anchor.y;
    prevOracleCheckKeyRef.current = { grp, x: anchor.x, y: anchor.y };
    if (!sameAnchorAndGroup) {
      setOracleVerdicts((v) => (v.length > 0 ? [] : v));
    }
    if (oraclePendingAnchor?.x !== anchor.x || oraclePendingAnchor?.y !== anchor.y) {
      setOraclePendingAnchor(anchor);
    }
    const seq = ++oracleSeqRef.current;
    oracleDebounceRef.current!.call(() => {
      const stillCurrent = () =>
        live && seq === oracleSeqRef.current
        && placingGroupRef.current === grp
        && hoveredRef.current?.x === anchor.x && hoveredRef.current?.y === anchor.y;
      checkPlacement(sessionId, groupRefsAt(grp, anchor))
        .then((res) => {
          if (!stillCurrent()) return;
          // Real footprint fn (not a stub) — oracleToVerdicts now expands a
          // multi-tile candidate's verdict onto every one of its footprint
          // tiles, not just the sidecar-reported offending tile.
          setOracleVerdicts(oracleToVerdicts(res.results, renderer ? (s) => renderer.getFootprint(s) : () => null));
          setOraclePendingAnchor(null);
          markOracleOnline();
        })
        .catch(() => {
          if (!stillCurrent()) return;
          setOracleVerdicts([]);
          setOraclePendingAnchor(null);
          markOracleOffline("Placement oracle offline — falling back to local-only placement checks.");
        });
    });
    // Cancel a still-pending (not yet fired) debounced call whenever this
    // effect re-runs (new anchor/edit superseding it — the debounce's own
    // trailing-edge replace already handles that case, this is belt &
    // braces) or the component unmounts (so a fetch that hadn't started
    // yet never fires against a dead route).
    return () => { live = false; oracleDebounceRef.current!.cancel(); };
    // eslint-disable-next-line react-hooks/exhaustive-deps -- log (via
    // markOracle{On,Off}line) is a stable context value, omitted to avoid
    // re-fire churn; oraclePendingAnchor is read for a skip-if-unchanged
    // check only, not a real trigger.
  }, [placingBuilding, hovered, session?.session_id, renderEpoch]);

  /** Arm a sprite ghost: reuses the building-
   * placement overlay — `run` fires on left mousedown at the hovered tile.
   * Shift+click ENQUEUES it and stays armed; a plain
   * click commits the whole queue plus this placement as one stroke, then
   * disarms on success. Red refuses (enforced in `enqueueGroup` /
   * `commitQueueAndPlace`). Fence line-drag bypasses `run`
   * entirely for a real drag — see onCanvasMouseDown/Up — `run` here is
   * still what a zero-length line (a plain click on a fence) falls back to. */
  const armGroup = useCallback((group: SpriteGroup, label?: string) => {
    if (!renderer) {
      log?.append({ severity: "warn", message: "Atlas still loading — try again in a moment." });
      return;
    }
    setActiveBrush(null); setPayload("tiles"); setOneShotShape(null);
    const region: ClipboardRegion = {
      sourceTileset: group.sourceTileset, sourceSector: group.sourceSector,
      w: group.w, h: group.h,
      tiles: groupToRegionTiles(group, (s) => renderer.getFootprint(s)).map((t) => ({ ...t, room: 0, height: 0 })),
    };
    setPlacingBuilding({
      w: group.w, h: group.h, group,
      label: label ?? (group.items.length === 1
        ? `s${group.items[0]!.slot}.${group.items[0]!.sub}`
        : `${group.items.length} sprites`),
      region,
      run: (x, y) => {
        // Capture Shift's state at CLICK time — the round-trip below can
        // take 100ms+, long enough for a quick single click's Shift to
        // have already released by the time `.then` runs.
        if (shiftHeldRef.current) {
          enqueueGroupRef.current({ x, y }, group);
          return;
        }
        return commitQueueAndPlaceRef.current({ x, y }, group).then((placed) => {
          if (placed) setPlacingBuilding(null);
        });
      },
    });
    // enqueueGroupRef / commitQueueAndPlaceRef / shiftHeldRef are stable
    // refs; renderer is the only real dependency (armBrush calls this
    // through armGroupRef, so its own identity churn on renderer swap is
    // harmless).
  }, [renderer]);
  armGroupRef.current = armGroup;

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
    // the hovered tile (or, mid fence line-drag, the line's min tile —
    // same tileToCanvasPixel math as the SVG overlay, plus the
    // region render's own bbox origin. Zoom needs no special handling:
    // the overlay canvas lives inside the same CSS-transformed wrapper
    // as the main canvas + SVG.
    const anchor = lineDrag ? lineDrag.anchor : hovered;
    const p = tileToCanvasPixel(anchor.x, anchor.y, renderMeta);
    cv.style.transform =
      `translate(${p.x + placementGhost.originX}px, `
      + `${p.y + placementGhost.originY}px)`;
    cv.style.display = "block";
  }, [placementGhost, hovered, placementStampBusy, renderMeta, lineDrag]);

  // ─── Placement queue overlay ───────────────────────────────────────
  // Unlike the single-ghost canvas above (tight bbox, retranslated per
  // hover), queued ghosts sit at MANY different anchors at once — so this
  // canvas spans the whole main render and each queued ghost's own
  // pre-rendered `render.canvas` (from `enqueueGroup`) is drawn at its
  // own anchor's pixel position, no CSS transform needed.
  const queueCanvasRef = useRef<HTMLCanvasElement | null>(null);
  useEffect(() => {
    const cv = queueCanvasRef.current;
    if (!cv) return;
    if (queue.length === 0 || !renderMeta) {
      cv.style.display = "none";
      return;
    }
    if (cv.width !== renderMeta.canvasW || cv.height !== renderMeta.canvasH) {
      cv.width = renderMeta.canvasW;
      cv.height = renderMeta.canvasH;
    }
    const ctx = cv.getContext("2d");
    if (!ctx) return;
    ctx.clearRect(0, 0, cv.width, cv.height);
    for (const q of queue) {
      if (!q.render) continue;
      const p = tileToCanvasPixel(q.anchor.x, q.anchor.y, renderMeta);
      ctx.drawImage(q.render.canvas, p.x + q.render.originX, p.y + q.render.originY);
    }
    cv.style.display = "block";
    // renderEpoch: re-render after an atlas hot-swap (cellMap changed) —
    // each queued render's canvas is itself frozen at enqueue time, but
    // this positions them fresh whenever renderMeta/zoom changes.
  }, [queue, renderMeta, renderEpoch]);
  // Full JSD-footprint tiles of every queued ghost, for the dashed
  // outline `IsoOverlay` draws (its own footprint, not just the anchor —
  // matches how the verdict/selection diamonds expand multi-tile structs).
  const queuedTiles = useMemo(() => {
    if (queue.length === 0 || !renderer) return [];
    const fp = (s: number) => renderer.getFootprint(s);
    const out: { x: number; y: number }[] = [];
    for (const q of queue) {
      for (const r of groupRefsAt(q.group, q.anchor)) {
        for (const ft of footprintTiles(r.x, r.y, r.slot, r.sub, fp)) out.push({ x: ft.x, y: ft.y });
      }
    }
    return out;
  }, [queue, renderer]);

  // ── Brush hover ghost ────────────────────────────────────────────────
  // The armed brush rendered as a translucent sprite at the hovered tile —
  // the same engine the building-placement ghost uses (renderRegionToCanvas).
  // For a multi-tile struct in stamp mode it shows the whole footprint
  // (variant-snapped to match what a click places); otherwise the single
  // (slot, sub) sprite. The radius / footprint OUTLINE overlays still draw
  // as a complement. Rendered ONCE per brush change; only retranslated per
  // hovered tile. A dedicated canvas so it never fights the placement ghost.
  const brushGhostCanvasRef = useRef<HTMLCanvasElement | null>(null);
  const brushGhost = useMemo(() => {
    if (!activeBrush || !renderer) return null;
    const layer = activeBrush.layer as LayerName;
    const empty = (): Record<LayerName, number[][]> =>
      ({ land: [], objs: [], shadows: [], structs: [], roofs: [], onroofs: [] });
    const footprint = activeBrush.forceSingleTile
      ? null
      : renderer.getFootprint(activeBrush.slot);
    // Default-mode preview (no Shift inversion): stamp when the slot has a
    // footprint AND paintMode is "stamp". Mirrors paintBrush's placement +
    // its variant-anchor sub snap.
    const stamp = footprint !== null && settings.paintMode === "stamp";
    let tiles: GhostRegionTile[];
    if (stamp && footprint) {
      const stride = footprint.tiles.length;
      const subDelta = Math.floor((activeBrush.sub - 1) / stride) * stride;
      tiles = footprint.tiles.map((ft) => {
        const layers = empty();
        layers[layer] = [[activeBrush.slot, ft.sub + subDelta]];
        return { dx: ft.bX, dy: ft.bY, layers };
      });
    } else {
      const layers = empty();
      layers[layer] = [[activeBrush.slot, activeBrush.sub]];
      tiles = [{ dx: 0, dy: 0, layers }];
    }
    return renderer.renderRegionToCanvas(tiles, 0.6);
    // renderEpoch: re-render after an atlas hot-swap (cellMap changed).
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activeBrush, renderer, settings.paintMode, renderEpoch]);
  useEffect(() => {
    const cv = brushGhostCanvasRef.current;
    if (!cv) return;
    // Pencil-only, and hidden while another overlay owns the ghost canvas
    // (building placement), the generator ghost is live, or a rectangle is
    // being picked.
    if (!brushGhost || !hovered || !renderMeta || tool !== "pencil"
        || placingBuilding || ghostActive || pickingRect || pickingPoint
        || payload !== "tiles") {
      cv.style.display = "none";
      return;
    }
    if (cv.width !== brushGhost.canvas.width
        || cv.height !== brushGhost.canvas.height) {
      cv.width = brushGhost.canvas.width;
      cv.height = brushGhost.canvas.height;
    }
    const ctx = cv.getContext("2d");
    if (!ctx) return;
    ctx.clearRect(0, 0, cv.width, cv.height);
    ctx.drawImage(brushGhost.canvas, 0, 0);
    const p = tileToCanvasPixel(hovered.x, hovered.y, renderMeta);
    cv.style.transform =
      `translate(${p.x + brushGhost.originX}px, ${p.y + brushGhost.originY}px)`;
    cv.style.display = "block";
  }, [brushGhost, hovered, renderMeta, tool, placingBuilding, ghostActive, pickingRect, pickingPoint, payload]);

  // ── Grab-and-move ghost ──────────────────────────────────────────────
  // Shift+click grabs a sprite (moveRef + setMoving); like every other
  // placement mode it needs a translucent sprite following the cursor so
  // you see WHERE it drops. Same engine as the brush hover ghost above —
  // the grabbed (slot, sub) rendered as one tile at 0.6 alpha. moveRef is
  // a ref, but `moving` flips true in the same handler right after it's
  // set, so gating on `moving` recomputes with moveRef.current already in.
  const moveGhostCanvasRef = useRef<HTMLCanvasElement | null>(null);
  const moveGhost = useMemo(() => {
    const mv = moveRef.current;
    if (!moving || !mv || !renderer) return null;
    const layers: Record<LayerName, number[][]> =
      { land: [], objs: [], shadows: [], structs: [], roofs: [], onroofs: [] };
    layers[mv.hit.layer] = [[mv.hit.slot, mv.hit.sub]];
    // Include the buddy shadow the move actually relocates (structs only:
    // shadows slot+1, same sub — the Estoni pairs moveEntry rides along)
    // so the ghost previews the shadow moving too, not just the sprite.
    if (mv.hit.layer === "structs") {
      const parsed = renderer.getParsed();
      const gn = mv.hit.y * parsed.cols + mv.hit.x;
      const sh = parsed.shadows[gn] ?? [];
      if (sh.some((en) => en && en[0] === mv.hit.slot + 1 && en[1] === mv.hit.sub)) {
        layers.shadows = [[mv.hit.slot + 1, mv.hit.sub]];
      }
    }
    return renderer.renderRegionToCanvas([{ dx: 0, dy: 0, layers }], 0.6);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [moving, renderer, renderEpoch]);
  useEffect(() => {
    const cv = moveGhostCanvasRef.current;
    if (!cv) return;
    if (!moveGhost || !moving || !hovered || !renderMeta) {
      cv.style.display = "none";
      return;
    }
    if (cv.width !== moveGhost.canvas.width || cv.height !== moveGhost.canvas.height) {
      cv.width = moveGhost.canvas.width;
      cv.height = moveGhost.canvas.height;
    }
    const ctx = cv.getContext("2d");
    if (!ctx) return;
    ctx.clearRect(0, 0, cv.width, cv.height);
    ctx.drawImage(moveGhost.canvas, 0, 0);
    const p = tileToCanvasPixel(hovered.x, hovered.y, renderMeta);
    cv.style.transform =
      `translate(${p.x + moveGhost.originX}px, ${p.y + moveGhost.originY}px)`;
    cv.style.display = "block";
  }, [moveGhost, moving, hovered, renderMeta]);

  // ─── Sprite selection outline ──────────────────────────────────────
  // Same technique as the placement/brush ghosts: render the SELECTED
  // sprites once to an offscreen canvas (full alpha, not translucent —
  // this is "what's selected", not a preview) and position it via CSS
  // transform at the selection's top-left tile. The CSS drop-shadow
  // filter gives the glow outline without a per-sprite alpha mask.
  const selectionCanvasRef = useRef<HTMLCanvasElement | null>(null);
  const selectionGhost = useMemo(() => {
    if (selection.length === 0 || !renderer) return null;
    const grp = sliceGroup(renderer.getParsed(), selection, tileset, "sel", isShadowOnlySlot);
    if (!grp) return null;
    const tiles = groupToRegionTiles(grp, (s) => renderer.getFootprint(s));
    return renderer.renderRegionToCanvas(tiles, 1.0);
    // `renderEpoch` is restored here (it had been dropped by an earlier
    // #10c had dropped it to avoid re-rendering this canvas on every edit
    // anywhere in the sector). That concern is now moot for the common
    // case — the `selection.length === 0` short-circuit above means this
    // memo does nothing while nothing is selected, which is when most
    // sector-wide edits (generators, paint strokes) happen. The rebuild
    // this re-adds only fires per edit while a selection is ACTIVELY live
    // (small, bounded cost) — and it's necessary: an edit elsewhere that
    // changes what a selected sprite's neighbor looks like (e.g. a
    // generator repainting the ground under it) must repaint this ghost,
    // not just a nudge/cycle that reassigns the selection's own refs.
    // eslint-disable-next-line react-hooks/exhaustive-deps -- `tileset` is
    // still not tracked (pre-existing gap, unrelated to this fix).
  }, [selection, renderer, renderMeta, renderEpoch]);
  useEffect(() => {
    const cv = selectionCanvasRef.current;
    if (!cv) return;
    if (!selectionGhost || selection.length === 0 || !renderMeta) {
      cv.style.display = "none";
      return;
    }
    if (cv.width !== selectionGhost.canvas.width || cv.height !== selectionGhost.canvas.height) {
      cv.width = selectionGhost.canvas.width;
      cv.height = selectionGhost.canvas.height;
    }
    const ctx = cv.getContext("2d");
    if (!ctx) return;
    ctx.clearRect(0, 0, cv.width, cv.height);
    ctx.drawImage(selectionGhost.canvas, 0, 0);
    const minX = Math.min(...selection.map((r) => r.x));
    const minY = Math.min(...selection.map((r) => r.y));
    const p = tileToCanvasPixel(minX, minY, renderMeta);
    cv.style.transform =
      `translate(${p.x + selectionGhost.originX}px, ${p.y + selectionGhost.originY}px)`;
    cv.style.display = "block";
  }, [selectionGhost, selection, renderMeta]);

  // Region pick for the Generate panel — drag/click two corners on the
  // canvas while the panel stays docked. STICKY: stays armed and re-aims
  // on every drag (the panel calls this once when a box generator is
  // selected; it disarms via cancelRegionPick on generator change).
  const pickRegionForPanel = useCallback(
    (cb: (c1: { x: number; y: number }, c2: { x: number; y: number }) => void) => {
      setPickingPoint(null);   // mutually exclusive with focal-point pick
      setPickingRect({
        stage: 0,
        sticky: true,
        onComplete: (c1, c2) => cb(c1, c2),
        onCancel: () => { /* ESC — keep the previous region */ },
      });
    }, [],
  );
  // Single-click focal-point pick for the density-falloff generator —
  // sticky; every click sets the focal point.
  const pickPointForPanel = useCallback(
    (cb: (t: { x: number; y: number }) => void) => {
      setPickingRect(null);    // mutually exclusive with box pick
      setPickingPoint({ onPick: cb });
    }, [],
  );
  // Disarm whatever region/focal pick is active (panel switched
  // generators, deselected, or unmounted).
  const cancelRegionPick = useCallback(() => {
    setPickingRect(null);
    setPickingPoint(null);
  }, []);

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
  const dragRef = useRef<{ startX: number; startY: number; panX: number; panY: number; moved: boolean } | null>(null);
  // Set when a plain-left drag actually panned - the click that follows
  // mouseup must not pin the inspector.
  const panConsumedClickRef = useRef(false);

  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  // On-screen size of the canvas VIEWPORT container (the overflow-hidden
  // box that crops the pan/zoomed render). The minimap navigator needs
  // it to draw the "what's visible" rectangle. A ref-callback (re)wires a
  // ResizeObserver whenever the node attaches — the dock can tear the
  // canvas panel down + rebuild it, swapping the underlying DOM node, so
  // a plain useRef + mount-effect would go stale.
  const [canvasViewportSize, setCanvasViewportSize] = useState({ w: 0, h: 0 });
  const canvasViewportRoRef = useRef<ResizeObserver | null>(null);
  const canvasViewportElRef = useRef<HTMLDivElement | null>(null);
  const setCanvasViewportEl = useCallback((el: HTMLDivElement | null) => {
    canvasViewportRoRef.current?.disconnect();
    canvasViewportRoRef.current = null;
    canvasViewportElRef.current = el;
    if (!el) return;
    const measure = () => {
      const r = el.getBoundingClientRect();
      setCanvasViewportSize((prev) =>
        prev.w === r.width && prev.h === r.height ? prev : { w: r.width, h: r.height }
      );
    };
    measure();
    const ro = new ResizeObserver(measure);
    ro.observe(el);
    canvasViewportRoRef.current = ro;
  }, []);

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
    zoom, pan, renderMeta, selectedRoom,
    ready: false,
  });
  demoRef.current = {
    zoom, pan, renderMeta, selectedRoom,
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
        // Bigmap "detail window" (~L2578): once cropped, renderMeta is the
        // CROPPED region's meta, but the frame div this pan centers on
        // stays sized to the full map (fullMetaRef) — use that instead so
        // the target lands in frame-relative space, not the crop's own
        // (confirmed live: a second panTo while cropped landed thousands
        // of px off). Room view keeps renderMeta — fullMetaRef only ever
        // tracks the last full-sector (non-room) meta.
        const rm = demoRef.current.renderMeta;
        const meta = demoRef.current.selectedRoom === null ? (fullMetaRef.current ?? rm) : rm;
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
        const toZ = Math.max(minZoomRef.current, Math.min(8, z));
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
      // renderer. Always WebGL2 — the Canvas2D
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

  // Read-only tactical appendix (items / entry points / exit grids / soldiers / lights).
  // Fetched once per session; the overlay is purely visual.
  useEffect(() => {
    setAppendix(null);
    setSpriteCache(new Map());
    setItemCache(new Map());
    if (!session) return;
    let cancelled = false;
    getSessionAppendix(session.session_id)
      .then((a) => { if (!cancelled) setAppendix(a); })
      .catch(() => { if (!cancelled) setAppendix(null); });
    return () => { cancelled = true; };
  }, [session?.session_id]);

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
        // Manifest first (tiny) so its fingerprint can cache-key the atlas URL.
        // Without it the browser's 24h HTTP cache serves the pre-art-change full
        // atlas over the freshly-baked one (e.g. dirt road after a roadtile swap).
        const manifest = await getAtlasManifest(xmlPath, session.tileset);
        if (cancelled) return;
        const url = await fetchAtlasBlobUrl(xmlPath, session.tileset, undefined,
                                            { cacheKey: manifest.fingerprint });
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

  // ─── Validation finding highlight (R4) ─────────────────────────────
  // Set when a finding row in the Validation panel is clicked: the
  // affected tiles tint on the canvas (alongside any room highlight) and
  // the view jumps to the first one. Cleared by clicking a different
  // finding (replaces) or starting a paint stroke.
  const [findingHighlight, setFindingHighlight] = useState<Set<string>>(() => new Set());
  const showFindingTiles = useCallback((tiles: number[]) => {
    if (!tiles.length || !renderer || !renderMeta) return;
    const cols = renderer.getParsed().cols;
    setFindingHighlight(new Set(
      tiles.map((g) => `${g % cols},${Math.floor(g / cols)}`),
    ));
    // Center the first affected tile (same math as the demo panTo).
    const g0 = tiles[0]!;
    const p = tileToCanvasPixel(g0 % cols, Math.floor(g0 / cols), renderMeta);
    const cx = p.x + renderMeta.tileW / 2, cy = p.y + renderMeta.tileH / 2;
    setPan({ x: (renderMeta.canvasW / 2 - cx) * zoom, y: (renderMeta.canvasH / 2 - cy) * zoom });
  }, [renderer, renderMeta, zoom]);

  // ─── Highlight tiles for the selected room (canvas-side green tint) ─
  // Matches the Python iso_renderer's `--room` behavior, unioned with any
  // clicked-finding tiles. The SVG overlay still draws hover + pinned
  // highlights on top.
  const highlightTiles = useMemo(() => {
    const tiles = new Set<string>(findingHighlight);
    if (selectedRoom !== null && renderer) {
      const parsed = renderer.getParsed();
      for (let g = 0; g < parsed.rooms.length; g++) {
        if (parsed.rooms[g] === selectedRoom) {
          tiles.add(`${g % parsed.cols},${Math.floor(g / parsed.cols)}`);
        }
      }
    }
    return tiles;
  }, [selectedRoom, renderer, renderEpoch, findingHighlight]);

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
  // ─── Detail window (bigmap sharpness) ──────────────────────────────
  // Chrome clamps the GL drawing buffer to ~2^25 px, so a whole 360×360
  // map rasterizes at ~0.55 scale and looks mushy/blocky when zoomed
  // in. When the user zooms in on such a map, re-render ONLY the
  // visible tile window (plus margin) — a region small enough to
  // rasterize 1:1 — and absolutely position that region canvas where
  // the full map would have put it, so the pan/zoom transform and all
  // meta-driven overlay/hit-test math keep working unchanged.
  const [detailBbox, setDetailBbox] = useState<[number, number, number, number] | null>(null);
  // Full-map meta captured whenever we render un-regioned; the frame
  // div keeps THESE dims so entering/leaving detail mode never moves
  // the world under the transform's -50% centering.
  const fullMetaRef = useRef<RenderMeta | null>(null);

  // A room is a standalone canvas, not a window into the full-map frame.
  // Discard the previous detail window and camera when changing scope.
  useEffect(() => {
    setDetailBbox(null);
    setZoom(1);
    setPan({ x: 0, y: 0 });
  }, [selectedRoom]);

  useEffect(() => {
    if (!renderer) return;
    if (selectedRoom === null && !detailBbox) {
      fullMetaRef.current = renderer.computeMeta({});
    }
    const meta = renderer.computeMeta({
      roomId: selectedRoom,
      ring: 5,
      bbox: selectedRoom === null ? detailBbox : null,
    });
    setRenderMeta(meta);
  }, [renderer, selectedRoom, detailBbox]);

  // Watch zoom/pan and pick the detail window (debounced). Only for
  // full-sector views of maps big enough to hit the buffer clamp.
  useEffect(() => {
    if (!renderer || !info.data || selectedRoom !== null || !renderMeta) return;
    const fm = fullMetaRef.current;
    if (!fm || fm.canvasW * fm.canvasH <= 32 * 1024 * 1024) return;
    const cols = info.data.cols, rows = info.data.rows;
    const timer = window.setTimeout(() => {
      if (zoom < 0.75) {
        setDetailBbox(null);
        return;
      }
      const el = canvasRef.current;
      const vp = canvasViewportElRef.current;
      if (!el || !vp) return;
      const rect = el.getBoundingClientRect();
      if (rect.width <= 0) return;
      const scale = el.clientWidth / rect.width;
      const vr = vp.getBoundingClientRect();
      const meta = renderMeta;
      const hw = meta.tileW / 2, hh = meta.tileH / 2;
      let tx0 = Infinity, ty0 = Infinity, tx1 = -Infinity, ty1 = -Infinity;
      for (const [cx, cy] of [[vr.left, vr.top], [vr.right, vr.top],
                              [vr.left, vr.bottom], [vr.right, vr.bottom]] as const) {
        const px = (cx - rect.left) * scale;
        const py = (cy - rect.top) * scale;
        const A = (px + meta.ixMin) / hw;
        const B = (py + meta.iyMin) / hh;
        const tx = (A + B) / 2 - 1, ty = (B - A) / 2;
        tx0 = Math.min(tx0, tx); tx1 = Math.max(tx1, tx);
        ty0 = Math.min(ty0, ty); ty1 = Math.max(ty1, ty);
      }
      const M = 10;                                  // margin tiles
      // Snap to an 8-tile grid so small pans reuse the rendered window.
      const snap = (v: number, up: boolean) =>
        up ? Math.ceil(v / 8) * 8 : Math.floor(v / 8) * 8;
      const bx0 = Math.max(0, snap(tx0 - M, false));
      const by0 = Math.max(0, snap(ty0 - M, false));
      const bx1 = Math.min(cols - 1, snap(tx1 + M, true));
      const by1 = Math.min(rows - 1, snap(ty1 + M, true));
      if (bx1 <= bx0 || by1 <= by0) return;
      if (bx0 === 0 && by0 === 0 && bx1 === cols - 1 && by1 === rows - 1) {
        setDetailBbox(null);
        return;
      }
      setDetailBbox((prev) =>
        prev && prev[0] === bx0 && prev[1] === by0 && prev[2] === bx1 && prev[3] === by1
          ? prev : [bx0, by0, bx1, by1]);
    }, 280);
    return () => window.clearTimeout(timer);
    // renderMeta in deps: after a detail render lands, the recomputed
    // window (now in region coordinates) must still resolve correctly.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [renderer, info.data, selectedRoom, zoom, pan, canvasViewportSize, renderMeta]);

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
      bbox: selectedRoom === null ? detailBbox : null,
      skipLayers: hiddenLayers,
      highlightTiles,
    });
    if (!firstPaintDone) {
      setFirstPaintDone(true);
      setRendererLoading(false);
      setLoadPhase(null);
    }
  }, [renderer, renderMeta, selectedRoom, detailBbox, hiddenLayers, renderEpoch, highlightTiles, firstPaintDone]);

  // Repaint when full-screen flips — the <canvas> remounts (dock slot
  // ↔ render-only view), so bump renderEpoch to redraw into the new
  // element.
  useEffect(() => {
    setRenderEpoch((e) => e + 1);
  }, [focusMode]);

  // Convert a mouse event on the canvas to LOGICAL canvas pixel coords
  // (renderMeta space). Scale by clientWidth (CSS size, pinned to
  // meta.canvasW), NOT canvas.width: on bigmaps IsoRendererGL renders
  // into a downscaled backing store to stay under Chrome's WebGL
  // drawing-buffer clamp, so the attribute size is no longer the
  // logical size. clientWidth ÷ bounding rect cancels the zoom
  // transform either way.
  const eventToCanvasPixel = useCallback((e: { clientX: number; clientY: number }) => {
    if (!canvasRef.current) return null;
    const rect = canvasRef.current.getBoundingClientRect();
    const scaleX = canvasRef.current.clientWidth / rect.width;
    const scaleY = canvasRef.current.clientHeight / rect.height;
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
    if (spec.op === "set_room") {
      setMaxPaintedRoomId((m) => Math.max(m, spec.roomId));
    }
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
  /** Erase the non-ground layers at the brush-radius tiles around `tile`
   * (R4 "wipe but keep the floor"): clears objs/shadows/structs/roofs/
   * onroofs via set_entries []. Runs inside the pencil's open stroke
   * (begin/endStroke handled by the canvas handlers), dedup'd per tile via
   * strokeRef. Mirrors paintHeight's snapshot → local → applyEdits path. */
  async function eraseAt(tile: { x: number; y: number }) {
    if (!session || !renderer || session.read_only) return;
    const cols = info.data?.cols ?? renderer.getParsed().cols;
    const rows = info.data?.rows ?? renderer.getParsed().rows;
    const r = brushRadius - 1;
    const edits: SessionEdit[] = [];
    for (let dy = -r; dy <= r; dy++) {
      for (let dx = -r; dx <= r; dx++) {
        if (Math.abs(dx) + Math.abs(dy) > r) continue;
        const x = tile.x + dx, y = tile.y + dy;
        if (x < 0 || y < 0 || x >= cols || y >= rows) continue;
        const key = `erase:${x},${y}`;
        if (!strokeRef.current) strokeRef.current = new Set();
        if (strokeRef.current.has(key)) continue;
        strokeRef.current.add(key);
        for (const l of ERASE_LAYERS) {
          renderer.recordSnapshot(x, y, l);
          renderer.applyLocalEdit({ x, y, op: "set_entries", layer: l, entries: [] });
          edits.push({ x, y, op: "set_entries", layer: l, entries: [] });
        }
      }
    }
    if (edits.length === 0) return;
    setRenderEpoch((e) => e + 1);
    setEditsInFlight((n) => n + 1);
    try {
      const res = await applyEdits(session.session_id, edits);
      setSession(res.session);
    } catch (e) {
      log?.append({
        severity: "error",
        message: "Erase sync failed — backend rejected an edit",
        detail: e instanceof Error ? e.message : String(e),
      });
    } finally {
      setEditsInFlight((n) => Math.max(0, n - 1));
    }
  }

  /** Delete hotkey (inspect tool): remove the sprite-picked entry at the
   * pinned tile — or, for a plain tile pin, the topmost visible entry
   * there. No confirm modal (the hotkey exists for speed); fully
   * undoable via the stroke machinery like every other edit. */
  async function deletePinnedEntry() {
    if (!session || !renderer || session.read_only || !pinned) return;
    const parsed = renderer.getParsed();
    const gn = pinned.y * parsed.cols + pinned.x;
    let layer: LayerName | null = null;
    let idx = -1;
    const pick = pinnedPickRef.current;
    if (pick) {
      const entries = parsed[pick.layer][gn] ?? [];
      for (let i = entries.length - 1; i >= 0; i--) {
        const e = entries[i];
        if (e && e[0] === pick.slot && e[1] === pick.sub) {
          layer = pick.layer; idx = i; break;
        }
      }
    }
    if (layer === null) {
      for (const l of ["onroofs", "roofs", "structs", "objs"] as const) {
        const entries = parsed[l][gn] ?? [];
        if (entries.length > 0) { layer = l; idx = entries.length - 1; break; }
      }
    }
    if (layer === null || idx < 0) return;
    renderer.beginStroke(`Delete ${layer}[${idx}] (${pinned.x},${pinned.y})`);
    renderer.recordSnapshot(pinned.x, pinned.y, layer);
    renderer.applyLocalEdit({
      x: pinned.x, y: pinned.y, op: "remove", layer, entryIndex: idx,
    });
    renderer.endStroke();
    bumpHistory();
    setRenderEpoch((e) => e + 1);
    setSpriteHit(null);
    pinnedPickRef.current = null;
    setEditsInFlight((n) => n + 1);
    try {
      const res = await applyEdits(session.session_id,
        [{ x: pinned.x, y: pinned.y, op: "remove", layer, entry_index: idx }]);
      setSession(res.session);
    } catch (err) {
      // Backend rejected — revert the optimistic local edit and discard
      // the stroke so Ctrl+Y can't replay it (inspector applyEdit's
      // pattern).
      const entry = renderer.discardLastUndo();
      if (entry) {
        for (const s of entry.snapshots) {
          renderer.applyLocalEdit({
            x: s.x, y: s.y, op: "set_entries", layer: s.layer, entries: s.entries,
          });
        }
      }
      setRenderEpoch((e) => e + 1);
      log?.append({
        severity: "error",
        message: "Delete rejected by the backend",
        detail: err instanceof Error ? err.message : String(err),
      });
    } finally {
      setEditsInFlight((n) => Math.max(0, n - 1));
    }
  }

  /** Paint the current room id onto the brush-radius tiles around `tile`
   * (the Room payload, under the pencil). Runs inside the pencil's open stroke;
   * dedup'd per tile via strokeRef. */
  async function paintRoomAt(tile: { x: number; y: number }) {
    if (!session || !renderer || session.read_only) return;
    const cols = info.data?.cols ?? renderer.getParsed().cols;
    const rows = info.data?.rows ?? renderer.getParsed().rows;
    const r = brushRadius - 1;
    const edits: SessionEdit[] = [];
    for (let dy = -r; dy <= r; dy++) {
      for (let dx = -r; dx <= r; dx++) {
        if (Math.abs(dx) + Math.abs(dy) > r) continue;
        const x = tile.x + dx, y = tile.y + dy;
        if (x < 0 || y < 0 || x >= cols || y >= rows) continue;
        const key = `room:${x},${y}`;
        if (!strokeRef.current) strokeRef.current = new Set();
        if (strokeRef.current.has(key)) continue;
        strokeRef.current.add(key);
        renderer.recordRoomSnapshot(x, y);
        renderer.applyLocalEdit({ x, y, op: "set_room", roomId });
        edits.push({ x, y, op: "set_room", room_id: roomId });
        setMaxPaintedRoomId((m) => Math.max(m, roomId));
      }
    }
    if (edits.length === 0) return;
    setRenderEpoch((e) => e + 1);
    setEditsInFlight((n) => n + 1);
    try {
      const res = await applyEdits(session.session_id, edits);
      setSession(res.session);
    } catch (e) {
      log?.append({ severity: "error", message: "Room sync failed — backend rejected an edit", detail: e instanceof Error ? e.message : String(e) });
    } finally {
      setEditsInFlight((n) => Math.max(0, n - 1));
    }
  }

  /** Stroke-label verb for the Height payload. */
  function heightVerb(): string {
    return heightMode === "set"
      ? `Set height ${heightValue}`
      : heightMode === "raise"
        ? `Raise height +${heightValue}`
        : `Lower height -${heightValue}`;
  }

  /** Apply the active payload to a list of tiles as ONE undoable stroke —
   * shared by the Shape commit + Flood fill. Tiles/Room go through
   * applyTileEdits (StrokeSpec ops); Erase/Height build the set_entries /
   * set_height edits directly (mirrors doDeleteSelection's pattern). The
   * `region` word leads the stroke label for tile placement ("Fill",
   * "Line", …). */
  function applyPayloadBatch(tiles: Tile[], region: string) {
    if (!session || session.read_only || !renderer || tiles.length === 0) return;
    if (payload === "tiles") {
      if (!activeBrush) return;
      const layer = (paintLayer ?? activeBrush.layer) as LayerName;
      const name = activeBrush.sti_filename.replace(/\.sti$/i, "");
      renderer.beginStroke(`${region} ${name} (${tiles.length} tiles)`);
      void applyTileEdits(tiles, { op: "place", layer, slot: activeBrush.slot, sub: activeBrush.sub });
      renderer.endStroke();
      bumpHistory();
      return;
    }
    if (payload === "room") {
      renderer.beginStroke(
        roomId === 0
          ? `Clear room (${tiles.length} tiles)`
          : `Mark room ${roomId} (${tiles.length} tiles)`,
      );
      void applyTileEdits(tiles, { op: "set_room", roomId });
      renderer.endStroke();
      bumpHistory();
      return;
    }
    // erase / height — build the edits + apply as one transactional batch.
    const parsed = renderer.getParsed();
    const cols = parsed.cols;
    const edits: SessionEdit[] = [];
    renderer.beginStroke(
      payload === "erase"
        ? `Erase (${tiles.length} tiles)`
        : `${heightVerb()} (${tiles.length} tiles)`,
    );
    for (const t of tiles) {
      if (payload === "erase") {
        for (const l of ERASE_LAYERS) {
          renderer.recordSnapshot(t.x, t.y, l);
          renderer.applyLocalEdit({ x: t.x, y: t.y, op: "set_entries", layer: l, entries: [] });
          edits.push({ x: t.x, y: t.y, op: "set_entries", layer: l, entries: [] });
        }
      } else {
        const cur = parsed.heights[t.y * cols + t.x] ?? 0;
        const next = heightMode === "set"
          ? Math.max(0, Math.min(255, heightValue))
          : heightMode === "raise"
            ? Math.max(0, Math.min(255, cur + heightValue))
            : Math.max(0, Math.min(255, cur - heightValue));
        renderer.recordHeightSnapshot(t.x, t.y);
        renderer.applyLocalEdit({ x: t.x, y: t.y, op: "set_height", height: next });
        edits.push({ x: t.x, y: t.y, op: "set_height", height: next });
      }
    }
    renderer.endStroke();
    bumpHistory();
    setRenderEpoch((e) => e + 1);
    setEditsInFlight((n) => n + 1);
    applyEdits(session.session_id, edits)
      .then((res) => setSession(res.session))
      .catch((e) => log?.append({ severity: "error", message: "Edit sync failed — backend rejected the batch.", detail: e instanceof Error ? e.message : String(e) }))
      .finally(() => setEditsInFlight((n) => Math.max(0, n - 1)));
  }

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
    // feedback). The next Ctrl+Z undoes map edits normally.
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
    // Review finding #3: an undo can remove/replace the sprites the
    // mode-less selection points at (stale refs → moveEdits/cycle would
    // place a phantom). Cheaper and safer to just clear than to diff.
    setSelection([]);
    setSpriteHit(null);
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
      // The sidecar did NOT apply the revert — and Save serializes the
      // SIDECAR's session, so silently keeping the local revert would
      // make the canvas lie about what a save writes. Roll the local
      // mirror forward again (popRedo returns the pre-undo capture,
      // and restores the stroke to the undo stack) and tell the user.
      rollbackHistorySync(renderer.popRedo(), "Undo", e);
    } finally {
      setEditsInFlight((n) => Math.max(0, n - 1));
    }
  }

  /** Recovery path for a failed undo/redo backend sync: re-apply the
   * mirror entry locally so the canvas matches the sidecar session
   * again (local-first apply had already diverged it), then surface
   * the failure in the log — a console.warn here once let users save
   * a different map than the one on their screen. */
  function rollbackHistorySync(
    mirror: UndoEntry | null, what: string, e: unknown,
  ) {
    if (renderer && mirror) {
      for (const s of mirror.snapshots) {
        renderer.applyLocalEdit({
          x: s.x, y: s.y, op: "set_entries",
          layer: s.layer, entries: s.entries,
        });
      }
      for (const r of mirror.roomSnapshots) {
        renderer.applyLocalEdit({
          x: r.x, y: r.y, op: "set_room", roomId: r.roomId,
        });
      }
      for (const h of mirror.heightSnapshots) {
        renderer.applyLocalEdit({
          x: h.x, y: h.y, op: "set_height", height: h.height,
        });
      }
      setRenderEpoch((n) => n + 1);
    }
    log?.append({
      severity: "error",
      message: `${what} couldn't reach the backend — the change was `
        + "rolled back locally so the canvas still matches what Save "
        + "will write. Check the sidecar and try again.",
      detail: e instanceof Error ? e.message : String(e),
    });
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
    // Review finding #3 — see undo()'s matching comment.
    setSelection([]);
    setSpriteHit(null);
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
      // Symmetric with undo(): popUndo returns the pre-redo capture
      // and restores the entry to the redo stack.
      rollbackHistorySync(renderer.popUndo(), "Redo", e);
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
    // Focal-point pick (density-falloff): a single click sets the focal
    // point. Sticky — stays armed so each click re-aims. The following
    // click event is swallowed so it can't pin the inspector.
    if (pickingPoint) {
      const tile = hovered ?? pixelToTile(e);
      if (tile) {
        pickSuppressClickRef.current = true;
        pickingPoint.onPick(tile);
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
      // Fence line-drag: a fence-armed
      // single item anchors a line instead of placing immediately — the
      // drag is finished (committed or falls back to a single place) on
      // mouseup. Shift is NOT consulted for STARTING a line — a fence
      // line is never queueable and always commits as its own stroke —
      // so Shift held here just falls through to the normal `run()`
      // below, which is the plain-place/enqueue decision.
      if (fenceArmed && !e.shiftKey) {
        setLineAnchor(tile);
        return;
      }
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
    // Shift+drag a SPRITE = grab-and-move; the drop happens in
    // onCanvasMouseUp. Plain mousedown stays a maybe-pan (wrapper) and a
    // clean click pins/selects (onCanvasClick). Lives on `inspectLike`
    // (inspect tool, or mode-less select with nothing armed) rather than
    // inside the `tool === "inspect"` branch of the chain below, so it
    // fires in the mode-less "nothing armed" state too — but it's a
    // stand-alone pre-check (not folded into the tool switch) so a
    // grab-and-move `return` can't shadow `assertNever(tool)`'s
    // exhaustiveness check on `tool`, and so a shift-drag that misses a
    // sprite still falls through to the select tool's normal marquee.
    if (inspectLike && e.shiftKey && renderer && renderMeta && !session?.read_only) {
      const p = eventToCanvasPixel(e);
      const hit = p ? renderer.pickSpriteAt(p.px, p.py, renderMeta) : null;
      if (hit) {
        e.stopPropagation();        // the wrapper must not arm a pan
        moveRef.current = { hit };
        setMoving(true);
        return;
      }
    }
    if (tool === "inspect") {
      // Click-to-pin handled in onCanvasClick — nothing to do on mousedown.
    } else if (tool === "pencil") {
      // The pencil applies the active PAYLOAD over its brush-radius tiles.
      strokeRef.current = new Set();
      if (payload === "erase") {
        if (session?.read_only) return;
        renderer.beginStroke(`Erase (brush ${brushRadius})`);
        void eraseAt(tile);
      } else if (payload === "height") {
        if (session?.read_only) return;
        renderer.beginStroke(heightVerb());
        void paintHeight(tile);
      } else if (payload === "room") {
        if (session?.read_only) return;
        renderer.beginStroke(roomId === 0
          ? `Clear room (brush ${brushRadius})`
          : `Room ${roomId} (brush ${brushRadius})`);
        void paintRoomAt(tile);
      } else {
        if (!activeBrush) return;
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
      }
    } else if (tool === "shape") {
      // Flood fill is a click action (not a bbox drag) — fill from the
      // clicked seed immediately and bail (no anchor / drag preview).
      // `oneShotShape` overrides `shapeKind` when a command-card verb
      // armed a one-shot (a later phase wires the card; the fallback
      // to `shapeKind` alone is what P1 exercises today).
      if ((oneShotShape ?? shapeKind) === "flood") {
        if (payload === "tiles" && !activeBrush) return;
        doFloodFill(tile);
        return;
      }
      // The Tiles payload needs an armed brush; Erase / Height / Room don't.
      // Anchor the drag; the commit happens on mouseup. A non-null
      // shapeAnchor also drives the live preview overlay.
      if (payload === "tiles" && !activeBrush) return;
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
    } else {
      assertNever(tool);
    }
  }

  function onCanvasClick(e: React.MouseEvent<HTMLCanvasElement>) {
    // A plain-left drag that PANNED must not pin on release.
    if (panConsumedClickRef.current) {
      panConsumedClickRef.current = false;
      return;
    }
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
        if (pickingRect.sticky) setPickingRect({ ...pickingRect, stage: 0, corner1: undefined });
        else setPickingRect(null);
        cb(c1, tile);
      }
      return;
    }
    // Building placement: the mousedown already stamped — the click
    // that follows must not pin the inspector.
    if (placingBuilding) return;
    // Ghost preview live — block tool actions (see onCanvasMouseDown).
    if (ghostActive) return;
    // Inspect (or mode-less select, nothing armed). Pencil + shape act
    // via mousedown/move/up above; their click event fires AFTER mouseup
    // and must not pin a tile.
    if (!inspectLike) return;
    const tile = pixelToTile(e, { logToConsole: true });
    if (!tile) return;
    // Sprite-aware pick: clicking a big sprite (cooling tower, wreck)
    // pins the tile that OWNS it, not whatever ground tile happens to
    // sit under the cursor. Ctrl+click bypasses for a raw tile pick.
    if (!e.ctrlKey && renderer && renderMeta) {
      const p = eventToCanvasPixel(e);
      const hit = p ? renderer.pickSpriteAt(p.px, p.py, renderMeta) : null;
      // Mode-less sprite selection, with nothing armed: a
      // plain click selects just this sprite; Shift+click toggles it
      // in/out of a multi-sprite selection; a click on empty ground
      // clears the selection (Shift+click on empty ground leaves it).
      if (modeless) {
        if (hit) {
          const ref: SpriteRef = { x: hit.x, y: hit.y, layer: hit.layer, slot: hit.slot, sub: hit.sub };
          setSelection((prev) => e.shiftKey
            ? (prev.some((r) => refKey(r) === refKey(ref)) ? prev.filter((r) => refKey(r) !== refKey(ref)) : [...prev, ref])
            : [ref]);
        } else if (!e.shiftKey) {
          setSelection([]);
        }
      }
      if (hit) {
        setPinned({ x: hit.x, y: hit.y });
        // Remember WHICH entry was picked so the Delete hotkey removes
        // exactly the sprite the user clicked, not a random co-tenant.
        pinnedPickRef.current = { layer: hit.layer, slot: hit.slot, sub: hit.sub };
        return;
      }
    }
    pinnedPickRef.current = null;
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
    // Fence line-drag: the line itself is a derived memo off
    // (lineAnchor, hovered) — set just above — so mousemove needs no
    // extra work to grow/shrink the preview. Just guard against a stray
    // anchor if the button was released somewhere the canvas never saw
    // a mouseup for (the wrapper's onMouseUpDrag also clears it, but a
    // re-entering drag with the button already up should not resume one).
    if (lineAnchor && e.buttons !== 1) setLineAnchor(null);
    // Sprite-aware hover (inspect, or mode-less select with nothing
    // armed): when the hovered TILE changes, probe which drawn sprite
    // the cursor is on and outline it + its anchor tile. Throttled to
    // tile changes so the ~ms pick cost never runs per-mousemove. Skipped
    // entirely while a ghost is armed (`placingBuilding`) — the outline is
    // hidden then anyway (see the `spriteHit` prop on IsoOverlay), so the
    // probe would just be paying its cost for nothing.
    if (inspectLike && !placingBuilding && renderer && renderMeta && tile) {
      const prev = spriteProbeTileRef.current;
      if (!prev || prev.x !== tile.x || prev.y !== tile.y) {
        spriteProbeTileRef.current = tile;
        const p = eventToCanvasPixel(e);
        const hit = p ? renderer.pickSpriteAt(p.px, p.py, renderMeta) : null;
        setSpriteHit((old) =>
          old && hit && old.x === hit.x && old.y === hit.y
            && old.slot === hit.slot && old.sub === hit.sub ? old : hit,
        );
      }
    } else if (spriteHit && !inspectLike) {
      setSpriteHit(null);
      spriteProbeTileRef.current = null;
    }
    // Drag-paint: if pencil + brush + left button held + stroke active.
    // Inherit the Shift state from the live mousemove so the user can
    // toggle stamp/manual mid-drag if they want to (rare but coherent).
    if (
      tool === "pencil" && tile
      && e.buttons === 1 && strokeRef.current !== null
    ) {
      if (payload === "erase") void eraseAt(tile);
      else if (payload === "height") void paintHeight(tile);
      else if (payload === "room") void paintRoomAt(tile);
      else if (activeBrush) paintBrush(tile, e.shiftKey);
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
  }

  /** Grab-and-move: relocate ONE picked entry (layer/slot/sub) from its
   * tile to `to` as a single undo stroke (remove + place). An explicit
   * same-tile shadow (slot+1, same sub — the Estoni 75/77/79 pairs, which
   * the engine does not auto-shadow) rides along. Multi-tile JSD
   * footprints follow their single anchor entry. */
  async function moveEntry(hit: SpriteHit, to: { x: number; y: number }) {
    if (!session || !renderer || session.read_only) return;
    const parsed = renderer.getParsed();
    const from = { x: hit.x, y: hit.y };
    const gn = from.y * parsed.cols + from.x;
    const entries = parsed[hit.layer][gn] ?? [];
    let idx = -1;
    for (let i = entries.length - 1; i >= 0; i--) {
      const en = entries[i];
      if (en && en[0] === hit.slot && en[1] === hit.sub) { idx = i; break; }
    }
    if (idx < 0) return;
    let shadowIdx = -1;
    if (hit.layer === "structs") {
      const sh = parsed.shadows[gn] ?? [];
      for (let i = sh.length - 1; i >= 0; i--) {
        const en = sh[i];
        if (en && en[0] === hit.slot + 1 && en[1] === hit.sub) { shadowIdx = i; break; }
      }
    }
    renderer.beginStroke(`Move s${hit.slot}/${hit.sub} (${from.x},${from.y})→(${to.x},${to.y})`);
    renderer.recordSnapshot(from.x, from.y, hit.layer);
    renderer.recordSnapshot(to.x, to.y, hit.layer);
    if (shadowIdx >= 0) {
      renderer.recordSnapshot(from.x, from.y, "shadows");
      renderer.recordSnapshot(to.x, to.y, "shadows");
    }
    renderer.applyLocalEdit({ x: from.x, y: from.y, op: "remove", layer: hit.layer, entryIndex: idx });
    renderer.applyLocalEdit({ x: to.x, y: to.y, op: "place", layer: hit.layer, slot: hit.slot, sub: hit.sub });
    const edits: SessionEdit[] = [
      { x: from.x, y: from.y, op: "remove", layer: hit.layer, entry_index: idx },
      { x: to.x, y: to.y, op: "place", layer: hit.layer, slot: hit.slot, sub: hit.sub },
    ];
    if (shadowIdx >= 0) {
      renderer.applyLocalEdit({ x: from.x, y: from.y, op: "remove", layer: "shadows", entryIndex: shadowIdx });
      renderer.applyLocalEdit({ x: to.x, y: to.y, op: "place", layer: "shadows", slot: hit.slot + 1, sub: hit.sub });
      edits.push(
        { x: from.x, y: from.y, op: "remove", layer: "shadows", entry_index: shadowIdx },
        { x: to.x, y: to.y, op: "place", layer: "shadows", slot: hit.slot + 1, sub: hit.sub },
      );
    }
    renderer.endStroke();
    bumpHistory();
    setRenderEpoch((e) => e + 1);
    setSpriteHit(null);
    setPinned(to);
    pinnedPickRef.current = { layer: hit.layer, slot: hit.slot, sub: hit.sub };
    setEditsInFlight((n) => n + 1);
    try {
      const res = await applyEdits(session.session_id, edits);
      setSession(res.session);
    } catch (err) {
      // Backend rejected — revert the optimistic local edits and discard
      // the stroke so Ctrl+Y can't replay it (deletePinnedEntry's pattern).
      const entry = renderer.discardLastUndo();
      if (entry) {
        for (const sn of entry.snapshots) {
          renderer.applyLocalEdit({
            x: sn.x, y: sn.y, op: "set_entries", layer: sn.layer, entries: sn.entries,
          });
        }
      }
      setRenderEpoch((e) => e + 1);
      log?.append({
        severity: "error",
        message: "Move rejected by the backend",
        detail: err instanceof Error ? err.message : String(err),
      });
    } finally {
      setEditsInFlight((n) => Math.max(0, n - 1));
    }
  }

  /** Mirror-then-persist helper shared by place/delete/nudge/cycle: on
   * backend rejection pop the stroke and restore its snapshots
   * (moveEntry's pattern above). */
  async function sendEdits(edits: SessionEdit[], failMsg: string): Promise<boolean> {
    if (!session || !renderer) return false;
    setEditsInFlight((n) => n + 1);
    try {
      const res = await applyEdits(session.session_id, edits);
      setSession(res.session);
      return true;
    } catch (err) {
      const entry = renderer.discardLastUndo();
      if (entry) for (const sn of entry.snapshots) renderer.applyLocalEdit({ x: sn.x, y: sn.y, op: "set_entries", layer: sn.layer, entries: sn.entries });
      setRenderEpoch((e) => e + 1);
      log?.append({ severity: "error", message: failMsg, detail: err instanceof Error ? err.message : String(err) });
      return false;
    } finally {
      setEditsInFlight((n) => Math.max(0, n - 1));
    }
  }

  /** Shared snapshot/apply/send dance for place/delete/nudge/cycle — ONE
   * undo stroke, then persist (review findings #4/#11 — this replaces
   * four near-identical copies). `nextSelection` replaces the sprite
   * selection right after the local edits land (before the backend
   * round-trip resolves); pass `null` to leave selection untouched
   * (callers that manage it themselves, e.g. clearing pinned alongside).
   * On backend rejection, `sendEdits` already rolls the RENDERER back;
   * this also rolls `selection` back to what it was before the call. */
  async function applyStroke(
    label: string,
    edits: SessionEdit[],
    touched: { x: number; y: number; layer: LayerName }[],
    nextSelection: SpriteRef[] | null,
    failMsg: string,
  ): Promise<boolean> {
    if (!session || session.read_only || !renderer) return false;
    const prevSelection = selectionRef.current;
    renderer.beginStroke(label);
    for (const t of touched) renderer.recordSnapshot(t.x, t.y, t.layer);
    for (const ed of edits) {
      renderer.applyLocalEdit(ed.op === "remove"
        ? { x: ed.x, y: ed.y, op: "remove", layer: ed.layer, entryIndex: ed.entry_index }
        : { x: ed.x, y: ed.y, op: "place", layer: ed.layer, slot: ed.slot, sub: ed.sub });
    }
    renderer.endStroke();
    bumpHistory(); setRenderEpoch((e) => e + 1);
    if (nextSelection !== null) setSelection(nextSelection);
    const ok = await sendEdits(edits, failMsg);
    if (!ok) setSelection(prevSelection);
    return ok;
  }

  /** Combined queue-so-far + this-candidate verdicts:
   * a FRESH local batch check across the whole queue — "re-checked
   * against the live state, and `localCheck`'s own batch rule
   * makes an earlier queued ghost count as occupied for a later one —
   * unioned with the CURRENT group's already-computed local+oracle merge
   * (`verdictsRef`; empty for a hover-less hotkey placement, the same
   * edge case the old single-ghost `placeGroupAt` guarded). A plain union
   * is exactly right for a `worstOf(...) === "blocking"` read: a local
   * BLOCKING always wins over an oracle verdict (`mergeVerdicts`'s own
   * rule), so nothing here can downgrade a real block either way. */
  function queueVerdicts(fullQueue: QueuedGhost[]): TileVerdict[] {
    if (!renderer) return [];
    const full = localCheck(renderer.getParsed(), placementTables, occupancy,
      fullQueue.flatMap((q) => groupRefsAt(q.group, q.anchor)), (s) => renderer.getFootprint(s));
    return verdictsRef.current.length > 0 ? [...full, ...verdictsRef.current] : full;
  }

  /** Shift+click while a ghost is armed: stack `group`
   * at `anchor` into the placement queue instead of placing it — refused
   * (logged) when the merged queue-so-far verdict is blocking. The ghost
   * stays armed either way; `armGroup`'s `run` never disarms on this path. */
  function enqueueGroup(anchor: Tile, group: SpriteGroup): void {
    if (!renderer) return;
    const fullQueue: QueuedGhost[] = [...queueRef.current, { anchor, group }];
    const v = queueVerdicts(fullQueue);
    if (worstOf(v) === "blocking") {
      const why = v.find((t) => t.tier === "blocking");
      log?.append({ severity: "warn", message: `Can't queue here — ${why?.detail ?? why?.test ?? "blocked"}.` });
      return;
    }
    const render = renderer.renderRegionToCanvas(groupToRegionTiles(group, (s) => renderer.getFootprint(s)), 0.5);
    setQueue((q) => [...q, { anchor, group, render }]);
  }

  /** Commit the WHOLE placement queue plus `group` at `anchor` as ONE undo
   * stroke — a plain click commits the queue plus the current placement —
   * `queueCommitEdits` degenerates to a single-entry commit when the
   * queue is empty, so this also replaces the old single-group
   * `placeGroupAt` for that case. Returns false (and logs the reason)
   * when the combined verdict is blocking; clears the queue on success. */
  async function commitQueueAndPlace(anchor: Tile, group: SpriteGroup): Promise<boolean> {
    if (!session || session.read_only || !renderer) return false;
    const fullQueue: QueuedGhost[] = [...queueRef.current, { anchor, group }];
    const v = queueVerdicts(fullQueue);
    if (worstOf(v) === "blocking") {
      const why = v.find((t) => t.tier === "blocking");
      log?.append({ severity: "warn", message: `Can't place here — ${why?.detail ?? why?.test ?? "blocked"}.` });
      return false;
    }
    const parsed = renderer.getParsed();
    const { edits, placed, dropped } = queueCommitEdits(fullQueue, parsed.cols, parsed.rows);
    if (edits.length === 0) { log?.append({ severity: "warn", message: "Nothing placed — off the map." }); return false; }
    if (dropped) log?.append({ severity: "warn", message: `${dropped} sprite(s) fell off the map and were skipped.` });
    const label = queueRef.current.length > 0
      ? `Place ${queueRef.current.length} queued + 1`
      : `Place ${group.items.length === 1 ? `s${group.items[0]!.slot}.${group.items[0]!.sub}` : `${group.items.length} sprites`} @ (${anchor.x},${anchor.y})`;
    const touched = edits.map((ed) => ({ x: ed.x, y: ed.y, layer: ed.layer! }));
    const ok = await applyStroke(label, edits, touched, placed, "Place rejected by the backend");
    if (ok) setQueue([]);
    return ok;
  }
  // Stale-closure guard: `armGroup`'s `run` closure is created once per
  // arm and must call the LATEST `enqueueGroup`/`commitQueueAndPlace`
  // (which themselves read `queueRef`/`verdictsRef`/`session`/`renderer`
  // fresh), not whichever versions existed at arm time.
  const enqueueGroupRef = useRef(enqueueGroup);
  enqueueGroupRef.current = enqueueGroup;
  const commitQueueAndPlaceRef = useRef(commitQueueAndPlace);
  commitQueueAndPlaceRef.current = commitQueueAndPlace;

  async function deleteSelection() {
    const sel = selectionRef.current;
    if (!session || session.read_only || !renderer || sel.length === 0) return;
    const parsed = renderer.getParsed();
    const { edits, touched } = deleteEdits(parsed, sel, isShadowOnlySlot);
    if (edits.length === 0) {
      log?.append({ severity: "warn", message: "Selection is stale (already removed) — cleared." });
      setSelection([]);
      return;
    }
    setSpriteHit(null);
    // Review finding #5: also clear the legacy tile pin, or a second
    // Delete right after this one falls through to deletePinnedEntry
    // and removes whatever's topmost on the now-stale pinned tile.
    setPinned(null);
    pinnedPickRef.current = null;
    await applyStroke(`Delete ${sel.length} sprite${sel.length === 1 ? "" : "s"}`, edits, touched, [], "Delete rejected by the backend");
  }

  async function nudgeSelection(dx: number, dy: number) {
    const sel = selectionRef.current;
    if (!session || session.read_only || !renderer || sel.length === 0) return;
    const parsed = renderer.getParsed();
    // Review finding #3: a selection can go stale (e.g. Ctrl+Z removed
    // the selected sprite) — deleteEdits finding nothing means moveEdits
    // would still emit an unconditional `place` at the destination,
    // landing a phantom sprite. Bail and clear instead of moving a
    // ghost of nothing.
    if (deleteEdits(parsed, sel).edits.length === 0) {
      log?.append({ severity: "warn", message: "Selection is stale (already removed) — cleared." });
      setSelection([]);
      return;
    }
    const moved = sel.map((r) => ({ ...r, x: r.x + dx, y: r.y + dy }));
    // occupancyRef (not the `occupancy` closure) — review finding #10b:
    // this function is called from the hotkey dispatcher's closure,
    // which only re-binds on its own deps; the ref is always current.
    const v = localCheck(parsed, placementTables, occupancyRef.current, moved, (s) => renderer.getFootprint(s),
      new Set(sel.map(refKey)));
    if (worstOf(v) === "blocking") {
      const why = v.find((t) => t.tier === "blocking");
      log?.append({ severity: "warn", message: `Can't move there — ${why?.detail ?? why?.test}.` });
      return;
    }
    const { edits, moved: placed, touched, dropped } = moveEdits(parsed, sel, dx, dy, isShadowOnlySlot);
    if (dropped > 0) {
      // moveEdits is all-or-nothing: any destination off-map refuses the
      // whole nudge (edits is already empty) rather than partially moving.
      log?.append({ severity: "warn", message: "Can't nudge — the destination is off the map." });
      return;
    }
    await applyStroke(`Nudge ${sel.length} sprite${sel.length === 1 ? "" : "s"} (${dx},${dy})`, edits, touched, placed, "Nudge rejected by the backend");
  }

  function copySelection(): SpriteGroup | null {
    const sel = selectionRef.current;
    if (!renderer || sel.length === 0) return null;
    const name = datPath.split(/[\\/]/).pop() ?? "sector";
    const grp = sliceGroup(renderer.getParsed(), sel, tileset, name, isShadowOnlySlot);
    if (!grp) { log?.append({ severity: "warn", message: "Nothing copyable in the selection." }); return null; }
    setSpriteClipboard(grp);
    log?.append({ severity: "info", message: `Copied ${grp.items.length} sprite(s) (${grp.w}×${grp.h}).` });
    return grp;
  }
  async function cutSelection() { if (copySelection()) await deleteSelection(); }
  function pasteClipboard() {
    if (!spriteClipboard) { log?.append({ severity: "warn", message: "Sprite clipboard is empty — select sprites and Ctrl+C first." }); return; }
    if (spriteClipboard.sourceTileset !== tileset) { log?.append({ severity: "error", message: `Cross-tileset paste isn't supported (clipboard tileset ${spriteClipboard.sourceTileset} → ${tileset}).` }); return; }
    armGroup(spriteClipboard, `${spriteClipboard.items.length} sprites (paste)`);
  }
  /** N: recall a control group — arm its brush or sprite group; an empty
   * slot just tells the user how to fill it. */
  function recallGroup(i: number) {
    const g = controlGroupsRef.current[i];
    if (!g) {
      log?.append({ severity: "warn", message: `Group ${i + 1} is empty — Ctrl+${i + 1} saves.` });
      return;
    }
    if (g.kind === "brush") armBrush(g.brush);
    else armGroup(g.group, `group ${i + 1}`);
  }
  /** Ctrl+N: save whatever is armed (brush, then ghost), else the current
   * selection sliced fresh, into that control group slot. */
  function saveGroup(i: number) {
    if (activeBrush) {
      setControlGroup(i, { kind: "brush", brush: activeBrush });
      log?.append({ severity: "info", message: `Group ${i + 1} saved (brush).` });
      return;
    }
    if (placingBuilding?.group) {
      setControlGroup(i, { kind: "group", group: placingBuilding.group });
      log?.append({ severity: "info", message: `Group ${i + 1} saved (${placingBuilding.group.items.length} sprites).` });
      return;
    }
    const sel = selectionRef.current;
    if (sel.length > 0 && renderer) {
      const name = datPath.split(/[\\/]/).pop() ?? "sector";
      const grp = sliceGroup(renderer.getParsed(), sel, tileset, name, isShadowOnlySlot);
      if (grp) {
        setControlGroup(i, { kind: "group", group: grp });
        log?.append({ severity: "info", message: `Group ${i + 1} saved (${grp.items.length} sprites).` });
        return;
      }
    }
    log?.append({ severity: "warn", message: "Nothing to save — arm a brush/ghost or select sprites first." });
  }
  /** Esc / right-click: queue → line-drag → paste-mode → armed ghost →
   * brush/payload → selection (one level per press). */
  function cancelOneLevel() {
    // The placement queue drops FIRST — it's the most
    // transient in-flight state, and Esc must never disarm the whole
    // ghost (losing the queue with it) while it's mid-build.
    if (queue.length > 0) { setQueue([]); return; }
    // Cancel just the in-progress LINE, not the fence ghost it
    // belongs to — one more Esc then drops the ghost via the level below.
    if (lineAnchor) { setLineAnchor(null); return; }
    // Fix-round-1b finding: region paste IS reachable in mode-less (via
    // the SelectOptions strip whenever a marquee exists), so Escape must
    // still cancel it — before the ghost level.
    if (pasteMode) { setPasteMode(false); return; }
    if (placingBuilding) { setPlacingBuilding(null); return; }
    if (activeBrush || payload !== "tiles" || oneShotShape) { setActiveBrush(null); setPayload("tiles"); setOneShotShape(null); return; }
    if (selectionRef.current.length > 0) { setSelection([]); return; }
  }
  /** R / Shift+R: cycle the armed single-sprite ghost's sub, else the single selected sprite's sub (a move-in-place edit). */
  async function cycleArmedOrSelected(dir: 1 | -1) {
    if (!renderer) return;
    if (placingBuilding?.group && placingBuilding.group.items.length === 1) {
      const it = placingBuilding.group.items[0]!;
      // Review finding #2: getSlotInfo().subCount is a dense MAX — a
      // slot can have gaps (sub 2 missing between 1 and 3). Step through
      // listValidSubs (sparse-aware) so a cycle never lands on a hole.
      const subs = renderer.listValidSubs(it.slot);
      armGroup({ ...placingBuilding.group, items: [{ ...it, sub: stepValidSub(subs, it.sub, dir) }] }, placingBuilding.label);
      return;
    }
    const sel = selectionRef.current;
    if (sel.length !== 1 || !session || session.read_only) return;
    const r = sel[0]!;
    const parsed = renderer.getParsed();
    const del = deleteEdits(parsed, [r]);
    if (del.edits.length === 0) {
      // Review finding #3: stale selection (already removed by an undo).
      log?.append({ severity: "warn", message: "Selection is stale (already removed) — cleared." });
      setSelection([]);
      return;
    }
    const subs = renderer.listValidSubs(r.slot);
    const next = { ...r, sub: stepValidSub(subs, r.sub, dir) };
    const edits: SessionEdit[] = [...del.edits, { x: r.x, y: r.y, op: "place", layer: r.layer, slot: r.slot, sub: next.sub }];
    // The explicit-shadow sub rides along only if the shadow was carried
    // by deleteEdits (Estoni-style slot+1 shadow on the same tile).
    if (del.edits.some((e) => e.layer === "shadows")) {
      edits.push({ x: r.x, y: r.y, op: "place", layer: "shadows", slot: r.slot + 1, sub: next.sub });
    }
    await applyStroke(`Cycle s${r.slot}.${r.sub}→${next.sub}`, edits, del.touched, [next], "Cycle rejected by the backend");
  }

  function onCanvasMouseUp() {
    // Fence line-drag commit — checked
    // FIRST since a fence line and a grabbed-sprite move (below) can
    // never both be in flight. `lineDrag` is null exactly when the drag
    // never moved off its anchor tile (or `hovered` went null) — that
    // degenerates to the normal single-place `run()`, same as a plain
    // click on a non-fence ghost. Always clears the anchor, win or lose.
    if (lineAnchor) {
      const anchor = lineAnchor;
      const drag = lineDrag;
      setLineAnchor(null);
      if (drag && placingBuilding && renderer) {
        if (worstOf(lineVerdicts) === "blocking") {
          const why = lineVerdicts.find((t) => t.tier === "blocking");
          log?.append({ severity: "warn", message: `Can't place fence line — ${why?.detail ?? why?.test ?? "blocked"}.` });
          return;
        }
        const parsed = renderer.getParsed();
        const { edits, placed, dropped } = groupPasteEdits(drag.group, drag.anchor, parsed.cols, parsed.rows);
        if (edits.length === 0) { log?.append({ severity: "warn", message: "Nothing placed — off the map." }); return; }
        if (dropped) log?.append({ severity: "warn", message: `${dropped} fence tile(s) fell off the map and were skipped.` });
        const touched = edits.map((ed) => ({ x: ed.x, y: ed.y, layer: ed.layer! }));
        // Busy-gate like the normal stamp path: hides the single-tile
        // ghost that would otherwise reappear at the line's end tile
        // (now `hovered`) and double-draw over the just-committed fence
        // for the round-trip window.
        setPlacementStampBusy(true);
        void applyStroke(`Fence line (${placed.length} tiles)`, edits, touched, placed, "Fence line rejected by the backend")
          .finally(() => setPlacementStampBusy(false));
      } else if (placingBuilding) {
        const r = placingBuilding.run(anchor.x, anchor.y);
        if (r instanceof Promise) {
          setPlacementStampBusy(true);
          void r.finally(() => setPlacementStampBusy(false));
        }
      }
      return;
    }
    // Drop a grabbed sprite (Shift+drag from inspect). Releasing on the
    // source tile is a no-op cancel.
    if (moveRef.current) {
      const { hit } = moveRef.current;
      moveRef.current = null;
      setMoving(false);
      const to = hovered;
      if (to && (to.x !== hit.x || to.y !== hit.y)) {
        // Shift+drag move is validity-
        // checked too, same as the keyboard nudge. `moveEntry`'s own
        // body is the co-tenant lane's code and stays untouched — the
        // check lives here, at the drop, gating whether it gets called.
        if (modeless && renderer) {
          const moved = { ...hit, x: to.x, y: to.y };
          const v = localCheck(renderer.getParsed(), placementTables, occupancy,
            [moved], (s) => renderer.getFootprint(s), new Set([refKey(hit)]));
          if (worstOf(v) === "blocking") {
            const why = v.find((t) => t.tier === "blocking");
            log?.append({ severity: "warn", message: `Can't move there — ${why?.detail ?? why?.test ?? "blocked"}.` });
            return;
          }
        }
        void moveEntry(hit, to);
      }
      return;
    }
    // Region pick: releasing a drag over a DIFFERENT tile completes the
    // region. Releasing on the anchor tile keeps the picker armed, so
    // click-then-click still works for precision picks.
    if (pickingRect?.stage === 1 && pickingRect.corner1) {
      const c1 = pickingRect.corner1;
      const tile = hovered;
      if (tile && (tile.x !== c1.x || tile.y !== c1.y)) {
        const cb = pickingRect.onComplete;
        pickSuppressClickRef.current = true;
        if (pickingRect.sticky) setPickingRect({ ...pickingRect, stage: 0, corner1: undefined });
        else setPickingRect(null);
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
    // onMouseUpDrag below. Mode-less additionally does the sprite marquee.
    if (tool === "select" && selectAnchor) {
      const b = selectCursor ?? selectAnchor;
      const dragged = b.x !== selectAnchor.x || b.y !== selectAnchor.y;
      if (modeless) {
        if (dragged && renderer && renderMeta) {
          // Marquee over sprites: the screen-space bbox of an iso tile
          // rect is bounded by all FOUR corner tiles, not just the two
          // drag endpoints (e.g. dragging north→south the screen-LEFT
          // extreme is the rect's (x0,y1) tile, owned by neither
          // endpoint) — union all four corner tiles' diamond corners.
          const x0 = Math.min(selectAnchor.x, b.x); const x1 = Math.max(selectAnchor.x, b.x);
          const y0 = Math.min(selectAnchor.y, b.y); const y1 = Math.max(selectAnchor.y, b.y);
          const corners = [
            ...tileDiamondCorners(x0, y0, renderMeta),
            ...tileDiamondCorners(x1, y0, renderMeta),
            ...tileDiamondCorners(x0, y1, renderMeta),
            ...tileDiamondCorners(x1, y1, renderMeta),
          ];
          const xs = corners.map((c) => c[0]); const ys = corners.map((c) => c[1]);
          const rect = { x: Math.min(...xs), y: Math.min(...ys), w: Math.max(...xs) - Math.min(...xs), h: Math.max(...ys) - Math.min(...ys) };
          // The rect is the axis-aligned canvas bbox of the tile rect —
          // roughly 2× the drawn iso parallelogram — so spritesInRect
          // over-selects. Filter to hits whose ANCHOR tile actually
          // falls inside the dragged tile rect (review finding #1).
          const hits = renderer.spritesInRect(rect, renderMeta)
            .filter((h) => h.x >= x0 && h.x <= x1 && h.y >= y0 && h.y <= y1);
          setSelection(hits.map((h) => ({ x: h.x, y: h.y, layer: h.layer, slot: h.slot, sub: h.sub })));
          setSelectRect({ a: selectAnchor, b });      // region tools (terrain Copy) still work
        }
        // A clean click (no drag) is handled entirely by onCanvasClick's
        // sprite pick — no selectRect / selection change here. A one-tile
        // mouse jitter on what the user meant as a click still counts as
        // `dragged` and yields a spurious 2-tile marquee; acceptable.
      } else {
        setSelectRect({ a: selectAnchor, b });
      }
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
  async function commitShape(anchor: Tile, cursor: Tile) {
    if (!session || session.read_only || !renderer) return;
    // Consume the one-shot arm the moment a commit is attempted (spec
    // D8's command-card verbs — wired in a later phase; `oneShotShape`
    // is always null in P1, so this is a no-op today).
    setOneShotShape(null);
    const effectiveShape = oneShotShape ?? shapeKind;
    const cols = info.data?.cols ?? 0;
    const rows = info.data?.rows ?? 0;
    const tiles = shapeTiles(effectiveShape, anchor, cursor).filter(
      (t) => t.x >= 0 && t.y >= 0 && t.x < cols && t.y < rows,
    );
    if (tiles.length === 0) return;
    // Soft guard for huge fills — one cheap confirm, not a hard block.
    if (
      tiles.length > 2000
      && !(await confirm({
        title: "Apply large shape?",
        body: `This shape covers ${tiles.length} tiles. Apply?`,
        confirmLabel: "Apply",
      }))
    ) {
      return;
    }
    // The shape kind picked the REGION; the active payload decides what to
    // do with it (place / erase / height / room).
    const region = effectiveShape === "line"
      ? "Line"
      : effectiveShape === "rect-outline"
        ? "Outline"
        : "Fill";
    applyPayloadBatch(tiles, region);
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
        && !(await confirm({
          title: "Paste over tiles?",
          body: `Paste over ${targetTiles} tiles? This replaces their current `
            + `terrain, objects, structures, rooms and heights.`,
          confirmLabel: "Paste",
          destructive: true,
        }))
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

  /** Clear the marquee selection — wipe all 6 layers + room + height on
   * every tile in the rect, as ONE undoable stroke. Mirrors doPaste's
   * snapshot → local mirror → transactional applyEdits → revert-on-reject
   * pattern. (R4 editor verb: Delete / area-erase.) */
  async function doDeleteSelection() {
    if (!session || session.read_only || !renderer || !selectRect) return;
    const cols = info.data?.cols ?? renderer.getParsed().cols;
    const rows = info.data?.rows ?? renderer.getParsed().rows;
    const x0 = Math.max(0, Math.min(selectRect.a.x, selectRect.b.x));
    const y0 = Math.max(0, Math.min(selectRect.a.y, selectRect.b.y));
    const x1 = Math.min(cols - 1, Math.max(selectRect.a.x, selectRect.b.x));
    const y1 = Math.min(rows - 1, Math.max(selectRect.a.y, selectRect.b.y));
    if (x0 > x1 || y0 > y1) return;
    const nTiles = (x1 - x0 + 1) * (y1 - y0 + 1);
    if (
      nTiles > 500
      && !(await confirm({
        title: "Clear tiles?",
        body: `Clear ${nTiles} tiles? This removes their terrain, objects, `
          + "structures, rooms and heights.",
        confirmLabel: "Clear",
        destructive: true,
      }))
    ) return;
    const edits: SessionEdit[] = [];
    for (let y = y0; y <= y1; y++) {
      for (let x = x0; x <= x1; x++) {
        for (const l of CLIP_LAYERS) {
          edits.push({ x, y, op: "set_entries", layer: l, entries: [] });
        }
        edits.push({ x, y, op: "set_room", room_id: 0 });
        edits.push({ x, y, op: "set_height", height: 0 });
      }
    }
    setEditsInFlight((n) => n + 1);
    let strokeCommitted = false;
    try {
      renderer.beginStroke(`Clear ${x1 - x0 + 1}×${y1 - y0 + 1} (${nTiles} tiles)`);
      for (const ed of edits) {
        if (ed.op === "set_entries" && ed.layer) renderer.recordSnapshot(ed.x, ed.y, ed.layer);
        else if (ed.op === "set_room") renderer.recordRoomSnapshot(ed.x, ed.y);
        else if (ed.op === "set_height") renderer.recordHeightSnapshot(ed.x, ed.y);
        renderer.applyLocalEdit({
          x: ed.x, y: ed.y, op: ed.op,
          layer: ed.layer, entries: ed.entries,
          roomId: ed.room_id, height: ed.height,
        });
      }
      renderer.endStroke();
      strokeCommitted = true;
      bumpHistory();
      setRenderEpoch((e) => e + 1);
      const res = await applyEdits(session.session_id, edits);
      setSession(res.session);
      log?.append({ severity: "info", message: `Cleared ${nTiles} tiles.` });
    } catch (e) {
      // applyEdits is transactional — revert the optimistic local mirror
      // (no backend round-trip; the server is already at pre-clear state).
      if (strokeCommitted) {
        const entry = renderer.discardLastUndo();
        if (entry) {
          for (const s of entry.snapshots) renderer.applyLocalEdit({ x: s.x, y: s.y, op: "set_entries", layer: s.layer, entries: s.entries });
          for (const r of entry.roomSnapshots) renderer.applyLocalEdit({ x: r.x, y: r.y, op: "set_room", roomId: r.roomId });
          for (const h of entry.heightSnapshots) renderer.applyLocalEdit({ x: h.x, y: h.y, op: "set_height", height: h.height });
          bumpHistory();
          setRenderEpoch((e2) => e2 + 1);
        }
      }
      log?.append({
        severity: "error",
        message: "Clear failed — the edit batch was rejected; reverted local changes.",
        detail: e instanceof Error ? e.message : String(e),
      });
    } finally {
      setEditsInFlight((n) => Math.max(0, n - 1));
    }
  }

  /** Cut = copy the selection to the clipboard, then clear it. */
  async function doCut() {
    if (!session || session.read_only || !selectRect) return;
    await doCopy();
    await doDeleteSelection();
  }

  /** Move = cut, then arm paste so the next map click drops the cut region
   * at a new spot (the existing paste ghost previews it under the cursor). */
  async function doMove() {
    if (!session || session.read_only || !selectRect) return;
    await doCut();
    setPasteMode(true);
  }

  /** Flood fill (R4): from the clicked seed, find its 4-connected region of
   * same-top-slot tiles on the brush's target layer, and fill them all with
   * the active brush (same `place` op as rect-fill) in ONE undoable stroke.
   * Capped for safety; confirms on big fills. */
  async function doFloodFill(seed: Tile) {
    if (!session || session.read_only || !renderer) return;
    if (payload === "tiles" && !activeBrush) return;
    const parsed = renderer.getParsed();
    const cols = parsed.cols, rows = parsed.rows;
    // Region = tiles connected to the seed that share its top slot on the
    // reference layer — the brush's layer for the Tiles payload, else land
    // (flood the connected ground area for erase / height / room).
    const layer: LayerName = (payload === "tiles" && activeBrush)
      ? ((paintLayer ?? activeBrush.layer) as LayerName)
      : "land";
    const topSlotAt = (x: number, y: number): number => {
      const arr = (parsed[layer] as number[][][])[y * cols + x] ?? [];
      const last = arr[arr.length - 1];
      return last ? (last[0] ?? -1) : -1;  // -1 = empty
    };
    const target = topSlotAt(seed.x, seed.y);
    if (payload === "tiles" && activeBrush && target === activeBrush.slot) {
      log?.append({ severity: "info", message: "Flood fill: the seed already holds this brush." });
      return;
    }
    const seen = new Set<number>();
    const region: Tile[] = [];
    const stack: Array<[number, number]> = [[seed.x, seed.y]];
    const CAP = 6000;
    while (stack.length > 0) {
      const popped = stack.pop()!;
      const x = popped[0], y = popped[1];
      if (x < 0 || y < 0 || x >= cols || y >= rows) continue;
      const g = y * cols + x;
      if (seen.has(g)) continue;
      if (topSlotAt(x, y) !== target) continue;
      seen.add(g);
      region.push({ x, y });
      if (region.length > CAP) break;
      stack.push([x + 1, y], [x - 1, y], [x, y + 1], [x, y - 1]);
    }
    if (region.length === 0) return;
    if (
      region.length > 2000
      && !(await confirm({
        title: "Flood-fill?",
        body: `Flood-fill ${region.length} tiles?`,
        confirmLabel: "Fill",
      }))
    ) return;
    applyPayloadBatch(region, "Fill");
    log?.append({ severity: "info", message: `Flood-filled ${region.length} tiles.` });
  }

  /** Revert the last `n` strokes (History-panel click). Sequential so each
   * backend round-trip lands in order. */
  async function revertUndo(n: number) {
    for (let i = 0; i < n; i++) await undo();
    bumpHistory();
  }
  /** Re-apply the next `n` shelved strokes (History-panel redo click). */
  async function redoForward(n: number) {
    for (let i = 0; i < n; i++) await redo();
    bumpHistory();
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
    // Mode-less: right-click cancels/disarms whatever's armed
    // (ghost / brush / payload / one-shot shape) instead of eyedropping.
    // Plain right-click with NOTHING armed still falls through to the
    // eyedropper below, unchanged. `!e.altKey` keeps this from shadowing
    // Alt+right-click's sub-cycle just below — cancel must not win over
    // cycling the very brush it would otherwise disarm.
    if (modeless && !e.altKey && (placingBuilding || activeBrush || payload !== "tiles" || oneShotShape)) {
      cancelOneLevel();
      return;
    }
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
  // Both go through saveSessionWithExternalRetry (the single owner of
  // the 409 EXTERNAL_MODIFICATION retry contract); the hotkey only adds
  // its own dirty-tracking updates on success.
  const externalOverrideRef = useRef(false);
  const saveFromHotkey = async () => {
    if (!session || session.read_only || !localDirty) return;
    const out = await saveSessionWithExternalRetry(
      session.session_id, externalOverrideRef, log);
    if (out.ok) {
      setSession(out.res.session);
      setSavedAtDepth(undoDepth);
      setSavedAtGen(renderer ? renderer.generation() : histGen);
    }
  };

  // ─── New sector + Save-a-copy-as (R6 "create / clone sectors") ─────
  // Path-derivation helper: split datPath into its directory (with the
  // OS-native separator) so a prompt default lands a sibling .dat next
  // to the current sector. Falls back to a bare filename when there's
  // no directory component.
  const siblingDatPath = (filename: string): string => {
    const m = datPath.match(/^(.*[\\/])[^\\/]*$/);
    return (m ? m[1] : "") + filename;
  };

  // "New sector…" — prompt for a destination .dat + tileset, write a
  // fresh empty 160×160 sector server-side, then navigate to it (which
  // opens a session on the new file). Guards: refuse the create on a
  // path collision unless the user confirms an overwrite.
  const createNewSector = async () => {
    if (localDirty) {
      const ok = await confirm({
        title: "Discard unsaved edits?",
        body: "You have unsaved edits in this sector. Creating a new sector "
          + "navigates away and discards them. Save first if you want to keep them.",
        confirmLabel: "Discard and create",
        cancelLabel: "Cancel",
        destructive: true,
      });
      if (!ok) return;
    }
    const dest = await prompt({
      title: "New sector",
      label: "Full path for the new .dat file:",
      defaultValue: siblingDatPath("NEW.dat"),
    });
    if (!dest) return;
    const tsRaw = await prompt({
      title: "New sector",
      label: "Tileset index for the new sector (number):",
      defaultValue: String(tileset || 0),
      validate: (s) => (Number.isFinite(parseInt(s, 10)) && parseInt(s, 10) >= 0
        ? null : "Enter a non-negative tileset number."),
    });
    if (tsRaw === null) return;
    const ts = parseInt(tsRaw, 10);
    if (Number.isNaN(ts) || ts < 0) {
      log?.append({ severity: "error", message: `Invalid tileset: ${tsRaw}` });
      return;
    }
    const navigateToNew = () => {
      const np = new URLSearchParams();
      np.set("dat", dest);
      if (xmlPath) np.set("xml", xmlPath);
      np.set("tileset", String(ts));
      navigate(`/mapforge/sector?${np.toString()}`);
    };
    try {
      await newSector(dest, ts);
      log?.append({ severity: "success", message: `Created empty sector ${dest}` });
      navigateToNew();
    } catch (e) {
      const msg = e instanceof Error ? e.message : String(e);
      // The backend signals a collision with a 409 FILE_EXISTS — offer
      // an explicit overwrite confirmation rather than silently clobbering.
      if (msg.includes("FILE_EXISTS")) {
        const ok = await confirm({
          title: "Overwrite existing map?",
          body: `${dest} already exists. Overwrite it with a fresh empty sector? `
            + "This destroys the existing map.",
          confirmLabel: "Overwrite",
          destructive: true,
        });
        if (!ok) return;
        try {
          await newSector(dest, ts, { overwrite: true });
          log?.append({ severity: "success", message: `Overwrote ${dest} with empty sector` });
          navigateToNew();
        } catch (e2) {
          log?.append({
            severity: "error",
            message: "New sector failed",
            detail: e2 instanceof Error ? e2.message : String(e2),
          });
        }
        return;
      }
      log?.append({
        severity: "error",
        message: "New sector failed",
        detail: msg,
      });
    }
  };

  // "Save a copy as…" — write the CURRENT session state to a new .dat
  // without touching the original. Stays on the current sector afterward
  // (the copy is a snapshot; the session keeps editing the source).
  const saveCopyAsNow = async () => {
    if (!session) {
      log?.append({ severity: "warn", message: "No session open." });
      return;
    }
    const dest = await prompt({
      title: "Save a copy as",
      label: "Full path for the new .dat file:",
      defaultValue: siblingDatPath(`${(datPath.split(/[\\/]/).pop() ?? "sector").replace(/\.dat$/i, "")}_copy.dat`),
    });
    if (!dest) return;
    const doCopy = async (overwrite: boolean) => {
      const res = await saveCopyAs(session.session_id, dest, { overwrite });
      log?.append({
        severity: "success",
        message: `Saved a copy (${(res.bytes_written / 1024).toFixed(1)} KB) to ${res.dat_path}`,
      });
    };
    try {
      await doCopy(false);
    } catch (e) {
      const msg = e instanceof Error ? e.message : String(e);
      if (msg.includes("FILE_EXISTS")) {
        const ok = await confirm({
          title: "Overwrite existing file?",
          body: `${dest} already exists. Overwrite it?`,
          confirmLabel: "Overwrite",
          destructive: true,
        });
        if (!ok) return;
        try {
          await doCopy(true);
        } catch (e2) {
          log?.append({
            severity: "error",
            message: "Save a copy failed",
            detail: e2 instanceof Error ? e2.message : String(e2),
          });
        }
        return;
      }
      log?.append({
        severity: "error",
        message: "Save a copy failed",
        detail: msg,
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
      // Skip every remaining shortcut — including the digit-based
      // control-group blocks below — while a dialog is open (Settings, a
      // confirm modal, …). Moved ahead of those blocks (was originally
      // only checked after the combo/actionForBinding lookup further
      // down): a plain "1" or "Ctrl+1" used to recall/save a control
      // group right through an open dialog since neither block ever
      // reached the old, later check.
      if (document.querySelector("[data-app-dialog]")) return;
      // WASD pans the map (camera scroll). Plain keys only — no modifier —
      // so Ctrl/Shift/Alt combos stay free; the input/textarea/select and
      // dialog guards above already exclude typing contexts. Held keys
      // repeat via OS key-repeat → continuous scroll. `a` intentionally
      // shadows the asset-browser hotkey (WASD was the explicit ask); the
      // asset browser stays reachable from its toolbar button.
      if (!e.ctrlKey && !e.metaKey && !e.altKey && !e.shiftKey) {
        const PAN_STEP = 64;
        let dx = 0, dy = 0;
        switch (e.key.toLowerCase()) {
          case "w": dy = PAN_STEP; break;   // reveal content above
          case "s": dy = -PAN_STEP; break;  // reveal content below
          case "a": dx = PAN_STEP; break;   // reveal content left
          case "d": dx = -PAN_STEP; break;  // reveal content right
        }
        if (dx || dy) {
          e.preventDefault();
          setPan((p) => ({ x: p.x + dx, y: p.y + dy }));
          return;
        }
      }
      // Number keys 1-9 recall a control group (a StarCraft-style brush
      // or sprite-group slot). Special-cased here rather
      // than as 9 rebindable registry actions; skipped with any modifier
      // so Ctrl+1 etc. stay free for Ctrl+N save below. 0 is left to the
      // reset-view binding.
      if (/^[1-9]$/.test(e.key) && !e.ctrlKey && !e.metaKey && !e.altKey && !e.shiftKey) {
        e.preventDefault();
        recallGroup(Number(e.key) - 1);
        return;
      }
      // Ctrl+1-9 saves the armed brush/ghost, or the current selection,
      // into that control group slot. Checked BEFORE the combo lookup so
      // preventDefault fires before the browser's own Ctrl+<digit> tab
      // switch ever sees the key.
      if ((e.ctrlKey || e.metaKey) && !e.altKey && !e.shiftKey && /^[1-9]$/.test(e.key)) {
        e.preventDefault();
        saveGroup(Number(e.key) - 1);
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
      // Mode-less-only actions: NEVER swallow their
      // key (no preventDefault, no dispatch) when settings.legacyTools is
      // on — otherwise Ctrl+C/X/V, Delete, arrows and Escape would eat
      // the user's normal page behaviour for actions that can't fire.
      if (!modelessRef.current && MODELESS_ONLY_ACTIONS.has(action)) return;
      // Within mode-less, a no-op for the current state is also NOT
      // claimed — don't swallow a key for an action that can't act.
      // Ghost-anchor nudging is deferred (arrows only ever nudge a
      // SELECTION today), so the nudge guard no longer exempts an armed
      // ghost (review finding #8).
      if (action.startsWith("nudge-") && selectionRef.current.length === 0) return;
      if (action === "sel-delete" && selectionRef.current.length === 0) return;
      if ((action === "sel-copy" || action === "sel-cut") && selectionRef.current.length === 0) return;
      if (action === "sel-paste" && !spriteClipboard) return;
      // Nothing for `cancel` to actually do (mirrors cancelOneLevel's own
      // checks) — don't swallow the keystroke's default behaviour for a no-op.
      if (
        action === "cancel"
        && queue.length === 0 && !lineAnchor && !pasteMode && !placingBuilding
        && !activeBrush && payload === "tiles" && !oneShotShape
        && selectionRef.current.length === 0
      ) return;
      // R/Shift+R only ever act on a single-item ghost or a single selected
      // sprite with an editable session (cycleArmedOrSelected's own no-op
      // conditions: no renderer at all is an unconditional no-op; absent
      // a single-item ghost, a single selection still needs a live,
      // writable session before the cycle can do anything).
      if (action === "sel-cycle-next" || action === "sel-cycle-prev") {
        const singleGhost = !!(placingBuilding?.group && placingBuilding.group.items.length === 1);
        const singleSelUsable = selectionRef.current.length === 1 && !!session && !session.read_only;
        if (!renderer || (!singleGhost && !singleSelUsable)) return;
      }
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
          // Review finding #9: setTool still mutates `toolState`, which
          // the placingBuilding-reset effect watches — an invisible
          // (mode-less ignores toolState) mutation would silently
          // cancel an armed ghost. No-op while mode-less.
          if (!modelessRef.current && activeBrush) setTool("pencil");
          break;
        case "tool-inspect":
          if (!modelessRef.current) setTool("inspect");
          break;
        case "zoom-in":
          setZoom((z) => Math.min(8, z * 1.15));
          break;
        case "zoom-out":
          setZoom((z) => Math.max(minZoomRef.current, z / 1.15));
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
        // ─── Mode-less sprite selection / clipboard / ghost ────────────
        // Gated above (MODELESS_ONLY_ACTIONS) so these never reach here
        // with legacyTools on.
        case "sel-copy":
          copySelection();
          break;
        case "sel-cut":
          void cutSelection();
          break;
        case "sel-paste":
          pasteClipboard();
          break;
        case "sel-delete":
          void deleteSelection();
          break;
        case "sel-cycle-next":
          void cycleArmedOrSelected(1);
          break;
        case "sel-cycle-prev":
          void cycleArmedOrSelected(-1);
          break;
        case "nudge-left":
          void nudgeSelection(-1, 0);
          break;
        case "nudge-right":
          void nudgeSelection(1, 0);
          break;
        case "nudge-up":
          void nudgeSelection(0, -1);
          break;
        case "nudge-down":
          void nudgeSelection(0, 1);
          break;
        case "cancel":
          cancelOneLevel();
          break;
        // ─── Payload / shape verbs (command card) — work in
        // BOTH models: mode-less arms a payload/one-shot shape (`tool`
        // derives to pencil/shape automatically); legacy also has to
        // flip the explicit tool since nothing derives it there.
        case "payload-room":
          setPayload("room");
          if (!modelessRef.current) setTool("pencil");
          break;
        case "payload-height":
          setPayload("height");
          if (!modelessRef.current) setTool("pencil");
          break;
        case "payload-erase":
          setPayload("erase");
          if (!modelessRef.current) setTool("pencil");
          break;
        case "shape-rect":
          if (modelessRef.current) setOneShotShape("rect-fill");
          else { setShapeKind("rect-fill"); setTool("shape"); }
          break;
        case "shape-line":
          if (modelessRef.current) setOneShotShape("line");
          else { setShapeKind("line"); setTool("shape"); }
          break;
        case "shape-flood":
          if (modelessRef.current) setOneShotShape("flood");
          else { setShapeKind("flood"); setTool("shape"); }
          break;
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
    // Captured-by-closure deps: settings + the various setters and
    // session state. eslint can't see the switch statement's deps
    // statically; we list the load-bearing ones explicitly. `occupancy`
    // is deliberately NOT here — nudgeSelection reads occupancyRef
    // instead (review finding #10b), so this effect doesn't need to
    // re-bind the whole keydown listener on every edit just to keep one
    // number fresh. `queue`/`lineAnchor`/`pasteMode` ARE listed (alongside
    // the already-present `oneShotShape`) — `cancelOneLevel` (bound as
    // `onKey`'s `cancel` case, and read by the `cancel` no-op guard above)
    // checks all of them, and a stale closure over `queue` here was
    // letting Escape disarm the whole ghost right after a Shift+click
    // queue instead of dropping just the queue first, as documented.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [settings, renderer, session?.session_id, session?.read_only,
      activeBrush, undoDepth, localDirty,
      selection, spriteClipboard, placingBuilding, payload, oneShotShape,
      queue, lineAnchor, pasteMode]);

  // ESC cancels the rectangle corner picker. Separate from the main
  // shortcut effect so it can react instantly when the picker mounts
  // without re-binding every other shortcut.
  useEffect(() => {
    // Sticky generator picks own ESC themselves (the Generate panel
    // deselects the whole generator on ESC) — don't tear the pick down
    // from here, or aiming would break mid-generate.
    if (!pickingRect || pickingRect.sticky) return;
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
    // Review finding #9 — see the dispatcher's tool-pencil/tool-inspect
    // cases: mutating toolState while mode-less is invisible but still
    // trips the placingBuilding-reset effect, cancelling an armed ghost.
    if (modelessRef.current) return;
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
      const next = Math.max(minZoomRef.current, Math.min(8, z * delta));
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
        panX: pan.x, panY: pan.y, moved: true,
      };
    } else if (e.button === 0 && tool === "inspect") {
      // Plain left-drag pans in inspect (no Alt needed). Armed as a
      // MAYBE-pan: only becomes a pan after a 4px move threshold, so a
      // clean click still pins the inspector (see onCanvasClick).
      panConsumedClickRef.current = false;
      dragRef.current = {
        startX: e.clientX, startY: e.clientY,
        panX: pan.x, panY: pan.y, moved: false,
      };
    }
  }
  function onMouseMoveDrag(e: React.MouseEvent<HTMLDivElement>) {
    if (!dragRef.current) return;
    const dx = e.clientX - dragRef.current.startX;
    const dy = e.clientY - dragRef.current.startY;
    if (!dragRef.current.moved) {
      if (Math.abs(dx) + Math.abs(dy) < 4) return;   // click jitter, not a pan yet
      dragRef.current.moved = true;
      panConsumedClickRef.current = true;            // suppress the pin on mouseup
    }
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
    // Same off-canvas-release safety net for a fence line-drag:
    // a release outside the inner canvas element never reaches
    // onCanvasMouseUp, so the anchor would otherwise stick armed with no
    // way to finish the drag. DROPS the line without committing (mirrors
    // Escape) — a release the user can already see landed off the grid
    // isn't a tile the line should try to write to anyway.
    if (lineAnchor !== null) {
      setLineAnchor(null);
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
    // A command-card shape verb (Rect/Line/Flood) arms `oneShotShape`,
    // which overrides `shapeKind` for that one drag (commitShape already
    // honours this).
    const effectiveShape = oneShotShape ?? shapeKind;
    let tiles = shapeTiles(effectiveShape, shapeAnchor, cursor);
    if (tiles.length > 6000) {
      tiles = shapeTiles("rect-outline", shapeAnchor, cursor);
    }
    return tiles.filter(
      (t) => t.x >= 0 && t.y >= 0 && t.x < cols && t.y < rows,
    );
  }, [tool, shapeAnchor, shapeCursor, shapeKind, oneShotShape, info.data,
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
    // See previewTiles above — a card-armed one-shot shape overrides
    // `shapeKind` for the readout too.
    const effectiveShape = oneShotShape ?? shapeKind;
    const w = Math.abs(cursor.x - shapeAnchor.x) + 1;
    const h = Math.abs(cursor.y - shapeAnchor.y) + 1;
    let count: number;
    if (effectiveShape === "line") count = Math.max(w, h);
    else if (effectiveShape === "rect-outline") {
      count = w === 1 || h === 1 ? w * h : 2 * (w + h) - 4;
    } else if (effectiveShape === "rect-fill") {
      count = w * h;
    } else {
      // diamond / cross / triangle / hexagon — exact count from the geometry
      count = shapeTiles(effectiveShape, shapeAnchor, cursor).length;
    }
    return { w, h, count, label: "Shape" };
  }, [tool, shapeAnchor, shapeCursor, shapeKind, oneShotShape,
      selectAnchor, selectCursor, selectRect, pasteMode, clipboard,
      pickingRect, hovered, placingBuilding]);

  // ─── Command card ────────────────────────────────────────────────────
  // Cells for the current mode-less state (ghost > brush > selection >
  // nothing — cardCellsFor's own precedence). Handlers are the SAME
  // functions the hotkey dispatcher above calls, so click and keypress
  // are one code path (mounted below, in renderCanvasPanel).
  const cardCells = useMemo(
    () => cardCellsFor(
      {
        selectionCount: selection.length,
        ghost: !!placingBuilding?.group,
        brush: !!activeBrush,
        payload,
        clipboard: !!spriteClipboard,
        readOnly: session?.read_only ?? false,
      },
      (id) => bindingFor(settings, id),
      {
        "sel-copy": () => { copySelection(); },
        "sel-cut": () => { void cutSelection(); },
        "sel-delete": () => { void deleteSelection(); },
        "sel-cycle-next": () => { void cycleArmedOrSelected(1); },
        "sel-paste": pasteClipboard,
        cancel: cancelOneLevel,
        "payload-room": () => setPayload("room"),
        "payload-height": () => setPayload("height"),
        "payload-erase": () => setPayload("erase"),
        "shape-rect": () => setOneShotShape("rect-fill"),
        "shape-line": () => setOneShotShape("line"),
        "shape-flood": () => setOneShotShape("flood"),
        place: () => log?.append({ severity: "info", message: "Click the map to place." }),
        queue: () => log?.append({ severity: "info", message: "Shift+click a tile to queue it; plain click builds the queue." }),
        radius: () => log?.append({
          severity: "info",
          message: `Radius ${brushRadius} — ${bindingFor(settings, "brush-size-up")} grow · `
                  + `${bindingFor(settings, "brush-size-down")} shrink.`,
        }),
        "nudge-help": () => log?.append({ severity: "info", message: "Arrow keys nudge the selection 1 tile." }),
        "save-group-help": () => log?.append({
          severity: "info",
          message: "Ctrl+1..9 saves the armed brush/ghost, or the selection, to that group.",
        }),
      },
    ),
    // Handler closures (copySelection etc.) are plain `function`s re-
    // created every render, not useCallback, so listing every one of
    // them would make this memo recompute every render anyway — same
    // tradeoff the keydown dispatcher effect above accepts. `renderer`
    // and `session?.session_id` ARE listed despite that, because they're
    // exactly what those closures capture that no ref mirrors (a stale
    // one would point deleteSelection/copySelection/pasteClipboard at a
    // dead session after a reopen) — same two the dispatcher effect
    // lists for the same reason. `queue`/`lineAnchor`/`pasteMode`/
    // `oneShotShape` are listed too — `cancelOneLevel` (the `cancel`
    // cell) reads all four.
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [selection.length, placingBuilding, activeBrush, payload, spriteClipboard,
     session?.read_only, session?.session_id, renderer, settings, brushRadius,
     queue, lineAnchor, pasteMode, oneShotShape],
  );

  // ─── Height overlay ─────────────────────────────────────────────────
  // Per-tile heights are invisible in the iso render (neither renderer
  // uses height for Z), so while the height brush is active we overlay the
  // non-zero-height tiles — tinted by height, numbered when zoomed in.
  // Recomputed on every edit (renderEpoch) so it tracks the brush live.
  const heightOverlay = useMemo<Array<{ x: number; y: number; h: number }> | null>(() => {
    // Shown while the Height PAYLOAD is active — or while a generator
    // ghost containing heights is up (heights are invisible in the iso
    // render; without this a cliff preview looks like a no-op).
    if ((payload !== "height" && !ghostHasHeights) || !renderer) return null;
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
  }, [payload, renderer, renderEpoch, ghostHasHeights]);

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
  // below is left untouched as the safe fallback); a later change retires
  // the fixed layout and dedupe. The canvas copy fills its panel
  // (h-full) instead of the fixed layout's 70vh.
  // Brush Box (dock panel "assets") = the consolidated picker (R3): the
  // Favorites + Recent + Just-added rail folded in ON TOP of the full
  // searchable, categorized MapForgePalette grid (with the in-place
  // sub-frame picker). The read-only cross-tileset "Tileset Viewer"
  // (MapForgeTilesetBrowser) stays a SEPARATE panel. Every pick arms the
  // pencil via armBrush.
  const renderBrushBoxPanel = () => (
    xmlPath ? (
      <div className="flex h-full flex-col bg-gray-950">
        {/* Favorites → Recent → Just-added (the old rail), capped so the
            Browse grid below always stays in view. */}
        <div className="max-h-[45%] shrink-0 overflow-y-auto border-b border-gray-800">
          <MapForgePaletteRail
            embedded
            renderer={renderer}
            activeBrush={activeBrush}
            recentBrushes={recentBrushes}
            recentAdditions={recentAdditions}
            controlGroups={controlGroups}
            onRecallGroup={recallGroup}
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
            onOpenInTilesetEditor={async (a) => {
              if (localDirty) {
                const ok = await confirm({
                  title: "Discard unsaved edits?",
                  body: "You have unsaved edits in this sector. Open the Tileset "
                    + "Editor anyway and discard them? Save first if you want to keep them.",
                  confirmLabel: "Discard and go",
                  destructive: true,
                });
                if (!ok) return;
              }
              navigate(`/tileset-editor/${a.tileset}?slot=${a.slot}`);
            }}
            onOpenViewer={onBrowseAssets}
          />
        </div>
        {/* Browse: search + category chips + thumbnail grid (in-place sub picker). */}
        <div className="min-h-0 flex-1">
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
        </div>
      </div>
    ) : (
      <div className="rounded border border-gray-700 bg-gray-950 p-3 text-xs text-gray-500">
        No Ja2Set.dat.xml — Brush Box unavailable
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

  const renderCanvasPanel = () => (
    <div
      ref={setCanvasViewportEl}
      className="relative h-full w-full overflow-hidden bg-gray-950"
      style={{ cursor: pickingRect || pickingPoint || placingBuilding ? "crosshair" : undefined }}
      onWheel={onWheel}
      onMouseDown={onMouseDown}
      onMouseMove={onMouseMoveDrag}
      onMouseUp={onMouseUpDrag}
      onMouseLeave={onMouseUpDrag}
    >
      {pickingRect && (
        <div className="absolute inset-x-0 top-0 z-30 flex items-center justify-between bg-emerald-800/95 text-emerald-50 px-3 py-2 text-sm border-b border-emerald-500 shadow-md pointer-events-none">
          <span>
            {pickingRect.stage === 1
              ? `Corner pinned at (${pickingRect.corner1?.x}, ${pickingRect.corner1?.y}) — release on the opposite corner`
              : "Drag a box on the map to aim this generator (preview is live)"}
          </span>
          <span className="text-xs text-emerald-200">✓ Apply / ✕ Clear in the Generate panel</span>
        </div>
      )}
      {!pickingRect && pickingPoint && (
        <div className="absolute inset-x-0 top-0 z-30 flex items-center justify-between bg-emerald-800/95 text-emerald-50 px-3 py-2 text-sm border-b border-emerald-500 shadow-md pointer-events-none">
          <span>
            Click the map to set the focal point (preview is live — radius
            on the slider)
          </span>
          <span className="text-xs text-emerald-200">✓ Apply / ✕ Clear in the Generate panel</span>
        </div>
      )}
      {!pickingRect && !pickingPoint && placingBuilding && (
        <div className="absolute inset-x-0 top-0 z-30 flex items-center justify-between bg-sky-800/95 text-sky-50 px-3 py-2 text-sm border-b border-sky-500 shadow-md pointer-events-none">
          <span>
            {placingBuilding.group ? (
              <>
                Placing {placingBuilding.label} — click = place · Shift+click = queue
                · R = cycle · Esc/RMB = cancel
                {queue.length > 0 ? ` · ${queue.length} queued` : ""}
              </>
            ) : (
              <>
                Placing {placingBuilding.w}×{placingBuilding.h} building
                ({placingBuilding.label}) — click to stamp, click again for
                another (each gets its own room)
              </>
            )}
          </span>
          <span className="text-xs text-sky-200">ESC to cancel</span>
        </div>
      )}
      {!pickingRect && !pickingPoint && !placingBuilding && ghostActive && (
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
          <div
            className="relative"
            style={{
              // Frame keeps FULL-map dims even in detail mode so the
              // transform's -50% centering + pan math never shift when
              // the region canvas swaps in.
              width: (selectedRoom === null && detailBbox && fullMetaRef.current ? fullMetaRef.current : renderMeta).canvasW,
              height: (selectedRoom === null && detailBbox && fullMetaRef.current ? fullMetaRef.current : renderMeta).canvasH,
            }}
          >
          <div
            className="absolute"
            style={{
              // Detail mode: place the region canvas where the full map
              // would have put those tiles (meta origins differ by the
              // region's iso offset). Full mode: (0,0), full size.
              left: selectedRoom === null && detailBbox && fullMetaRef.current
                ? renderMeta.ixMin - fullMetaRef.current.ixMin : 0,
              top: selectedRoom === null && detailBbox && fullMetaRef.current
                ? renderMeta.iyMin - fullMetaRef.current.iyMin : 0,
              width: renderMeta.canvasW,
              height: renderMeta.canvasH,
            }}
          >
            {/* Stacking inside this wrapper (explicit z-indexes):
                  z-0  main canvas (sector render)
                  z-10 SVG overlay (grid / footprint outline / markers)
                  z-20 placement ghost canvas — the building's real
                       sprites must render ABOVE the grid mesh.
                All three share the wrapper's CSS pan/zoom transform. */}
            <canvas
              ref={canvasRef}
              className={"relative z-0 block select-none " + (moving ? "cursor-grabbing" : "cursor-crosshair")}
              style={{
                // Bigmaps rasterize into a DOWNSCALED GL buffer (Chrome's
                // ~2^25-px drawing-buffer clamp — see IsoRendererGL). CSS
                // then stretches it back up; nearest-neighbor upscaling of
                // an already-downscaled raster turns fine art (chainlink,
                // thin props) into blocky checker garbage. Use smooth
                // interpolation whenever the buffer is below logical size;
                // keep crisp pixelated scaling for maps that fit 1:1.
                // Pixelated only when the buffer is 1:1 AND we are
                // magnifying: nearest-neighbor DOWNscaling (zoom < 1) is
                // the same wire-dropping artifact at the CSS layer.
                imageRendering:
                  renderMeta.canvasW * renderMeta.canvasH > 32 * 1024 * 1024
                    || zoom < 1
                    ? "auto" : "pixelated",
                width: renderMeta.canvasW,
                height: renderMeta.canvasH,
              }}
              onMouseDown={onCanvasMouseDown}
              onClick={onCanvasClick}
              onMouseMove={onCanvasMove}
              onMouseUp={onCanvasMouseUp}
              onMouseLeave={() => {
                setHovered(null);
                setSpriteHit(null);
                spriteProbeTileRef.current = null;
              }}
              onContextMenu={onCanvasContextMenu}
            />
            <IsoOverlay
              meta={renderMeta}
              info={info.data}
              selectedRoom={selectedRoom}
              hovered={hovered}
              pinned={pinned}
              spriteHit={inspectLike && !placingBuilding ? spriteHit : null}
              spriteHitLabel={spriteHit ? propFrameLabel(
                spriteHit.slot, spriteHit.sub, propSlots.get(spriteHit.slot),
                renderer?.getSlotInfo(spriteHit.slot).filename,
              ) : null}
              verdictTiles={lineDrag ? lineVerdicts : verdicts}
              selectionHits={selection}
              queuedTiles={queuedTiles}
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
                // Erase / Height / Room payloads: plain radius footprint
                // (no brush/stamp logic — the pencil just applies the op).
                if (tool === "pencil" && payload !== "tiles") {
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
              appendix={appendix}
              showItems={showItems}
              showEntries={showEntries}
              showExits={showExits}
              showSoldiers={showSoldiers}
              showLights={showLights}
              showDoors={showDoors}
              showEdges={showEdges}
              showSchedules={showSchedules}
              spriteCache={spriteCache}
              itemCache={itemCache}
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
            {/* Placement queue overlay — every queued
                ghost redrawn at its own anchor; unlike the single ghost
                above this spans the whole canvas (no CSS transform, it
                positions each entry itself). */}
            <canvas
              ref={queueCanvasRef}
              className="pointer-events-none absolute left-0 top-0 z-20"
              style={{ imageRendering: "pixelated", display: "none" }}
            />
            {/* Brush hover ghost (R3) — the armed brush sprite at the
                cursor; same z / pointer rules as the placement ghost. */}
            <canvas
              ref={brushGhostCanvasRef}
              className="pointer-events-none absolute left-0 top-0 z-20"
              style={{ imageRendering: "pixelated", display: "none" }}
            />
            {/* Grab-and-move ghost — the grabbed sprite at the cursor
                while a Shift+click move is in flight; same z / pointer
                rules as the brush hover ghost. */}
            <canvas
              ref={moveGhostCanvasRef}
              className="pointer-events-none absolute left-0 top-0 z-20"
              style={{ imageRendering: "pixelated", display: "none" }}
            />
            {/* Sprite-selection outline — the selected
                sprites re-drawn full-alpha with a CSS glow filter. */}
            <canvas
              ref={selectionCanvasRef}
              className="pointer-events-none absolute left-0 top-0 z-20"
              style={{
                imageRendering: "pixelated",
                display: "none",
                filter: "drop-shadow(0 0 1px rgb(80,255,120)) drop-shadow(0 0 1px rgb(80,255,120))",
              }}
            />
          </div>
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
          {modeless && (placingBuilding || selection.length > 0) && (
            <span className="ml-2 text-emerald-300" data-status="placement">
              · {oracleOffline && "oracle offline · "}
              {placingBuilding
                ? (lineDrag
                    ? (!lineDrag.hasTable
                        ? "no fence topology table for this slot — straight subs"
                        : worstOf(lineVerdicts) === "blocking"
                          ? `✕ ${lineVerdicts.find((t) => t.tier === "blocking")?.detail ?? lineVerdicts.find((t) => t.tier === "blocking")?.test ?? "blocked"}`
                          : `${lineDrag.lineTiles.length}-tile fence line — release to place`)
                    : queue.length > 0
                      ? `${queue.length} queued · click = build all · Esc = drop`
                      : (worstOf(verdicts) === "blocking"
                          ? `✕ ${verdicts.find((t) => t.tier === "blocking")?.detail ?? verdicts.find((t) => t.tier === "blocking")?.test ?? "blocked"}`
                          : worstOf(verdicts) === "advisory"
                            ? `⚠ ${verdicts.find((t) => t.tier === "advisory")?.detail ?? verdicts.find((t) => t.tier === "advisory")?.test ?? "advisory"}`
                            : `✓ place · Shift = queue · Esc/RMB = cancel${
                                hovered && oraclePendingAnchor
                                  && oraclePendingAnchor.x === hovered.x && oraclePendingAnchor.y === hovered.y
                                  && worstOf(localVerdicts) === "ok"
                                  ? " …" : ""
                              }`))
                : `${selection.length} selected · C copy · X cut · Del · ←↑→↓ nudge · R cycle`}
            </span>
          )}
          {showGrid && !showGridForThisView && (
            <span className="ml-2 text-amber-400">
              · grid hidden ({"too many tiles for this view"})
            </span>
          )}
        </span>
        <span className="text-gray-500">
          Zoom {zoom.toFixed(2)}× · {bindingFor(settings, "wheel-cycle-tool") || "—"} = tool · {bindingFor(settings, "wheel-zoom") || "—"} = zoom · Alt+drag or middle-drag = pan · Shift+drag sprite = move
        </span>
      </div>
      {/* Command card — mode-less only; hidden in legacy since
          its verbs (payload/shape arms, groups) rebind the same actions
          the SelectOptions strip + tool bar already cover there. */}
      {modeless && cardCells.length > 0 && (
        <div className="absolute bottom-2 right-2 z-30">
          <CommandCard cells={cardCells} />
        </div>
      )}
    </div>
  );

  const renderInspectorPanel = () => (
    <TileInspectorPanel
      xmlPath={xmlPath}
      tileset={tileset}
      session={session}
      renderer={renderer}
      propSlots={propSlots}
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

  const dockPanels = {
    canvas: renderCanvasPanel,
    assets: renderBrushBoxPanel,
    tilesetViewer: renderTilesetViewerPanel,
    inspector: renderInspectorPanel,
    history: () => (
      <HistoryPanel
        undoLabels={renderer?.listUndoLabels() ?? []}
        redoLabels={renderer?.listRedoLabels() ?? []}
        busy={editsInFlight > 0}
        onRevert={(n) => void revertUndo(n)}
        onRedo={(n) => void redoForward(n)}
      />
    ),
    minimap: () => (
      <MinimapPanel
        renderer={renderer}
        renderMeta={renderMeta}
        renderEpoch={renderEpoch}
        zoom={zoom}
        pan={pan}
        viewportW={canvasViewportSize.w}
        viewportH={canvasViewportSize.h}
        onRecenter={(tileX, tileY) => {
          if (!renderMeta) return;
          const p = tileToCanvasPixel(tileX, tileY, renderMeta);
          const cx = p.x + renderMeta.tileW / 2;
          const cy = p.y + renderMeta.tileH / 2;
          setPan({
            x: (renderMeta.canvasW / 2 - cx) * zoom,
            y: (renderMeta.canvasH / 2 - cy) * zoom,
          });
        }}
      />
    ),
    log: renderLogPanel,
    validate: () => (datPath ? (
      <MapForgeValidateBody
        datPath={datPath}
        xmlPath={xmlPath}
        tileset={tileset}
        sessionId={session?.session_id ?? null}
        onFindingTiles={showFindingTiles}
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
        pickPoint={pickPointForPanel}
        cancelRegionPick={cancelRegionPick}
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
              onClick={async (e) => {
                e.preventDefault();
                if (localDirty) {
                  const ok = await confirm({
                    title: "Discard unsaved edits?",
                    body: "You have unsaved edits in this sector. Leave anyway "
                      + "and discard them? Save first if you want to keep them.",
                    confirmLabel: "Discard and leave",
                    destructive: true,
                  });
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
              Focus. The Save button keeps emerald while dirty; the
              "● N unsaved" badge is subtle amber (distinct from the
              save-affordance accent — it's a STATUS, not an action);
              everything else is neutral — groups read via the thin
              dividers, not hue. */}
          <div className="mb-1 flex flex-wrap items-center gap-2">
            <a
              href="/mapforge"
              className="text-sm text-blue-400 hover:underline"
              onClick={async (e) => {
                e.preventDefault();
                if (localDirty) {
                  const ok = await confirm({
                    title: "Discard unsaved edits?",
                    body: "You have unsaved edits in this sector. Leave anyway "
                      + "and discard them? Save first if you want to keep them.",
                    confirmLabel: "Discard and leave",
                    destructive: true,
                  });
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
                className="flex items-center gap-1 text-xs leading-none text-amber-400"
                title={
                  "You have unsaved edits — they live in memory (and survive "
                  + "a reload) until you Save them to disk."
                }
              >
                <span aria-hidden="true">●</span>
                {session && session.edit_count > 0 && (
                  <span className="font-medium tabular-nums">
                    {session.edit_count} unsaved
                  </span>
                )}
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
            {/* File menu — create a fresh empty sector, or snapshot the
                current state to a new .dat without touching the original.
                Both prompt for a path + (new sector) tileset, refuse to
                clobber without confirmation, and write through the sidecar
                (atomic tmp+replace). */}
            <ToolbarMenu label="File">
              <ToolbarMenuItem
                title="Create a fresh empty 160×160 sector .dat (ground texture on every tile) and open it"
                onClick={() => void createNewSector()}
              >
                + New sector…
              </ToolbarMenuItem>
              <ToolbarMenuItem
                disabled={!session}
                title="Write the current map state to a NEW .dat (the original file is left untouched)"
                onClick={() => void saveCopyAsNow()}
              >
                Save a copy as…
              </ToolbarMenuItem>
            </ToolbarMenu>
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
            {!modeless && (
              <ToolSelector
                tool={tool} setTool={setTool}
                hasBrush={activeBrush !== null}
                payload={payload}
              />
            )}
            <PayloadSelector
              tool={tool}
              payload={payload}
              setPayload={setPayload}
              hasBrush={activeBrush !== null}
              modelessNoGhost={modeless && !placingBuilding}
            />
            <BrushChip
              brush={activeBrush}
              renderer={renderer}
              onClear={() => {
                setActiveBrush(null);
                log?.append({ severity: "info", message: "Brush cleared." });
              }}
              ghostLabel={placingBuilding?.group ? placingBuilding.label : null}
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
            />
            {(!modeless || selectRect !== null) && (
              <SelectOptions
                tool={tool}
                hasSelection={selectRect !== null}
                clipboard={clipboard}
                pasteMode={pasteMode}
                readOnly={session?.read_only ?? false}
                activeTileset={tileset}
                busy={editsInFlight > 0}
                onCopy={() => void doCopy()}
                onCut={() => void doCut()}
                onDelete={() => void doDeleteSelection()}
                onMove={() => void doMove()}
                onArmPaste={() => setPasteMode(true)}
                onCancelPaste={() => setPasteMode(false)}
              />
            )}
            <HeightOptions
              payload={payload}
              heightMode={heightMode}
              setHeightMode={setHeightMode}
              heightValue={heightValue}
              setHeightValue={setHeightValue}
            />
            <RoomOptions
              payload={payload}
              roomId={roomId}
              setRoomId={setRoomId}
              rooms={info.data?.rooms ?? []}
              suggestedRoomId={
                Math.max(
                  info.data?.rooms.reduce((m, r) => Math.max(m, r.room_id), 0) ?? 0,
                  maxPaintedRoomId,
                ) + 1
              }
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
              {/* Tactical appendix overlay toggles */}
              <label className="flex items-center gap-1 text-xs text-gray-300">
                <input type="checkbox" checked={showItems} onChange={(e) => setShowItems(e.target.checked)} />
                Items{appendix ? ` (${appendix.items.length})` : ""}
              </label>
              <label className="flex items-center gap-1 text-xs text-gray-300">
                <input type="checkbox" checked={showEntries} onChange={(e) => setShowEntries(e.target.checked)} />
                Entries{appendix ? ` (${appendix.entry_points.length})` : ""}
              </label>
              <label className="flex items-center gap-1 text-xs text-gray-300">
                <input type="checkbox" checked={showExits} onChange={(e) => setShowExits(e.target.checked)} />
                Exits{appendix ? ` (${appendix.exit_grids.length})` : ""}
              </label>
              <label className="flex items-center gap-1 text-xs text-gray-300">
                <input type="checkbox" checked={showSoldiers} onChange={(e) => setShowSoldiers(e.target.checked)} />
                NPCs{appendix ? ` (${appendix.soldiers.length})` : ""}
              </label>
              <label className="flex items-center gap-1 text-xs text-gray-300">
                <input type="checkbox" checked={showLights} onChange={(e) => setShowLights(e.target.checked)} />
                Lights{appendix ? ` (${appendix.lights.length})` : ""}
              </label>
              <label className="flex items-center gap-1 text-xs text-gray-300">
                <input type="checkbox" checked={showDoors} onChange={(e) => setShowDoors(e.target.checked)} />
                Doors{appendix ? ` (${appendix.doors.length})` : ""}
              </label>
              <label className="flex items-center gap-1 text-xs text-gray-300">
                <input type="checkbox" checked={showEdges} onChange={(e) => setShowEdges(e.target.checked)} />
                Edges{appendix ? ` (${appendix.edgepoints.length})` : ""}
              </label>
              <label className="flex items-center gap-1 text-xs text-gray-300">
                <input type="checkbox" checked={showSchedules} onChange={(e) => setShowSchedules(e.target.checked)} />
                Schedules{appendix ? ` (${appendix.schedules.length})` : ""}
              </label>
              {appendix?.blocked_at && (
                <span className="text-xs text-amber-400">layer &ldquo;{appendix.blocked_at}&rdquo; not yet shown</span>
              )}
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

      {/* Crash-recovery offer — an autosave snapshot from a previous
          sidecar process exists for this map. Restore swaps it into the
          session (nothing hits disk until the user saves); discard
          deletes the snapshot. */}
      <ConfirmModal
        open={recoveryOffer !== null}
        title="Recover unsaved edits?"
        body={
          <>
            A previous editing session ended without saving —
            an automatic recovery snapshot with{" "}
            <strong>
              {recoveryOffer?.edit_count ?? 0} edit
              {(recoveryOffer?.edit_count ?? 0) === 1 ? "" : "s"}
            </strong>{" "}
            was kept
            {recoveryOffer
              ? ` (${new Date(recoveryOffer.saved_at * 1000).toLocaleString()})`
              : ""}.
            <br /><br />
            Restore loads those edits into this session; the file on disk
            stays untouched until you save. Discard deletes the snapshot.
          </>
        }
        confirmLabel="Restore edits"
        cancelLabel="Discard snapshot"
        onCancel={() => {
          setRecoveryOffer(null);
          if (session) {
            sessionRecovery(session.session_id, "discard").catch(() => {});
          }
        }}
        onConfirm={async () => {
          setRecoveryOffer(null);
          if (!session) return;
          try {
            const info = await sessionRecovery(session.session_id, "restore");
            setSession(info);
            // Full canvas resync — same kitchen-sink path as a
            // generator run: server-side parsed changed wholesale.
            if (renderer) {
              const parsed = await getSessionParsed(info.session_id);
              renderer.setParsed(parsed);
              setRenderEpoch((e) => e + 1);
              bumpHistory();
            }
            log?.append({
              severity: "success",
              message: `Restored ${info.edit_count} autosaved edit`
                + `${info.edit_count === 1 ? "" : "s"} from the recovery `
                + "snapshot.",
              detail: "The session is now dirty — save to write the "
                + "recovered state to disk.",
            });
          } catch (e) {
            log?.append({
              severity: "error",
              message: "Recovery restore failed",
              detail: e instanceof Error ? e.message : String(e),
            });
          }
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
  spriteHit,
  spriteHitLabel,
  verdictTiles,
  selectionHits,
  queuedTiles,
  previewTiles,
  showGrid,
  showRoomLabels,
  debugClick,
  stampPreview,
  brushRadiusPreview,
  heightOverlay,
  appendix,
  showItems,
  showEntries,
  showExits,
  showSoldiers,
  showLights,
  showDoors,
  showEdges,
  showSchedules,
  spriteCache,
  itemCache,
}: {
  meta: RenderMeta;
  info: SectorInfo | undefined;
  selectedRoom: number | null;
  hovered: { x: number; y: number } | null;
  pinned: { x: number; y: number } | null;
  /** Sprite under the cursor (inspect tool): outline its full drawn
   * rect + highlight the OWNING anchor tile, so multi-tile-looking
   * objects (cooling towers etc.) are selectable at a glance. */
  spriteHit: SpriteHit | null;
  spriteHitLabel: PropFrameLabel | null;
  /** Per-footprint-tile local+oracle merged verdicts for the armed
   * placement ghost — colours the ghost's footprint
   * green/yellow/red. Empty when nothing is armed. */
  verdictTiles: TileVerdict[];
  /** The current sprite selection — outlined as footprint
   * diamonds, tinted green. Empty when nothing is selected. */
  selectionHits: SpriteRef[];
  /** Full footprint tiles of every placement-queue entry
   * — drawn as dashed-stroke diamonds (the queued ghosts themselves
   * are drawn by the separate `queueCanvasRef` overlay canvas). Empty
   * when the queue is empty. */
  queuedTiles: Array<{ x: number; y: number }>;
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
  /** Read-only tactical appendix (items / entry points / exit grids / soldiers / lights). */
  appendix: AppendixEntities | null;
  showItems: boolean;
  showEntries: boolean;
  showExits: boolean;
  showSoldiers: boolean;
  showLights: boolean;
  showDoors: boolean;
  showEdges: boolean;
  showSchedules: boolean;
  spriteCache: Map<string, { url: string; w: number; h: number } | null>;
  itemCache: Map<string, { url: string; w: number; h: number } | null>;
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

  // Placement-ghost validity tint: one filled path per
  // tier, keyed by tile so a footprint of N tiles draws N diamonds each
  // coloured by ITS OWN verdict (a truck's front bumper can be red while
  // its cab is green).
  const verdictPaths = useMemo(() => {
    const by: Record<"ok" | "advisory" | "blocking", string[]> = { ok: [], advisory: [], blocking: [] };
    for (const v of verdictTiles) {
      const c = tileDiamondCorners(v.x, v.y, meta);
      const d = `M${c[0][0]} ${c[0][1]}L${c[1][0]} ${c[1][1]}L${c[2][0]} ${c[2][1]}L${c[3][0]} ${c[3][1]}Z`;
      by[v.tier === "blocking" ? "blocking" : v.tier === "advisory" ? "advisory" : "ok"].push(d);
    }
    return by;
  }, [verdictTiles, meta]);
  // Sprite-selection outline diamonds.
  const selectionPath = useMemo(() => selectionHits.map((h) => {
    const c = tileDiamondCorners(h.x, h.y, meta);
    return `M${c[0][0]} ${c[0][1]}L${c[1][0]} ${c[1][1]}L${c[2][0]} ${c[2][1]}L${c[3][0]} ${c[3][1]}Z`;
  }).join(""), [selectionHits, meta]);
  // Placement-queue footprint diamonds — dashed
  // stroke, no fill (the queued sprites themselves are already drawn by
  // the queue overlay canvas; this is just their footprint outline).
  const queuedPath = useMemo(() => queuedTiles.map((t) => {
    const c = tileDiamondCorners(t.x, t.y, meta);
    return `M${c[0][0]} ${c[0][1]}L${c[1][0]} ${c[1][1]}L${c[2][0]} ${c[2][1]}L${c[3][0]} ${c[3][1]}Z`;
  }).join(""), [queuedTiles, meta]);

  // Playable-area outline — the iso "playable diamond" the engine renders
  // inside the 160×160 square (everything outside is off-map border). The
  // four extreme tiles of the inscribed diamond project to the four
  // corners of an axis-aligned rectangle in canvas space; we trace it so
  // map-edge work (cliffs / escarpments especially) has a visible
  // boundary instead of guessing where the battlefield ends. Geometry
  // mirrors _make_playable_predicate / _PLAYABLE_BORDER=10 in the sidecar
  // (generators.py).
  const playablePath = useMemo(() => {
    if (!info) return null;
    const cx = info.cols / 2;
    const cy = info.rows / 2;
    const r = Math.min(cx, cy) - 10;
    if (r <= 0) return null;
    const pts = ([[cx - r, cy], [cx, cy - r], [cx + r, cy], [cx, cy + r]] as const)
      .map(([tx, ty]) => {
        const p = tileToCanvasPixel(tx, ty, meta);
        return [p.x + meta.tileW / 2, p.y + meta.tileH / 2] as const;
      });
    return pts.map((p, i) => `${i === 0 ? "M" : "L"}${p[0]} ${p[1]}`).join("") + "Z";
  }, [info, meta]);

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

      {/* Playable-area boundary — the engine's iso battlefield rectangle.
          Dashed gold so it reads as a boundary marker, not a tile edge. */}
      {playablePath && (
        <path
          d={playablePath}
          stroke="rgba(255,205,80,0.85)"
          fill="none"
          strokeWidth={2}
          strokeDasharray="7 5"
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

      {/* Placement-ghost validity tint — green/yellow/red
          per footprint tile, keyed by the local (+ later, oracle) check.
          data-verdict is what the Playwright agenda reads. */}
      {verdictPaths.ok.length > 0 && (
        <path d={verdictPaths.ok.join("")} fill="rgba(80,255,120,0.35)" stroke="rgb(80,255,120)" strokeWidth={1} vectorEffect="non-scaling-stroke" data-verdict="ok" />
      )}
      {verdictPaths.advisory.length > 0 && (
        <path d={verdictPaths.advisory.join("")} fill="rgba(255,220,80,0.4)" stroke="rgb(255,220,80)" strokeWidth={1} vectorEffect="non-scaling-stroke" data-verdict="advisory" />
      )}
      {verdictPaths.blocking.length > 0 && (
        <path d={verdictPaths.blocking.join("")} fill="rgba(255,70,70,0.45)" stroke="rgb(255,70,70)" strokeWidth={1.5} vectorEffect="non-scaling-stroke" data-verdict="blocking" />
      )}
      {/* Sprite-selection footprint diamonds. */}
      {selectionPath && (
        <path d={selectionPath} fill="rgba(80,255,120,0.18)" stroke="rgb(80,255,120)" strokeWidth={1} vectorEffect="non-scaling-stroke" data-selection="tiles" />
      )}

      {/* Placement-queue footprint diamonds — dashed
          outline only; the queued ghosts' sprites are the queue canvas. */}
      {queuedPath && (
        <path d={queuedPath} fill="none" stroke="rgb(120,180,255)" strokeWidth={1} strokeDasharray="4 3" vectorEffect="non-scaling-stroke" data-queued="tiles" />
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
      {/* Tactical appendix markers — items / entry points / exit grids / soldiers / lights.
          Read-only overlay fetched once per session. tileToCanvasPixel
          returns the tile's top-left; half-tile offset centres the marker. */}
      {appendix && (() => {
        const c = (x: number, y: number) => {
          const p = tileToCanvasPixel(x, y, meta);
          return { cx: p.x + meta.tileW / 2, cy: p.y + meta.tileH / 2 };
        };
        return (
          <g>
            {showExits && appendix.exit_grids.map((e, i) => (
              <g key={`xg-${i}`}>
                <TileMarker tile={{ x: e.x, y: e.y }} meta={meta}
                  fill="rgba(120,200,255,0.28)" stroke="rgba(150,220,255,0.85)" strokeWidth={1} />
                <title>{`exit → sector (dest ${e.dest_gridno})`}</title>
              </g>
            ))}
            {showEntries && appendix.entry_points.map((e, i) => {
              const { cx, cy } = c(e.x, e.y);
              return <circle key={`ep-${i}`} cx={cx} cy={cy} r={5}
                fill="rgba(255,205,80,0.45)" stroke="rgba(255,225,120,0.95)"
                strokeWidth={1.5} vectorEffect="non-scaling-stroke" />;
            })}
            {showItems && appendix.items.map((it, i) => {
              const { cx, cy } = c(it.x, it.y);
              const g = itemCache.get(String(it.usItem));
              if (g) {
                const maxW = meta.tileW * 1.5;
                const scale = Math.min(1, maxW / g.w);
                const w = g.w * scale, h = g.h * scale;
                return <image key={`it-${i}`} href={g.url} width={w} height={h}
                  x={cx - w / 2} y={cy - h / 2} style={{ imageRendering: "pixelated" }}>
                  <title>{`item ${it.usItem}`}</title>
                </image>;
              }
              return <circle key={`it-${i}`} cx={cx} cy={cy} r={3}
                fill="rgba(120,255,160,0.9)" stroke="rgba(40,160,80,0.9)" strokeWidth={1}
                vectorEffect="non-scaling-stroke"><title>{`item ${it.usItem}`}</title></circle>;
            })}
            {showSoldiers && appendix.soldiers.map((s, i) => {
              const { cx, cy } = c(s.x, s.y);
              const sprite = spriteCache.get(`${s.body_type}-${s.facing}`);
              if (sprite) {
                return <image key={`sol-${i}`} href={sprite.url} width={sprite.w} height={sprite.h}
                  x={cx - sprite.w / 2} y={cy - sprite.h + meta.tileH / 2}
                  style={{ imageRendering: "pixelated" }}>
                  <title>{`${s.team_label} (body ${s.body_type}, dir ${s.facing})`}</title>
                </image>;
              }
              const color = s.team === 1 ? "rgba(255,80,80,0.95)"      // enemy
                : s.team === 2 ? "rgba(120,255,120,0.95)"              // creature
                : s.team === 3 ? "rgba(80,220,255,0.95)"               // militia
                : s.team === 4 ? "rgba(120,160,255,0.95)"              // civilian
                : "rgba(240,240,240,0.95)";                            // player/other
              return (
                <circle key={`sol-${i}`} cx={cx} cy={cy} r={4}
                  fill={color} stroke="rgba(0,0,0,0.7)" strokeWidth={1}
                  vectorEffect="non-scaling-stroke">
                  <title>{`${s.team_label} (body ${s.body_type}, dir ${s.facing})`}</title>
                </circle>
              );
            })}
            {showLights && appendix.lights.map((l, i) => {
              const { cx, cy } = c(l.x, l.y);
              return (
                <circle key={`lt-${i}`} cx={cx} cy={cy} r={3}
                  fill="rgba(255,220,90,0.9)" stroke="rgba(140,110,0,0.8)"
                  strokeWidth={1} vectorEffect="non-scaling-stroke">
                  <title>{l.template}</title>
                </circle>
              );
            })}
            {showDoors && appendix.doors.map((d, i) => {
              const { cx, cy } = c(d.x, d.y);
              const col = d.locked ? "rgba(255,205,80,0.95)" : "rgba(185,185,185,0.85)";
              return (
                <g key={`dr-${i}`} transform={`translate(${cx - 4} ${cy - 5})`}>
                  <path d="M2 4 V2.6 a2 2 0 0 1 4 0 V4" fill="none" stroke={col} strokeWidth={1}
                    vectorEffect="non-scaling-stroke" />
                  <rect x={0} y={4} width={8} height={6} rx={1} fill={col} stroke="rgba(0,0,0,0.7)"
                    strokeWidth={0.5} vectorEffect="non-scaling-stroke" />
                  <title>{d.locked ? "locked door" : "door"}</title>
                </g>
              );
            })}
            {showEdges && appendix.edgepoints.map((e, i) => (
              <g key={`eg-${i}`}>
                <TileMarker tile={{ x: e.x, y: e.y }} meta={meta}
                  fill="rgba(160,160,160,0.20)" stroke="rgba(120,120,120,0.5)" strokeWidth={0.75} />
                <title>{`edge: ${e.edge}`}</title>
              </g>
            ))}
            {showSchedules && appendix.schedules.map((s, i) => {
              const { cx, cy } = c(s.x, s.y);
              return <rect key={`sc-${i}`} x={cx - 3} y={cy - 3} width={6} height={6}
                transform={`rotate(45 ${cx} ${cy})`}
                fill="rgba(255,160,60,0.85)" stroke="rgba(160,90,0,0.9)" strokeWidth={1}
                vectorEffect="non-scaling-stroke">
                <title>{`schedule #${s.schedule_id} (action ${s.action})`}</title>
              </rect>;
            })}
          </g>
        );
      })()}
      {hovered && (
        <TileMarker tile={hovered} meta={meta}
          fill="rgba(120,220,255,0.22)" stroke="rgba(120,220,255,0.85)" />
      )}
      {/* Sprite-aware hover: outline the full drawn sprite under the
          cursor and mark its OWNING anchor tile in green — clicking
          anywhere inside the outline pins that anchor. This is the
          "which square is the cooling tower" affordance. */}
      {spriteHit && (
        <g>
          <rect
            x={spriteHit.rect.x} y={spriteHit.rect.y}
            width={spriteHit.rect.w} height={spriteHit.rect.h}
            fill="rgba(140,255,160,0.05)"
            stroke="rgba(140,255,160,0.95)"
            strokeWidth={2}
            strokeDasharray="6 3"
            vectorEffect="non-scaling-stroke"
          />
          <TileMarker tile={{ x: spriteHit.x, y: spriteHit.y }} meta={meta}
            fill="rgba(140,255,160,0.35)" stroke="rgba(140,255,160,1)"
            strokeWidth={2} />
          <text
            x={spriteHit.rect.x + 4}
            y={Math.max(24, spriteHit.rect.y - 18)}
            fill="rgba(140,255,160,0.95)"
            fontSize={12}
            style={{ paintOrder: "stroke", stroke: "rgba(0,0,0,0.75)", strokeWidth: 3 }}
          >
            <title>{`${spriteHitLabel?.primary ?? "Sprite"}\n${spriteHitLabel?.filename ?? "Unknown file"}`}</title>
            <tspan x={spriteHit.rect.x + 4}>{spriteHitLabel?.primary ?? "Sprite"}</tspan>
            <tspan x={spriteHit.rect.x + 4} dy={13} fontSize={10}>
              {`s${spriteHit.slot}.${spriteHit.sub} @ (${spriteHit.x},${spriteHit.y})`}
            </tspan>
          </text>
        </g>
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

// ─── R6 minimap navigator ─────────────────────────────────────────────
/** In-editor overview navigator (NOT the in-game radar STI). Draws a
 * cheap FLAT top-down map of the whole sector — one small cell per tile,
 * colored by its top occupied layer (roof → struct → land) — and overlays
 * the current viewport as a draggable quadrilateral. Click or drag the
 * overview to re-center the main canvas on that tile.
 *
 * Cheap by design: a single fillRect-per-tile pass on a small <canvas>,
 * re-run only on `renderEpoch` (one committed stroke). It deliberately
 * does NOT reuse the iso renderer — a flat colored grid is plenty for
 * navigation and stays fast even on a 160×160 sector.
 */
function MinimapPanel({
  renderer, renderMeta, renderEpoch, zoom, pan,
  viewportW, viewportH, onRecenter,
}: {
  renderer: IsoRenderer | null;
  renderMeta: RenderMeta | null;
  renderEpoch: number;
  zoom: number;
  pan: { x: number; y: number };
  viewportW: number;
  viewportH: number;
  onRecenter: (tileX: number, tileY: number) => void;
}) {
  const mapCanvasRef = useRef<HTMLCanvasElement | null>(null);
  // Box the overview into a fixed-width column; the panel scrolls if the
  // dock column is narrower. CELL px-per-tile is clamped so a 160-wide
  // sector lands around ~320px (2px/tile) but tiny sectors still read.
  const [dims, setDims] = useState<{ cols: number; rows: number; cell: number } | null>(null);
  const draggingRef = useRef(false);

  // (Re)paint the flat overview whenever the sector geometry / content
  // changes (renderEpoch) or the renderer swaps.
  useEffect(() => {
    const cv = mapCanvasRef.current;
    if (!cv || !renderer) {
      setDims(null);
      return;
    }
    const parsed = renderer.getParsed();
    const cols = parsed.cols;
    const rows = parsed.rows;
    if (cols <= 0 || rows <= 0) {
      setDims(null);
      return;
    }
    // Target ~320px on the longer axis, clamped to 1..3 px/tile, integer.
    const cell = Math.max(1, Math.min(3, Math.round(320 / Math.max(cols, rows))));
    const w = cols * cell;
    const h = rows * cell;
    cv.width = w;
    cv.height = h;
    const ctx = cv.getContext("2d");
    if (!ctx) return;
    // Background (matches the iso render's tan-ish void, darkened).
    ctx.fillStyle = "#1c1814";
    ctx.fillRect(0, 0, w, h);
    const land = parsed.land;
    const structs = parsed.structs;
    const roofs = parsed.roofs;
    const objs = parsed.objs;
    for (let y = 0; y < rows; y++) {
      for (let x = 0; x < cols; x++) {
        const gn = y * cols + x;
        let color: string | null = null;
        // Layer priority for the flat overview, top-down: a roof hides
        // everything below it; else a structure; else an object accent;
        // else the floor. Hue is derived from the top slot so distinct
        // terrains read differently without a palette lookup.
        const roofCell = roofs[gn];
        const structCell = structs[gn];
        const objCell = objs[gn];
        const landCell = land[gn];
        if (roofCell && roofCell.length > 0) {
          color = "#6b5847"; // roofs — muted brown
        } else if (structCell && structCell.length > 0) {
          const slot = structCell[0]?.[0] ?? 0;
          // Greys with a slot-driven tint so walls/furniture vary.
          const v = 90 + ((slot * 37) % 90);
          color = `rgb(${v},${Math.max(60, v - 25)},${Math.max(55, v - 35)})`;
        } else if (landCell && landCell.length > 0) {
          const slot = landCell[landCell.length - 1]?.[0] ?? 0;
          // Earthy greens/tans driven by the top land slot.
          const hue = (slot * 53) % 70 + 30; // 30..100 (greens→yellow)
          color = `hsl(${hue}, 32%, 34%)`;
          if (objCell && objCell.length > 0) {
            // Object accent — slightly lighter dot over the floor.
            color = `hsl(${hue}, 40%, 46%)`;
          }
        }
        if (color) {
          ctx.fillStyle = color;
          ctx.fillRect(x * cell, y * cell, cell, cell);
        }
      }
    }
    setDims({ cols, rows, cell });
  }, [renderer, renderEpoch]);

  // Viewport quadrilateral on the overview. The main canvas wrapper maps
  // canvas-px point p to screen = containerCenter + pan + (p − canvasCenter)·zoom,
  // so the visible canvas-px box is centered at (canvasCenter − pan/zoom)
  // with half-extents (viewport/2)/zoom. We project the four screen-rect
  // corners back to TILE coords (iso inverse) — an axis-aligned screen
  // rect becomes a diamond on the flat grid — then to overview pixels.
  const viewportPoly = (() => {
    if (!dims || !renderMeta || zoom <= 0 || viewportW <= 0 || viewportH <= 0) {
      return null;
    }
    const { cell } = dims;
    const ccx = renderMeta.canvasW / 2;
    const ccy = renderMeta.canvasH / 2;
    const vcx = ccx - pan.x / zoom;
    const vcy = ccy - pan.y / zoom;
    const halfW = viewportW / 2 / zoom;
    const halfH = viewportH / 2 / zoom;
    const cornersPx: Array<[number, number]> = [
      [vcx - halfW, vcy - halfH],
      [vcx + halfW, vcy - halfH],
      [vcx + halfW, vcy + halfH],
      [vcx - halfW, vcy + halfH],
    ];
    // CONTINUOUS iso inverse (mirror of imagePixelToTile without its
    // round + on-grid null-guard) so corners stay fractional and can hang
    // off the sector edge — otherwise an off-edge corner collapses to 0.
    const hw = renderMeta.tileW / 2;
    const hh = renderMeta.tileH / 2;
    const pts = cornersPx.map(([px, py]) => {
      const A = (px + renderMeta.ixMin) / hw;
      const B = (py + renderMeta.iyMin) / hh;
      const tx = (A + B) / 2 - 1;
      const ty = (B - A) / 2;
      return { x: (tx + 0.5) * cell, y: (ty + 0.5) * cell };
    });
    return pts;
  })();

  const recenterFromEvent = (e: React.PointerEvent<HTMLCanvasElement>) => {
    const cv = mapCanvasRef.current;
    if (!cv || !dims) return;
    const rect = cv.getBoundingClientRect();
    // The canvas may be scaled by CSS (object-fit / max-width); map client
    // px → canvas px → tile via the cell size.
    const scaleX = cv.width / rect.width;
    const scaleY = cv.height / rect.height;
    const cx = (e.clientX - rect.left) * scaleX;
    const cy = (e.clientY - rect.top) * scaleY;
    const tx = Math.max(0, Math.min(dims.cols - 1, Math.floor(cx / dims.cell)));
    const ty = Math.max(0, Math.min(dims.rows - 1, Math.floor(cy / dims.cell)));
    onRecenter(tx, ty);
  };

  if (!renderer || !renderMeta) {
    return (
      <div className="p-3 text-xs italic text-gray-500">
        No sector open. The minimap shows a downscaled overview of the
        whole sector — click or drag it to jump the main view.
      </div>
    );
  }

  return (
    <div className="flex flex-col gap-1.5 p-2">
      <div className="text-[10px] uppercase tracking-wider text-gray-400">
        Overview {dims ? `(${dims.cols}×${dims.rows})` : ""}
      </div>
      <div className="relative inline-block w-fit max-w-full">
        <canvas
          ref={mapCanvasRef}
          className="block max-w-full cursor-pointer select-none border border-gray-800 bg-gray-950"
          style={{ imageRendering: "pixelated", touchAction: "none" }}
          onPointerDown={(e) => {
            draggingRef.current = true;
            e.currentTarget.setPointerCapture(e.pointerId);
            recenterFromEvent(e);
          }}
          onPointerMove={(e) => {
            if (draggingRef.current) recenterFromEvent(e);
          }}
          onPointerUp={(e) => {
            draggingRef.current = false;
            try { e.currentTarget.releasePointerCapture(e.pointerId); } catch { /* noop */ }
          }}
          onPointerLeave={() => { draggingRef.current = false; }}
        />
        {/* Viewport rectangle overlay — an SVG sized to match the canvas
            (in canvas-px units) and CSS-scaled with it via 100% w/h. */}
        {viewportPoly && dims && (
          <svg
            className="pointer-events-none absolute left-0 top-0"
            width="100%"
            height="100%"
            viewBox={`0 0 ${dims.cols * dims.cell} ${dims.rows * dims.cell}`}
            preserveAspectRatio="none"
          >
            <polygon
              points={viewportPoly.map((p) => `${p.x},${p.y}`).join(" ")}
              fill="rgba(96,165,250,0.14)"
              stroke="rgba(147,197,253,0.95)"
              strokeWidth={Math.max(1, dims.cell)}
              strokeLinejoin="round"
            />
          </svg>
        )}
      </div>
      <div className="text-[10px] leading-tight text-gray-500">
        Click or drag to recenter the main view. Zoom {zoom.toFixed(2)}×.
      </div>
    </div>
  );
}

// Shape-kind segmented control options + per-kind tooltip.
/** R4 history panel: the undo + redo stacks as clickable stroke lists.
 * Click an entry to jump straight to that point (revert/redo N strokes).
 * Labels come from beginStroke() (e.g. "Paint floor (12 tiles)"). */
function HistoryPanel({
  undoLabels, redoLabels, busy, onRevert, onRedo,
}: {
  undoLabels: string[];
  redoLabels: string[];
  busy: boolean;
  onRevert: (n: number) => void;
  onRedo: (n: number) => void;
}) {
  if (undoLabels.length === 0 && redoLabels.length === 0) {
    return (
      <div className="p-3 text-xs italic text-gray-500">
        No edits yet. Paint something — your strokes appear here (newest
        first); click one to jump back to it.
      </div>
    );
  }
  return (
    <div className="space-y-2 p-2 text-xs">
      <div>
        <div className="mb-1 text-[10px] uppercase tracking-wider text-gray-400">
          Undo to ({undoLabels.length})
        </div>
        {undoLabels.length === 0 ? (
          <p className="text-[10px] italic text-gray-600">Nothing to undo.</p>
        ) : (
          <ul className="space-y-0.5">
            {undoLabels.map((lbl, i) => (
              <li key={`u${i}`}>
                <button
                  type="button"
                  disabled={busy}
                  onClick={() => onRevert(i + 1)}
                  title={`Undo ${i + 1} stroke${i === 0 ? "" : "s"} — back to before "${lbl}"`}
                  className="flex w-full items-center gap-1.5 rounded px-1.5 py-0.5 text-left hover:bg-gray-800 disabled:opacity-50"
                >
                  <span className="w-4 text-right font-mono text-[9px] text-gray-600">{i + 1}</span>
                  <span className="truncate text-gray-200">↶ {lbl}</span>
                </button>
              </li>
            ))}
          </ul>
        )}
      </div>
      {redoLabels.length > 0 && (
        <div>
          <div className="mb-1 text-[10px] uppercase tracking-wider text-gray-400">
            Redo to ({redoLabels.length})
          </div>
          <ul className="space-y-0.5">
            {redoLabels.map((lbl, i) => (
              <li key={`r${i}`}>
                <button
                  type="button"
                  disabled={busy}
                  onClick={() => onRedo(i + 1)}
                  title={`Redo ${i + 1} stroke${i === 0 ? "" : "s"} — forward through "${lbl}"`}
                  className="flex w-full items-center gap-1.5 rounded px-1.5 py-0.5 text-left hover:bg-gray-800 disabled:opacity-50"
                >
                  <span className="w-4 text-right font-mono text-[9px] text-gray-600">{i + 1}</span>
                  <span className="truncate text-blue-300">↷ {lbl}</span>
                </button>
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}

const SHAPE_KINDS: ReadonlyArray<{ kind: ShapeKind; label: string }> = [
  { kind: "rect-fill", label: "▦ Fill" },
  { kind: "rect-outline", label: "▢ Outline" },
  { kind: "flood", label: "🪣 Flood" },
  { kind: "line", label: "╱ Line" },
  { kind: "diamond", label: "◆ Diamond" },
  { kind: "cross", label: "✛ Cross" },
  { kind: "triangle", label: "▲ Triangle" },
  { kind: "hexagon", label: "⬡ Hex" },
];
const SHAPE_HINTS: Record<ShapeKind, string> = {
  "rect-fill": "Drag a box → fill the whole area with the active tile",
  "rect-outline": "Drag a box → paint just the perimeter (walls / fences)",
  "flood": "Click a tile → fill its connected same-tile area with the active brush",
  "line": "Drag A→B → a straight run of the active tile (roads, fences)",
  "diamond": "Drag a box → filled diamond inscribed in it",
  "cross": "Drag a box → a plus/cross through the center",
  "triangle": "Drag a box → filled triangle, apex at top",
  "hexagon": "Drag a box → filled flat-top hexagon",
};

/** R4 payload selector — what a pencil/shape stroke DOES (place the active
 * brush, erase the non-ground layers, set height, or write a room id).
 * Shown for the pencil + shape tools. */
function PayloadSelector({
  tool, payload, setPayload, hasBrush, modelessNoGhost,
}: {
  tool: Tool;
  payload: Payload;
  setPayload: (p: Payload) => void;
  hasBrush: boolean;
  /** `modeless && !placingBuilding` — a brush/payload already being
   * armed doesn't need this flag (`tool` is already "pencil"/"shape",
   * short-circuiting the check below); it only matters when `tool`
   * derives to "select" (nothing, or only a ghost, armed), where it
   * still shows this strip so Erase/Height/Room are reachable AT ALL —
   * they're the only way to ARM those payloads (which then flips the
   * derived tool to "pencil") until the command card (a later
   * phase) offers them as one-key verbs. False while a ghost is armed —
   * setting a payload there wouldn't do anything visible until the
   * ghost is placed/cancelled anyway. */
  modelessNoGhost: boolean;
}) {
  if (tool !== "pencil" && tool !== "shape" && !modelessNoGhost) return null;
  const items: Array<{ id: Payload; label: string; title: string }> = [
    { id: "tiles", label: "🖌 Tiles", title: "Place the active brush" + (hasBrush ? "" : " — pick a tile first") },
    { id: "erase", label: "🧽 Erase", title: "Clear objects / structures / roofs — keeps the floor" },
    { id: "height", label: "⛰ Height", title: "Raise / lower / set per-tile terrain height" },
    { id: "room", label: "⌂ Room", title: "Write a room id (engine hides the roof inside)" },
  ];
  const active: Record<Payload, string> = {
    tiles: "bg-emerald-900 text-emerald-100",
    erase: "bg-red-900 text-red-100",
    height: "bg-orange-900 text-orange-100",
    room: "bg-sky-900 text-sky-100",
  };
  return (
    <div>
      <span className="block text-xs text-gray-400">Payload</span>
      <div className="flex overflow-hidden rounded border border-gray-700">
        {items.map((it) => (
          <button
            key={it.id}
            type="button"
            onClick={() => setPayload(it.id)}
            title={it.title}
            className={`px-2 py-1 text-xs ${
              payload === it.id ? active[it.id] : "bg-gray-900 text-gray-300 hover:bg-gray-800"
            }`}
          >
            {it.label}
          </button>
        ))}
      </div>
    </div>
  );
}

/** Room-payload controls — the room id written by pencil/shape strokes
 * when the Room payload is active. */
function RoomOptions({
  payload, roomId, setRoomId, rooms, suggestedRoomId,
}: {
  payload: Payload;
  roomId: number;
  setRoomId: (n: number) => void;
  rooms: RoomSummary[];
  suggestedRoomId: number;
}) {
  if (payload !== "room") return null;
  return (
    <div>
      <span className="block text-xs text-gray-400">Room id</span>
      <select
        value={roomId}
        onChange={(e) => setRoomId(parseInt(e.target.value, 10) || 0)}
        className="rounded border border-gray-700 bg-gray-900 px-2 py-1 text-xs"
        title="Room id written by the stroke. New = a fresh id; Clear = mark as outdoors (room 0)."
      >
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
  );
}

function ToolSelector({
  tool, setTool, hasBrush, payload,
}: {
  tool: Tool;
  setTool: (t: Tool) => void;
  hasBrush: boolean;
  payload: Payload;
}) {
  // Minimal — just the Inspect/Pencil/Shape/Select toggle. Per-tool controls
  // (Paint to layer, Brush size) live in BrushOptions; shape controls in
  // ShapeOptions — so this component's width is constant across tools.
  const activeClass: Record<Tool, string> = {
    inspect: "bg-blue-900 text-blue-100",
    pencil: "bg-emerald-900 text-emerald-100",
    shape: "bg-teal-900 text-teal-100",
    select: "bg-purple-900 text-purple-100",
  };
  const toolLabel: Record<Tool, string> = {
    inspect: "⌖ Inspect", pencil: "✎ Pencil", shape: "▦ Shape", select: "⬚ Select",
  };
  return (
    <div>
      <span className="block text-xs text-gray-400">Tool</span>
      <div className="flex overflow-hidden rounded border border-gray-700">
        {(["inspect", "pencil", "shape", "select"] as const).map((t) => {
          // Pencil needs a brush only for the Tiles payload; Erase / Height /
          // Room work brush-free. Other tools never need one.
          const disabled = t === "pencil" && payload === "tiles" && !hasBrush;
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
                disabled
                  ? "Pick a tile from the palette first (or switch payload to Erase / Height / Room)"
                  : t === "pencil"
                    ? "Apply the payload over the brush radius (click or drag)"
                    : t === "shape"
                      ? "Drag to define a rectangle / line / flood region"
                      : t === "select"
                        ? "Drag a rectangle to copy a region; paste it elsewhere"
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

/** Shape-mode-only controls: which shape to draw + the Tiles-payload target
 * layer. (Room id / height options moved to the payload selectors.) Hidden
 * in other tools so the toolbar width stays stable. Mirrors BrushOptions. */
function ShapeOptions({
  tool, shapeKind, setShapeKind, activeBrush, hasBrush,
  paintLayer, setPaintLayer,
}: {
  tool: Tool;
  shapeKind: ShapeKind;
  setShapeKind: (k: ShapeKind) => void;
  activeBrush: ActiveBrush | null;
  hasBrush: boolean;
  paintLayer: LayerName | null;
  setPaintLayer: (l: LayerName | null) => void;
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

      <div>
        <span className="block text-xs text-gray-400">Paint to layer</span>
        <select
          value={paintLayer ?? ""}
          onChange={(e) =>
            setPaintLayer(e.target.value === "" ? null : e.target.value as LayerName)
          }
          className="rounded border border-gray-700 bg-gray-900 px-2 py-1 text-xs"
          title="Override which layer the shape paints into (Tiles payload). Default = picked from the brush's category."
        >
          <option value="">auto ({activeBrush?.layer ?? "—"})</option>
          {ALL_LAYERS.map((l) => (
            <option key={l} value={l}>{LAYER_SHORT[l]}</option>
          ))}
        </select>
      </div>

      {!hasBrush && (
        <p className="max-w-[12rem] self-center text-[10px] text-amber-400">
          Tiles payload: pick a tile to fill the shape with (Erase / Height /
          Room work without one).
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
 * only — a clipboard from a different tileset disables Paste
 * (cross-tileset slot remap is deferred). */
function SelectOptions({
  tool, hasSelection, clipboard, pasteMode, readOnly, activeTileset, busy,
  onCopy, onCut, onDelete, onMove, onArmPaste, onCancelPaste,
}: {
  tool: Tool;
  hasSelection: boolean;
  clipboard: ClipboardRegion | null;
  pasteMode: boolean;
  readOnly: boolean;
  activeTileset: number;
  busy: boolean;
  onCopy: () => void;
  onCut: () => void;
  onDelete: () => void;
  onMove: () => void;
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
          <button
            type="button"
            onClick={onCut}
            disabled={!hasSelection || readOnly || busy}
            className="border-l border-gray-700 bg-gray-900 px-2 py-1 text-xs text-gray-300 hover:bg-gray-800 disabled:cursor-not-allowed disabled:opacity-40"
            title={hasSelection
              ? "Cut the selection to the clipboard (copy, then clear)"
              : "Drag a rectangle on the map first"}
          >
            ✂ Cut
          </button>
          <button
            type="button"
            onClick={onDelete}
            disabled={!hasSelection || readOnly || busy}
            className="border-l border-gray-700 bg-gray-900 px-2 py-1 text-xs text-gray-300 hover:bg-gray-800 disabled:cursor-not-allowed disabled:opacity-40"
            title={hasSelection
              ? "Clear the selection — wipe all layers, rooms + heights (Delete / Backspace)"
              : "Drag a rectangle on the map first"}
          >
            🗑 Delete
          </button>
          {pasteMode ? (
            <button
              type="button"
              onClick={onCancelPaste}
              className="border-l border-gray-700 bg-amber-900 px-2 py-1 text-xs text-amber-100 hover:bg-amber-800"
              title="Cancel paste (Esc)"
            >
              ✕ Cancel
            </button>
          ) : (
            <button
              type="button"
              onClick={onArmPaste}
              disabled={!canPaste}
              className="border-l border-gray-700 bg-purple-900 px-2 py-1 text-xs text-purple-100 hover:bg-purple-800 disabled:cursor-not-allowed disabled:opacity-40"
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
          <button
            type="button"
            onClick={onMove}
            disabled={!hasSelection || readOnly || busy}
            className="border-l border-gray-700 bg-gray-900 px-2 py-1 text-xs text-gray-300 hover:bg-gray-800 disabled:cursor-not-allowed disabled:opacity-40"
            title={hasSelection
              ? "Move the selection: cuts it, then click the map to drop it at a new spot"
              : "Drag a rectangle on the map first"}
          >
            ✥ Move
          </button>
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
  payload, heightMode, setHeightMode, heightValue, setHeightValue,
}: {
  payload: Payload;
  heightMode: "raise" | "lower" | "set";
  setHeightMode: (m: "raise" | "lower" | "set") => void;
  heightValue: number;
  setHeightValue: (v: number) => void;
}) {
  if (payload !== "height") return null;
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
  brush, renderer, onClear, ghostLabel = null,
}: {
  brush: ActiveBrush | null;
  renderer: IsoRenderer | null;
  onClear: () => void;
  /** Fix-round-1b: mode-less sprite-group ghost has no `activeBrush`
   * (that's how the derived `tool` tells a ghost apart from a brush) —
   * without this the chip went blank while a ghost was armed. */
  ghostLabel?: string | null;
}) {
  if (!brush) {
    if (ghostLabel) {
      return (
        <div
          className="flex flex-col items-start gap-0.5"
          title={`Armed ghost: ${ghostLabel}\nClick the map to place it.`}
        >
          <span className="block text-xs text-gray-400">Brush</span>
          <div className="flex items-center gap-1.5 rounded border border-sky-700 bg-sky-950/40 px-2 py-1 text-[10px] text-sky-200">
            <span className="inline-flex h-7 w-7 items-center justify-center rounded bg-sky-900 text-sm">▦</span>
            <span className="truncate font-mono" style={{ maxWidth: "9rem" }}>{ghostLabel}</span>
          </div>
        </div>
      );
    }
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
          + `Switch sub-frame: , / . keys, or click a multi-frame tile in the Brush Box to pick a sub.`
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
// Shared save-with-external-retry — the single owner of the 409
// EXTERNAL_MODIFICATION contract (was copy-pasted verbatim between the
// save hotkey and the SaveButton). The sidecar 409s when the .dat
// changed on disk under the session; the first save surfaces that in
// the log and arms `overrideRef`, so the NEXT explicit save passes
// force=true and overwrites (the sidecar keeps a rolling backup of the
// external version). The ref resets on success so force never lingers.
async function saveSessionWithExternalRetry(
  sessionId: string,
  overrideRef: { current: boolean },
  log: ReturnType<typeof useMapForgeLog>,
): Promise<
  | { ok: true; res: Awaited<ReturnType<typeof saveSession>> }
  | { ok: false; message: string }
> {
  try {
    const res = await saveSession(sessionId, { force: overrideRef.current });
    overrideRef.current = false;
    log?.append({
      severity: "success",
      message: `Saved ${(res.bytes_written / 1024).toFixed(1)} KB to disk`,
      detail: res.backup_path ? `backup: ${res.backup_path}` : undefined,
    });
    return { ok: true, res };
  } catch (e) {
    const msg = e instanceof Error ? e.message : String(e);
    if (msg.includes("EXTERNAL_MODIFICATION")) {
      overrideRef.current = true;
      log?.append({
        severity: "error",
        message: "Save blocked — the .dat changed on disk since this "
          + "session opened it. Save again to overwrite the external "
          + "version (a rolling backup of it is kept), or reopen the "
          + "sector to load it and discard this session's edits.",
        detail: msg,
      });
    } else {
      log?.append({ severity: "error", message: "Save failed", detail: msg });
    }
    return { ok: false, message: msg };
  }
}

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
  // Retry-once force after a 409 EXTERNAL_MODIFICATION — contract owned
  // by saveSessionWithExternalRetry.
  const overrideRef = useRef(false);

  async function save() {
    setBusy(true); setErr(null);
    const out = await saveSessionWithExternalRetry(
      session.session_id, overrideRef, log);
    if (out.ok) {
      setLastSaved({
        bytes: out.res.bytes_written,
        backup: out.res.backup_path,
        at: Date.now(),
      });
      onSaved(out.res.session);
    } else {
      setErr(out.message);
    }
    setBusy(false);
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
