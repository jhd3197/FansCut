// @ts-check
// Thin fetch wrapper: JSON in, JSON out, errors carry the server's `detail`.

export class ApiError extends Error {
  constructor(message, status, code) {
    super(message);
    this.name = 'ApiError';
    this.status = status;
    this.code = code;
  }
}

// Fired when the server wants the API key (server.auth = "api_key"); App shows the key gate.
export const AUTH_EVENT = 'fanscut:auth';

// FastAPI sends detail as a string, or as a list of {loc, msg} for 422s.
export function detailOf(body, fallback) {
  const d = body && body.detail;
  if (typeof d === 'string') return d;
  if (Array.isArray(d)) return d.map((x) => x.msg).join('; ') || fallback;
  return fallback;
}

// Set by store.js: called after a successful write so lists on screen refetch.
let onWrite = null;
export function setOnWrite(fn) { onWrite = fn; }

// `read: true` marks a POST that only computes (a dry run): it changes nothing.
/**
 * @param {string} path
 * @param {{ method?: string, body?: unknown, read?: boolean }} [opts]
 */
export async function api(path, { method = 'GET', body, read = false } = {}) {
  const init = { method, headers: {} };
  if (body !== undefined) {
    init.headers['Content-Type'] = 'application/json';
    init.body = JSON.stringify(body);
  }
  let res;
  try {
    res = await fetch(`/api${path}`, init);
  } catch {
    throw new ApiError('cannot reach the FansCut server', 0);
  }
  const data = await res.json().catch(() => null);
  if (!res.ok) {
    const code = data && data.code;
    if (res.status === 401 && code === 'auth') window.dispatchEvent(new Event(AUTH_EVENT));
    throw new ApiError(detailOf(data, `${res.status} ${res.statusText}`), res.status, code);
  }
  if (method !== 'GET' && !read && onWrite) onWrite(path);
  return data;
}

// Series keys look like "folder:Harbor Lights"; they sit in a path segment.
export const seriesPath = (key) => `/series/${encodeURIComponent(key)}`;

// An item's art. `ref` is what the API gives: a direct https URL (TMDB or TVDB,
// from Radarr/Sonarr), or a library key, which FansCut serves from Plex or files.
// A URL may end in "#k=<key>": the item's key, for falling back to FansCut's own
// copy when the CDN fails (browsers ignore the fragment when loading).
// Bump when the server starts choosing different art (v3: new art cache keys),
// so browsers drop images they cached under the old URLs.
const ART_VERSION = 3;
const TMDB = /^https:\/\/image\.tmdb\.org\/t\/p\/[^/]+\//;
const TVDB = /^https:\/\/artworks\.thetvdb\.com\/.+\.(jpe?g|png|webp)$/i;
const TMDB_SIZES = { poster: [185, 342, 500, 780], backdrop: [300, 780, 1280] };
const TVDB_THUMB = 340; // TVDB's "_t" variants are about this wide

// { url, key }: the CDN URL (no fragment) and the item key it carries, if any.
export function splitRef(ref) {
  const m = /^(https?:\/\/[^#]+)(?:#k=(.*))?$/.exec(ref || '');
  if (!m) return { url: null, key: ref };
  return { url: m[1], key: m[2] ? decodeURIComponent(m[2]) : null };
}

const local = (key, kind, w) => `/api/art/${kind}/${encodeURIComponent(key)}?w=${w}&v=${ART_VERSION}`;

export function artUrl(ref, kind = 'poster', w = 500) {
  const { url, key } = splitRef(ref);
  if (!url) return local(key, kind, w);
  if (TMDB.test(url)) {
    // never "original": that can be several MB; the largest sized one is plenty
    const sizes = TMDB_SIZES[kind];
    const size = sizes.find((s) => s >= w * 0.95) || sizes[sizes.length - 1]; // 780 serves 800
    return url.replace(TMDB, `https://image.tmdb.org/t/p/w${size}/`);
  }
  if (TVDB.test(url) && w <= TVDB_THUMB && !/_t\.[a-z]+$/i.test(url)) {
    return url.replace(/\.([a-z]+)$/i, '_t.$1');
  }
  return url;
}

// FansCut's own copy of a CDN image, when the ref names its item; else null.
export function artFallback(ref, kind = 'poster', w = 500) {
  const { url, key } = splitRef(ref);
  return url && key ? local(key, kind, w) : null;
}

// srcset across a few widths, so 4K screens get sharp art and phones small files.
export function artSrcSet(ref, kind = 'poster', widths = kind === 'poster' ? [300, 500, 800] : [480, 800, 1280]) {
  const { url } = splitRef(ref);
  if (url && TVDB.test(url)) return `${artUrl(ref, kind, TVDB_THUMB)} ${TVDB_THUMB}w, ${url} 680w`;
  if (url && !TMDB.test(url)) return undefined;
  return widths.map((w) => `${artUrl(ref, kind, w)} ${w}w`).join(', ');
}
