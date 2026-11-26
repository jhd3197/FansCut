// Jobs: what needs you, what is running, what waits, what finished.
// Rendered files (keep / delete / clean up) sit under More.
import { useState } from 'react';
import { Link } from 'react-router';
import { api } from '../lib/api.js';
import { useAct, usePoll } from '../lib/hooks.js';
import { enc } from '../components/kit.jsx';
import { Dialog, Empty, ErrorBox, Loading, Notice } from '../components/ui.jsx';
import { useApi } from '../lib/store.js';
import './Jobs.css';

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
export const BUILD_PHASES = ['Check files and cut points', 'Effect segments', 'Cut and join', 'Subtitles and poster', 'Add to Plex'];

export function stages(job) {
  const running = job.status === 'running';
  const msg = job.message || '';
  const at = (names, i) => names.map((n, k) => [n, job.status === 'done' || k < i ? 'Done' : k === i && running ? 'Running' : 'Waiting']);
  switch (job.kind) {
    case 'timeline_render': {
      // builds report their phase (1–5); a build with no effects skips phase 2
      if (job.phase?.i) {
        return BUILD_PHASES.map((n, k) => {
          const st = job.status === 'done' || k < job.phase.i - 1 ? 'Done' : k === job.phase.i - 1 && running ? 'Running' : 'Waiting';
          return [n, st, st === 'Running' ? job.phase.detail : ''];
        });
      }
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

const KIND = {
  timeline_render: 'Build', timeline_sync: 'Sync', render: 'Render', detect: 'Find OP/ED', find_similar: 'Search',
  scene_index: 'Analyse', scene_signals: 'Analyse', scene_label: 'Tag', scene_tag: 'Tag',
};
const BUILDS = ['timeline_render', 'render'];
// The job's subject without the verb the server puts in front ("Render X" -> "X").
const jobTitle = (j) => j.title.replace(/^(Render|Sync|Index scenes of|Measure signals of|Tag|Search) /, '');
const jobKind = (j) => KIND[j.kind] || 'Job';

// Where a job's result is best looked at: [[to, label], ...], first one first.
export function jobLinks(job) {
  const p = job.payload || {};
  if (job.kind === 'timeline_sync' && p.timeline_id) return [[`/cuts/${enc(p.timeline_id)}/build`, 'Review']];
  if (job.kind === 'timeline_render' && p.timeline_id) return [[`/cuts/${enc(p.timeline_id)}`, 'Open cut']];
  if (job.kind.startsWith('scene_') && p.key) {
    return [[`/movies/${enc(p.key)}/scenes`, 'Scenes and tags'], [`/movies/${enc(p.key)}`, 'Open film']];
  }
  if (job.kind === 'detect' && p.series_key) {
    return [[p.season != null ? `/series/${enc(p.series_key)}/season/${p.season}` : `/series/${enc(p.series_key)}`, 'Open']];
  }
  if (job.kind === 'find_similar' && p.series_key && p.episode_id) return [[`/series/${enc(p.series_key)}/episode/${enc(p.episode_id)}`, 'Open']];
  if (p.series_key) return [[`/series/${enc(p.series_key)}`, 'Open']];
  return [];
}
export const jobLink = (job) => jobLinks(job)[0] || null;

// The mono line under a running job: kind, where it is, time left.
export function runningLine(job, left) {
  const steps = stages(job);
  const i = steps.findIndex((s) => s[1] === 'Running');
  const where = job.phase?.i
    ? [`step ${job.phase.i} of ${job.phase.n || BUILD_PHASES.length}`, job.phase.detail || job.phase.name].filter(Boolean).join(', ')
    : i >= 0 ? `step ${i + 1} of ${steps.length}, ${steps[i][0].toLowerCase()}` : job.message || 'starting';
  return [jobKind(job), where, left].filter(Boolean).join(' · ');
}

const BuildIcon = () => (
  <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden="true"><rect x="3" y="6" width="18" height="13" rx="2" /><path d="M3 10h18M8 6l-2 4M13 6l-2 4M18 6l-2 4" /></svg>
);
const ScanIcon = () => (
  <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden="true"><path d="M4 8V4h4M20 8V4h-4M4 16v4h4M20 16v4h-4M3 12h18" /></svg>
);
const DoneIcon = () => (
  <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.6" aria-hidden="true"><path d="M5 12.5l4.5 4.5L19 7.5" /></svg>
);
const kindIcon = (j) => (BUILDS.includes(j.kind) ? <BuildIcon /> : <ScanIcon />);

// Seconds left, from the first progress this page saw ({t, p}) to now; null
// until there's enough to go on (10 s and 2 points of progress).
export function etaSeconds(first, t, p) {
  if (!first || p >= 1) return null;
  const dt = (t - first.t) / 1000;
  const dp = p - first.p;
  if (dt < 10 || dp < 0.02) return null;
  return (dt / dp) * (1 - p);
}

// "about 1 h 10 m left" / "about 4 m left" / "under a minute left"
export function fmtLeft(secs) {
  if (secs == null) return null;
  if (secs < 60) return 'under a minute left';
  const m = Math.round(secs / 60);
  return m < 60 ? `about ${m} m left` : `about ${Math.floor(m / 60)} h ${m % 60} m left`;
}

// First progress seen per running job, kept across re-renders and page visits.
const firstSeen = new Map();

function timeLeft(job, now = Date.now()) {
  if (job.status !== 'running') return null;
  const p = Math.min(Math.max(job.progress || 0, 0), 1);
  const first = firstSeen.get(job.id);
  if (!first || p < first.p) {  // new to this page, or restarted: measure from here
    firstSeen.set(job.id, { t: now, p });
    return null;
  }
  return fmtLeft(etaSeconds(first, now, p));
}

function Renders({ act, busy }) {
  const { data, error, reload } = useApi('/renders');
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
  const { data, error, loading, reload } = useApi('/jobs');
  const { act, msg, busy, clear } = useAct();
  const jobs = data?.jobs || [];
  const active = jobs.filter((j) => ACTIVE.includes(j.status));
  usePoll(reload, 2000, active.length > 0);
  const call = (path, method, ok) => act(async () => { await api(path, { method }); await reload(); }, ok);
  // newest first from the API; the queue reads oldest first (the next one the worker takes)
  const running = jobs.filter((j) => j.status === 'running').reverse();
  const waiting = jobs.filter((j) => j.status === 'queued').reverse();
  const failed = jobs.filter((j) => j.status === 'failed');
  const done = jobs.filter((j) => j.status === 'done' || j.status === 'cancelled');
  const summary = [`${running.length} running`, `${waiting.length} waiting`,
    failed.length ? `${failed.length} need${failed.length === 1 ? 's' : ''} you` : null].filter(Boolean).join(' · ');
  const cancel = (j) => call(`/jobs/${j.id}/cancel`, 'POST', j.status === 'queued' ? `Removed ${jobTitle(j)}` : 'Cancelling');
  const remove = (j) => call(`/jobs/${j.id}`, 'DELETE');

  return (
    <div className="page jobs">
      <header className="jobs-head">
        <h1>Jobs</h1>
        {data && <span className="mono jobs-summary">{summary}</span>}
        <button type="button" className="btn" disabled title="Not yet">Pause all</button>
      </header>
      <Notice msg={msg} onClose={clear} />
      <ErrorBox error={error} onRetry={reload} />
      {loading && !data && <Loading />}

      {failed.length > 0 && (
        <section aria-labelledby="jobs-needs">
          <h2 id="jobs-needs" className="lab jobs-lab is-look">Needs you</h2>
          <div className="jobs-list">
            {failed.map((j, i) => (
              <div key={j.id} className="job is-failed">
                <span className="job-icon">{kindIcon(j)}</span>
                <div className="job-body">
                  <div className="job-title">{jobTitle(j)}</div>
                  <div className="job-error">{j.message || 'Failed'}</div>
                  <div className="mono job-sub">{jobKind(j)} · stopped at {Math.round((j.progress || 0) * 100)}% · {when(j.updated_at || j.created_at)}</div>
                </div>
                <div className="job-acts">
                  <button type="button" className={`btn${i === 0 ? ' btn-primary' : ''}`} disabled={busy}
                    onClick={() => call(`/jobs/${j.id}/retry`, 'POST', `Queued ${jobTitle(j)} again`)}>Retry</button>
                  <Link className="btn" to="/logs?level=ERROR">Logs</Link>
                  <button type="button" className="btn btn-quiet" disabled={busy} onClick={() => remove(j)}>Remove</button>
                </div>
              </div>
            ))}
          </div>
        </section>
      )}

      <section aria-labelledby="jobs-running">
        <h2 id="jobs-running" className="lab jobs-lab">Running now</h2>
        <div className="jobs-list">
          {running.map((j) => {
            const pct = Math.round(Math.min(Math.max(j.progress || 0, 0), 1) * 100);
            const open = jobLink(j);
            return (
              <div key={j.id} className="job">
                <span className="job-icon is-on">{kindIcon(j)}</span>
                <div className="job-body">
                  <div className="job-line"><span className="job-title">{jobTitle(j)}</span><span className="mono job-pct">{pct}%</span></div>
                  <div className="mono job-sub">{runningLine(j, timeLeft(j))}</div>
                  <div className="job-bar" role="progressbar" aria-label={jobTitle(j)} aria-valuemin={0} aria-valuemax={100} aria-valuenow={pct}>
                    <div style={{ width: `${pct}%` }} />
                  </div>
                </div>
                <div className="job-acts">
                  {open && <Link className="btn" to={open[0]}>Open</Link>}
                  <button type="button" className="btn btn-quiet" disabled={busy} onClick={() => cancel(j)}>Cancel</button>
                </div>
              </div>
            );
          })}
          {data && running.length === 0 && <div className="jobs-none">Nothing is running.</div>}
        </div>
      </section>

      {waiting.length > 0 && (
        <section aria-labelledby="jobs-waiting">
          <h2 id="jobs-waiting" className="jobs-lab"><span className="lab">Waiting</span> <span className="mono jobs-n">{waiting.length}</span></h2>
          <ol className="jobs-queue">
            {waiting.map((j, i) => (
              <li key={j.id}>
                <span className="mono jobs-n">{i + 1}</span>
                <span className="job-body">
                  <span className="job-title">{jobTitle(j)}</span>
                  <span className="mono job-sub">{jobKind(j)}</span>
                </span>
                <button type="button" className="btn btn-sm btn-quiet" disabled={busy} onClick={() => cancel(j)}>Remove</button>
              </li>
            ))}
          </ol>
        </section>
      )}

      {done.length > 0 && (
        <section aria-labelledby="jobs-done">
          <h2 id="jobs-done" className="lab jobs-lab">Done</h2>
          <div className="jobs-list">
            {done.map((j) => (
              <div key={j.id} className="job">
                <span className={`job-icon${j.status === 'done' ? ' is-on' : ''}`}>{j.status === 'done' ? <DoneIcon /> : kindIcon(j)}</span>
                <div className="job-body">
                  <div className="job-title">{jobTitle(j)}</div>
                  <div className="mono job-sub">
                    {[jobKind(j), j.status === 'cancelled' ? 'cancelled' : j.result?.message || j.message, when(j.updated_at || j.created_at)].filter(Boolean).join(' · ')}
                  </div>
                </div>
                <div className="job-acts">
                  {j.status === 'done' && jobLinks(j).map(([to, label]) => <Link key={to} className="btn" to={to}>{label}</Link>)}
                  <button type="button" className="btn btn-quiet" disabled={busy} onClick={() => remove(j)}>Remove</button>
                </div>
              </div>
            ))}
          </div>
        </section>
      )}

      <details className="jobs-more">
        <summary className="lab">More</summary>
        <Renders act={act} busy={busy} />
      </details>
    </div>
  );
}
