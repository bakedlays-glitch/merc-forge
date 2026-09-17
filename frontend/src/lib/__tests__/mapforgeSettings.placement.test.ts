import { describe, it, expect } from "vitest";
import { DEFAULT_SETTINGS, MAPFORGE_ACTIONS, actionForBinding } from "../mapforgeSettings";

describe("placement actions + legacy toggle", () => {
  it("defaults legacyTools off", () => {
    expect(DEFAULT_SETTINGS.legacyTools).toBe(false);
  });
  it("registers the selection/nudge/cancel verbs with their default combos", () => {
    const ids = MAPFORGE_ACTIONS.map((a) => a.id);
    for (const id of ["sel-copy", "sel-cut", "sel-paste", "sel-delete", "sel-cycle-next",
      "sel-cycle-prev", "nudge-left", "nudge-right", "nudge-up", "nudge-down", "cancel"]) {
      expect(ids).toContain(id);
    }
    expect(actionForBinding(DEFAULT_SETTINGS, "Ctrl+C")).toBe("sel-copy");
    expect(actionForBinding(DEFAULT_SETTINGS, "ArrowLeft")).toBe("nudge-left");
    expect(actionForBinding(DEFAULT_SETTINGS, "Shift+R")).toBe("sel-cycle-prev");
    expect(actionForBinding(DEFAULT_SETTINGS, "Escape")).toBe("cancel");
  });
  it("registers the payload/shape verbs (Task 10) with their default combos", () => {
    const ids = MAPFORGE_ACTIONS.map((a) => a.id);
    for (const id of ["payload-room", "payload-height", "payload-erase",
      "shape-rect", "shape-line", "shape-flood"]) {
      expect(ids).toContain(id);
    }
    expect(actionForBinding(DEFAULT_SETTINGS, "O")).toBe("payload-room");
    expect(actionForBinding(DEFAULT_SETTINGS, "H")).toBe("payload-height");
    expect(actionForBinding(DEFAULT_SETTINGS, "E")).toBe("payload-erase");
    expect(actionForBinding(DEFAULT_SETTINGS, "T")).toBe("shape-rect");
    expect(actionForBinding(DEFAULT_SETTINGS, "L")).toBe("shape-line");
    expect(actionForBinding(DEFAULT_SETTINGS, "F")).toBe("shape-flood");
  });
  it("keeps every default binding unique", () => {
    const combos = MAPFORGE_ACTIONS.map((a) => a.defaultBinding);
    expect(new Set(combos).size).toBe(combos.length);
  });
});
