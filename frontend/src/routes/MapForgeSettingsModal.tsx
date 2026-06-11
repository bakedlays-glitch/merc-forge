/**
 * Settings modal for the MapForge editor.
 *
 * Two main sections:
 *   - **Hotkeys** — every action in MAPFORGE_ACTIONS gets a row with
 *     its current binding and a "Click to rebind" button. Rebind
 *     opens a transient capture overlay that listens for the next
 *     keypress.
 *   - **Defaults** — default tool, default brush radius. Less
 *     interesting but the modal is also the discoverable home for
 *     "where do I configure X" questions.
 *
 * Settings persist to localStorage via `lib/mapforgeSettings.ts`.
 * The reset button restores defaults wholesale.
 */
import { useEffect, useState } from "react";

import {
  bindingFor,
  DEFAULT_SETTINGS,
  encodeKeyEvent,
  encodeWheelEvent,
  formatBinding,
  MAPFORGE_ACTIONS,
  saveSettings,
  type MapForgeAction,
  type MapForgeActionId,
  type MapForgeSettings,
} from "../lib/mapforgeSettings";

export function MapForgeSettingsModal({
  settings, onChange, onClose,
}: {
  settings: MapForgeSettings;
  /** Called whenever settings change. Parent should persist via
   * saveSettings (we also persist here so settings survive even if
   * the parent forgets, but the parent must update its local copy). */
  onChange: (next: MapForgeSettings) => void;
  onClose: () => void;
}) {
  // ── Esc closes the modal (matches the rest of MapForge modals).
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      // Don't close while a rebind capture is active — Esc cancels
      // the capture instead. The capture component handles that.
      if (e.key === "Escape" && !document.querySelector("[data-rebind-active]")) {
        onClose();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  // Group actions by their declared group for visual sectioning.
  const groups = new Map<string, MapForgeAction[]>();
  for (const a of MAPFORGE_ACTIONS) {
    const arr = groups.get(a.group) ?? [];
    arr.push(a);
    groups.set(a.group, arr);
  }

  const update = (next: MapForgeSettings) => {
    saveSettings(next);
    onChange(next);
  };
  const setBinding = (id: MapForgeActionId, binding: string) => {
    update({
      ...settings,
      keybindings: { ...settings.keybindings, [id]: binding },
    });
  };
  const clearBinding = (id: MapForgeActionId) => setBinding(id, "");
  const resetBinding = (id: MapForgeActionId) => {
    // Delete the override so the default takes effect again.
    const next = { ...settings.keybindings };
    delete next[id];
    update({ ...settings, keybindings: next });
  };
  const resetAllDefaults = () => {
    if (confirm("Reset ALL MapForge settings to defaults? This clears your hotkey overrides + brush/tool defaults.")) {
      saveSettings(DEFAULT_SETTINGS);
      onChange(DEFAULT_SETTINGS);
    }
  };

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm"
      onClick={onClose}
      role="dialog"
      aria-modal="true"
    >
      <div
        className="w-[36rem] max-w-[90vw] max-h-[85vh] overflow-hidden rounded-lg border border-gray-700 bg-gray-950 shadow-2xl flex flex-col"
        onClick={(e) => e.stopPropagation()}
      >
        {/* Header */}
        <div className="flex items-start justify-between gap-3 border-b border-gray-800 p-3">
          <div>
            <h3 className="text-sm font-semibold text-gray-200">
              MapForge Settings
            </h3>
            <p className="mt-0.5 text-[11px] text-gray-500">
              Persists to browser localStorage. Settings apply to the
              currently-open editor immediately.
            </p>
          </div>
          <button
            type="button"
            onClick={onClose}
            className="text-gray-500 hover:text-gray-200"
            title="Close (Esc)"
          >✕</button>
        </div>

        {/* Body */}
        <div className="flex-1 overflow-y-auto p-3 space-y-4">
          {/* ── Hotkey section ─────────────────────────────────── */}
          <section>
            <h4 className="mb-1 text-xs font-semibold uppercase text-gray-400">
              Hotkeys
            </h4>
            <p className="mb-2 text-[10px] text-gray-500">
              Click "Rebind" then press the key combo (or scroll gesture) you want for
              that action. Press Esc to cancel a rebind in progress.
              "Default" reverts to the shipped binding.
            </p>
            {Array.from(groups.entries()).map(([group, actions]) => (
              <div key={group} className="mb-3">
                <div className="mb-1 text-[10px] font-semibold uppercase text-gray-500">
                  {group}
                </div>
                <ul className="space-y-0.5">
                  {actions.map((a) => (
                    <HotkeyRow
                      key={a.id}
                      action={a}
                      currentBinding={bindingFor(settings, a.id)}
                      onRebind={(binding) => setBinding(a.id, binding)}
                      onClear={() => clearBinding(a.id)}
                      onResetDefault={() => resetBinding(a.id)}
                    />
                  ))}
                </ul>
              </div>
            ))}
          </section>

          {/* ── Defaults section ──────────────────────────────── */}
          <section>
            <h4 className="mb-1 text-xs font-semibold uppercase text-gray-400">
              Defaults
            </h4>
            <div className="space-y-2">
              <div className="flex items-center justify-between rounded border border-gray-800 bg-gray-900 px-2 py-1.5">
                <div>
                  <div className="text-xs text-gray-300">Default tool</div>
                  <div className="text-[10px] text-gray-500">
                    Selected when a sector first opens.
                  </div>
                </div>
                <select
                  value={settings.defaultTool}
                  onChange={(e) => update({
                    ...settings,
                    defaultTool: e.target.value as "inspect" | "pencil",
                  })}
                  className="rounded border border-gray-700 bg-gray-950 px-2 py-1 text-xs"
                >
                  <option value="inspect">Inspect</option>
                  <option value="pencil">Pencil</option>
                </select>
              </div>
              <div className="flex items-center justify-between rounded border border-gray-800 bg-gray-900 px-2 py-1.5">
                <div>
                  <div className="text-xs text-gray-300">
                    Default brush radius: <span className="font-mono text-gray-100">{settings.defaultBrushRadius}</span>
                  </div>
                  <div className="text-[10px] text-gray-500">
                    Pencil brush starts this size on each new session.
                  </div>
                </div>
                <input
                  type="range"
                  min={1} max={8}
                  value={settings.defaultBrushRadius}
                  onChange={(e) => update({
                    ...settings,
                    defaultBrushRadius: parseInt(e.target.value, 10) || 1,
                  })}
                  className="w-32"
                />
              </div>
              <div className="flex items-center justify-between rounded border border-gray-800 bg-gray-900 px-2 py-1.5">
                <div className="flex-1 pr-2">
                  <div className="text-xs text-gray-300">Auto-pair shadows</div>
                  <div className="text-[10px] text-gray-500">
                    Painting a struct (e.g. FIRSTOSTRUCT slot 12) also
                    drops the matching shadow entry (FIRSTSHADOW slot 24)
                    on the shadow layer. Mirrors how JA2 maps actually
                    work — every struct has a shadow underneath. Disable
                    if you want manual shadow control.
                  </div>
                </div>
                <input
                  type="checkbox"
                  checked={settings.autoPairShadows}
                  onChange={(e) => update({
                    ...settings,
                    autoPairShadows: e.target.checked,
                    // Force-show shadow slots when auto-pair is off,
                    // otherwise the user can't place shadows at all.
                    showShadowSlots: !e.target.checked || settings.showShadowSlots,
                  })}
                  className="h-4 w-4"
                />
              </div>
              <div className="flex items-center justify-between rounded border border-gray-800 bg-gray-900 px-2 py-1.5">
                <div className="flex-1 pr-2">
                  <div className="text-xs text-gray-300">Show shadow slots in palette</div>
                  <div className="text-[10px] text-gray-500">
                    Reveal the shadow-only slots (FIRSTSHADOW,
                    FENCESHADOW, etc.). Off by default — they ride
                    along with structs when auto-pair is on.
                    Force-enabled when auto-pair is off.
                  </div>
                </div>
                <input
                  type="checkbox"
                  checked={settings.showShadowSlots}
                  disabled={!settings.autoPairShadows
                            ? false : false /* always enabled */}
                  onChange={(e) => update({
                    ...settings,
                    showShadowSlots: e.target.checked,
                  })}
                  className="h-4 w-4"
                />
              </div>
              <div className="flex items-center justify-between rounded border border-gray-800 bg-gray-900 px-2 py-1.5">
                <div className="flex-1 pr-2">
                  <div className="text-xs text-gray-300">
                    Engine max tile slot: <span className="font-mono text-gray-100">{settings.engineMaxTileSlot}</span>
                  </div>
                  <div className="text-[10px] text-gray-500">
                    Highest tile-type slot your <code>ja2.exe</code> supports.
                    Slots above this are hidden from the palette and paint
                    is refused so the engine can never crash on a missing
                    slot. <b>Stock JA2 1.13 = 150</b>. Community builds
                    (some Wasteland forks) raise this — leave at 150 unless
                    you know your binary supports more.
                  </div>
                </div>
                <input
                  type="number"
                  min={50} max={511}
                  value={settings.engineMaxTileSlot}
                  onChange={(e) => {
                    const n = parseInt(e.target.value, 10);
                    if (!Number.isFinite(n)) return;
                    update({
                      ...settings,
                      engineMaxTileSlot: Math.max(50, Math.min(511, n)),
                    });
                  }}
                  className="w-20 rounded border border-gray-700 bg-gray-950 px-2 py-1 text-xs font-mono text-right"
                  title="Highest slot index ja2.exe accepts. 150 = stock 1.13."
                />
              </div>
              <div className="flex items-center justify-between rounded border border-gray-800 bg-gray-900 px-2 py-1.5">
                <div className="flex-1 pr-2">
                  <div className="text-xs text-gray-300">Multi-tile paint mode</div>
                  <div className="text-[10px] text-gray-500">
                    How brushes with multi-tile footprints (helis,
                    vehicles, big debris) paint.
                    <ul className="ml-3 mt-0.5 list-disc">
                      <li><b>Stamp</b> — one click drops the whole
                          footprint at the anchor tile. Matches the
                          JA2 engine's expectation.</li>
                      <li><b>Manual</b> — drops only the picked sub
                          at the clicked tile so you can assemble
                          the multi-tile piece by hand.</li>
                    </ul>
                    Shift+click temporarily inverts the mode for that
                    one click. Single-tile brushes ignore this.
                  </div>
                </div>
                <select
                  value={settings.paintMode}
                  onChange={(e) => update({
                    ...settings,
                    paintMode: e.target.value as "stamp" | "manual",
                  })}
                  className="rounded border border-gray-700 bg-gray-950 px-2 py-1 text-xs"
                  title="How multi-tile struct brushes paint. Shift+click inverts for one paint."
                >
                  <option value="stamp">Stamp (whole footprint)</option>
                  <option value="manual">Manual (one tile per click)</option>
                </select>
              </div>
            </div>
          </section>
        </div>

        {/* Footer */}
        <div className="flex items-center justify-between border-t border-gray-800 bg-gray-950 p-2">
          <button
            type="button"
            onClick={resetAllDefaults}
            className="rounded border border-red-800 bg-red-950/50 px-3 py-1 text-[11px] text-red-300 hover:border-red-600 hover:bg-red-900/50"
          >
            Reset all to defaults
          </button>
          <button
            type="button"
            onClick={onClose}
            className="rounded border border-gray-700 bg-gray-900 px-4 py-1 text-[11px] text-gray-200 hover:border-gray-500"
          >
            Done
          </button>
        </div>
      </div>
    </div>
  );
}

/** One row in the hotkey list: action label + description + current
 * binding badge + rebind/clear/default buttons. The rebind button
 * opens a transient capture state that swallows the next keypress
 * and sets it as the new binding. */
function HotkeyRow({
  action, currentBinding, onRebind, onClear, onResetDefault,
}: {
  action: MapForgeAction;
  currentBinding: string;
  onRebind: (binding: string) => void;
  onClear: () => void;
  onResetDefault: () => void;
}) {
  const [capturing, setCapturing] = useState(false);
  useEffect(() => {
    if (!capturing) return;
    const onKey = (e: KeyboardEvent) => {
      e.preventDefault();
      e.stopPropagation();
      if (e.key === "Escape") {
        setCapturing(false);
        return;
      }
      const combo = encodeKeyEvent(e);
      if (!combo) return;  // modifier-only press, keep waiting
      onRebind(combo);
      setCapturing(false);
    };
    // Wheel gestures are bindable too (wheel-zoom / wheel-cycle-tool) —
    // scroll (optionally with a modifier) to capture "Wheel" / "Alt+Wheel".
    const onWheel = (e: WheelEvent) => {
      e.preventDefault();
      e.stopPropagation();
      onRebind(encodeWheelEvent(e));
      setCapturing(false);
    };
    window.addEventListener("keydown", onKey, true);  // capture phase
    window.addEventListener("wheel", onWheel, { capture: true, passive: false });
    return () => {
      window.removeEventListener("keydown", onKey, true);
      window.removeEventListener("wheel", onWheel, true);
    };
  }, [capturing, onRebind]);

  const isDefault = currentBinding === action.defaultBinding;

  return (
    <li className="flex items-center justify-between gap-2 rounded border border-gray-800 bg-gray-900 px-2 py-1">
      <div className="min-w-0 flex-1">
        <div className="text-xs text-gray-200">{action.label}</div>
        <div className="truncate text-[10px] text-gray-500">
          {action.description}
        </div>
      </div>
      {capturing ? (
        <span
          data-rebind-active="1"
          className="rounded border border-blue-500 bg-blue-950/60 px-2 py-0.5 font-mono text-[10px] text-blue-200 animate-pulse"
        >
          press a key or scroll… (Esc cancels)
        </span>
      ) : (
        <span
          className="rounded border border-gray-700 bg-gray-950 px-2 py-0.5 font-mono text-[10px] text-gray-200"
          title={isDefault ? "Default binding" : `Default: ${action.defaultBinding}`}
        >
          {formatBinding(currentBinding)}
        </span>
      )}
      <div className="flex shrink-0 items-center gap-1">
        <button
          type="button"
          onClick={() => setCapturing(true)}
          disabled={capturing}
          className="rounded border border-blue-700 bg-blue-950/40 px-2 py-0.5 text-[10px] text-blue-200 hover:bg-blue-900/50 disabled:opacity-50"
          title="Rebind this action"
        >
          Rebind
        </button>
        <button
          type="button"
          onClick={onClear}
          disabled={!currentBinding}
          className="rounded border border-gray-700 bg-gray-900 px-2 py-0.5 text-[10px] text-gray-400 hover:border-gray-500 disabled:opacity-30"
          title="Unbind (no keyboard shortcut)"
        >
          ✕
        </button>
        {!isDefault && (
          <button
            type="button"
            onClick={onResetDefault}
            className="rounded border border-gray-700 bg-gray-900 px-2 py-0.5 text-[10px] text-gray-400 hover:border-gray-500"
            title={`Restore default: ${action.defaultBinding}`}
          >
            ↺
          </button>
        )}
      </div>
    </li>
  );
}
