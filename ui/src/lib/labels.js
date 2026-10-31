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
