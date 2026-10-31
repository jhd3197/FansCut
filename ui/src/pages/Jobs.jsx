import { api } from '../lib/api.js';
import { useAct, useLoad, usePoll } from '../lib/hooks.js';
import { Empty, ErrorBox, Loading, Notice, PageHeader, Progress, StatusTag } from '../components/ui.jsx';

const ACTIVE = ['queued', 'running'];

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
    </>
  );
}
