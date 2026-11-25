import { useEffect, useState } from 'react';
import { api } from '../lib/api.js';
import { useAct, useLoad } from '../lib/hooks.js';
import { Dialog, ErrorBox, Loading, Notice } from '../components/ui.jsx';
import { KV } from '../components/kit.jsx';

const KIND = {
  incomplete: 'Broken?',
  languages: 'Language',
  missing: 'Missing',
  mismatch: 'Mismatch',
  edition: 'Edition',
  filename: 'Filename',
};
const COLOR = { error: 'var(--danger)', warn: 'var(--accent)', info: 'var(--info)' };

function baseName(p) {
  return p ? p.split(/[\\/]/).pop() : '';
}

// Seconds since `running` turned on, for the "this can take a while" line.
function useElapsed(running) {
  const [secs, setSecs] = useState(0);
  useEffect(() => {
    if (!running) return undefined;
    const started = Date.now();
    const t = setInterval(() => setSecs(Math.round((Date.now() - started) / 1000)), 1000);
    return () => { clearInterval(t); setSecs(0); };
  }, [running]);
  return secs;
}

function System() {
  const doc = useLoad(() => api('/doctor'));
  const ver = useLoad(() => fetch('/healthz').then((r) => r.json()).catch(() => null));
  const d = doc.data;
  return (
    <section aria-labelledby="sys-h" className="section" style={{ gap: 12 }}>
      <div className="section-head">
        <h2 id="sys-h">System</h2>
        {d && <span className={`tiny ${d.ok ? 'c-ready' : 'c-danger'}`}>{d.ok ? 'All good' : 'Something needs attention'}</span>}
        <span className="end"><button className="btn btn-sm" type="button" onClick={doc.reload}>Re-check</button></span>
      </div>
      <ErrorBox error={doc.error} onRetry={doc.reload} />
      {doc.loading && !d && <Loading what="Checking" />}
      {d && (
        <div className="card" style={{ padding: '16px 18px' }}>
          <KV rows={[
            ver.data?.version && ['FansCut', ver.data.version],
            ...d.checks.map((c) => [c.name, (
              <span key={c.name}>
                {c.off ? <span className="muted">Off</span>
                  : <span className={c.ok ? 'c-ready' : 'c-danger'}>{c.ok ? 'OK' : 'Failed'}</span>}
                {c.detail && <span className="mono tiny muted"> · {c.detail}</span>}
              </span>
            )]),
          ]} />
        </div>
      )}
    </section>
  );
}

export function Health() {
  const last = useLoad(() => api('/library/check/last'));
  const [result, setResult] = useState(null); // a fresh run replaces the cached one
  const [running, setRunning] = useState(false);
  const [runError, setRunError] = useState(null);
  const [q, setQ] = useState('');
  const [deepFor, setDeepFor] = useState({}); // title -> {busy, result, error}
  const [fixed, setFixed] = useState([]);
  const [confirm, setConfirm] = useState(null);
  const { act, msg, busy, clear } = useAct();
  const elapsed = useElapsed(running);

  const data = result || (last.data?.summary ? last.data : null);

  const check = async (deep = false) => {
    setRunning(true);
    setRunError(null);
    try {
      setResult(await api(`/library/check?q=${encodeURIComponent(q.trim())}&deep=${deep}`));
      setDeepFor({});
    } catch (e) {
      setRunError(e);
    } finally {
      setRunning(false);
    }
  };

  const readToEnd = async (f) => {
    setDeepFor((m) => ({ ...m, [f.title]: { busy: true } }));
    try {
      const r = await api(`/library/check?deep=true&q=${encodeURIComponent(f.title)}`);
      setDeepFor((m) => ({ ...m, [f.title]: { result: r } }));
    } catch (e) {
      setDeepFor((m) => ({ ...m, [f.title]: { error: e.message } }));
    }
  };

  const rename = (r) => act(
    () => api('/library/rename', { method: 'POST', body: { file: r.file, to: r.to } }),
    () => {
      setConfirm(null);
      setFixed((x) => [...x, `Renamed ${baseName(r.file)} to ${baseName(r.to)}.`]);
      const drop = (d) => d && ({
        ...d,
        renames: d.renames.filter((x) => x.file !== r.file),
        findings: d.findings.filter((x) => !(x.check === 'filename' && x.file === r.file)),
      });
      setResult((d) => drop(d || last.data));
      return `Renamed to ${baseName(r.to)}`;
    });

  const renames = data?.renames || [];
  const renameFor = (f) => f.file && renames.find((r) => r.file === f.file);
  const findings = data?.findings || [];

  const deepNote = (f) => {
    const s = deepFor[f.title];
    if (!s) return null;
    if (s.busy) return <p className="tiny">Reading the file to the end. Large files take a minute…</p>;
    if (s.error) return <p className="tiny c-danger">{s.error}</p>;
    const still = (s.result.findings || []).filter((x) => x.check === 'incomplete' && (!f.file || x.file === f.file));
    return still.length
      ? <p className="tiny c-danger">Read to the end: {still[0].message}</p>
      : <p className="tiny c-ready">Read to the end: the file plays through. It looks complete.</p>;
  };

  return (
    <div className="page" style={{ maxWidth: 1000 }}>
      <Notice msg={msg} onClose={clear} />
      <section aria-labelledby="find-h" style={{ border: '1px solid var(--line)', borderRadius: 10, overflow: 'hidden' }}>
        <div style={{ display: 'flex', flexWrap: 'wrap', alignItems: 'center', justifyContent: 'space-between', gap: 12,
          padding: '10px 12px 10px 18px', borderBottom: '1px solid var(--line)', background: 'var(--side)' }}>
          <h2 id="find-h">
            {data ? `${findings.length} finding${findings.length === 1 ? '' : 's'}` : 'Library health'}
            {data?.summary && (
              <span className="muted tiny" style={{ fontWeight: 400, marginLeft: 10 }}>
                {data.summary.files} file(s) · {data.summary.error} error · {data.summary.warn} warning · {data.summary.info} note
                {data.checked_at ? ` · checked ${data.checked_at.replace('T', ' ')}` : ''}
              </span>
            )}
          </h2>
          <form className="row" onSubmit={(e) => { e.preventDefault(); check(false); }}>
            <input className="input" style={{ width: 180, padding: '6px 10px' }} aria-label="Filter by title"
              placeholder="Filter by title" value={q} onChange={(e) => setQ(e.target.value)} />
            <button className="btn" type="button" disabled={running} title="Also decode the last 30 s of every file"
              onClick={() => check(true)}>Deep check</button>
            <button className="btn" type="submit" disabled={running}>{data ? 'Check again' : 'Check the library'}</button>
          </form>
        </div>

        {running && (
          <p className="muted" role="status" style={{ margin: 0, padding: '14px 18px', borderBottom: '1px solid #22262B' }}>
            Checking every file{q.trim() ? ` matching “${q.trim()}”` : ''}… {elapsed}s. A big library can take a few minutes.
          </p>
        )}
        {runError && <div style={{ padding: '12px 18px' }}><ErrorBox error={runError} onRetry={() => check(false)} /></div>}
        {last.error && !data && <div style={{ padding: '12px 18px' }}><ErrorBox error={last.error} onRetry={last.reload} /></div>}
        {last.loading && !data && !running && <div style={{ padding: '0 18px' }}><Loading /></div>}
        {!last.loading && !data && !running && !runError && (
          <p className="muted" style={{ margin: 0, padding: '16px 18px' }}>
            The library has not been checked yet. A check looks at editions, languages, unfinished files, missing pieces and mismatches.
          </p>
        )}
        {data && findings.length === 0 && !running && (
          <p className="muted" style={{ margin: 0, padding: '16px 18px' }}>Nothing to fix.</p>
        )}

        {findings.length > 0 && (
          <div style={{ display: 'flex', flexDirection: 'column', gap: 10, padding: 12 }}>
            {findings.map((f, i) => {
              const rn = renameFor(f);
              return (
                <article key={`${f.check}-${f.file}-${i}`} className="finding" style={{ '--c': COLOR[f.severity] || 'var(--line)' }}>
                  <div style={{ display: 'grid', gridTemplateColumns: '84px minmax(0, 1fr) auto', gap: 16, alignItems: 'start' }}>
                    <span className="kind" style={{ paddingTop: 2 }}>{KIND[f.check] || f.check}</span>
                    <div style={{ display: 'flex', flexDirection: 'column', gap: 4, minWidth: 0 }}>
                      <h3>{f.title}</h3>
                      <p style={{ color: '#C4C7CD', fontSize: 13 }}>{f.message}</p>
                      {f.file && <span className="file">{f.file}</span>}
                      {f.files?.length > 0 && (
                        <span className="file">{f.files.length} file(s): {f.files.slice(0, 4).map(baseName).join(' · ')}{f.files.length > 4 ? ' …' : ''}</span>
                      )}
                      {f.fix && <p className="tiny"><span className="c-ready">Fix</span> {f.fix}</p>}
                      {rn && <p className="tiny mono" style={{ overflowWrap: 'anywhere' }}>→ {baseName(rn.to)}</p>}
                      {f.check === 'incomplete' && deepNote(f)}
                    </div>
                    <div className="row">
                      {rn && <button className="btn" type="button" disabled={busy || rn.writable === false}
                        title={rn.writable === false ? data?.rename_blocked : undefined} onClick={() => setConfirm(rn)}>Rename</button>}
                      {f.check === 'incomplete' && (
                        <button className="btn" type="button" disabled={deepFor[f.title]?.busy} onClick={() => readToEnd(f)}>Read to the end</button>
                      )}
                    </div>
                  </div>
                </article>
              );
            })}
          </div>
        )}

        {renames.length > 0 && (
          <div style={{ padding: '14px 18px', borderTop: '1px solid var(--line)' }}>
            <h3 style={{ marginBottom: 4 }}>Proposed renames</h3>
            <p className="muted tiny" style={{ margin: '0 0 8px' }}>Nothing is renamed until you confirm each one.</p>
            {data?.rename_blocked && (
              <p className="tiny" style={{ margin: '0 0 8px' }}><span className="c-warn">Can't rename</span> {data.rename_blocked}.</p>
            )}
            <table className="table">
              <tbody>
                {renames.map((r) => (
                  <tr key={r.file}>
                    <td className="mono tiny" style={{ overflowWrap: 'anywhere' }}>{baseName(r.file)}</td>
                    <td aria-hidden="true">→</td>
                    <td className="mono tiny" style={{ overflowWrap: 'anywhere' }}>{baseName(r.to)}
                      {r.reason && <div className="muted" style={{ fontFamily: 'var(--font)' }}>{r.reason}</div>}</td>
                    <td><button className="btn btn-sm" type="button" disabled={busy || r.writable === false}
                      title={r.writable === false ? data?.rename_blocked : undefined} onClick={() => setConfirm(r)}>Rename</button></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}

        {fixed.map((t, i) => (
          <div key={i} style={{ padding: '14px 18px', fontSize: 13, color: 'var(--muted)', display: 'flex', gap: 10, borderTop: '1px solid #22262B' }}>
            <span className="c-ready">Fixed</span><span>{t}</span>
          </div>
        ))}
      </section>

      <System />

      {confirm && (
        <Dialog title="Rename this file?" onClose={() => setConfirm(null)}>
          <p className="mono tiny" style={{ overflowWrap: 'anywhere', margin: 0 }}>{confirm.file}</p>
          <p style={{ margin: 0 }}>becomes</p>
          <p className="mono tiny" style={{ overflowWrap: 'anywhere', margin: 0 }}>{baseName(confirm.to)}</p>
          {confirm.reason && <p className="muted tiny" style={{ margin: 0 }}>{confirm.reason}</p>}
          <p className="muted tiny" style={{ margin: 0 }}>Plex picks the new name up on its next scan. Sonarr and Radarr may want a rescan too.</p>
          <div className="row">
            <button className="btn btn-primary" type="button" disabled={busy} onClick={() => rename(confirm)}>Rename</button>
            <button className="btn" type="button" onClick={() => setConfirm(null)}>Cancel</button>
          </div>
        </Dialog>
      )}
    </div>
  );
}
