// A cut (timeline recipe) as a page: what it is, what it needs, what's in it.
import { useState } from 'react';
import { Link, useParams } from 'react-router';
import { api } from '../lib/api.js';
import { useLoad } from '../lib/hooks.js';
import { fmtTime } from '../lib/time.js';
import { Crumbs, enc, hm, KV, Poster, SRC_COLOURS } from '../components/kit.jsx';
import { ErrorBox, Loading } from '../components/ui.jsx';

const CREDIT_LABELS = {
  author: 'Made by', based_on: 'Based on', repo: 'Repository', repository: 'Repository',
  url: 'Link', version: 'Version', license: 'License',
};
const NOT_KV = new Set(['description', 'summary', 'about']);

// Poster motif from the recipe's category, falling back to its shape.
export function posterKind(doc) {
  const c = (doc.category || '').toLowerCase();
  if (c.includes('merge')) return 'merge';
  if (c.includes('canon') || c.includes('anime')) return 'canon';
  if (c.includes('order')) return 'order';
  if (c.includes('character')) return 'character';
  const movies = Object.values(doc.sources || {}).filter((s) => s.kind === 'movie').length;
  return movies > 1 && doc.parts.length === 1 ? 'merge' : 'supercut';
}

// "github.com/owner/repo" or a full URL -> a link; anything else -> null.
export function repoUrl(credits = {}) {
  const raw = credits.repo || credits.repository || credits.url || '';
  if (/^https?:\/\//.test(raw)) return raw;
  if (/^github\.com\//.test(raw)) return `https://${raw}`;
  if (/^[\w.-]+\/[\w.-]+$/.test(raw)) return `https://github.com/${raw}`;
  return null;
}

const repoShort = (url) => url?.replace(/^https?:\/\/(www\.)?/, '').replace(/^github\.com\//, '');

// A segment is on unless its `when` names a toggle that's off (or "!toggle" that's on).
export function segActive(seg, values) {
  const w = seg.when;
  if (!w) return true;
  return w.startsWith('!') ? !values?.[w.slice(1)] : Boolean(values?.[w]);
}

const srcName = (doc, src) => doc.sources[src]?.note || src;

function SourceList({ doc, missing, report, movieKeys, seriesKey }) {
  const missingSet = new Set(missing.map((m) => m.src));
  const order = [];
  for (const p of doc.parts) for (const s of p.segments) if (!order.includes(s.src)) order.push(s.src);
  for (const src of Object.keys(doc.sources)) if (!order.includes(src)) order.push(src);
  const movies = order.every((s) => doc.sources[s]?.kind === 'movie');
  return (
    <section aria-labelledby="c-src" className="card" style={{ flex: '1 1 420px', overflow: 'hidden', padding: 0 }}>
      <h2 id="c-src" style={{ padding: '14px 16px' }}>Uses these {movies ? 'films' : 'episodes'}</h2>
      <ul style={{ listStyle: 'none', margin: 0, padding: 0 }}>
        {order.map((src) => {
          const s = doc.sources[src] || {};
          const have = !missingSet.has(src);
          const sync = report?.sources?.[src];
          const [cls, label] = have ? ['c-ready', 'Owned'] : s.optional ? ['c-accent', 'Optional'] : ['c-danger', 'Missing'];
          const movieKey = s.kind === 'movie' ? movieKeys[`${s.tmdb}|${s.edition || ''}`] || movieKeys[`${s.tmdb}|`] : null;
          const to = movieKey ? `/movies/${enc(movieKey)}`
            : s.kind !== 'movie' && have ? `/series/${enc(seriesKey)}/episode/${enc(src)}` : null;
          const name = srcName(doc, src);
          const facts = [s.edition, s.duration ? fmtTime(s.duration) : null].filter(Boolean).join(' · ');
          return (
            <li key={src} style={{ display: 'grid', gridTemplateColumns: '64px minmax(0, 1fr) auto', gap: 12, padding: '10px 16px', borderTop: '1px solid var(--raised)', alignItems: 'center' }}>
              <span className={`tiny ${cls}`}>{label}</span>
              <span style={{ display: 'flex', flexDirection: 'column', minWidth: 0 }}>
                {to ? <Link to={to} style={{ color: 'var(--text)' }}>{name}</Link> : <span>{name}</span>}
                {name !== src && <span className="mono tiny dim">{src}</span>}
                {have && sync && !sync.matched && <span className="tiny c-warn">Cut points not found in your copy{sync.reason ? `: ${sync.reason}` : ''}</span>}
                {!have && s.optional && <span className="tiny muted">Only needed for the variants that use it</span>}
              </span>
              {!have && movieKey
                ? <Link to={`/movies/${enc(movieKey)}`} className="tiny">Check file</Link>
                : <span className="mono tiny muted">{facts}</span>}
            </li>
          );
        })}
      </ul>
    </section>
  );
}

function Segments({ doc, values, report }) {
  const colour = {};
  let n = 0;
  for (const p of doc.parts) for (const s of p.segments) if (!(s.src in colour)) colour[s.src] = SRC_COLOURS[n++ % SRC_COLOURS.length];
  const status = {};
  for (const src of Object.values(report?.sources || {})) Object.assign(status, src.segments || {});
  let i = 0;
  let total = 0;
  let count = 0;
  const rows = [];
  doc.parts.forEach((p, pi) => {
    if (doc.parts.length > 1) {
      rows.push(
        <li key={`p${pi}`} style={{ padding: '10px 16px 6px', borderTop: '1px solid var(--raised)' }} className="tiny muted">
          Part {pi + 1} · {p.title}
        </li>,
      );
    }
    p.segments.forEach((s, si) => {
      i += 1;
      const on = segActive(s, values);
      const gone = status[`${pi + 1}:${si + 1}`] === 'missing';
      const dur = s.end - s.start;
      if (on && !gone) { total += dur; count += 1; }
      rows.push(
        <li key={`${pi}:${si}`} style={{ display: 'grid', gridTemplateColumns: '28px 64px minmax(0, 1fr) 80px', gap: 12, alignItems: 'center', padding: '9px 16px', borderTop: '1px solid var(--raised)', opacity: on && !gone ? 1 : 0.5 }}>
          <span className="mono tiny muted">{i}</span>
          <span className="mono tiny" style={{ color: colour[s.src], overflow: 'hidden', textOverflow: 'ellipsis' }}>{s.src}</span>
          <span style={{ minWidth: 0 }}>
            {s.desc || <span className="muted">{fmtTime(s.start)} – {fmtTime(s.end)}</span>}
            {!on && <span className="tiny muted"> · off in this variant</span>}
            {gone && <span className="tiny c-warn"> · not in your edition</span>}
          </span>
          <span className="mono tiny muted" style={{ textAlign: 'right' }}>{fmtTime(dur)}</span>
        </li>,
      );
    });
  });
  return (
    <section aria-labelledby="c-in" className="card" style={{ flex: '1 1 420px', overflow: 'hidden', padding: 0 }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline', padding: '14px 16px' }}>
        <h2 id="c-in">What’s in it</h2>
        <span className="mono tiny muted">{count} segment{count === 1 ? '' : 's'} · {fmtTime(total)}</span>
      </div>
      <ol style={{ listStyle: 'none', margin: 0, padding: 0 }}>{rows}</ol>
    </section>
  );
}

function Variants({ view, value, onChange }) {
  const rows = view?.variants || [];
  const offToggles = (view?.toggles || []).filter((t) => !t.enabled);
  if (rows.length < 2 && !offToggles.length) return null;
  return (
    <fieldset>
      <legend>Variant</legend>
      {rows.length > 1 && (
        <div style={{ display: 'flex', flexWrap: 'wrap', gap: 10 }}>
          {rows.map((v) => (
            <label key={v.id || 'default'} className="choice" style={{ flex: '1 1 240px' }}>
              <input type="radio" name="variant" checked={value === v.id} onChange={() => onChange(v.id)} style={{ margin: '3px 0 0' }} />
              <span style={{ display: 'flex', flexDirection: 'column', gap: 2 }}>
                <b style={{ fontWeight: 600 }}>{v.label}</b>
                <span className="tiny muted">{hm(v.runtime / 60)}</span>
                {v.blocked.map((b) => (
                  <span key={b.id} className="tiny c-danger">{b.label}: {b.reason}</span>
                ))}
              </span>
            </label>
          ))}
        </div>
      )}
      {offToggles.length > 0 && (
        <ul className="tiny muted" style={{ margin: 0, paddingLeft: 18 }}>
          {offToggles.map((t) => <li key={t.id}>{t.label} is off: {t.reason}</li>)}
        </ul>
      )}
    </fieldset>
  );
}

export function CutDetail() {
  const { id } = useParams();
  const tl = useLoad(() => api(`/timelines/${enc(id)}`), [id]);
  const variants = useLoad(() => api(`/timelines/${enc(id)}/variants`), [id]);
  const sync = useLoad(() => api(`/timelines/${enc(id)}/sync`), [id]);
  const hasMovies = Object.values(tl.data?.doc?.sources || {}).some((s) => s.kind === 'movie');
  const movies = useLoad(() => (hasMovies ? api('/library/movies') : Promise.resolve({ movies: [] })), [hasMovies]);
  const [variant, setVariant] = useState('');

  if (tl.error && !tl.data) return <div className="page"><ErrorBox error={tl.error} onRetry={tl.reload} /></div>;
  if (!tl.data) return <div className="page"><Loading /></div>;

  const doc = tl.data.doc;
  const credits = doc.credits || {};
  const url = repoUrl(credits);
  const report = sync.data?.synced ? sync.data : null;
  const rows = variants.data?.variants || [];
  const chosen = rows.find((v) => v.id === variant) || rows[0];
  const runtime = chosen ? chosen.runtime : doc.parts.reduce((a, p) => a + p.segments.reduce((b, s) => b + s.end - s.start, 0), 0);
  const kindLabel = doc.category || (doc.parts.length > 1 ? `${doc.parts.length}-part cut` : 'Cut');
  const subtitle = doc.tagline || doc.series?.title || '';
  const meta = [kindLabel, hm(runtime / 60), repoShort(url), credits.version ? `v${credits.version.replace(/^v/, '')}` : null].filter(Boolean).join(' · ');
  const description = credits.description || credits.summary || credits.about;
  const movieKeys = {};
  for (const m of movies.data?.movies || []) {
    if (m.tmdb_id == null) continue;
    movieKeys[`${m.tmdb_id}|${m.edition || ''}`] ??= m.key;
    movieKeys[`${m.tmdb_id}|`] ??= m.key;
  }
  const buildTo = `/cuts/${enc(id)}/build${variant ? `?variant=${enc(variant)}` : ''}`;
  const kv = Object.entries(credits).filter(([k]) => !NOT_KV.has(k)).map(([k, v]) => [
    CREDIT_LABELS[k] || k.replace(/_/g, ' ').replace(/^./, (c) => c.toUpperCase()),
    k === 'repo' || k === 'repository' || k === 'url' ? <span className="mono tiny">{v}</span> : v,
  ]);

  return (
    <div className="page">
      <Crumbs items={[['My cuts', '/my-cuts'], [doc.title]]} />
      <div className="hero">
        <div style={{ width: 240, flex: 'none' }}>
          <Poster title={doc.title} sub={subtitle} tag={doc.category || 'Cut'} kind={posterKind(doc)} />
        </div>
        <div className="hero-body" style={{ gap: 18 }}>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
            <h1 className="display" style={{ font: '700 40px/1 var(--display)' }}>{doc.title}</h1>
            {subtitle && <span style={{ fontSize: 16, color: 'var(--text2)' }}>{subtitle}</span>}
            <span className="mono muted" style={{ fontSize: 13 }}>{meta}</span>
          </div>
          {description && <p style={{ margin: 0, color: 'var(--text2)', maxWidth: 620 }}>{description}</p>}
          <div className="row wrap">
            <Link className="btn btn-primary" to={buildTo}>Build this cut</Link>
            <Link className="btn" to={`/cuts/${enc(id)}/edit`}>Open in editor</Link>
            {url && <a className="btn" href={url} target="_blank" rel="noreferrer">View on GitHub</a>}
          </div>
          <Variants view={variants.data} value={chosen?.id ?? ''} onChange={setVariant} />
          {variants.error && <ErrorBox error={variants.error} onRetry={variants.reload} />}
        </div>
      </div>
      <div style={{ display: 'flex', flexWrap: 'wrap', gap: 24, alignItems: 'flex-start' }}>
        <SourceList doc={doc} missing={tl.data.missing || []} report={report} movieKeys={movieKeys} seriesKey={tl.data.series_key} />
        <Segments doc={doc} values={chosen?.toggles} report={report} />
      </div>
      <section aria-labelledby="c-about" className="section" style={{ gap: 12 }}>
        <h2 id="c-about">About this cut</h2>
        <KV rows={[
          ...kv,
          ['Cut points', report ? `Matched to your files ${report.updated_at ? `on ${report.updated_at.replace('T', ' ')}` : ''}` : 'Not matched to your files yet'],
          ['Contains', 'Timestamps, ordering and fingerprints. No video, audio, subtitles or artwork.'],
        ]} />
      </section>
    </div>
  );
}
