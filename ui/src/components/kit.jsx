// Shared building blocks of the FansCut design: posters, tiles, sections, crumbs.
import { useCallback, useEffect, useRef, useState } from 'react';
import { Link } from 'react-router';
import { artUrl } from '../lib/api.js';

// Poster palettes: [background, shade, accent, title colour]. Picked by a hash of the title,
// so a cut always gets the same art. No images: the recipe never ships artwork.
const PALETTES = [
  ['#17301F', '#1F4028', '#E2A83A', '#F3E7C9'],
  ['#191226', '#2A1D3D', '#C9A35A', '#EFE6D8'],
  ['#1B2A1F', '#24382A', '#D9A441', '#E9E2C8'],
  ['#06070A', '#14161C', '#F2C14E', '#F2F2F2'],
  ['#1E1A14', '#2B241A', '#F0C419', '#F4EDE4'],
  ['#5E1A17', '#7A2420', '#F1E9DC', '#F1E9DC'],
  ['#102A33', '#163A45', '#6CC5CC', '#E8F4F4'],
  ['#2A1A10', '#3A2416', '#E8A15A', '#F4E4D2'],
  ['#14182A', '#1E2440', '#7FA8F0', '#E6ECFA'],
];

// One colour per source film or show in a cut, in order of first use.
export const SRC_COLOURS = ['#7FA8F0', '#B9A3F2', '#6CC5CC', '#F2A93B', '#E98CC0', '#3FA37A'];

export function hash(text) {
  let h = 2166136261;
  for (const c of String(text)) h = Math.imul(h ^ c.charCodeAt(0), 16777619);
  return h >>> 0;
}

export function palette(text) {
  return PALETTES[hash(text) % PALETTES.length];
}

// The motif follows the kind of cut: rings for merges, bars for canon cuts, a numbered
// path for watch orders, stacked frames for supercuts, a figure for character stories.
function Motif({ kind, c }) {
  const [, shade, acc] = c;
  switch (kind) {
    case 'merge':
      return (<>
        <path d="M0 230 Q50 200 100 214 T200 206 V300 H0z" fill={shade} />
        <circle cx="100" cy="128" r="62" fill={shade} />
        <circle cx="100" cy="128" r="62" fill="none" stroke={acc} strokeWidth="5" />
        <circle cx="100" cy="128" r="46" fill="none" stroke={acc} strokeOpacity=".35" strokeWidth="1.5" />
        <circle cx="100" cy="128" r="6" fill={acc} />
      </>);
    case 'canon':
      return (<>
        {[0, 1, 2, 3, 4, 5].map((i) => (
          <rect key={i} x="30" y={70 + i * 22} width={i % 3 === 1 ? 60 : 140} height="12" rx="3"
            fill={i % 3 === 1 ? acc : shade} opacity={i % 3 === 1 ? 0.9 : 1} />
        ))}
      </>);
    case 'order':
      return (<>
        <path d="M40 90 C90 60 120 140 160 110 S120 200 60 190" fill="none" stroke={acc} strokeOpacity=".5" strokeWidth="2" strokeDasharray="4 6" />
        {[[40, 90], [160, 110], [60, 190]].map(([x, y], i) => (
          <g key={i}><circle cx={x} cy={y} r="13" fill={shade} stroke={acc} strokeWidth="2" />
            <text x={x} y={y + 4} textAnchor="middle" fontSize="12" fontFamily="JetBrains Mono, monospace" fill={acc}>{i + 1}</text></g>
        ))}
      </>);
    case 'character':
      return (<>
        <circle cx="100" cy="105" r="34" fill={shade} />
        <path d="M40 230 Q100 130 160 230z" fill={shade} />
        <circle cx="100" cy="105" r="34" fill="none" stroke={acc} strokeOpacity=".6" strokeWidth="2" />
      </>);
    default: // supercut
      return (<>
        {[0, 1, 2].map((i) => (
          <rect key={i} x={34 + i * 14} y={60 + i * 18} width="104" height="72" rx="4" fill={shade}
            stroke={acc} strokeOpacity={0.25 + i * 0.25} strokeWidth="2" />
        ))}
      </>);
  }
}

// Poster title face: condensed Oswald for action, sci-fi and anime; Fraunces
// (the default serif) for fantasy, period and everything else.
const CONDENSED = /\b(action|adventure|sci-?fi|science fiction|anime|animation|thriller|war|western)\b/i;
export function posterFace({ genres = [], tag = '' } = {}) {
  return [...(genres || []), tag].some((g) => CONDENSED.test(g || '')) ? 'condensed' : 'serif';
}

export function Poster({ title, sub, tag, kind = 'supercut', src, wide, face, genres }) {
  const c = palette(title);
  const f = face || posterFace({ genres, tag });
  return (
    <div className={`pst${wide ? ' pst-wide' : ''}`} style={{ background: c[0] }}>
      {src ? <img src={src} alt="" /> : (
        <svg viewBox={wide ? '0 0 320 180' : '0 0 200 300'} preserveAspectRatio="xMidYMid slice" aria-hidden="true">
          <rect width="320" height="300" fill={c[0]} />
          {!wide && <Motif kind={kind} c={c} />}
        </svg>
      )}
      {tag && <span className="ptag" style={{ color: `${c[2]}cc` }}>{tag}</span>}
      <div className="ttl">
        <span className={`ttl-main${f === 'condensed' ? ' ttl-condensed' : ''}`} style={{ color: c[3], fontSize: wide ? 20 : undefined }}>{title}</span>
        {sub && <span className="ttl-sub" style={{ color: c[3] }}>{sub}</span>}
      </div>
    </div>
  );
}

// A plain poster for media the user owns: no art, just the facts.
export function PlainPoster({ tag, title }) {
  return (
    <div className="pst pst-plain">
      <span className="ptag">{tag}</span>
      <span className="ttl-main">{title}</span>
    </div>
  );
}

const STATUS = {
  ready: ['c-ready', 'Ready'],
  partly: ['c-warn', 'Partly ready'],
  missing: ['c-danger', 'Not available'],
};

export function statusText(status) {
  return (STATUS[status] || ['muted', status])[1];
}

export function CutTile({ to, title, sub, tag, kind, face, genres, meta, status, note, onClick, art, layout, badge }) {
  const [cls, label] = STATUS[status] || ['muted', status || ''];
  const inner = (
    <>
      {art?.length
        ? <ArtPoster keys={art} layout={layout} tag={tag} badge={badge}
          fallback={<Poster title={title} sub={sub} tag={tag} kind={kind} face={face} genres={genres} />} />
        : <Poster title={title} sub={sub} tag={tag} kind={kind} face={face} genres={genres} />}
      <div className="cap">
        <b>{title}</b>
        {meta && <span className="meta">{meta}</span>}
        {(note || label) && <span className={`note ${cls}`}>{note || label}</span>}
      </div>
    </>
  );
  if (to) return <Link className="tile" to={to}>{inner}</Link>;
  return <button type="button" className="tile tile-btn" onClick={onClick}>{inner}</button>;
}

export function MediaTile({ to, tag, short, title, year, meta, note, noteClass = 'muted', art }) {
  const plain = <PlainPoster tag={tag} title={short || title} />;
  return (
    <Link className="tile" to={to}>
      {art ? <ArtPoster keys={[art]} tag={tag} fallback={plain} /> : plain}
      <div className="cap">
        <span><b>{title}</b>{year ? <span className="muted"> {year}</span> : null}</span>
        {meta && <span className="meta">{meta}</span>}
        {note && <span className={`note ${noteClass}`}>{note}</span>}
      </div>
    </Link>
  );
}

export function Section({ title, count, end, children, id }) {
  return (
    <section className="section" aria-labelledby={id}>
      <div className="section-head">
        <h2 id={id}>{title}</h2>
        {count != null && <span className="n">{count}</span>}
        {end && <span className="end">{end}</span>}
      </div>
      {children}
    </section>
  );
}

export function Crumbs({ items }) {
  return (
    <nav className="crumbs" aria-label="Breadcrumb">
      {items.map(([label, to], i) => (
        <span key={i} className="row" style={{ gap: 8 }}>
          {i > 0 && <span aria-hidden="true">/</span>}
          {to ? <Link to={to}>{label}</Link> : <span className="here">{label}</span>}
        </span>
      ))}
    </nav>
  );
}

export function Seg({ label, options, value, onChange }) {
  return (
    <div className="seg" role="radiogroup" aria-label={label}>
      {options.map(([v, text]) => (
        <button key={v} type="button" role="radio" aria-checked={value === v} onClick={() => onChange(v)}>{text}</button>
      ))}
    </div>
  );
}

export function Search({ value, onChange, label = 'Search' }) {
  return (
    <label className="search">
      <svg width="14" height="14" viewBox="0 0 16 16" aria-hidden="true">
        <circle cx="7" cy="7" r="5" fill="none" stroke="#9A9EA6" strokeWidth="1.5" />
        <path d="M11 11l3.5 3.5" stroke="#9A9EA6" strokeWidth="1.5" strokeLinecap="round" />
      </svg>
      <input aria-label={label} placeholder="Search" value={value} onChange={(e) => onChange(e.target.value)} />
    </label>
  );
}

export function KV({ rows }) {
  return (
    <dl className="kv">
      {rows.filter(Boolean).map(([k, v]) => (
        <div key={k} style={{ display: 'contents' }}><dt>{k}</dt><dd>{v}</dd></div>
      ))}
    </dl>
  );
}

// "3 h 02 m" from minutes or seconds.
export function hm(minutes) {
  if (minutes == null) return '–';
  const m = Math.round(minutes);
  return m >= 60 ? `${Math.floor(m / 60)} h ${String(m % 60).padStart(2, '0')} m` : `${m} m`;
}

export const enc = encodeURIComponent;

// -- real artwork ------------------------------------------------------------------------------

// Poster widths the server resizes to; the browser picks by the tile's width
// and the screen's pixel density, so 4K screens get sharp art.
const POSTER_SRCSET = (key) => [300, 500, 800].map((w) => `${artUrl(key, 'poster', w)} ${w}w`).join(', ');
// Mirrors --tile (clamp(150px, 9vw, 300px)); sizes can't read CSS variables.
const POSTER_SIZES = '(min-width: 3334px) 300px, (min-width: 1667px) 9vw, 150px';

function useFailed() {
  const [failed, setFailed] = useState(() => new Set());
  const fail = useCallback((k) => setFailed((s) => (s.has(k) ? s : new Set(s).add(k))), []);
  return [failed, fail];
}

function ArtImg({ k, onFail, style, className }) {
  return (
    <img className={className} src={artUrl(k, 'poster', 500)} srcSet={POSTER_SRCSET(k)} sizes={POSTER_SIZES}
      alt="" loading="lazy" decoding="async" onError={() => onFail(k)} style={style} />
  );
}

// A poster from the library's own art. One key: that poster. Several: a merge
// shows them as slices side by side, a watch order as a fanned, numbered stack.
// Falls back to the drawn Poster when no art loads.
export function ArtPoster({ keys = [], layout = 'single', tag, badge, fallback }) {
  const [failed, fail] = useFailed();
  const ok = keys.filter((k) => !failed.has(k)).slice(0, layout === 'stack' ? 3 : 4);
  if (!ok.length) return fallback;
  let body;
  if (layout === 'split' && ok.length > 1) {
    // whole posters in a 2-column collage (2:3 cells fit a 2:3 tile), over a blur of the first
    body = (
      <div className="art-grid" data-n={ok.length}>
        <img className="art-blur" src={artUrl(ok[0], 'poster', 300)} alt="" aria-hidden="true" />
        {ok.map((k) => <div key={k} className="art-cell"><ArtImg k={k} onFail={fail} /></div>)}
      </div>
    );
  } else if (layout === 'stack' && ok.length > 1) {
    body = (
      <div className="art-stack" style={{ '--n': ok.length }}>
        {ok.map((k, i) => (
          <div key={k} className="art-card" style={{ '--i': i }}>
            <ArtImg k={k} onFail={fail} />
            <span className="art-num">{i + 1}</span>
          </div>
        ))}
      </div>
    );
  } else {
    body = <ArtImg className="art-one" k={ok[0]} onFail={fail} />;
  }
  return (
    <div className={`pst pst-art pst-art-${layout}`}>
      {body}
      {tag && <span className={`art-tag${badge ? ` art-tag-${badge}` : ''}`}>{tag}</span>}
    </div>
  );
}

// A backdrop with the name over it, for collections (Jellyseerr's genre cards).
export function Backdrop({ k, name, children }) {
  const [failed, setFailed] = useState(false);
  const [bg] = palette(name);
  return (
    <div className="bdrop" style={{ background: bg }}>
      {k && !failed && (
        <img src={artUrl(k, 'backdrop', 800)} srcSet={[480, 800, 1200].map((w) => `${artUrl(k, 'backdrop', w)} ${w}w`).join(', ')}
          sizes="(min-width: 3334px) 570px, (min-width: 1667px) 17vw, 285px" alt="" loading="lazy" decoding="async" onError={() => setFailed(true)} />
      )}
      <span className="bdrop-shade" style={{ '--tint': bg }} />
      <span className="bdrop-name">{name}</span>
      {children}
    </div>
  );
}

// A row of tiles that pages left and right with buttons (no scrollbar), or
// opens into a full grid with "See all".
export function Shelf({ title, count, id, children, wide }) {
  const ref = useRef(null);
  const [edges, setEdges] = useState({ start: true, end: true });
  const [all, setAll] = useState(false);
  const measure = useCallback(() => {
    const el = ref.current;
    if (!el) return;
    setEdges({ start: el.scrollLeft <= 2, end: el.scrollLeft + el.clientWidth >= el.scrollWidth - 2 });
  }, []);
  useEffect(() => {
    measure();
    const el = ref.current;
    if (!el || typeof ResizeObserver === 'undefined') return undefined;
    const ro = new ResizeObserver(measure);
    ro.observe(el);
    return () => ro.disconnect();
  }, [measure, all, children]);
  const page = (dir) => ref.current?.scrollBy({ left: dir * ref.current.clientWidth * 0.9, behavior: 'smooth' });
  const scrolls = !(edges.start && edges.end);
  return (
    <section className="section" aria-labelledby={id}>
      <div className="section-head">
        <h2 id={id}>{title}</h2>
        {count != null && <span className="n">{count}</span>}
        <span className="end row" style={{ gap: 6 }}>
          {(scrolls || all) && (
            <button type="button" className="btn btn-sm btn-ghost" onClick={() => setAll((a) => !a)}>
              {all ? 'Show less' : 'See all'}
            </button>
          )}
          {!all && scrolls && (
            <>
              <button type="button" className="shelf-arrow" aria-label={`Back in ${title}`} disabled={edges.start} onClick={() => page(-1)}>‹</button>
              <button type="button" className="shelf-arrow" aria-label={`More in ${title}`} disabled={edges.end} onClick={() => page(1)}>›</button>
            </>
          )}
        </span>
      </div>
      <div ref={ref} className={`${all ? 'grid' : 'shelf'}${wide ? ' shelf-wide' : ''}`} onScroll={all ? undefined : measure}>
        {children}
      </div>
    </section>
  );
}