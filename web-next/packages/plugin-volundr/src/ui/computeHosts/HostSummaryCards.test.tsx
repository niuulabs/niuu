import { describe, expect, it } from 'vitest';
import { render, screen, within } from '@testing-library/react';
import type { ComputeSummary } from '../../domain/computeHosts';
import { HostSummaryCards } from './HostSummaryCards';

const summary: ComputeSummary = {
  hosts: 7,
  byState: { in_service: 3, draining: 1, pending: 1, quarantined: 1, unreachable: 1 },
  slotsTotal: 44,
  slotsFree: 8,
  freeByTagSet: [
    { tags: ['site:hnoss'], free: 8 },
    { tags: ['gpu', 'site:hnoss'], free: 0 },
    { tags: [], free: 1 },
  ],
  warmTargets: [
    { tags: [], match: 'all', min: 4, free: 8, met: true },
    { tags: ['gpu'], match: 'all', min: 1, free: 0, met: false },
  ],
};

describe('HostSummaryCards', () => {
  it('shows hosts by state', () => {
    render(<HostSummaryCards summary={summary} />);
    expect(screen.getByText('7')).toBeInTheDocument();
    expect(
      screen.getByText('3 in service · 1 draining · 1 joining · 2 need attention'),
    ).toBeInTheDocument();
  });

  it('shows free slots and how many are in use', () => {
    render(<HostSummaryCards summary={summary} />);
    expect(screen.getByText('of 44')).toBeInTheDocument();
    expect(screen.getByRole('progressbar', { name: 'Slots in use' })).toHaveAttribute(
      'aria-valuenow',
      '36',
    );
  });

  it('lists free slots per tag set', () => {
    render(<HostSummaryCards summary={summary} />);
    const card = screen.getByText('Free slots by tags').parentElement!;
    expect(within(card).getByText('gpu')).toBeInTheDocument();
    expect(within(card).getAllByText('site:hnoss')).toHaveLength(2);
    expect(within(card).getByText('all hosts')).toBeInTheDocument();
  });

  it('marks a met warm target and a short one with its remedy', () => {
    render(<HostSummaryCards summary={summary} />);
    const card = screen.getByText('Warm targets').parentElement!;
    expect(within(card).getByText('met')).toBeInTheDocument();
    expect(within(card).getByText('0 · short by 1')).toBeInTheDocument();
    expect(within(card).getByText('Register or resume a host tagged gpu.')).toBeInTheDocument();
  });

  it('reads an empty pool without dividing by zero', () => {
    render(
      <HostSummaryCards
        summary={{
          hosts: 0,
          byState: {},
          slotsTotal: 0,
          slotsFree: 0,
          freeByTagSet: [],
          warmTargets: [],
        }}
      />,
    );
    expect(screen.getByText('No hosts yet')).toBeInTheDocument();
    expect(screen.getByText('No hosts in service')).toBeInTheDocument();
    expect(screen.getByText('None set')).toBeInTheDocument();
    expect(screen.getByRole('progressbar')).toHaveAttribute('aria-valuenow', '0');
  });
});
