import { describe, expect, it } from 'vitest';
import {
  cleanWeights, posterSlots, posterStills, rankStills, scoreStill, stillMetrics, stillTimes,
} from './stills.js';

// A w x h RGBA image from a function (x, y) -> [r, g, b].
function img(w, h, fn) {
  const d = new Uint8ClampedArray(w * h * 4);
  for (let y = 0; y < h; y += 1) {
    for (let x = 0; x < w; x += 1) {
      const [r, g, b] = fn(x, y);
      const i = (y * w + x) * 4;
      d[i] = r; d[i + 1] = g; d[i + 2] = b; d[i + 3] = 255;
    }
  }
  return d;
}

describe('stillTimes', () => {
  it('spreads stills over the middle of equal slices', () => {
    expect(stillTimes(10, 50, 4)).toEqual([15, 25, 35, 45]);
    expect(stillTimes(0, 10, 1)).toEqual([5]);
  });
});

describe('stillMetrics', () => {
  it('scores flat black as dark, flat and dull', () => {
    const m = stillMetrics(img(8, 8, () => [0, 0, 0]), 8, 8);
    expect(m).toEqual({ exposure: 0, contrast: 0, color: 0, detail: 0, luma: 0 });
  });

  it('gives mid grey full exposure and nothing else', () => {
    const m = stillMetrics(img(8, 8, () => [128, 128, 128]), 8, 8);
    expect(m.exposure).toBeGreaterThan(0.99);
    expect(m.contrast).toBe(0);
    expect(m.color).toBeCloseTo(0);
    expect(m.detail).toBe(0);
  });

  it('finds contrast and detail in a checkerboard', () => {
    const m = stillMetrics(img(8, 8, (x, y) => ((x + y) % 2 ? [255, 255, 255] : [0, 0, 0])), 8, 8);
    expect(m.contrast).toBe(1);
    expect(m.detail).toBe(1);
    expect(m.exposure).toBeGreaterThan(0.95);
  });

  it('rates colour above grey of the same brightness', () => {
    const grey = stillMetrics(img(8, 8, () => [120, 120, 120]), 8, 8);
    const red = stillMetrics(img(8, 8, (x) => (x < 4 ? [230, 30, 30] : [30, 60, 220])), 8, 8);
    expect(red.color).toBeGreaterThan(0.5);
    expect(grey.color).toBeLessThan(0.01);
  });

  it('keeps every measure inside 0..1', () => {
    const m = stillMetrics(img(16, 9, (x, y) => [x * 16, y * 28, (x * y) % 255]), 16, 9);
    for (const k of ['exposure', 'contrast', 'color', 'detail']) {
      expect(m[k]).toBeGreaterThanOrEqual(0);
      expect(m[k]).toBeLessThanOrEqual(1);
    }
  });

  it('copes with empty input', () => {
    expect(stillMetrics([], 0, 0).exposure).toBe(0);
  });
});

describe('scoreStill', () => {
  const m = { exposure: 1, contrast: 0.5, color: 0, detail: 0.5, luma: 0.5 };

  it('is the weighted mean of the measures', () => {
    expect(scoreStill(m, { exposure: 30, contrast: 25, color: 25, detail: 20 })).toBeCloseTo((30 + 12.5 + 10) / 100);
    expect(scoreStill(m, { exposure: 0, contrast: 0, color: 50, detail: 0 })).toBe(0);
  });

  it('pushes near-black and near-white frames down', () => {
    expect(scoreStill({ ...m, luma: 0.03 })).toBeCloseTo(scoreStill(m) * 0.2);
    expect(scoreStill({ ...m, luma: 0.97 })).toBeCloseTo(scoreStill(m) * 0.2);
  });

  it('is 0 when every weight is 0 or there are no measures', () => {
    expect(scoreStill(m, { exposure: 0, contrast: 0, color: 0, detail: 0 })).toBe(0);
    expect(scoreStill(null)).toBe(0);
  });
});

describe('cleanWeights', () => {
  it('fills defaults and clamps to 0..50', () => {
    expect(cleanWeights({ exposure: 80, contrast: -3 })).toEqual({ exposure: 50, contrast: 0, color: 25, detail: 20 });
  });
});

describe('ranking and poster stills', () => {
  const stills = [
    { src: 'a1', scene: 1, source: 'a' },
    { src: 'a2', scene: 1, source: 'a' },
    { src: 'b1', scene: 2, source: 'b' },
    { src: 'c1', scene: 3, source: 'a' },
    { src: 'x', scene: 4, source: 'c' }, // never measured
  ];
  const at = (v) => ({ exposure: v, contrast: v, color: v, detail: v, luma: 0.5 });
  const metrics = { a1: at(0.9), a2: at(0.8), b1: at(0.5), c1: at(0.7) };

  it('ranks measured stills best first and drops the rest', () => {
    expect(rankStills(stills, metrics).map((s) => s.src)).toEqual(['a1', 'a2', 'c1', 'b1']);
  });

  it('re-ranks with the weights', () => {
    const m = { ...metrics, b1: { exposure: 1, contrast: 0, color: 0, detail: 0, luma: 0.5 } };
    const r = rankStills(stills, m, { exposure: 50, contrast: 0, color: 0, detail: 0 });
    expect(r[0].src).toBe('b1');
  });

  it('fills a poster from other sources, then other scenes', () => {
    const r = rankStills(stills, metrics);
    expect(posterSlots('frame')).toBe(1);
    expect(posterStills(r, 'frame').map((s) => s.src)).toEqual(['a1']);
    expect(posterStills(r, 'strip').map((s) => s.src)).toEqual(['a1', 'b1', 'c1']);
    expect(posterStills(r, 'grid').map((s) => s.src)).toEqual(['a1', 'b1', 'c1', 'a2']);
  });

  it('puts the picks first', () => {
    const r = rankStills(stills, metrics);
    expect(posterStills(r, 'strip', ['c1', 'gone']).map((s) => s.src)).toEqual(['c1', 'b1', 'a1']);
  });
});
