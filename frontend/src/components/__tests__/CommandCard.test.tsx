// @vitest-environment jsdom
import { describe, it, expect, vi, afterEach } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";

import { CommandCard, cardCellsFor, type CardCell, type CardState } from "../CommandCard";
import type { MapForgeActionId } from "../../lib/mapforgeSettings";

afterEach(cleanup);

const BINDINGS: Partial<Record<MapForgeActionId, string>> = {
  "sel-copy": "Ctrl+C",
  "sel-cut": "Ctrl+X",
  "sel-delete": "Delete",
  "sel-cycle-next": "R",
  "sel-paste": "Ctrl+V",
  "cancel": "Escape",
  "shape-rect": "T",
  "shape-line": "L",
  "shape-flood": "F",
  "brush-size-up": "]",
  "brush-size-down": "[",
  "payload-room": "O",
  "payload-height": "H",
  "payload-erase": "E",
};
const bindingFor = (id: MapForgeActionId): string => BINDINGS[id] ?? "";

const base: CardState = {
  selectionCount: 0, ghost: false, brush: false, payload: "", clipboard: false, readOnly: false,
};

const ids = (cells: CardCell[]): string[] => cells.map((c) => c.id);

describe("cardCellsFor", () => {
  it("returns the selection row: Copy/Cut/Delete/Cycle/Nudge/Save-group", () => {
    const cells = cardCellsFor({ ...base, selectionCount: 2 }, bindingFor, {});
    expect(ids(cells)).toEqual(["sel-copy", "sel-cut", "sel-delete", "sel-cycle-next", "nudge", "save-group"]);
    expect(cells.find((c) => c.id === "nudge")?.hotkey).toBe("←↑→↓");
    expect(cells.find((c) => c.id === "save-group")?.hotkey).toBe("Ctrl+1-9");
    expect(cells.find((c) => c.id === "sel-copy")?.hotkey).toBe("Ctrl+C");
  });

  it("returns the ghost row: Place/Queue/Cycle/Cancel", () => {
    const cells = cardCellsFor({ ...base, ghost: true }, bindingFor, {});
    expect(ids(cells)).toEqual(["place", "queue", "sel-cycle-next", "cancel"]);
    expect(cells.find((c) => c.id === "place")?.hotkey).toBe("LMB");
    expect(cells.find((c) => c.id === "queue")?.hotkey).toBe("Shift+LMB");
  });

  it("returns the brush row: Rect/Line/Flood/Radius/Cancel, radius joins the two bindings (down / up order)", () => {
    const cells = cardCellsFor({ ...base, brush: true }, bindingFor, {});
    expect(ids(cells)).toEqual(["shape-rect", "shape-line", "shape-flood", "radius", "cancel"]);
    expect(cells.find((c) => c.id === "radius")?.hotkey).toBe("[ / ]");
  });

  it("returns the nothing row: Room/Height/Erase, plus Paste only when clipboard is non-empty", () => {
    expect(ids(cardCellsFor(base, bindingFor, {}))).toEqual(["payload-room", "payload-height", "payload-erase"]);
    const withClip = cardCellsFor({ ...base, clipboard: true }, bindingFor, {});
    expect(ids(withClip)).toEqual(["payload-room", "payload-height", "payload-erase", "sel-paste"]);
  });

  it("precedence: ghost beats brush and selection; brush beats selection", () => {
    const allThree = cardCellsFor({ ...base, selectionCount: 3, brush: true, ghost: true }, bindingFor, {});
    expect(ids(allThree)).toEqual(["place", "queue", "sel-cycle-next", "cancel"]);

    const brushOverSel = cardCellsFor({ ...base, selectionCount: 3, brush: true }, bindingFor, {});
    expect(ids(brushOverSel)).toEqual(["shape-rect", "shape-line", "shape-flood", "radius", "cancel"]);
  });

  it("read-only disables every mutating cell except Cancel and Copy", () => {
    const sel = cardCellsFor({ ...base, selectionCount: 2, readOnly: true }, bindingFor, {});
    const byId = Object.fromEntries(sel.map((c) => [c.id, c]));
    expect(byId["sel-copy"]?.disabled).toBeFalsy();
    expect(byId["sel-cut"]?.disabled).toBe(true);
    expect(byId["sel-delete"]?.disabled).toBe(true);
    expect(byId["sel-cycle-next"]?.disabled).toBe(true);
    expect(byId["nudge"]?.disabled).toBe(true);
    expect(byId["save-group"]?.disabled).toBe(true);

    const ghost = cardCellsFor({ ...base, ghost: true, readOnly: true }, bindingFor, {});
    const byId2 = Object.fromEntries(ghost.map((c) => [c.id, c]));
    expect(byId2["cancel"]?.disabled).toBeFalsy();
    expect(byId2["place"]?.disabled).toBe(true);
    expect(byId2["queue"]?.disabled).toBe(true);

    const nothing = cardCellsFor({ ...base, clipboard: true, readOnly: true }, bindingFor, {});
    expect(nothing.every((c) => c.disabled)).toBe(true);
  });

  it("wires Nudge and Save group to their help handler keys", () => {
    const nudgeHelp = vi.fn();
    const saveGroupHelp = vi.fn();
    const cells = cardCellsFor({ ...base, selectionCount: 1 }, bindingFor, {
      "nudge-help": nudgeHelp,
      "save-group-help": saveGroupHelp,
    });
    cells.find((c) => c.id === "nudge")?.onClick();
    cells.find((c) => c.id === "save-group")?.onClick();
    expect(nudgeHelp).toHaveBeenCalledTimes(1);
    expect(saveGroupHelp).toHaveBeenCalledTimes(1);
  });

  it("defaults a missing handler to a no-op rather than throwing", () => {
    const cells = cardCellsFor({ ...base, selectionCount: 1 }, bindingFor, {});
    expect(() => cells.forEach((c) => c.onClick())).not.toThrow();
  });
});

describe("CommandCard", () => {
  it("renders null for an empty cell list", () => {
    const { container } = render(<CommandCard cells={[]} />);
    expect(container.firstChild).toBeNull();
  });

  it("shows label + hotkey and dispatches a click to onClick", () => {
    const onClick = vi.fn();
    const cells: CardCell[] = [{ id: "sel-copy", label: "Copy", hotkey: "Ctrl+C", icon: "CPY", onClick }];
    render(<CommandCard cells={cells} />);
    const btn = screen.getByRole("button", { name: /Copy/ });
    expect(btn.textContent).toContain("Copy");
    expect(btn.textContent).toContain("Ctrl+C");
    expect(btn.getAttribute("title")).toBe("Copy (Ctrl+C)");
    fireEvent.click(btn);
    expect(onClick).toHaveBeenCalledTimes(1);
  });

  it("disables a cell marked disabled and skips its onClick", () => {
    const onClick = vi.fn();
    const cells: CardCell[] = [{ id: "sel-cut", label: "Cut", hotkey: "Ctrl+X", icon: "CUT", disabled: true, onClick }];
    render(<CommandCard cells={cells} />);
    const btn = screen.getByRole("button", { name: /Cut/ }) as HTMLButtonElement;
    expect(btn.disabled).toBe(true);
    fireEvent.click(btn);
    expect(onClick).not.toHaveBeenCalled();
  });
});
