// Step 1 for a trailer (/cuts/:id/find when the cut's category is Trailer): one film's tagged
// moments as short shots, grouped by where they sit in the film, against a target length.
// Ticking adds or removes the shot in the recipe; Arrange orders them.
import { useMemo, useState } from 'react';
import { Link, useSearchParams } from 'react-router';
import { api, artUrl } from '../lib/api.js';
import { useLoad } from '../lib/hooks.js';
import { fmtTime } from '../lib/time.js';
import {
  ACTS, TRAILER_LENGTHS, actOf, addPicks, cutLength, dedupeRows, inCut, isSpoiler, overlapShare, removePicks,
  segList, shotFor, trimDoc,
} from '../lib/picks.js';
import { enc } from '../components/kit.jsx';
import { Empty, ErrorBox, Loading } from '../components/ui.jsx';
import { FilterChip, Label, Switch, Tick } from '../components/v2.jsx';
import { ClipVideo, q, sceneFrameUrl, shownTags, sourceFrameUrl } from '../components/ScenePlayer.jsx';
import { tagName, useTagNames } from '../components/TagChip.jsx';
import { EditorBar } from './Find.jsx';
import './Find.css';
import './Trailer.css';

const len = (x) => Math.max(0, x.end - x.start);
const plural = (n, one) => `${n} ${one}${n === 1 ? '' : 's'}`;
const shotNote = (n, secs) => `${plural(n, 'shot')} · ${fmtTime(secs)}`;

// A new trailer for one film: a blank recipe with the film as its only source. Opens on
// /cuts/:id/find. `film` is a row of /scenes/films (key, title, tmdb_id, duration, franchises).
export async function createTrailer(film, title = '') {
  const src = `M${film.tmdb_id}`;
  const doc = {
    schema: 2, kind: 'timeline', category: 'Trailer',
    title: (title || `${film.title} trailer`).slice(0, 200),
    series: { title: film.franchises?.[0] || film.title },
    sources: { [src]: { kind: 'movie', tmdb: film.tmdb_id, note: film.title, ...(film.duration ? { duration: Math.round(film.duration * 1000) / 1000 } : {}) } },
    parts: [],
  };
  return api(`/series/${enc(`movie:${film.tmdb_id}`)}/timelines`, { method: 'POST', body: doc });
}

// The trailer's film: its first movie source, the indexed library copy first.
function trailerFilm(doc, films) {
  const s = Object.values(doc?.sources || {}).find((x) => x?.kind === 'movie');
  if (!s) return null;
  const all = (films || []).filter((f) => String(f.tmdb_id) === String(s.tmdb));
  return all.find((f) => f.indexed) || all.find((f) => f.on_disk) || all[0] || null;
}

export function TrailerPicks({ id, cut }) {
  const { doc, change } = cut;
  const names = useTagNames();
  const [params, setParams] = useSearchParams();
  const target = Number(params.get('len')) || 120;
  const spoilersIn = params.get('spoilers') === '1';
  const sel = params.get('shot') || '';
  const setQ = (k, v) => setParams((p) => { const n = new URLSearchParams(p); if (v == null) n.delete(k); else n.set(k, v); return n; }, { replace: true });
  const [playing, setPlaying] = useState(false);

  const filmsQ = useLoad(() => api('/scenes/films'), []);
  const film = useMemo(() => trailerFilm(doc, filmsQ.data?.films), [doc, filmsQ.data]);
  const fkey = film?.key || '';
  const dataQ = useLoad(() => (fkey ? Promise.all([
    api(`/scenes/tags?${q({ key: fkey })}`),
    api(`/scenes/shots?${q({ key: fkey })}`).catch(() => ({ shots: [] })),
  ]).then(([t, s]) => ({ rows: t.tags, shots: s.shots })) : Promise.resolve(null)), [fkey]);

  const src = film ? `M${film.tmdb_id}` : '';
  const shots = useMemo(() => {
    if (!film || !dataQ.data) return [];
    const { rows, shots: cuts } = dataQ.data;
    const dur = film.duration || 1;
    return dedupeRows(rows.filter((r) => shownTags(r).some((t) => !t.endsWith('spoiler'))))
      .map((r) => {
        const shot = shotFor(r, cuts);
        return { id: r.id, row: r, ...shot, act: actOf(shot.start, dur), spoiler: isSpoiler(r, rows) };
      });
  }, [film, dataQ.data]);

  const head = (body) => (
    <div className="ed-page">
      <EditorBar id={id} kind="Trailer" title={doc.title} step="find" what="In this trailer" note={shotNote(segList(doc).length, cutLength(doc))} />
      <div className="fnd-body">{body}</div>
    </div>
  );
  if (filmsQ.error && !filmsQ.data) return head(<ErrorBox error={filmsQ.error} onRetry={filmsQ.reload} />);
  if (!filmsQ.data) return head(<Loading />);
  if (!film) return head(<Empty>This trailer has no film in your library.</Empty>);

  const visible = shots.filter((s) => spoilersIn || !s.spoiler);
  const segs = segList(doc);
  const total = cutLength(doc);
  const over = total > target;
  const diff = Math.abs(target - total);
  const span = Math.max(target, total) * 1.08 || 1;
  const numberOf = (s) => segs.findIndex(({ seg }) => seg.src === src && overlapShare(seg, s) >= 0.5) + 1;
  const ticked = (s) => inCut(doc, src, s);
  const source = { kind: 'movie', tmdb: film.tmdb_id, note: film.title, ...(film.duration ? { duration: Math.round(film.duration * 1000) / 1000 } : {}) };
  const pickOf = (s) => ({ src, start: s.start, end: s.end, desc: s.row.summary || shotName(s, names), source });
  const tick = (s, on) => change((d) => (on ? addPicks(d, [pickOf(s)]) : removePicks(d, [{ src, start: s.start, end: s.end }], { prune: false })));
  const tickAll = () => change((d) => addPicks(d, visible.map(pickOf)));
  const fit = () => change((d) => trimDoc(d, target));
  const pv = visible.find((s) => s.id === sel) || visible[0] || null;
  const pick = (s) => { setPlaying(false); setQ('shot', s.id); };

  return head(
    <>
      <aside className="fnd-side trl-side" aria-label="Trailer recipe">
        <div>
          <Label className="lab fnd-lab">Film</Label>
          <Link to={`/movies/${enc(film.key)}`} className="trl-film">
            <img src={artUrl(film.key, 'poster', 100)} alt="" onError={(e) => { e.currentTarget.style.visibility = 'hidden'; }} />
            <span>
              <span className="trl-film-name">{film.title}</span>
              <span className="mono fnd-dim fnd-small">{[film.year, film.scenes != null ? plural(film.scenes, 'scene') : null].filter(Boolean).join(' · ')}</span>
            </span>
          </Link>
        </div>
        <div>
          <Label className="lab fnd-lab" id="trl-len">Length</Label>
          <div className="tags-row fnd-chips" role="radiogroup" aria-labelledby="trl-len">
            {TRAILER_LENGTHS.map((n) => (
              <button key={n} type="button" role="radio" aria-checked={target === n} className="fchip"
                onClick={() => setQ('len', n === 120 ? null : String(n))}>{fmtTime(n)}</button>
            ))}
          </div>
        </div>
        <div>
          <Label className="lab fnd-lab">Feel</Label>
          <div className="tags-row fnd-chips">
            {['Teaser', 'Story', 'Action'].map((n) => <FilterChip key={n} disabled title="Not yet">{n}</FilterChip>)}
          </div>
        </div>
        <div className="trl-switches">
          <label className="trl-switch">
            <Switch on={!spoilersIn} onChange={(on) => setQ('spoilers', on ? null : '1')} label="Leave out anything marked Spoiler" />
            <span>Leave out anything marked Spoiler</span>
          </label>
          <label className="trl-switch is-off" title="Not yet">
            <Switch on={false} disabled title="Not yet" label="Open on the theme music" />
            <span>Open on the theme music</span>
          </label>
        </div>
        <button type="button" className="btn" disabled title="Not yet">Pick again</button>
      </aside>

      <section className="fnd-main" aria-label="Shots">
        <div className="box trl-meter">
          <div className="trl-meter-head">
            <h1>{fmtTime(total)} of {fmtTime(target)}</h1>
            <span className={`mono trl-meter-note${over ? ' is-over' : ''}`}>
              {over ? `${fmtTime(diff)} over` : diff < 0.5 ? 'Exactly on length' : `${fmtTime(diff)} left`}
            </span>
            <span className="ed-gap" />
            {over && <button type="button" className="btn btn-sm" onClick={fit} title="Shortens every shot by the same share">Trim to fit</button>}
            <button type="button" className="btn btn-sm" disabled={!visible.some((s) => !ticked(s))} onClick={tickAll}>Tick all</button>
          </div>
          <div className="trl-bar" role="img" aria-label={`${fmtTime(total)} picked of a ${fmtTime(target)} trailer`}>
            <span className={`trl-fill${over ? ' is-over' : ''}`} style={{ width: `${(total / span) * 100}%` }} />
            <span className="trl-mark" style={{ left: `${(target / span) * 100}%` }} />
          </div>
        </div>

        {dataQ.error && <ErrorBox error={dataQ.error} onRetry={dataQ.reload} />}
        {!dataQ.data && !dataQ.error ? <Loading /> : visible.length === 0 ? (
          <Empty>
            No tagged moments in this film yet.{' '}
            <Link to={`/movies/${enc(film.key)}`}>Analyse it on its page</Link>
          </Empty>
        ) : ACTS.map((name, a) => {
          const inAct = visible.filter((s) => s.act === a);
          if (!inAct.length) return null;
          const on = inAct.filter(ticked);
          return (
            <div key={name} className="trl-act">
              <div className="fnd-ghead">
                <h2>{name}</h2>
                <span className="mono fnd-dim">{on.length} of {inAct.length} · {fmtTime(on.reduce((t, s) => t + len(s), 0))}</span>
              </div>
              <div className="fnd-grid trl-grid">
                {inAct.map((s) => {
                  const isOn = ticked(s);
                  const n = isOn ? numberOf(s) : 0;
                  const nm = shotName(s, names);
                  return (
                    <div key={s.id} className={`scene-card fnd-card${isOn ? '' : ' is-out'}${pv?.id === s.id ? ' is-sel' : ''}`}>
                      <div className="scene-frame">
                        <button type="button" className="fnd-thumb" onClick={() => pick(s)} aria-label={`Preview ${nm}`}>
                          <img src={sceneFrameUrl(film.key, (s.start + s.end) / 2, 240)} alt="" loading="lazy" />
                        </button>
                        <Tick on={isOn} onChange={(v) => tick(s, v)} label={`In the trailer: ${nm}`} />
                        <span className="scene-len">{fmtTime(len(s))}</span>
                        {n > 0 && <span className="mono trl-n">{n}</span>}
                      </div>
                      <div className="trl-name">{nm}</div>
                      <div className="scene-time">{fmtTime(s.start)} in the film</div>
                      {s.spoiler && <div className="trl-spoiler">Spoiler</div>}
                    </div>
                  );
                })}
              </div>
            </div>
          );
        })}
      </section>

      <aside className="fnd-right" aria-label="Preview and picks">
        <div className="box fnd-preview">
          <div className="fnd-stage">
            {pv && (playing ? <ClipVideo src={`/api/scenes/clip?${q({ key: film.key, start: pv.start.toFixed(2), end: pv.end.toFixed(2) })}`} offset={pv.start} onEnded={() => setPlaying(false)} /> : (
              <>
                <img src={sceneFrameUrl(film.key, (pv.start + pv.end) / 2, 480)} alt="Frame from the shot you clicked" />
                <button type="button" className="fnd-play" aria-label="Play this shot" onClick={() => setPlaying(true)}>
                  <svg width="20" height="20" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><path d="M8 5l11 7-11 7z" /></svg>
                </button>
              </>
            ))}
          </div>
          <div className="fnd-pv">
            {pv ? (
              <>
                <div>
                  <div className="fnd-pv-title">{shotName(pv, names)}</div>
                  <div className="mono fnd-dim fnd-small">{fmtTime(pv.start)} in the film · {fmtTime(len(pv))} shot</div>
                </div>
                <div className="fnd-pv-text">{shownTags(pv.row).map((t) => tagName(t, names)).join(' · ')}</div>
                <Link className="fnd-fix" to={`/movies/${enc(film.key)}/scenes?${q({ from: pv.row.start.toFixed(1), to: pv.row.end.toFixed(1) })}`}>Fix in Scenes and tags</Link>
              </>
            ) : <span className="fnd-dim">Click a shot to see it here.</span>}
          </div>
        </div>

        <div className="box fnd-picks">
          <div className="fnd-ghead"><h2>In this trailer</h2><span className="mono fnd-dim">{shotNote(segs.length, total)}</span></div>
          {segs.length === 0 ? (
            <div className="fnd-none">Nothing yet. Tick the shots you want, or use Tick all.</div>
          ) : (
            <ul className="fnd-plist trl-plist">
              {segs.map(({ seg }, i) => {
                const nm = seg.desc || `Shot at ${fmtTime(seg.start)}`;
                return (
                  <li key={`${seg.src}-${seg.start}-${i}`}>
                    <span className="mono trl-pn">{i + 1}</span>
                    <span className="fnd-pthumb" style={{ backgroundImage: `url("${sourceFrameUrl(id, seg.src, (seg.start + seg.end) / 2)}")` }} />
                    <span className="fnd-pname">{nm}</span>
                    <span className="mono fnd-dim">{fmtTime(len(seg))}</span>
                    <button type="button" className="fnd-x" aria-label={`Remove ${nm}`}
                      onClick={() => change((d) => removePicks(d, [seg], { prune: false }))}>
                      <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.6" aria-hidden="true"><path d="M6 6l12 12M18 6L6 18" /></svg>
                    </button>
                  </li>
                );
              })}
            </ul>
          )}
          <Link className="btn fnd-arr" to={`/cuts/${enc(id)}/arrange`}>{segs.length ? `Arrange ${plural(segs.length, 'shot')}` : 'Arrange'}</Link>
        </div>
      </aside>
    </>,
  );
}

// A shot's name: the moment's summary, else its first tags.
function shotName(s, names) {
  return s.row.summary || shownTags(s.row).slice(0, 3).map((t) => tagName(t, names)).join(' · ') || `Shot at ${fmtTime(s.start)}`;
}
