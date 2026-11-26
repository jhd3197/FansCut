// @ts-check
// Stills of a cut: where to take them, how good each one is, and the poster made from the
// best ones. The scoring is pure (pixel data in, numbers out) so it can be tested; the
// loading and drawing helpers at the end need a browser.

export const DEFAULT_WEIGHTS = { exposure: 30, contrast: 25, color: 25, detail: 20 };
export const WEIGHT_KEYS = /** @type {const} */ (['exposure', 'contrast', 'color', 'detail']);
export const LAYOUTS = /** @type {const} */ (['frame', 'strip', 'grid']);

// clamped to 0..1 and rounded, so float noise on flat frames reads as 0
const clamp01 = (v) => Math.round((v < 0 ? 0 : v > 1 ? 1 : v) * 1e4) / 1e4;

// `n` times evenly spread inside [start, end] (centres of n equal slices).
export function stillTimes(start, end, n) {
  const len = Math.max(0, end - start);
  return Array.from({ length: n }, (_, i) => Math.round((start + (len * (i + 0.5)) / n) * 10) / 10);
}

// Quality measures of one image, each 0..1, from RGBA pixel data (canvas getImageData).
// exposure: mean luma close to mid grey; contrast: luma spread; color: colourfulness
// (Hasler & Suesstrunk); detail: mean gradient (edge energy). `luma` is the mean itself.
/**
 * @param {ArrayLike<number>} data RGBA bytes, row-major
 * @param {number} width
 * @param {number} height
 */
export function stillMetrics(data, width, height) {
  const n = width * height;
  if (!n || data.length < n * 4) return { exposure: 0, contrast: 0, color: 0, detail: 0, luma: 0 };
  const lum = new Float32Array(n);
  let sumL = 0; let sumL2 = 0;
  let sumRg = 0; let sumRg2 = 0; let sumYb = 0; let sumYb2 = 0;
  for (let i = 0; i < n; i += 1) {
    const r = data[i * 4] / 255; const g = data[i * 4 + 1] / 255; const b = data[i * 4 + 2] / 255;
    const l = 0.2126 * r + 0.7152 * g + 0.0722 * b;
    lum[i] = l; sumL += l; sumL2 += l * l;
    const rg = r - g; const yb = 0.5 * (r + g) - b;
    sumRg += rg; sumRg2 += rg * rg; sumYb += yb; sumYb2 += yb * yb;
  }
  const mean = sumL / n;
  const sd = Math.sqrt(Math.max(0, sumL2 / n - mean * mean));
  const mRg = sumRg / n; const mYb = sumYb / n;
  const sdRg = Math.sqrt(Math.max(0, sumRg2 / n - mRg * mRg));
  const sdYb = Math.sqrt(Math.max(0, sumYb2 / n - mYb * mYb));
  const colourful = Math.hypot(sdRg, sdYb) + 0.3 * Math.hypot(mRg, mYb);
  let grad = 0; let cnt = 0;
  for (let y = 0; y < height - 1; y += 1) {
    for (let x = 0; x < width - 1; x += 1) {
      const i = y * width + x;
      grad += Math.abs(lum[i + 1] - lum[i]) + Math.abs(lum[i + width] - lum[i]);
      cnt += 1;
    }
  }
  return {
    exposure: clamp01(1 - Math.abs(mean - 0.5) * 2),
    contrast: clamp01(sd / 0.3), // a well-spread frame has an sd near 0.25-0.3
    color: clamp01(colourful / 0.45), // ~0.43 is "extremely colourful" on this scale
    detail: clamp01((cnt ? grad / cnt : 0) / 0.15),
    luma: mean,
  };
}

// Weights with every key present and inside 0..50; anything missing takes the default.
export function cleanWeights(w) {
  const out = {};
  for (const k of WEIGHT_KEYS) {
    const v = Number(w?.[k]);
    out[k] = Number.isFinite(v) ? Math.min(50, Math.max(0, v)) : DEFAULT_WEIGHTS[k];
  }
  return out;
}

// Weighted mean of the measures (0..1). Nearly black or white frames are pushed down.
export function scoreStill(m, weights = DEFAULT_WEIGHTS) {
  if (!m) return 0;
  const w = cleanWeights(weights);
  const total = WEIGHT_KEYS.reduce((a, k) => a + w[k], 0);
  if (!total) return 0;
  const v = WEIGHT_KEYS.reduce((a, k) => a + w[k] * (m[k] || 0), 0) / total;
  return m.luma < 0.08 || m.luma > 0.92 ? v * 0.2 : v;
}

// Stills that have measures, best first, each with its `score`.
export function rankStills(stills, metrics, weights) {
  return stills
    .filter((s) => metrics[s.src])
    .map((s) => ({ ...s, score: scoreStill(metrics[s.src], weights) }))
    .sort((a, b) => b.score - a.score);
}

export const posterSlots = (layout) => (layout === 'grid' ? 4 : layout === 'strip' ? 3 : 1);

// The stills a poster uses: the user's picks first (in pick order), then the best of the
// rest, preferring other sources, then other scenes, so a strip is not three of one shot.
export function posterStills(ranked, layout, picks = []) {
  const slots = posterSlots(layout);
  const bySrc = new Map(ranked.map((s) => [s.src, s]));
  const out = picks.map((p) => bySrc.get(p)).filter(Boolean).slice(0, slots);
  for (const pass of ['source', 'scene', 'any']) {
    for (const s of ranked) {
      if (out.length >= slots) return out;
      if (out.includes(s)) continue;
      if (pass === 'source' && out.some((o) => o.source === s.source)) continue;
      if (pass === 'scene' && out.some((o) => o.scene === s.scene)) continue;
      out.push(s);
    }
  }
  return out;
}

// -- browser helpers ---------------------------------------------------------------

const images = new Map(); // src -> Promise<HTMLImageElement | null>
const measured = new Map(); // src -> metrics | null

export function loadImage(src) {
  if (!images.has(src)) {
    images.set(src, new Promise((resolve) => {
      const im = new Image();
      im.onload = () => resolve(im);
      im.onerror = () => { images.delete(src); resolve(null); };
      im.src = src;
    }));
  }
  return images.get(src);
}

export const knownMetrics = (src) => measured.get(src);

// Measure one still on a small canvas (96 px wide is plenty for these averages).
export async function measureStill(src) {
  if (measured.has(src)) return measured.get(src);
  const im = await loadImage(src);
  let m = null;
  if (im && im.naturalWidth) {
    const w = 96;
    const h = Math.max(1, Math.round((w * im.naturalHeight) / im.naturalWidth));
    const cv = document.createElement('canvas');
    cv.width = w; cv.height = h;
    const ctx = cv.getContext('2d', { willReadFrequently: true });
    if (ctx) {
      ctx.drawImage(im, 0, 0, w, h);
      try { m = stillMetrics(ctx.getImageData(0, 0, w, h).data, w, h); } catch { m = null; }
    }
  }
  if (m) measured.set(src, m);
  return m;
}

function cover(c, im, x, y, w, h) {
  if (!im) { c.fillStyle = '#123A49'; c.fillRect(x, y, w, h); return; }
  const s = Math.max(w / im.naturalWidth, h / im.naturalHeight);
  const sw = w / s; const sh = h / s;
  c.drawImage(im, (im.naturalWidth - sw) / 2, (im.naturalHeight - sh) * 0.45, sw, sh, x, y, w, h);
}

// Draw a 2:3 poster from the chosen stills onto `canvas`. `stale()` true after the
// images load means a newer draw has started: this one gives up.
export async function drawPoster(canvas, srcs, { layout = 'frame', title = '', sub = '', stale = () => false } = {}) {
  const c = canvas?.getContext('2d');
  if (!c) return;
  const ims = await Promise.all(srcs.map(loadImage));
  if (document.fonts?.ready) await document.fonts.ready;
  if (stale()) return;
  const W = canvas.width; const H = canvas.height; const k = W / 600; const g = 4 * k;
  c.fillStyle = '#000'; c.fillRect(0, 0, W, H);
  if (layout === 'strip') ims.slice(0, 3).forEach((im, i) => cover(c, im, 0, (i * (H + g)) / 3, W, (H - 2 * g) / 3));
  else if (layout === 'grid') ims.slice(0, 4).forEach((im, i) => cover(c, im, ((i % 2) * (W + g)) / 2, (Math.floor(i / 2) * (H + g)) / 2, (W - g) / 2, (H - g) / 2));
  else cover(c, ims[0], 0, 0, W, H);
  if (!title) return;
  const gr = c.createLinearGradient(0, H * 0.45, 0, H);
  gr.addColorStop(0, 'rgba(0,0,0,0)'); gr.addColorStop(1, 'rgba(0,0,0,.88)');
  c.fillStyle = gr; c.fillRect(0, H * 0.45, W, H * 0.55);
  c.fillStyle = '#fff';
  const size = 64 * k;
  c.font = `700 ${size}px Outfit, "DM Sans", sans-serif`;
  const lines = [];
  let ln = '';
  for (const word of title.split(/\s+/)) {
    const t = ln ? `${ln} ${word}` : word;
    if (c.measureText(t).width > W - 80 * k && ln) { lines.push(ln); ln = word; } else ln = t;
  }
  lines.push(ln);
  const base = H - (sub ? 74 : 44) * k;
  lines.forEach((l, i) => c.fillText(l, 40 * k, base - (lines.length - 1 - i) * size * 1.02));
  if (sub) {
    c.font = `500 ${22 * k}px "DM Sans", sans-serif`;
    c.fillStyle = 'rgba(255,255,255,.75)';
    c.fillText(sub, 40 * k, H - 34 * k);
  }
}
