import { lazy, Suspense, useEffect, useState } from 'react';
import { Link, Navigate, NavLink, Route, Routes, useLocation, useParams } from 'react-router';
import { api, AUTH_EVENT } from './lib/api.js';
import { usePoll } from './lib/hooks.js';
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
import { SuggestionPage } from './pages/Suggestion.jsx';
import { MyCuts } from './pages/MyCuts.jsx';
import { Jobs } from './pages/Jobs.jsx';
import { Health } from './pages/Health.jsx';
import { Settings } from './pages/Settings.jsx';
import { Empty, Loading } from './components/ui.jsx';
import { ErrorBoundary, LoadingBar, MobileNav, StatusChecker, Toasts } from './components/shell.jsx';
import { Logs } from './pages/Logs.jsx';
import { t } from './i18n/index.js';

// The two editors are the heaviest pages: load them only when opened.
const Build = lazy(() => import('./pages/Build.jsx').then((m) => ({ default: m.Build })));
const TimelineEdit = lazy(() => import('./pages/TimelineEdit.jsx').then((m) => ({ default: m.TimelineEdit })));
import { useApi } from './lib/store.js';

// The Framehouse logo (brand kit: white horizontal logo, for the Deep Teal sidebar).
function Logo() {
  return <img src={logoWhite} alt="FansCut" width="160" height="39" />;
}

// Nav counts: ready suggestions, cuts, running jobs, open health findings.
function useCounts() {
  const sugg = useApi('/suggestions', { quiet: true });
  const cuts = useApi('/cuts', { quiet: true });
  const health = useApi('/library/check/last', { quiet: true });
  const jobs = useApi('/jobs', { quiet: true });
  usePoll(jobs.reload, 5000, true);
  const running = (jobs.data?.jobs || []).filter((j) => j.status === 'running' || j.status === 'queued').length;
  const s = health.data?.summary;
  return {
    discover: sugg.data ? sugg.data.suggestions.filter((x) => x.status === 'ready').length : null,
    cuts: cuts.data ? cuts.data.cuts.length : null,
    jobs: running ? t('nav.running', { n: running }) : null,
    health: s ? (s.error + s.warn) || null : null,
  };
}

function navItems(n) {
  return [
    ['/', t('nav.discover'), n.discover, true],
    ['/library', t('nav.library')],
    ['/my-cuts', t('nav.myCuts'), n.cuts],
    ['/jobs', t('nav.jobs'), n.jobs],
    ['/health', t('nav.health'), n.health],
    ['/settings', t('nav.settings')],
  ];
}

function Nav({ items }) {
  const { pathname } = useLocation();
  const settings = useApi('/settings', { quiet: true });
  const src = sourceSummary(settings.data);
  return (
    <nav className="side" aria-label="Main">
      <Link to="/" className="brand" aria-label="FansCut home"><Logo /></Link>
      <div className="nav">
        {items.map(([to, name, count, accent]) => (
          <NavLink key={to} to={to} end={to === '/'}
            className={to === '/' && pathname.startsWith('/discover/') ? 'active' : undefined}>
            <span>{name}</span>
            {count != null && <span className={`count${accent ? ' count-accent' : ''}`}>{count}</span>}
          </NavLink>
        ))}
      </div>
      <div className="side-foot">
        {src && <span title="Library source">{src.label}</span>}
        {src?.root && <span className="mono" title={src.root} style={{ fontSize: 11 }}>{src.root}</span>}
        <Link to="/logs" className="muted">{t('nav.logs')}</Link>
        <a href="/docs" target="_blank" rel="noreferrer" className="muted">{t('nav.apiDocs')}</a>
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
        <h1 className="sh" style={{ marginTop: 0 }}>{t('auth.title')}</h1>
        <p className="muted tiny">{t('auth.hint')}</p>
        <input className="inp mono" type="password" autoComplete="current-password" aria-label="API key"
          value={key} onChange={(e) => setKey(e.target.value)} autoFocus style={{ width: '100%' }} />
        {err && <p role="alert" className="tiny" style={{ color: 'var(--danger, #b42318)' }}>{err}</p>}
        <button className="btn btn-primary" type="submit" style={{ marginTop: 12 }} disabled={!key}>{t('auth.unlock')}</button>
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
  const counts = useCounts();
  const items = navItems(counts);
  const where = useLocation().pathname;
  useEffect(() => {
    // detail pages set their own (the movie or show name); these are the fixed ones
    const fixed = { '/': 'Discover', '/library': 'Library', '/my-cuts': 'My cuts', '/jobs': 'Jobs',
      '/health': 'Health', '/settings': 'Settings', '/logs': 'Logs' };
    if (fixed[where]) document.title = `${fixed[where]} · FansCut`;
  }, [where]);
  if (locked) return <KeyGate />;
  // The editor is full screen, like an NLE: no app sidebar (its bar has a Home link).
  if (/^\/cuts\/[^/]+\/edit\/?$/.test(where)) {
    return (
      <div className="shell-full">
        <LoadingBar />
        <main className="main main-full">
          <StatusChecker />
          <ErrorBoundary resetKey={where}>
            <Suspense fallback={<div className="page"><Loading /></div>}>
              <Routes><Route path="/cuts/:id/edit" element={<TimelineEdit />} /></Routes>
            </Suspense>
          </ErrorBoundary>
        </main>
        <Toasts />
      </div>
    );
  }
  return (
    <div className="shell">
      <LoadingBar />
      <Nav items={items} />
      <main className="main">
        <StatusChecker />
        <ErrorBoundary resetKey={where}>
        <Suspense fallback={<div className="page"><Loading /></div>}>
        <Routes>
          <Route path="/" element={<Discover />} />
          <Route path="/discover/:id" element={<SuggestionPage />} />
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
          <Route path="/logs" element={<Logs />} />
          <Route path="*" element={<Empty>{t('shell.notFound')} <Link to="/">{t('shell.backHome')}</Link></Empty>} />
        </Routes>
        </Suspense>
        </ErrorBoundary>
      </main>
      <MobileNav items={items.slice(0, 5)} />
      <Toasts />
    </div>
  );
}
