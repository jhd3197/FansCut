// A suggestion as its own page (Discover / A Goofy Movie): what it is, what it uses,
// what's missing, and the button that installs it. Same layout as a cut's page.
import { Link, useNavigate, useParams } from 'react-router';
import { api } from '../lib/api.js';
import { useAct, useCrumbRoot, useFrom, usePageTitle } from '../lib/hooks.js';
import { ArtPoster, Crumbs, KV, Poster, enc } from '../components/kit.jsx';
import { ErrorBox, Loading } from '../components/ui.jsx';
import { useApi } from '../lib/store.js';
import { KIND_LABEL, POSTER_KIND, STATUS_CLASS, SUGGESTION_ART, suggestionMeta, suggestionStatus, suggestionWhat } from './Discover.jsx';

function UsesList({ s, movies }) {
  const byKey = new Map((movies || []).map((m) => [m.key, m]));
  const names = s.detail?.movies || [];
  const keys = s.keys || [];
  // collection templates list their films in order; recipes and canon cuts only have keys
  const rows = keys.length ? keys.map((k, i) => {
    const m = byKey.get(k);
    if (m) return { key: k, name: m.title, sub: [m.year, m.edition].filter(Boolean).join(' · '), to: `/movies/${enc(k)}` };
    if (s.kind === 'canon_cut') return { key: k, name: s.name || s.title, sub: 'Show', to: `/series/${enc(k)}` };
    return { key: k, name: names[i] || k, sub: '', to: null };
  }) : names.map((n) => ({ key: n, name: n, sub: '', to: null }));
  const missing = s.missing || [];
  if (!rows.length && !missing.length) return null;
  return (
    <section aria-labelledby="s-uses" className="card" style={{ flex: '1 1 420px', overflow: 'hidden', padding: 0 }}>
      <h2 id="s-uses" style={{ padding: '14px 16px' }}>{s.kind === 'canon_cut' ? 'Uses this show' : 'Uses these films'}</h2>
      <ul style={{ listStyle: 'none', margin: 0, padding: 0 }}>
        {rows.map((r) => (
          <li key={r.key} style={{ display: 'grid', gridTemplateColumns: '64px minmax(0, 1fr)', gap: 12, padding: '10px 16px', borderTop: '1px solid var(--raised)', alignItems: 'center' }}>
            <span className="tiny c-ready">Owned</span>
            <span style={{ display: 'flex', flexDirection: 'column', minWidth: 0 }}>
              {r.to ? <Link to={r.to} style={{ color: 'var(--text)' }}>{r.name}</Link> : <span>{r.name}</span>}
              {r.sub && <span className="tiny muted">{r.sub}</span>}
            </span>
          </li>
        ))}
        {missing.map((m, i) => (
          <li key={`m${i}`} style={{ display: 'grid', gridTemplateColumns: '64px minmax(0, 1fr)', gap: 12, padding: '10px 16px', borderTop: '1px solid var(--raised)', alignItems: 'center' }}>
            <span className={`tiny ${/audio/.test(m.reason) ? 'c-warn' : 'c-danger'}`}>{/audio/.test(m.reason) ? 'Check' : 'Missing'}</span>
            <span style={{ display: 'flex', flexDirection: 'column', minWidth: 0 }}>
              <span className="mono">{m.src}</span>
              <span className="tiny muted">{m.reason}</span>
            </span>
          </li>
        ))}
      </ul>
      {missing.length > 0 && (
        <p className="tiny" style={{ margin: 0, padding: '10px 16px', borderTop: '1px solid var(--raised)' }}>
          {s.status === 'partly' && <span className="muted">Installing still works; the missing pieces are skipped or use another audio track. </span>}
          <Link to="/health">See what’s wrong in Health</Link>
        </p>
      )}
    </section>
  );
}

export function SuggestionPage() {
  const { id } = useParams();
  const sugg = useApi('/suggestions');
  const cat = useApi('/catalogue', { quiet: true });
  const cuts = useApi('/cuts', { quiet: true });
  const movies = useApi('/library/movies', { quiet: true });
  const crumb = useCrumbRoot(['Discover', '/']);
  const from = useFrom();
  const navigate = useNavigate();
  const { act, busy } = useAct();
  const s = (sugg.data?.suggestions || []).find((x) => x.id === id)
    || (cat.data?.cuts || []).map((c) => ({ ...c, from_catalogue: true })).find((x) => x.id === id);
  usePageTitle(s ? s.name || s.title : null);

  if (sugg.error && !sugg.data) return <div className="page"><ErrorBox error={sugg.error} onRetry={sugg.reload} /></div>;
  if (!s) {
    if (!sugg.data || (cat.loading && !cat.data)) return <div className="page"><Loading /></div>;
    return (
      <div className="page">
        <Crumbs items={[crumb.root, ['Not found']]} />
        <p className="muted">This suggestion isn’t offered any more. Your library may have changed. <Link to="/">Back to Discover</Link></p>
      </div>
    );
  }

  const title = s.name || s.title;
  const isPlaylist = s.kind === 'watch_order';
  const installed = (cuts.data?.cuts || []).find((c) => c.title === s.title);
  const [layout] = SUGGESTION_ART[s.kind] || ['single'];
  const meta = [KIND_LABEL[s.kind] || s.kind, suggestionMeta(s), s.category].filter(Boolean).join(' · ');
  const d = s.detail || {};
  const credits = d.credits && Object.keys(d.credits).length > 0
    ? Object.entries(d.credits).map(([k, v]) => [k.replace(/_/g, ' ').replace(/^./, (c) => c.toUpperCase()), Array.isArray(v) ? v.join(', ') : v])
    : [];

  const install = async () => {
    const repo = s.from_catalogue ? s.url || s.repo : null;
    const res = await act(() => (repo
      ? api('/cuts/install', { method: 'POST', body: { url: repo } })
      : api(`/suggestions/${enc(s.id)}/install`, { method: 'POST' })),
    (r) => (r?.action === 'playlist' ? 'Playlist created in Plex. Nothing was rendered.' : 'Queued. Watch the Jobs page.'));
    if (!res || res.action === 'playlist') { cuts.reload(); return; }
    const tid = res.timeline || (repo ? res.id : null);
    navigate(tid ? `/cuts/${enc(tid)}` : '/jobs', { state: from });
  };

  const drawn = <Poster title={title} sub={s.tagline} kind={POSTER_KIND[s.kind] || 'supercut'} bare />;
  return (
    <div className="page">
      <Crumbs items={[crumb.root, [title]]} />
      <div className="hero">
        <div style={{ width: 240, flex: 'none' }}>
          {s.art?.length ? <ArtPoster keys={s.art} layout={layout} fallback={drawn} /> : drawn}
        </div>
        <div className="hero-body" style={{ gap: 18 }}>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
            <h1 className="display" style={{ font: '700 40px/1 var(--display)' }}>{s.title}</h1>
            {s.tagline && <span style={{ fontSize: 16, color: 'var(--text2)' }}>{s.tagline}</span>}
            <span className="mono muted" style={{ fontSize: 13 }}>{meta}</span>
          </div>
          {suggestionWhat(s) && <p style={{ margin: 0, color: 'var(--text2)', maxWidth: 620 }}>{suggestionWhat(s)}</p>}
          <span className={STATUS_CLASS[s.status] || 'muted'} style={{ fontSize: 14 }}>{suggestionStatus(s)}</span>
          <div className="row wrap">
            {installed && !isPlaylist
              ? <Link className="btn btn-primary" to={`/cuts/${enc(installed.id)}`} state={from}>Open your cut</Link>
              : (
                <button type="button" className="btn btn-primary" disabled={busy || s.status === 'missing'} onClick={install}>
                  {busy ? 'Working…' : isPlaylist ? 'Create playlist in Plex' : 'Install this cut'}
                </button>
              )}
            {(s.url || s.repo) && <a className="btn" href={s.url || `https://github.com/${s.repo}`} target="_blank" rel="noreferrer">View on GitHub</a>}
          </div>
          {s.status === 'missing' && <span className="tiny muted">Add the missing pieces to your library, then rescan it.</span>}
        </div>
      </div>
      <div style={{ display: 'flex', flexWrap: 'wrap', gap: 24, alignItems: 'flex-start' }}>
        <UsesList s={s} movies={movies.data?.movies} />
      </div>
      <section aria-labelledby="s-about" className="section" style={{ gap: 12 }}>
        <h2 id="s-about">About this {isPlaylist ? 'playlist' : 'cut'}</h2>
        <KV rows={[
          ['Type', KIND_LABEL[s.kind] || s.kind],
          d.movies?.length > 0 && ['Order', d.movies.join(' → ')],
          ...credits,
          ['What happens', isPlaylist
            ? 'FansCut creates a playlist in Plex with these films in order. Nothing is rendered or copied.'
            : 'FansCut matches the cut points to your own files, then builds a new file into your output folder. Nothing is downloaded.'],
        ]} />
      </section>
    </div>
  );
}
