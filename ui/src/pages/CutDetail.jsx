// A cut (timeline recipe) as a page: its poster, a quick preview played from the sources,
// its segments in order (drag to reorder, Edit opens the film's scenes editor on that
// range), and what building it needs. Tags, faces and cut points live in the editors.
import { useEffect, useMemo, useRef, useState } from 'react';
import { Link, useParams } from 'react-router';
import { api } from '../lib/api.js';
import { useCrumbRoot, useLoad, usePageTitle, useUrlState } from '../lib/hooks.js';
import { fmtTime } from '../lib/time.js';
import { flatSegments, moveItem } from '../lib/timeline.js';
import { cleanWeights, drawPoster, measureStill, posterStills, rankStills, stillTimes } from '../lib/stills.js';
import { ArtPoster, Crumbs, enc, hm, KV, Poster } from '../components/kit.jsx';
import { ErrorBox, Loading } from '../components/ui.jsx';
import { personTo, segmentEditUrl, sourceFrameUrl } from '../components/ScenePlayer.jsx';
import { useApi } from '../lib/store.js';
import { CutPreview, filmsBySource } from './CutScenes.jsx';

const CREDIT_LABELS = {
  author: 'Made by', based_on: 'Based on', repo: 'Repository', repository: 'Repository',
  url: 'Link', version: 'Version', license: 'License',
};
const NOT_KV = new Set(['description', 'summary', 'about', 'repo', 'repository', 'url']);

// Poster motif from the recipe's category, falling back to its shape.
export function posterKind(doc) {
  const c = (doc.category || '').toLowerCase();
  if (c.includes('merge')) return 'merge';
  if (c.includes('canon') || c.includes('anime')) return 'canon';
  if (c.includes('order')) return 'order';
  if (c.includes('character')) return 'character';
  const movies = Object.values(doc.sources || {}).filter((s) => s.kind === 'movie').length;
  return movies > 1 && doc.parts.length === 1 ? 'merge' : 'supercut';
}

// "github.com/owner/repo" or a full URL -> a link; anything else -> null.
export function repoUrl(credits = {}) {
  const raw = credits.repo || credits.repository || credits.url || '';
  if (/^https?:\/\//.test(raw)) return raw;
  if (/^github\.com\//.test(raw)) return `https://${raw}`;
  if (/^[\w.-]+\/[\w.-]+$/.test(raw)) return `https://github.com/${raw}`;
  return null;
}

const repoShort = (url) => url?.replace(/^https?:\/\/(www\.)?/, '').replace(/^github\.com\//, '');

// A segment is on unless its `when` names a toggle that's off (or "!toggle" that's on).
export function segActive(seg, values) {
  const w = seg.when;
  if (!w) return true;
  return w.startsWith('!') ? !values?.[w.slice(1)] : Boolean(values?.[w]);
}

const srcName = (doc, src) => doc.sources[src]?.note || src;

function SourceList({ doc, missing, report, movieKeys, seriesKey }) {
  const missingSet = new Set(missing.map((m) => m.src));
  const order = [];
  for (const p of doc.parts) for (const s of p.segments) if (!order.includes(s.src)) order.push(s.src);
  for (const src of Object.keys(doc.sources)) if (!order.includes(src)) order.push(src);
  const movies = order.every((s) => doc.sources[s]?.kind === 'movie');
  return (
    <section aria-labelledby="c-src" className="stack" style={{ gap: 8, flex: '1 1 380px', minWidth: 0 }}>
      <h2 id="c-src">{movies ? 'Films' : 'Episodes'}</h2>
      <ul className="cut2-srcs">
        {order.map((src) => {
          const s = doc.sources[src] || {};
          const have = !missingSet.has(src);
          const sync = report?.sources?.[src];
          const [cls, label] = have ? ['c-ready', 'Owned'] : s.optional ? ['c-accent', 'Optional'] : ['c-danger', 'Missing'];
          const movieKey = s.kind === 'movie' ? movieKeys[`${s.tmdb}|${s.edition || ''}`] || movieKeys[`${s.tmdb}|`] : null;
          const to = movieKey ? `/movies/${enc(movieKey)}`
            : s.kind !== 'movie' && have ? `/series/${enc(seriesKey)}/episode/${enc(src)}` : null;
          const name = srcName(doc, src);
          return (
            <li key={src}>
              <span className={`tiny ${cls}`}>{label}</span>
              <span style={{ minWidth: 0 }}>
                {to ? <Link to={to} style={{ color: 'var(--text)' }}>{name}</Link> : name}
                {have && sync && !sync.matched && <span className="tiny c-warn"> · cut points not found in your copy</span>}
              </span>
              <span className="mono tiny muted">{[s.edition, s.duration ? fmtTime(s.duration) : null].filter(Boolean).join(' · ')}</span>
            </li>
          );
        })}
      </ul>
    </section>
  );
}

// "about 30 GB" / "about 800 MB"
export function gb(bytes) {
  if (!bytes) return '';
  return bytes >= 1e9 ? `about ${Math.round(bytes / 1e9)} GB` : `about ${Math.max(1, Math.round(bytes / 1e8) * 100)} MB`;
}

// "about 2 h 10 min" for an estimate
export function about(sec) {
  if (sec < 90) return 'about a minute';
  const m = Math.round(sec / 60);
  if (m < 60) return `about ${m} min`;
  const h = Math.floor(m / 60);
  const r = Math.round((m % 60) / 5) * 5;
  return `about ${h} h${r ? ` ${r} min` : ''}`;
}

// "Build on this PC": how long, and the quicker option if there is one.
function Estimate({ id, variant }) {
  const est = useApi(`/timelines/${enc(id)}/estimate${variant ? `?variant=${enc(variant)}` : ''}`, { quiet: true });
  const e = est.data;
  if (!e) return null;
  const encode = e.reasons.some((r) => r.startsWith('re-encoded'));
  return (
    <div className="stack tiny" style={{ gap: 4 }}>
      <span className="mono">Build on this PC: {e.label} · {about(e.seconds)}{e.best?.bytes ? ` · ${gb(e.best.bytes)}` : ''}</span>
      {e.fast && <span className="muted">Faster: {e.fast.name} · {about(e.fast.seconds)} · {gb(e.fast.bytes)}</span>}
      {encode && !e.gpu && <span className="muted">GPU encoding is off: <Link to="/settings?section=render">Settings</Link></span>}
    </div>
  );
}

function Variants({ view, value, onChange }) {
  const rows = view?.variants || [];
  const offToggles = (view?.toggles || []).filter((t) => !t.enabled);
  if (rows.length < 2 && !offToggles.length) return null;
  return (
    <fieldset className="cut2-variants">
      <legend>Variant</legend>
      {rows.length > 1 && (
        <div className="row wrap" style={{ gap: 8 }}>
          {rows.map((v) => (
            <label key={v.id || 'default'} className="choice" title={[v.description, ...v.blocked.map((b) => `${b.label}: ${b.reason}`)].filter(Boolean).join('\n') || undefined}>
              <input type="radio" name="variant" checked={value === v.id} onChange={() => onChange(v.id)} />
              <span>{v.label} <span className="tiny muted">{hm(v.runtime / 60)}</span>{v.blocked.length > 0 && <span className="tiny c-danger"> · blocked</span>}</span>
            </label>
          ))}
        </div>
      )}
      {offToggles.length > 0 && (
        <ul className="tiny muted" style={{ margin: 0, paddingLeft: 18 }}>
          {offToggles.map((t) => <li key={t.id}>{t.label} is off: {t.reason}</li>)}
        </ul>
      )}
    </fieldset>
  );
}

// -- reel: stills, scoring and the poster (shared with CutPoster.jsx) -------------------

export const frameUrl = (id, src, t) => `/api/timelines/${enc(id)}/sources/${enc(src)}/frame?t=${t}`;

// "1:05:09.5": always with hours, a tenth of a second.
export function tc(sec) {
  const t = Math.round(Math.max(0, sec) * 10) / 10;
  const h = Math.floor(t / 3600);
  const m = Math.floor((t % 3600) / 60);
  return `${h}:${String(m).padStart(2, '0')}:${(t % 60).toFixed(1).padStart(4, '0')}`;
}

// The stills of every scene (`perScene` evenly spaced in each segment), for picking.
export function cutStills(id, doc, perScene) {
  if (!doc) return [];
  return flatSegments(doc).segs.flatMap((s) => stillTimes(s.seg.start, s.seg.end, perScene)
    .map((t, j) => ({ src: frameUrl(id, s.seg.src, t), t, j, scene: s.n, source: s.seg.src })));
}

// Measure each still in the browser, a few at a time; failures (a busy server) get
// one more try later. Returns { src: metrics } as they come in.
export function useStillMetrics(stills) {
  const [metrics, setMetrics] = useState({});
  const key = stills.map((s) => s.src).join('|');
  useEffect(() => {
    let alive = true;
    const todo = key ? key.split('|') : [];
    const retry = [];
    let pending = {};
    let timer = null;
    const flush = () => {
      timer = null;
      const add = pending;
      pending = {};
      if (alive) setMetrics((m) => ({ ...m, ...add }));
    };
    const worker = async () => {
      while (alive && todo.length) {
        const src = todo.shift();
        const m = await measureStill(src);
        if (m) { pending[src] = m; timer ??= setTimeout(flush, 200); } else retry.push(src);
      }
    };
    let again = null;
    Promise.all([worker(), worker(), worker()]).then(() => {
      if (!alive || !retry.length) return;
      again = setTimeout(() => { todo.push(...retry.splice(0)); worker(); worker(); }, 15000);
    });
    return () => { alive = false; clearTimeout(timer); clearTimeout(again); };
  }, [key]);
  return metrics;
}

// This cut's poster choices, kept in this browser (localStorage), over the defaults
// from Settings > Posters.
const prefKey = (id) => `fanscut.poster.${id}`;
function readPrefs(id) {
  try { return JSON.parse(localStorage.getItem(prefKey(id))) || {}; } catch { return {}; }
}
export function usePosterPrefs(id) {
  const settings = useApi('/settings', { quiet: true });
  const p = settings.data?.posters || {};
  const [mine, setMine] = useState({});
  const own = mine[id] ?? readPrefs(id);
  const prefs = {
    layout: own.layout || p.layout || 'frame',
    title: own.title ?? p.title ?? true,
    weights: cleanWeights({ ...p.weights, ...own.weights }),
    picks: own.picks || [],
    perScene: Number(p.stills) || 4,
  };
  const set = (patch) => {
    const next = { ...own, ...patch };
    try { localStorage.setItem(prefKey(id), JSON.stringify(next)); } catch { /* private mode: keep it for this visit */ }
    setMine((m) => ({ ...m, [id]: next }));
  };
  return [prefs, set];
}

// Everything the reel and the poster need: the stills, their measures, the ranking.
export function useReel(id, doc) {
  const [prefs, setPrefs] = usePosterPrefs(id);
  const stills = useMemo(() => cutStills(id, doc, prefs.perScene), [id, doc, prefs.perScene]);
  const metrics = useStillMetrics(stills);
  const w = prefs.weights;
  const wKey = `${w.exposure},${w.contrast},${w.color},${w.detail}`;
  const ranked = useMemo(() => {
    const [exposure, contrast, color, detail] = wKey.split(',').map(Number);
    return rankStills(stills, metrics, { exposure, contrast, color, detail });
  }, [stills, metrics, wKey]);
  const used = posterStills(ranked, prefs.layout, prefs.picks);
  return { prefs, setPrefs, stills, metrics, ranked, used };
}

export function PosterCanvas({ srcs, layout, title, sub, label = 'Poster' }) {
  const ref = useRef(null);
  const seq = useRef(0);
  const key = srcs.join('|');
  useEffect(() => {
    const mine = ++seq.current;
    drawPoster(ref.current, key ? key.split('|') : [], { layout, title, sub, stale: () => mine !== seq.current });
  }, [key, layout, title, sub]);
  return <canvas ref={ref} className="reel-poster" width={600} height={900} role="img" aria-label={label} />;
}

// A person's row on the film's cast page (its search box preset to the name).
// A person stays in the film: the scenes editor filtered to them.
export const castTo = (filmKey, name) => personTo(filmKey, name);


// -- segments: play order, drag (or Alt+Up/Down) to reorder within a part ----------------

function SegmentList({ id, doc, segs, filmOf, values, report, current, onPick, onMove, saving, error }) {
  const [drag, setDrag] = useState(null); // flat index of the row being dragged
  const [over, setOver] = useState(null);
  const listRef = useRef(null);
  const refocus = useRef(null); // after a keyboard move, the row whose grip gets focus
  useEffect(() => {
    if (refocus.current == null) return;
    listRef.current?.querySelector(`[data-i="${refocus.current}"] .cut2-grip`)?.focus();
    refocus.current = null;
  }, [segs]);

  const status = {};
  for (const src of Object.values(report?.sources || {})) Object.assign(status, src.segments || {});
  let total = 0;
  for (const s of segs) total += s.dur;
  const canDrop = (i) => drag != null && drag !== i && segs[drag]?.pi === segs[i]?.pi;
  const end = () => { setDrag(null); setOver(null); };

  const rows = [];
  segs.forEach((s, i) => {
    if (doc.parts.length > 1 && s.si === 0) {
      rows.push(<li key={`p${s.pi}`} className="cut2-part tiny muted">Part {s.pi + 1}{doc.parts[s.pi].title ? ` · ${doc.parts[s.pi].title}` : ''}</li>);
    }
    const on = segActive(s.seg, values);
    const gone = status[`${s.pi + 1}:${s.si + 1}`] === 'missing';
    const film = filmOf[s.seg.src];
    const name = film?.title || srcName(doc, s.seg.src);
    const mid = (s.seg.start + s.seg.end) / 2;
    const cls = ['cut2-seg', !on || gone ? 'cut2-off' : '', drag === i ? 'cut2-dragging' : '',
      over === i ? (drag < i ? 'cut2-over-after' : 'cut2-over-before') : ''].filter(Boolean).join(' ');
    rows.push(
      <li key={`${s.pi}:${s.si}`} data-i={i} className={cls} aria-current={s.n === current}
        draggable={!saving}
        onDragStart={(e) => { setDrag(i); e.dataTransfer.effectAllowed = 'move'; e.dataTransfer.setData('text/plain', String(s.n)); }}
        onDragOver={(e) => { if (canDrop(i)) { e.preventDefault(); e.dataTransfer.dropEffect = 'move'; setOver(i); } }}
        onDragLeave={() => setOver((o) => (o === i ? null : o))}
        onDrop={(e) => { e.preventDefault(); if (canDrop(i)) onMove(drag, i); end(); }}
        onDragEnd={end}
        onKeyDown={(e) => {
          if (!e.altKey || (e.key !== 'ArrowUp' && e.key !== 'ArrowDown')) return;
          e.preventDefault();
          const j = i + (e.key === 'ArrowUp' ? -1 : 1);
          if (saving || segs[j]?.pi !== s.pi) return;
          refocus.current = j;
          onMove(i, j);
        }}>
        <button type="button" className="cut2-grip" aria-label={`Move segment ${s.n}`} title="Drag, or Alt+Up / Alt+Down">⠿</button>
        <button type="button" className="cut2-pick" onClick={() => onPick(s.n)}>
          <span className="mono tiny muted">{s.n}</span>
          <img src={sourceFrameUrl(id, s.seg.src, mid)} alt="" loading="lazy" draggable={false} />
          <span className="cut2-name">
            {name}
            {!on && <span className="tiny muted"> · off in this variant</span>}
            {gone && <span className="tiny c-warn"> · not in your edition</span>}
          </span>
          <span className="mono tiny muted">{fmtTime(s.seg.start)}–{fmtTime(s.seg.end)} · {fmtTime(s.dur)}</span>
        </button>
        {film
          ? <Link className="btn btn-sm" to={segmentEditUrl(film.key, s.seg, id, s.n)}>Edit</Link>
          : <button type="button" className="btn btn-sm" disabled title="This film is not in your library">Edit</button>}
      </li>,
    );
  });

  return (
    <section aria-labelledby="c-segs" className="stack" style={{ gap: 8, minWidth: 0 }}>
      <div className="section-head">
        <h2 id="c-segs">Segments</h2>
        <span className="n">{segs.length} · {fmtTime(total)}</span>
        {(saving || error) && (
          <span className={`end tiny ${error && !saving ? 'c-danger' : 'muted'}`} role="status">{saving ? 'Saving…' : error}</span>
        )}
      </div>
      <ol className="cut2-segs" ref={listRef}>{rows}</ol>
    </section>
  );
}

const sameSegs = (a = [], b = []) => a.length === b.length && a.every((x, i) => x.src === b[i].src
  && Math.abs(x.start - b[i].start) < 0.001 && Math.abs(x.end - b[i].end) < 0.001);

export function CutDetail() {
  const { id } = useParams();
  const tl = useApi(`/timelines/${enc(id)}`);
  usePageTitle(tl.data?.title);
  const variants = useApi(`/timelines/${enc(id)}/variants`);
  const sync = useApi(`/timelines/${enc(id)}/sync`);
  const hasMovies = Object.values(tl.data?.doc?.sources || {}).some((s) => s.kind === 'movie');
  const movies = useLoad(() => (hasMovies ? api('/library/movies') : Promise.resolve({ movies: [] })), [hasMovies]);
  const films = useApi(hasMovies ? '/scenes/films' : null, { quiet: true });
  const [variant, setVariant] = useUrlState('variant');
  const [scene, setScene] = useUrlState('scene', '1');
  const player = useRef(null);
  const crumb = useCrumbRoot();
  const aiState = useApi(`/timelines/${enc(id)}/poster/ai`, { quiet: true });
  const aiChosen = aiState.data?.chosen;
  // the stills poster is only measured when there is no AI poster in use
  const reel = useReel(id, aiState.loading || aiChosen ? null : tl.data?.doc);
  const [pending, setPending] = useState(null); // the reordered doc while it saves
  const [saving, setSaving] = useState(false);
  const [saveErr, setSaveErr] = useState('');
  const doc = pending || tl.data?.doc;
  const segs = useMemo(() => flatSegments(doc).segs, [doc]);
  const filmOf = useMemo(() => filmsBySource(doc, films.data?.films), [doc, films.data]);
  const srcOrder = useMemo(() => Object.keys(doc?.sources || {}), [doc]);

  if (tl.error && !tl.data) return <div className="page"><ErrorBox error={tl.error} onRetry={tl.reload} /></div>;
  if (!tl.data) return <div className="page"><Loading /></div>;

  // Move flat row `from` to `to` (same part): GET the doc fresh, reorder that part, PUT it all.
  const move = async (from, to) => {
    const a = segs[from];
    const b = segs[to];
    if (saving || !a || !b || a.pi !== b.pi || from === to) return;
    const local = structuredClone(doc);
    local.parts[a.pi].segments = moveItem(local.parts[a.pi].segments, a.si, b.si);
    setPending(local);
    setSaving(true);
    setSaveErr('');
    try {
      const fresh = (await api(`/timelines/${enc(id)}`)).doc;
      const list = fresh.parts?.[a.pi]?.segments;
      if (!sameSegs(list, doc.parts[a.pi].segments)) throw new Error('This cut changed somewhere else. Reloaded it: try again.');
      fresh.parts[a.pi].segments = moveItem(list, a.si, b.si);
      await api(`/timelines/${enc(id)}`, { method: 'PUT', body: fresh });
      if (Number(scene) === a.n) setScene(String(b.n));
    } catch (e) {
      setSaveErr(e.message || String(e));
    }
    await tl.reload();
    sync.reload();
    setPending(null);
    setSaving(false);
  };

  const credits = doc.credits || {};
  const url = repoUrl(credits);
  const report = sync.data?.synced ? sync.data : null;
  const rows = variants.data?.variants || [];
  const chosen = rows.find((v) => v.id === variant) || rows[0];
  const runtime = chosen ? chosen.runtime : segs.reduce((t, s) => t + s.dur, 0);
  const kindLabel = doc.category || (doc.parts.length > 1 ? `${doc.parts.length}-part cut` : 'Cut');
  const subtitle = doc.tagline || doc.series?.title || '';
  const meta = [kindLabel, hm(runtime / 60), credits.version ? `v${credits.version.replace(/^v/, '')}` : null].filter(Boolean).join(' · ');
  const description = credits.description || credits.summary || credits.about;
  const movieKeys = {};
  for (const m of movies.data?.movies || []) {
    if (m.tmdb_id == null) continue;
    movieKeys[`${m.tmdb_id}|${m.edition || ''}`] ??= m.key;
    movieKeys[`${m.tmdb_id}|`] ??= m.key;
  }
  // the films' own posters, in recipe order, while the stills poster has nothing yet
  const artByKey = new Map((movies.data?.movies || []).map((m) => [m.key, m.art]));
  const heroArt = [...new Set(Object.values(doc.sources).filter((s) => s.kind === 'movie')
    .map((s) => artByKey.get(movieKeys[`${s.tmdb}|${s.edition || ''}`] || movieKeys[`${s.tmdb}|`]))
    .filter(Boolean))];
  const buildTo = `/cuts/${enc(id)}/build${variant ? `?variant=${enc(variant)}` : ''}`;
  const kv = Object.entries(credits).filter(([k]) => !NOT_KV.has(k)).map(([k, v]) => [
    CREDIT_LABELS[k] || k.replace(/_/g, ' ').replace(/^./, (c) => c.toUpperCase()), v,
  ]);
  const n = Math.min(Math.max(Number(scene) || 1, 1), Math.max(segs.length, 1));
  const name = (src) => filmOf[src]?.title || srcName(doc, src);

  return (
    <div className="page">
      <Crumbs items={[crumb.root]} />
      <div className="hero">
        <div className="stack" style={{ width: 220, flex: 'none', gap: 10 }}>
          {aiChosen
            ? <img className="reel-poster" src={`/api/timelines/${enc(id)}/poster?v=${encodeURIComponent(aiChosen)}`} alt={`${doc.title} poster`} />
            : reel.used.length > 0
            ? <PosterCanvas srcs={reel.used.map((x) => x.src)} layout={reel.prefs.layout} title={reel.prefs.title ? doc.title : ''} sub={subtitle} />
            : <ArtPoster keys={heroArt} layout={heroArt.length > 1 ? 'split' : 'single'}
              fallback={<Poster title={doc.title} sub={subtitle} kind={posterKind(doc)} bare />} />}
          <Link className="btn" to={`/cuts/${enc(id)}/poster`} state={crumb.state}>Poster</Link>
        </div>
        <div className="hero-body" style={{ gap: 16 }}>
          <div className="stack" style={{ gap: 6 }}>
            <h1 className="display" style={{ font: '700 40px/1 var(--display)' }}>{doc.title}</h1>
            {subtitle && <span style={{ fontSize: 16, color: 'var(--text2)' }}>{subtitle}</span>}
            <span className="mono muted" style={{ fontSize: 13 }}>{meta}</span>
          </div>
          {description && <p style={{ margin: 0, color: 'var(--text2)', maxWidth: 620 }}>{description}</p>}
          <div className="row wrap">
            <Link className="btn btn-primary" to={buildTo} state={crumb.state}>Build</Link>
            <Link className="btn" to={`/cuts/${enc(id)}/edit`} state={crumb.state}>Edit</Link>
          </div>
        </div>
      </div>
      {segs.length > 0 && (
        <div className="cut2-main">
          <CutPreview ref={player} id={id} segs={segs} filmOf={filmOf} srcOrder={srcOrder} srcName={name} n={n}
            onN={(k) => setScene(String(k))} buildTo={buildTo} crumbState={crumb.state} />
          <SegmentList id={id} doc={doc} segs={segs} filmOf={filmOf} values={chosen?.toggles} report={report}
            current={n} onPick={(k) => { setScene(String(k)); player.current?.seekSegment(k, true); }} onMove={move} saving={saving} error={saveErr} />
        </div>
      )}
      <div className="cut2-more">
        <div className="stack" style={{ gap: 14, flex: '1 1 380px', minWidth: 0 }}>
          <Variants view={variants.data} value={chosen?.id ?? ''} onChange={setVariant} />
          <Estimate id={id} variant={chosen?.id || ''} />
          {variants.error && <ErrorBox error={variants.error} onRetry={variants.reload} />}
        </div>
        <SourceList doc={doc} missing={tl.data.missing || []} report={report} movieKeys={movieKeys} seriesKey={tl.data.series_key} />
        <section aria-labelledby="c-about" className="stack" style={{ gap: 8, flex: '1 1 380px', minWidth: 0 }}>
          <h2 id="c-about">About</h2>
          <KV rows={[
            ...kv,
            ...(url ? [['Repository', <a key="r" href={url} target="_blank" rel="noreferrer" className="mono tiny">{repoShort(url)}</a>]] : []),
            ['Cut points', report ? `Matched to your files${report.updated_at ? ` on ${report.updated_at.replace('T', ' ')}` : ''}` : 'Not matched to your files yet'],
          ]} />
        </section>
      </div>
    </div>
  );
}
