import { NavLink, Route, Routes } from 'react-router';
import { Library } from './pages/Library.jsx';
import { SeriesPage } from './pages/Series.jsx';
import { CutPage } from './pages/Cut.jsx';
import { Jobs } from './pages/Jobs.jsx';
import { Settings } from './pages/Settings.jsx';
import { Doctor } from './pages/Doctor.jsx';
import { Empty } from './components/ui.jsx';

const NAV = [['/', 'Library'], ['/jobs', 'Jobs'], ['/settings', 'Settings'], ['/doctor', 'Status']];

export function App() {
  return (
    <div className="shell">
      <aside className="side">
        <div className="brand">Fans<span>Cut</span></div>
        <nav className="nav">
          {NAV.map(([to, name]) => (
            <NavLink key={to} to={to} end={to === '/'}>{name}</NavLink>
          ))}
        </nav>
        <div className="side-foot muted"><a href="/docs" target="_blank" rel="noreferrer">API docs</a></div>
      </aside>
      <main className="main">
        <Routes>
          <Route path="/" element={<Library />} />
          <Route path="/series/:key" element={<SeriesPage />} />
          <Route path="/series/:key/cut" element={<CutPage />} />
          <Route path="/jobs" element={<Jobs />} />
          <Route path="/settings" element={<Settings />} />
          <Route path="/doctor" element={<Doctor />} />
          <Route path="*" element={<Empty>Nothing here. <NavLink to="/">Back to the library</NavLink></Empty>} />
        </Routes>
      </main>
    </div>
  );
}
