import { describe, it, expect, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { ReplayPanel } from './ReplayPanel';
import { FAKE_GRAPH } from '../../testing/fakeMimirService';

describe('ReplayPanel', () => {
  it('renders the REPLAYING header, date, and page counts', () => {
    render(
      <ReplayPanel graph={FAKE_GRAPH} asOf="2026-03-10" onExitReplay={vi.fn()} onFocus={vi.fn()} />,
    );
    expect(screen.getByText('Replaying')).toBeInTheDocument();
    expect(screen.getByText('10 March')).toBeInTheDocument();
    expect(screen.getByText('5 pages known by then · +2 that day')).toBeInTheDocument();
  });

  it('lists the pages first seen that day and focuses on click', async () => {
    const onFocus = vi.fn();
    render(
      <ReplayPanel graph={FAKE_GRAPH} asOf="2026-03-10" onExitReplay={vi.fn()} onFocus={onFocus} />,
    );
    await userEvent.click(screen.getByText('OpenBao policy'));
    expect(onFocus).toHaveBeenCalledWith('/shared/openbao-policy');
  });

  it('omits the "that day" ticker when nothing was born that day', () => {
    render(
      <ReplayPanel graph={FAKE_GRAPH} asOf="2026-03-02" onExitReplay={vi.fn()} onFocus={vi.fn()} />,
    );
    expect(screen.getByText('2 pages known by then')).toBeInTheDocument();
  });

  it('exits replay on click', async () => {
    const onExitReplay = vi.fn();
    render(
      <ReplayPanel
        graph={FAKE_GRAPH}
        asOf="2026-03-10"
        onExitReplay={onExitReplay}
        onFocus={vi.fn()}
      />,
    );
    await userEvent.click(screen.getByRole('button', { name: 'Exit replay' }));
    expect(onExitReplay).toHaveBeenCalled();
  });
});
