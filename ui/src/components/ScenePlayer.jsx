// Pieces shared by the film scenes page and the cut scenes page: the stage (a still or a
// playing clip), what is at the playhead (who is on screen, the moment's tags) and the
// helpers that keep measured `fc:` rows out of sight.
import { useEffect, useRef, useState } from 'react';
import { Link } from 'react-router';
import { enc } from './kit.jsx';
import { TagChip, isShownTag, tagName, useTagNames } from './TagChip.jsx';

export { useTagNames };

export const q = (o) => new URLSearchParams(Object.entries(o).filter(([, v]) => v != null && v !== '')).toString();
export const sceneFrameUrl = (key, t, height = 240) => `/api/scenes/frame?${q({ key, t: t.toFixed(2), height })}`;
export const sheetUrl = (key, start, end, cols = 4, rows = 2) =>
  `/api/scenes/sheet?${q({ key, start: start.toFixed(2), end: end.toFixed(2), cols, rows, height: 180 })}`;
export const clipUrl = (key, start, end) => `/api/scenes/clip?${q({ key, start: start.toFixed(2), end: end.toFixed(2) })}`;
// A still of one recipe source, in the user's own copy of it.
export const sourceFrameUrl = (tid, src, t) => `/api/timelines/${enc(tid)}/sources/${enc(src)}/frame?t=${Math.round(t * 10) / 10}`;

// /api/scenes/clip serves 5 minutes at most.
export const CLIP_MAX = 300;

// The clip windows that play [start, end]: one per 5 minutes.
export function clipWindows(start, end, max = CLIP_MAX) {
  const out = [];
  for (let a = start; a < end - 0.05; a += max) out.push([a, Math.min(end, a + max)]);
  return out.length ? out : [[start, end]];
}

// Measured "signals" (`fc:` ids other than the real cut tags) are not tags: never shown.
export const shownTags = (row) => (row.tags || []).filter(isShownTag);
// Rows with at least one real tag.
export const visibleRows = (rows) => (rows || []).filter((r) => shownTags(r).length > 0);

// The real tags of the non-rejected rows covering t, once each, in order of appearance.
export function tagsAt(rows, t) {
  const out = [];
  for (const r of rows || []) {
    if (r.status === 'rejected' || !(r.start <= t && t < r.end)) continue;
    for (const id of shownTags(r)) if (!out.includes(id)) out.push(id);
  }
  return out;
}

// A tag's display name (`names` from useTagNames).
export const tagLabel = (names, id) => tagName(id, names);

// The shared chip; a click opens the tag page without also picking the card it sits in.
export function TagLink({ id, names }) {
  return (
    <span className="scp-chip" onClick={(e) => e.stopPropagation()} role="presentation">
      <TagChip tag={id} names={names} />
    </span>
  );
}

// Faces at t in film `fkey`. Paused: asked 300 ms after the playhead settles. Playing: once
// per 2 s of film time. A newer question cancels the one still in flight.
export function useFacesAt(fkey, t, playing) {
  const [res, setRes] = useState(null);
  const tRef = useRef(t);
  useEffect(() => { tRef.current = t; });
  const step = playing ? Math.floor(t / 2) : Math.round(t * 10);
  useEffect(() => {
    if (!fkey) return undefined;
    const ac = new AbortController();
    const timer = setTimeout(() => {
      const at = Math.max(0, tRef.current);
      fetch(`/api/scenes/faces?${q({ key: fkey, t: at.toFixed(1) })}`, { signal: ac.signal })
        .then((r) => (r.ok ? r.json() : { faces: [] }))
        .then((d) => setRes({ key: fkey, faces: d?.faces || [] }))
        .catch(() => {});
    }, playing ? 0 : 300);
    return () => { clearTimeout(timer); ac.abort(); };
  }, [fkey, step, playing]);
  return res && res.key === fkey ? res.faces : null;
}

// Matched faces only, once per person; sure ones first.
export function namedFaces(faces) {
  const out = [];
  for (const f of [...(faces || [])].sort((a, b) => Number(b.sure) - Number(a.sure))) {
    if (!f.name) continue;
    if (!out.some((o) => o.name === f.name)) out.push(f);
  }
  return out;
}

export function OnScreen({ fkey, faces }) {
  const people = namedFaces(faces);
  if (!people.length) return null;
  return (
    <ul className="scp-people" aria-label="On screen">
      {people.map((f) => (
        <li key={f.name}>
          <Link to={`/movies/${enc(fkey)}/cast?${q({ q: f.name })}`} className={f.sure ? '' : 'scp-maybe'}
            title={f.sure ? undefined : 'Unsure match'}>
            {f.photo ? <img src={f.photo} alt="" loading="lazy" /> : <span className="scp-noface">{f.name.slice(0, 1)}</span>}
            <span className="stack" style={{ gap: 0, minWidth: 0 }}>
              <b>{f.character || f.name}{f.sure ? '' : '?'}</b>
              {f.character && <span className="tiny muted">{f.name}</span>}
            </span>
          </Link>
        </li>
      ))}
    </ul>
  );
}

export function MomentTags({ ids, names }) {
  if (!ids.length) return null;
  return <div className="scp-tags" aria-label="Tags here">{ids.map((id) => <TagLink key={id} id={id} names={names} />)}</div>;
}

// What is at the playhead: who is on screen and the tags covering it. Draws nothing when
// there is nothing to say.
export function AtPlayhead({ fkey, t, playing, rows, names }) {
  const faces = useFacesAt(fkey, t, playing);
  const ids = tagsAt(rows, t);
  return (
    <div className="scp-now">
      <OnScreen fkey={fkey} faces={faces} />
      <MomentTags ids={ids} names={names} />
    </div>
  );
}

// One clip playing from film time `offset`; reports film time as it plays, and whether
// it is running (the native controls can pause it).
export function ClipVideo({ src, offset, onTime, onRunning, onEnded, autoPlay = true }) {
  const report = (e) => onTime?.(offset + e.currentTarget.currentTime);
  return (
    <video key={src} src={src} controls autoPlay={autoPlay}
      onTimeUpdate={report} onSeeked={report}
      onPlay={() => onRunning?.(true)}
      onPause={(e) => { if (!e.currentTarget.ended) onRunning?.(false); }}
      onEnded={onEnded} />
  );
}
