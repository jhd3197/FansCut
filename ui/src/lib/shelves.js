// @ts-check
// Discover's shelves, in editorial order (the redesign):
//   1. the season's shelf, pinned while it is that time of year ("For Christmas")
//   2. genre shelves for merges and cuts ("Fantasy", "Action"), biggest first
//   3. "Watch in story order" (playlists), "Character stories", "Short and fun", "Anime"
// A suggestion sits on one of 2–3; a seasonal one also shows on the pinned shelf.

export const SEASON_SHELF = {
  christmas: { name: 'For Christmas', from: [11, 15], to: [12, 31] },
  halloween: { name: 'For Halloween', from: [10, 1], to: [10, 31] },
};

const TAIL = ['Watch in story order', 'Character stories', 'Short and fun', 'Anime'];

/** Is `date` inside a season's window (month/day, inclusive)? */
export function inSeason(season, date = new Date()) {
  const w = SEASON_SHELF[season];
  if (!w) return false;
  const md = (date.getMonth() + 1) * 100 + date.getDate();
  return md >= w.from[0] * 100 + w.from[1] && md <= w.to[0] * 100 + w.to[1];
}

/** The one shelf a suggestion lives on (besides a seasonal one). */
export function shelfOf(s) {
  if (s.kind === 'watch_order') return 'Watch in story order';
  if (s.kind === 'canon_cut' || /anime/i.test(s.category || '')) return 'Anime';
  if (/character/i.test(s.category || '') || s.poster_kind === 'character') return 'Character stories';
  if (/short|fun/i.test(s.category || '')) return 'Short and fun';
  return s.genres?.[0] || (s.category && s.category !== 'Community cuts' && s.category !== 'Merges' ? s.category : 'More cuts');
}

/**
 * @param {Array<any>} items suggestions and catalogue entries
 * @param {Date} [date]
 * @returns {Array<{name: string, items: Array<any>, seasonal?: boolean}>}
 */
export function discoverShelves(items, date = new Date()) {
  const seasonal = new Map();
  const byShelf = new Map();
  for (const s of items) {
    if (s.season && inSeason(s.season, date)) {
      const name = SEASON_SHELF[s.season].name;
      if (!seasonal.has(name)) seasonal.set(name, []);
      seasonal.get(name).push(s);
    }
    const name = shelfOf(s);
    if (!byShelf.has(name)) byShelf.set(name, []);
    byShelf.get(name).push(s);
  }
  // a genre with a single cut is a thin shelf: it joins "More cuts" at the end
  for (const n of [...byShelf.keys()]) {
    if (TAIL.includes(n) || n === 'More cuts' || byShelf.get(n).length > 1) continue;
    if (!byShelf.has('More cuts')) byShelf.set('More cuts', []);
    byShelf.get('More cuts').push(...byShelf.get(n));
    byShelf.delete(n);
  }
  const genres = [...byShelf.keys()].filter((n) => !TAIL.includes(n) && n !== 'More cuts')
    .sort((a, b) => byShelf.get(b).length - byShelf.get(a).length || a.localeCompare(b));
  const order = [...genres, ...TAIL, 'More cuts'].filter((n) => byShelf.has(n));
  return [
    ...[...seasonal].map(([name, list]) => ({ name, items: list, seasonal: true })),
    ...order.map((name) => ({ name, items: byShelf.get(name) })),
  ];
}
