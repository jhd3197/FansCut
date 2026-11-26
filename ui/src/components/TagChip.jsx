// Tag names and the tag chip. Every chip opens the library tag page, /library/tags/:tag.
// Measured "fc:" tags (no face, close-up, dialogue...) are not tags any more: they are never
// shown. Only the real cut tags keep an fc: id.
import { Link } from 'react-router';
import { useApi } from '../lib/store.js';
import '../pages/Sets.css';

// The cut tags a person or the AI still sets on a range (filler, spoiler...).
export const CUT_TAGS = new Set(['filler', 'spoiler', 'recap', 'theme', 'titles', 'establishing', 'transition', 'montage', 'extendable']);

const bare = (id) => String(id ?? '').replace(/^fc:/, '');

// A real cut tag, with or without the fc: prefix.
export const isCutTag = (id) => CUT_TAGS.has(bare(id));

// Shown anywhere in the app: content tags and the real cut tags, never measured fc: tags.
export const isShownTag = (id) => Boolean(id) && (!String(id).startsWith('fc:') || isCutTag(id));

// A tag's display name: the vocab name, else the id without "fc:", dashes as spaces, capitalised.
// `names` is a Map or a plain object of id -> name.
export function tagName(id, names) {
  const named = names instanceof Map ? names.get(id) : names?.[id];
  if (named) return named;
  const s = bare(id).replace(/[-_]/g, ' ').trim();
  return s ? s[0].toUpperCase() + s.slice(1) : '';
}

export const tagTo = (id) => `/library/tags/${encodeURIComponent(id)}`;

// id -> name, from the vocab (content and cut tags) and tag-counts.
export function useTagNames() {
  const vocab = useApi('/scenes/vocab', { quiet: true });
  const counts = useApi('/scenes/tag-counts', { quiet: true });
  const names = new Map();
  for (const x of [...(vocab.data?.tags || []), ...(vocab.data?.cut_tags || [])]) if (x?.id && x.name) names.set(x.id, x.name);
  for (const x of counts.data?.tags || []) if (x?.tag && x.name && !names.has(x.tag)) names.set(x.tag, x.name);
  return names;
}

// A tag as a link to its library page; `count` is drawn after the name when given.
export function TagChip({ tag, names, count }) {
  return (
    <Link className="tag-chip" to={tagTo(tag)}>
      {tagName(tag, names)}
      {count != null && <span className="n">{count}</span>}
    </Link>
  );
}

// Several chips, shown tags only, each once.
export function TagChips({ tags, names, className = 'tag-links' }) {
  const list = [...new Set((tags || []).filter(isShownTag))];
  if (!list.length) return null;
  return <span className={className}>{list.map((t) => <TagChip key={t} tag={t} names={names} />)}</span>;
}
