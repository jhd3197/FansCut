// A cut's scenes: its segments played one after another in the scenes player, a still per
// segment in the strip, and for the current one who is on screen and the film's tags there.
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Link, useParams } from 'react-router';
import { useCrumbRoot, usePageTitle, useUrlState } from '../lib/hooks.js';
import { useApi } from '../lib/store.js';
import { fmtTime } from '../lib/time.js';
import { flatSegments } from '../lib/timeline.js';
import { Empty, ErrorBox, Loading } from '../components/ui.jsx';
import { Crumbs, enc } from '../components/kit.jsx';
import {
  AtPlayhead, ClipVideo, clipUrl, clipWindows, sheetUrl, sourceFrameUrl, useTagNames, visibleRows,
} from '../components/ScenePlayer.jsx';

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

export function CutScenes() {
  const { id } = useParams();
  const tl = useApi(`/timelines/${enc(id)}`);
  const filmsQ = useApi('/scenes/films', { quiet: true });
  const names = useTagNames();
  const crumb = useCrumbRoot();
  usePageTitle(tl.data ? `${tl.data.title} scenes` : null);
  const [nParam, setNParam] = useUrlState('scene', '1');
  const doc = tl.data?.doc;
  const segs = useMemo(() => flatSegments(doc).segs, [doc]);
  const filmOf = useMemo(() => filmsBySource(doc, filmsQ.data?.films), [doc, filmsQ.data]);
  const n = Math.min(Math.max(Number(nParam) || 1, 1), Math.max(segs.length, 1));
  const cur = segs[n - 1] || null;
  const film = cur ? filmOf[cur.seg.src] : null;

  const [play, setPlay] = useState(false);
  const [chunk, setChunk] = useState(0); // which 5-minute window of a long segment is playing
  const [clipT, setClipT] = useState(null); // film time while playing
  const [running, setRunning] = useState(false);
  const [paused, setPaused] = useState(null); // film time of the still, inside the current segment

  const go = useCallback((to) => {
    setNParam(String(to));
    setChunk(0);
    setClipT(null);
    setPaused(null);
  }, [setNParam]);

  const seg = cur?.seg;
  // play from the still (where the last play stopped, or the segment's start), in 5-minute clips
  const windows = seg ? clipWindows(paused ?? seg.start, seg.end) : [];
  const still = seg ? (paused ?? seg.start + Math.min(0.5, (seg.end - seg.start) / 2)) : 0;
  const at = play && clipT != null ? clipT : still;

  const togglePlay = useCallback(() => {
    if (play && clipT != null) setPaused(clipT);
    setPlay(!play);
    setClipT(null);
    setRunning(false);
  }, [play, clipT]);

  // End of a clip: the segment's next 5-minute window, else the next segment that can play.
  const ended = () => {
    setRunning(false);
    if (chunk + 1 < windows.length) {
      setChunk(chunk + 1);
      setClipT(null);
      return;
    }
    const next = nextPlayable(segs, n, filmOf);
    if (next) go(next); else { setPlay(false); setClipT(null); }
  };

  const tagRows = useApi(film ? `/scenes/tags?key=${enc(film.key)}` : null, { quiet: true });
  const rows = useMemo(() => visibleRows(tagRows.data?.tags), [tagRows.data]);

  // J/K next and previous segment, Space play or stop.
  useEffect(() => {
    const onKey = (e) => {
      if (e.target.closest('input, textarea, select, video')) return;
      if (e.key === 'j' && n < segs.length) go(n + 1);
      else if (e.key === 'k' && n > 1) go(n - 1);
      else if (e.key === ' ' && film) togglePlay();
      else return;
      e.preventDefault();
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [n, segs.length, film, go, togglePlay]);

  // keep the current segment in view in the side list
  const listRef = useRef(null);
  useEffect(() => {
    listRef.current?.querySelector('[aria-current="true"]')?.scrollIntoView({ block: 'nearest' });
  }, [n]);

  const title = tl.data?.title || id;
  const crumbs = [crumb.root, [title, `/cuts/${enc(id)}`, crumb.state], ['Scenes']];
  if (tl.error && !tl.data) return <div className="page"><Crumbs items={crumbs} /><ErrorBox error={tl.error} onRetry={tl.reload} /></div>;
  if (!tl.data) return <div className="page"><Crumbs items={crumbs} /><Loading /></div>;
  if (!segs.length) return <div className="page"><Crumbs items={crumbs} /><Empty>This cut has no segments yet.</Empty></div>;

  const srcName = (src) => filmOf[src]?.title || doc.sources[src]?.note || src;
  const win = windows[Math.min(chunk, windows.length - 1)];
  const dur = seg.end - seg.start;
  const playedTo = Math.min(Math.max((at - seg.start) / (dur || 1), 0), 1);

  return (
    <div className="page page-wide" style={{ gap: 20 }}>
      <Crumbs items={crumbs} />
      <div className="sc-grid">
        <div className="stack" style={{ gap: 14, minWidth: 0 }}>
          <div className="sc-stage">
            {play && film
              ? <ClipVideo src={clipUrl(film.key, win[0], win[1])} offset={win[0]} onTime={setClipT}
                onRunning={setRunning} onEnded={ended} />
              : <img src={sourceFrameUrl(id, seg.src, still)} alt={`Scene ${n} at ${fmtTime(still)}`} />}
          </div>
          <div className="row wrap" style={{ justifyContent: 'space-between' }}>
            <span className="mono tiny">
              {n}/{segs.length} · {srcName(seg.src)} · {fmtTime(seg.start)}–{fmtTime(seg.end)}
              <span className="muted"> ({Math.round(dur)}s{windows.length > 1 ? `, ${windows.length} clips` : ''})</span>
            </span>
            <div className="row">
              <button type="button" className="btn btn-sm" disabled={n <= 1} onClick={() => go(n - 1)}>Previous <kbd>K</kbd></button>
              <button type="button" className="btn btn-sm" disabled={!film} onClick={togglePlay}
                title={film ? undefined : 'This film is not in your library'}>
                {play ? 'Show frames' : 'Play from here'} <kbd>Space</kbd>
              </button>
              <button type="button" className="btn btn-sm" disabled={n >= segs.length} onClick={() => go(n + 1)}>Next <kbd>J</kbd></button>
            </div>
          </div>
          <span className="tiny muted">
            This is a preview. The real file is made when you <Link to={`/cuts/${enc(id)}/build`} state={crumb.state}>build the cut</Link>,
            and that&rsquo;s what goes to Plex.
          </span>
          <nav className="reel-strip scp-strip" aria-label="Segments">
            {segs.map((s) => {
              const mid = (s.seg.start + s.seg.end) / 2;
              const on = s.n === n;
              return (
                <button key={s.n} type="button" aria-current={on} onClick={() => go(s.n)}
                  style={{ flexGrow: Math.max(s.dur, 1), backgroundImage: `url("${sourceFrameUrl(id, s.seg.src, mid)}")` }}
                  aria-label={`Scene ${s.n}, ${srcName(s.seg.src)}, ${fmtTime(s.dur)}`}>
                  <span>{s.n}</span>
                  {on && <i className="scp-prog" style={{ width: `${playedTo * 100}%` }} />}
                </button>
              );
            })}
          </nav>
          {seg.desc && <p style={{ margin: 0 }}>{seg.desc}</p>}
          {film && <img className="sc-sheet" src={sheetUrl(film.key, seg.start, seg.end)} alt="Frames across the scene" />}
        </div>

        <aside className="sc-side" ref={listRef}>
          <ol className="scp-segs">
            {segs.map((s) => {
              const on = s.n === n;
              const f = filmOf[s.seg.src];
              return (
                <li key={s.n} aria-current={on}>
                  <button type="button" className="scp-seg" onClick={() => go(s.n)}>
                    <span className="mono tiny muted">{s.n}</span>
                    <span className="scp-seg-name">{srcName(s.seg.src)}</span>
                    <span className="mono tiny muted">{fmtTime(s.seg.start)}–{fmtTime(s.seg.end)}</span>
                  </button>
                  {on && (f
                    ? <AtPlayhead fkey={f.key} t={at} playing={play && running} rows={rows} names={names} />
                    : <span className="tiny muted">Not in your library</span>)}
                </li>
              );
            })}
          </ol>
        </aside>
      </div>
    </div>
  );
}
