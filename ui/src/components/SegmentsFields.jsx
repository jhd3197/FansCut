import { useState } from 'react';
import { fmtTime, parseTime } from '../lib/time.js';

// Edit the custom kept ranges of one episode: the "my own cut" mode. The listed
// pieces are used exactly as given and beat the preset and trims. save(ranges)
// sends [[start, end], ...] in seconds; save(null) clears back to preset mode.
export function SegmentsFields({ episode, save }) {
  const [rows, setRows] = useState(null); // null = show the saved segments
  const [error, setError] = useState(null);
  const saved = episode.segments || [];
  const list = rows ?? saved;
  const dirty = rows != null;

  const update = (i, edge, text) => {
    const v = parseTime(text);
    setError(null);
    setRows((r) => (r ?? saved).map((seg, j) => (j === i ? { ...seg, [edge]: v ?? seg[edge] } : seg)));
  };
  const add = () => setRows([...list, [0, Math.min(60, episode.duration || 60)]]);
  const remove = (i) => setRows(list.filter((_, j) => j !== i));

  const submit = async () => {
    const bad = list.find((s) => !(s[0] >= 0) || !(s[1] > s[0]) ||
      (episode.duration && s[0] >= episode.duration));
    if (bad) {
      setError(`invalid range ${fmtTime(bad[0])}–${fmtTime(bad[1])}: needs 0 ≤ start < end`);
      return;
    }
    setError(null);
    await save(list.length ? list.map((s) => [s[0], s[1]]) : null);
    setRows(null);
  };

  return (
    <div className="trimfields" style={{ gap: 10 }}>
      {error && <div className="notice notice-err" role="alert">{error}</div>}
      {list.length === 0 && <p className="muted" style={{ margin: 0 }}>No custom segments: the episode follows the preset and trims.</p>}
      {list.length > 0 && (
        <div style={{ border: '1px solid var(--line)', borderRadius: 10, overflow: 'hidden' }}>
          {list.map((seg, i) => (
            <div key={`${i}:${seg[0]}-${seg[1]}`}
              style={{ display: 'grid', gridTemplateColumns: '60px auto auto 1fr auto', gap: 12, alignItems: 'center', padding: '10px 16px', borderBottom: i === list.length - 1 ? 0 : '1px solid var(--raised)' }}>
              <b className="mono" style={{ fontWeight: 600 }}>#{i + 1}</b>
              <label className="edge"><span className="muted tiny">start</span>
                <input className="input input-time" defaultValue={fmtTime(seg[0], 1)} aria-label={`Segment ${i + 1} start`}
                  onBlur={(e) => update(i, 0, e.target.value)}
                  onKeyDown={(e) => e.key === 'Enter' && e.currentTarget.blur()} />
              </label>
              <label className="edge"><span className="muted tiny">end</span>
                <input className="input input-time" defaultValue={fmtTime(seg[1], 1)} aria-label={`Segment ${i + 1} end`}
                  onBlur={(e) => update(i, 1, e.target.value)}
                  onKeyDown={(e) => e.key === 'Enter' && e.currentTarget.blur()} />
              </label>
              <span className="mono tiny muted">{fmtTime(Math.max(seg[1] - seg[0], 0))} kept</span>
              <button type="button" className="btn btn-sm btn-ghost" aria-label={`Remove segment ${i + 1}`} onClick={() => remove(i)}>Remove</button>
            </div>
          ))}
        </div>
      )}
      <div className="row">
        <button type="button" className="btn btn-sm" onClick={add}>Add segment</button>
        {(dirty || saved.length > 0) && (
          <button type="button" className="btn btn-sm btn-primary" onClick={submit}>Save segments</button>
        )}
        {saved.length > 0 && (
          <button type="button" className="btn btn-sm" onClick={() => { setRows(null); save(null); }}>Clear all</button>
        )}
      </div>
    </div>
  );
}
