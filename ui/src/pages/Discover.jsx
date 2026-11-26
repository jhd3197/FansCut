// Discover: the season's picks, scene sets, then one grid of every suggestion you can filter.
import { useState } from 'react';
import { Link, useNavigate } from 'react-router';
import { api } from '../lib/api.js';
import { useAct, useDebounced, useFrom, useLoad, useUrlState } from '../lib/hooks.js';
import { Dialog, Empty, ErrorBox, Notice, SkeletonTiles } from '../components/ui.jsx';
import { ArtPoster, CutTile, Poster, Search, Seg, enc, hm } from '../components/kit.jsx';
import { FilterChip, ReadyState } from '../components/v2.jsx';
import { discoverShelves } from '../lib/shelves.js';
import { SetCard, useSets } from './Sets.jsx';
import './Discover.css';

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

// The kind tabs: one per kind present, in this order.
const KIND_TABS = [['collection_merge', 'Merged movies'], ['watch_order', 'Story order'], ['recipe', 'Community cuts'], ['canon_cut', 'Canon cuts']];
const CARD_KIND = { collection_merge: 'Merged movie', watch_order: 'Story order', recipe: 'Community cut', canon_cut: 'Canon cut' };

// Short status under a card: "Ready" or "Missing 2 films".
export function cardStatus(s) {
  if (s.status === 'ready') return 'Ready';
  const n = new Set((s.missing || []).map((m) => m.src).filter((x) => x && x !== '-')).size;
  if (!n) return s.status === 'partly' ? 'Partly ready' : 'Not available';
  return `Missing ${n} ${s.kind === 'canon_cut' ? 'episode' : 'film'}${n === 1 ? '' : 's'}`;
}

// Genres with counts, biggest first, from every suggestion's genres.
export function genreCounts(items) {
  const n = new Map();
  for (const s of items) for (const g of new Set(s.genres || [])) n.set(g, (n.get(g) || 0) + 1);
  return [...n].sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]));
}

function Art({ s }) {
  const [layout] = SUGGESTION_ART[s.kind] || ['single'];
  const drawn = <Poster title={s.name || s.title} sub={s.tagline} kind={POSTER_KIND[s.kind] || 'supercut'} bare />;
  return s.art?.length ? <ArtPoster keys={s.art} layout={layout} fallback={drawn} /> : drawn;
}

function Card({ s }) {
  const from = useFrom();
  return (
    <Link className="disc-card" to={suggestionTo(s)} state={from}>
      <span className="disc-art"><Art s={s} /></span>
      <span className="lab disc-kind">{CARD_KIND[s.kind] || KIND_LABEL[s.kind] || 'Cut'}</span>
      <span className="hd disc-title">{s.name || s.title}</span>
      <span className="mono disc-meta">{suggestionMeta(s) || ''}</span>
      <ReadyState ready={s.status === 'ready'}>{cardStatus(s)}</ReadyState>
    </Link>
  );
}

// The season's pick: art, name, one Install button. Installing works like the cut's own page.
function Featured({ s }) {
  const navigate = useNavigate();
  const { act, busy } = useAct();
  const install = async () => {
    const repo = s.from_catalogue ? s.url || s.repo : null;
    const res = await act(() => (repo
      ? api('/cuts/install', { method: 'POST', body: { url: repo } })
      : api(`/suggestions/${enc(s.id)}/install`, { method: 'POST' })),
    (r) => (r?.action === 'playlist' ? 'Playlist created in Plex. Nothing was rendered.' : 'Queued. Watch the Jobs page.'));
    if (!res || res.action === 'playlist') return;
    const tid = res.timeline || (repo ? res.id : null);
    navigate(tid ? `/cuts/${enc(tid)}` : '/jobs');
  };
  return (
    <div className="disc-feat">
      <Link to={suggestionTo(s)} className="disc-feat-art" tabIndex={-1} aria-hidden="true"><Art s={s} /></Link>
      <div className="disc-feat-body">
        <div className="lab">{CARD_KIND[s.kind] || KIND_LABEL[s.kind]}</div>
        <Link to={suggestionTo(s)} className="hd disc-feat-title">{s.name || s.title}</Link>
        <div className="mono disc-meta">{suggestionMeta(s)}</div>
        <button type="button" className="btn btn-sm btn-primary" disabled={busy || s.status !== 'ready'} onClick={install}>Install</button>
      </div>
    </div>
  );
}

function AddByUrl({ onClose }) {
  const navigate = useNavigate();
  const [url, setUrl] = useState('');
  const { act, msg, busy, clear } = useAct({ inline: true });
  const submit = async (e) => {
    e.preventDefault();
    const res = await act(() => api('/cuts/install', { method: 'POST', body: { url: url.trim() } }));
    if (res?.id) navigate(`/cuts/${enc(res.id)}`);
  };
  return (
    <Dialog title="Add by repo URL" onClose={onClose}>
      <form className="stack" onSubmit={submit}>
        <label className="lab" htmlFor="disc-repo">Repository URL</label>
        <input id="disc-repo" className="inp" type="url" required placeholder="https://github.com/someone/some-cut"
          value={url} onChange={(e) => setUrl(e.target.value)} autoFocus />
        <Notice msg={msg} onClose={clear} />
        <div className="row" style={{ justifyContent: 'flex-end' }}>
          <button type="button" className="btn" onClick={onClose}>Cancel</button>
          <button type="submit" className="btn btn-primary" disabled={busy || !url.trim()}>{busy ? 'Adding' : 'Add cut'}</button>
        </div>
      </form>
    </Dialog>
  );
}

async function loadAll() {
  const [sugg, cat] = await Promise.all([
    api('/suggestions'),
    api('/catalogue').catch(() => null), // optional: the catalogue may not exist yet
  ]);
  const seen = new Set();
  const out = [];
  for (const s of sugg.suggestions || []) {
    if (!seen.has(s.id)) { seen.add(s.id); out.push(s); }
  }
  for (const c of cat?.cuts || []) {
    if (c?.id && !seen.has(c.id)) { seen.add(c.id); out.push({ ...c, from_catalogue: true }); }
  }
  return out;
}

const PAGE = 60;

export function Discover() {
  const { data, error, loading, reload } = useLoad(loadAll);
  const [status, setStatus] = useUrlState('show', 'all');
  const [kind, setKind] = useUrlState('kind', 'all');
  const [genre, setGenre] = useUrlState('genre', '');
  const [q, setQ] = useUrlState('q');
  const dq = useDebounced(q);
  const [adding, setAdding] = useState(false);
  const [more, setMore] = useState(1);
  const { sets } = useSets();

  const all = data || [];
  const needle = dq.trim().toLowerCase();
  const hit = (s) => !needle || [s.name, s.title, s.tagline, s.category, ...(s.genres || []), ...(s.detail?.movies || [])]
    .some((x) => x && String(x).toLowerCase().includes(needle));
  const items = all.filter((s) => hit(s)
    && (status === 'all' || (status === 'ready' ? s.status === 'ready' : s.status !== 'ready'))
    && (kind === 'all' || s.kind === kind)
    && (!genre || (s.genres || []).includes(genre)));
  const kinds = KIND_TABS.filter(([k]) => all.some((s) => s.kind === k));
  const genres = genreCounts(all.filter((s) => kind === 'all' || s.kind === kind));
  const seasonal = discoverShelves(all).filter((x) => x.seasonal);
  const plainSets = (sets || []).filter((x) => !x.smart && (!needle || x.name.toLowerCase().includes(needle)));
  const shown = items.slice(0, PAGE * more);

  return (
    <div className="page disc">
      <header className="disc-head">
        <div className="disc-head-name">
          <h1>Discover</h1>
          {data && <div className="mono disc-meta">{all.length} cuts</div>}
        </div>
        <Search value={q} onChange={setQ} label="Search films, collections, tags" />
        <button className="btn" type="button" onClick={() => setAdding(true)}>Add by repo URL</button>
      </header>

      <ErrorBox error={error} onRetry={reload} />
      {loading && !data && <SkeletonTiles n={12} />}

      {data && all.length === 0 && (
        <Empty>
          No suggestions yet. Films that share a Plex collection become merges and story orders here.
          Check your sources in <Link to="/settings">Settings</Link> or add a cut by repo URL.
        </Empty>
      )}

      {!needle && seasonal.map((shelf) => (
        <section key={shelf.name} className="box" aria-labelledby={`disc-${shelf.name}`}>
          <div className="disc-sec-head">
            <h2 id={`disc-${shelf.name}`}>{shelf.name}</h2>
            <span className="mono disc-meta">{shelf.items.filter((s) => s.status === 'ready').length} ready to install</span>
          </div>
          <div className="disc-feats">{shelf.items.slice(0, 4).map((s) => <Featured key={s.id} s={s} />)}</div>
        </section>
      ))}

      {plainSets.length > 0 && (
        <section aria-labelledby="disc-sets">
          <div className="disc-sec-head">
            <h2 id="disc-sets">Scene sets from your films</h2>
            <span className="mono disc-meta">{plainSets.length}</span>
            <Link to="/sets" className="disc-all">See all</Link>
          </div>
          <div className="disc-sets">{plainSets.slice(0, 5).map((x) => <SetCard key={x.tag} s={x} />)}</div>
        </section>
      )}

      {all.length > 0 && (
        <section aria-labelledby="disc-all">
          <div className="disc-sec-head disc-filters">
            <h2 id="disc-all">Everything</h2>
            <span className="mono disc-meta">{items.length === all.length ? all.length : `${items.length} shown`}</span>
            <span className="disc-gap" />
            <Seg label="What you have" value={status} onChange={setStatus}
              options={[['all', 'All'], ['ready', 'Ready'], ['missing', 'Missing something']]} />
            {kinds.length > 1 && (
              <Seg label="Kind of cut" value={kind} onChange={setKind} options={[['all', 'All kinds'], ...kinds]} />
            )}
          </div>
          {genres.length > 0 && (
            <div className="tags-row disc-genres" role="group" aria-label="Genre">
              <FilterChip on={!genre} onClick={() => setGenre('')}>All genres</FilterChip>
              {genres.map(([g, n]) => (
                <FilterChip key={g} on={genre === g} count={n} onClick={() => setGenre(genre === g ? '' : g)}>{g}</FilterChip>
              ))}
            </div>
          )}
          {items.length === 0
            ? <p className="disc-meta">Nothing here matches. Try another genre, or switch back to All.</p>
            : <div className="disc-grid">{shown.map((s) => <Card key={s.id} s={s} />)}</div>}
          {items.length > shown.length && (
            <div className="disc-more"><button type="button" className="btn" onClick={() => setMore((m) => m + 1)}>Show more ({items.length - shown.length})</button></div>
          )}
          <p className="disc-foot">
            Builds land in your output folder. When Plex is set up they also get a poster and a FansCut collection.
            Manage files on the <Link to="/jobs">Jobs</Link> page.
          </p>
        </section>
      )}

      {adding && <AddByUrl onClose={() => setAdding(false)} />}
    </div>
  );
}
