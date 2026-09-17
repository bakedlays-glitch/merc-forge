import type { AtlasCell } from './mapforge';

/** Repack whole sprites without scaling or splitting them across texture edges. */
export function atlasTextureLayout(cells: AtlasCell[], limit: number) {
  const placements: { source: AtlasCell; target: AtlasCell }[] = [];
  let x = 1, y = 1, rowHeight = 0, width = 1;
  for (const source of [...cells].sort((a, b) => b.h - a.h || b.w - a.w)) {
    if (source.w + 2 > limit || source.h + 2 > limit) {
      throw new Error(`Atlas sprite ${source.slot}:${source.sub} exceeds GPU texture limit ${limit}`);
    }
    if (x + source.w + 1 > limit) { x = 1; y += rowHeight + 2; rowHeight = 0; }
    if (y + source.h + 1 > limit) {
      throw new Error(`Atlas cannot fit within GPU texture limit ${limit}; reduce the tileset or use multiple atlas pages`);
    }
    placements.push({ source, target: { ...source, x, y } });
    width = Math.max(width, x + source.w + 1);
    x += source.w + 2;
    rowHeight = Math.max(rowHeight, source.h);
  }
  return { width, height: y + rowHeight + 1, placements };
}
