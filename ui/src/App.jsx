import { useEffect, useState } from 'react';
import { Link, Navigate, NavLink, Route, Routes, useParams } from 'react-router';
import { api, AUTH_EVENT } from './lib/api.js';
import { useLoad, usePoll } from './lib/hooks.js';
import { sourceSummary } from './lib/labels.js';
import logoWhite from './brand/logo-white.svg';
import { Discover } from './pages/Discover.jsx';
import { LibraryPage } from './pages/LibraryPage.jsx';
import { CollectionPage } from './pages/Collection.jsx';
import { MoviePage } from './pages/Movie.jsx';
import { ShowPage } from './pages/Show.jsx';
import { SeasonPage } from './pages/Season.jsx';
import { EpisodePage } from './pages/Episode.jsx';
import { CutDetail } from './pages/CutDetail.jsx';
import { Build } from './pages/Build.jsx';
import { MyCuts } from './pages/MyCuts.jsx';
import { TimelineEdit } from './pages/TimelineEdit.jsx';
import { Jobs } from './pages/Jobs.jsx';
import { Health } from './pages/Health.jsx';
import { Settings } from './pages/Settings.jsx';
import { Empty } from './components/ui.jsx';

// The Framehouse logo (brand kit: white horizontal logo, for the Deep Teal sidebar).
function Logo() {
  return <img src={logoWhite} alt="FansCut" width="160" height="39" />;
}

// Nav counts: ready suggestions, cuts, running jobs, open health findings.
function useCounts() {
  const sugg = useLoad(() => api('/suggestions').catch(() => null));
  const cuts = useLoad(() => api('/cuts').catch(() => null));
  const health = useLoad(() => api('/library/check/last').catch(() => null));
  const jobs = useLoad(() => api('/jobs').catch(() => null));
  usePoll(jobs.reload, 5000, true);
  const running = (jobs.data?.jobs || []).filter((j) => j.status === 'running' || j.status === 'queued').length;
  const s = health.data?.summary;
  return {
    discover: sugg.data ? sugg.data.suggestions.filter((x) => x.status === 'ready').length : null,
    cuts: cuts.data ? cuts.data.cuts.length : null,
    jobs: running ? `${running} running` : null,
    health: s ? (s.error + s.warn) || null : null,
  };
}

function Nav() {
  const n = useCounts();
  const settings = useLoad(() => api('/settings').catch(() => null));
  const src = sourceSummary(settings.data);
  const items = [
    ['/', 'Discover', n.discover, true],
    ['/library', 'Library'],
    ['/my-cuts', 'My cuts', n.cuts],
    ['/jobs', 'Jobs', n.jobs],
    ['/health', 'Health', n.health],
    ['/settings', 'Settings'],
  ];
  return (
    <nav className="side" aria-label="Main">
      <Link to="/" className="brand" aria-label="FansCut home"><Logo /></Link>
      <div className="nav">
        {items.map(([to, name, count, accent]) => (
          <NavLink key={to} to={to} end={to === '/'}>
            <span>{name}</span>
            {count != null && <span className={`count${accent ? ' count-accent' : ''}`}>{count}</span>}
          </NavLink>
        ))}
      </div>
      <div className="side-foot">
        {src && <span title="Library source">{src.label}</span>}
        {src?.root && <span className="mono" title={src.root} style={{ fontSize: 11 }}>{src.root}</span>}
        <a href="/docs" target="_blank" rel="noreferrer" className="muted">API docs</a>
      </div>
    </nav>
  );
}

// Old bookmarks keep working.
function OldCut() {
  const { key } = useParams();
  return <Navigate to={`/series/${encodeURIComponent(key)}`} replace />;
}
function OldTimeline() {
  const { tid } = useParams();
  return <Navigate to={`/cuts/${encodeURIComponent(tid)}/edit`} replace />;
}

// server.auth = "api_key": ask for the key once; the server keeps it in a cookie.
function KeyGate() {
  const [key, setKey] = useState('');
  const [err, setErr] = useState(null);
  const submit = async (e) => {
    e.preventDefault();
    try {
      await api('/auth/login', { method: 'POST', body: { key } });
      window.location.reload();
    } catch (x) {
      setErr(x.message);
    }
  };
  return (
    <main className="main" style={{ display: 'grid', placeItems: 'center', minHeight: '100vh' }}>
      <form onSubmit={submit} className="card" style={{ padding: 24, width: 'min(420px, 92vw)' }}>
        <h1 className="sh" style={{ marginTop: 0 }}>API key needed</h1>
        <p className="muted tiny">This FansCut asks for its API key. It is in config.yaml under server.api_key.</p>
        <input className="inp mono" type="password" autoComplete="current-password" aria-label="API key"
          value={key} onChange={(e) => setKey(e.target.value)} autoFocus style={{ width: '100%' }} />
        {err && <p role="alert" className="tiny" style={{ color: 'var(--danger, #b42318)' }}>{err}</p>}
        <button className="btn btn-primary" type="submit" style={{ marginTop: 12 }} disabled={!key}>Unlock</button>
      </form>
    </main>
  );
}

export function App() {
  const [locked, setLocked] = useState(false);
  useEffect(() => {
    const on = () => setLocked(true);
    window.addEventListener(AUTH_EVENT, on);
    return () => window.removeEventListener(AUTH_EVENT, on);
  }, []);
  if (locked) return <KeyGate />;
  return (
    <div className="shell">
      <Nav />
      <main className="main">
        <Routes>
          <Route path="/" element={<Discover />} />
          <Route path="/library" element={<LibraryPage />} />
          <Route path="/collections/:name" element={<CollectionPage />} />
          <Route path="/movies/:key" element={<MoviePage />} />
          <Route path="/series/:key" element={<ShowPage />} />
          <Route path="/series/:key/season/:season" element={<SeasonPage />} />
          <Route path="/series/:key/episode/:eid" element={<EpisodePage />} />
          <Route path="/series/:key/cut" element={<OldCut />} />
          <Route path="/series/:key/timelines/:tid" element={<OldTimeline />} />
          <Route path="/cuts/:id" element={<CutDetail />} />
          <Route path="/cuts/:id/build" element={<Build />} />
          <Route path="/cuts/:id/edit" element={<TimelineEdit />} />
          <Route path="/my-cuts" element={<MyCuts />} />
          <Route path="/jobs" element={<Jobs />} />
          <Route path="/health" element={<Health />} />
          <Route path="/settings" element={<Settings />} />
          <Route path="*" element={<Empty>Nothing here. <Link to="/">Back to Discover</Link></Empty>} />
        </Routes>
      </main>
    </div>
  );
}
