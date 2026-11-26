import { useState } from 'react';
import { Link, useNavigate } from 'react-router';
import { api } from '../lib/api.js';
import { useAct, useLoad, useUrlState, useDebounced, useShelfLayout } from '../lib/hooks.js';
import { Dialog, Empty, ErrorBox, Loading, Notice, SkeletonTiles } from '../components/ui.jsx';
import { Backdrop, CutTile, KV, Search, Seg, Shelf, enc, hm } from '../components/kit.jsx';
import { discoverShelves } from '../lib/shelves.js';

// -- shared with the Collection and Movie pages --------------------------------------------

export const KIND_LABEL = {
  recipe: 'Community cut',
  collection_merge: 'Merge',
  watch_order: 'Watch order',
  canon_cut: 'Canon cut',
};

export const POSTER_KIND = { recipe: 'supercut', collection_merge: 'merge', watch_order: 'order', canon_cut: 'canon' };

function films(s) {
  return s.detail?.movies?.length || 0;
}

// The small caps label in the poster's corner.
export function suggestionTag(s) {
  if (s.kind === 'collection_merge') return films(s) === 3 ? 'TRILOGY MERGE' : `MERGE · ${films(s)} FILMS`;
  if (s.kind === 'watch_order') return 'PLAYLIST · NO RENDER';
  if (s.kind === 'canon_cut') return 'CANON CUT';
  if (s.kind === 'recipe') return s.category && s.category !== 'Community cuts' ? `CUT · ${s.category.toUpperCase()}` : 'COMMUNITY CUT';
  return String(s.kind || 'cut').replace(/_/g, ' ').toUpperCase();
}

function yearSpan(years) {
  const ys = (years || []).filter(Boolean);
  if (!ys.length) return null;
  return ys[0] === ys[ys.length - 1] ? String(ys[0]) : `${ys[0]}–${ys[ys.length - 1]}`;
}

export function suggestionMeta(s) {
  const d = s.detail || {};
  switch (s.kind) {
    case 'collection_merge':
      return [`${films(s)} films`, yearSpan(d.years)].filter(Boolean).join(' · ');
    case 'watch_order':
      return `${films(s)} films · Plex playlist`;
    case 'canon_cut':
      return [d.episodes != null && `${d.episodes} episodes`, d.filler_episodes && `${d.filler_episodes} filler skipped`]
        .filter(Boolean).join(' · ');
    default:
      return [d.parts && `${d.parts} part${d.parts === 1 ? '' : 's'}`, d.segments && `${d.segments} scenes`]
        .filter(Boolean).join(' · ') || null;
  }
}

function missingNames(s, max = 2) {
  const names = [...new Set((s.missing || []).map((m) => m.src).filter((x) => x && x !== '-'))];
  if (!names.length) return '';
  const head = names.slice(0, max).join(', ');
  return names.length > max ? `${head} and ${names.length - max} more` : head;
}

export function suggestionStatus(s) {
  if (s.status === 'ready') {
    if (s.kind === 'watch_order') return 'Ready · instant';
    if (s.kind === 'collection_merge' || s.kind === 'canon_cut') return 'Ready · long build';
    return 'Ready · quick build';
  }
  const names = missingNames(s);
  if (s.status === 'partly') return names ? `Partly ready · missing ${names}` : 'Partly ready';
  if (names) return `Missing ${names}`;
  return s.missing?.[0]?.reason ? `Not available · ${s.missing[0].reason}` : 'Not available';
}

// On real artwork: a short badge, and how several films' posters are combined.
export const SUGGESTION_ART = {
  collection_merge: ['split', 'merge', (s) => `MERGE · ${films(s)}`],
  watch_order: ['stack', 'order', () => 'WATCH ORDER'],
  canon_cut: ['single', 'canon', () => 'CANON CUT'],
  recipe: ['split', 'cut', () => 'COMMUNITY CUT'],
};

export function SuggestionTile({ s }) {
  const [layout, badge, label] = SUGGESTION_ART[s.kind] || ['single', 'cut', () => suggestionTag(s)];
  return (
    <CutTile title={s.name || s.title} sub={s.tagline} tag={s.art?.length ? label(s) : suggestionTag(s)}
      kind={POSTER_KIND[s.kind] || 'supercut'} art={s.art} layout={layout} badge={badge}
      meta={suggestionMeta(s) || KIND_LABEL[s.kind]}
      status={s.status} note={suggestionStatus(s)} to={suggestionTo(s)} />
  );
}

// A timeline the user already has (from /api/cuts or a detail page's `cuts`).
export function TimelineTile({ c }) {
  const meta = [c.parts && `${c.parts} part${c.parts === 1 ? '' : 's'}`, c.minutes != null && hm(c.minutes),
    c.variants ? `${c.variants} variant${c.variants === 1 ? '' : 's'}` : null].filter(Boolean).join(' · ');
  const tag = (c.tmdb_ids?.length || 0) > 1 ? 'MERGE' : 'CUT';
  return (
    <CutTile to={`/cuts/${enc(c.id)}`} title={c.title} tag={tag} kind={tag === 'MERGE' ? 'merge' : 'supercut'}
      meta={meta} status={c.synced ? 'ready' : 'partly'} note={c.synced ? 'Synced · ready to build' : 'Not synced yet'} />
  );
}

export const STATUS_CLASS = { ready: 'c-ready', partly: 'c-warn', missing: 'c-danger' };

// One sentence on what installing it makes.
export function suggestionWhat(s) {
  return s.detail?.template || {
    recipe: 'A community cut: scenes from files you own, cut and joined into one new file.',
    collection_merge: 'One film made from every entry in the collection, in release order.',
    watch_order: 'A Plex playlist of the films in order. Nothing is rendered.',
    canon_cut: 'The show with filler episodes left out, rendered as new files.',
  }[s.kind];
}

// A suggestion's own page (Discover / <name>).
export const suggestionTo = (s) => `/discover/${enc(s.id)}`;

// -- the page --------------------------------------------------------------------------------

function AddByUrl({ onClose }) {
  const navigate = useNavigate();
  const [url, setUrl] = useState('');
  const { act, msg, busy, clear } = useAct();
  const submit = async (e) => {
    e.preventDefault();
    const res = await act(() => api('/cuts/install', { method: 'POST', body: { url: url.trim() } }));
    if (res?.id) navigate(`/cuts/${enc(res.id)}`);
  };
  return (
    <Dialog title="Add a cut by repo URL" onClose={onClose}>
      <form className="stack" onSubmit={submit}>
        <p className="muted" style={{ margin: 0 }}>Paste the address of a git repository that holds a FansCut cut file.
          FansCut copies the cut file and matches it against your library. No video is downloaded.</p>
        <label className="field">
          Repository URL
          <input className="input" type="url" required placeholder="https://github.com/someone/some-cut"
            value={url} onChange={(e) => setUrl(e.target.value)} autoFocus />
        </label>
        <Notice msg={msg} onClose={clear} />
        <div className="row" style={{ justifyContent: 'flex-end' }}>
          <button type="button" className="btn btn-ghost" onClick={onClose}>Cancel</button>
          <button type="submit" className="btn btn-primary" disabled={busy || !url.trim()}>{busy ? 'Adding…' : 'Add cut'}</button>
        </div>
      </form>
    </Dialog>
  );
}

async function loadAll() {
  const [sugg, cat, colls] = await Promise.all([
    api('/suggestions'),
    api('/catalogue').catch(() => null), // optional: the catalogue may not exist yet
    api('/library/collections').catch(() => null),
  ]);
  const seen = new Set();
  const out = [];
  for (const s of sugg.suggestions || []) {
    if (!seen.has(s.id)) { seen.add(s.id); out.push(s); }
  }
  for (const c of cat?.cuts || []) {
    if (c?.id && !seen.has(c.id)) { seen.add(c.id); out.push({ ...c, from_catalogue: true }); }
  }
  return { items: out, collections: colls?.collections || [] };
}

export function Discover() {
  const { data: loaded, error, loading, reload } = useLoad(loadAll);
  const data = loaded?.items;
  const [filter, setFilter] = useUrlState('show', 'all');
  const [q, setQ] = useUrlState('q');
  const dq = useDebounced(q);
  const [adding, setAdding] = useState(false);

  const needle = dq.trim().toLowerCase();
  const items = (data || []).filter((s) => (filter === 'all' || (filter === 'ready' ? s.status === 'ready' : s.status !== 'ready'))
    && (!needle || [s.title, s.tagline, s.category].some((x) => x && x.toLowerCase().includes(needle))));

  // editorial order: the season's shelf, genres, then story order / characters / short / anime
  const built = discoverShelves(items);
  const byName = new Map(built.map((x) => [x.name, x.items]));
  const layout = useShelfLayout(built.map((x) => x.name));
  const pinned = built.filter((x) => x.seasonal).map((x) => x.name);
  const visible = [...pinned.filter((n) => layout.visible.includes(n)), ...layout.visible.filter((n) => !pinned.includes(n))];

  return (
    <div className="page page-wide" style={{ gap: 30 }}>
      <div className="toolbar">
        <Seg label="Show" value={filter} onChange={setFilter}
          options={[['all', 'All'], ['ready', 'Ready'], ['missing', 'Missing something']]} />
        <div className="row wrap">
          <Search value={q} onChange={setQ} label="Search cuts" />
          <button className="btn" type="button" onClick={() => setAdding(true)}>Add by repo URL</button>
        </div>
      </div>

      <ErrorBox error={error} onRetry={reload} />
      {loading && !data && <SkeletonTiles n={12} />}

      {data && data.length === 0 && (
        <Empty>
          No suggestions yet. FansCut suggests cuts from what you own: movies that share a Plex
          collection become merges and watch orders, anime matched to MAL or AniList gets a canon cut,
          and community cuts in the recipes folder show up here. Check your sources in{' '}
          <Link to="/settings">Settings</Link>, rescan the <Link to="/library">Library</Link>, or add a cut by repo URL.
        </Empty>
      )}
      {data && data.length > 0 && items.length === 0 && (
        <Empty>Nothing matches. {needle ? 'Try another search' : 'Try another filter'}.</Empty>
      )}

      {!needle && filter === 'all' && loaded?.collections?.length > 0 && (
        <Shelf id="shelf-coll" title="Your collections" count={loaded.collections.length} wide>
          {loaded.collections.map((c) => (
            <Link key={c.id || c.name} className="tile tile-wide" to={`/collections/${enc(c.id || c.name)}`}>
              <Backdrop k={c.backdrops?.[0]} name={c.name} />
              <span className="tiny muted">{c.films} films{c.cuts ? ` · ${c.cuts} cuts` : ''}</span>
            </Link>
          ))}
        </Shelf>
      )}

      {visible.map((name, i) => (
        <Shelf key={name} id={`shelf-${i}`} title={name} count={byName.get(name).length}
          actions={(
            <span className="shelf-tools">
              <button type="button" className="btn btn-sm btn-ghost" aria-label={`Move ${name} up`} disabled={i === 0} onClick={() => layout.move(name, -1)}>↑</button>
              <button type="button" className="btn btn-sm btn-ghost" aria-label={`Move ${name} down`} disabled={i === visible.length - 1} onClick={() => layout.move(name, 1)}>↓</button>
              <button type="button" className="btn btn-sm btn-ghost" aria-label={`Hide ${name}`} onClick={() => layout.hide(name)}>Hide</button>
            </span>
          )}>
          {byName.get(name).map((s) => <SuggestionTile key={s.id} s={s} />)}
        </Shelf>
      ))}
      {layout.hidden.length > 0 && (
        <p className="muted tiny" style={{ margin: 0 }}>
          {layout.hidden.length} shelf{layout.hidden.length > 1 ? 'ves' : ''} hidden ({layout.hidden.join(', ')}).{' '}
          <button type="button" className="btn btn-sm btn-ghost" onClick={layout.showAll}>Show them</button>
        </p>
      )}

      {data && data.length > 0 && (
        <p className="muted tiny" style={{ margin: 0 }}>
          Renders land in your output folder; point a Plex library at it. When Plex is set up, renders
          also get a poster and a “FansCut” collection. Manage files on the <Link to="/jobs">Jobs</Link> page.
        </p>
      )}

      {adding && <AddByUrl onClose={() => setAdding(false)} />}
    </div>
  );
}
