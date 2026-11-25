import { useEffect, useState } from 'react';
import { api } from '../lib/api.js';
import { useAct, useLoad } from '../lib/hooks.js';
import { ErrorBox, Loading, Notice } from '../components/ui.jsx';

// Immutable set at a dotted path: set(cfg, 'library.sonarr.url', v)
export function setPath(obj, path, value) {
  const [head, ...rest] = path.split('.');
  return { ...obj, [head]: rest.length ? setPath(obj[head] ?? {}, rest.join('.'), value) : value };
}

const SECTIONS = [
  ['sources', 'Sources'], ['output', 'Output'], ['render', 'Rendering'], ['cut', 'Cut defaults'],
  ['ai', 'AI'], ['auto', 'Automation'], ['data', 'Filler data'],
];

const CSS = `
.settings .sub a { display:block; padding:6px 10px; border-radius:5px; color:var(--muted); font-size:13px; }
.settings .sub a:hover, .settings .sub a[aria-current=true] { color:var(--text); background:var(--panel2); }
.settings .srow { display:grid; grid-template-columns:minmax(0,220px) minmax(0,1fr); gap:8px 24px; align-items:start; padding:16px 0; border-top:1px solid #22262B; }
.settings .srow > .lbl { font-size:13px; font-weight:500; padding-top:8px; }
.settings .hint { display:block; font-weight:400; color:var(--muted); font-size:12px; margin-top:2px; }
.settings .inp { font:14px var(--font); color:var(--text); background:var(--bg); border:1px solid var(--line); border-radius:6px; height:36px; padding:0 10px; width:100%; max-width:420px; }
.settings .inp.mono { font-family:var(--mono); font-size:13px; }
.settings .inp:disabled { opacity:.6; }
.settings .stack10 { display:flex; flex-direction:column; gap:10px; }
.settings .ck { display:flex; gap:10px; align-items:center; font-size:13px; }
.settings .ck input { accent-color:var(--accent); margin:0; width:16px; height:16px; }
.settings h2.sh { margin:0 0 4px; font-size:17px; font-weight:700; }
@media (max-width: 640px) { .settings .srow { grid-template-columns:1fr; } }
`;

function Row({ label, hint, htmlFor, children }) {
  const head = <>{label}{hint && <span className="hint">{hint}</span>}</>;
  return (
    <div className="srow">
      {htmlFor ? <label className="lbl" htmlFor={htmlFor}>{head}</label> : <span className="lbl">{head}</span>}
      <div>{children}</div>
    </div>
  );
}

export function Settings() {
  const { data, error, loading, reload } = useLoad(() => api('/settings'));
  const fillers = useLoad(() => api('/fillers'));
  const [cfg, setCfg] = useState(null);
  const { act, msg, busy, clear } = useAct();
  const [tests, setTests] = useState({});
  const [active, setActive] = useState('sources');
  useEffect(() => { if (data) setCfg(data); }, [data]);

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
      <style>{CSS}</style>
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
            <Row label="Plex" hint="Optional. Shows and movies. Leave sections empty for every show library.">
              <div className="stack10">
                {input('library.plex.url', { 'aria-label': 'Plex URL', placeholder: 'http://plex:32400' })}
                {input('library.plex.token', { 'aria-label': 'Plex token', type: 'password', autoComplete: 'off', placeholder: 'Token' })}
                {testRow('plex', { url: lib.plex.url, token: lib.plex.token })}
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

          <section id="render" aria-labelledby="ren-h">
            <h2 id="ren-h" className="sh">Rendering</h2>
            <p className="muted tiny" style={{ margin: '0 0 8px' }}>Stream copy is used when files match; these apply to re-encodes.</p>
            {select('ffmpeg.container', 'Container', ['mkv', 'mp4'])}
            <Row label="Video encoder">
              <div style={{ paddingTop: 8 }}>{check('ffmpeg.nvenc', 'Use NVENC (h264_nvenc)')}</div>
            </Row>
            {text('ffmpeg.crf', 'Quality (CRF)', 'Lower is better and bigger. 18–23 is typical.', { type: 'number', min: 0, max: 51, className: 'inp mono', style: { width: 90 } })}
            {select('ffmpeg.preset', 'x264 preset', ['ultrafast', 'superfast', 'veryfast', 'faster', 'fast', 'medium', 'slow', 'slower', 'veryslow'])}
            {text('ffmpeg.audio_bitrate', 'Audio bitrate', null, { className: 'inp mono', style: { width: 120 } })}
            {text('ffmpeg.binary', 'ffmpeg binary', null, { className: 'inp mono' })}
            {text('ffmpeg.probe_binary', 'ffprobe binary', null, { className: 'inp mono' })}
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
            <Row label="Sonarr webhook" hint="Point a Sonarr Webhook connection at /api/webhooks/sonarr. No auth: keep FansCut on a trusted network.">
              <div className="stack10" style={{ paddingTop: 8 }}>
                {check('automation.sonarr_webhook', 'Accept Sonarr webhooks (new episode → detect + render)')}
                {check('automation.auto_detect', 'Run OP/ED detection first')}
                {check('automation.keep', 'Keep webhook renders')}
              </div>
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
        </div>
      </div>
    </div>
  );
}
