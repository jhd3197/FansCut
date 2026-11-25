// Background work: what's running now, what finished, and the files builds left behind.
import { useState } from 'react';
import { Link } from 'react-router';
import { api } from '../lib/api.js';
import { useAct, useLoad, usePoll } from '../lib/hooks.js';
import { enc } from '../components/kit.jsx';
import { Dialog, Empty, ErrorBox, Loading, Notice } from '../components/ui.jsx';

const ACTIVE = ['queued', 'running'];

const fmtSize = (n) => {
  if (!n) return '–';
  return n >= 1e9 ? `${(n / 1e9).toFixed(1)} GB` : `${Math.max(1, Math.round(n / 1e6))} MB`;
};

// "today 05:12" / "yesterday 21:40" / "2026-11-20 08:15"
export function when(iso, now = new Date()) {
  if (!iso) return '–';
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso.replace('T', ' ');
  const hhmm = `${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`;
  const day = (x) => new Date(x.getFullYear(), x.getMonth(), x.getDate()).getTime();
  const diff = Math.round((day(now) - day(d)) / 86400000);
  if (diff === 0) return `today ${hhmm}`;
  if (diff === 1) return `yesterday ${hhmm}`;
  return iso.slice(0, 16).replace('T', ' ');
}

// Stage list for the running card, from the job's kind and its latest message.
export function stages(job) {
  const running = job.status === 'running';
  const msg = job.message || '';
  const at = (names, i) => names.map((n, k) => [n, job.status === 'done' || k < i ? 'Done' : k === i && running ? 'Running' : 'Waiting']);
  switch (job.kind) {
    case 'timeline_render': {
      const m = msg.match(/part (\d+)\/(\d+)/);
      const names = ['Check files', 'Cut parts', 'Write poster and add to Plex'];
      if (m) names[1] = `Cut parts (${m[1]} of ${m[2]})`;
      return at(names, m ? 1 : 0);
    }
    case 'timeline_sync':
      return at(['Read the recipe', 'Match cut points', 'Save the report'], msg ? 1 : 0);
    case 'render':
      return at(['Plan the cut', 'Render', 'Save'], msg.startsWith('rendering') ? 1 : 0);
    case 'detect':
      return at(['Read episode audio', 'Find opening and ending', 'Save'], (job.progress || 0) > 0 ? 1 : 0);
    case 'find_similar':
      return at(['Read the scene', 'Search every episode'], msg ? 1 : 0);
    default:
      return [];
  }
}

const KIND = { timeline_render: 'Build', timeline_sync: 'Sync', render: 'Render' };
const jobName = (j) => (KIND[j.kind] ? `${KIND[j.kind]} · ${j.title.replace(/^(Render|Sync) /, '')}` : j.title);

// Where a finished job's result is best looked at.
export function jobLink(job) {
  const p = job.payload || {};
  if (job.kind === 'timeline_sync' && p.timeline_id) return [`/cuts/${enc(p.timeline_id)}/build`, 'Review'];
  if (job.kind === 'timeline_render' && p.timeline_id) return [`/cuts/${enc(p.timeline_id)}`, 'Open'];
  if (job.kind === 'detect' && p.series_key) {
    return [p.season != null ? `/series/${enc(p.series_key)}/season/${p.season}` : `/series/${enc(p.series_key)}`, 'Open'];
  }
  if (job.kind === 'find_similar' && p.series_key && p.episode_id) return [`/series/${enc(p.series_key)}/episode/${enc(p.episode_id)}`, 'Open'];
  if (p.series_key) return [`/series/${enc(p.series_key)}`, 'Open'];
  return null;
}

function Running({ job, queued, onCancel, busy }) {
  const pct = Math.round(Math.min(Math.max(job.progress || 0, 0), 1) * 100);
  const steps = stages(job);
  return (
    <section aria-label="Running" className="card" style={{ padding: 20, display: 'flex', flexDirection: 'column', gap: 16 }}>
      <div style={{ display: 'flex', flexWrap: 'wrap', gap: 12, alignItems: 'baseline', justifyContent: 'space-between' }}>
        <div style={{ display: 'flex', flexDirection: 'column', gap: 2, minWidth: 0 }}>
          <h2 style={{ fontSize: 16, fontWeight: 700 }}>{jobName(job)}</h2>
          <span className="muted" style={{ fontSize: 13, overflowWrap: 'anywhere' }}>
            {job.status === 'queued' ? 'Waiting for the job before it' : job.message || 'Starting'}
          </span>
        </div>
        <div className="row">
          <span className="mono" style={{ fontSize: 14 }}>{pct}%</span>
          <button className="btn" type="button" disabled={busy} onClick={() => onCancel(job)}>Cancel</button>
        </div>
      </div>
      <div role="progressbar" aria-valuenow={pct} aria-valuemin={0} aria-valuemax={100} aria-label={`${job.title} progress`}
        style={{ height: 6, borderRadius: 3, background: 'var(--raised)', overflow: 'hidden' }}>
        <div style={{ width: `${pct}%`, height: '100%', background: 'var(--accent)', transition: 'width .3s' }} />
      </div>
      {steps.length > 0 && (
        <ol style={{ listStyle: 'none', margin: 0, padding: 0, display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(min(150px, 100%), 1fr))', gap: 10, fontSize: 13 }}>
          {steps.map(([name, st]) => (
            <li key={name} style={{ display: 'flex', flexDirection: 'column', gap: 2 }}>
              <span className={st === 'Done' ? 'c-ready' : st === 'Running' ? 'c-accent' : 'muted'}>{st}</span>
              <span>{name}</span>
            </li>
          ))}
        </ol>
      )}
      {queued.length > 0 && (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 6, borderTop: '1px solid var(--raised)', paddingTop: 12 }}>
          <span className="tiny muted">Up next</span>
          {queued.map((q) => (
            <div key={q.id} className="row" style={{ justifyContent: 'space-between', fontSize: 13 }}>
              <span>{q.title}</span>
              <button className="btn btn-sm btn-ghost" type="button" disabled={busy} onClick={() => onCancel(q)}>Cancel</button>
            </div>
          ))}
        </div>
      )}
    </section>
  );
}

function Recent({ jobs, onRemove, busy }) {
  return (
    <section aria-labelledby="recent-h" className="section" style={{ gap: 12 }}>
      <h2 id="recent-h">Recent</h2>
      {jobs.length === 0 ? <Empty>No finished jobs yet.</Empty> : (
        <div className="scroll-x" style={{ border: '1px solid var(--line)', borderRadius: 10 }}>
          <table className="table" style={{ minWidth: 640 }}>
            <thead><tr><th>Job</th><th>Result</th><th>Finished</th><th /></tr></thead>
            <tbody>
              {jobs.map((j) => {
                const link = j.status === 'done' ? jobLink(j) : null;
                const result = j.result?.message || j.message || j.status;
                const cls = j.status === 'failed' ? 'c-danger' : j.status === 'cancelled' ? 'muted' : '';
                return (
                  <tr key={j.id}>
                    <td>{jobName(j)}</td>
                    <td className={cls} style={{ color: cls ? undefined : '#C4C7CD', overflowWrap: 'anywhere' }}>
                      {j.status === 'failed' ? `Failed: ${result}` : j.status === 'cancelled' ? 'Cancelled' : result}
                    </td>
                    <td className="mono muted" style={{ fontSize: 13, whiteSpace: 'nowrap' }}>{when(j.updated_at || j.created_at)}</td>
                    <td style={{ textAlign: 'right', whiteSpace: 'nowrap' }}>
                      {link && <Link to={link[0]}>{link[1]}</Link>}
                      <button className="btn btn-sm btn-ghost" type="button" disabled={busy} onClick={() => onRemove(j)} aria-label={`Remove ${j.title}`}>Remove</button>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}

function Renders({ act, busy, version }) {
  const { data, error, reload } = useLoad(() => api('/renders'), [version]);
  const [doomed, setDoomed] = useState(null);
  const renders = data?.renders || [];
  const call = (path, method, body, ok) => act(async () => {
    const r = await api(path, { method, body });
    await reload();
    return r;
  }, ok);
  const name = (p) => String(p).split(/[\\/]/).pop();
  return (
    <section aria-labelledby="files-h" className="section" style={{ gap: 12 }}>
      <div style={{ display: 'flex', flexWrap: 'wrap', gap: 12, alignItems: 'baseline', justifyContent: 'space-between' }}>
        <h2 id="files-h">Rendered files</h2>
        <button className="btn" type="button" disabled={busy}
          onClick={() => call('/renders/sweep', 'POST', undefined, (r) => (r.removed.length ? `Removed ${r.removed.length} expired file(s)` : 'Nothing has expired'))}>
          Clean up expired
        </button>
      </div>
      <p className="muted tiny" style={{ margin: 0 }}>Files without “keep” are deleted after the retention days in <Link to="/settings">Settings</Link>.</p>
      <ErrorBox error={error} onRetry={reload} />
      {data && renders.length === 0 && <Empty>No rendered files.</Empty>}
      {renders.length > 0 && (
        <div className="scroll-x" style={{ border: '1px solid var(--line)', borderRadius: 10 }}>
          <table className="table" style={{ minWidth: 640 }}>
            <thead><tr><th>Cut</th><th>File</th><th className="num">Size</th><th>Keep</th><th /></tr></thead>
            <tbody>
              {renders.map((r) => (
                <tr key={r.hash}>
                  <td>{r.title || r.series_key}<div className="tiny muted">{r.mode} · {when(r.created_at)}</div></td>
                  <td className="mono tiny muted" style={{ overflowWrap: 'anywhere' }} title={r.path}>{name(r.path)}</td>
                  <td className="num mono" style={{ fontSize: 13 }}>{fmtSize(r.size_bytes)}</td>
                  <td>
                    <input type="checkbox" checked={Boolean(r.keep)} aria-label={`Keep ${name(r.path)}`} disabled={busy}
                      onChange={(e) => call(`/renders/${r.hash}`, 'PUT', { keep: e.target.checked })} />
                  </td>
                  <td style={{ textAlign: 'right' }}>
                    <button className="btn btn-sm" type="button" style={{ height: 30 }} onClick={() => setDoomed(r)}>Delete</button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {doomed && (
        <Dialog title="Delete this file?" onClose={() => setDoomed(null)}>
          <p className="muted" style={{ margin: 0, overflowWrap: 'anywhere' }}>
            <span className="mono tiny">{doomed.path}</span> is deleted from disk. You can build it again from the cut.
          </p>
          <div className="row" style={{ justifyContent: 'flex-end' }}>
            <button type="button" className="btn" onClick={() => setDoomed(null)}>Keep it</button>
            <button type="button" className="btn btn-danger" disabled={busy}
              onClick={async () => { await call(`/renders/${doomed.hash}`, 'DELETE', undefined, 'File deleted'); setDoomed(null); }}>Delete</button>
          </div>
        </Dialog>
      )}
    </section>
  );
}

export function Jobs() {
  const { data, error, loading, reload } = useLoad(() => api('/jobs'));
  const { act, msg, busy, clear } = useAct();
  const jobs = data?.jobs || [];
  const active = jobs.filter((j) => ACTIVE.includes(j.status));
  usePoll(reload, 2000, active.length > 0);
  const call = (path, method, ok) => act(async () => { await api(path, { method }); await reload(); }, ok);
  // oldest active first: that's the one the worker is on (or will pick next)
  const ordered = [...active].reverse();
  const current = ordered.find((j) => j.status === 'running') || ordered[0];
  const queued = ordered.filter((j) => j !== current);
  const done = jobs.filter((j) => !ACTIVE.includes(j.status));

  return (
    <div className="page" style={{ maxWidth: 1100 }}>
      <Notice msg={msg} onClose={clear} />
      <ErrorBox error={error} onRetry={reload} />
      {loading && <Loading />}
      {data && !current && (
        <section aria-label="Running" className="card muted">Nothing running. Builds, syncs and OP/ED detection run here one at a time.</section>
      )}
      {current && <Running job={current} queued={queued} busy={busy} onCancel={(j) => call(`/jobs/${j.id}/cancel`, 'POST', 'Cancelling')} />}
      {data && <Recent jobs={done} busy={busy} onRemove={(j) => call(`/jobs/${j.id}`, 'DELETE')} />}
      <Renders act={act} busy={busy} version={done.length} />
    </div>
  );
}
