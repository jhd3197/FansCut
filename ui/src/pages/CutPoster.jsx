// A cut's poster: layout, title, which stills, and how stills are scored. The choice is
// kept in this browser (see usePosterPrefs); Settings > Posters holds the defaults.
import { Link, useParams } from 'react-router';
import { useCrumbRoot, usePageTitle } from '../lib/hooks.js';
import { posterSlots, WEIGHT_KEYS } from '../lib/stills.js';
import { useApi } from '../lib/store.js';
import { Crumbs, enc, Seg } from '../components/kit.jsx';
import { ErrorBox, Loading } from '../components/ui.jsx';
import { PosterCanvas, tc, useReel } from './CutDetail.jsx';

const WEIGHT_LABELS = { exposure: 'Exposure', contrast: 'Contrast', color: 'Colour', detail: 'Detail' };

export function CutPoster() {
  const { id } = useParams();
  const tl = useApi(`/timelines/${enc(id)}`);
  const doc = tl.data?.doc;
  usePageTitle(doc ? `Poster · ${doc.title}` : 'Poster');
  const crumb = useCrumbRoot();
  const reel = useReel(id, doc);

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
          {used.length
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
