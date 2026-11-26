// Pick the MAL / AniList entry a show belongs to (or type the ids in by hand), as a page:
// /series/<key>/match. Saving goes back to the show.
import { useEffect, useState } from 'react';
import { Link, useNavigate, useParams } from 'react-router';
import { api, seriesPath } from '../lib/api.js';
import { useAct, usePageTitle } from '../lib/hooks.js';
import { Crumbs, enc } from '../components/kit.jsx';
import { ErrorBox, Loading, Notice } from '../components/ui.jsx';
import { useApi } from '../lib/store.js';

export function MatchPage() {
  const { key } = useParams();
  const view = useApi(`${seriesPath(key)}/episodes`);
  usePageTitle(view.data ? `Match ${view.data.series.title}` : 'Match');
  if (view.error && !view.data) return <div className="page"><ErrorBox error={view.error} onRetry={view.reload} /></div>;
  if (!view.data) return <div className="page"><Loading /></div>;
  return <Matcher series={view.data.series} matches={view.data.matches} onChanged={view.reload} />;
}

function Matcher({ series, matches, onChanged }) {
  const nav = useNavigate();
  const showTo = `/series/${enc(series.key)}`;
  const onClose = () => nav(showTo);
  const [q, setQ] = useState(series.title);
  const [cands, setCands] = useState(null);
  const [ids, setIds] = useState({ mal: '', anilist: '' });
  const { act, msg, busy } = useAct();
  const base = seriesPath(series.key);
  const current = matches.find((m) => m.season === -1);

  const search = (text) => act(async () => {
    setCands(null);
    const r = await api(`${base}/match/candidates?q=${encodeURIComponent(text)}`);
    setCands(r.candidates);
  });
  useEffect(() => { search(series.title); /* once, on open */ }, []); // eslint-disable-line react-hooks/exhaustive-deps

  const pick = (body) => act(async () => {
    await api(`${base}/match`, { method: 'PUT', body });
    onChanged();
    onClose();
  }, 'Matched');
  const num = (s) => (s.trim() && /^\d+$/.test(s.trim()) ? Number(s) : null);

  return (
    <div className="page" style={{ maxWidth: 860 }}>
      <Crumbs items={[['Library', '/library'], [series.title, showTo], ['Match']]} />
      <div className="page-head"><div><h1>Match “{series.title}”</h1></div>
        <Link className="btn btn-ghost" to={showTo}>Back to the show</Link></div>
      <p className="muted" style={{ margin: 0 }}>
        Filler data is looked up by MAL / AniList id. {current && <>Currently MAL {current.mal_id ?? '–'} · AniList {current.anilist_id ?? '–'} ({current.source}).</>}
      </p>
      <form className="row" onSubmit={(e) => { e.preventDefault(); search(q); }}>
        <input className="input grow" value={q} onChange={(e) => setQ(e.target.value)} aria-label="Search title" />
        <button className="btn btn-primary" disabled={busy}>Search</button>
      </form>
      <Notice msg={msg} />
      <div className="cands">
        {cands === null && !msg && <p className="muted">Searching…</p>}
        {cands && cands.length === 0 && <p className="muted">Nothing found (offline mode, or no results). Enter ids below.</p>}
        {cands && cands.map((c, i) => (
          <div key={`${c.mal_id}-${c.anilist_id}-${i}`} className="cand">
            <div className="grow">
              <strong>{c.title}</strong>
              <div className="muted tiny">
                {[c.year, c.episodes && `${c.episodes} eps`, c.mal_id && `MAL ${c.mal_id}`, c.anilist_id && `AniList ${c.anilist_id}`, c.source].filter(Boolean).join(' · ')}
              </div>
            </div>
            <span className="tag">{Math.round((c.score || 0) * 100)}%</span>
            <button className="btn btn-sm" disabled={busy}
              onClick={() => pick({ mal_id: c.mal_id ?? null, anilist_id: c.anilist_id ?? null, title: c.title })}>Use</button>
          </div>
        ))}
      </div>
      <div className="row wrap manual">
        <label className="field">MAL id<input className="input" inputMode="numeric" value={ids.mal} onChange={(e) => setIds({ ...ids, mal: e.target.value })} /></label>
        <label className="field">AniList id<input className="input" inputMode="numeric" value={ids.anilist} onChange={(e) => setIds({ ...ids, anilist: e.target.value })} /></label>
        <button className="btn" disabled={busy || (num(ids.mal) == null && num(ids.anilist) == null)}
          onClick={() => pick({ mal_id: num(ids.mal), anilist_id: num(ids.anilist), title: series.title })}>Save ids</button>
        {current && <button className="btn btn-danger" disabled={busy}
          onClick={() => act(async () => { await api(`${base}/match`, { method: 'DELETE' }); onChanged(); onClose(); })}>Clear match</button>}
      </div>
    </div>
  );
}
