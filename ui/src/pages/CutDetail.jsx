// A cut (timeline recipe) as a page: what it is, what it needs, what's in it, and its
// reel: a still per scene, each scene's stills, faces, tags and cut points, and a poster
// made from the best stills.
import { useEffect, useMemo, useRef, useState } from 'react';
import { Link, useParams } from 'react-router';
import { api } from '../lib/api.js';
import { useAct, useCrumbRoot, useLoad, usePageTitle, useUrlState } from '../lib/hooks.js';
import { fmtTime } from '../lib/time.js';
import { flatSegments, nudge } from '../lib/timeline.js';
import { cleanWeights, drawPoster, measureStill, posterStills, rankStills, scoreStill, stillTimes } from '../lib/stills.js';
import { ArtPoster, Crumbs, enc, hm, KV, Poster, SRC_COLOURS } from '../components/kit.jsx';
import { ErrorBox, Loading } from '../components/ui.jsx';
import { useApi } from '../lib/store.js';

const CREDIT_LABELS = {
  author: 'Made by', based_on: 'Based on', repo: 'Repository', repository: 'Repository',
  url: 'Link', version: 'Version', license: 'License',
};
const NOT_KV = new Set(['description', 'summary', 'about']);

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
    <section aria-labelledby="c-src" className="card" style={{ flex: '1 1 420px', overflow: 'hidden', padding: 0 }}>
      <h2 id="c-src" style={{ padding: '14px 16px' }}>Uses these {movies ? 'films' : 'episodes'}</h2>
      <ul style={{ listStyle: 'none', margin: 0, padding: 0 }}>
        {order.map((src) => {
          const s = doc.sources[src] || {};
          const have = !missingSet.has(src);
          const sync = report?.sources?.[src];
          const [cls, label] = have ? ['c-ready', 'Owned'] : s.optional ? ['c-accent', 'Optional'] : ['c-danger', 'Missing'];
          const movieKey = s.kind === 'movie' ? movieKeys[`${s.tmdb}|${s.edition || ''}`] || movieKeys[`${s.tmdb}|`] : null;
          const to = movieKey ? `/movies/${enc(movieKey)}`
            : s.kind !== 'movie' && have ? `/series/${enc(seriesKey)}/episode/${enc(src)}` : null;
          const name = srcName(doc, src);
          const facts = [s.edition, s.duration ? fmtTime(s.duration) : null].filter(Boolean).join(' · ');
          return (
            <li key={src} style={{ display: 'grid', gridTemplateColumns: '64px minmax(0, 1fr) auto', gap: 12, padding: '10px 16px', borderTop: '1px solid var(--raised)', alignItems: 'center' }}>
              <span className={`tiny ${cls}`}>{label}</span>
              <span style={{ display: 'flex', flexDirection: 'column', minWidth: 0 }}>
                {to ? <Link to={to} style={{ color: 'var(--text)' }}>{name}</Link> : <span>{name}</span>}
                {name !== src && <span className="mono tiny dim">{src}</span>}
                {have && sync && !sync.matched && <span className="tiny c-warn">Cut points not found in your copy{sync.reason ? `: ${sync.reason}` : ''}</span>}
                {!have && s.optional && <span className="tiny muted">Only needed for the variants that use it</span>}
              </span>
              {!have && movieKey
                ? <Link to={`/movies/${enc(movieKey)}`} className="tiny">Check file</Link>
                : <span className="mono tiny muted">{facts}</span>}
            </li>
          );
        })}
      </ul>
    </section>
  );
}

function Segments({ doc, values, report }) {
  const colour = {};
  let n = 0;
  for (const p of doc.parts) for (const s of p.segments) if (!(s.src in colour)) colour[s.src] = SRC_COLOURS[n++ % SRC_COLOURS.length];
  const status = {};
  for (const src of Object.values(report?.sources || {})) Object.assign(status, src.segments || {});
  let i = 0;
  let total = 0;
  let count = 0;
  const rows = [];
  doc.parts.forEach((p, pi) => {
    if (doc.parts.length > 1) {
      rows.push(
        <li key={`p${pi}`} style={{ padding: '10px 16px 6px', borderTop: '1px solid var(--raised)' }} className="tiny muted">
          Part {pi + 1} · {p.title}
        </li>,
      );
    }
    p.segments.forEach((s, si) => {
      i += 1;
      const on = segActive(s, values);
      const gone = status[`${pi + 1}:${si + 1}`] === 'missing';
      const dur = s.end - s.start;
      if (on && !gone) { total += dur; count += 1; }
      rows.push(
        <li key={`${pi}:${si}`} style={{ display: 'grid', gridTemplateColumns: '28px 64px minmax(0, 1fr) 80px', gap: 12, alignItems: 'center', padding: '9px 16px', borderTop: '1px solid var(--raised)', opacity: on && !gone ? 1 : 0.5 }}>
          <span className="mono tiny muted">{i}</span>
          <span className="mono tiny" style={{ color: colour[s.src], overflow: 'hidden', textOverflow: 'ellipsis' }}>{s.src}</span>
          <span style={{ minWidth: 0 }}>
            {s.desc || <span className="muted">{fmtTime(s.start)} – {fmtTime(s.end)}</span>}
            {!on && <span className="tiny muted"> · off in this variant</span>}
            {gone && <span className="tiny c-warn"> · not in your edition</span>}
          </span>
          <span className="mono tiny muted" style={{ textAlign: 'right' }}>{fmtTime(dur)}</span>
        </li>,
      );
    });
  });
  return (
    <section aria-labelledby="c-in" className="card" style={{ flex: '1 1 420px', overflow: 'hidden', padding: 0 }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline', padding: '14px 16px' }}>
        <h2 id="c-in">What’s in it</h2>
        <span className="mono tiny muted">{count} segment{count === 1 ? '' : 's'} · {fmtTime(total)}</span>
      </div>
      <ol style={{ listStyle: 'none', margin: 0, padding: 0 }}>{rows}</ol>
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

// "Build on this PC": how long, why, and the quick way to make it faster.
function Estimate({ id, variant }) {
  const est = useApi(`/timelines/${enc(id)}/estimate${variant ? `?variant=${enc(variant)}` : ''}`, { quiet: true });
  const e = est.data;
  if (!e) return null;
  const encode = e.reasons.some((r) => r.startsWith('re-encoded'));
  return (
    <section aria-labelledby="c-est" className="card" style={{ display: 'flex', flexDirection: 'column', gap: 6, maxWidth: 620 }}>
      <h2 id="c-est" style={{ fontSize: 14 }}>Build on this PC: {e.label} · {about(e.seconds)}{e.best?.bytes ? ` · ${gb(e.best.bytes)}` : ''}</h2>
      <span style={{ fontSize: 13, color: 'var(--text2)' }}>Why: {e.reasons.join(', ')}</span>
      {e.fast && (
        <span style={{ fontSize: 13 }}>Faster option: {e.fast.name} · {about(e.fast.seconds)} · {gb(e.fast.bytes)}. Pick it on the build page.</span>
      )}
      {encode && !e.gpu && (
        <span style={{ fontSize: 13 }}>{e.fast ? 'Also' : 'Faster option'}: turn on the GPU encoder in <Link to="/settings?section=render">Settings</Link>, usually several times quicker.</span>
      )}
      <span className="tiny muted">{e.basis === 'measured' ? 'Timed from your last builds on this PC.' : 'From typical speeds; it gets closer after your first build.'}</span>
    </section>
  );
}

function Variants({ view, value, onChange }) {
  const rows = view?.variants || [];
  const offToggles = (view?.toggles || []).filter((t) => !t.enabled);
  if (rows.length < 2 && !offToggles.length) return null;
  return (
    <fieldset>
      <legend>Variant</legend>
      {rows.length > 1 && (
        <div style={{ display: 'flex', flexWrap: 'wrap', gap: 10 }}>
          {rows.map((v) => (
            <label key={v.id || 'default'} className="choice" style={{ flex: '1 1 240px' }}>
              <input type="radio" name="variant" checked={value === v.id} onChange={() => onChange(v.id)} style={{ margin: '3px 0 0' }} />
              <span style={{ display: 'flex', flexDirection: 'column', gap: 2 }}>
                <b style={{ fontWeight: 600 }}>{v.label}</b>
                {v.description && <span className="tiny" style={{ color: 'var(--text2)' }}>{v.description}</span>}
                <span className="tiny muted">{hm(v.runtime / 60)}</span>
                {v.blocked.map((b) => (
                  <span key={b.id} className="tiny c-danger">{b.label}: {b.reason}</span>
                ))}
              </span>
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

// A tag's vocab name; else its id without the "fc:" prefix, dashes as spaces.
const tagLabel = (names, id) => names[id] || String(id).replace(/^fc:/, '').replace(/[-_]/g, ' ');

// Is the element on screen yet? (once true, stays true)
function useSeen() {
  const ref = useRef(null);
  const [seen, setSeen] = useState(() => typeof IntersectionObserver === 'undefined');
  useEffect(() => {
    if (seen || !ref.current) return undefined;
    const io = new IntersectionObserver((es) => { if (es.some((e) => e.isIntersecting)) setSeen(true); }, { rootMargin: '300px' });
    io.observe(ref.current);
    return () => io.disconnect();
  }, [seen]);
  return [ref, seen];
}

function FaceBoxes({ film, t, seen }) {
  const path = seen && film?.indexed ? `/scenes/faces?key=${enc(film.key)}&t=${t}` : null;
  const { data } = useApi(path, { quiet: true });
  return (data?.faces || []).map((f, i) => {
    const [x, y, w, h] = f.box || [];
    if (w == null) return null;
    const who = f.character || f.name;
    const style = { left: `${x * 100}%`, top: `${y * 100}%`, width: `${w * 100}%`, height: `${h * 100}%` };
    if (!who) return <span key={i} className="reel-fb reel-fb-faint" style={style} />;
    return (
      <span key={i} className={`reel-fb ${f.sure ? 'reel-fb-sure' : 'reel-fb-maybe'}`} style={style}>
        <em>{f.sure ? who : `${who}?`}</em>
      </span>
    );
  });
}

// Tag names and the people (with cast photos) of the tags overlapping this scene.
function SceneTags({ film, seg, seen, names }) {
  const tags = useApi(seen && film?.indexed ? `/scenes/tags?key=${enc(film.key)}` : null, { quiet: true });
  const hits = (tags.data?.tags || []).filter((x) => x.status !== 'rejected' && x.start < seg.end && x.end > seg.start);
  const people = [];
  for (const x of hits) {
    for (const p of x.people || []) {
      const person = typeof p === 'string' ? { name: p } : p;
      const k = (person.character || person.name || '').toLowerCase();
      if (k && !people.some((q) => (q.character || q.name || '').toLowerCase() === k)) people.push(person);
    }
  }
  const cast = useApi(people.length ? `/scenes/cast?key=${enc(film.key)}` : null, { quiet: true });
  if (!film?.indexed) return <p className="muted tiny reel-note">Not analysed</p>;
  if (!tags.data) return null;
  if (!hits.length) return <p className="muted tiny reel-note">No tags here</p>;
  const labels = [...new Set(hits.flatMap((x) => x.tags || []).map((t) => tagLabel(names, t)))];
  const low = (v) => (v || '').toLowerCase();
  const castOf = (p) => (cast.data?.cast || []).find((c) => (p.name && low(c.name) === low(p.name))
    || (p.character && low(c.character).includes(low(p.character)))
    || (!p.character && p.name && low(c.character).includes(low(p.name))));
  return (
    <>
      <div className="reel-tags">{labels.map((l) => <span key={l} className="chip">{l}</span>)}</div>
      {people.length > 0 && (
        <ul className="reel-people">
          {people.map((p) => {
            const c = castOf(p);
            const label = p.character || c?.character || p.name;
            const actor = c?.name || (p.character ? p.name : null);
            return (
              <li key={label}>
                {c?.photo ? <img src={c.photo} alt="" loading="lazy" /> : <span className="reel-noface">{(label || '?').slice(0, 1)}</span>}
                <span><b>{label}</b>{actor && actor !== label && <span className="muted"> · {actor}</span>}</span>
              </li>
            );
          })}
        </ul>
      )}
    </>
  );
}

function SceneRow({ s, cur, moved, film, name, stills, metrics, weights, poster, names, onNudge, onReset }) {
  const [ref, seen] = useSeen();
  const [pick, setPick] = useState(null);
  let best = -1;
  stills.forEach((x, j) => {
    if (metrics[x.src] && (best < 0 || scoreStill(metrics[x.src], weights) > scoreStill(metrics[stills[best].src], weights))) best = j;
  });
  const main = stills[pick ?? (best >= 0 ? best : Math.floor(stills.length / 2))];
  const field = (edge, label) => (
    <span className="reel-tf">
      <span className="muted tiny">{label}</span>
      <button type="button" className="btn btn-sm" onClick={() => onNudge(edge, -0.5)} aria-label={`${label} back half a second, scene ${s.n}`}>−</button>
      <span className="mono">{tc(cur[edge])}</span>
      <button type="button" className="btn btn-sm" onClick={() => onNudge(edge, 0.5)} aria-label={`${label} forward half a second, scene ${s.n}`}>+</button>
    </span>
  );
  return (
    <article ref={ref} className="reel-scene" id={`scene-${s.n}`} tabIndex={-1} aria-label={`Scene ${s.n}`}>
      <div style={{ minWidth: 0 }}>
        {main && (
          <div className="reel-main">
            <img src={main.src} alt={`Still from scene ${s.n} at ${tc(main.t)}`} loading="lazy" />
            <FaceBoxes film={film} t={main.t} seen={seen} />
            {poster.has(main.src) && <span className="reel-pk">Poster</span>}
          </div>
        )}
        <div className="reel-thumbs" style={{ '--n': stills.length }}>
          {stills.map((x, j) => (
            <button key={x.src} type="button" aria-pressed={x === main} aria-label={`Still at ${tc(x.t)}`} onClick={() => setPick(j)}>
              <img src={x.src} alt="" loading="lazy" />
            </button>
          ))}
        </div>
      </div>
      <div className="stack" style={{ gap: 10, minWidth: 0 }}>
        <div className="reel-shead">
          <span className="reel-num">{s.n}</span>
          <span className="reel-src">{name}</span>
          <span className="mono tiny muted">{fmtTime(cur.end - cur.start, 1)}</span>
        </div>
        {s.seg.desc && <p style={{ margin: 0 }}>{s.seg.desc}</p>}
        <div className="row wrap" style={{ gap: 14 }}>
          {field('start', 'In')}
          {field('end', 'Out')}
          {moved && <button type="button" className="btn btn-sm btn-ghost" onClick={onReset}>Reset</button>}
        </div>
        <SceneTags film={film} seg={cur} seen={seen} names={names} />
      </div>
    </article>
  );
}

const sameRange = (a, b) => Boolean(a && b) && Math.abs(a.start - b.start) < 0.001 && Math.abs(a.end - b.end) < 0.001;

function Reel({ id, doc, reel, films, onSaved }) {
  const { segs } = flatSegments(doc);
  // the cut points as the page first loaded them (Reset goes back to these)
  const [original] = useState(() => Object.fromEntries(segs.map((s) => [`${s.pi}:${s.si}`, { start: s.seg.start, end: s.seg.end }])));
  const [drafts, setDrafts] = useState({});
  const { act, busy } = useAct();
  const vocab = useApi('/scenes/vocab', { quiet: true });
  const names = {};
  for (const t of [...(vocab.data?.tags || []), ...(vocab.data?.cut_tags || [])]) names[t.id] = t.name;
  const filmOf = (src) => {
    const s = doc.sources[src];
    if (s?.kind !== 'movie' || s.tmdb == null) return null;
    const all = (films || []).filter((f) => String(f.tmdb_id) === String(s.tmdb));
    return all.find((f) => f.indexed) || all.find((f) => f.on_disk) || all[0] || null;
  };
  const byScene = {};
  for (const x of reel.stills) (byScene[x.scene] ||= []).push(x);
  const poster = new Set(reel.used.map((x) => x.src));
  const dirty = segs.some((s) => { const d = drafts[`${s.pi}:${s.si}`]; return d && !sameRange(d, s.seg); });
  const setDraft = (s, v) => setDrafts((d) => ({ ...d, [`${s.pi}:${s.si}`]: { start: v.start, end: v.end, src: s.seg.src } }));

  // GET the whole doc again (it may have changed elsewhere), apply the moved cut points, PUT it.
  const save = () => act(async () => {
    const fresh = (await api(`/timelines/${enc(id)}`)).doc;
    for (const [k, v] of Object.entries(drafts)) {
      const [pi, si] = k.split(':').map(Number);
      const seg = fresh.parts?.[pi]?.segments?.[si];
      if (seg && seg.src === v.src) { seg.start = v.start; seg.end = v.end; }
    }
    await api(`/timelines/${enc(id)}`, { method: 'PUT', body: fresh });
    await onSaved();
    setDrafts({});
  }, 'Cut points saved. Sync was reset: run it again before building.');

  const jump = (n) => {
    const el = document.getElementById(`scene-${n}`);
    el?.scrollIntoView({ behavior: 'smooth', block: 'start' });
    el?.focus({ preventScroll: true });
  };

  return (
    <>
      <nav className="reel-strip" aria-label="Scenes">
        {segs.map((s) => {
          const mid = Math.round(((s.seg.start + s.seg.end) / 2) * 10) / 10;
          return (
            <button key={s.n} type="button" onClick={() => jump(s.n)}
              style={{ flexGrow: Math.max(s.dur, 1), backgroundImage: `url("${frameUrl(id, s.seg.src, mid)}")` }}
              aria-label={`Scene ${s.n}, ${srcName(doc, s.seg.src)}, ${fmtTime(s.dur)}`}>
              <span>{s.n}</span>
            </button>
          );
        })}
      </nav>
      <section aria-labelledby="c-scenes" className="section" style={{ gap: 0 }}>
        <div className="section-head" style={{ paddingBottom: 12 }}>
          <h2 id="c-scenes">Scenes</h2>
          <span className="n">{segs.length}</span>
          {dirty && (
            <span className="end row">
              <button type="button" className="btn btn-sm" onClick={() => setDrafts({})} disabled={busy}>Discard</button>
              <button type="button" className="btn btn-sm btn-primary" onClick={save} disabled={busy}>Save cut points</button>
            </span>
          )}
        </div>
        {segs.map((s) => {
          const k = `${s.pi}:${s.si}`;
          const cur = drafts[k] || { start: s.seg.start, end: s.seg.end };
          const film = filmOf(s.seg.src);
          return (
            <SceneRow key={k} s={s} cur={cur} moved={!sameRange(cur, original[k] || cur)} film={film}
              name={film?.title || srcName(doc, s.seg.src)} stills={byScene[s.n] || []} metrics={reel.metrics}
              weights={reel.prefs.weights} poster={poster} names={names}
              onNudge={(edge, delta) => setDraft(s, nudge(cur, edge, delta, doc.sources[s.seg.src]?.duration, 0.5))}
              onReset={() => original[k] && setDraft(s, original[k])} />
          );
        })}
      </section>
    </>
  );
}

export function CutDetail() {
  const { id } = useParams();
  const tl = useApi(`/timelines/${enc(id)}`);
  usePageTitle(tl.data?.title);
  const variants = useApi(`/timelines/${enc(id)}/variants`);
  const sync = useApi(`/timelines/${enc(id)}/sync`);
  const hasMovies = Object.values(tl.data?.doc?.sources || {}).some((s) => s.kind === 'movie');
  const movies = useLoad(() => (hasMovies ? api('/library/movies') : Promise.resolve({ movies: [] })), [hasMovies]);
  const [variant, setVariant] = useUrlState('variant');
  const crumb = useCrumbRoot();
  const films = useApi(hasMovies ? '/scenes/films' : null, { quiet: true });
  const reel = useReel(id, tl.data?.doc);

  if (tl.error && !tl.data) return <div className="page"><ErrorBox error={tl.error} onRetry={tl.reload} /></div>;
  if (!tl.data) return <div className="page"><Loading /></div>;

  const doc = tl.data.doc;
  const credits = doc.credits || {};
  const url = repoUrl(credits);
  const report = sync.data?.synced ? sync.data : null;
  const rows = variants.data?.variants || [];
  const chosen = rows.find((v) => v.id === variant) || rows[0];
  const runtime = chosen ? chosen.runtime : doc.parts.reduce((a, p) => a + p.segments.reduce((b, s) => b + s.end - s.start, 0), 0);
  const kindLabel = doc.category || (doc.parts.length > 1 ? `${doc.parts.length}-part cut` : 'Cut');
  const subtitle = doc.tagline || doc.series?.title || '';
  const meta = [kindLabel, hm(runtime / 60), repoShort(url), credits.version ? `v${credits.version.replace(/^v/, '')}` : null].filter(Boolean).join(' · ');
  const description = credits.description || credits.summary || credits.about;
  const movieKeys = {};
  for (const m of movies.data?.movies || []) {
    if (m.tmdb_id == null) continue;
    movieKeys[`${m.tmdb_id}|${m.edition || ''}`] ??= m.key;
    movieKeys[`${m.tmdb_id}|`] ??= m.key;
  }
  // the films' own posters, in recipe order, for the hero (no text drawn on the art)
  const artByKey = new Map((movies.data?.movies || []).map((m) => [m.key, m.art]));
  const heroArt = [...new Set(Object.values(doc.sources).filter((s) => s.kind === 'movie')
    .map((s) => artByKey.get(movieKeys[`${s.tmdb}|${s.edition || ''}`] || movieKeys[`${s.tmdb}|`]))
    .filter(Boolean))];
  const buildTo = `/cuts/${enc(id)}/build${variant ? `?variant=${enc(variant)}` : ''}`;
  const kv = Object.entries(credits).filter(([k]) => !NOT_KV.has(k)).map(([k, v]) => [
    CREDIT_LABELS[k] || k.replace(/_/g, ' ').replace(/^./, (c) => c.toUpperCase()),
    k === 'repo' || k === 'repository' || k === 'url' ? <span className="mono tiny">{v}</span> : v,
  ]);

  return (
    <div className="page">
      <Crumbs items={[crumb.root, [doc.title]]} />
      <div className="hero">
        <div className="stack" style={{ width: 240, flex: 'none', gap: 10 }}>
          {reel.used.length > 0
            ? <PosterCanvas srcs={reel.used.map((x) => x.src)} layout={reel.prefs.layout} title={reel.prefs.title ? doc.title : ''} sub={subtitle} />
            : <ArtPoster keys={heroArt} layout={heroArt.length > 1 ? 'split' : 'single'}
              fallback={<Poster title={doc.title} sub={subtitle} kind={posterKind(doc)} bare />} />}
          <Link className="btn" to={`/cuts/${enc(id)}/poster`} state={crumb.state}>Poster</Link>
        </div>
        <div className="hero-body" style={{ gap: 18 }}>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
            <h1 className="display" style={{ font: '700 40px/1 var(--display)' }}>{doc.title}</h1>
            {subtitle && <span style={{ fontSize: 16, color: 'var(--text2)' }}>{subtitle}</span>}
            <span className="mono muted" style={{ fontSize: 13 }}>{meta}</span>
          </div>
          {description && <p style={{ margin: 0, color: 'var(--text2)', maxWidth: 620 }}>{description}</p>}
          <div className="row wrap">
            <Link className="btn btn-primary" to={buildTo} state={crumb.state}>Build this cut</Link>
            <Link className="btn" to={`/cuts/${enc(id)}/edit`} state={crumb.state}>Open in editor</Link>
            {url && <a className="btn" href={url} target="_blank" rel="noreferrer">View on GitHub</a>}
          </div>
          <Variants view={variants.data} value={chosen?.id ?? ''} onChange={setVariant} />
          <Estimate id={id} variant={chosen?.id || ''} />
          {variants.error && <ErrorBox error={variants.error} onRetry={variants.reload} />}
        </div>
      </div>
      <Reel key={id} id={id} doc={doc} reel={reel} films={films.data?.films} onSaved={tl.reload} />
      <div style={{ display: 'flex', flexWrap: 'wrap', gap: 24, alignItems: 'flex-start' }}>
        <SourceList doc={doc} missing={tl.data.missing || []} report={report} movieKeys={movieKeys} seriesKey={tl.data.series_key} />
        <Segments doc={doc} values={chosen?.toggles} report={report} />
      </div>
      <p className="tiny muted" style={{ margin: 0, maxWidth: 720 }}>
        Joining is automatic: every cut point is lined up with your own copies, the audio track in your preferred
        language is picked, each source’s text subtitles are re-timed to the cut, and chapters mark where each
        source starts. Sources that share codecs are copied without re-encoding.
      </p>
      <section aria-labelledby="c-about" className="section" style={{ gap: 12 }}>
        <h2 id="c-about">About this cut</h2>
        <KV rows={[
          ...kv,
          ['Cut points', report ? `Matched to your files ${report.updated_at ? `on ${report.updated_at.replace('T', ' ')}` : ''}` : 'Not matched to your files yet'],
          ['Contains', 'Timestamps, ordering and fingerprints. No video, audio, subtitles or artwork.'],
        ]} />
      </section>
    </div>
  );
}
