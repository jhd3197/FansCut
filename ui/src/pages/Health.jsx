import { useState } from 'react';
import { api } from '../lib/api.js';
import { useAct, useLoad } from '../lib/hooks.js';
import { Dialog, Empty, ErrorBox, Loading, Notice, PageHeader } from '../components/ui.jsx';

const CHECKS = {
  edition: 'Edition',
  languages: 'Languages',
  incomplete: 'Incomplete file',
  missing: 'Missing pieces',
  mismatch: 'Mismatch',
  filename: 'Filename',
};
const SEV = { error: 'tag-bad', warn: 'tag-queued', info: 'tag-ok' };

function baseName(p) {
  return p ? p.split(/[\/]/).pop() : '';
}

export function Health() {
  const [q, setQ] = useState('');
  const [query, setQuery] = useState({ q: '', deep: false });
  const { data, error, loading, reload } = useLoad(
    () => api(`/library/check?q=${encodeURIComponent(query.q)}&deep=${query.deep}`), [query]);
  const { act, msg, busy, clear } = useAct();
  const [confirm, setConfirm] = useState(null);

  const rename = (r) => act(
    () => api('/library/rename', { method: 'POST', body: { file: r.file, to: r.to } }),
    () => { setConfirm(null); reload(); return `Renamed to ${baseName(r.to)}`; });

  return (
    <>
      <PageHeader title="Library health"
        sub="Editions, languages, incomplete files, missing pieces and mismatches — each with the file and the fix.">
        <form className="row" onSubmit={(e) => { e.preventDefault(); setQuery({ q, deep: false }); }}>
          <input className="input" placeholder="Filter by title" value={q} onChange={(e) => setQ(e.target.value)} />
          <button className="btn" type="submit">Check</button>
          <button className="btn" type="button" title="Also decode the last 30 s of every file"
            onClick={() => setQuery({ q, deep: true })}>Deep check</button>
        </form>
      </PageHeader>
      <Notice msg={msg} onClose={clear} />
      <ErrorBox error={error} onRetry={reload} />
      {loading && <Loading what="Checking the library" />}
      {data && !loading && (
        <>
          <p>
            <span className="tag tag-bad">{data.summary.error} error(s)</span>{' '}
            <span className="tag tag-queued">{data.summary.warn} warning(s)</span>{' '}
            <span className="tag tag-ok">{data.summary.info} note(s)</span>{' '}
            <span className="muted tiny">{data.summary.files} file(s) checked</span>
          </p>
          {data.findings.length === 0 && <Empty>Nothing to fix.</Empty>}
          {data.findings.length > 0 && (
            <div className="card">
              <table className="table">
                <thead><tr><th>Severity</th><th>Check</th><th>Title</th><th>Finding</th><th>Fix</th></tr></thead>
                <tbody>
                  {data.findings.map((f, i) => (
                    <tr key={i}>
                      <td><span className={`tag ${SEV[f.severity]}`}>{f.severity}</span></td>
                      <td>{CHECKS[f.check] || f.check}</td>
                      <td><b>{f.title}</b>{f.file && <div className="muted tiny mono" style={{ overflowWrap: 'anywhere' }}>{baseName(f.file)}</div>}</td>
                      <td>{f.message}</td>
                      <td className="muted">{f.fix}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
          {data.renames.length > 0 && (
            <div className="card" style={{ marginTop: 14 }}>
              <h3>Suggested renames</h3>
              <p className="muted tiny">Nothing is renamed until you confirm each one.</p>
              <table className="table">
                <tbody>
                  {data.renames.map((r) => (
                    <tr key={r.file}>
                      <td className="mono tiny" style={{ overflowWrap: 'anywhere' }}>{baseName(r.file)}</td>
                      <td>→</td>
                      <td className="mono tiny" style={{ overflowWrap: 'anywhere' }}>{baseName(r.to)}</td>
                      <td><button className="btn btn-sm" disabled={busy} onClick={() => setConfirm(r)}>Rename…</button></td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </>
      )}
      {confirm && (
        <Dialog title="Rename this file?" onClose={() => setConfirm(null)}>
          <p className="mono tiny" style={{ overflowWrap: 'anywhere' }}>{confirm.file}</p>
          <p>becomes</p>
          <p className="mono tiny" style={{ overflowWrap: 'anywhere' }}>{baseName(confirm.to)}</p>
          <p className="muted tiny">Plex picks the new name up on its next scan. Sonarr/Radarr may want a rescan too.</p>
          <div className="row">
            <button className="btn btn-primary" disabled={busy} onClick={() => rename(confirm)}>Rename</button>
            <button className="btn" onClick={() => setConfirm(null)}>Cancel</button>
          </div>
        </Dialog>
      )}
    </>
  );
}
