import { useEffect } from 'react';
import { labelTitle } from '../lib/labels.js';

export function LabelBadge({ label, source }) {
  return <span className={`badge badge-${label}`} title={labelTitle(label, source)}>{label}</span>;
}

export function Notice({ msg, onClose }) {
  if (!msg) return null;
  return (
    <div className={`notice ${msg.ok ? 'notice-ok' : 'notice-err'}`} role={msg.ok ? 'status' : 'alert'}>
      <span>{msg.text}</span>
      {onClose && <button className="btn btn-ghost btn-sm" onClick={onClose} aria-label="Dismiss">✕</button>}
    </div>
  );
}

export function Loading({ what = 'Loading' }) {
  return <p className="muted pad">{what}…</p>;
}

export function ErrorBox({ error, onRetry }) {
  if (!error) return null;
  return (
    <div className="notice notice-err" role="alert">
      <span>{error.message}</span>
      {onRetry && <button className="btn btn-sm" onClick={onRetry}>Retry</button>}
    </div>
  );
}

export function Empty({ children }) {
  return <div className="empty">{children}</div>;
}

export function PageHeader({ title, sub, children }) {
  return (
    <header className="page-head">
      <div>
        <h1>{title}</h1>
        {sub && <p className="muted">{sub}</p>}
      </div>
      <div className="row">{children}</div>
    </header>
  );
}

export function Dialog({ title, onClose, children, wide }) {
  useEffect(() => {
    const onKey = (e) => e.key === 'Escape' && onClose();
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [onClose]);
  return (
    <div className="scrim" onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <div className={`dialog${wide ? ' dialog-wide' : ''}`} role="dialog" aria-modal="true" aria-label={title}>
        <div className="dialog-head">
          <h2>{title}</h2>
          <button className="btn btn-ghost btn-sm" onClick={onClose} aria-label="Close">✕</button>
        </div>
        {children}
      </div>
    </div>
  );
}

export function Progress({ value }) {
  const pct = Math.round(Math.min(Math.max(value || 0, 0), 1) * 100);
  return (
    <div className="progress" role="progressbar" aria-valuenow={pct} aria-valuemin={0} aria-valuemax={100}>
      <div style={{ width: `${pct}%` }} />
    </div>
  );
}

export function StatusTag({ status }) {
  return <span className={`tag tag-${status}`}>{status}</span>;
}
