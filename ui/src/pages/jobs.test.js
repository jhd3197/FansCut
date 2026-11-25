import { describe, expect, it } from 'vitest';
import { etaSeconds, fmtLeft } from './Jobs.jsx';

describe('etaSeconds', () => {
  const first = { t: 0, p: 0.1 };

  it('waits for 10 s and 2 points of progress', () => {
    expect(etaSeconds(null, 60000, 0.5)).toBeNull();
    expect(etaSeconds(first, 5000, 0.5)).toBeNull();
    expect(etaSeconds(first, 60000, 0.11)).toBeNull();
  });

  it('extrapolates the rate seen so far', () => {
    // 10% -> 40% in 60 s is 0.5% a second: 60% left takes 120 s
    expect(etaSeconds(first, 60000, 0.4)).toBeCloseTo(120);
  });

  it('has nothing left at 100%', () => {
    expect(etaSeconds(first, 60000, 1)).toBeNull();
  });
});

describe('fmtLeft', () => {
  it('reads like the design', () => {
    expect(fmtLeft(null)).toBeNull();
    expect(fmtLeft(30)).toBe('under a minute left');
    expect(fmtLeft(4 * 60)).toBe('about 4 m left');
    expect(fmtLeft(70 * 60)).toBe('about 1 h 10 m left');
  });
});
