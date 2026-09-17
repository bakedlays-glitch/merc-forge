import { describe, expect, it } from 'vitest';
import { atlasTextureLayout } from '../atlasTextureLayout';
import type { AtlasCell } from '../mapforge';

const cell = (sub: number, w: number, h: number): AtlasCell =>
  ({ slot: 50, sub, x: 0, y: sub * h, w, h, ox: -400, oy: -200, zstrip: null });

describe('GPU atlas repacking', () => {
  it('fits a tall Vegas surface atlas without changing sprite sizes or offsets', () => {
    const cells = Array.from({ length: 292 }, (_, i) => cell(i + 1, 960, 480));
    const before = JSON.stringify(cells);
    const packed = atlasTextureLayout(cells, 16384);
    expect(packed.width).toBeLessThanOrEqual(16384);
    expect(packed.height).toBeLessThanOrEqual(16384);
    expect(packed.placements).toHaveLength(cells.length);
    expect(JSON.stringify(cells)).toBe(before);
    for (const { source, target } of packed.placements) {
      expect({ ...target, x: source.x, y: source.y }).toEqual(source);
    }
    for (let i = 0; i < packed.placements.length; ++i) {
      const a = packed.placements[i]!.target;
      for (const { target: b } of packed.placements.slice(i + 1)) {
        expect(a.x + a.w < b.x || b.x + b.w < a.x || a.y + a.h < b.y || b.y + b.h < a.y).toBe(true);
      }
    }
  });
  it('rejects an individual sprite larger than the device limit', () => {
    expect(() => atlasTextureLayout([cell(1, 100, 12)], 64)).toThrow('sprite');
  });
  it('rejects an atlas that cannot fit rather than uploading an invalid texture', () => {
    expect(() => atlasTextureLayout([cell(1, 40, 40), cell(2, 40, 40)], 64)).toThrow('cannot fit');
  });
});
