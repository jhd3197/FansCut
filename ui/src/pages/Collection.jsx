import { Link, useParams } from 'react-router';
import { api } from '../lib/api.js';
import { useAct, useCanonical, useFrom, usePageTitle } from '../lib/hooks.js';
import { Empty, ErrorBox, Loading, Notice } from '../components/ui.jsx';
import { Crumbs, MediaTile, Section, enc, hm, palette } from '../components/kit.jsx';
import { movieTag, quality } from './LibraryPage.jsx';
import { SuggestionTile, TimelineTile } from './Discover.jsx';
import { newCutTo } from './NewCut.jsx';
import { useApi } from '../lib/store.js';
import { hmm, tagName, useTagNames } from './Sets.jsx';

// Per film: runtime, automatic scenes, tagged ranges, tag names and how many of your cuts use it.
function FilmsTable({ data }) {
  const scenes = useApi(`/scenes/films?${new URLSearchParams({ collection: data.id || data.name })}`, { quiet: true });
  const rows = useApi('/scenes/tags', { quiet: true });
  const names = useTagNames();
  const info = new Map((scenes.data?.films || []).map((f) => [f.key, f]));
  // content tags only: measured smart tags (fc:) would swamp both columns
  const tagsOf = new Map();
  const taggedOf = new Map();
  for (const r of rows.data?.tags || []) {
    const content = (r.tags || []).filter((t) => !t.startsWith('fc:'));
    if (r.status === 'rejected' || !content.length) continue;
    if (!tagsOf.has(r.film_key)) tagsOf.set(r.film_key, new Set());
    for (const t of content) tagsOf.get(r.film_key).add(t);
    taggedOf.set(r.film_key, (taggedOf.get(r.film_key) || 0) + 1);
  }
  const cuts = (data.cuts || []).filter((c) => c.kind !== 'playlist');
  const dash = <span className="muted">—</span>;
  return (
    <div className="scroll-x">
      <table className="table">
        <thead><tr><th>Film</th><th className="num">Runtime</th><th className="num">Scenes</th><th className="num">Tagged</th><th>Tags</th><th className="num">In cuts</th></tr></thead>
        <tbody>
          {data.films.map((m) => {
            const f = info.get(m.key);
            const tagged = rows.data ? taggedOf.get(m.key) || 0 : null;
            const tags = [...(tagsOf.get(m.key) || [])].map((t) => tagName(t, names)).sort();
            const inCuts = m.tmdb_id ? cuts.filter((c) => (c.tmdb_ids || []).includes(m.tmdb_id)).length : 0;
            const secs = f?.duration ?? (m.minutes != null ? m.minutes * 60 : null);
            return (
              <tr key={m.key}>
                <td><Link to={`/movies/${enc(m.key)}`}>{m.title}</Link>{m.year ? <span className="muted"> {m.year}</span> : null}</td>
                <td className="num mono">{secs != null ? hmm(secs) : dash}</td>
                <td className="num">{f?.scenes ?? dash}</td>
                <td className="num">{tagged ?? dash}</td>
                <td>{tags.length ? <span title={tags.join(', ')}>{tags.slice(0, 3).join(', ')}{tags.length > 3 ? <span className="muted"> +{tags.length - 3}</span> : null}</span> : dash}</td>
                <td className="num">{inCuts || <span className="muted">0</span>}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

// A film on a shelf: its real poster, the facts underneath ("Extended · 1080p").
// `tag` ("NAME · 3") only goes in the tooltip: no text on the art.
export function FilmTile({ m, tag }) {
  const facts = !m.on_disk ? 'Not on disk'
    : [m.edition, 'video' in m ? (quality(m.video) || 'Not analysed') : null].filter(Boolean).join(' · ');
  return (
    <MediaTile to={`/movies/${enc(m.key)}`} art={m.art} tag={tag || movieTag(m)} title={m.title}
      meta={[m.year, m.minutes && hm(m.minutes), facts].filter(Boolean).join(' · ')}
      note={m.problem} noteClass={m.problem_severity === 'error' ? 'c-danger' : 'c-warn'} />
  );
}

// A show in a collection ("Attack on Titan · Show · 4 seasons").
export function ShowTile({ s }) {
  return (
    <MediaTile to={`/series/${enc(s.key)}`} art={s.art} tag="TV" title={s.title}
      meta={['Show', s.seasons ? `${s.seasons} season${s.seasons === 1 ? '' : 's'}` : null].filter(Boolean).join(' · ')} />
  );
}

export function CollectionPage() {
  const { id } = useParams(); // react-router hands params over decoded
  const { data, error, loading, reload } = useApi(`/library/collections/${enc(id)}`);
  usePageTitle(data?.name);
  useCanonical(data?.id && data.id !== id ? `/collections/${enc(data.id)}` : null);
  const { act, msg, busy, clear } = useAct();
  const from = useFrom();

  if (error && !data) return <div className="page"><Crumbs items={[['Library', '/library'], [id]]} /><ErrorBox error={error} onRetry={reload} /></div>;
  if (loading || !data) return <div className="page"><Loading /></div>;

  const [bg, , acc, fg] = palette(data.name);
  const years = data.films.map((f) => f.year).filter(Boolean);
  const span = years.length ? (Math.min(...years) === Math.max(...years) ? String(years[0]) : `${Math.min(...years)}–${Math.max(...years)}`) : null;
  const extended = data.films.filter((f) => /extended/i.test(f.edition || '')).length;
  const shows = data.shows || [];
  const meta = [`${data.films.length} film${data.films.length === 1 ? '' : 's'}`,
    shows.length ? `${shows.length} show${shows.length === 1 ? '' : 's'}` : null, span, data.minutes ? hm(data.minutes) : null,
    extended ? `${extended} extended` : null].filter(Boolean).join(' · ');
  const order = data.suggestions.find((s) => s.kind === 'watch_order' && s.status !== 'missing');
  const playInOrder = () => act(() => api(`/suggestions/${enc(order.id)}/install`, { method: 'POST' }),
    'Playlist created in Plex. Open Plex to play it.');
  const shelfCount = data.cuts.length + data.suggestions.length;

  return (
    <div className="page" style={{ gap: 30 }}>
      <Crumbs items={[['Library', '/library'], [data.name]]} />

      <div className="hero" style={{ gap: 28, alignItems: 'flex-end' }}>
        <div className="pst" style={{ width: 180, flex: 'none', background: bg }}>
          <span className="ptag" style={{ color: `${acc}cc` }}>Collection</span>
          <div className="ttl"><span className="ttl-main" style={{ color: fg, fontSize: 30 }}>{data.name}</span></div>
        </div>
        <div className="hero-body" style={{ flex: '1 1 320px', gap: 10 }}>
          <h1 className="hero-title" style={{ fontSize: 32 }}>{data.name}</h1>
          <span className="mono muted" style={{ fontSize: 13 }}>{meta}</span>
          <div className="row wrap" style={{ marginTop: 6 }}>
            {order && <button type="button" className="btn btn-primary" onClick={playInOrder} disabled={busy}
              title="Creates a Plex playlist of these films in order">Play in order</button>}
            <Link className="btn" to={newCutTo({ movies: data.films.filter((f) => f.on_disk).map((f) => f.key) })} state={from}>New cut from this collection</Link>
            <Link className="btn" to={`/collections/${enc(data.id)}/scenes`}>Tagged scenes</Link>
          </div>
        </div>
      </div>
      <Notice msg={msg} onClose={clear} />

      <Section id="films-h" title="Films" count={data.films.length}>
        <div className="shelf">
          {data.films.map((m, i) => <FilmTile key={m.key} m={m} tag={`${data.name} · ${i + 1}`} />)}
        </div>
      </Section>

      <Section id="films-table-h" title="Scenes and tags">
        <FilmsTable data={data} />
      </Section>

      {shows.length > 0 && (
        <Section id="shows-h" title="Shows" count={shows.length}>
          <div className="shelf">{shows.map((s) => <ShowTile key={s.key} s={s} />)}</div>
        </Section>
      )}

      <Section id="cuts-h" title="Cuts and playlists" count={shelfCount}>
        {shelfCount === 0
          ? <Empty>No cuts use these films yet. Merges and watch orders appear on <Link to="/">Discover</Link> once two or more films share a collection.</Empty>
          : (
            <div className="shelf">
              {data.cuts.map((c) => <TimelineTile key={c.id} c={c} />)}
              {data.suggestions.map((s) => <SuggestionTile key={s.id} s={s} />)}
            </div>
          )}
      </Section>


    </div>
  );
}
