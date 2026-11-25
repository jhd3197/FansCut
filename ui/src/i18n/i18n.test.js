import { describe, expect, it } from 'vitest';
import en from './en.js';
import { addLocale, setLocale, t } from './index.js';

describe('t', () => {
  it('fills in variables and falls back to English', () => {
    setLocale(['en']);
    expect(t('nav.running', { n: 3 })).toBe('3 running');
    addLocale('xx', { 'nav.jobs': 'Trabajos' });
    expect(setLocale(['xx-YY', 'en'])).toBe('xx');
    expect(t('nav.jobs')).toBe('Trabajos');
    expect(t('nav.library')).toBe(en['nav.library']);
    expect(t('no.such.key')).toBe('no.such.key');
    setLocale(['en']);
  });
});
