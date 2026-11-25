import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Link, useNavigate, useParams } from 'react-router';
import { api, seriesPath } from '../lib/api.js';
import { useAct, useCrumbRoot, useLoad, usePoll } from '../lib/hooks.js';
import { SegmentFx, GradePanel } from '../components/SegmentFx.jsx';
import { fmtDuration, fmtTime, parseTime } from '../lib/time.js';
import {
  flatSegments, fxList, moveItem, recipeIssues, rulerTicks, segmentSync, setFx, sourceJoins, syncCounts,
} from '../lib/timeline.js';
import { Dialog, ErrorBox, Loading, MoreMenu, Notice, Progress, StatusTag } from '../components/ui.jsx';
import { enc } from '../components/kit.jsx';
import './TimelineEdit.css';
import { useApi } from '../lib/store.js';

const ACTIVE = ['queued', 'running'];
// Deep, desaturated clip colours (from the design), one per source in recipe order.
const SRC_COLORS = ['#4A56A8', '#8E3434', '#8A7A55', '#4F7A5A', '#6A5A9E', '#2F6F7A', '#8A4F7A', '#5A6E3A'];
const STATUS_TEXT = { ok: 'found', partial: 'partly found', missing: 'not in your edition', unanchored: 'no anchors' };

function downloadJson(name, data) {
  const url = URL.createObjectURL(new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' }));
  const a = Object.assign(document.createElement('a'), { href: url, download: name });
  a.click();
  URL.revokeObjectURL(url);
}
const recipeFileName = (title) => `${title.replace(/[^\w.-]+/g, '_') || 'timeline'}.fanscut.json`;

// Editor chrome, scoped to .nle. Colours follow the design's editor screen.
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
    <input className="sel mono" value={shown} aria-label={label}
      onChange={(e) => setText(e.target.value)} onBlur={commit}
      onKeyDown={(e) => e.key === 'Enter' && e.currentTarget.blur()} />
  );
}

function boundaryText(b) {
  if (!b) return '–';
  if (!b.anchored && b.method === 'none') return 'no anchor';
  if (b.method === 'none') return 'not found';
  const conf = b.confidence != null ? ` ${Number(b.confidence).toFixed(2)}` : '';
  const moved = b.corrected != null && Math.abs(b.corrected - b.t) >= 0.05 ? ` → ${fmtTime(b.corrected, 1)}` : '';
  return `${b.method}${conf}${moved}${b.low_confidence || b.outlier ? ' · check' : ''}`;
}

function SyncReport({ report, onPreview }) {
  if (!report?.synced) return <p className="muted">Not synced yet. Sync finds every cut point in your own files.</p>;
  const sources = Object.entries(report.sources || {});
  return (
    <table className="table">
      <thead><tr><th>Source</th><th>Matched</th><th>Strategy</th><th>Cut points</th></tr></thead>
      <tbody>
        {sources.map(([src, s]) => (
          <tr key={src}>
            <td className="mono">{src}{s.edition ? <span className="chip">{s.edition}</span> : null}</td>
            <td>{s.matched
              ? <span className="tag tag-ok">{s.matched_boundaries ?? 'yes'} / {s.total_boundaries ?? '–'}</span>
              : <span className="tag tag-bad">no</span>}</td>
            <td className="muted">{s.strategy || s.reason || '–'}</td>
            <td className="muted tiny">
              {Object.entries(s.segments || {}).filter(([, st]) => st === 'missing').map(([ps]) => (
                <div key={ps}><span className="tag tag-bad">not in your edition</span> part {ps.replace(':', ', segment #')}</div>
              ))}
              {(s.boundaries || []).map((b, i) => (
                <div key={i} className={b.outlier ? 'c-danger' : ''}>
                  p{b.part}#{b.segment} {b.side} {fmtTime(b.t)} → {fmtTime(b.corrected)}{' '}
                  <span className="muted">({b.method}{b.low_confidence ? ', low' : ''})</span>{' '}
                  <button className="bt" onClick={() => onPreview(src, b.corrected ?? b.t)}>Preview</button>
                </div>
              ))}
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

// J/K/L, I/O, Ctrl+S, Ctrl+Z for the whole page: one window listener that always calls
// the editor's latest handler.
function KeyListener({ onKey }) {
  const ref = useRef(onKey);
  useEffect(() => { ref.current = onKey; });
  useEffect(() => {
    const on = (e) => ref.current(e);
    window.addEventListener('keydown', on);
    return () => window.removeEventListener('keydown', on);
  }, []);
  return null;
}

export function TimelineEdit() {
  const { id } = useParams();
  const crumb = useCrumbRoot();
  const navigate = useNavigate();
  const tl = useApi(`/timelines/${enc(id)}`);
  const seriesKey = tl.data?.series_key;
  const base = seriesKey ? seriesPath(seriesKey) : null;
  const epsQ = useLoad(() => (seriesKey
    ? api(`${seriesPath(seriesKey)}/episodes`).catch(() => ({ episodes: [] }))
    : Promise.resolve(null)), [seriesKey]);
  const syncQ = useApi(`/timelines/${enc(id)}/sync`);
  const varQ = useLoad(() => api(`/timelines/${enc(id)}/variants`).catch(() => null), [id]);
  const { act, msg, busy, clear } = useAct();
  const [doc, setDoc] = useState(null);
  const [dirty, setDirty] = useState(false);
  const [view, setView] = useState('edit');
  const [sel, setSel] = useState(null); // {pi, si}
  const [viewer, setViewer] = useState(null); // {src, t}
  const [now, setNow] = useState(null); // absolute source time under the viewer's playhead
  const [videoErr, setVideoErr] = useState(false);
  const [job, setJob] = useState(null);
  const [repeats, setRepeats] = useState(null); // {src, start, end, matches}
  const [dry, setDry] = useState(null);
  const [variant, setVariant] = useState('');
  const [aiOpen, setAiOpen] = useState(false);
  const [drag, setDrag] = useState(null); // {kind:'seg', pi, si} | {kind:'part', pi}
  const [over, setOver] = useState(null);
  const file = useRef(null);
  const video = useRef(null);
  const history = useRef([]); // earlier docs, for Undo (Ctrl+Z)
  const [zoom, setZoom] = useState('fit');
  const [rate, setRate] = useState(1);
  const [hiddenLanes, setHiddenLanes] = useState({}); // lane id -> hidden
  const [help, setHelp] = useState(false);
  const [undoable, setUndoable] = useState(0);

  const lastEdit = useRef({ key: null, at: 0 });
  // `key` groups a burst of changes to one field (a slider drag) into one Undo step
  const edit = useCallback((fn, key = null) => {
    setDirty(true);
    const now = Date.now();
    const burst = key && lastEdit.current.key === key && now - lastEdit.current.at < 1500;
    lastEdit.current = { key, at: now };
    setDoc((d) => {
      if (!burst) history.current = [...history.current.slice(-49), d];
      setUndoable(history.current.length);
      const next = structuredClone(d);
      fn(next);
      return next;
    });
  }, []);
  const openImport = useCallback(() => file.current?.click(), []);
  const undo = useCallback(() => {
    const prev = history.current.pop();
    setUndoable(history.current.length);
    if (prev) { setDoc(prev); setDirty(true); }
  }, []);
  const data = tl.data;
  if (data && !doc) setDoc(data.doc);

  const pollJob = useCallback(async () => {
    if (!job) return;
    const j = await api(`/jobs/${job.id}`).catch(() => job);
    setJob(j);
    if (ACTIVE.includes(j.status)) return;
    if (j.kind === 'find_similar') {
      setRepeats((r) => r && ({ ...r, matches: j.result?.matches || [] }));
    } else {
      syncQ.reload(); varQ.reload(); tl.reload();
    }
  }, [job]); // eslint-disable-line react-hooks/exhaustive-deps
  usePoll(pollJob, 1000, job && ACTIVE.includes(job.status));

  const episodes = useMemo(() => epsQ.data?.episodes || [], [epsQ.data]);
  const flat = useMemo(() => flatSegments(doc), [doc]);
  const issues = useMemo(() => recipeIssues(doc), [doc]);

  if (tl.error && !tl.data) return <div className="page"><ErrorBox error={tl.error} onRetry={tl.reload} /></div>;
  if (!doc) return <div className="page"><Loading /></div>;

  const report = syncQ.data;
  const counts = syncCounts(report);
  const srcKeys = Object.keys(doc.sources || {});
  const colorOf = (src) => {
    const i = srcKeys.indexOf(src);
    return i < 0 ? '#444' : SRC_COLORS[i % SRC_COLORS.length];
  };
  const epById = Object.fromEntries(episodes.map((e) => [e.id, e]));
  const srcName = (src) => doc.sources?.[src]?.note || epById[src]?.title || src;
  const srcOptions = [...new Set([...srcKeys, ...episodes.filter((e) => e.has_file).map((e) => e.id)])];
  const missing = data?.missing || [];
  const missingSet = new Set(missing.map((m) => m.src));
  const toggles = doc.toggles || [];
  const running = job && ACTIVE.includes(job.status);
  const selSeg = sel ? doc.parts[sel.pi]?.segments[sel.si] : null;
  const selFlat = selSeg ? flat.segs.find((x) => x.pi === sel.pi && x.si === sel.si) : null;
  const total = flat.total;
  const pct = (t) => (total ? (t / total) * 100 : 0);
  const viewerT = now ?? viewer?.t ?? 0;
  const clipBase = viewer ? Math.max(viewer.t - 5, 0) : 0;

  const ensureSource = (d, src) => {
    if (!d.sources[src]) d.sources[src] = { duration: epById[src]?.duration || 1440 };
  };
  const look = (src, t) => { setViewer({ src, t: Math.max(0, t) }); setNow(null); setVideoErr(false); };
  const select = (pi, si) => {
    const s = doc.parts[pi]?.segments[si];
    setSel(s ? { pi, si } : null);
    if (s) look(s.src, s.start);
  };
  const setSeg = (field, v) => sel && edit((d) => {
    const s = d.parts[sel.pi].segments[sel.si];
    if (v == null || v === '') delete s[field]; else s[field] = v;
    if (field === 'src') ensureSource(d, v);
  });
  const addSeg = (pi, at, seg) => edit((d) => {
    if (!d.parts.length) d.parts.push({ title: 'Part 1', segments: [] });
    const p = Math.min(pi ?? d.parts.length - 1, d.parts.length - 1);
    const src = seg?.src || Object.keys(d.sources)[0] || srcOptions[0];
    if (!src) return;
    ensureSource(d, src);
    const segs = d.parts[p].segments;
    segs.splice(at ?? segs.length, 0, seg || { src, start: 0, end: 60 });
  });
  const setSegFx = (path, v) => sel && edit((d) => { setFx(d.parts[sel.pi].segments[sel.si], path, v); }, `${sel.pi}:${sel.si}:${path}`);
  const copyGrade = () => sel && selSeg?.video?.grade && edit((d) => {
    const g = d.parts[sel.pi].segments[sel.si].video.grade;
    for (const p of d.parts) {
      for (const sg of p.segments) {
        if (sg.src === selSeg.src) { sg.video = { ...(sg.video || {}), grade: { ...g } }; }
      }
    }
  });
  // transport: J back 2 s (to the previous clip at its start), K play/pause, L play, faster on repeat
  const jkl = (key) => {
    const v = video.current;
    if (key === 'k') { if (v) { if (v.paused) v.play(); else v.pause(); } return; }
    if (key === 'j') {
      if (v && v.currentTime > 2) { v.currentTime -= 2; return; }
      if (viewer) look(viewer.src, Math.max(0, viewerT - 7));
      return;
    }
    if (key === 'l' && v) {
      const next = v.paused ? 1 : Math.min((v.playbackRate || 1) * 2, 4);
      v.playbackRate = next; setRate(next); v.play();
    }
  };
  const delSeg = () => { if (sel) { edit((d) => { d.parts[sel.pi].segments.splice(sel.si, 1); }); setSel(null); } };
  const shift = (by) => {
    if (!sel) return;
    const n = doc.parts[sel.pi].segments.length;
    const to = Math.max(0, Math.min(n - 1, sel.si + by));
    edit((d) => { d.parts[sel.pi].segments = moveItem(d.parts[sel.pi].segments, sel.si, to); });
    setSel({ pi: sel.pi, si: to });
  };

  // drag: segments between/within parts on the timeline, parts in the project panel
  const dropOn = (target) => {
    const from = drag;
    setDrag(null); setOver(null);
    if (!from || from.kind !== target.kind) return;
    if (from.kind === 'part') {
      edit((d) => { d.parts = moveItem(d.parts, from.pi, target.pi); });
      setSel(null);
      return;
    }
    edit((d) => {
      const [x] = d.parts[from.pi].segments.splice(from.si, 1);
      d.parts[target.pi].segments.splice(target.si, 0, x);
    });
    setSel({ pi: target.pi, si: target.si });
  };
  const dragProps = (target) => ({
    draggable: true,
    onDragStart: () => setDrag(target),
    onDragOver: (e) => { e.preventDefault(); setOver(target); },
    onDragEnd: () => { setDrag(null); setOver(null); },
    onDrop: (e) => { e.preventDefault(); dropOn(target); },
  });
  const isOver = (t) => over && drag && over.kind === t.kind && over.pi === t.pi && over.si === t.si;

  const save = () => act(async () => {
    const clean = { ...doc, parts: doc.parts.filter((p) => p.segments.length) };
    await api(`/timelines/${enc(id)}`, { method: 'PUT', body: clean });
    await Promise.all([tl.reload(), syncQ.reload(), varQ.reload()]);
    setDoc(null); setDirty(false); setSel(null);
  }, 'Saved. Sync was reset: run it again before rendering.');
  const sync = () => act(async () => setJob(await api(`/timelines/${enc(id)}/sync`, { method: 'POST' })), 'Sync queued');
  const dryRun = () => act(async () => {
    setDry(await api(`/timelines/${enc(id)}/render`, { method: 'POST', read: true, body: { dry_run: true, variant: variant || null } }));
  });
  const exportRecipe = () => act(async () => downloadJson(recipeFileName(doc.title),
    await api(`/timelines/${enc(id)}/export?anchors=both`)), dirty ? 'Exported the saved version (with anchors); save to include your changes' : 'Exported with anchors');
  const onFile = (e) => {
    const f = e.target.files[0];
    e.target.value = '';
    if (!f) return;
    act(async () => {
      const parsed = JSON.parse(await f.text());
      if (!parsed || !Array.isArray(parsed.parts) || typeof parsed.sources !== 'object') {
        throw new Error('That file is not a FansCut timeline recipe (no parts or sources).');
      }
      setDoc(parsed); setDirty(true); setSel(null);
    }, `Imported ${f.name}. Review it, then Save.`);
  };
  const markIn = () => selSeg && viewer?.src === selSeg.src && setSeg('start', Math.round(Math.min(viewerT, selSeg.end - 0.5) * 10) / 10);
  const markOut = () => selSeg && viewer?.src === selSeg.src && setSeg('end', Math.round(Math.max(viewerT, selSeg.start + 0.5) * 10) / 10);
  const addToCut = () => {
    const src = viewer?.src || selSeg?.src;
    const dur = doc.sources[src]?.duration || epById[src]?.duration || Infinity;
    const start = Math.round(viewerT * 10) / 10;
    const seg = src ? { src, start, end: Math.min(start + 60, dur) } : undefined;
    const pi = sel ? sel.pi : doc.parts.length - 1;
    const at = sel ? sel.si + 1 : undefined;
    addSeg(pi, at, seg);
    if (sel) setSel({ pi: sel.pi, si: sel.si + 1 });
  };
  const findRepeats = () => {
    if (!selSeg || !base) return;
    act(async () => {
      setRepeats({ src: selSeg.src, start: selSeg.start, end: selSeg.end, matches: null });
      setJob(await api(`${base}/find-similar`, { method: 'POST', body: { episode_id: selSeg.src, start: selSeg.start, end: selSeg.end } }));
    }, 'Looking for this scene in the other episodes');
  };
  const isMovie = (src) => doc.sources?.[src]?.kind === 'movie';
  const onKey = (e) => {
    const typing = /^(INPUT|TEXTAREA|SELECT)$/.test(e.target?.tagName || '') || e.target?.isContentEditable;
    const mod = e.ctrlKey || e.metaKey;
    if (mod && e.key.toLowerCase() === 's') { e.preventDefault(); save(); return; }
    if (mod && e.key.toLowerCase() === 'z' && !typing) { e.preventDefault(); undo(); return; }
    if (typing || mod || e.altKey || view !== 'edit') return;
    const k = e.key.toLowerCase();
    if (k === 'j' || k === 'k' || k === 'l') { e.preventDefault(); jkl(k); }
    else if (k === 'i') markIn();
    else if (k === 'o') markOut();
    else if (k === '?') setHelp((h) => !h);
  };
  const lane = (id) => !hiddenLanes[id];
  const toggleLane = (id) => setHiddenLanes((h) => ({ ...h, [id]: !h[id] }));
  const titled = flat.segs.filter((x) => x.seg.title);
  const music = flat.segs.filter((x) => x.seg.audio?.src);
  const withFx = flat.segs.filter((x) => fxList(x.seg).length);
  const menus = [
    ['File', [
      ['Save', save, { keys: 'Ctrl+S', disabled: busy }],
      ['Import recipe…', openImport],
      ['Export recipe', exportRecipe, { disabled: busy }],
      ['Render…', () => navigate(`/cuts/${enc(id)}/build`, { state: crumb.state })],
      ['Back to the cut', () => navigate(`/cuts/${enc(id)}`, { state: crumb.state })],
    ]],
    ['Edit', [
      ['Undo', undo, { keys: 'Ctrl+Z', disabled: !undoable }],
      ['Move earlier', () => shift(-1), { disabled: !sel || sel.si === 0 }],
      ['Move later', () => shift(1), { disabled: !sel || sel.si >= (doc.parts[sel.pi]?.segments.length || 1) - 1 }],
      ['Delete segment', delSeg, { disabled: !sel, danger: true }],
    ]],
    ['Cut', [
      ['Add part', () => edit((d) => d.parts.push({ title: `Part ${d.parts.length + 1}`, segments: [] }))],
      ['Add segment', () => addSeg(sel ? sel.pi : undefined), { disabled: !srcOptions.length && !srcKeys.length }],
      ['Mark in', markIn, { keys: 'I', disabled: !selSeg || viewer?.src !== selSeg.src }],
      ['Mark out', markOut, { keys: 'O', disabled: !selSeg || viewer?.src !== selSeg.src }],
      ['AI outline…', () => setAiOpen(true), { disabled: !base }],
    ]],
    ['View', [
      ['Edit', () => setView('edit')], ['Sync', () => setView('sync')],
      ['Colour', () => setView('colour')], ['Render plan', () => setView('render')],
      ['Viewer: fit', () => setZoom('fit')], ['Viewer: 100 %', () => setZoom('100')], ['Viewer: 200 %', () => setZoom('200')],
    ]],
    ['Help', [['Keyboard shortcuts', () => setHelp((h) => !h), { keys: '?' }]]],
  ];

  // Assist: real things to look at, from validation, missing files and the sync report.
  const assist = [];
  for (const m of missing) {
    assist.push({ key: `m-${m.src}`, mark: 'var(--danger)', title: `${srcName(m.src)} is not in your library`,
      detail: `Used in part ${m.parts.join(', ')}`, action: 'Show', run: () => {
        const x = flat.segs.find((s) => s.seg.src === m.src); if (x) select(x.pi, x.si);
      } });
  }
  for (const [i, is] of issues.entries()) {
    assist.push({ key: `i-${i}`, mark: 'var(--danger)', title: is.text, detail: 'Fix before saving',
      action: is.si == null ? '' : 'Select', run: is.si == null ? null : () => select(is.pi, is.si) });
  }
  if (dirty) assist.push({ key: 'dirty', mark: 'var(--accent)', title: 'Unsaved changes', detail: 'Save writes the recipe and resets the sync', action: 'Save', run: save });
  if (!report?.synced) {
    assist.push({ key: 'sync', mark: 'var(--accent)', title: 'Not synced yet', detail: 'Find every cut point in your files before rendering', action: 'Sync', run: sync });
  } else {
    for (const [src, s] of Object.entries(report.sources || {})) {
      if (!s.matched) assist.push({ key: `u-${src}`, mark: 'var(--danger)', title: `${src}: no cut points found`, detail: s.reason || 'The file does not match the recipe', action: 'Sync view', run: () => setView('sync') });
      for (const [ps, st] of Object.entries(s.segments || {})) {
        if (st !== 'missing' && st !== 'partial') continue;
        const [p, g] = ps.split(':').map(Number);
        assist.push({ key: `s-${src}-${ps}`, mark: st === 'missing' ? 'var(--danger)' : 'var(--accent)',
          title: `Segment ${p}.${g} ${STATUS_TEXT[st]}`, detail: src, action: 'Select', run: () => select(p - 1, g - 1) });
      }
      for (const b of s.boundaries || []) {
        if (!b.outlier && !b.low_confidence) continue;
        assist.push({ key: `b-${src}-${b.part}-${b.segment}-${b.side}`, mark: 'var(--accent)',
          title: `Check the ${b.side} of segment ${b.part}.${b.segment}`, detail: `${src} ${fmtTime(b.corrected ?? b.t)} · ${b.outlier ? 'outlier' : 'low confidence'}`,
          action: 'Preview', run: () => { select(b.part - 1, b.segment - 1); look(src, b.corrected ?? b.t); } });
      }
    }
  }

  const joins = sourceJoins(flat.segs);
  const ticks = rulerTicks(total);
  const variants = doc.variants || [];
  const segOff = (seg) => {
    if (!seg.when) return false;
    const v = variants.find((x) => x.id === variant);
    const id0 = seg.when.replace(/^!/, '');
    const def = toggles.find((t) => t.id === id0)?.default ?? false;
    const on = v && id0 in (v.toggles || {}) ? v.toggles[id0] : def;
    return seg.when.startsWith('!') ? on : !on;
  };
  const segSync = selSeg ? segmentSync(report, selSeg.src, sel.pi, sel.si) : null;
  const vrow = varQ.data?.variants?.find((v) => v.id === variant);
  const runtimeNote = vrow ? fmtDuration(vrow.runtime) : fmtDuration(total);

  return (
    <div className="page page-wide">
      <Notice msg={msg} onClose={clear} />
      <div className="nle">
        <KeyListener onKey={onKey} />
        <nav className="menubar" aria-label="Editor menu">
          {/* menu items run on click (edit/undo touch the undo history ref then), never during render */}
          {/* eslint-disable-next-line react-hooks/refs */}
          {menus.map(([name, items]) => <MoreMenu key={name} label={name} items={items} className="bt" align="left" />)}
        </nav>
        {help && (
          <div className="pn" style={{ margin: '0 6px', padding: '10px 14px' }}>
            <div className="row"><b className="grow">Keyboard shortcuts</b><button type="button" className="bt" onClick={() => setHelp(false)} aria-label="Close shortcuts">✕</button></div>
            <dl className="shortcuts">
              <dt>J</dt><dd>Back 2 seconds (keep pressing to go further back)</dd>
              <dt>K</dt><dd>Play or pause</dd>
              <dt>L</dt><dd>Play; press again for 2× and 4×</dd>
              <dt>I / O</dt><dd>Mark the selected segment's in / out at the viewer time</dd>
              <dt>Ctrl+S</dt><dd>Save</dd>
              <dt>Ctrl+Z</dt><dd>Undo</dd>
              <dt>?</dt><dd>Show or hide this list</dd>
            </dl>
          </div>
        )}
        <div className="bar">
          <div className="bar-l">
            <Link to="/" className="bt" aria-label="FansCut home" title="FansCut home">⌂</Link>
            <Link to={`/cuts/${enc(id)}`} state={crumb.state} className="bt" aria-label="Back to the cut">← Cut</Link>
            <b className="ell" style={{ fontSize: 14, maxWidth: 320 }}>{doc.title}</b>
            <span style={{ color: 'var(--nm)' }}>{runtimeNote}{dirty ? ' · unsaved' : ''}</span>
          </div>
          <div className="bar-r">
            <div role="tablist" aria-label="Mode" style={{ display: 'flex', gap: 16 }}>
              {[['edit', 'Edit'], ['sync', 'Sync'], ['colour', 'Colour'], ['render', 'Render']].map(([v, t]) => (
                <button key={v} type="button" role="tab" className="mtab" aria-selected={view === v} onClick={() => setView(v)}>{t}</button>
              ))}
            </div>
            <span style={{ color: 'var(--nm)', fontSize: 12, display: 'flex', alignItems: 'center', gap: 6 }}>
              <span className="dot" style={{ background: !counts ? '#555' : counts.matched === counts.total ? 'var(--ok)' : 'var(--accent)' }} />
              {counts ? `Synced ${counts.matched} / ${counts.total}` : 'Not synced'}
            </span>
            {variants.length > 0 && (
              <select className="sel" aria-label="Variant" style={{ width: 150 }} value={variant} onChange={(e) => setVariant(e.target.value)}>
                <option value="">Default</option>
                {variants.map((v) => <option key={v.id} value={v.id}>{v.label}</option>)}
              </select>
            )}
            <button type="button" className="bt" onClick={() => file.current.click()}>Import recipe</button>
            <input ref={file} type="file" accept="application/json,.json" hidden onChange={onFile} data-testid="recipe-file" />
            <button type="button" className="bt" onClick={exportRecipe} disabled={busy}>Export recipe</button>
            <button type="button" className="bt" onClick={save} disabled={busy}>Save</button>
            <Link to={`/cuts/${enc(id)}/build`} state={crumb.state} className="bt bp">Render</Link>
          </div>
        </div>

        {job && (
          <div className="pn" style={{ margin: '0 6px', padding: '8px 12px', gap: 6 }}>
            <div className="row"><b>{job.title}</b><StatusTag status={job.status} /><span className="muted grow">{job.message}</span>
              <button className="bt" onClick={() => setJob(null)} disabled={running} aria-label="Hide job">✕</button></div>
            <Progress value={job.progress} />
          </div>
        )}

        {view === 'edit' && (
          <>
            <div className="panes">
              {/* Project */}
              <div style={{ flex: '1 1 260px', maxWidth: 330, display: 'flex', flexDirection: 'column', gap: 6 }}>
                <section className="pn" aria-label="Project">
                  <div className="ph"><span className="on">Project</span><span style={{ marginLeft: 'auto' }}>{flat.segs.length} segments</span></div>
                  <ul className="list">
                    <li>
                      <button type="button" className="prow" aria-pressed={!sel} onClick={() => setSel(null)}>
                        <span /><span className="sw" style={{ background: 'var(--nblue)' }} />
                        <span className="ell">{doc.title}</span><span style={{ color: 'var(--nm)', fontSize: 12 }}>Main cut</span>
                      </button>
                    </li>
                    {doc.parts.map((p, pi) => {
                      const tgt = { kind: 'part', pi };
                      const rt = p.segments.reduce((a, s) => a + Math.max(0, s.end - s.start), 0);
                      return (
                        <li key={`p${pi}`} {...dragProps(tgt)} style={isOver(tgt) ? { boxShadow: 'inset 0 2px 0 #F2A93B' } : undefined}>
                          <button type="button" className="prow" aria-pressed={sel?.pi === pi} title="Drag to reorder parts"
                            onClick={() => (p.segments.length ? select(pi, 0) : setSel(null))}>
                            <span style={{ color: 'var(--nm)', fontSize: 11 }}>›</span><span className="sw" style={{ background: '#4F7A5A' }} />
                            <span className="ell">{p.title || `Part ${pi + 1}`}</span>
                            <span className="mono" style={{ color: 'var(--nm)', fontSize: 12 }}>{fmtTime(rt)}</span>
                          </button>
                        </li>
                      );
                    })}
                    {srcKeys.map((src) => (
                      <li key={`s${src}`} className="prow" style={{ color: missingSet.has(src) ? '#8A8A8A' : undefined }}>
                        <span /><span className="sw" style={{ background: missingSet.has(src) ? '#444' : colorOf(src) }} />
                        <span className="ell" title={src}>{srcName(src) !== src ? `${src} · ${srcName(src)}` : src}</span>
                        <span className="mono" style={{ color: 'var(--nm)', fontSize: 12 }}>
                          {missingSet.has(src) ? 'missing' : fmtTime(doc.sources[src].duration)}{doc.sources[src].edition ? ` · ${doc.sources[src].edition}` : ''}
                        </span>
                      </li>
                    ))}
                    {variants.length > 0 && (
                      <li className="prow"><span /><span className="sw" style={{ background: 'var(--nblue)' }} />
                        <span className="ell">{variants.map((v) => v.label).join(' · ')}</span><span style={{ color: 'var(--nm)', fontSize: 12 }}>Variants</span></li>
                    )}
                    {toggles.map((t) => {
                      const vt = varQ.data?.toggles?.find((x) => x.id === t.id);
                      return (
                        <li key={`t${t.id}`} className="prow" title={vt?.reason || ''}>
                          <span /><span className="sw" style={{ border: '1px solid #666', background: t.default ? '#666' : 'transparent' }} />
                          <span className="ell">{t.label}</span>
                          <span style={{ color: vt && !vt.enabled ? 'var(--accent)' : 'var(--nm)', fontSize: 12 }}>
                            {vt && !vt.enabled ? 'needs a film' : t.default ? 'Toggle · on' : 'Toggle · off'}
                          </span>
                        </li>
                      );
                    })}
                  </ul>
                  <div style={{ padding: '8px 12px', borderTop: '1px solid var(--nl)', display: 'flex', flexWrap: 'wrap', gap: 6 }}>
                    <button type="button" className="bt" onClick={() => edit((d) => d.parts.push({ title: `Part ${d.parts.length + 1}`, segments: [] }))}>+ Part</button>
                    <button type="button" className="bt" onClick={() => addSeg(sel ? sel.pi : undefined)} disabled={!srcOptions.length && !srcKeys.length}>+ Segment</button>
                    <button type="button" className="bt" onClick={() => setAiOpen(true)} disabled={!base}>AI outline</button>
                  </div>
                </section>
                <section className="pn" aria-label="Assist" style={{ flex: 1 }}>
                  <div className="ph"><span className="on">Assist</span><span style={{ marginLeft: 'auto' }}>{assist.length ? `${assist.length} to check` : 'nothing to check'}</span></div>
                  <ol className="list" style={{ maxHeight: 260, overflowY: 'auto' }}>
                    {assist.map((a) => (
                      <li key={a.key} className="prow" style={{ gridTemplateColumns: '12px minmax(0,1fr) auto' }}>
                        <span className="dot" style={{ background: a.mark }} />
                        <span className="ell" title={a.detail}>{a.title}</span>
                        {a.run ? <button type="button" className="mtab" style={{ color: 'var(--nlink)', fontSize: 12 }} onClick={a.run}>{a.action}</button> : <span />}
                      </li>
                    ))}
                    {assist.length === 0 && <li className="prow" style={{ gridTemplateColumns: '12px 1fr' }}><span className="dot" style={{ background: 'var(--ok)' }} /><span>Saved, synced and valid.</span></li>}
                  </ol>
                </section>
              </div>

              {/* Viewer */}
              <section className="pn" aria-label="Viewer" style={{ flex: '999 1 480px' }}>
                <div className="ph">
                  <span className="on">Cut: {doc.title}</span>
                  <span>Source: {viewer ? `${viewer.src} ${fmtTime(viewerT)}` : '–'}</span>
                </div>
                <div className="viewer">
                  <div className={`screen${zoom === 'fit' ? '' : ' zoomed'}`}>
                    {viewer && !videoErr ? (
                      <video ref={video} key={`${viewer.src}@${viewer.t}`} controls autoPlay
                        style={zoom === 'fit' ? undefined : { width: `${zoom}%`, height: 'auto', maxWidth: 'none' }}
                        onRateChange={(e) => setRate(e.currentTarget.playbackRate)}
                        src={`/api/timelines/${enc(id)}/sources/${enc(viewer.src)}/preview?t=${viewer.t}`}
                        onLoadedMetadata={(e) => { e.currentTarget.currentTime = viewer.t - clipBase; }}
                        onTimeUpdate={(e) => setNow(clipBase + e.currentTarget.currentTime)}
                        onError={() => setVideoErr(true)} />
                    ) : (
                      <span style={{ color: '#666', padding: 16, textAlign: 'center' }}>
                        {!viewer ? 'Select a segment to preview it'
                          : `No preview for ${viewer.src}: it isn't in your library${dirty ? ', or the recipe has unsaved sources (save first)' : ''}`}
                      </span>
                    )}
                  </div>
                </div>
                <div style={{ display: 'flex', flexWrap: 'wrap', alignItems: 'center', gap: 10, padding: '6px 12px', borderTop: '1px solid var(--nl)', color: 'var(--nc)' }}>
                  <span className="tc">{fmtTime(viewerT, 1)}</span>
                  <span className="transport" role="group" aria-label="Transport">
                    <button type="button" className="bt" onClick={() => jkl('j')} disabled={!viewer} title="Back 2 s (J)">J ◀</button>
                    <button type="button" className="bt" onClick={() => jkl('k')} disabled={!viewer} title="Play / pause (K)">K ❚❚</button>
                    <button type="button" className="bt" onClick={() => jkl('l')} disabled={!viewer} title="Play, faster on repeat (L)">L ▶{rate > 1 ? ` ${rate}×` : ''}</button>
                  </span>
                  <select className="sel" aria-label="Viewer zoom" value={zoom} onChange={(e) => setZoom(e.target.value)} style={{ width: 96 }}>
                    <option value="fit">Fit</option><option value="50">50 %</option><option value="100">100 %</option><option value="200">200 %</option>
                  </select>
                  {selSeg && (
                    <span style={{ display: 'flex', gap: 6 }}>
                      <button type="button" className="bt" onClick={() => look(selSeg.src, selSeg.start)}>Go to in</button>
                      <button type="button" className="bt" onClick={() => look(selSeg.src, selSeg.end)}>Go to out</button>
                    </span>
                  )}
                  <span style={{ marginLeft: 'auto', display: 'flex', flexWrap: 'wrap', gap: 6 }}>
                    <button type="button" className="bt" onClick={markIn} disabled={!selSeg || viewer?.src !== selSeg.src}
                      title="Set the selected segment's in point to the viewer time">Mark in</button>
                    <button type="button" className="bt" onClick={markOut} disabled={!selSeg || viewer?.src !== selSeg.src}
                      title="Set the selected segment's out point to the viewer time">Mark out</button>
                    <button type="button" className="bt" onClick={findRepeats}
                      disabled={!selSeg || !base || isMovie(selSeg.src) || running || selSeg.end - selSeg.start < 2}
                      title="Find this scene's audio in the other episodes (flashbacks, reused footage)">Find repeats</button>
                    <button type="button" className="bt bp" onClick={addToCut} disabled={!viewer && !srcKeys.length}
                      title="Add a 60 s segment from the viewer time, after the selection">Add to cut</button>
                  </span>
                </div>
                {repeats && (
                  <div style={{ borderTop: '1px solid var(--nl)', padding: '8px 12px' }}>
                    <div className="row" style={{ color: 'var(--nm)' }}>
                      <span className="grow">Repeats of {repeats.src} {fmtTime(repeats.start)}–{fmtTime(repeats.end)}:{' '}
                        {repeats.matches == null ? 'searching…' : `${repeats.matches.length} found`}</span>
                      <button type="button" className="bt" onClick={() => setRepeats(null)} aria-label="Close repeats">✕</button>
                    </div>
                    {(repeats.matches || []).map((m) => (
                      <div key={`${m.episode_id}-${m.start}`} className="row" style={{ padding: '3px 0' }}>
                        <span className="mono grow">{m.episode_id} {fmtTime(m.start, 1)}–{fmtTime(m.end, 1)}</span>
                        <button type="button" className="bt" onClick={() => look(m.episode_id, m.start)}>View</button>
                        <button type="button" className="bt" onClick={() => {
                          addSeg(sel ? sel.pi : undefined, sel ? sel.si + 1 : undefined, { src: m.episode_id, start: m.start, end: m.end });
                        }}>Add to cut</button>
                      </div>
                    ))}
                  </div>
                )}
              </section>

              {/* Properties */}
              <section className="pn" aria-label="Properties" style={{ flex: '1 1 280px', maxWidth: 340 }}>
                <div className="ph"><span className="on">Properties: {selFlat ? `Segment ${selFlat.n}` : 'Cut'}</span></div>
                {!selSeg ? (
                  <div className="wide">
                    <div className="grp">
                      <b>The cut</b>
                      <dl className="kvp">
                        <dt>Title</dt><dd><input className="sel" aria-label="Title" value={doc.title}
                          onChange={(e) => edit((d) => { d.title = e.target.value; })} /></dd>
                        <dt>Parts</dt><dd className="mono">{doc.parts.length}</dd>
                        <dt>Segments</dt><dd className="mono">{flat.segs.length}</dd>
                        <dt>Runtime</dt><dd className="mono">{fmtTime(total)}</dd>
                        <dt>Sources</dt><dd className="mono">{srcKeys.length}{missing.length ? ` · ${missing.length} missing` : ''}</dd>
                      </dl>
                    </div>
                    <p style={{ margin: 0, color: 'var(--nm)' }}>Click a clip on the timeline to edit it. Drag clips to reorder them, or drag parts in the project list.</p>
                  </div>
                ) : (
                  <div className="wide">
                    <div className="grp">
                      <b>{selSeg.desc || `${srcName(selSeg.src)} ${fmtTime(selSeg.start)}`}</b>
                      <dl className="kvp">
                        <dt>Source</dt>
                        <dd><select className="sel" aria-label="Source" value={selSeg.src} onChange={(e) => setSeg('src', e.target.value)}>
                          {[...new Set([selSeg.src, ...srcOptions])].map((s) => <option key={s} value={s}>{s}{epById[s]?.title ? ` · ${epById[s].title}` : ''}</option>)}
                        </select></dd>
                        <dt>In</dt><dd><TimeInput key={`in${sel.pi}-${sel.si}-${selSeg.start}`} value={selSeg.start} label="In" onCommit={(v) => setSeg('start', v)} /></dd>
                        <dt>Out</dt><dd><TimeInput key={`out${sel.pi}-${sel.si}-${selSeg.end}`} value={selSeg.end} label="Out" onCommit={(v) => setSeg('end', v)} /></dd>
                        <dt>Length</dt><dd className="mono">{fmtTime(Math.max(0, selSeg.end - selSeg.start), 1)}</dd>
                        <dt>When</dt>
                        <dd><select className="sel" aria-label="When" value={selSeg.when || ''} onChange={(e) => setSeg('when', e.target.value)} disabled={!toggles.length}>
                          <option value="">Always</option>
                          {toggles.flatMap((t) => [
                            <option key={t.id} value={t.id}>With {t.label}</option>,
                            <option key={`!${t.id}`} value={`!${t.id}`}>Without {t.label}</option>,
                          ])}
                        </select></dd>
                        <dt>Crossfade</dt>
                        <dd><input className="sel mono" type="number" min="0" max="10000" step="50" aria-label="Crossfade ms"
                          placeholder="ms" value={selSeg.xfade_ms || ''} title="Audio crossfade from the previous segment, in milliseconds"
                          onChange={(e) => setSeg('xfade_ms', e.target.value ? Number(e.target.value) : null)} /></dd>
                        <dt>Description</dt>
                        <dd><textarea className="sel" rows={2} aria-label="Description" value={selSeg.desc || ''}
                          onChange={(e) => setSeg('desc', e.target.value)} /></dd>
                      </dl>
                    </div>
                    <SegmentFx seg={selSeg} set={setSegFx} first={sel.pi === 0 && sel.si === 0} />
                    <div className="grp">
                      <b>Part</b>
                      <dl className="kvp">
                        <dt>Title</dt><dd><input className="sel" aria-label="Part title" value={doc.parts[sel.pi].title}
                          onChange={(e) => edit((d) => { d.parts[sel.pi].title = e.target.value; })} /></dd>
                        <dt>Position</dt><dd className="mono">{sel.si + 1} of {doc.parts[sel.pi].segments.length}</dd>
                      </dl>
                    </div>
                    <div className="grp">
                      <b>Anchors</b>
                      <dl className="kvp">
                        <dt>In recipe</dt><dd>{selSeg.anchors ? 'yes' : 'none'}</dd>
                        <dt>Status</dt>
                        <dd style={{ color: segSync?.status === 'missing' ? 'var(--danger)' : segSync?.status === 'partial' ? 'var(--accent)' : undefined }}>
                          {!report?.synced ? 'not synced' : !segSync ? 'not in the report' : segSync.matched === false ? (segSync.reason || 'source not matched') : STATUS_TEXT[segSync.status] || '–'}
                        </dd>
                        <dt>Start</dt><dd className="mono" style={{ fontSize: 12, color: segSync?.start?.low_confidence || segSync?.start?.outlier ? 'var(--accent)' : undefined }}>{boundaryText(segSync?.start)}</dd>
                        <dt>End</dt><dd className="mono" style={{ fontSize: 12, color: segSync?.end?.low_confidence || segSync?.end?.outlier ? 'var(--accent)' : undefined }}>{boundaryText(segSync?.end)}</dd>
                      </dl>
                    </div>
                    {issues.filter((x) => x.pi === sel.pi && x.si === sel.si).map((x, i) => (
                      <p key={i} role="alert" style={{ margin: 0, color: 'var(--danger)' }}>{x.text}</p>
                    ))}
                    <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6 }}>
                      <button type="button" className="bt" onClick={() => shift(-1)} disabled={sel.si === 0}>Earlier</button>
                      <button type="button" className="bt" onClick={() => shift(1)} disabled={sel.si >= doc.parts[sel.pi].segments.length - 1}>Later</button>
                      <button type="button" className="bt" onClick={delSeg} style={{ color: 'var(--danger)' }}>Delete segment</button>
                      <button type="button" className="bt" style={{ color: 'var(--danger)' }}
                        onClick={() => { edit((d) => { d.parts.splice(sel.pi, 1); }); setSel(null); }}>Delete part</button>
                    </div>
                  </div>
                )}
              </section>
            </div>

            {/* Timeline */}
            <section className="pn" aria-label="Timeline" style={{ margin: '0 6px' }}>
              <div className="ph">
                <span className="on">{doc.title}</span>
                <span style={{ marginLeft: 'auto', fontSize: 12 }}>
                  {selFlat ? `Segment ${selFlat.n} · ${fmtTime(selFlat.at)}` : 'Click a clip to inspect it'} · {fmtTime(total)}
                </span>
              </div>
              <div className="tl">
                <div style={{ borderRight: '1px solid var(--nl)' }}>
                  <div style={{ display: 'flex', alignItems: 'center', height: 40, padding: '0 12px', borderBottom: '1px solid var(--nl)' }}>
                    <span className="tc" style={{ fontSize: 18 }}>{fmtTime(selFlat?.at ?? 0)}</span>
                  </div>
                  <div style={{ height: 24, borderBottom: '1px solid var(--nl)', padding: '0 12px', color: 'var(--nm)', fontSize: 12, display: 'flex', alignItems: 'center' }}>Joins</div>
                  <div className="tl-h" style={{ height: 22, color: 'var(--nm)', fontSize: 11 }} aria-hidden="true">
                    <span>On</span><span>#</span><span>Contents</span><span>fx</span>
                  </div>
                  {[
                    ['parts', 'P', 'Parts', `${doc.parts.length}`, null],
                    ['v2', 'V2', 'Titles', `${titled.length}`, '👁'],
                    ['v1', 'V1', 'Picture', `${withFx.length}/${flat.segs.length}`, '👁'],
                    ['a1', 'A1', 'Crossfades', `${flat.segs.filter((x) => x.seg.xfade_ms).length}`, '🔈'],
                    ['a2', 'A2', 'Music bridges', `${music.length}`, '🔈'],
                  ].map(([lid, num, name, fx, icon]) => (
                    <div key={lid} className="tl-h" style={lid === 'v1' ? { background: '#222A36' } : undefined}>
                      {icon ? <button type="button" className="sw-btn" aria-pressed={lane(lid)} aria-label={`Show ${name}`}
                        onClick={() => toggleLane(lid)}>{icon}</button> : <span />}
                      <span className="mono" style={{ color: 'var(--nm)' }}>{num}</span>
                      <span className="ell">{name}</span>
                      <span style={{ color: 'var(--nm)', fontSize: 12 }} title={lid === 'v1' ? 'segments with effects / all segments' : undefined}>{fx}</span>
                    </div>
                  ))}
                </div>
                <div style={{ position: 'relative', padding: '0 12px', minWidth: 0 }}>
                  <div className="mono" style={{ position: 'relative', height: 40, borderBottom: '1px solid var(--nl)', fontSize: 11, color: 'var(--nm)' }}>
                    {ticks.map((t) => <span key={t} style={{ position: 'absolute', bottom: 6, left: `${pct(t)}%` }}>{fmtTime(t)}</span>)}
                  </div>
                  <div style={{ position: 'relative', height: 24, borderBottom: '1px solid var(--nl)' }}>
                    {joins.map((j) => <span key={j.i} className="join" style={{ left: `${pct(j.at)}%`, top: 3 }} title={`${j.from} → ${j.to}`}>{j.from} → {j.to}</span>)}
                  </div>
                  <div style={{ position: 'relative' }}>
                    <div style={{ height: 22, borderBottom: '1px solid #242424' }} aria-hidden="true" />
                    <div className="lane">
                      {(() => {
                        let at = 0;
                        return doc.parts.map((p, pi) => {
                          const len = p.segments.reduce((a, s) => a + Math.max(0, s.end - s.start), 0);
                          const left = at; at += len;
                          return (
                            <button key={pi} type="button" className="clip" aria-pressed={sel?.pi === pi}
                              style={{ left: `${pct(left)}%`, width: `${pct(len)}%`, background: '#3A4A3F', boxShadow: 'inset 0 0 0 1px #9CC8A6' }}
                              aria-label={`Part ${pi + 1}: ${p.title}`} onClick={() => p.segments.length && select(pi, 0)}>{p.title}</button>
                          );
                        });
                      })()}
                    </div>
                    <div className={`lane${lane('v2') ? '' : ' hidden'}`} aria-label="Titles">
                      {titled.map((x) => (
                        <button key={`t${x.pi}-${x.si}`} type="button" className="mark" onClick={() => select(x.pi, x.si)}
                          title={`Title card on segment ${x.n}: “${x.seg.title.text}” for ${x.seg.title.duration} s`}
                          style={{ all: 'unset', cursor: 'pointer', position: 'absolute', top: 7, bottom: 7, borderRadius: 3, padding: '0 5px', fontSize: 11, lineHeight: '20px', overflow: 'hidden', whiteSpace: 'nowrap', textOverflow: 'ellipsis', color: '#141414',
                            left: `${pct(x.at)}%`, width: `max(40px, ${pct(Math.min(x.seg.title.duration, x.dur))}%)`, background: '#E6D3A3' }}>
                          T {x.seg.title.text}
                        </button>
                      ))}
                    </div>
                    <div className={`lane${lane('v1') ? '' : ' hidden'}`} style={{ background: '#1E232B' }}>
                      {flat.segs.map((x) => {
                        const tgt = { kind: 'seg', pi: x.pi, si: x.si };
                        const st = report?.synced ? report.sources?.[x.seg.src]?.segments?.[`${x.pi + 1}:${x.si + 1}`] : null;
                        return (
                          <button key={`${x.pi}-${x.si}`} type="button" {...dragProps(tgt)}
                            className={`clip${segOff(x.seg) || st === 'missing' ? ' off' : ''}${isOver(tgt) ? ' drop' : ''}`}
                            aria-pressed={sel?.pi === x.pi && sel?.si === x.si}
                            aria-label={`Segment ${x.n}: ${x.seg.src} ${fmtTime(x.seg.start)} to ${fmtTime(x.seg.end)}${x.seg.desc ? `, ${x.seg.desc}` : ''}`}
                            title={`${x.n}. ${x.seg.src} ${fmtTime(x.seg.start)}–${fmtTime(x.seg.end)}${x.seg.desc ? ` · ${x.seg.desc}` : ''}${x.seg.when ? ` · when ${x.seg.when}` : ''}${st ? ` · ${STATUS_TEXT[st]}` : ''}`}
                            style={{ left: `${pct(x.at)}%`, width: `${pct(x.dur)}%`, background: missingSet.has(x.seg.src) ? '#444' : colorOf(x.seg.src) }}
                            onClick={() => select(x.pi, x.si)}>
                            {x.n} {x.seg.desc || x.seg.src}
                            {fxList(x.seg).length > 0 && <span className="fxdot" title={fxList(x.seg).join(', ')}>fx</span>}
                          </button>
                        );
                      })}
                    </div>
                    <div className={`lane${lane('a1') ? '' : ' hidden'}`}>
                      {flat.segs.filter((x) => x.seg.xfade_ms).map((x) => (
                        <span key={`${x.pi}-${x.si}`} title={`${x.seg.xfade_ms} ms crossfade into segment ${x.n}`}
                          style={{ position: 'absolute', top: 10, left: `${pct(x.at)}%`, width: 9, height: 9, background: '#E6E6E6', transform: 'translateX(-4px) rotate(45deg)' }} />
                      ))}
                    </div>
                    <div className={`lane${lane('a2') ? '' : ' hidden'}`} aria-label="Music bridges">
                      {music.map((x) => (
                        <span key={`a${x.pi}-${x.si}`} className="mark" title={`Music from ${x.seg.audio.src} at ${fmtTime(x.seg.audio.start || 0)} under segment ${x.n}`}
                          style={{ left: `${pct(x.at)}%`, width: `${pct(x.dur)}%`, background: '#9CC8A6' }}>♪ {x.seg.audio.src}</span>
                      ))}
                    </div>
                    {joins.map((j) => (
                      <span key={`l${j.i}`} aria-hidden="true" style={{ position: 'absolute', top: -24, bottom: 0, left: `${pct(j.at)}%`, borderLeft: '1px dashed #F2A93B', pointerEvents: 'none' }} />
                    ))}
                    {selFlat && (
                      <span aria-hidden="true" style={{ position: 'absolute', top: -64, bottom: 0, width: 2, left: `${pct(selFlat.at)}%`, background: 'var(--nblue)', pointerEvents: 'none' }} />
                    )}
                  </div>
                  {flat.segs.length === 0 && <p style={{ color: 'var(--nm)', margin: '10px 0' }}>No segments yet. Use + Segment, Add to cut or AI outline.</p>}
                </div>
              </div>
            </section>
          </>
        )}

        {view === 'colour' && (
          <section className="pn" aria-label="Colour" style={{ margin: '0 6px' }}>
            <div className="ph">
              <span className="on">Colour</span>
              <span>{flat.segs.filter((x) => x.seg.video?.grade).length} of {flat.segs.length} segments graded</span>
            </div>
            {flat.segs.length === 0 ? <p style={{ padding: 14, margin: 0, color: 'var(--nm)' }}>No segments yet.</p> : (
              <div style={{ display: 'flex', flexWrap: 'wrap' }}>
                <ol className="list" style={{ flex: '0 1 260px', maxHeight: 520, overflowY: 'auto', borderRight: '1px solid var(--nl)' }}>
                  {flat.segs.map((x) => (
                    <li key={`${x.pi}-${x.si}`}>
                      <button type="button" className="prow" aria-pressed={sel?.pi === x.pi && sel?.si === x.si} onClick={() => select(x.pi, x.si)}>
                        <span className="mono" style={{ color: 'var(--nm)', fontSize: 11 }}>{x.n}</span>
                        <span className="sw" style={{ background: colorOf(x.seg.src) }} />
                        <span className="ell">{x.seg.desc || `${x.seg.src} ${fmtTime(x.seg.start)}`}</span>
                        <span style={{ color: x.seg.video?.grade ? 'var(--nlink)' : 'var(--nm)', fontSize: 12 }}>{x.seg.video?.grade ? 'graded' : ''}</span>
                      </button>
                    </li>
                  ))}
                </ol>
                <div style={{ flex: '1 1 520px', minWidth: 0 }}>
                  {selSeg
                    ? <GradePanel tid={id} seg={selSeg} set={setSegFx} onCopy={copyGrade} saved={!dirty || Boolean(data?.doc?.sources?.[selSeg.src])}
                        sameSource={flat.segs.filter((x) => x.seg.src === selSeg.src).length} />
                    : <p style={{ padding: 14, margin: 0, color: 'var(--nm)' }}>Pick a segment to grade it.</p>}
                </div>
              </div>
            )}
          </section>
        )}

        {view === 'sync' && (
          <section className="pn" aria-label="Sync" style={{ margin: '0 6px' }}>
            <div className="ph">
              <span className="on">Sync report</span>
              {report?.updated_at && <span>last run {report.updated_at.replace('T', ' ')}</span>}
              <span style={{ marginLeft: 'auto' }}>
                <button type="button" className="bt bp" onClick={sync} disabled={busy || running}>{report?.synced ? 'Sync again' : 'Sync'}</button>
              </span>
            </div>
            <div className="wide">
              {dirty && <p style={{ margin: 0, color: 'var(--accent)' }}>You have unsaved changes. Save first: sync runs on the saved recipe.</p>}
              <SyncReport report={report} onPreview={(src, t) => { look(src, t); setView('edit'); }} />
            </div>
          </section>
        )}

        {view === 'render' && (
          <section className="pn" aria-label="Render" style={{ margin: '0 6px' }}>
            <div className="ph">
              <span className="on">Render check</span>
              <span style={{ marginLeft: 'auto', display: 'flex', gap: 6 }}>
                <button type="button" className="bt" onClick={dryRun} disabled={busy}>Dry run</button>
                <Link to={`/cuts/${enc(id)}/build`} state={crumb.state} className="bt bp">Render…</Link>
              </span>
            </div>
            <div className="wide">
              {dirty && <p style={{ margin: 0, color: 'var(--accent)' }}>Unsaved changes are not part of a render. Save first.</p>}
              {!report?.synced && <p style={{ margin: 0, color: 'var(--accent)' }}>Not synced yet. Rendering needs a sync.</p>}
              {varQ.data?.variants?.length > 0 && (
                <table className="table">
                  <thead><tr><th>Variant</th><th>Runtime</th><th>Ready</th></tr></thead>
                  <tbody>
                    {varQ.data.variants.map((v) => (
                      <tr key={v.id} aria-current={v.id === variant ? 'true' : undefined}>
                        <td>{v.label}{v.id === variant ? ' (selected)' : ''}</td>
                        <td className="mono">{fmtDuration(v.runtime)}</td>
                        <td>{v.complete ? <span className="tag tag-ok">ready</span>
                          : <span className="tag tag-warn">needs {(v.blocked || []).join(', ') || 'files'}</span>}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              )}
              {dry && (
                <div>
                  <h3 style={{ marginBottom: 6 }}>Dry run · {dry.show} · {fmtTime(dry.runtime)}</h3>
                  {dry.parts.map((p) => (
                    <div key={p.part} style={{ padding: '4px 0', color: 'var(--nc)' }}>
                      <b>{p.title}</b>{' '}
                      {p.skipped ? <span className="muted">skipped (nothing to render)</span> : (
                        <>— {fmtTime(p.runtime)} · {p.segments.length} segment(s) · {p.mode}{' '}<span className="mono tiny">{p.output}</span></>
                      )}
                      {(p.warnings || []).map((w, i) => <div key={i} className="tiny c-warn">{w}</div>)}
                    </div>
                  ))}
                </div>
              )}
              {!dry && <p style={{ margin: 0, color: 'var(--nm)' }}>A dry run shows each part's plan (copy or re-encode, runtime, output file) without rendering.</p>}
            </div>
          </section>
        )}
      </div>

      {aiOpen && base && (
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
    </div>
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
      <p className="muted">Paste wiki chronology or community notes. The AI orders scenes; you set the exact in and out points afterwards.</p>
      <textarea className="input" rows={8} value={notes} onChange={(e) => setNotes(e.target.value)} aria-label="Chronology notes" />
      {error && <div className="notice notice-err" role="alert">{error}</div>}
      {outline && <p className="muted">{outline.length} part(s), {outline.reduce((a, p) => a + p.scenes.length, 0)} scene(s). They load as placeholder segments.</p>}
      <div className="row" style={{ justifyContent: 'flex-end' }}>
        <button className="btn" onClick={run} disabled={busy || !notes.trim()}>Generate</button>
        {outline && <button className="btn btn-primary" onClick={load}>Load as placeholders</button>}
      </div>
    </Dialog>
  );
}
