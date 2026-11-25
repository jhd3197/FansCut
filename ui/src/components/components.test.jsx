// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { LabelBadge, MoreMenu, Progress } from './ui.jsx';
import { TrimFields } from './TrimFields.jsx';
import { SegmentsFields } from './SegmentsFields.jsx';
import { EpisodeTrack } from './Timeline.jsx';
import { setPath } from '../pages/Settings.jsx';
import { posterFace } from './kit.jsx';
import { sourceSummary } from '../lib/labels.js';

afterEach(cleanup);

describe('posterFace', () => {
  it('sets action, sci-fi and anime in the condensed face', () => {
    expect(posterFace({ genres: ['Action', 'Drama'] })).toBe('condensed');
    expect(posterFace({ tag: 'ANIME · CANON' })).toBe('condensed');
    expect(posterFace({ genres: ['Science Fiction'] })).toBe('condensed');
  });

  it('keeps everything else in the serif', () => {
    expect(posterFace({ genres: ['Fantasy'], tag: 'MERGE' })).toBe('serif');
    expect(posterFace()).toBe('serif');
  });
});

describe('sourceSummary', () => {
  it('names the sources that are on and the library root', () => {
    const cfg = { library: { plex: { url: 'http://plex.lan:32400' }, sonarr: { url: '' },
      radarr: { url: '' }, folder: { path: '/media' }, movies: { path: '' } } };
    expect(sourceSummary(cfg)).toEqual({ label: 'Plex · Folder', root: '/media' });
  });

  it('falls back to the Plex host, and says when nothing is set up', () => {
    expect(sourceSummary({ library: { plex: { url: 'http://plex.lan:32400' } } }))
      .toEqual({ label: 'Plex', root: 'plex.lan:32400' });
    expect(sourceSummary({ library: {} })).toEqual({ label: 'No source set up', root: '' });
    expect(sourceSummary(null)).toBeNull();
  });
});

describe('LabelBadge', () => {
  it('shows the label with its source in the tooltip', () => {
    render(<LabelBadge label="filler" source="anifiller" />);
    const b = screen.getByText('filler');
    expect(b.className).toContain('badge-filler');
    expect(b.title).toBe('filler (AniFiller)');
  });
});

describe('Progress', () => {
  it('clamps to 0-100%', () => {
    render(<Progress value={1.7} />);
    expect(screen.getByRole('progressbar').getAttribute('aria-valuenow')).toBe('100');
  });
});

describe('EpisodeTrack', () => {
  it('draws kept pieces and trim markers at the right place', () => {
    const ep = { id: 'S01E01', duration: 1000, trims: { intro: { start: 100, end: 200 } } };
    const { container } = render(<EpisodeTrack episode={ep} segments={[{ start: 200, end: 1000 }]} />);
    expect(container.querySelector('.piece').style.left).toBe('20%');
    expect(container.querySelector('.mark-intro').style.width).toBe('10%');
  });
});

describe('TrimFields', () => {
  const ep = { id: 'S01E01', duration: 1440, trims: { intro: { start: 60, end: 150, source: 'auto' } } };

  it('nudges an edge by a second through save()', async () => {
    const save = vi.fn().mockResolvedValue();
    render(<TrimFields episode={ep} save={save} reset={vi.fn()} />);
    fireEvent.click(screen.getByLabelText('OP end later'));
    await waitFor(() => expect(save).toHaveBeenCalledWith('intro', 60, 151));
  });

  it('refuses an invalid typed range instead of calling the server', async () => {
    const save = vi.fn();
    render(<TrimFields episode={ep} save={save} reset={vi.fn()} />);
    const start = screen.getAllByLabelText('start')[0];
    fireEvent.change(start, { target: { value: '5:00' } });
    fireEvent.blur(start);
    await screen.findByRole('alert');
    expect(save).not.toHaveBeenCalled();
  });

  it('clears a range with null/null', async () => {
    const save = vi.fn().mockResolvedValue();
    render(<TrimFields episode={ep} save={save} reset={vi.fn()} />);
    fireEvent.click(screen.getByText('Clear'));
    await waitFor(() => expect(save).toHaveBeenCalledWith('intro', null, null));
  });
});

describe('SegmentsFields', () => {
  const ep = { id: 'S01E01', duration: 1440, segments: [[60, 150]] };

  it('adds and saves segments as seconds', async () => {
    const save = vi.fn().mockResolvedValue();
    render(<SegmentsFields episode={{ ...ep, segments: [] }} save={save} />);
    fireEvent.click(screen.getByText('Add segment'));
    const end = screen.getByLabelText('Segment 1 end');
    fireEvent.change(end, { target: { value: '2:00' } });
    fireEvent.blur(end);
    fireEvent.click(screen.getByText('Save segments'));
    await waitFor(() => expect(save).toHaveBeenCalledWith([[0, 120]]));
  });

  it('rejects an inverted range instead of calling the server', async () => {
    const save = vi.fn();
    render(<SegmentsFields episode={ep} save={save} />);
    const end = screen.getByLabelText('Segment 1 end');
    fireEvent.change(end, { target: { value: '0:30' } });
    fireEvent.blur(end);
    fireEvent.click(screen.getByText('Save segments'));
    await screen.findByRole('alert');
    expect(save).not.toHaveBeenCalled();
  });

  it('clears all segments back to preset mode', async () => {
    const save = vi.fn().mockResolvedValue();
    render(<SegmentsFields episode={ep} save={save} />);
    fireEvent.click(screen.getByText('Clear all'));
    await waitFor(() => expect(save).toHaveBeenCalledWith(null));
  });
});

describe('settings helper', () => {
  it('sets nested values without mutating', () => {
    const a = { x: { y: 1, z: 2 } };
    const b = setPath(a, 'x.y', 9);
    expect(b).toEqual({ x: { y: 9, z: 2 } });
    expect(a.x.y).toBe(1);
  });
});

describe('MoreMenu', () => {
  it('opens, runs an item and closes; Escape closes it too', () => {
    const del = vi.fn();
    render(<MoreMenu items={[['Delete…', del, { danger: true }], false]} />);
    const more = screen.getByRole('button', { name: 'More' });
    expect(screen.queryByRole('menu')).toBeNull();
    fireEvent.click(more);
    expect(more.getAttribute('aria-expanded')).toBe('true');
    fireEvent.click(screen.getByRole('menuitem', { name: 'Delete…' }));
    expect(del).toHaveBeenCalledOnce();
    expect(screen.queryByRole('menu')).toBeNull();
    fireEvent.click(more);
    fireEvent.keyDown(document, { key: 'Escape' });
    expect(screen.queryByRole('menu')).toBeNull();
  });
});
