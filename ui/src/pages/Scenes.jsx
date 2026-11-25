import { useCallback, useEffect, useMemo, useState } from 'react';
import { Link, useParams } from 'react-router';
import { api } from '../lib/api.js';
import { useAct, useLoad, usePoll } from '../lib/hooks.js';
import { fmtTime } from '../lib/time.js';
import { Empty, ErrorBox, Loading, Notice, Progress } from '../components/ui.jsx';
import { Crumbs, enc } from '../components/kit.jsx';

const q = (o) => new URLSearchParams(Object.entries(o).filter(([, v]) => v != null && v !== '')).toString();
export const frameUrl = (key, t, height = 240) => `/api/scenes/frame?${q({ key, t: t.toFixed(2), height })}`;
const sheetUrl = (key, start, end, cols = 4, rows = 2) =>
  `/api/scenes/sheet?${q({ key, start: start.toFixed(2), end: end.toFixed(2), cols, rows, height: 180 })}`;
const clipUrl = (key, start, end) => `/api/scenes/clip?${q({ key, start: start.toFixed(2), end: end.toFixed(2) })}`;

// The camera cut before/after t (for nudging edges).
export function prevCut(shots, t) {
  let best = 0;
  for (const [a] of shots) if (a < t - 0.05) best = a;
  return best;
}
export function nextCut(shots, t, duration) {
  for (const [, b] of shots) if (b > t + 0.05) return b;
  return duration;
}

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

// Full-film strip: automatic scenes, tags (by status) and dialogue ticks; click to move the playhead.
function Strip({ doc, tags, sel, t, onSeek }) {
  const dur = doc.duration || 1;
  const pct = (x) => `${(x / dur) * 100}%`;
  const click = (e) => {
    const r = e.currentTarget.getBoundingClientRect();
    onSeek(((e.clientX - r.left) / r.width) * dur);
  };
  const marks = [];
  for (let m = 0; m < dur; m += 600) marks.push(m);
  return (
    <div className="sc-strip" onClick={click} role="presentation">
      <div className="sc-lane" aria-label="Scenes">
        <span className="sc-lane-name">Scenes</span>
        {doc.scenes.map((s, i) => (
          <span key={s.id} className={`sc-blk ${i % 2 ? 'sc-alt' : ''}`} style={{ left: pct(s.start), width: pct(s.end - s.start) }} />
        ))}
      </div>
      <div className="sc-lane" aria-label="Tags">
        <span className="sc-lane-name">Tags</span>
        {tags.filter((x) => x.status !== 'rejected').map((x) => (
          <span key={x.id} title={`${x.tags.join(', ')} ${fmtTime(x.start)}–${fmtTime(x.end)}`}
            className={`sc-blk sc-tag-${x.status} ${sel?.id === x.id ? 'sc-sel' : ''}`}
            style={{ left: pct(x.start), width: `max(3px, ${pct(x.end - x.start)})` }} />
        ))}
      </div>
      <div className="sc-lane sc-lane-thin" aria-label="Dialogue">
        <span className="sc-lane-name">Dialogue</span>
        {doc.lines.map((l, i) => <span key={i} className="sc-tick" style={{ left: pct(l.start) }} />)}
      </div>
      {marks.map((m) => <span key={m} className="sc-mark" style={{ left: pct(m) }}>{fmtTime(m)}</span>)}
      <span className="sc-head" style={{ left: pct(t) }} />
    </div>
  );
}

function TagCard({ x, active, onPick, onSet, onDelete }) {
  return (
    <div className={`sc-card ${active ? 'sc-card-on' : ''}`} onClick={onPick} role="button" tabIndex={0}
      onKeyDown={(e) => { if (e.key === 'Enter') onPick(); }}>
      <div className="row" style={{ justifyContent: 'space-between' }}>
        <span className="mono tiny">{fmtTime(x.start)}–{fmtTime(x.end)} <span className="muted">({Math.round(x.end - x.start)}s)</span></span>
        <span className={`tag sc-st-${x.status}`}>{x.status}</span>
      </div>
      <div className="row wrap" style={{ gap: 4 }}>
        {x.tags.map((g) => <span key={g} className="chip">{g}</span>)}
      </div>
      {x.summary && <span className="tiny">{x.summary}</span>}
      {x.people.length > 0 && (
        <span className="tiny muted">{x.people.map((p) => p.character || p.name).join(', ')}</span>
      )}
      <span className="tiny dim">{x.source}{x.confidence != null ? ` · ${Math.round(x.confidence * 100)}%` : ''}</span>
      {active && (
        <div className="row wrap" style={{ gap: 6 }}>
          {x.status !== 'accepted' && <button type="button" className="btn btn-sm btn-primary" onClick={(e) => { e.stopPropagation(); onSet({ status: 'accepted' }); }}>Accept <kbd>A</kbd></button>}
          {x.status !== 'rejected' && <button type="button" className="btn btn-sm" onClick={(e) => { e.stopPropagation(); onSet({ status: 'rejected' }); }}>Reject <kbd>R</kbd></button>}
          <button type="button" className="btn btn-sm btn-ghost" onClick={(e) => { e.stopPropagation(); onDelete(); }}>Delete</button>
        </div>
      )}
    </div>
  );
}

function TagForm({ range, onSave, busy }) {
  const [tags, setTags] = useState('');
  const [people, setPeople] = useState('');
  const [summary, setSummary] = useState('');
  const save = (e) => {
    e.preventDefault();
    const list = tags.split(',').map((s) => s.trim()).filter(Boolean);
    if (!list.length) return;
    onSave({ tags: list, people: people.split(',').map((s) => s.trim()).filter(Boolean), summary });
    setTags(''); setPeople(''); setSummary('');
  };
  return (
    <form className="stack sc-form" style={{ gap: 8 }} onSubmit={save}>
      <span className="tiny muted">Tag <span className="mono">{fmtTime(range[0])}–{fmtTime(range[1])}</span> (the selected range)</span>
      <input className="input" aria-label="Tags" placeholder="Tags: christmas, great hall" value={tags} onChange={(e) => setTags(e.target.value)} />
      <input className="input" aria-label="People" placeholder="People: Harry, Ron" value={people} onChange={(e) => setPeople(e.target.value)} />
      <input className="input" aria-label="Summary" placeholder="What happens (optional)" value={summary} onChange={(e) => setSummary(e.target.value)} />
      <button type="submit" className="btn btn-sm" disabled={busy || !tags.trim()}>Save tag</button>
    </form>
  );
}

function AIForm({ onRun, busy, configured }) {
  const [tag, setTag] = useState('');
  const [desc, setDesc] = useState('');
  const [sweep, setSweep] = useState(false);
  return (
    <form className="stack sc-form" style={{ gap: 8 }} onSubmit={(e) => { e.preventDefault(); onRun({ tag, description: desc, sweep }); }}>
      <span className="tiny muted">The AI reads the dialogue, checks each hit on a frame grid and leaves proposals for you.</span>
      <input className="input" aria-label="Tag to find" placeholder="Tag: christmas" value={tag} onChange={(e) => setTag(e.target.value)} />
      <input className="input" aria-label="What it means" placeholder="Meaning: Christmas at Hogwarts, snow, presents, the feast" value={desc} onChange={(e) => setDesc(e.target.value)} />
      <label className="row tiny muted"><input type="checkbox" checked={sweep} onChange={(e) => setSweep(e.target.checked)} /> Also look at scenes with no dialogue (slower)</label>
      <button type="submit" className="btn btn-sm" disabled={busy || !tag.trim() || !configured}>Find with AI</button>
      {!configured && <span className="tiny c-warn">Set an AI model in <Link to="/settings#ai">Settings</Link> first.</span>}
    </form>
  );
}

function XRay({ fkey, t, cast }) {
  const { data } = useLoad(() => api(`/scenes/xray?${q({ key: fkey, t: Math.floor(t) })}`), [fkey, Math.floor(t / 2)]);
  const photo = Object.fromEntries(cast.map((c) => [c.name, c.photo]));
  return (
    <div className="stack" style={{ gap: 14 }}>
      <span className="tiny muted">On screen at <span className="mono">{fmtTime(t)}</span>, from the tags covering it</span>
      {!data?.people.length
        ? <span className="tiny dim">Nobody tagged here yet.</span>
        : data.people.map((p) => (
          <div key={p.name || p.character} className="row sc-person">
            {photo[p.name] ? <img src={photo[p.name]} alt="" /> : <span className="sc-noface" />}
            <div className="stack" style={{ gap: 2 }}>
              <b>{p.character || p.name}</b>
              {p.name && p.character && <span className="tiny muted">{p.name}</span>}
              {p.scenes?.length > 0 && <span className="tiny dim">{p.scenes.length} accepted scene{p.scenes.length === 1 ? '' : 's'} in this film</span>}
            </div>
          </div>
        ))}
      {cast.length > 0 && (
        <details>
          <summary className="tiny muted">Full cast ({cast.length})</summary>
          <div className="sc-cast">
            {cast.slice(0, 30).map((c) => (
              <div key={`${c.name}-${c.character}`} className="row sc-person">
                {c.photo ? <img src={c.photo} alt="" loading="lazy" /> : <span className="sc-noface" />}
                <div className="stack" style={{ gap: 0 }}><span className="tiny">{c.character}</span><span className="tiny muted">{c.name}</span></div>
              </div>
            ))}
          </div>
        </details>
      )}
    </div>
  );
}

export function ScenesPage() {
  const { key } = useParams();
  const { data, error, loading, reload } = useLoad(() => api(`/scenes/film?${q({ key })}`), [key]);
  const settings = useLoad(() => api('/settings').catch(() => null));
  const { act, msg, busy, clear } = useAct();
  const [t, setT] = useState(0);
  const [sel, setSel] = useState(null); // selected tag id
  const [range, setRange] = useState(null); // [start, end] being viewed
  const [tab, setTab] = useState('proposed');
  const [play, setPlay] = useState(false);
  const onDone = useCallback(() => { reload(); }, [reload]);
  const [job, setJob] = useJob(key, onDone);

  const doc = data?.index;
  const tags = useMemo(() => data?.tags || [], [data]);
  const proposals = tags.filter((x) => x.status === 'proposed');
  const current = tags.find((x) => x.id === sel) || null;
  const view = useMemo(() => (current ? [current.start, current.end] : range), [current, range]);

  const scenes = doc?.scenes;
  const sceneAt = useCallback((time) => (scenes || []).find((s) => s.start <= time && time < s.end), [scenes]);
  const seek = useCallback((time) => {
    setT(time);
    setSel(null);
    setPlay(false);
    const s = sceneAt(time);
    setRange(s ? [s.start, s.end] : [Math.max(0, time - 30), time + 30]);
  }, [sceneAt]);

  const pick = useCallback((x) => { setSel(x.id); setT(x.start); setRange(null); setPlay(false); }, []);
  const setTag = useCallback((x, changes) => act(async () => {
    await api(`/scenes/tags/${x.id}`, { method: 'PATCH', body: changes });
    await reload();
  }), [act, reload]);

  // Keys: A accept, R reject, J/K next/previous proposal, [ ] widen to the neighbouring cut,
  // Shift+[ ] narrow, Space play the range.
  useEffect(() => {
    const onKey = (e) => {
      if (e.target.closest('input, textarea, select')) return;
      const list = proposals.length ? proposals : tags;
      const i = list.findIndex((x) => x.id === sel);
      if (e.key === 'j' || e.key === 'k') {
        const n = list[Math.min(Math.max(i + (e.key === 'j' ? 1 : -1), 0), list.length - 1)];
        if (n) pick(n);
      } else if (current && (e.key === 'a' || e.key === 'r')) {
        setTag(current, { status: e.key === 'a' ? 'accepted' : 'rejected' });
        const n = proposals[proposals.findIndex((x) => x.id === current.id) + 1];
        if (n) pick(n);
      } else if (current && doc && (e.key === '[' || e.key === '{')) {
        const s = e.key === '{' ? nextCut(doc.shots, current.start, current.end) : prevCut(doc.shots, current.start);
        if (s < current.end - 1) setTag(current, { start: s });
      } else if (current && doc && (e.key === ']' || e.key === '}')) {
        const end = e.key === '}' ? prevCut(doc.shots, current.end) : nextCut(doc.shots, current.end, doc.duration);
        if (end > current.start + 1) setTag(current, { end });
      } else if (e.key === ' ' && view) {
        e.preventDefault();
        setPlay((p) => !p);
      } else return;
      e.preventDefault();
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [proposals, tags, sel, current, doc, view, pick, setTag]);

  if (error && !data) return <div className="page"><Crumbs items={[['Library', '/library'], [key]]} /><ErrorBox error={error} onRetry={reload} /></div>;
  if (loading || !data) return <div className="page"><Loading /></div>;

  const f = data.film;
  const startIndex = (transcribe = false) => act(async () => {
    setJob(await api('/scenes/index', { method: 'POST', body: { key, transcribe } }));
  });
  const runAI = (body) => act(async () => {
    setJob(await api('/scenes/ai', { method: 'POST', body: { key, ...body } }));
    setTab('proposed');
  }, 'Started. Proposals appear here as the job finishes.');
  const addTag = (body) => act(async () => {
    const x = await api('/scenes/tags', { method: 'POST', body: { key, start: view[0], end: view[1], ...body } });
    await reload();
    setSel(x.id);
  });
  const del = (x) => act(async () => {
    await api(`/scenes/tags/${x.id}`, { method: 'DELETE' });
    setSel(null);
    await reload();
  });
  const aiReady = !!settings.data?.ai?.model;
  const crumbs = [['Library', '/library'], ['Movies', '/library'], [f.title, `/movies/${enc(key)}`], ['Scenes']];
  const col = f.collections[0];

  const listed = tab === 'proposed' ? proposals : tab === 'tags' ? tags.filter((x) => x.status !== 'proposed') : [];
  const lines = doc && view ? doc.lines.filter((l) => l.end > view[0] - 5 && l.start < view[1] + 5) : [];

  return (
    <div className="page page-wide" style={{ gap: 20 }}>
      <Crumbs items={crumbs} />
      <div className="toolbar">
        <div className="stack" style={{ gap: 4 }}>
          <h1 className="display" style={{ fontSize: 26 }}>{f.title} <span className="muted" style={{ fontSize: 18 }}>{f.year}</span></h1>
          <span className="mono muted tiny">
            {doc
              ? `${doc.scenes.length} scenes · ${doc.shots.length} shots · subtitles: ${doc.subtitles.kind} (${doc.subtitles.lines} lines) · ${tags.length} tags`
              : 'Not indexed yet'}
          </span>
        </div>
        <div className="row wrap">
          {col && <Link className="btn" to={`/collections/${enc(col)}/scenes`}>{col}: all scenes</Link>}
          <button type="button" className="btn" disabled={busy || !f.on_disk} onClick={() => startIndex(false)}>
            {doc ? 'Index again' : 'Index scenes'}
          </button>
        </div>
      </div>
      <JobLine job={job} />
      <Notice msg={msg} onClose={clear} />

      {!doc ? (
        <Empty>
          Indexing reads the subtitles and finds every camera cut (a few minutes for a full film).
          {f.on_disk ? '' : ' This film has no file on disk.'}
          {doc === null && f.on_disk && (
            <> Films without text subtitles can be <button type="button" className="btn btn-sm btn-ghost" onClick={() => startIndex(true)}>transcribed</button> instead.</>
          )}
        </Empty>
      ) : (
        <div className="sc-grid">
          <div className="stack" style={{ gap: 14, minWidth: 0 }}>
            <div className="sc-stage">
              {view && play
                ? <video key={`${view[0]}-${view[1]}`} src={clipUrl(key, view[0], view[1])} controls autoPlay />
                : <img src={frameUrl(key, view ? view[0] + 0.5 : t, 480)} alt={`Frame at ${fmtTime(t)}`} />}
            </div>
            <div className="row wrap" style={{ justifyContent: 'space-between' }}>
              <span className="mono tiny">
                {view ? `${fmtTime(view[0])}–${fmtTime(view[1])} (${Math.round(view[1] - view[0])}s)` : fmtTime(t)}
                {current && <span className="muted"> · {current.tags.join(', ')}</span>}
              </span>
              <div className="row">
                {view && <button type="button" className="btn btn-sm" onClick={() => setPlay((p) => !p)}>{play ? 'Show frames' : 'Play range'} <kbd>Space</kbd></button>}
              </div>
            </div>
            <Strip doc={doc} tags={tags} sel={current} t={view ? view[0] : t} onSeek={seek} />
            {view && <img className="sc-sheet" src={sheetUrl(key, view[0], view[1])} alt="Frames across the range" />}
            {view && lines.length > 0 && (
              <div className="sc-lines">
                {lines.map((l, i) => (
                  <button type="button" key={i} className="sc-line" onClick={() => setT(l.start)}>
                    <span className="mono dim">{fmtTime(l.start)}</span> {l.text}
                  </button>
                ))}
              </div>
            )}
          </div>

          <aside className="sc-side">
            <div className="tabs" role="tablist">
              {[['proposed', `Proposals (${proposals.length})`], ['tags', 'Tags'], ['new', 'Add'], ['xray', 'X-Ray']].map(([id, label]) => (
                <button key={id} type="button" role="tab" aria-selected={tab === id} className={`tab ${tab === id ? 'active' : ''}`} onClick={() => setTab(id)}>{label}</button>
              ))}
            </div>
            {(tab === 'proposed' || tab === 'tags') && (
              <div className="stack" style={{ gap: 8 }}>
                {listed.length === 0
                  ? <span className="tiny muted">{tab === 'proposed' ? 'No proposals. Use Add to run the AI or tag a range yourself.' : 'No accepted or rejected tags yet.'}</span>
                  : listed.map((x) => (
                    <TagCard key={x.id} x={x} active={x.id === sel} onPick={() => pick(x)}
                      onSet={(c) => setTag(x, c)} onDelete={() => del(x)} />
                  ))}
                <span className="tiny dim">Keys: J/K next and previous, A accept, R reject, [ ] widen to the next cut, Shift+[ ] narrow.</span>
              </div>
            )}
            {tab === 'new' && (
              <div className="stack" style={{ gap: 20 }}>
                {view ? <TagForm range={view} onSave={addTag} busy={busy} /> : <span className="tiny muted">Click the strip to pick a scene, then tag it.</span>}
                <AIForm onRun={runAI} busy={busy} configured={aiReady} />
              </div>
            )}
            {tab === 'xray' && <XRay fkey={key} t={view ? view[0] + 1 : t} cast={data.cast} />}
          </aside>
        </div>
      )}
    </div>
  );
}
