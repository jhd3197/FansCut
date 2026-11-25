// A small shared cache for GET requests, in the spirit of SWR:
// - one entry per path, shared by every component that reads it;
// - a component that mounts gets the cached data at once (no "Loading…"
//   flash on revisits) and a background refetch if it is older than FRESH_MS;
// - concurrent reads of one path make one request;
// - after any successful write (POST/PUT/DELETE) every mounted path is
//   refetched, so counts and lists elsewhere on screen never go stale;
// - coming back to the tab refetches what is on screen.
import { useCallback, useEffect, useState, useSyncExternalStore } from 'react';
import { api, setOnWrite } from './api.js';

const FRESH_MS = 2000;
const entries = new Map(); // path -> { data, error, at, promise }
const listeners = new Map(); // path -> Set(callback)
const inflightCount = { n: 0 };
const busyListeners = new Set();

function notify(path) {
  for (const fn of listeners.get(path) || []) fn();
}

function setBusy(delta) {
  inflightCount.n += delta;
  for (const fn of busyListeners) fn();
}

export function fetchPath(path, { force = false } = {}) {
  const e = entries.get(path);
  if (e?.promise) return e.promise;
  if (!force && e && Date.now() - e.at < FRESH_MS) return Promise.resolve(e.data);
  setBusy(1);
  const promise = api(path).then(
    (data) => {
      entries.set(path, { data, error: null, at: Date.now(), promise: null });
      notify(path);
      return data;
    },
    (error) => {
      const old = entries.get(path);
      entries.set(path, { data: old?.data ?? null, error, at: Date.now(), promise: null });
      notify(path);
      throw error;
    },
  ).finally(() => setBusy(-1));
  entries.set(path, { ...(e || { data: null, error: null, at: 0 }), promise });
  return promise;
}

// Refetch every path someone is showing (prefix limits it); forget the rest.
export function revalidate(prefix = '') {
  for (const path of [...entries.keys()]) {
    if (!path.startsWith(prefix)) continue;
    if (listeners.get(path)?.size) fetchPath(path, { force: true }).catch(() => {});
    else entries.delete(path);
  }
}

let pending = null;
export function revalidateSoon() {
  clearTimeout(pending);
  pending = setTimeout(() => revalidate(), 150);
}

setOnWrite(() => revalidateSoon());

if (typeof document !== 'undefined') {
  document.addEventListener('visibilitychange', () => {
    if (document.visibilityState === 'visible') revalidate();
  });
}

function subscribe(path, fn) {
  if (!listeners.has(path)) listeners.set(path, new Set());
  listeners.get(path).add(fn);
  return () => listeners.get(path)?.delete(fn);
}

// The shared, cached GET of `path` (null = don't load). `quiet`: errors are
// kept out of the result (for badges and other optional bits).
export function useApi(path, { quiet = false } = {}) {
  const sub = useCallback((fn) => (path ? subscribe(path, fn) : () => {}), [path]);
  const snap = useCallback(() => (path ? entries.get(path) : undefined), [path]);
  const e = useSyncExternalStore(sub, snap, snap);
  useEffect(() => {
    if (path) fetchPath(path).catch(() => {});
  }, [path]);
  const reload = useCallback(() => (path ? fetchPath(path, { force: true }).catch(() => {}) : Promise.resolve()), [path]);
  const data = e?.data ?? null;
  const error = quiet ? null : e?.error ?? null;
  return { data, error, loading: !!path && data == null && !(e?.error), reload };
}

// True while any cached request is in flight (for the top progress bar).
export function useBusy() {
  const [, force] = useState(0);
  useEffect(() => {
    const fn = () => force((n) => n + 1);
    busyListeners.add(fn);
    return () => busyListeners.delete(fn);
  }, []);
  return inflightCount.n > 0;
}

// Tests: start from an empty cache.
export function resetStore() {
  entries.clear();
  listeners.clear();
}
