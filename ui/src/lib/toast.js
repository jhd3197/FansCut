// Global toasts: toast('Saved') from anywhere; <Toasts /> in the shell shows them.
// Success toasts close after 4 s, errors after 8 s; both can be dismissed.
const listeners = new Set();
let items = [];
let next = 1;

function emit() {
  for (const fn of listeners) fn(items);
}

export function dismiss(id) {
  items = items.filter((t) => t.id !== id);
  emit();
}

export function toast(text, { ok = true, ms } = {}) {
  const id = next++;
  items = [...items.slice(-3), { id, text, ok }];
  emit();
  setTimeout(() => dismiss(id), ms ?? (ok ? 4000 : 8000));
  return id;
}

export function subscribeToasts(fn) {
  listeners.add(fn);
  fn(items);
  return () => listeners.delete(fn);
}
