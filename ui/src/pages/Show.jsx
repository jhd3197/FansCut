import { useRef, useState } from 'react';
import { Link, useNavigate, useParams } from 'react-router';
import { api, seriesPath } from '../lib/api.js';
import { useAct, useLoad, usePageTitle } from '../lib/hooks.js';
import { CutTile, Crumbs, KV, PlainPoster, Section, enc } from '../components/kit.jsx';
import { Dialog, ErrorBox, Loading, Notice } from '../components/ui.jsx';
import { MatchDialog } from '../components/MatchDialog.jsx';
import { RecipeDialog, downloadJson, recipeFileName } from '../components/Recipe.jsx';
import { audioSummary, showFolder, subtitleSummary } from './Episode.jsx';
import { useApi } from '../lib/store.js';
import { SuggestionDialog, SuggestionTile } from './Discover.jsx';
import { FilmTile } from './Collection.jsx';

const BLANK = (title) => ({
  schema: 2, kind: 'timeline', title: `${title} — Timeline`,
  series: { title }, sources: {}, parts: [{ title: 'Part 1', segments: [] }],
});

const yearOf = (d) => (d ? Number(String(d).slice(0, 4)) || null : null);
function yearSpan(eps, fallback) {
  const ys = eps.map((e) => yearOf(e.air_date)).filter(Boolean);
  if (!ys.length) return fallback ? String(fallback) : '';
  const lo = Math.min(...ys);
  const hi = Math.max(...ys);
  return lo === hi ? String(lo) : `${lo}–${hi}`;
}
const plural = (n, one, many = `${one}s`) => `${n} ${n === 1 ? one : many}`;

// A recipe's category ("Canon cut", "Chronological") as caption text; never on the art.
const cutKind = (t) => (t.category ? t.category.charAt(0).toUpperCase() + t.category.slice(1) : 'Timeline');

// "Missing OVA 1, Final Chapters and 2 more": which specials, so the gap means something.
function missingNote(eps) {
  const gone = eps.filter((e) => !e.has_file).map((e) => e.title || e.id);
  const head = gone.slice(0, 2).join(', ');
  return gone.length > 2 ? `Missing ${head} and ${gone.length - 2} more` : `Missing ${head}`;
}

function SeasonTile({ to, season, eps }) {
  const onDisk = eps.filter((e) => e.has_file).length;
  const missing = eps.length - onDisk;
  const special = season === 0;
  if (special && missing) {
    return (
      <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
        <SeasonLink to={to} season={season} eps={eps} onDisk={onDisk} missing={missing} special
          note={missingNote(eps)} />
        <Link to="/health" className="tiny">Why this matters: see Health</Link>
      </div>
    );
  }
  return <SeasonLink to={to} season={season} eps={eps} onDisk={onDisk} missing={missing} special={special} />;
}

function SeasonLink({ to, season, eps, onDisk, missing, special, note }) {
  return (
    <Link to={to} style={{ display: 'flex', flexDirection: 'column', gap: 8, color: 'var(--text)', textDecoration: 'none' }}>
      <div style={{ aspectRatio: '16 / 9', borderRadius: 6, background: special && missing ? 'color-mix(in srgb, var(--danger) 8%, var(--panel))' : 'var(--poster)', border: '1px solid var(--line)', display: 'flex', alignItems: 'flex-end', padding: 14, boxSizing: 'border-box' }}>
        <span className="display" style={{ fontSize: 24, lineHeight: 1, color: special && missing ? 'var(--danger)' : 'var(--text)' }}>
          {special ? 'Specials' : `Season ${season}`}
        </span>
      </div>
      <span style={{ fontSize: 13, fontWeight: 600 }}>{missing ? `${onDisk} of ${plural(eps.length, 'episode')}` : plural(eps.length, 'episode')}</span>
      <span className={`tiny ${missing ? 'c-danger' : 'muted'}`}>{note || (missing ? `${missing} missing` : yearSpan(eps) || ' ')}</span>
    </Link>
  );
}

export function ShowPage() {
  const { key } = useParams();
  const base = seriesPath(key);
  const nav = useNavigate();
  const view = useApi(`${base}/episodes`);
  usePageTitle(view.data?.series?.title);
  const tls = useApi(`${base}/timelines`);
  const sugg = useApi('/suggestions', { quiet: true });
  // a franchise collection holding this show: its films go in "Also in your library"
  const colls = useApi('/library/collections', { quiet: true });
  const franchise = (colls.data?.collections || []).find((c) => c.shows?.includes(key));
  const also = useApi(franchise ? `/library/collections/${enc(franchise.name)}` : null, { quiet: true });
  const [idea, setIdea] = useState(null);
  const eps = view.data?.episodes || [];
  const first = (eps.find((e) => e.has_file && e.season > 0) || eps.find((e) => e.has_file))?.id;
  const probe = useLoad(() => (first ? api(`${base}/episodes/${enc(first)}/info`).catch(() => null) : Promise.resolve(null)), [key, first]);
  const { act, msg, busy, clear } = useAct();
  const [dialog, setDialog] = useState(null); // 'match' | 'recipe' | {del: timeline}
  const file = useRef(null);

  if (view.error && !view.data) return <div className="page"><ErrorBox error={view.error} onRetry={view.reload} /></div>;
  if (!view.data) return <div className="page"><Loading /></div>;

  const { series, matches, filler_sources: sources } = view.data;
  const showTo = `/series/${enc(key)}`;
  const seasons = [...new Set(eps.map((e) => e.season))].sort((a, b) => (a === 0) - (b === 0) || a - b);
  const regular = seasons.filter((s) => s > 0);
  const onDisk = eps.filter((e) => e.has_file).length;
  const whole = matches.find((m) => m.season === -1) || matches[0];
  const matchText = whole ? [whole.anilist_id && `AniList ${whole.anilist_id}`, whole.mal_id && `MAL ${whole.mal_id}`].filter(Boolean).join(' · ') : '';

  const counts = {};
  for (const e of eps) counts[e.label] = (counts[e.label] || 0) + 1;
  const srcNames = [...new Set(Object.values(sources || {}).flatMap((s) => [s?.anifiller && 'AniFiller', s?.jikan && 'Jikan'].filter(Boolean)))];
  const labelText = [
    ...['canon', 'mixed', 'filler', 'unknown'].filter((l) => counts[l]).map((l) => `${counts[l]} ${l}`),
    srcNames.length ? `filler data from ${srcNames.join(' and ')}` : 'no filler data',
  ].join(' · ');

  const info = probe.data;
  const audio = audioSummary(info?.probe);
  const folder = showFolder(info?.file);
  const timelines = tls.data?.timelines || [];
  const have = new Set(timelines.map((t) => t.title));
  const ideas = (sugg.data?.suggestions || []).filter((x) => x.keys?.includes(key) && !have.has(x.title));

  const autoMatch = () => act(async () => {
    const r = await api(`${base}/match/auto`, { method: 'POST' });
    view.reload();
    return r;
  }, (r) => (r.matched ? `Matched to “${r.candidate.title}”` : 'No confident match found. Pick one by hand.'));
  const detectAll = () => act(async () => {
    for (const s of seasons) await api(`${base}/detect-trims`, { method: 'POST', body: { season: s } });
    return seasons.length;
  }, (n) => `Detection queued for ${plural(n, 'season')}. It runs in Jobs.`);
  const create = (doc) => act(async () => {
    const r = await api(`${base}/timelines`, { method: 'POST', body: doc });
    nav(`/cuts/${enc(r.id)}/edit`);
    return r;
  });
  const onFile = async (e) => {
    const f = e.target.files[0];
    e.target.value = '';
    if (!f) return;
    let doc;
    try { doc = JSON.parse(await f.text()); } catch { await act(async () => { throw new Error('that file is not valid JSON'); }); return; }
    await create(doc);
  };
  const exportTl = (t) => act(async () => downloadJson(recipeFileName(t.title), await api(`/timelines/${enc(t.id)}/export?anchors=none`)));
  const deleteTl = (t) => act(async () => {
    await api(`/timelines/${enc(t.id)}`, { method: 'DELETE' });
    setDialog(null);
    await tls.reload();
  }, `Deleted “${t.title}”`);

  return (
    <div className="page" style={{ gap: 30 }}>
      <Crumbs items={[['Library', '/library'], ['Shows', '/library#shows-h'], [series.title]]} />
      <Notice msg={msg} onClose={clear} />

      <div className="hero" style={{ gap: 28 }}>
        <div style={{ width: 200, flex: 'none' }}>
          <PlainPoster tag={`TV · ${plural(regular.length, 'season')}`} title={series.title} />
        </div>
        <div className="hero-body">
          <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
            <h1 className="hero-title" style={{ fontSize: 30 }}>{series.title}</h1>
            <span className="mono muted" style={{ fontSize: 13 }}>
              {[yearSpan(eps.filter((e) => e.season > 0), series.year), plural(regular.length, 'season'),
                `${onDisk} of ${plural(eps.length, 'episode')}`, matchText].filter(Boolean).join(' · ')}
            </span>
          </div>
          <KV rows={[
            ['Labels', labelText],
            ['Catalogue', (
              <span className="row wrap">
                <span>{matchText || <span className="muted">Not matched. Labels fall back to “unknown”.</span>}</span>
                <button type="button" className="btn btn-sm" onClick={() => setDialog('match')}>{whole ? 'Change' : 'Match to MAL / AniList'}</button>
                <button type="button" className="btn btn-sm btn-ghost" onClick={autoMatch} disabled={busy}>Auto-match</button>
              </span>
            )],
            ['Audio', probe.loading && !info ? <span className="muted">Reading…</span>
              : audio ? <span className={audio.english ? '' : 'c-accent'}>{audio.text}</span> : <span className="muted">–</span>],
            ['Subtitles', info?.probe ? subtitleSummary(info.probe) : <span className="muted">–</span>],
            ['Folder', folder ? <span className="mono tiny">{folder}</span> : <span className="muted">No files on disk</span>],
          ]} />
          <div className="row wrap">
            {regular.length || seasons.length ? (
              <Link className="btn btn-primary" to={`${showTo}/season/${regular[0] ?? seasons[0]}`}>Build a cut</Link>
            ) : null}
            <button type="button" className="btn" onClick={detectAll} disabled={busy || !onDisk}
              title="Fingerprint the audio of every season to find openings, endings and recaps">Detect openings and endings</button>
            <button type="button" className="btn" onClick={() => setDialog('recipe')}>Recipe</button>
          </div>
        </div>
      </div>

      <Section id="s-seasons" title="Seasons" count={seasons.length}>
        {seasons.length === 0 ? <p className="muted" style={{ margin: 0 }}>No episodes found for this show.</p> : (
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(min(220px, 100%), 1fr))', gap: 16 }}>
            {seasons.map((s) => <SeasonTile key={s} to={`${showTo}/season/${s}`} season={s} eps={eps.filter((e) => e.season === s)} />)}
          </div>
        )}
      </Section>

      <Section id="s-cuts" title="Cuts and playlists for this show" count={timelines.length + ideas.length} end={(
        <span className="row">
          <button type="button" className="btn btn-sm" onClick={() => file.current.click()} disabled={busy}>Import timeline</button>
          <button type="button" className="btn btn-sm" onClick={() => create(BLANK(series.title))} disabled={busy}>New timeline</button>
          <input ref={file} type="file" accept="application/json,.json" hidden onChange={onFile} data-testid="timeline-file" />
        </span>
      )}>
        {tls.error && <ErrorBox error={tls.error} onRetry={tls.reload} />}
        {!tls.data && !tls.error && <Loading />}
        {tls.data && timelines.length === 0 && ideas.length === 0 && (
          <p className="muted" style={{ margin: 0 }}>No timelines yet. A timeline is a reordered or merged cut of this show, kept as a recipe. Import one or start a new one.</p>
        )}
        {timelines.length + ideas.length > 0 && (
          <div className="shelf">
            {timelines.map((t) => (
              <div key={t.id} style={{ flex: '0 0 164px', display: 'flex', flexDirection: 'column', gap: 6 }}>
                <CutTile to={`/cuts/${enc(t.id)}`} title={t.title} sub={series.title} tag={cutKind(t)} kind="order"
                  meta={`${cutKind(t)} · ${plural(t.parts, 'part')} · ${plural(t.sources, 'source')}`}
                  status={t.synced ? 'ready' : undefined} note={t.synced ? 'Synced' : 'Not synced yet'} />
                <span className="row" style={{ gap: 4 }}>
                  <button type="button" className="btn btn-sm btn-ghost" onClick={() => exportTl(t)} aria-label={`Export ${t.title}`}>Export</button>
                  <button type="button" className="btn btn-sm btn-ghost" onClick={() => setDialog({ del: t })} aria-label={`Delete ${t.title}`}>Delete</button>
                </span>
              </div>
            ))}
            {ideas.map((x) => <SuggestionTile key={x.id} s={x} onOpen={setIdea} />)}
          </div>
        )}
      </Section>
      {idea && <SuggestionDialog s={idea} onClose={() => setIdea(null)} />}

      {also.data?.films?.length > 0 && (
        <Section id="s-also" title="Also in your library" count={also.data.films.length}
          end={<Link to={`/collections/${enc(also.data.name)}`} className="tiny">Open {also.data.name}</Link>}>
          <div className="shelf">{also.data.films.map((f) => <FilmTile key={f.key} m={f} />)}</div>
        </Section>
      )}

      {dialog === 'match' && <MatchDialog series={series} matches={matches} onClose={() => setDialog(null)} onChanged={view.reload} />}
      {dialog === 'recipe' && <RecipeDialog series={series} onClose={() => setDialog(null)} onImported={view.reload} />}
      {dialog?.del && (
        <Dialog title="Delete timeline" onClose={() => setDialog(null)}>
          <p style={{ margin: 0 }}>Delete “{dialog.del.title}”? Rendered files stay; the recipe and its sync data go.</p>
          <div className="row" style={{ justifyContent: 'flex-end' }}>
            <button type="button" className="btn" onClick={() => setDialog(null)}>Keep it</button>
            <button type="button" className="btn btn-danger" onClick={() => deleteTl(dialog.del)} disabled={busy}>Delete</button>
          </div>
        </Dialog>
      )}
    </div>
  );
}
