// Thin fetch wrapper: JSON in, JSON out, errors carry the server's `detail`.

export class ApiError extends Error {
  constructor(message, status) {
    super(message);
    this.name = 'ApiError';
    this.status = status;
  }
}

// FastAPI sends detail as a string, or as a list of {loc, msg} for 422s.
export function detailOf(body, fallback) {
  const d = body && body.detail;
  if (typeof d === 'string') return d;
  if (Array.isArray(d)) return d.map((x) => x.msg).join('; ') || fallback;
  return fallback;
}

export async function api(path, { method = 'GET', body } = {}) {
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
  if (!res.ok) throw new ApiError(detailOf(data, `${res.status} ${res.statusText}`), res.status);
  return data;
}

// Series keys look like "folder:Harbor Lights"; they sit in a path segment.
export const seriesPath = (key) => `/series/${encodeURIComponent(key)}`;

// An item's art. `ref` is what the API gives: a direct https URL (TMDB, from
// Radarr/Sonarr) or a library key, which FansCut serves from Plex or files.
const TMDB = /^https:\/\/image\.tmdb\.org\/t\/p\/[^/]+\//;
const TMDB_SIZES = { poster: [185, 342, 500, 780], backdrop: [300, 780, 1280] };

export function artUrl(ref, kind = 'poster', w = 500) {
  if (TMDB.test(ref)) {
    const size = TMDB_SIZES[kind].find((s) => s >= w * 0.95) || 'original'; // 780 serves 800
    return ref.replace(TMDB, `https://image.tmdb.org/t/p/${size === 'original' ? size : `w${size}`}/`);
  }
  if (/^https?:\/\//.test(ref)) return ref;
  return `/api/art/${kind}/${encodeURIComponent(ref)}?w=${w}`;
}

// srcset across a few widths, so 4K screens get sharp art and phones small files.
export function artSrcSet(ref, kind = 'poster', widths = kind === 'poster' ? [300, 500, 800] : [480, 800, 1280]) {
  if (/^https?:\/\//.test(ref) && !TMDB.test(ref)) return undefined;
  return widths.map((w) => `${artUrl(ref, kind, w)} ${w}w`).join(', ');
}
