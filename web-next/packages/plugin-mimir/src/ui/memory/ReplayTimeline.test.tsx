import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { ReplayTimeline } from './ReplayTimeline';
import { perDayHistogram } from '../../domain/replayHistogram';
import { FAKE_GRAPH } from '../../testing/fakeMimirService';

const HISTOGRAM = perDayHistogram(FAKE_GRAPH.nodes, '2026-03-01', '2026-03-10');

function setup(overrides: Partial<React.ComponentProps<typeof ReplayTimeline>> = {}) {
  const props: React.ComponentProps<typeof ReplayTimeline> = {
    histogram: HISTOGRAM,
    asOf: '2026-03-10',
    onAsOfChange: vi.fn(),
    isPlaying: false,
    onTogglePlay: vi.fn(),
    speed: 1,
    onSpeedChange: vi.fn(),
    ...overrides,
  };
  render(<ReplayTimeline {...props} />);
  return props;
}

describe('ReplayTimeline', () => {
  it('renders play/pause and toggles it', async () => {
    const props = setup();
    expect(screen.getByRole('button', { name: 'Play' })).toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', { name: 'Play' }));
    expect(props.onTogglePlay).toHaveBeenCalled();
  });

  it('shows Pause when playing', () => {
    setup({ isPlaying: true });
    expect(screen.getByRole('button', { name: 'Pause' })).toBeInTheDocument();
  });

  it('renders all three speed options and selects one', async () => {
    const props = setup();
    expect(screen.getByRole('button', { name: '1×' })).toHaveAttribute('aria-pressed', 'true');
    await userEvent.click(screen.getByRole('button', { name: '8×' }));
    expect(props.onSpeedChange).toHaveBeenCalledWith(8);
  });

  it('renders date ticks for the first and last day', () => {
    setup();
    expect(screen.getByText('2026-03-01')).toBeInTheDocument();
    expect(screen.getByText('2026-03-10')).toBeInTheDocument();
  });

  it('the scrubber reflects the current date', () => {
    setup();
    const scrubber = screen.getByLabelText('Replay date') as HTMLInputElement;
    expect(scrubber.value).toBe(String(HISTOGRAM.findIndex((d) => d.date === '2026-03-10')));
  });

  it('changing the scrubber calls onAsOfChange with the corresponding date', () => {
    const props = setup();
    const scrubber = screen.getByLabelText('Replay date') as HTMLInputElement;
    fireEvent.change(scrubber, { target: { value: '0' } });
    expect(props.onAsOfChange).toHaveBeenCalledWith(HISTOGRAM[0]!.date);
  });
});
