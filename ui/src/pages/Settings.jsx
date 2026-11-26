import { useEffect, useRef, useState } from 'react';
import { Link, useLocation, useSearchParams } from 'react-router';
import { api } from '../lib/api.js';
import { useAct } from '../lib/hooks.js';
import { ErrorBox, Loading, Notice } from '../components/ui.jsx';
import { EditTag, Switch } from '../components/v2.jsx';
import './Settings.css';
import { useApi } from '../lib/store.js';

// Immutable set at a dotted path: set(cfg, 'library.sonarr.url', v)
export function setPath(obj, path, value) {
  const [head, ...rest] = path.split('.');
  return { ...obj, [head]: rest.length ? setPath(obj[head] ?? {}, rest.join('.'), value) : value };
}

const SECTIONS = [
  ['sources', 'Sources'], ['output', 'Output'], ['plex-out', 'Add to Plex'], ['render', 'Rendering'], ['cut', 'Cut defaults'],
  ['ai', 'AI'], ['tags', 'Tags'], ['markers', 'Markers'], ['posters', 'Posters'], ['auto', 'Automation'], ['data', 'Filler data'], ['system', 'Caches & network'],
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

// A tag's display name: never the fc: id ("fc:filler" -> "filler" when the vocab has no name).
export const tagName = (t) => t.name || String(t.id ?? t).replace(/^fc:/, '').replace(/-/g, ' ');
// Families switched off are listed; switching one on removes it.
export const toggleIn = (list, item, on) => (on ? list.filter((x) => x !== item) : [...new Set([...list, item])]);
const cap = (s) => s.charAt(0).toUpperCase() + s.slice(1);

const DETECT = [['auto', 'Found automatically'], ['ai', 'Checked by AI'], ['person', 'Needs a person']];
const EXTEND = [
  ['none', 'Don’t extend'],
  ['ambient', 'Add ambient and establishing shots'],
  ['filler', 'Also add filler from the same franchise'],
];

// A small radio group bound to one setting.
function Choice({ name, value, options, onChange }) {
  return (
    <div className="choice" role="radiogroup" aria-label={name}>
      {options.map(([v, label]) => (
        <label key={v} className="ck">
          <input type="radio" name={name} checked={value === v} onChange={() => onChange(v)} /> {label}
        </label>
      ))}
    </div>
  );
}

// The occasion tags: pick one to see what it looks and listens for, and whether it is searched closely.
function OccasionBox({ tags, on, onToggle, closely, setClosely }) {
  const [pick, setPick] = useState(null);
  const sel = tags.find((t) => t.id === pick) || tags[0];
  if (!sel) return null;
  const close = closely.includes(sel.id);
  const nClose = tags.filter((t) => closely.includes(t.id)).length;
  const looks = sel.cues?.see?.length ? `${cap(sel.cues.see.join(', '))}.` : sel.hint;
  return (
    <section className="set-occ" aria-label="Occasion tags">
      <div className="set-group-head">
        <Switch on={on} onChange={onToggle} label="Use Occasion tags when analysing films" />
        <span className="hd set-group-name">Occasion</span>
        <span className="mono set-n">{tags.length} tags · {nClose} searched closely</span>
      </div>
      <div className={`set-occ-body${on ? '' : ' is-off'}`}>
        <div className="set-occ-tags">
          {tags.map((t) => (
            <button key={t.id} type="button" className={`set-tg${t.id === sel.id ? ' is-on' : ''}`} aria-pressed={t.id === sel.id}
              onClick={() => setPick(t.id)}>
              {closely.includes(t.id) && <span className="set-close-dot" aria-label="searched closely" />}
              {tagName(t)}
            </button>
          ))}
          <span className="set-legend"><span className="set-close-dot" aria-hidden="true" />searched closely</span>
        </div>
        <div className="set-occ-pick">
          <div className="hd set-occ-name">{tagName(sel)}</div>
          {looks && <div><div className="lab">Looks for</div><div className="set-looks">{looks}</div></div>}
          {sel.cues?.hear?.length > 0 && (
            <div>
              <div className="lab">Listens for</div>
              <div className="set-hears">{sel.cues.hear.map((w) => <span key={w} className="mono">{w}</span>)}</div>
            </div>
          )}
          <div className="set-line">
            <Switch on={close} disabled={!on} label="Search closely"
              onChange={(v) => setClosely(v ? [...new Set([...closely, sel.id])] : closely.filter((x) => x !== sel.id))} />
            <span>Search closely</span>
          </div>
        </div>
      </div>
    </section>
  );
}

function TagsSection({ get, put }) {
  const vocab = useApi('/scenes/vocab', { quiet: true });
  const [draft, setDraft] = useState('');
  const v = vocab.data;
  const off = get('scenes.families_off') || [];
  const custom = get('scenes.custom_tags') || [];
  const closely = get('scenes.search_closely') ?? ['christmas', 'halloween'];
  const auto = get('scenes.auto_accept') ?? 0.75;
  const add = () => {
    const t = draft.trim();
    if (!t) return;
    if (!custom.includes(t)) put('scenes.custom_tags', [...custom, t]);
    setDraft('');
  };
  const family = (f) => (v?.tags || []).filter((t) => t.family === f);
  const flip = (f) => (on) => put('scenes.families_off', toggleIn(off, f, on));
  return (
    <section id="tags" aria-labelledby="tags-h" className="set-sec">
      <h2 id="tags-h">Tags</h2>
      {!v && !vocab.loading && <div className="set-empty">The tag list is not available.</div>}
      {!v && vocab.loading && <Loading />}
      {v && (v.families || []).includes('occasion') && (
        <OccasionBox tags={family('occasion')} on={!off.includes('occasion')} onToggle={flip('occasion')}
          closely={closely} setClosely={(x) => put('scenes.search_closely', x)} />
      )}
      {v && (
        <section aria-label="Tag groups" className="set-groups">
          {(v.families || []).filter((f) => f !== 'occasion').map((f) => {
            const on = !off.includes(f);
            const ts = family(f);
            return (
              <div key={f} className="set-group">
                <div className="set-group-head">
                  <Switch on={on} onChange={flip(f)} label={`Use ${cap(f)} tags when analysing films`} />
                  <span className="hd set-group-name">{cap(f)}</span>
                  <span className="mono set-n">{ts.length}</span>
                </div>
                <div className={`set-chips${on ? '' : ' is-off'}`}>
                  {ts.map((t) => <span key={t.id} className="set-tg" title={t.hint}>{tagName(t)}</span>)}
                </div>
              </div>
            );
          })}
        </section>
      )}
      {v && (v.cut_tags || []).length > 0 && (
        <section aria-labelledby="tags-made">
          <h3 id="tags-made" className="hd set-h3">Tags FansCut makes itself</h3>
          <div className="set-cols">
            {DETECT.map(([d, label]) => {
              const ts = (v.cut_tags || []).filter((t) => t.detect === d);
              if (!ts.length) return null;
              return (
                <div key={d} className="set-card">
                  <div className="lab">{label}</div>
                  {ts.map((t) => (
                    <div key={t.id} className="set-made">
                      <span className="set-tg" title={t.hint}>{tagName(t)}</span>
                      {t.how && <div className="set-how">{cap(t.how)}.</div>}
                    </div>
                  ))}
                </div>
              );
            })}
          </div>
        </section>
      )}
      <section aria-label="How tags are used" className="set-cols set-use">
        <div className="set-card">
          <label htmlFor="set-auto-accept" className="hd set-card-name">AI moments</label>
          <div className="set-line">
            <input id="set-auto-accept" type="range" min="50" max="100" step="5" className="set-range"
              value={Math.round(auto * 100)} onChange={(e) => put('scenes.auto_accept', Number(e.target.value) / 100)} />
            <output className="mono" htmlFor="set-auto-accept">{auto >= 1 ? 'Never' : `${Math.round(auto * 100)}%`}</output>
          </div>
          <div className="set-how">This sure or more: a tag straight away, marked AI. Below: a proposal.</div>
        </div>
        <div className="set-card">
          <div className="set-line"><span className="hd set-card-name">Signals</span>
            <span className="set-push"><Switch on={!!get('scenes.signals')} onChange={(x) => put('scenes.signals', x)} label="Measure signals after indexing a film" /></span>
          </div>
          <div className="set-looks">Measure signals after indexing a film</div>
          <div className="set-how">Faces, shots, brightness and sound. Used to pick posters and extend cuts; never shown as tags.</div>
        </div>
        <div className="set-card" role="radiogroup" aria-label="Extending cuts">
          <div className="hd set-card-name">Extending cuts</div>
          {EXTEND.map(([x, label]) => {
            const on = (get('scenes.extend') ?? 'ambient') === x;
            return (
              <button key={x} type="button" role="radio" aria-checked={on} className={`set-opt${on ? ' is-on' : ''}`}
                onClick={() => put('scenes.extend', x)}>
                <span className="set-radio" aria-hidden="true" />{label}
              </button>
            );
          })}
        </div>
        <div className="set-card">
          <label htmlFor="set-custom-tag" className="hd set-card-name">Your own tags</label>
          {custom.length > 0 && (
            <div className="tags-row">
              {custom.map((t) => <EditTag key={t} name={t} onRemove={() => put('scenes.custom_tags', custom.filter((x) => x !== t))} />)}
            </div>
          )}
          <div className="set-line">
            <input id="set-custom-tag" className="inp" placeholder="great hall" maxLength={60}
              value={draft} onChange={(e) => setDraft(e.target.value)}
              onKeyDown={(e) => { if (e.key === 'Enter') { e.preventDefault(); add(); } }} />
            <button className="btn" type="button" onClick={add} disabled={!draft.trim()}>Add</button>
          </div>
          <div className="set-how">Kept on this server, never shared.</div>
        </div>
      </section>
    </section>
  );
}

function MarkersSection({ check, text }) {
  return (
    <section id="markers" aria-labelledby="markers-h">
      <h2 id="markers-h" className="sh">Markers</h2>
      <Row label="Community markers" hint="Tags other people made for films you own.">
        <div className="stack10" style={{ paddingTop: 8 }}>
          {check('markers.check', 'Check for newer markers daily')}
          {check('markers.auto_install', 'Install new markers without asking')}
        </div>
      </Row>
      <Row label="Share yours" hint="Timestamps and tags only.">
        <div className="stack10" style={{ paddingTop: 8 }}>
          {check('markers.share', 'Offer my markers to the community index')}
          {check('markers.reviewed_only', 'Only moments I reviewed')}
          {check('markers.share_people', 'Include who is on screen')}
        </div>
      </Row>
      {text('markers.index_url', 'Index', null, { className: 'inp mono' })}
    </section>
  );
}

const LAYOUTS = [['frame', 'Frame'], ['strip', 'Strip'], ['grid', 'Grid']];
const STILLS = [[2, '2'], [4, '4'], [6, '6']];
const QUALITY = [['low', 'Low'], ['medium', 'Medium'], ['high', 'High']];
const WEIGHTS = [['exposure', 'Exposure'], ['contrast', 'Contrast'], ['color', 'Colour'], ['detail', 'Detail']];

function PostersSection({ get, put, check }) {
  return (
    <section id="posters" aria-labelledby="posters-h">
      <h2 id="posters-h" className="sh">Posters</h2>
      <Row label="Layout">
        <div style={{ paddingTop: 8 }}>
          <Choice name="Layout" value={get('posters.layout') ?? 'frame'} options={LAYOUTS} onChange={(x) => put('posters.layout', x)} />
        </div>
      </Row>
      <Row label="Title">
        <div style={{ paddingTop: 8 }}>{check('posters.title', 'Title on the poster')}</div>
      </Row>
      <Row label="Stills per scene">
        <div style={{ paddingTop: 8 }}>
          <Choice name="Stills per scene" value={Number(get('posters.stills') ?? 4)} options={STILLS} onChange={(x) => put('posters.stills', x)} />
        </div>
      </Row>
      <Row label="Scoring" hint="How a still is picked.">
        <div className="sliders" style={{ paddingTop: 8 }}>
          {WEIGHTS.map(([k, label]) => {
            const val = Number(get(`posters.weights.${k}`) ?? 0);
            return (
              <label key={k} htmlFor={`set-w-${k}`}>
                <span>{label}</span>
                <input id={`set-w-${k}`} type="range" min="0" max="50" value={val}
                  onChange={(e) => put(`posters.weights.${k}`, Number(e.target.value))} />
                <output className="mono tiny" htmlFor={`set-w-${k}`}>{val}</output>
              </label>
            );
          })}
        </div>
      </Row>
      <Row label="AI posters" hint="Draws a poster from the cut's stills. The key comes from the environment (OPENAI_API_KEY for openai/…). Empty = off." htmlFor="set-ai-poster">
        <div className="stack10">
          <input id="set-ai-poster" className="inp mono" placeholder="openai/gpt-image-2" value={get('posters.ai_model') ?? ''}
            onChange={(e) => put('posters.ai_model', e.target.value)} />
          <Choice name="Quality" value={get('posters.ai_quality') ?? 'medium'} options={QUALITY} onChange={(x) => put('posters.ai_quality', x)} />
        </div>
      </Row>
    </section>
  );
}

export function Settings() {
  const { data, error, loading, reload } = useApi('/settings');
  const fillers = useApi('/fillers');
  const [cfg, setCfg] = useState(null);
  const { act, msg, busy, clear } = useAct();
  const [tests, setTests] = useState({});
  // one section at a time, picked by ?section= (links elsewhere open e.g. ?section=render)
  // (older links use a hash, e.g. /settings#ai)
  const [params] = useSearchParams();
  const { hash } = useLocation();
  const asked = params.get('section') || hash.slice(1);
  const active = SECTIONS.some(([id]) => id === asked) ? asked : 'sources';
  // seed the form once; background refetches must not wipe unsaved edits
  useEffect(() => { if (data && !cfg) setCfg(data); }, [data, cfg]);
  // check the saved connections once, so each source shows whether it is connected
  const probed = useRef(false);
  useEffect(() => {
    if (!data || probed.current) return;
    probed.current = true;
    const lib0 = data.library || {};
    for (const svc of ['plex', 'sonarr', 'radarr']) {
      if (!lib0[svc]?.url) continue;
      setTests((t) => ({ ...t, [svc]: { text: 'Checking' } }));
      api(`/settings/test/${svc}`, { method: 'POST', body: {} })
        .then((r) => setTests((t) => ({ ...t, [svc]: { ok: r.ok, text: r.message } })))
        .catch((e) => setTests((t) => ({ ...t, [svc]: { ok: false, text: e.message } })));
    }
  }, [data]);

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
  const put = (path, v) => setCfg((c) => setPath(c, path, v));
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
  const testNote = (service) => tests[service] && tests[service].ok !== true && (
    <span role="status" className={`set-how${tests[service].ok === false ? ' c-danger' : ''}`}>{tests[service].text}</span>
  );
  const appState = (service, url) => {
    const t = tests[service];
    if (t?.ok === true) return <span className="state state-ready" title={t.text}>Connected</span>;
    if (t?.ok === false) return <span className="state state-look">Not connected</span>;
    if (t) return <span className="state">Checking</span>;
    return <span className="state">{url ? 'Not checked' : 'Not set up'}</span>;
  };
  const appCard = (service, name, desc, urlPath, urlHint, keyPath, keyLabel, body, extra) => (
    <div className="set-card set-app">
      <div>
        <div className="set-line"><span className="hd set-card-name">{name}</span><span className="set-push">{appState(service, get(urlPath))}</span></div>
        <div className="set-how set-desc">{desc}</div>
      </div>
      <div><label className="set-fl" htmlFor={idOf(urlPath)}>Address</label>{input(urlPath, { placeholder: urlHint, className: 'inp mono' })}</div>
      <div><label className="set-fl" htmlFor={idOf(keyPath)}>{keyLabel}</label>{input(keyPath, { type: 'password', autoComplete: 'off', className: 'inp mono' })}</div>
      <div className="set-line set-wrap">
        <button className="btn btn-sm" type="button" onClick={() => test(service, body)}>Test</button>
        {extra}
      </div>
      {testNote(service)}
    </div>
  );
  const refreshFillers = () => act(async () => {
    const r = await api('/fillers/refresh', { method: 'POST' });
    await fillers.reload();
    if (!r.ok) throw new Error(r.message);
    return r;
  }, (r) => r.message || 'Filler data refreshed');
  const f = fillers.data;
  const lib = cfg.library;

  return (
    <div className="page settings">
      <header className="set-head">
        <div>
          <h1>Settings</h1>
          <div className="set-how">Saved to config.yaml. Host and port changes need a restart.</div>
        </div>
        <button className="btn btn-primary" type="button" onClick={save} disabled={busy}>Save changes</button>
      </header>
      <Notice msg={msg} onClose={clear} />
      <div className="set-body">
        <nav className="sub" aria-label="Settings sections">
          {SECTIONS.map(([id, name]) => (
            <Link key={id} to={{ search: id === 'sources' ? '' : `?section=${id}` }} aria-current={active === id ? 'page' : undefined}>{name}</Link>
          ))}
        </nav>
        <div className="set-main">
          {active === 'sources' && (<section id="sources" aria-labelledby="src-h" className="set-sec">
            <div className="set-sec-head">
              <h2 id="src-h">Sources</h2>
              <div className="set-find">
                <span className="set-how">Reads Plex, Radarr and Sonarr&apos;s own settings on this computer and fills any empty URL or key.</span>
                <button className="btn" type="button" disabled={busy} onClick={detect}>Find apps on this computer</button>
              </div>
            </div>
            <div className="set-cols">
              {appCard('plex', 'Plex', 'Shows and movies. Leave sections empty for every show library.',
                'library.plex.url', 'http://plex:32400', 'library.plex.token', 'Token', { url: lib.plex.url, token: lib.plex.token },
                <PlexSignIn onDone={(r) => setCfg((c) => setPath(setPath(c, 'library.plex.token', '***'), 'library.plex.url', r.url || c.library.plex.url))} />)}
              {appCard('sonarr', 'Sonarr', 'Series, episodes and file paths, plus webhooks.',
                'library.sonarr.url', 'http://sonarr:8989', 'library.sonarr.api_key', 'API key', { url: lib.sonarr.url, api_key: lib.sonarr.api_key })}
              {appCard('radarr', 'Radarr', 'Movie files and editions, as timeline sources.',
                'library.radarr.url', 'http://radarr:7878', 'library.radarr.api_key', 'API key', { url: lib.radarr.url, api_key: lib.radarr.api_key })}
            </div>
            <div className="set-cols set-cols-2">
              <div className="set-card">
                <div>
                  <div className="hd set-card-name">Media folders</div>
                  <div className="set-how">Plain mode, no Sonarr needed: one subfolder per series. Movies: one subfolder per movie; &apos;{'{tmdb-123}'}&apos; in the name sets the id.</div>
                </div>
                <div><label className="set-fl" htmlFor={idOf('library.folder.path')}>Series folder</label>
                  {input('library.folder.path', { placeholder: '/media', className: 'inp mono' })}</div>
                <div><label className="set-fl" htmlFor={idOf('library.movies.path')}>Movies folder</label>
                  {input('library.movies.path', { placeholder: '/movies', className: 'inp mono' })}</div>
                <div className="set-line">
                  <button className="btn btn-sm" type="button" onClick={() => test('folder', { path: lib.folder.path })}>Check folder</button>
                  {testNote('folder')}
                </div>
              </div>
              <div className="set-card">
                <div>
                  <div className="hd set-card-name">Path mapping</div>
                  <div className="set-how">When a server sees files under a different path than FansCut does.</div>
                </div>
                {maps.length === 0 && <div className="set-empty">No mappings yet.</div>}
                {maps.map((m, i) => (
                  <div key={i} className="set-map">
                    <input className="inp mono" aria-label={`Remote prefix ${i + 1}`} placeholder="remote, e.g. /tv" value={m.remote}
                      onChange={(e) => setMaps(maps.map((x, j) => (j === i ? { ...x, remote: e.target.value } : x)))} />
                    <span className="set-how" aria-hidden="true">→</span>
                    <input className="inp mono" aria-label={`Local prefix ${i + 1}`} placeholder="local, e.g. /media/tv" value={m.local}
                      onChange={(e) => setMaps(maps.map((x, j) => (j === i ? { ...x, local: e.target.value } : x)))} />
                    <button className="btn btn-sm btn-quiet" type="button" aria-label={`Remove mapping ${i + 1}`}
                      onClick={() => setMaps(maps.filter((_, j) => j !== i))}>Remove</button>
                  </div>
                ))}
                <div><button className="btn btn-sm" type="button" onClick={() => setMaps([...maps, { remote: '', local: '' }])}>Add mapping</button></div>
              </div>
            </div>
          </section>)}

          {active === 'output' && (<section id="output" aria-labelledby="out-h">
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
          </section>)}

          {active === 'plex-out' && (<section id="plex-out" aria-labelledby="plexout-h">
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
          </section>)}

          {active === 'render' && (<section id="render" aria-labelledby="ren-h">
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
          </section>)}

          {active === 'cut' && (<section id="cut" aria-labelledby="cut-h">
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
          </section>)}

          {active === 'ai' && (<section id="ai" aria-labelledby="ai-h">
            <h2 id="ai-h" className="sh">AI</h2>
            <p className="muted tiny" style={{ margin: '0 0 8px' }}>Turns cut notes into recipes and tags scenes. Models go through Prompture: claude/…, google/…, ollama/…, or a bare name for the OpenAI-compatible endpoint in Base URL.</p>
            {text('ai.model', 'Model', 'Provider and name, e.g. claude/claude-opus-5-5 or ollama/qwen3:8b.', { placeholder: 'ollama/qwen3:8b', className: 'inp mono' })}
            {text('ai.vision_model', 'Vision model', 'Looks at frame grids when tagging scenes. Empty uses Model.', { placeholder: 'ollama/qwen3-vl:8b', className: 'inp mono' })}
            {text('ai.base_url', 'Base URL', 'Only for a bare model name (OpenAI-compatible) or a remote Ollama.', { placeholder: 'http://localhost:11434/v1', className: 'inp mono' })}
            {text('ai.api_key', 'API key', 'Prefer FANSCUT_AI_API_KEY over the config file.', { type: 'password', autoComplete: 'off' })}
            {text('catalogues.tmdb_api_key', 'TMDB key', 'Cast for films outside Plex (scene tagging). Prefer FANSCUT_TMDB_API_KEY.', { type: 'password', autoComplete: 'off' })}
            {text('ai.timeout', 'Timeout', 'Seconds.', { type: 'number', min: 5, className: 'inp mono', style: { width: 90 } })}
          </section>)}

          {active === 'tags' && <TagsSection get={get} put={put} />}
          {active === 'markers' && <MarkersSection check={check} text={text} />}
          {active === 'posters' && <PostersSection get={get} put={put} check={check} />}

          {active === 'auto' && (<section id="auto" aria-labelledby="auto-h">
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
          </section>)}

          {active === 'data' && (<section id="data" aria-labelledby="data-h">
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
          </section>)}

          {active === 'system' && (<section id="system" aria-labelledby="system-h">
            <h2 id="system-h" className="sh">Caches & network</h2>
            <Row label="Caches"><CachePanel /></Row>
            <Row label="Remote art" hint="Fetch TMDB/TVDB posters through FansCut and keep them, so browsers never contact those sites. Good for offline or private setups.">
              <div style={{ paddingTop: 8 }}>{check('art.cache_remote', 'Cache TMDB and TVDB art here')}</div>
            </Row>
            {text('art.cache_mb', 'Art cache size', 'MB. The oldest images go first past this.', { type: 'number', min: 16, className: 'inp mono', style: { width: 110 } })}
            {text('network.timeout', 'Server timeout', 'Seconds to wait for Sonarr, Radarr and Plex.', { type: 'number', min: 2, className: 'inp mono', style: { width: 90 } })}
            {text('network.proxy', 'Outbound proxy', 'For internet calls (catalogues, GitHub, TMDB). Empty = none.', { placeholder: 'http://proxy:3128', className: 'inp mono' })}
            <Row label="Proxy bypass"><div style={{ paddingTop: 8 }}>{check('network.proxy_bypass_local', 'Reach LAN servers (Plex, Sonarr, Radarr) directly')}</div></Row>
          </section>)}
        </div>
      </div>
    </div>
  );
}
