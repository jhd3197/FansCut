// App-wide pieces: error boundary, top progress bar, new-version banner,
// toasts, and the phone tab bar.
import { Component, useEffect, useRef, useState } from 'react';
import { NavLink } from 'react-router';
import { api } from '../lib/api.js';
import { usePoll } from '../lib/hooks.js';
import { useBusy } from '../lib/store.js';
import { dismiss, subscribeToasts } from '../lib/toast.js';
import { t } from '../i18n/index.js';

// A render error in one page shows a way out instead of blanking the app.
export class ErrorBoundary extends Component {
  constructor(props) {
    super(props);
    this.state = { error: null };
  }

  static getDerivedStateFromError(error) {
    return { error };
  }

  componentDidCatch(error, info) {
    console.error('page crashed', error, info?.componentStack);
  }

  componentDidUpdate(prev) {
    // navigating elsewhere clears the error
    if (this.state.error && prev.resetKey !== this.props.resetKey) this.setState({ error: null });
  }

  render() {
    if (!this.state.error) return this.props.children;
    return (
      <div className="page" role="alert">
        <h1>{t('shell.crashTitle')}</h1>
        <p className="muted">{String(this.state.error.message || this.state.error)}</p>
        <div style={{ display: 'flex', gap: 10 }}>
          <button className="btn btn-primary" type="button" onClick={() => window.location.reload()}>{t('shell.reload')}</button>
          <a className="btn" href="/">{t('shell.backHome')}</a>
        </div>
      </div>
    );
  }
}

// A thin bar along the top while any data is loading (after a short delay, so
// fast loads don't flicker).
export function LoadingBar() {
  const busy = useBusy();
  const [late, setLate] = useState(false);
  useEffect(() => {
    if (!busy) return undefined;
    const t = setTimeout(() => setLate(true), 250);
    return () => { clearTimeout(t); setLate(false); };
  }, [busy]);
  return <div className={`loadbar${busy && late ? ' is-on' : ''}`} aria-hidden="true" />;
}

// Polls /api/status: offers a reload when the server runs a newer build than
// this tab loaded, and says when saved settings wait for a restart.
const buildOf = (s) => `${s.version}|${s.commit}|${s.started_at}`;

export function StatusChecker() {
  const first = useRef(null);
  const [st, setSt] = useState(null);
  const [hidden, setHidden] = useState(false);
  const check = () => api('/status').then((s) => {
    if (first.current == null) first.current = buildOf(s);
    setSt({ ...s, changed: first.current !== buildOf(s) });
  }).catch(() => {});
  useEffect(() => { check(); }, []);
  usePoll(check, 60000, true);
  if (!st || hidden) return null;
  const changed = st.changed;
  if (changed) {
    return (
      <div className="banner" role="status">
        <span>{t('shell.updated')}</span>
        <button className="btn btn-sm btn-primary" type="button" onClick={() => window.location.reload()}>{t('shell.reload')}</button>
        <button className="btn btn-sm btn-ghost" type="button" onClick={() => setHidden(true)} aria-label={t('shell.dismiss')}>✕</button>
      </div>
    );
  }
  if (st.restart_required) {
    return (
      <div className="banner" role="status">
        <span>{t('shell.restart', { fields: st.restart_fields.join(', ') })}</span>
        <button className="btn btn-sm btn-ghost" type="button" onClick={() => setHidden(true)} aria-label={t('shell.dismiss')}>✕</button>
      </div>
    );
  }
  return null;
}

export function Toasts() {
  const [items, setItems] = useState([]);
  useEffect(() => subscribeToasts(setItems), []);
  return (
    <div className="toasts" aria-live="polite">
      {items.map((t) => (
        <div key={t.id} className={`toast ${t.ok ? 'toast-ok' : 'toast-err'}`} role={t.ok ? 'status' : 'alert'}>
          <span>{t.text}</span>
          <button type="button" className="btn btn-ghost btn-sm" onClick={() => dismiss(t.id)} aria-label={t('shell.dismiss')}>✕</button>
        </div>
      ))}
    </div>
  );
}

// Phones: the main sections as a bottom tab bar (the sidebar hides).
export function MobileNav({ items }) {
  return (
    <nav className="tabbar" aria-label="Main">
      {items.map(([to, name, count]) => (
        <NavLink key={to} to={to} end={to === '/'}>
          <span>{name}</span>
          {count != null && <span className="tabbar-count">{typeof count === 'number' ? count : '•'}</span>}
        </NavLink>
      ))}
    </nav>
  );
}
