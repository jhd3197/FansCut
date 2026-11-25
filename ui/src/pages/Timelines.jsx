import { useRef } from 'react';
import { Link, useNavigate, useParams } from 'react-router';
import { api, seriesPath } from '../lib/api.js';
import { useAct, useLoad } from '../lib/hooks.js';
import { Empty, ErrorBox, Loading, Notice, PageHeader } from '../components/ui.jsx';
import { downloadJson, recipeFileName } from '../components/Recipe.jsx';

const BLANK = (title) => ({
  schema: 2, kind: 'timeline', title: `${title} — Timeline`,
  series: { title }, sources: {}, parts: [{ title: 'Part 1', segments: [] }],
});

export function Timelines() {
  const { key } = useParams();
  const base = seriesPath(key);
  const { data, error, loading, reload } = useLoad(() => api(`${base}/timelines`), [key]);
  const series = useLoad(() => api(`${base}/episodes`), [key]);
  const { act, msg, busy, clear } = useAct();
  const file = useRef(null);
  const nav = useNavigate();

  if (error && !data) return <ErrorBox error={error} onRetry={reload} />;
  if (loading || !data || !series.data) return <Loading />;
  const title = series.data.series.title;

  const create = (doc) => act(async () => {
    const r = await api(`${base}/timelines`, { method: 'POST', body: doc });
    nav(`/series/${encodeURIComponent(key)}/timelines/${r.id}`);
    return r;
  });
  const onFile = async (e) => {
    const f = e.target.files[0];
    e.target.value = '';
    if (!f) return;
    let doc;
    try { doc = JSON.parse(await f.text()); } catch { await act(async () => { throw new Error('not valid JSON'); }); return; }
    await create(doc);
  };

  return (
    <>
      <PageHeader title={`Timelines · ${title}`} sub="Chronological edits and other reorderings, as media-free recipes.">
        <Link className="btn" to={`/series/${encodeURIComponent(key)}`}>← Episodes</Link>
        <button className="btn" onClick={() => file.current.click()}>Import timeline</button>
        <button className="btn btn-primary" disabled={busy} onClick={() => create(BLANK(title))}>New timeline</button>
        <input ref={file} type="file" accept="application/json,.json" hidden onChange={onFile} data-testid="timeline-file" />
      </PageHeader>
      <Notice msg={msg} onClose={clear} />
      {data.timelines.length === 0 && <Empty>No timelines yet. Import one or start a new one.</Empty>}
      <div className="cards">
        {data.timelines.map((t) => (
          <div key={t.id} className="card stack">
            <b>{t.title}</b>
            <span className="muted tiny">{t.parts} part(s) · {t.sources} source(s) · {t.synced ? 'synced' : 'not synced'}</span>
            <div className="row">
              <Link className="btn btn-sm" to={`/series/${encodeURIComponent(key)}/timelines/${t.id}`}>Open</Link>
              <button className="btn btn-sm" onClick={() => act(async () => downloadJson(recipeFileName(t.title), await api(`/timelines/${t.id}/export?anchors=none`)))}>Export</button>
              <button className="btn btn-sm btn-ghost" onClick={() => act(async () => { await api(`/timelines/${t.id}`, { method: 'DELETE' }); await reload(); }, 'Deleted')}>Delete</button>
            </div>
          </div>
        ))}
      </div>
    </>
  );
}
