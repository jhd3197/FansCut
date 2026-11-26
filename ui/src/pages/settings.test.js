import { describe, expect, it } from 'vitest';
import { setPath, tagName, toggleIn } from './Settings.jsx';

describe('settings helpers', () => {
  it('shows tag names, never the fc: id', () => {
    expect(tagName({ id: 'fc:no-face', name: 'No face' })).toBe('No face');
    expect(tagName({ id: 'fc:close-up' })).toBe('close up');
  });

  it('keeps switched-off families as a list', () => {
    expect(toggleIn([], 'mood', false)).toEqual(['mood']);
    expect(toggleIn(['mood'], 'mood', false)).toEqual(['mood']);
    expect(toggleIn(['mood', 'content'], 'mood', true)).toEqual(['content']);
  });

  it('sets nested poster weights without touching the rest', () => {
    const cfg = { posters: { layout: 'frame', weights: { exposure: 30, detail: 20 } } };
    const next = setPath(cfg, 'posters.weights.detail', 45);
    expect(next.posters.weights).toEqual({ exposure: 30, detail: 45 });
    expect(next.posters.layout).toBe('frame');
    expect(cfg.posters.weights.detail).toBe(20);
  });
});
