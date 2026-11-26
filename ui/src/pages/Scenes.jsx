// The film scenes editor: the player and who is on screen at the top, a timeline with a lane
// per tag, the frames and the dialogue under it, and the film's tags (then the AI's
// proposals) on the side. `?person=` keeps you in the film and follows one actor;
// `?from&to&cut&seg` edits one segment of a cut (only those seconds are loaded).
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Link, useNavigate, useParams, useSearchParams } from 'react-router';
import { api } from '../lib/api.js';
import { useApi } from '../lib/store.js';
import { useAct, useLoad, usePageTitle, usePoll } from '../lib/hooks.js';
import { fmtTime } from '../lib/time.js';
import { flatSegments } from '../lib/timeline.js';
import { ConfirmButton, Empty, ErrorBox, Loading, Notice, Progress } from '../components/ui.jsx';
import { Crumbs, enc } from '../components/kit.jsx';
import {
  AtPlayhead, ClipVideo, TagLink, clipUrl, clipWindows, q, sceneFrameUrl, segmentEditUrl, sheetUrl, shownTags,
  tagLabel, useFacesAt, useTagNames, visibleRows,
} from '../components/ScenePlayer.jsx';

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

export const isAI = (x) => String(x?.source || '').startsWith('ai:');
// "claude-opus-5-5 · 70%" for the AI badge's hover.
export const aiTitle = (x) => [String(x.source || '').replace(/^ai:/, ''),
  x.confidence != null ? `${Math.round(x.confidence * 100)}%` : null].filter(Boolean).join(' · ');

const rank = (x) => (x.status === 'accepted' ? 2 : x.status === 'proposed' ? 1 : 0) + (x.confidence ?? 0.5) / 10;
const sameTags = (a, b) => {
  const x = [...shownTags(a)].sort().join('|');
  return x === [...shownTags(b)].sort().join('|');
};
const overlap = (a, b) => {
  const inter = Math.min(a.end, b.end) - Math.max(a.start, b.start);
  return inter / Math.max(Math.min(a.end - a.start, b.end - b.start), 0.001);
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

// Ruler step for a span of seconds.
const stepFor = (span) => [5, 10, 15, 30, 60, 120, 300, 600, 1200, 1800].find((s) => span / s <= 9) || 3600;

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
    <div className="row wrap sc-job">
      <span className="tag">{job.status}</span>
      <span className="muted tiny">{job.title}{job.message ? ` · ${job.message}` : ''}</span>
      {live && <Progress value={job.progress} />}
      {job.error && <span className="c-danger tiny">{job.error}</span>}
    </div>
  );
}

function AiBadge({ x }) {
  if (!isAI(x)) return null;
  return <span className="sc2-ai" title={aiTitle(x)}>AI</span>;
}

// The timeline: a ruler, the scenes (or shots) lane, one lane per tag in view, the dialogue.
// Click a lane to move the playhead; click a block to select its moment.
function Timeline({ win, base, lanes, lines, t, sel, keys, band, names, onSeek, onPick }) {
  const [v0, v1] = win;
  const span = Math.max(v1 - v0, 1);
  const pct = (x) => `${((x - v0) / span) * 100}%`;
  const wid = (a, b) => `${((Math.min(b, v1) - Math.max(a, v0)) / span) * 100}%`;
  const inView = (a, b) => b > v0 && a < v1;
  const seekAt = (e) => {
    const r = e.currentTarget.getBoundingClientRect();
    onSeek(v0 + ((e.clientX - r.left) / r.width) * span);
  };
  const step = stepFor(span);
  const marks = [];
  for (let m = Math.ceil(v0 / step) * step; m <= v1; m += step) if (m - v0 > span * 0.02 && v1 - m > span * 0.02) marks.push(m);
  const head = t >= v0 && t <= v1 ? pct(t) : null;
  const block = (x, lane) => {
    if (!inView(x.start, x.end)) return null;
    const hit = keys ? hasPerson(x, keys) : false;
    return (
      <button key={`${lane}-${x.id}`} type="button" tabIndex={-1}
        className={`sc2-blk sc2-${x.status} ${sel?.id === x.id ? 'sc2-on' : ''} ${keys ? (hit ? 'sc2-hit' : 'sc2-dim') : ''}`}
        style={{ left: pct(Math.max(x.start, v0)), width: `max(4px, ${wid(x.start, x.end)})` }}
        title={`${shownTags(x).map((g) => tagLabel(names, g)).join(', ')} · ${fmtTime(x.start)}–${fmtTime(x.end)}`}
        onClick={(e) => { e.stopPropagation(); onPick(x); }} />
    );
  };
  return (
    <div className="sc2-tl" aria-label="Timeline">
      <div className="sc2-row sc2-ruler">
        <span className="sc2-ln" />
        <div className="sc2-track" onClick={seekAt} role="presentation">
          {marks.map((m) => <span key={m} className="sc2-mark" style={{ left: pct(m) }}>{fmtTime(m)}</span>)}
        </div>
      </div>
      <div className="sc2-row">
        <span className="sc2-ln">{base.name}</span>
        <div className="sc2-track" onClick={seekAt} role="presentation">
          {base.blocks.filter(([a, b]) => inView(a, b)).map(([a, b], i) => (
            <span key={a} className={`sc2-base ${i % 2 ? 'sc2-alt' : ''}`} style={{ left: pct(Math.max(a, v0)), width: wid(a, b) }} />
          ))}
        </div>
      </div>
      <div className="sc2-lanes">
        {lanes.length === 0 && <div className="sc2-row"><span className="sc2-ln dim">No tags</span><div className="sc2-track" onClick={seekAt} role="presentation" /></div>}
        {lanes.map((l) => (
          <div key={l.tag} className="sc2-row">
            <span className="sc2-ln" title={l.name}>{l.name}</span>
            <div className="sc2-track" onClick={seekAt} role="presentation">{l.rows.map((x) => block(x, l.tag))}</div>
          </div>
        ))}
      </div>
      <div className="sc2-row sc2-thin">
        <span className="sc2-ln">Dialogue</span>
        <div className="sc2-track" onClick={seekAt} role="presentation">
          {lines.filter((l) => inView(l.start, l.end)).map((l, i) => <span key={i} className="sc2-tick" style={{ left: pct(l.start) }} />)}
        </div>
      </div>
      <div className="sc2-over" aria-hidden="true">
        {band && <span className="sc2-band" style={{ left: pct(Math.max(band[0], v0)), width: wid(band[0], band[1]) }} />}
        {head && <span className="sc2-head" style={{ left: head }} />}
      </div>
    </div>
  );
}

// Type-ahead over the vocab: Enter adds the highlighted tag (or a new one), Esc closes.
function TagPicker({ label, current, names, onAdd, onRemove, onClose }) {
  const vocab = useApi('/scenes/vocab', { quiet: true });
  const [text, setText] = useState('');
  const [i, setI] = useState(0);
  const all = useMemo(() => [...(vocab.data?.tags || []), ...(vocab.data?.cut_tags || [])]
    .map((x) => ({ id: x.id, name: x.name || tagLabel(names, x.id) })), [vocab.data, names]);
  const qy = norm(text);
  const hits = useMemo(() => {
    if (!qy) return all.filter((x) => !current.includes(x.id)).slice(0, 8);
    const ok = all.filter((x) => !current.includes(x.id) && norm(x.name).includes(qy));
    return ok.sort((a, b) => Number(!norm(a.name).startsWith(qy)) - Number(!norm(b.name).startsWith(qy))).slice(0, 8);
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
    <div className="sc2-pop" role="dialog" aria-label="Tags">
      <div className="row" style={{ justifyContent: 'space-between' }}>
        <span className="mono tiny muted">{label}</span>
        <button type="button" className="btn btn-sm btn-ghost" onClick={onClose} aria-label="Close">×</button>
      </div>
      {current.length > 0 && (
        <div className="row wrap" style={{ gap: 4 }}>
          {current.map((g) => (
            <span key={g} className="sc2-chip">
              {tagLabel(names, g)}
              <button type="button" aria-label={`Remove ${tagLabel(names, g)}`} onClick={() => onRemove(g)}>×</button>
            </span>
          ))}
        </div>
      )}
      <input className="input" autoFocus aria-label="Tag" placeholder="Tag" value={text}
        onChange={(e) => { setText(e.target.value); setI(0); }} onKeyDown={onKey} />
      <div className="sc2-opts" role="listbox">
        {opts.map(([id, name], j) => (
          <button key={id} type="button" role="option" aria-selected={j === i} className="sc2-opt"
            onMouseEnter={() => setI(j)} onClick={() => pick(id)}>{name}</button>
        ))}
      </div>
    </div>
  );
}

const KEYS = [
  ['Space', 'Play / stop'], ['J  ]', 'Next moment'], ['K  [', 'Previous moment'], ['A', 'Accept'], ['R', 'Reject'],
  ['T', 'Tag'], ['I  O', 'In / out to playhead'], ['← →', 'Playhead ±1 s'], ['Shift ← →', 'Previous / next cut'],
  ['N  P', 'Person: next / previous'], ['Esc', 'Clear selection'], ['?', 'Shortcuts'],
];
function Shortcuts({ open, onToggle }) {
  return (
    <span className="sc2-keys">
      <button type="button" className="btn btn-sm" aria-expanded={open} onClick={onToggle} aria-label="Keyboard shortcuts">?</button>
      {open && (
        <dl className="sc2-pop sc2-keylist">
          {KEYS.map(([k, v]) => <div key={k}><dt><kbd>{k}</kbd></dt><dd>{v}</dd></div>)}
        </dl>
      )}
    </span>
  );
}

// Matched faces on the still; the followed person's box stands out. Unmatched: nothing.
function FaceBoxes({ faces, keys }) {
  return (faces || []).map((f, i) => {
    const [x, y, w, h] = f.box || [];
    if (w == null || !f.name) return null;
    const me = keys && (keys.has(norm(f.name)) || keys.has(norm(f.character)));
    const who = f.character || f.name;
    return (
      <span key={i} className={`sc2-fb ${f.sure ? '' : 'sc2-fb-maybe'} ${me ? 'sc2-fb-me' : ''} ${keys && !me ? 'sc2-fb-other' : ''}`}
        style={{ left: `${x * 100}%`, top: `${y * 100}%`, width: `${w * 100}%`, height: `${h * 100}%` }}>
        <em>{f.sure ? who : `${who}?`}</em>
      </span>
    );
  });
}

// One moment in the list; the selected one opens its details and actions.
function Moment({ x, names, on, hideTag, onPick, onSet, onTag, onEdge, onDelete, onPerson }) {
  const tags = shownTags(x).filter((g) => g !== hideTag);
  return (
    <div className={`sc2-m ${on ? 'sc2-m-on' : ''} ${x.status === 'rejected' ? 'sc2-m-rej' : ''}`} data-on={on}
      onClick={onPick} role="button" tabIndex={0} onKeyDown={(e) => { if (e.key === 'Enter') onPick(); }}>
      <div className="sc2-m-top">
        <span className="mono tiny">{fmtTime(x.start)}–{fmtTime(x.end)}</span>
        <AiBadge x={x} />
        {x.status === 'rejected' && <span className="tag">rejected</span>}
      </div>
      {tags.length > 0 && (on
        ? <div className="row wrap" style={{ gap: 4 }}>{tags.map((g) => <TagLink key={g} id={g} names={names} />)}</div>
        : <span className="tiny sc2-names">{tags.map((g) => tagLabel(names, g)).join(' · ')}</span>)}
      {x.summary && <span className={`tiny muted ${on ? '' : 'sc2-clamp'}`}>{x.summary}</span>}
      {on && (
        <div className="stack" style={{ gap: 8 }} onClick={(e) => e.stopPropagation()} role="presentation">
          {x.people?.length > 0 && (
            <div className="row wrap" style={{ gap: 4 }}>
              {x.people.map((p) => {
                const who = typeof p === 'string' ? { name: p } : p;
                return (
                  <button key={`${who.name}-${who.character}`} type="button" className="sc2-who" onClick={() => onPerson(who.name || who.character)}>
                    {who.character || who.name}
                  </button>
                );
              })}
            </div>
          )}
          {x.evidence?.length > 0 && <span className="tiny muted">Seen: {x.evidence.join(', ')}</span>}
          {isAI(x) && <span className="tiny dim">{aiTitle(x)}</span>}
          <div className="row wrap" style={{ gap: 6 }}>
            {x.status !== 'accepted' && <button type="button" className="btn btn-sm btn-primary" onClick={() => onSet({ status: 'accepted' })}>Accept</button>}
            {x.status !== 'rejected' && <button type="button" className="btn btn-sm" onClick={() => onSet({ status: 'rejected' })}>Reject</button>}
            <button type="button" className="btn btn-sm" onClick={onTag}>Tags</button>
            <button type="button" className="btn btn-sm btn-ghost" onClick={() => onEdge('start')} title="Start at the playhead">In</button>
            <button type="button" className="btn btn-sm btn-ghost" onClick={() => onEdge('end')} title="End at the playhead">Out</button>
            <ConfirmButton className="btn btn-sm btn-ghost" onConfirm={onDelete}>Delete</ConfirmButton>
          </div>
        </div>
      )}
    </div>
  );
}

function AIForm({ onRun, busy, configured }) {
  const [tag, setTag] = useState('');
  const [desc, setDesc] = useState('');
  const [sweep, setSweep] = useState(false);
  return (
    <form className="stack sc-form" style={{ gap: 8 }} onSubmit={(e) => { e.preventDefault(); onRun({ tag, description: desc, sweep }); }}>
      <input className="input" aria-label="Tag to find" placeholder="Tag: christmas" value={tag} onChange={(e) => setTag(e.target.value)} />
      <input className="input" aria-label="What it means" placeholder="Meaning: Christmas at Hogwarts, snow, presents, the feast" value={desc} onChange={(e) => setDesc(e.target.value)} />
      <label className="row tiny muted"><input type="checkbox" checked={sweep} onChange={(e) => setSweep(e.target.checked)} /> Also scenes with no dialogue</label>
      <button type="submit" className="btn btn-sm" disabled={busy || !tag.trim() || !configured}>Find with AI</button>
      {!configured && <span className="tiny c-warn">Set an AI model in <Link to="/settings#ai">Settings</Link> first.</span>}
    </form>
  );
}

function XRay({ fkey, t, cast, onPerson }) {
  const { data } = useLoad(() => api(`/scenes/xray?${q({ key: fkey, t: Math.floor(t) })}`), [fkey, Math.floor(t / 2)]);
  const photo = Object.fromEntries(cast.map((c) => [c.name, c.photo]));
  return (
    <div className="stack" style={{ gap: 12 }}>
      {!data?.people.length
        ? <span className="tiny dim">Nobody tagged at {fmtTime(t)}</span>
        : data.people.map((p) => (
          <button key={p.name || p.character} type="button" className="row sc-person sc2-xp" onClick={() => onPerson(p.name || p.character)}>
            {photo[p.name] ? <img src={photo[p.name]} alt="" /> : <span className="sc-noface" />}
            <span className="stack" style={{ gap: 2 }}>
              <b>{p.character || p.name}</b>
              {p.name && p.character && <span className="tiny muted">{p.name}</span>}
            </span>
          </button>
        ))}
      {cast.length > 0 && (
        <details>
          <summary className="tiny muted">Cast {cast.length}</summary>
          <div className="sc-cast">
            {cast.slice(0, 40).map((c) => (
              <button key={`${c.name}-${c.character}`} type="button" className="row sc-person sc2-xp" onClick={() => onPerson(c.name)}>
                {c.photo ? <img src={c.photo} alt="" loading="lazy" /> : <span className="sc-noface" />}
                <span className="stack" style={{ gap: 0 }}><span className="tiny">{c.character}</span><span className="tiny muted">{c.name}</span></span>
              </button>
            ))}
          </div>
        </details>
      )}
    </div>
  );
}

// The person being followed: who, their moments here as jump buttons, previous/next, clear.
function PersonBar({ person, cast, moments, t, onJump, onClear }) {
  const strip = useRef(null);
  useEffect(() => {
    strip.current?.querySelector('.sc2-pmb-on')?.scrollIntoView({ block: 'nearest', inline: 'center' });
  }, [t]);
  const keys = personKeys(person, cast);
  const c = (cast || []).find((x) => keys.has(norm(x.name)) || keys.has(norm(x.character)));
  const nextOf = (dir) => (dir > 0 ? moments.find((m) => m.start > t + 0.5) : [...moments].reverse().find((m) => m.start < t - 0.5));
  return (
    <div className="sc2-person" aria-label="Person">
      {c?.photo ? <img src={c.photo} alt="" /> : <span className="sc-noface" />}
      <span className="stack" style={{ gap: 0, minWidth: 0 }}>
        <b>{c?.character || person}</b>
        {c?.character && <span className="tiny muted">{c.name}</span>}
      </span>
      <span className="mono tiny muted">{moments.length}</span>
      <button type="button" className="btn btn-sm" disabled={!nextOf(-1)} onClick={() => onJump(nextOf(-1))} aria-label="Previous moment with them">‹ <kbd>P</kbd></button>
      <button type="button" className="btn btn-sm" disabled={!nextOf(1)} onClick={() => onJump(nextOf(1))} aria-label="Next moment with them"><kbd>N</kbd> ›</button>
      <div className="sc2-pm" ref={strip}>
        {moments.map((m) => (
          <button key={m.id} type="button" className={`sc2-pmb ${m.start <= t && t < m.end ? 'sc2-pmb-on' : ''}`}
            onClick={() => onJump(m)} title={m.summary || undefined}>{fmtTime(m.start)}</button>
        ))}
      </div>
      <button type="button" className="btn btn-sm btn-ghost" onClick={onClear} aria-label="Stop following">×</button>
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

export function ScenesPage() {
  const { key } = useParams();
  const navigate = useNavigate();
  // Everything that makes a place lives in the URL, so a reload or a link opens it again.
  const [params, setParams] = useSearchParams();
  const t = Number(params.get('t')) || 0;
  const sel = params.get('scene') || null;
  const tab = params.get('tab') || 'tags';
  const person = params.get('person') || '';
  const tagF = params.get('tag') || '';
  const aiOnly = params.get('ai') === '1';
  const showRej = params.get('rejected') === '1';
  const byTime = params.get('view') === 'time';
  const cut = params.get('cut') || '';
  const segN = Number(params.get('seg')) || 0;
  const from = Number(params.get('from'));
  const to = Number(params.get('to'));
  const segMode = !!cut && segN > 0 && Number.isFinite(from) && to > from;
  const patch = useCallback((o) => setParams((p) => {
    const n = new URLSearchParams(p);
    for (const [k, v] of Object.entries(o)) {
      if (v == null || v === '' || v === false || (k === 'tab' && v === 'tags')) n.delete(k);
      else n.set(k, k === 't' || k === 'from' || k === 'to' ? String(Math.round(v * 1000) / 1000) : v === true ? '1' : v);
    }
    return n;
  }, { replace: true }), [setParams]);

  // segment mode loads only the window's shots and lines, never the whole film's
  const [w0, w1] = segMode ? segWindow(from, to) : [0, 0];
  const opened = useRef(null);
  const { data, error, loading, reload } = useLoad(async () => {
    if (!segMode) return api(`/scenes/film?${q({ key })}`);
    const range = q({ key, start: w0, end: w1 });
    const [films, tags, cast, shots, lines] = await Promise.all([
      api('/scenes/films'), api(`/scenes/tags?${q({ key })}`),
      api(`/scenes/cast?${q({ key })}`).catch(() => ({ cast: [] })),
      api(`/scenes/shots?${range}`).catch(() => ({ shots: [] })),
      api(`/scenes/transcript?${range}`).catch(() => ({ lines: [] })),
    ]);
    const f = (films.films || []).find((x) => x.key === key);
    if (!f) throw new Error('This film is not in your library');
    return {
      film: { ...f, collections: f.collections || [] },
      index: { duration: f.duration, shots: shots.shots || [], lines: lines.lines || [], scenes: [] },
      tags: tags.tags || [], cast: cast.cast || [], films: films.films || [],
    };
  }, [key, segMode, w0, w1]);
  const tl = useApi(segMode ? `/timelines/${enc(cut)}` : null);

  // open on the person's first moment, else the first tag, else the first proposal
  useEffect(() => {
    if (!data || opened.current === key || segMode) return;
    opened.current = key;
    const now = new URLSearchParams(window.location.search);
    if (now.get('scene') || now.get('t')) return;
    const all = dedupeMoments(visibleRows(data.tags)).filter((x) => x.status !== 'rejected');
    const keys = personKeys(now.get('person'), data.cast);
    const mine = keys ? all.filter((x) => hasPerson(x, keys)) : all;
    const first = mine.find((x) => x.status === 'accepted') || mine[0] || all[0];
    if (first) patch({ scene: first.id, t: first.start, tab: first.status === 'proposed' ? 'proposed' : null });
  }, [data, key, segMode, patch]);

  const settings = useLoad(() => api('/settings').catch(() => null));
  const { act, msg, busy, clear } = useAct();
  const [play, setPlay] = useState(false);
  const [chunk, setChunk] = useState(0);
  const [clipT, setClipT] = useState(null);
  const [running, setRunning] = useState(false);
  const [picker, setPicker] = useState(false);
  const [keysOpen, setKeysOpen] = useState(false);
  const [open, setOpen] = useState(() => new Set());
  const [zoom, setZoom] = useState(false);
  const names = useTagNames();
  usePageTitle(data?.film ? `${data.film.title} scenes` : null);
  const onDone = useCallback(() => { reload(); }, [reload]);
  const [job, setJob] = useJob(key, onDone);

  const doc = data?.index;
  const duration = doc?.duration || data?.film?.duration || 0;
  const cast = useMemo(() => data?.cast || [], [data]);
  const keys = useMemo(() => personKeys(person, cast), [person, cast]);

  // the cut's segment (segment mode): the recipe is the truth once loaded
  const recipe = tl.data?.doc;
  const segs = useMemo(() => flatSegments(recipe).segs, [recipe]);
  const segHit = segMode ? segs[segN - 1] : null;
  const segA = segMode ? segHit?.seg.start ?? from : null;
  const segB = segMode ? segHit?.seg.end ?? to : null;
  const segRange = useMemo(() => (segA == null ? null : [segA, segB]), [segA, segB]);
  const winB = segMode ? Math.min(w1, duration || w1) : null;
  const win = useMemo(() => (winB == null ? null : [w0, winB]), [w0, winB]);

  const inWin = useCallback((x) => !win || (x.end > win[0] && x.start < win[1]), [win]);
  const all = useMemo(() => dedupeMoments(visibleRows(data?.tags)), [data]);
  const passes = useCallback((x) => inWin(x) && (!tagF || shownTags(x).includes(tagF)) && (!aiOnly || isAI(x))
    && (showRej || x.status !== 'rejected'), [inWin, tagF, aiOnly, showRej]);
  const onLine = useMemo(() => all.filter(passes), [all, passes]); // timeline: person highlights, not filters
  const shown = useMemo(() => (keys ? onLine.filter((x) => hasPerson(x, keys)) : onLine), [onLine, keys]);
  const accepted = shown.filter((x) => x.status === 'accepted' || (showRej && x.status === 'rejected'));
  const proposed = shown.filter((x) => x.status === 'proposed' || (showRej && x.status === 'rejected'));
  const listed = tab === 'proposed' ? proposed : accepted;
  const personMoments = useMemo(() => (keys ? all.filter((x) => inWin(x) && x.status !== 'rejected' && hasPerson(x, keys)) : []), [all, keys, inWin]);
  const selected = all.find((x) => x.id === sel) || null;
  const current = selected || listed.find((x) => x.start <= t && t < x.end) || null;

  const scenes = doc?.scenes;
  const sceneAt = useCallback((time) => (scenes || []).find((s) => s.start <= time && time < s.end), [scenes]);
  // the range Space plays and T tags: the segment, else the selected moment, else the scene at t
  const view = useMemo(() => {
    if (segRange) return segRange;
    if (selected) return [selected.start, selected.end];
    const s = sceneAt(t);
    return s ? [s.start, s.end] : [Math.max(0, t - 30), Math.min(duration || t + 30, t + 30)];
  }, [segRange, selected, sceneAt, t, duration]);
  const windows = useMemo(() => clipWindows(view[0], view[1]), [view]);
  const still = segMode && !params.get('t') ? view[0] + 0.3 : t;
  const at = play && clipT != null ? clipT : still;
  const faces = useFacesAt(doc ? key : null, at, play && running);

  const stop = useCallback(() => { setPlay(false); setClipT(null); setRunning(false); setChunk(0); }, []);
  const togglePlay = useCallback(() => {
    if (play && clipT != null) patch({ t: clipT });
    if (play) stop(); else { setPlay(true); setChunk(0); setClipT(null); }
  }, [play, clipT, patch, stop]);
  const seek = useCallback((time) => {
    const lo = win ? win[0] : 0;
    const hi = win ? win[1] : duration || time;
    patch({ t: Math.min(Math.max(time, lo), hi) });
    stop();
  }, [patch, stop, win, duration]);
  const pick = useCallback((x) => {
    patch({ scene: x.id, t: Math.max(x.start, win ? win[0] : 0) });
    stop();
    setPicker(false);
  }, [patch, stop, win]);
  const setPerson = useCallback((name) => patch({ person: name || null }), [patch]);

  const setTag = useCallback((x, changes) => act(async () => {
    for (const id of [x.id, ...(changes.status ? x.dups || [] : [])]) {
      await api(`/scenes/tags/${id}`, { method: 'PATCH', body: changes });
    }
    await reload();
    return true;
  }), [act, reload]);
  const setEdge = useCallback((x, edge) => {
    if (edge === 'start' && at >= x.end - 0.5) return;
    if (edge === 'end' && at <= x.start + 0.5) return;
    setTag(x, { [edge]: r3(at) });
  }, [at, setTag]);
  const del = (x) => act(async () => {
    await api(`/scenes/tags/${x.id}`, { method: 'DELETE' });
    patch({ scene: null });
    await reload();
  });
  // T: retag the selected moment, else tag the range as a new accepted moment
  const addTagTo = (id) => act(async () => {
    if (selected) {
      await api(`/scenes/tags/${selected.id}`, { method: 'PATCH', body: { tags: [...new Set([...selected.tags, id])] } });
      await reload();
    } else {
      const x = await api('/scenes/tags', { method: 'POST', body: { key, start: r3(view[0]), end: r3(view[1]), tags: [id], status: 'accepted' } });
      await reload();
      if (x?.id) patch({ scene: x.id });
    }
  });
  const removeTag = (id) => {
    if (!selected) return;
    const rest = selected.tags.filter((g) => g !== id);
    if (!rest.some((g) => shownTags({ tags: [g] }).length)) return; // a moment keeps one tag at least
    setTag(selected, { tags: rest });
  };

  // segment mode: trim the cut's segment and save the recipe
  const trim = useCallback((start, end) => act(async () => {
    const fresh = await api(`/timelines/${enc(cut)}`);
    const next = trimSegment(fresh.doc, segN, start, end, duration);
    await api(`/timelines/${enc(cut)}`, { method: 'PUT', body: next });
    await tl.reload();
    const s = flatSegments(next).segs[segN - 1].seg;
    patch({ from: s.start, to: s.end });
  }), [act, cut, segN, duration, tl, patch]);
  const segEdge = useCallback((edge, value) => {
    if (!segRange) return;
    trim(edge === 'start' ? value : segRange[0], edge === 'end' ? value : segRange[1]);
  }, [segRange, trim]);
  const goSeg = (n) => {
    const s = segs[n - 1];
    const f = s && filmForSource(recipe, s.seg.src, data?.films);
    if (f) navigate(segmentEditUrl(f.key, s.seg, cut, n));
  };

  // the moment list in time order, for J/K
  const order = useMemo(() => [...listed].sort((a, b) => a.start - b.start), [listed]);
  const step = useCallback((dir) => {
    const i = order.findIndex((x) => x.id === sel);
    const n = i >= 0 ? order[i + dir]
      : dir > 0 ? order.find((x) => x.start > t + 0.1) : [...order].reverse().find((x) => x.start < t - 0.1);
    if (n) pick(n);
  }, [order, sel, t, pick]);
  const personStep = useCallback((dir) => {
    const n = dir > 0 ? personMoments.find((m) => m.start > t + 0.5) : [...personMoments].reverse().find((m) => m.start < t - 0.5);
    if (n) pick(n);
  }, [personMoments, t, pick]);

  useEffect(() => {
    const onKey = (e) => {
      if (e.target.closest?.('input, textarea, select, video') || e.ctrlKey || e.metaKey || e.altKey) return;
      const k = e.key.length === 1 ? e.key.toLowerCase() : e.key;
      if (k === 'j' || k === ']') step(1);
      else if (k === 'k' || k === '[') step(-1);
      else if (k === 'n' && keys) personStep(1);
      else if (k === 'p' && keys) personStep(-1);
      else if ((k === 'a' || k === 'r') && current) {
        const advance = tab === 'proposed' && current.status === 'proposed';
        setTag(current, { status: k === 'a' ? 'accepted' : 'rejected' }).then((ok) => { if (ok && advance) step(1); });
      } else if (k === 't') setPicker(true);
      else if ((k === 'i' || k === 'o') && segMode) segEdge(k === 'i' ? 'start' : 'end', at);
      else if ((k === 'i' || k === 'o') && selected) setEdge(selected, k === 'i' ? 'start' : 'end');
      else if (k === 'ArrowLeft' || k === 'ArrowRight') {
        const dir = k === 'ArrowLeft' ? -1 : 1;
        if (e.shiftKey && doc) seek(dir < 0 ? prevCut(doc.shots, at) : nextCut(doc.shots, at, duration));
        else seek(at + dir);
      } else if (k === 'Escape') { setPicker(false); setKeysOpen(false); patch({ scene: null }); } else if (k === '?') setKeysOpen((v) => !v);
      else if (k === ' ') togglePlay();
      else return;
      e.preventDefault();
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [step, personStep, keys, current, selected, tab, setTag, setEdge, segMode, segEdge, at, doc, duration, seek, patch, togglePlay]);

  // keep the selected moment in view in the side list, and its group open
  const listRef = useRef(null);
  useEffect(() => {
    listRef.current?.querySelector('[data-on="true"]')?.scrollIntoView({ block: 'nearest' });
  }, [sel, tab, byTime]);

  if (error && !data) return <div className="page"><Crumbs items={[['Library', '/library'], [key]]} /><ErrorBox error={error} onRetry={reload} /></div>;
  if (loading || !data) return <div className="page"><Loading /></div>;

  const f = data.film;
  const startIndex = (transcribe = false) => act(async () => {
    setJob(await api('/scenes/index', { method: 'POST', body: { key, transcribe } }));
  });
  const runAI = (body) => act(async () => {
    setJob(await api('/scenes/ai', { method: 'POST', body: { key, ...body } }));
    patch({ tab: 'proposed' });
  }, 'Started. Proposals appear as the job finishes.');
  const aiReady = !!settings.data?.ai?.model;
  const crumbs = segMode
    ? [['My cuts', '/my-cuts'], [tl.data?.title || cut, `/cuts/${enc(cut)}`], [`Scene ${segN}`]]
    : [['Library', '/library'], ['Movies', '/library'], [f.title, `/movies/${enc(key)}`], ['Scenes']];
  const col = f.collections?.[0];
  const hrefFor = (name) => `?${(() => { const n = new URLSearchParams(params); n.set('person', name); return n; })()}`;

  // timeline window and lanes
  const tlWin = win || (zoom ? [Math.max(0, at - 300), Math.min(duration, Math.max(0, at - 300) + 600)] : [0, duration || 1]);
  const lanes = tagGroups(onLine.filter((x) => x.end > tlWin[0] && x.start < tlWin[1]), names);
  const base = segMode
    ? { name: 'Shots', blocks: doc.shots }
    : { name: 'Scenes', blocks: (doc?.scenes || []).map((s) => [s.start, s.end]) };
  const lineWin = segMode ? win : [view[0] - 5, view[1] + 5];
  const lines = (doc?.lines || []).filter((l) => l.end > lineWin[0] && l.start < lineWin[1]);
  const lineAt = lines.find((l) => l.start <= at && at < l.end);
  const groups = tagGroups(listed, names);
  const holds = (g) => !!selected && g.rows.some((x) => x.id === selected.id);
  // the selected moment's first group opens by itself unless one of its groups is open already
  const autoTag = groups.some((g) => open.has(g.tag) && holds(g)) ? null : groups.find(holds)?.tag;
  const isOpen = (g) => open.has(g.tag) || tagF === g.tag || g.tag === autoTag;
  const toggleGroup = (tag) => setOpen((s) => { const n = new Set(s); if (n.has(tag)) n.delete(tag); else n.add(tag); return n; });
  const tagOpts = tagGroups(onLine, names);
  const peopleOpts = (() => {
    const m = new Map();
    for (const x of onLine) for (const p of x.people || []) {
      const who = typeof p === 'string' ? { name: p } : p;
      const id = who.name || who.character;
      if (id && !m.has(norm(id))) m.set(norm(id), { id, label: who.character ? `${who.character} (${who.name || ''})`.replace(' ()', '') : id });
    }
    return [...m.values()].sort((a, b) => a.label.localeCompare(b.label));
  })();
  const momentProps = (x, hideTag) => ({
    x, names, hideTag, on: x.id === sel, onPick: () => pick(x), onSet: (c) => setTag(x, c),
    onTag: () => { pick(x); setPicker(true); }, onEdge: (edge) => setEdge(x, edge), onDelete: () => del(x), onPerson: setPerson,
  });
  const segFilm = (n) => { const s = segs[n - 1]; return s && filmForSource(recipe, s.seg.src, data.films); };

  return (
    <div className="page page-wide sc2" style={{ gap: 14 }}>
      <Crumbs items={crumbs} />
      <div className="toolbar scp-bar">
        {doc ? <AtPlayhead fkey={key} t={at} playing={play && running} rows={all} names={names} faces={faces} hrefFor={hrefFor} person={person} /> : <span />}
        <div className="row wrap">
          {segMode ? (
            <>
              <button type="button" className="btn btn-sm" disabled={!segFilm(segN - 1)} onClick={() => goSeg(segN - 1)}>‹ Scene {segN - 1 || ''}</button>
              <button type="button" className="btn btn-sm" disabled={!segFilm(segN + 1)} onClick={() => goSeg(segN + 1)}>Scene {segN + 1} ›</button>
              <Link className="btn btn-sm" to={`/cuts/${enc(cut)}`}>Back to the cut</Link>
            </>
          ) : (
            <>
              {col && <Link className="btn btn-sm" to={`/collections/${enc(col.id)}/scenes`}>{col.name}</Link>}
              <button type="button" className="btn btn-sm" disabled={busy || !f.on_disk} onClick={() => startIndex(false)}>
                {doc ? 'Index again' : 'Index scenes'}
              </button>
            </>
          )}
          <Shortcuts open={keysOpen} onToggle={() => setKeysOpen((v) => !v)} />
        </div>
      </div>
      <JobLine job={job} />
      <Notice msg={msg} onClose={clear} />
      {person && <PersonBar person={person} cast={cast} moments={personMoments} t={at} onJump={pick} onClear={() => setPerson(null)} />}

      {!doc ? (
        <Empty>
          Indexing reads the subtitles and finds every camera cut.
          {f.on_disk ? '' : ' This film has no file on disk.'}
          {doc === null && f.on_disk && (
            <> Films without text subtitles can be <button type="button" className="btn btn-sm btn-ghost" onClick={() => startIndex(true)}>transcribed</button> instead.</>
          )}
        </Empty>
      ) : (
        <div className="sc2-grid">
          <div className="stack sc2-main">
            <div className="sc2-stage">
              <div className="sc2-frame" style={{ '--ar': (doc.width && doc.height) ? doc.width / doc.height : 16 / 9 }}>
                {play
                  ? <ClipVideo src={clipUrl(key, windows[Math.min(chunk, windows.length - 1)][0], windows[Math.min(chunk, windows.length - 1)][1])}
                    offset={windows[Math.min(chunk, windows.length - 1)][0]} onTime={setClipT} onRunning={setRunning}
                    onEnded={() => { if (chunk + 1 < windows.length) { setChunk(chunk + 1); setClipT(null); } else { patch({ t: view[1] - 0.1 }); stop(); } }} />
                  : <img src={sceneFrameUrl(key, Math.max(0, still), 480)} alt={`Frame at ${fmtTime(still)}`} />}
                {!play && <FaceBoxes faces={faces} keys={keys} />}
              </div>
            </div>

            <div className="sc2-bar">
              <button type="button" className="btn btn-sm" onClick={togglePlay}>{play ? 'Stop' : 'Play'} <kbd>Space</kbd></button>
              <span className="mono tiny">{fmtTime(at)} <span className="muted">· {fmtTime(view[0])}–{fmtTime(view[1])}</span></span>
              <span className="sc2-grow" />
              {current && (
                <span className="row wrap sc2-cur" style={{ gap: 6 }}>
                  <span className={`tag sc-st-${current.status}`}>{current.status}</span>
                  <AiBadge x={current} />
                  <button type="button" className="btn btn-sm" disabled={current.status === 'accepted'} onClick={() => setTag(current, { status: 'accepted' })}>Accept <kbd>A</kbd></button>
                  <button type="button" className="btn btn-sm" disabled={current.status === 'rejected'} onClick={() => setTag(current, { status: 'rejected' })}>Reject <kbd>R</kbd></button>
                </span>
              )}
              <span className="sc2-anchor">
                <button type="button" className="btn btn-sm" onClick={() => setPicker((v) => !v)}>Tag <kbd>T</kbd></button>
                {picker && (
                  <TagPicker names={names}
                    label={selected ? `${fmtTime(selected.start)}–${fmtTime(selected.end)}` : `New · ${fmtTime(view[0])}–${fmtTime(view[1])}`}
                    current={selected ? shownTags(selected) : []} onAdd={addTagTo} onRemove={removeTag} onClose={() => setPicker(false)} />
                )}
              </span>
              {selected && !segMode && (
                <>
                  <button type="button" className="btn btn-sm btn-ghost" onClick={() => setEdge(selected, 'start')} title="Start at the playhead">In <kbd>I</kbd></button>
                  <button type="button" className="btn btn-sm btn-ghost" onClick={() => setEdge(selected, 'end')} title="End at the playhead">Out <kbd>O</kbd></button>
                </>
              )}
              {!segMode && (
                <div className="seg" role="radiogroup" aria-label="Zoom">
                  <button type="button" role="radio" aria-checked={!zoom} onClick={() => setZoom(false)}>Film</button>
                  <button type="button" role="radio" aria-checked={zoom} onClick={() => setZoom(true)}>10 min</button>
                </div>
              )}
            </div>

            {segMode && segRange && (
              <div className="sc2-bar sc2-trim">
                {[['start', 'In', 'I'], ['end', 'Out', 'O']].map(([edge, label, kb]) => {
                  const v = edge === 'start' ? segRange[0] : segRange[1];
                  return (
                    <span key={edge} className="row" style={{ gap: 4 }}>
                      <span className="tiny muted">{label}</span>
                      <button type="button" className="btn btn-sm" disabled={busy} onClick={() => segEdge(edge, v - 0.5)} aria-label={`${label} back half a second`}>−</button>
                      <span className="mono">{fmtTime(v, 1)}</span>
                      <button type="button" className="btn btn-sm" disabled={busy} onClick={() => segEdge(edge, v + 0.5)} aria-label={`${label} forward half a second`}>+</button>
                      <button type="button" className="btn btn-sm btn-ghost" disabled={busy} onClick={() => segEdge(edge, at)}>At playhead <kbd>{kb}</kbd></button>
                    </span>
                  );
                })}
                <span className="mono tiny muted">{fmtTime(segRange[1] - segRange[0], 1)}</span>
              </div>
            )}

            <Timeline win={tlWin} base={base} lanes={lanes} lines={doc.lines || []} t={at} sel={selected} keys={keys}
              band={segRange || (selected ? [selected.start, selected.end] : null)} names={names} onSeek={seek} onPick={pick} />

            <div className="sc2-under">
              <div className="sc2-pane">
                <img className="sc-sheet" src={sheetUrl(key, view[0], view[1])} alt="Frames across the range" />
              </div>
              <div className="sc2-pane sc2-lines">
                {lines.length === 0 ? <span className="tiny dim">No dialogue</span> : lines.map((l, i) => (
                  <button type="button" key={i} className={`sc-line ${l === lineAt ? 'sc2-line-on' : ''}`} onClick={() => seek(l.start)}>
                    <span className="mono dim">{fmtTime(l.start)}</span> {l.text}
                  </button>
                ))}
              </div>
            </div>
          </div>

          <aside className="sc-side sc2-side">
            <div className="tabs" role="tablist">
              {[['tags', 'Tags', accepted.length], ['proposed', 'Proposals', proposed.length], ['xray', 'X-Ray'], ['find', 'Find']].map(([id, label, n]) => (
                <button key={id} type="button" role="tab" aria-selected={tab === id} className={`tab ${tab === id ? 'active' : ''}`}
                  onClick={() => patch({ tab: id })}>{label}{n != null && <span className="sc2-n">{n}</span>}</button>
              ))}
            </div>
            {(tab === 'tags' || tab === 'proposed') && (
              <>
                <div className="sc2-filters">
                  <div className="seg" role="radiogroup" aria-label="Group">
                    <button type="button" role="radio" aria-checked={!byTime} onClick={() => patch({ view: null })}>By tag</button>
                    <button type="button" role="radio" aria-checked={byTime} onClick={() => patch({ view: 'time' })}>By time</button>
                  </div>
                  <select className="input sc2-sel" aria-label="Tag" value={tagF} onChange={(e) => patch({ tag: e.target.value })}>
                    <option value="">All tags</option>
                    {tagOpts.map((g) => <option key={g.tag} value={g.tag}>{g.name} ({g.rows.length})</option>)}
                  </select>
                  <select className="input sc2-sel" aria-label="Person" value={person} onChange={(e) => setPerson(e.target.value)}>
                    <option value="">Anyone</option>
                    {person && !peopleOpts.some((p) => norm(p.id) === norm(person)) && <option value={person}>{person}</option>}
                    {peopleOpts.map((p) => <option key={p.id} value={p.id}>{p.label}</option>)}
                  </select>
                  <button type="button" className={`sc2-toggle ${aiOnly ? 'on' : ''}`} aria-pressed={aiOnly} onClick={() => patch({ ai: !aiOnly })}>AI</button>
                  <button type="button" className={`sc2-toggle ${showRej ? 'on' : ''}`} aria-pressed={showRej} onClick={() => patch({ rejected: !showRej })}>Rejected</button>
                </div>
                <div className="sc2-list" ref={listRef}>
                  {listed.length === 0 && <span className="tiny muted">{tab === 'proposed' ? 'No proposals' : 'No tags'}</span>}
                  {byTime
                    ? order.map((x) => <Moment key={x.id} {...momentProps(x)} />)
                    : groups.map((g) => (
                      <div key={g.tag} className="sc2-group">
                        <button type="button" className="sc2-gh" aria-expanded={!!isOpen(g)} onClick={() => toggleGroup(g.tag)}>
                          <span className="sc2-caret">{isOpen(g) ? '▾' : '▸'}</span>
                          <b>{g.name}</b><span className="sc2-n">{g.rows.length}</span>
                        </button>
                        {isOpen(g) && <div className="stack" style={{ gap: 4 }}>{g.rows.map((x) => <Moment key={x.id} {...momentProps(x, g.tag)} />)}</div>}
                      </div>
                    ))}
                </div>
              </>
            )}
            {tab === 'xray' && <XRay fkey={key} t={at} cast={cast} onPerson={setPerson} />}
            {tab === 'find' && <AIForm onRun={runAI} busy={busy} configured={aiReady} />}
          </aside>
        </div>
      )}
    </div>
  );
}

