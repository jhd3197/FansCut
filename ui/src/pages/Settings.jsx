import { useEffect, useState } from 'react';
import { api } from '../lib/api.js';
import { useAct, useLoad } from '../lib/hooks.js';
import { ErrorBox, Loading, Notice, PageHeader } from '../components/ui.jsx';

// Immutable set at a dotted path: set(cfg, 'library.sonarr.url', v)
export function setPath(obj, path, value) {
  const [head, ...rest] = path.split('.');
  return { ...obj, [head]: rest.length ? setPath(obj[head] ?? {}, rest.join('.'), value) : value };
}

function Section({ title, hint, children }) {
  return (
    <section className="card" style={{ marginBottom: 14 }}>
      <h2>{title}</h2>
      {hint && <p className="muted" style={{ marginTop: -4 }}>{hint}</p>}
      {children}
    </section>
  );
}

export function Settings() {
  const { data, error, loading, reload } = useLoad(() => api('/settings'));
  const fillers = useLoad(() => api('/fillers'));
  const [cfg, setCfg] = useState(null);
  const { act, msg, busy, clear } = useAct();
  const [tests, setTests] = useState({});
  useEffect(() => { if (data) setCfg(data); }, [data]);

  if (error && !data) return <ErrorBox error={error} onRetry={reload} />;
  if (loading || !cfg) return <Loading />;

  const set = (path) => (e) => {
    const v = e.target.type === 'checkbox' ? e.target.checked : e.target.type === 'number' ? Number(e.target.value) : e.target.value;
    setCfg((c) => setPath(c, path, v));
  };
  const text = (path, label, props = {}) => {
    const v = path.split('.').reduce((o, k) => o?.[k], cfg);
    const locked = cfg.env_locked?.includes(path);
    return <label className="field">{label}<input className="input" value={locked ? '' : v ?? ''} onChange={set(path)} {...props}
      {...(locked && { disabled: true, placeholder: 'set by environment' })} /></label>;
  };
  const select = (path, label, options) => {
    const v = path.split('.').reduce((o, k) => o?.[k], cfg);
    return <label className="field">{label}<select className="input" value={v} onChange={set(path)}>{options.map((o) => <option key={o}>{o}</option>)}</select></label>;
  };
  const maps = cfg.library.path_mappings;
  const setMaps = (next) => setCfg((c) => setPath(c, 'library.path_mappings', next));

  const save = () => act(async () => { setCfg(await api('/settings', { method: 'PUT', body: cfg })); }, 'Settings saved');
  const test = async (service, body) => {
    setTests((t) => ({ ...t, [service]: { text: 'testing…' } }));
    try {
      const r = await api(`/settings/test/${service}`, { method: 'POST', body });
      setTests((t) => ({ ...t, [service]: { ok: r.ok, text: r.message } }));
    } catch (e) {
      setTests((t) => ({ ...t, [service]: { ok: false, text: e.message } }));
    }
  };
  const result = (s) => tests[s] && <span className={tests[s].ok ? 'tag tag-ok' : 'tag tag-bad'}>{tests[s].text}</span>;
  const refreshFillers = () => act(async () => {
    const r = await api('/fillers/refresh', { method: 'POST' });
    await fillers.reload();
    if (!r.ok) throw new Error(r.message);
    return r;
  }, (r) => r.message || 'Filler data refreshed');
  const f = fillers.data;

  return (
    <>
      <PageHeader title="Settings" sub="Saved to config.yaml. Host and port changes need a restart.">
        <button className="btn btn-primary" onClick={save} disabled={busy}>Save</button>
      </PageHeader>
      <Notice msg={msg} onClose={clear} />

      <Section title="Sonarr" hint="Optional. Series, episodes and file paths.">
        <div className="form-grid">{text('library.sonarr.url', 'URL', { placeholder: 'http://sonarr:8989' })}
          {text('library.sonarr.api_key', 'API key', { type: 'password', autoComplete: 'off' })}</div>
        <div className="row" style={{ marginTop: 10 }}>
          <button className="btn btn-sm" onClick={() => test('sonarr', { url: cfg.library.sonarr.url, api_key: cfg.library.sonarr.api_key })}>Test connection</button>{result('sonarr')}
        </div>
      </Section>
      <Section title="Plex" hint="Optional. Leave sections empty for every show library.">
        <div className="form-grid">{text('library.plex.url', 'URL', { placeholder: 'http://plex:32400' })}
          {text('library.plex.token', 'Token', { type: 'password', autoComplete: 'off' })}</div>
        <div className="row" style={{ marginTop: 10 }}>
          <button className="btn btn-sm" onClick={() => test('plex', { url: cfg.library.plex.url, token: cfg.library.plex.token })}>Test connection</button>{result('plex')}
        </div>
      </Section>
      <Section title="Media folder" hint="Plain mode: one subfolder per series, no Sonarr needed.">
        <div className="form-grid">{text('library.folder.path', 'Folder', { placeholder: '/media' })}</div>
        <div className="row" style={{ marginTop: 10 }}>
          <button className="btn btn-sm" onClick={() => test('folder', { path: cfg.library.folder.path })}>Check folder</button>{result('folder')}
        </div>
      </Section>
      <Section title="Path mapping" hint="Sonarr and Plex report paths as they see them. Map the prefix to where FansCut sees the same files.">
        {maps.map((m, i) => (
          <div key={i} className="row" style={{ marginBottom: 8 }}>
            <input className="input" aria-label={`Remote prefix ${i + 1}`} placeholder="remote, e.g. /tv" value={m.remote}
              onChange={(e) => setMaps(maps.map((x, j) => (j === i ? { ...x, remote: e.target.value } : x)))} />
            <span className="muted">→</span>
            <input className="input" aria-label={`Local prefix ${i + 1}`} placeholder="local, e.g. /media/tv" value={m.local}
              onChange={(e) => setMaps(maps.map((x, j) => (j === i ? { ...x, local: e.target.value } : x)))} />
            <button className="btn btn-sm" aria-label={`Remove mapping ${i + 1}`} onClick={() => setMaps(maps.filter((_, j) => j !== i))}>✕</button>
          </div>
        ))}
        <button className="btn btn-sm" onClick={() => setMaps([...maps, { remote: '', local: '' }])}>Add mapping</button>
      </Section>
      <Section title="Output">
        <div className="form-grid">{text('paths.output_dir', 'Output folder')}
          <label className="field">Data folder<input className="input" value={cfg.paths.data_dir} disabled /></label></div>
      </Section>
      <Section title="ffmpeg" hint="Stream copy is used when files match; these apply to re-encodes.">
        <div className="form-grid">
          {text('ffmpeg.binary', 'ffmpeg binary')}{text('ffmpeg.probe_binary', 'ffprobe binary')}
          {select('ffmpeg.container', 'Container', ['mkv', 'mp4'])}
          {text('ffmpeg.crf', 'CRF', { type: 'number', min: 0, max: 51 })}
          {select('ffmpeg.preset', 'x264 preset', ['ultrafast', 'superfast', 'veryfast', 'faster', 'fast', 'medium', 'slow', 'slower', 'veryslow'])}
          {text('ffmpeg.audio_bitrate', 'Audio bitrate')}
          <label className="check" style={{ alignSelf: 'end', paddingBottom: 8 }}>
            <input type="checkbox" checked={cfg.ffmpeg.nvenc} onChange={set('ffmpeg.nvenc')} /> use NVENC (h264_nvenc)
          </label>
        </div>
      </Section>
      <Section title="Cut defaults" hint="Used for every series unless it has its own settings.">
        <div className="form-grid">
          {select('cut.preset', 'Preset', ['canon_only', 'canon_mixed', 'everything', 'custom'])}
          {select('cut.unknown_policy', 'Unlabelled episodes', ['keep', 'skip'])}
          {text('cut.pad', 'Pad (s)', { type: 'number', min: 0, step: 0.5 })}
          {text('cut.merge_gap', 'Merge gap (s)', { type: 'number', min: 0, step: 0.5 })}
          {text('cut.min_segment', 'Min segment (s)', { type: 'number', min: 0, step: 0.5 })}
          {select('cut.transition', 'Transition', ['none', 'fade'])}
          <label className="check" style={{ alignSelf: 'end', paddingBottom: 8 }}>
            <input type="checkbox" checked={cfg.cut.keep_intro_first_episode} onChange={set('cut.keep_intro_first_episode')} /> keep the OP in the first episode
          </label>
        </div>
      </Section>
      <Section title="Filler data" hint="Downloaded at runtime from the AniFiller project; nothing is bundled. Check their terms before relying on it.">
        <label className="check" style={{ marginBottom: 10 }}>
          <input type="checkbox" checked={cfg.catalogues.offline} onChange={set('catalogues.offline')} /> offline mode (never call AniList, Jikan or GitHub)
        </label>
        <div className="row wrap">
          <button className="btn" onClick={refreshFillers} disabled={busy}>Refresh filler data</button>
          {f && <span className="muted">{f.shows} show(s) in {f.files} file(s) · <span className="mono">{f.dir}</span></span>}
        </div>
      </Section>
    </>
  );
}
