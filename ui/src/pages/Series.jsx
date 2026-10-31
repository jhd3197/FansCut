import { useMemo, useState } from 'react';
import { Link, useParams } from 'react-router';
import { api, seriesPath } from '../lib/api.js';
import { useAct, useLoad } from '../lib/hooks.js';
import { LABELS, PRESET_NAMES } from '../lib/labels.js';
import { TRIM_KINDS, TRIM_NAMES } from '../lib/timeline.js';
import { fmtDuration, fmtTime } from '../lib/time.js';
import { Dialog, Empty, ErrorBox, LabelBadge, Loading, Notice, PageHeader } from '../components/ui.jsx';
import { MatchDialog } from '../components/MatchDialog.jsx';
import { RecipeButtons } from '../components/Recipe.jsx';
import { TrimFields } from '../components/TrimFields.jsx';

const seasonName = (n) => (n === 0 ? 'Specials' : `Season ${n}`);

function TrimChips({ trims }) {
  const shown = TRIM_KINDS.filter((k) => trims?.[k]?.start != null);
  if (!shown.length) return <span className="muted">–</span>;
  return shown.map((k) => (
    <span key={k} className="chip" title={`${TRIM_NAMES[k]} (${trims[k].source})`}>
      {TRIM_NAMES[k]} {fmtTime(trims[k].start)}–{fmtTime(trims[k].end)}
    </span>
  ));
}

export function SeriesPage() {
  const { key } = useParams();
  const base = seriesPath(key);
  const { data, error, loading, reload } = useLoad(() => api(`${base}/episodes`), [key]);
  const { act, msg, busy, clear } = useAct();
  const [season, setSeason] = useState(null);
  const [matching, setMatching] = useState(false);
  const [editing, setEditing] = useState(null); // episode id whose trims are open

  const episodes = useMemo(() => data?.episodes || [], [data]);
  const seasons = useMemo(() => [...new Set(episodes.map((e) => e.season))].sort((a, b) => a - b), [episodes]);
  const current = seasons.includes(season) ? season : (seasons.find((s) => s > 0) ?? seasons[0]);
  const rows = episodes.filter((e) => e.season === current);
  const editEp = episodes.find((e) => e.id === editing);

  if (error && !data) return <ErrorBox error={error} onRetry={reload} />;
  if (loading || !data) return <Loading />;
  const { series, settings, filler_sources: sources } = data;
  const whole = data.matches.find((m) => m.season === -1);
  const src = sources[String(whole ? -1 : current)] || Object.values(sources)[0];

  const call = (path, method, body, ok) => act(async () => { await api(`${base}${path}`, { method, body }); await reload(); }, ok);
  const setLabel = (id, label) => call(`/episodes/${id}/override`, 'PUT', { label: label || null });
  const setInclude = (id, include) => call(`/episodes/${id}/override`, 'PUT', { include });
  const setPreset = (preset) => call('/settings', 'PUT', { preset });
  const saveTrim = (id) => (kind, start, end) => call(`/episodes/${id}/trims/${kind}`, 'PUT', { start, end });
  const resetTrim = (id) => (kind) => call(`/episodes/${id}/trims/${kind}`, 'DELETE');
  const autoMatch = () => act(async () => {
    const r = await api(`${base}/match/auto`, { method: 'POST' });
    await reload();
    return r;
  }, (r) => (r.matched ? `Matched to “${r.candidate.title}”` : 'No confident match found; pick one by hand'));
  const detect = () => act(() => api(`${base}/detect-trims`, { method: 'POST', body: { season: current } }),
    'OP/ED detection queued; check Jobs, then reload this page');

  return (
    <>
      <PageHeader title={series.title} sub={`${series.source} · ${series.episode_count} episodes${series.year ? ` · ${series.year}` : ''}`}>
        <select className="input" value={settings.preset} onChange={(e) => setPreset(e.target.value)} aria-label="Preset">
          {Object.entries(PRESET_NAMES).map(([v, n]) => <option key={v} value={v}>{n}</option>)}
        </select>
        <Link className="btn btn-primary" to={`/series/${encodeURIComponent(key)}/cut`}>Build cut →</Link>
      </PageHeader>

      <div className="card row wrap" style={{ marginBottom: 14 }}>
        <div className="grow">
          <b>Catalogue match: </b>
          {data.matches.length === 0 ? <span className="muted">none yet (labels fall back to “unknown”)</span> : data.matches.map((m) => (
            <span key={m.season} className="chip">{m.season === -1 ? 'all' : `S${m.season}`} · MAL {m.mal_id ?? '–'} · AniList {m.anilist_id ?? '–'}</span>
          ))}
          <div className="muted tiny">
            Label data: {src ? [src.anifiller && 'AniFiller', src.jikan && 'Jikan'].filter(Boolean).join(' + ') || 'none found' : 'none'}
          </div>
        </div>
        <button className="btn" onClick={autoMatch} disabled={busy}>Auto-match</button>
        <button className="btn" onClick={() => setMatching(true)}>Match to MAL / AniList…</button>
        <RecipeButtons series={series} onImported={reload} />
      </div>

      <Notice msg={msg} onClose={clear} />

      {episodes.length === 0 ? <Empty>No episodes found for this series.</Empty> : (
        <>
          <div className="row wrap" style={{ justifyContent: 'space-between' }}>
            <div className="tabs" role="tablist" style={{ flex: 1, minWidth: 0 }}>
              {seasons.map((s) => (
                <button key={s} role="tab" className="tab" aria-selected={s === current} onClick={() => setSeason(s)}>{seasonName(s)}</button>
              ))}
            </div>
            <button className="btn btn-sm" onClick={detect} disabled={busy} title="Fingerprint the audio of this season to find OP / ED / recaps">Detect OP/ED</button>
          </div>
          <div className="card scroll-x" style={{ padding: 0 }}>
            <table className="table">
              <thead>
                <tr><th>Ep</th><th>Title</th><th>Label</th><th>Override</th><th>Keep</th><th>Trims</th><th className="num">Runtime</th></tr>
              </thead>
              <tbody>
                {rows.map((e) => (
                  <tr key={e.id} className={e.include ? '' : 'off'}>
                    <td className="mono">{e.id}</td>
                    <td>{e.title || <span className="muted">–</span>}{!e.has_file && <span className="tag" style={{ marginLeft: 6 }}>no file</span>}</td>
                    <td><LabelBadge label={e.label} source={e.label_source} /></td>
                    <td>
                      <select className="input" aria-label={`Label for ${e.id}`} value={e.override?.label || ''} onChange={(ev) => setLabel(e.id, ev.target.value)}>
                        <option value="">auto</option>
                        {LABELS.map((l) => <option key={l} value={l}>{l}</option>)}
                      </select>
                    </td>
                    <td>
                      <label className="check" title={e.include_reason}>
                        <input type="checkbox" checked={e.include} aria-label={`Keep ${e.id}`} onChange={(ev) => setInclude(e.id, ev.target.checked)} />
                        {e.override?.include != null && (
                          <button className="btn btn-ghost btn-sm" title="Back to the preset's choice" onClick={(ev) => { ev.preventDefault(); call(`/episodes/${e.id}/override`, 'PUT', { include: null }); }}>↺</button>
                        )}
                      </label>
                    </td>
                    <td>
                      <TrimChips trims={e.trims} />
                      <button className="btn btn-ghost btn-sm" onClick={() => setEditing(e.id)} aria-label={`Edit trims for ${e.id}`}>edit</button>
                    </td>
                    <td className="num mono">{fmtDuration(e.duration)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}

      {matching && <MatchDialog series={series} matches={data.matches} onClose={() => setMatching(false)} onChanged={reload} />}
      {editEp && (
        <Dialog title={`Trims · ${editEp.id}`} onClose={() => setEditing(null)} wide>
          <p className="muted">Ranges are cut out of the episode. Times are m:ss; edits are kept when detection runs again.</p>
          <TrimFields episode={editEp} save={saveTrim(editEp.id)} reset={resetTrim(editEp.id)} />
        </Dialog>
      )}
    </>
  );
}
