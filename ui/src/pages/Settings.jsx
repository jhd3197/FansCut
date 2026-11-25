import { useEffect, useState } from 'react';
import { api } from '../lib/api.js';
import { useAct, useUrlState } from '../lib/hooks.js';
import { ErrorBox, Loading, Notice } from '../components/ui.jsx';
import './Settings.css';
import { useApi } from '../lib/store.js';

// Immutable set at a dotted path: set(cfg, 'library.sonarr.url', v)
export function setPath(obj, path, value) {
  const [head, ...rest] = path.split('.');
  return { ...obj, [head]: rest.length ? setPath(obj[head] ?? {}, rest.join('.'), value) : value };
}

const SECTIONS = [
  ['sources', 'Sources'], ['output', 'Output'], ['plex-out', 'Add to Plex'], ['render', 'Rendering'], ['cut', 'Cut defaults'],
  ['ai', 'AI'], ['auto', 'Automation'], ['data', 'Filler data'], ['system', 'Caches & network'],
];

const mb = (b) => (b >= 1048576 ? `${(b / 1048576).toFixed(1)} MB` : `${Math.round(b / 1024)} KB`);

// Cache sizes and hit counts, each with a clear button.
function CachePanel() {
  const c = useApi('/system/cache');
  const { act, busy } = useAct();
  const clear = (name) => act(() => api(`/system/cache/${name}/clear`, { method: 'POST' })).then(c.reload);
  if (!c.data) return null;
  const { art, library, http } = c.data;
  const rows = [
    ['art', 'Posters and backdrops', `${art.images} images · ${mb(art.bytes)} of ${mb(art.max_bytes)} · ${art.counts.hit} hits, ${art.counts.miss} fetched, ${art.counts.error} failed`],
    ['library', 'Library listings', `${library.entries} lists · ${library.hits} hits, ${library.stale} served while refreshing, ${library.failures} failures`],
    ['http', 'Catalogue lookups', `${http.rows} replies · ${mb(http.bytes)}`],
  ];
  return (
    <div className="stack10" style={{ paddingTop: 8 }}>
      {rows.map(([name, label, detail]) => (
        <div key={name} style={{ display: 'flex', gap: 12, alignItems: 'center', flexWrap: 'wrap' }}>
          <button className="btn btn-sm" type="button" disabled={busy} onClick={() => clear(name)}>Clear</button>
          <span><b>{label}</b> <span className="muted tiny">{detail}</span></span>
        </div>
      ))}
    </div>
  );
}

const CODECS = [
  ['auto', 'Automatic (HEVC 10-bit for HDR, H.264 otherwise)'],
  ['hevc', 'HEVC 10-bit (keeps HDR)'],
  ['h264', 'H.264 (plays everywhere, no HDR)'],
];

function Row({ label, hint, htmlFor, children }) {
  const head = <>{label}{hint && <span className="hint">{hint}</span>}</>;
  return (
    <div className="srow">
      {htmlFor ? <label className="lbl" htmlFor={htmlFor}>{head}</label> : <span className="lbl">{head}</span>}
      <div>{children}</div>
    </div>
  );
}

// Sign in with Plex: open plex.tv to approve, then FansCut saves the token.
function PlexSignIn({ onDone }) {
  const [state, setState] = useState(null);
  const start = async () => {
    try {
      const pin = await api('/plex/pin', { method: 'POST' });
      window.open(pin.auth_url, '_blank', 'noopener');
      setState({ text: 'Approve FansCut in the Plex tab that opened…' });
      for (let i = 0; i < 90; i += 1) {
        await new Promise((r) => setTimeout(r, 2000));
        const r = await api(`/plex/pin/${pin.id}`, { method: 'POST', body: {} });
        if (r.done) {
          setState({ ok: true, text: `Signed in${r.url ? `; using ${r.url}` : ''}.` });
          onDone(r);
          return;
        }
      }
      setState({ ok: false, text: 'Timed out waiting for Plex. Try again.' });
    } catch (e) {
      setState({ ok: false, text: e.message });
    }
  };
  return (
    <div style={{ display: 'flex', gap: 12, alignItems: 'center', flexWrap: 'wrap' }}>
      <button className="btn" type="button" onClick={start}>Sign in with Plex</button>
      {state && <span role="status" className={state.ok == null ? 'muted tiny' : state.ok ? 'c-ready tiny' : 'c-danger tiny'}>{state.text}</span>}
    </div>
  );
}

// Scheduled tasks: when each runs, run it now, change its cron.
function Schedules() {
  const q = useApi('/schedules');
  const { act, busy } = useAct();
  const [edit, setEdit] = useState({});
  if (!q.data) return null;
  const save = (name) => act(() => api(`/schedules/${name}`, { method: 'PUT', body: { cron: edit[name] ?? '' } }), 'Schedule saved')
    .then(() => setEdit((e) => { const n = { ...e }; delete n[name]; return n; }));
  return (
    <div className="stack10" style={{ paddingTop: 8 }}>
      {q.data.map((t) => (
        <div key={t.name} className="sched">
          <div>
            <b>{t.description}</b>
            <div className="muted tiny">
              {t.when}{t.next_run ? ` · next ${t.next_run.replace('T', ' ')}` : ''}
              {t.last_run ? ` · last ${t.last_run.replace('T', ' ')}` : ''}
              {t.last_error ? <span className="c-danger"> · failed: {t.last_error}</span> : null}
            </div>
          </div>
          <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
            <input className="inp mono" style={{ width: 150 }} aria-label={`${t.name} schedule`} placeholder="off"
              value={edit[t.name] ?? t.cron} onChange={(e) => setEdit((x) => ({ ...x, [t.name]: e.target.value }))} />
            {edit[t.name] != null && edit[t.name] !== t.cron && (
              <button className="btn btn-sm" type="button" disabled={busy} onClick={() => save(t.name)}>Save</button>
            )}
            {t.running
              ? <button className="btn btn-sm" type="button" onClick={() => act(() => api(`/schedules/${t.name}/cancel`, { method: 'POST' }))}>Stop</button>
              : <button className="btn btn-sm" type="button" disabled={busy} onClick={() => act(() => api(`/schedules/${t.name}/run`, { method: 'POST' }), 'Started')}>Run now</button>}
          </div>
        </div>
      ))}
    </div>
  );
}

// Where to send "job done / failed" messages.
function NotifyTargets({ value, onChange }) {
  const list = value || [];
  const set = (i, k, v) => onChange(list.map((t, j) => (j === i ? { ...t, [k]: v } : t)));
  return (
    <div className="stack10" style={{ paddingTop: 8 }}>
      {list.map((t, i) => (
        <div key={i} style={{ display: 'flex', gap: 8, flexWrap: 'wrap', alignItems: 'center' }}>
          <input className="inp mono" style={{ flex: '1 1 260px' }} aria-label="Notification URL" placeholder="https://ntfy.sh/my-topic"
            value={t.url} onChange={(e) => set(i, 'url', e.target.value)} />
          <select className="inp" aria-label="Format" value={t.format} onChange={(e) => set(i, 'format', e.target.value)}>
            <option value="json">JSON webhook</option><option value="discord">Discord</option><option value="ntfy">ntfy</option>
          </select>
          <label className="tiny"><input type="checkbox" checked={t.events.includes('done')}
            onChange={(e) => set(i, 'events', e.target.checked ? [...t.events, 'done'] : t.events.filter((x) => x !== 'done'))} /> done</label>
          <label className="tiny"><input type="checkbox" checked={t.events.includes('failed')}
            onChange={(e) => set(i, 'events', e.target.checked ? [...t.events, 'failed'] : t.events.filter((x) => x !== 'failed'))} /> failed</label>
          <button className="btn btn-sm btn-ghost" type="button" aria-label="Remove" onClick={() => onChange(list.filter((_, j) => j !== i))}>✕</button>
        </div>
      ))}
      <div><button className="btn btn-sm" type="button" onClick={() => onChange([...list, { url: '', format: 'json', events: ['done', 'failed'] }])}>Add a target</button></div>
    </div>
  );
}

// The webhook URL with the API key in it, and a way to replace a leaked key.
function WebhookUrl() {
  const key = useApi('/auth/key');
  const [shown, setShown] = useState(false);
  const { act, busy } = useAct();
  const k = key.data?.api_key;
  if (!k) return null;
  const url = `${window.location.origin}/api/webhooks/sonarr?apikey=${shown ? k : '•'.repeat(12)}`;
  return (
    <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
      <code className="mono tiny" style={{ wordBreak: 'break-all' }}>{url}</code>
      <button type="button" className="btn btn-sm" onClick={() => setShown((v) => !v)}>{shown ? 'Hide' : 'Show'}</button>
      <button type="button" className="btn btn-sm" disabled={busy}
        onClick={() => act(() => api('/auth/key/regenerate', { method: 'POST' }), 'New key made; update Sonarr').then(key.reload)}>
        New key
      </button>
    </div>
  );
}

export function Settings() {
  const { data, error, loading, reload } = useApi('/settings');
  const fillers = useApi('/fillers');
  const [cfg, setCfg] = useState(null);
  const { act, msg, busy, clear } = useAct();
  const [tests, setTests] = useState({});
  const [active, setActive] = useUrlState('section', 'sources');
  // seed the form once; background refetches must not wipe unsaved edits
  useEffect(() => { if (data && !cfg) setCfg(data); }, [data, cfg]);
  // a link to /settings?section=plex-out opens at that section
  const loaded = Boolean(cfg);
  useEffect(() => {
    if (loaded && active !== 'sources') document.getElementById(active)?.scrollIntoView({ block: 'start' });
  }, [loaded]); // eslint-disable-line react-hooks/exhaustive-deps

  if (error && !data) return <div className="page"><ErrorBox error={error} onRetry={reload} /></div>;
  if (loading || !cfg) return <div className="page"><Loading /></div>;

  const get = (path) => path.split('.').reduce((o, k) => o?.[k], cfg);
  const set = (path) => (e) => {
    const v = e.target.type === 'checkbox' ? e.target.checked : e.target.type === 'number' ? Number(e.target.value) : e.target.value;
    setCfg((c) => setPath(c, path, v));
  };
  const idOf = (path) => `set-${path.replace(/\./g, '-')}`;
  const input = (path, props = {}) => {
    const locked = cfg.env_locked?.includes(path);
    return <input id={idOf(path)} className="inp" value={locked ? '' : get(path) ?? ''} onChange={set(path)} {...props}
      {...(locked && { disabled: true, placeholder: 'set by environment' })} />;
  };
  const text = (path, label, hint, props = {}) => (
    <Row label={label} hint={hint} htmlFor={idOf(path)}>{input(path, props)}</Row>
  );
  const select = (path, label, options, hint) => (
    <Row label={label} hint={hint} htmlFor={idOf(path)}>
      <select id={idOf(path)} className="inp" value={get(path) ?? ''} onChange={set(path)}>
        {options.map((o) => <option key={o}>{o}</option>)}
      </select>
    </Row>
  );
  const check = (path, label) => (
    <label className="ck"><input type="checkbox" checked={!!get(path)} onChange={set(path)} /> {label}</label>
  );
  const maps = cfg.library.path_mappings;
  const setMaps = (next) => setCfg((c) => setPath(c, 'library.path_mappings', next));

  const save = () => act(async () => { setCfg(await api('/settings', { method: 'PUT', body: cfg })); }, 'Settings saved');
  const detect = () => act(async () => {
    const r = await api('/settings/detect', { method: 'POST' });
    setCfg(r.settings);
    return r;
  }, (r) => {
    const names = { plex: 'Plex', radarr: 'Radarr', sonarr: 'Sonarr' };
    if (r.filled.length) return `Connected ${r.filled.map((s) => names[s]).join(', ')} and saved.`;
    return r.found.length ? `Found ${r.found.map((f) => names[f.service]).join(', ')}; already set up.` : 'No Plex, Radarr or Sonarr found on this computer.';
  });
  const test = async (service, body) => {
    setTests((t) => ({ ...t, [service]: { text: 'Testing…' } }));
    try {
      const r = await api(`/settings/test/${service}`, { method: 'POST', body });
      setTests((t) => ({ ...t, [service]: { ok: r.ok, text: r.message } }));
    } catch (e) {
      setTests((t) => ({ ...t, [service]: { ok: false, text: e.message } }));
    }
  };
  const testRow = (service, body, label = 'Test') => (
    <div style={{ display: 'flex', gap: 12, alignItems: 'center', flexWrap: 'wrap' }}>
      <button className="btn" type="button" onClick={() => test(service, body)}>{label}</button>
      {tests[service] && (
        <span role="status" style={{ fontSize: 13 }}
          className={tests[service].ok == null ? 'muted' : tests[service].ok ? 'c-ready' : 'c-danger'}>{tests[service].text}</span>
      )}
    </div>
  );
  const refreshFillers = () => act(async () => {
    const r = await api('/fillers/refresh', { method: 'POST' });
    await fillers.reload();
    if (!r.ok) throw new Error(r.message);
    return r;
  }, (r) => r.message || 'Filler data refreshed');
  const f = fillers.data;
  const jump = (id) => (e) => {
    e.preventDefault();
    setActive(id);
    document.getElementById(id)?.scrollIntoView({ behavior: 'smooth', block: 'start' });
  };
  const lib = cfg.library;

  return (
    <div className="page settings" style={{ maxWidth: 1060, gap: 24 }}>
      
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: 12, flexWrap: 'wrap' }}>
        <span className="muted tiny">Saved to config.yaml. Host and port changes need a restart.</span>
        <button className="btn btn-primary" type="button" onClick={save} disabled={busy}>Save changes</button>
      </div>
      <Notice msg={msg} onClose={clear} />
      <div style={{ display: 'flex', flexWrap: 'wrap', gap: 32, alignItems: 'flex-start' }}>
        <nav className="sub" aria-label="Settings sections"
          style={{ flex: '1 1 160px', maxWidth: 180, display: 'flex', flexDirection: 'column', gap: 2, position: 'sticky', top: 28 }}>
          {SECTIONS.map(([id, name]) => (
            <a key={id} href={`#${id}`} aria-current={active === id} onClick={jump(id)}>{name}</a>
          ))}
        </nav>
        <div style={{ flex: '999 1 520px', minWidth: 0, display: 'flex', flexDirection: 'column', gap: 36 }}>
          <section id="sources" aria-labelledby="src-h">
            <h2 id="src-h" className="sh">Sources</h2>
            <Row label="Find automatically" hint="Reads Plex, Radarr and Sonarr's own settings on this computer and fills any empty URL or key.">
              <div style={{ display: 'flex', gap: 12, alignItems: 'center', flexWrap: 'wrap' }}>
                <button className="btn" type="button" disabled={busy} onClick={detect}>Find apps on this computer</button>
              </div>
            </Row>
            <Row label="Plex" hint="Optional. Shows and movies. Leave sections empty for every show library.">
              <div className="stack10">
                {input('library.plex.url', { 'aria-label': 'Plex URL', placeholder: 'http://plex:32400' })}
                {input('library.plex.token', { 'aria-label': 'Plex token', type: 'password', autoComplete: 'off', placeholder: 'Token' })}
                {testRow('plex', { url: lib.plex.url, token: lib.plex.token })}
                <PlexSignIn onDone={(r) => setCfg((c) => setPath(setPath(c, 'library.plex.token', '***'), 'library.plex.url', r.url || c.library.plex.url))} />
              </div>
            </Row>
            <Row label="Sonarr" hint="Optional. Series, episodes and file paths, plus webhooks.">
              <div className="stack10">
                {input('library.sonarr.url', { 'aria-label': 'Sonarr URL', placeholder: 'http://sonarr:8989' })}
                {input('library.sonarr.api_key', { 'aria-label': 'Sonarr API key', type: 'password', autoComplete: 'off', placeholder: 'API key' })}
                {testRow('sonarr', { url: lib.sonarr.url, api_key: lib.sonarr.api_key })}
              </div>
            </Row>
            <Row label="Radarr" hint="Optional. Movie files and editions, as timeline sources.">
              <div className="stack10">
                {input('library.radarr.url', { 'aria-label': 'Radarr URL', placeholder: 'http://radarr:7878' })}
                {input('library.radarr.api_key', { 'aria-label': 'Radarr API key', type: 'password', autoComplete: 'off', placeholder: 'API key' })}
                {testRow('radarr', { url: lib.radarr.url, api_key: lib.radarr.api_key })}
              </div>
            </Row>
            <Row label="Media folders" hint="Plain mode, no Sonarr needed: one subfolder per series. Movies: one subfolder per movie; '{tmdb-123}' in the name sets the id.">
              <div className="stack10">
                {input('library.folder.path', { 'aria-label': 'Series folder', placeholder: 'Series folder, e.g. /media', className: 'inp mono' })}
                {input('library.movies.path', { 'aria-label': 'Movies folder', placeholder: 'Movies folder, e.g. /movies', className: 'inp mono' })}
                {testRow('folder', { path: lib.folder.path }, 'Check folder')}
              </div>
            </Row>
            <Row label="Path mapping" hint="When a server sees files under a different path than FansCut does.">
              <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
                {maps.map((m, i) => (
                  <div key={i} style={{ display: 'grid', gridTemplateColumns: 'minmax(0,1fr) auto minmax(0,1fr) auto', gap: 8, alignItems: 'center', maxWidth: 520 }}>
                    <input className="inp mono" aria-label={`Remote prefix ${i + 1}`} placeholder="remote, e.g. /tv" value={m.remote}
                      onChange={(e) => setMaps(maps.map((x, j) => (j === i ? { ...x, remote: e.target.value } : x)))} />
                    <span className="muted" aria-hidden="true">→</span>
                    <input className="inp mono" aria-label={`Local prefix ${i + 1}`} placeholder="local, e.g. /media/tv" value={m.local}
                      onChange={(e) => setMaps(maps.map((x, j) => (j === i ? { ...x, local: e.target.value } : x)))} />
                    <button className="btn" type="button" aria-label={`Remove mapping ${i + 1}`} style={{ width: 36, padding: 0 }}
                      onClick={() => setMaps(maps.filter((_, j) => j !== i))}>×</button>
                  </div>
                ))}
                <button className="btn" type="button" style={{ alignSelf: 'flex-start' }}
                  onClick={() => setMaps([...maps, { remote: '', local: '' }])}>Add mapping</button>
              </div>
            </Row>
          </section>

          <section id="output" aria-labelledby="out-h">
            <h2 id="out-h" className="sh">Output</h2>
            {text('paths.output_dir', 'Output folder', null, { className: 'inp mono' })}
            <Row label="Data folder" hint="Set at startup." htmlFor="set-data-dir">
              <input id="set-data-dir" className="inp mono" value={cfg.paths.data_dir} disabled />
            </Row>
            <Row label="Remove after" hint="For renders without “keep”. 0 keeps everything until you delete it." htmlFor={idOf('render.ephemeral_days')}>
              <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
                {input('render.ephemeral_days', { type: 'number', min: 0, className: 'inp mono', style: { width: 90 } })}
                <span className="muted" style={{ fontSize: 13 }}>days</span>
              </div>
            </Row>
            <Row label="Keep renders" hint="“Keep” off plus a day count = watch and clean up.">
              <div style={{ paddingTop: 8 }}>{check('render.keep', 'Keep renders by default')}</div>
            </Row>
            {text('render.language', 'Audio language', 'Preferred audio track, e.g. jpn or eng. Empty uses the first track.', { placeholder: 'jpn', className: 'inp mono', style: { width: 120 } })}
          </section>

          <section id="plex-out" aria-labelledby="plexout-h">
            <h2 id="plexout-h" className="sh">Add to Plex</h2>
            <p className="muted tiny" style={{ margin: '0 0 8px' }}>
              After every build. Needs Plex under Sources; without it, files still land in the output folder.
            </p>
            <Row label="Rescan" hint="Plex picks up the new file right away instead of at its next scheduled scan.">
              <div style={{ paddingTop: 8 }}>{check('render.plex_refresh', 'Rescan the Plex libraries after a build')}</div>
            </Row>
            {text('render.plex_collection', 'Collection', 'Builds are added to this collection. Empty turns it off.', { placeholder: 'FansCut', style: { width: 240 } })}
            <Row label="Poster" hint="The cut’s own poster when it has one, else a frame from the build.">
              <div style={{ paddingTop: 8 }}>{check('render.poster', 'Write poster.jpg next to each build')}</div>
            </Row>
          </section>

          <section id="render" aria-labelledby="ren-h">
            <h2 id="ren-h" className="sh">Rendering</h2>
            <p className="muted tiny" style={{ margin: '0 0 8px' }}>Stream copy is used when files match; these apply to re-encodes.</p>
            {select('ffmpeg.container', 'Container', ['mkv', 'mp4'])}
            <Row label="Video encoder" htmlFor={idOf('ffmpeg.video_codec')}
              hint="Automatic picks HEVC 10-bit for HDR sources, so HDR stays HDR, and H.264 otherwise.">
              <div className="stack10">
                <select id={idOf('ffmpeg.video_codec')} className="inp" style={{ maxWidth: 320 }}
                  value={get('ffmpeg.video_codec') ?? 'auto'} onChange={set('ffmpeg.video_codec')}>
                  {CODECS.map(([v, label]) => <option key={v} value={v}>{label}</option>)}
                </select>
                {check('ffmpeg.nvenc', `Encode on an NVIDIA GPU (${get('ffmpeg.video_codec') === 'h264' ? 'h264_nvenc' : get('ffmpeg.video_codec') === 'hevc' ? 'hevc_nvenc' : 'h264_nvenc / hevc_nvenc'})`)}
              </div>
            </Row>
            {text('ffmpeg.crf', 'Quality (CRF)', 'Lower is better and bigger. 18–23 is typical.', { type: 'number', min: 0, max: 51, className: 'inp mono', style: { width: 90 } })}
            {select('ffmpeg.preset', 'Encoder preset', ['ultrafast', 'superfast', 'veryfast', 'faster', 'fast', 'medium', 'slow', 'slower', 'veryslow'])}
            {text('ffmpeg.audio_bitrate', 'Audio bitrate', null, { className: 'inp mono', style: { width: 120 } })}
            {text('ffmpeg.binary', 'ffmpeg binary', null, { className: 'inp mono' })}
            {text('ffmpeg.probe_binary', 'ffprobe binary', null, { className: 'inp mono' })}
            <Row label="EffectCraft" hint="Renders segments with effects and grades. Empty keeps everything in ffmpeg."
              htmlFor={idOf('render.effectcraft_cli')}>
              <div className="stack10">
                {input('render.effectcraft_cli', { className: 'inp mono', placeholder: 'path to effectcraft-cli' })}
                {check('render.effectcraft_grade', 'Send segment colour grades through EffectCraft')}
              </div>
            </Row>
          </section>

          <section id="cut" aria-labelledby="cut-h">
            <h2 id="cut-h" className="sh">Cut defaults</h2>
            <p className="muted tiny" style={{ margin: '0 0 8px' }}>Used for every series unless it has its own settings.</p>
            {select('cut.preset', 'Preset', ['canon_only', 'canon_mixed', 'everything', 'custom'])}
            {select('cut.unknown_policy', 'Unlabelled episodes', ['keep', 'skip'])}
            {text('cut.pad', 'Pad', 'Seconds kept around each cut.', { type: 'number', min: 0, step: 0.5, className: 'inp mono', style: { width: 90 } })}
            {text('cut.merge_gap', 'Merge gap', 'Seconds; closer pieces join into one.', { type: 'number', min: 0, step: 0.5, className: 'inp mono', style: { width: 90 } })}
            {text('cut.min_segment', 'Min segment', 'Seconds; shorter pieces are dropped.', { type: 'number', min: 0, step: 0.5, className: 'inp mono', style: { width: 90 } })}
            {select('cut.transition', 'Transition', ['none', 'fade'])}
            <Row label="Opening">
              <div style={{ paddingTop: 8 }}>{check('cut.keep_intro_first_episode', 'Keep the OP in the first episode')}</div>
            </Row>
          </section>

          <section id="ai" aria-labelledby="ai-h">
            <h2 id="ai-h" className="sh">AI</h2>
            <p className="muted tiny" style={{ margin: '0 0 8px' }}>Any OpenAI-compatible chat endpoint (Ollama, LM Studio, OpenAI…). Turns community cut notes into a recipe.</p>
            {text('ai.base_url', 'Base URL', null, { placeholder: 'http://localhost:11434/v1', className: 'inp mono' })}
            {text('ai.model', 'Model', null, { placeholder: 'qwen2.5:7b' })}
            {text('ai.api_key', 'API key', 'Prefer FANSCUT_AI_API_KEY over the config file.', { type: 'password', autoComplete: 'off' })}
            {text('ai.timeout', 'Timeout', 'Seconds.', { type: 'number', min: 5, className: 'inp mono', style: { width: 90 } })}
          </section>

          <section id="auto" aria-labelledby="auto-h">
            <h2 id="auto-h" className="sh">Automation</h2>
            <Row label="Sonarr webhook" hint="Add a Webhook connection in Sonarr (Settings, Connect) with this URL. The key in it is FansCut's API key.">
              <div className="stack10" style={{ paddingTop: 8 }}>
                <WebhookUrl />
                {check('automation.sonarr_webhook', 'Accept Sonarr webhooks (new episode → detect + render)')}
                {check('automation.auto_detect', 'Run OP/ED detection first')}
                {check('automation.keep', 'Keep webhook renders')}
              </div>
            </Row>
            <Row label="Notifications" hint="Sent when a job finishes. Saved with the rest of the settings.">
              <NotifyTargets value={cfg.notify?.targets} onChange={(v) => setCfg((c) => setPath(c, 'notify.targets', v))} />
            </Row>
            <Row label="Scheduled tasks" hint="Cron: minute hour day month weekday, local time. Empty = off. Saved as you go.">
              <Schedules />
            </Row>
          </section>

          <section id="data" aria-labelledby="data-h">
            <h2 id="data-h" className="sh">Filler data</h2>
            <Row label="AniFiller" hint="Downloaded at runtime from the AniFiller project; nothing is bundled. Check their terms before relying on it.">
              <div className="stack10" style={{ paddingTop: 8 }}>
                {check('catalogues.offline', 'Offline mode (never call AniList, Jikan or GitHub)')}
                <div style={{ display: 'flex', gap: 12, alignItems: 'center', flexWrap: 'wrap' }}>
                  <button className="btn" type="button" onClick={refreshFillers} disabled={busy}>Refresh filler data</button>
                  {f && <span className="muted" style={{ fontSize: 13 }}>{f.shows} show(s) in {f.files} file(s) · <span className="mono">{f.dir}</span></span>}
                </div>
              </div>
            </Row>
          </section>

          <section id="system" aria-labelledby="system-h">
            <h2 id="system-h" className="sh">Caches & network</h2>
            <Row label="Caches"><CachePanel /></Row>
            <Row label="Remote art" hint="Fetch TMDB/TVDB posters through FansCut and keep them, so browsers never contact those sites. Good for offline or private setups.">
              <div style={{ paddingTop: 8 }}>{check('art.cache_remote', 'Cache TMDB and TVDB art here')}</div>
            </Row>
            {text('art.cache_mb', 'Art cache size', 'MB. The oldest images go first past this.', { type: 'number', min: 16, className: 'inp mono', style: { width: 110 } })}
            {text('network.timeout', 'Server timeout', 'Seconds to wait for Sonarr, Radarr and Plex.', { type: 'number', min: 2, className: 'inp mono', style: { width: 90 } })}
            {text('network.proxy', 'Outbound proxy', 'For internet calls (catalogues, GitHub, TMDB). Empty = none.', { placeholder: 'http://proxy:3128', className: 'inp mono' })}
            <Row label="Proxy bypass"><div style={{ paddingTop: 8 }}>{check('network.proxy_bypass_local', 'Reach LAN servers (Plex, Sonarr, Radarr) directly')}</div></Row>
          </section>
        </div>
      </div>
    </div>
  );
}
