/**
 * MapForge shortcut cheatsheet — the `?` overlay (UX Phase 3).
 *
 * One screen that answers "what can I even do here?": every rebindable
 * action with its LIVE binding (reads the settings, so a rebind shows
 * up here immediately), plus the mouse/canvas gestures and the
 * workflow surfaces that aren't keyboard-driven. Esc / ? / backdrop
 * click closes.
 *
 * The action list is data-driven from MAPFORGE_ACTIONS — adding an
 * action to the registry adds it here for free. Only the gesture
 * section is hand-written (mouse semantics live in canvas handlers,
 * not in any registry).
 */
import { useEffect, useMemo } from "react";

import {
  MAPFORGE_ACTIONS,
  type MapForgeSettings,
  bindingFor,
  formatBinding,
} from "../lib/mapforgeSettings";

interface Props {
  open: boolean;
  onClose(): void;
  settings: MapForgeSettings;
}

/** Hand-maintained gestures + workflow notes (no registry to read). */
const GESTURES: Array<{ combo: string; what: string }> = [
  { combo: "Left-drag", what: "Paint (pencil/height) · drag a shape (shape tool) · marquee select (select tool)" },
  { combo: "Shift+Click", what: "Invert stamp/manual placement for that one click (multi-tile brushes)" },
  { combo: "Right-click", what: "Eyedropper — pick the hovered tile's top entry as the active brush" },
  { combo: "Alt+Right-click", what: "Cycle the active brush to the next sub-frame (Shift+Alt = previous)" },
  { combo: "Alt+Drag / Middle-drag", what: "Pan the canvas" },
  { combo: "Drag (region pick)", what: "Generators: drag a box over the region (or click two corners) · Esc cancels" },
];

const WORKFLOW: Array<{ name: string; what: string }> = [
  { name: "✨ Generate", what: "Dock panel: pick a generator → drag its region → live ghost preview on the canvas → sliders re-preview → Apply" },
  { name: "✓ Validate", what: "Pre-flight crash/playability checks. Findings the map already had when opened are tagged 'pre-existing'" },
  { name: "🛰 Radar", what: "Writes the in-game minimap STI to the install's user profile and shows a thumbnail of it" },
  { name: "Select tool", what: "Marquee → Copy → Paste (rooms re-numbered automatically; big pastes auto-validate)" },
  { name: "Panels", what: "Every panel docks — drag tabs to rearrange; closed panels come back from the 'Closed —' strip under the dock" },
  { name: "Console (`)", what: "Power-user shortcut surface — everything it does also has a button. `:help` lists commands" },
];

export function MapForgeHelpOverlay({ open, onClose, settings }: Props) {
  // Esc closes (the `?` toggle lives in the parent's dispatcher).
  useEffect(() => {
    if (!open) return undefined;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        e.preventDefault();
        onClose();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, onClose]);

  const groups = useMemo(() => {
    const order: string[] = [];
    const byGroup = new Map<string, typeof MAPFORGE_ACTIONS>();
    for (const a of MAPFORGE_ACTIONS) {
      if (!byGroup.has(a.group)) {
        byGroup.set(a.group, []);
        order.push(a.group);
      }
      byGroup.get(a.group)!.push(a);
    }
    return order.map((g) => ({ group: g, actions: byGroup.get(g)! }));
  }, []);

  if (!open) return null;

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-label="Keyboard shortcuts and controls"
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 p-4"
      onClick={(e) => { if (e.target === e.currentTarget) onClose(); }}
    >
      <div className="flex max-h-[85vh] w-full max-w-3xl flex-col overflow-hidden rounded-lg border border-gray-700 bg-gray-950 shadow-2xl">
        <div className="flex items-center justify-between border-b border-gray-800 px-4 py-3">
          <h2 className="text-lg font-semibold text-gray-100">
            Controls &amp; shortcuts
          </h2>
          <div className="flex items-center gap-3">
            <span className="text-[10px] text-gray-500">
              Rebind keys in Settings → Keybindings
            </span>
            <button
              type="button"
              onClick={onClose}
              aria-label="Close"
              className="-mt-1 px-2 text-2xl leading-none text-gray-500 hover:text-gray-200"
            >
              ×
            </button>
          </div>
        </div>
        <div className="grid flex-1 grid-cols-1 gap-x-8 gap-y-4 overflow-y-auto p-4 sm:grid-cols-2">
          {groups.map(({ group, actions }) => (
            <section key={group}>
              <h3 className="mb-1.5 text-[10px] font-semibold uppercase tracking-wide text-amber-400">
                {group}
              </h3>
              <table className="w-full text-xs">
                <tbody>
                  {actions.map((a) => (
                    <tr key={a.id} title={a.description}>
                      <td className="w-28 py-0.5 pr-2 align-top">
                        <kbd className="rounded border border-gray-700 bg-gray-900 px-1.5 py-0.5 font-mono text-[10px] text-gray-200">
                          {formatBinding(bindingFor(settings, a.id))}
                        </kbd>
                      </td>
                      <td className="py-0.5 text-gray-300">{a.label}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </section>
          ))}
          <section>
            <h3 className="mb-1.5 text-[10px] font-semibold uppercase tracking-wide text-amber-400">
              Mouse
            </h3>
            <table className="w-full text-xs">
              <tbody>
                {GESTURES.map((g) => (
                  <tr key={g.combo}>
                    <td className="w-40 py-0.5 pr-2 align-top">
                      <kbd className="rounded border border-gray-700 bg-gray-900 px-1.5 py-0.5 font-mono text-[10px] text-gray-200">
                        {g.combo}
                      </kbd>
                    </td>
                    <td className="py-0.5 leading-snug text-gray-300">{g.what}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </section>
          <section className="sm:col-span-2">
            <h3 className="mb-1.5 text-[10px] font-semibold uppercase tracking-wide text-amber-400">
              Where things live
            </h3>
            <table className="w-full text-xs">
              <tbody>
                {WORKFLOW.map((w) => (
                  <tr key={w.name}>
                    <td className="w-40 py-0.5 pr-2 align-top font-medium text-gray-200">
                      {w.name}
                    </td>
                    <td className="py-0.5 leading-snug text-gray-400">{w.what}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </section>
        </div>
        <div className="border-t border-gray-800 px-4 py-2 text-center text-[10px] text-gray-600">
          Press <kbd className="rounded border border-gray-700 bg-gray-900 px-1 font-mono">?</kbd> anytime to toggle this overlay
        </div>
      </div>
    </div>
  );
}
