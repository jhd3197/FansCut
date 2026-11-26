// A cut's poster: layout, title, which stills, and how stills are scored. The choice is
// kept in this browser (see usePosterPrefs); Settings > Posters holds the defaults.
// "AI poster" draws one with an image model from the same stills (kept on the server).
import { useEffect, useState } from 'react';
import { Link, useParams } from 'react-router';
import { api } from '../lib/api.js';
import { useCrumbRoot, usePageTitle } from '../lib/hooks.js';
import { posterSlots, WEIGHT_KEYS } from '../lib/stills.js';
import { useApi } from '../lib/store.js';
import { Crumbs, enc, Seg } from '../components/kit.jsx';
import { ErrorBox, Loading } from '../components/ui.jsx';
import { PosterCanvas, tc, useReel } from './CutDetail.jsx';

const WEIGHT_LABELS = { exposure: 'Exposure', contrast: 'Contrast', color: 'Colour', detail: 'Detail' };

export const aiPosterUrl = (id, v) => `/api/timelines/${enc(id)}/poster${v ? `?v=${encodeURIComponent(v)}` : ''}`;
const takeUrl = (id, name) => `/api/timelines/${enc(id)}/poster/ai/${encodeURIComponent(name)}`;

// The cut's AI poster takes: state from the server, polled while one is being drawn.
export function useAiPoster(id) {
  const st = useApi(`/timelines/${enc(id)}/poster/ai`, { quiet: true });
  const running = st.data?.task?.status === 'running';
  const { reload } = st;
  useEffect(() => {
    if (!running) return undefined;
    const timer = setInterval(reload, 2000);
    return () => clearInterval(timer);
  }, [running, reload]);
  return st;
}

function AiPoster({ id, stills, title, view, setView }) {
  const st = useAiPoster(id);
  const [err, setErr] = useState('');
  const data = st.data;
  const task = data?.task;
  const running = task?.status === 'running';
  const call = async (path, opts) => {
    setErr('');
    try { await api(path, opts); } catch (e) { setErr(e.message || String(e)); }
    st.reload();
  };
  const generate = () => call(`/timelines/${enc(id)}/poster/ai`, {
    method: 'POST', body: { stills: stills.slice(0, 4).map((x) => ({ src: x.source, t: x.t })), title },
  });
  useEffect(() => { if (task?.status === 'done' && task.take) setView(task.take); }, [task?.status, task?.take, setView]);
  if (!data) return null;
  if (!data.model) return <p className="muted" style={{ margin: 0 }}>Set an image model under Settings, Posters to draw posters.</p>;
  return (
    <section className="stack" style={{ gap: 10 }} aria-labelledby="pp-ai">
      <div className="section-head">
        <h2 id="pp-ai">AI poster</h2>
        <span className="n">{data.takes.length ? `${data.takes.length} take${data.takes.length === 1 ? '' : 's'}` : ''}</span>
        <button type="button" className="btn btn-sm btn-primary end" disabled={running || !stills.length} onClick={generate}>
          {data.takes.length ? 'Generate again' : 'Generate with AI'}
        </button>
      </div>
      <span className="tiny muted">Draws a poster from the {Math.min(4, stills.length)} stills the poster uses, with {data.model}. It costs a few cents and stays on this server.</span>
      {running && <span className="tiny">{task.message || 'Drawing'}…</span>}
      {task?.status === 'failed' && <span className="tiny" style={{ color: 'var(--bad)' }}>{task.message}</span>}
      {err && <span className="tiny" style={{ color: 'var(--bad)' }}>{err}</span>}
      {data.takes.length > 0 && (
        <div className="reel-cands">
          {data.takes.map((t) => (
            <div key={t.name} className="stack" style={{ gap: 4 }}>
              <button type="button" aria-pressed={view === t.name} onClick={() => setView(t.name)}
                aria-label={`Take from ${new Date(Number(t.name.slice(5, -4))).toLocaleTimeString()}`}>
                <img src={takeUrl(id, t.name)} alt="" loading="lazy" />
                <span className="mono tiny">{data.chosen === t.name ? 'In use' : t.cost ? `$${t.cost.toFixed(2)}` : ''}</span>
              </button>
              <div className="row" style={{ gap: 4 }}>
                {data.chosen !== t.name && <button type="button" className="btn btn-sm" onClick={() => call(`${takeUrl(id, t.name).replace('/api', '')}/keep`, { method: 'POST' })}>Keep</button>}
                <button type="button" className="btn btn-sm" onClick={() => { if (view === t.name) setView(''); call(takeUrl(id, t.name).replace('/api', ''), { method: 'DELETE' }); }}>Discard</button>
              </div>
            </div>
          ))}
        </div>
      )}
      {data.chosen && (
        <button type="button" className="btn btn-sm" style={{ alignSelf: 'flex-start' }}
          onClick={() => { setView(''); call(`/timelines/${enc(id)}/poster`, { method: 'DELETE' }); }}>Use the stills poster instead</button>
      )}
    </section>
  );
}

export function CutPoster() {
  const { id } = useParams();
  const tl = useApi(`/timelines/${enc(id)}`);
  const doc = tl.data?.doc;
  usePageTitle(doc ? `Poster · ${doc.title}` : 'Poster');
  const crumb = useCrumbRoot();
  const reel = useReel(id, doc);
  const ai = useAiPoster(id);
  const [picked, setView] = useState('');
  // with nothing kept yet, the newest take is what the page shows
  const view = picked || (!ai.data?.chosen && ai.data?.takes?.[0]?.name) || '';

  if (tl.error && !tl.data) return <div className="page"><ErrorBox error={tl.error} onRetry={tl.reload} /></div>;
  if (!doc) return <div className="page"><Loading /></div>;

  const { prefs, setPrefs, ranked, used } = reel;
  const top = ranked.slice(0, 12);
  const usedSet = new Set(used.map((x) => x.src));
  const picks = prefs.picks.filter((p) => ranked.some((x) => x.src === p));
  const slots = posterSlots(prefs.layout);
  const toggle = (src) => {
    const next = picks.includes(src) ? picks.filter((p) => p !== src) : [...picks, src].slice(-4);
    setPrefs({ picks: next });
  };
  const back = `/cuts/${enc(id)}`;
  const subtitle = doc.tagline || doc.series?.title || '';
  const waiting = reel.stills.length > 0 && ranked.length < Math.min(12, reel.stills.length);

  return (
    <div className="page">
      <Crumbs items={[crumb.root, [doc.title, back, crumb.state], ['Poster']]} />
      <div className="reel-pp">
        <div className="stack" style={{ gap: 12 }}>
          {view || ai.data?.chosen
            ? <img className="reel-poster" src={view ? takeUrl(id, view) : aiPosterUrl(id, ai.data.chosen)} alt="AI poster" />
            : used.length
            ? <PosterCanvas srcs={used.map((x) => x.src)} layout={prefs.layout} title={prefs.title ? doc.title : ''} sub={subtitle} label="Poster preview" />
            : <div className="reel-poster reel-poster-empty" />}
          <Link className="btn btn-primary" to={back} state={crumb.state}>Done</Link>
        </div>
        <div className="stack" style={{ gap: 28, minWidth: 0 }}>
          <section className="stack" style={{ gap: 10 }} aria-labelledby="pp-layout">
            <h2 id="pp-layout">Layout</h2>
            <Seg label="Layout" options={[['frame', 'Frame'], ['strip', 'Strip'], ['grid', 'Grid']]}
              value={prefs.layout} onChange={(v) => setPrefs({ layout: v })} />
            <label className="check">
              <input type="checkbox" checked={prefs.title} onChange={(e) => setPrefs({ title: e.target.checked })} />
              Title on the poster
            </label>
          </section>
          <section className="stack" style={{ gap: 10 }} aria-labelledby="pp-stills">
            <div className="section-head">
              <h2 id="pp-stills">Stills</h2>
              <span className="n">{picks.length ? `${Math.min(picks.length, slots)} of ${slots} picked` : 'Automatic'}</span>
              {picks.length > 0 && <button type="button" className="btn btn-sm end" onClick={() => setPrefs({ picks: [] })}>Automatic</button>}
            </div>
            {!top.length && <p className="muted" style={{ margin: 0 }}>{reel.stills.length ? 'Scoring stills…' : 'This cut has no scenes.'}</p>}
            {top.length > 0 && (
              <div className="reel-cands">
                {top.map((x) => (
                  <button key={x.src} type="button" aria-pressed={picks.includes(x.src)}
                    className={usedSet.has(x.src) ? 'reel-used' : undefined}
                    aria-label={`Scene ${x.scene} at ${tc(x.t)}, score ${Math.round(x.score * 100)}`}
                    onClick={() => toggle(x.src)}>
                    <img src={x.src} alt="" loading="lazy" />
                    <span className="mono tiny">{x.scene} · {Math.round(x.score * 100)}</span>
                  </button>
                ))}
              </div>
            )}
            {waiting && top.length > 0 && <span className="tiny muted">Still scoring the rest…</span>}
          </section>
          <AiPoster id={id} stills={used.length ? used : top} title={prefs.title} view={view} setView={setView} />
          <section className="stack" style={{ gap: 10 }} aria-labelledby="pp-score">
            <h2 id="pp-score">Scoring</h2>
            <div className="reel-sliders">
              {WEIGHT_KEYS.map((k) => (
                <label key={k} htmlFor={`pp-w-${k}`}>
                  <span>{WEIGHT_LABELS[k]}</span>
                  <input id={`pp-w-${k}`} type="range" min="0" max="50" value={prefs.weights[k]}
                    onChange={(e) => setPrefs({ weights: { ...prefs.weights, [k]: Number(e.target.value) } })} />
                  <span className="mono tiny">{prefs.weights[k]}</span>
                </label>
              ))}
            </div>
          </section>
        </div>
      </div>
    </div>
  );
}
