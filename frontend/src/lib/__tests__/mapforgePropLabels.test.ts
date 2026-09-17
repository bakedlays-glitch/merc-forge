import { describe, expect, it } from "vitest";
import { propFrameLabel } from "../mapforgePropLabels";
import type { PaletteSlot } from "../mapforge";

const slot: PaletteSlot = {
  slot: 42,
  sti_filename: "fo2_junktown_props_v101.sti",
  frame_count: 3,
  category: "furniture",
  has_jsd: false,
  display_name: "Junktown furnishings",
  origin: "Junktown",
  sub_names: { 1: "Lantern", 2: "Crate" },
};

describe("propFrameLabel", () => {
  it("uses the named frame first and keeps the sheet and raw identity available", () => {
    expect(propFrameLabel(42, 1, slot)).toEqual({
      primary: "Lantern",
      context: "Junktown furnishings",
      filename: "fo2_junktown_props_v101.sti",
      technical: "slot 42 · frame 1",
    });
  });

  it("falls back from a missing frame name to the sheet name", () => {
    expect(propFrameLabel(42, 3, slot).primary).toBe("Junktown furnishings");
  });

  it("makes an uncatalogued filename readable and accepts atlas fallback", () => {
    expect(propFrameLabel(19, 2, undefined, "junk_scatter-v101.STI")).toEqual({
      primary: "junk scatter v101",
      context: null,
      filename: "junk_scatter-v101.STI",
      technical: "slot 19 · frame 2",
    });
  });

  it("uses slot and frame when no filename is available", () => {
    expect(propFrameLabel(7, 4, undefined).primary).toBe("Slot 7 frame 4");
  });
});
