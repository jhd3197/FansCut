import { afterEach, describe, expect, it, vi } from 'vitest';
import { api, ApiError, detailOf, seriesPath } from './api.js';

const reply = (status, body) => vi.fn().mockResolvedValue({
  ok: status < 400, status, statusText: 'x', json: () => Promise.resolve(body),
});

afterEach(() => vi.unstubAllGlobals());

describe('api client', () => {
  it('prefixes /api and sends JSON bodies', async () => {
    const f = reply(200, { ok: true });
    vi.stubGlobal('fetch', f);
    await expect(api('/x', { method: 'PUT', body: { a: 1 } })).resolves.toEqual({ ok: true });
    expect(f).toHaveBeenCalledWith('/api/x', {
      method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: '{"a":1}',
    });
  });

  it('turns an error response into ApiError with the server detail', async () => {
    vi.stubGlobal('fetch', reply(404, { detail: 'series not found' }));
    await expect(api('/series/nope')).rejects.toMatchObject({ name: 'ApiError', message: 'series not found', status: 404 });
  });

  it('reports an unreachable server plainly', async () => {
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new TypeError('failed')));
    await expect(api('/x')).rejects.toBeInstanceOf(ApiError);
    await expect(api('/x')).rejects.toThrow(/cannot reach/);
  });

  it('reads validation error lists', () => {
    expect(detailOf({ detail: [{ msg: 'bad a' }, { msg: 'bad b' }] }, 'f')).toBe('bad a; bad b');
    expect(detailOf(null, 'fallback')).toBe('fallback');
  });

  it('encodes series keys for paths', () => {
    expect(seriesPath('folder:Harbor Lights')).toBe('/series/folder%3AHarbor%20Lights');
  });
});
