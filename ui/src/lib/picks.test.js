import { describe, expect, it } from 'vitest';
import {
  addPicks, analysed, cutLength, dedupeRows, filmsForCut, inCut, isSure, matchRows, removeAt, removePicks,
  shortTitle, tagCounts, topPeople,
} from './picks.js';

const film = (key, tmdb, year, extra = {}) => ({
  key, title: `Saga and the ${key}`, year, tmdb_id: tmdb, franchises: ['Saga'], indexed: true, on_disk: true,
  tags: { accepted: 1, proposed: 0 }, ...extra,
});
const blank = { schema: 2, kind: 'timeline', title: 'Snow', series: { title: 'Saga' }, sources: {}, parts: [] };
const src = (tmdb) => ({ kind: 'movie', tmdb, note: `film ${tmdb}` });

describe('films for a cut', () => {
  it('takes the franchise from series.title, one copy per film, in year order', () => {
    const films = [film('b', 2, 2004), film('a', 1, 2001), film('a4k', 1, 2001, { indexed: false, tags: { accepted: 0, proposed: 0 } }),
      { key: 'x', title: 'Other', year: 2000, tmdb_id: 9, franchises: ['Else'] }];
    const out = filmsForCut(blank, films);
    expect(out.franchise).toBe('Saga');
    expect(out.films.map((f) => f.key)).toEqual(['a', 'b']);
  });

  it('falls back to the franchise of the films already in the cut', () => {
    const doc = { ...blank, series: { title: 'Unknown' }, sources: { M2: src(2) } };
    const out = filmsForCut(doc, [film('a', 1, 2001), film('b', 2, 2004)]);
    expect(out.franchise).toBe('Saga');
    expect(out.films).toHaveLength(2);
  });

  it('shortens film names inside their franchise', () => {
    expect(shortTitle("Harry Potter and the Sorcerer's Stone", 'Harry Potter')).toBe("Sorcerer's Stone");
    expect(shortTitle('Alien: Covenant', 'Alien')).toBe('Covenant');
    expect(shortTitle('Inception', 'Nolan')).toBe('Inception');
    expect(shortTitle('Alien', 'Alien')).toBe('Alien');
  });

  it('knows an analysed film from one that is only indexed', () => {
    expect(analysed(film('a', 1, 2001))).toBe(true);
    expect(analysed(film('a', 1, 2001, { tags: { accepted: 0, proposed: 0 } }))).toBe(false);
    expect(analysed(film('a', 1, 2001, { indexed: false }))).toBe(false);
  });
});

describe('scene rows', () => {
  const rows = [
    { id: '1', film_key: 'a', start: 10, end: 20, tags: ['snow', 'night'], people: [{ name: 'D', character: 'Harry' }], status: 'accepted' },
    { id: '2', film_key: 'a', start: 11, end: 20, tags: ['snow'], people: [], status: 'proposed', confidence: 0.9 },
    { id: '3', film_key: 'b', start: 50, end: 80, tags: ['snow', 'fire'], people: ['Ron', { name: 'R', character: 'Harry' }], status: 'proposed', confidence: 0.5 },
    { id: '4', film_key: 'b', start: 90, end: 99, tags: ['fire'], people: [], status: 'rejected' },
  ];

  it('splits sure scenes from maybes at the threshold', () => {
    expect(rows.map((r) => isSure(r, 0.75))).toEqual([true, true, false, false]);
    expect(isSure({ status: 'proposed', confidence: null }, 0.75)).toBe(false);
  });

  it('keeps the surest of overlapping rows and drops rejected ones', () => {
    expect(dedupeRows(rows).map((r) => r.id)).toEqual(['1', '3']);
  });

  it('counts people and tags, and filters on films, tags and a person', () => {
    expect(topPeople(rows)).toEqual([{ id: 'Harry', n: 2 }, { id: 'Ron', n: 1 }]);
    expect(tagCounts(rows, (t) => t !== 'night')[0]).toEqual({ tag: 'snow', n: 3 });
    expect(matchRows(rows, { tags: ['snow', 'fire'] }).map((r) => r.id)).toEqual(['3']);
    expect(matchRows(rows, { person: 'Ron' }).map((r) => r.id)).toEqual(['3']);
    expect(matchRows(rows, { off: new Set(['a']), tags: ['snow'] }).map((r) => r.id)).toEqual(['3']);
  });
});

describe('ticking scenes in and out', () => {
  const pick = (tmdb, start, end) => ({ src: `M${tmdb}`, start, end, desc: 'x', source: src(tmdb) });

  it('adds a part, the source and the segment once', () => {
    let doc = addPicks(blank, [pick(1, 10, 20)]);
    doc = addPicks(doc, [pick(1, 10.2, 20), pick(2, 5, 9)]);
    expect(doc.parts).toHaveLength(1);
    expect(doc.parts[0].segments.map((s) => [s.src, s.start, s.end])).toEqual([['M1', 10, 20], ['M2', 5, 9]]);
    expect(Object.keys(doc.sources)).toEqual(['M1', 'M2']);
    expect(blank.parts).toHaveLength(0);
    expect(inCut(doc, 'M1', { start: 12, end: 19 })).toBe(true);
    expect(inCut(doc, 'M1', { start: 30, end: 40 })).toBe(false);
    expect(cutLength(doc)).toBe(14);
  });

  it('removes covering segments and prunes unused sources unless told not to', () => {
    const doc = addPicks(blank, [pick(1, 10, 20), pick(2, 5, 9)]);
    const out = removePicks(doc, [{ src: 'M2', start: 5, end: 9 }]);
    expect(out.parts[0].segments).toHaveLength(1);
    expect(Object.keys(out.sources)).toEqual(['M1']);
    expect(Object.keys(removeAt(doc, 0, { prune: false }).sources)).toEqual(['M1', 'M2']);
  });
});
