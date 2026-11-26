// @ts-check
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

// -- timeline editor (recipe docs) ---------------------------------------------

// Move one item of a list to a new index (clamped), returning a new list.
export function moveItem(list, from, to) {
  const next = [...list];
  const [x] = next.splice(from, 1);
  next.splice(Math.max(0, Math.min(to, next.length)), 0, x);
  return next;
}

// Every segment of a recipe in play order, with its place on the cut's own clock.
export function flatSegments(doc) {
  const out = [];
  let at = 0;
  (doc?.parts || []).forEach((part, pi) => {
    (part.segments || []).forEach((seg, si) => {
      const dur = Math.max(0, (seg.end ?? 0) - (seg.start ?? 0));
      out.push({ pi, si, seg, at, dur, n: out.length + 1 });
      at += dur;
    });
  });
  return { segs: out, total: at };
}

// Where the picture switches from one source to another.
export function sourceJoins(segs) {
  const out = [];
  for (let i = 1; i < segs.length; i += 1) {
    if (segs[i].seg.src !== segs[i - 1].seg.src) {
      out.push({ at: segs[i].at, from: segs[i - 1].seg.src, to: segs[i].seg.src, i });
    }
  }
  return out;
}

// Round tick marks for a ruler covering `total` seconds (about `want` of them).
export function rulerTicks(total, want = 6) {
  if (!total || total <= 0) return [];
  const steps = [10, 30, 60, 120, 300, 600, 900, 1800, 3600, 7200];
  const step = steps.find((s) => total / s <= want) || steps[steps.length - 1];
  const out = [];
  for (let t = 0; t < total; t += step) out.push(t);
  return out;
}

// "Synced N / M": cut points found in the user's files over all cut points.
export function syncCounts(report) {
  if (!report?.synced) return null;
  let matched = 0;
  let total = 0;
  for (const s of Object.values(report.sources || {})) {
    matched += s.matched_boundaries || 0;
    total += s.total_boundaries ?? (s.boundaries || []).length;
  }
  return { matched, total };
}

// What the sync found for one segment: its status and the start/end boundaries.
export function segmentSync(report, src, pi, si) {
  const s = report?.sources?.[src];
  if (!s) return null;
  const pick = (side) => (s.boundaries || []).find(
    (b) => b.part === pi + 1 && b.segment === si + 1 && b.side === side) || null;
  return { status: s.segments?.[`${pi + 1}:${si + 1}`] || null, matched: s.matched,
    reason: s.reason, start: pick('start'), end: pick('end') };
}

// Client-side checks before saving: bad ranges, unknown sources, empty parts.
export function recipeIssues(doc) {
  const out = [];
  (doc?.parts || []).forEach((part, pi) => {
    if (!part.segments?.length) out.push({ pi, si: null, text: `${part.title || `Part ${pi + 1}`} has no segments (dropped on save)` });
    (part.segments || []).forEach((seg, si) => {
      const source = doc.sources?.[seg.src];
      if (!source) out.push({ pi, si, text: `${seg.src} is not one of the recipe's sources` });
      const err = checkRange(seg.start, seg.end, source?.duration);
      if (err) out.push({ pi, si, text: `${seg.src}: ${err.replace('episode', 'source')}` });
    });
  });
  return out;
}

// -- segment effects (fanscut/timeline/effects.py validates the same fields) ---------------

// A value that means "no effect": the field is dropped instead of saved.
const NEUTRAL = {
  speed: 1, hold: 0, j_cut: 0, l_cut: 0,
  'video.fade_in': 0, 'video.fade_out': 0, 'video.mirror': false,
  'video.grade.exposure': 0, 'video.grade.saturation': 1, 'video.grade.temperature': 0, 'video.grade.contrast': 1,
  'audio.fade_in': 0, 'audio.fade_out': 0, 'audio.volume': 1,
};

export const GRADE = [
  // [field, label, min, max, step, neutral]
  ['exposure', 'Exposure', -2, 2, 0.05, 0],
  ['saturation', 'Saturation', 0, 2, 0.05, 1],
  ['temperature', 'Temperature', -50, 50, 1, 0],
  ['contrast', 'Contrast', 0.5, 2, 0.05, 1],
];

export const TRANSITIONS = [['cut', 'Cut'], ['dissolve', 'Dissolve'], ['dip', 'Dip to black'], ['fade', 'Fade']];

// Set seg[path] (dotted, e.g. "video.grade.exposure") in place. Neutral or empty values
// delete the field, and objects left empty go too, so the recipe only holds real edits.
// A J-cut and an L-cut exclude each other; "cut" removes the transition.
export function setFx(seg, path, value) {
  const keys = path.split('.');
  const neutral = value == null || value === '' || Number.isNaN(value) || NEUTRAL[path] === value
    || (path === 'transition.type' && value === 'cut');
  if (path === 'transition.type' && neutral) { delete seg.transition; return seg; }
  if (path === 'j_cut' && !neutral) delete seg.l_cut;
  if (path === 'l_cut' && !neutral) delete seg.j_cut;
  const chain = [seg];
  for (const k of keys.slice(0, -1)) {
    const parent = chain[chain.length - 1];
    if (neutral && !parent[k]) return seg;
    parent[k] = parent[k] && typeof parent[k] === 'object' ? parent[k] : {};
    chain.push(parent[k]);
  }
  const last = keys[keys.length - 1];
  if (neutral) delete chain[chain.length - 1][last];
  else chain[chain.length - 1][last] = value;
  if (path === 'transition.type' && seg.transition && seg.transition.duration == null) seg.transition.duration = 0.5;
  if (path === 'title.text' && seg.title && seg.title.duration == null) seg.title.duration = 3;
  for (let i = chain.length - 1; i > 0; i -= 1) { // prune objects that ended up empty
    if (Object.keys(chain[i]).length === 0) delete chain[i - 1][keys[i - 1]];
  }
  return seg;
}

// The effect names on a segment, for the fx column and clip badges.
export function fxList(seg) {
  const out = [];
  if (seg.speed) out.push(`${seg.speed}×`);
  if (seg.hold) out.push('hold');
  if (seg.transition) out.push(seg.transition.type);
  if (seg.j_cut) out.push('J-cut');
  if (seg.l_cut) out.push('L-cut');
  if (seg.video?.grade) out.push('grade');
  if (seg.video?.crop) out.push('crop');
  if (seg.video?.fade_in || seg.video?.fade_out) out.push('fade');
  if (seg.video?.mirror) out.push('mirror');
  if (seg.title) out.push('title');
  if (seg.audio?.src) out.push('music');
  else if (seg.audio) out.push('audio');
  return out;
}

// A CSS filter that previews a grade on a still (an approximation: the render uses ffmpeg's
// eq / colortemperature, or EffectCraft).
export function gradeFilter(g = {}) {
  const ex = g.exposure ?? 0;
  const t = g.temperature ?? 0;
  return [`brightness(${(2 ** ex).toFixed(3)})`, `saturate(${g.saturation ?? 1})`, `contrast(${g.contrast ?? 1})`,
    t ? `sepia(${Math.min(Math.abs(t) / 100, 0.5).toFixed(2)}) hue-rotate(${t > 0 ? -10 : 170}deg)` : ''].filter(Boolean).join(' ');
}
