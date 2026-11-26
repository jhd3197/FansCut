// The library's tags. /library/tags: every tag with its counts, one table per family.
// /library/tags/:tag: every moment with that tag, by collection, then film. A set (/sets/:tag)
// is the same tag as a theme with exports on top.
import { Link, useParams } from 'react-router';
import { useDebounced, usePageTitle, useUrlState } from '../lib/hooks.js';
import { useApi } from '../lib/store.js';
import { fmtTime } from '../lib/time.js';
import { Empty, ErrorBox, Loading } from '../components/ui.jsx';
import { Crumbs, Search, Section, enc } from '../components/kit.jsx';
import { TagChips, isCutTag, isShownTag, tagName, tagTo, useTagNames } from '../components/TagChip.jsx';
import { momentTo } from './Sets.jsx';
import './Sets.css';

const CUT_GROUP = 'Cut tags';
const OTHER_GROUP = 'Other';
const cap = (s) => (s ? s[0].toUpperCase() + s.slice(1) : s);
const plural = (n, w) => `${n} ${w}${n === 1 ? '' : 's'}`;

// Per shown tag: moments (not rejected), accepted, films; grouped by family in vocab order.
export function tagTable(vocab, counts, rows, names) {
  const familyOf = new Map((vocab?.tags || []).map((t) => [t.id, t.family]));
  const byTag = new Map();
  const get = (tag) => {
    if (!byTag.has(tag)) byTag.set(tag, { tag, moments: 0, accepted: 0, films: new Set(), counted: false });
    return byTag.get(tag);
  };
  for (const c of counts || []) {
    if (!isShownTag(c.tag)) continue;
    const x = get(c.tag);
    x.moments = (c.proposed || 0) + (c.accepted || 0);
    x.accepted = c.accepted || 0;
    x.counted = true;
    if (c.family && !familyOf.has(c.tag)) familyOf.set(c.tag, c.family);
  }
  for (const r of rows || []) {
    if (r.status === 'rejected') continue;
    for (const t of (r.tags || []).filter(isShownTag)) {
      const x = get(t);
      x.films.add(r.film_key);
      if (!x.counted) { x.moments += 1; if (r.status === 'accepted') x.accepted += 1; }
    }
  }
  const order = [...(vocab?.families || [])];
  const groups = new Map();
  for (const x of byTag.values()) {
    if (!x.moments) continue;
    const g = isCutTag(x.tag) ? CUT_GROUP : familyOf.get(x.tag) || OTHER_GROUP;
    if (!groups.has(g)) groups.set(g, []);
    groups.get(g).push({ tag: x.tag, name: tagName(x.tag, names), moments: x.moments, accepted: x.accepted, films: x.films.size });
  }
  const rank = (g) => (g === CUT_GROUP ? order.length + 1 : g === OTHER_GROUP ? order.length : order.indexOf(g) < 0 ? order.length : order.indexOf(g));
  return [...groups].sort(([a], [b]) => rank(a) - rank(b) || a.localeCompare(b)).map(([id, tags]) => ({
    id, label: id === CUT_GROUP || id === OTHER_GROUP ? id : cap(id),
    tags: tags.sort((a, b) => b.moments - a.moments || a.name.localeCompare(b.name)),
  }));
}

export function LibraryTags() {
  usePageTitle('Tags');
  const vocab = useApi('/scenes/vocab', { quiet: true });
  const counts = useApi('/scenes/tag-counts');
  const rows = useApi('/scenes/tags', { quiet: true });
  const names = useTagNames();
  const [q, setQ] = useUrlState('q');
  const needle = useDebounced(q).trim().toLowerCase();
  const crumbs = [['Library', '/library'], ['Tags']];

  if (counts.error && !counts.data && !rows.data) return <div className="page"><Crumbs items={crumbs} /><ErrorBox error={counts.error} onRetry={counts.reload} /></div>;
  if (!counts.data && !rows.data) return <div className="page"><Crumbs items={crumbs} /><Loading /></div>;

  const groups = tagTable(vocab.data, counts.data?.tags, rows.data?.tags, names)
    .map((g) => ({ ...g, tags: g.tags.filter((t) => !needle || t.name.toLowerCase().includes(needle)) }))
    .filter((g) => g.tags.length);
  const films = rows.data ? (n) => n : () => <span className="muted">—</span>;

  return (
    <div className="page" style={{ gap: 30 }}>
      <div className="toolbar">
        <Crumbs items={crumbs} />
        <Search value={q} onChange={setQ} label="Search tags" />
      </div>
      {groups.length === 0 ? (
        <Empty>{needle ? 'No tag matches that search.' : <>No tagged moments yet. Tag scenes on a film's <Link to="/library#movies-h">Scenes and tags</Link> page.</>}</Empty>
      ) : groups.map((g) => (
        <Section key={g.id} id={`lt-${g.id.replace(/\W+/g, '-')}`} title={g.label} count={g.tags.length}>
          <div className="scroll-x">
            <table className="table">
              <thead><tr><th>Tag</th><th className="num">Moments</th><th className="num">Accepted</th><th className="num">Films</th></tr></thead>
              <tbody>
                {g.tags.map((t) => (
                  <tr key={t.tag}>
                    <td><Link to={tagTo(t.tag)}>{t.name}</Link></td>
                    <td className="num">{t.moments}</td>
                    <td className="num">{t.accepted || <span className="muted">0</span>}</td>
                    <td className="num">{films(t.films)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Section>
      ))}
    </div>
  );
}

// -- /library/tags/:tag --------------------------------------------------------------------------

const OTHER_FILMS = 'Other films';
const still = (key, t) => `/api/scenes/frame?${new URLSearchParams({ key, t: (Math.max(0, t)).toFixed(2), height: '240' })}`;
const who = (p) => (typeof p === 'string' ? p : p?.character || p?.name || '');

// Moments grouped by collection (a film's first franchise; none -> "Other films"), then film.
export function byCollection(moments, films) {
  const film = new Map((films || []).map((f) => [f.key, f]));
  const groups = new Map();
  for (const r of moments) {
    const f = film.get(r.film_key);
    const c = (f?.franchises?.length ? f.franchises : f?.collections || [])[0] || OTHER_FILMS;
    if (!groups.has(c)) groups.set(c, new Map());
    const g = groups.get(c);
    if (!g.has(r.film_key)) g.set(r.film_key, { key: r.film_key, title: f?.title || r.film_key, year: f?.year, moments: [] });
    g.get(r.film_key).moments.push(r);
  }
  return [...groups].sort(([a], [b]) => (a === OTHER_FILMS) - (b === OTHER_FILMS) || a.localeCompare(b)).map(([name, g]) => ({
    name,
    films: [...g.values()].map((f) => ({ ...f, moments: f.moments.sort((a, b) => a.start - b.start) }))
      .sort((a, b) => (a.year || 0) - (b.year || 0) || a.title.localeCompare(b.title)),
  }));
}

function Moment({ r, tag, names }) {
  const people = [...new Set((r.people || []).map(who).filter(Boolean))];
  return (
    <div className="moment">
      <Link className="moment-link" to={momentTo(r)}>
        <img src={still(r.film_key, r.start + 1)} alt="" loading="lazy" decoding="async" />
        <span className="mono tiny">
          {fmtTime(r.start)}–{fmtTime(r.end)}
          <span className={r.status === 'accepted' ? 'c-ready' : 'muted'}> · {r.status === 'accepted' ? 'accepted' : 'proposed'}</span>
        </span>
      </Link>
      <TagChips tags={(r.tags || []).filter((x) => x !== tag)} names={names} className="tag-links-sm" />
      {people.length > 0 && <span className="tiny muted moment-people" title={people.join(', ')}>{people.join(', ')}</span>}
    </div>
  );
}

export function LibraryTag() {
  const { tag } = useParams();
  const names = useTagNames();
  const name = tagName(tag, names);
  usePageTitle(name);
  const rowsQ = useApi(`/scenes/tags?${new URLSearchParams({ tag })}`);
  const filmsQ = useApi('/scenes/films', { quiet: true });

  const crumbs = [['Library', '/library'], ['Tags', '/library/tags'], [name]];
  const head = (
    <div className="toolbar">
      <Crumbs items={crumbs} />
      {isShownTag(tag) && <Link className="btn btn-sm" to={`/sets/${enc(tag)}`}>Set</Link>}
    </div>
  );
  if (rowsQ.error && !rowsQ.data) return <div className="page">{head}<ErrorBox error={rowsQ.error} onRetry={rowsQ.reload} /></div>;
  // wait for the films too, so moments don't flash under "Other films"
  if (!rowsQ.data || (!filmsQ.data && !filmsQ.error)) return <div className="page">{head}<Loading /></div>;

  const moments = isShownTag(tag) ? (rowsQ.data.tags || []).filter((r) => r.status !== 'rejected') : [];
  const groups = byCollection(moments, filmsQ.data?.films);
  const accepted = moments.filter((r) => r.status === 'accepted').length;
  const filmCount = groups.reduce((n, g) => n + g.films.length, 0);

  return (
    <div className="page" style={{ gap: 30 }}>
      {head}
      {moments.length === 0 ? <Empty>No moments with this tag.</Empty> : (<>
        <ul className="facts">
          <li><b>{moments.length}</b>{moments.length === 1 ? 'moment' : 'moments'}</li>
          <li><b>{filmCount}</b>{filmCount === 1 ? 'film' : 'films'}</li>
          <li><b>{accepted}</b>accepted</li>
          <li><b>{moments.length - accepted}</b>proposed</li>
        </ul>
        {groups.map((g, i) => (
          <Section key={g.name} id={`ltc-${i}`} title={g.name} count={g.films.reduce((n, f) => n + f.moments.length, 0)}>
            {g.films.map((f) => (
              <div key={f.key} className="stack" style={{ gap: 10 }}>
                <h3 style={{ margin: 0, fontSize: 15 }}>
                  <Link to={`/movies/${enc(f.key)}`} style={{ color: 'var(--text)' }}>{f.title}</Link>
                  {f.year ? <span className="muted" style={{ fontWeight: 400 }}> {f.year}</span> : null}
                  <span className="muted tiny" style={{ fontWeight: 400 }}> · {plural(f.moments.length, 'moment')}</span>
                </h3>
                <div className="moments">
                  {f.moments.map((r) => <Moment key={r.id} r={r} tag={tag} names={names} />)}
                </div>
              </div>
            ))}
          </Section>
        ))}
      </>)}
    </div>
  );
}
