// The v2 pieces from the Components board as small React components.
// Classes live in styles.css ("v2 kit"); usage notes in components/V2.md.
import { Link } from 'react-router';

// One colour per film, so a bar or a list shows which film each part comes from.
// Same values as --film-1..8 in styles.css.
export const FILM_COLOURS = ['#D3A85A', '#6FA36A', '#5B93C7', '#9A86D6', '#47B3AD', '#C27A5C', '#B5B9C2', '#D98FB0'];

export function filmColour(i) {
  return FILM_COLOURS[((i % FILM_COLOURS.length) + FILM_COLOURS.length) % FILM_COLOURS.length];
}

// { [filmKey]: colour } in order of first use, for a cut's sources.
export function filmColours(keys) {
  const out = {};
  let n = 0;
  for (const k of keys || []) if (!(k in out)) out[k] = filmColour(n++);
  return out;
}

const enc = encodeURIComponent;

// The two steps of making a cut. Only in the editor.
export function EditorSteps({ cut, step }) {
  const items = [
    ['find', 1, 'Find scenes', `/cuts/${enc(cut)}/find`],
    ['arrange', 2, 'Arrange', `/cuts/${enc(cut)}/arrange`],
  ];
  return (
    <nav className="esteps" aria-label="Steps to make a cut">
      {items.map(([key, n, name, to]) => (key === step
        ? <span key={key} className="estep" aria-current="step"><b>{n}</b>{name}</span>
        : <Link key={key} className="estep" to={to}><b>{n}</b>{name}</Link>))}
    </nav>
  );
}

// Small caps label above a group. Never a button.
export function Label({ children, as: As = 'div', ...rest }) {
  return <As className="lab" {...rest}>{children}</As>;
}

export function FilmDot({ colour, title }) {
  return <span className="film-dot" style={{ '--film': colour }} title={title} aria-hidden={title ? undefined : 'true'} />;
}

// Filter chip: narrows what you see, changes nothing. `count` shows in mono after the name.
export function FilterChip({ on, count, children, className = '', ...rest }) {
  return (
    <button type="button" className={`fchip ${className}`.trim()} aria-pressed={!!on} {...rest}>
      {children}{count != null && <em>{count}</em>}
    </button>
  );
}

// A tag you can edit: solid Sky with a cross. Only in Scenes and tags.
export function EditTag({ name, onRemove, disabled }) {
  return (
    <button type="button" className="tag-edit" onClick={onRemove} disabled={disabled} aria-label={`Remove the tag ${name}`}>
      {name}
      <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="3" aria-hidden="true"><path d="M6 6l12 12M18 6L6 18" /></svg>
    </button>
  );
}

// An AI suggestion: dashed until a person keeps it. Keep/Drop are optional.
export function AiTag({ name, onKeep, onDrop }) {
  return (
    <span className="tags-row">
      <span className="tag-ai">{name}</span>
      {onKeep && <button type="button" className="btn btn-sm" onClick={onKeep}>Keep</button>}
      {onDrop && <button type="button" className="btn btn-sm btn-quiet" onClick={onDrop}>Drop</button>}
    </span>
  );
}

// Tags you can only read: plain text joined with dots.
export function ReadTags({ tags, className = 'tags-read' }) {
  const list = (tags || []).map((x) => (typeof x === 'string' ? x : x?.name)).filter(Boolean);
  if (!list.length) return null;
  return <span className={className}>{list.join(' · ')}</span>;
}

// A setting that is on or off.
export function Switch({ on, onChange, label, disabled, title }) {
  return (
    <button type="button" role="switch" className="toggle" aria-checked={!!on} aria-label={label}
      disabled={disabled} title={title} onClick={() => onChange?.(!on)} />
  );
}

// In the cut, or not. On scene cards.
export function Tick({ on, onChange, label, disabled }) {
  return (
    <button type="button" role="checkbox" className="tick" aria-checked={!!on} aria-label={label}
      disabled={disabled} onClick={(e) => { e.stopPropagation(); onChange?.(!on); }}>
      {on && <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="3.2" aria-hidden="true"><path d="M5 12.5l4.5 4.5L19 7.5" /></svg>}
    </button>
  );
}

const ICONS = {
  play: <path d="M8 5l11 7-11 7z" />,
  pause: <path d="M7 5h3.5v14H7zM13.5 5H17v14h-3.5z" />,
  next: <path d="M15.5 5H18v14h-2.5zM4 5l10 7-10 7z" />,
  prev: <path d="M6 5h2.5v14H6zM20 5L10 12l10 7z" />,
};

// Player controls only. kind: play | pause | next | prev. `go` = the filled play button.
export function IconButton({ kind, label, go, ...rest }) {
  return (
    <button type="button" className={`icon-btn${go ? ' is-play' : ''}`} aria-label={label} {...rest}>
      <svg width="18" height="18" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true">{ICONS[kind]}</svg>
    </button>
  );
}

// Join status between two scenes: 'check' (Coral, needs a look), 'clean', or 'unchecked'.
export function JoinStatus({ state, children }) {
  const text = children ?? (state === 'clean' ? 'Clean' : state === 'unchecked' ? 'Film not analysed' : '');
  return <span className={`join join-${state}`}>{text}</span>;
}

// Ready (Sky dot) or missing (Apricot ring). On Discover cards.
export function ReadyState({ ready, children }) {
  return <span className={`state ${ready ? 'state-ready' : 'state-missing'}`}>{children}</span>;
}

// On every player that is not the built file.
export function PreviewBadge() {
  return <span className="preview-badge">PREVIEW</span>;
}

// One segment per scene, coloured by film, bright = played.
// parts: [{ len (seconds), colour, played }]
export function GluedBar({ parts, label = 'Scenes in this cut' }) {
  return (
    <div className="glued" role="img" aria-label={label}>
      {(parts || []).map((p, i) => (
        <span key={i} className={p.played ? 'is-played' : undefined} style={{ '--len': Math.max(1, Math.round(p.len || 1)), '--film': p.colour }} />
      ))}
    </div>
  );
}
