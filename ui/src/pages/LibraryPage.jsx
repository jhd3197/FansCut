import { Link } from 'react-router';
import { api } from '../lib/api.js';
import { useAct, useHashScroll, useLoad, useUrlState, useDebounced } from '../lib/hooks.js';
import { Empty, ErrorBox, Loading, Notice, SkeletonTiles } from '../components/ui.jsx';
import { Backdrop, MediaTile, Search, Section, Shelf, enc } from '../components/kit.jsx';

async function loadLibrary() {
  const [series, movies, collections] = await Promise.all([
    api('/library/series'), api('/library/movies'), api('/library/collections'),
  ]);
  return { series, movies, collections };
}

// "4 seasons · 87 of 89 episodes"
function showMeta(s) {
  const eps = s.episode_total > s.episode_count ? `${s.episode_count} of ${plural(s.episode_total, 'episode')}` : plural(s.episode_count, 'episode');
  return [s.season_count ? plural(s.season_count, 'season') : null, eps].filter(Boolean).join(' · ');
}

function plural(n, word) {
  return `${n} ${word}${n === 1 ? '' : 's'}`;
}

function CollectionTile({ c }) {
  return (
    <Link className="tile tile-wide" to={`/collections/${enc(c.id || c.name)}`}>
      <Backdrop k={c.backdrops?.[0]} name={c.name} />
      <span className="tiny muted">{[plural(c.films, 'film'), c.shows?.length ? plural(c.shows.length, 'show') : null, plural(c.cuts, 'cut')].filter(Boolean).join(' · ')}</span>
    </Link>
  );
}

// "4K HDR10" from cached probe facts; null when unknown.
export function quality(v) {
  return v ? [v.res, v.hdr].filter(Boolean).join(' ') : null;
}

// Caption facts for a film: "182 min · 4K HDR10 · HEVC", or "Not analysed" for a copy
// FansCut never probed (opening the film probes it).
export function movieMeta(m) {
  const len = m.minutes ? `${m.minutes} min` : m.on_disk ? 'Length unknown' : 'No file';
  if (!m.on_disk || !('video' in m)) return len;
  if (!m.video) return `${len} · Not analysed`;
  return [len, quality(m.video), m.video.codec].filter(Boolean).join(' · ');
}

// The tooltip label (never drawn on the art): "EXTENDED · 4K HDR10".
export function movieTag(m) {
  if (!m.on_disk) return 'NOT ON DISK';
  return [m.edition || 'Movie', quality(m.video)].filter(Boolean).join(' · ').toUpperCase();
}

// A film's note: a problem first, then which cut uses this copy, then other versions.
export function movieNote(m) {
  if (m.problem) return [m.problem, m.problem_severity === 'error' ? 'c-danger' : 'c-warn'];
  if (m.source_for?.length) return [`Source for ${m.source_for.join(', ')}`, 'muted'];
  if (m.versions?.length > 1) return [`${m.versions.length} versions: ${m.versions.map((v) => v.edition || 'unnamed').join(', ')}`, 'muted'];
  return [null, 'muted'];
}

export function LibraryPage() {
  const [q, setQ] = useUrlState('q');
  const dq = useDebounced(q);
  const { data, error, loading, reload } = useLoad(loadLibrary);
  const { act, msg, busy, clear } = useAct();
  const rescan = () => act(async () => { await api('/library/refresh', { method: 'POST' }); await reload(); }, 'Library rescanned');

  const needle = dq.trim().toLowerCase();
  const has = (...xs) => !needle || xs.some((x) => x && String(x).toLowerCase().includes(needle));
  const collections = (data?.collections.collections || []).filter((c) => has(c.name));
  const series = (data?.series.series || []).filter((s) => has(s.title, s.year));
  // other copies of a film ride on its first copy's tile (see movieNote)
  const movies = (data?.movies.movies || []).filter((m) => !m.version_of && has(m.title, m.year, ...(m.collections || [])));
  const errors = Object.entries({ ...(data?.series.errors || {}), ...(data?.movies.errors || {}) });
  // series sources only list Sonarr/Plex/folder shows; a movies-only library is still set up
  const noSources = data && !(data.series.sources || []).length && !(data.movies.movies || []).length && !errors.length;
  const nothing = data && !collections.length && !series.length && !movies.length;
  useHashScroll(!!data);

  return (
    <div className="page page-wide">
      <div className="toolbar toolbar-end">
        <div className="row wrap">
          <Search value={q} onChange={setQ} label="Search library" />
          <Link className="btn" to="/library/tags">Tags</Link>
          <button className="btn" type="button" onClick={rescan} disabled={busy}>{busy ? 'Rescanning…' : 'Rescan'}</button>
        </div>
      </div>

      <Notice msg={msg} onClose={clear} />
      <ErrorBox error={error} onRetry={reload} />
      {errors.map(([src, text]) => (
        <div key={src} className="notice notice-err" role="alert"><span><b>{src}</b>: {text}</span></div>
      ))}
      {loading && !data && <SkeletonTiles n={12} />}

      {noSources && (
        <Empty>No library source is set up yet. Add Sonarr, Radarr, Plex or a media folder in <Link to="/settings">Settings</Link>.</Empty>
      )}
      {data && !noSources && nothing && !errors.length && (
        <Empty>{needle ? 'Nothing in your library matches that search.' : 'Your sources answered, but no shows or movies were found.'}</Empty>
      )}

      {collections.length > 0 && (
        <Shelf id="coll-h" title="Collections" count={collections.length} wide>
          {collections.map((c) => <CollectionTile key={c.name} c={c} />)}
        </Shelf>
      )}

      {series.length > 0 && (
        <Section id="shows-h" title="Shows">
          <div className="grid">
            {series.map((s) => (
              <MediaTile key={s.key} art={s.art} to={`/series/${enc(s.key)}`}
                tag={['TV', s.season_count ? plural(s.season_count, 'season') : null, s.is_anime ? 'Anime' : null].filter(Boolean).join(' · ')}
                title={s.title} year={s.year} meta={showMeta(s)}
                note={s.audio_note || (s.is_anime && !s.matched ? 'Not matched to a catalogue yet' : null)} noteClass="c-warn" />
            ))}
          </div>
        </Section>
      )}

      {movies.length > 0 && (
        <Section id="movies-h" title="Movies">
          <div className="grid">
            {movies.map((m) => (
              <MediaTile key={m.key} art={m.art} to={`/movies/${enc(m.key)}`} tag={movieTag(m)} title={m.title} year={m.year}
                meta={movieMeta(m)} note={movieNote(m)[0]} noteClass={movieNote(m)[1]} />
            ))}
          </div>
        </Section>
      )}
    </div>
  );
}
