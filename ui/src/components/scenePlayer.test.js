import { describe, expect, it } from 'vitest';
import { clipWindows, namedFaces, tagsAt, visibleRows } from './ScenePlayer.jsx';
import { filmsBySource, nextPlayable } from '../pages/CutScenes.jsx';

describe('scenes player helpers', () => {
  it('splits a long range into 5-minute clips', () => {
    expect(clipWindows(10, 100)).toEqual([[10, 100]]);
    expect(clipWindows(0, 700)).toEqual([[0, 300], [300, 600], [600, 700]]);
  });
  it('keeps measured rows out and lists the tags at t once', () => {
    const rows = [
      { start: 0, end: 60, tags: ['fc:no-face'], status: 'proposed' },
      { start: 0, end: 60, tags: ['christmas', 'fc:close-up'], status: 'accepted' },
      { start: 10, end: 20, tags: ['christmas', 'snow'], status: 'proposed' },
      { start: 10, end: 20, tags: ['feast'], status: 'rejected' },
      { start: 0, end: 60, tags: ['fc:filler'], status: 'accepted' },
    ];
    expect(visibleRows(rows)).toHaveLength(4);
    expect(tagsAt(rows, 15)).toEqual(['christmas', 'snow', 'fc:filler']);
    expect(tagsAt(rows, 30)).toEqual(['christmas', 'fc:filler']);
  });
  it('shows matched faces only, once each, sure first', () => {
    const faces = [{ name: null }, { name: 'A', sure: false }, { name: 'B', sure: true }, { name: 'A', sure: true }];
    expect(namedFaces(faces).map((f) => [f.name, f.sure])).toEqual([['B', true], ['A', true]]);
  });
  it('maps sources to films and skips segments that cannot play', () => {
    const doc = { sources: { M1: { kind: 'movie', tmdb: 1 }, M2: { kind: 'movie', tmdb: 2 } } };
    const films = [{ key: 'a', tmdb_id: 1, on_disk: true }, { key: 'b', tmdb_id: 1, indexed: true }];
    const by = filmsBySource(doc, films);
    expect(by.M1.key).toBe('b');
    expect(by.M2).toBeNull();
    const segs = [{ n: 1, seg: { src: 'M1' } }, { n: 2, seg: { src: 'M2' } }, { n: 3, seg: { src: 'M1' } }];
    expect(nextPlayable(segs, 1, by)).toBe(3);
    expect(nextPlayable(segs, 3, by)).toBeNull();
  });
});
