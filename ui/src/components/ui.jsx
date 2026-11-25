import { useEffect, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
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

const FOCUSABLE = 'a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])';

// A modal in a portal on <body>: Esc and the backdrop close it, the page behind
// does not scroll, focus moves in, stays in (Tab wraps), and returns on close.
export function Dialog({ title, onClose, children, wide }) {
  const ref = useRef(null);
  const closeRef = useRef(onClose);
  useEffect(() => { closeRef.current = onClose; });
  useEffect(() => {
    const before = document.activeElement;
    const overflow = document.body.style.overflow;
    document.body.style.overflow = 'hidden';
    const box = ref.current;
    const first = box?.querySelector('[autofocus]') || box?.querySelector(FOCUSABLE);
    (first || box)?.focus();
    const onKey = (e) => {
      if (e.key === 'Escape') { e.stopPropagation(); closeRef.current(); return; }
      if (e.key !== 'Tab' || !box) return;
      const items = [...box.querySelectorAll(FOCUSABLE)];
      if (!items.length) return;
      const [a, z] = [items[0], items[items.length - 1]];
      if (e.shiftKey && document.activeElement === a) { e.preventDefault(); z.focus(); }
      else if (!e.shiftKey && document.activeElement === z) { e.preventDefault(); a.focus(); }
    };
    window.addEventListener('keydown', onKey);
    return () => {
      window.removeEventListener('keydown', onKey);
      document.body.style.overflow = overflow;
      if (before && typeof before.focus === 'function') before.focus();
    };
  }, []);
  return createPortal(
    <div className="scrim" onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <div ref={ref} tabIndex={-1} className={`dialog${wide ? ' dialog-wide' : ''}`} role="dialog" aria-modal="true" aria-label={title}>
        <div className="dialog-head">
          <h2>{title}</h2>
          <button className="btn btn-ghost btn-sm" onClick={onClose} aria-label="Close">✕</button>
        </div>
        {children}
      </div>
    </div>,
    document.body,
  );
}

// Click once to arm ("Sure?"), again to act. Clicking elsewhere or waiting
// 4 s disarms it. For deletes that don't need a whole dialog.
export function ConfirmButton({ onConfirm, children, confirmText = 'Sure?', className = 'btn btn-sm', disabled }) {
  const [armed, setArmed] = useState(false);
  const ref = useRef(null);
  useEffect(() => {
    if (!armed) return undefined;
    const t = setTimeout(() => setArmed(false), 4000);
    const off = (e) => { if (!ref.current?.contains(e.target)) setArmed(false); };
    document.addEventListener('mousedown', off);
    return () => { clearTimeout(t); document.removeEventListener('mousedown', off); };
  }, [armed]);
  return (
    <button ref={ref} type="button" disabled={disabled} className={`${className}${armed ? ' confirm-armed' : ''}`}
      onClick={() => { if (armed) { setArmed(false); onConfirm(); } else setArmed(true); }}>
      {armed ? confirmText : children}
    </button>
  );
}

// Poster-shaped placeholders while a grid or shelf loads.
export function SkeletonTiles({ n = 8, shelf = false }) {
  return (
    <div className={shelf ? 'shelf' : 'grid'} aria-busy="true" aria-label="Loading">
      {Array.from({ length: n }, (_, i) => (
        <div key={i} className="tile" aria-hidden="true">
          <div className="skel skel-tile" />
          <div className="skel skel-line" />
        </div>
      ))}
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
