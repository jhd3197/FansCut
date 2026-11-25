import { useCallback, useEffect, useRef, useState } from 'react';
import { useSearchParams } from 'react-router';
import { toast } from './toast.js';

// Load on mount (and when `deps` change); `reload()` refetches without blanking the page.
export function useLoad(fn, deps = []) {
  const [state, setState] = useState({ data: null, error: null, loading: true });
  const fnRef = useRef(fn);
  useEffect(() => { fnRef.current = fn; });
  const seq = useRef(0);
  const reload = useCallback(() => {
    const mine = ++seq.current;
    return fnRef.current().then(
      (data) => { if (mine === seq.current) setState({ data, error: null, loading: false }); },
      (error) => { if (mine === seq.current) setState((s) => ({ ...s, error, loading: false })); },
    );
  }, []);
  // eslint-disable-next-line react-hooks/exhaustive-deps
  useEffect(() => { reload(); }, [reload, ...deps]);
  return { ...state, reload };
}

// Poll `fn` every `ms` while `active` and the tab is visible; a hidden tab
// stops polling and catches up once when it is shown again.
export function usePoll(fn, ms, active) {
  const ref = useRef(fn);
  useEffect(() => { ref.current = fn; });
  useEffect(() => {
    if (!active) return undefined;
    let t = null;
    const start = () => { if (t == null) t = setInterval(() => ref.current(), ms); };
    const stop = () => { clearInterval(t); t = null; };
    const onVis = () => {
      if (document.visibilityState === 'visible') { ref.current(); start(); } else stop();
    };
    if (document.visibilityState !== 'hidden') start();
    document.addEventListener('visibilitychange', onVis);
    return () => { stop(); document.removeEventListener('visibilitychange', onVis); };
  }, [ms, active]);
}

// A value that follows `value` after it has been still for `ms` (search boxes).
export function useDebounced(value, ms = 250) {
  const [v, setV] = useState(value);
  useEffect(() => {
    const t = setTimeout(() => setV(value), ms);
    return () => clearTimeout(t);
  }, [value, ms]);
  return v;
}

// The browser tab's title: "<page> · FansCut".
export function usePageTitle(title) {
  useEffect(() => {
    document.title = title ? `${title} · FansCut` : 'FansCut';
  }, [title]);
}

// Run an async action; its outcome (ok text or error) shows as a toast, so it
// survives navigating away. `msg` stays for callers that render it inline, but
// only when they ask for it ({ inline: true }).
export function useAct({ inline = false } = {}) {
  const [msg, setMsg] = useState(null);
  const [busy, setBusy] = useState(false);
  const act = useCallback(async (fn, okText) => {
    setBusy(true);
    try {
      const res = await fn();
      const text = okText ? (typeof okText === 'function' ? okText(res) : okText) : null;
      if (text) {
        if (inline) setMsg({ ok: true, text }); else toast(text);
      } else setMsg(null);
      return res;
    } catch (e) {
      if (inline) setMsg({ ok: false, text: e.message }); else toast(e.message, { ok: false });
      return undefined;
    } finally {
      setBusy(false);
    }
  }, [inline]);
  return { act, msg, busy, clear: () => setMsg(null) };
}

// State kept in the URL's query string (?key=value), so Back, reload and shared
// links keep it. The default is left out of the URL. Writes replace the history
// entry rather than adding one per keystroke.
export function useUrlState(key, fallback = '') {
  const [params, setParams] = useSearchParams();
  const value = params.get(key) ?? fallback;
  const set = useCallback((v) => {
    setParams((p) => {
      const n = new URLSearchParams(p);
      if (v == null || v === '' || v === fallback) n.delete(key); else n.set(key, v);
      return n;
    }, { replace: true });
  }, [key, fallback, setParams]);
  return [value, set];
}

// Discover shelf layout, kept in this browser: hidden shelves and a preferred order.
const LAYOUT_KEY = 'fanscut.shelves';
function readLayout() {
  try { return JSON.parse(localStorage.getItem(LAYOUT_KEY)) || { hidden: [], order: [] }; } catch { return { hidden: [], order: [] }; }
}
export function useShelfLayout(names) {
  const [layout, setLayout] = useState(readLayout);
  const save = (next) => { setLayout(next); try { localStorage.setItem(LAYOUT_KEY, JSON.stringify(next)); } catch { /* private mode */ } };
  const rank = (n) => { const i = layout.order.indexOf(n); return i === -1 ? 1e6 + names.indexOf(n) : i; };
  const ordered = [...names].sort((a, b) => rank(a) - rank(b));
  const visible = ordered.filter((n) => !layout.hidden.includes(n));
  const hidden = ordered.filter((n) => layout.hidden.includes(n));
  const move = (name, dir) => {
    const list = [...visible];
    const i = list.indexOf(name);
    const j = i + dir;
    if (i < 0 || j < 0 || j >= list.length) return;
    [list[i], list[j]] = [list[j], list[i]];
    save({ ...layout, order: [...list, ...hidden] });
  };
  const hide = (name) => save({ ...layout, hidden: [...layout.hidden, name] });
  const showAll = () => save({ ...layout, hidden: [] });
  return { visible, hidden, move, hide, showAll };
}
