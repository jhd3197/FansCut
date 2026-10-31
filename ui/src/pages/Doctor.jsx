import { api } from '../lib/api.js';
import { useLoad } from '../lib/hooks.js';
import { ErrorBox, Loading, PageHeader } from '../components/ui.jsx';

export function Doctor() {
  const { data, error, loading, reload } = useLoad(() => api('/doctor'));
  const health = useLoad(() => fetch('/healthz').then((r) => r.json()));
  return (
    <>
      <PageHeader title="Status" sub={health.data ? `FansCut ${health.data.version}` : undefined}>
        <button className="btn" onClick={reload}>Re-check</button>
      </PageHeader>
      <ErrorBox error={error} onRetry={reload} />
      {loading && <Loading what="Checking" />}
      {data && (
        <div className="card">
          <p><span className={`tag ${data.ok ? 'tag-ok' : 'tag-bad'}`}>{data.ok ? 'all good' : 'something needs attention'}</span></p>
          <table className="table">
            <tbody>
              {data.checks.map((c) => (
                <tr key={c.name}>
                  <td style={{ width: 28 }} aria-label={c.ok ? 'ok' : 'failed'}>{c.ok ? '✅' : '❌'}</td>
                  <td><b>{c.name}</b></td>
                  <td className="muted mono" style={{ overflowWrap: 'anywhere' }}>{c.detail}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </>
  );
}
