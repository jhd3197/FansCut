// @ts-check
// Find scenes: which films a cut may use, which tagged scenes match the filters, and
// ticking scenes in and out of the cut's recipe. Kept free of React so it can be tested.

/** @typedef {{ key: string, title: string, year?: number, tmdb_id?: number, duration?: number,
 *   on_disk?: boolean, indexed?: boolean, franchises?: string[], tags?: { proposed: number, accepted: number } }} Film */
/** @typedef {{ id: string, film_key: string, start: number, end: number, tags: string[],
 *   people?: ({ name?: string, character?: string } | string)[], summary?: string, status: string,
 *   confidence?: number | null }} SceneRow */

const len = (x) => Math.max(0, x.end - x.start);

// "Harry Potter and the Sorcerer's Stone" -> "Sorcerer's Stone" inside the Harry Potter cut.
export function shortTitle(title, franchise) {
  const t = String(title || '');
  const f = String(franchise || '').trim();
  if (!f || !t.toLowerCase().startsWith(f.toLowerCase())) return t;
  const rest = t.slice(f.length).replace(/^\s*(?:[:\-–]\s*|and the\s+|and\s+)/i, '').trim();
  return rest || t;
}

// One library copy per film (the indexed one, else the one on disk), in year order.
function onePerFilm(list) {
  const rank = (f) => (f.indexed ? 2 : 0) + (f.on_disk ? 1 : 0) + ((f.tags?.accepted || 0) + (f.tags?.proposed || 0) > 0 ? 4 : 0);
  const by = new Map();
  for (const f of list) {
    const k = f.tmdb_id ? `t${f.tmdb_id}` : `k${f.key}`;
    const had = by.get(k);
    if (!had || rank(f) > rank(had)) by.set(k, f);
  }
  return [...by.values()].sort((a, b) => (a.year || 0) - (b.year || 0) || a.title.localeCompare(b.title));
}

// The films a cut may use: its franchise (series.title), else the franchise of the films
// already in it, else just those films. Cuts never cross franchises.
/** @param {any} doc @param {Film[]} films */
export function filmsForCut(doc, films) {
  const all = films || [];
  const tmdbs = Object.values(doc?.sources || {}).filter((s) => s?.kind === 'movie').map((s) => String(s.tmdb));
  const inCutFilms = all.filter((f) => tmdbs.includes(String(f.tmdb_id)));
  let franchise = doc?.series?.title || '';
  let list = franchise ? all.filter((f) => (f.franchises || []).includes(franchise)) : [];
  if (!list.length) {
    const fr = inCutFilms.flatMap((f) => f.franchises || [])[0];
    if (fr) {
      franchise = fr;
      list = all.filter((f) => (f.franchises || []).includes(fr));
    } else {
      list = inCutFilms;
    }
  }
  return { franchise, films: onePerFilm(list) };
}

export const sourceKey = (film) => `M${film.tmdb_id}`;

// Analysed = indexed and tagged at least once.
export const analysed = (f) => !!f.indexed && ((f.tags?.accepted || 0) + (f.tags?.proposed || 0)) > 0;

// Sure enough to show with the results: a person accepted it, or the AI was at least
// `threshold` sure (Settings, scenes.auto_accept). Anything else is a Maybe.
export const isSure = (row, threshold) => row.status === 'accepted'
  || (row.status === 'proposed' && row.confidence != null && row.confidence >= threshold);

// How much two ranges overlap, as a share of the shorter one.
export function overlapShare(a, b) {
  const o = Math.min(a.end, b.end) - Math.max(a.start, b.start);
  const m = Math.min(len(a), len(b));
  return o > 0 && m > 0 ? o / m : 0;
}

// Rows of one film that cover the same moment: keep the surest (accepted first).
/** @param {SceneRow[]} rows */
export function dedupeRows(rows) {
  const score = (r) => (r.status === 'accepted' ? 2 : 1) + (r.confidence ?? 0.5) / 10;
  const kept = [];
  for (const r of [...rows].filter((x) => x.status !== 'rejected').sort((a, b) => score(b) - score(a))) {
    if (!kept.some((k) => k.film_key === r.film_key && overlapShare(k, r) > 0.8)) kept.push(r);
  }
  return kept.sort((a, b) => a.start - b.start);
}

// A person as the scene lists them: the character, else the actor.
export const personId = (p) => (typeof p === 'string' ? p : p?.character || p?.name || '').trim();

// A ?person= from another page may name the actor ("Robbie Coltrane"): the id the
// scenes use for them (their character), or the value as it is.
export function resolvePerson(rows, person) {
  if (!person) return '';
  for (const r of rows) for (const p of r.people || []) {
    if (typeof p === 'object' && p && (p.name === person || p.character === person)) return personId(p);
  }
  return person;
}

// The most frequent people across rows: [{ id, n }].
export function topPeople(rows, max = 8) {
  const n = new Map();
  for (const r of rows) for (const id of new Set((r.people || []).map(personId).filter(Boolean))) n.set(id, (n.get(id) || 0) + 1);
  return [...n.entries()].map(([id, c]) => ({ id, n: c })).sort((a, b) => b.n - a.n || a.id.localeCompare(b.id)).slice(0, max);
}

// Tag counts across rows: [{ tag, n }], most used first. `shown` filters out hidden ids.
export function tagCounts(rows, shown = () => true) {
  const n = new Map();
  for (const r of rows) for (const t of new Set(r.tags || [])) if (shown(t)) n.set(t, (n.get(t) || 0) + 1);
  return [...n.entries()].map(([tag, c]) => ({ tag, n: c })).sort((a, b) => b.n - a.n || a.tag.localeCompare(b.tag));
}

// Rows that pass the filters: films left out, every picked tag, the picked person.
export function matchRows(rows, { off = new Set(), tags = [], person = '' } = {}) {
  return rows.filter((r) => !off.has(r.film_key)
    && tags.every((t) => (r.tags || []).includes(t))
    && (!person || (r.people || []).some((p) => personId(p) === person)));
}

// -- the cut's recipe ----------------------------------------------------------------

// Every segment in play order with where it sits ({ pi, si, seg }).
export function segList(doc) {
  const out = [];
  (doc?.parts || []).forEach((p, pi) => (p.segments || []).forEach((seg, si) => out.push({ pi, si, seg })));
  return out;
}

// Is the range [start, end] of `src` in the cut (a segment covering most of it)?
export function inCut(doc, src, range) {
  return segList(doc).some(({ seg }) => seg.src === src && overlapShare(seg, range) >= 0.5);
}

// Add picks to the end of the last part (Arrange orders them). Each pick:
// { src, start, end, desc?, source: { kind: 'movie', tmdb, note, duration? } }. Picks already
// in the cut are skipped. Returns a new doc.
export function addPicks(doc, picks) {
  const next = structuredClone(doc);
  next.sources ||= {};
  next.parts ||= [];
  if (!next.parts.length) next.parts.push({ title: next.title || 'Part 1', segments: [] });
  const part = next.parts[next.parts.length - 1];
  part.segments ||= [];
  for (const p of picks) {
    if (inCut(next, p.src, p)) continue;
    if (!next.sources[p.src]) next.sources[p.src] = p.source;
    const seg = { src: p.src, start: round3(p.start), end: round3(p.end) };
    if (p.desc) seg.desc = String(p.desc).slice(0, 200);
    part.segments.push(seg);
  }
  return next;
}

// Take every segment covering one of `ranges` ({ src, start, end }) out of the cut. Empty
// parts go; with `prune`, so do sources no segment uses any more. Returns a new doc.
export function removePicks(doc, ranges, { prune = true } = {}) {
  const next = structuredClone(doc);
  const hit = (seg) => ranges.some((r) => r.src === seg.src && overlapShare(seg, r) >= 0.5);
  next.parts = (next.parts || []).map((p) => ({ ...p, segments: (p.segments || []).filter((s) => !hit(s)) }));
  if (next.parts.length > 1) next.parts = next.parts.filter((p) => p.segments.length);
  if (prune) {
    const used = new Set(segList(next).map(({ seg }) => seg.src));
    for (const k of Object.keys(next.sources || {})) if (!used.has(k) && next.sources[k]?.kind === 'movie') delete next.sources[k];
  }
  return next;
}

// Take segment number i (0-based, play order) out of the cut.
export function removeAt(doc, i, opts) {
  const s = segList(doc)[i];
  return s ? removePicks(doc, [s.seg], opts) : doc;
}

function round3(v) { return Math.round(v * 1000) / 1000; }

// Total length of the cut in seconds.
export const cutLength = (doc) => segList(doc).reduce((t, { seg }) => t + len(seg), 0);

// -- trailers ------------------------------------------------------------------------

export const TRAILER_LENGTHS = [60, 120, 150];
export const ACTS = ['Open', 'Build', 'Peak', 'Close'];

// The act a shot falls in, by where it sits in the film.
export function actOf(t, duration) {
  const x = duration > 0 ? t / duration : 0;
  return x < 0.2 ? 0 : x < 0.6 ? 1 : x < 0.9 ? 2 : 3;
}

// The shot a tagged moment gives a trailer: its longest camera shot (from the film's
// index), cut to `max` seconds around its middle; without shots, the moment's first seconds.
export function shotFor(row, shots, { min = 2, max = 8 } = {}) {
  const inside = (shots || []).filter(([a, b]) => a >= row.start - 0.05 && b <= row.end + 0.05 && b > a);
  let [a, b] = inside.length
    ? inside.reduce((best, s) => (s[1] - s[0] > best[1] - best[0] ? s : best))
    : [row.start, Math.min(row.end, row.start + 6)];
  if (b - a > max) { const mid = (a + b) / 2; a = mid - max / 2; b = mid + max / 2; }
  if (b - a < min) b = Math.min(row.end, a + min);
  return { start: round3(a), end: round3(b) };
}

const SPOILER = new Set(['spoiler', 'fc:spoiler']);
const spoilerRow = (r) => r.status !== 'rejected' && (r.tags || []).some((t) => SPOILER.has(t));

// A moment is a spoiler when it, or a spoiler range over it, is tagged Spoiler.
export function isSpoiler(row, rows) {
  return spoilerRow(row) || (rows || []).some((r) => r.film_key === row.film_key && spoilerRow(r) && overlapShare(r, row) > 0.5);
}

// Shorten every shot by the same share of what it has above `min` seconds (keeping its
// start) until the segments fit in `target`. Returns new segments.
export function trimToFit(segs, target, min = 1.5) {
  const total = segs.reduce((t, s) => t + len(s), 0);
  const spare = segs.reduce((t, s) => t + Math.max(0, len(s) - min), 0);
  if (total <= target || spare <= 0) return segs;
  const share = Math.min(1, (total - target) / spare);
  return segs.map((s) => {
    const extra = Math.max(0, len(s) - min);
    return extra ? { ...s, end: round3(s.end - extra * share) } : s;
  });
}

// trimToFit over a whole recipe.
export function trimDoc(doc, target, min) {
  const next = structuredClone(doc);
  const flat = segList(next);
  const out = trimToFit(flat.map(({ seg }) => seg), target, min);
  flat.forEach(({ pi, si }, i) => { next.parts[pi].segments[si] = out[i]; });
  return next;
}
