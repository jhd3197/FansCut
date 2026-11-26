// The cut page (v2 "CutPage" board): the poster and who made it, the preview played from
// the films, the build band (estimate, build progress from the jobs API, the result), and
// what is in the build, grouped by film. Editing lives in Find scenes and Arrange.
// The old /cuts/:id/poster page is the poster picker here (?poster=1); /cuts/:id/build
// stays for checking weak cut points.
import { useEffect, useMemo, useRef, useState } from 'react';
import { Link, Navigate, useLocation, useParams } from 'react-router';
import { api, openInPlex } from '../lib/api.js';
import { useAct, useCrumbRoot, usePageTitle, usePoll, useUrlState } from '../lib/hooks.js';
import { fmtTime } from '../lib/time.js';
import { flatSegments } from '../lib/timeline.js';
import { drawPoster, loadImage, stillTimes } from '../lib/stills.js';
import { useApi } from '../lib/store.js';
import { Crumbs, enc } from '../components/kit.jsx';
import { Dialog, ErrorBox, Loading } from '../components/ui.jsx';
import { PreviewBadge, filmColours } from '../components/v2.jsx';
import { CutPlayer } from '../components/CutPlayer.jsx';
import { sourceFrameUrl } from '../components/ScenePlayer.jsx';
import { filmsBySource } from './CutScenes.jsx';
import './CutDetail.css';

// "github.com/owner/repo" or a full URL -> a link; anything else -> null.
export function repoUrl(credits = {}) {
  const raw = credits.repo || credits.repository || credits.url || '';
  if (/^https?:\/\//.test(raw)) return raw;
  if (/^github\.com\//.test(raw)) return `https://${raw}`;
  if (/^[\w.-]+\/[\w.-]+$/.test(raw)) return `https://github.com/${raw}`;
  return null;
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

export const frameUrl = (id, src, t) => `/api/timelines/${enc(id)}/sources/${enc(src)}/frame?t=${t}`;
const takeUrl = (id, name) => `/api/timelines/${enc(id)}/poster/ai/${encodeURIComponent(name)}`;
const chosenUrl = (id, v) => `/api/timelines/${enc(id)}/poster?v=${encodeURIComponent(v)}`;

// "1:28:52": hours only when there are some.
const hms = (sec) => fmtTime(sec);

// Weak cut points in a sync report (same rules as the build page's review).
export function weakCount(report) {
  let n = 0;
  for (const s of Object.values(report?.sources || {})) {
    for (const b of s.boundaries || []) {
      if (b.anchored && (b.method === 'none' || b.outlier || b.low_confidence)) n += 1;
    }
  }
  return n;
}

const ACTIVE = ['queued', 'running'];

// The newest sync and render jobs of this cut, polled while one runs.
function useCutJobs(id) {
  const jobs = useApi('/jobs');
  const mine = (jobs.data?.jobs || []).filter((j) => j.payload?.timeline_id === id);
  const render = mine.find((j) => j.kind === 'timeline_render') || null;
  const sync = mine.find((j) => j.kind === 'timeline_sync') || null;
  const active = [render, sync].some((j) => j && ACTIVE.includes(j.status));
  usePoll(jobs.reload, 1000, active);
  return { render, sync, active, reload: jobs.reload };
}

// The cut's AI poster takes and its chosen poster, polled while a take is being drawn.
export function useAiPoster(id) {
  const st = useApi(`/timelines/${enc(id)}/poster/ai`, { quiet: true });
  usePoll(st.reload, 2000, st.data?.task?.status === 'running');
  return st;
}

// -- posters drawn here: a frame with the title, or the title only ------------------

const cssVar = (name, fallback) => (typeof document === 'undefined' ? fallback
  : getComputedStyle(document.documentElement).getPropertyValue(name).trim() || fallback);

function wrap(c, text, width) {
  const lines = [];
  let ln = '';
  for (const word of String(text).split(/\s+/)) {
    const t = ln ? `${ln} ${word}` : word;
    if (c.measureText(t).width > width && ln) { lines.push(ln); ln = word; } else ln = t;
  }
  lines.push(ln);
  return lines;
}

// A 2:3 poster with no picture: a small label, the title, the franchise and length.
export async function drawTitlePoster(canvas, { title, sub }) {
  const c = canvas?.getContext('2d');
  if (!c) return;
  if (document.fonts?.load) {
    await Promise.all(['700 40px Outfit', '700 20px "DM Sans"', '400 20px "DM Mono"'].map((f) => document.fonts.load(f).catch(() => null)));
  }
  const W = canvas.width; const H = canvas.height; const k = W / 600; const pad = W * 0.12;
  c.fillStyle = cssVar('--raised', '#142D38');
  c.fillRect(0, 0, W, H);
  c.textBaseline = 'top';
  c.fillStyle = cssVar('--apricot', '#FFBE98');
  c.font = `700 ${22 * k}px "DM Sans", sans-serif`;
  c.fillText('A FANSCUT', pad, pad);
  c.fillStyle = cssVar('--porcelain', '#F5FAFC');
  const size = 76 * k;
  c.font = `700 ${size}px Outfit, "DM Sans", sans-serif`;
  const lines = wrap(c, title, W - 2 * pad);
  const top = (H - lines.length * size * 1.02) / 2;
  lines.forEach((l, i) => c.fillText(l, pad, top + i * size * 1.02));
  if (sub) {
    c.textBaseline = 'bottom';
    c.fillStyle = cssVar('--soft', '#DBE7EC');
    c.font = `400 ${24 * k}px "DM Mono", monospace`;
    c.fillText(sub, pad, H - pad);
  }
}

function TitleCanvas({ title, sub, label }) {
  const ref = useRef(null);
  useEffect(() => { drawTitlePoster(ref.current, { title, sub }); }, [title, sub]);
  return <canvas ref={ref} className="cutp-poster" width={600} height={900} role="img" aria-label={label} />;
}

export function PosterCanvas({ srcs, layout = 'frame', title, sub, label = 'Poster' }) {
  const ref = useRef(null);
  const seq = useRef(0);
  const key = srcs.join('|');
  useEffect(() => {
    const mine = ++seq.current;
    drawPoster(ref.current, key ? key.split('|') : [], { layout, title, sub, stale: () => mine !== seq.current });
  }, [key, layout, title, sub]);
  return <canvas ref={ref} className="cutp-poster" width={600} height={900} role="img" aria-label={label} />;
}

// The picked frame or title poster at full size, as a JPEG data URL for the server.
async function posterImage(pick, { still, title, sub }) {
  const cv = document.createElement('canvas');
  cv.width = 1000;
  cv.height = 1500;
  if (pick.kind === 'frame') {
    if (!(await loadImage(still))) throw new Error('could not load that frame');
    await drawPoster(cv, [still], { layout: 'frame', title, sub });
  } else await drawTitlePoster(cv, { title, sub });
  return cv.toDataURL('image/jpeg', 0.9);
}

// -- the poster picker ---------------------------------------------------------------

function PosterPicker({ id, title, sub, stills, ai, onClose }) {
  const data = ai.data || { takes: [], chosen: null };
  const chosen = data.chosen || '';
  const task = data.task;
  const running = task?.status === 'running';
  const [pick, setPick] = useState(() => {
    if (chosen.startsWith('frame-')) return { kind: 'frame' };
    if (chosen.startsWith('title-')) return { kind: 'title' };
    if (chosen) return { kind: 'ai', name: chosen };
    return data.takes[0] ? { kind: 'ai', name: data.takes[0].name } : { kind: 'title' };
  });
  const [scene, setScene] = useState(0); // index into stills for the frame poster
  const [err, setErr] = useState('');
  const [busy, setBusy] = useState(false);
  const still = stills[Math.min(scene, Math.max(0, stills.length - 1))];

  // a take that just finished is the one picked
  const lastTake = task?.status === 'done' ? task.take : null;
  const seen = useRef(lastTake);
  useEffect(() => {
    if (lastTake && lastTake !== seen.current) { seen.current = lastTake; setPick({ kind: 'ai', name: lastTake }); }
  }, [lastTake]);

  const call = async (fn) => {
    setErr('');
    setBusy(true);
    try { await fn(); return true; } catch (e) { setErr(e.message || String(e)); return false; } finally { setBusy(false); ai.reload(); }
  };
  const askAnother = () => call(() => api(`/timelines/${enc(id)}/poster/ai`, {
    method: 'POST',
    // four stills spread over the cut; the server moves them off faces
    body: { stills: spread(stills, 4).map((s) => ({ src: s.source, t: s.t })), title: true },
  }));
  const discard = (name) => call(async () => {
    await api(`/timelines/${enc(id)}/poster/ai/${encodeURIComponent(name)}`, { method: 'DELETE' });
    if (pick.kind === 'ai' && pick.name === name) setPick({ kind: 'title' });
  });
  const use = async () => {
    const ok = await call(async () => {
      if (pick.kind === 'ai') {
        if (pick.name !== chosen) await api(`/timelines/${enc(id)}/poster/ai/${encodeURIComponent(pick.name)}/keep`, { method: 'POST' });
        return;
      }
      if (pick.kind === 'frame' && !still) throw new Error('this cut has no scenes');
      const image = await posterImage(pick, { still: still?.src, title, sub });
      await api(`/timelines/${enc(id)}/poster`, { method: 'PUT', body: { kind: pick.kind, image } });
    });
    if (ok) onClose();
  };
  const on = (p) => p.kind === pick.kind && (p.kind !== 'ai' || p.name === pick.name);

  return (
    <Dialog title="Choose a poster" onClose={onClose} wide>
      <div className="cutp-opts">
        {data.takes.map((t) => (
          <div key={t.name} className="cutp-opt-wrap">
            <button type="button" className="cutp-opt" aria-pressed={on({ kind: 'ai', name: t.name })} onClick={() => setPick({ kind: 'ai', name: t.name })}>
              <img className="cutp-poster" src={takeUrl(id, t.name)} alt="" loading="lazy" />
              <span className="cutp-opt-name">Painted by AI</span>
              <span className="cutp-opt-sub">{chosen === t.name ? 'On the cut now' : t.cost ? <span className="mono">${t.cost.toFixed(2)}</span> : 'Not used'}</span>
            </button>
            <button type="button" className="btn btn-sm btn-quiet" disabled={busy || running} onClick={() => discard(t.name)}>Discard</button>
          </div>
        ))}
        <div className="cutp-opt-wrap">
          <button type="button" className="cutp-opt" aria-pressed={on({ kind: 'frame' })} onClick={() => setPick({ kind: 'frame' })} disabled={!still}>
            {still
              ? <PosterCanvas srcs={[still.src]} title={title} sub={sub} label="Poster made from a frame of the cut" />
              : <span className="cutp-poster cutp-empty" />}
            <span className="cutp-opt-name">A frame from the cut</span>
            <span className="cutp-opt-sub">{still ? `Scene ${still.scene}${chosen.startsWith('frame-') ? ' · on the cut now' : ''}` : 'No scenes'}</span>
          </button>
        </div>
        <div className="cutp-opt-wrap">
          <button type="button" className="cutp-opt" aria-pressed={on({ kind: 'title' })} onClick={() => setPick({ kind: 'title' })}>
            <TitleCanvas title={title} sub={sub} label="Poster with the title only" />
            <span className="cutp-opt-name">Title only</span>
            <span className="cutp-opt-sub">{chosen.startsWith('title-') ? 'On the cut now' : 'No picture'}</span>
          </button>
        </div>
        <div className="cutp-opt-wrap cutp-ask">
          <div className="cutp-poster cutp-ask-box" aria-live="polite">
            {running
              ? <span>{task.message || 'Drawing'}…</span>
              : <><svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden="true"><path d="M12 5v14M5 12h14" /></svg><span>Another from AI</span></>}
          </div>
          <span className="cutp-opt-name">Ask for a new one</span>
          <span className="cutp-opt-sub">{data.model ? <span className="mono">{data.model}</span> : <>Needs an image model. <Link to="/settings?section=posters">Settings</Link></>}</span>
        </div>
      </div>

      {pick.kind === 'frame' && stills.length > 1 && (
        <div className="cutp-frames" role="radiogroup" aria-label="Frame from scene">
          {stills.map((s, i) => (
            <button key={s.src} type="button" role="radio" aria-checked={i === scene} aria-label={`Scene ${s.scene}`} onClick={() => setScene(i)}>
              <img src={s.src} alt="" loading="lazy" />
            </button>
          ))}
        </div>
      )}

      <div className="row wrap" style={{ gap: 8 }}>
        <input className="inp" type="text" style={{ flex: '1 1 280px', width: 'auto' }} aria-label="Describe the poster you want"
          placeholder="Describe the poster you want" disabled title="Not yet" />
        <button type="button" className="btn" disabled={!data.model || running || busy || !stills.length}
          title={data.model ? undefined : 'Set an image model under Settings, Posters'} onClick={askAnother}>Ask for another</button>
      </div>
      {task?.status === 'failed' && <span className="tiny c-danger" role="alert">{task.message}</span>}
      {err && <span className="tiny c-danger" role="alert">{err}</span>}

      <div className="cutp-dlg-foot">
        <button type="button" className="btn" onClick={onClose}>Cancel</button>
        <button type="button" className="btn btn-primary" disabled={busy || (pick.kind === 'ai' && !pick.name)} onClick={use}>Use this poster</button>
      </div>
    </Dialog>
  );
}

// n items spread evenly over a list.
function spread(list, n) {
  if (list.length <= n) return list;
  return Array.from({ length: n }, (_, i) => list[Math.floor(((i + 0.5) * list.length) / n)]);
}

// -- build band --------------------------------------------------------------------

function BuildBand({ id, tl, report, segs, total, jobs, plex, onSynced }) {
  const est = useApi(`/timelines/${enc(id)}/estimate`, { quiet: true });
  const { act, busy } = useAct();
  const [working, setWorking] = useState(false); // a build started here, until its job is queued
  const [err, setErr] = useState('');
  const doc = tl.doc;
  const required = (tl.missing || []).filter((m) => !doc.sources[m.src]?.optional);
  const weak = weakCount(report);
  const { render, sync, reload: reloadJobs } = jobs;
  const rendering = render && ACTIVE.includes(render.status);
  const syncing = sync && ACTIVE.includes(sync.status);
  const stamp = (s) => String(s || '').slice(0, 19);
  const built = render?.status === 'done' && stamp(render.updated_at) >= stamp(tl.updated_at);

  const alive = useRef(true);
  useEffect(() => { alive.current = true; return () => { alive.current = false; }; }, []);

  // Build: match the cut points first when they aren't yet (waiting for that job here),
  // then queue the render, unless a cut point needs a look.
  const build = async () => {
    setErr('');
    setWorking(true);
    try {
      if (!report) {
        let job = await api(`/timelines/${enc(id)}/sync`, { method: 'POST' });
        reloadJobs();
        while (ACTIVE.includes(job.status)) {
          await new Promise((r) => { setTimeout(r, 1000); });
          if (!alive.current) return;
          job = await api(`/jobs/${enc(job.id)}`);
        }
        if (job.status !== 'done') throw new Error(job.message || 'Matching the cut points failed');
        const rep = await api(`/timelines/${enc(id)}/sync`);
        onSynced();
        if (weakCount(rep)) return;
      }
      await api(`/timelines/${enc(id)}/render`, { method: 'POST', body: { variant: null, quality: 'best' } });
      reloadJobs();
    } catch (x) {
      if (alive.current) setErr(x.message || String(x));
    } finally {
      if (alive.current) setWorking(false);
    }
  };
  const cancel = (job) => act(async () => { await api(`/jobs/${enc(job.id)}/cancel`, { method: 'POST' }); reloadJobs(); });
  const e = est.data;
  const encode = e?.reasons?.some((r) => r.startsWith('re-encoded'));

  if (rendering || syncing) {
    const job = rendering ? render : sync;
    const pct = Math.round((job.progress || 0) * 100);
    const step = rendering
      ? [job.phase?.name, job.phase?.detail].filter(Boolean).join(' · ') || job.message || 'Waiting to start'
      : job.message || 'Waiting to start';
    return (
      <section className="build-band cutp-band" aria-label="Build">
        <div>
          <div className="row" style={{ gap: 10, alignItems: 'baseline', flexWrap: 'wrap' }}>
            <span className="hd cutp-band-hd">{rendering ? 'Building' : 'Matching cut points'}</span>
            <span className="mono cutp-pct">{pct}%</span>
            <span className="mono tiny cutp-dim">{step}</span>
          </div>
          <div className="cutp-progress" role="progressbar" aria-label={rendering ? 'Build progress' : 'Matching progress'}
            aria-valuemin={0} aria-valuemax={100} aria-valuenow={pct}>
            <span style={{ width: `${pct}%` }} />
          </div>
        </div>
        <div className="row" style={{ gap: 8 }}>
          <button type="button" className="btn" disabled={busy} onClick={() => cancel(job)}>Cancel</button>
          <Link className="btn" to="/jobs">See in Jobs</Link>
        </div>
      </section>
    );
  }

  if (built) {
    return (
      <section className="build-band cutp-band" aria-label="Build">
        <div className="row" style={{ gap: 12 }}>
          <span className="cutp-done" aria-hidden="true">
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="3"><path d="M5 12.5l4.5 4.5L19 7.5" /></svg>
          </span>
          <div>
            <div className="hd cutp-band-hd">{plex ? 'Built. It is in Plex.' : 'Built.'}</div>
            <div className="mono tiny cutp-dim">{fmtTime(total)} · {segs.length} scene{segs.length === 1 ? '' : 's'}</div>
          </div>
        </div>
        <div className="row" style={{ gap: 8 }}>
          {plex && <button type="button" className="btn btn-primary" onClick={() => act(() => openInPlex(`/cuts/${enc(id)}/plex`))}>Play in Plex</button>}
          <button type="button" className={`btn${plex ? '' : ' btn-primary'}`} disabled={busy || working} onClick={build}>Build again</button>
        </div>
      </section>
    );
  }

  const failed = render && render.status === 'failed' && stamp(render.updated_at) >= stamp(tl.updated_at) ? render.message : '';
  return (
    <section className="build-band cutp-band" aria-label="Build">
      <div>
        <div className="hd cutp-band-hd">This is a preview, not the finished file.</div>
        {encode && e && !e.gpu && <div className="cutp-dim cutp-band-sub">GPU encoding is off. <Link to="/settings?section=render">Settings</Link></div>}
        {required.length > 0 && <div className="cutp-band-sub c-danger">Missing {required.map((m) => m.note || doc.sources[m.src]?.note || m.src).join(', ')}</div>}
        {weak > 0 && <div className="cutp-band-sub"><span className="state state-look">{weak} cut point{weak === 1 ? '' : 's'} to check</span> <Link to={`/cuts/${enc(id)}/build`}>Check them</Link></div>}
        {(err || failed) && <div className="cutp-band-sub c-danger" role="alert">{err || `Last build failed: ${failed}`}</div>}
      </div>
      {e && <span className="mono tiny cutp-soft">{about(e.seconds)}{e.best?.bytes ? ` · ${gb(e.best.bytes)}` : ''}</span>}
      <div className="row" style={{ gap: 8 }}>
        <button type="button" className="btn btn-primary" disabled={busy || working || required.length > 0} onClick={build}
          title={required.length ? 'A film this cut needs is not in your library' : undefined}>Build this cut</button>
        <Link className="btn" to={`/cuts/${enc(id)}/arrange`}>Open editor</Link>
      </div>
    </section>
  );
}

// -- in this build: the scenes grouped by film ---------------------------------------

function FilmGroups({ id, doc, segs, filmOf, colour, current, onPick }) {
  const groups = [];
  for (const s of segs) {
    let g = groups.find((x) => x.src === s.seg.src);
    if (!g) { g = { src: s.seg.src, len: 0, items: [] }; groups.push(g); }
    g.len += s.dur;
    g.items.push(s);
  }
  return (
    <div className="cutp-groups">
      {groups.map((g) => {
        const film = filmOf[g.src];
        const s = doc.sources[g.src] || {};
        const name = film?.title || s.note || g.src;
        const sub = [film ? 'Owned' : 'Not in your library', film?.year, s.edition, (film?.duration || s.duration) ? `${hms(film?.duration || s.duration)} long` : null]
          .filter(Boolean).join(' · ');
        return (
          <div key={g.src} className="film-group" style={{ '--film': colour[g.src] }}>
            <div className="film-group-head">
              <span className="film-stripe" />
              <span>
                <span className="cutp-group-name">{name}</span>
                <span className="mono cutp-group-sub">{sub}</span>
              </span>
              <span className="mono tiny cutp-soft">{hms(g.len)}</span>
            </div>
            {g.items.map((x) => (
              <button key={x.n} type="button" className="film-group-row cutp-row" aria-current={x.n === current ? 'true' : undefined}
                aria-label={`Preview ${name} from ${hms(x.seg.start)}`} onClick={() => onPick(x.n)}>
                <img src={sourceFrameUrl(id, x.seg.src, (x.seg.start + x.seg.end) / 2)} alt="" loading="lazy" />
                <span className="mono tiny cutp-soft" style={{ flex: 1, minWidth: 0 }}>{hms(x.seg.start)} to {hms(x.seg.end)}</span>
                <span className="mono tiny cutp-dim">{hms(x.dur)}</span>
              </button>
            ))}
          </div>
        );
      })}
    </div>
  );
}

// -- the page ----------------------------------------------------------------------

export function CutDetail() {
  const { id } = useParams();
  const tl = useApi(`/timelines/${enc(id)}`);
  usePageTitle(tl.data?.title);
  const syncRep = useApi(`/timelines/${enc(id)}/sync`);
  const films = useApi('/scenes/films', { quiet: true });
  const settings = useApi('/settings', { quiet: true });
  const ai = useAiPoster(id);
  const jobs = useCutJobs(id);
  const [scene, setScene] = useUrlState('scene', '1');
  const [posterOpen, setPosterOpen] = useUrlState('poster');
  const player = useRef(null);
  const crumb = useCrumbRoot();
  const doc = tl.data?.doc;
  const segs = useMemo(() => flatSegments(doc).segs, [doc]);
  const filmOf = useMemo(() => filmsBySource(doc, films.data?.films), [doc, films.data]);
  const srcOrder = useMemo(() => [...new Set(segs.map((s) => s.seg.src))], [segs]);
  const colour = useMemo(() => filmColours(srcOrder), [srcOrder]);
  const stills = useMemo(() => segs.map((s) => {
    const [t] = stillTimes(s.seg.start, s.seg.end, 1);
    return { src: frameUrl(id, s.seg.src, t), t, scene: s.n, source: s.seg.src };
  }), [id, segs]);

  // a finished sync or build changes the report and the cut's files
  const lastSync = jobs.sync?.status;
  const { reload: reloadSync } = syncRep;
  useEffect(() => { if (lastSync === 'done') reloadSync(); }, [lastSync, reloadSync]);

  if (tl.error && !tl.data) return <div className="page"><ErrorBox error={tl.error} onRetry={tl.reload} /></div>;
  if (!doc) return <div className="page"><Loading /></div>;

  const report = syncRep.data?.synced ? syncRep.data : null;
  const total = segs.reduce((t, s) => t + s.dur, 0);
  const franchise = doc.series?.title || '';
  const nFilms = srcOrder.length;
  const credits = doc.credits || {};
  const url = repoUrl(credits);
  const name = (src) => filmOf[src]?.title || doc.sources[src]?.note || src;
  const n = Math.min(Math.max(Number(scene) || 1, 1), Math.max(segs.length, 1));
  const chosen = ai.data?.chosen;
  const posterSub = [franchise, total ? `${Math.max(1, Math.round(total / 60))} m` : ''].filter(Boolean).join(' · ');
  const plex = Boolean(settings.data?.library?.plex?.url);

  return (
    <div className="page cutp">
      <aside className="cutp-side" aria-label="Poster and details">
        {chosen
          ? <img className="cutp-poster" src={chosenUrl(id, chosen)} alt={`${doc.title} poster`} />
          : <TitleCanvas title={doc.title} sub={posterSub} label={`${doc.title} poster`} />}
        <button type="button" className="btn" onClick={() => setPosterOpen('1')}>
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" aria-hidden="true"><path d="M20 11a8 8 0 1 0-2.3 5.7M20 4v7h-7" /></svg>
          New poster
        </button>
        <dl className="cutp-dl">
          {credits.author && <div><dt className="lab">Made by</dt><dd>{credits.author}</dd></div>}
          {credits.based_on && <div><dt className="lab">Based on</dt><dd>{credits.based_on}</dd></div>}
          {url && <div><dt className="lab">Repository</dt><dd><a href={url} target="_blank" rel="noreferrer">{url.replace(/^https?:\/\/(www\.)?(github\.com\/)?/, '')}</a></dd></div>}
          <div>
            <dt className="lab">Cut points</dt>
            <dd className={report ? undefined : 'cutp-dim'}>{report ? 'Matched to your files' : 'Not matched to your files yet'}</dd>
          </div>
        </dl>
      </aside>

      <div className="cutp-main">
        <Crumbs items={[crumb.root]} />
        <div className="lab cutp-kicker">Cut</div>
        <h1 className="cutp-title">{doc.title}</h1>
        <div className="cutp-meta">
          {franchise && <span className="cutp-franchise">{franchise}</span>}
          <span className="mono tiny cutp-dim">
            {fmtTime(total)} · {segs.length} scene{segs.length === 1 ? '' : 's'}{nFilms ? ` from ${nFilms} film${nFilms === 1 ? '' : 's'}` : ''}
          </span>
        </div>

        {segs.length > 0 && (
          <section className="cutp-player" aria-label="Preview player">
            <CutPlayer ref={player} id={id} segs={segs} filmOf={filmOf} srcOrder={srcOrder} srcName={name}
              start={n} onSegment={(k) => setScene(String(k))} />
            <span className="cutp-badge"><PreviewBadge /></span>
          </section>
        )}

        <BuildBand id={id} tl={tl.data} report={report} segs={segs} total={total} jobs={jobs} plex={plex} onSynced={syncRep.reload} />

        <section className="cutp-build" aria-labelledby="cutp-in">
          <div className="cutp-build-head">
            <h2 id="cutp-in">In this build</h2>
            <span className="mono tiny cutp-dim">{segs.length} scene{segs.length === 1 ? '' : 's'} · {nFilms} film{nFilms === 1 ? '' : 's'} · {fmtTime(total)}</span>
            <Link className="cutp-find" to={`/cuts/${enc(id)}/find`}>Find scenes</Link>
          </div>
          {segs.length > 0
            ? <FilmGroups id={id} doc={doc} segs={segs} filmOf={filmOf} colour={colour} current={n}
              onPick={(k) => { setScene(String(k)); player.current?.seekSegment(k, false); }} />
            : <p className="cutp-dim" style={{ margin: 0 }}>No scenes yet. <Link to={`/cuts/${enc(id)}/find`}>Find scenes</Link></p>}
        </section>
      </div>

      {posterOpen && (
        <PosterPicker id={id} title={doc.title} sub={posterSub} stills={stills} ai={ai} onClose={() => setPosterOpen('')} />
      )}
    </div>
  );
}

// The old poster page: the picker on the cut page now.
export function CutPosterRedirect() {
  const { id } = useParams();
  const { state } = useLocation();
  return <Navigate replace to={`/cuts/${enc(id)}?poster=1`} state={state} />;
}
