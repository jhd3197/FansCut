import { describe, expect, it } from 'vitest';
import { bar, checkRange, episodeTrack, groupSegments, nudge } from './timeline.js';
import { fmtTime, parseTime } from './time.js';

describe('timeline math', () => {
  it('places a range as percentages and clamps to the episode', () => {
    expect(bar(60, 120, 1200)).toEqual({ left: 5, width: 5 });
    expect(bar(-10, 5000, 1200)).toEqual({ left: 0, width: 100 });
    expect(bar(10, 20, 0)).toEqual({ left: 0, width: 0 });
  });

  it('nudges one edge, never crossing the other or leaving the episode', () => {
    expect(nudge({ start: 10, end: 100 }, 'start', -1, 1440)).toEqual({ start: 9, end: 100 });
    expect(nudge({ start: 0.2, end: 100 }, 'start', -5, 1440).start).toBe(0);
    expect(nudge({ start: 10, end: 10.6 }, 'start', 5, 1440).start).toBe(10.1);
    expect(nudge({ start: 10, end: 1439 }, 'end', 5, 1440).end).toBe(1440);
  });

  it('validates typed ranges like the server does', () => {
    expect(checkRange(10, 90, 1440)).toBeNull();
    expect(checkRange(null, 90, 1440)).toMatch(/both/);
    expect(checkRange(90, 90, 1440)).toMatch(/after start/);
    expect(checkRange(-1, 90, 1440)).toMatch(/negative/);
    expect(checkRange(10, 2000, 1440)).toMatch(/past the end/);
  });

  it('groups segments by episode and builds a track with kept pieces and trim markers', () => {
    const segs = [
      { episode_id: 'S01E01', start: 0, end: 600 },
      { episode_id: 'S01E02', start: 90, end: 1380 },
      { episode_id: 'S01E01', start: 700, end: 1000 },
    ];
    expect(Object.keys(groupSegments(segs))).toEqual(['S01E01', 'S01E02']);
    const ep = { duration: 1200, trims: { intro: { start: 600, end: 700 }, outro: { start: null, end: null } } };
    const t = episodeTrack(ep, groupSegments(segs).S01E01);
    expect(t.kept.map((k) => k.width)).toEqual([50, 25]);
    expect(t.trims).toHaveLength(1); // a cleared trim draws nothing
    expect(t.trims[0]).toMatchObject({ kind: 'intro', left: 50 });
  });
});

describe('time formatting', () => {
  it('formats and parses m:ss and h:mm:ss', () => {
    expect(fmtTime(65)).toBe('1:05');
    expect(fmtTime(3725)).toBe('1:02:05');
    expect(fmtTime(7.5, 1)).toBe('0:07.5');
    expect(fmtTime(null)).toBe('–');
    expect(parseTime('1:30')).toBe(90);
    expect(parseTime('1:00:05.5')).toBe(3605.5);
    expect(parseTime('90')).toBe(90);
    expect(parseTime('abc')).toBeNull();
    expect(parseTime('')).toBeNull();
  });
});

it('formats runtimes, short ones in seconds', async () => {
  const { fmtDuration } = await import('./time.js');
  expect(fmtDuration(45)).toBe('45s');
  expect(fmtDuration(1440)).toBe('24m');
  expect(fmtDuration(5400)).toBe('1h 30m');
});
