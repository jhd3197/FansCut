// My cuts: every cut on this machine as a poster grid, plus ways to start a new one.
// A new cut is a blank recipe for one franchise; making it happens on /cuts/:id/find.
import { useState } from 'react';
import { Link, useNavigate } from 'react-router';
import { api, openInPlex } from '../lib/api.js';
import { useAct, usePoll } from '../lib/hooks.js';
import { enc, Poster } from '../components/kit.jsx';
import { Dialog, ErrorBox, Loading, MoreMenu, Notice } from '../components/ui.jsx';
import { useApi } from '../lib/store.js';
import './MyCuts.css';

const ACTIVE = ['queued', 'running'];

// Folder Plex sees: the show folder for "Show/Season 01/x.mkv", the movie folder otherwise.
export function plexName(path) {
  const parts = String(path).split(/[\\/]/).filter(Boolean);
  const parent = parts[parts.length - 2];
  if (parent && /^Season \d+$/i.test(parent)) return parts[parts.length - 3] || parent;
  return parent || parts[parts.length - 1] || '';
}

// [text, class] for the line under a cut's name.
export function cutState(cut, job, update) {
  if (update) return ['Update available', 'state state-used'];
  if (job && ACTIVE.includes(job.status)) {
    return [job.status === 'queued' ? 'Waiting to build' : `Building ${Math.round((job.progress || 0) * 100)}%`, 'state state-ready'];
  }
  if (job?.status === 'failed') return ['Last build failed', 'state state-look'];
  if (job?.status === 'done') return ['Built', 'state state-ready'];
  return ['Not built yet', 'state state-missing'];
}

const plural = (n, one) => `${n} ${one}${n === 1 ? '' : 's'}`;

// The cut's AI poster when it has one, the drawn poster otherwise.
function CutCover({ cut }) {
  const [failed, setFailed] = useState(false);
  if (cut.kind === 'timeline' && !failed) {
    return <img className="mycuts-poster" src={`/api/timelines/${enc(cut.id)}/poster`} alt="" loading="lazy" onError={() => setFailed(true)} />;
  }
  return <span className="mycuts-poster"><Poster title={cut.title} kind={cut.kind === 'playlist' ? 'order' : 'supercut'} bare /></span>;
}

// Small form: which franchise, and a name. Creates the blank cut and opens Find scenes.
function NewCutDialog({ onClose }) {
  const nav = useNavigate();
  const colQ = useApi('/library/collections');
  const serQ = useApi('/library/series', { quiet: true });
  const [pick, setPick] = useState('');
  const [title, setTitle] = useState('');
  const { act, msg, busy, clear } = useAct({ inline: true });
  const cols = (colQ.data?.collections || []).filter((c) => c.films > 0);
  const shows = serQ.data?.series || [];

  const create = (e) => {
    e.preventDefault();
    act(async () => {
      const [kind, id] = [pick.slice(0, 2), pick.slice(2)];
      let key;
      let franchise;
      if (kind === 'c:') {
        const col = await api(`/library/collections/${enc(id)}`);
        const first = (col.films || []).find((f) => f.tmdb_id);
        if (!first) throw new Error(`No film in ${col.name} has a TMDB id yet`);
        key = `movie:${first.tmdb_id}`;
        franchise = col.name;
      } else {
        const show = shows.find((s) => s.key === id);
        key = id;
        franchise = show?.title || id;
      }
      const name = title.trim() || franchise;
      const doc = { schema: 2, kind: 'timeline', title: name, series: { title: franchise }, sources: {}, parts: [] };
      const t = await api(`/series/${enc(key)}/timelines`, { method: 'POST', body: doc });
      nav(`/cuts/${enc(t.id)}/find`);
    });
  };

  return (
    <Dialog title="New cut" onClose={onClose}>
      <form className="mycuts-form" onSubmit={create}>
        <label className="lab" htmlFor="nc-franchise">Franchise</label>
        {colQ.error ? <ErrorBox error={colQ.error} onRetry={colQ.reload} /> : (
          <select id="nc-franchise" className="inp" value={pick} onChange={(e) => setPick(e.target.value)} required autoFocus>
            <option value="" disabled>{colQ.data ? 'Pick one' : 'Loading'}</option>
            {cols.length > 0 && (
              <optgroup label="Films">
                {cols.map((c) => <option key={c.id} value={`c:${c.id}`}>{c.name} ({c.films})</option>)}
              </optgroup>
            )}
            {shows.length > 0 && (
              <optgroup label="Shows">
                {shows.map((s) => <option key={s.key} value={`s:${s.key}`}>{s.title}</option>)}
              </optgroup>
            )}
          </select>
        )}
        <label className="lab" htmlFor="nc-title">Name</label>
        <input id="nc-title" className="inp" value={title} maxLength={200} placeholder="Same as the franchise"
          onChange={(e) => setTitle(e.target.value)} />
        <Notice msg={msg} onClose={clear} />
        <div className="row" style={{ justifyContent: 'flex-end' }}>
          <button type="button" className="btn" onClick={onClose}>Cancel</button>
          <button type="submit" className="btn btn-primary" disabled={busy || !pick}>Find scenes</button>
        </div>
      </form>
    </Dialog>
  );
}

export function MyCuts() {
  const cutsQ = useApi('/cuts');
  const jobsQ = useApi('/jobs');
  const settingsQ = useApi('/settings', { quiet: true });
  const suggQ = useApi('/suggestions', { quiet: true });
  const [doomed, setDoomed] = useState(null);
  const [starting, setStarting] = useState(false);
  const [updates, setUpdates] = useState(null);
  const { act, msg, busy, clear } = useAct();
  const jobs = jobsQ.data?.jobs || [];
  const running = jobs.some((j) => ACTIVE.includes(j.status));
  usePoll(() => { jobsQ.reload(); if (!running) cutsQ.reload(); }, 2000, running);

  if (cutsQ.error && !cutsQ.data) return <div className="page"><ErrorBox error={cutsQ.error} onRetry={cutsQ.reload} /></div>;
  if (!cutsQ.data) return <div className="page"><Loading /></div>;

  const cuts = cutsQ.data.cuts || [];
  const plex = Boolean(settingsQ.data?.library?.plex?.url);
  const upd = Object.fromEntries((updates || []).map((u) => [u.id, u]));
  const ready = suggQ.data ? suggQ.data.suggestions.filter((s) => s.status === 'ready').length : null;
  const lastJob = (tid) => jobs.find((j) => j.kind === 'timeline_render' && j.payload?.timeline_id === tid);

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
  }, (r) => (r.changed ? `Updated ${c.title}${r.summary ? ` (${r.summary})` : ''}. Build it again to re-match the cut points.` : `${c.title} was already up to date`));
  const remove = (cut) => act(async () => {
    await api(`/timelines/${enc(cut.id)}`, { method: 'DELETE' });
    setDoomed(null);
    await cutsQ.reload();
  }, `Deleted ${cut.title}`);

  const card = (c) => {
    if (c.kind === 'playlist') {
      return (
        <li key={c.id} className="mycuts-card">
          <Link to={`/discover/${enc(c.suggestion)}`} className="mycuts-link">
            <CutCover cut={c} />
            <span className="mycuts-name hd">{c.title}</span>
          </Link>
          <span className="mono mycuts-facts">Playlist · {plural(c.films.length, 'film')}</span>
          <span className="mycuts-foot">
            <span className="state state-ready">In Plex</span>
            {plex && <MoreMenu className="btn btn-sm btn-quiet" items={[
              ['Open in Plex', () => act(() => openInPlex(`/playlists/${enc(c.playlist_id)}/plex`))],
            ]} />}
          </span>
        </li>
      );
    }
    const job = lastJob(c.id);
    const u = upd[c.id];
    const [text, cls] = cutState(c, job, u?.update);
    const building = job && ACTIVE.includes(job.status);
    const films = c.tmdb_ids?.length || 0;
    const eps = c.episode_ids?.length || 0;
    const facts = [c.minutes ? `${Math.round(c.minutes)} m` : null, plural(c.segments, 'scene'),
      films ? plural(films, 'film') : eps ? plural(eps, 'episode') : null].filter(Boolean).join(' · ');
    return (
      <li key={c.id} className="mycuts-card">
        <Link to={`/cuts/${enc(c.id)}`} className="mycuts-link">
          <CutCover cut={c} />
          <span className="mycuts-name hd">{c.title}</span>
        </Link>
        <span className="mono mycuts-facts">{facts}</span>
        <span className="mycuts-foot">
          <span className={cls} title={u?.update ? u.summary || undefined : undefined}>{text}</span>
          <MoreMenu LinkAs={Link} className="btn btn-sm btn-quiet" items={[
            u?.update && ['Update', () => update(c), { disabled: busy }],
            building ? ['Open in Jobs', { to: '/jobs' }] : ['Build', { to: `/cuts/${enc(c.id)}/build` }],
            plex && job?.status === 'done' && ['Open in Plex', () => act(() => openInPlex(`/cuts/${enc(c.id)}/plex`))],
            ['Delete…', () => setDoomed(c), { danger: true }],
          ]} />
        </span>
      </li>
    );
  };

  return (
    <div className="page mycuts">
      <header className="mycuts-head">
        <h1>My cuts</h1>
        <span className="mono mycuts-count">{cuts.length}</span>
        <button type="button" className="btn btn-sm btn-quiet" disabled={busy} onClick={checkUpdates}>Check for updates</button>
        <button type="button" className="btn btn-primary" onClick={() => setStarting(true)}>New cut</button>
      </header>
      <Notice msg={msg} onClose={clear} />
      <ErrorBox error={jobsQ.error} onRetry={jobsQ.reload} />

      {cuts.length > 0 ? (
        <ul className="mycuts-grid" aria-label="Your cuts">
          {cuts.map(card)}
          <li className="mycuts-card">
            <button type="button" className="mycuts-new" onClick={() => setStarting(true)}>
              <svg width="26" height="26" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden="true"><path d="M12 5v14M5 12h14" /></svg>
              <span>New cut</span>
            </button>
          </li>
        </ul>
      ) : (
        <section className="mycuts-empty" aria-label="No cuts yet">
          <h2>No cuts yet</h2>
          <button type="button" className="btn" onClick={() => setStarting(true)}>Make your first cut</button>
        </section>
      )}

      <section aria-labelledby="mycuts-start">
        <h2 id="mycuts-start">Start a cut</h2>
        <div className="mycuts-ways">
          <button type="button" className="mycuts-way is-ai" onClick={() => setStarting(true)}>
            <span className="lab">With AI</span>
            <span className="hd mycuts-way-name">Ask for scenes</span>
            <span className="mono mycuts-way-note">Pick a franchise, then ask</span>
          </button>
          <Link to="/library" className="mycuts-way">
            <span className="lab">From one film</span>
            <span className="hd mycuts-way-name">A trailer, a person, a tag</span>
            <span className="mono mycuts-way-note">Library</span>
          </Link>
          <Link to="/" className="mycuts-way">
            <span className="lab">Ready made</span>
            <span className="hd mycuts-way-name">Browse Discover</span>
            <span className="mono mycuts-way-note">{ready != null ? plural(ready, 'cut') : 'Discover'}</span>
          </Link>
        </div>
      </section>

      {starting && <NewCutDialog onClose={() => setStarting(false)} />}
      {doomed && (
        <Dialog title={`Delete ${doomed.title}?`} onClose={() => setDoomed(null)}>
          <p className="muted" style={{ margin: 0 }}>
            This removes the recipe and its matched cut points. Files already built stay where they are.
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
