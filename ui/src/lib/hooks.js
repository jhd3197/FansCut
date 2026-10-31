import { useCallback, useEffect, useRef, useState } from 'react';

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

// Poll `fn` every `ms` while `active`.
export function usePoll(fn, ms, active) {
  const ref = useRef(fn);
  useEffect(() => { ref.current = fn; });
  useEffect(() => {
    if (!active) return undefined;
    const t = setInterval(() => ref.current(), ms);
    return () => clearInterval(t);
  }, [ms, active]);
}

// Run an async action, keeping one message (ok or error) for a banner.
export function useAct() {
  const [msg, setMsg] = useState(null);
  const [busy, setBusy] = useState(false);
  const act = useCallback(async (fn, okText) => {
    setBusy(true);
    try {
      const res = await fn();
      setMsg(okText ? { ok: true, text: typeof okText === 'function' ? okText(res) : okText } : null);
      return res;
    } catch (e) {
      setMsg({ ok: false, text: e.message });
      return undefined;
    } finally {
      setBusy(false);
    }
  }, []);
  return { act, msg, busy, clear: () => setMsg(null) };
}
