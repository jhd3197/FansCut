// @ts-check
// The join check in Arrange: does a cut between two scenes clip someone's line?
// Lines come from /api/scenes/transcript?speech=true (subtitle speech, sound
// descriptions left out). `null` lines = the film has no speech data: unchecked.

export const AIR = 0.7; // how much room "Give it air" leaves after the last line
const RUSHED = 0.5; // a line ending closer than this to the cut feels rushed
const CLIPPED = 0.05;
const REACH = 20; // how far "Give it air" may move an edge looking for quiet

/** The first cut at or after t with `air` seconds of quiet before it, or null past `reach`. */
export function quietAfter(lines, t, air = AIR, reach = REACH) {
  let cut = t;
  for (;;) {
    const hit = (lines || []).filter((l) => l.start < cut && l.end > cut - air);
    if (!hit.length) return cut;
    cut = Math.max(...hit.map((l) => l.end)) + air;
    if (cut - t > reach) return null;
  }
}

/** The last cut at or before t that no line runs across, `lead` seconds before the
 * line after it; null past `reach`. */
export function quietBefore(lines, t, lead = AIR / 2, reach = REACH) {
  let cut = t;
  for (;;) {
    const hit = (lines || []).filter((l) => l.start < cut + CLIPPED && l.end > cut - CLIPPED);
    if (!hit.length) return cut;
    cut = Math.min(...hit.map((l) => l.start)) - lead;
    if (t - cut > reach) return null;
  }
}

const r1 = (x) => Math.round(x * 10) / 10;
const r3 = (x) => Math.round(x * 1000) / 1000;

/** Lines overlapping [a, b]. */
export function linesIn(lines, a, b) {
  return (lines || []).filter((l) => l.end > a && l.start < b);
}

/**
 * The join from segment `a` to segment `b` ({src, start, end}), with the speech lines
 * of each one's film (or null). Returns {state: 'check'|'clean'|'unchecked', short,
 * text, fix} where fix = {which: 'a'|'b', edge: 'start'|'end', delta} or null.
 */
export function joinCheck(a, b, linesA, linesB) {
  if (!a || !b) return null;
  if (a.src === b.src && Math.abs(a.end - b.start) < CLIPPED) {
    return { state: 'clean', short: '', text: 'Continuous: the next scene picks up where this one ends.', fix: null };
  }
  if (!linesA || !linesB) {
    return {
      state: 'unchecked', short: '', fix: null,
      text: 'This film has not been analysed, so FansCut cannot check the join. Watch it yourself.',
    };
  }
  // the end of a: the last line that starts before the cut
  const said = linesA.filter((l) => l.start < a.end && l.end > a.start);
  const last = said[said.length - 1];
  if (last) {
    const gap = r1(a.end - last.end);
    // extend past the talk to the first quiet moment; failing that, end before the line
    const to = quietAfter(linesA, a.end) ?? quietBefore(linesA, a.end) ?? last.end + AIR;
    const fix = { which: 'a', edge: 'end', delta: r3(to - a.end) };
    if (a.end - last.end < CLIPPED) {
      return {
        state: 'check', short: 'Last line runs into the cut',
        text: 'The last line runs right into the cut, so it will sound clipped.',
        fix,
      };
    }
    if (gap < RUSHED) {
      return {
        state: 'check', short: `Line ends ${gap.toFixed(1)}s before the cut`,
        text: `The last line ends ${gap.toFixed(1)}s before the cut. It will feel rushed.`,
        fix,
      };
    }
  }
  // the start of b: a line already running when b comes in
  const mid = linesB.find((l) => l.start < b.start - CLIPPED && l.end > b.start + CLIPPED);
  if (mid) {
    return {
      state: 'check', short: 'Next scene starts mid-line',
      text: 'The next scene starts in the middle of a line.',
      fix: { which: 'b', edge: 'start', delta: r3((quietBefore(linesB, b.start) ?? mid.start - AIR / 2) - b.start) },
    };
  }
  if (!last) return { state: 'clean', short: '', text: 'Nobody speaks in this scene, so there is nothing to clip.', fix: null };
  return { state: 'clean', short: '', text: `Clean. The cut lands ${r1(a.end - last.end).toFixed(1)}s after the last line.`, fix: null };
}

/**
 * Move one item of a flat list of {pi, seg} rows to index `to`; the moved row joins the
 * part of the row it lands on. Returns the new parts' segment lists (same part count).
 */
export function moveAcrossParts(rows, from, to, partCount) {
  if (from === to || from < 0 || to < 0 || from >= rows.length || to >= rows.length) return null;
  const target = rows[to].pi;
  const next = [...rows];
  const [x] = next.splice(from, 1);
  next.splice(to, 0, { ...x, pi: target });
  const out = Array.from({ length: partCount }, () => []);
  for (const r of next) out[r.pi].push(r.seg);
  return out;
}
