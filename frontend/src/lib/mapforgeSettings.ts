/**
 * MapForge settings + hotkey action registry.
 *
 * Central source of truth for user-configurable editor behavior:
 * keybindings (rebindable via the settings modal), default brush
 * size, default tool, etc. Persists to localStorage so settings
 * survive across sessions; the sidecar's APPDATA state.json stays
 * out of this layer to keep it simple — these are per-user UI
 * preferences, not anything the sidecar needs to know about.
 *
 * Adding a new action:
 *   1. Append to `MAPFORGE_ACTIONS` below with a unique id, a human
 *      label, and a default binding string.
 *   2. Wire it up in MapForgeSector's keydown dispatcher.
 *   3. (Optional) Add it to the settings modal's action list — it
 *      auto-renders since the modal reads the registry.
 */

// ─── Key combo encoding ──────────────────────────────────────────────
// Format: "Ctrl+Shift+Alt+Key" with modifiers in canonical order
// (Ctrl > Alt > Shift) and the bare key name from KeyboardEvent.key.
// Examples: "Ctrl+Z", "Shift+G", "F2", "[", "Escape".
// "" means "unbound" — the action can't be triggered by keyboard.

const MOD_ORDER = ["Ctrl", "Alt", "Shift"] as const;

/** Encode a KeyboardEvent into the canonical combo string. Returns
 * "" for modifier-only presses (so holding Ctrl alone doesn't get
 * captured as a binding when the user is mid-rebind). */
export function encodeKeyEvent(e: KeyboardEvent): string {
  if (e.key === "Control" || e.key === "Shift" || e.key === "Alt" || e.key === "Meta") {
    return "";
  }
  const mods: string[] = [];
  if (e.ctrlKey || e.metaKey) mods.push("Ctrl");
  if (e.altKey) mods.push("Alt");
  if (e.shiftKey) mods.push("Shift");
  // Normalize single-letter keys to uppercase so "Ctrl+z" and "Ctrl+Z"
  // both match the canonical "Ctrl+Z".
  let key = e.key;
  if (key.length === 1) key = key.toUpperCase();
  // Special chars from shift+number layer get the un-shifted version
  // for readability — e.g., Shift+"+" comes in as "+", which is fine,
  // but `[` is already canonical.
  return [...mods, key].join("+");
}

/** Encode a wheel gesture into the canonical combo format, using "Wheel"
 * as the key token — e.g. "Alt+Wheel" or "Wheel". Scroll DIRECTION is not
 * encoded (the handler reads deltaY), so one wheel binding covers both
 * up + down. Accepts anything carrying the modifier flags (React or DOM
 * wheel event). */
export function encodeWheelEvent(
  e: { ctrlKey: boolean; altKey: boolean; shiftKey: boolean; metaKey: boolean },
): string {
  const mods: string[] = [];
  if (e.ctrlKey || e.metaKey) mods.push("Ctrl");
  if (e.altKey) mods.push("Alt");
  if (e.shiftKey) mods.push("Shift");
  return [...mods, "Wheel"].join("+");
}

/** True when the keydown event matches the binding (after canonical
 * encoding). Use in the central dispatcher: `if (matchesBinding(e, "Ctrl+Z")) ...` */
export function matchesBinding(e: KeyboardEvent, binding: string): boolean {
  if (!binding) return false;
  return encodeKeyEvent(e) === binding;
}

/** Pretty-print a binding for the UI (e.g., "Ctrl+Z" stays as-is,
 * "" becomes "—"). */
export function formatBinding(binding: string): string {
  return binding || "—";
}


// ─── Action registry ─────────────────────────────────────────────────

export type MapForgeActionId =
  | "undo"
  | "redo"
  | "save"
  | "tool-pencil"
  | "tool-inspect"
  | "zoom-in"
  | "zoom-out"
  | "reset-view"
  | "toggle-grid"
  | "toggle-debug"
  | "brush-size-up"
  | "brush-size-down"
  | "cycle-sub-next"
  | "cycle-sub-prev"
  | "open-asset-viewer"
  | "wheel-zoom"
  | "wheel-cycle-tool"
  | "sel-copy"
  | "sel-cut"
  | "sel-paste"
  | "sel-delete"
  | "sel-cycle-next"
  | "sel-cycle-prev"
  | "nudge-left"
  | "nudge-right"
  | "nudge-up"
  | "nudge-down"
  | "cancel"
  | "payload-room"
  | "payload-height"
  | "payload-erase"
  | "shape-rect"
  | "shape-line"
  | "shape-flood";

export interface MapForgeAction {
  id: MapForgeActionId;
  label: string;
  description: string;
  defaultBinding: string;
  /** UI grouping for the settings modal. */
  group: "Edit" | "Tools" | "View" | "Brush";
}

/** Master list of rebindable actions. The settings modal lists these
 * in declaration order; group is used for visual section headers. */
export const MAPFORGE_ACTIONS: MapForgeAction[] = [
  {
    id: "undo",
    label: "Undo",
    description: "Revert the last paint stroke (or inspector edit).",
    defaultBinding: "Ctrl+Z",
    group: "Edit",
  },
  {
    id: "redo",
    label: "Redo",
    description: "Re-apply the last undone stroke.",
    defaultBinding: "Ctrl+Y",
    group: "Edit",
  },
  {
    id: "save",
    label: "Save",
    description: "Flush all in-memory edits to the .dat on disk.",
    defaultBinding: "Ctrl+S",
    group: "Edit",
  },
  {
    id: "tool-pencil",
    label: "Pencil tool",
    description: "Switch to the paint brush.",
    defaultBinding: "B",
    group: "Tools",
  },
  {
    id: "tool-inspect",
    label: "Inspect tool",
    description: "Switch to click-to-pin-tile mode.",
    defaultBinding: "I",
    group: "Tools",
  },
  {
    id: "zoom-in",
    label: "Zoom in",
    description: "Step the iso canvas zoom up by ~15%.",
    defaultBinding: "=",
    group: "View",
  },
  {
    id: "zoom-out",
    label: "Zoom out",
    description: "Step the iso canvas zoom down by ~15%.",
    defaultBinding: "-",
    group: "View",
  },
  {
    id: "reset-view",
    label: "Reset view",
    description: "Reset zoom to 1× and pan to origin.",
    defaultBinding: "0",
    group: "View",
  },
  {
    id: "toggle-grid",
    label: "Toggle grid",
    description: "Show/hide the diamond tile grid overlay.",
    defaultBinding: "G",
    group: "View",
  },
  {
    id: "toggle-debug",
    label: "Toggle debug HUD",
    description: "Show/hide the click→tile diagnostic markers.",
    defaultBinding: "Shift+D",
    group: "View",
  },
  {
    id: "brush-size-up",
    label: "Brush size +",
    description: "Increase the pencil brush radius.",
    defaultBinding: "]",
    group: "Brush",
  },
  {
    id: "brush-size-down",
    label: "Brush size −",
    description: "Decrease the pencil brush radius.",
    defaultBinding: "[",
    group: "Brush",
  },
  {
    id: "cycle-sub-next",
    label: "Next subframe",
    description: "Cycle the active brush to the next sub-frame (wraps). Also bound to Alt+right-click on the canvas.",
    defaultBinding: ".",
    group: "Brush",
  },
  {
    id: "cycle-sub-prev",
    label: "Previous subframe",
    description: "Cycle the active brush to the previous sub-frame (wraps). Also bound to Shift+Alt+right-click on the canvas.",
    defaultBinding: ",",
    group: "Brush",
  },
  {
    id: "open-asset-viewer",
    label: "Open asset browser",
    description: "Open the full pop-out asset viewer (categories, search, library).",
    defaultBinding: "A",
    group: "Tools",
  },
  {
    id: "wheel-cycle-tool",
    label: "Wheel: cycle tool",
    description: "Scroll over the canvas to cycle the active tool (inspect → pencil → shape). Default = Alt+scroll; rebind by scrolling with a modifier in the capture.",
    defaultBinding: "Alt+Wheel",
    group: "Tools",
  },
  {
    id: "wheel-zoom",
    label: "Wheel: zoom",
    description: "Scroll over the canvas to zoom in/out around the cursor. Default = plain scroll (no modifier).",
    defaultBinding: "Wheel",
    group: "View",
  },
  {
    id: "sel-copy",
    label: "Copy selection",
    description: "Copy the selected sprites (anchors + layout) to the sprite clipboard.",
    defaultBinding: "Ctrl+C",
    group: "Edit",
  },
  {
    id: "sel-cut",
    label: "Cut selection",
    description: "Copy, then remove the selected sprites (one undo).",
    defaultBinding: "Ctrl+X",
    group: "Edit",
  },
  {
    id: "sel-paste",
    label: "Paste sprites",
    description: "Arm a ghost of the sprite clipboard; click to place.",
    defaultBinding: "Ctrl+V",
    group: "Edit",
  },
  {
    id: "sel-delete",
    label: "Delete selection",
    description: "Remove the selected sprites (explicit shadows ride along).",
    defaultBinding: "Delete",
    group: "Edit",
  },
  {
    id: "sel-cycle-next",
    label: "Cycle variant",
    description: "Next sub-frame of the armed ghost or selected sprite.",
    defaultBinding: "R",
    group: "Edit",
  },
  {
    id: "sel-cycle-prev",
    label: "Cycle variant back",
    description: "Previous sub-frame.",
    defaultBinding: "Shift+R",
    group: "Edit",
  },
  {
    id: "nudge-left",
    label: "Nudge west",
    description: "Move the selection or ghost one tile in −x.",
    defaultBinding: "ArrowLeft",
    group: "Edit",
  },
  {
    id: "nudge-right",
    label: "Nudge east",
    description: "Move one tile in +x.",
    defaultBinding: "ArrowRight",
    group: "Edit",
  },
  {
    id: "nudge-up",
    label: "Nudge north",
    description: "Move one tile in −y.",
    defaultBinding: "ArrowUp",
    group: "Edit",
  },
  {
    id: "nudge-down",
    label: "Nudge south",
    description: "Move one tile in +y.",
    defaultBinding: "ArrowDown",
    group: "Edit",
  },
  {
    id: "cancel",
    label: "Cancel",
    description: "Drop the queue, disarm, then clear the selection — one level per press.",
    defaultBinding: "Escape",
    group: "Edit",
  },
  {
    id: "payload-room",
    label: "Arm: Room",
    description: "Arm the room payload (mode-less placement) — click paints a room id.",
    defaultBinding: "O",
    group: "Tools",
  },
  {
    id: "payload-height",
    label: "Arm: Height",
    description: "Arm the height payload — click adjusts a tile's height.",
    defaultBinding: "H",
    group: "Tools",
  },
  {
    id: "payload-erase",
    label: "Arm: Erase",
    description: "Arm the erase payload — click removes whatever occupies the tile.",
    defaultBinding: "E",
    group: "Tools",
  },
  {
    id: "shape-rect",
    label: "Shape: Rectangle",
    description: "One-shot rectangle fill with the armed brush.",
    defaultBinding: "T",
    group: "Tools",
  },
  {
    id: "shape-line",
    label: "Shape: Line",
    description: "One-shot straight line with the armed brush.",
    defaultBinding: "L",
    group: "Tools",
  },
  {
    id: "shape-flood",
    label: "Shape: Flood fill",
    description: "One-shot flood fill with the armed brush.",
    defaultBinding: "F",
    group: "Tools",
  },
];

const ACTION_BY_ID: Record<MapForgeActionId, MapForgeAction> =
  Object.fromEntries(MAPFORGE_ACTIONS.map((a) => [a.id, a])) as Record<MapForgeActionId, MapForgeAction>;


// ─── Settings shape ──────────────────────────────────────────────────

export interface MapForgeSettings {
  /** action id → key combo. Missing keys fall back to the default. */
  keybindings: Partial<Record<MapForgeActionId, string>>;
  /** Brush radius the pencil starts at when the editor mounts.
   * Read once, not re-applied when the user switches sector without
   * leaving the editor. */
  defaultBrushRadius: number;
  /** Default tool on session open: "inspect" or "pencil". */
  defaultTool: "inspect" | "pencil";
  /** When painting a struct slot that has a paired shadow slot
   * (per the JA2 engine's TileType pairing — see lib/jaSlotPairs),
   * also BAKE the matching shadow entry into the shadow layer.
   * Default OFF: the engine auto-adds these foliage/fence/vehicle/door
   * shadows at load (HAS_SHADOW_BUDDY) and the renderer now shows them
   * (effectiveShadowEntries), so baking only duplicates — saving a baked
   * shadow makes JA2 stack its own on top (double/darker shadow in-game).
   * Enable only if you specifically need the shadow persisted in the .dat. */
  autoPairShadows: boolean;
  /** Whether the palette shows shadow-only slots (FIRSTSHADOW,
   * SECONDSHADOW, etc.). When auto-pair is on, shadows ride along
   * with structs and the user never needs to pick them directly —
   * hiding them keeps the palette focused. Forced ON when
   * autoPairShadows is OFF (otherwise the user can't paint
   * shadows at all). */
  showShadowSlots: boolean;
  /** The highest tile-type slot the running game engine accepts.
   * JA2 1.13 stock `ja2.exe` is built with NUMBEROFTILETYPES = 151,
   * meaning valid slot indices are 0-150 inclusive. Painting anything
   * higher leaves a .dat entry the engine can't dereference and the
   * game crashes when it loads the sector.
   *
   * Some Ja2Set.dat.xml files in heavy mod installs (e.g. Mod
   * Prototype) define slots above this cap because they were authored
   * for community ja2.exe builds with a higher limit. Without this
   * guard MercForge happily lets the user paint with those slots and
   * the result is invisible-until-launch corruption.
   *
   * Per-install would be ideal but we don't have install metadata
   * plumbed for that yet — a single global default with a manual
   * override gets us most of the value. Wasteland-team custom builds
   * can bump this; everyone else leaves it at 150.
   *
   * Cap of 150 covers stock 1.13 and is the conservative default. */
  engineMaxTileSlot: number;
  /** How multi-tile struct brushes (anything with a JSD whose
   * ubNumberOfTiles > 1, e.g. helis, vehicles, big debris) paint.
   *   - "stamp": one click drops the WHOLE footprint at the anchor.
   *     Default — matches the JA2 engine's expectation that all
   *     visible footprint pieces are placed together.
   *   - "manual": one click drops only the picked sub at the clicked
   *     tile. The user assembles the multi-tile piece tile-by-tile.
   *     Power-user mode for precise placement / partial stamps.
   *
   * Shift+click temporarily INVERTS the mode for that one click —
   * lets you drop a single piece in stamp mode (or stamp the whole
   * thing in manual mode) without changing the setting. Single-tile
   * brushes ignore this setting; it only governs stamp-eligible
   * slots (those with `slot_jsd_footprint` in the atlas manifest). */
  paintMode: "stamp" | "manual";
  /** Show the Inspect/Pencil/Shape/Select tool bar and route input
   * through the per-tool branches instead of the mode-less model
   * (marquee-select always live, no tool switching). Default OFF —
   * the mode-less StarCraft-style placement is the new default; this
   * keeps the old tool bar reachable for anyone who prefers it. */
  legacyTools: boolean;
}

export const DEFAULT_SETTINGS: MapForgeSettings = {
  keybindings: {},  // empty → all actions use their defaultBinding
  defaultBrushRadius: 1,
  defaultTool: "inspect",
  autoPairShadows: false,
  showShadowSlots: false,
  paintMode: "stamp",
  // Stock JA2 1.13 cap. Bump for installs running a custom ja2.exe
  // built with a higher NUMBEROFTILETYPES.
  engineMaxTileSlot: 150,
  legacyTools: false,
};

const STORAGE_KEY = "mapforge.settings.v1";

/** Force a stored number into range, falling back for a missing or
 * non-numeric value. */
function clamp(value: unknown, lo: number, hi: number, fallback: number): number {
  return typeof value === "number" && Number.isFinite(value)
    ? Math.min(hi, Math.max(lo, value))
    : fallback;
}

/** Read settings from localStorage. Returns DEFAULT_SETTINGS on any
 * parse failure or absence. Schema-tolerant: unknown keys are
 * dropped, missing keys default, out-of-range numerics are clamped. */
export function loadSettings(): MapForgeSettings {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (!raw) return DEFAULT_SETTINGS;
    const parsed = JSON.parse(raw) as Partial<MapForgeSettings>;
    const merged: MapForgeSettings = {
      ...DEFAULT_SETTINGS,
      ...parsed,
      keybindings: {
        ...DEFAULT_SETTINGS.keybindings,
        ...(parsed.keybindings ?? {}),
      },
      // Re-clamp the numerics rather than trusting what is in storage.
      // The settings UI clamps on the way in, but a stored blob can also
      // come from a hand edit or from a future build whose valid range
      // narrowed, and engineMaxTileSlot sizes an allocated slot grid.
      defaultBrushRadius: clamp(parsed.defaultBrushRadius, 1, 8, DEFAULT_SETTINGS.defaultBrushRadius),
      engineMaxTileSlot: clamp(parsed.engineMaxTileSlot, 50, 511, DEFAULT_SETTINGS.engineMaxTileSlot),
    };
    return merged;
  } catch {
    return DEFAULT_SETTINGS;
  }
}

/** Persist settings to localStorage. Throws on quota exceeded; the
 * settings UI should catch and surface. */
export function saveSettings(s: MapForgeSettings): void {
  localStorage.setItem(STORAGE_KEY, JSON.stringify(s));
}

/** Resolve the current binding for an action (user override or
 * default). Returns "" only if the user explicitly cleared it. */
export function bindingFor(
  settings: MapForgeSettings,
  action: MapForgeActionId,
): string {
  const override = settings.keybindings[action];
  if (override !== undefined) return override;
  return ACTION_BY_ID[action].defaultBinding;
}

/** Build a reverse lookup: binding string → action id. Used by the
 * keydown dispatcher to translate an event into the action it
 * should trigger. Returns undefined when no action matches a
 * binding (or two actions share one — caller picks the first). */
export function actionForBinding(
  settings: MapForgeSettings,
  binding: string,
): MapForgeActionId | undefined {
  if (!binding) return undefined;
  for (const a of MAPFORGE_ACTIONS) {
    if (bindingFor(settings, a.id) === binding) return a.id;
  }
  return undefined;
}
