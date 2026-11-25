import { useState } from 'react';
import { Link, useParams } from 'react-router';
import { api } from '../lib/api.js';
import { useAct, useLoad } from '../lib/hooks.js';
import { Empty, ErrorBox, Loading, Notice } from '../components/ui.jsx';
import { Crumbs, Section, enc, hm, palette } from '../components/kit.jsx';
import { SuggestionDialog, SuggestionTile, TimelineTile } from './Discover.jsx';
import { TemplateDialog } from '../components/TemplateDialog.jsx';

// A plain film tile for a shelf: "NAME · 3" in the corner, the facts underneath.
export function FilmTile({ m, tag }) {
  const facts = [m.edition, !m.on_disk && 'Not on disk'].filter(Boolean).join(' · ');
  return (
    <Link className="tile" to={`/movies/${enc(m.key)}`} style={{ flex: '0 0 150px' }}>
      <div className="pst pst-plain">
        <span className="ptag">{tag}</span>
        <span className="ttl-main">{m.title}</span>
      </div>
      <div className="cap">
        <b>{m.title}</b>
        <span className="meta">{[m.year, m.minutes && hm(m.minutes)].filter(Boolean).join(' · ')}</span>
        {m.problem
          ? <span className={`note ${m.problem_severity === 'error' ? 'c-danger' : 'c-warn'}`}>{m.problem}</span>
          : facts && <span className="note muted">{facts}</span>}
      </div>
    </Link>
  );
}

export function CollectionPage() {
  const { name } = useParams(); // react-router hands params over decoded
  const { data, error, loading, reload } = useLoad(() => api(`/library/collections/${enc(name)}`), [name]);
  const { act, msg, busy, clear } = useAct();
  const [open, setOpen] = useState(null);
  const [tpl, setTpl] = useState(false);

  if (error && !data) return <div className="page"><Crumbs items={[['Library', '/library'], [name]]} /><ErrorBox error={error} onRetry={reload} /></div>;
  if (loading || !data) return <div className="page"><Loading /></div>;

  const [bg, , acc, fg] = palette(data.name);
  const years = data.films.map((f) => f.year).filter(Boolean);
  const span = years.length ? (Math.min(...years) === Math.max(...years) ? String(years[0]) : `${Math.min(...years)}–${Math.max(...years)}`) : null;
  const extended = data.films.filter((f) => /extended/i.test(f.edition || '')).length;
  const meta = [`${data.films.length} film${data.films.length === 1 ? '' : 's'}`, span, data.minutes ? hm(data.minutes) : null,
    extended ? `${extended} extended` : null].filter(Boolean).join(' · ');
  const order = data.suggestions.find((s) => s.kind === 'watch_order' && s.status !== 'missing');
  const playInOrder = () => act(() => api(`/suggestions/${enc(order.id)}/install`, { method: 'POST' }),
    'Playlist created in Plex. Open Plex to play it.');
  const shelfCount = data.cuts.length + data.suggestions.length;

  return (
    <div className="page" style={{ gap: 30 }}>
      <Crumbs items={[['Library', '/library'], [data.name]]} />

      <div className="hero" style={{ gap: 28, alignItems: 'flex-end' }}>
        <div className="pst" style={{ width: 180, flex: 'none', background: bg }}>
          <span className="ptag" style={{ color: `${acc}cc` }}>Collection</span>
          <div className="ttl"><span className="ttl-main" style={{ color: fg, fontSize: 30 }}>{data.name}</span></div>
        </div>
        <div className="hero-body" style={{ flex: '1 1 320px', gap: 10 }}>
          <h1 className="hero-title" style={{ fontSize: 32 }}>{data.name}</h1>
          <span className="mono muted" style={{ fontSize: 13 }}>{meta}</span>
          <div className="row wrap" style={{ marginTop: 6 }}>
            {order && <button type="button" className="btn btn-primary" onClick={playInOrder} disabled={busy}
              title="Creates a Plex playlist of these films in order">Play in order</button>}
            <button type="button" className="btn" onClick={() => setTpl(true)}>New cut from this collection</button>
          </div>
        </div>
      </div>
      <Notice msg={msg} onClose={clear} />
      {tpl && <TemplateDialog movies={data.films.filter((f) => f.on_disk).map((f) => f.key)} onClose={() => setTpl(false)} />}

      <Section id="films-h" title="Films" count={data.films.length}>
        <div className="shelf">
          {data.films.map((m, i) => <FilmTile key={m.key} m={m} tag={`${data.name} · ${i + 1}`} />)}
        </div>
      </Section>

      <Section id="cuts-h" title="Cuts and playlists" count={shelfCount}>
        {shelfCount === 0
          ? <Empty>No cuts use these films yet. Merges and watch orders appear on <Link to="/">Discover</Link> once two or more films share a collection.</Empty>
          : (
            <div className="shelf">
              {data.cuts.map((c) => <TimelineTile key={c.id} c={c} />)}
              {data.suggestions.map((s) => <SuggestionTile key={s.id} s={s} onOpen={setOpen} />)}
            </div>
          )}
      </Section>

      {open && <SuggestionDialog s={open} onClose={() => setOpen(null)} />}
    </div>
  );
}
