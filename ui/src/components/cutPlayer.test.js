import { describe, expect, it } from 'vitest';
import { barBlocks, cutPieces, locate, pieceAt, playableFrom, segmentStart } from './CutPlayer.jsx';

const seg = (n, src, start, end, at) => ({ n, at, dur: end - start, seg: { src, start, end } });
// 1: A 100..160 (60 s), 2: B (not in the library) 0..30, 3: A 1000..1700 (700 s: three clips)
const segs = [seg(1, 'A', 100, 160, 0), seg(2, 'B', 0, 30, 60), seg(3, 'A', 1000, 1700, 90)];
const filmOf = { A: { key: 'film-a' } };

describe('cut player timing', () => {
  const pieces = cutPieces(segs, filmOf);
  it('lays every clip window end to end in cut time', () => {
    expect(pieces.map((p) => [p.n, p.key, p.a, p.b, p.at])).toEqual([
      [1, 'film-a', 100, 160, 0],
      [2, null, 0, 30, 60],
      [3, 'film-a', 1000, 1300, 90],
      [3, 'film-a', 1300, 1600, 390],
      [3, 'film-a', 1600, 1700, 690],
    ]);
  });
  it('maps cut time to a clip and the time inside it', () => {
    expect(pieceAt(pieces, 0)).toBe(0);
    expect(pieceAt(pieces, 59.9)).toBe(0);
    expect(pieceAt(pieces, 400)).toBe(3);
    expect(pieceAt(pieces, 9999)).toBe(4);
    expect(locate(pieces, 30)).toEqual({ i: 0, local: 30 });
    expect(locate(pieces, 400)).toEqual({ i: 3, local: 10 });
    expect(locate(pieces, 790)).toEqual({ i: 4, local: 100 });
  });
  it('jumps over segments that are not in the library', () => {
    expect(locate(pieces, 70)).toEqual({ i: 2, local: 0 });
    expect(playableFrom(pieces, 1)).toBe(2);
    expect(playableFrom(pieces, 5)).toBe(-1);
    expect(playableFrom(pieces, 2, new Set([pieces[2]]))).toBe(3);
    expect(locate(cutPieces([seg(1, 'B', 0, 30, 0)], filmOf), 0)).toBeNull();
  });
  it('finds where a segment starts and draws the bar', () => {
    expect(segmentStart(pieces, 3)).toBe(90);
    expect(segmentStart(pieces, 9)).toBe(0);
    const blocks = barBlocks(segs, filmOf, 790);
    expect(blocks[1]).toMatchObject({ n: 2, on: false });
    expect(blocks[2].left).toBeCloseTo((90 / 790) * 100);
    expect(blocks.reduce((t, b) => t + b.width, 0)).toBeCloseTo(100);
  });
});
