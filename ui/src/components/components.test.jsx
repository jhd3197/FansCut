// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { LabelBadge, Progress } from './ui.jsx';
import { TrimFields } from './TrimFields.jsx';
import { EpisodeTrack } from './Timeline.jsx';
import { setPath } from '../pages/Settings.jsx';

afterEach(cleanup);

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

describe('settings helper', () => {
  it('sets nested values without mutating', () => {
    const a = { x: { y: 1, z: 2 } };
    const b = setPath(a, 'x.y', 9);
    expect(b).toEqual({ x: { y: 9, z: 2 } });
    expect(a.x.y).toBe(1);
  });
});
