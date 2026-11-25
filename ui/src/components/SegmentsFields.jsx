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
    <div className="trimfields">
      {error && <div className="notice notice-err" role="alert">{error}</div>}
      {list.length === 0 && <p className="muted">No custom segments: the episode follows the preset and trims.</p>}
      {list.map((seg, i) => (
        <div key={`${i}:${seg[0]}-${seg[1]}`} className="trimrow">
          <strong>#{i + 1}</strong>
          <label className="edge"><span className="muted">start</span>
            <input className="input input-time" defaultValue={fmtTime(seg[0], 1)} aria-label={`Segment ${i + 1} start`}
              onBlur={(e) => update(i, 0, e.target.value)}
              onKeyDown={(e) => e.key === 'Enter' && e.currentTarget.blur()} />
          </label>
          <label className="edge"><span className="muted">end</span>
            <input className="input input-time" defaultValue={fmtTime(seg[1], 1)} aria-label={`Segment ${i + 1} end`}
              onBlur={(e) => update(i, 1, e.target.value)}
              onKeyDown={(e) => e.key === 'Enter' && e.currentTarget.blur()} />
          </label>
          <button className="btn btn-sm btn-ghost" aria-label={`Remove segment ${i + 1}`} onClick={() => remove(i)}>✕</button>
        </div>
      ))}
      <div className="row" style={{ marginTop: 6 }}>
        <button className="btn btn-sm" onClick={add}>Add segment</button>
        {(dirty || saved.length > 0) && (
          <button className="btn btn-sm btn-primary" onClick={submit}>Save segments</button>
        )}
        {saved.length > 0 && (
          <button className="btn btn-sm" onClick={() => { setRows(null); save(null); }}>Clear all</button>
        )}
      </div>
    </div>
  );
}
