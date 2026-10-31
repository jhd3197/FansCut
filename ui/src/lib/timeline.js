// Timeline math, kept free of React so it can be tested.

export const TRIM_KINDS = ['recap', 'intro', 'outro', 'preview'];
export const TRIM_NAMES = { recap: 'Recap', intro: 'OP', outro: 'ED', preview: 'Preview' };

const clamp = (v, lo, hi) => Math.min(Math.max(v, lo), hi);
const round3 = (v) => Math.round(v * 1000) / 1000;

// Position of [start, end] on a bar covering `duration`, as percentages.
export function bar(start, end, duration) {
  if (!duration || duration <= 0) return { left: 0, width: 0 };
  const s = clamp(start, 0, duration);
  const e = clamp(end, 0, duration);
  return { left: (s / duration) * 100, width: (Math.max(e - s, 0) / duration) * 100 };
}

export function groupSegments(segments) {
  const out = {};
  for (const seg of segments) (out[seg.episode_id] ||= []).push(seg);
  return out;
}

// Move one edge of a range by `delta` seconds, keeping start < end inside [0, duration].
export function nudge(range, edge, delta, duration, minLen = 0.5) {
  let { start, end } = range;
  if (edge === 'start') start = clamp(start + delta, 0, end - minLen);
  else end = clamp(end + delta, start + minLen, duration || Infinity);
  return { start: round3(start), end: round3(end) };
}

// Validate typed values the way the server will: both set, start >= 0, end > start.
export function checkRange(start, end, duration) {
  if (start == null || end == null) return 'enter both start and end';
  if (start < 0) return 'start cannot be negative';
  if (end <= start) return 'end must be after start';
  if (duration && end > duration + 0.001) return 'end is past the end of the episode';
  return null;
}

// What is kept of one episode, as % of the episode, plus the trims as markers.
export function episodeTrack(episode, segments) {
  const d = episode.duration || 0;
  const kept = (segments || []).map((s) => ({ ...bar(s.start, s.end, d), start: s.start, end: s.end }));
  const trims = TRIM_KINDS.flatMap((kind) => {
    const t = episode.trims && episode.trims[kind];
    return t && t.start != null ? [{ kind, ...bar(t.start, t.end, d), start: t.start, end: t.end }] : [];
  });
  return { kept, trims };
}
