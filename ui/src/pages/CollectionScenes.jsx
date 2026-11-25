import { useState } from 'react';
import { Link, useNavigate, useParams, useSearchParams } from 'react-router';
import { api } from '../lib/api.js';
import { useAct, useCanonical, useLoad } from '../lib/hooks.js';
import { fmtTime } from '../lib/time.js';
import { Empty, ErrorBox, Loading, Notice } from '../components/ui.jsx';
import { Crumbs, Section, enc } from '../components/kit.jsx';
import { frameUrl } from './Scenes.jsx';

// Every tagged scene across a collection, film by film; a tag's accepted scenes become a cut.
export function CollectionScenesPage() {
  const { id } = useParams();
  const [params, setParams] = useSearchParams();
  const tag = params.get('tag') || '';
  const { data, error, loading, reload } = useLoad(
    () => api(`/scenes/collection?${new URLSearchParams({ name: id, ...(tag ? { tag } : {}) })}`), [id, tag]);
  const name = data?.name || '';
  useCanonical(data?.id && data.id !== id ? `/collections/${enc(data.id)}/scenes` : null);
  const { act, msg, busy, clear } = useAct();
  const [title, setTitle] = useState('');
  const nav = useNavigate();

  const crumbs = [['Library', '/library'], [name || id, `/collections/${enc(data?.id || id)}`], ['Scenes']];
  if (error && !data) return <div className="page"><Crumbs items={crumbs} /><ErrorBox error={error} onRetry={reload} /></div>;
  if (loading || !data) return <div className="page"><Loading /></div>;

  const all = data.films.flatMap((f) => f.scenes);
  const accepted = all.filter((x) => x.status === 'accepted');
  const seconds = accepted.reduce((s, x) => s + (x.end - x.start), 0);
  const notIndexed = data.films.filter((f) => !f.indexed && f.on_disk);
  const makeCut = () => act(async () => {
    const t = await api('/scenes/to-timeline', { method: 'POST', body: { collection: data?.id || id, tag, title } });
    nav(`/cuts/${enc(t.id)}`);
  });

  return (
    <div className="page" style={{ gap: 24 }}>
      <Crumbs items={crumbs} />
      <div className="stack" style={{ gap: 6 }}>
        <h1 className="display" style={{ fontSize: 28 }}>{name}{tag ? `: ${tag.replace(/-/g, ' ')}` : ''}</h1>
        <span className="mono muted tiny">
          {all.length} tagged scene{all.length === 1 ? '' : 's'} · {accepted.length} accepted
          {accepted.length > 0 && ` · ${fmtTime(seconds)} of film`}
          {notIndexed.length > 0 && ` · ${notIndexed.length} film${notIndexed.length === 1 ? '' : 's'} not indexed`}
        </span>
      </div>

      <div className="row wrap" role="group" aria-label="Filter by tag">
        <button type="button" className={`btn btn-sm ${tag ? '' : 'btn-primary'}`} onClick={() => setParams({})}>All tags</button>
        {data.tags.map((x) => (
          <button key={x.tag} type="button" className={`btn btn-sm ${tag === x.tag ? 'btn-primary' : ''}`}
            onClick={() => setParams({ tag: x.tag })}>{x.tag} <span className="mono">{x.count}</span></button>
        ))}
      </div>

      {tag && (
        <div className="card row wrap" style={{ gap: 10 }}>
          <span className="grow tiny muted">
            Make a cut of the {accepted.length} accepted “{tag}” scene{accepted.length === 1 ? '' : 's'}, in film order.
            Proposals don't count until you accept them.
          </span>
          <input className="input" style={{ maxWidth: 280 }} aria-label="Cut title" placeholder={`${name}: ${tag.replace(/-/g, ' ')}`}
            value={title} onChange={(e) => setTitle(e.target.value)} />
          <button type="button" className="btn btn-primary" disabled={busy || !accepted.length} onClick={makeCut}>Make the cut</button>
        </div>
      )}
      <Notice msg={msg} onClose={clear} />

      {data.films.map((f) => (
        <Section key={f.key} id={`f-${f.key}`} title={`${f.title}${f.year ? ` (${f.year})` : ''}`} count={f.scenes.length}
          end={<Link className="tiny" to={`/movies/${enc(f.key)}/scenes`}>{f.indexed ? 'Open tagger' : 'Index and tag'}</Link>}>
          {f.scenes.length === 0
            ? <Empty>{f.indexed ? 'Nothing tagged here yet.' : f.on_disk ? 'Not indexed yet.' : 'No file on disk.'}</Empty>
            : (
              <div className="sc-shelf">
                {f.scenes.map((x) => (
                  <Link key={x.id} className={`sc-thumb sc-th-${x.status}`} to={`/movies/${enc(f.key)}/scenes`}>
                    <img src={frameUrl(f.key, (x.start + x.end) / 2, 180)} alt="" loading="lazy" />
                    <span className="mono tiny">{fmtTime(x.start)}–{fmtTime(x.end)} <span className={`sc-st-${x.status}`}>{x.status}</span></span>
                    <span className="tiny">{x.summary || x.tags.join(', ')}</span>
                  </Link>
                ))}
              </div>
            )}
        </Section>
      ))}
    </div>
  );
}
