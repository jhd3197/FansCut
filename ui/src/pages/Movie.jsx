import { useState } from 'react';
import { Link, useParams } from 'react-router';
import { ErrorBox, Loading } from '../components/ui.jsx';
import { Crumbs, KV, Section, enc, hm } from '../components/kit.jsx';
import { SuggestionTile, TimelineTile } from './Discover.jsx';
import { FilmTile, ShowTile } from './Collection.jsx';
import { movieTag } from './LibraryPage.jsx';
import { TemplateDialog } from '../components/TemplateDialog.jsx';
import { useApi } from '../lib/store.js';
import { usePageTitle } from '../lib/hooks.js';

const CODECS = {
  hevc: 'HEVC', h264: 'H.264', av1: 'AV1', vp9: 'VP9', mpeg2video: 'MPEG-2', mpeg4: 'MPEG-4',
  eac3: 'E-AC3', ac3: 'AC3', truehd: 'TrueHD', dts: 'DTS', aac: 'AAC', flac: 'FLAC', opus: 'Opus',
  mp3: 'MP3', pcm_s16le: 'PCM', pcm_s24le: 'PCM', subrip: 'SRT', ass: 'ASS', hdmv_pgs_subtitle: 'PGS',
  dvd_subtitle: 'VobSub', mov_text: 'Text',
};

const LANGS = {
  eng: 'English', en: 'English', jpn: 'Japanese', ja: 'Japanese', fre: 'French', fra: 'French', fr: 'French',
  ger: 'German', deu: 'German', de: 'German', spa: 'Spanish', es: 'Spanish', ita: 'Italian', it: 'Italian',
  por: 'Portuguese', pt: 'Portuguese', rus: 'Russian', ru: 'Russian', chi: 'Chinese', zho: 'Chinese',
  zh: 'Chinese', kor: 'Korean', ko: 'Korean', dut: 'Dutch', nld: 'Dutch', swe: 'Swedish', dan: 'Danish',
  nor: 'Norwegian', fin: 'Finnish', pol: 'Polish', hin: 'Hindi', ara: 'Arabic', tur: 'Turkish',
};

const codec = (c) => CODECS[c] || (c ? c.toUpperCase() : '?');
const lang = (l) => LANGS[(l || '').toLowerCase()] || (l && l !== 'und' ? l.toUpperCase() : 'Unknown language');

function videoLine(v) {
  if (!v) return 'No video stream';
  const depth = /1[02]le|p010|1[02]be/.test(v.pix_fmt || '') ? (/12/.test(v.pix_fmt) ? ' 12-bit' : ' 10-bit') : '';
  return [`${codec(v.codec)}${depth}`, v.width && `${v.width}×${v.height}`,
    v.hdr && v.hdr !== 'SDR' ? v.hdr : null, v.fps ? `${Math.round(v.fps * 1000) / 1000} fps` : null]
    .filter(Boolean).join(' · ');
}

function audioLine(tracks) {
  if (!tracks?.length) return 'No audio tracks';
  return tracks.map((t) => [lang(t.language), codec(t.codec), t.channels > 2 ? `${t.channels === 6 ? '5.1' : t.channels === 8 ? '7.1' : `${t.channels} ch`}` : null]
    .filter(Boolean).join(' ')).join(' · ');
}

function subsLine(tracks) {
  return tracks.map((t) => `${lang(t.language)}${t.forced ? ' (forced)' : ''}`).join(' · ');
}

function Health({ findings }) {
  const seen = new Set();
  const bad = findings.filter((f) => (f.severity === 'error' || f.severity === 'warn')
    && !seen.has(f.message) && seen.add(f.message));
  if (!bad.length) return <span className="c-ready">No problems found</span>;
  return (
    <span className="stack" style={{ gap: 4 }}>
      {bad.map((f, i) => (
        <span key={i} className={f.severity === 'error' ? 'c-danger' : 'c-warn'}>
          {f.message}{f.fix ? <span className="muted"> — {f.fix}</span> : null}
        </span>
      ))}
      <Link to="/health" className="tiny">Open library health</Link>
    </span>
  );
}

export function MoviePage() {
  const { key } = useParams();
  const [tpl, setTpl] = useState(false);
  const { data: m, error, loading, reload } = useApi(`/library/movies/${encodeURIComponent(key)}`);
  const sugg = useApi('/suggestions', { quiet: true });
  usePageTitle(m?.title);

  if (error && !m) return <div className="page"><Crumbs items={[['Library', '/library'], ['Movies', '/library#movies-h'], [key]]} /><ErrorBox error={error} onRetry={reload} /></div>;
  if (loading || !m) return <div className="page"><Loading what="Reading the file" /></div>;

  const p = m.probe;
  const minutes = m.minutes ?? (p?.duration ? p.duration / 60 : null);
  const meta = [m.year, minutes ? hm(minutes) : null, m.edition, m.tmdb_id ? `TMDB ${m.tmdb_id}` : null].filter(Boolean).join(' · ');
  const subs = p?.subtitle_tracks || [];
  const rows = [
    p && ['Video', videoLine(p.video)],
    p && ['Audio', audioLine(p.audio_tracks)],
    p && subs.length > 0 && ['Subtitles', subsLine(subs)],
    m.source_for?.length > 0 && ['Used by', <span key="u">This copy is the source for {m.source_for.join(', ')}</span>],
    m.other_versions?.length > 0 && ['Other versions', (
      <span key="v" className="row wrap" style={{ gap: 10 }}>
        {m.other_versions.map((v) => (
          <Link key={v.key} to={`/movies/${enc(v.key)}`}>{v.edition || 'Another copy'}{v.on_disk ? '' : ' (not on disk)'}</Link>
        ))}
      </span>
    )],
    ['File', m.file
      ? <span className="mono" style={{ fontSize: 12, overflowWrap: 'anywhere' }}>{m.file}</span>
      : <span className="c-warn">No file on disk</span>],
    ['Health', m.file && !p && !m.findings.length
      ? <span className="c-warn">Could not read the file</span>
      : <Health findings={m.findings} />],
  ];

  // suggestions that use this film and are not installed yet
  const installed = new Set(m.cuts.map((c) => c.title));
  const ideas = (sugg.data?.suggestions || []).filter((s) => s.keys?.includes(m.key) && !installed.has(s.title));
  const shelf = m.cuts.length + ideas.length;

  return (
    <div className="page" style={{ gap: 30 }}>
      <Crumbs items={[['Library', '/library'], ['Movies', '/library#movies-h'], [m.title]]} />

      <div className="hero" style={{ gap: 28 }}>
        <div className="pst pst-plain" style={{ width: 200, flex: 'none' }}>
          <span className="ptag">{movieTag(m)}</span>
          <span className="ttl-main" style={{ fontSize: 22 }}>{m.title}</span>
        </div>
        <div className="hero-body">
          <div className="stack" style={{ gap: 6 }}>
            <h1 className="hero-title" style={{ fontSize: 30 }}>{m.title}</h1>
            <span className="mono muted" style={{ fontSize: 13 }}>{meta}</span>
          </div>
          <KV rows={rows} />
          <div className="row wrap">
            <button type="button" className="btn" onClick={() => setTpl(true)}>New cut with this film</button>
          </div>
          {tpl && <TemplateDialog movies={[m.key]} template="watch-order" onClose={() => setTpl(false)} />}
        </div>
      </div>

      {shelf > 0 && (
        <Section id="m-cuts" title="Cuts and playlists with this film" count={shelf}>
          <div className="shelf">
            {m.cuts.map((c) => <TimelineTile key={c.id} c={c} />)}
            {ideas.map((s) => <SuggestionTile key={s.id} s={s} />)}
          </div>
        </Section>
      )}


      {(() => {
        const shows = [...new Map(m.collection_films.flatMap((c) => c.shows || []).map((s) => [s.key, s])).values()];
        return shows.length > 0 && (
          <Section id="m-also" title="Also in your library" count={shows.length}>
            <div className="shelf">{shows.map((s) => <ShowTile key={s.key} s={s} />)}</div>
          </Section>
        );
      })()}

      {m.collection_films.map((c, i) => (
        <Section key={c.name} id={`m-coll-${i}`} title={`In the collection: ${c.name}`} count={c.films.length}
          end={<Link to={`/collections/${encodeURIComponent(c.name)}`} className="tiny">Open collection</Link>}>
          <div className="shelf">
            {c.films.map((f, j) => <FilmTile key={f.key} m={f} tag={`${c.name} · ${j + 1}`} />)}
          </div>
        </Section>
      ))}
    </div>
  );
}
