import { useState } from 'react';
import { TRIM_KINDS, TRIM_NAMES, checkRange, nudge } from '../lib/timeline.js';
import { fmtTime, parseTime } from '../lib/time.js';

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
    <label className="edge">
      <span className="muted">{label}</span>
      <input className="input input-time" value={shown} aria-label={label}
        onChange={(e) => setText(e.target.value)} onBlur={commit}
        onKeyDown={(e) => e.key === 'Enter' && e.currentTarget.blur()} />
    </label>
  );
}

// Edit the recap/OP/ED/preview ranges of one episode. `save(kind, start, end)` and
// `reset(kind)` talk to the API; start=end=null records "no such range here".
export function TrimFields({ episode, save, reset, step = 1 }) {
  const [error, setError] = useState(null);
  const d = episode.duration;
  const put = async (kind, start, end) => {
    const bad = start == null && end == null ? null : checkRange(start, end, d);
    setError(bad);
    if (!bad) await save(kind, start, end);
  };
  return (
    <div className="trimfields">
      {error && <div className="notice notice-err" role="alert">{error}</div>}
      {TRIM_KINDS.map((kind) => {
        const t = (episode.trims || {})[kind];
        const has = t && t.start != null;
        return (
          <div key={kind} className="trimrow">
            <strong>{TRIM_NAMES[kind]}</strong>
            {has ? (
              <>
                <Edge label="start" value={t.start} onCommit={(v) => put(kind, v, t.end)} />
                <button className="btn btn-sm" aria-label={`${TRIM_NAMES[kind]} start earlier`}
                  onClick={() => put(kind, nudge(t, 'start', -step, d).start, t.end)}>−{step}s</button>
                <button className="btn btn-sm" aria-label={`${TRIM_NAMES[kind]} start later`}
                  onClick={() => put(kind, nudge(t, 'start', step, d).start, t.end)}>+{step}s</button>
                <Edge label="end" value={t.end} onCommit={(v) => put(kind, t.start, v)} />
                <button className="btn btn-sm" aria-label={`${TRIM_NAMES[kind]} end earlier`}
                  onClick={() => put(kind, t.start, nudge(t, 'end', -step, d).end)}>−{step}s</button>
                <button className="btn btn-sm" aria-label={`${TRIM_NAMES[kind]} end later`}
                  onClick={() => put(kind, t.start, nudge(t, 'end', step, d).end)}>+{step}s</button>
                <span className="muted tiny">{t.source}</span>
                <button className="btn btn-sm" onClick={() => put(kind, null, null)}>Clear</button>
              </>
            ) : (
              <>
                <span className="muted">{t ? 'cleared' : 'none'}</span>
                <button className="btn btn-sm" onClick={() => put(kind, 0, Math.min(90, d || 90))}>Add</button>
              </>
            )}
            {t && <button className="btn btn-ghost btn-sm" title="Forget my edit; let detection fill it again"
              onClick={() => reset(kind)}>Reset</button>}
          </div>
        );
      })}
    </div>
  );
}
