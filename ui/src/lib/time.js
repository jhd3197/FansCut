// "1:05:09" / "24:03" / "0:07.5" for display; parse accepts the same plus plain seconds.
export function fmtTime(sec, decimals = 0) {
  if (sec == null || Number.isNaN(sec)) return '–';
  const s = Math.max(sec, 0);
  const whole = Math.floor(s);
  const h = Math.floor(whole / 3600);
  const m = Math.floor((whole % 3600) / 60);
  const rest = decimals ? (s - h * 3600 - m * 60).toFixed(decimals).padStart(3 + decimals, '0') : String(whole % 60).padStart(2, '0');
  return h ? `${h}:${String(m).padStart(2, '0')}:${rest}` : `${m}:${rest}`;
}

export function parseTime(text) {
  const t = String(text).trim();
  if (!t) return null;
  const parts = t.split(':');
  if (parts.length > 3 || parts.some((p) => !/^\d+(\.\d+)?$/.test(p))) return null;
  return parts.reduce((acc, p) => acc * 60 + Number(p), 0);
}

export function fmtDuration(sec) {
  if (sec == null) return '–';
  if (sec < 120) return `${Math.round(sec)}s`;
  const m = Math.round(sec / 60);
  return m >= 60 ? `${Math.floor(m / 60)}h ${String(m % 60).padStart(2, '0')}m` : `${m}m`;
}
