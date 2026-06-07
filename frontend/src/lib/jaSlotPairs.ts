/**
 * JA2 struct→shadow slot pairing table.
 *
 * Source: `TileEngine/TileDat.h:3230` (TileTypeDefines enum). Slot
 * numbers in `Ja2Set.dat.xml` are positions in that enum — each is
 * a categorical TILE TYPE (FIRSTOSTRUCT, FIRSTSHADOW, etc.), and
 * most struct types have a paired shadow type for the under-shadow
 * sprite that drops below the structure on the map.
 *
 * When the user paints a struct, MapForge auto-places the matching
 * shadow entry on the shadow layer so the placement looks right
 * without two manual paint actions. Same sub index — JA2 ships
 * struct + shadow STIs frame-aligned (sub N of the struct .sti
 * corresponds to sub N of the shadow .sti).
 *
 * Slots that don't appear as a key here have no auto-shadow (walls,
 * roofs, floors, decals, decorations, textures, etc.). Slots that
 * appear as a VALUE here are shadow-only and get hidden from the
 * palette (they're not user-pickable — they ride along with their
 * struct).
 *
 * Pairs derived 2026-05-22 by walking the enum positions in the
 * 1.13 engine source. Update if Flugente reorders TileDat.h (very
 * rare).
 */

/** struct slot → shadow slot. Lookups: `STRUCT_TO_SHADOW.get(12) === 24`. */
export const STRUCT_TO_SHADOW = new Map<number, number>([
  // FIRSTOSTRUCT .. EIGHTOSTRUCT (12-19) → FIRSTSHADOW .. EIGHTSHADOW (24-31)
  [12, 24], [13, 25], [14, 26], [15, 27],
  [16, 28], [17, 29], [18, 30], [19, 31],
  // FIRSTFULLSTRUCT .. FOURTHFULLSTRUCT (20-23) → FIRSTFULLSHADOW .. FOURTHFULLSHADOW (32-35)
  [20, 32], [21, 33], [22, 34], [23, 35],
  // FIRSTDOOR .. FOURTHDOOR (40-43) → FIRSTDOORSHADOW .. FOURTHDOORSHADOW (44-47)
  [40, 44], [41, 45], [42, 46], [43, 47],
  // FENCESTRUCT (86) → FENCESHADOW (87)
  [86, 87],
  // FIRSTVEHICLE / SECONDVEHICLE (88-89) → FIRSTVEHICLESHADOW / SECONDVEHICLESHADOW (90-91)
  [88, 90], [89, 91],
  // FIRSTDEBRISSTRUCT / SECONDDEBRISSTRUCT (93-94) → ..SHADOW (95-96)
  [93, 95], [94, 96],
  // NINTHOSTRUCT / TENTHOSTRUCT (97-98) → NINTHOSTRUCTSHADOW / TENTHOSTRUCTSHADOW (99-100)
  [97, 99], [98, 100],
  // FIRSTLARGEEXPDEBRIS / SECONDLARGEEXPDEBRIS (103-104) → ..SHADOW (105-106)
  [103, 105], [104, 106],
  // FIRSTCLIFF (10) → FIRSTCLIFFSHADOW (11) — niche, only relevant
  // for cliff-edge tiles. Included for completeness.
  [10, 11],
]);

/** Reverse lookup: shadow slot → struct slot. Used to hide shadow-only
 * slots from the palette (they aren't user-pickable). */
export const SHADOW_TO_STRUCT: Map<number, number> = (() => {
  const m = new Map<number, number>();
  for (const [structSlot, shadowSlot] of STRUCT_TO_SHADOW.entries()) {
    m.set(shadowSlot, structSlot);
  }
  return m;
})();

/** True when the slot is a shadow-only category (its only purpose is
 * to be paired with a struct). The palette hides these by default. */
export function isShadowOnlySlot(slot: number): boolean {
  return SHADOW_TO_STRUCT.has(slot);
}

/** Return the shadow slot paired with `slot`, or null if none. Used
 * by paintBrush to know whether to auto-place a shadow on the
 * shadow layer alongside the struct. */
export function findShadowSlot(slot: number): number | null {
  return STRUCT_TO_SHADOW.get(slot) ?? null;
}

/** Encoded explanation for the settings UI / tooltips. */
export const SHADOW_PAIRING_DESCRIPTION =
  "JA2 struct slots (FIRSTOSTRUCT, FENCESTRUCT, FIRSTVEHICLE, etc.) " +
  "have paired SHADOW slots in the engine's TileType table. Real " +
  "maps place both — the struct on the structs layer and the shadow " +
  "on the shadows layer. Auto-pair places both with one click.";
