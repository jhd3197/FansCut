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
