// @vitest-environment jsdom
import { describe, it, expect, beforeEach } from "vitest";
import { act, renderHook } from "@testing-library/react";

import {
  CONTROL_GROUPS_KEY,
  seedFromFavorites,
  usePersistentControlGroups,
  type ControlGroup,
} from "../controlGroups";
import type { ActiveBrush } from "../../routes/MapForgePalette";
import type { SpriteGroup } from "../mapPlacement";

const brush = (slot: number, sub = 1): ActiveBrush => ({
  slot,
  sub,
  category: "furniture",
  layer: "structs",
  sti_filename: `slot${slot}.sti`,
});

const group = (slot: number): SpriteGroup => ({
  sourceTileset: 5,
  sourceSector: "B5",
  w: 1,
  h: 1,
  items: [{ dx: 0, dy: 0, layer: "structs", slot, sub: 1 }],
});

beforeEach(() => {
  localStorage.clear();
});

describe("seedFromFavorites", () => {
  it("seeds empty slots 0..8 from favorites in order when every slot is null", () => {
    const favorites = [brush(10), brush(11)];
    const seeded = seedFromFavorites(new Array(9).fill(null), favorites);
    expect(seeded).toHaveLength(9);
    expect(seeded[0]).toEqual({ kind: "brush", brush: favorites[0] });
    expect(seeded[1]).toEqual({ kind: "brush", brush: favorites[1] });
    expect(seeded[2]).toBeNull();
  });

  it("does not seed when one stored slot is already non-null", () => {
    const groups: ControlGroup[] = new Array(9).fill(null);
    groups[4] = { kind: "brush", brush: brush(99) };
    const seeded = seedFromFavorites(groups, [brush(10)]);
    expect(seeded[0]).toBeNull();
    expect(seeded[4]).toEqual({ kind: "brush", brush: brush(99) });
  });

  it("pads/truncates the input to exactly 9 entries either way", () => {
    const short = seedFromFavorites([{ kind: "brush", brush: brush(1) }], []);
    expect(short).toHaveLength(9);
    expect(short[0]).toEqual({ kind: "brush", brush: brush(1) });

    const twelve: ControlGroup[] = new Array(12).fill(null);
    twelve[0] = { kind: "brush", brush: brush(2) };
    const long = seedFromFavorites(twelve, []);
    expect(long).toHaveLength(9);
    expect(long[0]).toEqual({ kind: "brush", brush: brush(2) });
  });
});

describe("usePersistentControlGroups", () => {
  it("always yields nine entries, all null before any write", () => {
    const { result } = renderHook(() => usePersistentControlGroups("sectorA.xml", 70));
    expect(result.current[0]).toHaveLength(9);
    expect(result.current[0].every((g) => g === null)).toBe(true);
  });

  it("round-trips a single-slot write through localStorage", () => {
    const { result } = renderHook(() => usePersistentControlGroups("sectorA.xml", 70));
    act(() => {
      result.current[1](2, { kind: "brush", brush: brush(42) });
    });
    expect(result.current[0]).toHaveLength(9);
    expect(result.current[0][2]).toEqual({ kind: "brush", brush: brush(42) });

    const raw = localStorage.getItem(CONTROL_GROUPS_KEY);
    expect(raw).toBeTruthy();
    const parsed = JSON.parse(raw as string) as Record<string, ControlGroup[]>;
    expect(parsed["sectorA.xml::70"]?.[2]).toEqual({ kind: "brush", brush: brush(42) });
  });

  it("a second hook instance with the same key reads the first instance's write", () => {
    const first = renderHook(() => usePersistentControlGroups("sectorB.xml", 5));
    act(() => {
      first.result.current[1](7, { kind: "group", group: group(3) });
    });

    const second = renderHook(() => usePersistentControlGroups("sectorB.xml", 5));
    expect(second.result.current[0]).toHaveLength(9);
    expect(second.result.current[0][7]).toEqual({ kind: "group", group: group(3) });
  });

  it("rehydrates when the (xmlPath, tileset) key changes mid-session", () => {
    const seed = renderHook(() => usePersistentControlGroups("sectorC.xml", 1));
    act(() => {
      seed.result.current[1](0, { kind: "brush", brush: brush(1) });
    });

    const { result, rerender } = renderHook(
      ({ xmlPath, tileset }: { xmlPath: string; tileset: number }) => usePersistentControlGroups(xmlPath, tileset),
      { initialProps: { xmlPath: "sectorD.xml", tileset: 1 } },
    );
    expect(result.current[0][0]).toBeNull();

    rerender({ xmlPath: "sectorC.xml", tileset: 1 });
    expect(result.current[0][0]).toEqual({ kind: "brush", brush: brush(1) });
  });
});
