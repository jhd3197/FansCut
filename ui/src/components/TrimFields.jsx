import { useState } from 'react';
import { TRIM_KINDS, TRIM_NAMES, checkRange, nudge } from '../lib/timeline.js';
import { fmtTime, parseTime } from '../lib/time.js';

// Longer names for the rows; TRIM_NAMES stays the short form used in aria labels.
export const TRIM_TITLES = { recap: 'Recap', intro: 'Opening', outro: 'Ending', preview: 'Next-episode preview' };
const SOURCE = { auto: 'detected', user: 'edited by you' };

const ROW = {
  display: 'grid', gridTemplateColumns: '150px minmax(150px, 1fr) minmax(150px, 1fr) 110px auto',
  gap: 12, alignItems: 'center', padding: '10px 16px', borderBottom: '1px solid var(--raised)',
};

function Edge({ label, value, onCommit }) {
  const [text, setText] = useState(null); // null = show the saved value
  const shown = text ?? (value == null ? '' : fmtTime(value, 1));
  const commit = () => {
    if (text == null) return;
    const v = parseTime(text);
    setText(null);
    if (v != null) onCommit(v);
  };
  return (
    <input className="input input-time" value={shown} aria-label={label}
      onChange={(e) => setText(e.target.value)} onBlur={commit}
      onKeyDown={(e) => e.key === 'Enter' && e.currentTarget.blur()} />
  );
}

// Edit the recap/OP/ED/preview ranges of one episode. `save(kind, start, end)` and
// `reset(kind)` talk to the API; start=end=null records "no such range here".
// Optional: `preview(kind, range)` adds a Preview button; `notes[kind]` replaces the
// source text (e.g. "kept by a setting").
export function TrimFields({ episode, save, reset, step = 1, preview, notes = {} }) {
  const [error, setError] = useState(null);
  const d = episode.duration;
  const put = async (kind, start, end) => {
    const bad = start == null && end == null ? null : checkRange(start, end, d);
    setError(bad);
    if (!bad) await save(kind, start, end);
  };
  const nudgeBtns = (kind, t, edge) => {
    const name = TRIM_NAMES[kind];
    const set = (delta) => {
      const n = nudge(t, edge, delta, d);
      return edge === 'start' ? put(kind, n.start, t.end) : put(kind, t.start, n.end);
    };
    return (
      <>
        <button type="button" className="btn btn-sm" aria-label={`${name} ${edge} earlier`} onClick={() => set(-step)}>−{step} s</button>
        <button type="button" className="btn btn-sm" aria-label={`${name} ${edge} later`} onClick={() => set(step)}>+{step} s</button>
      </>
    );
  };
  return (
    <div className="trimfields" style={{ gap: 10 }}>
      {error && <div className="notice notice-err" role="alert">{error}</div>}
      <div style={{ border: '1px solid var(--line)', borderRadius: 10, overflowX: 'auto' }}>
        <div style={{ ...ROW, padding: '8px 16px', fontSize: 12, color: 'var(--muted)', minWidth: 760 }}>
          <span>Part</span><span>Start</span><span>End</span><span>Source</span><span />
        </div>
        {TRIM_KINDS.map((kind, i) => {
          const t = (episode.trims || {})[kind];
          const has = t && t.start != null;
          const last = i === TRIM_KINDS.length - 1;
          return (
            <div key={kind} style={{ ...ROW, minWidth: 760, borderBottom: last ? 0 : ROW.borderBottom }}>
              <b style={{ fontWeight: 600 }}>{TRIM_TITLES[kind]}</b>
              {has ? (
                <>
                  <span className="row">
                    <Edge label="start" value={t.start} onCommit={(v) => put(kind, v, t.end)} />
                    {nudgeBtns(kind, t, 'start')}
                  </span>
                  <span className="row">
                    <Edge label="end" value={t.end} onCommit={(v) => put(kind, t.start, v)} />
                    {nudgeBtns(kind, t, 'end')}
                  </span>
                </>
              ) : (
                <>
                  <span className="mono muted">{t ? 'cleared by you' : 'not found'}</span>
                  <span />
                </>
              )}
              <span className="tiny muted">{notes[kind] || (t ? SOURCE[t.source] || t.source : '')}</span>
              <span className="row" style={{ justifyContent: 'flex-end' }}>
                {has && preview && <button type="button" className="btn btn-sm" onClick={() => preview(kind, t)}>Preview</button>}
                {has && <button type="button" className="btn btn-sm" onClick={() => put(kind, null, null)}>Clear</button>}
                {!has && <button type="button" className="btn btn-sm" onClick={() => put(kind, 0, Math.min(90, d || 90))}>Add</button>}
                {t && <button type="button" className="btn btn-ghost btn-sm" title="Forget my edit; let detection fill it again"
                  onClick={() => reset(kind)}>Reset</button>}
              </span>
            </div>
          );
        })}
      </div>
    </div>
  );
}
