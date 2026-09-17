/** Map JA2's structural Z values onto the renderer's WebGL depth scale. */
import { WALL_HEIGHT } from './IsoRenderer';

export type StructuralLayer = 'structs' | 'roofs' | 'onroofs';

// Visual Studio Root/TileEngine/worlddef.h:10-11 and
// Render Z.cpp:53-79: each iso row advances world Y by 10, while a roof
// or on-roof sprite adds WALL_HEIGHT before the layer sublevel.
const WORLD_Y_PER_ROW = 10;
const Z_SUBLAYERS = 8; // renderworld.h:37
const STRUCT_Z_LEVEL = 4;
const ROOF_Z_LEVEL = 5;
const ONROOF_Z_LEVEL = 6;

// Preserve the existing WebGL row spacing and the separate ground tiers.
export const ISO_ROW_DEPTH_STEP = 0.04 / 320;
const STRUCT_BASE_DEPTH = 0.50;

export function structuralDepth(
  layer: StructuralLayer,
  isoRow: number,
  zStripDelta = 0,
): number {
  const height = layer === 'structs' ? 0 : WALL_HEIGHT;
  const sublevel = layer === 'structs' ? STRUCT_Z_LEVEL
    : layer === 'roofs' ? ROOF_Z_LEVEL : ONROOF_Z_LEVEL;
  const effectiveRow = isoRow + height / WORLD_Y_PER_ROW
    + (sublevel - STRUCT_Z_LEVEL) / (WORLD_Y_PER_ROW * Z_SUBLAYERS)
    + zStripDelta;
  return STRUCT_BASE_DEPTH - effectiveRow * ISO_ROW_DEPTH_STEP;
}
