import { describe, expect, it } from 'vitest';
import { joinCheck, linesIn, moveAcrossParts, quietAfter, quietBefore } from './joins.js';

const A = { src: 'M1', start: 100, end: 160 };
const B = { src: 'M2', start: 500, end: 540 };
const line = (start, end) => ({ start, end, text: 'x' });

describe('joinCheck', () => {
  it('is unchecked when either film has no speech data', () => {
    expect(joinCheck(A, B, null, []).state).toBe('unchecked');
    expect(joinCheck(A, B, [], null).state).toBe('unchecked');
  });

  it('flags a last line that runs into the cut, and the fix gives it air', () => {
    const j = joinCheck(A, B, [line(150, 161)], []);
    expect(j.state).toBe('check');
    expect(j.short).toBe('Last line runs into the cut');
    expect(j.fix).toEqual({ which: 'a', edge: 'end', delta: 1.7 });
  });

  it('flags a line that ends just before the cut', () => {
    const j = joinCheck(A, B, [line(150, 159.8)], []);
    expect(j.short).toBe('Line ends 0.2s before the cut');
    expect(j.fix.delta).toBeCloseTo(0.5);
  });

  it('flags the next scene starting mid-line', () => {
    const j = joinCheck(A, B, [line(120, 130)], [line(498, 503)]);
    expect(j.state).toBe('check');
    expect(j.fix).toEqual({ which: 'b', edge: 'start', delta: -2.35 });
  });

  it('is clean with room after the last line, or nobody speaking', () => {
    expect(joinCheck(A, B, [line(120, 130)], []).text).toBe('Clean. The cut lands 30.0s after the last line.');
    expect(joinCheck(A, B, [], []).state).toBe('clean');
  });

  it('is clean when the next scene continues the same film', () => {
    expect(joinCheck(A, { src: 'M1', start: 160, end: 200 }, null, null).state).toBe('clean');
  });
});

it('linesIn keeps overlapping lines', () => {
  expect(linesIn([line(1, 2), line(5, 6), line(9, 12)], 5.5, 10)).toHaveLength(2);
});

it('moveAcrossParts moves a row into the part it lands on', () => {
  const rows = [{ pi: 0, seg: 'a' }, { pi: 0, seg: 'b' }, { pi: 1, seg: 'c' }];
  expect(moveAcrossParts(rows, 0, 2, 2)).toEqual([['b'], ['c', 'a']]);
  expect(moveAcrossParts(rows, 1, 0, 2)).toEqual([['b', 'a'], ['c']]);
  expect(moveAcrossParts(rows, 1, 1, 2)).toBeNull();
});

it('finds the first quiet moment around running talk', () => {
  const talk = [line(10, 12), line(12.3, 14), line(14.2, 15), line(20, 22)];
  expect(quietAfter(talk, 11)).toBeCloseTo(15.7);
  expect(quietAfter(talk, 11, 0.7, 2)).toBeNull();
  expect(quietBefore(talk, 13)).toBeCloseTo(9.65);
  expect(quietBefore([], 13)).toBe(13);
});
