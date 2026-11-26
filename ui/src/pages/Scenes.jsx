// Scenes and tags (Library tool): one film's scenes, each a named range with tags. The
// picture shows what FansCut found at the playhead; the scene's edges are checked against
// the dialogue and can snap to a clean spot; the whole film sits under it as a strip.
// A scene is a group of tag rows over the same range: accepted rows give its tags,
// proposed rows are the AI's suggestions (Keep accepts them, Drop rejects them).
// `?person=` follows one actor; `?from&to&cut&seg` edits one segment of a cut.
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Link, useNavigate, useParams, useSearchParams } from 'react-router';
import { api } from '../lib/api.js';
import { useApi } from '../lib/store.js';
import { useAct, useLoad, usePageTitle, usePoll } from '../lib/hooks.js';
import { fmtTime } from '../lib/time.js';
import { flatSegments } from '../lib/timeline.js';
import { ConfirmButton, Empty, ErrorBox, Loading, Progress } from '../components/ui.jsx';
import { Crumbs, enc } from '../components/kit.jsx';
import { AiTag, EditTag, FilterChip, IconButton, Label } from '../components/v2.jsx';
import {
  ClipVideo, clipUrl, clipWindows, namedFaces, q, sceneFrameUrl, segmentEditUrl, shownTags,
  tagLabel, useFacesAt, useTagNames, visibleRows,
} from '../components/ScenePlayer.jsx';
import './Scenes.css';

export { segmentEditUrl };
export const frameUrl = sceneFrameUrl;

// The camera cut before/after t.
export function prevCut(shots, t) {
  let best = 0;
  for (const [a] of shots) if (a < t - 0.05) best = a;
  return best;
}
export function nextCut(shots, t, duration) {
  for (const [, b] of shots) if (b > t + 0.05) return b;
  return duration;
}

const norm = (s) => String(s || '').trim().toLowerCase();
const r3 = (v) => Math.round(v * 1000) / 1000;
const uniq = (xs) => [...new Set(xs)];

export const isAI = (x) => String(x?.source || '').startsWith('ai:');

const rank = (x) => (x.status === 'accepted' ? 2 : x.status === 'proposed' ? 1 : 0) + (x.confidence ?? 0.5) / 10;
const sameTags = (a, b) => {
  const x = [...shownTags(a)].sort().join('|');
  return x === [...shownTags(b)].sort().join('|');
};
const overlap = (a, b) => {
  const inter = Math.min(a.end, b.end) - Math.max(a.start, b.start);
  return inter / Math.max(Math.min(a.end - a.start, b.end - b.start), 0.001);
};
const iou = (a, b) => {
  const inter = Math.min(a.end, b.end) - Math.max(a.start, b.start);
  return inter <= 0 ? 0 : inter / Math.max(Math.max(a.end, b.end) - Math.min(a.start, b.start), 0.001);
};

// One row per moment: the same tag set over >= 50% of the shorter range is the same moment.
// The kept row (accepted first, then the surer one) carries the others' ids in `dups`.
export function dedupeMoments(rows) {
  const out = [];
  for (const r of [...(rows || [])].sort((a, b) => rank(b) - rank(a))) {
    const twin = out.find((o) => sameTags(o, r) && overlap(o, r) >= 0.5);
    if (twin) twin.dups.push(r.id);
    else out.push({ ...r, dups: [] });
  }
  return out.sort((a, b) => a.start - b.start || a.end - b.end);
}

// Scenes from a film's tag rows: rejected rows left out, rows over (mostly) the same range
// grouped. The best row (accepted first) is the anchor: it gives the range and the name.
// `tags` come from accepted rows, `ai` from proposed ones; `ids` are every row behind it.
export function buildScenes(rows) {
  const live = dedupeMoments(visibleRows(rows)).filter((x) => x.status !== 'rejected');
  const groups = [];
  for (const r of [...live].sort((a, b) => rank(b) - rank(a) || a.start - b.start)) {
    const g = groups.find((x) => iou(x.anchor, r) >= 0.5);
    if (g) g.rows.push(r);
    else groups.push({ anchor: r, rows: [r] });
  }
  return groups.map(({ anchor, rows: rs }) => {
    const kept = rs.filter((x) => x.status === 'accepted');
    const proposed = rs.filter((x) => x.status === 'proposed');
    const tags = uniq(kept.flatMap(shownTags));
    const people = [];
    for (const p of rs.flatMap((x) => x.people || [])) {
      const who = typeof p === 'string' ? { character: p } : p;
      const k = norm(who.name || who.character);
      if (k && !people.some((o) => norm(o.name || o.character) === k)) people.push(who);
    }
    return {
      id: anchor.id, start: anchor.start, end: anchor.end, anchor, rows: rs, kept, proposed,
      name: anchor.summary || rs.find((x) => x.summary)?.summary || '',
      tags, ai: uniq(proposed.flatMap(shownTags)).filter((g) => !tags.includes(g)),
      people, evidence: uniq(rs.flatMap((x) => x.evidence || [])),
      ids: rs.flatMap((x) => [x.id, ...(x.dups || [])]),
    };
  }).sort((a, b) => a.start - b.start || a.end - b.end);
}

// The names a person goes by in tags: the actor's name and, from the cast, the character.
export function personKeys(person, cast) {
  const p = norm(person);
  if (!p) return null;
  const keys = new Set([p]);
  for (const c of cast || []) {
    if (norm(c.name) === p || norm(c.character) === p) {
      if (c.name) keys.add(norm(c.name));
      if (c.character) keys.add(norm(c.character));
    }
  }
  return keys;
}
export const hasPerson = (row, keys) => !!keys
  && (row.people || []).some((x) => (typeof x === 'string' ? keys.has(norm(x)) : keys.has(norm(x.name)) || keys.has(norm(x.character))));

// Tag -> its moments, most used first.
export function tagGroups(rows, names) {
  const by = new Map();
  for (const r of rows) for (const g of shownTags(r)) (by.get(g) || by.set(g, []).get(g)).push(r);
  return [...by].map(([tag, list]) => ({ tag, name: tagLabel(names, tag), rows: list }))
    .sort((a, b) => b.rows.length - a.rows.length || a.name.localeCompare(b.name));
}

// The seconds a segment edit loads: the segment plus 15 s or 10% each side (the larger),
// widened to whole 15 s steps so trimming does not refetch on every nudge.
export function segWindow(from, to, duration) {
  const m = Math.max(15, (to - from) * 0.1);
  const a = Math.max(0, Math.floor((from - m) / 15) * 15);
  let b = Math.ceil((to + m) / 15) * 15;
  if (duration) b = Math.min(b, duration);
  return [a, b];
}

// A copy of the recipe with segment n's range set; throws on a bad range.
export function trimSegment(doc, n, start, end, duration) {
  const hit = flatSegments(doc).segs[n - 1];
  if (!hit) throw new Error(`The cut has no segment ${n}`);
  const s = r3(Math.max(0, start));
  const e = r3(duration ? Math.min(end, duration) : end);
  if (e - s < 0.5) throw new Error('The segment must be at least half a second long');
  const next = structuredClone(doc);
  const seg = next.parts[hit.pi].segments[hit.si];
  seg.start = s;
  seg.end = e;
  return next;
}

// -- clean edges ---------------------------------------------------------------------
// Sound descriptions ("[thunder]", "♪ ...") are not speech.
const SOUND_ONLY = /^[\s-]*([[(].*[\])]|[♪♫#*].*)[\s-]*$/;
export const isSpeech = (text) => !SOUND_ONLY.test(String(text || '').trim());
export const speechLines = (lines) => (lines || []).filter((l) => isSpeech(l.text));
export const cutsOf = (shots) => uniq((shots || []).flat()).sort((a, b) => a - b);
// The line t cuts into (more than 50 ms inside it), or null.
export const midLine = (lines, t) => (lines || []).find((l) => t > l.start + 0.05 && t < l.end - 0.05) || null;
const talking = (lines, t, pad) => lines.some((l) => t > l.start - pad && t < l.end + pad);
const within = (ranges, t) => (ranges || []).some(([a, b]) => t >= a && t <= b);

export const SNAP_NEAR = 3;
export const SNAP_REACH = 20;

// A clean spot for an edge that cuts into a line. A start moves earlier and an end later
// (or onto a cut within SNAP_NEAR either way), so the line stays whole. In order: a camera
// cut nobody talks across (one in quiet first), the quiet just past the line, the pause
// between lines, the line's own edge. Stays within [lo, hi]. -> { t, why }
export function snapEdge(t, { later, lines = [], cuts = [], silence = [], lo = 0, hi = Infinity }) {
  const fits = (x) => x >= lo && x <= hi;
  const near = cuts.filter((c) => Math.abs(c - t) <= SNAP_NEAR).sort((a, b) => Math.abs(a - t) - Math.abs(b - t));
  const far = cuts.filter((c) => (later ? c > t + SNAP_NEAR && c <= t + SNAP_REACH : c < t - SNAP_NEAR && c >= t - SNAP_REACH));
  if (!later) far.reverse();
  const clean = [...near, ...far].filter((c) => fits(c) && !talking(lines, c, 0.3));
  const cut = clean.find((c) => within(silence, c)) ?? clean[0];
  if (cut != null) return { t: r3(cut), why: 'cut' };
  const quiet = later
    ? silence.filter(([a]) => a >= t && a <= t + SNAP_REACH).map(([a, b]) => Math.min(b, a + 0.5))
    : silence.filter(([, b]) => b <= t && b >= t - SNAP_REACH).map(([a, b]) => Math.max(a, b - 0.5)).reverse();
  const calm = quiet.find((x) => fits(x) && !talking(lines, x, 0.1));
  if (calm != null) return { t: r3(calm), why: 'quiet' };
  const gaps = later
    ? lines.map((l) => l.end + 0.3).filter((g) => g >= t && g <= t + SNAP_REACH).sort((a, b) => a - b)
    : lines.map((l) => l.start - 0.3).filter((g) => g <= t && g >= t - SNAP_REACH).sort((a, b) => b - a);
  const gap = gaps.find((g) => fits(g) && !talking(lines, g, 0.25));
  if (gap != null) return { t: r3(gap), why: 'pause' };
  const line = midLine(lines, t);
  if (line) {
    const x = later ? line.end + 0.05 : line.start - 0.05;
    if (fits(x)) return { t: r3(x), why: 'line' };
  }
  return null;
}

// What an edge lands on, for the note next to it.
export function edgeNote(t, { lines = [], cuts = [], silence = [] }) {
  if (midLine(lines, t)) return null;
  if (within(silence, t)) return 'Lands in quiet';
  if (cuts.some((c) => Math.abs(c - t) < 0.15)) return 'Lands on a camera cut';
  return 'Lands between lines';
}

const hms = (s) => {
  const v = Math.max(0, Math.floor(s));
  return `${Math.floor(v / 3600)}:${String(Math.floor((v % 3600) / 60)).padStart(2, '0')}:${String(v % 60).padStart(2, '0')}`;
};
const ms = (s) => `${Math.floor(s / 60)}:${String(Math.floor(s % 60)).padStart(2, '0')}`;

// A running job, polled until it ends; on load it picks up one already running for this film.
function useJob(fkey, onDone) {
  const [job, setJob] = useState(null);
  useEffect(() => {
    let live = true;
    api('/jobs').then((d) => {
      const mine = (d.jobs || []).find((j) => (j.kind === 'scene_index' || j.kind === 'scene_tag')
        && j.payload?.key === fkey && (j.status === 'running' || j.status === 'queued'));
      if (live && mine) setJob(mine);
    }).catch(() => {});
    return () => { live = false; };
  }, [fkey]);
  const poll = useCallback(async () => {
    if (!job) return;
    const j = await api(`/jobs/${job.id}`).catch(() => null);
    if (!j) return;
    setJob(j);
    if (j.status !== 'running' && j.status !== 'queued') onDone(j);
  }, [job, onDone]);
  usePoll(poll, 1500, !!job && (job.status === 'running' || job.status === 'queued'));
  return [job, setJob];
}

function JobLine({ job }) {
  if (!job) return null;
  const live = job.status === 'running' || job.status === 'queued';
  return (
    <div className="row wrap sx-job">
      <span className="tag">{job.status}</span>
      <span className="muted tiny">{job.title}{job.message ? ` · ${job.message}` : ''}</span>
      {live && <Progress value={job.progress} />}
      {job.error && <span className="c-danger tiny">{job.error}</span>}
    </div>
  );
}

// Type-ahead over the vocab: Enter adds the highlighted tag (or a new one), Esc closes.
function TagAdder({ current, names, onAdd, onClose }) {
  const vocab = useApi('/scenes/vocab', { quiet: true });
  const [text, setText] = useState('');
  const [i, setI] = useState(0);
  const all = useMemo(() => [...(vocab.data?.tags || []), ...(vocab.data?.cut_tags || [])]
    .map((x) => ({ id: x.id, name: x.name || tagLabel(names, x.id) })), [vocab.data, names]);
  const qy = norm(text);
  const hits = useMemo(() => {
    const free = all.filter((x) => !current.includes(x.id));
    if (!qy) return free.slice(0, 8);
    return free.filter((x) => norm(x.name).includes(qy))
      .sort((a, b) => Number(!norm(a.name).startsWith(qy)) - Number(!norm(b.name).startsWith(qy))).slice(0, 8);
  }, [all, qy, current]);
  const custom = qy && !all.some((x) => norm(x.name) === qy) ? qy.replace(/\s+/g, '-') : null;
  const opts = [...hits.map((x) => [x.id, x.name]), ...(custom ? [[custom, `Add “${text.trim()}”`]] : [])];
  const pick = (id) => { onAdd(id); setText(''); setI(0); };
  const onKey = (e) => {
    if (e.key === 'Escape') { e.preventDefault(); e.stopPropagation(); onClose(); } else if (e.key === 'ArrowDown') { e.preventDefault(); setI((v) => Math.min(v + 1, opts.length - 1)); } else if (e.key === 'ArrowUp') { e.preventDefault(); setI((v) => Math.max(v - 1, 0)); } else if (e.key === 'Enter') {
      e.preventDefault();
      const o = opts[Math.min(i, opts.length - 1)];
      if (o) pick(o[0]);
    }
  };
  return (
    <div className="sx-adder">
      <input className="inp" autoFocus aria-label="Tag" placeholder="Tag" value={text}
        onChange={(e) => { setText(e.target.value); setI(0); }} onKeyDown={onKey} onBlur={(e) => { if (!e.relatedTarget?.closest?.('.sx-adder')) onClose(); }} />
      {opts.length > 0 && (
        <div className="sx-opts" role="listbox">
          {opts.map(([id, name], j) => (
            <button key={id} type="button" role="option" aria-selected={j === i} className="sx-opt"
              onMouseEnter={() => setI(j)} onClick={() => pick(id)}>{name}</button>
          ))}
        </div>
      )}
    </div>
  );
}

// Matched faces on the still; the followed person's box stands out.
function FaceBoxes({ faces, keys }) {
  return (faces || []).map((f, i) => {
    const [x, y, w, h] = f.box || [];
    if (w == null || !f.name) return null;
    const me = keys && (keys.has(norm(f.name)) || keys.has(norm(f.character)));
    return (
      <span key={i} className={`sx-fb ${f.sure ? '' : 'sx-fb-maybe'} ${me ? 'sx-fb-me' : ''}`}
        style={{ left: `${x * 100}%`, top: `${y * 100}%`, width: `${w * 100}%`, height: `${h * 100}%` }} />
    );
  });
}

// Who is on screen at t: named faces first, else the people of the tag rows covering t.
function useOnScreen(fkey, t, faces, cast) {
  const step = Math.floor(t / 2);
  const tRef = useRef(t);
  useEffect(() => { tRef.current = t; });
  const { data } = useLoad(() => (fkey ? api(`/scenes/xray?${q({ key: fkey, t: Math.floor(tRef.current) })}`).catch(() => null) : Promise.resolve(null)), [fkey, step]);
  const photo = useMemo(() => Object.fromEntries((cast || []).map((c) => [c.name, c.photo])), [cast]);
  const named = namedFaces(faces);
  if (named.length) return named.map((f) => ({ name: f.name, character: f.character, photo: f.photo || photo[f.name], sure: f.sure }));
  return (data?.people || []).map((p) => ({ name: p.name, character: p.character, photo: p.photo || photo[p.name], sure: true }));
}

// The window of the film the detailed track shows, and its scenes, thumbnails, speech.
function Track({ fkey, win, scenes, sel, edges, lines, quiet, t, names, keys, onSeek, onPick, onEdge }) {
  const [v0, v1] = win;
  const span = Math.max(v1 - v0, 1);
  const [drag, setDrag] = useState(null);
  const clampV = (x) => Math.min(v1, Math.max(v0, x));
  const pct = (x) => `${((clampV(x) - v0) / span) * 100}%`;
  const wid = (a, b) => `${((clampV(b) - clampV(a)) / span) * 100}%`;
  const at = (e) => {
    const r = e.currentTarget.closest('.sx-trk').getBoundingClientRect();
    return v0 + Math.min(1, Math.max(0, (e.clientX - r.left) / r.width)) * span;
  };
  const inView = scenes.filter((s) => s.end > v0 && s.start < v1);
  const gaps = [];
  let c = v0;
  for (const s of inView) { if (s.start > c) gaps.push([c, s.start]); c = Math.max(c, s.end); }
  if (c < v1) gaps.push([c, v1]);
  const thumbs = Array.from({ length: 12 }, (_, i) => Math.round((v0 + ((i + 0.5) * span) / 12) * 10) / 10);
  const ruler = Array.from({ length: 8 }, (_, i) => v0 + (i * span) / 7);
  const [a, b] = edges ? [drag?.edge === 'start' ? drag.t : edges.start, drag?.edge === 'end' ? drag.t : edges.end] : [null, null];
  const show = (x) => x != null && x >= v0 && x <= v1;
  const grip = (edge) => ({
    onPointerDown: (e) => { e.stopPropagation(); e.currentTarget.setPointerCapture(e.pointerId); setDrag({ edge, t: edge === 'start' ? a : b }); },
    onPointerMove: (e) => { if (drag?.edge === edge) setDrag({ edge, t: at(e) }); },
    onPointerUp: (e) => { if (drag?.edge === edge) { e.stopPropagation(); onEdge(edge, drag.t); setDrag(null); } },
    onClick: (e) => e.stopPropagation(),
  });
  return (
    <div className="sx-trk" onClick={(e) => onSeek(at(e))} role="presentation">
      <div className="sx-ruler">
        {ruler.map((m, i) => <span key={i} className="mono" style={{ left: `${(i * 100) / 7}%`, transform: `translateX(${i === 0 ? '0' : i === 7 ? '-100%' : '-50%'})` }}>{hms(m)}</span>)}
      </div>
      <div className="sx-scn">
        {inView.map((s) => {
          const on = sel?.id === s.id;
          return (
            <button key={s.id} type="button" className={`sx-tab ${on ? 'is-on' : ''}`} aria-pressed={on} title={s.name}
              style={{ left: pct(s.start), width: wid(s.start, s.end) }} onClick={(e) => { e.stopPropagation(); onPick(s); }}>
              {s.name || tagLabel(names, s.tags[0] || s.ai[0])}
            </button>
          );
        })}
        <div className="sx-thumbs">{thumbs.map((x) => <img key={x} src={sceneFrameUrl(fkey, x, 90)} alt="" loading="lazy" />)}</div>
        {gaps.map(([g0, g1]) => <span key={g0} className="sx-gap" style={{ left: pct(g0), width: wid(g0, g1) }} />)}
        {inView.map((s) => {
          const on = sel?.id === s.id;
          const dim = keys && !hasPerson(s, keys);
          return <span key={s.id} className={`sx-frm ${on ? 'is-on' : ''} ${s.kept.length ? '' : 'is-ai'} ${dim ? 'is-dim' : ''}`} style={{ left: pct(s.start), width: wid(s.start, s.end) }} />;
        })}
        {show(a) && <span className={`sx-grip sx-grip-l ${edges.inWarn ? 'is-warn' : ''}`} style={{ left: pct(a) }} aria-label="Drag the start" role="slider" aria-valuenow={a} {...grip('start')}><i /></span>}
        {show(b) && <span className={`sx-grip sx-grip-r ${edges.outWarn ? 'is-warn' : ''}`} style={{ left: pct(b) }} aria-label="Drag the end" role="slider" aria-valuenow={b} {...grip('end')}><i /></span>}
        {inView.map((s) => (
          <div key={s.id} className="sx-chips" style={{ left: pct(s.start), width: wid(s.start, s.end) }}>
            {s.tags.map((g) => <span key={g} className={sel?.id === s.id ? 'is-on' : ''}>{tagLabel(names, g)}</span>)}
            {s.ai.map((g) => <span key={g} className="is-ai">{tagLabel(names, g)}</span>)}
          </div>
        ))}
      </div>
      <div className="sx-lane">
        {quiet.filter(([q0, q1]) => q1 > v0 && q0 < v1).map(([q0, q1]) => <span key={q0} className="sx-quiet" style={{ left: pct(q0), width: `max(3px, ${wid(q0, q1)})` }} />)}
        {lines.filter((l) => l.end > v0 && l.start < v1).map((l) => <span key={l.start} className="sx-talk" title={l.text} style={{ left: pct(l.start), width: `max(3px, ${wid(l.start, l.end)})` }} />)}
      </div>
      {show(a) && <span className={`sx-edge ${edges.inWarn ? 'is-warn' : ''}`} style={{ left: pct(a) }} />}
      {show(b) && <span className={`sx-edge ${edges.outWarn ? 'is-warn' : ''}`} style={{ left: pct(b) }} />}
      {t >= v0 && t <= v1 && <><span className="sx-ph" style={{ left: pct(t) }} /><span className="sx-phcap" style={{ left: pct(t) }} /></>}
    </div>
  );
}

// The film's library copy for a recipe source (by TMDB id), an indexed copy first.
const filmForSource = (doc, src, films) => {
  const s = doc?.sources?.[src];
  if (!s || s.tmdb == null) return null;
  const all = (films || []).filter((f) => String(f.tmdb_id) === String(s.tmdb));
  return all.find((f) => f.indexed) || all.find((f) => f.on_disk) || all[0] || null;
};

const ICON = {
  undo: 'M9 7L4 12l5 5M4 12h10a6 6 0 0 1 0 12',
  redo: 'M15 7l5 5-5 5M20 12H10a6 6 0 0 0 0 12',
  split: 'M12 3v18M7 8l-3 4 3 4M17 8l3 4-3 4',
};
const Svg = ({ d, size = 17 }) => (
  <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" aria-hidden="true"><path d={d} /></svg>
);

export function ScenesPage() {
  const { key } = useParams();
  const navigate = useNavigate();
  // Everything that makes a place lives in the URL, so a reload or a link opens it again.
  const [params, setParams] = useSearchParams();
  const t = Number(params.get('t')) || 0;
  const sel = params.get('scene') || null;
  const person = params.get('person') || '';
  const tagF = params.get('tag') || '';
  const cut = params.get('cut') || '';
  const segN = Number(params.get('seg')) || 0;
  const from = Number(params.get('from'));
  const to = Number(params.get('to'));
  const segMode = !!cut && segN > 0 && Number.isFinite(from) && to > from;
  const patch = useCallback((o) => setParams((p) => {
    const n = new URLSearchParams(p);
    for (const [k, v] of Object.entries(o)) {
      if (v == null || v === '' || v === false) n.delete(k);
      else n.set(k, k === 't' || k === 'from' || k === 'to' ? String(Math.round(v * 1000) / 1000) : v === true ? '1' : v);
    }
    return n;
  }, { replace: true }), [setParams]);

  // segment mode loads the film without its subtitles, and only the window's lines
  const [w0, w1] = segMode ? segWindow(from, to) : [0, 0];
  const opened = useRef(null);
  const { data, error, loading, reload } = useLoad(async () => {
    if (!segMode) return api(`/scenes/film?${q({ key })}`);
    const [film, films, lines] = await Promise.all([
      api(`/scenes/film?${q({ key, lines: false })}`), api('/scenes/films'),
      api(`/scenes/transcript?${q({ key, start: w0, end: w1 })}`).catch(() => ({ lines: [] })),
    ]);
    return { ...film, index: film.index && { ...film.index, lines: lines.lines || [] }, films: films.films || [] };
  }, [key, segMode, w0, w1]);
  const signals = useLoad(() => api(`/scenes/signals?${q({ key })}`).catch(() => null), [key]);
  const tl = useApi(segMode ? `/timelines/${enc(cut)}` : null);

  const { act, busy } = useAct();
  const [saved, setSaved] = useState(false);
  const [play, setPlay] = useState(false);
  const [chunk, setChunk] = useState(0);
  const [clipT, setClipT] = useState(null);
  const [running, setRunning] = useState(false);
  const [adding, setAdding] = useState(false);
  const [renaming, setRenaming] = useState(null);
  const [overlay, setOverlay] = useState(true);
  const sleepT = useRef(null);
  const names = useTagNames();
  usePageTitle(data?.film ? `${data.film.title} scenes and tags` : null);
  const onDone = useCallback(() => { reload(); }, [reload]);
  const [job, setJob] = useJob(key, onDone);
  useEffect(() => () => clearTimeout(sleepT.current), []);

  const doc = data?.index;
  const duration = doc?.duration || data?.film?.duration || 0;
  const cast = useMemo(() => data?.cast || [], [data]);
  const keys = useMemo(() => personKeys(person, cast), [person, cast]);
  const scenes = useMemo(() => buildScenes(data?.tags), [data]);
  const lines = useMemo(() => speechLines(doc?.lines), [doc]);
  const cuts = useMemo(() => cutsOf(doc?.shots), [doc]);
  const silence = useMemo(() => signals.data?.signals?.silence || [], [signals.data]);
  const ctx = useMemo(() => ({ lines, cuts, silence }), [lines, cuts, silence]);

  // the cut's segment (segment mode): the recipe is the truth once loaded
  const recipe = tl.data?.doc;
  const segs = useMemo(() => flatSegments(recipe).segs, [recipe]);
  const segHit = segMode ? segs[segN - 1] : null;
  const segA = segMode ? segHit?.seg.start ?? from : null;
  const segB = segMode ? segHit?.seg.end ?? to : null;

  // open on the person's first scene, else the first confirmed one, else the first
  useEffect(() => {
    if (!data || opened.current === key || segMode) return;
    opened.current = key;
    const now = new URLSearchParams(window.location.search);
    if (now.get('scene') || now.get('t')) return;
    const mine = keys ? scenes.filter((s) => hasPerson(s, keys)) : scenes;
    const first = mine.find((s) => s.kept.length) || mine[0] || scenes[0];
    if (first) patch({ scene: first.id, t: first.start + 0.1 });
  }, [data, key, segMode, patch, scenes, keys]);

  const still = segMode && !params.get('t') ? segA + 0.3 : t;
  const at = play && clipT != null ? clipT : still;
  const picked = sel ? scenes.find((s) => s.ids.includes(sel)) || null : null;
  const hereScene = (picked && picked.start <= at && at < picked.end ? picked : scenes.find((s) => s.start <= at && at < s.end)) || null;
  const scene = picked || hereScene;
  const idx = scene ? scenes.indexOf(scene) : -1;
  const prevS = idx > 0 ? scenes[idx - 1] : null;
  const nextS = idx >= 0 ? scenes[idx + 1] || null : null;
  const sceneA = scene?.start;
  const sceneB = scene?.end;

  // what Space plays: the segment, else the scene, else a minute from the playhead
  const view = segMode && segA != null ? [segA, segB]
    : sceneA != null ? [sceneA, sceneB] : [t, Math.min(duration || t + 60, t + 60)];
  const windows = clipWindows(view[0], view[1]);
  const faces = useFacesAt(doc ? key : null, at, play && running);
  const onScreen = useOnScreen(doc ? key : null, at, faces, cast);

  // the window the track shows: the segment's window, else the scene with a margin
  const win = (() => {
    if (segMode) return segWindow(segA ?? from, segB ?? to, duration);
    const [a, b] = sceneA != null ? [sceneA, sceneB] : [Math.max(0, t - 60), t + 60];
    const pad = Math.max(10, (b - a) * 0.25);
    return [Math.max(0, a - pad), Math.min(duration || b + pad, b + pad)];
  })();

  const stop = useCallback(() => { setPlay(false); setClipT(null); setRunning(false); setChunk(0); }, []);
  const togglePlay = useCallback(() => {
    if (play && clipT != null) patch({ t: clipT });
    if (play) stop(); else { setPlay(true); setChunk(0); setClipT(null); }
  }, [play, clipT, patch, stop]);
  const seek = useCallback((time) => {
    patch({ t: Math.min(Math.max(time, 0), duration || time) });
    stop();
  }, [patch, stop, duration]);
  const pick = useCallback((s) => {
    patch({ scene: s.id, t: s.start + 0.1 });
    stop();
    setAdding(false);
    setRenaming(null);
  }, [patch, stop]);
  const setPerson = useCallback((name) => patch({ person: name || null }), [patch]);

  // -- saving ---------------------------------------------------------------------------
  const save = useCallback((fn) => act(async () => {
    await fn();
    await reload();
    setSaved(true);
    return true;
  }), [act, reload]);
  const each = (ids, body) => Promise.all(ids.map((id) => api(`/scenes/tags/${id}`, { method: 'PATCH', body })));
  const setRange = (s, start, end) => save(() => each(s.ids, { start: r3(start), end: r3(end) }));
  // an edge may cross into a neighbour (AI scenes often overlap); a scene stays 1 s long
  const sceneEdge = (s, edge, v) => {
    const lo = edge === 'start' ? 0 : s.start + 1;
    const hi = edge === 'end' ? duration || Infinity : s.end - 1;
    if (v < lo - 0.001 || v > hi + 0.001) return;
    setRange(s, edge === 'start' ? v : s.start, edge === 'end' ? v : s.end);
  };

  // segment mode: trim the cut's segment and save the recipe
  const trim = useCallback((start, end) => act(async () => {
    const fresh = await api(`/timelines/${enc(cut)}`);
    const next = trimSegment(fresh.doc, segN, start, end, duration);
    await api(`/timelines/${enc(cut)}`, { method: 'PUT', body: next });
    await tl.reload();
    const s = flatSegments(next).segs[segN - 1].seg;
    patch({ from: s.start, to: s.end });
    setSaved(true);
  }), [act, cut, segN, duration, tl, patch]);

  // the edges the bar and the grips edit: the segment's, else the scene's
  const edgeTarget = segMode && segA != null ? { start: segA, end: segB, seg: true }
    : scene ? { start: scene.start, end: scene.end, seg: false } : null;
  const setEdgeTo = (edge, v) => {
    if (!edgeTarget) return;
    if (edgeTarget.seg) trim(edge === 'start' ? v : segA, edge === 'end' ? v : segB);
    else sceneEdge(scene, edge, v);
  };
  const inHit = edgeTarget ? midLine(lines, edgeTarget.start) : null;
  const outHit = edgeTarget ? midLine(lines, edgeTarget.end) : null;
  const bounds = (edge) => (edgeTarget?.seg
    ? (edge === 'start' ? [0, segB - 0.5] : [segA + 0.5, duration || Infinity])
    : edge === 'start' ? [0, scene.end - 1] : [scene.start + 1, duration || Infinity]);
  const snapTo = (edge) => {
    const [lo, hi] = bounds(edge);
    const s = snapEdge(edge === 'start' ? edgeTarget.start : edgeTarget.end, { later: edge === 'end', ...ctx, lo, hi });
    if (s) setEdgeTo(edge, s.t);
  };

  const split = () => {
    if (!hereScene || at < hereScene.start + 1 || at > hereScene.end - 1) return;
    const s = hereScene;
    const cutAt = r3(at);
    save(async () => {
      const made = [];
      for (const r of s.rows) {
        await api(`/scenes/tags/${r.id}`, { method: 'PATCH', body: { end: cutAt } });
        made.push(await api('/scenes/tags', {
          method: 'POST',
          body: {
            key, start: cutAt, end: r.end, tags: r.tags, people: r.people || [], status: r.status,
            summary: r.summary ? `${r.summary}, part 2` : '', source: r.source || 'user',
            confidence: r.confidence ?? undefined, snap: false,
          },
        }));
      }
      const anchor = made[s.rows.indexOf(s.anchor)];
      if (anchor?.id) patch({ scene: anchor.id, t: cutAt + 0.1 });
    });
  };
  const merge = () => {
    if (!scene || !nextS) return;
    const a = scene.start;
    const b = nextS.end;
    save(async () => {
      await each([...scene.ids, ...nextS.ids], { start: r3(a), end: r3(b) });
      if (!scene.anchor.summary && nextS.name) await api(`/scenes/tags/${scene.anchor.id}`, { method: 'PATCH', body: { summary: nextS.name } });
    });
  };
  const delScene = () => {
    if (!scene) return;
    const go = nextS || prevS;
    save(async () => {
      for (const id of scene.ids) await api(`/scenes/tags/${id}`, { method: 'DELETE' });
      patch({ scene: go ? go.id : null });
    });
  };
  const rename = (name) => {
    if (!scene) return;
    setRenaming(null);
    if (name.trim() === scene.name) return;
    save(() => api(`/scenes/tags/${scene.anchor.id}`, { method: 'PATCH', body: { summary: name.trim() } }));
  };
  const keepAI = () => scene && save(() => each(scene.proposed.flatMap((r) => [r.id, ...(r.dups || [])]),
    { status: 'accepted', start: r3(scene.start), end: r3(scene.end) }));
  const dropAI = () => scene && save(() => each(scene.proposed.flatMap((r) => [r.id, ...(r.dups || [])]), { status: 'rejected' }));
  // a new tag goes on the scene's accepted row; an AI-only scene gets one, over its range
  const addTag = (id) => {
    if (!scene && !hereScene) {
      const auto = (doc?.scenes || []).find((s) => s.start <= at && at < s.end);
      const [a, b] = auto ? [auto.start, auto.end] : view;
      return save(async () => {
        const x = await api('/scenes/tags', { method: 'POST', body: { key, start: r3(a), end: r3(b), tags: [id], status: 'accepted', snap: false } });
        if (x?.id) patch({ scene: x.id });
      });
    }
    const s = scene || hereScene;
    const row = s.kept.includes(s.anchor) ? s.anchor : s.kept[0];
    if (row) return save(() => api(`/scenes/tags/${row.id}`, { method: 'PATCH', body: { tags: uniq([...row.tags, id]) } }));
    return save(() => api('/scenes/tags', {
      method: 'POST', body: { key, start: r3(s.start), end: r3(s.end), tags: [id], status: 'accepted', summary: s.name, people: s.people, snap: false },
    }));
  };
  // a tag comes off every accepted row of the scene; a row left with no tag goes
  const lastTag = scene ? scene.tags.length <= 1 && scene.proposed.length === 0 : true;
  const removeTag = (g) => {
    if (!scene || lastTag) return;
    save(async () => {
      for (const r of scene.kept) {
        if (!r.tags.includes(g)) continue;
        const rest = r.tags.filter((x) => x !== g);
        for (const id of [r.id, ...(r.dups || [])]) {
          if (rest.length) await api(`/scenes/tags/${id}`, { method: 'PATCH', body: { tags: rest } });
          else await api(`/scenes/tags/${id}`, { method: 'DELETE' });
        }
      }
    });
  };

  const goSeg = (n) => {
    const s = segs[n - 1];
    const f = s && filmForSource(recipe, s.seg.src, data?.films);
    if (f) navigate(segmentEditUrl(f.key, s.seg, cut, n));
  };
  const segFilm = (n) => { const s = segs[n - 1]; return s && filmForSource(recipe, s.seg.src, data?.films); };

  // previous / next scene (of the person, when following one)
  const pool = useMemo(() => (keys ? scenes.filter((s) => hasPerson(s, keys)) : scenes), [scenes, keys]);
  const stepScene = useCallback((dir, list = scenes) => {
    const n = dir > 0 ? list.find((s) => s.start > at + 0.5) : [...list].reverse().find((s) => s.start < at - 1);
    if (n) pick(n);
  }, [scenes, at, pick]);

  const wake = () => {
    clearTimeout(sleepT.current);
    sleepT.current = setTimeout(() => setOverlay(false), 2600);
    if (!overlay) setOverlay(true);
  };
  const sleep = () => { clearTimeout(sleepT.current); setOverlay(false); };

  useEffect(() => {
    const onKey = (e) => {
      if (e.target.closest?.('input, textarea, select, video') || e.ctrlKey || e.metaKey || e.altKey) return;
      const k = e.key.length === 1 ? e.key.toLowerCase() : e.key;
      if (k === ' ') togglePlay();
      else if (k === 's' && !segMode) split();
      else if (k === 'i') setEdgeTo('start', r3(at));
      else if (k === 'o') setEdgeTo('end', r3(at));
      else if (k === ']') stepScene(1);
      else if (k === '[') stepScene(-1);
      else if (k === 'n' && keys) stepScene(1, pool);
      else if (k === 'p' && keys) stepScene(-1, pool);
      else if (k === 't' && scene) setAdding(true);
      else if (k === 'ArrowLeft' || k === 'ArrowRight') {
        const dir = k === 'ArrowLeft' ? -1 : 1;
        if (e.shiftKey && doc) seek(dir < 0 ? prevCut(doc.shots, at) : nextCut(doc.shots, at, duration));
        else seek(at + dir);
      } else if (k === 'Escape') { setAdding(false); setRenaming(null); } else return;
      e.preventDefault();
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  });

  if (error && !data) return <div className="page"><Crumbs items={[['Library', '/library'], [key]]} /><ErrorBox error={error} onRetry={reload} /></div>;
  if (loading || !data) return <div className="page"><Loading /></div>;

  const f = data.film;
  const startIndex = (transcribe = false) => act(async () => {
    setJob(await api('/scenes/index', { method: 'POST', body: { key, transcribe } }));
  });
  const back = segMode ? `/cuts/${enc(cut)}` : `/movies/${enc(key)}`;
  const crumbs = segMode
    ? [['My cuts', '/my-cuts'], [tl.data?.title || cut, back], [`Scene ${segN}`]]
    : [['Library', '/library'], [f.title, back], ['Scenes and tags']];

  // whole film: the automatic scenes as a strip, lit where a scene has the picked tag/person
  const base = (doc?.scenes?.length ? doc.scenes : scenes).map((s) => [s.start, s.end]);
  const counts = new Map();
  for (const s of scenes) for (const g of uniq([...s.tags, ...s.ai])) counts.set(g, (counts.get(g) || 0) + 1);
  const topTags = [...counts].sort((a, b) => b[1] - a[1] || tagLabel(names, a[0]).localeCompare(tagLabel(names, b[0]))).slice(0, 8);
  if (tagF && !counts.has(tagF)) topTags.push([tagF, 0]);
  const matches = (s) => (!tagF || s.tags.includes(tagF) || s.ai.includes(tagF)) && (!keys || hasPerson(s, keys));
  const filtering = !!tagF || !!keys;
  const lit = scenes.filter(matches);
  const D = duration || 1;
  const hours = [];
  for (let h = 0; h <= D; h += 1800) hours.push(h);
  const filmAt = (e) => {
    const r = e.currentTarget.getBoundingClientRect();
    const x = Math.min(1, Math.max(0, (e.clientX - r.left) / r.width)) * D;
    const s = scenes.find((y) => y.start <= x && x < y.end);
    patch({ t: x, scene: s ? s.id : null });
    stop();
  };

  const inWin = scenes.filter((s) => s.end > win[0] && s.start < win[1]);
  const status = (s) => (s.kept.length === 0
    ? 'AI scene, not confirmed'
    : `${s.tags.length} ${s.tags.length === 1 ? 'tag' : 'tags'}${s.ai.length ? ` · ${s.ai.length} from AI to review` : ''}`);
  const edgeInfo = edgeTarget && { ...edgeTarget, inWarn: !!inHit, outWarn: !!outHit };
  const caption = (() => {
    const l = lines.find((x) => x.start <= at && at < x.end);
    if (l) return l.text.replace(/\s*\n\s*/g, ' ');
    return within(silence, at) ? 'Quiet' : '';
  })();
  const ar = doc?.width && doc?.height ? doc.width / doc.height : 16 / 9;

  const edgeRow = (edge) => {
    const v = edge === 'start' ? edgeTarget.start : edgeTarget.end;
    const hit = edge === 'start' ? inHit : outHit;
    return (
      <div className="sx-edgebox">
        <Label as="span">{edge === 'start' ? 'Starts' : 'Ends'}</Label>
        <span className="mono">{hms(v)}</span>
        {hit
          ? <><span className="sx-warn">In the middle of a line</span><button type="button" className="btn btn-sm" disabled={busy} onClick={() => snapTo(edge)}>Snap</button></>
          : <span className="sx-note">{edgeNote(v, ctx)}</span>}
      </div>
    );
  };

  return (
    <div className="page page-wide sx">
      <div className="sx-head">
        <Crumbs items={crumbs} />
        <span className="sx-grow" />
        {segMode && (
          <>
            <button type="button" className="btn btn-sm" disabled={!segFilm(segN - 1)} onClick={() => goSeg(segN - 1)}>Previous cut scene</button>
            <button type="button" className="btn btn-sm" disabled={!segFilm(segN + 1)} onClick={() => goSeg(segN + 1)}>Next cut scene</button>
          </>
        )}
        <button type="button" className="icon-btn" aria-label="Undo" title="Not yet" disabled><Svg d={ICON.undo} /></button>
        <button type="button" className="icon-btn" aria-label="Redo" title="Not yet" disabled><Svg d={ICON.redo} /></button>
        {(saved || busy) && <span className="sx-saved">{busy ? 'Saving' : 'Saved'}</span>}
        <Link className="btn" to={back}>Done</Link>
      </div>
      <JobLine job={job} />

      {!doc ? (
        <Empty>
          Indexing reads the subtitles and finds every camera cut.
          {f.on_disk ? '' : ' This film has no file on disk.'}
          {' '}<button type="button" className="btn btn-sm btn-primary" disabled={busy || !f.on_disk} onClick={() => startIndex(false)}>Index scenes</button>
          {doc === null && f.on_disk && (
            <> Films without text subtitles can be <button type="button" className="btn btn-sm" onClick={() => startIndex(true)}>transcribed</button> instead.</>
          )}
        </Empty>
      ) : (
        <>
          <div className="sx-top">
            <section className="sx-viewer" aria-label="Viewer">
              <div className="sx-stage" style={{ '--ar': ar }} onMouseMove={wake} onMouseLeave={sleep}>
                {play
                  ? <ClipVideo src={clipUrl(key, windows[Math.min(chunk, windows.length - 1)][0], windows[Math.min(chunk, windows.length - 1)][1])}
                    offset={windows[Math.min(chunk, windows.length - 1)][0]} onTime={setClipT} onRunning={setRunning}
                    onEnded={() => { if (chunk + 1 < windows.length) { setChunk(chunk + 1); setClipT(null); } else { patch({ t: view[1] - 0.1 }); stop(); } }} />
                  : <img src={sceneFrameUrl(key, Math.max(0, still), 720)} alt={`Film frame at ${fmtTime(still)}`} />}
                {!play && <FaceBoxes faces={faces} keys={keys} />}
                <div className={`sx-ov ${overlay ? '' : 'is-off'}`} aria-label="What FansCut found at this moment">
                  <span className="sx-ov-name">{hereScene ? hereScene.name || 'Scene' : 'Between scenes'}</span>
                  <span className="sx-ov-by">Found by FansCut</span>
                  {onScreen.length > 0 && (
                    <div className="sx-ov-people">
                      {onScreen.slice(0, 5).map((p) => (
                        <button key={p.name || p.character} type="button" onClick={() => setPerson(p.name || p.character)}
                          className={keys && (keys.has(norm(p.name)) || keys.has(norm(p.character))) ? 'is-me' : ''}>
                          {p.photo ? <img src={p.photo} alt="" /> : <span className="sx-noface">{(p.character || p.name || '?').slice(0, 1)}</span>}
                          <span>{p.character || p.name}{p.sure ? '' : '?'}</span>
                        </button>
                      ))}
                    </div>
                  )}
                  <div className="sx-ov-cap">{caption}</div>
                </div>
              </div>

              <div className="sx-controls">
                <IconButton kind="prev" label="Previous scene" onClick={() => stepScene(-1)} />
                <IconButton kind={play && running ? 'pause' : 'play'} label={play ? 'Pause' : 'Play'} go onClick={togglePlay} />
                <IconButton kind="next" label="Next scene" onClick={() => stepScene(1)} />
                <span className="mono sx-now">{hms(at)}</span>
                <span className="mono sx-of">of {hms(duration)}</span>
                <span className="sx-grow" />
                {!segMode && (
                  <button type="button" className="sx-tool" disabled={busy || !hereScene || at < hereScene.start + 1 || at > hereScene.end - 1} onClick={split}>
                    <Svg d={ICON.split} size={14} />Split here <span className="kbd">S</span>
                  </button>
                )}
                <button type="button" className="sx-tool" disabled={busy || !edgeTarget} onClick={() => setEdgeTo('start', r3(at))}>Scene starts here <span className="kbd">I</span></button>
                <button type="button" className="sx-tool" disabled={busy || !edgeTarget} onClick={() => setEdgeTo('end', r3(at))}>Scene ends here <span className="kbd">O</span></button>
              </div>

              {edgeTarget && (
                <div className="sx-edges" aria-label="Where this scene starts and ends">
                  {segMode && <Label as="span">Cut scene {segN}</Label>}
                  {edgeRow('start')}
                  {edgeRow('end')}
                  <span className="sx-grow" />
                  <span className="mono sx-of">{ms(edgeTarget.end - edgeTarget.start)} long</span>
                </div>
              )}
            </section>

            <aside className="sx-side" aria-label="Selected scene">
              <div className="sx-card">
                {scene ? (
                  <>
                    <div>
                      <div className="sx-row"><Label as="span">Scene</Label><span className="mono sx-range">{hms(scene.start)} to {hms(scene.end)}</span></div>
                      <div className="sx-title">
                        {renaming != null ? (
                          <input className="inp" autoFocus aria-label="Scene name" value={renaming}
                            onChange={(e) => setRenaming(e.target.value)} onBlur={() => rename(renaming)}
                            onKeyDown={(e) => { if (e.key === 'Enter') rename(renaming); else if (e.key === 'Escape') setRenaming(null); }} />
                        ) : (
                          <>
                            <h1 className="hd">{scene.name || 'No name yet'}</h1>
                            <button type="button" className="btn btn-sm" onClick={() => setRenaming(scene.name)}>Rename</button>
                          </>
                        )}
                      </div>
                    </div>
                    <div>
                      <Label>Tags</Label>
                      <div className="tags-row sx-tags">
                        {scene.tags.map((g) => (
                          <EditTag key={g} name={tagLabel(names, g)} disabled={busy || lastTag} onRemove={() => removeTag(g)} />
                        ))}
                        {adding
                          ? <TagAdder current={[...scene.tags, ...scene.ai]} names={names} onAdd={(id) => { addTag(id); }} onClose={() => setAdding(false)} />
                          : <button type="button" className="tag-add" onClick={() => setAdding(true)}>Add tag <span className="kbd">T</span></button>}
                      </div>
                    </div>
                    {scene.ai.length > 0 && (
                      <div className="sx-ai">
                        <div className="sx-row">
                          <Label as="span" className="lab sx-grow">AI suggests</Label>
                          <button type="button" className="btn btn-sm" disabled={busy} onClick={keepAI}>Keep</button>
                          <button type="button" className="btn btn-sm btn-quiet" disabled={busy} onClick={dropAI}>Drop</button>
                        </div>
                        <div className="tags-row sx-tags">{scene.ai.map((g) => <AiTag key={g} name={tagLabel(names, g)} />)}</div>
                      </div>
                    )}
                    {(scene.evidence.length > 0 || scene.people.length > 0) && (
                      <div className="sx-found">
                        {scene.people.length > 0 && (
                          <div className="tags-row">
                            {scene.people.map((p) => (
                              <button key={`${p.name}-${p.character}`} type="button" className="fchip fchip-sm"
                                aria-pressed={!!keys && (keys.has(norm(p.name)) || keys.has(norm(p.character)))}
                                onClick={() => setPerson(p.name || p.character)}>{p.character || p.name}</button>
                            ))}
                          </div>
                        )}
                        {scene.evidence.length > 0 && <span className="tiny muted">Seen: {scene.evidence.join(', ')}</span>}
                      </div>
                    )}
                    <div className="sx-acts">
                      <button type="button" className="sx-tool" disabled={busy || !nextS} onClick={merge}>Merge with next</button>
                      <ConfirmButton className="sx-tool" disabled={busy} onConfirm={delScene}>Delete scene</ConfirmButton>
                    </div>
                  </>
                ) : (
                  <>
                    <div className="sx-row"><Label as="span">Scene</Label></div>
                    <span className="muted">No scene at {hms(at)}</span>
                    <div className="tags-row sx-tags">
                      {adding
                        ? <TagAdder current={[]} names={names} onAdd={addTag} onClose={() => setAdding(false)} />
                        : <button type="button" className="tag-add" onClick={() => setAdding(true)}>Tag this stretch</button>}
                    </div>
                  </>
                )}
              </div>

              <div className="sx-card sx-list">
                {person && (
                  <div className="sx-follow">
                    <Label as="span">Following</Label>
                    <b>{(cast.find((c) => keys.has(norm(c.name)) || keys.has(norm(c.character)))?.character) || person}</b>
                    <span className="mono sx-of">{pool.length}</span>
                    <span className="sx-grow" />
                    <button type="button" className="btn btn-sm" onClick={() => stepScene(-1, pool)}>Previous <span className="kbd">P</span></button>
                    <button type="button" className="btn btn-sm" onClick={() => stepScene(1, pool)}>Next <span className="kbd">N</span></button>
                    <button type="button" className="btn btn-sm btn-ghost" aria-label="Stop following" onClick={() => setPerson(null)}>×</button>
                  </div>
                )}
                <div className="sx-row sx-pad"><Label as="span">Scenes here</Label><span className="mono sx-range">{inWin.length} {inWin.length === 1 ? 'scene' : 'scenes'}</span></div>
                {inWin.length === 0 && <span className="tiny muted sx-pad">No scenes in this stretch</span>}
                {inWin.map((s) => (
                  <button key={s.id} type="button" className={`sx-srow ${scene?.id === s.id ? 'is-on' : ''} ${keys && !hasPerson(s, keys) ? 'is-dim' : ''}`} onClick={() => pick(s)}>
                    <span className="mono sx-st">{hms(s.start)}</span>
                    <span className="sx-sn">
                      <b>{s.name || 'No name yet'}</b>
                      <span className={s.ai.length || !s.kept.length ? 'is-ai' : ''}>{status(s)}</span>
                    </span>
                  </button>
                ))}
              </div>
            </aside>
          </div>

          <section className="sx-film" aria-label="The film, scene by scene">
            <div className="sx-filmhead">
              <span className="hd sx-ft">{f.title}</span>
              <span className="mono sx-of">{[f.year, hms(duration), `${scenes.length} scenes`].filter(Boolean).join(' · ')}</span>
              {topTags.length > 0 && <Label as="span" className="lab sx-showlab">Show scenes tagged</Label>}
              {topTags.map(([g, n]) => (
                <FilterChip key={g} on={tagF === g} count={n} onClick={() => patch({ tag: tagF === g ? null : g })}>{tagLabel(names, g)}</FilterChip>
              ))}
              <span className="sx-grow" />
              <span className="mono sx-of">{filtering ? `${lit.length} of ${scenes.length} scenes` : `${scenes.length} scenes`}</span>
            </div>

            <div className="sx-lrow">
              <div className="sx-lname sx-lname-film"><Label as="span">Whole film</Label></div>
              <div className="sx-lbody">
                <div className="sx-hours">
                  {hours.map((h, i) => <span key={h} className="mono" style={{ left: `${(h / D) * 100}%`, transform: i === 0 ? 'none' : 'translateX(-50%)' }}>{Math.floor(h / 3600)}:{String((h % 3600) / 60).padStart(2, '0')}</span>)}
                </div>
                <div className="sx-barcode" role="img" aria-label="Every scene in the film. Lit scenes have the tag you picked." onClick={filmAt}>
                  {base.map(([a, b]) => {
                    const over = scenes.filter((s) => s.end > a && s.start < b);
                    const on = filtering && over.some(matches);
                    return <span key={a} className={`${over.length ? 'is-tagged' : ''} ${on ? 'is-lit' : ''} ${filtering ? 'is-filter' : ''}`} style={{ left: `${(a / D) * 100}%`, width: `calc(${((b - a) / D) * 100}% - 1px)` }} />;
                  })}
                  <span className="sx-winmark" style={{ left: `${(win[0] / D) * 100}%`, width: `${((win[1] - win[0]) / D) * 100}%` }} />
                </div>
                <svg className="sx-funnel" viewBox="0 0 100 10" preserveAspectRatio="none" aria-hidden="true">
                  <polygon points={`${(win[0] / D) * 100},0 ${(win[1] / D) * 100},0 100,10 0,10`} />
                </svg>
              </div>
            </div>

            <div className="sx-lrow">
              <div className="sx-lname sx-lnames">
                <span className="sx-sp16" />
                <Label as="span" className="lab sx-l-scn">Scenes</Label>
                <Label as="span" className="lab sx-l-sp">Speech</Label>
              </div>
              <div className="sx-lbody">
                <Track fkey={key} win={win} scenes={scenes} sel={scene} edges={edgeInfo} lines={lines} quiet={silence} t={at}
                  names={names} keys={keys} onSeek={seek} onPick={pick} onEdge={setEdgeTo} />
              </div>
            </div>

            <div className="sx-legend">
              <span><i className="sx-lg-talk" />someone is speaking</span>
              {silence.length > 0 && <span><i className="sx-lg-quiet" />quiet, a clean place to cut</span>}
            </div>
          </section>
        </>
      )}
    </div>
  );
}
