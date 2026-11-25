import { api } from '../lib/api.js';
import { useAct, useLoad, usePoll } from '../lib/hooks.js';
import { Empty, ErrorBox, Loading, Notice, PageHeader, Progress, StatusTag } from '../components/ui.jsx';

const ACTIVE = ['queued', 'running'];

const fmtSize = (n) => (n >= 1e9 ? `${(n / 1e9).toFixed(1)} GB` : `${Math.round(n / 1e6)} MB`);

function Renders() {
  const { data, reload } = useLoad(() => api('/renders'));
  const { act } = useAct();
  const renders = data?.renders || [];
  if (data && renders.length === 0) return null;
  const call = (path, method, body) => act(async () => { await api(path, { method, body }); await reload(); });
  return (
    <>
      <h2 style={{ marginTop: 22 }}>Rendered files</h2>
      <p className="muted">Cached cuts. Unchecked “keep” files are auto-deleted after the retention days in Settings.</p>
      <div className="card scroll-x" style={{ padding: 0 }}>
        <table className="table">
          <thead><tr><th>Series</th><th>File</th><th className="num">Size</th><th>Mode</th><th>Keep</th><th>Created</th><th /></tr></thead>
          <tbody>
            {renders.map((r) => (
              <tr key={r.hash}>
                <td><b>{r.title || r.series_key}</b></td>
                <td className="mono tiny" style={{ overflowWrap: 'anywhere' }}>{r.path}</td>
                <td className="num mono">{fmtSize(r.size_bytes)}</td>
                <td className="muted">{r.mode}</td>
                <td>
                  <input type="checkbox" checked={r.keep} aria-label={`Keep ${r.path}`}
                    onChange={(e) => call(`/renders/${r.hash}`, 'PUT', { keep: e.target.checked })} />
                </td>
                <td className="muted tiny">{r.created_at.replace('T', ' ')}</td>
                <td><button className="btn btn-sm btn-ghost" onClick={() => call(`/renders/${r.hash}`, 'DELETE')}>Delete</button></td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <div className="row" style={{ marginTop: 10 }}>
        <button className="btn btn-sm" onClick={() => call('/renders/sweep', 'POST')}>Sweep expired</button>
      </div>
    </>
  );
}

export function Jobs() {
  const { data, error, loading, reload } = useLoad(() => api('/jobs'));
  const { act, msg, clear } = useAct();
  const jobs = data?.jobs || [];
  usePoll(reload, 2000, jobs.some((j) => ACTIVE.includes(j.status)));
  const call = (path, method) => act(async () => { await api(path, { method }); await reload(); });

  return (
    <>
      <PageHeader title="Jobs" sub="Renders and OP/ED detection run one at a-time in the background." />
      <Notice msg={msg} onClose={clear} />
      <ErrorBox error={error} onRetry={reload} />
      {loading && <Loading />}
      {data && jobs.length === 0 && <Empty>No jobs yet. Build a cut and press Render.</Empty>}
      {jobs.length > 0 && (
        <div className="card scroll-x" style={{ padding: 0 }}>
          <table className="table">
            <thead><tr><th>Job</th><th>Status</th><th style={{ width: '22%' }}>Progress</th><th>Message</th><th>Started</th><th /></tr></thead>
            <tbody>
              {jobs.map((j) => (
                <tr key={j.id}>
                  <td><b>{j.title}</b><div className="muted tiny">{j.kind} · {j.id}</div></td>
                  <td><StatusTag status={j.status} /></td>
                  <td><Progress value={j.progress} /></td>
                  <td className="muted" style={{ overflowWrap: 'anywhere' }}>{j.result?.output || j.message}</td>
                  <td className="muted tiny">{j.created_at.replace('T', ' ')}</td>
                  <td className="row">
                    {ACTIVE.includes(j.status)
                      ? <button className="btn btn-sm" onClick={() => call(`/jobs/${j.id}/cancel`, 'POST')}>Cancel</button>
                      : <button className="btn btn-sm btn-ghost" onClick={() => call(`/jobs/${j.id}`, 'DELETE')}>Remove</button>}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      <Renders />
    </>
  );
}
