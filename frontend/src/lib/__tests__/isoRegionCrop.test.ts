import { describe, expect, it } from 'vitest';
import { spriteIntersectsCrop, type RenderMeta } from '../IsoRenderer';

const crop: RenderMeta = {
  ixMin: 0, iyMin: 0, canvasW: 200, canvasH: 160, tileW: 40, tileH: 20,
};

describe('room-crop sprite admission', () => {
  it('keeps an out-of-bbox anchor whose offset sprite reaches the crop', () => {
    expect(spriteIntersectsCrop(220, 100, { ox: -80, oy: -20, w: 100, h: 80 }, 0, crop)).toBe(true);
  });

  it('excludes an out-of-bbox sprite with no projected overlap', () => {
    expect(spriteIntersectsCrop(220, 100, { ox: 0, oy: 0, w: 40, h: 40 }, 0, crop)).toBe(false);
  });
});
