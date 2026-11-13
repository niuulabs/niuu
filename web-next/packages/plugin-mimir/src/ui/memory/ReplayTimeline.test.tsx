import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { ReplayTimeline } from './ReplayTimeline';
import { perDayHistogram } from '../../domain/replayHistogram';
import { FAKE_GRAPH } from '../../testing/fakeMimirService';

const TODAY = '2026-03-10';
const HISTOGRAM = perDayHistogram(FAKE_GRAPH.nodes, '2026-03-01', TODAY);

function setup(overrides: Partial<React.ComponentProps<typeof ReplayTimeline>> = {}) {
  const props: React.ComponentProps<typeof ReplayTimeline> = {
    histogram: HISTOGRAM,
    asOf: '2026-03-05',
    today: TODAY,
    onAsOfChange: vi.fn(),
    isPlaying: false,
    onTogglePlay: vi.fn(),
    speed: 1,
    onSpeedChange: vi.fn(),
    marks: [],
    ...overrides,
  };
  render(<ReplayTimeline {...props} />);
  return props;
}

describe('ReplayTimeline', () => {
  it('at rest reads AS OF today, sits at the end of the track, and offers no playback', () => {
    setup({ asOf: null });
    expect(screen.getByText('As of 10 Mar')).toHaveClass('niuu-hud-caption');
    expect(screen.getByText('Drag back to replay')).toBeInTheDocument();
    const scrubber = screen.getByLabelText('Show memory as of a date') as HTMLInputElement;
    expect(scrubber.value).toBe(String(HISTOGRAM.length - 1));
    expect(scrubber).toHaveAttribute('aria-valuetext', '10 Mar');
    expect(screen.queryByRole('button', { name: 'Play' })).not.toBeInTheDocument();
  });

  it('while replaying shows the day, play/pause, speeds and a way back to now', async () => {
    const props = setup();
    expect(screen.getByText('As of 5 Mar')).toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', { name: 'Play' }));
    expect(props.onTogglePlay).toHaveBeenCalled();
    expect(screen.getByRole('button', { name: '1×' })).toHaveAttribute('aria-pressed', 'true');
    await userEvent.click(screen.getByRole('button', { name: '8×' }));
    expect(props.onSpeedChange).toHaveBeenCalledWith(8);
    await userEvent.click(screen.getByRole('button', { name: 'Back to now' }));
    expect(props.onAsOfChange).toHaveBeenCalledWith(null);
  });

  it('shows Pause when playing', () => {
    setup({ isPlaying: true });
    expect(screen.getByRole('button', { name: 'Pause' })).toBeInTheDocument();
  });

  it('dragging back picks that day; dragging to the end goes back to now', () => {
    const props = setup();
    const scrubber = screen.getByLabelText('Show memory as of a date') as HTMLInputElement;
    expect(scrubber.value).toBe(String(HISTOGRAM.findIndex((d) => d.date === '2026-03-05')));
    fireEvent.change(scrubber, { target: { value: '0' } });
    expect(props.onAsOfChange).toHaveBeenLastCalledWith('2026-03-01');
    fireEvent.change(scrubber, { target: { value: String(HISTOGRAM.length - 1) } });
    expect(props.onAsOfChange).toHaveBeenLastCalledWith(null);
  });

  it('draws one bar per day, the shown day highlighted', () => {
    setup();
    const bars = screen.getByRole('img', { name: 'Pages first seen per day' }).children;
    expect(bars).toHaveLength(HISTOGRAM.length);
    const current = [...bars].filter((b) => b.classList.contains('niuu-hud-scrubber-bar--current'));
    expect(current).toHaveLength(1);
    expect(current[0]).toHaveAttribute('title', expect.stringMatching(/^2026-03-05:/));
  });

  it('lists the focused item’s dated marks, recent ones amber, each a jump to that day', async () => {
    const props = setup({
      marks: [
        { date: '2026-03-03', label: 'planned in', recent: false },
        { date: '2026-03-09', label: 'moved', recent: true },
      ],
    });
    const planned = screen.getByRole('button', { name: '3 Mar · planned in' });
    expect(planned).not.toHaveClass('niuu-hud-mark--recent');
    expect(screen.getByRole('button', { name: '9 Mar · moved' })).toHaveClass(
      'niuu-hud-mark--recent',
    );
    await userEvent.click(planned);
    expect(props.onAsOfChange).toHaveBeenCalledWith('2026-03-03');
  });

  it('renders an empty track without failing when memory has no dated pages', () => {
    setup({ histogram: [], asOf: null });
    expect(screen.getByLabelText('Show memory as of a date')).toHaveAttribute('max', '0');
  });
});
