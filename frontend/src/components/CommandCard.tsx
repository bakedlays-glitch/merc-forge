/**
 * Command card — bottom-right of the MapForge canvas, one
 * button per available verb for the current mode-less placement state.
 * `cardCellsFor` is pure (no React) so every row/precedence rule is
 * covered by a plain vitest test; `CommandCard` just renders whatever
 * cell list the route hands it.
 */
import type { MapForgeActionId } from "../lib/mapforgeSettings";

export interface CardCell {
  id: string;
  label: string;
  hotkey: string;
  icon: string;
  disabled?: boolean;
  onClick: () => void;
}

export interface CardState {
  selectionCount: number;
  ghost: boolean;
  brush: boolean;
  payload: string;
  clipboard: boolean;
  readOnly: boolean;
}

const NOOP = (): void => {};

function cell(id: string, label: string, hotkey: string, icon: string, onClick: () => void): CardCell {
  return { id, label, hotkey, icon, onClick };
}

/** Only these two stay usable when `readOnly` is set — everything else
 * mutates the sector. */
const READ_ONLY_ALLOWED = new Set(["cancel", "sel-copy"]);

/** Pure: state → the row of cells for the command card. Precedence
 * when several flags are true: ghost > brush >
 * selection > nothing — a ghost armed always wins the card even with a
 * live selection underneath it, and an armed brush beats a bare
 * selection. `handlers` keys action ids for action-backed cells, plus
 * "place"/"queue" (ghost verbs with no single-key binding) and the two
 * help placeholders "nudge-help"/"save-group-help" (multi-key gestures
 * that don't have one action id to bind). A missing handler is a no-op,
 * never a throw. */
export function cardCellsFor(
  state: CardState,
  bindingFor: (id: MapForgeActionId) => string,
  handlers: Record<string, () => void>,
): CardCell[] {
  const h = (id: string): (() => void) => handlers[id] ?? NOOP;
  let cells: CardCell[];

  if (state.ghost) {
    cells = [
      cell("place", "Place", "LMB", "PLC", h("place")),
      cell("queue", "Queue", "Shift+LMB", "QUE", h("queue")),
      cell("sel-cycle-next", "Cycle", bindingFor("sel-cycle-next"), "CYC", h("sel-cycle-next")),
      cell("cancel", "Cancel", bindingFor("cancel"), "CNL", h("cancel")),
    ];
  } else if (state.brush) {
    cells = [
      cell("shape-rect", "Rect", bindingFor("shape-rect"), "REC", h("shape-rect")),
      cell("shape-line", "Line", bindingFor("shape-line"), "LIN", h("shape-line")),
      cell("shape-flood", "Flood", bindingFor("shape-flood"), "FLD", h("shape-flood")),
      cell("radius", "Radius", `${bindingFor("brush-size-down")} / ${bindingFor("brush-size-up")}`, "RAD", h("radius")),
      cell("cancel", "Cancel", bindingFor("cancel"), "CNL", h("cancel")),
    ];
  } else if (state.selectionCount > 0) {
    cells = [
      cell("sel-copy", "Copy", bindingFor("sel-copy"), "CPY", h("sel-copy")),
      cell("sel-cut", "Cut", bindingFor("sel-cut"), "CUT", h("sel-cut")),
      cell("sel-delete", "Delete", bindingFor("sel-delete"), "DEL", h("sel-delete")),
      cell("sel-cycle-next", "Cycle", bindingFor("sel-cycle-next"), "CYC", h("sel-cycle-next")),
      cell("nudge", "Nudge", "←↑→↓", "NDG", h("nudge-help")),
      cell("save-group", "Save group", "Ctrl+1-9", "GRP", h("save-group-help")),
    ];
  } else {
    cells = [
      cell("payload-room", "Room", bindingFor("payload-room"), "RM", h("payload-room")),
      cell("payload-height", "Height", bindingFor("payload-height"), "HT", h("payload-height")),
      cell("payload-erase", "Erase", bindingFor("payload-erase"), "ER", h("payload-erase")),
    ];
    if (state.clipboard) {
      cells.push(cell("sel-paste", "Paste", bindingFor("sel-paste"), "PST", h("sel-paste")));
    }
  }

  if (!state.readOnly) return cells;
  return cells.map((c) => (READ_ONLY_ALLOWED.has(c.id) ? c : { ...c, disabled: true }));
}

export function CommandCard({ cells }: { cells: CardCell[] }): JSX.Element | null {
  if (cells.length === 0) return null;
  return (
    <div
      data-card
      className="grid grid-cols-3 gap-1 rounded border border-neutral-700 bg-neutral-900/90 p-1 text-xs text-neutral-200 shadow-lg"
    >
      {cells.map((c) => (
        <button
          key={c.id}
          type="button"
          data-card-cell={c.id}
          title={`${c.label} (${c.hotkey})`}
          disabled={c.disabled}
          onClick={c.onClick}
          className="flex flex-col items-center justify-center gap-0.5 rounded bg-neutral-800 px-1.5 py-1 hover:bg-neutral-700 disabled:cursor-not-allowed disabled:opacity-40"
        >
          <span aria-hidden="true" className="text-[10px] font-semibold tracking-wide">{c.icon}</span>
          <span>{c.label}</span>
          <span className="text-[9px] text-neutral-400">{c.hotkey}</span>
        </button>
      ))}
    </div>
  );
}
