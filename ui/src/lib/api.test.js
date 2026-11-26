import { afterEach, describe, expect, it, vi } from 'vitest';
import { api, ApiError, artFallback, artSrcSet, artUrl, detailOf, seriesPath, splitRef } from './api.js';

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

describe('artUrl', () => {
  const tmdb = 'https://image.tmdb.org/t/p/original/abc.jpg';

  it('loads TMDB art straight from its CDN at a size near the one asked for', () => {
    expect(artUrl(tmdb, 'poster', 500)).toBe('https://image.tmdb.org/t/p/w500/abc.jpg');
    expect(artUrl(tmdb, 'poster', 300)).toBe('https://image.tmdb.org/t/p/w342/abc.jpg');
    expect(artUrl(tmdb, 'backdrop', 800)).toBe('https://image.tmdb.org/t/p/w780/abc.jpg');
    expect(artUrl(tmdb, 'poster', 2000)).toBe('https://image.tmdb.org/t/p/w780/abc.jpg'); // never original
    expect(artSrcSet(tmdb)).toContain('/w780/abc.jpg 800w');
  });

  it('sends library keys through FansCut', () => {
    expect(artUrl('plexmovie:55', 'poster', 300)).toBe('/api/art/poster/plexmovie%3A55?w=300&v=3');
    expect(artSrcSet('plexmovie:55')).toContain('?w=800&v=3 800w');
  });
});

describe('art fallbacks', () => {
  const ref = 'https://image.tmdb.org/t/p/original/abc.jpg#k=radarr%3A1';
  it('splits the item key off a CDN URL', () => {
    expect(splitRef(ref)).toEqual({ url: 'https://image.tmdb.org/t/p/original/abc.jpg', key: 'radarr:1' });
    expect(splitRef('plex:9')).toEqual({ url: null, key: 'plex:9' });
  });
  it('loads the CDN first and the local copy after', () => {
    expect(artUrl(ref, 'poster', 300)).toBe('https://image.tmdb.org/t/p/w342/abc.jpg');
    expect(artFallback(ref, 'poster', 300)).toBe('/api/art/poster/radarr%3A1?w=300&v=3');
    expect(artFallback('plex:9')).toBeNull();
  });
  it('uses TVDB thumbnails for small tiles', () => {
    const tvdb = 'https://artworks.thetvdb.com/banners/posters/77-1.jpg';
    expect(artUrl(tvdb, 'poster', 300)).toBe('https://artworks.thetvdb.com/banners/posters/77-1_t.jpg');
    expect(artUrl(tvdb, 'poster', 800)).toBe(tvdb);
    expect(artSrcSet(tvdb)).toBe('https://artworks.thetvdb.com/banners/posters/77-1_t.jpg 340w, https://artworks.thetvdb.com/banners/posters/77-1.jpg 680w');
  });
});
