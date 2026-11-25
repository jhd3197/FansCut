import { describe, expect, it } from 'vitest';
import { discoverShelves, inSeason, shelfOf } from './shelves.js';

const merge = (name, genres, extra = {}) => ({ id: `merge:${name}`, kind: 'collection_merge', name, genres, ...extra });

describe('Discover shelves', () => {
  it('knows when a season is on', () => {
    expect(inSeason('christmas', new Date(2026, 10, 25))).toBe(true); // Nov 25
    expect(inSeason('christmas', new Date(2026, 10, 1))).toBe(false);
    expect(inSeason('halloween', new Date(2026, 9, 31))).toBe(true);
    expect(inSeason('easter', new Date(2026, 3, 1))).toBe(false);
  });

  it('puts each kind on its shelf', () => {
    expect(shelfOf({ kind: 'watch_order', genres: ['Fantasy'] })).toBe('Watch in story order');
    expect(shelfOf({ kind: 'canon_cut', genres: ['Animation'] })).toBe('Anime');
    expect(shelfOf({ kind: 'recipe', category: 'Character', genres: ['Fantasy'] })).toBe('Character stories');
    expect(shelfOf(merge('The Hobbit', ['Adventure', 'Fantasy']))).toBe('Adventure');
    expect(shelfOf({ kind: 'recipe', category: 'Community cuts' })).toBe('More cuts');
  });

  it('pins the season, then genres by size, then the fixed tail', () => {
    const items = [
      merge('Home Alone', ['Comedy'], { season: 'christmas' }),
      merge('The Hobbit', ['Fantasy']),
      merge('LOTR', ['Fantasy']),
      merge('Alien', ['Horror']),
      { id: 'order:x', kind: 'watch_order', name: 'x' },
      { id: 'canon:aot', kind: 'canon_cut', name: 'AoT' },
    ];
    const nov = discoverShelves(items, new Date(2026, 10, 25)).map((s) => s.name);
    // Comedy and Horror have one cut each: they fold into "More cuts" at the end
    expect(nov).toEqual(['For Christmas', 'Fantasy', 'Watch in story order', 'Anime', 'More cuts']);
    const jun = discoverShelves(items, new Date(2026, 5, 1)).map((s) => s.name);
    expect(jun[0]).toBe('Fantasy');
  });
});
