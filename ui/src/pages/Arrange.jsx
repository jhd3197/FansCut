// Editor step 2, Arrange: the running order (drag to reorder), ±1s trims, the join check
// from the films' speech lines, the Scenes/Speech/Music lanes and the cut preview.
// Saves the recipe through PUT /api/timelines/:id.
import { useEffect, useMemo, useRef, useState } from 'react';
import { Link, useParams } from 'react-router';
import { api } from '../lib/api.js';
import { useAct, useLoad, usePageTitle } from '../lib/hooks.js';
import { fmtTime } from '../lib/time.js';
import { flatSegments, nudge, rulerTicks } from '../lib/timeline.js';
import { joinCheck, linesIn, moveAcrossParts } from '../lib/joins.js';
import { useApi } from '../lib/store.js';
import { enc } from '../components/kit.jsx';
import { ErrorBox, Loading } from '../components/ui.jsx';
import { EditorSteps, filmColours, JoinStatus, Label, PreviewBadge } from '../components/v2.jsx';
import { CutPlayer } from '../components/CutPlayer.jsx';
import { sourceFrameUrl } from '../components/ScenePlayer.jsx';
import { filmsBySource } from './CutScenes.jsx';
import logoWhite from '../brand/logo-white.svg';
import './Arrange.css';

const PRE_ROLL = 3; // "Play this join" starts this long before it
const PICK_HOLD = 1500; // after a pick, the player's report of a neighbour is the seek landing

// Remember which scene was picked, and when (event handlers only).
const markPick = (ref, n) => { ref.current = { n, at: Date.now() }; };
const heldPick = (ref, k) => Date.now() - ref.current.at < PICK_HOLD && k !== ref.current.n;

// Titles without the prefix every film of the cut shares ("Harry Potter and the ").
export function shortNames(titles) {
  const list = [...new Set(titles.filter(Boolean))];
  if (list.length < 2) return (t) => t;
  let pre = list[0];
  for (const t of list) while (pre && !t.startsWith(pre)) pre = pre.slice(0, -1);
  pre = pre.slice(0, pre.lastIndexOf(' ') + 1);
  if (pre.length < 6) return (t) => t;
  return (t) => (t && t.startsWith(pre) && t.length > pre.length ? t.slice(pre.length) : t);
}

const Grip = () => (
  <svg className="grip" width="8" height="14" viewBox="0 0 10 16" fill="currentColor" aria-hidden="true">
    <circle cx="2" cy="3" r="1.5" /><circle cx="8" cy="3" r="1.5" /><circle cx="2" cy="8" r="1.5" />
    <circle cx="8" cy="8" r="1.5" /><circle cx="2" cy="13" r="1.5" /><circle cx="8" cy="13" r="1.5" />
  </svg>
);

// The saved segment a local one came from: same film, closest range.
function savedTwin(saved, seg) {
  let best = null;
  let dist = Infinity;
  for (const p of saved?.parts || []) {
    for (const s of p.segments || []) {
      if (s.src !== seg.src) continue;
      const d = Math.abs(s.start - seg.start) + Math.abs(s.end - seg.end);
      if (d < dist) { best = s; dist = d; }
    }
  }
  return best;
}

export function Arrange() {
  const { id } = useParams();
  const tl = useApi(`/timelines/${enc(id)}`);
  const hasMovies = Object.values(tl.data?.doc?.sources || {}).some((s) => s.kind === 'movie');
  const films = useApi(hasMovies ? '/scenes/films' : null, { quiet: true });
  const [doc, setDoc] = useState(null);
  const [dirty, setDirty] = useState(false);
  const [sel, setSel] = useState(1);
  const [drag, setDrag] = useState(null); // flat index being dragged
  const [over, setOver] = useState(null);
  const { act, busy } = useAct();
  const player = useRef(null);
  const rowsRef = useRef(null);
  const pickRef = useRef({ n: null, at: 0 });
  usePageTitle(tl.data?.title ? `Arrange ${tl.data.title}` : 'Arrange');

  if (tl.data && !doc) setDoc(structuredClone(tl.data.doc));

  const { segs, total } = useMemo(() => flatSegments(doc), [doc]);
  const sources = doc?.sources;
  const filmOf = useMemo(() => filmsBySource({ sources }, films.data?.films), [sources, films.data]);
  const srcOrder = useMemo(() => Object.keys(sources || {}), [sources]);
  const colourOf = useMemo(() => filmColours(srcOrder), [srcOrder]);

  // Speech lines per film (subtitle speech only). null = no speech data for that film.
  const keys = [...new Set(Object.values(filmOf).filter(Boolean).map((f) => f.key))].sort();
  const speech = useLoad(async () => {
    const out = {};
    await Promise.all(keys.map(async (k) => {
      try {
        const r = await api(`/scenes/transcript?key=${enc(k)}&speech=true`);
        out[k] = r.lines?.length ? r.lines : null;
      } catch {
        out[k] = null; // not indexed
      }
    }));
    return out;
  }, [keys.join('|')]);

  useEffect(() => {
    if (!dirty) return undefined;
    const warn = (e) => { e.preventDefault(); e.returnValue = ''; };
    window.addEventListener('beforeunload', warn);
    return () => window.removeEventListener('beforeunload', warn);
  }, [dirty]);

  if (tl.error && !tl.data) return <div className="page"><ErrorBox error={tl.error} onRetry={tl.reload} /></div>;
  if (!doc) return <div className="page"><Loading /></div>;

  const shorten = shortNames(srcOrder.map((s) => filmOf[s]?.title || sources[s]?.note));
  const filmName = (src) => shorten(filmOf[src]?.title || sources[src]?.note || src);
  const linesOf = (src) => {
    const f = filmOf[src];
    return f && speech.data ? speech.data[f.key] ?? null : null;
  };
  const joins = segs.map((s, i) => (i + 1 < segs.length
    ? { ...joinCheck(s.seg, segs[i + 1].seg, linesOf(s.seg.src), linesOf(segs[i + 1].seg.src)), at: segs[i + 1].at }
    : null));
  const cur = segs[Math.min(Math.max(sel, 1), segs.length) - 1] || null;
  const J = cur ? joins[cur.n - 1] : null;

  const edit = (fn) => {
    setDoc((d) => { const next = structuredClone(d); fn(next); return next; });
    setDirty(true);
  };
  const pick = (n, seek = true) => {
    markPick(pickRef, n);
    setSel(n);
    if (seek) player.current?.seekSegment(n, false);
  };
  const onSegment = (k) => {
    if (heldPick(pickRef, k)) return;
    setSel(k);
  };
  const trim = (s, edge, delta) => edit((d) => {
    const seg = d.parts[s.pi].segments[s.si];
    const r = nudge(seg, edge, delta, d.sources?.[seg.src]?.duration, 1);
    seg.start = r.start;
    seg.end = r.end;
  });
  const move = (from, to) => {
    if (from === to || to < 0 || to >= segs.length) return;
    edit((d) => {
      const rows = flatSegments(d).segs.map((s) => ({ pi: s.pi, seg: s.seg }));
      const parts = moveAcrossParts(rows, from, to, d.parts.length);
      if (parts) d.parts.forEach((p, i) => { p.segments = parts[i]; });
    });
    pick(to + 1, false);
  };
  const playJoin = (i) => {
    const j = joins[i];
    if (!j) return;
    markPick(pickRef, i + 1);
    setSel(i + 1);
    player.current?.seekTime(j.at - PRE_ROLL, true);
  };
  const giveAir = (i) => {
    const j = joins[i];
    if (!j?.fix) return;
    const s = j.fix.which === 'a' ? segs[i] : segs[i + 1];
    trim(s, j.fix.edge, j.fix.delta);
  };
  const save = () => act(async () => {
    const clean = { ...doc, parts: doc.parts.filter((p) => p.segments.length) };
    await api(`/timelines/${enc(id)}`, { method: 'PUT', body: clean });
    setDoc(structuredClone(clean));
    setDirty(false);
    tl.reload();
  }, 'Saved');

  const twin = cur ? savedTwin(tl.data?.doc, cur.seg) : null;
  const trimmed = Boolean(cur && twin && (twin.start !== cur.seg.start || twin.end !== cur.seg.end));
  const byFilm = [];
  for (const s of segs) {
    const f = byFilm.find((x) => x.src === s.seg.src);
    if (f) f.len += s.dur; else byFilm.push({ src: s.seg.src, len: s.dur });
  }
  const unchecked = [...new Set(segs.map((s) => s.seg.src).filter((src) => !linesOf(src)))];
  const toAnalyse = unchecked.map((src) => filmOf[src]).filter(Boolean);
  const pct = (t) => (total ? (t / total) * 100 : 0);
  const seekLanes = (e) => {
    if (e.target.closest('button')) return;
    const r = e.currentTarget.getBoundingClientRect();
    const t = Math.min(Math.max((e.clientX - r.left) / r.width, 0), 1) * total;
    const s = segs.findLast((x) => x.at <= t) || segs[0];
    if (!s) return;
    markPick(pickRef, s.n);
    setSel(s.n);
    player.current?.seekTime(t, false);
  };

  return (
    <div className="arr">
      <header className="arr-head">
        <Link to="/" aria-label="FansCut home"><img src={logoWhite} alt="FansCut" height="22" /></Link>
        <span className="arr-sep" />
        <span className="arr-kind">Cut</span>
        <Link className="hd arr-title" to={`/cuts/${enc(id)}`}>{doc.title}</Link>
        <span className="arr-fill" />
        <EditorSteps cut={id} step="arrange" />
        <span className="arr-fill" />
        {dirty && <span className="tiny arr-unsaved">Unsaved</span>}
        <button type="button" className="btn" onClick={save} disabled={!dirty || busy}>Save</button>
        {dirty
          ? <button type="button" className="btn btn-primary" disabled title="Save first">Build</button>
          : <Link className="btn btn-primary" to={`/cuts/${enc(id)}/build`}>Build</Link>}
      </header>

      {segs.length === 0 ? (
        <div className="page"><p className="muted">No scenes yet. <Link to={`/cuts/${enc(id)}/find`}>Find scenes</Link></p></div>
      ) : (
        <>
          <div className="arr-top">
            <section className="arr-preview" aria-label="Cut preview">
              <div className="arr-stage">
                <CutPlayer ref={player} id={id} segs={segs} filmOf={filmOf} srcOrder={srcOrder} srcName={filmName}
                  start={cur?.n || 1} onSegment={onSegment} />
                <span className="arr-badge"><PreviewBadge /></span>
              </div>
              <div className="arr-sum" aria-label="How the cut adds up">
                <div>
                  <Label>The cut adds up to</Label>
                  <div className="row" style={{ gap: 10, alignItems: 'baseline' }}>
                    <span className="mono arr-total">{fmtTime(total)}</span>
                    <span className="mono tiny muted">{segs.length} scenes, {byFilm.length} {byFilm.length === 1 ? 'film' : 'films'}</span>
                  </div>
                </div>
                <div className="arr-films">
                  {byFilm.map((f) => (
                    <div key={f.src} style={{ flex: `${f.len.toFixed(1)} 1 0`, '--film': colourOf[f.src] }}>
                      <i />
                      <span title={filmName(f.src)}>{filmName(f.src)}</span>
                      <span className="mono">{fmtTime(f.len)}</span>
                    </div>
                  ))}
                </div>
              </div>
            </section>

            <aside className="arr-order" aria-label="Running order">
              <div className="arr-order-head"><Label as="span">Running order</Label><span className="tiny">drag to reorder</span></div>
              <div className="arr-rows" ref={rowsRef}>
                {segs.map((s, i) => {
                  const j = joins[i];
                  return (
                    <div key={`${s.pi}:${s.si}`}>
                      <div className={`order-row arr-row${s.n === cur?.n ? ' is-on' : ''}${over === i && drag != null && drag !== i ? ' is-over' : ''}`}
                        draggable onDragStart={(e) => { setDrag(i); e.dataTransfer.effectAllowed = 'move'; }}
                        onDragOver={(e) => { e.preventDefault(); setOver(i); }}
                        onDragEnd={() => { setDrag(null); setOver(null); }}
                        onDrop={(e) => { e.preventDefault(); if (drag != null) move(drag, i); setDrag(null); setOver(null); }}>
                        <Grip />
                        <button type="button" className="arr-pick" aria-current={s.n === cur?.n ? 'true' : undefined}
                          title="Alt+Up / Alt+Down moves it"
                          onClick={() => pick(s.n)}
                          onKeyDown={(e) => {
                            if (!e.altKey || (e.key !== 'ArrowUp' && e.key !== 'ArrowDown')) return;
                            e.preventDefault();
                            const to = i + (e.key === 'ArrowUp' ? -1 : 1);
                            move(i, to);
                            setTimeout(() => rowsRef.current?.querySelectorAll('.arr-pick')[to]?.focus(), 0);
                          }}>
                          <span className="n">{s.n}</span>
                          <img src={sourceFrameUrl(id, s.seg.src, s.seg.start + Math.min(1, s.dur / 2))} alt="" loading="lazy" />
                          <span className="arr-name">
                            <span style={{ '--film': colourOf[s.seg.src] }}>{filmName(s.seg.src)}</span>
                            <span className="mono">{fmtTime(s.seg.start)} in the film</span>
                          </span>
                          <span className="mono arr-len">{fmtTime(s.dur)}</span>
                        </button>
                      </div>
                      {j && (
                        <button type="button" className={`order-join arr-join${s.n === cur?.n ? ' is-on' : ''}`}
                          aria-label={`Join ${s.n} to ${s.n + 1}: ${j.state === 'check' ? j.short : j.state === 'clean' ? 'clean' : 'not checked'}`}
                          onClick={() => { pick(s.n, false); player.current?.seekTime(j.at - PRE_ROLL, false); }}>
                          <JoinStatus state={j.state}>{j.short}</JoinStatus>
                          <i />
                        </button>
                      )}
                    </div>
                  );
                })}
              </div>
              <div className="arr-legend">
                <div className="row wrap" style={{ gap: 12 }}>
                  <JoinStatus state="check">Check the join</JoinStatus>
                  <JoinStatus state="clean" />
                  <JoinStatus state="unchecked" />
                </div>
                {toAnalyse.length > 0 && (
                  <Link to={`/movies/${enc(toAnalyse[0].key)}`} className="tiny">
                    Analyse {toAnalyse.length} more {toAnalyse.length === 1 ? 'film' : 'films'} to check every join
                  </Link>
                )}
              </div>
            </aside>
          </div>

          {cur && (
            <section className="arr-trim" aria-label="Selected scene: trim and join">
              <b>Scene {cur.n}</b>
              <div className="row" style={{ gap: 6 }}>
                <Label as="span">Starts</Label><span className="mono">{fmtTime(cur.seg.start)}</span>
                <button type="button" className="nudge" aria-label="Start one second earlier" onClick={() => trim(cur, 'start', -1)}>-1s</button>
                <button type="button" className="nudge" aria-label="Start one second later" onClick={() => trim(cur, 'start', 1)}>+1s</button>
              </div>
              <div className="row" style={{ gap: 6 }}>
                <Label as="span">Ends</Label><span className="mono">{fmtTime(cur.seg.end)}</span>
                <button type="button" className="nudge" aria-label="End one second earlier" onClick={() => trim(cur, 'end', -1)}>-1s</button>
                <button type="button" className="nudge" aria-label="End one second later" onClick={() => trim(cur, 'end', 1)}>+1s</button>
              </div>
              <span className="mono muted">{fmtTime(cur.dur)} long</span>
              {trimmed && (
                <button type="button" className="btn btn-sm" onClick={() => edit((d) => {
                  const seg = d.parts[cur.pi].segments[cur.si];
                  seg.start = twin.start;
                  seg.end = twin.end;
                })}>Reset</button>
              )}
              <span className="arr-fill" />
              {J ? (
                <>
                  <span className={`arr-jtext${J.state === 'check' ? ' is-check' : ''}`}>
                    <JoinStatus state={J.state}><b>Join to scene {cur.n + 1}.</b> {J.text}</JoinStatus>
                  </span>
                  {J.state === 'check' && <button type="button" className="btn btn-sm" onClick={() => giveAir(cur.n - 1)}>Give it air</button>}
                  {J.state === 'unchecked' && <button type="button" className="btn btn-sm" disabled title="Not yet: this film has no speech data">Give it air</button>}
                  <button type="button" className="btn btn-sm" onClick={() => playJoin(cur.n - 1)}>Play this join</button>
                </>
              ) : <span className="muted">Last scene. Nothing comes after it.</span>}
            </section>
          )}

          <section className="arr-lanes" aria-label="Cut timeline">
            <div className="arr-lane-names">
              <span />
              <Label className="lab arr-ln-scenes">Scenes</Label>
              <Label className="lab arr-ln">Speech</Label>
              <Label className="lab arr-ln">Music</Label>
            </div>
            <div className="arr-lane-body" onClick={seekLanes}>
              <div className="arr-ruler">
                {rulerTicks(total, 6).map((t) => <span key={t} className="mono" style={{ left: `${pct(t)}%` }}>{fmtTime(t)}</span>)}
              </div>
              <div className="arr-tabs">
                {segs.map((s) => (
                  <button key={s.n} type="button" aria-pressed={s.n === cur?.n} onClick={() => pick(s.n)}
                    style={{ left: `${pct(s.at)}%`, width: `calc(${pct(s.dur)}% - 2px)`, '--film': colourOf[s.seg.src] }}>
                    {filmName(s.seg.src)}
                  </button>
                ))}
              </div>
              <div className="arr-blocks">
                {segs.map((s) => (
                  <button key={s.n} type="button" aria-label={`Scene ${s.n}, ${filmName(s.seg.src)}`} onClick={() => pick(s.n)}
                    className={s.n === cur?.n ? 'is-on' : undefined}
                    style={{ left: `${pct(s.at)}%`, width: `calc(${pct(s.dur)}% - 2px)`,
                      backgroundImage: `url("${sourceFrameUrl(id, s.seg.src, s.seg.start + Math.min(1, s.dur / 2))}")` }}>
                    <span className="mono">{s.n}</span>
                  </button>
                ))}
              </div>
              <div className="arr-seams">
                {joins.map((j, i) => j && (
                  <button key={i} type="button" className={i + 1 === cur?.n ? 'is-on' : undefined}
                    aria-label={`Join ${i + 1} to ${i + 2}: ${j.state === 'check' ? 'check' : j.state === 'clean' ? 'clean' : 'not checked'}`}
                    title={j.text}
                    style={{ left: `${pct(j.at)}%` }}
                    onClick={() => { pick(i + 1, false); player.current?.seekTime(j.at - PRE_ROLL, false); }}>
                    <JoinStatus state={j.state}>{''}</JoinStatus>
                  </button>
                ))}
              </div>
              <div className="arr-speech">
                {segs.map((s) => {
                  const lines = linesOf(s.seg.src);
                  if (!lines) {
                    return <span key={s.n} className="arr-nodata" title="Film not analysed"
                      style={{ left: `${pct(s.at)}%`, width: `calc(${pct(s.dur)}% - 2px)` }} />;
                  }
                  return linesIn(lines, s.seg.start, s.seg.end).map((l, k) => {
                    const a = Math.max(l.start, s.seg.start);
                    const b = Math.min(l.end, s.seg.end);
                    return <i key={`${s.n}-${k}`} style={{ left: `${pct(s.at + a - s.seg.start)}%`, width: `max(${pct(b - a)}%, 1px)` }} />;
                  });
                })}
              </div>
              <div className="arr-music">
                <button type="button" disabled title="Not yet">Music bed</button>
              </div>
            </div>
          </section>
        </>
      )}
    </div>
  );
}
