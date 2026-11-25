// @ts-check
// A small message layer: t('nav.jobs'), t('nav.running', { n: 2 }).
// The language follows the browser (navigator.languages); a locale file only
// has to list the keys it translates, the rest stay English.
import en from './en.js';

const LOCALES = { en };
let messages = en;

export function setLocale(list = typeof navigator !== 'undefined' ? navigator.languages || [navigator.language] : []) {
  const code = (list || []).map((l) => String(l).toLowerCase().split('-')[0]).find((c) => LOCALES[c]);
  messages = { ...en, ...(LOCALES[code] || {}) };
  return code || 'en';
}

export function addLocale(code, dict) {
  LOCALES[code] = dict;
}

export function t(key, vars) {
  const text = messages[key] ?? en[key] ?? key;
  return vars ? text.replace(/\{(\w+)\}/g, (_, k) => (vars[k] != null ? String(vars[k]) : `{${k}}`)) : text;
}

setLocale();
