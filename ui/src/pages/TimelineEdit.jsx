import { useCallback, useMemo, useState } from 'react';
import { Link, useParams } from 'react-router';
import { api, seriesPath } from '../lib/api.js';
import { useAct, useLoad, usePoll } from '../lib/hooks.js';
import { fmtTime, parseTime } from '../lib/time.js';
import { Dialog, ErrorBox, Loading, Notice, PageHeader, Progress, StatusTag } from '../components/ui.jsx';
import { downloadJson, recipeFileName } from '../components/Recipe.jsx';

const ACTIVE = ['queued', 'running'];

function TimeInput({ value, onCommit, label }) {
  const [text, setText] = useState(null);
  const shown = text ?? fmtTime(value, 1);
  const commit = () => {
    if (text == null) return;
    const v = parseTime(text);
    setText(null);
    if (v != null) onCommit(v);
  };
  return (
    <input className="input input-time" value={shown} aria-label={label}
      onChange={(e) => setText(e.target.value)} onBlur={commit}
      onKeyDown={(e) => e.key === 'Enter' && e.currentTarget.blur()} />
  );
}

function move(list, from, to) {
  const next = [...list];
  const [x] = next.splice(from, 1);
  next.splice(Math.max(0, Math.min(to, next.length)), 0, x);
  return next;
}

function SyncReport({ report, onPreview }) {
  if (!report?.synced) return null;
  const sources = Object.entries(report.sources || {});
  return (
    <div className="card" style={{ marginBottom: 14 }}>
      <h3>Sync report</h3>
      <table className="table">
        <thead><tr><th>Source</th><th>Matched</th><th>Strategy</th><th>Boundaries</th></tr></thead>
        <tbody>
          {sources.map(([src, s]) => (
            <tr key={src}>
              <td className="mono">{src}</td>
              <td>{s.matched
                ? <span className="tag tag-ok">yes</span> : <span className="tag tag-bad">no</span>}</td>
              <td className="muted">{s.strategy || s.reason || '–'}</td>
              <td className="muted tiny">
                {(s.boundaries || []).map((b, i) => (
                  <div key={i} className={b.outlier ? 'tag tag-bad' : ''}>
                    p{b.part}#{b.segment} {b.side} {fmtTime(b.t)} → {fmtTime(b.corrected)}{' '}
                    <span className="muted">({b.method}{b.low_confidence ? ', low' : ''})</span>{' '}
                    <button className="btn btn-ghost btn-sm" onClick={() => onPreview(src, b.corrected)}>preview</button>
                  </div>
                ))}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export function TimelineEdit() {
  const { key, tid } = useParams();
  const base = seriesPath(key);
  const tl = useLoad(() => api(`/timelines/${tid}`), [tid]);
  const epsQ = useLoad(() => api(`${base}/episodes`), [key]);
  const syncQ = useLoad(() => api(`/timelines/${tid}/sync`), [tid]);
  const { act, msg, busy, clear } = useAct();
  const [doc, setDoc] = useState(null);
  const [job, setJob] = useState(null);
  const [dry, setDry] = useState(null);
  const [preview, setPreview] = useState(null); // {src, t}
  const [aiOpen, setAiOpen] = useState(false);
  const [drag, setDrag] = useState(null); // {list: 'parts'|pi, i}

  const edit = useCallback((fn) => setDoc((d) => {
    const next = structuredClone(d);
    fn(next);
    return next;
  }), []);
  const data = tl.data;
  if (data && !doc) setDoc(data.doc);

  const pollJob = useCallback(async () => {
    if (job) {
      const j = await api(`/jobs/${job.id}`).catch(() => job);
      setJob(j);
      if (!ACTIVE.includes(j.status)) { syncQ.reload(); tl.reload(); }
    }
  }, [job]); // eslint-disable-line react-hooks/exhaustive-deps
  usePoll(pollJob, 1000, job && ACTIVE.includes(job.status));

  const episodes = useMemo(() => epsQ.data?.episodes || [], [epsQ.data]);
  const srcOptions = useMemo(() => episodes.filter((e) => e.has_file).map((e) => e.id), [episodes]);

  if ((tl.error && !tl.data) || (epsQ.error && !epsQ.data)) return <ErrorBox error={tl.error || epsQ.error} onRetry={tl.reload} />;
  if (!doc) return <Loading />;

  const save = () => act(async () => {
    const clean = { ...doc, parts: doc.parts.filter((p) => p.segments.length) };
    await api(`/timelines/${tid}`, { method: 'PUT', body: clean });
    await Promise.all([tl.reload(), syncQ.reload()]);
    setDoc(null);
  }, 'Saved (sync invalidated; run sync again before rendering)');
  const sync = () => act(async () => setJob(await api(`/timelines/${tid}/sync`, { method: 'POST' })), 'Sync queued');
  const render = (dryRun) => act(async () => {
    if (dryRun) { setDry(await api(`/timelines/${tid}/render`, { method: 'POST', body: { dry_run: true } })); return undefined; }
    return setJob(await api(`/timelines/${tid}/render`, { method: 'POST', body: {} }));
  }, dryRun ? undefined : 'Render queued');

  const setSeg = (pi, si, field, v) => edit((d) => { d.parts[pi].segments[si][field] = v; });
  const addSeg = (pi) => edit((d) => {
    const src = Object.keys(d.sources)[0] || srcOptions[0];
    if (!src) return;
    if (!d.sources[src]) d.sources[src] = { duration: episodes.find((e) => e.id === src)?.duration || 1440 };
    d.parts[pi].segments.push({ src, start: 0, end: 60 });
  });
  const runtime = (p) => p.segments.reduce((a, s) => a + Math.max(0, s.end - s.start), 0);

  const drop = (target) => {
    if (!drag || drag.list !== target.list) { setDrag(null); return; }
    edit((d) => {
      if (drag.list === 'parts') d.parts = move(d.parts, drag.i, target.i);
      else d.parts[drag.list].segments = move(d.parts[drag.list].segments, drag.i, target.i);
    });
    setDrag(null);
  };
  const dragProps = (list, i) => ({
    draggable: true,
    onDragStart: () => setDrag({ list, i }),
    onDragOver: (e) => e.preventDefault(),
    onDrop: () => drop({ list, i }),
  });

  return (
    <>
      <PageHeader title={doc.title} sub={data ? `${data.parts} part(s) · ${data.sources} source(s)` : ''}>
        <Link className="btn" to={`/series/${encodeURIComponent(key)}/timelines`}>← Timelines</Link>
        <button className="btn" onClick={() => setAiOpen(true)}>AI outline…</button>
        <button className="btn" onClick={() => act(async () => downloadJson(recipeFileName(doc.title), await api(`/timelines/${tid}/export?anchors=both`)), 'Exported with anchors')}>Export + anchors</button>
        <button className="btn btn-primary" onClick={save} disabled={busy}>Save</button>
      </PageHeader>
      <Notice msg={msg} onClose={clear} />

      {data?.missing?.length > 0 && (
        <div className="notice notice-err" role="alert">
          <span>Missing sources: {data.missing.map((m) => `${m.src} (parts ${m.parts.join(', ')})`).join(' · ')}</span>
        </div>
      )}

      <div className="card row wrap" style={{ marginBottom: 14 }}>
        <label className="field grow">Title
          <input className="input" value={doc.title} onChange={(e) => edit((d) => { d.title = e.target.value; })} />
        </label>
        <button className="btn" onClick={sync} disabled={busy || (job && ACTIVE.includes(job.status))}>Sync</button>
        <button className="btn" onClick={() => render(true)} disabled={busy}>Dry run</button>
        <button className="btn btn-primary" onClick={() => render(false)}
          disabled={busy || !syncQ.data?.synced || (job && ACTIVE.includes(job.status))}
          title={syncQ.data?.synced ? '' : 'Run sync first'}>Render</button>
      </div>

      {job && (
        <div className="card stack" style={{ marginBottom: 14 }}>
          <div className="row"><b>{job.title}</b><StatusTag status={job.status} /><span className="muted grow">{job.message}</span></div>
          <Progress value={job.progress} />
          {job.status === 'done' && job.result?.outputs && (
            <div className="mono tiny">{job.result.outputs.map((o) => <div key={o}>{o}</div>)}</div>
          )}
        </div>
      )}

      <SyncReport report={syncQ.data} onPreview={(src, t) => setPreview({ src, t })} />

      {doc.parts.map((p, pi) => (
        <div key={pi} className="card" style={{ marginBottom: 14 }} {...dragProps('parts', pi)}>
          <div className="row">
            <span className="muted" title="Drag to reorder">⠿</span>
            <input className="input grow" value={p.title} aria-label={`Part ${pi + 1} title`}
              onChange={(e) => edit((d) => { d.parts[pi].title = e.target.value; })} />
            <span className="mono tiny muted">{fmtTime(runtime(p))}</span>
            <button className="btn btn-sm" onClick={() => addSeg(pi)}>+ segment</button>
            <button className="btn btn-sm btn-ghost" aria-label={`Delete part ${pi + 1}`}
              onClick={() => edit((d) => { d.parts.splice(pi, 1); })}>✕</button>
          </div>
          <table className="table">
            <tbody>
              {p.segments.map((s, si) => (
                <tr key={si} {...dragProps(pi, si)}>
                  <td className="muted" title="Drag to reorder">⠿</td>
                  <td>
                    <select className="input" value={s.src} aria-label={`Segment ${si + 1} source`}
                      onChange={(e) => setSeg(pi, si, 'src', e.target.value)}>
                      {[...new Set([s.src, ...srcOptions])].map((id) => <option key={id}>{id}</option>)}
                    </select>
                  </td>
                  <td><TimeInput value={s.start} label={`Segment ${si + 1} start`} onCommit={(v) => setSeg(pi, si, 'start', v)} /></td>
                  <td><TimeInput value={s.end} label={`Segment ${si + 1} end`} onCommit={(v) => setSeg(pi, si, 'end', v)} /></td>
                  <td className="muted tiny">{s.desc || ''}</td>
                  <td className="row">
                    <button className="btn btn-ghost btn-sm" onClick={() => setPreview({ src: s.src, t: s.start })}>▶</button>
                    <button className="btn btn-ghost btn-sm" aria-label={`Delete segment ${si + 1}`}
                      onClick={() => edit((d) => { d.parts[pi].segments.splice(si, 1); })}>✕</button>
                  </td>
                </tr>
              ))}
              {p.segments.length === 0 && <tr><td className="muted">No segments yet.</td></tr>}
            </tbody>
          </table>
        </div>
      ))}
      <button className="btn" onClick={() => edit((d) => d.parts.push({ title: `Part ${d.parts.length + 1}`, segments: [] }))}>+ Add part</button>

      {dry && (
        <div className="card" style={{ marginTop: 14 }}>
          <h3>Dry run · {dry.show}</h3>
          {dry.parts.map((p) => (
            <p key={p.part} className="muted tiny">
              <b>{p.title}</b> — {fmtTime(p.runtime)} · {p.segments.length} segment(s) · {p.mode}{' '}
              <span className="mono">{p.output}</span>
            </p>
          ))}
        </div>
      )}

      {preview && (
        <Dialog title={`${preview.src} @ ${fmtTime(preview.t, 1)}`} onClose={() => setPreview(null)}>
          <video controls autoPlay style={{ width: '100%' }}
            src={`/api/series/${encodeURIComponent(key)}/episodes/${preview.src}/preview?t=${preview.t}`} />
        </Dialog>
      )}

      {aiOpen && (
        <AIOutlineDialog base={base} episodes={episodes}
          onClose={() => setAiOpen(false)}
          onLoad={(parts, sources) => {
            edit((d) => {
              Object.assign(d.sources, sources);
              d.parts = [...d.parts.filter((x) => x.segments.length), ...parts];
            });
            setAiOpen(false);
          }} />
      )}
    </>
  );
}

function AIOutlineDialog({ base, episodes, onLoad, onClose }) {
  const { act, busy } = useAct();
  const [notes, setNotes] = useState('');
  const [outline, setOutline] = useState(null);
  const [error, setError] = useState(null);
  const run = () => act(async () => {
    setError(null);
    try {
      const r = await api(`${base}/timeline/outline`, { method: 'POST', body: { notes } });
      setOutline(r.outline);
    } catch (e) { setError(e.message); throw e; }
  });
  const load = () => {
    // placeholder segments: 2 minutes from the approximate time, clamped to the source
    const sources = {};
    const parts = outline.map((p) => ({
      title: p.part,
      segments: p.scenes.map((s) => {
        const dur = episodes.find((e) => e.id === s.src)?.duration || 1440;
        sources[s.src] = { duration: dur };
        return { src: s.src, start: Math.max(0, s.approx), end: Math.min(s.approx + 120, dur), desc: s.desc || '' };
      }),
    }));
    onLoad(parts, sources);
  };
  return (
    <Dialog title="AI outline from chronology notes" onClose={onClose} wide>
      <p className="muted">Paste wiki chronology or community notes. The AI orders scenes; you set the exact in/out points afterwards.</p>
      <textarea className="input" rows={8} style={{ width: '100%', fontFamily: 'inherit' }}
        value={notes} onChange={(e) => setNotes(e.target.value)} aria-label="Chronology notes" />
      {error && <div className="notice notice-err" role="alert">{error}</div>}
      {outline && <p className="muted">{outline.length} part(s), {outline.reduce((a, p) => a + p.scenes.length, 0)} scene(s) — loads as placeholder segments.</p>}
      <div className="row" style={{ marginTop: 10, justifyContent: 'flex-end' }}>
        <button className="btn" onClick={run} disabled={busy || !notes.trim()}>Generate</button>
        {outline && <button className="btn btn-primary" onClick={load}>Load as placeholders</button>}
      </div>
    </Dialog>
  );
}
