import { useState } from 'react';
import { Link, useNavigate } from 'react-router';
import { api } from '../lib/api.js';
import { useAct, useLoad } from '../lib/hooks.js';
import { Dialog, Empty, ErrorBox, Loading, Notice } from '../components/ui.jsx';
import { Backdrop, CutTile, KV, Search, Seg, Shelf, enc, hm } from '../components/kit.jsx';

// -- shared with the Collection and Movie pages --------------------------------------------

const KIND_LABEL = {
  recipe: 'Community cut',
  collection_merge: 'Merge',
  watch_order: 'Watch order',
  canon_cut: 'Canon cut',
};

const POSTER_KIND = { recipe: 'supercut', collection_merge: 'merge', watch_order: 'order', canon_cut: 'canon' };

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
const ART = {
  collection_merge: ['split', 'merge', (s) => `MERGE · ${films(s)}`],
  watch_order: ['stack', 'order', () => 'WATCH ORDER'],
  canon_cut: ['single', 'canon', () => 'CANON CUT'],
  recipe: ['split', 'cut', () => 'COMMUNITY CUT'],
};

export function SuggestionTile({ s, onOpen }) {
  const [layout, badge, label] = ART[s.kind] || ['single', 'cut', () => suggestionTag(s)];
  return (
    <CutTile title={s.name || s.title} sub={s.tagline} tag={s.art?.length ? label(s) : suggestionTag(s)}
      kind={POSTER_KIND[s.kind] || 'supercut'} art={s.art} layout={layout} badge={badge}
      meta={suggestionMeta(s) || KIND_LABEL[s.kind]}
      status={s.status} note={suggestionStatus(s)} onClick={() => onOpen(s)} />
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

const STATUS_CLASS = { ready: 'c-ready', partly: 'c-warn', missing: 'c-danger' };

// What a suggestion is, its status, what is missing, and the install button.
export function SuggestionDialog({ s, onClose }) {
  const navigate = useNavigate();
  const { act, msg, busy, clear } = useAct();
  const isPlaylist = s.kind === 'watch_order';

  const install = async () => {
    const repo = s.from_catalogue ? s.url || s.repo : null;
    const res = await act(() => (repo
      ? api('/cuts/install', { method: 'POST', body: { url: repo } })
      : api(`/suggestions/${enc(s.id)}/install`, { method: 'POST' })),
    (r) => (r?.action === 'playlist' ? 'Playlist created in Plex. Nothing was rendered.' : 'Queued. Watch the Jobs page.'));
    if (!res) return;
    if (res.action === 'playlist') return;
    const tid = res.timeline || (repo ? res.id : null);
    navigate(tid ? `/cuts/${enc(tid)}` : '/jobs');
  };

  const d = s.detail || {};
  const what = d.template || {
    recipe: 'A community recipe: scenes from files you own, cut and joined into one new file.',
    collection_merge: 'One film made from every entry in the collection, in release order.',
    watch_order: 'A Plex playlist of the films in order. Nothing is rendered.',
    canon_cut: 'The show with filler episodes left out, rendered as new files.',
  }[s.kind];

  return (
    <Dialog title={s.title} onClose={onClose} wide>
      <span className="mono tiny muted">{suggestionTag(s)}{s.category ? ` · ${s.category}` : ''}</span>
      {s.tagline && <p className="muted" style={{ margin: 0 }}>{s.tagline}</p>}
      {what && <p style={{ margin: 0 }}>{what}</p>}
      <KV rows={[
        ['Type', KIND_LABEL[s.kind] || s.kind],
        suggestionMeta(s) && ['Contents', suggestionMeta(s)],
        d.movies?.length && ['Films', d.movies.join(' · ')],
        ['Status', <span key="st" className={STATUS_CLASS[s.status] || 'muted'}>{suggestionStatus(s)}</span>],
        d.credits && Object.keys(d.credits).length > 0 && ['Credits', Object.entries(d.credits).map(([k, v]) => `${k}: ${Array.isArray(v) ? v.join(', ') : v}`).join(' · ')],
      ]} />
      {s.missing?.length > 0 && (
        <div className="stack" style={{ gap: 6 }}>
          <b style={{ fontSize: 13 }}>Missing pieces</b>
          <ul className="tiny" style={{ margin: 0, paddingLeft: 18 }}>
            {s.missing.map((m, i) => (
              <li key={i}><span className="mono">{m.src}</span> <span className="muted">— {m.reason}</span></li>
            ))}
          </ul>
          {s.status === 'partly' && <span className="tiny muted">Installing still works; the missing pieces are skipped or use another audio track.</span>}
        </div>
      )}
      <Notice msg={msg} onClose={clear} />
      <div className="row" style={{ justifyContent: 'flex-end' }}>
        <button type="button" className="btn btn-ghost" onClick={onClose}>Close</button>
        <button type="button" className="btn btn-primary" disabled={busy || s.status === 'missing'} onClick={install}>
          {busy ? 'Working…' : isPlaylist ? 'Create playlist' : 'Install'}
        </button>
      </div>
    </Dialog>
  );
}

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
        <p className="muted" style={{ margin: 0 }}>Paste the address of a git repository that holds a FansCut recipe.
          FansCut copies the recipe and matches it against your library. No video is downloaded.</p>
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
  const [filter, setFilter] = useState('all');
  const [q, setQ] = useState('');
  const [open, setOpen] = useState(null);
  const [adding, setAdding] = useState(false);

  const needle = q.trim().toLowerCase();
  const items = (data || []).filter((s) => (filter === 'all' || (filter === 'ready' ? s.status === 'ready' : s.status !== 'ready'))
    && (!needle || [s.title, s.tagline, s.category].some((x) => x && x.toLowerCase().includes(needle))));

  const shelves = [];
  const byName = new Map();
  for (const s of items) {
    const name = s.category || KIND_LABEL[s.kind] || 'Other';
    if (!byName.has(name)) { byName.set(name, []); shelves.push(name); }
    byName.get(name).push(s);
  }

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
      {loading && !data && <Loading what="Looking at your library" />}

      {data && data.length === 0 && (
        <Empty>
          No suggestions yet. FansCut suggests cuts from what you own: movies that share a Plex
          collection become merges and watch orders, anime matched to MAL or AniList gets a canon cut,
          and community recipes in the recipes folder show up here. Check your sources in{' '}
          <Link to="/settings">Settings</Link>, rescan the <Link to="/library">Library</Link>, or add a cut by repo URL.
        </Empty>
      )}
      {data && data.length > 0 && items.length === 0 && (
        <Empty>Nothing matches. {needle ? 'Try another search' : 'Try another filter'}.</Empty>
      )}

      {!needle && filter === 'all' && loaded?.collections?.length > 0 && (
        <Shelf id="shelf-coll" title="Your collections" count={loaded.collections.length} wide>
          {loaded.collections.map((c) => (
            <Link key={c.name} className="tile tile-wide" to={`/collections/${enc(c.name)}`}>
              <Backdrop k={c.art?.[0]} name={c.name} />
              <span className="tiny muted">{c.films} films{c.cuts ? ` · ${c.cuts} cuts` : ''}</span>
            </Link>
          ))}
        </Shelf>
      )}

      {shelves.map((name, i) => (
        <Shelf key={name} id={`shelf-${i}`} title={name} count={byName.get(name).length}>
          {byName.get(name).map((s) => <SuggestionTile key={s.id} s={s} onOpen={setOpen} />)}
        </Shelf>
      ))}

      {data && data.length > 0 && (
        <p className="muted tiny" style={{ margin: 0 }}>
          Renders land in your output folder; point a Plex library at it. When Plex is set up, renders
          also get a poster and a “FansCut” collection. Manage files on the <Link to="/jobs">Jobs</Link> page.
        </p>
      )}

      {open && <SuggestionDialog s={open} onClose={() => setOpen(null)} />}
      {adding && <AddByUrl onClose={() => setAdding(false)} />}
    </div>
  );
}
