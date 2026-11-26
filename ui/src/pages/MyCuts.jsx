// Every cut installed on this machine, with its build state and where it landed.
import { useState } from 'react';
import { Link } from 'react-router';
import { api, openInPlex } from '../lib/api.js';
import { useAct, usePoll, useUrlState } from '../lib/hooks.js';
import { enc, hm, Poster, Seg } from '../components/kit.jsx';
import { Dialog, Empty, ErrorBox, Loading, MoreMenu, Notice } from '../components/ui.jsx';
import { useApi } from '../lib/store.js';

const ACTIVE = ['queued', 'running'];
const GRID = { display: 'grid', gridTemplateColumns: '64px minmax(0, 1.4fr) minmax(0, 1.4fr) 110px auto', gap: 18, alignItems: 'center', padding: '14px 16px', borderTop: '1px solid var(--raised)' };

// Folder Plex sees: the show folder for "Show/Season 01/x.mkv", the movie folder otherwise.
export function plexName(path) {
  const parts = String(path).split(/[\\/]/).filter(Boolean);
  const parent = parts[parts.length - 2];
  if (parent && /^Season \d+$/i.test(parent)) return parts[parts.length - 3] || parent;
  return parent || parts[parts.length - 1] || '';
}

function status(cut, job) {
  if (job && ACTIVE.includes(job.status)) {
    return [job.status === 'queued' ? 'Waiting to build' : `Building · ${Math.round((job.progress || 0) * 100)}%`, 'c-accent'];
  }
  if (job?.status === 'failed') return ['Last build failed', 'c-danger'];
  if (job?.status === 'done') return ['Built', 'c-ready'];
  return cut.synced ? ['Cut points matched · ready to build', 'c-ready'] : ['Not matched to your files yet', 'muted'];
}

export function MyCuts() {
  const cutsQ = useApi('/cuts');
  const jobsQ = useApi('/jobs');
  const settingsQ = useApi('/settings', { quiet: true });
  const [show, setShow] = useUrlState('show', 'all');
  const [doomed, setDoomed] = useState(null);
  const [updates, setUpdates] = useState(null);
  const { act, msg, busy, clear } = useAct();
  const jobs = jobsQ.data?.jobs || [];
  const running = jobs.some((j) => ACTIVE.includes(j.status));
  usePoll(() => { jobsQ.reload(); if (!running) cutsQ.reload(); }, 2000, running);

  if (cutsQ.error && !cutsQ.data) return <div className="page"><ErrorBox error={cutsQ.error} onRetry={cutsQ.reload} /></div>;
  if (!cutsQ.data) return <div className="page"><Loading /></div>;

  const cuts = cutsQ.data.cuts || [];
  const plex = Boolean(settingsQ.data?.library?.plex?.url);
  const renders = cutsQ.data.renders || [];
  const upd = Object.fromEntries((updates || []).map((u) => [u.id, u]));
  const nUpd = (updates || []).filter((u) => u.update).length;
  const rows = cuts.filter((c) => (show === 'all' || (show === 'updates' ? upd[c.id]?.update
    : show === 'cuts' ? c.kind === 'timeline' : c.kind === 'playlist')));
  const checkUpdates = () => act(async () => {
    const r = await api('/cuts/updates');
    setUpdates(r.installs);
    return r.installs;
  }, (list) => (list.some((u) => u.update) ? `${list.filter((u) => u.update).length} update(s) available`
    : list.length ? 'Every installed cut is up to date' : 'No cuts installed from a repo yet'));
  const update = (c) => act(async () => {
    const r = await api(`/cuts/${enc(c.id)}/update`, { method: 'POST' });
    setUpdates((u) => (u || []).map((x) => (x.id === c.id ? { ...x, update: false, installed: r.sha } : x)));
    await cutsQ.reload();
    return r;
  }, (r) => (r.changed ? `Updated ${c.title}${r.summary ? ` (${r.summary})` : ''}. Run Build to re-match the cut points.` : `${c.title} was already up to date`));

  const lastJob = (tid) => jobs.find((j) => j.kind === 'timeline_render' && j.payload?.timeline_id === tid);
  const rendersOf = (cut) => {
    const ids = new Set(jobs.filter((j) => j.payload?.timeline_id === cut.id).map((j) => j.id));
    return renders.filter((r) => ids.has(r.job_id)
      || (r.series_key === cut.series_key && (r.title === cut.title || r.title?.startsWith(`${cut.title} · part `))));
  };

  const remove = (cut) => act(async () => {
    await api(`/timelines/${enc(cut.id)}`, { method: 'DELETE' });
    setDoomed(null);
    await cutsQ.reload();
  }, `Deleted ${cut.title}`);

  return (
    <div className="page" style={{ maxWidth: 1100 }}>
      <div className="toolbar">
        <Seg label="Show" value={show} onChange={setShow}
          options={[['all', 'All'], ['updates', nUpd ? `Updates ${nUpd}` : 'Updates'], ['cuts', 'Cuts'], ['playlists', 'Playlists']]} />
        <span className="row wrap">
          <button type="button" className="btn" disabled={busy} onClick={checkUpdates}>Check for updates</button>
          <Link className="btn" to="/new" state={{ from: { label: 'My cuts', to: '/my-cuts' } }}>New cut from template</Link>
          <Link className="btn" to="/">Find more cuts</Link>
        </span>
      </div>
      <Notice msg={msg} onClose={clear} />
      <ErrorBox error={jobsQ.error} onRetry={jobsQ.reload} />

      {cuts.length === 0 && <Empty>No cuts yet. Pick one on <Link to="/">Discover</Link> or install one from a repo.</Empty>}
      {cuts.length > 0 && rows.length === 0 && <Empty>{show === 'playlists' ? 'No playlists yet.' : 'Nothing here.'}</Empty>}
      {rows.length > 0 && (
        <ul className="card" style={{ listStyle: 'none', margin: 0, padding: 0, overflow: 'hidden' }}>
          <li style={{ ...GRID, alignItems: 'start', borderTop: 0, padding: '10px 16px' }} className="tiny muted">
            <span /><span>Cut</span><span>In Plex</span><span>Version</span><span />
          </li>
          {rows.map((c) => {
            if (c.kind === 'playlist') {
              return (
                <li key={c.id} style={GRID}>
                  <div className="mycuts-thumb" style={{ width: 64 }} aria-hidden="true">
                    <Poster title={c.title} kind="order" />
                  </div>
                  <span style={{ display: 'flex', flexDirection: 'column', gap: 2, minWidth: 0 }}>
                    <Link to={`/discover/${enc(c.suggestion)}`} style={{ color: 'var(--text)', fontWeight: 600 }}>{c.title}</Link>
                    <span className="tiny muted">Playlist · {c.films.length} film{c.films.length === 1 ? '' : 's'}</span>
                    <span className="tiny c-ready">In Plex · nothing rendered</span>
                  </span>
                  <span className="tiny" style={{ color: 'var(--text2)' }} title={c.films.join(' → ')}>
                    {c.films.slice(0, 3).join(' → ')}{c.films.length > 3 ? ' …' : ''}
                  </span>
                  <span className="mono tiny muted">—</span>
                  <span className="row wrap" style={{ gap: 6, justifyContent: 'flex-end' }}>
                    {plex && <button type="button" className="btn" style={{ height: 32 }} disabled={busy}
                      onClick={() => act(() => openInPlex(`/playlists/${enc(c.playlist_id)}/plex`))}>Open in Plex</button>}
                  </span>
                </li>
              );
            }
            const job = lastJob(c.id);
            const [text, cls] = status(c, job);
            const files = rendersOf(c);
            const where = job?.status === 'done' && job.result?.show ? job.result.show : files[0] ? plexName(files[0].path) : null;
            const building = job && ACTIVE.includes(job.status);
            const facts = [`${c.parts} part${c.parts === 1 ? '' : 's'}`, `${c.segments} segment${c.segments === 1 ? '' : 's'}`, hm(c.minutes)].join(' · ');
            return (
              <li key={c.id} style={GRID}>
                <div className="mycuts-thumb" style={{ width: 64 }} aria-hidden="true">
                  <Poster title={c.title} kind={c.tmdb_ids?.length > 1 && c.parts === 1 ? 'merge' : 'supercut'} />
                </div>
                <span style={{ display: 'flex', flexDirection: 'column', gap: 2, minWidth: 0 }}>
                  <Link to={`/cuts/${enc(c.id)}`} style={{ color: 'var(--text)', fontWeight: 600 }}>{c.title}</Link>
                  <span className="tiny muted">Cut · {facts}</span>
                  <span className={`tiny ${upd[c.id]?.update ? 'c-accent' : cls}`}>
                    {upd[c.id]?.update ? ['Update available', upd[c.id].summary].filter(Boolean).join(' · ') : text}
                  </span>
                </span>
                <span style={{ fontSize: 13, color: 'var(--text2)', overflowWrap: 'anywhere' }} title={files[0]?.path}>
                  {where || <span className="dim">—</span>}
                  {files.length > 1 && <span className="tiny muted"> · {files.length} files</span>}
                </span>
                <span className="mono tiny muted" title={c.repo || undefined}>
                  {c.version ? `v${String(c.version).replace(/^v/, '')}` : '—'}
                  {upd[c.id]?.update && <span className="c-accent"> → {upd[c.id].latest.slice(0, 7)}</span>}
                </span>
                <span className="row wrap" style={{ gap: 6, justifyContent: 'flex-end' }}>
                  {/* one primary action; the rest live in More */}
                  {upd[c.id]?.update
                    ? <button type="button" className="btn btn-primary" style={{ height: 32 }} disabled={busy} onClick={() => update(c)}>Update</button>
                    : building
                      ? <Link className="btn" style={{ height: 32 }} to="/jobs">Open in Jobs</Link>
                      : <Link className="btn" style={{ height: 32 }} to={`/cuts/${enc(c.id)}/build`}>Build</Link>}
                  {plex && (where || job?.status === 'done') && (
                    <button type="button" className="btn" style={{ height: 32 }} disabled={busy}
                      onClick={() => act(() => openInPlex(`/cuts/${enc(c.id)}/plex`))}>Open in Plex</button>
                  )}
                  <MoreMenu LinkAs={Link} items={[
                    upd[c.id]?.update && !building && ['Build', { to: `/cuts/${enc(c.id)}/build` }],
                    upd[c.id]?.update && building && ['Open in Jobs', { to: '/jobs' }],
                    ['Open in editor', { to: `/cuts/${enc(c.id)}/edit` }],
                    ['Delete…', () => setDoomed(c), { danger: true }],
                  ]} />
                </span>
              </li>
            );
          })}
        </ul>
      )}

      {doomed && (
        <Dialog title={`Delete ${doomed.title}?`} onClose={() => setDoomed(null)}>
          <p className="muted" style={{ margin: 0 }}>
            This removes the cut and its matched cut points from FansCut. Files already built stay where they are; manage them under <Link to="/jobs">Jobs</Link>.
          </p>
          <div className="row" style={{ justifyContent: 'flex-end' }}>
            <button type="button" className="btn" onClick={() => setDoomed(null)}>Keep it</button>
            <button type="button" className="btn btn-danger" disabled={busy} onClick={() => remove(doomed)}>Delete</button>
          </div>
        </Dialog>
      )}
    </div>
  );
}
