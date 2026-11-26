// A film's full cast: search, "in analysed moments" filter, how many moments each is in.
import { Link, useParams } from 'react-router';
import { useDebounced, usePageTitle, useUrlState } from '../lib/hooks.js';
import { useApi } from '../lib/store.js';
import { Empty, ErrorBox, Loading } from '../components/ui.jsx';
import { Crumbs, Search, Seg, enc } from '../components/kit.jsx';
import { personTo } from '../components/ScenePlayer.jsx';
import './Sets.css';

const norm = (s) => String(s || '').trim().toLowerCase();

// How many of the film's moments (non-rejected tag ranges) list this person, by actor or character.
export function momentsByPerson(cast, rows) {
  const live = (rows || []).filter((r) => r.status !== 'rejected');
  return cast.map((c) => {
    const actor = norm(c.name);
    const role = norm(c.character);
    return live.filter((r) => (r.people || []).some((p) => (actor && norm(p.name) === actor)
      || (role && norm(p.character) === role))).length;
  });
}

export function MovieCast() {
  const { key } = useParams();
  const movie = useApi(`/library/movies/${enc(key)}`, { quiet: true });
  const castQ = useApi(`/scenes/cast?${new URLSearchParams({ key })}`);
  const tagsQ = useApi(`/scenes/tags?${new URLSearchParams({ key })}`, { quiet: true });
  const title = movie.data?.title || key;
  usePageTitle(movie.data ? `${title} cast` : null);
  const [q, setQ] = useUrlState('q');
  const [only, setOnly] = useUrlState('show', 'all');
  const dq = norm(useDebounced(q));

  const crumbs = [['Library', '/library'], ['Movies', '/library#movies-h'], [title, `/movies/${enc(key)}`], ['Cast']];
  if (castQ.error && !castQ.data) return <div className="page"><Crumbs items={crumbs} /><ErrorBox error={castQ.error} onRetry={castQ.reload} /></div>;
  if (!castQ.data) return <div className="page"><Crumbs items={crumbs} /><Loading /></div>;

  const cast = castQ.data.cast || [];
  const seen = momentsByPerson(cast, tagsQ.data?.tags);
  const all = cast.map((c, i) => ({ ...c, order: i + 1, n: seen[i] }));
  const inMoments = all.filter((c) => c.n > 0).length;
  const rows = all.filter((c) => (only !== 'moments' || c.n > 0)
    && (!dq || `${c.name} ${c.character || ''}`.toLowerCase().includes(dq)));

  return (
    <div className="page" style={{ gap: 24 }}>
      <Crumbs items={crumbs} />
      <div className="toolbar">
        <Seg label="Show" value={only} onChange={setOnly}
          options={[['all', `All ${all.length}`], ['moments', `In analysed moments ${inMoments}`]]} />
        <Search value={q} onChange={setQ} label="Search cast" />
      </div>
      {cast.length === 0 ? <Empty>No cast for this film.</Empty> : rows.length === 0 ? <Empty>Nothing matches.</Empty> : (
        <div className="scroll-x">
          <table className="table">
            <thead><tr><th className="num">#</th><th>Actor</th><th>Character</th><th className="num">Moments</th></tr></thead>
            <tbody>
              {rows.map((c) => (
                <tr key={`${c.order}-${c.name}`}>
                  <td className="num muted">{c.order}</td>
                  <td>
                    <Link className="row" style={{ gap: 10, color: 'var(--text)' }} to={personTo(key, c.name)}>
                      {c.photo ? <img className="cast-thumb" src={c.photo} alt="" loading="lazy" />
                        : <span className="cast-thumb" aria-hidden="true">{(c.name || '?').slice(0, 1)}</span>}
                      <b>{c.name}</b>
                    </Link>
                  </td>
                  <td>{c.character ? <Link to={personTo(key, c.name)} style={{ color: 'var(--text)' }}>{c.character}</Link> : <span className="muted">—</span>}</td>
                  <td className="num">{c.n || <span className="muted">0</span>}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
