// Step 1 of making a cut (/cuts/:id/find): find scenes in the cut's franchise by film, main
// character and tag, tick the ones you want. Ticking adds or removes segments in the cut's
// recipe straight away; step 2 (Arrange) puts them in order.
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Link, useParams, useSearchParams } from 'react-router';
import { api } from '../lib/api.js';
import { useLoad, usePageTitle, usePoll } from '../lib/hooks.js';
import { useApi } from '../lib/store.js';
import { toast } from '../lib/toast.js';
import { fmtTime } from '../lib/time.js';
import {
  addPicks, analysed, cutLength, dedupeRows, filmsForCut, inCut, isSure, matchRows, personId, removePicks,
  segList, shortTitle, sourceKey, tagCounts, topPeople,
} from '../lib/picks.js';
import { enc } from '../components/kit.jsx';
import { Empty, ErrorBox, Loading } from '../components/ui.jsx';
import { EditorSteps, FilmDot, FilterChip, Label, Tick, filmColours } from '../components/v2.jsx';
import { ClipVideo, CLIP_MAX, clipUrl, q, sceneFrameUrl, shownTags, sourceFrameUrl } from '../components/ScenePlayer.jsx';
import { isShownTag, tagName, useTagNames } from '../components/TagChip.jsx';
import logoWhite from '../brand/logo-white.svg';
import './Find.css';

const ACTIVE = ['queued', 'running'];
const ANALYSIS_JOBS = ['scene_index', 'scene_label', 'scene_signals'];
const SHOWN = 120; // scene cards drawn before "Show all"
const plural = (n, one, many = `${one}s`) => `${n} ${n === 1 ? one : many}`;
const len = (x) => Math.max(0, x.end - x.start);

// The cut's recipe, with edits saved in order: each change is applied to the recipe as
// the server has it (GET), then PUT. The page shows the change at once.
export function useCutDoc(id) {
  const tl = useLoad(() => api(`/timelines/${enc(id)}`), [id]);
  const [local, setLocal] = useState(null);
  const queue = useRef(Promise.resolve());
  const pending = useRef(0);
  const [saving, setSaving] = useState(false);
  const { reload } = tl;
  const base = tl.data?.doc;
  const change = useCallback((fn) => {
    setLocal((d) => fn(d || base));
    pending.current += 1;
    setSaving(true);
    queue.current = queue.current.then(async () => {
      let saved = null;
      try {
        const fresh = (await api(`/timelines/${enc(id)}`)).doc;
        saved = (await api(`/timelines/${enc(id)}`, { method: 'PUT', body: fn(fresh) })).doc;
      } catch (e) {
        toast(e.message || String(e), { ok: false });
      }
      pending.current -= 1;
      if (pending.current === 0) {
        if (saved) setLocal(saved);
        else { await reload(); setLocal(null); }
        setSaving(false);
      }
    });
  }, [id, base, reload]);
  return { ...tl, doc: local || base, change, saving };
}

// A trailer is a cut whose category says so (made from a film's page).
export const isTrailer = (doc) => /trailer/i.test(doc?.category || '');

// The bar over both editor steps: home, the cut's name, the steps, what is in it, Arrange.
export function EditorBar({ id, kind, title, step, what, note }) {
  return (
    <header className="ed-bar">
      <Link to="/" className="ed-logo" aria-label="FansCut home"><img src={logoWhite} alt="FansCut" width="90" height="22" /></Link>
      <span className="ed-sep" aria-hidden="true" />
      <Link to={`/cuts/${enc(id)}`} className="ed-kind">{kind}</Link>
      <span className="hd ed-title">{title}</span>
      <span className="ed-gap" />
      <EditorSteps cut={id} step={step} />
      <span className="ed-gap" />
      <span className="lab">{what}</span>
      <span className="mono ed-note">{note}</span>
      <Link className="btn btn-primary" to={`/cuts/${enc(id)}/arrange`}>Arrange</Link>
    </header>
  );
}

// "3 scenes · 4:12"
const pickNote = (n, secs, one = 'scene') => `${plural(n, one)} · ${fmtTime(secs)}`;

export function Find() {
  const { id } = useParams();
  const cut = useCutDoc(id);
  usePageTitle(cut.doc ? `${cut.doc.title}: find scenes` : 'Find scenes');
  if (cut.error && !cut.data) return <div className="page"><ErrorBox error={cut.error} onRetry={cut.reload} /></div>;
  if (!cut.doc) return <div className="page"><Loading /></div>;
  return <FindScenes id={id} cut={cut} />;
}

function FindScenes({ id, cut }) {
  const { doc, change } = cut;
  const [params, setParams] = useSearchParams();
  const tags = params.getAll('tag');
  const person = params.get('person') || '';
  const off = useMemo(() => new Set(params.getAll('off')), [params]);
  const sel = params.get('scene') || '';
  const setQ = (fn) => setParams((p) => { const n = new URLSearchParams(p); fn(n); return n; }, { replace: true });
  const toggleIn = (key, v) => setQ((n) => {
    const all = n.getAll(key);
    n.delete(key);
    for (const x of all.includes(v) ? all.filter((y) => y !== v) : [...all, v]) n.append(key, x);
  });

  const names = useTagNames();
  const settings = useApi('/settings', { quiet: true });
  const threshold = settings.data?.scenes?.auto_accept ?? 0.75;
  const aiReady = !!settings.data?.ai?.model;
  const filmsQ = useLoad(() => api('/scenes/films'), []);
  const { franchise, films } = useMemo(() => filmsForCut(doc, filmsQ.data?.films), [doc, filmsQ.data]);
  const colours = useMemo(() => filmColours(films.map((f) => f.key)), [films]);
  const byKey = useMemo(() => Object.fromEntries(films.map((f) => [f.key, f])), [films]);
  const bySrc = useMemo(() => Object.fromEntries(films.filter((f) => f.tmdb_id).map((f) => [sourceKey(f), f])), [films]);
  const tagged = films.filter((f) => f.indexed || (f.tags?.accepted || 0) + (f.tags?.proposed || 0) > 0).map((f) => f.key);
  const tagKeys = tagged.join('|');
  const rowsQ = useLoad(() => Promise.all(tagKeys ? tagKeys.split('|').map((k) => api(`/scenes/tags?${q({ key: k })}`)) : [])
    .then((rs) => rs.flatMap((r) => r.tags)), [tagKeys]);

  // analysis jobs on these films: reload when one finishes
  const jobs = useApi('/jobs', { quiet: true });
  usePoll(jobs.reload, 4000, true);
  const busyKeys = new Set((jobs.data?.jobs || [])
    .filter((j) => ACTIVE.includes(j.status) && ANALYSIS_JOBS.includes(j.kind)).map((j) => j.payload?.key));
  const busySig = [...busyKeys].filter((k) => byKey[k]).sort().join('|');
  const lastBusy = useRef(busySig);
  const reloadFilms = filmsQ.reload;
  const reloadRows = rowsQ.reload;
  useEffect(() => {
    const was = lastBusy.current.split('|').filter(Boolean);
    lastBusy.current = busySig;
    if (was.some((k) => !busySig.split('|').includes(k))) { reloadFilms(); reloadRows(); }
  }, [busySig, reloadFilms, reloadRows]);

  const [all, setAll] = useState(false);
  const [moreTags, setMoreTags] = useState(false);
  const [playing, setPlaying] = useState(false);
  const [analysing, setAnalysing] = useState(false);

  const rows = useMemo(() => dedupeRows((rowsQ.data || []).filter((r) => byKey[r.film_key] && shownTags(r).length)), [rowsQ.data, byKey]);
  const order = useMemo(() => Object.fromEntries(films.map((f, i) => [f.key, i])), [films]);
  const sortRows = (list) => [...list].sort((a, b) => order[a.film_key] - order[b.film_key] || a.start - b.start);
  const found = sortRows(matchRows(rows, { off, tags, person }));
  const sure = found.filter((r) => isSure(r, threshold));
  const maybe = found.filter((r) => !isSure(r, threshold));
  const segs = segList(doc);
  const total = cutLength(doc);

  if (filmsQ.error && !filmsQ.data) return <Shell id={id} doc={doc}><ErrorBox error={filmsQ.error} onRetry={filmsQ.reload} /></Shell>;
  if (!filmsQ.data) return <Shell id={id} doc={doc}><Loading /></Shell>;
  if (!films.length) {
    return (
      <Shell id={id} doc={doc}>
        <Empty>
          Find scenes works on films. {franchise ? `No film in your library belongs to ${franchise}.` : 'This cut has no franchise yet.'}{' '}
          <Link to={`/cuts/${enc(id)}/arrange`}>Arrange it by hand</Link>
        </Empty>
      </Shell>
    );
  }

  const film = (r) => byKey[r.film_key];
  const srcOf = (r) => (film(r)?.tmdb_id ? sourceKey(film(r)) : null);
  const ticked = (r) => !!srcOf(r) && inCut(doc, srcOf(r), r);
  const toPick = (r) => {
    const f = film(r);
    return {
      src: sourceKey(f), start: r.start, end: r.end, desc: r.summary,
      source: { kind: 'movie', tmdb: f.tmdb_id, note: f.title, ...(f.duration ? { duration: Math.round(f.duration * 1000) / 1000 } : {}) },
    };
  };
  const range = (r) => ({ src: srcOf(r), start: r.start, end: r.end });
  const tick = (r, on) => change((d) => (on ? addPicks(d, [toPick(r)]) : removePicks(d, [range(r)])));
  const tickAll = () => change((d) => addPicks(d, sure.filter(srcOf).map(toPick)));
  const untickAll = () => change((d) => removePicks(d, found.filter(srcOf).map(range)));

  // filter options, each counted under the other filters
  const peopleRows = matchRows(rows, { off, tags });
  const people = topPeople(peopleRows);
  if (person && !people.some((p) => p.id === person)) people.unshift({ id: person, n: 0 });
  const tagRows = matchRows(rows, { off, person });
  const counted = tagCounts(tagRows, isShownTag);
  const tagOpts = [...tags.map((t) => counted.find((c) => c.tag === t) || { tag: t, n: 0 }),
    ...counted.filter((c) => !tags.includes(c.tag))];
  const tagShown = moreTags ? tagOpts : tagOpts.slice(0, Math.max(14, tags.length));
  const filmCount = (f) => matchRows(rows, { tags, person }).filter((r) => r.film_key === f.key).length;

  const hasRows = new Set(rows.map((r) => r.film_key));
  const notDone = films.filter((f) => !f.indexed || !(analysed(f) || hasRows.has(f.key)));
  const canAnalyse = (f) => f.on_disk && !busyKeys.has(f.key) && (!f.indexed || aiReady);
  const analyse = async () => {
    setAnalysing(true);
    let n = 0;
    for (const f of notDone.filter(canAnalyse)) {
      try {
        await api(f.indexed ? '/scenes/label' : '/scenes/index', { method: 'POST', body: { key: f.key } });
        n += 1;
      } catch (e) {
        toast(`${f.title}: ${e.message}`, { ok: false });
      }
    }
    if (n) toast(`Started ${plural(n, 'job')}. Follow them in Jobs.`);
    jobs.reload();
    setAnalysing(false);
  };

  const pv = found.find((r) => r.id === sel) || sure[0] || maybe[0] || null;
  const pick = (r) => { setPlaying(false); setQ((n) => n.set('scene', r.id)); };
  const nFilms = new Set(sure.map((r) => r.film_key)).size;
  const sureSecs = sure.reduce((t, r) => t + len(r), 0);
  const shownSure = all ? sure : sure.slice(0, SHOWN);
  const card = (r) => (
    <SceneCard key={r.id} row={r} film={film(r)} franchise={franchise} colour={colours[r.film_key]} names={names}
      on={ticked(r)} sel={pv?.id === r.id} canTick={!!srcOf(r)} onPick={() => pick(r)} onTick={(on) => tick(r, on)} />
  );

  return (
    <Shell id={id} doc={doc}>
      <aside className="fnd-side" aria-label="Filters">
        <div className="fnd-ask">
          <Label as="label" htmlFor="fnd-ask">Ask for scenes</Label>
          <input id="fnd-ask" className="inp" type="text" placeholder="Describe the scenes you want" disabled title="Not yet" />
          <button type="button" className="btn" disabled title="Not yet">Find scenes</button>
        </div>

        <div>
          <Label className="lab fnd-lab">Look in</Label>
          {films.map((f) => {
            const on = !off.has(f.key);
            return (
              <button key={f.key} type="button" className={`fnd-frow${on ? '' : ' is-off'}`} aria-pressed={on} onClick={() => toggleIn('off', f.key)}>
                <span className="fnd-box" aria-hidden="true" />
                <FilmDot colour={colours[f.key]} />
                <span className="fnd-fname">{shortTitle(f.title, franchise)}</span>
                <span className="mono fnd-fn">{filmCount(f)}</span>
              </button>
            );
          })}
        </div>

        {people.length > 0 && (
          <div>
            <Label className="lab fnd-lab">Main characters</Label>
            <div className="tags-row fnd-chips">
              {people.map((p) => (
                <FilterChip key={p.id} on={person === p.id} className="fnd-person"
                  onClick={() => setQ((n) => { if (person === p.id) n.delete('person'); else n.set('person', p.id); })}>
                  <span className="fnd-av" aria-hidden="true">{p.id.charAt(0)}</span>{p.id}
                </FilterChip>
              ))}
            </div>
          </div>
        )}

        {tagOpts.length > 0 && (
          <div>
            <Label className="lab fnd-lab">Tags</Label>
            <div className="tags-row fnd-chips">
              {tagShown.map((x) => (
                <FilterChip key={x.tag} on={tags.includes(x.tag)} count={x.n} onClick={() => toggleIn('tag', x.tag)}>
                  {tagName(x.tag, names)}
                </FilterChip>
              ))}
              {tagOpts.length > tagShown.length && (
                <button type="button" className="tag-add" onClick={() => setMoreTags(true)}>{tagOpts.length - tagShown.length} more</button>
              )}
            </div>
          </div>
        )}
      </aside>

      <section className="fnd-main" aria-label="Scenes found">
        <div className="fnd-head">
          <h1>{plural(sure.length, 'scene')} found</h1>
          <span className="mono fnd-dim">in {plural(nFilms, 'film')} · {fmtTime(sureSecs)}{person ? ` · with ${person}` : ''}</span>
          <span className="ed-gap" />
          <button type="button" className="btn btn-sm" disabled={!sure.some((r) => srcOf(r) && !ticked(r))} onClick={tickAll}>Tick all</button>
          <button type="button" className="btn btn-sm btn-quiet" disabled={!found.some(ticked)} onClick={untickAll}>Untick all</button>
        </div>
        {rowsQ.error && <ErrorBox error={rowsQ.error} onRetry={rowsQ.reload} />}
        {!rowsQ.data && !rowsQ.error ? <Loading /> : sure.length === 0 ? (
          <Empty>{rows.length ? 'No scene matches these filters.' : 'No tagged scenes in these films yet.'}</Empty>
        ) : (
          <div className="fnd-grid">{shownSure.map(card)}</div>
        )}
        {sure.length > shownSure.length && (
          <button type="button" className="btn btn-sm fnd-more" onClick={() => setAll(true)}>Show all {sure.length}</button>
        )}

        {maybe.length > 0 && (
          <div className="fnd-maybe">
            <div className="fnd-ghead">
              <h2>Maybe</h2><span className="mono fnd-dim">{maybe.length}</span>
              <span className="fnd-dim">under {Math.round(threshold * 100)}% sure</span>
            </div>
            <div className="fnd-grid">{maybe.map(card)}</div>
          </div>
        )}

        {notDone.length > 0 && (
          <div className="fnd-unk">
            <div className="fnd-ghead">
              <h2>Not analysed yet</h2><span className="mono fnd-dim">{notDone.length}</span>
              <span className="ed-gap" />
              <button type="button" className="btn btn-sm" disabled={analysing || !notDone.some(canAnalyse)} onClick={analyse}
                title={notDone.some(canAnalyse) ? undefined : 'Nothing to start here'}>Analyse these films</button>
            </div>
            <ul className="fnd-unk-list">
              {notDone.map((f) => (
                <li key={f.key}>
                  <FilmDot colour={colours[f.key]} />
                  <Link to={`/movies/${enc(f.key)}`}>{shortTitle(f.title, franchise)}</Link>
                  <span className="mono fnd-dim">{f.year || ''}</span>
                  <span className={`state ${busyKeys.has(f.key) ? 'state-ready' : 'state-missing'}`}>
                    {busyKeys.has(f.key) ? 'Analysing' : !f.on_disk ? 'No file on disk'
                      : !f.indexed ? 'Not indexed' : aiReady ? 'Not tagged' : 'Not tagged: set an AI model in Settings'}
                  </span>
                </li>
              ))}
            </ul>
          </div>
        )}
      </section>

      <aside className="fnd-right" aria-label="Preview and picks">
        <Preview row={pv} film={pv && film(pv)} names={names} playing={playing} onPlay={() => setPlaying(true)} onEnded={() => setPlaying(false)} />
        <div className="box fnd-picks">
          <div className="fnd-ghead">
            <h2>In this cut</h2><span className="mono fnd-dim">{pickNote(segs.length, total)}</span>
          </div>
          {segs.length === 0 ? (
            <div className="fnd-none">Nothing yet. Tick the scenes you want, or use Tick all.</div>
          ) : (
            <ul className="fnd-plist">
              {segs.map(({ seg }, i) => {
                const f = bySrc[seg.src];
                const name = f ? shortTitle(f.title, franchise) : doc.sources?.[seg.src]?.note || seg.src;
                return (
                  <li key={`${seg.src}-${seg.start}-${i}`}>
                    <span className="fnd-pthumb" style={{ '--film': f ? colours[f.key] : 'var(--faint)', backgroundImage: `url("${sourceFrameUrl(id, seg.src, (seg.start + seg.end) / 2)}")` }} />
                    <span className="fnd-pname">{name}</span>
                    <span className="mono fnd-dim">{fmtTime(len(seg))}</span>
                    <button type="button" className="fnd-x" aria-label={`Remove ${name} at ${fmtTime(seg.start)}`}
                      onClick={() => change((d) => removePicks(d, [seg]))}>
                      <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.6" aria-hidden="true"><path d="M6 6l12 12M18 6L6 18" /></svg>
                    </button>
                  </li>
                );
              })}
            </ul>
          )}
          <Link className="btn fnd-arr" to={`/cuts/${enc(id)}/arrange`}>{segs.length ? `Arrange ${plural(segs.length, 'scene')}` : 'Arrange'}</Link>
        </div>
      </aside>
    </Shell>
  );
}

function Shell({ id, doc, children }) {
  const segs = segList(doc);
  return (
    <div className="ed-page">
      <EditorBar id={id} kind="Cut" title={doc.title} step="find" what="In this cut" note={pickNote(segs.length, cutLength(doc))} />
      <div className="fnd-body">{children}</div>
    </div>
  );
}

function SceneCard({ row, film, franchise, colour, names, on, sel, canTick, onPick, onTick }) {
  const name = shortTitle(film?.title, franchise);
  const at = fmtTime(row.start);
  return (
    <div className={`scene-card fnd-card${on ? '' : ' is-out'}${sel ? ' is-sel' : ''}`}>
      <div className="scene-frame">
        <button type="button" className="fnd-thumb" onClick={onPick} aria-label={`Preview the scene from ${name} at ${at}`}>
          <img src={sceneFrameUrl(row.film_key, (row.start + row.end) / 2, 240)} alt="" loading="lazy" />
        </button>
        <Tick on={on} onChange={onTick} disabled={!canTick} label={`In the cut: ${name} at ${at}`} />
        <span className="scene-len">{fmtTime(len(row))}</span>
      </div>
      <div className="scene-film"><FilmDot colour={colour} /><span>{name}</span></div>
      <div className="scene-time">{at} in the film</div>
      <div className="scene-tags">{shownTags(row).slice(0, 5).map((t) => tagName(t, names)).join(' · ')}</div>
    </div>
  );
}

function Preview({ row, film, names, playing, onPlay, onEnded }) {
  if (!row || !film) return <div className="box fnd-preview"><div className="fnd-stage" /><div className="fnd-pv"><span className="fnd-dim">Click a scene to see it here.</span></div></div>;
  const end = Math.min(row.end, row.start + CLIP_MAX);
  const cast = [...new Set((row.people || []).map(personId).filter(Boolean))];
  return (
    <div className="box fnd-preview">
      <div className="fnd-stage">
        {playing ? <ClipVideo src={clipUrl(row.film_key, row.start, end)} offset={row.start} onEnded={onEnded} /> : (
          <>
            <img src={sceneFrameUrl(row.film_key, (row.start + row.end) / 2, 480)} alt="Frame from the scene you clicked" />
            <button type="button" className="fnd-play" aria-label="Play this scene" onClick={onPlay}>
              <svg width="20" height="20" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><path d="M8 5l11 7-11 7z" /></svg>
            </button>
          </>
        )}
      </div>
      <div className="fnd-pv">
        <div>
          <div className="fnd-pv-title">{film.title}</div>
          <div className="mono fnd-dim fnd-small">{fmtTime(row.start)} to {fmtTime(row.end)} · {fmtTime(len(row))}</div>
        </div>
        {row.summary && <div className="fnd-pv-text">{row.summary}</div>}
        <div><Label>Tags</Label><div className="fnd-pv-text">{shownTags(row).map((t) => tagName(t, names)).join(', ')}</div></div>
        {cast.length > 0 && <div><Label>Main characters</Label><div className="fnd-pv-text">{cast.join(', ')}</div></div>}
        <Link className="fnd-fix" to={`/movies/${enc(row.film_key)}/scenes?${q({ from: row.start.toFixed(1), to: row.end.toFixed(1) })}`}>Fix in Scenes and tags</Link>
      </div>
    </div>
  );
}
