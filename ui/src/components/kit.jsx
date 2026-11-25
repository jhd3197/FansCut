// Shared building blocks of the FansCut design: posters, tiles, sections, crumbs.
import { Link } from 'react-router';

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

export function Poster({ title, sub, tag, kind = 'supercut', src, wide }) {
  const c = palette(title);
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
        <span className="ttl-main" style={{ color: c[3], fontSize: wide ? 20 : undefined }}>{title}</span>
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

export function CutTile({ to, title, sub, tag, kind, meta, status, note, onClick }) {
  const [cls, label] = STATUS[status] || ['muted', status || ''];
  const inner = (
    <>
      <Poster title={title} sub={sub} tag={tag} kind={kind} />
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

export function MediaTile({ to, tag, short, title, year, meta, note, noteClass = 'muted' }) {
  return (
    <Link className="tile" to={to}>
      <PlainPoster tag={tag} title={short || title} />
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
