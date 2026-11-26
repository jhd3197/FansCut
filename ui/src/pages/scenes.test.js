import { describe, expect, it } from 'vitest';
import { nextCut, prevCut } from './Scenes.jsx';

const shots = [[0, 4.2], [4.2, 9.8], [9.8, 20]];

describe('cut nudging', () => {
  it('finds the cut before a time', () => {
    expect(prevCut(shots, 9.8)).toBe(4.2);
    expect(prevCut(shots, 5)).toBe(4.2);
    expect(prevCut(shots, 1)).toBe(0);
  });
  it('finds the cut after a time, or the end', () => {
    expect(nextCut(shots, 4.2, 20)).toBe(9.8);
    expect(nextCut(shots, 15, 20)).toBe(20);
    expect(nextCut(shots, 20, 20)).toBe(20);
  });
});
