import { useCallback, useMemo, useState } from 'react';
import { Link, useParams } from 'react-router';
import { api, seriesPath } from '../lib/api.js';
import { useAct, useLoad, usePoll } from '../lib/hooks.js';
import { groupSegments } from '../lib/timeline.js';
import { fmtDuration, fmtTime } from '../lib/time.js';
import { ErrorBox, Loading, Notice, PageHeader, Progress, StatusTag } from '../components/ui.jsx';
import { EpisodeTrack, TimelineLegend } from '../components/Timeline.jsx';
import { TrimFields } from '../components/TrimFields.jsx';

const ACTIVE = ['queued', 'running'];

function Stat({ value, label }) {
  return <div className="card stat"><b>{value}</b><span>{label}</span></div>;
}

export function CutPage() {
  const { key } = useParams();
  const base = seriesPath(key);
  const [seasonSel, setSeasonSel] = useState('all');
  const [opts, setOpts] = useState({});
  const [mode, setMode] = useState('auto');
  const [open, setOpen] = useState(null);
  const [dry, setDry] = useState(null);
  const [job, setJob] = useState(null);
  const { act, msg, busy, clear } = useAct();

  const seasons = seasonSel === 'all' ? null : [Number(seasonSel)];
  const body = useMemo(() => ({ seasons, options: opts }), [seasons?.[0], opts]); // eslint-disable-line react-hooks/exhaustive-deps
  const eps = useLoad(() => api(`${base}/episodes`), [key]);
  const cutQ = useLoad(() => api(`${base}/cut`, { method: 'POST', body }), [key, body]);
  const refresh = useCallback(() => { setDry(null); eps.reload(); cutQ.reload(); }, [eps, cutQ]);

  const pollJob = useCallback(async () => {
    if (job) setJob(await api(`/jobs/${job.id}`).catch(() => job));
  }, [job]);
  usePoll(pollJob, 1000, job && ACTIVE.includes(job.status));

  if ((eps.error && !eps.data) || (cutQ.error && !cutQ.data)) {
    return <><ErrorBox error={eps.error || cutQ.error} onRetry={refresh} /><Link to={`/series/${encodeURIComponent(key)}`}>← Back to series</Link></>;
  }
  if (!eps.data || !cutQ.data) return <Loading />;

  const { series, settings } = eps.data;
  const cut = cutQ.data;
  const t = cut.totals;
  const bySeg = groupSegments(cut.segments);
  const byId = Object.fromEntries(eps.data.episodes.map((e) => [e.id, e]));
  const seasonList = [...new Set(eps.data.episodes.map((e) => e.season))].sort((a, b) => a - b);
  const val = (k) => opts[k] ?? settings[k];
  const setOpt = (k, v) => setOpts((o) => ({ ...o, [k]: v }));
  const num = (k) => (
    <label className="field">{k.replace('_', ' ')} (s)
      <input className="input" type="number" min="0" step="0.5" value={val(k)}
        onChange={(e) => e.target.value !== '' && setOpt(k, Number(e.target.value))} />
    </label>
  );

  const trimCall = (id, kind, method, payload) => act(async () => {
    await api(`${base}/episodes/${id}/trims/${kind}`, { method, body: payload });
    refresh();
  });
  const dryRun = () => act(async () => setDry(await api(`${base}/render`, { method: 'POST', body: { ...body, dry_run: true, mode } })));
  const render = () => act(async () => setJob(await api(`${base}/render`, { method: 'POST', body: { ...body, mode } })), 'Render queued');
  const cancel = () => act(async () => setJob(await api(`/jobs/${job.id}/cancel`, { method: 'POST' })));

  return (
    <>
      <PageHeader title={`Cut · ${series.title}`} sub={`Preset: ${val('preset')}`}>
        <Link className="btn" to={`/series/${encodeURIComponent(key)}`}>← Episodes</Link>
        <button className="btn" onClick={dryRun} disabled={busy || !cut.segments.length}>Dry run</button>
        <button className="btn btn-primary" onClick={render} disabled={busy || !cut.segments.length || (job && ACTIVE.includes(job.status))}>Render</button>
      </PageHeader>
      <Notice msg={msg} onClose={clear} />

      <div className="cards">
        <Stat value={`${t.kept} / ${t.kept + t.skipped}`} label="episodes kept" />
        <Stat value={fmtDuration(t.original_runtime)} label="original runtime" />
        <Stat value={fmtDuration(t.final_runtime)} label="final runtime" />
        <Stat value={fmtDuration(t.time_saved)} label="time saved" />
      </div>

      {job && (
        <div className="card stack" style={{ marginBottom: 14 }}>
          <div className="row"><b>Render</b><StatusTag status={job.status} />
            <span className="muted grow">{job.message}</span>
            {ACTIVE.includes(job.status) && <button className="btn btn-sm" onClick={cancel}>Cancel</button>}
          </div>
          <Progress value={job.progress} />
          {job.status === 'done' && <div className="mono tiny">{job.result?.output}</div>}
        </div>
      )}

      <div className="card" style={{ marginBottom: 14 }}>
        <div className="form-grid">
          <label className="field">seasons
            <select className="input" value={seasonSel} onChange={(e) => setSeasonSel(e.target.value)}>
              <option value="all">all seasons</option>
              {seasonList.map((s) => <option key={s} value={s}>{s === 0 ? 'Specials' : `Season ${s}`}</option>)}
            </select>
          </label>
          {num('pad')}{num('merge_gap')}{num('min_segment')}
          <label className="field">transition
            <select className="input" value={val('transition')} onChange={(e) => setOpt('transition', e.target.value)}>
              <option value="none">none (cut)</option><option value="fade">fade (re-encodes)</option>
            </select>
          </label>
          <label className="field">render mode
            <select className="input" value={mode} onChange={(e) => setMode(e.target.value)}>
              <option value="auto">auto</option><option value="copy">stream copy</option><option value="encode">re-encode</option>
            </select>
          </label>
          <label className="check" style={{ alignSelf: 'end', paddingBottom: 8 }}>
            <input type="checkbox" checked={val('keep_intro_first_episode')} onChange={(e) => setOpt('keep_intro_first_episode', e.target.checked)} />
            keep the OP in the first episode
          </label>
        </div>
        <p className="muted tiny" style={{ margin: '8px 0 0' }}>Changes here apply to this page only; set series defaults on the episodes page or in Settings.</p>
      </div>

      <h2>Timeline</h2>
      <TimelineLegend />
      <div className="card">
        {cut.kept_episodes.length === 0 && <p className="muted">Nothing is kept with the current preset.</p>}
        {cut.kept_episodes.map((id) => {
          const ep = byId[id];
          const segs = bySeg[id] || [];
          const kept = segs.reduce((a, s) => a + s.duration, 0);
          return (
            <div key={id} className="tl-row">
              <span className="mono">{id}</span>
              <EpisodeTrack episode={ep} segments={segs} />
              <span className="mono tiny muted">{fmtTime(kept)} / {fmtTime(ep.duration)}</span>
              <button className="btn btn-sm" aria-expanded={open === id} onClick={() => setOpen(open === id ? null : id)}>{open === id ? 'Close' : 'Nudge'}</button>
              {open === id && (
                <div className="tl-detail">
                  <TrimFields episode={ep}
                    save={(kind, start, end) => trimCall(id, kind, 'PUT', { start, end })}
                    reset={(kind) => trimCall(id, kind, 'DELETE')} />
                </div>
              )}
            </div>
          );
        })}
        {t.skipped > 0 && (
          <details style={{ marginTop: 12 }}>
            <summary className="muted">{t.skipped} skipped episode(s)</summary>
            <ul className="muted">{cut.skipped_episodes.map((s) => <li key={s.episode_id}><span className="mono">{s.episode_id}</span>: {s.reason}</li>)}</ul>
          </details>
        )}
      </div>

      <h2 style={{ marginTop: 22 }}>Cut list</h2>
      <div className="card scroll-x" style={{ padding: 0 }}>
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

      {dry && (
        <>
          <h2 style={{ marginTop: 22 }}>Dry run</h2>
          <div className="card">
            <p>Mode: <b>{dry.render.mode}</b> <span className="muted">{dry.render.reason}</span></p>
            <p className="muted tiny">Output: <span className="mono">{dry.render.output}</span> · {fmtTime(dry.render.total_seconds)} · {dry.render.chapters?.length ?? 0} chapters</p>
            {(dry.render.warnings || []).map((w) => <div key={w} className="notice notice-err">{w}</div>)}
            <div className="pre">{Array.isArray(dry.render.command) ? dry.render.command.join(' ') : String(dry.render.command)}</div>
          </div>
        </>
      )}
    </>
  );
}
