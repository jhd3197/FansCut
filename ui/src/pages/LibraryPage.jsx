import { useState } from 'react';
import { Link } from 'react-router';
import { api } from '../lib/api.js';
import { useAct, useLoad } from '../lib/hooks.js';
import { Empty, ErrorBox, Loading, Notice } from '../components/ui.jsx';
import { Backdrop, MediaTile, Search, Section, enc } from '../components/kit.jsx';

async function loadLibrary() {
  const [series, movies, collections] = await Promise.all([
    api('/library/series'), api('/library/movies'), api('/library/collections'),
  ]);
  return { series, movies, collections };
}

function plural(n, word) {
  return `${n} ${word}${n === 1 ? '' : 's'}`;
}

function CollectionTile({ c }) {
  return (
    <Link className="tile tile-wide" to={`/collections/${enc(c.name)}`}>
      <Backdrop k={c.backdrops?.[0]} name={c.name} />
      <span className="tiny muted">{plural(c.films, 'film')} · {plural(c.cuts, 'cut')}</span>
    </Link>
  );
}

export function movieTag(m) {
  if (!m.on_disk) return 'NOT ON DISK';
  return (m.edition || 'Movie').toUpperCase();
}

export function LibraryPage() {
  const [q, setQ] = useState('');
  const { data, error, loading, reload } = useLoad(loadLibrary);
  const { act, msg, busy, clear } = useAct();
  const rescan = () => act(async () => { await api('/library/refresh', { method: 'POST' }); await reload(); }, 'Library rescanned');

  const needle = q.trim().toLowerCase();
  const has = (...xs) => !needle || xs.some((x) => x && String(x).toLowerCase().includes(needle));
  const collections = (data?.collections.collections || []).filter((c) => has(c.name));
  const series = (data?.series.series || []).filter((s) => has(s.title, s.year));
  const movies = (data?.movies.movies || []).filter((m) => has(m.title, m.year, ...(m.collections || [])));
  const errors = Object.entries({ ...(data?.series.errors || {}), ...(data?.movies.errors || {}) });
  // series sources only list Sonarr/Plex/folder shows; a movies-only library is still set up
  const noSources = data && !(data.series.sources || []).length && !(data.movies.movies || []).length && !errors.length;
  const nothing = data && !collections.length && !series.length && !movies.length;

  return (
    <div className="page page-wide">
      <div className="toolbar toolbar-end">
        <div className="row wrap">
          <Search value={q} onChange={setQ} label="Search library" />
          <button className="btn" type="button" onClick={rescan} disabled={busy}>{busy ? 'Rescanning…' : 'Rescan'}</button>
        </div>
      </div>

      <Notice msg={msg} onClose={clear} />
      <ErrorBox error={error} onRetry={reload} />
      {errors.map(([src, text]) => (
        <div key={src} className="notice notice-err" role="alert"><span><b>{src}</b>: {text}</span></div>
      ))}
      {loading && !data && <Loading what="Reading your library" />}

      {noSources && (
        <Empty>No library source is set up yet. Add Sonarr, Radarr, Plex or a media folder in <Link to="/settings">Settings</Link>.</Empty>
      )}
      {data && !noSources && nothing && !errors.length && (
        <Empty>{needle ? 'Nothing in your library matches that search.' : 'Your sources answered, but no shows or movies were found.'}</Empty>
      )}

      {collections.length > 0 && (
        <Section id="coll-h" title="Collections">
          <div className="grid grid-wide">
            {collections.map((c) => <CollectionTile key={c.name} c={c} />)}
          </div>
        </Section>
      )}

      {series.length > 0 && (
        <Section id="shows-h" title="Shows">
          <div className="grid">
            {series.map((s) => (
              <MediaTile key={s.key} art={s.art} to={`/series/${enc(s.key)}`} tag={`TV · ${plural(s.episode_count, 'episode')}`}
                title={s.title} year={s.year}
                meta={[s.is_anime ? 'Anime' : null, s.source].filter(Boolean).join(' · ')}
                note={s.is_anime && !s.matched ? 'Not matched to a catalogue yet' : null} noteClass="c-warn" />
            ))}
          </div>
        </Section>
      )}

      {movies.length > 0 && (
        <Section id="movies-h" title="Movies">
          <div className="grid">
            {movies.map((m) => (
              <MediaTile key={m.key} art={m.art} to={`/movies/${enc(m.key)}`} tag={movieTag(m)} title={m.title} year={m.year}
                meta={m.minutes ? `${m.minutes} min` : m.on_disk ? 'Length unknown' : 'No file'}
                note={m.problem} noteClass={m.problem_severity === 'error' ? 'c-danger' : 'c-warn'} />
            ))}
          </div>
        </Section>
      )}
    </div>
  );
}
