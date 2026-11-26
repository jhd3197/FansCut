import { useState } from 'react';
import { Link, useParams } from 'react-router';
import { api, seriesPath } from '../lib/api.js';
import { useAct, useLoad } from '../lib/hooks.js';
import { LABELS, SOURCE_NAMES } from '../lib/labels.js';
import { TRIM_KINDS, TRIM_NAMES } from '../lib/timeline.js';
import { fmtTime } from '../lib/time.js';
import { CutTile, Crumbs, KV, Section, enc } from '../components/kit.jsx';
import { ErrorBox, Loading, Notice } from '../components/ui.jsx';
import { CutBar, cutPieces } from '../components/Timeline.jsx';
import { TRIM_TITLES, TrimFields } from '../components/TrimFields.jsx';
import { SegmentsFields } from '../components/SegmentsFields.jsx';
import { useApi } from '../lib/store.js';

// -- probe text, shared with the show page -------------------------------------------
const LANGS = {
  eng: 'English', en: 'English', jpn: 'Japanese', ja: 'Japanese', fre: 'French', fra: 'French', fr: 'French',
  spa: 'Spanish', es: 'Spanish', ger: 'German', deu: 'German', de: 'German', ita: 'Italian', it: 'Italian',
  por: 'Portuguese', pt: 'Portuguese', kor: 'Korean', ko: 'Korean', chi: 'Chinese', zho: 'Chinese', zh: 'Chinese',
  rus: 'Russian', ru: 'Russian', dut: 'Dutch', nld: 'Dutch', pol: 'Polish', swe: 'Swedish', tur: 'Turkish',
  ara: 'Arabic', hin: 'Hindi', und: 'Unknown language',
};
const CODECS = {
  h264: 'H.264', hevc: 'HEVC', h265: 'HEVC', av1: 'AV1', vp9: 'VP9', mpeg4: 'MPEG-4', mpeg2video: 'MPEG-2',
  aac: 'AAC', ac3: 'AC-3', eac3: 'E-AC-3', dts: 'DTS', truehd: 'TrueHD', flac: 'FLAC', opus: 'Opus', mp3: 'MP3',
  ass: 'ASS', ssa: 'SSA', subrip: 'SRT', hdmv_pgs_subtitle: 'PGS', dvd_subtitle: 'VobSub', mov_text: 'MP4 text', webvtt: 'WebVTT',
};
export const langName = (code) => LANGS[(code || '').toLowerCase()] || (code ? code.toUpperCase() : 'Unknown language');
const codecName = (c) => CODECS[(c || '').toLowerCase()] || (c || '').toUpperCase();
const channels = (n) => (n === 1 ? '1.0' : n === 2 ? '2.0' : n === 6 ? '5.1' : n === 8 ? '7.1' : n ? `${n} ch` : '');
const isEnglish = (code) => ['eng', 'en'].includes((code || '').toLowerCase());
const and = (xs) => (xs.length < 2 ? xs.join('') : `${xs.slice(0, -1).join(', ')} and ${xs[xs.length - 1]}`);

// "Japanese and French. No English track." for the show page.
export function audioSummary(probe) {
  const tracks = probe?.audio_tracks || [];
  if (!tracks.length) return null;
  const langs = [...new Set(tracks.map((t) => langName(t.language)))];
  const english = tracks.some((t) => isEnglish(t.language));
  return { text: `${and(langs)}.${english ? '' : ' No English track.'}`, english };
}
export function subtitleSummary(probe) {
  const subs = probe?.subtitle_tracks || [];
  if (!subs.length) return 'None';
  return and([...new Set(subs.map((s) => langName(s.language) + (s.forced ? ' (forced)' : '')))]);
}

// The show's folder from one episode file: drop a "Season 01" / "Specials" level.
export function showFolder(file) {
  if (!file) return null;
  const sep = file.includes('\\') ? '\\' : '/';
  const parts = file.split(sep);
  parts.pop();
  if (/^(season|series|specials|s\d+$)/i.test(parts[parts.length - 1] || '')) parts.pop();
  return parts.join(sep);
}

const ext = (file) => (file && file.includes('.') ? file.split('.').pop().toUpperCase() : '');

function videoText(probe, file) {
  const v = probe?.video;
  if (!v) return '–';
  return [codecName(v.codec), v.width && `${v.width}×${v.height}`, v.hdr && v.hdr !== 'SDR' && v.hdr,
    v.fps && `${Math.round(v.fps * 100) / 100} fps`, ext(file)].filter(Boolean).join(' · ');
}

const seasonName = (n) => (n === 0 ? 'Specials' : `Season ${n}`);
const diff = (sec) => `−${fmtTime(sec)}`;

function Preview({ base, eid, what, onClose }) {
  const [edge, setEdge] = useState('start');
  const t = Math.max(what.range[edge], 0);
  return (
    <div className="card stack" style={{ padding: 16 }}>
      <div className="row wrap" style={{ justifyContent: 'space-between' }}>
        <b>{TRIM_TITLES[what.kind]} · 10 s around the {edge} ({fmtTime(t, 1)})</b>
        <div className="row">
          <div className="seg" role="radiogroup" aria-label="Preview edge">
            {[['start', 'Start'], ['end', 'End']].map(([v, n]) => (
              <button key={v} type="button" role="radio" aria-checked={edge === v} onClick={() => setEdge(v)}>{n}</button>
            ))}
          </div>
          <button type="button" className="btn btn-sm btn-ghost" onClick={onClose}>Close</button>
        </div>
      </div>
      <video key={`${what.kind}-${edge}-${t}`} controls autoPlay style={{ width: '100%', maxWidth: 640, borderRadius: 6, background: '#000' }}
        src={`/api${base}/episodes/${enc(eid)}/preview?t=${t.toFixed(2)}`} />
    </div>
  );
}

export function EpisodePage() {
  const { key, eid } = useParams();
  const base = seriesPath(key);
  const view = useApi(`${base}/episodes`);
  const info = useApi(`${base}/episodes/${enc(eid)}/info`);
  const season = Number((/^S(\d+)/.exec(eid) || [])[1] ?? 1);
  const plan = useLoad(() => api(`${base}/cut`, { method: 'POST', read: true, body: { seasons: [season] } }).catch(() => null), [key, season]);
  const { act, msg, busy, clear } = useAct();
  const [preview, setPreview] = useState(null);

  if ((view.error && !view.data) || (info.error && !info.data)) {
    return <div className="page"><ErrorBox error={view.error || info.error} onRetry={() => { view.reload(); info.reload(); }} /></div>;
  }
  if (!view.data || !info.data) return <div className="page"><Loading /></div>;

  const { series } = view.data;
  const ep = view.data.episodes.find((e) => e.id === eid);
  const i = info.data;
  const probe = i.probe;
  if (!ep) return <div className="page"><ErrorBox error={new Error(`${eid} is not in this show`)} /></div>;
  const episode = { ...ep, duration: ep.duration || i.duration || probe?.duration };
  const showTo = `/series/${enc(key)}`;
  const segs = plan.data ? plan.data.segments.filter((s) => s.episode_id === eid) : null;
  const d = episode.duration || 0;
  const kept = !episode.include ? 0 : segs ? segs.reduce((a, s) => a + s.duration, 0) : null;
  const pieces = cutPieces(episode, segs, episode.include);

  const reloadAll = () => { view.reload(); plan.reload(); };
  const call = (path, method, body, ok) => act(async () => { await api(`${base}/episodes/${enc(eid)}${path}`, { method, body }); reloadAll(); }, ok);
  const userTrims = TRIM_KINDS.filter((k) => episode.trims?.[k]?.source === 'user');
  const resetAll = () => act(async () => {
    for (const k of userTrims) await api(`${base}/episodes/${enc(eid)}/trims/${k}`, { method: 'DELETE' });
    reloadAll();
  }, 'Back to the detected ranges');

  // A trim the cut does not drop (the first-episode OP setting, a custom cut) gets a note.
  const notes = {};
  if (segs && episode.include) {
    for (const k of TRIM_KINDS) {
      const t = episode.trims?.[k];
      if (t?.start == null || pieces.some((p) => p.what === TRIM_NAMES[k])) continue;
      notes[k] = episode.segments ? 'kept by your own cut' : 'kept by a setting';
    }
  }

  const audio = probe?.audio_tracks || [];
  const english = audio.some((t) => isEnglish(t.language));
  const audioText = audio.length ? (
    <>{audio.map((t) => [langName(t.language), codecName(t.codec), channels(t.channels)].filter(Boolean).join(' ')).join(' · ')}
      {!english && <span className="c-accent"> · no English</span>}</>
  ) : '–';
  const subs = probe?.subtitle_tracks || [];
  const subText = subs.length ? subs.map((s) => [langName(s.language), codecName(s.codec), s.forced && 'forced'].filter(Boolean).join(' ')).join(' · ') : 'None';
  const year = (i.air_date || '').slice(0, 4);

  return (
    <div className="page" style={{ gap: 30 }}>
      <div className="toolbar">
        <Crumbs items={[['Library', '/library'], [series.title, showTo], [seasonName(ep.season), `${showTo}/season/${ep.season}`], [`Episode ${ep.number}`]]} />
        <div className="row">
          {i.prev ? <Link className="btn" to={`${showTo}/episode/${enc(i.prev)}`}>Previous</Link>
            : <button type="button" className="btn" disabled>Previous</button>}
          {i.next ? <Link className="btn" to={`${showTo}/episode/${enc(i.next)}`}>Next episode</Link>
            : <button type="button" className="btn" disabled>Next episode</button>}
        </div>
      </div>

      <Notice msg={msg} onClose={clear} />

      <div className="hero" style={{ gap: 28 }}>
        <div style={{ width: 320, maxWidth: '100%', flex: 'none', aspectRatio: '16 / 9', borderRadius: 6, background: 'var(--poster)', border: '1px solid var(--line)', display: 'flex', alignItems: 'flex-end', padding: 14, boxSizing: 'border-box' }}>
          <span className="mono tiny muted">{eid} · {fmtTime(d)}{!ep.has_file && <span className="c-danger"> · no file</span>}</span>
        </div>
        <div className="hero-body" style={{ gap: 14 }}>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
            <h1 style={{ margin: 0, font: '600 24px/1.2 var(--font)' }}>{ep.title || eid}</h1>
            <span className="mono muted" style={{ fontSize: 13 }}>{[eid, year, fmtTime(d), ep.label].filter(Boolean).join(' · ')}</span>
          </div>
          <KV rows={[
            ['Video', videoText(probe, i.file)],
            ['Audio', audioText],
            ['Subtitles', subText],
            ['File', i.file ? <span className="mono tiny">{i.file}</span> : <span className="c-danger">Not on disk</span>],
            ['Label', (
              <span className="row wrap">
                <select className="input" style={{ width: 'auto', padding: '3px 8px' }} aria-label={`Label for ${eid}`}
                  value={ep.override?.label || ''} onChange={(e) => call('/override', 'PUT', { label: e.target.value || null })}>
                  <option value="">auto ({ep.override?.label ? 'from data' : ep.label})</option>
                  {LABELS.map((l) => <option key={l} value={l}>{l}</option>)}
                </select>
                <span className="muted tiny">{SOURCE_NAMES[ep.label_source] || ep.label_source}</span>
              </span>
            )],
          ]} />
          <div className="row wrap" style={{ gap: 10 }}>
            <label className="check" style={{ fontSize: 14 }} title={ep.include_reason}>
              <input type="checkbox" checked={ep.include} disabled={busy} style={{ accentColor: 'var(--accent)', margin: 0, width: 16, height: 16 }}
                onChange={(e) => call('/override', 'PUT', { include: e.target.checked })} />
              Keep this episode in cuts
            </label>
            <span className="muted tiny">{ep.include_reason}</span>
            {ep.override?.include != null && (
              <button type="button" className="btn btn-sm btn-ghost" onClick={() => call('/override', 'PUT', { include: null })}>Use the preset’s choice</button>
            )}
          </div>
        </div>
      </div>

      <section className="section" aria-labelledby="e-trims">
        <div className="section-head" style={{ flexWrap: 'wrap', justifyContent: 'space-between' }}>
          <h2 id="e-trims">What gets cut</h2>
          <span className="mono" style={{ fontSize: 13 }}>
            {!ep.include ? <span className="muted">skipped in cuts ({ep.include_reason})</span>
              : kept == null ? <span className="muted">{fmtTime(d)}</span>
                : <><span className="muted">{fmtTime(d)} →</span> {fmtTime(kept)} <span className="c-accent">{diff(Math.max(d - kept, 0))}</span></>}
          </span>
        </div>
        <CutBar episode={episode} segments={segs} included={ep.include} height={28} radius={4} />
        <TrimFields episode={episode} notes={notes}
          save={(kind, start, end) => call(`/trims/${kind}`, 'PUT', { start, end })}
          reset={(kind) => call(`/trims/${kind}`, 'DELETE')}
          preview={ep.has_file ? (kind, range) => setPreview({ kind, range }) : undefined} />
        {preview && <Preview base={base} eid={eid} what={preview} onClose={() => setPreview(null)} />}
        <div className="row wrap">
          {i.cuts.length > 0 ? (
            <Link className="btn" to={`/cuts/${enc(i.cuts[0].id)}/edit`}
              title={`Edit this episode's place in “${i.cuts[0].title}”`}>Open in editor</Link>
          ) : (
            <Link className="btn" to={`${showTo}/season/${ep.season}`}>Back to {seasonName(ep.season).toLowerCase()}</Link>
          )}
          <button type="button" className="btn" disabled={busy || !userTrims.length} onClick={resetAll}
            title="Forget your edits to the trims of this episode">Reset to detected</button>
        </div>
      </section>

      <section className="section" aria-labelledby="e-own">
        <div className="section-head"><h2 id="e-own">My own cut</h2>{ep.segments && <span className="n">{ep.segments.length} segment(s)</span>}</div>
        <p className="muted" style={{ margin: 0, fontSize: 13 }}>
          Ranges to keep, used exactly as given. Once set, they beat the preset and the trims above. Unticking “Keep” still drops the episode.
        </p>
        <SegmentsFields key={JSON.stringify(ep.segments)} episode={episode}
          save={(segments) => call('/segments', 'PUT', { segments }, segments ? 'Segments saved' : 'Back to preset and trims')} />
      </section>

      <Section id="e-cuts" title="Used in" count={i.cuts.length}>
        {i.cuts.length === 0 ? <p className="muted" style={{ margin: 0 }}>No timeline uses this episode yet.</p> : (
          <div className="shelf">
            {i.cuts.map((c) => (
              <CutTile key={c.id} to={`/cuts/${enc(c.id)}`} title={c.title} sub={series.title} tag="Timeline" kind="order"
                meta={`${c.parts} part(s) · ${c.minutes} min`} status={c.synced ? 'ready' : undefined}
                note={c.synced ? 'Synced' : 'Not synced yet'} />
            ))}
          </div>
        )}
      </Section>
    </div>
  );
}
