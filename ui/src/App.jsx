import { Link, Navigate, NavLink, Route, Routes, useParams } from 'react-router';
import { api } from './lib/api.js';
import { useLoad, usePoll } from './lib/hooks.js';
import { Discover } from './pages/Discover.jsx';
import { LibraryPage } from './pages/LibraryPage.jsx';
import { CollectionPage } from './pages/Collection.jsx';
import { MoviePage } from './pages/Movie.jsx';
import { ScenesPage } from './pages/Scenes.jsx';
import { CollectionScenesPage } from './pages/CollectionScenes.jsx';
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

function Logo() {
  return (
    <svg width="20" height="20" viewBox="0 0 20 20" aria-hidden="true">
      <rect x="2" y="4" width="7" height="12" rx="1.5" fill="#F2A93B" />
      <rect x="11" y="4" width="7" height="12" rx="1.5" fill="none" stroke="#F2A93B" strokeWidth="1.5" />
    </svg>
  );
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
      <Link to="/" className="brand"><Logo /><span>FansCut</span></Link>
      <div className="nav">
        {items.map(([to, name, count, accent]) => (
          <NavLink key={to} to={to} end={to === '/'}>
            <span>{name}</span>
            {count != null && <span className={`count${accent ? ' count-accent' : ''}`}>{count}</span>}
          </NavLink>
        ))}
      </div>
      <div className="side-foot">
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

export function App() {
  return (
    <div className="shell">
      <Nav />
      <main className="main">
        <Routes>
          <Route path="/" element={<Discover />} />
          <Route path="/library" element={<LibraryPage />} />
          <Route path="/collections/:name" element={<CollectionPage />} />
          <Route path="/collections/:name/scenes" element={<CollectionScenesPage />} />
          <Route path="/movies/:key" element={<MoviePage />} />
          <Route path="/movies/:key/scenes" element={<ScenesPage />} />
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
