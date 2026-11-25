export const LABELS = ['canon', 'mixed', 'filler', 'unknown'];

export const PRESET_NAMES = {
  canon_only: 'Canon only',
  canon_mixed: 'Canon + mixed',
  everything: 'Everything',
  custom: 'Custom (per episode)',
};

export const SOURCE_NAMES = {
  override: 'your override',
  anifiller: 'AniFiller',
  jikan: 'Jikan',
  none: 'no data',
};

export const labelTitle = (label, source) =>
  `${label}${source ? ` (${SOURCE_NAMES[source] || source})` : ''}`;

// Sidebar footer: which sources are on and where the library lives, from the
// (masked) settings. {label: 'Plex · Sonarr', root: '/media'} or null.
export function sourceSummary(cfg) {
  const lib = cfg?.library;
  if (!lib) return null;
  const on = [['Plex', lib.plex?.url], ['Sonarr', lib.sonarr?.url], ['Radarr', lib.radarr?.url],
    ['Folder', lib.folder?.path || lib.movies?.path]].filter(([, v]) => v).map(([n]) => n);
  const host = (u) => { try { return new URL(u).host; } catch { return u; } };
  const root = lib.folder?.path || lib.movies?.path || (lib.plex?.url && host(lib.plex.url)) || '';
  return { label: on.length ? on.join(' · ') : 'No source set up', root };
}