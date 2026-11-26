// A cut played as one video: every segment's /api/scenes/clip windows laid end to end on
// one bar. Two stacked <video>s take turns: while one plays, the other holds the next
// clip at its first frame, and near the boundary it starts and is shown before the first
// one pauses, so there is no reload between films.
import { useEffect, useImperativeHandle, useMemo, useRef, useState } from 'react';
import { fmtTime } from '../lib/time.js';
import { SRC_COLOURS } from './kit.jsx';
import { CLIP_MAX, clipUrl, clipWindows, sourceFrameUrl } from './ScenePlayer.jsx';

const EARLY = 0.1; // hand off this long before a clip's end
const MIN_PIECE = 0.05;

// -- timing (pure) ----------------------------------------------------------------------

// Every clip window of the cut in play order: {n (segment), src, key (null: not in the
// library), a, b (film time), at (cut time), dur}.
export function cutPieces(segs, filmOf, max = CLIP_MAX) {
  const out = [];
  for (const s of segs || []) {
    const film = filmOf?.[s.seg.src];
    if (!film) { out.push({ n: s.n, src: s.seg.src, key: null, a: s.seg.start, b: s.seg.end, at: s.at, dur: s.dur }); continue; }
    for (const [a, b] of clipWindows(s.seg.start, s.seg.end, max)) {
      out.push({ n: s.n, src: s.seg.src, key: film.key, a, b, at: s.at + (a - s.seg.start), dur: b - a });
    }
  }
  return out;
}

export const canPlay = (p, bad) => Boolean(p?.key) && p.dur > MIN_PIECE && !bad?.has(p);

// The first piece at or after i that can play, or -1.
export function playableFrom(pieces, i, bad) {
  for (let j = Math.max(0, i); j < pieces.length; j += 1) if (canPlay(pieces[j], bad)) return j;
  return -1;
}

// The piece covering cut time t (the last one when t is past the end).
export function pieceAt(pieces, t) {
  let i = 0;
  while (i + 1 < pieces.length && pieces[i + 1].at <= t + 1e-6) i += 1;
  return i;
}

// Cut time -> {i, local}: the piece and the time inside its clip. A piece that can't play
// gives the start of the next one that can; null when nothing after t can play.
export function locate(pieces, t, bad) {
  if (!pieces.length) return null;
  const i = pieceAt(pieces, Math.max(0, t));
  if (canPlay(pieces[i], bad)) return { i, local: Math.min(Math.max(t - pieces[i].at, 0), pieces[i].dur) };
  const j = playableFrom(pieces, i + 1, bad);
  return j < 0 ? null : { i: j, local: 0 };
}

// Where segment n starts in cut time (0 when it isn't there).
export function segmentStart(pieces, n) {
  return pieces.find((p) => p.n === n)?.at ?? 0;
}

// Segments as bar blocks (left/width in % of the cut).
export function barBlocks(segs, filmOf, total) {
  return (segs || []).map((s) => ({
    n: s.n, src: s.seg.src, on: Boolean(filmOf?.[s.seg.src]),
    left: total ? (s.at / total) * 100 : 0, width: total ? (s.dur / total) * 100 : 0,
  }));
}

// -- loading ----------------------------------------------------------------------------

// Clips come down whole and play from blob: URLs. /api/scenes/clip has no byte ranges,
// so a clip played from its URL can't be seeked; a blob can, and a clip asked for early
// (the server cuts it the first time) is ready when the boundary comes. One fetch per URL;
// the oldest blobs not on screen are let go past KEEP.
const KEEP = 6;
const blobs = new Map(); // clip URL -> {p: Promise<blob: URL>, obj}
export function clipBlob(url) {
  let e = blobs.get(url);
  if (e) { blobs.delete(url); blobs.set(url, e); return e.p; } // most recent last
  e = { obj: null };
  e.p = fetch(url).then((r) => {
    if (!r.ok) throw new Error(`clip ${r.status}`);
    return r.blob();
  }).then((b) => { e.obj = URL.createObjectURL(b); return e.obj; });
  e.p.catch(() => blobs.delete(url));
  blobs.set(url, e);
  return e.p;
}
export const warm = (url) => clipBlob(url).then(() => {}, () => {});
function trimBlobs(keep) {
  for (const [url, e] of blobs) {
    if (blobs.size <= KEEP) break;
    if (keep.has(url) || !e.obj) continue;
    URL.revokeObjectURL(e.obj);
    blobs.delete(url);
  }
}

const once = (v, ev) => new Promise((resolve, reject) => {
  const done = (e) => { v.removeEventListener(ev, done); v.removeEventListener('error', done); (e.type === 'error' ? reject : resolve)(e); };
  v.addEventListener(ev, done);
  v.addEventListener('error', done);
});

// The next frame v paints, but no longer than `ms` (a hidden video may paint nothing, and
// it already holds a decoded first frame).
const nextFrame = (v, ms = 40) => new Promise((resolve) => {
  const timer = setTimeout(resolve, ms);
  v.requestVideoFrameCallback?.(() => { clearTimeout(timer); resolve(); });
});

class Stale extends Error {}

// -- the player -------------------------------------------------------------------------

// `start` is the segment to open on; `onSegment(n)` reports the segment playing; the ref
// gets { seekSegment(n, play) } for a list beside it.
export function CutPlayer({ id, segs, filmOf, srcOrder, srcName, start = 1, onSegment, ref }) {
  // Kept while what plays is the same: a refetched doc or film list must not restart it.
  const fresh = useMemo(() => cutPieces(segs, filmOf), [segs, filmOf]);
  const sig = fresh.map((x) => `${x.n}|${x.key}|${x.a}|${x.b}`).join(';');
  const [held, setHeld] = useState({ sig, pieces: fresh });
  const pieces = held.sig === sig ? held.pieces : fresh;
  const total = (segs || []).reduce((t, s) => t + s.dur, 0);
  const colourOf = useMemo(() => {
    const out = {};
    (srcOrder || []).forEach((s, i) => { out[s] = SRC_COLOURS[i % SRC_COLOURS.length]; });
    return out;
  }, [srcOrder]);
  const blocks = barBlocks(segs, filmOf, total);

  const [t, setT] = useState(() => segmentStart(pieces, start));
  const [cur, setCur] = useState(null); // piece index on screen
  const [playing, setPlaying] = useState(false);
  const [waiting, setWaiting] = useState(false);
  const [shown, setShown] = useState(false); // a video has a frame up
  const [drag, setDrag] = useState(null); // cut time under the pointer while scrubbing
  if (held.sig !== sig) { // another cut (or reordered): back to the chosen segment
    setHeld({ sig, pieces: fresh });
    setT(segmentStart(pieces, start));
    setCur(null);
    setPlaying(false);
    setWaiting(false);
    setShown(false);
  }

  const v0 = useRef(null);
  const v1 = useRef(null);
  const barRef = useRef(null);
  const dragging = useRef(false);
  const E = useRef(null); // the engine: everything the async hand-offs touch
  const api = useRef(null);
  const startRef = useRef(start);
  const onSegRef = useRef(onSegment);
  useEffect(() => { startRef.current = start; onSegRef.current = onSegment; });

  useEffect(() => {
    const vids = [v0.current, v1.current];
    const S = {
      pieces, active: 0, slot: [{ i: null, tok: 0, ready: null }, { i: null, tok: 0, ready: null }],
      gen: 0, playing: false, handing: false, bad: new Set(), raf: 0, edge: 0, alive: true, lastN: null,
    };
    E.current = S;
    const urlOf = (i) => clipUrl(pieces[i].key, pieces[i].a, pieces[i].b);
    const report = (i) => {
      setCur(i);
      const n = pieces[i]?.n;
      if (n != null && n !== S.lastN) { S.lastN = n; onSegRef.current?.(n); }
    };
    const setOn = (k) => {
      vids[k].dataset.on = '1';
      vids[1 - k].dataset.on = '0';
      S.active = k;
    };

    // Put piece i at `local` in slot k and wait until that frame can play.
    const load = (k, i, local) => {
      const slot = S.slot[k];
      const tok = ++slot.tok;
      slot.i = i;
      const v = vids[k];
      const fresh = () => { if (!S.alive || tok !== slot.tok) throw new Stale(); };
      slot.ready = (async () => {
        const url = urlOf(i);
        const obj = await clipBlob(url);
        fresh();
        if (v.dataset.url !== url) {
          v.dataset.url = url;
          v.src = obj;
          v.load();
          trimBlobs(new Set(vids.map((x) => x.dataset.url)));
          await once(v, 'loadedmetadata');
          fresh();
        }
        if (Math.abs(v.currentTime - local) > 0.04) {
          v.currentTime = local;
          await once(v, 'seeked');
          fresh();
        }
        if (v.readyState < 3) { await once(v, 'canplay'); fresh(); }
        return i;
      })();
      slot.ready.catch(() => {});
      return slot.ready;
    };
    // The spare slot holds the next piece after the one on screen, at its first frame;
    // the piece after that is cut on the server ahead of time.
    const prime = () => {
      const k = 1 - S.active;
      const here = S.slot[S.active].i;
      if (here == null) return;
      const nx = playableFrom(pieces, here + 1, S.bad);
      if (nx < 0) return;
      if (S.slot[k].i !== nx) {
        load(k, nx, 0).catch((e) => { if (!(e instanceof Stale)) S.bad.add(pieces[nx]); });
      }
      const after = playableFrom(pieces, nx + 1, S.bad);
      if (after >= 0) warm(urlOf(after));
    };

    const tick = () => {
      S.raf = 0;
      if (!S.alive || !S.playing) return;
      check();
      S.raf = requestAnimationFrame(tick);
    };
    const loop = () => { if (!S.raf) S.raf = requestAnimationFrame(tick); };

    // Where the clip on screen really ends (its file can be a hair shorter than the range).
    const endOf = (k) => {
      const v = vids[k];
      const p = pieces[S.slot[k].i];
      return Number.isFinite(v.duration) && v.duration > 0 ? Math.min(p.dur, v.duration) : p.dur;
    };
    const atEnd = (k) => vids[k].ended || vids[k].currentTime >= endOf(k) - EARLY;
    // Time on screen, and the hand-off when the clip is about to end.
    const check = () => {
      const i = S.slot[S.active].i;
      if (i == null) return;
      const p = pieces[i];
      const v = vids[S.active];
      if (!S.handing) setT(p.at + Math.min(v.currentTime, p.dur));
      if (!S.playing || S.handing) return;
      if (atEnd(S.active)) { advance(); return; }
      // close to the end: a timer for the exact moment (frames and timeupdate can be sparse)
      const left = (endOf(S.active) - EARLY - v.currentTime) / (v.playbackRate || 1);
      if (left < 0.6) { clearTimeout(S.edge); S.edge = setTimeout(check, Math.max(0, left * 1000)); }
    };

    const advance = async () => {
      const gen = S.gen;
      const from = S.slot[S.active].i;
      const nx = playableFrom(pieces, from + 1, S.bad);
      if (nx < 0) { // the end of the cut
        S.playing = false;
        setPlaying(false);
        setT(pieces[from].at + pieces[from].dur);
        return;
      }
      S.handing = true;
      const k = 1 - S.active;
      const nv = vids[k];
      const ov = vids[S.active];
      try {
        const ready = S.slot[k].i === nx ? S.slot[k].ready : load(k, nx, 0);
        let late = false;
        const slow = setTimeout(() => { late = true; if (gen === S.gen) setWaiting(true); }, 60);
        try { await ready; } finally { clearTimeout(slow); }
        if (gen !== S.gen) return;
        if (!S.playing) { S.handing = false; return; } // paused while it loaded: hand off on play
        if (Math.abs(nv.currentTime) > 0.04) { nv.currentTime = 0; await once(nv, 'seeked'); if (gen !== S.gen) return; }
        await nv.play(); // the next clip runs under the current one...
        if (gen !== S.gen) { nv.pause(); return; }
        await nextFrame(nv);
        if (gen !== S.gen) { nv.pause(); return; }
        setOn(k); // ...is shown...
        ov.pause(); // ...and only then the old one stops
        if (late) setWaiting(false);
        report(nx);
        setT(pieces[nx].at + nv.currentTime);
        S.handing = false;
        prime();
      } catch (e) {
        if (gen !== S.gen) return;
        S.handing = false;
        if (!(e instanceof Stale)) { S.bad.add(pieces[nx]); setWaiting(false); advance(); }
      }
    };

    // Jump to cut time `to`; keep playing if it was.
    const seek = async (to, play = S.playing) => {
      const loc = locate(pieces, to, S.bad);
      if (!loc) return;
      const gen = ++S.gen;
      S.handing = false;
      S.playing = play;
      setPlaying(play);
      setT(pieces[loc.i].at + loc.local);
      const a = S.active;
      const av = vids[a];
      if (S.slot[a].i === loc.i && av.readyState >= 1) {
        av.currentTime = loc.local;
        report(loc.i);
        if (play) { av.play().catch(() => {}); loop(); } else av.pause();
        prime();
        return;
      }
      const k = S.slot[a].i == null ? a : 1 - a;
      vids[1 - k].pause();
      const slow = setTimeout(() => { if (gen === S.gen) setWaiting(true); }, 60);
      try {
        await load(k, loc.i, loc.local);
      } catch (e) {
        clearTimeout(slow);
        if (gen !== S.gen || e instanceof Stale) return;
        S.bad.add(pieces[loc.i]);
        setWaiting(false);
        seek(pieces[loc.i].at + pieces[loc.i].dur, play);
        return;
      }
      clearTimeout(slow);
      if (gen !== S.gen) return;
      setOn(k);
      setShown(true);
      setWaiting(false);
      report(loc.i);
      if (S.playing) { vids[k].play().catch(() => {}); loop(); }
      prime();
    };

    const play = () => {
      const i = S.slot[S.active].i;
      const v = vids[S.active];
      const p = i == null ? null : pieces[i];
      if (!p || v.readyState < 1) { seek(t0(), true); return; }
      const last = playableFrom(pieces, i + 1, S.bad) < 0;
      if (last && atEnd(S.active)) { seek(0, true); return; } // from the top again
      S.playing = true;
      setPlaying(true);
      if (atEnd(S.active)) advance(); // stopped on a boundary: go straight to the next clip
      else v.play().catch(() => {});
      loop();
      prime();
    };
    const pause = () => {
      S.playing = false;
      setPlaying(false);
      S.handing = false;
      S.gen += 1;
      clearTimeout(S.edge);
      vids.forEach((v) => v.pause());
      setWaiting(false);
      check();
    };
    let shownT = segmentStart(pieces, startRef.current);
    const t0 = () => shownT;

    // A stall inside a clip: the spinner until it plays on.
    const onWaiting = (e) => { if (e.currentTarget === vids[S.active] && S.playing) setWaiting(true); };
    const onPlaying = (e) => { if (e.currentTarget === vids[S.active] && !S.handing) setWaiting(false); };
    const onTime = (e) => { if (e.currentTarget === vids[S.active]) check(); };
    for (const v of vids) {
      v.addEventListener('waiting', onWaiting);
      v.addEventListener('playing', onPlaying);
      v.addEventListener('timeupdate', onTime);
      v.addEventListener('ended', onTime);
      v.dataset.on = '0';
    }

    api.current = {
      seek: (to, p) => { shownT = to; return seek(to, p); },
      play, pause,
      toggle: () => (S.playing ? pause() : play()),
      playing: () => S.playing,
      time: () => {
        const i = S.slot[S.active].i;
        return i == null ? shownT : pieces[i].at + vids[S.active].currentTime;
      },
    };
    // Open on the chosen segment, paused, with its first frame up.
    if (playableFrom(pieces, 0) >= 0) seek(shownT, false);

    return () => {
      S.alive = false;
      S.gen += 1;
      cancelAnimationFrame(S.raf);
      clearTimeout(S.edge);
      for (const v of vids) {
        v.removeEventListener('waiting', onWaiting);
        v.removeEventListener('playing', onPlaying);
        v.removeEventListener('timeupdate', onTime);
        v.removeEventListener('ended', onTime);
        v.pause();
      }
    };
  }, [pieces]);

  useImperativeHandle(ref, () => ({
    seekSegment: (n, play = true) => api.current?.seek(segmentStart(pieces, n), play),
  }), [pieces]);

  const any = playableFrom(pieces, 0) >= 0;
  const at = drag ?? t;
  const p = pieces[cur ?? pieceAt(pieces, at)];
  const timeAtX = (x) => {
    const r = barRef.current.getBoundingClientRect();
    return Math.min(Math.max((x - r.left) / r.width, 0), 1) * total;
  };
  const onKey = (e) => {
    if (e.target.closest?.('button, a, input')) return;
    if (e.key === ' ' || e.key === 'k') { e.preventDefault(); api.current?.toggle(); }
    if (e.key === 'ArrowLeft' || e.key === 'ArrowRight') {
      e.preventDefault();
      const now = api.current?.time() ?? t;
      api.current?.seek(Math.min(Math.max(now + (e.key === 'ArrowLeft' ? -5 : 5), 0), total));
    }
  };
  const still = p ? sourceFrameUrl(id, p.src, p.a + Math.min(0.5, p.dur / 2)) : null;
  const bar = (cls) => blocks.map((b) => (
    <i key={b.n} className={`${cls}${b.on ? '' : ' cp-off'}`}
      style={{ left: `${b.left}%`, width: `${b.width}%`, background: b.on ? colourOf[b.src] : undefined }} />
  ));
  const pct = total ? (at / total) * 100 : 0;

  return (
    <div className="cp" tabIndex={0} onKeyDown={onKey} aria-label="Cut preview">
      <div className="cut2-stage cp-stage" onClick={() => any && api.current?.toggle()}>
        {!shown && still && <img src={still} alt="" />}
        <video ref={v0} className="cp-v" preload="auto" playsInline disablePictureInPicture />
        <video ref={v1} className="cp-v" preload="auto" playsInline disablePictureInPicture />
        {waiting && <span className="cp-spin" role="status" aria-label="Loading" />}
        {!playing && !waiting && any && <span className="cp-big" aria-hidden="true">▶</span>}
      </div>
      <div ref={barRef} className={`cp-bar${drag != null ? ' cp-drag' : ''}`} role="slider" tabIndex={-1}
        aria-label="Cut time" aria-valuemin={0} aria-valuemax={Math.round(total)} aria-valuenow={Math.round(at)}
        aria-valuetext={`${fmtTime(at)} of ${fmtTime(total)}`}
        onPointerDown={(e) => {
          if (e.button !== 0 || !any) return;
          try { e.currentTarget.setPointerCapture(e.pointerId); } catch { /* not a live pointer */ }
          dragging.current = true;
          setDrag(timeAtX(e.clientX));
        }}
        onPointerMove={(e) => { if (dragging.current) setDrag(timeAtX(e.clientX)); }}
        onPointerUp={(e) => {
          if (!dragging.current) return;
          dragging.current = false;
          setDrag(null);
          api.current?.seek(timeAtX(e.clientX));
        }}
        onPointerCancel={() => { dragging.current = false; setDrag(null); }}>
        <div className="cp-track">{bar('cp-blk')}</div>
        <div className="cp-track cp-done" style={{ clipPath: `inset(0 ${100 - pct}% 0 0)` }}>{bar('cp-blk')}</div>
        {blocks.map((b) => (
          <span key={b.n} className="cp-hit" style={{ left: `${b.left}%`, width: `${b.width}%` }}
            title={`${b.n} · ${srcName(b.src)}${b.on ? '' : ' · not in your library'}`} />
        ))}
        <b className="cp-head" style={{ left: `${pct}%` }} />
      </div>
      <div className="row" style={{ gap: 10, minWidth: 0 }}>
        <button type="button" className="btn btn-sm btn-primary cp-play" disabled={!any}
          onClick={() => api.current?.toggle()}>{playing ? 'Pause' : 'Play'}</button>
        <span className="mono tiny">{fmtTime(at)} / {fmtTime(total)}</span>
        {p && <span className="tiny muted cp-now">{p.n} · {srcName(p.src)}</span>}
      </div>
    </div>
  );
}
