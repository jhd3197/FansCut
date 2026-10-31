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
