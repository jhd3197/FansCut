import { afterEach, describe, expect, it, vi } from 'vitest';
import { fetchPath, resetStore, revalidate } from './store.js';

afterEach(() => { resetStore(); vi.restoreAllMocks(); });

function mockFetch(bodies) {
  let i = 0;
  return vi.spyOn(globalThis, 'fetch').mockImplementation(async () => ({
    ok: true, status: 200, statusText: 'OK', json: async () => bodies[Math.min(i++, bodies.length - 1)],
  }));
}

describe('shared GET cache', () => {
  it('makes one request for concurrent reads of a path', async () => {
    const f = mockFetch([{ jobs: [] }]);
    const [a, b] = await Promise.all([fetchPath('/jobs'), fetchPath('/jobs')]);
    expect(a).toEqual({ jobs: [] });
    expect(b).toBe(a);
    expect(f).toHaveBeenCalledTimes(1);
  });

  it('serves a fresh entry from memory and refetches when forced', async () => {
    const f = mockFetch([{ n: 1 }, { n: 2 }]);
    await fetchPath('/cuts');
    expect(await fetchPath('/cuts')).toEqual({ n: 1 });
    expect(await fetchPath('/cuts', { force: true })).toEqual({ n: 2 });
    expect(f).toHaveBeenCalledTimes(2);
  });

  it('forgets paths nobody shows on revalidate', async () => {
    const f = mockFetch([{ n: 1 }, { n: 2 }]);
    await fetchPath('/suggestions');
    revalidate();
    await fetchPath('/suggestions');
    expect(f).toHaveBeenCalledTimes(2);
  });
});
