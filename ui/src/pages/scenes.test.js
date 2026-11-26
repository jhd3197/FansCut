import { describe, expect, it } from 'vitest';
import {
  buildScenes, dedupeMoments, edgeNote, hasPerson, isAI, midLine, nextCut, personKeys, prevCut, segWindow,
  snapEdge, speechLines, tagGroups, trimSegment,
} from './Scenes.jsx';

describe('scenes from tag rows', () => {
  it('groups rows over the same range: accepted tags, AI suggestions, every id', () => {
    const rows = [
      { id: 'a', start: 100, end: 200, tags: ['christmas', 'family'], status: 'accepted', summary: 'Presents' },
      { id: 'b', start: 102, end: 198, tags: ['magic', 'family'], status: 'proposed', confidence: 0.7 },
      { id: 'c', start: 300, end: 400, tags: ['night'], status: 'proposed', summary: 'The cloak' },
      { id: 'd', start: 300, end: 400, tags: ['night'], status: 'rejected' },
      { id: 'e', start: 500, end: 510, tags: ['fc:no-face'], status: 'accepted' },
    ];
    const s = buildScenes(rows);
    expect(s.map((x) => x.id)).toEqual(['a', 'c']);
    expect(s[0]).toMatchObject({ name: 'Presents', tags: ['christmas', 'family'], ai: ['magic'], start: 100, end: 200 });
    expect(s[0].ids.sort()).toEqual(['a', 'b']);
    expect(s[1].kept).toEqual([]);
    expect(s[1].ai).toEqual(['night']);
  });
});

describe('clean edges', () => {
  const lines = speechLines([
    { start: 10, end: 14, text: 'Happy Christmas, Harry.' },
    { start: 14.5, end: 16, text: '[door creaks]' },
    { start: 20, end: 25, text: 'What is it?' },
  ]);
  it('leaves sound descriptions out of speech', () => {
    expect(lines).toHaveLength(2);
    expect(midLine(lines, 12)?.start).toBe(10);
    expect(midLine(lines, 10.02)).toBeNull();
    expect(midLine(lines, 17)).toBeNull();
  });
  it('moves a start before the line, onto a quiet cut first', () => {
    expect(snapEdge(12, { later: false, lines, cuts: [8, 9.5], silence: [[7, 9]] })).toEqual({ t: 8, why: 'cut' });
    expect(snapEdge(12, { later: false, lines, cuts: [9.5] })).toEqual({ t: 9.5, why: 'cut' });
  });
  it('moves an end past the line: quiet, then the pause, then the line edge', () => {
    expect(snapEdge(22, { later: true, lines, cuts: [], silence: [[26, 30]] })).toEqual({ t: 26.5, why: 'quiet' });
    expect(snapEdge(22, { later: true, lines, cuts: [] })).toEqual({ t: 25.3, why: 'pause' });
    expect(snapEdge(22, { later: true, lines, cuts: [], hi: 25.1 })).toEqual({ t: 25.05, why: 'line' });
    expect(snapEdge(22, { later: true, lines, cuts: [], hi: 23 })).toBeNull();
  });
  it('says what a clean edge lands on', () => {
    expect(edgeNote(12, { lines })).toBeNull();
    expect(edgeNote(17, { lines, silence: [[16, 19]] })).toBe('Lands in quiet');
    expect(edgeNote(18, { lines, cuts: [18.1] })).toBe('Lands on a camera cut');
    expect(edgeNote(18, { lines })).toBe('Lands between lines');
  });
});

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


describe('scenes editor helpers', () => {
  it('keeps one of two overlapping moments with the same tags, accepted first', () => {
    const rows = [
      { id: 'a', start: 0, end: 100, tags: ['snow', 'night'], status: 'proposed', confidence: 0.9 },
      { id: 'b', start: 20, end: 110, tags: ['night', 'snow'], status: 'accepted', confidence: 0.6 },
      { id: 'c', start: 90, end: 200, tags: ['snow', 'night'], status: 'proposed' },
      { id: 'd', start: 0, end: 100, tags: ['snow'], status: 'proposed' },
    ];
    const out = dedupeMoments(rows);
    expect(out.map((x) => x.id)).toEqual(['d', 'b', 'c']);
    expect(out.find((x) => x.id === 'b').dups).toEqual(['a']);
  });
  it('matches a person by actor or character, case-insensitively', () => {
    const cast = [{ name: 'Rupert Grint', character: 'Ron Weasley' }];
    const keys = personKeys('rupert grint', cast);
    expect(hasPerson({ people: [{ name: null, character: 'RON WEASLEY' }] }, keys)).toBe(true);
    expect(hasPerson({ people: [{ name: 'Rupert Grint' }] }, keys)).toBe(true);
    expect(hasPerson({ people: [{ name: 'Emma Watson', character: 'Hermione' }] }, keys)).toBe(false);
    expect(personKeys('', cast)).toBeNull();
  });
  it('flags AI moments and groups by tag, most used first', () => {
    expect(isAI({ source: 'ai:claude' })).toBe(true);
    expect(isAI({ source: 'user' })).toBe(false);
    const g = tagGroups([{ tags: ['snow', 'fc:no-face'] }, { tags: ['snow', 'feast'] }], new Map([['snow', 'Snow']]));
    expect(g.map((x) => [x.tag, x.name, x.rows.length])).toEqual([['snow', 'Snow', 2], ['feast', 'Feast', 1]]);
  });
  it('loads a segment with a margin, in 15 s steps', () => {
    expect(segWindow(100, 130)).toEqual([75, 150]);
    expect(segWindow(5, 1005, 1050)).toEqual([0, 1050]);
  });
  it('trims one segment of a recipe and refuses an empty range', () => {
    const doc = { parts: [{ segments: [{ src: 'M1', start: 0, end: 10 }] }, { segments: [{ src: 'M1', start: 50, end: 60 }] }] };
    const next = trimSegment(doc, 2, 48.5, 70, 65);
    expect(next.parts[1].segments[0]).toEqual({ src: 'M1', start: 48.5, end: 65 });
    expect(doc.parts[1].segments[0].start).toBe(50);
    expect(() => trimSegment(doc, 2, 60, 60.2)).toThrow();
    expect(() => trimSegment(doc, 3, 0, 1)).toThrow();
  });
});
