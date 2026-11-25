import { useCallback, useMemo, useState } from 'react';
import { Link, useNavigate, useParams } from 'react-router';
import { api, seriesPath } from '../lib/api.js';
import { useAct, useLoad, usePoll } from '../lib/hooks.js';
import { labelTitle } from '../lib/labels.js';
import { fmtDuration, fmtTime } from '../lib/time.js';
import { Crumbs, Seg, enc } from '../components/kit.jsx';
import { Dialog, ErrorBox, Loading, Notice, Progress, StatusTag } from '../components/ui.jsx';
import { CutBar, CutLegend } from '../components/Timeline.jsx';
import { RecipeDialog } from '../components/Recipe.jsx';
import { useApi } from '../lib/store.js';

const ACTIVE = ['queued', 'running'];
const PRESETS = [['canon_only', 'Canon only'], ['canon_mixed', 'Canon + mixed'], ['everything', 'Everything'], ['custom', 'Custom']];
const LABEL_COLOR = { canon: 'var(--ready)', mixed: 'var(--accent)', filler: 'var(--danger)', unknown: 'var(--dim)' };
const seasonName = (n) => (n === 0 ? 'Specials' : `Season ${n}`);

function Stat({ value, label }) {
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 2, padding: '10px 14px', border: '1px solid var(--line)', borderRadius: 8, flex: '1 1 130px' }}>
      <b className="mono" style={{ fontSize: 16 }}>{value}</b><span className="tiny muted">{label}</span>
    </div>
  );
}

// The render job of this page, shown in the dialog and on the page once it is closed.
function JobCard({ job, onCancel }) {
  return (
    <div className="card stack" style={{ padding: 16 }}>
      <div className="row"><b>Render</b><StatusTag status={job.status} />
        <span className="muted grow">{job.message}</span>
        {ACTIVE.includes(job.status) && <button type="button" className="btn btn-sm" onClick={onCancel}>Cancel</button>}
      </div>
      <Progress value={job.progress} />
      {job.status === 'done' && (
        <div className="mono tiny">
          {job.result?.cached && <span className="tag tag-ok" title="This exact cut was already rendered">cache hit</span>}{' '}
          {job.result?.output}
        </div>
      )}
    </div>
  );
}

// "Build cut": per-run options, the plan's totals, dry run and render. Options here
// apply to this run only; the preset on the page is the series default.
function BuildDialog({ base, season, settings, job, setJob, onClose }) {
  const [scope, setScope] = useState('season');
  const [opts, setOpts] = useState({});
  const [mode, setMode] = useState('auto');
  const [keep, setKeep] = useState(true);
  const [refresh, setRefresh] = useState(false);
  const [dry, setDry] = useState(null);
  const { act, msg, busy, clear } = useAct();
  const body = useMemo(() => ({ seasons: scope === 'all' ? null : [season], options: opts }), [scope, season, opts]);
  const cutQ = useLoad(() => api(`${base}/cut`, { method: 'POST', read: true, body }), [base, body]);
  const val = (k) => opts[k] ?? settings[k];
  const setOpt = (k, v) => { setDry(null); setOpts((o) => ({ ...o, [k]: v })); };
  const num = (k, label) => (
    <label className="field">{label} (s)
      <input className="input" type="number" min="0" step="0.5" value={val(k)}
        onChange={(e) => e.target.value !== '' && setOpt(k, Number(e.target.value))} />
    </label>
  );
  const cut = cutQ.data;
  const t = cut?.totals;
  const running = job && ACTIVE.includes(job.status);
  const dryRun = () => act(async () => setDry(await api(`${base}/render`, { method: 'POST', read: true, body: { ...body, dry_run: true, mode } })));
  const render = () => act(async () => setJob(await api(`${base}/render`, { method: 'POST', body: { ...body, mode, keep, refresh } })), 'Render queued; it also shows in Jobs');
  const cancel = () => act(async () => setJob(await api(`/jobs/${job.id}/cancel`, { method: 'POST' })));

  return (
    <Dialog title={`Build cut · ${scope === 'all' ? 'all seasons' : seasonName(season)}`} onClose={onClose} wide>
      <Seg label="What to build" value={scope} onChange={(v) => { setScope(v); setDry(null); }}
        options={[['season', seasonName(season)], ['all', 'All seasons']]} />
      <Notice msg={msg} onClose={clear} />
      {cutQ.error && <ErrorBox error={cutQ.error} onRetry={cutQ.reload} />}
      {t ? (
        <div className="row wrap" style={{ gap: 10, alignItems: 'stretch' }}>
          <Stat value={`${t.kept} / ${t.kept + t.skipped}`} label="episodes kept" />
          <Stat value={fmtDuration(t.original_runtime)} label="original runtime" />
          <Stat value={fmtDuration(t.final_runtime)} label="final runtime" />
          <Stat value={fmtDuration(t.time_saved)} label="time saved" />
        </div>
      ) : !cutQ.error && <Loading what="Planning" />}

      <details>
        <summary className="muted" style={{ cursor: 'pointer' }}>Options for this run</summary>
        <div className="form-grid" style={{ marginTop: 10 }}>
          {num('pad', 'Padding')}{num('merge_gap', 'Merge gaps under')}{num('min_segment', 'Drop pieces under')}
          <label className="field">Transition
            <select className="input" value={val('transition')} onChange={(e) => setOpt('transition', e.target.value)}>
              <option value="none">None (cut)</option><option value="fade">Fade (re-encodes)</option>
            </select>
          </label>
          <label className="field">Render mode
            <select className="input" value={mode} onChange={(e) => { setMode(e.target.value); setDry(null); }}>
              <option value="auto">Auto</option><option value="copy">Stream copy</option><option value="encode">Re-encode</option>
            </select>
          </label>
        </div>
        <div className="stack" style={{ gap: 6, marginTop: 10 }}>
          <label className="check"><input type="checkbox" checked={val('keep_intro_first_episode')} onChange={(e) => setOpt('keep_intro_first_episode', e.target.checked)} />
            Keep the opening in the first episode</label>
          <label className="check" title="Off: the output is deleted after the retention days in Settings">
            <input type="checkbox" checked={keep} onChange={(e) => setKeep(e.target.checked)} />Keep the rendered file</label>
          <label className="check" title="Render again even if this exact cut is already cached">
            <input type="checkbox" checked={refresh} onChange={(e) => setRefresh(e.target.checked)} />Render again (ignore the cache)</label>
        </div>
        <p className="muted tiny" style={{ margin: '8px 0 0' }}>An unchanged cut replays from the cache instead of rendering again.</p>
      </details>

      {cut && cut.segments.length > 0 && (
        <details>
          <summary className="muted" style={{ cursor: 'pointer' }}>Cut list · {cut.segments.length} piece(s){t.skipped ? ` · ${t.skipped} skipped` : ''}</summary>
          <div className="scroll-x" style={{ maxHeight: 280, overflowY: 'auto', marginTop: 8 }}>
            <table className="table">
              <thead><tr><th>#</th><th>Episode</th><th>Start</th><th>End</th><th className="num">Length</th><th>Why</th></tr></thead>
              <tbody>
                {cut.segments.map((s, i) => (
                  <tr key={i}>
                    <td className="muted">{i + 1}</td><td className="mono">{s.episode_id}</td>
                    <td className="mono">{fmtTime(s.start, 1)}</td><td className="mono">{fmtTime(s.end, 1)}</td>
                    <td className="num mono">{fmtTime(s.duration, 1)}</td><td className="muted">{s.reason}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          {t.skipped > 0 && (
            <ul className="muted tiny">{cut.skipped_episodes.map((s) => <li key={s.episode_id}><span className="mono">{s.episode_id}</span>: {s.reason}</li>)}</ul>
          )}
        </details>
      )}
      {cut && cut.segments.length === 0 && <p className="muted" style={{ margin: 0 }}>Nothing is kept with the current choices.</p>}

      {dry && (
        <div className="stack" style={{ gap: 8 }}>
          <span>Mode <b>{dry.render.mode}</b> <span className="muted">{dry.render.reason}</span>
            {dry.cached && <span className="tag tag-ok" style={{ marginLeft: 8 }}>cached</span>}</span>
          <span className="muted tiny">Output <span className="mono">{dry.render.output}</span> · {fmtTime(dry.render.total_seconds)} · {dry.render.chapters?.length ?? 0} chapters</span>
          {(dry.render.warnings || []).map((w) => <div key={w} className="notice notice-err">{w}</div>)}
          <div className="pre">{Array.isArray(dry.render.command) ? dry.render.command.join(' ') : String(dry.render.command)}</div>
        </div>
      )}

      {job && <JobCard job={job} onCancel={cancel} />}

      <div className="row" style={{ justifyContent: 'flex-end' }}>
        <button type="button" className="btn" onClick={dryRun} disabled={busy || !cut?.segments.length}>Dry run</button>
        <button type="button" className="btn btn-primary" onClick={render} disabled={busy || !cut?.segments.length || running}>Render</button>
      </div>
    </Dialog>
  );
}

export function SeasonPage() {
  const { key, season: seasonParam } = useParams();
  const season = Number(seasonParam);
  const base = seriesPath(key);
  const nav = useNavigate();
  const view = useApi(`${base}/episodes`);
  const plan = useLoad(() => api(`${base}/cut`, { method: 'POST', read: true, body: { seasons: [season] } }), [key, season]);
  const { act, msg, busy, clear } = useAct();
  const [dialog, setDialog] = useState(null); // 'build' | 'recipe'
  const [job, setJob] = useState(null);

  const pollJob = useCallback(async () => {
    if (job) setJob(await api(`/jobs/${job.id}`).catch(() => job));
  }, [job]);
  usePoll(pollJob, 1000, job && ACTIVE.includes(job.status));

  if (view.error && !view.data) return <div className="page"><ErrorBox error={view.error} onRetry={view.reload} /></div>;
  if (!view.data) return <div className="page"><Loading /></div>;

  const { series, settings, episodes } = view.data;
  const showTo = `/series/${enc(key)}`;
  const seasons = [...new Set(episodes.map((e) => e.season))].sort((a, b) => (a === 0) - (b === 0) || a - b);
  const rows = episodes.filter((e) => e.season === season);
  const segsBy = {};
  for (const s of plan.data?.segments || []) (segsBy[s.episode_id] ||= []).push(s);

  const reload = () => { view.reload(); plan.reload(); };
  const call = (path, method, body, ok) => act(async () => { await api(`${base}${path}`, { method, body }); reload(); }, ok);
  const setInclude = (id, include) => call(`/episodes/${enc(id)}/override`, 'PUT', { include });
  const detect = () => act(() => api(`${base}/detect-trims`, { method: 'POST', body: { season } }),
    'Detection queued. It runs in Jobs; reload this page when it is done.');

  return (
    <div className="page" style={{ gap: 22 }}>
      <div className="toolbar">
        <Crumbs items={[['Library', '/library'], [series.title, showTo], [seasonName(season)]]} />
        <div className="row">
          <button type="button" className="btn" onClick={() => setDialog('recipe')}>Recipe</button>
          <button type="button" className="btn btn-primary" onClick={() => setDialog('build')} disabled={!rows.length}>Build cut</button>
        </div>
      </div>

      <div className="toolbar">
        <Seg label="Preset" value={settings.preset} options={PRESETS}
          onChange={(preset) => call('/settings', 'PUT', { preset }, `Preset: ${PRESETS.find(([v]) => v === preset)[1]}`)} />
        <div className="row wrap">
          <div role="tablist" aria-label="Season" style={{ display: 'flex', gap: 2, flexWrap: 'wrap' }}>
            {seasons.map((s) => {
              const eps = episodes.filter((e) => e.season === s);
              const onDisk = eps.filter((e) => e.has_file).length;
              const on = s === season;
              return (
                <button key={s} type="button" role="tab" aria-selected={on} className="btn"
                  style={{ height: 32, background: on ? 'var(--line)' : 'transparent', borderColor: on ? undefined : 'transparent', color: on ? undefined : 'var(--muted)' }}
                  onClick={() => nav(`${showTo}/season/${s}`)}>
                  {s === 0 ? 'Specials' : `S${s}`}
                  {onDisk < eps.length && <span className="mono c-danger" title={`${onDisk} of ${eps.length} on disk`}>{onDisk}</span>}
                </button>
              );
            })}
          </div>
          <button type="button" className="btn" style={{ height: 32 }} onClick={detect} disabled={busy || !rows.length}
            title="Fingerprint the audio of this season to find openings, endings and recaps">Detect OP / ED</button>
        </div>
      </div>

      <Notice msg={msg} onClose={clear} />
      {job && dialog !== 'build' && <JobCard job={job} onCancel={() => act(async () => setJob(await api(`/jobs/${job.id}/cancel`, { method: 'POST' })))} />}
      <CutLegend />

      {rows.length === 0 ? <p className="muted">No episodes in {seasonName(season).toLowerCase()}.</p> : (
        <div style={{ border: '1px solid var(--line)', borderRadius: 10, overflowX: 'auto' }}>
          <table className="table" style={{ minWidth: 860 }}>
            <thead>
              <tr>
                <th style={{ width: 72, padding: '10px 12px' }}>Episode</th><th>Title</th><th style={{ width: 96 }}>Label</th>
                <th style={{ width: 64 }}>Keep</th><th style={{ width: 260 }}>What gets cut</th><th style={{ width: 128, textAlign: 'right' }}>Runtime</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((e) => {
                const segs = plan.data ? segsBy[e.id] || [] : null;
                const kept = !e.include ? 0 : segs ? segs.reduce((a, s) => a + s.duration, 0) : null;
                return (
                  <tr key={e.id}>
                    <td className="mono muted">{e.id}</td>
                    <td>
                      <Link to={`${showTo}/episode/${enc(e.id)}`} style={{ color: 'var(--text)' }}>{e.title || e.id}</Link>
                      {!e.has_file && <span className="tag tag-error" style={{ marginLeft: 8 }}>no file</span>}
                      {e.segments && <span className="tag" style={{ marginLeft: 8 }} title="Custom segments: these exact ranges are kept">own cut</span>}
                    </td>
                    <td>
                      <span title={labelTitle(e.label, e.label_source)} style={{ display: 'inline-flex', alignItems: 'center', gap: 6, fontSize: 13 }}>
                        <span style={{ width: 8, height: 8, borderRadius: 2, background: LABEL_COLOR[e.label] || 'var(--dim)' }} />
                        {e.label}{e.override?.label && <span className="dim tiny">·you</span>}
                      </span>
                    </td>
                    <td>
                      <span className="row" style={{ gap: 4 }}>
                        <input type="checkbox" checked={e.include} aria-label={`Keep ${e.id}`} title={e.include_reason} disabled={busy}
                          style={{ width: 16, height: 16, accentColor: 'var(--accent)', margin: 0 }}
                          onChange={(ev) => setInclude(e.id, ev.target.checked)} />
                        {e.override?.include != null && (
                          <button type="button" className="btn btn-ghost btn-sm" style={{ padding: '0 4px' }} title="Back to the preset’s choice"
                            aria-label={`Use the preset for ${e.id}`} onClick={() => setInclude(e.id, null)}>↺</button>
                        )}
                      </span>
                    </td>
                    <td><CutBar episode={e} segments={segs} included={e.include} /></td>
                    <td className="mono" style={{ textAlign: 'right', fontSize: 13 }}>
                      <span className="muted">{fmtTime(e.duration)}</span> → {e.include ? (kept == null ? '…' : fmtTime(kept)) : <span className="muted">skipped</span>}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
      {plan.error && rows.length > 0 && <ErrorBox error={plan.error} onRetry={plan.reload} />}

      {dialog === 'build' && (
        <BuildDialog base={base} season={season} settings={settings} job={job} setJob={setJob} onClose={() => setDialog(null)} />
      )}
      {dialog === 'recipe' && <RecipeDialog series={series} onClose={() => setDialog(null)} onImported={reload} />}
    </div>
  );
}
