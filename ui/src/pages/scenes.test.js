import { describe, expect, it } from 'vitest';
import {
  dedupeMoments, hasPerson, isAI, nextCut, personKeys, prevCut, segWindow, tagGroups, trimSegment,
} from './Scenes.jsx';

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
