// A cut played straight from its sources as one continuous video. The cut page embeds
// it; /cuts/:id/scenes now lands on the cut page.
import { Link, Navigate, useLocation, useParams } from 'react-router';
import { enc } from '../components/kit.jsx';
import { CutPlayer } from '../components/CutPlayer.jsx';

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

// The preview: the whole cut as one video (components/CutPlayer.jsx). `n` is the segment
// to open on; `onN` hears the segment playing; `ref` gets { seekSegment(n, play) }.
export function CutPreview({ id, segs, filmOf, srcOrder, srcName, n, onN, buildTo, crumbState, ref }) {
  return (
    <div className="cut2-preview">
      <CutPlayer ref={ref} id={id} segs={segs} filmOf={filmOf} srcOrder={srcOrder} srcName={srcName}
        start={n} onSegment={onN} />
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
