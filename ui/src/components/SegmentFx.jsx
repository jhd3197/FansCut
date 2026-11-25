// Segment effects in the editor's properties panel and the Colour tab. Every field
// maps 1:1 to fanscut/timeline/effects.py; neutral values are dropped (setFx).
import { useState } from 'react';
import { GRADE, TRANSITIONS, gradeFilter } from '../lib/timeline.js';
import { enc } from './kit.jsx';

function Num({ label, value, onChange, min, max, step, unit, placeholder }) {
  return (
    <span style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
      <input className="sel mono" type="number" aria-label={label} min={min} max={max} step={step}
        placeholder={placeholder} value={value ?? ''} style={{ width: 84 }}
        onChange={(e) => onChange(e.target.value === '' ? null : Number(e.target.value))} />
      {unit && <span style={{ color: 'var(--nm)', fontSize: 12 }}>{unit}</span>}
    </span>
  );
}

// Speed, Transition in (type + J/L cut), Fades and title: the design's property groups.
export function SegmentFx({ seg, set, first }) {
  const t = seg.transition?.type || 'cut';
  const audioLead = seg.j_cut ? 'j' : seg.l_cut ? 'l' : '';
  return (
    <>
      <div className="grp">
        <b>Speed</b>
        <dl className="kvp">
          <dt>Speed</dt>
          <dd>
            <select className="sel" aria-label="Speed" value={String(seg.speed ?? 1)} onChange={(e) => set('speed', Number(e.target.value))}>
              {[0.5, 0.75, 0.9, 1, 1.1, 1.25, 1.5, 2].map((v) => <option key={v} value={v}>{v === 1 ? 'Normal (1×)' : `${v}×`}</option>)}
            </select>
          </dd>
          <dt>Hold last frame</dt>
          <dd><Num label="Hold seconds" value={seg.hold} min={0} max={10} step={0.5} unit="s" placeholder="0" onChange={(v) => set('hold', v)} /></dd>
        </dl>
        {seg.speed && seg.speed !== 1 && <span style={{ color: 'var(--nm)', fontSize: 12 }}>Audio keeps its pitch. This segment is re-encoded.</span>}
      </div>

      <div className="grp">
        <b>Transition in</b>
        <dl className="kvp">
          <dt>Type</dt>
          <dd>
            <select className="sel" aria-label="Transition type" value={t} disabled={first} onChange={(e) => set('transition.type', e.target.value)}>
              {TRANSITIONS.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
            </select>
          </dd>
          {t !== 'cut' && (
            <>
              <dt>Duration</dt>
              <dd><Num label="Transition seconds" value={seg.transition?.duration} min={0.04} max={5} step={0.1} unit="s" onChange={(v) => set('transition.duration', v ?? 0.5)} /></dd>
            </>
          )}
          <dt>J / L cut</dt>
          <dd>
            <select className="sel" aria-label="J or L cut" value={audioLead} disabled={first}
              onChange={(e) => {
                const v = e.target.value;
                const amount = seg.j_cut || seg.l_cut || 0.5;
                set('j_cut', v === 'j' ? amount : null);
                set('l_cut', v === 'l' ? amount : null);
              }}>
              <option value="">None</option>
              <option value="j">J-cut: this audio starts early</option>
              <option value="l">L-cut: the last audio runs over</option>
            </select>
          </dd>
          {audioLead && (
            <>
              <dt>By</dt>
              <dd><Num label="J or L cut seconds" value={seg.j_cut || seg.l_cut} min={0.1} max={5} step={0.1} unit="s"
                onChange={(v) => set(audioLead === 'j' ? 'j_cut' : 'l_cut', v)} /></dd>
            </>
          )}
        </dl>
        {first && <span style={{ color: 'var(--nm)', fontSize: 12 }}>The first segment has nothing to transition from.</span>}
      </div>

      <div className="grp">
        <b>Fades and title</b>
        <dl className="kvp">
          <dt>Fade from black</dt>
          <dd><Num label="Fade in seconds" value={seg.video?.fade_in} min={0} max={30} step={0.5} unit="s" placeholder="0" onChange={(v) => set('video.fade_in', v)} /></dd>
          <dt>Fade to black</dt>
          <dd><Num label="Fade out seconds" value={seg.video?.fade_out} min={0} max={30} step={0.5} unit="s" placeholder="0" onChange={(v) => set('video.fade_out', v)} /></dd>
          <dt>Title card</dt>
          <dd><input className="sel" aria-label="Title text" maxLength={80} placeholder="e.g. Sixty years later" value={seg.title?.text || ''}
            onChange={(e) => set('title.text', e.target.value)} /></dd>
          {seg.title && (
            <>
              <dt>Title for</dt>
              <dd><Num label="Title seconds" value={seg.title.duration} min={0.5} max={10} step={0.5} unit="s" onChange={(v) => set('title.duration', v ?? 3)} /></dd>
            </>
          )}
        </dl>
      </div>
    </>
  );
}

// Colour tab: a still of the segment's first frame with the grade previewed on it, and
// the four controls the renderer applies.
export function GradePanel({ tid, seg, set, onCopy, sameSource, saved }) {
  const g = seg.video?.grade || {};
  const [failed, setFailed] = useState(false);
  const [loaded, setLoaded] = useState(null); // the frame URL that has finished loading
  const at = seg.start + Math.min(2, Math.max(0, (seg.end - seg.start) / 2));
  const frame = `/api/timelines/${enc(tid)}/sources/${enc(seg.src)}/frame?t=${at.toFixed(2)}`;
  return (
    <div style={{ display: 'flex', flexWrap: 'wrap', gap: 16, padding: 14 }}>
      <figure style={{ margin: 0, flex: '1 1 420px', display: 'flex', flexDirection: 'column', gap: 6 }}>
        <div className="screen" style={{ maxWidth: 'none' }}>
          {saved && !failed && loaded !== frame && <span style={{ position: 'absolute', color: '#777' }}>Grabbing a frame… (a 4K file can take a few seconds)</span>}
          {saved && !failed
            ? <img alt={`${seg.src} at ${Math.round(at)} s, graded`} src={frame} onLoad={() => setLoaded(frame)}
              onError={() => setFailed(true)} style={{ width: '100%', height: '100%', objectFit: 'contain', filter: gradeFilter(g) }} />
            : <span style={{ color: '#666', padding: 16 }}>{saved ? 'No frame: this source is not in your library' : 'Save first to see a frame of this segment'}</span>}
        </div>
        <figcaption style={{ color: 'var(--nm)', fontSize: 12 }}>
          Preview only, close to the result. The build applies the grade with ffmpeg, or EffectCraft when it is set up.
        </figcaption>
      </figure>
      <div className="grp" style={{ flex: '1 1 260px', borderBottom: 0 }}>
        <b>Grade</b>
        <dl className="kvp">
          {GRADE.map(([k, label, min, max, step, neutral]) => (
            <div key={k} style={{ display: 'contents' }}>
              <dt><label htmlFor={`grade-${k}`}>{label}</label></dt>
              <dd style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                <input id={`grade-${k}`} type="range" min={min} max={max} step={step} value={g[k] ?? neutral}
                  onChange={(e) => set(`video.grade.${k}`, Number(e.target.value))} style={{ flex: 1 }} />
                <span className="mono" style={{ width: 44, textAlign: 'right' }}>{(g[k] ?? neutral).toFixed(k === 'temperature' ? 0 : 2)}</span>
              </dd>
            </div>
          ))}
        </dl>
        <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6 }}>
          <button type="button" className="bt" onClick={() => GRADE.forEach(([k, , , , , n]) => set(`video.grade.${k}`, n))} disabled={!seg.video?.grade}>Reset</button>
          <button type="button" className="bt" onClick={onCopy} disabled={!seg.video?.grade || sameSource < 2}
            title="Use this grade on every segment from the same source">Copy to all {sameSource} from {seg.src}</button>
        </div>
      </div>
    </div>
  );
}
