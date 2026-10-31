import { useState } from 'react';
import { Link } from 'react-router';
import { api } from '../lib/api.js';
import { useAct, useLoad } from '../lib/hooks.js';
import { Empty, ErrorBox, Loading, Notice, PageHeader } from '../components/ui.jsx';

export function Library() {
  const [q, setQ] = useState('');
  const { data, error, loading, reload } = useLoad(() => api('/library/series'));
  const { act, msg, busy, clear } = useAct();
  const rescan = () => act(async () => { await api('/library/refresh', { method: 'POST' }); await reload(); }, 'Library rescanned');

  const needle = q.trim().toLowerCase();
  const series = (data?.series || []).filter((s) => !needle || s.title.toLowerCase().includes(needle));
  const errors = Object.entries(data?.errors || {});

  return (
    <>
      <PageHeader title="Library" sub="Pick a series to label episodes and build a cut.">
        <input className="input" style={{ width: 220 }} placeholder="Search…" aria-label="Search series"
          value={q} onChange={(e) => setQ(e.target.value)} />
        <button className="btn" onClick={rescan} disabled={busy}>Rescan</button>
      </PageHeader>
      <Notice msg={msg} onClose={clear} />
      <ErrorBox error={error} onRetry={reload} />
      {errors.map(([src, text]) => (
        <div key={src} className="notice notice-err" role="alert"><span><b>{src}</b>: {text}</span></div>
      ))}
      {loading && <Loading />}
      {data && data.sources.length === 0 && (
        <Empty>No library source is set up yet. Add Sonarr, Plex or a media folder in <Link to="/settings">Settings</Link>.</Empty>
      )}
      {data && data.sources.length > 0 && series.length === 0 && !errors.length && (
        <Empty>{needle ? 'No series match that search.' : 'No series found in your sources.'}</Empty>
      )}
      <div className="series-grid">
        {series.map((s) => (
          <Link key={s.key} className="series-card" to={`/series/${encodeURIComponent(s.key)}`}>
            <h3>{s.title}{s.year ? <span className="muted"> ({s.year})</span> : null}</h3>
            <div className="row wrap">
              <span className="tag">{s.source}</span>
              <span className="tag">{s.episode_count} ep</span>
              <span className={`tag ${s.matched ? 'tag-ok' : ''}`}>{s.matched ? 'matched' : 'not matched'}</span>
            </div>
          </Link>
        ))}
      </div>
    </>
  );
}
