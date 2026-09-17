import { describe, expect, it } from 'vitest';
import { structuralDepth } from '../isoDepth';

describe('engine-relative structural depth', () => {
  it('keeps the cafe sign in front of the earlier slanted roof', () => {
    const sign = structuralDepth('structs', 153 + 251);
    expect(sign).toBeLessThan(structuralDepth('roofs', 152 + 244));
    expect(sign).toBeLessThan(structuralDepth('roofs', 152 + 246));
    expect(structuralDepth('roofs', 152 + 249)).toBeLessThan(sign);
  });

  it('puts a roof over its own wall and keeps roof rows ordered', () => {
    expect(structuralDepth('roofs', 404)).toBeLessThan(structuralDepth('structs', 404));
    expect(structuralDepth('onroofs', 404)).toBeLessThan(structuralDepth('roofs', 404));
    expect(structuralDepth('roofs', 393)).toBeLessThan(structuralDepth('roofs', 392));
  });

  it('treats one native Z strip change as one iso row', () => {
    expect(structuralDepth('structs', 403, 1)).toBeCloseTo(structuralDepth('structs', 404), 8);
  });
});
