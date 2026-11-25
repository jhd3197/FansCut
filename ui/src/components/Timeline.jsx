import { TRIM_NAMES, episodeTrack } from '../lib/timeline.js';
import { fmtTime } from '../lib/time.js';

// One row per kept episode: the full episode as a track, kept pieces filled in,
// trimmed ranges (recap / OP / ED / preview) drawn as markers on top.
export function EpisodeTrack({ episode, segments }) {
  const { kept, trims } = episodeTrack(episode, segments);
  return (
    <div className="track" aria-label={`${episode.id} timeline`}>
      {trims.map((t) => (
        <span key={t.kind} className={`mark mark-${t.kind}`} style={{ left: `${t.left}%`, width: `${t.width}%` }}
          title={`${TRIM_NAMES[t.kind]} ${fmtTime(t.start)}–${fmtTime(t.end)}`} />
      ))}
      {kept.map((k, i) => (
        <span key={i} className="piece" style={{ left: `${k.left}%`, width: `${k.width}%` }}
          title={`keep ${fmtTime(k.start)}–${fmtTime(k.end)}`} />
      ))}
    </div>
  );
}

export function TimelineLegend() {
  return (
    <div className="legend muted">
      <span><i className="sw sw-piece" /> kept</span>
      {Object.entries(TRIM_NAMES).map(([k, n]) => (
        <span key={k}><i className={`sw sw-${k}`} /> {n}</span>
      ))}
    </div>
  );
}

// "What gets cut" bar of the design: the episode is grey (kept); what the cut drops is
// drawn on top, coloured by why (opening / ending in amber, recap / preview in lilac,
// anything else such as a skipped episode or a custom cut in near-black).
export const CUT_COLORS = { kept: '#3B4048', opening: '#F2A93B', recap: '#B9A3F2', other: '#15171A' };
const KIND_COLOR = { intro: CUT_COLORS.opening, outro: CUT_COLORS.opening, recap: CUT_COLORS.recap, preview: CUT_COLORS.recap };

// The parts of [0, duration] the kept segments do not cover.
export function cutGaps(segments, duration) {
  const kept = [...(segments || [])].map((s) => [s.start, s.end]).sort((a, b) => a[0] - b[0]);
  const out = [];
  let at = 0;
  for (const [s, e] of kept) {
    if (s > at + 0.05) out.push([at, s]);
    at = Math.max(at, e);
  }
  if (duration - at > 0.05) out.push([at, duration]);
  return out;
}

// What one episode loses in the cut, as ranges with a colour. segments == null means
// "no plan known": the trims are shown as they are.
export function cutPieces(episode, segments, included = true) {
  const d = episode.duration || 0;
  if (!d) return [];
  if (!included) return [{ start: 0, end: d, color: CUT_COLORS.other, what: 'skipped' }];
  const { trims } = episodeTrack(episode, []);
  if (segments == null) {
    return trims.map((t) => ({ start: t.start, end: t.end, color: KIND_COLOR[t.kind], what: TRIM_NAMES[t.kind] }));
  }
  const gaps = cutGaps(segments, d);
  const out = gaps.map(([s, e]) => ({ start: s, end: e, color: CUT_COLORS.other, what: 'cut' }));
  for (const t of trims) {
    for (const [s, e] of gaps) {
      const a = Math.max(s, t.start);
      const b = Math.min(e, t.end);
      if (b - a > 0.05) out.push({ start: a, end: b, color: KIND_COLOR[t.kind], what: TRIM_NAMES[t.kind] });
    }
  }
  return out;
}

const pct = (v, d) => `${((Math.min(Math.max(v, 0), d) / d) * 100).toFixed(2)}%`;

export function CutBar({ episode, segments, included = true, height = 10, radius = 2 }) {
  const d = episode.duration || 0;
  const pieces = cutPieces(episode, segments, included);
  return (
    <div role="img" aria-label={`${episode.id}: ${pieces.length ? `${pieces.length} part(s) cut` : 'nothing cut'}`}
      style={{ position: 'relative', height, borderRadius: radius, background: CUT_COLORS.kept, overflow: 'hidden' }}>
      {d > 0 && pieces.map((p, i) => (
        <span key={i} title={`${p.what} ${fmtTime(p.start)}–${fmtTime(p.end)}`}
          style={{ position: 'absolute', top: 0, bottom: 0, background: p.color, left: pct(p.start, d), width: pct(p.end - p.start, d) }} />
      ))}
    </div>
  );
}

export function CutLegend() {
  const sw = (c) => <span style={{ width: 10, height: 10, borderRadius: 2, background: c, border: c === CUT_COLORS.other ? '1px solid var(--line)' : 0 }} />;
  const item = (c, text) => <span style={{ display: 'flex', alignItems: 'center', gap: 6 }}>{sw(c)}{text}</span>;
  return (
    <div style={{ display: 'flex', flexWrap: 'wrap', gap: 18, fontSize: 12, color: 'var(--muted)', alignItems: 'center' }}>
      {item(CUT_COLORS.kept, 'Kept')}
      {item(CUT_COLORS.opening, 'Opening / ending')}
      {item(CUT_COLORS.recap, 'Recap / preview')}
      {item(CUT_COLORS.other, 'Other cuts and skipped episodes')}
    </div>
  );
}
