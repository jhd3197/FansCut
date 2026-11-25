import { Link } from 'react-router';
import { api } from '../lib/api.js';
import { useAct, useLoad } from '../lib/hooks.js';
import { Empty, ErrorBox, Loading, Notice, PageHeader } from '../components/ui.jsx';

const KIND_NAMES = {
  recipe: 'community recipe',
  collection_merge: 'merge template',
  watch_order: 'watch order',
  canon_cut: 'canon cut',
};

function StatusBadge({ status }) {
  const cls = status === 'ready' ? 'tag-ok' : status === 'partly' ? 'tag-queued' : 'tag-bad';
  return <span className={`tag ${cls}`}>{status === 'partly' ? 'partly ready' : status}</span>;
}

export function Suggestions() {
  const { data, error, loading, reload } = useLoad(() => api('/suggestions'));
  const { act, msg, busy, clear } = useAct();

  if (error && !data) return <ErrorBox error={error} onRetry={reload} />;
  if (loading || !data) return <Loading />;
  const items = data.suggestions;

  const install = (s) => act(async () => {
    const r = await api(`/suggestions/${encodeURIComponent(s.id)}/install`, { method: 'POST' });
    return r;
  }, (r) => (r.action === 'playlist'
    ? 'Playlist created in Plex — nothing was rendered'
    : 'Sync + render queued; watch the Jobs page'));

  return (
    <>
      <PageHeader title="Suggestions" sub="What your library is one click away from: community recipes, collection merges, watch orders, canon cuts." />
      <Notice msg={msg} onClose={clear} />
      {items.length === 0 && (
        <Empty>No suggestions yet. Drop community recipes in the recipes folder, match a show to
          MAL/AniList, or add movies with Plex collection tags.</Empty>
      )}
      <div className="cards">
        {items.map((s) => (
          <div key={s.id} className="card stack">
            <div className="row">
              <b className="grow">{s.title}</b>
              <StatusBadge status={s.status} />
            </div>
            <span className="muted tiny">{KIND_NAMES[s.kind] || s.kind}
              {s.detail?.template ? ` · ${s.detail.template}` : ''}
              {s.detail?.filler_episodes ? ` · ${s.detail.filler_episodes} filler episode(s)` : ''}
            </span>
            {s.missing.length > 0 && (
              <ul className="muted tiny" style={{ margin: 0 }}>
                {s.missing.slice(0, 5).map((m, i) => <li key={i}><span className="mono">{m.src}</span>: {m.reason}</li>)}
              </ul>
            )}
            <div className="row">
              <button className="btn btn-sm btn-primary" disabled={busy || s.status === 'missing'}
                onClick={() => install(s)}>
                {s.kind === 'watch_order' ? 'Create playlist' : 'Install'}
              </button>
            </div>
          </div>
        ))}
      </div>
      <p className="muted tiny" style={{ marginTop: 14 }}>
        Renders land in your output folder; point a Plex library at it. Renders also get a poster
        and a “FansCut” collection when Plex is configured. Manage files on the <Link to="/jobs">Jobs</Link> page.
      </p>
    </>
  );
}
