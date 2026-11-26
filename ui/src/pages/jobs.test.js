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

describe('build phases', () => {
  it('shows the five phases with the running one detailed', async () => {
    const { stages } = await import('./Jobs.jsx');
    const job = { kind: 'timeline_render', status: 'running', phase: { i: 3, n: 5, name: 'Cut and join', detail: 'part 2 of 3 · re-encoded' } };
    const s = stages(job);
    expect(s.map((x) => x[1])).toEqual(['Done', 'Done', 'Running', 'Waiting', 'Waiting']);
    expect(s[2][2]).toBe('part 2 of 3 · re-encoded');
    expect(stages({ ...job, status: 'done' }).every((x) => x[1] === 'Done')).toBe(true);
  });

  it('words an estimate', async () => {
    const { about } = await import('./CutDetail.jsx');
    expect(about(40)).toBe('about a minute');
    expect(about(35 * 60)).toBe('about 35 min');
    expect(about(2 * 3600 + 9 * 60)).toBe('about 2 h 10 min');
    expect(about(2 * 3600)).toBe('about 2 h');
    const { gb } = await import('./CutDetail.jsx');
    expect(gb(30.4e9)).toBe('about 30 GB');
    expect(gb(812e6)).toBe('about 800 MB');
    expect(gb(0)).toBe('');
  });
});
