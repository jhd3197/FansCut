// Build a cut: check files, find cut points, review the weak ones, then render.
import { useCallback, useState } from 'react';
import { Link, useNavigate, useParams, useSearchParams } from 'react-router';
import { api } from '../lib/api.js';
import { useAct, useLoad, usePoll } from '../lib/hooks.js';
import { fmtTime } from '../lib/time.js';
import { Crumbs, enc, hm, SRC_COLOURS } from '../components/kit.jsx';
import { Dialog, ErrorBox, Loading, Notice, Progress } from '../components/ui.jsx';
import { useApi } from '../lib/store.js';

const ACTIVE = ['queued', 'running'];
const STEP_STYLE = {
  done: ['var(--ready)', 'Done', 'var(--text)'],
  now: ['var(--accent)', 'Now', 'var(--text)'],
  running: ['var(--accent)', 'Running', 'var(--text)'],
  blocked: ['var(--danger)', 'Blocked', 'var(--text)'],
  todo: ['var(--line)', null, 'var(--muted)'],
};

function Stepper({ steps }) {
  return (
    <ol aria-label="Progress" style={{ listStyle: 'none', margin: 0, padding: 0, display: 'flex', flexWrap: 'wrap', gap: 16 }}>
      {steps.map(([label, state], i) => {
        const [bar, text, colour] = STEP_STYLE[state];
        return (
          <li key={label} aria-current={state === 'now' ? 'step' : undefined} style={{ flex: '1 1 140px', display: 'flex', flexDirection: 'column', gap: 8 }}>
            <span style={{ height: 3, borderRadius: 2, background: bar }} />
            <span className="tiny" style={{ color: state === 'todo' ? 'var(--muted)' : bar }}>{text || `Step ${i + 1}`}</span>
            <span style={{ fontWeight: 600, color: colour }}>{label}</span>
          </li>
        );
      })}
    </ol>
  );
}

// Every cut point the sync wasn't sure about, plus segments missing from the user's edition.
export function weakPoints(report) {
  const points = [];
  let unanchored = 0;
  for (const [src, s] of Object.entries(report?.sources || {})) {
    for (const b of s.boundaries || []) {
      if (!b.anchored) { unanchored += 1; continue; }
      let why = null;
      if (b.method === 'none') why = 'not found';
      else if (b.outlier) why = 'out of step with the rest';
      else if (b.low_confidence) why = 'weak match';
      if (why) points.push({ key: `${src}:${b.part}:${b.segment}:${b.side}`, src, ...b, why });
    }
  }
  const gone = [];
  for (const [src, s] of Object.entries(report?.sources || {})) {
    for (const [ps, st] of Object.entries(s.segments || {})) {
      if (st === 'missing') gone.push({ src, ps });
    }
  }
  return { points, unanchored, gone };
}

const signed = (x) => `${x >= 0 ? '+' : '−'}${Math.abs(x).toFixed(3)} s`;

function median(xs) {
  if (!xs.length) return null;
  const s = [...xs].sort((a, b) => a - b);
  const m = Math.floor(s.length / 2);
  return s.length % 2 ? s[m] : (s[m - 1] + s[m]) / 2;
}

function Copies({ doc, missing, report, colour }) {
  const missingSet = new Set(missing.map((m) => m.src));
  const srcs = Object.keys(doc.sources);
  const offsets = srcs.map((src) => median((report?.sources?.[src]?.boundaries || []).map((b) => b.offset).filter((o) => o != null)));
  const same = report && srcs.every((src, i) => report.sources?.[src]?.matched && offsets[i] != null && Math.abs(offsets[i]) < 0.05);
  const note = !report ? 'Not matched yet' : same ? 'Same release as the recipe’s author' : 'Offsets are corrected per file';
  return (
    <section aria-labelledby="i-src" className="card" style={{ overflow: 'hidden', padding: 0 }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline', padding: '14px 16px', gap: 10, flexWrap: 'wrap' }}>
        <h2 id="i-src">Your copies</h2>
        <span className="muted" style={{ fontSize: 13 }}>{note}</span>
      </div>
      <ul style={{ listStyle: 'none', margin: 0, padding: 0 }}>
        {srcs.map((src, i) => {
          const s = doc.sources[src];
          const r = report?.sources?.[src];
          let match;
          if (missingSet.has(src)) match = <span className={`tiny ${s.optional ? 'c-accent' : 'c-danger'}`}>{s.optional ? 'Not in library (optional)' : 'Not in library'}</span>;
          else if (!r) match = <span className="mono tiny muted">not matched yet</span>;
          else if (!r.matched && r.reason) match = <span className="tiny c-danger">{r.reason}</span>;
          else match = <span className="mono tiny">{r.matched_boundaries ?? 0} of {r.total_boundaries ?? 0} matched</span>;
          return (
            <li key={src} style={{ display: 'grid', gridTemplateColumns: '64px minmax(0, 1fr) 170px 130px', gap: 12, padding: '10px 16px', borderTop: '1px solid var(--raised)', alignItems: 'center' }}>
              <span className="mono tiny" style={{ color: colour[src], overflow: 'hidden', textOverflow: 'ellipsis' }}>{src}</span>
              <span style={{ minWidth: 0 }}>{s.note || src}{s.edition ? <span className="muted tiny"> · {s.edition}</span> : null}</span>
              {match}
              <span className="mono tiny muted">
                {offsets[i] != null ? `offset ${signed(offsets[i])}` : ''}
                {r?.strategy === 'linear' ? ' · drift' : r?.strategy === 'per-boundary' ? ' · jumps' : ''}
              </span>
            </li>
          );
        })}
      </ul>
    </section>
  );
}

function Review({ doc, weak, checked, onCheck, onPreview, editTo }) {
  const { points, unanchored, gone } = weak;
  const open = points.filter((p) => !checked[p.key]);
  const isEpisode = (src) => doc.sources[src]?.kind !== 'movie';
  const shown = points.slice(0, 12);
  return (
    <section aria-labelledby="i-check" className="card" style={{ display: 'flex', flexDirection: 'column', gap: 14, borderColor: open.length ? 'var(--warn-line)' : undefined }}>
      <div style={{ display: 'flex', flexWrap: 'wrap', justifyContent: 'space-between', gap: 10, alignItems: 'baseline' }}>
        <h2 id="i-check">
          {points.length === 0 ? 'Every cut point matched' : `${open.length} cut point${open.length === 1 ? '' : 's'} to check`}
        </h2>
        {points.length > 0 && <span className="mono tiny c-accent">{points.length - open.length} of {points.length} checked</span>}
      </div>
      {points.length > 0 && (
        <p style={{ margin: 0, color: 'var(--text2)', fontSize: 13 }}>
          FansCut found these points but isn’t sure. Watch a few seconds around each one, then confirm it, or nudge it in the editor.
        </p>
      )}
      {shown.length > 0 && (
        <ul style={{ listStyle: 'none', margin: 0, padding: 0, display: 'flex', flexDirection: 'column' }}>
          {shown.map((p) => {
            const seg = doc.parts[p.part - 1]?.segments[p.segment - 1];
            return (
              <li key={p.key} style={{ display: 'flex', flexWrap: 'wrap', gap: 10, alignItems: 'center', padding: '8px 0', borderTop: '1px solid var(--raised)' }}>
                <span style={{ flex: '1 1 260px', minWidth: 0, display: 'flex', flexDirection: 'column', gap: 2 }}>
                  <span>{doc.parts.length > 1 ? `Part ${p.part} · ` : ''}Segment {p.segment} {p.side}{seg?.desc ? ` · ${seg.desc}` : ''}</span>
                  <span className="mono tiny c-accent">{p.src} {fmtTime(p.corrected ?? p.t, 1)} · {p.why}</span>
                </span>
                {isEpisode(p.src)
                  ? <button type="button" className="btn btn-sm" onClick={() => onPreview(p.src, p.corrected ?? p.t)}>Play 10 s around it</button>
                  : <Link className="btn btn-sm" to={editTo}>Open in editor</Link>}
                {checked[p.key]
                  ? <button type="button" className="btn btn-sm btn-ghost" onClick={() => onCheck([p.key], false)}>Checked · undo</button>
                  : <button type="button" className="btn btn-sm btn-primary" onClick={() => onCheck([p.key], true)}>Looks right</button>}
              </li>
            );
          })}
        </ul>
      )}
      {points.length > shown.length && (
        <div className="row wrap" style={{ justifyContent: 'space-between' }}>
          <span className="muted tiny">{points.length - shown.length} more weak point(s). Open the editor to go through them one by one.</span>
          <button type="button" className="btn btn-sm" onClick={() => onCheck(points.map((p) => p.key), true)}>Mark all as checked</button>
        </div>
      )}
      {gone.length > 0 && (
        <p className="tiny c-warn" style={{ margin: 0 }}>
          {gone.length} segment{gone.length === 1 ? '' : 's'} not in your edition ({gone.map((g) => `${g.src} part ${g.ps.replace(':', ' segment ')}`).join(', ')}): {gone.length === 1 ? 'it is' : 'they are'} skipped.
        </p>
      )}
      {unanchored > 0 && (
        <p className="tiny muted" style={{ margin: 0 }}>
          {unanchored} cut point{unanchored === 1 ? '' : 's'} carry no fingerprints, so the recipe’s times are used as they are.
        </p>
      )}
    </section>
  );
}

function BuildPlan({ dry, error, loading }) {
  if (error) return <ErrorBox error={error} />;
  if (!dry) return loading ? <Loading what="Planning the build" /> : null;
  const parts = dry.parts || [];
  const live = parts.filter((p) => !p.skipped);
  const copy = live.filter((p) => p.mode === 'copy').reduce((a, p) => a + p.runtime, 0);
  const encode = live.filter((p) => p.mode !== 'copy').reduce((a, p) => a + p.runtime, 0);
  const warnings = parts.flatMap((p) => p.warnings || []);
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
      <span className="tiny muted">Where the time goes</span>
      {copy + encode > 0 && (
        <div style={{ display: 'flex', height: 12, borderRadius: 3, overflow: 'hidden' }} aria-hidden="true">
          {copy > 0 && <span style={{ flex: copy, background: 'var(--ready)' }} />}
          {encode > 0 && <span style={{ flex: encode, background: 'var(--accent)' }} />}
        </div>
      )}
      <dl className="kv">
        {copy > 0 && (<><dt><span className="c-ready">■</span> Copy</dt><dd>{fmtTime(copy)} copied as-is: fast, minutes</dd></>)}
        {encode > 0 && (<><dt><span className="c-accent">■</span> Re-encode</dt><dd>{fmtTime(encode)} re-encoded: most of the time</dd></>)}
        <dt>Runtime</dt><dd>{hm(dry.runtime / 60)} · {dry.layout === 'movie' ? 'one film' : `${live.length} episode${live.length === 1 ? '' : 's'}`}</dd>
        {live.map((p) => (
          <div key={p.part} style={{ display: 'contents' }}>
            <dt>{parts.length > 1 ? `Part ${p.part}` : 'Output'}</dt>
            <dd>
              <span className="mono tiny" style={{ overflowWrap: 'anywhere' }}>{p.output}</span>
              <div className="tiny muted">{p.mode === 'copy' ? 'Copy' : 'Re-encode'} · {p.reason}</div>
            </dd>
          </div>
        ))}
        {parts.filter((p) => p.skipped).map((p) => (
          <div key={p.part} style={{ display: 'contents' }}><dt>Part {p.part}</dt><dd className="muted">Nothing to build: skipped</dd></div>
        ))}
      </dl>
      {warnings.length > 0 && (
        <ul className="tiny c-warn" style={{ margin: 0, paddingLeft: 18 }}>
          {warnings.map((w, i) => <li key={i}>{w}</li>)}
        </ul>
      )}
    </div>
  );
}

export function Build() {
  const { id } = useParams();
  const [search] = useSearchParams();
  const nav = useNavigate();
  const tl = useApi(`/timelines/${enc(id)}`);
  const sync = useApi(`/timelines/${enc(id)}/sync`);
  const variants = useApi(`/timelines/${enc(id)}/variants`);
  const settings = useApi('/settings');
  const jobs = useApi('/jobs');
  const [variant, setVariant] = useState(search.get('variant') || '');
  const dry = useLoad(() => api(`/timelines/${enc(id)}/render`, { method: 'POST', read: true, body: { dry_run: true, variant: variant || null } }), [id, variant]);
  const [keep, setKeep] = useState(null);
  const [checked, setChecked] = useState({});
  const [job, setJob] = useState(null);
  const [preview, setPreview] = useState(null);
  const { act, msg, busy, clear } = useAct();

  const pollJob = useCallback(async () => {
    if (!job) return;
    const j = await api(`/jobs/${job.id}`).catch(() => job);
    setJob(j);
    if (!ACTIVE.includes(j.status)) { sync.reload(); variants.reload(); dry.reload(); jobs.reload(); }
  }, [job]); // eslint-disable-line react-hooks/exhaustive-deps
  usePoll(pollJob, 1000, job && ACTIVE.includes(job.status));

  if (tl.error && !tl.data) return <div className="page"><ErrorBox error={tl.error} onRetry={tl.reload} /></div>;
  if (!tl.data || !sync.data) return <div className="page"><Loading /></div>;

  const doc = tl.data.doc;
  const missing = tl.data.missing || [];
  const report = sync.data.synced ? sync.data : null;
  const weak = weakPoints(report);
  const open = weak.points.filter((p) => !checked[p.key]);
  const required = missing.filter((m) => !doc.sources[m.src]?.optional);
  const syncing = job && job.kind === 'timeline_sync' && ACTIVE.includes(job.status);
  const lastRender = (jobs.data?.jobs || []).find((j) => j.kind === 'timeline_render' && j.payload?.timeline_id === id);
  const rendering = lastRender && ACTIVE.includes(lastRender.status);
  const built = lastRender?.status === 'done';
  const keepValue = keep ?? settings.data?.render?.keep ?? true;
  const plex = Boolean(settings.data?.library?.plex?.url);
  const colour = {};
  Object.keys(doc.sources).forEach((s, i) => { colour[s] = SRC_COLOURS[i % SRC_COLOURS.length]; });
  const rows = variants.data?.variants || [];
  const chosen = rows.find((v) => v.id === variant) || rows[0];
  const editTo = `/cuts/${enc(id)}/edit`;

  const steps = [
    ['Check files', required.length ? 'blocked' : 'done'],
    ['Find cut points', report ? 'done' : syncing ? 'running' : required.length ? 'todo' : 'now'],
    ['Review', !report ? 'todo' : open.length ? 'now' : 'done'],
    ['Build', rendering ? 'running' : built ? 'done' : report && !open.length ? 'now' : 'todo'],
    ['Add to Plex', built && plex ? 'done' : 'todo'],
  ];

  const runSync = () => act(async () => setJob(await api(`/timelines/${enc(id)}/sync`, { method: 'POST' })), 'Matching cut points against your files');
  const start = () => act(async () => {
    await api(`/timelines/${enc(id)}/render`, { method: 'POST', body: { variant: variant || null, keep: keepValue } });
    nav('/jobs');
  });
  const onCheck = (keys, on) => setChecked((c) => {
    const next = { ...c };
    for (const k of keys) next[k] = on;
    return next;
  });

  let blocker = '';
  if (required.length) blocker = `Missing ${required.map((m) => m.note || m.src).join(', ')}`;
  else if (!report) blocker = 'Find the cut points first';
  else if (open.length) blocker = `Confirm the cut point${open.length === 1 ? '' : 's'} to continue`;
  else if (rendering) blocker = 'A build of this cut is already running';

  return (
    <div className="page" style={{ maxWidth: 980 }}>
      <Crumbs items={[['My cuts', '/my-cuts'], [doc.title, `/cuts/${enc(id)}`], ['Build']]} />
      <Stepper steps={steps} />
      <Notice msg={msg} onClose={clear} />

      {required.length > 0 && (
        <div className="notice notice-err" role="alert">
          <span>Your library is missing {required.map((m) => m.note || m.src).join(', ')}. Add {required.length === 1 ? 'it' : 'them'} and refresh the library, then come back.</span>
        </div>
      )}

      <Copies doc={doc} missing={missing} report={report} colour={colour} />

      {!report && (
        <section aria-labelledby="i-sync" className="card" style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
          <h2 id="i-sync">Find cut points</h2>
          <p style={{ margin: 0, color: 'var(--text2)', fontSize: 13 }}>
            FansCut listens to your copies and lines up every cut point in the recipe with them. Nothing is cut yet.
          </p>
          {syncing && (
            <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
              <Progress value={job.progress} />
              <span className="mono tiny muted">{Math.round((job.progress || 0) * 100)}% · {job.message || 'waiting to start'}</span>
            </div>
          )}
          {job?.status === 'failed' && <span className="tiny c-danger">{job.message}</span>}
          <div className="row">
            <button type="button" className="btn btn-primary" onClick={runSync} disabled={busy || syncing || required.length > 0}>
              {syncing ? 'Matching…' : 'Run sync'}
            </button>
          </div>
        </section>
      )}

      {report && (
        <Review doc={doc} weak={weak} checked={checked} onCheck={onCheck}
          onPreview={(src, t) => setPreview({ src, t })} editTo={editTo} />
      )}

      <section aria-labelledby="i-render" className="card" style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
        <div style={{ display: 'flex', flexWrap: 'wrap', justifyContent: 'space-between', gap: 10, alignItems: 'baseline' }}>
          <h2 id="i-render">Build</h2>
          <span className="muted" style={{ fontSize: 13 }}>Planned on this PC from your files</span>
        </div>
        {rows.length > 1 && (
          <fieldset style={{ flexDirection: 'row', flexWrap: 'wrap', gap: 10 }}>
            <legend style={{ position: 'absolute', left: -9999 }}>Variant</legend>
            {rows.map((v) => (
              <label key={v.id || 'default'} className="choice" style={{ flex: '1 1 260px' }}>
                <input type="radio" name="variant" checked={(chosen?.id ?? '') === v.id} onChange={() => setVariant(v.id)} style={{ margin: '3px 0 0' }} />
                <span style={{ display: 'flex', flexDirection: 'column', gap: 2 }}>
                  <b style={{ fontWeight: 600 }}>{v.label}</b>
                  <span className="tiny muted">{hm(v.runtime / 60)}</span>
                  {v.blocked.map((b) => <span key={b.id} className="tiny c-danger">{b.label}: {b.reason}</span>)}
                </span>
              </label>
            ))}
          </fieldset>
        )}
        <BuildPlan dry={dry.data} error={dry.error} loading={dry.loading} />
        {settings.data && (
          <div style={{ display: 'flex', flexDirection: 'column', gap: 6, borderTop: '1px solid var(--raised)', paddingTop: 14 }}>
            <span className="tiny muted">Speed-ups</span>
            <ul style={{ listStyle: 'none', margin: 0, padding: 0, display: 'flex', flexDirection: 'column', gap: 6, fontSize: 13 }}>
              <li style={{ display: 'flex', gap: 10 }}><span className="c-ready" style={{ width: 56 }}>On</span><span>Reuse parts already built with the same cut points</span></li>
              <li style={{ display: 'flex', gap: 10 }}>
                <span className={settings.data.ffmpeg?.nvenc ? 'c-ready' : 'c-accent'} style={{ width: 56 }}>{settings.data.ffmpeg?.nvenc ? 'On' : 'Off'}</span>
                <span>GPU encoder (NVENC){settings.data.ffmpeg?.nvenc ? '' : <> · <Link to="/settings">Turn on</Link></>}</span>
              </li>
            </ul>
          </div>
        )}
        <div style={{ display: 'flex', flexDirection: 'column', gap: 10, fontSize: 13, borderTop: '1px solid var(--raised)', paddingTop: 14 }}>
          <label className="check">
            <input type="checkbox" checked={keepValue} onChange={(e) => setKeep(e.target.checked)} />
            Keep the file{settings.data?.render?.ephemeral_days ? ` (otherwise it’s cleaned up after ${settings.data.render.ephemeral_days} days)` : ''}
          </label>
          {plex && <span className="muted">When it’s done, FansCut refreshes Plex and adds it to the “FansCut” collection.</span>}
        </div>
      </section>

      <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8, justifyContent: 'space-between', alignItems: 'center' }}>
        <Link className="btn" to={`/cuts/${enc(id)}`}>Back</Link>
        <div className="row wrap">
          {blocker && <span className="muted" style={{ fontSize: 13 }}>{blocker}</span>}
          {rendering && <Link className="btn" to="/jobs">Open in Jobs</Link>}
          <button type="button" className="btn btn-primary" onClick={start} disabled={busy || Boolean(blocker)}>
            Start build
          </button>
        </div>
      </div>

      {preview && (
        <Dialog title={`${preview.src} at ${fmtTime(preview.t, 1)}`} onClose={() => setPreview(null)}>
          <video controls autoPlay style={{ width: '100%' }}
            src={`/api/series/${enc(tl.data.series_key)}/episodes/${enc(preview.src)}/preview?t=${preview.t}`} />
        </Dialog>
      )}
    </div>
  );
}
