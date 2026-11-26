// Sets: every tag is a set of moments across the library. /sets lists them as
// mosaic cards; /sets/:tag shows one set and exports it, one cut per collection.
import { Link, useNavigate, useParams } from 'react-router';
import { api } from '../lib/api.js';
import { useAct, usePageTitle } from '../lib/hooks.js';
import { useApi } from '../lib/store.js';
import { fmtTime } from '../lib/time.js';
import { Empty, ErrorBox, Loading } from '../components/ui.jsx';
import { Crumbs, Section, enc } from '../components/kit.jsx';
import { TagChips, isShownTag, tagName, tagTo, useTagNames } from '../components/TagChip.jsx';
import { frameUrl } from './Scenes.jsx';
import './Sets.css';

// -- shared with Movie, MovieCast, Collection and Discover -------------------------------------

// tag names live with the chip now; kept here so older imports keep working
export { tagName, useTagNames };

// "2:05" from seconds: hours and minutes.
export function hmm(sec) {
  if (sec == null) return '–';
  const m = Math.round(sec / 60);
  return `${Math.floor(m / 60)}:${String(m % 60).padStart(2, '0')}`;
}

const mid = (r) => (r.start + r.end) / 2;
const plural = (n, w) => `${n} ${w}${n === 1 ? '' : 's'}`;

// Up to four moments for a mosaic: accepted first, one per film before seconds.
function pickStills(moments, n = 4) {
  const sorted = [...moments].sort((a, b) => (a.status === 'accepted' ? 0 : 1) - (b.status === 'accepted' ? 0 : 1));
  const out = [];
  const films = new Set();
  for (const r of sorted) if (out.length < n && !films.has(r.film_key)) { out.push(r); films.add(r.film_key); }
  for (const r of sorted) if (out.length < n && !out.includes(r)) out.push(r);
  return out;
}

// One set per shown tag (never a measured fc: one) with at least one moment that isn't rejected.
export function buildSets(counts, rows, names) {
  const byTag = new Map();
  for (const r of rows || []) {
    if (r.status === 'rejected') continue;
    for (const t of (r.tags || []).filter(isShownTag)) {
      if (!byTag.has(t)) byTag.set(t, []);
      byTag.get(t).push(r);
    }
  }
  const list = (counts || []).filter((c) => isShownTag(c.tag)).map((c) => ({ tag: c.tag, live: (c.proposed || 0) + (c.accepted || 0) }));
  for (const t of byTag.keys()) if (!list.some((x) => x.tag === t)) list.push({ tag: t, live: byTag.get(t).length });
  return list.filter((x) => x.live > 0).map((x) => {
    const moments = byTag.get(x.tag) || [];
    return {
      tag: x.tag, smart: false, name: tagName(x.tag, names), moments,
      count: moments.length || x.live,
      films: new Set(moments.map((r) => r.film_key)).size,
      stills: pickStills(moments),
    };
  }).sort((a, b) => b.count - a.count || a.name.localeCompare(b.name));
}

// Every set, from the cached tag-counts, vocab and tag list.
export function useSets() {
  const counts = useApi('/scenes/tag-counts', { quiet: true });
  const rows = useApi('/scenes/tags', { quiet: true });
  const names = useTagNames();
  const ready = counts.data != null || rows.data != null;
  return {
    sets: ready ? buildSets(counts.data?.tags, rows.data?.tags, names) : null,
    error: counts.error && rows.error ? counts.error : null,
    reload: () => { counts.reload(); rows.reload(); },
  };
}

export function Mosaic({ stills }) {
  return (
    <div className="mosaic" data-n={stills.length || 1} aria-hidden="true">
      {stills.map((r) => <img key={r.id} src={frameUrl(r.film_key, mid(r), 240)} alt="" loading="lazy" decoding="async" />)}
    </div>
  );
}

export function SetCard({ s }) {
  return (
    <Link className="set-card" to={`/sets/${enc(s.tag)}`}>
      <Mosaic stills={s.stills} />
      <div className="cap">
        <b className="cap-title">{s.name}</b>
        <span className="meta">{plural(s.count, 'moment')}{s.films ? ` · ${plural(s.films, 'film')}` : ''}</span>
      </div>
    </Link>
  );
}

// -- /sets ---------------------------------------------------------------------------------------

export function Sets() {
  usePageTitle('Sets');
  const { sets, error, reload } = useSets();
  const content = sets || [];
  return (
    <div className="page page-wide" style={{ gap: 30 }}>
      <div className="toolbar">
        <Crumbs items={[['Sets']]} />
        <button type="button" className="btn btn-primary btn-sm" disabled title="Coming soon">New set</button>
      </div>
      <ErrorBox error={error} onRetry={reload} />
      {!sets && !error && <Loading />}
      {sets && sets.length === 0 && (
        <Empty>No tagged moments yet. Tag scenes on a film's <Link to="/library">Scenes and tags</Link> page.</Empty>
      )}
      {content.length > 0 && <div className="set-grid">{content.map((s) => <SetCard key={s.tag} s={s} />)}</div>}
    </div>
  );
}

// -- /sets/:tag ----------------------------------------------------------------------------------

// Where a moment opens: the film's tagger, on that tag range.
export const momentTo = (r) => `/movies/${enc(r.film_key)}/scenes?${new URLSearchParams({
  scene: r.id, t: String(Math.round(r.start * 10) / 10), ...(r.status === 'accepted' ? { tab: 'tags' } : {}),
})}`;

export function SetDetail() {
  const { tag } = useParams();
  const names = useTagNames();
  const name = tagName(tag, names);
  usePageTitle(name);
  const rowsQ = useApi(`/scenes/tags?${new URLSearchParams({ tag })}`);
  const filmsQ = useApi('/scenes/films', { quiet: true });
  const { act, busy } = useAct();
  const navigate = useNavigate();

  const crumbs = [['Sets', '/sets'], [name]];
  if (rowsQ.error && !rowsQ.data) return <div className="page"><Crumbs items={crumbs} /><ErrorBox error={rowsQ.error} onRetry={rowsQ.reload} /></div>;
  if (!rowsQ.data) return <div className="page"><Crumbs items={crumbs} /><Loading /></div>;

  const moments = (rowsQ.data.tags || []).filter((r) => r.status !== 'rejected');
  const film = new Map((filmsQ.data?.films || []).map((f) => [f.key, f]));
  const titleOf = (k) => film.get(k)?.title || k;
  const accepted = moments.filter((r) => r.status === 'accepted');
  const total = moments.reduce((s, r) => s + (r.end - r.start), 0);
  const keys = [...new Set(moments.map((r) => r.film_key))];
  const films = keys.map((k) => {
    const mine = moments.filter((r) => r.film_key === k);
    const f = film.get(k);
    return {
      key: k, title: titleOf(k), year: f?.year, collections: f?.franchises || f?.collections || [],
      moments: mine.length, accepted: mine.filter((r) => r.status === 'accepted').length,
      seconds: mine.reduce((s, r) => s + (r.end - r.start), 0),
    };
  }).sort((a, b) => (a.year || 0) - (b.year || 0) || a.title.localeCompare(b.title));

  // One cut per collection with accepted moments: a cut never mixes franchises.
  const perCollection = new Map();
  for (const f of films) {
    if (!f.accepted) continue;
    for (const c of f.collections) perCollection.set(c, (perCollection.get(c) || 0) + f.accepted);
  }
  const makeCut = (collection) => act(async () => {
    const t = await api('/scenes/to-timeline', { method: 'POST', body: { collection, tag } });
    if (t?.id) navigate(`/cuts/${enc(t.id)}`);
    return t;
  });

  return (
    <div className="page" style={{ gap: 30 }}>
      <Crumbs items={crumbs} />

      {moments.length === 0 ? <Empty>No moments with this tag yet.</Empty> : (<>
        <div className="stack" style={{ gap: 16 }}>
          <ul className="facts">
            <li><b>{moments.length}</b>{moments.length === 1 ? 'moment' : 'moments'}</li>
            <li><b>{films.length}</b>{films.length === 1 ? 'film' : 'films'}</li>
            <li><b>{fmtTime(total)}</b>total</li>
            <li><b>{accepted.length}</b>accepted</li>
            <li><b>{moments.length - accepted.length}</b>proposed</li>
          </ul>
          <div className="row wrap">
            <span className="rule-chip"><span>tag is</span><b>{name}</b></span>
            <Link className="tiny" to={tagTo(tag)}>All moments with this tag</Link>
          </div>
        </div>

        <Section id="set-export" title="Export">
          <div className="row wrap">
            {[...perCollection.keys()].sort().map((c) => (
              <button key={c} type="button" className="btn btn-primary" disabled={busy}
                title={`A cut of the ${perCollection.get(c)} accepted moments in ${c}`}
                onClick={() => makeCut(c)}>Cut: {c}</button>
            ))}
            {perCollection.size === 0 && <span className="muted tiny">Accept moments in a collection's films to make a cut.</span>}
            <button type="button" className="btn" disabled title="Coming soon">Plex collection</button>
            <button type="button" className="btn" disabled title="Coming soon">Marker file</button>
          </div>
        </Section>

        <Section id="set-films" title="Films" count={films.length}>
          <div className="scroll-x">
            <table className="table">
              <thead><tr><th>Film</th><th>Collection</th><th className="num">Moments</th><th className="num">Accepted</th><th className="num">Duration</th></tr></thead>
              <tbody>
                {films.map((f) => (
                  <tr key={f.key}>
                    <td><Link to={`/movies/${enc(f.key)}`}>{f.title}</Link>{f.year ? <span className="muted"> {f.year}</span> : null}</td>
                    <td>{f.collections.length ? f.collections.join(', ') : <span className="muted">—</span>}</td>
                    <td className="num">{f.moments}</td>
                    <td className="num">{f.accepted}</td>
                    <td className="num mono">{fmtTime(f.seconds)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Section>

        <Section id="set-moments" title="Moments" count={moments.length}>
          <div className="moments">
            {moments.map((r) => (
              <div key={r.id} className="moment">
                <Link className="moment-link" to={momentTo(r)}>
                  <img src={frameUrl(r.film_key, mid(r), 240)} alt="" loading="lazy" decoding="async" />
                  <b className="tiny">{titleOf(r.film_key)}</b>
                  <span className="mono tiny">{fmtTime(r.start)}–{fmtTime(r.end)}{r.status === 'proposed' ? <span className="muted"> · proposed</span> : null}</span>
                </Link>
                <TagChips tags={(r.tags || []).filter((x) => x !== tag)} names={names} className="tag-links-sm" />
              </div>
            ))}
          </div>
        </Section>
      </>)}
    </div>
  );
}
