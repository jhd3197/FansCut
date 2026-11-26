// A film's page (/movies/:key). Analysed: what is inside it (the scene map, cast, tags),
// what to make from it, the cuts that use it and its collection. Not analysed yet: the four
// analysis steps (shots, faces, speech, tags) with their real state and jobs.
// /movies/:key/cast is folded into this page and redirects here.
import { useMemo, useState } from 'react';
import { Link, Navigate, useNavigate, useParams } from 'react-router';
import { ErrorBox, Loading } from '../components/ui.jsx';
import { ArtPoster, CutTile, Crumbs, PlainPoster, enc, hm } from '../components/kit.jsx';
import { SuggestionTile } from './Discover.jsx';
import { FilmTile, ShowTile } from './Collection.jsx';
import { movieTag } from './LibraryPage.jsx';
import { personTo } from '../components/ScenePlayer.jsx';
import { isCutTag, tagName, tagTo, useTagNames } from '../components/TagChip.jsx';
import { Label } from '../components/v2.jsx';
import { useApi } from '../lib/store.js';
import { useAct, useLoad, usePageTitle, usePoll } from '../lib/hooks.js';
import { api, openInPlex } from '../lib/api.js';
import './Film.css';

const CODECS = {
  hevc: 'HEVC', h264: 'H.264', av1: 'AV1', vp9: 'VP9', mpeg2video: 'MPEG-2', mpeg4: 'MPEG-4',
  eac3: 'E-AC3', ac3: 'AC3', truehd: 'TrueHD', dts: 'DTS', aac: 'AAC', flac: 'FLAC', opus: 'Opus',
  mp3: 'MP3', pcm_s16le: 'PCM', pcm_s24le: 'PCM', subrip: 'SRT', ass: 'ASS', hdmv_pgs_subtitle: 'PGS',
  dvd_subtitle: 'VobSub', mov_text: 'Text',
};

const LANGS = {
  eng: 'English', en: 'English', jpn: 'Japanese', ja: 'Japanese', fre: 'French', fra: 'French', fr: 'French',
  ger: 'German', deu: 'German', de: 'German', spa: 'Spanish', es: 'Spanish', ita: 'Italian', it: 'Italian',
  por: 'Portuguese', pt: 'Portuguese', rus: 'Russian', ru: 'Russian', chi: 'Chinese', zho: 'Chinese',
  zh: 'Chinese', kor: 'Korean', ko: 'Korean', dut: 'Dutch', nld: 'Dutch', swe: 'Swedish', dan: 'Danish',
  nor: 'Norwegian', fin: 'Finnish', pol: 'Polish', hin: 'Hindi', ara: 'Arabic', tur: 'Turkish',
};

const codec = (c) => CODECS[c] || (c ? c.toUpperCase() : '?');
const lang = (l) => LANGS[(l || '').toLowerCase()] || (l && l !== 'und' ? l.toUpperCase() : 'Unknown language');
const qs = (o) => new URLSearchParams(o).toString();
const norm = (s) => String(s || '').trim().toLowerCase();
const plural = (n, one, many = `${one}s`) => `${n} ${n === 1 ? one : many}`;

function videoLine(v) {
  if (!v) return 'No video stream';
  const depth = /1[02]le|p010|1[02]be/.test(v.pix_fmt || '') ? (/12/.test(v.pix_fmt) ? ' 12-bit' : ' 10-bit') : '';
  return [`${codec(v.codec)}${depth}`, v.width && `${v.width}×${v.height}`,
    v.hdr && v.hdr !== 'SDR' ? v.hdr : null, v.fps ? `${Math.round(v.fps * 1000) / 1000} fps` : null]
    .filter(Boolean).join(' · ');
}

function audioLine(tracks) {
  if (!tracks?.length) return 'No audio tracks';
  return tracks.map((t) => [lang(t.language), codec(t.codec), t.channels > 2 ? `${t.channels === 6 ? '5.1' : t.channels === 8 ? '7.1' : `${t.channels} ch`}` : null]
    .filter(Boolean).join(' ')).join(' · ');
}

const subsLine = (tracks) => tracks.map((t) => `${lang(t.language)}${t.forced ? ' (forced)' : ''}`).join(' · ');

function Health({ findings }) {
  const seen = new Set();
  const bad = findings.filter((f) => (f.severity === 'error' || f.severity === 'warn')
    && !seen.has(f.message) && seen.add(f.message));
  if (!bad.length) return <span className="film-ok">No problems found</span>;
  return (
    <span className="stack" style={{ gap: 4 }}>
      {bad.map((f, i) => (
        <span key={i} className={f.severity === 'error' ? 'c-danger' : 'c-warn'}>
          {f.message}{f.fix ? <span className="muted"> — {f.fix}</span> : null}
        </span>
      ))}
      <Link to="/health" className="tiny">Open library health</Link>
    </span>
  );
}

// "This copy is the source for A, B": each a link to its cut when we know it.
function UsedBy({ m }) {
  const ids = new Map(m.cuts.map((c) => [c.title, c.id]));
  return (
    <span>
      This copy is the source for{' '}
      {m.source_for.map((t, i) => (
        <span key={t}>{i > 0 && ', '}{ids.has(t) ? <Link to={`/cuts/${enc(ids.get(t))}`}>{t}</Link> : t}</span>
      ))}
    </span>
  );
}

function PlexButton({ m }) {
  const settings = useApi('/settings', { quiet: true });
  const { act, busy } = useAct();
  const plex = Boolean(settings.data?.library?.plex?.url);
  const why = !plex ? 'Plex is not set up in Settings' : !m.on_disk ? 'No file on disk' : undefined;
  return (
    <button type="button" className="btn" disabled={busy || Boolean(why)} title={why}
      onClick={() => act(() => openInPlex(`/movies/${enc(m.key)}/plex`))}>Play in Plex</button>
  );
}

// Crumbs, poster, title, meta line, the buttons and the facts. `full`: every fact (analysed).
function FilmHead({ m, badge, buttons, full }) {
  const p = m.probe;
  const minutes = m.minutes ?? (p?.duration ? p.duration / 60 : null);
  const meta = [m.year, minutes ? hm(minutes) : null, m.edition, m.tmdb_id ? `TMDB ${m.tmdb_id}` : null].filter(Boolean).join(' · ');
  const subs = p?.subtitle_tracks || [];
  const facts = [
    full && p && ['Video', videoLine(p.video)],
    full && p && ['Audio', audioLine(p.audio_tracks)],
    full && p && subs.length > 0 && ['Subtitles', subsLine(subs)],
    full && ['Health', m.file && !p && !m.findings.length
      ? <span className="c-warn">Could not read the file</span> : <Health findings={m.findings} />],
    m.source_for?.length > 0 && ['Used by', <UsedBy key="u" m={m} />],
    m.other_versions?.length > 0 && ['Other versions', (
      <span key="v" className="row wrap" style={{ gap: 10 }}>
        {m.other_versions.map((v) => (
          <Link key={v.key} to={`/movies/${enc(v.key)}`}>{v.edition || 'Another copy'}{v.on_disk ? '' : ' (not on disk)'}</Link>
        ))}
      </span>
    )],
    ['File', m.file ? <span className="mono film-file">{m.file}</span> : <span className="c-warn">No file on disk</span>, true],
  ].filter(Boolean);
  return (
    <header className="film-head">
      <Crumbs items={[['Library', '/library'], ['Movies', '/library#movies-h'], [m.title]]} />
      <div className="film-hero">
        <div className="film-poster-art" title={movieTag(m)}>
          <ArtPoster keys={m.art ? [m.art] : []} fallback={<PlainPoster title={m.title} bare />} />
        </div>
        <div className="film-hero-body">
          <h1 className="film-title">{m.title}</h1>
          <div className="film-meta">
            <span className="mono">{meta}</span>
            {badge}
          </div>
          <div className="film-buttons">{buttons}</div>
          <dl className="film-facts">
            {facts.map(([k, v, wide]) => (
              <div key={k} className={wide ? 'wide' : undefined}><dt className="lab">{k}</dt><dd>{v}</dd></div>
            ))}
          </dl>
        </div>
      </div>
    </header>
  );
}

// -- the scene map ----------------------------------------------------------------------------
const clock = (s) => `${Math.floor(s / 3600)}:${String(Math.floor((s % 3600) / 60)).padStart(2, '0')}`;

// Every scene from start to end; `lit` (a Set of scene indexes) brightens the picked ones,
// `marks` ([{start, end}]) are stretches already used in a cut.
function SceneMap({ scenes, duration, tagged, lit, marks = [], label }) {
  const D = duration || scenes.at(-1)?.end || 1;
  const hours = [];
  for (let t = 0; t < D; t += 1800) hours.push(t);
  const pct = (x) => `${(x / D) * 100}%`;
  return (
    <div className="film-map">
      <div className="film-map-hours" aria-hidden="true">
        {hours.map((t) => <span key={t} className="mono" style={{ left: pct(t) }}>{clock(t)}</span>)}
      </div>
      <div className="film-map-bar" role="img" aria-label={label}>
        {marks.map((r, i) => <span key={`m${i}`} className="film-map-used" style={{ left: pct(r.start), width: pct(Math.max(r.end - r.start, D / 400)) }} />)}
        {scenes.map((s, i) => {
          const cls = lit ? (lit.has(i) ? 'is-lit' : 'is-dim') : (tagged?.has(i) ? 'is-tagged' : '');
          return <span key={s.id || i} className={cls} style={{ left: pct(s.start), width: `calc(${pct(s.end - s.start)} - 1px)` }} />;
        })}
      </div>
    </div>
  );
}

// Who and which tags are in each scene, from the non-rejected tag rows overlapping it.
function sceneIndex(scenes, rows) {
  const people = scenes.map(() => new Set());
  const tags = scenes.map(() => new Set());
  for (const r of rows || []) {
    if (r.status === 'rejected') continue;
    scenes.forEach((s, i) => {
      if (Math.min(r.end, s.end) - Math.max(r.start, s.start) < 1) return;
      for (const p of r.people || []) {
        if (p.name) people[i].add(norm(p.name));
        if (p.character) people[i].add(`as:${norm(p.character)}`);
      }
      for (const t of r.tags || []) tags[i].add(t);
    });
  }
  return { people, tags };
}

const isContentTag = (t) => !String(t).startsWith('fc:') && !isCutTag(t);

// -- making something from the film -------------------------------------------------------------
// A new, empty cut of this film's franchise, then its Find scenes step.
function useMakeCut(film) {
  const nav = useNavigate();
  const { act, busy } = useAct();
  const franchise = film?.collections?.[0]?.name || film?.title || '';
  const make = (params = {}) => act(async () => {
    const src = `M${film.tmdb_id}`;
    const doc = {
      schema: 2, kind: 'timeline', title: `${franchise}: new cut`.slice(0, 200), series: { title: franchise },
      sources: { [src]: { kind: 'movie', tmdb: film.tmdb_id, note: film.title, ...(film.duration ? { duration: Math.round(film.duration * 1000) / 1000 } : {}) } },
      parts: [],
    };
    const r = await api(`/series/${enc(`movie:${film.tmdb_id}`)}/timelines`, { method: 'POST', body: doc });
    const q = qs(params);
    nav(`/cuts/${enc(r.id)}/find${q ? `?${q}` : ''}`);
  });
  const why = !film ? 'Loading' : !film.tmdb_id ? 'This film has no TMDB id' : !film.on_disk ? 'No file on disk' : undefined;
  return { make, busy, why };
}

function MakeSection({ film, castTop, tagTop, names }) {
  const { make, busy, why } = useMakeCut(film);
  const off = busy || Boolean(why);
  return (
    <section id="make" className="film-section" aria-labelledby="make-h">
      <h2 id="make-h">Make something from this film</h2>
      <div className="film-make">
        <div className="film-make-card is-ai">
          <div><Label className="lab film-ai">Made by AI</Label><h3 className="hd">A trailer</h3></div>
          <div><button type="button" className="btn" disabled title="Not yet: FansCut has no trailer maker yet">Make a trailer</button></div>
        </div>
        <div className="film-make-card">
          <h3 className="hd">Every scene with one person</h3>
          <div className="tags-row">
            {castTop.length ? castTop.map((c) => (
              <button key={c.name} type="button" className="fchip" disabled={off} title={why}
                onClick={() => make({ person: c.name })}>{c.character || c.name}<em>{c.n}</em></button>
            )) : <span className="muted tiny">Nobody tagged yet</span>}
          </div>
        </div>
        <div className="film-make-card">
          <h3 className="hd">Every scene with one tag</h3>
          <div className="tags-row">
            {tagTop.length ? tagTop.map((t) => (
              <button key={t.tag} type="button" className="fchip" disabled={off} title={why}
                onClick={() => make({ tag: t.tag })}>{tagName(t.tag, names)}<em>{t.n}</em></button>
            )) : <span className="muted tiny">No tags yet</span>}
          </div>
        </div>
        <div className="film-make-card is-blank">
          <h3 className="hd">A blank cut</h3>
          <div><button type="button" className="btn" disabled={off} title={why} onClick={() => make()}>New cut</button></div>
        </div>
      </div>
    </section>
  );
}

// -- inside the film: map, cast, tags ---------------------------------------------------------
function useCutMarks(m) {
  const cuts = m.cuts.filter((c) => c.kind === 'timeline' || !c.kind);
  const ids = cuts.map((c) => c.id).join(',');
  const { data } = useLoad(() => Promise.all(cuts.map((c) => api(`/timelines/${enc(c.id)}`).catch(() => null))), [ids]);
  return useMemo(() => {
    const out = [];
    for (const t of data || []) {
      if (!t?.doc) continue;
      const srcs = Object.entries(t.doc.sources || {}).filter(([, s]) => s.kind === 'movie' && s.tmdb === m.tmdb_id).map(([k]) => k);
      const ranges = (t.doc.parts || []).flatMap((p) => p.segments || []).filter((s) => srcs.includes(s.src));
      if (ranges.length) out.push({ id: t.id, title: t.title, ranges });
    }
    return out;
  }, [data, m.tmdb_id]);
}

function Inside({ m, ov, names, pick, setPick, castRows, tagRows }) {
  const scenes = useMemo(() => ov.index?.scenes || [], [ov]);
  const idx = useMemo(() => sceneIndex(scenes, ov.tags), [scenes, ov]);
  const marks = useCutMarks(m);
  const [allCast, setAllCast] = useState(false);

  const tagged = useMemo(() => new Set(scenes.map((_, i) => i).filter((i) => idx.people[i].size || idx.tags[i].size)), [scenes, idx]);
  let lit = null;
  if (pick) {
    lit = new Set(scenes.map((_, i) => i).filter((i) => (pick.kind === 'tag'
      ? idx.tags[i].has(pick.id)
      : idx.people[i].has(norm(pick.id)) || (pick.role && idx.people[i].has(`as:${norm(pick.role)}`)))));
  }
  const hold = (p) => ({ onMouseEnter: () => setPick(p), onMouseLeave: () => setPick(null), onFocus: () => setPick(p), onBlur: () => setPick(null) });

  const shownCast = allCast ? castRows : castRows.filter((c) => c.n > 0).slice(0, 8);
  const families = new Map();
  for (const t of tagRows) {
    const f = t.family || 'other';
    if (!families.has(f)) families.set(f, []);
    families.get(f).push(t);
  }
  const familyName = (f) => f[0].toUpperCase() + f.slice(1);

  return (
    <section className="box film-inside" aria-labelledby="inside-h">
      <div className="film-inside-head">
        <h2 id="inside-h">Inside this film</h2>
        <span className="mono dim">{plural(scenes.length, 'scene')} · {plural(tagRows.length, 'tag')} · {castRows.length} cast</span>
        <span className="grow" />
        {pick && <span className="mono film-match">{pick.label}: {lit.size} of {scenes.length} scenes</span>}
        <Link className="btn btn-sm" to={`/movies/${enc(m.key)}/scenes`}>Scenes and tags</Link>
      </div>

      <div>
        <SceneMap scenes={scenes} duration={ov.index?.duration || ov.film?.duration} tagged={tagged} lit={lit}
          marks={marks.flatMap((c) => c.ranges)}
          label="Every scene in the film, start to end. Lit scenes have the person or tag you point at." />
        {marks.length > 0 && (
          <div className="film-legend">
            {marks.map((c) => (
              <span key={c.id}><i aria-hidden="true" />already in <Link to={`/cuts/${enc(c.id)}`}>{c.title}</Link></span>
            ))}
          </div>
        )}
      </div>

      {castRows.length > 0 && (
        <div>
          <div className="film-group-label">
            <Label as="span">Cast</Label><span className="mono">{castRows.length}</span>
            {castRows.length > shownCast.length || allCast ? (
              <button type="button" className="linkish" onClick={() => setAllCast((v) => !v)}>
                {allCast ? 'Fewer' : `All ${castRows.length}`}
              </button>
            ) : null}
          </div>
          <div className="film-cast">
            {shownCast.map((c) => (
              <Link key={`${c.name}-${c.character}`} className="film-person" to={personTo(m.key, c.name)}
                {...hold({ kind: 'person', id: c.name, role: c.character, label: c.character || c.name })}>
                {c.photo ? <img src={c.photo} alt="" loading="lazy" /> : <span className="film-face" aria-hidden="true">{initials(c.name)}</span>}
                <span>
                  <b>{c.name}</b>
                  <span className="sub">{[c.character, c.n ? plural(c.n, 'scene') : null].filter(Boolean).join(' · ')}</span>
                </span>
              </Link>
            ))}
          </div>
        </div>
      )}

      {families.size > 0 && (
        <div className="film-families">
          {[...families].map(([f, list]) => (
            <div key={f}>
              <div className="film-group-label"><Label as="span">{familyName(f)}</Label><span className="mono">{list.length}</span></div>
              <div className="tags-row">
                {list.map((t) => (
                  <Link key={t.tag} className="film-tag" to={tagTo(t.tag)}
                    {...hold({ kind: 'tag', id: t.tag, label: tagName(t.tag, names) })}>
                    {tagName(t.tag, names)}<em>{t.n}</em>
                  </Link>
                ))}
              </div>
            </div>
          ))}
        </div>
      )}
    </section>
  );
}

const initials = (name) => String(name || '?').split(/\s+/).map((w) => w[0]).join('').slice(0, 2);

// -- the analysed page ------------------------------------------------------------------------
function FilmAnalysed({ m, sugg }) {
  const ovq = useApi(`/scenes/film?${qs({ key: m.key, lines: 'false' })}`);
  const vocab = useApi('/scenes/vocab', { quiet: true });
  const names = useTagNames();
  const [pick, setPick] = useState(null);
  const ov = ovq.data;

  const family = useMemo(() => new Map((vocab.data?.tags || []).map((t) => [t.id, t.family])), [vocab.data]);

  const { castRows, tagRows } = useMemo(() => {
    const order = vocab.data?.families || [];
    const rows = (ov?.tags || []).filter((r) => r.status !== 'rejected');
    const counts = new Map();
    for (const r of rows) for (const t of r.tags || []) if (isContentTag(t)) counts.set(t, (counts.get(t) || 0) + 1);
    const tagList = [...counts].map(([tag, n]) => ({ tag, n, family: family.get(tag) }))
      .sort((a, b) => (order.indexOf(a.family) + 1 || 99) - (order.indexOf(b.family) + 1 || 99) || b.n - a.n || a.tag.localeCompare(b.tag));
    const castList = (ov?.cast || []).map((c) => {
      const actor = norm(c.name);
      const role = norm(c.character);
      const n = rows.filter((r) => (r.people || []).some((p) => (actor && norm(p.name) === actor) || (role && norm(p.character) === role))).length;
      return { ...c, n };
    });
    return { castRows: castList, tagRows: tagList };
  }, [ov, family, vocab.data]);

  const castTop = [...castRows].filter((c) => c.n > 0).sort((a, b) => b.n - a.n).slice(0, 4);
  const tagTop = [...tagRows].sort((a, b) => b.n - a.n).slice(0, 4);

  const installed = new Set(m.cuts.map((c) => c.title));
  const ideas = (sugg.data?.suggestions || []).filter((s) => s.keys?.includes(m.key) && !installed.has(s.title));
  const shows = [...new Map(m.collection_films.flatMap((c) => c.shows || []).map((s) => [s.key, s])).values()];
  const made = m.cuts.length + ideas.length + shows.length;

  return (
    <>
      <FilmHead m={m} full buttons={(
        <>
          <Link className="btn btn-primary" to={`/movies/${enc(m.key)}/scenes`}>Scenes and tags</Link>
          <a className="btn" href="#make">Make something from it</a>
          <PlexButton m={m} />
        </>
      )} />

      {ovq.error && !ov ? <ErrorBox error={ovq.error} onRetry={ovq.reload} /> : !ov ? <Loading what="Reading the scenes" /> : (
        <>
          <MakeSection film={ov.film} castTop={castTop} tagTop={tagTop} names={names} />
          <Inside m={m} ov={ov} names={names} pick={pick} setPick={setPick} castRows={castRows} tagRows={tagRows} />
        </>
      )}

      {made > 0 && (
        <section className="film-section" aria-labelledby="made-h">
          <div className="film-section-head"><h2 id="made-h">Made with this film</h2><span className="mono dim">{made}</span></div>
          <div className="film-grid">
            {m.cuts.map((c) => (
              <CutTile key={c.id} to={`/cuts/${enc(c.id)}`} title={c.title} tag="CUT" kind="supercut"
                meta={[c.parts && plural(c.parts, 'part'), c.minutes != null && hm(c.minutes)].filter(Boolean).join(' · ')}
                status={c.synced ? 'ready' : 'partly'} note={c.synced ? 'Synced' : 'Not synced yet'} />
            ))}
            {ideas.map((s) => <SuggestionTile key={s.id} s={s} />)}
            {shows.map((s) => <ShowTile key={s.key} s={s} />)}
          </div>
        </section>
      )}

      <Collections m={m} />
    </>
  );
}

function Collections({ m, status }) {
  return m.collection_films.map((c, i) => (
    <section key={c.name} className="film-section" aria-labelledby={`coll-${i}`}>
      <div className="film-section-head">
        <h2 id={`coll-${i}`}>In the collection: {c.name}</h2>
        <span className="mono dim">{c.films.length}</span>
        <span className="grow" />
        {status?.(c)}
        <Link to={`/collections/${encodeURIComponent(c.id || c.name)}`} className="tiny">Open collection</Link>
      </div>
      <div className="film-grid film-grid-sm">
        {c.films.map((f) => (
          <div key={f.key} className={f.key === m.key ? 'film-here' : undefined}>
            <FilmTile m={f} tag={c.name} />
            {status && <FilmState fkey={f.key} />}
          </div>
        ))}
      </div>
    </section>
  ));
}

// -- not analysed yet -------------------------------------------------------------------------
const STEPS = {
  shots: ['Shots', 'Where each shot and scene starts'],
  faces: ['Faces', 'Which main characters are on screen'],
  speech: ['Speech', 'Where people talk and where it is quiet'],
  tags: ['Tags', 'What happens in each scene'],
};
const ACTIVE = new Set(['queued', 'running']);
const NEEDS = ['Scenes with one tag', 'Scenes with one person', 'Join checks in Arrange', 'Making a cut from it'];

// The whole analysis as one number: each step a quarter, a running one by its job.
function overall(steps) {
  let sum = 0;
  for (const s of steps) {
    if (s.state === 'done' || s.state === 'off') sum += 1;
    else if (s.state === 'running') sum += s.job?.progress || 0;
  }
  return Math.round((sum / Math.max(steps.length, 1)) * 100);
}

function stateText(s) {
  if (s.state === 'running') return `Running${s.job?.progress ? ` ${Math.round(s.job.progress * 100)}%` : ''}`;
  return { done: 'Done', queued: 'In the queue', failed: 'Failed', off: 'Not available', todo: 'Not done' }[s.state] || s.state;
}

// The one job a step can start on its own, with why it can't when it can't.
function stepAction(s, by, an) {
  const shots = by.shots?.state === 'done';
  if (!an.on_disk) return { why: 'No file on disk' };
  if (s.id === 'shots' && s.state !== 'done') return { label: 'Index', path: '/scenes/index', body: { key: an.key } };
  if (s.id === 'speech' && s.state !== 'done') {
    return shots ? { label: 'Transcribe', path: '/scenes/index', body: { key: an.key, transcribe: true } } : { why: 'Read with the shots' };
  }
  if (s.id === 'faces' && s.state !== 'done') {
    return shots ? { label: s.state === 'off' ? 'Measure again' : 'Measure', path: '/scenes/signals', body: { key: an.key } } : { why: 'Needs the shots first' };
  }
  if (s.id === 'tags' && s.state !== 'done') {
    if (!an.ai) return { why: 'Set an AI model in Settings' };
    return shots ? { label: 'Tag', path: '/scenes/label', body: { key: an.key } } : { why: 'Needs the shots first' };
  }
  return null;
}

function FilmNew({ m, an, reload }) {
  const { act, busy } = useAct();
  const steps = an.steps;
  const by = Object.fromEntries(steps.map((s) => [s.id, s]));
  const active = steps.filter((s) => ACTIVE.has(s.state));
  const running = active.length > 0;
  const shotsDone = by.shots.state === 'done';
  const pct = overall(steps);
  const now = steps.find((s) => s.state === 'running') || active[0];
  const title = running ? `Analysing: ${STEPS[now.id][0].toLowerCase()}` : 'Not analysed yet';
  const ovq = useApi(shotsDone ? `/scenes/film?${qs({ key: m.key, lines: 'false' })}` : null, { quiet: true });
  const scenes = ovq.data?.index?.scenes || [];

  const start = () => act(() => api('/scenes/analyse', { method: 'POST', body: { key: m.key } }), 'Analysis queued').then(reload);
  const cancel = () => act(async () => {
    const ids = [...new Set(active.map((s) => s.job?.id).filter(Boolean))];
    for (const id of ids) await api(`/jobs/${enc(id)}/cancel`, { method: 'POST' });
  }, 'Cancelled').then(reload);
  const run = (a) => act(() => api(a.path, { method: 'POST', body: a.body }), 'Queued').then(reload);
  const startWhy = !m.on_disk ? 'No file on disk' : undefined;

  return (
    <>
      <FilmHead m={m}
        badge={<span className={`film-badge ${running ? 'is-run' : 'is-no'}`}>{running ? 'Analysing' : 'Not analysed'}</span>}
        buttons={(
          <>
            {!running && <button type="button" className="btn btn-primary" disabled={busy || Boolean(startWhy)} title={startWhy} onClick={start}>Analyse this film</button>}
            {shotsDone && <Link className="btn" to={`/movies/${enc(m.key)}/scenes`}>Scenes and tags</Link>}
            <PlexButton m={m} />
          </>
        )} />

      <section className="box film-inside" aria-labelledby="inside-h">
        <div className="film-inside-head">
          <h2 id="inside-h" className="grow">{title}</h2>
          {running && (
            <>
              <span className="mono film-pct">{pct}%</span>
              <button type="button" className="btn" disabled={busy} onClick={cancel}>Cancel</button>
              <Link className="btn" to="/jobs">See in Jobs</Link>
            </>
          )}
        </div>

        {scenes.length > 0 ? (
          <SceneMap scenes={scenes} duration={ovq.data.index.duration} label="Every scene in the film, start to end." />
        ) : (
          <div className="film-strip" role="img" aria-label="The scene strip, empty until the film is indexed">
            <div style={{ width: `${pct}%` }} />
          </div>
        )}

        <ol className="film-steps">
          {steps.map((s, i) => {
            const a = stepAction(s, by, an);
            const done = s.state === 'done';
            return (
              <li key={s.id} className={`film-step is-${s.state}`}>
                <span className="film-step-n mono" aria-hidden="true">{done ? '✓' : i + 1}</span>
                <span className="film-step-body">
                  <b>{STEPS[s.id][0]}</b>
                  <span className="dim">{STEPS[s.id][1]}</span>
                  <span className={`film-step-state${s.state === 'failed' ? ' c-danger' : ''}`}>
                    {stateText(s)}{s.detail ? ` · ${s.detail}` : ''}
                  </span>
                  {a && !ACTIVE.has(s.state) && (a.path
                    ? <button type="button" className="btn btn-sm" disabled={busy} onClick={() => run(a)}>{a.label}</button>
                    : null)}
                </span>
              </li>
            );
          })}
        </ol>

        <div>
          <Label>Needs analysis first</Label>
          <div className="tags-row film-needs">
            {NEEDS.map((n) => <span key={n}>{n}</span>)}
          </div>
        </div>
      </section>

      <Collections m={m} status={(c) => <AnalyseAll c={c} />} />
    </>
  );
}

// A collection film's analysis state, under its tile.
function FilmState({ fkey }) {
  const { data } = useApi(`/scenes/analysis?${qs({ key: fkey })}`, { quiet: true });
  if (!data) return null;
  const act = data.steps.some((s) => ACTIVE.has(s.state));
  const [cls, text] = data.analysed ? ['is-ok', 'Analysed'] : act ? ['is-run', 'In the queue'] : ['is-no', 'Not analysed'];
  return <span className={`film-state ${cls}`}>{text}</span>;
}

function AnalyseAll({ c }) {
  const { act, busy } = useAct();
  const keys = c.films.filter((f) => f.on_disk).map((f) => f.key);
  const { data, reload } = useLoad(() => Promise.all(keys.map((k) => api(`/scenes/analysis?${qs({ key: k })}`).catch(() => null))), [keys.join(',')]);
  const left = (data || []).filter((a) => a && !a.analysed && !a.steps.some((s) => ACTIVE.has(s.state)));
  if (!data) return null;
  if (!left.length) return <span className="mono dim">All {keys.length} analysed or in the queue</span>;
  return (
    <>
      <span className="mono dim">{left.length} of {c.films.length} not analysed</span>
      <button type="button" className="btn btn-sm" disabled={busy} onClick={() => act(async () => {
        for (const a of left) await api('/scenes/analyse', { method: 'POST', body: { key: a.key } });
      }, `Queued ${plural(left.length, 'film')}`).then(reload)}>Analyse all {left.length}</button>
    </>
  );
}

// -- the page ---------------------------------------------------------------------------------
export function MoviePage() {
  const { key } = useParams();
  const { data: m, error, loading, reload } = useApi(`/library/movies/${encodeURIComponent(key)}`);
  const anq = useApi(`/scenes/analysis?${qs({ key })}`);
  const sugg = useApi('/suggestions', { quiet: true });
  usePageTitle(m?.title);
  const an = anq.data;
  const busy = Boolean(an?.steps?.some((s) => ACTIVE.has(s.state)));
  usePoll(anq.reload, 3000, busy);

  const crumbs = <Crumbs items={[['Library', '/library'], ['Movies', '/library#movies-h'], [m?.title || key]]} />;
  if (error && !m) return <div className="page">{crumbs}<ErrorBox error={error} onRetry={reload} /></div>;
  if (loading || !m) return <div className="page"><Loading what="Reading the file" /></div>;
  if (anq.error && !an) return <div className="page film">{crumbs}<ErrorBox error={anq.error} onRetry={anq.reload} /></div>;
  if (!an) return <div className="page"><Loading what="Reading the scenes" /></div>;

  return (
    <div className="page film">
      {an.analysed ? <FilmAnalysed m={m} sugg={sugg} /> : <FilmNew m={m} an={an} reload={anq.reload} />}
    </div>
  );
}

// The old cast page is part of the film page now.
export function MovieCastRedirect() {
  const { key } = useParams();
  return <Navigate to={`/movies/${enc(key)}`} replace />;
}
