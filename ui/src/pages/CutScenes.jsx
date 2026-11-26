// A cut played straight from its sources: each segment as /api/scenes/clip windows, one
// after another. The cut page embeds it; /cuts/:id/scenes now lands on the cut page.
import { useState } from 'react';
import { Link, Navigate, useLocation, useParams } from 'react-router';
import { fmtTime } from '../lib/time.js';
import { enc } from '../components/kit.jsx';
import { ClipVideo, clipUrl, clipWindows, sourceFrameUrl } from '../components/ScenePlayer.jsx';

// The library film each movie source plays from (by TMDB id): an indexed copy first.
export function filmsBySource(doc, films) {
  const out = {};
  for (const [src, s] of Object.entries(doc?.sources || {})) {
    if (s?.kind !== 'movie' || s.tmdb == null) continue;
    const all = (films || []).filter((f) => String(f.tmdb_id) === String(s.tmdb));
    out[src] = all.find((f) => f.indexed) || all.find((f) => f.on_disk) || all[0] || null;
  }
  return out;
}

// The next segment after n (1-based) that can play, or null.
export function nextPlayable(segs, n, filmOf) {
  for (let i = n; i < segs.length; i += 1) if (filmOf[segs[i].seg.src]) return segs[i].n;
  return null;
}

// The preview player. `n` (1-based segment) and `play` are the caller's, so a list next
// to it can pick and start a segment; the clip window and pause point are kept here.
export function CutPreview({ id, segs, filmOf, srcName, n, onN, play, onPlay, buildTo, crumbState }) {
  const [shown, setShown] = useState(n);
  const [chunk, setChunk] = useState(0); // which 5-minute window of a long segment is playing
  const [clipT, setClipT] = useState(null); // film time while playing
  const [paused, setPaused] = useState(null); // film time of the still, inside the segment
  if (shown !== n) { // another segment: start it from the top
    setShown(n);
    setChunk(0);
    setClipT(null);
    setPaused(null);
  }

  const cur = segs[n - 1];
  if (!cur) return null;
  const seg = cur.seg;
  const film = filmOf[seg.src];
  const windows = clipWindows(paused ?? seg.start, seg.end);
  const win = windows[Math.min(chunk, windows.length - 1)];
  const still = paused ?? seg.start + Math.min(0.5, (seg.end - seg.start) / 2);
  const at = play && clipT != null ? clipT : still;
  const playedTo = Math.min(Math.max((at - seg.start) / (cur.dur || 1), 0), 1);

  const toggle = () => {
    if (play && clipT != null) setPaused(clipT);
    setClipT(null);
    onPlay(!play);
  };
  // End of a clip: the segment's next 5-minute window, else the next segment that can play.
  const ended = () => {
    if (chunk + 1 < windows.length) { setChunk(chunk + 1); setClipT(null); return; }
    const next = nextPlayable(segs, n, filmOf);
    if (next) onN(next); else { onPlay(false); setClipT(null); }
  };

  return (
    <div className="cut2-preview">
      <div className="cut2-stage">
        {play && film
          ? <ClipVideo src={clipUrl(film.key, win[0], win[1])} offset={win[0]} onTime={setClipT} onEnded={ended} />
          : <img src={sourceFrameUrl(id, seg.src, still)} alt={`Segment ${n} at ${fmtTime(still)}`} />}
        <i className="cut2-prog" style={{ width: `${playedTo * 100}%` }} />
      </div>
      <div className="row wrap" style={{ justifyContent: 'space-between' }}>
        <span className="mono tiny">{n}/{segs.length} · {srcName(seg.src)} · {fmtTime(seg.start)}–{fmtTime(seg.end)}</span>
        <div className="row">
          <button type="button" className="btn btn-sm" disabled={n <= 1} onClick={() => onN(n - 1)}>Previous</button>
          <button type="button" className="btn btn-sm btn-primary" disabled={!film} onClick={toggle}
            title={film ? undefined : 'This film is not in your library'}>{play ? 'Stop' : 'Play'}</button>
          <button type="button" className="btn btn-sm" disabled={n >= segs.length} onClick={() => onN(n + 1)}>Next</button>
        </div>
      </div>
      <span className="tiny muted">
        This is a preview. The real file is made when you <Link to={buildTo} state={crumbState}>build the cut</Link>,
        and that&rsquo;s what goes to Plex.
      </span>
    </div>
  );
}

// The old full-size scenes page: the cut page has the preview now (?scene= carries over).
export function CutScenes() {
  const { id } = useParams();
  const { search, state } = useLocation();
  return <Navigate replace to={`/cuts/${enc(id)}${search}`} state={state} />;
}
